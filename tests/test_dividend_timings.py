import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from open_proxy_mcp.services import dividend_v2 as dv
from open_proxy_mcp.services.company import CompanyResolution
from open_proxy_mcp.services.contracts import AnalysisStatus


class FakeClient:
    def __init__(self):
        self.calls = 0

    def api_call_snapshot(self):
        return self.calls


def _resolution():
    return CompanyResolution(
        status=AnalysisStatus.EXACT,
        query="삼성전자",
        selected={
            "corp_name": "삼성전자",
            "stock_code": "005930",
            "corp_code": "00126380",
        },
        candidates=[],
    )


async def _fake_resolve(_query):
    return _resolution()


async def _fake_annual_summary(_corp_code, year):
    return {
        "period": f"{year}",
        "stlm_dt": f"{year}-12-31",
        "cash_dps": 1000,
        "total_dps": 1000,
        "source": "alotMatter",
    }, None


async def _fake_search_dividend_filings(*_args, **_kwargs):
    return [], [], None


async def _fake_decision_details(_filings):
    return []


def test_dividend_meta_detections_run_concurrently(monkeypatch):
    started: list[str] = []
    both_started = asyncio.Event()

    async def fake_pre(*_args, **_kwargs):
        started.append("pre")
        if len(started) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=0.2)
        return False, []

    async def fake_capital(*_args, **_kwargs):
        started.append("capital")
        if len(started) == 2:
            both_started.set()
        await asyncio.wait_for(both_started.wait(), timeout=0.2)
        return False, []

    monkeypatch.setattr(dv, "get_dart_client", lambda: FakeClient())
    monkeypatch.setattr(dv, "resolve_company_query", _fake_resolve)
    monkeypatch.setattr(dv, "_annual_summary", _fake_annual_summary)
    monkeypatch.setattr(dv, "_search_dividend_filings", _fake_search_dividend_filings)
    monkeypatch.setattr(dv, "_decision_details", _fake_decision_details)
    monkeypatch.setattr(dv, "_detect_pre_dividend_post_resolution", fake_pre)
    monkeypatch.setattr(dv, "_detect_capital_reserve_reduction", fake_capital)

    payload = asyncio.run(dv.build_dividend_payload("삼성전자", scope="summary", year=2025))

    assert payload["status"] == "exact"
    assert set(started) == {"pre", "capital"}
    timings = payload["data"]["timings_ms"]
    assert "pre_dividend_detection" in timings
    assert "capital_reserve_detection" in timings


def test_dividend_summary_and_filings_exposes_nested_timings(monkeypatch):
    monkeypatch.setattr(dv, "get_dart_client", lambda: FakeClient())
    monkeypatch.setattr(dv, "resolve_company_query", _fake_resolve)
    monkeypatch.setattr(dv, "_annual_summary", _fake_annual_summary)
    monkeypatch.setattr(dv, "_search_dividend_filings", _fake_search_dividend_filings)
    monkeypatch.setattr(dv, "_decision_details", _fake_decision_details)
    monkeypatch.setattr(dv, "_detect_pre_dividend_post_resolution", lambda *_args, **_kwargs: _async_pair(False, []))
    monkeypatch.setattr(dv, "_detect_capital_reserve_reduction", lambda *_args, **_kwargs: _async_pair(False, []))

    payload = asyncio.run(dv.build_dividend_payload("삼성전자", scope="summary", year=2025))

    timings = payload["data"]["timings_ms"]
    assert "summary_and_filings.annual_summary" in timings
    assert "summary_and_filings.search_filings" in timings


async def _async_pair(first, second):
    return first, second
