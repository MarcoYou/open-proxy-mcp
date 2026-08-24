from open_proxy_mcp.services.fiscal_period import (
    fiscal_quarter_from_end,
    fiscal_year_from_end,
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
