"""19 sparse PFS 케이스 fix 후 재측정 (Phase 4 검증).

19 회사 × 2 DART calls = ~38 calls. 안전.
"""

from __future__ import annotations
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from open_proxy_mcp.dart.client import get_dart_client  # noqa: E402
from open_proxy_mcp.services.filing_search import search_filings_by_report_name  # noqa: E402
from open_proxy_mcp.services.provisional_financial_statement import (  # noqa: E402
    extract_metrics, parse_provisional_financial_statement,
)


SPARSE_CASES = [
    # KOSPI sparse cases (16)
    ("005380", "현대차"),
    ("034020", "두산에너빌리티"),
    ("068270", "셀트리온"),
    ("064350", "현대로템"),
    ("000720", "현대건설"),
    ("024110", "기업은행"),
    ("003550", "LG"),
    ("030200", "KT"),
    ("003490", "대한항공"),
    ("011070", "LG이노텍"),
    ("000100", "유한양행"),
    ("241560", "두산밥캣"),
    ("454910", "두산로보틱스"),
    ("010060", "OCI홀딩스"),
    ("011210", "현대위아"),
    ("012630", "HDC"),
    # KOSDAQ sparse cases (3)
    ("222800", "심텍"),
    ("060370", "LS마린솔루션"),
    ("048410", "현대바이오"),
]


async def _audit_one(ticker: str, name: str, sem: asyncio.Semaphore) -> dict:
    async with sem:
        client = get_dart_client()
        await asyncio.sleep(1.0)
        corp = await client.lookup_corp_code(ticker)
        if not corp:
            return {"ticker": ticker, "name": name, "status": "no_corp"}
        filings, _, error = await search_filings_by_report_name(
            corp_code=corp["corp_code"], bgn_de="20260101", end_de="20261231",
            pblntf_tys="E", keywords=("주주총회소집공고",))
        if error:
            return {"ticker": ticker, "name": name, "status": f"search_err:{error}"}
        agm = [f for f in filings if "주주총회소집공고" in (f.get("report_nm") or "")]
        if not agm:
            return {"ticker": ticker, "name": name, "status": "no_agm"}
        agm.sort(key=lambda r: (r.get("rcept_dt", ""), r.get("rcept_no", "")))
        rcept_no = agm[-1]["rcept_no"]
        await asyncio.sleep(0.5)
        doc = await client.get_document_cached(rcept_no)
        html = doc.get("html", "") or ""
        if not html:
            return {"ticker": ticker, "name": name, "status": "no_html"}
        parsed = parse_provisional_financial_statement(html)
        metrics = extract_metrics(parsed)
        filled = sum(1 for k, v in metrics.items() if k.startswith("fy_") and v not in (None, ""))
        return {
            "ticker": ticker, "name": name, "status": "ok",
            "extraction_status": metrics.get("extraction_status"),
            "scope_used": metrics.get("scope_used"),
            "filled": filled,
        }


async def main():
    sem = asyncio.Semaphore(2)
    t0 = time.time()
    results = await asyncio.gather(*[_audit_one(t, n, sem) for t, n in SPARSE_CASES])
    print(f"[done in {round(time.time()-t0, 1)}s]\n")
    print(f"{'ticker':>8} {'name':<25} status   ext.status  scope         filled  pass")
    print("-" * 80)
    pass_count = 0
    for r in results:
        if r["status"] != "ok":
            print(f"{r['ticker']:>8} {r['name']:<25} {r['status']}")
            continue
        marker = "✓" if r["filled"] >= 6 else "✗"
        if r["filled"] >= 6:
            pass_count += 1
        print(f"{r['ticker']:>8} {r['name']:<25} ok       "
              f"{(r['extraction_status'] or ''):8}    "
              f"{(r['scope_used'] or 'None'):13} {r['filled']:2}     {marker}")
    print(f"\n{pass_count}/{len(results)} passed (filled ≥ 6)")


if __name__ == "__main__":
    asyncio.run(main())
