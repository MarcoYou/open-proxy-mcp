#!/usr/bin/env python3
"""업종분류 기준 시장·섹터 밸류에이션 집계 — `opm_val_market`(업종분류 scheme). DART·KRX 0콜.

260823 신설. 종전 집계는 KSIC 하이브리드 하나뿐이었다. 업종분류는 벤더·리서치가 쓰는
다른 축이라 나란히 놓을 값이 있다 — 실측으로 그림이 실제로 다르다:

  KSIC 반도체·반도체장비(261+29271)   12사 2,937조   ← 세분 62버킷
  중분류 반도체와반도체장비             14사 3,093조   ← 하위업종 28
  대분류 IT                           69사 3,436조   ← 대분류 10

계산은 `market_val_history_backfill.py` 와 **완전히 같다**(로직 이중구현 방지 — `_pit_fy`·
`_pit_quarter`·`_ttm_ni`·`_mrq_eq` 를 valuation.py 에서 그대로 import). 갈아끼우는 것은
**섹터 버킷 하나**뿐이다.

★ 네 지표가 각각 독립 시총 분모를 쓴다. 한 종목이 ni_fy0 는 없고 ni_ttm 만 있어도 그 종목의
  시총이 per_fy0 분자에 섞이면 분자만 커지고 분모는 안 커져 PER 이 왜곡된다
  (260706 실측: KOSPI 2020-12 FY0 PER 32.7 → 42.8 오염). 원본과 같은 방식을 유지한다.

★ 과거 구간은 **소급**이다. 분류 공급처는 조회 시점 구성종목만 주고 우리는 2026-08 부터 모았다.
  그래서 과거 월말에는 「그 날짜 이하의 가장 최근 스냅샷, 없으면 가장 이른 스냅샷」을 쓴다.
  = 지금 분류를 과거에 적용하는 것이고, 산출물에 `sector_asof` 로 그 사실을 남긴다.
  앞으로 월 1회 실측이 쌓이면 그 구간부터는 진짜 시점 분류가 된다.

scheme:
  wics_sector    대분류 10 (IT·산업재·금융 …)
  wics_industry  하위업종 28 (반도체와반도체장비·자본재 …) — KSIC 세분과 비교 가능한 층

★ 🔴 261019: 월말 날짜·시총은 `scripts/month_end_prices.py` 에서 고른다. DB krx_weekly 는 보존 하한 이전을 종목별 연말 행만 남겨
  종전 고르기(`DISTINCT ON (월) max(price_dd)`)는 하한 이전 달마다 상장폐지 종목 1~8개짜리 날짜를 월말로 집는다(업종분류 월간 일정이 기본
  --since 20200101 로 돈다 — 10-19 실측 2020-01~2023-09 의 41개 달 중 37개. 그날은 그 종목들이 분류에 없어 집계 0행 — 분류가 남은 종목이
  걸리면 그 집계가 저장된 행을 덮는다). 이제 하한 이전은 연말(12월)만
  DB 로, 나머지 달은 콜드 서빙본(`--cold-sqlite`)이 있을 때만 다시 내고 없으면 건너뛴다(저장된 행 그대로).
★ 261019: 최소 커버리지 — 배수를 낸 회사 시총이 그 버킷에서 재무가 하나라도 있는 회사 시총의 절반 미만이면 그 배수·분모 합을 비운다
  (`month_end_prices.covered`, 시장 백필과 같은 규칙).
★ 261019: 값이 같은 행은 다시 쓰지 않는다(`WHERE … IS DISTINCT FROM`) — 시장 백필(261018)과 같게. 월간 일정이 2020 년 이후 업종 행
  전부(약 6천 행)를 같은 값으로 다시 써 죽은 행만 남기던 것.
★ 261019: `--with-stored-weekly` — 월말에 더해 이미 저장된 업종 주간 점(2026-08-21~, 매일 배치가 그 주의 현재 점으로 쓴 것)도 같은 산식으로
  다시 낸다. 재무를 다시 받은 뒤 월말만 다시 내면 사이 주간 점이 옛 재무 값으로 남아 곡선이 톱니가 된다(10-19 운영: 코스닥 에너지 PER(FY0)
  월말 0828·0923 은 136.2·134.8 인데 사이 주간 점 0904~0918 은 89.9~101.0 — 다시 낸 뒤 129.3~145.0). 매일 배치(market-val-weekly)가
  이 플래그로 돈다 — 재무 고침·재작성치가 들어와도 월말과 주간 점이 함께 바뀐다(값이 같으면 쓰지 않는다).

실행: python3 scripts/sector_val_backfill.py [--since 20200101] [--include-current] [--cold-sqlite <서빙본.sqlite>] [--with-stored-weekly]
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")
import psycopg

from open_proxy_mcp.krx_daily import LATEST_DAY_SQL
from open_proxy_mcp.market_codes import KQ as MKT_KQ, KS as MKT_KS
from open_proxy_mcp.services.price_multiple_data import _mrq_eq, _pit_fy, _pit_quarter, _ttm_ni
from open_proxy_mcp.services import report_calendar as rcal
from scripts.month_end_prices import MonthEndPrices, covered

UPSERT = """
INSERT INTO opm_val_market
  (snap_dd, market, scheme, sector, label, n, cap, per_fy0, pbr_fy0, per_ttm, pbr_mrq, ni_fy0, ni_ttm)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (snap_dd, market, scheme, sector) DO UPDATE SET
  label=EXCLUDED.label, n=EXCLUDED.n, cap=EXCLUDED.cap,
  per_fy0=EXCLUDED.per_fy0, pbr_fy0=EXCLUDED.pbr_fy0,
  per_ttm=EXCLUDED.per_ttm, pbr_mrq=EXCLUDED.pbr_mrq,
  ni_fy0=EXCLUDED.ni_fy0, ni_ttm=EXCLUDED.ni_ttm
WHERE (opm_val_market.label, opm_val_market.n, opm_val_market.cap, opm_val_market.per_fy0, opm_val_market.pbr_fy0,
       opm_val_market.per_ttm, opm_val_market.pbr_mrq, opm_val_market.ni_fy0, opm_val_market.ni_ttm)
  IS DISTINCT FROM (EXCLUDED.label, EXCLUDED.n, EXCLUDED.cap, EXCLUDED.per_fy0, EXCLUDED.pbr_fy0,
                    EXCLUDED.per_ttm, EXCLUDED.pbr_mrq, EXCLUDED.ni_fy0, EXCLUDED.ni_ttm)
"""

# 260829: PER 이 비었을 때 「적자」인지 「자료없음」인지 가르려면 **분모를 같이 남겨야** 한다.
#   합이 0.0 인 것과 더한 회사가 하나도 없는 것이 지금은 구별되지 않는다 — 그래서 값이 아니라
#   **더한 회사 수**로 판정하고(n_nif/n_nit), 0 이면 NULL 을 넣는다.
MIGRATE = (
    "ALTER TABLE opm_val_market ADD COLUMN IF NOT EXISTS ni_fy0 double precision",
    "ALTER TABLE opm_val_market ADD COLUMN IF NOT EXISTS ni_ttm double precision",
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="20200101")
    ap.add_argument("--include-current", action="store_true",
                    help="현재월도 포함(일간 배치용). 기본은 제외 — 월말 확정본만 쌓는다")
    ap.add_argument("--cold-sqlite", metavar="PATH",
                    help="보존 하한 이전 달을 콜드 서빙본(하한 이전 주간 시세 전 행 sqlite)으로 다시 낸다 — 없으면 그 달은 건너뛴다(261019)")
    ap.add_argument("--with-stored-weekly", action="store_true",
                    help="이미 저장된 업종 주간 점(월말이 아닌 날짜)도 같은 산식으로 다시 낸다 — 재무를 다시 받은 뒤 전 구간 재계산용(261019)")
    a = ap.parse_args()

    con = psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=30)
    con.autocommit = True
    for ddl in MIGRATE:
        con.execute(ddl)

    snaps = [r[0] for r in con.execute(
        "SELECT DISTINCT snap_dd FROM wise_sector ORDER BY snap_dd")]
    if not snaps:
        print("분류 스냅샷 표가 비어 있음 — refresh_sector_class.py 를 먼저 돌린다")
        return 1

    # 종목 → {snap_dd: (대분류, 대분류명, 하위업종, 하위업종명)}
    classes: dict[str, dict[str, tuple]] = defaultdict(dict)
    for t, sd, sc, s, ic, i in con.execute(
            "SELECT ticker, snap_dd, sector_code, sector, industry_code, industry FROM wise_sector"):
        classes[t][sd] = (sc, s, ic, i)

    fin_all: dict[str, dict] = {}
    for isu, fy, ni, eq, nir, eqr in con.execute(
            "SELECT ticker, fy, ni, eq, ni_restated, eq_restated FROM dart_finstat_y"):
        fin_all.setdefault(isu, {})[int(fy)] = (
            nir if nir is not None else ni, eqr if eqr is not None else eq)
    finq_all: dict[str, dict] = {}
    for isu, fy, q, ni_cum, eq in con.execute(
            "SELECT ticker, fy, quarter, COALESCE(ni_cum_restated, ni_cum), COALESCE(eq_restated, eq) FROM dart_finstat_q WHERE quarter != 4"):
        finq_all.setdefault(isu, {})[(int(fy), int(q))] = (ni_cum, eq)

    # 261018(재설계 6단계): 종목 결산월 — 칸 고르기·TTM 직전 연간을 firm_history 와 같은 매핑으로(12월 결산은 종전과 같다).
    fye_of = rcal.load_fye_map(con)

    # 261019: 월말 = 그 달의 진짜 마지막 주간 날짜를 말할 수 있는 달만(하한 이전은 연말·콜드 서빙본 — `month_end_prices`).
    src = MonthEndPrices(con, a.since, a.cold_sqlite)
    print(src.describe(), flush=True)
    months = list(src.plan.dates)
    keep: set[str] = set()
    if a.with_stored_weekly:
        # 저장된 업종 날짜 가운데 DB 촘촘한 주간 구간의 닫힌 주 날짜(그 날 시세가 온전하다) 전부 — 월말이 아닌 점 = 매일 배치가 남긴 주간 점.
        #   현재월 제외·현재 점 바꿔 끼우기(아래) 뒤에 다시 더한다 — 이번 달의 저장된 점도 빠짐없이 다시 낸다.
        stored = {r[0] for r in con.execute(
            "SELECT DISTINCT snap_dd FROM opm_val_market WHERE scheme IN ('wics_sector', 'wics_industry') AND snap_dd >= %s", (a.since,))}
        keep = {d for d in stored if d in src.dense}
        for d in keep:
            src.plan.src[d] = "db"
        extra = sorted(keep - set(months))
        if extra:
            print(f"저장된 업종 주간 점 {len(extra)}개도 다시 낸다({extra[0]}~{extra[-1]})", flush=True)
    today = date.today()
    cur_ym = f"{today.year}{today.month:02d}"
    # 260823: 종전엔 현재월을 무조건 제외했다(월말 확정본만). 그런데 일간 배치가 이 스크립트를
    #   부르게 되면서, 제외하면 **사용자가 보는 섹터 배수가 최대 한 달 낡는다**
    #   (실측: KSIC 8/21 vs 업종분류 7/31). 일간 배치는 --include-current 로 최신 주까지 채운다.
    daily_dd = None
    if not a.include_current:
        months = sorted(d for d in months if d[:6] < cur_ym)
    else:
        # 현재 점은 「일별 시세 저장분의 최신 거래일」 — 261018(재설계 5-1 쓰는 쪽): krx_weekly 는 닫힌 주만 담아 주중에는
        #   지난 금요일이 최신이라, 그대로 쓰면 섹터 배수가 최대 한 주 낡는다. 같은 달이면 그 달의 점을 바꾸고 새 달이면 덧붙인다.
        #   주중 임시 점은 같은 배치의 `market_val_weekly`(같은 주 수렴)가 주마다 한 점으로 정리한다.
        months = sorted(months)
        latest = con.execute(LATEST_DAY_SQL).fetchone()[0]          # 261019: 일별 최신일 = 달력(krx_day)
        if latest and (not months or latest > months[-1]):
            daily_dd = latest
            if months and months[-1][:6] == latest[:6]:
                months[-1] = latest
            else:
                months.append(latest)
    months = sorted(set(months) | keep)
    if not months:
        print("다시 낼 월말이 없다", flush=True)
        src.close(); con.close()
        return 0
    print(f"대상 월말 {len(months)}개({months[0]}~{months[-1]}) · 업종분류 스냅샷 {snaps}", flush=True)

    n_rows = n_backfilled = 0
    for d in months:
        # 폴백: d 이하의 가장 최근 스냅샷, 없으면 가장 이른 것(=소급)
        use = max((s for s in snaps if s <= d), default=snaps[0])
        if use > d:
            n_backfilled += 1
        pit = {rcal.Fiscal(): (_pit_fy(d), _pit_quarter(d))}   # 공시 달력(결산월·외국 상장사 기한) → (그 시점 FY, (분기 칸)) — 12월 결산은 날짜마다 한 번
        if d == daily_dd:
            px = con.execute("SELECT ticker, market, mktcap FROM krx_daily_v WHERE trd_dd=%s", (d,)).fetchall()
        else:
            px = src.rows(d)
        caps = {t: float(c or 0) for t, _m, c in px}

        # (market, scheme, code) → [cap_pf, ni_fy0, cap_bf, eq_fy0, cap_pt, ni_ttm, cap_bm, eq_mrq,
        #                            n, n_nif, n_nit, cap_any]  (n_nif·n_nit = 분모를 실제로 더한 회사 수,
        #                            cap_any = 그 버킷에 센 회사의 시총 합 — 최소 커버리지의 분모, 261019)
        acc: dict[tuple, list] = defaultdict(lambda: [0.0] * 8 + [0, 0, 0] + [0.0])
        label: dict[tuple, str] = {}
        # 시장 구분은 그 날짜의 시세에서 (상장 시장이 바뀔 수 있으므로 날짜별로 읽는다)
        mkts = {t: m for t, m, _c in px}

        for isu, per_snap in classes.items():
            cls = per_snap.get(use)
            cap = caps.get(isu)
            market = mkts.get(isu)
            if not cls or not cap or market not in (MKT_KS, MKT_KQ):
                continue
            fye = rcal.fiscal(fye_of.get(isu, 12), isu)
            if fye not in pit:
                pit[fye] = (_pit_fy(d, fye), _pit_quarter(d, fye))
            fy, (fy_q, q) = pit[fye]
            fin = fin_all.get(isu, {})
            f = fin.get(fy)
            ni_ttm = _ttm_ni(fin, finq_all.get(isu, {}), fy_q, q, fye)
            eq_mrq = _mrq_eq(fin, finq_all.get(isu, {}), fy_q, q)
            if not f and ni_ttm is None and eq_mrq is None:
                continue
            ni_fy0, eq_fy0 = f if f else (None, None)
            # 260829: 완전자본잠식은 모든 집계에서 뺀다(마스터 지시). 자본이 0 이하면 배수가
            #   뜻을 잃고, 그 적자가 분모에 섞여 시장·섹터 값을 흔든다.
            _eq_now = eq_mrq if eq_mrq is not None else eq_fy0
            if _eq_now is not None and _eq_now <= 0:
                continue
            sc, s_nm, ic, i_nm = cls
            for scheme, code, nm in (("wics_sector", sc, s_nm), ("wics_industry", ic, i_nm)):
                k = (market, scheme, code)
                label[k] = nm
                v = acc[k]
                if ni_fy0 is not None: v[0] += cap; v[1] += ni_fy0; v[9] += 1
                if eq_fy0 is not None: v[2] += cap; v[3] += eq_fy0
                if ni_ttm is not None: v[4] += cap; v[5] += ni_ttm; v[10] += 1
                if eq_mrq is not None: v[6] += cap; v[7] += eq_mrq
                v[8] += 1
                if (ni_fy0, eq_fy0, ni_ttm, eq_mrq) != (None, None, None, None):
                    v[11] += cap   # 연간 행이 nodata(값 없는 행)뿐인 회사는 n 에는 세지만 커버리지 분모에는 넣지 않는다

        rows = []
        for (market, scheme, code), (cpf, nif, cbf, eqf, cpt, nit, cbm, eqm, n, n_nif, n_nit, c_any) in acc.items():
            # 261019: 낸 회사 시총이 버킷의 절반 미만이면 배수·분모 합 모두 None(「자료없음」 — 분모 합이 남으면 「적자」로 읽힌다).
            ok_f, ok_bf, ok_t, ok_bm = (covered(c, c_any) for c in (cpf, cbf, cpt, cbm))
            rows.append((d, market, scheme, code, label[(market, scheme, code)], n,
                         round(max(cpf, cbf, cpt, cbm)),
                         (cpf / nif) if ok_f and nif > 0 else None, (cbf / eqf) if ok_bf and eqf > 0 else None,
                         (cpt / nit) if ok_t and nit > 0 else None, (cbm / eqm) if ok_bm and eqm > 0 else None,
                         nif if ok_f and n_nif else None, nit if ok_t and n_nit else None))
        with con.cursor() as cur:
            cur.executemany(UPSERT, rows)
        n_rows += len(rows)
        if len(months) > 12 and months.index(d) % 20 == 0:
            print(f"  {d} … {n_rows:,}행", flush=True)

    print(f"\n업종분류 집계 {n_rows:,}행 · 월말 {len(months)}개 (그중 소급 {n_backfilled}개)")
    for r in con.execute("SELECT scheme, count(*) FROM opm_val_market GROUP BY 1 ORDER BY 1"):
        print(f"  {r[0]:16s} {r[1]:,}행")
    src.close()
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
