"""dividend_data — 시장(KOSPI·KOSDAQ) 인자. 코스닥 원장을 같은 표에 올려도 코스피 숫자가 바뀌지 않는다(2026-11-25).

DB 0콜 — `dd.*` 를 바꿔 끼우거나 `_rows` 로 가는 SQL·인자를 잡는다.
"""
from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import sys

from open_proxy_mcp.services import dividend_data as dd
from open_proxy_mcp.services import price_multiple_data as pmd
from open_proxy_mcp.tools import dividend_data as dd_tool

ROOT = pathlib.Path(__file__).resolve().parent.parent


class _Registry:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self, *args, **kwargs):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


def _tool():
    reg = _Registry()
    dd_tool.register_tools(reg)
    return reg.tools["dividend_data"]


def _capture(monkeypatch, ret=None):
    seen: list[tuple[str, tuple]] = []

    def fake(sql, params=()):
        seen.append((sql, tuple(params)))
        return ret if ret is not None else []
    monkeypatch.setattr(dd, "_rows", fake)
    return seen


# ── 서비스: 모든 원장 질의가 market 으로 거른다 ─────────────────────────────────────────────────

def test_aggregate_filters_market_and_defaults_to_kospi(monkeypatch):
    seen = _capture(monkeypatch)
    dd.aggregate_history("market", "코스피", 2020, 2025)
    sql, params = seen[-1]
    assert "d.market = %s" in sql and params[-1] == "KOSPI"
    dd.aggregate_history("market", "코스닥", 2020, 2025, "KOSDAQ")
    assert seen[-1][1][-1] == "KOSDAQ"


def test_screen_binds_market_in_universe_and_population(monkeypatch):
    seen = _capture(monkeypatch)
    dd.screen(2025, market="KOSDAQ")
    sqls = [s for s, _ in seen]
    assert all("market = %s" in s for s in sqls[:1]), "본 질의(모집단 uni + 연도 CTE)가 market 을 건다"
    first_sql, first_params = seen[0]
    assert first_sql.count("market = %s") == 2           # uni · per
    assert first_params[:3] == ("11011", 2025, "KOSDAQ")
    # 행이 없으면 모집단을 따로 한 번 더 읽는다 — 그 질의도 market 을 건다
    assert any("COUNT(DISTINCT corp_code)" in s and params[-1] == "KOSDAQ" for s, params in seen[1:])


def test_screen_default_is_kospi(monkeypatch):
    seen = _capture(monkeypatch)
    dd.screen(2025)
    assert seen[0][1][:3] == ("11011", 2025, "KOSPI")


def test_scope_years_follow_market(monkeypatch):
    got = []
    monkeypatch.setattr(dd, "payment_scope_years", lambda m="KOSPI": got.append(m) or [2024])
    out = dd.screen(2025, min_payments=2, market="KOSDAQ")
    assert got == ["KOSDAQ"] and out["status"] == "scope_incomplete"


def test_year_range_and_latest_annual_filter_market(monkeypatch):
    seen = _capture(monkeypatch, ret=[(2020, 2026)])
    dd.year_range("KOSDAQ")
    dd.latest_annual_year()
    assert seen[0][1] == ("KOSDAQ",) and seen[1][1] == ("11011", "KOSPI")


# ── 도구: market 인자 ──────────────────────────────────────────────────────────────────────────

def _wire(monkeypatch):
    calls: dict[str, list] = {}

    def rec(name, ret):
        def fn(*a, **k):
            calls.setdefault(name, []).append((a, k))
            return ret
        return fn
    monkeypatch.setattr(dd, "year_range", rec("year_range", (2020, 2026)))
    monkeypatch.setattr(dd, "latest_annual_year", rec("latest_annual_year", 2025))
    agg = {"status": "ok", "rows": [{"bsns_year": 2025, "n_universe": 1, "n_payers": 1, "div_total_krw": 1, "payout_avg": 1.0, "n_copy_guard": 0}]}
    monkeypatch.setattr(dd, "aggregate_history", rec("aggregate_history", agg))
    monkeypatch.setattr(dd, "payment_scope_years", rec("payment_scope_years", [2025]))
    monkeypatch.setattr(dd, "screen", rec("screen", {"status": "ok", "rows": [], "matched": 0, "n_universe": 0,
                                                      "returned": 0, "limit": 50, "sort": "total", "min_yield": None,
                                                      "price_min_dd": None, "n_noprice": 0, "yield_mode": False}))

    async def px():
        return "20261124"
    monkeypatch.setattr(pmd, "_serving_price_dd", px)
    return calls


def test_tool_market_scope_routes_kosdaq_and_labels_it(monkeypatch):
    calls = _wire(monkeypatch)
    out = asyncio.run(_tool()(scope="market", market="kosdaq"))
    assert calls["aggregate_history"][0][0] == ("market", "코스닥", 2020, 2026, "KOSDAQ")
    assert "코스닥" in out and "코스피" not in out.split("\n")[0]


def test_tool_default_market_is_kospi(monkeypatch):
    calls = _wire(monkeypatch)
    asyncio.run(_tool()(scope="market"))
    assert calls["aggregate_history"][0][0][-2:] == (2026, "KOSPI")
    assert calls["year_range"][0][0] == ("KOSPI",)


def test_tool_screen_passes_market(monkeypatch):
    calls = _wire(monkeypatch)
    asyncio.run(_tool()(scope="screen", bsns_year=2025, market="KOSDAQ"))
    assert calls["screen"][0][1]["market"] == "KOSDAQ"
    assert calls["payment_scope_years"][0][0] == ("KOSDAQ",)


def test_tool_rejects_unknown_market(monkeypatch):
    calls = _wire(monkeypatch)
    out = asyncio.run(_tool()(scope="market", market="KONEX"))
    assert "status=invalid" in out and not calls


# ── DDL ───────────────────────────────────────────────────────────────────────────────────────

def _ddl():
    spec = importlib.util.spec_from_file_location("div_market_ddl", ROOT / "scripts" / "div_market_ddl.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_ddl_adds_defaulted_market_column_idempotently():
    sql = _ddl().render()
    assert sql.count("ADD COLUMN IF NOT EXISTS market TEXT NOT NULL DEFAULT 'KOSPI'") == 2
    assert "div_declared" in sql and "div_quarterly" in sql
    assert "lock_timeout" in sql and "DROP" not in sql
