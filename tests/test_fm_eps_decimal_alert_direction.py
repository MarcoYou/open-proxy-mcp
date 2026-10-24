"""EPS 소수 판독 · 영업CF 경고 방향 · 경고 문구 — network 0 · DB 0.

P1-#20: EPS(원/주)는 외화 보고사(USD 4.14)·원화 리츠(4.21)처럼 소수로 공시되는데 `int(float())` 가 소수를 버려
        외화 보고사 eps_krw 가 3% 넘게 틀렸다(4.14×1,470=6,086 인데 4×1,470=5,880).
P1-#25: 영업이익 ≤0 인데 cfo_quality_red 가 켜지고, accruals_red 는 abs() 라 현금>이익 방향에서도
        「이익이 현금으로 안 들어왔다」가 나갔다.
P1-#26: interest_coverage_low 문턱은 2배인데 설명은 「1배 미만」, roe_driven_by_leverage 는 수준 판정인데 설명은 변화.
입력은 DART 응답 경계(fnlttSinglAcntAll 의 list) 꼴 — 필요한 행만 줄였다.
"""
import asyncio

import pytest

from open_proxy_mcp.services import financial_metrics as fm
from open_proxy_mcp.tools import financial_metrics as tool


def _eps_rows(basic, diluted, *, currency="KRW", sj="IS", add_basic=None, frm=None):
    def row(acc, nm, th, add=None, fr=None):
        r = {"sj_div": sj, "account_id": acc, "account_nm": nm, "thstrm_amount": th,
             "frmtrm_amount": fr, "currency": currency, "fs_div": "CFS", "ord": "1"}
        if add is not None:
            r["thstrm_add_amount"] = add
        return r
    return [
        row("ifrs-full_BasicEarningsLossPerShare", "기본주당이익", basic, add_basic, frm),
        row("ifrs-full_DilutedEarningsLossPerShare", "희석주당이익", diluted, None, frm),
    ]


# ── P1-#20 EPS 소수 ──

def test_usd_decimal_eps_is_kept_not_truncated():
    m = fm._build_account_map_all(_eps_rows("4.14", "4.13", currency="USD"))
    assert m["basic_eps"] == 4.14 and m["diluted_eps"] == 4.13


def test_krw_decimal_eps_is_kept():
    m = fm._build_account_map_all(_eps_rows("4.21", "4.21", sj="CIS"))
    assert m["basic_eps"] == 4.21 and m["diluted_eps"] == 4.21


def test_integer_eps_stays_int_with_commas():
    m = fm._build_account_map_all(_eps_rows("11,732", "11,732"))
    assert m["basic_eps"] == 11732 and type(m["basic_eps"]) is int


def test_negative_decimal_eps_both_notations():
    assert fm._build_account_map_all(_eps_rows("-5.08", "-5.08"))["basic_eps"] == -5.08
    assert fm._build_account_map_all(_eps_rows("(5.08)", "(5.08)"))["basic_eps"] == -5.08


def test_cumulative_path_prefers_thstrm_add_and_keeps_decimal():
    rows = _eps_rows("1.10", "1.10", add_basic="2.55")
    assert fm._build_account_map_all(rows, cumulative_is=True)["basic_eps"] == 2.55
    assert fm._build_account_map_all(_eps_rows("1.10", "1.10"), cumulative_is=True)["basic_eps"] == 1.10


def test_continuing_operations_fallback_keeps_decimal():
    rows = [{"sj_div": "IS", "account_id": "ifrs-full_BasicEarningsLossPerShareFromContinuingOperations",
             "account_nm": "계속영업기본주당이익", "thstrm_amount": "3.75", "currency": "KRW", "ord": "1"}]
    assert fm._build_account_map_all(rows)["basic_eps"] == 3.75


def test_missing_or_dash_eps_is_none():
    assert fm._build_account_map_all(_eps_rows("-", ""))["basic_eps"] is None


def test_normalize_amount_contract_stays_integer():
    """금액 정규화는 정수 계약 그대로 — EPS 만 소수로 읽는다."""
    assert fm.normalize_amount("4.14") == 4
    assert fm.normalize_amount("1,234,000") == 1234000


def _metrics_from(rows):
    m = fm._build_account_map_all(rows)
    base = {"total_assets": 1000, "total_equity": 500, "revenue": 100, "operating_profit": 10, "net_income": 5}
    return fm._compute_metrics(bs_is=base, bs_is_prev=None, detail=m, detail_prev=None, indx_map=None)


def test_usd_eps_converts_to_whole_won_with_real_decimal(monkeypatch):
    async def _rate(cur, date):
        return 1470.0
    monkeypatch.setattr(fm, "fx_to_krw", _rate)
    rows = _eps_rows("4.14", "4.13", currency="USD")
    metrics = _metrics_from(rows)
    asyncio.run(fm._normalize_currency(metrics, rows, "2024-12-31", 2024))
    assert metrics["eps_krw"] == 6086 and type(metrics["eps_krw"]) is int      # 옛 5,880
    assert metrics["diluted_eps_krw"] == 6071


def test_eps_converts_to_whole_won_at_another_years_rate(monkeypatch):
    """다른 해(2023 말 환율 1,289.4)로 환산해도 소수 EPS 의 결과가 정수 원이다.

    전년 대비의 전기 EPS 는 전년 보고서를 따로 읽는 경로(`_fetch_year_metrics(target_year−1)`)를 타는데, 그 경로 자체는
    이 시험이 지키지 않는다 — 전년 보고서 원행의 꼴과 새 전기 값은 병합 뒤 운영 응답(V5 verifier)에서 확인한다.
    """
    seen = {}

    async def _rate(cur, date):
        seen["date"] = date
        return 1289.4
    monkeypatch.setattr(fm, "fx_to_krw", _rate)
    rows = _eps_rows("7.05", "7.04", currency="USD")
    metrics = _metrics_from(rows)
    asyncio.run(fm._normalize_currency(metrics, rows, "2023-12-31", 2023))
    assert seen["date"] == "20231231"
    assert metrics["eps_krw"] == round(7.05 * 1289.4) == 9090 and type(metrics["eps_krw"]) is int


def test_krw_decimal_eps_survives_krw_normalization():
    rows = _eps_rows("4.21", "4.21", sj="CIS")
    metrics = _metrics_from(rows)
    asyncio.run(fm._normalize_currency(metrics, rows, "2024-12-31", 2024))
    assert metrics["eps_krw"] == 4.21


def test_md_eps_shows_decimal_only_when_present():
    assert tool._eps(4.21) == "4.21원"
    assert tool._eps(11732) == "11,732원"
    assert tool._eps(11732.0) == "11,732원"
    assert tool._eps(-5.08) == "-5.08원"
    assert tool._eps(4.137) == "4.137원"          # 자릿수를 고정해 반올림하지 않는다(JSON 4.137 과 같은 표기)
    assert tool._eps(None) == "미공시"


# ── P1-#25 경고 방향 ──

def _curr(**kw):
    base = {"operating_profit_krw": 100, "cfo_to_op_ratio": 1.0, "accruals_gap_pct": 0.0,
            "accruals_gap_reliability": "ok"}
    base.update(kw)
    return base


def test_cfo_quality_red_not_raised_when_operating_loss():
    """영업적자(−1,092억)·영업CF 흑자(+1,070억) → 비율 −0.98 이 0.7 미만이라 켜졌다."""
    sig = fm._detect_yoy_signals(_curr(operating_profit_krw=-109, cfo_to_op_ratio=-0.98,
                                       accruals_gap_pct=198.0, accruals_gap_reliability="negative_op"), {})
    assert "cfo_quality_red" not in sig
    assert "accruals_ratio_unreliable" in sig


def test_cfo_quality_red_kept_when_profit_but_cash_lags():
    sig = fm._detect_yoy_signals(_curr(cfo_to_op_ratio=0.43, accruals_gap_pct=56.86), {})
    assert "cfo_quality_red" in sig and "accruals_red" in sig


def test_accruals_red_not_raised_when_cash_exceeds_profit():
    """현금이 이익의 2.8배(gap −179%) — 「이익이 현금으로 안 들어왔다」가 아니다."""
    sig = fm._detect_yoy_signals(_curr(cfo_to_op_ratio=2.79, accruals_gap_pct=-179.0), {})
    assert "accruals_red" not in sig and "cfo_quality_red" not in sig


def test_accruals_red_threshold_is_directional_at_30():
    assert "accruals_red" not in fm._detect_yoy_signals(_curr(accruals_gap_pct=-30.85), {})
    assert "accruals_red" not in fm._detect_yoy_signals(_curr(accruals_gap_pct=30.0), {})
    assert "accruals_red" in fm._detect_yoy_signals(_curr(accruals_gap_pct=30.83), {})


def test_thin_operating_profit_still_unreliable_not_red():
    sig = fm._detect_yoy_signals(_curr(accruals_gap_pct=-2999.5, accruals_gap_reliability="thin_op",
                                       cfo_to_op_ratio=31.0), {})
    assert "accruals_ratio_unreliable" in sig and "accruals_red" not in sig


# ── P1-#26 문구 ──

def test_interest_coverage_gloss_matches_the_threshold_of_2():
    gloss = tool._ALERT_KR["interest_coverage_low"]
    assert "2배" in gloss
    sig = fm._detect_yoy_signals(_curr(interest_coverage_ratio=1.18), {})
    assert "interest_coverage_low" in sig                      # 문턱 자체는 그대로 2배
    assert "interest_coverage_low" not in fm._detect_yoy_signals(_curr(interest_coverage_ratio=2.0), {})


def test_leverage_gloss_describes_level_with_the_real_definition():
    gloss = tool._ALERT_KR["roe_driven_by_leverage"]
    assert "올라간" not in gloss and "자기자본 비중" not in gloss
    assert "지배자본" in gloss and "2배" in gloss
    assert "roe_driven_by_leverage" in fm._detect_yoy_signals(_curr(equity_multiplier=2.6263), {})
    assert "roe_driven_by_leverage" not in fm._detect_yoy_signals(_curr(equity_multiplier=2.0), {})


def test_tool_description_states_the_eps_exception():
    import inspect
    from open_proxy_mcp.tools import financial_metrics as t
    src = inspect.getsource(t)
    assert "EPS(원/주)는 공시 소수" in src
