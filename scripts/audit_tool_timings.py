#!/usr/bin/env python3
"""Run a small timing audit across common OPM tools."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
import time
from typing import Any, Awaitable, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from open_proxy_mcp.services.company import build_company_payload  # noqa: E402
from open_proxy_mcp.services.corp_gov_report import build_corp_gov_report_payload  # noqa: E402
from open_proxy_mcp.services.dividend_v2 import build_dividend_payload  # noqa: E402
from open_proxy_mcp.services.financial_metrics import build_financial_metrics_payload  # noqa: E402
from open_proxy_mcp.services.ownership_structure import build_ownership_structure_payload  # noqa: E402
from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload  # noqa: E402
from open_proxy_mcp.services.treasury_share import build_treasury_share_payload  # noqa: E402
from open_proxy_mcp.services.value_up_v2 import build_value_up_payload  # noqa: E402


ToolFactory = Callable[[str], Awaitable[dict[str, Any]]]


TOOLS: dict[str, ToolFactory] = {
    "company": lambda company: build_company_payload(company, max_recent_filings=3),
    "shareholder_meeting_notice": lambda company: build_shareholder_meeting_payload(
        company, year=2026, meeting_type="annual", scope="summary"
    ),
    "financial_metrics": lambda company: build_financial_metrics_payload(
        company, scope="summary", year=2024
    ),
    "ownership_structure": lambda company: build_ownership_structure_payload(
        company, scope="summary", year=2024
    ),
    "dividend": lambda company: build_dividend_payload(company, scope="summary", year=2024),
    "treasury_share": lambda company: build_treasury_share_payload(
        company, scope="summary", lookback_months=24
    ),
    "value_up": lambda company: build_value_up_payload(company, scope="summary"),
    "corp_gov_report": lambda company: build_corp_gov_report_payload(
        company, scope="summary"
    ),
}


def _top_timing(timings: dict[str, Any]) -> tuple[str, int]:
    numeric = {
        str(key): int(value)
        for key, value in timings.items()
        if isinstance(value, (int, float)) and key != "total"
    }
    if not numeric:
        return "", 0
    return max(numeric.items(), key=lambda item: item[1])


async def _run_one(tool: str, company: str, factory: ToolFactory, timeout_sec: float) -> dict[str, Any]:
    started_at = time.perf_counter()
    try:
        payload = await asyncio.wait_for(factory(company), timeout=timeout_sec)
        elapsed_ms = int((time.perf_counter() - started_at) * 1000)
    except Exception as exc:
        return {
            "company": company,
            "tool": tool,
            "status": "error",
            "elapsed_ms": int((time.perf_counter() - started_at) * 1000),
            "error": f"{type(exc).__name__}: {exc}",
        }

    data = payload.get("data") or {}
    timings = data.get("timings_ms") or {}
    top_stage, top_stage_ms = _top_timing(timings)
    return {
        "company": company,
        "tool": tool,
        "status": payload.get("status"),
        "elapsed_ms": elapsed_ms,
        "response_total_ms": timings.get("total"),
        "top_stage": top_stage,
        "top_stage_ms": top_stage_ms,
        "dart_api_calls": (data.get("usage") or {}).get("dart_api_calls"),
        "warnings_count": len(payload.get("warnings") or []),
        "timings_ms": timings,
    }


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--companies",
        default="LG화학,삼성전자,KT&G",
        help="Comma-separated company names.",
    )
    parser.add_argument("--timeout-sec", type=float, default=45.0)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    companies = [item.strip() for item in args.companies.split(",") if item.strip()]
    rows: list[dict[str, Any]] = []
    for company in companies:
        for tool, factory in TOOLS.items():
            row = await _run_one(tool, company, factory, args.timeout_sec)
            rows.append(row)
            print(
                f"{company}\t{tool}\t{row['status']}\t"
                f"elapsed={row['elapsed_ms']}ms\ttop={row.get('top_stage', '')}:{row.get('top_stage_ms', 0)}ms"
            )

    summary = {
        "companies": companies,
        "tool_count": len(TOOLS),
        "rows": rows,
        "slowest": sorted(rows, key=lambda row: row.get("elapsed_ms", 0), reverse=True)[:10],
    }

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {out_path}")
    else:
        print(json.dumps(summary["slowest"], ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
