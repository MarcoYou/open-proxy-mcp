"""주총 결과공시는 **회의 뒤에만** 그 회차의 결과다.

예전엔 그 해 전체에서 「회의일과 가장 가까운」 결과공시를 집었고, 거리에 부호가 없었다.
그래서 **아직 열리지 않은 회차에 지난 회차의 결과가 붙었다** — 실측 대림제지:
2026-09-04 임시주총 머리에 「결과 공시 확보」가 찍히고 참석률 73.1%는 3월 정기주총
결과(접수 20260326901639)로 계산됐다. 주총 **전** 판단에 사후 정보가 새는 자리라
`as_of` 통제가 그 자리에서 무너진다.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import open_proxy_mcp.services.shareholder_meeting as sm


def _run(notice, items):
    async def fake_search(**kwargs):
        return items, [], None

    orig = sm.search_filings_by_report_name
    sm.search_filings_by_report_name = fake_search  # type: ignore[assignment]
    try:
        return asyncio.run(sm._find_meeting_result_filing("00000000", 2026, notice))
    finally:
        sm.search_filings_by_report_name = orig  # type: ignore[assignment]


def _notice(meeting: date) -> dict:
    return {"datetime": f"{meeting.year}년 {meeting.month:02d}월 {meeting.day:02d}일 오전 9시"}


_PAST_RESULT = {"rcept_no": "20260326901639", "rcept_dt": "20260326",
                "report_nm": "정기주주총회결과"}


def test_future_meeting_has_no_result() -> None:
    """회의일이 아직 안 왔으면 결과공시는 존재할 수 없다."""
    future = date.today() + timedelta(days=30)
    filing, warn, _ = _run(_notice(future), [_PAST_RESULT])
    assert filing is None
    assert "아직" in (warn or "")


def test_earlier_filing_is_not_this_round_result() -> None:
    """회의 前 접수분은 이 회차의 결과가 아니다 — 지난 회차 결과다."""
    meeting = date.today() - timedelta(days=5)
    filing, warn, _ = _run(_notice(meeting), [_PAST_RESULT])
    assert filing is None
    assert "이후" in (warn or "")


def test_filing_right_after_meeting_is_accepted() -> None:
    """회의 당일·직후 접수분은 그 회차의 결과다."""
    meeting = date.today() - timedelta(days=5)
    same_day = meeting.strftime("%Y%m%d")
    filing, warn, _ = _run(_notice(meeting), [
        _PAST_RESULT,
        {"rcept_no": "2026" + "0" * 10, "rcept_dt": same_day, "report_nm": "임시주주총회결과"},
    ])
    assert filing is not None
    assert filing["rcept_dt"] == same_day
    assert warn is None


def test_far_later_filing_is_rejected() -> None:
    """창을 벗어난 뒤늦은 접수는 다음 회차의 것일 수 있다 — 붙이지 않는다."""
    meeting = date.today() - timedelta(days=200)
    late = (meeting + timedelta(days=sm._RESULT_WINDOW_DAYS + 5)).strftime("%Y%m%d")
    filing, warn, _ = _run(_notice(meeting), [
        {"rcept_no": "2026" + "1" * 10, "rcept_dt": late, "report_nm": "정기주주총회결과"},
    ])
    assert filing is None
    assert "이후" in (warn or "")
