"""환율 영구 캐시(ecos_fx_rate) — 읽기는 SELECT 만, 표 생성은 쓰기 경로가 표가 없을 때만 (261018).

종전 `_db_get` 은 읽을 때마다 CREATE TABLE IF NOT EXISTS 를 먼저 보냈다. Postgres 는 표가 이미 있어도 읽기 전용
트랜잭션·스키마 생성 권한이 없는 역할(opm_ro)에서 그 문장을 거절하고, 예외를 삼킨 `_db_get` 은 None 을 냈다
— 읽기 전용 서버는 캐시에 행이 있어도 매번 ECOS/야후로 갔다.

앞의 두 테스트는 네트워크·DB 없이 돈다. 나머지는 `tests/conftest.py` 의 `pg17`(로컬 Docker Postgres 17)이 필요하고,
Docker 가 없으면 그 테스트만 건너뛴다.
"""
from __future__ import annotations

import asyncio
import urllib.parse
from unittest.mock import AsyncMock, Mock

import pytest

from open_proxy_mcp import db
from open_proxy_mcp.dart import fx

psycopg = pytest.importorskip("psycopg")

USD_ROW = ("USD", "20241231", 1470.0)


# ── DB 없이 ──────────────────────────────────────────────────────────────

def test_read_sends_only_a_select(monkeypatch):
    seen: list[str] = []

    def _rows(sql, params=()):
        seen.append(sql)
        return [(1470.0,)]

    monkeypatch.setattr(fx, "pg_rows", _rows)
    assert fx._db_get("USD", "20241231") == 1470.0
    assert len(seen) == 1 and seen[0].lstrip().upper().startswith("SELECT")
    assert "CREATE" not in seen[0].upper()


@pytest.mark.parametrize("rows", [None, [], [(None,)]])
def test_read_failure_or_empty_is_a_miss(monkeypatch, rows):
    """None(DB 미설정·장애·없는 표·권한 없음) / 행 없음 / 빈 값 — 모두 미스."""
    monkeypatch.setattr(fx, "pg_rows", lambda sql, params=(): rows)
    assert fx._db_get("USD", "20241231") is None


# ── Postgres 17 ──────────────────────────────────────────────────────────

@pytest.fixture
def fresh_pool(monkeypatch):
    """`db` 의 공용 풀은 프로세스 싱글턴이다 — 테스트마다 새 DB 를 가리키도록 비우고, 끝나면 닫는다."""
    monkeypatch.setattr(db, "_pool", None)
    monkeypatch.setattr(db, "_pool_disabled_until", 0.0)
    yield
    if db._pool is not None:
        db._pool.close()


def _with_options(url: str, options: str) -> str:
    return f"{url}?options={urllib.parse.quote(options)}"


def _create_prod_like(url: str, *, rls: bool = True) -> None:
    """운영과 같은 꼴 — 표(소유자 postgres) + RLS·SELECT 정책 + opm_ro 읽기 권한 + 행 하나."""
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(fx._FX_DDL)
        if rls:
            c.execute("ALTER TABLE ecos_fx_rate ENABLE ROW LEVEL SECURITY")
            c.execute("CREATE POLICY ecos_fx_rate_read ON ecos_fx_rate FOR SELECT USING (true)")
        c.execute("GRANT SELECT ON ecos_fx_rate TO opm_ro")
        c.execute(fx._FX_INSERT, USD_ROW)


def _table_exists(url: str) -> bool:
    with psycopg.connect(url, autocommit=True) as c:
        return c.execute("SELECT to_regclass('public.ecos_fx_rate') IS NOT NULL").fetchone()[0]


def _stored(url: str) -> list[tuple]:
    with psycopg.connect(url, autocommit=True) as c:
        return c.execute("SELECT base_ccy, fx_dd, rate FROM ecos_fx_rate ORDER BY 1, 2").fetchall()


@pytest.mark.parametrize("options", [
    "-c default_transaction_read_only=on",     # 읽기 전용 파일럿(PGOPTIONS)과 같은 세션
    "-c role=opm_ro",                          # 시험 서버의 읽기 전용 역할 — 스키마 생성 권한 없음
])
def test_read_only_session_hits_the_cache(pg17, fresh_pool, monkeypatch, options):
    _create_prod_like(pg17)
    monkeypatch.setenv("DATABASE_URL", _with_options(pg17, options))
    # 종전 코드가 여기서 실패한 이유가 실제로 성립하는지 — 같은 세션에서 DDL 은 거절된다
    with psycopg.connect(_with_options(pg17, options), autocommit=True) as c:
        with pytest.raises((psycopg.errors.ReadOnlySqlTransaction, psycopg.errors.InsufficientPrivilege)):
            c.execute(fx._FX_DDL)
    assert fx._db_get("USD", "20241231") == 1470.0
    assert fx._db_get("USD", "20231231") is None                # 없는 날짜는 미스


def test_read_only_server_answers_from_cache_without_live_sources(pg17, fresh_pool, monkeypatch):
    """도구 경로(fx_to_krw)까지 — 캐시 적중이면 ECOS·야후를 부르지 않고, 캐시 적중이라 쓰기도 시도하지 않는다
    (fx_to_krw 는 rate 가 None 일 때만 `_db_put` 으로 간다 — 읽기 전용 여부와는 무관)."""
    _create_prod_like(pg17)
    monkeypatch.setenv("DATABASE_URL", _with_options(pg17, "-c role=opm_ro -c default_transaction_read_only=on"))
    monkeypatch.setattr(fx, "_MEM", {})
    monkeypatch.setattr(fx, "_MEM_NEG", {})
    ecos, yahoo, put = AsyncMock(return_value=1.0), AsyncMock(return_value=1.0), Mock()
    monkeypatch.setattr(fx, "_ecos", ecos)
    monkeypatch.setattr(fx, "_yahoo", yahoo)
    monkeypatch.setattr(fx, "_db_put", put)
    assert asyncio.run(fx.fx_to_krw("USD", "20241231")) == 1470.0
    ecos.assert_not_called()
    yahoo.assert_not_called()
    put.assert_not_called()


def test_write_from_a_read_only_session_is_swallowed_and_changes_nothing(pg17, monkeypatch):
    _create_prod_like(pg17)
    monkeypatch.setenv("DATABASE_URL", _with_options(pg17, "-c role=opm_ro"))
    fx._db_put("USD", "20250331", 1450.0)                       # 예외가 새지 않는다
    assert _stored(pg17) == [USD_ROW]


def test_missing_table_is_a_miss_and_reading_does_not_create_it(pg17, fresh_pool, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", pg17)                    # 쓰기 가능한 역할이어도 읽기는 표를 만들지 않는다
    assert fx._db_get("USD", "20241231") is None
    assert not _table_exists(pg17)


def test_write_path_creates_the_missing_table_once_then_reads_hit(pg17, fresh_pool, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", pg17)
    fx._db_put(*USD_ROW)
    assert _table_exists(pg17)
    assert _stored(pg17) == [USD_ROW]
    fx._db_put("USD", "20241231", 9999.0)                       # 확정일 값은 덮지 않는다(ON CONFLICT DO NOTHING)
    fx._db_put("JPY", "20241231", 9.5)
    assert _stored(pg17) == [("JPY", "20241231", 9.5), USD_ROW]
    assert fx._db_get("USD", "20241231") == 1470.0


def test_write_path_sends_no_ddl_when_the_table_exists(pg17, monkeypatch):
    """표가 있으면 INSERT 만 — 스키마 생성 권한이 없고 INSERT 권한만 있는 역할로도 저장된다(DDL 을 보냈다면 거절됐다)."""
    _create_prod_like(pg17, rls=False)
    with psycopg.connect(pg17, autocommit=True) as c:
        if not c.execute("SELECT 1 FROM pg_roles WHERE rolname='fx_writer_test'").fetchone():
            c.execute("CREATE ROLE fx_writer_test NOLOGIN")
        c.execute("GRANT SELECT, INSERT ON ecos_fx_rate TO fx_writer_test")
    url = _with_options(pg17, "-c role=fx_writer_test")
    with psycopg.connect(url, autocommit=True) as c:            # 전제: 이 역할은 DDL 을 못 보낸다
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute(fx._FX_DDL)
    monkeypatch.setenv("DATABASE_URL", url)
    fx._db_put("USD", "20250331", 1450.0)
    assert ("USD", "20250331", 1450.0) in _stored(pg17)
