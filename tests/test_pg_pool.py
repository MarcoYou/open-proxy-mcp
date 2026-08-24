"""PG 커넥션 풀 — 260824 신설. network 0콜·DB 0콜(전부 monkeypatch).

실측 근거: 핸드셰이크 124ms vs 질의 63ms — 연결이 질의보다 2배 비쌌다.
  `price_multiple_data(scope="firm_history")` 는 이 경로를 8번 지나
  1,690ms → 967ms (-43%) 가 됐다.

여기서 지키는 것은 속도가 아니라 **계약**이다 —
  ① None(장애) 과 [](데이터 없음) 의 구분. 섞이면 「배치 미실행」과 「DB 죽음」이 한 말이 된다.
  ② fail-open. 풀은 빠르게 하려고 두는 것이지 없으면 못 도는 것이 아니다.
"""
from __future__ import annotations

import importlib

import pytest


@pytest.fixture
def db(monkeypatch):
    """모듈 상태(싱글턴 풀)를 매번 새로 — 테스트끼리 풀을 물려주면 순서 의존이 생긴다."""
    import open_proxy_mcp.db as m
    m = importlib.reload(m)
    return m


def test_no_database_url_returns_none_not_empty(db, monkeypatch):
    """None 과 [] 는 다른 뜻이다. DB 가 없는데 [] 를 주면 호출부가 '데이터 없음'으로 렌더한다."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert db.pg_rows("SELECT 1") is None
    assert db._get_pool() is None


def test_pool_creation_failure_falls_back_to_direct_connect(db, monkeypatch):
    """★ fail-open. 풀을 못 만들어도 조회는 돌아야 한다."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")
    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setattr(db, "_pool_failed", False)

    import psycopg_pool
    monkeypatch.setattr(psycopg_pool, "ConnectionPool",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("풀 못 만듦")))
    calls = []

    class _FakeConn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, sql, params=()):
            calls.append(sql)
            return type("R", (), {"fetchall": lambda self: [("ok",)]})()

    import psycopg
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: _FakeConn())

    assert db.pg_rows("SELECT 1") == [("ok",)]
    assert calls == ["SELECT 1"], "직접 접속으로 빠지지 않았다"
    assert db._pool_failed is True, "실패를 기억해야 매 질의마다 재시도하지 않는다"


def test_pool_error_retries_via_direct_connect(db, monkeypatch):
    """풀은 섰는데 빌려오다 실패한 경우 — 한 번은 직접 접속으로 더 가본다."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")

    class _BadPool:
        def connection(self): raise RuntimeError("풀 고갈")
    monkeypatch.setattr(db, "_pool", _BadPool())
    monkeypatch.setattr(db, "_pool_failed", False)

    class _FakeConn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def execute(self, *a, **k):
            return type("R", (), {"fetchall": lambda self: [(42,)]})()

    import psycopg
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: _FakeConn())
    assert db.pg_rows("SELECT 1") == [(42,)]


def test_total_failure_returns_none(db, monkeypatch):
    """풀도 직접 접속도 안 되면 None — 여기서 [] 를 주면 오진이 된다."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")
    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setattr(db, "_pool_failed", True)
    import psycopg
    monkeypatch.setattr(psycopg, "connect",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("죽음")))
    assert db.pg_rows("SELECT 1") is None


def test_pool_size_matches_thread_ceiling(db):
    """풀 크기는 `asyncio.to_thread` 칸 수와 맞춘다. 프로덕션 1 CPU → min(32, 1+4)=5.
    그보다 크게 열어도 동시에 쓰지 못하고, DB max_connections(60) 만 잡아먹는다."""
    assert db._POOL_MAX == 5
    assert db._POOL_MIN >= 1
    assert db._POOL_MAX * 2 < 60, "머신 2대 × 풀크기가 DB 한도 안에 들어야 한다"


def test_pool_stats_distinguishes_absent_from_empty(db, monkeypatch):
    """「풀이 없다」와 「풀은 있는데 비었다」는 다른 상태다 — /health 가 구분해서 내야 한다."""
    monkeypatch.setattr(db, "_pool", None)
    assert db.pool_stats()["enabled"] is False


def test_valuation_pg_rows_delegates_to_pool(monkeypatch):
    """호출부 20곳은 안 건드리고 `_pg_rows` 한 곳만 갈아끼웠다 — 계약이 같은지 확인."""
    import open_proxy_mcp.db as m
    from open_proxy_mcp.services.valuation import _pg_rows
    seen = {}

    def _spy(sql, params=()):
        seen["v"] = (sql, params)
        return [(1,)]

    monkeypatch.setattr(m, "pg_rows", _spy)
    assert _pg_rows("SELECT 9", ("a",)) == [(1,)]
    assert seen["v"] == ("SELECT 9", ("a",))


def test_trading_inherits_the_pool():
    """`trading` 은 `valuation._pg_rows` 를 import 한다 — 같은 함수여야 풀을 함께 쓴다."""
    from open_proxy_mcp.services.trading import _pg_rows as t
    from open_proxy_mcp.services.valuation import _pg_rows as v
    assert t is v
