"""Filing-sample audit for shareholder_meeting_notice.

분류된 notice CSV를 입력으로 받아 공시 단위 parsing 성공률을 집계한다.
"""

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
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from open_proxy_mcp.services.provisional_financial_statement import (  # noqa: E402
    extract_metrics as extract_pfs_metrics,
    parse_provisional_financial_statement,
)
from open_proxy_mcp.services.shareholder_meeting import (  # noqa: E402
    _agenda_titles,
    _load_notice_bundle_with_fallback,
)
from open_proxy_mcp.tools.parser import parse_aoi_xml  # noqa: E402


def _load_rows(path: Path, start: int = 0, count: int = 0) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    if count > 0:
        return rows[start:start + count]
    return rows[start:]


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    return statistics.quantiles(values, n=100, method="inclusive")[94]


def _has_topic(titles: list[str], keywords: tuple[str, ...]) -> bool:
    return any(any(keyword in title for keyword in keywords) for title in titles)


async def _audit_one(row: dict[str, str], sem: asyncio.Semaphore) -> dict[str, Any]:
    async with sem:
        t0 = time.perf_counter()
        try:
            parsed, warnings, source = await _load_notice_bundle_with_fallback(row["rcept_no"], scope="full", soup_cache={})
            agenda = parsed.get("agenda") or []
            titles = _agenda_titles(agenda)
            meeting_info = parsed.get("meeting_info") or {}
            board = parsed.get("board") or {}
            compensation = parsed.get("compensation") or {}
            html = parsed.get("html") or ""

            summary_ok = bool(meeting_info.get("meeting_type")) and bool(meeting_info.get("datetime")) and bool(parsed.get("agenda_valid")) and len(titles) >= 1

            board_expected = _has_topic(titles, ("이사 선임", "이사선임", "사외이사 선임", "감사 선임", "감사위원"))
            board_ok = (not board_expected) or len(board.get("appointments") or []) >= 1

            comp_expected = _has_topic(titles, ("보수한도", "보수 한도", "이사 보수", "감사 보수"))
            comp_ok = (not comp_expected) or len(compensation.get("items") or []) >= 1

            aoi_expected = _has_topic(titles, ("정관 일부 변경", "정관변경", "정관 변경"))
            aoi = parse_aoi_xml(html, sub_agendas=agenda) if html and aoi_expected else {"amendments": []}
            aoi_ok = (not aoi_expected) or len(aoi.get("amendments") or []) >= 1

            pfs_expected = _has_topic(titles, ("재무제표 승인", "재무제표승인", "재무제표 보고"))
            pfs_ok = True
            pfs_metric_keys_filled = 0
            if pfs_expected:
                pfs_parsed = parse_provisional_financial_statement(html)
                pfs_metrics = extract_pfs_metrics(pfs_parsed)
                pfs_metric_keys_filled = sum(1 for k, v in pfs_metrics.items() if k.startswith("fy_") and v not in (None, ""))
                pfs_ok = pfs_metric_keys_filled >= 4

            if summary_ok and board_ok and comp_ok and aoi_ok and pfs_ok:
                audit_class = "success"
            elif summary_ok:
                audit_class = "soft_fail"
            else:
                audit_class = "hard_fail"

            duration_ms = round((time.perf_counter() - t0) * 1000, 1)
            return {
                "corp_name": row.get("corp_name", ""),
                "stock_code": row.get("stock_code", ""),
                "rcept_no": row.get("rcept_no", ""),
                "rcept_dt": row.get("rcept_dt", ""),
                "report_nm": row.get("report_nm", ""),
                "meeting_type_detected": meeting_info.get("meeting_type") or row.get("meeting_type_detected", ""),
                "info_source": source,
                "duration_ms": duration_ms,
                "audit_class": audit_class,
                "summary_ok": summary_ok,
                "agenda_titles_count": len(titles),
                "board_expected": board_expected,
                "board_ok": board_ok,
                "board_appointments_count": len(board.get("appointments") or []),
                "comp_expected": comp_expected,
                "comp_ok": comp_ok,
                "comp_items_count": len(compensation.get("items") or []),
                "aoi_expected": aoi_expected,
                "aoi_ok": aoi_ok,
                "aoi_amendments_count": len(aoi.get("amendments") or []),
                "pfs_expected": pfs_expected,
                "pfs_ok": pfs_ok,
                "pfs_metric_keys_filled": pfs_metric_keys_filled,
                "warnings_count": len(warnings),
                "warning_sample": warnings[:2],
            }
        except Exception as exc:  # noqa: BLE001
            duration_ms = round((time.perf_counter() - t0) * 1000, 1)
            return {
                "corp_name": row.get("corp_name", ""),
                "stock_code": row.get("stock_code", ""),
                "rcept_no": row.get("rcept_no", ""),
                "rcept_dt": row.get("rcept_dt", ""),
                "report_nm": row.get("report_nm", ""),
                "meeting_type_detected": row.get("meeting_type_detected", ""),
                "duration_ms": duration_ms,
                "audit_class": "hard_fail",
                "summary_ok": False,
                "board_expected": False,
                "board_ok": False,
                "comp_expected": False,
                "comp_ok": False,
                "aoi_expected": False,
                "aoi_ok": False,
                "pfs_expected": False,
                "pfs_ok": False,
                "warnings_count": 0,
                "exception_type": type(exc).__name__,
                "exception_message": str(exc)[:500],
            }


def _summarize(records: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(r["audit_class"] for r in records)
    durations = [float(r["duration_ms"]) for r in records]

    def _rate_ok(field: str, expected_field: str | None = None) -> dict[str, Any]:
        if expected_field is None:
            ok = sum(1 for r in records if r.get(field))
            return {"ok": ok, "total": len(records), "rate": round(ok * 100.0 / len(records), 1) if records else 0.0}
        denom = [r for r in records if r.get(expected_field)]
        ok = sum(1 for r in denom if r.get(field))
        return {"ok": ok, "total": len(denom), "rate": round(ok * 100.0 / len(denom), 1) if denom else 0.0}

    return {
        "total": len(records),
        "audit_counts": dict(counts),
        "strict_success_rate": round(counts.get("success", 0) * 100.0 / len(records), 1) if records else 0.0,
        "usable_rate": round((counts.get("success", 0) + counts.get("soft_fail", 0)) * 100.0 / len(records), 1) if records else 0.0,
        "hard_fail_rate": round(counts.get("hard_fail", 0) * 100.0 / len(records), 1) if records else 0.0,
        "median_ms": round(statistics.median(durations), 1) if durations else 0.0,
        "p95_ms": round(_p95(durations), 1) if durations else 0.0,
        "summary_usable_rate": _rate_ok("summary_ok"),
        "board_usable_rate": _rate_ok("board_ok", "board_expected"),
        "compensation_usable_rate": _rate_ok("comp_ok", "comp_expected"),
        "aoi_change_usable_rate": _rate_ok("aoi_ok", "aoi_expected"),
        "prov_financials_usable_rate": _rate_ok("pfs_ok", "pfs_expected"),
    }


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--start", type=int, default=0)
    parser.add_argument("--count", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=3)
    args = parser.parse_args()

    rows = _load_rows(Path(args.input), start=args.start, count=args.count)
    sem = asyncio.Semaphore(args.concurrency)
    records = await asyncio.gather(*[_audit_one(row, sem) for row in rows])
    result = {
        "meta": {
            "script": "scripts/shareholder_meeting_notice_parsing_audit.py",
            "input": args.input,
            "n_notices": len(rows),
        },
        "records": records,
        "summary": _summarize(records),
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["summary"], ensure_ascii=False, indent=2))
    print(f"[notice-audit] wrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
