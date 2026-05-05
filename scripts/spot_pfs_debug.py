"""PFS sparse 케이스 debug — 1 회사 doc fetch + parse_provisional_financial_statement raw 검사.

DART 호출 2회 (1 회사). 매우 가벼움.

사용법:
    uv run python scripts/spot_pfs_debug.py 005380   # 현대차
"""

from __future__ import annotations
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from open_proxy_mcp.dart.client import get_dart_client  # noqa: E402
from open_proxy_mcp.services.filing_search import search_filings_by_report_name  # noqa: E402
from open_proxy_mcp.services.provisional_financial_statement import (  # noqa: E402
    extract_metrics, parse_provisional_financial_statement,
)


async def main(ticker: str):
    client = get_dart_client()
    corp = await client.lookup_corp_code(ticker)
    if not corp:
        print(f"no_corp: {ticker}")
        return
    corp_code = corp["corp_code"]
    print(f"corp: {corp}")

    filings, _, error = await search_filings_by_report_name(
        corp_code=corp_code, bgn_de="20260101", end_de="20261231",
        pblntf_tys="E", keywords=("주주총회소집공고",))
    if error:
        print(f"search_error: {error}")
        return
    agm = [f for f in filings if "주주총회소집공고" in (f.get("report_nm") or "")]
    if not agm:
        print("no_agm")
        return
    agm.sort(key=lambda r: (r.get("rcept_dt", ""), r.get("rcept_no", "")))
    rcept_no = agm[-1]["rcept_no"]
    print(f"rcept_no: {rcept_no} report_nm: {agm[-1].get('report_nm')}")

    doc = await client.get_document_cached(rcept_no)
    html = doc.get("html", "")
    print(f"html len: {len(html)}")

    parsed = parse_provisional_financial_statement(html)
    metrics = extract_metrics(parsed)

    print(f"\n--- metrics ---")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))

    for scope_key in ("consolidated", "separate"):
        scope = parsed.get(scope_key) or {}
        for stmt_key in ("balance_sheet", "income_statement"):
            stmt = scope.get(stmt_key)
            if not stmt:
                print(f"\n{scope_key}.{stmt_key}: None")
                continue
            cols = stmt.get("columns") or []
            unit = stmt.get("unit") or ""
            rows = stmt.get("rows") or []
            print(f"\n{scope_key}.{stmt_key}: unit={unit} cols={cols} n_rows={len(rows)}")
            for row in rows[:25]:
                print(f"  {row}")


if __name__ == "__main__":
    ticker = sys.argv[1] if len(sys.argv) > 1 else "005380"
    asyncio.run(main(ticker))
