"""시장 밸류에이션 분기 시계열 — PIT(공시 접수 근사) 기준 시장 PER/PBR 추이.

시총: krx_weekly(주간, 2015-12~) 재활용 → KRX 콜 0.
재무: FY2020~2024 지배순이익·지배자본 배치(mkt_fund_hist) — DART 종목당 5콜, 총 ~13k콜.
PIT 근사: 분기말 D 시점 최신 확정재무 = (D가 4월 이후면 전년 FY, 아니면 전전년 FY)
          — 사업보고서 3월 중순 공시 규칙의 연 단위 근사(look-ahead 방지).

실행: python3 scripts/market_val_series.py --fetch   # 과거 FY 배치(재개 가능, ~100분)
      python3 scripts/market_val_series.py --series  # 분기 시계열 산출
"""
import argparse, asyncio, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
import psycopg

YEARS = [2020, 2021, 2022, 2023, 2024]
DDL = """CREATE TABLE IF NOT EXISTS mkt_fund_hist(
  isu_cd text, fy int, fs text, ni double precision, eq double precision,
  fetched text, PRIMARY KEY(isu_cd, fy))"""

def num(v):
    try: return float(str(v).replace(",","")) if v not in (None,"","-") else None
    except: return None

def gid(rows, frag, sj):
    for r in rows:
        if r.get("sj_div") in sj and frag in (r.get("account_id") or "") and str(r.get("thstrm_amount") or "")!="":
            return num(r.get("thstrm_amount"))
    return None

async def fetch():
    from open_proxy_mcp.dart.client import get_dart_client, DartClientError
    con=psycopg.connect(os.environ["DATABASE_URL"]); con.execute(DDL); con.commit()
    firms=[r for r in con.execute("SELECT isu_cd, corp_code FROM mkt_fundamentals WHERE fetched='ok' ORDER BY isu_cd")]
    done={(r[0],r[1]) for r in con.execute("SELECT isu_cd, fy FROM mkt_fund_hist")}
    todo=[(i,c,y) for i,c in firms for y in YEARS if (i,y) not in done]
    print(f"대상 {len(firms)}사 × {len(YEARS)}년 · 남은 {len(todo)}건",flush=True)
    c=get_dart_client(); k=0
    async def acnt(cc,yr,fs):
        try:
            d=await c.get_fnltt_singl_acnt_all(cc,str(yr),"11011",fs)
            return (d.get("list") or []) if isinstance(d,dict) else []
        except DartClientError as e:
            if "[013]" in str(e): return []
            raise
    for isu,cc,yr in todo:
        k+=1
        try:
            fs="CFS"; rows=await acnt(cc,yr,fs); await asyncio.sleep(0.45)
            if not rows:
                fs="OFS"; rows=await acnt(cc,yr,fs); await asyncio.sleep(0.45)
            attr="ProfitLossAttributableToOwnersOfParent"; eqa="EquityAttributableToOwnersOfParent"
            ni=gid(rows,attr,("CIS","IS")) or gid(rows,"ifrs-full_ProfitLoss",("CIS","IS"))
            eq=gid(rows,eqa,("BS",)) or gid(rows,"ifrs-full_Equity",("BS",))
            st="ok" if (ni is not None or eq is not None) else "nodata"
            con.execute("INSERT INTO mkt_fund_hist VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                        (isu,yr,fs,ni,eq,st))
            con.commit()
        except Exception as e:
            en=type(e).__name__
            if "ReadError" in en or "Connect" in en or "Timeout" in en:
                print(f"네트워크({en}) — 중단(재개 가능)",flush=True); break
            con.execute("INSERT INTO mkt_fund_hist VALUES(%s,%s,NULL,NULL,NULL,%s) ON CONFLICT DO NOTHING",
                        (isu,yr,f"err:{str(e)[:30]}")); con.commit()
        if k%200==0: print(f"{k}/{len(todo)}",flush=True)
    print("fetch 종료",flush=True)

def series():
    con=psycopg.connect(os.environ["DATABASE_URL"])
    # 분기말 후보: krx_weekly에서 각 분기 마지막 주간일
    qs=con.execute("""SELECT MAX(bas_dd) FROM krx_weekly
      GROUP BY LEFT(bas_dd,4), CASE WHEN SUBSTRING(bas_dd,5,2)::int<=3 THEN 1
        WHEN SUBSTRING(bas_dd,5,2)::int<=6 THEN 2 WHEN SUBSTRING(bas_dd,5,2)::int<=9 THEN 3 ELSE 4 END
      ORDER BY 1""").fetchall()
    qdates=[r[0] for r in qs if r[0]>="20210601"]
    # 재무: FY별 (mkt_fund_hist ∪ 최신 FY2025는 mkt_fundamentals)
    fin={}
    for isu,fy,ni,eq in con.execute("SELECT isu_cd,fy,ni,eq FROM mkt_fund_hist WHERE fetched='ok'"):
        fin[(isu,fy)]=(ni,eq)
    for isu,ni,eq in con.execute("SELECT isu_cd,ni_fy,eq_fy FROM mkt_fundamentals WHERE fetched='ok'"):
        fin[(isu,2025)]=(ni,eq)
    print(f"{'분기말':>9} {'시장':>7} {'PIT_FY':>6} {'PER':>7} {'PBR':>6} {'커버시총%':>7}")
    for d in qdates:
        y,m=int(d[:4]),int(d[4:6])
        pit_fy = y-1 if m>=4 else y-2
        caps=con.execute("SELECT isu_cd, mkt, mktcap FROM krx_weekly WHERE bas_dd=%s",(d,)).fetchall()
        from collections import defaultdict
        agg=defaultdict(lambda: dict(cap=0,ni=0,eq=0,capn=0,cape=0,tot=0))
        for isu,mkt,cap in caps:
            if not cap: continue
            a=agg[mkt]; a["tot"]+=cap
            f=fin.get((isu,pit_fy))
            if not f: continue
            ni,eq=f
            if ni is not None: a["ni"]+=ni; a["capn"]+=cap
            if eq and eq>0: a["eq"]+=eq; a["cape"]+=cap
        for mkt in ("KOSPI","KOSDAQ"):
            a=agg.get(mkt)
            if not a or not a["tot"]: continue
            per=a["capn"]/a["ni"] if a["ni"] and a["ni"]>0 else None
            pbr=a["cape"]/a["eq"] if a["eq"] else None
            cov=a["capn"]/a["tot"]*100
            print(f"{d:>9} {mkt:>7} {pit_fy:>6} {(f'{per:.1f}' if per else 'N/M'):>7} {(f'{pbr:.2f}' if pbr else '-'):>6} {cov:>6.0f}%")
    con.close()

if __name__=="__main__":
    ap=argparse.ArgumentParser(); ap.add_argument("--fetch",action="store_true"); ap.add_argument("--series",action="store_true")
    a=ap.parse_args()
    if a.fetch: asyncio.run(fetch())
    else: series()
