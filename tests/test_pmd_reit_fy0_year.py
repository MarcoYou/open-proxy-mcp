# -*- coding: utf-8 -*-
"""배수 도구 실시간 firm 의 첫 재무 요약 연도 — #142 뒤 반기·분기 결산 리츠 회귀(10-19 운영). network 0 · DB 0.

#142 가 financial_metrics 의 기본 사업연도(year=0)를 **회사 개황 결산월**로 고르자, 배수 firm 이 그 연도를 그대로 이어받았다.
개황은 반기·분기 결산 리츠를 「마지막 결산기 달」(롯데리츠 06·신한알파리츠 03·SK리츠 03)로 주는데 재무 요약 표의 결산월 칸
(`dart_fundamentals.acc_mt`)은 12(종전 읽기 — S6-B)라 결산월 보정이 돌지 않아 FY0 가 2026 으로 남았다.

  · 2026 사업보고서 배당 사항에는 결산기가 하나뿐 → 12개월 합 조건(`len(_per) > 1`)이 거짓 → 배당수익률 반토막(롯데리츠 6.42% → 3.41%)
  · 분기 칸이 아직 없는 2027 을 찾아 PER(TTM) 빈칸(리츠 15종) · PBR 기준 MRQ → FY0
  · 제이알글로벌리츠는 2026 결산기가 적자라 배당·PER 이 모두 빔

종전 시험(`test_pmd_found_defects`)의 가짜 재무 요약은 요청 연도와 상관없이 2025 를 돌려줘 이 길을 못 봤다. 여기 가짜는 financial_metrics 와
같은 규칙(연도를 주면 그 해, 안 주면 #142 의 개황 결산월 규칙)으로 답한다. 고침: 첫 호출에 12월 결산 달력 규칙의 연도(`_latest_annual_fy`)를
직접 주고, 12월 결산이 아닌 회사는 기존 결산월 보정이 옮긴다(#142 전 경로). 기대값은 10-19 06:33 운영(#135 직후, #142 전) 응답이다.
"""
from __future__ import annotations

import asyncio
from datetime import date

from open_proxy_mcp.services import dividend as D
from open_proxy_mcp.services import financial_metrics as FM
from open_proxy_mcp.services import price_multiple_data as S

TODAY = date(2026, 10, 19)


def _annual(ni: str, eq: str, ni_prev: str | None = None) -> list[dict]:
    """사업보고서 전체 재무제표 행(파싱 전 모양) — 당기·전기 지배순이익과 지배자본."""
    return [
        {"sj_div": "BS", "account_id": "ifrs-full_Assets", "thstrm_amount": "1,900,000,000,000", "currency": "KRW"},
        {"sj_div": "BS", "account_id": "ifrs-full_Liabilities", "thstrm_amount": "800,000,000,000"},
        {"sj_div": "BS", "account_id": "ifrs-full_Equity", "thstrm_amount": "1,100,000,000,000"},
        {"sj_div": "BS", "account_id": "ifrs-full_EquityAttributableToOwnersOfParent", "thstrm_amount": eq},
        {"sj_div": "IS", "account_id": "ifrs-full_ProfitLossAttributableToOwnersOfParent", "thstrm_amount": ni,
         "frmtrm_amount": ni_prev or ni},
    ]


def _interim(ni_add: str, eq: str) -> list[dict]:
    return [{"sj_div": "BS", "account_id": "ifrs-full_EquityAttributableToOwnersOfParent", "thstrm_amount": eq, "currency": "KRW"},
            {"sj_div": "IS", "account_id": "ifrs-full_ProfitLossAttributableToOwnersOfParent",
             "thstrm_amount": ni_add, "thstrm_add_amount": ni_add}]


def _alot(*groups) -> list[dict]:
    out = []
    for stlm, dps in groups:
        out.append({"category": "주당 현금배당금(원)", "stock_type": "보통주", "current": dps, "previous": "", "before_previous": "",
                    "stlm_dt": stlm, "is_special": False, "is_stock_dividend": False})
    return out


def _run(mp, *, overview_acc_mt: str, db_acc_mt: int | None, reports: dict, alot: dict, ticker: str = "330590",
         cap: int = 1_178_993_046_720, price: int = 4080):
    """overview_acc_mt = DART 회사 개황 결산월 · db_acc_mt = 재무 요약 표 결산월 칸 · reports = {(사업연도, 보고서코드): 행}
    alot = {사업연도: alotMatter 행}. 가짜 재무 요약은 financial_metrics 와 같은 연도 규칙으로 답한다."""
    seen = {"fm": [], "acnt": [], "alot": [], "sql": []}
    mp.setattr(FM, "today_kst", lambda: TODAY)

    async def resolve(q):
        return {"corp_name": "가나다리츠", "stock_code": ticker, "corp_code": "01363818"}, None

    async def fin_metrics(code, **kw):
        req = kw.get("year")
        seen["fm"].append(req)
        y = req or FM._default_recent_year(int(overview_acc_mt), code)     # #142 — 안 주면 개황 결산월로
        # 그 해 사업보고서가 없으면 분기·반기 보고서로 대체(`_fetch_year_metrics` 의 대체 경로) — 요약 연도는 그대로 y
        rc = next((c for c in ("11011", "11014", "11012", "11013") if (y, c) in reports), None)
        if rc is None:
            return {"data": {"year": y}}
        return {"data": {"summary": {"eps_krw": None, "revenue_krw": 70_879_702_538, "roe_pct": 1.7,
                                     "capital_impairment_status": "normal", "reprt_code": rc}, "year": y}}

    async def market_for(code):
        return {"price": price, "date": "20261016", "common_mktcap": cap, "list_shrs": 288_968_884, "source": "db"}

    async def acntall(client, cc, year, rc, fs=None):
        seen["acnt"].append((year, rc))
        rows = reports.get((year, rc))
        return (rows, "OFS") if rows else ([], None)

    async def shares_out(client, cc, year):
        return {"total": 288_968_884, "common": 288_968_884}

    async def annual_summary(cc, year):
        seen["alot"].append(year)
        items = alot.get(year)
        if not items:
            return {}, None
        s = D.build_dividend_summary(items, "사업보고서(기말)")
        s["source"] = "alotMatter"
        return s, None

    class Client:
        async def get_company_info(self, cc):
            return {"induty_code": "68112", "acc_mt": overview_acc_mt}

    def rows(sql, params=()):
        s = " ".join(sql.split())
        seen["sql"].append(s)
        if s.startswith("SELECT acc_mt FROM dart_fundamentals"):
            return [(db_acc_mt,)] if db_acc_mt is not None else []
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
    return asyncio.run(S.build_valuation_payload(ticker, format="json")), seen


# 롯데리츠(330590, 1~6월·7~12월 결산) — DART 원값(10-19 배치 키 직접 조회). 「반기보고서」는 6개월 결산기의 3개월 시점 보고서다.
LOTTE = {
    (2025, "11011"): _annual("19,627,460,882", "1,139,498,580,374", "16,546,765,792"),   # 사업보고서(2025.12) 2025-07~12
    (2026, "11011"): _annual("23,770,182,540", "1,120,000,000,000", "19,627,460,882"),   # 사업보고서(2026.06) 2026-01~06
    (2026, "11012"): _interim("10,570,554,501", "1,114,525,962,143"),                    # 반기보고서(2026.03)
    (2025, "11012"): _interim("9,707,636,832", "1,130,000,000,000"),                     # 반기보고서(2025.09)
}
LOTTE_ALOT = {2025: _alot(("2025-06-30", "117"), ("2025-12-31", "123")), 2026: _alot(("2026-06-30", "139"))}


def test_semiannual_reit_with_a_june_overview_keeps_fy2025_and_the_twelve_month_dps(monkeypatch):
    """10-19 운영 롯데리츠 — 개황 06 · 칸 12 · 2026 사업보고서 결산기 하나. 06:33 운영(#142 전)과 같은 값이어야 한다."""
    p, seen = _run(monkeypatch, overview_acc_mt="06", db_acc_mt=12, reports=LOTTE, alot=LOTTE_ALOT)
    d, i, m = p["data"], p["data"]["inputs"], p["data"]["multiples"]
    assert seen["fm"] == [2025], "첫 재무 요약은 12월 결산 달력 규칙의 연도를 직접 준다(기본 연도 0 이면 #142 의 개황 결산월 → 2026)"
    assert d["fiscal_year"] == 2025
    assert i["dps_krw"] == 262 and m["dividend_yield_pct"] == 6.42                                   # 회귀: 139 · 3.41%
    assert [x["stlm_dt"] for x in i["dps_basis"]["periods"]] == ["2025-12-31", "2026-06-30"]
    assert (i["ttm_quarter"], i["ttm_quarter_year"]) == ("반기 누적", 2026)                           # 회귀: 1분기 누적 2027
    assert i["net_income_ttm_krw"] == 19_627_460_882 + 10_570_554_501 - 9_707_636_832 == 20_490_378_551
    assert (m["per_fy0"], m["per_ttm"], m["pbr_basis"]) == (60.07, 57.54, "MRQ")                      # 06:33 운영 그대로
    assert (2027, "11013") not in seen["acnt"] and (2026, "11012") in seen["acnt"]


def test_march_september_reit_keeps_fy2025(monkeypatch):
    """신한알파리츠(293940, 3·9월 결산) — 개황 03 · 칸 12. 회귀: FY2026 · DPS 182 · 3.31%."""
    reports = {(2025, "11011"): _annual("11,311,947,161", "635,378,526,920"),
               (2026, "11012"): _interim("2,739,324,279", "600,821,669,866"),
               (2025, "11012"): _interim("6,566,446,264", "630,000,000,000")}
    alot = {2025: _alot(("2025-03-31", "173"), ("2025-09-30", "179")), 2026: _alot(("2026-03-31", "182"))}
    p, seen = _run(monkeypatch, overview_acc_mt="03", db_acc_mt=12, reports=reports, alot=alot, ticker="293940",
                   cap=665_170_676_500, price=5500)
    i = p["data"]["inputs"]
    assert seen["fm"] == [2025] and p["data"]["fiscal_year"] == 2025
    assert i["dps_krw"] == 361 and p["data"]["multiples"]["dividend_yield_pct"] == 6.56
    assert (i["ttm_quarter"], i["ttm_quarter_year"]) == ("반기 누적", 2026)


def test_quarterly_reit_keeps_fy2025(monkeypatch):
    """SK리츠(395400, 분기 결산) — 개황 03 · 칸 12. 수익률은 회귀 때도 같았고(결산기 넷) FY0·TTM 칸만 2026·2027 로 갔다."""
    reports = {(2025, "11011"): _annual("32,360,769,209", "1,939,593,502,956")}
    alot = {2025: _alot(("2025-03-31", "70"), ("2025-06-30", "66"), ("2025-09-30", "66"), ("2025-12-31", "66")),
            2026: _alot(("2026-03-31", "68"), ("2026-06-30", "68"))}
    p, seen = _run(monkeypatch, overview_acc_mt="03", db_acc_mt=12, reports=reports, alot=alot, ticker="395400",
                   cap=1_598_403_562_200, price=5310)
    d, i = p["data"], p["data"]["inputs"]
    assert seen["fm"] == [2025] and d["fiscal_year"] == 2025 and i["dps_krw"] == 268
    assert d["multiples"]["per_fy0"] == 49.39 and (i["ttm_quarter"], i["ttm_quarter_year"]) == ("1분기 누적", 2026)


def test_december_company_reads_one_summary_and_no_fiscal_month(monkeypatch):
    """12월 결산 — 재무 요약 한 번(2025) · 결산월 칸 읽기 없음 · 종전 그대로."""
    reports = {(2025, "11011"): _annual("100,000,000,000", "1,000,000,000,000"),
               (2026, "11012"): _interim("60,000,000,000", "1,050,000,000,000"),
               (2025, "11012"): _interim("40,000,000,000", "950,000,000,000")}
    p, seen = _run(monkeypatch, overview_acc_mt="12", db_acc_mt=None, reports=reports,
                   alot={2025: _alot(("2025-12-31", "1,668"))}, ticker="005930")
    assert seen["fm"] == [2025] and not any("acc_mt" in s for s in seen["sql"])
    assert p["data"]["fiscal_year"] == 2025 and p["data"]["inputs"]["net_income_ttm_krw"] == 120_000_000_000
    assert seen["alot"] == [2025]


def test_true_february_company_still_moves_to_its_latest_annual(monkeypatch):
    """JTC(950170, 2월 결산 외국 상장사) — 칸도 2. #142 의 본래 목표(2026.02 사업보고서 = FY2026)는 결산월 보정이 그대로 옮긴다."""
    reports = {(2025, "11011"): _annual("4,000,000,000", "160,000,000,000"),
               (2026, "11011"): _annual("4,663,950,771", "171,505,968,938")}
    p, seen = _run(monkeypatch, overview_acc_mt="02", db_acc_mt=2, reports=reports, alot={}, ticker="950170",
                   cap=200_000_000_000, price=5000)
    assert seen["fm"] == [2025, 2026], "12월 결산이 아니면 결산월 보정이 최신 사업보고서 해로 한 번 더 읽는다(#142 전과 같은 비용)"
    assert p["data"]["fiscal_year"] == 2026 and p["data"]["inputs"]["net_income_fy0_krw"] == 4_663_950_771


def test_late_filing_march_company_keeps_the_previous_annual(monkeypatch):
    """3월 결산이 2026.03 사업보고서를 아직 안 냈으면(늦게 냄) 2025 그대로 — #142 의 기본 연도(2026)를 받으면 재무 요약이 분기 보고서로
    대체되고 결산월 보정은 연도가 같아 돌지 않아 FY0 가 비었다(코드 경로)."""
    reports = {(2025, "11011"): _annual("21,164,000,000", "132,962,000,000"),
               (2026, "11013"): _interim("13,920,000,000", "194,790,000,000"),
               (2025, "11013"): _interim("5,470,000,000", "129,740,000,000")}
    p, seen = _run(monkeypatch, overview_acc_mt="03", db_acc_mt=3, reports=reports, alot={}, ticker="018500",
                   cap=60_000_000_000, price=1300)
    d = p["data"]
    assert seen["fm"] == [2025, 2026] and d["fiscal_year"] == 2025
    assert d["inputs"]["net_income_fy0_krw"] == 21_164_000_000 and d["multiples"]["per_fy0"] is not None
