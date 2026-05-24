import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from open_proxy_mcp.services import treasury_share as ts
from open_proxy_mcp.services.company import CompanyResolution
from open_proxy_mcp.services.contracts import AnalysisStatus


class FakeClient:
    def __init__(self):
        self.calls = 0

    def api_call_snapshot(self):
        return self.calls

    async def get_treasury_acquisition(self, *_args, **_kwargs):
        return {"list": []}

    async def get_treasury_disposal(self, *_args, **_kwargs):
        return {"list": []}

    async def get_treasury_trust_contract(self, *_args, **_kwargs):
        return {"list": []}

    async def get_treasury_trust_termination(self, *_args, **_kwargs):
        return {"list": []}


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


async def _fake_fetch_filings_for_title_scan(**_kwargs):
    return [], [], None


def test_treasury_share_exposes_fetch_decision_stage_timings(monkeypatch):
    searched_types = []
    monkeypatch.setattr(ts, "get_dart_client", lambda: FakeClient())
    monkeypatch.setattr(ts, "resolve_company_query", _fake_resolve)

    async def fake_fetch_filings_for_title_scan(**kwargs):
        searched_types.append(kwargs["pblntf_tys"])
        return await _fake_fetch_filings_for_title_scan(**kwargs)

    monkeypatch.setattr(ts, "fetch_filings_for_title_scan", fake_fetch_filings_for_title_scan)

    payload = asyncio.run(
        ts.build_treasury_share_payload(
            "삼성전자",
            scope="summary",
            lookback_months=24,
        )
    )

    timings = payload["data"]["timings_ms"]
    assert "fetch_decisions.ds005_apis" in timings
    assert "fetch_decisions.title_search" in timings
    assert "fetch_decisions.cancelation_filter" in timings
    assert "fetch_decisions.execution_report_filter" in timings
    assert "fetch_decisions.cancelation_body_enrich" in timings
    assert "fetch_decisions.execution_body_enrich" in timings
    assert searched_types.count(("B", "I", "E")) == 1
    assert "" not in searched_types
