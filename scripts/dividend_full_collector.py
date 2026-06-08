"""종합 배당 collector — 사업/반기/분기보고서(alotMatter) + 배당결정 공시.

회사별로 수집:
- Full-year 보통주/우선주 DPS·배당총액 (사업보고서 11011 다년컬럼)
- 분기별 보통주/우선주 DPS·총액 (분기/반기 누적 차분: Q1=11013, Q2=반기-Q1, Q3=3분기-반기, 결산=연간-3분기)
- 날짜: 배당기준일(record_date, 항상 존재) + 지급일(분기/중간)
- 무배당 분기/연도(차분=0) + 특별배당(배당결정 has_special) 감지

정확도 우선. rate limit: 회사당 ~10콜, 순차+sleep, 공유 client(limiter).
"""
import asyncio
import re
import sys

from open_proxy_mcp.dart.client import get_dart_client, DartClientError
from open_proxy_mcp.services.company import resolve_company_query
from open_proxy_mcp.services.dividend_v2 import _search_dividend_filings, _decision_details, _effective_decisions, _bucket_fiscal_year, _quarter_label
from open_proxy_mcp.tools.dividend import _parse_dividend_items

SLEEP = 0.35


def _col(items, col):
    """alotMatter items의 한 컬럼(current/previous/before_previous)에서 종합 추출."""
    out = {"dps_common": 0, "dps_pref": 0, "total_mil": 0, "payout": None, "yield_c": None, "exists": False}
    for it in items:
        cat = it.get("category", ""); sk = it.get("stock_type", ""); raw = it.get(col, "")
        v = re.sub(r"[,\s]", "", str(raw))
        num = int(v) if v.lstrip("-").isdigit() else None
        if "주당액면가액" in cat and num and num > 0:
            out["exists"] = True
        if "주당 현금배당금" in cat:
            if "우선주" in sk:
                out["dps_pref"] = num or 0
            elif "보통주" in sk or (num and num > 0):
                out["dps_common"] = num or 0
        elif "현금배당금총액" in cat:
            out["total_mil"] = num or 0
        elif "현금배당성향" in cat:
            f = _f(raw)
            if f and f > 0 and (out["payout"] is None or "연결" in cat):
                out["payout"] = f
        elif "현금배당수익률" in cat and "우선주" not in sk:
            f = _f(raw)
            if f and f > 0:
                out["yield_c"] = f
        elif "당기순이익" in cat and "연결" in cat and num:
            out["exists"] = True
    return out


def _f(raw):
    try:
        return float(re.sub(r"[,\s]", "", str(raw)))
    except (ValueError, TypeError):
        return None


async def collect(client, name, year):
    r = await resolve_company_query(name)
    cc = r.selected["corp_code"]
    # 1) 4개 보고서 alotMatter (당해년도 누적 + 연간)
    reports = {}
    for rc in ("11013", "11012", "11014", "11011"):
        try:
            data = await client.get_dividend_info(cc, str(year), rc)
            reports[rc] = _col(_parse_dividend_items(data), "current")
        except DartClientError:
            reports[rc] = None
        await asyncio.sleep(SLEEP)
    annual = reports.get("11011")
    # 2) 분기별 차분 (보통/우선 DPS + 총액)
    def cum(rc, key):
        c = reports.get(rc)
        return c[key] if c and c.get("exists") else None
    quarters = []
    c1, c2, c3 = cum("11013", "dps_common"), cum("11012", "dps_common"), cum("11014", "dps_common")
    p1, p2, p3 = cum("11013", "dps_pref"), cum("11012", "dps_pref"), cum("11014", "dps_pref")
    t1, t2, t3 = cum("11013", "total_mil"), cum("11012", "total_mil"), cum("11014", "total_mil")
    ann_c = annual["dps_common"] if annual and annual.get("exists") else None
    ann_p = annual["dps_pref"] if annual and annual.get("exists") else None
    ann_t = annual["total_mil"] if annual and annual.get("exists") else None
    def q(label, dc, dp, tt):
        quarters.append({"q": label, "dps_common": dc, "dps_pref": dp, "total_mil": tt})
    if c1 is not None:
        q("Q1", c1, p1, t1)
    if c1 is not None and c2 is not None:
        q("Q2", c2 - c1, (p2 or 0) - (p1 or 0), (t2 or 0) - (t1 or 0))
    if c2 is not None and c3 is not None:
        q("Q3", c3 - c2, (p3 or 0) - (p2 or 0), (t3 or 0) - (t2 or 0))
    if c3 is not None and ann_c is not None:
        q("결산", ann_c - c3, (ann_p or 0) - (p3 or 0), (ann_t or 0) - (t3 or 0))
    # 3) 배당결정 공시 → 기준일/지급일/배당구분/특별 (당해 fiscal year)
    div_filings, _rec, _n, _e = await _search_dividend_filings(cc, year, year)
    await asyncio.sleep(SLEEP)
    decision_filings = [f for f in div_filings if "배당결정" in (f.get("report_nm") or "") and "자회사" not in (f.get("report_nm") or "")]
    details = await _decision_details(decision_filings) if decision_filings else []
    yr_dec = _effective_decisions([d for d in details if _bucket_fiscal_year(d) == year])
    events = [{
        "구분": d.get("dividend_type"), "q": _quarter_label(d),
        "기준일": d.get("record_date"), "지급일": d.get("payment_date") or "미정",
        "dps_c": d.get("dps_common"), "특별": bool(d.get("has_special")),
    } for d in sorted(yr_dec, key=lambda x: x.get("record_date") or "")]
    return {"name": name, "year": year, "annual": annual, "quarters": quarters, "events": events}


async def main(samples):
    client = get_dart_client()
    for name, year in samples:
        try:
            res = await collect(client, name, year)
        except Exception as e:
            print(f"\n### {name} {year}: ERROR {type(e).__name__}: {str(e)[:60]}")
            if "ReadError" in type(e).__name__:
                print("!! ReadError 중단"); break
            await asyncio.sleep(0.5); continue
        a = res["annual"]
        print(f"\n### {res['name']} (FY{res['year']})")
        if a and a.get("exists"):
            print(f"  연간: 보통 DPS={a['dps_common']:,} 우선 DPS={a['dps_pref']:,} 총액={a['total_mil']:,}백만 성향={a['payout']}% 시가율={a['yield_c']}%")
        else:
            print("  연간: (alotMatter 없음/무배당)")
        qsum_c = sum(q["dps_common"] for q in res["quarters"]) if res["quarters"] else None
        print(f"  분기별({len(res['quarters'])}): " + " | ".join(
            f"{q['q']} 보통{q['dps_common']:,}{'/우선'+format(q['dps_pref'],',') if q['dps_pref'] else ''}" for q in res["quarters"]
        ) + (f"  [분기합 보통={qsum_c:,}]" if qsum_c is not None else ""))
        # 정합성: 분기합 보통 == 연간 보통?
        if qsum_c is not None and a and a.get("exists"):
            ok = "✓" if qsum_c == a["dps_common"] else f"✗(연간{a['dps_common']:,})"
            print(f"  정합성: 분기합={qsum_c:,} {ok}")
        for e in res["events"]:
            sp = " [특별]" if e["특별"] else ""
            print(f"     {e['구분']} {e['q']} 기준일={e['기준일']} 지급일={e['지급일']} DPS={e['dps_c']}{sp}")
        await asyncio.sleep(0.4)


if __name__ == "__main__":
    SAMPLES = [
        ("삼성전자", 2024), ("현대차", 2024), ("POSCO홀딩스", 2024), ("KT&G", 2024),
        ("고려아연", 2024), ("SK이노베이션", 2023), ("삼성바이오로직스", 2024),
        ("에이피알", 2025), ("미래에셋증권", 2024), ("메리츠금융지주", 2024),
    ]
    asyncio.run(main(SAMPLES))
