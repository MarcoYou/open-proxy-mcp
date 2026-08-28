"""financial_metrics 산출물이 용어만 던지지 않는지 — 260828 U 지적 A-3 · B-5 · D-9."""

from open_proxy_mcp.services.financial_metrics import _is_correction_report
from open_proxy_mcp.tools.financial_metrics import (
    _alert_line,
    _render,
    _render_accruals,
    _render_correction_note,
)


def test_correction_report_name_detected():
    assert _is_correction_report("[기재정정]사업보고서 (2025.12)")
    assert _is_correction_report("[첨부정정]반기보고서 (2025.06)")
    assert _is_correction_report("[첨부추가]사업보고서 (2024.12)")
    assert not _is_correction_report("사업보고서 (2025.12)")
    assert not _is_correction_report("")


def test_correction_note_names_the_correction_and_its_date():
    lines = _render_correction_note({"source_report": {
        "report_nm": "[기재정정]사업보고서 (2025.12)",
        "is_correction": True, "correction_dt": "20260813", "same_day_corrections": 13,
    }})
    assert len(lines) == 1
    assert "정정본" in lines[0]
    assert "2026-08-13 정정" in lines[0]
    assert "13건" in lines[0]


def test_no_correction_note_for_a_normal_report():
    assert _render_correction_note({"source_report": {
        "report_nm": "사업보고서 (2025.12)", "is_correction": False}}) == []
    assert _render_correction_note({}) == []


def test_fiscal_year_label_shows_the_twelve_months_it_covers():
    md = _render({"data": {
        "canonical_name": "포시에스", "scope": "summary", "year": 2025,
        "identifiers": {"ticker": "189690"}, "fs_div": "CFS",
        "fiscal_year_end_month": 6,
        "fiscal_period": {"start": "2024-07-01", "end": "2025-06-30",
                          "fiscal_year_end_month": 6,
                          "label": "2024-07-01~2025-06-30 · 6월 결산"},
        "summary": {},
    }})
    assert "사업연도 2025 (2024-07-01~2025-06-30 · 6월 결산)" in md


def test_accruals_ratio_flips_sign_when_operating_profit_is_negative():
    """하이퍼코퍼레이션 2025 실측 — 현금이 이익보다 40억 **더** 빠졌는데 비율은 -93.67%."""
    lines = _render_accruals({
        "accruals_gap_pct": -93.67, "accruals_gap_krw": 4_010_373_965,
        "accruals_gap_reliability": "negative_op",
        "operating_profit_krw": -4_281_271_081, "nwc_change_yoy_krw": 8_600_000_000,
        "inv_to_revenue_pct": 7.27, "days_inventory_outstanding": 19.8,
        "ar_to_revenue_pct": 25.12, "days_sales_outstanding": 73.5,
    })
    body = "\n".join(lines)
    assert "금액차" in body and "40억원" in body       # 부호가 왜곡되지 않는 값을 같이 준다
    assert "비율은 읽지 마세요" in body                 # 분모 적자 경고
    assert "뜻:" in body                                # 한 줄 뜻풀이
    assert "갈림길:" in body                            # 무엇을 더 봐야 갈리는지


def test_accruals_fork_points_at_working_capital_when_nwc_covers_the_gap():
    """고려아연 2025 실측 — 괴리 1.86조, NWC YoY 1.9조. 재고 증가와 분식을 같은 red 로 묶지 않는다."""
    body = "\n".join(_render_accruals({
        "accruals_gap_pct": 150.99, "accruals_gap_krw": 1_860_137_497_744,
        "accruals_gap_reliability": "ok",
        "operating_profit_krw": 1_231_926_775_791, "nwc_change_yoy_krw": 1_930_000_000_000,
        "inv_to_revenue_pct": 37.5, "days_inventory_outstanding": 123.4,
        "ar_to_revenue_pct": 6.9, "days_sales_outstanding": 22.3,
    }))
    assert "운전자본이 괴리의" in body and "덮습니다" in body
    assert "재고/매출 37.50%" in body                   # 방향 판단 근거를 같이 준다


def test_accruals_negative_gap_is_not_treated_as_a_red_flag():
    """포시에스 2025 실측 — 현금이 이익보다 많은 쪽(-51.28%)은 분식 신호의 반대다."""
    body = "\n".join(_render_accruals({
        "accruals_gap_pct": -51.28, "accruals_gap_krw": -2_255_777_499,
        "accruals_gap_reliability": "ok", "operating_profit_krw": 4_399_159_063,
        "nwc_change_yoy_krw": 99_860_000,
    }))
    assert "역방향" in body
    assert "갈림길:" not in body                        # 역방향엔 갈림길 안내가 군더더기다


def test_alert_codes_carry_a_one_line_gloss():
    assert _alert_line("accruals_red").startswith("- ⚠ `accruals_red` — ")
    assert "금액차" in _alert_line("accruals_ratio_unreliable")
    assert _alert_line("무슨코드인지_모름") == "- ⚠ `무슨코드인지_모름`"
