# -*- coding: utf-8 -*-
"""director_board `pay_agenda`는 **최근 정기주총 소집공고**를 읽는다 — 최근 공고가 아니라.

260904 실측(고려아연): 09-09 임시주총 소집공고가 최신이라 `auto`(임박 회차 우선) 선택이 그 공고를
읽었고, 3월 정기 제6호 「이사 보수한도 승인(120억)」을 놓친 채 「없거나 파싱 실패 ⚠️」를 냈다.
애경케미칼도 동일. 보수한도 승인은 정기주총 안건이므로 회차 선택은 정기 공고여야 한다.

network 0콜. list.json / document 경계만 가짜 client로 대체하고(회귀 캐시 원칙: DART 응답 경계),
shareholder_meeting의 후보 선택 코드는 실제 경로를 탄다. 회의 종류 분류·안건 파싱은 문서별로 고정.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from open_proxy_mcp.services import director_board as db
from open_proxy_mcp.services import filing_search as fs
from open_proxy_mcp.services import shareholder_meeting as sm
from open_proxy_mcp.services.company import CompanyResolution
from open_proxy_mcp.services.contracts import AnalysisStatus

TODAY = date.today()
CORP_CODE = "00102858"

# 임시주총(최신, 회의 예정) 가 정기주총(3월, 이미 개최) 뒤에 온다 — 고려아연 260904 모양.
ANNUAL_NOTICE_DT = TODAY - timedelta(days=180)
ANNUAL_MEETING = ANNUAL_NOTICE_DT + timedelta(days=19)
EXTRA_NOTICE_DT = TODAY - timedelta(days=9)
EXTRA_MEETING = TODAY + timedelta(days=6)

ANNUAL_RCEPT = ANNUAL_NOTICE_DT.strftime("%Y%m%d") + "001616"
EXTRA_RCEPT = EXTRA_NOTICE_DT.strftime("%Y%m%d") + "000111"


def _kdate(d: date) -> str:
    return f"{d.year}년 {d.month}월 {d.day}일 오전 9시"


def _filing(rcept_no: str, rcept_dt: date) -> dict:
    return {"rcept_no": rcept_no, "report_nm": "주주총회소집공고", "corp_code": CORP_CODE,
            "rcept_dt": rcept_dt.strftime("%Y%m%d"), "flr_nm": "고려아연"}


NOTICE_INFO = {
    ANNUAL_RCEPT: {"meeting_type": "정기", "meeting_term": "제52기", "is_correction": False,
                   "datetime": _kdate(ANNUAL_MEETING), "location": "서울"},
    EXTRA_RCEPT: {"meeting_type": "임시", "meeting_term": "", "is_correction": False,
                  "datetime": _kdate(EXTRA_MEETING), "location": "서울"},
}

ANNUAL_COMP_ITEM = {
    "number": "제6호", "title": "이사 보수한도 승인의 건(120억원)", "target": "이사",
    "current": {"limitAmount": 12_000_000_000},
    "prior": {"limitAmount": 10_000_000_000, "actualPaidAmount": 6_750_000_000},
}


class FakeClient:
    """list.json(E006) + document 경계만 흉내 낸다."""

    def __init__(self, filings: list[dict]):
        self.filings = filings
        self.search_calls: list[dict] = []
        self.calls = 0

    def api_call_snapshot(self):
        return self.calls

    async def search_filings(self, **kwargs):
        self.search_calls.append(kwargs)
        self.calls += 1
        rows = [f for f in self.filings if kwargs["bgn_de"] <= f["rcept_dt"] <= kwargs["end_de"]]
        return {"status": "000", "total_count": len(rows), "list": rows}

    async def get_document_cached(self, rcept_no: str):
        return {"text": f"doc {rcept_no}", "html": ""}

    async def get_company_info(self, _corp_code):
        return {"acc_mt": "12"}


def _install(monkeypatch, filings: list[dict]) -> FakeClient:
    client = FakeClient(filings)
    monkeypatch.setattr(sm, "get_dart_client", lambda: client)
    monkeypatch.setattr(fs, "get_dart_client", lambda: client)

    async def fake_resolve(_query):
        return CompanyResolution(
            status=AnalysisStatus.EXACT, query="고려아연",
            selected={"corp_name": "고려아연", "stock_code": "010130", "corp_code": CORP_CODE},
            candidates=[])

    async def fake_notice_info(rcept_no, _text, _html):
        return NOTICE_INFO[rcept_no], "dart_xml"

    async def fake_load(rcept_no, *, scope, soup_cache):
        info = NOTICE_INFO[rcept_no]
        items = [ANNUAL_COMP_ITEM] if info["meeting_type"] == "정기" else []
        return ({"text": f"doc {rcept_no}", "html": "", "meeting_info": info,
                 "agenda": [], "agenda_valid": True, "board": {"summary": {}},
                 "compensation": {"items": items, "summary": {"totalItems": len(items)}},
                 "correction": None}, [], "dart_xml")

    monkeypatch.setattr(sm, "resolve_company_query", fake_resolve)
    monkeypatch.setattr(sm, "_notice_info_with_fallback", fake_notice_info)
    monkeypatch.setattr(sm, "_load_notice_bundle_with_fallback", fake_load)
    return client


def test_before_auto_selection_reads_the_upcoming_extraordinary_notice(monkeypatch):
    """(before) `auto`는 임박한 임시주총 공고를 고른다 — 보수한도 안건이 없는 공고."""
    _install(monkeypatch, [_filing(EXTRA_RCEPT, EXTRA_NOTICE_DT), _filing(ANNUAL_RCEPT, ANNUAL_NOTICE_DT)])
    payload = asyncio.run(sm.build_shareholder_meeting_payload("고려아연", scope="compensation"))
    assert payload["data"]["notice"]["rcept_no"] == EXTRA_RCEPT
    assert payload["data"]["compensation"]["items"] == []


def test_pay_agenda_reads_the_latest_annual_notice_even_when_extraordinary_is_newer(monkeypatch):
    """(after) pay_agenda는 정기 공고(제6호 120억)를 읽고, 근거 공고 rcept·회의일을 싣는다."""
    client = _install(monkeypatch, [_filing(EXTRA_RCEPT, EXTRA_NOTICE_DT), _filing(ANNUAL_RCEPT, ANNUAL_NOTICE_DT)])
    warnings: list[str] = []
    out = asyncio.run(db._pay_agenda_scope("고려아연", CORP_CODE, warnings=warnings))

    assert out["notice_rcept_no"] == ANNUAL_RCEPT
    assert out["meeting_type"] == "annual"
    assert out["meeting_date"] == ANNUAL_MEETING.isoformat()
    assert out["agenda_no"] == "제6호"
    assert out["proposed_limit_krw"] == 12_000_000_000
    assert out["prior_limit_krw"] == 10_000_000_000
    assert out["limit_change_pct"] == 20.0
    assert out["prior_utilization_pct"] == 67.5
    assert not [w for w in warnings if "없거나 파싱" in w]
    # list.json은 E006(주주총회소집공고)으로 먼저 좁힌다 — 전체 유형 순회 금지.
    assert client.search_calls and all(c.get("pblntf_detail_ty") == "E006" for c in client.search_calls)


def test_pay_agenda_says_so_when_only_an_extraordinary_notice_exists(monkeypatch):
    """구간에 임시주총 공고만 있으면 파싱 실패가 아니라 「정기 공고 없음」으로 말한다."""
    _install(monkeypatch, [_filing(EXTRA_RCEPT, EXTRA_NOTICE_DT)])
    warnings: list[str] = []
    out = asyncio.run(db._pay_agenda_scope("고려아연", CORP_CODE, warnings=warnings))

    assert out["status"] == "no_annual_notice"
    assert out["extraordinary_notice"]["notice_rcept_no"] == EXTRA_RCEPT
    assert out["extraordinary_notice"]["meeting_date"] == EXTRA_MEETING.isoformat()
    joined = " ".join(warnings)
    assert "임시주총" in joined and EXTRA_RCEPT in joined and "정기주총 안건" in joined

    flags = db._collect_data_quality_flags({"pay_agenda": out})
    pa_flags = [f for f in flags if f["scope"] == "pay_agenda"]
    assert pa_flags and pa_flags[0]["kind"] == "no_annual_notice"


def test_pay_agenda_reports_no_notice_at_all(monkeypatch):
    """소집공고가 하나도 없으면 임시주총 언급 없이 「정기 공고 없음」."""
    _install(monkeypatch, [])
    warnings: list[str] = []
    out = asyncio.run(db._pay_agenda_scope("고려아연", CORP_CODE, warnings=warnings))
    assert out["status"] == "no_annual_notice"
    assert "extraordinary_notice" not in out
    assert any("정기주총 소집공고 없음" in w for w in warnings)
    assert not any("임시주총" in w for w in warnings)


def test_parse_failure_on_annual_notice_is_still_parse_failed(monkeypatch):
    """정기 공고는 찾았는데 안건이 없으면 종전대로 no_agenda(parse_failed) — 근거 공고는 남긴다."""
    _install(monkeypatch, [_filing(ANNUAL_RCEPT, ANNUAL_NOTICE_DT)])

    async def empty_load(rcept_no, *, scope, soup_cache):
        return ({"text": "", "html": "", "meeting_info": NOTICE_INFO[rcept_no], "agenda": [],
                 "agenda_valid": True, "board": {"summary": {}},
                 "compensation": {"items": [], "summary": {}}, "correction": None}, [], "dart_xml")

    monkeypatch.setattr(sm, "_load_notice_bundle_with_fallback", empty_load)
    warnings: list[str] = []
    out = asyncio.run(db._pay_agenda_scope("고려아연", CORP_CODE, warnings=warnings))
    assert out["status"] == "no_agenda"
    assert out["notice_rcept_no"] == ANNUAL_RCEPT
    flags = db._collect_data_quality_flags({"pay_agenda": out})
    assert [f["kind"] for f in flags if f["scope"] == "pay_agenda"] == ["parse_failed"]
