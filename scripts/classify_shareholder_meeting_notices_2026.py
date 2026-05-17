"""Classify 2026 shareholder meeting notices by actual parsed meeting_type.

기존 meeting_notices_scan.csv의 is_extraordinary 플래그를 신뢰하지 않고,
실제 notice 본문에서 meeting_type을 다시 판정한다.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "wiki/architecture/audits/data/260504_meeting_notices_scan.csv"
OUT_DIR = ROOT / "wiki/architecture/audits/data/260517_parsing_success_rate_audit"

from open_proxy_mcp.dart.client import get_dart_client  # noqa: E402
from open_proxy_mcp.services.shareholder_meeting import _notice_info_with_fallback  # noqa: E402


def _load_rows(limit: int = 0) -> list[dict[str, str]]:
    today = int(date.today().strftime("%Y%m%d"))
    rows: list[dict[str, str]] = []
    with SOURCE.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rcept_dt = (row.get("rcept_dt") or "").strip()
            if not (len(rcept_dt) == 8 and rcept_dt.isdigit()):
                continue
            if not rcept_dt.startswith("2026"):
                continue
            if int(rcept_dt) > today:
                continue
            rows.append(row)
            if limit and len(rows) >= limit:
                break
    return rows


async def _classify_one(row: dict[str, str], sem: asyncio.Semaphore) -> dict[str, Any]:
    async with sem:
        client = get_dart_client()
        rcept_no = row["rcept_no"]
        doc = await client.get_document_cached(rcept_no)
        info, source = await _notice_info_with_fallback(rcept_no, doc.get("text", ""), doc.get("html", ""))
        return {
            **row,
            "meeting_type_detected": info.get("meeting_type") or "",
            "meeting_datetime": info.get("datetime") or "",
            "info_source": source,
        }


def _write(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "corp_code",
        "corp_name",
        "stock_code",
        "rcept_no",
        "rcept_dt",
        "report_nm",
        "is_correction",
        "is_extraordinary",
        "meeting_type_detected",
        "meeting_datetime",
        "info_source",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=3)
    args = parser.parse_args()

    rows = _load_rows(limit=args.limit)
    sem = asyncio.Semaphore(args.concurrency)
    classified = await asyncio.gather(*[_classify_one(row, sem) for row in rows])

    annual = [r for r in classified if r.get("meeting_type_detected") in {"annual", "정기"}]
    extraordinary = [r for r in classified if r.get("meeting_type_detected") in {"extraordinary", "임시"} and int(r["rcept_dt"]) >= 20260331]
    unknown = [r for r in classified if not r.get("meeting_type_detected")]

    annual.sort(key=lambda r: (r["rcept_dt"], r["rcept_no"]))
    extraordinary.sort(key=lambda r: (r["rcept_dt"], r["rcept_no"]))
    unknown.sort(key=lambda r: (r["rcept_dt"], r["rcept_no"]))

    all_path = OUT_DIR / "shareholder_meeting_notice_2026_classified.csv"
    annual_path = OUT_DIR / "shareholder_meeting_notice_annual_2026.csv"
    extraordinary_path = OUT_DIR / "shareholder_meeting_notice_extraordinary_since_20260331.csv"
    unknown_path = OUT_DIR / "shareholder_meeting_notice_unknown_2026.csv"
    _write(all_path, classified)
    _write(annual_path, annual)
    _write(extraordinary_path, extraordinary)
    _write(unknown_path, unknown)
    print("classified", len(classified))
    print("annual", len(annual))
    print("extraordinary", len(extraordinary))
    print("unknown", len(unknown))


if __name__ == "__main__":
    asyncio.run(main())
