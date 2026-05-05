"""Step 0 — 퇴직금 안건 본문 spot 측정."""
from __future__ import annotations
import asyncio, csv, json, sys
from pathlib import Path

ROOT = Path("/Users/marcoyou/Projects/open-proxy-mcp")
sys.path.insert(0, str(ROOT))

from open_proxy_mcp.dart.client import DartClient
from open_proxy_mcp.tools.parser import parse_retirement_pay_xml


async def find_with_retirement(ticker_name_pairs, year, max_found=10, max_try=120):
    client = DartClient()
    found = []
    tried = 0
    sem = asyncio.Semaphore(2)

    async def try_one(ticker, name):
        nonlocal tried
        async with sem:
            tried += 1
            try:
                results = await client.search_filings_by_ticker(
                    ticker=ticker, bgn_de=f"{year-1}1201", end_de=f"{year}0501", pblntf_ty="E"
                )
                agms = [f for f in (results or {}).get("list", []) if "주주총회소집공고" in (f.get("report_nm") or "")]
                if not agms:
                    return None
                f = agms[0]
                rcept_no = f.get("rcept_no")
                doc = await client.get_document(rcept_no)
                text = doc.get("text", "") or ""
                html = doc.get("html", "") or ""
                if "퇴직금" not in text and "퇴직금" not in html and "퇴임위로금" not in text and "퇴임위로금" not in html:
                    return None
                res = parse_retirement_pay_xml(html)
                amends = res.get("amendments") or []
                marker = "✓" if amends else "✗"
                print(f"  {marker} {ticker} {name}: amendments={len(amends)} (rcept_no={rcept_no})", flush=True)
                return {
                    "ticker": ticker, "name": name, "rcept_no": rcept_no,
                    "amendments": amends, "parser_miss": not amends,
                }
            except Exception as e:
                print(f"  ! {ticker} {name}: {type(e).__name__}: {str(e)[:80]}", flush=True)
                return None

    for ticker, name in ticker_name_pairs:
        if len(found) >= max_found or tried >= max_try:
            break
        r = await try_one(ticker, name)
        if r:
            found.append(r)
    return found


def load_universe():
    pairs = []
    for fp in [
        ROOT / "wiki/architecture/audits/data/260503_universe_200.csv",
        ROOT / "wiki/architecture/audits/data/260504_proxy_advise_framework/kosdaq_top50.csv",
    ]:
        if fp.exists():
            with fp.open() as f:
                for row in csv.DictReader(f):
                    pairs.append((row["ticker"], row["company"]))
    return pairs


async def main():
    pairs = load_universe()
    print(f"# universe: {len(pairs)} companies")
    found = await find_with_retirement(pairs, year=2026, max_found=10, max_try=120)
    out = ROOT / "wiki/architecture/audits/data/260505_compensation_retirement/iter01_retirement_spot.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(found, ensure_ascii=False, indent=2))
    print(f"\n# found: {len(found)} (parser_miss: {sum(1 for f in found if f['parser_miss'])}) → {out}")
    print("\n## 변경 사례:")
    for r in found:
        if r["parser_miss"]:
            continue
        print(f"\n### {r['ticker']} {r['name']}")
        for i, a in enumerate(r["amendments"][:3]):
            print(f"  [{i+1}] clause={a.get('clause')!r}")
            print(f"      before: {(a.get('before') or '').strip()[:200]}")
            print(f"      after:  {(a.get('after') or '').strip()[:200]}")
            if a.get("reason"):
                print(f"      reason: {a['reason'][:120]}")


if __name__ == "__main__":
    asyncio.run(main())
