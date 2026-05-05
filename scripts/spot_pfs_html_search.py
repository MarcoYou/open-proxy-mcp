"""sparse 케이스 raw html 직접 검색 — 매출액/영업이익/당기순이익 값 위치 찾기.

parse_provisional 이 빈 값 주는 케이스에서, raw html에는 정말 값이 없는지 확인.
"""

from __future__ import annotations
import asyncio
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from open_proxy_mcp.dart.client import get_dart_client  # noqa: E402
from open_proxy_mcp.services.filing_search import search_filings_by_report_name  # noqa: E402


KEYS = ["매출액", "영업이익", "당기순이익", "자산총계", "부채총계", "자본총계"]


async def main(ticker: str):
    client = get_dart_client()
    corp = await client.lookup_corp_code(ticker)
    if not corp:
        print(f"no_corp: {ticker}")
        return
    filings, _, error = await search_filings_by_report_name(
        corp_code=corp["corp_code"], bgn_de="20260101", end_de="20261231",
        pblntf_tys="E", keywords=("주주총회소집공고",))
    if error:
        print(f"search_error: {error}")
        return
    agm = [f for f in filings if "주주총회소집공고" in (f.get("report_nm") or "")]
    agm.sort(key=lambda r: (r.get("rcept_dt", ""), r.get("rcept_no", "")))
    rcept_no = agm[-1]["rcept_no"]
    print(f"=== {corp['corp_name']} {ticker} ===")
    print(f"rcept_no: {rcept_no}")

    doc = await client.get_document_cached(rcept_no)
    html = doc.get("html", "") or ""
    text = doc.get("text", "") or ""
    print(f"html_len: {len(html)} text_len: {len(text)}")

    # 표 개수
    tables = re.findall(r'<table[^>]*>.*?</table>', html, re.DOTALL | re.IGNORECASE)
    print(f"\nHTML 안 <table> 개수: {len(tables)}")

    # 각 keyword 발견 위치 (raw text)
    print(f"\nKeyword 발견 (raw text 기준):")
    for kw in KEYS:
        positions = [m.start() for m in re.finditer(re.escape(kw), text)]
        print(f"  {kw}: {len(positions)}개 위치 — first {positions[:3]}")

    # 매출액 주변 ±200 chars context (첫 5건)
    print(f"\n매출액 주변 context (첫 3건):")
    for i, m in enumerate(re.finditer("매출액", text)):
        if i >= 3:
            break
        s, e = max(0, m.start() - 100), min(len(text), m.end() + 200)
        snippet = text[s:e].replace("\n", " | ").strip()
        print(f"  [{i+1}] ...{snippet}...")

    # 잠정 재무제표 / 요약 재무제표 구분 위치
    print(f"\n구역 markers:")
    for marker in ["잠정 재무제표", "잠정재무제표", "요약 재무제표", "요약재무제표",
                   "재무제표 승인", "재무상태표", "포괄손익계산서", "손익계산서"]:
        positions = [m.start() for m in re.finditer(re.escape(marker), text)]
        print(f"  {marker}: {len(positions)}개 — {positions[:3]}")


if __name__ == "__main__":
    ticker = sys.argv[1] if len(sys.argv) > 1 else "005380"
    asyncio.run(main(ticker))
