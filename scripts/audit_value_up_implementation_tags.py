"""Audit value_up plan-title and implementation-section tagging.

Runs over the marketwide universe and inspects value-up filings since 2024.
Keeps DART pressure low by batching company searches and only fetching matched
value-up documents.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from open_proxy_mcp.dart.client import DartClientError, get_dart_client  # noqa: E402
from open_proxy_mcp.services.company import resolve_company_query  # noqa: E402
from open_proxy_mcp.services.filing_search import search_filings_by_report_name  # noqa: E402
from open_proxy_mcp.services.value_up_v2 import (  # noqa: E402
    _VALUATION_KEYWORDS,
    _classify_value_up_item,
    _extract_implementation_sections,
    _extract_plan_title,
)


DEFAULT_UNIVERSES = [
    ROOT / "wiki/architecture/audits/data/260525_agenda_parser_marketwide/universe_kospi500.csv",
    ROOT / "wiki/architecture/audits/data/260525_agenda_parser_marketwide/universe_kosdaq150.csv",
]


def _load_universe(paths: list[Path], start: int = 0, limit: int = 0) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for path in paths:
        with path.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append({
                    "market": row.get("market", ""),
                    "rank": row.get("rank", ""),
                    "ticker": row.get("ticker", ""),
                    "company": row.get("company", ""),
                })
    sliced = rows[start:]
    return sliced[:limit] if limit else sliced


async def _audit_company(row: dict[str, str], args: argparse.Namespace) -> dict[str, Any]:
    started_at = time.perf_counter()
    company = row["company"]
    try:
        resolution = await resolve_company_query(company)
        if not resolution.selected:
            return {**row, "status": "resolve_failed", "duration_ms": int((time.perf_counter() - started_at) * 1000)}
        selected = resolution.selected
        items, notices, error = await search_filings_by_report_name(
            corp_code=selected["corp_code"],
            bgn_de=args.start_date,
            end_de=args.end_date,
            pblntf_tys="I",
            keywords=_VALUATION_KEYWORDS,
            strip_spaces=True,
            max_pages=3,
            page_count=100,
        )
        if error:
            if error == "013":
                return {
                    **row,
                    "status": "ok",
                    "corp_code": selected["corp_code"],
                    "filing_count": 0,
                    "filings": [],
                    "duration_ms": int((time.perf_counter() - started_at) * 1000),
                }
            return {
                **row,
                "status": "search_error",
                "error": error,
                "notices": notices,
                "duration_ms": int((time.perf_counter() - started_at) * 1000),
            }
        filings: list[dict[str, Any]] = []
        for item in items:
            rcept_no = item.get("rcept_no", "")
            text = ""
            fetch_error = ""
            if rcept_no:
                try:
                    doc = await get_dart_client().get_document_cached(rcept_no)
                    text = doc.get("text", "")
                except DartClientError as exc:
                    fetch_error = exc.status
            plan_title = _extract_plan_title(text)
            category = _classify_value_up_item(item.get("report_nm", ""), plan_title=plan_title)
            sections = _extract_implementation_sections(text)
            tag_counts = Counter(section["tag"] for section in sections)
            filings.append({
                "rcept_dt": item.get("rcept_dt", ""),
                "rcept_no": rcept_no,
                "report_nm": item.get("report_nm", ""),
                "category": category,
                "plan_title": plan_title,
                "implementation_tag_counts": dict(tag_counts),
                "implementation_sections_count": len(sections),
                "embedded_results_count": sum(1 for section in sections if section["tag"] == "implementation_result"),
                "fetch_error": fetch_error,
                "sample_sections": sections[:5],
            })
        return {
            **row,
            "status": "ok",
            "corp_code": selected["corp_code"],
            "filing_count": len(filings),
            "filings": filings,
            "duration_ms": int((time.perf_counter() - started_at) * 1000),
        }
    except Exception as exc:
        return {
            **row,
            "status": "exception",
            "error_type": type(exc).__name__,
            "error": str(exc)[:300],
            "duration_ms": int((time.perf_counter() - started_at) * 1000),
        }


def _summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    status_counts = Counter(record.get("status", "") for record in records)
    filing_records = [record for record in records if record.get("filing_count", 0) > 0]
    filings = [filing for record in filing_records for filing in record.get("filings", [])]
    category_counts = Counter(filing.get("category", "") for filing in filings)
    tag_counts: Counter[str] = Counter()
    plan_title_counts = Counter(bool(filing.get("plan_title")) for filing in filings)
    for filing in filings:
        tag_counts.update(filing.get("implementation_tag_counts") or {})
    return {
        "companies": len(records),
        "status_counts": dict(status_counts),
        "companies_with_value_up": len(filing_records),
        "filings": len(filings),
        "category_counts": dict(category_counts),
        "plan_title_presence": {
            "present": plan_title_counts[True],
            "missing": plan_title_counts[False],
        },
        "implementation_tag_counts": dict(tag_counts),
        "filings_with_implementation_sections": sum(1 for filing in filings if filing.get("implementation_sections_count", 0) > 0),
        "filings_with_embedded_results": sum(1 for filing in filings if filing.get("embedded_results_count", 0) > 0),
        "companies_with_embedded_results": sum(
            1 for record in filing_records
            if any(filing.get("embedded_results_count", 0) > 0 for filing in record.get("filings", []))
        ),
    }


async def _run(args: argparse.Namespace) -> int:
    universe_paths = [Path(p) for p in args.universe] if args.universe else DEFAULT_UNIVERSES
    rows = _load_universe(universe_paths, start=args.start, limit=args.limit)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    started_at = time.perf_counter()
    for offset in range(0, len(rows), args.batch_size):
        batch = rows[offset:offset + args.batch_size]
        sem = asyncio.Semaphore(args.concurrency)

        async def wrapped(row: dict[str, str]) -> dict[str, Any]:
            async with sem:
                return await _audit_company(row, args)

        batch_results = await asyncio.gather(*(wrapped(row) for row in batch))
        records.extend(batch_results)
        with_value_up = sum(1 for row in batch_results if row.get("filing_count", 0) > 0)
        print(
            f"[batch] {offset + len(batch)}/{len(rows)} "
            f"value_up_companies={with_value_up} elapsed={time.perf_counter() - started_at:.1f}s",
            flush=True,
        )
        if offset + args.batch_size < len(rows):
            await asyncio.sleep(args.batch_sleep)

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": {"start_date": args.start_date, "end_date": args.end_date},
        "start": args.start,
        "limit": args.limit,
        "batch_size": args.batch_size,
        "concurrency": args.concurrency,
        "duration_s": round(time.perf_counter() - started_at, 1),
        **_summarize(records),
    }
    (out_dir / "records.json").write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    rows_csv = out_dir / "filings.csv"
    with rows_csv.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "market", "rank", "ticker", "company", "rcept_dt", "rcept_no",
                "report_nm", "category", "plan_title", "implementation_sections_count",
                "embedded_results_count", "implementation_tag_counts",
            ],
        )
        writer.writeheader()
        for record in records:
            for filing in record.get("filings", []):
                writer.writerow({
                    "market": record.get("market", ""),
                    "rank": record.get("rank", ""),
                    "ticker": record.get("ticker", ""),
                    "company": record.get("company", ""),
                    "rcept_dt": filing.get("rcept_dt", ""),
                    "rcept_no": filing.get("rcept_no", ""),
                    "report_nm": filing.get("report_nm", ""),
                    "category": filing.get("category", ""),
                    "plan_title": filing.get("plan_title", ""),
                    "implementation_sections_count": filing.get("implementation_sections_count", 0),
                    "embedded_results_count": filing.get("embedded_results_count", 0),
                    "implementation_tag_counts": json.dumps(filing.get("implementation_tag_counts", {}), ensure_ascii=False),
                })
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", action="append", help="CSV universe path. Can repeat.")
    parser.add_argument("--start-date", default="20240101")
    parser.add_argument("--end-date", default="20260530")
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--batch-size", type=int, default=30)
    parser.add_argument("--batch-sleep", type=float, default=2.0)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--output-dir", default="wiki/architecture/audits/data/260530_value_up_implementation_tags")
    return asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
