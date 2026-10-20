"""밸류업 도구 목표 대조 — 반기·분기 결산 리츠의 ROE 는 12개월 값으로 견준다 (DATA-FY3). network 0 · DB 0.

롯데리츠 FY2025 사업보고서의 당기는 2025.07~12 여섯 달이라 재무 지표 도구의 roe_pct 가 1.70%(여섯 달 값)다. 「ROE 3% 이상」 목표를 거기에 대면
「미달」로 판정됐다 — 12개월 값(roe_12m_pct 3.13%)이면 「달성」이다. 12개월 값을 못 만들면 한 결산기 값으로 판정하지 않고 「대조 못 함」이다.
12개월 결산 회사의 대조는 한 글자도 바뀌지 않는다.
"""
from __future__ import annotations

import asyncio

import pytest

import open_proxy_mcp.services.financial_metrics as fm
from open_proxy_mcp.services.value_up import compare_targets_with_actuals, extract_numeric_targets


def _payload(summary, label):
    return {"data": {"summary": summary, "fiscal_period": {"label": label}, "consolidated": False,
                     "source_report": {"report_nm": "사업보고서 (2025.12)"}}}


REIT_SUMMARY = {"period_months": 6, "roe_pct": 1.7, "roa_pct": 0.75, "roic_pct": 1.2, "debt_ratio_pct": 127.7,
                "net_income_12m_krw": 36_174_226_674, "roe_12m_pct": 3.13,
                "twelve_month_basis": {"status": "ok"}}
LABEL_6M = "2025-07-01~2025-12-31 · 6개월 결산기"


def _run(monkeypatch, summary, label, text):
    async def fake_build(company_ref, scope="summary", **kw):
        return _payload(summary, label)
    monkeypatch.setattr(fm, "build_financial_metrics_payload", fake_build)
    targets, _ = extract_numeric_targets(text)
    rows, warnings = asyncio.run(compare_targets_with_actuals(targets, "롯데리츠"))
    return {r["metric_key"]: r for r in rows}, warnings


def test_reit_roe_target_is_judged_on_the_12_month_value(monkeypatch):
    rows, _ = _run(monkeypatch, REIT_SUMMARY, LABEL_6M, "ROE 3% 이상 달성")
    r = rows["roe"]
    assert r["actual"] == 3.13 and r["actual_field"] == "roe_12m_pct" and r["verdict"] == "달성"     # 종전: 1.7 → 미달
    assert "12개월 결산기 합" in r["actual_basis"] and "6개월 결산기" in r["actual_basis"]
    assert "한 결산기 6개월 값 아님" in r["actual_basis"]


def test_reit_roe_target_above_the_12_month_value_is_still_a_miss(monkeypatch):
    rows, _ = _run(monkeypatch, REIT_SUMMARY, LABEL_6M, "ROE 4% 이상 달성")
    assert rows["roe"]["actual"] == 3.13 and rows["roe"]["verdict"] == "미달"


def test_reit_roe_without_12_month_value_is_not_judged_on_the_half_year_value(monkeypatch):
    summary = {**REIT_SUMMARY, "roe_12m_pct": None, "net_income_12m_krw": None,
               "twelve_month_basis": {"status": "unavailable", "reason": "DB 를 읽지 못함(미설정 또는 장애)"}}
    rows, _ = _run(monkeypatch, summary, LABEL_6M, "ROE 3% 이상 달성")
    r = rows["roe"]
    assert r["actual"] is None and r["verdict"] == "대조 못 함"
    assert "6개월" in r["verdict_note"] and "DB 를 읽지 못함" in r["verdict_note"] and "1.7%" in r["verdict_note"]


def test_reit_other_ratios_keep_their_values_and_roa_gets_a_period_caveat(monkeypatch):
    rows, _ = _run(monkeypatch, REIT_SUMMARY, LABEL_6M, "ROA 3% 이상, 부채비율 150% 이하")
    assert rows["roa"]["actual"] == 0.75 and "6개월 기준(연환산 아님)" in rows["roa"]["caveat"]
    assert "actual_field" not in rows["roa"]
    assert rows["debt_ratio"]["actual"] == 127.7 and rows["debt_ratio"]["caveat"] == ""


def test_twelve_month_company_is_unchanged(monkeypatch):
    summary = {"period_months": 12, "roe_pct": 8.4, "roa_pct": 4.0, "roic_pct": 6.0, "debt_ratio_pct": 80.0}
    rows, _ = _run(monkeypatch, summary, "2025-01-01~2025-12-31", "ROE 8% 이상 달성, ROA 3% 이상")
    assert rows["roe"]["actual"] == 8.4 and rows["roe"]["verdict"] == "달성" and "actual_field" not in rows["roe"]
    assert "12개월" not in rows["roe"]["actual_basis"] and rows["roa"]["caveat"] == ""
    # period_months 가 응답에 없는 옛 모양도 종전 그대로
    rows2, _ = _run(monkeypatch, {"roe_pct": 8.4}, "2025-01-01~2025-12-31", "ROE 8% 이상 달성")
    assert rows2["roe"]["actual"] == 8.4 and "actual_field" not in rows2["roe"]


@pytest.mark.parametrize("bad", [True, None, "6"])
def test_non_numeric_period_months_is_ignored(monkeypatch, bad):
    rows, _ = _run(monkeypatch, {"period_months": bad, "roe_pct": 8.4}, "2025-01-01~2025-12-31", "ROE 8% 이상 달성")
    assert rows["roe"]["actual"] == 8.4 and "actual_field" not in rows["roe"]
