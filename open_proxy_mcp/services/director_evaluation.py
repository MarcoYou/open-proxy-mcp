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
    # "해당사항 없음(충족)" / "해당사항없음" 등 변형 모두 clean으로 인식.
    elig = candidate.get("eligibility") or {}
    elig_flags: dict[str, str | None] = {}
    has_red = False
    for k in ("taxDelinquency", "insolventMgmt", "legalDisqualification"):
        v = elig.get(k)
        if not v or v in ("-", None):
            elig_flags[k] = None
            continue
        v_norm = str(v).replace(" ", "")
        # 부정 키워드 ("없음" / "충족" / "해당없음") 포함 시 clean
        if any(kw in v_norm for kw in ("없음", "충족", "해당사항없음")):
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


# ── 충실성 — Marco 시나리오 (과거 회사 × 재직 기간 × 회계 risk overlap) ──

# 한국 회사명 정규식 패턴 — careerCompanyGroups company 필드에서 추출.
# 예: "삼성전자 사외이사", "KB금융 ESG위원장", "POSCO홀딩스 부사장"
# 한국 회사 + 직책이 한 줄로 붙어 있는 케이스 대응.
_KOREAN_CORP_SUFFIX_RE = re.compile(
    r"([가-힣A-Za-z0-9&\(\)]+(?:홀딩스|금융지주|증권|건설|중공업|화학|전자|반도체|"
    r"바이오|제약|텔레콤|에너지|화공|상사|글로벌|디스플레이|자동차|생명과학)?[가-힣A-Za-z0-9]*)"
)


def _extract_korean_corp_names(career_company_groups: list[dict[str, Any]] | None) -> list[str]:
    """careerCompanyGroups → 한국 회사명 candidates list.

    매핑: success (회사명 추출 성공) / soft-fail (정규식 실패 시 raw 그대로 노출)
    """
    if not career_company_groups:
        return []
    names: list[str] = []
    for grp in career_company_groups:
        company = (grp.get("company") or "").strip()
        if not company:
            continue
        # 첫 segment 추출 (콤마/공백 분리)
        first = re.split(r"[,，\(]", company, maxsplit=1)[0].strip()
        if first and len(first) >= 2:
            names.append(first)
    return names


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


async def _check_marco_overlap(
    corp_name: str,
    period_start: int | None,
    period_end: int | None,
) -> dict[str, Any] | None:
    """과거 회사 corp_code lookup → financial_metrics audit_opinion 호출.

    재직 기간 (period_start ~ period_end) 안에 non_clean 감사의견 또는
    capital_impairment_full 발생했는지 체크.

    return: red_flag dict / None.
    매핑: success (corp_code lookup OK) / soft-fail (회사명 매핑 실패 시 None)
    """
    if not corp_name or not period_start:
        return None
    client = get_dart_client()
    try:
        match = await client.lookup_corp_code(corp_name)
    except Exception:
        return None
    if not match or not match.get("stock_code"):
        # 한국 비상장 또는 매핑 실패 → soft-fail (None 반환, 메모에서 "raw text only")
        return None
    past_corp_code = match["corp_code"]
    end_year = period_end or 2025  # 현재까지 재직이면 현재 연도까지 cross-check

    # 재직 기간 동안의 financial_metrics yoy + audit_opinion 호출
    from open_proxy_mcp.services.financial_metrics import _safe_fetch_audit, _fetch_year_metrics

    red_flags: list[dict[str, Any]] = []
    for y in range(max(period_start, 2020), min(end_year, 2025) + 1):
        # audit opinion check
        try:
            rows, err = await _safe_fetch_audit(past_corp_code, y)
            if rows:
                for r in rows[:1]:  # 첫 row만 (당기)
                    op = (r.get("adt_opinion") or "").strip()
                    if op and "적정" not in op:
                        red_flags.append({
                            "type": "non_clean_audit_opinion",
                            "year": y,
                            "opinion": op,
                            "company": corp_name,
                            "rcept_no": r.get("rcept_no"),
                        })
        except Exception:
            continue
        # 자본잠식 체크 — yoy 호출 비싸므로 audit_opinion만 우선 (Phase 1 한계)
        # 자본잠식은 다음 iteration에서 metrics summary로 추가 가능

    if red_flags:
        return {
            "company": corp_name,
            "corp_code": past_corp_code,
            "tenure_start_year": period_start,
            "tenure_end_year": period_end,
            "red_flags": red_flags,
        }
    return None


async def evaluate_faithfulness(
    candidate: dict[str, Any],
    *,
    enable_marco: bool = False,
) -> dict[str, Any]:
    """충실성 평가.

    Phase 1 기본:
    - dutyPlan / recommendationReason → soft-fail (raw 노출, LLM 자연어 판단)
    - mainJob / recommender / careerCompanyGroups → success (구조화)

    enable_marco=True: 과거 회사 × 재직 기간 × 회계 risk overlap 자동 체크.
    Marco 시나리오는 추가 DART 호출 발생 (cost) — 옵션.
    """
    out: dict[str, Any] = {
        "duty_plan_raw": candidate.get("dutyPlan") or None,
        "recommendation_reason_raw": candidate.get("recommendationReason") or None,
        "main_job": candidate.get("mainJob"),
        "recommender": candidate.get("recommender"),
        "career_company_groups": candidate.get("careerCompanyGroups") or [],
    }

    # Marco 시나리오 — 과거 회사 × 재직 기간 cross-check
    marco_red_flags: list[dict[str, Any]] = []
    marco_status = "disabled"
    if enable_marco:
        marco_status = "checked"
        career_details = candidate.get("careerDetails") or []
        career_groups = candidate.get("careerCompanyGroups") or []
        # career_groups의 company + items (period 리스트) 조합
        for grp in career_groups:
            company_raw = (grp.get("company") or "").strip()
            if not company_raw:
                continue
            # 첫 segment만 corp_name으로 시도 (정규식 매칭)
            first_segment = re.split(r"[,，\(]", company_raw, maxsplit=1)[0].strip()
            # 첫 단어가 한국 회사 형태인지 확인 (한글/영문 mix)
            corp_name_candidate = first_segment.split()[0] if first_segment else ""
            if not corp_name_candidate or len(corp_name_candidate) < 2:
                continue
            for period in (grp.get("items") or []):
                start, end = _parse_career_period(period)
                if start is None:
                    continue
                overlap = await _check_marco_overlap(corp_name_candidate, start, end)
                if overlap:
                    marco_red_flags.append(overlap)

    out["marco_scenario"] = {
        "status": marco_status,
        "red_flags": marco_red_flags,
        "summary": "red_flag" if marco_red_flags else ("clean" if marco_status == "checked" else "not_checked"),
    }

    # 통합 summary
    if marco_red_flags:
        out["summary"] = "concerns"
    else:
        out["summary"] = "raw_disclosed" if marco_status != "checked" else "clean"
    return out


# 후방 호환 alias (Phase 1 코드 사용 중)
def evaluate_faithfulness_basic(candidate: dict[str, Any]) -> dict[str, Any]:
    """동기 alias — Marco 비활성. enable_marco 옵션 없는 호출처용."""
    return {
        "duty_plan_raw": candidate.get("dutyPlan") or None,
        "recommendation_reason_raw": candidate.get("recommendationReason") or None,
        "main_job": candidate.get("mainJob"),
        "recommender": candidate.get("recommender"),
        "career_company_groups": candidate.get("careerCompanyGroups") or [],
        "marco_scenario": {"status": "disabled", "red_flags": [], "summary": "not_checked"},
        "summary": "raw_disclosed",
    }


# ── 후보 평가 통합 ──

def evaluate_candidate(candidate: dict[str, Any], current_year: int) -> dict[str, Any]:
    """단일 후보 → 3축 평가 dict (Marco 비활성, sync)."""
    return {
        "name": candidate.get("name"),  # success
        "birth_date": candidate.get("birthDate"),  # success
        "role_type": candidate.get("roleType"),  # success
        "separate_election": candidate.get("separateElection"),  # success (감사위원 분리선임)
        "independence": evaluate_independence(candidate, current_year),
        "faithfulness": evaluate_faithfulness_basic(candidate),
        "disqualification": evaluate_disqualification(candidate, current_year),
    }


async def evaluate_candidate_async(
    candidate: dict[str, Any],
    current_year: int,
    *,
    enable_marco: bool = False,
) -> dict[str, Any]:
    """단일 후보 평가 (async, Marco 옵션). Marco 활성 시 과거 회사 cross-check."""
    return {
        "name": candidate.get("name"),
        "birth_date": candidate.get("birthDate"),
        "role_type": candidate.get("roleType"),
        "separate_election": candidate.get("separateElection"),
        "independence": evaluate_independence(candidate, current_year),
        "faithfulness": await evaluate_faithfulness(candidate, enable_marco=enable_marco),
        "disqualification": evaluate_disqualification(candidate, current_year),
    }


# ── Public payload builder ──

async def build_director_evaluation_payload(
    company_query: str,
    *,
    year: int | None = None,
    meeting_type: str = "annual",
    enable_marco: bool = False,
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
            ev = await evaluate_candidate_async(c, target_year, enable_marco=enable_marco)
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
