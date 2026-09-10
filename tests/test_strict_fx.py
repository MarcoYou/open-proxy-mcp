"""Unversioned FX sources cannot enter strict historical runs; network zero."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, Mock

import pytest

from open_proxy_mcp.dart import as_of, fx
from open_proxy_mcp.services import financial_metrics as fm


@pytest.fixture
def sources(monkeypatch):
    monkeypatch.setattr(fx, "_MEM", {})
    monkeypatch.setattr(fx, "_MEM_NEG", {})
    mocks = {
        "_db_get": Mock(return_value=1450.0),
        "_db_put": Mock(),
        "_ecos": AsyncMock(return_value=1440.0),
        "_yahoo": AsyncMock(return_value=1430.0),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(fx, name, mock)
    return mocks


@pytest.mark.parametrize("date", ["20241231", "20250326", "20251231", None])
@pytest.mark.parametrize("warm_cache", [False, True])
def test_strict_fx_skips_shared_caches_and_sources_and_records_exclusion(sources, date, warm_cache):
    if warm_cache:
        fx._MEM[("USD", date or fx._today())] = 1420.0
    previous_cache = dict(fx._MEM)
    tokens = as_of.set_strict_as_of("2025-03-26T09:00:00+09:00")
    try:
        assert asyncio.run(fx.fx_to_krw("USD", date)) is None
        assert as_of.strict_exclusions() == [{
            "endpoint": "fx_to_krw", "receipt": "",
            "reason": "unversioned_source", "count": 1,
        }]
    finally:
        as_of.reset_strict_as_of(tokens)
    assert fx._MEM == previous_cache
    for mock in sources.values():
        mock.assert_not_called()


def test_strict_krw_requires_no_conversion_or_exclusion(sources):
    tokens = as_of.set_strict_as_of("2025-03-26T09:00:00+09:00")
    try:
        assert asyncio.run(fx.fx_to_krw("KRW", "20241231")) == 1.0
        assert as_of.strict_exclusions() == []
    finally:
        as_of.reset_strict_as_of(tokens)
    for mock in sources.values():
        mock.assert_not_called()


def test_legacy_fx_still_reads_shared_memory_and_database_after_strict_reset(sources):
    fx._MEM[("USD", "20241231")] = 1420.0
    tokens = as_of.set_strict_as_of("2025-03-26T09:00:00+09:00")
    try:
        assert asyncio.run(fx.fx_to_krw("USD", "20241231")) is None
    finally:
        as_of.reset_strict_as_of(tokens)
    assert asyncio.run(fx.fx_to_krw("USD", "20241231")) == 1420.0
    sources["_db_get"].assert_not_called()
    assert asyncio.run(fx.fx_to_krw("EUR", "20241231")) == 1450.0
    sources["_db_get"].assert_called_once_with("EUR", "20241231")
    sources["_ecos"].assert_not_called()
    sources["_yahoo"].assert_not_called()
    assert as_of.strict_exclusions() == []


def test_legacy_fx_still_falls_back_to_network_sources(sources):
    sources["_db_get"].return_value = None
    sources["_ecos"].return_value = None
    assert asyncio.run(fx.fx_to_krw("USD", "20241231")) == 1430.0
    sources["_ecos"].assert_awaited_once_with("USD", "20241231")
    sources["_yahoo"].assert_awaited_once_with("USD", "20241231")
    sources["_db_put"].assert_called_once_with("USD", "20241231", 1430.0)


def test_strict_unconverted_amounts_are_missing_without_erasing_ratios_or_original_rows(sources):
    rows = [{"account_id": "ifrs-full_Assets", "currency": "USD", "thstrm_amount": "1000"}]
    original = deepcopy(rows)
    metrics = {
        "total_assets_krw": 1000, "net_income_krw": 100, "roe_pct": 10.0,
        "standalone": {"total_assets_krw": 400, "net_profit_margin_pct": 8.0},
        "borrowing_detail": {"by_canonical_id": {"OPM_ST": 100}},
        "years": [{"total_assets_krw": 500, "debt_ratio_pct": 20.0}],
    }
    tokens = as_of.set_strict_as_of("2025-03-26T09:00:00+09:00")
    try:
        warnings = asyncio.run(fm._normalize_currency(metrics, rows, "2024-12-31", 2024))
    finally:
        as_of.reset_strict_as_of(tokens)
    assert metrics["total_assets_krw"] is None
    assert metrics["net_income_krw"] is None
    assert metrics["standalone"]["total_assets_krw"] is None
    assert metrics["borrowing_detail"]["by_canonical_id"]["OPM_ST"] is None
    assert metrics["years"][0]["total_assets_krw"] is None
    assert metrics["roe_pct"] == 10.0
    assert metrics["standalone"]["net_profit_margin_pct"] == 8.0
    assert metrics["years"][0]["debt_ratio_pct"] == 20.0
    assert metrics["functional_currency"] == "USD"
    assert metrics["fx_rate_to_krw"] is None
    assert any("원화 금액을 제외" in warning for warning in warnings)
    assert rows == original


def test_strict_quarterly_unconverted_amounts_are_missing_with_native_ratios_preserved(monkeypatch, sources):
    rows = [
        {"account_nm": name, "account_id": identifier, "thstrm_amount": value,
         "fs_div": "CFS", "sj_div": section, "currency": "USD", "thstrm_dt": "2024.03.31"}
        for name, identifier, value, section in [
            ("자산총계", "ifrs-full_Assets", "1000", "BS"),
            ("매출액", "ifrs-full_Revenue", "100", "IS"),
            ("영업이익", "dart_OperatingIncomeLoss", "10", "IS"),
            ("당기순이익", "ifrs-full_ProfitLoss", "8", "IS"),
        ]
    ]
    original = deepcopy(rows)

    async def statement(corp_code, year, reprt_code, fs_div):
        return {"status": "000", "list": rows if year == "2024" and reprt_code == "11013" else []}

    client = type("Client", (), {})()
    client.get_fnltt_singl_acnt = AsyncMock(side_effect=statement)
    client.get_fnltt_singl_acnt_all = AsyncMock(return_value={"status": "000", "list": []})
    monkeypatch.setattr(fm, "get_dart_client", lambda: client)
    tokens = as_of.set_strict_as_of("2025-03-26T09:00:00+09:00")
    try:
        quarters, warnings = asyncio.run(fm._build_quarterly("00100000", 2024, "CFS", fiscal_month=12))
    finally:
        as_of.reset_strict_as_of(tokens)
    assert len(quarters) == 1
    quarter = quarters[0]
    assert quarter["total_assets_krw"] is None
    assert quarter["revenue_krw"] is None
    assert quarter["operating_margin_pct"] == 10.0
    assert quarter["functional_currency"] == "USD"
    assert quarter["fx_rate_to_krw"] is None
    assert any("분기 원화 금액을 제외" in warning for warning in warnings)
    assert rows == original
