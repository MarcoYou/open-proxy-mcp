"""임기 칸의 보수적 정규화. 공시상 기간을 재직 사실이나 추산 만료일로 바꾸지 않는다."""
import pytest

from open_proxy_mcp.services.personnel_term import parse_appointment_term


@pytest.mark.parametrize("raw,months", [("3년", 36), ("1년 6개월", 18), ("18개월", 18), ("임기: 2년", 24), (" 1 년\n6 개월 ", 18)])
def test_duration_preserves_raw_without_inventing_dates(raw, months):
    d = parse_appointment_term(raw)
    assert d["raw"] == raw
    assert d["duration_months"] == months
    assert d["start"] is None and d["end"] is None
    assert d["parse_status"] == "parsed" and not d["requires_review"]


@pytest.mark.parametrize("raw,start,end,sp,ep", [
    ("2026.3.1 ~ 2029.3.1", "2026-03-01", "2029-03-01", "day", "day"),
    ("2026년 3월 1일부터 2029년 3월 1일까지", "2026-03-01", "2029-03-01", "day", "day"),
    ("2026.03~2029.03", "2026-03", "2029-03", "month", "month"),
    ("2026-03-01-2029-03-01", "2026-03-01", "2029-03-01", "day", "day"),
    ("2026년~2029년", "2026", "2029", "year", "year"),
    ("2026/3 ~ 2029/3/31", "2026-03", "2029-03-31", "month", "day"),
])
def test_dates_keep_precision(raw, start, end, sp, ep):
    d = parse_appointment_term(raw)
    assert (d["start"], d["end"], d["start_precision"], d["end_precision"]) == (start, end, sp, ep)
    assert d["duration_months"] is None
    assert d["parse_status"] == "parsed"


@pytest.mark.parametrize("raw", ["2026.2.30~2029.3.1", "2026.13~2029.3", "2029.3~2026.3", "0년", "0개월"])
def test_invalid_keeps_no_structured_claim(raw):
    d = parse_appointment_term(raw)
    assert d["parse_status"] == "invalid" and d["requires_review"]
    assert d["start"] is None and d["end"] is None and d["duration_months"] is None


@pytest.mark.parametrize("raw", [None, "", "미정", "-", "해당사항 없음"])
def test_missing_is_not_zero(raw):
    d = parse_appointment_term(raw)
    assert d["parse_status"] == "missing" and d["duration_months"] is None


@pytest.mark.parametrize("raw", ["3", "1.5년", "3년 또는 2년", "3년 이내", "2026.3~현재", "전임자", "3년(후임자는 2년)"])
def test_unresolved_is_not_guessed(raw):
    d = parse_appointment_term(raw)
    assert d["parse_status"] == "unparsed" and d["requires_review"]
    assert d["duration_months"] is None


@pytest.mark.parametrize("raw,months", [("3년(주1)", 36), ("3년(정기주주총회 종결 시까지)", 36), ("2029년 정기주총 종결 시까지", None), ("전임자의 잔여 임기", None)])
def test_conditions_are_not_exact_end_dates(raw, months):
    d = parse_appointment_term(raw)
    assert d["parse_status"] == "partial" and d["requires_review"]
    assert d["duration_months"] == months and d["end"] is None
    assert d["raw"] == raw and d["warnings"]


def test_bare_number_requires_explicit_unit():
    assert parse_appointment_term("3")["duration_months"] is None
    assert parse_appointment_term("3", unit="년")["duration_months"] == 36
    assert parse_appointment_term("3", unit="개월")["duration_months"] == 3


def test_input_types_and_units():
    with pytest.raises(TypeError):
        parse_appointment_term(3)
    with pytest.raises(ValueError):
        parse_appointment_term("3", unit="추정")
