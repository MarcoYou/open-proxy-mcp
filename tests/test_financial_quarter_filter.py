from datetime import date

from open_proxy_mcp.services.financial_metrics import _quarterly_status


def test_missing_q4_after_due_date_is_explicitly_unsubmitted():
    rows = [
        {"fiscal_year": 2026, "fiscal_quarter": "Q1"},
        {"fiscal_year": 2026, "fiscal_quarter": "Q2"},
        {"fiscal_year": 2026, "fiscal_quarter": "Q3"},
    ]

    status = _quarterly_status(rows, 2026, 6, as_of=date(2026, 10, 1))

    assert status["missing"] == [{
        "fiscal_quarter": "Q4",
        "period_end": "2026-06-30",
        "status": "미제출",
    }]


def test_missing_q4_after_period_end_is_unsubmitted_even_before_statutory_deadline():
    status = _quarterly_status([], 2026, 6, as_of=date(2026, 8, 24))

    q4 = next(item for item in status["missing"] if item["fiscal_quarter"] == "Q4")
    assert q4["period_end"] == "2026-06-30"
    assert q4["status"] == "미제출"


def test_future_quarters_are_not_reported_as_missing():
    status = _quarterly_status([
        {"fiscal_year": 2026, "fiscal_quarter": "Q1"},
        {"fiscal_year": 2026, "fiscal_quarter": "Q2"},
    ], 2026, 12, as_of=date(2026, 8, 24))

    assert status["missing"] == []
