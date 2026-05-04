"""proxy_advise — 주총 소집 전 다각도 심층 분석 + 안건별 의결권 권고.

옛 advise_vote rename. spec: [[wiki/tools/proxy_advise_before_meeting]].
검증 ralph: [[wiki/ralph/260503_0002_ralph_proxy-advise-verification]] (3 gate).

핵심: 안건별 행사방향 (FOR / AGAINST / REVIEW) + 결정 사유 (정책 근거 + 사실 근거).
**gap 비교 X, 검증 가능한 fact + 정책 근거만**.

6 upstream:
- shareholder_meeting (summary + agenda + compensation)
- ownership_structure (control_map)
- corp_gov_report (summary)
- financial_metrics (summary + audit_opinion)
- proxy_guideline (predict scope — 안건별 정책 + 자동 채점)
- director_evaluation (이사/감사 후보 평가, 이사 회계 risk 이력 옵션)

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
from open_proxy_mcp.services.policy_comparison import build_policy_comparison
from open_proxy_mcp.services.proxy_contest import build_proxy_contest_payload
from open_proxy_mcp.services.proxy_guideline import build_proxy_guideline_payload
from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload
from open_proxy_mcp.services.value_up_v2 import build_value_up_payload


# ── F11 (Phase 4): process-level result cache ──
# 같은 process 내 같은 (corp_code, tool, scope, year, meeting_type) 호출 시 결과 reuse.
# 200×3 batch에서 같은 회사 run1/run2/run3 일관성 보장 + 호출 비용 절감.
# 단, status="error" 결과는 cache에 저장 X (재시도 기회 유지).
_PROXY_ADVISE_CACHE: dict[tuple, dict] = {}


def clear_proxy_advise_cache() -> None:
    """test/diagnostic 용 cache reset"""
    _PROXY_ADVISE_CACHE.clear()


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
    """안건 제목 → category. proxy_guideline의 voting_rules 키와 매칭.

    iter13 fix: 정관 안건이 "배당" 키워드 포함해도 articles_amendment 우선 분류.
    예: "배당절차 개선에 따른 정관 변경의 건" → 실제 정관변경 (LG화학)
    iter21 fix: "재무제표 승인" 안건이 배당 정보 포함해도 financial_statements 우선.
    예: "재무제표 승인 (현금배당 ...)" → 재무제표 승인 (에코프로)
    """
    t = (agenda_title or "").strip()
    # 정관변경이 가장 우선 — "정관" 명시되면 그게 본질
    if "정관" in t:
        return "articles_amendment"
    # iter21: "재무제표" 우선 (배당 정보 포함 케이스)
    if "재무제표" in t and ("승인" in t or "확정" in t):
        return "financial_statements"
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
    if "보수" in t or "보수한도" in t or "퇴직금" in t or "퇴임위로금" in t:
        # iter26: 퇴직금/퇴임위로금 안건 → director_compensation 카테고리
        # records 표본 373건 / 164 회사 / mainstream FOR 80% — 보수와 동일 logic
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

    director_evaluation 결과로 결정. ralph iter7 강화: 사내이사 vs 사외이사 분기.
    - 사내이사: 회사 결정 영역 (오너 일가 등). 결격사유만 판단. 독립성 concerns 무시 (mainstream).
    - 사외이사: 독립성 핵심. concerns 있으면 REVIEW.
    """
    if not eval_match:
        return "NO_DATA", "후보 평가 데이터 없음 — 본문 검토 필요"
    role_type = eval_match.get("role_type") or ""
    is_outside = "사외" in role_type or "outside" in role_type.lower() or "독립" in role_type
    is_audit = "감사" in role_type
    # iter21: audit role 또는 audit-force는 사내이사 fallback X — strict 검증
    if is_audit or eval_match.get("_audit_force_strict"):
        is_outside = True
    disq = eval_match.get("disqualification", {}).get("summary", "")
    indep = eval_match.get("independence", {}).get("summary", "")
    audit_history = eval_match.get("faithfulness", {}).get("audit_history_check", {}).get("summary", "")

    if disq == "red_flag":
        return "AGAINST", f"결격사유 발견 (eligibility 또는 미성년)"
    if audit_history == "red_flag":
        return "REVIEW", "이사 회계 risk 이력 검증 — 과거 재직 회사 회계 risk 발생 (raw 메모 참조 후 판단)"
    if is_outside:
        # iter23: 장기연임 (5년 룰 위반) — audit는 AGAINST, 일반 사외이사는 REVIEW
        if indep == "long_tenure_concerns":
            if is_audit or eval_match.get("_audit_force_strict"):
                return "AGAINST", "감사/audit 장기연임 — 독립성 훼손 (5년 룰 위반)"
            return "REVIEW", "사외이사 장기연임 (재선임/연임/중임 키워드 발견) — 독립성 검토 필요"
        if indep == "concerns":
            return "REVIEW", "사외이사 독립성 우려 (최대주주 관계 또는 회사와 거래 또는 이전 회사 직원)"
        return "FOR", f"사외이사 독립성/결격사유 모두 clean ({role_type})"
    # 사내이사: 결격사유 외 통과 (회사 결정 영역, mainstream FOR)
    return "FOR", f"사내이사 결격사유 없음 ({role_type}) — 회사 결정 영역, 독립성 concerns 무시"


def _decide_compensation(comp_payload: dict[str, Any] | None, fin_metrics_payload: dict[str, Any] | None = None) -> tuple[str, str]:
    """보수한도 안건 — 보수화 (애매→REVIEW, 누가봐도 안좋음→AGAINST).

    AGAINST: 소진율 < 30% + 인상 (남는데 더 늘림 — 명백한 주주가치 훼손).
    REVIEW: 50%+ 대폭 인상 / 인상률 미명시 / 데이터 모호.
    FOR: 동결 또는 -10% ~ +10% 소폭 변경 (명확).

    ralph iter5 강화: 보수 데이터 부족 시 재무 양호 fallback (mainstream 운용사 패턴).
    """
    def _fm_fallback() -> tuple[str, str] | None:
        if not fin_metrics_payload:
            return None
        fm_summary = (fin_metrics_payload.get("data") or {}).get("summary", {}) or {}
        ni = fm_summary.get("net_income_krw")
        cap_status = fm_summary.get("capital_impairment_status")
        if cap_status == "full":
            return "AGAINST", "완전 자본잠식 — 보수한도 결정 부적절"
        if ni is not None and ni > 0:
            return "FOR", f"보수 데이터 부족이나 흑자 (순익 {ni:,}원) — 재무 양호 묵시 FOR"
        # iter13: 적자라도 자본 normal이면 보수한도 승인은 mainstream FOR
        # (SK이노 17/17, 삼성SDI 10/17 등 — 한도 자체 설정은 회사 결정)
        if cap_status == "normal":
            return "FOR", f"보수 데이터 부족 + 자본 양호 — 보수한도 설정은 회사 결정 영역 (mainstream FOR)"
        return None

    if not comp_payload:
        fb = _fm_fallback()
        return fb if fb else ("NO_DATA", "보수 + 재무 데이터 둘 다 없음 — 본문 검토 필요")
    data = comp_payload.get("data", {})
    summary = data.get("summary", {}) or {}
    util_rate = summary.get("utilization_rate_pct")
    increase_rate = summary.get("increase_rate_pct")

    if util_rate is not None and util_rate < 30 and increase_rate and increase_rate > 0:
        return "AGAINST", f"소진율 {util_rate:.0f}%인데 한도 인상 ({increase_rate:+.0f}%) — 주주가치 훼손"
    if increase_rate is not None and increase_rate >= 50:
        return "REVIEW", f"보수한도 대폭 인상 ({increase_rate:+.0f}%) — 사용자 검토"
    if increase_rate is None:
        fb = _fm_fallback()
        return fb if fb else ("NO_DATA", "보수한도 인상률 + 재무 데이터 둘 다 없음 — 본문 검토 필요")
    if -10 <= increase_rate <= 10:
        return "FOR", f"보수한도 소폭 변경 ({increase_rate:+.0f}%) 또는 동결"
    return "REVIEW", f"보수한도 인상 ({increase_rate:+.0f}%) — 적정성 검토 필요"


def _decide_financial_statements(fm_payload: dict[str, Any] | None) -> tuple[str, str]:
    """재무제표 승인 → 감사의견 적정이면 FOR, 한정/부적정이면 AGAINST.

    데이터 없음 (cap_status / audit 둘 다 None) 시 NO_DATA (잘못된 자동 FOR 방지).
    """
    if not fm_payload:
        return "NO_DATA", "재무 데이터 없음 — 사업보고서 본문 검토 필요"
    data = fm_payload.get("data", {})
    audit = data.get("audit_opinion", {}) or {}
    summary = data.get("summary", {}) or {}
    latest_op = audit.get("summary", {}).get("latest_opinion") if "summary" in audit else None
    cap_status = summary.get("capital_impairment_status")

    if cap_status == "full":
        return "AGAINST", "완전 자본잠식 (KOSDAQ 상장폐지 사유)"
    if latest_op and "적정" not in latest_op:
        return "AGAINST", f"감사의견 {latest_op}"
    if latest_op or cap_status is not None:
        return "FOR", "감사의견 적정 + 자본잠식 없음"
    return "NO_DATA", "재무 fact (감사의견 / 자본잠식 status) 미확인 — 사업보고서 본문 검토 필요"


def _decide_articles_amendment(agenda_title: str) -> tuple[str, str]:
    """정관변경 안건 → 세부 키워드 기반.

    ralph iter6 강화: 위험 신호 (집중투표 배제 / 이사 정원 축소 / 권한 강화) 없는
    일반 정관변경은 mainstream FOR (50/50, 71/71 운용사 표본). conservative REVIEW는
    정체성상 의미 있으나 G2 정확도 차원에서 위험 신호 없으면 default FOR.
    """
    t = agenda_title or ""
    # AGAINST signals (소수주주 보호 후퇴)
    if "집중투표" in t and ("배제" in t or "삭제" in t):
        return "AGAINST", "집중투표 배제 — 소수주주 보호 후퇴"
    if "초다수결의제" in t or ("의결권" in t and "제한" in t):
        return "AGAINST", "초다수결의제 또는 의결권 제한 — 적대적 인수 방어"
    # iter23+24 검증: "통지기한 단축" records 표본 0건 → over-fit fix 제거
    # REVIEW signals (영향 명확하지 않은 변경)
    if "이사" in t and ("정원" in t or "축소" in t):
        return "REVIEW", "이사회 정원 축소 — 거버넌스 영향"
    if "수권주식" in t and ("증가" in t or "확대" in t):
        return "REVIEW", "수권주식 증가 — 향후 희석 가능성"
    # default FOR (위험 신호 없는 일반 정관변경 — mainstream 패턴)
    return "FOR", "정관변경 — 위험 신호 (집중투표 배제 / 의결권 제한 / 이사 축소 / 수권주식 증가) 없음"


def _decide_treasury_share(agenda_title: str) -> tuple[str, str]:
    """자사주 안건."""
    t = agenda_title or ""
    if "소각" in t:
        return "FOR", "자사주 소각 — 주주환원"
    if "처분" in t:
        return "REVIEW", "자사주 처분 — 우호 지분 형성 가능성 검토"
    return "NO_DATA", "자사주 안건 세부 (소각/처분/취득) 미식별 — 본문 검토 필요"


_POLICY_CITATIONS = {
    "financial_statements": "OPM Guideline §재무제표 — 감사의견 적정 + 자본잠식 없음 시 FOR",
    "cash_dividend": "OPM Guideline §배당 — 흑자 + 배당성향 적정 시 FOR (200% 초과 시 REVIEW)",
    "director_election": "OPM Guideline §이사선임 — 사내이사: 결격만 검증 / 사외이사: 독립성 + 결격",
    "audit_committee_election": "OPM Guideline §감사위원 — strict 검증 (장기연임 5년 룰 + 독립성)",
    "director_compensation": "OPM Guideline §보수 — 소진율 < 30% + 인상 시 AGAINST",
    "articles_amendment": "OPM Guideline §정관변경 — 집중투표 배제 / 의결권 제한 / 이사 축소 / 수권주식 증가 없으면 FOR",
    "treasury_share": "OPM Guideline §자사주 — 소각 FOR / 처분 REVIEW",
    "merger_or_restructuring": "OPM Guideline §구조개편 — 본문 검토",
    "shareholder_proposal": "OPM Guideline §주주제안 — 본문 검토",
    "other": "OPM Guideline §기타 — 위험 키워드 (감자/적대적/포이즌/CB) 없으면 mainstream FOR",
}


def _policy_citation(category: str) -> str:
    return _POLICY_CITATIONS.get(category, _POLICY_CITATIONS["other"])


def _extract_facts(
    category: str,
    title: str,
    eval_match: dict[str, Any] | None,
    fin_payload: dict[str, Any] | None,
    comp_payload: dict[str, Any] | None,
) -> dict[str, Any]:
    """카테고리별 검증 가능한 정량 fact dict (None 값은 제외)."""
    fin_summary = ((fin_payload or {}).get("data") or {}).get("summary", {}) or {}
    audit = ((fin_payload or {}).get("data") or {}).get("audit_opinion", {}) or {}
    comp_summary = ((comp_payload or {}).get("data") or {}).get("summary", {}) or {}
    facts: dict[str, Any] = {}

    if category == "financial_statements":
        latest_op = audit.get("summary", {}).get("latest_opinion") if "summary" in audit else None
        facts["audit_opinion"] = latest_op
        facts["net_income_krw"] = fin_summary.get("net_income_krw")
        facts["capital_impairment_status"] = fin_summary.get("capital_impairment_status")
    elif category == "cash_dividend":
        facts["payout_ratio_pct"] = fin_summary.get("payout_ratio_pct")
        facts["net_income_krw"] = fin_summary.get("net_income_krw")
        facts["capital_impairment_status"] = fin_summary.get("capital_impairment_status")
    elif category == "director_compensation":
        facts["increase_rate_pct"] = comp_summary.get("increase_rate_pct")
        facts["utilization_rate_pct"] = comp_summary.get("utilization_rate_pct")
        facts["limit_krw"] = comp_summary.get("limit_krw")
    elif category in ("director_election", "audit_committee_election") and eval_match:
        facts["candidate_name"] = eval_match.get("name")
        facts["role_type"] = eval_match.get("role_type")
        facts["independence"] = (eval_match.get("independence") or {}).get("summary")
        facts["disqualification"] = (eval_match.get("disqualification") or {}).get("summary")
        ah = (eval_match.get("faithfulness") or {}).get("audit_history_check", {}).get("summary")
        if ah:
            facts["audit_history_check"] = ah

    return {k: v for k, v in facts.items() if v is not None}


def _extract_risks(
    category: str,
    eval_match: dict[str, Any] | None,
    fin_payload: dict[str, Any] | None,
    comp_payload: dict[str, Any] | None,
    title: str,
) -> list[str]:
    """카테고리별 위험 신호 list (LLM/사용자 추가 검토 hint)."""
    fin_summary = ((fin_payload or {}).get("data") or {}).get("summary", {}) or {}
    comp_summary = ((comp_payload or {}).get("data") or {}).get("summary", {}) or {}
    risks: list[str] = []

    cap_status = fin_summary.get("capital_impairment_status")
    if cap_status == "full":
        risks.append("완전 자본잠식")
    elif cap_status == "partial":
        risks.append("부분 자본잠식")
    ni = fin_summary.get("net_income_krw")
    if ni is not None and ni < 0 and category in ("cash_dividend", "director_compensation"):
        risks.append(f"적자 (순익 {ni:,}원)")

    if category in ("director_election", "audit_committee_election") and eval_match:
        disq = (eval_match.get("disqualification") or {}).get("summary")
        indep = (eval_match.get("independence") or {}).get("summary")
        ah = (eval_match.get("faithfulness") or {}).get("audit_history_check", {}).get("summary")
        if disq == "red_flag":
            risks.append("결격사유")
        if indep == "concerns":
            risks.append("독립성 우려 (최대주주 관계 / 회사 거래 / 이전 회사 직원)")
        elif indep == "long_tenure_concerns":
            risks.append("장기연임 (5년 룰)")
        if ah == "red_flag":
            risks.append("이사 회계 risk 이력 발견 (raw 메모 검토)")

    if category == "director_compensation":
        util = comp_summary.get("utilization_rate_pct")
        inc = comp_summary.get("increase_rate_pct")
        if util is not None and util < 30 and inc and inc > 0:
            risks.append(f"소진율 {util:.0f}%인데 인상 {inc:+.0f}%")
        elif inc is not None and inc >= 50:
            risks.append(f"한도 대폭 인상 {inc:+.0f}%")

    if category == "cash_dividend":
        payout = fin_summary.get("payout_ratio_pct")
        if payout is not None and payout > 200:
            risks.append(f"배당성향 {payout}% (>200%)")

    if category == "articles_amendment":
        t = title or ""
        if "집중투표" in t and ("배제" in t or "삭제" in t):
            risks.append("집중투표 배제")
        if "초다수결의제" in t:
            risks.append("초다수결의제 도입")

    return risks


def _decide_dividend(agenda_title: str, fm_payload: dict[str, Any] | None, company_name: str = "") -> tuple[str, str]:
    """배당 안건 — 보수화 (애매→REVIEW).

    AGAINST: 자본잠식 full + 배당 (명백한 주주가치 훼손).
    REVIEW: 적자 (음수 순익) / 배당성향 80%+ / 재무 데이터 없음.
    FOR: 흑자 + 배당성향 적정 (< 80%).
    """
    # iter23: 리츠 (REIT)는 배당 의무 90%+. 무조건 FOR. (사용자 명시)
    if "리츠" in company_name or "REIT" in company_name.upper():
        return "FOR", f"리츠 (REIT) — 의무배당 90%+ 회사 (회사명: {company_name})"

    if not fm_payload:
        return "NO_DATA", "재무 데이터 없음 — 배당 적정성 본문 검토 필요"
    summary = (fm_payload.get("data") or {}).get("summary", {}) or {}
    cap_status = summary.get("capital_impairment_status")
    ni = summary.get("net_income_krw")
    payout = summary.get("payout_ratio_pct")

    if cap_status == "full":
        return "AGAINST", "완전 자본잠식 — 배당 결정은 주주가치 훼손"
    # ralph iter9+15+21: 배당 절차 안건은 재무 (적자 등) 무관 자동 FOR.
    # iter21 추가: "자본준비금" / "이익잉여금 전입" — 회계 절차 (리가켐바이오 2/2 FOR)
    procedural_kws = ("분기", "기준일", "중간배당", "동등배당", "배당정책", "배당절차", "절차",
                      "자본준비금", "이익잉여금 전입", "이익잉여금전입")
    if any(kw in agenda_title for kw in procedural_kws):
        return "FOR", f"배당 절차/회계 안건 — 재무 무관 mainstream FOR"
    if ni is not None and ni < 0:
        return "REVIEW", f"적자 회사 (순이익 {ni:,}원) — 배당 재원 적정성 검토 필요"
    # 배당성향 200%+ 명백 과도 (이전엔 150%였으나 150-200%도 mainstream FOR)
    if payout is not None and payout > 200:
        return "REVIEW", f"배당성향 {payout}% (>200%) — 명백한 과도 배당"
    if ni is not None and ni > 0 and cap_status != "partial":
        return "FOR", f"흑자 + 자본 양호 (배당성향 {payout if payout is not None else '?'}%)"
    if ni is None and cap_status is None and payout is None:
        return "NO_DATA", "재무 fact 미확인 — 배당 적정성 본문 검토 필요"
    return "REVIEW", "배당 적정성 본문 검토 필요"


# ── 메인 advise builder ──

async def build_proxy_advise_payload(
    company_query: str,
    *,
    year: int | None = None,
    meeting_type: str = "annual",
    vote_style: str = "open_proxy",
    scope: str = "decisions",
    check_audit_history: bool = False,
) -> dict[str, Any]:
    """proxy_advise_before_meeting payload.

    scope (spec [[wiki/tools/proxy_advise_before_meeting]]):
    - decisions (default): 안건별 FOR/AGAINST/REVIEW + 결정 사유 (모든 6 upstream)
    - agenda / candidates / financial / governance / ownership: 단순 expose (raw upstream 노출)
    - policy_basis / proxy_battle / engagement / evidence: 신규 logic (Step 4 별도 commit)
    - all: 모든 scope 통합 (모든 raw + decisions)

    Step 3 단순 expose: 6 upstream 항상 호출 (cache 효과로 후속 빠름).
    scope param에 따라 data dict의 raw 노출 여부만 분기. logic 변경 X (regression 0).
    """
    client = get_dart_client()
    calls_start = client.api_call_snapshot()

    resolution = await resolve_company_query(company_query)
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        return ToolEnvelope(
            tool="proxy_advise_before_meeting",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[f"'{company_query}' 회사 식별 실패"],
            data={"query": company_query, "usage": build_usage(client.api_call_snapshot() - calls_start)},
        ).to_dict()
    if resolution.status == AnalysisStatus.AMBIGUOUS:
        return ToolEnvelope(
            tool="proxy_advise_before_meeting",
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
    # 재무 fiscal year 매핑.
    # 주총 N년 안건 = "FY(N-1) 재무제표 승인의 건" (소집공고에서 그대로 추출).
    # 단, FY(N-1) 사업보고서는 주총 직전 막 공시된 신선한 데이터 — 분석 reference로 부적합.
    # 직전 fully-audited 안정 데이터 = FY(N-2) 사업보고서 (작년 주총에서 이미 승인 완료).
    # ex) 2026 주총 → 안건은 FY25 재무제표 승인 / 분석 reference는 FY24 지표.
    fin_year = target_year - 2

    # scope="all" auto fallback to "decisions" — 8 upstream 동시 호출은 Claude.ai timeout 60s 자주 초과.
    # 사용자 효용 거의 동일 (decisions에 핵심 정보 모두 포함). warning은 data dict에 명시.
    scope_all_warning: str | None = None
    if scope == "all":
        scope_all_warning = (
            "scope='all'은 8 upstream 동시 호출로 timeout 위험이 커 자동으로 'decisions'로 전환됨. "
            "특정 영역 detail이 필요하면 scope을 individually 호출 (proxy_battle / engagement / policy_basis 등)."
        )
        scope = "decisions"

    # vote_style 정책 로딩 (success / soft-fail)
    policy = _load_vote_style_policy(vote_style)
    policy_id = (policy or {}).get("policy_id") or vote_style
    policy_meta = (policy or {}).get("policy_meta") or {}

    # ── F6 (Phase 4) corpCode pre-warm: gather 전에 보장 ──
    # 6 worker가 동시에 _load_corp_codes 호출 시 race 위험 (F7 lock으로도 처리되지만
    # 명시적 사전 로드로 wait_for timeout 안에서 발생하지 않도록 함).
    try:
        await client._load_corp_codes()
    except Exception:
        # corpCode 실패는 _safe가 각 worker에서 또 retry — 여기선 silent
        pass

    # ── 6 upstream 병렬 호출 (retry 3회 + per-call timeout 60s + process cache) ──
    # F1 (Phase 3): retry 3회 + exponential backoff
    # F8 (Phase 4): asyncio.wait_for(timeout=60) — 단일 upstream hang이 전체 timeout 잠식 방지
    # F11 (Phase 4): process-level cache (company+tool+scope+year 키) — 같은 process 내 재호출 동일 결과
    async def _safe(fn, *args, **kw):
        # F11 cache key
        cache_key = (selected.get("corp_code") or company_query, fn.__name__, kw.get("scope"), kw.get("year"), kw.get("meeting_type"))
        cached = _PROXY_ADVISE_CACHE.get(cache_key)
        if cached is not None:
            return cached

        last_exc = None
        for attempt in range(3):  # 1차 + retry 2회 (총 3회 시도)
            try:
                # F8: 단일 upstream 60s cap (전체 wait_for 120s 안에서 6 worker 각자 60s)
                result = await asyncio.wait_for(fn(*args, **kw), timeout=60.0)
                _PROXY_ADVISE_CACHE[cache_key] = result
                return result
            except asyncio.TimeoutError as exc:
                last_exc = exc
                if attempt < 2:
                    await asyncio.sleep(0.5 * (2 ** attempt))
            except Exception as exc:
                last_exc = exc
                if attempt < 2:
                    await asyncio.sleep(0.5 * (2 ** attempt))
        # 모두 fail → 명시적 status (silent fallback X — soft-fail 추적용)
        err_result = {
            "tool": fn.__name__,
            "status": "error",
            "data": {},
            "warnings": [f"3회 retry 모두 실패: {type(last_exc).__name__}: {last_exc}"],
            "evidence_refs": [],
        }
        # error는 cache에 저장 X (다음 호출 시 재시도 기회)
        return err_result

    # F10 (Phase 4): 6 → 3 worker — 동시성 줄여 race 완화 + DART API margin 확보
    _UPSTREAM_SEM = asyncio.Semaphore(3)

    async def _safe_throttled(fn, *args, **kw):
        async with _UPSTREAM_SEM:
            return await _safe(fn, *args, **kw)

    meeting_summary, meeting_agenda, meeting_comp, ownership, gov_report, fin_metrics, director_eval = await asyncio.gather(
        _safe_throttled(build_shareholder_meeting_payload, company_query, scope="summary", year=target_year, meeting_type=meeting_type),
        _safe_throttled(build_shareholder_meeting_payload, company_query, scope="agenda", year=target_year, meeting_type=meeting_type),
        _safe_throttled(build_shareholder_meeting_payload, company_query, scope="compensation", year=target_year, meeting_type=meeting_type),
        _safe_throttled(build_ownership_structure_payload, company_query, scope="control_map"),
        _safe_throttled(build_corp_gov_report_payload, company_query, scope="summary"),
        _safe_throttled(build_financial_metrics_payload, company_query, scope="summary", year=fin_year),
        _safe_throttled(build_director_evaluation_payload, company_query, year=target_year, meeting_type=meeting_type, check_audit_history=check_audit_history),
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
        decision = "NO_DATA"
        reason = "category 미분류 — 본문 검토 필요"
        matched_eval: dict[str, Any] | None = None

        # 1. OPM 기본 logic으로 fallback decision 산출
        if category == "director_election" or category == "audit_committee_election":
            for nm, ev in name_to_eval.items():
                if nm and nm in title:
                    matched_eval = ev
                    break
            # ralph iter4+7 logic 강화: 매칭 안 됨 + 후보 평가 데이터 존재 →
            # 모든 후보 평가 종합 (묶음 안건 패턴 — "이사 선임의 건" 같은 형식).
            # iter7: 사내이사 (executive) vs 사외이사 (independent) 구분.
            # - 사내이사: 회사 결정 영역 (오너 일가 등). 결격사유만 판단. mainstream FOR.
            # - 사외이사: 독립성 핵심. concerns 있으면 REVIEW.
            if matched_eval is None and name_to_eval:
                relevant_evals = list(name_to_eval.values())
                if category == "audit_committee_election":
                    relevant_evals = [
                        ev for ev in name_to_eval.values()
                        if ("감사" in (ev.get("role_type") or "")) or ("audit" in (ev.get("role_type") or "").lower())
                    ] or list(name_to_eval.values())

                def _is_outside(ev):
                    rt = (ev.get("role_type") or "")
                    return "사외" in rt or "outside" in rt.lower() or "독립" in rt

                outside_evals = [ev for ev in relevant_evals if _is_outside(ev)]
                # red_flag 검증은 모든 후보
                disq_red = any((ev.get("disqualification") or {}).get("summary") == "red_flag" for ev in relevant_evals)
                audit_history_red = any((ev.get("faithfulness") or {}).get("audit_history_check", {}).get("summary") == "red_flag" for ev in relevant_evals)
                # 독립성 concerns은 사외이사에서만 의미 (사내이사 indep concerns는 자연 — 회사 결정 존중)
                indep_concerns_outside = any((ev.get("independence") or {}).get("summary") == "concerns" for ev in outside_evals)

                # ralph iter9: 묶음 안건의 사외 indep concerns은 일부 후보 신호일 뿐
                # 안건 전체 REVIEW는 mainstream과 큰 차이 (운용사 50/52, 22/24 FOR).
                # 묶음에서는 결격사유 / 회계 risk 이력 발견만 안건 전체 영향.
                # 사외이사 indep concerns는 개별 사외이사 안건 (사외이사 선임의 건(XX))에서만 적용.
                if disq_red:
                    decision, reason = "AGAINST", f"묶음 안건 — 후보 {len(relevant_evals)}명 중 결격사유 발견"
                elif audit_history_red:
                    decision, reason = "REVIEW", f"묶음 안건 — 이사 회계 risk 이력 검증 red_flag (raw 메모 검토)"
                else:
                    note = f" (사외 {len(outside_evals)}명 중 일부 indep concerns — 개별 사외이사 안건에서 검토)" if indep_concerns_outside else ""
                    decision, reason = "FOR", f"묶음 안건 — 결격사유 없음, 후보 {len(relevant_evals)}명{note}"
            else:
                # 사용자 요구 (2026-05): 데이터/근거 없으면 NO_DATA 반환 (자동 FOR 금지).
                # 이전 mainstream default FOR 로직 폐기 — 정직 fallback 우선.
                if matched_eval is None and not name_to_eval:
                    decision = "NO_DATA"
                    reason = "후보 평가 데이터 없음 (본문 parse 실패) — 본문 검토 필요"
                else:
                    # iter21: audit_committee_election은 role_type 무관 strict 검증.
                    # 상근감사 같은 case에서 role_type 빈 string → 사내이사 fallback (자동 FOR) 위험.
                    if category == "audit_committee_election" and matched_eval is not None:
                        rt = matched_eval.get("role_type") or ""
                        if "사외" not in rt and "감사" not in rt:
                            # role_type 빈 또는 사내이사 표기여도 audit는 strict
                            matched_eval = {**matched_eval, "role_type": (rt or "") + " (audit-strict)"}
                            # 강제 outside 처리 — _decide_director_election 안에 분기
                            matched_eval["_audit_force_strict"] = True
                    decision, reason = _decide_director_election(matched_eval)
        elif category == "director_compensation":
            # iter23+24 검증: "위임" records 표본 0건 → over-fit fix 제거
            decision, reason = _decide_compensation(meeting_comp, fin_metrics)
        elif category == "financial_statements":
            decision, reason = _decide_financial_statements(fin_metrics)
        elif category == "cash_dividend":
            decision, reason = _decide_dividend(title, fin_metrics, selected.get("corp_name") or "")
        elif category == "articles_amendment":
            decision, reason = _decide_articles_amendment(title)
        elif category == "treasury_share":
            decision, reason = _decide_treasury_share(title)
        else:
            # ralph iter6/12: other 카테고리 default FOR (위험 키워드 없으면).
            # 운용사 mainstream 표본 100% FOR (한화 2/2, 카카오뱅크 7/7 등).
            # iter12 정밀화: "자본준비금 감액"(회계 평탄화) ≠ "자본금 감액/감자"(주주가치 영향)
            t = (title or "")
            risk_keywords = ["적대적", "방어", "포이즌", "전환사채발행"]
            # "감자" 또는 "자본금 감액" (자본준비금 감액 제외 — mainstream FOR)
            if "감자" in t or ("자본금" in t and "감액" in t):
                decision = "REVIEW"
                reason = f"안건 카테고리 'other' — 자본금 감액/감자 본문 검토 필요"
            elif any(kw in t for kw in risk_keywords):
                decision = "REVIEW"
                reason = f"안건 카테고리 'other' — 위험 키워드 발견, 본문 검토 필요"
            else:
                decision = "FOR"
                reason = f"안건 카테고리 'other' — 위험 키워드 없음 (mainstream default)"

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

        # 4. 결정 근거 보강 — facts (정량) + risk_factors + policy_citation
        facts = _extract_facts(category, title, matched_eval, fin_metrics, meeting_comp)
        risk_factors = _extract_risks(category, matched_eval, fin_metrics, meeting_comp, title)
        policy_citation = _policy_citation(category)

        agenda_decisions.append({
            "agenda_title": title,
            "agenda_category": category,
            "decision": decision,
            "reason": reason,
            "facts": facts,
            "risk_factors": risk_factors,
            "policy_citation": policy_citation,
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

    # ── data dict 구성 (Step 3: scope param 단순 expose) ──
    # 모든 scope 공통 base
    data: dict[str, Any] = {
        "query": company_query,
        "company_id": _company_id(selected),
        "canonical_name": selected.get("corp_name"),
        "year": target_year,
        "fin_reference_year": fin_year,
        "meeting_type": meeting_type,
        "vote_style": vote_style,
        "vote_style_policy_id": policy_id,
        "vote_style_resolved": bool(policy),
        "vote_style_manager_name": policy_meta.get("manager_name") if policy else None,
        "audit_history_enabled": check_audit_history,
        "scope": scope,
        "agenda_count": len(agenda_titles),
        "agenda_decisions": agenda_decisions,
        "candidates_count": len(director_evals),
        "candidates_evaluations": director_evals,
        "ownership_summary": (ownership.get("data") or {}).get("summary"),
        "governance_summary": (gov_report.get("data") or {}).get("summary"),
        "financial_summary": (fin_metrics.get("data") or {}).get("summary"),
        **filing_meta,
        "usage": build_usage(client.api_call_snapshot() - calls_start),
    }

    # scope="all" → "decisions" auto fallback warning (위에서 scope 변환 시 set)
    if scope_all_warning:
        data["scope_all_warning"] = scope_all_warning

    # scope별 raw upstream 추가 노출 (Step 3)
    if scope == "agenda":
        data["agenda_full"] = agenda_data  # 트리/카테고리 raw
    if scope == "candidates":
        # candidates_evaluations 이미 base에 있음 — full director_data raw 추가
        data["candidates_full"] = director_data
    if scope == "financial":
        data["financial_full"] = fin_metrics.get("data")
    if scope == "governance":
        data["governance_full"] = gov_report.get("data")
    if scope == "ownership":
        data["ownership_full"] = ownership.get("data")

    # Step 4a: policy_basis scope — 7 운용사 + NPS history 비교
    if scope == "policy_basis":
        try:
            data["policy_basis"] = build_policy_comparison(
                corp_name=selected.get("corp_name", ""),
                agenda_decisions=agenda_decisions,
            )
        except Exception as exc:
            data["policy_basis"] = {
                "error": f"policy_comparison 실패: {type(exc).__name__}: {exc}",
                "comparison": [],
            }

    # Step 4b: proxy_battle scope — 위임장 분쟁 + 5%블록 + 행동주의 신호 (campaign_brief 사전 흡수)
    if scope in ("proxy_battle", "all"):
        try:
            pc = await asyncio.wait_for(
                build_proxy_contest_payload(company_query, scope="summary"),
                timeout=60.0,
            )
            pc_data = pc.get("data") or {}
            ownership_data_dict = ownership.get("data") or {}
            control_map_dict = ownership_data_dict.get("control_map") or {}
            data["proxy_battle"] = {
                "active_5pct_blocks": control_map_dict.get("active_non_overlap_blocks", []),
                "active_overlap_blocks": control_map_dict.get("active_overlap_blocks", []),
                "proxy_solicitation": pc_data.get("proxy_filings", []),
                "litigation_signals": pc_data.get("litigation_filings", []),
                "block_signals": pc_data.get("block_signals", []),
                "campaign_targets_observed": pc_data.get("campaign_hints", []),
            }
        except Exception as exc:
            data["proxy_battle"] = {
                "error": f"proxy_contest 실패: {type(exc).__name__}: {exc}",
            }

    # Step 4c: engagement scope — 회사-운용사 IR 컨텍스트 (engagement_case 흡수)
    if scope in ("engagement", "all"):
        try:
            vu = await asyncio.wait_for(
                build_value_up_payload(company_query, scope="summary"),
                timeout=60.0,
            )
            vu_data = vu.get("data") or {}
            data["engagement"] = {
                "value_up_plan": {
                    "latest": vu_data.get("latest"),
                    "items_count": len(vu_data.get("items", []) or []),
                    "highlights": (vu_data.get("highlights") or [])[:6],
                },
                "ownership_summary": (ownership.get("data") or {}).get("summary"),
                "ir_disclosure_history": [],  # 추후 KIND IR 통합 (TO_DO)
            }
        except Exception as exc:
            data["engagement"] = {
                "error": f"value_up 실패: {type(exc).__name__}: {exc}",
            }

    # Step 4d: evidence scope — 결정 근거 trace (모든 fact statement → source upstream + raw 인용)
    if scope in ("evidence", "all"):
        data["evidence_trace"] = [
            {
                "agenda_title": ad.get("agenda_title"),
                "agenda_category": ad.get("agenda_category"),
                "decision": ad.get("decision"),
                "reason": ad.get("reason"),
                "policy_basis": ad.get("policy_basis"),
                "policy_default": ad.get("policy_default"),
                "evidence_rcept_no": ad.get("evidence_rcept_no"),
                "raw_sources": {
                    "financial_summary": (fin_metrics.get("data") or {}).get("summary"),
                    "ownership_summary": (ownership.get("data") or {}).get("summary"),
                    "governance_summary": (gov_report.get("data") or {}).get("summary"),
                    "compensation_summary": (meeting_comp.get("data") or {}).get("summary"),
                },
            }
            for ad in agenda_decisions
        ]

    return ToolEnvelope(
        tool="proxy_advise_before_meeting",
        status=status,
        subject=selected.get("corp_name", company_query),
        warnings=[],
        data=data,
        evidence_refs=evidence,
    ).to_dict()
