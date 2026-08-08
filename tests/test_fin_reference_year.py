"""재무 기준연도는 **주총일 시점에 이미 제출된 가장 최근 사업보고서**다.

종전에는 `주총연도 − 2`로 못박았다. 그 값이 맞는 이유는 3월 정기주총 일정에 있다 — 그때는
FY(N-1) 사업보고서가 아직 안 나왔으니 마지막 확정치가 FY(N-2)다. 하지만 그건 일정에서 나온
어림이지 규칙이 아니어서, 연중에 열리는 임시주총에서는 한 해 과하게 보수적이었다. 8월 임시주총은
그해 3월 제출된 FY(N-1) 사업보고서를 이미 볼 수 있는데 2년 전 숫자로 자본잠식·배당을 판단했다.

「그 시점에 제출돼 있었나」로 되돌리면 특례 분기가 없어지고 look-ahead 도 정의상 막힌다.
"""

from __future__ import annotations

import asyncio

import pytest

from open_proxy_mcp.services import financial_metrics as fm


class _FakeClient:
    def __init__(self, rows):
        self._rows = rows
        self.calls = []

    async def search_filings(self, **kw):
        self.calls.append(kw)
        return {"list": self._rows}


def _row(rcept_dt: str, report_nm: str, rcept_no: str = "X"):
    return {"rcept_dt": rcept_dt, "report_nm": report_nm, "rcept_no": rcept_no}


@pytest.fixture
def client(monkeypatch):
    def _install(rows):
        c = _FakeClient(rows)
        monkeypatch.setattr(fm, "get_dart_client", lambda: c)
        return c
    return _install


def test_a_march_agm_still_sees_only_two_years_back(client) -> None:
    """정기주총 답은 바뀌면 안 된다 — FY(N-1) 사업보고서는 주총 뒤에 나온다."""
    client([_row("20250311", "사업보고서 (2024.12)")])
    got = asyncio.run(fm.latest_annual_report_before("00126380", "20260324"))
    assert got["fiscal_year"] == 2024


def test_a_mid_year_egm_sees_the_year_that_was_already_filed(client) -> None:
    """8월 임시주총 — 그해 3월 제출된 FY(N-1)을 쓸 수 있다. 여기가 종전에 한 해 뒤처지던 곳."""
    client([_row("20250311", "사업보고서 (2024.12)"),
            _row("20260310", "사업보고서 (2025.12)")])
    got = asyncio.run(fm.latest_annual_report_before("00126380", "20260814"))
    assert got["fiscal_year"] == 2025


def test_reports_filed_after_the_meeting_are_invisible(client) -> None:
    """주총 뒤에 나온 보고서를 쓰면 그때는 알 수 없던 정보로 그때의 판단을 채점하게 된다."""
    client([_row("20260310", "사업보고서 (2025.12)")])
    assert asyncio.run(fm.latest_annual_report_before("00126380", "20260301")) is None


def test_a_non_december_close_is_left_blank_not_guessed(client) -> None:
    """리츠는 사업보고서가 3~6개월마다 나온다 — 라벨의 연도가 사업연도 번호라는 보장이 없다.

    실측 SK리츠: 사업보고서 (2025.03)·(2025.06)·(2025.09)·(2025.12)·(2026.03).
    모르는 것을 추측하느니 비워서 호출측이 종전 기준으로 물러나게 한다.
    """
    client([_row("20260610", "사업보고서 (2026.03)"),
            _row("20251210", "사업보고서 (2025.09)")])
    got = asyncio.run(fm.latest_annual_report_before("01535150", "20260701"))
    assert got["report_nm"] == "사업보고서 (2026.03)"   # 문서 자체는 찾되
    assert got["fiscal_year"] is None                  # 연도는 단정하지 않는다


def test_half_year_reports_are_not_mistaken_for_annual(client) -> None:
    """반기·분기보고서는 사업보고서가 아니다."""
    client([_row("20260814", "반기보고서 (2026.06)"),
            _row("20260310", "사업보고서 (2025.12)")])
    got = asyncio.run(fm.latest_annual_report_before("00126380", "20260901"))
    assert got["fiscal_year"] == 2025


def test_a_lookup_failure_is_not_reported_as_absence(client, monkeypatch) -> None:
    """조회 실패를 「없음」으로 위장하면 호출측이 잘못된 확신을 갖는다."""
    class _Broken:
        async def search_filings(self, **kw):
            raise RuntimeError("DART down")
    monkeypatch.setattr(fm, "get_dart_client", lambda: _Broken())
    assert asyncio.run(fm.latest_annual_report_before("00126380", "20260814")) is None


def test_it_refuses_a_malformed_date(client) -> None:
    c = client([_row("20260310", "사업보고서 (2025.12)")])
    assert asyncio.run(fm.latest_annual_report_before("00126380", "2026-08-14")) is None
    assert asyncio.run(fm.latest_annual_report_before("", "20260814")) is None
    assert c.calls == []                               # 못 쓸 입력으로 DART 를 부르지 않는다
