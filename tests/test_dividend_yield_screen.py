# -*- coding: utf-8 -*-
"""dividend_data screen — 배당수익률 거르기(`min_yield`)와 정렬(`sort="yield"`) (2026-10-22 사용자 유형 시험의 빈틈).

운영 재호출: screen 에 수익률 축이 없어 「코스피 배당수익률 상위」에 답하지 못했다 — 표는 배당총액 큰 순이고 수익률 칸이 없었다.

정의(이 시험이 지키는 것)
  · 분자 = screen 이 고른 사업연도 **보통주** 연 DPS(결산기 합 규칙 그대로; 우선주 DPS 는 쓰지 않는다), 분모 = 같은 회사 보통주 코드의 `price_latest` 종가.
  · 거르기·정렬은 limit 으로 자르기 **전에** 그 해 모집단 전체에 건다 — 총액 순위로는 limit 밖인 고수익률 종목이 `sort=yield` 위쪽에 나온다.
  · 주가가 없는 종목은 수익률 미상 — `min_yield` 에서 빠지고 그 수를 센다. 서빙 기준일보다 오래된 주가는 없는 것으로 본다.
  · 비운 호출(`sort`·`min_yield` 없음)은 종전과 같은 순서이고 수익률 칸만 덧붙는다.
  · 결산 뒤 주식 수를 바꾸는 이벤트(병합·분할)가 있는 종목은 수익률을 못 믿으므로 표시한다(`share_event`, 값은 안 바꾼다).
실제 Postgres 17 (Docker 가 없으면 SQL 쪽은 건너뜀).
"""
from __future__ import annotations

import asyncio

import pytest

from open_proxy_mcp.services import dividend_data as dd
from open_proxy_mcp.tools import dividend_data as dd_tool

psycopg = pytest.importorskip("psycopg")

DDL = """
CREATE TABLE div_declared(
  corp_code text, name text, tickers text, bsns_year int, reprt_code text, reprt_label text, stock_knd_raw text, stock_kind text,
  dps_krw double precision, div_total_krw double precision, unit_raw text, payout_pct double precision, rcept_no text, stlm_dt text,
  se_raw text, status text, line_no smallint);
CREATE TABLE div_payment(corp_code text, fiscal_year int, board_date text);
CREATE TABLE wise_sector(snap_dd text, ticker text, sector text);
CREATE TABLE div_payment_scope(market text, fiscal_year int, is_complete boolean);
CREATE TABLE price_latest(ticker text, close bigint, price_dd text);
CREATE TABLE krx_adj_events(ticker text, event_dd text, event_type text, adj_factor double precision, label_confidence text);
"""


def _row(corp, name, ticker, stlm, kind, dps, total, line=1, fy=2025):
    return (corp, name, ticker, fy, "11011", "사업보고서", "보통주식" if kind == "보통" else "우선주", kind, dps, total, "원", None,
            f"R{corp}", stlm, "주당 현금배당금(원)", "확정", line)


LEDGER = [
    # 총액 순서: A > B > C > D > ... / 수익률 순서: D(20%) > B(10%) > C(9%) > A(1%)
    _row("A", "대형저수익", "000010", "2025-12-31", "보통", 1000.0, 1000e8),
    _row("A", "대형저수익", "000010", "2025-12-31", "우선", 1100.0, 1000e8, line=2),     # 우선주 DPS 는 수익률에 안 쓴다
    _row("B", "중형고수익", "000020", "2025-12-31", "보통", 3000.0, 500e8),
    _row("C", "소형", "000030", "2025-12-31", "보통", 450.0, 400e8),
    _row("D", "소형최고수익", "000040", "2025-12-31", "보통", 2000.0, 300e8),
    _row("E", "주가없음", "000050", "2025-12-31", "보통", 700.0, 200e8),
    _row("F", "낡은주가", "000060", "2025-12-31", "보통", 800.0, 100e8),
    # 반기·분기 결산 리츠 — 사업연도 DPS 는 결산기 합(120 + 130 = 250)
    _row("R", "합리츠", "000070", "2025-06-30", "보통", 120.0, 50e8),
    _row("R", "합리츠", "000070", "2025-12-31", "보통", 130.0, 55e8),
    # 병합(역분할) 이벤트가 결산 뒤에 있었던 종목 — DPS 는 병합 전 주식 기준, 종가는 병합 뒤
    _row("G", "병합사", "000080", "2025-12-31", "보통", 100.0, 20e8),
    # 호가단위 맞춤 이벤트는 주식 수 변화가 아니다
    _row("H", "호가단위", "000090", "2025-12-31", "보통", 100.0, 10e8),
]
PRICES = [("000010", 100_000, "20261021"), ("000020", 30_000, "20261021"), ("000030", 5_000, "20261021"),
          ("000040", 10_000, "20261021"), ("000060", 4_000, "20260810"),            # F: 서빙 기준일보다 오래된 행(거래정지·상장폐지)
          ("000070", 25_000, "20261021"), ("000080", 1_250, "20261021"), ("000090", 2_000, "20261021")]
EVENTS = [("000080", "20260315", "merge", 10.0, "confirmed"),
          ("000090", "20260315", "tick_rounding", 1.0014, "excluded_tick_rounding"),
          ("000030", "20251020", "split", 0.5, "confirmed")]                       # C: 결산(2025-12-31) 앞의 이벤트 — 이미 DPS 에 반영됐다
SERVING = "20261021"


@pytest.fixture
def ledger(pg17, monkeypatch):
    def run(sql, params=()):
        with psycopg.connect(pg17, autocommit=True) as c:
            cur = c.execute(sql, params)
            return [tuple(r) for r in cur.fetchall()] if cur.description else None
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(DDL)
        with c.cursor() as cur:
            cur.executemany("INSERT INTO div_declared VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)", LEDGER)
            cur.executemany("INSERT INTO price_latest VALUES (%s,%s,%s)", PRICES)
            cur.executemany("INSERT INTO krx_adj_events VALUES (%s,%s,%s,%s,%s)", EVENTS)
            cur.execute("INSERT INTO div_payment_scope VALUES ('KOSPI', 2025, true)")
    monkeypatch.setattr(dd, "_rows", lambda sql, params=(): run(sql, params))
    return run


def _screen(year, **kw):
    """도구가 하는 순서 그대로 — `screen` 한 번 + (수익률 모드가 아니면) 수익률 붙이기 + 주식 수 변동 표시."""
    d = dd.screen(year, **kw)
    if d.get("status") == "ok":
        if not d["yield_mode"]:
            dd.attach_prices(d, kw.get("price_dd"))
        dd.flag_share_events(d, year)
    return d


def _names(d):
    return [r["corp_code"] for r in d["rows"]]


def _by(d):
    return {r["corp_code"]: r for r in d["rows"]}


def test_sort_by_yield_is_applied_to_the_whole_population_before_limit(ledger):
    by_total = _screen(2025, limit=2, price_dd=SERVING)
    assert _names(by_total) == ["A", "B"], "총액 순위 위 둘"
    by_yield = _screen(2025, limit=2, sort="yield", price_dd=SERVING)
    assert _names(by_yield) == ["D", "B"], "총액으로는 4위인 D(20%)가 수익률 순 맨 위 — limit 안에서만 다시 줄 세우면 이 결과가 나올 수 없다"
    assert by_yield["matched"] == 9 and by_yield["sort"] == "yield"
    assert by_yield["rows"][0]["yield_pct"] == pytest.approx(20.0) and by_yield["rows"][1]["yield_pct"] == pytest.approx(10.0)


def test_min_yield_filters_before_limit_and_counts_the_companies_without_a_price(ledger):
    d = _screen(2025, min_yield=9.0, limit=1, price_dd=SERVING)
    assert d["matched"] == 3 and _names(d) == ["B"], "D(20%)·B(10%)·C(9%) 셋이 걸리고 총액 순 첫 줄이 B"
    full = _screen(2025, min_yield=9.0, limit=50, price_dd=SERVING)
    assert sorted(_names(full)) == ["B", "C", "D"] and full["matched"] == 3
    assert full["n_noprice"] == 2, "주가 행이 없는 E, 서빙 기준일보다 낡은 F — 수익률 미상이라 min_yield 에서 빠진 회사 수"
    assert full["sort"] == "total", "min_yield 만 주면 정렬은 종전 배당총액 순"
    assert _names(full) == ["B", "C", "D"]


def test_min_yield_with_no_match_still_reports_the_missing_price_count(ledger):
    d = _screen(2025, min_yield=50.0, price_dd=SERVING)
    assert d["rows"] == [] and d["matched"] == 0 and d["n_noprice"] == 2 and d["n_universe"] == 9


def test_yield_uses_common_dps_and_the_latest_close_not_the_preferred_dps(ledger):
    a = _by(_screen(2025, sort="yield", price_dd=SERVING))["A"]
    assert a["dps_krw"] == 1000.0 and a["price_krw"] == 100_000 and a["price_dd"] == "20261021"
    assert a["yield_pct"] == pytest.approx(1.0), "우선주 DPS 1,100 이 섞이면 1.1"


def test_reit_yield_uses_the_year_sum_of_settlement_period_dps(ledger):
    r = _by(_screen(2025, sort="yield", price_dd=SERVING))["R"]
    assert r["dps_krw"] == 250.0 and r["n_periods"] == 2 and r["yield_pct"] == pytest.approx(250 / 25000 * 100)


def test_yield_sort_puts_unknown_last(ledger):
    d = _screen(2025, sort="yield", limit=50, price_dd=SERVING)
    assert _names(d) == ["D", "B", "C", "G", "H", "A", "R", "E", "F"], "수익률 큰 순(동률은 총액 큰 순), 수익률 미상 E·F 는 맨 뒤"
    assert all(r["yield_pct"] is None for r in d["rows"] if r["corp_code"] in {"E", "F"})


def test_stale_price_rows_older_than_the_serving_date_are_not_used(ledger):
    f = _by(_screen(2025, sort="yield", price_dd=SERVING))["F"]
    assert f["price_krw"] is None and f["yield_pct"] is None
    # 기준일을 낮추면(옛 행도 허용) F 의 수익률이 나온다 — 낡은 값을 최신처럼 쓰지 않는 것이 서빙 기준일 조건이다.
    f2 = _by(_screen(2025, sort="yield", price_dd="20260801"))["F"]
    assert f2["yield_pct"] == pytest.approx(20.0)


def test_default_call_keeps_the_total_order_and_only_adds_yield_columns(ledger):
    d = _screen(2025, limit=100, price_dd=SERVING)
    assert _names(d) == ["A", "B", "C", "D", "E", "R", "F", "G", "H"], "배당총액 큰 순(종전 그대로)"
    assert d["sort"] == "total" and d["price_failed"] is False
    rows = _by(d)
    assert rows["B"]["yield_pct"] == pytest.approx(10.0) and rows["B"]["price_dd"] == "20261021"
    assert rows["E"]["yield_pct"] is None
    # 종전 칸은 그대로
    assert rows["A"]["div_total_krw"] == 1000e8 and rows["A"]["dps_krw"] == 1000.0 and rows["R"]["total_basis"] == "sum"


def test_default_call_without_a_serving_date_says_the_price_lookup_was_not_possible(ledger):
    d = _screen(2025, limit=100)
    assert d["price_failed"] is True and all(r["yield_pct"] is None for r in d["rows"])
    assert _names(d)[:3] == ["A", "B", "C"], "수익률을 못 붙여도 거르기·순서는 종전 그대로"


def test_yield_mode_without_a_serving_date_is_refused_not_guessed(ledger):
    assert _screen(2025, sort="yield")["status"] == "price_unavailable"
    assert _screen(2025, min_yield=3.0)["status"] == "price_unavailable"


def test_invalid_sort_is_reported(ledger):
    assert _screen(2025, sort="dps", price_dd=SERVING)["status"] == "invalid_sort"


def test_share_count_events_after_the_settlement_date_are_flagged_not_adjusted(ledger):
    rows = _by(_screen(2025, sort="yield", price_dd=SERVING, limit=50))
    assert rows["G"]["share_event"] is True and rows["G"]["yield_pct"] == pytest.approx(8.0), "값은 그대로, 표시만"
    assert rows["H"]["share_event"] is False, "호가단위 맞춤은 주식 수 변화가 아니다"
    assert rows["C"]["share_event"] is False, "결산일보다 앞선 이벤트는 DPS 에 이미 반영됐다"
    assert rows["A"]["share_event"] is False


def test_default_screen_is_still_one_query_and_the_price_columns_come_from_a_separate_read(ledger, monkeypatch):
    seen = []
    real = dd._rows
    monkeypatch.setattr(dd, "_rows", lambda sql, params=(): (seen.append(" ".join(sql.split())), real(sql, params))[1])
    d = dd.screen(2025, limit=3, price_dd=SERVING)
    assert len(seen) == 1 and "price_latest" not in seen[0], "비운 호출의 screen 은 종전처럼 질의 하나 — 주가는 건드리지 않는다"
    assert all(r["yield_pct"] is None for r in d["rows"])
    dd.attach_prices(d, SERVING)
    assert len(seen) == 2 and "price_latest" in seen[1] and d["price_failed"] is False
    assert d["rows"][1]["yield_pct"] == pytest.approx(10.0)


def test_other_conditions_still_apply_together_with_min_yield(ledger):
    d = _screen(2025, min_yield=9.0, min_dps=1000, price_dd=SERVING)
    assert sorted(_names(d)) == ["B", "D"] and d["matched"] == 2


# ── 도구(렌더·인자) — DB 없이 ────────────────────────────────────────────────────────────────
class _Registry:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self, *args, **kwargs):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


def _tool():
    reg = _Registry()
    dd_tool.register_tools(reg)
    return reg.tools["dividend_data"]


def _wire(monkeypatch, *, annual=2025, serving="20261021", result=None):
    calls: dict[str, list] = {}

    def rec(name, ret):
        def fn(*a, **k):
            calls.setdefault(name, []).append((a, k))
            return ret
        return fn
    row = {"corp_code": "B", "name": "중형고수익", "ticker": "000020", "dps_krw": 3000.0, "div_total_krw": 500e8, "payout_pct": 30.0,
           "rcept_no": "R1", "n_periods": 1, "total_basis": "single", "price_krw": 30000, "price_dd": "20261021", "yield_pct": 10.0,
           "share_event": False, "last_stlm_dt": "2025-12-31"}
    res = result or {"status": "ok", "rows": [row], "matched": 1, "n_universe": 825, "returned": 1, "limit": 50, "sort": "yield",
                     "min_yield": None, "price_min_dd": serving, "n_noprice": 0, "price_failed": False, "share_events_failed": False}
    monkeypatch.setattr(dd, "year_range", rec("year_range", (2020, 2026)))
    monkeypatch.setattr(dd, "latest_annual_year", rec("latest_annual_year", annual))
    monkeypatch.setattr(dd, "screen", rec("screen", res))

    def attach(data, price_dd):
        data.setdefault("price_failed", False)
        return data

    def flag(data, year):
        data.setdefault("share_events_failed", False)
        return data
    monkeypatch.setattr(dd, "attach_prices", attach)
    monkeypatch.setattr(dd, "flag_share_events", flag)
    monkeypatch.setattr(dd, "payment_scope_years", rec("payment_scope_years", [2020, 2021, 2022, 2023, 2024]))
    monkeypatch.setattr(dd, "payment_history", rec("payment_history", {}))

    async def fake_serving():
        return serving
    from open_proxy_mcp.services import price_multiple_data as pmd
    monkeypatch.setattr(pmd, "_serving_price_dd", fake_serving)
    return calls


def test_tool_passes_the_serving_date_and_the_yield_arguments_to_the_screen(monkeypatch):
    calls = _wire(monkeypatch)
    asyncio.run(_tool()(scope="screen", min_yield=5.0, sort="yield", limit=20, format="json"))
    (a, k), = calls["screen"]
    assert a[0] == 2025 and k["min_yield"] == 5.0 and k["sort"] == "yield" and k["price_dd"] == "20261021" and k["limit"] == 20


def test_tool_without_the_new_arguments_asks_the_old_question(monkeypatch):
    calls = _wire(monkeypatch)
    asyncio.run(_tool()(scope="screen", min_payout=30, format="json"))
    (a, k), = calls["screen"]
    assert k["min_yield"] is None and k["sort"] == "" and k["price_dd"] == "20261021"


def test_tool_rejects_an_unknown_sort_with_the_choices(monkeypatch):
    calls = _wire(monkeypatch)
    out = asyncio.run(_tool()(scope="screen", sort="dps"))
    assert "status=invalid" in out and "yield" in out and "screen" not in calls


def test_tool_says_when_the_serving_price_date_cannot_be_read_in_yield_mode(monkeypatch):
    calls = _wire(monkeypatch, serving=None)
    out = asyncio.run(_tool()(scope="screen", sort="yield"))
    assert "status=db_error" in out and "screen" not in calls
    out = asyncio.run(_tool()(scope="screen"))               # 비운 호출은 수익률 없이도 답한다
    assert "screen" in calls and "배당 스크리닝" in out


def test_md_table_shows_yield_dps_year_and_price_date(monkeypatch):
    _wire(monkeypatch)
    out = asyncio.run(_tool()(scope="screen", sort="yield"))
    assert "수익률(%)" in out and "10.00" in out
    assert "FY2025" in out and "2026-10-21" in out or "20261021" in out, "DPS 사업연도와 기준 주가일"
    assert "정렬: 배당수익률 큰 순" in out
    assert "우선주 DPS" in out and "수익률에 쓰지 않" in out
    assert "과거 연도 DPS" not in out, "마지막 사업보고서 해이므로 경고 없음"


def test_md_warns_when_the_year_is_not_the_latest_annual_report_year(monkeypatch):
    _wire(monkeypatch, annual=2025)
    out = asyncio.run(_tool()(scope="screen", bsns_year=2023, sort="yield"))
    assert "과거 연도 DPS 를 최신 주가로 나눈 값" in out and "FY2023" in out


def test_md_marks_unknown_yield_flags_share_events_and_counts_dropped_companies(monkeypatch):
    base = {"corp_code": "G", "name": "병합사", "ticker": "000080", "dps_krw": 100.0, "div_total_krw": 20e8, "payout_pct": None, "rcept_no": "R2",
            "n_periods": 1, "total_basis": "single", "price_krw": 1000, "price_dd": "20261021", "yield_pct": 10.0, "share_event": True,
            "last_stlm_dt": "2025-12-31"}
    nop = {**base, "corp_code": "E", "name": "주가없음", "ticker": "000050", "price_krw": None, "price_dd": None, "yield_pct": None, "share_event": False}
    res = {"status": "ok", "rows": [base, nop], "matched": 2, "n_universe": 825, "returned": 2, "limit": 50, "sort": "total", "min_yield": 3.0,
           "price_min_dd": "20261021", "n_noprice": 4, "price_failed": False, "share_events_failed": False}
    _wire(monkeypatch, result=res)
    out = asyncio.run(_tool()(scope="screen", min_yield=3.0))
    assert "수익률 미상" in out
    assert "주가가 없어 수익률 조건에서 빠진 회사 4사" in out
    assert "‡" in out and "주식 수" in out, "결산 뒤 병합·분할 이벤트가 있는 종목 표시"
    assert "배당수익률 ≥ 3" in out, "조건 줄"


def test_md_says_when_prices_could_not_be_read(monkeypatch):
    row = {"corp_code": "B", "name": "중형고수익", "ticker": "000020", "dps_krw": 3000.0, "div_total_krw": 500e8, "payout_pct": 30.0,
           "rcept_no": "R1", "n_periods": 1, "total_basis": "single", "price_krw": None, "price_dd": None, "yield_pct": None,
           "share_event": False, "last_stlm_dt": "2025-12-31"}
    res = {"status": "ok", "rows": [row], "matched": 1, "n_universe": 825, "returned": 1, "limit": 50, "sort": "total", "min_yield": None,
           "price_min_dd": "20261021", "n_noprice": 0, "price_failed": True, "share_events_failed": False}
    _wire(monkeypatch, result=res)
    out = asyncio.run(_tool()(scope="screen"))
    assert "종가를 읽지 못했다" in out and "모른다" in out
