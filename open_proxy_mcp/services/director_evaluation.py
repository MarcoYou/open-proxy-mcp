"""director_evaluation — 이사/감사/감사위원 후보 평가 모듈.

3축: 독립성 / 충실성 / 결격사유.
**✅ 가능 항목만 메모에 표시**. 자동 검증 안 된 항목 (hard-fail)은 침묵.

매핑 분류 (모든 항목 주석):
- success: 정형 필드 직접 매핑
- soft-fail: raw text를 LLM에게 노출 (정규식/매칭 실패 시)
- hard-fail: 데이터 자체 미존재 — 메모/코드 모두 침묵 (코붕이 명시 지시)

Phase 1: 독립성 + 결격사유 (기본 매핑) + 후보 추출.
Phase 2 (다음 iteration): 충실성 — Marco 시나리오 (과거 회사 × 재직 기간 × 회계 risk).
"""

from __future__ import annotations

import re
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
from open_proxy_mcp.tools.parser import parse_personnel_xml


# ── 후보 데이터 fetch (success/soft-fail 분류) ──

async def fetch_appointments(
    corp_code: str,
    year: int,
    meeting_type: str = "annual",
) -> tuple[list[dict[str, Any]], str | None, list[dict[str, Any]]]:
    """주총소집공고 검색 + 후보 추출.

    매핑:
    - rcept_no, rcept_dt, report_nm → success (정형)
    - 본문 personnel section → parse_personnel_xml로 success / soft-fail (파싱 실패 시)

    return: (appointments, rcept_no, filings_meta)
    """
    client = get_dart_client()
    # 정기는 보통 직전 12월 결산 이후 1-3월. 임시는 연중.
    if meeting_type == "annual":
        bgn_de = f"{year}0101"
        end_de = f"{year}0501"
    else:
        bgn_de = f"{year}0101"
        end_de = f"{year}1231"

    try:
        data = await client.search_filings(
            corp_code=corp_code,
            bgn_de=bgn_de,
            end_de=end_de,
            pblntf_ty=None,
        )
    except DartClientError as exc:
        return [], None, [{"error": f"search_filings 실패: {exc.status} {exc}"}]

    items = data.get("list", []) or []
    notices = [
        i for i in items
        if "주주총회소집공고" in i.get("report_nm", "")
        and (("임시" in i.get("report_nm", "")) if meeting_type == "extraordinary" else ("임시" not in i.get("report_nm", "")))
    ]
    if not notices:
        return [], None, [{"info": f"{year} {meeting_type} 주총소집공고 미발견"}]

    notice = notices[0]
    rcept_no = notice.get("rcept_no")

    try:
        doc = await client.get_document_cached(rcept_no)
    except Exception as exc:
        return [], rcept_no, [{"error": f"get_document 실패: {exc}"}]

    text = doc.get("html") or doc.get("text") or ""
    if not text:
        return [], rcept_no, [{"error": "본문 비어 있음"}]

    parsed = parse_personnel_xml(text)
    appointments = parsed.get("appointments", []) or []
    return appointments, rcept_no, [{"rcept_no": rcept_no, "report_nm": notice.get("report_nm")}]


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
    return False, None


def evaluate_independence(candidate: dict[str, Any], current_year: int) -> dict[str, Any]:
    """독립성 4 sub-factor 평가 (모두 success).

    return: {sub_factors: {key: {result, evidence}}, summary: str}
    """
    out: dict[str, Any] = {"sub_factors": {}}

    # 1. 최대주주/특수관계인 여부 → success (DART 정형 필드)
    msr = (candidate.get("majorShareholderRelation") or "").strip()
    is_independent_from_major = msr in ("없음", "-", "")
    out["sub_factors"]["major_shareholder_relation"] = {
        "result": "independent" if is_independent_from_major else "related",
        "raw": msr,
        "mapping": "success",
    }

    # 2. 회사와 거래 관계 (recent3yTransactions) → success
    rt = candidate.get("recent3yTransactions")
    has_transactions = bool(rt) and rt not in ("없음", "-", None)
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
        "evidence": employee_ev,
        "mapping": "success" if employee_ev or not candidate.get("careerDetails") else "soft-fail",
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

    # 통합 sumamry — "독립" / "관련" / "검토 필요"
    flags = [
        not is_independent_from_major,
        has_transactions,
        employee_match,
    ]
    if any(flags):
        out["summary"] = "concerns"
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
    bd = (candidate.get("birthDate") or "").strip()
    age = None
    if bd:
        m = re.search(r"(\d{4})", bd)
        if m:
            age = current_year - int(m.group(1))
    is_minor = age is not None and age < 19
    out["sub_factors"]["age"] = {
        "result": "minor" if is_minor else "adult",
        "age": age,
        "mapping": "success",
    }

    # 2. eligibility 필드 (taxDelinquency / insolventMgmt / legalDisqualification) → success
    elig = candidate.get("eligibility") or {}
    elig_flags: dict[str, str | None] = {}
    has_red = False
    for k in ("taxDelinquency", "insolventMgmt", "legalDisqualification"):
        v = elig.get(k)
        if v and v not in ("해당 사항 없음", "해당사항 없음", "없음", "-", None):
            has_red = True
            elig_flags[k] = v
        else:
            elig_flags[k] = None
    out["sub_factors"]["eligibility"] = {
        "result": "red_flag" if has_red else "clean",
        "raw_flags": {k: v for k, v in elig_flags.items() if v},
        "mapping": "success",
    }

    # ⚠️ hard-fail (메모에 안 적음): 형사 처벌 / 파산 / 임원 자격 박탈 / 사적 관계
    # → 코드/메모에서 침묵 (코붕이 지시)

    out["summary"] = "red_flag" if (is_minor or has_red) else "clean"
    return out


# ── 충실성 placeholder (Phase 2에서 확장) ──

def evaluate_faithfulness_basic(candidate: dict[str, Any]) -> dict[str, Any]:
    """충실성 — Phase 1: dutyPlan + recommendationReason raw 노출 (soft-fail).

    Phase 2: 출석률 (corp_gov_report) + Marco overlap (financial_metrics × careerDetails 회사명).
    """
    return {
        "duty_plan_raw": candidate.get("dutyPlan") or None,  # soft-fail (자유 텍스트)
        "recommendation_reason_raw": candidate.get("recommendationReason") or None,
        "main_job": candidate.get("mainJob"),  # success
        "recommender": candidate.get("recommender"),  # success
        "career_company_groups": candidate.get("careerCompanyGroups") or [],
        "marco_scenario_status": "phase_2_pending",  # 다음 iteration
        "summary": "raw_disclosed",
    }


# ── 후보 평가 통합 ──

def evaluate_candidate(candidate: dict[str, Any], current_year: int) -> dict[str, Any]:
    """단일 후보 → 3축 평가 dict."""
    return {
        "name": candidate.get("name"),  # success
        "birth_date": candidate.get("birthDate"),  # success
        "role_type": candidate.get("roleType"),  # success
        "separate_election": candidate.get("separateElection"),  # success (감사위원 분리선임)
        "independence": evaluate_independence(candidate, current_year),
        "faithfulness": evaluate_faithfulness_basic(candidate),
        "disqualification": evaluate_disqualification(candidate, current_year),
    }


# ── Public payload builder ──

async def build_director_evaluation_payload(
    company_query: str,
    *,
    year: int | None = None,
    meeting_type: str = "annual",
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

    # 후보별 평가
    evaluations: list[dict[str, Any]] = []
    candidate_count = 0
    for ap in appointments:
        cands = ap.get("candidates") or []
        for c in cands:
            ev = evaluate_candidate(c, target_year)
            ev["agenda_title"] = ap.get("title")
            ev["agenda_action"] = ap.get("action")
            ev["agenda_category"] = ap.get("category")
            evaluations.append(ev)
            candidate_count += 1

    filing_meta = build_filing_meta(
        filing_count=len(appointments),
        parsing_failures=0,
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
        warnings=[],
        data={
            "query": company_query,
            "company_id": _company_id(selected),
            "canonical_name": selected.get("corp_name"),
            "year": target_year,
            "meeting_type": meeting_type,
            "appointments_count": len(appointments),
            "candidates_count": candidate_count,
            "evaluations": evaluations,
            "rcept_no": rcept_no,
            **filing_meta,
            "usage": build_usage(client.api_call_snapshot() - calls_start),
        },
        evidence_refs=evidence,
    ).to_dict()
