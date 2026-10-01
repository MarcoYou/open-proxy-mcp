"""공유 날짜·금액 헬퍼의 기존 입력/출력 경계와 모듈별 별칭을 보존한다."""

from copy import deepcopy

import pytest

from open_proxy_mcp.services import corporate_restructuring, date_utils, dilutive_issuance
from open_proxy_mcp.tools import (
    dividend_data,
    dividend_disclosure,
    forward_estimates_data,
    order_contracts,
    treasury_share,
)
from open_proxy_mcp.tools._shared import krw_scaled, krw_short, krw_with_raw


@pytest.mark.parametrize(
    "normalize",
    [
        date_utils.normalize_row_dates,
        dilutive_issuance._normalize_row_dates,
        corporate_restructuring._normalize_row_dates,
    ],
)
def test_row_dates_mutate_only_matching_fields_and_nested_dicts(normalize):
    nested = {"payment_date": "2026.2.11", "deep": {"end_date": "20260212"}}
    untouched_list = [{"payment_date": "2026년 2월 11일"}]
    row = {
        "board_date": " 2026년 02월 11일 ",
        "schedule": nested,
        "items": untouched_list,
        "rcept_dt": "20260211",
        "date": "20260211",
        "board_date_note": "20260211",
        "numeric_date": 20260211,
        "missing_date": None,
        "empty_date": "",
        "unknown_date": " 미정 ",
    }
    expected = {
        **deepcopy(row),
        "board_date": "2026-02-11",
        "schedule": {"payment_date": "2026-02-11", "deep": {"end_date": "2026-02-12"}},
    }

    assert normalize(row) is None
    assert row == expected
    assert row["schedule"] is nested
    assert row["items"] is untouched_list
    assert normalize(row) is None
    assert row == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026년 2월 1일", "2026-02-01"),
        ("2026.02.11", "2026-02-11"),
        ("2026/2/11", "2026-02-11"),
        ("2026-2-11", "2026-02-11"),
        (" 20260211 ", "2026-02-11"),
        ("2026-02-11", "2026-02-11"),
        # 기존 동작은 형식 정규화만 한다. 달력 검증이나 파싱 확장은 하지 않는다.
        ("2026년 13월 40일", "2026-13-40"),
        ("20260230", "2026-02-30"),
        ("2026.02.11.", "2026.02.11."),
        ("20260211000000", "20260211000000"),
        ("2026-02-11T00:00:00", "2026-02-11T00:00:00"),
        (" 2026년 2월 ", " 2026년 2월 "),
        ("   ", "   "),
    ],
)
def test_row_date_format_boundaries(value, expected):
    row = {"payment_date": value}
    date_utils.normalize_row_dates(row)
    assert row == {"payment_date": expected}


def test_legacy_names_are_shared_helper_aliases():
    assert dilutive_issuance._normalize_row_dates is date_utils.normalize_row_dates
    assert corporate_restructuring._normalize_row_dates is date_utils.normalize_row_dates
    assert order_contracts._won is krw_with_raw
    assert treasury_share._won is krw_with_raw
    assert dividend_disclosure._won is krw_with_raw
    assert forward_estimates_data._won is krw_short
    assert dividend_data._won_short is krw_short


@pytest.mark.parametrize(
    "format_amount",
    [krw_with_raw, order_contracts._won, treasury_share._won, dividend_disclosure._won],
)
@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (None, "-"),
        (0, "-"),
        (-0.0, "-"),
        (1, "1원"),
        (99_999_999, "99,999,999원"),
        (100_000_000, "1억원 (100,000,000원)"),
        (150_000_000, "2억원 (150,000,000원)"),
        (250_000_000, "2억원 (250,000,000원)"),
        (999_999_999_999, "10,000억원 (999,999,999,999원)"),
        (1_000_000_000_000, "1.00조원 (1,000,000,000,000원)"),
        (1_234_567_890_123, "1.23조원 (1,234,567,890,123원)"),
        (9_007_199_254_740_993, "9007.20조원 (9,007,199,254,740,993원)"),
        (-100_000_000, "-100,000,000원"),
        (-1_234_567_890_123, "-1,234,567,890,123원"),
        (1234.5, "1,234.5원"),
    ],
)
def test_krw_with_raw_keeps_zero_negative_and_exact_precision(format_amount, amount, expected):
    assert format_amount(amount) == expected


@pytest.mark.parametrize(
    "format_amount", [krw_short, forward_estimates_data._won, dividend_data._won_short]
)
@pytest.mark.parametrize(
    ("amount", "expected"),
    [
        (None, "-"),
        (0, "0원"),
        (-0.0, "-0원"),
        (1, "1원"),
        (99_999_999, "99,999,999원"),
        (100_000_000, "1억원"),
        (150_000_000, "2억원"),
        (250_000_000, "2억원"),
        (999_999_999_999, "10,000억원"),
        (1_000_000_000_000, "1.00조원"),
        (1_234_567_890_123, "1.23조원"),
        (10_555_000_000_000, "10.55조원"),
        (9_007_199_254_740_993, "9,007.20조원"),
        (-100_000_000, "-1억원"),
        (-1_234_567_890_123, "-1.23조원"),
        (1234.5, "1,234원"),
        ("1234567890123", "1.23조원"),
    ],
)
def test_krw_short_keeps_zero_signed_units_and_rounding(format_amount, amount, expected):
    assert format_amount(amount) == expected


def test_similar_but_distinct_formatters_are_not_merged():
    # 크기별 소수 자릿수가 다른 기존 헬퍼와 배당 원장용 원 금액 병기는 별개다.
    assert krw_scaled(10_555_000_000_000) == "10.6조원"
    assert krw_short(10_555_000_000_000) == "10.55조원"
    assert dividend_data._won(0) == "0원"
    assert dividend_data._won(-100_000_000) == "-1억원 (-100,000,000원)"
    assert dividend_data._won is not krw_with_raw
