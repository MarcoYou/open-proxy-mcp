"""KOSPI+KOSDAQ 역추적 분쟁 종목 재검토 — 공시 내용으로 진짜 분쟁 추출 (260607).

소송 키워드 hit 중:
- 경영권분쟁소송 / 공개매수 / 의결권대리행사권유 = 진짜 경영권 분쟁
- "일정금액이상의청구" / 일반 손배소 = 단순 상거래 소송 (분쟁 아님)

종목별로 공시명을 받아 분류 → 진짜 분쟁 신호 점수화.
"""
from __future__ import annotations
import argparse
import asyncio
import csv
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from open_proxy_mcp.dart.client import get_dart_client  # noqa: E402
from open_proxy_mcp.services.company import resolve_company_query  # noqa: E402

# 진짜 경영권 분쟁 신호
_REAL_DISPUTE = ("경영권분쟁소송", "경영권변경", "공개매수", "의결권대리행사권유", "위임장")
# 단순 상거래 (분쟁 아님)
_COMMERCIAL = ("일정금액이상의청구", "일정금액이상의청구ㆍ소송")

_QUARTERS = [("0101", "0331"), ("0401", "0630"), ("0701", "0930"), ("1001", "1231")]


def _dedup_name(name: str) -> str:
    """정정 마커 제거 후 핵심 공시명."""
    for m in ("[기재정정]", "[첨부정정]", "[정정]"):
        name = name.replace(m, "")
    return name.strip()


async def _classify_one(ticker: str, company: str, years: list[int], sem: asyncio.Semaphore) -> dict:
    async with sem:
        t0 = time.time()
        try:
            res = await asyncio.wait_for(resolve_company_query(company), timeout=30.0)
            if not res.selected:
                # ticker로 재시도
                res = await resolve_company_query(ticker)
            if not res.selected:
                return {"ticker": ticker, "company": company, "status": "no_match"}
            corp_code = res.selected["corp_code"]
        except Exception as exc:
            return {"ticker": ticker, "company": company, "status": "error", "error": str(exc)[:80]}

        client = get_dart_client()
        real_disputes: set[str] = set()
        commercial: set[str] = set()
        proxy_solicit = 0
        tender_offer = 0
        mgmt_litigation = 0
        commercial_litigation = 0

        for ty in ("B", "I"):
            for year in years:
                for q_start, q_end in _QUARTERS:
                    try:
                        data = await client.search_filings(
                            corp_code=corp_code, pblntf_ty=ty,
                            bgn_de=f"{year}{q_start}", end_de=f"{year}{q_end}",
                            page_no=1, page_count=100,
                        )
                    except Exception:
                        continue
                    for it in data.get("list", []) or []:
                        raw = it.get("report_nm", "")
                        name = _dedup_name(raw)
                        if "경영권분쟁소송" in name:
                            real_disputes.add(name)
                            mgmt_litigation += 1
                        elif "공개매수" in name:
                            real_disputes.add(name); tender_offer += 1
                        elif "의결권대리행사권유" in name or "위임장" in name:
                            real_disputes.add(name); proxy_solicit += 1
                        elif "경영권변경" in name:
                            real_disputes.add(name)
                        elif any(c in name for c in _COMMERCIAL):
                            commercial.add(name); commercial_litigation += 1

        # 진짜 분쟁 점수: 경영권소송/공개매수/위임장 각 가중
        real_score = mgmt_litigation + tender_offer * 2 + proxy_solicit * 2
        is_real_dispute = real_score > 0

        return {
            "ticker": ticker, "company": company, "status": "ok",
            "duration_s": round(time.time() - t0, 2),
            "is_real_dispute": is_real_dispute,
            "real_score": real_score,
            "mgmt_litigation": mgmt_litigation,
            "tender_offer": tender_offer,
            "proxy_solicit": proxy_solicit,
            "commercial_litigation": commercial_litigation,
            "real_dispute_kinds": sorted(real_disputes)[:6],
            "commercial_kinds": sorted(commercial)[:3],
        }


async def _run(args):
    rows = []
    seen = set()
    for p in args.csvs:
        with open(p) as f:
            for r in csv.DictReader(f):
                if r["ticker"] not in seen:
                    seen.add(r["ticker"])
                    rows.append(r)
    if args.limit:
        rows = rows[:args.limit]

    print(f"재검토 n={len(rows)} (years={args.years})")
    sem = asyncio.Semaphore(args.concurrency)
    results = []
    for cs in range(0, len(rows), 20):
        chunk = rows[cs:cs+20]
        cr = await asyncio.gather(*[_classify_one(r["ticker"], r["company"], args.years, sem) for r in chunk])
        results.extend(cr)
        ok = [r for r in results if r.get("status") == "ok"]
        real = sum(1 for r in ok if r.get("is_real_dispute"))
        print(f"  done {cs+len(chunk)}/{len(rows)} — 진짜 분쟁 {real}")
        if cs + 20 < len(rows):
            await asyncio.sleep(1.5)

    out = ROOT / "wiki/architecture/audits/data/260607_dispute_reclassified.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")

    ok = [r for r in results if r.get("status") == "ok"]
    real = [r for r in ok if r.get("is_real_dispute")]
    fake = [r for r in ok if not r.get("is_real_dispute")]
    real.sort(key=lambda r: r["real_score"], reverse=True)
    print(f"\n=== 재검토 결과 (n={len(ok)} ok) ===")
    print(f"  진짜 경영권 분쟁: {len(real)} ({100*len(real)/len(ok) if ok else 0:.0f}%)")
    print(f"  단순 상거래 소송만 (분쟁 아님): {len(fake)}")
    print(f"\n  [진짜 분쟁 — 점수순]")
    for r in real:
        kinds = ", ".join(k[:16] for k in r["real_dispute_kinds"][:3])
        print(f"    {r['company']:<16} {r['ticker']} 점수{r['real_score']} "
              f"(경영권소송{r['mgmt_litigation']} 공개매수{r['tender_offer']} 위임장{r['proxy_solicit']}) | {kinds}")
    print(f"\n  [단순 상거래만 (제외 대상) {len(fake)}]")
    for r in fake[:15]:
        print(f"    {r['company']:<16} {r['ticker']} 상거래소송{r['commercial_litigation']}")
    print(f"\n  saved: {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csvs", nargs="+", default=[
        "wiki/architecture/audits/data/260607_kospi_dispute_universe.csv",
        "wiki/architecture/audits/data/260607_kosdaq_dispute_universe.csv",
    ])
    ap.add_argument("--years", type=int, nargs="+", default=[2024, 2025])
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--concurrency", type=int, default=3)
    args = ap.parse_args()
    asyncio.run(_run(args))
