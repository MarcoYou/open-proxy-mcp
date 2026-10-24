"""financial_metrics — 전년 보고서의 보고 통화가 올해와 다르면(통화 전환 해) 전기 값을 전년 보고서 당기 칸에서 읽지 않는다.
network 0 · DB 0.

종전: `_fetch_year_metrics` 가 전기를 전년 보고서의 당기 칸에서 읽어, 전년 보고서가 원화이고 올해 보고서가 달러면 원화 금액과 달러 금액으로
전년비·평균자본 ROE·회전일수를 계산하고 `_normalize_currency` 가 올해 환율 하나를 전기 금액에까지 곱했다
(실제 사례: 원화 → 달러 전환 해 매출 전년비 -99.91%, ROE 0.03%). 사업보고서는 올해 보고서의 전기 칸(올해 통화로 다시 적은 값)을 쓰고,
분기·반기는 전기를 비운다(연결/별도가 바뀐 경우와 같은 처리). 고정본 숫자는 실제 공시 원응답의 행을 줄인 것이다.
"""
from __future__ import annotations

import asyncio

import open_proxy_mcp.services.financial_metrics as fm

RATE = 1000.0   # 달러 → 원 (시험용 고정 환율)


def _row(sj, nm, cur, prev, *, ccy, fs="CFS", acc_id=None, dt=("2023.12.31 현재", "2022.12.31 현재")):
    r = {"sj_div": sj, "account_nm": nm, "thstrm_amount": f"{cur:,}", "frmtrm_amount": f"{prev:,}", "ord": "1",
         "fs_div": fs, "thstrm_dt": dt[0], "frmtrm_dt": dt[1]}
    if ccy:
        r["currency"] = ccy
    if acc_id:
        r["account_id"] = acc_id
    return r


# (당기, 전기) — 원화 보고서는 2022 사업보고서, 달러 보고서는 2023 사업보고서의 실제 값
KRW_REPORT = dict(ccy="KRW", assets=(9_248_386_178_000, 8_580_899_465_000), equity=(5_024_387_667_000, 4_321_265_808_000),
                  revenue=(8_621_911_872_000, 5_816_199_969_000), op=(1_071_606_774_000, 595_338_266_000),
                  ni=(644_056_037_000, 385_888_628_000), dt=("2022.12.31 현재", "2021.12.31 현재"))
USD_REPORT = dict(ccy="USD", assets=(8_043_860_515, 7_297_708_655), equity=(4_618_281_799, 3_964_639_523),
                  revenue=(7_475_740_778, 6_673_564_667), op=(1_064_722_975, 829_449_107),
                  ni=(705_892_351, 498_514_677), dt=("2023.12.31 현재", "2022.12.31 현재"))


def _acnt(rep, *, fs="CFS", ccy="__default__"):
    c = rep["ccy"] if ccy == "__default__" else ccy
    kw = dict(ccy=c, fs=fs, dt=rep["dt"])
    return [_row("BS", "자산총계", *rep["assets"], **kw), _row("BS", "자본총계", *rep["equity"], **kw),
            _row("IS", "매출액", *rep["revenue"], **kw), _row("IS", "영업이익", *rep["op"], **kw),
            _row("IS", "당기순이익(손실)", *rep["ni"], **kw)]


def _acnt_all(rep, *, fs="CFS", ccy="__default__"):
    c = rep["ccy"] if ccy == "__default__" else ccy
    kw = dict(ccy=c, fs=fs, dt=rep["dt"])
    return [_row("BS", "자산총계", *rep["assets"], acc_id="ifrs-full_Assets", **kw),
            _row("BS", "자본총계", *rep["equity"], acc_id="ifrs-full_Equity", **kw),
            _row("IS", "매출액", *rep["revenue"], acc_id="ifrs-full_Revenue", **kw),
            _row("IS", "영업이익", *rep["op"], acc_id="dart_OperatingIncomeLoss", **kw),
            _row("IS", "당기순이익(손실)", *rep["ni"], acc_id="ifrs-full_ProfitLoss", **kw)]


def _run(monkeypatch, cur_rep, prev_rep, *, reprt_code="11011", cur_fs="CFS", prev_fs="CFS", cur_ccy="__default__", prev_ccy="__default__",
         with_detail=False):
    """올해(2023) 보고서 = cur_rep, 전년(2022) 보고서 = prev_rep. with_detail 이면 전체 재무제표(acnt_all)도 같은 값으로 준다(분기 단독 계산용)."""
    cur_rows, prev_rows = _acnt(cur_rep, fs=cur_fs, ccy=cur_ccy), _acnt(prev_rep, fs=prev_fs, ccy=prev_ccy)

    async def acnt(corp_code, year, rc, fs_div):
        return (cur_rows, None) if year == 2023 else (prev_rows, None)

    async def acnt_all(corp_code, year, rc, fs_div):
        if not with_detail:
            return [], "no_filing"
        # 올해 · 같은 해 직전 분기(standalone 의 CF 차분 기준)는 올해 보고서, 전년은 전년 보고서
        return (_acnt_all(cur_rep, fs=cur_fs, ccy=cur_ccy) if year == 2023 else _acnt_all(prev_rep, fs=prev_fs, ccy=prev_ccy)), None

    async def none(*a, **k):
        return None

    async def fx(ccy, date):
        return RATE if ccy == "USD" else 1.0

    monkeypatch.setattr(fm, "_safe_fetch_acnt", acnt)
    monkeypatch.setattr(fm, "_safe_fetch_acnt_all", acnt_all)
    monkeypatch.setattr(fm, "_periodic_filing_ref", none)
    monkeypatch.setattr(fm, "_accrual_payout_pct", none)
    monkeypatch.setattr(fm, "fx_to_krw", fx)
    return asyncio.run(fm._fetch_year_metrics("00000002", 2023, "CFS", allow_quarterly_fallback=False, reprt_code=reprt_code))


def test_business_report_prior_in_other_currency_uses_the_current_report_prior_column(monkeypatch):
    """전년 보고서 원화 · 올해 보고서 달러(사업보고서) — 전기는 올해 보고서의 전기 칸(달러)이다: 매출 전년비 +12.02%."""
    m, warnings, _ = _run(monkeypatch, USD_REPORT, KRW_REPORT)
    assert m["functional_currency"] == "USD" and m["fx_rate_to_krw"] == RATE
    assert m["revenue_krw"] == 7_475_740_778 * 1000
    assert m["prev_revenue_krw"] == 6_673_564_667 * 1000           # 종전: 8,621,911,872,000 × 1000 = 8.6조 × 1000
    assert m["revenue_yoy_pct"] == round((7_475_740_778 / 6_673_564_667 - 1) * 100, 2) == 12.02
    assert m["net_income_yoy_pct"] == round((705_892_351 / 498_514_677 - 1) * 100, 2)
    # 평균자본 = (올해 말 + 전년 말) 모두 달러 → 달러 기준 ROE (종전 0.03%)
    assert m["roe_pct"] == round(705_892_351 / ((4_618_281_799 + 3_964_639_523) / 2) * 100, 2)
    assert "prev_fs_div" not in m                                   # 재무제표 기준은 같다 — 새 키 없음
    assert any("보고 통화(KRW)" in w and "USD" in w and "전기 칸" in w for w in warnings)


def test_business_report_same_currency_is_unchanged(monkeypatch):
    """통화가 같으면(달러 → 달러) 전년 보고서 당기 칸을 그대로 쓴다 — 올해 보고서 전기 칸으로 바꾸지 않는다."""
    prev_usd = dict(USD_REPORT, revenue=(6_673_564_667, 5_000_000_000), dt=("2022.12.31 현재", "2021.12.31 현재"))
    m, warnings, _ = _run(monkeypatch, USD_REPORT, prev_usd)
    assert m["prev_revenue_krw"] == 6_673_564_667 * 1000
    assert not any("보고 통화" in w for w in warnings)


def test_currency_only_compared_when_both_reports_state_it(monkeypatch):
    """currency 칸이 없는 쪽이 있으면(옛 고정본·캐시) 통화 전환으로 보지 않는다 — 칸이 없으면 statement_currency 는 원화로 본다."""
    m, warnings, _ = _run(monkeypatch, USD_REPORT, KRW_REPORT, prev_ccy=None)
    assert m["prev_revenue_krw"] == 8_621_911_872_000 * 1000        # 종전과 같은 읽기
    assert not any("보고 통화(" in w for w in warnings)


def test_currency_and_basis_both_changed_names_both_facts(monkeypatch):
    """통화와 연결/별도가 함께 바뀌면 경고에 두 사실을 다 싣고 prev_fs_div 꼬리도 남긴다."""
    m, warnings, _ = _run(monkeypatch, USD_REPORT, KRW_REPORT, prev_fs="OFS")
    assert m["prev_fs_div"] == "OFS"
    assert m["prev_revenue_krw"] == 6_673_564_667 * 1000
    w = next(w for w in warnings if "전기 칸" in w)
    assert "보고 통화(KRW)" in w and "재무제표 기준(OFS)" in w and "prev_fs_div=OFS" in w


def test_quarterly_report_prior_in_other_currency_drops_prior_and_ratios(monkeypatch):
    """분기·반기 보고서의 재무상태표 전기 칸은 직전 사업연도 말 잔액이라 평균을 낼 수 없다 — 전기를 비우고 전년비·평균 기준 ROE·ROA 를 비운다."""
    m, warnings, _ = _run(monkeypatch, USD_REPORT, KRW_REPORT, reprt_code="11013")
    assert m["revenue_yoy_pct"] is None and m["net_income_yoy_pct"] is None
    assert m["roe_pct"] is None and m["roa_pct"] is None and m["roe_dupont_pct"] is None
    assert "prev_fs_div" not in m
    assert any("보고 통화(KRW)" in w and "전기 값을 쓰지 않았다" in w and "filing_section(손익계산서)" in w for w in warnings)


def test_half_year_standalone_prior_is_dropped_too(monkeypatch):
    """반기(11012): 분기 단독(3개월) 블록도 버린 전기 보고서를 다시 읽지 않는다 — 통화 전환 · 연결/별도 전환 두 갈래 모두."""
    for kw in ({}, {"prev_fs": "OFS"}):
        m, warnings, _ = _run(monkeypatch, USD_REPORT, KRW_REPORT if not kw else USD_REPORT, reprt_code="11012", with_detail=True, **kw)
        sa = m["standalone"]
        assert sa is not None, kw
        assert sa["revenue_yoy_pct"] is None and sa["net_income_yoy_pct"] is None and sa["operating_profit_yoy_pct"] is None, kw
        assert m["revenue_yoy_pct"] is None and m["roe_pct"] is None, kw
        # 분기 단독 ROE·ROA 는 평균 대신 기말 기준으로 나간다 — 응답이 그 사실을 말한다(null 로 두지 않은 칸을 읽는 쪽에 알림)
        assert any("분기 단독(standalone) ROE·ROA" in w and "기말" in w for w in warnings), kw


def test_dropped_prior_explains_the_ttm_gap_truthfully(monkeypatch):
    """전기를 비워 TTM 분모가 안 서면 「직전 FY 미조회」가 아니라 전기 보고서를 쓰지 않았기 때문이라고 말한다."""
    m, warnings, _ = _run(monkeypatch, USD_REPORT, KRW_REPORT, reprt_code="11012", with_detail=True)
    ttm = [w for w in warnings if "TTM 회전일수 분모 계산 실패" in w]
    assert ttm and "직전 FY 미조회" not in ttm[0] and "전기 보고서를 쓰지 않아" in ttm[0]
