"""trading_data 지수·금리·시세 순위표(`services/trading_screen.py`)의 SQL 을 Postgres 17 의 실제 표·뷰에서 증명한다 (2026-10-22).

  S1  지수: 계열 사전·종가를 읽어 코스피 20261021 종가가 573566 → 5,735.66 으로 나오고, 상한 날짜(as_of)가 그 이하 가장 늦은 보관일로 풀린다.
  S2  금리: 국고채 1년 최신값·기본 최근 1년 구간·구간 시작 이전 기준점.
  S3  순위표 52주: 고가·저가는 기준일 −364일 이후 행만, 고가·저가가 0 인 거래 없는 날은 뺀다 / 창 앞의 옛 값(20251014)은 세지 않는다.
  S4  액면분할 종목(0.1)은 지금 규칙(원 시세 최댓값)이면 52주 고가가 현재가의 10배라 순위표가 산출 불가로 비운다 / 호가단위 맞춤 이벤트·창 앞 이벤트는 막지 않는다.
Docker 가 없으면 건너뛴다(`tests/conftest.py` 의 `_pg17_server`). 이 시험은 모듈이 없던 main(877478a7)에서 ImportError 로 실패한다.
"""
from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from test_krx_daily_pg import _ddl, put_daily  # noqa: E402

from open_proxy_mcp import db as dbm  # noqa: E402
from open_proxy_mcp.services import trading_screen as ts  # noqa: E402
from open_proxy_mcp.services import universe as uni  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent

T1, T2, T3 = "900001", "900002", "900003"      # T1 정상(신고가) · T2 5분할(×0.1) · T3 호가단위 맞춤 이벤트만
OLD, W_FIRST, MID, SPLIT_DAY, LAST_BUT_ONE, LAST = "20251014", "20251022", "20260601", "20260309", "20261020", "20261021"


def _script(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rows():
    """(trd_dd, ticker, market, close, list_shrs, open, high, low, volume, value, cmp)"""
    return [
        # T1 — 창 앞(20251014)의 고가 99,999·저가 1 은 52주(20251022~)에 들지 않는다 / 20251201 은 거래 없는 날(고가·저가 0)
        (OLD, T1, "KS", 9_500, 1000, 9_500, 99_999, 1, 100, 1, None),
        (W_FIRST, T1, "KS", 10_000, 1000, 10_000, 12_000, 9_000, 100, 1_000_000, None),
        ("20251201", T1, "KS", 11_000, 1000, 0, 0, 0, 0, 0, 0),
        (MID, T1, "KS", 14_000, 1000, 13_800, 15_000, 13_500, 100, 1_400_000, 400),
        (LAST_BUT_ONE, T1, "KS", 14_500, 1000, 14_000, 14_800, 14_000, 100, 1_450_000, 500),
        (LAST, T1, "KS", 15_000, 1000, 14_700, 15_000, 14_600, 1_000, 15_000_000, 500),
        # T2 — 5분할: 분할 전 가격 100,000(고가 200,000), 20260309 부터 10,000 대
        (W_FIRST, T2, "KS", 100_000, 1000, 100_000, 200_000, 90_000, 100, 10_000_000, None),
        (SPLIT_DAY, T2, "KS", 10_000, 5000, 10_000, 11_000, 9_500, 100, 1_000_000, 0),
        (LAST_BUT_ONE, T2, "KS", 19_900, 5000, 19_900, 20_000, 19_500, 100, 1_990_000, 100),
        (LAST, T2, "KS", 20_000, 5000, 19_900, 20_500, 19_800, 200, 4_000_000, 100),
        # T3 — 호가단위 5원 맞춤 이벤트만
        (W_FIRST, T3, "KQ", 2_080, 1000, 2_080, 2_100, 2_040, 100, 208_000, None),
        (LAST_BUT_ONE, T3, "KQ", 2_080, 1000, 2_080, 2_090, 2_075, 100, 208_000, 0),
        (LAST, T3, "KQ", 2_085, 1000, 2_080, 2_090, 2_080, 100, 208_500, 5),
    ]


#: 실제 코스피 일별 종가(close_x100) — 2026-10-22 읽기 전용 조회
_KOSPI = [("2025-10-20", 381469), ("2025-10-21", 382384), ("2025-12-29", 422056), ("2025-12-30", 421417), ("2026-09-18", 689423),
          ("2026-09-21", 700772), ("2026-10-14", 600881), ("2026-10-20", 555274), ("2026-10-21", 573566)]


@pytest.fixture(scope="module")
def world(_pg17_server):
    admin, url_for = _pg17_server
    dbname = "tscr_" + uuid.uuid4().hex[:8]
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{dbname}"')
        for role, attrs in (("anon", "NOLOGIN"), ("authenticated", "NOLOGIN"), ("service_role", "NOLOGIN BYPASSRLS"), ("opm_ro", "NOLOGIN")):
            if not c.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                c.execute(f"CREATE ROLE {role} {attrs}")
    url = url_for(dbname)
    _ddl().apply(url)
    _script("krx_weekly_ddl").apply(url, "full")
    _script("krx_idx_ddl").apply(url)
    _script("ecos_rate_ddl").apply(url)
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("""
CREATE TABLE krx_adj_events (ticker text NOT NULL, event_dd text NOT NULL, market text, event_close double precision,
  prev_close double precision, base_price double precision, adj_factor_raw double precision, adj_factor double precision,
  event_type text, rcept_no text, label_source text, label_confidence text, note text, PRIMARY KEY (ticker, event_dd));
""")
        put_daily(c, _rows())
        ev = ("INSERT INTO krx_adj_events (ticker, event_dd, market, prev_close, base_price, adj_factor, event_type, label_confidence) "
              "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)")
        c.execute(ev, (T2, SPLIT_DAY, "KS", 100_000.0, 10_000.0, 0.1, "split", "confirmed"))                    # 5분할 — 막는다
        c.execute(ev, (T3, "20260410", "KQ", 2_082.0, 2_085.0, 1.0014, None, "unlabeled"))                      # 호가단위 맞춤(라벨 비어 있음) — 코드로 가려 막지 않는다
        c.execute(ev, (T3, "20260501", "KQ", 2_085.0, 2_200.0, 1.0551, "tick_rounding", "excluded_tick_rounding"))   # 제외 표시 — SQL 이 거른다
        c.execute(ev, (T3, "20250101", "KQ", 100.0, 10.0, 0.1, "split", "confirmed"))                           # 52주 창 앞 — SQL 이 거른다
        c.execute(ev, (T1, W_FIRST, "KS", 9_000.0, 10_000.0, 1.1111, None, "unlabeled"))                        # 창 첫 보관일 — 그 앞 보관 행이 창에 없다
        for sid, src, name, kind, in_db in ((1, "P", "코스피", "z", True), (36, "P", "코스피 200 건설", "s", False)):
            c.execute("INSERT INTO krx_idx_series (sid, src, name, kind, in_db, first_dd, last_dd) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                      (sid, src, name, kind, in_db, "2015-12-30", "2026-10-21"))
        for dd, v in _KOSPI:
            c.execute("INSERT INTO krx_idx (sid, trd_dd, close_x100) VALUES (1, %s, %s)", (dd, v))
        for dd, v in (("2025-10-21", 2.95), ("2025-12-30", 3.05), ("2026-10-14", 3.7), ("2026-10-20", 3.692), ("2026-10-21", 3.688)):
            c.execute("INSERT INTO ecos_rate (series, d, rate) VALUES ('1', %s, %s)", (dd, v))
    yield {"url": url}
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{dbname}" WITH (FORCE)')


@pytest.fixture
def use_db(world, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", world["url"])
    monkeypatch.setattr(dbm, "_pool", None)
    monkeypatch.setattr(dbm, "_pool_disabled_until", float("inf"))        # 풀 없이 직접 접속

    async def names():
        return {T1: "정상주", T2: "분할주", T3: "스팩"}
    monkeypatch.setattr(uni, "_names_by_ticker", names)


# ── S1 지수 ──────────────────────────────────────────────────────────────────

def test_index_reads_the_real_close_and_returns(world, use_db):
    p = asyncio.run(ts.build_index_payload("코스피"))
    r = p["data"]["indices"][0]
    assert p["status"] == "ok" and r["close"] == 5735.66 and p["data"]["as_of"] == "20261021"
    assert r["prev_dd"] == "20261020" and r["change"] == 182.92
    assert r["periods"]["1y"]["base_dd"] == "20251021" and r["periods"]["ytd"]["base_dd"] == "20251230"
    old = asyncio.run(ts.build_index_payload("코스피", as_of="20261015"))
    assert old["data"]["as_of"] == "20261014" and old["data"]["indices"][0]["close"] == 6008.81        # 그 이하 가장 늦은 보관일
    nodb = asyncio.run(ts.build_index_payload("코스피 200 건설"))
    assert nodb["status"] == "no_data" and "DB 에 일별 종가가 없습니다" in nodb["warnings"][0]


# ── S2 금리 ──────────────────────────────────────────────────────────────────

def test_rate_reads_latest_and_default_year(world, use_db):
    p = asyncio.run(ts.build_rate_payload())
    d = p["data"]
    assert p["status"] == "ok" and d["latest"] == {"asof": "20261021", "rate": 3.688}
    assert d["range"]["from"] == "20251021" and d["range"]["n"] == 5
    ch = {c["key"]: c for c in d["changes"]}
    assert ch["ytd"]["base_dd"] == "20251230" and ch["ytd"]["change_bp"] == 63.8 and ch["1w"]["base_dd"] == "20261014"
    since = asyncio.run(ts.build_rate_payload(since="20251230"))
    assert since["data"]["range"]["from"] == "20251230" and since["data"]["range"]["n"] == 4
    assert asyncio.run(ts.build_rate_payload(as_of="20261015"))["data"]["latest"]["asof"] == "20261014"


# ── S3·S4 순위표 52주 ────────────────────────────────────────────────────────

def _rank(**kw):
    return asyncio.run(ts.build_quote_rank_payload(universe="전체", **kw))


def test_week52_window_zero_days_and_new_high(world, use_db):
    p = _rank()
    assert p["status"] == "ok" and p["data"]["as_of"] == LAST and p["data"]["n"] == 3
    a = next(r for r in p["data"]["rows"] if r["ticker"] == T1)
    assert (a["high52_krw"], a["low52_krw"]) == (15_000, 9_000)               # 창 앞 99,999·1 과 거래 없는 날 0 을 세지 않는다
    assert a["from_high52_pct"] == 0.0 and a["from_low52_pct"] == 66.67 and a["new_high52"] is True
    assert a["window_days"] == 5 and a["week52_blocked"] is False             # 창 첫 보관일의 이벤트(20251022)는 막지 않는다
    assert a["change_pct"] == 3.45                                            # 공표 cmp 500 ÷ 기준가(15,000−500)
    assert p["data"]["window52"] == {"from": W_FIRST, "to": LAST, "days": 364, "stored_from": OLD, "covers_full": True}


def test_split_stock_is_blank_and_the_old_rule_would_say_ten_times(world, use_db):
    with psycopg.connect(world["url"]) as c:
        naive = c.execute("SELECT max(high), max(close) FILTER (WHERE trd_dd = %s) FROM krx_daily_v WHERE ticker = %s AND trd_dd >= %s",
                          (LAST, T2, W_FIRST)).fetchone()
    assert naive == (200_000, 20_000)                 # 지금 규칙(원 시세 최댓값)은 52주 고가가 현재가의 10배 → 대비 −90%
    p = _rank()
    s = next(r for r in p["data"]["rows"] if r["ticker"] == T2)
    assert s["week52_blocked"] is True and s["high52_krw"] is None and s["from_high52_pct"] is None and s["new_high52"] is None
    assert p["data"]["week52_blocked_n"] == 1


def test_tick_rounding_labeled_and_unlabeled_and_out_of_window_events_do_not_block(world, use_db):
    p = _rank()
    t = next(r for r in p["data"]["rows"] if r["ticker"] == T3)
    assert t["week52_blocked"] is False and (t["high52_krw"], t["low52_krw"]) == (2_100, 2_040)
    assert t["from_high52_pct"] == -0.71


def test_ranking_sorts_by_value_and_volume(world, use_db):
    assert [r["ticker"] for r in _rank(sort="거래대금")["data"]["rows"]] == [T1, T2, T3]       # 15,000,000 · 4,000,000 · 208,500
    assert [r["ticker"] for r in _rank(sort="거래량")["data"]["rows"]] == [T1, T2, T3]         # 1,000 · 200 · 100
    assert [r["ticker"] for r in _rank(sort="등락률")["data"]["rows"]][0] == T1
    outside = _rank(as_of="20250101")
    assert outside["status"] == "invalid" and "보관 창(20251014~20261021) 밖" in outside["warnings"][0]
    past = _rank(as_of=LAST_BUT_ONE)
    assert past["data"]["as_of"] == LAST_BUT_ONE and next(r for r in past["data"]["rows"] if r["ticker"] == T1)["close_krw"] == 14_500
