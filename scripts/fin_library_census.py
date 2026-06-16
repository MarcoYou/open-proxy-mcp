"""금융사 보수한도 단일-library 양식 전수 — library 개수 + 보수 파싱 상태."""
import warnings as W; W.filterwarnings("ignore")
import asyncio, os, sys, io, json, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
for line in open(".env.local", encoding="utf-8"):
    if line.startswith("DART_API_KEY="):
        os.environ["OPENDART_API_KEY"] = line.split("=",1)[1].strip()
from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload
from open_proxy_mcp.dart.client import get_dart_client
from bs4 import BeautifulSoup
c = get_dart_client()
async def one(f):
    name=f["name"]
    try:
        p = await build_shareholder_meeting_payload(name, scope="compensation", year=2026, meeting_type="annual")
        d=p.get("data",{}); rc=(d.get("selected_meeting") or {}).get("notice_rcept_no","")
        comp=d.get("compensation",{}) or {}
        ps=comp.get("summary",{}).get("parse_status")
        n_items=len(comp.get("items",[]))
        nlib=None
        if rc:
            html=(await c.get_document_cached(rc)).get("html","") or ""
            nlib=len(BeautifulSoup(html,"lxml").find_all("library"))
        return {"name":name,"sector":f["sector"],"n_library":nlib,"comp_status":ps,"comp_items":n_items}
    except Exception as e:
        return {"name":name,"sector":f["sector"],"err":f"{type(e).__name__}"}
async def main():
    fins=json.load(open("sample_universe/_fin.json",encoding="utf-8"))
    res,t0=[],time.monotonic()
    for i in range(0,len(fins),10):
        res.extend(await asyncio.gather(*(one(f) for f in fins[i:i+10])))
        print(f"  ... {min(i+10,len(fins))}/{len(fins)} ({time.monotonic()-t0:.0f}s)")
    print(f"\n{'회사':16s} {'업종':4s} lib  보수상태")
    single=[]; multi=[]
    for r in sorted(res,key=lambda x:(x.get('n_library') or 99)):
        if r.get("err"): print(f"  {r['name']:16s} ERR {r['err']}"); continue
        print(f"  {r['name']:16s} {r['sector']:4s} {str(r['n_library']):>3s}  {r['comp_status']} ({r['comp_items']}건)")
        (single if r['n_library']==1 else multi).append(r)
    print(f"\n단일 library(1개): {len(single)}사  / 다중: {len(multi)}사")
    print("단일+amount_unparsed:", [r['name'] for r in single if r['comp_status']=='amount_unparsed'])
    print("단일인데 ok:", [r['name'] for r in single if r['comp_status']=='ok'])
    json.dump(res,open("wiki/architecture/audits/data/260617_fin_library_census.json","w",encoding="utf-8"),ensure_ascii=False,indent=2)
asyncio.run(main())
