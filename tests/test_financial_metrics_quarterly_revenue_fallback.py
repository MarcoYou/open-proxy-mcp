"""분기 매출 lazy 폴백 — 주요계정에 매출 행이 없는 분기만 전체 재무제표를 추가 조회한다.

리파인 실측(260906): 주요계정(fnlttSinglAcnt)은 12분기 전부 매출 행이 없고, 전체 재무제표
(fnlttSinglAcntAll)의 「영업수익」은 분기보고서에서 thstrm=당기 3개월·thstrm_add=누적으로
주요계정과 같은 규칙이다. Q4 = 연간 − 9개월 누적을 같은 소스에서 읽어야 gap 이 안 난다. DART 콜 0.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import open_proxy_mcp.services.financial_metrics as fm  # noqa: E402

_END = {"11013": "2024.03.31", "11012": "2024.06.30", "11014": "2024.09.30", "11011": "2024.12.31"}
_OP = {"11013": (10, 10), "11012": (12, 22), "11014": (11, 33), "11011": (45, 45)}      # (당기, 누적)
_REV = {"11013": (100, 100), "11012": (120, 220), "11014": (110, 330), "11011": (450, 450)}


def _acnt(rc: str, with_revenue: bool) -> list[dict]:
    cur, cum = _OP[rc]
    rows = [
        {"fs_div": "OFS", "sj_div": "IS", "account_nm": "영업이익", "thstrm_amount": str(cur),
         "thstrm_add_amount": str(cum), "thstrm_dt": _END[rc], "ord": "2"},
        {"fs_div": "OFS", "sj_div": "IS", "account_nm": "당기순이익(손실)", "thstrm_amount": str(cur),
         "thstrm_add_amount": str(cum), "thstrm_dt": _END[rc], "ord": "3"},
        {"fs_div": "OFS", "sj_div": "BS", "account_nm": "자산총계", "thstrm_amount": "1000", "thstrm_dt": _END[rc], "ord": "1"},
    ]
    if with_revenue:
        r, c = _REV[rc]
        rows.insert(0, {"fs_div": "OFS", "sj_div": "IS", "account_nm": "매출액", "thstrm_amount": str(r),
                        "thstrm_add_amount": str(c), "thstrm_dt": _END[rc], "ord": "1"})
    return rows


def _acnt_all(rc: str) -> list[dict]:
    r, c = _REV[rc]
    return [{"fs_div": "OFS", "sj_div": "CIS", "account_nm": "영업수익", "account_id": "-표준계정코드 미사용-",
             "thstrm_amount": str(r), "thstrm_add_amount": str(c), "ord": "11"}]


def _run(monkeypatch, with_revenue_rc: set[str]):
    calls_all: list[tuple[int, str, str]] = []

    async def fake_acnt(corp_code, year, reprt_code, fs_div):
        if year != 2024:
            return [], "no_filing"
        return _acnt(reprt_code, reprt_code in with_revenue_rc), None

    async def fake_acnt_all(corp_code, year, reprt_code, fs_div):
        calls_all.append((year, reprt_code, fs_div))
        return _acnt_all(reprt_code), None

    monkeypatch.setattr(fm, "_safe_fetch_acnt", fake_acnt)
    monkeypatch.setattr(fm, "_safe_fetch_acnt_all", fake_acnt_all)
    rows, warnings = asyncio.run(fm._build_quarterly("01210066", 2024, "CFS", fiscal_month=12, induty_code="63991"))
    return rows, warnings, calls_all


def test_missing_quarters_are_filled_from_full_statement_and_q4_is_derived_from_same_source(monkeypatch):
    rows, warnings, calls_all = _run(monkeypatch, with_revenue_rc=set())
    by_q = {r["fiscal_quarter"]: r for r in rows if r["fiscal_year"] == 2024}
    assert [by_q[q]["revenue_krw"] for q in ("Q1", "Q2", "Q3", "Q4")] == [100, 120, 110, 120]  # Q4 = 450 − 330
    assert by_q["Q4"]["basis"] == "standalone_3m_derived"
    assert by_q["Q4"]["annual_revenue_krw"] == 450
    assert all(by_q[q]["revenue_account_nm"] == "영업수익" for q in by_q)
    assert all(by_q[q]["revenue_standard"] is True for q in by_q)
    assert by_q["Q2"]["operating_margin_pct"] == 10.0
    assert sorted(c[2] for c in calls_all) == ["OFS"] * 4          # 실제 기준(OFS)으로, 빈 분기 4개만
    assert any("전체 재무제표 「영업수익」" in w for w in warnings)
    assert not any("분기 합이 연간과 차이" in w for w in warnings)  # 같은 소스라 gap 없음


def test_quarters_that_already_have_revenue_do_not_trigger_extra_calls(monkeypatch):
    rows, _, calls_all = _run(monkeypatch, with_revenue_rc={"11013", "11012", "11014", "11011"})
    assert calls_all == []
    by_q = {r["fiscal_quarter"]: r for r in rows if r["fiscal_year"] == 2024}
    assert by_q["Q4"]["revenue_krw"] == 120
    assert by_q["Q1"]["revenue_account_nm"] == "매출액"


def test_partial_gap_only_fetches_the_missing_quarter(monkeypatch):
    rows, _, calls_all = _run(monkeypatch, with_revenue_rc={"11013", "11012", "11011"})
    assert calls_all == [(2024, "11014", "OFS")]
    by_q = {r["fiscal_quarter"]: r for r in rows if r["fiscal_year"] == 2024}
    assert by_q["Q3"]["revenue_krw"] == 110
    assert by_q["Q4"]["revenue_krw"] == 120   # cum9 는 전체 재무제표 thstrm_add(330)로 채워짐
