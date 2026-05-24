import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from open_proxy_mcp.services import proxy_advise as pa
from open_proxy_mcp.services.company import CompanyResolution
from open_proxy_mcp.services.contracts import AnalysisStatus


class FakeClient:
    def __init__(self):
        self.calls = 0

    def api_call_snapshot(self):
        return self.calls

    async def _load_corp_codes(self):
        return None

    async def get_document_cached(self, _rcept_no):
        return {"text": "", "html": ""}


def _resolution():
    return CompanyResolution(
        status=AnalysisStatus.EXACT,
        query="LG화학",
        selected={
            "corp_name": "LG화학",
            "stock_code": "051910",
            "corp_code": "00356361",
        },
        candidates=[],
    )


async def _fake_resolve(_query):
    return _resolution()


async def _fake_shareholder_meeting(_company, *, scope, **_kwargs):
    data = {
        "agenda_summary": {"titles": []},
        "notice": {"rcept_no": "20260224004273"},
        "summary": {},
    }
    if scope == "aoi_change":
        data["aoi_change"] = {"amendments": []}
    return {"status": "exact", "data": data, "evidence_refs": []}


async def _fake_payload(*_args, **_kwargs):
    return {"status": "exact", "data": {"summary": {}}, "evidence_refs": []}


async def _fake_financial(*_args, **_kwargs):
    return {
        "status": "exact",
        "data": {"summary": {"total_assets_krw": 1_000_000}},
        "evidence_refs": [],
    }


async def _fake_director_eval(*_args, **_kwargs):
    return {
        "status": "exact",
        "data": {"evaluations": [], "agenda_titles_fallback": []},
        "evidence_refs": [],
    }


def test_proxy_advise_exposes_upstream_stage_timings(monkeypatch):
    pa.clear_proxy_advise_cache()
    monkeypatch.setattr(pa, "get_dart_client", lambda: FakeClient())
    monkeypatch.setattr(pa, "resolve_company_query", _fake_resolve)
    monkeypatch.setattr(pa, "_load_vote_style_policy", lambda _style: None)
    monkeypatch.setattr(pa, "build_shareholder_meeting_payload", _fake_shareholder_meeting)
    monkeypatch.setattr(pa, "build_ownership_structure_payload", _fake_payload)
    monkeypatch.setattr(pa, "build_corp_gov_report_payload", _fake_payload)
    monkeypatch.setattr(pa, "build_financial_metrics_payload", _fake_financial)
    monkeypatch.setattr(pa, "build_director_evaluation_payload", _fake_director_eval)

    payload = asyncio.run(
        pa.build_proxy_advise_payload(
            "LG화학",
            year=2026,
            meeting_type="annual",
        )
    )

    timings = payload["data"]["timings_ms"]
    assert timings["total"] >= 0
    assert "resolve_company" in timings
    assert "prewarm_corp_codes" in timings
    assert "upstreams_total" in timings
    assert "upstream.shareholder_meeting.summary" in timings
    assert "upstream.financial_metrics.summary" in timings
    assert "decision_engine" in timings
