"""금융 영업 신호가 잡힌 회사(`is_financial_company`)는 영업CF 기반 경고 다섯 가지를 판정하지 않는다. network 0 · DB 0.

옛 코드는 영업CF 하나에 기댄 cfo_quality_red·accruals_red·negative_fcf·low_dividend_capacity_use·accruals_ratio_unreliable 을 판정 여부와 무관하게 냈다.
KB금융 2024(연결)는 영업CF 4.02조 안에 상각후원가 대출채권 −27.52조·예수부채 +23.82조·보험계약부채 +11.81조가 들어 있어
영업CF/영업이익 0.4997·괴리 50.03% 로 cfo_quality_red·accruals_red 가 켜졌다 — 이익의 질이 아니라 대차대조표 성장이 만든 값이다.
고침: 판정 참이면 다섯 이름을 경고 목록에서 빼고 yoy 경고(warnings)에 한 줄 안내를 단다. 영업CF·FCF·비율 값은 그대로다.
행 모양(id·이름·순서)과 금액은 실제 DART 전체계정 응답(KB금융 2024 사업보고서 연결)에서 필요한 행만 줄였다.
"""
from __future__ import annotations

import asyncio

import pytest

from open_proxy_mcp.services import financial_metrics as FM
from open_proxy_mcp.services.company import CompanyResolution
from open_proxy_mcp.services.contracts import AnalysisStatus

FIVE = {"cfo_quality_red", "accruals_red", "negative_fcf", "low_dividend_capacity_use", "accruals_ratio_unreliable"}
NOTE_TAG = "[현금흐름 경고]"


# ── 판정 함수 단독: 현재 연도 지표 dict 만 본다 ──
def _kb_like(**kw):
    """KB금융 2024 의 값(영업이익 8.05조·영업CF 4.02조)에 네 경고가 모두 켜지게 만든 지표."""
    base = {"operating_profit_krw": 8_045_261_000_000, "cfo_to_op_ratio": 0.4997, "accruals_gap_pct": 50.03,
            "accruals_gap_reliability": "ok", "fcf_krw": -1_000_000_000, "dividend_to_fcf_pct": None,
            "interest_coverage_ratio": 0.4554}
    base.update(kw)
    return base


def test_non_financial_keeps_the_cash_flow_alerts():
    sig = FM._detect_yoy_signals(_kb_like(), {})
    assert {"cfo_quality_red", "accruals_red", "negative_fcf"} <= set(sig)
    assert "low_dividend_capacity_use" in FM._detect_yoy_signals(_kb_like(dividend_to_fcf_pct=14.5), {})


def test_financial_company_drops_the_five_cash_flow_alerts():
    sig = FM._detect_yoy_signals(_kb_like(is_financial_company=True, dividend_to_fcf_pct=14.5), {})
    assert not (set(sig) & FIVE)
    assert "interest_coverage_low" in sig            # 다른 경고는 그대로


def test_financial_company_drops_the_unreliable_ratio_notice_too():
    thin = _kb_like(accruals_gap_reliability="thin_op", accruals_gap_pct=-2999.5)
    assert "accruals_ratio_unreliable" in FM._detect_yoy_signals(thin, {})
    assert "accruals_ratio_unreliable" not in FM._detect_yoy_signals({**thin, "is_financial_company": True}, {})


def test_false_flag_is_the_same_as_no_flag():
    assert FM._detect_yoy_signals(_kb_like(is_financial_company=False), {}) == FM._detect_yoy_signals(_kb_like(), {})


# ── yoy 조립: 판정 → 경고 목록 · 안내 한 줄 · 값 유지 ──
_BS_IDS = {"assets": "ifrs-full_Assets", "dep": "ifrs-full_DepositsFromCustomers", "ins": "ifrs-full_InsuranceContractsIssuedThatAreLiabilities"}
_BASE = {"fs_div": "CFS", "thstrm_dt": "2024.12.31", "ord": "1", "currency": "KRW"}


def _row(sj, aid, nm, cur, prev=None):
    return {**_BASE, "sj_div": sj, "account_id": aid, "account_nm": nm, "thstrm_amount": cur, "frmtrm_amount": prev or cur}


def _single(*, signal: bool):
    """fnlttSinglAcnt 꼴(주요계정): 자산·부채·자본·이자수익·영업이익·순이익."""
    rows = [_row("BS", "", "자산총계", "757845532000000"), _row("BS", "", "부채총계", "698030351000000"),
            _row("BS", "", "자본총계", "59815181000000"), _row("IS", "", "영업수익", "30491385000000"),
            _row("IS", "", "영업이익", "8045261000000"), _row("IS", "", "당기순이익", "5078221000000")]
    return rows


def _all(*, signal: bool):
    """fnlttSinglAcntAll 꼴: 금융 신호 BS 행(있을 때만) + 현금흐름 세 줄."""
    rows = [_row("BS", _BS_IDS["assets"], "자산총계", "757845532000000")]
    if signal:
        rows += [_row("BS", _BS_IDS["dep"], "예수부채", "435687897000000"),
                 _row("BS", _BS_IDS["ins"], "보험계약부채", "55863701000000")]
    rows += [_row("CF", "ifrs-full_CashFlowsFromUsedInOperatingActivities", "영업활동으로부터의 현금흐름", "4020186000000"),
             _row("CF", "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities", "유형자산의 취득", "337178000000"),
             _row("CF", "ifrs-full_DividendsPaidClassifiedAsFinancingActivities", "배당금의 지급", "1486978000000")]
    return rows


def _run(monkeypatch, *, signal: bool, induty: str = "64992", year: int = 2024):
    monkeypatch.setenv("OPENDART_API_KEY", "test-key-not-real")

    async def resolve(query, *a, **k):
        return CompanyResolution(status=AnalysisStatus.EXACT, query=query,
                                 selected={"corp_code": "00000001", "corp_name": "시험회사", "stock_code": "000001"}, candidates=[])

    async def info(cc):
        return {"acc_mt": "12", "induty_code": induty}, None

    async def acnt(cc, y, rc, fs):
        return (_single(signal=signal), None) if rc == "11011" else ([], "no_filing")

    async def acnt_all(cc, y, rc, fs):
        return (_all(signal=signal), None) if rc == "11011" else ([], "no_filing")

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
    monkeypatch.setattr(FM, "_FM_CACHE", type(FM._FM_CACHE)(16 * 1024 * 1024, 300.0, "test-fm-fin-cf"))
    return asyncio.run(FM.build_financial_metrics_payload("시험회사", scope="yoy", year=year))


def test_balance_sheet_signal_company_loses_the_alerts_and_gets_one_notice_but_keeps_the_values(monkeypatch):
    p = _run(monkeypatch, signal=True)
    y = p["data"]["yoy"]
    assert y["current"]["is_financial_company"] is True
    assert y["current"]["cfo_krw"] == 4_020_186_000_000 and y["current"]["cfo_to_op_ratio"] == 0.4997   # 값은 그대로
    assert y["current"]["accruals_gap_pct"] == 50.03
    assert not (set(y["alerts"]) & FIVE)                                                                # 옛 값: cfo_quality_red·accruals_red
    notes = [w for w in p["warnings"] if NOTE_TAG in w]
    assert len(notes) == 1 and notes[0].startswith("2024년 ")
    assert all(n in notes[0] for n in sorted(FIVE))
    assert "금융사" not in notes[0] and "섞인다" not in notes[0]                                          # 판정 신호가 잡혔다는 사실만 적는다
    assert "계정 신호인 경우" in notes[0]                                                               # 업종코드로만 참인 회사에는 맞지 않는 구절이라 조건을 단다
    assert "회계 risk 강화 cross-check" not in " ".join(p["next_actions"])                               # accruals_red 가 없으니 다음 행동도 빠진다


def test_industry_code_only_company_gets_the_same_treatment(monkeypatch):
    p = _run(monkeypatch, signal=False, induty="64913")        # 수신 없는 카드사 꼴: 재무상태표 신호 없음, 업종코드로만 참
    y = p["data"]["yoy"]
    assert y["current"]["is_financial_company"] is True
    assert not (set(y["alerts"]) & FIVE)
    assert len([w for w in p["warnings"] if NOTE_TAG in w]) == 1


def test_non_financial_twin_is_unchanged(monkeypatch):
    p = _run(monkeypatch, signal=False, induty="471")          # 같은 현금흐름 값, 신호도 업종코드도 없음
    y = p["data"]["yoy"]
    assert y["current"]["is_financial_company"] is False
    assert {"cfo_quality_red", "accruals_red"} <= set(y["alerts"])
    assert not [w for w in p["warnings"] if NOTE_TAG in w]


def test_notice_is_yoy_only(monkeypatch):
    monkeypatch.setenv("OPENDART_API_KEY", "test-key-not-real")
    _run(monkeypatch, signal=True)                              # 하네스 patch 재사용
    p = asyncio.run(FM.build_financial_metrics_payload("시험회사", scope="summary", year=2024))
    assert not [w for w in p["warnings"] if NOTE_TAG in w]


def test_period_mismatch_and_financial_do_not_mix_the_two_reasons(monkeypatch):
    """올해(3분기 대체)인데 전년 같은 보고서가 없을 때: alerts_not_evaluated 는 기간 사유 그대로, 금융 사유는 warnings 한 줄."""
    monkeypatch.setenv("OPENDART_API_KEY", "test-key-not-real")

    async def resolve(query, *a, **k):
        return CompanyResolution(status=AnalysisStatus.EXACT, query=query,
                                 selected={"corp_code": "00000001", "corp_name": "시험회사", "stock_code": "000001"}, candidates=[])

    async def info(cc):
        return {"acc_mt": "12", "induty_code": "64992"}, None

    q3 = {**_BASE, "thstrm_dt": "2026.09.30"}

    def rows(year, rc, annual):
        th = f"{year}.12.31" if annual else f"{year}.09.30"
        out = []
        for r in _all(signal=True):
            out.append({**r, "thstrm_dt": th})
        return out

    async def acnt(cc, y, rc, fs):
        if (y, rc) in {(2025, "11011"), (2024, "11011"), (2026, "11014")}:
            return [{**r, "thstrm_dt": f"{y}.12.31" if rc == "11011" else f"{y}.09.30",
                     **({"thstrm_add_amount": r["thstrm_amount"]} if rc == "11014" and r["sj_div"] == "IS" else {})}
                    for r in _single(signal=True)], None
        return [], "no_filing"

    async def acnt_all(cc, y, rc, fs):
        if (y, rc) in {(2025, "11011"), (2024, "11011"), (2026, "11014")}:
            return rows(y, rc, rc == "11011"), None
        return [], "no_filing"

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
    monkeypatch.setattr(FM, "_FM_CACHE", type(FM._FM_CACHE)(16 * 1024 * 1024, 300.0, "test-fm-fin-cf-mix"))
    p = asyncio.run(FM.build_financial_metrics_payload("시험회사", scope="yoy", year=2026))
    y = p["data"]["yoy"]
    assert y["period_aligned"] is False
    assert set(y["alerts_not_evaluated"]) == set(FM._PERIOD_SENSITIVE_ALERTS)          # 기간 사유 칸에 금융 사유가 섞이지 않는다
    assert not (set(y["alerts"]) & (FIVE | set(y["alerts_not_evaluated"])))
    assert len([w for w in p["warnings"] if NOTE_TAG in w]) == 1
