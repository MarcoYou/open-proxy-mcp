# -*- coding: utf-8 -*-
"""price_multiple_data(배수 도구) 원래 결함 다섯 갈래(10-19 기능 검증에서 찾음 — 그날 아침 코드도 같았다). network 0 · DB 0.

  ① 반기·분기 결산(리츠 등) 배당수익률 — 사업보고서의 **마지막 결산기 DPS 한 칸**만 썼다(롯데리츠 3.01% · SK리츠 1.24% · 신한알파리츠 3.25%).
     → 최근 12개월 결산기 DPS 합(`dividend.period_dps`·`trailing_period_dps`). 12월 결산·주기가 고르지 않은 해는 종전 그대로.
  ② 외화 보고사 — TTM·MRQ 까지 사업연도 말 환율 하나로 환산했다(두산밥캣 PBR 0.67 vs 분기 말 환율 0.62).
     → 기간마다 그 기간 말 환율(곡선·업종 비교와 같은 규칙). 평균 환율 규칙은 저장소에 없다(`dart/fx.py` v1.1).
  ③ 매출이 비면 「금융·지주 업종」 — 재무 없는 신규상장(멜콘 업종코드 292)이 금융으로 나갔다 → 업종코드만, 없을 때 저장 업종코드 → 업종분류.
  ④ KSIC 축 집계가 20260821 에서 멈췄는데(2026-08-23 결정) 응답은 기준일만 적었다 → 시장 전체보다 낡은 축이면 밝힌다(`stale`).
  ⑤ 곡선이 비는 주(피씨엘 241820 — 2025 사업보고서 미제출)의 과거 시점이 20주 전 점으로 까닭 없이 답했다 → 앞선 칸으로 센 대체 점
     (`series_gap_fill`, 업종 비교·「주간」 항목과 같은 규칙). explain 「자본잠식 상태: None」, 한창 PER 「0.0」(0.0038 반올림)도 고친다.
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

from open_proxy_mcp.services import dividend as D
from open_proxy_mcp.services import price_multiple_data as S
from open_proxy_mcp.tools import price_multiple_data as TP

TODAY = date(2026, 10, 19)


# ═══ 공통 — 실시간 firm 경로 가짜(DART·DB 없이) ═════════════════════════════════════════════════════════
def _annual_rows(ni: str, eq: str, ccy: str = "KRW") -> list[dict]:
    return [
        {"sj_div": "BS", "account_id": "ifrs-full_Assets", "thstrm_amount": "500,000,000,000", "currency": ccy},
        {"sj_div": "BS", "account_id": "ifrs-full_Liabilities", "thstrm_amount": "300,000,000,000"},
        {"sj_div": "BS", "account_id": "ifrs-full_Equity", "thstrm_amount": "200,000,000,000"},
        {"sj_div": "BS", "account_id": "ifrs-full_EquityAttributableToOwnersOfParent", "thstrm_amount": eq},
        {"sj_div": "IS", "account_id": "ifrs-full_ProfitLossAttributableToOwnersOfParent", "thstrm_amount": ni, "frmtrm_amount": ni},
    ]


def _cum_rows(ni_add: str, eq: str, ccy: str = "KRW") -> list[dict]:
    return [{"sj_div": "BS", "account_id": "ifrs-full_EquityAttributableToOwnersOfParent", "thstrm_amount": eq, "currency": ccy},
            {"sj_div": "IS", "account_id": "ifrs-full_ProfitLossAttributableToOwnersOfParent",
             "thstrm_amount": "1", "thstrm_add_amount": ni_add}]


KRW_REPORTS = {
    (2025, "11011"): _annual_rows("100,000,000,000", "1,000,000,000,000"),
    (2026, "11012"): _cum_rows("60,000,000,000", "1,050,000,000,000"),
    (2025, "11012"): _cum_rows("40,000,000,000", "950,000,000,000"),
}


def _alot(*groups) -> list[dict]:
    """alotMatter 행(파싱 뒤 모양) — groups = [(결산기준일, 보통주 DPS 문자열), …]. 결산기마다 서식이 남긴 빈 종류 줄도 함께."""
    out = []
    for stlm, dps in groups:
        out.append({"category": "주당 현금배당금(원)", "stock_type": "보통주", "current": dps, "previous": "", "before_previous": "",
                    "stlm_dt": stlm, "is_special": False, "is_stock_dividend": False})
        out.append({"category": "주당 현금배당금(원)", "stock_type": "-", "current": "-", "previous": "", "before_previous": "",
                    "stlm_dt": stlm, "is_special": False, "is_stock_dividend": False})
    return out


def _run_live(mp, *, reports=KRW_REPORTS, alot=None, induty="68112", revenue=600_000_000_000, fx=None, price=4080,
              cap=1_000_000_000_000, db_rows=None, cap_status="normal", alot_err=None):
    """alot = {사업연도: alotMatter 행} (없으면 그 해 배당 없음). fx = {날짜: 환율} — 주면 그 날짜로만 답한다.
    alot_err = {사업연도: 오류 문자열} — 그 해 배당 사항 조회가 실패한 것으로."""
    seen = {"alot": [], "fx": [], "sql": []}

    async def resolve(q):
        return {"corp_name": "가나다리츠", "stock_code": "330590", "corp_code": "01363818"}, None

    async def fin_metrics(code, **kw):
        return {"data": {"summary": {"eps_krw": None, "revenue_krw": revenue, "roe_pct": 5.0,
                                     "capital_impairment_status": cap_status, "reprt_code": "11011"}, "year": 2025}}

    async def market_for(code):
        return {"price": price, "date": "20261016", "common_mktcap": cap, "list_shrs": 245_000_000, "source": "db"}

    async def acntall(client, cc, year, rc, fs=None):
        rows = reports.get((year, rc))
        return (rows, "CFS") if rows else ([], None)

    async def shares_out(client, cc, year):
        return {"total": 245_000_000, "common": 245_000_000}

    async def annual_summary(cc, year):
        seen["alot"].append(year)
        if (alot_err or {}).get(year):
            return {}, alot_err[year]
        items = (alot or {}).get(year)
        if not items:
            return {}, None
        s = D.build_dividend_summary(items, "사업보고서(기말)")
        s["source"] = "alotMatter"
        return s, None

    async def fx_to_krw(ccy, dd):
        seen["fx"].append(dd)
        return (fx or {}).get(dd)

    class Client:
        async def get_company_info(self, cc):
            return {"induty_code": induty, "acc_mt": "12"}

    def rows(sql, params=()):
        s = " ".join(sql.split())
        seen["sql"].append(s)
        if db_rows is not None:
            r = db_rows(s, params)
            if r is not None:
                return r
        return []
    mp.setattr(S, "_pg_rows", rows)
    mp.setattr(S, "today_kst", lambda: TODAY)
    mp.setattr(S, "_resolve_listed", resolve)
    mp.setattr(S, "get_dart_client", lambda: Client())
    mp.setattr(S, "build_financial_metrics_payload", fin_metrics)
    mp.setattr(S, "_market_for", market_for)
    mp.setattr(S, "_acntall", acntall)
    mp.setattr(S, "_shares_outstanding", shares_out)
    mp.setattr(S, "_annual_summary", annual_summary)
    mp.setattr(S, "fx_to_krw", fx_to_krw)
    return asyncio.run(S.build_valuation_payload("330590", format="json")), seen


# ═══ ① 반기·분기 결산 배당 ═════════════════════════════════════════════════════════════════════════════
def test_period_dps_splits_one_report_into_its_settlement_periods():
    items = _alot(("2025-06-30", "117"), ("2025-12-31", "123"))
    items.append({"category": "주당 현금배당금(원)", "stock_type": "우선주", "current": "500", "stlm_dt": "2025-12-31", "is_special": False})
    items.append({"category": "특별 주당 현금배당금(원)", "stock_type": "보통주", "current": "999", "stlm_dt": "2025-12-31", "is_special": True})
    assert D.period_dps(items) == {"20250630": 117, "20251231": 123}, "보통주만 · 특별배당 줄은 빼고(요약의 cash_dps 와 같은 정의)"
    assert D.period_dps(_alot(("2026-06-30", "-"))) == {"20260630": 0}, "그 결산기 무배당은 0 — 빈 결산기를 버리지 않는다"
    assert D.period_dps(None) == {}


@pytest.mark.parametrize("periods,dps,months", [
    ({"20250630": 117, "20251231": 123, "20260630": 139}, 262, 6),                              # 롯데리츠(반기) — 최근 두 결산기
    ({"20250331": 70, "20250630": 66, "20250930": 66, "20251231": 66, "20260331": 68, "20260630": 68}, 268, 3),   # SK리츠(분기)
    ({"20250331": 173, "20250930": 179, "20260331": 182}, 361, 6),                              # 신한알파리츠(3·9월)
])
def test_trailing_period_dps_sums_the_last_twelve_months(periods, dps, months):
    t = D.trailing_period_dps(periods)
    assert t["dps"] == dps and t["months"] == months and len(t["periods"]) == 12 // months


@pytest.mark.parametrize("periods", [
    {"20251231": 1668},                                   # 12월 결산 — 결산기 하나
    {"20250630": 300, "20251231": 150},                   # 결산기 둘뿐 — 2025-06 이 6개월짜리인지(반기) 12개월짜리인지(결산월 변경) 모른다
    {"20240630": 280, "20250630": 300, "20251231": 150},  # 6월 결산 → 12월 결산 이행기(12개월 + 6개월) — 한 해 반치를 연 배당으로 만들지 않는다
    {"20250630": 100, "20251231": 100, "20260331": 50},   # 주기가 고르지 않다
    {"20260331": 68, "20260630": 68},                     # 분기 결산인데 12개월을 못 채운다
])
def test_trailing_period_dps_is_none_when_the_cadence_is_not_regular(periods):
    assert D.trailing_period_dps(periods) is None


def test_semiannual_reit_dividend_yield_uses_the_trailing_twelve_months(monkeypatch):
    p, seen = _run_live(monkeypatch, alot={2025: _alot(("2025-06-30", "117"), ("2025-12-31", "123")),
                                           2026: _alot(("2026-06-30", "139"))})
    d = p["data"]
    assert seen["alot"] == [2025, 2026], "결산기가 둘 이상 보이면 다음 사업연도 사업보고서도 읽는다"
    assert d["inputs"]["dps_krw"] == 262 and d["multiples"]["dividend_yield_pct"] == round(262 / 4080 * 100, 2)   # 종전 123 → 3.01%
    assert d["inputs"]["dps_basis"] == {"rule": "trailing_12m_periods", "period_months": 6,
                                        "periods": [{"stlm_dt": "2025-12-31", "dps_krw": 123}, {"stlm_dt": "2026-06-30", "dps_krw": 139}]}
    assert any(w.startswith("반기 결산 회사 — DPS 는 최근 12개월 결산기 2개") and "262원" in w for w in d["warnings"])
    md = TP._render_explain_firm(p)
    assert "262원 (최근 12개월 결산기 2개 합 — 2025-12 123원 + 2026-06 139원)" in md


def test_quarterly_reit_sums_four_quarters(monkeypatch):
    p, _ = _run_live(monkeypatch, alot={2025: _alot(("2025-03-31", "70"), ("2025-06-30", "66"), ("2025-09-30", "66"), ("2025-12-31", "66")),
                                        2026: _alot(("2026-03-31", "68"), ("2026-06-30", "68"))})
    assert p["data"]["inputs"]["dps_krw"] == 268 and p["data"]["inputs"]["dps_basis"]["period_months"] == 3


def test_december_company_reads_one_report_and_keeps_its_dps(monkeypatch):
    p, seen = _run_live(monkeypatch, alot={2025: _alot(("2025-12-31", "1,668"))}, induty="26110")
    i = p["data"]["inputs"]
    assert seen["alot"] == [2025] and i["dps_krw"] == 1668 and "dps_basis" not in i
    assert not any("결산 회사 — DPS" in w for w in p["data"]["warnings"])


def test_without_newer_periods_the_previous_year_confirms_the_cadence(monkeypatch):
    """5월처럼 다음 사업연도 결산기가 아직 없으면 결산기 둘뿐이라 주기를 모른다 — 앞 사업연도를 읽어 확인한다."""
    p, seen = _run_live(monkeypatch, alot={2024: _alot(("2024-06-30", "103"), ("2024-12-31", "112")),
                                           2025: _alot(("2025-06-30", "117"), ("2025-12-31", "123"))})
    assert seen["alot"] == [2025, 2026, 2024] and p["data"]["inputs"]["dps_krw"] == 240


def test_next_year_failure_is_said_and_the_read_periods_still_count(monkeypatch):
    """다음 사업연도 배당 사항 조회가 실패하면 가장 늦은 결산기가 빠졌을 수 있다 — 읽은 결산기까지의 합을 쓰고 그렇다고 적는다."""
    p, seen = _run_live(monkeypatch, alot={2024: _alot(("2024-06-30", "103"), ("2024-12-31", "112")),
                                           2025: _alot(("2025-06-30", "117"), ("2025-12-31", "123"))},
                        alot_err={2026: "alotMatter 조회 실패: 020"})
    assert seen["alot"] == [2025, 2026, 2024] and p["data"]["inputs"]["dps_krw"] == 240
    assert any(w.startswith("다음 사업연도 배당 사항 조회 실패") for w in p["data"]["warnings"])


def test_transport_error_in_an_extra_dividend_read_keeps_the_multiples(monkeypatch):
    """보조 읽기(다음·앞 사업연도)의 전송 오류가 PER·PBR 응답을 통째로 막지 않는다 — 종전 DPS(마지막 결산기)로 내려가고 그렇다고 적는다."""
    async def boom_after_first(cc, year):
        if year != 2025:
            raise RuntimeError("ReadTimeout")
        s = D.build_dividend_summary(_alot(("2025-06-30", "117"), ("2025-12-31", "123")), "사업보고서(기말)")
        s["source"] = "alotMatter"
        return s, None
    p, _ = _run_live(monkeypatch)
    monkeypatch.setattr(S, "_annual_summary", boom_after_first)
    p = asyncio.run(S.build_valuation_payload("330590", format="json"))
    d = p["data"]
    assert p["status"] == "ok" and d["multiples"]["per_fy0"] is not None
    assert d["inputs"]["dps_krw"] == 123 and "dps_basis" not in d["inputs"]
    assert any(w.startswith("다음 사업연도 배당 사항 조회 실패 — 사업보고서에 결산기가 여럿인데") for w in d["warnings"])


def test_fiscal_month_change_keeps_the_old_value(monkeypatch):
    """6월 결산(12개월) → 12월 결산 이행기(7~12월 6개월) — 같은 사업연도에 결산기가 둘이지만 반기 결산이 아니다."""
    p, seen = _run_live(monkeypatch, alot={2024: _alot(("2024-06-30", "280")),
                                           2025: _alot(("2025-06-30", "300"), ("2025-12-31", "150"))})
    assert seen["alot"] == [2025, 2026, 2024]
    assert p["data"]["inputs"]["dps_krw"] == 150 and "dps_basis" not in p["data"]["inputs"]


# ═══ ② 외화 보고사 — 기간 말 환율 ══════════════════════════════════════════════════════════════════════
USD_REPORTS = {
    (2025, "11011"): _annual_rows("283,000,000", "5,000,000,000", "USD"),
    (2026, "11012"): _cum_rows("180,000,000", "5,100,000,000", "USD"),
    (2025, "11012"): _cum_rows("120,000,000", "4,800,000,000", "USD"),
}
FX = {"20251231": 1434.9, "20260630": 1541.5, "20250630": 1356.4}


def test_foreign_reporter_converts_each_period_at_its_own_end_rate(monkeypatch):
    p, seen = _run_live(monkeypatch, reports=USD_REPORTS, fx=FX, induty="29241", cap=4_879_073_708_500)
    i, m = p["data"]["inputs"], p["data"]["multiples"]
    assert sorted(seen["fx"]) == ["20250630", "20251231", "20260630"]
    ni_ttm = round(283e6 * 1434.9) + round(180e6 * 1541.5) - round(120e6 * 1356.4)
    eq_mrq = round(5_100e6 * 1541.5)
    assert i["net_income_ttm_krw"] == ni_ttm and i["controlling_equity_krw"] == eq_mrq       # 종전엔 셋 다 1,434.9
    assert m["pbr_mrq"] == round(4_879_073_708_500 / eq_mrq, 2)
    assert i["net_income_fy0_krw"] == round(283e6 * 1434.9) and i["fx_rate_to_krw"] == 1434.9   # FY0 는 사업연도 말 그대로
    assert i["fx_rates_to_krw"] == {"fy0": {"date": "20251231", "rate": 1434.9},
                                    "quarter": {"date": "20260630", "rate": 1541.5},
                                    "prior_quarter": {"date": "20250630", "rate": 1356.4}}
    w = [x for x in p["data"]["warnings"] if x.startswith("기능통화 USD")]
    assert len(w) == 1 and "2026-06-30 1,541.5원" in w[0] and "2025-06-30 1,356.4원" in w[0]
    md = TP._render_explain_firm(p)
    assert "최근 분기 누적·자본 2026-06-30 1,541.5원" in md


def _eps(rows: list[dict], annual: str | None = None, cum: str | None = None) -> list[dict]:
    r = {"sj_div": "IS", "account_id": "ifrs-full_BasicEarningsLossPerShare", "thstrm_amount": annual or "0.1"}
    if cum is not None:
        r["thstrm_add_amount"] = cum
    return rows + [r]


def test_foreign_reporter_assembles_ttm_eps_at_each_period_end_rate(monkeypatch):
    """EPS(TTM) 도 조각마다 그 기간 말 환율(지배순이익 TTM 과 같은 규칙) — 종전엔 USD 로 조립한 뒤 사업연도 말 환율 하나를 곱했다."""
    reports = {(2025, "11011"): _eps(USD_REPORTS[(2025, "11011")], annual="2.95"),
               (2026, "11012"): _eps(USD_REPORTS[(2026, "11012")], cum="1.80"),
               (2025, "11012"): _eps(USD_REPORTS[(2025, "11012")], cum="1.20")}
    p, _ = _run_live(monkeypatch, reports=reports, fx=FX, induty="29241")
    i = p["data"]["inputs"]
    assert i["eps_ttm_basis"] == "disclosed_assembled"
    assert i["eps_ttm_krw"] == round(2.95 * 1434.9 + 1.80 * 1541.5 - 1.20 * 1356.4)
    assert i["eps_fy0_krw"] == round(2.95 * 1434.9)


def test_fiscal_year_rate_failure_converts_nothing_rather_than_mixing_currencies(monkeypatch):
    """사업연도 말 환율만 못 받으면 분기 값도 환산하지 않는다 — 분기만 원화로 바꾸면 TTM 에 외화 연간과 원화 분기가 섞인다."""
    p, _ = _run_live(monkeypatch, reports=USD_REPORTS, fx={"20260630": 1541.5, "20250630": 1356.4}, induty="29241")
    i = p["data"]["inputs"]
    assert i["net_income_ttm_krw"] == 283e6 + 180e6 - 120e6 and i["controlling_equity_krw"] == 5_100e6
    assert i["fx_rate_to_krw"] is None and "fx_rates_to_krw" not in i
    assert any("환율 조회 실패 — 배수 통화 불일치 가능" in w for w in p["data"]["warnings"])


def test_quarter_rate_failure_falls_back_to_the_fiscal_year_rate_and_says_so(monkeypatch):
    p, _ = _run_live(monkeypatch, reports=USD_REPORTS, fx={"20251231": 1434.9}, induty="29241")
    i = p["data"]["inputs"]
    assert i["controlling_equity_krw"] == round(5_100e6 * 1434.9)
    assert any("분기 말 환율 조회 실패" in w for w in p["data"]["warnings"])
    # 대신 쓴 환율은 그 날짜로 적는다 — 분기 말 날짜에 사업연도 말 환율을 붙여 「6월 말 환율 1,434.9」처럼 읽히지 않게
    assert i["fx_rates_to_krw"]["quarter"] == {"date": "20251231", "rate": 1434.9}
    assert any("최근 분기 누적·자본(MRQ)은 2025-12-31 1,434.9원" in w for w in p["data"]["warnings"])


def test_krw_reporter_is_unchanged(monkeypatch):
    p, seen = _run_live(monkeypatch, induty="26110")
    i = p["data"]["inputs"]
    assert seen["fx"] == [] and i["fx_rate_to_krw"] is None and "fx_rates_to_krw" not in i
    assert i["net_income_ttm_krw"] == 100e9 + 60e9 - 40e9


# ═══ ③ 금융·지주 판정 ═════════════════════════════════════════════════════════════════════════════════
FIN_WARN = "금융·지주 업종 —"


@pytest.mark.parametrize("induty,expect", [("292", "general"), ("29299", "general"), ("64992", "financial"),
                                           ("64121", "financial"), ("65110", "financial"), ("66199", "financial")])
def test_financial_class_follows_the_dart_industry_code_not_missing_revenue(monkeypatch, induty, expect):
    p, _ = _run_live(monkeypatch, induty=induty, revenue=None)          # 재무 없는 신규상장(멜콘 292) — 매출이 비어 있다
    d = p["data"]
    assert d["sector_class"] == expect
    assert any(w.startswith(FIN_WARN) for w in d["warnings"]) == (expect == "financial")


@pytest.mark.parametrize("stored,cls,expect", [
    ("64992", None, "financial"), ("292", "금융", "general"), (None, "금융", "financial"), (None, "IT", "general"), (None, None, "general"),
])
def test_without_a_live_industry_code_the_stored_code_then_the_class_decides(monkeypatch, stored, cls, expect):
    def db(s, params):
        if s.startswith("SELECT (SELECT induty FROM dart_fundamentals"):
            return [(stored, cls)]
        return None
    p, _ = _run_live(monkeypatch, induty="", revenue=None, db_rows=db)
    assert p["data"]["sector_class"] == expect


# ═══ ④ KSIC 축 낡음 ════════════════════════════════════════════════════════════════════════════════
def _sector_rows(axis_latest, market_latest):
    def rows(sql, params=()):
        s = " ".join(sql.split())
        if s.startswith("SELECT MAX(snap_dd) FROM opm_val_market WHERE scheme=%s AND sector=%s"):
            return [(market_latest,)]
        if s.startswith("SELECT snap_dd, market, sector, label, n, cap, per_ttm"):
            return [(axis_latest, "KS", "649", "기타금융·지주(649)", 67, 529e12, 9.89, 1.11, 9.0, 1.0, 1e12, 1e12)]
        return []
    return rows


def _sector(mp, scheme, axis_latest, market_latest, as_of=None, company=""):
    async def no_fwd(*a, **k):
        return {}, {}, None

    async def no_div(*a, **k):
        return {}, {}
    mp.setattr(S, "_pg_rows", _sector_rows(axis_latest, market_latest))
    mp.setattr(S, "_fwd_val_map", no_fwd)
    mp.setattr(S, "_div_yield_map", no_div)
    return asyncio.run(S.build_sector_val_payload(company=company, scheme=scheme, format="json", as_of=as_of))


def test_frozen_ksic_axis_says_so(monkeypatch):
    p = _sector(monkeypatch, "ksic", "20260821", "20261016")
    assert p["data"]["stale"] == {"axis_as_of": "20260821", "market_as_of": "20261016", "reason": "ksic_aggregation_frozen"}
    assert p["warnings"][0].startswith("주간 스냅샷 기준(이 축의 마지막 집계 20260821)")
    assert any("KSIC 축 집계는 20260821 에서 멈춰 있다" in w and "2026-08-23" in w for w in p["warnings"])
    md = TP._render_sector(p)
    assert md.splitlines()[2].startswith("> ⚠ 이 축의 마지막 집계는 20260821")


def test_fresh_axis_is_unchanged(monkeypatch):
    p = _sector(monkeypatch, "중분류", "20261016", "20261016")
    assert "stale" not in p["data"] and p["warnings"][0].startswith("주간 스냅샷 기준(최신 20261016)")


def test_past_as_of_flags_only_the_ksic_axis(monkeypatch):
    """업종분류 축은 2026-08-21 전까지 월말 한 점씩이라(시장 전체는 2026-07 부터 주간) 과거 기준일에서 시장보다 이른 것이 설계대로다."""
    assert "stale" not in _sector(monkeypatch, "중분류", "20260626", "20260710", as_of="20260715")["data"]
    assert _sector(monkeypatch, "ksic", "20260821", "20260911", as_of="20260915")["data"]["stale"]["market_as_of"] == "20260911"
    assert "stale" not in _sector(monkeypatch, "ksic", "20260515", "20260515", as_of="20260515")["data"]


def test_other_axis_left_behind_is_flagged_without_the_ksic_text(monkeypatch):
    p = _sector(monkeypatch, "대분류", "20261008", "20261016")
    assert p["data"]["stale"]["reason"] == "axis_not_updated" and not any("KSIC 축 집계는" in w for w in p["warnings"])


# ═══ ⑤ 곡선 공백·None·0.0 ══════════════════════════════════════════════════════════════════════════
# 피씨엘(241820) 모양 — 2024 사업보고서까지, 2025 1~3분기까지. 2025 사업보고서·2026 분기 칸은 비었다(미제출).
PCL_FIN = {2023: (-20.3e9, 35.5e9), 2024: (-52.3e9, 7.1e9)}
PCL_FINQ = {(2024, 1): (-5.0e9, 30.0e9), (2024, 2): (-12.0e9, 22.0e9), (2024, 3): (-20.0e9, 15.0e9),
            (2025, 1): (-0.15e9, 6.97e9), (2025, 2): (-4.6e9, 2.51e9), (2025, 3): (-4.4e9, 2.7156e9)}
CAP = 16_803_871_920


def test_gap_point_uses_the_previous_cell_while_the_curve_stays_point_in_time():
    assert S._pit_point("20260814", CAP, PCL_FIN, PCL_FINQ) is None, "곡선은 그 시점 칸만 쓴다(종전 규칙 그대로)"
    g = S._gap_point("20260814", CAP, PCL_FIN, PCL_FINQ)
    assert g["pit_q"] == "2026Q2" and g["pit_fy"] == 2025 and g["pbr_mrq"] == round(CAP / 2.7156e9, 3)
    assert g["per_ttm"] is None and g["per_fy0"] is None and g["basis"] == {"pit_q": "2026Q2", "per_ttm": "2025Q3", "pbr_mrq": "2025Q3"}
    assert S._gap_point("20271015", CAP, PCL_FIN, PCL_FINQ) is None, "앞선 칸은 1년 안까지만"


def _weekly(start: str, n: int):
    from datetime import timedelta
    d0 = date(int(start[:4]), int(start[4:6]), int(start[6:]))
    return [((d0 + timedelta(days=7 * i)).strftime("%Y%m%d"), float(CAP)) for i in range(n)]


def _patch_history(mp):
    async def resolve(q):
        return {"corp_name": "피씨엘", "stock_code": "241820", "corp_code": "01051092"}, None

    async def bounds():
        return "20261016", "20231016"

    async def fin_y(isu, cur="KRW", fye=12):
        return dict(PCL_FIN)

    async def fin_q(isu, cur="KRW", fye=12):
        return dict(PCL_FINQ)

    weeks = _weekly("20251003", 55)                     # 20251003 ~ 20261016

    def rows(sql, params=()):
        s = " ".join(sql.split())
        if "FROM dart_fundamentals" in s:
            return [("KQ", "213", "KRW", 12, "ok")]
        if s.startswith("SELECT price_dd, mktcap, 'w' FROM krx_weekly"):
            return [(d, c, "w") for d, c in weeks]
        return []
    mp.setattr(S, "_resolve_listed", resolve)
    mp.setattr(S, "_weekly_bounds", bounds)
    mp.setattr(S, "_firm_fin_by_fy", fin_y)
    mp.setattr(S, "_firm_fin_by_q", fin_q)
    mp.setattr(S, "_pg_rows", rows)


def test_firm_history_keeps_the_curve_and_adds_the_gap_fill(monkeypatch):
    _patch_history(monkeypatch)
    p = asyncio.run(S.build_firm_history_payload("241820", format="json"))
    d = p["data"]
    assert d["series"][-1]["asof"] == "20260327", "곡선은 종전처럼 그 시점 칸이 있는 마지막 주에서 끝난다"
    gap = d["series_gap_fill"]
    assert gap[0]["asof"] == "20260403" and gap[-1]["asof"] == "20261016" and all(g["basis"] for g in gap)
    assert d["price_rows_end"] == "20261016"
    assert any(w.startswith("주간 곡선에 점이 없는 주 29개(20260403~20261016)") for w in p["warnings"])


def test_firm_at_answers_the_requested_week_from_the_gap_fill(monkeypatch):
    _patch_history(monkeypatch)
    p = asyncio.run(S.build_firm_at_payload("241820", "20260814"))
    d = p["data"]
    assert d["as_of"] == "20260814" and d["pbr_mrq"] == round(CAP / 2.7156e9, 3)        # 종전 20260327
    assert d["basis"] == {"pit_q": "2026Q2", "per_ttm": "2025Q3", "pbr_mrq": "2025Q3"}
    assert any(w.startswith("기준 분기 늦음 — 그 시점 칸(2026Q2)이 비어") for w in p["warnings"])
    assert not any("일 이른" in w for w in p["warnings"])
    # 미래 날짜도 그 종목 마지막 시세 주로 답한다(종전엔 곡선 끝 20260327)
    assert asyncio.run(S.build_firm_at_payload("241820", "20261231"))["data"]["as_of"] == "20261016"


def test_firm_at_says_when_it_answers_with_a_much_earlier_point(monkeypatch):
    async def hist(company, format="md"):
        return {"tool": "price_multiple_data", "status": "ok", "subject": "가나다",
                "data": {"scope": "firm_history", "ticker": "000001", "market": "KS", "sector": "C26", "price_rows_end": "20261016",
                         "series": [{"asof": "20260327", "pit_fy": 2024, "pit_q": "2025Q3", "cap_krw": 1, "per_fy0": 1.0, "pbr": 1.0,
                                     "per_ttm": 1.0, "pbr_mrq": 1.0}]}}
    monkeypatch.setattr(S, "build_firm_history_payload", hist)
    p = asyncio.run(S.build_firm_at_payload("가나다", "20260814"))
    assert p["data"]["as_of"] == "20260327" and any("140일 이른 20260327 점으로 답했다" in w for w in p["warnings"])
    q = asyncio.run(S.build_firm_at_payload("가나다", "20260331"))
    assert not any("일 이른" in w for w in q["warnings"])


def test_explain_never_prints_python_none_for_the_impairment_status(monkeypatch):
    p, _ = _run_live(monkeypatch, cap_status=None, reports={}, induty="292", revenue=None)
    md = TP._render_explain_firm(p)
    assert "자본잠식 상태: 판정 불가(재무 없음)" in md and "None" not in md
    p3, _ = _run_live(monkeypatch, cap_status=None, induty="26110")      # 재무는 있는데 자본금 칸이 비어 판정이 없는 회사
    assert "- 자본잠식 상태: 판정 불가(자본금 또는 자본 값을 재무에서 찾지 못함)" in TP._render_explain_firm(p3)
    p2, _ = _run_live(monkeypatch, cap_status="normal", induty="26110")
    assert "- 자본잠식 상태: 없음\n" in TP._render_explain_firm(p2) + "\n"


def test_tiny_multiple_in_the_sector_company_row_is_not_rounded_to_zero(monkeypatch):
    """업종 비교 기업 행도 firm·곡선과 같은 규칙 — 같은 종목이 firm 에선 0.0038, 업종 비교에선 0.0 으로 갈리지 않게."""
    async def resolve(q):
        return {"corp_name": "한창", "stock_code": "005110", "corp_code": "00105235"}, None

    async def firm_val_at(isu, as_of):
        return ("649", "KS", 0.0038, 0.0199, {})
    monkeypatch.setattr(S, "_resolve_listed", resolve)
    monkeypatch.setattr(S, "_firm_val_at", firm_val_at)
    p = _sector(monkeypatch, "ksic", "20260821", "20260821", company="005110")
    c = p["data"]["company"]
    assert c["firm_per_ttm"] == 0.0038 and c["firm_pbr_mrq"] == 0.02


def test_tiny_multiple_is_not_rounded_to_zero(monkeypatch):
    """한창(005110) 정리매매가 11원 — 시총 2.3억 ÷ 지배순이익 611억 = 0.0038. 종전엔 「0.0」."""
    p, _ = _run_live(monkeypatch, reports={(2025, "11011"): _annual_rows("61,110,643,142", "11,640,097,797")},
                     cap=232_170_477, price=11, induty="26110")
    m = p["data"]["multiples"]
    assert m["per_fy0"] == 0.0038 and m["pbr_mrq"] == 0.02
    assert "- PER 0.0038(FY0)" in S._render_md(p)
    assert S._round_mult(12.345, 2) == 12.35 and S._round_mult(0.0, 2) == 0.0 and S._r3(0.0004) == 0.0004
    assert TP._f(0.0038) == "0.0038" and TP._f(1.5) == "1.50" and TP._f(None) == "N/M"
