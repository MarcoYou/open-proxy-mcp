#!/usr/bin/env python3
"""WICS 기준 시장·섹터 밸류에이션 집계 — `opm_val_market(scheme='wics_*')`. DART·KRX 0콜.

260823 신설. 종전 집계는 KSIC 하이브리드 하나뿐이었다. WICS(WiseIndex)는 벤더·리서치가 쓰는
다른 축이라 나란히 놓을 값이 있다 — 실측으로 그림이 실제로 다르다:

  KSIC 반도체·반도체장비(261+29271)   12사 2,937조   ← 세분 62버킷
  WICS 반도체와반도체장비             14사 3,093조   ← 하위업종 28
  WICS IT(대분류)                   69사 3,436조   ← 대분류 10

계산은 `market_val_history_backfill.py` 와 **완전히 같다**(로직 이중구현 방지 — `_pit_fy`·
`_pit_quarter`·`_ttm_ni`·`_mrq_eq` 를 valuation.py 에서 그대로 import). 갈아끼우는 것은
**섹터 버킷 하나**뿐이다.

★ 네 지표가 각각 독립 시총 분모를 쓴다. 한 종목이 ni_fy0 는 없고 ni_ttm 만 있어도 그 종목의
  시총이 per_fy0 분자에 섞이면 분자만 커지고 분모는 안 커져 PER 이 왜곡된다
  (260706 실측: KOSPI 2020-12 FY0 PER 32.7 → 42.8 오염). 원본과 같은 방식을 유지한다.

★ 과거 구간은 **소급**이다. WICS 는 조회 시점 구성종목만 주고 우리는 2026-08 부터 모았다.
  그래서 과거 월말에는 「그 날짜 이하의 가장 최근 스냅샷, 없으면 가장 이른 스냅샷」을 쓴다.
  = 지금 분류를 과거에 적용하는 것이고, 산출물에 `sector_asof` 로 그 사실을 남긴다.
  앞으로 월 1회 실측이 쌓이면 그 구간부터는 진짜 시점 분류가 된다.

scheme:
  wics_sector    대분류 10 (IT·산업재·금융 …)
  wics_industry  하위업종 28 (반도체와반도체장비·자본재 …) — KSIC 세분과 비교 가능한 층

실행: python3 scripts/wics_val_backfill.py [--since 20200101]
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

from open_proxy_mcp.market_codes import KQ as MKT_KQ, KS as MKT_KS
from open_proxy_mcp.services.valuation import _mrq_eq, _pit_fy, _pit_quarter, _ttm_ni

UPSERT = """
INSERT INTO opm_val_market
  (snap_dd, market, scheme, sector, label, n, cap, per_fy0, pbr_fy0, per_ttm, pbr_mrq, ni_fy0, ni_ttm)
VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
ON CONFLICT (snap_dd, market, scheme, sector) DO UPDATE SET
  label=EXCLUDED.label, n=EXCLUDED.n, cap=EXCLUDED.cap,
  per_fy0=EXCLUDED.per_fy0, pbr_fy0=EXCLUDED.pbr_fy0,
  per_ttm=EXCLUDED.per_ttm, pbr_mrq=EXCLUDED.pbr_mrq,
  ni_fy0=EXCLUDED.ni_fy0, ni_ttm=EXCLUDED.ni_ttm
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
    a = ap.parse_args()

    con = psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=30)
    con.autocommit = True
    for ddl in MIGRATE:
        con.execute(ddl)

    snaps = [r[0] for r in con.execute(
        "SELECT DISTINCT snap_dd FROM wise_sector ORDER BY snap_dd")]
    if not snaps:
        print("wise_sector 비어 있음 — refresh_wics.py 를 먼저 돌린다")
        return 1

    # 종목 → {snap_dd: (대분류, 대분류명, 하위업종, 하위업종명)}
    wics: dict[str, dict[str, tuple]] = defaultdict(dict)
    for t, sd, sc, s, ic, i in con.execute(
            "SELECT ticker, snap_dd, sector_code, sector, industry_code, industry FROM wise_sector"):
        wics[t][sd] = (sc, s, ic, i)

    fin_all: dict[str, dict] = {}
    for isu, fy, ni, eq, nir, eqr in con.execute(
            "SELECT ticker, fy, ni, eq, ni_restated, eq_restated FROM dart_finstat_y"):
        fin_all.setdefault(isu, {})[int(fy)] = (
            nir if nir is not None else ni, eqr if eqr is not None else eq)
    finq_all: dict[str, dict] = {}
    for isu, fy, q, ni_cum, eq in con.execute(
            "SELECT ticker, fy, quarter, COALESCE(ni_cum_restated, ni_cum), COALESCE(eq_restated, eq) FROM dart_finstat_q WHERE quarter != 4"):
        finq_all.setdefault(isu, {})[(int(fy), int(q))] = (ni_cum, eq)

    months = [r[0] for r in con.execute(
        "SELECT DISTINCT ON (substring(price_dd,1,6)) price_dd FROM krx_weekly "
        "WHERE price_dd>=%s ORDER BY substring(price_dd,1,6), price_dd DESC", (a.since,))]
    today = date.today()
    cur_ym = f"{today.year}{today.month:02d}"
    # 260823: 종전엔 현재월을 무조건 제외했다(월말 확정본만). 그런데 일간 배치가 이 스크립트를
    #   부르게 되면서, 제외하면 **사용자가 보는 섹터 배수가 최대 한 달 낡는다**
    #   (실측: KSIC 8/21 vs WICS 7/31). 일간 배치는 --include-current 로 최신 주까지 채운다.
    if not a.include_current:
        months = sorted(d for d in months if d[:6] < cur_ym)
    else:
        # 현재월은 「그 달의 최신 거래주」 — krx_weekly 최신 포인트를 쓴다
        months = sorted(months)
    print(f"대상 월말 {len(months)}개({months[0]}~{months[-1]}) · WICS 스냅샷 {snaps}", flush=True)

    n_rows = n_backfilled = 0
    for d in months:
        # 폴백: d 이하의 가장 최근 스냅샷, 없으면 가장 이른 것(=소급)
        use = max((s for s in snaps if s <= d), default=snaps[0])
        if use > d:
            n_backfilled += 1
        fy = _pit_fy(d)
        fy_q, q = _pit_quarter(d)
        caps = {r[0]: float(r[1] or 0) for r in con.execute(
            "SELECT ticker, mktcap FROM krx_weekly WHERE price_dd=%s", (d,))}

        # (market, scheme, code) → [cap_pf, ni_fy0, cap_bf, eq_fy0, cap_pt, ni_ttm, cap_bm, eq_mrq,
        #                            n, n_nif, n_nit]  (뒤 둘 = 분모를 실제로 더한 회사 수)
        acc: dict[tuple, list] = defaultdict(lambda: [0.0] * 8 + [0, 0, 0])
        label: dict[tuple, str] = {}
        # 시장 구분은 그 날짜의 krx_weekly 에서 (상장 시장이 바뀔 수 있으므로 날짜별로 읽는다)
        mkts = {r[0]: r[1] for r in con.execute(
            "SELECT ticker, market FROM krx_weekly WHERE price_dd=%s", (d,))}

        for isu, per_snap in wics.items():
            cls = per_snap.get(use)
            cap = caps.get(isu)
            market = mkts.get(isu)
            if not cls or not cap or market not in (MKT_KS, MKT_KQ):
                continue
            fin = fin_all.get(isu, {})
            f = fin.get(fy)
            ni_ttm = _ttm_ni(fin, finq_all.get(isu, {}), fy_q, q)
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

        rows = []
        for (market, scheme, code), (cpf, nif, cbf, eqf, cpt, nit, cbm, eqm, n, n_nif, n_nit) in acc.items():
            rows.append((d, market, scheme, code, label[(market, scheme, code)], n,
                         round(max(cpf, cbf, cpt, cbm)),
                         (cpf / nif) if nif > 0 else None, (cbf / eqf) if eqf > 0 else None,
                         (cpt / nit) if nit > 0 else None, (cbm / eqm) if eqm > 0 else None,
                         nif if n_nif else None, nit if n_nit else None))
        with con.cursor() as cur:
            cur.executemany(UPSERT, rows)
        n_rows += len(rows)
        if len(months) > 12 and months.index(d) % 20 == 0:
            print(f"  {d} … {n_rows:,}행", flush=True)

    print(f"\nWICS 집계 {n_rows:,}행 · 월말 {len(months)}개 (그중 소급 {n_backfilled}개)")
    for r in con.execute("SELECT scheme, count(*) FROM opm_val_market GROUP BY 1 ORDER BY 1"):
        print(f"  {r[0]:16s} {r[1]:,}행")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
