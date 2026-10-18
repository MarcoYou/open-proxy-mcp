# -*- coding: utf-8 -*-
"""기준가 「호가단위 맞춤」은 가격 불연속이 아니다 (261019). network 0 · DB 0.

운영 실측: 수정계수 원장의 3년 창 안 |계수−1|<0.5% 378건 중 371건(81종목, 대부분 기업인수목적회사)은 기준가가 전일 종가를
호가단위로 올리거나 내린 것뿐이었다(하나31호스팩 469900: 2,082 → 2,085, ×1.0014). `trading_data`(firm)는 이것까지 「기준가 조정
… 불연속」으로 셌다. 나머지 7건(맥쿼리인프라 088980 11,820→11,800 등)은 호가단위보다 크게 움직인 진짜 조정이라 그대로 센다.
"""
from __future__ import annotations

import asyncio

import pytest

from open_proxy_mcp.krx_ticks import TICK_ROUNDING_LABEL, is_tick_rounding, tick_size
from open_proxy_mcp.services import trading as T
from open_proxy_mcp.tools.trading import _render_firm


@pytest.mark.parametrize("price,market,dd,tick", [
    (1_999, "KS", "20260702", 1), (2_000, "KQ", "20260702", 5), (19_990, "KS", "20240101", 10),
    (20_000, "KS", "20240101", 50), (199_900, "KQ", "20240101", 100), (500_000, "KS", "20240101", 1_000),
    # 2023-01-25 전: 시장마다 다르다
    (999, "KS", "20221230", 1), (5_000, "KS", "20221230", 10), (60_000, "KS", "20221230", 100),
    (60_000, "KQ", "20221230", 100), (700_000, "KQ", "20221230", 100), (700_000, "KS", "20221230", 1_000),
])
def test_tick_size_table(price, market, dd, tick):
    assert tick_size(price, market, dd) == tick


def test_tick_rounding_separates_rounding_from_real_adjustments():
    assert is_tick_rounding(2_082, 2_085, "KQ", "20260702")            # 하나31호스팩 — 5원 단위로 올림
    assert is_tick_rounding(2_077, 2_080, "KQ", "20260702")
    assert is_tick_rounding(101_600, 102_000, "KS", "20170710")        # 옛 코스피 500원 단위(카카오 시장이전 날)
    assert is_tick_rounding(4_998, 5_000, "KS", "20240101")            # 경계: 기준가 쪽 호가단위(10원)로 판정
    assert not is_tick_rounding(11_820, 11_800, "KS", "20240827")      # 맥쿼리인프라 — 20원(호가단위 10원의 두 칸) 조정
    assert not is_tick_rounding(69_500, 69_200, "KS", "20260316")      # 300원(호가단위 100원의 세 칸)
    assert not is_tick_rounding(208_500, 20_850, "KS", "20161028")     # 액면분할
    assert not is_tick_rounding(None, 2_085, "KQ", "20260702")         # 값이 없으면 판정하지 않는다(조정으로 둔다)
    assert not is_tick_rounding(2_085, 2_085, "KQ", "20260702")        # 어긋남 0 은 사건이 아니다


# ── trading_data(firm) ───────────────────────────────────────────────────────

SH = 1_000_000


def _w(dd, close, market="KQ"):
    return (dd, market, close, close * SH, SH)


def _firm(monkeypatch, adj_rows):
    async def fake_resolve(q):
        return {"corp_name": "하나31호스팩", "stock_code": "469900"}, None

    async def fake_bounds():
        return "20261009", "20231009"

    def fake_rows(sql, params=()):
        s = " ".join(sql.split())
        if "FROM krx_weekly" in s:
            return [_w("20260925", 2_100), _w("20261002", 2_105), _w("20261009", 2_110)]
        if "FROM price_latest" in s:
            return []
        if "FROM krx_adj_events" in s:
            assert "prev_close, base_price, market, label_confidence" in s
            return list(adj_rows)
        raise AssertionError(s)

    monkeypatch.setattr(T, "_resolve_listed", fake_resolve)
    monkeypatch.setattr(T, "_weekly_bounds", fake_bounds)
    monkeypatch.setattr(T, "_pg_rows", fake_rows)
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")
    return asyncio.run(T.build_firm_series_payload("하나31호스팩", format="json"))


def test_firm_does_not_call_tick_rounding_a_discontinuity(monkeypatch):
    p = _firm(monkeypatch, [("20260929", 1.0014, 2_082.0, 2_085.0, "KQ", "unlabeled"),
                            ("20261006", 1.0024, 2_102.0, 2_105.0, "KQ", TICK_ROUNDING_LABEL)])
    assert p["status"] == "ok"
    assert not any("⚠ 구간 내 기준가 조정" in w for w in p["warnings"])
    assert any("호가단위에 맞춘 날 2회" in w and "불연속으로 세지 않았습니다" in w for w in p["warnings"])
    # 지우지 않는다 — 표시만 붙여 그대로 싣는다
    assert p["data"]["adj_events"] == [
        {"event_dd": "20260929", "adj_factor": 1.0014, "tick_rounding": True},
        {"event_dd": "20261006", "adj_factor": 1.0024, "tick_rounding": True}]
    md = _render_firm(p)
    assert "⚠ 기준가 조정" not in md and "호가단위에 맞춘 날 2회" in md


def test_firm_still_warns_about_real_adjustments_next_to_rounding(monkeypatch):
    p = _firm(monkeypatch, [("20260929", 1.0014, 2_082.0, 2_085.0, "KQ", "unlabeled"),
                            ("20261001", 0.9983, 11_820.0, 11_800.0, "KQ", "unlabeled"),     # 두 칸 — 진짜 조정
                            ("20261005", 0.5, None, None, "KQ", "confirmed")])              # 값 없는 옛 행 — 조정으로 둔다
    warn = next(w for w in p["warnings"] if "⚠ 구간 내 기준가 조정" in w)
    assert "조정 2회" in warn and "20261001" in warn and "20261005" in warn and "20260929" not in warn
    assert [e.get("tick_rounding", False) for e in p["data"]["adj_events"]] == [True, False, False]
    assert "> ⚠ 기준가 조정 2회" in _render_firm(p)
