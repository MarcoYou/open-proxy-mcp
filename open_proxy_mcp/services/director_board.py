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
    표5-2-1 겸직. (v1은 정형+출석; 금융지주 PDF 별도양식은 v2 — 경고 반환)

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
import re
import time
from typing import Any

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.services.company import resolve_company_query
from open_proxy_mcp.services.contracts import (
    AnalysisStatus,
    ToolEnvelope,
    build_usage,
)

_SUPPORTED_SCOPES = {"compensation", "roster", "individual", "unregistered", "pay_gap",
                     "pay_agenda", "attendance", "summary"}

# 소진율 임계 (참고용 flag — 판단이 아니라 신호)
_UTILIZATION_HIGH = 90.0   # 한도의 90%+ 소진 = 여유 적음
_ATTENDANCE_LOW = 75.0     # 출석률 75% 미만 = 저조 flag

_REPRT_ANNUAL = "11011"    # 사업보고서


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


# 각주 문장 종결어미 — 진짜 각주 '정의'는 문장으로 끝난다. 표 행 조각은 안 끝난다(정밀도 우선).
_FN_END = re.compile(r"(받았습니다|하였습니다|되었습니다|있습니다|없습니다|됩니다|입니다|습니다|합니다|하였음|하였다|하였함|였음|됨|함\b|임\b)[.\)]?")


def _clean_fn_body(body: str) -> tuple[str, bool]:
    """각주 본문 정리: 앞쪽 마커/※/콜론 제거 후 첫 문장 종결에서 자름.
    반환 (정리본문, 확신) — 문장 종결이 있으면 확신 True(진짜 각주), 없으면 False(표 조각 의심)."""
    body = re.sub(r"^\s*[※\-]*\s*\(?\s*(?:주|[*])\s?\d+\s*\)?\s*[:：]?\s*", "", body).strip()
    m = _FN_END.search(body)
    if m:
        return body[:m.end()].strip(), True
    return body.strip(), False


def _extract_footnote(text: str, scope: str, marker: str, window: int = 6000) -> tuple[str | None, str | None]:
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
        if not confident or len(re.findall(r"[가-힣]", cleaned)) < 6 or not _FN_SIGNAL.search(cleaned):
            continue  # 문장 종결 없음/한글 부족/신호 없음 → 표 조각으로 보고 스킵(확신 각주만 채택)
        if mk not in defs or len(cleaned) > len(defs[mk]):
            defs[mk] = cleaned[:250]
    if want in defs:
        return defs[want], None
    return None, seg[:400].strip()  # 확신 각주 못 찾음 → raw 발췌 폴백


async def _resolve_footnotes(client, flags: list[dict[str, Any]]) -> None:
    """footnote_marker_unresolved 플래그를 원문에서 해소(in-place). 마커 뜬 공시(rcept_no)만
    get_document_cached로 1회씩 fetch(캐시) — 앱레벨 limiter 보호. 확신 추출→resolved_text,
    애매→raw_text 폴백. 원문 파싱 스콥에서 코붕이의 raw_text fallback 설계를 실제 구현한 지점."""
    targets = [f for f in flags if f.get("kind") == "footnote_marker_unresolved" and f.get("rcept_no")]
    by_rcept: dict[str, list[dict]] = {}
    for f in targets:
        by_rcept.setdefault(f["rcept_no"], []).append(f)
    for rcept_no, fs in by_rcept.items():
        try:
            doc = await client.get_document_cached(rcept_no)
        except Exception:  # noqa: BLE001 — 원문 폴백 실패는 치명적 아님(마커만 남음)
            continue
        text = (doc or {}).get("text") or ""
        if not text:
            continue
        for f in fs:
            body, raw = _extract_footnote(text, f.get("scope"), f.get("raw_text") or "")
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
        prev_rows = list(snapshots[prev_year].values())
        curr_rows = list(current.values())
        prev_names = {_clean_name(r.get("nm")) for r in prev_rows}
        curr_names = {_clean_name(r.get("nm")) for r in curr_rows}

        # Pass 1: 이름으로 못 잡힌 나머지만 추림
        prev_unmatched = [r for r in prev_rows if _clean_name(r.get("nm")) not in curr_names]
        curr_unmatched = [r for r in curr_rows if _clean_name(r.get("nm")) not in prev_names]

        # Pass 2: 남은 후보군 안에서 birth_ym이 유일하게 겹치는 쌍만 동일인으로 인정
        def _births(rows):
            from collections import Counter
            return Counter((r.get("birth_ym") or "").strip() for r in rows if (r.get("birth_ym") or "").strip())

        prev_b_count, curr_b_count = _births(prev_unmatched), _births(curr_unmatched)
        matched_prev_births = {
            (r.get("birth_ym") or "").strip() for r in prev_unmatched
            if (b := (r.get("birth_ym") or "").strip()) and prev_b_count[b] == 1 and curr_b_count.get(b) == 1
        }

        for r in curr_unmatched:
            b = (r.get("birth_ym") or "").strip()
            if b not in matched_prev_births:
                changes.append({"name": _clean_name(r.get("nm")), "change": "신규선임/등재",
                                "position": _clean_text_or_none(r.get("ofcps")) or "", "since_year": latest,
                                "director_type": (r.get("rgist_exctv_at") or "").strip()})
        for r in prev_unmatched:
            b = (r.get("birth_ym") or "").strip()
            if b not in matched_prev_births:
                changes.append({"name": _clean_name(r.get("nm")),
                                "change": "이탈(사퇴·임기만료·해임 중 하나)",
                                "position": _clean_text_or_none(r.get("ofcps")) or "", "until_year": prev_year,
                                "director_type": (r.get("rgist_exctv_at") or "").strip()})
    else:
        warnings.append(f"{prev_year}년 임원현황이 없어 재직/사퇴 diff 미산출.")

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
            "note": "둘 다 '사외이사 신규선임' 건수로 맞춰 비교(같은 정의여도 재선임(연임)을 우리 diff는"
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
        "note": "5억원 이상만 법정 개별공개 — 그 미만은 비공개(범주 평균은 compensation scope).",
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

    payload = await build_shareholder_meeting_payload(company_query, scope="compensation")
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
                "인상률·작년소진율은 사실 — 찬반 판단은 proxy_advise_before_meeting 참조.",
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

    try:
        if tasks:
            results = await asyncio.gather(*tasks.values())
            for key, result in zip(tasks.keys(), results):
                data[key] = result
        if scope in {"attendance", "summary"}:
            # v1: 원문 파서 미구현 — 정직하게 stub. (금융지주는 PDF 별도양식)
            data["attendance"] = {"status": "not_implemented",
                                  "note": "개별 이사 출석률·선임변동·겸직은 지배구조보고서 원문 파서 필요 "
                                          "(v2 예정). 금융지주는 PDF 별도양식이라 OCR tier 필요."}
            if scope == "attendance":
                warnings.append("attendance scope는 v2에서 원문 파서와 함께 제공 예정(현재 stub).")
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
    if scope != "attendance":
        flags = _collect_data_quality_flags(data)
        # 각주 마커 플래그가 있으면 원문 폴백으로 해소 시도(마커 뜬 공시만 1회씩 fetch·캐시).
        # 정형 API가 못 주는 각주 본문을 사업보고서 원문에서 복구 — 260709 코붕이 제안(url 기반).
        if resolve_footnotes and any(f.get("kind") == "footnote_marker_unresolved" for f in flags):
            await _resolve_footnotes(client, flags)
        data["data_quality_flags"] = flags

    data["usage"] = build_usage(client.api_call_snapshot() - calls_start)
    # 성능측정용 타이머(사용자 요청): scope별 개별 소요 + 전체 wall-clock(ms). 병렬 실행이라
    # 전체는 개별 합보다 작다(가장 느린 scope에 수렴) — 순차 대비 얼마나 절약했는지도 볼 수 있음.
    data["timing"] = {
        "per_scope_ms": timings,
        "total_wall_ms": round((time.perf_counter() - t_start) * 1000, 1),
        "scope_sum_ms": round(sum(v for k, v in timings.items() if k != "resolve"), 1),
    }
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
                          "detail": f"이름기반 사외이사 신규선임 diff({ours})와 DART 공식 집계({official})가 "
                                    f"{abs(ours - official)} 차이 — 재선임/정의차 가능하나 roster diff 신뢰도 낮음(공식값 우선)."})

    return flags


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
        "note": "소진율·인당보수는 기계적 사실. '적절성'은 동종업계·규모 대비 판단이 필요해 "
                "이 tool은 수치와 변동·flag만 제공(가치판단 안 함).",
    }
