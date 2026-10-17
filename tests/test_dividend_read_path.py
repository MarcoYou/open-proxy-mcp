"""dividend_data 읽기 경로(재설계 4단계 T3) — 연도 범위 캐시 · 이벤트 루프 보호.

근거(운영 `pg_stat_statements`·`EXPLAIN`, 2026-10-18): `SELECT MIN(bsns_year), MAX(bsns_year) FROM div_declared` 는 인덱스가 없어 표 전체(38,183행·1,683쪽)를
읽었다 — 335회 평균 475ms, 차가운 캐시 1.5초. 게다가 `tools/dividend_data.py` 는 이 동기 호출을 async 함수 안에서 그대로 불러, 질의가 끝날 때까지
**서버 전체(다른 도구·/health)가 멈췄다.** DB 0콜 — 전부 `dd._rows` 를 바꿔 끼운다.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from open_proxy_mcp.services import dividend_data as dd
from open_proxy_mcp.services import price_multiple_data as pmd
from open_proxy_mcp.tools import dividend_data as dd_tool


@pytest.fixture(autouse=True)
def _fresh_cache():
    dd.year_range_cache_clear()
    yield
    dd.year_range_cache_clear()


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
# 1. 연도 범위 캐시
# ═════════════════════════════════════════════════════════════════════════════════════════════════
def _counting_rows(monkeypatch, result=((2020, 2025),)):
    calls = []

    def rows(sql, params=()):
        calls.append(" ".join(sql.split()))
        return [tuple(r) for r in result] if result is not None else None
    monkeypatch.setattr(dd, "_rows", rows)
    return calls


def test_year_range_hits_the_database_once_within_the_ttl(monkeypatch):
    calls = _counting_rows(monkeypatch)
    assert dd.year_range() == (2020, 2025)
    assert dd.year_range() == (2020, 2025)
    assert dd.year_range() == (2020, 2025)
    assert len(calls) == 1 and "MIN(bsns_year)" in calls[0], "TTL 안에서 질의를 반복했다"


def test_year_range_refreshes_after_the_ttl_and_picks_up_a_new_year(monkeypatch):
    calls = _counting_rows(monkeypatch)
    assert dd.year_range() == (2020, 2025)
    monkeypatch.setattr(dd, "_YEAR_RANGE_TTL_SEC", 0.0)                       # 만료
    monkeypatch.setattr(dd, "_rows", lambda sql, params=(): [(2020, 2026)])    # 새 사업연도를 적재했다
    assert dd.year_range() == (2020, 2026)


def test_year_range_ttl_is_six_hours():
    assert dd._YEAR_RANGE_TTL_SEC == 6 * 3600


def test_a_failed_query_is_not_cached_so_one_outage_cannot_stick_for_hours(monkeypatch):
    _counting_rows(monkeypatch, result=None)                                  # DB 장애 = None
    assert dd.year_range() is None
    monkeypatch.setattr(dd, "_rows", lambda sql, params=(): [(2020, 2025)])   # 복구
    assert dd.year_range() == (2020, 2025), "장애 결과가 캐시에 굳었다"


def test_an_empty_table_is_not_cached_either(monkeypatch):
    _counting_rows(monkeypatch, result=((None, None),))
    assert dd.year_range() is None
    monkeypatch.setattr(dd, "_rows", lambda sql, params=(): [(2021, 2025)])
    assert dd.year_range() == (2021, 2025)


def test_cache_clear_forces_a_fresh_read(monkeypatch):
    calls = _counting_rows(monkeypatch)
    dd.year_range()
    dd.year_range_cache_clear()
    dd.year_range()
    assert len(calls) == 2


def test_cache_is_safe_when_many_threads_ask_at_once(monkeypatch):
    """to_thread 로 여러 호출이 동시에 부르면 첫 순간 질의가 겹칠 수 있다 — 값은 언제나 온전해야 한다."""
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
        if "FROM div_declared" in s and "DISTINCT ON (corp_code, bsns_year)" in s:
            return [(2024, 800, 600, 1.0e13, 30.0), (2023, 800, 590, 0.9e13, 29.0)]
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


def test_the_second_call_skips_the_year_range_query_and_does_not_block_either(monkeypatch):
    threads: list[int] = []
    monkeypatch.setattr(dd, "_rows", _slow_rows(threads, delay=0.05))
    tool = _tool()

    async def go():
        await tool(scope="market", format="md")
        n_first = len(threads)
        await tool(scope="market", format="md")
        return n_first, len(threads) - n_first

    n_first, n_second = asyncio.run(go())
    assert n_first == 2 and n_second == 1, "두 번째 호출은 연도 범위 질의를 건너뛰어야 한다(캐시)"


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
    assert set(seen) == {"year_range", "aggregate_history", "sector_list", "screen", "payment_scope_years",
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
