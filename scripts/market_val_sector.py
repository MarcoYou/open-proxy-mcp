"""OPM 섹터(KSIC 하이브리드)별 시총가중 밸류에이션 — 코스피/코스닥 분리.

분류: data/ksic/opm_sector_map.json (중분류 기본 + 26/64/66/21/28/20 소분류, <5사 폴딩).
근거: wiki/decisions/ksic-sector-mapping.md. 시총=krx_weekly 최신주, 재무=mkt_fundamentals(TTM/MRQ).
실행: python3 scripts/market_val_sector.py
"""
import os, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
import psycopg
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]
ksic = json.load(open(ROOT/"open_proxy_mcp/data/ksic/ksic10_ko.json"))
smap = json.load(open(ROOT/"open_proxy_mcp/data/ksic/opm_sector_map.json"))
L3 = set(smap["level3_prefixes"]); OVR = smap["display_overrides"]; MINB = smap["min_bucket_firms"]

def bucket(ind): return ind[:3] if ind[:2] in L3 else ind[:2]
def label(code): return OVR.get(code) or f"{ksic.get(code,'?')[:14]}({code})"

def main():
    con = psycopg.connect(os.environ["DATABASE_URL"])
    wk = {c:(m or 0) for c,m in con.execute(
        "SELECT isu_cd, mktcap FROM krx_weekly WHERE bas_dd=(SELECT MAX(bas_dd) FROM krx_weekly)")}
    rows = con.execute("""SELECT isu_cd, mkt, induty, ni_ttm, eq_mrq, eq_fy
        FROM mkt_fundamentals WHERE fetched='ok' AND induty NOT IN ('none','err')""").fetchall()
    for MKT in ("KOSPI","KOSDAQ"):
        agg = defaultdict(lambda: dict(n=0,ni=0,eq=0,capn=0,cape=0,cap=0))
        for isu,mkt,ind,nt,em,ef in rows:
            if mkt != MKT: continue
            cap = wk.get(isu)
            if not cap: continue
            a = agg[bucket(ind)]; a["n"]+=1; a["cap"]+=cap
            eq = em if em is not None else ef
            if nt is not None: a["ni"]+=nt; a["capn"]+=cap
            if eq and eq>0: a["eq"]+=eq; a["cape"]+=cap
        fold = dict(n=0,ni=0,eq=0,capn=0,cape=0,cap=0); final={}
        for b,a in agg.items():
            if a["n"] < MINB:
                for k in fold: fold[k]+=a[k]
            else: final[b]=a
        if fold["n"]: final["_fold"]=fold
        print(f"\n════════ {MKT} (OPM 섹터) ════════")
        print(f"{'섹터':<26}{'사수':>4}{'PER(TTM)':>9}{'PBR(MRQ)':>9}{'Σ시총(조)':>9}")
        for b,a in sorted(final.items(), key=lambda x:-x[1]["cap"]):
            nm = smap["fold_label"] if b=="_fold" else label(b)
            per = a["capn"]/a["ni"] if a["ni"] and a["ni"]>0 else None
            pbr = a["cape"]/a["eq"] if a["eq"] else None
            print(f"{nm:<27}{a['n']:>4}{(f'{per:.1f}' if per else 'N/M'):>9}"
                  f"{(f'{pbr:.2f}' if pbr else '-'):>9}{a['cap']/1e12:>9.1f}")
    con.close()

if __name__ == "__main__":
    main()
