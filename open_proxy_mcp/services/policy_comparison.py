"""policy_comparison — 7 운용사 + NPS 행사내역 vs 우리 결정 비교.

proxy_advise_before_meeting의 policy_basis scope 백엔드.
spec: [[wiki/tools/proxy_advise_before_meeting]] policy_basis scope.

데이터: data/asset_managers/records/{manager_id}_{period}.json
- 22 파일 (a_activist/b_foreign/c_activist/k_legacy + 기타 × 2024/2025/2026)
- 각 파일: {manager_id, manager_name, period_label, votes: [{company, agenda_title, agenda_category, decision}]}

매칭:
- 회사명: records의 votes[i].company == 우리 corp_name 정확 매칭
- 안건: agenda_category 매칭 (records와 OPM 동일 카테고리 사용)
"""

from __future__ import annotations

import json
from collections import Counter
from importlib.resources import files
from typing import Any

# ── module-level cache (process lifetime) ──
_RECORDS_CACHE: dict[str, dict] | None = None


def _load_all_records() -> dict[str, dict]:
    """22 records 파일 일괄 read + cache."""
    global _RECORDS_CACHE
    if _RECORDS_CACHE is not None:
        return _RECORDS_CACHE
    records_root = files("open_proxy_mcp.data.asset_managers") / "records"
    out: dict[str, dict] = {}
    for path in records_root.iterdir():
        name = path.name
        if not name.endswith(".json"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            stem = name[:-5]  # strip ".json"
            out[stem] = data
        except Exception:
            continue
    _RECORDS_CACHE = out
    return out


def clear_records_cache() -> None:
    """test/diagnostic 용 cache reset"""
    global _RECORDS_CACHE
    _RECORDS_CACHE = None


def build_policy_comparison(
    corp_name: str,
    agenda_decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    """우리 결정 vs 7 운용사 + NPS history 비교.

    Args:
        corp_name: 회사 정식명 (DART corp_name)
        agenda_decisions: proxy_advise.decisions scope의 agenda_decisions
                          [{agenda_title, agenda_category, decision, ...}, ...]

    Returns:
        {
          "corp_name": ...,
          "managers_with_data": N,
          "comparison": [
            {"agenda_category": "director_election",
             "our_decision": "FOR",
             "manager_decisions": [{manager_id, manager_name, decision, vote_count, period_label}, ...],
             "manager_count": 7,
             "majority_decision": "FOR",
             "match_majority": true,
             "majority_strength": "5/7"}
          ]
        }
    """
    records = _load_all_records()

    # 회사명 매칭 — records의 votes에서 corp_name 정확 매치
    # 한 운용사가 여러 연도 있으면 모두 포함 (history 누적)
    by_manager: dict[str, dict[str, Any]] = {}  # manager_id (without period) → aggregated
    for record_id, data in records.items():
        # record_id 형식: "{manager_id}_{period}" (예: "k_legacy_2026-04")
        manager_id = "_".join(record_id.split("_")[:-1]) if "_" in record_id else record_id
        votes = [v for v in (data.get("votes") or []) if v.get("company") == corp_name]
        if not votes:
            continue
        if manager_id not in by_manager:
            by_manager[manager_id] = {
                "manager_name": data.get("manager_name"),
                "periods": [],
                "all_votes": [],
            }
        by_manager[manager_id]["periods"].append(data.get("period_label"))
        by_manager[manager_id]["all_votes"].extend(votes)

    # 안건 카테고리별 비교
    our_categories: dict[str, str] = {}
    for ad in agenda_decisions:
        cat = ad.get("agenda_category")
        dec = ad.get("decision", "").upper()
        if cat:
            our_categories[cat] = dec

    comparison: list[dict[str, Any]] = []
    for category, our_decision in our_categories.items():
        manager_decisions = []
        for manager_id, info in by_manager.items():
            cat_votes = [v for v in info["all_votes"] if v.get("agenda_category") == category]
            if not cat_votes:
                continue
            # 운용사의 카테고리 결정 majority (여러 연도/안건)
            decs = [v.get("decision", "").upper() for v in cat_votes]
            dc = Counter(decs)
            most = dc.most_common(1)[0][0] if dc else None
            manager_decisions.append({
                "manager_id": manager_id,
                "manager_name": info["manager_name"],
                "decision": most,
                "vote_count": len(decs),
                "periods": info["periods"],
            })

        # 7 운용사 majority 계산
        if manager_decisions:
            dc = Counter(d["decision"] for d in manager_decisions if d.get("decision"))
            top = dc.most_common(1)[0] if dc else (None, 0)
            majority_decision = top[0]
            majority_count = top[1]
            total = sum(dc.values())
            comparison.append({
                "agenda_category": category,
                "our_decision": our_decision,
                "manager_decisions": manager_decisions,
                "manager_count": len(manager_decisions),
                "majority_decision": majority_decision,
                "majority_strength": f"{majority_count}/{total}" if total else "0/0",
                "match_majority": our_decision == majority_decision if majority_decision else None,
            })
        else:
            comparison.append({
                "agenda_category": category,
                "our_decision": our_decision,
                "manager_decisions": [],
                "manager_count": 0,
                "majority_decision": None,
                "majority_strength": "0/0",
                "match_majority": None,
            })

    return {
        "corp_name": corp_name,
        "managers_with_data": len(by_manager),
        "managers_searched": len({mid for record_id in records.keys() for mid in ["_".join(record_id.split("_")[:-1])]}),
        "comparison": comparison,
    }
