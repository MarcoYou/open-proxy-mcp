"""`filings_found`(검색된 보고서 수) 와 `filing_count`(status 산출용 사건 수) 는 세는 대상이 다르다.

정상 경로에서만 값이 같아 보여 중복으로 오인하기 쉽다. 한쪽을 지우면 금융회사 연차보고서
서식처럼 `filing_count` 가 0 인 회사에서 이력 건수가 0 건으로 표시된다.
"""

from __future__ import annotations

from open_proxy_mcp.tools.corp_gov_report import _render


def _payload(filings_found: int, filing_count: int) -> dict:
    return {
        "status": "no_filing",
        "subject": "KB금융",
        "warnings": [],
        "data": {
            "canonical_name": "KB금융",
            "market": "KOSPI",
            "mandatory": True,
            "filings_found": filings_found,
            "filing_count": filing_count,
            "report_meta": {"rcept_no": "20260601800001", "rcept_dt": "20260601"},
            "company_overview": {},
            "usage": {"dart_api_calls": 2, "mcp_tool_calls": 1},
        },
    }


def test_history_count_survives_when_the_status_count_is_zero() -> None:
    rendered = _render(_payload(filings_found=1, filing_count=0), "summary")
    assert "총 1건 이력" in rendered


def test_history_count_reads_the_search_result_not_the_status_count() -> None:
    rendered = _render(_payload(filings_found=3, filing_count=3), "summary")
    assert "총 3건 이력" in rendered
