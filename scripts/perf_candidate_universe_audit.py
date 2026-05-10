from __future__ import annotations

import argparse
import asyncio
import copy
import csv
import json
import statistics
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
load_dotenv(ROOT / ".env")

import open_proxy_mcp.services.provisional_financial_statement as pfs_mod  # noqa: E402
import open_proxy_mcp.tools.parser as parser_mod  # noqa: E402
from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload  # noqa: E402


UNIVERSE_FILES = {
    "kospi35": ROOT / "wiki/architecture/audits/data/260510_perf_data_tools_audit/universe_kospi35.csv",
    "kosdaq25": ROOT / "wiki/architecture/audits/data/260510_perf_data_tools_audit/universe_kosdaq25.csv",
    "kospi200": ROOT / "wiki/architecture/audits/data/260506_universe_kospi_200.csv",
    "kosdaq100": ROOT / "wiki/architecture/audits/data/260506_universe_kosdaq_100.csv",
}


def _load_universe(name: str, start: int = 0, limit: int | None = None) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with UNIVERSE_FILES[name].open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    if start:
        rows = rows[start:]
    if limit is not None:
        rows = rows[:limit]
    return rows


def _strip_dynamic(payload: dict[str, Any]) -> dict[str, Any]:
    clone = copy.deepcopy(payload)
    clone.pop("generated_at", None)
    data = clone.get("data")
    if isinstance(data, dict):
        data.pop("usage", None)
    return clone


def _payload_summary(payload: dict[str, Any]) -> dict[str, Any]:
    data = payload.get("data") or {}
    return {
        "status": payload.get("status"),
        "warning_count": len(payload.get("warnings") or []),
        "filing_count": data.get("filing_count"),
        "parsing_failures": data.get("parsing_failures"),
        "meeting_phase": data.get("meeting_phase"),
        "result_status": data.get("result_status"),
    }


class SoupCache:
    def __init__(self, original: Callable[..., Any]) -> None:
        self.original = original
        self.cache: dict[tuple[str, Any], Any] = {}

    def clear(self) -> None:
        self.cache.clear()

    def __call__(self, markup: Any = "", features: Any = None, *args: Any, **kwargs: Any) -> Any:
        key = (markup, features) if isinstance(markup, str) else None
        if key is not None and key in self.cache:
            return self.cache[key]
        soup = self.original(markup, features, *args, **kwargs)
        if key is not None:
            self.cache[key] = soup
        return soup


@contextmanager
def _cached_soup_patch() -> Any:
    orig_parser_bs = parser_mod.BeautifulSoup
    orig_pfs_bs = pfs_mod.BeautifulSoup
    parser_cache = SoupCache(orig_parser_bs)
    pfs_cache = SoupCache(orig_pfs_bs)
    parser_mod.BeautifulSoup = parser_cache
    pfs_mod.BeautifulSoup = pfs_cache
    try:
        yield parser_cache, pfs_cache
    finally:
        parser_mod.BeautifulSoup = orig_parser_bs
        pfs_mod.BeautifulSoup = orig_pfs_bs


async def _time_call(factory: Callable[[], Any]) -> tuple[float, dict[str, Any]]:
    t0 = time.perf_counter()
    payload = await factory()
    return time.perf_counter() - t0, payload


async def _audit_shareholder_meeting(company: str, ticker: str) -> dict[str, Any]:
    factory = lambda: build_shareholder_meeting_payload(company, scope="summary", year=2026, meeting_type="annual")

    try:
        await asyncio.wait_for(factory(), timeout=90)
        baseline_sec, baseline_payload = await asyncio.wait_for(_time_call(factory), timeout=90)
        with _cached_soup_patch() as caches:
            for cache in caches:
                cache.clear()
            experimental_sec, experimental_payload = await asyncio.wait_for(_time_call(factory), timeout=90)
    except Exception as exc:
        return {
            "ticker": ticker,
            "company": company,
            "status": "exception",
            "error": f"{type(exc).__name__}: {exc}",
        }

    equal = _strip_dynamic(baseline_payload) == _strip_dynamic(experimental_payload)
    return {
        "ticker": ticker,
        "company": company,
        "status": baseline_payload.get("status"),
        "experimental_status": experimental_payload.get("status"),
        "baseline_sec": baseline_sec,
        "experimental_sec": experimental_sec,
        "speedup_pct": ((baseline_sec - experimental_sec) / baseline_sec * 100.0) if baseline_sec else 0.0,
        "payload_equal_without_usage_generated_at": equal,
        "baseline_summary": _payload_summary(baseline_payload),
        "experimental_summary": _payload_summary(experimental_payload),
    }


def _summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    ok = [r for r in records if r.get("status") != "exception"]
    equal = [r for r in ok if r.get("payload_equal_without_usage_generated_at")]
    speedups = [r["speedup_pct"] for r in ok]
    return {
        "n_total": len(records),
        "n_ok": len(ok),
        "n_exception": len(records) - len(ok),
        "n_equal": len(equal),
        "n_not_equal": len(ok) - len(equal),
        "median_speedup_pct": statistics.median(speedups) if speedups else None,
        "mean_speedup_pct": statistics.mean(speedups) if speedups else None,
        "max_speedup_pct": max(speedups) if speedups else None,
        "min_speedup_pct": min(speedups) if speedups else None,
        "status_counts": {
            key: sum(1 for r in ok if r.get("status") == key)
            for key in sorted({r.get("status") for r in ok})
        },
    }


async def _run_batch(rows: list[dict[str, str]], batch_index: int, batch_count: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for i, row in enumerate(rows, 1):
        result = await _audit_shareholder_meeting(row["company"], row["ticker"])
        results.append(result)
        marker = "✓" if result.get("status") != "exception" else "✗"
        sp = result.get("speedup_pct")
        sp_txt = f"{sp:.1f}%" if isinstance(sp, (int, float)) else "-"
        eq_txt = result.get("payload_equal_without_usage_generated_at")
        print(
            f"[batch {batch_index}/{batch_count} {i}/{len(rows)}] {marker} "
            f"{row['ticker']} {row['company']} status={result.get('status')} speedup={sp_txt} equal={eq_txt}",
            flush=True,
        )
    return results


async def main(args: argparse.Namespace) -> None:
    rows = _load_universe(args.universe, start=args.start, limit=args.limit)
    batches = [rows[i:i + args.batch_size] for i in range(0, len(rows), args.batch_size)]
    all_results: list[dict[str, Any]] = []
    started = time.time()
    for idx, batch in enumerate(batches, 1):
        batch_results = await _run_batch(batch, idx, len(batches))
        all_results.extend(batch_results)
        if idx < len(batches) and args.batch_sleep_sec > 0:
            print(f"# sleeping {args.batch_sleep_sec}s before next batch", flush=True)
            await asyncio.sleep(args.batch_sleep_sec)

    payload = {
        "meta": {
            "tool": "shareholder_meeting_summary",
            "universe": args.universe,
            "start": args.start,
            "sample_size": len(rows),
            "batch_size": args.batch_size,
            "batch_sleep_sec": args.batch_sleep_sec,
            "duration_sec": round(time.time() - started, 2),
        },
        "summary": _summarize(all_results),
        "records": all_results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"# wrote {args.output}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", choices=sorted(UNIVERSE_FILES), required=True)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--batch-sleep-sec", type=float, default=10.0)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--output", type=Path, required=True)
    parsed = parser.parse_args()
    asyncio.run(main(parsed))
