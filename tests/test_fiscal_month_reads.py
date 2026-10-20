"""결산월을 아는 읽기·파생·수집(261018 재설계 6단계) — 12월 결산이 아닌 회사의 분기·연간 칸을 실제 기간 말로.

배경: DART 는 분기·연간 재무 칸의 사업연도를 보고서 기준일의 달력 연도로 붙인다. 읽는 쪽이 그 칸을 달력 분기로 읽어, 12월 결산이 아닌
회사(10-18 운영 32사)는 아직 안 나온 보고서를 앞당겨 쓰고(→ 칸이 비어 업종 비교 N/M), TTM 의 직전 연간 값이 한 해 어긋났다.
연간 수집도 12월 결산 규칙이라 3월 결산 회사의 2026.03 사업보고서(6월 제출)가 2027년 4월에야 들어왔다.

고정하는 것
  A. 서버 — 곡선 한 점(`_pit_raw`)·업종 비교 기업 행(`_firm_val_at`)·firm_history·과거 시점이 결산월로 칸을 고른다. 12월 결산(결산월 칸이 비었을 때 포함)은
     종전과 같다(종전 응답 기준 파일 `pmd_read_path_golden.json` 이 그대로 통과한다 — `test_pmd_read_path.py`).
  B. 파생(`--derive`) — 「최신 분기」를 실제 기간 말로, TTM 직전 연간을 결산월로. 12월 결산·결산월 빈 칸은 종전 값.
  C. 수집 — 분기 마감·nodata 재확인·연간 수집 상한이 결산월로. 결산월 정하기(사업보고서 기준월, 바뀐 회사·리츠는 12 = 종전 읽기).
  D. Q4 seed 는 바뀐 행만 쓰고, 재무 요약에서 새 FY 칸을 만들던 bootstrap 은 없다. 결산월 칸 DDL 은 멱등이다.
DART 0콜(가짜 클라이언트). DB 가 필요한 것은 Postgres 17(`tests/conftest.py` 의 `pg17` — Docker 없으면 건너뜀).
"""
from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import sys
from datetime import date

import pytest

from open_proxy_mcp.services import price_multiple_data as S
from tests.pmd_fin_rows import pad_fin_rows
from scripts import market_fund_quarterly as Q

ROOT = pathlib.Path(__file__).resolve().parent.parent
TODAY = date(2026, 10, 18)
CAP = 1.0e12

# 3월 결산 한 종목(동원모빌리티 꼴): 연간 = 2025.03·2026.03, 분기 = 2025.06·09·12, 2026.06. 반기(2026.09)는 11/14 마감이라 아직 없다.
FIN_MAR = {2024: (150.0e8, 1000.0e8), 2025: (210.0e8, 1300.0e8), 2026: (260.0e8, 1900.0e8)}
FINQ_MAR = {(2024, 1): (40.0e8, 1050.0e8), (2024, 2): (80.0e8, 1100.0e8), (2024, 3): (110.0e8, 1200.0e8),
            (2025, 1): (55.0e8, 1290.0e8), (2025, 2): (150.0e8, 1450.0e8), (2025, 3): (260.0e8, 1590.0e8),
            (2026, 1): (140.0e8, 1950.0e8), (2026, 2): (None, None)}


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "scripts"))
    spec.loader.exec_module(mod)
    return mod


# ═══ A. 서버 ═══════════════════════════════════════════════════════════════════════════════════════
def test_march_point_uses_the_june_quarter_and_the_march_annual():
    p = S._pit_raw("20261016", CAP, FIN_MAR, FINQ_MAR, 3)
    assert (p["pit_fy"], p["pit_q"]) == (2026, "2026Q1")
    assert p["per_ttm"] == CAP / (260.0e8 + 140.0e8 - 55.0e8)          # 2026.03 연간 + 2026.06 누적 − 2025.06 누적
    assert p["pbr_mrq"] == CAP / 1950.0e8                               # 2026.06 말 자본
    assert p["per_fy0"] == CAP / 260.0e8 and p["pbr"] == CAP / 1900.0e8
    # 종전(달력) 읽기: 「2026 반기」 칸(실제로는 2026.09 말 — 아직 없음)을 골라 TTM·MRQ 가 비었다(업종 비교 N/M 의 원인)
    old = S._pit_raw("20261016", CAP, FIN_MAR, FINQ_MAR)
    assert old["pit_q"] == "2026Q2" and old["per_ttm"] is None and old["pbr_mrq"] is None
    assert old["pit_fy"] == 2025                                        # FY0 도 한 해 낡았다(2025.03)


def test_march_point_does_not_look_ahead():
    """5/15 의 3월 결산 — 종전엔 「2026 1분기」(2026.06 말 · 8/14 마감)를 3개월 앞당겨 썼다. 이제 2025.12 말 3분기."""
    p = S._pit_raw("20260515", CAP, FIN_MAR, FINQ_MAR, 3)
    assert p["pit_q"] == "2025Q3" and p["pit_fy"] == 2025
    assert p["per_ttm"] == CAP / (210.0e8 + 260.0e8 - 110.0e8)          # 2025.03 연간 + 2025.12 누적 − 2024.12 누적
    assert S._pit_raw("20260515", CAP, FIN_MAR, FINQ_MAR)["pit_q"] == "2026Q1"


def test_impaired_check_uses_the_same_cell():
    finq = dict(FINQ_MAR)
    finq[(2026, 1)] = (1.0e8, -5.0)
    assert S._pit_impaired("20261016", FIN_MAR, finq, 3)
    assert not S._pit_impaired("20261016", FIN_MAR, finq)                 # 달력 읽기는 빈 칸(2026 반기)을 보고 연간 자본으로 넘어갔다
    assert S._pit_raw("20261016", CAP, FIN_MAR, finq, 3)["per_ttm"] is None


class _Rows:
    """`_pg_rows` 대역 — SQL 모양으로 가른다."""

    def __init__(self, acc_mt, fund=(210.0e8, 1300.0e8)):
        self.acc_mt, self.fund, self.log = acc_mt, fund, []

    def __call__(self, sql, params=()):
        s = " ".join(sql.split())
        self.log.append(s)
        if s.startswith("SELECT market, induty, currency, fetched, acc_mt FROM dart_fundamentals"):
            return [("KS", "30320", "KRW", "ok", self.acc_mt)]
        if s.startswith("SELECT market, induty, currency, acc_mt, fetched FROM dart_fundamentals"):
            return [("KS", "30320", "KRW", self.acc_mt, "ok")]
        if s == "SELECT ni_fy, eq_fy FROM dart_fundamentals WHERE ticker=%s":
            return [self.fund]
        if "FROM dart_finstat_y" in s:
            return pad_fin_rows(sql, [(fy, ni, eq, None, None) for fy, (ni, eq) in FIN_MAR.items()])
        if "FROM dart_finstat_q" in s:
            return pad_fin_rows(sql, [(fy, q, ni, eq) for (fy, q), (ni, eq) in FINQ_MAR.items()])
        if "UNION ALL" in s and "FROM krx_weekly" in s and "AS src" in s:     # 업종 비교의 그 날 시총(`_FIRM_CAP_SQL`)
            return [(CAP, "KS", 1)]
        if "DISTINCT ON (substring(price_dd,1,4))" in s:
            return [("2025", "20251230", CAP)]
        if s.startswith("SELECT MAX(price_dd) FROM krx_weekly"):
            return [("20261016",)]
        if "SELECT price_dd, mktcap, 'w' FROM krx_weekly WHERE ticker=%s AND price_dd>=%s" in s:
            return [("20260515", CAP, "w"), ("20261016", CAP, "w")]
        if s.startswith("SELECT 'below'"):
            return []
        if "FROM krx_stock_flags" in s:
            return []
        raise AssertionError("모르는 SQL: " + s)


def _patch(mp, rows):
    async def resolve(q):
        return {"corp_name": "동원모빌리티", "stock_code": "018500", "corp_code": "00118008"}, None
    mp.setattr(S, "_pg_rows", rows)
    mp.setattr(S, "_resolve_listed", resolve)
    mp.setattr(S, "today_kst", lambda: TODAY)


def test_sector_compare_firm_row_reads_the_fiscal_month(monkeypatch):
    _patch(monkeypatch, _Rows(3))
    sec, mkt, per_ttm, pbr_mrq, note = asyncio.run(S._firm_val_at("018500", "20261016"))
    assert per_ttm == CAP / (260.0e8 + 140.0e8 - 55.0e8) and pbr_mrq == CAP / 1950.0e8 and note == {}
    _patch(monkeypatch, _Rows(None))                                     # 결산월 칸이 비면 종전 읽기(12월)
    # 종전 읽기의 그 시점 칸(「2026 반기」)은 비었다 → 앞선 칸(「2026 1분기」)으로 세고 표시한다(감사 4 — 종전 스냅샷 표도 그 칸 값을 냈다)
    *_, per12, pbr12, note12 = asyncio.run(S._firm_val_at("018500", "20261016"))
    assert (per12, pbr12) == (CAP / (210.0e8 + 140.0e8 - 55.0e8), CAP / 1950.0e8)
    assert note12 == {"basis": {"pit_q": "2026Q2", "per_ttm": "2026Q1", "pbr_mrq": "2026Q1"}}


def test_non_december_does_not_lay_the_summary_annual_on_the_calendar_year(monkeypatch):
    """재무 요약 연간 값은 12월 결산 달력 규칙의 해(`_latest_annual_fy()` = 2025)에 얹힌다 — 3월 결산이면 그 값이 다른 해의 사업보고서일 수 있어 얹지 않는다."""
    _patch(monkeypatch, _Rows(3, fund=(999.0, 999.0)))
    assert asyncio.run(S._firm_fin_by_fy("018500", "KRW", 3))[2025] == FIN_MAR[2025]
    assert asyncio.run(S._firm_fin_by_fy("018500", "KRW"))[2025] == (999.0, 999.0)          # 12월 결산은 종전 그대로


def test_firm_history_and_as_of_say_how_to_read_the_cells(monkeypatch):
    _patch(monkeypatch, _Rows(3))
    async def bounds():
        return "20261016", "20231016"
    monkeypatch.setattr(S, "_weekly_bounds", bounds)
    p = asyncio.run(S.build_firm_history_payload("018500", format="json"))
    assert [pt["pit_q"] for pt in p["data"]["series"]] == ["2025Q3", "2026Q1"]
    assert p["data"]["history"][0]["source"] == "연말(PIT FY2025)"       # 2025.12 연말 — 2025.03 사업보고서(종전 달력 규칙은 2024)
    note = [w for w in p["warnings"] if w.startswith("결산월 3월")]
    assert len(note) == 1 and "1분기 = 그해 6월 말" in note[0] and "사업보고서 = 그해 3월 말" in note[0]
    at = asyncio.run(S.build_firm_at_payload("018500", "20261016"))
    assert at["data"]["pit_q"] == "2026Q1" and at["warnings"][-1] == note[0]
    _patch(monkeypatch, _Rows(12))
    p12 = asyncio.run(S.build_firm_history_payload("018500", format="json"))
    assert not any(w.startswith("결산월") for w in p12["warnings"])       # 12월 결산은 종전 응답 그대로(안내 없음)


# ═══ C. 수집 규칙(DB 없이) ═════════════════════════════════════════════════════════════════════════
def test_quarterly_deadline_follows_the_fiscal_month():
    assert Q._disclosed(2026, 3, today=date(2026, 5, 15), fye=6)          # 6월 결산 3분기 = 3월 말 → 5/15
    assert not Q._disclosed(2026, 3, today=date(2026, 5, 15))            # 12월 결산 3분기 = 9월 말 → 11/14(종전)
    assert not Q._disclosed(2026, 2, today=TODAY, fye=3)                 # 3월 결산 반기 = 9월 말 → 11/14
    assert Q._disclosed(2026, 2, today=TODAY)                            # 12월 결산 반기(종전)
    assert Q._retry_nodata(2026, 3, today=TODAY, fye=6) and not Q._retry_nodata(2026, 3, today=TODAY)
    done, retry = Q._resume_sets([("M", 2026, 2, "nodata"), ("D", 2026, 2, "nodata")], today=TODAY, fye_of={"M": 3})
    assert retry == {("D", 2026, 2)} and ("M", 2026, 2) in done          # 3월 결산 반기는 아직 마감 전 — 다시 받지도, 지금 받지도 않는다


def test_annual_fetch_plan_reaches_the_fiscal_months_latest_annual():
    V = _load("market_val_series")
    firms = [("D", "c1"), ("M", "c2"), ("J", "c3"), ("S", "c4")]
    have = {(t, y): "ok" for t, _ in firms for y in range(2018, 2026)}
    have[("D", 2025)] = "nodata"                       # 12월 결산 — 공시 시점(2026-04-01) 뒤 200일 → 다시 받는다
    have[("D", 2024)] = "nodata"                       # 565일 → 그대로
    have[("S", 2024)] = "err:[020]"                    # 오류는 다시
    fye = {"M": 3, "J": 6, "S": 9}
    plan = V.fetch_plan(firms, have, fye, today=TODAY)
    assert sorted((t, y) for t, _, y in plan) == [("D", 2025), ("J", 2026), ("M", 2026), ("S", 2024)]
    # 종전 상한(12월 결산 규칙)이었다면 3·6월 결산의 2026 사업보고서는 2027-04 이후에야 대상이 됐다
    assert V._latest_annual_fy(TODAY) == 2025


@pytest.mark.parametrize("stored, months, comp, want", [
    (None, {3}, None, 3),                 # 처음 — 사업보고서 기준월 하나
    (3, {3}, None, 3),
    (12, {12}, None, 12),
    (None, {6, 12}, None, 12),            # 반기 결산 리츠·인프라 펀드 — 결산월 하나로 못 읽는다
    (6, {12}, None, 12),                  # 결산월을 바꿈(6 → 12) — 바뀐 해의 칸이 겹친다 → 종전 읽기
    (12, {6}, None, 12),                  # 한 번 12 가 되면 그대로
    (None, set(), "03", 3),               # 상장 첫해 — 사업보고서가 아직 없어 회사 개황
    (None, set(), None, None),
    (9, set(), "12", 9),                  # 창 안 사업보고서가 없으면 저장값 그대로(개황은 보지 않는다)
])
def test_decide_fiscal_month(stored, months, comp, want):
    assert Q.decide_acc_mt(stored, months, comp) == want


# ═══ B·C·D. Postgres 17 ═══════════════════════════════════════════════════════════════════════════
psycopg = pytest.importorskip("psycopg")

FINSTAT_Q_DDL = """CREATE TABLE dart_finstat_q(
  ticker text, fy int, quarter int, reprt_code text, fs text,
  ni_cum double precision, eq double precision, fetched text, ni_case text, eq_case text,
  ni_cum_raw double precision, eq_raw double precision,
  ni_cum_restated double precision, eq_restated double precision, restate_why text,
  PRIMARY KEY(ticker, fy, quarter))"""
FINSTAT_Y_DDL = """CREATE TABLE dart_finstat_y(
  ticker text, fy int, fs text, ni double precision, eq double precision, fetched text,
  ni_restated double precision, eq_restated double precision, PRIMARY KEY(ticker, fy))"""


def _q(url, sql, params=()):
    with psycopg.connect(url) as c:
        return c.execute(sql, params).fetchall()


@pytest.fixture
def fdb(pg17, monkeypatch):
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(FINSTAT_Q_DDL)
        c.execute(FINSTAT_Y_DDL)
        c.execute(Q.DDL_FUNDAMENTALS)
    monkeypatch.setenv("DATABASE_URL", pg17)
    return pg17


def _seed_cells(c, t, cells):
    for (fy, q), (ni, eq) in cells.items():
        c.execute("INSERT INTO dart_finstat_q (ticker, fy, quarter, reprt_code, fs, ni_cum, eq, fetched) "
                  "VALUES (%s,%s,%s,%s,'CFS',%s,%s,'ok')", (t, fy, q, Q.Q_TO_RC[q], ni, eq))


def test_derive_reads_cells_by_real_period_end(fdb, capsys):
    """M(3월 결산): 사업보고서 2026.03 이 1분기(2026.06)보다 표기 순서상 뒤지만 기간 말은 앞이다. D(12월)·N(결산월 칸 빔)은 종전 그대로."""
    cells_m = {(2025, 4): (210.0, 1300.0), (2025, 1): (55.0, 1290.0), (2025, 2): (150.0, 1450.0), (2025, 3): (260.0, 1590.0),
               (2026, 4): (260.0, 1900.0), (2026, 1): (140.0, 1950.0)}
    cells_d = {(2024, 4): (100.0, 1000.0), (2025, 4): (120.0, 1100.0), (2025, 2): (50.0, 1050.0),
               (2026, 1): (30.0, 1150.0), (2026, 2): (70.0, 1200.0)}
    with psycopg.connect(fdb, autocommit=True) as c:
        for t, m in (("M", 3), ("D", 12), ("N", None)):
            c.execute("INSERT INTO dart_fundamentals (ticker, corp_code, fetched, acc_mt) VALUES (%s, %s, 'ok', %s)", (t, "c" + t, m))
        _seed_cells(c, "M", cells_m)
        _seed_cells(c, "D", cells_d)
        _seed_cells(c, "N", cells_m)                                     # 결산월을 모르면 3월 결산 자료도 종전처럼 읽는다
    Q.derive_fundamentals()
    got = {r[0]: r[1:] for r in _q(fdb, "SELECT ticker, ni_fy, ni_ttm, eq_fy, eq_mrq FROM dart_fundamentals")}
    assert got["M"] == (260.0, 260.0 + 140.0 - 55.0, 1900.0, 1950.0)     # TTM 직전 연간 = 2026.03 · MRQ = 2026.06 말
    assert got["D"] == (120.0, 120.0 + 70.0 - 50.0, 1100.0, 1200.0)      # 종전과 같다
    assert got["N"] == (260.0, 260.0, 1900.0, 1900.0)                    # 종전 읽기: (2026,4) 를 마지막 칸으로 — 결산월을 채우면 M 처럼
    assert "값이 바뀐 3사" in capsys.readouterr().out


def test_derive_without_the_column_reads_everything_as_december(pg17, monkeypatch, capsys):
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(FINSTAT_Q_DDL)
        c.execute("CREATE TABLE dart_fundamentals(ticker text PRIMARY KEY, corp_code text, market text, fs text, ni_fy double precision, "
                  "ni_ttm double precision, eq_fy double precision, eq_mrq double precision, fetched text)")
        c.execute("INSERT INTO dart_fundamentals (ticker, fetched) VALUES ('D', 'ok')")
        _seed_cells(c, "D", {(2025, 4): (120.0, 1100.0), (2025, 2): (50.0, 1050.0), (2026, 2): (70.0, 1200.0)})
    monkeypatch.setenv("DATABASE_URL", pg17)
    Q.derive_fundamentals()
    assert _q(pg17, "SELECT ni_fy, ni_ttm, eq_fy, eq_mrq FROM dart_fundamentals") == [(120.0, 140.0, 1100.0, 1200.0)]
    assert "결산월 칸(dart_fundamentals.acc_mt)이 없다" in capsys.readouterr().out


def test_seed_writes_only_changed_rows_and_never_bootstraps_from_the_summary(fdb, capsys):
    with psycopg.connect(fdb, autocommit=True) as c:
        c.execute("INSERT INTO dart_fundamentals (ticker, fetched, ni_fy, eq_fy) VALUES ('A', 'ok', 111, 222)")
        c.execute("INSERT INTO dart_finstat_y (ticker, fy, fs, ni, eq, fetched) VALUES ('A', 2024, 'CFS', 10, 100, 'ok')")
    Q.seed_q4()
    assert _q(fdb, "SELECT fy, ni_cum, eq FROM dart_finstat_q WHERE quarter=4") == [(2024, 10.0, 100.0)]   # 2025 bootstrap(111·222) 없음
    x1 = _q(fdb, "SELECT xmin::text FROM dart_finstat_q")
    Q.seed_q4()
    assert _q(fdb, "SELECT xmin::text FROM dart_finstat_q") == x1                                     # 같은 값 — 다시 쓰지 않는다
    with psycopg.connect(fdb, autocommit=True) as c:
        c.execute("UPDATE dart_finstat_y SET ni_restated = 12 WHERE ticker='A'")
    Q.seed_q4()
    assert _q(fdb, "SELECT ni_cum FROM dart_finstat_q") == [(12.0,)]
    # 연간 표의 빈 칸은 Q4 칸 값을 지우지 않는다(운영 466100·471820·082270 꼴) — 둘 다 값이면 연간 표를 따른다
    with psycopg.connect(fdb, autocommit=True) as c:
        c.execute("UPDATE dart_finstat_y SET ni = NULL, ni_restated = NULL, eq = 101 WHERE ticker='A'")
    Q.seed_q4()
    assert _q(fdb, "SELECT ni_cum, eq, fetched FROM dart_finstat_q") == [(12.0, 101.0, "ok")]
    assert capsys.readouterr().out.count("Q4 seed") == 4


def test_seed_does_not_wait_for_the_table_lock_when_the_columns_exist(fdb, monkeypatch):
    """261019: `ALTER TABLE … ADD COLUMN IF NOT EXISTS` 는 칸이 있어도 표 전체 잠금을 기다린다 — 서버 읽기가 열린 동안 주간 일정의 seed 가
    그 뒤에 줄 서고, 그 seed 뒤에 다음 서버 읽기들이 줄 섰다. 표·칸이 있으면 DDL 을 보내지 않는다(없으면 종전처럼 만든다)."""
    with psycopg.connect(fdb, autocommit=True) as c:
        c.execute("INSERT INTO dart_finstat_y (ticker, fy, fs, ni, eq, fetched) VALUES ('A', 2024, 'CFS', 10, 100, 'ok')")
    monkeypatch.setenv("PGOPTIONS", "-c lock_timeout=1500")       # 잠금을 기다리면 1.5초 뒤 실패한다(종전 코드)
    with psycopg.connect(fdb) as reader:                           # 서버 읽기 — 트랜잭션이 열린 동안 AccessShareLock
        reader.execute("SELECT count(*) FROM dart_finstat_q").fetchone()
        Q.seed_q4()
        reader.rollback()
    assert _q(fdb, "SELECT fy, ni_cum, eq FROM dart_finstat_q WHERE quarter=4") == [(2024, 10.0, 100.0)]
    # 표가 없으면(새 DB) 종전처럼 만든다 — 칸 둘까지
    with psycopg.connect(fdb, autocommit=True) as c:
        c.execute("DROP TABLE dart_finstat_q")
    Q.seed_q4()
    cols = {r[0] for r in _q(fdb, "SELECT column_name FROM information_schema.columns WHERE table_name='dart_finstat_q'")}
    assert {"ni_case", "eq_case"} <= cols and _q(fdb, "SELECT count(*) FROM dart_finstat_q") == [(1,)]


class _ListClient:
    """정기공시 목록·회사 개황 대역. 목록은 창마다 같은 내용을 한 쪽으로 준다(코스피·코스닥 각각)."""

    def __init__(self, reports, company):
        self.reports, self.company, self.calls = reports, company, []

    async def search_filings(self, bgn_de, end_de, pblntf_ty="", corp_cls="", page_no=1, page_count=100, **kw):
        self.calls.append(("list", corp_cls, bgn_de))
        rows = [r for r in self.reports if r["cls"] == corp_cls and bgn_de <= r["rcept_dt"] <= end_de]
        return {"status": "000", "list": rows, "total_page": 1}

    async def get_company_info(self, cc):
        self.calls.append(("company", cc))
        return {"acc_mt": self.company.get(cc, "")}


def test_refresh_fiscal_month_from_annual_report_names(fdb, monkeypatch, capsys):
    reports = [
        {"cls": "Y", "corp_code": "c1", "report_nm": "사업보고서 (2026.03)", "rcept_dt": "20260622"},
        {"cls": "Y", "corp_code": "c1", "report_nm": "분기보고서 (2026.06)", "rcept_dt": "20260814"},
        {"cls": "K", "corp_code": "c2", "report_nm": "[기재정정]사업보고서 (2025.12)", "rcept_dt": "20260320"},
        {"cls": "K", "corp_code": "c3", "report_nm": "사업보고서 (2025.12)", "rcept_dt": "20260310"},
        {"cls": "K", "corp_code": "c3", "report_nm": "사업보고서 (2026.06)", "rcept_dt": "20260910"},   # 반기 결산
        {"cls": "K", "corp_code": "c9", "report_nm": "사업보고서 (2026.03)", "rcept_dt": "20260601"},   # 우리 종목이 아님
    ]
    fake = _ListClient(reports, {"c4": "06"})
    monkeypatch.setattr("open_proxy_mcp.dart.client.get_dart_client", lambda: fake)
    monkeypatch.setattr(Q, "SLEEP", 0.0)
    with psycopg.connect(fdb, autocommit=True) as c:
        for t, cc, m in (("A", "c1", None), ("B", "c2", None), ("C", "c3", None), ("N", "c4", None), ("K", "c5", 9)):
            c.execute("INSERT INTO dart_fundamentals (ticker, corp_code, fetched, acc_mt) VALUES (%s, %s, 'ok', %s)", (t, cc, m))
    out = asyncio.run(Q.refresh_acc_mt(since=date(2026, 1, 1), today=TODAY))
    assert out == {"A": 3, "B": 12, "C": 12, "N": 6}
    assert dict(_q(fdb, "SELECT ticker, acc_mt FROM dart_fundamentals")) == {"A": 3, "B": 12, "C": 12, "N": 6, "K": 9}
    assert [x for x in fake.calls if x[0] == "company"] == [("company", "c4")]     # 사업보고서가 없고 저장값도 빈 종목만
    # 다시 돌리면 바뀐 것 없음 — 저장값과 같은 기준월
    assert asyncio.run(Q.refresh_acc_mt(since=date(2026, 1, 1), today=TODAY)) == {}
    # 결산월을 바꾼 사업보고서가 들어오면 12(종전 읽기)로
    reports.append({"cls": "Y", "corp_code": "c1", "report_nm": "사업보고서 (2026.12)", "rcept_dt": "20261001"})
    assert asyncio.run(Q.refresh_acc_mt(since=date(2026, 1, 1), today=TODAY)) == {"A": 12}
    assert "[결산월] A 3 → 12" in capsys.readouterr().out


def test_fundamentals_ddl_is_idempotent_and_checks_the_range(pg17, monkeypatch):
    ddl = _load("fundamentals_ddl")
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute("CREATE TABLE dart_fundamentals(ticker text PRIMARY KEY, fetched text)")
        c.execute("INSERT INTO dart_fundamentals VALUES ('A', 'ok')")
        assert ddl.check(c, out=lambda *_: None)[0] == "todo"
    assert ddl.apply(pg17, out=lambda *_: None) == (True, "칸·확인 제약 있음")
    assert ddl.apply(pg17, out=lambda *_: None) == (True, "칸·확인 제약 있음")         # 멱등
    with psycopg.connect(pg17, autocommit=True) as c:
        assert ddl.check(c, out=lambda *_: None)[0] == "done"
        c.execute("UPDATE dart_fundamentals SET acc_mt = 3")
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("UPDATE dart_fundamentals SET acc_mt = 13")
    # market_fund_quarterly 의 CREATE TABLE 과 같은 제약 이름 — 새 DB 에서 두 길이 겹쳐도 하나만 남는다
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute("DROP TABLE dart_fundamentals")
        c.execute(Q.DDL_FUNDAMENTALS)
        assert ddl.check(c, out=lambda *_: None)[0] == "done"


def test_fundamentals_ddl_print_mode_never_touches_the_database(monkeypatch, capsys):
    ddl = _load("fundamentals_ddl")
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("접속하면 안 된다")))
    monkeypatch.setattr(sys, "argv", ["fundamentals_ddl.py"])
    assert ddl.main() == 0
    out = capsys.readouterr().out
    assert "ADD COLUMN IF NOT EXISTS acc_mt smallint" in out and "CHECK (acc_mt BETWEEN 1 AND 12)" in out


# ═══ A'. 실시간 firm(현재 배수) — 12월 결산이 아닐 때만 결산월로 사업연도·분기 칸을 고른다 ═══════════════════════════
def _annual_rows(ni: str, eq: str) -> list[dict]:
    return [
        {"sj_div": "BS", "account_id": "ifrs-full_Assets", "thstrm_amount": "500,000,000,000", "currency": "KRW"},
        {"sj_div": "BS", "account_id": "ifrs-full_Liabilities", "thstrm_amount": "300,000,000,000"},
        {"sj_div": "BS", "account_id": "ifrs-full_Equity", "thstrm_amount": "200,000,000,000"},
        {"sj_div": "BS", "account_id": "ifrs-full_EquityAttributableToOwnersOfParent", "thstrm_amount": eq},
        # 261020: 지배 + 비지배 = 자본총계(2,000억)가 되게 비지배 줄을 둔다 — 지배자본 줄이 자본총계 − 비지배와 1.6배 넘게 어긋나면 그 줄을 믿지 않는다
        #   (`services/controlling_eq`). 이 응답 모양의 다른 칸(자산·부채·자본총계 — 단위 가드 항등식)은 그대로다.
        {"sj_div": "BS", "account_id": "ifrs-full_NoncontrollingInterests", "thstrm_amount": f"{200_000_000_000 - int(eq.replace(',', '')):,}"},
        {"sj_div": "IS", "account_id": "ifrs-full_ProfitLossAttributableToOwnersOfParent", "thstrm_amount": ni, "frmtrm_amount": ni},
    ]


def _cum_rows(ni_add: str, eq: str) -> list[dict]:
    return [{"sj_div": "BS", "account_id": "ifrs-full_EquityAttributableToOwnersOfParent", "thstrm_amount": eq},
            {"sj_div": "IS", "account_id": "ifrs-full_ProfitLossAttributableToOwnersOfParent",
             "thstrm_amount": "1", "thstrm_add_amount": ni_add}]


def _run_live(mp, *, company_acc_mt, db_acc_mt, reports, fm_years):
    """reports = {(사업연도, 보고서코드): 행}. fm_years = {요청 연도: (응답 연도, 그 요약의 보고서코드[, 출처 정기보고서 이름])}.
    261019(p1d): 첫 재무 요약은 12월 결산 달력 규칙의 연도(TODAY 10-19 → 2025)를 직접 준다 — 종전 키 0(기본 연도)은 이제 오지 않는다."""
    seen = {"fm": [], "acnt": [], "sql": []}

    async def resolve(q):
        return {"corp_name": "가나다", "stock_code": "018500", "corp_code": "00118008"}, None

    async def fin_metrics(code, **kw):
        y = kw.get("year") or 0
        seen["fm"].append(y)
        fy, rc, *nm = fm_years[y]
        out = {"data": {"summary": {"eps_krw": None, "revenue_krw": 600_000_000_000, "roe_pct": 10.0,
                                    "capital_impairment_status": None, "reprt_code": rc}, "year": fy}}
        if nm:
            out["data"]["source_report"] = {"report_nm": nm[0]}
        return out

    async def market_for(code):
        return {"price": 1300, "date": "20261016", "common_mktcap": 60_000_000_000, "list_shrs": 46_744_805}

    async def acntall(client, cc, year, rc, fs=None):
        seen["acnt"].append((year, rc))
        rows = reports.get((year, rc))
        return (rows, "CFS") if rows else ([], None)

    async def shares_out(client, cc, year):
        return {"total": 46_744_805, "common": 46_744_805}

    async def annual_summary(cc, fy):
        return {"cash_dps": 50}, {}

    class Client:
        async def get_company_info(self, cc):
            return {"induty_code": "30320", "acc_mt": company_acc_mt}

    def rows(sql, params=()):
        seen["sql"].append(" ".join(sql.split()))
        if "SELECT acc_mt FROM dart_fundamentals" in sql:
            return [(db_acc_mt,)]
        if "list_shrs" in sql:
            return [(46_744_805,)]
        return []
    mp.setattr(S, "_pg_rows", rows)
    mp.setattr(S, "today_kst", lambda: TODAY)
    mp.setattr(S, "_resolve_listed", resolve)
    mp.setattr(S, "get_dart_client", lambda: Client())
    mp.setattr(S, "build_financial_metrics_payload", fin_metrics)
    mp.setattr(S, "_market_for", market_for)
    mp.setattr(S, "_acntall", acntall)
    mp.setattr(S, "_shares_outstanding", shares_out)
    mp.setattr(S, "_annual_summary", annual_summary)
    return asyncio.run(S.build_valuation_payload("018500", format="json")), seen


MARCH_REPORTS = {
    (2025, "11011"): _annual_rows("21,164,000,000", "132,962,000,000"),     # 2025.03
    (2026, "11011"): _annual_rows("26,000,000,000", "190,000,000,000"),     # 2026.03 (6월 제출)
    (2026, "11013"): _cum_rows("13,920,000,000", "194,790,000,000"),        # 2026.06 1분기 누적
    (2025, "11013"): _cum_rows("5,470,000,000", "129,740,000,000"),         # 2025.06
    (2025, "11014"): _cum_rows("25,920,000,000", "158,530,000,000"),        # 2025.12 3분기 누적
    (2024, "11014"): _cum_rows("11,000,000,000", "122,270,000,000"),        # 2024.12
}


def test_live_firm_march_uses_the_latest_annual_and_the_quarter_after_it(monkeypatch):
    p, seen = _run_live(monkeypatch, company_acc_mt="03", db_acc_mt=3, reports=MARCH_REPORTS,
                        fm_years={2025: (2025, "11011"), 2026: (2026, "11011")})
    d = p["data"]
    assert seen["fm"] == [2025, 2026] and d["fiscal_year"] == 2026
    assert d["inputs"]["net_income_fy0_krw"] == 26_000_000_000
    assert d["inputs"]["net_income_ttm_krw"] == 26_000_000_000 + 13_920_000_000 - 5_470_000_000   # 2026.03 + 2026.06 − 2025.06
    assert (d["inputs"]["ttm_quarter"], d["inputs"]["ttm_quarter_year"]) == ("1분기 누적", 2026)
    assert d["inputs"]["controlling_equity_krw"] == 194_790_000_000
    assert (2026, "11014") in seen["acnt"] and (2027, "11014") not in seen["acnt"]      # 3월 결산의 3분기 칸 = 같은 해 표기(12월 말)
    assert any(w.startswith("결산월 3월") for w in p["data"]["warnings"])


def test_live_firm_march_late_filer_keeps_the_previous_annual(monkeypatch):
    """2026.03 사업보고서가 아직 없으면(늦게 냄) 종전 사업연도 그대로 — 그 뒤 분기는 결산월 표기(2025 3분기 = 2025.12)로 찾는다."""
    reports = {k: v for k, v in MARCH_REPORTS.items() if k != (2026, "11011")}
    p, seen = _run_live(monkeypatch, company_acc_mt="03", db_acc_mt=3, reports=reports,
                        fm_years={2025: (2025, "11011"), 2026: (2026, "11013")})
    d = p["data"]
    assert d["fiscal_year"] == 2025
    assert d["inputs"]["net_income_ttm_krw"] == 21_164_000_000 + 25_920_000_000 - 11_000_000_000
    assert (d["inputs"]["ttm_quarter"], d["inputs"]["ttm_quarter_year"]) == ("3분기 누적", 2025)


def test_live_firm_june_right_after_the_annual_uses_the_annual_as_ttm(monkeypatch):
    """6월 결산 10/1~11/13 — 최신 공시가 사업보고서(2026.06)라 TTM = 그 연간 값(곡선의 사업보고서 칸과 같다)."""
    reports = {(2026, "11011"): _annual_rows("-3,500,000,000", "188,571,000,000"),
               (2025, "11011"): _annual_rows("-4,435,000,000", "179,080,000,000"),
               (2026, "11014"): _cum_rows("-3,790,000,000", "188,370,000,000"),      # 2026.03 3분기(지난 회계연도)
               (2025, "11014"): _cum_rows("-29,540,000,000", "228,240,000,000")}
    p, seen = _run_live(monkeypatch, company_acc_mt="06", db_acc_mt=6, reports=reports,
                        fm_years={2025: (2025, "11011"), 2026: (2026, "11011")})
    d = p["data"]
    assert d["fiscal_year"] == 2026
    assert d["inputs"]["net_income_ttm_krw"] == d["inputs"]["net_income_fy0_krw"] == -3_500_000_000
    assert (d["inputs"]["ttm_quarter"], d["inputs"]["ttm_quarter_year"]) == ("사업보고서(연간)", 2026)
    assert {(2026, "11013"), (2026, "11012"), (2027, "11014")} <= set(seen["acnt"])     # 6월 결산의 다음 회계연도 칸


def test_live_firm_reads_the_db_month_only_when_a_non_december_sign_and_trusts_it(monkeypatch):
    """회사 개황이 6월이어도 재무 요약 표가 12(결산월을 바꾼 회사·반기 결산 리츠)면 종전 읽기. 개황이 12월이고 출처 사업보고서도 12월 기준
    (또는 이름이 없음)이면 결산월 읽기 자체가 없다 — 12월 결산은 읽기가 늘지 않는다."""
    p, seen = _run_live(monkeypatch, company_acc_mt="06", db_acc_mt=12, reports=MARCH_REPORTS,
                        fm_years={2025: (2025, "11011")})
    assert seen["fm"] == [2025] and p["data"]["fiscal_year"] == 2025
    assert (2026, "11014") in seen["acnt"]                                            # 종전: fy+1 표기
    assert not any(w.startswith("결산월") for w in p["data"]["warnings"])
    for nm in ([], ["사업보고서 (2025.12)"], ["[기재정정]사업보고서 (2025.12)"]):
        p, seen = _run_live(monkeypatch, company_acc_mt="12", db_acc_mt=3, reports=MARCH_REPORTS,
                            fm_years={2025: (2025, "11011", *nm)})
        assert not any("acc_mt" in s for s in seen["sql"]) and p["data"]["fiscal_year"] == 2025


def test_live_firm_december_overview_but_non_december_report_reads_the_db_month(monkeypatch):
    """261019: 개황은 12월(결산월을 12월로 바꿨다)인데 출처 사업보고서 이름이 3월 기준이면 결산월 칸을 읽고 그 칸(3)을 따른다 —
    곡선·업종 비교와 같은 칸. 종전엔 개황만 보고 12월로 읽어 FY0 가 한 해 낡았다(10-19 운영 021880)."""
    p, seen = _run_live(monkeypatch, company_acc_mt="12", db_acc_mt=3, reports=MARCH_REPORTS,
                        fm_years={2025: (2025, "11011", "사업보고서 (2026.03)"), 2026: (2026, "11011", "사업보고서 (2026.03)")})
    assert any("SELECT acc_mt FROM dart_fundamentals" in s for s in seen["sql"])
    d = p["data"]
    assert d["fiscal_year"] == 2026 and d["inputs"]["net_income_fy0_krw"] == 26_000_000_000
    assert d["inputs"]["net_income_ttm_krw"] == 26_000_000_000 + 13_920_000_000 - 5_470_000_000
    assert any(w.startswith("결산월 3월") for w in d["warnings"])
    # 칸이 12(결산월을 바꾼 해의 종전 읽기)면 이름이 3월이어도 12월로 읽는다 — 칸을 믿는다
    p, seen = _run_live(monkeypatch, company_acc_mt="12", db_acc_mt=12, reports=MARCH_REPORTS,
                        fm_years={2025: (2025, "11011", "사업보고서 (2026.03)")})
    assert any("SELECT acc_mt FROM dart_fundamentals" in s for s in seen["sql"]) and p["data"]["fiscal_year"] == 2025
    assert not any(w.startswith("결산월") for w in p["data"]["warnings"])


def test_live_firm_december_overview_with_june_cells_uses_the_june_annual(monkeypatch):
    """10-19 운영 001080(MH머트리얼즈) 재현 — 개황은 12월(결산월 변경), 저장된 칸은 6월 결산이고 2026.06 사업보고서(9/10 제출)가 최신.
    종전 실시간 firm 은 12월로 읽어 FY2025(2025.06, 적자 → PER N/M)·TTM 2026.03 3분기 조합을 냈고, 같은 날 firm + as_of·업종 비교는
    FY2026(2026.06) 116.51 이었다. 이제 같은 칸(FY2026 · TTM = 그 사업보고서 연간)을 쓴다."""
    reports = {(2026, "11011"): _annual_rows("1,193,000,000", "361,379,000,000"),        # 2026.06(연결 흑자)
               (2025, "11011"): _annual_rows("-3,536,000,000", "188,571,000,000"),       # 2025.06
               (2026, "11014"): _cum_rows("2,782,000,000", "357,832,000,000"),           # 2026.03 3분기(지난 회계연도)
               (2025, "11014"): _cum_rows("-3,789,000,000", "188,375,000,000")}
    p, seen = _run_live(monkeypatch, company_acc_mt="12", db_acc_mt=6, reports=reports,
                        fm_years={2025: (2025, "11011", "사업보고서 (2026.06)"), 2026: (2026, "11011", "사업보고서 (2026.06)")})
    d = p["data"]
    assert d["fiscal_year"] == 2026 and d["inputs"]["net_income_fy0_krw"] == 1_193_000_000
    assert d["inputs"]["net_income_ttm_krw"] == 1_193_000_000
    assert (d["inputs"]["ttm_quarter"], d["inputs"]["ttm_quarter_year"]) == ("사업보고서(연간)", 2026)
    assert d["inputs"]["controlling_equity_krw"] == 361_379_000_000 and d["multiples"]["pbr_basis"] == "FY0"
    assert d["multiples"]["per_fy0"] == round(60_000_000_000 / 1_193_000_000, 2)
    assert any(w.startswith("결산월 6월") for w in d["warnings"])


def test_source_report_month_parses_only_the_report_name():
    assert S._source_report_month({"data": {"source_report": {"report_nm": "사업보고서 (2026.06)"}}}) == 6
    assert S._source_report_month({"data": {"source_report": {"report_nm": "[기재정정]사업보고서 (2025.12)"}}}) == 12
    for fm in (None, {}, {"data": {}}, {"data": {"source_report": None}}, {"data": {"source_report": {"report_nm": "사업보고서"}}},
               {"data": {"source_report": {"report_nm": "사업보고서 (2026.13)"}}}):
        assert S._source_report_month(fm) is None
