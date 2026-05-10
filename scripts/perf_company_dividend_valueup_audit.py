from __future__ import annotations

import argparse
import asyncio
import csv
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Awaitable, Callable

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

from open_proxy_mcp.services.company import build_company_payload  # noqa: E402
from open_proxy_mcp.services.dividend_v2 import build_dividend_payload  # noqa: E402
from open_proxy_mcp.services.value_up_v2 import build_value_up_payload  # noqa: E402


AuditFactory = Callable[[str], Awaitable[dict[str, Any]]]


UNIVERSE_FILES = {
    "kospi50": ROOT / "wiki/architecture/audits/data/260511_perf_company_dividend_valueup_audit/universe_kospi50.csv",
    "kosdaq10": ROOT / "wiki/architecture/audits/data/260511_perf_company_dividend_valueup_audit/universe_kosdaq10.csv",
}


def _load_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip():
        digits = value.replace(",", "").strip()
        if digits.lstrip("-").isdigit():
            return int(digits)
    return None


def _summarize_payload(tool: str, payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data") or {}
    usage = data.get("usage") or {}
    summary: dict[str, Any] = {
        "status": payload.get("status"),
        "warning_count": len(payload.get("warnings") or []),
        "api_calls": _safe_int(usage.get("api_calls")),
        "filing_count": data.get("filing_count"),
        "parsing_failures": data.get("parsing_failures"),
    }
    if tool == "company":
        recent_filings = data.get("recent_filings") or []
        summary.update({
            "candidate_count": len(data.get("candidates") or []),
            "recent_filings_count": len(recent_filings),
            "market": ((data.get("classification") or {}).get("market")),
        })
    elif tool == "dividend":
        latest_decisions = data.get("latest_decisions") or []
        summary.update({
            "latest_decisions_count": len(latest_decisions),
            "history_count": len(data.get("history") or []),
            "quarterly_breakdown_count": len(data.get("quarterly_breakdown") or []),
            "has_summary": bool(data.get("summary")),
        })
    elif tool == "value_up":
        items = data.get("items") or []
        summary.update({
            "availability_status": data.get("availability_status"),
            "item_count": len(items),
            "highlight_count": len(data.get("highlights") or []),
            "highlight_source_text_length": data.get("highlight_source_text_length"),
            "primary_source": data.get("primary_source"),
        })
    return summary


def _tool_factories() -> dict[str, AuditFactory]:
    return {
        "company": lambda company: build_company_payload(company),
        "dividend": lambda company: build_dividend_payload(company, scope="summary", year=2025),
        "value_up": lambda company: build_value_up_payload(company, scope="summary"),
    }


async def _time_call(factory: AuditFactory, company: str) -> tuple[float, dict[str, Any]]:
    started = time.perf_counter()
    payload = await factory(company)
    return time.perf_counter() - started, payload


async def _run_one(tool: str, company: str, ticker: str, factory: AuditFactory, timeout_sec: float) -> dict[str, Any]:
    try:
        elapsed_sec, payload = await asyncio.wait_for(_time_call(factory, company), timeout=timeout_sec)
    except Exception as exc:
        return {
            "tool": tool,
            "ticker": ticker,
            "company": company,
            "status": "exception",
            "elapsed_sec": None,
            "warning_count": None,
            "api_calls": None,
            "error": f"{type(exc).__name__}: {exc}",
        }

    summary = _summarize_payload(tool, payload)
    return {
        "tool": tool,
        "ticker": ticker,
        "company": company,
        "status": payload.get("status"),
        "elapsed_sec": elapsed_sec,
        **summary,
    }


def _pct(values: list[float], q: float) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    ordered = sorted(values)
    idx = (len(ordered) - 1) * q
    lower = int(idx)
    upper = min(lower + 1, len(ordered) - 1)
    if lower == upper:
        return ordered[lower]
    frac = idx - lower
    return ordered[lower] * (1 - frac) + ordered[upper] * frac


def _summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in records if r.get("status") != "exception"]
    elapsed = [r["elapsed_sec"] for r in ok if isinstance(r.get("elapsed_sec"), (int, float))]
    api_calls = [r["api_calls"] for r in ok if isinstance(r.get("api_calls"), int)]
    warning_counts = [r["warning_count"] for r in ok if isinstance(r.get("warning_count"), int)]
    status_counts = Counter(r.get("status") for r in ok)
    return {
        "n_total": len(records),
        "n_ok": len(ok),
        "n_exception": len(records) - len(ok),
        "status_counts": dict(sorted(status_counts.items())),
        "elapsed_sec": {
            "min": min(elapsed) if elapsed else None,
            "median": statistics.median(elapsed) if elapsed else None,
            "p95": _pct(elapsed, 0.95),
            "max": max(elapsed) if elapsed else None,
            "mean": statistics.mean(elapsed) if elapsed else None,
        },
        "api_calls": {
            "median": statistics.median(api_calls) if api_calls else None,
            "p95": _pct(api_calls, 0.95),
            "max": max(api_calls) if api_calls else None,
            "mean": statistics.mean(api_calls) if api_calls else None,
        },
        "warning_count": {
            "median": statistics.median(warning_counts) if warning_counts else None,
            "max": max(warning_counts) if warning_counts else None,
            "mean": statistics.mean(warning_counts) if warning_counts else None,
        },
        "slowest_examples": [
            {
                "ticker": r["ticker"],
                "company": r["company"],
                "status": r.get("status"),
                "elapsed_sec": r.get("elapsed_sec"),
            }
            for r in sorted(ok, key=lambda row: row.get("elapsed_sec") or -1, reverse=True)[:5]
        ],
    }


async def _run_tool(tool: str, rows: list[dict[str, str]], timeout_sec: float) -> list[dict[str, Any]]:
    factory = _tool_factories()[tool]
    results: list[dict[str, Any]] = []
    for idx, row in enumerate(rows, 1):
        result = await _run_one(tool, row["company"], row["ticker"], factory, timeout_sec)
        results.append(result)
        elapsed = result.get("elapsed_sec")
        elapsed_txt = f"{elapsed:.3f}s" if isinstance(elapsed, (int, float)) else "-"
        print(
            f"[{tool} {idx}/{len(rows)}] {row['ticker']} {row['company']} "
            f"status={result.get('status')} elapsed={elapsed_txt} api_calls={result.get('api_calls')}",
            flush=True,
        )
    return results


async def main(args: argparse.Namespace) -> None:
    rows = _load_rows(UNIVERSE_FILES["kospi50"]) + _load_rows(UNIVERSE_FILES["kosdaq10"])
    started = time.time()
    tool_records: dict[str, list[dict[str, Any]]] = {}
    tool_summaries: dict[str, Any] = {}

    for tool in ("company", "dividend", "value_up"):
        records = await _run_tool(tool, rows, args.timeout_sec)
        tool_records[tool] = records
        tool_summaries[tool] = _summarize_records(records)

    payload = {
        "meta": {
            "sample": "KOSPI 50 + KOSDAQ 10",
            "sample_size": len(rows),
            "timeout_sec": args.timeout_sec,
            "duration_sec": round(time.time() - started, 2),
        },
        "summary": tool_summaries,
        "records": tool_records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"# wrote {args.output}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "wiki/architecture/audits/data/260511_perf_company_dividend_valueup_audit/baseline_kospi50_kosdaq10.json",
    )
    parser.add_argument("--timeout-sec", type=float, default=120.0)
    parsed = parser.parse_args()
    asyncio.run(main(parsed))
