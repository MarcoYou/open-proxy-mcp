"""advise_vote — 주총 전 의결권 행사 메모 (운용사 보고서 스타일).

핵심: 안건별 행사방향 (FOR / AGAINST / REVIEW) + 결정 사유 (정책 근거 + 사실 근거).
**gap 비교 X, 검증 가능한 fact + 정책 근거만**.

6 upstream:
- shareholder_meeting (summary + agenda + compensation)
- ownership_structure (control_map)
- corp_gov_report (summary)
- financial_metrics (summary + audit_opinion)
- proxy_guideline (predict scope — 안건별 정책 + 자동 채점)
- director_evaluation (이사/감사 후보 평가, optional Marco)

매핑 분류:
- 안건 리스트 / 후보 / 지분 / 재무 → success (정형)
- 결정 사유 / 후보 약력 → soft-fail (raw text 일부 노출)
- 형사 / 사적 관계 등 → hard-fail (침묵)
"""

from __future__ import annotations

import asyncio
import json
from datetime import date
from importlib.resources import files
from typing import Any

from open_proxy_mcp.dart.client import get_dart_client
from open_proxy_mcp.services.company import _company_id, resolve_company_query
from open_proxy_mcp.services.contracts import (
    AnalysisStatus,
    EvidenceRef,
    SourceType,
    ToolEnvelope,
    build_filing_meta,
    build_usage,
)
from open_proxy_mcp.services.corp_gov_report import build_corp_gov_report_payload
from open_proxy_mcp.services.director_evaluation import build_director_evaluation_payload
from open_proxy_mcp.services.financial_metrics import build_financial_metrics_payload
from open_proxy_mcp.services.ownership_structure import build_ownership_structure_payload
from open_proxy_mcp.services.proxy_guideline import build_proxy_guideline_payload
from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload


# ── vote_style 정책 로딩 (운용사별 voting_rules) ──

# vote_style alias → policy JSON file ID
_VOTE_STYLE_POLICY_FILE = {
    "open_proxy": "open_proxy_v1",
    "mirae_asset": "m_legacy_2026-04",  # 최신 2026 정책 우선
    "m_legacy": "m_legacy_2026-04",
    "samsung": "s_legacy_2025-04",
    "s_legacy": "s_legacy_2025-04",
    "samsung_active": "sa_active_2025-04",
    "sa_active": "sa_active_2025-04",
    "kim": "k_legacy_2025-04",
    "k_legacy": "k_legacy_2025-04",
    "truston": "t_activist_2025-04",
    "t_activist": "t_activist_2025-04",
    "align_partners": "a_activist_2025-04",
    "a_activist": "a_activist_2025-04",
    "baring": "b_foreign_2025-04",
    "b_foreign": "b_foreign_2025-04",
    "cha_partners": "c_activist_2026-04",
    "c_activist": "c_activist_2026-04",
    "nps": "nps_2025-03",
}


def _load_vote_style_policy(vote_style: str) -> dict[str, Any] | None:
    """vote_style → policy JSON (voting_rules + meta).

    매핑: success (file 존재) / soft-fail (file 없음 — None 반환, OPM default fallback).
    """
    file_id = _VOTE_STYLE_POLICY_FILE.get(vote_style)
    if not file_id:
        return None
    try:
        path = files("open_proxy_mcp.data.asset_managers") / "policies" / f"{file_id}.json"
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, Exception):
        return None


def _policy_default(policy: dict[str, Any] | None, category: str) -> str | None:
    """voting_rules[category]['default'] 값 (for/against/review/case_by_case/None)."""
    if not policy:
        return None
    rules = policy.get("voting_rules") or {}
    cat_rule = rules.get(category) or {}
    return cat_rule.get("default")


def _apply_policy_default(default_str: str | None, fallback_decision: str, fallback_reason: str) -> tuple[str, str]:
    """운용사 정책 default → 결정 변환. case_by_case/None → 기존 OPM logic fallback."""
    if not default_str or default_str == "case_by_case":
        return fallback_decision, fallback_reason
    if default_str == "for":
        return "FOR", "운용사 정책상 default=FOR (case별 reverse 룰은 별도)"
    if default_str == "against":
        return "AGAINST", "운용사 정책상 default=AGAINST"
    if default_str == "review":
        return "REVIEW", "운용사 정책상 default=REVIEW (case별 검토)"
    return fallback_decision, fallback_reason


# ── 안건별 결정 logic ──

def _classify_agenda(agenda_title: str) -> str:
    """안건 제목 → category. proxy_guideline의 voting_rules 키와 매칭."""
    t = (agenda_title or "").strip()
    if "재무제표" in t and "배당" not in t:
        return "financial_statements"
    if "배당" in t or "이익잉여금" in t:
        return "cash_dividend"
    if "사외이사" in t or ("이사" in t and "선임" in t and "감사위원" not in t):
        return "director_election"
    if "감사위원" in t and "선임" in t:
        return "audit_committee_election"
    if "감사" in t and "선임" in t:
        return "audit_committee_election"
    if "보수" in t or "보수한도" in t:
        return "director_compensation"
    if "정관" in t:
        return "articles_amendment"
    if "자기주식" in t or "자사주" in t:
        return "treasury_share"
    if any(k in t for k in ("합병", "분할", "주식교환", "주식이전")):
        return "merger_or_restructuring"
    if "주주제안" in t:
        return "shareholder_proposal"
    return "other"


def _decide_director_election(eval_match: dict[str, Any] | None) -> tuple[str, str]:
    """이사/감사위원 선임 안건 → (decision, reason).

    director_evaluation 결과로 결정:
    - 결격사유 red_flag → AGAINST
    - 독립성 concerns → REVIEW (사용자 판단)
    - 모두 clean → FOR
    """
    if not eval_match:
        return "REVIEW", "후보 평가 데이터 없음 — 사용자 검토 필요"
    disq = eval_match.get("disqualification", {}).get("summary", "")
    indep = eval_match.get("independence", {}).get("summary", "")
    marco = eval_match.get("faithfulness", {}).get("marco_scenario", {}).get("summary", "")

    if disq == "red_flag":
        return "AGAINST", f"결격사유 발견 (eligibility 또는 미성년)"
    if marco == "red_flag":
        # 코붕이 지시: Marco red flag = AGAINST 자동 X. REVIEW + 메모 raw 노출.
        # 사외이사가 회계 risk를 막지 못한 게 본인 책임이라고 단정 어려움 — 사용자 판단 위임.
        return "REVIEW", "Marco 시나리오 — 과거 재직 회사 회계 risk 발생 (raw 메모 참조 후 판단)"
    if indep == "concerns":
        return "REVIEW", "독립성 우려 (최대주주 관계 또는 회사와 거래 또는 이전 회사 직원 가능성)"
    return "FOR", "독립성/결격사유 모두 clean"


def _decide_compensation(comp_payload: dict[str, Any] | None) -> tuple[str, str]:
    """보수한도 안건 → (decision, reason).

    소진율 < 30%인데 인상 → AGAINST. 50%+ 대폭 인상 → REVIEW. 그 외 → FOR.
    proxy_guideline director_compensation 룰 적용.
    """
    if not comp_payload:
        return "REVIEW", "보수 데이터 없음"
    data = comp_payload.get("data", {})
    summary = data.get("summary", {}) or {}
    util_rate = summary.get("utilization_rate_pct")  # 소진율
    increase_rate = summary.get("increase_rate_pct")  # 전년 대비 증감률

    if util_rate is not None and util_rate < 30 and increase_rate and increase_rate > 0:
        return "AGAINST", f"소진율 {util_rate:.0f}%인데 한도 인상 ({increase_rate:+.0f}%)"
    if increase_rate and increase_rate >= 50:
        return "REVIEW", f"보수한도 대폭 인상 ({increase_rate:+.0f}%) — 사용자 검토"
    return "FOR", "소진율 적정 범위 또는 동결/소폭 변경"


def _decide_financial_statements(fm_payload: dict[str, Any] | None) -> tuple[str, str]:
    """재무제표 승인 → 감사의견 적정이면 FOR, 한정/부적정이면 AGAINST."""
    if not fm_payload:
        return "REVIEW", "재무 데이터 없음"
    data = fm_payload.get("data", {})
    audit = data.get("audit_opinion", {}) or {}
    summary = data.get("summary", {}) or {}
    latest_op = audit.get("summary", {}).get("latest_opinion") if "summary" in audit else None
    cap_status = summary.get("capital_impairment_status")

    if cap_status == "full":
        return "AGAINST", "완전 자본잠식 (KOSDAQ 상장폐지 사유)"
    if latest_op and "적정" not in latest_op:
        return "AGAINST", f"감사의견 {latest_op}"
    return "FOR", "감사의견 적정 + 자본잠식 없음"


def _decide_articles_amendment(agenda_title: str) -> tuple[str, str]:
    """정관변경 안건 → 세부 키워드 기반 default."""
    t = agenda_title or ""
    if "집중투표" in t and ("배제" in t or "삭제" in t):
        return "AGAINST", "집중투표 배제 — 소수주주 보호 후퇴"
    if "이사" in t and ("정원" in t or "축소" in t):
        return "REVIEW", "이사회 정원 축소 — 거버넌스 영향"
    return "REVIEW", "정관변경 — 본문 검토 필요 (각 조문별 영향 확인)"


def _decide_treasury_share(agenda_title: str) -> tuple[str, str]:
    """자사주 안건."""
    t = agenda_title or ""
    if "소각" in t:
        return "FOR", "자사주 소각 — 주주환원"
    if "처분" in t:
        return "REVIEW", "자사주 처분 — 우호 지분 형성 가능성 검토"
    return "REVIEW", "자사주 안건 본문 검토 필요"


def _decide_dividend(agenda_title: str, fm_payload: dict[str, Any] | None) -> tuple[str, str]:
    """배당 안건 — 분기/특별 등."""
    if not fm_payload:
        return "FOR", "배당 안건 — 정상 영업 가정 (재무 데이터 부족)"
    summary = (fm_payload.get("data") or {}).get("summary", {}) or {}
    payout = summary.get("payout_ratio_pct")
    if payout is not None and payout > 80:
        return "REVIEW", f"배당성향 {payout}% — 과도한 배당 가능성"
    return "FOR", "배당 안건 — 재무 건전 + 배당성향 적정"


# ── 메인 advise builder ──

async def build_advise_vote_payload(
    company_query: str,
    *,
    year: int | None = None,
    meeting_type: str = "annual",
    vote_style: str = "open_proxy",
    enable_marco: bool = False,
) -> dict[str, Any]:
    """advise_vote_before_meeting payload."""
    client = get_dart_client()
    calls_start = client.api_call_snapshot()

    resolution = await resolve_company_query(company_query)
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        return ToolEnvelope(
            tool="advise_vote_before_meeting",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[f"'{company_query}' 회사 식별 실패"],
            data={"query": company_query, "usage": build_usage(client.api_call_snapshot() - calls_start)},
        ).to_dict()
    if resolution.status == AnalysisStatus.AMBIGUOUS:
        return ToolEnvelope(
            tool="advise_vote_before_meeting",
            status=AnalysisStatus.AMBIGUOUS,
            subject=company_query,
            warnings=["회사 식별 모호"],
            data={
                "query": company_query,
                "candidates": [
                    {"corp_name": c.get("corp_name"), "corp_code": c.get("corp_code")}
                    for c in resolution.candidates[:10]
                ],
                "usage": build_usage(client.api_call_snapshot() - calls_start),
            },
        ).to_dict()

    selected = resolution.selected
    target_year = year or date.today().year - 1

    # vote_style 정책 로딩 (success / soft-fail)
    policy = _load_vote_style_policy(vote_style)
    policy_id = (policy or {}).get("policy_id") or vote_style
    policy_meta = (policy or {}).get("policy_meta") or {}

    # ── 6 upstream 병렬 호출 ──
    async def _safe(fn, *args, **kw):
        try:
            return await fn(*args, **kw)
        except Exception as exc:
            return {"tool": fn.__name__, "status": "error", "data": {}, "warnings": [str(exc)], "evidence_refs": []}

    meeting_summary, meeting_agenda, meeting_comp, ownership, gov_report, fin_metrics, director_eval = await asyncio.gather(
        _safe(build_shareholder_meeting_payload, company_query, scope="summary", year=target_year, meeting_type=meeting_type),
        _safe(build_shareholder_meeting_payload, company_query, scope="agenda", year=target_year, meeting_type=meeting_type),
        _safe(build_shareholder_meeting_payload, company_query, scope="compensation", year=target_year, meeting_type=meeting_type),
        _safe(build_ownership_structure_payload, company_query, scope="control_map"),
        _safe(build_corp_gov_report_payload, company_query, scope="summary"),
        _safe(build_financial_metrics_payload, company_query, scope="summary", year=target_year),
        _safe(build_director_evaluation_payload, company_query, year=target_year, meeting_type=meeting_type, enable_marco=enable_marco),
    )

    # 안건 리스트 추출 (success 매핑)
    agenda_data = (meeting_agenda.get("data") or {})
    agenda_summary = agenda_data.get("agenda_summary", {}) or {}
    agenda_titles = agenda_summary.get("titles", []) or []
    # shareholder_meeting v2 agenda 미검출 시 director_evaluation의 본문 agenda fallback
    if not agenda_titles:
        fallback_titles = (director_eval.get("data") or {}).get("agenda_titles_fallback", []) or []
        if fallback_titles:
            agenda_titles = fallback_titles

    # 후보 평가 dict — name → eval
    director_data = (director_eval.get("data") or {})
    director_evals = director_data.get("evaluations", []) or []
    name_to_eval: dict[str, dict[str, Any]] = {}
    for ev in director_evals:
        nm = ev.get("name")
        if nm:
            name_to_eval[nm] = ev

    # 안건별 결정 + 사유 (vote_style 정책 wire 적용)
    agenda_decisions: list[dict[str, Any]] = []
    for title in agenda_titles:
        category = _classify_agenda(title)
        decision = "REVIEW"
        reason = "category 미분류"

        # 1. OPM 기본 logic으로 fallback decision 산출
        if category == "director_election" or category == "audit_committee_election":
            matched_eval = None
            for nm, ev in name_to_eval.items():
                if nm and nm in title:
                    matched_eval = ev
                    break
            decision, reason = _decide_director_election(matched_eval)
        elif category == "director_compensation":
            decision, reason = _decide_compensation(meeting_comp)
        elif category == "financial_statements":
            decision, reason = _decide_financial_statements(fin_metrics)
        elif category == "cash_dividend":
            decision, reason = _decide_dividend(title, fin_metrics)
        elif category == "articles_amendment":
            decision, reason = _decide_articles_amendment(title)
        elif category == "treasury_share":
            decision, reason = _decide_treasury_share(title)
        else:
            decision = "REVIEW"
            reason = f"안건 카테고리 '{category}' — 정책 미정의 또는 본문 검토 필요"

        # 2. vote_style 정책 default가 명확하면 (for / against / review) 그걸 우선
        # case_by_case면 OPM fallback 결정 유지.
        policy_default = _policy_default(policy, category)
        original_decision, original_reason = decision, reason
        decision, reason = _apply_policy_default(policy_default, decision, reason)

        # 3. 정책 근거 명시 (vote_style + 운용사명 + 카테고리 default)
        policy_basis = f"{policy_id}"
        if policy_meta.get("manager_name"):
            policy_basis = f"{policy_meta['manager_name']} ({policy_id})"
        if policy_default and policy_default != "case_by_case":
            policy_basis += f" / {category}.default={policy_default}"
        else:
            policy_basis += f" / case_by_case → OPM fallback"

        agenda_decisions.append({
            "agenda_title": title,
            "agenda_category": category,
            "decision": decision,
            "reason": reason,
            "policy_basis": policy_basis,
            "policy_default": policy_default,
            "opm_fallback_decision": original_decision if (policy_default and policy_default != "case_by_case") else None,
            "evidence_rcept_no": (meeting_summary.get("data") or {}).get("rcept_no") or director_data.get("rcept_no"),
        })

    # 통합 evidence_refs
    evidence: list[EvidenceRef] = []
    for upstream_payload, label in [
        (meeting_summary, "주주총회소집공고"),
        (director_eval, "후보 평가"),
        (fin_metrics, "재무지표"),
        (gov_report, "거버넌스 보고서"),
    ]:
        for ref in (upstream_payload.get("evidence_refs") or [])[:2]:
            evidence.append(EvidenceRef(
                evidence_id=ref.get("evidence_id", ""),
                source_type=ref.get("source_type", SourceType.DART_API),
                rcept_no=ref.get("rcept_no", ""),
                section=ref.get("section", label),
                note=ref.get("note", ""),
            ))

    # filing meta
    n_decisions = len(agenda_decisions)
    filing_meta = build_filing_meta(filing_count=n_decisions, parsing_failures=0)
    if filing_meta["no_filing"]:
        status = AnalysisStatus.NO_FILING
    else:
        status = AnalysisStatus.EXACT

    return ToolEnvelope(
        tool="advise_vote_before_meeting",
        status=status,
        subject=selected.get("corp_name", company_query),
        warnings=[],
        data={
            "query": company_query,
            "company_id": _company_id(selected),
            "canonical_name": selected.get("corp_name"),
            "year": target_year,
            "meeting_type": meeting_type,
            "vote_style": vote_style,
            "vote_style_policy_id": policy_id,
            "vote_style_resolved": bool(policy),
            "vote_style_manager_name": policy_meta.get("manager_name") if policy else None,
            "marco_enabled": enable_marco,
            "agenda_count": len(agenda_titles),
            "agenda_decisions": agenda_decisions,
            "candidates_count": len(director_evals),
            "candidates_evaluations": director_evals,
            "ownership_summary": (ownership.get("data") or {}).get("summary"),
            "governance_summary": (gov_report.get("data") or {}).get("summary"),
            "financial_summary": (fin_metrics.get("data") or {}).get("summary"),
            **filing_meta,
            "usage": build_usage(client.api_call_snapshot() - calls_start),
        },
        evidence_refs=evidence,
    ).to_dict()
