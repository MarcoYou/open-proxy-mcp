"""trading_data 해상도(`freq`) — 요청한 해상도와 실제로 준 해상도가 어긋나지 않는다 (261031).

실사용 대화: 일간 주가 차트를 요청받은 AI 가 `freq="daily"` 로 불렀는데, 서버는 모르는 말을 거르지 않고 주간 점을 주면서
응답에는 `freq:"daily"` 라 적었다 — AI 는 일간 곡선인 줄 알고 주간 차트를 그렸다. 지키는 것:
  ① firm + daily → 일별 시세 저장분(최근 약 1년)의 전 거래일. 그보다 이른 요청 구간은 주간 점을 앞에 잇고 이음매를 밝힌다
  ② 일별 저장분을 못 읽으면 주간으로 답하되 `data.freq` 는 "weekly" + 경고(거짓 "daily" 금지)
  ③ 주간·월간 응답은 종전 그대로(새 칸 없음)
  ④ 모르는 해상도 말은 invalid — 추측하지 않는다 / 한국어 말(일간·주간·월간)은 받는다
  ⑤ 시장·섹터 시총 집계는 일간이 없다 — 주간으로 주고 `freq:"weekly"` + 경고
network 0 · DB 0(가짜 `_pg_rows`).
"""
from __future__ import annotations

import asyncio

from open_proxy_mcp.services import trading as T
from open_proxy_mcp.tools.trading import _render_firm

SH = 5_846_278_608


def _r(dd, close, market="KS"):
    return (dd, market, close, close * SH, SH)


#: 주간 점 — 일별 창(20251009~) 앞의 두 점과 창 안의 두 점.
WEEKLY = [_r("20250926", 60_000), _r("20251002", 61_000), _r("20261016", 215_500), _r("20261023", 215_500)]
#: 일별 저장분 — 창 첫날부터. 실제로는 약 250행이지만 모양만 본다.
DAILY = [_r("20251010", 62_000), _r("20251013", 62_500), _r("20261022", 214_500), _r("20261023", 215_500),
         _r("20261030", 225_000)]


def _route(monkeypatch, weekly=WEEKLY, daily=DAILY, tail=()):
    calls = []

    async def fake_resolve(q):
        return {"corp_name": "삼성전자", "stock_code": "005930"}, None

    async def fake_bounds():
        return "20261023", "20231023"

    def fake_rows(sql, params=()):
        s = " ".join(sql.split())
        calls.append(s)
        if "FROM krx_weekly" in s:
            return None if weekly is None else [r for r in weekly if r[0] >= params[1]]
        if "FROM krx_daily" in s:
            return None if daily is None else [r for r in daily if r[0] >= params[1]]
        if "FROM price_latest" in s:
            return list(tail)
        if "FROM krx_adj_events" in s:
            return []
        raise AssertionError(f"모르는 SQL: {s}")

    monkeypatch.setattr(T, "_resolve_listed", fake_resolve)
    monkeypatch.setattr(T, "_weekly_bounds", fake_bounds)
    monkeypatch.setattr(T, "_pg_rows", fake_rows)
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")
    return calls


def _firm(monkeypatch, **kw):
    route = {k: kw.pop(k) for k in ("weekly", "daily", "tail") if k in kw}
    calls = _route(monkeypatch, **route)
    return asyncio.run(T.build_firm_series_payload("삼성전자", format="json", **kw)), calls


# ── ① 일간 ────────────────────────────────────────────────────────────────
def test_daily_within_the_window_is_every_trading_day(monkeypatch):
    p, calls = _firm(monkeypatch, freq="daily", since="20261020")
    d = p["data"]
    assert p["status"] == "ok" and d["freq"] == "daily"
    assert [s["asof"] for s in d["series"]] == ["20261022", "20261023", "20261030"]
    assert d["daily_from"] == "20261022" and d["points_daily"] == 3 and d["points_weekly"] == 0
    assert d["as_of"] == "20261030" and d["latest"]["close_krw"] == 225_000
    assert d["method"] == T._FIRM_METHOD_DAILY
    assert not any("일간은" in w for w in p["warnings"])                # 이음매가 없으면 이음매 경고도 없다
    assert any("FROM krx_daily" in s for s in calls) and not any("FROM price_latest" in s for s in calls)


def test_daily_before_the_window_prefixes_weekly_points_and_says_so(monkeypatch):
    p, _ = _firm(monkeypatch, freq="daily", since="20250901")
    d = p["data"]
    assert [s["asof"] for s in d["series"]] == ["20250926", "20251002", "20251010", "20251013",
                                                 "20261022", "20261023", "20261030"]
    assert d["daily_from"] == "20251010" and d["points_weekly"] == 2 and d["points_daily"] == 5
    assert d["points"] == 7 and d["freq"] == "daily"
    assert any("일간은 20251010 부터" in w for w in p["warnings"])
    md = _render_firm(p)
    assert "일간 관측" in md and "일간은 20251010~" in md and "주간 관측" not in md


# ── ② 일별을 못 읽으면 주간이라고 말한다 ────────────────────────────────────
def test_daily_read_failure_answers_weekly_and_says_weekly(monkeypatch):
    for daily in (None, []):
        p, _ = _firm(monkeypatch, freq="daily", daily=daily)
        d = p["data"]
        assert p["status"] == "ok" and d["freq"] == "weekly"            # 거짓 "daily" 를 적지 않는다
        assert "daily_from" not in d and "points_daily" not in d
        assert any("주간**으로 답했습니다" in w for w in p["warnings"])
        assert "주간 관측" in _render_firm(p)


# ── ③ 주간·월간은 종전 그대로 ───────────────────────────────────────────────
def test_weekly_and_monthly_payloads_have_no_new_keys(monkeypatch):
    for freq in ("", "weekly", "주간"):
        p, calls = _firm(monkeypatch, freq=freq)
        d = p["data"]
        assert d["freq"] == "weekly" and "daily_from" not in d and "points_daily" not in d
        assert not any("FROM krx_daily" in s for s in calls)
    p, _ = _firm(monkeypatch, freq="월간")
    assert p["data"]["freq"] == "monthly" and "월말 관측" in _render_firm(p)


# ── ④ 해상도 말 ────────────────────────────────────────────────────────────
def test_unknown_freq_is_invalid_not_guessed(monkeypatch):
    p, calls = _firm(monkeypatch, freq="hourly")
    assert p["status"] == "invalid" and "daily / weekly / monthly" in p["warnings"][0]
    assert calls == []                                                  # DB 를 부르기 전에 거른다


def test_korean_freq_words(monkeypatch):
    for word in ("일간", "일별", "DAILY", " daily "):
        p, _ = _firm(monkeypatch, freq=word, since="20261020")
        assert p["data"]["freq"] == "daily", word


def test_norm_freq_contract():
    assert T._norm_freq("", "firm") == ("weekly", None)
    assert T._norm_freq("", "market") == ("monthly", None)
    assert T._norm_freq("일간", "firm") == ("daily", None)
    f, note = T._norm_freq("daily", "market")
    assert f == "weekly" and "주간으로 답했습니다" in note
    f, note = T._norm_freq("분봉", "sector")
    assert f is None and "weekly / monthly" in note


# ── ⑤ 시총 집계의 일간 ─────────────────────────────────────────────────────
def test_market_daily_answers_weekly_and_says_so(monkeypatch):
    rows = [("20261023", "KQ", 400, 1800), ("20261023", "KS", 4700, 940),
            ("20261030", "KQ", 410, 1828), ("20261030", "KS", 4730, 939)]

    async def fake_read_agg(cold, key, since, primary, cols, **kw):
        return await primary, None

    monkeypatch.setattr(T, "_read_agg", fake_read_agg)
    monkeypatch.setattr(T, "_pg_rows", lambda sql, params=(): rows)
    p = asyncio.run(T.build_cap_agg_payload("market", freq="daily"))
    assert p["status"] == "ok" and p["data"]["freq"] == "weekly" and p["data"]["points"] == 2
    assert "주간으로 답했습니다" in p["warnings"][0]
    p = asyncio.run(T.build_cap_agg_payload("market", freq="분봉"))
    assert p["status"] == "invalid"
