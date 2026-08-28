# -*- coding: utf-8 -*-
"""FY 라벨이 어느 12개월인지 확정되는가 (2026-08-28 U 지적 B-6).

포시에스(6월 결산)는 회사 IR 문서가 「FY2025」를 2025-07-01~2026-06-30 으로 쓰고,
이 도구는 결산 **종료**연도로 부른다. 라벨만 같고 가리키는 기간이 다르면 「40% 배당 계획」이
어느 해 것인지 확정할 수 없다. 구간·결산월·라벨 기준을 항상 붙이고, 결산은 끝났는데 결의가
없는 사업연도는 지급 완료처럼 보이지 않게 「미결의」로 세운다.
"""
from __future__ import annotations

from datetime import date

from open_proxy_mcp.services.dividend import (
    FISCAL_YEAR_BASIS,
    _alot_multiyear_summaries,
    _fiscal_end_month,
    _fiscal_period,
    _latest_completed_fiscal_year,
)
from open_proxy_mcp.tools.dividend import _render


def test_fiscal_end_month_reads_settlement_date():
    assert _fiscal_end_month("2025-06-30") == 6
    assert _fiscal_end_month("2025-12-31") == 12
    assert _fiscal_end_month("") is None
    assert _fiscal_end_month(None) is None


def test_fiscal_period_spans_the_twelve_months_ending_in_that_year():
    assert _fiscal_period(2025, 6) == {"start": "2024-07-01", "end": "2025-06-30"}
    assert _fiscal_period(2025, 12) == {"start": "2025-01-01", "end": "2025-12-31"}
    assert _fiscal_period(2025, 3) == {"start": "2024-04-01", "end": "2025-03-31"}
    assert _fiscal_period(2024, 2) == {"start": "2023-03-01", "end": "2024-02-29"}  # 윤년
    # 결산월을 모르면 12월로 단정하지 않는다.
    assert _fiscal_period(2025, None) is None


def test_latest_completed_fiscal_year_tracks_the_settlement_month():
    assert _latest_completed_fiscal_year(date(2026, 8, 28), 6) == 2026
    assert _latest_completed_fiscal_year(date(2026, 6, 15), 6) == 2025   # 6월 결산 진행 중
    assert _latest_completed_fiscal_year(date(2026, 8, 28), 12) == 2025
    assert _latest_completed_fiscal_year(date(2026, 8, 28), None) is None


def test_multiyear_summaries_do_not_hardcode_december():
    """예전엔 stlm_dt 를 f"{fy}-12-31" 로 박아 6월 결산 회사 구간이 통째로 틀렸다."""
    latest = {
        "stlm_dt": "2025-06-30",
        "items": [
            {"category": "주당액면가액(원)", "stock_type": "-",
             "current": "500", "previous": "500", "before_previous": "500"},
            {"category": "주당 현금배당금(원)", "stock_type": "보통주",
             "current": "50", "previous": "50", "before_previous": "100"},
        ],
    }
    out = _alot_multiyear_summaries(latest)
    assert out[2025]["stlm_dt"] == "2025-06-30"
    assert out[2025]["period_start"] == "2024-07-01"
    assert out[2025]["period_end"] == "2025-06-30"
    assert out[2025]["fiscal_year_end_month"] == 6
    assert out[2024]["period_start"] == "2023-07-01"
    assert out[2023]["cash_dps"] == 100


def _payload(**data) -> dict:
    base = {
        "canonical_name": "포시에스",
        "identifiers": {"ticker": "189690", "corp_code": "00939942"},
        "year": 2025,
        "fiscal_year_basis": FISCAL_YEAR_BASIS,
        "fiscal_year_end_month": 6,
        "window": {"start_date": "20230117", "end_date": "20260630"},
        "summary": {
            "fiscal_year": 2025, "fiscal_year_end_month": 6,
            "period_start": "2024-07-01", "period_end": "2025-06-30",
            "cash_dps": 50, "total_amount_mil": 1318, "payout_ratio_dart": 28.96,
        },
        "policy_signals": {"trend": "stable"},
    }
    base.update(data)
    return {"status": "exact", "subject": "포시에스", "data": base, "warnings": []}


def test_summary_states_the_date_span_and_the_labelling_rule():
    out = _render(_payload(), "summary")
    assert "FY2025" in out
    assert "2024-07-01~2025-06-30" in out
    assert "6월 결산" in out
    assert "결산 종료연도 기준" in out


def test_december_year_end_company_gets_no_extra_line():
    """12월 결산은 FY와 달력연도가 같다 — 군더더기를 붙이지 않는다."""
    out = _render(_payload(
        fiscal_year_end_month=12,
        summary={"fiscal_year": 2025, "fiscal_year_end_month": 12,
                 "period_start": "2025-01-01", "period_end": "2025-12-31", "cash_dps": 1668},
    ), "summary")
    assert "결산 종료연도 기준" not in out
    assert "12월 결산" not in out


def test_undecided_fiscal_year_is_not_shown_as_paid():
    history = [
        {"year": 2025, "period_start": "2024-07-01", "period_end": "2025-06-30",
         "annual_dps": 50, "decision_count": 1, "payout_ratio": 28.96,
         "yield_pct": 2.0, "has_special": False, "pattern": "연간배당"},
        {"year": 2026, "period_start": "2025-07-01", "period_end": "2026-06-30",
         "annual_dps": 0, "decision_count": 0, "payout_ratio": None, "yield_pct": None,
         "has_special": False, "pattern": "미결의 (결산 종료 · 배당 결의 공시·사업보고서 모두 미확인)",
         "pending_confirmation": True},
    ]
    out = _render(_payload(history=history), "history")
    assert "2025-07-01~2026-06-30" in out
    assert "미결의" in out
    # 「0원 지급」으로 읽히면 안 된다.
    assert "| 0원 |" not in out
    assert "| 2026 | 2025-07-01~2026-06-30 | - |" in out
