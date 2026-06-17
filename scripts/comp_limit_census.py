"""코스피 시총 상위 100사 — 2026 정기주총 이사 보수한도 변경 방향(상향/유지/하향) 통계."""
import warnings as W; W.filterwarnings("ignore")
import asyncio, os, sys, io, json, re, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
for line in open(".env.local", encoding="utf-8"):
    if line.startswith("DART_API_KEY="):
        os.environ["OPENDART_API_KEY"] = line.split("=",1)[1].strip()
from open_proxy_mcp.services.company import resolve_company_query
from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload

T = 1e8  # 억원


def director_direction(comp):
    items = comp.get("items", []) or []
    dirs = [it for it in items
            if it.get("target") != "감사" and "감사" not in re.sub(r"\s", "", it.get("title", ""))]
    for it in dirs:
        cur = (it.get("current") or {}).get("limitAmount")
        pri = (it.get("prior") or {}).get("limitAmount")
        if cur is not None and pri is not None:
            d = "상향" if cur > pri else ("하향" if cur < pri else "유지")
            return d, cur, pri, it.get("title", "")
    if dirs:
        return "판정불가", None, None, dirs[0].get("title", "")
    return "안건없음", None, None, ""


async def one(rec):
    name = rec["name"]
    _PRIO = {"상향": 3, "유지": 3, "하향": 3, "판정불가": 2, "안건없음": 1}

    async def _attempt(mtype):
        p = await build_shareholder_meeting_payload(name, scope="compensation", year=2026, meeting_type=mtype)
        d = p.get("data", {}); sm = d.get("selected_meeting", {}) or {}; comp = d.get("compensation", {}) or {}
        direction, cur, pri, title = director_direction(comp)
        return {"result": direction, "meeting_type": sm.get("meeting_type"),
                "meeting_date": sm.get("datetime") or sm.get("rcept_dt"),
                "cur_eok": round(cur/T, 1) if cur else None, "pri_eok": round(pri/T, 1) if pri else None,
                "parse_status": comp.get("summary", {}).get("parse_status"), "title": title}

    try:
        # 같은 2026 연도에 소집공고/결의·정정 등 복수 공시가 있어 모드별 선택이 엇갈림 →
        # annual·auto 둘 다 시도해 '방향 확정(상향/유지/하향)'된 결과를 우선 채택.
        a = await _attempt("annual")
        if _PRIO.get(a["result"], 0) < 3:
            b = await _attempt("auto")
            if _PRIO.get(b["result"], 0) > _PRIO.get(a["result"], 0):
                a = b
        return {**rec, **a}
    except Exception as e:
        return {**rec, "result": f"에러:{type(e).__name__}"}


async def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    top = json.load(open("sample_universe/_ks_top100.json", encoding="utf-8"))[:n]
    res, t0 = [], time.monotonic()
    for i in range(0, len(top), 10):
        res.extend(await asyncio.gather(*(one(x) for x in top[i:i+10])))
        print(f"  ... {min(i+10,len(top))}/{len(top)} ({time.monotonic()-t0:.0f}s)")
    if n <= 12:
        for r in res:
            print(f"  {r['name']:14s} {r['result']:6s} 당기={r.get('cur_eok')} 전기={r.get('pri_eok')} "
                  f"[{r.get('meeting_type')}/{r.get('parse_status')}] {r.get('title','')[:24]}")
    json.dump(res, open("sample_universe/_comp_census.json","w",encoding="utf-8"), ensure_ascii=False, indent=2)
    print("저장: sample_universe/_comp_census.json")


asyncio.run(main())
