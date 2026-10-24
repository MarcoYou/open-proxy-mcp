# -*- coding: utf-8 -*-
"""배당 후속 셋 (#216 병합 뒤 확인이 찾은 기존 결함 — DATA-FY1·DATA-EQ4).

  1. `dividend_disclosure` 에 `year` 를 안 주면 기본이 늘 올해-1 이라, 3·6월 결산 회사의 이미 끝난 최신 사업연도(포시에스 6월 결산 FY2026 115원 ·
     동원모빌리티 3월 결산 FY2026 50원)가 기본 요약·추이·결정공시에서 빠졌다.
  2. 「분기별 결정공시」 표 머리줄이 「우선주 DPS」인 채 우선주 아닌 종류주식(두산 「종류주식」·한국금융지주 「1종 종류주식」) 값을 넣었다.
  3. `dividend_data`(firm) 의 결정공시 횟수 표가 3월 결산 회사의 사업연도를 원장 표와 한 해씩 다르게 불렀다.

고정본은 모두 **합성**이다(network 0콜 · DB 0콜). #216 병합 뒤 운영 응답으로 확인한 모양(2026-10-23)을 본떴다.
"""
from __future__ import annotations

import asyncio
import calendar
import json
from datetime import date

import pytest

from open_proxy_mcp.services import dividend as dv
from open_proxy_mcp.services import dividend_data as dd
from open_proxy_mcp.services import price_multiple_data as pmd
from open_proxy_mcp.tools import dividend_data as dd_tool
from open_proxy_mcp.tools import dividend_disclosure as dd_disc


# ── 공통 대역 ───────────────────────────────────────────────────────────────────────────────
def _summary(fy, em, dps, **extra):
    last = calendar.monthrange(fy, em)[1]
    return {"period": f"{fy} 사업보고서(기말)", "stlm_dt": f"{fy}-{em:02d}-{last:02d}", "cash_dps": dps, "total_dps": dps,
            "source": "alotMatter", **dv._fiscal_period_fields(fy, em), **extra}


def _decision(dtype, record_date, rcept_dt, rcept_no, dps=100, pref=0):
    return {"dividend_type": dtype, "record_date": record_date, "rcept_dt": rcept_dt, "rcept_no": rcept_no,
            "dps_common": dps, "dps_preferred": pref, "report_name": "현금ㆍ현물배당결정"}


def _run(monkeypatch, *, annual_fn, decisions=None, year=None, scope="history", today=date(2026, 10, 23)):
    """build_dividend_payload 를 DART 0콜로 — 사업보고서 조회(`_annual_summary`) 호출을 모두 `asked` 에 쌓는다."""
    from open_proxy_mcp.services.company import CompanyResolution
    from open_proxy_mcp.services.contracts import AnalysisStatus

    class _Client:
        def api_call_snapshot(self):
            return 0

    async def resolve(_q):
        return CompanyResolution(status=AnalysisStatus.EXACT, query="합성", candidates=[],
                                 selected={"corp_name": "합성", "stock_code": "", "corp_code": "00000000"})

    asked: list[int] = []
    searched: list[tuple[int, int]] = []

    async def annual(_corp, y):
        asked.append(y)
        return annual_fn(y)

    async def search(corp, start, end):
        searched.append((start, end))
        filings = [{"rcept_no": d["rcept_no"], "rcept_dt": d["rcept_dt"], "report_nm": "현금ㆍ현물배당결정"} for d in (decisions or [])]
        return filings, [], [], None

    async def details(_f):
        return [dict(d) for d in (decisions or [])]

    async def capital(*_a, **_k):
        return False, []

    async def quarterly(*_a, **_k):
        return []

    monkeypatch.setattr(dv, "today_kst", lambda: today)
    monkeypatch.setattr(dv, "get_dart_client", lambda: _Client())
    monkeypatch.setattr(dv, "resolve_company_query", resolve)
    monkeypatch.setattr(dv, "_annual_summary", annual)
    monkeypatch.setattr(dv, "_search_dividend_filings", search)
    monkeypatch.setattr(dv, "_decision_details", details)
    monkeypatch.setattr(dv, "_detect_capital_reserve_reduction", capital)
    monkeypatch.setattr(dv, "_quarterly_full_from_cumulative", quarterly)
    monkeypatch.setattr(dv, "_quarterly_dps_from_cumulative", quarterly)
    p = asyncio.run(dv.build_dividend_payload("합성", scope=scope, year=year, years=3))
    return p, asked, searched


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 1. 기본 연도 — 결산월 기준 끝난 최신 사업연도
# ═════════════════════════════════════════════════════════════════════════════════════════════
def _june_reported(y):
    # 6월 결산: FY2026 = 2025-07-01~2026-06-30. 오늘(2026-10-23) 사업보고서가 이미 나왔다.
    return (_summary(y, 6, {2026: 115}.get(y, 50)), None) if y <= 2026 else ({}, None)


def test_default_year_moves_to_the_latest_ended_fiscal_year_for_a_june_year_end_company(monkeypatch):
    """포시에스 꼴: 6월 결산, 결의 20260904(기준일 2026-06-30), FY2026 사업보고서 있음 — year 를 안 주면 FY2026 이 요약·추이·결정공시에 나온다.
    종전엔 기본이 2025 라 FY2026 결의가 「이미 결의된 해」로만 걸러져 어디에도 안 보였다."""
    dec = _decision("결산배당", "2026-06-30", "20260904", "20260904000001", dps=115)
    p, asked, _ = _run(monkeypatch, annual_fn=_june_reported, decisions=[dec])
    d = p["data"]
    assert d["year"] == 2026
    assert d["summary"]["fiscal_year"] == 2026 and d["summary"]["cash_dps"] == 115
    by_year = {r["year"]: r for r in d["history"]}
    assert by_year[2026]["annual_dps"] == 115 and by_year[2026]["decision_count"] == 1
    assert [r["rcept_no"] for r in d["quarterly_breakdown"]] == ["20260904000001"]
    assert d["default_year"] == {"fiscal_year": 2026, "previous_default": 2025, "fiscal_year_end_month": 6,
                                 "basis": "year 를 주지 않았을 때 결산월 기준 끝난 최신 사업연도 (사업보고서가 있는 해)"}
    assert any("year 를 주지 않아" in w and "FY2026" in w for w in p["warnings"])
    assert d["undecided_fiscal_years"] == []


def test_default_year_moves_for_a_march_year_end_company(monkeypatch):
    """동원모빌리티(018500) 꼴: 3월 결산 FY2026(2025-04-01~2026-03-31) 50원 · 결의 20260612(기준일 2026-03-31)."""
    dec = _decision("결산배당", "2026-03-31", "20260612", "20260612000001", dps=50)
    p, _, _ = _run(monkeypatch, annual_fn=lambda y: (_summary(y, 3, 50), None) if y <= 2026 else ({}, None), decisions=[dec])
    d = p["data"]
    assert d["year"] == 2026 and d["summary"]["fiscal_year"] == 2026 and d["summary"]["cash_dps"] == 50
    assert {r["year"]: r["annual_dps"] for r in d["history"]}[2026] == 50


def test_default_window_reaches_the_resolution_of_the_moved_year(monkeypatch):
    """조사 구간이 옮긴 해의 결산배당 결의(20260904)까지 덮는다 — 종전 구간은 20260630 에서 끝나 이 결의가 구간 밖이었다."""
    dec = _decision("결산배당", "2026-06-30", "20260904", "20260904000001", dps=115)
    p, _, searched = _run(monkeypatch, annual_fn=_june_reported, decisions=[dec], scope="summary")
    assert p["data"]["window"]["end_date"] >= "20260904"
    assert searched and searched[0][1] == 2026
    assert [x["rcept_no"] for x in p["data"]["latest_decisions"]] == ["20260904000001"]


def test_default_year_stays_for_a_december_year_end_company_with_no_extra_call(monkeypatch):
    """삼성전자 꼴(12월 결산): 기본은 작년(2025)이고, 사업보고서를 읽는 횟수는 종전과 같다 — 기본 연도 조사가 추가 호출을 만들지 않는다."""
    p, asked, _ = _run(monkeypatch, annual_fn=lambda y: (_summary(y, 12, 1668), None) if y <= 2025 else ({}, None))
    d = p["data"]
    assert d["year"] == 2025 and "default_year" not in d
    assert asked == [2025], "12월 결산의 기본 연도 조사 응답은 곧 요약이다 — 같은 해를 두 번 묻지 않는다"
    assert not any("year 를 주지 않아" in w for w in p["warnings"])


def test_default_year_does_not_move_when_the_annual_report_is_not_out_yet(monkeypatch):
    """결산은 끝났지만 사업보고서가 아직 없으면 옮기지 않는다 — 빈 요약을 「무배당」처럼 내느니 종전대로 작년을 두고 「미결의」 안내를 거친다."""
    p, asked, _ = _run(monkeypatch, annual_fn=lambda y: (_summary(y, 6, 50), None) if y <= 2025 else ({}, None))
    d = p["data"]
    assert d["year"] == 2025 and "default_year" not in d
    assert [f["fiscal_year"] for f in d["undecided_fiscal_years"]] == [2026]
    assert asked.count(2026) == 1, "빈 응답을 다시 묻지 않는다"


def test_default_year_hint_when_a_resolution_exists_but_the_report_does_not(monkeypatch):
    """결의는 났는데 사업보고서가 아직 없는 몇 주: 기본 연도는 못 옮기지만 결의가 있다는 사실과 year= 손잡이를 알린다(종전엔 아무 신호가 없었다)."""
    dec = _decision("결산배당", "2026-06-30", "20260904", "20260904000001", dps=115)
    p, _, _ = _run(monkeypatch, annual_fn=lambda y: (_summary(y, 6, 50), None) if y <= 2025 else ({}, None), decisions=[dec])
    assert p["data"]["year"] == 2025
    assert any("FY2026" in w and "year=2026" in w and "사업보고서가 아직 없어" in w for w in p["warnings"])


def test_default_year_probe_failure_keeps_the_legacy_year_and_its_warning(monkeypatch):
    p, asked, _ = _run(monkeypatch, annual_fn=lambda y: ({}, "alotMatter 조회 실패: 020"))
    assert p["data"]["year"] == 2025 and asked == [2025]
    assert any("alotMatter 조회 실패: 020" in w for w in p["warnings"])


def test_explicit_year_is_untouched_by_the_default_rule(monkeypatch):
    """`year` 를 명시한 호출은 그대로 — 기본 연도 조사(작년 사업보고서 선행 조회)를 하지 않고 응답에 새 칸도 없다."""
    p, asked, _ = _run(monkeypatch, annual_fn=_june_reported, year=2025)
    d = p["data"]
    assert d["year"] == 2025 and "default_year" not in d
    assert asked[0] == 2025 and asked.count(2025) == 1
    assert not any("year 를 주지 않아" in w for w in p["warnings"])


def test_worst_case_extra_dart_calls_is_one(monkeypatch):
    """한 도구 호출에서 사업보고서(alotMatter) 조회가 종전보다 최악 몇 건 늘어나나 — 비12월 결산에서 +1, 12월 결산 +0 (evidence 의 근거)."""
    def count(annual_fn, decisions=None):
        _, asked, _ = _run(monkeypatch, annual_fn=annual_fn, decisions=decisions)
        return len(asked)

    # 종전 흐름의 호출 수는 year=2025 명시 호출(= 종전 기본과 같은 경로)로 잰다.
    dec = [_decision("결산배당", "2026-06-30", "20260904", "20260904000001", dps=115)]
    old_june_decided = len(_run(monkeypatch, annual_fn=_june_reported, decisions=dec, year=2025)[1])
    new_june_decided = count(_june_reported, dec)
    assert (old_june_decided, new_june_decided) == (1, 2)
    dec_fn = lambda y: (_summary(y, 12, 100), None) if y <= 2025 else ({}, None)             # noqa: E731
    assert len(_run(monkeypatch, annual_fn=dec_fn, year=2025)[1]) == count(dec_fn) == 1
    unreported = lambda y: (_summary(y, 6, 50), None) if y <= 2025 else ({}, None)           # noqa: E731
    assert len(_run(monkeypatch, annual_fn=unreported, decisions=dec, year=2025)[1]) == 1
    assert count(unreported, dec) == 2


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 2. 「분기별 결정공시」 표 — 우선주 칸에는 우선주만
# ═════════════════════════════════════════════════════════════════════════════════════════════
def _class_decisions():
    # 두산 FY2025 꼴 — 결정공시 서식의 「종류주식」 줄 값(2,050/2,050/4,050)이 파서에서 dps_preferred 로 온다.
    return [_decision("분기배당", "2025-03-31", "20250510", "20250510000001", dps=2000, pref=2050),
            _decision("중간배당", "2025-06-30", "20250812", "20250812000001", dps=2000, pref=2050),
            _decision("결산배당", "2025-12-31", "20260213", "20260213000001", dps=4000, pref=4050)]


def _class_summary(y):
    return (_summary(y, 12, 8000, cash_dps_preferred=8150, cash_dps_preferred_label="종류주식"), None) if y <= 2025 else ({}, None)


def test_class_share_decision_values_leave_the_preferred_column(monkeypatch):
    p, _, _ = _run(monkeypatch, annual_fn=_class_summary, decisions=_class_decisions(), year=2025)
    rows = p["data"]["quarterly_breakdown"]
    assert [(r["dps_common_krw"], r["dps_preferred_krw"], r["dps_class_krw"], r["dps_class_label"]) for r in rows] == [
        (2000, 0, 2050, "종류주식"), (2000, 0, 2050, "종류주식"), (4000, 0, 4050, "종류주식")]


def test_decision_table_header_names_the_class_share_like_the_cumulative_table(monkeypatch):
    p, _, _ = _run(monkeypatch, annual_fn=_class_summary, decisions=_class_decisions(), year=2025)
    md = dd_disc._render(p, "history")
    assert "| 연도 | 분기 | 보통주 DPS | 우선주 DPS | 종류주식 DPS | 기준일 | 공시번호 |" in md
    row = [l for l in md.splitlines() if "`20260213000001`" in l][0]
    assert "| 4,000원 | - | 4,050원 |" in row, row


def test_class_label_is_the_original_name_not_a_generic_one(monkeypatch):
    """한국금융지주 꼴 — 원문 이름 「1종 종류주식」이 그대로 머리줄이 된다. 값은 공시 그대로(4,041)."""
    summ = lambda y: (_summary(y, 12, 3980, cash_dps_preferred=4042, cash_dps_preferred_label="1종 종류주식"), None) if y <= 2025 else ({}, None)   # noqa: E731
    dec = [_decision("결산배당", "2025-12-31", "20260213", "20260213000001", dps=3980, pref=4041)]
    p, _, _ = _run(monkeypatch, annual_fn=summ, decisions=dec, year=2025)
    r = p["data"]["quarterly_breakdown"][0]
    assert (r["dps_class_krw"], r["dps_class_label"], r["dps_preferred_krw"]) == (4041, "1종 종류주식", 0)
    assert "1종 종류주식 DPS" in dd_disc._render(p, "history")


def test_preferred_share_company_keeps_its_rows_and_header_unchanged(monkeypatch):
    """삼성전자 꼴: 사업보고서가 「우선주」로 적는 회사의 결정공시 행은 키도 값도 표도 종전 그대로."""
    summ = lambda y: (_summary(y, 12, 1668, cash_dps_preferred=1669, cash_dps_preferred_label="우선주"), None) if y <= 2025 else ({}, None)   # noqa: E731
    dec = [_decision("분기배당", "2025-03-31", "20250510", "20250510000001", dps=365, pref=365)]
    p, _, _ = _run(monkeypatch, annual_fn=summ, decisions=dec, year=2025)
    r = p["data"]["quarterly_breakdown"][0]
    assert r["dps_preferred_krw"] == 365 and "dps_class_krw" not in r and "dps_class_label" not in r
    md = dd_disc._render(p, "history")
    assert "| 연도 | 분기 | 보통주 DPS | 우선주 DPS | 기준일 | 공시번호 |" in md
    assert "| 365원 | 365원 |" in md


def test_company_with_both_preferred_and_class_rows_is_left_as_before():
    """사업보고서에 우선주 행도 있으면 결정공시 한 줄을 어느 쪽에 줄지 모른다 — 종전(우선주 칸)을 둔다."""
    assert dv._company_class_label([{"cash_dps_preferred_label": "우선주·1종 종류주식"}]) == ""
    assert dv._company_class_label([{"cash_dps_preferred_label": "우선주"}, None, {}]) == ""
    assert dv._company_class_label([{"cash_dps_preferred_label": "1종 종류주식"}, {"cash_dps_preferred_label": "1종 종류주식"}]) == "1종 종류주식"
    assert dv._company_class_label([]) == ""


# ═════════════════════════════════════════════════════════════════════════════════════════════
# 3. dividend_data(firm) — 결정공시 표의 사업연도 번호
# ═════════════════════════════════════════════════════════════════════════════════════════════
class _Registry:
    def __init__(self):
        self.tools = {}

    def tool(self, *a, **k):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


def _firm_tool():
    reg = _Registry()
    dd_tool.register_tools(reg)
    return reg.tools["dividend_data"]


def _pay_row(fy, board, record, rcept, dps, dtype="결산배당"):
    """`div_payment` 행 모양: fiscal_year, board_date, record_date, dividend_type_filed, dividend_type, dps_common, total_amount, rcept_no, amended, anomaly, has_special, remarks."""
    return (fy, board, record, dtype, dtype, dps, dps * 1000, rcept, False, None, False, "x")


def _ledger(months_by_year):
    """`div_declared` 연간 행: bsns_year, stock_kind, stock_knd_raw, dps_krw, div_total_krw, payout_pct, status, rcept_no, stlm_dt."""
    return [(y, "미구분", "-", 50.0, 5e9, 10.0, "확정", f"{y}0618000195", f"{y}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}")
            for y, m in sorted(months_by_year.items(), reverse=True)]


def _annual_dicts(months_by_year):
    return [{"stlm_dt": r[8]} for r in _ledger(months_by_year)]


def _call_firm(monkeypatch, pay_rows, ledger_rows, *, fmt="md", complete=(2020, 2021, 2022, 2023, 2024, 2025)):
    """실제 서비스 질의(`firm_history`·`payment_counts`·`payment_scope_years`)를 돌리되 DB 행만 대역으로 — 도구까지 통째로."""
    def rows(sql, params=()):
        s = " ".join(sql.split())
        if "FROM div_payment_scope" in s:
            return [(y,) for y in complete]
        if "FROM div_payment" in s:
            return pay_rows
        if "FROM div_declared" in s:
            return ledger_rows
        return []

    async def fill(p, _corp):
        return p

    async def resolve(_c):
        return {"corp_code": "00000000", "corp_name": "동원모빌리티", "stock_code": "018500"}, None

    monkeypatch.setattr(dd, "year_range", lambda: (2020, 2026))
    monkeypatch.setattr(dd, "_rows", rows)
    monkeypatch.setattr(dd, "fill_remarks_from_cold", fill)
    monkeypatch.setattr(pmd, "_resolve_listed", resolve)
    return asyncio.run(_firm_tool()(company="018500", scope="firm", format=fmt))


def _march_decisions():
    # 3월 결산. 저장된 해(fiscal_year)는 12월 결산 가정이다: 2025-06-11 결의(기준일 2025-03-31)가 FY2024, 2026-06-12 결의가 FY2025.
    return [_pay_row(2025, "2026-06-12", "2026-03-31", "20260612000101", 50),
            _pay_row(2025, "2025-11-10", "2025-09-30", "20251110000101", 20, dtype="중간배당"),
            _pay_row(2024, "2025-06-11", "2025-03-31", "20250611000101", 50)]


def test_march_year_end_decision_table_uses_the_fiscal_year_end_year(monkeypatch):
    """2025-06-11 결의(3월 결산 FY2025)를 결정공시 표가 FY2024 로, 원장 표는 FY2025 로 불렀다 — 이제 같은 해 번호다."""
    md = _call_firm(monkeypatch, _march_decisions(), _ledger({2025: 3, 2026: 3}))
    table = md.split("### 결정공시 — 몇 번 배당했나")[1].split("\n\n")[1]
    rows = [l for l in table.splitlines() if l.startswith("| 20")]
    assert [l.split("|")[1].strip().split()[0] for l in rows] == ["2026", "2025"], rows
    assert [l.split("|")[2].strip() for l in rows] == ["2회", "1회"], rows
    assert "결산 종료연도" in md and "3월 결산" in md


def test_march_year_end_json_has_new_year_numbers_and_an_additive_alignment_key(monkeypatch):
    out = json.loads(_call_firm(monkeypatch, _march_decisions(), _ledger({2025: 3, 2026: 3}), fmt="json"))
    pc = out["payment_counts"]
    assert [(r["fiscal_year"], r["n_payments"]) for r in pc["rows"]] == [(2026, 2), (2025, 1)]
    assert {d["rcept_no"]: d["fiscal_year"] for d in pc["decisions"]} == {"20260612000101": 2026, "20251110000101": 2026, "20250611000101": 2025}
    al = pc["fiscal_year_alignment"]
    assert al["end_month"] == 3 and al["shifted_decisions"] == 3 and al["stored_complete_years"] == [2020, 2021, 2022, 2023, 2024, 2025]
    # 온전한 해는 걸쳐 있는 저장 두 해가 모두 온전할 때만 — 2020 은 2019 가 없어 빠진다.
    assert pc["complete_years"] == [2021, 2022, 2023, 2024, 2025]
    # 원장 표는 건드리지 않는다.
    assert [r["bsns_year"] for r in out["data"]["annual"]] == [2026, 2025]


def test_december_year_end_pay_is_returned_untouched(monkeypatch):
    """삼성전자 꼴(12월 결산): 같은 객체를 그대로 — 응답 바이트가 같다. 새 키도 없다."""
    rows = [_pay_row(2025, "2026-01-29", "2025-12-31", "20260129000101", 566),
            _pay_row(2025, "2025-10-30", "2025-09-30", "20251030000101", 370, dtype="분기배당")]
    monkeypatch.setattr(dd, "_rows", lambda sql, params=(): [(y,) for y in (2024, 2025)] if "div_payment_scope" in sql else rows)
    pay = dd.payment_counts("00000000", 2020, 2026)
    assert dd.align_payment_years(pay, dd.settlement_end_month(_annual_dicts({2024: 12, 2025: 12}))) is pay
    out = json.loads(_call_firm(monkeypatch, rows, _ledger({2024: 12, 2025: 12}), fmt="json"))
    assert "fiscal_year_alignment" not in out["payment_counts"]
    assert [r["fiscal_year"] for r in out["payment_counts"]["rows"]] == [2025]
    assert "결산 종료연도로 맞췄다" not in _call_firm(monkeypatch, rows, _ledger({2024: 12, 2025: 12}))


def test_mixed_settlement_months_are_left_alone():
    """결산기가 여럿인 회사(반기·분기 결산 리츠)·결산월 변경 이행기: 한 달로 못 박을 수 없으니 옮기지 않는다."""
    assert dd.settlement_end_month(_annual_dicts({2024: 6}) + _annual_dicts({2025: 12})) is None
    assert dd.settlement_end_month(_annual_dicts({2024: 3, 2025: 3})) == 3
    assert dd.settlement_end_month([]) is None and dd.settlement_end_month(None) is None
    assert dd.settlement_end_month([{"stlm_dt": None}, {"stlm_dt": "-"}]) is None


def test_alignment_matches_the_disclosure_tool_rule(monkeypatch):
    """결정공시 한 건의 사업연도는 `dividend_disclosure` 가 쓰는 `_bucket_fiscal_year(결산월)` 와 같다 — 공용 귀속 함수 재사용(규칙 6)."""
    for r in _march_decisions():
        item = {"dividend_type": r[4], "record_date": r[2], "rcept_dt": r[7][:8]}
        monkeypatch.setattr(dd, "_rows", lambda sql, params=(), r=r: [(2024,)] if "div_payment_scope" in sql else [r])
        out = dd.align_payment_years(dd.payment_counts("00000000", 2020, 2026), 3)["decisions"][0]
        assert out["fiscal_year"] == dv._bucket_fiscal_year(item, 3)
