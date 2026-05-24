import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from open_proxy_mcp.services import value_up_v2 as vu
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


async def _fake_search_value_up_items(*_args, **_kwargs):
    return [], [], None


async def _fake_search_kind_value_up_items(*_args, **_kwargs):
    return [], None


def test_value_up_exposes_search_stage_timings(monkeypatch):
    monkeypatch.setattr(vu, "get_dart_client", lambda: FakeClient())
    monkeypatch.setattr(vu, "resolve_company_query", _fake_resolve)
    monkeypatch.setattr(vu, "_search_value_up_items", _fake_search_value_up_items)
    monkeypatch.setattr(vu, "_search_kind_value_up_items", _fake_search_kind_value_up_items)

    payload = asyncio.run(vu.build_value_up_payload("삼성전자", scope="summary", year=2025))

    timings = payload["data"]["timings_ms"]
    assert "dart_search.requested_window" in timings
    assert "kind_search.requested_window" in timings
    assert "diagnostic_search.dart" in timings
    assert "diagnostic_search.kind" in timings
    assert "diagnostic_search" in timings
