"""KRX 호출 장부 — `bump()` 는 네트워크를 만지지 않고, 쓰기는 배경 스레드가 공용 풀로, 표가 있으면 DDL 0건 (261018 T7).

종전 `_flush_locked` 는 20번째 `bump()` 를 부른 그 자리에서 새 접속 + CREATE TABLE + INSERT 를 했다. 부르는 곳 넷 중 셋이
async 함수 안이라 그동안 이벤트 루프가 멈췄고, CREATE TABLE 이 거절되는 역할에서는 그 뒤로 `bump()` 마다 새 접속을 시도했다.
"""
from __future__ import annotations

import threading
import time
import urllib.parse

import pytest

from open_proxy_mcp import db
from open_proxy_mcp.dart import krx_meter


@pytest.fixture
def meter(monkeypatch):
    """계수를 비운 채 시작하고 끝낸다 — 남은 계수는 atexit 이 쓰려고 든다(DATABASE_URL 이 있으면 그 DB 로)."""
    monkeypatch.setattr(krx_meter, "_pending", 0)
    monkeypatch.setattr(krx_meter, "_FLUSH_SEC", 3600.0)
    if krx_meter._flusher_started:
        krx_meter._wake.set()     # 앞 시험이 띄운 배경 스레드를 긴 잠으로 다시 재운다 — 시험 중 30초 틱에 끼어들지 않게
        time.sleep(0.05)
    yield krx_meter
    krx_meter._pending = 0


def _wait(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


# ── DB 없이 ──────────────────────────────────────────────────────────────

def test_bump_returns_immediately_even_when_the_write_is_slow(meter, monkeypatch):
    """20번째 호출도 곧장 돌아온다 — 느린 쓰기(0.5초)는 배경 스레드에서 돈다."""
    writes: list[tuple[int, str]] = []

    def slow_write(n, *, create_pool=True):
        time.sleep(0.5)
        writes.append((n, threading.current_thread().name))

    monkeypatch.setattr(meter, "_write", slow_write)
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    t0 = time.monotonic()
    for _ in range(25):
        meter.bump()
    assert time.monotonic() - t0 < 0.1, "bump() 가 쓰기를 기다렸다 — 이벤트 루프 위에서 부르면 루프가 멈춘다"
    assert _wait(lambda: writes, timeout=3)
    n, thread = writes[0]
    assert thread == "krx-meter", f"쓰기가 배경 스레드가 아닌 곳에서 돌았다: {thread}"
    assert n >= 20


def test_failed_write_keeps_the_count(meter, monkeypatch):
    def broken(n, *, create_pool=True):
        raise RuntimeError("DB 장애")

    monkeypatch.setattr(meter, "_write", broken)
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")
    meter._pending = 7
    assert meter._flush_once() is False
    assert meter._pending == 7, "못 쓴 계수가 사라졌다"


def test_async_callers_do_not_block_the_loop(meter, monkeypatch):
    """async 경로에서 부르는 그대로 — 루프 표본기가 밀리지 않는다."""
    import asyncio

    monkeypatch.setattr(meter, "_write", lambda n, *, create_pool=True: time.sleep(0.3))
    monkeypatch.setenv("DATABASE_URL", "postgresql://unused")

    async def caller():
        lag = 0.0
        for _ in range(40):
            t = time.monotonic()
            meter.bump()
            await asyncio.sleep(0)
            lag = max(lag, time.monotonic() - t)
        return lag

    assert asyncio.run(caller()) < 0.05


# ── Postgres 17 ──────────────────────────────────────────────────────────

psycopg = pytest.importorskip("psycopg")


@pytest.fixture
def fresh_pool(monkeypatch):
    """`db` 의 공용 풀은 프로세스 싱글턴이다 — 시험마다 새 DB 를 가리키도록 비우고, 끝나면 닫는다."""
    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setattr(db, "_pool_disabled_until", 0.0)
    yield
    if db._pool is not None:
        db._pool.close()


def _install_ddl_counter(url):
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("CREATE TABLE ddl_seen(tag text)")
        c.execute("""CREATE FUNCTION _note_ddl() RETURNS event_trigger LANGUAGE plpgsql SECURITY DEFINER AS
                     $$ BEGIN INSERT INTO public.ddl_seen(tag) VALUES (tg_tag); END $$""")
        c.execute("CREATE EVENT TRIGGER note_ddl ON ddl_command_start EXECUTE FUNCTION _note_ddl()")


def _q(url, sql):
    with psycopg.connect(url, autocommit=True) as c:
        return c.execute(sql).fetchall()


def test_flush_writes_through_the_pool_and_sends_no_ddl(pg17, fresh_pool, meter, monkeypatch):
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(krx_meter._DDL)                    # 운영처럼 표가 이미 있다
    _install_ddl_counter(pg17)
    monkeypatch.setenv("DATABASE_URL", pg17)
    meter._pending = 20
    assert meter._flush_once() is True
    meter._pending = 5
    assert meter._flush_once() is True
    assert _q(pg17, "SELECT machine, calls FROM ops_krx_calls") == [(krx_meter.MACHINE, 25)]
    assert _q(pg17, "SELECT count(*) FROM ddl_seen") == [(0,)], "표가 있는데 DDL 을 보냈다"
    assert db._pool is not None and db.pool_stats()["requests"] >= 2, "공용 풀을 거치지 않았다"


def test_missing_table_is_created_once_by_the_write_path(pg17, fresh_pool, meter, monkeypatch):
    """새 DB(표 없음) — 쓰기가 표를 한 번 만들고 들어간다. 그 뒤로는 다시 만들지 않는다."""
    _install_ddl_counter(pg17)
    monkeypatch.setenv("DATABASE_URL", pg17)
    meter._pending = 3
    assert meter._flush_once() is True
    meter._pending = 4
    assert meter._flush_once() is True
    assert _q(pg17, "SELECT calls FROM ops_krx_calls") == [(7,)]
    assert _q(pg17, "SELECT count(*) FROM ddl_seen") == [(1,)]


def test_read_only_session_keeps_the_count_and_the_pool(pg17, fresh_pool, meter, monkeypatch):
    """읽기 전용 파일럿 — 쓰기는 거절되고 계수는 남는다. **풀은 내리지 않는다**(내리면 모든 조회가 직접 접속으로 떨어진다)."""
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(krx_meter._DDL)
    ro = f"{pg17}?options={urllib.parse.quote('-c default_transaction_read_only=on')}"
    monkeypatch.setenv("DATABASE_URL", ro)
    meter._pending = 9
    assert meter._flush_once() is False
    assert meter._pending == 9
    assert db._pool is not None and db._pool_disabled_until == 0.0, "문장의 거절을 풀 고장으로 보고 풀을 내렸다"
    assert db.pg_rows("SELECT 1") == [(1,)]


def test_flush_at_exit_does_not_build_a_pool(pg17, fresh_pool, meter, monkeypatch):
    """atexit — 풀이 없으면 새로 세우지 않고 직접 접속으로 쓴다(종료 중에 스레드를 띄우는 풀을 만들지 않는다)."""
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(krx_meter._DDL)
    monkeypatch.setenv("DATABASE_URL", pg17)
    meter._pending = 2
    meter.flush()
    assert meter._pending == 0
    assert db._pool is None
    assert _q(pg17, "SELECT calls FROM ops_krx_calls") == [(2,)]
