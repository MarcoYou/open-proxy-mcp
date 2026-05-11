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
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

import open_proxy_mcp.services.filing_search as filing_search_mod  # noqa: E402
import open_proxy_mcp.services.treasury_share as treasury_mod  # noqa: E402
from open_proxy_mcp.dart.client import DartClient  # noqa: E402
from open_proxy_mcp.services.treasury_share import build_treasury_share_payload  # noqa: E402


UNIVERSE_FILES = {
    "kospi50": ROOT / "wiki/architecture/audits/data/260511_perf_company_dividend_valueup_audit/universe_kospi50.csv",
    "kosdaq10": ROOT / "wiki/architecture/audits/data/260511_perf_company_dividend_valueup_audit/universe_kosdaq10.csv",
}


class StageProfiler:
    def __init__(self) -> None:
        self._totals: dict[str, float] = {}
        self._counts: dict[str, int] = {}

    def record(self, label: str, elapsed: float) -> None:
        self._totals[label] = self._totals.get(label, 0.0) + elapsed
        self._counts[label] = self._counts.get(label, 0) + 1

    def snapshot(self) -> dict[str, dict[str, float | int | None]]:
        return {
            label: {
                "total_sec": total,
                "count": self._counts.get(label, 0),
                "avg_sec": total / self._counts[label] if self._counts.get(label) else None,
            }
            for label, total in sorted(self._totals.items())
        }


def _load_rows() -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for key in ("kospi50", "kosdaq10"):
        with UNIVERSE_FILES[key].open(encoding="utf-8") as f:
            rows.extend(csv.DictReader(f))
    return rows


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
def _patch_sync_attr(owner: Any, attr: str, label: str, profiler: StageProfiler) -> Any:
    original = getattr(owner, attr)

    def wrapped(*args: Any, **kwargs: Any) -> Any:
        started = time.perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            profiler.record(label, time.perf_counter() - started)

    setattr(owner, attr, wrapped)
    try:
        yield
    finally:
        setattr(owner, attr, original)


@contextmanager
def _patch_title_scan_fetch(profiler: StageProfiler) -> Any:
    original = filing_search_mod.fetch_filings_for_title_scan

    async def wrapped(*args: Any, **kwargs: Any) -> Any:
        pblntf_tys = kwargs.get("pblntf_tys")
        if pblntf_tys == "":
            label = "fetch_execution_report_scan"
        elif pblntf_tys == ("B", "I"):
            label = "fetch_cancelation_scan"
        else:
            label = "fetch_filings_for_title_scan"
        started = time.perf_counter()
        try:
            return await original(*args, **kwargs)
        finally:
            profiler.record(label, time.perf_counter() - started)

    filing_search_mod.fetch_filings_for_title_scan = wrapped
    treasury_mod.fetch_filings_for_title_scan = wrapped
    try:
        yield
    finally:
        filing_search_mod.fetch_filings_for_title_scan = original
        treasury_mod.fetch_filings_for_title_scan = original


@contextmanager
def _treasury_profile_patches(profiler: StageProfiler) -> Any:
    with ExitStack() as stack:
        stack.enter_context(_patch_async_attr(treasury_mod, "resolve_company_query", "resolve_company_query", profiler))
        stack.enter_context(_patch_async_attr(treasury_mod, "_fetch_decisions", "_fetch_decisions", profiler))
        stack.enter_context(_patch_async_attr(treasury_mod, "_enrich_cancelation_with_body", "_enrich_cancelation_with_body", profiler))
        stack.enter_context(_patch_async_attr(treasury_mod, "_enrich_result_reports_with_body", "_enrich_result_reports_with_body", profiler))
        stack.enter_context(_patch_sync_attr(treasury_mod, "_link_cycles", "_link_cycles", profiler))
        stack.enter_context(_patch_sync_attr(treasury_mod, "_combined_events", "_combined_events", profiler))
        stack.enter_context(_patch_sync_attr(treasury_mod, "_summary_counts", "_summary_counts", profiler))
        stack.enter_context(_patch_async_attr(DartClient, "get_treasury_acquisition", "get_treasury_acquisition", profiler))
        stack.enter_context(_patch_async_attr(DartClient, "get_treasury_disposal", "get_treasury_disposal", profiler))
        stack.enter_context(_patch_async_attr(DartClient, "get_treasury_trust_contract", "get_treasury_trust_contract", profiler))
        stack.enter_context(_patch_async_attr(DartClient, "get_treasury_trust_termination", "get_treasury_trust_termination", profiler))
        stack.enter_context(_patch_async_attr(DartClient, "get_document_cached", "get_document_cached", profiler))
        stack.enter_context(_patch_title_scan_fetch(profiler))
        yield


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


def _summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in records if r.get("status") != "exception"]
    elapsed = [r["elapsed_sec"] for r in ok if isinstance(r.get("elapsed_sec"), (int, float))]
    status_counts: dict[str, int] = {}
    stage_totals: dict[str, list[float]] = {}
    stage_counts: dict[str, list[int]] = {}

    for record in records:
        status = record.get("status") or "unknown"
        status_counts[status] = status_counts.get(status, 0) + 1

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

    slowest = sorted(
        [record for record in ok if isinstance(record.get("elapsed_sec"), (int, float))],
        key=lambda item: item["elapsed_sec"],
        reverse=True,
    )[:10]

    return {
        "n_total": len(records),
        "n_ok": len(ok),
        "n_exception": len(records) - len(ok),
        "status_counts": status_counts,
        "elapsed_sec": {
            "median": statistics.median(elapsed) if elapsed else None,
            "p95": _percentile(elapsed, 0.95),
            "max": max(elapsed) if elapsed else None,
            "mean": statistics.mean(elapsed) if elapsed else None,
        },
        "stage_summary": stage_summary,
        "slowest": [
            {
                "ticker": record["ticker"],
                "company": record["company"],
                "status": record["status"],
                "elapsed_sec": record["elapsed_sec"],
            }
            for record in slowest
        ],
    }


async def _run_one(company: str, ticker: str, timeout_sec: float) -> dict[str, Any]:
    profiler = StageProfiler()
    try:
        with _treasury_profile_patches(profiler):
            started = time.perf_counter()
            payload = await asyncio.wait_for(
                build_treasury_share_payload(company, scope="summary", lookback_months=24),
                timeout=timeout_sec,
            )
            elapsed = time.perf_counter() - started
    except Exception as exc:
        return {
            "ticker": ticker,
            "company": company,
            "status": "exception",
            "elapsed_sec": None,
            "error": f"{type(exc).__name__}: {exc}",
            "stages": profiler.snapshot(),
        }

    return {
        "ticker": ticker,
        "company": company,
        "status": payload.get("status"),
        "elapsed_sec": elapsed,
        "warnings": payload.get("warnings", []),
        "summary": (payload.get("data") or {}).get("summary", {}),
        "stages": profiler.snapshot(),
    }


async def main(args: argparse.Namespace) -> None:
    rows = _load_rows()
    records: list[dict[str, Any]] = []
    started = time.time()
    for idx, row in enumerate(rows, 1):
        record = await _run_one(row["company"], row["ticker"], args.timeout_sec)
        records.append(record)
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
            f"[treasury {idx}/{len(rows)}] {row['ticker']} {row['company']} "
            f"status={record.get('status')} elapsed={elapsed_txt} hot={hottest_txt}",
            flush=True,
        )

    payload = {
        "meta": {
            "sample": "KOSPI 50 + KOSDAQ 10",
            "row_count": len(rows),
            "generated_at": int(time.time()),
            "elapsed_wall_sec": time.time() - started,
            "timeout_sec": args.timeout_sec,
        },
        "summary": _summarize(records),
        "records": records,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "wiki/architecture/audits/data/260511_perf_treasury_share_audit/stage_profile_kospi50_kosdaq10.json",
    )
    parser.add_argument("--timeout-sec", type=float, default=180.0)
    asyncio.run(main(parser.parse_args()))
