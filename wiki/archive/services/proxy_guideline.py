"""v2 proxy_guideline service.

자산운용사 의결권 행사 정책 + 행사내역 + Open Proxy Guideline + 12 매트릭스 통합 조회.

데이터 위치: open_proxy_mcp/data/asset_managers/
  - _index.json (운용사 메타)
  - _consensus_matrix.json (운용사 합의/이견)
  - _decision_matrices.json (12 카테고리 의사결정 매트릭스)
  - policies/{manager_id}_{version}.json (정책)
  - records/{manager_id}_{period}.json (행사내역)
  - nps_records/nps_list_{period}.json (N연기금 list 캐시)
  - nps_records/details/{ticker}_{gmos_ymd}_{kind}.json (N연기금 상세 캐시)

7 scope:
  - policy    : 정책 조회 (default policy_id=open_proxy)
  - record    : 운용사 실제 행사내역
  - predict   : 회사·안건 → 정책 적용 예측 (matrix scoring 추후 확장)
  - compare   : N개 정책 비교 매트릭스
  - consensus : 운용사 합의/이견 분석
  - audit     : 정책 vs 실제 행사내역 갭
  - nps_record: N연기금 의결권 행사내역 (실시간 + 정적 캐시 하이브리드)

데이터는 정적 (실시간 호출 X). DART API 호출 0회 (cross-domain 시만).
nps_record는 fund.nps.or.kr 직접 크롤링 + JSON 캐시.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from importlib.resources import files
from pathlib import Path
from typing import Any

from open_proxy_mcp.services.contracts import (
    AnalysisStatus,
    ToolEnvelope,
    build_usage,
)

logger = logging.getLogger(__name__)


_SUPPORTED_SCOPES = {
    "policy", "record", "predict", "compare", "consensus", "audit", "nps_record",
}
_DATA_ROOT = files("open_proxy_mcp.data.asset_managers")
_N연기금_RECORDS_DIR = _DATA_ROOT / "nps_records"
_N연기금_DETAIL_DIR = _N연기금_RECORDS_DIR / "details"

# 최근 N일 안의 호출은 실시간 N연기금 호출 (정적 캐시 무시)
_N연기금_REALTIME_WINDOW_DAYS = 30


# ── 데이터 로딩 ──


def load_index() -> dict[str, Any]:
    """_index.json — 운용사 메타 + OPM 디폴트."""
    path = _DATA_ROOT / "_index.json"
    return json.loads(path.read_text(encoding="utf-8"))


def load_policy(policy_id: str) -> dict[str, Any] | None:
    """정책 조회. policy_id=open_proxy면 OPM 정책, 그 외 운용사 id."""
    if policy_id == "open_proxy":
        candidates = ["open_proxy_v1.json"]
    else:
        # 최신 버전 자동 탐색 (manager_id_*.json 중 가장 최신)
        index = load_index()
        manager_meta = index.get("managers", {}).get(policy_id)
        if not manager_meta:
            return None
        policy_file = manager_meta.get("policy_file", "")
        if not policy_file:
            return None
        candidates = [policy_file.split("/")[-1]]

    policies_dir = _DATA_ROOT / "policies"
    for fname in candidates:
        try:
            return json.loads((policies_dir / fname).read_text(encoding="utf-8"))
        except FileNotFoundError:
            continue
    return None


def load_records(manager_id: str, period: str = "") -> list[dict[str, Any]]:
    """행사내역 조회. period 지정 없으면 모든 period."""
    records_dir = _DATA_ROOT / "records"
    out = []
    if period:
        path = records_dir / f"{manager_id}_{period}.json"
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except FileNotFoundError:
            pass
    else:
        # 해당 manager의 모든 period
        for entry in records_dir.iterdir():
            name = entry.name
            if name.startswith(f"{manager_id}_") and name.endswith(".json"):
                out.append(json.loads(entry.read_text(encoding="utf-8")))
    return out


def load_consensus_matrix() -> dict[str, Any]:
    """_consensus_matrix.json — 운용사 합의/이견 분석."""
    path = _DATA_ROOT / "_consensus_matrix.json"
    return json.loads(path.read_text(encoding="utf-8"))


def load_decision_matrices() -> dict[str, Any]:
    """_decision_matrices.json — 12 카테고리 의사결정 매트릭스."""
    path = _DATA_ROOT / "_decision_matrices.json"
    return json.loads(path.read_text(encoding="utf-8"))


def list_managers() -> list[str]:
    index = load_index()
    return list(index.get("managers", {}).keys())


# ── 12 카테고리 표준 ──

_CATEGORIES = [
    "financial_statements",
    "cash_dividend",
    "articles_amendment",
    "director_election",
    "audit_committee_election",
    "director_compensation",
    "treasury_share",
    "merger",
    "spin_off",
    "capital_increase_decrease",
    "cb_bw",
    "shareholder_proposal",
]

_CATEGORY_KO = {
    "financial_statements": "재무제표",
    "cash_dividend": "현금배당",
    "articles_amendment": "정관변경",
    "director_election": "이사 선임",
    "audit_committee_election": "감사위원 선임",
    "director_compensation": "이사 보수",
    "treasury_share": "자기주식",
    "merger": "합병",
    "spin_off": "분할",
    "capital_increase_decrease": "유증/감자",
    "cb_bw": "CB/BW",
    "shareholder_proposal": "주주제안",
}


# ── 안건 카테고리 자동 분류 (predict scope) ──


def classify_agenda(agenda_title: str, agenda_type_raw: str = "") -> str:
    """안건명 + 의안유형 → 12 카테고리 매핑.

    우선순위 (위→아래):
    1. 주주제안
    2. 감사위원 (선임/해임 vs 분리선출 정관 변경 분기)
    3. M&A (합병/분할/주식교환)
    4. 희석성 증권 (CB/BW > 유증/감자)
    5. 자기주식
    6. 보수/퇴직금/스톡옵션 ("이사 보수" 등 — director_election보다 먼저)
    7. 이사 선임/해임 (감사위원·보수 다 거른 후)
    8. 정관변경 (명시 + 정관 부속 안건 키워드)
    9. 배당
    10. 재무제표
    """
    text_ko = f"{agenda_type_raw} {agenda_title}"
    text_lower = text_ko.lower()

    if "주주제안" in text_ko:
        return "shareholder_proposal"

    if "감사위원" in text_ko:
        if "분리선출" in text_ko or "분리 선출" in text_ko:
            return "articles_amendment"
        if "선임" in text_ko or "해임" in text_ko or "후보" in text_ko:
            return "audit_committee_election"
        return "audit_committee_election"

    if "합병" in text_ko:
        return "merger"
    if any(k in text_ko for k in ["물적분할", "인적분할", "분할합병"]):
        return "spin_off"
    if "분할" in text_ko and "분리선출" not in text_ko:
        return "spin_off"

    if any(k in text_ko for k in ["전환사채", "신주인수권부사채"]) or "cb" in text_lower or "bw" in text_lower:
        return "cb_bw"
    if any(k in text_ko for k in ["유상증자", "무상증자", "신주발행", "주식분할", "주식병합", "액면분할", "액면병합"]):
        return "capital_increase_decrease"

    if any(k in text_ko for k in ["자기주식", "자사주", "자본감소", "감자"]):
        return "treasury_share"

    # 보수·퇴직금·스톡옵션 (이사 선임보다 먼저)
    if any(k in text_ko for k in [
        "보수한도", "보수승인", "보수액", "이사 보수", "감사 보수", "임원 보수", "임원보수",
        "퇴직금", "퇴직위로금", "퇴직금규정", "퇴직금 지급", "퇴직급여",
        "주식매수선택권", "스톡옵션", "성과급여", "성과급",
    ]):
        return "director_compensation"

    if "이사" in text_ko and ("선임" in text_ko or "해임" in text_ko):
        return "director_election"

    # 정관변경 (명시 + 부속 안건 키워드)
    articles_keywords = [
        "정관",
        "사업목적", "목적사업", "회사명", "상호 변경", "상호변경",
        "본점", "본사 소재", "본점이전",
        "회계연도",
        "전자주주총회", "전자투표", "서면투표",
        "주주총회 소집", "주주총회의 소집", "주주총회 결의",
        "의결권 대리",
        "이사회 규모", "이사회 내 위원회", "이사회 의장", "사외이사 비중",
        "시차임기제", "황금낙하산", "독약처방",
        "사외이사 명칭",
        "집중투표 규정", "집중투표제 도입", "집중투표제 배제",
    ]
    if any(k in text_ko for k in articles_keywords):
        return "articles_amendment"

    if "배당" in text_ko and "주식배당" not in text_ko:
        return "cash_dividend"
    if any(k in text_ko for k in ["재무제표", "결산", "이익잉여금"]):
        return "financial_statements"
    return "other"


# ── Scope: policy ──


def scope_policy(policy_id: str = "open_proxy", agenda_category: str = "") -> dict[str, Any]:
    """정책 조회. agenda_category 지정 시 해당 카테고리만."""
    pol = load_policy(policy_id)
    if not pol:
        return {
            "status": "error",
            "warning": f"정책 미발견: policy_id={policy_id}",
            "available_policies": ["open_proxy"] + list_managers(),
        }

    rules = pol.get("voting_rules", {})
    if agenda_category:
        if agenda_category not in rules:
            return {
                "status": "error",
                "warning": f"카테고리 미발견: {agenda_category}",
                "available_categories": list(rules.keys()),
            }
        return {
            "status": "exact",
            "policy_id": policy_id,
            "policy_meta": pol.get("policy_meta", {}),
            "category": agenda_category,
            "rule": rules[agenda_category],
        }

    return {
        "status": "exact",
        "policy_id": policy_id,
        "policy_meta": pol.get("policy_meta", {}),
        "general_principles": pol.get("general_principles", []),
        "decision_process": pol.get("decision_process", {}),
        "voting_rules_summary": {
            cat: {
                "default": rules.get(cat, {}).get("default", "not_specified"),
                "for_count": len(rules.get(cat, {}).get("for", [])),
                "against_count": len(rules.get(cat, {}).get("against", [])),
                "review_count": len(rules.get(cat, {}).get("review", [])),
            }
            for cat in _CATEGORIES
        },
        "novel_topics": pol.get("novel_topics", {}),
        "korea_specific": pol.get("korea_specific", []),
        "completeness": pol.get("completeness", {}),
    }


# ── Scope: record ──


def scope_record(
    manager: str,
    company: str = "",
    year: int = 0,
    period: str = "",
    agenda_category: str = "",
) -> dict[str, Any]:
    """운용사 실제 행사내역 조회. company/year/period/category 필터 가능."""
    if not manager:
        return {"status": "error", "warning": "manager 필수"}

    records = load_records(manager, period)
    if not records:
        return {
            "status": "error",
            "warning": f"행사내역 미발견: manager={manager}, period={period}",
            "available_managers": list_managers(),
        }

    all_votes = []
    period_summary = []
    for rec in records:
        votes = rec.get("votes", [])
        if company:
            votes = [v for v in votes if company in v.get("company", "")]
        if year:
            votes = [v for v in votes if v.get("meeting_date", "").startswith(str(year))]
        if agenda_category:
            votes = [v for v in votes if v.get("agenda_category") == agenda_category]
        all_votes.extend(votes)
        period_summary.append({
            "period": rec.get("period_label"),
            "filtered_count": len(votes),
            "original_total": rec.get("summary", {}).get("total_votes", 0),
        })

    # 결과 통계
    from collections import Counter
    decisions = Counter(v.get("decision", "") for v in all_votes)
    categories = Counter(v.get("agenda_category", "other") for v in all_votes)
    companies = Counter(v.get("company", "") for v in all_votes)

    return {
        "status": "exact" if all_votes else "partial",
        "manager": manager,
        "filters": {"company": company, "year": year, "period": period, "agenda_category": agenda_category},
        "period_summary": period_summary,
        "total_votes": len(all_votes),
        "decision_breakdown": dict(decisions),
        "category_breakdown": dict(categories.most_common(8)),
        "company_breakdown_top10": dict(companies.most_common(10)),
        "votes": all_votes[:100],  # 최대 100건 (전체 보려면 별도 query)
        "votes_truncated": len(all_votes) > 100,
    }


# ── Scope: consensus ──


def scope_consensus(agenda_category: str = "", topic_id: str = "") -> dict[str, Any]:
    """운용사 합의/이견 분석. category 필터 가능."""
    matrix = load_consensus_matrix()
    cats = matrix.get("categories", {})

    if agenda_category:
        if agenda_category not in cats:
            return {"status": "error", "warning": f"카테고리 미발견: {agenda_category}"}
        cat_data = cats[agenda_category]
        topics = cat_data.get("topics", [])
        if topic_id:
            topic = next((t for t in topics if t.get("topic_id") == topic_id), None)
            if not topic:
                return {"status": "error", "warning": f"topic 미발견: {topic_id}"}
            return {"status": "exact", "category": agenda_category, "topic": topic}
        return {
            "status": "exact",
            "category": agenda_category,
            "summary": cat_data.get("summary", {}),
            "topics": topics,
        }

    return {
        "status": "exact",
        "managers": matrix.get("managers", []),
        "global_summary": matrix.get("global_summary", {}),
        "category_summaries": {
            cat: cats.get(cat, {}).get("summary", {})
            for cat in _CATEGORIES
            if cat in cats
        },
    }


# ── Scope: compare ──


def scope_compare(compare_policies: list[str], agenda_category: str = "") -> dict[str, Any]:
    """N개 정책 비교 매트릭스. policy_id 리스트 받음."""
    if not compare_policies:
        compare_policies = ["open_proxy"] + list_managers()

    loaded = {}
    missing = []
    for pid in compare_policies:
        pol = load_policy(pid)
        if pol:
            loaded[pid] = pol
        else:
            missing.append(pid)

    if not loaded:
        return {"status": "error", "warning": "정책 모두 미발견", "missing": missing}

    if agenda_category:
        comparison = {}
        for pid, pol in loaded.items():
            r = pol.get("voting_rules", {}).get(agenda_category, {})
            comparison[pid] = {
                "default": r.get("default", "not_specified"),
                "for": r.get("for", []),
                "against": r.get("against", []),
                "review": r.get("review", []),
            }
        return {
            "status": "exact",
            "category": agenda_category,
            "policies": list(loaded.keys()),
            "missing": missing,
            "comparison": comparison,
        }

    # 전 카테고리 요약 매트릭스
    matrix = {}
    for cat in _CATEGORIES:
        matrix[cat] = {}
        for pid, pol in loaded.items():
            r = pol.get("voting_rules", {}).get(cat, {})
            matrix[cat][pid] = {
                "default": r.get("default", "not_specified"),
                "for": len(r.get("for", [])),
                "against": len(r.get("against", [])),
                "review": len(r.get("review", [])),
            }

    return {
        "status": "exact",
        "policies": list(loaded.keys()),
        "missing": missing,
        "matrix": matrix,
    }


# ── Scope: audit ──


def scope_audit(manager: str, agenda_category: str = "") -> dict[str, Any]:
    """정책 vs 실제 행사내역 갭 분석.

    - 정책에서 against criterion이 있는 카테고리에서 실제 against rate
    - 정책 충실도 점수 (높을수록 정책-실제 일치)
    """
    if not manager:
        return {"status": "error", "warning": "manager 필수"}

    pol = load_policy(manager)
    records = load_records(manager)
    if not pol or not records:
        return {
            "status": "error",
            "warning": f"정책 또는 행사내역 미발견: manager={manager}",
        }

    all_votes = []
    for rec in records:
        all_votes.extend(rec.get("votes", []))

    # 카테고리별 갭
    rules = pol.get("voting_rules", {})
    gaps = {}
    target_cats = [agenda_category] if agenda_category else _CATEGORIES
    for cat in target_cats:
        cat_votes = [v for v in all_votes if v.get("agenda_category") == cat]
        if not cat_votes:
            continue
        rule = rules.get(cat, {})
        n_total = len(cat_votes)
        n_for = sum(1 for v in cat_votes if v.get("decision") == "for")
        n_against = sum(1 for v in cat_votes if v.get("decision") == "against")
        n_abstain = sum(1 for v in cat_votes if v.get("decision") == "abstain")
        n_not_voted = sum(1 for v in cat_votes if v.get("decision") == "not_voted")
        against_rate = round(n_against / n_total * 100, 1) if n_total else 0.0

        # 정책에 against criterion 갯수
        policy_against_count = len(rule.get("against", []))
        policy_review_count = len(rule.get("review", []))

        # 갭 평가
        if policy_against_count >= 3 and against_rate < 5:
            assessment = "policy_strict_practice_lenient"
        elif policy_against_count >= 3 and against_rate >= 15:
            assessment = "policy_strict_practice_strict"
        elif policy_against_count <= 1 and against_rate >= 15:
            assessment = "policy_lenient_practice_strict"
        else:
            assessment = "balanced"

        gaps[cat] = {
            "category": cat,
            "category_ko": _CATEGORY_KO.get(cat, cat),
            "total_votes": n_total,
            "for_count": n_for,
            "against_count": n_against,
            "abstain_count": n_abstain,
            "not_voted_count": n_not_voted,
            "against_rate_pct": against_rate,
            "policy_against_criteria_count": policy_against_count,
            "policy_review_criteria_count": policy_review_count,
            "assessment": assessment,
        }

    # 전체 통계
    overall_against = sum(g["against_count"] for g in gaps.values())
    overall_total = sum(g["total_votes"] for g in gaps.values())
    overall_against_rate = round(overall_against / overall_total * 100, 1) if overall_total else 0.0

    return {
        "status": "exact",
        "manager": manager,
        "policy_meta": pol.get("policy_meta", {}),
        "overall": {
            "total_votes": overall_total,
            "total_against": overall_against,
            "overall_against_rate_pct": overall_against_rate,
        },
        "gaps": gaps,
    }


# ── Scope: predict ──


async def scope_predict(
    company: str,
    agenda_title: str,
    agenda_type_raw: str = "",
    policy_id: str = "open_proxy",
    matrix_dimensions: dict[str, int] | None = None,
    auto_score: bool = True,
    meeting_date: str = "",
    notice_disclosure_date: str = "",
    extra_agenda_titles: list[str] | None = None,
) -> dict[str, Any]:
    """회사·안건 → 정책 적용 예측 + 매트릭스 자동 채점 (v1.3, 2026-04-29).

    auto_score=True (기본): data tool 자동 호출 → ~85+ dim 채점 → 빙고 평가 → for/against/review 자동 결정.
    matrix_dimensions: 사용자 override (manual dim 입력 또는 자동 채점 보정).

    안전망:
      - 데이터 부족 dim은 None → conservative 빙고 skip
      - 채점 불가 카테고리 (other) → review fallback
      - manual dim (adverse_news 등)은 명시적 표시 + 사용자 input 권유.
    """
    pol = load_policy(policy_id)
    if not pol:
        return {"status": "error", "warning": f"정책 미발견: {policy_id}"}

    matrices = load_decision_matrices()
    cat = classify_agenda(agenda_title, agenda_type_raw)
    rule = pol.get("voting_rules", {}).get(cat, {})
    matrix_id = rule.get("matrix_id") or f"matrix_{cat}"
    matrix = matrices.get("matrices", {}).get(matrix_id, {})

    # ── 자동 채점 ──
    matrix_score: dict[str, Any] | None = None
    auto_decision: dict[str, Any] | None = None
    bingo_matches: list[dict[str, Any]] = []
    auto_warnings: list[str] = []
    data_calls: dict[str, str] = {}
    manual_dims: list[str] = []
    auto_dims: list[str] = []

    final_scores: dict[str, int | None] = {}

    if auto_score and matrix and cat != "other":
        try:
            from open_proxy_mcp.services.proxy_guideline_scoring import (
                aggregate_score_to_decision,
                auto_score_matrix,
                evaluate_all_bingo_patterns,
                manual_dims_for_category,
            )

            agenda_titles_for_score = [agenda_title] + list(extra_agenda_titles or [])
            score_result = await auto_score_matrix(
                company,
                cat,
                agenda_titles=agenda_titles_for_score,
                user_dimensions=matrix_dimensions,
                meeting_date=meeting_date,
                notice_disclosure_date=notice_disclosure_date,
            )
            final_scores = score_result.get("scores", {}) or {}
            data_calls = score_result.get("data_calls", {}) or {}
            auto_warnings = score_result.get("warnings", []) or []
            manual_dims = score_result.get("manual_dims", []) or []
            auto_dims = score_result.get("auto_dims", []) or []

            # 빙고 평가
            valid_for_bingo = {k: v for k, v in final_scores.items() if v is not None}
            bingo_matches = evaluate_all_bingo_patterns(
                matrix,
                valid_for_bingo,
                agenda_date=meeting_date or None,
                agenda_category=cat,
            )
            auto_decision = aggregate_score_to_decision(final_scores, matrix, bingo_matches)

            matrix_score = {
                "dimensions_scored": final_scores,
                "raw_score": auto_decision.get("raw_score"),
                "max_score": auto_decision.get("max_score"),
                "red_count": auto_decision.get("red_count"),
                "yellow_count": auto_decision.get("yellow_count"),
                "green_count": auto_decision.get("green_count"),
                "unknown_count": auto_decision.get("unknown_count"),
                "thresholds": matrix.get("scoring", {}).get("thresholds", {}),
                "scored_dim_count": auto_decision.get("scored_dim_count"),
                "total_dim_count": auto_decision.get("total_dim_count"),
            }
        except Exception as e:
            auto_warnings.append(f"자동 채점 오류: {type(e).__name__}: {str(e)[:200]}")
            logger.exception("auto_score_matrix 실패: %s", e)

    elif matrix_dimensions and matrix:
        # 사용자 input만 사용 (auto_score=False 또는 fallback)
        try:
            from open_proxy_mcp.services.proxy_guideline_scoring import (
                aggregate_score_to_decision,
                evaluate_all_bingo_patterns,
            )

            dims = matrix.get("dimensions", [])
            for d in dims:
                dim_id = d.get("dim_id")
                if dim_id in matrix_dimensions:
                    final_scores[dim_id] = matrix_dimensions[dim_id]
                else:
                    final_scores[dim_id] = None

            valid_for_bingo = {k: v for k, v in final_scores.items() if v is not None}
            bingo_matches = evaluate_all_bingo_patterns(
                matrix,
                valid_for_bingo,
                agenda_date=meeting_date or None,
                agenda_category=cat,
            )
            auto_decision = aggregate_score_to_decision(final_scores, matrix, bingo_matches)
            matrix_score = {
                "dimensions_scored": final_scores,
                "raw_score": auto_decision.get("raw_score"),
                "max_score": auto_decision.get("max_score"),
                "red_count": auto_decision.get("red_count"),
                "yellow_count": auto_decision.get("yellow_count"),
                "green_count": auto_decision.get("green_count"),
                "unknown_count": auto_decision.get("unknown_count"),
                "thresholds": matrix.get("scoring", {}).get("thresholds", {}),
                "scored_dim_count": auto_decision.get("scored_dim_count"),
                "total_dim_count": auto_decision.get("total_dim_count"),
            }
        except Exception as e:
            auto_warnings.append(f"매뉴얼 채점 오류: {type(e).__name__}: {str(e)[:200]}")

    return {
        "status": "exact",
        "company": company,
        "agenda_title": agenda_title,
        "agenda_category": cat,
        "agenda_category_ko": _CATEGORY_KO.get(cat, cat),
        "policy_id": policy_id,
        "policy_default": rule.get("default"),
        "policy_for": rule.get("for", []),
        "policy_against": rule.get("against", []),
        "policy_review": rule.get("review", []),
        "matrix_id": matrix_id,
        "matrix": {
            "dimensions": matrix.get("dimensions", []),
            "scoring": matrix.get("scoring", {}),
            "bingo_patterns": matrix.get("bingo_patterns", []),
        } if matrix else None,
        "matrix_score": matrix_score,
        "auto_decision": auto_decision,
        "bingo_matches": bingo_matches,
        "auto_score_enabled": auto_score,
        "data_calls": data_calls,
        "manual_dims": manual_dims,
        "auto_dims": auto_dims,
        "warnings": auto_warnings,
        "disclaimer": "자동 채점 결과는 참고용. 최종 판단은 사용자가 검토 후 결정. 데이터 부족 dim은 manual input으로 보정 가능.",
        "evaluation_note": (
            "자동 채점 활성화 — data tool 호출 + 빙고 평가 통합."
            if auto_score
            else "정책 룰 + 매트릭스 구조만 표시. matrix_dimensions로 사용자 채점 가능."
        ),
    }


# ── Scope: nps_record (N연기금) ──


def _nps_period_label(year: int) -> str:
    """연도 → '2025-q1' 등 정적 캐시 partition (분기별)."""
    if not year:
        year = date.today().year
    return f"{year}"


def _nps_resolve_data_dir() -> Path | None:
    """importlib.resources Traversable → Path. 없으면 None."""
    try:
        # MultiplexedPath나 PosixPath 모두 .iterdir()는 동작하지만 mkdir/write가 필요
        return Path(str(_N연기금_RECORDS_DIR))
    except Exception:
        return None


def _nps_list_cache_path(year: int) -> Path | None:
    base = _nps_resolve_data_dir()
    if base is None:
        return None
    return base / f"nps_list_{year}.json"


def _nps_detail_cache_path(ticker: str, gmos_ymd: str, gmos_kind_cd: str) -> Path | None:
    base = _nps_resolve_data_dir()
    if base is None:
        return None
    return base / "details" / f"{ticker}_{gmos_ymd}_{gmos_kind_cd}.json"


def _nps_load_static_list(year: int) -> list[dict[str, Any]] | None:
    p = _nps_list_cache_path(year)
    if p is None or not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("N연기금 list cache 로드 실패: %s", e)
        return None


def _nps_save_static_list(year: int, rows: list[dict[str, Any]]) -> bool:
    p = _nps_list_cache_path(year)
    if p is None:
        return False
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception as e:
        logger.warning("N연기금 list cache 저장 실패: %s", e)
        return False


def _nps_load_static_detail(ticker: str, gmos_ymd: str, gmos_kind_cd: str) -> dict[str, Any] | None:
    p = _nps_detail_cache_path(ticker, gmos_ymd, gmos_kind_cd)
    if p is None or not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("N연기금 detail cache 로드 실패: %s", e)
        return None


def _nps_save_static_detail(ticker: str, gmos_ymd: str, gmos_kind_cd: str, payload: dict[str, Any]) -> bool:
    p = _nps_detail_cache_path(ticker, gmos_ymd, gmos_kind_cd)
    if p is None:
        return False
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception as e:
        logger.warning("N연기금 detail cache 저장 실패: %s", e)
        return False


def _is_recent_meeting(gmos_date: str, days: int = _N연기금_REALTIME_WINDOW_DAYS) -> bool:
    """주총일이 오늘 기준 최근 days일 안이면 True (실시간 우선)."""
    if not gmos_date:
        return False
    try:
        s = gmos_date.replace("-", "").replace("/", "")
        if len(s) != 8:
            return False
        d = datetime.strptime(s, "%Y%m%d").date()
        delta = abs((date.today() - d).days)
        return delta <= days
    except Exception:
        return False


def _nps_filter_rows(
    rows: list[dict[str, Any]],
    company: str = "",
    ticker: str = "",
    nps_code: str = "",
    start_date: str = "",
    end_date: str = "",
) -> list[dict[str, Any]]:
    out = []
    s = start_date.replace("-", "").replace("/", "") if start_date else ""
    e = end_date.replace("-", "").replace("/", "") if end_date else ""
    for r in rows:
        if company and company not in r.get("company_name", ""):
            continue
        if ticker and r.get("ticker") != ticker:
            continue
        if nps_code and r.get("nps_code") != nps_code:
            continue
        if s and r.get("gmos_ymd", "") < s:
            continue
        if e and r.get("gmos_ymd", "") > e:
            continue
        out.append(r)
    return out


async def _nps_resolve_ticker(
    company: str = "", ticker: str = "", nps_code: str = ""
) -> tuple[str, str, str, str]:
    """입력 → (ticker, nps_code, company_resolved, warning).

    우선순위: nps_code > ticker > company → DART resolve_company_query.
    N연기금 코드 5자리 + '0' = 티커.
    """
    if nps_code:
        nc = str(nps_code).strip().zfill(5)
        return nc + "0", nc, company, ""
    if ticker:
        t = str(ticker).strip().zfill(6)
        # 티커 마지막 자리가 '0'이어야 N연기금 매핑 가능 (한국 표준)
        nc = t[:-1] if t.endswith("0") and len(t) == 6 else ""
        return t, nc, company, ""
    if company:
        try:
            from open_proxy_mcp.services.company import resolve_company_query
            res = await resolve_company_query(company)
            sel = res.selected or {}
            sc = (sel.get("stock_code") or "").strip()
            if sc and len(sc) == 6:
                nc = sc[:-1] if sc.endswith("0") else ""
                return sc, nc, sel.get("corp_name", company), ""
            return "", "", company, f"DART resolve 실패: {company}"
        except Exception as e:
            return "", "", company, f"resolve_company_query 에러: {e}"
    return "", "", "", "company / ticker / nps_code 중 1개는 필수"


async def scope_nps_record(
    company: str = "",
    ticker: str = "",
    nps_code: str = "",
    year: int = 0,
    start_date: str = "",
    end_date: str = "",
    fetch_detail: bool = True,
    force_refresh: bool = False,
    max_details: int = 5,
) -> dict[str, Any]:
    """N연기금 의결권 행사내역 조회.

    Strategy:
    1. company/ticker/nps_code 중 하나로 회사 식별 → ticker + N연기금 코드
    2. year 기반 list 조회 (정적 캐시 우선, 미존재 또는 force_refresh면 실시간)
    3. ticker filter → 매칭 row만 반환
    4. fetch_detail=True 시 상위 max_details건의 detail 추가 호출
       - 최근 30일 안의 주총은 항상 실시간
       - 그 외는 정적 캐시 우선 → 미존재 시 실시간 + 캐시 저장
    """
    # 0. 입력 정규화
    resolved_ticker, resolved_nps_code, company_label, warn = await _nps_resolve_ticker(
        company=company, ticker=ticker, nps_code=nps_code
    )
    if warn and not (resolved_ticker or resolved_nps_code or company):
        return {"status": "error", "warning": warn}

    # 1. 연도 결정
    if not year:
        if start_date:
            try:
                year = int(start_date[:4])
            except Exception:
                year = date.today().year
        else:
            year = date.today().year

    # N연기금 사이트 기본 검색 윈도우: 4월말 ~ 다음해 4월말 (시즌 단위)
    if not start_date:
        start_date = f"{year}-04-29"
    if not end_date:
        end_date = f"{year + 1}-04-29"

    # 2. list 조회 (정적 캐시 우선)
    list_source = "static_cache"
    rows = None if force_refresh else _nps_load_static_list(year)
    if rows is None:
        try:
            from open_proxy_mcp.dart.nps_client import N연기금Client
            async with N연기금Client() as nc:
                # 회사명 직접 검색이 가능하면 그쪽이 더 빠름
                search_company = company_label if company_label else (company or "")
                rows = await nc.search_voting(start_date, end_date, company_name=search_company)
            list_source = "live_nps"
            # 빈 검색이거나 기간이 정상이면 캐시 갱신 (회사명 필터 없는 풀 dump일 때만)
            if not search_company and rows:
                _nps_save_static_list(year, rows)
        except Exception as e:
            return {
                "status": "error",
                "warning": f"N연기금 조회 실패: {type(e).__name__}: {str(e)[:200]}",
            }

    # 3. filter
    filtered = _nps_filter_rows(
        rows or [],
        company=company_label or company,
        ticker=resolved_ticker,
        nps_code=resolved_nps_code,
        start_date=start_date,
        end_date=end_date,
    )

    # 4. detail
    details: list[dict[str, Any]] = []
    detail_source: dict[str, str] = {}  # row_key → source
    detail_errors: list[str] = []
    if fetch_detail and filtered:
        try:
            from open_proxy_mcp.dart.nps_client import N연기금Client
            client_needed = False
            async with N연기금Client() as nc:
                for r in filtered[:max_details]:
                    t = r.get("ticker") or ""
                    ymd = r.get("gmos_ymd") or ""
                    kind = r.get("gmos_kind_cd") or "1"
                    cache = (
                        None
                        if (force_refresh or _is_recent_meeting(ymd))
                        else _nps_load_static_detail(t, ymd, kind)
                    )
                    if cache is not None:
                        details.append(cache)
                        detail_source[f"{t}_{ymd}_{kind}"] = "static_cache"
                        continue
                    client_needed = True
                    try:
                        d = await nc.get_voting_detail(
                            nps_code=r.get("nps_code"),
                            gmos_ymd=ymd,
                            gmos_kind_cd=kind,
                            edwm_vtrt_use_sn=r.get("edwm_vtrt_use_sn", ""),
                            data_pvsn_inst_cd_vl=r.get("data_pvsn_inst_cd_vl", "0095000"),
                        )
                        details.append(d)
                        detail_source[f"{t}_{ymd}_{kind}"] = "live_nps"
                        if not _is_recent_meeting(ymd):
                            _nps_save_static_detail(t, ymd, kind, d)
                    except Exception as e:
                        detail_errors.append(f"{r.get('company_name', '?')}/{ymd}: {e}")
            if not client_needed:
                # 모두 캐시에서 충당
                pass
        except Exception as e:
            detail_errors.append(f"N연기금Client init 실패: {e}")

    status = "exact" if filtered else "partial"
    return {
        "status": status,
        "manager": "N연기금",
        "manager_id": "nps",
        "filters": {
            "company": company_label or company,
            "ticker": resolved_ticker,
            "nps_code": resolved_nps_code,
            "year": year,
            "start_date": start_date,
            "end_date": end_date,
        },
        "list_source": list_source,
        "list_total_in_window": len(rows or []),
        "matched_count": len(filtered),
        "matched_rows": filtered,
        "details": details,
        "detail_sources": detail_source,
        "detail_errors": detail_errors,
        "ticker_mapping_note": "N연기금 종목코드 5자리 + '0' = KRX 표준 6자리 티커 (검증 100%)",
    }


# ── 메인 진입점 ──


async def build_proxy_guideline_payload(
    scope: str = "policy",
    policy_id: str = "open_proxy",
    manager: str = "",
    company: str = "",
    ticker: str = "",
    nps_code: str = "",
    year: int = 0,
    period: str = "",
    start_date: str = "",
    end_date: str = "",
    agenda_category: str = "",
    agenda_title: str = "",
    agenda_type_raw: str = "",
    compare_policies: list[str] | None = None,
    topic_id: str = "",
    matrix_dimensions: dict[str, int] | None = None,
    auto_score: bool = True,
    meeting_date: str = "",
    notice_disclosure_date: str = "",
    extra_agenda_titles: list[str] | None = None,
    fetch_detail: bool = True,
    force_refresh: bool = False,
    max_details: int = 5,
) -> dict[str, Any]:
    """proxy_guideline tool 메인 진입점."""

    if scope not in _SUPPORTED_SCOPES:
        return ToolEnvelope(
            tool="proxy_guideline",
            status=AnalysisStatus.ERROR,
            warnings=[f"지원하지 않는 scope: {scope}. 가능: {sorted(_SUPPORTED_SCOPES)}"],
            data={"usage": build_usage(0)},
        ).to_dict()

    try:
        if scope == "policy":
            data = scope_policy(policy_id, agenda_category)
        elif scope == "record":
            data = scope_record(manager, company, year, period, agenda_category)
        elif scope == "consensus":
            data = scope_consensus(agenda_category, topic_id)
        elif scope == "compare":
            data = scope_compare(compare_policies or [], agenda_category)
        elif scope == "audit":
            data = scope_audit(manager, agenda_category)
        elif scope == "predict":
            data = await scope_predict(
                company,
                agenda_title,
                agenda_type_raw=agenda_type_raw,
                policy_id=policy_id,
                matrix_dimensions=matrix_dimensions,
                auto_score=auto_score,
                meeting_date=meeting_date,
                notice_disclosure_date=notice_disclosure_date,
                extra_agenda_titles=extra_agenda_titles,
            )
        elif scope == "nps_record":
            data = await scope_nps_record(
                company=company,
                ticker=ticker,
                nps_code=nps_code,
                year=year,
                start_date=start_date,
                end_date=end_date,
                fetch_detail=fetch_detail,
                force_refresh=force_refresh,
                max_details=max_details,
            )
        else:
            data = {"status": "error", "warning": f"unhandled scope: {scope}"}
    except Exception as e:
        return ToolEnvelope(
            tool="proxy_guideline",
            status=AnalysisStatus.ERROR,
            warnings=[f"{type(e).__name__}: {str(e)[:200]}"],
            data={"usage": build_usage(0), "scope": scope},
        ).to_dict()

    status = AnalysisStatus.EXACT
    warnings = []
    if data.get("status") == "error":
        status = AnalysisStatus.ERROR
        warnings.append(data.get("warning", "unknown error"))
    elif data.get("status") == "partial":
        status = AnalysisStatus.PARTIAL

    data["usage"] = build_usage(0)  # DART API 호출 0
    data["scope"] = scope

    subject = ""
    if scope in ("policy", "compare", "predict"):
        subject = policy_id
    elif scope in ("record", "audit"):
        subject = manager
    elif scope == "consensus":
        subject = agenda_category or "all"
    elif scope == "nps_record":
        subject = company or ticker or nps_code or "nps"

    return ToolEnvelope(
        tool="proxy_guideline",
        status=status,
        subject=subject,
        warnings=warnings,
        data=data,
    ).to_dict()
