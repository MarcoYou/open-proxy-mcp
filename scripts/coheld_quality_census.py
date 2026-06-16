"""공동보유자 파서 품질 전수조사 — 분쟁 엣지(경영권분쟁) 140사 + 일반 top 포함 200+사.
불변식(보고자본인+특관 합 ≈ 헤드라인)으로 파서 정합률 측정. before/after 품질 비교용.
usage: uv run python scripts/coheld_quality_census.py [N]
"""
import warnings as W; W.filterwarnings("ignore")
import asyncio, csv, json, os, sys, io, re, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
for line in open(".env.local", encoding="utf-8"):
    if line.startswith("DART_API_KEY="):
        os.environ["OPENDART_API_KEY"] = line.split("=",1)[1].strip()
from open_proxy_mcp.services.company import resolve_company_query
from open_proxy_mcp.services.filing_search import search_filings_by_report_name
from open_proxy_mcp.dart.client import get_dart_client
from open_proxy_mcp.services.holder_table import parse_holder_table, holder_table_total

def universe():
    names, seen = [], set()
    # 1) 분쟁 엣지(경영권 분쟁) 우선
    for f in ("260607_kospi_dispute_universe.csv", "260607_kosdaq_dispute_universe.csv"):
        for row in csv.DictReader(open(f"wiki/architecture/audits/data/{f}", encoding="utf-8")):
            n = row["company"]
            if n not in seen: seen.add(n); names.append((n, "dispute"))
    # 2) 일반 top (KS/KQ) 보강 → 200+
    for jf in ("sample_universe/_ks_top100.json", "sample_universe/_kq_top100.json"):
        try:
            for r in json.load(open(jf, encoding="utf-8")):
                n = r["name"]
                if n not in seen: seen.add(n); names.append((n, "general"))
        except Exception: pass
    return names

async def one(name, tag):
    try:
        c = get_dart_client()
        r = await resolve_company_query(name)
        if not r.selected: return {"name": name, "tag": tag, "err": "resolve"}
        items, _, _ = await search_filings_by_report_name(
            corp_code=r.selected["corp_code"], bgn_de="20250101", end_de="20260620",
            pblntf_tys="", keywords=["대량보유상황보고서"], max_pages=4)
        items = [i for i in items if i.get("rcept_no")]
        if not items: return {"name": name, "tag": tag, "no_report": True}
        items.sort(key=lambda x: (("약식" in (x.get("report_nm") or "")), -int(x.get("rcept_dt") or 0)))
        rc = items[0]["rcept_no"]
        html = (await c.get_document_cached(rc)).get("html", "")
        parsed = parse_holder_table(html)
        flat = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))
        hm = re.search(r"이번\s*보고서\s*[\d,]+\s*(\d+\.\d+)", flat)
        headline = float(hm.group(1)) if hm else None
        out = {"name": name, "tag": tag, "rcept": rc, "headline": headline}
        if not parsed: out["parse"] = "fail"; return out
        out["format"] = parsed["format"]
        if parsed["format"] != "일반": out["parse"] = "no_table"; return out
        out["parse"] = "ok"
        out["self_name"] = parsed["self"]["name"]
        out["summed"] = holder_table_total(parsed)
        out["n_related"] = len(parsed["related"])
        # 이름 품질: '호'·헤더오염·빈이름 등 의심 토큰
        bad = [h["name"] for h in [parsed["self"]] + parsed["related"]
               if not h["name"] or h["name"] in ("호",) or h["name"].startswith("주수") or len(h["name"]) <= 1]
        out["bad_names"] = bad
        if headline is not None and out["summed"] is not None:
            out["invariant_ok"] = abs(out["summed"] - headline) <= max(headline*0.05, 0.5)
        return out
    except Exception as e:
        return {"name": name, "tag": tag, "err": f"{type(e).__name__}"}

async def main():
    names = universe()
    n = int(sys.argv[1]) if len(sys.argv) > 1 else len(names)
    names = names[:n]
    print(f"전수조사 {len(names)}사 (분쟁 {sum(1 for _,t in names if t=='dispute')} + 일반 {sum(1 for _,t in names if t=='general')})")
    res, t0 = [], time.monotonic()
    for i in range(0, len(names), 16):
        res.extend(await asyncio.gather(*(one(nm, tg) for nm, tg in names[i:i+16])))
        d = min(i+16, len(names))
        if d % 64 == 0 or d == len(names): print(f"  ... {d}/{len(names)} ({time.monotonic()-t0:.0f}s)")
    okp = [r for r in res if r.get("parse")=="ok"]
    inv = [r for r in okp if "invariant_ok" in r]
    inv_ok = [r for r in inv if r["invariant_ok"]]
    badn = [r for r in okp if r.get("bad_names")]
    print(f"\n파싱 ok {len(okp)} / no_table {sum(1 for r in res if r.get('parse')=='no_table')} / fail {sum(1 for r in res if r.get('parse')=='fail')} / 보고서없음 {sum(1 for r in res if r.get('no_report'))} / err {sum(1 for r in res if r.get('err'))}")
    print(f"불변식 통과: {len(inv_ok)}/{len(inv)} ({len(inv_ok)/max(len(inv),1)*100:.1f}%)")
    print(f"이름 품질 의심(bad_names) 보유: {len(badn)}사")
    for r in badn[:12]: print(f"   {r['name']}: {r['bad_names'][:4]}")
    json.dump(res, open("wiki/architecture/audits/data/260616_coheld_quality_census.json","w",encoding="utf-8"), ensure_ascii=False, indent=2)
    print("저장: 260616_coheld_quality_census.json")
asyncio.run(main())
