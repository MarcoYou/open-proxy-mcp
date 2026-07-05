"""시장·섹터 밸류에이션 **과거 시계열 밴드** 백필 — FY0 기준(연간 mkt_fund_hist). DART 0콜.

방법(firm_history와 동일 계산의 전종목 합산 = 지수 PER 표준):
  각 과거 월말 d마다  시장/섹터 PER(FY0) = Σ보통주시총 ÷ Σ지배순이익(PIT FY) ,  PBR(FY0) = Σ시총 ÷ Σ지배자본.
  · 보통주 시총 = krx_weekly를 mkt_fund_hist(보통주 코드)로 JOIN → 우선주 자동 제외(KRX kinds 불요).
  · PIT: _pit_fy(d) = 4월 이후 전년 FY, 아니면 전전년(사업보고서 3월 공시, look-ahead 방지).
  · **FX 없음(의도적 단순화)**: 대부분 한국 상장사는 원화 공시라 원화 저장. 비KRW 라벨은 USD 12·CNY 9·
    JPY 1 = 22사뿐이고 시총 합산 <0.2%(밴드가 실제 시장사와 일치하는 게 증거). 게다가 mkt_fund_hist에
    연도별 통화 컬럼이 없어(두산밥캣 241560은 fy≤2022 원화·fy2023+ USD로 **연도별 통화 상이**) 단일 라벨을
    전 연도에 곱하면 옛 연도가 4조→4,826조로 폭증(260705 실측 버그). → 시장/섹터 레벨은 no-FX가 정확.
    (개별 종목 firm_history는 통화전환사 옛 연도 오차 존재 — 별도 이슈.) 적자 포함(Σ≤0→NULL).
  · 월말 = 각 월 마지막 거래주. **현재월 제외**(daily cron 소유 — TTM/MRQ 보존).

저장: mkt_val_history(per_fy0·pbr_fy0·cap) · mkt_sector_val(per_fy0·pbr_fy0·cap·n·label).
  ON CONFLICT은 **FY0 열만** 갱신 — 기존 per_ttm/pbr_mrq(cron) 보존.
TTM/MRQ 밴드는 분기(mkt_fund_q) 백필 완주 후 별도 추가(같은 방식, 분모만 교체).

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
    # 연간 재무(restated 우선, raw 통화)
    fin: dict[tuple, tuple] = {}
    for isu, fy, ni, eq, nir, eqr in con.execute(
            "SELECT isu_cd, fy, ni, eq, ni_restated, eq_restated FROM mkt_fund_hist"):
        fin[(isu, int(fy))] = (nir if nir is not None else ni, eqr if eqr is not None else eq)
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
        caps = {r[0]: float(r[1] or 0) for r in con.execute(
            "SELECT isu_cd, mktcap FROM krx_weekly WHERE bas_dd=%s", (d,))}
        mk = defaultdict(lambda: [0.0, 0.0, 0.0])            # mkt -> [cap, ni, eq]
        sec = defaultdict(lambda: [0.0, 0.0, 0.0, 0])        # (mkt,sector) -> [cap, ni, eq, n]
        for isu, (mkt, ind) in meta.items():
            cap = caps.get(isu)
            if not cap or mkt not in ("KOSPI", "KOSDAQ"):
                continue
            f = fin.get((isu, fy))
            if not f:
                continue
            ni, eq = f                                        # 원화(FX 불요)
            a = mk[mkt]; a[0] += cap
            if ni is not None: a[1] += ni
            if eq is not None: a[2] += eq
            s = bucket(ind, isu) if ind and ind not in ("none", "err") else None
            if s:
                b = sec[(mkt, s)]; b[0] += cap; b[3] += 1
                if ni is not None: b[1] += ni
                if eq is not None: b[2] += eq
        for mkt, (cap, ni, eq) in mk.items():
            con.execute("""INSERT INTO mkt_val_history(snap_dd,mkt,per_fy0,pbr_fy0,cap)
                VALUES(%s,%s,%s,%s,%s) ON CONFLICT(snap_dd,mkt) DO UPDATE SET
                per_fy0=EXCLUDED.per_fy0, pbr_fy0=EXCLUDED.pbr_fy0, cap=EXCLUDED.cap""",
                (d, mkt, (cap / ni) if ni > 0 else None, (cap / eq) if eq > 0 else None, round(cap)))
            nmk += 1
        for (mkt, s), (cap, ni, eq, n) in sec.items():
            con.execute("""INSERT INTO mkt_sector_val(snap_dd,mkt,sector,label,n,cap,per_fy0,pbr_fy0)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(snap_dd,mkt,sector) DO UPDATE SET
                label=EXCLUDED.label, n=EXCLUDED.n, cap=EXCLUDED.cap,
                per_fy0=EXCLUDED.per_fy0, pbr_fy0=EXCLUDED.pbr_fy0""",
                (d, mkt, s, label(s), n, round(cap), (cap / ni) if ni > 0 else None, (cap / eq) if eq > 0 else None))
            nsec += 1
    print(f"✓ 과거 FY0 밴드 저장: 시장 {nmk}행 + 섹터 {nsec}행 ({len(months)}개 월말)", flush=True)
    con.close()


if __name__ == "__main__":
    asyncio.run(main())
