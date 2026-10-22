"""financial_metrics 연간 전년비·평균자본 ROE 가 연결(CFS)↔별도(OFS)가 바뀐 해에 다른 재무제표 기준끼리 견주지 않는다(DATA-Q5 의 연간 변형).
network 0 · DB 0. 전기 주요계정은 요청 fs_div 로 불러 연결이 없으면 별도 행이 그대로 오므로, 전기 행의 실제 기준을 당기와 대조한다.
"""
from __future__ import annotations

import asyncio

import open_proxy_mcp.services.financial_metrics as fm


def _rows(fs: str, *, rev, rev_prev, op, op_prev, ni, ni_prev, eq, eq_prev):
    base = {"fs_div": fs, "thstrm_dt": "2025.12.31", "frmtrm_dt": "2024.12.31", "ord": "1"}

    def row(sj, nm, cur, prev):
        return {**base, "sj_div": sj, "account_nm": nm, "thstrm_amount": str(cur), "frmtrm_amount": str(prev)}
    return [row("BS", "자산총계", eq * 2, eq_prev * 2), row("BS", "자본총계", eq, eq_prev),
            row("IS", "매출액", rev, rev_prev), row("IS", "영업이익", op, op_prev), row("IS", "당기순이익(손실)", ni, ni_prev)]


def _run(monkeypatch, cur_rows, prev_rows):
    async def acnt(corp_code, year, reprt_code, fs_div):
        return (cur_rows, None) if year == 2025 else (prev_rows, None)

    async def acnt_all(corp_code, year, reprt_code, fs_div):
        return [], "no_filing"

    async def none(*a, **k):
        return None

    monkeypatch.setattr(fm, "_safe_fetch_acnt", acnt)
    monkeypatch.setattr(fm, "_safe_fetch_acnt_all", acnt_all)
    monkeypatch.setattr(fm, "_periodic_filing_ref", none)
    monkeypatch.setattr(fm, "_accrual_payout_pct", none)
    return asyncio.run(fm._fetch_year_metrics("00000001", 2025, "CFS", allow_quarterly_fallback=False, reprt_code="11011"))


def test_current_cfs_previous_ofs_uses_the_current_report_prior_column(monkeypatch):
    """당기 CFS(연결 신규 작성) · 전기 OFS — 종전 전년비는 당기 연결 1000 대 전기 별도 400(+150%). 당기 사업보고서의 전기 칸(연결로 다시 적은 800)을 쓴다(+25%)."""
    cur = _rows("CFS", rev=1000, rev_prev=800, op=100, op_prev=80, ni=100, ni_prev=60, eq=600, eq_prev=500)
    prev_ofs = _rows("OFS", rev=400, rev_prev=0, op=40, op_prev=0, ni=30, ni_prev=0, eq=250, eq_prev=0)
    m, warnings, _ = _run(monkeypatch, cur, prev_ofs)
    assert m["fs_div"] == "CFS"
    assert m["prev_fs_div"] == "OFS"
    assert m["prev_revenue_krw"] == 800 and m["revenue_yoy_pct"] == 25.0
    assert m["prev_net_income_krw"] == 60
    assert m["net_income_yoy_pct"] == round((100 - 60) / 60 * 100, 2)
    assert any("prev_fs_div" in w or "OFS" in w for w in warnings)


def test_current_ofs_previous_cfs_uses_the_current_report_prior_column(monkeypatch):
    cur = _rows("OFS", rev=500, rev_prev=450, op=50, op_prev=45, ni=40, ni_prev=36, eq=300, eq_prev=280)
    prev_cfs = _rows("CFS", rev=900, rev_prev=0, op=90, op_prev=0, ni=80, ni_prev=0, eq=600, eq_prev=0)
    m, warnings, _ = _run(monkeypatch, cur, prev_cfs)
    assert m["fs_div"] == "OFS" and m["prev_fs_div"] == "CFS"
    assert m["prev_revenue_krw"] == 450 and m["revenue_yoy_pct"] == round((500 - 450) / 450 * 100, 2)


def test_same_basis_is_unchanged_and_has_no_prev_fs_div(monkeypatch):
    cur = _rows("CFS", rev=1000, rev_prev=800, op=100, op_prev=80, ni=100, ni_prev=60, eq=600, eq_prev=500)
    prev = _rows("CFS", rev=800, rev_prev=0, op=80, op_prev=0, ni=60, ni_prev=0, eq=500, eq_prev=0)
    m, warnings, _ = _run(monkeypatch, cur, prev)
    assert "prev_fs_div" not in m
    assert m["prev_revenue_krw"] == 800 and m["revenue_yoy_pct"] == 25.0


def test_quarterly_report_with_mixed_prior_basis_drops_prior_and_ratios(monkeypatch):
    """분기·반기 보고서의 전기 칸은 전년 말 잔액이라 대체하지 못한다 — 전기를 버리고 전년비·평균 기준 ROE·ROA 를 비운다."""
    cur = _rows("CFS", rev=1000, rev_prev=800, op=100, op_prev=80, ni=100, ni_prev=60, eq=600, eq_prev=500)
    prev_ofs = _rows("OFS", rev=400, rev_prev=0, op=40, op_prev=0, ni=30, ni_prev=0, eq=250, eq_prev=0)

    async def acnt(corp_code, year, reprt_code, fs_div):
        return (cur, None) if year == 2025 else (prev_ofs, None)

    async def acnt_all(*a, **k):
        return [], "no_filing"

    async def none(*a, **k):
        return None

    monkeypatch.setattr(fm, "_safe_fetch_acnt", acnt)
    monkeypatch.setattr(fm, "_safe_fetch_acnt_all", acnt_all)
    monkeypatch.setattr(fm, "_periodic_filing_ref", none)
    monkeypatch.setattr(fm, "_accrual_payout_pct", none)
    m, warnings, _ = asyncio.run(fm._fetch_year_metrics("00000001", 2025, "CFS", allow_quarterly_fallback=False, reprt_code="11013"))
    assert m["prev_fs_div"] == "OFS"
    assert m["revenue_yoy_pct"] is None and m["net_income_yoy_pct"] is None
    assert m["roe_pct"] is None and m["roa_pct"] is None
    assert any("전년비" in w and "prev_fs_div=OFS" in w for w in warnings)
