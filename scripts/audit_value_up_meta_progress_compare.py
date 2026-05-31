"""Compare value_up meta_amendment filings against progress/plan filings.

Purpose: decide whether high-dividend/meta republications can substitute for
latest progress filings, or should be exposed as separate supplemental context.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from open_proxy_mcp.dart.client import get_dart_client  # noqa: E402
from open_proxy_mcp.services.value_up_v2 import (  # noqa: E402
    _clean_text,
    _extract_implementation_sections,
    _extract_main_content,
)


INPUT_RECORDS = ROOT / "wiki/architecture/audits/data/260530_value_up_implementation_tags/records.json"
OUTPUT_DIR = ROOT / "wiki/architecture/audits/data/260530_value_up_meta_progress_compare"


def _norm_for_compare(text: str) -> str:
    text = _clean_text(text)
    text = text.replace(" ", "")
    for token in ("기업가치제고계획", "기업가치제고", "자율공시", "고배당기업", "조세특례제한법"):
        text = text.replace(token, "")
    return text[:6000]


def _similarity(a: str, b: str) -> float:
    aa = _norm_for_compare(a)
    bb = _norm_for_compare(b)
    if not aa or not bb:
        return 0.0
    return round(SequenceMatcher(None, aa, bb).ratio(), 4)


def _tag_counts(text: str) -> Counter[str]:
    return Counter(section["tag"] for section in _extract_implementation_sections(text))


def _classify_relation(
    *,
    meta_text: str,
    progress_text: str,
    progress_similarity: float,
    meta_tags: Counter[str],
    has_progress: bool,
) -> str:
    if meta_tags.get("implementation_result", 0) > 0:
        return "meta_embedded_result"
    if has_progress and progress_similarity >= 0.82:
        return "meta_duplicates_progress"
    if has_progress and progress_similarity >= 0.45:
        return "meta_partially_overlaps_progress"
    if meta_tags and set(meta_tags) <= {"meta_reference"}:
        return "meta_reference_only"
    if not has_progress:
        return "meta_without_progress_compare_to_plan"
    if "참조" in meta_text or "고배당" in meta_text:
        return "meta_reference_or_summary"
    return "meta_distinct_from_progress"


async def _doc_text(rcept_no: str) -> str:
    if not rcept_no:
        return ""
    doc = await get_dart_client().get_document_cached(rcept_no)
    return doc.get("text", "")


async def main() -> int:
    records = json.loads(INPUT_RECORDS.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    details: list[dict[str, Any]] = []

    for record in records:
        filings = record.get("filings", [])
        metas = [f for f in filings if f.get("category") == "meta_amendment"]
        if not metas:
            continue
        progress = [f for f in filings if f.get("category") == "progress"]
        plans = [f for f in filings if f.get("category") == "plan"]

        for meta in metas:
            meta_date = meta.get("rcept_dt", "")
            prior_progress = [f for f in progress if f.get("rcept_dt", "") <= meta_date]
            prior_plan = [f for f in plans if f.get("rcept_dt", "") <= meta_date]
            latest_progress = (prior_progress or progress or [None])[0]
            latest_plan = (prior_plan or plans or [None])[0]

            meta_text = await _doc_text(meta.get("rcept_no", ""))
            progress_text = await _doc_text(latest_progress.get("rcept_no", "")) if latest_progress else ""
            plan_text = await _doc_text(latest_plan.get("rcept_no", "")) if latest_plan else ""

            meta_main = _extract_main_content(meta_text)
            progress_main = _extract_main_content(progress_text)
            plan_main = _extract_main_content(plan_text)
            meta_tags = _tag_counts(meta_text)
            progress_tags = _tag_counts(progress_text)
            plan_tags = _tag_counts(plan_text)
            progress_similarity = _similarity(meta_main, progress_main)
            plan_similarity = _similarity(meta_main, plan_main)
            relation = _classify_relation(
                meta_text=meta_main,
                progress_text=progress_main,
                progress_similarity=progress_similarity,
                meta_tags=meta_tags,
                has_progress=latest_progress is not None,
            )

            row = {
                "market": record.get("market", ""),
                "rank": record.get("rank", ""),
                "ticker": record.get("ticker", ""),
                "company": record.get("company", ""),
                "meta_rcept_dt": meta.get("rcept_dt", ""),
                "meta_rcept_no": meta.get("rcept_no", ""),
                "meta_report_nm": meta.get("report_nm", ""),
                "meta_plan_title": meta.get("plan_title", ""),
                "progress_rcept_dt": latest_progress.get("rcept_dt", "") if latest_progress else "",
                "progress_rcept_no": latest_progress.get("rcept_no", "") if latest_progress else "",
                "progress_plan_title": latest_progress.get("plan_title", "") if latest_progress else "",
                "plan_rcept_dt": latest_plan.get("rcept_dt", "") if latest_plan else "",
                "plan_rcept_no": latest_plan.get("rcept_no", "") if latest_plan else "",
                "progress_similarity": progress_similarity,
                "plan_similarity": plan_similarity,
                "meta_tags": dict(meta_tags),
                "progress_tags": dict(progress_tags),
                "plan_tags": dict(plan_tags),
                "relation": relation,
            }
            rows.append(row)
            details.append({
                **row,
                "meta_main_excerpt": _clean_text(meta_main)[:1000],
                "progress_main_excerpt": _clean_text(progress_main)[:1000],
                "plan_main_excerpt": _clean_text(plan_main)[:1000],
            })

    summary = {
        "meta_filings": len(rows),
        "companies": len({row["ticker"] for row in rows}),
        "relation_counts": dict(Counter(row["relation"] for row in rows)),
        "with_progress": sum(1 for row in rows if row["progress_rcept_no"]),
        "with_plan": sum(1 for row in rows if row["plan_rcept_no"]),
        "avg_progress_similarity": round(sum(row["progress_similarity"] for row in rows) / len(rows), 4) if rows else 0,
        "avg_plan_similarity": round(sum(row["plan_similarity"] for row in rows) / len(rows), 4) if rows else 0,
    }

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUTPUT_DIR / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (OUTPUT_DIR / "details.json").write_text(json.dumps(details, ensure_ascii=False, indent=2), encoding="utf-8")
    with (OUTPUT_DIR / "compare.csv").open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "market", "rank", "ticker", "company",
            "meta_rcept_dt", "meta_rcept_no", "meta_report_nm", "meta_plan_title",
            "progress_rcept_dt", "progress_rcept_no", "progress_plan_title",
            "plan_rcept_dt", "plan_rcept_no",
            "progress_similarity", "plan_similarity", "relation",
            "meta_tags", "progress_tags", "plan_tags",
        ])
        writer.writeheader()
        writer.writerows(rows)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import asyncio

    raise SystemExit(asyncio.run(main()))
