"""DART pblntf_ty / pblntf_detail_ty 코드 → 실제 공시명 매핑 디스커버리.

6개 회사(삼성전자/고려아연/삼성바이오로직스/알테오젠/파두/셀트리온)에 대해
실존 detail 코드별로 report_nm 샘플을 수집한다.
list.json 응답에는 detail 코드 필드가 없으므로, 코드를 파라미터로 넣어 역으로 매핑.

⚠️ DART throttle 방지: 완전 순차(동시성 1) + 호출 사이 sleep + ReadError 즉시 중단.
   버스트/재시도 증폭 금지 (feedback_dart_retry_amplification).
"""
import asyncio
import json
import sys
from collections import defaultdict

from open_proxy_mcp.dart.client import get_dart_client, DartClientError

COMPANIES = {
    "삼성전자": "00126380",
    "고려아연": "00102858",
    "삼성바이오로직스": "00877059",
    "알테오젠": "00989619",
    "파두": "01292291",
    "셀트리온": "00413046",
}
BGN, END = "20150101", "20261231"
SLEEP = 0.3  # 호출 사이 간격 (순차)

# 6사 실증으로 확정된 실존 코드.
# C001/C002/C004는 유상증자·CB·합병 이력 있는 회사(삼바/고려아연/셀트리온)에서만 발생 →
# 1차 디스커버리(삼성전자·알테오젠 2사)에선 누락됐다가 직접 probe로 보강.
# G(펀드)·H(자산유동화)는 6사 전체 0건 — 사업회사 유니버스 밖이라 부재 확정.
KNOWN = ['A001', 'A002', 'A003', 'B001', 'C001', 'C002', 'C004',
         'D001', 'D002', 'D003',
         'E001', 'E002', 'E003', 'E004', 'E005', 'E006', 'F001', 'F002',
         'I001', 'I002', 'I003', 'I004', 'J001', 'J004', 'J009']


async def probe(client, corp_code, code):
    """(code, corp_code) → (total_count, [report_nm 전체]).

    [013] 데이터 없음 = 정상 0건. rate/auth 에러나 ReadError는 re-raise (중단)."""
    try:
        d = await client._request("list.json", {
            "corp_code": corp_code, "bgn_de": BGN, "end_de": END,
            "pblntf_detail_ty": code, "page_no": 1, "page_count": 100,
        })
    except DartClientError as exc:
        if exc.status == "013":  # 조회된 데이터가 없습니다 = 0건 (정상)
            return 0, []
        raise  # 사용한도 초과(020/021) 등은 즉시 중단
    if d.get("status") != "000":
        return 0, []
    lst = d.get("list", [])
    names = list(dict.fromkeys(i.get("report_nm", "").strip() for i in lst))
    return int(d.get("total_count", 0)), names


async def main():
    client = get_dart_client()
    companies = list(COMPANIES)
    mapping = {}
    total = len(KNOWN) * len(companies)
    done = 0

    for code in KNOWN:
        per = {}
        allnames = defaultdict(int)
        for cname in companies:
            r = await probe(client, COMPANIES[cname], code)  # ReadError 나면 즉시 예외→중단
            done += 1
            if r[0] > 0:
                per[cname] = {"count": r[0], "names": r[1][:10]}
                for nm in r[1]:
                    allnames[nm] += 1
            await asyncio.sleep(SLEEP)
        mapping[code] = {
            "per_company": per,
            "report_names": dict(sorted(allnames.items(), key=lambda x: -x[1])),
        }
        cos = ",".join(per.keys())
        print(f"  [{done}/{total}] {code}: {len(per)}/6사 ({cos})", file=sys.stderr)

    out = {"window": f"{BGN}~{END}", "companies": companies, "codes": mapping}
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
