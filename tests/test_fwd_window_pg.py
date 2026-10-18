"""컨센서스 이력 읽는 쪽 — 주별 모양(교체 전)과 달력 모양(구간형 저장 뒤)에서 같은 답을 내는지 Postgres 17 에서 증명한다 (261018).

같은 주간 스냅샷 8벌로 두 저장 모양을 만든다.
  · 주별 모양 — 주마다 통째로 담은 18칸 표 위의 뷰 `fwd_hist`(추정 보유 종목만, 지금 운영과 같은 범위).
  · 달력 모양 — 게시 달력 `fwd_asof` + 구간형 사실값 `fwd_fact_ver`(값이 바뀔 때만 새 판, 유효 구간 [from, to))
    + 주별 시세 `fwd_price`(그 주 추정 보유 `has_est`) 위의 뷰. 뷰는 설계 초안과 같은 두 갈래(추정 행 / 추정 보유 종목의 실적 행).
지키는 것:
  ① 모양 판별은 뷰의 정의로 — 달력 표가 그림자로만 있으면(뷰는 아직 주별) 주별 모양이다.
  ② 서버의 리비전 이력(종목·유니버스)과 선행 집계 배치의 결과가 두 모양에서 같다.
  ③ 달력 모양의 창: 창 밖 닫힌 판을 지운 뒤 창 밖 날짜는 판 일부만 보인다 — 읽는 쪽은 그 날짜를 읽지 않는다.
  ④ 「미분류」 버킷(감사 8)은 이미 쓴 날짜에 그 행만 채우고 다른 행은 건드리지 않는다.
  ⑤ 밀림 감시가 달력 모양에서 창 52주를 정상으로 본다.
Docker 가 없으면 건너뛴다(`tests/conftest.py` 의 `pg17`).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")

from open_proxy_mcp import fwd_window  # noqa: E402
from open_proxy_mcp.services import forward_estimates as fe  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
_SPEC = importlib.util.spec_from_file_location("fwd_val_weekly_pg", ROOT / "scripts" / "fwd_val_weekly.py")
fv = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fv)

#: 게시 8벌 — 10-03 주는 수집이 없었다(운영과 같은 빈 주).
WEEKS = [dt.date(2026, 8, 29), dt.date(2026, 9, 5), dt.date(2026, 9, 12), dt.date(2026, 9, 19),
         dt.date(2026, 9, 26), dt.date(2026, 10, 10), dt.date(2026, 10, 17), dt.date(2026, 10, 24)]
KEY = ("stock_code", "period", "period_type", "is_estimate")
VALS = ("fiscal_year", "period_end", "basis", "rev_krw", "op_krw", "ni_ctrl_krw", "eps_krw", "bps_krw", "dps_krw",
        "roe_pct", "shares_common")
BASIS = {1: "IFRS연결", 2: "IFRS별도"}
STOCKS = {"005930": ("KS", 70000.0, 4.0e14), "000660": ("KS", 180000.0, 1.3e14),
          "035720": ("KS", 40000.0, 1.8e13), "123450": ("KQ", 9000.0, 9.0e10)}


def _snapshots() -> dict[dt.date, list[dict]]:
    """주마다의 사실값 행(키 + 값). 일부 값만 주마다 바뀐다 — 구간형이 판을 나누는 경우와 이어 붙이는 경우가 다 있다."""
    out: dict[dt.date, list[dict]] = {}
    for i, d in enumerate(WEEKS):
        rows = []

        def add(code, period, ptype, est, fy, op, eps, basis=1, ni=None, dps=1000.0):
            rows.append({"stock_code": code, "period": period, "period_type": ptype, "is_estimate": est,
                         "fiscal_year": fy, "period_end": dt.date(fy, 12, 31), "basis": basis,
                         "rev_krw": op * 10, "op_krw": op, "ni_ctrl_krw": ni if ni is not None else int(op * 0.8),
                         "eps_krw": eps, "bps_krw": eps * 10, "dps_krw": dps, "roe_pct": 9.5, "shares_common": None})
        # 삼성전자: 추정 둘(매주 영업이익이 오른다) + 실적 하나(그대로)
        add("005930", "2026.12E", "FY", True, 2026, (400 + 5 * i) * 10**9 * 100, 4000.0 + i)
        add("005930", "2027.12E", "FY", True, 2027, 500 * 10**11 if i < 5 else 520 * 10**11, 5000.0)
        add("005930", "2025.12A", "FY", False, 2025, 300 * 10**11, 3000.0)
        # SK하이닉스: 5주째부터 추정이 생긴다(그 전은 실적만 — 추정 보유가 아니라 이력 뷰에 없다)
        if i >= 4:
            add("000660", "2026.12E", "FY", True, 2026, 200 * 10**11 - i * 10**10, 20000.0)
        add("000660", "2025.12A", "FY", False, 2025, 150 * 10**11, 15000.0)
        # 카카오: 추정이 마지막 주에 빠진다(판이 닫힌다) — 업종분류가 없는 종목(미분류 버킷)
        if i < 7:
            add("035720", "2026.12E", "FY", True, 2026, 50 * 10**10, 1200.0, basis=2)
        add("035720", "2025.12A", "FY", False, 2025, 40 * 10**10, 1000.0)
        # 코스닥 종목: 실적만(추정 없음) — 이력 뷰 범위 밖
        add("123450", "2025.12A", "FY", False, 2025, 30 * 10**8, 300.0)
        out[d] = rows
    return out


def _has_est(rows: list[dict]) -> set[str]:
    return {r["stock_code"] for r in rows if r["is_estimate"]}


def _versions(snaps: dict[dt.date, list[dict]]) -> list[dict]:
    """주간 스냅샷 → 구간형 판. 값이 바뀌거나 키가 그 주에 없으면 그 날짜로 닫는다."""
    open_: dict[tuple, dict] = {}
    done: list[dict] = []
    for d in WEEKS:
        now = {tuple(r[k] for k in KEY): r for r in snaps[d]}
        for k, v in list(open_.items()):
            if k not in now or tuple(now[k][c] for c in VALS) != tuple(v[c] for c in VALS):
                done.append(dict(v, valid_to=d))
                del open_[k]
        for k, r in now.items():
            if k not in open_:
                open_[k] = dict(r, valid_from=d)
    return done + [dict(v, valid_to=dt.date.max) for v in open_.values()]


HIST_COLS = ("as_of", "stock_code", "period", "period_type", "is_estimate", "basis", "fiscal_year", "period_end",
             "rev_krw", "op_krw", "ni_ctrl_krw", "eps_krw", "bps_krw", "dps_krw", "roe_pct", "shares_common",
             "price_krw", "mktcap_krw")

LEGACY_DDL = """
CREATE TABLE fwd_hist_t (as_of date, stock_code text, period text, period_type text, is_estimate boolean, basis text,
  fiscal_year bigint, period_end date, rev_krw bigint, op_krw bigint, ni_ctrl_krw bigint, eps_krw float8, bps_krw float8,
  dps_krw float8, roe_pct float8, shares_common float8, price_krw float8, mktcap_krw float8);
CREATE VIEW fwd_hist AS SELECT * FROM fwd_hist_t;
CREATE VIEW fwd AS SELECT * FROM fwd_hist_t WHERE as_of >= (SELECT max(as_of) FROM fwd_hist_t) - 28;
"""

CALENDAR_TABLES = """
CREATE TABLE fwd_asof (as_of date PRIMARY KEY, price_dd text, n_stock integer NOT NULL, n_fact integer NOT NULL);
CREATE TABLE fwd_text (col text NOT NULL, code smallint NOT NULL, txt text NOT NULL, PRIMARY KEY (col, code));
CREATE TABLE fwd_price (stock_code text NOT NULL, as_of date NOT NULL, price_krw float8, mktcap_krw float8,
  price_dd text, market text, has_est boolean NOT NULL, PRIMARY KEY (stock_code, as_of));
CREATE TABLE fwd_fact_ver (stock_code text NOT NULL, period text NOT NULL, period_type text NOT NULL,
  is_estimate boolean NOT NULL, valid_from date NOT NULL, valid_to date NOT NULL DEFAULT 'infinity',
  fiscal_year smallint, period_end date, basis smallint, rev_krw bigint, op_krw bigint, ni_ctrl_krw bigint,
  eps_krw float8, bps_krw float8, dps_krw float8, roe_pct float8, shares_common float8,
  PRIMARY KEY (stock_code, period, period_type, is_estimate, valid_from), CHECK (valid_from < valid_to));
CREATE INDEX fwd_fact_ver_to ON fwd_fact_ver (valid_to, valid_from);
CREATE INDEX fwd_fact_ver_est_to ON fwd_fact_ver (valid_to, valid_from) WHERE is_estimate;
"""

#: 설계 초안(a_scd)의 fwd_hist 두 갈래를 그대로 — 시세는 주별 시세 표(`fwd_price`).
CALENDAR_VIEWS = """
CREATE VIEW fwd_hist AS
SELECT a.as_of, f.stock_code, f.period, f.period_type, f.is_estimate, tb.txt AS basis, f.fiscal_year::bigint AS fiscal_year,
       f.period_end, f.rev_krw, f.op_krw, f.ni_ctrl_krw, f.eps_krw, f.bps_krw, f.dps_krw, f.roe_pct, f.shares_common,
       p.price_krw, p.mktcap_krw
FROM fwd_asof a
JOIN fwd_fact_ver f ON f.valid_from <= a.as_of AND a.as_of < f.valid_to AND f.is_estimate
LEFT JOIN fwd_price p ON p.as_of = a.as_of AND p.stock_code = f.stock_code
LEFT JOIN fwd_text tb ON tb.col = 'basis' AND tb.code = f.basis
UNION ALL
SELECT a.as_of, f.stock_code, f.period, f.period_type, f.is_estimate, tb.txt AS basis, f.fiscal_year::bigint AS fiscal_year,
       f.period_end, f.rev_krw, f.op_krw, f.ni_ctrl_krw, f.eps_krw, f.bps_krw, f.dps_krw, f.roe_pct, f.shares_common,
       p.price_krw, p.mktcap_krw
FROM fwd_asof a
JOIN fwd_fact_ver f ON f.valid_from <= a.as_of AND a.as_of < f.valid_to AND NOT f.is_estimate
JOIN fwd_price p ON p.as_of = a.as_of AND p.stock_code = f.stock_code AND p.has_est
LEFT JOIN fwd_text tb ON tb.col = 'basis' AND tb.code = f.basis;
CREATE VIEW fwd AS
SELECT a.as_of, f.stock_code, f.period, f.period_type, f.is_estimate, p.price_krw, p.price_dd
FROM fwd_asof a JOIN fwd_fact_ver f ON f.valid_from <= a.as_of AND a.as_of < f.valid_to
LEFT JOIN fwd_price p ON p.as_of = a.as_of AND p.stock_code = f.stock_code;
"""

MARKET_DDL = """
CREATE TABLE wise_sector (ticker text, sector_code text, sector text, industry_code text, industry text, snap_dd text);
CREATE TABLE krx_weekly (ticker text, market text, price_dd text);
CREATE TABLE krx_daily (trd_dd text, ticker text, market text);
CREATE TABLE ops_tool_calls (ts_ns bigint);
"""


def _price_dd(d: dt.date) -> str:
    return (d - dt.timedelta(days=1)).strftime("%Y%m%d")          # 토요일 게시 = 금요일 종가


def _load(url: str, shape: str) -> None:
    """shape: legacy(주별 표 위 뷰) / shadow(달력 표는 있으나 뷰는 주별) / calendar(달력 위 뷰)."""
    snaps = _snapshots()
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(MARKET_DDL)
        c.execute("INSERT INTO wise_sector VALUES ('005930','G45','IT','G4530','반도체','20260828'), "
                  "('000660','G45','IT','G4530','반도체','20260828'), ('123450','G20','산업재','G2010','기계','20260828')")
        for code, (mk, _p, _c) in STOCKS.items():
            for d in WEEKS:
                c.execute("INSERT INTO krx_weekly VALUES (%s, %s, %s)", (code, mk, _price_dd(d)))
        if shape in ("legacy", "shadow"):
            c.execute(LEGACY_DDL)
            for d in WEEKS:
                est = _has_est(snaps[d])
                for r in snaps[d]:
                    if not (r["is_estimate"] or r["stock_code"] in est):
                        continue
                    mk, price, cap = STOCKS[r["stock_code"]]
                    row = dict(r, as_of=d, basis=BASIS[r["basis"]], price_krw=price, mktcap_krw=cap)
                    c.execute(f"INSERT INTO fwd_hist_t ({', '.join(HIST_COLS)}) VALUES ({', '.join(['%s'] * len(HIST_COLS))})",
                              [row[k] for k in HIST_COLS])
        if shape in ("shadow", "calendar"):
            c.execute(CALENDAR_TABLES)
            c.execute("INSERT INTO fwd_text VALUES ('basis', 1, 'IFRS연결'), ('basis', 2, 'IFRS별도')")
            for d in WEEKS:
                est = _has_est(snaps[d])
                codes = {r["stock_code"] for r in snaps[d]}
                c.execute("INSERT INTO fwd_asof VALUES (%s, %s, %s, %s)", (d, _price_dd(d), len(codes), len(snaps[d])))
                for code in codes:
                    mk, price, cap = STOCKS[code]
                    c.execute("INSERT INTO fwd_price VALUES (%s, %s, %s, %s, %s, %s, %s)",
                              (code, d, price, cap, _price_dd(d), mk, code in est))
            cols = KEY + ("valid_from", "valid_to") + VALS
            for v in _versions(snaps):
                row = dict(v, valid_to=("infinity" if v["valid_to"] == dt.date.max else v["valid_to"]))
                c.execute(f"INSERT INTO fwd_fact_ver ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                          [row[k] for k in cols])
        if shape == "calendar":
            c.execute(CALENDAR_VIEWS)


@pytest.fixture
def db(pg17, monkeypatch):
    """서버 읽기(`pg_rows`)가 풀 없이 이 컨테이너로 바로 접속하게 하고, 저장 모양 캐시를 비운다."""
    monkeypatch.setenv("DATABASE_URL", pg17)
    import open_proxy_mcp.db as dbm
    monkeypatch.setattr(dbm, "_pool", None)
    monkeypatch.setattr(dbm, "_pool_disabled_until", float("inf"))
    monkeypatch.setattr(fe, "_hist_cache", {"at": 0.0, "ok": None, "cal": False, "latest": None})
    return pg17


def _fresh_cache(monkeypatch):
    monkeypatch.setattr(fe, "_hist_cache", {"at": 0.0, "ok": None, "cal": False, "latest": None})


def _hist(url: str) -> list[tuple]:
    with psycopg.connect(url) as c:
        return c.execute(f"SELECT {', '.join(HIST_COLS)} FROM fwd_hist ORDER BY 1, 2, 3, 4, 5").fetchall()


def _second_db(pg17: str) -> str:
    """같은 컨테이너에 데이터베이스 하나 더(두 모양을 나란히 비교)."""
    import uuid
    name = "t2_" + uuid.uuid4().hex[:10]
    base = pg17.rsplit("/", 1)[0]
    with psycopg.connect(base + "/postgres", autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
    return base + "/" + name


@pytest.mark.parametrize(("shape", "want"), [("legacy", (True, False)), ("shadow", (True, False)),
                                             ("calendar", (True, True))])
def test_shape_is_read_from_the_view_definition_not_the_table_names(db, shape, want):
    _load(db, shape)
    with psycopg.connect(db) as c:
        assert fwd_window.parse_probe(c.execute(fwd_window.PROBE_SQL).fetchone()) == want


def test_test_views_are_the_same_rows_in_both_shapes(pg17):
    """시험의 두 모양이 같은 사실을 담는다(아래 비교가 헛돌지 않게) — 18칸 행 집합이 같다."""
    other = _second_db(pg17)
    _load(pg17, "legacy")
    _load(other, "calendar")
    assert _hist(pg17) == _hist(other) and len(_hist(pg17)) > 30


def _server_view(monkeypatch, url: str) -> dict:
    monkeypatch.setenv("DATABASE_URL", url)
    _fresh_cache(monkeypatch)
    one = fe._fetch_hist("005930")
    rev = fe.compute_revision(one)
    many = fe._fetch_hist_many(None, "FY", fe._screen_span_days(28))
    # 같은 날짜 안의 행 순서는 질의가 정하지 않는다(ORDER BY 종목·날짜뿐) — 집합으로 비교한다
    one = sorted(one, key=lambda r: (r["as_of"], r["period"], r["period_type"]))
    many = sorted(many)
    screen = asyncio.run(_screen(monkeypatch))
    return {"one": one, "rev": rev, "many": many, "screen": screen, "cal": fe._hist_on_calendar()}


async def _screen(monkeypatch):
    from open_proxy_mcp.services import universe as uni

    async def lu(raw):
        rows = [{"rank": i + 1, "ticker": t, "name": t, "market": "KOSPI", "mktcap_krw": 10**12, "close_krw": 1,
                 "list_shrs": 1} for i, t in enumerate(STOCKS)]
        return uni.UniverseList(spec="kospi:4", label="시험 4", resolved=True, notice="", as_of="20261023",
                                rows=rows, db_ok=True)
    monkeypatch.setattr(uni, "list_universe", lu)
    p = await fe.build_revision_screen_payload("시험 4", window="4w")
    p["data"].pop("note")                                               # 출처 문구는 보관 주 수(13/52)를 말해 다르다
    return p


def test_server_revision_reads_are_identical_in_both_shapes(pg17, monkeypatch):
    """서버 리비전(종목 하나·유니버스 스크린)이 주별 모양과 달력 모양에서 같은 값을 낸다 — 달력 모양은 창 조건이 붙을 뿐."""
    import open_proxy_mcp.db as dbm
    monkeypatch.setattr(dbm, "_pool", None)
    monkeypatch.setattr(dbm, "_pool_disabled_until", float("inf"))
    other = _second_db(pg17)
    _load(pg17, "legacy")
    _load(other, "calendar")
    a = _server_view(monkeypatch, pg17)
    b = _server_view(monkeypatch, other)
    assert a.pop("cal") is False and b.pop("cal") is True
    assert a["one"] and a["one"] == b["one"]
    assert a["rev"] == b["rev"] and a["rev"]["windows_not_reached"] == ["26w", "52w"]
    assert a["many"] == b["many"]
    assert a["screen"] == b["screen"] and a["screen"]["status"] == "ok"


def test_calendar_window_skips_dates_whose_versions_were_partly_moved_out(db, monkeypatch):
    """창 4주로 정리하면 창 밖 날짜는 뷰에서 판 일부만 보인다 — 서버·집계 배치는 그 날짜를 읽지 않는다."""
    _load(db, "calendar")
    monkeypatch.setenv("FWD_HOT_WEEKS", "4")
    latest = WEEKS[-1]
    cutoff = latest - dt.timedelta(days=28)
    with psycopg.connect(db, autocommit=True) as c:
        before = dict(c.execute("SELECT as_of, count(*) FROM fwd_hist GROUP BY 1").fetchall())
        n = c.execute("DELETE FROM fwd_fact_ver WHERE valid_to <= %s", (cutoff,)).rowcount   # 창 정리(보관본 내보낸 뒤)
        after = dict(c.execute("SELECT as_of, count(*) FROM fwd_hist GROUP BY 1").fetchall())
    assert n > 0
    assert any(after.get(d, 0) < before[d] for d in WEEKS if d < cutoff)      # 창 밖 날짜는 일부만 남는다(읽으면 틀린다)
    assert all(after[d] == before[d] for d in WEEKS if d >= cutoff)          # 창 안은 그대로
    rows = fe._fetch_hist("005930")
    assert rows and min(r["as_of"] for r in rows) == cutoff                   # 창의 첫날(포함)부터만
    with psycopg.connect(db) as c:
        days, cal = fv.load_hist_days(c)
    assert cal and days == [d for d in WEEKS if d >= cutoff]


def _val_rows(url: str) -> list[tuple]:
    with psycopg.connect(url) as c:
        return c.execute(f"SELECT {', '.join(fv.COLS)} FROM opm_val_fwd ORDER BY as_of, market, scheme, bucket").fetchall()


def test_forward_aggregate_batch_is_identical_in_both_shapes(pg17, monkeypatch, capsys):
    """선행 집계 배치(날짜 목록: 주별 = 뷰 DISTINCT, 달력 = 게시 달력)가 두 모양에서 같은 표를 만든다 — 미분류 버킷 포함."""
    other = _second_db(pg17)
    _load(pg17, "legacy")
    _load(other, "calendar")
    out = {}
    for url in (pg17, other):
        monkeypatch.setenv("DATABASE_URL", url)
        assert fv.main([]) == 0
        out[url] = _val_rows(url)
    assert out[pg17] == out[other] and out[pg17]
    unc = [r for r in out[pg17] if r[3] == fv.UNCLASSIFIED]
    assert unc and {r[2] for r in unc} == {"wics_sector"} and {r[4] for r in unc} == {"미분류"}
    assert {r[0] for r in unc} == set(WEEKS[:7])          # 카카오(분류 없음)의 추정이 있던 주만 — 마지막 주는 빠졌다
    text = capsys.readouterr().out
    assert "게시 달력 창 52주" in text                       # 달력 모양은 날짜를 달력에서 읽었다고 밝힌다
    # 다시 대조해도 어긋남 0 — 원본 모양이 바뀌어도 같은 집계
    monkeypatch.setenv("DATABASE_URL", other)
    assert fv.main(["--dry", "--check"]) == 0


def test_unclassified_rows_fill_old_days_without_touching_other_rows(db, monkeypatch, capsys):
    """버킷이 생기기 전에 쓴 날짜: 정상 실행이 그 날짜의 미분류 행만 채운다(저장 때 분류·시세 날짜로). 다른 행은 그대로."""
    _load(db, "legacy")
    assert fv.main([]) == 0
    day = WEEKS[2]
    with psycopg.connect(db, autocommit=True) as c:
        c.execute("DELETE FROM opm_val_fwd WHERE bucket = %s", (fv.UNCLASSIFIED,))
        c.execute("UPDATE opm_val_fwd SET computed_at = '2026-01-01' WHERE as_of = %s", (day,))
        before = c.execute("SELECT count(*) FROM opm_val_fwd WHERE as_of = %s", (day,)).fetchone()[0]
    capsys.readouterr()
    assert fv.main(["--dry", "--check"]) == 0                                 # 버킷 없는 날짜는 어긋남으로 세지 않는다
    assert "미분류 버킷은 아직 저장 전" in capsys.readouterr().out
    assert fv.main([]) == 0
    with psycopg.connect(db) as c:
        rows = c.execute("SELECT bucket, computed_at FROM opm_val_fwd WHERE as_of = %s", (day,)).fetchall()
        all_unc = {r[0] for r in c.execute("SELECT as_of FROM opm_val_fwd WHERE bucket = %s", (fv.UNCLASSIFIED,))}
    unc = [r for r in rows if r[0] == fv.UNCLASSIFIED]
    assert len(rows) == before + len(unc) and len(unc) == 2                   # KS·ALL
    assert all(r[1].year == 2026 and r[1].month == 1 for r in rows if r[0] != fv.UNCLASSIFIED)   # 다른 행은 안 건드림
    assert all_unc == set(WEEKS[:7])
    assert "미분류」 버킷이 없던 날짜에 그 행만" in capsys.readouterr().out


@pytest.mark.parametrize(("shape", "phrase"), [("legacy", "fwd 4벌"), ("calendar", "게시 달력 8벌")])
def test_drain_monitor_reads_the_right_dates_per_shape(db, monkeypatch, capsys, shape, phrase):
    """밀림 감시: 주별 모양은 종전 질의·상한(4·13), 달력 모양은 달력 + 창 52주 — 둘 다 정상."""
    from scripts import drain_backlog_check as checker
    _load(db, shape)
    monkeypatch.setattr(sys, "argv", ["drain_backlog_check.py"])
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert checker.main() == 0
    out = capsys.readouterr().out
    assert phrase in out and "상태: OK" in out
    if shape == "calendar":
        monkeypatch.setenv("FWD_HOT_WEEKS", "4")             # 창을 4주로 줄였는데 정리가 안 돌았으면
        assert checker.main() == 1
        out = capsys.readouterr().out
        assert "보다 길다" in out and "창 밖 닫힌 판" in out


@pytest.mark.parametrize("calendar_pruned", [True, False])
def test_drain_monitor_catches_a_writer_window_shorter_than_the_readers(db, monkeypatch, capsys, calendar_pruned):
    """쓰는 쪽이 창 4주로 판을 정리했는데 읽는 쪽 창은 52주(기본)일 때:
    · 쓰는 쪽이 창 밖 날짜의 달력 행도 지웠으면 읽는 쪽은 그 날짜를 못 본다 — 정상.
    · 달력 행을 남겼으면 읽는 쪽은 일부 종목만 남은 날을 한 벌로 읽는다(서버 리비전·선행 집계가 조용히 틀린다).
      달력 길이(8주 ≤ 53)·창 밖 닫힌 판(이미 지움) 검사로는 안 잡힌다 — 창 첫날 검사가 잡는다."""
    from scripts import drain_backlog_check as checker
    _load(db, "calendar")
    cutoff = WEEKS[-1] - dt.timedelta(days=28)
    with psycopg.connect(db, autocommit=True) as c:
        c.execute("DELETE FROM fwd_fact_ver WHERE valid_to <= %s", (cutoff,))
        if calendar_pruned:
            c.execute("DELETE FROM fwd_price WHERE as_of < %s", (cutoff,))
            c.execute("DELETE FROM fwd_asof WHERE as_of < %s", (cutoff,))
    rows = fe._fetch_hist("005930")                                            # 읽는 쪽 창 52주
    monkeypatch.setattr(sys, "argv", ["drain_backlog_check.py"])
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    result = checker.main()
    out = capsys.readouterr().out
    if calendar_pruned:
        assert min(r["as_of"] for r in rows) == cutoff
        assert result == 0 and f"창 첫날 {cutoff}" in out and "상태: OK" in out
    else:
        assert min(r["as_of"] for r in rows) == WEEKS[0]                      # 일부만 남은 날을 읽는다
        assert result == 1 and f"창 첫날 {WEEKS[0]} 이 한 벌이 아니다" in out
        assert "보다 길다 —" not in out and "1주 넘게 남았다" not in out         # 다른 검사로는 안 잡힌다


def _add_quarter(url: str, shape: str) -> None:
    """005930 2026.09 분기 추정(주마다 영업이익이 오른다 — 판이 주마다 나뉜다)을 두 모양에 같은 사실로 넣는다."""
    q = [10**12 + i * 10**10 for i in range(len(WEEKS))]
    with psycopg.connect(url, autocommit=True) as c:
        for i, d in enumerate(WEEKS):
            if shape == "legacy":
                c.execute("INSERT INTO fwd_hist_t (as_of, stock_code, period, period_type, is_estimate, basis, fiscal_year, "
                          "period_end, rev_krw, op_krw, ni_ctrl_krw) VALUES (%s, '005930', '2026.09E', 'Q', true, 'IFRS연결', "
                          "2026, '2026-09-30', %s, %s, %s)", (d, q[i] * 10, q[i], q[i] * 8 // 10))
            else:
                to = WEEKS[i + 1] if i + 1 < len(WEEKS) else "infinity"
                c.execute("INSERT INTO fwd_fact_ver (stock_code, period, period_type, is_estimate, valid_from, valid_to, "
                          "fiscal_year, period_end, basis, rev_krw, op_krw, ni_ctrl_krw) VALUES ('005930', '2026.09E', 'Q', "
                          "true, %s, %s, 2026, '2026-09-30', 1, %s, %s, %s)", (d, to, q[i] * 10, q[i], q[i] * 8 // 10))
            if shape == "calendar":
                c.execute("UPDATE fwd_asof SET n_fact = n_fact + 1 WHERE as_of = %s", (d,))


def _gaps(monkeypatch, url: str) -> dict:
    monkeypatch.setenv("DATABASE_URL", url)
    _fresh_cache(monkeypatch)
    actual = {"revenue": 10_500 * 10**9, "operating_profit": 1_040 * 10**9, "net_income_controlling": None}
    out = {}
    for filed in ("2026-08-20", "2026-09-20", "2026-10-08", "2026-10-30"):
        g = fe.quarter_consensus_gap("005930", "2026-09-30", filed, actual, True)
        out[filed] = {k: v for k, v in g.items() if k not in ("source", "why", "note")}   # 문구는 보관 주 수를 말해 모양별로 다르다
    return out


def test_consensus_gap_is_the_same_in_both_shapes(pg17, monkeypatch):
    """#111 잠정실적 컨센서스 대비 — 저장 전환 전후 같은 값(기준일·간격·예상치·차이·상태)."""
    other = _second_db(pg17)
    _load(pg17, "legacy")
    _add_quarter(pg17, "legacy")
    _load(other, "calendar")
    _add_quarter(other, "calendar")
    monkeypatch.setattr(dbm_module(), "_pool", None)
    monkeypatch.setattr(dbm_module(), "_pool_disabled_until", float("inf"))
    a, b = _gaps(monkeypatch, pg17), _gaps(monkeypatch, other)
    assert a == b
    assert a["2026-08-20"]["status"] == "out_of_history"
    assert (a["2026-09-20"]["as_of"], a["2026-10-08"]["as_of"], a["2026-10-30"]["as_of"]) == ("2026-09-19", "2026-09-26", "2026-10-24")
    assert a["2026-10-08"]["metrics"]["operating_profit"]["consensus_krw"] == 10**12 + 4 * 10**10


def test_consensus_gap_reads_only_inside_the_window_on_the_calendar(pg17, monkeypatch):
    """달력 모양에서 창(여기선 4주) 밖 날짜는 판 일부가 보관본으로 옮겨졌을 수 있다 — 그 날 스냅샷으로 맞대지 않고 「이력 밖」이다.
    창 안 공시는 전환 전과 같은 기준일이다."""
    _load(pg17, "calendar")
    _add_quarter(pg17, "calendar")
    monkeypatch.setattr(dbm_module(), "_pool", None)
    monkeypatch.setattr(dbm_module(), "_pool_disabled_until", float("inf"))
    monkeypatch.setenv("FWD_HOT_WEEKS", "4")                                   # 창 첫날 = 10-24 − 28일 = 09-26
    g = _gaps(monkeypatch, pg17)
    assert g["2026-09-20"]["status"] == "out_of_history"                       # 09-19 스냅샷은 창 밖 — 읽지 않는다
    assert (g["2026-10-08"]["as_of"], g["2026-10-30"]["as_of"]) == ("2026-09-26", "2026-10-24")


def dbm_module():
    import open_proxy_mcp.db as dbm
    return dbm
