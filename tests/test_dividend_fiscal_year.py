# -*- coding: utf-8 -*-
"""FY 라벨이 어느 12개월인지 확정되는가 (2026-08-28 U 지적 B-6).

포시에스(6월 결산)는 회사 IR 문서가 「FY2025」를 2025-07-01~2026-06-30 으로 쓰고,
이 도구는 결산 **종료**연도로 부른다. 라벨만 같고 가리키는 기간이 다르면 「40% 배당 계획」이
어느 해 것인지 확정할 수 없다. 구간·결산월·라벨 기준을 항상 붙이고, 결산은 끝났는데 결의가
없는 사업연도는 지급 완료처럼 보이지 않게 「미결의」로 세운다.
"""
from __future__ import annotations

import calendar
from datetime import date

from open_proxy_mcp.services.dividend import (
    FISCAL_YEAR_BASIS,
    _alot_multiyear_summaries,
    _fiscal_end_month,
    _fiscal_period,
    _judgeable_undecided_years,
    _latest_completed_fiscal_year,
)
from open_proxy_mcp.tools.dividend_disclosure import _render


def test_fiscal_end_month_reads_settlement_date():
    assert _fiscal_end_month("2025-06-30") == 6
    assert _fiscal_end_month("2025-12-31") == 12
    assert _fiscal_end_month("") is None
    assert _fiscal_end_month(None) is None


def test_fiscal_period_spans_the_twelve_months_ending_in_that_year():
    assert _fiscal_period(2025, 6) == {"start": "2024-07-01", "end": "2025-06-30"}
    assert _fiscal_period(2025, 12) == {"start": "2025-01-01", "end": "2025-12-31"}
    assert _fiscal_period(2025, 3) == {"start": "2024-04-01", "end": "2025-03-31"}
    assert _fiscal_period(2024, 2) == {"start": "2023-03-01", "end": "2024-02-29"}  # 윤년
    # 결산월을 모르면 12월로 단정하지 않는다.
    assert _fiscal_period(2025, None) is None


def test_latest_completed_fiscal_year_tracks_the_settlement_month():
    assert _latest_completed_fiscal_year(date(2026, 8, 28), 6) == 2026
    assert _latest_completed_fiscal_year(date(2026, 6, 15), 6) == 2025   # 6월 결산 진행 중
    assert _latest_completed_fiscal_year(date(2026, 8, 28), 12) == 2025
    assert _latest_completed_fiscal_year(date(2026, 8, 28), None) is None


def test_multiyear_summaries_do_not_hardcode_december():
    """예전엔 stlm_dt 를 f"{fy}-12-31" 로 박아 6월 결산 회사 구간이 통째로 틀렸다."""
    latest = {
        "stlm_dt": "2025-06-30",
        "items": [
            {"category": "주당액면가액(원)", "stock_type": "-",
             "current": "500", "previous": "500", "before_previous": "500"},
            {"category": "주당 현금배당금(원)", "stock_type": "보통주",
             "current": "50", "previous": "50", "before_previous": "100"},
        ],
    }
    out = _alot_multiyear_summaries(latest)
    assert out[2025]["stlm_dt"] == "2025-06-30"
    assert out[2025]["period_start"] == "2024-07-01"
    assert out[2025]["period_end"] == "2025-06-30"
    assert out[2025]["fiscal_year_end_month"] == 6
    assert out[2024]["period_start"] == "2023-07-01"
    assert out[2023]["cash_dps"] == 100


def _payload(**data) -> dict:
    base = {
        "canonical_name": "포시에스",
        "identifiers": {"ticker": "189690", "corp_code": "00939942"},
        "year": 2025,
        "fiscal_year_basis": FISCAL_YEAR_BASIS,
        "fiscal_year_end_month": 6,
        "window": {"start_date": "20230117", "end_date": "20260630"},
        "summary": {
            "fiscal_year": 2025, "fiscal_year_end_month": 6,
            "period_start": "2024-07-01", "period_end": "2025-06-30",
            "cash_dps": 50, "total_amount_mil": 1318, "payout_ratio_dart": 28.96,
        },
        "policy_signals": {"trend": "stable"},
    }
    base.update(data)
    return {"status": "exact", "subject": "포시에스", "data": base, "warnings": []}


def test_summary_states_the_date_span_and_the_labelling_rule():
    out = _render(_payload(), "summary")
    assert "FY2025" in out
    assert "2024-07-01~2025-06-30" in out
    assert "6월 결산" in out
    assert "결산 종료연도 기준" in out


def test_december_year_end_company_gets_no_extra_line():
    """12월 결산은 FY와 달력연도가 같다 — 군더더기를 붙이지 않는다."""
    out = _render(_payload(
        fiscal_year_end_month=12,
        summary={"fiscal_year": 2025, "fiscal_year_end_month": 12,
                 "period_start": "2025-01-01", "period_end": "2025-12-31", "cash_dps": 1668},
    ), "summary")
    assert "결산 종료연도 기준" not in out
    assert "12월 결산" not in out


def test_undecided_fiscal_year_is_not_shown_as_paid():
    history = [
        {"year": 2025, "period_start": "2024-07-01", "period_end": "2025-06-30",
         "annual_dps": 50, "decision_count": 1, "payout_ratio": 28.96,
         "yield_pct": 2.0, "has_special": False, "pattern": "연간배당"},
        {"year": 2026, "period_start": "2025-07-01", "period_end": "2026-06-30",
         "annual_dps": 0, "decision_count": 0, "payout_ratio": None, "yield_pct": None,
         "has_special": False, "pattern": "미결의 (결산 종료 · 배당 결의 공시·사업보고서 모두 미확인)",
         "pending_confirmation": True},
    ]
    out = _render(_payload(history=history), "history")
    assert "2025-07-01~2026-06-30" in out
    assert "미결의" in out
    # 「0원 지급」으로 읽히면 안 된다.
    assert "| 0원 |" not in out
    assert "| 2026 | 2025-07-01~2026-06-30 | - |" in out


def test_undecided_check_covers_every_completed_year_when_search_reaches_today():
    """기본 조회(작년 기준) — 검색이 오늘까지 덮으니 끝난 사업연도를 다 본다(포시에스 6월 결산 FY2026)."""
    today = date(2026, 10, 19)
    assert _judgeable_undecided_years(2025, 2026, 6, date(2026, 12, 31), today) == [2026]
    assert _judgeable_undecided_years(2024, 2025, 12, date(2026, 12, 31), today) == [2025]


def test_past_year_query_does_not_call_unsearched_years_undecided():
    """year=2020 → 결정공시 검색은 2021-12-31 까지. 그 뒤 사업연도의 결의는 읽지 않았으니 「미결의」가 아니다.

    삼성전자 detail year=2020 실측(261019): FY2022~FY2025 를 「결의 공시 없음 — 미결의」로 경고했다
    (원장엔 2024 1,446원·2025 1,668원 확정 배당이 있다).
    """
    today = date(2026, 10, 19)
    assert _judgeable_undecided_years(2020, 2025, 12, date(2021, 12, 31), today) == []
    # year=2024 → 검색 2025-12-31 까지 — FY2025 결산배당 결의(2026년 초)는 범위 밖
    assert _judgeable_undecided_years(2024, 2025, 12, date(2025, 12, 31), today) == []
    # 3월 결산 FY2025(2025-03-31 종료)는 2025-12-31 까지의 검색이 결의 시기를 덮는다 — 판정 대상
    assert _judgeable_undecided_years(2024, 2026, 3, date(2025, 12, 31), today) == [2025]
    # 결산월을 모르면 구간을 지어내지 않는다
    assert _judgeable_undecided_years(2020, 2025, None, date(2021, 12, 31), today) == []
    assert _judgeable_undecided_years(2020, None, 12, date(2021, 12, 31), today) == []


def _payload_for(monkeypatch, year: int, end_month: int = 12):
    """build_dividend_payload 를 DART 0콜로 — 사업보고서 배당 사항은 `year` 해 것만(결산월 `end_month`), 결정공시 검색은 0건."""
    import asyncio

    from open_proxy_mcp.services import dividend as dv
    from open_proxy_mcp.services.company import CompanyResolution
    from open_proxy_mcp.services.contracts import AnalysisStatus

    class _Client:
        def api_call_snapshot(self):
            return 0

    async def resolve(_q):
        return CompanyResolution(status=AnalysisStatus.EXACT, query="삼성전자", candidates=[],
                                 selected={"corp_name": "삼성전자", "stock_code": "005930", "corp_code": "00126380"})

    async def annual(_corp, y):
        if y != year:
            return {}, None          # docstring 대로 `year` 해 보고서만 있다 — 다른 해는 DART 013(보고서 없음)과 같은 빈 답
        last = calendar.monthrange(y, end_month)[1]
        return {"period": f"{y}", "stlm_dt": f"{y}-{end_month:02d}-{last:02d}", "cash_dps": 1000, "total_dps": 1000,
                "source": "alotMatter"}, None

    searched = []

    async def search(corp, start, end):
        searched.append(end)
        return [], [], [], None

    async def details(_f):
        return []

    async def capital(*_a, **_k):
        return False, []

    monkeypatch.setattr(dv, "today_kst", lambda: date(2026, 10, 19))
    monkeypatch.setattr(dv, "get_dart_client", lambda: _Client())
    monkeypatch.setattr(dv, "resolve_company_query", resolve)
    monkeypatch.setattr(dv, "_annual_summary", annual)
    monkeypatch.setattr(dv, "_search_dividend_filings", search)
    monkeypatch.setattr(dv, "_decision_details", details)
    monkeypatch.setattr(dv, "_detect_capital_reserve_reduction", capital)
    p = asyncio.run(dv.build_dividend_payload("삼성전자", scope="summary", year=year))
    return p, searched


def test_past_year_payload_has_no_undecided_years_it_never_searched(monkeypatch):
    """도구 전체 경로 — year=2020 은 결정공시를 2021-12-31 까지만 찾는다. 그 뒤 사업연도를 「미결의」로 세우지 않는다."""
    p, searched = _payload_for(monkeypatch, year=2020)
    assert searched == [2020]                                  # 검색 끝 = 2021-12-31(… end_year + 1 년 말)
    assert p["data"].get("undecided_fiscal_years") in (None, [])
    assert not any("미결의" in w for w in p["warnings"])
    # 6월 결산이면 FY2021(2021-06-30 종료)의 결의 시기는 그 검색이 덮는다 — 그 해만 판정 대상이다(결의 0건 → 미결의)
    p6, _ = _payload_for(monkeypatch, year=2020, end_month=6)
    assert [f["fiscal_year"] for f in p6["data"]["undecided_fiscal_years"]] == [2021]


def test_recent_year_payload_still_flags_an_undecided_year(monkeypatch):
    """대조 — 검색이 오늘까지 덮는 기본 조회(작년 기준)는 종전처럼 끝난 사업연도를 다 본다(6월 결산 FY2026 미결의)."""
    p, _ = _payload_for(monkeypatch, year=2025, end_month=6)
    assert [f["fiscal_year"] for f in p["data"]["undecided_fiscal_years"]] == [2026]
    assert any("FY2026" in w and "미결의" in w for w in p["warnings"])


# ── 반기·분기 결산 — 결산월은 가장 늦은 결산기로, 사업연도 구간은 그 해 결산기들이 덮는 12개월 (261021, DATA-FY3) ──────────────────────
def _two_period_response() -> dict:
    """롯데리츠 FY2025 모양 — 결산기 둘(2025-06-30 · 2025-12-31). 한 행의 당기·전기·전전기는 연속한 결산기."""
    def rows(stlm, dps, prev, before):
        base = {"stlm_dt": stlm, "stock_type": "보통주식"}
        return [{**base, "category": "주당액면가액(원)", "stock_type": "-", "current": "500", "previous": "500", "before_previous": "500"},
                {**base, "category": "주당 현금배당금(원)", "current": dps, "previous": prev, "before_previous": before}]
    return {"stlm_dt": "2025-12-31", "items": rows("2025-06-30", "117", "112", "103") + rows("2025-12-31", "123", "117", "112")}


def test_two_settlement_periods_name_the_calendar_year_and_the_latest_month():
    out = _alot_multiyear_summaries(_two_period_response())
    assert out[2025]["cash_dps"] == 240 and out[2024]["cash_dps"] == 215
    assert (out[2025]["fiscal_year_end_month"], out[2025]["period_start"], out[2025]["period_end"]) == (12, "2025-01-01", "2025-12-31"), \
        "종전: 결산월을 첫 행(2025-06-30)에서 읽어 6 → 사업연도 2024-07-01~2025-06-30"
    assert out[2025]["stlm_dt"] == "2025-12-31"
    assert _fiscal_end_month(out[2025]["stlm_dt"]) == 12


def test_two_period_payload_renders_the_settlement_composition_not_a_fiscal_month_line():
    p = {"status": "exact", "subject": "롯데리츠", "warnings": [], "data": {
        "canonical_name": "롯데리츠", "year": 2025, "fiscal_year_basis": FISCAL_YEAR_BASIS, "fiscal_year_end_month": 12,
        "window": {"start_date": "20230101", "end_date": "20260630"},
        "summary": {"fiscal_year": 2025, "fiscal_year_end_month": 12, "period_start": "2025-01-01", "period_end": "2025-12-31",
                    "cash_dps": 240, "total_amount_mil": 69352, "settlement_months": 6,
                    "settlement_periods": [{"stlm_dt": "2025-06-30", "dps_krw": 117}, {"stlm_dt": "2025-12-31", "dps_krw": 123}],
                    "latest_period": {"stlm_dt": "2025-12-31", "cash_dps": 123, "total_amount_mil": 35543}}}}
    md = _render(p, "summary")
    assert "- 연간 DPS(보통주): 240원" in md
    assert "  - 반기 결산기 2개의 합: 2025-06-30 117원 + 2025-12-31 123원 _(사업보고서 한 통은 가장 늦은 결산기 2025-12-31 분 123원)_" in md
    assert "결산 ·" not in md, "12월로 읽으니 비12월 결산 안내 줄(「N월 결산」)을 붙이지 않는다"
