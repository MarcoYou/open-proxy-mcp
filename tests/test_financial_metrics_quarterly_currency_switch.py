"""분기 추이 — 보고 통화를 바꾼 회사는 분기마다 **그 보고서의 통화**로 환산한다. DART 콜 0.

글로벌에스엠(900070) 실측(261019): 2023 보고서는 CNY, 2024~ 보고서는 USD. 예전 코드는 첫 보고서(FY-3 Q1)의
통화 하나(CNY)를 12분기 전부에 써서 USD 금액을 CNY 환율(≈227원)로 바꿨다 → 2026 반기 자본 227억(실제 약 1,544억,
약 6.8배 과소). 이 시험은 그 꼴을 작은 수로 다시 만든다.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import open_proxy_mcp.services.financial_metrics as fm  # noqa: E402
from open_proxy_mcp.dart import as_of  # noqa: E402

_RC_END = {"11013": "03.31", "11012": "06.30", "11014": "09.30", "11011": "12.31"}
# (당기, 누적) — 연간 보고서는 (연간, 연간)
_REV = {"11013": (100, 100), "11012": (100, 200), "11014": (100, 300), "11011": (420, 420)}
_CUR = {2022: "CNY", 2023: "CNY", 2024: "USD", 2025: "USD", 2026: "USD"}
_RATES = {"CNY": 200.0, "USD": 1400.0}


def _rows(year: int, rc: str, cur: str) -> list[dict]:
    r, c = _REV[rc]
    dt = f"{year}.{_RC_END[rc]}"
    common = {"fs_div": "CFS", "currency": cur, "thstrm_dt": dt}
    return [
        {**common, "sj_div": "BS", "account_nm": "자산총계", "account_id": "ifrs-full_Assets",
         "thstrm_amount": "1000", "ord": "1"},
        {**common, "sj_div": "BS", "account_nm": "자본총계", "thstrm_amount": "600", "ord": "2"},
        {**common, "sj_div": "IS", "account_nm": "매출액", "thstrm_amount": str(r),
         "thstrm_add_amount": str(c), "ord": "3"},
        {**common, "sj_div": "IS", "account_nm": "영업이익", "thstrm_amount": str(r // 10),
         "thstrm_add_amount": str(c // 10), "ord": "4"},
    ]


def _run(monkeypatch, end_year: int, last_year: int, cur_by_year=None, rates=None, rc_cur=None, no_add=()):
    cur_by_year = cur_by_year or _CUR
    rates = _RATES if rates is None else rates
    fx_calls: list[tuple[str, str]] = []

    async def fake_acnt(corp_code, year, reprt_code, fs_div):
        # 마지막 해는 반기까지만 나왔다(조회 시점이 그 해 3분기 전)
        if year > last_year or (year == last_year and reprt_code in ("11014", "11011")):
            return [], "no_filing"
        cur = (rc_cur or {}).get((year, reprt_code)) or cur_by_year[year]
        rows = _rows(year, reprt_code, cur)
        if (year, reprt_code) in no_add:           # 누적 칸이 없는 3분기 보고서 — Q4 차분은 Q1+Q2+Q3 합으로
            rows = [{k: v for k, v in r.items() if k != "thstrm_add_amount"} for r in rows]
        return rows, None

    async def fake_acnt_all(corp_code, year, reprt_code, fs_div):
        return [], "no_filing"

    async def fake_fx(cur, date=None):
        fx_calls.append((cur, date))
        if cur == "KRW":
            return 1.0
        return rates.get(cur)

    monkeypatch.setattr(fm, "_safe_fetch_acnt", fake_acnt)
    monkeypatch.setattr(fm, "_safe_fetch_acnt_all", fake_acnt_all)
    monkeypatch.setattr(fm, "fx_to_krw", fake_fx)
    rows, warnings = asyncio.run(fm._build_quarterly("00783246", end_year, "CFS", fiscal_month=12))
    return rows, warnings, fx_calls


def test_window_all_in_new_currency_uses_new_currency_rate(monkeypatch):
    # 2026 조회 → 표시 창 2024~2026 은 전부 USD. 창 밖 2023(CNY)은 차분 재료로만 읽고 버린다.
    rows, warnings, fx_calls = _run(monkeypatch, end_year=2026, last_year=2026)
    assert rows and all(r["functional_currency"] == "USD" for r in rows)
    assert all(r["fx_rate_to_krw"] == 1400.0 for r in rows)
    assert rows[-1]["total_equity_krw"] == 600 * 1400          # 예전: 600 × 200(CNY 환율) — 7배 과소
    assert fx_calls == [("USD", "20260630")]
    assert all("_cur" not in r for r in rows)
    assert any("기능통화 USD" in w for w in warnings)
    assert not any("CNY" in w for w in warnings)


def test_currency_switch_inside_window_converts_each_quarter_with_its_own_currency(monkeypatch):
    # 2025 조회 → 창 2023(CNY)·2024~2025(USD). 같은 날(마지막 분기말) 환율로 통화마다 환산.
    rows, warnings, fx_calls = _run(monkeypatch, end_year=2025, last_year=2025)
    by = {(r["fiscal_year"], r["fiscal_quarter"]): r for r in rows}
    assert by[(2023, "Q1")]["functional_currency"] == "CNY"
    assert by[(2023, "Q1")]["fx_rate_to_krw"] == 200.0
    assert by[(2023, "Q1")]["total_equity_krw"] == 600 * 200
    assert by[(2024, "Q1")]["functional_currency"] == "USD"
    assert by[(2024, "Q1")]["total_equity_krw"] == 600 * 1400
    # Q4 차분은 그 해 통화 안에서 — 420 − 300 = 120
    assert by[(2023, "Q4")]["revenue_krw"] == 120 * 200
    assert by[(2024, "Q4")]["revenue_krw"] == 120 * 1400
    assert by[(2024, "Q4")]["basis"] == "standalone_3m_derived"
    # 통화가 바뀐 경계의 증감률도 원화끼리 — 2024Q1(100 USD) vs 2023Q4(120 CNY)
    assert by[(2024, "Q1")]["qoq_pct"]["revenue"] == round((100 * 1400 - 120 * 200) / (120 * 200) * 100, 2)
    assert by[(2024, "Q1")]["yoy_pct"]["revenue"] == round((100 * 1400 - 100 * 200) / (100 * 200) * 100, 2)
    assert sorted(c for c, _ in fx_calls) == ["CNY", "USD"]
    assert {d for _, d in fx_calls} == {"20250630"}             # 둘 다 같은 날
    assert any("보고 통화가 기간 중 바뀌었다" in w and "CNY 2023Q1~2023Q4" in w and "USD 2024Q1~2025Q2" in w
               for w in warnings)
    assert all("_cur" not in r for r in rows)


def test_currency_switch_mid_year_differences_in_krw(monkeypatch):
    # 한 해 안에서 바뀌면(2024 Q1~Q3 CNY, 연간 USD) Q4 = 연간 − 9개월 누적을 **원화끼리** 뺀다.
    rc_cur = {(2024, rc): "CNY" for rc in ("11013", "11012", "11014")}
    rows, _, _ = _run(monkeypatch, end_year=2024, last_year=2025,
                      cur_by_year={2021: "CNY", 2022: "CNY", 2023: "CNY", 2024: "USD"}, rc_cur=rc_cur)
    by = {(r["fiscal_year"], r["fiscal_quarter"]): r for r in rows}
    assert by[(2024, "Q4")]["functional_currency"] == "USD"
    assert by[(2024, "Q4")]["revenue_krw"] == 420 * 1400 - 300 * 200


def test_currency_switch_with_failed_rate_does_not_mix_units(monkeypatch):
    # 환율 하나를 못 구하면 원화로 못 바꾼다 — 통화가 다른 분기 사이 금액 증감률은 비우고 경고한다.
    rows, warnings, _ = _run(monkeypatch, end_year=2025, last_year=2025, rates={"USD": 1400.0})
    by = {(r["fiscal_year"], r["fiscal_quarter"]): r for r in rows}
    assert by[(2024, "Q1")]["functional_currency"] == "USD"
    assert by[(2024, "Q1")]["fx_rate_to_krw"] is None
    assert by[(2024, "Q1")]["total_equity_krw"] == 600                # 원 단위가 아니라 USD 그대로(경고)
    assert by[(2024, "Q1")]["qoq_pct"]["revenue"] is None             # CNY 2023Q4 와 비교 안 함
    assert by[(2024, "Q1")]["yoy_pct"]["revenue"] is None
    assert by[(2024, "Q2")]["qoq_pct"]["revenue"] == 0.0              # 같은 USD 끼리는 그대로
    assert by[(2024, "Q1")]["qoq_pct"]["operating_margin_pp"] == 0.0  # 비율은 통화와 무관
    assert any("환율 조회 실패" in w and "보고 통화가 기간 중 바뀌었는데" in w for w in warnings)


def test_currency_switch_strict_as_of_excludes_foreign_amounts(monkeypatch):
    tokens = as_of.set_strict_as_of("2026-01-31T09:00:00+09:00")
    try:
        rows, warnings, _ = _run(monkeypatch, end_year=2025, last_year=2025, rates={})
    finally:
        as_of.reset_strict_as_of(tokens)
    assert all(r["total_equity_krw"] is None for r in rows)
    assert all(r["operating_margin_pct"] == 10.0 for r in rows if r["fiscal_quarter"] != "Q4")
    assert any("외화 분기의 원화 금액을 제외" in w for w in warnings)


def test_single_currency_company_unchanged(monkeypatch):
    # 통화를 안 바꾼 회사(전부 USD) — 예전 경로 그대로: 마지막 분기말 단일 환율, 행마다 같은 환율.
    rows, warnings, fx_calls = _run(monkeypatch, end_year=2025, last_year=2025,
                                    cur_by_year={y: "USD" for y in range(2022, 2026)})
    assert fx_calls == [("USD", "20250630")]
    assert all(r["functional_currency"] == "USD" and r["fx_rate_to_krw"] == 1400.0 for r in rows)
    by = {(r["fiscal_year"], r["fiscal_quarter"]): r for r in rows}
    assert by[(2024, "Q4")]["revenue_krw"] == 120 * 1400
    assert any("12분기 단일환율" in w for w in warnings)


def test_currency_switch_mid_year_without_q3_cumulative_sums_quarters_in_krw(monkeypatch):
    # 3분기 보고서에 누적 칸이 없으면 9개월 = Q1+Q2+Q3 단독 합 — 그 셋(CNY)도 원화로 바꾼 뒤 연간(USD, 원화)에서 뺀다.
    rc_cur = {(2024, rc): "CNY" for rc in ("11013", "11012", "11014")}
    rows, _, _ = _run(monkeypatch, end_year=2024, last_year=2025,
                      cur_by_year={2021: "CNY", 2022: "CNY", 2023: "CNY", 2024: "USD"}, rc_cur=rc_cur,
                      no_add={(2024, "11014")})
    by = {(r["fiscal_year"], r["fiscal_quarter"]): r for r in rows}
    assert by[(2024, "Q4")]["basis"] == "standalone_3m_derived"
    assert by[(2024, "Q4")]["revenue_krw"] == 420 * 1400 - 300 * 200


def test_currency_switch_mid_year_with_failed_rate_keeps_the_annual_figure(monkeypatch):
    # 한 해 안에서 통화가 바뀌었는데(Q1~Q3 CNY · 연간 USD) CNY 환율을 못 구했다 — USD 연간에서 CNY 9개월을 빼지 않는다.
    rc_cur = {(2024, rc): "CNY" for rc in ("11013", "11012", "11014")}
    rows, warnings, _ = _run(monkeypatch, end_year=2024, last_year=2025, rates={"USD": 1400.0},
                             cur_by_year={2021: "CNY", 2022: "CNY", 2023: "CNY", 2024: "USD"}, rc_cur=rc_cur)
    by = {(r["fiscal_year"], r["fiscal_quarter"]): r for r in rows}
    assert by[(2024, "Q4")]["basis"] == "annual_cumulative"
    assert by[(2024, "Q4")]["revenue_krw"] == 420                     # USD 연간 그대로(경고) — 420 − 300 이 아니다
    assert any("2024-Q4" in w and "그 해 안에서 보고 통화가 바뀌었는데 환율을 못 구해" in w for w in warnings)
    assert not any(w.startswith("⚠ 2024년 분기 합이 연간과 차이") for w in warnings)   # 통화가 다른 분기를 더하지 않는다
