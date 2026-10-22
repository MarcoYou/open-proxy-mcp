"""성과 분석 읽기 계층(`services/perf_data.py`)을 Postgres 17 의 실제 표·뷰에서 증명한다 (261022).

  P1  달력·종목 번호·일별 시세 — `tid = ANY(...)` 읽기가 종목별 (종가, 공표 대비 cmp, 주식수)를 그대로 돌려준다(cmp NULL 행 포함).
  P2  주간 시세 뷰(압축 표 + 일별 주 닫는 날)와 주 닫는 날 고르기.
  P3  수정계수 원장 원값 읽기(반올림 계수 아님).
  P4  계산 벤치마크 SQL 집계가 파이썬 종목별 합과 같다(분자 Σcmp·주식수, 분모 Σ(close−cmp)·주식수 — 업종별, 소급·구간 경계·cmp NULL 센 값 포함).
  P5  업종 구간 조인(`wise_sector_iv`)이 「그 날 이하 가장 최근 스냅샷, 없으면 첫 스냅샷 소급」 규칙(뷰 `wise_sector`)과 같다.
  P6  공식 지수: 계열 사전·종가, 같은 이름이 양쪽 시장에 있는 업종은 되묻는다.
  P7  배당: 정정 2행 → 늦은 접수만, 기준일 창·DPS 0 거르기, 범위 표.
  P8  무위험 금리: 구간 시작 이전 마지막 관측부터. **`ecos_rate` SELECT 가 없는 역할이면 금리만 None 이고 나머지 읽기는 그대로다**(권한 오류가 DB 장애로 번지지 않는다).
Docker 가 없으면 건너뛴다(`tests/conftest.py` 의 `_pg17_server`). 이 시험은 모듈이 없던 main(39e6a672)에서 ImportError 로 실패한다.
"""
from __future__ import annotations

import importlib.util
import pathlib
import uuid
from datetime import date

import pytest

psycopg = pytest.importorskip("psycopg")

from test_krx_daily_pg import _ddl, put_daily  # noqa: E402

from open_proxy_mcp import db as dbm  # noqa: E402
from open_proxy_mcp.services import perf_data as D  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


A, B, C, DD, PR = "900010", "900020", "800010", "800020", "900015"     # B: 5분할 · C: cmp NULL · DD: 20261008 상장폐지 · PR: A 의 우선주
DAYS = ["20260930", "20261005", "20261006", "20261007", "20261008", "20261012", "20261013", "20261014", "20261015", "20261016"]

#: 종목별 (시장, 상장주식수, [(종가, cmp)...]) — 날짜 DAYS 순서. cmp = KRX 공표 전일대비.
A_CLOSE = [9900, 10000, 10100, 10200, 10100, 10300, 10400, 10500, 10600, 10700]
B_CLOSE = [50000, 50500, 51000, 51500, 52000, 52500, 10500, 10600, 10700, 10800]            # 20261013 5분할: 52,500 → 기준가 10,500(×0.2), 종가 10,500
C_CLOSE = [5000, 5050, 5100, 5150, 5200, 5250, 5300, 5350, 5400, 5450]
D_CLOSE = [2000, 2020, 2040, 2060, 2080]                                                    # 20261008 까지만(그 뒤 상장폐지)
PR_CLOSE = [5000, 5010, 5020, 5030, 5040, 5050, 5060, 5070, 5080, 5090]


def _rows():
    out = []
    for i, dd in enumerate(DAYS):
        a_prev = A_CLOSE[i - 1] if i else 9800
        out.append((dd, A, "KS", A_CLOSE[i], 1000, None, None, None, 100, 1000, A_CLOSE[i] - a_prev))
        if i == 6:                                                                       # 분할일: 주식 5배, 기준가 = 52,500 × 0.2
            out.append((dd, B, "KS", B_CLOSE[i], 2500, None, None, None, 100, 1000, B_CLOSE[i] - 10500))
        else:
            b_prev = B_CLOSE[i - 1] if i else 49500
            out.append((dd, B, "KS", B_CLOSE[i], 500 if i < 6 else 2500, None, None, None, 100, 1000, B_CLOSE[i] - b_prev))
        c_prev = C_CLOSE[i - 1] if i else 4950
        out.append((dd, C, "KQ", C_CLOSE[i], 400, None, None, None, 100, 1000, None if i < 4 else C_CLOSE[i] - c_prev))   # 앞 4일은 옛 이관 행(cmp 없음)
        if i < len(D_CLOSE):
            d_prev = D_CLOSE[i - 1] if i else 1980
            out.append((dd, DD, "KQ", D_CLOSE[i], 100, None, None, None, 100, 1000, D_CLOSE[i] - d_prev))
        out.append((dd, PR, "KS", PR_CLOSE[i], 200, None, None, None, 100, 1000, PR_CLOSE[i] - (PR_CLOSE[i - 1] if i else 4990)))
    return out


@pytest.fixture(scope="module")
def world(_pg17_server):
    admin, url_for = _pg17_server
    dbname = "perf_" + uuid.uuid4().hex[:8]
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{dbname}"')
        for role, attrs in (("anon", "NOLOGIN"), ("authenticated", "NOLOGIN"), ("service_role", "NOLOGIN BYPASSRLS"), ("opm_ro", "NOLOGIN")):
            if not c.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                c.execute(f"CREATE ROLE {role} {attrs}")
        if not c.execute("SELECT 1 FROM pg_roles WHERE rolname='perf_app'").fetchone():
            c.execute("CREATE ROLE perf_app LOGIN PASSWORD 'x'")
    url = url_for(dbname)
    _ddl().apply(url)
    _script("krx_weekly_ddl").apply(url, "full")
    _script("wise_sector_iv_ddl").apply(url, "full")
    _script("krx_idx_ddl").apply(url)
    _script("ecos_rate_ddl").apply(url)
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("""
CREATE TABLE krx_adj_events (ticker text NOT NULL, event_dd text NOT NULL, market text, event_close double precision,
  prev_close double precision, base_price double precision, adj_factor_raw double precision, adj_factor double precision,
  event_type text, rcept_no text, label_source text, label_confidence text, note text, PRIMARY KEY (ticker, event_dd));
CREATE TABLE krx_stock_flags (ticker text PRIMARY KEY, flag text NOT NULL, detail jsonb, updated text);
CREATE TABLE div_payment (corp_code text, corp_name text, stock_code text, market text, fiscal_year integer, acc_mt integer,
  dividend_type text, dividend_type_filed text, agm_required text, record_date text, board_date text, dps_common double precision,
  total_amount double precision, rcept_no text PRIMARY KEY, amended boolean, anomaly text, has_special boolean, remarks text);
CREATE TABLE div_payment_scope (fiscal_year integer NOT NULL, market text NOT NULL, is_complete boolean, reason text,
  n_payments integer, n_corps integer, window_lo integer, window_hi integer, PRIMARY KEY (fiscal_year, market));
""")
        put_daily(c, _rows())
        tid = dict(c.execute("SELECT ticker, tid FROM krx_ticker").fetchall())
        # 압축 표의 옛 닫힌 주 둘(일별 보관 창 앞)
        for dd in ("20260918", "20260925"):
            c.execute("INSERT INTO krx_wk_day (wk_dd, dd, n) VALUES (%s, %s, 2)", (date(int(dd[:4]), int(dd[4:6]), int(dd[6:])), dd))
            for t, mk, close in ((A, "S", 9700), (B, "S", 48000)):
                c.execute("INSERT INTO krx_wk (list_shrs, close, wk_dd, tid, market) VALUES (%s, %s, %s, %s, %s)",
                          (1000, close, date(int(dd[:4]), int(dd[4:6]), int(dd[6:])), tid[t], mk))
        # 수정계수 원장 — 20261013 B 5분할 원값(반올림 계수는 0.2)
        c.execute("INSERT INTO krx_adj_events (ticker, event_dd, market, prev_close, base_price, adj_factor, event_type, label_confidence) "
                  "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", (B, "20261013", "KS", 52500.0, 10500.0, 0.2, "split", "confirmed"))
        c.execute("INSERT INTO krx_adj_events (ticker, event_dd, market, prev_close, base_price, adj_factor, event_type, label_confidence) "
                  "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)", (C, "20261007", "KQ", 5100.0, 5150.0, 1.0098, None, "excluded_tick_rounding"))
        c.execute("INSERT INTO krx_stock_flags VALUES (%s, %s, NULL, '20261001')", (B, "unresolved_adjustment"))
        # 업종 구간 — 스냅샷 1001·1012. B 는 1012 에 반도체 → 자동차로 옮겨 간다. DD 는 분류 없음(미분류).
        for snap in ("20261001", "20261012"):
            c.execute("INSERT INTO wise_sector_snap (snap, dd, n) VALUES (%s, %s, 0)", (date(int(snap[:4]), int(snap[4:6]), int(snap[6:])), snap))
        semi, auto, soft = ("G45", "IT", "G4530", "반도체와반도체장비"), ("G25", "경기관련소비재", "G2510", "자동차와부품"), ("G45", "IT", "G4510", "소프트웨어와서비스")

        def iv(ticker, frm, to, k):
            c.execute("INSERT INTO wise_sector_iv (tid, valid_from, valid_to, src, sector_code, sector, industry_code, industry) "
                      "VALUES (%s, %s, %s, 'W', %s, %s, %s, %s)",
                      (tid[ticker], date(int(frm[:4]), int(frm[4:6]), int(frm[6:])),
                       date(int(to[:4]), int(to[4:6]), int(to[6:])) if to else None, *k))
        iv(A, "20261001", None, semi)
        iv(PR, "20261001", None, semi)
        iv(B, "20261001", "20261012", semi)
        iv(B, "20261012", None, auto)
        iv(C, "20261001", None, soft)
        c.execute("UPDATE wise_sector_snap s SET n = (SELECT count(*) FROM wise_sector w WHERE w.snap_dd = s.dd)")
        # 공식 지수 — 같은 이름 「전기전자」가 코스피·코스닥 양쪽에 있다
        for sid, src, name, kind, in_db in ((1, "P", "코스피", "z", True), (2, "P", "코스피 200", "z", True), (3, "P", "전기전자", "s", True),
                                            (4, "Q", "코스닥", "z", True), (5, "Q", "전기전자", "s", True), (6, "X", "KRX 반도체", "s", False)):
            c.execute("INSERT INTO krx_idx_series (sid, src, name, kind, in_db, first_dd, last_dd) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                      (sid, src, name, kind, in_db, date(2026, 9, 18) if in_db else None, date(2026, 10, 16) if in_db else None))
        for dd, v in zip(DAYS, (260000, 261000, 262000, 261500, 263000, 264000, 262500, 265000, 266000, 267000)):
            c.execute("INSERT INTO krx_idx (sid, trd_dd, close_x100) VALUES (1, %s, %s)", (date(int(dd[:4]), int(dd[4:6]), int(dd[6:])), v))
        # 금리(국고채 1년) — 20260929 에 한 번, 20261007 에 한 번
        c.execute("INSERT INTO ecos_rate (series, d, rate) VALUES ('1', '2026-09-29', 2.875), ('1', '2026-10-07', 2.9), ('1', '2026-09-01', 2.8)")
        # 배당 결정 — A: 20251231 기준일에 정정 2행(늦은 접수가 이긴다)·20261014 중간배당·DPS 0 / PR 은 행 없음
        ins = ("INSERT INTO div_payment (corp_code, stock_code, market, fiscal_year, dividend_type, record_date, board_date, dps_common, rcept_no, amended) "
               "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)")
        c.execute(ins, ("C1", A, "KOSPI", 2025, "결산배당", "2025-12-31", "2026-02-10", 100.0, "20260210000001", False))
        c.execute(ins, ("C1", A, "KOSPI", 2025, "결산배당", "2025-12-31", "2026-02-24", 150.0, "20260224000007", True))
        c.execute(ins, ("C1", A, "KOSPI", 2026, "중간배당", "2026-10-14", "2026-09-30", 40.0, "20260930000003", False))
        c.execute(ins, ("C1", A, "KOSPI", 2026, "분기배당", "2026-03-31", "2026-02-24", 0.0, "20260224000009", False))       # DPS 0 — 거른다
        c.execute(ins, ("C3", C, "KOSDAQ", 2026, "결산배당", "2026-10-30", None, 30.0, "20261001000004", False))
        c.execute("INSERT INTO div_payment_scope (fiscal_year, market, is_complete, reason, window_lo) VALUES "
                  "(2019, 'KOSPI', false, '수집창 시작 이전 결의 누락', 2020), (2024, 'KOSPI', true, '', 2020), (2026, 'KOSPI', false, '마감 전', 2020)")
        # 서버 계정을 닮은 역할: 모든 표를 읽되 ecos_rate 는 못 읽는다
        c.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO perf_app")
        c.execute("REVOKE ALL ON ecos_rate FROM perf_app")
    yield {"url": url, "app_url": url.replace("postgres:test@", "perf_app:x@"), "tid": tid}
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')


@pytest.fixture
def use_db(world, monkeypatch):
    def _use(which="url"):
        monkeypatch.setenv("DATABASE_URL", world[which])
        monkeypatch.setattr(dbm, "_pool", None)
        monkeypatch.setattr(dbm, "_pool_disabled_until", float("inf"))        # 풀 없이 직접 접속
    _use()
    return _use


# ── P1 달력·종목 번호·일별 시세 ──────────────────────────────────────────────────

def test_daily_calendar_tids_and_prices(world, use_db):
    assert D.read_daily_days() == DAYS
    tids = D.read_tids([A, B, "000000"])
    assert set(tids) == {A, B}                                                      # 번호표에 없는 코드는 빠진다
    px = D.read_daily_px(D.read_tids([A, B, C, DD]), "20261005", "20261013")
    assert [r[0] for r in px[A]] == DAYS[1:7]                                       # [lo, hi] 닫힌 구간
    assert px[A][0] == ("20261005", 10000, 100, 1000)
    assert px[B][-1] == ("20261013", 10500, 0, 2500)                                # 분할일: 종가 10,500, 공표 기준가 10,500 → cmp 0, 주식수 5배
    assert [r[2] for r in px[C][:3]] == [None] * 3 and px[C][3][2] == 50            # 옛 이관 행은 cmp NULL
    assert [r[0] for r in px[DD]] == ["20261005", "20261006", "20261007", "20261008"]   # 상장폐지 종목은 마지막 거래일에서 끝난다


# ── P2 주간 시세·주 닫는 날 ──────────────────────────────────────────────────────

def test_weekly_view_unions_compressed_weeks_and_daily_week_closes(world, use_db):
    rows = D.read_weekly_px([A], "20260901", "20261231")[A]
    assert [r[0] for r in rows] == ["20260918", "20260925", "20260930", "20261008", "20261016"]      # 압축 표 둘 + 일별의 주 닫는 날 셋
    assert rows[0] == ("20260918", 9700, 1000) and rows[-1] == ("20261016", 10700, 1000)
    assert D.pick_week_close("20261013") == "20261008" and D.pick_week_close("20261016") == "20261016"
    assert D.pick_week_close("20200101") is None
    mk = D.read_weekly_market_px(["KQ"], "20260930", "20261231")
    assert set(mk) == {C} | {DD} and [r[0] for r in mk[C]] == ["20260930", "20261008", "20261016"]


def test_week_close_calendar_matches_the_weekly_view_dates(world, use_db):
    """지수 target 의 주간 격자 — 종목 시세 없이도 뷰 `krx_weekly` 와 같은 주 닫는 날 목록(압축 표 + 일별의 주 닫는 날)."""
    assert D.read_week_days("20260901", "20261231") == ["20260918", "20260925", "20260930", "20261008", "20261016"]
    assert D.read_week_days("20260920", "20261010") == ["20260925", "20260930", "20261008"]
    assert D.read_week_days("20200101", "20200201") == []
    assert D.read_week_days("20260901", "20261231") == sorted({r[0] for r in D.read_weekly_px([A], "20260901", "20261231")[A]})   # 종목 시세의 날짜와 같은 주 정의


# ── P3 수정계수 원값·딱지 ────────────────────────────────────────────────────────

def test_adj_events_are_raw_values_and_flags_read(world, use_db):
    ev = D.read_adj_events([A, B, C], "20261005", "20261016")
    assert ev[B][0].prev_close == 52500.0 and ev[B][0].base_price == 10500.0           # 원값 — 0.2 로 반올림된 계수가 아니다
    assert ev[C][0].label == "excluded_tick_rounding" and A not in ev
    assert D.read_adj_events([B], "20261013", "20261016") == {}                         # (lo, hi] 반열린 — lo 날짜의 사건은 이미 앞 구간
    assert D.read_stock_flags([A, B]) == {B: "unresolved_adjustment"}


# ── P4 계산 벤치마크 SQL 집계 = 파이썬 종목별 합 ─────────────────────────────────

def test_bench_daily_sql_equals_python_per_stock_sums(world, use_db):
    markets = ["KS", "KQ"]
    got = D.read_bench_daily(markets, "20260930", "20261016")
    sec = D.read_sector_map([A, B, C, DD, PR])
    rows = _rows()
    prev = {dd: DAYS[i - 1] for i, dd in enumerate(DAYS) if i}
    want: dict[tuple, list] = {}
    n_null: dict[str, int] = {}
    for dd, t, mk, close, shrs, _o, _h, _l, _v, _val, cmp in rows:
        if dd not in prev:
            continue
        if cmp is None:
            n_null[dd] = n_null.get(dd, 0) + 1
            key = (dd, sec.at(t, prev[dd]))
            want.setdefault(key, [0.0, 0.0])
            continue
        key = (dd, sec.at(t, prev[dd]))
        v = want.setdefault(key, [0.0, 0.0])
        v[0] += cmp * shrs
        v[1] += (close - cmp) * shrs
    assert {(g.dd, g.key) for g in got} == set(want)
    for g in got:
        assert g.num == pytest.approx(want[(g.dd, g.key)][0], rel=1e-9, abs=1e-6) and g.den == pytest.approx(want[(g.dd, g.key)][1], rel=1e-9, abs=1e-6), g
    assert {g.dd: g.n_null for g in got if g.n_null} == n_null                        # cmp NULL 행 수를 센다(합에서는 뺀다)
    assert all(g.dd != "20260930" for g in got)                                       # 기준일은 전일 계산용 — 결과에 없다
    ks = D.read_bench_daily(["KS"], "20261005", "20261008")
    assert {g.key.mid for g in ks if g.key} == {"반도체와반도체장비"} and sum(g.n for g in ks if g.dd == "20261006") == 3   # A·B·PR
    assert D.read_bench_daily(["KS"], "20261016", "20261016") == []                    # 하루뿐이면 전일이 없다


def test_bench_daily_sector_is_the_one_covering_the_previous_day_with_retro_before_first_snapshot(world, use_db):
    got = D.read_bench_daily(["KS", "KQ"], "20260930", "20261016")
    by = {(g.dd, g.key.mid if g.key else None): g for g in got}
    assert by[("20261005", "반도체와반도체장비")].n == 3                               # 전일 20260930 < 첫 스냅샷 20261001 → 소급 (A·B·PR)
    assert ("20261005", None) in by                                                    # DD 는 구간이 없어 미분류로 남는다(합 보존)
    # B 는 20261012 에 자동차로 옮겼다: 20261012 구간의 전일은 20261008(아직 반도체), 20261013 구간의 전일은 20261012(자동차)
    assert ("20261012", "자동차와부품") not in by and by[("20261013", "자동차와부품")].n == 1
    assert by[("20261012", "반도체와반도체장비")].n == 3 and by[("20261013", "반도체와반도체장비")].n == 2     # 20261013: A·PR 만 반도체


# ── P5 업종 구간 조인 = 「스냅샷 ≤ d 중 가장 최근」 규칙 ──────────────────────────

def test_sector_map_equals_wise_sector_view_rule(world, use_db):
    sec = D.read_sector_map([A, B, C, DD, PR])
    assert sec.snaps == ["20261001", "20261012"]
    with psycopg.connect(world["url"]) as c:
        for dd in DAYS + ["20261011", "20260801"]:
            snap = c.execute("SELECT COALESCE((SELECT max(snap_dd) FROM wise_sector WHERE snap_dd <= %s), (SELECT min(snap_dd) FROM wise_sector))",
                             (dd,)).fetchone()[0]
            view = {t: (sc, s, ic, i) for t, sc, s, ic, i in c.execute(
                "SELECT ticker, sector_code, sector, industry_code, industry FROM wise_sector WHERE snap_dd = %s", (snap,)).fetchall()}
            for t in (A, B, C, DD, PR):
                k = sec.at(t, dd)
                assert (None if k is None else (k.major_code, k.major, k.mid_code, k.mid)) == view.get(t), (dd, t)
    assert sec.retro is True                                                          # 20260930·20260801 처럼 첫 스냅샷 앞 날짜를 물었다


# ── P6 공식 지수 ─────────────────────────────────────────────────────────────────

def test_index_series_and_closes_and_ambiguous_name(world, use_db):
    series = D.read_index_series()
    assert [(s.sid, s.src, s.name, s.in_db) for s in series][:3] == [(1, "P", "코스피", True), (2, "P", "코스피 200", True), (3, "P", "전기전자", True)]
    assert D.resolve_index("전기전자", series).hit is None and D.resolve_index("코스닥 전기전자", series).hit.sid == 5
    assert D.resolve_index("코스피200", series).hit.sid == 2 and D.resolve_index("KRX 반도체", series).hit.in_db is False
    closes = D.read_index_closes(1, "20261005", "20261008")
    assert closes == [("20261005", 2610.0), ("20261006", 2620.0), ("20261007", 2615.0), ("20261008", 2630.0)]      # close_x100 / 100


# ── P7 배당 ──────────────────────────────────────────────────────────────────────

def test_dividends_latest_amendment_only_and_dps_zero_dropped(world, use_db):
    divs = D.read_dividends([A, B, C, PR], "20251201", "20261031")
    assert [(d.ticker, d.record_dd, d.dps, d.rcept_no) for d in divs] == [
        (C, "20261030", 30.0, "20261001000004"), (A, "20251231", 150.0, "20260224000007"), (A, "20261014", 40.0, "20260930000003")]
    assert all(d.ticker != PR for d in divs)                                           # 우선주는 DPS 칸이 없어 행이 없다
    a = {d.record_dd: d for d in divs if d.ticker == A}
    assert a["20251231"].board_dd == "20260224" and a["20251231"].market == "KOSPI" and a["20251231"].fiscal_year == 2025
    assert D.read_dividends([A], "20260101", "20260630") == []                          # 기준일 창 밖 + DPS 0 행은 읽지 않는다
    scope = D.read_div_scope()
    assert {(s.fiscal_year, s.market, s.is_complete, s.window_lo) for s in scope} == {
        (2019, "KOSPI", False, 2020), (2024, "KOSPI", True, 2020), (2026, "KOSPI", False, 2020)}


# ── P8 무위험 금리·권한 ──────────────────────────────────────────────────────────

def test_rf_starts_at_last_observation_before_the_window(world, use_db):
    # 구간 시작 20261005 이전의 마지막 관측(20260929)부터 — 적립은 구간 시작 시점 값을 쓴다
    assert D.read_rf("20261005", "20261016") == [("20260929", 2.875), ("20261007", 2.9)]
    assert D.read_rf("20260801", "20260831") == []                                       # 이전 관측도 창 안 관측도 없다(20260901 은 창 밖)
    assert D.read_rf("20260901", "20260905") == [("20260901", 2.8)]


def test_role_without_ecos_rate_select_loses_only_the_rate(world, use_db):
    use_db("app_url")
    assert D.read_rf("20261005", "20261016") is None                                    # 권한 오류 → None (호출부가 rf_unavailable 로 비운다)
    assert D.read_daily_days() == DAYS                                                  # 나머지 읽기는 그대로
    assert D.read_daily_px(D.read_tids([A]), "20261005", "20261008")[A][0][0] == "20261005"
    assert D.read_sector_map([A]) is not None and D.read_index_series() is not None
    assert D.read_dividends([A], "20251201", "20261031") is not None and D.read_div_scope() is not None


# ── P9 시장 정보(기본 벤치마크 선택·주간 벤치마크 행 어림) ─────────────────────────────

def test_markets_counts_and_weekly_market_tail(world, use_db):
    tids = D.read_tids([A, B, C, DD])
    assert D.read_markets(tids, "20261005") == {A: "KS", B: "KS", C: "KQ", DD: "KQ"}
    assert DD not in D.read_markets(tids, "20261012")                                    # 상장폐지 종목은 그 날 행이 없다
    assert D.read_market_counts("20261005") == {"KS": 3, "KQ": 2} and D.read_market_counts("20261016") == {"KS": 3, "KQ": 1}
    assert D.read_market_counts("20250101") == {}
    # 끝 기준일이 주 닫는 날이 아니면 일별 저장분의 그 날 행을 끝점으로 잇는다(주 닫는 날이면 중복 없이)
    mid = D.read_weekly_market_px(["KQ"], "20260930", "20261012", "20261014")
    assert [r[0] for r in mid[C]] == ["20260930", "20261008", "20261014"] and mid[C][-1][1] == C_CLOSE[7]
    wk = D.read_weekly_market_px(["KQ"], "20260930", "20261016", "20261016")
    assert [r[0] for r in wk[C]] == ["20260930", "20261008", "20261016"]                  # 20261016 은 주 닫는 날 — 한 번만
    assert [r[0] for r in D.read_weekly_market_px(["KQ"], "20260930", "20261016")[C]] == ["20260930", "20261008", "20261016"]
