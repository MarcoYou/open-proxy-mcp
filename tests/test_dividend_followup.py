# -*- coding: utf-8 -*-
"""배당 후속 셋 (#216 병합 뒤 확인이 찾은 기존 결함 — DATA-FY1·DATA-EQ4).

  1. `dividend_disclosure` 에 `year` 를 안 주면 기본이 늘 올해-1 이라, 3·6월 결산 회사의 이미 끝난 최신 사업연도(포시에스 6월 결산 FY2026 115원 ·
     동원모빌리티 3월 결산 FY2026 50원)가 기본 요약·추이·결정공시에서 빠졌다.
  2. 「분기별 결정공시」 표 머리줄이 「우선주 DPS」인 채 우선주 아닌 종류주식(두산 「종류주식」·한국금융지주 「1종 종류주식」) 값을 넣었다.
  3. `dividend_data`(firm·screen) 의 결정공시 횟수가 비12월 결산 회사의 사업연도를 원장 표와 한 해씩 다르게 불렀다.
     원인: 수집기가 `div_payment.fiscal_year` 를 결산월(`acc_mt`)로 정하고 **비12월 결산은 시작연도**로 부른다(12월 가정이 아니다).

고정본은 모두 **합성**이다(network 0콜 · 운영 DB 0콜). 3번은 운영 `div_payment` 행의 모양(2026-10-24 읽기 전용 조회: 동원모빌리티 018500 4행, 비12월 결산 129행)을 본떴다.
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
# 3. dividend_data(firm·screen) — 결정공시 표의 사업연도 번호 (수집기는 비12월 결산을 시작연도로 저장한다)
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


def _pay_row(fy, board, record, rcept, dps, dtype="결산배당", acc_mt=12):
    """운영 `div_payment` 행 모양: fiscal_year(저장 해), board_date, record_date, dividend_type_filed, dividend_type, dps_common, total_amount, rcept_no, amended, anomaly, has_special, remarks, acc_mt."""
    return (fy, board, record, dtype, dtype, dps, dps * 1000, rcept, False, None, False, "x", acc_mt)


def _ledger(months_by_year):
    """`div_declared` 연간 행: bsns_year, stock_kind, stock_knd_raw, dps_krw, div_total_krw, payout_pct, status, rcept_no, stlm_dt."""
    return [(y, "미구분", "-", 50.0, 5e9, 10.0, "확정", f"{y}0618000195", f"{y}-{m:02d}-{calendar.monthrange(y, m)[1]:02d}")
            for y, m in sorted(months_by_year.items(), reverse=True)]


#: 코스피 온전한 해 — 운영 `div_payment_scope`(2020~2024)와 같다. 저장 해 번호 기준이다.
_STORED_COMPLETE = (2020, 2021, 2022, 2023, 2024)


def _call_firm(monkeypatch, pay_rows, ledger_rows, *, fmt="md", complete=_STORED_COMPLETE, year_to=2026):
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

    monkeypatch.setattr(dd, "year_range", lambda: (2020, year_to))
    monkeypatch.setattr(dd, "_rows", rows)
    monkeypatch.setattr(dd, "fill_remarks_from_cold", fill)
    monkeypatch.setattr(pmd, "_resolve_listed", resolve)
    return asyncio.run(_firm_tool()(company="018500", scope="firm", format=fmt))


def _md_year_table(md):
    table = md.split("### 결정공시 — 몇 번 배당했나")[1].split("\n\n")[1]
    rows = [l for l in table.splitlines() if l.startswith("| 20")]
    return [(l.split("|")[1].strip(), l.split("|")[2].strip()) for l in rows]


# 동원모빌리티(018500, 3월 결산) 운영 행 그대로 — 저장 해는 사업연도 **시작**연도다(2025-06-11 결의는 2024.04~2025.03 사업연도의 결산배당, 기준일을 주총 뒤 5-30 으로 옮긴 분리형).
_DONGWON = [_pay_row(2025, "2026-06-12", "2026-03-31", "20260612800660", 50, acc_mt=3),
            _pay_row(2024, "2025-06-11", "2025-05-30", "20250611800339", 50, acc_mt=3),
            _pay_row(2023, "2024-06-12", "2024-03-31", "20240612800189", 50, acc_mt=3),
            _pay_row(2022, "2023-06-13", "2023-03-31", "20230613800185", 20, acc_mt=3)]


def test_march_year_end_decision_table_uses_the_same_year_numbers_as_the_ledger(monkeypatch):
    """018500: 원장 표는 bsns_year 2025 = 결산일 2025-03-31 이다. 결정공시 표의 2025-06-11 결의(저장 2024)가 같은 FY2025 로 나와야 한다 —
    종전(12월 가정 대비 차이만큼 옮기기)은 이 결의를 FY2024 에 두고 2024-03-31 결의는 FY2024 로 옮겨 FY2024 「2회」·FY2025 0회를 냈다."""
    md = _call_firm(monkeypatch, _DONGWON, _ledger({2026: 3, 2025: 3, 2024: 3, 2023: 3}))
    assert _md_year_table(md) == [("2026 ⚠️미완", "1회"), ("2025", "1회"), ("2024", "1회"), ("2023", "1회")], _md_year_table(md)
    assert "결산 종료연도" in md and "3월 결산" in md and "시작연도" in md


def test_march_year_end_json_has_new_year_numbers_and_an_additive_alignment_key(monkeypatch):
    out = json.loads(_call_firm(monkeypatch, _DONGWON, _ledger({2026: 3, 2025: 3, 2024: 3, 2023: 3}), fmt="json"))
    pc = out["payment_counts"]
    assert [(r["fiscal_year"], r["n_payments"]) for r in pc["rows"]] == [(2026, 1), (2025, 1), (2024, 1), (2023, 1)]
    assert {d["rcept_no"]: d["fiscal_year"] for d in pc["decisions"]} == {
        "20260612800660": 2026, "20250611800339": 2025, "20240612800189": 2024, "20230613800185": 2023}
    al = pc["fiscal_year_alignment"]
    assert al["end_month"] == 3 and al["shifted_decisions"] == 4 and al["stored_complete_years"] == [2020, 2021, 2022, 2023, 2024]
    # 온전한 해(저장 해 2020~2024 가 온전) = 결산 종료연도 2021~2025. 맨 끝 해 FY2026(저장 2025)은 아직 아니다.
    assert pc["complete_years"] == [2021, 2022, 2023, 2024, 2025]
    # 원장 표는 건드리지 않는다 — 원장과 같은 번호가 되도록 결정공시 쪽만 옮겼다.
    assert [r["bsns_year"] for r in out["data"]["annual"]] == [2026, 2025, 2024, 2023]
    assert {r["fiscal_year"] for r in pc["rows"]} <= {r["bsns_year"] for r in out["data"]["annual"]}


def test_the_last_year_is_complete_once_its_stored_year_is_complete(monkeypatch):
    """⚠️미완 판정도 같은 매핑 — 저장 2025 가 온전해지면 FY2026 의 ⚠️미완이 빠진다(종전 「걸친 두 해」 규칙은 맨 끝 해를 영영 미완으로 두었다)."""
    md = _call_firm(monkeypatch, _DONGWON, _ledger({2026: 3, 2025: 3, 2024: 3, 2023: 3}), complete=(2020, 2021, 2022, 2023, 2024, 2025))
    assert _md_year_table(md)[0] == ("2026", "1회")
    # 가장 이른 해: 저장 2022 가 온전하지 않으면 그 해(FY2023)만 미완이다.
    md = _call_firm(monkeypatch, _DONGWON, _ledger({2026: 3, 2025: 3, 2024: 3, 2023: 3}), complete=(2023, 2024))
    assert dict(_md_year_table(md)) == {"2026 ⚠️미완": "1회", "2025": "1회", "2024": "1회", "2023 ⚠️미완": "1회"}


def test_both_ways_of_numbering_agree_on_every_operating_shaped_row():
    """`저장 해 + 1`(비12월 결산)은 `dividend._bucket_fiscal_year(결의, 결산월)`(공용 귀속 함수)과 같은 해를 낸다 — 운영 비12월 결산 129행 전부에서 같았다."""
    for r in _DONGWON:
        item = {"dividend_type": r[4], "record_date": r[2], "rcept_dt": r[7][:8]}
        assert r[0] + dd.stored_year_shift(r[12]) == dv._bucket_fiscal_year(item, r[12])
    for stored, dtype, record, board, acc in ((2024, "결산배당", "2025-06-30", "2025-08-14", 6), (2024, "중간배당", "2024-12-31", "2024-11-12", 6),
                                                (2024, "결산배당", "2025-09-30", "2025-11-12", 9), (2024, "중간배당", "2025-03-31", "2025-02-10", 9)):
        item = {"dividend_type": dtype, "record_date": record, "rcept_dt": board.replace("-", "")}
        assert stored + dd.stored_year_shift(acc) == dv._bucket_fiscal_year(item, acc), (dtype, record, acc)


def test_june_year_end_settles_on_the_record_date_equal_to_the_settlement_date(monkeypatch):
    """6월 결산: 결산배당 기준일 = 결산일(6-30) 이라 12월 가정 대비 차이가 0 이다 — 종전 방식은 한 건도 못 옮겼다. 저장 2024(2024.07~2025.06) → FY2025."""
    rows = [_pay_row(2024, "2025-08-14", "2025-06-30", "20250814800001", 115, acc_mt=6),
            _pay_row(2024, "2024-11-12", "2024-12-31", "20241112800001", 30, dtype="중간배당", acc_mt=6),
            _pay_row(2023, "2024-08-13", "2024-06-30", "20240813800001", 100, acc_mt=6)]
    md = _call_firm(monkeypatch, rows, _ledger({2025: 6, 2024: 6}))
    assert _md_year_table(md) == [("2025", "2회"), ("2024", "1회")], _md_year_table(md)
    assert "6월 결산" in md


def test_september_year_end_interim_dividend_with_a_march_record_date(monkeypatch):
    """9월 결산: 중간배당 기준일 3월(2025-03-31)은 2024.10~2025.09 사업연도 안이다 → FY2025(저장 2024). 종전 방식은 결산일 기준일 결산배당을 못 옮겼다."""
    rows = [_pay_row(2024, "2025-11-12", "2025-09-30", "20251112800001", 200, acc_mt=9),
            _pay_row(2024, "2025-02-10", "2025-03-31", "20250210800001", 100, dtype="중간배당", acc_mt=9),
            _pay_row(2023, "2024-11-13", "2024-09-30", "20241113800001", 180, acc_mt=9)]
    md = _call_firm(monkeypatch, rows, _ledger({2025: 9, 2024: 9}))
    assert _md_year_table(md) == [("2025", "2회"), ("2024", "1회")], _md_year_table(md)


def test_december_year_end_pay_is_returned_untouched(monkeypatch):
    """삼성전자 꼴(12월 결산): 해 번호·온전한 해 목록이 종전과 같고 새 키·안내도 없다. 구간 밖(저장 2019·2027) 행은 12월 결산에서는 옮기지 않고 걸러진다."""
    rows = [_pay_row(2025, "2026-01-29", "2025-12-31", "20260129000101", 566),
            _pay_row(2025, "2025-10-30", "2025-09-30", "20251030000101", 370, dtype="분기배당"),
            _pay_row(2019, "2020-01-30", "2019-12-31", "20200130000101", 361)]       # 요청 구간(2020~) 밖 — SQL 은 이미 한 해 넓게 읽는다
    out = json.loads(_call_firm(monkeypatch, rows, _ledger({2024: 12, 2025: 12}), fmt="json"))
    pc = out["payment_counts"]
    assert "fiscal_year_alignment" not in pc
    assert [(r["fiscal_year"], r["n_payments"]) for r in pc["rows"]] == [(2025, 2)]
    assert pc["complete_years"] == list(_STORED_COMPLETE)
    assert "결산 종료연도로 맞췄다" not in _call_firm(monkeypatch, rows, _ledger({2024: 12, 2025: 12}))


def test_missing_settlement_month_is_not_filled_with_december():
    assert dd.stored_year_shift(None) == 0 and dd.stored_year_shift(12) == 0 and dd.stored_year_shift("12") == 0
    assert dd.stored_year_shift(3) == 1 and dd.stored_year_shift("9") == 1 and dd.stored_year_shift("-") == 0


def test_payment_counts_reads_one_stored_year_earlier_for_the_requested_range(monkeypatch):
    """요청 구간은 결산 종료연도 기준이라 SQL 은 저장 해를 한 해 앞부터 읽는다(비12월 결산의 첫 해 결의가 저장 해 y0-1 에 있다)."""
    seen = {}

    def rows(sql, params=()):
        if "div_payment_scope" in sql:
            return [(2020,)]
        seen["params"] = params
        return []
    monkeypatch.setattr(dd, "_rows", rows)
    dd.payment_counts("00000000", 2020, 2026)
    assert seen["params"] == ("00000000", 2019, 2026)


# ── screen·이력열: 같은 매핑 (실제 Postgres 17 — Docker 가 없으면 건너뜀) ─────────────────────────────
psycopg = pytest.importorskip("psycopg")

_SCREEN_DDL = """
CREATE TABLE div_declared(
  corp_code text, name text, tickers text, bsns_year int, reprt_code text, reprt_label text, stock_knd_raw text, stock_kind text,
  dps_krw double precision, div_total_krw double precision, unit_raw text, payout_pct double precision, rcept_no text, stlm_dt text,
  se_raw text, status text, line_no smallint);
CREATE TABLE div_payment(corp_code text, fiscal_year int, acc_mt int, board_date text);
CREATE TABLE div_payment_scope(market text, fiscal_year int, is_complete boolean);
CREATE TABLE krx_listing(ticker text, first_seen_dd text, before_window boolean);
"""


def _ledger_row(corp, name, ticker, bsns_year, stlm, rcept):
    return (corp, name, ticker, bsns_year, "11011", "사업보고서", "보통주", "보통", 50.0, 5e9, "원", 10.0, rcept, stlm, "주당 현금배당금(원)", "확정", 1)


@pytest.fixture
def screen_db(pg17, monkeypatch):
    """M1 = 3월 결산(저장 해는 시작연도: 2024 가 FY2025, 결의 2건) · C1 = 12월 결산 · C0 = 결산월 칸이 빈 행(12월로 본다)."""
    def run(sql, params=()):
        with psycopg.connect(pg17, autocommit=True) as c:
            cur = c.execute(sql, params)
            return [tuple(r) for r in cur.fetchall()] if cur.description else None
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(_SCREEN_DDL)
        with c.cursor() as cur:
            cur.executemany("INSERT INTO div_declared VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", [
                _ledger_row("M1", "동원모빌리티", "018500", 2025, "2025-03-31", "20250618000195"),
                _ledger_row("M1", "동원모빌리티", "018500", 2024, "2024-03-31", "20240618000195"),
                _ledger_row("C1", "삼성전자", "005930", 2025, "2025-12-31", "20260310000100"),
                _ledger_row("C0", "결산월미상", "000009", 2025, "2025-12-31", "20260310000900")])
            cur.execute("INSERT INTO div_payment_scope VALUES ('KOSPI', 2024, true), ('KOSPI', 2025, true)")
            cur.executemany("INSERT INTO div_payment (corp_code, fiscal_year, acc_mt, board_date) VALUES (%s,%s,%s,%s)", [
                ("M1", 2024, 3, "2025-06-11"), ("M1", 2024, 3, "2025-02-10"),        # 결산 종료연도 FY2025 의 결의 2건(결산 + 중간)
                ("M1", 2025, 3, "2026-06-12"),                                         # FY2026
                ("M1", 2023, 3, "2024-06-12"),                                         # FY2024
                ("C1", 2025, 12, "2026-01-29"), ("C1", 2025, 12, "2025-10-30"),       # 12월 결산: 저장 해 = 종료연도
                ("C0", 2025, None, "2026-01-30"), ("C0", 2025, None, "2025-07-30")])  # 결산월 칸이 비면 옮기지 않는다
            cur.executemany("INSERT INTO krx_listing VALUES (%s,%s,%s)", [("018500", "20200101", True), ("005930", "20200101", True), ("000009", "20200101", True)])
    monkeypatch.setattr(dd, "_rows", lambda sql, params=(): run(sql, params))
    return run


def test_screen_min_payments_counts_non_december_year_ends_by_their_ending_year(screen_db):
    got = {r["corp_code"] for r in dd.screen(2025, min_payments=2, limit=100)["rows"]}
    assert got == {"M1", "C1", "C0"}, "M1: 원장 FY2025 와 같은 번호의 결의 2건(종전은 저장 해 2025 의 1건이라 빠졌다) · 12월 결산·결산월 빈 행은 종전 그대로"
    assert {r["corp_code"] for r in dd.screen(2025, min_payments=3, limit=100)["rows"]} == set()


def test_payment_history_column_years_are_ledger_years_for_non_december_year_ends(screen_db):
    out = dd.payment_history([("M1", "018500"), ("C1", "005930"), ("C0", "000009")], 2024, 2026)
    assert out["M1"] == [1, 2, 1], "FY2024·FY2025·FY2026 결의 횟수 — 종전은 한 해씩 앞이어 [2, 1, 0]"
    assert out["C1"] == [0, 2, 0] and out["C0"] == [0, 2, 0], "12월 결산(·결산월 미상)은 종전과 같다"
