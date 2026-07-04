"""시장 밸류에이션 분기 시계열 — PIT(공시 접수 근사) 기준 시장 PER/PBR 추이.

시총: krx_weekly(주간, 2015-12~) 재활용 → KRX 콜 0.
재무: FY2020~2024 지배순이익·지배자본 배치(mkt_fund_hist) — DART 종목당 5콜, 총 ~13k콜.
PIT 근사: 분기말 D 시점 최신 확정재무 = (D가 4월 이후면 전년 FY, 아니면 전전년 FY)
          — 사업보고서 3월 중순 공시 규칙의 연 단위 근사(look-ahead 방지).

자동 정정(260704, 소프트센 032680 FY2022 사례로 확정): 회사가 당해년도 XBRL에 단위 스케일을
잘못 넣는 경우(사람이 읽는 표는 "단위: 백만원"이 맞는데 XBRL 원시값엔 배율을 안 곱함 → 100만배
부풀림)가 실제로 있다. 원본 하나만 보고는 못 잡지만(두 DART 엔드포인트가 같은 버그를 공유),
**다음 해 보고서의 전기(frmtrm_amount) 비교치는 회사가 재작성하면서 정상화**돼 있다(실측 확인:
소프트센 FY2022 매출 73,373,050,121,000,000 → FY2023 보고서 전기란엔 73,373,050,121로 정정).
그래서 매 연도 fy를 가져올 때 **다음 해(fy+1) 보고서의 전기 비교치도 함께 수집**해 `ni_restated`/
`eq_restated`에 저장하고, series() 계산 시 **restated가 있으면 그걸 우선** 사용한다.

실행: python3 scripts/market_val_series.py --fetch   # 과거 FY 배치(재개 가능, ~100분)
      python3 scripts/market_val_series.py --series  # 분기 시계열 산출
"""
import argparse, asyncio, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
import psycopg
from open_proxy_mcp.services.scale_guard import gid_exact, assess as scale_assess

YEARS = [2020, 2021, 2022, 2023, 2024]
DDL = """CREATE TABLE IF NOT EXISTS mkt_fund_hist(
  isu_cd text, fy int, fs text, ni double precision, eq double precision,
  ni_restated double precision, eq_restated double precision,
  fetched text, PRIMARY KEY(isu_cd, fy))"""
DDL_MIGRATE = (
    "ALTER TABLE mkt_fund_hist ADD COLUMN IF NOT EXISTS ni_restated double precision",
    "ALTER TABLE mkt_fund_hist ADD COLUMN IF NOT EXISTS eq_restated double precision",
)

def num(v):
    try: return float(str(v).replace(",","")) if v not in (None,"","-") else None
    except: return None

def gid(rows, account_id, sj, field="thstrm_amount"):
    """정확일치(exact) — substring(in) 금지(260704 실측: 접두어 충돌로 오탐 확인, wiki §9)."""
    return gid_exact(rows, account_id, sj, field)

def _pg():
    con=psycopg.connect(os.environ["DATABASE_URL"]); con.execute(DDL)
    for stmt in DDL_MIGRATE: con.execute(stmt)
    con.commit()
    return con

def _flush(buf):
    """버퍼를 새 연결로 일괄 저장 — Supabase 유휴 끊김에 면역(flush마다 fresh conn)."""
    if not buf: return
    for attempt in (1,2):
        try:
            with psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=15) as c:
                with c.cursor() as cur:
                    # 순서 무관 병합: 실제조회(ni/eq)와 재작성(ni_restated/eq_restated)이 어느 순서로
                    # 와도 서로의 필드를 덮어쓰지 않음. fetched는 'restate_only'(선행 삽입용 자리표시)
                    # 보다 실제 상태('ok'/'nodata'/'err:*')가 항상 우선.
                    cur.executemany(
                        "INSERT INTO mkt_fund_hist (isu_cd,fy,fs,ni,eq,ni_restated,eq_restated,fetched) "
                        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s) "
                        "ON CONFLICT (isu_cd,fy) DO UPDATE SET "
                        "fs=COALESCE(EXCLUDED.fs, mkt_fund_hist.fs), "
                        "ni=COALESCE(EXCLUDED.ni, mkt_fund_hist.ni), "
                        "eq=COALESCE(EXCLUDED.eq, mkt_fund_hist.eq), "
                        "ni_restated=COALESCE(EXCLUDED.ni_restated, mkt_fund_hist.ni_restated), "
                        "eq_restated=COALESCE(EXCLUDED.eq_restated, mkt_fund_hist.eq_restated), "
                        "fetched=CASE WHEN EXCLUDED.fetched <> 'restate_only' THEN EXCLUDED.fetched "
                        "             ELSE mkt_fund_hist.fetched END",
                        buf)
                c.commit()
            buf.clear(); return
        except psycopg.OperationalError:
            if attempt==2: raise

async def fetch():
    from open_proxy_mcp.dart.client import get_dart_client, DartClientError
    con=_pg()
    buf=[]
    firms=[r for r in con.execute("SELECT isu_cd, corp_code FROM mkt_fundamentals WHERE fetched='ok' ORDER BY isu_cd")]
    done={(r[0],r[1]) for r in con.execute("SELECT isu_cd, fy FROM mkt_fund_hist")}
    con.close()  # 이후 쓰기는 _flush(fresh conn)만 사용
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
            attr="ifrs-full_ProfitLossAttributableToOwnersOfParent"; eqa="ifrs-full_EquityAttributableToOwnersOfParent"
            ni=gid(rows,attr,("CIS","IS")) or gid(rows,"ifrs-full_ProfitLoss",("CIS","IS"))
            ni_frmtrm=gid(rows,attr,("CIS","IS"),"frmtrm_amount")
            eq=gid(rows,eqa,("BS",)) or gid(rows,"ifrs-full_Equity",("BS",))
            # 실시간 스케일 가드(소프트센 032680 사례, wiki §9) — mktcap은 이 배치엔 없어 ①②③만 적용.
            # 항등식은 총자본(지배+비지배) 기준 — eq(지배자본)와 별도로 조회 필요.
            assets=gid(rows,"ifrs-full_Assets",("BS",)); liab=gid(rows,"ifrs-full_Liabilities",("BS",))
            eq_total=gid(rows,"ifrs-full_Equity",("BS",))
            verdict=scale_assess(thstrm=ni, frmtrm=ni_frmtrm, assets=assets, liabilities=liab, equity=eq_total)
            if verdict["tier"]=="hard":
                print(f"[가드] {isu} FY{yr} 스케일오류 감지({verdict['hard_hit']}) — ni/eq 무효화",flush=True)
                ni=eq=None
            st="ok" if (ni is not None or eq is not None) else "nodata"
            buf.append((isu,yr,fs,ni,eq,None,None,st))
            if len(buf)>=25: _flush(buf)
        except Exception as e:
            en=type(e).__name__
            if "ReadError" in en or "Connect" in en or "Timeout" in en:
                _flush(buf)
                print(f"네트워크({en}) — 중단(재개 가능)",flush=True); break
            buf.append((isu,yr,None,None,None,None,None,f"err:{str(e)[:30]}"))
            if len(buf)>=25: _flush(buf)
        if k%200==0: print(f"{k}/{len(todo)}",flush=True)
    _flush(buf)
    print("fetch 종료",flush=True)


async def backfill_restated():
    """이미 수집된 firms에 대해 **fy=2024 딱 1콜만** 재조회 — fnlttSinglAcntAll이 thstrm(2024)·
    frmtrm(2023)·bfefrmtrm(2022)을 한 응답에 다 주므로, 5년 재조회 없이 종목당 1콜로 2022~2024
    3개년 재작성치를 확보(소프트센 032680 실측으로 bfefrmtrm 제공 확인, 260704)."""
    from open_proxy_mcp.dart.client import get_dart_client, DartClientError
    con=_pg()
    firms=[r for r in con.execute("SELECT isu_cd, corp_code FROM mkt_fundamentals WHERE fetched='ok' ORDER BY isu_cd")]
    fs_map={r[0]:r[1] for r in con.execute("SELECT isu_cd, fs FROM mkt_fund_hist WHERE fy=2024")}
    # 이 함수는 fy=2024 보고서를 조회해 fy=2023·2022 행에 재작성치를 쓴다(fy=2024 자체엔 안 씀).
    # 따라서 완료 판정은 2023/2022 중 하나라도 재작성치가 있으면 그 종목은 처리됐다고 본다.
    done={r[0] for r in con.execute(
        "SELECT isu_cd FROM mkt_fund_hist WHERE fy IN (2022,2023) "
        "AND (ni_restated IS NOT NULL OR eq_restated IS NOT NULL)")}
    con.close()
    todo=[(i,c) for i,c in firms if i not in done]
    print(f"대상 {len(firms)}사 · 남은 {len(todo)}건 (종목당 1콜, fy=2024)",flush=True)
    c=get_dart_client(); k=0
    restate_buf=[]
    for isu,cc in todo:
        k+=1
        try:
            fs=fs_map.get(isu,"CFS")
            d=await c.get_fnltt_singl_acnt_all(cc,"2024","11011",fs)
            rows=(d.get("list") or []) if isinstance(d,dict) else []
            await asyncio.sleep(0.45)
            attr="ifrs-full_ProfitLossAttributableToOwnersOfParent"; eqa="ifrs-full_EquityAttributableToOwnersOfParent"
            for fy_off,field in ((2023,"frmtrm_amount"),(2022,"bfefrmtrm_amount")):
                ni_r=gid(rows,attr,("CIS","IS"),field) or gid(rows,"ifrs-full_ProfitLoss",("CIS","IS"),field)
                eq_r=gid(rows,eqa,("BS",),field) or gid(rows,"ifrs-full_Equity",("BS",),field)
                if ni_r is not None or eq_r is not None:
                    restate_buf.append((isu,fy_off,fs,None,None,ni_r,eq_r,"restate_only"))
            if len(restate_buf)>=25: _flush(restate_buf)
        except Exception as e:
            en=type(e).__name__
            if "ReadError" in en or "Connect" in en or "Timeout" in en:
                _flush(restate_buf)
                print(f"네트워크({en}) — 중단(재개 가능)",flush=True); break
        if k%200==0: print(f"{k}/{len(todo)}",flush=True)
    _flush(restate_buf)
    print("백필 종료",flush=True)

def series():
    con=psycopg.connect(os.environ["DATABASE_URL"])
    # 분기말 후보: krx_weekly에서 각 분기 마지막 주간일
    qs=con.execute("""SELECT MAX(bas_dd) FROM krx_weekly
      GROUP BY LEFT(bas_dd,4), CASE WHEN SUBSTRING(bas_dd,5,2)::int<=3 THEN 1
        WHEN SUBSTRING(bas_dd,5,2)::int<=6 THEN 2 WHEN SUBSTRING(bas_dd,5,2)::int<=9 THEN 3 ELSE 4 END
      ORDER BY 1""").fetchall()
    qdates=[r[0] for r in qs if r[0]>="20210601"]
    # 재무: FY별 (mkt_fund_hist ∪ 최신 FY2025는 mkt_fundamentals)
    # restated(다음 해 보고서 전기 비교치)가 있으면 그걸 우선 — 회사가 스스로 재작성한 참값
    # (소프트센 032680 FY2022 사례: 당해 XBRL 100만배 오류를 다음해 보고서가 정상화).
    fin={}
    restated_used=[]
    for isu,fy,ni,eq,ni_r,eq_r in con.execute(
            "SELECT isu_cd,fy,ni,eq,ni_restated,eq_restated FROM mkt_fund_hist WHERE fetched='ok' OR ni_restated IS NOT NULL OR eq_restated IS NOT NULL"):
        final_ni = ni_r if ni_r is not None else ni
        final_eq = eq_r if eq_r is not None else eq
        if ni_r is not None and ni is not None and abs(ni_r - ni) > abs(ni) * 0.01:
            restated_used.append((isu, fy, ni, ni_r))
        fin[(isu,fy)]=(final_ni, final_eq)
    for isu,ni,eq in con.execute("SELECT isu_cd,ni_fy,eq_fy FROM mkt_fundamentals WHERE fetched='ok'"):
        fin[(isu,2025)]=(ni,eq)
    if restated_used:
        print(f"[재작성 적용] {len(restated_used)}건 (당해 XBRL 대신 다음해 보고서 전기 비교치 사용)")
        for r in restated_used[:10]: print(f"    {r}")

    # 스케일 오류 가드(재작성으로도 못 잡은 잔여 케이스): KOSDAQ 종목의 ni/eq가 50조 초과면
    # 자릿수 오류로 간주해 무효화(KOSPI 대기업은 실제로 100조 넘으므로 KOSDAQ만 적용).
    kosdaq_isu = {r[0] for r in con.execute(
        "SELECT isu_cd FROM mkt_fundamentals WHERE mkt='KOSDAQ'")}
    SCALE_CAP = 50e12
    dropped = []
    for k in list(fin):
        isu, fy = k
        ni, eq = fin[k]
        if isu in kosdaq_isu and ((ni is not None and abs(ni) > SCALE_CAP) or
                                    (eq is not None and abs(eq) > SCALE_CAP)):
            dropped.append(k); del fin[k]
    if dropped:
        print(f"[가드] KOSDAQ 스케일오류 제외(재작성 미확보분): {dropped}")
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
    ap=argparse.ArgumentParser()
    ap.add_argument("--fetch",action="store_true")
    ap.add_argument("--backfill-restated",action="store_true",
                    help="종목당 1콜(fy=2024)로 2022~2024 재작성치만 백필")
    ap.add_argument("--series",action="store_true")
    a=ap.parse_args()
    if a.fetch: asyncio.run(fetch())
    elif a.backfill_restated: asyncio.run(backfill_restated())
    else: series()
