from open_proxy_mcp.services.fiscal_period import (
    fiscal_period_label,
    fiscal_quarter_from_end,
    fiscal_year_from_end,
    fiscal_year_span,
    normalize_period_end,
)


def test_normalize_dart_period_end_suffix():
    assert normalize_period_end("2025-06-30 현재") == "2025-06-30"
    assert normalize_period_end("2025.09.30") == "2025-09-30"


def test_march_close_maps_calendar_periods_to_next_fiscal_year():
    assert fiscal_year_from_end("2025-06-30 현재", 3) == 2026
    assert fiscal_quarter_from_end("2025-06-30 현재", 3) == 1
    assert fiscal_quarter_from_end("2025-12-31", 3) == 3
    assert fiscal_quarter_from_end("2026-03-31", 3) == 4


def test_june_close_maps_q1_to_q4_correctly():
    assert fiscal_year_from_end("2025-09-30", 6) == 2026
    assert [fiscal_quarter_from_end(end, 6) for end in (
        "2025-09-30", "2025-12-31", "2026-03-31", "2026-06-30"
    )] == [1, 2, 3, 4]


def test_fiscal_year_span_covers_the_right_twelve_months():
    assert fiscal_year_span(2025, 12) == ("2025-01-01", "2025-12-31")
    assert fiscal_year_span(2025, 6) == ("2024-07-01", "2025-06-30")
    assert fiscal_year_span(2025, 3) == ("2024-04-01", "2025-03-31")
    # 2월 결산 — 윤년 말일도 정확해야 한다.
    assert fiscal_year_span(2024, 2) == ("2023-03-01", "2024-02-29")


def test_fiscal_year_span_returns_none_without_close_month():
    assert fiscal_year_span(2025, None) is None
    assert fiscal_year_span(None, 6) is None
    assert fiscal_year_span(2025, 13) is None


def test_fiscal_period_label_marks_non_december_close():
    # 6월 결산은 결산월을 반드시 붙인다 — 라벨만 보면 1년을 통째로 오독한다.
    assert fiscal_period_label(2025, 6) == "2024-07-01~2025-06-30 · 6월 결산"
    # 12월 결산은 짧게 — 결산월 표기가 군더더기다.
    assert fiscal_period_label(2025, 12) == "2025-01-01~2025-12-31"
    assert fiscal_period_label(2025, None) is None
