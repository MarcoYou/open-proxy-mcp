# -*- coding: utf-8 -*-
"""반기·분기 결산 회사(리츠)의 결산기 칸 — 곡선·과거 시점·업종 비교·집계가 firm 과 같은 12개월 기준. network 0 · DB 0. (261020 p1e)

값은 DART 원값(10-20 배치 키 직접 조회 — list.json·fnlttSinglAcntAll·fnlttXbrl): 롯데리츠(330590, 1~6·7~12월, 별도) · SK리츠(395400, 분기, 연결)
· 신한알파리츠(293940, 4~9·10~3월, 연결 — 2025.09 사업보고서는 순이익을 지배·비지배로 나누지 않고 총포괄만 나눴다).
종전 연간 표(dart_finstat_y)는 해마다 마지막 한 결산기(롯데 FY2025 = 2025.07~12 196.3억), 분기 표 반기 칸은 그해 마지막 3개월 시점 보고서
(롯데 2025 반기 칸 = 2025.07~09 — 11/13 접수인데 곡선은 8/14 부터 썼다)라 곡선 끝점이 firm(#149 — 12개월 361.7억)과 갈렸다.
"""
from __future__ import annotations

import asyncio

import pytest

from open_proxy_mcp.services import period_cells as pc
from open_proxy_mcp.services import price_multiple_data as S
from open_proxy_mcp.services import report_calendar as rc


def _rep(rcept: str, terms: tuple, ni: tuple, eq: tuple, *, owners_ci: tuple | None = None, nci: tuple | None = None,
         id_ni: bool = False) -> list[dict]:
    """사업보고서 전체 재무제표 행 — 당기·전기·전전기(「제 N 기」). 기본은 지배 구분 없는 별도(총순이익 = 지배)."""
    nm = {f: f"제 {t} 기" for f, t in zip(("thstrm_nm", "frmtrm_nm", "bfefrmtrm_nm"), terms)}

    def row(sj, aid, vals):
        r = {"rcept_no": rcept, "sj_div": sj, "account_id": aid, **nm}
        r.update(zip(("thstrm_amount", "frmtrm_amount", "bfefrmtrm_amount"), vals))
        return r
    rows = [row("BS", "ifrs-full_Equity", eq), row("CIS", "ifrs-full_ProfitLoss", ni), row("CIS", "ifrs-full_ComprehensiveIncome", ni)]
    if id_ni:
        rows.append(row("CIS", "ifrs-full_ProfitLossAttributableToOwnersOfParent", ni))
    if owners_ci:
        rows.append(row("CIS", "ifrs-full_ComprehensiveIncomeAttributableToOwnersOfParent", owners_ci))
    if nci:
        rows.append(row("CIS", "ifrs-full_ProfitLossAttributableToNoncontrollingInterests", nci))
    return rows


def _interim(rcept: str, term: int, eq: str) -> list[dict]:
    return [{"rcept_no": rcept, "sj_div": "BS", "account_id": "ifrs-full_Equity", "thstrm_nm": f"제 {term} 기 반기말", "thstrm_amount": eq},
            {"rcept_no": rcept, "sj_div": "CIS", "account_id": "ifrs-full_ProfitLoss", "thstrm_nm": f"제 {term} 기 반기", "thstrm_amount": "1"}]


def _items(rows):
    return [{"report_nm": nm, "rcept_no": rn, "rcept_dt": rn[:8]} for nm, rn in rows]


# ── 롯데리츠(330590) — 반기 결산 1~6·7~12월 ───────────────────────────────────────────────────────
LOTTE_LIST = _items([
    ("사업보고서 (2023.12)", "20240306000500"), ("사업보고서 (2024.06)", "20240902000317"), ("반기보고서 (2024.09)", "20241114001627"),
    ("사업보고서 (2024.12)", "20250312000767"), ("반기보고서 (2025.03)", "20250514000917"), ("사업보고서 (2025.06)", "20250911000374"),
    ("반기보고서 (2025.09)", "20251113000638"), ("사업보고서 (2025.12)", "20260310002810"), ("[기재정정]사업보고서 (2025.12)", "20260402000111"),
    ("반기보고서 (2026.03)", "20260514000609"), ("사업보고서 (2026.06)", "20260921000222")])
LOTTE_ANNUAL = [
    _rep("20250312000767", (12, 11, 10), ("10613567528", "5841464604", "3177878339"), ("1169497783225", "1038068673629", "1055461407196")),
    _rep("20260402000111", (14, 13, 12), ("19627460882", "16546765792", "10613567528"), ("1139498580374", "1153680478920", "1169497783225")),
    _rep("20260921000222", (15, 14, 13), ("23770182540", "19627460882", "16546765792"), ("1127725590182", "1139498580374", "1153680478920")),
]
LOTTE_INTERIM = [_interim("20251113000638", 14, "1129578756324"), _interim("20260514000609", 15, "1114525962143"),
                 _interim("20241114001627", 12, "1017577519609")]
TODAY = "20261020"


def _lotte():
    cal = pc.calendar_of(pc.parse_listing(LOTTE_LIST))
    facts = pc.collect_facts(cal, LOTTE_ANNUAL, LOTTE_INTERIM)
    return cal, facts, pc.build_cells(cal, facts, TODAY)


def test_calendar_reads_six_month_periods_and_first_filing_dates():
    cal = pc.calendar_of(pc.parse_listing(LOTTE_LIST))
    assert cal["step"] == 6 and cal["ends"][-3:] == ["20250630", "20251231", "20260630"]
    assert cal["filed"]["20251231"] == "20260310"          # 기재정정(04/02)이 아니라 처음 공시된 날
    assert cal["interims"] == {"20240930": "20241114", "20250331": "20250514", "20250930": "20251113", "20260331": "20260514"}
    twelve = pc.parse_listing(_items([(f"사업보고서 ({y}.12)", f"{y + 1}0320000001") for y in range(2018, 2026)]))
    assert pc.calendar_of(twelve) is None                  # 12개월 결산은 대상이 아니다


def test_semiannual_cells_give_the_same_twelve_months_as_firm():
    _cal, _f, cells = _lotte()
    a, q = cells["annual"], cells["quarter"]
    assert a[2025]["ni"] == 16546765792 + 19627460882 == 36174226674          # firm FY0(#149) 와 같다 — 종전 연간 칸 19,627,460,882
    assert a[2025]["eq"] == 1139498580374 and a[2025]["avail"] == "20260310" and "202506~202512 결산기 2개 합" in a[2025]["why"]
    assert (q[(2025, 1)]["ni"], q[(2025, 2)]["ni"], q[(2025, 3)]["ni"]) == (0.0, 16546765792, 16546765792)
    assert q[(2025, 2)]["avail"] == "20250911"        # 2025.06 사업보고서 접수일 — 공시 기한 45일 규칙(8/14)이 아니다
    assert q[(2025, 3)]["eq"] == 1129578756324 and q[(2025, 3)]["avail"] == "20251113"   # 자본 = 9월 3개월 시점 보고서
    assert q[(2026, 2)]["ni"] == 23770182540 and q[(2026, 2)]["eq"] == 1127725590182 and q[(2026, 2)]["avail"] == "20260921"
    assert (2026, 3) not in q                          # 2026.09 3개월 시점 보고서가 아직 없다 — 칸 미확정(다음 수집이 쓴다)
    ttm = a[2025]["ni"] + q[(2026, 2)]["ni"] - q[(2025, 2)]["ni"]
    assert ttm == 19627460882 + 23770182540 == 43397643422                     # firm TTM(2025-12 + 2026-06) 과 같다
    assert all(pc.avail_of(c["why"]) == c["avail"] for c in list(q.values()) + list(a.values()))


def test_first_period_is_not_summed():
    """첫 결산기(제1기)는 길이를 모른다 — 그 결산기가 든 사업연도 합은 없다(값을 지어내지 않는다)."""
    lst = _items([("사업보고서 (2019.12)", "20200330000001"), ("사업보고서 (2020.06)", "20200901000001"), ("사업보고서 (2020.12)", "20210322000001")])
    cal = pc.calendar_of(pc.parse_listing(lst))
    facts = pc.collect_facts(cal, [_rep("20210322000001", (3, 2, 1), ("30", "20", "10"), ("9", "8", "7"))], [])
    cells = pc.build_cells(cal, facts, TODAY)
    assert cells["annual"][2019]["ni"] is None and "순이익 합 없음" in cells["annual"][2019]["why"]
    assert cells["annual"][2020]["ni"] == 50


def test_owner_comprehensive_income_when_profit_is_not_split():
    """신한알파리츠 2025.09 사업보고서 — 순이익 지배·비지배 줄이 없고 지배 총포괄만(기타포괄 0). firm 종전 규칙은 총순이익(비지배 포함)."""
    rows = _rep("20251202000364", (15, 14, 13), ("11311947161", "12428538238", "4652415132"), ("1", "1", "1"),
                owners_ci=("6751988258", "9784917968", "3115415132"))
    assert pc.ctrl_ni(rows) == (6751988258.0, "CI_OWNERS_OCI0")
    assert S._ctrl_ni(rows) == 11311947161                                       # 종전 규칙(일반 회사 경로는 그대로)
    assert S._report_periods(rows)[15][0] == 6751988258.0                        # 리츠 결산기 경로는 같은 규칙
    rows_nci = _rep("x", (2, 1, 0), ("100", "90", "0"), ("1", "1", "1"), nci=("30", "20", "0"))
    assert pc.ctrl_ni(rows_nci)[0] == 70.0


# ── SK리츠(395400) — 분기 결산 ────────────────────────────────────────────────────────────────────
SK_LIST = _items([(f"사업보고서 ({y}.{m:02d})", f"{y if m != 12 else y + 1}{(m + 2) % 12 + 1:02d}10000001") for y in (2024, 2025)
                  for m in (3, 6, 9, 12)] + [("사업보고서 (2026.03)", "20260610000569"), ("사업보고서 (2026.06)", "20260908000397")])
SK_ANNUAL = [
    _rep("20250310000001", (15, 14, 13), ("1", "1", "1"), ("1", "1", "1"), id_ni=True),
    _rep("20260310000001", (19, 18, 17), ("32360769209", "19112879383", "30116559104"), ("1939593502956", "1902004458971", "1881154015523"), id_ni=True),
    _rep("20260908000397", (21, 20, 19), ("37228609126", "19375384605", "32360769209"), ("1961439139427", "1950000000000", "1939593502956"), id_ni=True),
]


def test_quarterly_fy0_needs_the_first_period_from_xbrl():
    cal = pc.calendar_of(pc.parse_listing(SK_LIST))
    assert cal["step"] == 3
    facts = pc.collect_facts(cal, SK_ANNUAL, [])
    cells = pc.build_cells(cal, facts, TODAY)
    assert cells["annual"][2025]["ni"] is None             # 2025.03 결산기 — 그해 가장 늦은 사업보고서(2025.12)에 없다
    facts = pc.collect_facts(cal, SK_ANNUAL, [], {"20250331": {"ni": 987539510.0, "eq": 1183440633278.0, "how": "XBRL_XBRL_ID", "filed": "20250610"}})
    cells = pc.build_cells(cal, facts, TODAY)
    a, q = cells["annual"], cells["quarter"]
    assert a[2025]["ni"] == 987539510 + 30116559104 + 19112879383 + 32360769209          # 사업연도 2025 네 결산기
    assert q[(2025, 1)]["ni"] == 987539510 and q[(2025, 1)]["avail"] == "20250610"
    assert q[(2026, 2)]["ni"] == 19375384605 + 37228609126 and q[(2026, 2)]["eq"] == 1961439139427
    ttm = a[2025]["ni"] + q[(2026, 2)]["ni"] - q[(2025, 2)]["ni"]
    assert ttm == 19112879383 + 32360769209 + 19375384605 + 37228609126    # firm TTM(2025-09 ~ 2026-06) 과 같다


# ── 읽는 쪽: 공시일로 칸 고르기 ────────────────────────────────────────────────────────────────────

def test_pit_cell_uses_filing_dates_not_the_45_day_rule():
    _cal, _f, cells = _lotte()
    avail = {k: v["avail"] for k, v in cells["quarter"].items()} | {(y, 4): v["avail"] for y, v in cells["annual"].items()}
    f = rc.with_cells(12, avail)
    assert rc.pit_cell("20250901", 12) == (2025, 2)          # 종전 규칙 — 2025.06 사업보고서(9/11 접수) 전인데 반기 칸
    assert rc.pit_cell("20250901", f) == (2025, 1)           # 결산기 칸 — 그날까지 공시된 칸
    assert rc.pit_cell("20250911", f) == (2025, 2)
    assert rc.pit_cell("20261020", f) == (2026, 2)
    assert rc.pit_cell("20261020", f) == rc.pit_cell("20261020", rc.with_cells(rc.Fiscal(), tuple((k[0], k[1], v) for k, v in avail.items())))
    assert rc.pit_annual("20261020", f) == 2025               # FY0 사업연도는 달력 규칙(firm 과 같다)
    assert rc.cell_open(2026, 2, "20260920", f) is False and rc.cell_open(2026, 2, "20260921", f) is True
    assert rc.with_cells(12, {}) == rc.Fiscal()


def _fake_rows(mp, rows_by_prefix):
    def rows(sql, params=()):
        s = " ".join(sql.split())
        for pre, out in rows_by_prefix.items():
            if s.startswith(pre):
                return out
        return []
    mp.setattr(S, "_pg_rows", rows)


def _db_cells():
    _cal, _f, cells = _lotte()
    q = [(fy, qq, None, None, c["ni"], c["eq"], c["avail"]) for (fy, qq), c in cells["quarter"].items()]
    q += [(fy, 4, a["ni"], a["eq"], a["ni"], a["eq"], a["avail"]) for fy, a in cells["annual"].items()]
    return cells, q


def test_server_loaders_use_period_cells(monkeypatch):
    cells, q = _db_cells()
    junk = [(2026, 3, 5.0, 6.0, None, None, None)]          # 표시 없는 3분기 칸(DART 가 준 값) — 버린다
    y = [(2025, 19627460882.0, 1139498580374.0, None, None, 36174226674.0, 1139498580374.0, True),
         (2024, 10613567528.0, 1169497783225.0, 3.0, None, 16455032132.0, 1169497783225.0, True)]
    _fake_rows(monkeypatch, {"SELECT fy, quarter,": q + junk, "SELECT y.fy,": y, "SELECT ni_fy, eq_fy": [(19627460882.0, 1.0)]})
    monkeypatch.setattr(S, "today_kst", lambda: __import__("datetime").date(2026, 10, 20))
    finq = asyncio.run(S._firm_fin_by_q("330590"))
    fin = asyncio.run(S._firm_fin_by_fy("330590"))
    assert (2026, 3) not in finq and finq[(2026, 2)] == (23770182540.0, 1127725590182.0)
    assert finq.cells[(2026, 2)] == "20260921" and finq.cells[(2025, 4)] == "20260310"
    assert fin[2025] == (36174226674.0, 1139498580374.0) and fin[2024][0] == 16455032132.0   # 재무 요약 값(한 결산기)이 덮지 않는다
    fye = rc.with_cells(12, finq.cells)
    cap = 1_178_993_046_720
    p = S._pit_raw("20261016", cap, fin, finq, fye)
    assert p["pit_q"] == "2026Q2" and round(cap / p["per_ttm"]) == 43397643422 and round(cap / p["per_fy0"]) == 36174226674
    assert round(p["per_fy0"], 2) == 32.59 and round(p["per_ttm"], 2) == 27.17          # firm(#149) 과 같다
    assert round(cap / p["pbr_mrq"]) == 1127725590182
    p0 = S._pit_raw("20250901", cap, fin, finq, fye)                                   # 2025.06 사업보고서 접수(9/11) 전 — 1분기 칸(= 사업연도 2024)
    assert p0["pit_q"] == "2025Q1" and round(cap / p0["per_ttm"]) == 16455032132


def test_server_loaders_unchanged_without_period_cells(monkeypatch):
    _fake_rows(monkeypatch, {"SELECT fy, quarter,": [(2026, 2, 5.0, 6.0, None, None, None)],
                             "SELECT y.fy,": [(2025, 1.0, 2.0, None, None, None, None, False)], "SELECT ni_fy, eq_fy": []})
    finq = asyncio.run(S._firm_fin_by_q("005930"))
    fin = asyncio.run(S._firm_fin_by_fy("005930"))
    assert dict(finq) == {(2026, 2): (5.0, 6.0)} and finq.cells == {} and fin == {2025: (1.0, 2.0)}
    assert rc.with_cells(12, finq.cells) == rc.Fiscal()


def test_batch_apply_marked_matches_server_rules():
    cells, q = _db_cells()
    rows = [("330590", fy, qq, ni, eq, av) for fy, qq, _a, _b, ni, eq, av in q]
    fin_all = {"330590": {2025: (19627460882.0, 1.0), 2018: (None, None)}, "005930": {2025: (1.0, 2.0)}}
    finq_all = {"330590": {(2026, 3): (5.0, 6.0), (2025, 2): (9.0, 9.0)}, "005930": {(2026, 2): (1.0, 1.0)}}
    out = pc.apply_marked(fin_all, finq_all, rows)
    assert set(out) == {"330590"} and out["330590"][(2025, 2)] == "20250911"
    assert fin_all["330590"][2025] == (36174226674.0, 1139498580374.0) and fin_all["330590"][2018] == (None, None)
    assert (2026, 3) not in finq_all["330590"] and finq_all["330590"][(2025, 2)] == (16546765792.0, 1153680478920.0)
    assert finq_all["005930"] == {(2026, 2): (1.0, 1.0)} and fin_all["005930"] == {2025: (1.0, 2.0)}
    pq: dict = {}
    pc.apply_marked(None, pq, rows, keep_q4=True)
    assert (2025, 4) in pq["330590"]


def test_period_cell_writes_rows():
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location("mfq", pathlib.Path(__file__).resolve().parents[1] / "scripts" / "market_fund_quarterly.py")
    mfq = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mfq)
    _cal, _f, cells = _lotte()
    w = mfq.period_cell_writes("330590", "OFS", cells, {2025, 2026})
    assert ("330590", 2025, "OFS", 19627460882.0, 1139498580374.0, 36174226674, 1139498580374.0) in w["y"]
    q4 = [r for r in w["q4"] if r[1] == 2025][0]
    assert q4[3] == q4[5] == 36174226674 and pc.avail_of(q4[7]) == "20260310"
    assert [(r[1], r[2], r[3]) for r in w["q"]] == [(2025, 1, "11013"), (2025, 2, "11012"), (2025, 3, "11014"), (2026, 1, "11013"), (2026, 2, "11012")]
    assert all(pc.avail_of(r[7]) for r in w["q"])


# ── firm: 가장 새 재무상태표 · 분기 결산 FY0(저장된 결산기 칸) · 12개월 ROE ──────────────────────────────────────────

def _firm(monkeypatch, reports, alot, **kw):
    from tests.test_pmd_reit_period_ni import _run
    return _run(monkeypatch, overview_acc_mt=kw.pop("overview_acc_mt", "06"), db_acc_mt=12, reports=reports, alot=alot, **kw)


def test_firm_mrq_is_the_newest_balance_sheet(monkeypatch):
    """롯데리츠 10-20 — 3개월 시점 보고서(2026.03, 5/14 접수)보다 2026.06 사업보고서(9/21 접수)가 늦다 → 자본은 2026.06(곡선 2026Q2 칸과 같다)."""
    from tests import test_pmd_reit_period_ni as T
    reports = dict(T.LOTTE)
    reports[(2026, "11012")] = [dict(r, rcept_no="20260514000609") for r in T.LOTTE[(2026, "11012")]]
    p, _ = _firm(monkeypatch, reports, T.LOTTE_ALOT)
    i, m = p["data"]["inputs"], p["data"]["multiples"]
    assert (m["pbr_basis"], i["controlling_equity_krw"]) == ("MRQ", 1_127_725_590_182)
    assert m["pbr_mrq"] == round(T.LOTTE_CAP / 1_127_725_590_182, 2)
    fy0 = 16_546_765_792 + 19_627_460_882
    assert i["roe_pct"] == round(fy0 / ((1_139_498_580_374 + 1_139_498_580_374) / 2) * 100, 2) == 3.17   # 12개월 ÷ 평균(사업연도 말·12개월 전 — 고정값은 세 칸 같은 자본) — 종전 1.7(한 결산기)
    later = dict(reports)
    later[(2026, "11012")] = [dict(r, rcept_no="20261113000001") for r in T.LOTTE[(2026, "11012")]]   # 3개월 시점이 더 늦으면 그것
    p2, _ = _firm(monkeypatch, later, T.LOTTE_ALOT)
    assert p2["data"]["inputs"]["controlling_equity_krw"] == 1_114_525_962_143


def test_firm_quarterly_fy0_uses_the_stored_period_cell(monkeypatch):
    """SK리츠 — 사업보고서 하나에 결산기가 셋이라 DART 로는 FY0 12개월을 못 맞춘다. 저장된 결산기 칸(2025 사업연도 넷 — 첫 결산기는 XBRL)이 있으면 그 값."""
    from tests import test_pmd_reit_period_ni as T
    fy0 = 987_539_510 + 30_116_559_104 + 19_112_879_383 + 32_360_769_209

    async def stored(ticker, fy, e0):
        assert (ticker, fy, e0) == ("395400", 2025, "20251231")
        return {"ni": fy0, "eps": None, "periods": [], "months": 3, "stored": {"from": "2025-03", "to": "2025-12", "count": 4}}, 1_881_154_015_523.0
    monkeypatch.setattr(S, "_stored_period_fy0", stored)
    p, _ = _firm(monkeypatch, T.SK, T.SK_ALOT, overview_acc_mt="03", ticker="395400", cap=T.SK_CAP, price=5310)
    i, m = p["data"]["inputs"], p["data"]["multiples"]
    assert i["net_income_fy0_krw"] == fy0 and m["per_fy0"] == round(T.SK_CAP / fy0, 2)          # 종전 49.39(2025-12 석 달)
    assert i["ni_basis"]["fy0"] == {"periods": [], "net_income_krw": fy0, "stored_period_sum": {"from": "2025-03", "to": "2025-12", "count": 4}}
    assert i["eps_fy0_krw"] == round(fy0 / 288_968_884)
    w = [x for x in p["data"]["warnings"] if x.startswith("분기 결산 회사 — 지배순이익")]
    assert len(w) == 1 and "2025-03~2025-12 결산기 4개" in w[0] and "값 그대로" not in w[0]
    assert m["pbr_basis"] == "MRQ"                                                            # 분기 결산 — 2026.06 사업보고서 자본(종전 FY0 자본)


def test_stored_period_fy0_reads_only_a_matching_cell(monkeypatch):
    why = pc.why_text("20260311", "사업연도 2025 = 202503~202512 결산기 4개 합 · 간격 3개월")
    _fake_rows(monkeypatch, {"SELECT fy, ni_cum_restated, eq_restated, restate_why FROM dart_finstat_q":
                             [(2025, 82_577_747_206.0, 1.9e12, why), (2024, 8.0e9, 1.8e12, why)]})
    got, prev_eq = asyncio.run(S._stored_period_fy0("395400", 2025, "20251231"))
    assert got["ni"] == 82_577_747_206 and got["stored"] == {"from": "2025-03", "to": "2025-12", "count": 4} and prev_eq == 1.8e12
    assert asyncio.run(S._stored_period_fy0("395400", 2025, "20250930")) == (None, None)       # 결산기가 어긋나면 쓰지 않는다


def test_old_taxonomy_prefix_is_read():
    """2018 사업보고서 무렵은 옛 분류체계(`ifrs_…`) — 이리츠코크렙 2018.12 보고서의 전기(2018-06 결산기) 순이익 44.0억."""
    rows = [{"rcept_no": "20190329000001", "sj_div": "CIS", "account_id": "ifrs_ProfitLossAttributableToOwnersOfParent",
             "thstrm_nm": "제 26 기", "thstrm_amount": "5065502394", "frmtrm_nm": "제 25 기", "frmtrm_amount": "4402740732"},
            {"rcept_no": "20190329000001", "sj_div": "BS", "account_id": "ifrs_Equity", "thstrm_nm": "제 26 기", "thstrm_amount": "273463533150",
             "frmtrm_nm": "제 25 기", "frmtrm_amount": "306099597613"}]
    assert pc.ctrl_ni(rows, "frmtrm_amount") == (4402740732.0, "ID") and pc.ctrl_eq(rows, "frmtrm_amount") == 306099597613.0


def test_owner_line_without_standard_code_only_when_unambiguous():
    """디앤디플랫폼리츠 2021.09 사업보고서 — 지배·비지배를 표준 코드 없는 이름 줄로만 나눴다(지배 27.7억 · 비지배 −2.7억, 총순이익 25.0억).
    순이익 귀속·총포괄 귀속 두 곳의 같은 이름 줄이 같은 값일 때만 쓴다(기타포괄이 있어 갈리면 총순이익 — 총포괄 지배 몫을 순이익으로 고르지 않는다)."""
    def row(aid, nm, v):
        return {"rcept_no": "20211209000001", "sj_div": "CIS", "account_id": aid, "account_nm": nm, "thstrm_nm": "제 2 기", "thstrm_amount": v}
    base = [row("ifrs-full_ProfitLoss", "당기순이익(손실)", "2498514116"), row("ifrs-full_ComprehensiveIncome", "총포괄손익", "2498514116"),
            row("-표준계정코드 미사용-", "지배기업 소유주지분", "2772756487"), row("-표준계정코드 미사용-", "비지배지분", "-274242371")]
    assert pc.ctrl_ni(base + [row("-표준계정코드 미사용-", "지배기업 소유주지분", "2772756487")]) == (2772756487.0, "NM_OWNERS")
    split = base + [row("-표준계정코드 미사용-", "지배기업 소유주지분", "2700000000")]          # 기타포괄이 있어 두 값이 갈린다
    assert pc.ctrl_ni(split) == (2498514116.0, "TOTAL")
    nci_only = [r for r in base if r["account_nm"] != "지배기업 소유주지분"]
    assert pc.ctrl_ni(nci_only) == (2498514116.0 + 274242371.0, "TOTAL_MINUS_NM_NCI")
