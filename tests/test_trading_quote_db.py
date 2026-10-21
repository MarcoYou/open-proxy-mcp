"""trading_data(scope=quote) — 최근 약 1년은 DB, 그 이전은 KRX 라이브(날짜별 LRU) · 요청 경로는 KRX 를 부르지 않는다.

네트워크 0 · 운영 DB 0 — `read_quote`·KRX 호출을 가짜로 바꿔 끼워 갈림길과 호출 수를 검사한다.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
from decimal import Decimal

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from open_proxy_mcp import krx_daily  # noqa: E402
from open_proxy_mcp.krx_daily import QuoteLookup  # noqa: E402
from open_proxy_mcp.services import price_multiple_data as pmd  # noqa: E402
from open_proxy_mcp.services import trading as T  # noqa: E402


def _no_krx(*a, **k):
    raise AssertionError("KRX 를 부르면 안 되는 경로다")


async def _no_krx_async(*a, **k):
    raise AssertionError("KRX 를 부르면 안 되는 경로다")


def _corp(monkeypatch, ticker="005930", name="삼성전자", cls="Y"):
    async def fake(q):
        return {"corp_name": name, "stock_code": ticker, "corp_cls": cls}, None
    monkeypatch.setattr(T, "_resolve_listed", fake)


def _db_row(**kw):
    row = {"market": "KS", "open": 70_500, "high": 71_500, "low": 70_000, "close": 71_000, "volume": 12_345_678,
           "value": 876_543_210_000, "list_shrs": 5_919_637_922, "mktcap": 420_294_092_462_000,
           "prev_dd": "20261015", "prev_close": 70_000, "chg": 1_000, "chg_pct": Decimal("1.43")}
    row.update(kw)
    return row


def _krx_row(**kw):
    row = {"ISU_CD": "005930", "MKT_NM": "KOSPI", "TDD_CLSPRC": "71,000", "TDD_OPNPRC": "70,500", "TDD_HGPRC": "71,500",
           "TDD_LWPRC": "70,000", "CMPPREVDD_PRC": "1,000", "ACC_TRDVOL": "12,345,678", "ACC_TRDVAL": "876,543,210,000",
           "MKTCAP": "420,294,092,462,000", "LIST_SHRS": "5,919,637,922", "FLUC_RT": "1.43"}
    row.update(kw)
    return row


def _lookup(row, day="20261016", present=True, window=("20261008", "20261016")):
    return QuoteLookup(ok=True, day=day, window=window, day_present=present, row=row)


def _quote(monkeypatch, lookup, *, as_of="", read_calls=None, **corp):
    _corp(monkeypatch, **corp)

    def fake_read(ticker, dd=""):
        if read_calls is not None:
            read_calls.append((ticker, dd))
        return lookup
    monkeypatch.setattr(T, "read_quote", fake_read)
    return asyncio.run(T.build_quote_payload("삼성전자", as_of=as_of))


# ── DB 경로 ─────────────────────────────────────────────────────────────────

def test_recent_day_is_answered_from_db_with_zero_krx_calls(monkeypatch):
    monkeypatch.setattr(T, "_krx_quote_row", _no_krx_async)
    monkeypatch.setattr(pmd, "_fetch_live_snapshot", _no_krx_async)
    calls = []
    p = _quote(monkeypatch, _lookup(_db_row()), as_of="20261016", read_calls=calls)
    assert p["status"] == "ok" and calls == [("005930", "20261016")]           # DB 한 왕복
    d = p["data"]
    assert d["source"] == "db" and d["scope"] == "quote" and d["as_of"] == "20261016" and d["market"] == "KOSPI"
    assert d["quote"] == {"close_krw": 71_000, "open_krw": 70_500, "high_krw": 71_500, "low_krw": 70_000,
                          "change_krw": 1_000, "volume": 12_345_678, "value_krw": 876_543_210_000,
                          "mktcap_krw": 420_294_092_462_000, "list_shrs": 5_919_637_922, "change_pct": 1.43}
    assert isinstance(d["quote"]["change_pct"], float)                       # Decimal 이 JSON 으로 새지 않는다
    assert d["price_adjusted"] is False and p["warnings"] == []
    assert "최근 약 1년" in d["method"] and "실제 체결가" in d["method"] and "KRX 공표값" in d["method"]


def test_db_and_live_payloads_have_the_same_shape_and_values():
    """같은 하루를 DB 행과 KRX 원본 행으로 만든 시세는 키·순서·값이 같다 — 사용자가 보는 출력은 갈림길을 모른다."""
    q_live, m_live = T._quote_from_krx_row(_krx_row())
    q_db, m_db = T._quote_from_db_row(_db_row())
    assert list(q_live) == list(q_db)
    assert q_live == q_db and m_live == m_db == "KOSPI"


def test_empty_as_of_uses_the_latest_stored_day(monkeypatch):
    monkeypatch.setattr(T, "_krx_quote_row", _no_krx_async)
    monkeypatch.setattr(pmd, "_fetch_live_snapshot", _no_krx_async)
    calls = []
    p = _quote(monkeypatch, _lookup(_db_row(prev_dd="20261014"), day="20261015"), as_of="", read_calls=calls)
    assert calls == [("005930", "")] and p["data"]["as_of"] == "20261015" and "20261015" in p["subject"]


def test_kosdaq_label_and_zero_volume_warning_survive_the_db_path(monkeypatch):
    monkeypatch.setattr(T, "_krx_quote_row", _no_krx_async)
    p = _quote(monkeypatch, _lookup(_db_row(market="KQ", volume=0, open=0, high=0, low=0, chg=0, chg_pct=Decimal("0.00"))),
               as_of="20261016", ticker="247540", name="에코프로비엠")
    assert p["data"]["market"] == "KOSDAQ" and any("거래량 0" in w for w in p["warnings"])


def test_day_stored_but_ticker_missing_answers_no_data_without_krx(monkeypatch):
    monkeypatch.setattr(T, "_krx_quote_row", _no_krx_async)
    monkeypatch.setattr(pmd, "_fetch_live_snapshot", _no_krx_async)
    p = _quote(monkeypatch, _lookup(None, day="20261014"), as_of="20261014")
    assert p["status"] == "no_data" and "20261014" in p["warnings"][0]


def test_konex_ticker_is_reported_as_unsupported_not_as_a_holiday(monkeypatch):
    monkeypatch.setattr(T, "_krx_quote_row", _no_krx_async)
    p = _quote(monkeypatch, _lookup(None), as_of="20261014", cls="N")
    assert p["status"] == "unsupported_market"


# ── 등락률을 DB 만으로 못 낼 때(보관 창의 첫날 · 신규상장일) ───────────────────────────

def test_oldest_stored_day_takes_the_official_change_from_krx(monkeypatch):
    seen = []

    async def live(dd, ticker):
        seen.append((dd, ticker))
        return _krx_row(CMPPREVDD_PRC="-500", FLUC_RT="-0.71")
    monkeypatch.setattr(T, "_krx_quote_row", live)
    p = _quote(monkeypatch, _lookup(_db_row(prev_dd=None, prev_close=None, chg=None, chg_pct=None), day="20261008"), as_of="20261008")
    assert seen == [("20261008", "005930")] and p["data"]["source"] == "krx_live"
    assert p["data"]["quote"]["change_krw"] == -500 and p["data"]["quote"]["change_pct"] == -0.71


def test_oldest_stored_day_without_krx_serves_db_values_and_says_change_is_missing(monkeypatch):
    async def none(dd, ticker):
        return None
    monkeypatch.setattr(T, "_krx_quote_row", none)
    p = _quote(monkeypatch, _lookup(_db_row(prev_dd=None, prev_close=None, chg=None, chg_pct=None), day="20261008"), as_of="20261008")
    q = p["data"]["quote"]
    assert p["status"] == "ok" and p["data"]["source"] == "db"
    assert q["close_krw"] == 71_000 and q["change_krw"] is None and q["change_pct"] is None
    assert any("등락률" in w for w in p["warnings"])


# ── 라이브로 가는 길 ────────────────────────────────────────────────────────

def test_date_older_than_the_window_goes_live(monkeypatch):
    seen = []

    async def live(dd, ticker):
        seen.append((dd, ticker))
        return _krx_row()
    monkeypatch.setattr(T, "_krx_quote_row", live)
    old = QuoteLookup(ok=True, day="20260115", window=("20261008", "20261016"), day_present=False, row=None)
    p = _quote(monkeypatch, old, as_of="20260115")
    assert seen == [("20260115", "005930")] and p["data"]["source"] == "krx_live" and p["data"]["as_of"] == "20260115"
    assert p["data"]["quote"]["close_krw"] == 71_000 and "7거래일" not in p["data"]["method"]


def test_holiday_or_gap_inside_the_window_also_goes_live(monkeypatch):
    """창 안이지만 보관된 적 없는 날(휴장일·서버가 쉰 날) — DB 가 「없다」고 단정하지 않고 KRX 에 확인한다."""
    seen = []

    async def live(dd, ticker):
        seen.append(dd)
        return None
    monkeypatch.setattr(T, "_krx_quote_row", live)
    gap = QuoteLookup(ok=True, day="20261012", window=("20261008", "20261016"), day_present=False, row=None)
    p = _quote(monkeypatch, gap, as_of="20261012")
    assert seen == ["20261012"] and p["status"] == "no_data"


def _cal(monkeypatch, closed, prev_dd="20261008", next_dd="20261012", calls=None):
    def fake(dd):
        if calls is not None:
            calls.append(dd)
        return (closed, prev_dd, next_dd)
    monkeypatch.setattr(T, "_calendar_day", fake)


def test_holiday_marked_in_the_calendar_is_answered_without_krx(monkeypatch):
    """한글날(20261009, 금) — 달력에 휴장으로 적힌 날은 KRX 를 부르지 않고 「휴장일」 + 앞뒤 거래일로 답한다.

    261019 실측: 예전엔 매번 KRX 2콜(빈 응답은 캐시 안 함) 뒤 「휴장일·상장 전·거래정지 또는 KRX 키 미설정」 일반 문구였다."""
    monkeypatch.setattr(T, "_krx_quote_row", _no_krx_async)
    cal_calls = []
    _cal(monkeypatch, True, calls=cal_calls)
    hol = QuoteLookup(ok=True, day="20261009", window=("20251010", "20261016"), day_present=False, row=None)
    p = _quote(monkeypatch, hol, as_of="20261009")
    assert cal_calls == ["20261009"]
    assert p["status"] == "no_data"
    assert p["data"]["market_closed"] is True and p["data"]["as_of"] == "20261009"
    assert p["data"]["prev_trading_day"] == "20261008" and p["data"]["next_trading_day"] == "20261012"
    msg = p["warnings"][-1]
    assert "20261009(금) 은 휴장일" in msg and "휴장으로 적힌 평일" in msg and "직전 거래일 20261008" in msg
    assert "KRX 키 미설정" not in msg


def test_weekend_inside_the_window_is_answered_without_krx(monkeypatch):
    monkeypatch.setattr(T, "_krx_quote_row", _no_krx_async)
    _cal(monkeypatch, False, prev_dd="20261016", next_dd=None)
    sat = QuoteLookup(ok=True, day="20261017", window=("20251010", "20261016"), day_present=False, row=None)
    # 20261017(토)은 창 끝(20261016) 뒤라 지름길 밖 — 지금처럼 KRX 로 확인
    seen = []

    async def live(dd, ticker):
        seen.append(dd)
        return None
    monkeypatch.setattr(T, "_krx_quote_row", live)
    p = _quote(monkeypatch, sat, as_of="20261017")
    assert seen == ["20261017"] and "market_closed" not in (p.get("data") or {})
    # 창 안의 주말(20261011 일)은 0콜
    monkeypatch.setattr(T, "_krx_quote_row", _no_krx_async)
    _cal(monkeypatch, False)
    sun = QuoteLookup(ok=True, day="20261011", window=("20251010", "20261016"), day_present=False, row=None)
    p = _quote(monkeypatch, sun, as_of="20261011")
    assert p["status"] == "no_data" and p["data"]["market_closed"] is True
    assert "20261011(일) 은 휴장일" in p["warnings"][-1] and "주말" in p["warnings"][-1]


def test_unmarked_weekday_gap_and_unreadable_calendar_still_go_live(monkeypatch):
    seen = []

    async def live(dd, ticker):
        seen.append(dd)
        return None
    monkeypatch.setattr(T, "_krx_quote_row", live)
    gap = QuoteLookup(ok=True, day="20261012", window=("20251010", "20261016"), day_present=False, row=None)
    _cal(monkeypatch, False)                                   # 달력에 없는 평일(서버가 쉰 날)
    assert _quote(monkeypatch, gap, as_of="20261012")["status"] == "no_data"
    monkeypatch.setattr(T, "_calendar_day", lambda dd: None)   # 달력을 못 읽음
    hol = QuoteLookup(ok=True, day="20261009", window=("20251010", "20261016"), day_present=False, row=None)
    assert _quote(monkeypatch, hol, as_of="20261009")["status"] == "no_data"
    assert seen == ["20261012", "20261009"]


def test_konex_and_impossible_dates_skip_the_calendar_shortcut(monkeypatch):
    monkeypatch.setattr(T, "_calendar_day", _no_krx)
    async def none(dd, ticker):
        return None
    monkeypatch.setattr(T, "_krx_quote_row", none)
    hol = QuoteLookup(ok=True, day="20261009", window=("20251010", "20261016"), day_present=False, row=None)
    assert _quote(monkeypatch, hol, as_of="20261009", cls="N")["status"] == "unsupported_market"
    bad = QuoteLookup(ok=True, day="20260231", window=("20251010", "20261016"), day_present=False, row=None)
    assert _quote(monkeypatch, bad, as_of="20260231")["status"] == "no_data"


def test_calendar_sql_reads_closed_flag_and_neighbours_in_one_statement(monkeypatch):
    got = []

    def fake_rows(sql, params=()):
        got.append((sql, params))
        return [(True, "20261008", "20261012")]
    monkeypatch.setattr(T, "_pg_rows", fake_rows)
    assert T._calendar_day("20261009") == (True, "20261008", "20261012")
    sql, params = got[0]
    assert sql.count(";") == 0 and "krx_day" in sql and "closed" in sql and params == ("20261009",) * 3
    monkeypatch.setattr(T, "_pg_rows", lambda sql, params=(): None)
    assert T._calendar_day("20261009") is None


def test_db_unreadable_falls_back_to_live_for_both_forms(monkeypatch):
    down = QuoteLookup(ok=False)
    seen = []

    async def live(dd, ticker):
        seen.append(("row", dd))
        return _krx_row()

    async def snap():
        seen.append(("snapshot",))
        return "20261016", {"005930": _krx_row()}
    monkeypatch.setattr(T, "_krx_quote_row", live)
    monkeypatch.setattr(pmd, "_fetch_live_snapshot", snap)
    p1 = _quote(monkeypatch, down, as_of="20261014")
    p2 = _quote(monkeypatch, down, as_of="")
    assert seen == [("row", "20261014"), ("snapshot",)]
    assert p1["data"]["source"] == p2["data"]["source"] == "krx_live" and p2["data"]["as_of"] == "20261016"


def test_empty_db_table_and_no_as_of_goes_live(monkeypatch):
    seen = []

    async def snap():
        seen.append(1)
        return "20261016", {"005930": _krx_row()}
    monkeypatch.setattr(pmd, "_fetch_live_snapshot", snap)
    empty = QuoteLookup(ok=True, day=None, window=(None, None), day_present=False, row=None)
    p = _quote(monkeypatch, empty, as_of="")
    assert seen == [1] and p["data"]["source"] == "krx_live"


def test_input_validation_still_precedes_any_lookup(monkeypatch):
    monkeypatch.setattr(T, "read_quote", _no_krx)
    assert asyncio.run(T.build_quote_payload("", as_of=""))["status"] == "invalid"
    assert asyncio.run(T.build_quote_payload("삼성전자", as_of="2026-1-1"))["status"] == "invalid"


# ── T6: 과거일 전종목 응답을 날짜별 LRU(2일)에 보관 ──────────────────────────────

@pytest.fixture
def fake_krx(monkeypatch):
    """KRX 호출을 세는 가짜. routes[날짜] = (코스피 행들, 코스닥 행들)."""
    import httpx
    from open_proxy_mcp.dart import krx_meter
    calls = []
    routes = {}

    class Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, params=None):
            dd, which = params["basDd"], url.rsplit("/", 1)[-1]
            calls.append((dd, which))
            kospi, kosdaq = routes.get(dd, ([], []))

            class R:
                status_code = 200

                def json(_):
                    return {"OutBlock_1": kospi if which == "stk_bydd_trd" else kosdaq}
            return R()
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    monkeypatch.setattr(krx_meter, "bump", lambda n=1: None)
    monkeypatch.setenv("KRX_API_KEY", "test-key")
    T._QUOTE_DAYS.clear()
    yield calls, routes
    T._QUOTE_DAYS.clear()


def _day_rows(prefix, n, **extra):
    return [{**_krx_row(ISU_CD=f"{prefix}{i:05d}"), **extra} for i in range(n)]


def test_full_market_response_is_kept_so_a_session_over_many_tickers_costs_two_calls(fake_krx):
    calls, routes = fake_krx
    routes["20200102"] = (_day_rows("K", 5), _day_rows("Q", 5))

    async def sweep():
        return [await T._krx_quote_row("20200102", t) for t in ("K00000", "K00003", "Q00004", "Q00001", "K00002", "X99999")]
    rows = asyncio.run(sweep())
    assert len(calls) == 2                                         # 650종목을 훑어도 KRX 는 날짜당 2콜(코스피·코스닥)
    assert [r["ISU_CD"] if r else None for r in rows] == ["K00000", "K00003", "Q00004", "Q00001", "K00002", None]


def test_day_lru_holds_two_dates_and_evicts_the_least_recently_used(fake_krx):
    calls, routes = fake_krx
    for dd in ("20200102", "20200103", "20200106"):
        routes[dd] = (_day_rows("K", 3), _day_rows("Q", 3))

    async def go():
        await T._krx_quote_row("20200102", "K00000")               # d1 적재
        await T._krx_quote_row("20200103", "K00000")               # d2 적재
        n_after_two = len(calls)
        await T._krx_quote_row("20200102", "K00001")               # d1 사용 → d1 이 최근, d2 가 가장 오래 안 쓴 날짜
        await T._krx_quote_row("20200106", "K00000")               # d3 적재 → d2 가 밀려난다
        return n_after_two
    n_after_two = asyncio.run(go())
    assert n_after_two == 4 and len(calls) == 6                    # d1 2콜 + d2 2콜, d1 재사용 0콜, d3 2콜
    assert list(T._QUOTE_DAYS) == ["20200102", "20200106"] and len(T._QUOTE_DAYS) <= T._QUOTE_DAYS_MAX == 2

    async def again():
        await T._krx_quote_row("20200103", "K00000")               # 밀려난 d2 는 다시 받아야 한다
    asyncio.run(again())
    assert len(calls) == 8


def test_empty_or_half_responses_are_never_cached(fake_krx):
    calls, routes = fake_krx
    routes["20200107"] = ([], [])                                  # 상류가 이미 게시한 날을 일시적으로 0행으로 줌
    routes["20200108"] = (_day_rows("K", 3), [])                   # 코스닥만 비어 옴

    async def go():
        assert await T._krx_quote_row("20200107", "K00000") is None
        assert await T._krx_quote_row("20200107", "K00000") is None
        half = await T._krx_quote_row("20200108", "K00001")        # 있는 시장의 종목은 이번 응답으로 답한다
        assert half["ISU_CD"] == "K00001"
        assert await T._krx_quote_row("20200108", "Q00001") is None
    asyncio.run(go())
    assert not T._QUOTE_DAYS                                        # 비었거나 반쪽인 날짜는 담기지 않았다 → 복구되면 다음 질문이 다시 받는다
    assert len(calls) == 8                                         # 4번 호출 × 2콜 (캐시가 없으니 매번 다시)


def test_day_lru_stores_only_the_fields_the_quote_needs_and_leaves_the_snapshot_cache_alone(fake_krx):
    calls, routes = fake_krx
    fat = {"SECT_TP_NM": "x" * 200, "MKT_NM": "KOSPI", "BAS_PRC": "1", "ACC_TRDVAL": "5", "TDD_CLSPRC": "7", "ISU_NM": "n" * 100}
    routes["20200109"] = ([{"ISU_CD": "K00000", **fat}], [{"ISU_CD": "Q00000", **fat}])
    asyncio.run(T._krx_quote_row("20200109", "K00000"))
    kept = T._QUOTE_DAYS["20200109"]["K00000"]
    assert set(kept) == set(T._QUOTE_KEEP_KEYS) and "SECT_TP_NM" not in kept and "ISU_NM" not in kept
    assert T._QUOTE_KEEP_KEYS and {src for src, _, _ in T._QUOTE_FIELDS} <= set(T._QUOTE_KEEP_KEYS)
    assert pmd._KRX_CACHE.get("20200109") is None                  # 배수 산출용 오늘 스냅샷 캐시를 건드리지 않는다
    assert T._QUOTE_DAYS is not pmd._KRX_CACHE


def test_shared_snapshot_cache_hit_costs_zero_calls(fake_krx):
    calls, routes = fake_krx
    pmd._KRX_CACHE.put("20200110", {"K00000": _krx_row(ISU_CD="K00000")})
    try:
        row = asyncio.run(T._krx_quote_row("20200110", "K00000"))
        assert row["ISU_CD"] == "K00000" and calls == []
    finally:
        pmd._KRX_CACHE._entries.pop("20200110", None)


def test_unbudgeted_cache_registry_tracks_the_day_lru():
    from open_proxy_mcp.dart.client import _UNBUDGETED_CACHES
    getters = dict(_UNBUDGETED_CACHES)
    assert "trading_quote" in getters and getters["trading_quote"]() is T._QUOTE_DAYS


# ── 요청 경로는 KRX 를 부르지도, 갱신을 떠안지도 않는다 ────────────────────────────

def test_market_for_reads_db_state_only_and_never_triggers_the_refresh(monkeypatch):
    pmd._serving_dd_invalidate()
    monkeypatch.setattr(pmd, "_krx_daily_latest_dd", lambda: "20261015")         # DB 는 하루 낡았다(KRX 게시 전·갱신기 대기 중)
    monkeypatch.setattr(pmd, "_krx_db_get", lambda dd, t: {"price": 71_000, "date": dd, "common_mktcap": 1, "list_shrs": 2})
    for name in ("_ensure_krx_fresh", "_fetch_live_snapshot", "_krx_market_live"):
        monkeypatch.setattr(pmd, name, _no_krx_async)
    monkeypatch.setattr(krx_daily, "upsert_day", _no_krx)                          # 기록 함수는 krx_daily 에 있다(261021) — 요청 경로는 쓰지도 않는다
    row = asyncio.run(pmd._market_for("005930"))
    assert row["price"] == 71_000 and row["date"] == "20261015"                 # 낡은 날짜를 날짜 표기와 함께 서빙할 뿐
    pmd._serving_dd_invalidate()


def test_serving_date_is_cached_for_a_minute_and_empty_is_not_cached(monkeypatch):
    pmd._serving_dd_invalidate()
    reads, clock = [], [1000.0]
    monkeypatch.setattr(pmd, "_krx_daily_latest_dd", lambda: reads.append(1) or "20261015")
    monkeypatch.setattr(pmd.time, "monotonic", lambda: clock[0])
    assert asyncio.run(pmd._serving_price_dd()) == "20261015" and len(reads) == 1
    clock[0] += 59
    assert asyncio.run(pmd._serving_price_dd()) == "20261015" and len(reads) == 1      # TTL 안 — DB 재조회 없음
    clock[0] += 2
    asyncio.run(pmd._serving_price_dd())
    assert len(reads) == 2                                                              # TTL 지남 → 새 날짜를 본다
    pmd._serving_dd_invalidate()
    asyncio.run(pmd._serving_price_dd())
    assert len(reads) == 3                                                              # 갱신기가 쓴 직후엔 즉시
    pmd._serving_dd_invalidate()
    monkeypatch.setattr(pmd, "_krx_daily_latest_dd", lambda: None)
    assert asyncio.run(pmd._serving_price_dd()) is None and pmd._SERVING_DD["dd"] is None   # 못 읽은 값은 담지 않는다
    pmd._serving_dd_invalidate()


def test_request_path_has_no_refresh_call_left():
    import inspect
    src = inspect.getsource(pmd._market_for)
    assert "_ensure_krx_fresh" not in src and "_serving_price_dd" in src
    # 배치 스크립트용 갱신 함수는 그대로 남아 있다(예비 갱신자)
    assert callable(pmd._ensure_krx_fresh)


# ── 서빙 기준일·종목 행은 일별 시세 저장분에서 (261018, 재설계 5-1) ─────────────────────────────

def _sql_spy(monkeypatch, answers):
    """`_pg_rows` 대역 — 나간 SQL(공백 정리)과 인자를 모으고, SQL 앞부분이 맞는 답을 돌려준다(없으면 None = DB 장애)."""
    seen = []

    def fake(sql, params=()):
        s = " ".join(sql.split())
        seen.append((s, tuple(params)))
        return next((v for k, v in answers.items() if s.startswith(k)), None)
    monkeypatch.setattr(pmd, "_pg_rows", fake)
    return seen


def test_serving_date_reads_krx_daily_and_never_krx_weekly(monkeypatch):
    seen = _sql_spy(monkeypatch, {"SELECT max(dd) FROM krx_day WHERE n > 0": [("20261016",)],
                                  "SELECT MAX(price_dd) FROM krx_weekly": [("20261009",)]})
    pmd._serving_dd_invalidate()
    assert asyncio.run(pmd._serving_price_dd()) == "20261016"            # krx_weekly 최신 주(지난주)가 아니라 일별 저장분의 최신일
    assert [s for s, _ in seen] == ["SELECT max(dd) FROM krx_day WHERE n > 0"]
    # 일별 저장분을 못 읽거나 비면 None — krx_weekly 로 대신하지 않는다(기준일과 행은 같은 원천에서). 호출자는 라이브로 간다
    for ans in ({}, {"SELECT max(dd) FROM krx_day WHERE n > 0": [(None,)]}):
        pmd._serving_dd_invalidate()
        seen = _sql_spy(monkeypatch, {**ans, "SELECT MAX(price_dd) FROM krx_weekly": [("20261009",)]})
        assert asyncio.run(pmd._serving_price_dd()) is None
        assert all("krx_weekly" not in s for s, _ in seen)
    pmd._serving_dd_invalidate()


def test_weekly_latest_week_is_still_krx_weekly_for_the_curve_floor_and_batches(monkeypatch):
    """`_krx_db_latest_dd` 는 닫힌 주 최신이다 — 주간 곡선 하한(`_weekly_bounds`)은 표를 자르는 쪽과 같은 정의여야 한다.
    261019(재설계 v03 C4): 달력의 가장 늦은 주 닫는 날을 먼저 읽고(뷰 위의 max 는 약 50 ms), 달력이 비면 krx_weekly 의 max 로 대신한다(한 문장)."""
    latest = " ".join(pmd._krx_daily.WEEKLY_LATEST_SQL.split())
    seen = _sql_spy(monkeypatch, {latest: [("20261009",)]})
    assert pmd._krx_db_latest_dd() == "20261009" and seen == [(latest, ())]
    assert "FROM krx_day WHERE wk_close" in latest and latest.endswith("(SELECT max(price_dd) FROM krx_weekly))")


def test_db_row_comes_from_price_latest_on_the_serving_date(monkeypatch):
    seen = _sql_spy(monkeypatch, {"SELECT close, mktcap, list_shrs, price_dd FROM price_latest":
                                  [(71_000, 420_294_092_462_000, 5_919_637_922, "20261016")]})
    row = pmd._krx_db_get("20261016", "005930")
    assert row == {"price": 71_000, "date": "20261016", "common_mktcap": 420_294_092_462_000, "list_shrs": 5_919_637_922}  # 키·값 그대로
    assert seen == [("SELECT close, mktcap, list_shrs, price_dd FROM price_latest WHERE ticker=%s AND price_dd>=%s",
                     ("005930", "20261016"))]
    # 그 종목의 최신 행이 기준일보다 이르면(보관 창 안의 상장폐지) 뷰가 행을 안 준다 → 미스 → 호출자가 라이브로 확인
    _sql_spy(monkeypatch, {"SELECT close, mktcap, list_shrs, price_dd FROM price_latest": []})
    assert pmd._krx_db_get("20261016", "469900") == {}
    _sql_spy(monkeypatch, {})                                                # DB 장애
    assert pmd._krx_db_get("20261016", "005930") == {}


def test_db_row_date_is_the_rows_own_day_when_the_cached_serving_date_is_a_day_behind(monkeypatch):
    """다른 머신의 갱신기가 새 거래일을 쓴 직후 이 머신의 기준일 캐시(60초)는 하루 전이다 — 행은 새 거래일 것이고, 날짜도 그 행의 것이다."""
    _sql_spy(monkeypatch, {"SELECT close, mktcap, list_shrs, price_dd FROM price_latest":
                           [(72_000, 426_213_930_384_000, 5_919_637_922, "20261016")]})
    row = pmd._krx_db_get("20261015", "005930")
    assert row == {"price": 72_000, "date": "20261016", "common_mktcap": 426_213_930_384_000, "list_shrs": 5_919_637_922}


def test_market_for_falls_back_to_live_not_to_krx_weekly_when_daily_is_unreadable(monkeypatch):
    seen = _sql_spy(monkeypatch, {"SELECT MAX(price_dd) FROM krx_weekly": [("20261009",)],
                                  "SELECT close, mktcap, list_shrs FROM krx_weekly": [(1, 1, 1)]})
    pmd._serving_dd_invalidate()
    live = []

    async def fake_live(dd):
        live.append(dd)
        return {"005930": _krx_row()} if dd == "20261016" else {}
    monkeypatch.setattr(pmd, "_krx_market_live", fake_live)
    monkeypatch.setattr(pmd, "_price_dates", lambda today=None: ["20261016", "20261015"])
    row = asyncio.run(pmd._market_for("005930"))
    assert row == {"price": 71_000, "date": "20261016", "common_mktcap": 420_294_092_462_000, "list_shrs": 5_919_637_922,
                   "source": "krx_live"}                                   # 261018: 라이브로 받은 값은 출처가 그렇다고 적힌다
    assert live == ["20261016"] and all("krx_weekly" not in s for s, _ in seen)
    pmd._serving_dd_invalidate()


def test_markdown_shows_a_dash_not_none_when_the_change_is_unavailable(monkeypatch):
    """보관 창의 첫날을 KRX 없이 DB 값만으로 낼 때 대비·등락률 칸이 「(None%)」로 새지 않는다."""
    from open_proxy_mcp.tools.trading import _render_quote
    async def none(dd, ticker):
        return None
    monkeypatch.setattr(T, "_krx_quote_row", none)
    p = _quote(monkeypatch, _lookup(_db_row(prev_dd=None, prev_close=None, chg=None, chg_pct=None), day="20261008"), as_of="20261008")
    md = _render_quote(p)
    assert "None" not in md and "| 대비 | - (-) |" in md
    p2 = _quote(monkeypatch, _lookup(_db_row()), as_of="20261016")
    assert "| 대비 | 1,000원 (1.43%) |" in _render_quote(p2)


# ── KRX 공표 대비를 보관한 행 (261019 재설계 v03) ─────────────────────────────────────

def test_published_change_is_used_as_is_even_where_the_stored_previous_close_cannot_be_trusted(monkeypatch):
    """보관한 공표 대비(`krx_chg`·`krx_chg_pct`)가 있으면 그것이 대비·등락률이다 — 휴장 다음 날(직전 평일 행 없음)·분할일(±30% 밖)에도
    KRX 를 부르지 않는다(종전에는 날짜당 2콜로 공표값을 받았다). KRX 라이브 경로와 같은 값이다."""
    monkeypatch.setattr(T, "_krx_quote_row", _no_krx_async)
    for kw in (dict(prev_dd="20261014"),                                              # 15일이 휴장이었다
               dict(prev_close=500_000, chg=-429_000, chg_pct=Decimal("-85.80"))):     # 분할일 — 직전 종가 대비는 뜻이 없다
        p = _quote(monkeypatch, _lookup(_db_row(krx_chg=1_000, krx_chg_pct=Decimal("1.43"), **kw)), as_of="20261016")
        q = p["data"]["quote"]
        assert p["data"]["source"] == "db" and (q["change_krw"], q["change_pct"]) == (1_000, 1.43) and p["warnings"] == []
    q_db, _ = T._quote_from_db_row(_db_row(chg=7, chg_pct=Decimal("0.01"), krx_chg=1_000, krx_chg_pct=Decimal("1.43")))
    q_live, _ = T._quote_from_krx_row(_krx_row())
    assert q_db == q_live and isinstance(q_db["change_pct"], float)


def test_rows_without_a_published_change_keep_the_old_reliability_rule(monkeypatch):
    """공표 대비가 비어 있는 행(교체 때 옛 표에서 옮겨 온 날)은 종전 규칙 그대로 — 믿을 수 없으면 KRX 공표값을 받는다."""
    seen = []

    async def live(dd, ticker):
        seen.append(dd)
        return _krx_row()
    monkeypatch.setattr(T, "_krx_quote_row", live)
    p = _quote(monkeypatch, _lookup(_db_row(prev_dd="20261014", krx_chg=None, krx_chg_pct=None)), as_of="20261016")
    assert seen == ["20261016"] and p["data"]["source"] == "krx_live"


# ── 대비·등락률을 믿을 수 있나 (`_change_is_reliable`) ───────────────────────────────

@pytest.mark.parametrize("dd,row,expected", [
    ("20261016", dict(prev_dd="20261015", prev_close=70_000, chg_pct=Decimal("1.43")), True),          # 목 → 금
    ("20261019", dict(prev_dd="20261016", prev_close=70_000, chg_pct=Decimal("-2.00")), True),         # 금 → 월(주말 건너뜀)
    ("20261016", dict(prev_dd="20261014", prev_close=70_000, chg_pct=Decimal("1.43")), False),         # 직전 평일(15일) 행이 없다: 휴장일일 수도, 빠진 날일 수도
    ("20261016", dict(prev_dd=None, prev_close=None, chg_pct=None), False),                            # 직전 행 없음(보관 첫날·신규상장일)
    ("20261016", dict(prev_dd="20261015", prev_close=0, chg_pct=None), False),                         # 직전 종가 0
    ("20261016", dict(prev_dd="20261015", prev_close=500_000, chg_pct=Decimal("-79.80")), False),     # 5:1 분할 — 일일 제한폭(±30%) 밖
    ("20261016", dict(prev_dd="20261015", prev_close=70_000, chg_pct=Decimal("30.00")), True),         # 상·하한가 경계는 정상 변동
    ("20261016", dict(prev_dd="20261015", prev_close=70_000, chg_pct=Decimal("-30.01")), False),
])
def test_change_reliability_rules(dd, row, expected):
    assert T._change_is_reliable(dd, row) is expected


def test_unreliable_change_goes_to_krx_official_value_and_blanks_when_krx_is_unavailable(monkeypatch):
    seen = []

    async def live(dd, ticker):
        seen.append(dd)
        return _krx_row(CMPPREVDD_PRC="1,000", FLUC_RT="1.00", TDD_CLSPRC="101,000")
    monkeypatch.setattr(T, "_krx_quote_row", live)
    split = _lookup(_db_row(prev_close=500_000, chg=-399_000, chg_pct=Decimal("-79.80"), close=101_000))
    p = _quote(monkeypatch, split, as_of="20261016")
    assert seen == ["20261016"] and p["data"]["source"] == "krx_live" and p["data"]["quote"]["change_pct"] == 1.0

    async def none(dd, ticker):
        return None
    monkeypatch.setattr(T, "_krx_quote_row", none)
    p2 = _quote(monkeypatch, split, as_of="20261016")
    q = p2["data"]["quote"]
    assert p2["data"]["source"] == "db" and q["close_krw"] == 101_000 and q["change_pct"] is None and q["change_krw"] is None
    assert any("대비·등락률은 비웠습니다" in w for w in p2["warnings"])


def test_gap_before_the_day_goes_to_krx_official_value(monkeypatch):
    seen = []

    async def live(dd, ticker):
        seen.append(dd)
        return _krx_row()
    monkeypatch.setattr(T, "_krx_quote_row", live)
    gap = _lookup(_db_row(prev_dd="20261012"), day="20261014")                    # 13일(화) 행이 없는 채 12일 대비 — 이틀 변동
    p = _quote(monkeypatch, gap, as_of="20261014")
    assert seen == ["20261014"] and p["data"]["source"] == "krx_live"


def test_db_lookups_run_off_the_event_loop(monkeypatch):
    """quote 의 DB 한 왕복과 요청 경로의 기준일 조회는 루프 밖 스레드에서 돈다."""
    import threading
    _corp(monkeypatch)
    seen = {}

    def fake_read(ticker, dd=""):
        seen["read_quote"] = threading.get_ident()
        return _lookup(_db_row())
    monkeypatch.setattr(T, "read_quote", fake_read)
    monkeypatch.setattr(pmd, "_krx_daily_latest_dd", lambda: seen.__setitem__("latest_dd", threading.get_ident()) or "20261016")
    pmd._serving_dd_invalidate()

    async def go():
        tid = threading.get_ident()
        await T.build_quote_payload("삼성전자", as_of="20261016")
        await pmd._serving_price_dd()
        return tid
    loop_tid = asyncio.run(go())
    pmd._serving_dd_invalidate()
    assert set(seen) == {"read_quote", "latest_dd"} and loop_tid not in seen.values()


# ── KRX 호출 실패는 「그 날 시세 없음」이 아니다 (261021, DATA-PX8) ─────────────────────────────

@pytest.fixture
def failing_krx(monkeypatch):
    """KRX 호출을 세고, `mode` 로 실패를 흉내 낸다. mode = {"status": 401|429|503} 이면 그 HTTP 상태, {"raise": 예외} 면 전송 오류, 비면 정상 응답."""
    import httpx
    from open_proxy_mcp.dart import krx_meter
    calls, mode, routes = [], {}, {}

    class Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, params=None):
            dd, which = params["basDd"], url.rsplit("/", 1)[-1]
            calls.append((dd, which))
            if mode.get("raise"):
                raise mode["raise"]
            kospi, kosdaq = routes.get(dd, ([], []))

            class R:
                status_code = mode.get("status", 200)

                def json(_):
                    return {"OutBlock_1": kospi if which == "stk_bydd_trd" else kosdaq}
            return R()
    monkeypatch.setattr(httpx, "AsyncClient", Client)
    monkeypatch.setattr(krx_meter, "bump", lambda n=1: None)
    monkeypatch.setenv("KRX_API_KEY", "test-key")
    T._QUOTE_DAYS.clear()
    yield calls, mode, routes
    T._QUOTE_DAYS.clear()


@pytest.mark.parametrize("mode,status", [({"status": 401}, "auth"), ({"status": 429}, "limit"), ({"status": 503}, "http_error"),
                                         ({"raise": OSError("timeout")}, "transport")])
def test_a_krx_failure_raises_instead_of_returning_a_missing_row_and_is_never_cached(failing_krx, mode, status):
    calls, m, routes = failing_krx
    routes["20200102"] = (_day_rows("K", 3), _day_rows("Q", 3))
    m.update(mode)

    async def go():
        with pytest.raises(T.KrxQuoteError) as ei:
            await T._krx_quote_row("20200102", "K00000")
        assert ei.value.status == status
        assert not T._QUOTE_DAYS                                   # 실패는 담기지 않는다
        m.clear()                                                  # 복구
        row = await T._krx_quote_row("20200102", "K00000")
        assert row["ISU_CD"] == "K00000"                           # 같은 날짜를 다시 받는다 — 실패가 「없음」으로 굳지 않았다
    asyncio.run(go())


def test_an_empty_answer_is_still_none_not_an_error(failing_krx):
    calls, m, routes = failing_krx
    routes["20200107"] = ([], [])                                  # 호출은 성공했고 그 날 행이 없다 — 휴장·게시 전이라는 답

    async def go():
        assert await T._krx_quote_row("20200107", "K00000") is None
    asyncio.run(go())


def test_a_good_kospi_row_is_served_even_when_kosdaq_failed_but_a_kosdaq_ticker_is_an_error(failing_krx):
    """코스피는 받았고 코스닥 호출만 실패한 반쪽 — 코스피 종목은 그대로 답하고(캐시는 안 한다), 코스닥 종목은 「없다」가 아니라 실패다."""
    calls, m, routes = failing_krx
    routes["20200108"] = (_day_rows("K", 3), _day_rows("Q", 3))
    import httpx
    real_get = httpx.AsyncClient.get

    async def get(self, url, headers=None, params=None):
        if url.endswith("ksq_bydd_trd"):
            raise OSError("timeout")
        return await real_get(self, url, headers=headers, params=params)
    httpx.AsyncClient.get = get

    async def go():
        assert (await T._krx_quote_row("20200108", "K00001"))["ISU_CD"] == "K00001"
        assert not T._QUOTE_DAYS
        with pytest.raises(T.KrxQuoteError) as ei:
            await T._krx_quote_row("20200108", "Q00001")
        assert ei.value.status == "transport"
    try:
        asyncio.run(go())
    finally:
        httpx.AsyncClient.get = real_get


def test_quote_payload_says_the_krx_call_failed_not_holiday_or_halt(monkeypatch):
    async def boom(dd, ticker):
        raise T.KrxQuoteError("limit")
    monkeypatch.setattr(T, "_krx_quote_row", boom)
    old = QuoteLookup(ok=True, day="20260115", window=("20261008", "20261016"), day_present=False, row=None)
    p = _quote(monkeypatch, old, as_of="20260115")
    assert p["status"] == "no_data" and p["data"]["krx_status"] == "limit" and p["data"]["as_of"] == "20260115"
    text = " ".join(p["warnings"])
    assert "KRX 호출 한도 초과" in text and "호출이 실패한 것" in text
    assert "휴장일·상장 전·거래정지 또는 KRX 키 미설정" not in text          # 종전 문구(실패를 자료 없음으로 읽게 하던 것)가 아니다


def test_konex_ticker_stays_unsupported_even_when_the_krx_call_fails(monkeypatch):
    """코넥스는 시세 소스(코스피·코스닥 두 endpoint)에 애초에 없다 — KRX 호출이 실패해도 답은 종전처럼 「수록 대상 아님」이다."""
    async def boom(dd, ticker):
        raise T.KrxQuoteError("transport")
    monkeypatch.setattr(T, "_krx_quote_row", boom)
    old = QuoteLookup(ok=True, day="20260115", window=("20261008", "20261016"), day_present=False, row=None)
    p = _quote(monkeypatch, old, as_of="20260115", cls="N")
    assert p["status"] == "unsupported_market" and "KONEX" in " ".join(p["warnings"])


def test_quote_payload_without_a_failure_keeps_the_old_no_data_wording(monkeypatch):
    async def none(dd, ticker):
        return None
    monkeypatch.setattr(T, "_krx_quote_row", none)
    old = QuoteLookup(ok=True, day="20260115", window=("20261008", "20261016"), day_present=False, row=None)
    p = _quote(monkeypatch, old, as_of="20260115")
    assert p["status"] == "no_data" and "krx_status" not in p.get("data", {})
    assert "휴장일·상장 전·거래정지 또는 KRX 키 미설정" in " ".join(p["warnings"])


def test_unreliable_change_names_the_krx_failure_when_it_blanks_the_change(monkeypatch):
    async def boom(dd, ticker):
        raise T.KrxQuoteError("auth")
    monkeypatch.setattr(T, "_krx_quote_row", boom)
    split = _lookup(_db_row(prev_close=500_000, chg=-399_000, chg_pct=Decimal("-79.80"), close=101_000))
    p = _quote(monkeypatch, split, as_of="20261016")
    q = p["data"]["quote"]
    assert p["status"] == "ok" and p["data"]["source"] == "db" and q["close_krw"] == 101_000 and q["change_pct"] is None
    text = " ".join(p["warnings"])
    assert "대비·등락률은 비웠습니다" in text and "KRX 인증 실패" in text


def test_quote_calendar_comments_no_longer_claim_there_is_no_calendar():
    """낡은 주석 바로잡기(261021) — `krx_day` 달력이 있다. `_prev_weekday`/`_change_is_reliable` 이 그것을 읽지 않는다는 사실로 고쳐 적었다."""
    import inspect
    src = inspect.getsource(T._change_is_reliable) + inspect.getsource(T._prev_weekday)
    assert "구분할 달력이 없다" not in src and "krx_day" in src
    assert "공용 커넥션 풀" in inspect.getsource(T.build_cap_agg_payload)            # 「호출마다 새 커넥션을 연다」는 풀 이전의 설명이었다
