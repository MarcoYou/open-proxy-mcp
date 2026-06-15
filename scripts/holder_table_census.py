"""5% 대량보유보고서 '보고자/특별관계자 합계표' 파서 + 전수 성능 검증.

목적: proxy_contest 5% signal의 actor_side 재분류(공동보유 탐지) + 본인/합산 분리를 위한
파서를 만들고, 분쟁 유니버스 전수로 성능을 검증한다. (Phase 1 probe로 구조 확정 후 작성)

합계표 구조 (12사 probe 확정):
  주수 비율 보고자 [이름] [ID] …숫자… [합계주수] [비율] 특별관계자 [이름] [ID] … ※ 소유에 준하는
  ID = 생년월일 6자리 | 사업자번호 XXX-XX-XXXXX

검증 불변식: 보고자 + 특관 비율 합 ≈ 헤드라인 보유비율(있으면).
usage: uv run python scripts/holder_table_census.py [N]
raw: wiki/architecture/audits/data/260615_holder_table_census.json
"""
import warnings as W
W.filterwarnings("ignore")
import asyncio, csv, json, os, sys, io, re, time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
for line in open(".env.local", encoding="utf-8"):
    if line.startswith("DART_API_KEY="):
        os.environ["OPENDART_API_KEY"] = line.split("=", 1)[1].strip()

from open_proxy_mcp.services.company import resolve_company_query
from open_proxy_mcp.services.filing_search import search_filings_by_report_name
from open_proxy_mcp.dart.client import get_dart_client

# ID: 생년월일 6자리 | 사업자번호(하이픈) | 법인·고유번호(하이픈 없는 5~13자리, 이탄에쿼티
# 53541 / 백운조합 6758003138 실측). 이름엔 숫자가 없어 이름 뒤 첫 숫자런이 항상 ID.
_ID = r"(?:\d{3}-\d{2,3}-\d{4,5}|\d{5,13})"
# 한 행: [이름(한글/영문/공백/괄호/·)] [ID] [숫자/-/콤마 토큰들] → 마지막 비율(X.XX) 직전이 합계주수
_ROW = re.compile(
    r"([가-힣A-Za-z()ㄱ-ㆎ·,.&\s]{1,40}?)\s+(" + _ID + r")\s+"
    r"((?:[\d,]+|-|0)(?:\s+(?:[\d,]+|-|0))*)\s+([\d,]+|-)\s+(\d+\.\d+|-)"
)


def parse_holder_table(html: str) -> dict | None:
    flat = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
    # 정정본: 합계표가 2번 → 마지막(정정후) 사용
    anchors = [m.start() for m in re.finditer(r"주수\s*비율\s*보고자", flat)]
    if not anchors:
        # 합계표 마커 자체가 없음 = 약식(기관 단순투자 등) — 특별관계자 분해 대상 아님(예상된 한계)
        return {"format": "no_table", "self": None, "related": []}
    seg = flat[anchors[-1]: anchors[-1] + 4000]
    seg = re.split(r"※\s*소유에\s*준하는|제\d부\s|주\d\)", seg)[0]
    # 보고자/특별관계자 라벨 제거 후 행 파싱
    seg2 = seg.replace("보고자", " ").replace("특별관계자", " ")
    holders = []
    for m in _ROW.finditer(seg2):
        name = re.sub(r"\s+", " ", m.group(1)).strip(" ,")
        pct_raw = m.group(5)  # 비율 (group4는 합계주수)
        pct = 0.0 if pct_raw == "-" else float(pct_raw)
        holders.append({"name": name, "pct": pct})
    if not holders:
        return None
    return {"format": "일반", "self": holders[0], "related": holders[1:]}


async def census_one(name):
    try:
        c = get_dart_client()
        r = await resolve_company_query(name)
        if not r.selected:
            return {"name": name, "err": "resolve"}
        items, _, _ = await search_filings_by_report_name(
            corp_code=r.selected["corp_code"], bgn_de="20250101", end_de="20260620",
            pblntf_tys="", keywords=["대량보유상황보고서"], max_pages=4)
        # 경영참여 우선이 이상적이나 보고서명으론 구분 불가 → 최신 일반보고서 우선(약식 후순위)
        items = [i for i in items if i.get("rcept_no")]
        if not items:
            return {"name": name, "no_report": True}
        items.sort(key=lambda x: (("약식" in (x.get("report_nm") or "")), -int(x.get("rcept_dt") or 0)))
        rc = items[0]["rcept_no"]
        html = (await c.get_document_cached(rc)).get("html", "")
        parsed = parse_holder_table(html)
        # 헤드라인 보유비율 (불변식 검증용)
        flat = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
        hm = re.search(r"이번\s*보고서\s*[\d,]+\s*(\d+\.\d+)", flat)
        headline = float(hm.group(1)) if hm else None
        out = {"name": name, "rcept": rc, "report_nm": items[0].get("report_nm", ""), "headline_pct": headline}
        if not parsed:
            out["parse"] = "fail"
            return out
        out["format"] = parsed["format"]
        if parsed["format"] == "no_table":
            out["parse"] = "no_table"  # 약식/기관 단순투자 — 합계표 없음(예상)
            return out
        out["self_name"] = parsed["self"]["name"]
        out["self_pct"] = parsed["self"]["pct"]
        out["n_related"] = len(parsed["related"])
        summed = round(parsed["self"]["pct"] + sum(x["pct"] for x in parsed["related"]), 2)
        out["summed_pct"] = summed
        out["parse"] = "ok"
        if headline is not None:
            out["invariant_ok"] = abs(summed - headline) <= max(headline * 0.05, 0.3)
        return out
    except Exception as e:
        return {"name": name, "err": f"{type(e).__name__}: {e}"}


async def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 9999
    # 분쟁 유니버스 (kospi+kosdaq)
    names = []
    for f in ("260607_kospi_dispute_universe.csv", "260607_kosdaq_dispute_universe.csv"):
        for row in csv.DictReader(open(f"wiki/architecture/audits/data/{f}", encoding="utf-8")):
            names.append(row["company"])
    seen = set(); names = [x for x in names if not (x in seen or seen.add(x))][:n]
    print(f"분쟁 유니버스 {len(names)}사 — 합계표 파서 전수 검증")

    res, t0 = [], time.monotonic()
    for i in range(0, len(names), 16):
        res.extend(await asyncio.gather(*(census_one(x) for x in names[i:i+16])))
        done = min(i+16, len(names))
        if done % 48 == 0 or done == len(names):
            print(f"  ... {done}/{len(names)} ({time.monotonic()-t0:.0f}s)")

    err = [r for r in res if r.get("err")]
    norep = [r for r in res if r.get("no_report")]
    done = [r for r in res if r.get("parse")]
    okp = [r for r in done if r["parse"] == "ok"]
    yakshik = [r for r in done if r["parse"] == "no_table"]
    failp = [r for r in done if r["parse"] == "fail"]
    inv = [r for r in okp if "invariant_ok" in r]
    inv_ok = [r for r in inv if r["invariant_ok"]]
    print(f"\n조회 {len(res)} / 에러 {len(err)} / 보고서없음 {len(norep)}")
    print(f"파싱: ok {len(okp)} / no_table(약식) {len(yakshik)} / fail {len(failp)}")
    print(f"불변식(합≈헤드라인): {len(inv_ok)}/{len(inv)} ({len(inv_ok)/max(len(inv),1)*100:.1f}%)")
    print("\n불변식 실패 샘플:")
    for r in [r for r in inv if not r["invariant_ok"]][:12]:
        print(f"  {r['name']}: 합 {r['summed_pct']} vs 헤드라인 {r['headline_pct']} ({r['n_related']}특관)")
    print("\n파싱 fail 샘플:", [r["name"] for r in failp][:12])
    if err: print("에러 샘플:", [(r["name"], r["err"][:30]) for r in err][:6])

    json.dump(res, open("wiki/architecture/audits/data/260615_holder_table_census.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print("\n저장: wiki/architecture/audits/data/260615_holder_table_census.json")


asyncio.run(main())
