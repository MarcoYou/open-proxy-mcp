# -*- coding: utf-8 -*-
"""dividend_disclosure — 분기배당이 어느 분기에 귀속되는지 두 표가 같게 말한다 (B4, 공개 DATA 문서 DATA-DIV8).

사고 모양(KB금융 2025, 운영 재호출): 같은 배당을 두 표가 다른 분기로 불렀다.
  · 결정공시 표(`_quarter_label`): 기준일 달력 월만 봤다 — 2025-05-12 기준일(1분기 배당) → 「Q2 (중간)」, 08-11 → 「Q3」, 11-14 → 「Q4 (예비결산)」.
  · 누적차분 표(`_quarterly_full_from_cumulative`): 반기보고서 행이 없으면 3분기 누계 − 1분기 누계(= 두 분기분 1,850원)에 「Q3」 이름을 붙였다.

규칙: 분기 라벨은 「배당기준일 당일 또는 그 이전에 끝난 가장 최근 회계 분기」(회계연도 결산월 기준, 기준일이 분기 말 날이면 그 분기)다. 결산배당은 그대로 「결산」.
차분이 두 분기를 덮으면(보고서 행 없음) 한 분기로 적지 않고 합산이라고 표시한다.
"""
from __future__ import annotations

import asyncio

import pytest

from open_proxy_mcp.services import dividend as dv


def _d(record_date, dtype="분기배당", dps=100, rcept="20250101000001", rcept_dt="20250101"):
    return {"dividend_type": dtype, "record_date": record_date, "dps_common": dps, "rcept_no": rcept,
            "rcept_dt": rcept_dt, "report_name": "현금ㆍ현물배당결정"}


# ── 결정공시 표 라벨 ───────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("record_date,want", [
    ("2025-05-12", "Q1"),             # KB금융 1분기 배당 — 달력 월(5월)로는 Q2 로 읽혔다
    ("2025-08-11", "Q2 (중간)"),      # KB금융 2분기 배당 — 종전 Q3
    ("2025-11-14", "Q3"),             # KB금융 3분기 배당 — 종전 Q4 (예비결산)
    ("2025-03-31", "Q1"), ("2025-06-30", "Q2 (중간)"), ("2025-09-30", "Q3"),     # 삼성전자 꼴(분기 말 기준일)은 종전과 같다
])
def test_quarter_label_is_the_latest_quarter_that_ended_on_or_before_the_record_date(record_date, want):
    assert dv._quarter_label(_d(record_date)) == want


def test_a_record_date_on_the_fiscal_year_end_is_the_fourth_quarter_unless_it_is_the_year_end_dividend():
    assert dv._quarter_label(_d("2025-12-31")) == "Q4 (예비결산)"
    assert dv._quarter_label(_d("2025-12-31", dtype="결산배당")) == "결산"
    assert dv._quarter_label(_d("2026-02-27", dtype="결산배당")) == "결산"


def test_quarter_label_follows_the_fiscal_year_end_month():
    # 3월 결산: 분기 말 = 6·9·12·3월. 기준일 2025-08-14 은 6월 말에 끝난 1분기 뒤, 2025-12-31 은 3분기 말 날이다.
    assert dv._quarter_label(_d("2025-08-14"), end_month=3) == "Q1"
    assert dv._quarter_label(_d("2025-12-31"), end_month=3) == "Q3"
    assert dv._quarter_label(_d("2025-09-30"), end_month=3) == "Q2 (중간)"
    # 결산월을 모르면(None) 12월 결산으로 본다.
    assert dv._quarter_label(_d("2025-05-12"), end_month=None) == "Q1"


KB = [
    _d("2025-05-12", dps=912, rcept="20250424800496", rcept_dt="20250424"),
    _d("2025-08-11", dps=920, rcept="20250724800397", rcept_dt="20250724"),
    _d("2025-11-14", dps=930, rcept="20251030800487", rcept_dt="20251030"),
    _d("2026-02-27", dtype="결산배당", dps=1605, rcept="20260205800608", rcept_dt="20260205"),
]


def test_kb_financial_2025_decision_table_labels_match_the_cumulative_table():
    rows = [r for r in dv._quarterly_breakdown(KB, [2025]) if r["year"] == 2025]
    assert [(r["quarter"], r["dps_common_krw"]) for r in rows] == [("Q1", 912), ("Q2 (중간)", 920), ("Q3", 930), ("결산", 1605)]


def test_relabelling_does_not_change_how_many_decisions_are_counted():
    # 같은 결의 묶기 키는 (사업연도, 분기, 기준일) — 기준일이 키에 들어 있어 라벨이 바뀌어도 건수가 같다. 같은 기준일 정정은 여전히 한 건.
    amended = {**KB[0], "rcept_no": "20250425800001", "rcept_dt": "20250425", "report_name": "[기재정정]현금ㆍ현물배당결정"}
    assert len(dv._effective_decisions(KB)) == 4
    assert [d["rcept_no"] for d in dv._effective_decisions([*KB, amended]) if d["record_date"] == "2025-05-12"] == ["20250425800001"]
    assert len(dv._effective_decisions([*KB, amended])) == 4


# ── 누적차분 표 ─────────────────────────────────────────────────────────────────────────────
def _alot(dps, total_mil):
    return {"list": [
        {"se": "주당액면가액(원)", "stock_knd": "", "thstrm": "5,000", "stlm_dt": "2025-12-31"},
        {"se": "주당 현금배당금(원)", "stock_knd": "보통주", "thstrm": f"{dps:,}", "stlm_dt": "2025-12-31"},
        {"se": "현금배당금총액(백만원)", "stock_knd": "", "thstrm": f"{total_mil:,}", "stlm_dt": "2025-12-31"},
    ]}


class _Client:
    """KB금융 2025 — 반기보고서(11012)에 배당 행이 없다(div_quarterly status=보고서없음)."""

    def __init__(self, by_code):
        self.by_code = by_code

    async def get_dividend_info(self, corp_code, year, reprt_code):
        if reprt_code not in self.by_code:
            raise dv.DartClientError("013", "조회된 데이터가 없습니다")
        return _alot(*self.by_code[reprt_code])


def _full(monkeypatch, by_code):
    monkeypatch.setattr(dv, "get_dart_client", lambda: _Client(by_code))
    return asyncio.run(dv._quarterly_full_from_cumulative("00688996", 2025))


def test_missing_half_year_report_makes_the_difference_a_two_quarter_sum_not_a_quarter(monkeypatch):
    rows = _full(monkeypatch, {"11013": (912, 335_000), "11014": (2762, 1_000_000), "11011": (4367, 1_500_000)})
    assert [(r["quarter"], r["dps_common"]) for r in rows] == [
        ("Q1", 912), ("Q2+Q3 합산(반기보고서 없음)", 1850), ("결산", 1605)]
    assert sum(r["dps_common"] for r in rows) == 4367, "연 합은 그대로"
    merged = rows[1]
    assert merged["merged"] is True and merged["span"] == ["Q2", "Q3"] and merged["missing_reports"] == ["반기보고서"]
    assert not rows[0].get("merged") and not rows[2].get("merged")


def test_complete_reports_keep_the_plain_quarter_labels(monkeypatch):
    rows = _full(monkeypatch, {"11013": (100, 10), "11012": (200, 20), "11014": (300, 30), "11011": (500, 50)})
    assert [(r["quarter"], r["dps_common"]) for r in rows] == [("Q1", 100), ("Q2", 100), ("Q3", 100), ("결산", 200)]
    assert not any(r.get("merged") for r in rows)


def test_missing_first_quarter_report_is_marked_too(monkeypatch):
    rows = _full(monkeypatch, {"11012": (200, 20), "11014": (300, 30), "11011": (500, 50)})
    assert [r["quarter"] for r in rows] == ["Q1+Q2 합산(1분기보고서 없음)", "Q3", "결산"]


# ── 렌더 ────────────────────────────────────────────────────────────────────────────────────
def test_render_explains_the_merged_row_with_the_matching_decisions():
    from open_proxy_mcp.tools import dividend_disclosure as tool
    full = [{"quarter": "Q1", "dps_common": 912, "dps_preferred": 0, "total_mil": 335_000},
            {"quarter": "Q2+Q3 합산(반기보고서 없음)", "dps_common": 1850, "dps_preferred": 0, "total_mil": 700_000,
             "merged": True, "span": ["Q2", "Q3"], "missing_reports": ["반기보고서"]},
            {"quarter": "결산", "dps_common": 1605, "dps_preferred": 0, "total_mil": 575_000}]
    qb = [{"year": 2025, "quarter": q, "dps_common_krw": d, "dps_preferred_krw": 0, "record_date": rd, "rcept_no": "x",
           "is_superseded": False, "is_cancelled": False, "is_amendment": False}
          for q, d, rd in (("Q1", 912, "2025-05-12"), ("Q2 (중간)", 920, "2025-08-11"), ("Q3", 930, "2025-11-14"))]
    lines = tool._quarterly_full_lines(full, qb, 2025)
    text = "\n".join(lines)
    assert "Q2+Q3 합산(반기보고서 없음)" in text and "1,850원" in text
    assert "Q2 920원 + Q3 930원" in text and "= 1,850원 (일치)" in text


# ── 있는 보고서(누적 0)와 없는 보고서(013)를 가른다 — 후속 수정 (DATA-DIV8 / 조사 N9) ─────────────────────
def _alot_dashes():
    """기업은행 2025 1분기·3분기 꼴: DART 가 000(정상)으로 행을 주지만 값 칸이 전부 「-」(보고서는 있고 그 시점 누적 배당이 없음)."""
    return {"list": [
        {"rcept_no": "20250530002315", "se": se, "stock_knd": "-", "thstrm": "-", "frmtrm": "-", "lwfr": "-", "stlm_dt": "2025-03-31"}
        for se in ("주당액면가액(원)", "(연결)당기순이익(백만원)", "(별도)당기순이익(백만원)", "현금배당금총액(백만원)")]}


class _ClientDash(_Client):
    """by_code 에 값이 있으면 그 누적, `DASH` 면 값 칸이 전부 「-」인 보고서, 없으면 013(보고서 없음)."""

    async def get_dividend_info(self, corp_code, year, reprt_code):
        if self.by_code.get(reprt_code) == "DASH":
            return _alot_dashes()
        return await super().get_dividend_info(corp_code, year, reprt_code)


def _full_dash(monkeypatch, by_code):
    monkeypatch.setattr(dv, "get_dart_client", lambda: _ClientDash(by_code))
    return asyncio.run(dv._quarterly_full_from_cumulative("00149646", 2025))


def test_a_report_with_all_dash_values_is_a_present_report_with_zero_cumulative_not_a_missing_report(monkeypatch):
    # 기업은행(024110) 2025: 1분기·3분기 보고서는 있고(값 「-」), 반기는 누적 0, 사업보고서에 연 1,048원.
    rows = _full_dash(monkeypatch, {"11013": "DASH", "11012": (0, 0), "11014": "DASH", "11011": (1048, 835_702)})
    assert [(r["quarter"], r["dps_common"]) for r in rows] == [("Q1", 0), ("Q2", 0), ("Q3", 0), ("결산", 1048)]
    assert not any(r.get("merged") for r in rows)
    assert not any("합산" in r["quarter"] for r in rows)


def test_a_report_the_source_does_not_have_is_still_a_missing_report_even_when_a_neighbour_has_dashes(monkeypatch):
    # 롯데리츠형(1분기·3분기 013 = 보고서 없음)은 그대로 합산 — 없는 보고서를 있는 것으로 꾸미지 않는다.
    rows = _full_dash(monkeypatch, {"11012": (0, 0), "11011": (123, 35_543)})
    assert [r["quarter"] for r in rows] == ["Q1+Q2 합산(1분기보고서 없음)", "Q3+결산 합산(3분기보고서 없음)"]
    assert [r["dps_common"] for r in rows] == [0, 123]
