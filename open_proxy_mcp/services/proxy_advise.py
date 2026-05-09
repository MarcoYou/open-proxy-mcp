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
from pathlib import Path
from typing import Any

from open_proxy_mcp.dart.client import get_dart_client
from open_proxy_mcp.services.provisional_financial_statement import (
    parse_provisional_financial_statement,
    extract_metrics as _extract_provisional_fs_metrics,
)
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
from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload
from open_proxy_mcp.services.director_performance import compute_performance
from open_proxy_mcp.services.dividend_v2 import build_dividend_payload
from open_proxy_mcp.services.treasury_share import build_treasury_share_payload
# Removed dead imports (archived at wiki/archive/services/):
#   policy_comparison / proxy_guideline / proxy_guideline_scoring


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
# 익명 코드만 accept (운용사/연기금 실명 alias는 보안상 제거 — 2026-05-09)
_VOTE_STYLE_POLICY_FILE = {
    "open_proxy": "open_proxy_v1",
    "m_legacy": "m_legacy_2026-04",  # 최신 2026 정책 우선
    "s_legacy": "s_legacy_2025-04",
    "sa_legacy": "sa_legacy_2025-04",
    "k_legacy": "k_legacy_2025-04",
    "t_activist": "t_activist_2025-04",
    "a_activist": "a_activist_2025-04",
    "b_foreign": "b_foreign_2025-04",
    "c_activist": "c_activist_2026-04",
    "n_pension": "n_pension_2025-03",  # n_pension rename (Phase 4)
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


# ── 법령 layer (1·2·3차 상법 개정 + 정관 우회 시나리오, 260508 신규) ──

_LAW_LAYER_RULES_CACHE: list[dict[str, Any]] | None = None


def _load_law_layer_rules() -> list[dict[str, Any]]:
    """wiki/rules/laws/law_layer_rules.json 로드 (모듈 캐시).

    36 룰 (A1=8 / A2=5 / B1=10 / B2=9 / C=4). priority 오름차순.
    """
    global _LAW_LAYER_RULES_CACHE
    if _LAW_LAYER_RULES_CACHE is not None:
        return _LAW_LAYER_RULES_CACHE
    try:
        # wiki는 repo 루트에 있어 상대 경로로 접근
        repo_root = Path(__file__).resolve().parent.parent.parent
        path = repo_root / "wiki" / "rules" / "laws" / "law_layer_rules.json"
        if not path.exists():
            _LAW_LAYER_RULES_CACHE = []
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        rules = data.get("rules", []) or []
        rules.sort(key=lambda r: r.get("priority", 999))
        _LAW_LAYER_RULES_CACHE = rules
        return rules
    except Exception:
        _LAW_LAYER_RULES_CACHE = []
        return []


def _agenda_pattern_match(title: str, parent: str, pattern: dict[str, Any]) -> bool:
    """agenda title + parent 결합 텍스트에서 pattern 매칭.

    pattern keys:
    - all_of: 전부 포함 (AND)
    - any_of: 하나 이상 포함 (OR)
    - secondary: any_of 안에서 추가 매치 필요 (AND with all_of)
    - secondary_then: secondary 매치 후 추가 매치
    - exclude: 매치하면 false
    - parent_must_contain: parent_title이 이 키워드 포함해야 함 (예: 정관변경 sub-agenda 한정)
    - parent_excludes: parent_title에 이 키워드 있으면 false (예: 정관변경 sub-agenda 제외)
    """
    text = f"{parent} {title}".strip()
    text_clean = text.replace(" ", "")
    parent_clean = (parent or "").replace(" ", "")

    def _has_kw(keywords: list[str]) -> bool:
        return any(kw.replace(" ", "") in text_clean for kw in keywords)

    def _parent_has_kw(keywords: list[str]) -> bool:
        return any(kw.replace(" ", "") in parent_clean for kw in keywords)

    # all_of: 전부 포함
    all_of = pattern.get("all_of") or []
    if all_of and not all(kw.replace(" ", "") in text_clean for kw in all_of):
        return False

    # any_of: 하나 이상
    any_of = pattern.get("any_of") or []
    if any_of and not _has_kw(any_of):
        return False

    # secondary: 추가 매치 (AND)
    secondary = pattern.get("secondary") or []
    if secondary and not _has_kw(secondary):
        return False

    # secondary_then: secondary 매치 후 추가 매치
    secondary_then = pattern.get("secondary_then") or []
    if secondary_then and not _has_kw(secondary_then):
        return False

    # exclude: 매치하면 false
    exclude = pattern.get("exclude") or []
    if exclude and _has_kw(exclude):
        return False

    # parent_must_contain: parent에 이 키워드 없으면 false
    parent_must_contain = pattern.get("parent_must_contain") or []
    if parent_must_contain and not _parent_has_kw(parent_must_contain):
        return False

    # parent_excludes: parent에 이 키워드 있으면 false
    parent_excludes = pattern.get("parent_excludes") or []
    if parent_excludes and _parent_has_kw(parent_excludes):
        return False

    return True


def _applies_to_match(rule: dict[str, Any], corp_total_asset_won: int | None,
                      today_iso: str) -> bool:
    """applies_to 조건 (자산 + 시행일) 매치."""
    applies = rule.get("applies_to") or {}

    # 자산 조건
    min_asset = applies.get("min_asset_won", 0)
    max_asset = applies.get("max_asset_won")
    if min_asset > 0:
        if corp_total_asset_won is None or corp_total_asset_won < min_asset:
            return False
    if max_asset is not None:
        if corp_total_asset_won is None or corp_total_asset_won >= max_asset:
            return False

    # 시행일 조건
    applies_after = applies.get("applies_after")
    if applies_after and today_iso < applies_after:
        return False
    applies_before = applies.get("applies_before")
    if applies_before and today_iso >= applies_before:
        return False

    return True


def _law_layer(
    agenda_title: str,
    parent_title: str = "",
    corp_total_asset_won: int | None = None,
    today_iso: str | None = None,
) -> tuple[str, str, str, str] | None:
    """법령 layer 우선 적용 — vote_style 운용사 정책보다 먼저.

    1차/2차/3차 상법 개정 (2025-2026) 강행규정 + 정관 우회 시나리오.

    Returns:
        (decision, reason, rule_id, law_reference) 또는 None (룰 hit 없음 → 운용사 정책 fallback)

    decision:
        FOR (Layer A1 법 정합)
        AGAINST (Layer A2 법 위반)
        REVIEW (Layer B1·B2 법 테두리 안 우회 의심)
        risk_factors는 별도 처리 (정관 안건 X, ownership 신호)
    """
    if today_iso is None:
        today_iso = date.today().isoformat()

    rules = _load_law_layer_rules()
    if not rules:
        return None

    # Layer C는 정관 안건 분류 X (ownership 신호) — skip
    for rule in rules:
        if rule.get("layer") == "C":
            continue
        if rule.get("decision") == "risk_factors":
            continue

        pattern = rule.get("agenda_pattern") or {}
        if not _agenda_pattern_match(agenda_title, parent_title, pattern):
            continue

        if not _applies_to_match(rule, corp_total_asset_won, today_iso):
            continue

        return (
            rule["decision"],
            rule.get("reason_template", ""),
            rule.get("id", ""),
            rule.get("law_reference", ""),
        )

    return None


# ── 안건별 결정 logic ──

def _classify_agenda(agenda_title: str, parent_title: str = "") -> str:
    """안건 제목 → category. proxy_guideline의 voting_rules 키와 매칭.

    iter13 fix: 정관 안건이 "배당" 키워드 포함해도 articles_amendment 우선 분류.
    예: "배당절차 개선에 따른 정관 변경의 건" → 실제 정관변경 (LG화학)
    iter21 fix: "재무제표 승인" 안건이 배당 정보 포함해도 financial_statements 우선.
    예: "재무제표 승인 (현금배당 ...)" → 재무제표 승인 (에코프로)
    260507 fix: parent에 정관 키워드 있으면 sub 안건은 무조건 articles_amendment.
    예: parent="정관 일부 변경의 건" / title="사외이사 명칭 변경" → director_election 오분류 방지.
    300 회사 audit (KOSPI 200 + KOSDAQ 100)에서 mismatch 607건 (19.3%) 모두 이 패턴.
    """
    t = (agenda_title or "").strip()
    parent = (parent_title or "").strip()
    # 260507 단일 fix: parent가 정관변경이면 sub 안건도 articles_amendment.
    # title 자체에 "정관" 없어도 (사외이사 명칭/감사위원 분리선임/위원회 명칭/배당절차 개선 등)
    # 모두 정관변경 sub 안건이라 articles_amendment 처리.
    if parent and "정관" in parent:
        return "articles_amendment"
    # ralph 260505 코붕이 의견: 한국 회사 관행상 퇴직금/보수는 대부분 정관 일부 변경 형태로 들어옴.
    # → 정관이 본질, _decide_articles_amendment 안에서 amendments raw 보고 위험 detect.
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
    # ralph 260505 17:50: 퇴직금 / 감사 보수한도 분리
    if "퇴직금" in t or "퇴임위로금" in t:
        return "retirement_pay"
    if ("감사" in t and "감사위원" not in t) and ("보수" in t or "보수한도" in t):
        return "audit_compensation"
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
    # 사내이사: 결격사유 외에 재직 중 회사 운영 성과 평가 (status quo 편향 mitigation, ralph 260505)
    perf = (eval_match.get("performance") or {}).get("classification")
    if perf == "bad":
        return "AGAINST", f"사내이사 재직 중 성과 bad — 자본잠식/적자 또는 누적 악화"
    if perf == "weak":
        return "REVIEW", f"사내이사 재직 중 성과 weak — 사용자 검토 필요"
    if perf in ("moderate", "good"):
        return "FOR", f"사내이사 결격 없음 + 재직 성과 {perf} ({role_type})"
    # performance 미평가 (신임 사내이사 — appointment_type=new) → 기존 logic
    return "FOR", f"사내이사 결격사유 없음 ({role_type}) — 신임 또는 평가 미실시"


def _fm_yoy_pct(fm_payload: dict[str, Any] | None) -> float | None:
    """financial_metrics summary에서 순익 yoy 추출.

    260505 ralph precision iter 2: financial_metrics summary scope에 net_income_yoy_pct 직접 노출.
    이전엔 yearly scope만 봐서 summary scope (compensation chain default)에서는 항상 None이었음.
    """
    if not fm_payload:
        return None
    data = fm_payload.get("data") or {}
    summary = data.get("summary") or {}
    return summary.get("net_income_yoy_pct")


def _decide_director_compensation(
    comp_payload: dict[str, Any] | None,
    fin_metrics_payload: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """이사 보수한도 — 13 분기 (hard trigger → 자동 trigger → fallback).

    정책 근거:
    - OPM Open Proxy v1.3 #2 (적자/순익 감소 + 한도 증액 against)
    - OPM #8 (50%+ 인상 against, 일회성 사유 외)
    - N연기금 [별표 1] IV-33① (이사회 안 원칙적 찬성), IV-33② (한도 과다 against)
    - mainstream FOR fallback (records 표본 82.5% FOR)
    """
    fm_summary = ((fin_metrics_payload or {}).get("data") or {}).get("summary", {}) or {}
    cap_status = fm_summary.get("capital_impairment_status")
    ni = fm_summary.get("net_income_krw")
    yoy = _fm_yoy_pct(fin_metrics_payload)

    if not comp_payload:
        # 데이터 부족 fallback
        if cap_status == "full":
            return "AGAINST", "완전 자본잠식 — 보수한도 결정 부적절"  # 분기 12
        if ni is not None and ni > 0:
            return "FOR", f"보수 데이터 부족이나 흑자 (순익 {ni:,}원) — 재무 양호 묵시 FOR"  # 분기 11
        if cap_status == "normal":
            return "FOR", "보수 데이터 부족 + 자본 양호 — 보수한도 설정은 회사 결정 영역 (mainstream FOR)"
        return "NO_DATA", "보수 + 재무 데이터 둘 다 없음 — 본문 검토 필요"  # 분기 13

    summary = (comp_payload.get("data") or {}).get("summary", {}) or {}
    util_rate = summary.get("utilization_rate_pct")
    inc = summary.get("increase_rate_pct")

    # 분기 1: 자본잠식 + 인상
    if cap_status == "full" and inc is not None and inc > 0:
        return "AGAINST", f"완전 자본잠식 + 한도 인상 ({inc:+.0f}%) — OPM Guideline (보수 결정 부적절)"
    # 분기 2: 소진율 < 30% — 단독 강화 (코붕이 의견 260505 ralph precision iter 3)
    # "오바해서 올리거나 사용 안하면서 늘리거나"는 인상 외에도 "남는데 한도 유지" 도 검토 대상
    if util_rate is not None and util_rate < 30:
        if inc is not None and inc > 0:
            return "AGAINST", f"소진율 {util_rate:.0f}%인데 한도 인상 ({inc:+.0f}%) — 주주가치 훼손"
        if inc is None:
            return "REVIEW", f"소진율 {util_rate:.0f}% (낮음) + 인상률 미파악 — 한도 적정성 검토"
        if inc == 0 or (-10 < inc < 0):
            return "REVIEW", f"소진율 {util_rate:.0f}%인데 한도 동결/소폭 변경 ({inc:+.0f}%) — 한도 적정성 검토"
        # inc <= -10 (감액)은 분기 8에서 처리 — 한도 줄이는 건 OK
    # 분기 3: 적자 OR 순익 감소 + 인상 (OPM #2 strict)
    if inc is not None and inc > 0:
        if (ni is not None and ni < 0) or (yoy is not None and yoy < 0):
            ni_label = "적자" if (ni is not None and ni < 0) else f"순익 yoy {yoy:+.0f}%"
            return "AGAINST", f"{ni_label} + 한도 인상 ({inc:+.0f}%) — OPM #2 (경영성과 대비 과다)"
    # 분기 5: 50%+ 인상 (#8)
    if inc is not None and inc >= 50:
        return "REVIEW", f"보수한도 대폭 인상 ({inc:+.0f}%) — OPM #8 (50%+ 인상, 일회성 사유 외)"
    # 분기 4: +10~+30% + 순익 yoy 둔화 (N연기금 IV-33② 보수)
    if inc is not None and 10 < inc < 30 and yoy is not None and yoy < 5:
        return "REVIEW", f"한도 +{inc:.0f}% + 순익 yoy {yoy:+.0f}% (둔화) — N연기금 IV-33② 보수적"
    # 분기 6: +30~+50% 외 (#3-4 미해당)
    if inc is not None and 30 <= inc < 50:
        return "REVIEW", f"한도 +{inc:.0f}% 인상 — 적정성 검토"
    # 분기 7: 소진율 ≥100% + 인상 (한도 부족 정당화)
    if util_rate is not None and util_rate >= 100 and inc is not None and inc > 0:
        return "FOR", f"소진율 {util_rate:.0f}% (한도 초과 사용) + 인상 ({inc:+.0f}%) — 한도 부족 정당화"
    # 분기 8: 한도 감액
    if inc is not None and inc < -10:
        return "FOR", f"한도 감액 ({inc:+.0f}%) — 주주가치 우호"
    # 분기 9: -10 ~ +10 (동결)
    if inc is not None and -10 <= inc <= 10:
        return "FOR", f"보수한도 소폭 변경 ({inc:+.0f}%) — N연기금 IV-33① 원칙적 찬성"
    # 분기 10: +10~+30 + 순익 양호
    if inc is not None and 10 < inc < 30 and (yoy is None or yoy >= 5):
        return "FOR", f"한도 +{inc:.0f}% + 경영성과 양호 — N연기금 IV-33①"
    # 분기 11/13: 인상률 None (compensation parsed but increase_rate missing)
    if inc is None:
        if cap_status == "full":
            return "AGAINST", "완전 자본잠식 + 인상률 미파악 — OPM Guideline"
        if ni is not None and ni > 0:
            return "FOR", f"인상률 미파악이나 흑자 (순익 {ni:,}원) — mainstream fallback"
        if cap_status == "normal":
            return "FOR", "인상률 미파악 + 자본 정상 — mainstream fallback"
        return "NO_DATA", "보수한도 인상률 + 재무 데이터 둘 다 없음 — 본문 검토 필요"
    # default fallback (이론상 도달 X)
    return "REVIEW", f"보수한도 변경 ({inc:+.0f}%) — 적정성 검토"


# 하위 호환 alias (proxy_advise dispatch 등 기존 호출)
_decide_compensation = _decide_director_compensation


def _decide_audit_compensation(
    comp_payload: dict[str, Any] | None,
    fin_metrics_payload: dict[str, Any] | None = None,
    *,
    threshold_low_per_person: int = 50_000_000,   # N연기금 IV-34 과소 임계 (잠정 5천만원/인)
    threshold_high_per_person: int = 100_000_000,  # 잠정 1억원/인
) -> tuple[str, str]:
    """감사 보수한도 — 11 분기.

    정책 근거:
    - N연기금 [별표 1] IV-34: 한도 과소 (감사 충실 업무 훼손) AGAINST
    - s_legacy 패턴: 인상률 ≥+50% (감사 보수 급증 = 경영진 동조 인센티브) AGAINST
    - mainstream FOR (records 11 majority case 모두 FOR)
    """
    fm_summary = ((fin_metrics_payload or {}).get("data") or {}).get("summary", {}) or {}
    cap_status = fm_summary.get("capital_impairment_status")
    ni = fm_summary.get("net_income_krw")

    if not comp_payload:
        if cap_status == "full":
            return "AGAINST", "완전 자본잠식 — 감사 보수한도 결정 부적절"
        if ni is not None and ni > 0:
            return "FOR", f"감사 보수 데이터 부족이나 흑자 (순익 {ni:,}원) — mainstream fallback"
        if cap_status == "normal":
            return "FOR", "감사 보수 데이터 부족 + 자본 양호 — mainstream fallback"
        return "NO_DATA", "감사 보수 + 재무 데이터 둘 다 없음 — 본문 검토 필요"

    data = comp_payload.get("data") or {}
    items = data.get("items") or []
    audit_items = [i for i in items if i.get("target") == "감사"]
    summary = data.get("summary", {}) or {}
    # 감사 분리 데이터 — items의 current.total_amount + current.count 활용 (parser 재집계)
    audit_inc = None
    audit_total = None
    audit_count = None
    audit_per_person = None
    util_rate = None  # 감사 분리 소진율은 parser 추가 작업 필요 (Step 6)

    for it in audit_items:
        cur = it.get("current") or {}
        prior = it.get("prior") or {}
        if cur.get("total_amount"):
            audit_total = cur["total_amount"]
        if cur.get("count"):
            audit_count = cur["count"]
        if prior.get("total_amount") and cur.get("total_amount"):
            try:
                audit_inc = (cur["total_amount"] - prior["total_amount"]) / prior["total_amount"] * 100
            except ZeroDivisionError:
                pass
    if audit_total and audit_count:
        audit_per_person = audit_total / audit_count

    # 분기 1: 자본잠식 + 인상
    if cap_status == "full" and audit_inc is not None and audit_inc > 0:
        return "AGAINST", f"완전 자본잠식 + 감사 한도 인상 ({audit_inc:+.0f}%) — 보수 결정 부적절"
    # 분기 3: 1인당 평균 < threshold_low (N연기금 IV-34 과소)
    if audit_per_person is not None and audit_per_person < threshold_low_per_person:
        return "AGAINST", f"감사 1인당 평균 {audit_per_person/1e8:.2f}억 (< {threshold_low_per_person/1e8:.1f}억) — N연기금 IV-34 (과소, 충실 업무 훼손)"
    # 분기 4: 인상률 ≥+50% + 1인당 평균 > threshold_high (s_legacy 패턴)
    if audit_inc is not None and audit_inc >= 50 and audit_per_person is not None and audit_per_person > threshold_high_per_person:
        return "AGAINST", f"감사 한도 +{audit_inc:.0f}% + 1인당 평균 {audit_per_person/1e8:.2f}억 (>{threshold_high_per_person/1e8:.1f}억) — s_legacy 패턴 (경영진 동조 인센티브 우려)"
    # 분기 5: 인상률 +30~+50% (s_legacy 보수)
    if audit_inc is not None and 30 <= audit_inc < 50:
        return "REVIEW", f"감사 한도 +{audit_inc:.0f}% 인상 — s_legacy 패턴 보수적 검토"
    # 분기 6: 1인당 평균 경계
    if audit_per_person is not None and threshold_low_per_person <= audit_per_person < threshold_high_per_person:
        return "REVIEW", f"감사 1인당 평균 {audit_per_person/1e8:.2f}억 (경계 — {threshold_low_per_person/1e8:.1f}~{threshold_high_per_person/1e8:.1f}억) — 사용자 노출"
    # 분기 7: ±10% (동결)
    if audit_inc is not None and -10 <= audit_inc <= 10:
        return "FOR", f"감사 한도 소폭 변경 ({audit_inc:+.0f}%) — N연기금 IV-34 + mainstream FOR"
    # 분기 8: 1인당 평균 ≥ threshold_high + +10~+30% 인상
    if audit_per_person is not None and audit_per_person >= threshold_high_per_person and audit_inc is not None and 10 < audit_inc < 30:
        return "FOR", f"감사 1인당 평균 {audit_per_person/1e8:.2f}억 (≥{threshold_high_per_person/1e8:.1f}억) + 한도 +{audit_inc:.0f}% — N연기금 IV-34 + mainstream"
    # 분기 9/10: 데이터 부족 fallback
    if audit_inc is None and audit_per_person is None:
        if cap_status == "full":
            return "AGAINST", "감사 보수 데이터 부족 + 자본잠식 — 보수 결정 부적절"
        if ni is not None and ni > 0:
            return "FOR", f"감사 보수 데이터 부족이나 흑자 (순익 {ni:,}원) — mainstream fallback"
    # 분기 11: default
    return "FOR", f"감사 보수한도 — 위험 신호 없음 (변경률 {audit_inc:+.0f}% 또는 1인당 {audit_per_person})"


# 퇴직금 위험 키워드 (Step 0 sample 분석 + OPM Open Proxy v1.3 + N연기금 [별표 1] IV-35)
_RETIREMENT_AGAINST_KEYWORDS_AFTER = (
    "황금낙하산", "Golden Parachute", "golden parachute",
    "경영권 변동", "경영권의 변동", "M&A시", "M&A 시",
)
_RETIREMENT_OUTSIDE_DIRECTOR_KEYWORDS = ("사외이사",)  # OPM #6
_RETIREMENT_REVIEW_KEYWORDS_AFTER = (
    # 진짜 위험 신호만 (sample 분석 결과)
    "지급률", "배수", "특별공로금", "명예퇴직", "전직",
    "비등기임원",  # 대상 확장
    # NB "확정기여형/확정급여형/퇴직연금" 제외 — 단순 퇴직연금 제도 도입은 형식적 (KT&G case)
    # NB "신설" 제외 — 단순 조항 신설 (예: 산정 방법 명시)은 위험 X. Step 5 has_new_clause logic으로 별도 처리.
)
_RETIREMENT_FORMAL_KEYWORDS = (
    "법령", "상법", "개정", "정비", "용어", "명칭", "공시", "반영",
)
_RETIREMENT_FORMAL_AFTER_KEYWORDS = (
    # after 필드에 있어도 형식적 (제도 도입은 단순 정비)
    "확정급여형", "확정기여형", "퇴직연금제도",
)


def _decide_retirement_pay(
    retirement_payload: dict[str, Any] | None,
    fin_metrics_payload: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """퇴직금 규정 변경 안건 — 12 분기.

    정책 근거:
    - N연기금 [별표 1] IV-35: 황금낙하산 원칙적 반대
    - OPM Open Proxy v1.3 #6 (사외이사 퇴직혜택 부여 against)
    - OPM #7 (황금낙하산 정관 도입 against)
    - s_legacy 패턴 (퇴직금 31% AGAINST — 적자 case 등)
    - mainstream FOR (records 표본 80% FOR)
    """
    if not retirement_payload:
        return "NO_DATA", "퇴직금 변경 raw 추출 데이터 없음 — 본문 검토 필요"
    data = retirement_payload.get("data") or retirement_payload  # 직접 dict 들어올 수도
    amendments = data.get("amendments") or []
    fm_summary = ((fin_metrics_payload or {}).get("data") or {}).get("summary", {}) or {}
    cap_status = fm_summary.get("capital_impairment_status")

    if not amendments:
        return "NO_DATA", "퇴직금 안건 본문에서 amendments 추출 실패 (parser miss 또는 단순 정정)"

    # 키워드 hit 검출
    risk_against = []  # 황금낙하산 등
    risk_outside_dir = []  # 사외이사 퇴직금
    risk_review = []  # 지급률 등
    formal_hits = []  # 법령 반영 등

    for a in amendments:
        after = (a.get("after") or "").strip()
        before = (a.get("before") or "").strip()
        reason = (a.get("reason") or "").strip()
        # AGAINST hard trigger
        for kw in _RETIREMENT_AGAINST_KEYWORDS_AFTER:
            if kw in after:
                risk_against.append({"clause": a.get("clause"), "kw": kw})
        # 사외이사 퇴직금 신설 (after에 "사외이사" 등장 + before에 없음)
        for kw in _RETIREMENT_OUTSIDE_DIRECTOR_KEYWORDS:
            if kw in after and kw not in before:
                risk_outside_dir.append({"clause": a.get("clause"), "kw": kw})
        # REVIEW trigger
        for kw in _RETIREMENT_REVIEW_KEYWORDS_AFTER:
            if kw in after:
                risk_review.append({"clause": a.get("clause"), "kw": kw})
        # 형식적 변경
        for kw in _RETIREMENT_FORMAL_KEYWORDS:
            if kw in reason:
                formal_hits.append({"clause": a.get("clause"), "kw": kw})

    # 분기 1: 황금낙하산
    if risk_against:
        kws = ", ".join(sorted({h["kw"] for h in risk_against}))
        return "AGAINST", f"퇴직금 위험 trigger ({kws}) 신설 — N연기금 IV-35 + OPM #7 (원칙적 반대)"
    # 분기 2: 사외이사 퇴직금 신설
    if risk_outside_dir:
        return "AGAINST", "사외이사 퇴직금 신설 — OPM #6 (사외이사 퇴직혜택 부여 against)"
    # 분기 3: 지급률 ≥2배수 인상 (sample-aware)
    # SK하이닉스 sample: 사장 4.0배수, 부사장 3.0배수 — 신설인지 변경인지 판단
    payment_multiplier_signal = False
    for a in amendments:
        after = a.get("after") or ""
        before = a.get("before") or ""
        # 배수 패턴 detect: "X배수" 또는 "X.X" 숫자
        import re as _re
        cur_multipliers = [float(m) for m in _re.findall(r"(\d+\.?\d*)\s*배수?", after)]
        prev_multipliers = [float(m) for m in _re.findall(r"(\d+\.?\d*)\s*배수?", before)]
        if cur_multipliers and prev_multipliers:
            max_cur = max(cur_multipliers)
            max_prev = max(prev_multipliers)
            if max_prev > 0 and max_cur / max_prev >= 2:
                payment_multiplier_signal = True
                break
        elif cur_multipliers and not prev_multipliers and max(cur_multipliers) >= 3:
            # 신설 시 ≥3배수
            payment_multiplier_signal = True
            break
    if payment_multiplier_signal:
        return "AGAINST", "지급률 2배수 이상 인상 또는 신설 (≥3배수) — s_legacy strict 패턴"
    # 분기 4: 자본잠식 + 변경
    if cap_status == "full" and amendments:
        return "REVIEW", f"완전 자본잠식 + 퇴직금 변경 {len(amendments)}건 — 보수적 검토"
    # 분기 5: 퇴직금 한도/규정 신설 (없던 것 신설) — 단, after에 형식적 키워드 (퇴직연금 등)만 있으면 분기 9a로 fall-through
    has_new_clause = any(("신  설" in (a.get("before") or "") or "신설" in (a.get("before") or "")) for a in amendments)
    if has_new_clause:
        # 신설 조항이 단순 퇴직연금 제도 도입이면 형식적 — 9a에서 처리
        new_clauses_only_formal = all(
            (("신  설" in (a.get("before") or "") or "신설" in (a.get("before") or ""))
             and any(fkw in (a.get("after") or "") for fkw in _RETIREMENT_FORMAL_AFTER_KEYWORDS))
            for a in amendments
            if ("신  설" in (a.get("before") or "") or "신설" in (a.get("before") or ""))
        )
        if not new_clauses_only_formal:
            return "REVIEW", f"퇴직금 한도/규정 신설 (신설 조항 {sum(1 for a in amendments if '신설' in (a.get('before') or '') or '신  설' in (a.get('before') or ''))}건) — 경영진 보호 신호"
    # 분기 9a: 퇴직연금 제도 도입 (after 필드 hit + 위험 hit 0) — 형식적 변경
    formal_after_hits = []
    for a in amendments:
        after = a.get("after") or ""
        for kw in _RETIREMENT_FORMAL_AFTER_KEYWORDS:
            if kw in after:
                formal_after_hits.append(kw)
    if formal_after_hits and not risk_review and not risk_against and not risk_outside_dir:
        return "FOR", f"퇴직연금 제도 도입 ({', '.join(sorted(set(formal_after_hits)))}) — 형식적 변경"
    # 분기 9b: 형식적 변경 (법령/상법/개정 등 reason hit + 위험 hit 0)
    if formal_hits and not risk_review:
        return "FOR", f"법령/표현 정비 ({', '.join(sorted({h['kw'] for h in formal_hits}))}) — 형식적 변경"
    # 분기 8: 위험 키워드 hit
    if risk_review:
        kws = ", ".join(sorted({h["kw"] for h in risk_review})[:3])
        return "REVIEW", f"퇴직금 변경 {len(amendments)}건, 위험 키워드 hit {len(risk_review)}건 ({kws}) — 사용자 검토"
    # 분기 10: amendments ≥1, 위험 hit 0
    if amendments:
        return "REVIEW", f"퇴직금 변경 {len(amendments)}건 — 변경 raw 검토 권장"
    # 분기 11: amendments 0
    return "FOR", "퇴직금 단순 정정 (amendments 0건)"


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


def _decide_articles_amendment(
    agenda_title: str,
    retirement_payload: dict[str, Any] | None = None,
    comp_payload: dict[str, Any] | None = None,
    fin_metrics_payload: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """정관변경 안건 → 세부 키워드 기반.

    ralph iter6 강화: 위험 신호 (집중투표 배제 / 이사 정원 축소 / 권한 강화) 없는
    일반 정관변경은 mainstream FOR (50/50, 71/71 운용사 표본). conservative REVIEW는
    정체성상 의미 있으나 G2 정확도 차원에서 위험 신호 없으면 default FOR.
    """
    t = agenda_title or ""
    # 260508: 법령 layer (1·2·3차 상법 개정 + 정관 우회 시나리오) 우선 적용으로 이동.
    # 이 함수는 법령 layer 미매치 시 fallback (운용사 정책 hardcoded 분기).
    # 참조: services/proxy_advise.py:_law_layer + wiki/rules/laws/law_layer_rules.json
    #
    # AGAINST signals (소수주주 보호 후퇴) — 법령 layer 미매치 fallback
    if "집중투표" in t and "배제" in t:
        return "AGAINST", "집중투표 배제 — 소수주주 보호 후퇴 (법령 layer A2-1 미매치 fallback)"
    if "초다수결의제" in t or ("의결권" in t and "제한" in t):
        return "AGAINST", "초다수결의제 또는 의결권 제한 — 적대적 인수 방어"
    # iter23+24 검증: "통지기한 단축" records 표본 0건 → over-fit fix 제거
    # REVIEW signals (영향 명확하지 않은 변경)
    if "이사" in t and ("정원" in t or "축소" in t):
        return "REVIEW", "이사회 정원 축소 — 거버넌스 영향"
    if "수권주식" in t and ("증가" in t or "확대" in t):
        return "REVIEW", "수권주식 증가 — 향후 희석 가능성"
    # ralph 260505 코붕이 의견: 정관 안에 묶인 퇴직금 변경은 amendments raw 보고 위험 detect
    if "퇴직금" in t or "퇴임위로금" in t:
        ret_decision, ret_reason = _decide_retirement_pay(retirement_payload, fin_metrics_payload)
        return ret_decision, f"정관변경 (퇴직금) — {ret_reason}"
    # 정관 안에 묶인 보수한도 변경
    if "보수한도" in t or "보수의 한도" in t:
        if "감사" in t and "감사위원" not in t:
            comp_decision, comp_reason = _decide_audit_compensation(comp_payload, fin_metrics_payload)
            return comp_decision, f"정관변경 (감사 보수한도) — {comp_reason}"
        comp_decision, comp_reason = _decide_director_compensation(comp_payload, fin_metrics_payload)
        return comp_decision, f"정관변경 (이사 보수한도) — {comp_reason}"
    # iter 5 fix: title 키워드 없어도 본문에 퇴직금 amendments raw가 있으면 hybrid 처리
    # (예: "정관 일부 변경의 건" — 모든 정관 변경 amendments 포함, 고려아연 case)
    ret_amends = ((retirement_payload or {}).get("data") or {}).get("amendments") or []
    if ret_amends:
        ret_decision, ret_reason = _decide_retirement_pay(retirement_payload, fin_metrics_payload)
        return ret_decision, f"정관변경 (본문 퇴직금 raw {len(ret_amends)}건 detect) — {ret_reason}"
    # default FOR (위험 신호 없는 일반 정관변경 — mainstream 패턴)
    return "FOR", "정관변경 — 위험 신호 (집중투표 배제 / 의결권 제한 / 이사 축소 / 수권주식 증가 / 퇴직금 / 보수한도) 없음"


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
    "director_compensation": "OPM Guideline §보수 — 소진율 30% 미만 + 인상 시 AGAINST / 적자+인상 시 AGAINST (#2) / 50% 이상 인상 시 REVIEW (#8)",
    "audit_compensation": "N연기금 [별표 1] IV-34 + s_legacy 패턴 — 1인당 평균 과소 시 AGAINST / 50% 이상 인상 + 1인당 평균 과다 시 AGAINST",
    "retirement_pay": "N연기금 [별표 1] IV-35 + OPM #6/#7 — 황금낙하산 신설 시 AGAINST / 사외이사 퇴직금 신설 시 AGAINST / 지급률 2배수 이상 인상 시 AGAINST",
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
    all_evals: list[dict[str, Any]] | None = None,
    fy_raw_from_agenda: dict[str, Any] | None = None,
    retirement_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """카테고리별 검증 가능한 정량 fact dict (None 값은 제외)."""
    fin_summary = ((fin_payload or {}).get("data") or {}).get("summary", {}) or {}
    audit = ((fin_payload or {}).get("data") or {}).get("audit_opinion", {}) or {}
    comp_summary = ((comp_payload or {}).get("data") or {}).get("summary", {}) or {}
    facts: dict[str, Any] = {}

    if category == "financial_statements":
        latest_op = audit.get("summary", {}).get("latest_opinion") if "summary" in audit else None
        facts["audit_opinion"] = latest_op
        facts["fy_prior_net_income_krw_dart"] = fin_summary.get("net_income_krw")  # DART API (확정치)
        facts["capital_impairment_status"] = fin_summary.get("capital_impairment_status")
        # 1번 안건 본문 잠정 재무제표 (provisional, 표 raw에서 추출 — 사업보고서 제출 전)
        if fy_raw_from_agenda and fy_raw_from_agenda.get("extraction_status") in ("success", "partial"):
            for k in ("fy_current_net_income_krw", "fy_prior_net_income_krw",
                      "fy_current_revenue_krw", "fy_prior_revenue_krw",
                      "fy_current_operating_profit_krw", "fy_prior_operating_profit_krw",
                      "fy_current_total_assets_krw", "fy_current_total_liabilities_krw",
                      "fy_current_total_equity_krw"):
                v = fy_raw_from_agenda.get(k)
                if v is not None:
                    facts[k] = v
            facts["fy_raw_extraction_status"] = fy_raw_from_agenda.get("extraction_status")
            facts["fy_raw_scope"] = fy_raw_from_agenda.get("scope_used")
    elif category == "cash_dividend":
        facts["payout_ratio_pct"] = fin_summary.get("payout_ratio_pct")
        facts["net_income_krw"] = fin_summary.get("net_income_krw")
        facts["capital_impairment_status"] = fin_summary.get("capital_impairment_status")
    elif category == "director_compensation":
        facts["increase_rate_pct"] = comp_summary.get("increase_rate_pct")
        facts["utilization_rate_pct"] = comp_summary.get("utilization_rate_pct")
        facts["limit_krw"] = comp_summary.get("limit_krw")
        facts["net_income_krw"] = fin_summary.get("net_income_krw")
        facts["capital_impairment_status"] = fin_summary.get("capital_impairment_status")
    elif category == "audit_compensation":
        # 감사 분리 데이터 (items에서 target == "감사" filter)
        items = ((comp_payload or {}).get("data") or {}).get("items") or []
        audit_items = [i for i in items if i.get("target") == "감사"]
        for it in audit_items[:1]:
            cur = it.get("current") or {}
            prior = it.get("prior") or {}
            facts["audit_total_limit_krw"] = cur.get("total_amount")
            facts["audit_count"] = cur.get("count")
            if cur.get("total_amount") and cur.get("count"):
                facts["audit_per_person_krw"] = cur["total_amount"] // cur["count"]
            if prior.get("total_amount") and cur.get("total_amount"):
                try:
                    facts["audit_increase_rate_pct"] = round(
                        (cur["total_amount"] - prior["total_amount"]) / prior["total_amount"] * 100, 1
                    )
                except ZeroDivisionError:
                    pass
        facts["net_income_krw"] = fin_summary.get("net_income_krw")
        facts["capital_impairment_status"] = fin_summary.get("capital_impairment_status")
    elif category == "retirement_pay":
        amends = ((retirement_payload or {}).get("data") or {}).get("amendments") or []
        facts["amendments_count"] = len(amends)
        if amends:
            # raw 노출 (LLM 판단용) — 처음 5개
            facts["amendments_sample"] = [
                {"clause": a.get("clause"), "before": (a.get("before") or "")[:200], "after": (a.get("after") or "")[:200], "reason": (a.get("reason") or "")[:120]}
                for a in amends[:5]
            ]
        facts["capital_impairment_status"] = fin_summary.get("capital_impairment_status")
    elif category in ("director_election", "audit_committee_election"):
        if eval_match:
            facts["candidate_name"] = eval_match.get("name")
            facts["role_type"] = eval_match.get("role_type")
            facts["agenda_action"] = eval_match.get("agenda_action")
            apt = eval_match.get("appointment_type") or {}
            if isinstance(apt, dict) and apt.get("type"):
                facts["appointment_type"] = apt.get("type")  # new / renewed / ambiguous
                if apt.get("earliest_start"):
                    facts["this_company_since"] = apt.get("earliest_start")
            five_y = ((eval_match.get("independence") or {}).get("sub_factors") or {}).get("five_year_rule", {}).get("result")
            if five_y:
                facts["tenure_status"] = five_y
            facts["independence"] = (eval_match.get("independence") or {}).get("summary")
            facts["disqualification"] = (eval_match.get("disqualification") or {}).get("summary")
            ah = (eval_match.get("faithfulness") or {}).get("audit_history_check", {}).get("summary")
            if ah:
                facts["audit_history_check"] = ah
        elif all_evals:
            # 묶음 안건 — 종합 fact (개별 매칭 X)
            outsiders = sum(1 for e in all_evals if any(k in (e.get("role_type") or "") for k in ("사외", "독립")))
            insiders = len(all_evals) - outsiders
            disq_red = sum(1 for e in all_evals if (e.get("disqualification") or {}).get("summary") == "red_flag")
            apt_new = sum(1 for e in all_evals if (e.get("appointment_type") or {}).get("type") == "new")
            apt_renewed = sum(1 for e in all_evals if (e.get("appointment_type") or {}).get("type") == "renewed")
            apt_amb = len(all_evals) - apt_new - apt_renewed
            facts["total_candidates"] = len(all_evals)
            if insiders or outsiders:
                facts["composition"] = f"사외/독립 {outsiders} + 사내 {insiders}"
            facts["appointment_breakdown"] = f"신임 {apt_new} / 연임 {apt_renewed}" + (f" / 미상 {apt_amb}" if apt_amb else "")
            facts["disqualified_count"] = disq_red

    return {k: v for k, v in facts.items() if v is not None}


def _extract_risks(
    category: str,
    eval_match: dict[str, Any] | None,
    fin_payload: dict[str, Any] | None,
    comp_payload: dict[str, Any] | None,
    title: str,
    retirement_payload: dict[str, Any] | None = None,
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
    if ni is not None and ni < 0 and category in ("cash_dividend", "director_compensation", "audit_compensation", "retirement_pay"):
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

    if category == "retirement_pay":
        amends = ((retirement_payload or {}).get("data") or {}).get("amendments") or []
        if amends:
            risks.append(f"퇴직금 변경 {len(amends)}건 — 변경 raw 검토 권장")
        # 황금낙하산 / 사외이사 키워드 hit 탐지
        for a in amends:
            after = (a.get("after") or "")
            if "황금낙하산" in after or "경영권 변동" in after:
                risks.append("황금낙하산 또는 경영권 변동 special 가산 신설 (N연기금 IV-35 원칙적 반대)")
                break
        for a in amends:
            after = a.get("after") or ""
            before = a.get("before") or ""
            if "사외이사" in after and "사외이사" not in before:
                risks.append("사외이사 퇴직금 신설 (OPM #6 against)")
                break

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

    meeting_summary, meeting_agenda, meeting_comp, meeting_aoi, ownership, gov_report, fin_metrics, director_eval = await asyncio.gather(
        _safe_throttled(build_shareholder_meeting_payload, company_query, scope="summary", year=target_year, meeting_type=meeting_type),
        _safe_throttled(build_shareholder_meeting_payload, company_query, scope="agenda", year=target_year, meeting_type=meeting_type),
        _safe_throttled(build_shareholder_meeting_payload, company_query, scope="compensation", year=target_year, meeting_type=meeting_type),
        # aoi_change scope — B1/B2 hit 안건의 정관 변경 본문 raw (cache hit이라 free, parsing CPU만)
        _safe_throttled(build_shareholder_meeting_payload, company_query, scope="aoi_change", year=target_year, meeting_type=meeting_type),
        _safe_throttled(build_ownership_structure_payload, company_query, scope="control_map"),
        _safe_throttled(build_corp_gov_report_payload, company_query, scope="summary"),
        _safe_throttled(build_financial_metrics_payload, company_query, scope="summary", year=fin_year),
        _safe_throttled(build_director_evaluation_payload, company_query, year=target_year, meeting_type=meeting_type, check_audit_history=check_audit_history),
    )

    # 1번 안건 (재무제표 승인) 잠정 FS 본문 raw — meeting_summary notice.rcept_no로 doc 가져와 파싱
    # 260505 ralph 17:50: 같은 doc에서 퇴직금 amendments도 파싱 (extra DART 호출 없이)
    # 260505 ralph 23:30: parse_fy_from_agm_doc (정규식 텍스트) → parse_provisional_financial_statement (BS4 표) 교체
    fy_raw_from_agenda: dict[str, Any] = {"extraction_status": "no_data"}
    retirement_payload: dict[str, Any] | None = None
    notice_dict = ((meeting_summary.get("data") or {}).get("notice") or {})
    agm_rcept = notice_dict.get("rcept_no") if isinstance(notice_dict, dict) else None
    if agm_rcept:
        try:
            doc = await asyncio.wait_for(client.get_document_cached(agm_rcept), timeout=30.0)
            text = (doc or {}).get("text") or ""
            html = (doc or {}).get("html") or ""
            # 잠정 재무제표 표 파싱 (HTML 표 구조 그대로) + flat metrics 추출
            if html:
                pfs_parsed = parse_provisional_financial_statement(html)
                fy_raw_from_agenda = _extract_provisional_fs_metrics(pfs_parsed)
            # 퇴직금 amendments parse — 본문에 "퇴직금" 키워드 있을 때만
            if html and ("퇴직금" in text or "퇴직금" in html or "퇴임위로금" in text or "퇴임위로금" in html):
                from open_proxy_mcp.tools.parser import parse_retirement_pay_xml
                _ret = parse_retirement_pay_xml(html)
                if _ret and _ret.get("amendments"):
                    retirement_payload = {"data": _ret, "status": "ok", "source_rcept_no": agm_rcept}
        except Exception:
            fy_raw_from_agenda = {"extraction_status": "error"}

    # 안건 리스트 추출 (success 매핑) — 260507: parent_title 함께 추출 (정관 sub-안건 분류용)
    agenda_data = (meeting_agenda.get("data") or {})
    agenda_summary = agenda_data.get("agenda_summary", {}) or {}
    agenda_titles = agenda_summary.get("titles", []) or []
    # shareholder_meeting v2 agenda 미검출 시 director_evaluation의 본문 agenda fallback
    if not agenda_titles:
        fallback_titles = (director_eval.get("data") or {}).get("agenda_titles_fallback", []) or []
        if fallback_titles:
            agenda_titles = fallback_titles

    # parent_title map: title → parent_title (agenda tree에서 추출)
    title_to_parent: dict[str, str] = {}
    def _walk_agenda_tree(items: list, parent: str = "") -> None:
        for it in items or []:
            t = (it.get("title") or "").strip()
            if t:
                title_to_parent[t] = parent
            _walk_agenda_tree(it.get("children", []), parent=t)
    _walk_agenda_tree(agenda_data.get("agendas") or [])

    # 후보 평가 dict — name → eval
    director_data = (director_eval.get("data") or {})
    director_evals = director_data.get("evaluations", []) or []
    name_to_eval: dict[str, dict[str, Any]] = {}
    for ev in director_evals:
        nm = ev.get("name")
        if nm:
            name_to_eval[nm] = ev

    # ── 사내이사 재직 중 성과 매트릭스 (ralph 260505) ──
    # 사내이사 + renewed (또는 inside_director_default fallback) 후보가 있으면
    # 추가로 dividend + treasury_share + financial_metrics yearly fetch → performance compute
    inside_renewed_candidates = [
        ev for ev in director_evals
        if "사내" in (ev.get("role_type") or "")
        and (ev.get("appointment_type") or {}).get("type") == "renewed"
    ]
    if inside_renewed_candidates:
        # 회사 단위 한 번 fetch (모든 사내이사 동일 source 공유)
        # 추가 호출 ~3개 (dividend + treasury + financial yearly)
        perf_div, perf_treas, perf_fin = await asyncio.gather(
            _safe_throttled(build_dividend_payload, company_query, scope="history", years=10),
            _safe_throttled(build_treasury_share_payload, company_query, scope="summary", lookback_months=120),
            _safe_throttled(build_financial_metrics_payload, company_query, scope="yearly", year=fin_year),
        )
        # yearly 데이터 파싱
        roe_yearly: dict[int, float | None] = {}
        leverage_yearly: dict[int, float | None] = {}
        net_income_yearly: dict[int, int | None] = {}
        capital_impairment_status = ((fin_metrics.get("data") or {}).get("summary") or {}).get("capital_impairment_status")
        for row in ((perf_fin.get("data") or {}).get("yearly") or []):
            y = row.get("year")
            if y is None:
                continue
            roe_yearly[y] = row.get("roe_pct")
            leverage_yearly[y] = row.get("debt_ratio_pct")
            net_income_yearly[y] = row.get("net_income_krw")

        # 배당 yearly — history scope의 quarterly_breakdown에서 total_amount_krw 연도별 합산
        # (history scope이 latest_decisions[:20] 노출 + quarterly_breakdown 신규 — 사이클 dedup된 효과)
        dividend_yearly: dict[int, int] = {}
        for q in ((perf_div.get("data") or {}).get("quarterly_breakdown") or []):
            if q.get("is_superseded"):
                continue  # 정정공시 superseded는 제외
            y = q.get("year")
            amt = q.get("total_amount_krw") or 0
            if y and amt:
                dividend_yearly[y] = dividend_yearly.get(y, 0) + amt

        # 소각 yearly (treasury_share events에서 cancelation_decision 합산)
        cancelation_yearly: dict[int, int] = {}
        for e in ((perf_treas.get("data") or {}).get("events") or []):
            if e.get("event") != "cancelation_decision":
                continue
            y = e.get("rcept_dt", "")[:4]
            if y and y.isdigit():
                yi = int(y)
                cancelation_yearly[yi] = cancelation_yearly.get(yi, 0) + (e.get("amount_krw") or 0)

        # 각 사내이사 renewed 후보별 performance compute
        # earliest_start None (career detect fail) 시 default 5년 fallback (낮은 정확도)
        for ev in inside_renewed_candidates:
            apt = ev.get("appointment_type") or {}
            earliest = apt.get("earliest_start") or (target_year - 5)  # fallback: 5년
            tenure_years = list(range(earliest, target_year + 1))
            ev["performance"] = compute_performance(
                tenure_years=tenure_years,
                roe_yearly=roe_yearly,
                leverage_yearly=leverage_yearly,
                net_income_yearly=net_income_yearly,
                dividend_yearly=dividend_yearly,
                cancelation_yearly=cancelation_yearly,
                capital_impairment_status=capital_impairment_status,
            )
            if not apt.get("earliest_start"):
                ev["performance"]["tenure_fallback"] = True
                ev["performance"]["rationale"] = "(재직 시작 detect fail — 5년 default) " + ev["performance"].get("rationale", "")

    # 법령 layer (260508 신규) — 강행규정 + 정관 우회 시나리오. vote_style 위에 우선 적용.
    # corp_total_asset_won: financial_metrics summary에서 자산 추출 (자산 2조+ 분기 등)
    corp_total_asset_won: int | None = None
    try:
        fm_summary_for_law = ((fin_metrics or {}).get("data") or {}).get("summary") or {}
        ta = fm_summary_for_law.get("total_assets_krw")
        if isinstance(ta, (int, float)) and ta > 0:
            corp_total_asset_won = int(ta)
    except Exception:
        corp_total_asset_won = None
    today_iso_for_law = date.today().isoformat()

    # aoi_change scope에서 amendments raw 추출 — B1/B2 hit 시 본문 인용용 (260510 raw 보강)
    aoi_amendments: list[dict[str, Any]] = []
    try:
        aoi_data = (meeting_aoi or {}).get("data") or {}
        aoi_change_raw = aoi_data.get("aoi_change") or {}
        aoi_amendments = aoi_change_raw.get("amendments") or []
    except Exception:
        aoi_amendments = []

    def _find_amendment_for_title(t: str) -> dict[str, Any] | None:
        """안건 title에 해당하는 amendment 매칭. label/reason/before/after 키워드 fuzzy 매칭."""
        if not aoi_amendments or not t:
            return None
        t_clean = t.strip().replace(" ", "")
        # 1. label 직접 매칭
        for am in aoi_amendments:
            label = (am.get("label") or "").strip().replace(" ", "")
            if label and label != "제" and (label in t_clean or t_clean in label):
                return am
        # 2. reason / before / after 본문에서 keyword overlap (3+자 substring)
        # 안건 title의 의미 있는 키워드 추출 (제외: 의/건/안)
        title_keywords = [w for w in t.replace("의 건", "").replace("의건", "").split() if len(w) >= 2]
        if not title_keywords:
            return None
        best_score = 0
        best_am = None
        for am in aoi_amendments:
            haystack = (am.get("reason", "") + " " + am.get("before", "") + " " + am.get("after", ""))
            score = sum(1 for kw in title_keywords if kw in haystack)
            if score > best_score:
                best_score = score
                best_am = am
        # 최소 2 키워드 매칭 (집중투표 + 배제 / 의결권 + 제한 등)
        return best_am if best_score >= 2 else None

    # 안건별 결정 + 사유 (vote_style 정책 wire 적용)
    agenda_decisions: list[dict[str, Any]] = []
    for title in agenda_titles:
        parent_for_title = title_to_parent.get(title, "")
        category = _classify_agenda(title, parent_title=parent_for_title)
        decision = "NO_DATA"
        reason = "category 미분류 — 본문 검토 필요"
        matched_eval: dict[str, Any] | None = None
        law_layer_hit: tuple[str, str, str, str] | None = None

        # 0. 법령 layer 우선 적용 (1·2·3차 상법 개정 + 정관 우회 시나리오)
        # hit 시 운용사 정책 / hardcoded _decide_* 모두 skip → 법 강행규정 일관 적용.
        law_layer_hit = _law_layer(
            title, parent_title=parent_for_title,
            corp_total_asset_won=corp_total_asset_won, today_iso=today_iso_for_law,
        )

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
                # 사내이사 renewed 후보 중 performance 평가 — bad/weak 1명이라도 있으면 안건 영향
                inside_evals = [ev for ev in relevant_evals if "사내" in (ev.get("role_type") or "") and not _is_outside(ev)]
                inside_perf_bad = any((ev.get("performance") or {}).get("classification") == "bad" for ev in inside_evals)
                inside_perf_weak = any((ev.get("performance") or {}).get("classification") == "weak" for ev in inside_evals)

                if disq_red:
                    decision, reason = "AGAINST", f"묶음 안건 — 후보 {len(relevant_evals)}명 중 결격사유 발견"
                elif inside_perf_bad:
                    bad_names = [ev.get("name", "?") for ev in inside_evals if (ev.get("performance") or {}).get("classification") == "bad"]
                    decision, reason = "AGAINST", f"묶음 안건 — 사내이사 재직 성과 bad ({', '.join(bad_names[:3])})"
                elif audit_history_red:
                    decision, reason = "REVIEW", f"묶음 안건 — 이사 회계 risk 이력 검증 red_flag (raw 메모 검토)"
                elif inside_perf_weak:
                    weak_names = [ev.get("name", "?") for ev in inside_evals if (ev.get("performance") or {}).get("classification") == "weak"]
                    decision, reason = "REVIEW", f"묶음 안건 — 사내이사 재직 성과 weak ({', '.join(weak_names[:3])}) — 사용자 검토"
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
            decision, reason = _decide_director_compensation(meeting_comp, fin_metrics)
        elif category == "audit_compensation":
            # ralph 260505 17:50: 감사 보수한도 별도 분기 (N연기금 IV-34)
            decision, reason = _decide_audit_compensation(meeting_comp, fin_metrics)
        elif category == "retirement_pay":
            # ralph 260505 17:50: 퇴직금 별도 분기 (N연기금 IV-35 + OPM #6/#7)
            decision, reason = _decide_retirement_pay(retirement_payload, fin_metrics)
        elif category == "financial_statements":
            decision, reason = _decide_financial_statements(fin_metrics)
        elif category == "cash_dividend":
            decision, reason = _decide_dividend(title, fin_metrics, selected.get("corp_name") or "")
        elif category == "articles_amendment":
            decision, reason = _decide_articles_amendment(
                title,
                retirement_payload=retirement_payload,
                comp_payload=meeting_comp,
                fin_metrics_payload=fin_metrics,
            )
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

        # 1.5. 법령 layer hit 시 우선 적용 — vote_style/hardcoded 위에 (260508)
        if law_layer_hit is not None:
            ll_decision, ll_reason, ll_id, ll_law_ref = law_layer_hit
            decision = ll_decision
            # A1/A2 (강행규정) — LLM이 안건명만 보고 결정 뒤집는 케이스 빈번 → 명시적 anti-misread guard
            if ll_id.startswith("A1-") or ll_id.startswith("A2-"):
                guard = ""
                # 빈번한 misread 패턴별 explicit 설명
                if "배제 조항 삭제" in title or "배제 삭제" in title:
                    guard = " ⛔ LLM 주의: '배제 조항 삭제' = 집중투표 의무화 정합 = FOR. '배제'라는 단어 보고 AGAINST 추측 금지."
                elif "의결권 제한 강화" in title or "의결권 제한 확대" in title:
                    guard = " ⛔ LLM 주의: '의결권 제한 강화' = 합산 3% 룰 (소수주주 보호 강화) = FOR. '제한'이라는 단어 보고 AGAINST 추측 금지."
                elif "독립이사" in title and "명칭" in title:
                    guard = " ⛔ LLM 주의: 사외이사→독립이사 명칭 변경 = 1차 개정 정합 = FOR. 자체 판단 금지."
                elif "전자주주총회" in title:
                    guard = " ⛔ LLM 주의: 전자주총 도입 = 1차 개정 정합 = FOR. 자체 판단 금지."
                else:
                    guard = " ⛔ LLM 주의: 강행규정 정합 — 결정 변경 금지. 안건명 키워드만 보고 추측 금지."
                reason = f"[법령 {ll_id}] {ll_reason} (근거: {ll_law_ref}){guard}"
            else:
                reason = f"[법령 {ll_id}] {ll_reason} (근거: {ll_law_ref})"
            # B1/B2 (REVIEW) — case-by-case 영역. 정관변경 본문 raw 첨부 (LLM 직접 검토 — 260510)
            # A1/A2 (FOR/AGAINST 강행규정)는 결정 명확 — raw 추가 X (토큰 절약)
            if ll_id.startswith("B1-") or ll_id.startswith("B2-"):
                am = _find_amendment_for_title(title)
                if am:
                    before_raw = (am.get("before") or "").strip()
                    after_raw = (am.get("after") or "").strip()
                    if before_raw or after_raw:
                        # raw 첨부 (LLM 본문 직접 검토용 — 결정은 REVIEW 유지)
                        clause = am.get("clause") or "?"
                        raw_excerpt = []
                        if before_raw:
                            raw_excerpt.append(f"[{clause} 변경 전] {before_raw[:400]}")
                        if after_raw:
                            raw_excerpt.append(f"[{clause} 변경 후] {after_raw[:400]}")
                        if raw_excerpt:
                            reason += "\n\n📄 정관 본문 raw (LLM 직접 검토):\n" + "\n".join(raw_excerpt)

        # 2. vote_style 정책 default가 명확하면 (for / against / review) 그걸 우선
        # case_by_case면 OPM fallback 결정 유지.
        # 단 법령 layer hit 시는 vote_style 무시 (강행규정 일관성).
        policy_default = _policy_default(policy, category)
        original_decision, original_reason = decision, reason
        if law_layer_hit is None:
            decision, reason = _apply_policy_default(policy_default, decision, reason)

        # 3. 정책 근거 명시 (vote_style + 운용사명 + 카테고리 default)
        policy_basis = f"{policy_id}"
        if policy_meta.get("manager_name"):
            policy_basis = f"{policy_meta['manager_name']} ({policy_id})"
        if law_layer_hit is not None:
            policy_basis = f"법령 layer (1·2·3차 상법 개정) — {law_layer_hit[2]}"
        elif policy_default and policy_default != "case_by_case":
            policy_basis += f" / {category}.default={policy_default}"
        else:
            policy_basis += f" / case_by_case → OPM fallback"

        # 4. 결정 근거 보강 — facts (정량) + risk_factors + policy_citation
        all_director_evals = list(name_to_eval.values()) if category in ("director_election", "audit_committee_election") else None
        facts = _extract_facts(category, title, matched_eval, fin_metrics, meeting_comp, all_director_evals, retirement_payload=retirement_payload,
                                fy_raw_from_agenda=fy_raw_from_agenda)
        risk_factors = _extract_risks(category, matched_eval, fin_metrics, meeting_comp, title, retirement_payload=retirement_payload)
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

    # 단일 scope (decisions) — 모든 specialized scope 폐지.
    # 사용자가 raw upstream 보고 싶으면 각 tool 직접 호출:
    # - agenda → shareholder_meeting_notice(scope="agenda")
    # - candidates → director_evaluation은 internal, 후보 detail은 본 응답의 candidates_evaluations 활용
    # - financial / governance / ownership → financial_metrics / corp_gov_report / ownership_structure
    # - policy_basis / proxy_battle / engagement → 별도 ralph 또는 사용 시 archive에서 부활

    return ToolEnvelope(
        tool="proxy_advise_before_meeting",
        status=status,
        subject=selected.get("corp_name", company_query),
        warnings=[],
        data=data,
        evidence_refs=evidence,
    ).to_dict()
