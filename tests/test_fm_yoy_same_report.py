"""yoy 에 올해를 주면 전기도 당기와 같은 보고서로 센다 — DATA-Q6 (기간 맞춤). network 0 · DB 0.

올해는 사업보고서가 아직 없어 3분기(누적 9개월)로 대체되는데 전기는 사업보고서(12개월)라, 올해 9개월 매출을 전년 12개월과
견주어 revenue_decline(30%↓)·roe_decline_*·receivables_surge·inventory_surge 가 거짓으로 켜졌다.
이 시험의 합성 사례에서 옛 응답은 alerts = [revenue_decline, roe_decline_margin_driven](전기 매출 1000 대 올해 9개월 650)이었다.
고침: 당기와 전기의 보고서 코드가 다르면 전기를 **당기와 같은 보고서**(전년 3분기 누적)로 다시 센다. 그 보고서가 없으면
기간에 민감한 경고를 계산하지 않고 까닭을 적는다.
"""
from __future__ import annotations

import asyncio

import pytest

from open_proxy_mcp.services import financial_metrics as FM
from open_proxy_mcp.services.company import CompanyResolution
from open_proxy_mcp.services.contracts import AnalysisStatus

CO = {"corp_code": "00000001", "corp_name": "시험회사", "stock_code": "000001"}
_NO = ([], "no_filing")


def _rows(year: int, rev: str, op: str, ni: str, *, quarterly: bool) -> list[dict]:
    base = {"fs_div": "CFS", "thstrm_dt": f"{year}.09.30" if quarterly else f"{year}.12.31", "ord": "1"}
    is_extra = {"thstrm_add_amount": rev} if quarterly else {}
    return [
        {**base, "sj_div": "BS", "account_nm": "자산총계", "thstrm_amount": "1000"},
        {**base, "sj_div": "BS", "account_nm": "부채총계", "thstrm_amount": "500"},
        {**base, "sj_div": "BS", "account_nm": "자본총계", "thstrm_amount": "500"},
        {**base, "sj_div": "IS", "account_nm": "매출액", "thstrm_amount": rev, **is_extra},
        {**base, "sj_div": "IS", "account_nm": "영업이익", "thstrm_amount": op,
         **({"thstrm_add_amount": op} if quarterly else {})},
        {**base, "sj_div": "IS", "account_nm": "당기순이익(손실)", "thstrm_amount": ni,
         **({"thstrm_add_amount": ni} if quarterly else {})},
    ]


def _table(with_prior_q3: bool, annual_2026: bool = False) -> dict:
    """(사업연도, 보고서코드) → 주요계정 행. 올해(2026)는 3분기만(또는 annual_2026 이면 사업보고서)."""
    t = {
        (2025, "11011"): _rows(2025, "1000", "100", "80", quarterly=False),
        (2024, "11011"): _rows(2024, "1000", "100", "80", quarterly=False),
        (2024, "11014"): _rows(2024, "700", "70", "50", quarterly=True),
    }
    if annual_2026:
        t[(2026, "11011")] = _rows(2026, "600", "30", "10", quarterly=False)   # 12개월끼리: 1000 → 600 은 진짜 40% 감소
    else:
        t[(2026, "11014")] = _rows(2026, "650", "50", "30", quarterly=True)    # 9개월 누적
    if with_prior_q3:
        t[(2025, "11014")] = _rows(2025, "690", "70", "55", quarterly=True)    # 전년 3분기 누적 — 9개월끼리 −5.8%
    return t


def _run(monkeypatch, table: dict, year: int):
    calls: list[tuple] = []
    monkeypatch.setenv("OPENDART_API_KEY", "test-key-not-real")

    async def resolve(query, *a, **k):
        return CompanyResolution(status=AnalysisStatus.EXACT, query=query, selected=dict(CO), candidates=[])

    async def info(cc):
        return {"acc_mt": "12", "induty_code": "471"}, None

    async def acnt(cc, y, rc, fs):
        calls.append(("acnt", y, rc))
        return (table[(y, rc)], None) if (y, rc) in table else _NO

    async def acnt_all(cc, y, rc, fs):
        calls.append(("all", y, rc))
        return _NO

    async def audit(*a, **k):
        return {"opinions": []}, [], []

    async def none(*a, **k):
        return None

    monkeypatch.setattr(FM, "resolve_company_query", resolve)
    monkeypatch.setattr(FM, "_safe_company_info", info)
    monkeypatch.setattr(FM, "_safe_fetch_acnt", acnt)
    monkeypatch.setattr(FM, "_safe_fetch_acnt_all", acnt_all)
    monkeypatch.setattr(FM, "_build_audit_opinion_data", audit)
    monkeypatch.setattr(FM, "_periodic_filing_ref", none)
    monkeypatch.setattr(FM, "_accrual_payout_pct", none)
    monkeypatch.setattr(FM, "_FM_CACHE", type(FM._FM_CACHE)(16 * 1024 * 1024, 300.0, "test-fm-yoy-same"))
    p = asyncio.run(FM.build_financial_metrics_payload("시험회사", scope="yoy", year=year))
    return p, calls


def test_this_year_third_quarter_is_compared_with_last_years_third_quarter(monkeypatch):
    p, calls = _run(monkeypatch, _table(with_prior_q3=True), 2026)
    y = p["data"]["yoy"]
    assert y["current"]["reprt_code"] == "11014" and y["current"]["revenue_krw"] == 650
    # 전기도 3분기 누적(690) — 사업보고서 12개월(1000)이 아니다
    assert y["prior"]["reprt_code"] == "11014" and y["prior"]["revenue_krw"] == 690
    assert y["period_aligned"] is True
    assert "revenue_decline" not in y["alerts"]          # 옛 값: (1000−650)/1000 = 35% 로 거짓 경고
    assert "period_note" in y and "같은 보고서" in y["period_note"]
    assert "9개월 누적" in y["period_note"] and "cumulative" not in y["period_note"]   # 사람 말로 — 엔진 식별자를 문장에 싣지 않는다
    assert y["current"]["revenue_yoy_pct"] == -5.8       # 당기가 이미 전기 같은 보고서로 센 값과 이제 한 줄로 맞는다
    assert ("acnt", 2025, "11014") in calls


def test_without_the_same_report_period_sensitive_alerts_are_off_with_a_reason(monkeypatch):
    p, _calls = _run(monkeypatch, _table(with_prior_q3=False), 2026)
    y = p["data"]["yoy"]
    assert y["prior"]["reprt_code"] == "11011"           # 전기는 사업보고서 그대로 보이되
    assert y["period_aligned"] is False
    assert not (set(y["alerts"]) & set(y["alerts_not_evaluated"]))
    assert "revenue_decline" in y["alerts_not_evaluated"]
    assert any("계산하지 않았" in w and "연간 12개월" in w for w in p["warnings"])
    assert any("2025년 3분기보고서 미공시" in w for w in p["warnings"])    # 같은 보고서를 못 읽은 까닭도 그대로


def test_annual_vs_annual_is_unchanged_and_real_declines_still_fire(monkeypatch):
    p, calls = _run(monkeypatch, _table(with_prior_q3=False, annual_2026=True), 2026)
    y = p["data"]["yoy"]
    assert y["current"]["reprt_code"] == y["prior"]["reprt_code"] == "11011"
    assert y["period_aligned"] is True and "period_note" not in y
    assert "revenue_decline" in y["alerts"]               # 1000 → 600 은 진짜 40% 감소
    assert "alerts_not_evaluated" not in y
    assert ("acnt", 2026, "11014") not in calls          # 사업보고서가 있으면 분기 보고서로 내려가지 않는다


def test_realignment_adds_no_duplicate_dart_call(monkeypatch):
    _p, calls = _run(monkeypatch, _table(with_prior_q3=True), 2026)
    assert len(calls) == len(set(calls)), [c for c in calls if calls.count(c) > 1]
