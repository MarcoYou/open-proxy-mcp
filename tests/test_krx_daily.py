"""krx_daily 쓰기·조회 — 네트워크 0 · 운영 DB 0. SQLite 와 가짜 연결로 검사한다.

뷰·권한·트랜잭션처럼 Postgres 가 있어야 하는 부분은 `test_krx_daily_pg.py`(Docker, 없으면 건너뜀)가 맡는다.
"""
from __future__ import annotations

import sqlite3
import sys
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from open_proxy_mcp import krx_daily  # noqa: E402


def _row(code, close="71,000", **kw):
    base = {"ISU_CD": code, "TDD_CLSPRC": close, "TDD_OPNPRC": "70,500", "TDD_HGPRC": "71,500", "TDD_LWPRC": "70,000",
            "ACC_TRDVOL": "12,345,678", "ACC_TRDVAL": "876,543,210,000", "LIST_SHRS": "5,919,637,922",
            "MKTCAP": "420,294,092,462,000", "MKT_NM": "KOSPI", "FLUC_RT": "1.42", "CMPPREVDD_PRC": "1,000"}
    base.update(kw)
    return base


# ── 레코드 만들기 ──────────────────────────────────────────────────────────

def test_daily_records_carry_every_krx_field_in_column_order():
    recs = krx_daily.daily_records("20261016", [_row("005930")], [_row("247540", "250,000")])
    assert krx_daily.DAILY_COLUMNS == ("trd_dd", "ticker", "market", "open", "high", "low", "close", "volume", "value", "list_shrs")
    assert recs == [
        ("20261016", "005930", "KS", 70_500, 71_500, 70_000, 71_000, 12_345_678, 876_543_210_000, 5_919_637_922),
        ("20261016", "247540", "KQ", 70_500, 71_500, 70_000, 250_000, 12_345_678, 876_543_210_000, 5_919_637_922),
    ]
    assert all(len(r) == len(krx_daily.DAILY_COLUMNS) for r in recs)


def test_daily_records_do_not_store_derived_values():
    """시총·등락률은 저장하지 않는다 — 뷰가 계산한다(한 행 안의 곱셈·나눗셈은 계산)."""
    assert "mktcap" not in krx_daily.DAILY_COLUMNS and "chg_pct" not in krx_daily.DAILY_COLUMNS
    assert "MKTCAP" not in krx_daily.UPSERT_DAILY_SQL and "FLUC_RT" not in krx_daily.UPSERT_DAILY_SQL


def test_daily_records_tolerate_blank_dash_and_missing_ids():
    recs = krx_daily.daily_records("20261016", [
        _row("005930", "-", TDD_OPNPRC="", ACC_TRDVOL=None),           # 값이 비었거나 '-' 면 NULL
        _row(None), _row(""), {"TDD_CLSPRC": "1"},                     # 종목코드가 없는 행은 버린다
    ], [])
    assert len(recs) == 1
    assert recs[0][3] is None and recs[0][6] is None and recs[0][7] is None
    assert krx_daily.daily_records("20261016", [], []) == []
    assert krx_daily.daily_records("20261016", None, None) == []


# ── 7거래일 롤링 (SQLite 로 같은 SQL 을 돌린다) ─────────────────────────────────

def _sqlite_exec(con, sql, params=()):
    return con.execute(sql.replace("%s", "?"), params)


def _roll_db():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE krx_daily (trd_dd TEXT NOT NULL, ticker TEXT NOT NULL, market TEXT, open INT, high INT, low INT, "
                "close INT, volume INT, value INT, list_shrs INT, PRIMARY KEY (trd_dd, ticker))")
    return con


def _put(con, dd, rows):
    for rec in krx_daily.daily_records(dd, rows, []):
        _sqlite_exec(con, krx_daily.UPSERT_DAILY_SQL, rec)
    cur = _sqlite_exec(con, krx_daily.PRUNE_DAILY_SQL, (krx_daily.KEEP_TRADING_DAYS,))
    return cur.rowcount


def test_keeps_exactly_the_seven_most_recent_distinct_days():
    con = _roll_db()
    days = ["20261005", "20261006", "20261007", "20261008", "20261012", "20261013", "20261014", "20261015", "20261016"]
    pruned = [_put(con, d, [_row("005930"), _row("000660")]) for d in days]
    kept = [r[0] for r in con.execute("SELECT DISTINCT trd_dd FROM krx_daily ORDER BY 1")]
    assert kept == days[-7:]
    assert pruned == [0] * 7 + [2, 2]                  # 8번째·9번째 날에 가장 오래된 날(2행)이 하나씩 지워진다
    assert con.execute("SELECT count(*) FROM krx_daily").fetchone()[0] == 14


def test_fewer_than_seven_days_deletes_nothing_and_same_day_rewrite_is_idempotent():
    con = _roll_db()
    for d in ("20261014", "20261015", "20261016"):
        assert _put(con, d, [_row("005930")]) == 0
    assert _put(con, "20261016", [_row("005930", "72,000")]) == 0
    assert con.execute("SELECT count(*), max(close) FROM krx_daily").fetchone() == (3, 72_000)


def test_keep_days_constant_is_seven():
    assert krx_daily.KEEP_TRADING_DAYS == 7
    assert "LIMIT %s" in krx_daily.PRUNE_DAILY_SQL and "DISTINCT trd_dd" in krx_daily.PRUNE_DAILY_SQL


def test_lock_key_is_a_fixed_positive_bigint():
    assert krx_daily.REFRESH_LOCK_KEY == int.from_bytes(b"OPMKRXD1", "big")
    assert 0 < krx_daily.REFRESH_LOCK_KEY < 2**63


# ── write_daily: 가짜 연결 ─────────────────────────────────────────────────

class _Cur:
    def __init__(self, conn):
        self.c = conn
        self.rowcount = -1

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        self.c.log.append(("execute", " ".join(sql.split()), params))
        self.rowcount = 5 if sql.lstrip().startswith("DELETE FROM krx_daily") else -1

    def executemany(self, sql, seq):
        if self.c.fail_daily and "krx_daily" in sql:
            raise RuntimeError('relation "krx_daily" does not exist')
        self.c.log.append(("executemany", " ".join(sql.split()), list(seq)))


class _Tx:
    def __init__(self, conn):
        self.c = conn

    def __enter__(self):
        self.c.log.append(("savepoint",))
        return self

    def __exit__(self, et, ev, tb):
        self.c.log.append(("release",) if et is None else ("rollback_to_savepoint",))
        return False                                   # 예외는 그대로 올린다(psycopg 와 같다)


class FakeConn:
    def __init__(self, fail_daily=False):
        self.log, self.commits, self.fail_daily = [], 0, fail_daily

    def cursor(self):
        return _Cur(self)

    def transaction(self):
        return _Tx(self)

    def commit(self):
        self.commits += 1
        self.log.append(("commit",))

    def rollback(self):
        self.log.append(("rollback",))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_write_daily_runs_inside_a_savepoint_and_prunes_to_seven():
    c = FakeConn()
    ok, n, pruned = krx_daily.write_daily(c, "20261016", [_row("005930")], [_row("247540")])
    assert (ok, n, pruned) == (True, 2, 5)
    kinds = [e[0] for e in c.log]
    assert kinds == ["savepoint", "executemany", "execute", "release"]
    assert c.log[1][1].startswith("INSERT INTO krx_daily") and len(c.log[1][2]) == 2
    assert c.log[2][2] == (7,)                          # 프루닝 인자 = 보관 거래일 수
    assert c.commits == 0                               # 커밋은 호출자(krx_weekly 와 같은 트랜잭션)가 한다


def test_write_daily_failure_never_raises_so_the_weekly_write_still_commits(caplog):
    c = FakeConn(fail_daily=True)
    with caplog.at_level("ERROR"):
        assert krx_daily.write_daily(c, "20261016", [_row("005930")], []) == (False, 0, 0)
    assert ("rollback_to_savepoint",) in c.log
    assert any("krx_daily 기록 실패" in r.message for r in caplog.records)    # 조용히 삼키지 않는다


def test_write_daily_with_no_rows_touches_nothing():
    c = FakeConn()
    assert krx_daily.write_daily(c, "20261016", [], []) == (False, 0, 0)
    assert c.log == []


# ── _krx_db_upsert: krx_daily 가 주(主), 닫힌 주만 krx_weekly(부) — 한 트랜잭션 ─────────────

def _patch_connect(monkeypatch, conn):
    import psycopg
    seen = {}

    def fake_connect(url, **kw):
        seen["url"], seen["kw"] = url, kw
        return conn
    monkeypatch.setattr(psycopg, "connect", fake_connect)
    return seen


def _patch_sync(monkeypatch, conn, result):
    """주간 닫기(`krx_daily.sync_weekly`)를 기록만 하는 대역으로 — 실제 SQL 은 pg17 시험(test_krx_weekly_close_pg.py)이 본다."""
    from open_proxy_mcp.services import price_multiple_data as pmd

    def fake_sync(c):
        assert c is conn                                      # 일별 기록과 같은 연결(같은 트랜잭션)
        conn.log.append(("sync_weekly",))
        return result
    monkeypatch.setattr(pmd._krx_daily, "sync_weekly", fake_sync)


def test_upsert_writes_daily_then_closes_weeks_before_the_single_commit(monkeypatch):
    """261018(재설계 5-1 쓰는 쪽): 매 거래일 krx_weekly 의 이번 주 행을 지우고 다시 넣던 것을 멈췄다 — 이 함수는 krx_weekly 에 직접
    아무것도 쓰지 않고, 일별 기록 뒤 같은 트랜잭션에서 주간 닫기를 부른다(닫힌 주만 옮겨 적는다)."""
    from open_proxy_mcp.services import price_multiple_data as pmd
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/x")
    c = FakeConn()
    _patch_connect(monkeypatch, c)
    _patch_sync(monkeypatch, c, krx_daily.WeeklySync(True, ("20261009",), 0))
    res = pmd._krx_db_upsert("20261015", [_row("005930")], [_row("247540", "250,000")])
    assert res == pmd.KrxWrite(True, True, ("20261009",)) and res.daily_ok
    # 바깥 트랜잭션 하나(첫 savepoint·마지막 release) 안에 일별 기록(SAVEPOINT)과 주간 닫기 — 한 커밋
    assert [e[0] for e in c.log] == ["savepoint", "savepoint", "executemany", "execute", "release", "sync_weekly", "release"]
    assert c.log[2][1].startswith("INSERT INTO krx_daily (") and len(c.log[2][2]) == 2
    assert c.log[3][1].startswith("DELETE FROM krx_daily WHERE")
    assert not any("krx_weekly" in (e[1] if len(e) > 1 else "") for e in c.log)   # 이번 주 행을 지우고 다시 넣지 않는다


def test_upsert_commits_the_daily_write_even_when_the_weekly_close_fails(monkeypatch):
    """주간 닫기는 부(副)다 — 실패해도 일별 기록(서빙 기준일)은 커밋되고 성공으로 보고한다. 다음 기록이 다시 닫는다."""
    from open_proxy_mcp.services import price_multiple_data as pmd
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/x")
    c = FakeConn()
    _patch_connect(monkeypatch, c)
    _patch_sync(monkeypatch, c, krx_daily.WeeklySync(False))
    assert pmd._krx_db_upsert("20261016", [_row("005930")], [_row("247540")]) == pmd.KrxWrite(True, False, ())
    assert c.log[0] == ("savepoint",) and c.log[-1] == ("release",)          # 바깥 트랜잭션은 그대로 커밋된다


def test_upsert_commits_nothing_when_the_daily_write_fails(monkeypatch):
    """일별 기록이 주(主)다 — 실패하면 주간 닫기도 부르지 않고 아무것도 커밋하지 않는다(서빙 기준일과 주간 표가 어긋나지 않게)."""
    from open_proxy_mcp.services import price_multiple_data as pmd
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/x")
    c = FakeConn(fail_daily=True)
    _patch_connect(monkeypatch, c)
    _patch_sync(monkeypatch, c, krx_daily.WeeklySync(True))
    assert pmd._krx_db_upsert("20261015", [_row("005930")], [_row("247540")]) == pmd.KrxWrite(False)
    assert ("rollback_to_savepoint",) in c.log and ("sync_weekly",) not in c.log
    assert not any(e[0] == "executemany" for e in c.log)                     # 일별 행도 하나 남지 않는다(SAVEPOINT 로 되돌림)


def test_upsert_returns_false_without_db_or_rows_and_on_connection_error(monkeypatch, caplog):
    from open_proxy_mcp.services import price_multiple_data as pmd
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert pmd._krx_db_upsert("20261015", [_row("005930")], []) == pmd.KrxWrite(False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/x")
    assert pmd._krx_db_upsert("20261015", [], []) == pmd.KrxWrite(False)
    assert pmd._krx_db_upsert("20261015", [{"TDD_CLSPRC": "1"}], []) == pmd.KrxWrite(False)        # 종목코드 없는 행뿐

    import psycopg

    def boom(*a, **k):
        raise psycopg.OperationalError("connection reset")
    monkeypatch.setattr(psycopg, "connect", boom)
    with caplog.at_level("WARNING"):
        assert pmd._krx_db_upsert("20261015", [_row("005930")], []) == pmd.KrxWrite(False)
    assert any("KRX 스냅샷 기록 실패" in r.message for r in caplog.records)


# ── plan_weeks: 어느 주가 닫혔나 (순수 함수) ────────────────────────────────────

def test_plan_weeks_closes_a_week_on_its_friday_or_when_a_later_week_appears():
    W = krx_daily.WeekPlan
    # 이번 주(목요일까지) — 아직 안 닫힘. 지난주는 금요일이 있어 닫힘.
    assert krx_daily.plan_weeks(["20261008", "20261012", "20261013", "20261014", "20261015"]) == [
        W("20261005", "20261011", "20261008", True),        # 금(10/9) 휴장 — 더 늦은 주가 있어 목요일로 닫힌다(R2)
        W("20261012", "20261018", "20261015", False),
    ]
    # 금요일이 저장되면 그 주가 바로 닫힌다(R1 — 토요일 아침 기록)
    assert krx_daily.plan_weeks(["20261015", "20261016"]) == [W("20261012", "20261018", "20261016", True)]
    assert krx_daily.plan_weeks([]) == []


def test_plan_weeks_splits_the_iso_week_at_the_year_boundary():
    """2025-12-30(화)과 2026-01-02(금)은 같은 ISO 주(2026-W01)다. 한 주로 묶으면 12/30 이 지워진다 — 연도에서 나눈다."""
    W = krx_daily.WeekPlan
    assert krx_daily.plan_weeks(["20251229", "20251230"]) == [W("20251229", "20251231", "20251230", False)]   # 12/31 은 휴장 — 아직 열림
    assert krx_daily.plan_weeks(["20251229", "20251230", "20260102"]) == [
        W("20251229", "20251231", "20251230", True),         # 다음 해 첫 거래일이 저장되면 그해 마지막 거래일로 닫힌다
        W("20260101", "20260104", "20260102", True),         # 1/2 는 금요일 — 그 주의 마지막 평일이라 바로 닫힌다
    ]
    # 2024-12-30(월) · 2025-01-02(목): 연말 주는 월요일 하루, 새해 주는 목·금
    assert krx_daily.plan_weeks(["20241230", "20250102"]) == [
        W("20241230", "20241231", "20241230", True), W("20250101", "20250105", "20250102", False)]


def test_weekly_with_latest_only_names_weekly_columns():
    sql = krx_daily.weekly_with_latest("price_dd, ticker, market")
    assert sql.startswith("(SELECT price_dd, ticker, market FROM krx_weekly UNION ALL SELECT d.trd_dd AS price_dd,")
    assert "d.trd_dd = (SELECT max(trd_dd) FROM krx_daily)" in sql and "> (SELECT COALESCE(max(price_dd), '') FROM krx_weekly)" in sql
    assert "d.close * d.list_shrs AS mktcap" in krx_daily.WEEKLY_WITH_LATEST
    with pytest.raises(ValueError):
        krx_daily.weekly_with_latest("price_dd, volume")


# ── read_quote: 한 왕복의 결과를 해석한다 ────────────────────────────────────────

def _quote_result(dd="20261016", lo="20261008", hi="20261016", present=True, with_row=True, prev=100_000, prev_dd="20261015"):
    row = ("KS", 101_000, 104_000, 100_500, 103_000, 777, 99_999, 10, 1_030_000, prev_dd if prev else None, prev,
           3_000 if prev else None, 3.0 if prev else None) if with_row else (None,) * 13
    return [(dd, lo, hi, present) + row]


def test_read_quote_maps_the_single_row(monkeypatch):
    calls = []
    monkeypatch.setattr("open_proxy_mcp.db.pg_rows", lambda sql, params=(): calls.append((sql, params)) or _quote_result())
    got = krx_daily.read_quote("005930", " 20261016 ")
    assert len(calls) == 1 and calls[0][1] == ("20261016", "005930")        # 한 번만 묻는다 · as_of 앞뒤 공백 제거
    assert got.ok and got.day == "20261016" and got.window == ("20261008", "20261016") and got.day_present
    assert got.row["close"] == 103_000 and got.row["prev_close"] == 100_000 and got.row["market"] == "KS"
    assert got.row["prev_dd"] == "20261015"
    assert set(got.row) >= {"open", "high", "low", "close", "volume", "value", "list_shrs", "mktcap", "prev_dd", "prev_close", "chg", "chg_pct"}


def test_read_quote_row_none_when_the_day_has_no_such_ticker(monkeypatch):
    monkeypatch.setattr("open_proxy_mcp.db.pg_rows", lambda sql, params=(): _quote_result(with_row=False))
    got = krx_daily.read_quote("000000", "")
    assert got.ok and got.day_present and got.row is None


def test_read_quote_not_ok_when_db_unreadable(monkeypatch):
    monkeypatch.setattr("open_proxy_mcp.db.pg_rows", lambda sql, params=(): None)     # 미설정·장애·표 없음
    got = krx_daily.read_quote("005930", "")
    assert got.ok is False and got.row is None and not got.day_present


def test_read_quote_sql_is_one_statement_with_two_params():
    assert krx_daily.QUOTE_SQL.count("%s") == 2
    assert ";" not in krx_daily.QUOTE_SQL
    assert "krx_daily_v" in krx_daily.QUOTE_SQL and "LATERAL" in krx_daily.QUOTE_SQL


def test_ddl_script_prints_by_default_and_never_applies_without_the_flag(capsys, monkeypatch):
    """스키마 스크립트는 기본이 「출력만」이다 — 실수로 DB 를 만지지 않는다. --no-tx 는 BEGIN/COMMIT 없는 본문."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("krx_daily_ddl", ROOT / "scripts" / "krx_daily_ddl.py")
    ddl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ddl)
    applied = []
    monkeypatch.setattr(ddl, "apply", lambda url: applied.append(url))
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/x")
    monkeypatch.setattr(sys, "argv", ["krx_daily_ddl.py"])
    assert ddl.main() == 0
    out = capsys.readouterr().out
    assert out.startswith("BEGIN;") and out.rstrip().endswith("COMMIT;") and not applied
    for needle in ("CREATE TABLE IF NOT EXISTS krx_daily", "CREATE OR REPLACE VIEW krx_daily_v", "CREATE OR REPLACE VIEW price_latest",
                   "ENABLE ROW LEVEL SECURITY", "CREATE POLICY krx_daily_read", "REVOKE ALL ON krx_daily, krx_daily_v, price_latest FROM anon, authenticated"):
        assert needle in out
    grants = [ln for ln in out.splitlines() if ln.upper().startswith("GRANT")]
    assert grants == ["GRANT SELECT ON krx_daily, krx_daily_v, price_latest TO opm_ro;"]     # 새로 주는 권한은 읽기 전용 역할 하나뿐
    assert not any(role in ln for ln in grants for role in ("anon", "authenticated"))
    monkeypatch.setattr(sys, "argv", ["krx_daily_ddl.py", "--no-tx"])
    assert ddl.main() == 0
    body = capsys.readouterr().out
    assert "BEGIN" not in body and "COMMIT" not in body and body.startswith("CREATE TABLE") and not applied
    monkeypatch.setattr(sys, "argv", ["krx_daily_ddl.py", "--apply"])
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None, raising=False)
    assert ddl.main() == 0 and applied == ["postgresql://u:p@localhost/x"]     # 명시했을 때만 적용
