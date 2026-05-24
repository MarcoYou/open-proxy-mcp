import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from open_proxy_mcp.services import corp_gov_report as cgr
from open_proxy_mcp.services.company import CompanyResolution
from open_proxy_mcp.services.contracts import AnalysisStatus


class FakeClient:
    def __init__(self):
        self.calls = 0

    def api_call_snapshot(self):
        return self.calls

    async def get_company_info(self, _corp_code):
        return {"corp_cls": "Y"}

    async def get_document_cached(self, _rcept_no):
        return {"html": "<html><body></body></html>"}


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


async def _fake_fetch_latest_reports(*_args, **_kwargs):
    return [], [], 0


def test_corp_gov_report_uses_short_window_for_summary(monkeypatch):
    called_years = []

    async def fake_fetch_latest_reports(_corp_code, years):
        called_years.append(years)
        return [], [], 0

    monkeypatch.setattr(cgr, "get_dart_client", lambda: FakeClient())
    monkeypatch.setattr(cgr, "resolve_company_query", _fake_resolve)
    monkeypatch.setattr(cgr, "_fetch_latest_reports", fake_fetch_latest_reports)

    asyncio.run(cgr.build_corp_gov_report_payload("삼성전자", scope="summary"))

    assert called_years == [2]


def test_corp_gov_report_keeps_long_window_for_timeline(monkeypatch):
    called_years = []

    async def fake_fetch_latest_reports(_corp_code, years):
        called_years.append(years)
        return [], [], 0

    monkeypatch.setattr(cgr, "get_dart_client", lambda: FakeClient())
    monkeypatch.setattr(cgr, "resolve_company_query", _fake_resolve)
    monkeypatch.setattr(cgr, "_fetch_latest_reports", fake_fetch_latest_reports)

    asyncio.run(cgr.build_corp_gov_report_payload("삼성전자", scope="timeline"))

    assert called_years == [4]


def test_corp_gov_report_exposes_nested_filings_and_company_timings(monkeypatch):
    monkeypatch.setattr(cgr, "get_dart_client", lambda: FakeClient())
    monkeypatch.setattr(cgr, "resolve_company_query", _fake_resolve)
    monkeypatch.setattr(cgr, "_fetch_latest_reports", _fake_fetch_latest_reports)

    payload = asyncio.run(cgr.build_corp_gov_report_payload("삼성전자", scope="summary"))

    timings = payload["data"]["timings_ms"]
    assert "filings_and_company_info.fetch_latest_reports" in timings
    assert "filings_and_company_info.company_info" in timings
