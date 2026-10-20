"""정정 칸은 그 출처 보고서가 공시된 날부터 — 서버 곡선·과거 시점과 두 과거 백필이 실제 SQL 로 같은 규칙(261020 p1g pit-restated).

종전엔 정정 칸(더 늦은 보고서의 전기·전전기 칸)을 출처 접수일과 상관없이 그 사업연도 칸으로 썼다. 10-20 배포 main 의 000680 곡선
2024-06-28 점 FY0 PBR 0.372 는 2026-03 사업보고서(접수 20260318)가 재작성한 FY2023 자본 9,277억으로 센 값이었다 — 그 시점에 공시돼 있던
자본은 5,640억(0.611). 아래 「A」 가 그 꼴이다(값은 운영 000680 연간 행 그대로, 분기 칸·시총은 합성).

  · A    — 출처 접수번호(`restated_rcept_no`)가 있는 정정 칸: 접수일 전엔 원본 칸, 그날부터 정정 칸. 지금 값(dict)은 정정 칸 그대로.
  · OLD  — 출처를 모르는 옛 정정 칸(옛 정정치 백필): 보수적 추정 공시일(FY2022 → 2024 사업보고서 공시 시점 2025-04-01)부터.
  · FILL — 원본이 빈 칸의 보충값, UNIT — 원본과 1,000배 어긋난 단위 고침: 처음부터 정정 값(종전 그대로).
  · QREV — 분기 정정 칸(근거 칸에 다음 해 같은 분기 보고서 접수번호): 그 접수일부터.
  · REIT — 결산기 칸(반기 결산 리츠): 근거 칸 「공시 YYYYMMDD」 전엔 값 없음(#153 의 칸 고르기와 같은 날짜).
Postgres 시험은 Docker 가 없으면 건너뛴다(`tests/conftest.py` 의 `pg17`).
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date

import pytest

psycopg = pytest.importorskip("psycopg")

from open_proxy_mcp.services import price_multiple_data as S        # noqa: E402
from scripts import market_val_history_backfill as H                # noqa: E402
from scripts import sector_val_backfill as SB                        # noqa: E402

E8 = 1e8
TODAY = date(2026, 10, 20)

FINSTAT_Y_DDL = """CREATE TABLE dart_finstat_y(
  ticker text, fy int, fs text, ni double precision, eq double precision, fetched text,
  ni_restated double precision, eq_restated double precision, restated_rcept_no text, PRIMARY KEY(ticker, fy))"""
FINSTAT_Q_DDL = """CREATE TABLE dart_finstat_q(
  ticker text, fy int, quarter int, reprt_code text, fs text,
  ni_cum double precision, eq double precision, fetched text, ni_case text, eq_case text,
  ni_cum_raw double precision, eq_raw double precision,
  ni_cum_restated double precision, eq_restated double precision, restate_why text,
  PRIMARY KEY(ticker, fy, quarter))"""

# (ticker, fy, ni, eq, ni_restated, eq_restated, 출처 접수번호, fetched)
Y_ROWS = [
    ("A", 2022, -23.97 * E8, 5518.8 * E8, None, None, None, "ok"),
    ("A", 2023, 107.05 * E8, 5640.1 * E8, 107.05 * E8, 9277.1 * E8, "20260318001521", "ok"),
    ("A", 2024, -439.58 * E8, 5459.5 * E8, -296.61 * E8, 9239.4 * E8, "20260318001521", "ok"),
    ("A", 2025, -319.44 * E8, 8924.7 * E8, None, None, None, "ok"),
    ("OLD", 2022, 10 * E8, 100 * E8, 12 * E8, 150 * E8, None, "ok"),
    ("OLD", 2023, 20 * E8, 120 * E8, None, None, None, "ok"),
    ("FILL", 2022, None, None, 30 * E8, 300 * E8, None, "nodata"),
    ("UNIT", 2022, 40 * E8 * 1000, 500 * E8 * 1000, 40 * E8, 500 * E8, None, "ok"),
    ("QREV", 2022, 100 * E8, 1000 * E8, None, None, None, "ok"),
    ("QREV", 2023, 120 * E8, 1100 * E8, None, None, None, "ok"),
    ("REIT", 2019, 10 * E8, 1000 * E8, None, None, None, "ok"),
    ("REIT", 2020, 12 * E8, 1010 * E8, None, None, None, "ok"),
    ("REIT2", 2023, 10 * E8, 2000 * E8, None, None, None, "ok"),
    ("IMP", 2022, -10 * E8, -50 * E8, -10 * E8, 80 * E8, "20240520000111", "ok"),     # 원본은 완전자본잠식, 재작성은 아님
    ("FB", 2023, 100 * E8, 1000 * E8, 200 * E8, 1000 * E8, "20250318000222", "ok"),
    ("QIMP", 2022, 10 * E8, 100 * E8, None, None, None, "ok"),
    ("PFIX", 2022, 590.87 * E8, 2159.55 * E8, 109.87 * E8, 2159.55 * E8, None, "ok"),   # 출처 모름 · 순이익만 다름 · 자본 같음 — 원본 칸 결함(세전이익 줄)
]
QREV_WHY = ("261020 검토: 이 보고서엔 순이익 쪽 비지배 줄이 없어 원본 칸은 근사(총포괄 쪽 비지배로 뺀 값) — 다음 해 같은 분기 보고서"
            "(2024 11012 CFS 접수 20240920001234)의 전기 칸에서 지배 귀속 순이익 5000000000원(CIS_ID)을 정정 칸에")
# (ticker, fy, quarter, ni_cum, eq, ni_cum_restated, eq_restated, restate_why)
Q_ROWS = [
    ("A", 2023, 1, 30 * E8, 5600 * E8, None, None, None),
    ("A", 2024, 1, 50 * E8, 5500 * E8, None, None, None),
    ("A", 2024, 3, 100 * E8, 5450 * E8, None, None, None),
    ("A", 2025, 1, 600 * E8, 5600 * E8, None, None, None),
    ("A", 2025, 3, 700 * E8, 5700 * E8, None, None, None),
    ("QREV", 2022, 2, 40 * E8, 990 * E8, None, None, None),
    ("QREV", 2023, 2, 70 * E8, 1050 * E8, 50 * E8, None, QREV_WHY),
    ("QREV", 2024, 2, 80 * E8, 1150 * E8, None, None, None),
    ("REIT", 2019, 4, 10 * E8, 1000 * E8, 25 * E8, 1000 * E8,
     "결산기 12개월(261020) 공시 20210319 — 사업연도 2019 = 201906~201912 결산기 2개 합 · 간격 6개월"),
    ("REIT", 2020, 4, 12 * E8, 1010 * E8, 26 * E8, 1010 * E8,
     "결산기 12개월(261020) 공시 20210319 — 사업연도 2020 = 202006~202012 결산기 2개 합 · 간격 6개월"),
    ("REIT2", 2023, 4, 10 * E8, 2000 * E8, 22 * E8, 2000 * E8,                    # 달력 규칙(2024-04-01)보다 늦게 공시된 결산기 칸
     "결산기 12개월(261020) 공시 20240415 — 사업연도 2023 = 202306~202312 결산기 2개 합 · 간격 6개월"),
    ("FB", 2023, 1, 30 * E8, 990 * E8, None, None, None),
    ("FB", 2024, 1, 40 * E8, 1010 * E8, None, None, None),                         # 2024 반기 칸 없음 — 업종 비교 기업 행은 앞선 칸(1분기)으로
    ("QIMP", 2022, 2, 2 * E8, 90 * E8, None, None, None),
    ("QIMP", 2023, 2, 5 * E8, -20 * E8, None, 30 * E8,                             # 반기 자본 원본 −20억(잠식) · 정정 +30억(2024-08-14 접수)
     "261020 검토: 다음 해 같은 분기 보고서(2024 11012 CFS 접수 20240814001111)의 전기 칸 자본을 정정 칸에"),
]
CAP = 3446 * E8
DATES = ["20231020", "20231229", "20240405", "20240628", "20240830", "20241227", "20250630", "20260320", "20261016"]
MKT = {"A": "KS", "OLD": "KQ", "FILL": "KQ", "UNIT": "KQ", "QREV": "KQ", "REIT": "KS", "REIT2": "KS", "IMP": "KQ", "FB": "KQ", "QIMP": "KQ", "PFIX": "KS"}
SECTOR = {"A": "G10", "OLD": "G20", "FILL": "G30", "UNIT": "G35", "QREV": "G40", "REIT": "G60", "REIT2": "G65", "IMP": "G70", "FB": "G75",
          "QIMP": "G80", "PFIX": "G85"}


def _exec(url):
    def run(sql, params=()):
        with psycopg.connect(url) as c:
            return c.execute(sql, params).fetchall()
    return run


@pytest.fixture
def rdb(pg17, monkeypatch):
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(FINSTAT_Y_DDL)
        c.execute(FINSTAT_Q_DDL)
        c.cursor().executemany("INSERT INTO dart_finstat_y (ticker, fy, ni, eq, ni_restated, eq_restated, restated_rcept_no, fetched, fs) "
                               "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'CFS')", Y_ROWS)
        c.cursor().executemany("INSERT INTO dart_finstat_q (ticker, fy, quarter, ni_cum, eq, ni_cum_restated, eq_restated, restate_why, "
                               "fetched, fs) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'ok','CFS')", Q_ROWS)
        c.execute("CREATE TABLE dart_fundamentals (ticker text PRIMARY KEY, corp_code text, market text, fs text, ni_fy double precision, "
                  "ni_ttm double precision, eq_fy double precision, eq_mrq double precision, fetched text, induty text, currency text, "
                  "acc_mt smallint)")
        c.cursor().executemany("INSERT INTO dart_fundamentals (ticker, market, induty, fetched, ni_fy, eq_fy) VALUES (%s,%s,'26410','ok',%s,%s)",
                               [(t, m, -319.44 * E8 if t == "A" else None, 8924.7 * E8 if t == "A" else None) for t, m in MKT.items()])
        c.execute("CREATE TABLE krx_weekly (price_dd text, ticker text, market text, close bigint, mktcap double precision, list_shrs bigint, "
                  "PRIMARY KEY (price_dd, ticker))")
        c.cursor().executemany("INSERT INTO krx_weekly VALUES (%s,%s,%s,1,%s,1)", [(d, t, m, CAP) for d in DATES for t, m in MKT.items()])
        c.execute("CREATE TABLE price_latest (ticker text PRIMARY KEY, price_dd text, mktcap double precision)")
        c.execute("CREATE TABLE wise_sector (snap_dd text, ticker text, sector_code text, sector text, industry_code text, industry text)")
        c.cursor().executemany("INSERT INTO wise_sector VALUES ('20231001',%s,%s,%s,%s,%s)",
                               [(t, s, s, s + "10", s + "10") for t, s in SECTOR.items()])
        c.execute("""CREATE TABLE opm_val_market (snap_dd text, market text, scheme text DEFAULT 'ksic', sector text, label text, n integer,
                     cap double precision, per_fy0 double precision, pbr_fy0 double precision, per_ttm double precision,
                     pbr_mrq double precision, ni_fy0 double precision, ni_ttm double precision, cap_pref double precision,
                     eq double precision, PRIMARY KEY (snap_dd, market, scheme, sector))""")
    monkeypatch.setattr(S, "_pg_rows", _exec(pg17))
    monkeypatch.setattr(S, "today_kst", lambda: TODAY)
    monkeypatch.setenv("DATABASE_URL", pg17)
    return pg17


def _fin(t):
    return asyncio.run(S._firm_fin_by_fy(t)), asyncio.run(S._firm_fin_by_q(t))


def _point(t, dd):
    fin, finq = _fin(t)
    fye = S._rc.with_cells(12, getattr(finq, "cells", None))
    return S._pit_raw(dd, CAP, fin, finq, fye)


# ── 서버: 곡선·과거 시점의 한 점(`_pit_raw` — 업종 비교 기업 행·주간 항목·과거 시점이 같은 함수) ──────────────

def test_latest_values_stay_the_restated_cells(rdb):
    """지금 값(dict)은 종전과 같다 — 정정 칸 그대로(최신 응답·firm 은 바뀌지 않는다)."""
    fin, _ = _fin("A")
    assert fin[2023] == (107.05 * E8, 9277.1 * E8)
    assert fin[2024] == (-296.61 * E8, 9239.4 * E8)
    assert _fin("OLD")[0][2022] == (12 * E8, 150 * E8)


def test_restated_equity_is_not_used_before_its_report_was_filed(rdb):
    """000680 꼴: 2024-06-28 의 FY0(=FY2023) 자본은 그때 공시돼 있던 5,640.1억 — 2026-03-18 접수 보고서의 재작성 값(9,277.1억)이 아니다."""
    p = _point("A", "20240628")
    assert p["pit_fy"] == 2023
    assert p["pbr"] == pytest.approx(CAP / (5640.1 * E8))
    assert p["per_ttm"] == pytest.approx(CAP / ((107.05 + 50 - 30) * E8))


def test_restated_net_income_is_not_used_before_its_report_was_filed(rdb):
    """2025-06-30: FY0(=FY2024) 자본 원본 5,459.5억 · TTM 의 직전 연간 순이익 원본 −439.58억(재작성 −296.61억은 2026-03-18 부터)."""
    p = _point("A", "20250630")
    assert p["pbr"] == pytest.approx(CAP / (5459.5 * E8))
    assert p["per_ttm"] == pytest.approx(CAP / ((-439.58 + 600 - 50) * E8))


def test_restated_cells_open_on_the_filing_date(rdb):
    """2026-03-20: FY0 는 아직 FY2024(4/1 전)지만 출처 보고서(20260318)가 나왔다 — 그날부터 정정 칸."""
    p = _point("A", "20260320")
    assert p["pit_fy"] == 2024
    assert p["pbr"] == pytest.approx(CAP / (9239.4 * E8))
    assert p["per_ttm"] == pytest.approx(CAP / ((-296.61 + 700 - 100) * E8))
    q = _point("A", "20261016")                                                   # 최신 점: FY2025(정정 칸 없음) — 종전과 같다
    assert q["pbr"] == pytest.approx(CAP / (8924.7 * E8))


def test_unknown_source_uses_the_conservative_filing_estimate(rdb):
    """출처를 모르는 옛 정정 칸(FY2022): 2024 사업보고서 공시 시점(2025-04-01) 전엔 원본."""
    assert _point("OLD", "20231229")["pbr"] == pytest.approx(CAP / (100 * E8))
    assert _point("OLD", "20231229")["per_fy0"] == pytest.approx(CAP / (10 * E8))


def test_fills_and_unit_fixes_are_used_from_the_start(rdb):
    """원본이 빈 보충값·10ⁿ 배 단위 고침은 처음부터 정정 값 — 종전 그대로(누락·오류 값으로 돌아가지 않는다)."""
    assert _point("FILL", "20231229")["pbr"] == pytest.approx(CAP / (300 * E8))
    u = _point("UNIT", "20231229")
    assert u["pbr"] == pytest.approx(CAP / (500 * E8)) and u["per_fy0"] == pytest.approx(CAP / (40 * E8))


def test_unknown_source_net_income_fix_with_the_same_equity_is_used_from_the_start(rdb):
    """출처 모르는 연간 칸에서 순이익만 다르고 자본이 같다 — 원본 칸이 다른 줄(세전이익)을 읽은 결함이고 정정 칸이 그 해 보고서 값(261020 p1g 검토).
    034730 FY2022 꼴: 2023-12-29 FY0 PER 은 정정 순이익 109.87억으로(원본 590.87억이면 PER 이 1/5 로 틀린다)."""
    p = _point("PFIX", "20231229")
    assert p["pit_fy"] == 2022 and p["per_fy0"] == pytest.approx(CAP / (109.87 * E8))
    assert p["pbr"] == pytest.approx(CAP / (2159.55 * E8))


def test_quarterly_restated_cell_waits_for_the_next_years_report(rdb):
    """분기 정정 칸(근거 칸 접수 20240920 — 다음 해 같은 분기 보고서가 기한(8/14)보다 늦게 나온 꼴): 2023-10-20 · 2024-08-30 의 TTM 은 원본 누적 70억,
    2024-10-10 부터 정정 50억(근거 칸의 접수일 — 추정 공시일 8/14 가 아니다)."""
    assert _point("QREV", "20231020")["per_ttm"] == pytest.approx(CAP / ((100 + 70 - 40) * E8))
    assert _point("QREV", "20240830")["per_ttm"] == pytest.approx(CAP / ((120 + 80 - 70) * E8))
    assert _point("QREV", "20241010")["per_ttm"] == pytest.approx(CAP / ((120 + 80 - 50) * E8))


def test_period_cells_are_not_used_before_their_filing_date(rdb):
    """결산기 칸(리츠): 2020-12-30 의 FY0(=FY2019) 칸은 2021-03-19 공시 — 그 전엔 값 없음(원본 한 결산기 값도 쓰지 않는다)."""
    p = _point("REIT", "20201230")
    assert p["pit_fy"] == 2019 and p["pbr"] is None and p["per_fy0"] is None
    q = _point("REIT", "20210402")
    assert q["pbr"] == pytest.approx(CAP / (1010 * E8)) and q["per_fy0"] == pytest.approx(CAP / (26 * E8))


def test_period_cell_filed_after_the_calendar_rule_waits_for_its_filing(rdb):
    """결산기 칸 공시일(2024-04-15)이 달력 규칙(2024-04-01)보다 늦으면 그 사이 FY0 는 값 없음(종전엔 미래 값)."""
    p = _point("REIT2", "20240405")
    assert p["pit_fy"] == 2023 and p["pbr"] is None
    assert _point("REIT2", "20240628")["pbr"] == pytest.approx(CAP / (2000 * E8))


def test_impairment_is_judged_on_the_equity_public_at_that_date(rdb):
    """원본 자본 −50억(완전자본잠식)·재작성 +80억(2024-05-20 접수): 2023-12-29 엔 잠식 — 업종 비교 기업 행을 내지 않는다(종전엔 재작성 값으로 냈다)."""
    fin, finq = _fin("IMP")
    assert S._row_val("20231229", CAP, fin, finq, 12) is None
    assert S._pit_raw("20231229", CAP, fin, finq, 12)["pbr"] is None


def test_impairment_on_the_latest_quarter_equity_public_at_that_date(rdb):
    """그 시점 분기 자본(MRQ)이 원본 −20억(잠식) · 정정 +30억(2024-08-14 접수): 2023-10-20 엔 잠식 — 기업 행 없음. 접수 뒤(전년 동기 칸으로 쓰일 때)는 정정 값."""
    fin, finq = _fin("QIMP")
    assert S._row_val("20231020", CAP, fin, finq, 12) is None
    assert S._rpit.at(finq, "20240830")[(2023, 2)] == (5 * E8, 30 * E8)


def test_fallback_cells_follow_the_rule(rdb):
    """그 시점 칸(2024 반기)이 비어 앞선 칸(1분기)으로 세는 업종 비교 기업 행 — 그 TTM 의 직전 연간도 그 날 값(원본 100억, 재작성 200억은 2025-03-18 부터)."""
    fin, finq = _fin("FB")
    rv = S._row_val("20240830", CAP, fin, finq, 12)
    assert rv["basis"] == {"pit_q": "2024Q2", "per_ttm": "2024Q1", "pbr_mrq": "2024Q1"}
    assert rv["per_ttm"] == pytest.approx(CAP / ((100 + 40 - 30) * E8))
    assert rv["per_fy0"] == pytest.approx(CAP / (100 * E8))


def test_year_end_band_follows_the_rule(rdb):
    """연말 밴드(`history` 의 연말 항목): 2024 연말의 FY0(=FY2023) 자본은 원본 5,640.1억."""
    fin, _ = _fin("A")
    band = {b["period"]: b for b in asyncio.run(S._annual_pit_band("A", fin=fin, fye=12))}
    assert band["2024"]["pbr_fy0"] == S._round_mult(CAP / (5640.1 * E8), 2)
    assert band["2023"]["pbr_fy0"] == S._round_mult(CAP / (5518.8 * E8), 2)


def test_foreign_currency_converts_the_pre_filing_values_too(rdb, monkeypatch):
    """외화 보고사: 출처 공시 전 값도 같은 환율로 환산한다."""
    async def fx(ccy, dd):
        return 1000.0
    monkeypatch.setattr(S, "fx_to_krw", fx)
    fin = asyncio.run(S._firm_fin_by_fy("A", "USD"))
    assert fin[2023] == (107.05 * E8 * 1000, 9277.1 * E8 * 1000)
    assert S._rpit.at(fin, "20240628")[2023] == (107.05 * E8 * 1000, 5640.1 * E8 * 1000)
    finq = asyncio.run(S._firm_fin_by_q("QREV", "USD"))
    assert S._rpit.at(finq, "20231020")[(2023, 2)] == (70 * E8 * 1000, 1050 * E8 * 1000)
    assert finq[(2023, 2)] == (50 * E8 * 1000, 1050 * E8 * 1000)


def test_firm_history_curve_and_as_of_points_follow_the_rule(rdb):
    """곡선(`_weekly_series`)의 점이 같은 규칙 — 주간 시세 행마다."""
    fin, finq = _fin("A")
    pts = {p["asof"]: p for p in asyncio.run(S._weekly_series("A", fin=fin, finq=finq, since_dd="20231016", fye=12))}
    assert pts["20240628"]["pbr"] == S._r3(CAP / (5640.1 * E8))
    assert pts["20260320"]["pbr"] == S._r3(CAP / (9239.4 * E8))


def test_server_reads_a_table_without_the_source_column(rdb):
    """출처 칸이 없는 표(그 칸을 만드는 이행 전)도 읽는다 — 모두 추정 공시일."""
    with psycopg.connect(rdb, autocommit=True) as c:
        c.execute("ALTER TABLE dart_finstat_y DROP COLUMN restated_rcept_no")
    fin, _ = _fin("A")
    assert fin[2023] == (107.05 * E8, 9277.1 * E8)
    # FY2023 추정 공시일 = 2025 사업보고서 공시 시점(2026-04-01) — 2024-06-28 엔 원본
    assert _point("A", "20240628")["pbr"] == pytest.approx(CAP / (5640.1 * E8))


# ── 두 과거 백필(시장·업종 월말 집계) ───────────────────────────────────────────────

def _val(url, dd, market, scheme, sector):
    with psycopg.connect(url) as c:
        r = c.execute("SELECT per_fy0, pbr_fy0, per_ttm, pbr_mrq FROM opm_val_market WHERE snap_dd=%s AND market=%s AND scheme=%s "
                      "AND sector=%s", (dd, market, scheme, sector)).fetchone()
    return dict(zip(("per_fy0", "pbr_fy0", "per_ttm", "pbr_mrq"), r)) if r else None


def test_market_and_sector_backfills_follow_the_same_rule(rdb, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["market_val_history_backfill.py", "--since", "20230101"])
    asyncio.run(H.main())
    monkeypatch.setattr(sys, "argv", ["sector_val_backfill.py", "--since", "20230101"])
    assert SB.main() == 0
    a = _val(rdb, "20240628", "KS", "wics_sector", "G10")
    assert a["pbr_fy0"] == pytest.approx(CAP / (5640.1 * E8))                    # 000680 꼴 — 원본 자본
    a25 = _val(rdb, "20250630", "KS", "wics_sector", "G10")
    assert a25["pbr_fy0"] == pytest.approx(CAP / (5459.5 * E8))
    assert a25["per_ttm"] == pytest.approx(CAP / ((-439.58 + 600 - 50) * E8))
    a26 = _val(rdb, "20260320", "KS", "wics_sector", "G10")
    assert a26["pbr_fy0"] == pytest.approx(CAP / (9239.4 * E8))                  # 출처 공시 뒤 — 정정 칸
    assert _val(rdb, "20231229", "KQ", "wics_sector", "G20")["pbr_fy0"] == pytest.approx(CAP / (100 * E8))     # 출처 모름 — 추정일 전 원본
    assert _val(rdb, "20231229", "KQ", "wics_sector", "G30")["pbr_fy0"] == pytest.approx(CAP / (300 * E8))     # 보충값
    assert _val(rdb, "20231229", "KQ", "wics_sector", "G35")["pbr_fy0"] == pytest.approx(CAP / (500 * E8))     # 단위 고침
    assert _val(rdb, "20231020", "KQ", "wics_sector", "G40")["per_ttm"] == pytest.approx(CAP / (130 * E8))     # 분기 정정 칸 전
    assert _val(rdb, "20240830", "KQ", "wics_sector", "G40")["per_ttm"] == pytest.approx(CAP / (130 * E8))     # 접수(0920) 전
    assert _val(rdb, "20240405", "KS", "wics_sector", "G65")["pbr_fy0"] is None                         # 결산기 칸 공시 전
    assert _val(rdb, "20240628", "KS", "wics_sector", "G65")["pbr_fy0"] == pytest.approx(CAP / (2000 * E8))
    assert _val(rdb, "20231229", "KQ", "wics_sector", "G70") is None                                      # 그 시점 잠식 — 집계에서 뺌
    assert _val(rdb, "20231229", "KS", "wics_sector", "G85")["per_fy0"] == pytest.approx(CAP / (109.87 * E8))  # 원본 칸 결함 — 정정 값
    # 시장 백필: 코스피 = A + REIT(2024-06 엔 FY2023 행 없음) + REIT2(결산기 칸 FY2023 2,000억)
    ks = _val(rdb, "20240628", "KS", "market", "_ALL")
    assert ks["pbr_fy0"] == pytest.approx(2 * CAP / ((5640.1 + 2000) * E8))
