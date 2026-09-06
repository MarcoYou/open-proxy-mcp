"""연결 미작성 회사의 전체 재무제표 호출 기준 — 요청(CFS)이 아니라 실제(OFS)로.

260906 리파인 실측: 주요계정(fnlttSinglAcnt)은 CFS 가 없으면 OFS 행을 돌려주지만, 전체 재무제표
(fnlttSinglAcntAll)는 요청 fs_div 그대로라 CFS 로 부르면 013(없음). 그래서 연결 미작성 회사는
CF(CFO·FCF)·세부 IS·매출 폴백이 통째로 비었다(2022~2025 4개년 전부). DART 콜 0 회귀.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import open_proxy_mcp.services.financial_metrics as fm  # noqa: E402


def _acnt_rows_ofs_only() -> list[dict]:
    """CFS 요청에 OFS 행이 온 상황(리파인) — fs_div 가 전부 OFS."""
    base = {"fs_div": "OFS", "thstrm_dt": "2024.12.31", "ord": "1"}
    return [
        {**base, "sj_div": "BS", "account_nm": "자산총계", "thstrm_amount": "1000"},
        {**base, "sj_div": "BS", "account_nm": "자본총계", "thstrm_amount": "800"},
        {**base, "sj_div": "IS", "account_nm": "영업이익", "thstrm_amount": "100"},
        {**base, "sj_div": "IS", "account_nm": "당기순이익(손실)", "thstrm_amount": "90"},
    ]


def test_acnt_all_is_fetched_with_actual_fs_div_when_consolidated_is_missing(monkeypatch):
    seen: list[tuple[int, str, str]] = []

    async def fake_acnt(corp_code, year, reprt_code, fs_div):
        return _acnt_rows_ofs_only(), None

    async def fake_acnt_all(corp_code, year, reprt_code, fs_div):
        seen.append((year, reprt_code, fs_div))
        if fs_div != "OFS":
            return [], "no_filing"  # DART 실제 동작: CFS 로 부르면 013
        return [{"sj_div": "CIS", "account_nm": "영업수익", "account_id": "-표준계정코드 미사용-",
                 "thstrm_amount": "500", "ord": "11", "fs_div": "OFS"}], None

    async def fake_ref(*a, **k):
        return None

    monkeypatch.setattr(fm, "_safe_fetch_acnt", fake_acnt)
    monkeypatch.setattr(fm, "_safe_fetch_acnt_all", fake_acnt_all)
    monkeypatch.setattr(fm, "_periodic_filing_ref", fake_ref)
    monkeypatch.setattr(fm, "_accrual_payout_pct", fake_ref)

    metrics, warnings, _ = asyncio.run(
        fm._fetch_year_metrics("01210066", 2024, "CFS", allow_quarterly_fallback=False, induty_code="63991")
    )

    assert seen and all(fs == "OFS" for _, _, fs in seen), seen
    assert metrics["fs_div"] == "OFS"
    assert metrics["revenue_krw"] == 500
    assert metrics["revenue_source"] == "fnlttSinglAcntAll"
    assert metrics["revenue_account_nm"] == "영업수익"
    assert any("영업수익" in w for w in warnings)
