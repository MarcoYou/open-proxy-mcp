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
from open_proxy_mcp.services.treasury_share import build_treasury_share_payload  # noqa: E402


AuditFactory = Callable[[str], Awaitable[dict[str, Any]]]


def _load_rows(paths: list[Path]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in paths:
        with path.open(encoding="utf-8") as f:
            rows.extend(csv.DictReader(f))
    return rows


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


def _tool_factories() -> dict[str, AuditFactory]:
    return {
        "company": lambda company: build_company_payload(company),
        "dividend": lambda company: build_dividend_payload(company, scope="summary", year=2025),
        "treasury_share": lambda company: build_treasury_share_payload(company, scope="summary", lookback_months=24),
    }


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
        summary["recent_filings_count"] = len(data.get("recent_filings") or [])
    elif tool == "dividend":
        summary["latest_decisions_count"] = len(data.get("latest_decisions") or [])
        summary["history_count"] = len(data.get("history") or [])
    elif tool == "treasury_share":
        tool_summary = data.get("summary") or {}
        summary["total_event_count"] = tool_summary.get("total_event_count")
        summary["cancelation_count"] = tool_summary.get("cancelation_count")
    return summary


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
    return {
        "tool": tool,
        "ticker": ticker,
        "company": company,
        "status": payload.get("status"),
        "elapsed_sec": elapsed_sec,
        **_summarize_payload(tool, payload),
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
            "median": statistics.median(elapsed) if elapsed else None,
            "p95": _pct(elapsed, 0.95),
            "max": max(elapsed) if elapsed else None,
            "mean": statistics.mean(elapsed) if elapsed else None,
        },
        "api_calls": {
            "sum": sum(api_calls) if api_calls else None,
            "median": statistics.median(api_calls) if api_calls else None,
            "p95": _pct(api_calls, 0.95),
            "max": max(api_calls) if api_calls else None,
            "mean": statistics.mean(api_calls) if api_calls else None,
        },
        "warning_count": {
            "median": statistics.median(warning_counts) if warning_counts else None,
            "max": max(warning_counts) if warning_counts else None,
        },
        "slowest_examples": [
            {
                "ticker": r["ticker"],
                "company": r["company"],
                "status": r.get("status"),
                "elapsed_sec": r.get("elapsed_sec"),
                "api_calls": r.get("api_calls"),
            }
            for r in sorted(ok, key=lambda row: row.get("elapsed_sec") or -1, reverse=True)[:8]
        ],
    }


async def _run_tool(
    tool: str,
    rows: list[dict[str, str]],
    timeout_sec: float,
    batch_size: int,
    batch_sleep_sec: float,
    per_item_sleep_sec: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    factory = _tool_factories()[tool]
    results: list[dict[str, Any]] = []
    batch_meta: list[dict[str, Any]] = []

    for start in range(0, len(rows), batch_size):
        batch_rows = rows[start:start + batch_size]
        batch_api_calls = 0
        batch_started = time.time()
        for offset, row in enumerate(batch_rows, 1):
            idx = start + offset
            result = await _run_one(tool, row["company"], row["ticker"], factory, timeout_sec)
            results.append(result)
            if isinstance(result.get("api_calls"), int):
                batch_api_calls += result["api_calls"]
            elapsed = result.get("elapsed_sec")
            elapsed_txt = f"{elapsed:.3f}s" if isinstance(elapsed, (int, float)) else "-"
            print(
                f"[{tool} {idx}/{len(rows)}] {row['ticker']} {row['company']} "
                f"status={result.get('status')} elapsed={elapsed_txt} api_calls={result.get('api_calls')}",
                flush=True,
            )
            if per_item_sleep_sec > 0 and idx < len(rows):
                await asyncio.sleep(per_item_sleep_sec)
        batch_meta.append(
            {
                "batch_index": start // batch_size + 1,
                "row_count": len(batch_rows),
                "api_calls": batch_api_calls,
                "elapsed_wall_sec": round(time.time() - batch_started, 2),
            }
        )
        if batch_sleep_sec > 0 and start + batch_size < len(rows):
            print(
                f"[{tool}] batch {start // batch_size + 1} complete "
                f"(rows={len(batch_rows)}, api_calls={batch_api_calls}) sleep={batch_sleep_sec}s",
                flush=True,
            )
            await asyncio.sleep(batch_sleep_sec)
    return results, batch_meta


async def main(args: argparse.Namespace) -> None:
    rows = _load_rows(args.universe)
    started = time.time()
    tool_records: dict[str, list[dict[str, Any]]] = {}
    tool_summaries: dict[str, Any] = {}
    tool_batches: dict[str, list[dict[str, Any]]] = {}

    for tool in args.tools:
        records, batch_meta = await _run_tool(
            tool=tool,
            rows=rows,
            timeout_sec=args.timeout_sec,
            batch_size=args.batch_size,
            batch_sleep_sec=args.batch_sleep_sec,
            per_item_sleep_sec=args.per_item_sleep_sec,
        )
        tool_records[tool] = records
        tool_summaries[tool] = _summarize_records(records)
        tool_batches[tool] = batch_meta
        if args.tool_sleep_sec > 0 and tool != args.tools[-1]:
            print(f"[{tool}] tool complete, sleep={args.tool_sleep_sec}s before next tool", flush=True)
            await asyncio.sleep(args.tool_sleep_sec)

    payload = {
        "meta": {
            "sample_files": [str(path) for path in args.universe],
            "sample_size": len(rows),
            "tools": args.tools,
            "timeout_sec": args.timeout_sec,
            "batch_size": args.batch_size,
            "batch_sleep_sec": args.batch_sleep_sec,
            "per_item_sleep_sec": args.per_item_sleep_sec,
            "tool_sleep_sec": args.tool_sleep_sec,
            "duration_sec": round(time.time() - started, 2),
        },
        "summary": tool_summaries,
        "batches": tool_batches,
        "records": tool_records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"# wrote {args.output}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--universe",
        nargs="+",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "--tools",
        nargs="+",
        default=["company", "dividend", "treasury_share"],
        choices=["company", "dividend", "treasury_share"],
    )
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
    )
    parser.add_argument("--timeout-sec", type=float, default=180.0)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--batch-sleep-sec", type=float, default=20.0)
    parser.add_argument("--per-item-sleep-sec", type=float, default=0.5)
    parser.add_argument("--tool-sleep-sec", type=float, default=30.0)
    asyncio.run(main(parser.parse_args()))
