"""DART pblntf_detail_ty 전체 taxonomy 스윕 — 빈 슬롯(미존재 코드) 탐지.

목표는 "어느 기업이냐"가 아니라 "어떤 코드가 존재하나". 다양한 업종 풀에 각 코드를
probe해서 한 곳이라도 데이터가 있으면 '존재', 풀 전체가 0이면 '빈 슬롯'으로 확정.

early-exit: 한 회사에서 데이터 나오면 그 코드는 다음으로 (호출 절약).
빈 코드만 풀 전체를 다 돌게 됨 (= 빈 슬롯 확정).

⚠️ 순차(동시성 1) + sleep, ReadError 즉시 중단 (feedback_dart_retry_amplification).
"""
import asyncio
import re
import sys

from open_proxy_mcp.dart.client import get_dart_client, DartClientError
from open_proxy_mcp.services.company import resolve_company_query

BGN, END = "20150101", "20261231"
SLEEP = 0.3
LETTERS = "ABCDEFGHIJ"
NUMS = range(1, 13)  # 001-012 (관측 최대 J009 + 버퍼)

# 업종 다양화 풀 — 각기 다른 공시 유형을 끌어내도록
POOL = ["삼성전자", "KB금융", "미래에셋증권", "롯데리츠", "신한카드", "두산"]


async def probe(client, cc, code):
    try:
        d = await client._request("list.json", {
            "corp_code": cc, "bgn_de": BGN, "end_de": END,
            "pblntf_detail_ty": code, "page_no": 1, "page_count": 100,
        })
    except DartClientError as e:
        if e.status == "013":
            return 0, []
        raise
    if d.get("status") != "000":
        return 0, []
    lst = d.get("list", [])
    names = list(dict.fromkeys(
        re.sub(r"^\[[^\]]+\]", "", i.get("report_nm", "").strip()).strip() for i in lst))
    return int(d.get("total_count", 0)), names


async def main():
    client = get_dart_client()
    # corp_code 해석
    ccs = {}
    for name in POOL:
        r = await resolve_company_query(name)
        ccs[name] = r.selected["corp_code"]
        print(f"  resolve {name} -> {ccs[name]} ({r.selected.get('corp_name')})", file=sys.stderr)
        await asyncio.sleep(SLEEP)

    print("\n# DART 공시유형 taxonomy 스윕 (존재=★, 빈=·)")
    for L in LETTERS:
        print(f"\n## {L}")
        for n in NUMS:
            code = f"{L}{n:03d}"
            hit_company, total, names = None, 0, []
            for name in POOL:
                t, nm = await probe(client, ccs[name], code)
                await asyncio.sleep(SLEEP)
                if t > 0:
                    hit_company, total, names = name, t, nm
                    break  # early-exit
            if hit_company:
                core = list(dict.fromkeys(
                    re.sub(r"\(.*$", "", re.sub(r"\s*\(\d{4}\.\d{2}\)", "", x)).strip() or x
                    for x in names))[:4]
                print(f"  {code} ★ [{hit_company} {total}건] {core}")
            else:
                print(f"  {code} ·  (빈 슬롯 — 풀 {len(POOL)}사 전체 0)")


if __name__ == "__main__":
    asyncio.run(main())
