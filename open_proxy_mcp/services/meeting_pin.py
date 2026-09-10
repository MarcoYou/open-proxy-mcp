"""Opt-in, request-local identity of one verified shareholder meeting notice.

The pin selects a receipt, not the latest notice for a company/year. It contains
only immutable source metadata and never persists a user's evaluation.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import date, datetime
import hashlib
import json
import re
from typing import Any, Iterator


def _document_digest(document: dict[str, Any]) -> str:
    content = {key: document.get(key) or "" for key in ("text", "html")}
    return hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class MeetingPin:
    corp_code: str
    notice_rcept_no: str
    meeting_date: date
    meeting_type: str
    year: int
    published: str
    report_name: str
    filer_name: str
    meeting_datetime: str
    source_sha256: str
    meeting_term: str | None = None
    location: str | None = None
    is_correction: bool = False

    @property
    def digest(self) -> str:
        return hashlib.sha256(json.dumps(
            asdict(self), ensure_ascii=False, sort_keys=True,
            default=lambda value: value.isoformat(),
        ).encode()).hexdigest()

    def filing_row(self) -> dict[str, Any]:
        return {"corp_code": self.corp_code, "rcept_no": self.notice_rcept_no,
                "rcept_dt": self.published, "report_nm": self.report_name,
                "flr_nm": self.filer_name}

    def notice_row(self) -> dict[str, Any]:
        return {"rcept_no": self.notice_rcept_no, "report_name": self.report_name,
                "disclosure_date": self.published, "filer_name": self.filer_name,
                "meeting_type": {"annual": "정기", "extraordinary": "임시"}[self.meeting_type],
                "meeting_term": self.meeting_term, "datetime": self.meeting_datetime,
                "location": self.location, "is_correction": self.is_correction}

    def verify_document(self, document: dict[str, Any]) -> None:
        if _document_digest(document) != self.source_sha256:
            raise ValueError("meeting_pin: notice content changed; prepare a new pin")


_MEETING_PIN: ContextVar[MeetingPin | None] = ContextVar("opm_meeting_pin", default=None)


def get_meeting_pin() -> MeetingPin | None:
    return _MEETING_PIN.get()


def get_matching_pin(corp_code: str, year: int | None = None,
                     meeting_type: str = "auto", *, scope: str = "") -> MeetingPin | None:
    pin = get_meeting_pin()
    if (pin is None or scope == "results" or pin.corp_code != corp_code
            or (year and pin.year != year)
            or meeting_type not in {"auto", pin.meeting_type}):
        return None
    return pin


@contextmanager
def use_meeting_pin(pin: MeetingPin) -> Iterator[MeetingPin]:
    if not isinstance(pin, MeetingPin):
        raise ValueError("meeting_pin: invalid pin")
    token = _MEETING_PIN.set(pin)
    try:
        yield pin
    finally:
        _MEETING_PIN.reset(token)


def _valid_date(value: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}", value):
        raise ValueError("meeting_pin: invalid date")
    try:
        return datetime.strptime(value, "%Y%m%d").date()
    except ValueError:
        raise ValueError("meeting_pin: invalid date") from None


async def prepare_meeting_pin(client: Any, corp_code: str, notice_rcept_no: str,
                              meeting_type: str = "auto", year: int | None = None,
                              as_of: str = "") -> MeetingPin:
    """Verify company membership, public availability and the actual notice round.

    ``as_of`` is the last permitted whole day supplied by the strict cutoff
    caller. Unknown dates/types are rejected, never guessed from receipt/year.
    The exact-day search includes original filings, even if corrected later.
    """
    if (not isinstance(corp_code, str) or not re.fullmatch(r"\d{8}", corp_code)
            or not isinstance(notice_rcept_no, str) or not re.fullmatch(r"\d{14}", notice_rcept_no)
            or meeting_type not in {"auto", "annual", "extraordinary"}
            or (year is not None and (type(year) is not int or not 1 <= year <= 9999))):
        raise ValueError("meeting_pin: invalid request")
    cutoff = _valid_date(as_of)
    receipt_date = _valid_date(notice_rcept_no[:8])
    if receipt_date > cutoff:
        raise ValueError("meeting_pin: notice was not available by the cutoff")

    try:
        found = None
        for page in range(1, 6):
            payload = await client.search_filings(
                corp_code=corp_code, bgn_de=notice_rcept_no[:8], end_de=notice_rcept_no[:8],
                pblntf_detail_ty="E006", last_reprt_at="N", page_no=page, page_count=100,
            )
            for row in payload.get("list") or []:
                if row.get("rcept_no") == notice_rcept_no:
                    found = row
                    break
            if found or page * 100 >= int(payload.get("total_count") or 0):
                break
        if (not found or found.get("corp_code") != corp_code
                or "주주총회소집공고" not in re.sub(r"\s+", "", found.get("report_nm") or "")
                or _valid_date(found.get("rcept_dt") or "") != receipt_date):
            raise ValueError("unverified notice")
        document = await client.get_document_cached(notice_rcept_no)
        text, html = document.get("text") or "", document.get("html") or ""
        # Reuse the existing XML/text parser without introducing a second parser
        # or reaching a live viewer whose publication state is unverified here.
        from open_proxy_mcp.services.shareholder_meeting import _notice_section_slice, _parse_notice_meeting_date
        from open_proxy_mcp.services.shareholder_meeting_parser import parse_meeting_info_xml

        info = await asyncio.to_thread(parse_meeting_info_xml, text, html=_notice_section_slice(html) or html)
        actual_date = _parse_notice_meeting_date(info.get("datetime") or "")
        actual_type = {"정기": "annual", "임시": "extraordinary"}.get(info.get("meeting_type"))
        if (actual_date is None or actual_type is None or info.get("meeting_type_conflict")
                or (meeting_type != "auto" and meeting_type != actual_type)
                or (year is not None and actual_date.year != year)):
            raise ValueError("unverified meeting")
    except Exception:
        # Never echo client URLs, credentials, source text or exception details.
        raise ValueError("meeting_pin: company notice and meeting identity could not be verified") from None

    return MeetingPin(
        corp_code=corp_code, notice_rcept_no=notice_rcept_no, meeting_date=actual_date,
        meeting_type=actual_type, year=actual_date.year, published=found["rcept_dt"],
        report_name=found["report_nm"], filer_name=found.get("flr_nm") or found.get("corp_name") or "",
        meeting_datetime=info["datetime"], source_sha256=_document_digest(document),
        meeting_term=info.get("meeting_term"), location=info.get("location"),
        is_correction=bool(info.get("is_correction")),
    )
