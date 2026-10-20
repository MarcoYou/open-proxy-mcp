"""시장·섹터 밸류에이션 **과거 시계열 밴드** 백필 — FY0 + TTM/MRQ(260706, 분기백필 완주 후 추가). DART 0콜.

방법(firm_history와 동일 계산의 전종목 합산 = 지수 PER 표준):
  각 과거 월말 d마다:
    FY0  = Σ보통주시총 ÷ Σ지배순이익(연간 dart_finstat_y, PIT FY)  /  Σ시총 ÷ Σ지배자본(PIT FY)
    TTM  = Σ보통주시총 ÷ Σ[FY(y-1)연간+누적(y,q)−누적(y-1,q)](분기 dart_finstat_q, PIT quarter)
    MRQ  = Σ시총 ÷ Σ최근분기 지배자본(분기 dart_finstat_q, PIT quarter)
  · 보통주 시총 = krx_weekly를 dart_finstat_y/dart_finstat_q(보통주 코드)로 JOIN → 우선주 자동 제외.
  · PIT FY: `_pit_fy(d)` = 4월 이후 전년 FY, 아니면 전전년. PIT quarter: `_pit_quarter(d)` — valuation.py의
    firm_history와 **동일 함수 재사용**(로직 이중구현 방지, 사업보고서 3월/1Q 5·15/반기 8·14/3Q 11·14).
  · TTM/MRQ 계산식(`_ttm_ni`/`_mrq_eq`)도 valuation.py에서 그대로 import — firm 레벨과 완전히 같은 로직.
  · **FX 없음(의도적 단순화)**: 대부분 한국 상장사는 원화 공시라 원화 저장. 비KRW 라벨은 USD 12·CNY 9·
    JPY 1 = 22사뿐이고 시총 합산 <0.2%(밴드가 실제 시장사와 일치하는 게 증거). 게다가 mkt_finstat_y에
    연도별 통화 컬럼이 없어(두산밥캣 241560은 fy≤2022 원화·fy2023+ USD로 **연도별 통화 상이**) 단일 라벨을
    전 연도에 곱하면 옛 연도가 4조→4,826조로 폭증(260705 실측 버그). → 시장/섹터 레벨은 no-FX가 정확.
    (개별 종목 firm_history는 통화전환사 옛 연도 오차 존재 — 별도 이슈.) 적자 포함(Σ≤0→NULL).
  · 월말 = 각 월 마지막 거래주. **현재월 제외**(daily cron 소유).
  · 🔴 261019: 월말 날짜·시총은 `scripts/month_end_prices.py` 에서 고른다. DB krx_weekly 는 보존 하한 이전을 종목별 연말 행만 남겨
    종전 고르기(`DISTINCT ON (월) max(price_dd)`)는 하한 이전 달마다 상장폐지 종목 1~8개짜리 날짜를 월말로 집는다(그 종목에 재무가 남아
    있으면 그 집계가 저장된 행을 덮는다 — 10-19 에는 재무 표에 없어 0행).
    이제 하한 이전은 연말(12월)만 DB 로, 나머지 달은 콜드 서빙본(`--cold-sqlite`)이 있을 때만 다시 내고 없으면 건너뛴다(저장된 행 그대로).
  · 261019: 최소 커버리지 — 배수를 낸 회사 시총이 그 버킷에서 재무가 하나라도 있는 회사 시총의 절반 미만이면 그 배수·분모 합을 비운다.
  · 261019: **주간 스냅샷이 쓴 행은 덮지 않는다**(`cap_pref` 가 있는 행 — `market_val_weekly` 만 그 칸을 채운다). 그 날의 실시간 산식
    (그때의 재무 요약 × 보통주 시총, 외화 환산 포함)이라 이 백필의 PIT 산식으로 월말 점만 바꾸면 주간 이웃 점과 산식이 달라 곡선이 튄다
    (10-19 실측: 코스닥 20260828 PER(FY0) 97.18 → 101.85, 앞뒤 주 98.01·94.56). 주간 점이 있는 구간(2026-07~)의 시장 행은 스냅샷 몫이다.
  · 261019: KSIC 업종 행은 2026-08-23 결정으로 집계를 멈췄다(`KSIC_FROZEN_FROM`) — 그날 이후 월말은 KSIC 행을 새로 만들지 않는다.

저장: opm_val_market(260706 병합 — 시장전체 sector='_ALL' + 섹터별 행 한 테이블, PK snap_dd,market,sector).
  FY0와 TTM/MRQ는 독립 페어링(한쪽 데이터 없어도 다른 쪽은 채움) — firm별로 결측 있을 수 있어서.

실행: python3 scripts/market_val_history_backfill.py [--since 20200101] [--cold-sqlite <서빙본.sqlite>]
"""
import asyncio, os, sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from open_proxy_mcp.market_codes import KS as MKT_KS, KQ as MKT_KQ
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")
import psycopg
from scripts.market_val_weekly import bucket, label
from open_proxy_mcp.services.price_multiple_data import _pit_quarter, _ttm_ni, _mrq_eq
from open_proxy_mcp.services import report_calendar as rcal
from open_proxy_mcp.services import period_cells as pcells
from open_proxy_mcp.services import restated_pit as rpit
from scripts.month_end_prices import MonthEndPrices, covered

DDL_MIGRATE = (
    "ALTER TABLE opm_val_market ADD COLUMN IF NOT EXISTS per_fy0 double precision",
    "ALTER TABLE opm_val_market ADD COLUMN IF NOT EXISTS pbr_fy0 double precision",
    # 260829: 배수가 빈 이유를 「적자」와 「자료없음」으로 가르려면 분모를 같이 남겨야 한다.
    "ALTER TABLE opm_val_market ADD COLUMN IF NOT EXISTS ni_fy0 double precision",
    "ALTER TABLE opm_val_market ADD COLUMN IF NOT EXISTS ni_ttm double precision",
)


# 🔴 260829: 종전 INSERT 는 `scheme` 을 안 줬고 ON CONFLICT 도 3열이었다. 260823 에 PK 가
#   (snap_dd,market,scheme,sector) 4열로 바뀌었고 scheme 기본값은 'ksic' 다. 그대로 두면
#   ① 4열 PK 에 3열 ON CONFLICT 라 터지고 ② 시장전체 행이 scheme='ksic' 로 들어가
#   서비스가 읽는 scheme='market' 행을 영영 못 고친다. 축을 명시한다.
# 261018(재설계 v02 7단계): 값이 같은 행은 다시 쓰지 않는다(`WHERE … IS DISTINCT FROM`). 이 백필은 2020년 이후 월말 전부를
#   다시 계산해 올리므로, 종전에는 한 번 돌 때마다 opm_val_market 의 거의 모든 행(운영 18,601행)을 같은 값으로 다시 써서
#   표 크기만큼 죽은 행이 남았다. 이제 계산 결과가 실제로 달라진 행만 쓴다(새 월말은 종전대로 INSERT).
UPSERT_MKT = """
INSERT INTO opm_val_market
  (snap_dd,market,scheme,sector,per_fy0,pbr_fy0,per_ttm,pbr_mrq,cap,ni_fy0,ni_ttm)
VALUES (%s,%s,'market','_ALL',%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (snap_dd,market,scheme,sector) DO UPDATE SET
  per_fy0=EXCLUDED.per_fy0, pbr_fy0=EXCLUDED.pbr_fy0,
  per_ttm=EXCLUDED.per_ttm, pbr_mrq=EXCLUDED.pbr_mrq, cap=EXCLUDED.cap,
  ni_fy0=EXCLUDED.ni_fy0, ni_ttm=EXCLUDED.ni_ttm
WHERE opm_val_market.cap_pref IS NULL AND (opm_val_market.per_fy0, opm_val_market.pbr_fy0, opm_val_market.per_ttm, opm_val_market.pbr_mrq,
       opm_val_market.cap, opm_val_market.ni_fy0, opm_val_market.ni_ttm)
  IS DISTINCT FROM (EXCLUDED.per_fy0, EXCLUDED.pbr_fy0, EXCLUDED.per_ttm, EXCLUDED.pbr_mrq,
                    EXCLUDED.cap, EXCLUDED.ni_fy0, EXCLUDED.ni_ttm)
"""
UPSERT_SEC = """
INSERT INTO opm_val_market
  (snap_dd,market,scheme,sector,label,n,cap,per_fy0,pbr_fy0,per_ttm,pbr_mrq,ni_fy0,ni_ttm)
VALUES (%s,%s,'ksic',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (snap_dd,market,scheme,sector) DO UPDATE SET
  label=EXCLUDED.label, n=EXCLUDED.n, cap=EXCLUDED.cap,
  per_fy0=EXCLUDED.per_fy0, pbr_fy0=EXCLUDED.pbr_fy0,
  per_ttm=EXCLUDED.per_ttm, pbr_mrq=EXCLUDED.pbr_mrq,
  ni_fy0=EXCLUDED.ni_fy0, ni_ttm=EXCLUDED.ni_ttm
WHERE opm_val_market.cap_pref IS NULL AND (opm_val_market.label, opm_val_market.n, opm_val_market.cap, opm_val_market.per_fy0, opm_val_market.pbr_fy0,
       opm_val_market.per_ttm, opm_val_market.pbr_mrq, opm_val_market.ni_fy0, opm_val_market.ni_ttm)
  IS DISTINCT FROM (EXCLUDED.label, EXCLUDED.n, EXCLUDED.cap, EXCLUDED.per_fy0, EXCLUDED.pbr_fy0,
                    EXCLUDED.per_ttm, EXCLUDED.pbr_mrq, EXCLUDED.ni_fy0, EXCLUDED.ni_ttm)
"""


#: 261019: KSIC 업종 집계를 멈춘 날(2026-08-23 결정 — 업종 배수 기준 축을 업종분류로 옮겼다). 이 날 이후 월말은 KSIC 행을 쓰지 않는다
#: (서버는 KSIC 축의 마지막 날짜로 「집계가 멈춰 있다」를 알린다 — 여기서 새 월말을 만들면 산식이 다른 점이 그 뒤에 붙는다).
KSIC_FROZEN_FROM = "20260823"


def _multiples(v: list) -> tuple:
    """누산기 [cap_pf, ni_fy0, cap_bf, eq_fy0, cap_pt, ni_ttm, cap_bm, eq_mrq, n, n_nif, n_nit, cap_any] → (per_fy0, pbr_fy0, per_ttm, pbr_mrq,
    ni_fy0, ni_ttm). 합이 0 이하면 배수 None(적자·잠식), 더한 회사가 없거나(n_nif·n_nit = 0) 낸 회사 시총이 버킷의 절반 미만이면(`covered`,
    261019) 배수와 분모 합 모두 None — 화면은 「자료없음」(분모 합이 남으면 「적자」로 읽힌다)."""
    cap_pf, ni_fy0, cap_bf, eq_fy0, cap_pt, ni_ttm, cap_bm, eq_mrq, _n, n_nif, n_nit, cap_any = v
    ok_f, ok_bf, ok_t, ok_bm = (covered(c, cap_any) for c in (cap_pf, cap_bf, cap_pt, cap_bm))
    return ((cap_pf / ni_fy0) if ok_f and ni_fy0 > 0 else None, (cap_bf / eq_fy0) if ok_bf and eq_fy0 > 0 else None,
            (cap_pt / ni_ttm) if ok_t and ni_ttm > 0 else None, (cap_bm / eq_mrq) if ok_bm and eq_mrq > 0 else None,
            ni_fy0 if ok_f and n_nif else None, ni_ttm if ok_t and n_nit else None)


def _pit_fy(dd: str, fye: int = 12) -> int:
    """12월 결산은 4월 이후 전년, 아니면 전전년(종전 식). 261018: 결산월 `fye` 를 받는다(`report_calendar.pit_annual`)."""
    return rcal.pit_annual(dd, fye)


async def main() -> None:
    # 260829: 15분 job 한도에 걸려 79개 월말 중 26개에서 잘렸다. --since 로 이어서 돌린다.
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="20200101")
    ap.add_argument("--cold-sqlite", metavar="PATH",
                    help="보존 하한 이전 달을 콜드 서빙본(하한 이전 주간 시세 전 행 sqlite)으로 다시 낸다 — 없으면 그 달은 건너뛴다(261019)")
    a = ap.parse_args()
    since = a.since
    con = psycopg.connect(os.environ["DATABASE_URL"]); con.autocommit = True
    for m in DDL_MIGRATE:
        con.execute(m)
    # 종목 메타: 시장·업종 (통화 X — mkt_finstat_y는 이미 원화)
    meta = {r[0]: (r[1], r[2] or "") for r in con.execute(
        "SELECT ticker, market, induty FROM dart_fundamentals WHERE fetched='ok'")}
    # 261018(재설계 6단계): 종목 결산월 — 칸 고르기·TTM 직전 연간을 firm_history 와 같은 매핑으로(12월 결산은 종전과 같다).
    fye_of = rcal.load_fye_map(con)
    # 연간 재무(restated 우선, raw 통화) — 종목별 dict로 그룹 (valuation.py의 fin 형태와 동일: {fy:(ni,eq)})
    # 분기 재무 — 종목별 dict {(fy,q):(ni_cum,eq)}
    # 261020(p1g pit-restated): 값은 지금 시점(정정 칸 우선 — 종전 그대로), 정정 칸마다 출처 공시일 문(`restated_pit`) — 월말마다 `rpit.at` 으로
    #   그 날 공시돼 있던 값을 읽는다(서버 곡선·업종 비교 기업 행과 같은 규칙). 출처를 모르는 옛 정정 칸은 보수적 추정 공시일.
    fye_for = lambda t: rcal.fiscal(fye_of.get(t, 12), t)          # noqa: E731
    fin_all: dict[str, dict[int, tuple]] = rpit.load_annual(con, fye_for)
    finq_all: dict[str, dict[tuple, tuple]] = rpit.load_quarter(con, fye_for)
    # 261020(p1e): 반기·분기 결산 회사(리츠 등)는 결산기 칸(12개월 결산 회사와 같은 꼴 — `period_cells`)으로 고치고, 칸은 그 보고서 접수일부터 쓴다
    #   (서버 곡선·업종 비교와 같은 규칙). 표시 칸이 없으면 종전 그대로.
    period_of = pcells.apply_marked(fin_all, finq_all, pcells.load_marked(con))
    # 월말 날짜(각 YYYYMM 마지막 거래주), 2020~현재월 이전 — 261019: 하한 이전은 연말·콜드 서빙본만(`month_end_prices`)
    src = MonthEndPrices(con, since, a.cold_sqlite)
    print(src.describe(), flush=True)
    today = date.today(); cur_ym = f"{today.year}{today.month:02d}"
    months = sorted(d for d in src.plan.dates if d[:6] < cur_ym)
    if not months:
        print("다시 낼 월말이 없다", flush=True)
        src.close(); con.close()
        return
    print(f"대상 월말 {len(months)}개({months[0]}~{months[-1]}) · 종목 {len(meta)}(12월 결산이 아닌 {len(fye_of)})", flush=True)

    nmk = nsec = 0
    for d in months:
        pit = {rcal.Fiscal(): (_pit_fy(d), _pit_quarter(d))}   # 공시 달력(결산월·외국 상장사 기한) → (그 시점 FY, (분기 칸)) — 12월 결산은 날짜마다 한 번
        caps = {t: float(c or 0) for t, _m, c in src.rows(d)}
        # ⚠ 4지표 각각 독립 시총분모 필수 — 한 firm이 ni_fy0는 없고 ni_ttm만 있어도 그 firm의 cap이
        # per_fy0 분자에 섞이면 분자(cap)만 커지고 분모(ni)는 안 커져 PER 왜곡(260706 실측: KOSPI
        # 2020-12 FY0 PER이 32.7→42.8로 오염, FY0 없이 TTM/MRQ만 있는 219사·426조가 원인).
        # market -> {cap_per_fy0, ni_fy0, cap_pbr_fy0, eq_fy0, cap_per_ttm, ni_ttm, cap_pbr_mrq, eq_mrq}
        # 뒤 셋 = n(종목수) · n_nif/n_nit(분모를 실제로 더한 회사 수). 합이 0.0 인 것과 더한 회사가
        # 하나도 없는 것을 금액만으로는 구별할 수 없어, NULL 판정은 **개수**로 한다(260829).
        # 261019: 끝 칸 = cap_any(재무가 하나라도 있어 그 버킷에 센 회사의 시총 합) — 최소 커버리지의 분모(`covered`).
        mk = defaultdict(lambda: [0.0] * 8 + [0, 0, 0] + [0.0])
        sec = defaultdict(lambda: [0.0] * 8 + [0, 0, 0] + [0.0])
        for isu, (market, ind) in meta.items():
            cap = caps.get(isu)
            if not cap or market not in (MKT_KS, MKT_KQ):
                continue
            fye = rcal.fiscal(fye_of.get(isu, 12), isu)
            if isu in period_of:
                fye = rcal.with_cells(fye, period_of[isu])
            if fye not in pit:
                pit[fye] = (_pit_fy(d, fye), _pit_quarter(d, fye))
            fy, (fy_q, q) = pit[fye]
            fin = rpit.at(fin_all.get(isu, {}), d)                 # 261020(p1g): 그 월말에 공시돼 있던 값(정정 칸은 출처 공시일부터)
            finq = rpit.at(finq_all.get(isu, {}), d)
            f = fin.get(fy)
            ni_ttm = _ttm_ni(fin, finq, fy_q, q, fye)
            eq_mrq = _mrq_eq(fin, finq, fy_q, q)
            if not f and ni_ttm is None and eq_mrq is None:
                continue
            ni_fy0, eq_fy0 = f if f else (None, None)
            # 260829: 완전자본잠식은 모든 집계에서 뺀다(마스터 지시) — 위 두 스크립트와 같은 규칙.
            _eq_now = eq_mrq if eq_mrq is not None else eq_fy0
            if _eq_now is not None and _eq_now <= 0:
                continue
            s = bucket(ind, isu) if ind and ind not in ("none", "err") else None
            for acc in (mk[market], sec[(market, s)] if s else None):
                if acc is None:
                    continue
                if ni_fy0 is not None: acc[0] += cap; acc[1] += ni_fy0; acc[9] += 1
                if eq_fy0 is not None: acc[2] += cap; acc[3] += eq_fy0
                if ni_ttm is not None: acc[4] += cap; acc[5] += ni_ttm; acc[10] += 1
                if eq_mrq is not None: acc[6] += cap; acc[7] += eq_mrq
                if (ni_fy0, eq_fy0, ni_ttm, eq_mrq) != (None, None, None, None):
                    acc[11] += cap   # 연간 행이 nodata(값 없는 행)뿐인 회사는 n 에는 세지만 커버리지 분모에는 넣지 않는다
            if s:
                sec[(market, s)][8] += 1
        mk_rows, sec_rows = [], []
        for market, v in mk.items():
            # 260706 병합: 시장전체 행 = sector='_ALL' 센티넬. 축은 분류와 무관하므로 scheme='market'.
            pf, bf, pt, bm, nif, nit = _multiples(v)
            mk_rows.append((d, market, pf, bf, pt, bm, round(max(v[0], v[2], v[4], v[6])), nif, nit))
        for (market, s_), v in sec.items():
            # 이 섹터 버킷은 KSIC 하이브리드(bucket())다 — 업종분류는 sector_val_backfill.py 소관.
            if d >= KSIC_FROZEN_FROM:
                continue
            pf, bf, pt, bm, nif, nit = _multiples(v)
            sec_rows.append((d, market, s_, label(s_), v[8], round(max(v[0], v[2], v[4], v[6])), pf, bf, pt, bm, nif, nit))
        # 260829: 행마다 왕복하던 것을 월 단위로 묶는다. 한 달에 100회 넘게 부르던 것이 2회가 된다
        #   (실측: 15분 job 한도에 79개 월말 중 26개에서 잘렸다 — 원격 DB 라 지연이 곧 비용).
        with con.cursor() as cur:
            cur.executemany(UPSERT_MKT, mk_rows)
            cur.executemany(UPSERT_SEC, sec_rows)
        nmk += len(mk_rows); nsec += len(sec_rows)
        if months.index(d) % 12 == 0:
            print(f"  {d} … 시장 {nmk}행 + 섹터 {nsec}행", flush=True)
    print(f"✓ 과거 FY0+TTM/MRQ 밴드 저장: 시장 {nmk}행 + 섹터 {nsec}행 ({len(months)}개 월말)", flush=True)
    src.close()
    con.close()


if __name__ == "__main__":
    asyncio.run(main())
