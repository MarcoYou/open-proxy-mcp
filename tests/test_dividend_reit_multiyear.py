# -*- coding: utf-8 -*-
"""반기·분기 결산 리츠 — `dividend_disclosure` 의 사업연도 배당은 **그 해 결산기 DPS 의 합**이다 (261021, 공개 DATA 문서 DATA-DIV1·DATA-FY3). network 0 · DB 0.

사업보고서 `alotMatter` 한 번에 결산기가 `stlm_dt` 로 갈라져 여럿 온다(롯데리츠 FY2025 = 2025-06-30 행 · 2025-12-31 행). 종전 다년 칸은

  · 한 행의 당기·전기·전전기를 「한 해 앞」으로 읽었다 — 실제로는 **연속한 결산기**다(2025-12 행 = 123 · 117 · 112 = 2025-12 · 2025-06 · 2024-12).
    → 롯데리츠 FY2025 123 (참 240 = 117 + 123) · FY2024 117 (실은 2025-06 결산기) · FY2023 112 (실은 2024-12 결산기)
  · 요약은 결산기준일을 첫 행(2025-06-30)에서, DPS·총액을 마지막 행(2025-12-31 것)에서 읽어 한 요약이 두 결산기를 섞었고,
    결산월을 6 으로 오인해 사업연도 구간이 2024-07~2025-06 으로 나왔다.

고정물 값은 DART `alotMatter` 원문 그대로다(수집 원문 줄 — 롯데리츠 FY2024·FY2025, SK리츠 FY2021·FY2025, KB스타리츠 FY2025, 신한글로벌액티브리츠 FY2025,
한국주철관 FY2020, 체시스 FY2023). 95개 (회사, 사업연도) 묶음 전부에서 새 사업연도 합이 원장(`div_declared`)의 결산기 합과 같다는 것은 별도 대조로 확인했다.
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

from open_proxy_mcp.services import dividend as D
from open_proxy_mcp.services import price_multiple_data as S
from open_proxy_mcp.services.company import CompanyResolution
from open_proxy_mcp.services.contracts import AnalysisStatus
from open_proxy_mcp.services.dividend_parser import build_dividend_summary
from open_proxy_mcp.tools import dividend_disclosure as TD


# ─────────────────────────────────────────────────────────────────────────── 고정물 ──
def _alot(face: tuple, rows: dict, *, stlm: str, rcept: str, yld=None) -> list[dict]:
    """한 결산기 행 묶음(= 사업보고서 한 통의 `alotMatter`) — rows: {항목: (당기, 전기, 전전기)}. 서식이 남기는 빈 종류 줄도 함께."""
    def row(cat, stock, v):
        return {"category": cat, "stock_type": stock, "current": v[0], "previous": v[1], "before_previous": v[2],
                "stlm_dt": stlm, "rcept_no": rcept, "is_special": False, "is_stock_dividend": False}
    out = [row("주당액면가액(원)", "-", face)]
    for cat, v in rows.items():
        out.append(row(cat, "보통주식" if cat in ("주당 현금배당금(원)", "현금배당수익률(%)") else "-", v))
        if cat in ("주당 현금배당금(원)", "현금배당수익률(%)"):
            out.append(row(cat, "-", ("-", "-", "-")))             # 우선주 자리 — 그 회사엔 없다
    return out


def _lotte25() -> list[dict]:
    d = "주당 현금배당금(원)"; t = "현금배당금총액(백만원)"; y = "현금배당수익률(%)"
    return (_alot(("500", "500", "500"), {d: ("117", "112", "103"), t: ("33,809", "32,365", "25,026"), y: ("3.00", "3.51", "3.10")},
                  stlm="2025-06-30", rcept="20250911000374")
            + _alot(("500", "500", "500"), {d: ("123", "117", "112"), t: ("35,543", "33,809", "32,365"), y: ("2.79", "3.00", "3.51")},
                    stlm="2025-12-31", rcept="20260310002810"))


def _lotte24() -> list[dict]:
    d = "주당 현금배당금(원)"; t = "현금배당금총액(백만원)"; y = "현금배당수익률(%)"
    return (_alot(("500", "500", "500"), {d: ("103", "95", "109"), t: ("25,026", "23,234", "26,528"), y: ("3.10", "2.98", "2.88")},
                  stlm="2024-06-30", rcept="20240902000317")
            + _alot(("500", "500", "500"), {d: ("112", "103", "95"), t: ("32,365", "25,026", "23,234"), y: ("3.51", "3.10", "2.98")},
                    stlm="2024-12-31", rcept="20250312000767"))


def _sk25() -> list[dict]:
    d = "주당 현금배당금(원)"; t = "현금배당금총액(백만원)"
    cols = {"2025-03-31": ("70", "66", "66"), "2025-06-30": ("66", "70", "66"), "2025-09-30": ("66", "66", "70"), "2025-12-31": ("66", "66", "66")}
    tot = {"2025-03-31": ("19,333", "18,004", "18,004"), "2025-06-30": ("19,197", "19,333", "18,004"),
           "2025-09-30": ("19,478", "19,197", "19,333"), "2025-12-31": ("19,797", "19,478", "19,197")}
    rc = {"2025-03-31": "20250611000555", "2025-06-30": "20250922000291", "2025-09-30": "20251210000536", "2025-12-31": "20260311004347"}
    out = []
    for st in cols:
        out += _alot(("500", "500", "500"), {d: cols[st], t: tot[st]}, stlm=st, rcept=rc[st])
    return out


def _sk21() -> list[dict]:
    """상장 첫 해 — 2021-09 첫 결산기. 2021-06 은 배당 0 인 결산기(액면가는 있다), 2021-03 은 칸이 통째로 비었다(회사가 없었다)."""
    d = "주당 현금배당금(원)"; t = "현금배당금총액(백만원)"
    return (_alot(("500", "500", "-"), {d: ("53", "0", "-"), t: ("8,251", "0", "-")}, stlm="2021-09-30", rcept="20211215000203")
            + _alot(("500", "500", "500"), {d: ("70", "53", "0"), t: ("10,870", "8,251", "0")}, stlm="2021-12-31", rcept="20220310001102"))


def _kb25() -> list[dict]:
    d = "주당 현금배당금(원)"; t = "현금배당금총액(백만원)"
    return (_alot(("500", "500", "500"), {d: ("180", "188", "188"), t: ("18,254", "19,066", "19,066")}, stlm="2025-01-31", rcept="20250417000094")
            + _alot(("500", "500", "500"), {d: ("170", "180", "170"), t: ("17,240", "18,254", "19,066")}, stlm="2025-07-31", rcept="20251024000148"))


def _shinhan_global25() -> list[dict]:
    d = "주당 현금배당금(원)"; t = "현금배당금총액(백만원)"
    return (_alot(("1,000", "1,000", "1,000"), {d: ("127", "126", "-"), t: ("5,522", "5,478", "-")}, stlm="2025-02-28", rcept="20250514001235")
            + _alot(("1,000", "1,000", "1,000"), {d: ("127", "127", "126"), t: ("5,522", "5,522", "5,478")}, stlm="2025-08-31", rcept="20251120000571"))


def _hankook_cast_iron20() -> list[dict]:
    """결산월을 3월 → 12월로 바꾼 해 — 2020-03-31 (12개월) · 2020-12-31 (9개월 이행기). 간격 9개월은 규칙적인 주기가 아니다."""
    d = "주당 현금배당금(원)"; t = "현금배당금총액(백만원)"
    return (_alot(("500", "500", "500"), {d: ("200", "200", "200"), t: ("4,302", "4,302", "4,302")}, stlm="2020-03-31", rcept="20200629000304")
            + _alot(("500", "500", "500"), {d: ("200", "200", "200"), t: ("4,302", "4,302", "4,302")}, stlm="2020-12-31", rcept="20210317000600"))


def _chesys23() -> list[dict]:
    """무배당 회사의 결산월 변경(06 → 12). 배당 칸은 전부 `-` 지만 액면가가 있어 「회사가 있었다」."""
    d = "주당 현금배당금(원)"
    return (_alot(("500", "500", "500"), {d: ("-", "-", "-")}, stlm="2023-06-30", rcept="20230918000095")
            + _alot(("500", "500", "500"), {d: ("-", "-", "-")}, stlm="2023-12-31", rcept="20240321001312"))


def _summary(items: list[dict]) -> dict:
    s = build_dividend_summary(items, "사업보고서(기말)")
    s["source"] = "alotMatter"
    s.update(D._fiscal_period_fields(int(s["stlm_dt"][:4]), D._fiscal_end_month(s["stlm_dt"])))
    return s


def _stlm_dps(fy_summary: dict) -> list[tuple]:
    return [(p["stlm_dt"], p["dps_krw"]) for p in fy_summary["settlement_periods"]]


# ═══ ① 요약 — 가장 늦은 결산기 한 통에서 기준일·DPS·총액을 같이 읽는다 ═══════════════════════════════════
def test_summary_reads_date_dps_and_total_from_the_latest_settlement_period():
    items = _lotte25()
    s = build_dividend_summary(items, "사업보고서(기말)")
    assert (s["stlm_dt"], s["cash_dps"], s["total_amount_mil"], s["yield_dart"]) == ("2025-12-31", 123, 35543, 2.79), \
        "종전: 기준일 2025-06-30 (첫 행) · DPS 123 (마지막 행) — 한 요약이 두 결산기였다"
    assert s["items"] is items or len(s["items"]) == len(items), "원문 행은 줄이지 않는다"
    assert D._fiscal_end_month(s["stlm_dt"]) == 12, "결산월은 가장 늦은 결산기준일로 — 종전 6 으로 오인해 사업연도가 2024-07~2025-06 으로 나왔다"


def test_single_settlement_period_summary_is_unchanged():
    items = _alot(("500", "500", "500"), {"주당 현금배당금(원)": ("1,444", "1,444", "361"), "현금배당금총액(백만원)": ("9,809,438", "9,809,438", "2,452,359")},
                  stlm="2025-12-31", rcept="20260310000100")
    s = build_dividend_summary(items, "사업보고서(기말)")
    assert (s["stlm_dt"], s["cash_dps"], s["total_amount_mil"]) == ("2025-12-31", 1444, 9809438)
    assert D.settlement_periods(items) is None and D._year_sum_summary(s, None) == (s, None)


# ═══ ② 다년 칸 — 사업연도 = 그 해 결산기 합 ═════════════════════════════════════════════════════════════
def test_lotte_reit_fy2025_is_240_and_the_columns_are_read_as_consecutive_settlement_periods():
    ms = D._alot_multiyear_summaries(_summary(_lotte25()))
    assert ms[2025]["cash_dps"] == 240, "종전 123"
    assert ms[2024]["cash_dps"] == 215, "종전 117 (실은 2025-06 결산기)"
    assert 2023 not in ms, "응답 열이 2024-06 까지만 거슬러 올라간다 — FY2023 은 결산기 하나(2024-06 은 FY2024 것)만 봐서 연 DPS 를 지어내지 않는다"
    assert _stlm_dps(ms[2025]) == [("2025-06-30", 117), ("2025-12-31", 123)]
    assert _stlm_dps(ms[2024]) == [("2024-06-30", 103), ("2024-12-31", 112)]
    assert ms[2025]["total_amount_mil"] == 33809 + 35543 and ms[2024]["total_amount_mil"] == 25026 + 32365, "총액도 결산기 값의 합(원문 열 값)"
    assert ms[2025]["yield_dart"] == 5.79 and ms[2024]["yield_dart"] == 6.61, "결산기 시가배당률의 합"
    assert (ms[2025]["fiscal_year_end_month"], ms[2025]["period_start"], ms[2025]["period_end"]) == (12, "2025-01-01", "2025-12-31"), \
        "종전 기간 2024-07~2025-06 (결산월 6 오인)"
    assert ms[2025]["dps_basis"] == "sum_of_settlement_periods" and ms[2025]["settlement_months"] == 6
    assert ms[2025]["source"] == "alotMatter_multiyear" and ms[2025]["stlm_dt"] == "2025-12-31"


def test_lotte_reit_fy2023_is_204_from_the_fy2024_report():
    """FY2024 사업보고서 응답은 2023-06·2023-12 결산기까지 거슬러 올라간다 → FY2023 = 109 + 95 (종전 112 대신 FY2022 로 95)."""
    ms = D._alot_multiyear_summaries(_summary(_lotte24()))
    assert ms[2024]["cash_dps"] == 215 and ms[2023]["cash_dps"] == 204
    assert _stlm_dps(ms[2023]) == [("2023-06-30", 109), ("2023-12-31", 95)] and ms[2023]["total_amount_mil"] == 26528 + 23234
    assert 2022 not in ms


def test_quarterly_settlement_sums_four_periods():
    ms = D._alot_multiyear_summaries(_summary(_sk25()))
    assert ms[2025]["cash_dps"] == 268 and ms[2025]["settlement_months"] == 3 and len(ms[2025]["settlement_periods"]) == 4
    assert ms[2025]["total_amount_mil"] == 19333 + 19197 + 19478 + 19797
    assert 2024 not in ms, "열이 2024-12 까지만 — FY2024 의 앞 세 분기가 열 밖이다"


def test_first_year_listing_counts_only_the_periods_that_existed():
    """SK리츠 FY2021 — 2021-06 결산기는 배당 0 (액면가는 있다 = 있던 결산기), 2021-03 은 칸이 비었다(없던 결산기) → 53 + 70."""
    ms = D._alot_multiyear_summaries(_summary(_sk21()))
    assert ms[2021]["cash_dps"] == 123
    assert _stlm_dps(ms[2021]) == [("2021-06-30", 0), ("2021-09-30", 53), ("2021-12-31", 70)]


def test_non_december_settlement_months_follow_the_latest_period():
    kb = D._alot_multiyear_summaries(_summary(_kb25()))
    assert kb[2025]["cash_dps"] == 350 and _stlm_dps(kb[2025]) == [("2025-01-31", 180), ("2025-07-31", 170)]
    assert (kb[2025]["fiscal_year_end_month"], kb[2025]["period_start"], kb[2025]["period_end"]) == (7, "2024-08-01", "2025-07-31"), \
        "결산기 2025-01·2025-07 이 덮는 12개월 = 2024-08-01 ~ 2025-07-31"
    assert kb[2024]["cash_dps"] == 376 and _stlm_dps(kb[2024]) == [("2024-01-31", 188), ("2024-07-31", 188)], \
        "2024-07 결산기는 두 보고서의 열에 다르게 적혀 있다(2025-01 행 전기 188 · 2025-07 행 전전기 170) — 가장 가까운 뒤 보고서의 열이 이긴다(원장 FY2024 = 188 + 188)"
    sg = D._alot_multiyear_summaries(_summary(_shinhan_global25()))
    assert sg[2025]["cash_dps"] == 254 and sg[2025]["period_start"] == "2024-09-01"
    assert sg[2024]["cash_dps"] == 126 and _stlm_dps(sg[2024]) == [("2024-08-31", 126)], "상장 첫 결산기 — 앞 결산기 칸이 비어(-) 하나뿐"


def test_irregular_period_gap_reads_only_each_rows_own_current_value():
    """결산월 변경(3 → 12, 간격 9개월)은 열이 몇 개월 앞인지 알 수 없다 — 행마다 자기 당기만 쓰고 가장 앞 행만 종전처럼 연 단위 전기·전전기."""
    plan = D.settlement_periods(_hankook_cast_iron20())
    assert plan["regular"] is False and plan["months"] is None
    ms = D._alot_multiyear_summaries(_summary(_hankook_cast_iron20()))
    assert ms[2020]["cash_dps"] == 400 and ms[2020]["dps_basis"] == "sum_of_settlement_periods_irregular"
    assert ms[2019]["cash_dps"] == 200 and ms[2018]["cash_dps"] == 200
    s, note = D._year_sum_summary(_summary(_hankook_cast_iron20()), ms[2020])
    assert note is None and s["cash_dps"] == 200, "고르지 않은 응답은 합을 헤드라인(요약)으로 쓰지 않는다 — 12개월이 아닐 수 있다"


def test_no_dividend_company_with_two_settlement_periods_keeps_its_zero_years():
    """체시스(무배당) FY2023 — 06 → 12 결산월 변경 이행기지만 간격 6개월은 반기 결산과 모양이 같다. 값은 0 으로 같고 무배당 사업연도가 history 에서 빠지지 않는다.
    (열 범위 밖인 FY2021 은 이 응답에서 만들지 않는다 — 결정공시 합산이 채울 자리다.)"""
    ms = D._alot_multiyear_summaries(_summary(_chesys23()))
    assert {fy: m["cash_dps"] for fy, m in ms.items()} == {2023: 0, 2022: 0}
    assert all(m["settlement_periods"] for m in ms.values()) and all(p["dps_krw"] == 0 for m in ms.values() for p in m["settlement_periods"])


def test_single_settlement_columns_stay_annual():
    """결산기가 하나인 응답(12월 결산 등)은 열을 연 단위로 읽는다 — 종전 그대로(`test_dividend_fiscal_year` 도 같은 규칙을 잠근다)."""
    items = _alot(("500", "500", "500"), {"주당 현금배당금(원)": ("300", "250", "200")}, stlm="2025-12-31", rcept="20260310000100")
    ms = D._alot_multiyear_summaries(_summary(items))
    assert {fy: m["cash_dps"] for fy, m in ms.items()} == {2025: 300, 2024: 250, 2023: 200}
    assert not any("settlement_periods" in m for m in ms.values())


def test_headline_summary_is_replaced_by_the_year_sum_only_for_regular_periods():
    s = _summary(_lotte25())
    ms = D._alot_multiyear_summaries(s)
    out, note = D._year_sum_summary(s, ms[2025])
    assert (out["cash_dps"], out["total_dps"], out["total_amount_mil"], out["yield_dart"]) == (240, 240, 69352, 5.79)
    assert out["latest_period"] == {"stlm_dt": "2025-12-31", "cash_dps": 123, "total_amount_mil": 35543}
    assert s["cash_dps"] == 123, "입력 요약은 건드리지 않는다"
    assert note.startswith("반기 결산 회사 — FY2025 배당은 결산기 2개의 합이다: 2025-06-30 117원 + 2025-12-31 123원 = 240원")


# ═══ ③ 도구 끝까지 — 요약·다년 추이·현재가 수익률·렌더 ══════════════════════════════════════════════════════
class _Client:
    def api_call_snapshot(self):
        return 0


def _decision(rcept_dt, record_date, dps, total_won, rcept_no):
    return {"rcept_dt": rcept_dt, "rcept_no": rcept_no, "record_date": record_date, "dividend_type": "결산배당", "dps_common": dps,
            "dps_preferred": 0, "total_amount": total_won, "report_name": "현금ㆍ현물배당결정", "has_special": False}


def _payload(monkeypatch, items, *, scope="summary", events=None, price=4000, decisions=()):
    async def resolve(_q):
        return CompanyResolution(status=AnalysisStatus.EXACT, query="롯데리츠",
                                 selected={"corp_name": "롯데리츠", "stock_code": "330590", "corp_code": "01363818"}, candidates=[])

    async def annual(_cc, year):
        return _summary(items), None

    async def search(*_a, **_k):
        return ([{"report_nm": "현금ㆍ현물배당결정", "rcept_no": d["rcept_no"]} for d in decisions], [], [], None)

    async def details(_f):
        return [dict(d) for d in decisions]

    async def market(_isu):
        return {"price": price, "date": "2026-10-20"}

    async def dps_events(_t, _d):
        return (events or []), {}

    async def none_pair(*_a, **_k):
        return False, []

    async def empty(*_a, **_k):
        return []
    monkeypatch.setattr(D, "get_dart_client", lambda: _Client())
    monkeypatch.setattr(D, "resolve_company_query", resolve)
    monkeypatch.setattr(D, "_annual_summary", annual)
    monkeypatch.setattr(D, "_search_dividend_filings", search)
    monkeypatch.setattr(D, "_decision_details", details)
    monkeypatch.setattr(D, "_detect_capital_reserve_reduction", none_pair)
    monkeypatch.setattr(D, "_quarterly_full_from_cumulative", empty)
    monkeypatch.setattr(D, "_quarterly_dps_from_cumulative", empty)
    monkeypatch.setattr(D, "today_kst", lambda: date(2026, 10, 21))
    monkeypatch.setattr(S, "_market_for", market)
    monkeypatch.setattr(S, "_dps_events", dps_events)
    return asyncio.run(D.build_dividend_payload("롯데리츠", scope=scope, year=2025, years=3))


def test_disclosure_summary_shows_the_year_sum_and_the_trailing_twelve_month_yield(monkeypatch):
    p = _payload(monkeypatch, _lotte25())
    s = p["data"]["summary"]
    assert (s["cash_dps"], s["total_amount_mil"], s["stlm_dt"]) == (240, 69352, "2025-12-31")
    assert [(x["stlm_dt"], x["dps_krw"]) for x in s["settlement_periods"]] == [("2025-06-30", 117), ("2025-12-31", 123)]
    assert s["latest_period"]["cash_dps"] == 123
    assert s["yield_current_pct"] == 6.0, "240 ÷ 4,000원 (종전 123 ÷ 4,000 = 3.08%)"
    assert p["data"]["fiscal_year_end_month"] == 12
    assert any(w.startswith("반기 결산 회사 — FY2025 배당은 결산기 2개의 합이다") for w in p["warnings"])
    assert not any("미결의" in w for w in p["warnings"]), "결산월을 12 로 읽어 「FY2026 미결의」 거짓 경고가 사라진다"
    md = TD._render(p, "summary")
    assert "- 연간 DPS(보통주): 240원" in md
    assert "반기 결산기 2개의 합: 2025-06-30 117원 + 2025-12-31 123원" in md and "가장 늦은 결산기 2025-12-31 분 123원" in md
    assert "현재가 기준 배당수익률: 6.0% (DPS 240원 ÷ 종가 4,000원" in md
    assert "사업연도 2025-01-01" not in md, "12월 결산 — 사업연도 구간 줄을 따로 붙이지 않는다"


def test_disclosure_history_is_204_215_240(monkeypatch):
    """롯데리츠 FY2023·FY2024·FY2025 — 종전 112·117·123. FY2023 은 응답 열 밖이라 배당결정 공시 합산(2023-06 109 + 2023-12 95)이 채운다."""
    dec = (_decision("20230810", "2023-06-30", 109, 26_528_000_000, "20230810000001"),
           _decision("20240206", "2023-12-31", 95, 23_234_000_000, "20240206000001"))
    p = _payload(monkeypatch, _lotte25(), scope="history", decisions=dec)
    hist = {r["year"]: r for r in p["data"]["history"]}
    assert (hist[2023]["annual_dps"], hist[2024]["annual_dps"], hist[2025]["annual_dps"]) == (204, 215, 240)
    assert hist[2025]["period_start"] == "2025-01-01" and hist[2025]["yield_pct"] == 5.79 and hist[2024]["yield_pct"] == 6.61
    md = TD._render(p, "history")
    assert "| 2025 | 240원 |" in md and "| 2024 | 215원 |" in md and "| 2023 | 204원 |" in md


def test_current_yield_aligns_each_settlement_period_to_its_own_events(monkeypatch):
    """결산기 사이에 낀 액면분할(2025-10-01, 1:2)은 그 앞 결산기(2025-06, 접수 2025-09-11)에만 곱한다 — 117 × 0.5 + 123 = 181.5원."""
    ev = [("20251001", 0.5, "split", "confirmed", None, None)]
    p = _payload(monkeypatch, _lotte25(), events=ev, price=4000)
    s = p["data"]["summary"]
    assert s["yield_current_dps_krw"] == 181.5 and s["yield_current_pct"] == round(181.5 / 4000 * 100, 2)
    adj = s["yield_current_dps_adjustment"]
    assert adj["dps_reported_krw"] == 240 and adj["dps_krw"] == 181.5 and adj["factor"] == round(181.5 / 240, 6)
    assert [(a["stlm_dt"], a["dps_krw"]) for a in adj["periods"]] == [("2025-06-30", 58.5)]
    md = TD._render(p, "summary")
    assert "DPS 240원 × 계수 0.75625(2025-10-01 주식 분할·병합 뒤) = 181.5원" in md


def test_raw_item_table_gets_a_settlement_date_column_only_when_periods_differ(monkeypatch):
    p = _payload(monkeypatch, _lotte25(), scope="detail")
    md = TD._render(p, "detail")
    assert "| 결산기준일 | 항목(원문) | 주식종류 | 당기 | 전기 | 전전기 |" in md
    assert "| 2025-12-31 | 주당 현금배당금(원) | 보통주식 | 123 | 117 | 112 |" in md
    assert "한 행의 당기·전기·전전기가 **연속한 결산기**" in md
    one = _alot(("500", "500", "500"), {"주당 현금배당금(원)": ("300", "250", "200")}, stlm="2025-12-31", rcept="20260310000100")
    md1 = TD._render(_payload(monkeypatch, one, scope="detail"), "detail")
    assert "| 항목(원문) | 주식종류 | 당기 | 전기 | 전전기 |" in md1 and "결산기준일" not in md1


def test_twelve_month_company_payload_is_unchanged(monkeypatch):
    one = _alot(("500", "500", "500"), {"주당 현금배당금(원)": ("300", "250", "200"), "현금배당금총액(백만원)": ("3,000", "2,500", "2,000")},
                stlm="2025-12-31", rcept="20260310000100")
    p = _payload(monkeypatch, one, scope="history")
    s = p["data"]["summary"]
    assert s["cash_dps"] == 300 and "settlement_periods" not in s and "latest_period" not in s
    assert [(r["year"], r["annual_dps"]) for r in p["data"]["history"]] == [(2023, 200), (2024, 250), (2025, 300)]
    assert not any("결산기" in w for w in p["warnings"])
    assert "결산기" not in TD._render(p, "summary")
