# -*- coding: utf-8 -*-
"""dividend_disclosure P0 셋 — 사업연도 귀속(결산월)·요약과 추이표의 FY 통일·종류주식이 보통주를 덮는 옛 규칙 (DATA-FY1·DATA-EQ4).

고정본은 모두 **합성**이다(network 0콜). 사업연도 귀속 규칙은 DART 응답 모양이 아니라 날짜 산수라 합성으로 충분하고,
종류주식 모양(보통주 3,980 → 「1종 종류주식」 4,042)은 `test_dividend_share_class.py` 가 밝힌 한국금융지주 FY2024 꼴을 따른다.
실제 DART 로 확인한 것(2026-10-23, 배치 키): 포시에스(6월 결산) bsns_year 2024·2025·2026 → 결산기준일 2024-06-30·2025-06-30·2026-06-30,
기신정기(3월 결산) bsns_year 2025·2026 → 2025-03-31·2026-03-31 — 요청 연도와 결산기준일의 해가 같다. 그래서 「요청 연도 ≠ 결산기준일의 해」 응답은
실제로 만나지 못했고 합성으로만 지킨다. 실제로 만난 것은 목표 연도 **뒤**에 결산이 끝나고 사업보고서가 이미 나온 해(포시에스 FY2026 보통주 115원)를
추이표가 「사업보고서 배당 항목도 확인되지 않는다 — 미결의」로 내던 모습이다.
"""
from __future__ import annotations

import asyncio
import calendar
from datetime import date

import pytest

from open_proxy_mcp.services import dividend as dv


def _d(dtype, record_date, rcept_dt, dps=100, rcept_no="20250101000001"):
    return {"dividend_type": dtype, "record_date": record_date, "rcept_dt": rcept_dt, "rcept_no": rcept_no,
            "dps_common": dps, "report_name": "현금ㆍ현물배당결정"}


# ── 1. 사업연도 귀속 — 결산월을 받는다 ───────────────────────────────────────────────────────
@pytest.mark.parametrize("em,item,want", [
    # 3월 결산 결산배당: 기준일 = 결산일(03-31) → 그 해 3월에 끝나는 사업연도. 종전은 12월 결산 가정(1~4월 기준일 → 전년)이라 FY-1.
    (3, _d("결산배당", "2025-03-31", "20250512"), 2025),
    (3, _d("결산배당", "2026-03-31", "20260512"), 2026),
    # 3월 결산 결산배당, 기준일이 결산일 뒤 한두 달(분리형): 아직 그 사업연도.
    (3, _d("결산배당", "2025-04-30", "20250512"), 2025),
    # 6월 결산: 중간배당 기준일 2024-12-31 은 2025-06 에 끝나는 사업연도의 것이다(종전 2024).
    (6, _d("중간배당", "2024-12-31", "20250110"), 2025),
    (6, _d("결산배당", "2025-06-30", "20250812"), 2025),
    # 9월 결산: 중간배당 기준일 2025-03-31 은 2025-09 사업연도, 2025-12-31 은 2026-09 사업연도.
    (9, _d("중간배당", "2025-03-31", "20250510"), 2025),
    (9, _d("분기배당", "2025-12-31", "20260110"), 2026),
    # 기준일이 없으면 접수일로 — 3월 결산 결산배당은 5월 접수가 그 해 3월 종료 사업연도(종전 FY-1).
    (3, _d("결산배당", "", "20250512"), 2025),
])
def test_bucket_follows_the_fiscal_year_the_record_date_falls_in(em, item, want):
    assert dv._bucket_fiscal_year(item, em) == want


@pytest.mark.parametrize("item,want", [
    # 12월 결산(기본값·None)의 귀속은 종전과 같다.
    (_d("결산배당", "2025-12-31", "20260220"), 2025),
    (_d("결산배당", "2026-03-31", "20260220"), 2025),        # 선배당-후결의: 다음 해 1~4월 기준일 → 전년 사업연도
    (_d("결산배당", "", "20240222"), 2023),
    (_d("중간배당", "2025-06-30", "20250725"), 2025),
    (_d("분기배당", "2025-03-31", "20250424"), 2025),
    (_d("결산배당", "2025-08-31", "20250720"), 2025),         # 비정상(5~11월)은 기준일 연도
])
def test_december_year_end_bucket_is_unchanged(item, want):
    assert dv._bucket_fiscal_year(item) == want
    assert dv._bucket_fiscal_year(item, 12) == want
    assert dv._bucket_fiscal_year(item, None) == want


def test_non_december_decisions_are_summed_into_the_year_they_belong_to():
    """3월 결산: 2025-03 결산배당(60원)·2025-09 중간배당(20원)은 FY2025 합산 80원, 종전은 결산배당만 FY2024 로 가 FY2025 = 20원."""
    decisions = [
        _d("결산배당", "2025-03-31", "20250512", dps=60, rcept_no="20250512000001"),
        _d("중간배당", "2024-12-31", "20250110", dps=20, rcept_no="20250110000001"),
    ]
    out = dv._decisions_summary_for_year(decisions, 2025, 3)
    assert out["cash_dps"] == 80
    assert out["decision_count"] == 2
    assert dv._decisions_summary_for_year(decisions, 2024, 3) == {}


def test_quarterly_breakdown_and_history_use_the_same_year_for_a_march_year_end_company():
    decisions = [_d("결산배당", "2025-03-31", "20250512", dps=60, rcept_no="20250512000001")]
    rows = dv._quarterly_breakdown(decisions, [2025], 3)
    assert [(r["year"], r["quarter"]) for r in rows] == [(2025, "결산")]
    hist = dv._history_rows(2025, {2025: {"total_dps": 60}}, decisions, 3)
    assert hist[0]["decision_count"] == 1 and hist[0]["pattern"] == "연간배당"


# ── 2. 종류주식이 보통주를 덮지 않는다 — 세 곳 ──────────────────────────────────────────────
def _rows(*pairs, col="current"):
    return [{"category": "주당 현금배당금(원)", "stock_type": t, col: v} for t, v in pairs]


def test_common_cash_dps_keeps_common_when_a_class_row_follows():
    items = _rows(("보통주", "3,980"), ("1종 종류주식", "4,042"))
    assert dv._common_cash_dps_from_items(items) == 3980
    # 삼성전자 꼴(우선주)·표기 없는 단일 행은 종전과 같다.
    assert dv._common_cash_dps_from_items(_rows(("보통주", "1,668"), ("우선주", "1,669"))) == 1668
    assert dv._common_cash_dps_from_items(_rows(("-", "500"))) == 500
    assert dv._common_cash_dps_from_items(_rows(("보통주", "-"), ("우선주", "100"))) == 0
    assert dv._common_cash_dps_from_items(_rows(("보통주", "50"), col="previous"), "previous") == 50


def test_cum_full_splits_common_and_class_shares():
    items = _rows(("보통주", "3,980"), ("1종 종류주식", "4,042"))
    items.append({"category": "현금배당금총액(백만원)", "stock_type": "-", "current": "232,767"})
    out = dv._cum_full_from_items(items)
    assert out == {"dps_common": 3980, "dps_preferred": 4042, "total_mil": 232_767}
    assert dv._cum_full_from_items([]) is None
    same = dv._cum_full_from_items(_rows(("보통주", "1,668"), ("우선주", "1,669")))
    assert (same["dps_common"], same["dps_preferred"]) == (1668, 1669)


def test_interim_dividend_reads_the_common_dps(monkeypatch):
    class _Client:
        async def get_dividend_info(self, corp_code, year, reprt_code):
            if reprt_code != "11014":
                raise dv.DartClientError("013", "조회된 데이터가 없습니다")
            return {"list": [
                {"se": "주당 현금배당금(원)", "stock_knd": "보통주", "thstrm": "3,980"},
                {"se": "주당 현금배당금(원)", "stock_knd": "1종 종류주식", "thstrm": "4,042"},
                {"se": "현금배당금총액(백만원)", "stock_knd": "-", "thstrm": "232,767"},
            ]}

    monkeypatch.setattr(dv, "get_dart_client", lambda: _Client())
    out = asyncio.run(dv._interim_dividend_from_quarterly("00000000", 2025))
    assert out["interim_dps"] == 3980 and out["period"] == "3분기까지"


# ── 3. build_dividend_payload — 요약과 추이표가 같은 FY ──────────────────────────────────────
def _harness(monkeypatch, *, year=2025, end_month=3, annual_fn, decisions=None):
    """build_dividend_payload(history)를 DART 0콜로. annual_fn(y) → (요약 dict | {}, 경고). 결정공시는 decisions 목록(없으면 0건)."""
    from open_proxy_mcp.services.company import CompanyResolution
    from open_proxy_mcp.services.contracts import AnalysisStatus

    class _Client:
        def api_call_snapshot(self):
            return 0

    async def resolve(_q):
        return CompanyResolution(status=AnalysisStatus.EXACT, query="합성", candidates=[],
                                 selected={"corp_name": "합성", "stock_code": "", "corp_code": "00000000"})

    asked: list[int] = []

    async def annual(_corp, y):
        asked.append(y)
        return annual_fn(y)

    async def search(corp, start, end):
        filings = [{"rcept_no": d["rcept_no"], "rcept_dt": d["rcept_dt"], "report_nm": "현금ㆍ현물배당결정"} for d in (decisions or [])]
        return filings, [], [], None

    async def details(_f):
        return list(decisions or [])

    async def capital(*_a, **_k):
        return False, []

    async def quarterly(*_a, **_k):
        return []

    monkeypatch.setattr(dv, "today_kst", lambda: date(2026, 10, 23))
    monkeypatch.setattr(dv, "get_dart_client", lambda: _Client())
    monkeypatch.setattr(dv, "resolve_company_query", resolve)
    monkeypatch.setattr(dv, "_annual_summary", annual)
    monkeypatch.setattr(dv, "_search_dividend_filings", search)
    monkeypatch.setattr(dv, "_decision_details", details)
    monkeypatch.setattr(dv, "_detect_capital_reserve_reduction", capital)
    monkeypatch.setattr(dv, "_quarterly_full_from_cumulative", quarterly)
    monkeypatch.setattr(dv, "_quarterly_dps_from_cumulative", quarterly)
    p = asyncio.run(dv.build_dividend_payload("합성", scope="history", year=year, years=3))
    return p, asked


def _summary(fy, em, dps):
    last = calendar.monthrange(fy, em)[1]
    return {"period": f"{fy} 사업보고서(기말)", "stlm_dt": f"{fy}-{em:02d}-{last:02d}", "cash_dps": dps, "total_dps": dps,
            "source": "alotMatter", **dv._fiscal_period_fields(fy, em)}


def test_summary_year_comes_from_stlm_dt_not_from_the_requested_year(monkeypatch):
    """요청 bsns_year 2025 에 3월 결산 보고서(결산기준일 2026-03-31 = FY2026)가 오면 그 요약은 FY2026 자리에 선다.
    종전엔 요청 연도 2025 자리에 FY2026 요약이 꽂혀, FY2026 은 「미결의 0」·FY2025 는 FY2026 의 값으로 나갔다."""
    def annual(y):
        return (_summary(2026, 3, 60), None) if y == 2025 else ({}, None)

    p, _ = _harness(monkeypatch, annual_fn=annual)
    d = p["data"]
    assert d["undecided_fiscal_years"] == []
    assert not any("미결의" in w for w in p["warnings"])
    by_year = {r["year"]: r for r in d["history"]}
    assert by_year[2026]["annual_dps"] == 60 and by_year[2026]["period_end"] == "2026-03-31"
    assert 2026 in d["history_selection"]["available_years"]


def test_year_with_a_filed_annual_report_is_not_called_undecided(monkeypatch):
    """3월 결산, 기본 조회(작년=2025): FY2026(2026-03-31 종료) 사업보고서가 이미 나왔다 — 기신정기·포시에스(6월)의 실제 모양.
    종전엔 그 보고서를 읽지 않고 「사업보고서 배당 항목도 확인되지 않는다 — 미결의」로 냈다."""
    def annual(y):
        return (_summary(y, 3, 100 if y == 2025 else 60), None) if y <= 2026 else ({}, None)

    p, asked = _harness(monkeypatch, annual_fn=annual)
    d = p["data"]
    assert 2026 in asked
    assert d["undecided_fiscal_years"] == []
    by_year = {r["year"]: r for r in d["history"]}
    assert by_year[2026]["annual_dps"] == 60 and not by_year[2026].get("pending_confirmation")
    assert [r["year"] for r in d["history"]] == sorted(r["year"] for r in d["history"])


def test_year_without_report_or_decision_is_still_undecided(monkeypatch):
    p, _ = _harness(monkeypatch, annual_fn=lambda y: (_summary(y, 3, 100), None) if y <= 2025 else ({}, None))
    assert [f["fiscal_year"] for f in p["data"]["undecided_fiscal_years"]] == [2026]


def test_failed_annual_lookup_is_not_declared_undecided(monkeypatch):
    """사업보고서 조회가 실패(013 아님)한 해는 「모른다」다 — 미결의로 단정하지 않고 경고한다."""
    def annual(y):
        if y <= 2025:
            return _summary(y, 3, 100), None
        return {}, "alotMatter 조회 실패: 020"

    p, _ = _harness(monkeypatch, annual_fn=annual)
    assert p["data"]["undecided_fiscal_years"] == []
    assert any("2026" in w and "조회 실패" in w for w in p["warnings"])


def test_decision_in_the_march_year_end_company_marks_the_year_decided(monkeypatch):
    """3월 결산 FY2026 결산배당(기준일 2026-03-31)은 FY2026 에 귀속된다 — 종전엔 FY2025 로 가서 FY2026 이 미결의로 남았다."""
    dec = _d("결산배당", "2026-03-31", "20260512", dps=60, rcept_no="20260512000001")
    p, _ = _harness(monkeypatch, decisions=[dec],
                    annual_fn=lambda y: (_summary(y, 3, 100), None) if y <= 2025 else ({}, None))
    assert p["data"]["undecided_fiscal_years"] == []
