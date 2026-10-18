# -*- coding: utf-8 -*-
"""krx_weekly 를 최근 3년 + 연말 주로 줄인 뒤에도 서버가 옛 연말 점을 주간 곡선에 섞지 않고,
표가 비는 순간에 완전한 시장 맥락 캐시를 잃지 않는지 고정한다. network 0 · DB 0."""
from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from open_proxy_mcp import company_resolver as CR
from open_proxy_mcp import krx_retention as R
from open_proxy_mcp.company_resolver import MarketContext
from open_proxy_mcp.services import price_multiple_data as S
from open_proxy_mcp.services import trading as T
from open_proxy_mcp.tools.price_multiple_data import _render_firm_history
from open_proxy_mcp.tools.trading import _render_firm

NOTE = "주간 시세는 최근 3년까지 제공(연말 값은 전체 기간)"


# ═══════════════════════════════════════════════════════════════════════════════
# 1. price_multiple_data — 주간 곡선 하한
# ═══════════════════════════════════════════════════════════════════════════════
def test_weekly_series_reads_only_from_the_cutoff(monkeypatch):
    seen = []

    def fake_rows(sql, params=()):
        seen.append((" ".join(sql.split()), params))
        return [("20231020", 1000.0), ("20231027", 1100.0)]

    monkeypatch.setattr(S, "_pg_rows", fake_rows)
    monkeypatch.setattr(S, "_krx_db_latest_dd", lambda: "20261016")
    fin = {2022: (100.0, 500.0)}
    # 명시한 하한
    series = asyncio.run(S._weekly_series("005930", fin=fin, finq={}, since_dd="20231016"))
    assert [p["asof"] for p in series] == ["20231020", "20231027"]
    assert "price_dd>=%s" in seen[-1][0] and seen[-1][1] == ("005930", "20231016")
    # 비우면 저장된 최신 주(20261016)의 달력 3년 전 — 표를 자르는 쪽과 같은 정의
    asyncio.run(S._weekly_series("005930", fin=fin, finq={}))
    assert seen[-1][1] == ("005930", R.krx_weekly_cutoff("20261016")) == ("005930", "20231016")


def test_weekly_bounds_use_stored_latest_week_not_today(monkeypatch):
    monkeypatch.setattr(S, "_krx_db_latest_dd", lambda: "20260227")
    assert asyncio.run(S._weekly_bounds()) == ("20260227", "20230227")
    # 표를 못 읽으면(빈 표·장애) 오늘로 대신한다 — 이후 질의가 no_data/db_error 로 갈린다
    monkeypatch.setattr(S, "_krx_db_latest_dd", lambda: None)
    latest, floor = asyncio.run(S._weekly_bounds())
    assert latest == S.today_kst().strftime("%Y%m%d") and floor == R.krx_weekly_cutoff(latest)


def _fake_hist(series, weekly_from=None):
    async def fake(company, format="md"):
        data = {"scope": "firm_history", "ticker": "005930", "market": "KS", "sector": "C26", "series": series}
        if weekly_from:
            data["weekly_from"] = weekly_from
        return {"tool": "price_multiple_data", "status": "ok", "subject": "삼성전자", "data": data}
    return fake


POINT = {"asof": "20231020", "pit_fy": 2022, "pit_q": "2023Q2", "cap_krw": 3.0e14, "per_fy0": 12.0, "pbr": 1.2,
         "per_ttm": 11.0, "pbr_mrq": 1.1}
LATEST_POINT = {**POINT, "asof": "20261016"}


def test_firm_at_before_the_cutoff_explains_instead_of_answering_with_a_stale_point(monkeypatch):
    monkeypatch.setattr(S, "build_firm_history_payload", _fake_hist([POINT, LATEST_POINT], weekly_from="20231016"))
    p = asyncio.run(S.build_firm_at_payload("삼성전자", "20200101"))
    assert p["status"] == "no_data"
    assert NOTE in p["warnings"][0] and "기준일 20200101" in p["warnings"][0] and "`scope=firm_history`" in p["warnings"][0]
    assert "2023-10-16" in p["warnings"][0]
    assert "곡선의 첫 점은 20231020" in p["warnings"][1]


def test_firm_at_derives_the_cutoff_from_the_curve_when_the_field_is_absent(monkeypatch):
    monkeypatch.setattr(S, "build_firm_history_payload", _fake_hist([POINT, LATEST_POINT]))   # weekly_from 없는 옛 응답
    p = asyncio.run(S.build_firm_at_payload("삼성전자", "20200101"))
    assert p["status"] == "no_data" and NOTE in p["warnings"][0]


def test_firm_at_between_the_cutoff_and_the_first_point_keeps_the_old_message(monkeypatch):
    monkeypatch.setattr(S, "build_firm_history_payload", _fake_hist([POINT, LATEST_POINT], weekly_from="20231016"))
    p = asyncio.run(S.build_firm_at_payload("삼성전자", "20231017"))      # 하한 안쪽인데 첫 점(20231020)보다 이르다
    assert p["status"] == "no_data" and len(p["warnings"]) == 1
    assert "곡선의 첫 점은 20231020" in p["warnings"][0] and NOTE not in p["warnings"][0]


def test_firm_at_with_an_empty_weekly_curve_does_not_print_none(monkeypatch):
    """상장폐지·거래정지로 최근 3년 안에 주간 점이 하나도 없는 종목."""
    monkeypatch.setattr(S, "build_firm_history_payload", _fake_hist([], weekly_from="20231016"))
    p = asyncio.run(S.build_firm_at_payload("삼성전자", "20240105"))
    assert p["status"] == "no_data" and "None" not in " ".join(p["warnings"])
    assert "주간 곡선이 비어 있습니다" in p["warnings"][0]


def test_firm_at_inside_the_window_is_unchanged(monkeypatch):
    monkeypatch.setattr(S, "build_firm_history_payload", _fake_hist([POINT, LATEST_POINT], weekly_from="20231016"))
    p = asyncio.run(S.build_firm_at_payload("삼성전자", "20260101"))
    assert p["status"] == "ok" and p["data"]["as_of"] == "20231020" and p["data"]["as_of_requested"] == "20260101"


def test_firm_history_passes_the_cutoff_and_says_so(monkeypatch):
    got = {}

    async def fake_resolve(q):
        return {"corp_name": "삼성전자", "stock_code": "005930", "corp_code": "00126380"}, None

    async def fake_fin_y(isu, cur="KRW", fye=12):
        return {2022: (1.0, 1.0)}

    async def fake_fin_q(isu, cur="KRW", fye=12):
        return {}

    async def fake_band(isu, cur="KRW", fin=None, fye=12):
        return [{"period": "2022", "asof": "20221229", "pit_fy": 2021, "cap_krw": 4e14, "per_fy0": 10.0,
                 "pbr_fy0": 1.0, "source": "연말(PIT FY2021)"}]

    async def fake_bounds():
        return "20261016", "20231016"

    async def fake_series(isu, cur="KRW", fin=None, finq=None, since_dd=None, fye=12, raw_out=None):
        got["since_dd"] = since_dd
        return [dict(POINT), dict(LATEST_POINT)]

    def fake_rows(sql, params=()):
        if "FROM dart_fundamentals" in sql:
            return [("KS", "C26", "KRW", None, "ok")]
        return []

    monkeypatch.setattr(S, "_resolve_listed", fake_resolve)
    monkeypatch.setattr(S, "_firm_fin_by_fy", fake_fin_y)
    monkeypatch.setattr(S, "_firm_fin_by_q", fake_fin_q)
    monkeypatch.setattr(S, "_annual_pit_band", fake_band)
    monkeypatch.setattr(S, "_weekly_bounds", fake_bounds)
    monkeypatch.setattr(S, "_weekly_series", fake_series)
    monkeypatch.setattr(S, "_pg_rows", fake_rows)
    p = asyncio.run(S.build_firm_history_payload("삼성전자", format="json"))
    assert p["status"] == "ok"
    assert got["since_dd"] == "20231016" and p["data"]["weekly_from"] == "20231016"
    assert any(NOTE in w and "연말(PIT FY" in w for w in p["warnings"])
    assert "최근 3년 주간 곡선" in p["data"]["method"] and "전구간" not in p["data"]["method"]
    # 연말 밴드(history)는 하한과 무관하게 전체 기간 — 옛 연말 값이 그대로 남는다
    assert p["data"]["history"][0]["asof"] == "20221229"
    md = _render_firm_history(p)
    assert "차트용 주간 곡선(최근 3년)" in md and "전 구간" not in md and NOTE in md and "## 연말 밴드" in md


# ═══════════════════════════════════════════════════════════════════════════════
# 2. trading_data scope=firm — 전 기간 질의
# ═══════════════════════════════════════════════════════════════════════════════
ROWS = [("20231020", "KS", 70000, 4.2e14, 5_969_782_550), ("20261016", "KS", 80000, 4.8e14, 5_969_782_550)]


def _patch_trading(monkeypatch, rows=ROWS):
    seen = {}

    async def fake_resolve(q):
        return {"corp_name": "삼성전자", "stock_code": "005930"}, None

    async def fake_bounds():
        return "20261016", "20231016"

    def fake_rows(sql, params=()):
        if "FROM krx_weekly" in sql:
            seen["sql"], seen["params"] = " ".join(sql.split()), params
            return list(rows)
        return []          # krx_adj_events

    monkeypatch.setattr(T, "_resolve_listed", fake_resolve)
    monkeypatch.setattr(T, "_weekly_bounds", fake_bounds)
    monkeypatch.setattr(T, "_pg_rows", fake_rows)
    return seen


def test_firm_series_default_is_bounded_and_says_so(monkeypatch):
    seen = _patch_trading(monkeypatch)
    p = asyncio.run(T.build_firm_series_payload("삼성전자"))
    assert "price_dd>=%s" in seen["sql"] and seen["params"] == ("005930", "20231016")     # 전 기간 질의에도 하한
    assert p["status"] == "ok" and p["data"]["weekly_from"] == "20231016"
    notes = [w for w in p["warnings"] if NOTE in w]
    assert len(notes) == 1 and "2023-10-16" in notes[0] and "price_multiple_data" in notes[0]
    assert "요청한 since" not in notes[0]
    md = _render_firm(p)
    assert "주간 곡선 2개(최근 3년)" in md and "전 구간 주간 곡선" not in md and NOTE in md


def test_firm_series_since_before_the_cutoff_is_raised_to_it_with_a_note(monkeypatch):
    seen = _patch_trading(monkeypatch)
    p = asyncio.run(T.build_firm_series_payload("삼성전자", since="20200101"))
    assert seen["params"] == ("005930", "20231016")
    note = next(w for w in p["warnings"] if NOTE in w)
    assert "요청한 since 20200101 가 하한보다 이르러" in note


def test_firm_series_since_inside_the_window_is_untouched_and_silent(monkeypatch):
    seen = _patch_trading(monkeypatch)
    p = asyncio.run(T.build_firm_series_payload("삼성전자", since="2025-01-01"))      # 하이픈 형식도 받는다
    assert seen["params"] == ("005930", "20250101")
    assert not any(NOTE in w for w in p["warnings"])


def test_firm_series_no_rows_mentions_the_window(monkeypatch):
    _patch_trading(monkeypatch, rows=[])
    p = asyncio.run(T.build_firm_series_payload("삼성전자"))
    assert p["status"] == "no_data" and any(NOTE in w for w in p["warnings"])


# ═══════════════════════════════════════════════════════════════════════════════
# 3. company_resolver — 시장 맥락 캐시는 빈 읽기로 완전한 맥락을 잃지 않는다
# ═══════════════════════════════════════════════════════════════════════════════
def _day(days_ago: int) -> str:
    return (CR.today_kst() - timedelta(days=days_ago)).strftime("%Y%m%d")


def _complete(days_ago=3, cap=1) -> MarketContext:
    return MarketContext({"005930": cap}, frozenset({"005930"}), _day(days_ago), "krx_daily")


LOCAL = MarketContext({"005930": 9}, source="local_popularity_prior")


def _setup(monkeypatch, prev, database):
    monkeypatch.setattr(CR, "_market_context", prev)
    monkeypatch.setattr(CR, "_market_context_loaded_at", 0.0)                 # 만료 상태
    monkeypatch.setattr(CR, "_market_context_valid_for", CR._MARKET_CONTEXT_TTL_SECONDS)
    monkeypatch.setattr(CR, "_database_market_context", lambda: database)
    monkeypatch.setattr(CR, "_local_market_context", lambda: LOCAL)


def test_empty_refresh_keeps_the_previous_complete_context_and_retries_soon(monkeypatch):
    good = _complete()
    _setup(monkeypatch, good, None)                      # 빈 표·접속 실패·시간 초과 → None
    loaded = asyncio.run(CR.load_market_context())
    assert loaded is good and CR._market_context is good  # 같은 객체 — 리졸버도 다시 짓지 않는다
    assert CR._market_context_valid_for == CR._MARKET_CONTEXT_RETRY_SECONDS and CR._market_context_is_fresh()
    # 재시도 시각이 되어 DB 가 돌아오면 새 값으로 바뀌고 유효 시간은 평소로 돌아간다
    fresh = _complete(days_ago=0, cap=2)
    monkeypatch.setattr(CR, "_market_context_loaded_at", 0.0)
    monkeypatch.setattr(CR, "_database_market_context", lambda: fresh)
    loaded = asyncio.run(CR.load_market_context())
    assert loaded.as_of_date == fresh.as_of_date and loaded.market_caps["005930"] == 2
    assert CR._market_context_valid_for == CR._MARKET_CONTEXT_TTL_SECONDS


def test_half_snapshot_does_not_replace_a_complete_context(monkeypatch):
    good = _complete()
    half = MarketContext({"005930": 5}, None, _day(0), "krx_daily")      # 일부만 읽혔다 → active 없음
    _setup(monkeypatch, good, half)
    assert asyncio.run(CR.load_market_context()) is good


def test_complete_refresh_replaces_normally(monkeypatch):
    _setup(monkeypatch, _complete(days_ago=10), _complete(days_ago=0, cap=7))
    loaded = asyncio.run(CR.load_market_context())
    assert loaded.market_caps["005930"] == 7 and CR._market_context_valid_for == CR._MARKET_CONTEXT_TTL_SECONDS


def test_without_a_good_previous_context_the_old_fallback_applies(monkeypatch):
    # 첫 적재(이전 없음) — 종전처럼 로컬 폴백, 평소 유효 시간
    _setup(monkeypatch, None, None)
    assert asyncio.run(CR.load_market_context()) is LOCAL
    assert CR._market_context_valid_for == CR._MARKET_CONTEXT_TTL_SECONDS
    # 이전이 로컬 폴백이면 지킬 이유가 없다
    _setup(monkeypatch, MarketContext({"005930": 1}, source="local_popularity_prior"), None)
    assert asyncio.run(CR.load_market_context()) is LOCAL


def test_a_stale_previous_context_is_not_protected(monkeypatch):
    """as_of 가 14일을 넘긴 맥락은 지킬 가치가 없다 — 배치가 오래 멎었는데 낡은 명부를 붙들지 않는다."""
    _setup(monkeypatch, _complete(days_ago=30), None)
    assert asyncio.run(CR.load_market_context()) is LOCAL


@pytest.mark.parametrize("prev,new,expected", [
    (_complete(), None, True),
    (_complete(), MarketContext({}, None, _day(0), "krx_daily"), True),
    (_complete(), _complete(0), False),
    (None, None, False),
    (MarketContext({}, None, "", "none"), None, False),
    (_complete(days_ago=30), None, False),
    (MarketContext({"1": 1}, frozenset({"1"}), "not-a-date", "krx_daily"), None, False),
    # 261018: DB 맥락의 라벨은 일별 시세 저장분(krx_daily) 하나다 — 옮기기 전 라벨은 DB 맥락으로 보지 않는다
    (MarketContext({"005930": 1}, frozenset({"005930"}), _day(3), "krx_weekly"), None, False),
])
def test_should_keep_previous_matrix(prev, new, expected):
    assert CR._should_keep_previous(prev, new) is expected
