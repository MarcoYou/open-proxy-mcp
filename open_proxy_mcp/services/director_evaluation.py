"""director_evaluation — 이사/감사/감사위원 후보 평가 모듈.

3축: 독립성 / 충실성 / 결격사유.
**✅ 가능 항목만 메모에 표시**. 자동 검증 안 된 항목 (hard-fail)은 침묵.

매핑 분류 (모든 항목 주석):
- success: 정형 필드 직접 매핑
- soft-fail: raw text를 LLM에게 노출 (정규식/매칭 실패 시)
- hard-fail: 데이터 자체 미존재 — 메모/코드 모두 침묵 (코붕이 명시 지시)

Phase 1: 독립성 + 결격사유 (기본 매핑) + 후보 추출.
Phase 2 (다음 iteration): 충실성 — 이사 회계 risk 이력 검증 (과거 회사 × 재직 기간 × 회계 risk).
"""

from __future__ import annotations

import asyncio
import re
from collections import Counter
from datetime import date
from typing import Any

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.services.contracts import (
    AnalysisStatus,
    EvidenceRef,
    SourceType,
    ToolEnvelope,
    build_filing_meta,
    build_usage,
)
from open_proxy_mcp.services.shareholder_meeting_parser import parse_personnel_xml


# ── 후보 데이터 fetch (success/soft-fail 분류) ──

async def fetch_appointments(
    corp_code: str,
    year: int,
    meeting_type: str = "annual",
) -> tuple[list[dict[str, Any]], str | None, list[dict[str, Any]]]:
    """주총소집공고 검색 + 후보 추출.

    meeting_type:
    - "annual": 정기주총만 (본문 detect == "annual" 인 첫 공고)
    - "extraordinary": 임시주총만 (본문 detect == "extraordinary" 인 첫 공고)
    - "auto": 가장 최신 주총소집공고 (분류 무관)

    매핑:
    - rcept_no, rcept_dt, report_nm → success (정형)
    - 본문 personnel section → parse_personnel_xml로 success / soft-fail
    - meeting_type 매칭 실패 시 다음 공고 fallback (사용자 logic)

    return: (appointments, rcept_no, filings_meta)
    """
    from open_proxy_mcp.services.shareholder_meeting_parser import detect_meeting_type

    client = get_dart_client()
    # 검색 범위: auto 또는 extraordinary는 연중, annual은 1-5월
    # (본문 detect가 최종 판단이므로 search 범위는 넉넉히)
    if meeting_type == "annual":
        bgn_de = f"{year}0101"
        end_de = f"{year}0501"
    else:
        bgn_de = f"{year}0101"
        end_de = f"{year}1231"

    # report_nm 필터 — "주주총회소집공고" 포함 (정기/임시 구분은 본문 detect로)
    def _filter(items: list) -> list:
        return [i for i in items if "주주총회소집공고" in (i.get("report_nm") or "")]

    # 주주총회소집공고 ∈ E006(주주총회소집공고). 전 type(None) 순회 대신 E006으로 좁히면
    # 무관 공시 flood 없이 소집공고만 받아 page 1로 충분(차집합0 검증: 삼성·고려아연·SK·현대차).
    max_pages = 2
    notices: list = []
    accumulated: list = []  # 모든 페이지 누적 (가장 최신부터)
    for pg in range(1, max_pages + 1):
        try:
            data = await client.search_filings(
                corp_code=corp_code, bgn_de=bgn_de, end_de=end_de,
                pblntf_ty=None, pblntf_detail_ty="E006", page_no=pg, page_count=100,
            )
        except DartClientError as exc:
            if pg == 1:
                return [], None, [{"error": f"search_filings 실패: {exc.status} {exc}"}]
            break
        items = data.get("list", []) or []
        page_notices = _filter(items)
        accumulated.extend(page_notices)
        # total_count 적으면 더 이상 페이지 없음
        if pg * 100 >= int(data.get("total_count") or 0):
            break

    notices = accumulated
    if not notices:
        return [], None, [{"info": f"{year} {meeting_type} 주총소집공고 미발견 (page 1-{max_pages} 모두 시도)"}]

    # 본문 detect 기반 meeting_type 매칭 + 정정공고 처리.
    # 1) 시간 desc 순서대로 본문 fetch + detect_meeting_type
    # 2) meeting_type=="auto" → 첫 번째 채택
    #    meeting_type 명시 → detect 결과 매칭 시 채택, 아니면 다음 공고 fallback
    # 3) 매칭 후 parse 결과 빈 경우 (정정 본문 등) 다음 공고 fallback
    # 4) 최대 5개 notice 시도
    notice = notices[0]
    rcept_no = notice.get("rcept_no")
    last_text = ""
    last_meta: dict[str, Any] = {}
    appointments: list[dict[str, Any]] = []
    agenda_titles: list[str] = []
    detected_type = None
    matched = False
    skipped_for_type: list[str] = []

    for idx, candidate_notice in enumerate(notices[:5]):
        candidate_rcept = candidate_notice.get("rcept_no")
        try:
            doc = await client.get_document_cached(candidate_rcept)
        except Exception as exc:
            if idx == 0:
                return [], candidate_rcept, [{"error": f"get_document 실패: {exc}"}]
            continue

        text = doc.get("html") or doc.get("text") or ""
        if not text:
            continue

        # detect meeting_type from body
        candidate_detected = detect_meeting_type(text)

        # meeting_type 매칭 — auto면 모두 통과, 명시면 일치 필요
        type_ok = (meeting_type == "auto") or (candidate_detected == meeting_type)
        if not type_ok:
            skipped_for_type.append(f"{candidate_rcept}({candidate_detected})")
            continue

        parsed = parse_personnel_xml(text)
        candidate_appointments = parsed.get("appointments", []) or []
        try:
            from open_proxy_mcp.services.shareholder_meeting_parser import parse_agenda_xml
            agenda_items = parse_agenda_xml(text, html=text)
            candidate_agenda_titles = [a.get("title") for a in (agenda_items or []) if a.get("title")]
        except Exception:
            candidate_agenda_titles = []

        is_correction = candidate_notice.get("report_nm", "").startswith("[기재정정]")

        # 매칭된 첫 공고이거나, 결과 있으면 채택
        if not matched or candidate_appointments or candidate_agenda_titles:
            notice = candidate_notice
            rcept_no = candidate_rcept
            appointments = candidate_appointments
            agenda_titles = candidate_agenda_titles
            last_text = text
            last_meta = {"is_correction": is_correction}
            detected_type = candidate_detected
            matched = True
            if candidate_appointments or candidate_agenda_titles:
                break

    if not last_text:
        # meeting_type 매칭 실패 시 명시
        base_info = {
            "requested_meeting_type": meeting_type,
            "detected_meeting_type": None,
            "skipped_for_type_count": len(skipped_for_type),
            "skipped_for_type_sample": skipped_for_type[:3],
        }
        if skipped_for_type and meeting_type != "auto":
            return [], rcept_no, [{"info": f"{year} {meeting_type} 매칭 공고 없음 (본문 detect 기준)", **base_info}]
        return [], rcept_no, [{"error": "본문 비어 있음", **base_info}]

    return appointments, rcept_no, [{
        "rcept_no": rcept_no,
        "report_nm": notice.get("report_nm"),
        "agenda_titles": agenda_titles,
        "is_correction": last_meta.get("is_correction", False),
        "detected_meeting_type": detected_type,
        "requested_meeting_type": meeting_type,
        "fallback_attempts": min(len(notices), 5),
        "skipped_for_type_count": len(skipped_for_type),
    }]


# ── 독립성 평가 (모두 success — DART 정형 필드) ──

# 5년 룰: 같은 회사 사외이사 누적 5년+ → 독립성 의심
_FIVE_YEAR_KEYWORDS = ("재선임", "재임", "연임", "중임")

# "최근 2년 회사 직원" 매칭 키워드 (careerDetails content에서)
_RECENT_EMPLOYEE_KEYWORDS = ("재직", "근무", "임직원")


def _is_recent_employee(career_details: list[dict[str, Any]] | None, current_year: int) -> tuple[bool, str | None]:
    """careerDetails에서 "최근 2년 내 회사 직원" 여부 추정.

    매핑: success (정형 list) / soft-fail (period 형식 다양 — 정규식 실패 시 raw 노출)
    return: (matched, evidence_text or None)
    """
    if not career_details:
        return False, None
    for cd in career_details:
        period = (cd.get("period") or "").strip()
        content = (cd.get("content") or "").strip()
        if not any(kw in content for kw in _RECENT_EMPLOYEE_KEYWORDS):
            continue
        # period 정규식: "2024 ~ 2026", "2023.01 ~ 현재", "2022 ~"
        m = re.search(r"(\d{4})", period)
        if not m:
            continue
        start_year = int(m.group(1))
        end_year = current_year
        if "현재" in period or "재직" in content:
            end_year = current_year
        else:
            m2 = re.search(r"~\s*(\d{4})", period)
            if m2:
                end_year = int(m2.group(1))
        if end_year >= current_year - 2:
            return True, f"{period}: {content[:60]}"
    # 매칭 실패(soft-fail) — 경력 content가 '재직/근무' 키워드 없이 직책 형식('팀장·담당임원')이라
    # 정규식이 못 잡는다. 최근 연도순 경력 2개 raw를 evidence로 노출해 LLM이 '실제 2년 내 이
    # 회사 직원이었나'를 직접 판단하게 한다(docstring의 'soft-fail = raw 노출' 폴백을 실제 구현).
    # careerDetails 순서가 학력부터인 경우가 있어 period의 최대 연도순으로 정렬해 최근 경력 우선.
    def _max_year(c: dict) -> int:
        return max((int(y) for y in re.findall(r"\d{4}", c.get("period") or "")), default=0)

    def _is_education(c: dict) -> bool:
        return any(k in (c.get("content") or "") for k in ("학사", "석사", "박사", "대학교", "대학원", "학과", "졸업"))

    ranked = sorted(career_details, key=_max_year, reverse=True)
    # 직원 판단엔 학력이 무용 — 경력(비학력) 우선, 없으면 학력 fallback
    picks = [c for c in ranked if not _is_education(c)][:2] or ranked[:2]
    recent_raw = " / ".join(
        f"{(c.get('period') or '').strip()} {(c.get('content') or '').strip()}".strip()
        for c in picks
    ).strip()
    return False, (recent_raw[:100] or None)


def evaluate_independence(candidate: dict[str, Any], current_year: int) -> dict[str, Any]:
    """독립성 4 sub-factor 평가 (모두 success).

    return: {sub_factors: {key: {result, evidence}}, summary: str}
    """
    out: dict[str, Any] = {"sub_factors": {}}

    # ralph iter8 fix: 부정 표현 다양화 — "관계없음" / "해당없음" / "없습니다" 등
    # 이전엔 ("없음", "-", "")만 negation 인식 → "관계없음" → "related" 잘못 분류 → 모든 후보 indep concerns
    def _is_negation(s: str | None) -> bool:
        if s is None:
            return True
        s = s.strip()
        if s in ("", "-"):
            return True
        # "없" 포함 (관계없음 / 해당없음 / 거래 없음 / 없습니다 등) — soft pattern
        if "없" in s and len(s) <= 12:  # 짧은 부정구만 (긴 본문은 raw 노출)
            return True
        return False

    # 1. 최대주주/특수관계인 여부 → success (DART 정형 필드)
    msr = (candidate.get("majorShareholderRelation") or "").strip()
    is_independent_from_major = _is_negation(msr)
    out["sub_factors"]["major_shareholder_relation"] = {
        "result": "independent" if is_independent_from_major else "related",
        "raw": msr,
        "mapping": "success",
    }

    # 2. 회사와 거래 관계 (recent3yTransactions) → success
    rt = candidate.get("recent3yTransactions")
    has_transactions = not _is_negation(rt)
    out["sub_factors"]["recent_3y_transactions"] = {
        "result": "no_transactions" if not has_transactions else "transactions_exist",
        "raw": rt if rt else None,
        "mapping": "success",
    }

    # 3. 최근 2년 회사 직원 이력 → success/soft-fail
    employee_match, employee_ev = _is_recent_employee(
        candidate.get("careerDetails"), current_year
    )
    out["sub_factors"]["recent_2y_employee"] = {
        "result": "former_employee" if employee_match else "outsider",
        "evidence": employee_ev,  # soft-fail이어도 '재직/근무' 경력 raw 노출 (LLM 검증용)
        # mapping은 확정 매칭 여부 기준(evidence 유무 아님) — soft-fail에 raw 붙여도 동작 보존
        "mapping": "success" if employee_match or not candidate.get("careerDetails") else "soft-fail",
    }

    # 4. 5년 룰 (같은 회사 사외이사 5년+) — careerDetails에 회사 자체가 있으면 누적 체크
    # title의 action ("재선임"/"중임"/"연임") + 임기 정보로 보완. 여기는 단순 신호만.
    five_year_signal = any(
        kw in (cd.get("content", "") or "")
        for kw in _FIVE_YEAR_KEYWORDS
        for cd in (candidate.get("careerDetails") or [])
    )
    out["sub_factors"]["five_year_rule"] = {
        "result": "potential_long_tenure" if five_year_signal else "first_term_or_short",
        "mapping": "success",
    }

    # iter23: 5년 룰 위반 — 장기연임 (9-13년 audit case) → strong signal.
    # mainstream "장기연임 → 독립성 훼손 → AGAINST" (서진/심텍/고영/펩트론 등 6 case 일치)
    # five_year_signal은 careerDetails에 "재선임/재임/연임/중임" 키워드 발견 시 True.

    # ralph iter18 + iter27: indep summary 약화 — major_shareholder_relation 단독은 약한 신호.
    # iter27 추가: 한자/한글 친족 키워드 — 최대주주의 자녀/배우자/형제 등은 strong relation
    strong_flags = has_transactions or employee_match
    msr_strong_keywords = ("현직", "재직중", "현재")
    # 친족 표기 (한자/한글): 子(자) / 女(녀) / 父(부) / 母(모) / 兄(형) / 弟(제) / 姉/姊 / 妹 / 妻 / 夫
    # 한글: 자녀/배우자/형제/자매/처/남편/딸/아들/모친/부친
    msr_kinship_keywords = ("子", "女", "父", "母", "兄", "弟", "姉", "姊", "妹", "妻", "夫",
                            "자녀", "배우자", "형제", "자매", "처(妻)", "남편", "딸", "아들", "모친", "부친", "친족")
    msr_now = (msr or "") and (
        any(k in msr for k in msr_strong_keywords)
        or any(k in msr for k in msr_kinship_keywords)
    )
    # 장기연임(5년 룰/§34조5항7호)은 사외이사·감사 도메인 — 사내이사엔 키워드가 있어도 적용 안 함
    # (260710: 사내이사에 long_tenure_concerns 표시되던 cosmetic 잔재 제거. 결정경로는 원래 무영향).
    _role = candidate.get("roleType") or ""
    _is_oversight = any(k in _role for k in ("사외", "감사", "독립"))
    if five_year_signal and _is_oversight:
        # 장기연임은 audit/사외이사 모두 strong concerns
        out["summary"] = "long_tenure_concerns"
    elif strong_flags or (not is_independent_from_major and msr_now):
        out["summary"] = "concerns"
    elif not is_independent_from_major:
        out["summary"] = "weak_concerns"
    else:
        out["summary"] = "independent"
    return out


# ── 결격사유 평가 (✅ 가능 항목만: 나이 + eligibility 필드) ──

def evaluate_disqualification(candidate: dict[str, Any], current_year: int) -> dict[str, Any]:
    """결격사유 — ✅ 가능 항목만.

    return: {sub_factors: {...}, summary: str}
    """
    out: dict[str, Any] = {"sub_factors": {}}

    # 1. 미성년 체크 → success (birthDate 정형)
    # iter22 fix: birth_date format 다양 (1970-05-04 / 70.05.04 / 5378-05-04 잘못된 데이터 등).
    # 1900-현재 범위만 허용 — 음수 나이 방지 (현대오토에버 -5378세 같은 case red_flag 잘못 분류).
    bd = (candidate.get("birthDate") or "").strip()
    age = None
    if bd:
        m = re.search(r"(\d{4})", bd)
        if m:
            year = int(m.group(1))
            if 1900 <= year <= current_year:
                age = current_year - year
    is_minor = age is not None and 0 < age < 19
    out["sub_factors"]["age"] = {
        "result": "minor" if is_minor else "adult",
        "age": age,
        "mapping": "success",
    }

    # 2. eligibility 필드 (taxDelinquency / insolventMgmt / legalDisqualification) → success
    # ralph iter14: 한국 회계공시 negation 키워드 다양 — "부"(단독) / "미해당" / "해당없음" 추가.
    # 이전엔 "없음" / "충족" / "해당사항없음"만 → "부" / "미해당" 표기 회사 모두 잘못 red_flag 분류.
    elig = candidate.get("eligibility") or {}
    elig_flags: dict[str, str | None] = {}
    has_red = False
    NEGATION_TOKENS = ("없음", "없다", "없습니다", "충족", "해당사항없음", "해당없음", "미해당", "비해당", "해당안", "N", "n")
    for k in ("taxDelinquency", "insolventMgmt", "legalDisqualification"):
        v = elig.get(k)
        if not v or v in ("-", None):
            elig_flags[k] = None
            continue
        v_norm = str(v).replace(" ", "").strip()
        # 단독 "부" / "무" / "X" 단답형
        if v_norm in ("부", "무", "X", "x", "아니오", "아니요"):
            elig_flags[k] = None
            continue
        # 부정 키워드 substring
        if any(kw in v_norm for kw in NEGATION_TOKENS):
            elig_flags[k] = None
        else:
            has_red = True
            elig_flags[k] = v
    out["sub_factors"]["eligibility"] = {
        "result": "red_flag" if has_red else "clean",
        "raw_flags": {k: v for k, v in elig_flags.items() if v},
        "mapping": "success",
    }

    # ⚠️ hard-fail (메모에 안 적음): 형사 처벌 / 파산 / 임원 자격 박탈 / 사적 관계
    # → 코드/메모에서 침묵 (코붕이 지시)

    out["summary"] = "red_flag" if (is_minor or has_red) else "clean"
    return out


# ── 충실성 — 이사 회계 risk 이력 검증 (과거 회사 × 재직 기간 × 회계 risk overlap) ──

# 경력 원문 맨 앞의 회사·기관명 — 「(주)카카오 대표이사」 → 「(주)카카오」.
# 법인 접두/접미(㈜·(주)·주식회사)는 이름의 일부라 **떼지 않는다**. 떼면 조회가 어긋난다.
# 「現)」·「전)」 같은 시점 마커와 「-」·「·」 불릿만 앞에서 걷어낸다.
_CAREER_LEAD_TRIM = re.compile(r"^[\s\-·•]*(?:現|현|前|전)\s*[\)\]]?\s*")


# 「잘 쪼갰나」를 의심할 신호 — 하나라도 걸리면 원문 두 칸을 함께 싣는다(폴백 체인).
# 짝이 맞아 보여도 잘 쪼갰다는 보장이 없다. 실측 1,211명:
#   A 기간 미대응 3.6% · B blob(한 항목에 회사 2개+) 9.1% · C 절단 흔적 1.4% → 하나라도 13.0%
_CORP_MARK = re.compile(r"㈜|\(주\)|주식회사")
# 「㈜영풍 사장」처럼 법인표기로 시작하는 건 정상이다 — 진짜 절단은 **닫는 괄호로 시작**하거나
# **여는 괄호로 끝나는** 것이다(「㈜) 수석부장」·「…ㆍ(」). 처음엔 「㈜ 시작」을 절단으로 세어
# 13.0%가 나왔는데 실제로는 1.4%였다(측정 오류).
_CAREER_CUT = re.compile(r"^\s*[)\]）]|^\s*㈜\s*\)|[(\[（]\s*$|\(\s*구,?\s*$")


def career_split_doubt(candidate: dict[str, Any]) -> list[str]:
    """경력 쪼개기를 의심할 근거 목록(비면 의심 없음)."""
    det = [d for d in (candidate.get("careerDetails") or []) if (d.get("content") or "").strip()]
    if not det:
        return []
    out: list[str] = []
    pers = {(d.get("period") or "").strip() for d in det}
    if len(det) >= 2 and len(pers) == 1 and next(iter(pers)):
        out.append("기간이 항목별로 갈리지 않음")
    if any(len(_CORP_MARK.findall(d.get("content") or "")) >= 2 for d in det):
        out.append("한 항목에 회사가 여럿(뭉침)")
    if any(_CAREER_CUT.search(d.get("content") or "") for d in det):
        out.append("항목이 괄호 중간에서 잘림")
    return out


def _career_period_unpaired(candidate: dict[str, Any]) -> str | None:
    """기간이 항목별로 갈리지 않았으면 원문 기간 셀을 돌려준다(아니면 None).

    소집공고 후보표에서 기간·내용 셀에 줄 구분(`<p>`)이 없는 서식이 있다. 그때 파서가
    기간 하나를 전 항목에 복사해 **존재하지 않는 기간**을 만든다 — 카카오 정신아는 원문에
    시점이 8개(학력 3 + 경력 5) 있는데 「1997 ~ 현재」 하나만 6번 반복됐다.
    실측 904명 중 43명(4.8%). 짝을 못 지었으면 짓지 말고 원문 두 칸을 그대로 보여준다.
    (기간이 전부 같은 정상 케이스 — 동시 겸직 3건 등 — 도 항목별 반복은 군더더기라 같이 묶인다.)
    """
    det = [d for d in (candidate.get("careerDetails") or []) if (d.get("content") or "").strip()]
    if len(det) < 2:
        return None
    pers = {(d.get("period") or "").strip() for d in det}
    if len(pers) != 1 or not next(iter(pers)):
        return None                       # 기간이 항목별로 갈려 있으면 정상 — 손대지 않는다
    raw = (candidate.get("careerPeriodRaw") or "").strip()
    return raw or next(iter(pers))


def _career_content_raw(candidate: dict[str, Any]) -> str | None:
    """원문 내용 셀 — 항목 분할이 회사명을 자르므로(「㈜(구, 엔에이치엔」) 통째로 쓴다."""
    return (candidate.get("careerContentRaw") or "").strip() or None


def _leading_corp_name(content: str) -> str:
    """경력 항목 원문 → 맨 앞 회사·기관명 후보(없으면 빈 문자열).

    쪼갠 company 필드로 하던 때는 「(주)카카오」가 통째로 드롭되고(실측 17.1%)
    「대한변호사협회」가 「대한」으로 잘려 나갔다. 원문 앞머리를 그대로 쓴다.
    """
    s = _CAREER_LEAD_TRIM.sub("", (content or "").strip())
    if not s:
        return ""
    # 첫 공백까지가 이름 — 「(주) 카카오」처럼 법인표기 뒤에 공백이 오면 한 토큰 더 붙인다.
    parts = s.split()
    name = parts[0]
    if re.fullmatch(r"[\(（]?주[\)）]?|㈜|주식회사", name) and len(parts) > 1:
        name = f"{name}{parts[1]}"
    name = name.strip("·,，")
    return name if len(name) >= 2 else ""


# 등기이사(이사회 구성원) 직위 — 성과 귀속은 이 기간에만 한다.
# ⚠️ 이건 **추정**이다. 소집공고 경력란은 등기 여부를 적을 의무가 없고(상법 §542조의4② +
#    시행령 §31③은 성명·약력·추천인·최대주주 관계·거래내역·결격사유만 요구), 실측상
#    경력 항목 7,617개 중 「등기」를 명시한 것은 15개(0.20%)뿐이다. 확정 근거는 사업보고서
#    임원현황(rgist_exctv_at) — `apply_roster_board_tenure`가 그 값으로 이 추정을 덮는다.
#    정형 대조 결과 이 추정의 일치율은 43%(30사·77명)라 단독 사용은 위험하다.
_BOARD_ROLE_RE = re.compile(
    r"대표이사|사내이사|사외이사|기타비상무이사|비상무이사|감사위원|이사회\s*의장"
    r"|상무이사|전무이사|부사장이사"
)
# 「감사」 단독은 등기 대상이나 이사가 아니다(상법 §409·§412 — 이사회 구성원 아님).
# 감사원·감사실·감사본부·감사팀·감사법인은 조직명이라 등기와 무관하다(실측 오탐 16%).
# 숫자는 lookahead **안**에 둔다 — 밖에 두면 「감사2팀」에서 역행 추적이 숫자를 건너뛰어
# 「2」를 보고 통과시킨다(조직명이 등기 감사로 오탐).
# 앞의 한글은 막되 **상근·비상근·상임·비상임은 예외** — 「상근감사」는 상법 §409 의 감사 직위인데
# 한글 lookbehind 가 「근」에 걸려 통째로 막혔다(260730: 동원시스템즈 오종환에서 드러남).
_BOARD_AUDIT_RE = re.compile(
    r"(?:(?<=상근)|(?<=비상근)|(?<=상임)|(?<=비상임)|(?<![가-힣]))"
    r"감사(?!\s*\d*\s*(?:보고|결과|의견|인|원|실|반|팀|본부|법인|부문|위원회\s*운영))")
# ↑ 「이사장」(학교법인·재단·공단)과 「CEO」는 뺐다. 상법상 등기 대상은 §317②8호가 정한
#   사내·사외·기타비상무이사·감사·집행임원이며 CEO·이사장은 거기 없다. 대표이사는 별도로 잡힌다.


def _is_board_role(text: str) -> bool:
    """경력 항목이 등기이사 재직으로 **보이는가**(집행임원 제외) — 확정 아님, 정형 데이터로 덮인다."""
    if not text:
        return False
    return bool(_BOARD_ROLE_RE.search(text) or _BOARD_AUDIT_RE.search(text))


def _parse_career_period(period: str) -> tuple[int | None, int | None]:
    """careerDetails.period → (start_year, end_year). "현재" → None (current).

    매핑: success (정규식 매칭) / soft-fail (포맷 다른 케이스)
    """
    if not period:
        return None, None
    period = period.strip()
    # "2013 ~ 현재" / "2013.01 ~ 2024.03" / "2013-2024"
    m = re.match(r"(\d{4})", period)
    if not m:
        return None, None
    start = int(m.group(1))
    if "현재" in period:
        return start, None
    m2 = re.search(r"~\s*(\d{4})", period)
    if m2:
        return start, int(m2.group(1))
    m3 = re.search(r"-\s*(\d{4})", period)
    if m3:
        return start, int(m3.group(1))
    return start, None


# 이사 회계 risk 회사명 alias — 약칭 → DART 정식명. 매핑 실패 시 fallback 시도.
_MARCO_ALIASES = {
    "KT": "케이티",
    "kt": "케이티",
    "POSCO": "포스코홀딩스",
    "POSCO홀딩스": "포스코홀딩스",
    "포스코": "포스코홀딩스",
    "SK": "에스케이",
    "LG": "엘지",
    "GS": "지에스",
    "CJ": "씨제이",
    "KT&G": "케이티앤지",
    "BNK": "BNK금융지주",
    "DGB": "iM금융지주",
    "JB": "JB금융지주",
    "KB": "KB금융",
    "NH": "농협금융지주",
    "BKK": "케이비국민카드",  # 가능
    "삼전": "삼성전자",
    "현차": "현대자동차",
    "현대차": "현대자동차",
    "셀트리온헬스케어": "셀트리온",
    "카뱅": "카카오뱅크",
}


def _candidate_corp_names(corp_name: str) -> list[str]:
    """원본 + alias + 변형 candidates 생성 (lookup 시도용)."""
    out: list[str] = [corp_name]
    if corp_name in _MARCO_ALIASES:
        out.append(_MARCO_ALIASES[corp_name])
    # 영문 대문자 → 한글 변환 시도
    for k, v in _MARCO_ALIASES.items():
        if k in corp_name and v not in out:
            # 회사명에 alias key가 substring 포함 (예: "KT 사외이사")
            out.append(corp_name.replace(k, v))
    # 공백/특수문자 제거 변형
    cleaned = re.sub(r"[\s\(\)·,]+", "", corp_name)
    if cleaned and cleaned not in out:
        out.append(cleaned)
    return out


async def _resolve_audit_history_corp(corp_name: str) -> dict | None:
    """corp_name → DART corp_code 매칭 (multi-alias fallback)."""
    client = get_dart_client()
    for cand in _candidate_corp_names(corp_name):
        if not cand or len(cand) < 2:
            continue
        try:
            match = await client.lookup_corp_code(cand)
            if match and match.get("stock_code"):
                return match
        except Exception:
            continue
    return None


def _periods_overlap(p_start: int, p_end: int | None, risk_year: int) -> bool:
    """재직 기간 (p_start ~ p_end) 와 risk 발생 연도 partial overlap 체크.

    p_end=None → 현재까지 재직.
    """
    actual_end = p_end if p_end is not None else 2026
    return p_start <= risk_year <= actual_end


async def _check_audit_history_overlap(
    corp_name: str,
    period_start: int | None,
    period_end: int | None,
) -> dict[str, Any] | None:
    """과거 회사 cross-check (4 risk type, 재직 기간 partial overlap, 병렬).

    Risk 유형 (코붕이 지시):
    1. audit_opinion 적정 외 (한정/부적정/거절)
    2. capital_impairment_full (완전 자본잠식)
    3. 적자전환 후 적자지속/악화 (loss_conversion → continued_loss + 악화)
    4. 레버리지 가중 (debt 30%+ 증가) → 후 실적 악화

    return: red_flag dict / None.
    매핑: success (corp_code lookup OK) / soft-fail (alias 매칭 실패 시 None)
    """
    if not corp_name or not period_start:
        return None

    match = await _resolve_audit_history_corp(corp_name)
    if not match:
        return None  # soft-fail (코드 침묵, 메모에서 별도 raw 노출 — 호출자가 처리)
    past_corp_code = match["corp_code"]
    actual_corp_name = match.get("corp_name", corp_name)
    end_year = period_end if period_end is not None else 2026

    # 재직 기간 ∩ [2020, 2025] (DART 데이터 가용 윈도우)
    scan_start = max(period_start, 2020)
    scan_end = min(end_year, 2025)
    if scan_end < scan_start:
        return None

    # 4 risk 병렬 호출 — yoy scope (loss/debt) + audit_opinion + capital(summary)
    from open_proxy_mcp.services.financial_metrics import _safe_fetch_audit, _fetch_year_metrics

    async def fetch_year(y):
        # audit_opinion + summary metrics 동시 호출
        try:
            audit_rows, _ = await _safe_fetch_audit(past_corp_code, y)
        except Exception:
            audit_rows = []
        try:
            metrics, _ws, _ev = await _fetch_year_metrics(past_corp_code, y, "CFS", include_prev=True)
        except Exception:
            metrics = {}
        return y, audit_rows, metrics

    years = list(range(scan_start, scan_end + 1))
    results = await asyncio.gather(*[fetch_year(y) for y in years], return_exceptions=False)

    red_flags: list[dict[str, Any]] = []
    metrics_by_year: dict[int, dict[str, Any]] = {}
    for y, audit_rows, metrics in results:
        metrics_by_year[y] = metrics
        # 1. audit_opinion
        if audit_rows:
            r = audit_rows[0]
            op = (r.get("adt_opinion") or "").strip()
            if op and "적정" not in op:
                red_flags.append({
                    "type": "non_clean_audit_opinion",
                    "year": y, "opinion": op,
                    "company": actual_corp_name, "rcept_no": r.get("rcept_no"),
                })
        # 2. capital_impairment_full
        cap_status = (metrics or {}).get("capital_impairment_status")
        if cap_status == "full":
            red_flags.append({
                "type": "capital_impairment_full",
                "year": y, "ratio_pct": metrics.get("capital_impairment_ratio_pct"),
                "company": actual_corp_name,
            })

    # 3. 적자전환 후 적자지속/악화 — 재직 기간 안 연속 적자 + net_income 악화 체크
    sorted_years = sorted(metrics_by_year.keys())
    for i, y in enumerate(sorted_years[:-1]):
        curr = metrics_by_year.get(y) or {}
        nxt = metrics_by_year.get(sorted_years[i + 1]) or {}
        ni_curr = curr.get("net_income_krw")
        ni_nxt = nxt.get("net_income_krw")
        if ni_curr is None or ni_nxt is None:
            continue
        # 적자전환 후 (curr 적자) + (nxt 적자 또는 ni 악화)
        if ni_curr < 0 and ni_nxt < 0 and ni_nxt < ni_curr:
            # 이미 같은 type 한 번이면 skip (회사당 1건)
            if any(rf["type"] == "loss_continued_worsening" for rf in red_flags):
                continue
            red_flags.append({
                "type": "loss_continued_worsening",
                "year_from": y, "year_to": sorted_years[i + 1],
                "ni_from": ni_curr, "ni_to": ni_nxt,
                "company": actual_corp_name,
            })

    # 4. 레버리지 가중 (debt 30%+) 후 실적 악화 — 재직 기간 내 30%+ debt 증가 + 다음 연도 영업이익 악화
    for i, y in enumerate(sorted_years[:-1]):
        curr = metrics_by_year.get(y) or {}
        nxt = metrics_by_year.get(sorted_years[i + 1]) or {}
        debt_curr = curr.get("total_liabilities_krw")
        debt_nxt = nxt.get("total_liabilities_krw")
        op_curr = curr.get("operating_profit_krw")
        op_nxt = nxt.get("operating_profit_krw")
        if not all(v is not None for v in (debt_curr, debt_nxt, op_curr, op_nxt)):
            continue
        if debt_curr <= 0:
            continue
        debt_growth = (debt_nxt - debt_curr) / debt_curr
        if debt_growth >= 0.30 and op_nxt < op_curr:
            if any(rf["type"] == "leverage_surge_op_worsening" for rf in red_flags):
                continue
            red_flags.append({
                "type": "leverage_surge_op_worsening",
                "year_from": y, "year_to": sorted_years[i + 1],
                "debt_growth_pct": round(debt_growth * 100, 1),
                "op_from": op_curr, "op_to": op_nxt,
                "company": actual_corp_name,
            })

    # partial overlap 필터 — risk year가 재직 기간과 겹치는지
    overlapped = [
        rf for rf in red_flags
        if _periods_overlap(period_start, period_end, rf.get("year") or rf.get("year_to") or 0)
    ]

    if overlapped:
        return {
            "company": actual_corp_name,
            "alias_input": corp_name,
            "corp_code": past_corp_code,
            "tenure_start_year": period_start,
            "tenure_end_year": period_end,
            "red_flags": overlapped,
        }
    return None


async def evaluate_faithfulness(
    candidate: dict[str, Any],
    *,
    check_audit_history: bool = False,
    own_company_name: str = "",
) -> dict[str, Any]:
    """충실성 평가.

    Phase 1 기본:
    - dutyPlan / recommendationReason → soft-fail (raw 노출, LLM 자연어 판단)
    - mainJob / recommender / career_raw(경력 원문) → success
    - **concurrent_outside_directors** (Ralph 9) — 사외이사 한정, 본 회사 포함 카운트

    check_audit_history=True: 과거 회사 × 재직 기간 × 회계 risk overlap 자동 체크.
    이사 회계 risk 이력 검증는 추가 DART 호출 발생 (cost) — 옵션.
    """
    out: dict[str, Any] = {
        "duty_plan_raw": candidate.get("dutyPlan") or None,
        "recommendation_reason_raw": candidate.get("recommendationReason") or None,
        # 문면이 이 후보를 안 밝혀 구간 공통으로 붙은 것 — 확정 표기를 피하려고 전파
        "recommendation_reason_shared": candidate.get("recommendationReasonShared") or None,
        "main_job": candidate.get("mainJob"),
        "recommender": candidate.get("recommender"),
        # 기간이 항목별로 안 갈린 경우 원문 기간 셀 — 짝을 지어 보여주면 거짓이 된다
        "career_period_unpaired": _career_period_unpaired(candidate),
        "career_content_raw": _career_content_raw(candidate),
        # 쪼개기를 의심할 근거 — 있으면 렌더가 원문 두 칸을 함께 싣는다
        "career_split_doubt": career_split_doubt(candidate) or None,
        # 원문 기간 셀 — 의심 유무와 무관하게 payload 에 남긴다(호출측 AI 가 언제든 대조)
        "career_period_raw": (candidate.get("careerPeriodRaw") or "").strip() or None,
        # 경력은 소집공고 표 원문(기간·내용) 그대로 싣는다. 쪼갠 결과는 쓰지 않는다 —
        # 회사/직위 분리가 후보 17%에서 깨져 「…공학부 부」/「교수」처럼 단어를 찢었고,
        # 분량은 원문의 2배였으며(후보당 168자 vs 83자), 매칭·부문매핑 어느 쪽도
        # 원문 대비 나은 결과를 낸 적이 없다(전수 비교 100% 동일 또는 원문 우세).
        "career_raw": [{"period": (d.get("period") or "").strip(),
                        "content": (d.get("content") or "").strip()}
                       for d in (candidate.get("careerDetails") or [])
                       if (d.get("content") or "").strip()],
    }

    # 사외이사 겸직 카운트 (사외/독립이사 한정)
    if _is_outside_director_role(candidate.get("roleType") or "") and own_company_name:
        co = count_outside_director_positions(candidate, own_company_name)
        if co["total"] >= 3:
            co_summary = "strong_concerns_concurrent"
        elif co["total"] >= 2:
            co_summary = "concerns_concurrent"
        elif co["total"] == 1:
            co_summary = "single_position"
        else:
            co_summary = "no_data"
        out["concurrent_outside_directors"] = {
            "total": co["total"],
            "in_career_count": co["in_career_count"],
            "own_in_career": co["own_in_career"],
            "signals": co["signals"],
            "summary": co_summary,
        }

    # 이사 회계 risk 이력 검증 — 과거 회사 × 재직 기간 cross-check
    audit_history_red_flags: list[dict[str, Any]] = []
    audit_history_status = "disabled"
    if check_audit_history:
        audit_history_status = "checked"
        # (corp_name, start, end) 튜플 list — 회사 + 기간 조합 모두 만들고 병렬 호출.
        # 회사명은 **경력 원문**에서 뽑는다. 쪼갠 company 필드를 쓰던 때는
        # ① `re.split(r"[,，\(]")` 가 「(주)카카오」처럼 `(` 로 시작하는 이름에서 빈 문자열을 내
        #    조회 자체를 건너뛰고(실측 7,452건 중 **17.1%** 가 이렇게 사라졌다),
        # ② 잘린 이름(「대한」·「금융」)을 DART 회사검색에 던져 유효 조회가 **27.1%** 뿐이었다.
        tasks_meta: list[tuple[str, int, int | None]] = []
        for cd in (candidate.get("careerDetails") or []):
            content = (cd.get("content") or "").strip()
            if not content:
                continue
            start, end = _parse_career_period(cd.get("period") or "")
            if start is None:
                continue
            corp_name_candidate = _leading_corp_name(content)
            if corp_name_candidate:
                tasks_meta.append((corp_name_candidate, start, end))

        # asyncio.gather로 N 회사 × 기간 동시 — 속도 핵심 (코붕이 5번 지시).
        if tasks_meta:
            overlaps = await asyncio.gather(*[
                _check_audit_history_overlap(n, s, e) for n, s, e in tasks_meta
            ], return_exceptions=False)
            audit_history_red_flags = [o for o in overlaps if o]

    out["audit_history_check"] = {
        "status": audit_history_status,
        "red_flags": audit_history_red_flags,
        "summary": "red_flag" if audit_history_red_flags else ("clean" if audit_history_status == "checked" else "not_checked"),
    }

    # 통합 summary
    co_summary = (out.get("concurrent_outside_directors") or {}).get("summary")
    if audit_history_red_flags:
        out["summary"] = "concerns"
    elif co_summary == "strong_concerns_concurrent":
        out["summary"] = "concerns"
    elif co_summary == "concerns_concurrent":
        out["summary"] = "weak_concerns"
    else:
        out["summary"] = "raw_disclosed" if audit_history_status != "checked" else "clean"
    return out


# 후방 호환 alias (Phase 1 코드 사용 중)
def evaluate_faithfulness_basic(candidate: dict[str, Any], own_company_name: str = "") -> dict[str, Any]:
    """동기 alias — 이사 회계 risk 이력 검증 비활성. check_audit_history 옵션 없는 호출처용."""
    out = {
        "duty_plan_raw": candidate.get("dutyPlan") or None,
        "recommendation_reason_raw": candidate.get("recommendationReason") or None,
        # 문면이 이 후보를 안 밝혀 구간 공통으로 붙은 것 — 확정 표기를 피하려고 전파
        "recommendation_reason_shared": candidate.get("recommendationReasonShared") or None,
        "main_job": candidate.get("mainJob"),
        "recommender": candidate.get("recommender"),
        # async 경로와 **같은 계약**을 유지한다 — 한쪽에만 넣으면 sync 소비자가 조용히 빈다.
        "career_raw": [{"period": (d.get("period") or "").strip(),
                        "content": (d.get("content") or "").strip()}
                       for d in (candidate.get("careerDetails") or [])
                       if (d.get("content") or "").strip()],
        "career_period_unpaired": _career_period_unpaired(candidate),
        "career_content_raw": _career_content_raw(candidate),
        # 쪼개기를 의심할 근거 — 있으면 렌더가 원문 두 칸을 함께 싣는다
        "career_split_doubt": career_split_doubt(candidate) or None,
        # 원문 기간 셀 — 의심 유무와 무관하게 payload 에 남긴다(호출측 AI 가 언제든 대조)
        "career_period_raw": (candidate.get("careerPeriodRaw") or "").strip() or None,
        "audit_history_check": {"status": "disabled", "red_flags": [], "summary": "not_checked"},
        "summary": "raw_disclosed",
    }
    # 사외이사 겸직 카운트 (Ralph 9)
    if _is_outside_director_role(candidate.get("roleType") or "") and own_company_name:
        co = count_outside_director_positions(candidate, own_company_name)
        if co["total"] >= 3:
            co_summary = "strong_concerns_concurrent"
        elif co["total"] >= 2:
            co_summary = "concerns_concurrent"
        elif co["total"] == 1:
            co_summary = "single_position"
        else:
            co_summary = "no_data"
        out["concurrent_outside_directors"] = {
            "total": co["total"],
            "in_career_count": co["in_career_count"],
            "own_in_career": co["own_in_career"],
            "signals": co["signals"],
            "summary": co_summary,
        }
        # summary 통합
        if co_summary == "strong_concerns_concurrent":
            out["summary"] = "concerns"
        elif co_summary == "concerns_concurrent":
            out["summary"] = "weak_concerns"
    return out


# ── 후보 평가 통합 ──

def detect_appointment_type(
    candidate: dict[str, Any],
    canonical_corp_name: str,
    current_year: int,
) -> dict[str, Any]:
    """신임/연임 자동 분류.

    - 'renewed' (연임): 경력 원문에 이 회사 항목 존재 + 시작 연도 < current_year
    - 'new' (신임): 이 회사 항목 없음 (외부 회사만)
    - 'ambiguous': 경력 원문이 비었거나 매칭 불가

    이 회사 매칭: 정규화된 회사명(괄호·(주)·주식회사 제거)이 경력 원문에 포함되는가.
    """
    # 경력 원문(기간·내용)을 그대로 본다. 쪼갠 그룹은 쓰지 않는다 —
    # 전수 비교(후보 2,552명) 결과 type·earliest_start·board_earliest_start·
    # outside_earliest_start·outside_ongoing·match_source 가 **100% 동일**했고,
    # 쪼개기는 「기타비상무이사」를 「기타비」+「상무이사」로 찢는 등 어휘를 훼손했다.
    details = candidate.get("careerDetails") or []
    if not details:
        return {"type": "ambiguous", "reason": "경력 원문 없음", "matched_entries": []}

    norm_name = _normalize_corp_name(canonical_corp_name)
    if not norm_name:
        return {"type": "ambiguous", "reason": "canonical_name 비어있음", "matched_entries": []}

    matched: list[dict[str, Any]] = []
    for cd in details:
        content = (cd.get("content") or "").strip()
        if not content or norm_name not in _normalize_corp_name(content):
            continue
        co = content
        items = [f"{(cd.get('period') or '').strip()} {content}".strip()]
        if items:
            # 시작 연도 추출 (가장 빠른 시작 연도) + 진행중(현재) 재직 여부
            earliest = None
            ongoing = False  # "~ 현재" (end=None) 항목이 있으면 현재 재직 중
            # 🔴 감독(oversight) service만 별도 집계 — 5년 룰/§34조5항7호는 '사외이사(감사위원 포함) 재직
            #   기간' 규정이라, 임직원(대표이사·사장·본부장·담당·센터장·그룹장 등) 재직을 세면 과대계상
            #   (신민철 담당장·박성수 대표이사·SK텔레콤 CIC장/CSO 사례로 검증). 사외이사/독립이사/감사위원/
            #   감사 item만 센다. 임직원 career는 이 키워드가 없어 자연히 제외 → over-count 제거.
            outside_earliest = None
            outside_ongoing = False
            board_earliest = None
            for it in items:
                s = str(it)
                start, end = _parse_career_period(s)
                if start is not None and (earliest is None or start < earliest):
                    earliest = start
                if start is not None and end is None:
                    ongoing = True
                if any(k in s for k in ("사외이사", "독립이사", "감사위원", "감사")):
                    if start is not None and (outside_earliest is None or start < outside_earliest):
                        outside_earliest = start
                    if start is not None and end is None:
                        outside_ongoing = True
                # 🔴 **등기이사** 재직만 — 성과 귀속은 이사회 구성원이었던 기간에만 해야 한다.
                #    260729: 김동춘(LG화학)은 2018~2025 가 비등기 집행임원(상무·전무·부사장·
                #    본부장)이고 2026 에 CEO 로 등기됐는데, 전체 최초연도 2018 을 잡아
                #    「재직 2018~2026(9년)」의 전사 ROE·부채비율을 개인에게 귀속했다.
                if _is_board_role(s):
                    if start is not None and (board_earliest is None or start < board_earliest):
                        board_earliest = start
            matched.append({
                "company_in_career": co,
                "earliest_start": earliest,
                "board_earliest_start": board_earliest,     # 🔴 등기이사 재직만 (성과 귀속용)
                "outside_earliest_start": outside_earliest,  # 🔴 사외이사 재직만 (장기연임 판정용)
                "outside_ongoing": outside_ongoing,
                "items_count": len(items),
                "ongoing": ongoing,  # 갭C: 종료된 과거 재직을 장기연임으로 오탐하지 않기 위함
            })

    if not matched:
        # 경력 원문에서 가장 오래된 연도 추출 (회사명 mismatch라도 어느 항목이든 시작 연도 사용)
        # — fallback case에서 performance tenure 추정용
        fallback_earliest = None
        for cd in details:
            for it in [f"{(cd.get('period') or '').strip()} {(cd.get('content') or '').strip()}".strip()]:
                start, _end = _parse_career_period(str(it))
                if start is not None and (fallback_earliest is None or start < fallback_earliest):
                    fallback_earliest = start

        # main_job fallback (1) — 정확한 회사명 prefix 매칭 (예: "삼성전자 DS부문 경영전략총괄")
        main_job = (candidate.get("mainJob") or "").strip()
        if main_job:
            norm_mj = _normalize_corp_name(main_job)
            if norm_mj and (norm_mj.startswith(norm_name) or norm_name in norm_mj.split()):
                return {
                    "type": "renewed",
                    "reason": f"career 매칭 X but main_job 회사명 prefix 발견: {main_job[:60]}",
                    "matched_entries": [],
                    "earliest_start": fallback_earliest,  # career의 가장 오래된 연도 (어느 회사든)
                    "match_source": "main_job_prefix",
                }

        # main_job fallback (2) — 사내이사인데 main_job 있음 → renewed 추정.
        # 사례: LS ELECTRIC 구자균 (canonical=엘에스일렉트릭 ↔ main_job=LS ELECTRIC 한글-영문 mismatch),
        #      신한지주 진옥동 (canonical=신한지주 ↔ main_job=신한금융지주 명칭 차이).
        # 외부 영입 신임 CEO는 드물어 false-renewed 위험 ~5-10% (수용).
        role_type = (candidate.get("roleType") or "")
        is_inside = "사내" in role_type and "사외" not in role_type
        if is_inside and main_job:
            return {
                "type": "renewed",
                "reason": f"사내이사 + main_job 있음 (한글-영문/약칭 mismatch 가능) — 사내이사 default renewed: {main_job[:60]}",
                "matched_entries": [],
                "earliest_start": fallback_earliest,  # career fallback (없으면 None)
                "match_source": "inside_director_default",
            }

        return {"type": "new", "reason": "이 회사 entry 없음 — 외부 경력만", "matched_entries": []}

    # 시작 연도 기준 — 과거 (current_year 이전) 시작이면 연임
    earliest_overall = min((m["earliest_start"] for m in matched if m["earliest_start"] is not None), default=None)
    # 성과 귀속 전용 — 등기이사였던 기간의 최초 연도(없으면 None → 성과 매트릭스 미실행)
    board_earliest_overall = min(
        (m["board_earliest_start"] for m in matched
         if isinstance(m, dict) and m.get("board_earliest_start") is not None),
        default=None)
    if earliest_overall is not None and earliest_overall < current_year:
        return {
            "type": "renewed",
            "reason": f"이 회사 재직 이력 {len(matched)}건 (최초 {earliest_overall}년)",
            "matched_entries": matched,
            "earliest_start": earliest_overall,
            "board_earliest_start": board_earliest_overall,
        }
    if earliest_overall is not None and earliest_overall >= current_year:
        return {
            "type": "new",
            "reason": f"이 회사 entry 있으나 모두 미래 시작 ({earliest_overall}~)",
            "matched_entries": matched,
            "earliest_start": earliest_overall,
            "board_earliest_start": board_earliest_overall,
        }
    # entry 있는데 시작 연도 미상 — ambiguous
    return {"type": "ambiguous", "reason": "이 회사 entry 있으나 시작 연도 미상", "matched_entries": matched}


def _normalize_corp_name(name: str) -> str:
    """회사명 정규화 — (주)/주식회사/괄호/공백 제거."""
    if not name:
        return ""
    s = name.strip()
    # 한국어 prefix 제거
    for pref in ("(주)", "주식회사 ", "주식회사"):
        if s.startswith(pref):
            s = s[len(pref):].strip()
    # 괄호 안 내용 제거 (예: "(전 LG상사)" → "")
    import re
    s = re.sub(r"\([^)]*\)", "", s)
    s = s.strip()
    return s


# ── 사외이사 겸직 카운트 (Ralph 9 — 260510) ──

_CONCURRENT_CURRENT_KW = ("현재", "현직", "재직")
_CONCURRENT_OUTSIDE_RE = re.compile(r'(?:사외|독립)\s*이사')
# careerDetails content 정규화 (회사명 substring 매칭용 — 공백/괄호/㈜ 제거)
_CONTENT_NORMALIZE_RE = re.compile(r'[\s㈜㈱()주식회사]')


def count_outside_director_positions(
    candidate: dict[str, Any],
    own_company_name: str,
) -> dict[str, Any]:
    """후보의 현직 사외이사 직책 총 갯수 (본 회사 자동 포함).

    careerDetails 중:
    - period에 '현재' / '현직' / '재직' 마커
    - content에 '사외이사' / '독립이사' 키워드
    - 본 회사명 매칭 자동 검출 → 본 회사 표기 X면 +1 (후보 본인 본 회사 보장)

    return: {total, in_career_count, own_in_career, signals}
    """
    career_details = candidate.get("careerDetails") or []
    own_norm = _CONTENT_NORMALIZE_RE.sub('', own_company_name or '').lower()
    in_career = 0
    own_in_career = False
    signals: list[str] = []
    for cd in career_details:
        period = cd.get("period", "") or ""
        content = cd.get("content", "") or ""
        if not any(k in period for k in _CONCURRENT_CURRENT_KW):
            continue
        matches = _CONCURRENT_OUTSIDE_RE.findall(content)
        if not matches:
            continue
        in_career += len(matches)
        signals.append(f"{period} | {content[:140]}")
        content_norm = _CONTENT_NORMALIZE_RE.sub('', content).lower()
        if own_norm and own_norm in content_norm:
            own_in_career = True
    total = in_career + (0 if own_in_career else 1)
    return {
        "total": total,
        "in_career_count": in_career,
        "own_in_career": own_in_career,
        "signals": signals[:3],
    }


def _is_outside_director_role(role_type: str) -> bool:
    """사외이사/독립이사 role 식별."""
    rt = role_type or ""
    return any(k in rt for k in ("사외", "독립"))


# 갭C (260710): 같은 회사 사외이사 재직 5년+ = 장기연임(독립성 훼손 우려).
_LONG_TENURE_YEARS = 5


def apply_tenure_long_tenure(ev: dict[str, Any], appointment_type: dict[str, Any] | None, current_year: int) -> None:
    """earliest_start(이 회사 재직 시작)로 재직연수 계산 → 5년+면 independence를 장기연임으로 승격.

    배경: `detect_appointment_type`이 '연임'으로 감지하며 `earliest_start`를 이미 계산해두는데,
    `evaluate_independence`의 five_year_rule은 careerDetails 키워드("재선임/연임/중임")만 봐서
    키워드가 없으면 놓쳤다 (계산-후-폐기). 날짜 기반으로 그 blind spot을 닫는다.

    안전장치:
    - `matched_entries`가 있을 때만 신뢰 — 이 회사 매칭 기반 earliest_start만 사용.
      main_job/사내이사 fallback의 earliest_start는 career-wide(다른 회사 연도)라 over-count 위험 → 제외.
    - **진행중(현재) 재직이 있을 때만** — 과거에 재직했다 떠난 뒤 신규 지명된 후보(예: 2001~2005
      재직 후 2026 신규)를 tenure 25년으로 오탐하지 않기 위함 (QA HIGH finding 260710).
    - 사외/감사 role에서만 적용 (장기연임 독립성 우려의 도메인). 사내이사는 회사 결정 영역.
    - earliest_start는 careerDetails 누락 시 under-count 쪽이라 false-positive 위험은 낮다(보수적).
    - 승격만(never downgrade). keyword rule이 이미 잡았으면 그대로.
    - 🔴 사외이사 service만 셈(260710): earliest_start(전체 경력)이 아니라 **사외이사 재직 item의 최초
      연도**(outside_earliest_start)로 tenure 계산. 5년 룰/§34조5항7호는 '사외이사 재직기간' 규정이라,
      임직원·대표이사 재직을 세면 과대계상(신민철 2012 담당장, 박성수 2015 대표이사 사례로 검증). 사외이사
      career item이 없으면(임직원만) Path A 미발화 → 과대계상 제거. under-count(false-negative)는 안전측.
    """
    if not isinstance(appointment_type, dict) or appointment_type.get("type") != "renewed":
        return
    matched_entries = appointment_type.get("matched_entries")
    if not matched_entries:  # 이 회사 매칭 기반만 신뢰
        return
    role = ev.get("role_type") or ""
    if not ("사외" in role or "감사" in role or "독립" in role or "outside" in role.lower()):
        return
    # 🔴 사외이사 재직 item만: outside_earliest_start(사외/독립이사 career) + outside_ongoing(진행중).
    #    임직원만 매칭된 후보(신민철·박성수)는 outside_* 가 None → 미발화(과대계상 제거).
    if not any(isinstance(m, dict) and m.get("outside_ongoing") for m in matched_entries):
        return
    outside_starts = [m.get("outside_earliest_start") for m in matched_entries
                      if isinstance(m, dict) and isinstance(m.get("outside_earliest_start"), int)]
    if not outside_starts:
        return
    es = min(outside_starts)
    tenure = current_year - es
    if tenure < _LONG_TENURE_YEARS:
        return
    indep = ev.get("independence")
    if not isinstance(indep, dict):
        return
    fyr = indep.setdefault("sub_factors", {}).setdefault("five_year_rule", {})
    fyr["result"] = "potential_long_tenure"
    fyr["basis"] = f"이 회사 재직 {tenure}년 (최초 {es}년 → {current_year}년)"
    fyr["source"] = "tenure_years"
    fyr["years"] = tenure  # 6년(§34⑤ 결격 경계) tiering용 — proxy_advise가 읽음
    # 장기연임은 summary 최상위 우선순위 (evaluate_independence의 five_year_signal과 동일 정책)
    indep["summary"] = "long_tenure_concerns"


# ── Purpose 1 (260710): roster(exctvSttus)를 파싱 prior로 — new→renewed 오분류 교정 ──
# 소집공고 career 텍스트 회사매칭이 실패하면 재선임 후보가 '신임'으로 오분류(baseline 19%)되어
# 5년 tenure 체크를 통째로 스킵한다. 이미 가진 정형 힌트(임원현황 재직 사실)로 분류를 바로잡는다.
#
# **힌트 정체성 원칙(사용자 260710)**: 정형 데이터는 소집공고를 override하는 ground-truth가 아니라
# 힌트다. 소집공고와 사업보고서는 발표 시점이 달라 그 사이 사임·이슈가 생길 수 있다. 그래서:
#   - 승격만(new→renewed), 절대 downgrade 안 함.
#   - roster 부재는 override 안 함(미등기·시차·EGM 선임 가능) — career-text 결과 유지.
#   - source="roster_prior" provenance 기록(투명·재검토 가능).
#   - tenure 값 자체(5년 룰 계산)는 career 기반 유지(H1: hffc_pd 기산점 재선임 리셋 위험).

def _hffc_to_years(hffc: str | None, ref_year: int) -> int | None:
    """hffc_pd(재직기간) → 재직 연수(floor). 파싱 실패 None.

    포맷: '2년 7개월'·'2021.03.17~현재'·'2021.03.17~2024.03.15'·'22개월'·'2021.3'.
    **H1 주의**: hffc는 재선임 시 기산점이 리셋돼 실제 tenure를 과소계상할 수 있다 →
    장기연임 감지의 **하한(floor)으로만** 안전(≥5년이면 진짜 ≥5년; false-positive 낮음).
    상한/정확값으로는 쓰지 말 것.
    """
    if not hffc:
        return None
    s = str(hffc).strip()
    if not s or s in ("-", "재직중", "현재", "미상"):
        return None
    # ① 명시적 '기간' 우선 — "N년"/"N.M년"(N은 1~2자리 정수부, 4자리 연도 아님).
    #    `(?<!\d)`로 '2023년'의 '23'을, 소수부(?:\.\d+)?로 '2.8년'의 '8'을 기간으로 오독하지 않게 막고
    #    앞 숫자가 없는 정수부만 floor로 취한다('2.8년'→2). 기간 표기가 곧 tenure(직접 진술)라
    #    "3년 (2021.03 선임)"처럼 연도가 함께 있어도 기간을 신뢰 → 날짜기반 over-count 방지(QA·전수 발견).
    m = re.search(r"(?<!\d)(\d{1,2})(?:\.\d+)?\s*년", s)
    if m:
        return int(m.group(1))
    # ② 개월만: "22개월" → //12
    m = re.search(r"(?<!\d)(\d{1,3})\s*개월", s)
    if m:
        return int(m.group(1)) // 12
    # ③ 소수 연수 "2.0" / "3.8" ('년' 없이) → floor.
    m = re.fullmatch(r"\s*(\d{1,2})\.\d+\s*", s)
    if m:
        return int(m.group(1))
    # ④ 4자리 날짜(연도) — '2023년 03월 17일 ~' / '2021.03.17~현재' 같은 **선임 시작일**. 시작연도 기준.
    yrs = [int(y) for y in re.findall(r"(?:19|20)\d{2}", s)]
    if yrs:
        start = yrs[0]
        end = yrs[1] if len(yrs) > 1 else ref_year
        return end - start if end >= start else None
    # ⑤ 2자리 연도 시작일 "'22.03.17~" / "24.03.14~" — '~'가 있고 YY.MM 꼴이면 날짜(20YY 가정).
    if "~" in s or "∼" in s:
        m = re.match(r"['\s]*(\d{2})[.\-/]\s*\d{1,2}", s)
        if m:
            start = 2000 + int(m.group(1))
            after = s.split("~", 1)[-1]
            em = re.search(r"(\d{2})[.\-/]", after)
            end = 2000 + int(em.group(1)) if em else ref_year
            return end - start if ref_year >= start and end >= start else None
    return None


def _core_name(name: str | None) -> str:
    """매칭용 핵심 이름 — 영문 병기·괄호·개행 제거. '도진명 (Jim Myong Doh)'→'도진명'."""
    if not name:
        return ""
    s = re.split(r"[(（]", str(name), maxsplit=1)[0]
    s = re.sub(r"\s+", "", s)
    return s


def _birth_ym_key(s: str | None) -> tuple[int | None, int | None]:
    """생년월 (year, month) 추출 — birthDate('1970-05-04')·birth_ym('1970년 05월') 공통."""
    if not s:
        return (None, None)
    t = str(s)
    ym = re.search(r"(19|20)\d{2}", t)
    if not ym:
        return (None, None)
    year = int(ym.group())
    rest = t[ym.end():]
    mm = re.search(r"(\d{1,2})", rest)
    month = int(mm.group()) if mm and 1 <= int(mm.group()) <= 12 else None
    return (year, month)


def build_roster_index(rows: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """exctvSttus 행 → {core_name: [{birth, director_type, tenure}]} 인덱스 (roster-prior용)."""
    idx: dict[str, list[dict[str, Any]]] = {}
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        core = _core_name(r.get("nm"))
        if not core:
            continue
        idx.setdefault(core, []).append({
            "birth": _birth_ym_key(r.get("birth_ym")),
            "director_type": (r.get("rgist_exctv_at") or "").strip(),
            "tenure": (r.get("hffc_pd") or "").strip(),
            "major_shareholder_relation": (r.get("mxmm_shrholdr_relate") or "").strip(),  # H2 rescue용
            # 260730: 상법 §382③1호(상무 종사) 검산용 — fte_at 은 실측 100% 채워진다
            "full_time": (r.get("fte_at") or "").strip(),
            "position": (r.get("ofcps") or "").strip(),
            "duty": re.sub(r"\s+", " ", (r.get("chrg_job") or "")).strip(),
            # 260730: 임기 만료일 — 등기 행 실측 96.3% 채움. 서식이 「YYYY년 MM월 DD일」 하나라
            # hffc_pd(재직기간)보다 해석 여지가 없다.
            "tenure_end": re.sub(r"\s+", " ", (r.get("tenure_end_on") or "")).strip(),
            # 260730: 주요경력 — 소집공고 세부경력이 결측일 때 메운다(실측 98.6% 채움).
            "main_career": re.sub(r"\s+", " ", (r.get("main_career") or "")).strip(),
        })
    return idx


def apply_roster_prior(ev: dict[str, Any], candidate: dict[str, Any], roster_index: dict[str, list[dict[str, Any]]]) -> None:
    """roster-prior로 new→renewed 오분류 교정. 승격만·부재는 유지·provenance 기록(힌트 정체성)."""
    apt = ev.get("appointment_type")
    if not isinstance(apt, dict) or apt.get("type") != "new":
        return
    if not roster_index:
        return
    core = _core_name(candidate.get("name"))
    matches = roster_index.get(core) or []
    if not matches:
        return  # roster 부재 → 힌트 없음, career-text 결과 유지 (override 금지)
    cbk = _birth_ym_key(candidate.get("birthDate"))
    board = []
    for m in matches:
        if "미등기" in (m.get("director_type") or ""):
            continue  # 엣지3: 미등기임원은 사외이사로선 '신임' — 승격 안 함
        mbk = m.get("birth") or (None, None)
        # 생년 대조: 양쪽 다 있으면 연도 일치 요구(동명이인 방지)
        if cbk[0] and mbk[0] and cbk[0] != mbk[0]:
            continue
        board.append(m)
    if not board:
        return
    if len(board) > 1 and not cbk[0]:
        return  # 다중매칭 + 생년 없음 → 동명이인 위험, 승격 보류
    m0 = board[0]
    apt["type"] = "renewed"
    apt["source"] = "roster_prior"
    apt["roster_hint"] = {
        "director_type": m0.get("director_type"),
        "tenure": m0.get("tenure"),
        "major_shareholder_relation": m0.get("major_shareholder_relation"),  # H2 rescue
    }
    apt["reason"] = ((apt.get("reason") or "") + " | roster(임원현황) 재직 확인 → 연임 재분류(힌트)").strip(" |")


# ── 260729: 등기 재직기간의 SSOT를 소집공고 경력 → 사업보고서 임원현황으로 ──
# 소집공고 경력란은 등기 여부를 적을 의무가 없어(상법 §542조의4②·시행령 §31③) 실측상 0.20%만
# 명시한다. 반면 사업보고서 임원현황은 「등기임원 여부」가 법정 기재사항이고, 캐시 302건 중 97%에
# 그 열이 있다. 소집공고 추정과 정형 데이터의 일치율은 43%(30사 77명) — 추정을 덮어야 한다.
#
# ⚠️ hffc_pd(재직기간)는 **등기 구분과 함께** 써야 한다. 미등기 행에도 연수가 찍히고(실측 640건,
#    삼성중공업 미등기 부사장 6년) 그것만 빼 쓰면 비등기 시절이 그대로 딸려 온다.
_ROSTER_BOARD_TYPES = ("사내이사", "사외이사", "기타비상무이사", "감사")

# roster 를 어느 보고서에서 가져오나 — (몇 해 전, reprt_code, 이름, 기준일 월).
# 신선한 순서로 시도하고 없으면 다음으로 내려간다.
#
# **시점**: 소집공고는 2~3월(캐시 479건 중앙값 3/13)인데 같은 해 사업보고서는 3월 하순
# (302건 중앙값 3/23)이라 주총 무렵엔 아직 없을 수 있다. 그래도 1순위로 둔다 —
# 「누가 언제 등기됐나」는 그때 이미 주총결과 공시로 공개된 사실이라 look-ahead 가 아니고
# (재무제표와 다르다: 그쪽은 감사 전이라 그때 알 수 없다), 없으면 빈 응답이 와서 다음으로 내려간다.
# 판단 기준은 **도구 실행 시점**이지 소집공고 시점이 아니다.
# 분기·반기보고서는 그보다 먼저 나온다(분기 종료 후 45일 → 3분기 11/14, 반기 8/14).
# 실측(10사×4종): 분기·반기에도 임원현황이 100% 실리고 등기구분·재직기간도 100% 채워졌다.
# 다만 분·반기는 기재 생략이 허용되는 항목이라 **소형사에서 빌 수 있다** — 그래서 사다리다.
# FY(N-2)는 최후이고, 그 rung 에서는 「등기 재직 없음」을 단정하지 않는다
# (실측 등기 190행 중 28행(14.7%)이 직전 1년 내 시작 — 그 사이 승진해 등기됐을 수 있다).
_ROSTER_SOURCES: tuple[tuple[int, str, str, int], ...] = (
    (1, "11011", "사업보고서", 12),
    (1, "11014", "3분기보고서", 9),
    (1, "11012", "반기보고서", 6),
    (2, "11011", "사업보고서", 12),
)


# 담당업무(chrg_job)에 적히는 위원회 — 실측 30사 등기 190명 중 담당업무 95.8% 채움,
# 감사위원회 39 · 내부거래 20 · ESG 20 · 사외이사후보추천 14 건 언급.
# 감사위원 후보가 직전 보고서에 이미 감사위원회 소속이면 연임이고, 아니면 신임이다.
_COMMITTEES = ("감사위원회", "사외이사후보추천위원회", "보수위원회", "내부거래위원회",
               "ESG위원회", "경영위원회", "리스크관리위원회", "지속가능경영위원회")


def _committees_in(duty: str) -> list[str]:
    """담당업무 문구 → 소속 위원회 목록(원문에 적힌 것만)."""
    d = re.sub(r"\s+", "", duty or "")
    return [c for c in _COMMITTEES if re.sub(r"\s+", "", c) in d]


def _roster_has_board_member(row: dict[str, Any]) -> bool:
    """임원현황 한 행이 **등기 이사회 구성원**인가 — rung 완전성 판정용."""
    k = (row.get("rgist_exctv_at") or "").strip()
    return "미등기" not in k and any(b in k for b in _ROSTER_BOARD_TYPES)


def _roster_board_start_year(
    raw: str, roster_year: int, ref_month: int = 12
) -> tuple[int | None, str | None]:
    """hffc_pd → (등기 재직 시작연도, 근거). 서식이 회사마다 달라 네 가지를 모두 본다.

    실측 분포(30사 861행): 날짜 58% · 연수만 20% · 「N년」 14% · 「N개월」 7%.
    날짜형은 시작일을 직접 적으므로 역산이 필요 없다 — 가장 정확하다.

    `ref_month` 는 그 보고서의 기준일 월(3분기=9, 사업=12). 개월 역산이 연도를 넘길 때 쓴다.

    ⚠️ **이 필드의 뜻은 회사마다 다르다.** 서식에 정의가 없다. 실측(30사 등기 190행)에서
    다수는 등기 재직기간으로 보이지만, 일부 회사는 **입사 근속연수**를 적는다 —
    역산 취임연령이 30세 미만인 행이 11개(5.8%), 최악은 19세 전무이사다. 그런 회사는 같은
    표의 미등기 상무·전무에도 「18년」·「19년」을 쓴다. 날짜형도 안전하지 않다
    (넥센타이어 「1990.05.28~」 = 그해 24세).
    그래서 값을 그대로 믿지 않고 `apply_roster_board_tenure` 에서 정합성 게이트를 건다.

    (앞서 「FY2024→FY2025 같은 사람 55명의 기산점 100% 동일」을 근거로 삼았는데 **판별력이 없다** —
     입사일도 고정점이라 두 가설이 같은 결과를 낸다. 그 테스트가 배제하는 건 「현 직위 기간」뿐이다.)
    """
    s = re.sub(r"\s+", " ", (raw or "")).strip()
    if not s or s in ("-", "—"):
        return None, None
    # ① "2019.01.01~" / "2023년 3월 13일~" / "2022년 05월 ~" — 시작일 직접 표기
    if m := re.match(r"(19|20)(\d{2})\s*[.\-/년]", s):
        return int(m.group(1) + m.group(2)), "재직 시작일"
    # ② "08.04.01 ~ 현재" — 2자리 연도. 미래가 되면 1900년대로 본다.
    if m := re.match(r"(\d{2})[.\-/]\d{1,2}[.\-/]\d{1,2}\s*~", s):
        y = 2000 + int(m.group(1))
        return (y if y <= roster_year else y - 100), "재직 시작일"
    # ③ "10개월" / "46개월" — 기준일에서 개월 수를 빼 시작 시점의 **연도**를 구한다.
    if m := re.match(r"(\d{1,3})\s*개월", s):
        months = ref_month - int(m.group(1))          # 기준일 월에서 뺀다(음수면 이전 해)
        return roster_year + (months - 1) // 12, "재직기간 역산"
    # ④ "14년" / "5년 1개월" / "2.1년" / 단위 없는 "4"(= 재직 연수)
    if m := re.match(r"(\d{1,2})(?:\.\d+)?\s*년?(?:\s*(\d{1,2})\s*개월)?$", s):
        yrs = int(m.group(1))
        return roster_year - yrs, "재직기간 역산"
    return None, None


# 등기이사 취임 하한 — 상법상 연령 제한은 없으나, 실측 190행 중 92.6%가 35세 이상이고
# 30세 미만 5.8%는 전부 근속연수 오기재로 보인다(19·23·24세 등). 게이트를 30세에 둔다.
_MIN_PLAUSIBLE_BOARD_AGE = 30
# 생년이 없어 취임연령 게이트를 못 돌릴 때의 대체 검산 — (임기만료 − 도출시작)이 이만큼을
# 넘으면 재직기간이 근속연수일 가능성이 크다. 창업 일가가 40년 등기인 경우도 있어 넉넉히 둔다.
_MAX_PLAUSIBLE_BOARD_SPAN = 40


def _year_of(raw: str) -> int | None:
    """「2026년 03월 20일」 → 2026."""
    m = re.match(r"\s*(19|20)(\d{2})", raw or "")
    return int(m.group(1) + m.group(2)) if m else None


def _birth_ym_str(row: dict[str, Any]) -> str | None:
    """roster 행의 birth 튜플 → "YYYY-MM" (후보 생년월일이 없을 때 대체 근거)."""
    b = row.get("birth") or (None, None)
    return f"{b[0]}-{b[1] or 1:02d}" if b[0] else None


def _age_at(birth: str | None, year: int) -> int | None:
    """생년(문자열) 과 연도 → 그 해 나이. 파싱 실패는 None(게이트 미적용)."""
    if not birth:
        return None
    m = re.search(r"(19|20)\d{2}", str(birth))
    return year - int(m.group()) if m else None


def apply_roster_board_tenure(
    ev: dict[str, Any], candidate: dict[str, Any],
    roster_index: dict[str, list[dict[str, Any]]], roster_year: int | None,
    *, ref_month: int = 12, report_label: str | None = None,
    can_confirm_unregistered: bool = True, meeting_year: int | None = None,
) -> None:
    """등기 재직 시작연도를 임원현황(정형)에서 확정 — 소집공고 경력 추정을 덮는다.

    `can_confirm_unregistered` 는 이 스냅샷으로 「등기 재직 없음」을 단정해도 되는가다.
    직전 사업연도 보고서면 True, FY(N-2) 같은 오래된 것이면 False(그 뒤 승진 가능).
    """
    apt = ev.get("appointment_type")
    if not isinstance(apt, dict) or not roster_index or roster_year is None:
        return
    matches = roster_index.get(_core_name(candidate.get("name"))) or []
    if not matches:
        return  # 임원현황에 없음 = 신임이거나 매칭 실패 → 추정을 덮지 않는다
    cbk = _birth_ym_key(candidate.get("birthDate"))
    # 같은 이름이라도 생년이 어긋나면 다른 사람이다 — 판정 대상에서 빼되,
    # 「전부 미등기」 확정은 **동일인으로 확인된 행**만 보고 내려야 한다(오확정 방지).
    same_person = [m for m in matches
                   if not (cbk[0] and (m.get("birth") or (None, None))[0]
                           and cbk[0] != (m.get("birth") or (None, None))[0])]
    board = [m for m in same_person
             if "미등기" not in (m.get("director_type") or "")
             and any(b in (m.get("director_type") or "") for b in _ROSTER_BOARD_TYPES)]
    prov: dict[str, Any] = {
        "source": report_label or "정기보고서 임원현황", "fiscal_year": roster_year,
        "notice_estimate": apt.get("board_earliest_start"),
    }
    if not board:
        if not same_person:
            return  # 동명이인만 있었다 = 이 사람 정보가 아니다 → 덮지 않는다
        types = [(m.get("director_type") or "").strip() for m in same_person]
        if not all("미등기" in t for t in types if t):
            # 「등기」·「집행임원」처럼 우리가 모르는 표기 → 모른다고 말한다(미등기라 단정 금지)
            prov["director_type"] = next((t for t in types if t), None)
            prov["note"] = "임원현황의 등기 구분 표기를 해석하지 못해 등기 여부를 확정하지 못했습니다"
            apt["board_tenure_source"] = prov
            return
        if not can_confirm_unregistered:
            # 오래된 스냅샷(FY N-2)이다 — 그 뒤 승진해 등기됐을 수 있어 확정하지 않는다.
            prov["director_type"] = "미등기"
            prov["note"] = "이 보고서 시점에는 미등기 — 이후 변동 가능(더 최신 임원현황 없음)"
            apt["board_tenure_source"] = prov
            return
        apt["board_earliest_start"] = None       # 등기 재직 없음이 **확정**된다
        prov["director_type"] = next((t for t in types if t), None)
        prov["note"] = "임원현황에 미등기임원으로만 기재 — 등기이사 재직 없음"
        apt["board_tenure_source"] = prov
        return
    if len(board) > 1 and not cbk[0]:
        return  # 다중매칭 + 생년 없음 → 동명이인 위험, 덮지 않는다
    # 성과 귀속은 「등기이사였던 기간 전체」다 — 여러 등기 행이 있으면 **가장 이른** 것을 쓴다.
    # (예: 기타비상무이사 2012 → 사내이사 2020 이면 2012 가 맞다. board[0] 은 순서 의존이라 틀린다.)
    cands = []
    for m in board:
        st, bs = _roster_board_start_year(m.get("tenure") or "", roster_year, ref_month)
        if st is not None:
            cands.append((st, bs, m))
    m0 = min(cands, key=lambda x: x[0])[2] if cands else board[0]
    prov["director_type"] = (m0.get("director_type") or "").strip() or None
    prov["tenure_raw"] = re.sub(r"\s+", " ", (m0.get("tenure") or "")).strip() or None
    # 임기 만료일 — 서식이 하나라(「YYYY년 MM월 DD일」) 재직기간보다 해석 여지가 없다.
    # 등기 행 실측 96.3% 채움. 읽는 쪽이 「이 사람 임기가 언제 끝나나」를 바로 볼 수 있다.
    # 정형 직위(ofcps) — 소집공고 「주된직업」은 자유기재인데 이건 정형이다.
    # 실측 53명 중 43명(81.1%)에서 주된직업에 안 나타나는 정보를 담고 있었다
    # (삼성전자 전영현: 주된직업에 없고 정형은 「부회장」).
    if (m0.get("position") or "").strip():
        prov["position"] = (m0.get("position") or "").strip()
        apt["roster_position"] = prov["position"]
    _comms = _committees_in(m0.get("duty") or "")
    if _comms:
        prov["committees"] = _comms
        apt["roster_committees"] = _comms
    _end = (m0.get("tenure_end") or "").strip()
    if _end:
        prov["term_end_on"] = _end
        apt["term_end_on"] = _end
        _ey = _year_of(_end)
        if _ey is not None:
            # 임기가 이번 회차(주총 연도 또는 그 직전 결산 후)에 만료 = 재선임 대상이다.
            # 실측 71명 중 37명(52.1%). 경력 텍스트 추론과 독립된 정형 근거다.
            _my = meeting_year if meeting_year is not None else roster_year + 1
            apt["term_expiring_this_meeting"] = _my - 1 <= _ey <= _my
            if apt.get("type") == "new" and apt["term_expiring_this_meeting"]:
                # 승격만 — 임기가 만료된다는 건 이미 재직 중이라는 뜻이다.
                apt["type"] = "renewed"
                apt["source"] = "roster_term_end"
                apt["reason"] = ((apt.get("reason") or "")
                                 + f" | 임원현황 임기 만료일 {_end} → 연임 재분류").strip(" |")
    if not cands:
        # 등기인 건 확정, 시작연도만 미상 — 추정을 남기되 출처를 밝힌다.
        prov["note"] = "등기 구분은 확정, 재직기간 표기를 읽지 못해 시작연도는 소집공고 경력 추정값"
        apt["board_tenure_source"] = prov
        return
    start, basis, _ = min(cands, key=lambda x: x[0])
    # ── 정합성 게이트 — 「재직기간」이 근속연수인 회사가 있다 ──────────────────────────
    # 실측(30사 등기 190행): 역산 시작연도 기준 취임연령이 **30세 미만인 행이 11개(5.8%)**,
    # 최악은 19세 전무이사다. 등기 재직기간이면 불가능하니 그 회사는 입사 근속을 적은 것이다
    # (같은 회사 미등기 상무·전무도 「18년」·「19년」을 쓴다). 날짜형도 안전하지 않다 —
    # 넥센타이어 김현석은 「1990.05.28~」인데 그해 24세다.
    # 근속을 등기 기간으로 쓰면 **이번에 고치려던 오류(비등기 기간 귀속)를 더 크게 재도입**한다.
    # 그래서 의심스러우면 시작연도를 버리고 등기 여부만 남긴다(보수적 = 성과 미평가).
    age = _age_at(candidate.get("birthDate") or _birth_ym_str(m0), start)
    if age is None:
        # 생년이 없어 취임연령을 못 본다 — 임기 만료일로 총 재직 기간을 대신 검산한다.
        _ey2 = _year_of((m0.get("tenure_end") or "").strip())
        if _ey2 is not None and _ey2 - start > _MAX_PLAUSIBLE_BOARD_SPAN:
            prov["note"] = (f"재직기간 환산 시작 {start}년 ~ 임기만료 {_ey2}년 = "
                            f"{_ey2 - start}년으로 등기 재직기간으로 보기 어렵습니다"
                            "(입사 근속연수로 기재한 것으로 보임) — 시작연도 미채택")
            prov["rejected_start"] = start
            apt["board_tenure_source"] = prov
            return
    if age is not None and age < _MIN_PLAUSIBLE_BOARD_AGE:
        prov["note"] = (f"재직기간을 시작연도로 환산하면 취임 당시 {age}세라 등기 재직기간으로 "
                        "보기 어렵습니다(입사 근속연수로 기재한 것으로 보임) — 시작연도 미채택")
        prov["rejected_start"] = start
        apt["board_tenure_source"] = prov
        return
    prov["basis"] = basis
    apt["board_earliest_start"] = start
    apt["board_tenure_source"] = prov


# ── 260730: 사외이사 후보의 「상무 종사」를 정형으로 검산 (상법 §382③1호) ──
# 상법 §382③1호는 「회사의 상무에 종사하는 이사·집행임원·감사 및 피용자 또는 **최근 2년 내**
# 그러했던 자」를 사외이사 결격으로 정한다. 그런데 소집공고는 결격사유를 「해당사항 없음」이라고
# 적을 뿐이고(실측 84.2%가 그렇게 적힌다), 우리는 그걸 경력 텍스트로만 검증했다.
# 임원현황에는 `fte_at`(상근 여부)가 **100%** 채워져 있다 — 직전 보고서에서 이 회사 상근
# 임원이었다면 그 사실을 검산 재료로 쓸 수 있다.
#
# ⚠️ **결격을 단정하지 않는다.** ① 스냅샷 하나로 「최근 2년」을 확정할 수 없고
# ② 동명이인 위험이 있고 ③ 그 사이 사임했을 수 있다. 「정형이 이렇게 말한다」까지만 낸다.
# 실측 30사 사외이사 후보 82명 중 4명(4.9%) 발동(태광산업 정인철 — 미등기/부사장/상근).
_FULLTIME_YES = "상근"


def names_titled_inside_director(appointments: list[dict[str, Any]]) -> set[str]:
    """공고의 **모든** 안건 제목에서 「사내이사 {이름}」으로 지목된 이름 집합.

    안건 제목이 여러 안건으로 뭉쳐 오면 뒤 안건의 직위가 앞 후보의 roleType 에 붙는다
    (실측 태광산업: 「사내이사 정인철 선임의 건 … 사외이사 김대근 선임의 건」 → 정인철이
    roleType='사외이사'). 제목에 명시된 지목이 roleType 보다 신뢰도가 높다.
    """
    out: set[str] = set()
    for ap in appointments or []:
        title = re.sub(r"\s+", "", ap.get("title") or "")
        for c in ap.get("candidates") or []:
            nm = re.sub(r"\s+", "", (c.get("name") or ""))
            if nm and f"사내이사{nm}" in title:
                out.add(nm)
    return out


def apply_roster_employee_check(
    ev: dict[str, Any], candidate: dict[str, Any],
    roster_index: dict[str, list[dict[str, Any]]], *, report_label: str | None = None,
) -> None:
    """사외이사 후보가 직전 정기보고서에서 이 회사 상근 임원이었나 — 승격만, override 금지."""
    indep = ev.get("independence")
    if not isinstance(indep, dict) or not roster_index:
        return
    if not any(k in (ev.get("role_type") or candidate.get("roleType") or "")
               for k in ("사외", "독립", "감사위원")):
        return
    # ⚠️ roleType 을 그대로 믿으면 안 된다. 안건 제목이 여러 안건으로 뭉쳐 오는 경우
    # (「사내이사 정인철 선임의 건 … 제3-3호 의안 : 사외이사 김대근 선임의 건」) 뒤 안건의
    # 직위가 앞 후보에게 붙는다 — 실측 태광산업에서 사내이사 후보 2명이 사외이사로 잡혀
    # §382③ 신호가 전부 오탐이 됐다. 제목이 이 사람을 사내이사로 지목하면 적용하지 않는다.
    # 한 후보가 여러 안건에 등장하므로 **공고 전체**의 제목을 봐야 한다 — 지금 안건 제목만
    # 보면 「이사 선임의 건」처럼 지목 없는 안건에서 오탐이 남는다(실측 태광산업 정인철).
    if ev.get("agenda_named_inside_director"):
        return
    cbk = _birth_ym_key(candidate.get("birthDate"))
    hits = []
    for m in roster_index.get(_core_name(candidate.get("name"))) or []:
        mbk = m.get("birth") or (None, None)
        if cbk[0] and mbk[0] and cbk[0] != mbk[0]:
            continue                      # 동명이인 배제
        if not (cbk[0] and mbk[0]):
            continue                      # 생년 대조 불가 → 신호로 쓰지 않는다(오탐 방지)
        dt = (m.get("director_type") or "").strip()
        if m.get("full_time") == _FULLTIME_YES and "사외" not in dt:
            hits.append(m)
    if not hits:
        return
    m0 = hits[0]
    sub = (indep.setdefault("sub_factors", {})
           .setdefault("recent_2y_employee", {"result": "outsider", "mapping": "success"}))
    sub["roster_cross_check"] = {
        "source": report_label or "정기보고서 임원현황",
        "director_type": (m0.get("director_type") or "").strip() or None,
        "position": (m0.get("position") or "").strip() or None,
        "full_time": _FULLTIME_YES,
        "duty": (m0.get("duty") or "").strip() or None,
        "note": ("직전 정기보고서 임원현황에 이 회사 **상근** 임원(사외이사 아님)으로 기재됨 "
                 "— 상법 §382조③1호(상무 종사자·최근 2년) 해당 여부 확인이 필요합니다. "
                 "소집공고의 결격사유 기재와 대조하세요."),
    }
    # 「외부인」으로 남겨두면 안 된다 — 정형이 반대로 말하고 있다. 단정은 피하고 검토로 올린다.
    if sub.get("result") == "outsider":
        sub["result"] = "roster_says_fulltime_insider"


def apply_roster_career_fallback(
    ev: dict[str, Any], candidate: dict[str, Any],
    roster_index: dict[str, list[dict[str, Any]]], *, report_label: str | None = None,
) -> None:
    """소집공고 세부경력이 결측이면 임원현황 「주요경력」으로 메운다 — 출처를 밝히고 덮지 않는다.

    실측: roster 대조 가능한 후보 53명 중 공고 경력 결측은 1명(1.9%)이지만 그게 하드케이스다
    (모나리자 Lok Shean Yang Peter — 외국인명). 빈도는 낮고 비용도 낮다.
    **공고에 경력이 있으면 손대지 않는다** — 시점(사업보고서 결산기준일)이 다르다.
    """
    faith = ev.get("faithfulness")
    if not isinstance(faith, dict) or faith.get("career_raw") or not roster_index:
        return
    cbk = _birth_ym_key(candidate.get("birthDate"))
    for m in roster_index.get(_core_name(candidate.get("name"))) or []:
        mbk = m.get("birth") or (None, None)
        if cbk[0] and mbk[0] and cbk[0] != mbk[0]:
            continue
        mc = (m.get("main_career") or "").strip()
        if mc and mc not in ("-", "—"):
            faith["career_from_roster"] = {
                "source": report_label or "정기보고서 임원현황",
                "main_career": mc,
                "note": "소집공고 세부경력이 없어 임원현황 주요경력으로 대신 보여줍니다 "
                        "— 기준일이 다릅니다(그 보고서 결산기준일).",
            }
            return


# ── Item2c/H2 (260710): roster 최대주주관계 rescue — 소집공고 결측 시 힌트로 채움 ──
# 소집공고 majorShareholderRelation이 비면 raw="" → 관계 텍스트 없이 generic 약신호만 뜬다.
# roster mxmm_shrholdr_relate(예: '계열회사임원')로 **채우기만**(fill-when-missing) 한다.
# 힌트 정체성: 소집공고에 값이 있으면 override 금지 / 승격만(concern clear 금지) / blank는 신호 아님.
_MSR_NOISE = ("", "-", "없음", "해당없음", "해당사항없음", "관계없음", "n/a", "na", "본인")
# 의미있는 관계(summary 승격 가치) — **최대주주와의 실제 관계만**. 친족 + '최대주주/특수관계' 명시.
# ⚠️ '계열회사 임원'·'임원'·'대표이사'·'발행회사' 등은 제외: 삼성전자 등은 독립적 사외이사 전원을
#    mxmm_shrholdr_relate='계열회사 임원'으로 채우는 **형식적 boilerplate**라 여기에 넣으면 전원 오탐
#    (ground-truth 검증: 허은녕·유명희·조혜경 등 독립 사외이사 전원 동일값). 이런 값은 provenance만 기록.
_MSR_MEANINGFUL = ("子", "女", "父", "母", "兄", "弟", "姉", "姊", "妹", "妻", "夫",
                   "자녀", "배우자", "형제", "자매", "남편", "딸", "아들", "모친", "부친", "친족",
                   "최대주주", "특수관계")


def apply_roster_msr_rescue(ev: dict[str, Any], candidate: dict[str, Any], roster_index: dict[str, list[dict[str, Any]]]) -> None:
    """소집공고 최대주주관계 결측 시 roster 값을 힌트로 채움 (사외/감사, 승격만, override 금지)."""
    indep = ev.get("independence")
    if not isinstance(indep, dict):
        return
    role = ev.get("role_type") or ""
    if not any(k in role for k in ("사외", "감사", "독립")):
        return
    sf = indep.setdefault("sub_factors", {}).setdefault("major_shareholder_relation", {})
    if (sf.get("raw") or "").strip() not in _MSR_NOISE:
        return  # 소집공고에 실제 값 있음 → authoritative, override 금지
    # roster 값 확보: roster_prior 승격 힌트 우선, 없으면 index 직접 조회(단일 매칭만)
    apt = ev.get("appointment_type") or {}
    rel = ((apt.get("roster_hint") or {}).get("major_shareholder_relation") or "").strip()
    if not rel:
        core = _core_name(candidate.get("name"))
        cands = [m for m in (roster_index.get(core) or []) if "미등기" not in (m.get("director_type") or "")]
        rel = (cands[0].get("major_shareholder_relation") or "").strip() if len(cands) == 1 else ""
    if not rel or rel.lower() in _MSR_NOISE:
        return  # roster에도 없거나 형식적 → 조용히 skip (틀린 단정 금지)
    # 부정 표기 가드: '임원 아님'·'특수관계 없음'·'해당하지 않음'·'미해당'처럼 의미 키워드를 품은
    # 부정문은 관계 '있음'이 아니다(QA Finding2). 부분일치 escalation 전에 반드시 먼저 거른다.
    if any(neg in rel for neg in ("아니", "아님", "않", "없", "미해당", "비해당", "해당사항")):
        return
    if not any(k in rel for k in _MSR_MEANINGFUL):
        # 비어있지 않지만 generic → hard 신호 금지, provenance만
        sf["roster_hint_relation"] = rel
        sf["hint_source"] = "roster_mxmm_shrholdr_relate"
        return
    sf["result"] = "related"
    sf["raw"] = rel
    sf["hint_source"] = "roster_mxmm_shrholdr_relate"
    sf["mapping"] = "roster-hint"
    if indep.get("summary") in (None, "independent"):
        indep["summary"] = "weak_concerns"  # 승격만 — concern/long_tenure는 유지


# ── Item1 (260710): roster tenure → 장기연임 감지 연동 (Purpose1 완성) ──
# apply_roster_prior가 new→renewed로 승격한 사외이사는 career-matched earliest_start가 없어
# apply_tenure_long_tenure가 그냥 return → 장기연임을 통째로 놓친다. roster_hint.tenure(hffc_pd)를
# floor로 써서 그 blind spot을 닫는다. hffc는 과소계상(H1)이라 ≥5년이면 확실 → false-positive 낮음.

def apply_roster_tenure_long_tenure(ev: dict[str, Any], appointment_type: dict[str, Any] | None, current_year: int) -> None:
    """roster_prior로 승격된 사외/감사 후보의 roster tenure(hffc)를 장기연임 감지에 연동.

    - roster_hint가 있는 후보(=roster_prior 승격)만 대상 — career earliest_start가 없어 놓친 케이스.
    - hffc→연수는 floor(과소계상 안전) → ≥5년이면 potential_long_tenure로 승격.
    - **승격만**: five_year_rule이 이미 potential_long_tenure면 손대지 않음(career 근거 우선, never downgrade).
    - 사외/감사 role에서만(장기연임 독립성 우려 도메인).
    """
    if not isinstance(appointment_type, dict):
        return
    hint = appointment_type.get("roster_hint")
    if not isinstance(hint, dict):
        return  # roster_prior 승격 후보만 (career-renewed는 apply_tenure_long_tenure가 담당)
    role = ev.get("role_type") or ""
    if not ("사외" in role or "감사" in role or "독립" in role or "outside" in role.lower()):
        return
    indep = ev.get("independence")
    if not isinstance(indep, dict):
        return
    fyr = indep.setdefault("sub_factors", {}).setdefault("five_year_rule", {})
    if fyr.get("result") == "potential_long_tenure":
        return  # 이미 감지됨(career 등) → 그대로 유지
    years = _hffc_to_years(hint.get("tenure"), current_year)
    if years is None or years < _LONG_TENURE_YEARS:
        return
    fyr["result"] = "potential_long_tenure"
    fyr["basis"] = f"임원현황 재직기간 {years}년+ (roster hffc 하한, 실제 이상)"
    fyr["source"] = "roster_tenure"
    fyr["years"] = years  # floor — 6년 경계 tiering은 '이상'으로 안전측
    indep["summary"] = "long_tenure_concerns"


def evaluate_candidate(candidate: dict[str, Any], current_year: int, own_company_name: str = "") -> dict[str, Any]:
    """단일 후보 → 3축 평가 dict (이사 회계 risk 이력 검증 비활성, sync)."""
    return {
        "name": candidate.get("name"),  # success
        "birth_date": candidate.get("birthDate"),  # success
        "role_type": candidate.get("roleType"),  # success
        # 후보자 표와 안건 제목이 직위를 다르게 밝힌 경우 — 어느 쪽도 덮지 않고 사실만 전달한다
        "role_type_conflict": candidate.get("roleTypeConflict"),
        "separate_election": candidate.get("separateElection"),  # success (감사위원 분리선임)
        "independence": evaluate_independence(candidate, current_year),
        "faithfulness": evaluate_faithfulness_basic(candidate, own_company_name),
        "disqualification": evaluate_disqualification(candidate, current_year),
    }


async def evaluate_candidate_async(
    candidate: dict[str, Any],
    current_year: int,
    *,
    check_audit_history: bool = False,
    own_company_name: str = "",
) -> dict[str, Any]:
    """단일 후보 평가 (async, 이사 회계 risk 이력 검증 옵션). 이사 회계 risk 이력 검증 활성 시 과거 회사 cross-check."""
    return {
        "name": candidate.get("name"),
        "birth_date": candidate.get("birthDate"),
        "role_type": candidate.get("roleType"),
        # 후보자 표와 안건 제목이 직위를 다르게 밝힌 경우 — 어느 쪽도 덮지 않고 사실만 전달한다
        "role_type_conflict": candidate.get("roleTypeConflict"),
        "separate_election": candidate.get("separateElection"),
        "independence": evaluate_independence(candidate, current_year),
        "faithfulness": await evaluate_faithfulness(candidate, check_audit_history=check_audit_history, own_company_name=own_company_name),
        "disqualification": evaluate_disqualification(candidate, current_year),
    }


# ── Public payload builder ──

async def build_director_evaluation_payload(
    company_query: str,
    *,
    year: int | None = None,
    meeting_type: str = "annual",
    check_audit_history: bool = False,
) -> dict[str, Any]:
    from open_proxy_mcp.services.company import _company_id, resolve_company_query

    client = get_dart_client()
    calls_start = client.api_call_snapshot()

    resolution = await resolve_company_query(company_query)
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        return ToolEnvelope(
            tool="director_evaluation",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[f"'{company_query}'에 해당하는 회사를 찾지 못했다."],
            data={"query": company_query, "usage": build_usage(client.api_call_snapshot() - calls_start)},
        ).to_dict()
    if resolution.status == AnalysisStatus.AMBIGUOUS:
        return ToolEnvelope(
            tool="director_evaluation",
            status=AnalysisStatus.AMBIGUOUS,
            subject=company_query,
            warnings=["회사 식별이 애매해 후보 평가 자동 선택하지 않았다."],
            data={
                "query": company_query,
                "candidates": [{"corp_name": c.get("corp_name"), "corp_code": c.get("corp_code")} for c in resolution.candidates[:10]],
                "usage": build_usage(client.api_call_snapshot() - calls_start),
            },
        ).to_dict()

    selected = resolution.selected
    target_year = year or (date.today().year if date.today().month <= 5 else date.today().year)

    appointments, rcept_no, meta = await fetch_appointments(
        selected["corp_code"], target_year, meeting_type
    )
    canonical_corp_name = selected.get("corp_name", "") or ""

    # Purpose 1 (260710): roster(exctvSttus)를 파싱 prior로 fetch — new→renewed 오분류 교정용.
    # 실패/부재는 graceful(힌트 없이 진행).
    roster_index: dict[str, list[dict[str, Any]]] = {}
    roster_year: int | None = None
    roster_ref: tuple[int, int] | None = None   # (기준일 연, 월)
    board_gender: Counter = Counter()           # 사다리가 전부 비면 빈 상태로 남는다
    roster_report: str | None = None
    roster_back: int | None = None
    for back, code, label, ref_month in _ROSTER_SOURCES:
        try:
            _resp = await client.get_executive_status(
                selected["corp_code"], str(target_year - back), code)
        except DartClientError as exc:
            # 과호출·인증 실패를 삼키고 사다리를 계속 타면 경고 없이 콜만 태운다.
            if getattr(exc, "status", None) in ("020", "011", "012"):
                break
            continue
        except Exception:
            continue
        _rows = (_resp or {}).get("list") or []
        # **완전성 게이트** — 행이 있다고 쓸 수 있는 게 아니다. 분기·반기보고서는 임원현황
        # 기재를 생략할 수 있어(자본시장법 시행령 §170) **일부만 실린 채 응답이 오는** 경우가
        # 있다. 실측 29사 중 1사(미래에셋증권 2025 3분기): 사업보고서 157행·등기 7명인데
        # 3분기는 **1행·등기 0명**이었다. 그걸 받아들이면 사업보고서로 확정했던 등기 시작이
        # 소집공고 추정으로 되돌아간다(김미섭 2021→1994). 불완전하면 다음 rung 으로 내려간다.
        if not any(_roster_has_board_member(r) for r in _rows):
            continue
        roster_index = build_roster_index(_rows)
        # 자본시장법 §165의20 — 자산 2조+ 상장사는 이사회를 특정 성의 이사로만 구성할 수 없다.
        # `sexdstn` 은 실측 100% 채워지는데 지금까지 받아만 오고 안 썼다. 등기 이사만 센다
        # (미등기 집행임원은 이사회 구성원이 아니다).
        board_gender = Counter(
            (r.get("sexdstn") or "").strip() or "미상"
            for r in _rows if _roster_has_board_member(r))
        if roster_index:
            roster_year, roster_back = target_year - back, back
            roster_ref = (roster_year, ref_month)
            roster_report = f"{roster_year}년 {label}"
            break

    # 안건 제목이 사내이사로 **지목한** 이름 — roleType 오배정을 걸러내는 데 쓴다
    inside_named = names_titled_inside_director(appointments)

    # 후보별 평가
    evaluations: list[dict[str, Any]] = []
    candidate_count = 0
    seen_candidate_names: set[str] = set()
    for ap in appointments:
        cands = ap.get("candidates") or []
        for c in cands:
            # 같은 후보가 묶음 안건(제3호)+개별 안건(제3-1호)+번호중복(제2-3호)에 중복 등장하면
            # 이름 기준 1회만 평가 — candidates_count·evaluations 부풀림 방지 (콜마홀딩스 23→5)
            cname = (c.get("name") or "").strip()
            if cname and cname in seen_candidate_names:
                continue
            if cname:
                seen_candidate_names.add(cname)
            ev = await evaluate_candidate_async(c, target_year, check_audit_history=check_audit_history, own_company_name=canonical_corp_name)
            ev["agenda_title"] = ap.get("title")
            ev["agenda_action"] = ap.get("action")
            ev["agenda_category"] = ap.get("category")
            ev["appointment_type"] = detect_appointment_type(c, canonical_corp_name, target_year)
            # Purpose 1: roster(임원현황) 힌트로 new→renewed 오분류 교정 (tenure 체크 앞단)
            apply_roster_prior(ev, c, roster_index)
            # Item2c/H2: 소집공고 최대주주관계 결측 시 roster 값으로 채움 (fill-when-missing)
            apply_roster_msr_rescue(ev, c, roster_index)
            # 260730: 사외이사 후보의 「상무 종사」를 정형으로 검산 (상법 §382③1호)
            ev["agenda_named_inside_director"] = (
                re.sub(r"\s+", "", c.get("name") or "") in inside_named)
            apply_roster_employee_check(ev, c, roster_index, report_label=roster_report)
            # 260730: 소집공고 경력 결측 시 임원현황 주요경력으로 보완(덮지 않음)
            apply_roster_career_fallback(ev, c, roster_index, report_label=roster_report)
            # 260729: 등기 재직 시작연도를 임원현황(정형)으로 확정 — 소집공고 경력 추정을 덮는다
            apply_roster_board_tenure(
                ev, c, roster_index, roster_year,
                ref_month=(roster_ref[1] if roster_ref else 12),
                report_label=roster_report,
                can_confirm_unregistered=(roster_back == 1),
                meeting_year=target_year)
            # 갭C: 이 회사 재직 5년+ → 장기연임 승격 (earliest_start 계산-후-폐기 해소)
            apply_tenure_long_tenure(ev, ev["appointment_type"], target_year)
            # Item1: roster_prior 승격 후보는 earliest_start가 없음 → roster tenure(hffc)로 장기연임 catch
            apply_roster_tenure_long_tenure(ev, ev["appointment_type"], target_year)
            evaluations.append(ev)
            candidate_count += 1

    # zero-candidate 시그널: 인사 안건(appointments)은 있는데 후보 추출이 0명이면
    # 소집공고 후보표 파싱 실패 의심 → warning + parsing_failures 반영 (silent empty 방지).
    zero_candidate = len(appointments) > 0 and candidate_count == 0
    director_warnings: list[str] = []
    if zero_candidate:
        director_warnings.append(
            f"인사 안건 {len(appointments)}건이 있으나 후보 추출 0명 — 소집공고 후보표 파싱 실패 의심"
        )
    filing_meta = build_filing_meta(
        filing_count=len(appointments),
        parsing_failures=len(appointments) if zero_candidate else 0,
    )
    if filing_meta["no_filing"]:
        status = AnalysisStatus.NO_FILING
    else:
        status = AnalysisStatus.EXACT

    evidence = []
    if rcept_no:
        evidence.append(EvidenceRef(
            evidence_id=f"ev_director_eval_{selected['corp_code']}_{target_year}",
            source_type=SourceType.DART_XML,
            rcept_no=rcept_no,
            section="주주총회소집공고 — 임원 선임",
            note=f"{candidate_count}명 후보 추출 / {len(appointments)} 안건",
        ))

    return ToolEnvelope(
        tool="director_evaluation",
        status=status,
        subject=selected.get("corp_name", company_query),
        warnings=director_warnings,
        data={
            "query": company_query,
            "company_id": _company_id(selected),
            "canonical_name": selected.get("corp_name"),
            "year": target_year,
            "meeting_type": meeting_type,
            "appointments_count": len(appointments),
            "candidates_count": candidate_count,
            "evaluations": evaluations,
            # 이사회 성별 구성 — 자본시장법 §165의20 판정 재료(자산 임계는 호출측에서 본다)
            "board_gender": ({"male": board_gender.get("남", 0),
                              "female": board_gender.get("여", 0),
                              "unknown": board_gender.get("미상", 0),
                              "as_of": roster_report}
                             if board_gender else None),
            "rcept_no": rcept_no,
            "agenda_titles_fallback": (meta[0].get("agenda_titles") if meta and meta[0].get("agenda_titles") else []),
            **filing_meta,
            "usage": build_usage(client.api_call_snapshot() - calls_start),
        },
        evidence_refs=evidence,
    ).to_dict()
