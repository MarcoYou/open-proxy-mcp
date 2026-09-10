"""One verified receipt must survive every round-selection path (network zero)."""
from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError, replace
from datetime import date

import pytest

from open_proxy_mcp.services.meeting_pin import (
    get_matching_pin, get_meeting_pin, prepare_meeting_pin, use_meeting_pin,
)
import open_proxy_mcp.services.director_evaluation as directors
import open_proxy_mcp.services.shareholder_meeting as meetings


CORP = "00123456"
EARLY = "20260115000123"
LATER = "20260815000123"


def _document(meeting="2026년 2월 10일", kind="임시", agenda=True):
    body = f"""주주총회 소집공고
(제10기 {kind})
당사는 제10기 {kind}주주총회를 아래와 같이 개최합니다.
1. 일시 : {meeting} 오전 9시
2. 장소 : 본점 회의실
3. 회의 목적사항
"""
    if agenda:
        body += "[결의사항]\n제1호 의안 : 이사 보수한도 승인의 건\n4. 경영참고사항 비치\n"
    return {"text": body, "html": ""}


def _filing(receipt=EARLY, **updates):
    return {"corp_code": CORP, "corp_name": "검증회사", "flr_nm": "검증회사",
            "rcept_no": receipt, "rcept_dt": receipt[:8],
            "report_nm": "주주총회소집공고", **updates}


class Client:
    def __init__(self, *, rows=None, documents=None):
        self.rows = rows if rows is not None else [_filing(LATER), _filing()]
        self.documents = documents or {EARLY: _document(), LATER: _document("2026년 9월 10일")}
        self.searches = []
        self.reads = []
        self.viewer_reads = []

    async def search_filings(self, **kwargs):
        self.searches.append(kwargs)
        return {"list": self.rows, "total_count": len(self.rows)}

    async def get_document_cached(self, receipt):
        self.reads.append(receipt)
        return self.documents[receipt]

    async def get_viewer_document(self, *args, **kwargs):
        self.viewer_reads.append((args, kwargs))
        raise AssertionError("A pin must not change the verified source to a live viewer")


async def _pin(client, **kwargs):
    return await prepare_meeting_pin(client, CORP, EARLY, as_of="20260131", **kwargs)


def test_same_year_extraordinary_notice_is_pinned_across_all_readers(monkeypatch):
    async def run():
        client = Client()
        monkeypatch.setattr(meetings, "get_dart_client", lambda: client)
        monkeypatch.setattr(directors, "get_dart_client", lambda: client)
        pin = await _pin(client, meeting_type="extraordinary", year=2026)
        with use_meeting_pin(pin):
            resolved = await meetings.resolve_latest_meeting_year(CORP, meeting_type="auto", year=2026)
            candidate, alternatives, _, error, _ = await meetings._select_notice_candidate(
                CORP, 2026, "auto", "advise")
            parsed, _, source = await meetings._load_notice_bundle_with_fallback(
                candidate["notice"]["rcept_no"], scope="advise")
            _, appointment_receipt, meta = await directors.fetch_appointments(CORP, 2026, "auto")
        assert resolved["notice_rcept_no"] == candidate["notice"]["rcept_no"] == appointment_receipt == EARLY
        assert resolved["meeting_date"] == date(2026, 2, 10)
        assert source == "dart_xml" and "2026년 2월 10일" in parsed["text"]
        assert not alternatives and error is None
        assert candidate["result_filing"] is None
        assert meta[0]["selection_basis"] == "verified_pinned_notice"
        assert set(client.reads) == {EARLY}
        assert len(client.searches) == 1
        assert client.searches[0]["last_reprt_at"] == "N"
        assert client.viewer_reads == []
        assert get_meeting_pin() is None
    asyncio.run(run())


def test_empty_candidate_table_never_falls_through_to_another_round(monkeypatch):
    async def run():
        client = Client(documents={EARLY: _document(agenda=False), LATER: _document()})
        monkeypatch.setattr(directors, "get_dart_client", lambda: client)
        pin = await _pin(client)
        with use_meeting_pin(pin):
            appointments, receipt, _ = await directors.fetch_appointments(CORP, 2026, "auto")
        assert appointments == [] and receipt == EARLY
        assert set(client.reads) == {EARLY}
        assert len(client.searches) == 1
    asyncio.run(run())


@pytest.mark.parametrize("updates", [
    {"corp_code": "00999999"}, {"report_nm": "임시주주총회결과"},
    {"rcept_dt": "20260116"}, {"rcept_dt": ""},
])
def test_official_receipt_must_match_company_notice_and_date(updates):
    client = Client(rows=[_filing(**updates)])
    with pytest.raises(ValueError, match="could not be verified"):
        asyncio.run(_pin(client))
    assert client.reads == []


def test_future_receipt_is_rejected_before_reading_any_source():
    client = Client()
    with pytest.raises(ValueError, match="not available"):
        asyncio.run(prepare_meeting_pin(client, CORP, LATER, as_of="20260131"))
    assert client.searches == client.reads == []


@pytest.mark.parametrize("document,kwargs", [
    (_document("일시 미정"), {}),
    (_document("2026년 2월 30일"), {}),
    (_document(kind="구분 미상"), {}),
    (_document(), {"year": 2025}),
    (_document(), {"meeting_type": "annual"}),
    (_document(kind="정기") | {"text": _document(kind="정기")["text"].replace(
        "당사는 제10기 정기주주총회", "당사는 제10기 임시주주총회")}, {}),
])
def test_unknown_or_conflicting_actual_round_is_not_guessed(document, kwargs):
    client = Client(documents={EARLY: document})
    with pytest.raises(ValueError, match="could not be verified"):
        asyncio.run(_pin(client, **kwargs))


def test_date_boundary_uses_meeting_year_not_receipt_year():
    client = Client(documents={EARLY: _document("2027년 1월 10일")})
    pin = asyncio.run(_pin(client, year=2027))
    assert pin.year == 2027 and pin.meeting_date.year == 2027


def test_context_is_immutable_isolated_and_does_not_pin_prior_results():
    async def run():
        pin = await _pin(Client())
        later = replace(pin, notice_rcept_no=LATER, meeting_date=date(2026, 9, 10))
        with pytest.raises(FrozenInstanceError):
            pin.year = 2025
        assert pin.digest != later.digest

        async def worker(value):
            with use_meeting_pin(value):
                await asyncio.sleep(0)
                assert get_matching_pin(CORP, 2026, "auto") is value
                assert await asyncio.to_thread(get_meeting_pin) is value
                assert get_matching_pin(CORP, 2026, "auto", scope="results") is None
                assert get_matching_pin(CORP, 2025, "annual") is None
                assert get_matching_pin("00999999", 2026, "auto") is None
                with use_meeting_pin(later):
                    assert get_meeting_pin() is later
                assert get_meeting_pin() is value
            assert get_meeting_pin() is None

        await asyncio.gather(worker(pin), worker(later))
        assert get_meeting_pin() is None
        with pytest.raises(RuntimeError):
            with use_meeting_pin(pin):
                raise RuntimeError("caller cancelled")
        assert get_meeting_pin() is None
    asyncio.run(run())


def test_changed_document_is_rejected_by_both_consumers(monkeypatch):
    async def run():
        client = Client()
        monkeypatch.setattr(meetings, "get_dart_client", lambda: client)
        monkeypatch.setattr(directors, "get_dart_client", lambda: client)
        pin = await _pin(client)
        client.documents[EARLY] = _document("2026년 9월 10일")
        with use_meeting_pin(pin):
            with pytest.raises(ValueError, match="content changed"):
                await meetings._load_notice_bundle_with_fallback(EARLY, scope="advise")
            appointments, receipt, meta = await directors.fetch_appointments(CORP, 2026, "auto")
        assert not appointments and receipt == EARLY
        assert "error" in meta[0]
        assert LATER not in client.reads
    asyncio.run(run())


def test_client_failure_does_not_expose_exception_details():
    class Failing(Client):
        async def search_filings(self, **kwargs):
            raise RuntimeError("sensitive-url-and-query")
    with pytest.raises(ValueError) as result:
        asyncio.run(_pin(Failing()))
    assert "sensitive-url-and-query" not in str(result.value)


def test_missing_receipt_is_not_substituted_with_latest_notice():
    client = Client(rows=[_filing(LATER)])
    with pytest.raises(ValueError, match="could not be verified"):
        asyncio.run(_pin(client))
    assert client.reads == []


@pytest.mark.parametrize("as_of", ["", "20260230", "2026-01-31", "not-a-date"])
def test_invalid_cutoff_is_not_replaced_with_today(as_of):
    client = Client()
    with pytest.raises(ValueError, match="invalid date"):
        asyncio.run(prepare_meeting_pin(client, CORP, EARLY, as_of=as_of))
    assert client.searches == []
