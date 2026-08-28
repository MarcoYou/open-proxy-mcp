"""느린 이유 계측 — 260824 신설. network 0콜·DB 0콜.

계기: `business_details` 의 p95 가 104초, 최장 615초였다. 그런데 느린 호출 580건 중
**535건(92%)이 DART 원문을 한 번도 안 받았고** 웹 대기도 0 이었다. 장부에는
「178,000ms 걸렸다」밖에 없어서, 그 호출이 스스로 178초를 쓴 것인지 남을 기다린 것인지
구분할 방법이 없었다. 결국 종료시각이 같은 초에 몰린 것을 보고 역산해야 했다:

    23:18:24 끝  178.7s  business_details  disk=1   ← 캐시히트
    23:18:24 끝  168.5s  business_details  disk=1   ← 캐시히트
    23:18:24 끝  159.6s  financial_metrics
    ...
    23:26:29 끝    0.7s  business_details  disk=1   ← 같은 조건, 혼자일 때

셋이 같은 초에 끝났고, 8분 뒤 같은 호출은 0.7초였다. 178초는 그 호출이 한 일이 아니라
**줄**이었다. 이 파일은 그 역산을 다시 하지 않기 위한 계약이다.
"""
from __future__ import annotations

import pytest

from open_proxy_mcp.dart.client import (
    _ACTIVE_LEDGERS,
    inflight_now,
    ledger_enter,
    ledger_exit,
    new_request_ledger,
)


@pytest.fixture(autouse=True)
def _clean():
    _ACTIVE_LEDGERS.clear()
    yield
    _ACTIVE_LEDGERS.clear()


# ── 등록부 ────────────────────────────────────────────────────────────
def test_a_lone_request_sees_one():
    led = new_request_ledger()
    ledger_enter(led)
    assert led["inflight_max"] == 1
    ledger_exit(led)
    assert inflight_now() == 0


def test_the_first_request_learns_about_the_ones_that_arrive_later():
    """★ 핵심. 들어올 때 한 번만 재면 첫 요청은 영원히 1 로 남는다 — 그런데 실제로
    피해를 보는 건 **먼저 들어와서 뒤에 몰린 것을 다 겪은** 그 요청이다."""
    first = new_request_ledger()
    ledger_enter(first)
    later = [new_request_ledger() for _ in range(4)]
    for led in later:
        ledger_enter(led)
    assert first["inflight_max"] == 5, "먼저 온 요청이 나중 혼잡을 못 봤다"
    assert later[-1]["inflight_max"] == 5


def test_peak_survives_the_crowd_leaving():
    """최대값이지 현재값이 아니다 — 다 빠져나간 뒤에 기록해도 봉우리가 남아야 한다."""
    led = new_request_ledger()
    ledger_enter(led)
    others = [new_request_ledger() for _ in range(6)]
    for o in others:
        ledger_enter(o)
    for o in others:
        ledger_exit(o)
    assert inflight_now() == 1
    assert led["inflight_max"] == 7


def test_exit_is_idempotent_and_never_raises():
    """미들웨어의 finally 가 두 번 돌거나, 등록 안 된 장부가 와도 죽지 않는다."""
    led = new_request_ledger()
    ledger_exit(led)            # 등록된 적 없음
    ledger_enter(led)
    ledger_exit(led)
    ledger_exit(led)            # 두 번째
    assert inflight_now() == 0


def test_two_ledgers_with_identical_contents_are_not_confused():
    """장부는 dict 다. 내용이 같아도 **다른 요청**이므로 하나를 빼면 하나만 빠져야 한다
    (`list.remove` 는 == 로 찾는다 — 그래서 이게 진짜 위험한 자리다)."""
    a, b = new_request_ledger(), new_request_ledger()
    assert a == b, "전제: 갓 만든 장부 둘은 내용이 같다"
    ledger_enter(a)
    ledger_enter(b)
    ledger_exit(a)
    assert inflight_now() == 1, "하나 뺐는데 둘 다 빠졌거나 안 빠졌다"


# ── 기록 계약 ─────────────────────────────────────────────────────────
def test_columns_are_in_the_single_source_of_truth():
    from open_proxy_mcp.usage import _EVENT_COLUMNS, _insert_sql
    assert {"inflight", "cpu_ms"} <= set(_EVENT_COLUMNS)
    sql = _insert_sql("t", "?")
    assert sql.count("?") == len(_EVENT_COLUMNS), "INSERT 자리수가 컬럼 수와 다르다"


def test_sqlite_migration_covers_every_event_column():
    """이름 기반 ALTER 는 빠뜨리면 에러가 나서 보이지만, **빠뜨린 채 배포되면**
    그 컬럼만 조용히 안 쌓인다. 목록 둘을 여기서 대조한다."""
    import inspect

    from open_proxy_mcp import usage
    lite = inspect.getsource(usage._sqlite_connect)
    pg = inspect.getsource(usage._pg_connect)
    fixed = {"event_id", "ts_ns", "key_hash", "status"}   # CREATE TABLE 본문에 있는 것
    for col in usage._EVENT_COLUMNS:
        if col in fixed:
            continue
        assert f'"{col} ' in lite, f"sqlite 마이그레이션에 {col} 이 없다"
        assert f"IF NOT EXISTS {col} " in pg, f"PG 마이그레이션에 {col} 이 없다"


def test_middleware_passes_both_numbers():
    import inspect

    from open_proxy_mcp import server
    src = inspect.getsource(server)
    assert "inflight=ledger.get(\"inflight_max\")" in src
    assert "cpu_ms=" in src and "process_time()" in src
    assert "lag_ms=" in src and "loop_lag_ms()" in src


def test_middleware_releases_the_slot_even_on_failure():
    """★ 빠뜨리면 목록이 자라 **이후 모든 요청의 동시 수가 부풀고**, 그 왜곡은
    에러가 아니라 틀린 숫자로 나타난다 — 이 레포에서 제일 비싼 실패 모양이다."""
    import inspect

    from open_proxy_mcp import server
    src = inspect.getsource(server)
    i = src.index("await self.app(scope, replay, send_wrapper)")
    before, after = src[i - 120:i], src[i:i + 900]
    assert "try:" in before, "호출이 try 밖에 있다"
    j = after.index("finally:")
    assert "ledger_exit(ledger)" in after[j:], "finally 안에서 안 뺀다"


# ── 집계 쪽 ───────────────────────────────────────────────────────────
def _tracker():
    import importlib.util
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("ut", root / "scripts" / "usage_tracker.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_four_causes_are_told_apart():
    """★ 260827 까지 셋이었고 그게 틀렸다. `cpu` 가 낮은 것을 전부 「네트워크 대기」로
    몰았는데, 그 안에 **CPU 차례를 못 받은 것**이 섞여 있었다."""
    ut = _tracker()
    rows = [                      # ts, tool, latency, inflight, cpu, lag
        (1, "bd", 100_000, 5, 90_000, 40_000),   # 태웠고·밀렸고·여럿 → 줄
        (2, "bd", 100_000, 1, 90_000,    100),   # 태웠고·안 밀렸고·혼자 → 자신이 무겁다
        (3, "bd", 100_000, 1,  1_000, 40_000),   # 안 태웠는데 밀렸다 → CPU 못 받음
        (4, "bd", 100_000, 4,  1_000,    100),   # 안 태웠고 루프도 한가 → 네트워크 대기
    ]
    per_tool, seen = ut.contention_stats(rows)
    assert seen == 4
    assert dict(per_tool["bd"]) == {"줄": 1, "자신이 무겁다": 1,
                                    "CPU 못 받음": 1, "대기(네트워크)": 1}


def test_the_local_only_tool_is_no_longer_called_a_network_wait():
    """실측 재현: 네트워크를 한 줄도 안 타는 tool 이 15.1초 걸렸다(할 일 3.8초어치).
    종전 분류는 이걸 「대기(네트워크)」라고 적었다 — 기다릴 상대가 없는 tool 인데."""
    ut = _tracker()
    rows = [(1, "law_lookup", 15_100, 1, 3_800, 11_000)]
    per_tool, seen = ut.contention_stats(rows)
    assert dict(per_tool["law_lookup"]) == {"CPU 못 받음": 1}


def test_rows_before_lag_existed_are_not_forced_into_a_category():
    """lag 이 없는 기간을 0 으로 읽으면 그 기간이 통째로 「네트워크 대기」로 쏠린다 —
    없던 컬럼을 0 으로 읽는 것이 `degraded` 를 첫날 망친 형태다."""
    ut = _tracker()
    rows = [(1, "bd", 100_000, 1, 1_000, None)]
    per_tool, seen = ut.contention_stats(rows)
    assert dict(per_tool["bd"]) == {"모름(lag 계측 전)": 1}


def test_rows_from_before_the_columns_existed_are_not_a_category():
    """★ `degraded` 가 첫 실행에서 조용히 틀렸던 바로 그 자리다. 드레인 백업에는 이
    컬럼이 없어 `merge_drained` 가 None 으로 채우는데, 그걸 안 거르면 「없음」이
    하나의 원인 범주로 둔갑해 65,500건짜리 유령이 된다(260824 실측)."""
    ut = _tracker()
    rows = [(1, "bd", 100_000, None, None, None), (2, "bd", 100_000, 3, 90_000, 40_000)]
    per_tool, seen = ut.contention_stats(rows)
    assert seen == 1, "계측 전 행이 분모에 들어갔다"
    assert dict(per_tool["bd"]) == {"줄": 1}


def test_no_rows_is_silence_not_an_alarm():
    """계측 전 기간에는 아무것도 안 낸다 — 「0건」과 「안 쟀다」는 다르다."""
    ut = _tracker()
    per_tool, seen = ut.contention_stats([])
    assert seen == 0 and not per_tool


def test_the_column_order_matches_the_query():
    """SELECT 순서와 `_SLOW_COLS` 가 어긋나면 값이 조용히 다른 자리로 들어간다
    (260704 mkt_fund_hist 사고와 같은 실패 모드)."""
    import inspect
    ut = _tracker()
    src = inspect.getsource(ut.fetch_slow)
    sel = src.split("SELECT ")[1].split(" FROM")[0]
    assert tuple(c.strip() for c in sel.split(",")) == ut._SLOW_COLS


# ── 미들웨어 왕복 ─────────────────────────────────────────────────────
# 위의 소스 문자열 검사는 「호출부가 있다」까지만 본다. 여기서는 실제로 미들웨어를
# 돌려 **기록된 값**을 본다 — 배선이 끊겨도 문자열 검사는 통과하기 때문이다.
def _drive(app_body, n=1, method="POST", rpc="tools/call"):
    """가짜 ASGI 앱을 미들웨어에 물려 n건을 동시에 흘리고, record() 인자를 모은다."""
    import asyncio
    import json

    from open_proxy_mcp import server, usage

    calls = []

    async def app(scope, receive, send):
        await receive()
        await app_body()
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b'{"isError":false}',
                    "more_body": False})

    mw = server.ApiKeyMiddleware(app)
    body = json.dumps({"method": rpc, "params": {"name": "t"}}).encode() if rpc else b""

    async def one():
        scope = {"type": "http", "path": "/mcp", "query_string": b"opendart=k" * 1,
                 "headers": [], "method": method}
        sent = []

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        async def send(m):
            sent.append(m)
        await mw(scope, receive, send)

    async def main():
        orig = usage.record
        usage.record = lambda *a, **k: calls.append(k)
        try:
            await asyncio.gather(*[one() for _ in range(n)])
        finally:
            usage.record = orig

    asyncio.run(main())
    return calls


def test_a_lone_call_records_inflight_one():
    async def quick():
        return
    calls = [c for c in _drive(quick, n=1) if "inflight" in c]
    assert len(calls) == 1
    assert calls[0]["inflight"] == 1
    assert calls[0]["cpu_ms"] is not None


def test_eight_concurrent_calls_record_that_they_were_eight():
    """★ 이 숫자 하나가 「이 호출이 무겁다」와 「줄에 서 있었다」를 가른다."""
    import asyncio

    async def slow():
        await asyncio.sleep(0.05)
    calls = [c for c in _drive(slow, n=8) if "inflight" in c]
    assert len(calls) == 8
    assert max(c["inflight"] for c in calls) == 8, [c["inflight"] for c in calls]


def test_the_registry_is_empty_after_every_request_finished():
    """샜다면 다음 요청의 동시 수가 부풀어 **이후 모든 측정이 조용히 틀린다.**"""
    async def quick():
        return
    _drive(quick, n=5)
    assert inflight_now() == 0


def test_cpu_time_separates_waiting_from_working():
    """기다린 요청과 코어를 태운 요청이 다른 숫자로 남아야 한다 —
    안 그러면 컬럼을 더한 뜻이 없다."""
    import asyncio

    async def waited():
        await asyncio.sleep(0.15)

    async def worked():
        x = 0
        for i in range(3_000_000):
            x += i

    wait_ms = _drive(waited, n=1)[0]["cpu_ms"]
    work_ms = _drive(worked, n=1)[0]["cpu_ms"]
    assert wait_ms < 30, f"기다리기만 했는데 CPU {wait_ms}ms 로 잡혔다"
    assert work_ms > wait_ms, f"태웠는데 안 잡혔다 ({work_ms}ms vs {wait_ms}ms)"


def test_health_can_tell_the_machines_apart_without_naming_them():
    """머신 ID 원문은 public `/health` 에 못 둔다(인프라 좌표는 private). 그런데
    **두 대가 실제로 갈라 받고 있나**는 물어볼 수 있어야 한다 — 260824 에 동시성이
    높았던 네 구간 전부 한 머신이 100% 를 받고 있었고 그건 로그로 잘 안 보였다."""
    import os

    from open_proxy_mcp.server import _instance_tag
    old = os.environ.get("FLY_MACHINE_ID")
    try:
        os.environ["FLY_MACHINE_ID"] = "832e73a7701738"
        a = _instance_tag()
        os.environ["FLY_MACHINE_ID"] = "84e667b22d22d8"
        b = _instance_tag()
    finally:
        os.environ.pop("FLY_MACHINE_ID", None)
        if old is not None:
            os.environ["FLY_MACHINE_ID"] = old
    assert a != b, "두 머신이 같은 표식을 낸다 — 구별이 안 된다"
    assert "832e73a7701738" not in a and "84e667b22d22d8" not in b, "ID 원문이 샌다"
    assert len(a) == 8


def test_a_held_stream_is_not_a_competitor():
    """★ 이 지표가 **첫 배포에서 조용히 틀렸던** 지점이다.

    streamable-http 클라이언트는 `GET /mcp` 로 스트림을 열어 세션 내내 붙들고 있다.
    그걸 함께 세면 「지금 CPU 를 다투는 요청 수」가 아니라 「열려 있는 연결 수」가 된다 —
    실측으로 기록 19건이 **전부 6 이상**이었고 64ms 짜리 호출도 inflight=12 로 적혔다.
    1 이 한 번도 안 나오는 지표는 「모두가 줄에 서 있다」고 말하는 것과 같다.
    """
    import asyncio

    async def hold():
        await asyncio.sleep(0.08)

    # 스트림만 흘려 보면 아무것도 등록되지 않아야 한다
    _drive(hold, n=6, method="GET", rpc=None)
    assert inflight_now() == 0

    calls = [c for c in _drive(hold, n=1) if "inflight" in c]
    assert calls and calls[0]["inflight"] == 1, \
        f"붙들린 스트림이 tools/call 의 동시 수를 부풀린다: {calls}"


def test_handshakes_do_not_count_either():
    """initialize·ping 은 비용이 0 에 가까워 줄을 만들지 않는다 — 세면 잡음만 된다."""
    async def quick():
        return
    _drive(quick, n=4, rpc="initialize")
    assert inflight_now() == 0


# ── 루프 지연 표본기 ──────────────────────────────────────────────────
def test_the_sampler_stays_near_zero_when_the_loop_is_free():
    """네트워크를 기다리는 요청은 루프를 붙들지 않는다 — 지연이 안 쌓여야 한다.
    안 그러면 모든 대기가 「CPU 못 받음」으로 오분류된다."""
    import asyncio

    from open_proxy_mcp.dart import client as C

    async def main():
        C.ensure_lag_sampler()
        a = C.loop_lag_ms()
        await asyncio.sleep(0.5)          # 순수 대기 — 루프는 한가하다
        return C.loop_lag_ms() - a
    assert asyncio.run(main()) < 50, "한가한 루프인데 지연이 쌓였다"


def test_the_sampler_catches_a_blocked_loop():
    """동기 코드가 루프를 붙들면 표본기가 제때 못 깬다 — 그 지각이 신호다."""
    import asyncio
    import time

    from open_proxy_mcp.dart import client as C

    async def main():
        C.ensure_lag_sampler()
        await asyncio.sleep(0.15)         # 표본기가 한 번 돌게 둔다
        a = C.loop_lag_ms()
        time.sleep(0.6)                   # ★ await 없이 붙든다
        # **막힌 직후 바로 읽는다** — 미들웨어가 하는 것과 같다. 여기서 await 을 하나
        # 끼우면 표본기가 따라잡아 통과해 버리는데, 정작 실전에서 놓치는 건 이 순간이다.
        return C.loop_lag_ms() - a
    assert asyncio.run(main()) > 300, "루프가 0.6초 막혔는데 lag 이 0 이다"


def test_the_sampler_follows_the_loop_it_was_started_on():
    """`asyncio.Task` 도 락처럼 만든 루프에 묶인다. 루프가 바뀌면 다시 띄워야 한다 —
    안 그러면 배포 뒤 조용히 0 만 기록된다."""
    import asyncio

    from open_proxy_mcp.dart import client as C

    async def go():
        C.ensure_lag_sampler()
        await asyncio.sleep(0.15)
        # **루프 안에서** 본다 — asyncio.run 이 끝나면 남은 task 를 취소하므로
        # 밖에서 본 done() 은 아무 뜻이 없다.
        return C._lag_task, asyncio.get_running_loop(), C._lag_task.done()
    t1, l1, alive1 = asyncio.run(go())
    t2, l2, alive2 = asyncio.run(go())
    assert l1 is not l2, "전제: 서로 다른 루프"
    assert t1 is not t2, "루프가 바뀌었는데 옛 task 를 그대로 쓴다"
    assert not alive1 and not alive2, "표본기가 도는 중에 죽었다"


def test_outside_a_loop_it_is_silent():
    """스크립트·import 시점에는 루프가 없다. 죽지 말고 조용히 통과해야 한다."""
    from open_proxy_mcp.dart.client import ensure_lag_sampler
    ensure_lag_sampler()
