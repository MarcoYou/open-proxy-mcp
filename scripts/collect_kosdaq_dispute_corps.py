"""KOSDAQ 전체에서 분쟁 신호 공시 뜬 종목 역추적 수집 (260607).

corp_code 없는 검색은 3개월 제한 → 분기별 + 페이지 순회.
분쟁 공시 유형:
- B (주요사항): 소송등의제기/경영권분쟁소송
- I (기타): 의결권대리행사권유 / 공개매수
- D (지분): 주식등의대량보유상황보고 (5% — 경영참여만 후속 필터)

수집된 corp는 proxy_contest 본조사 universe로 사용.
"""
from __future__ import annotations
import argparse
import asyncio
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from open_proxy_mcp.dart.client import get_dart_client  # noqa: E402

_DISPUTE_KEYWORDS = ("소송", "경영권", "분쟁", "위임장", "공개매수", "대량보유")
_QUARTERS = [
    ("0101", "0331"), ("0401", "0630"), ("0701", "0930"), ("1001", "1231"),
]


async def _scan(client, corp_cls: str, pblntf_ty: str, year: int, max_pages: int) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for q_start, q_end in _QUARTERS:
        bgn, end = f"{year}{q_start}", f"{year}{q_end}"
        for page in range(1, max_pages + 1):
            try:
                data = await client.search_filings(
                    corp_cls=corp_cls, pblntf_ty=pblntf_ty,
                    bgn_de=bgn, end_de=end, page_no=page, page_count=100,
                )
            except Exception as exc:
                print(f"    {pblntf_ty} {bgn} p{page} err: {str(exc)[:50]}")
                break
            items = data.get("list", []) or []
            if not items:
                break
            for it in items:
                name = it.get("report_nm", "")
                if not any(k in name for k in _DISPUTE_KEYWORDS):
                    continue
                sc = (it.get("stock_code") or "").strip()
                if not sc:
                    continue
                rec = found.setdefault(sc, {
                    "ticker": sc, "company": it.get("corp_name", ""),
                    "corp_code": it.get("corp_code", ""), "hit_count": 0, "kinds": set(),
                })
                rec["hit_count"] += 1
                rec["kinds"].add(name[:18])
            total_pages = (int(data.get("total_count", 0)) + 99) // 100
            if page >= total_pages:
                break
    return found


async def _run(args):
    client = get_dart_client()
    merged: dict[str, dict] = {}
    # 공시유형: B(소송 주요사항) / I(위임장·공개매수). D(5% 대량보유)는 일상적이라 제외 —
    # 진짜 분쟁(소송전·표대결) 종목만 타겟. 5% 동학은 본조사 proxy_contest가 어차피 잡음.
    scan_types = args.types.split(",")
    market_label = {"Y": "KOSPI", "K": "KOSDAQ", "N": "KONEX"}.get(args.corp_cls, args.corp_cls)
    for year in args.years:
        print(f"\n=== {year} {market_label} 분쟁 공시 스캔 ===")
        for ty in scan_types:
            res = await _scan(client, args.corp_cls, ty, year, args.max_pages)
            print(f"  {ty}공시: {len(res)} 종목 hit")
            for sc, rec in res.items():
                m = merged.setdefault(sc, {
                    "ticker": sc, "company": rec["company"],
                    "corp_code": rec["corp_code"], "hit_count": 0, "kinds": set(),
                })
                m["hit_count"] += rec["hit_count"]
                m["kinds"] |= rec["kinds"]

    # 기존 universe 제외
    if args.exclude_universe:
        have = set()
        for p in ["260506_universe_kospi_200.csv", "260506_universe_kosdaq_300.csv"]:
            with open(ROOT / f"wiki/architecture/audits/data/{p}") as f:
                have |= set(r["ticker"] for r in csv.DictReader(f))
        new_corps = {k: v for k, v in merged.items() if k not in have}
    else:
        new_corps = merged

    ranked = sorted(new_corps.values(), key=lambda r: r["hit_count"], reverse=True)
    print(f"\n=== 결과 ===")
    print(f"  분쟁 공시 종목 총: {len(merged)} / 출력: {len(new_corps)}")
    print(f"\n  [hit 많은 분쟁 종목 상위 25]")
    for r in ranked[:25]:
        print(f"    {r['company']:<16} {r['ticker']} hit={r['hit_count']} {sorted(r['kinds'])[:2]}")

    out = ROOT / f"wiki/architecture/audits/data/{args.out}"
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["ticker", "company", "hit_count"])
        for r in ranked:
            w.writerow([r["ticker"], r["company"], r["hit_count"]])
    print(f"\n  saved: {out} ({len(ranked)} 종목)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, nargs="+", default=[2024, 2025])
    ap.add_argument("--max-pages", type=int, default=10)
    ap.add_argument("--types", default="B,I", help="공시유형 (B=소송주요사항 / I=위임장공개매수 / D=5%대량보유)")
    ap.add_argument("--corp-cls", default="K", help="시장 (Y=KOSPI / K=KOSDAQ / N=KONEX)")
    ap.add_argument("--out", default="260607_kosdaq_dispute_universe.csv", help="출력 csv 파일명")
    ap.add_argument("--exclude-universe", action="store_true", help="기존 500 universe 제외")
    args = ap.parse_args()
    asyncio.run(_run(args))
