"""시장 전체(KOSPI·KOSDAQ) 시총가중 trailing 밸류에이션 aggregate.

방식: 시장 PER = Σ시총 ÷ Σ지배순이익 (지수 PER 표준 = 시총가중 조화평균과 동치)
      PBR = Σ시총 ÷ Σ지배자본(MRQ). TTM = FY + 1Q(당해) − 1Q(전년), 지배귀속 account_id.
데이터: KRX 4콜(시세×2 + 종목기본×2) + DART 종목당 3콜(CFS 비면 OFS 폴백 +1).
한도: DART 동시성 1 + sleep 0.45s(분당 ~130, hard rule 준수) · ReadError 즉시 중단 · 재개 가능.
저장: mkt_fundamentals (Supabase, ~2,700행) — 이후 aggregate는 재계산만.

실행: python3 scripts/market_val_agg.py --fetch   # 배치 수집(재개 가능, ~60분) — 분기 갱신용(현역)
      python3 scripts/market_val_agg.py --report  # aggregate 산출

⚠ deprecated(260705, QA): --report/--snapshot aggregate는 비KRW 22사 FX 미환산 — KOSDAQ PER ~5.7%
  왜곡 실측. 스냅샷 저장 정본 = scripts/market_val_weekly.py(FX 환산). 이 스크립트는 --fetch
  (mkt_fundamentals 분기 재수집)만 계속 사용.
"""
import argparse, asyncio, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
import httpx, psycopg
from open_proxy_mcp.services.scale_guard import gid_exact, assess as scale_assess, MARKET_MAX_NI_ANCHOR

FY = 2025
def latest_trading_day():
    """최근 12일 중 KRX 데이터 있는 최신 거래일 — 호출부에서 결정."""
    from datetime import date, timedelta
    return [(date.today()-timedelta(days=i)).strftime("%Y%m%d") for i in range(12)]
BAS = "20260702"  # 배치(fetch) 스냅샷 기준일 (재무 수집용 고정)
DDL = """CREATE TABLE IF NOT EXISTS mkt_fundamentals(
  isu_cd text PRIMARY KEY, corp_code text, mkt text, fs text,
  ni_fy double precision, ni_ttm double precision,
  eq_fy double precision, eq_mrq double precision, fetched text)"""
DDL_MIGRATE = ("ALTER TABLE mkt_fundamentals ADD COLUMN IF NOT EXISTS scale_flag text",)

def num(v):
    try: return float(str(v).replace(",","")) if v not in (None,"","-") else None
    except: return None

async def krx_snapshot(bas=None):
    key=os.getenv("KRX_API_KEY") or os.getenv("KRX_OPEN_API_KEY")
    from open_proxy_mcp.dart.krx_meter import bump
    listings={}; kinds={}
    async with httpx.AsyncClient(timeout=30) as h:
        for ep,mkt in (("stk_bydd_trd","KOSPI"),("ksq_bydd_trd","KOSDAQ")):
            bump(); r=await h.get(f"https://data-dbg.krx.co.kr/svc/apis/sto/{ep}",headers={"AUTH_KEY":key},params={"basDd":bas or BAS})
            for row in next(v for v in r.json().values() if isinstance(v,list)):
                listings[row["ISU_CD"]]={"mkt":mkt,"cap":num(row.get("MKTCAP")) or 0,"nm":row.get("ISU_NM","")}
        for ep in ("stk_isu_base_info","ksq_isu_base_info"):
            bump(); r=await h.get(f"https://data-dbg.krx.co.kr/svc/apis/sto/{ep}",headers={"AUTH_KEY":key},params={"basDd":bas or BAS})
            for row in next(v for v in r.json().values() if isinstance(v,list)):
                kinds[row["ISU_SRT_CD"]]=row.get("KIND_STKCERT_TP_NM","")
    return listings, kinds

def gid(rows, account_id, sj, field="thstrm_amount"):
    """정확일치(exact) — substring(in) 금지(260704 실측: 접두어 충돌로 오탐 확인, wiki §9)."""
    return gid_exact(rows, account_id, sj, field)

async def fetch():
    from open_proxy_mcp.dart.client import get_dart_client, DartClientError
    con=psycopg.connect(os.environ["DATABASE_URL"]); con.execute(DDL)
    for stmt in DDL_MIGRATE: con.execute(stmt)
    con.commit()
    done={r[0] for r in con.execute("SELECT isu_cd FROM mkt_fundamentals")}
    listings,kinds=await krx_snapshot()
    commons=[(c,v) for c,v in listings.items() if kinds.get(c)=="보통주"]
    print(f"전 상장 {len(listings)} · 보통주 {len(commons)} · 기수집 {len(done)}",flush=True)
    # 시장 내 실측 최댓값(scale_guard.MARKET_MAX_NI_ANCHOR=삼성전자) — 회사규모 안 가리고 작동.
    # DB 현재값과 큰 쪽 사용하되 scale_flag 있는(이미 이상치로 걸린) 행은 제외해 자기오염 방지.
    db_max = con.execute("SELECT MAX(ni_fy) FROM mkt_fundamentals WHERE fetched='ok' AND scale_flag IS NULL").fetchone()[0]
    market_max_ni = max(MARKET_MAX_NI_ANCHOR, db_max or 0)
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
            attr="ifrs-full_ProfitLossAttributableToOwnersOfParent"; eqa="ifrs-full_EquityAttributableToOwnersOfParent"
            ni_fy=gid(fyr,attr,("CIS","IS")) or gid(fyr,"ifrs-full_ProfitLoss",("CIS","IS"))
            ni_fy_frmtrm=gid(fyr,attr,("CIS","IS"),"frmtrm_amount")
            ni_c=gid(qc,attr,("CIS","IS")) or gid(qc,"ifrs-full_ProfitLoss",("CIS","IS"))
            ni_p=gid(qp,attr,("CIS","IS")) or gid(qp,"ifrs-full_ProfitLoss",("CIS","IS"))
            ni_ttm=(ni_fy+ni_c-ni_p) if None not in (ni_fy,ni_c,ni_p) else None
            eq_fy=gid(fyr,eqa,("BS",)) or gid(fyr,"ifrs-full_Equity",("BS",))
            eq_mrq=gid(qc,eqa,("BS",)) or gid(qc,"ifrs-full_Equity",("BS",))
            # 실시간 스케일 가드(소프트센 032680 사례, wiki §9) — 항등식은 총자본(지배+비지배) 기준
            assets_fy=gid(fyr,"ifrs-full_Assets",("BS",)); liab_fy=gid(fyr,"ifrs-full_Liabilities",("BS",))
            eq_total_fy=gid(fyr,"ifrs-full_Equity",("BS",))
            verdict=scale_assess(thstrm=ni_fy, frmtrm=ni_fy_frmtrm, assets=assets_fy, liabilities=liab_fy,
                                  equity=eq_total_fy, mktcap=v["cap"], market_max=market_max_ni)
            scale_flag = ",".join(verdict["hard_hit"]) if verdict["tier"]=="hard" else (
                ",".join(verdict["soft_hit"]) if verdict["tier"]=="soft" else None)
            if verdict["tier"]=="hard":
                print(f"[가드] {code} 스케일오류 감지({scale_flag}) — ni/eq 무효화",flush=True)
                ni_fy=ni_ttm=eq_fy=eq_mrq=None
            con.execute("""INSERT INTO mkt_fundamentals(isu_cd,corp_code,mkt,fs,ni_fy,ni_ttm,eq_fy,eq_mrq,fetched,scale_flag)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'ok',%s)
                ON CONFLICT (isu_cd) DO NOTHING""",(code,cc,v["mkt"],fs,ni_fy,ni_ttm,eq_fy,eq_mrq,scale_flag))
            con.commit()
        except Exception as e:
            en=type(e).__name__
            if "ReadError" in en or "Connect" in en or "Timeout" in en:
                print(f"네트워크({en}) — 즉시 중단(재개 가능)",flush=True); break
            fail+=1
            con.execute("INSERT INTO mkt_fundamentals(isu_cd,mkt,fetched) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING",(code,v["mkt"],f"err:{str(e)[:40]}")); con.commit()
        if i%100==0: print(f"{i}/{len(commons)-len(done)} 처리 (실패 {fail})",flush=True)
    print("fetch 종료",flush=True)

async def report(store=False):
    con=psycopg.connect(os.environ["DATABASE_URL"])
    listings=kinds=None; used=None
    for d in latest_trading_day():
        listings,kinds=await krx_snapshot(d)
        if listings: used=d; break
    if not listings: print("KRX 스냅샷 실패"); return
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
        if ef is not None and ef>0: a["eq_fy"]=a.get("eq_fy",0)+ef; a["cap_eqf"]=a.get("cap_eqf",0)+cap
    print(f"=== 시장 시총가중 trailing 밸류에이션 (시총 {used} · 재무 FY{FY}/TTM) ===")
    for mkt in ("KOSPI","KOSDAQ"):
        a=agg[mkt]; tc=total_cap[mkt]
        if not a["n"]: print(f"[{mkt}] 데이터 없음"); continue
        per_fy=a["cap"]/a["ni_fy"] if a["ni_fy"] else None
        per_ttm=a["cap_ttm"]/a["ni_ttm"] if a["ni_ttm"] else None
        pbr=a["cap_eq"]/a["eq"] if a["eq"] else None
        pbr_fy=a.get("cap_eqf",0)/a["eq_fy"] if a.get("eq_fy") else None
        print(f"\n[{mkt}] 커버 {a['n']}사 · 시총커버리지 {a['cap']/tc*100:.1f}%")
        print(f"  PER  {per_fy:.2f}(FY0) / {per_ttm:.2f}(TTM)")
        print(f"  PBR  {pbr_fy:.2f}(FY0) / {pbr:.2f}(MRQ)")
        print(f"  Σ시총 {a['cap']/1e12:,.0f}조 · Σ지배순이익(TTM) {a['ni_ttm']/1e12:,.1f}조 · Σ지배자본 {a['eq']/1e12:,.0f}조")
    if unmapped: print(f"\n(우선주 미매핑 시총 {unmapped/1e12:.1f}조 — 제외)")
    if store:
        con.execute("""CREATE TABLE IF NOT EXISTS mkt_val_history(
          snap_dd text, mkt text, per_fy0 double precision, per_ttm double precision,
          pbr_fy0 double precision, pbr_mrq double precision,
          cap double precision, ni_ttm double precision, eq double precision,
          PRIMARY KEY(snap_dd, mkt))""")
        for mkt in ("KOSPI","KOSDAQ"):
            a=agg[mkt]
            if not a["n"]: continue
            con.execute("""INSERT INTO mkt_val_history
              (snap_dd,mkt,per_fy0,per_ttm,pbr_fy0,pbr_mrq,cap,ni_ttm,eq) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
              ON CONFLICT (snap_dd,mkt) DO NOTHING""",
              (used, mkt,
               a["cap"]/a["ni_fy"] if a["ni_fy"] else None,
               a["cap_ttm"]/a["ni_ttm"] if a["ni_ttm"] else None,
               a.get("cap_eqf",0)/a["eq_fy"] if a.get("eq_fy") else None,
               a["cap_eq"]/a["eq"] if a["eq"] else None,
               a["cap"], a["ni_ttm"], a["eq"]))
        con.commit()
        print(f"(mkt_val_history 저장: {used})")
    con.close()

if __name__=="__main__":
    ap=argparse.ArgumentParser(); ap.add_argument("--fetch",action="store_true"); ap.add_argument("--report",action="store_true"); ap.add_argument("--snapshot",action="store_true")
    a=ap.parse_args()
    asyncio.run(fetch() if a.fetch else report(store=a.snapshot))
