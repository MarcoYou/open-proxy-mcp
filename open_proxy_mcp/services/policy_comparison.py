"""policy_comparison — 7 운용사 + NPS 행사내역에서 모범 사례 + 특이 케이스 추출.

proxy_advise_before_meeting의 policy_basis scope 백엔드.
spec: [[wiki/tools/proxy_advise_before_meeting]] policy_basis scope.

목적 (코붕이 명시):
- "개별 운용사 결정 list가 중요한 게 아니다"
- 여러 운용사 합쳐서 → **모범 사례 (consensus + 그 reason)**
- "어떤 곳은 이런 근거로 이런 결정도 했더라" → **특이 케이스 + 그 근거 인용**

데이터: data/asset_managers/records/{manager_id}_{period}.json
- 22 파일 (a_activist/b_foreign/c_activist/k_legacy + 기타 × 2024/2025/2026)
- 각 vote: {company, agenda_title, agenda_category, decision, reason}

매칭:
- 회사명: records의 votes[i].company == 우리 corp_name 정확 매치
- 안건: agenda_category 매칭 (records와 OPM 동일 카테고리)
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
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
            stem = name[:-5]
            out[stem] = data
        except Exception:
            continue
    _RECORDS_CACHE = out
    return out


def clear_records_cache() -> None:
    """test/diagnostic 용 cache reset"""
    global _RECORDS_CACHE
    _RECORDS_CACHE = None


def _split_manager_period(record_id: str) -> tuple[str, str]:
    """record_id (예: 'k_legacy_2026-04') → (manager_id, period)"""
    parts = record_id.rsplit("_", 1)
    if len(parts) == 2:
        return parts[0], parts[1]
    return record_id, ""


def build_policy_comparison(
    corp_name: str,
    agenda_decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    """우리 결정 vs 7 운용사 + NPS — 모범 사례 + 특이 케이스 example 형태.

    Args:
        corp_name: 회사 정식명 (DART corp_name)
        agenda_decisions: proxy_advise.decisions scope의 agenda_decisions

    Returns:
        {
          "corp_name": ...,
          "managers_with_data": N,
          "managers_searched": M,
          "examples": [
            {
              "agenda_category": "director_election",
              "our_decision": "FOR",
              "vote_distribution": {"FOR": 5, "AGAINST": 2},
              "consensus": {
                "decision": "FOR",
                "strength": "5/7",
                "sample_reasons": [
                  "후보자는 결격사유를 발견하지 못해 찬성 의견임",
                  "독립성 요건 충족 + 5년 룰 미저촉..."
                ]
              },
              "outliers": [
                {
                  "manager_name": "행동주의 X",
                  "decision": "AGAINST",
                  "reason": "회사 직원 출신으로 독립성 의문...",
                  "period_label": "2026-04"
                }
              ],
              "our_alignment": "follows_consensus" | "aligns_with_outlier" | "unique"
            }
          ]
        }
    """
    records = _load_all_records()

    # 회사명 매칭 — 모든 records의 votes에서 corp_name 정확 매치
    relevant_votes: list[dict[str, Any]] = []
    managers_searched: set[str] = set()
    for record_id, data in records.items():
        manager_id, period = _split_manager_period(record_id)
        managers_searched.add(manager_id)
        for v in (data.get("votes") or []):
            if v.get("company") == corp_name:
                relevant_votes.append({
                    **v,
                    "manager_id": manager_id,
                    "manager_name": data.get("manager_name"),
                    "period_label": data.get("period_label") or period,
                })

    managers_with_data = {v["manager_id"] for v in relevant_votes}

    # 우리 결정 카테고리별 매핑
    our_categories: dict[str, str] = {}
    for ad in agenda_decisions:
        cat = ad.get("agenda_category")
        dec = (ad.get("decision") or "").upper()
        if cat and cat not in our_categories:
            our_categories[cat] = dec

    # 카테고리별 group + 모범 사례 + 특이 케이스 추출
    examples: list[dict[str, Any]] = []
    for category, our_decision in our_categories.items():
        cat_votes = [v for v in relevant_votes if v.get("agenda_category") == category]
        if not cat_votes:
            examples.append({
                "agenda_category": category,
                "our_decision": our_decision,
                "vote_distribution": {},
                "consensus": None,
                "outliers": [],
                "our_alignment": "no_data",
            })
            continue

        # decision 분포 (모든 운용사 × 모든 안건)
        decisions_upper = [(v.get("decision") or "").upper() for v in cat_votes if v.get("decision")]
        dc = Counter(decisions_upper)
        total = sum(dc.values())
        most_common, most_count = dc.most_common(1)[0] if dc else (None, 0)

        # consensus reasons (다수 결정의 reason 발췌, 의미 있는 텍스트만)
        consensus_reasons: list[str] = []
        seen_reason_prefixes: set[str] = set()
        for v in cat_votes:
            if (v.get("decision") or "").upper() != most_common:
                continue
            reason = (v.get("reason") or "").strip()
            if not reason or len(reason) < 15:
                continue
            # dedupe (앞 30자 prefix로)
            prefix = reason[:30]
            if prefix in seen_reason_prefixes:
                continue
            seen_reason_prefixes.add(prefix)
            consensus_reasons.append(reason[:300])  # 300자 cap
            if len(consensus_reasons) >= 3:
                break

        # outliers (소수 결정 + 그 reason 인용)
        outliers: list[dict[str, Any]] = []
        seen_outlier_prefixes: set[str] = set()
        for v in cat_votes:
            dec = (v.get("decision") or "").upper()
            if dec == most_common or not dec:
                continue
            reason = (v.get("reason") or "").strip()
            if not reason or len(reason) < 15:
                continue
            prefix = reason[:30]
            if prefix in seen_outlier_prefixes:
                continue
            seen_outlier_prefixes.add(prefix)
            outliers.append({
                "manager_name": v.get("manager_name"),
                "decision": dec,
                "reason": reason[:300],
                "period_label": v.get("period_label"),
                "agenda_title_sample": (v.get("agenda_title") or "")[:80],
            })
            if len(outliers) >= 3:
                break

        # our_alignment 판단
        if not most_common:
            alignment = "no_data"
        elif our_decision == most_common:
            alignment = "follows_consensus"
        elif outliers and any(o["decision"] == our_decision for o in outliers):
            alignment = "aligns_with_outlier"
        else:
            alignment = "unique"

        examples.append({
            "agenda_category": category,
            "our_decision": our_decision,
            "vote_distribution": dict(dc),
            "consensus": {
                "decision": most_common,
                "strength": f"{most_count}/{total}",
                "sample_reasons": consensus_reasons,
            } if most_common else None,
            "outliers": outliers,
            "our_alignment": alignment,
        })

    return {
        "corp_name": corp_name,
        "managers_with_data": len(managers_with_data),
        "managers_searched": len(managers_searched),
        "examples": examples,
    }
