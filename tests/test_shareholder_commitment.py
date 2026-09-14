# -*- coding: utf-8 -*-
"""shareholder_commitment 260914 변경 2건 회귀 테스트.

1. CSR 회계연도 정합: 배당(1개년 스냅샷)과 자사주매입 기간을 맞추지 않으면 CSR이
   최대 2배 가까이 부풀려진다(실측: 신한지주 3년 매입 기준 94.5% vs 회사 자체 "50.2%").
   `_overall_shareholder_return`이 `csr_treasury_summary`(회계연도 정합 조회)를 쓰는지,
   못 구했을 때 `treasury_summary`(lookback_years 누적)로 올바르게 폴백하는지 확인한다.

2. 최신 공시 무조건 노출: 그 회계연도가 끝난 뒤 나온 대형 공시(실측: SK하이닉스
   2026-08-19 취득결정 40.00조원)가 CSR 어디에도 안 보이면 "최신 데이터를 놓쳤다"는
   오해를 산다. `_latest_events_beyond_period`가 크기 무관 전부 잡아내는지, 렌더러가
   최상단에 띄우는지 확인한다.
"""
from __future__ import annotations

from open_proxy_mcp.services.shareholder_commitment import (
    _latest_events_beyond_period,
    _overall_shareholder_return,
)
from open_proxy_mcp.tools.shareholder_commitment import _render

FISCAL_2025 = {"start": "2025-01-01", "end": "2025-12-31"}


# ── _latest_events_beyond_period ──────────────────────────────────────

def test_event_after_fiscal_period_end_is_surfaced():
    """SK하이닉스 실측 재현: FY2025가 끝난 뒤(2026-08-19) 나온 40조 취득결정."""
    treasury_data = {"events": [
        {"event": "acquisition_decision", "rcept_dt": "20260819", "rcept_no": "20260819000254",
         "amount_krw": 40_004_340_000_000, "for_cancelation": True},
    ]}
    out = _latest_events_beyond_period(treasury_data, FISCAL_2025)
    assert len(out) == 1
    assert out[0]["amount_krw"] == 40_004_340_000_000
    assert out[0]["event_label"] == "자사주 취득결정"
    assert out[0]["for_cancelation"] is True


def test_event_inside_fiscal_period_is_excluded():
    """정상적으로 CSR에 이미 반영된(=회계연도 안) 공시는 다시 안 나온다."""
    treasury_data = {"events": [
        {"event": "acquisition_decision", "rcept_dt": "20250601", "rcept_no": "x",
         "amount_krw": 1_000_000_000, "for_cancelation": False},
    ]}
    assert _latest_events_beyond_period(treasury_data, FISCAL_2025) == []


def test_event_on_fiscal_period_end_date_itself_is_excluded():
    """경계값: 회계연도 마지막 날 공시는 '그 연도 안'이지 '그 이후'가 아니다."""
    treasury_data = {"events": [
        {"event": "acquisition_decision", "rcept_dt": "20251231", "rcept_no": "x",
         "amount_krw": 1, "for_cancelation": False},
    ]}
    assert _latest_events_beyond_period(treasury_data, FISCAL_2025) == []


def test_non_decision_events_are_excluded():
    """결과보고서([E], 예: acquisition_result)는 '사전 의도 공시'가 아니라서 이 섹션 대상 아님."""
    treasury_data = {"events": [
        {"event": "acquisition_result", "rcept_dt": "20260901", "rcept_no": "x", "amount_krw": 1},
    ]}
    assert _latest_events_beyond_period(treasury_data, FISCAL_2025) == []


def test_returns_empty_when_fiscal_period_unknown():
    """결산월을 못 구해 fiscal_period가 None이면 '이후'를 정의할 기준이 없다 — 조용히 빈 리스트."""
    treasury_data = {"events": [
        {"event": "acquisition_decision", "rcept_dt": "20260819", "rcept_no": "x",
         "amount_krw": 1, "for_cancelation": True},
    ]}
    assert _latest_events_beyond_period(treasury_data, None) == []


def test_multiple_events_sorted_newest_first():
    treasury_data = {"events": [
        {"event": "cancelation_decision", "rcept_dt": "20260128", "rcept_no": "a", "amount_krw": 100},
        {"event": "acquisition_decision", "rcept_dt": "20260819", "rcept_no": "b", "amount_krw": 200,
         "for_cancelation": True},
        {"event": "disposal_decision", "rcept_dt": "20260330", "rcept_no": "c", "amount_krw": 50},
    ]}
    out = _latest_events_beyond_period(treasury_data, FISCAL_2025)
    assert [e["rcept_dt"] for e in out] == ["20260819", "20260330", "20260128"]


# ── _overall_shareholder_return ───────────────────────────────────────

def test_csr_uses_period_matched_treasury_summary_when_available():
    """신한지주 실측 재현: 정합 후 매입 1.3조 -> CSR 51.2%(회사 자체 50.2%와 근접)."""
    dividend_summary = {"total_amount_mil": 1_245_730, "net_income_consolidated_mil": 4_932_500}
    lookback_treasury_summary = {  # 3년 누적 — 절대 CSR 분자로 쓰이면 안 됨
        "acquisition_amount_total_krw": 3_450_000_000_000,
        "trust_contract_amount_total_krw": 0,
        "cancelation_amount_total_krw": 5_700_000_000_000,
    }
    csr_treasury_summary = {  # 회계연도 정합 조회 — 이게 CSR 분자여야 함
        "acquisition_amount_total_krw": 800_000_000_000,
        "trust_contract_amount_total_krw": 500_000_000_000,
    }
    out = _overall_shareholder_return(
        dividend_summary, lookback_treasury_summary,
        csr_treasury_summary=csr_treasury_summary, fiscal_period=FISCAL_2025,
    )
    assert out["buyback_acquisition_krw"] == 1_300_000_000_000
    assert out["csr_period_matched"] is True
    assert out["csr_fiscal_period"] == FISCAL_2025
    assert "정합됨" in out["period_note"]
    # 소각금액은 여전히 lookback 누적 그대로(참고용) — csr_treasury_summary와 안 섞임
    assert out["buyback_cancelation_krw"] == 5_700_000_000_000
    expected_pct = round((1_245_730_000_000 + 1_300_000_000_000) / (4_932_500_000_000) * 100, 1)
    assert out["cash_shareholder_return_pct"] == expected_pct


def test_csr_falls_back_to_lookback_summary_when_fiscal_period_unknown():
    """결산월 미상 회사 — 조용히 안 맞는 숫자 내지 않고 폴백 사실을 period_note에 남긴다."""
    dividend_summary = {"total_amount_mil": 1_000_000, "net_income_consolidated_mil": 4_000_000}
    lookback_treasury_summary = {
        "acquisition_amount_total_krw": 3_000_000_000_000,
        "trust_contract_amount_total_krw": 0,
        "cancelation_amount_total_krw": 1_000_000_000_000,
    }
    out = _overall_shareholder_return(
        dividend_summary, lookback_treasury_summary,
        csr_treasury_summary=None, fiscal_period=None,
    )
    assert out["buyback_acquisition_krw"] == 3_000_000_000_000  # lookback 그대로 사용
    assert out["csr_period_matched"] is False
    assert out["csr_fiscal_period"] is None
    assert "⚠" in out["period_note"]
    assert "기간 불일치" in out["period_note"]


def test_csr_zero_acquisition_matches_samsung_fire_sk_telecom_pattern():
    """실측: 삼성화재·SK텔레콤은 소각결정은 있었지만(과거 보유분) 해당 회계연도 신규 취득은 0.
    소각금액이 커도(참고용) CSR 분자엔 안 들어가 CSR이 낮게(=정직하게) 나와야 한다."""
    dividend_summary = {"total_amount_mil": 828_949, "net_income_consolidated_mil": 2_016_664}
    lookback_treasury_summary = {
        "acquisition_amount_total_krw": 0,
        "trust_contract_amount_total_krw": 0,
        "cancelation_amount_total_krw": 1_201_451_512_000,  # 과거 보유분 소각 — CSR 분자 아님
    }
    csr_treasury_summary = {"acquisition_amount_total_krw": 0, "trust_contract_amount_total_krw": 0}
    out = _overall_shareholder_return(
        dividend_summary, lookback_treasury_summary,
        csr_treasury_summary=csr_treasury_summary, fiscal_period=FISCAL_2025,
    )
    assert out["buyback_acquisition_krw"] == 0
    assert out["cash_shareholder_return_pct"] == round(828_949_000_000 / 2_016_664_000_000 * 100, 1)


# ── 렌더러 ─────────────────────────────────────────────────────────────

def _payload(**overrides) -> dict:
    base = {
        "canonical_name": "SK하이닉스",
        "lookback_years": 3,
        "commitments": {"latest_plan": {"exists": True}},
        "capital_return_execution": {"buyback_cycles": [], "dividend_history": []},
        "governance_trend": {"transitions": []},
        "overall": {
            "dividend_krw": 2_095_133_000_000,
            "buyback_acquisition_krw": 0,
            "buyback_cancelation_krw": 52_244_340_000_000,
            "cash_shareholder_return_pct": 4.9,
            "csr_period_matched": True,
            "period_note": "배당·자사주매입 모두 동일 회계연도(2025-01-01~2025-12-31) 기준 — 정합됨.",
        },
        "latest_events_beyond_csr_period": [],
        "data_quality_flags": [],
    }
    base.update(overrides)
    return {"status": "exact", "subject": "SK하이닉스", "data": base}


def test_render_shows_latest_events_section_at_top_when_present():
    payload = _payload(latest_events_beyond_csr_period=[
        {"event": "acquisition_decision", "event_label": "자사주 취득결정", "rcept_dt": "20260819",
         "rcept_no": "20260819000254", "amount_krw": 40_004_340_000_000, "for_cancelation": True},
    ])
    out = _render(payload)
    assert "🔴 최신 공시" in out
    assert "40,004,340,000,000원" in out
    assert "자사주 취득결정(소각목적)" in out
    assert "20260819000254" in out
    # 최신 공시 섹션이 밸류업 계획 섹션보다 위에 와야 한다(무조건 눈에 띄게).
    assert out.index("🔴 최신 공시") < out.index("밸류업 계획 공표 여부")
    # 종합 섹션에도 교차참조가 붙는다.
    assert "이 CSR 이후 새 공시 1건 있음" in out


def test_render_omits_latest_events_section_when_empty():
    out = _render(_payload(latest_events_beyond_csr_period=[]))
    assert "🔴 최신 공시" not in out
    assert "이 CSR 이후 새 공시" not in out


def test_render_labels_buyback_period_by_match_status():
    matched = _render(_payload())
    assert "배당과 동일 회계연도" in matched

    unmatched = _render(_payload(overall={
        "dividend_krw": 1_000_000_000_000, "buyback_acquisition_krw": 3_000_000_000_000,
        "buyback_cancelation_krw": 1_000_000_000_000, "cash_shareholder_return_pct": 99.1,
        "csr_period_matched": False,
        "period_note": "⚠ 회계연도를 못 구해 배당(1개년)·자사주매입(lookback 누적)을 그대로 합산.",
    }))
    assert "배당과 기간 불일치" in unmatched
