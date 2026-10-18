"""지수 구성종목 원장 — Postgres 17 에서 표·권한·쓰기·읽기를 증명한다 (261018). Docker 가 없으면 건너뛴다.

① DDL 은 멱등이고 RLS 를 켜며, anon/authenticated 에는 권한이 없고 opm_ro 는 정책을 거쳐 읽는다.
② 적재(`scripts/krx_index_members.settle`)는 처음엔 묶음을 넣고, 구성이 그대로면 확인일만 올리고(행 수 그대로), 바뀌면 새 묶음을 넣는다.
   옛 날짜로 다른 구성을 쓰려 하면 거절한다. 확인 실패면 아무것도 쓰지 않는다.
③ 서버 읽기(`index_members.latest` + `db.pg_rows`)는 가장 늦은 묶음만 본다.
"""
from __future__ import annotations

import pytest

psycopg = pytest.importorskip("psycopg")

from open_proxy_mcp import index_members as im  # noqa: E402
from open_proxy_mcp.db import pg_rows  # noqa: E402
from scripts import krx_index_members as S  # noqa: E402
from scripts import krx_index_members_ddl as ddl  # noqa: E402

K200 = S.SOURCES[0]


def _ok(members):
    return S.Outcome(K200, members=frozenset(members), check=S.Check(ok=True, sums=(1, 1, 1), krx=(1, 1, 1)))


def _rows(url):
    with psycopg.connect(url) as c:
        return c.execute("SELECT snap_dd, checked_dd, ticker FROM krx_index_members ORDER BY 1, 3").fetchall()


@pytest.fixture
def db(pg17, monkeypatch):
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(ddl.STATEMENTS)
        c.execute(ddl.STATEMENTS)              # 멱등
    monkeypatch.setenv("DATABASE_URL", pg17)
    import open_proxy_mcp.db as dbm
    monkeypatch.setattr(dbm, "_pool", None)
    monkeypatch.setattr(dbm, "_pool_disabled_until", float("inf"))
    return pg17


def test_access_control_matches_other_tables(db):
    with psycopg.connect(db, autocommit=True) as c:
        assert c.execute("SELECT relrowsecurity FROM pg_class WHERE relname='krx_index_members'").fetchone()[0] is True
        for role in ("anon", "authenticated"):
            for priv in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                assert c.execute("SELECT has_table_privilege(%s,'krx_index_members',%s)", (role, priv)).fetchone()[0] is False
        assert c.execute("SELECT has_table_privilege('opm_ro','krx_index_members','SELECT')").fetchone()[0] is True
        assert c.execute("SELECT has_table_privilege('opm_ro','krx_index_members','INSERT')").fetchone()[0] is False
        c.execute("INSERT INTO krx_index_members (index_code, snap_dd, ticker, checked_dd) VALUES ('kospi200','20261016','005930','20261016')")
        c.execute("SET ROLE opm_ro")           # BYPASSRLS 없음 — 정책(USING true)을 거쳐 읽혀야 한다
        assert c.execute("SELECT count(*) FROM krx_index_members").fetchone()[0] == 1
        c.execute("RESET ROLE")
        with pytest.raises(psycopg.errors.CheckViolation):
            c.execute("INSERT INTO krx_index_members (index_code, snap_dd, ticker, checked_dd) VALUES ('kospi200','20261016','000660','20261015')")


def test_insert_then_touch_then_new_snapshot(db, capsys):
    assert S.settle([_ok({"005930", "000660"})], "20261015", db, False) == 0
    assert _rows(db) == [("20261015", "20261015", "000660"), ("20261015", "20261015", "005930")]
    # 같은 구성 · 다음 거래일 → 확인일만 오른다(행 수 그대로)
    assert S.settle([_ok({"005930", "000660"})], "20261016", db, False) == 0
    assert _rows(db) == [("20261015", "20261016", "000660"), ("20261015", "20261016", "005930")]
    # 같은 날 다시 → 아무것도 안 한다
    assert S.settle([_ok({"005930", "000660"})], "20261016", db, False) == 0
    assert "noop" in capsys.readouterr().out
    # 구성이 바뀌었다 → 새 묶음. 옛 묶음은 이력으로 남는다
    assert S.settle([_ok({"005930", "000080"})], "20261019", db, False) == 0
    assert _rows(db)[-2:] == [("20261019", "20261019", "000080"), ("20261019", "20261019", "005930")]
    assert len(_rows(db)) == 4
    m = im.latest("kospi200", pg_rows)
    assert m.tickers == {"005930", "000080"} and m.snap_dd == "20261019" and m.checked_dd == "20261019"
    assert im.latest("kosdaq150", pg_rows) is None


def test_refuses_to_rewrite_history_and_writes_nothing_when_unverified(db, capsys):
    assert S.settle([_ok({"005930", "000660"})], "20261016", db, False) == 0
    before = _rows(db)
    assert S.settle([_ok({"005930", "000080"})], "20261016", db, False) == 0     # 같은 날 다른 구성 — 거절(경고)
    assert "::warning::" in capsys.readouterr().out
    bad = S.Outcome(K200, members=frozenset({"005930"}), check=S.Check(ok=False, sums=(1, 1, 1), krx=(2, 2, 2)))
    assert S.settle([bad], "20261019", db, False) == 0                           # 직전 확인 3일 — 전환일 경고
    assert _rows(db) == before


def test_dry_run_opens_read_only_and_writes_nothing(db):
    assert S.settle([_ok({"005930"})], "20261016", db, True) == 0
    assert _rows(db) == []
