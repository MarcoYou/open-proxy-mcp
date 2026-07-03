"""시장 전체(KOSPI·KOSDAQ) 시총가중 trailing 밸류에이션 aggregate.

방식: 시장 PER = Σ시총 ÷ Σ지배순이익 (지수 PER 표준 = 시총가중 조화평균과 동치)
      PBR = Σ시총 ÷ Σ지배자본(MRQ). TTM = FY + 1Q(당해) − 1Q(전년), 지배귀속 account_id.
데이터: KRX 4콜(시세×2 + 종목기본×2) + DART 종목당 3콜(CFS 비면 OFS 폴백 +1).
한도: DART 동시성 1 + sleep 0.45s(분당 ~130, hard rule 준수) · ReadError 즉시 중단 · 재개 가능.
저장: mkt_fundamentals (Supabase, ~2,700행) — 이후 aggregate는 재계산만.

실행: python3 scripts/market_val_agg.py --fetch   # 배치 수집(재개 가능, ~60분)
      python3 scripts/market_val_agg.py --report  # aggregate 산출
"""
import argparse, asyncio, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
import httpx, psycopg

FY = 2025
BAS = "20260702"  # 최근 거래일 스냅샷
DDL = """CREATE TABLE IF NOT EXISTS mkt_fundamentals(
  isu_cd text PRIMARY KEY, corp_code text, mkt text, fs text,
  ni_fy double precision, ni_ttm double precision,
  eq_fy double precision, eq_mrq double precision, fetched text)"""

def num(v):
    try: return float(str(v).replace(",","")) if v not in (None,"","-") else None
    except: return None

async def krx_snapshot():
    key=os.getenv("KRX_API_KEY") or os.getenv("KRX_OPEN_API_KEY")
    from open_proxy_mcp.dart.krx_meter import bump
    listings={}; kinds={}
    async with httpx.AsyncClient(timeout=30) as h:
        for ep,mkt in (("stk_bydd_trd","KOSPI"),("ksq_bydd_trd","KOSDAQ")):
            bump(); r=await h.get(f"https://data-dbg.krx.co.kr/svc/apis/sto/{ep}",headers={"AUTH_KEY":key},params={"basDd":BAS})
            for row in next(v for v in r.json().values() if isinstance(v,list)):
                listings[row["ISU_CD"]]={"mkt":mkt,"cap":num(row.get("MKTCAP")) or 0,"nm":row.get("ISU_NM","")}
        for ep in ("stk_isu_base_info","ksq_isu_base_info"):
            bump(); r=await h.get(f"https://data-dbg.krx.co.kr/svc/apis/sto/{ep}",headers={"AUTH_KEY":key},params={"basDd":BAS})
            for row in next(v for v in r.json().values() if isinstance(v,list)):
                kinds[row["ISU_SRT_CD"]]=row.get("KIND_STKCERT_TP_NM","")
    return listings, kinds

def gid(rows, frag, sj):
    for r in rows:
        if r.get("sj_div") in sj and frag in (r.get("account_id") or "") and str(r.get("thstrm_amount") or "")!="":
            return num(r.get("thstrm_amount"))
    return None

async def fetch():
    from open_proxy_mcp.dart.client import get_dart_client, DartClientError
    con=psycopg.connect(os.environ["DATABASE_URL"]); con.execute(DDL); con.commit()
    done={r[0] for r in con.execute("SELECT isu_cd FROM mkt_fundamentals")}
    listings,kinds=await krx_snapshot()
    commons=[(c,v) for c,v in listings.items() if kinds.get(c)=="보통주"]
    print(f"전 상장 {len(listings)} · 보통주 {len(commons)} · 기수집 {len(done)}",flush=True)
    c=get_dart_client(); i=fail=0
    async def acnt(cc,yr,rc,fs):
        try:
            d=await c.get_fnltt_singl_acnt_all(cc,str(yr),rc,fs)
            return (d.get("list") or []) if isinstance(d,dict) else []
        except DartClientError as e:
            if "[013]" in str(e): return []
            raise
    for code,v in commons:
        if code in done: continue
        i+=1
        try:
            corp=await c.lookup_corp_code(code)
            if not corp:
                con.execute("INSERT INTO mkt_fundamentals(isu_cd,mkt,fetched) VALUES(%s,%s,'nocorp') ON CONFLICT DO NOTHING",(code,v["mkt"])); con.commit(); continue
            cc=corp["corp_code"]; fs="CFS"
            fyr=await acnt(cc,FY,"11011",fs); await asyncio.sleep(0.45)
            if not fyr:
                fs="OFS"; fyr=await acnt(cc,FY,"11011",fs); await asyncio.sleep(0.45)
            qc=await acnt(cc,FY+1,"11013",fs); await asyncio.sleep(0.45)
            qp=await acnt(cc,FY,"11013",fs); await asyncio.sleep(0.45)
            attr="ProfitLossAttributableToOwnersOfParent"; eqa="EquityAttributableToOwnersOfParent"
            ni_fy=gid(fyr,attr,("CIS","IS")) or gid(fyr,"ifrs-full_ProfitLoss",("CIS","IS"))
            ni_c=gid(qc,attr,("CIS","IS")) or gid(qc,"ifrs-full_ProfitLoss",("CIS","IS"))
            ni_p=gid(qp,attr,("CIS","IS")) or gid(qp,"ifrs-full_ProfitLoss",("CIS","IS"))
            ni_ttm=(ni_fy+ni_c-ni_p) if None not in (ni_fy,ni_c,ni_p) else None
            eq_fy=gid(fyr,eqa,("BS",)) or gid(fyr,"ifrs-full_Equity",("BS",))
            eq_mrq=gid(qc,eqa,("BS",)) or gid(qc,"ifrs-full_Equity",("BS",))
            con.execute("""INSERT INTO mkt_fundamentals VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'ok')
                ON CONFLICT (isu_cd) DO NOTHING""",(code,cc,v["mkt"],fs,ni_fy,ni_ttm,eq_fy,eq_mrq))
            con.commit()
        except Exception as e:
            en=type(e).__name__
            if "ReadError" in en or "Connect" in en or "Timeout" in en:
                print(f"네트워크({en}) — 즉시 중단(재개 가능)",flush=True); break
            fail+=1
            con.execute("INSERT INTO mkt_fundamentals(isu_cd,mkt,fetched) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",(code,v["mkt"],f"err:{str(e)[:40]}")); con.commit()
        if i%100==0: print(f"{i}/{len(commons)-len(done)} 처리 (실패 {fail})",flush=True)
    print("fetch 종료",flush=True)

async def report():
    con=psycopg.connect(os.environ["DATABASE_URL"])
    listings,kinds=await krx_snapshot()
    # 우선주 시총 → 보통주 코드(첫5자리+0)로 귀속
    caps={}
    unmapped=0
    for code,v in listings.items():
        kind=kinds.get(code,"")
        if kind=="보통주": caps[code]=caps.get(code,0)+v["cap"]
        elif "우선주" in kind or kind in ("구형우선주","신형우선주"):
            base=code[:5]+"0"
            if base in listings: caps[base]=caps.get(base,0)+v["cap"]
            else: unmapped+=v["cap"]
    rows=con.execute("SELECT isu_cd,mkt,ni_fy,ni_ttm,eq_fy,eq_mrq FROM mkt_fundamentals WHERE fetched='ok'").fetchall()
    from collections import defaultdict
    agg=defaultdict(lambda: dict(cap=0,ni_fy=0,ni_ttm=0,eq=0,n=0,cap_ttm=0,cap_eq=0))
    total_cap=defaultdict(float)
    for code,v in listings.items():
        if kinds.get(code)=="보통주": total_cap[v["mkt"]]+=caps.get(code,v["cap"])
    for code,mkt,nf,nt,ef,em in rows:
        cap=caps.get(code); a=agg[mkt]
        if not cap: continue
        eq=em if em is not None else ef
        if nf is not None: a["ni_fy"]+=nf; a["cap"]+=cap; a["n"]+=1
        if nt is not None: a["ni_ttm"]+=nt; a["cap_ttm"]+=cap
        if eq is not None and eq>0: a["eq"]+=eq; a["cap_eq"]+=cap
    print(f"=== 시장 시총가중 trailing 밸류에이션 (시총 {BAS} · 재무 FY{FY}/TTM) ===")
    for mkt in ("KOSPI","KOSDAQ"):
        a=agg[mkt]; tc=total_cap[mkt]
        if not a["n"]: print(f"[{mkt}] 데이터 없음"); continue
        per_fy=a["cap"]/a["ni_fy"] if a["ni_fy"] else None
        per_ttm=a["cap_ttm"]/a["ni_ttm"] if a["ni_ttm"] else None
        pbr=a["cap_eq"]/a["eq"] if a["eq"] else None
        print(f"\n[{mkt}] 커버 {a['n']}사 · 시총커버리지 {a['cap']/tc*100:.1f}%")
        print(f"  PER  {per_fy:.2f}(FY0) / {per_ttm:.2f}(TTM)")
        print(f"  PBR  {pbr:.2f}(MRQ)")
        print(f"  Σ시총 {a['cap']/1e12:,.0f}조 · Σ지배순이익(TTM) {a['ni_ttm']/1e12:,.1f}조 · Σ지배자본 {a['eq']/1e12:,.0f}조")
    if unmapped: print(f"\n(우선주 미매핑 시총 {unmapped/1e12:.1f}조 — 제외)")
    con.close()

if __name__=="__main__":
    ap=argparse.ArgumentParser(); ap.add_argument("--fetch",action="store_true"); ap.add_argument("--report",action="store_true")
    a=ap.parse_args()
    asyncio.run(fetch() if a.fetch else report())
