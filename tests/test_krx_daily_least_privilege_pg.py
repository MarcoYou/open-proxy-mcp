"""최소 권한 서버 계정으로 일별 시세 쓰기 경로 전체가 도는가 — Postgres 17.

서버 DB 계정을 표 소유자에서 최소 권한 계정(소유자 아님 · BYPASSRLS 없음 · RLS 정책을 따름)으로 바꾸면 세 군데가 달라진다
(261019 로컬 PG17 실측):
  ① RLS 가 켜진 `krx_px` 에 `COPY FROM` 을 못 한다(`COPY FROM not supported with row-level security`) → 쓰는 쪽은 한 문장 INSERT.
  ② `brin_desummarize_range` 는 색인 소유자만 된다 — PG17 MAINTAIN 권한으로도 `must be owner of index` → 소유자 함수
     `krx_px_brin_desummarize(date[])`(`scripts/krx_daily_ddl.py`)를 거친다.
  ③ `VACUUM` 은 권한(MAINTAIN)이 없으면 오류 없이 건너뛰고 서버 경고만 낸다 → 갱신기가 그 경고를 로그로 옮긴다.
이 파일은 그 계정의 모양(`_grant_writer` — 쓰는 표마다 필요한 권한·RLS 정책만)으로 갱신기가 하는 일 — 새 종목 번호·달력·사실값·
주 닫는 날·보관 창 정리(콜드 날 지우기·BRIN 요약)·주간 닫기·휴장 기록·청소 — 을 돌린다. Docker 가 없으면 건너뛴다.
"""
from __future__ import annotations

import logging
import pathlib
import sys
import urllib.parse
from datetime import date

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

psycopg = pytest.importorskip("psycopg")

from test_krx_daily_pg import KRX_WEEKLY_DDL, _ddl, _row, put_daily  # noqa: E402 — 스키마·KRX 행 모양 재사용

from open_proxy_mcp import krx_daily  # noqa: E402
from open_proxy_mcp.services import krx_refresher  # noqa: E402
from open_proxy_mcp.services import price_multiple_data as pmd  # noqa: E402

WRITER = "opm_writer_t"
FN = f"{krx_daily.PRUNE_DESUMMARIZE_FN}(date[])"


def _as(url: str, role: str) -> str:
    return f"{url}?options={urllib.parse.quote(f'-c role={role}')}"


def _grant_writer(url: str, *, maintain: bool = True, fn: bool = True) -> str:
    """최소 권한 서버 계정의 모양 — 표 소유자 아님 · RLS 를 따름 · 쓰는 표에만 그 일에 필요한 권한과 정책.
    `krx_day` 는 칸 단위(콜드 표시 `cold` 는 못 바꾼다), `krx_ticker` 는 종목코드만 넣는다(번호는 IDENTITY — 시퀀스 권한 없이 된다)."""
    with psycopg.connect(url, autocommit=True) as c:
        if not c.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (WRITER,)).fetchone():
            c.execute(f"CREATE ROLE {WRITER} NOLOGIN NOINHERIT NOBYPASSRLS")
        x = c.execute
        x(f"GRANT USAGE ON SCHEMA public TO {WRITER}")
        x(f"GRANT SELECT ON krx_px, krx_day, krx_ticker, krx_daily, krx_daily_v, price_latest, krx_weekly TO {WRITER}")
        x(f"GRANT INSERT (ticker) ON krx_ticker TO {WRITER}")
        x(f"GRANT INSERT (trd_dd, dd, n, closed), UPDATE (n, closed, wk_close), DELETE ON krx_day TO {WRITER}")
        x(f"GRANT INSERT, DELETE ON krx_px TO {WRITER}")
        x(f"GRANT INSERT, DELETE ON krx_weekly TO {WRITER}")
        if maintain:
            x(f"GRANT MAINTAIN ON krx_px, krx_day TO {WRITER}")
        if fn:
            x(f"GRANT EXECUTE ON FUNCTION {FN} TO {WRITER}")
        for t, cmd, expr in (("krx_ticker", "INSERT", "WITH CHECK (true)"), ("krx_day", "INSERT", "WITH CHECK (true)"),
                             ("krx_day", "UPDATE", "USING (true) WITH CHECK (true)"), ("krx_day", "DELETE", "USING (true)"),
                             ("krx_px", "INSERT", "WITH CHECK (true)"), ("krx_px", "DELETE", "USING (true)")):
            x(f"CREATE POLICY w_{cmd.lower()} ON {t} FOR {cmd} TO {WRITER} {expr}")
    return _as(url, WRITER)


@pytest.fixture
def ldb(pg17, monkeypatch):
    """스키마(소유자) + 최소 권한 계정. 반환 (소유자 URL, 쓰는 계정 URL). 서버 읽기(`pg_rows`)·쓰기(`DATABASE_URL`)는 쓰는 계정으로."""
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(KRX_WEEKLY_DDL)
    _ddl().apply(pg17)
    w = _grant_writer(pg17)
    monkeypatch.setenv("DATABASE_URL", w)
    import open_proxy_mcp.db as dbm
    monkeypatch.setattr(dbm, "_pool", None)
    monkeypatch.setattr(dbm, "_pool_disabled_until", float("inf"))
    return pg17, w


def _q(url, sql, params=()):
    with psycopg.connect(url) as c:
        return c.execute(sql, params).fetchall()


def _day(dd: str, n: int = 4):
    base = int(dd[-4:])
    return [_row(f"K{i:05d}", base * 10 + i, cmp=0) for i in range(n)], [_row(f"Q{i:05d}", base + i, cmp=0) for i in range(n)]


def test_writer_is_not_owner_and_is_subject_to_rls_so_copy_is_refused(ldb):
    """시험 전제 — 이 계정에 RLS 가 걸리고(COPY 가 막힌다), 그래서 쓰는 쪽은 COPY 를 쓰지 않는다."""
    owner, w = ldb
    with psycopg.connect(w) as c:
        assert c.execute("SELECT current_user, row_security_active('krx_px')").fetchone() == (WRITER, True)
        with pytest.raises(psycopg.errors.FeatureNotSupported, match="row-level security"):
            with c.cursor() as cur, cur.copy("COPY krx_px (trd_dd, tid, market) FROM STDIN") as cp:
                cp.write_row((date(2026, 10, 16), 1, "S"))


def test_writer_writes_a_day_with_new_tickers_and_reads_it_back_through_the_views(ldb):
    owner, w = ldb
    with psycopg.connect(w) as c:
        assert krx_daily.write_daily(c, "20261015", *_day("20261015")) == (True, 8, 0)
        assert krx_daily.write_daily(c, "20261016", *_day("20261016", 5)) == (True, 10, 0)    # 새 종목 2 — 번호는 IDENTITY
        c.commit()
    assert _q(owner, "SELECT dd, n, closed FROM krx_day ORDER BY 1") == [("20261015", 8, False), ("20261016", 10, False)]
    assert _q(owner, "SELECT count(*), count(DISTINCT tid) FROM krx_px") == [(18, 10)]
    q = krx_daily.read_quote("K00004", "20261016")
    assert q.ok and q.row["close"] == 1016 * 10 + 4 and q.day == "20261016"
    # 같은 날을 다시 쓰면 그 날 행을 통째로 바꾼다(DELETE + INSERT, 정책 안에서)
    with psycopg.connect(w) as c:
        assert krx_daily.write_daily(c, "20261016", *_day("20261016", 3)) == (True, 6, 0)
        c.commit()
    assert _q(owner, "SELECT n FROM krx_day WHERE dd='20261016'") == [(6,)]
    assert _q(owner, "SELECT count(*) FROM krx_px WHERE trd_dd='2026-10-16'") == [(6,)]


def test_writer_prunes_cold_days_and_desummarizes_through_the_owner_function(ldb, caplog):
    owner, w = ldb
    with psycopg.connect(owner) as c:                       # 창 밖 옛 날 둘(콜드) — 소유자가 넣는다(백필·이관 몫)
        put_daily(c, [(d, f"K{i:05d}", "KS", 100 + i, 1_000) for d in ("20250106", "20250107") for i in range(4)])
        c.execute("UPDATE krx_day SET cold = true WHERE dd IN ('20250106', '20250107')")
        c.commit()
    with psycopg.connect(w) as c:                           # 함수가 실제로 요약을 지운다(행이 있는 범위 수)
        assert c.execute(f"SELECT {krx_daily.PRUNE_DESUMMARIZE_FN}(%s::date[])", ([date(2025, 1, 6)],)).fetchone()[0] >= 1
        c.rollback()
    with caplog.at_level(logging.WARNING, logger="open_proxy_mcp.krx_daily"):
        with psycopg.connect(w) as c:
            ok, n, pruned = krx_daily.write_daily(c, "20260116", *_day("20260116"))
            c.commit()
    assert (ok, pruned) == (True, 8)
    assert not [r for r in caplog.records if "BRIN 요약 지우기 실패" in r.getMessage()]
    assert _q(owner, "SELECT count(*) FROM krx_day WHERE dd IN ('20250106','20250107')") == [(0,)]
    # 대조군: 함수 실행 권한이 없으면 직접 부르는 길로 떨어져 「소유자가 아님」으로 실패한다 — 그래도 지우기는 계속한다
    with psycopg.connect(owner, autocommit=True) as c:
        c.execute(f"REVOKE EXECUTE ON FUNCTION {FN} FROM {WRITER}")
    with psycopg.connect(owner) as c:
        put_daily(c, [("20250108", f"K{i:05d}", "KS", 100 + i, 1_000) for i in range(4)])
        c.execute("UPDATE krx_day SET cold = true WHERE dd = '20250108'")
        c.commit()
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="open_proxy_mcp.krx_daily"):
        with psycopg.connect(w) as c:
            ok, _, pruned = krx_daily.write_daily(c, "20260119", *_day("20260119"))
            c.commit()
    assert ok and pruned == 4
    assert any("BRIN 요약 지우기 실패" in r.getMessage() for r in caplog.records)


def test_writer_runs_the_refresher_write_with_the_weekly_close_and_a_closed_day(ldb):
    """갱신기의 쓰기(`_krx_db_upsert` — 일별 기록 + 같은 트랜잭션의 주간 닫기)와 휴장 기록이 이 계정으로 된다."""
    owner, w = ldb
    for dd in ("20261012", "20261013", "20261014", "20261015", "20261016", "20261019"):
        res = pmd._krx_db_upsert(dd, *_day(dd))
        assert res.daily_ok and res.weekly_ok, (dd, res)
    assert _q(owner, "SELECT price_dd, count(*) FROM krx_weekly GROUP BY 1") == [("20261016", 8)]
    assert _q(owner, "SELECT dd FROM krx_day WHERE wk_close") == [("20261016",)]
    with psycopg.connect(w) as c:
        assert krx_daily.record_closed(c, "20261009")
        c.commit()
    assert _q(owner, "SELECT closed, n FROM krx_day WHERE dd='20261009'") == [(True, 0)]


def test_vacuum_needs_maintain_and_the_refresher_says_so_when_it_is_missing(pg17, monkeypatch, caplog):
    """권한 없는 VACUUM 은 오류 없이 건너뛴다 — 갱신기가 서버 경고를 로그로 옮겨 조용히 넘어가지 않는다. MAINTAIN 이 있으면 경고 0."""
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(KRX_WEEKLY_DDL)
    _ddl().apply(pg17)
    w = _grant_writer(pg17, maintain=False)
    monkeypatch.setenv("DATABASE_URL", w)
    with caplog.at_level(logging.WARNING, logger="open_proxy_mcp.services.krx_refresher"):
        krx_refresher._vacuum()
    msgs = [r.getMessage() for r in caplog.records if "VACUUM" in r.getMessage()]
    assert msgs and "MAINTAIN" in msgs[0] and "krx_px" in msgs[0]
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(f"GRANT MAINTAIN ON krx_px, krx_day TO {WRITER}")
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="open_proxy_mcp.services.krx_refresher"):
        krx_refresher._vacuum()
    assert not [r for r in caplog.records if "VACUUM" in r.getMessage()]


def test_writer_cannot_flip_cold_truncate_or_desummarize_directly(ldb):
    """권한 밖은 막힌다 — 콜드 표시(보관본을 올린 쪽 몫)·TRUNCATE·BRIN 요약 직접 지우기(MAINTAIN 이 있어도 소유자만)."""
    owner, w = ldb
    with psycopg.connect(w) as c:
        assert krx_daily.write_daily(c, "20261016", *_day("20261016"))[0]
        c.commit()
    for sql in ("UPDATE krx_day SET cold = true", "TRUNCATE krx_px",
                f"SELECT brin_desummarize_range('{krx_daily.BRIN_INDEX}'::regclass, 0)", "UPDATE krx_px SET close = 0"):
        with psycopg.connect(w) as c:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(sql)
    with psycopg.connect(owner) as c:                         # 함수는 SECURITY DEFINER · PUBLIC·anon 실행 권한 없음
        secdef, pub, anon = c.execute(
            "SELECT prosecdef, has_function_privilege('public', %s, 'EXECUTE'), has_function_privilege('anon', %s, 'EXECUTE') "
            "FROM pg_proc WHERE oid = %s::regprocedure", (FN, FN, FN)).fetchone()
    assert secdef and not pub and not anon
