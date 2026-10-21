"""financial_metrics 분기 행의 지배 순이익 새 키 `net_income_controlling_krw`(DATA-NI2 값 고침) — 운영 `dart_finstat_q.ni_cum`(수집이 같은 controlling_ni 규칙으로 저장한
누적 지배 순이익)의 차분으로 3개월 값을 만든다. DART 콜은 늘지 않는다(DB 1회). 옛 키 `net_income_krw`(총계)·`net_income_basis` 는 그대로. network 0 · DB 가짜.
"""
from __future__ import annotations

import asyncio

from open_proxy_mcp import db
from open_proxy_mcp.services import financial_metrics as FM
from open_proxy_mcp.tools.financial_metrics import _render_quarterly


def _rows(end: str, ni_q: str, ni_add: str, fs: str = "CFS") -> list[dict]:
    base = {"fs_div": fs, "thstrm_dt": end, "ord": "1"}
    return [
        {**base, "sj_div": "BS", "account_nm": "자산총계", "thstrm_amount": "1000000000000"},
        {**base, "sj_div": "BS", "account_nm": "자본총계", "thstrm_amount": "500000000000"},
        {**base, "sj_div": "IS", "account_nm": "매출액", "thstrm_amount": "100000000000", "thstrm_add_amount": "300000000000"},
        {**base, "sj_div": "IS", "account_nm": "영업이익(손실)", "thstrm_amount": "-1000000000", "thstrm_add_amount": "-2000000000"},
        {**base, "sj_div": "IS", "account_nm": "당기순이익(손실)", "thstrm_amount": ni_q, "thstrm_add_amount": ni_add},
    ]


def _run(monkeypatch, reports: dict, db_rows, stock_code="000230", fiscal_month=12, end_year=2023):
    async def fake_acnt(corp_code, year, reprt_code, fs_div):
        r = reports.get((year, reprt_code))
        return (r, None) if r else ([], "no_filing")

    calls = []

    def fake_pg(sql, params=()):
        calls.append((sql, params))
        return db_rows

    monkeypatch.setattr(FM, "_safe_fetch_acnt", fake_acnt)
    monkeypatch.setattr(db, "pg_rows", fake_pg)
    out, ws = asyncio.run(FM._build_quarterly("00000000", end_year, "CFS", fiscal_month=fiscal_month, stock_code=stock_code))
    return out, ws, calls


def test_000230_2023_q3_controlling_is_the_db_cumulative_difference(monkeypatch):
    """000230 2023 3분기: 총계 −574.41억(net_income_krw 그대로) · 지배는 누적 차분 −255.5억(반기 누적 −23.5억 → 3분기 누적 −279.0억)."""
    reports = {(2023, "11014"): _rows("2023.09.30", "-57441304690", "-107771897498")}
    db_rows = [(2023, 2, "CFS", -2_350_000_000), (2023, 3, "CFS", -27_900_000_000)]
    out, ws, calls = _run(monkeypatch, reports, db_rows)
    q3 = next(r for r in out if r["fiscal_quarter"] == "Q3")
    assert q3["net_income_krw"] == -57441304690 and q3["net_income_basis"] == "total_incl_nci"
    assert q3["net_income_controlling_krw"] == -25_550_000_000
    assert q3["net_income_controlling_source"] == "db_ni_cum_diff"
    assert len(calls) == 1 and calls[0][1] == ("000230",)                  # DB 한 번
    md = "\n".join(_render_quarterly({"quarterly": out}))
    assert "순이익(지배)" in md


def test_q1_is_the_cumulative_itself_and_missing_previous_cell_is_none(monkeypatch):
    reports = {(2023, "11013"): _rows("2023.03.31", "-100", "-100"), (2023, "11014"): _rows("2023.09.30", "-300", "-900")}
    db_rows = [(2023, 1, "CFS", -5_000_000_000), (2023, 3, "CFS", -27_900_000_000)]      # 반기 칸 없음
    out, ws, _ = _run(monkeypatch, reports, db_rows)
    by = {r["fiscal_quarter"]: r for r in out}
    assert by["Q1"]["net_income_controlling_krw"] == -5_000_000_000
    assert by["Q3"]["net_income_controlling_krw"] is None                    # 직전 누적을 모르면 비운다(추정하지 않는다)
    assert by["Q3"]["net_income_controlling_source"] is None


def test_different_statement_basis_between_db_cells_is_not_subtracted(monkeypatch):
    """DB 의 반기 칸이 별도(OFS)·3분기 칸이 연결(CFS)이면 차분하지 않는다 — 기준이 섞이면 뜻이 없다(DATA-Q5)."""
    reports = {(2023, "11014"): _rows("2023.09.30", "-3", "-9")}
    db_rows = [(2023, 2, "OFS", -2_350_000_000), (2023, 3, "CFS", -27_900_000_000)]
    out, _ws, _ = _run(monkeypatch, reports, db_rows)
    assert next(r for r in out if r["fiscal_quarter"] == "Q3")["net_income_controlling_krw"] is None


def test_db_cell_with_other_statement_basis_than_the_report_is_not_used(monkeypatch):
    reports = {(2023, "11013"): _rows("2023.03.31", "-1", "-1", fs="CFS")}
    out, _ws, _ = _run(monkeypatch, reports, [(2023, 1, "OFS", -5)])
    assert out[0]["net_income_controlling_krw"] is None


def test_q4_uses_annual_cumulative_minus_q3_cumulative(monkeypatch):
    reports = {(2023, "11011"): _rows("2023.12.31", "-5", "-5")}
    db_rows = [(2023, 3, "CFS", -27_900_000_000), (2023, 4, "CFS", -40_000_000_000)]
    out, _ws, _ = _run(monkeypatch, reports, db_rows)
    q4 = next(r for r in out if r["fiscal_quarter"] == "Q4")
    assert q4["net_income_controlling_krw"] == -12_100_000_000


def test_non_december_fiscal_year_uses_period_end_calendar_year_for_the_db_key(monkeypatch):
    """3월 결산: 사업연도 2024 의 1분기 보고서 기간 말은 2023-06-30 — DART 표기 (fy=2023, 1). 반기는 (2023, 2)."""
    reports = {(2023, "11013"): _rows("2023.06.30", "-1", "-1"), (2023, "11012"): _rows("2023.09.30", "-2", "-3")}
    db_rows = [(2023, 1, "CFS", 1_000), (2023, 2, "CFS", 4_000)]
    out, _ws, _ = _run(monkeypatch, reports, db_rows, fiscal_month=3, end_year=2024)
    by = {r["fiscal_quarter"]: r for r in out}
    assert by["Q1"]["net_income_controlling_krw"] == 1_000 and by["Q2"]["net_income_controlling_krw"] == 3_000


def test_no_db_or_no_ticker_leaves_the_key_empty_and_old_keys_untouched(monkeypatch):
    reports = {(2023, "11013"): _rows("2023.03.31", "-1", "-1")}
    out, ws, _ = _run(monkeypatch, reports, None)                           # DB 미설정·장애 (pg_rows → None)
    assert out[0]["net_income_controlling_krw"] is None and out[0]["net_income_krw"] == -1
    assert any("지배 순이익" in w and "DB" in w for w in ws)
    out2, ws2, calls2 = _run(monkeypatch, reports, [(2023, 1, "CFS", 5)], stock_code=None)
    assert out2[0]["net_income_controlling_krw"] is None and calls2 == [] and not any("지배 순이익" in w for w in ws2)
    md = "\n".join(_render_quarterly({"quarterly": out}))
    assert "순이익(지배)" not in md                                          # 값이 하나도 없으면 열을 만들지 않는다
