"""dividend_data — 원장이 매주 갱신되며 올해 분기 행이 먼저 들어와도 screen 기본 연도가 비지 않는다(2026-10-19).

사고 모양: 원장(`div_declared`)에 FY2026 1분기·반기 행이 들어오면 `year_range()` 끝이 2026 이 된다. 종전 screen 은
`bsns_year or hi` 로 그 해를 기본으로 잡아 사업보고서(`11011`) 행이 없는 해를 걸렀다 — 연도 없이 부른 스크리닝이 **빈 결과**.
기본 연도는 사업보고서가 있는 마지막 해(`latest_annual_year()`)여야 하고, 사업보고서가 아직 없는 해를 직접 고르면
「없다」가 아니라 「아직 사업보고서가 없다」로 답해야 한다. DB 0콜 — `dd.*` 를 바꿔 끼운다.
"""
from __future__ import annotations

import asyncio
import json

from open_proxy_mcp.services import dividend_data as dd
from open_proxy_mcp.services import price_multiple_data as pmd
from open_proxy_mcp.tools import dividend_data as dd_tool


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


def _wire(monkeypatch, year_range=(2020, 2026), annual=2025):
    calls: dict[str, list] = {}

    def rec(name, ret):
        def fn(*a, **k):
            calls.setdefault(name, []).append((a, k))
            return ret
        return fn
    monkeypatch.setattr(dd, "year_range", rec("year_range", year_range))
    monkeypatch.setattr(dd, "latest_annual_year", rec("latest_annual_year", annual))
    monkeypatch.setattr(dd, "screen", rec("screen", {"status": "ok", "rows": [], "matched": 0, "n_universe": 825,
                                                     "returned": 0, "limit": 20}))
    monkeypatch.setattr(dd, "payment_scope_years", rec("payment_scope_years", [2020, 2021, 2022, 2023, 2024]))
    monkeypatch.setattr(dd, "payment_history", rec("payment_history", {}))
    monkeypatch.setattr(dd, "firm_history", rec("firm_history", {"status": "ok", "annual": [], "quarterly": [],
                                                                 "annual_failed": False, "quarterly_failed": False,
                                                                 "empty_kind_rows_folded": 0}))
    monkeypatch.setattr(dd, "payment_counts", rec("payment_counts", {"status": "ok", "complete_years": [2024],
                                                                     "rows": [], "decisions": []}))
    monkeypatch.setattr(dd, "aggregate_history", rec("aggregate_history", {"status": "ok", "rows": []}))
    return calls


def test_screen_without_a_year_uses_the_latest_year_that_has_annual_reports(monkeypatch):
    calls = _wire(monkeypatch)
    out = asyncio.run(_tool()(scope="screen", min_payout=30, limit=20, format="json"))
    assert calls["screen"][0][0][0] == 2025, "분기 행만 있는 FY2026 이 아니라 사업보고서가 있는 FY2025 를 걸러야 한다"
    assert json.loads(out)["bsns_year"] == 2025


def test_screen_on_a_year_without_annual_reports_says_so_instead_of_returning_nothing(monkeypatch):
    calls = _wire(monkeypatch)
    out = asyncio.run(_tool()(scope="screen", bsns_year=2026, format="md"))
    assert "screen" not in calls, "사업보고서가 없는 해를 거르면 「조건에 맞는 회사가 없다」로 읽힌다"
    assert "FY2026 사업보고서 아직 없음" in out and "FY2020~FY2025" in out and "status=invalid" in out


def test_screen_range_check_is_unchanged_outside_the_table(monkeypatch):
    _wire(monkeypatch)
    out = asyncio.run(_tool()(scope="screen", bsns_year=2019, format="md"))
    assert "사업연도 범위 밖" in out and "FY2020~FY2026" in out


def test_screen_reports_a_failed_annual_year_query_as_db_error(monkeypatch):
    calls = _wire(monkeypatch, annual=None)
    out = asyncio.run(_tool()(scope="screen", format="md"))
    assert "status=db_error" in out and "screen" not in calls


def test_when_annual_reports_catch_up_the_default_moves_with_them(monkeypatch):
    """이듬해 3월 사업보고서가 들어오면 기본 연도가 그 해로 옮겨 간다 — 상수가 아니다."""
    calls = _wire(monkeypatch, year_range=(2020, 2026), annual=2026)
    asyncio.run(_tool()(scope="screen"))
    assert calls["screen"][0][0][0] == 2026


def test_firm_default_range_reaches_this_years_quarterly_rows(monkeypatch):
    """firm 은 분기 표를 내므로 끝을 모든 보고서 기준(`hi`)으로 둔다 — 올해 1분기·반기가 기본으로 보인다."""
    calls = _wire(monkeypatch)

    async def fake_resolve(q):
        return {"corp_name": "KB금융", "stock_code": "105560", "corp_code": "00688996"}, None
    monkeypatch.setattr(pmd, "_resolve_listed", fake_resolve)
    asyncio.run(_tool()(scope="firm", company="KB금융"))
    assert calls["firm_history"][0][0] == ("00688996", 2020, 2026)
    assert calls["payment_counts"][0][0] == ("00688996", 2020, 2026)
    assert "latest_annual_year" not in calls, "firm 은 연간 기본 연도 질의가 필요 없다"


def test_market_and_sector_do_not_need_the_annual_year_query(monkeypatch):
    calls = _wire(monkeypatch)
    asyncio.run(_tool()(scope="market"))
    asyncio.run(_tool()(scope="sector", sector="금융"))
    assert "latest_annual_year" not in calls
    assert all(c[0][2:] == (2020, 2026) for c in calls["aggregate_history"]), "집계는 사업보고서 행만 세므로 끝이 2026 이어도 같다"


def test_latest_annual_year_reads_only_annual_report_rows(monkeypatch):
    seen = []

    def rows(sql, params=()):
        seen.append((" ".join(sql.split()), params))
        return [(2025,)]
    monkeypatch.setattr(dd, "_rows", rows)
    assert dd.latest_annual_year() == 2025
    assert seen == [("SELECT MAX(bsns_year) FROM div_declared WHERE reprt_code = %s", ("11011",))]
    monkeypatch.setattr(dd, "_rows", lambda sql, params=(): None)
    assert dd.latest_annual_year() is None
    monkeypatch.setattr(dd, "_rows", lambda sql, params=(): [(None,)])
    assert dd.latest_annual_year() is None


def test_screen_text_names_the_complete_years_it_was_given():
    out = dd_tool._render_screen(2025, [], {"rows": [], "matched": 0, "n_universe": 825}, 20,
                                 [2020, 2021, 2022, 2023, 2024, 2025], {})
    assert "FY2020~2025 만 온전하다" in out and "FY2020~2024 만" not in out
    out = dd_tool._render_screen(2025, [], {"rows": [], "matched": 0, "n_universe": 825}, 20, [], {})
    assert "온전한 해가 없다" in out
