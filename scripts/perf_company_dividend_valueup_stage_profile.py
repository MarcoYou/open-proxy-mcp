from __future__ import annotations

import argparse
import asyncio
import csv
import json
import statistics
import sys
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Awaitable, Callable

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

import open_proxy_mcp.services.company as company_mod  # noqa: E402
import open_proxy_mcp.services.dividend_v2 as dividend_mod  # noqa: E402
import open_proxy_mcp.services.value_up_v2 as value_up_mod  # noqa: E402
import open_proxy_mcp.services.treasury_share as treasury_mod  # noqa: E402
from open_proxy_mcp.dart.client import DartClient  # noqa: E402
from open_proxy_mcp.services.company import build_company_payload  # noqa: E402
from open_proxy_mcp.services.dividend_v2 import build_dividend_payload  # noqa: E402
from open_proxy_mcp.services.value_up_v2 import build_value_up_payload  # noqa: E402


UNIVERSE_FILES = {
    "kospi50": ROOT / "wiki/architecture/audits/data/260511_perf_company_dividend_valueup_audit/universe_kospi50.csv",
    "kosdaq10": ROOT / "wiki/architecture/audits/data/260511_perf_company_dividend_valueup_audit/universe_kosdaq10.csv",
}


AuditFactory = Callable[[str], Awaitable[dict[str, Any]]]


def _load_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for key in ("kospi50", "kosdaq10"):
        with UNIVERSE_FILES[key].open(encoding="utf-8") as f:
            rows.extend(csv.DictReader(f))
    return rows


class StageProfiler:
    def __init__(self) -> None:
        self._totals: dict[str, float] = {}
        self._counts: dict[str, int] = {}

    def record(self, label: str, elapsed: float) -> None:
        self._totals[label] = self._totals.get(label, 0.0) + elapsed
        self._counts[label] = self._counts.get(label, 0) + 1

    def snapshot(self) -> dict[str, dict[str, float | int]]:
        return {
            label: {
                "total_sec": total,
                "count": self._counts.get(label, 0),
                "avg_sec": total / self._counts[label] if self._counts.get(label) else None,
            }
            for label, total in sorted(self._totals.items())
        }


@contextmanager
def _patch_async_attr(owner: Any, attr: str, label: str, profiler: StageProfiler) -> Any:
    original = getattr(owner, attr)

    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            return await original(*args, **kwargs)
        finally:
            profiler.record(label, time.perf_counter() - started)

    setattr(owner, attr, wrapped)
    try:
        yield
    finally:
        setattr(owner, attr, original)


@contextmanager
def _company_profile_patches(profiler: StageProfiler) -> Any:
    with ExitStack() as stack:
        stack.enter_context(_patch_async_attr(DartClient, "lookup_corp_code_all", "lookup_corp_code_all", profiler))
        stack.enter_context(_patch_async_attr(company_mod, "_safe_company_info", "_safe_company_info", profiler))
        stack.enter_context(_patch_async_attr(company_mod, "_safe_recent_filings", "_safe_recent_filings", profiler))
        yield


@contextmanager
def _dividend_profile_patches(profiler: StageProfiler) -> Any:
    with ExitStack() as stack:
        stack.enter_context(_patch_async_attr(dividend_mod, "resolve_company_query", "resolve_company_query", profiler))
        stack.enter_context(_patch_async_attr(dividend_mod, "_annual_summary", "_annual_summary", profiler))
        stack.enter_context(_patch_async_attr(dividend_mod, "_search_dividend_filings", "_search_dividend_filings", profiler))
        stack.enter_context(_patch_async_attr(dividend_mod, "_decision_details", "_decision_details", profiler))
        stack.enter_context(_patch_async_attr(dividend_mod, "_detect_pre_dividend_post_resolution", "_detect_pre_dividend_post_resolution", profiler))
        stack.enter_context(_patch_async_attr(dividend_mod, "_detect_capital_reserve_reduction", "_detect_capital_reserve_reduction", profiler))
        yield


@contextmanager
def _value_up_profile_patches(profiler: StageProfiler) -> Any:
    with ExitStack() as stack:
        stack.enter_context(_patch_async_attr(value_up_mod, "resolve_company_query", "resolve_company_query", profiler))
        stack.enter_context(_patch_async_attr(value_up_mod, "_search_value_up_items", "_search_value_up_items", profiler))
        stack.enter_context(_patch_async_attr(value_up_mod, "_search_kind_value_up_items", "_search_kind_value_up_items", profiler))
        stack.enter_context(_patch_async_attr(DartClient, "get_document_cached", "get_document_cached", profiler))
        stack.enter_context(_patch_async_attr(DartClient, "kind_fetch_document", "kind_fetch_document", profiler))
        stack.enter_context(_patch_async_attr(treasury_mod, "build_treasury_share_payload", "treasury_cross_ref", profiler))
        yield


def _tool_specs() -> dict[str, tuple[AuditFactory, Callable[[StageProfiler], Any]]]:
    return {
        "company": (
            lambda company: build_company_payload(company),
            _company_profile_patches,
        ),
        "dividend": (
            lambda company: build_dividend_payload(company, scope="summary", year=2025),
            _dividend_profile_patches,
        ),
        "value_up": (
            lambda company: build_value_up_payload(company, scope="summary"),
            _value_up_profile_patches,
        ),
    }


async def _run_one(
    tool: str,
    company: str,
    ticker: str,
    factory: AuditFactory,
    patch_factory: Callable[[StageProfiler], Any],
    timeout_sec: float,
) -> dict[str, Any]:
    profiler = StageProfiler()
    try:
        with patch_factory(profiler):
            started = time.perf_counter()
            payload = await asyncio.wait_for(factory(company), timeout=timeout_sec)
            elapsed = time.perf_counter() - started
    except Exception as exc:
        return {
            "tool": tool,
            "ticker": ticker,
            "company": company,
            "status": "exception",
            "elapsed_sec": None,
            "error": f"{type(exc).__name__}: {exc}",
            "stages": profiler.snapshot(),
        }

    return {
        "tool": tool,
        "ticker": ticker,
        "company": company,
        "status": payload.get("status"),
        "elapsed_sec": elapsed,
        "stages": profiler.snapshot(),
    }


def _percentile(values: list[float], q: float) -> float | None:
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


def _summarize_tool(records: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in records if r.get("status") != "exception"]
    elapsed = [r["elapsed_sec"] for r in ok if isinstance(r.get("elapsed_sec"), (int, float))]
    stage_totals: dict[str, list[float]] = {}
    stage_counts: dict[str, list[int]] = {}
    for record in ok:
        for label, stats in (record.get("stages") or {}).items():
            total_sec = stats.get("total_sec")
            count = stats.get("count")
            if isinstance(total_sec, (int, float)):
                stage_totals.setdefault(label, []).append(float(total_sec))
            if isinstance(count, int):
                stage_counts.setdefault(label, []).append(count)

    stage_summary: list[dict[str, Any]] = []
    for label, totals in stage_totals.items():
        counts = stage_counts.get(label, [])
        stage_summary.append({
            "stage": label,
            "median_total_sec": statistics.median(totals) if totals else None,
            "p95_total_sec": _percentile(totals, 0.95),
            "max_total_sec": max(totals) if totals else None,
            "mean_total_sec": statistics.mean(totals) if totals else None,
            "median_count": statistics.median(counts) if counts else None,
            "max_count": max(counts) if counts else None,
        })
    stage_summary.sort(key=lambda item: item.get("mean_total_sec") or 0.0, reverse=True)

    return {
        "n_total": len(records),
        "n_ok": len(ok),
        "n_exception": len(records) - len(ok),
        "elapsed_sec": {
            "median": statistics.median(elapsed) if elapsed else None,
            "p95": _percentile(elapsed, 0.95),
            "max": max(elapsed) if elapsed else None,
            "mean": statistics.mean(elapsed) if elapsed else None,
        },
        "stage_summary": stage_summary,
    }


async def main(args: argparse.Namespace) -> None:
    rows = _load_rows()
    results: dict[str, list[dict[str, Any]]] = {}
    summary: dict[str, Any] = {}
    specs = _tool_specs()
    started = time.time()

    for tool in ("company", "dividend", "value_up"):
        factory, patch_factory = specs[tool]
        tool_records: list[dict[str, Any]] = []
        for idx, row in enumerate(rows, 1):
            record = await _run_one(tool, row["company"], row["ticker"], factory, patch_factory, args.timeout_sec)
            tool_records.append(record)
            elapsed = record.get("elapsed_sec")
            elapsed_txt = f"{elapsed:.3f}s" if isinstance(elapsed, (int, float)) else "-"
            hottest = None
            hottest_sec = -1.0
            for label, stats in (record.get("stages") or {}).items():
                total_sec = stats.get("total_sec")
                if isinstance(total_sec, (int, float)) and total_sec > hottest_sec:
                    hottest = label
                    hottest_sec = float(total_sec)
            hottest_txt = f"{hottest}={hottest_sec:.3f}s" if hottest else "-"
            print(
                f"[{tool} {idx}/{len(rows)}] {row['ticker']} {row['company']} "
                f"status={record.get('status')} elapsed={elapsed_txt} hot={hottest_txt}",
                flush=True,
            )
        results[tool] = tool_records
        summary[tool] = _summarize_tool(tool_records)

    payload = {
        "meta": {
            "sample": "KOSPI 50 + KOSDAQ 10",
            "sample_size": len(rows),
            "timeout_sec": args.timeout_sec,
            "duration_sec": round(time.time() - started, 2),
        },
        "summary": summary,
        "records": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"# wrote {args.output}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "wiki/architecture/audits/data/260511_perf_company_dividend_valueup_audit/stage_profile_kospi50_kosdaq10.json",
    )
    parser.add_argument("--timeout-sec", type=float, default=120.0)
    parsed = parser.parse_args()
    asyncio.run(main(parsed))
