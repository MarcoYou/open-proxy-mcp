"""scripts/div_indexes_ddl.py — div_declared 인덱스 둘(재설계 4단계 T3).

핵심 계약 셋:
  ① `CREATE INDEX CONCURRENTLY` 는 트랜잭션 안에서 못 돈다 → `--apply` 는 **자동 커밋 접속으로 문장을 하나씩** 보낸다.
  ② 전부 멱등이다. 끊겨서 무효(INVALID)가 된 인덱스는 `IF NOT EXISTS` 가 그냥 지나치므로 스크립트가 찾아 다시 만든다.
  ③ 만든 인덱스를 배당 도구의 실제 질의가 쓴다(연도 범위 MIN/MAX · 회사 시계열).

①②의 SQL 쪽은 Postgres 17 에서만 검증된다(`tests/conftest.py` 의 `pg17` — Docker 가 없으면 그 테스트만 건너뜀).
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

psycopg = pytest.importorskip("psycopg")


def _ddl():
    spec = importlib.util.spec_from_file_location("div_indexes_ddl", ROOT / "scripts" / "div_indexes_ddl.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── DB 없이 ───────────────────────────────────────────────────────────────────────────────────────

def test_rendered_sql_is_two_concurrent_idempotent_statements_without_a_transaction():
    ddl = _ddl()
    sql = ddl.render()
    assert "BEGIN" not in sql and "COMMIT" not in sql, "CONCURRENTLY 는 트랜잭션 안에서 못 돈다"
    assert sql.count("CREATE INDEX CONCURRENTLY IF NOT EXISTS") == 2
    assert "ON div_declared (corp_code, reprt_code, bsns_year)" in sql
    assert "ON div_declared (bsns_year)" in sql
    stmts = [s for s in sql.split(";") if "CREATE INDEX" in s]
    assert len(stmts) == 2
    assert [n for n, _ in ddl.INDEXES] == ["div_declared_corp_rep_yr", "div_declared_yr"]


def test_print_mode_never_touches_the_database(monkeypatch, capsys):
    ddl = _ddl()
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("접속하면 안 된다")))
    monkeypatch.setattr(sys, "argv", ["div_indexes_ddl.py"])
    assert ddl.main() == 0
    assert "CREATE INDEX CONCURRENTLY" in capsys.readouterr().out


def test_apply_without_database_url_refuses(monkeypatch, capsys):
    ddl = _ddl()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)       # 개발자 .env 에 운영 주소가 있어도 이 테스트는 읽지 않는다
    monkeypatch.setattr(sys, "argv", ["div_indexes_ddl.py", "--apply"])
    assert ddl.main() == 2
    assert "DATABASE_URL" in capsys.readouterr().err


class _Cur:
    def __init__(self, row): self._row = row
    def fetchone(self): return self._row


class _FakeCon:
    """접속 흉내 — 보낸 문장과 접속 옵션을 기록하고, 인덱스 상태 조회에는 지금까지 만든 것을 답한다."""
    def __init__(self, log, kwargs, existing):
        self.log, self.kwargs, self.existing = log, kwargs, dict(existing)
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def execute(self, sql, params=None):
        self.log.append(" ".join(sql.split()))
        if "FROM pg_index" in sql:
            st = self.existing.get(params[0])
            return _Cur(None if st is None else (st, st, 4096))
        if sql.lstrip().startswith("CREATE INDEX"):
            name = sql.split("IF NOT EXISTS")[1].split()[0]
            self.existing.setdefault(name, True)
        if sql.lstrip().startswith("DROP INDEX"):
            self.existing.pop(sql.split("EXISTS")[1].strip(), None)
        return _Cur(None)


def test_apply_uses_autocommit_and_sends_one_statement_per_execute(monkeypatch):
    ddl = _ddl()
    log, seen = [], {}

    def fake_connect(url, **kwargs):
        seen.update(kwargs)
        return _FakeCon(log, kwargs, existing={})
    monkeypatch.setattr(psycopg, "connect", fake_connect)
    out = []
    result = ddl.apply("postgresql://x/y", out=out.append)
    assert seen.get("autocommit") is True, "자동 커밋이 아니면 CONCURRENTLY 가 실패한다"
    creates = [s for s in log if s.startswith("CREATE INDEX")]
    assert len(creates) == 2 and all(";" not in s for s in creates), "문장마다 따로 보낸다 — 여러 문장을 한 번에 보내면 암묵 트랜잭션이다"
    assert all(ok for _, ok, _ in result) and len(result) == 2


def test_apply_replaces_an_invalid_leftover_instead_of_skipping_it(monkeypatch):
    ddl = _ddl()
    log = []
    monkeypatch.setattr(psycopg, "connect", lambda url, **k: _FakeCon(log, k, existing={"div_declared_yr": False}))
    out = []
    ddl.apply("postgresql://x/y", out=out.append)
    drops = [s for s in log if s.startswith("DROP INDEX")]
    assert drops == ["DROP INDEX CONCURRENTLY IF EXISTS div_declared_yr"]
    assert any("무효" in line and "div_declared_yr" in line for line in out)
    # 정상 인덱스는 건드리지 않는다
    log.clear()
    monkeypatch.setattr(psycopg, "connect", lambda url, **k: _FakeCon(log, k, existing={"div_declared_yr": True, "div_declared_corp_rep_yr": True}))
    ddl.apply("postgresql://x/y", out=lambda *_: None)
    assert not [s for s in log if s.startswith("DROP INDEX")]


# ── Postgres 17 ────────────────────────────────────────────────────────────────────────────────────

_TABLE = """CREATE TABLE div_declared (
  corp_code text, name text, tickers text, bsns_year integer, reprt_code text, reprt_label text, stock_knd_raw text,
  stock_kind text, dps_krw double precision, div_total_krw double precision, unit_raw text, payout_pct double precision,
  rcept_no text, stlm_dt text, se_raw text, status text)"""


def _seed(url, n_corps=300):
    """운영 분포를 닮은 표: 회사 × 사업연도 6 × 보고서 4 × 종류 1~2."""
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(_TABLE)
        c.execute(
            "INSERT INTO div_declared (corp_code, name, tickers, bsns_year, reprt_code, stock_kind, dps_krw, div_total_krw, status) "
            "SELECT lpad(g::text, 8, '0'), 'n' || g, lpad(g::text, 6, '0'), y, r, k, 100, 1e9, '확정' "
            "FROM generate_series(1, %s) g, generate_series(2020, 2025) y, unnest(ARRAY['11011','11012','11013','11014']) r, "
            "unnest(ARRAY['보통','우선']) k", (n_corps,))
        c.execute("ANALYZE div_declared")


def _plan(c, sql, params=None):
    c.execute("SET enable_seqscan = off")            # 「쓸 수 있는가」를 본다 — 비용 선택이 아니라
    return "\n".join(r[0] for r in c.execute("EXPLAIN " + sql, params).fetchall())


def test_ddl_creates_valid_indexes_and_is_idempotent(pg17):
    ddl = _ddl()
    _seed(pg17)
    first = ddl.apply(pg17, out=lambda *_: None)
    second = ddl.apply(pg17, out=lambda *_: None)                   # 두 번째도 오류 없이 같은 결과
    assert [(n, ok) for n, ok, _ in first] == [(n, ok) for n, ok, _ in second] == [
        ("div_declared_corp_rep_yr", True), ("div_declared_yr", True)]
    with psycopg.connect(pg17) as c:
        defs = dict(c.execute("SELECT indexname, indexdef FROM pg_indexes WHERE tablename='div_declared'").fetchall())
        assert "(corp_code, reprt_code, bsns_year)" in defs["div_declared_corp_rep_yr"]
        assert "(bsns_year)" in defs["div_declared_yr"]
        bad = c.execute("SELECT count(*) FROM pg_index WHERE indrelid='div_declared'::regclass AND NOT (indisvalid AND indisready)").fetchone()[0]
        assert bad == 0


def test_the_year_range_and_firm_queries_can_use_the_new_indexes(pg17):
    """도구의 실제 질의 모양 그대로 — 인덱스가 있으면 계획이 그것을 고를 수 있어야 한다."""
    ddl = _ddl()
    _seed(pg17)
    ddl.apply(pg17, out=lambda *_: None)
    with psycopg.connect(pg17, autocommit=True) as c:
        plan = _plan(c, "SELECT MIN(bsns_year), MAX(bsns_year) FROM div_declared")
        assert "div_declared_yr" in plan and "Seq Scan" not in plan, plan
        plan = _plan(c, "SELECT bsns_year, stock_kind, stock_knd_raw, dps_krw, div_total_krw, payout_pct, status, rcept_no, stlm_dt "
                        "FROM div_declared WHERE corp_code = %s AND reprt_code = %s AND bsns_year BETWEEN %s AND %s "
                        "ORDER BY bsns_year DESC, stock_kind, stock_knd_raw", ("00000007", "11011", 2020, 2025))
        assert "div_declared_corp_rep_yr" in plan and "Seq Scan" not in plan, plan
        # 결과는 인덱스 유무와 무관하다
        rows = c.execute("SELECT MIN(bsns_year), MAX(bsns_year) FROM div_declared").fetchone()
        assert rows == (2020, 2025)


def test_a_failed_concurrent_build_leaves_an_invalid_index_and_apply_repairs_it(pg17):
    ddl = _ddl()
    _seed(pg17)
    ddl.apply(pg17, out=lambda *_: None)
    with psycopg.connect(pg17, autocommit=True) as c:
        # 중간에 끊긴 CONCURRENTLY 빌드가 남기는 상태를 만든다(실제로는 같은 표시가 붙는다)
        c.execute("UPDATE pg_index SET indisvalid = false WHERE indexrelid = 'div_declared_yr'::regclass")
        assert c.execute("SELECT indisvalid FROM pg_index WHERE indexrelid='div_declared_yr'::regclass").fetchone()[0] is False
        c.execute("CREATE INDEX CONCURRENTLY IF NOT EXISTS div_declared_yr ON div_declared (bsns_year)")   # IF NOT EXISTS 는 그냥 지나친다
        assert c.execute("SELECT indisvalid FROM pg_index WHERE indexrelid='div_declared_yr'::regclass").fetchone()[0] is False
    out = []
    result = ddl.apply(pg17, out=out.append)
    assert all(ok for _, ok, _ in result)
    assert any("무효" in line for line in out)


def test_sending_both_statements_in_one_string_fails_which_is_why_apply_goes_one_by_one(pg17):
    ddl = _ddl()
    _seed(pg17, n_corps=5)
    with psycopg.connect(pg17, autocommit=True) as c:
        with pytest.raises(psycopg.errors.ActiveSqlTransaction):
            c.execute(";\n".join(stmt for _, stmt in ddl.INDEXES))
    # 같은 접속으로 하나씩 보내면 된다
    with psycopg.connect(pg17, autocommit=True) as c:
        for _, stmt in ddl.INDEXES:
            c.execute(stmt)
