"""코스닥 시총 상위 100사 — 2026 정기주총 주주제안 안건 탐지 + 가결 여부."""
import warnings as W; W.filterwarnings("ignore")
import asyncio, os, sys, io, json, re, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
for line in open(".env.local", encoding="utf-8"):
    if line.startswith("DART_API_KEY="):
        os.environ["OPENDART_API_KEY"] = line.split("=",1)[1].strip()
from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload

def walk(nodes):
    for n in nodes or []:
        yield n
        yield from walk(n.get("children"))

async def agenda_scan(rec, mtype):
    name = rec["name"]
    p = await build_shareholder_meeting_payload(name, scope="summary", year=2026, meeting_type=mtype)
    d = p.get("data", {}); sm = d.get("selected_meeting", {}) or {}
    ag = list(walk(d.get("agendas", [])))
    # 버그 수정 후: proposer_type가 canonical "shareholder_proposal"로 통일됨 → clean 검출.
    props = [n for n in ag if n.get("proposer_type") == "shareholder_proposal"]
    return {"n_agenda": len(ag), "props": [n.get("title","") for n in props],
            "meeting_type": sm.get("meeting_type"), "meeting_date": sm.get("datetime") or sm.get("rcept_dt")}

async def one(rec):
    try:
        a = await agenda_scan(rec, "annual")
        # 안건이 비면(공고 미선택) auto로 보강
        if a["n_agenda"] == 0:
            b = await agenda_scan(rec, "auto")
            if b["n_agenda"] > 0:
                a = b
        return {**rec, **a}
    except Exception as e:
        return {**rec, "n_agenda": -1, "props": [], "err": f"{type(e).__name__}"}

async def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    top = json.load(open("sample_universe/_kq_top100.json", encoding="utf-8"))[:n]
    res, t0 = [], time.monotonic()
    for i in range(0, len(top), 10):
        res.extend(await asyncio.gather(*(one(x) for x in top[i:i+10])))
        print(f"  ... {min(i+10,len(top))}/{len(top)} ({time.monotonic()-t0:.0f}s)")
    withp = [r for r in res if r.get("props")]
    zero = [r for r in res if r.get("n_agenda") == 0]
    err = [r for r in res if r.get("n_agenda") == -1]
    print(f"\n주주제안 보유: {len(withp)}사 / 안건0(공고미확보): {len(zero)} / 에러: {len(err)}")
    for r in withp:
        print(f"  · {r['name']}: {r['props']}")
    if zero: print("안건0:", [r['name'] for r in zero])
    if err: print("에러:", [(r['name'], r.get('err')) for r in err][:8])
    json.dump(res, open("sample_universe/_kq_proposal_scan.json","w",encoding="utf-8"), ensure_ascii=False, indent=2)

asyncio.run(main())
