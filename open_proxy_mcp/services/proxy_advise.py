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
- (구) predict scope — 안건별 정책 + 자동 채점
- director_evaluation (이사/감사 후보 평가, 이사 회계 risk 이력 옵션)

매핑 분류:
- 안건 리스트 / 후보 / 지분 / 재무 → success (정형)
- 결정 사유 / 후보 약력 → soft-fail (raw text 일부 노출)
- 형사 / 사적 관계 등 → hard-fail (침묵)
"""

from __future__ import annotations

import asyncio
import json
import re
import time
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
from open_proxy_mcp.services.financial_metrics import (
    build_financial_metrics_payload,
    compute_capital_impairment,
    latest_annual_report_before,
)
from open_proxy_mcp.services.ownership_structure import build_ownership_structure_payload
from open_proxy_mcp.services.shareholder_meeting import (
    build_shareholder_meeting_payload,
    resolve_latest_meeting_year,
)
from open_proxy_mcp.services.director_performance import _PERF_KO, compute_performance
from open_proxy_mcp.services.dividend import build_dividend_payload
from open_proxy_mcp.services.treasury_share import build_treasury_share_payload
from open_proxy_mcp.services.order_contracts import build_order_contracts_payload
# Removed dead imports (archived at wiki/archive/services/):
#   (구 백엔드 3종 — private archive)


# ── F11 (Phase 4): process-level result cache ──
# 같은 process 내 같은 (corp_code, tool, scope, year, meeting_type) 호출 시 결과 reuse.
# 200×3 batch에서 같은 회사 run1/run2/run3 일관성 보장 + 호출 비용 절감.
# 단, status="error" 결과는 cache에 저장 X (재시도 기회 유지).
_PROXY_ADVISE_CACHE: dict[tuple, dict] = {}

# 회차 pre-resolution 표기용 (shareholder_meeting._MEETING_TYPE_MAP과 동일 한글 라벨)
_MEETING_TYPE_KO = {"annual": "정기", "extraordinary": "임시", "auto": "정기/임시"}

# ── 안건 유형 표준 섹션 코드 → 카테고리 (260724, 캐시 27건×111회 대조 근거) ──
# DART 편집기가 소집공고·위임장에 자동 기입하는 안건 유형 코드. 20-0(기타)은 권위 없음.
# 5-0(감사의 선임)은 상법상 감사 — 결정 경로는 감사위원과 공유(3%룰·후보검증), 라벨은 citation에서 구분.
_LCODE_CATEGORY = {
    "L0-0-2-1-0": "financial_statements",
    "L0-0-2-2-0": "articles_amendment",
    "L0-0-2-3-0": "director_election",
    "L0-0-2-4-0": "audit_committee_election",
    "L0-0-2-5-0": "audit_committee_election",
    "L0-0-2-9-0": "director_compensation",
    "L0-0-2-10-0": "audit_compensation",
    "L0-0-2-19-0": "capital_reduction",
}
_LCODE_NON_AUTHORITATIVE = {"L0-0-2-20-0"}  # 기타 목적사항 — 무엇이든 담김


_CATEGORY_KO = {
    "financial_statements": "재무제표 승인", "articles_amendment": "정관 변경",
    "director_election": "이사 선임", "audit_committee_election": "감사위원/감사 선임",
    "director_compensation": "이사 보수한도", "audit_compensation": "감사 보수한도",
    "capital_reduction": "자본 감소", "stock_option_grant": "주식매수선택권 부여",
    "other": "기타",
}


def _reconcile_category_with_lcode(
    category: str,
    section_code: str | None,
    section_title: str = "",
    map_trusted: bool = True,
) -> tuple[str, str | None]:
    """텍스트 분류 ↔ 안건 유형 코드 이중 대조 (코드로 짚고 제목으로 재확인).

    - 코드가 특정 유형이고 텍스트 분류가 'other'(놓침)면 → 코드로 승격하되 **분류 근거를
      note로 남긴다** (260724 QA: silent 승격 금지 — 설명책임).
      승격 게이트 2중: ① map_trusted (같은 공고에서 불일치가 하나라도 나오면 순서
      바인딩 전체 불신 — zip-order 밀림 방어) ② 섹션 제목 자체의 텍스트 분류가 코드와
      일치해야 함 (이름 기반 정합 — 밀린 코드는 여기서 걸린다).
    - 둘 다 특정인데 다르면 → 텍스트 분류 유지 + 확인 권고 note.
    - 코드 없음/기타(20-0)/미등재 → 텍스트 분류 그대로 (미등재는 note로 수기 확인 권고).
    반환: (category, classification_note|None) — note는 risk_factors가 아니라 별도 필드로
    노출한다 (거버넌스 위험 목록에 분류 품질 메모를 섞지 않는다 — 스튜어드십 리뷰 260724).
    """
    if not section_code or section_code in _LCODE_NON_AUTHORITATIVE:
        return category, None
    mapped = _LCODE_CATEGORY.get(section_code)
    # 문구에 원시 L-코드 비노출 (260724 사용자: 코드는 운영자용 — payload의
    # source_section.section_code로만 제공, 사용자 문구는 한글 유형명으로)
    if mapped is None:
        return category, "표준 서식 신고 유형이 분류 체계에 미등록 — 안건 분류 수기 확인 권고"
    if category == "other":
        # 이름 기반 정합: 섹션 제목("□ 자본의 감소" 류)을 분류기에 넣어 코드와 일치할 때만 승격
        title_cat = _classify_agenda(section_title) if section_title else None
        if map_trusted and title_cat == mapped:
            return mapped, (
                f"분류 근거: 표준 서식 신고 유형 '{_CATEGORY_KO.get(mapped, mapped)}' — "
                f"제목 기반 분류 미매칭을 보완"
            )
        return category, (
            f"표준 서식 신고 유형('{_CATEGORY_KO.get(mapped, mapped)}') 존재하나 "
            f"정합 확인 실패 — 원문 발췌 확인 권고"
        )
    if mapped != category:
        return category, (
            f"표준 서식 신고 유형('{_CATEGORY_KO.get(mapped, mapped)}')과 제목 기반 분류"
            f"('{_CATEGORY_KO.get(category, category)}') 불일치 — 원문 발췌 확인 권고"
        )
    return category, None


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
        return "FOR", "적용 정책의 기본 입장이 찬성 (사안별 예외 규칙은 별도)"
    if default_str == "against":
        return "REVIEW", "적용 정책의 기본 입장은 반대이나, 법령상 강행규정이 아니므로 검토 필요로 둡니다"
    if default_str == "review":
        return "REVIEW", "적용 정책의 기본 입장이 사안별 검토"
    return fallback_decision, fallback_reason


def _public_vote_style_label(vote_style: str | None) -> str:
    if vote_style == "open_proxy":
        return "open_proxy"
    return "internal_policy_variant"


def _public_policy_basis(
    vote_style: str,
    category: str,
    policy_default: str | None,
    law_layer_hit: tuple[str, str, str] | None,
) -> str:
    if law_layer_hit is not None:
        return f"법령 판단 (1·2·3차 상법 개정) — {law_layer_hit[2]}"

    base = "Open Proxy guideline" if vote_style == "open_proxy" else "Internal policy variant"
    if policy_default and policy_default != "case_by_case":
        return f"{base} / 운용사 정책 기본값: {policy_default}"
    return f"{base} / 운용사 정책은 사안별 판단 — OPM 기준으로 판정"


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


_LAW_PROVISIONS_CACHE: dict[str, dict[str, Any]] | None = None


def _load_law_provisions() -> dict[str, dict[str, Any]]:
    """wiki/rules/laws/law_provisions.json(상법 개정 조항 대장 SSOT) 로드 (모듈 캐시).

    {provision_id: provision} 매핑. 엔진 룰의 provision 필드가 이 키를 가리킨다.
    law-layer hit 시 근거에 조문·유예도래일·적용대상·시행령 임계를 붙이는 데 쓴다(260709).
    """
    global _LAW_PROVISIONS_CACHE
    if _LAW_PROVISIONS_CACHE is not None:
        return _LAW_PROVISIONS_CACHE
    try:
        repo_root = Path(__file__).resolve().parent.parent.parent
        path = repo_root / "wiki" / "rules" / "laws" / "law_provisions.json"
        if not path.exists():
            _LAW_PROVISIONS_CACHE = {}
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        _LAW_PROVISIONS_CACHE = {
            p["provision_id"]: p for p in (data.get("provisions") or []) if p.get("provision_id")
        }
        return _LAW_PROVISIONS_CACHE
    except Exception:
        _LAW_PROVISIONS_CACHE = {}
        return {}


def _law_provision_detail(rule_id: str) -> dict[str, Any] | None:
    """엔진 룰 id → 그 룰의 provision → 조항 대장 상세(근거 심화용).

    반환: {article, amendment_round, effective_date, obligation_date, applies_to,
           threshold_decree, first_agm_trigger, summary}. provision 없으면 None.
    summary = 사람이 읽는 한 줄(reason에 붙임).
    """
    if not rule_id:
        return None
    prov_id = None
    for r in _load_law_layer_rules():
        if r.get("id") == rule_id:
            prov_id = r.get("provision")
            break
    if not prov_id:
        return None
    p = _load_law_provisions().get(prov_id)
    if not p:
        return None
    parts = [f"조항 상세: {p.get('article', '')} "
             f"({p.get('amendment_round_label', '')}, 시행 {p.get('effective_date', '')})"]
    if p.get("obligation_date"):
        parts.append(f"유예도래일 {p['obligation_date']}")
    if p.get("threshold_decree"):
        parts.append(f"임계 {p['threshold_decree']}")
    if p.get("first_agm_trigger"):
        parts.append("시행 후 최초 이사선임 주총부터 적용(엔진은 주총일 기준 근사)")
    if p.get("table_applies_to"):
        parts.append(f"적용 {p['table_applies_to']}")
    return {
        "provision_id": prov_id,
        "article": p.get("article"),
        "amendment_round": p.get("amendment_round_label"),
        "effective_date": p.get("effective_date"),
        "obligation_date": p.get("obligation_date"),
        "applies_to": p.get("table_applies_to"),
        "threshold_decree": p.get("threshold_decree"),
        "first_agm_trigger": bool(p.get("first_agm_trigger")),
        "summary": " · ".join(parts),
    }


_LLM_MISREAD_PATTERNS_CACHE: list[dict[str, Any]] | None = None


def _load_llm_misread_patterns() -> list[dict[str, Any]]:
    """wiki/rules/laws/llm_misread_patterns.json 로드 (모듈 캐시).

    LLM이 안건명 키워드만 보고 자체 결정 변경하는 misread 패턴 catalog.
    새 패턴 발견 시 본 JSON에만 추가 — 코드 변경 X.
    """
    global _LLM_MISREAD_PATTERNS_CACHE
    if _LLM_MISREAD_PATTERNS_CACHE is not None:
        return _LLM_MISREAD_PATTERNS_CACHE
    try:
        repo_root = Path(__file__).resolve().parent.parent.parent
        path = repo_root / "wiki" / "rules" / "laws" / "llm_misread_patterns.json"
        if not path.exists():
            _LLM_MISREAD_PATTERNS_CACHE = []
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        patterns = [p for p in (data.get("patterns") or []) if p.get("active", True) is not False]
        _LLM_MISREAD_PATTERNS_CACHE = patterns
        return patterns
    except Exception:
        _LLM_MISREAD_PATTERNS_CACHE = []
        return []


def _find_misread_guard(title: str, law_layer_id: str | None) -> str:
    """안건 title + 법령 ID 매칭 → anti-misread inline guard 메시지.

    catalog (wiki/rules/laws/llm_misread_patterns.json)에서 dynamic load.
    매칭 우선순위: trigger_keywords (title 포함) → law_layer_id 매칭 → 기본 guard.
    """
    patterns = _load_llm_misread_patterns()
    if not patterns:
        return ""
    for p in patterns:
        keywords = p.get("trigger_keywords") or []
        if any(kw in title for kw in keywords):
            return p.get("anti_misread_inline", "")
    # 폴백: law_layer_id 매칭
    if law_layer_id:
        for p in patterns:
            if p.get("law_layer_id") == law_layer_id:
                return p.get("anti_misread_inline", "")
    return ""


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


def _is_charter_top(title: str) -> bool:
    """top-level 정관변경 안건 식별 (D 패턴 fallback 진입 조건 中 1).

    호출부에서 parent_title == "" + children == 0 + amendments 비어있지 않음을
    추가로 확인해야 D 패턴 (raw에 sub-agenda 자체 부재)으로 확정.
    """
    if not title:
        return False
    return "정관" in title and any(k in title for k in ("변경", "개정"))


def _law_layer_body(
    amendments: list[dict[str, Any]],
    *,
    parent_title: str,
    corp_total_asset_won: int | None,
    today_iso: str,
) -> tuple[str, str, str, str] | None:
    """D 패턴 한정 amendments body fallback.

    각 amendment의 raw 본문 (label/clause/before/after/reason) 합친 텍스트로 룰 매칭.
    **amendment 단위 검사**로 Ralph 6 회귀 (모든 amendments 통합 → 한 안건 키워드가
    다른 sub에 잘못 매칭) 회피.

    호출 조건 (호출부에서 보장):
        - title_hit None
        - parent_title == "" (top)
        - _is_charter_top(title) True
        - 안건 children 0
        - amendments 비어있지 않음

    룰 매칭은 룰의 `body_pattern` (있으면 우선 — D 패턴용 lenient 패턴)
    또는 `agenda_pattern` (fallback) 사용. amendment 1건이라도 hit하면 결과 반환.
    """
    if not amendments:
        return None
    rules = _load_law_layer_rules()
    if not rules:
        return None

    for am in amendments:
        parts = [
            am.get("label") or "",
            am.get("clause") or "",
            am.get("before") or "",
            am.get("after") or "",
            am.get("reason") or "",
        ]
        body_text = " ".join(p for p in parts if p).strip()
        if not body_text:
            continue

        for rule in rules:
            if rule.get("layer") == "C":
                continue
            if rule.get("decision") == "risk_factors":
                continue
            # body_pattern 우선, 없으면 agenda_pattern fallback
            pattern = rule.get("body_pattern") or rule.get("agenda_pattern") or {}
            if not _agenda_pattern_match(body_text, parent_title, pattern):
                continue
            if not _applies_to_match(rule, corp_total_asset_won, today_iso):
                continue
            label = (am.get("label") or am.get("clause") or "").strip()
            label_ref = f" [body: {label[:30]}]" if label else " [body fallback]"
            return (
                rule["decision"],
                rule.get("reason_template", "") + label_ref,
                rule.get("id", ""),
                rule.get("law_reference", ""),
            )
    return None


_CLAUSE_RE = re.compile(r'제\s*(\d+)\s*조(?:\s*의\s*(\d+))?')

# sub-agenda → amendment 매핑용 도메인 키워드 (정관 변경 안건)
# generic 동사 (신설/삭제/정비/개정/반영/조문) 제외 — LG화학 "정관 정비" 같은 generic sub false positive 회피.
# 정관변경 + 강행규정 specific 키워드만 (Ralph 6 회귀 회피).
_SUBAGENDA_DOMAIN_KEYWORDS = [
    "기준일", "소집지", "의결권", "이사", "감사", "보수", "퇴직금", "임기",
    "사업목적", "주식", "전자", "주주명부", "이사회", "위원회", "수권주식",
    "전환사채", "신주인수권", "배당", "사명", "본점", "정원", "원수",
    "전자증권",
    "집중투표", "사외이사", "독립이사", "전자주주총회", "자사주",
]


def _extract_clauses(text: str) -> set[str]:
    """텍스트에서 정관 조항 번호 추출 (제N조 / 제N조의M)."""
    nums = set()
    for m in _CLAUSE_RE.finditer(text or ""):
        n1, n2 = m.group(1), m.group(2)
        nums.add(f"제{n1}조의{n2}" if n2 else f"제{n1}조")
    return nums


def _extract_sub_keywords(text: str) -> set[str]:
    """sub-agenda title에서 도메인 키워드 추출."""
    text_clean = (text or "").replace(" ", "")
    return {kw for kw in _SUBAGENDA_DOMAIN_KEYWORDS if kw in text_clean}


def _is_generic_sub(title: str) -> bool:
    """generic sub-agenda 식별 — 정관/변경/개정 단어 없음 + 도메인 키워드 없음.

    카카오게임즈 패턴 진입 조건 (호출부에서 보장)에서 "정관/변경/개정 없음"은 이미 충족.
    여기서는 추가로 도메인 키워드도 없는지 검사 (예: "그 외 변경의 건" / "기타 정비").
    """
    return not _extract_sub_keywords(title)


def _map_subagenda_to_amendment(
    sub_title: str,
    amendments: list[dict[str, Any]],
    used: set[int],
) -> int | None:
    """sub-agenda → amendment 매핑. 매핑된 amendment idx 반환 또는 None.

    Priority cascade (strict — semantic mismatch 회피):
    1. amendment label == sub title (또는 substring) — 강원랜드 같은 동일 string
    2. amendment label/before/after에서 조항 추출 → sub clauses 매칭

    keyword 매칭은 의도적으로 제외:
    - sub title의 keyword가 amendment reason에 있어도 의미 다를 수 있음 (예: LG화학
      "선임독립이사 선임" sub가 "독립이사 명칭 변경" amendment에 매핑되어 A1-5 false
      positive 발생). Ralph 6 회귀 회피 원칙 — 정확성 우선.
    - keyword 매칭이 필요한 케이스 (예: 카카오게임즈 "주주총회 기준일 변경" → 제13조의3)는
      별도 architect 필요 (sub→amendment semantic 매핑은 LLM 영역).

    `used` set: 이미 매핑된 amendment idx — cross-match 회피.
    """
    sub_title_clean = (sub_title or "").strip()
    if not sub_title_clean or not amendments:
        return None

    # Priority 1: label == sub title (substring)
    for i, am in enumerate(amendments):
        if i in used:
            continue
        label = (am.get("label") or "").strip()
        if not label:
            continue
        if label == sub_title_clean or label in sub_title_clean or sub_title_clean in label:
            return i

    # Priority 2: clause 매칭 (label/before/after 모두 검사)
    sub_clauses = _extract_clauses(sub_title)
    if sub_clauses:
        best_i, best_overlap = None, 0
        for i, am in enumerate(amendments):
            if i in used:
                continue
            am_text = " ".join([
                am.get("label") or "", am.get("before") or "",
                am.get("after") or "", am.get("clause") or "",
            ])
            am_clauses = _extract_clauses(am_text)
            overlap = len(sub_clauses & am_clauses)
            if overlap > best_overlap:
                best_overlap = overlap
                best_i = i
        if best_i is not None:
            return best_i

    return None


def _law_layer_subagenda_mapped(
    sub_title: str,
    amendment: dict[str, Any],
    *,
    parent_title: str,
    corp_total_asset_won: int | None,
    today_iso: str,
) -> tuple[str, str, str, str] | None:
    """카카오게임즈 패턴 fallback — 매핑된 amendment 1개 본문으로 룰 매칭.

    호출 조건 (호출부에서 보장):
        - title_hit None
        - parent_title에 "정관" + "변경"/"개정" (정관변경 sub)
        - 자기 children == 0
        - 자기 title generic (정관/변경/개정 없음)
        - amendments 매핑 성공

    매핑된 amendment의 body_text로 룰 매칭 (body_pattern 우선).
    """
    if not amendment:
        return None
    rules = _load_law_layer_rules()
    if not rules:
        return None

    parts = [
        amendment.get("label") or "",
        amendment.get("clause") or "",
        amendment.get("before") or "",
        amendment.get("after") or "",
        amendment.get("reason") or "",
    ]
    body_text = " ".join(p for p in parts if p).strip()
    if not body_text:
        return None

    for rule in rules:
        if rule.get("layer") == "C" or rule.get("decision") == "risk_factors":
            continue
        pattern = rule.get("body_pattern") or rule.get("agenda_pattern") or {}
        if not _agenda_pattern_match(body_text, parent_title, pattern):
            continue
        if not _applies_to_match(rule, corp_total_asset_won, today_iso):
            continue
        label = (amendment.get("label") or amendment.get("clause") or "").strip()
        label_ref = f" [sub-mapped: {label[:30]}]" if label else " [sub-mapped]"
        return (
            rule["decision"],
            rule.get("reason_template", "") + label_ref,
            rule.get("id", ""),
            rule.get("law_reference", ""),
        )
    return None


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

# 준비금 재분류(자본준비금·이익준비금 → 이익잉여금) — 배당 자체가 아니라 배당가능이익을
# 만드는 자본거래. '적립의 건'처럼 반대 방향 안건은 걸리지 않게 감액·전입 동작을 함께 요구한다.
_RESERVE_RECLASS = re.compile(
    r'(?:자본|이익)\s*준비금[^\n]{0,60}?(?:감액|감소|전입|전환|이입)'
    r'|(?:감액|감소|전입|전환|이입)[^\n]{0,60}?(?:자본|이익)\s*준비금')


def _classify_agenda(agenda_title: str, parent_title: str = "") -> str:
    """안건 제목 → category. 가이드라인 voting_rules 키와 매칭.

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
    # 260710 삼성카드 auto-FOR 사고 방지 — 개별 후보 sub-안건 카테고리 상속.
    # parent가 "…선임…" 묶음인데 sub 제목이 "사내이사 김이태"·"감사위원 김준규"처럼
    # "선임" 키워드 없이 이름만 와서 title 매칭 실패 → 'other'(→auto-FOR)로 새는 것을 막는다.
    # (사외이사 sub는 "사외이사" 키워드로 우연히 걸렸지만 사내이사·감사위원 sub는 통째로 우회하던 버그)
    # 감사위원 우선 판정 — 감사위원 sub는 director보다 strict한 독립성 검증 경로 필요.
    if parent and "선임" in parent and t and ("이사" in t or "감사위원" in t or "감사" in t):
        if "감사위원" in parent or "감사위원" in t:
            return "audit_committee_election"
        if "이사" in parent:
            return "director_election"
    # ralph 260505 코붕이 의견: 한국 회사 관행상 퇴직금/보수는 대부분 정관 일부 변경 형태로 들어옴.
    # → 정관이 본질, _decide_articles_amendment 안에서 amendments raw 보고 위험 detect.
    if "정관" in t:
        return "articles_amendment"
    # iter21: "재무제표" 우선 (배당 정보 포함 케이스)
    if "재무제표" in t and ("승인" in t or "확정" in t):
        return "financial_statements"
    if "재무제표" in t and "배당" not in t:
        return "financial_statements"
    # 「자본준비금 감액 및 이익잉여금 전입」은 배당이 아니라 배당가능이익을 만드는 자본거래다.
    # '이익잉여금' 단축경로가 이 안건을 배당으로 끌고 가면 배당성향·잉여금으로 적정성을 따지는
    # 엉뚱한 판정이 나온다 — 실측 12건 중 11건이 이렇게 새고 있었고 2건은 「결손보전을 위한」,
    # 즉 배당 여력과 정반대 국면이었다. 아래 iter12 주석이 밝힌 의도(준비금 감액은 other)가
    # 문면에 '이익잉여금'이 들어간 순간 달성되지 않던 순서 결함.
    if _RESERVE_RECLASS.search(t) and "배당" not in t:
        return "other"
    # 「배당가능이익을 재원으로 한 자기주식 소각」에서 배당은 **재원의 이름**으로만 등장한다.
    # 아래 '배당' 단축경로가 먼저 걸리면 자사주 소각에 배당성향·잉여금 기준을 들이대는
    # 엉뚱한 판정이 나온다(실측 태광산업 — 권고적 주주제안). 위 준비금 감액과 같은 순서 결함.
    # '처분'은 「이익잉여금처분계산서」와 겹치므로 제외한다.
    if ("자기주식" in t or "자사주" in t) and (
        "소각" in t or "취득" in t or ("처분" in t and "처분계산서" not in t)
    ):
        return "treasury_share"
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
    # 260724 L-코드 진단(캐시 27건×111회): DART 공식 표기 '자본의 감소'(L0-0-2-19-0)가
    # 'other' 위험키워드('감자'·'자본금 감액')를 우회해 mainstream FOR로 흐르던 실사례 2건
    # → 전용 카테고리. 단 '자본준비금/이익준비금 감액'(회계 평탄화)은 종전대로 other(FOR).
    if "감자" in t or (
        "자본" in t and ("감소" in t or "감액" in t) and "준비금" not in t
    ) or ("병합" in t and ("주식" in t or "액면" in t)):
        # 주식(액면)병합 = reverse split (260724 상상인증권 8/7 EGM 라이브 실사례 —
        # '기타'→자동FOR로 새던 것): 단주 처리 소수주주 축출 리스크 → 자본감소 체크리스트로 REVIEW
        return "capital_reduction"
    # 260724 스튜어드십 리뷰: 스톡옵션 부여도 'other'→자동FOR로 새던 동종 구멍 —
    # 희석률·행사가·부여대상 검토가 mainstream 필수라 전용 카테고리(REVIEW).
    if "주식매수선택권" in t or "스톡옵션" in t:
        return "stock_option_grant"
    if any(k in t for k in ("합병", "분할", "주식교환", "주식이전", "영업양도", "영업양수", "영업 양도", "영업 양수")):
        return "merger_or_restructuring"
    if "주주제안" in t:
        return "shareholder_proposal"
    return "other"


def _is_statutory_auditor_agenda(title: str) -> bool:
    t = title or ""
    return "감사" in t and "감사위원" not in t and "보수" not in t


def _core_person_name(name: str | None) -> str:
    """후보 이름에서 안건 제목 매칭용 핵심 이름 추출.

    '도진명 (Jim Myong Doh)' → '도진명' (영문 병기·괄호 제거).
    영문 전용 이름('Benjamin Tan')은 그대로. 한글 이름 뒤 공백+영문도 앞 토큰만.
    260710 현대차 도진명 매칭 실패 사고: eval name에 영문이 병기돼 `nm in title`이
    False → 개별 후보 평가가 통째로 우회되던 버그.
    """
    if not name:
        return ""
    # 괄호(반각/전각) 앞부분만
    core = re.split(r"[(（]", name, maxsplit=1)[0].strip()
    # 한글 이름 뒤 공백+영문("홍길동 James") → 한글 토큰만 (앞 토큰이 한글이면)
    if " " in core:
        head = core.split()[0]
        if re.search(r"[가-힣]", head):
            core = head
    return core or name.strip()


def _raw_excerpt(full_text: str, title: str, *, limit: int = 1800) -> str | None:
    """파싱 실패 안건용 소집공고 원문 발췌.

    안건 제목(또는 핵심 토큰)을 원문에서 찾아 주변 문맥을 반환. 못 찾으면 원문 앞부분.
    260710 코붕이 지시: 파싱 퀄리티가 낮으면(NO_DATA) 구조화 대신 raw 텍스트로 폴백해
    사람/LLM이 직접 읽고 판단하게 한다.
    """
    if not full_text:
        return None
    hay = full_text
    needle = (title or "").strip()
    idx = hay.find(needle) if needle else -1
    if idx < 0 and needle:
        # 제목 첫 핵심 토큰(공백/괄호 앞)으로 재시도
        token = re.split(r"[ (（]", needle, maxsplit=1)[0].strip()
        if len(token) >= 2:
            idx = hay.find(token)
    if idx < 0:
        excerpt = hay[:limit].strip()
        prefix = "[원문 앞부분 — 안건 위치 미확인] "
    else:
        start = max(0, idx - 200)
        excerpt = hay[start:start + limit].strip()
        prefix = "[원문 발췌] "
    excerpt = re.sub(r"\s+", " ", excerpt)
    return (prefix + excerpt) if excerpt else None


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
    faith = eval_match.get("faithfulness", {}) or {}
    audit_history = faith.get("audit_history_check", {}).get("summary", "")
    # Ralph 9가 계산해두고 판단에서 버려지던 신호 — 겸직 과다(사외이사 3곳 이상).
    # 260710 audit: 삼성전자 신제윤(감사위원, 태평양+HDC+롯데손보 3중 겸직 strong_concerns_concurrent)이
    # FOR "모두 clean"으로 나오던 문제 → 판단에 반영.
    concurrent = (faith.get("concurrent_outside_directors") or {}).get("summary", "")

    if disq == "red_flag":
        return "AGAINST", f"결격사유 발견 (eligibility 또는 미성년)"
    if audit_history == "red_flag":
        return "REVIEW", "이사 회계 위험 이력 — 과거 재직 회사에서 회계 위험이 발생했습니다 (원문 메모 참조 후 판단)"
    if is_outside:
        _role = "감사" if (is_audit or eval_match.get("_audit_force_strict")) else "사외이사"
        # 장기연임 — 법률 정정(260710 lawyer): "5년 룰 위반"은 법적 부정확(위반할 성문 규정 없음).
        #   · 5년 = OPM 자체 보수적 조기경보(특정 법정/지침 수치 아님).
        #   · HARD 결격 = 상법 시행령 §34조5항7호: 동일 상장회사 6년 초과 / 계열 합산 9년 초과 → 사외이사 결격.
        #   · 우리 tenure는 floor(과소계상)이고 동일회사 vs 계열 합산을 구분 못 함 → 결격을 사실확정 불가
        #     → tenure만으로 AGAINST(결격 확정) 부적절. 감사도 종전 AGAINST → REVIEW로 하향(법적 방어 위해
        #        사용자가 원문에서 동일회사 6년 초과 확인 필요). 6년 경계로 문구만 tiering.
        if indep == "long_tenure_concerns":
            _fyr = ((eval_match.get("independence") or {}).get("sub_factors") or {}).get("five_year_rule", {})
            _is_audit = bool(is_audit or eval_match.get("_audit_force_strict"))
            # 260724 스튜어드십 리뷰: 상근감사(감사위원 아님)에는 시행령 §34⑤7호(사외이사
            # 전용 결격)를 인용하지 않는다 — 장기재직 REVIEW 결론은 유지하되 소프트 경보로 서술.
            _rt = (eval_match.get("role_type") or "")
            _statutory = ("감사" in _rt) and ("감사위원" not in _rt) and ("사외" not in _rt)
            _who = ("감사(상근·감사위원 아님)" if _statutory
                    else "감사위원(사외)" if _is_audit else "사외이사")
            _audit_note = "(감사위원=사외이사 자격 동일 문턱, 독립성 가중)" if (_is_audit and not _statutory) else ""
            _law6 = ("장기재직에 따른 독립성 약화 소지 (소프트 경보 — 법정 결격 아님)" if _statutory
                     else "동일 상장회사 6년 초과 시 상법 시행령 §34조5항7호 사외이사 결격 해당 가능")
            # tenure 기반이면 실제 근거를, keyword 기반이면 정직하게 키워드 발견을 명시.
            if _fyr.get("source") in ("tenure_years", "roster_tenure"):
                _basis = _fyr.get("basis") or "재직기간 확인"
                _years = _fyr.get("years")
                if isinstance(_years, int) and _years >= 6:
                    return "REVIEW", (f"{_who} 장기연임 ({_basis}) — {_law6}{_audit_note}. "
                                      f"계열 합산(9년)·재직기간 과소계상 여부 원문 확인 권고")
                return "REVIEW", (f"{_who} 장기연임 소프트 경보 ({_basis}) — 재직 5년 이상. "
                                  + ("독립성 약화 소지" if _statutory
                                     else "법정 결격(상법 시행령 §34조5항7호 6년 초과)에는 미달하나 독립성 약화 소지")
                                  + f"{_audit_note}, 사용자 검토 권고")
            # keyword 기반(재직연수 미상) — 단일 REVIEW 문구(6년 경계 판정 불가)
            return "REVIEW", (f"{_who} 장기연임 (재선임/연임/중임 키워드 발견) — 5년 이상은 소프트 독립성 경보, "
                              f"{_law6}{_audit_note}(계열 합산·과소계상 원문 확인 권고)")
        if indep == "concerns":
            return "REVIEW", "사외이사 독립성 우려 (최대주주 관계 또는 회사와 거래 또는 이전 회사 직원)"
        # 겸직 과다 (3곳 이상) — 충실의무 수행 여력 검토 (260710 계산-후-폐기 신호 반영)
        if concurrent == "strong_concerns_concurrent":
            return "REVIEW", f"{_role} 겸직 과다 (타사 사외이사 3곳 이상) — 충실의무 수행 여력 검토 (concurrent overboarding, 원문 확인 권고)"
        # 최대주주 관계 약한 신호(iter18/27 calibration: 단독은 약신호) — 결정은 FOR 유지하되
        # reason을 정직화("모두 clean" 거짓 금지, 260710 한화오션 발행회사 관계 사고).
        if indep == "weak_concerns":
            return "FOR", f"{_role} 결격 없음 — 단 최대주주 관계 약한 신호 있음(발행회사/계열 관계 표기), 원문 확인 권고"
        if is_audit or eval_match.get("_audit_force_strict"):
            return "FOR", f"감사 독립성·결격사유 모두 해당 없음 ({role_type})"
        return "FOR", f"사외이사 독립성·결격사유 모두 해당 없음 ({role_type})"
    # 사내이사: 결격사유 외에 재직 중 회사 운영 성과 평가 (status quo 편향 mitigation, ralph 260505)
    perf = (eval_match.get("performance") or {}).get("classification")
    if perf == "bad":
        return "REVIEW", f"사내이사 재직 중 성과 저조 — 자본잠식/적자 또는 누적 악화, 법정 결격은 아니므로 사용자 검토"
    if perf == "weak":
        return "REVIEW", f"사내이사 재직 중 성과 부진 — 사용자 검토 필요"
    if perf in ("moderate", "good"):
        return "FOR", f"사내이사 결격 없음 + 재직 성과 {_PERF_KO.get(perf, perf)} ({role_type})"
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


def _first_present(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _director_comp_summary_values(summary: dict[str, Any]) -> dict[str, Any]:
    """보수한도 summary key normalization.

    shareholder_meeting parser는 camelCase(`currentTotalLimit`)를 내고,
    proxy_advise decision/facts는 snake_case(`limit_krw`)를 기대해왔다.
    여기서 한 번 normalize해서 판단 로직이 실제 파싱값을 쓰게 한다.
    """
    current_limit = _first_present(summary.get("limit_krw"), summary.get("currentTotalLimit"))
    prior_limit = _first_present(summary.get("prior_limit_krw"), summary.get("priorTotalLimit"))
    prior_paid = _first_present(summary.get("prior_paid_krw"), summary.get("priorTotalPaid"))
    util_rate = _first_present(summary.get("utilization_rate_pct"), summary.get("priorUtilization"))
    inc = summary.get("increase_rate_pct")

    if inc is None and current_limit is not None and prior_limit:
        inc = round((current_limit - prior_limit) / prior_limit * 100, 1)
    if util_rate is None and prior_paid is not None and prior_limit:
        util_rate = round(prior_paid / prior_limit * 100, 1)

    return {
        "increase_rate_pct": inc,
        "utilization_rate_pct": util_rate,
        "limit_krw": current_limit,
        "prior_limit_krw": prior_limit,
        "prior_paid_krw": prior_paid,
    }


def _compensation_data(comp_payload: dict[str, Any] | None) -> dict[str, Any]:
    if not comp_payload:
        return {}
    data = comp_payload.get("data") if isinstance(comp_payload, dict) else {}
    if not isinstance(data, dict):
        return {}
    compensation = data.get("compensation")
    if isinstance(compensation, dict):
        return compensation
    return data


def _comp_amount(data: dict[str, Any], *keys: str) -> int | float | None:
    """보수 항목 숫자 추출. 일부 회사(고려아연 등)는 파서가 '7'/'7명' 같은 문자열을
    내려보내 하류 `limit // headcount`가 TypeError — 여기서 일괄 숫자 강제."""
    for key in keys:
        value = data.get(key)
        if value is None:
            continue
        if isinstance(value, (int, float)):
            return value
        if isinstance(value, str):
            cleaned = value.replace(",", "").replace("명", "").replace("원", "").strip()
            try:
                return int(cleaned)
            except ValueError:
                try:
                    return float(cleaned)
                except ValueError:
                    return None
        return None
    return None


def _comp_target_values(comp_payload: dict[str, Any] | None, target: str) -> dict[str, Any]:
    data = _compensation_data(comp_payload)
    items = data.get("items") or []
    item = next((it for it in items if it.get("target") == target), None)
    if not item:
        if target == "이사":
            return _director_comp_summary_values(data.get("summary", {}) or {})
        return {
            "increase_rate_pct": None,
            "utilization_rate_pct": None,
            "limit_krw": None,
            "prior_limit_krw": None,
            "prior_paid_krw": None,
            "headcount": None,
        }

    cur = item.get("current") or {}
    prior = item.get("prior") or {}
    current_limit = _comp_amount(cur, "limitAmount", "total_amount", "totalAmount", "limit_krw")
    prior_limit = _comp_amount(prior, "limitAmount", "total_amount", "totalAmount", "limit_krw")
    prior_paid = _comp_amount(prior, "actualPaidAmount", "actual_paid_amount", "actualPaid", "paid_krw")
    headcount = _comp_amount(cur, "totalDirectors", "count", "headcount")
    inc = None
    util_rate = None
    if current_limit is not None and prior_limit:
        inc = round((current_limit - prior_limit) / prior_limit * 100, 1)
    if prior_paid is not None and prior_limit:
        util_rate = round(prior_paid / prior_limit * 100, 1)

    return {
        "increase_rate_pct": inc,
        "utilization_rate_pct": util_rate,
        "limit_krw": current_limit,
        "prior_limit_krw": prior_limit,
        "prior_paid_krw": prior_paid,
        "headcount": headcount,
    }


def _decide_director_compensation(
    comp_payload: dict[str, Any] | None,
    fin_metrics_payload: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """이사 보수한도 — 13 분기 (hard trigger → 자동 trigger → fallback).

    정책 근거:
    - OPM Open Proxy v1.3 #2 (적자/순익 감소 + 한도 증액 검토)
    - OPM #8 (50%+ 인상 검토, 일회성 사유 확인)
    - N연기금 [별표 1] IV-33① (이사회 안 원칙적 찬성), IV-33② (한도 과다 검토)
    - mainstream FOR fallback (records 표본 82.5% FOR)
    """
    fm_summary = ((fin_metrics_payload or {}).get("data") or {}).get("summary", {}) or {}
    cap_status = fm_summary.get("capital_impairment_status")
    ni = fm_summary.get("net_income_krw")
    yoy = _fm_yoy_pct(fin_metrics_payload)

    if not comp_payload:
        # 데이터 부족 fallback
        if cap_status == "full":
            return "REVIEW", "완전 자본잠식 — 보수한도 결정 부적절 가능성, 법정 금지는 아니므로 검토"  # 분기 12
        if ni is not None or cap_status == "normal":
            return "REVIEW", "보수 데이터 부족 — 전년 한도·소진율·인상률 확인 필요"
        return "NO_DATA", "보수 + 재무 데이터 둘 다 없음 — 본문 검토 필요"  # 분기 13

    comp_values = _comp_target_values(comp_payload, "이사")
    util_rate = comp_values["utilization_rate_pct"]
    inc = comp_values["increase_rate_pct"]

    # 분기 1: 자본잠식 + 인상
    if cap_status == "full" and inc is not None and inc > 0:
        return "REVIEW", f"완전 자본잠식 + 한도 인상 ({inc:+.0f}%) — 보수 결정 부적절 가능성, 법정 금지는 아니므로 검토"
    # 분기 2: 소진율 < 30% — 단독 강화 (코붕이 의견 260505 ralph precision iter 3)
    # "오바해서 올리거나 사용 안하면서 늘리거나"는 인상 외에도 "남는데 한도 유지" 도 검토 대상
    if util_rate is not None and util_rate < 30:
        if inc is not None and inc > 0:
            return "REVIEW", f"소진율 {util_rate:.0f}%인데 한도 인상 ({inc:+.0f}%) — 한도 적정성 검토"
        if inc is None:
            return "REVIEW", f"소진율 {util_rate:.0f}% (낮음) + 인상률 미파악 — 한도 적정성 검토"
        if inc == 0 or (-10 < inc < 0):
            return "REVIEW", f"소진율 {util_rate:.0f}%인데 한도 동결/소폭 변경 ({inc:+.0f}%) — 한도 적정성 검토"
        # inc <= -10 (감액)은 분기 8에서 처리 — 한도 줄이는 건 OK
    # 분기 3: 적자 OR 순익 감소 + 인상 (OPM #2 strict)
    if inc is not None and inc > 0:
        if (ni is not None and ni < 0) or (yoy is not None and yoy < 0):
            ni_label = "적자" if (ni is not None and ni < 0) else f"순익 yoy {yoy:+.0f}%"
            return "REVIEW", f"{ni_label} + 한도 인상 ({inc:+.0f}%) — 경영성과 대비 보수 적정성 검토"
    # 분기 5: 50%+ 인상 (#8)
    if inc is not None and inc >= 50:
        return "REVIEW", f"보수한도 대폭 인상 ({inc:+.0f}%) — OPM #8 (50%+ 인상, 일회성 사유 외)"
    # 분기 4: +10~+30% + 순익 yoy 둔화 (N연기금 IV-33② 보수)
    if inc is not None and 10 < inc < 30 and yoy is not None and yoy < 5:
        return "REVIEW", f"한도 +{inc:.0f}% + 순익 yoy {yoy:+.0f}% (둔화) — 참조 보수 규칙 (보수적)"
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
        return "FOR", f"보수한도 소폭 변경 ({inc:+.0f}%) — 참조 보수 규칙 (원칙적 찬성)"
    # 분기 10: +10~+30 + 순익 양호
    if inc is not None and 10 < inc < 30 and (yoy is None or yoy >= 5):
        return "FOR", f"한도 +{inc:.0f}% + 경영성과 양호 — 참조 보수 규칙"
    # 분기 11/13: 인상률 None (compensation parsed but increase_rate missing)
    if inc is None:
        if cap_status == "full":
            return "REVIEW", "완전 자본잠식 + 인상률 미파악 — 보수한도 적정성 검토"
        if ni is not None or cap_status == "normal":
            return "REVIEW", "보수한도 인상률 미파악 — 전년 한도·소진율 확인 필요"
        return "NO_DATA", "보수한도 인상률 + 재무 데이터 둘 다 없음 — 본문 검토 필요"
    # default fallback (이론상 도달 X)
    return "REVIEW", f"보수한도 변경 ({inc:+.0f}%) — 적정성 검토"


# 하위 호환 alias (proxy_advise dispatch 등 기존 호출)
_decide_compensation = _decide_director_compensation


# 본문↔확정 재무제표 검산 대상. **매출·영업이익만** 쓴다 — 실측 20사(260729):
#   매출     16곳 중 15곳이 1.00±5%   (범위 1.00~1.09)
#   영업이익  18곳 중 17곳이 1.00±5%   (범위 0.60~1.00)
#   순이익    **-0.75 ~ 22.69** — 못 쓴다. 본문은 총 순이익, API 는 지배주주 귀속이라
#            개념이 다르다(하이브: 매출·영업이익은 1.00인데 순이익만 22.69배).
# 자산·부채·자본은 본문에서 **당기만** 넘어와 대조 상대가 없다(API 는 FY(N-2)).
_CROSS_CHECK_ITEMS = (
    ("fy_prior_revenue_krw", "revenue_krw", "매출"),
    ("fy_prior_operating_profit_krw", "operating_profit_krw", "영업이익"),
)
# 절대액이 작으면 비율이 크게 흔들린다 — 남광토건 영업이익 43억 vs 73억 = 0.60배지만
# 차이는 30억이라 오파싱이 아니라 감사 전/후 조정이다. 그래서 소액 구간은 비율을 보지 않는다.
_CROSS_CHECK_MIN_ABS = 100_000_000_000        # 1,000억
_CROSS_CHECK_LOW, _CROSS_CHECK_HIGH = 0.7, 1.4


#: 순이익이 매출을 넘는 일은 거의 없다. 대규모 처분이익 같은 예외가 있으므로 **동률 근처는 넘긴다** —
#: 파싱 사고는 배 단위로 벌어지지 자릿수 안에서 아슬아슬하지 않다(영풍 실측 1.24배는 넘고, 실제 사고는
#: 116배였다). 1.2배를 문턱으로 둔다.
_NET_OVER_REVENUE = 1.2


#: 추출 위치를 사람 말로 — 엔진 필드명은 산출물에 나오지 않는다(산출물 표기 규칙).
_FY_METRIC_KO = {
    "revenue_krw": "매출", "operating_profit_krw": "영업이익", "net_income_krw": "순이익",
    "total_assets_krw": "자산총계", "total_liabilities_krw": "부채총계", "total_equity_krw": "자본총계",
}


def _internal_consistency(fy_raw: dict[str, Any] | None) -> list[str]:
    """본문 안에서 숫자끼리 맞는지 — API 대조가 못 보는 자리.

    API 검산은 매출·영업이익만 맞댄다. **순이익을 API 와 맞대지 않는 것은 의도된 것**이다 —
    본문은 총 순이익, API 는 지배주주 귀속이라 개념이 달라 비율이 -0.75~22.69배까지 정상적으로
    벌어진다. 그래서 순이익은 검산 그물을 통째로 빠져나갔고, 영풍 2026 회차는 본문 당기 순이익이
    3조 6,027억(실제 309억, 116배)인데 「본문 파싱 정상」이라고 선언됐다.

    **본문 안에서의 정합성은 그 개념 차이의 영향을 받지 않는다.** 같은 표에서 뽑은 숫자끼리
    비교하기 때문이다. 값을 고치지는 않는다 — 어긋난다고 말할 뿐이다.
    """
    if not fy_raw:
        return []
    issues: list[str] = []
    for period, label in (("current", "당기"), ("prior", "전기")):
        net = fy_raw.get(f"fy_{period}_net_income_krw")
        rev = fy_raw.get(f"fy_{period}_revenue_krw")
        if net and rev and rev > 0 and net > rev * _NET_OVER_REVENUE:
            issues.append(f"{label} 순이익({net / 1e12:.2f}조)이 매출({rev / 1e12:.2f}조)보다 큽니다")
    assets = fy_raw.get("fy_current_total_assets_krw")
    debt = fy_raw.get("fy_current_total_liabilities_krw")
    equity = fy_raw.get("fy_current_total_equity_krw")
    if assets and debt is not None and equity is not None:
        gap = abs(assets - (debt + equity))
        if gap > abs(assets) * 0.01:   # 1% — 반올림·표시단위 차이는 넘긴다
            issues.append(
                f"당기 자산({assets / 1e12:.2f}조)이 부채+자본({(debt + equity) / 1e12:.2f}조)과 맞지 않습니다"
            )
    return issues


def _cross_check_provisional_revenue(fy_raw: dict[str, Any] | None,
                                     fin_summary: dict[str, Any] | None) -> str | None:
    """소집공고 본문 잠정 재무제표를 DART API 확정치로 검산한다.

    소집공고는 사업보고서보다 먼저 나오므로(실측 88곳 중 78곳, 중앙 7일) 본문의 당기는
    API 에 아직 없다. 대신 **본문의 전기 = API 의 당기**라 이 둘을 맞대면 파싱이 맞는지 알 수 있다.
    값을 고치지는 않는다 — 어긋나면 그렇게 말할 뿐이다(호출측이 원문을 보게).

    **한계**: 검산 대상은 전기다. 안건이 승인하려는 **당기는 직접 검증되지 않는다** —
    같은 표·같은 행에서 뽑으므로 전기가 맞으면 당기도 맞다고 추론할 뿐이다.
    행을 잘못 고르는 오류(260729 「기타영업수익」)는 당기·전기가 함께 틀리므로 잡힌다.

    **성립 조건**: 이 등식은 `fin_year = target_year - 2` 에 의존한다(주총 N년 → 안건은 FY(N-1),
    분석 reference 는 FY(N-2)). 본문의 전기도 FY(N-1)-1 = FY(N-2) 라 같은 해가 된다.
    그 선택 로직이 「최신 사업보고서」로 바뀌면 사업보고서 제출 전후로 API 가 FY(N-1) 로 옮겨가
    **이 검산이 조용히 무너진다**(전기 vs 당기를 비교하게 되어 실제 YoY 변동을 오탐).
    `tests/test_provisional_fs_revenue.py::test_cross_check_assumes_fin_year_is_two_years_back` 가
    그 계약을 잡는다(260729 사용자 지적으로 확인).
    """
    if not fy_raw or not fin_summary:
        return None
    checked: list[str] = []
    bad: list[str] = []
    for raw_key, api_key, label in _CROSS_CHECK_ITEMS:
        prior, api_cur = fy_raw.get(raw_key), fin_summary.get(api_key)
        if not prior or not api_cur:
            continue
        if abs(api_cur) < _CROSS_CHECK_MIN_ABS:
            continue                          # 소액 구간은 비율이 신호가 안 된다
        ratio = prior / api_cur
        if _CROSS_CHECK_LOW <= ratio <= _CROSS_CHECK_HIGH:
            checked.append(label)
        else:
            bad.append(f"{label} {prior / 1e12:.2f}조 vs 확정 {api_cur / 1e12:.2f}조({ratio:.2f}배)")
    internal = _internal_consistency(fy_raw)
    if bad or internal:
        parts = []
        if bad:
            parts.append("본문 전기 " + " · ".join(bad))
        parts.extend(internal)
        # 어긋났을 때는 **어느 계정에서 뽑았는지**를 함께 보여준다. 값만 주면 사용자가 무엇을
        # 잘못 집었는지 알 수 없다 — 영풍이면 「순이익 ← 지배기업 소유주지분(재무상태표)」이
        # 그 자리에서 드러난다.
        src = (fy_raw or {}).get("source_accounts") or {}
        if src:
            trail = " · ".join(
                f"{_FY_METRIC_KO.get(k, k)} ← 「{v.get('account')}」"
                + ("(재무상태표)" if v.get("statement") == "balance_sheet" else "")
                for k, v in src.items()
            )
            parts.append(f"추출 위치: {trail}")
        return " · ".join(parts) + " — 본문 파싱을 신뢰하지 마시고 원문을 확인하세요"
    if checked:
        # 「정상」이라고 단정하지 않는다 — **확인한 범위만** 말한다. 예전에는 매출만 맞으면
        # 「본문 파싱 정상」이라고 했고, 그 사이 순이익이 116배 틀려 있었다(영풍).
        return f"본문 전기 {'·'.join(checked)}이 확정 재무제표와 일치 (대조 항목: {'·'.join(checked)})"
    return None


def _decide_audit_compensation(
    comp_payload: dict[str, Any] | None,
    fin_metrics_payload: dict[str, Any] | None = None,
    *,
    threshold_low_per_person: int = 50_000_000,   # N연기금 IV-34 과소 임계 (잠정 5천만원/인)
    threshold_high_per_person: int = 100_000_000,  # 잠정 1억원/인
) -> tuple[str, str]:
    """감사 보수한도 — 11 분기.

    정책 근거:
    - N연기금 [별표 1] IV-34: 한도 과소 (감사 충실 업무 훼손) 검토
    - s_legacy 패턴: 인상률 ≥+50% (감사 보수 급증 = 경영진 동조 인센티브) 검토
    - mainstream FOR (records 11 majority case 모두 FOR)
    """
    fm_summary = ((fin_metrics_payload or {}).get("data") or {}).get("summary", {}) or {}
    cap_status = fm_summary.get("capital_impairment_status")
    ni = fm_summary.get("net_income_krw")

    if not comp_payload:
        if cap_status == "full":
            return "REVIEW", "완전 자본잠식 — 감사 보수한도 결정 부적절 가능성, 법정 금지는 아니므로 검토"
        if ni is not None and ni > 0:
            return "FOR", f"감사 보수 데이터 부족이나 흑자 (순익 {ni:,}원) — 일반 기준 적용"
        if cap_status == "normal":
            return "FOR", "감사 보수 데이터 부족 + 자본 양호 — 일반 기준 적용"
        return "NO_DATA", "감사 보수 + 재무 데이터 둘 다 없음 — 본문 검토 필요"

    audit_values = _comp_target_values(comp_payload, "감사")
    audit_inc = audit_values["increase_rate_pct"]
    audit_total = audit_values["limit_krw"]
    audit_count = audit_values["headcount"]
    audit_per_person = None
    if audit_total and audit_count:
        audit_per_person = audit_total / audit_count

    # 분기 1: 자본잠식 + 인상
    if cap_status == "full" and audit_inc is not None and audit_inc > 0:
        return "REVIEW", f"완전 자본잠식 + 감사 한도 인상 ({audit_inc:+.0f}%) — 보수 결정 적정성 검토"
    # 분기 3: 1인당 평균 < threshold_low (N연기금 IV-34 과소)
    if audit_per_person is not None and audit_per_person < threshold_low_per_person:
        return "REVIEW", f"감사 1인당 평균 {audit_per_person/1e8:.2f}억 (< {threshold_low_per_person/1e8:.1f}억) — 과소 보수 여부 검토"
    # 분기 4: 인상률 ≥+50% + 1인당 평균 > threshold_high (s_legacy 패턴)
    if audit_inc is not None and audit_inc >= 50 and audit_per_person is not None and audit_per_person > threshold_high_per_person:
        return "REVIEW", f"감사 한도 +{audit_inc:.0f}% + 1인당 평균 {audit_per_person/1e8:.2f}억 (>{threshold_high_per_person/1e8:.1f}억) — 급증/과다 여부 검토"
    # 분기 5: 인상률 +30~+50% (s_legacy 보수)
    if audit_inc is not None and 30 <= audit_inc < 50:
        return "REVIEW", f"감사 한도 +{audit_inc:.0f}% 인상 — 감사보수 엄격 기준으로 검토"
    # 분기 6: 1인당 평균 경계
    if audit_per_person is not None and threshold_low_per_person <= audit_per_person < threshold_high_per_person:
        return "REVIEW", f"감사 1인당 평균 {audit_per_person/1e8:.2f}억 (경계 — {threshold_low_per_person/1e8:.1f}~{threshold_high_per_person/1e8:.1f}억) — 사용자 노출"
    # 분기 7: ±10% (동결)
    if audit_inc is not None and -10 <= audit_inc <= 10:
        return "FOR", f"감사 한도 소폭 변경 ({audit_inc:+.0f}%) — 참조 감사보수 규칙(원칙적 찬성)"
    # 분기 8: 1인당 평균 ≥ threshold_high + +10~+30% 인상
    if audit_per_person is not None and audit_per_person >= threshold_high_per_person and audit_inc is not None and 10 < audit_inc < 30:
        return "FOR", f"감사 1인당 평균 {audit_per_person/1e8:.2f}억 (≥{threshold_high_per_person/1e8:.1f}억) + 한도 +{audit_inc:.0f}% — 참조 감사보수 규칙(원칙적 찬성)"
    # 분기 9/10: 데이터 부족 fallback
    if audit_inc is None and audit_per_person is None:
        if cap_status == "full":
            return "REVIEW", "감사 보수 데이터 부족 + 자본잠식 — 보수 적정성 검토"
        if ni is not None and ni > 0:
            return "FOR", f"감사 보수 데이터 부족이나 흑자 (순익 {ni:,}원) — 일반 기준 적용"
    # 분기 11: default
    # 분기 9/10 은 둘 다 None 일 때만 잡는다 — 하나만 None 이면 여기로 떨어져 포맷이 터졌다
    # (260728 부실기업 검증에서 이오플로우·한국유니온제약 크래시로 발견).
    _inc = f"변경률 {audit_inc:+.0f}%" if audit_inc is not None else "변경률 미상"
    _pp = (f"1인당 {audit_per_person / 1e8:.2f}억원" if audit_per_person is not None
           else "1인당 미상")
    return "FOR", f"감사 보수한도 — 위험 신호 없음 ({_inc} · {_pp})"


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
    - N연기금 [별표 1] IV-35: 황금낙하산 검토
    - OPM Open Proxy v1.3 #6 (사외이사 퇴직혜택 부여 검토)
    - OPM #7 (황금낙하산 정관 도입 검토)
    - s_legacy 패턴 (퇴직금 적자 case 등) 검토
    - mainstream FOR (records 표본 80% FOR)
    """
    if not retirement_payload:
        return "NO_DATA", "퇴직금 변경 조항을 원문에서 추출하지 못했습니다 — 본문 검토 필요"
    data = retirement_payload.get("data") or retirement_payload  # 직접 dict 들어올 수도
    amendments = data.get("amendments") or []
    fm_summary = ((fin_metrics_payload or {}).get("data") or {}).get("summary", {}) or {}
    cap_status = fm_summary.get("capital_impairment_status")

    if not amendments:
        return "NO_DATA", "퇴직금 변경 상세가 정형으로 잡히지 않음 — 본문 원문으로 확인 (단순 정정일 수 있음)"

    # 키워드 hit 검출
    risk_against = []  # 황금낙하산 등
    risk_outside_dir = []  # 사외이사 퇴직금
    risk_review = []  # 지급률 등
    formal_hits = []  # 법령 반영 등

    for a in amendments:
        after = (a.get("after") or "").strip()
        before = (a.get("before") or "").strip()
        reason = (a.get("reason") or "").strip()
        # REVIEW trigger (policy concern, not legal disqualification)
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
        return "REVIEW", f"퇴직금 위험 문구 ({kws}) 신설 — 황금낙하산/경영권 변동 보상 가능성 검토"
    # 분기 2: 사외이사 퇴직금 신설
    if risk_outside_dir:
        return "REVIEW", "사외이사 퇴직금 신설 — 독립성 훼손 가능성 검토"
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
        return "REVIEW", "지급률 2배수 이상 인상 또는 신설 (≥3배수) — 과도한 퇴직급여 가능성 검토"
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
        return "REVIEW", f"퇴직금 변경 {len(amendments)}건, 위험 키워드 {len(risk_review)}건 ({kws}) — 사용자 검토"
    # 분기 10: amendments ≥1, 위험 hit 0
    if amendments:
        return "REVIEW", f"퇴직금 변경 {len(amendments)}건 — 변경 조항 원문 검토 권장"
    # 분기 11: amendments 0
    return "FOR", "퇴직금 단순 정정 (amendments 0건)"


#: 나쁜 순. 같은 결산일에 서로 다른 의견이 오면 **가장 나쁜 것**을 택한다 — `_build_audit_opinion_data`
#: 의 정렬 키가 결산일 하나뿐이라 그냥 첫 행을 쓰면 DART 응답 순서가 판정을 정한다(셀리버리 2022
#: 사업연도 실측 = 의견거절/적정/해당사항없음 3행이 모두 결산일 2022-12-31). 순서가 바뀌면 반대가
#: 조용히 사라지므로, 우연이 아니라 규칙으로 고른다. 「해당사항없음」은 의견이 아니라 빈칸이다.
_OPINION_SEVERITY = ("의견거절", "부적정", "한정", "적정")


def _worst_audit_opinion(rows: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, bool]:
    """의견 행들 중 가장 나쁜 것 + 서로 다른 의견이 섞여 있었는지."""
    ranked = [
        (rank, row)
        for row in rows
        for rank, kw in enumerate(_OPINION_SEVERITY)
        if kw in (row.get("adt_opinion") or "")
    ]
    if not ranked:
        return None, False
    ranked.sort(key=lambda pair: pair[0])
    return ranked[0][1], len({rank for rank, _ in ranked}) > 1


def _audit_opinion_at_meeting(
    audit_payload: dict[str, Any] | None,
    meeting_date_iso: str,
) -> dict[str, Any]:
    """**주총일에 공시돼 있던** 감사의견만 판정 근거로 쓴다.

    DART 는 재무제표가 재작성되면 **재작성본만** 돌려준다 — 비덴트 2022사업연도를 조회하면
    2024-12-31 접수본의 「적정」이 나오지만, 2023-03 주총 당시 의견은 의견거절이었다. 그대로 쓰면
    그때 알 수 없던 정보로 그때의 판단을 채점하게 된다(작업 원칙 5). 접수번호 앞 8자리가 접수일이라
    주총일과 비교하면 갈린다.

    값을 지우지는 않는다 — 「적정」이라는 사실 자체는 정보이므로 사유에 밝히고 **판정 근거에서만** 뺀다.
    """
    out: dict[str, Any] = {"opinion": None, "status": "unavailable", "row": None, "conflict": False}
    if not audit_payload:
        return out
    block = (audit_payload.get("data") or {}).get("audit_opinion") or {}
    rows = block.get("opinions") or []
    if not rows:
        # 조회는 됐는데 행이 없다 = 그 사업연도 사업보고서가 아직 없다(회사 사유)
        out["status"] = "not_filed" if audit_payload.get("data") is not None else "unavailable"
        return out
    # 조회한 사업연도의 행만 본다. 전기·전전기는 이번 안건의 승인 대상이 아니다.
    current = [r for r in rows if r.get("period_tag") == "current"] or rows[:1]
    row, conflict = _worst_audit_opinion(current)
    if row is None:
        # 행은 왔는데 의견 칸이 비어 있다(현대차 2025사업연도 실측). 「제출을 못 찾았다」와 다르다 —
        # 문서는 있으니 볼 곳도 다르다. 그 문서를 그대로 가리켜 준다.
        blank = current[0] if current else None
        out["status"] = "blank" if blank else "not_filed"
        out["row"] = blank
        return out
    out["row"], out["conflict"] = row, conflict
    filed_on = (row.get("rcept_no") or "")[:8]
    meeting_ymd = (meeting_date_iso or "").replace("-", "")[:8]
    if filed_on and meeting_ymd and filed_on > meeting_ymd:
        # 주총 뒤에 접수된 **사업보고서**다. 그렇다고 그때 감사의견이 없었다는 뜻은 아니다 —
        # 감사보고서는 외감법 §23① 로 주총 1주 전까지 별도 공시되고, 이 API 는 그 별도 문서가
        # 아니라 사업보고서만 읽는다. 「사업보고서가 늦었다」를 「감사의견이 없었다」로 바꿔 말하면
        # 확인하지 않은 부재를 단정하는 것이고, 실측 현대차·KB금융이 그 오탐에 걸렸다.
        # 갈리는 지점은 **의견이 그 사이 바뀌었는가**이고, 그 표지가 강조사항의 「재작성」이다.
        if "재작성" in (row.get("emphs_matter") or ""):
            out["status"] = "restated_after_meeting"
            return out
        out["opinion"], out["status"] = row.get("adt_opinion"), "available_late"
        return out
    out["opinion"], out["status"] = row.get("adt_opinion"), "available"
    return out


def _fy_label(row: dict[str, Any] | None) -> str:
    """「2022사업연도」 — 연도 없는 문장은 독자가 승인 대상 연도의 값으로 읽는다."""
    stlm = (row or {}).get("stlm_dt") or ""
    return f"{stlm[:4]}사업연도" if stlm[:4].isdigit() else ""


def _won(v: int | float | None) -> str:
    """금액을 읽을 수 있게 — 「-252,140,554,081원」은 사람이 자릿수를 못 센다."""
    if v is None:
        return "-"
    a = abs(v)
    if a >= 1e12:
        return f"{v / 1e12:,.2f}조원"
    if a >= 1e8:
        return f"{v / 1e8:,.0f}억원"
    return f"{v:,.0f}원"


def _provisional_state_payload(raw: dict[str, Any] | None,
                               base: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """공고의 잠정 재무제표(FY(N-1)P)를 `financial_metrics` 와 같은 모양으로 싸서 돌려준다.

    사업보고서가 주총 뒤에 나오는 구간(시장 전수 기준 18%)에서는 확정치가 없다. 그때 2년 전
    확정치로 자본잠식을 판단하느니, **승인 대상 연도의 잠정치**로 보는 것이 가깝다 — 주주가
    승인하려는 대상이 바로 그 숫자다.

    **검산을 통과해야만 값을 낸다.** 소집공고에는 XBRL 계정 코드가 없어 한글 라벨로만 찾으므로
    (실측 소집공고 62건 중 코드 0건) 잘못 집을 여지가 확정치보다 크다. 지배지분 + 비지배지분 =
    자본총계 가 어긋나면 라벨을 잘못 집은 것이니 값을 내지 않는다(실측 121건 중 검산 가능한
    78건 전부 성립 — 어긋나는 쪽이 비정상이다).

    **순이익은 싣지 않는다.** 공고는 연결 총액을 적고 확정치는 지배주주 귀속이라 개념이 다르다.
    배당성향 분모로 총액을 쓰면 성향이 낮게 나와 과도 배당을 정상으로 보이게 한다 — 안전한
    방향이 아니다. 없는 채로 두면 배당 판단이 「확인 필요」로 정직하게 간다.
    """
    if not raw or raw.get("extraction_status") not in ("success", "partial"):
        return None
    cap = raw.get("fy_current_capital_stock_krw")
    ctrl = raw.get("fy_current_controlling_equity_krw")
    total = raw.get("fy_current_total_equity_krw")
    nci = raw.get("fy_current_nci_krw")
    if cap is None or (ctrl is None and total is None):
        return None
    if ctrl is not None and nci is not None and total is not None:
        if abs((ctrl + nci) - total) > max(abs(total) * 1e-6, 1):
            return None
    imp = compute_capital_impairment(capital_stock=cap, controlling_equity=ctrl,
                                     total_equity=total, nci=nci)
    if imp["status"] is None:
        return None
    # 직전 확정치를 바탕에 깔고 **자본 항목만** 잠정치로 덮는다. 통째로 갈아치우면 공고에
    # 없는 신호(현금흐름 품질·이자보상배율·감사의견·전기 순이익)가 전부 사라져, 「완전 자본잠식」
    # 판정 옆에 위험신호가 하나도 없는 메모가 나간다.
    _base = dict(((base or {}).get("data") or {}).get("summary") or {})
    _base.update({
        "capital_stock_krw": cap,
        "total_equity_krw": total,
        "capital_impairment_status": imp["status"],
        "capital_impairment_ratio_pct": imp["ratio_pct"],
        "capital_impairment_ratio_total_pct": imp["ratio_total_pct"],
        "capital_impairment_basis": imp["basis"],
        "is_provisional": True,
        # 어느 필드가 잠정인지 남긴다 — 나머지는 직전 확정치라 연도가 다르다.
        "provisional_fields": list(_PROVISIONAL_FIELDS),
    })
    return {"data": {"summary": _base}}


#: 공고 잠정치가 덮는 필드. 나머지는 직전 확정치(FY(N-2)A)를 그대로 둔다 —
#: 공고에는 현금흐름표가 없어 `cfo_to_op_ratio`·`interest_coverage_ratio` 같은 신호를
#: FY(N-1) 로는 **만들 수가 없다**. 통째로 비우면 「완전 자본잠식」 판정 옆에 위험신호가
#: 하나도 없는 메모가 나간다(실측: 위험신호 4개 → 1개). 지우느니 직전 값이라도 보여준다.
_PROVISIONAL_FIELDS = (
    "capital_stock_krw", "total_equity_krw", "capital_impairment_status",
    "capital_impairment_ratio_pct", "capital_impairment_ratio_total_pct",
    "capital_impairment_basis",
)


def _impairment_equity_label(summary: dict[str, Any]) -> tuple[str, str]:
    """자본잠식을 **어느 자기자본으로 쟀는지** — 판정과 문장이 어긋나면 안 된다.

    규정 기준은 지배주주 귀속 자기자본(비지배지분 제외)이다. 그런데 그 값을 못 구하면
    자본총계로 물러나면서도(`capital_impairment_basis="total"`) 문장은 계속 「지배주주 귀속
    자기자본」이라고 말하고 있었다. 별도재무제표에서는 우연히 참이지만(비지배지분이라는 것이
    아예 없다), **연결에서 폴백한 경우에는 재지 않은 것을 쟀다고 쓰는 것**이다.

    두 경우는 읽는 사람에게 뜻이 정반대다 — 별도는 정상이고 더 볼 것이 없지만, 연결 폴백은
    「이 숫자는 규정 기준이 아닐 수 있다」는 신호다. `fs_div`(실제 사용된 기준)로 갈라 쓴다.
    """
    # 잠정치로 잰 것은 **규정 판정이 아니다.** 코스닥 해설서 자본잠식 적용기준 ②는
    # 「감사보고서상 감사의견이 적정인 재무제표 기준 적용」이라, 감사 전 잠정치는 규정이
    # 명시적으로 배제한다. 그래서 여기서 얻는 건 「더 이른 시점의 추정치」이지 관리종목 판정이
    # 아니다 — 그 구분을 문장에 남기지 않으면 읽는 쪽이 규정 판정으로 받아들인다.
    _prov = " · 주주총회 소집공고의 감사 전 재무제표에서 읽은 값이라, 외부감사인의 감사 결과와" \
            " 주주총회 승인 과정에서 달라질 수 있습니다(관리종목 판정은 감사 후 재무제표 기준)" \
            if summary.get("is_provisional") else ""
    basis = summary.get("capital_impairment_basis")
    if basis == "controlling":
        return "지배주주 귀속 자기자본", _prov
    if basis == "derived":
        # 규정 기준(비지배지분 제외)으로 잰 것이 맞다 — 다만 계정을 직접 읽은 게 아니라
        # 자본총계에서 빼서 만들었다. 결함이 아니라 산출 방법이므로 그대로 밝힌다.
        return ("지배주주 귀속 자기자본",
                " · 공시에 지배주주 지분 소계가 없어 자본총계에서 비지배지분을 빼 산출했습니다"
                + _prov)
    if (summary.get("fs_div") or "").upper() == "OFS":
        return "자기자본", " · 별도재무제표라 비지배지분이 없어 자본총계와 같습니다" + _prov
    return "자기자본", " · 지배주주 지분을 따로 확인하지 못해 자본총계로 계산했습니다" + _prov


def _capital_clause(summary: dict[str, Any], fy: str) -> tuple[str, str]:
    """자본잠식 절 — 있는 그대로 쓴다. 「부분」을 「없음」이라 쓰지 않는다."""
    status = summary.get("capital_impairment_status")
    pct = summary.get("capital_impairment_ratio_pct")
    equity_label, basis_note = _impairment_equity_label(summary)
    # 판정은 규정대로 지배지분 기준이지만, **비지배 포함 값도 같이 말한다** — 두 값의 간격이
    # 그 회사의 자회사 구조를 말해주고, 다른 자료(연결 자본총계 기준)와 대조할 때 필요하다.
    pct_total = summary.get("capital_impairment_ratio_total_pct")
    both = ""
    if (pct is not None and pct_total is not None and pct > 0
            and abs(pct - pct_total) >= 1.0):
        both = f" · 비지배지분 포함 기준으로는 {pct_total}%"
    suffix = f"({fy})" if fy else ""
    if status == "full":
        return "full", f"완전 자본잠식 — {equity_label} 0 이하{suffix}{both}{basis_note}"
    if status == "partial_50plus":
        # 단년도 50%는 관리종목, **2년 연속**이면 상장폐지다. 한 해 수치만 보고 「기준 초과」라고
        # 쓰면 그 결정적 조건이 빠진다. 시장(유가·코스닥)에 따라 조문·후속 효과도 다르므로
        # 시장을 확인하지 않은 상태에서는 규정명을 인용하지 않는다.
        return "partial_50plus", (
            f"자본잠식률 {pct}%{suffix} — 자본금의 50% 이상이 잠식됐습니다"
            f"(단년도 기준. 2개 사업연도 연속이면 상장폐지 사유로 이어집니다){both}{basis_note}"
        )
    if status == "partial":
        return "partial", f"부분 자본잠식 {pct}%{suffix}{both}{basis_note}"
    if status == "normal":
        return "normal", f"자본잠식 없음{suffix}"
    return "unknown", "자본잠식 상태 미확인"


def _rcept_date(raw: str, rcept_no: str) -> str:
    """접수일 `YYYY-MM-DD`. upstream 서식이 제각각이라 접수번호 앞 8자리로 통일한다."""
    digits = "".join(ch for ch in (raw or "") if ch.isdigit())
    if len(digits) < 8:
        digits = (rcept_no or "")[:8]
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}" if len(digits) >= 8 else "-"


def _disclosure_name(ref: dict[str, Any], label: str) -> str:
    """공시명 — 사람이 읽는 이름만 쓴다.

    일부 upstream 은 `report_nm` 에 `disposal_result` 같은 **내부 식별자**를 싣는다. 산출물은 사람이
    읽는 문서라 엔진 내부 이름이 나가면 안 된다(표기 규칙) — 한글이 없으면 용도 라벨로 대체한다.
    """
    name = (ref.get("report_nm") or "").replace("\n", " ").strip()
    if name and any("가" <= ch <= "힣" for ch in name):
        return name
    section = (ref.get("section") or "").replace("\n", " ").strip()
    if section and any("가" <= ch <= "힣" for ch in section):
        return section
    return label


def _decide_financial_statements(
    fm_payload: dict[str, Any] | None,
    audit_payload: dict[str, Any] | None = None,
    meeting_date_iso: str = "",
    approval_year: int | None = None,
    fin_reference_year: int | None = None,
) -> tuple[str, str]:
    """재무제표 승인 판정.

    **두 축을 한 문장에 뭉치지 않는다.** 예전에는 `if latest_op or cap_status is not None` 하나로
    「감사의견 적정 + 자본잠식 없음」을 냈는데, 그 결과 ① 감사의견을 한 번도 조회하지 않고 「적정」이라
    단정했고(호출부가 `scope="summary"` 로만 불러 `audit_opinion` 이 늘 비어 있었다) ② 부분 자본잠식도
    「없음」으로 나갔다(`full` 만 검사). 절을 갈라 쓰면 한쪽 근거로 다른 쪽을 참으로 만드는 코드 자체가
    성립하지 않는다.
    """
    if not fm_payload:
        return "NO_DATA", "재무 데이터 없음 — 사업보고서 본문 검토 필요"
    summary = (fm_payload.get("data") or {}).get("summary") or {}
    audit = _audit_opinion_at_meeting(audit_payload, meeting_date_iso)
    row, opinion = audit["row"], audit["opinion"]
    fy_audit = _fy_label(row) or (f"{approval_year}사업연도" if approval_year else "")
    cap_status, cap_clause = _capital_clause(
        summary, f"{fin_reference_year}사업연도" if fin_reference_year else ""
    )

    auditor = (row or {}).get("adtor") or ""
    stamp = f"({fy_audit}, {auditor})" if fy_audit and auditor else (f"({fy_audit})" if fy_audit else "")
    conflict_note = " — 같은 기간에 서로 다른 의견이 조회되어 가장 보수적인 값을 택했습니다" if audit["conflict"] else ""

    # 1. 비적정 의견 — 이 안건이 확정하려는 숫자를 외부감사인이 보증하지 않았다.
    if opinion and "적정" not in opinion:
        return "AGAINST", f"감사의견 {opinion}{stamp}{conflict_note} / {cap_clause}"
    # 2. 완전 자본잠식 — 감사의견을 기다릴 이유가 없다. 자본총계가 음수라는 사실은 그 자체로 확정이다.
    if cap_status == "full":
        return "AGAINST", f"{cap_clause} / " + (
            f"감사의견 {opinion}{stamp}" if opinion else "감사의견은 판정 근거로 쓰지 않았습니다"
        )
    # 3. 승인 대상 연도의 감사의견을 찾지 못했다. **없다고 말하지 않는다** — 우리가 읽는 것은
    #    사업보고서이고, 감사보고서는 외감법 §23① 로 주총 1주 전까지 별도 공시되므로 그쪽에
    #    있을 수 있다. 확인한 범위만 밝히고 어디를 보라고 알려준다.
    if audit["status"] == "blank":
        rcept = (row or {}).get("rcept_no") or ""
        where = f"사업보고서({rcept})" if rcept else "사업보고서"
        return "REVIEW", (
            f"승인 대상 {fy_audit or '해당 사업연도'} {where}의 감사의견 항목이 비어 있습니다 — "
            f"원문 「회계감사인의 감사의견」을 직접 확인하십시오 / {cap_clause}"
        )
    if audit["status"] == "not_filed":
        when = f"주주총회일({meeting_date_iso})" if meeting_date_iso else "주주총회일"
        return "REVIEW", (
            f"승인 대상 {fy_audit or '해당 사업연도'} 감사의견을 사업보고서에서 확인하지 못했습니다"
            f"({when} 기준) — 별도 제출된 감사보고서 원문을 확인하십시오 / {cap_clause}"
        )
    # 4. 조회된 의견이 주총 이후 재작성본이다 — 값은 밝히되 판정 근거로 쓰지 않는다.
    if audit["status"] == "restated_after_meeting":
        return "REVIEW", (
            f"감사의견 시점 불일치 — 조회된 의견({opinion or row.get('adt_opinion')})은 주주총회 이후 "
            f"접수된 사업보고서 기준이라 당시 판단의 근거로 쓰지 않았습니다 / {cap_clause}"
        )
    # 5. 조회는 됐는데 그 값이 주총 이후 접수된 사업보고서에서 나왔다 — **적정을 확정할 수 없다.**
    #    감사보고서는 외감법 §23① 로 주총 1주 전까지 별도 공시되므로 그때 이미 같은 의견이 공개돼
    #    있었을 가능성이 크지만, 그건 추정이다. 실측이 그 추정을 두 번 배신했다 — 오스템 2021사업연도
    #    감사보고서는 주총(2022-03-16) 뒤인 2022-04-01 제출이었고, 국일제지 2022사업연도는 주총 당시
    #    의견거절이었는데 조회되는 「적정」은 2024-02 재감사분이다. **확인 못 한 것을 찬성으로 내지
    #    않는다** — 값은 밝히고 대조 경로를 준다. (비적정은 위 1번에서 이미 갈렸다.)
    if audit["status"] == "available_late":
        return "REVIEW", (
            f"감사의견 {opinion}{stamp}으로 조회되나 주주총회 이후 접수된 사업보고서 기준입니다 — "
            f"주주총회 전 별도 제출된 감사보고서 원문으로 대조하십시오 / {cap_clause}"
        )
    # 6. 우리가 못 읽었다 — 회사 사유가 아니다. 섞어 쓰면 실무자가 없는 문제를 찾으러 간다.
    if audit["status"] == "unavailable":
        return "NO_DATA", f"감사의견을 조회하지 못했습니다(조회 오류) — 판정 근거 없음 / {cap_clause}"
    # 6~7. 감사의견은 적정. 이제 자본 쪽만 남았다.
    if cap_status == "partial_50plus":
        return "REVIEW", f"{cap_clause} / 감사의견 적정{stamp}"
    if cap_status == "unknown":
        return "REVIEW", f"감사의견 적정{stamp} / 자본잠식 상태 미확인 — 자본금·자본총계 확인 필요"
    # 8. 찬성. 잠식률이 있으면 숫자를 남긴다 — 「없음」으로 뭉개지 않는다.
    return "FOR", f"감사의견 적정{stamp}{conflict_note} / {cap_clause}"


#: 「3인 이상 11인 이하」 · 「삼(3)인 이상 십일(11)인 이하」 · 「칠인(7)인 이하」 — 서식이 제각각이라
#: 괄호 안 아라비아 숫자를 기준으로 읽는다.
#: 상한 표기는 「이하」와 「이내」 둘 다 쓴다 — 한진칼은 「11인 이내 → 9인 이내」라 「이하」만
#: 보면 정원 축소를 통째로 놓친다.
_DIRECTOR_CAP = re.compile(r"(\d+)\s*\)?\s*인\s*(?:이하|이내)")
_AUTHORIZED_SHARES = re.compile(r"발행할\s*주식의\s*총수[^\d]{0,30}([\d,]{4,})")


def _articles_body_risks(amendment: dict[str, Any] | None) -> list[str]:
    """정관 **조문 본문**에서 위험 신호를 읽는다.

    회사는 제목을 완곡하게 쓴다 — 「이사 수 상한 설정의 건」·「이사회 규모 정상화」·「이사회 운영의
    효율성 제고의 건」·「상법 개정에 따른 변경」. 제목만 보면 넷 다 위험 신호 0인데, 조문 본문은
    각각 이사 정원 상한 신설(태광산업)·11인→9인(한진칼)·11인→7인(카카오)·전자주주총회 배제
    조항 신설(가비아)이다. **그 본문은 이미 fetch 해서 산출물에 첨부까지 하고 있었다** — 판정에만
    안 쓰였을 뿐이다. 사유 문구는 「이사 축소 … 없음」이라고 적극적으로 안심시키기까지 했다.
    """
    if not amendment:
        return []
    before = (amendment.get("before") or "")
    after = (amendment.get("after") or "")
    if not (before or after):
        return []
    risks: list[str] = []

    # 이사 정원 — 상한이 낮아졌거나, 없던 상한이 새로 생겼거나.
    caps_before = [int(m) for m in _DIRECTOR_CAP.findall(before)]
    caps_after = [int(m) for m in _DIRECTOR_CAP.findall(after)]
    if "이사" in before or "이사" in after:
        if caps_before and caps_after and max(caps_after) < max(caps_before):
            risks.append(f"이사 정원 상한 축소 ({max(caps_before)}인 → {max(caps_after)}인)")
        elif not caps_before and caps_after and "이사" in after:
            risks.append(f"이사 정원 상한 신설 ({max(caps_after)}인)")

    # 수권주식 — 늘면 향후 희석 여지다.
    sb = _AUTHORIZED_SHARES.search(before)
    sa = _AUTHORIZED_SHARES.search(after)
    if sb and sa:
        n_before = int(sb.group(1).replace(",", ""))
        n_after = int(sa.group(1).replace(",", ""))
        if n_after > n_before:
            risks.append(f"수권주식 증가 ({n_before:,}주 → {n_after:,}주)")

    # 집중투표 배제 — 신설된 경우만(원래 있던 조항은 이번 안건의 변경 사항이 아니다).
    if ("집중투표" in after and any(k in after for k in ("적용하지 아니", "배제", "적용하지 않"))
            and "집중투표" not in before):
        risks.append("집중투표 배제 조항 신설")

    # 전자주주총회 배제 — 상법 §542조의14 의 반대 방향인데 「상법 개정에 따른 변경」이라는 제목을
    # 달고 온다(가비아·솔루엠). 「~방식으로만 개최한다」가 대면 전용을 못 박는 문구다.
    if "방식으로만" in after.replace(" ", "") or "직접출석하는방식으로만" in after.replace(" ", ""):
        risks.append("전자주주총회 배제 — 대면 개최로 한정하는 조항")

    return risks


def _decide_articles_amendment(
    agenda_title: str,
    retirement_payload: dict[str, Any] | None = None,
    comp_payload: dict[str, Any] | None = None,
    fin_metrics_payload: dict[str, Any] | None = None,
    amendment: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """정관변경 안건 → 제목 키워드 + **조문 본문**.

    ralph iter6 강화: 위험 신호 (집중투표 배제 / 이사 정원 축소 / 권한 강화) 없는
    일반 정관변경은 mainstream FOR (50/50, 71/71 운용사 표본). conservative REVIEW는
    정체성상 의미 있으나 G2 정확도 차원에서 위험 신호 없으면 default FOR.
    """
    t = agenda_title or ""
    t_compact = re.sub(r"\s+", "", t)
    # 260508: 법령 layer (1·2·3차 상법 개정 + 정관 우회 시나리오) 우선 적용으로 이동.
    # 이 함수는 법령 layer 미매치 시 fallback (운용사 정책 hardcoded 분기).
    # 참조: services/proxy_advise.py:_law_layer + wiki/rules/laws/law_layer_rules.json
    #
    # REVIEW signals (소수주주 보호 후퇴) — 법령 layer A2 직접 hit이 아니면 자동 반대 금지
    if "집중투표" in t and "배제" in t:
        return "REVIEW", "집중투표 배제 — 소수주주 보호 후퇴 가능성. 강행규정 직접 적용 대상은 아닙니다"
    if "초다수결의제" in t or ("의결권" in t and "제한" in t):
        return "REVIEW", "초다수결의제 또는 의결권 제한 — 적대적 인수 방어 가능성 검토"
    # iter23+24 검증: "통지기한 단축" records 표본 0건 → over-fit fix 제거
    # REVIEW signals (영향 명확하지 않은 변경)
    if "이사" in t and ("정원" in t or "축소" in t):
        return "REVIEW", "이사회 정원 축소 — 거버넌스 영향"
    if "수권주식" in t and ("증가" in t or "확대" in t):
        return "REVIEW", "수권주식 증가 — 향후 희석 가능성"
    if "액면분할" in t:
        return "REVIEW", "액면분할 정관 변경 — 수권주식수·액면가 비례 조정 여부 본문 검토 필요"
    if "소수주주" in t and "보호" in t:
        return "FOR", "소수주주 보호 명문화 — 주주권 보호 강화"
    if "오기" in t and "정정" in t:
        return "FOR", "오기 정정 — 실질 권리변동 없음"
    if "신주발행" in t:
        return "REVIEW", "신주발행 관련 정관 변경 — 주주평등·희석 영향 본문 검토 필요"
    if "충실의무" in t and "이사" in t:
        return "FOR", "이사 충실의무 명문화 — 상법 개정 정합"
    if "분기배당" in t:
        return "REVIEW", "분기배당 관련 정관 변경 — 배당 재원·기준일·준비금 영향 본문 검토 필요"
    if "집행임원" in t:
        return "REVIEW", "집행임원제도 도입 — 이사회·경영진 권한 구조 변경 본문 검토 필요"
    if "주주총회" in t and "의장" in t:
        return "REVIEW", "주주총회 의장 변경 — 회의 운영권·중립성 영향 본문 검토 필요"
    if "이사회" in t and "소집" in t:
        return "REVIEW", "이사회 소집 절차 변경 — 통지기간·긴급소집 예외가 이사회 운영에 미치는 영향 본문 검토 필요"
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
    generic_articles_titles = {
        "정관변경의건",
        "정관일부변경의건",
        "정관개정의건",
        "정관일부개정의건",
        "정관일부변경",
        "정관일부개정",
    }
    if ret_amends and t_compact in generic_articles_titles:
        ret_decision, ret_reason = _decide_retirement_pay(retirement_payload, fin_metrics_payload)
        return ret_decision, f"정관변경 (본문에서 퇴직금 변경 {len(ret_amends)}건 확인) — {ret_reason}"
    # 제목에 안 걸렸으면 조문 본문을 읽는다. 회사는 제목을 완곡하게 쓴다 — 여기까지 와서야
    # 이사 정원 축소·수권주식 증가·전자주총 배제가 드러난다.
    body_risks = _articles_body_risks(amendment)
    if body_risks:
        return "REVIEW", "정관변경 — 조문 본문 위험 신호: " + " · ".join(body_risks) + " (원문 확인 권장)"
    # default FOR. **검사한 범위만 말한다** — 예전에는 조문을 보지도 않고 「이사 축소 … 없음」이라고
    # 적극적으로 안심시켰다(미탐지보다 나쁘다).
    checked = "제목과 조문 본문" if amendment else "제목"
    return "FOR", (
        f"정관변경 — {checked}에서 위험 신호 (집중투표 배제 / 의결권 제한 / 이사 정원 축소 / "
        f"수권주식 증가 / 전자주총 배제 / 퇴직금 / 보수한도) 없음"
    )


def _decide_treasury_share(agenda_title: str) -> tuple[str, str]:
    """자사주 안건."""
    t = agenda_title or ""
    if "소각" in t:
        return "FOR", "자사주 소각 — 주주환원"
    if "처분" in t:
        return "REVIEW", "자사주 처분 — 우호 지분 형성 가능성 검토"
    return "NO_DATA", "자사주 안건 세부 (소각/처분/취득) 미식별 — 본문 검토 필요"


#: 판정 enum(FOR/REVIEW/AGAINST)은 산출물 표기가 한글(✅ 찬성·⚠️ 검토 필요·❌ 반대)인데
#: 정책 인용문에만 영문이 남아 있었다 — 같은 문서에서 같은 것을 두 이름으로 부르면 읽는 사람이
#: 다른 것으로 읽는다.
_POLICY_CITATIONS = {
    "financial_statements": "OPM Guideline §재무제표 — 감사의견 적정 + 자본잠식 없음이면 찬성",
    "cash_dividend": "OPM Guideline §배당 — 흑자 + 배당성향 적정이면 찬성 (200% 초과 시 검토 필요)",
    "director_election": "OPM Guideline §이사선임 — 사내이사: 결격만 검증 / 사외이사: 독립성 + 결격",
    "audit_committee_election": "OPM Guideline §감사위원 — 엄격 검증 (장기연임 5년+ 소프트/6년+ 상법 시행령 §34조5항7호 + 독립성)",
    "director_compensation": "OPM Guideline §보수 — 소진율 30% 미만 + 인상 / 적자+인상 / 50% 이상 인상은 검토 필요",
    "audit_compensation": "참조 감사보수 규칙 — 1인당 평균 과소 / 50% 이상 인상 + 1인당 평균 과다는 검토 필요",
    "retirement_pay": "참조 퇴직금 규칙 + OPM #6/#7 — 황금낙하산 / 사외이사 퇴직금 / 지급률 2배수 이상 인상은 검토 필요",
    "articles_amendment": "OPM Guideline §정관변경 — 집중투표 배제 / 의결권 제한 / 이사 정원 축소 / 수권주식 증가 없으면 찬성",
    "treasury_share": "OPM Guideline §자사주 — 소각은 찬성 / 처분은 검토 필요",
    "capital_reduction": "OPM Guideline §자본감소 — 원칙 반대·예외 찬성(회생/구조조정 불가피·상장폐지 회피·주주가치 미훼손 유상감자·자사주 소각). 유형을 확정하지 못하면 검토 필요로 두고 원문 판단에 맡깁니다",
    "stock_option_grant": "OPM Guideline §주식매수선택권 — 희석률 한도·행사가격·부여대상 검토 필수. 검토 필요로 두고 원문 판단에 맡깁니다",
    "merger_or_restructuring": "OPM Guideline §구조개편 — 본문 검토",
    "shareholder_proposal": "OPM Guideline §주주제안 — 본문 검토",
    "other": "OPM Guideline §기타 — 위험 키워드 (감자/적대적/포이즌/CB) 없으면 일반 안건으로 보고 찬성",
}


def _policy_citation(category: str) -> str:
    return _POLICY_CITATIONS.get(category, _POLICY_CITATIONS["other"])


def _cumulative_voting_threshold(title: str) -> dict[str, Any] | None:
    """집중투표 최소 지분율 근사치.

    m명 선임 시 보장 당선 문턱은 행사 의결권 기준 약 1/(m+1).
    100% 출석·행사면 발행주식 대비도 같은 비율이고, 실제 보유지분
    기준은 출석률을 곱해 낮아진다.
    """
    if not title:
        return None
    if "집중투표" not in title and "이사" not in title:
        return None
    match = re.search(r"(\d+)\s*인\s*선임", title)
    if not match:
        return None
    seats = int(match.group(1))
    if seats <= 0:
        return None
    threshold = round(100 / (seats + 1), 2)
    return {
        "seats_to_elect": seats,
        "guaranteed_election_threshold_pct_of_votes_cast": threshold,
        "full_attendance_shareholding_threshold_pct": threshold,
        "actual_shareholding_threshold_formula": "attendance_rate_pct / (seats_to_elect + 1)",
        "basis": "단순 근사: 1/(선임 이사 수+1), 행사 의결권 기준. 전원 출석·전원 행사 시 발행주식 대비 동일.",
    }


def _concurrent_outside_band(total: int | None) -> str | None:
    if total is None:
        return None
    if total >= 3:
        return "strong_review"
    if total == 2:
        return "review"
    if total == 1:
        return "single_position"
    return "none"


def _candidate_independence_detail(eval_match: dict[str, Any]) -> dict[str, Any]:
    """후보 독립성 summary를 사용자 검토용 세부 사유로 펼친다.

    새 decision rule을 만들지 않고, 이미 계산된 director_evaluation sub_factors만
    읽기 쉬운 facts로 재구성한다.
    """
    independence = eval_match.get("independence") or {}
    sub = independence.get("sub_factors") or {}
    detail: dict[str, Any] = {
        "summary": independence.get("summary"),
    }

    major = sub.get("major_shareholder_relation") or {}
    if major:
        detail["major_shareholder_relation"] = {
            "result": major.get("result"),
            "raw": major.get("raw"),
        }

    transactions = sub.get("recent_3y_transactions") or {}
    if transactions:
        detail["recent_3y_transactions"] = {
            "result": transactions.get("result"),
            "raw": transactions.get("raw"),
        }

    employee = sub.get("recent_2y_employee") or {}
    if employee:
        detail["recent_2y_employee"] = {
            "result": employee.get("result"),
            "evidence": employee.get("evidence"),
        }

    five_year = sub.get("five_year_rule") or {}
    if five_year:
        detail["five_year_rule"] = {
            "result": five_year.get("result"),
        }

    return {k: v for k, v in detail.items() if v is not None}


def _candidate_performance_brief(eval_match: dict[str, Any]) -> dict[str, Any] | None:
    perf = eval_match.get("performance") or {}
    if not perf:
        return None
    brief = {
        "classification": perf.get("classification_ko") or perf.get("classification"),  # 한글 노출(부진 등)
        "total_score": perf.get("total_score"),
        "score_range": f"{perf.get('min_score')}~{perf.get('max_score')}"
        if perf.get("min_score") is not None and perf.get("max_score") is not None
        else None,
        "tenure_period": perf.get("tenure_period"),
        "tenure_fallback": perf.get("tenure_fallback"),
        "rationale": perf.get("rationale"),
    }
    return {k: v for k, v in brief.items() if v is not None}


def _candidate_review_profile(eval_match: dict[str, Any]) -> dict[str, Any]:
    """후보 선임 안건의 사용자-facing evidence bundle.

    후보 개인 본문 기반 evidence만 포함한다. 지배구조보고서, 최대주주 지분율 같은
    회사 context는 후보 개인 판단 근거로 섞지 않는다.
    """
    role = eval_match.get("role_type") or ""
    is_outside = any(k in role for k in ("사외", "독립"))
    apt = eval_match.get("appointment_type") or {}
    faithfulness = eval_match.get("faithfulness") or {}
    concurrent = faithfulness.get("concurrent_outside_directors") or {}
    concurrent_total = concurrent.get("total")
    profile: dict[str, Any] = {
        "candidate_name": eval_match.get("name"),
        "role_type": role,
        "appointment_type": apt.get("type") if isinstance(apt, dict) else None,
        "this_company_since": apt.get("earliest_start") if isinstance(apt, dict) else None,
        "disqualification": (eval_match.get("disqualification") or {}).get("summary"),
        "recommendation_reason_raw": faithfulness.get("recommendation_reason_raw"),
        "duty_plan_raw": faithfulness.get("duty_plan_raw"),
    }

    if is_outside:
        profile["independence_detail"] = _candidate_independence_detail(eval_match)
        if concurrent:
            profile["concurrent_outside_directors"] = {
                "total": concurrent_total,
                "band": _concurrent_outside_band(concurrent_total),
                "summary": concurrent.get("summary"),
                "signals": concurrent.get("signals"),
            }
    else:
        profile["independence_detail"] = {"summary": "not_applicable_inside_director"}

    performance = _candidate_performance_brief(eval_match)
    if performance:
        profile["performance_brief"] = performance

    audit_history = faithfulness.get("audit_history_check") or {}
    if audit_history.get("summary"):
        profile["audit_history_check"] = {
            "summary": audit_history.get("summary"),
            "status": audit_history.get("status"),
            "red_flags_count": len(audit_history.get("red_flags") or []),
        }

    return {k: v for k, v in profile.items() if v not in (None, "", [])}


def _pct_change_band(value: float | int | None) -> str | None:
    if value is None:
        return None
    if value <= 10:
        return "small_or_flat"
    if value < 30:
        return "moderate_increase"
    if value < 50:
        return "large_increase"
    return "very_large_increase"


def _utilization_band(value: float | int | None) -> str | None:
    if value is None:
        return None
    if value < 30:
        return "low_under_30"
    if value < 70:
        return "mid_30_to_70"
    if value < 100:
        return "normal_70_to_100"
    return "over_100"


def _audit_per_person_band(value: float | int | None) -> str | None:
    if value is None:
        return None
    if value < 50_000_000:
        return "low_under_50m"
    if value < 100_000_000:
        return "borderline_50m_to_100m"
    if value < 300_000_000:
        return "sufficient_100m_to_300m"
    return "high_over_300m"


def _treasury_pct_band(value: float | int | None) -> str | None:
    if value is None:
        return None
    if value < 5:
        return "low_under_5"
    if value < 10:
        return "notable_5_to_10"
    return "high_over_10"


def _payout_ratio_band(value: float | int | None) -> str | None:
    if value is None:
        return None
    if value <= 80:
        return "ordinary_under_80"
    if value <= 150:
        return "high_80_to_150"
    if value <= 200:
        return "borderline_150_to_200"
    return "very_high_over_200"


def _retirement_multiplier_evidence(amendments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    for amendment in amendments:
        before = amendment.get("before") or ""
        after = amendment.get("after") or ""
        before_multipliers = [float(m) for m in re.findall(r"(\d+\.?\d*)\s*배수?", before)]
        after_multipliers = [float(m) for m in re.findall(r"(\d+\.?\d*)\s*배수?", after)]
        if not before_multipliers and not after_multipliers:
            continue
        max_before = max(before_multipliers) if before_multipliers else None
        max_after = max(after_multipliers) if after_multipliers else None
        ratio = round(max_after / max_before, 2) if max_before and max_after else None
        strong_review = bool(
            (ratio is not None and ratio >= 2.0)
            or (max_before is None and max_after is not None and max_after >= 3.0)
        )
        evidence.append({
            "clause": amendment.get("clause"),
            "before_multipliers": before_multipliers,
            "after_multipliers": after_multipliers,
            "max_before": max_before,
            "max_after": max_after,
            "increase_ratio": ratio,
            "strong_review_signal": strong_review,
        })
    return evidence


def _retirement_target_expansion(amendments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    target_keywords = ("사외이사", "비등기임원", "고문", "상담역", "전직", "명예퇴직")
    expanded: list[dict[str, Any]] = []
    for amendment in amendments:
        before = amendment.get("before") or ""
        after = amendment.get("after") or ""
        hits = [kw for kw in target_keywords if kw in after and kw not in before]
        if hits:
            expanded.append({"clause": amendment.get("clause"), "targets": hits})
    return expanded


# 자본시장법 §165조의20 적용 임계 — 자산총액 2조원
_GENDER_DIVERSITY_ASSET_KRW = 2_000_000_000_000


def _extract_facts(
    category: str,
    title: str,
    eval_match: dict[str, Any] | None,
    fin_payload: dict[str, Any] | None,
    comp_payload: dict[str, Any] | None,
    all_evals: list[dict[str, Any]] | None = None,
    fy_raw_from_agenda: dict[str, Any] | None = None,
    retirement_payload: dict[str, Any] | None = None,
    ownership_payload: dict[str, Any] | None = None,
    confirmed_payload: dict[str, Any] | None = None,
    confirmed_year: int | None = None,
    crosscheck_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """카테고리별 검증 가능한 정량 fact dict (None 값은 제외)."""
    fin_summary = ((fin_payload or {}).get("data") or {}).get("summary", {}) or {}
    audit = ((fin_payload or {}).get("data") or {}).get("audit_opinion", {}) or {}
    comp_summary = _compensation_data(comp_payload).get("summary", {}) or {}
    facts: dict[str, Any] = {}

    if category == "financial_statements":
        latest_op = audit.get("summary", {}).get("latest_opinion") if "summary" in audit else None
        facts["audit_opinion"] = latest_op
        facts["fy_prior_net_income_krw_dart"] = fin_summary.get("net_income_krw")  # DART API (확정치)
        # 자본잠식은 **판정에 쓴 것과 같은 연도**로 싣는다. 판정은 FY(N-1)A(있으면)로 하는데
        # 사실란만 FY(N-2)A 를 보여주면, 「자기자본 0 이하」와 「자본총계 +71억」이 한 메모에
        # 나란히 실린다(실측 엔케이젠바이오텍). 판정과 근거는 같은 해를 가리켜야 한다.
        # 호출부가 이미 판정과 같은 payload(`state_metrics`)를 넘긴다 — 여기서 다시 고르면
        # 두 곳이 어긋날 여지가 생긴다. 실제로 그렇게 어긋나 있었다.
        facts["capital_impairment_status"] = fin_summary.get("capital_impairment_status")
        # 잠식률은 `(자본금 - 자본총계) / 자본금` 이라 **잠식이 없으면 음수**다. 그 값은 잠식률이
        # 아니라 자본 여유폭인데 라벨은 「자본잠식률(%)」이라, 정상 회사에 「-44,711.79」 같은
        # 읽을 수 없는 숫자가 나갔다. 잠식이 실제로 있을 때만 싣는다 — 상태는 위 필드가 말한다.
        _cap_ratio = fin_summary.get("capital_impairment_ratio_pct")
        if _cap_ratio is not None and _cap_ratio > 0:
            facts["capital_impairment_ratio_pct"] = _cap_ratio
        facts["cfo_to_op_ratio"] = fin_summary.get("cfo_to_op_ratio")
        facts["accruals_gap_pct"] = fin_summary.get("accruals_gap_pct")
        facts["interest_coverage_ratio"] = fin_summary.get("interest_coverage_ratio")
        # 본문 수치를 **왜** 못 실었는지 말한다. 외화 표시라 안 쓴 것과 못 읽은 것은 뜻이 다르고,
        # 아무 말 없이 비면 사용자는 어느 쪽인지 알 수 없다(실측 두산밥캣·컬러레이 등 외국법인).
        _skipped = (fy_raw_from_agenda or {}).get("skipped_units") or []
        if _skipped:
            facts["fy_raw_skipped_currency"] = " · ".join(sorted(set(_skipped)))
        _mixed = (fy_raw_from_agenda or {}).get("scope_mixed") or []
        if _mixed:
            # 손익은 연결에서, 재무상태는 별도에서 온 경우다(실측 2건). 두 표를 섞어 비율을 내면
            # 분자·분모가 다른 회사 것이 된다 — 값을 버리지는 않되 그 사실을 밝힌다.
            _ko = {"consolidated": "연결", "separate": "별도"}
            facts["fy_raw_scope_mixed"] = " + ".join(_ko.get(s, s) for s in _mixed)
        _rejected = (fy_raw_from_agenda or {}).get("rejected_accounts") or {}
        if _rejected:
            facts["fy_raw_rejected_accounts"] = " · ".join(
                f"{_FY_METRIC_KO.get(k, k)} ← 「{v}」" for k, v in _rejected.items()
            )
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
            # 본문 파싱이 맞는지 확정치로 검산한다. **반드시 FY(N-2)A 와 맞댄다** — 등식이
            # 「본문의 전기 = FY(N-2)A 의 당기」라서다. 판정용 payload 는 이제 FY(N-1) 일 수
            # 있으므로 그걸로 검산하면 한 해 어긋난 값을 비교해 없는 불일치를 만든다.
            _chk = _cross_check_provisional_revenue(
                fy_raw_from_agenda,
                ((crosscheck_payload or {}).get("data") or {}).get("summary") or {})
            if _chk:
                facts["fy_raw_cross_check"] = _chk

        # 승인 대상 연도의 **확정치**. 공고 잠정치 추출 성공 여부와 **무관하게** 싣는다 —
        # 오히려 공고를 못 읽었을 때 이것만이 승인 대상 연도의 유일한 숫자가 된다.
        # (한때 잠정 블록 안에 넣었다가 삼성전자처럼 공고 파싱이 안 되는 회사에서 통째로 사라졌다.)
        _conf_outer = ((confirmed_payload or {}).get("data") or {}).get("summary") or {}
        if _conf_outer:
            facts["fy_current_confirmed_year"] = confirmed_year
            for _src, _dst in (
                ("revenue_krw", "fy_current_revenue_krw_confirmed"),
                ("operating_profit_krw", "fy_current_operating_profit_krw_confirmed"),
                ("net_income_krw", "fy_current_net_income_krw_confirmed"),
                ("total_equity_krw", "fy_current_total_equity_krw_confirmed"),
            ):
                if _conf_outer.get(_src) is not None:
                    facts[_dst] = _conf_outer.get(_src)

            # 잠정(P) ↔ 확정(A) 대조. **같은 것끼리만 뺀다** — 순이익은 공고가 연결 총액
            # (「Ⅶ.당기순손실」)을 싣고 확정치는 지배주주 귀속이라 개념이 다르다. 실측 영풍
            # FY2025: 총액 +366억 vs 귀속 -83억으로 449억이 벌어지는데 전부 비지배지분 몫이다.
            # 이 둘을 빼서 「감사 과정에서 흑자가 적자로 뒤집혔다」고 쓰면 없는 사건을 만들어낸다.
            # 대조에서 빼되 뺐다는 사실을 밝힌다 — 말없이 「일치」라고만 하면 그 자체로 오해다.
            _p_src = fy_raw_from_agenda or {}
            # 본문 표의 단위(원/천원/백만원) — 그만큼은 반올림 오차이지 조정이 아니다.
            _tol = max((v.get("scale") or 1)
                       for v in ((_p_src.get("source_accounts") or {}).values() or [{}])) \
                if (_p_src.get("source_accounts") or {}) else 1
            _moved, _checked = [], []
            for _label, _prov, _src in (
                ("매출", "fy_current_revenue_krw", "revenue_krw"),
                ("영업이익", "fy_current_operating_profit_krw", "operating_profit_krw"),
                ("자본총계", "fy_current_total_equity_krw", "total_equity_krw"),
            ):
                _p, _c = _p_src.get(_prov), _conf_outer.get(_src)
                if _p is None or _c is None:
                    continue
                _checked.append(_label)
                # **표의 단위만큼은 조정이 아니다.** 공고 표의 28%가 백만원·천원 단위라(캐시
                # 479개 표 실측) 잠정치는 그 단위로 반올림돼 있고 확정치는 원 단위로 정확하다.
                # 그대로 빼면 **아무것도 안 움직였는데 매번 「조정」이 나간다** — 반올림 나머지를
                # 감사 조정분이라고 사용자에게 보고하는 셈이다. 허용오차를 단위 크기로 둔다.
                if abs(_c - _p) >= max(_tol, 1):
                    # 두 값을 나란히 쓰면 반올림에 먹혀 「4.02조원 → 4.02조원」이 된다.
                    # 움직인 크기를 쓴다 — 그게 곧 감사 조정분이다.
                    _moved.append(f"{_label} {_won(_c - _p)}")
            if _checked:
                _fy = confirmed_year
                facts["fy_provisional_vs_confirmed"] = (
                    (f"FY{_fy}P(공고 잠정) → FY{_fy}A(확정) 조정 — {' · '.join(_moved)}"
                     if _moved else f"FY{_fy}P(공고 잠정)와 FY{_fy}A(확정)가 일치")
                    + f" · 대조 {'·'.join(_checked)}"
                    + " · 순이익은 공고가 연결 총액, 확정치가 지배주주 귀속이라 개념이 달라 제외"
                )

    elif category == "cash_dividend":
        facts["payout_ratio_pct"] = fin_summary.get("payout_ratio_pct")
        facts["payout_ratio_band"] = _payout_ratio_band(fin_summary.get("payout_ratio_pct"))
        facts["net_income_krw"] = fin_summary.get("net_income_krw")
        facts["capital_impairment_status"] = fin_summary.get("capital_impairment_status")
        facts["fcf_krw"] = fin_summary.get("fcf_krw")
        facts["dividend_to_fcf_pct"] = fin_summary.get("dividend_to_fcf_pct")
    elif category == "director_compensation":
        comp_values = _comp_target_values(comp_payload, "이사")
        facts["increase_rate_pct"] = comp_values.get("increase_rate_pct")
        facts["increase_rate_band"] = _pct_change_band(comp_values.get("increase_rate_pct"))
        facts["utilization_rate_pct"] = comp_values.get("utilization_rate_pct")
        facts["utilization_rate_band"] = _utilization_band(comp_values.get("utilization_rate_pct"))
        facts["limit_krw"] = comp_values.get("limit_krw")
        facts["prior_limit_krw"] = comp_values.get("prior_limit_krw")
        facts["prior_paid_krw"] = comp_values.get("prior_paid_krw")
        facts["director_count"] = comp_values.get("headcount")
        if comp_values.get("limit_krw") and comp_values.get("headcount"):
            facts["director_per_person_limit_krw"] = comp_values["limit_krw"] // comp_values["headcount"]
        facts["net_income_krw"] = fin_summary.get("net_income_krw")
        facts["net_income_yoy_pct"] = fin_summary.get("net_income_yoy_pct")
        facts["capital_impairment_status"] = fin_summary.get("capital_impairment_status")
    elif category == "audit_compensation":
        audit_values = _comp_target_values(comp_payload, "감사")
        facts["audit_total_limit_krw"] = audit_values.get("limit_krw")
        facts["audit_count"] = audit_values.get("headcount")
        if audit_values.get("limit_krw") and audit_values.get("headcount"):
            facts["audit_per_person_krw"] = audit_values["limit_krw"] // audit_values["headcount"]
            facts["audit_per_person_band"] = _audit_per_person_band(facts["audit_per_person_krw"])
        facts["audit_increase_rate_pct"] = audit_values.get("increase_rate_pct")
        facts["audit_increase_rate_band"] = _pct_change_band(audit_values.get("increase_rate_pct"))
        facts["audit_prior_limit_krw"] = audit_values.get("prior_limit_krw")
        facts["audit_prior_paid_krw"] = audit_values.get("prior_paid_krw")
        facts["net_income_krw"] = fin_summary.get("net_income_krw")
        facts["capital_impairment_status"] = fin_summary.get("capital_impairment_status")
    elif category == "retirement_pay":
        amends = ((retirement_payload or {}).get("data") or {}).get("amendments") or []
        facts["amendments_count"] = len(amends)
        facts["retirement_multiplier_evidence"] = _retirement_multiplier_evidence(amends)
        facts["retirement_target_expansion"] = _retirement_target_expansion(amends)
        if amends:
            # raw 노출 (LLM 판단용) — 처음 5개. length 300자 통일 (B1/B2 raw와 통일).
            facts["amendments_sample"] = [
                {"clause": a.get("clause"), "before": (a.get("before") or "")[:300], "after": (a.get("after") or "")[:300], "reason": (a.get("reason") or "")[:120]}
                for a in amends[:5]
            ]
        facts["capital_impairment_status"] = fin_summary.get("capital_impairment_status")
    elif category == "treasury_share":
        ownership_summary = ((ownership_payload or {}).get("data") or {}).get("summary") or {}
        treasury_pct = ownership_summary.get("treasury_pct")
        facts["treasury_pct"] = treasury_pct
        facts["treasury_pct_band"] = _treasury_pct_band(treasury_pct)
        facts["related_total_pct"] = ownership_summary.get("related_total_pct")
        facts["active_signal_count"] = ownership_summary.get("active_signal_count")
    elif category in ("director_election", "audit_committee_election"):
        if eval_match:
            facts["candidate_name"] = eval_match.get("name")
            role = eval_match.get("role_type") or ""
            facts["role_type"] = role
            facts["candidate_review_profile"] = _candidate_review_profile(eval_match)
            facts["agenda_action"] = eval_match.get("agenda_action")
            apt = eval_match.get("appointment_type") or {}
            if isinstance(apt, dict) and apt.get("type"):
                facts["appointment_type"] = apt.get("type")  # new / renewed / ambiguous
                if apt.get("earliest_start"):
                    facts["this_company_since"] = apt.get("earliest_start")
            five_y = ((eval_match.get("independence") or {}).get("sub_factors") or {}).get("five_year_rule", {}).get("result")
            if five_y:
                facts["tenure_status"] = five_y
            # 사내이사는 독립성 평가 비대상 — "충족"으로 오인 방지 (Ralph 9, 260510)
            is_outside = any(k in role for k in ("사외", "독립"))
            if is_outside:
                facts["independence"] = (eval_match.get("independence") or {}).get("summary")
            else:
                facts["independence"] = "독립성 평가 비대상 (사내이사)"
            facts["disqualification"] = (eval_match.get("disqualification") or {}).get("summary")
            ah = (eval_match.get("faithfulness") or {}).get("audit_history_check", {}).get("summary")
            if ah:
                facts["audit_history_check"] = ah
            # 사외이사 겸직 카운트 (Ralph 9) — 사외이사 한정
            if is_outside:
                co = (eval_match.get("faithfulness") or {}).get("concurrent_outside_directors")
                if co:
                    facts["concurrent_outside_positions"] = co.get("total")
                    facts["concurrent_summary"] = co.get("summary")
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
            # 묶음 후보별 mini-summary (LLM/사용자 detail 노출 — fix 260510)
            # 후보 5명 같이 묶인 안건도 각자 평가 detail 보여야 LLM 판단 가능.
            facts["candidate_summary"] = []
            for ev in all_evals[:10]:  # 묶음 최대 10명
                role = ev.get("role_type") or ""
                is_outside_ev = any(k in role for k in ("사외", "독립"))
                apt_type = (ev.get("appointment_type") or {}).get("type")
                indep = "비대상 (사내)"
                if is_outside_ev:
                    indep = (ev.get("independence") or {}).get("summary")
                disq = (ev.get("disqualification") or {}).get("summary")
                cand_info = {
                    "name": ev.get("name"),
                    "role": role,
                    "appointment": apt_type,
                    "independence": indep,
                    "disqualification": disq,
                    "review_profile": _candidate_review_profile(ev),
                }
                # 사외이사 겸직 카운트 (Ralph 9)
                if is_outside_ev:
                    co = (ev.get("faithfulness") or {}).get("concurrent_outside_directors")
                    if co:
                        cand_info["concurrent_positions"] = co.get("total")
                        cand_info["concurrent_summary"] = co.get("summary")
                facts["candidate_summary"].append(cand_info)

    return {k: v for k, v in facts.items() if v is not None}


def _extract_risks(
    category: str,
    eval_match: dict[str, Any] | None,
    fin_payload: dict[str, Any] | None,
    comp_payload: dict[str, Any] | None,
    title: str,
    retirement_payload: dict[str, Any] | None = None,
    ownership_payload: dict[str, Any] | None = None,
) -> list[str]:
    """카테고리별 위험 신호 list (LLM/사용자 추가 검토 hint)."""
    fin_summary = ((fin_payload or {}).get("data") or {}).get("summary", {}) or {}
    comp_summary = _compensation_data(comp_payload).get("summary", {}) or {}
    risks: list[str] = []

    cap_status = fin_summary.get("capital_impairment_status")
    if cap_status == "full":
        risks.append("완전 자본잠식")
    elif cap_status == "partial_50plus":
        risks.append("자본잠식률 50% 이상")
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
            risks.append("장기연임 (5년+ 소프트 경보 / 6년+ 상법 시행령 §34조5항7호 결격 가능)")
        if ah == "red_flag":
            risks.append("이사 회계 위험 이력 발견 (원문 메모 검토)")

    if category == "director_compensation":
        comp_values = _comp_target_values(comp_payload, "이사")
        util = comp_values["utilization_rate_pct"]
        inc = comp_values["increase_rate_pct"]
        if util is not None and util < 30 and inc and inc > 0:
            risks.append(f"소진율 {util:.0f}%인데 인상 {inc:+.0f}%")
        elif inc is not None and inc >= 50:
            risks.append(f"한도 대폭 인상 {inc:+.0f}%")

    if category == "audit_compensation":
        audit_values = _comp_target_values(comp_payload, "감사")
        audit_total = audit_values.get("limit_krw")
        audit_count = audit_values.get("headcount")
        audit_inc = audit_values.get("increase_rate_pct")
        if audit_total and audit_count:
            audit_per_person = audit_total / audit_count
            band = _audit_per_person_band(audit_per_person)
            if band == "low_under_50m":
                risks.append(f"감사 1인당 보수한도 {audit_per_person/1e8:.2f}억원 — 낮은 한도 가능성")
            elif band == "borderline_50m_to_100m":
                risks.append(f"감사 1인당 보수한도 {audit_per_person/1e8:.2f}억원 — 경계 구간")
            elif band == "high_over_300m":
                risks.append(f"감사 1인당 보수한도 {audit_per_person/1e8:.2f}억원 — 고액 구간")
        if audit_inc is not None and audit_inc >= 50:
            risks.append(f"감사 보수한도 강한 급증 {audit_inc:+.0f}%")
        elif audit_inc is not None and audit_inc >= 30:
            risks.append(f"감사 보수한도 급증 {audit_inc:+.0f}%")

    if category == "retirement_pay":
        amends = ((retirement_payload or {}).get("data") or {}).get("amendments") or []
        if amends:
            risks.append(f"퇴직금 변경 {len(amends)}건 — 변경 조항 원문 검토 권장")
        multiplier_evidence = _retirement_multiplier_evidence(amends)
        if any(item.get("strong_review_signal") for item in multiplier_evidence):
            risks.append("퇴직금 지급률 2배 이상 증가 또는 3배수 이상 신설")
        target_expansion = _retirement_target_expansion(amends)
        if target_expansion:
            targets = sorted({target for item in target_expansion for target in item.get("targets", [])})
            risks.append(f"퇴직금 지급 대상 확장 가능성 ({', '.join(targets[:4])})")
        # 황금낙하산 / 사외이사 키워드 hit 탐지
        for a in amends:
            after = (a.get("after") or "")
            if "황금낙하산" in after or "경영권 변동" in after:
                risks.append("황금낙하산 또는 경영권 변동 special 가산 신설 (참조 퇴직금 규칙상 검토)")
                break
        for a in amends:
            after = a.get("after") or ""
            before = a.get("before") or ""
            _out = ("사외이사", "독립이사")     # 상법 1차 개정 명칭 변경 — 둘 다 본다
            if any(k in after for k in _out) and not any(k in before for k in _out):
                risks.append("사외이사(독립이사) 퇴직금 신설 (OPM #6 검토)")
                break

    if category == "cash_dividend":
        payout = fin_summary.get("payout_ratio_pct")
        if payout is not None and payout > 200:
            risks.append(f"배당성향 {payout}% (>200%)")
        fcf = fin_summary.get("fcf_krw")
        if fcf is not None and fcf < 0:
            risks.append(f"FCF 음수 ({fcf:,}원)")
        div_to_fcf = fin_summary.get("dividend_to_fcf_pct")
        if div_to_fcf is not None and div_to_fcf > 100:
            risks.append(f"FCF 대비 배당 {div_to_fcf}% (>100%)")

    if category == "financial_statements":
        cfo_to_op = fin_summary.get("cfo_to_op_ratio")
        if cfo_to_op is not None and cfo_to_op < 0.7:
            risks.append(f"영업현금흐름/영업이익 {cfo_to_op:.2f} (<0.7)")
        accruals_gap = fin_summary.get("accruals_gap_pct")
        if accruals_gap is not None and abs(accruals_gap) > 30:
            risks.append(f"발생액 괴리 {accruals_gap}% — 이익과 현금흐름의 차이가 큽니다(기준 ±30%)")
        interest_coverage = fin_summary.get("interest_coverage_ratio")
        if interest_coverage is not None and interest_coverage < 2:
            risks.append(f"이자보상배율 {interest_coverage:.2f} (<2)")

    if category == "treasury_share":
        ownership_summary = ((ownership_payload or {}).get("data") or {}).get("summary") or {}
        treasury_pct = ownership_summary.get("treasury_pct")
        if treasury_pct is not None and treasury_pct >= 10:
            risks.append(f"자사주 비율 {treasury_pct}% (10% 이상)")
        elif treasury_pct is not None and treasury_pct >= 5:
            risks.append(f"자사주 비율 {treasury_pct}% (5% 이상)")

    if category == "articles_amendment":
        t = title or ""
        if "집중투표" in t and ("배제" in t or "삭제" in t):
            risks.append("집중투표 배제")
        if "초다수결의제" in t:
            risks.append("초다수결의제 도입")

    return risks


def _decide_dividend(agenda_title: str, fm_payload: dict[str, Any] | None, company_name: str = "") -> tuple[str, str]:
    """배당 안건 — 보수화 (애매→REVIEW).

    REVIEW: 완전 자본잠식 / 적자 (음수 순익) / 배당성향 200%+ / 재무 데이터 없음.
    FOR: 흑자 + 배당성향 적정.
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
        return "REVIEW", "완전 자본잠식 — 배당 재원과 주주가치 영향 검토"
    # ralph iter9+15+21: 배당 절차 안건은 재무 (적자 등) 무관 자동 FOR.
    # iter21 추가: "자본준비금" / "이익잉여금 전입" — 회계 절차 (리가켐바이오 2/2 FOR)
    procedural_kws = ("분기", "기준일", "중간배당", "동등배당", "배당정책", "배당절차", "절차",
                      "자본준비금", "이익잉여금 전입", "이익잉여금전입")
    if any(kw in agenda_title for kw in procedural_kws):
        return "FOR", "배당 절차·회계 안건 — 재무와 무관(원칙적 찬성)"
    if ni is not None and ni < 0:
        # **당기 순손익은 배당 재원이 아니다.** 상법 제462조제1항의 배당가능이익은 순자산에서
        # 자본금·준비금·미실현이익을 뺀 값이고, 산정 기준은 **별도(개별) 재무제표**다. 누적
        # 이익잉여금이 두터우면 당기 적자라도 배당이 적법하고(경기 하강기 제조업에 흔하다),
        # 반대로 당기 흑자여도 미처리결손금이 크면 배당할 수 없다. 부호 하나로 재원을 대신하면
        # 양방향으로 오판한다. 여기서 보는 값은 연결이라 그 사실도 함께 밝힌다.
        retained = summary.get("retained_earnings_krw")
        base = f"당기 순손실 {_won(ni)}"
        if retained is not None and retained > 0:
            return "REVIEW", (
                f"{base} — 다만 이익잉여금 {_won(retained)}이 남아 있어 재원 자체는 있을 수 있습니다. "
                f"배당가능이익은 상법 제462조제1항에 따라 별도 재무제표 기준으로 산정되므로 "
                f"별도 이익잉여금으로 확인 필요"
            )
        if retained is not None and retained <= 0:
            return "REVIEW", (
                f"{base} · 이익잉여금 {_won(retained)} — 누적 결손입니다. "
                f"배당가능이익(상법 제462조제1항, 별도 재무제표 기준) 존부를 확인해야 합니다"
            )
        return "REVIEW", (
            f"{base} — 배당가능이익은 상법 제462조제1항에 따라 별도 재무제표 기준으로 산정됩니다. "
            f"별도 이익잉여금으로 재원을 확인하십시오"
        )
    # 배당성향 200%+ 명백 과도 (이전엔 150%였으나 150-200%도 mainstream FOR)
    if payout is not None and payout > 200:
        return "REVIEW", f"배당성향 {payout}% (>200%) — 명백한 과도 배당"
    if ni is not None and ni > 0 and cap_status != "partial":
        return "FOR", f"흑자 + 자본 양호 (배당성향 {payout if payout is not None else '?'}%)"
    if ni is None and cap_status is None and payout is None:
        return "NO_DATA", "재무 fact 미확인 — 배당 적정성 본문 검토 필요"
    return "REVIEW", "배당 적정성 본문 검토 필요"


# ── 메인 advise builder ──

_SEGMENT_CONTEXT_DEFAULT_CHARS = 8000
_SEGMENT_CONTEXT_MAX_CHARS = 30000  # proxy_advise 응답이 이미 대형 — business_details(6만)보다 낮게 cap


async def build_proxy_advise_payload(
    company_query: str,
    *,
    year: int | None = None,
    meeting_type: str = "annual",
    vote_style: str = "open_proxy",
    scope: str = "decisions",
    check_audit_history: bool = False,
    segment_context_chars: int = _SEGMENT_CONTEXT_DEFAULT_CHARS,
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
    total_started_at = time.perf_counter()
    timings_ms: dict[str, int] = {}

    def _mark(stage: str, started_at: float) -> None:
        timings_ms[stage] = int((time.perf_counter() - started_at) * 1000)

    client = get_dart_client()
    calls_start = client.api_call_snapshot()

    stage_started_at = time.perf_counter()
    resolution = await resolve_company_query(company_query)
    _mark("resolve_company", stage_started_at)
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        timings_ms["total"] = int((time.perf_counter() - total_started_at) * 1000)
        return ToolEnvelope(
            tool="proxy_advise_before_meeting",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[f"'{company_query}' 회사 식별 실패"],
            data={
                "query": company_query,
                "usage": build_usage(client.api_call_snapshot() - calls_start),
                "timings_ms": timings_ms,
            },
        ).to_dict()
    if resolution.status == AnalysisStatus.AMBIGUOUS:
        timings_ms["total"] = int((time.perf_counter() - total_started_at) * 1000)
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
                "timings_ms": timings_ms,
            },
        ).to_dict()

    selected = resolution.selected

    # ── target_year 결정 (260723 변경) ──
    # 종전: year 미지정 시 달력 전년(today.year-1) 하드코딩 — 주총 시즌(2~3월)에
    # 기본 호출하면 1년 묵은 회차를 분석하는 모순 (notice tool의 auto=최신 공고와 불일치).
    # 현행: 최신 소집공고(12개월 lookback) pre-resolution으로 실제 회의연도를 확정.
    #   - 요청 meeting_type(기본 annual=정기) 그대로 검색 — 정기/임시 명시 확인.
    #   - 비용: list.json 1콜 (doc 파싱은 get_document_cached 공유 — 본 payload가 재사용).
    #   - 공고 미발견 시 종전 기본값(전년) fallback + warning.
    year_resolution: dict[str, Any]
    pre_resolved: dict[str, Any] | None = None
    if year:
        target_year = year
        year_resolution = {"mode": "user_specified", "basis": f"사용자 지정 연도 {year}"}
    else:
        stage_started_at = time.perf_counter()
        resolve_error: str | None = None
        try:
            pre_resolved = await resolve_latest_meeting_year(
                selected["corp_code"], meeting_type=meeting_type,
            )
        except Exception as exc:  # 260723 리뷰: '조회 실패'를 '공고 없음'으로 위장하지 않는다
            pre_resolved = None
            resolve_error = type(exc).__name__
        _mark("resolve_meeting_year", stage_started_at)
        if pre_resolved:
            target_year = pre_resolved["year"]
            _md = pre_resolved.get("meeting_date")
            year_resolution = {
                "mode": "latest_notice",
                "basis": (
                    f"최신 {_MEETING_TYPE_KO.get(pre_resolved['meeting_type'], pre_resolved['meeting_type'])}주총 "
                    f"소집공고({pre_resolved.get('notice_date', '?')} 공시) 기준 자동 선택 — "
                    f"회의일 {(_md.isoformat() if _md else '파싱 실패 (공시 연도로 추정)')}"
                ),
                "resolved_meeting_type": pre_resolved["meeting_type"],
                "notice_rcept_no": pre_resolved.get("notice_rcept_no"),
                "meeting_phase": pre_resolved.get("meeting_phase"),
            }
        elif resolve_error:
            target_year = date.today().year - 1
            year_resolution = {
                "mode": "resolve_error",
                "basis": (
                    f"최신 소집공고 조회 실패({resolve_error}) — 달력 전년({target_year})으로 임시 대체했습니다. "
                    f"일시 장애일 수 있으니 재시도하거나 year를 직접 지정해 재조회 권장"
                ),
            }
        else:
            target_year = date.today().year - 1
            year_resolution = {
                "mode": "fallback_prev_year",
                "basis": (
                    f"최근 12개월(+예정분) 내 {_MEETING_TYPE_KO.get(meeting_type, meeting_type)}주총 소집공고를 "
                    f"찾지 못해 달력 전년({target_year})으로 대체했습니다 — 연도를 직접 지정해 다시 조회하실 수 있습니다"
                ),
            }
    # 재무 fiscal year 매핑 — 기준은 하나다: **주총일 시점에 이미 제출된 가장 최근 사업보고서.**
    #
    # 종전에는 `target_year - 2` 로 못박았다. 그 값이 맞는 이유는 3월 정기주총 일정에 있다 —
    # 그때는 FY(N-1) 사업보고서가 아직 안 나왔으니 마지막 확정치가 FY(N-2)다. 그런데 그건
    # 정기주총 일정에서 나온 어림이지 규칙이 아니어서, 연중에 열리는 임시주총에서는 한 해
    # 과하게 보수적이었다. 8월 임시주총은 그해 3월 제출된 FY(N-1) 사업보고서를 이미 볼 수 있는데
    # 2년 전 숫자로 자본잠식·배당을 판단하고 있었다.
    #
    # 「그 시점에 제출돼 있었나」로 되돌리면 특례 분기가 필요 없고, 3월 정기주총은 자동으로
    # 예전과 같은 답(N-2)이 나온다. look-ahead 도 정의상 막힌다.
    # 비용은 list.json 1콜. 회의일을 모르면(사용자가 year 를 직접 준 경우) 종전값으로 물러난다.
    #
    # 정기주총에서는 이 값을 **비교 기준**으로 남긴다(= 직전 확정 FY(N-2)). 승인 대상 FY(N-1)은
    # 공고의 잠정 재무제표로 이미 싣고 있으므로, 기준연도를 FY(N-1)로 올려버리면 비교 상대가
    # 사라진다. 대신 아래 `confirmed_year` 로 **주총 시점에 이미 나와 있던 FY(N-1) 확정치**를
    # 따로 가져온다 — 시장 전수(12월 결산 2,731사) 기준 사업보고서는 소집공고 +7일(중앙값)에
    # 나오고, 상법 §363(주총 2주 전 통지)과 겹치면 최소 81.7%가 주총 전에 확정된다.
    # 즉 표를 던지는 시점에는 잠정과 확정이 **둘 다** 있다. 소집공고 시점에서 멈추면 안 된다.
    fin_year = target_year - 2
    fin_year_basis = f"주총 연도({target_year}) 기준 직전 확정 사업연도로 추정"
    confirmed_year: int | None = None          # 주총 시점에 확정돼 있던 FY(N-1)
    confirmed_ref: dict[str, Any] | None = None
    _meeting_iso = (pre_resolved or {}).get("meeting_date")
    _is_egm = (pre_resolved or {}).get("meeting_type") == "extraordinary"
    if _meeting_iso is not None:
        stage_started_at = time.perf_counter()
        try:
            _annual_ref = await latest_annual_report_before(
                selected["corp_code"], _meeting_iso.strftime("%Y%m%d"))
        except Exception:      # 조회 실패는 「없음」이 아니다 — 종전 기준연도로 물러난다
            _annual_ref = None
        _mark("latest_annual_report", stage_started_at)
        _ref_fy = (_annual_ref or {}).get("fiscal_year")
        if _ref_fy and _is_egm and _ref_fy > fin_year:
            # 임시주총은 승인 대상이 따로 없다 — 그 시점 최신 확정치가 곧 분석 기준이다.
            fin_year = _ref_fy
            fin_year_basis = (
                f"주총일({_meeting_iso.isoformat()}) 시점 최신 사업보고서"
                f"({_annual_ref.get('report_nm', '')}, {_annual_ref.get('rcept_dt', '')} 제출) 기준"
            )
        elif _ref_fy and not _is_egm and _ref_fy > fin_year:
            # 정기주총 — 기준연도는 FY(N-2)로 두고, 확정 FY(N-1)을 **추가로** 확보한다.
            confirmed_year = _ref_fy
            confirmed_ref = _annual_ref

    # scope="all" auto fallback to "decisions" — 8 upstream 동시 호출은 Claude.ai timeout 60s 자주 초과.
    # 사용자 효용 거의 동일 (decisions에 핵심 정보 모두 포함). warning은 data dict에 명시.
    scope_all_warning: str | None = None
    if scope == "all":
        scope_all_warning = (
            "요청하신 범위는 동시 조회량이 많아 응답이 지연될 수 있어, 의결권 판단 결과만 "
            "돌려드립니다. 지분·행동주의·정책 비교 등 개별 영역은 따로 요청하시면 자세히 분석합니다."
        )
        scope = "decisions"

    # vote_style 정책 로딩 (success / soft-fail)
    policy = _load_vote_style_policy(vote_style)
    policy_id = (policy or {}).get("policy_id") or vote_style
    policy_meta = (policy or {}).get("policy_meta") or {}

    # ── F6 (Phase 4) corpCode pre-warm: gather 전에 보장 ──
    # 6 worker가 동시에 _load_corp_codes 호출 시 race 위험 (F7 lock으로도 처리되지만
    # 명시적 사전 로드로 wait_for timeout 안에서 발생하지 않도록 함).
    stage_started_at = time.perf_counter()
    try:
        await client._load_corp_codes()
    except Exception:
        # corpCode 실패는 _safe가 각 worker에서 또 retry — 여기선 silent
        pass
    _mark("prewarm_corp_codes", stage_started_at)

    # ── 6 upstream 병렬 호출 (retry 3회 + per-call timeout 60s + process cache) ──
    # F1 (Phase 3): retry 3회 + exponential backoff
    # F8 (Phase 4): asyncio.wait_for(timeout=60) — 단일 upstream hang이 전체 timeout 잠식 방지
    # F11 (Phase 4): process-level cache (company+tool+scope+year 키) — 같은 process 내 재호출 동일 결과
    async def _safe(fn, *args, timing_label: str | None = None, **kw):
        upstream_started_at = time.perf_counter()
        # F11 cache key (260723: bsns_year 추가 — business_details 연도별 조회 충돌 방지)
        cache_key = (selected.get("corp_code") or company_query, fn.__name__, kw.get("scope"), kw.get("year"), kw.get("meeting_type"), kw.get("bsns_year"))
        cached = _PROXY_ADVISE_CACHE.get(cache_key)
        if cached is not None:
            if timing_label:
                _mark(f"upstream.{timing_label}", upstream_started_at)
            return cached

        last_exc = None
        for attempt in range(3):  # 1차 + retry 2회 (총 3회 시도)
            try:
                # F8: 단일 upstream 60s cap (전체 wait_for 120s 안에서 6 worker 각자 60s)
                result = await asyncio.wait_for(fn(*args, **kw), timeout=60.0)
                _PROXY_ADVISE_CACHE[cache_key] = result
                if timing_label:
                    _mark(f"upstream.{timing_label}", upstream_started_at)
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
        if timing_label:
            _mark(f"upstream.{timing_label}", upstream_started_at)
        return err_result

    # F10 (Phase 4): 6 → 3 worker — 동시성 줄여 race 완화 + DART API margin 확보
    _UPSTREAM_SEM = asyncio.Semaphore(3)

    async def _safe_throttled(fn, *args, timing_label: str | None = None, **kw):
        async with _UPSTREAM_SEM:
            return await _safe(fn, *args, timing_label=timing_label, **kw)

    stage_started_at = time.perf_counter()
    # 같은 주총을 4개 scope(summary/agenda/compensation/aoi_change)로 따로 부르던 것을 scope="advise" 1회로 통합.
    #   - 회차 선별(공시 검색 + 후보 필터)이 4→1회로 감소 — 콜 수 자체 절감(throttle 하한과 무관하게 이득).
    #   - advise = full에서 results만 제외한 scope: agenda/compensation/aoi_change 데이터 + comp/aoi viewer 보정은
    #     모두 포함하되, proxy_advise가 안 쓰는 results는 fetch 안 함(회의 후 회사의 results fetch=네트워크 wall-clock 손해 회피).
    # 감사의견은 `scope="summary"` 에 실리지 않는다(`scope="audit_opinion"` 전용). 그래서 예전에는
    # 재무제표 판정이 감사의견을 **한 번도 조회하지 않고** 「적정」이라 단정했다. 연도도 다르다 —
    # reference 는 FY(N-2)지만 이 안건이 승인하려는 건 **FY(N-1)** 이라 그쪽을 물어야 한다
    # (한 번 부르면 당기·전기·전전기 3년치가 함께 온다).
    audit_year = target_year - 1
    # 정기주총에서 FY(N-1) 사업보고서가 주총 전에 이미 나온 경우(시장 전수 기준 최소 81.7%),
    # 승인 대상 연도의 **확정치**를 함께 가져온다. 공고의 잠정치와 나란히 놓으면 감사 과정에서
    # 무엇이 바뀌었는지 보이고, 안 가져오면 「잠정만 있는 회사」와 구분이 안 된다.
    # 없으면 이 upstream 은 건너뛴다 — 없는 해를 물어 빈 응답을 받느니 부르지 않는다.
    async def _confirmed_metrics():
        if confirmed_year is None:
            return None
        return await _safe_throttled(
            build_financial_metrics_payload, company_query,
            timing_label="financial_metrics.confirmed", scope="summary", year=confirmed_year)

    (meeting_full, ownership, gov_report, fin_metrics, director_eval, audit_opinion,
     fin_confirmed) = await asyncio.gather(
        _safe_throttled(build_shareholder_meeting_payload, company_query, timing_label="shareholder_meeting.advise", scope="advise", year=target_year, meeting_type=meeting_type),
        _safe_throttled(build_ownership_structure_payload, company_query, timing_label="ownership_structure.control_map", scope="control_map"),
        _safe_throttled(build_corp_gov_report_payload, company_query, timing_label="corp_gov_report.summary", scope="summary"),
        _safe_throttled(build_financial_metrics_payload, company_query, timing_label="financial_metrics.summary", scope="summary", year=fin_year),
        _safe_throttled(build_director_evaluation_payload, company_query, timing_label="director_evaluation", year=target_year, meeting_type=meeting_type, check_audit_history=check_audit_history),
        _safe_throttled(build_financial_metrics_payload, company_query, timing_label="financial_metrics.audit_opinion", scope="audit_opinion", year=audit_year),
        _confirmed_metrics(),
    )
    # full payload가 summary/agenda/compensation/aoi_change 데이터를 모두 포함 — 다운스트림 4개 참조에 동일 객체 할당
    meeting_summary = meeting_agenda = meeting_comp = meeting_aoi = meeting_full

    # **회사의 현재 상태를 말하는 것은 전부 이 하나를 본다.** 자본잠식·적자·배당재원은 「승인
    # 대상 연도」의 사실이지 2년 전 사실이 아니다. 판정·위험신호·배당판단이 서로 다른 해를 보면
    # 한 문장 안에서 충돌한다 — 실측 지엔코: 「자본잠식 없음(2025사업연도) · 유의: 부분
    # 자본잠식」. 주총일 기준으로 FY(N-1)A 가 이미 나와 있으면 그것, 아니면 종전 FY(N-2)A.
    # **확정치를 실제로 받았을 때만** 그걸 쓴다. `_safe` 는 재시도 뒤에도 실패하면 `data` 없는
    # 에러 dict 를 돌려주는데, `confirmed_year` 는 공시목록 조회로 정해지므로 「연도는 있는데
    # 재무 조회는 실패」가 성립한다. 그대로 넘기면 확실한 AGAINST 가 「자본잠식 미확인」으로
    # 조용히 내려앉는다 — upstream 한 번 흔들린 것이 판정을 지우면 안 된다.
    _conf_ok = bool((((fin_confirmed or {}).get("data") or {}).get("summary")))
    state_metrics = fin_confirmed if (confirmed_year and _conf_ok) else fin_metrics
    state_year = confirmed_year if (confirmed_year and _conf_ok) else fin_year
    _mark("upstreams_total", stage_started_at)

    # 이 메모를 만들며 실제로 읽은 공시를 전부 모은다. upstream 마다 근거를 2건까지만 싣고 잘라내던
    # 탓에 지분·감사의견·배당처럼 판정에 쓰인 문서가 목록에서 빠져 있었다 — 무엇을 근거로 이 판정이
    # 나왔는지 사용자가 되짚을 수 없으면 판정도 못 쓴다.
    read_payloads: list[tuple[Any, str]] = [
        (meeting_summary, "주주총회 소집공고"),
        (director_eval, "이사·감사 후보 평가"),
        (fin_metrics, "재무지표"),
        (audit_opinion, "감사의견"),
        (gov_report, "기업지배구조보고서"),
        (ownership, "지분 구조"),
    ]

    # 1번 안건 (재무제표 승인) 잠정 FS 본문 raw — meeting_summary notice.rcept_no로 doc 가져와 파싱
    # 260505 ralph 17:50: 같은 doc에서 퇴직금 amendments도 파싱 (extra DART 호출 없이)
    # 260505 ralph 23:30: parse_fy_from_agm_doc (정규식 텍스트) → parse_provisional_financial_statement (BS4 표) 교체
    fy_raw_from_agenda: dict[str, Any] = {"extraction_status": "no_data"}
    retirement_payload: dict[str, Any] | None = None
    notice_full_text: str = ""  # 파싱 실패 안건 raw 폴백용 소집공고 원문 (아래 decision loop에서 사용)
    notice_html: str = ""       # 260724 provenance: L0-0-2-* 섹션 좌표 추출용 원문 XML
    notice_dict = ((meeting_summary.get("data") or {}).get("notice") or {})
    agm_rcept = notice_dict.get("rcept_no") if isinstance(notice_dict, dict) else None
    # 260723 리뷰: pre-resolution은 연도만 downstream에 넘기고 payload는 그 연도 창에서 재선택
    # — 두 선택이 다른 공고를 가리키면(예: 같은 해 임시주총 2회) basis가 근거를 오표기하므로 명시
    if (
        pre_resolved and agm_rcept
        and pre_resolved.get("notice_rcept_no")
        and agm_rcept != pre_resolved["notice_rcept_no"]
    ):
        year_resolution["notice_mismatch"] = True
        year_resolution["basis"] = (
            year_resolution.get("basis", "")
            + " ※ 실제 분석 공고는 회차 창 내에서 재선택됨(연도 결정 근거 공고와 다름 — evidence 참조)"
        )
    if agm_rcept:
        stage_started_at = time.perf_counter()
        try:
            doc = await asyncio.wait_for(client.get_document_cached(agm_rcept), timeout=30.0)
            _mark("notice_doc_reuse", stage_started_at)
            text = (doc or {}).get("text") or ""
            notice_full_text = text
            html = (doc or {}).get("html") or ""
            notice_html = html
            # 잠정 재무제표 표 파싱 (HTML 표 구조 그대로) + flat metrics 추출
            if html:
                pfs_parsed = parse_provisional_financial_statement(html)
                fy_raw_from_agenda = _extract_provisional_fs_metrics(pfs_parsed)
            # 퇴직금 amendments parse — 본문에 "퇴직금" 키워드 있을 때만
            if html and ("퇴직금" in text or "퇴직금" in html or "퇴임위로금" in text or "퇴임위로금" in html):
                from open_proxy_mcp.services.shareholder_meeting_parser import parse_retirement_pay_xml
                _ret = parse_retirement_pay_xml(html)
                if _ret and _ret.get("amendments"):
                    retirement_payload = {"data": _ret, "status": "ok", "source_rcept_no": agm_rcept}
        except Exception:
            _mark("notice_doc_reuse", stage_started_at)
            fy_raw_from_agenda = {"extraction_status": "error"}

    # FY(N-1)A 가 아직 없는 구간 — 사업보고서가 주총 뒤에 나오는 18% — 에서는 공고의 잠정치로
    # 판단한다. 2년 전 확정치보다 **승인 대상 연도의 잠정치**가 가깝다. 주주가 승인하려는 대상이
    # 바로 그 숫자이기도 하다. 검산을 통과할 때만 쓰므로 못 쓰면 종전대로 FY(N-2)A 로 남는다.
    # 확정치 조회가 실패한 경우도 잠정치가 구제한다 — 「연도는 있는데 값이 없다」가 그 자리다.
    if not _is_egm and not (confirmed_year and _conf_ok):
        _prov_state = _provisional_state_payload(fy_raw_from_agenda, fin_metrics)
        if _prov_state:
            state_metrics = _prov_state
            state_year = target_year - 1

    # 안건 리스트 추출 (success 매핑) — 260507: parent_title 함께 추출 (정관 sub-안건 분류용)
    agenda_data = (meeting_agenda.get("data") or {})
    agenda_summary = agenda_data.get("agenda_summary", {}) or {}
    agenda_tree = agenda_data.get("agendas") or []

    def _flatten_agenda_rows(items: list) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for it in items or []:
            title = (it.get("title") or "").strip() if isinstance(it, dict) else ""
            if title:
                rows.append({
                    "title": title,
                    "agenda_id": it.get("agenda_id"),
                    "agenda_relation_type": it.get("agenda_relation_type") or "normal",
                    "agenda_relation_reasons": it.get("agenda_relation_reasons") or [],
                    "proposer_type": it.get("proposer_type"),
                    "source": it.get("source"),
                    "conditional": it.get("conditional"),
                    # 재무제표 승인 안건에 병합된 배당 + 회사가 신고한 안건 구간 코드(진단용)
                    "dividend": it.get("dividend"),
                    "filed_code": it.get("filed_code"),
                    "filed_kind": it.get("filed_kind"),
                    # 구간 코드를 어떻게 이었는지(declared/candidate_name/heading/kind_match).
                    # declared 만 문서가 직접 밝힌 것이라 분류 정확도의 독립 근거로 쓸 수 있다.
                    "filed_link": it.get("filed_link"),
                    # 상법 §449조의2 — 재무제표가 보고사항으로 갈음됐는지(표결 유무)
                    "declared_role": it.get("declared_role"),
                    "resolution_status": it.get("resolution_status"),
                    "resolution_note": it.get("resolution_note"),
                })
            if isinstance(it, dict):
                rows.extend(_flatten_agenda_rows(it.get("children") or []))
        return rows

    agenda_rows = _flatten_agenda_rows(agenda_tree)
    if not agenda_rows:
        agenda_rows = [
            {"title": title, "agenda_relation_type": "normal", "agenda_relation_reasons": []}
            for title in (agenda_summary.get("titles", []) or [])
        ]
    # shareholder_meeting agenda 미검출 시 director_evaluation의 본문 agenda fallback
    if not agenda_rows:
        fallback_titles = (director_eval.get("data") or {}).get("agenda_titles_fallback", []) or []
        if fallback_titles:
            agenda_rows = [
                {"title": title, "agenda_relation_type": "normal", "agenda_relation_reasons": []}
                for title in fallback_titles
            ]

    # parent_title map: title → parent_title (agenda tree에서 추출)
    # title → children 수 map (D 패턴 식별용 — children 0 + 정관변경 top + amendments 있음)
    title_to_parent: dict[str, str] = {}
    title_to_children_count: dict[str, int] = {}
    def _walk_agenda_tree(items: list, parent: str = "") -> None:
        for it in items or []:
            t = (it.get("title") or "").strip()
            if t:
                title_to_parent[t] = parent
                title_to_children_count[t] = len(it.get("children") or [])
            _walk_agenda_tree(it.get("children", []), parent=t)
    _walk_agenda_tree(agenda_tree)

    # ── 근거 위치(provenance) 1단계 (260724) — 루트 안건 ↔ L0-0-2-* 섹션 순서 바인딩 ──
    # DART 편집기가 안건 유형별로 찍는 표준 섹션 코드(L0-0-2-N-0)를 "어느 공시의 어디를
    # 보라"는 좌표로 동봉한다. 루트 안건 수 == L-섹션 수일 때만 순서 바인딩(보수 원칙 —
    # 불일치·부재 시 미부착), 자식 안건은 부모 섹션을 상속. 코드↔카테고리 대조는 향후 확장.
    agenda_source_map: dict[str, dict[str, str]] = {}
    if notice_html and agm_rcept:
        _lsecs = [
            (m.group(1), re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m.group(2))).replace("□", "").strip())
            for m in re.finditer(
                r'<TITLE\b[^>]*AASSOCNOTE="(L0-0-2-\d+-0)"[^>]*>(.*?)</TITLE>', notice_html, re.S)
        ]
        _roots = [
            (it.get("title") or "").strip()
            for it in agenda_tree
            if isinstance(it, dict) and (it.get("title") or "").strip()
        ]
        # 섹션 원문 구간(다음 L/D 앵커 전까지) — 정합 실패 시 통 원문 발췌용 (260724)
        _lsec_pos = [m.start() for m in re.finditer(
            r'<TITLE\b[^>]*AASSOCNOTE="L0-0-2-\d+-0"[^>]*>', notice_html)]
        if _lsecs and _roots and len(_lsecs) == len(_roots):
            for _i, (_rt, (_code, _sec_title)) in enumerate(zip(_roots, _lsecs)):
                _s = _lsec_pos[_i] if _i < len(_lsec_pos) else 0
                _e = _lsec_pos[_i + 1] if _i + 1 < len(_lsec_pos) else min(len(notice_html), _s + 60000)
                agenda_source_map[_rt] = {
                    "rcept_no": agm_rcept, "section_code": _code, "section_title": _sec_title,
                    "_span": (_s, _e),
                }
    # 맵 단위 정합성 게이트 (260724 QA): 루트 하나라도 "코드 vs 제목 분류" 상충이면
    # zip-order 밀림 의심 → 이 공고의 'other' 승격 전체 억제 (행별 독립 대조의 맹점 보완)
    lcode_map_trusted = True
    for _rt, _src in agenda_source_map.items():
        _mapped = _LCODE_CATEGORY.get(_src.get("section_code") or "")
        if _mapped:
            _tc = _classify_agenda(_rt)
            if _tc != "other" and _tc != _mapped:
                lcode_map_trusted = False
                break

    # 후보 평가 dict — name → eval
    director_data = (director_eval.get("data") or {})
    # 이사회 성별 구성 — 자본시장법 §165조의20 판정 재료(등기이사만 집계됨)
    _board_gender = director_data.get("board_gender") or {}
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
    # 부문 매핑 실패/정형 저신뢰 시 회사 단위 참고 첨부 (아래 segment 블록에서 설정)
    segment_reference: dict[str, Any] | None = None
    if inside_renewed_candidates:
        # 회사 단위 한 번 fetch (모든 사내이사 동일 source 공유)
        # 추가 호출 ~3개 (dividend + treasury + financial yearly)
        stage_started_at = time.perf_counter()
        # treasury lookback 동적 — 소각 이력은 사내이사 재직기간만 CSR에 쓰이므로, 가장 오래
        # 재직한 사내이사 기준으로 lookback을 좁힌다(짧은 재직 회사는 빨라지고, 오너 장기재직은
        # 120 유지). earliest_start는 1차 gather(director_evaluation)에서 이미 결정됨. 단 한 명이라도
        # detect fail(None)이면 보수적으로 120 (그 이사는 5년 fallback이라 정확한 span 모름).
        _earliest = [(ev.get("appointment_type") or {}).get("earliest_start") for ev in inside_renewed_candidates]
        if _earliest and all(_earliest):
            _span_months = (target_year - min(_earliest) + 2) * 12  # 재직기간 + 1년 여유
            treasury_lookback = max(36, min(120, _span_months))
        else:
            treasury_lookback = 120
        perf_div, perf_treas, perf_fin, perf_order = await asyncio.gather(
            _safe_throttled(build_dividend_payload, company_query, timing_label="dividend.history", scope="history", years=5),
            _safe_throttled(build_treasury_share_payload, company_query, timing_label="treasury_share.summary", scope="summary", lookback_months=treasury_lookback),
            _safe_throttled(build_financial_metrics_payload, company_query, timing_label="financial_metrics.yearly", scope="yearly", year=fin_year),
            # 매트릭스 fact용(signal_summary 집계만 필요) — 문서 30→10 경량화. 수주 fact는
            # '최근 모멘텀'이 핵심이라 영향 작고, 대부분 회사는 24개월 내 수주 10건 미만.
            _safe_throttled(build_order_contracts_payload, company_query, timing_label="order_contracts", max_documents=10),
        )
        _mark("inside_director_performance_upstreams", stage_started_at)
        read_payloads.extend([
            (perf_div, "배당 이력"),
            (perf_treas, "자기주식"),
            (perf_fin, "재무지표 (연도별)"),
            (perf_order, "수주·계약"),
        ])
        # 수주 시그널 — 회사 단위 별도 fact. 성과 매트릭스(ROE/부채/CSR) 점수에는 반영하지 않는다
        # (적자 디폴트 코스닥 바이오 등에서 수주 부재를 성과 저조로 오판하지 않도록). 정보로만 노출.
        order_signal = (perf_order.get("data") or {}).get("signal_summary") if isinstance(perf_order, dict) else None
        # yearly 데이터 파싱
        roe_yearly: dict[int, float | None] = {}
        leverage_yearly: dict[int, float | None] = {}
        net_income_yearly: dict[int, int | None] = {}
        op_margin_yearly: dict[int, float | None] = {}  # 영업이익률 — 본업 수익성 fact (점수 미반영)
        capital_impairment_status = ((fin_metrics.get("data") or {}).get("summary") or {}).get("capital_impairment_status")
        for row in ((perf_fin.get("data") or {}).get("yearly") or []):
            y = row.get("year")
            if y is None:
                continue
            roe_yearly[y] = row.get("roe_pct")
            leverage_yearly[y] = row.get("debt_ratio_pct")
            net_income_yearly[y] = row.get("net_income_krw")
            op_margin_yearly[y] = row.get("operating_margin_pct")

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
            # 성과 귀속은 **등기이사였던 기간**에만 한다 — 비등기 집행임원 시절의 전사 지표를
            # 개인에게 물으면 안 된다(260729: 김동춘은 2018~2025 가 비등기 본부장이고 2026 에
            # CEO 로 등기됐는데 「재직 2018~2026(9년)」으로 9년치 ROE 를 귀속했다).
            board_start = apt.get("board_earliest_start")
            src = apt.get("board_tenure_source") or {}
            if board_start is None:
                # 등기 이력이 없다 — 왜 없는지에 따라 뜻이 전혀 다르므로 갈라서 말한다.
                # (260729: 하나로 뭉뚱그리면 「확정된 신임」과 「파싱 실패」가 구분되지 않는다.)
                if src.get("note", "").startswith("임원현황에 미등기"):
                    why = (f"{src.get('source')} 임원현황에 미등기임원으로만 올라 있어, "
                           "등기이사로 재직한 기간이 없습니다.")
                elif src.get("rejected_start"):
                    # 등기 여부는 **확정됐고** 시작 시점만 못 정한 경우다. 「등기인지 모른다」로
                    # 말하면 읽는 쪽이 잘못 읽는다(260731: 대웅제약 박은경 — 정형이 「사내이사」로
                    # 명시하고 임기 만료일까지 있는데 메시지는 등기 여부를 모르는 것처럼 말했다).
                    why = (f"{src.get('source')} 임원현황이 「{src.get('director_type')}」로 밝혀 "
                           "등기이사인 것은 확인되나, 재직기간 표기"
                           + (f"({src.get('tenure_raw')})" if src.get("tenure_raw") else "")
                           + "가 근속연수로 보여 등기 시작 시점을 확정하지 못했습니다.")
                elif src.get("director_type") and "미등기" not in (src.get("director_type") or ""):
                    why = (f"{src.get('source')} 임원현황이 「{src.get('director_type')}」로 밝혀 "
                           "등기이사인 것은 확인되나, 재직기간 표기가 없어 시작 시점을 "
                           "확정하지 못했습니다.")
                elif not (apt.get("matched_entries") or []):
                    why = ("소집공고 세부경력에서 이 회사 재직 이력을 찾지 못했고 직전 정기보고서 "
                           "임원현황에도 없습니다 — 신규 선임이거나 경력 표기가 달라 대조되지 않았습니다.")
                else:
                    why = ("이 회사 재직 이력은 있으나 등기이사 재직 시점을 확정할 근거가 없습니다 "
                           "— 소집공고 경력란은 등기 여부를 적을 의무가 없습니다.")
                ev["performance"] = {
                    "classification": "not_evaluated",
                    "rationale": why + " 재직 중 성과는 평가하지 않았습니다.",
                    "tenure_period": None,
                    # 「왜 확정 못 했나」를 문장으로만 내보내면 읽는 쪽이 검증할 수 없다 —
                    # 판단의 근거가 된 임원현황 행을 통째로 함께 싣는다(확정된 경우엔 안 붙는다).
                    "roster_row": src.get("roster_row"),
                }
                continue
            # 등기 첫 해는 취임 전 실적이라 본인 성과가 아니다 — 최소 2개 사업연도를 요구한다.
            # (260729 실측 25사: 이에 해당하는 후보는 김동춘 1건. 취임 연도 1년으로 「저조」가
            #  나왔는데, 그 해 실적은 전임 경영진의 것이다.)
            if target_year - board_start < 2:
                ev["performance"] = {
                    "classification": "not_evaluated",
                    "rationale": (f"등기이사 재직이 {board_start}년부터라 평가할 사업연도가 부족합니다"
                                  " — 취임 연도 실적은 본인 성과로 보기 어렵습니다."),
                    "tenure_period": None,
                    "board_start_year": board_start,
                }
                continue
            tenure_years = list(range(board_start, target_year + 1))
            ev["performance"] = compute_performance(
                tenure_years=tenure_years,
                roe_yearly=roe_yearly,
                leverage_yearly=leverage_yearly,
                net_income_yearly=net_income_yearly,
                dividend_yearly=dividend_yearly,
                cancelation_yearly=cancelation_yearly,
                capital_impairment_status=capital_impairment_status,
                operating_margin_yearly=op_margin_yearly,
            )
            # 등기 시작이 회사 근무 시작보다 늦으면 그 사실을 밝힌다(오해 방지)
            if apt.get("earliest_start") and apt["earliest_start"] < board_start:
                ev["performance"]["tenure_note"] = (
                    f"이 회사 근무는 {apt['earliest_start']}년부터이나 **등기이사 재직은 "
                    f"{board_start}년부터**입니다 — 성과는 등기 기간만 반영했습니다.")
            # 등기 기간의 출처를 밝힌다 — 정형 데이터인지 경력란 추정인지에 따라 신뢰도가 다르다.
            if src.get("rejected_start"):
                # 게이트가 정형 값을 버렸다 — 지금 쓰는 시작연도는 소집공고 추정이다.
                # 임원현황 재직기간을 그대로 찍으면 그걸 쓴 것처럼 읽힌다.
                ev["performance"]["tenure_source"] = (
                    f"소집공고 세부경력 추정 — {src.get('source')} 임원현황의 재직기간"
                    + (f"({src['tenure_raw']})" if src.get("tenure_raw") else "")
                    + "은 근속연수로 보여 쓰지 않았습니다.")
            elif src.get("director_type"):
                ev["performance"]["tenure_source"] = (
                    f"{src.get('source')} 임원현황 기준 「{src['director_type']}」"
                    + (f" · 재직기간 {src['tenure_raw']}" if src.get("tenure_raw") else ""))
            else:
                ev["performance"]["tenure_source"] = (
                    "소집공고 세부경력 추정 — 임원현황과 대조되지 않았습니다.")
            # 수주 시그널 — 회사 공통 별도 fact (점수 미반영). 적자기업 미래 매출 가시성 참고용.
            # 체결 0·해지만 있는 회사(종근당홀딩스 등)도 해지가 부정 시그널이므로 포함.
            if order_signal and (order_signal.get("order_count") or order_signal.get("terminated_count")):
                ev["performance"]["order_signal"] = order_signal

        # ── 담당부문 성과 참고 fact (260723 Phase 1 — 점수 미반영) ──
        # 부문장 출신 사내이사(예: LG화학 김동춘 — 첨단소재 라인)가 전사 지표로만 평가되는
        # 한계 보완: 커리어→부문 보수적 매핑(정확히 1개 매칭만) 후 해당 부문 매출·영업이익
        # 최근 3개 사업연도 추이를 참고로 첨부. decision 로직 개입 없음 (order_signal 패턴).
        # 콜 게이트: ① 부문장류 커리어 키워드 없으면 fetch 0 ② 최신 1개년 먼저 → 정형
        # 고신뢰(OK) + 매핑 성공일 때만 과거 2개년 추가 fetch (+2 payload).
        from open_proxy_mcp.services.business_details import build_business_details_payload
        from open_proxy_mcp.services.director_segment_signal import (
            build_segment_series,
            extract_segment_items,
            has_division_career,
            map_candidate_to_segment,
        )

        if not has_division_career(inside_renewed_candidates):
            # 게이트 skip도 상태를 기록 — QA에서 '미기록 None'과 '의도된 skip' 구분 가능하게
            for ev in inside_renewed_candidates:
                ev["performance"]["segment_signal_status"] = "no_division_career"
        else:
            stage_started_at = time.perf_counter()
            _seg_y1 = target_year - 1  # 최신 확정 사업연도 (2026 주총 → FY2025 사업보고서)
            seg_latest_payload = await _safe_throttled(
                build_business_details_payload, company_query,
                timing_label="business_details.segments.latest",
                fields=["segments"], bsns_year=str(_seg_y1), reprt_code="11011",
            )
            seg_latest = extract_segment_items(seg_latest_payload if isinstance(seg_latest_payload, dict) else {})
            # FY-2 fallback은 '아직 미공시' 케이스에만 (260723 리뷰 P1-3): 단일부문사
            # (NOT_APPLICABLE)·금융/REIT(UNSUPPORTED_FORM)·upstream 에러는 과거 연도에서도
            # 같은 결과가 확정적이라 추가 fetch가 순낭비 + '저신뢰' 오라벨을 낳는다.
            _latest_seg_status = (
                (((seg_latest_payload or {}).get("data") or {}).get("segments") or {}).get("status")
                if isinstance(seg_latest_payload, dict) else None
            )
            _latest_is_error = (
                isinstance(seg_latest_payload, dict) and seg_latest_payload.get("status") == "error"
            )
            _structural_absence = _latest_seg_status in ("NOT_APPLICABLE", "UNSUPPORTED_FORM")
            if not seg_latest and not _structural_absence and not _latest_is_error:
                # 주총 시즌 사전 호출이면 FY(회차-1) 사업보고서가 아직 미공시일 수 있음 → FY(회차-2) 1회 fallback
                _fb_year = target_year - 2
                _fb_payload = await _safe_throttled(
                    build_business_details_payload, company_query,
                    timing_label="business_details.segments.latest_fb",
                    fields=["segments"], bsns_year=str(_fb_year), reprt_code="11011",
                )
                _fb_seg = extract_segment_items(_fb_payload if isinstance(_fb_payload, dict) else {})
                if _fb_seg:
                    # 정형 성공 시에만 교체 — 실패면 FY(t-1)의 마크다운(더 신선)을 보존
                    _seg_y1, seg_latest_payload, seg_latest = _fb_year, _fb_payload, _fb_seg
            _company_name_for_seg = selected.get("corp_name") or company_query
            seg_mappings: dict[int, dict[str, Any]] = {}
            _seg_statuses: list[str] = []
            if seg_latest:
                seg_names = [s.get("name", "") for s in seg_latest["items"]]
                for idx, ev in enumerate(inside_renewed_candidates):
                    m = map_candidate_to_segment(ev, seg_names, _company_name_for_seg)
                    ev["performance"]["segment_signal_status"] = m["status"]
                    _seg_statuses.append(m["status"])
                    if m["status"] == "mapped":
                        seg_mappings[idx] = m
                # (A) 정형 OK인데 매핑 실패한 부문장류 후보 존재 → 부문표 전체를 구조화 참고로
                # 첨부 (260723 사용자 결정: 매칭 단정 불가 시 통으로 넘겨 호출측 AI가 직접 대조).
                # no_division_career는 대조 수요 없음 — no_match/ambiguous만 트리거. 추가 콜 0.
                if any(s in ("no_match", "ambiguous") for s in _seg_statuses):
                    segment_reference = {
                        "kind": "structured_table",
                        "fiscal_year": _seg_y1,
                        "unit": seg_latest.get("unit"),
                        "revenue_metric": seg_latest.get("revenue_metric"),
                        "profit_metric": seg_latest.get("profit_metric"),
                        "items": seg_latest["items"],
                        "note": (
                            "후보 경력과 사업부문을 자동으로 연결하지 못해 부문표 전체를 참고로 첨부합니다. "
                            "후보 경력과 직접 대조해 판단 — 점수 미반영."
                        ),
                    }
            else:
                for ev in inside_renewed_candidates:
                    # 상태 세분화(260723 리뷰 P1-2/P1-3): 구조적 부재·조회 실패를
                    # '저신뢰'로 뭉뚱그리면 사용자에게 잘못된 서사가 나간다.
                    ev["performance"]["segment_signal_status"] = (
                        "segments_not_applicable" if _latest_seg_status == "NOT_APPLICABLE"
                        else "segments_unsupported_form" if _latest_seg_status == "UNSUPPORTED_FORM"
                        else "segments_fetch_error" if _latest_is_error
                        else "segments_low_confidence"
                    )
                # (B) 정형 저신뢰 → segments 추출기가 돌려준 영업부문 주석 마크다운을 회사 단위
                # 1회 첨부 (260718 결정 '통으로 마크다운' 재사용 — 호출측 AI가 직접 읽음). 추가 콜 0.
                _seg_data = ((seg_latest_payload or {}).get("data") or {}) if isinstance(seg_latest_payload, dict) else {}
                _seg_raw = _seg_data.get("segments") or {}
                _md = (_seg_raw.get("segment_note_md") or "").strip()
                # 호출측이 지정하는 발췌 길이 — 잘리면 AI가 파라미터를 늘려 재호출하거나
                # business_details(fields='segments')로 전체 조회 (260723 사용자 결정). clamp 안전.
                try:
                    _cap = max(1000, min(_SEGMENT_CONTEXT_MAX_CHARS, int(segment_context_chars)))
                except (TypeError, ValueError):
                    _cap = _SEGMENT_CONTEXT_DEFAULT_CHARS
                if not _md:
                    # raw_candidates 변형 (주석 앵커 실패 — CJ제일제당류): 상위 후보 표
                    # 파이프격자 텍스트를 이어붙여 동일하게 노출 (호출측 AI가 진짜 부문표 선별)
                    _cand_tables = _seg_raw.get("candidates") or []
                    _md = "\n\n".join(
                        (c.get("rendered") or "").strip() for c in _cand_tables[:3] if c.get("rendered")
                    ).strip()
                if _md:
                    segment_reference = {
                        "kind": "note_markdown",
                        "fiscal_year": _seg_y1,
                        "source": _seg_raw.get("source"),
                        "markdown": _md[:_cap],
                        "truncated": len(_md) > _cap,
                        "context_chars": _cap,
                        "full_length": len(_md),
                        "note": (
                            "부문표 정형 추출 저신뢰 → 영업부문 주석/후보 표 원문 첨부 (LLM 직접 검토). "
                            "여기서 후보 담당부문의 매출·영업이익을 직접 읽되, 합계/조정/부문간/미배분 "
                            "열·행은 제외 — 점수 미반영."
                        ),
                    }

            if seg_mappings:
                # 매핑 성공 → 과거 2개년 추가 fetch (docs는 get_document_cached 공유)
                past_payloads = await asyncio.gather(*[
                    _safe_throttled(
                        build_business_details_payload, company_query,
                        timing_label=f"business_details.segments.y{off}",
                        fields=["segments"], bsns_year=str(_seg_y1 - off), reprt_code="11011",
                    )
                    for off in (1, 2)
                ])
                yearly = {_seg_y1: seg_latest_payload}
                for off, p in zip((1, 2), past_payloads):
                    yearly[_seg_y1 - off] = p if isinstance(p, dict) else None
                for idx, m in seg_mappings.items():
                    ev = inside_renewed_candidates[idx]
                    series = build_segment_series(yearly, m["segment"])
                    if series:
                        # 요청 연도 중 시계열에서 빠진 연도 — 사유를 구분해 기록(260723 리뷰 P1-2):
                        # fetch 에러를 '회사 공시가 저신뢰'라고 표기하면 거짓 서사가 된다.
                        _series_fys = {r["fy"] for r in series}
                        excluded = sorted(fy for fy in yearly if fy not in _series_fys)
                        excluded_reasons: dict[str, str] = {}
                        for _fy in excluded:
                            _p = yearly.get(_fy)
                            if not isinstance(_p, dict) or _p.get("status") == "error":
                                excluded_reasons[str(_fy)] = "fetch_error"
                                continue
                            _st = ((_p.get("data") or {}).get("segments") or {}).get("status")
                            excluded_reasons[str(_fy)] = (
                                "not_applicable" if _st in ("NOT_APPLICABLE", "UNSUPPORTED_FORM")
                                else "segment_absent_or_renamed" if _st == "OK"
                                else "low_confidence"
                            )
                        ev["performance"]["segment_signal"] = {
                            "segment": m["segment"],
                            "matched_from": m["matched_from"],
                            "revenue_metric": seg_latest.get("revenue_metric"),
                            "profit_metric": seg_latest.get("profit_metric"),
                            "series": series,
                            "excluded_years": excluded,
                            "excluded_reasons": excluded_reasons,
                            # 연도 간 공시 단위 변경(백만원↔억원) 시 추이가 100배 착시를 만든다 —
                            # 불일치하면 렌더가 연도별 단위를 병기하도록 플래그 (260723 리뷰 P1-6)
                            "unit_consistent": len({(r.get("unit") or "") for r in series}) <= 1,
                            "note": (
                                "참고 — 점수 미반영. 담당부문 추정은 후보 경력 텍스트 기반 보수적 매핑. "
                                "부문 구성·지표 정의(K-IFRS 1108)는 회사 공시 기준이라 연도 간 재편 시 불연속 가능."
                            ),
                        }
                    else:
                        ev["performance"]["segment_signal_status"] = "mapped_but_no_series"
            _mark("inside_director_segment_signal", stage_started_at)

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
    # 법 적용 판단 기준일 = 오늘이 아니라 '이 주총'. 강행규정은 주총일이 시행일 이후여야 적용된다
    # (260709 확장: 스튜어드십 검수 — today 게이트는 시행 전 주총을 놓치거나 시행 후 과거 주총을 오판).
    # 소집공고 주총일 미파싱 시 today로 폴백(기존 동작 보존). applies_after의 layer 의미(A1=공포일 조기
    # 보상 / A2=시행일)는 그대로 두고 비교 대상만 today→주총일로 바꾼다.
    from open_proxy_mcp.services.shareholder_meeting import _parse_notice_meeting_date
    _today_iso = date.today().isoformat()
    _meeting_dt_text = notice_dict.get("datetime") if isinstance(notice_dict, dict) else None
    _meeting_date = _parse_notice_meeting_date(_meeting_dt_text or "")
    law_gate_iso = _meeting_date.isoformat() if _meeting_date else _today_iso

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
    # 카카오게임즈 패턴 fallback의 cross-match 회피용 — 매핑된 amendment idx track
    _subagenda_used_amendments: set[int] = set()
    # 1.6 raw 첨부 logic (Ralph 7 → fix 260510 hh:mm)
    # - 회사 단위 첨부 flag (중복 회피): 첫 미catch 정관변경 안건에 모든 amendments 첨부
    # - sub→amendment 매핑 결과 활용: 매핑 성공 sub는 자기 amendment 1개만 첨부
    _amendments_attached_for_company: bool = False
    _subagenda_attempted_mappings: dict[str, int] = {}  # sub title → mapped amendment idx (룰 매치 여부 무관)
    agenda_decisions: list[dict[str, Any]] = []
    stage_started_at = time.perf_counter()
    for agenda_row in agenda_rows:
        title = agenda_row.get("title") or ""
        agenda_relation_type = agenda_row.get("agenda_relation_type") or "normal"
        agenda_relation_reasons = agenda_row.get("agenda_relation_reasons") or []
        proposer_type = agenda_row.get("proposer_type")
        parent_for_title = title_to_parent.get(title, "")
        category = _classify_agenda(title, parent_title=parent_for_title)
        # 안건 유형 코드 이중 대조 (260724) — 루트 안건만 직접 바인딩됨(자식은 상속이라
        # 유형이 다를 수 있어 대조 제외: 예 이사선임 묶음 아래 개별 감사위원 sub)
        classification_note: str | None = None
        source_excerpt: str | None = None
        if not parent_for_title:
            _src = agenda_source_map.get(title)
            category, classification_note = _reconcile_category_with_lcode(
                category, (_src or {}).get("section_code"),
                section_title=(_src or {}).get("section_title") or "",
                map_trusted=lcode_map_trusted)
            # 정합 실패·불일치·미등록이면 메모로 끝내지 않고 해당 절 원문을 통으로 동봉
            # (260724 사용자 결정 — segments 사다리와 동일 철학). 표는 그리드 변환기
            # (_render_html_region_md → _table_to_markdown)가 마크다운 표로 변환.
            if classification_note and "분류 근거" not in classification_note and _src and _src.get("_span"):
                from open_proxy_mcp.services.segment_candidates import _render_html_region_md
                _s, _e = _src["_span"]
                _md = _render_html_region_md(notice_html, _s, _e) or ""
                if _md:
                    source_excerpt = _md[:3500] + (" …(발췌)" if len(_md) > 3500 else "")
        decision = "NO_DATA"
        reason = "category 미분류 — 본문 검토 필요"
        matched_eval: dict[str, Any] | None = None
        law_layer_hit: tuple[str, str, str, str] | None = None

        # 0. 법령 layer 우선 적용 (1·2·3차 상법 개정 + 정관 우회 시나리오)
        # hit 시 운용사 정책 / hardcoded _decide_* 모두 skip → 법 강행규정 일관 적용.
        law_layer_hit = _law_layer(
            title, parent_title=parent_for_title,
            corp_total_asset_won=corp_total_asset_won, today_iso=law_gate_iso,
        )

        # 0-b. D 패턴 한정 amendments body fallback (260510 ralph 7)
        # title 미매치 + top 정관변경 + children 0 + amendments 있음 → amendment 단위 검사.
        # children > 0 (LG화학 sub 명확 회사) 자동 제외 — Ralph 6 회귀 회피 핵심.
        if (
            law_layer_hit is None
            and parent_for_title == ""
            and _is_charter_top(title)
            and title_to_children_count.get(title, 0) == 0
            and aoi_amendments
        ):
            law_layer_hit = _law_layer_body(
                aoi_amendments,
                parent_title=title,
                corp_total_asset_won=corp_total_asset_won,
                today_iso=law_gate_iso,
            )

        # 0-c. 카카오게임즈 패턴 fallback — sub→amendment 1:1 매핑 (260510 ralph 8)
        # 진입: title 미매치 + parent에 정관변경 + sub children 0 + sub title generic 아님 + amendments 있음
        # generic sub (도메인 키워드 없음)는 skip — cross-match 회피 (옵션 B 정책).
        # 매핑된 amendment의 body로만 룰 매칭 (Ralph 7 통합 검사와 다름 — 1:1 매핑).
        if (
            law_layer_hit is None
            and parent_for_title
            and _is_charter_top(parent_for_title)
            and title_to_children_count.get(title, 0) == 0
            and aoi_amendments
            and not _is_generic_sub(title)
        ):
            mapped_idx = _map_subagenda_to_amendment(
                title, aoi_amendments, _subagenda_used_amendments,
            )
            if mapped_idx is not None:
                # 매핑 시도 결과 track (룰 매치 여부 무관 — 1.6 raw 첨부에서 활용)
                _subagenda_attempted_mappings[title] = mapped_idx
                law_layer_hit = _law_layer_subagenda_mapped(
                    title, aoi_amendments[mapped_idx],
                    parent_title=parent_for_title,
                    corp_total_asset_won=corp_total_asset_won,
                    today_iso=law_gate_iso,
                )
                if law_layer_hit:
                    _subagenda_used_amendments.add(mapped_idx)

        # 1. OPM 기본 logic으로 fallback decision 산출
        if category == "director_election" or category == "audit_committee_election":
            for nm, ev in name_to_eval.items():
                if not nm:
                    continue
                # nm in title (기존) + core-name(영문병기 제거) 매칭 (260710 도진명 사고)
                if nm in title or _core_person_name(nm) in title:
                    matched_eval = ev
                    break
            statutory_auditor_agenda = (
                category == "audit_committee_election"
                and _is_statutory_auditor_agenda(title)
            )
            if matched_eval is None and statutory_auditor_agenda:
                audit_evals = [
                    ev for ev in name_to_eval.values()
                    if "감사" in (ev.get("role_type") or "")
                ]
                if len(audit_evals) == 1:
                    matched_eval = audit_evals[0]
            # ralph iter4+7 logic 강화: 매칭 안 됨 + 후보 평가 데이터 존재 →
            # 모든 후보 평가 종합 (묶음 안건 패턴 — "이사 선임의 건" 같은 형식).
            # iter7: 사내이사 (executive) vs 사외이사 (independent) 구분.
            # - 사내이사: 회사 결정 영역 (오너 일가 등). 결격사유만 판단. mainstream FOR.
            # - 사외이사: 독립성 핵심. concerns 있으면 REVIEW.
            if matched_eval is None and statutory_auditor_agenda:
                decision = "NO_DATA"
                reason = "감사 후보 평가 데이터 없음 — 본문 검토 필요"
            elif matched_eval is None and name_to_eval:
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
                    decision, reason = "REVIEW", f"묶음 안건 — 사내이사 재직 성과 저조 ({', '.join(bad_names[:3])}) — 사용자 검토"
                elif audit_history_red:
                    decision, reason = "REVIEW", f"묶음 안건 — 이사 회계 위험 이력에 결격 신호 (원문 메모 검토)"
                elif inside_perf_weak:
                    weak_names = [ev.get("name", "?") for ev in inside_evals if (ev.get("performance") or {}).get("classification") == "weak"]
                    decision, reason = "REVIEW", f"묶음 안건 — 사내이사 재직 성과 부진 ({', '.join(weak_names[:3])}) — 사용자 검토"
                else:
                    note = f" (사외 {len(outside_evals)}명 중 일부 indep concerns — 개별 사외이사 안건에서 검토)" if indep_concerns_outside else ""
                    decision, reason = "FOR", f"묶음 안건 — 결격사유 없음, 후보 {len(relevant_evals)}명{note}"
            else:
                # 사용자 요구 (2026-05): 데이터/근거 없으면 NO_DATA 반환 (자동 FOR 금지).
                # 이전 mainstream default FOR 로직 폐기 — 정직 fallback 우선.
                if matched_eval is None and not name_to_eval:
                    decision = "NO_DATA"
                    reason = "후보 평가 데이터 없음 (본문을 읽지 못했습니다) — 본문 검토 필요"
                else:
                    # iter21: audit_committee_election은 role_type 무관 strict 검증.
                    # 상근감사 같은 case에서 role_type 빈 string → 사내이사 fallback (자동 FOR) 위험.
                    if category == "audit_committee_election" and matched_eval is not None:
                        rt = matched_eval.get("role_type") or ""
                        if not any(k in rt for k in ("사외", "독립", "감사")):
                            # role_type 빈 또는 사내이사 표기여도 audit는 strict
                            matched_eval = {**matched_eval, "role_type": (rt or "") + " (audit-strict)"}
                            # 강제 outside 처리 — _decide_director_election 안에 분기
                            matched_eval["_audit_force_strict"] = True
                    decision, reason = _decide_director_election(matched_eval)
                # 공고는 사외이사라고 밝혔는데 사외이사 경로를 타지 않은 경우 — 독립성 검증이
                # 통째로 건너뛰어진 채 조용히 FOR 가 나간다(실측 667건 중 20건, 3.0%).
                # 후보자 표에 「직위」 칸이 없으면 roleType 이 구간 전체 제목에서 추정되는데,
                # 하위안건이 한 표에 묶이면 첫 하위안건의 직위를 전원이 상속하기 때문이다.
                # 제목으로 덮어쓰지는 않는다 — 반대 방향(사내→사외 11건)과 세분도 차이
                # (사내이사 vs 이사 106건)까지 함께 깨진다. 대신 판단을 원문으로 넘긴다.
                # 감사위원 경로는 위에서 _audit_force_strict 로 이미 강제 엄격 검증(독립성 포함)을
                # 건다 — 거기에 REVIEW 를 덧씌우면 오탐이다(실측: 삼진식품 「감사위원이 되는
                # 사외이사 …」 2건). 그래서 순수 이사선임에서만 본다.
                _declared = (agenda_row.get("declared_role") or "") if isinstance(agenda_row, dict) else ""
                if (_declared == "사외이사" and decision == "FOR"
                        and category == "director_election"
                        and not (matched_eval or {}).get("_audit_force_strict")):
                    _rt = (matched_eval or {}).get("role_type") or ""
                    if not any(k in _rt for k in ("사외", "독립", "감사")):
                        decision = "REVIEW"
                        reason = (f"공고는 사외이사 선임으로 밝혔는데 후보자 표 파싱은 "
                                  f"'{_rt or '미상'}' — 사외이사 독립성 검증(최대주주 관계·거래·"
                                  f"임직원 이력·5년 임기)이 적용되지 않았다. "
                                  f"「□ 이사의 선임」 구간 원문으로 직접 확인 필요. "
                                  f"(원래 판정: {reason})")
        elif category == "director_compensation":
            decision, reason = _decide_director_compensation(meeting_comp, state_metrics)
        elif category == "audit_compensation":
            # ralph 260505 17:50: 감사 보수한도 별도 분기 (N연기금 IV-34)
            decision, reason = _decide_audit_compensation(meeting_comp, state_metrics)
        elif category == "retirement_pay":
            # ralph 260505 17:50: 퇴직금 별도 분기 (N연기금 IV-35 + OPM #6/#7)
            decision, reason = _decide_retirement_pay(retirement_payload, state_metrics)
        elif category == "financial_statements":
            # **상태 판정은 승인 대상 연도로 한다.** 비교용 FY(N-2)A 로 자본잠식을 판정하면,
            # 그 사이 감자·증자로 잠식이 해소된 회사에 **이미 없어진 사유로 반대표를 권하게**
            # 된다. 실측 엔케이젠바이오텍: FY2024A 자본총계 -250억(완전잠식 158.48%) vs
            # FY2025A +71억(부분 1.39%) — 무상감자 427억→72억 + 증자로 해소됐는데 메모는
            # 「자기자본 0 이하」로 AGAINST 를 냈고, 같은 메모 안에 FY2025A +71억이 실려 있었다.
            # 기준일이 **주총일**이라 look-ahead 도 아니다 — 그 회사는 공고 3/16, FY2025A 제출
            # 3/24, 주총 3/31로, 표를 던지는 시점에는 확정치를 실제로 볼 수 있었다.
            decision, reason = _decide_financial_statements(
                state_metrics, audit_opinion, law_gate_iso, audit_year,
                fin_reference_year=state_year,
            )
            # 재무제표 승인 안건에 배당이 함께 실린 경우(실측 68건 중 재무제표 안건의 71.7%)
            # 배당 적정성 판단도 함께 돌린다 — 한 안건에 두 판단이 묶여 있어 종전엔
            # 배당 로직이 아예 호출되지 않았다. 두 판단 중 **보수적인 쪽을 채택**한다.
            _div = agenda_row.get("dividend") if isinstance(agenda_row, dict) else None
            if _div and _div.get("mentioned") and not _div.get("none_declared"):
                # 배당 재원도 승인 대상 연도다 — 2년 전 잉여금으로 이번 배당을 판단하지 않는다.
                d_dec, d_reason = _decide_dividend(title, state_metrics,
                                                  selected.get("corp_name") or "")
                _amt = _div.get("per_share_krw")
                _detail = (f"주당 {_amt:,}원" if _amt is not None else "금액은 본문 확인")
                if _div.get("yield_pct") is not None:
                    _detail += f" · 시가배당률 {_div['yield_pct']}%"
                if _div.get("by_class"):
                    _detail += " · " + ", ".join(f"{k} {v:,}원" for k, v in _div["by_class"].items())
                _rank = {"AGAINST": 3, "REVIEW": 2, "NO_DATA": 2, "FOR": 1}
                if _rank.get(d_dec, 0) > _rank.get(decision, 0):
                    decision = d_dec
                reason = f"{reason} / 배당 병합({_detail}) — {d_reason}"
        elif category == "cash_dividend":
            decision, reason = _decide_dividend(title, state_metrics, selected.get("corp_name") or "")
        elif category == "articles_amendment":
            decision, reason = _decide_articles_amendment(
                title,
                retirement_payload=retirement_payload,
                comp_payload=meeting_comp,
                fin_metrics_payload=state_metrics,
                amendment=_find_amendment_for_title(title),
            )
        elif category == "treasury_share":
            decision, reason = _decide_treasury_share(title)
        elif category == "capital_reduction":
            # 260724: 자동 FOR 금지 — 무상감자(결손보전)와 유상감자(지분 환급)는 주주 영향이
            # 정반대일 수 있어 원문 판단 위임. 종전엔 'other'로 새어 mainstream FOR 위험.
            # 체크리스트는 스튜어드십 리뷰(260724) 실무 기준 — 합의 매트릭스 '원칙반대·예외찬성' 정합.
            decision = "REVIEW"
            # 주식(액면)병합은 발행주식수만 줄고 자본금은 그대로라 감자가 아니다. 판단 경로는
            # 단주 처리 리스크 때문에 공유하되(자동 찬성 유출 방어), 문면을 감자라고 하지 않는다 —
            # 실측 10건 중 4건은 공고문에서 명시적으로 감자가 아니라고 밝히고 있었다.
            if "병합" in (title or "") and ("주식" in (title or "") or "액면" in (title or "")):
                reason = ("주식(액면)병합 — 자본금 감소가 아니라 발행주식수 감소(액면가 상향). "
                          "확인사항: ① 병합비율 ② 단주 처리 방식·보상단가(소수주주 축출 우려) "
                          "③ 목적(유통주식수 조정·관리종목 회피 등) ④ 정관 액면가 변경 동반 여부")
            else:
                reason = ("자본 감소(특별결의) — 확인사항: ① 무상/유상 구분 ② 목적(결손보전·회생·"
                          "구조조정 불가피성 — 해당 시 일반적으로 찬성) ③ 감자비율·주주평등"
                          "(주식병합 시 단주 처리) ④ 유상감자 시 환급가액 적정성")
        elif category == "merger_or_restructuring":
            # 260727: 분류 카테고리는 있는데 판정 분기가 없어 'other'→자동 FOR 로 새고 있었다
            # (라이브 실측: 에이치디현대미포 「합병계약 체결 승인의 건」, 롯데케미칼
            # 「분할계획서 승인의 건」 둘 다 ✅ FOR). 합병비율·주식매수청구권은 의결권 판단에서
            # 가장 무거운 항목이라 자동 찬성이 나가면 안 된다. stock_option_grant·
            # capital_reduction 과 같은 구멍이고 이번이 세 번째다.
            decision = "REVIEW"
            reason = ("합병·분할·영업양수도(특별결의) — 확인사항: ① 합병·분할 비율의 산정근거와 "
                      "외부평가기관 의견 ② 주식매수청구권 행사가액·행사기간 ③ 지배주주 지분 변동과 "
                      "일반주주 희석 ④ 사업적 정당성(시너지·구조조정 필요성) ⑤ 계열사 간 거래면 "
                      "이해상충 검토. 「목적사항별 기재사항」 구간 원문 확인 필요")
        elif category == "shareholder_proposal":
            # 주주제안은 경영진 안건과 이해가 정면으로 충돌하는 자리 — 자동 찬성/반대 모두 부적절.
            decision = "REVIEW"
            reason = ("주주제안 안건 — 확인사항: ① 제안 주체와 지분율·보유기간(상법 §363-2 요건) "
                      "② 제안 내용이 회사·전체주주 이익에 부합하는지 ③ 이사회 반대의견의 근거 "
                      "④ 경영권 분쟁 국면이면 양측 주장 대조. 제안자 측 자료와 회사 측 자료를 "
                      "모두 읽고 판단 — 구간 원문 참조")
        elif category == "stock_option_grant":
            # 260724 스튜어드십 리뷰: 'other'→자동FOR로 새던 동종 구멍 봉쇄
            decision = "REVIEW"
            reason = ("주식매수선택권 부여 — 확인사항: ① 희석률(발행주식총수 대비, 통상 1~3% 한도) "
                      "② 행사가격 적정성 ③ 부여 대상·수량 ④ 기존 부여분 누적 희석")
        else:
            # ralph iter6/12: other 카테고리 default FOR (위험 키워드 없으면).
            # 운용사 mainstream 표본 100% FOR (한화 2/2, 카카오뱅크 7/7 등).
            # iter12 정밀화: "자본준비금 감액"(회계 평탄화) ≠ "자본금 감액/감자"(주주가치 영향)
            t = (title or "")
            # 260724 스튜어드십 리뷰: '해임'은 분쟁·부정행위 국면 안건 — 자동 FOR 방어 불가
            risk_keywords = ["적대적", "방어", "포이즌", "전환사채발행", "해임"]
            # "감자" 또는 "자본금 감액" (자본준비금 감액 제외 — mainstream FOR)
            if "감자" in t or ("자본금" in t and "감액" in t):
                decision = "REVIEW"
                reason = "자본금 감액·감자 관련 — 본문 확인이 필요합니다"
            elif any(kw in t for kw in risk_keywords):
                decision = "REVIEW"
                reason = "분류되지 않은 안건 — 위험 요소가 보입니다. 본문 확인이 필요합니다"
            else:
                decision = "FOR"
                reason = "분류되지 않은 안건 — 알려진 위험 요소가 발견되지 않았습니다(본문 확인 권장)"

        # 1.5. 법령 layer hit 시 우선 적용 — vote_style/hardcoded 위에 (260508)
        law_detail: dict[str, Any] | None = None
        law_layer_id: str | None = None
        if law_layer_hit is not None:
            ll_decision, ll_reason, ll_id, ll_law_ref = law_layer_hit
            law_layer_id = ll_id
            decision = ll_decision
            # 조항 대장(SSOT) 상세 — 유예도래일·적용 티어·시행령 임계를 근거에 심화 (260709)
            law_detail = _law_provision_detail(ll_id)
            detail_line = f"\n📋 {law_detail['summary']}" if (law_detail and law_detail.get("summary")) else ""
            # A1/A2 (강행규정) — LLM이 안건명만 보고 결정 뒤집는 케이스 빈번
            # → catalog (wiki/rules/laws/llm_misread_patterns.json)에서 dynamic guard 매칭
            # 사유는 사람이 읽는 문장이다 — 내부 규칙 ID(`A1-1`) 대신 조문을 앞세운다.
            # ID 는 law_layer_id 필드로 따로 나가므로 기계 소비자·회귀 테스트는 그대로 쓴다.
            # LLM 이 안건명만 보고 결정을 뒤집는 것은 tool docstring 과 행 단위 🛡️ 마커가 막는다.
            reason = f"{ll_reason} (근거: {ll_law_ref}){detail_line}"
            # B1/B2 (REVIEW) — case-by-case 영역. 정관변경 본문 raw 첨부 (LLM 직접 검토 — 260510)
            # A1/A2 (FOR/AGAINST 강행규정)는 결정 명확 — raw 추가 X (토큰 절약)
            if ll_id.startswith("B1-") or ll_id.startswith("B2-"):
                am = _find_amendment_for_title(title)
                if am:
                    before_raw = (am.get("before") or "").strip()
                    after_raw = (am.get("after") or "").strip()
                    if before_raw or after_raw:
                        # raw 첨부 (LLM 본문 직접 검토용 — 결정은 REVIEW 유지). length 300자 통일.
                        clause = am.get("clause") or "?"
                        raw_excerpt = []
                        if before_raw:
                            raw_excerpt.append(f"[{clause} 변경 전] {before_raw[:300]}")
                        if after_raw:
                            raw_excerpt.append(f"[{clause} 변경 후] {after_raw[:300]}")
                        if raw_excerpt:
                            reason += "\n\n📄 해당 정관 조문 원문:\n" + "\n".join(raw_excerpt)

        # 1.55. agenda relation metadata — 절차/대안형 안건은 후보평가 자동 FOR 금지.
        # 법령 layer hit은 더 강한 근거이므로 우선한다.
        # 회사가 이미 내려놓은 안건 — 표결 대상이 아니다. 찬반을 내면 실행 불가능한 지시가 되고,
        # 목록에서 지우면 소집공고와 대조가 안 된다. 남기되 표결 대상이 아니라고 말한다.
        if agenda_relation_type == "withdrawn":
            decision = "NO_VOTE"
            reason = "폐기·철회된 안건 — 표결 대상이 아닙니다(공고 원문에 폐기/사퇴 사실이 기재됨)"
        elif law_layer_hit is None and agenda_relation_type in {"procedural", "alternative", "conditional"}:
            cumulative_threshold = _cumulative_voting_threshold(title)
            if agenda_relation_type == "procedural":
                decision = "REVIEW"
                reason = "절차성 안건 — 후보 결격 평가로 자동 찬성하지 않고 표결 구조/선행 안건 확인 필요"
            elif agenda_relation_type == "alternative":
                decision = "REVIEW"
                reason = "대안형/상호배타 가능 안건 — 관련 안건과 조건부 구조 확인 필요"
            else:
                decision = "REVIEW"
                reason = "조건부 안건 — 선행 안건 결과와 효력 조건 확인 필요"
            if cumulative_threshold:
                reason += (
                    f" (집중투표 필요최소지분율: 행사 의결권 기준 약 "
                    f"{cumulative_threshold['guaranteed_election_threshold_pct_of_votes_cast']:.2f}%, "
                    "전원 출석·행사 가정 시 발행주식 대비 동일)"
                )

        # 1.6. 미catch 정관변경 안건 — amendments raw 첨부 (LLM 직접 검토용)
        # 조건: 정관변경 안건 (top 또는 sub) + amendments 있음 + 모든 fallback (title/body/sub) miss
        # → LLM이 raw 본문 보고 catch 못한 강행규정 정합 / 우회 신호 직접 판단
        # fix (260510): 두 가지 동시 fix
        # 1) sub→amendment 매핑 성공 sub는 그 amendment 1개만 첨부 (Ralph 8 매핑 활용)
        # 2) 회사 단위 첨부 flag — 첫 미매핑 안건에 모든 amendments 첨부 / 다음은 anchor (중복 회피)
        if law_layer_hit is None and aoi_amendments and (
            (parent_for_title == "" and _is_charter_top(title))
            or (parent_for_title and _is_charter_top(parent_for_title))
        ):
            target_amendments: list[dict[str, Any]] | None = None
            attach_anchor = False
            mapped_idx = _subagenda_attempted_mappings.get(title)
            if mapped_idx is not None:
                # sub-agenda 매핑 성공 (룰 매치 X) → 매핑된 amendment 1개만
                target_amendments = [aoi_amendments[mapped_idx]]
            elif not _amendments_attached_for_company:
                # 매핑 X + 회사 첫 첨부 → 모든 amendments
                target_amendments = aoi_amendments
                _amendments_attached_for_company = True
            else:
                # 매핑 X + 회사 이미 첨부 → anchor (중복 회피)
                attach_anchor = True

            if target_amendments:
                raw_excerpts = []
                for am in target_amendments:
                    label = (am.get("label") or am.get("clause") or "?").strip()
                    before_raw = (am.get("before") or "").strip()
                    after_raw = (am.get("after") or "").strip()
                    reason_raw = (am.get("reason") or "").strip()
                    parts = []
                    if before_raw:
                        parts.append(f"  변경 전: {before_raw[:300]}")
                    if after_raw:
                        parts.append(f"  변경 후: {after_raw[:300]}")
                    if reason_raw:
                        parts.append(f"  사유: {reason_raw[:120]}")
                    if parts:
                        raw_excerpts.append(f"[{label}]\n" + "\n".join(parts))
                if raw_excerpts:
                    header = (
                        "📄 해당 정관 조문 원문:"
                        if mapped_idx is not None
                        else "📄 정관 변경 조문 원문 (이 회사 정관변경 안건 전체 기준, 1회만 첨부):"
                    )
                    reason += f"\n\n{header}\n" + "\n\n".join(raw_excerpts)
            elif attach_anchor:
                reason += "\n\n📄 정관 조문 원문은 같은 회사의 다른 정관변경 안건에 첨부되어 있습니다"

        # 2. vote_style 정책 default가 명확하면 (for / against / review) 그걸 우선
        # case_by_case면 OPM fallback 결정 유지.
        # 단 법령 layer hit 시는 vote_style 무시 (강행규정 일관성).
        policy_default = _policy_default(policy, category)
        original_decision, original_reason = decision, reason
        if law_layer_hit is None and agenda_relation_type not in {"procedural", "alternative", "conditional", "withdrawn"}:
            decision, reason = _apply_policy_default(policy_default, decision, reason)

        # 3. 정책 근거 명시 (공개 surface에서는 내부 운용사/NPS 식별자 비노출)
        policy_basis = _public_policy_basis(vote_style, category, policy_default, law_layer_hit)

        # 4. 결정 근거 보강 — facts (정량) + risk_factors + policy_citation
        all_director_evals = list(name_to_eval.values()) if category in ("director_election", "audit_committee_election") else None
        facts_all_evals = all_director_evals
        if category == "audit_committee_election" and _is_statutory_auditor_agenda(title) and matched_eval is None:
            facts_all_evals = None
        facts = _extract_facts(
            category,
            title,
            matched_eval,
            # **판정과 같은 payload**. 여기만 FY(N-2)A 로 두면 사유는 「자본잠식률 60.08%」인데
            # 근거란은 「16.4%」를 보여준다(실측 웰바이오텍). 지엔코 때 위험신호에서 고친 것과
            # 같은 결함이 이 경로에 남아 있었다 — 판정·근거·위험신호는 한 해를 가리켜야 한다.
            state_metrics,
            meeting_comp,
            facts_all_evals,
            retirement_payload=retirement_payload,
            fy_raw_from_agenda=fy_raw_from_agenda,
            confirmed_payload=fin_confirmed,
            confirmed_year=confirmed_year,
            crosscheck_payload=fin_metrics,   # 검산은 FY(N-2)A 고정 — 판정 payload 와 다르다
            ownership_payload=ownership,
        )
        # 조항 대장(SSOT) 상세를 구조화 필드로도 노출 (근거 심화 — 260709)
        if law_detail:
            facts["law_detail"] = law_detail
        # 파싱 퀄리티 미달(NO_DATA) → 소집공고 원문 발췌 폴백 (260710 코붕이 지시).
        # 구조화 파싱이 실패해도 사람/LLM이 원문을 직접 읽고 판단할 수 있게 raw 첨부.
        if decision == "NO_DATA":
            _raw = _raw_excerpt(notice_full_text, title)
            if _raw:
                facts["raw_text_fallback"] = _raw
                facts["parsing_quality"] = "low_fallback_to_raw"
        cumulative_threshold = _cumulative_voting_threshold(title)
        if cumulative_threshold:
            facts["cumulative_voting_threshold"] = cumulative_threshold
        risk_factors = _extract_risks(
            category,
            matched_eval,
            # 판정과 **같은 payload** 를 본다. 여기만 FY(N-2)A 로 두면 「자본잠식 없음(2025)」과
            # 「유의: 부분 자본잠식」이 한 문장에 같이 나간다(실측 지엔코). 위험 신호는 회사의
            # 현재 상태를 말하는 것이라, 승인 대상 연도와 어긋나면 신호가 아니라 소음이 된다.
            state_metrics,
            meeting_comp,
            title,
            retirement_payload=retirement_payload,
            ownership_payload=ownership,
        )
        # 자본시장법 §165조의20 — 자산총액 2조원 이상 상장사는 이사회를 특정 성(性)의 이사로만
        # 구성할 수 없다(2020 개정, 2022-08-05 시행). `sexdstn` 은 임원현황에 100% 채워지는데
        # 지금까지 받아만 오고 안 썼다. 이사 선임 안건에서 「여성 0명인데 또 남성만 선임」이면
        # 위반 상태를 유지하는 안건이라 반드시 알려야 한다.
        # 판정은 하지 않는다 — 자산은 FY(N-2) 기준이고 이사회 구성도 스냅샷이라 확정이 아니다.
        if (category in ("director_election", "audit_committee_election")
                and _board_gender and _board_gender.get("female") == 0
                and corp_total_asset_won and corp_total_asset_won >= _GENDER_DIVERSITY_ASSET_KRW):
            risk_factors = list(risk_factors) + [
                f"이사회 여성 0명 (등기이사 {_board_gender.get('male', 0)}명 전원 남성, "
                f"{_board_gender.get('as_of')} 기준) — 자산 2조원 이상 상장사는 이사회를 특정 성의 "
                "이사로만 구성할 수 없습니다(자본시장법 §165조의20)"]
        policy_citation = _policy_citation(category)
        if agenda_relation_type == "withdrawn":
            # 카테고리별 정책을 인용하면 「이 안건을 이 기준으로 판단했다」로 읽힌다 — 판단한 적이 없다.
            policy_citation = "OPM Guideline §표결 — 상정이 철회된 안건에는 찬반을 내지 않습니다"
        # 260724 L-코드 진단 부수(감사의 선임 L0-0-2-5-0): 상법상 감사(상근·비상근)는
        # 감사위원회 위원과 별개 기구 — 결정 경로(3%룰·후보검증)는 공유하되 인용 라벨만 구분.
        if category == "audit_committee_election" and _is_statutory_auditor_agenda(title):
            # 260724 스튜어드십 리뷰 교정: 상장사 감사 선임은 최대주주만 합산 3%(§542-12④),
            # 그 외 주주 개별 3%. 결격은 §542-10②(사외이사 결격 §382③·§542-8과 별개).
            policy_citation = ("OPM Guideline §감사선임 — 상법상 감사(감사위원회 위원 아님): "
                               "최대주주 합산 3%·그 외 주주 개별 3% 의결권 제한(상법 §542-12④), "
                               "결격은 §542-10② 기준. 후보 독립성 검증은 감사위원 경로 준용")

        # FOR로 결론났지만 재무 risk_factors(적자·자본잠식 등)가 계산돼 있으면 reason에 정직 병기.
        # 결정 자체는 안 바꾼다(예: 적자여도 보수한도 동결(+0%)은 정당) — 다만 reason이 위험을
        # 감추지 않게 표면화 (260710 이마트 보수한도 -890억 적자 미언급 사고 = 계산-후-폐기).
        if (
            decision == "FOR"
            and risk_factors
            and category in (
                "director_compensation", "audit_compensation", "retirement_pay",
                "cash_dividend", "financial_statements", "articles_amendment",
            )
        ):
            _risk_note = "; ".join(str(r) for r in risk_factors[:2])
            if _risk_note and _risk_note not in reason:
                reason = f"{reason} · 유의: {_risk_note}"

        # 상법 §449조의2 — 재무제표 승인이 이사회 결의로 갈음돼 주총 보고사항이 된 경우
        # 그 안건은 표결하지 않는다. 찬반을 내면 없는 표결에 의견을 내는 셈이다.
        # 조건부(요건 충족 시 전환 예정)는 공고 시점엔 여전히 표결 안건이므로 판정을 유지하고
        # 전환 가능성만 알린다 — 실측 27건 중 조건부 15 · 확정 12로 둘 다 흔하다.
        _res_status = agenda_row.get("resolution_status") if isinstance(agenda_row, dict) else None
        if _res_status == "report_only":
            decision = "NO_VOTE"
            reason = ("표결 대상이 아님 — 상법 §449조의2에 따라 재무제표를 이사회 결의로 승인하고 "
                      "주주총회에는 보고로 갈음(정관 근거 + 외부감사인 적정의견 + 감사 전원 동의). "
                      f"공고 문면: 「{(agenda_row.get('resolution_note') or '')[:120]}」")
            # 근거도 함께 바꾼다 — 판정은 '표결없음'인데 인용이 '위험 키워드 없으면 FOR'로 남으면
            # 근거가 판정과 반대로 읽힌다.
            policy_citation = ("상법 §449조의2(재무제표 등의 승인에 대한 특칙) — 주주총회 결의사항이 "
                               "아니므로 의결권 행사 대상에서 제외. 재무제표 자체의 적정성은 "
                               "financial_metrics·감사보고서로 별도 검토")
        elif _res_status == "report_if_conditions_met":
            reason = (f"{reason} / 상법 §449조의2 요건(외부감사인 적정의견·감사 전원 동의) 충족 시 "
                      "이사회 승인으로 갈음돼 보고사항으로 바뀔 수 있음 — 총회 직전 정정공고 확인 필요")

        agenda_decisions.append({
            "agenda_title": title,
            "agenda_category": category,
            # 강행규정 layer 규칙 ID — 사유 문장에서 뺐으므로 마커·회귀 테스트는 이 필드를 쓴다
            "law_layer_id": law_layer_id,
            "agenda_id": agenda_row.get("agenda_id"),
            "agenda_relation_type": agenda_relation_type,
            "agenda_relation_reasons": agenda_relation_reasons,
            "proposer_type": proposer_type,
            "decision": decision,
            "reason": reason,
            "facts": facts,
            "risk_factors": risk_factors,
            "policy_citation": policy_citation,
            "policy_basis": policy_basis,
            "policy_default": policy_default,
            "opm_fallback_decision": original_decision if (policy_default and policy_default != "case_by_case") else None,
            # **이 안건을 실제로 읽은 공고**를 가리켜야 한다. 예전에는 `data.rcept_no` 를 찾았는데
            # 접수번호는 `data.notice.rcept_no` 에 있어 항상 None 이었고, 그래서 **다른 도구(후보
            # 평가)가 고른 공고로 폴백**했다. 주총이 잦은 회사(리츠 등)는 그게 아예 다른 회차다 —
            # 실측 SK리츠: 안건은 20260602000425 에서 왔는데 근거는 20260304001363(3월 회차)을
            # 가리켰다. 사용자가 그 링크를 열면 이 안건이 없다.
            "evidence_rcept_no": agm_rcept or (meeting_summary.get("data") or {}).get("rcept_no"),
            # provenance 1단계: 이 안건을 원문 어디서 볼지 — 자식 안건은 부모 섹션 상속
            "source_section": agenda_source_map.get(title)
            or agenda_source_map.get(title_to_parent.get(title) or ""),
            # 분류 품질 메모 (승격 근거·불일치·미등재) — 거버넌스 risk와 분리 (260724 리뷰)
            "classification_note": classification_note,
            # 정합 실패 시 해당 절 원문 통 발췌 (표는 그리드→마크다운 변환)
            "source_excerpt": source_excerpt,
        })
    _mark("decision_engine", stage_started_at)

    # 통합 evidence_refs + 읽은 공시 목록
    evidence: list[EvidenceRef] = []
    disclosures: list[dict[str, Any]] = []
    _by_rcept: dict[str, dict[str, Any]] = {}
    for upstream_payload, label in read_payloads:
        for ref in ((upstream_payload or {}).get("evidence_refs") or []):
            evidence.append(EvidenceRef(
                evidence_id=ref.get("evidence_id", ""),
                source_type=ref.get("source_type", SourceType.DART_API),
                rcept_no=ref.get("rcept_no", ""),
                rcept_dt=ref.get("rcept_dt", ""),
                report_nm=ref.get("report_nm", ""),
                section=ref.get("section", label),
                note=ref.get("note", ""),
            ))
            # 같은 문서를 여러 upstream 이 읽는다(소집공고 하나로 안건·후보·보수를 다 본다) — 접수번호로
            # 묶고 용도를 모은다. 같은 링크를 세 줄로 늘어놓으면 목록이 아니라 소음이 된다.
            rcept = (ref.get("rcept_no") or "").strip()
            if not rcept:
                continue
            entry = _by_rcept.get(rcept)
            if entry is None:
                entry = {
                    "rcept_no": rcept,
                    # 접수일은 upstream 마다 서식이 다르고(`20250311` vs `2025-03-11`) 비는 곳도 있다.
                    # 접수번호 앞 8자리가 곧 접수일이라 그걸로 통일한다.
                    "rcept_dt": _rcept_date(ref.get("rcept_dt", ""), rcept),
                    "report_nm": _disclosure_name(ref, label),
                    "used_for": [],
                    "notes": [],
                    "viewer_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept}",
                }
                _by_rcept[rcept] = entry
                disclosures.append(entry)
            if label not in entry["used_for"]:
                entry["used_for"].append(label)
            note = (ref.get("note") or "").strip()
            if note and note not in entry["notes"]:
                entry["notes"].append(note)

    # filing meta
    # 260710: parsing_failures를 하드코딩 0 → 실제 NO_DATA(구조화 파싱 실패로 권고 불가) 안건
    # 수로 채운다. 죽은 메트릭(항상 all_parsed로 보이던 문제) 정직화. NO_DATA 안건엔 위에서
    # raw_text_fallback을 첨부했으므로 사용자는 partial_failure를 보고 원문으로 넘어갈 수 있다.
    n_decisions = len(agenda_decisions)
    parsing_failures = sum(1 for a in agenda_decisions if a.get("decision") == "NO_DATA")
    filing_meta = build_filing_meta(filing_count=n_decisions, parsing_failures=parsing_failures)
    if filing_meta["no_filing"]:
        status = AnalysisStatus.NO_FILING
    else:
        status = AnalysisStatus.EXACT

    # 표면 한글화 — performance.classification(영문)은 위 decision 분기에서 이미 소비됐다.
    # LLM에 노출되는 candidates_evaluations에는 한글 라벨(부진 등)만 보이도록 치환.
    for _ev in director_evals:
        _perf = _ev.get("performance")
        if isinstance(_perf, dict) and _perf.get("classification"):
            _perf["classification"] = _perf.get("classification_ko") or _perf["classification"]

    # ── 회차 상태 힌트 (260723 UX): 선택된 주총이 이미 종료된 회차면 명시 + 후속 제안 ──
    # meeting_phase는 date 기반 (advise scope는 결과공시 fetch 안 함 — post_result는 안 뜸).
    _meeting_data = (meeting_full.get("data") or {})
    _selected_mt = _meeting_data.get("meeting_type") or meeting_type
    _meeting_phase = _meeting_data.get("meeting_phase")
    _selected_meeting_date = ((_meeting_data.get("selected_meeting") or {}).get("meeting_date")) or "?"
    meeting_closed_hint: str | None = None
    if _meeting_phase in ("post_meeting_pre_result", "post_result"):
        _mt_ko = _MEETING_TYPE_KO.get(_selected_mt, _selected_mt)
        meeting_closed_hint = (
            f"이 {_mt_ko}주총({_selected_meeting_date})은 이미 종료된 회차입니다 — "
            f"이 분석은 사후 복기용입니다. 이후 열렸거나 예정된 임시주총, 또는 이 회차의 "
            f"실제 의결 결과가 필요하시면 말씀해 주세요."
        )

    # ── data dict 구성 (Step 3: scope param 단순 expose) ──
    # 모든 scope 공통 base
    data: dict[str, Any] = {
        "query": company_query,
        "company_id": _company_id(selected),
        "canonical_name": selected.get("corp_name"),
        "year": target_year,
        "year_resolution": year_resolution,
        "selected_meeting_type": _selected_mt,
        "meeting_phase": _meeting_phase,
        "meeting_closed_hint": meeting_closed_hint,
        "fin_reference_year": fin_year,
        "fin_reference_basis": fin_year_basis,
        "meeting_type": meeting_type,
        "vote_style": _public_vote_style_label(vote_style),
        "vote_style_policy_id": _public_vote_style_label(vote_style),
        "vote_style_resolved": bool(policy),
        "audit_history_enabled": check_audit_history,
        "scope": scope,
        "agenda_count": len(agenda_rows),
        "agenda_decisions": agenda_decisions,
        "candidates_count": len(director_evals),
        "candidates_evaluations": director_evals,
        "segment_reference": segment_reference,
        "ownership_summary": (ownership.get("data") or {}).get("summary"),
        "governance_summary": (gov_report.get("data") or {}).get("summary"),
        # 15개 핵심지표 중 **미준수(X)** 만 골라 싣는다. 준수율(93.3%)만 보여주면 어느 지표가
        # 빠졌는지 알 수 없는데, 그중엔 의결권 판단에 바로 닿는 것이 있다 —
        # 「사외이사가 이사회 의장인지 여부」(CEO·의장 겸직) ·
        # 「이사회 구성원 모두 단일성(性)이 아님」(자본시장법 §165조의20) ·
        # 「집중투표제 채택」 · 「기업가치 훼손 책임자의 임원 선임 방지」.
        # 라벨만 싣지 않는다 — 회사가 적은 **사유**(note)와 전년 값(prior)을 함께 싣는다.
        # 「사외이사가 이사회 의장인지 여부 X」는 그것만 보면 왜인지 모르지만 회사는
        # 「사내이사가 이사회 의장직 수행」이라 밝혀 둔다. 전년 값이 있으면 이번에 나빠진
        # 것인지 계속 그랬던 것인지도 갈린다. 실측 미준수 82개 중 68개(82.9%)에 사유 있음.
        "governance_non_compliant": [
            {"label": (it.get("label") or "").strip(),
             "note": (it.get("note") or "").strip() or None,
             # 비고가 「(세부원칙 4-1) 참고」처럼 포인터뿐이면 그 절을 데려온 것
             "note_ref": (it.get("note_ref") or "").strip() or None,
             "prior": (it.get("prior") or "").strip() or None}
            for it in ((gov_report.get("data") or {}).get("metrics_summary") or [])
            if isinstance(it, dict) and (it.get("current") or "").strip().upper() == "X"
        ] or None,
        "financial_summary": (fin_metrics.get("data") or {}).get("summary"),
        # 이 메모를 만들며 읽은 공시 전부. 판정을 되짚으려면 무엇을 봤는지 알아야 한다.
        "disclosures_read": disclosures,
        **filing_meta,
        "usage": build_usage(client.api_call_snapshot() - calls_start),
    }
    timings_ms["total"] = int((time.perf_counter() - total_started_at) * 1000)
    data["timings_ms"] = timings_ms

    # 단일 scope (decisions) — 모든 specialized scope 폐지.
    # 사용자가 raw upstream 보고 싶으면 각 tool 직접 호출:
    # - agenda → shareholder_meeting_notice(scope="agenda")
    # - candidates → director_evaluation은 internal, 후보 detail은 본 응답의 candidates_evaluations 활용
    # - financial / governance / ownership → financial_metrics / corp_gov_report / ownership_structure
    # - policy_basis / proxy_battle / engagement → 별도 ralph 또는 사용 시 archive에서 부활

    envelope_warnings: list[str] = []
    if year_resolution.get("mode") in ("fallback_prev_year", "resolve_error"):
        envelope_warnings.append(year_resolution["basis"])
    if meeting_closed_hint:
        envelope_warnings.append(meeting_closed_hint)

    return ToolEnvelope(
        tool="proxy_advise_before_meeting",
        status=status,
        subject=selected.get("corp_name", company_query),
        warnings=envelope_warnings,
        data=data,
        evidence_refs=evidence,
    ).to_dict()
