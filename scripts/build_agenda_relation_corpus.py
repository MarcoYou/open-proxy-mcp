"""Build local agenda relation audit corpus from DART document.xml.

The corpus is for parser/audit/regression work only. Production tools still
fetch DART live.

Usage:
    uv run python scripts/build_agenda_relation_corpus.py --limit 50
"""

from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from open_proxy_mcp.dart.client import get_dart_client  # noqa: E402
from open_proxy_mcp.services.shareholder_meeting import _agenda_nodes, _parse_notice_bundle  # noqa: E402
from open_proxy_mcp.tools.parser import parse_aoi_xml, parse_retirement_pay_xml  # noqa: E402

CLASSIFIED_CSV = ROOT / "wiki/architecture/audits/data/260517_parsing_success_rate_audit/shareholder_meeting_notice_2026_classified.csv"
DISPUTE_CSV = ROOT / "wiki/architecture/audits/data/260508_law_layer/dispute_universe.csv"
LAW_KOSPI = ROOT / "wiki/architecture/audits/data/260510_law_layer_450/kospi_200.json"
LAW_KOSDAQ = ROOT / "wiki/architecture/audits/data/260510_law_layer_450/kosdaq_150.json"
OUT_DIR = ROOT / "wiki/architecture/audits/data/260524_agenda_relation_corpus"


def _read_classified() -> list[dict[str, str]]:
    with CLASSIFIED_CSV.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _latest_annual_by_ticker(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    annual = [
        row for row in rows
        if row.get("stock_code")
        and row.get("rcept_no")
        and row.get("meeting_type_detected") in {"정기", "annual"}
    ]
    annual.sort(key=lambda r: (r.get("stock_code", ""), r.get("rcept_dt", ""), r.get("rcept_no", "")))
    out: dict[str, dict[str, str]] = {}
    for row in annual:
        out[row["stock_code"]] = row
    return out


def _read_dispute_tickers() -> list[str]:
    if not DISPUTE_CSV.exists():
        return []
    with DISPUTE_CSV.open(encoding="utf-8") as f:
        return [row["ticker"] for row in csv.DictReader(f) if row.get("ticker")]


def _read_law_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return (json.loads(path.read_text(encoding="utf-8")).get("records") or [])


def _choose_samples(limit: int) -> list[dict[str, Any]]:
    rows = _read_classified()
    by_ticker = _latest_annual_by_ticker(rows)

    chosen: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(row: dict[str, str] | None, bucket: str, reason: str) -> None:
        if not row:
            return
        ticker = row.get("stock_code") or ""
        rcept_no = row.get("rcept_no") or ""
        if not ticker or not rcept_no or ticker in seen:
            return
        seen.add(ticker)
        chosen.append({
            "bucket": bucket,
            "sample_reason": reason,
            "ticker": ticker,
            "company": row.get("corp_name") or "",
            "corp_code": row.get("corp_code") or "",
            "rcept_no": rcept_no,
            "rcept_dt": row.get("rcept_dt") or "",
            "report_nm": row.get("report_nm") or "",
            "is_correction": row.get("is_correction") or "",
            "meeting_type_detected": row.get("meeting_type_detected") or "",
        })

    # 1) controversial / dispute universe, keep as many as available.
    for ticker in _read_dispute_tickers():
        add(by_ticker.get(ticker), "controversial", "dispute_universe")
        if sum(1 for x in chosen if x["bucket"] == "controversial") >= 20:
            break

    # 2) large cap / legally interesting. Prefer 2T+ and law-layer hit records.
    large_records = _read_law_records(LAW_KOSPI) + _read_law_records(LAW_KOSDAQ)
    large_records.sort(key=lambda r: (
        0 if r.get("is_2tril_plus") else 1,
        -(r.get("n_law_hits") or 0),
        -(r.get("n_agendas") or 0),
    ))
    for rec in large_records:
        add(by_ticker.get(rec.get("ticker") or ""), "large_cap", "2tril_plus_or_law_layer")
        if sum(1 for x in chosen if x["bucket"] == "large_cap") >= 15:
            break

    # 3) small/mid cap. Pick spaced rows from classified annual list, excluding
    # law-layer corpus picks, to avoid only mega caps.
    annual_rows = list(by_ticker.values())
    annual_rows.sort(key=lambda r: (r.get("rcept_dt", ""), r.get("stock_code", "")))
    stride = max(1, len(annual_rows) // 80)
    for idx in range(0, len(annual_rows), stride):
        add(annual_rows[idx], "small_mid_cap", "classified_annual_spaced_sample")
        if len(chosen) >= limit:
            break

    # Fill any gap with remaining annuals.
    for row in annual_rows:
        if len(chosen) >= limit:
            break
        add(row, "fill", "classified_annual_fill")

    return chosen[:limit]


def _charter_subs(agenda: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for item in agenda:
        if "정관" in (item.get("title") or ""):
            return item.get("children") or []
    return []


async def _build_one(sample: dict[str, Any], sem: asyncio.Semaphore) -> dict[str, Any]:
    async with sem:
        client = get_dart_client()
        rcept_no = sample["rcept_no"]
        doc = await client.get_document_cached(rcept_no)
        text = doc.get("text") or ""
        html = doc.get("html") or ""
        parsed = _parse_notice_bundle(text, html, rcept_no=rcept_no, soup_cache={})
        agenda = parsed.get("agenda") or []
        aoi_change = parse_aoi_xml(html, sub_agendas=_charter_subs(agenda)) if html else {"amendments": [], "summary": {}}
        retirement = parse_retirement_pay_xml(html) if html else {"amendments": []}

        out = {
            "metadata": {
                **sample,
                "source": "DART document.xml",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "text_length": len(text),
                "document_xml_length": len(html),
                "image_count": len(doc.get("images") or []),
            },
            "document_xml": html,
            "text": text,
            "images": doc.get("images") or [],
            "parsed": {
                "meeting_info": parsed.get("meeting_info") or {},
                "agenda_valid": parsed.get("agenda_valid"),
                "agendas_raw": agenda,
                "agendas": _agenda_nodes(agenda),
                "aoi_change": aoi_change,
                "retirement_pay": retirement,
                "board": parsed.get("board") or {},
                "compensation": parsed.get("compensation") or {},
                "correction": parsed.get("correction"),
            },
        }
        return out


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--concurrency", type=int, default=3)
    args = parser.parse_args()

    samples = _choose_samples(args.limit)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    docs_dir = OUT_DIR / "documents"
    docs_dir.mkdir(exist_ok=True)

    sem = asyncio.Semaphore(args.concurrency)
    results = await asyncio.gather(*[_build_one(sample, sem) for sample in samples], return_exceptions=True)

    manifest_rows: list[dict[str, Any]] = []
    for sample, result in zip(samples, results):
        if isinstance(result, Exception):
            manifest_rows.append({**sample, "status": "error", "error": f"{type(result).__name__}: {result}"})
            continue
        path = docs_dir / f"{sample['rcept_no']}.json"
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        parsed = result["parsed"]
        manifest_rows.append({
            **sample,
            "status": "ok",
            "path": str(path.relative_to(ROOT)),
            "agenda_count": len(parsed.get("agendas_raw") or []),
            "agenda_tree_count": _count_agendas(parsed.get("agendas") or []),
            "aoi_amendments_count": len((parsed.get("aoi_change") or {}).get("amendments") or []),
            "retirement_amendments_count": len((parsed.get("retirement_pay") or {}).get("amendments") or []),
            "board_appointments_count": len((parsed.get("board") or {}).get("appointments") or []),
            "document_xml_length": result["metadata"]["document_xml_length"],
            "text_length": result["metadata"]["text_length"],
        })

    manifest = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "purpose": "Local agenda relation parser/audit/regression corpus. Not production storage.",
        "source_files": {
            "classified_csv": str(CLASSIFIED_CSV.relative_to(ROOT)),
            "dispute_csv": str(DISPUTE_CSV.relative_to(ROOT)),
            "law_kospi": str(LAW_KOSPI.relative_to(ROOT)),
            "law_kosdaq": str(LAW_KOSDAQ.relative_to(ROOT)),
        },
        "target_limit": args.limit,
        "samples": manifest_rows,
    }
    (OUT_DIR / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print("out_dir", OUT_DIR)
    print("ok", sum(1 for r in manifest_rows if r.get("status") == "ok"))
    print("error", sum(1 for r in manifest_rows if r.get("status") == "error"))
    buckets: dict[str, int] = {}
    for row in manifest_rows:
        buckets[row["bucket"]] = buckets.get(row["bucket"], 0) + 1
    print("buckets", buckets)


def _count_agendas(items: list[dict[str, Any]]) -> int:
    return sum(1 + _count_agendas(item.get("children") or []) for item in items)


if __name__ == "__main__":
    asyncio.run(main())
