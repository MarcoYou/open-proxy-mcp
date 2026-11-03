"""접수번호 9번째 자리 = 접수 창구. `80` 만 거래소로 보면 코스닥·코넥스 수시공시를 잘못 가른다.

네트워크 0. 고치기 전에는 ① `evidence` 가 코스닥(9)·코넥스(6) 수시공시를 DART 접수로 적었고,
② `ownership_structure` 가 9계열 최대주주등소유주식변동신고서를 「공시번호 형식이 올바르지 않습니다」로
건너뛰었고, ③ 주총결과 참조가 9계열을 화이트리스트 밖으로 두었다(원문 조회는 됐지만 창구 표시 없음).
"""
import asyncio
from datetime import date
from pathlib import Path

import pytest

from open_proxy_mcp.dart.client import DartClientError
from open_proxy_mcp.services import ownership_structure as O
from open_proxy_mcp.services.evidence import build_evidence_payload
from open_proxy_mcp.services.rcept_channel import (
    is_exchange_filing,
    kind_acptno_for,
    rcept_channel,
)
from open_proxy_mcp.services.shareholder_meeting import _result_reference


@pytest.mark.parametrize("rcept_no, channel, exchange", [
    ("20260312000987", "dart", False),
    ("20260312100987", "fss", False),
    ("20260312600987", "krx_konex", True),
    ("20260312700987", "krx_bond", True),
    ("20260312800987", "krx_kospi", True),
    ("20260312900987", "krx_kosdaq", True),
    ("20260312300987", "unknown", False),     # 목록을 닫지 않는다 — 모르는 자리는 오류가 아니다
    ("2026031280098", "unknown", False),
    ("", "unknown", False),
])
def test_ninth_digit_is_the_channel(rcept_no, channel, exchange):
    assert rcept_channel(rcept_no) == channel
    assert is_exchange_filing(rcept_no) is exchange


def test_kind_number_only_for_the_verified_kospi_series():
    assert kind_acptno_for("20260326803383") == "20260326003383"
    assert kind_acptno_for("20261014900662") is None
    assert kind_acptno_for("20260312000987") is None


@pytest.mark.parametrize("rcept_no, source_type, channel", [
    ("20261014900662", "kind_html", "krx_kosdaq"),
    ("20260312600987", "kind_html", "krx_konex"),
    ("20260326803383", "kind_html", "krx_kospi"),
    ("20260312000987", "dart_xml", "dart"),
])
def test_evidence_source_type_follows_the_channel(rcept_no, source_type, channel):
    payload = asyncio.run(build_evidence_payload(rcept_no=rcept_no))
    assert payload["data"]["source_type"] == source_type
    assert payload["data"]["filing_channel"] == channel


def test_meeting_result_reference_reports_kosdaq_channel():
    ref = _result_reference({"rcept_no": "20260916900146", "report_nm": "임시주주총회결과",
                             "rcept_dt": "20260916"})
    assert ref["filing_channel"] == "krx_kosdaq"
    assert ref["dart_fetchable"] is True and ref["kind_acptno"] is None
    kospi = _result_reference({"rcept_no": "20260326803383", "report_nm": "정기주주총회결과",
                               "rcept_dt": "20260326"})
    assert kospi["kind_acptno"] == "20260326003383" and kospi["whitelist_ok"] is True


_CHANGE_HTML = (Path(__file__).parent / "dart_responses" / "dart"
                / "change_filing_officer_buy_20260430801870.html").read_text("utf-8")


class _Client:
    def __init__(self, rcepts, docs):
        self.rcepts, self.docs, self.seen = rcepts, docs, []

    async def search_filings(self, **kw):
        return {"list": [{"rcept_no": rn, "rcept_dt": rn[:8], "report_nm": "최대주주등소유주식변동신고서"}
                         for rn in self.rcepts]}

    async def get_document_cached(self, rn):
        self.seen.append(("dart", rn))
        return {"html": self.docs.get(rn, "")}

    async def kind_fetch_document(self, acptno):
        self.seen.append(("kind", acptno))
        raise DartClientError("013", "없음")


def test_kosdaq_change_filing_is_read_not_skipped():
    """코스닥(9계열) 변동신고서도 DART 원문으로 읽는다. 같은 서식 원문을 9계열 번호에 물렸다."""
    client = _Client(["20260430901870"], {"20260430901870": _CHANGE_HTML})
    filings, warnings = asyncio.run(
        O._fetch_change_filings("00126937", date(2026, 1, 1), date(2026, 10, 18), client))
    assert [(f["rcept_no"], f["source"], f["status"]) for f in filings] == [
        ("20260430901870", "dart_api", "parsed")]
    assert warnings == []


def test_kosdaq_change_filing_without_dart_body_does_not_guess_a_kind_number():
    client = _Client(["20260430901870"], {})
    filings, warnings = asyncio.run(
        O._fetch_change_filings("00126937", date(2026, 1, 1), date(2026, 10, 18), client))
    assert filings == [] and not any(kind == "kind" for kind, _ in client.seen)
    assert len(warnings) == 1 and "20260430901870" in warnings[0]
