# -*- coding: utf-8 -*-
"""TTM 순이익은 **같은 재무제표 기준(연결 CFS · 별도 OFS)의 세 칸끼리만** 더하고 뺀다(261021, fin audit L3).

TTM = 직전 연간 + 당해 누적 − 전년 누적. 종전 DB 경로(곡선·업종 비교·「주간」 항목·재무 요약 파생 `ni_ttm`·두 과거 시장/업종 백필)는 세 칸의 기준을 보지 않았다.
한 해 안에 연결·별도가 섞인 종목(연결을 새로 냈거나 종속회사를 처분해 별도만 내게 된 회사)은 연결 연간 + 별도 누적 − 연결 누적처럼 **다른 재무제표끼리**
계산한 값이 나갔다. 운영 읽기 전용 실측(2024~26, 12월 결산): TTM 점 19,768 중 세 칸 기준이 서로 다른 점 813(265종목), 한 해 안에 섞인 160종목 안 567점.
서버 실시간 firm 은 연간에서 정한 기준을 분기에 강제해(`_acntall(…, fs_used)`) 이미 같다 — DB 경로를 그와 맞춘다.

  · 순수: `_ttm_ni`·`_ttm_mixed`·`_ttm_blocked`(기준을 모르는 칸은 보지 않는다 — 종전 그대로), 재무 요약 파생 `ttm_from_cells`·`pick_ttm`.
  · 실제 SQL(Postgres): 서버가 칸마다 기준을 읽는다(**원본 순이익이 있는 칸만** — 값 없는 칸의 `fs` 는 마지막으로 시도한 기준일 뿐), 곡선은 N/M + `per_ttm_why`,
    업종 비교·「주간」 항목은 앞선 칸으로 세고 `basis.per_ttm_why`, 재무 요약 파생은 앞선 칸 TTM, 두 백필은 그 종목 TTM 을 집계에서 뺀다.
Postgres 시험은 Docker 가 없으면 건너뛴다(`tests/conftest.py` 의 `pg17`).
"""
from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import sys
from datetime import date

import pytest

from open_proxy_mcp.services import price_multiple_data as S
from open_proxy_mcp.services import restated_pit as RP

E8 = 1e8


def _mq():
    spec = importlib.util.spec_from_file_location("mfq261021", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "market_fund_quarterly.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _fins(fin, fin_fs, finq, finq_fs):
    f, q = RP.PitFin(fin), RP.PitFin(finq)
    f.fs, q.fs = dict(fin_fs), dict(finq_fs)
    return f, q


# ═══ 순수 — 서버 `_ttm_ni` ═══════════════════════════════════════════════════════════════════════════
FIN = {2023: (60.0, 560.0)}
FINQ = {(2023, 1): (10.0, 520.0), (2024, 1): (30.0, 600.0)}


def test_same_basis_sums_as_before():
    fin, finq = _fins(FIN, {2023: "CFS"}, FINQ, {(2023, 1): "CFS", (2024, 1): "CFS"})
    assert S._ttm_ni(fin, finq, 2024, 1) == 60.0 + 30.0 - 10.0
    assert not S._ttm_mixed(fin, finq, 2024, 1) and not S._ttm_blocked(fin, finq, 2024, 1)


@pytest.mark.parametrize("fy_fs,now_fs,prev_fs", [
    ("CFS", "OFS", "OFS"),     # 연간만 연결 — 당해·전년 누적은 별도
    ("OFS", "OFS", "CFS"),     # 전년 누적만 연결 (운영 004770 꼴)
    ("CFS", "OFS", "CFS"),     # 당해 누적만 별도
])
def test_mixed_basis_is_blank_not_a_number_made_of_different_statements(fy_fs, now_fs, prev_fs):
    fin, finq = _fins(FIN, {2023: fy_fs}, FINQ, {(2023, 1): prev_fs, (2024, 1): now_fs})
    assert S._ttm_ni(fin, finq, 2024, 1) is None
    assert S._ttm_mixed(fin, finq, 2024, 1) and S._ttm_blocked(fin, finq, 2024, 1)


def test_unknown_basis_is_not_a_reason_to_blank():
    """기준을 모르는 칸(값 없는 칸의 보충값 · `fs` 칸이 없는 표 · 기준 속성이 없는 dict)은 보지 않는다 — 종전 값 그대로. 아는 것끼리만 맞춘다."""
    fin, finq = _fins(FIN, {2023: "CFS"}, FINQ, {(2024, 1): "CFS"})            # 전년 누적(2023,1)의 기준을 모른다
    assert S._ttm_ni(fin, finq, 2024, 1) == 80.0
    assert S._ttm_ni(dict(FIN), dict(FINQ), 2024, 1) == 80.0, "기준 속성이 없는 일반 dict(옛 호출·시험)"
    fin2, finq2 = _fins(FIN, {2023: "CFS"}, FINQ, {(2024, 1): "OFS"})          # 아는 두 칸이 다르면 섞인 것
    assert S._ttm_ni(fin2, finq2, 2024, 1) is None


def test_year_end_cell_is_one_value_and_cannot_be_mixed():
    fin, finq = _fins(FIN, {2023: "OFS"}, FINQ, {(2023, 1): "CFS", (2024, 1): "CFS"})
    assert S._ttm_ni(fin, finq, 2023, 4) == 60.0 and not S._ttm_blocked(fin, finq, 2023, 4)


def test_missing_values_are_empty_not_blocked():
    """값이 없어 빈 칸은 「기준이 섞여서」가 아니다 — 곡선·`basis` 가 이유를 거짓으로 적지 않게."""
    fin, finq = _fins(FIN, {2023: "CFS"}, {(2024, 1): (30.0, 600.0)}, {(2024, 1): "OFS"})
    assert S._ttm_ni(fin, finq, 2024, 1) is None and not S._ttm_blocked(fin, finq, 2024, 1)


def test_point_in_time_view_keeps_the_bases():
    """정정 칸 문(`at`)이 만든 시점 사본도 기준을 그대로 들고 간다 — 사본에서 규칙이 꺼지면 과거 시점만 종전 값이 나간다."""
    fin, finq = _fins(FIN, {2023: "CFS"}, {**FINQ}, {(2023, 1): "CFS", (2024, 1): "OFS"})
    finq.gate((2023, 1), ("20300101", (9.0, 500.0)))
    view = finq.at("20240628")
    assert view is not finq and view[(2023, 1)] == (9.0, 500.0)
    assert S._ttm_ni(fin, view, 2024, 1) is None


# ═══ 순수 — 재무 요약 파생 (`scripts/market_fund_quarterly.py`) ═══════════════════════════════════════
def _m():
    return {(2022, 4): (50.0, 500.0), (2023, 4): (60.0, 560.0), (2023, 1): (10.0, 520.0), (2023, 2): (25.0, 540.0), (2023, 3): (40.0, 550.0),
            (2024, 1): (30.0, 600.0), (2024, 2): (55.0, 620.0)}


def test_derive_ttm_from_cells_same_basis_and_mixed():
    mq = _mq()
    all_cfs = {k: "CFS" for k in _m()}
    assert mq.ttm_from_cells(_m(), all_cfs, 12, 2024, 2) == (60.0 + 55.0 - 25.0, False)
    mixed = {**all_cfs, (2024, 2): "OFS"}
    assert mq.ttm_from_cells(_m(), mixed, 12, 2024, 2) == (None, True)
    assert mq.ttm_from_cells(_m(), {}, 12, 2024, 2) == (60.0 + 55.0 - 25.0, False), "기준을 모르면 종전 값"
    assert mq.ttm_from_cells(_m(), mixed, 12, 2023, 4) == (60.0, False), "사업보고서 칸은 한 칸"
    assert mq.ttm_from_cells({(2024, 2): (55.0, 1.0)}, all_cfs, 12, 2024, 2) == (None, False), "값이 없어 빈 것은 섞임이 아니다"


def test_derive_picks_the_latest_cell_whose_ttm_is_clean():
    """최신 칸이 섞여 비면 앞선 칸(네 칸 안)의 TTM — 최신 분기보고서를 아직 안 낸 회사가 앞선 칸을 쓰는 것과 같은 규칙. 앞선 칸도 없으면 비움."""
    mq = _mq()
    m = _m()
    present = [k for k, (ni, eq) in m.items() if ni is not None]
    latest = lambda keys: max(keys, key=lambda k: S._rc.cell_end(k[0], k[1], 12))      # noqa: E731
    cfs = {k: "CFS" for k in m}
    assert mq.pick_ttm(m, cfs, 12, present, latest) == (60.0 + 55.0 - 25.0, (2024, 2))
    mixed = {**cfs, (2024, 2): "OFS"}                                                    # 2024 반기만 별도
    v, cell = mq.pick_ttm(m, mixed, 12, present, latest)
    assert (v, cell) == (60.0 + 30.0 - 10.0, (2024, 1)), "한 칸 앞(2024 1분기)의 TTM"
    both = {**mixed, (2024, 1): "OFS", (2023, 1): "CFS"}                                 # 1분기도 섞임 → 그 앞 사업보고서 칸(2023 연간 = 한 칸)
    assert mq.pick_ttm(m, both, 12, present, latest) == (60.0, (2023, 4))
    only = {(2024, 2): (55.0, 1.0), (2023, 2): (25.0, 1.0), (2023, 4): (60.0, 1.0)}
    assert mq.pick_ttm(only, {(2024, 2): "OFS", (2023, 2): "CFS", (2023, 4): "CFS"}, 12, list(only), latest) == (60.0, (2023, 4))



# ═══ Postgres — 실제 SQL ═══════════════════════════════════════════════════════════════════════════
psycopg = pytest.importorskip("psycopg")

TODAY = date(2026, 10, 20)
CAP = 1000 * E8
DATES = ["20230630", "20231229", "20240628", "20240830", "20250103"]
FINSTAT_Y_DDL = """CREATE TABLE dart_finstat_y(
  ticker text, fy int, fs text, ni double precision, eq double precision, fetched text,
  ni_restated double precision, eq_restated double precision, restated_rcept_no text, PRIMARY KEY(ticker, fy))"""
FINSTAT_Q_DDL = """CREATE TABLE dart_finstat_q(
  ticker text, fy int, quarter int, reprt_code text, fs text,
  ni_cum double precision, eq double precision, fetched text, ni_case text, eq_case text,
  ni_cum_raw double precision, eq_raw double precision,
  ni_cum_restated double precision, eq_restated double precision, restate_why text,
  PRIMARY KEY(ticker, fy, quarter))"""

# (ticker, fy, fs, ni, eq, fetched)
Y_ROWS = [
    ("C", 2022, "CFS", 50 * E8, 500 * E8, "ok"), ("C", 2023, "CFS", 60 * E8, 560 * E8, "ok"),
    # M: 2023 연간은 별도(종속회사를 처분해 연결을 못 냄) — 2022 연간·2023 1분기는 연결
    ("M", 2022, "CFS", 40 * E8, 400 * E8, "ok"), ("M", 2023, "OFS", 20 * E8, 300 * E8, "ok"),
    # F: 값 없는 칸(nodata)의 fs 는 마지막으로 시도한 기준(별도) — 보충값뿐이라 기준을 모른다
    ("F", 2022, "CFS", 30 * E8, 300 * E8, "ok"), ("F", 2023, "CFS", 36 * E8, 330 * E8, "ok"),
]
# (ticker, fy, quarter, fs, ni_cum, eq, ni_cum_restated, fetched)
Q_ROWS = [
    ("C", 2023, 1, "CFS", 10 * E8, 520 * E8, None, "ok"), ("C", 2024, 1, "CFS", 30 * E8, 600 * E8, None, "ok"),
    ("M", 2022, 1, "CFS", 7 * E8, 380 * E8, None, "ok"), ("M", 2023, 1, "CFS", 8 * E8, 390 * E8, None, "ok"),
    ("M", 2024, 1, "OFS", 6 * E8, 310 * E8, None, "ok"),
    ("F", 2023, 1, "OFS", None, None, 6 * E8, "nodata"), ("F", 2024, 1, "CFS", 12 * E8, 350 * E8, None, "ok"),
]
# G: 연간 2023 은 원본이 없어 다음 해 보고서가 채운 정정 칸(nodata — 마지막으로 시도한 기준 OFS 가 적혀 있을 뿐) · 분기 칸은 연결
Y_FILL = [("G", 2022, "CFS", 30 * E8, 300 * E8, "ok", None, None), ("G", 2023, "OFS", None, None, "nodata", 36 * E8, 330 * E8)]
Q_G = [("G", 2023, 1, "CFS", 6 * E8, 320 * E8, None, "ok"), ("G", 2024, 1, "CFS", 12 * E8, 350 * E8, None, "ok")]
MKT = {"C": "KS", "M": "KS", "F": "KS", "G": "KS"}
SECTOR = {"C": "G10", "M": "G20", "F": "G30", "G": "G40"}


def _exec(url):
    def run(sql, params=()):
        with psycopg.connect(url) as c:
            return c.execute(sql, params).fetchall()
    return run


@pytest.fixture
def mdb(pg17, monkeypatch):
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(FINSTAT_Y_DDL)
        c.execute(FINSTAT_Q_DDL)
        c.cursor().executemany("INSERT INTO dart_finstat_y (ticker, fy, fs, ni, eq, fetched) VALUES (%s,%s,%s,%s,%s,%s)", Y_ROWS)
        c.cursor().executemany("INSERT INTO dart_finstat_y (ticker, fy, fs, ni, eq, fetched, ni_restated, eq_restated) "
                               "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", Y_FILL)
        c.cursor().executemany("INSERT INTO dart_finstat_q (ticker, fy, quarter, fs, ni_cum, eq, ni_cum_restated, fetched) "
                               "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", Q_ROWS + Q_G)
        c.execute("CREATE TABLE dart_fundamentals (ticker text PRIMARY KEY, corp_code text, market text, fs text, ni_fy double precision, "
                  "ni_ttm double precision, eq_fy double precision, eq_mrq double precision, fetched text, induty text, currency text, "
                  "acc_mt smallint)")
        c.cursor().executemany("INSERT INTO dart_fundamentals (ticker, market, induty, fetched) VALUES (%s,%s,'26410','ok')", list(MKT.items()))
        c.execute("CREATE TABLE krx_weekly (price_dd text, ticker text, market text, close bigint, mktcap double precision, list_shrs bigint, "
                  "PRIMARY KEY (price_dd, ticker))")
        c.execute("CREATE TABLE krx_day (dd text, n integer, wk_close boolean NOT NULL DEFAULT false)")
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


def test_server_reads_each_cells_basis_but_only_when_the_original_value_exists(mdb):
    fin, finq = _fin("M")
    assert fin.fs == {2022: "CFS", 2023: "OFS"} and finq.fs == {(2022, 1): "CFS", (2023, 1): "CFS", (2024, 1): "OFS"}
    _, fq_f = _fin("F")
    assert (2023, 1) not in fq_f.fs, "원본 순이익이 없는 칸(nodata — 보충값뿐)의 fs 는 마지막으로 시도한 기준이라 믿지 않는다"
    assert fq_f.fs == {(2024, 1): "CFS"}
    fin_g, finq_g = _fin("G")
    assert fin_g.fs == {2022: "CFS"} and fin_g[2023][0] == 36 * E8, "연간도 같다 — 원본이 없는 해(정정 칸 보충값)의 fs 는 마지막 시도 기준일 뿐"
    assert S._ttm_ni(fin_g, finq_g, 2024, 1) == (36 + 12 - 6) * E8


def test_curve_blanks_the_mixed_point_and_says_why(mdb):
    fin, finq = _fin("M")
    pt = S._pit_point("20240628", CAP, fin, finq)                      # 2024 1분기 칸 — 연간 OFS · 당해 OFS · 전년 CFS
    assert pt["per_ttm"] is None and pt["per_ttm_why"] == "mixed_fs"
    assert pt["pbr_mrq"] is not None and pt["per_fy0"] is not None, "다른 배수는 그대로"
    c_fin, c_q = _fin("C")
    ok = S._pit_point("20240628", CAP, c_fin, c_q)
    assert ok["per_ttm"] == round(CAP / (80 * E8), 3) and "per_ttm_why" not in ok, "섞이지 않은 종목은 응답이 종전 그대로(새 칸 없음)"
    f_fin, f_q = _fin("F")
    unk = S._pit_point("20240628", CAP, f_fin, f_q)
    assert unk["per_ttm"] == round(CAP / (36 * E8 + 12 * E8 - 6 * E8), 3), "보충값 칸은 기준을 몰라 종전처럼 쓴다"


def test_company_rows_fall_back_to_the_latest_clean_cell_and_label_it(mdb):
    """업종 비교 기업 행·「주간」 항목(`_row_val`)은 앞선 칸 가운데 세 칸이 같은 기준인 가장 늦은 칸으로 센다 — 값을 비우지 않는다."""
    fin, finq = _fin("M")
    rv = S._row_val("20240628", CAP, fin, finq)
    assert rv["per_ttm"] == pytest.approx(CAP / (20 * E8)), "2023 사업보고서 칸(연간 TTM = 별도 20억) — 연결 40+별도 6−연결 8 같은 섞인 값(18억)이 아니다"
    assert rv["basis"] == {"pit_q": "2024Q1", "per_ttm": "2023Q4", "per_ttm_why": "mixed_fs"}
    assert S._row_val("20240628", CAP, *_fin("C"))["basis"] is None


def test_derive_uses_the_latest_clean_ttm(mdb, capsys):
    from scripts import market_fund_quarterly as Q
    with psycopg.connect(mdb, autocommit=True) as c:
        c.execute("INSERT INTO dart_finstat_q (ticker, fy, quarter, reprt_code, fs, ni_cum, eq, fetched) "
                  "SELECT ticker, fy, 4, '11011', fs, ni, eq, 'ok' FROM dart_finstat_y")
    Q.derive_fundamentals()
    got = {r[0]: r[1] for r in _exec(mdb)("SELECT ticker, ni_ttm FROM dart_fundamentals")}
    assert got["C"] == 80 * E8
    assert got["M"] == 20 * E8, "최신 칸(2024 1분기)이 섞여 비면 앞선 칸(2023 사업보고서)의 TTM — 18억 같은 섞인 값이 아니다"
    assert got["F"] == 36 * E8 + 12 * E8 - 6 * E8


def test_both_backfills_leave_the_mixed_ticker_out_of_the_ttm_sum(mdb, monkeypatch):
    from scripts import market_val_history_backfill as H
    from scripts import sector_val_backfill as SB
    # G 의 2023 사업보고서 칸(Q4 seed) — 연간 표에 원본이 없는 해인데 정정 칸 보충값을 옮겨 값이 차 있고, fs 는 연간 표의 마지막 시도 기준(OFS)을 옮긴 것.
    #   서버(`_FIN_FY_SQL`)는 이 해의 기준을 모른다 — 백필도 이 칸의 fs 를 믿지 않아야 서버 곡선과 같은 값이다(G40 아래 단언).
    _exec(mdb)("INSERT INTO dart_finstat_q (ticker, fy, quarter, fs, ni_cum, eq, fetched) VALUES ('G', 2023, 4, 'OFS', %s, %s, 'ok') RETURNING ticker",
               (36 * E8, 330 * E8))
    monkeypatch.setattr(sys, "argv", ["market_val_history_backfill.py", "--since", "20230101"])
    asyncio.run(H.main())
    monkeypatch.setattr(sys, "argv", ["sector_val_backfill.py", "--since", "20230101"])
    assert SB.main() == 0
    rows = {(r[0], r[1], r[2]): r[3] for r in _exec(mdb)("SELECT snap_dd, scheme, sector, per_ttm FROM opm_val_market WHERE snap_dd='20240628'")}
    # 업종분류(wics_sector) — M 은 TTM 이 비어 G20 의 PER(TTM) 이 없다(종전: 18억으로 센 값). C·F 는 그대로.
    assert rows[("20240628", "wics_sector", "G20")] is None
    assert rows[("20240628", "wics_sector", "G10")] == pytest.approx(CAP / (80 * E8))
    assert rows[("20240628", "wics_sector", "G30")] == pytest.approx(CAP / (42 * E8))
    assert rows[("20240628", "wics_sector", "G40")] == pytest.approx(CAP / (42 * E8)), "G: 보충값 해의 기준을 몰라 종전처럼 센다"
    # 시장 전체(코스피): C(80억)·F(42억)·G(42억)만 분모에 든다 — M 의 시총도 분자에서 빠진다(종목별로 분자·분모를 같이 센다)
    assert rows[("20240628", "market", "_ALL")] == pytest.approx(3 * CAP / ((80 + 42 + 42) * E8))
