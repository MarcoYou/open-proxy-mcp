import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from open_proxy_mcp.services import shareholder_meeting as sm
from open_proxy_mcp.services.company import CompanyResolution
from open_proxy_mcp.services.contracts import AnalysisStatus


class FakeClient:
    def __init__(self):
        self.calls = 0
        self.document_fetches = []

    def api_call_snapshot(self):
        return self.calls

    async def get_document_cached(self, rcept_no):
        self.document_fetches.append(rcept_no)
        return {"text": "mock", "html": "<html></html>"}

    async def get_company_info(self, _corp_code):
        return {"acc_mt": "12"}


def _fake_resolution():
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


def _fake_candidate():
    notice = {
        "rcept_no": "20260224004273",
        "report_name": "주주총회소집공고",
        "disclosure_date": "20260224",
        "filer_name": "LG화학",
        "meeting_type": "정기",
        "meeting_term": "제25기",
        "is_correction": False,
        "datetime": "2026년 3월 31일 (화) 오전 9시",
        "location": "서울특별시 영등포구 여의대로 128",
    }
    return {
        "meeting_type": "annual",
        "meeting_type_label": "정기",
        "notice": notice,
        "meeting_date": None,
        "result_search_year": 2026,
        "result_filing": None,
        "result_filing_warning": None,
        "result_reference": None,
        "meeting_phase": "post_meeting_pre_result",
        "result_status": "pending_or_missing",
        "search_notices": [],
    }


def _fake_parsed_notice():
    return (
        {
            "text": "mock",
            "html": "<html></html>",
            "meeting_info": {
                "meeting_type": "정기",
                "meeting_term": "제25기",
                "datetime": "2026년 3월 31일 (화) 오전 9시",
                "location": "서울특별시 영등포구 여의대로 128",
                "electronic_voting": "very long electronic voting guide",
                "online_broadcast": "very long online broadcast guide",
                "report_items": ["감사보고"],
            },
            "agenda": [
                {
                    "agenda_id": "1",
                    "number": "제1호",
                    "title": "제25기 재무제표 승인의 건",
                    "children": [],
                }
            ],
            "agenda_valid": True,
            "board": {"summary": {}},
            "compensation": {"summary": {}},
            "correction": None,
        },
        [],
        "dart_xml",
    )


def test_explicit_annual_summary_skips_coverage_by_default(monkeypatch):
    monkeypatch.setattr(sm, "get_dart_client", lambda: FakeClient())

    async def fake_resolve(_query):
        return _fake_resolution()

    async def fake_select(*_args, **_kwargs):
        return _fake_candidate(), [], "basis", None, []

    async def fake_load(*_args, **_kwargs):
        return _fake_parsed_notice()

    async def fail_coverage(*_args, **_kwargs):
        raise AssertionError("coverage search should be lazy for explicit annual summary")

    monkeypatch.setattr(sm, "resolve_company_query", fake_resolve)
    monkeypatch.setattr(sm, "_select_notice_candidate", fake_select)
    monkeypatch.setattr(sm, "_load_notice_bundle_with_fallback", fake_load)
    monkeypatch.setattr(sm, "_meeting_window_coverage", fail_coverage)

    payload = asyncio.run(
        sm.build_shareholder_meeting_payload(
            "LG화학",
            meeting_type="annual",
            scope="summary",
            year=2026,
        )
    )

    assert payload["status"] == "exact"
    assert "meeting_coverage_12m" not in payload["data"]


def test_summary_payload_exposes_stage_timings(monkeypatch):
    monkeypatch.setattr(sm, "get_dart_client", lambda: FakeClient())

    async def fake_resolve(_query):
        return _fake_resolution()

    async def fake_select(*_args, **_kwargs):
        return _fake_candidate(), [], "basis", None, []

    async def fake_load(*_args, **_kwargs):
        return _fake_parsed_notice()

    monkeypatch.setattr(sm, "resolve_company_query", fake_resolve)
    monkeypatch.setattr(sm, "_select_notice_candidate", fake_select)
    monkeypatch.setattr(sm, "_load_notice_bundle_with_fallback", fake_load)

    payload = asyncio.run(
        sm.build_shareholder_meeting_payload(
            "LG화학",
            meeting_type="annual",
            scope="summary",
            year=2026,
        )
    )

    timings = payload["data"]["timings_ms"]
    assert timings["total"] >= 0
    assert "resolve_company" in timings
    assert "select_notice_candidate" in timings
    assert "load_notice_bundle" in timings


def test_select_notice_candidate_exposes_nested_timings(monkeypatch):
    monkeypatch.setattr(sm, "get_dart_client", lambda: FakeClient())

    async def fake_resolve(_query):
        return _fake_resolution()

    async def fake_search(**_kwargs):
        return (
            [
                {
                    "rcept_no": "20260224004273",
                    "report_nm": "주주총회소집공고",
                    "rcept_dt": "20260224",
                    "flr_nm": "LG화학",
                }
            ],
            [],
            None,
        )

    async def fake_notice_info(_rcept_no, _text, _html):
        return (
            {
                "meeting_type": "정기",
                "meeting_term": "제25기",
                "datetime": "2026년 3월 31일 (화) 오전 9시",
                "location": "서울특별시 영등포구 여의대로 128",
                "is_correction": False,
            },
            "dart_xml",
        )

    async def fake_load(*_args, **_kwargs):
        return _fake_parsed_notice()

    monkeypatch.setattr(sm, "resolve_company_query", fake_resolve)
    monkeypatch.setattr(sm, "search_filings_by_report_name", fake_search)
    monkeypatch.setattr(sm, "_notice_info_with_fallback", fake_notice_info)
    monkeypatch.setattr(sm, "_load_notice_bundle_with_fallback", fake_load)

    payload = asyncio.run(
        sm.build_shareholder_meeting_payload(
            "LG화학",
            meeting_type="annual",
            scope="summary",
            year=2026,
        )
    )

    timings = payload["data"]["timings_ms"]
    assert "select_notice_candidate.search_filings" in timings
    assert "select_notice_candidate.fetch_top_documents" in timings
    assert "select_notice_candidate.parse_top_documents" in timings
    assert "select_notice_candidate.filter_meeting_window" in timings
    assert "select_notice_candidate.build_candidate" in timings


def test_annual_notice_search_uses_fiscal_month_window(monkeypatch):
    search_windows = []
    monkeypatch.setattr(sm, "get_dart_client", lambda: FakeClient())

    async def fake_resolve(_query):
        return _fake_resolution()

    async def fake_search(**kwargs):
        search_windows.append((kwargs["bgn_de"], kwargs["end_de"]))
        return (
            [
                {
                    "rcept_no": "20260224004273",
                    "report_nm": "주주총회소집공고",
                    "rcept_dt": "20260224",
                    "flr_nm": "LG화학",
                }
            ],
            [],
            None,
        )

    async def fake_notice_info(_rcept_no, _text, _html):
        return (
            {
                "meeting_type": "정기",
                "meeting_term": "제25기",
                "datetime": "2026년 3월 31일 (화) 오전 9시",
                "location": "서울특별시 영등포구 여의대로 128",
                "is_correction": False,
            },
            "dart_xml",
        )

    async def fake_load(*_args, **_kwargs):
        return _fake_parsed_notice()

    monkeypatch.setattr(sm, "resolve_company_query", fake_resolve)
    monkeypatch.setattr(sm, "search_filings_by_report_name", fake_search)
    monkeypatch.setattr(sm, "_notice_info_with_fallback", fake_notice_info)
    monkeypatch.setattr(sm, "_load_notice_bundle_with_fallback", fake_load)

    payload = asyncio.run(
        sm.build_shareholder_meeting_payload(
            "LG화학",
            meeting_type="annual",
            scope="summary",
            year=2026,
        )
    )

    assert payload["status"] == "exact"
    assert search_windows[0] == ("20251003", "20260430")
    assert payload["data"]["fiscal_month"] == "12"
    assert "fiscal_month_lookup" in payload["data"]["timings_ms"]


def test_annual_notice_search_falls_back_when_fiscal_window_empty(monkeypatch):
    search_windows = []
    monkeypatch.setattr(sm, "get_dart_client", lambda: FakeClient())

    async def fake_resolve(_query):
        return _fake_resolution()

    async def fake_search(**kwargs):
        search_windows.append((kwargs["bgn_de"], kwargs["end_de"]))
        if len(search_windows) == 1:
            return [], [], "013"
        return (
            [
                {
                    "rcept_no": "20260224004273",
                    "report_nm": "주주총회소집공고",
                    "rcept_dt": "20260224",
                    "flr_nm": "LG화학",
                }
            ],
            [],
            None,
        )

    async def fake_notice_info(_rcept_no, _text, _html):
        return (
            {
                "meeting_type": "정기",
                "meeting_term": "제25기",
                "datetime": "2026년 3월 31일 (화) 오전 9시",
                "location": "서울특별시 영등포구 여의대로 128",
                "is_correction": False,
            },
            "dart_xml",
        )

    async def fake_load(*_args, **_kwargs):
        return _fake_parsed_notice()

    monkeypatch.setattr(sm, "resolve_company_query", fake_resolve)
    monkeypatch.setattr(sm, "search_filings_by_report_name", fake_search)
    monkeypatch.setattr(sm, "_notice_info_with_fallback", fake_notice_info)
    monkeypatch.setattr(sm, "_load_notice_bundle_with_fallback", fake_load)

    payload = asyncio.run(
        sm.build_shareholder_meeting_payload(
            "LG화학",
            meeting_type="annual",
            scope="summary",
            year=2026,
        )
    )

    assert payload["status"] == "exact"
    assert search_windows[:2] == [
        ("20251003", "20260430"),
        ("20251003", "20261231"),
    ]
    assert "select_notice_candidate.full_year_fallback" in payload["data"]["timings_ms"]


def test_no_filing_warning_includes_fiscal_annual_window(monkeypatch):
    monkeypatch.setattr(sm, "get_dart_client", lambda: FakeClient())

    async def fake_resolve(_query):
        return _fake_resolution()

    async def fake_fiscal_month(_corp_code):
        return "03"

    async def fake_select(*_args, **_kwargs):
        return None, [], None, "2026-01-01~2026-12-31 구간에 정기 주주총회 소집공고를 찾지 못했다.", []

    monkeypatch.setattr(sm, "resolve_company_query", fake_resolve)
    monkeypatch.setattr(sm, "_safe_fiscal_month", fake_fiscal_month)
    monkeypatch.setattr(sm, "_select_notice_candidate", fake_select)

    payload = asyncio.run(
        sm.build_shareholder_meeting_payload(
            "신영증권",
            meeting_type="annual",
            scope="summary",
            year=2026,
        )
    )

    assert payload["status"] == "no_filing"
    assert payload["data"]["fiscal_month"] == "03"
    assert "회계연도 종료월은 3월" in payload["warnings"][0]
    assert "2026-04-01~2026-07-31" in payload["warnings"][0]


def test_fiscal_window_annual_search_fetches_only_latest_document_first(monkeypatch):
    fake_client = FakeClient()
    monkeypatch.setattr(sm, "get_dart_client", lambda: fake_client)

    async def fake_resolve(_query):
        return _fake_resolution()

    async def fake_search(**_kwargs):
        return (
            [
                {
                    "rcept_no": "20260224004273",
                    "report_nm": "주주총회소집공고",
                    "rcept_dt": "20260224",
                    "flr_nm": "LG화학",
                },
                {
                    "rcept_no": "20260201000001",
                    "report_nm": "주주총회소집공고",
                    "rcept_dt": "20260201",
                    "flr_nm": "LG화학",
                },
            ],
            [],
            None,
        )

    async def fake_notice_info(_rcept_no, _text, _html):
        return (
            {
                "meeting_type": "정기",
                "meeting_term": "제25기",
                "datetime": "2026년 3월 31일 (화) 오전 9시",
                "location": "서울특별시 영등포구 여의대로 128",
                "is_correction": False,
            },
            "dart_xml",
        )

    async def fake_load(*_args, **_kwargs):
        return _fake_parsed_notice()

    monkeypatch.setattr(sm, "resolve_company_query", fake_resolve)
    monkeypatch.setattr(sm, "search_filings_by_report_name", fake_search)
    monkeypatch.setattr(sm, "_notice_info_with_fallback", fake_notice_info)
    monkeypatch.setattr(sm, "_load_notice_bundle_with_fallback", fake_load)

    payload = asyncio.run(
        sm.build_shareholder_meeting_payload(
            "LG화학",
            meeting_type="annual",
            scope="summary",
            year=2026,
        )
    )

    assert payload["status"] == "exact"
    assert fake_client.document_fetches == ["20260224004273"]


def test_fiscal_window_annual_search_fetches_remaining_when_latest_is_not_annual(monkeypatch):
    fake_client = FakeClient()
    monkeypatch.setattr(sm, "get_dart_client", lambda: fake_client)

    async def fake_resolve(_query):
        return _fake_resolution()

    async def fake_search(**_kwargs):
        return (
            [
                {
                    "rcept_no": "20260224004273",
                    "report_nm": "주주총회소집공고",
                    "rcept_dt": "20260224",
                    "flr_nm": "LG화학",
                },
                {
                    "rcept_no": "20260201000001",
                    "report_nm": "주주총회소집공고",
                    "rcept_dt": "20260201",
                    "flr_nm": "LG화학",
                },
            ],
            [],
            None,
        )

    async def fake_notice_info(rcept_no, _text, _html):
        meeting_type = "임시" if rcept_no == "20260224004273" else "정기"
        return (
            {
                "meeting_type": meeting_type,
                "meeting_term": "제25기",
                "datetime": "2026년 3월 31일 (화) 오전 9시",
                "location": "서울특별시 영등포구 여의대로 128",
                "is_correction": False,
            },
            "dart_xml",
        )

    async def fake_load(*_args, **_kwargs):
        return _fake_parsed_notice()

    monkeypatch.setattr(sm, "resolve_company_query", fake_resolve)
    monkeypatch.setattr(sm, "search_filings_by_report_name", fake_search)
    monkeypatch.setattr(sm, "_notice_info_with_fallback", fake_notice_info)
    monkeypatch.setattr(sm, "_load_notice_bundle_with_fallback", fake_load)

    payload = asyncio.run(
        sm.build_shareholder_meeting_payload(
            "LG화학",
            meeting_type="annual",
            scope="summary",
            year=2026,
        )
    )

    assert payload["status"] == "exact"
    assert fake_client.document_fetches == ["20260224004273", "20260201000001"]
    assert payload["data"]["notice"]["rcept_no"] == "20260201000001"


def test_rcept_no_fast_path_skips_company_and_candidate_search(monkeypatch):
    monkeypatch.setattr(sm, "get_dart_client", lambda: FakeClient())

    async def fail_resolve(*_args, **_kwargs):
        raise AssertionError("rcept_no fast path should not resolve company")

    async def fail_select(*_args, **_kwargs):
        raise AssertionError("rcept_no fast path should not search candidates")

    async def fake_load(*_args, **_kwargs):
        return _fake_parsed_notice()

    monkeypatch.setattr(sm, "resolve_company_query", fail_resolve)
    monkeypatch.setattr(sm, "_select_notice_candidate", fail_select)
    monkeypatch.setattr(sm, "_load_notice_bundle_with_fallback", fake_load)

    payload = asyncio.run(
        sm.build_shareholder_meeting_payload(
            "LG화학",
            meeting_type="annual",
            scope="summary",
            rcept_no="20260224004273",
        )
    )

    assert payload["status"] == "exact"
    assert payload["data"]["notice"]["rcept_no"] == "20260224004273"
    assert payload["data"]["selection_basis"] == "rcept_no가 제공되어 해당 소집공고를 직접 파싱했다."


def test_summary_omits_verbose_meeting_guides(monkeypatch):
    monkeypatch.setattr(sm, "get_dart_client", lambda: FakeClient())

    async def fake_resolve(_query):
        return _fake_resolution()

    async def fake_select(*_args, **_kwargs):
        return _fake_candidate(), [], "basis", None, []

    async def fake_load(*_args, **_kwargs):
        return _fake_parsed_notice()

    monkeypatch.setattr(sm, "resolve_company_query", fake_resolve)
    monkeypatch.setattr(sm, "_select_notice_candidate", fake_select)
    monkeypatch.setattr(sm, "_load_notice_bundle_with_fallback", fake_load)

    payload = asyncio.run(
        sm.build_shareholder_meeting_payload(
            "LG화학",
            meeting_type="annual",
            scope="summary",
            year=2026,
        )
    )

    meeting_info = payload["data"]["meeting_info"]
    assert "electronic_voting" not in meeting_info
    assert "online_broadcast" not in meeting_info
    assert meeting_info["datetime"] == "2026년 3월 31일 (화) 오전 9시"
