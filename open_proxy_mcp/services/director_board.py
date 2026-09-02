"""director_board — 이사회/개별 이사 프로필 (보수·소진율·재직/사퇴·출석률) Action Tool.

`corp_gov_report`가 "회사 지배구조 15지표 준수 여부"라면, 이 tool은 "개별 이사 단위" 정보 —
누가 얼마 받고(보수), 승인한도를 얼마나 썼고(소진율), 인원이 바뀌었고(재직/사퇴), 이사회에 얼마나
나왔는지(출석률) — 를 잡는다. 스튜어드십 engagement·보수 안건 판단·거버넌스 스크리닝에 쓴다.

목표 질문 3개 (사용자 260708):
  ① 이사 인당 보수가 적절한가        → psn1_avrg_pymntamt (DART API 직접 제공)
  ② 보수한도 소진율은 얼마인가        → Σ실지급 ÷ 주총승인 한도
  ③ 사퇴/인원변동으로 인당보수·소진율이 바뀌었나 → 연도간 임원 diff × 보수 변동 교차

데이터 소스 (전부 신규 스콥 — corp_gov_report·director_performance·shareholder_meeting과 무중복):
  - 정형 API(DS002 정기보고서): exctvSttus(임원현황) · drctrAdtAllMendngSttus*(보수한도·실지급) ·
    hmvAuditIndvdlBySttus(개인별 5억+).
  - 원문 파서(지배구조보고서, scope=attendance): 개별 이사 출석률 매트릭스 · 표4-2-1 선임/변동 ·
    표5-2-1 겸직. (정형+출석만; 금융지주 등 별도양식은 not_found 로 반환 — PDF/OCR 은 OPM 범위 밖)

scope:
  - compensation : 인당보수·보수한도·소진율 (정형 API)          [🟢 바로]
  - roster       : 임원현황 + 재직/사퇴 감지 (연도 diff)         [🟢🟡]
  - attendance   : 개별 이사 출석률·선임변동·겸직 (원문 파서)     [🟡]  (v1 stub→차기)
  - summary      : 위 종합 + 판단(인당보수·소진율·사퇴영향)

주의 (data QA 260708 검증):
  1. 버킷 불일치 — 한도(gmtsckConfmAmount)는 "등기이사" 단일버킷(사외 포함) vs 실지급
     (MendngPymntamtTyCl)은 등기(사외·감사위 제외)/사외/감사위원 세분. 소진율 계산 시 실지급 버킷
     합을 한도와 대응(단순 join 금지).
  2. 한도 공백 — 새 주총 결의 없는 해엔 gmtsck_confm_amount="-" → 최근 유효연도 lookback.
  3. 감사 보수한도 분리 — 감사위원은 이사 한도 안, 별도 감사(비위원회)는 별도 한도.
  4. 5억 미만 비공개 — 개인별은 상위 일부만(범주 평균은 전원).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.services.company import resolve_company_query
from open_proxy_mcp.services.executive_pay import parse_executive_pay, reconcile_with_api
from open_proxy_mcp.services.contracts import (
    AnalysisStatus,
    ToolEnvelope,
    build_usage,
)

_SUPPORTED_SCOPES = {"compensation", "roster", "individual", "unregistered", "pay_gap",
                     "pay_agenda", "attendance", "pay_criteria", "summary"}

# 소진율 임계 (참고용 flag — 판단이 아니라 신호)
_UTILIZATION_HIGH = 90.0   # 한도의 90%+ 소진 = 여유 적음
_ATTENDANCE_LOW = 75.0     # 출석률 75% 미만 = 저조 flag

_REPRT_ANNUAL = "11011"    # 사업보고서
# 사업보고서가 아직 안 나온 시기(주총 성수기 2~3월)에 현재 명단을 채울 대체 보고서 — 신선한 순.
# (reprt_code, 이름, 기준일 월). 자본시장법상 분기 종료 후 45일 제출이라 3분기는 11/14 에 나온다.
_QUARTERLY_FALLBACK: tuple[tuple[str, str, int], ...] = (
    ("11014", "3분기보고서", 9),
    ("11012", "반기보고서", 6),
    ("11013", "1분기보고서", 3),
)


def _to_int(value: Any) -> int | None:
    """DART 정형 금액/인원 문자열('21,800,000,000'·'-'·'')을 int로. 공백/미기재는 None."""
    if value is None:
        return None
    s = str(value).replace(",", "").strip()
    if not s or s in {"-", "해당없음", "해당사항없음"}:
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


async def _fetch_rows(coro) -> list[dict[str, Any]]:
    """DART 정형 API 응답에서 list만 안전 추출.

    **client._request는 status!=000이면 항상 예외를 던진다**(재시도 후에도) — 이 아래
    `resp.get("status")` 체크는 사실 정상 응답에서만 도달하는 죽은 방어였다. status="013"
    ("조회된 데이타가 없습니다")은 진짜 오류가 아니라 "이 회사는 해당 데이터가 없음"이라는
    흔한 정상 케이스(소액공모/신규상장사가 미등기임원·5억+개인·사외이사변동 등이 없을 때) —
    300개사 census(260709)에서 5개사가 이걸 못 잡아 tool 전체가 크래시(status=error)했다.
    013만 빈 리스트로 흡수, 그 외 예외(진짜 오류)는 그대로 올려보냄."""
    try:
        resp = await coro
    except DartClientError as e:
        if e.status == "013":
            return []
        raise
    if (resp or {}).get("status") != "000":
        return []
    return resp.get("list") or []


async def _timed(coro, name: str, timings: dict[str, float]) -> Any:
    """scope 코루틴 소요시간(ms)을 timings에 기록. 병렬 실행이라 wall-clock은 겹치지만 개별
    scope의 소요는 그대로 측정돼 어느 scope가 느린지·성능 회귀를 나중에 잴 수 있다(사용자 요청).
    time.perf_counter는 벽시계가 아닌 단조 증가 카운터라 측정에 안전."""
    t0 = time.perf_counter()
    try:
        return await coro
    finally:
        timings[name] = round((time.perf_counter() - t0) * 1000, 1)


# 각주 마커: (*1)·( * 1 )·주1)·(주 1) 등. DART 정형 API가 비고/breakdown에 각주 마커만 반환하고
# 각주 본문은 사업보고서 원문에만 있는 경우가 있다(120사 census: SK하이닉스 승인한도 '(주1)'·
# NAVER 개인별 '주1)' 등 9/120사). 이런 필드는 값이 마커뿐이라 그대로 렌더하면 무의미 —
# raw_text로도 복구 불가(본문이 API 응답에 없음)라 '원문 각주 미해결' 플래그로 표식만 남긴다.
_MARKER_RE = re.compile(r"\(\s*\*\s*\d+\s*\)|\(?\s*주\s*\d+\s*\)|\*\d+")


def _is_bare_marker(value: Any) -> bool:
    """필드가 사실상 각주 마커뿐인지('[등기임원] (주1)'·'주1)' → True). 마커+실내용이면 False."""
    if not value:
        return False
    body = re.sub(r"^\[[^\]]*\]\s*", "", str(value).strip())  # '[label] ' 접두 제거 후 판정
    if not _MARKER_RE.search(body):
        return False
    return len(_MARKER_RE.sub("", body).strip(" -·.,()[]")) <= 2


# 각주가 붙는 표의 제목(섹션 앵커). 마커는 section-local이라(같은 '주1)'이 임원보수·재무각주에
# 따로 존재·의미 다름 — 크래프톤 실측) 전체 원문이 아니라 앵커 뒤 window 안에서만 각주를 찾는다.
_SECTION_ANCHORS = {
    "compensation": r"주주총회\s*승인금액",
    "individual": r"개인별\s*보수지급\s*금액|이사ㆍ감사의?\s*개인별|5억원\s*이상",
    "unregistered": r"미등기\s*임원",
}
# 각주 '정의'(사용처 아님) 신호어 — 이게 있어야 설명문으로 인정(표 행 조각 오탐 억제).
_FN_SIGNAL = re.compile(r"(습니다|합니다|하였|함\b|임\b|됨\b|승인|지급|부여|산정|포함|제외|사임|선임|한도|받았)")


def _norm_marker(token: str) -> str:
    """'(주1)'·'주1)'→'주1', '(*1)'·'*1'→'*1'."""
    return re.sub(r"[^\d주*]", "", token or "")


# 각주 문장 종결어미 — 진짜 각주 '정의'는 강한 동사 종결로 끝난다. bare 함/임/됨은 명사(사임·위원 등)를
# 오탐해 표 조각/중간잘림을 통과시켜서(보로노이 '…사임' 사례) 제외 — 확실한 서술 종결만(정밀도 우선).
_FN_END = re.compile(r"(받았습니다|하였습니다|되었습니다|있습니다|없습니다|됩니다|입니다|습니다|합니다|"
                     r"하였음|되었음|받았음|하였다|되었다|한다)[.\)]?")

# 각주 유형 게이트(footnote_qa 300사 검증 260709): 마커가 붙은 slot의 주제와 각주 내용이 맞아야
# 채택. 안 맞으면(승인한도 셀인데 소송충당부채·특수관계자거래·스톡옵션 각주 등) 오답이므로 raw 강등.
# BAD=그 scope에서 절대 그 마커 각주일 수 없는 주제 / OK=최소 하나는 있어야 하는 주제.
_FN_BAD = {
    "compensation": re.compile(r"주식매수선택권|스톡옵션|공정가치|액면분할|무상증자|유상증자|충당부채|"
                               r"소송|특수\s*관계|거래\s*금액|거래내역|채무보증|담보|배당금"),
    "individual": re.compile(r"충당부채|소송|특수\s*관계|거래\s*금액|채무보증"),
    "unregistered": re.compile(r"충당부채|소송|특수\s*관계|거래\s*금액"),
}
_FN_OK = {
    "compensation": re.compile(r"보수|한도|승인|주주총회|지급|성과|퇴직|퇴임|사임"),
    "individual": re.compile(r"보수|주식|RSU|RSA|스톡옵션|주식매수선택권|성과|지급|퇴임|사임|"
                             r"근로소득|상여|가득|제한조건부|PSU|SAR"),
    "unregistered": re.compile(r"보수|급여|지급|성과|산정|퇴직|근로소득"),
}


def _fn_topic_ok(scope: str, body: str) -> bool:
    """각주 본문이 그 scope 마커의 주제에 부합하는가(BAD 없고 OK 하나 이상)."""
    bad = _FN_BAD.get(scope)
    ok = _FN_OK.get(scope)
    if bad and bad.search(body):
        return False
    if ok and not ok.search(body):
        return False
    return True


def _clean_fn_body(body: str) -> tuple[str, bool]:
    """각주 본문 정리: 앞쪽 마커/※/콜론 제거 후 첫 문장 종결에서 자름.
    반환 (정리본문, 확신) — 강한 서술 종결이 있으면 확신 True(진짜 각주), 없으면 False(표 조각/중간잘림)."""
    body = re.sub(r"^\s*[※\-]*\s*\(?\s*(?:주|[*])\s?\d+\s*\)?\s*[:：]?\s*", "", body).strip()
    m = _FN_END.search(body)
    if m:
        return body[:m.end()].strip(), True
    return body.strip(), False


def _extract_footnote(text: str, scope: str, marker: str, window: int = 6000,
                      subject: str | None = None) -> tuple[str | None, str | None]:
    """원문에서 (scope 섹션의) 특정 마커 각주 본문을 추출.
    반환 (resolved_body, raw_snippet): 문장으로 끝나는 확신 각주면 body(raw None), 애매하면 body None
    + 앵커 구간 raw 발췌(코붕이 raw_text 폴백 — 틀린 각주를 지어내느니 원문을 그대로 보여줌).
    크래프톤처럼 마커가 표 컬럼이라 옆 행을 긁는 오탐은 '문장 종결 필수'로 걸러 raw 폴백으로 보낸다."""
    anchor = _SECTION_ANCHORS.get(scope)
    if not anchor:
        return None, None
    m0 = re.search(anchor, text)
    if not m0:
        return None, None
    seg = re.sub(r"\s+", " ", text[m0.start(): m0.start() + window])
    want = _norm_marker(marker)
    defs: dict[str, str] = {}
    pat = re.compile(r"(\(?\s*(?:주|[*])\s?\d+\s*\))\s*([가-힣0-9A-Za-z※][^\n]{9,260}?)"
                     r"(?=\(?\s*(?:주|[*])\s?\d+\s*\)|\(단위|$)")
    for m in pat.finditer(seg):
        mk = _norm_marker(m.group(1))
        cleaned, confident = _clean_fn_body(m.group(2))
        # 확신 조건: 강한 문장종결 + 한글 6자+ + 신호어 + scope 주제 게이트 + **표 조각 아님**.
        # 주제 게이트(footnote_qa 300사): 승인한도 셀 마커인데 소송충당부채·특수관계자거래·스톡옵션
        # 각주를 긁는 오답(한국가스공사·보로노이·HPSP 등 22%)을 거부 → raw 폴백 강등.
        # 표 조각 필터: 'N N N'(공백구분 숫자 3연속)은 표 셀 행이지 각주 문장이 아님(BGF리테일
        # '2 126 63 -' 오탐). '5명과 사외이사 2명'류(명 접미)는 이 패턴에 안 걸림.
        if (not confident or len(re.findall(r"[가-힣]", cleaned)) < 6
                or not _FN_SIGNAL.search(cleaned) or not _fn_topic_ok(scope, cleaned)
                or re.search(r"\d+\s+\d+\s+\d+", cleaned)):
            continue
        if mk not in defs or len(cleaned) > len(defs[mk]):
            defs[mk] = cleaned[:250]
    if want in defs:
        body = defs[want]
        # individual은 인물별 마커 — 여러 명이 같은 (주N) 공유하는 표에서 엉뚱한 사람 각주를 집는
        # 오탐(SK 이성형→조대식/장동현) 방지: 본문이 subject 이름을 안 담고 **다른** 임원을
        # 명시적으로 지칭하면(이름+직위) raw 강등(footnote_qa #2).
        if scope == "individual" and subject and subject not in body:
            other = re.search(r"([가-힣]{2,4})\s*(대표이사|사내이사|사외이사|이사|사장|부사장|회장)", body)
            if other and other.group(1) != subject:
                return None, seg[:400].strip()
        return body, None
    return None, seg[:400].strip()  # 확신 각주 못 찾음 → raw 발췌 폴백(틀린 각주보다 안전)


async def _resolve_footnotes(client, flags: list[dict[str, Any]]) -> None:
    """footnote_marker_unresolved 플래그를 원문에서 해소(in-place). 마커 뜬 공시(rcept_no)만
    get_document_cached로 1회씩 fetch(캐시) — 앱레벨 limiter 보호. 확신 추출→resolved_text,
    애매→raw_text 폴백. 원문 파싱 스콥에서 코붕이의 raw_text fallback 설계를 실제 구현한 지점."""
    targets = [f for f in flags if f.get("kind") == "footnote_marker_unresolved" and f.get("rcept_no")]
    by_rcept: dict[str, list[dict]] = {}
    for f in targets:
        by_rcept.setdefault(f["rcept_no"], []).append(f)

    # 여러 공시(rcept_no) 원문을 병렬 fetch(perf 260709: 순차 루프가 tail 지연 주범 — GS리테일
    # distinct rcept 다수를 8MB씩 순차로 받아 +15초. 다운로드는 앱 limiter가 보호하므로 병렬 안전).
    async def _fetch(rc: str):
        try:
            return rc, await client.get_document_cached(rc)
        except Exception:  # noqa: BLE001 — 원문 폴백 실패는 치명적 아님(마커만 남음)
            return rc, None

    docs = dict(await asyncio.gather(*[_fetch(rc) for rc in by_rcept]))
    for rcept_no, fs in by_rcept.items():
        text = (docs.get(rcept_no) or {}).get("text") or ""
        if not text:
            continue
        for f in fs:
            body, raw = _extract_footnote(text, f.get("scope"), f.get("raw_text") or "",
                                          subject=f.get("subject"))
            if body:
                f["resolved_text"] = body
                f["resolved_from"] = "원문 각주(사업보고서)"
            elif raw:
                f["raw_text_excerpt"] = raw
                f["resolved_from"] = "원문 발췌(각주 자동추출 실패 — 원문 확인 필요)"


# ── compensation: 인당보수·한도·소진율 ──────────────────────────────────────

def _compensation_from_rows(
    limit_rows: list[dict[str, Any]], actual_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """주총 승인한도(limit) vs 유형별 실지급(actual)으로 소진율·인당보수 계산.

    버킷 불일치(주의 1) 대응: 실지급은 유형별 여러 행이라 '이사 한도' 대상 유형만 합산.
    실지급 유형 se 예: '등기이사(사외이사, 감사위원회 위원 제외)'·'사외이사'·'감사위원회 위원'·'감사'.
    이사 보수한도 소진율 = (감사 제외 이사류 실지급 합) ÷ 이사 승인한도.
    """
    # 유형별 실지급 정리. rm(비고)은 원문 그대로 노출(260708) — 회사마다 날짜·표기 포맷이
    # 제각각이라(선임'25.03.20 / 2025년 03월 25일 / 2025.3.26 부) 정규식 구조화는 깨지기 쉽다.
    # 300개사 실측: 17.3%(52사)에 내용 있고 평균 24.9자·최대 187자 — 짧아서 raw passthrough로 충분.
    # 퇴직금·중도사임 등 1회성 사유, 심지어 이사 성명+정확한 선임/사임 날짜까지 담긴 경우도 있어
    # exceeded_limit 등 이상치 해석에 바로 쓸모 있다.
    pay_by_type: list[dict[str, Any]] = []
    for r in actual_rows:
        se = (r.get("se") or "").strip()
        rm = (r.get("rm") or "").strip()
        pay_by_type.append({
            "type": se,
            "headcount": _to_int(r.get("nmpr")),
            "total_paid_krw": _to_int(r.get("pymnt_totamt")),
            "per_capita_krw": _to_int(r.get("psn1_avrg_pymntamt")),
            "note": rm if rm and rm != "-" else None,
        })

    # 이사 승인한도. 감사 전용 행 판정은 **'이사' 문자열 부재**로(QA 260708 발견 — "위원" 유무로
    # 가르면 "감사위원회 위원 또는 감사"처럼 이사 언급 없이 위원·감사가 결합된 통합 표기를 이사 한도로
    # 오분류해 진짜 이사 한도를 덮어씀. '이사'가 전혀 없고 '감사'만 있으면 무조건 감사 전용).
    # 이사류 행은 **합산**(덮어쓰기 금지 — 상임/비상임·등기/사외로 분리 공시하는 회사가 있어(한국전력·
    # 기업은행·강원랜드 실측 260708) 마지막 행으로 덮어쓰면 한도가 5~8배 축소돼 소진율이 허위로 폭등).
    # 단 '계'/'합계' 총계행이 있으면 그 행만 채택(중복 합산 방지, employee 로직과 동일 원칙).
    director_rows: list[tuple[str, int | None, int | None]] = []
    audit_limit_krw = None
    limit_notes: list[str] = []
    for r in limit_rows:
        se = (r.get("se") or "").strip()
        amt = _to_int(r.get("gmtsck_confm_amount"))
        n = _to_int(r.get("nmpr"))
        rm = (r.get("rm") or "").strip()
        if rm and rm != "-":
            limit_notes.append(f"[{se}] {rm}")
        if "감사" in se and "이사" not in se:
            audit_limit_krw = amt if amt is not None else audit_limit_krw
        else:
            director_rows.append((se, amt, n))

    total_row = next(
        ((se, amt, n) for se, amt, n in director_rows
         if amt is not None and any(m in se for m in ("계", "합계", "총계"))),
        None,
    )
    if total_row:
        director_limit_krw = total_row[1]
        director_limit_headcount = total_row[2]
    else:
        valid = [(amt, n) for _, amt, n in director_rows if amt is not None]
        director_limit_krw = sum(a for a, _ in valid) if valid else None
        director_limit_headcount = sum(n or 0 for _, n in valid) if valid else None

    # 한도 원문이 각주 마커('(*1)' 등)라 실제 금액이 숫자로 안 잡혀 spurious 소액(수백만원)이
    # 분모가 되면 소진율이 수만%로 폭발한다(QA 260709: 디앤디파마텍 2024 한도 0.0억→35833.3%
    # 🚨한도초과 오표기). 상장사 이사 보수한도가 1억 미만인 경우는 사실상 없으므로 파싱실패 신호로
    # 보고 '한도 미상'으로 처리 → 소진율 산출에서 제외(공백해 lookback이 직전 유효연도로 채움).
    if director_limit_krw is not None and director_limit_krw < 100_000_000:
        limit_notes.append("[소진율 산출] 승인한도 원문이 각주 마커 등으로 금액 미파싱 — 한도 미상 처리(소진율 제외)")
        director_limit_krw = None
        director_limit_headcount = None

    # 이사류 실지급 합. **주의**: 실지급(actual) 테이블의 '감사위원회 위원'(순수 표기)은 등기이사이므로
    # 이사 한도 안(260708 최초 검증 — 현대차 헤드카운트 12=등기5+사외2+감사위원5 정합 확인됨). 한도 테이블의
    # "감사위원회 위원 또는 감사"(결합 표기, 이번에 발견한 별개 버그)와 판정 기준이 달라야 함 — 실지급
    # 쪽은 원래 조건('위원' 없어야 감사 전용) 유지, 한도 쪽만 위에서 '이사' 부재로 판정.
    director_paid_total = 0
    has_director_paid = False
    for p in pay_by_type:
        t = p["type"]
        if "감사" in t and "위원" not in t and "이사" not in t:
            continue  # 순수 감사(위원 아님) → 별도 한도
        if p["total_paid_krw"] is not None:
            director_paid_total += p["total_paid_krw"]
            has_director_paid = True

    utilization_pct = None
    if director_limit_krw and has_director_paid:
        utilization_pct = round(director_paid_total / director_limit_krw * 100, 1)

    # 각주 원문 해소용 접수번호(rcept_no) — 마커 뜬 해의 사업보고서 원문을 찾아갈 때 씀(260709).
    rcept_no = next((r.get("rcept_no") for r in (limit_rows + actual_rows) if r.get("rcept_no")), None)

    return {
        "director_pay_limit_krw": director_limit_krw,
        "director_pay_limit_headcount": director_limit_headcount,
        "audit_pay_limit_krw": audit_limit_krw,
        "director_paid_total_krw": director_paid_total if has_director_paid else None,
        "utilization_pct": utilization_pct,
        "by_type": pay_by_type,
        "limit_notes": limit_notes,  # 승인한도 공시 rm(비고) — 260709 추가
        "rcept_no": rcept_no,
    }


async def _compensation_scope(
    client, corp_code: str, year: int, *, lookback_years: int, warnings: list[str]
) -> dict[str, Any]:
    """연도별 인당보수·한도·소진율. 한도 공백 해는 최근 유효값 lookback(주의 2).

    260709 병렬화: 한도(limit)·실지급(actual)은 서로 무관한 API라 연도당 병렬(gather), 연도끼리도
    서로 독립이라 전부 한 번에 병렬 fetch — **fetch만** 병렬화하고, last_valid_limit 캐리포워드
    처리는 네트워크 I/O 없는 순수 계산이라 fetch 후 최신→과거 순서로 순차 처리(정확성 그대로 유지).
    """
    years = [year - i for i in range(lookback_years)]

    async def _fetch_year(y: int) -> tuple[int, list, list]:
        limit_rows, actual_rows = await asyncio.gather(
            _fetch_rows(client.get_director_pay_limit(corp_code, str(y), _REPRT_ANNUAL)),
            _fetch_rows(client.get_director_pay_actual(corp_code, str(y), _REPRT_ANNUAL)),
        )
        return y, limit_rows, actual_rows

    fetched = await asyncio.gather(*[_fetch_year(y) for y in years])
    fetched.sort(key=lambda t: -t[0])  # 최신→과거 순서 보장(캐리포워드 방향성 유지)

    per_year: list[dict[str, Any]] = []
    last_valid_limit: dict[str, Any] | None = None

    for y, limit_rows, actual_rows in fetched:
        comp = _compensation_from_rows(limit_rows, actual_rows)
        # 한도 공백 → 직전 유효 한도로 소진율 재계산(주의 2)
        if comp["director_pay_limit_krw"] is None and last_valid_limit:
            comp["director_pay_limit_krw"] = last_valid_limit["director_pay_limit_krw"]
            comp["director_pay_limit_headcount"] = last_valid_limit["director_pay_limit_headcount"]
            comp["limit_source"] = f"{last_valid_limit['year']}년 승인분 유지(당해 미갱신)"
            if comp["director_paid_total_krw"] and comp["director_pay_limit_krw"]:
                comp["utilization_pct"] = round(
                    comp["director_paid_total_krw"] / comp["director_pay_limit_krw"] * 100, 1
                )
        elif comp["director_pay_limit_krw"] is not None:
            last_valid_limit = {
                "year": y,
                "director_pay_limit_krw": comp["director_pay_limit_krw"],
                "director_pay_limit_headcount": comp["director_pay_limit_headcount"],
            }

        comp["year"] = y
        util = comp.get("utilization_pct")
        # >100%는 주총 승인한도를 실제로 초과 지급한 것 — 300개사 전수조사(260708)에서 실제 사례
        # 확인(HD현대중공업 등 조선업 슈퍼사이클 성과급이 사전승인 한도 40억을 넘어 125.7% 지급).
        # 코드 버그가 아니라 스튜어드십 신호이므로 "high"와 구분해 명시적으로 표시.
        if util is not None and util > 100:
            comp["utilization_flag"] = "exceeded_limit"
        elif util is not None and util >= _UTILIZATION_HIGH:
            comp["utilization_flag"] = "high"
        per_year.append(comp)

    if not any(y.get("director_paid_total_krw") for y in per_year):
        warnings.append("이사 보수 정형 데이터가 조회 구간에 없음(사업보고서 미제출/비대상 가능).")

    return {"per_year": per_year}


# ── roster: 임원현황 + 재직/사퇴 감지 ───────────────────────────────────────

def _clean_name(value: Any) -> str:
    """DART 이름 필드의 개행·중복공백 정규화(원문이 '호세\\n무뇨스'처럼 개행 포함)."""
    return " ".join(str(value or "").split())


def _clean_text_or_none(value: Any) -> str | None:
    """긴 텍스트 필드(주요경력·최대주주관계 등) 정규화 — 원문 개행을 ' / '로 치환해 마크다운
    표 셀·목록이 안 깨지게(QA 260709 발견 — main_career/largest_shareholder_relation에 raw
    개행이 그대로 남아 표가 깨짐). '-'(DART 공백 마커)는 None으로."""
    text = " / ".join(line.strip() for line in str(value or "").splitlines() if line.strip())
    return text if text and text != "-" else None


def _roster_key(row: dict[str, Any]) -> str:
    """동명이인 최소화를 위해 이름+생년월 조합을 식별키로."""
    return f"{_clean_name(row.get('nm'))}|{(row.get('birth_ym') or '').strip()}"


# 등기 이사회 멤버 구분값(rgist_exctv_at은 '등기여부'가 아니라 이사 '구분'을 담음 — QA 260708)
_BOARD_TYPES = {"사내이사", "사외이사", "기타비상무이사", "감사"}



def _diff_roster_rows(
    prev_rows: list[dict[str, Any]], curr_rows: list[dict[str, Any]],
    *, joined_label: str, left_label: str,
) -> list[dict[str, Any]]:
    """두 임원현황 스냅샷의 명단 변화. 동일인 판정은 **2-pass**(QA 260709 대응).

      Pass 1: 이름 정확히 일치 → 확정 동일인(잔류).
      Pass 2: Pass 1 에서 못 잡힌 나머지끼리만 birth_ym 매칭 — 단 그 birth_ym 이
              **남은 후보군 안에서 유일**할 때만 동일인으로 인정.

    나누는 이유: 원래 OR 매칭(이름 OR 생년월, 전체 집합)은 birth_ym 이 연·월뿐이라 정밀도가
    낮아, 이탈자와 **이름이 전혀 다른 잔류자**의 birth_ym 이 우연히 같으면(현대차 이탈자
    윤치원 vs 잔류자 심달훈, 둘 다 "1959년 06월") 이탈이 통째로 누락됐다. 잔류자는 Pass 1 에서
    이름으로 확정되므로 Pass 2 후보군에서 빠져 이 오탐이 사라진다. 로마자표기 변동
    (이름 다름·생년월 같음, Jose Munoz↔호세무뇨스)은 남은 후보가 1쌍이면 Pass 2 로 잡힌다.
    """
    changes: list[dict[str, Any]] = []
    prev_names = {_clean_name(r.get("nm")) for r in prev_rows}
    curr_names = {_clean_name(r.get("nm")) for r in curr_rows}
    prev_unmatched = [r for r in prev_rows if _clean_name(r.get("nm")) not in curr_names]
    curr_unmatched = [r for r in curr_rows if _clean_name(r.get("nm")) not in prev_names]

    def _births(rows):
        from collections import Counter
        return Counter((r.get("birth_ym") or "").strip() for r in rows
                       if (r.get("birth_ym") or "").strip())

    prev_b, curr_b = _births(prev_unmatched), _births(curr_unmatched)
    paired = {
        (r.get("birth_ym") or "").strip() for r in prev_unmatched
        if (b := (r.get("birth_ym") or "").strip()) and prev_b[b] == 1 and curr_b.get(b) == 1
    }
    for r in curr_unmatched:
        if (r.get("birth_ym") or "").strip() not in paired:
            changes.append({"name": _clean_name(r.get("nm")), "change": joined_label,
                            "position": _clean_text_or_none(r.get("ofcps")) or "",
                            "director_type": (r.get("rgist_exctv_at") or "").strip()})
    for r in prev_unmatched:
        if (r.get("birth_ym") or "").strip() not in paired:
            changes.append({"name": _clean_name(r.get("nm")), "change": left_label,
                            "position": _clean_text_or_none(r.get("ofcps")) or "",
                            "director_type": (r.get("rgist_exctv_at") or "").strip()})
    return changes


async def _roster_scope(
    client, corp_code: str, year: int, *, lookback_years: int, warnings: list[str]
) -> dict[str, Any]:
    """임원현황 스냅샷 + 연도간 diff로 신규선임/이탈 감지(사유는 스냅샷이라 미확정 — 주의)."""
    years = [year - i for i in range(lookback_years)]

    # exctvSttus(임원현황, 연도별)와 outcmpnyDrctrNdChangeSttus(사외이사 변동현황, 연도별)는
    # 서로 완전 독립 — 하나의 gather로 한 번에 병렬 fetch(260709, 이전엔 두 루프로 나뉘어 순차 대기).
    exctv_fetched, oc_fetched = await asyncio.gather(
        asyncio.gather(*[_fetch_rows(client.get_executive_status(corp_code, str(y), _REPRT_ANNUAL))
                         for y in years]),
        asyncio.gather(*[_fetch_rows(client.get_outside_director_changes(corp_code, str(y), _REPRT_ANNUAL))
                         for y in years]),
    )
    snapshots: dict[int, dict[str, dict[str, Any]]] = {
        y: {_roster_key(r): r for r in rows} for y, rows in zip(years, exctv_fetched)
    }

    latest = year
    # 현재 명단은 **가장 최신 정기보고서**로 채운다(260730 사용자 지적).
    # 사업보고서만 보면 2~3월엔 FY(N-2) = 15개월 묵은 명단을 「현재 이사회」로 내놓는다 —
    # 그 사이 주총 한 번이 지나 이사회 구성이 바뀌었을 수 있다.
    # 분기·반기보고서에도 임원현황이 실린다(실측 10사×4종 100%, 등기구분·재직기간도 100%).
    # 다만 분·반기는 기재 생략이 허용되는 항목이라 소형사에서 빌 수 있어 사다리로 내려간다.
    # 과거 연도는 diff 기준선이라 사업보고서로 고정한다(스냅샷 기준일을 섞으면 비교가 어긋난다).
    roster_as_of = f"{latest}년 사업보고서"
    interim_rows: list[dict[str, Any]] = []
    interim_label = ""
    if not snapshots.get(latest):
        for code, label, _ref_month in _QUARTERLY_FALLBACK:
            rows = await _fetch_rows(client.get_executive_status(corp_code, str(latest), code))
            # 행이 온다고 쓸 수 있는 게 아니다 — 분·반기는 임원현황 기재를 생략할 수 있어
            # 일부만 실린 응답이 온다(실측 29사 중 1사: 사업보고서 157행·등기 7명 →
            # 3분기 1행·등기 0명). 등기 이사회 구성원이 없으면 명단으로 쓰지 않는다.
            if rows and any((r.get("rgist_exctv_at") or "").strip() in _BOARD_TYPES for r in rows):
                snapshots[latest] = {_roster_key(r): r for r in rows}
                interim_rows, interim_label = rows, f"{latest}년 {label}"
                roster_as_of = interim_label
                warnings.append(
                    f"{latest}년 사업보고서가 아직 없어 {label}(기준일 {_ref_month}월 말) "
                    "임원현황으로 현재 명단을 구성했습니다 — 이후 변동은 반영되지 않습니다.")
                break
    current = snapshots.get(latest, {})
    # 모든 텍스트 필드에 _clean_text_or_none 방어 적용(260709 — duty·tenure에서도 raw 개행이
    # 표를 깨뜨리는 걸 추가 발견, main_career/largest_shareholder_relation만 고쳤던 걸 전체로 확대).
    roster = [{
        "name": _clean_name(r.get("nm")),
        "gender": (r.get("sexdstn") or "").strip(),
        "birth_ym": (r.get("birth_ym") or "").strip(),             # 출생년월(diff 매칭 키였는데 렌더 요청으로 노출)
        "position": _clean_text_or_none(r.get("ofcps")) or "",
        "director_type": (r.get("rgist_exctv_at") or "").strip(),  # 사내이사/사외이사/기타비상무/감사
        "full_time": (r.get("fte_at") or "").strip(),              # 상근/비상근
        "duty": _clean_text_or_none(r.get("chrg_job")) or "",
        "tenure": _clean_text_or_none(r.get("hffc_pd")) or "",
        "tenure_end": _clean_text_or_none(r.get("tenure_end_on")) or "",
        "main_career": _clean_text_or_none(r.get("main_career")),
        "largest_shareholder_relation": _clean_text_or_none(r.get("mxmm_shrholdr_relate")),
    } for r in current.values()]

    # 연도간 diff (최신 vs 직전연도). 동일인 판정 — **2-pass**(QA 260709 발견 대응):
    #   Pass 1: 이름 정확히 일치 → 확정 동일인(잔류).
    #   Pass 2: Pass 1에서 못 잡힌(이름 불일치) 나머지끼리만 birth_ym 매칭 시도 —
    #           단 그 birth_ym이 "남은 후보군 안에서 유일"할 때만 동일인으로 인정.
    # 이렇게 나누는 이유: 원래 OR 매칭(이름 OR 생년월, 전체 집합 대상)은 birth_ym이 연·월만 있어
    # 정밀도가 낮은 탓에, 이탈자와 **이름이 전혀 다른 잔류자**의 birth_ym이 우연히 같으면
    # (현대차 이탈자 윤치원 vs 잔류자 심달훈, 둘 다 "1959년 06월") 이탈자가 "잔류"로 오판돼
    # 이탈 자체가 통째로 누락됐다(QA 실측). 잔류자는 Pass 1에서 이미 이름으로 확정되므로
    # Pass 2 후보군에서 빠져, 이런 오탐이 사라진다. 로마자표기 변동(이름 다름·생년월 같음,
    # José: Jose Munoz↔호세무뇨스)은 여전히 Pass 2로 잡힘 — 남은 후보가 1쌍뿐이면 유일하니까.
    changes: list[dict[str, Any]] = []
    prev_year = latest - 1
    if prev_year in snapshots and snapshots[prev_year]:
        changes = _diff_roster_rows(
            list(snapshots[prev_year].values()), list(current.values()),
            joined_label="신규선임/등재", left_label="이탈(사퇴·임기만료·해임 중 하나)")
        for c in changes:
            c["since_year" if "이탈" not in c["change"] else "until_year"] = (
                latest if "이탈" not in c["change"] else prev_year)
    else:
        warnings.append(f"{prev_year}년 임원현황이 없어 재직/사퇴 비교 미산출.")

    # 직전 사업보고서 이후의 **기중 변동**(260730 사용자 지적).
    # 사업보고서끼리 비교하면 6월에 사임한 이사가 안 보인다 — 다음 사업보고서가 나와야 드러난다.
    # 분기·반기 명단을 직전 사업보고서와 대조하면 그 사이 들고 난 사람이 바로 보인다.
    # 스튜어드십 실무에서 「지금 이사회가 작년 말과 뭐가 다른가」가 곧 이 diff 다.
    interim_changes: list[dict[str, Any]] = []
    interim_vs = ""
    if interim_rows and snapshots.get(prev_year):
        interim_changes = _diff_roster_rows(
            list(snapshots[prev_year].values()), interim_rows,
            joined_label="신규 등재(직전 사업보고서 이후)",
            left_label="이탈(직전 사업보고서 이후 — 사퇴·임기만료·해임 중 하나)")
        interim_vs = f"{prev_year}년 사업보고서 → {interim_label}"

    board_count = sum(1 for r in roster if r["director_type"] in _BOARD_TYPES)

    # 사외이사 변동현황(outcmpnyDrctrNdChangeSttus) 교차검증 — DART 공식 집계(개별 성명은 없음).
    # fetch는 함수 상단에서 exctvSttus와 함께 이미 병렬로 끝남(oc_fetched), 여기선 가공만.
    official_changes: list[dict[str, Any]] = []
    for y, oc_rows in zip(years, oc_fetched):
        if oc_rows:
            r = oc_rows[0]
            official_changes.append({
                "year": y,
                "director_count": _to_int(r.get("drctr_co")),
                "outside_director_count": _to_int(r.get("otcmp_drctr_co")),
                "appointed": _to_int(r.get("apnt")),
                "released": _to_int(r.get("rlsofc")),
                "mid_term_resigned": _to_int(r.get("mdstrm_resig")),
            })

    # 사외이사 신규선임만 필터(QA 260709 발견 — 필터 없이 exctvSttus 전체 임원 diff와 비교하면
    # 미등기 임원까지 섞여 공식값과 규모 자체가 안 맞음, 예: 미래에셋증권 17 vs 공식 3).
    our_outside_new = sum(1 for c in changes if "이탈" not in c["change"] and c.get("director_type") == "사외이사")
    official_latest = next((o for o in official_changes if o["year"] == latest), None)
    diff_cross_check = None
    if official_latest is not None:
        official_appointed = official_latest.get("appointed") or 0
        diff_cross_check = {
            "our_outside_director_new_appointments": our_outside_new,
            "official_outside_director_appointed": official_appointed,
            "비고": "둘 다 '사외이사 신규선임' 건수로 맞춰 비교(같은 정의여도 재선임(연임)을 우리 diff는"
                    " '변동 없음'으로, 공식값은 다르게 셀 수 있어 완전 일치는 아닐 수 있음 — 크게 어긋나면"
                    " roster diff 추론을 의심할 신호로만 사용, 공식값이 항상 우선).",
        }

    # 변동을 등기 이사회 ↔ 미등기 집행임원으로 분리(3-에이전트 수렴 최우선, QA/스튜어드십 260709):
    # exctvSttus는 이사회 멤버만 적는 회사(삼성·현대차·POSCO)와 전 집행임원(상무·전무)까지 적는
    # 회사(미래에셋 157명·HD현대중공업 153명)가 섞여, diff를 통으로 내면 대형사에선 '감지된 이탈
    # 16명'이 대부분 상무라 정작 이사회 변동이 노이즈에 묻힌다. director_type(rgist_exctv_at)로 갈라
    # 이사회 변동을 주(主)로, 집행임원 변동은 참고로 분리. 종합신호 이탈도 이사회 기준이 된다.
    board_changes = [c for c in changes if (c.get("director_type") or "") in _BOARD_TYPES]
    exec_changes = [c for c in changes if (c.get("director_type") or "") not in _BOARD_TYPES]

    return {
        "roster": roster,
        "roster_as_of": roster_as_of,   # 현재 명단이 어느 보고서 기준인지 — 기준일이 다르면 해석이 달라진다
        # 직전 사업보고서 이후 기중 변동 — 연간 diff 가 놓치는 것.
        # 연간 diff 와 같은 기준으로 **이사회(등기)만** 싣는다 — 상무 인사이동을 이사회 변동으로
        # 오독하던 문제(QA 260709)를 여기서 되풀이하지 않는다. 집행임원은 건수만 요약한다.
        "changes_since_last_annual": [
            c for c in interim_changes if (c.get("director_type") or "") in _BOARD_TYPES],
        "executive_changes_since_last_annual_count": sum(
            1 for c in interim_changes if (c.get("director_type") or "") not in _BOARD_TYPES),
        "changes_since_last_annual_basis": interim_vs or None,
        "headcount_total": len(roster),
        "headcount_board": board_count,     # 등기 이사회 구성원(사내+사외+기타비상무+감사)
        "changes_vs_prev_year": board_changes,          # 이사회 변동만(종합신호·주 렌더가 이걸 씀)
        "executive_changes_vs_prev_year": exec_changes,  # 미등기 집행임원 변동(참고)
        "official_outside_director_changes": official_changes,  # DART 공식 집계, 연도별(YoY)
        "diff_cross_check": diff_cross_check,
    }


# ── individual: 개인별 5억+ 실명 보수 ───────────────────────────────────────

async def _individual_scope(
    client, corp_code: str, year: int, *, lookback_years: int = 1, warnings: list[str]
) -> dict[str, Any]:
    """개인별 5억 이상 실명 보수(법정공개, YoY). 5억 미만은 비공개라 상위 일부만 — 정직하게 명시."""
    years = [year - i for i in range(lookback_years)]
    # 연도별 fetch는 서로 독립 — 260709 병렬화(순차 sleep 제거)
    fetched = await asyncio.gather(
        *[_fetch_rows(client.get_individual_pay(corp_code, str(y), _REPRT_ANNUAL)) for y in years]
    )
    per_year: list[dict[str, Any]] = []
    for y, rows in zip(years, fetched):
        people = []
        for r in rows:
            name = _clean_name(r.get("nm"))
            total = _to_int(r.get("mendng_totamt"))
            # 5억+ 대상자가 없는 연도엔 DART가 nm='-'·금액공백 placeholder 행을 반환한다 —
            # 이걸 사람으로 집계하면 '(1명)' 헤더 뒤 빈 행이 뜨고 disclosed_count가 허위가 됨
            # (QA 260709: 로보티즈 3개연도 전부 유령 1명, 23/60 파일). 실명·금액 둘 다 없으면 제외.
            if name in ("", "-") and total is None:
                continue
            people.append({
                "name": name,
                "position": _clean_text_or_none(r.get("ofcps")) or "",
                "total_pay_krw": total,
                # mendng_totamt_ct_incls_mendng = 보수총액에 "미포함"된 보수(주로 RSA·스톡옵션 등
                # 아직 확정 안 된 주식기준보상) 설명 텍스트 — 260709 실측 확인(삼성전자/SK하이닉스).
                # raw 개행 있으면 목록 렌더가 깨져 _clean_text_or_none으로 정규화(260709 census 재발견).
                "breakdown_note": _clean_text_or_none(r.get("mendng_totamt_ct_incls_mendng")),
            })
        people.sort(key=lambda p: p["total_pay_krw"] or 0, reverse=True)
        rcept_no = next((r.get("rcept_no") for r in rows if r.get("rcept_no")), None)
        per_year.append({"year": y, "disclosed_count": len(people), "people": people,
                         "rcept_no": rcept_no})
    if not any(y["people"] for y in per_year):
        warnings.append("개인별 5억+ 보수 공개 대상이 없음(전원 5억 미만이거나 미공시).")
    return {
        "비고": "5억원 이상만 법정 개별공개 — 그 미만은 비공개(범주 평균은 compensation scope).",
        "per_year": per_year,
    }


# ── unregistered: 미등기 집행임원 보수 ──────────────────────────────────────

async def _unregistered_scope(
    client, corp_code: str, year: int, *, lookback_years: int = 1, warnings: list[str]
) -> dict[str, Any]:
    """미등기 집행임원 인당보수(YoY). 주총 승인한도 밖(등기 안 됨)이라 등기이사와 별개 지표."""
    years = [year - i for i in range(lookback_years)]
    # 연도별 fetch는 서로 독립 — 260709 병렬화(순차 sleep 제거)
    fetched = await asyncio.gather(
        *[_fetch_rows(client.get_unregistered_pay(corp_code, str(y), _REPRT_ANNUAL)) for y in years]
    )
    per_year: list[dict[str, Any]] = []
    for y, rows in zip(years, fetched):
        buckets = []
        for r in rows:
            rm = (r.get("rm") or "").strip()
            buckets.append({
                "type": (r.get("se") or "").strip(),
                "headcount": _to_int(r.get("nmpr")),
                "annual_total_krw": _to_int(r.get("fyer_salary_totamt")),
                "per_capita_krw": _to_int(r.get("jan_salary_am")),
                "note": rm if rm and rm != "-" else None,
            })
        rcept_no = next((r.get("rcept_no") for r in rows if r.get("rcept_no")), None)
        per_year.append({"year": y, "buckets": buckets, "rcept_no": rcept_no})
    if not any(y["buckets"] for y in per_year):
        warnings.append("미등기임원 보수 데이터 없음(미등기임원 미보유 또는 미공시).")
    return {"per_year": per_year}


# ── pay_gap: 경영진 vs 직원 보수 배수 ───────────────────────────────────────

# 합계/집계 행 표시어(부분매칭). 부문별 상세가 공백이고 이 행에만 총액이 오는 양식(삼성 등) 대응.
_EMP_TOTAL_MARKERS = ("합계", "소계", "총계", "전체")


def _emp_row_usable(r: dict[str, Any]) -> tuple[int, int] | None:
    """(연급여총액, 인원) — 둘 다 유효할 때만.

    인원은 **sm(공시상 합계 필드)을 우선 신뢰**(QA 260708 발견 — 클로봇 원문에서 특정 행의
    `rgllbr_co`가 981로 오기재(오타 추정, `sm`은 98)돼 있었는데, rgllbr_co+cnttk_co를 그대로
    합산하면 총원이 실제보다 10배 부풀려져 인당급여가 비현실적으로 낮아지고 pay_gap 배수가
    56.6배로 왜곡됨 — 다른 5개 행은 전부 rgllbr_co+cnttk_co==sm 일치, 이 행만 원문 자체 오류).
    sm이 없는 회사도 있어 그때만 rgllbr_co+cnttk_co로 폴백.
    """
    pay = _to_int(r.get("fyer_salary_totamt"))
    head = _to_int(r.get("sm"))
    if not head:
        head = (_to_int(r.get("rgllbr_co")) or 0) + (_to_int(r.get("cnttk_co")) or 0)
    return (pay, head) if (pay and head) else None


def _employee_avg_krw(emp_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """직원 전체 가중평균 급여 = Σ연급여총액 ÷ Σ인원.

    양식이 두 갈래(QA 260708 발견): ① 대부분은 부문·성별 상세행에 급여가 있음(현대차 남/여) →
    상세행만 합산(합계행 있으면 중복이므로 제외). ② 삼성류는 부문 상세행 급여가 공백("-")이고
    '성별합계' 행에만 실제 총액이 옴 → 이땐 상세행이 못 쓰이므로 합계행으로 폴백.
    ⇒ 상세행에 유효 데이터가 있으면 상세만, 없으면 합계행. **둘을 섞지 않음**(중복/누락 방지).
    """
    def _is_total(r: dict[str, Any]) -> bool:
        label = f"{r.get('se') or ''}{r.get('fo_bbm') or ''}"
        return any(m in label for m in _EMP_TOTAL_MARKERS)

    detail = [r for r in emp_rows if not _is_total(r)]
    totals = [r for r in emp_rows if _is_total(r)]

    use = [uv for r in detail if (uv := _emp_row_usable(r))]
    if not use:  # 상세행이 전부 공백 → 합계행 폴백(삼성류)
        use = [uv for r in totals if (uv := _emp_row_usable(r))]

    total_pay = sum(p for p, _ in use)
    total_head = sum(h for _, h in use)
    avg = round(total_pay / total_head) if total_head else None
    return {"employee_avg_pay_krw": avg, "employee_headcount": total_head or None}


def _employee_breakdown_rows(emp_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """부문·성별 원본 행을 그대로 구조화 노출(260709 — 지금까지 pay_gap 내부계산에만 쓰고
    화면엔 안 보여주던 데이터). 평균근속연수(avrg_cnwk_sdytrn)도 포함.

    **is_total 플래그 필수**(census 260709 발견): 삼성전자류는 부문 상세행(DX/DS)과 '성별합계'
    총계행이 **한 응답에 같이** 온다 — 상세행 total_headcount(예: DX남 38,119 + DX여 12,698 + ...)를
    합계행과 함께 그냥 다 더하면 실제 인원의 2배가 나온다(더블카운트). 소비자가 이 구분 없이 전체
    합산하지 않도록 명시."""
    def _is_total(r: dict[str, Any]) -> bool:
        label = f"{r.get('se') or ''}{r.get('fo_bbm') or ''}"
        return any(m in label for m in _EMP_TOTAL_MARKERS)

    rows = []
    for r in emp_rows:
        rows.append({
            "division": (r.get("fo_bbm") or "").strip(),
            "gender": (r.get("sexdstn") or "").strip(),
            "is_total": _is_total(r),  # True면 이미 전사 합계 — 다른 행과 합산 금지
            "regular_headcount": _to_int(r.get("rgllbr_co")),
            "contract_headcount": _to_int(r.get("cnttk_co")),
            "total_headcount": _to_int(r.get("sm")),
            # 내부 개행(신한지주 '3년 1개월\n(16년 2개월)')이 마크다운 표 셀을 두 줄로 쪼개므로
            # _clean_text_or_none로 개행을 ' / '로 정규화(QA 260709: 신한·KB금융 표 붕괴).
            "avg_tenure_years": _clean_text_or_none(r.get("avrg_cnwk_sdytrn")),
            "annual_salary_total_krw": _to_int(r.get("fyer_salary_totamt")),
            "per_capita_salary_krw": _to_int(r.get("jan_salary_am")),
        })
    return rows


async def _pay_gap_scope(
    client, corp_code: str, year: int, *, lookback_years: int = 1,
    comp_data: dict[str, Any] | None, warnings: list[str]
) -> dict[str, Any]:
    """등기이사 인당보수 ÷ 직원 평균급여 배수(YoY). comp_data(최신연도분 이미 조회했으면 재사용)로
    이사 인당 확보 — 나머지(과거) 연도는 병렬 fetch(260709, comp_data 재사용은 최신연도 1건 콜
    절약용 최적화라 병렬화를 위해 단순화: 재사용 실패시에만 fetch 대상에 포함)."""
    years = [year - i for i in range(lookback_years)]

    def _reused_pc(y: int) -> int | None:
        if not (comp_data and y == year):
            return None
        for cy in comp_data.get("per_year", []):
            if cy.get("year") == y:
                for b in cy.get("by_type", []):
                    if "등기이사" in (b.get("type") or "") and "제외" in (b.get("type") or ""):
                        return b.get("per_capita_krw")
        return None

    reused = {y: _reused_pc(y) for y in years}
    need_actual = [y for y in years if reused[y] is None]

    # emp_rows(전 연도) + actual_rows(재사용 실패한 연도만) 한 번에 병렬 fetch
    emp_fetched, actual_fetched = await asyncio.gather(
        asyncio.gather(*[_fetch_rows(client.get_employee_status(corp_code, str(y), _REPRT_ANNUAL))
                         for y in years]),
        asyncio.gather(*[_fetch_rows(client.get_director_pay_actual(corp_code, str(y), _REPRT_ANNUAL))
                         for y in need_actual]),
    )
    emp_by_year = dict(zip(years, emp_fetched))
    actual_by_year = dict(zip(need_actual, actual_fetched))

    per_year: list[dict[str, Any]] = []
    for y in years:
        emp_rows = emp_by_year[y]
        emp = _employee_avg_krw(emp_rows)

        director_pc = reused[y]
        if director_pc is None:
            for r in actual_by_year.get(y, []):
                if "등기이사" in (r.get("se") or "") and "제외" in (r.get("se") or ""):
                    director_pc = _to_int(r.get("psn1_avrg_pymntamt"))

        avg = emp.get("employee_avg_pay_krw")
        gap = round(director_pc / avg, 1) if director_pc and avg else None
        per_year.append({
            "year": y,
            "director_per_capita_krw": director_pc,
            "employee_avg_pay_krw": avg,
            "employee_headcount": emp.get("employee_headcount"),
            "gap_multiple": gap,
            "employee_breakdown": _employee_breakdown_rows(emp_rows),
        })

    if not any(y["gap_multiple"] for y in per_year):
        warnings.append("보수 격차 배수 산출 불가(이사 인당보수 또는 직원 평균급여 결측).")
    return {
        "per_year": per_year,
        "note": "등기이사(사외·감사위 제외) 인당보수 ÷ 직원 전체 가중평균 급여. 배수 자체가 "
                "과다/적정 판단은 아님(업종·직군 구성 차이 있음) — 비교 신호로만.",
    }


# ── pay_agenda: 주총 보수한도 안건(올해 제안) vs 작년 실적 ────────────────────

async def _pay_agenda_scope(company_query: str, *, warnings: list[str]) -> dict[str, Any]:
    """주총 소집공고의 '이사 보수한도 승인' 안건을 재사용해 올해 제안 한도 vs 작년 한도·실지급 비교.

    소집공고 하나에 current(올해 제안)·prior(작년 한도+실지급)가 모두 들어있어(shareholder_meeting
    notice가 이미 파싱), 별도 계산 없이 '작년 소진율'과 '올해 인상률'을 뽑아 보수한도 안건 의결권
    판단 신호를 만든다. 가치판단(찬반)은 하지 않고 인상률·작년소진율 사실만.
    """
    from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload

    # 소집공고 파싱을 8초로 제한(perf 260709: 일부 회사가 DART viewer HTML crawl 폴백에 6~21초
    # 낭비하는데 warning상 "개선 안 됨"=무용. director_board는 실패 시 compensation 표 승인한도
    # YoY 폴백이 이미 있으므로 타임아웃하면 자동 대체된다 — 한솔케미칼 21.6초→8초).
    try:
        payload = await asyncio.wait_for(
            build_shareholder_meeting_payload(company_query, scope="compensation"), timeout=8.0)
    except asyncio.TimeoutError:
        warnings.append("[pay_agenda] 소집공고 파싱 8초 초과 — 사업보고서 승인한도 YoY 폴백으로 대체.")
        return {"status": "no_agenda",
                "note": "주총 소집공고 파싱 타임아웃(viewer crawl) — 사업보고서 한도 YoY 폴백 사용."}
    for w in (payload.get("warnings") or []):
        warnings.append(f"[notice] {w}")
    items = ((payload.get("data") or {}).get("compensation") or {}).get("items") or []
    director_item = next(
        (it for it in items if "이사" in (it.get("target") or "") and "감사" not in (it.get("target") or "")),
        items[0] if items else None,
    )
    if not director_item:
        return {"status": "no_agenda",
                "note": "최근 주총 소집공고에 이사 보수한도 안건이 없거나 파싱 실패."}

    cur = director_item.get("current") or {}
    pri = director_item.get("prior") or {}
    proposed = cur.get("limitAmount")
    prior_limit = pri.get("limitAmount")
    prior_actual = pri.get("actualPaidAmount")

    limit_change_pct = (round((proposed - prior_limit) / prior_limit * 100, 1)
                        if proposed and prior_limit else None)
    prior_util = (round(prior_actual / prior_limit * 100, 1)
                  if prior_actual and prior_limit else None)

    signal = None
    if prior_util is not None and limit_change_pct is not None:
        if prior_util >= 90 and limit_change_pct > 5:
            signal = f"작년 소진율 {prior_util}%로 한도 거의 소진 + 올해 {limit_change_pct:+.1f}% 인상 요구 — 인상 근거 있음."
        elif prior_util < 60 and limit_change_pct > 5:
            signal = f"작년 소진율 {prior_util}%로 여유 있는데 올해 {limit_change_pct:+.1f}% 인상 요구 — 인상 근거 약함(검토)."
        elif limit_change_pct <= 0:
            signal = f"한도 동결/인하({limit_change_pct:+.1f}%)."

    return {
        "agenda_no": director_item.get("number"),
        "agenda_title": director_item.get("title"),
        "proposed_limit_krw": proposed,
        "prior_limit_krw": prior_limit,
        "prior_actual_krw": prior_actual,
        "limit_change_pct": limit_change_pct,
        "prior_utilization_pct": prior_util,
        "signal": signal,
        "note": "주총 소집공고 보수한도 안건의 current(올해 제안)/prior(작년 한도·실지급) 컬럼 재사용. "
                "인상률·작년 소진율은 사실 — 찬반 판단은 의결권 자문 도구(proxy_advise_before_meeting)에서 합니다.",
    }


# ── attendance: 개별 이사 이사회 출석률(사업보고서 원문 파서) ────────────────────

# 사업보고서 '이사회 활동내역'의 개별 이사 출석률: '한애라 (출석률 :100%)'·'박성하(출석률:50%)' 형태.
# 소수점 허용 — (\d+)만 잡으면 '서창석 (출석률 : 87.5%)'에서 "87" 뒤 '%'가 '.'과 안 맞아 매치 실패 →
# 해당 이사 통째 누락(260713 KT 서창석 실측). 값은 float로 파싱(int('87.5') 크래시 방지).
_ATTEND_RE = re.compile(r"([가-힣]{2,5})\s*\(\s*출석률\s*[:：]\s*(\d+(?:\.\d+)?)\s*%\s*\)")


def _parse_board_attendance(text: str) -> dict[str, int | float]:
    """사업보고서 원문에서 '이사회' 개별 출석률 파싱. 출석률 표가 여러 개(이사회·감사위·보상위 등)라
    같은 이름이 body마다 다른 값으로 나오므로, **첫 클러스터(=이사회 본 표 헤더행)만** 잡는다
    (섹션-local, 260709 실측: SK하이닉스 안현이 이사회 91% vs 위원회 100%로 달라 마지막값 잡으면 오류).
    헤더행은 이름들이 연속(간격<400자) → 큰 간격 나오면 다음 위원회 표로 보고 끊는다."""
    ms = list(_ATTEND_RE.finditer(text))
    if not ms:
        return {}
    cluster = [ms[0]]
    for m in ms[1:]:
        if m.start() - cluster[-1].end() < 400:
            cluster.append(m)
        else:
            break
    board: dict[str, float] = {}
    for m in cluster:
        if m.group(1) not in board:      # 첫 표(이사회)의 첫 값 우선
            val = float(m.group(2))      # 87.5 등 소수점 — int()는 크래시
            board[m.group(1)] = int(val) if val.is_integer() else val
    return board


async def _attendance_scope(client, corp_code: str, year: int, *, warnings: list[str]) -> dict[str, Any]:
    """개별 이사 이사회 출석률 — 사업보고서 원문 파서(v2, 260709 신규). exctvSttus의 rcept_no로 그
    사업보고서 원문(document.xml, 각주 해소와 캐시 공유)을 받아 '이사회 활동내역'의 출석률 표를 파싱.
    금융지주 등 지배구조보고서 별도양식이라 사업보고서에 표가 없으면 status='not_found'로 정직하게."""
    rcept_no = None
    board_headcount = None
    for y in (year, year - 1):  # 당해 사업보고서 없으면 전년
        rows = await _fetch_rows(client.get_executive_status(corp_code, str(y), _REPRT_ANNUAL))
        rcept_no = next((r.get("rcept_no") for r in rows if r.get("rcept_no")), None)
        if rcept_no:
            # 완전성 교차검증용 등기 이사회 인원(같은 exctvSttus 행에서 직접 — roster 스콥 의존 없음)
            board_headcount = sum(1 for r in rows if (r.get("rgist_exctv_at") or "").strip() in _BOARD_TYPES)
            break
    if not rcept_no:
        return {"status": "not_found", "note": "사업보고서 접수번호 확보 실패(임원현황 미제출)."}
    try:
        doc = await client.get_document_cached(rcept_no)
    except Exception as e:  # noqa: BLE001
        warnings.append(f"[attendance] 원문 조회 실패: {e}")
        return {"status": "fetch_failed", "rcept_no": rcept_no, "note": "사업보고서 원문 조회 실패."}
    text = (doc or {}).get("text") or ""
    board = _parse_board_attendance(text)
    if not board:
        return {"status": "not_found", "rcept_no": rcept_no,
                "note": "사업보고서에서 이사회 개별 출석률 표 미발견 — 금융지주 등 지배구조보고서 "
                        "별도양식이거나 소규모사 미기재."}
    directors = sorted(
        [{"name": n, "attendance_pct": p, "low": p < _ATTENDANCE_LOW} for n, p in board.items()],
        key=lambda d: d["attendance_pct"])
    # 이사회 개최 횟수(best-effort) — 출석률 클러스터 근처의 'N회 개최'/'총 N회'
    first = _ATTEND_RE.search(text)
    near = text[max(0, first.start() - 1500): first.start()] if first else ""
    mm = re.search(r"(\d+)\s*회\s*개최|총\s*(\d+)\s*회", near)
    count = next((int(g) for g in (mm.groups() if mm else []) if g), None)
    return {
        "status": "parsed",
        "source": "사업보고서 이사회 활동내역 원문",
        "rcept_no": rcept_no,
        "board_meeting_count": count,
        "board_headcount": board_headcount,   # 등기 이사회 인원(완전성 교차검증용)
        "directors": directors,
        "low_attendance": [d for d in directors if d["low"]],
        "note": f"이사회 출석률 <{_ATTENDANCE_LOW:.0f}%는 저조(low) 표시. 위원회(감사위 등) 출석률은 "
                "이사회 본 표만 파싱해 제외. 개별 성명은 사업보고서 원문 표 기준.",
    }


# ── pay_criteria: 보수 산정기준 (사업보고서 VIII-2 원문 파서) ─────────────────

async def _pay_criteria_scope(client, corp_code: str, year: int, *, warnings: list[str]) -> dict[str, Any]:
    """보수 산정기준 — 사업보고서 VIII-2 「임원의 보수 등」 원문 파서(260713 신규).

    정형 API(compensation/individual)가 못 주는 **산식·KPI 서술**을 원문에서 구조화:
    ① 보수지급기준(정책, 버킷별 급여/상여/성과급 배수) ② 개인별 산정기준(실명 급여/상여 분해 +
    계량·비계량 KPI). exctvSttus의 rcept_no로 사업보고서 원문(document.xml)을 받아 파싱하며,
    attendance·각주해소와 원문 캐시를 공유(같은 회사면 재fetch 없음). 제목/순서가 아니라 표 헤더
    시그니처+rowspan 정규화+표별 단위로 판별([[executive_pay]]). 금융지주 등 별도양식은 not_found."""
    # 내부 단계별 소요(ms) 계측 — scope 총시간(_timed)은 있으나 병목이 원문 fetch(I/O)인지
    # parse(CPU)인지 안 보였다(260714). 실측: fetch_gather가 지배(8~14MB, 이미 API와 병렬),
    # parse_executive_pay가 CPU 300~770ms로 2순위(원문 캐시히트 시 지배 병목). data.timing_detail로 노출.
    _td: dict[str, float] = {}
    _t0 = time.perf_counter()
    rcept_no = None
    used_year = year
    for y in (year, year - 1):  # 당해 사업보고서 없으면 전년
        rows = await _fetch_rows(client.get_executive_status(corp_code, str(y), _REPRT_ANNUAL))
        rcept_no = next((r.get("rcept_no") for r in rows if r.get("rcept_no")), None)
        if rcept_no:
            used_year = y
            break
    _td["status_probe"] = round((time.perf_counter() - _t0) * 1000, 1)
    if not rcept_no:
        return {"status": "not_found", "note": "사업보고서 접수번호 확보 실패(임원현황 미제출)."}
    # 원문 document(8MB, 느림)와 정형 API 개인별 보수(hmvAuditIndvdlBySttus)는 서로 독립 —
    # get_individual_pay는 rcept_no가 필요 없어 document fetch와 **병렬**로 돌린다(260713). 이렇게
    # 하면 하이브리드 교차검증용 API 호출이 wall-clock을 사실상 늘리지 않는다(작은 JSON이 큰 원문
    # 다운로드 그늘에 가려짐). API 실패(5억+ 대상 없음 등)는 검증 생략일 뿐 치명적 아님 → 흡수.
    _t1 = time.perf_counter()
    try:
        doc, api_rows = await asyncio.gather(
            client.get_document_cached(rcept_no),
            _fetch_rows(client.get_individual_pay(corp_code, str(used_year), _REPRT_ANNUAL)),
        )
    except Exception as e:  # noqa: BLE001
        warnings.append(f"[pay_criteria] 원문 조회 실패: {e}")
        return {"status": "fetch_failed", "rcept_no": rcept_no, "note": "사업보고서 원문 조회 실패."}
    _td["fetch_gather"] = round((time.perf_counter() - _t1) * 1000, 1)
    html = (doc or {}).get("html") or ""
    text = (doc or {}).get("text") or ""
    _t2 = time.perf_counter()
    parsed = parse_executive_pay(html, text)
    _td["parse"] = round((time.perf_counter() - _t2) * 1000, 1)
    if not parsed["pay_policy"] and not parsed["individuals"] and not parsed["policy_narrative"]:
        return {"status": "not_found", "rcept_no": rcept_no,
                "note": "VIII-2 보수지급기준/산정기준 표 미발견 — 금융지주 지배구조 연차보고서 등 "
                        "별도양식이거나 원문 서식 차이 가능."}
    # 하이브리드 교차검증: 파서 Σ컴포넌트 vs 정형 API 공식 총액(독립 소스). in-place로 individuals에
    # api_consistent 부여 + 요약 반환. API가 비어도(5억+ 없음) 빈 요약이라 안전.
    _t3 = time.perf_counter()
    api_reconciliation = reconcile_with_api(parsed, api_rows)
    _td["reconcile"] = round((time.perf_counter() - _t3) * 1000, 1)
    return {
        "status": "parsed",
        "source": "사업보고서 VIII. 임원 및 직원 등에 관한 사항 › 2. 임원의 보수 등 원문",
        "rcept_no": rcept_no,
        "timing_detail": _td,
        "unit_note": "금액(amount_krw/total_krw)은 원문 표별 단위를 원(KRW)으로 환산한 값.",
        "pay_policy": parsed["pay_policy"],
        "policy_narrative": parsed["policy_narrative"],
        "individuals": parsed["individuals"],
        "individual_totals": parsed.get("individual_totals"),
        "reconciliation": parsed.get("reconciliation"),
        "api_reconciliation": api_reconciliation,
        "aggregate_seen": parsed["aggregate_seen"],
        "unknown_tables": parsed["unknown_tables"],
        "note": "'상위 5명' group은 미등기·직원 포함 — 이사회 명단과 다름(합산 금지). KPI 가중치·성과급 "
                "배수는 basis/ranges에 원문 그대로 보존(회사별 편차 커 무리한 구조화 안 함).",
    }


# ── 진입점 ─────────────────────────────────────────────────────────────────

async def build_director_board_payload(
    company_query: str, *, scope: str = "summary", year: int = 0, lookback_years: int = 3,
    format: str = "md", resolve_footnotes: bool = True,
) -> dict[str, Any]:
    client = get_dart_client()
    calls_start = client.api_call_snapshot()
    t_start = time.perf_counter()
    timings: dict[str, float] = {}

    if scope not in _SUPPORTED_SCOPES:
        return ToolEnvelope(
            tool="director_board", status=AnalysisStatus.ERROR, subject=company_query,
            warnings=[f"지원하지 않는 scope='{scope}'. 사용 가능: {sorted(_SUPPORTED_SCOPES)}"],
            data={"query": company_query, "scope": scope},
        ).to_dict()

    resolution = await resolve_company_query(company_query)
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        return ToolEnvelope(
            tool="director_board", status=resolution.status, subject=company_query,
            warnings=[f"'{company_query}' 상장사를 찾지 못함"],
            data={"query": company_query, "candidates": resolution.candidates},
        ).to_dict()
    if resolution.status == AnalysisStatus.AMBIGUOUS:
        return ToolEnvelope(
            tool="director_board", status=AnalysisStatus.AMBIGUOUS, subject=company_query,
            data={"query": company_query, "candidates": resolution.candidates},
        ).to_dict()

    selected = resolution.selected
    corp_code = selected["corp_code"]
    canonical_name = selected.get("corp_name", company_query)
    if not year:
        # 최근 확정 사업연도(사업보고서는 익년 3월 제출 → 보수적으로 전년)
        from datetime import date
        year = date.today().year - 1

    timings["resolve"] = round((time.perf_counter() - t_start) * 1000, 1)

    warnings: list[str] = []
    data: dict[str, Any] = {
        "query": company_query, "canonical_name": canonical_name,
        "corp_code": corp_code, "scope": scope, "year": year,
        "lookback_years": lookback_years,
    }

    # scope 함수 6개는 서로 데이터를 안 씀(pay_gap의 comp_data 재사용은 순수 최적화라 병렬화를 위해
    # 포기 — compensation 자체를 fetch 안 하는 케이스도 있어 항상 재사용 가능한 게 아니었음) —
    # 전부 asyncio.gather로 병렬 실행(260709, 이전엔 6개가 순차 대기 → 지연시간 누적).
    # warnings 리스트는 asyncio 협조적 스케줄링이라 concurrent append도 안전(진짜 스레드 병렬 아님).
    # 각 scope는 _timed로 감싸 개별 소요(ms)를 timings에 기록 → data["timing"]으로 노출(성능측정용).
    tasks: dict[str, Any] = {}
    if scope in {"compensation", "summary"}:
        tasks["compensation"] = _timed(_compensation_scope(
            client, corp_code, year, lookback_years=lookback_years, warnings=warnings),
            "compensation", timings)
    if scope in {"roster", "summary"}:
        tasks["roster"] = _timed(_roster_scope(
            client, corp_code, year, lookback_years=lookback_years, warnings=warnings),
            "roster", timings)
    if scope in {"individual", "summary"}:
        tasks["individual"] = _timed(_individual_scope(
            client, corp_code, year, lookback_years=lookback_years, warnings=warnings),
            "individual", timings)
    if scope in {"unregistered", "summary"}:
        tasks["unregistered"] = _timed(_unregistered_scope(
            client, corp_code, year, lookback_years=lookback_years, warnings=warnings),
            "unregistered", timings)
    if scope in {"pay_gap", "summary"}:
        tasks["pay_gap"] = _timed(_pay_gap_scope(
            client, corp_code, year, lookback_years=lookback_years, comp_data=None, warnings=warnings),
            "pay_gap", timings)
    if scope in {"pay_agenda", "summary"}:
        tasks["pay_agenda"] = _timed(_pay_agenda_scope(company_query, warnings=warnings),
                                     "pay_agenda", timings)
    if scope == "attendance":
        # v2(260709): 사업보고서 원문에서 개별 이사 출석률 파싱. summary 기본엔 제외 — 원문 fetch(8MB,
        # 금융지주 최대 10초)라 흔한 summary 경로를 느리게 함. 출석률은 on-demand scope로 조회
        # (footnote 해소와 원문 캐시 공유라 같은 회사면 재사용). 필요 시 summary 포함은 옵션화 가능.
        tasks["attendance"] = _timed(_attendance_scope(client, corp_code, year, warnings=warnings),
                                     "attendance", timings)
    if scope == "pay_criteria":
        # 260713: 사업보고서 VIII-2 원문에서 보수 산정기준(정책+개인별 KPI) 파싱. attendance처럼
        # 원문 fetch(8MB)라 summary 기본엔 제외 — on-demand scope(원문 캐시는 attendance와 공유).
        tasks["pay_criteria"] = _timed(_pay_criteria_scope(client, corp_code, year, warnings=warnings),
                                       "pay_criteria", timings)

    try:
        if tasks:
            results = await asyncio.gather(*tasks.values())
            for key, result in zip(tasks.keys(), results):
                data[key] = result
    except DartClientError as e:
        return ToolEnvelope(
            tool="director_board", status=AnalysisStatus.ERROR, subject=canonical_name,
            warnings=[f"DART 조회 실패: {e}"], data=data,
        ).to_dict()

    # pay_agenda 안건 미파싱/부재 시 compensation 표의 연도별 승인한도 YoY로 폴백(스튜어드십 260709:
    # 기아 80→175억처럼 상단 표엔 한도가 있는데 안건 섹션만 빈칸이던 8/60 케이스 보강). 안건 원문이
    # 아니라 사업보고서 승인한도의 연도간 변화이므로 출처를 명확히 구분해 표기.
    if scope == "summary":
        pa = data.get("pay_agenda") or {}
        if not pa.get("proposed_limit_krw"):
            comp_years = (data.get("compensation") or {}).get("per_year") or []
            limits = [(y.get("year"), y.get("director_pay_limit_krw"))
                      for y in comp_years if y.get("director_pay_limit_krw")]
            distinct: list[tuple[int, int]] = []
            for yr, lim in limits:  # 캐리포워드로 중복된 값 접어 실제 변화점만
                if not distinct or distinct[-1][1] != lim:
                    distinct.append((yr, lim))
            if len(distinct) >= 2:
                (ny, nl), (py, pl) = distinct[0], distinct[1]
                pa["fallback_limit_recent_krw"] = nl
                pa["fallback_limit_prev_krw"] = pl
                pa["fallback_limit_change_pct"] = round((nl - pl) / pl * 100, 1) if pl else None
                pa["fallback_note"] = (
                    f"주총안건 미파싱 — 사업보고서 승인한도 YoY로 대체({py}년 {pl/1e8:.0f}억원 → "
                    f"{ny}년 {nl/1e8:.0f}억원). 주총 소집공고 안건 원문이 아님.")
                data["pay_agenda"] = pa

        data["assessment"] = _summary_assessment(data)

    # 데이터 품질 플래그(machine-readable 통합) — 흩어진 신호(각주 마커·한도 미상·소진율 초과·안건
    # 미파싱·교차검증 불일치)를 스콥별 항목 배열로 모아 소비자(LLM/에이전트)가 프로그램적으로 읽게 함.
    # 260709 설계: 전역 '파싱의심' 단일 플래그는 이질적 신호를 뭉뚱그려 실제값(스톡옵션 초과 등)에
    # 오탐 → 종류(kind)별로 분리. raw_text는 원문 파싱 스콥(attendance v2·notice)에서 파싱 실패 시
    # 원문을 담는 자리 — 정형 API 마커는 본문이 응답에 없어 마커 자체만 참고로 싣는다.
    flags = _collect_data_quality_flags(data)
    # 각주 마커 플래그가 있으면 원문 폴백으로 해소 시도(마커 뜬 공시만 1회씩 fetch·캐시).
    # 정형 API가 못 주는 각주 본문을 사업보고서 원문에서 복구 — 260709 코붕이 제안(url 기반).
    if resolve_footnotes and any(f.get("kind") == "footnote_marker_unresolved" for f in flags):
        await _resolve_footnotes(client, flags)
        # 동일 resolved_text 반복 제거(footnote_qa: 한국가스공사 통합공시 각주가 연도·scope 넘어
        # 5회 반복). 같은 본문이면 첫 건만 남긴다(정보 손실 없음, 노이즈만 감소).
        seen_bodies: set = set()
        pruned = []
        for f in flags:
            rt = f.get("resolved_text")
            if rt and rt in seen_bodies:
                continue
            if rt:
                seen_bodies.add(rt)
            pruned.append(f)
        flags = pruned
    data["data_quality_flags"] = flags

    data["usage"] = build_usage(client.api_call_snapshot() - calls_start)
    # 성능측정용 타이머(사용자 요청): scope별 개별 소요 + 전체 wall-clock(ms). 병렬 실행이라
    # 전체는 개별 합보다 작다(가장 느린 scope에 수렴) — 순차 대비 얼마나 절약했는지도 볼 수 있음.
    data["timing"] = {
        "per_scope_ms": timings,
        "total_wall_ms": round((time.perf_counter() - t_start) * 1000, 1),
        "scope_sum_ms": round(sum(v for k, v in timings.items() if k != "resolve"), 1),
    }

    # 구조화 품질 로그 1줄(코붕이 260709) — 실전 트래픽을 상시 관측으로. 로그에서 flags 종류별 급증·
    # 각주 복구율 하락·특정 scope 지연·새 warning을 잡아 사용자 불평 전에 엣지케이스 발견. 로그 자체가
    # tool을 깨선 안 되므로 방어적으로.
    try:
        fl = data.get("data_quality_flags") or []
        kinds = dict(Counter(f.get("kind") for f in fl))
        fn = [f for f in fl if f.get("kind") == "footnote_marker_unresolved"]
        fn_res = sum(1 for f in fn if f.get("resolved_text"))
        logger.info(
            "[DB_QUALITY] %s scope=%s wall=%.0fms calls=%s flags=%s fn=%d/%d warns=%d",
            canonical_name, scope, data["timing"]["total_wall_ms"],
            (data.get("usage") or {}).get("dart_api_calls"), kinds or {}, fn_res, len(fn),
            len(warnings))
    except Exception:  # noqa: BLE001 — 관측 로그가 tool 동작을 절대 깨지 않게
        pass

    return ToolEnvelope(
        tool="director_board", status=AnalysisStatus.EXACT, subject=canonical_name,
        warnings=warnings, data=data,
    ).to_dict()


def _collect_data_quality_flags(data: dict[str, Any]) -> list[dict[str, Any]]:
    """흩어진 파싱 품질 신호를 스콥별 항목으로 통합. 각 항목:
      {scope, kind, severity(info/warn), detail, [year], [raw_text], [fallback_used]}.
    kind별 의미가 달라 소비자가 선택적으로 대응 가능(극단 소진율=info는 실제값이라 무시해도 되고,
    crosscheck_mismatch=warn은 roster diff 신뢰도를 낮춰 봐야 함). 120사 census 근거로 종류 설계."""
    flags: list[dict[str, Any]] = []

    comp = data.get("compensation") or {}
    for y in comp.get("per_year", []):
        yr = y.get("year")
        rc = y.get("rcept_no")
        for note in y.get("limit_notes", []):
            if "한도 미상" in note:
                flags.append({"scope": "compensation", "year": yr, "kind": "limit_unreliable",
                              "severity": "warn",
                              "detail": "승인한도 원문이 각주 마커라 금액 미파싱 — 소진율 산출에서 제외(직전 유효연도 lookback)."})
            elif _is_bare_marker(note):
                flags.append({"scope": "compensation", "year": yr, "kind": "footnote_marker_unresolved",
                              "severity": "info", "raw_text": note, "rcept_no": rc,
                              "detail": "승인한도 비고가 각주 마커뿐 — 각주 본문은 사업보고서 원문에 있음(정형 API 미제공)."})
        for b in y.get("by_type", []):
            if _is_bare_marker(b.get("note")):
                flags.append({"scope": "compensation", "year": yr, "kind": "footnote_marker_unresolved",
                              "severity": "info", "raw_text": b.get("note"), "rcept_no": rc,
                              "detail": f"[{b.get('type')}] 실지급 비고가 각주 마커뿐 — 원문 각주 참조."})
        if y.get("utilization_flag") == "exceeded_limit":
            flags.append({"scope": "compensation", "year": yr, "kind": "utilization_exceeds_limit",
                          "severity": "info",
                          "detail": f"소진율 {y.get('utilization_pct')}% — 주총 승인한도 초과 지급. 파싱오류 아님"
                                    "(퇴직금·성과급·스톡옵션 행사이익 등). 비고·개인별로 원인 확인 권장."})

    indiv = data.get("individual") or {}
    for y in indiv.get("per_year", []):
        for p in y.get("people", []):
            if _is_bare_marker(p.get("breakdown_note")):
                flags.append({"scope": "individual", "year": y.get("year"),
                              "kind": "footnote_marker_unresolved", "severity": "info",
                              "raw_text": p.get("breakdown_note"), "rcept_no": y.get("rcept_no"),
                              "subject": p.get("name"),
                              "detail": f"{p.get('name')} 보수 미포함내역(RSA/스톡옵션)이 각주 마커뿐 — 원문 각주 참조."})

    unreg = data.get("unregistered") or {}
    for y in unreg.get("per_year", []):
        for b in y.get("buckets", []):
            if _is_bare_marker(b.get("note")):
                flags.append({"scope": "unregistered", "year": y.get("year"),
                              "kind": "footnote_marker_unresolved", "severity": "info",
                              "raw_text": b.get("note"), "rcept_no": y.get("rcept_no"),
                              "detail": "미등기임원 보수 비고가 각주 마커뿐 — 원문 각주 참조."})

    pa = data.get("pay_agenda") or {}
    if pa and not pa.get("proposed_limit_krw"):
        fb = bool(pa.get("fallback_limit_recent_krw"))
        flags.append({"scope": "pay_agenda", "kind": "parse_failed",
                      "severity": "info" if fb else "warn", "fallback_used": fb,
                      "detail": "주총 소집공고에서 보수한도 안건 미파싱." +
                                (" 사업보고서 승인한도 YoY로 폴백 제공." if fb
                                 else " 폴백도 불가(compensation 연도별 한도 부족).")})

    roster = data.get("roster") or {}
    cc = roster.get("diff_cross_check") or {}
    if cc:
        ours = cc.get("our_outside_director_new_appointments") or 0
        official = cc.get("official_outside_director_appointed") or 0
        if abs(ours - official) >= 3:
            flags.append({"scope": "roster", "kind": "crosscheck_mismatch", "severity": "warn",
                          "상세": f"이름기반 사외이사 신규선임 비교({ours})와 DART 공식 집계({official})가 "
                                    f"{abs(ours - official)} 차이 — 재선임/정의차 가능하나 임원현황 비교 신뢰도 낮음(공식값 우선)."})

    att = data.get("attendance") or {}
    for d in att.get("low_attendance", []):
        flags.append({"scope": "attendance", "kind": "low_attendance", "severity": "warn",
                      "subject": d.get("name"),
                      "detail": f"{d.get('name')} 이사회 출석률 {d.get('attendance_pct')}% "
                                f"(<{_ATTENDANCE_LOW:.0f}%) — 저조."})
    # 완전성 교차검증(260709): 회사가 (출석률:%)로 요약한 이사 수 < 등기 이사회 인원이면, 원문이
    # 일부(주로 사외이사)만 요약한 것 — 전체 이사회로 오독하지 않게 flag. roster·attendance 둘 다
    # 있는 summary에서만 가능(이 partial 여부를 로그로도 관측 → 실전 커버리지 추적, 코붕이 260709).
    if att.get("status") == "parsed":
        parsed_n = len(att.get("directors", []))
        board_n = att.get("board_headcount") or (data.get("roster") or {}).get("headcount_board")
        if board_n and parsed_n < board_n:
            flags.append({"scope": "attendance", "kind": "attendance_partial", "severity": "info",
                          "detail": f"원문에 출석률 요약된 이사 {parsed_n}명 < 등기 이사회 {board_n}명 — "
                                    "회사가 일부(주로 사외이사)만 '(출석률:%)' 형식으로 기재. 전체 이사회 아님."})

    # 각주 마커 flag dedup(footnote_qa 260709: 보로노이 동일연도·동일마커 2회 중복 노출 버그) —
    # (scope, year, 정규화 마커, subject) 같으면 하나만.
    deduped: list[dict[str, Any]] = []
    seen: set = set()
    for f in flags:
        if f.get("kind") == "footnote_marker_unresolved":
            key = (f.get("scope"), f.get("year"), _norm_marker(f.get("raw_text") or ""), f.get("subject"))
            if key in seen:
                continue
            seen.add(key)
        deduped.append(f)
    return deduped


def _summary_assessment(data: dict[str, Any]) -> dict[str, Any]:
    """목표 질문 3개에 대한 신호(판단 아님 — 기계적 사실 + flag)."""
    comp_years = (data.get("compensation") or {}).get("per_year") or []
    latest = comp_years[0] if comp_years else {}
    prev = comp_years[1] if len(comp_years) > 1 else {}

    # 인당보수 변동 (등기이사(사외·감사위 제외) 유형 기준)
    def _reg_director_per_capita(comp: dict[str, Any]) -> int | None:
        for b in comp.get("by_type") or []:
            t = b.get("type") or ""
            if "등기이사" in t and "제외" in t:
                return b.get("per_capita_krw")
        return None

    pc_now = _reg_director_per_capita(latest)
    pc_prev = _reg_director_per_capita(prev)
    per_capita_change = None
    if pc_now and pc_prev:
        per_capita_change = {
            "prev_krw": pc_prev, "now_krw": pc_now,
            "delta_pct": round((pc_now - pc_prev) / pc_prev * 100, 1),
        }

    roster = data.get("roster") or {}
    changes = roster.get("changes_vs_prev_year") or []
    departures = [c for c in changes if "이탈" in (c.get("change") or "")]

    return {
        "latest_utilization_pct": latest.get("utilization_pct"),
        "latest_per_capita_krw": pc_now,
        "per_capita_change_yoy": per_capita_change,
        "departures_detected": departures,
        "비고": "소진율·인당보수는 기계적 사실. '적절성'은 동종업계·규모 대비 판단이 필요해 "
                "이 tool은 수치와 변동·flag만 제공(가치판단 안 함).",
    }
