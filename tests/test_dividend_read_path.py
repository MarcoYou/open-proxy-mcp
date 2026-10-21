"""dividend_data 읽기 경로(재설계 4단계 T3) — 연도 범위 · 이벤트 루프 보호.

근거(운영 `pg_stat_statements`·`EXPLAIN`, 2026-10-18): `SELECT MIN(bsns_year), MAX(bsns_year) FROM div_declared` 는 인덱스가 없어 표 전체(38,183행·1,683쪽)를
읽었다 — 335회 평균 475ms, 차가운 캐시 1.5초. 게다가 `tools/dividend_data.py` 는 이 동기 호출을 async 함수 안에서 그대로 불러, 질의가 끝날 때까지
**서버 전체(다른 도구·/health)가 멈췄다.** DB 0콜 — 전부 `dd._rows` 를 바꿔 끼운다.

261018 후속: 그때 붙인 6시간 캐시는 새 사업연도 적재 뒤 최대 6시간 지난해를 기본 연도로 냈다. 인덱스 `div_declared_yr` 가 운영에 있어
(MIN·MAX = Index Only Scan 양 끝 한 칸씩) 캐시를 없앴다 — 아래 1절이 그것을 잠근다.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from open_proxy_mcp.services import dividend_data as dd
from open_proxy_mcp.services import price_multiple_data as pmd
from open_proxy_mcp.tools import dividend_data as dd_tool


class _Registry:
    """FastMCP 대역 — `@mcp.tool()` 데코레이터가 붙잡은 함수를 꺼내 쓴다."""
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


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 1. 연도 범위 — 캐시 없이 호출마다 묻는다
# ═════════════════════════════════════════════════════════════════════════════════════════════════
def _counting_rows(monkeypatch, result=((2020, 2025),)):
    calls = []

    def rows(sql, params=()):
        calls.append(" ".join(sql.split()))
        return [tuple(r) for r in result] if result is not None else None
    monkeypatch.setattr(dd, "_rows", rows)
    return calls


def test_a_newly_loaded_fiscal_year_shows_up_on_the_very_next_call(monkeypatch):
    """새 사업연도를 적재하면 다음 호출부터 보인다 — 종전엔 6시간(머신마다) 지난해가 기본 연도였다."""
    _counting_rows(monkeypatch)
    assert dd.year_range() == (2020, 2025)
    monkeypatch.setattr(dd, "_rows", lambda sql, params=(): [(2020, 2026)])    # 새 사업연도를 적재했다
    assert dd.year_range() == (2020, 2026), "적재한 새 사업연도가 캐시에 가려졌다"


def test_year_range_asks_the_database_on_every_call(monkeypatch):
    calls = _counting_rows(monkeypatch)
    for _ in range(3):
        assert dd.year_range() == (2020, 2025)
    assert len(calls) == 3 and all("MIN(bsns_year)" in c and "MAX(bsns_year)" in c for c in calls)


def test_no_year_range_cache_is_left_behind():
    """캐시를 되살리면 위 문제가 돌아온다 — 상수·저장소가 다시 생기지 않게 막는다."""
    assert not any(hasattr(dd, name) for name in ("_YEAR_RANGE_TTL_SEC", "_YEAR_RANGE_CACHE", "year_range_cache_clear"))


def test_a_failed_query_reads_as_none_and_the_next_call_recovers(monkeypatch):
    _counting_rows(monkeypatch, result=None)                                  # DB 장애 = None
    assert dd.year_range() is None
    monkeypatch.setattr(dd, "_rows", lambda sql, params=(): [(2020, 2025)])   # 복구
    assert dd.year_range() == (2020, 2025)


def test_an_empty_table_reads_as_none(monkeypatch):
    _counting_rows(monkeypatch, result=((None, None),))
    assert dd.year_range() is None
    monkeypatch.setattr(dd, "_rows", lambda sql, params=(): [(2021, 2025)])
    assert dd.year_range() == (2021, 2025)


def test_year_range_is_safe_when_many_threads_ask_at_once(monkeypatch):
    """to_thread 로 여러 호출이 동시에 부른다 — 값은 언제나 온전해야 한다."""
    monkeypatch.setattr(dd, "_rows", lambda sql, params=(): (time.sleep(0.01), [(2020, 2025)])[1])
    got = []
    ts = [threading.Thread(target=lambda: got.append(dd.year_range())) for _ in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert got == [(2020, 2025)] * 8


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 2. 도구 — DB 호출이 이벤트 루프 밖에서 돈다
# ═════════════════════════════════════════════════════════════════════════════════════════════════
_BLOCK_SEC = 0.4         # 가짜 DB 한 번의 소요. 루프를 막으면 이 시간만큼 멈춘다


def _slow_rows(threads, delay=_BLOCK_SEC):
    """실제 서비스 SQL 모양에 맞는 행을 돌려주는 느린 `_rows` — 어느 스레드에서 불렸는지 기록한다."""
    def rows(sql, params=()):
        threads.append(threading.get_ident())
        time.sleep(delay)
        s = " ".join(sql.split())
        if "MIN(bsns_year)" in s:
            return [(2020, 2025)]
        if "FROM div_payment_scope" in s:
            return [(2020,), (2021,), (2022,), (2023,), (2024,)]
        if "FROM div_declared" in s and "FROM yr GROUP BY bsns_year" in s:      # 261021: 시장 집계 — 회사·사업연도 한 줄(결산기 합) 위의 해별 집계
            return [(2024, 800, 600, 1.0e13, 30.0, 0), (2023, 800, 590, 0.9e13, 29.0, 0)]
        return []
    return rows


async def _heartbeat(stop: asyncio.Event, gaps: list):
    last = time.perf_counter()
    while not stop.is_set():
        await asyncio.sleep(0.01)
        now = time.perf_counter()
        gaps.append(now - last)
        last = now


def test_db_calls_run_off_the_event_loop_and_the_loop_keeps_ticking(monkeypatch):
    """🔴 이 테스트가 막는 사고: 동기 DB 호출이 async 도구 안에서 루프를 세운다 — 호출당 0.8초(0.4 × 2) 동안 심장박동이 멎는다."""
    threads: list[int] = []
    monkeypatch.setattr(dd, "_rows", _slow_rows(threads))
    tool = _tool()

    async def go():
        main_tid = threading.get_ident()
        stop, gaps = asyncio.Event(), []
        hb = asyncio.create_task(_heartbeat(stop, gaps))
        out = await tool(scope="market", format="md")
        stop.set()
        await hb
        return main_tid, gaps, out

    main_tid, gaps, out = asyncio.run(go())
    assert "코스피 — 확정 배당 집계" in out and "| 2024 | 800사 | 600사 |" in out
    assert len(threads) == 2, "연도 범위 + 시장 집계, DB 두 번"
    assert main_tid not in threads, "DB 호출이 이벤트 루프 스레드에서 돌았다"
    assert len(gaps) > 20, f"루프가 거의 못 돌았다: {len(gaps)}번"
    assert max(gaps) < _BLOCK_SEC / 2, f"루프가 {max(gaps):.2f}초 멈췄다 — 동기 DB 호출이 루프를 막고 있다"


def test_every_call_asks_the_year_range_again_off_the_loop(monkeypatch):
    """261018: 캐시를 없앴다 — 두 번째 호출도 연도 범위를 다시 묻고(새 사업연도가 바로 보인다), 그 질의도 루프 밖에서 돈다."""
    threads: list[int] = []
    monkeypatch.setattr(dd, "_rows", _slow_rows(threads, delay=0.05))
    tool = _tool()

    async def go():
        main = threading.get_ident()
        await tool(scope="market", format="md")
        n_first = len(threads)
        await tool(scope="market", format="md")
        return main, n_first, len(threads) - n_first

    main, n_first, n_second = asyncio.run(go())
    assert n_first == 2 and n_second == 2, "두 번째 호출도 연도 범위 + 집계 두 질의여야 한다"
    assert main not in threads, "DB 호출이 이벤트 루프 스레드에서 돌았다"


def test_every_scope_sends_all_of_its_database_calls_to_worker_threads(monkeypatch):
    """scope 별로 어떤 `dd.*` 가 불리든 전부 루프 밖에서 돈다 — 하나라도 빠지면 그 scope 만 루프를 세운다."""
    main = {}
    seen: dict[str, bool] = {}

    def record(name, ret):
        def fn(*a, **k):
            seen[name] = threading.get_ident() != main["tid"]
            return ret
        return fn
    corp = {"corp_name": "삼성전자", "stock_code": "005930", "corp_code": "00126380"}

    async def fake_resolve(q):
        return corp, None
    monkeypatch.setattr(pmd, "_resolve_listed", fake_resolve)
    monkeypatch.setattr(dd, "year_range", record("year_range", (2020, 2025)))
    monkeypatch.setattr(dd, "latest_annual_year", record("latest_annual_year", 2025))
    monkeypatch.setattr(dd, "aggregate_history", record("aggregate_history", {"status": "ok", "rows": []}))
    monkeypatch.setattr(dd, "sector_list", record("sector_list", ["금융"]))
    monkeypatch.setattr(dd, "screen", record("screen", {"status": "ok", "rows": [{"corp_code": "00126380", "name": "삼성전자", "ticker": "005930"}],
                                                          "matched": 1, "n_universe": 700, "returned": 1, "limit": 50}))
    monkeypatch.setattr(dd, "payment_scope_years", record("payment_scope_years", [2020, 2021, 2022, 2023, 2024]))
    monkeypatch.setattr(dd, "payment_history", record("payment_history", {"00126380": [4, 4, 4, 4, 4]}))
    monkeypatch.setattr(dd, "firm_history", record("firm_history", {"status": "ok", "annual": [], "quarterly": [], "annual_failed": False,
                                                                     "quarterly_failed": False, "empty_kind_rows_folded": 0}))
    monkeypatch.setattr(dd, "payment_counts", record("payment_counts", {"status": "ok", "complete_years": [2024], "rows": [], "decisions": []}))
    tool = _tool()

    async def go():
        main["tid"] = threading.get_ident()
        await tool(scope="market")
        await tool(scope="sector")                            # sector 미지정 → sector_list
        await tool(scope="sector", sector="금융")
        await tool(scope="screen", bsns_year=2024, min_payments=2)
        await tool(scope="firm", company="삼성전자")

    asyncio.run(go())
    assert set(seen) == {"year_range", "latest_annual_year", "aggregate_history", "sector_list", "screen", "payment_scope_years",
                         "payment_history", "firm_history", "payment_counts"}
    assert all(seen.values()), f"루프 스레드에서 돈 호출: {[k for k, v in seen.items() if not v]}"


def test_tool_output_is_unchanged_by_moving_the_calls_off_the_loop(monkeypatch):
    """같은 입력이면 서비스를 직접 부른 뒤 렌더한 것과 도구 출력이 글자까지 같다."""
    monkeypatch.setattr(dd, "_rows", _slow_rows([], delay=0.0))
    direct = dd_tool._render_agg("market", "코스피", dd.aggregate_history("market", "코스피", 2020, 2025))
    got = asyncio.run(_tool()(scope="market", format="md"))
    assert got == direct


def test_db_failure_messages_are_unchanged(monkeypatch):
    """연도 범위 질의 실패 → 종전과 같은 안내(조회 실패 · status=db_error). 캐시가 실패를 숨기지도, 만들어내지도 않는다."""
    monkeypatch.setattr(dd, "_rows", lambda sql, params=(): None)
    out = asyncio.run(_tool()(scope="market"))
    assert out.startswith("## 배당 원장 DB 조회 실패 (`status=db_error`)")
    monkeypatch.setattr(dd, "_rows", lambda sql, params=(): [(2020, 2025)] if "MIN(" in sql else None)
    out = asyncio.run(_tool()(scope="market"))
    assert out == "## 시장 배당 집계 조회 실패 (`status=db_error`)"


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 3. screen 은 질의 하나 · firm 의 두 왕복은 동시에 (261021 B9①②)
# ═════════════════════════════════════════════════════════════════════════════════════════════════
def test_screen_reads_rows_matched_count_and_universe_in_one_query(monkeypatch):
    """종전엔 행 · COUNT(*) · 모집단이 질의 셋이었다 — 같은 CTE 를 두 번 더 돌렸다. 이제 `COUNT(*) OVER ()` 와 첫 CTE 하나로 질의 하나."""
    calls = []

    def rows(sql, params=()):
        calls.append((" ".join(sql.split()), params))
        return [("00126380", "삼성전자", "005930", 1444.0, 9.8e12, 30.0, "20260310000100", 1, False, 57, 825)]
    monkeypatch.setattr(dd, "_rows", rows)
    d = dd.screen(2025, min_dps=100, limit=1)
    assert len(calls) == 1, "행 · 매칭 수 · 모집단이 질의 하나"
    sql, params = calls[0]
    assert "COUNT(*) OVER () AS matched" in sql and "(SELECT n FROM uni) AS n_universe" in sql and sql.rstrip().endswith("LIMIT %s")
    assert params == ("11011", 2025, "11011", 2025, 100, 1), "모집단 CTE → 행 조건 → 한도 순서로 자리표시자와 맞는다"
    assert (d["matched"], d["n_universe"], d["returned"], d["limit"]) == (57, 825, 1, 1), "limit 은 표시 한도 — 매칭 수는 한도 앞에서 센다"
    assert d["rows"][0]["dps_krw"] == 1444 and d["rows"][0]["total_basis"] == "single"


def test_screen_with_no_matching_company_reads_the_universe_in_a_second_query(monkeypatch):
    calls = []

    def rows(sql, params=()):
        calls.append(" ".join(sql.split()))
        return [] if "OVER ()" in sql else [(825,)]
    monkeypatch.setattr(dd, "_rows", rows)
    d = dd.screen(2025, min_dps=10 ** 9)
    assert len(calls) == 2 and (d["matched"], d["n_universe"], d["rows"]) == (0, 825, [])
    monkeypatch.setattr(dd, "_rows", lambda sql, params=(): [] if "OVER ()" in sql else None)       # 모집단 질의만 실패
    d2 = dd.screen(2025, min_dps=10 ** 9)
    assert d2["status"] == "ok" and d2["matched"] == 0 and d2["n_universe"] is None, "못 센 모집단을 0 으로 메우지 않는다"
    monkeypatch.setattr(dd, "_rows", lambda sql, params=(): None)
    assert dd.screen(2025) == {"status": "db_error"}


def test_firm_scope_sends_the_ledger_and_the_decision_reads_together(monkeypatch):
    """원장 시계열과 결정공시 횟수는 입력이 겹치지 않는다 — 두 DB 왕복이 겹쳐 돈다(합계 ≈ 한 번의 시간)."""
    corp = {"corp_name": "삼성전자", "stock_code": "005930", "corp_code": "00126380"}

    async def fake_resolve(q):
        return corp, None
    monkeypatch.setattr(pmd, "_resolve_listed", fake_resolve)
    monkeypatch.setattr(dd, "year_range", lambda: (2020, 2025))
    spans = {}

    def timed(name, ret):
        def fn(*a, **k):
            t0 = time.perf_counter()
            time.sleep(0.3)
            spans[name] = (t0, time.perf_counter())
            return ret
        return fn
    monkeypatch.setattr(dd, "firm_history", timed("firm", {"status": "ok", "annual": [], "quarterly": [], "annual_failed": False,
                                                          "quarterly_failed": False, "empty_kind_rows_folded": 0}))
    monkeypatch.setattr(dd, "payment_counts", timed("pay", {"status": "ok", "complete_years": [2024], "rows": [], "decisions": []}))
    t0 = time.perf_counter()
    out = asyncio.run(_tool()(scope="firm", company="삼성전자"))
    elapsed = time.perf_counter() - t0
    assert out.startswith("## 삼성전자 (005930)") and set(spans) == {"firm", "pay"}
    assert spans["firm"][0] < spans["pay"][1] and spans["pay"][0] < spans["firm"][1], "두 읽기의 구간이 겹친다"
    assert elapsed < 0.55, f"차례로 기다렸다: {elapsed:.2f}초 (동시면 약 0.3초)"


def test_firm_scope_still_reports_a_failed_ledger_read(monkeypatch):
    corp = {"corp_name": "삼성전자", "stock_code": "005930", "corp_code": "00126380"}

    async def fake_resolve(q):
        return corp, None
    monkeypatch.setattr(pmd, "_resolve_listed", fake_resolve)
    monkeypatch.setattr(dd, "year_range", lambda: (2020, 2025))
    monkeypatch.setattr(dd, "firm_history", lambda *a, **k: {"status": "db_error"})
    monkeypatch.setattr(dd, "payment_counts", lambda *a, **k: {"status": "ok", "complete_years": [], "rows": [], "decisions": []})
    assert asyncio.run(_tool()(scope="firm", company="삼성전자")) == "## 배당 원장 조회 실패 (`status=db_error`)"


def test_screen_sends_independent_reads_together(monkeypatch):
    """서로 입력이 필요 없는 작은 읽기는 함께 보낸다: 연도 범위 + 사업보고서 마지막 해(둘), 거르기 + 온전한 해 목록(둘). 차례로 보내면 0.4초, 함께면 약 0.2초."""
    import time

    def slow(ret, delay=0.1):
        def fn(*a, **k):
            time.sleep(delay)
            return ret
        return fn
    monkeypatch.setattr(dd, "year_range", slow((2020, 2025)))
    monkeypatch.setattr(dd, "latest_annual_year", slow(2025))
    monkeypatch.setattr(dd, "screen", slow({"status": "ok", "rows": [], "matched": 0, "n_universe": 700, "returned": 0, "limit": 50}))
    monkeypatch.setattr(dd, "payment_scope_years", slow([2020, 2021, 2022]))
    monkeypatch.setattr(dd, "payment_history", slow({}, 0.0))
    tool = _tool()

    async def go():
        t0 = time.perf_counter()
        out = await tool(scope="screen", bsns_year=2024)
        return time.perf_counter() - t0, out

    elapsed, out = asyncio.run(go())
    assert isinstance(out, str)
    assert elapsed < 0.33, f"차례로 보냈다({elapsed:.2f}s)"
