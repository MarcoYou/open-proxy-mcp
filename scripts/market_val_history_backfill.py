"""시장·섹터 밸류에이션 **과거 시계열 밴드** 백필 — FY0 + TTM/MRQ(260706, 분기백필 완주 후 추가). DART 0콜.

방법(firm_history와 동일 계산의 전종목 합산 = 지수 PER 표준):
  각 과거 월말 d마다:
    FY0  = Σ보통주시총 ÷ Σ지배순이익(연간 mkt_fund_hist, PIT FY)  /  Σ시총 ÷ Σ지배자본(PIT FY)
    TTM  = Σ보통주시총 ÷ Σ[FY(y-1)연간+누적(y,q)−누적(y-1,q)](분기 mkt_fund_q, PIT quarter)
    MRQ  = Σ시총 ÷ Σ최근분기 지배자본(분기 mkt_fund_q, PIT quarter)
  · 보통주 시총 = krx_weekly를 mkt_fund_hist/mkt_fund_q(보통주 코드)로 JOIN → 우선주 자동 제외.
  · PIT FY: `_pit_fy(d)` = 4월 이후 전년 FY, 아니면 전전년. PIT quarter: `_pit_quarter(d)` — valuation.py의
    firm_history와 **동일 함수 재사용**(로직 이중구현 방지, 사업보고서 3월/1Q 5·15/반기 8·14/3Q 11·14).
  · TTM/MRQ 계산식(`_ttm_ni`/`_mrq_eq`)도 valuation.py에서 그대로 import — firm 레벨과 완전히 같은 로직.
  · **FX 없음(의도적 단순화)**: 대부분 한국 상장사는 원화 공시라 원화 저장. 비KRW 라벨은 USD 12·CNY 9·
    JPY 1 = 22사뿐이고 시총 합산 <0.2%(밴드가 실제 시장사와 일치하는 게 증거). 게다가 mkt_fund_hist에
    연도별 통화 컬럼이 없어(두산밥캣 241560은 fy≤2022 원화·fy2023+ USD로 **연도별 통화 상이**) 단일 라벨을
    전 연도에 곱하면 옛 연도가 4조→4,826조로 폭증(260705 실측 버그). → 시장/섹터 레벨은 no-FX가 정확.
    (개별 종목 firm_history는 통화전환사 옛 연도 오차 존재 — 별도 이슈.) 적자 포함(Σ≤0→NULL).
  · 월말 = 각 월 마지막 거래주. **현재월 제외**(daily cron 소유).

저장: mkt_val_history(per_fy0·pbr_fy0·per_ttm·pbr_mrq·cap) · mkt_sector_val(동일+label·n).
  FY0와 TTM/MRQ는 독립 페어링(한쪽 데이터 없어도 다른 쪽은 채움) — firm별로 결측 있을 수 있어서.

실행: python3 scripts/market_val_history_backfill.py
"""
import asyncio, os, sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
import psycopg
from scripts.market_val_weekly import bucket, label
from open_proxy_mcp.services.valuation import _pit_quarter, _ttm_ni, _mrq_eq

DDL_MIGRATE = (
    "ALTER TABLE mkt_val_history ADD COLUMN IF NOT EXISTS per_fy0 double precision",
    "ALTER TABLE mkt_val_history ADD COLUMN IF NOT EXISTS pbr_fy0 double precision",
    "ALTER TABLE mkt_sector_val ADD COLUMN IF NOT EXISTS per_fy0 double precision",
    "ALTER TABLE mkt_sector_val ADD COLUMN IF NOT EXISTS pbr_fy0 double precision",
)


def _pit_fy(dd: str) -> int:
    y, m = int(dd[:4]), int(dd[4:6])
    return y - 1 if m >= 4 else y - 2


async def main() -> None:
    con = psycopg.connect(os.environ["DATABASE_URL"]); con.autocommit = True
    for m in DDL_MIGRATE:
        con.execute(m)
    # 종목 메타: 시장·업종 (통화 X — mkt_fund_hist는 이미 원화)
    meta = {r[0]: (r[1], r[2] or "") for r in con.execute(
        "SELECT isu_cd, mkt, induty FROM mkt_fundamentals WHERE fetched='ok'")}
    # 연간 재무(restated 우선, raw 통화) — 종목별 dict로 그룹 (valuation.py의 fin 형태와 동일: {fy:(ni,eq)})
    fin_all: dict[str, dict[int, tuple]] = {}
    for isu, fy, ni, eq, nir, eqr in con.execute(
            "SELECT isu_cd, fy, ni, eq, ni_restated, eq_restated FROM mkt_fund_hist"):
        fin_all.setdefault(isu, {})[int(fy)] = (nir if nir is not None else ni, eqr if eqr is not None else eq)
    # 분기 재무 — 종목별 dict {(fy,q):(ni_cum,eq)}
    finq_all: dict[str, dict[tuple, tuple]] = {}
    for isu, fy, q, ni_cum, eq in con.execute(
            "SELECT isu_cd, fy, quarter, ni_cum, eq FROM mkt_fund_q WHERE quarter != 4"):
        finq_all.setdefault(isu, {})[(int(fy), int(q))] = (ni_cum, eq)
    # 월말 날짜(각 YYYYMM 마지막 거래주), 2020~현재월 이전
    months = [r[0] for r in con.execute(
        "SELECT DISTINCT ON (substring(bas_dd,1,6)) bas_dd FROM krx_weekly WHERE bas_dd>='20200101' "
        "ORDER BY substring(bas_dd,1,6), bas_dd DESC")]
    today = date.today(); cur_ym = f"{today.year}{today.month:02d}"
    months = sorted(d for d in months if d[:6] < cur_ym)
    print(f"대상 월말 {len(months)}개({months[0]}~{months[-1]}) · 종목 {len(meta)}", flush=True)

    nmk = nsec = 0
    for d in months:
        fy = _pit_fy(d)
        fy_q, q = _pit_quarter(d)
        caps = {r[0]: float(r[1] or 0) for r in con.execute(
            "SELECT isu_cd, mktcap FROM krx_weekly WHERE bas_dd=%s", (d,))}
        # ⚠ 4지표 각각 독립 시총분모 필수 — 한 firm이 ni_fy0는 없고 ni_ttm만 있어도 그 firm의 cap이
        # per_fy0 분자에 섞이면 분자(cap)만 커지고 분모(ni)는 안 커져 PER 왜곡(260706 실측: KOSPI
        # 2020-12 FY0 PER이 32.7→42.8로 오염, FY0 없이 TTM/MRQ만 있는 219사·426조가 원인).
        # mkt -> {cap_per_fy0, ni_fy0, cap_pbr_fy0, eq_fy0, cap_per_ttm, ni_ttm, cap_pbr_mrq, eq_mrq}
        mk = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0])
        sec = defaultdict(lambda: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0])
        for isu, (mkt, ind) in meta.items():
            cap = caps.get(isu)
            if not cap or mkt not in ("KOSPI", "KOSDAQ"):
                continue
            fin = fin_all.get(isu, {})
            f = fin.get(fy)
            ni_ttm = _ttm_ni(fin, finq_all.get(isu, {}), fy_q, q)
            eq_mrq = _mrq_eq(fin, finq_all.get(isu, {}), fy_q, q)
            if not f and ni_ttm is None and eq_mrq is None:
                continue
            ni_fy0, eq_fy0 = f if f else (None, None)
            s = bucket(ind, isu) if ind and ind not in ("none", "err") else None
            for acc in (mk[mkt], sec[(mkt, s)] if s else None):
                if acc is None:
                    continue
                if ni_fy0 is not None: acc[0] += cap; acc[1] += ni_fy0
                if eq_fy0 is not None: acc[2] += cap; acc[3] += eq_fy0
                if ni_ttm is not None: acc[4] += cap; acc[5] += ni_ttm
                if eq_mrq is not None: acc[6] += cap; acc[7] += eq_mrq
            if s:
                sec[(mkt, s)][8] += 1
        for mkt, (cap_pf, ni_fy0, cap_bf, eq_fy0, cap_pt, ni_ttm, cap_bm, eq_mrq) in mk.items():
            con.execute("""INSERT INTO mkt_val_history(snap_dd,mkt,per_fy0,pbr_fy0,per_ttm,pbr_mrq,cap)
                VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(snap_dd,mkt) DO UPDATE SET
                per_fy0=EXCLUDED.per_fy0, pbr_fy0=EXCLUDED.pbr_fy0,
                per_ttm=EXCLUDED.per_ttm, pbr_mrq=EXCLUDED.pbr_mrq, cap=EXCLUDED.cap""",
                (d, mkt, (cap_pf / ni_fy0) if ni_fy0 > 0 else None, (cap_bf / eq_fy0) if eq_fy0 > 0 else None,
                 (cap_pt / ni_ttm) if ni_ttm > 0 else None, (cap_bm / eq_mrq) if eq_mrq > 0 else None,
                 round(max(cap_pf, cap_bf, cap_pt, cap_bm))))
            nmk += 1
        for (mkt, s), (cap_pf, ni_fy0, cap_bf, eq_fy0, cap_pt, ni_ttm, cap_bm, eq_mrq, n) in sec.items():
            con.execute("""INSERT INTO mkt_sector_val(snap_dd,mkt,sector,label,n,cap,per_fy0,pbr_fy0,per_ttm,pbr_mrq)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(snap_dd,mkt,sector) DO UPDATE SET
                label=EXCLUDED.label, n=EXCLUDED.n, cap=EXCLUDED.cap,
                per_fy0=EXCLUDED.per_fy0, pbr_fy0=EXCLUDED.pbr_fy0,
                per_ttm=EXCLUDED.per_ttm, pbr_mrq=EXCLUDED.pbr_mrq""",
                (d, mkt, s, label(s), n, round(max(cap_pf, cap_bf, cap_pt, cap_bm)),
                 (cap_pf / ni_fy0) if ni_fy0 > 0 else None, (cap_bf / eq_fy0) if eq_fy0 > 0 else None,
                 (cap_pt / ni_ttm) if ni_ttm > 0 else None, (cap_bm / eq_mrq) if eq_mrq > 0 else None))
            nsec += 1
    print(f"✓ 과거 FY0+TTM/MRQ 밴드 저장: 시장 {nmk}행 + 섹터 {nsec}행 ({len(months)}개 월말)", flush=True)
    con.close()


if __name__ == "__main__":
    asyncio.run(main())
