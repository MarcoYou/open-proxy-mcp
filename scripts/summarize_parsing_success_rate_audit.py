"""Summarize parsing success-rate audit JSON into compact stats.

Usage:
    uv run python scripts/summarize_parsing_success_rate_audit.py \
      --input wiki/architecture/audits/data/260517_parsing_success_rate_audit/baseline_company_sample_450.json
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

TOOL_TO_FAMILY = {
    "company": "회사 식별 계열",
    "shareholder_meeting_results": "주총 결과 계열",
    "ownership_structure": "지분 구조 계열",
    "financial_metrics": "재무 집계 계열",
    "corp_gov_report": "지배구조보고서 계열",
    "dividend": "배당 / 자사주 / 밸류업 계열",
    "treasury_share": "배당 / 자사주 / 밸류업 계열",
    "value_up": "배당 / 자사주 / 밸류업 계열",
    "corporate_restructuring": "DS005 이벤트 계열",
    "dilutive_issuance": "DS005 이벤트 계열",
    "proxy_contest": "분쟁 / 내부거래 계열",
    "related_party_transaction": "분쟁 / 내부거래 계열",
}


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[94]


def _top_status_samples(records: list[dict[str, Any]], status_set: set[str], limit: int = 10) -> list[dict[str, Any]]:
    picked = []
    for r in records:
        if r.get("status") in status_set or r.get("audit_class") in status_set:
            picked.append(
                {
                    "tool": r.get("tool"),
                    "ticker": r.get("ticker"),
                    "company": r.get("company"),
                    "market": r.get("market"),
                    "status": r.get("status"),
                    "audit_class": r.get("audit_class"),
                    "warning_sample": r.get("warning_sample"),
                    "exception_type": r.get("exception_type"),
                    "exception_message": r.get("exception_message"),
                }
            )
        if len(picked) >= limit:
            break
    return picked


def summarize(data: dict[str, Any]) -> dict[str, Any]:
    records = data["records"]
    by_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_tool: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for r in records:
        by_tool[r["tool"]].append(r)
        by_family[TOOL_TO_FAMILY.get(r["tool"], "기타")].append(r)

    family_summary: dict[str, Any] = {}
    for family, group in sorted(by_family.items()):
        audit_counts = Counter(r["audit_class"] for r in group)
        statuses = Counter(r["status"] for r in group)
        durations = [float(r["duration_ms"]) for r in group]
        family_summary[family] = {
            "total": len(group),
            "status_counts": dict(statuses),
            "audit_counts": dict(audit_counts),
            "strict_success_rate": round(audit_counts.get("success", 0) * 100.0 / len(group), 1) if group else 0.0,
            "usable_rate": round((audit_counts.get("success", 0) + audit_counts.get("soft_fail", 0)) * 100.0 / len(group), 1) if group else 0.0,
            "hard_fail_rate": round(audit_counts.get("hard_fail", 0) * 100.0 / len(group), 1) if group else 0.0,
            "median_ms": round(statistics.median(durations), 1) if durations else 0.0,
            "p95_ms": round(_p95(durations), 1) if durations else 0.0,
        }

    tool_failures: dict[str, Any] = {}
    for tool, group in sorted(by_tool.items()):
        hard = [r for r in group if r["audit_class"] == "hard_fail"]
        soft = [r for r in group if r["audit_class"] == "soft_fail"]
        if hard or soft:
            tool_failures[tool] = {
                "hard_fail_examples": _top_status_samples(hard, {"hard_fail"}, limit=10),
                "soft_fail_examples": _top_status_samples(soft, {"soft_fail"}, limit=10),
            }

    return {
        "meta": data.get("meta", {}),
        "summary": data.get("summary", {}),
        "family_summary": family_summary,
        "tool_failures": tool_failures,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    input_path = Path(args.input)
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    result = summarize(payload)

    if args.output:
        out = Path(args.output)
    else:
        out = input_path.with_name(input_path.stem + "_summary.json")
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"[summary] wrote {out}")


if __name__ == "__main__":
    main()
