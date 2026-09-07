# -*- coding: utf-8 -*-
"""price_multiple_data 의 as_of — 과거 시점 배수. 형제 trading_data 엔 있었고 여기엔 없었다(260907). network·DB 0."""
from __future__ import annotations

import asyncio

import pytest

from open_proxy_mcp.services import price_multiple_data as S
from open_proxy_mcp.server import mcp

ROWS = [("20260904", "KOSPI", 12.0, 11.0, 1.1, 1.0, 100, 1, 1, None, None), ("20260904", "KOSDAQ", 30.0, 25.0, 2.0, 1.9, 10, 1, 1, None, None),
        ("20251226", "KOSPI", 10.0, 9.5, 1.0, 0.9, 90, 1, 1, None, None), ("20251226", "KOSDAQ", 28.0, 24.0, 1.8, 1.7, 9, 1, 1, None, None),
        ("20200131", "KOSPI", 15.0, 14.0, 0.9, 0.8, 50, 1, 1, None, None)]


def test_norm_as_of_accepts_both_forms_and_rejects_garbage():
    assert S._norm_as_of("2025-12-31") == "20251231" and S._norm_as_of("20251231") == "20251231" and S._norm_as_of("") is None
    with pytest.raises(ValueError):
        S._norm_as_of("2025/12/31")


def test_market_as_of_picks_the_latest_snapshot_at_or_before(monkeypatch):
    monkeypatch.setattr(S, "_pg_rows", lambda sql, params=(): ROWS)
    async def no_div(scheme): return {}, {}, {}
    monkeypatch.setattr(S, "_div_yield_map", no_div)
    p = asyncio.run(S.build_market_val_payload(as_of="20251231"))
    assert p["status"] == "ok" and p["data"]["as_of"] == "20251226" and p["data"]["as_of_requested"] == "20251231"
    assert {h["market"] for h in p["data"]["latest"]} == {"KOSPI", "KOSDAQ"} and len(p["data"]["history"]) == 5
    p = asyncio.run(S.build_market_val_payload(as_of="20190101"))
    assert p["status"] == "no_data" and "가장 이른 스냅샷은 20200131" in p["warnings"][0]
    assert asyncio.run(S.build_market_val_payload())["data"]["as_of"] == "20260904"          # 없으면 최신


def test_sector_sql_bounds_the_snapshot_when_as_of_given(monkeypatch):
    seen = {}
    def fake_rows(sql, params=()):
        seen["sql"], seen["params"] = sql, params
        return []
    monkeypatch.setattr(S, "_pg_rows", fake_rows)
    p = asyncio.run(S.build_sector_val_payload(scheme="ksic", as_of="20251231"))
    assert "snap_dd <= %s" in seen["sql"] and seen["params"] == ("ksic", "ksic", "20251231")
    assert p["status"] == "no_data" and "as_of 20251231" in p["warnings"][0]
    asyncio.run(S.build_sector_val_payload(scheme="ksic"))
    assert "snap_dd <= %s" not in seen["sql"] and seen["params"] == ("ksic", "ksic")


def test_firm_as_of_picks_the_curve_point_at_or_before(monkeypatch):
    async def fake_hist(company, format="md"):
        return {"tool": "price_multiple_data", "status": "ok", "subject": "삼성전자", "data": {"scope": "firm_history", "ticker": "005930", "market": "KOSPI", "sector": "C26",
                "series": [{"asof": "20251219", "pit_fy": 2024, "pit_q": "2025Q3", "cap_krw": 3.1e14, "per_fy0": 12.0, "pbr": 1.2, "per_ttm": 11.0, "pbr_mrq": 1.1},
                           {"asof": "20251226", "pit_fy": 2024, "pit_q": "2025Q3", "cap_krw": 3.2e14, "per_fy0": 12.34, "pbr": 1.21, "per_ttm": 11.5, "pbr_mrq": 1.15},
                           {"asof": "20260102", "pit_fy": 2024, "pit_q": "2025Q3", "cap_krw": 3.3e14, "per_fy0": 13.0, "pbr": 1.3, "per_ttm": 12.0, "pbr_mrq": 1.2}]}}
    monkeypatch.setattr(S, "build_firm_history_payload", fake_hist)
    async def go(a):
        r = await mcp.call_tool("price_multiple_data", a)
        return "".join(getattr(c, "text", "") for c in (r if isinstance(r, list) else r.content))
    out = asyncio.run(go({"company": "삼성전자", "scope": "firm", "as_of": "2025-12-31"}))
    assert out.startswith("# 삼성전자 — 2025-12-26 기준 밸류에이션 (주간 스냅샷)")
    assert "| PER (FY0) | 12.34 |" in out and "| PBR (MRQ) | 1.15 |" in out and "요청 기준일 20251231 → 스냅샷 20251226" in out
    assert "FY0=2024, 분기=2025Q3" in out and "배당수익률은 과거 시점 미제공" in out
    assert "곡선의 첫 점은 20251219" in asyncio.run(go({"company": "삼성전자", "scope": "firm", "as_of": "20200101"}))
    assert "YYYYMMDD" in asyncio.run(go({"company": "삼성전자", "as_of": "작년말"}))
