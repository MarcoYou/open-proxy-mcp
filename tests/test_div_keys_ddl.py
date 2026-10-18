"""scripts/div_keys_ddl.py — 배당 원장 5표 기본키(재설계 v02 7단계).

핵심 계약:
  ① 넷은 이미 있는 UNIQUE 인덱스 `<표>_key` 를 **그대로** 기본키로 올린다 — 새 인덱스가 생기지 않고 이름도 그대로다.
  ② `div_declared` 는 원문의 똑같은 줄을 지우지 않고 `line_no`(보고서 안 몇 번째 줄)를 더해 키를 만든다. 채우는 순서는 지금 저장 순서다.
  ③ 표마다 한 트랜잭션 — 한 표가 막혀도(잠금 대기 3초 초과·NULL 키·일부만 찬 line_no) 그 표만 그대로 두고 나머지는 한다.
  ④ 멱등이다. `--check` 는 읽기 전용 세션에서도 돈다.
  ⑤ 기본키가 회사 시계열 질의를 맡는다 — 그래서 지운 `div_declared_corp_rep_yr` 를 `div_indexes_ddl.py` 가 다시 만들지 않는다.

SQL 쪽은 Postgres 17 에서만 검증된다(`tests/conftest.py` 의 `pg17` — Docker 가 없으면 그 테스트만 건너뜀).
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
import threading
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

psycopg = pytest.importorskip("psycopg")


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _ddl():
    return _load("div_keys_ddl")


# ── DB 없이 ───────────────────────────────────────────────────────────────────────────────────────

def test_rendered_sql_promotes_existing_unique_indexes_and_keys_div_declared_by_line():
    ddl = _ddl()
    sql = ddl.render()
    for table, index, _ in ddl.PROMOTE:
        assert f"ALTER TABLE {table} ADD CONSTRAINT {index} PRIMARY KEY USING INDEX {index}" in sql
    assert "ADD COLUMN IF NOT EXISTS line_no smallint" in sql
    assert "PRIMARY KEY (corp_code, reprt_code, bsns_year, line_no)" in sql
    assert "DROP INDEX IF EXISTS div_declared_corp_rep_yr" in sql
    assert sql.count("BEGIN;") == sql.count("COMMIT;") == 5
    assert sql.count("lock_timeout = '3s'") == 5
    # 이름을 _pkey 로 바꾸지 않는다 — 옛 적재의 `CREATE UNIQUE INDEX IF NOT EXISTS <표>_key` 가 계속 그냥 지나가야 한다
    assert "_pkey" not in sql


def test_print_mode_never_touches_the_database(monkeypatch, capsys):
    ddl = _ddl()
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("접속하면 안 된다")))
    monkeypatch.setattr(sys, "argv", ["div_keys_ddl.py"])
    assert ddl.main() == 0
    assert "PRIMARY KEY" in capsys.readouterr().out


@pytest.mark.parametrize("flag", ["--apply", "--check"])
def test_db_modes_without_database_url_refuse(monkeypatch, capsys, flag):
    ddl = _ddl()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(sys, "argv", ["div_keys_ddl.py", flag])
    assert ddl.main() == 2
    assert "DATABASE_URL" in capsys.readouterr().err


# ── Postgres 17 ────────────────────────────────────────────────────────────────────────────────────

# 운영 모양(2026-10-18): 기본키 없음, 적재 쪽이 만든 UNIQUE 인덱스 `<표>_key`, div_declared 는 4단계 인덱스 둘.
_SCHEMA = (
    """CREATE TABLE div_declared (
      corp_code text, name text, tickers text, bsns_year integer, reprt_code text, reprt_label text, stock_knd_raw text,
      stock_kind text, dps_krw double precision, div_total_krw double precision, unit_raw text, payout_pct double precision,
      rcept_no text, stlm_dt text, se_raw text, status text)""",
    "CREATE INDEX div_declared_corp_rep_yr ON div_declared (corp_code, reprt_code, bsns_year)",
    "CREATE INDEX div_declared_yr ON div_declared (bsns_year)",
    """CREATE TABLE div_quarterly (corp_code text, name text, tickers text, bsns_year integer, reprt_code text, q_label text,
      stlm_dt text, rcept_no text, cum_div_total_krw double precision, quarterly_div_krw double precision, basis_from text,
      basis_to text, anomaly text, status text)""",
    "CREATE UNIQUE INDEX div_quarterly_key ON div_quarterly (corp_code, bsns_year, reprt_code)",
    """CREATE TABLE div_payment (corp_code text, corp_name text, stock_code text, market text, fiscal_year integer,
      dps_common double precision, rcept_no text, remarks text)""",
    "CREATE UNIQUE INDEX div_payment_key ON div_payment (rcept_no)",
    "CREATE INDEX div_payment_corp_fy ON div_payment (corp_code, fiscal_year)",
    "CREATE TABLE div_payment_scope (fiscal_year integer, market text, is_complete boolean, n_corps integer)",
    "CREATE UNIQUE INDEX div_payment_scope_key ON div_payment_scope (fiscal_year, market)",
    """CREATE TABLE div_yield_hist (market text, scheme text, bucket text, label text, fiscal_year integer, denom_basis text,
      div_yield_pct double precision)""",
    "CREATE UNIQUE INDEX div_yield_hist_key ON div_yield_hist (market, scheme, bucket, fiscal_year, denom_basis)",
    "ALTER TABLE div_declared ENABLE ROW LEVEL SECURITY",
    "GRANT SELECT ON div_declared, div_quarterly, div_payment, div_payment_scope, div_yield_hist TO opm_ro",
)


def _seed(url, n_corps=40):
    with psycopg.connect(url, autocommit=True) as c:
        for s in _SCHEMA:
            c.execute(s)
        # 회사 × 연도 2 × 보고서 2. 각 보고서에 원문 줄 3개: 보통주 확정 · 똑같은 무배당 줄 두 번(원문 그대로의 완전 중복)
        c.execute("""
            INSERT INTO div_declared (corp_code, name, tickers, bsns_year, reprt_code, stock_knd_raw, stock_kind, dps_krw, se_raw, status)
            SELECT lpad(g::text, 8, '0'), 'n' || g, lpad(g::text, 6, '0'), y, r, k.raw, k.kind, k.dps, '주당 현금배당금(원)', k.st
              FROM generate_series(1, %s) g, generate_series(2024, 2025) y, unnest(ARRAY['11011','11012']) r,
                   (VALUES (1, '보통주', '보통', 100.0, '확정'), (2, NULL, '미구분', NULL, '무배당'), (3, NULL, '미구분', NULL, '무배당'))
                     AS k(o, raw, kind, dps, st)
             ORDER BY 1, 4, 5, k.o""", (n_corps,))
        c.execute("""INSERT INTO div_quarterly (corp_code, bsns_year, reprt_code, status)
                     SELECT lpad(g::text, 8, '0'), y, r, '확정' FROM generate_series(1, %s) g, generate_series(2024, 2025) y,
                            unnest(ARRAY['11011','11012','11013','11014']) r""", (n_corps,))
        c.execute("""INSERT INTO div_payment (corp_code, fiscal_year, rcept_no, dps_common)
                     SELECT lpad(g::text, 8, '0'), 2024, '2025' || lpad(g::text, 10, '0'), 100 FROM generate_series(1, %s) g""", (n_corps,))
        c.execute("INSERT INTO div_payment_scope VALUES (2024, 'KS', true, 500), (2025, 'KS', false, 10)")
        c.execute("""INSERT INTO div_yield_hist (market, scheme, bucket, fiscal_year, denom_basis, div_yield_pct)
                     SELECT 'KS', 'wics', 'b' || b, y, d, 1.5 FROM generate_series(1, 5) b, generate_series(2021, 2024) y,
                            unnest(ARRAY['all','payers','reported']) d""")
        c.execute("ANALYZE")


def _pks(c):
    return dict(c.execute(
        "SELECT conrelid::regclass::text, conname FROM pg_constraint WHERE contype = 'p' AND conrelid::regclass::text LIKE 'div_%'").fetchall())


def _indexes(c, table):
    return {r[0] for r in c.execute("SELECT indexname FROM pg_indexes WHERE tablename = %s", (table,)).fetchall()}


def test_apply_puts_primary_keys_on_all_five_and_is_idempotent(pg17):
    ddl = _ddl()
    _seed(pg17)
    with psycopg.connect(pg17) as c:
        before = {t: _indexes(c, t) for t, _, _ in ddl.PROMOTE}
        n_before = c.execute("SELECT count(*) FROM div_declared").fetchone()[0]
    first = ddl.apply(pg17, out=lambda *_: None)
    assert all(ok for _, ok, _ in first), first
    second = ddl.apply(pg17, out=lambda *_: None)                       # 두 번째: 전부 「이미 있음」
    assert all(ok and "이미" in why for _, ok, why in second), second
    with psycopg.connect(pg17) as c:
        assert _pks(c) == {"div_quarterly": "div_quarterly_key", "div_payment": "div_payment_key",
                           "div_payment_scope": "div_payment_scope_key", "div_yield_hist": "div_yield_hist_key",
                           "div_declared": "div_declared_key"}
        # ① 새 인덱스 없음 — 인덱스 집합이 그대로다(이름도 그대로)
        assert {t: _indexes(c, t) for t, _, _ in ddl.PROMOTE} == before
        # ② 행은 하나도 안 지웠다(완전 중복 줄도 그대로) · 줄 번호는 비지 않았다
        n, n_null, n_dup = c.execute(
            "SELECT count(*), count(*) FILTER (WHERE line_no IS NULL), "
            "(SELECT count(*) FROM (SELECT 1 FROM div_declared GROUP BY corp_code, reprt_code, bsns_year, stock_knd_raw, dps_krw, status "
            " HAVING count(*) > 1) g) FROM div_declared").fetchone()
        assert (n, n_null) == (n_before, 0) and n_dup > 0
        assert _indexes(c, "div_declared") == {"div_declared_key", "div_declared_yr"}   # corp_rep_yr 는 기본키가 대신한다
        # 칸 권한·RLS 는 그대로 — 새 칸도 표 단위 SELECT 로 읽힌다
        assert c.execute("SELECT has_column_privilege('opm_ro', 'div_declared', 'line_no', 'SELECT')").fetchone()[0]
        assert c.execute("SELECT relrowsecurity FROM pg_class WHERE oid = 'div_declared'::regclass").fetchone()[0]


def test_line_numbers_follow_the_stored_order_within_each_report(pg17):
    ddl = _ddl()
    _seed(pg17, n_corps=3)
    ddl.apply(pg17, out=lambda *_: None)
    with psycopg.connect(pg17) as c:
        rows = c.execute("SELECT line_no, stock_knd_raw, status FROM div_declared "
                         "WHERE corp_code = '00000002' AND reprt_code = '11011' AND bsns_year = 2025 ORDER BY line_no").fetchall()
    assert rows == [(1, "보통주", "확정"), (2, None, "무배당"), (3, None, "무배당")]


def test_old_style_insert_without_line_no_now_fails_instead_of_duplicating(pg17):
    """키로 맞추는 적재로 바꾸기 전의 옛 적재(전량 지우고 다시 넣기)는 실패해야 한다 — 조용히 줄 번호 없는 행을 쌓지 않게."""
    ddl = _ddl()
    _seed(pg17, n_corps=2)
    ddl.apply(pg17, out=lambda *_: None)
    with psycopg.connect(pg17) as c:
        with pytest.raises(psycopg.errors.NotNullViolation):
            with c.transaction():
                c.execute("DELETE FROM div_declared WHERE bsns_year = 2025")
                c.execute("INSERT INTO div_declared (corp_code, bsns_year, reprt_code, status) VALUES ('00000001', 2025, '11011', '무배당')")
        assert c.execute("SELECT count(*) FROM div_declared WHERE bsns_year = 2025").fetchone()[0] == 2 * 2 * 3   # 지우기도 되돌려졌다


def test_the_firm_series_query_uses_the_primary_key_index(pg17):
    ddl = _ddl()
    _seed(pg17)
    ddl.apply(pg17, out=lambda *_: None)
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute("SET enable_seqscan = off")
        plan = "\n".join(r[0] for r in c.execute(
            "EXPLAIN SELECT bsns_year, stock_kind, stock_knd_raw, dps_krw, div_total_krw, payout_pct, status, rcept_no, stlm_dt "
            "FROM div_declared WHERE corp_code = %s AND reprt_code = %s AND bsns_year BETWEEN %s AND %s "
            "ORDER BY bsns_year DESC, stock_kind, stock_knd_raw", ("00000007", "11011", 2020, 2025)).fetchall())
        assert "div_declared_key" in plan and "Seq Scan" not in plan, plan


def test_div_indexes_ddl_does_not_recreate_the_index_the_primary_key_replaced(pg17):
    ddl, idx = _ddl(), _load("div_indexes_ddl")
    _seed(pg17, n_corps=3)
    ddl.apply(pg17, out=lambda *_: None)
    out = []
    result = idx.apply(pg17, out=out.append)
    assert all(ok for _, ok, _ in result)
    with psycopg.connect(pg17) as c:
        assert _indexes(c, "div_declared") == {"div_declared_key", "div_declared_yr"}
    assert any("div_declared_corp_rep_yr" in line and "만들지 않음" in line for line in out)


def test_a_blocked_table_is_left_alone_and_the_others_still_get_keys(pg17):
    ddl = _ddl()
    _seed(pg17, n_corps=3)
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute("DROP INDEX div_payment_key")
        c.execute("INSERT INTO div_payment (corp_code, fiscal_year, rcept_no) VALUES ('x', 2024, NULL)")   # 키 칸 NULL
        c.execute("CREATE UNIQUE INDEX div_payment_key ON div_payment (rcept_no)")
    result = {t: (ok, why) for t, ok, why in ddl.apply(pg17, out=lambda *_: None)}
    assert result["div_payment"][0] is False and "NULL" in result["div_payment"][1]
    assert all(ok for t, (ok, _) in result.items() if t != "div_payment")
    with psycopg.connect(pg17) as c:
        assert "div_payment" not in _pks(c) and len(_pks(c)) == 4


def test_partially_filled_line_numbers_stop_div_declared_only(pg17):
    ddl = _ddl()
    _seed(pg17, n_corps=3)
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute("ALTER TABLE div_declared ADD COLUMN line_no smallint")
        c.execute("UPDATE div_declared SET line_no = 1 WHERE corp_code = '00000001'")
    result = {t: (ok, why) for t, ok, why in ddl.apply(pg17, out=lambda *_: None)}
    assert result["div_declared"][0] is False and "일부만" in result["div_declared"][1]
    with psycopg.connect(pg17) as c:
        assert "div_declared" not in _pks(c)
        assert c.execute("SELECT count(*) FILTER (WHERE line_no IS NULL) FROM div_declared").fetchone()[0] > 0   # 채우지 않았다
        assert "div_declared_corp_rep_yr" in _indexes(c, "div_declared")                                          # 지우지 않았다


def test_a_reader_holding_the_table_makes_that_table_fail_fast_not_wait(pg17):
    """잠금 대기는 3초까지 — 오래 읽는 트랜잭션이 있으면 그 표만 실패하고 다른 표는 진행한다(다시 돌리면 된다)."""
    ddl = _ddl()
    _seed(pg17, n_corps=3)
    holder = psycopg.connect(pg17)
    holder.execute("SELECT count(*) FROM div_quarterly").fetchone()            # 트랜잭션 열린 채 ACCESS SHARE 를 쥔다
    try:
        t0 = time.monotonic()
        result = {t: (ok, why) for t, ok, why in ddl.apply(pg17, out=lambda *_: None)}
        took = time.monotonic() - t0
    finally:
        holder.rollback()
        holder.close()
    assert result["div_quarterly"][0] is False and "55P03" in result["div_quarterly"][1]
    assert all(ok for t, (ok, _) in result.items() if t != "div_quarterly")
    assert took < 15
    again = {t: ok for t, ok, _ in ddl.apply(pg17, out=lambda *_: None)}     # 잠금이 풀린 뒤 다시 돌리면 끝난다
    assert all(again.values())


def test_check_runs_in_a_read_only_session_and_changes_nothing(pg17):
    ddl = _ddl()
    _seed(pg17, n_corps=3)
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute("SET default_transaction_read_only = on")
        res = {t: st for t, st, _ in ddl.check(c, out=lambda *_: None)}
        assert res == {"div_quarterly": "todo", "div_payment": "todo", "div_payment_scope": "todo",
                       "div_yield_hist": "todo", "div_declared": "todo"}
    with psycopg.connect(pg17) as c:
        assert _pks(c) == {}
        assert not c.execute("SELECT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = 'div_declared'::regclass "
                             "AND attname = 'line_no')").fetchone()[0]
    ddl.apply(pg17, out=lambda *_: None)
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute("SET default_transaction_read_only = on")
        assert {st for _, st, _ in ddl.check(c, out=lambda *_: None)} == {"done"}
