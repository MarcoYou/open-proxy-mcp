"""정정 칸 시점 규칙(`services/restated_pit.py`) 순수 함수 — 261020 p1g pit-restated. DB·네트워크 0.

실제 SQL·서버 곡선·두 과거 백필의 같은 규칙은 `tests/test_restated_pit_pg.py`(Postgres 17)."""
from __future__ import annotations

from datetime import date

import pytest

from open_proxy_mcp.services import report_calendar as rc
from open_proxy_mcp.services import restated_pit as R

TODAY = date(2026, 10, 20)


# ── 출처 접수일 ─────────────────────────────────────────────────────────────

def test_rcept_day_is_the_first_eight_digits():
    assert R.rcept_day("20260318001521") == "20260318"
    assert R.rcept_day(None) is None and R.rcept_day("") is None and R.rcept_day("2026") is None and R.rcept_day("abcd0318001521") is None


def test_why_rcept_day_reads_the_source_receipt_in_the_reason_cell():
    why = ("전기 칸 보충(261019) — DART 재무제표 API 에 이 칸의 보고서가 없어(013) 다음 해 같은 분기 보고서(2020 11012 CFS, 접수 20200814001642)의 "
           "전년 동기 값: 순이익 CIS_ID(전기 누적)")
    assert R.why_rcept_day(why) == "20200814"
    assert R.why_rcept_day("결산기 12개월(261020) 공시 20210319 — …") is None      # 결산기 칸 공시일은 따로(`period_cells.avail_of`)
    assert R.why_rcept_day(None) is None


# ── 보수적 추정 공시일 ───────────────────────────────────────────────────────

def test_annual_estimate_prefers_the_report_two_years_later_when_it_is_out():
    """옛 정정치 백필은 한 보고서에서 전기·전전기를 함께 썼다 — fy+2 사업보고서 공시 시점이 지났으면 그날."""
    assert R.annual_estimate(2022, 12, TODAY) == "20250401"     # 2024 사업보고서
    assert R.annual_estimate(2023, 12, TODAY) == "20260401"     # 2025 사업보고서
    assert R.annual_estimate(2024, 12, TODAY) == "20260401"     # 2026 사업보고서는 아직 → 2025 사업보고서
    assert R.annual_estimate(2025, 12, TODAY) is None           # 어느 쪽도 아직 — 지금 있는 값의 출처일 수 없다(문 없음)


def test_annual_estimate_follows_the_fiscal_calendar():
    assert R.annual_estimate(2022, 3, TODAY) == rc.cell_available(2024, 4, 3).strftime("%Y%m%d") == "20240701"
    foreign = rc.fiscal(6, "900000")                             # 12월 결산이 아닌 외국 상장사 — 사업보고서 120일
    assert R.annual_estimate(2022, foreign, TODAY) == rc.cell_available(2024, 4, foreign).strftime("%Y%m%d") == "20241101"


def test_quarter_estimate_is_the_next_years_same_quarter_report():
    assert R.quarter_estimate(2023, 2, 12, TODAY) == "20240814"
    assert R.quarter_estimate(2025, 3, 12, TODAY) is None        # 2026 3분기보고서는 11/14 — 아직


# ── 칸 하나 → (지금 값, 문) ──────────────────────────────────────────────────

def test_restated_cell_with_a_source_opens_on_its_filing_date():
    now, g = R.annual_cell(2023, 107.05e8, 5640.1e8, 107.05e8, 9277.1e8, "20260318001521", 12, TODAY)
    assert now == (107.05e8, 9277.1e8)                           # 지금 값 = 정정 칸(종전 그대로)
    assert g == ("20260318", (107.05e8, 5640.1e8))               # 그 전 = 원본(순이익은 같은 값이라 그대로)


def test_no_gate_when_nothing_differs():
    assert R.annual_cell(2023, 1.0, 2.0, None, None, None, 12, TODAY) == ((1.0, 2.0), None)
    assert R.annual_cell(2023, 1.0, 2.0, 1.0, 2.0, "20260318001521", 12, TODAY) == ((1.0, 2.0), None)


def test_fills_and_unit_fixes_are_restated_from_the_start():
    assert R.annual_cell(2022, None, None, 3.0e9, 3.0e10, None, 12, TODAY) == ((3.0e9, 3.0e10), None)            # 보충값
    assert R.annual_cell(2025, -17858891000.0, 11988390000000.0, -17858891.0, 11988390000.0, None, 12, TODAY) \
        == ((-17858891.0, 11988390000.0), None)                                                                 # 0091W0 1,000배
    assert R.annual_cell(2022, 1.0e15, 5e13, 1.0e9, 5e10, None, 12, TODAY)[1] is None                           # 100만 배
    now, g = R.annual_cell(2022, None, 5e10, 1.0e9, 6e10, None, 12, TODAY)                                      # 순이익만 보충 — 자본은 문
    assert now == (1.0e9, 6e10) and g == ("20250401", (1.0e9, 5e10))


def test_a_ten_times_or_sign_change_is_a_restatement_not_a_unit_fix():
    """10배(100배 미만)·부호가 바뀐 같은 크기는 단위 고침이 아니다 — 재작성으로 보고 출처 공시일부터."""
    assert R.annual_cell(2022, 10.0, 100.0, 100.0, 100.0, None, 12, TODAY)[1] == ("20250401", (10.0, 100.0))
    assert R.annual_cell(2022, 497.18e8, 1.0, -497.18e8, 1.0, None, 12, TODAY)[1] == ("20250401", (497.18e8, 1.0))


def test_unknown_source_without_an_estimate_keeps_the_restated_value():
    """추정일이 오늘 뒤(지금 있는 값의 출처가 다음 해 보고서일 수 없다) — 문 없음: 최신 응답이 바뀌지 않는다."""
    assert R.annual_cell(2025, 10.0, 100.0, 12.0, 100.0, None, 12, TODAY) == ((12.0, 100.0), None)


def test_quarter_cell_uses_the_reason_receipt_or_the_estimate():
    assert R.quarter_cell(2023, 2, 70.0, 1050.0, 50.0, None, "20240816", 12, TODAY) == ((50.0, 1050.0), ("20240816", (70.0, 1050.0)))
    assert R.quarter_cell(2023, 2, 70.0, 1050.0, 50.0, None, None, 12, TODAY)[1] == ("20240814", (70.0, 1050.0))
    assert R.quarter_cell(2019, 2, None, None, 3.8e10, 1.4e12, "20200814", 12, TODAY) == ((3.8e10, 1.4e12), None)   # 전기 칸 보충


def test_marked_gate_means_no_value_before_the_filing_date():
    assert R.marked_gate("20210319") == ("20210319", (None, None))
    assert R.marked_gate(None) is None


# ── 그 날 값(`at`) ───────────────────────────────────────────────────────────

def test_at_swaps_only_the_closed_cells_and_reuses_the_view():
    f = R.PitFin({2023: (1.0, 9.0), 2024: (2.0, 8.0), 2025: (3.0, 7.0)})
    f.gate(2023, ("20260318", (1.0, 5.0)))
    f.gate(2024, ("20260318", (4.0, 6.0)))
    assert R.at(f, "20260320") is f                              # 다 열림 — 그대로(복사 없음)
    v = R.at(f, "20240628")
    assert v == {2023: (1.0, 5.0), 2024: (4.0, 6.0), 2025: (3.0, 7.0)} and v is not f
    assert R.at(f, "2024-06-28") is v                            # 같은 닫힘 조합 = 같은 사본 · 날짜 꼴 무관
    assert R.at(v, "20240628") is v                              # 사본은 문이 없다(두 번 적용해도 같다)
    assert f[2023] == (1.0, 9.0)                                 # 원본 dict 는 지금 값 그대로
    assert R.at(f, "20260318") is f                              # 공시 당일부터 연다


def test_at_keeps_attached_attributes_and_passes_plain_dicts_through():
    class Q(R.PitFin):
        cells: dict = {}
    q = Q({(2023, 2): (50.0, 1.0)})
    q.cells = {(2023, 2): "20230820"}
    q.gate((2023, 2), ("20240816", (70.0, 1.0)))
    v = R.at(q, "20231020")
    assert isinstance(v, Q) and v.cells == {(2023, 2): "20230820"} and v[(2023, 2)] == (70.0, 1.0)
    plain = {2023: (1.0, 2.0)}
    assert R.at(plain, "20200101") is plain


def test_gate_none_removes_a_gate():
    f = R.PitFin({2023: (1.0, 9.0)})
    f.gate(2023, ("20260318", (1.0, 5.0)))
    assert R.at(f, "20240101")[2023] == (1.0, 5.0)
    f.gate(2023, None)
    assert R.at(f, "20240101") is f


@pytest.mark.parametrize("fy", range(2016, 2027))
def test_latest_view_is_always_open(fy):
    """지금(오늘) 시점에는 모든 문이 열려 있다 — 출처 있는 칸은 과거 접수일, 추정일은 오늘 이하만 문으로 단다."""
    for args in ((fy, 1.0, 2.0, 1.5, 2.5, None), (fy, 1.0, 2.0, 1.5, 2.5, "20251231000001")):
        _, g = R.annual_cell(*args, fye=12, today=TODAY)
        assert g is None or g[0] <= TODAY.strftime("%Y%m%d")
