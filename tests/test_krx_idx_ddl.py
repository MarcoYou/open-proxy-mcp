"""scripts/krx_idx_ddl.py — KRX 공식 지수 종가 표 둘(krx_idx_series 사전 · krx_idx 종가).

핵심 계약:
  ① 시세 표는 반복 text 열쇠 없이 (sid smallint, trd_dd date, close_x100 int) 세 칸이고, 색인은 기본키 하나뿐이다(행당 바이트가 곧 용량).
  ② 열쇠는 (엔드포인트 src, 앞뒤 공백 없는 name) — 같은 이름이 코스피·코스닥 양쪽에 있을 수 있다.
  ③ DDL 은 멱등이고 표만 만든다(행 0). RLS 를 켜고, anon/authenticated 에는 권한이 없고, opm_ro 는 정책을 거쳐 SELECT 만 한다.
  ④ 합성 적재로 잰 행당 바이트가 설계 값(정렬 적재 ≈ 70 B · 매일 늘리기 ≈ 80 B) 안에 든다 — 모양이 바뀌어 용량이 부풀면 여기서 잡힌다.

SQL 쪽은 Postgres 17 에서만 검증된다(`tests/conftest.py` 의 `pg17` — Docker 가 없으면 그 테스트만 건너뜀).
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
from datetime import date, timedelta

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

psycopg = pytest.importorskip("psycopg")


def _ddl():
    spec = importlib.util.spec_from_file_location("krx_idx_ddl", ROOT / "scripts" / "krx_idx_ddl.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── DB 없이 ───────────────────────────────────────────────────────────────────────────────────────

def test_rendered_sql_creates_two_tables_only_and_is_idempotent_text():
    ddl = _ddl()
    sql = ddl.render()
    assert sql.startswith("BEGIN;") and sql.rstrip().endswith("COMMIT;")
    assert sql.count("CREATE TABLE IF NOT EXISTS") == 2
    assert "CREATE TABLE IF NOT EXISTS krx_idx_series" in sql and "CREATE TABLE IF NOT EXISTS krx_idx (" in sql
    assert "INSERT INTO" not in ddl.STATEMENTS                  # 행을 넣지 않는다
    # 시세 표는 세 칸 · 반복 text 열쇠 없음 · 보조 색인 없음(기본키 하나)
    body = ddl.STATEMENTS.split("CREATE TABLE IF NOT EXISTS krx_idx (")[1].split(");")[0]
    assert "text" not in body and "double" not in body and "numeric" not in body
    assert "smallint" in body and "date" in body and "integer" in body
    assert ddl.STATEMENTS.count("CREATE INDEX") == 0
    assert ddl.STATEMENTS.count("CREATE UNIQUE INDEX") == 1      # 사전의 idx_code(부분 유일) 하나뿐
    assert "ENABLE ROW LEVEL SECURITY" in sql and "GRANT SELECT ON krx_idx_series, krx_idx TO opm_ro" in sql
    assert "REVOKE ALL ON krx_idx_series, krx_idx FROM anon, authenticated" in sql
    # 서버 쓰기 권한은 주지 않는다
    assert "opm_app" not in sql and "INSERT, " not in sql


def test_print_mode_never_touches_the_database(monkeypatch, capsys):
    ddl = _ddl()
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: (_ for _ in ()).throw(AssertionError("접속하면 안 된다")))
    monkeypatch.setattr(sys, "argv", ["krx_idx_ddl.py"])
    assert ddl.main() == 0
    assert "krx_idx_series" in capsys.readouterr().out
    monkeypatch.setattr(sys, "argv", ["krx_idx_ddl.py", "--no-tx"])
    assert ddl.main() == 0
    out = capsys.readouterr().out
    assert "BEGIN" not in out and "CREATE TABLE" in out


@pytest.mark.parametrize("flag", ["--apply", "--check"])
def test_db_modes_without_database_url_refuse(monkeypatch, capsys, flag):
    ddl = _ddl()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    monkeypatch.setattr(sys, "argv", ["krx_idx_ddl.py", flag])
    assert ddl.main() == 2
    assert "DATABASE_URL" in capsys.readouterr().err


# ── Postgres 17 ────────────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def db(pg17):
    ddl = _ddl()
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(ddl.STATEMENTS)
        c.execute(ddl.STATEMENTS)                     # 멱등
    return pg17


def _one(c, sql, params=None):
    return c.execute(sql, params).fetchone()[0]


def test_tables_are_empty_and_shape_matches_the_contract(db):
    ddl = _ddl()
    with psycopg.connect(db, autocommit=True) as c:
        assert _one(c, "SELECT count(*) FROM krx_idx") == 0 and _one(c, "SELECT count(*) FROM krx_idx_series") == 0
        # 날마다 지우고 넣는 표라 죽은 행 2% 에서 치운다(합성 2년 굴리기: 2% 2.04 MiB · 기본 20% 2.91 · 안 치우면 5.13)
        assert _one(c, "SELECT reloptions FROM pg_class WHERE relname='krx_idx'") == ["autovacuum_vacuum_scale_factor=0.02", "autovacuum_analyze_scale_factor=0.01"]
        # 시세 표의 색인은 기본키 하나뿐
        idx = c.execute("SELECT indexrelid::regclass::text FROM pg_index WHERE indrelid = 'krx_idx'::regclass").fetchall()
        assert idx == [("krx_idx_pkey",)]
        # 사전: 기본키 + (src,name) 유일 + idx_code 부분 유일
        sidx = sorted(r[0] for r in c.execute("SELECT indexrelid::regclass::text FROM pg_index WHERE indrelid = 'krx_idx_series'::regclass").fetchall())
        assert sidx == ["krx_idx_series_idx_code_key", "krx_idx_series_pkey", "krx_idx_series_src_name_key"]
        # 칸 모양(행 바이트): smallint + date + int4 — 튜플 12 B 데이터
        assert c.execute("SELECT attname, atttypid::regtype::text FROM pg_attribute WHERE attrelid = 'krx_idx'::regclass AND attnum > 0 ORDER BY attnum").fetchall() == [
            ("sid", "smallint"), ("trd_dd", "date"), ("close_x100", "integer")]
    assert ddl.check(db) == []


def test_check_reports_drift(db):
    ddl = _ddl()
    with psycopg.connect(db, autocommit=True) as c:
        c.execute("ALTER TABLE krx_idx DISABLE ROW LEVEL SECURITY")
        c.execute("ALTER TABLE krx_idx RESET (autovacuum_vacuum_scale_factor)")
        c.execute("GRANT SELECT ON krx_idx_series TO anon")
        c.execute("REVOKE SELECT ON krx_idx FROM opm_ro")
    problems = ddl.check(db)
    assert any("krx_idx: RLS" in p for p in problems)
    assert any("krx_idx: 저장 옵션" in p for p in problems)
    assert any("krx_idx_series: anon" in p and "SELECT" in p for p in problems)
    assert any("krx_idx: opm_ro" in p for p in problems)
    with psycopg.connect(db, autocommit=True) as c:
        c.execute("ALTER TABLE krx_idx ADD COLUMN volume bigint")
    assert any("칸이 다르다" in p for p in ddl.check(db))
    with psycopg.connect(db, autocommit=True) as c:
        c.execute("DROP POLICY krx_idx_series_read ON krx_idx_series")
        c.execute("GRANT USAGE ON SEQUENCE krx_idx_series_sid_seq TO authenticated")
    problems = ddl.check(db)
    assert any("krx_idx_series: SELECT 정책" in p for p in problems)
    assert any("krx_idx_series_sid_seq: authenticated" in p for p in problems)
    # 열쇠가 넓어지거나 외래키가 빠지면 --check 가 잡는다
    with psycopg.connect(db, autocommit=True) as c:
        c.execute("ALTER TABLE krx_idx_series DROP CONSTRAINT krx_idx_series_src_name_key, "
                  "ADD CONSTRAINT krx_idx_series_src_name_key UNIQUE (src, name, kind)")
        c.execute("ALTER TABLE krx_idx DROP CONSTRAINT krx_idx_sid_fk")
    problems = ddl.check(db)
    assert any("krx_idx_series_src_name_key" in p for p in problems)
    assert any("krx_idx_sid_fk" in p for p in problems)


def test_rerun_revokes_sequence_grants_like_supabase_defaults(db):
    """운영(Supabase)의 기본 권한은 새 시퀀스에도 anon·authenticated 권한을 준다 — 시험 DB 의 기본 권한은 표만 흉내 내므로 직접 준 뒤
    DDL 을 다시 돌려 거둬지는지 본다(시퀀스 REVOKE 줄이 빠져도 잡히게)."""
    ddl = _ddl()
    with psycopg.connect(db, autocommit=True) as c:
        c.execute("GRANT USAGE, SELECT, UPDATE ON SEQUENCE krx_idx_series_sid_seq TO anon, authenticated")
        c.execute(ddl.STATEMENTS)
        for role in ("anon", "authenticated"):
            for priv in ("USAGE", "SELECT", "UPDATE"):
                assert _one(c, "SELECT has_sequence_privilege(%s,'krx_idx_series_sid_seq',%s)", (role, priv)) is False
    assert ddl.check(db) == []


def test_check_is_read_only(db):
    ddl = _ddl()
    with psycopg.connect(db, autocommit=True) as c:
        before = _one(c, "SELECT count(*) FROM pg_class")
    assert ddl.check(db) == []
    with psycopg.connect(db, autocommit=True) as c:
        assert _one(c, "SELECT count(*) FROM pg_class") == before


def test_access_control_matches_other_tables(db):
    with psycopg.connect(db, autocommit=True) as c:
        for t in ("krx_idx_series", "krx_idx"):
            assert _one(c, "SELECT relrowsecurity FROM pg_class WHERE relname=%s", (t,)) is True
            for role in ("anon", "authenticated"):
                for priv in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                    assert _one(c, "SELECT has_table_privilege(%s,%s,%s)", (role, t, priv)) is False
            assert _one(c, "SELECT has_table_privilege('opm_ro',%s,'SELECT')", (t,)) is True
            for priv in ("INSERT", "UPDATE", "DELETE"):
                assert _one(c, "SELECT has_table_privilege('opm_ro',%s,%s)", (t, priv)) is False
        for role in ("anon", "authenticated"):
            assert _one(c, "SELECT has_sequence_privilege(%s,'krx_idx_series_sid_seq','USAGE')", (role,)) is False
        c.execute("INSERT INTO krx_idx_series (src, name, kind, in_db) VALUES ('P', '코스피', 'z', true)")
        c.execute("INSERT INTO krx_idx (sid, trd_dd, close_x100) SELECT sid, DATE '2026-10-20', 555274 FROM krx_idx_series")
        c.execute("SET ROLE opm_ro")                   # BYPASSRLS 없음 — 정책(USING true)을 거쳐 읽혀야 한다
        assert _one(c, "SELECT count(*) FROM krx_idx") == 1 and _one(c, "SELECT count(*) FROM krx_idx_series") == 1
        c.execute("RESET ROLE")


def test_constraints(db):
    err = psycopg.errors
    with psycopg.connect(db, autocommit=True) as c:
        # 열쇠 = (src, name) — 같은 이름이 P·Q 양쪽에 있어도 된다(실제 업종 20개), 같은 (src,name) 두 번은 안 된다
        c.execute("INSERT INTO krx_idx_series (src, name, kind, in_db) VALUES ('P', '화학', 's', true), ('Q', '화학', 's', true)")
        with pytest.raises(err.UniqueViolation):
            c.execute("INSERT INTO krx_idx_series (src, name, kind, in_db) VALUES ('P', '화학', 't', false)")   # kind 가 달라도 같은 열쇠
        # 공백이 붙은 이름·빈 이름·모르는 src/kind 거절 — ' K-샤프지수(10년)' 같은 앞 공백이 열쇠에 섞이지 않게
        for bad in ("('P', ' 화학2', 's')", "('P', '화학3 ', 's')", "('P', '', 's')", "('Z', '화학4', 's')", "('P', '화학5', 'q')"):
            with pytest.raises((err.CheckViolation, err.UniqueViolation)):
                c.execute(f"INSERT INTO krx_idx_series (src, name, kind) VALUES {bad}")
        # 날짜 순서
        with pytest.raises(err.CheckViolation):
            c.execute("INSERT INTO krx_idx_series (src, name, kind, first_dd, last_dd) VALUES ('X', 'KRX 300', 'z', '2026-01-02', '2026-01-01')")
        # idx_code 는 있으면 유일, NULL 은 여럿
        c.execute("INSERT INTO krx_idx_series (src, name, kind, idx_code) VALUES ('P', '코스피 200', 'z', 'kospi200'), ('X', 'KRX 300', 'z', NULL), ('X', 'KRX 100', 'z', NULL)")
        with pytest.raises(err.UniqueViolation):
            c.execute("INSERT INTO krx_idx_series (src, name, kind, idx_code) VALUES ('Q', 'x', 'z', 'kospi200')")
        sid = _one(c, "SELECT sid FROM krx_idx_series WHERE src='P' AND name='화학'")
        c.execute("INSERT INTO krx_idx VALUES (%s, '2026-10-20', 455075)", (sid,))
        with pytest.raises(err.UniqueViolation):
            c.execute("INSERT INTO krx_idx VALUES (%s, '2026-10-20', 455076)", (sid,))
        with pytest.raises(err.ForeignKeyViolation):
            c.execute("INSERT INTO krx_idx VALUES (30000, '2026-10-20', 1)")
        for v in (0, -5):
            with pytest.raises(err.CheckViolation):
                c.execute("INSERT INTO krx_idx VALUES (%s, '2026-10-19', %s)", (sid, v))
        with pytest.raises(err.NotNullViolation):
            c.execute("INSERT INTO krx_idx VALUES (%s, '2026-10-19', NULL)", (sid,))
        # 사전에서 시세가 있는 계열을 지울 수 없다
        with pytest.raises(err.ForeignKeyViolation):
            c.execute("DELETE FROM krx_idx_series WHERE sid=%s", (sid,))


def test_identity_keeps_explicit_ids_and_continues_after_setval(db):
    """적재가 정한 번호를 그대로 넣고 시퀀스를 맞추면, 새 계열은 그 다음 번호를 받는다(번호 충돌 없음)."""
    with psycopg.connect(db, autocommit=True) as c:
        c.execute("INSERT INTO krx_idx_series (sid, src, name, kind, in_db) VALUES (1, 'P', 'a', 'z', true), (2, 'P', 'b', 's', true), (128, 'X', 'c', 't', false)")
        c.execute("SELECT setval(pg_get_serial_sequence('krx_idx_series', 'sid'), (SELECT max(sid) FROM krx_idx_series))")
        c.execute("INSERT INTO krx_idx_series (src, name, kind) VALUES ('Q', 'new', 't')")
        assert _one(c, "SELECT sid FROM krx_idx_series WHERE name='new'") == 129


def _workdays(start: date, n: int):
    d, out = start, []
    while len(out) < n:
        if d.isoweekday() < 6:
            out.append(d)
        d += timedelta(days=1)
    return out


def test_synthetic_load_row_bytes(db):
    """합성 적재: 83계열 × (일별 250일 + 주간 105점 ≈ 355일) = 29.5천 행. 행당 바이트가 설계 안에 든다.

    ① 계열·날짜 순으로 한 번에 넣기(백필 모양) ≤ 76 B/행  ② 날짜마다 83행씩 늘려 가기(갱신기 모양, 기본키 색인이 덜 촘촘) ≤ 92 B/행.
    행당 = pg_total_relation_size / 행 수(힙 + 기본키 색인 + 페이지 여유 포함, TOAST 없음)."""
    n_series, n_days = 83, 355
    days = _workdays(date(2023, 10, 20), n_days)
    with psycopg.connect(db, autocommit=True) as c:
        c.execute("INSERT INTO krx_idx_series (sid, src, name, kind, in_db) SELECT g, 'P', 'n' || g, 'z', true FROM generate_series(1, %s) g", (n_series,))
        c.execute("CREATE TABLE _days AS SELECT * FROM unnest(%s::date[]) AS d(d)", (days,))
        c.execute("INSERT INTO krx_idx SELECT s.sid, d.d, 100000 + (s.sid * 37 + extract(doy FROM d.d)::int * 11) % 90000 "
                  "FROM krx_idx_series s CROSS JOIN _days d ORDER BY s.sid, d.d")
        n = _one(c, "SELECT count(*) FROM krx_idx")
        assert n == n_series * n_days
        sorted_b = _one(c, "SELECT pg_total_relation_size('krx_idx')") / n
        print(f"\n[합성 적재] 정렬 적재 {n}행 행당 {sorted_b:.1f} B 합 {n * sorted_b / 1048576:.3f} MiB")
        assert sorted_b <= 76
        c.execute("TRUNCATE krx_idx")
        for d in days:                                   # 날짜별 한 번씩(갱신기가 하루 한 번 83행을 넣는 모양)
            c.execute("INSERT INTO krx_idx SELECT sid, %s, 100000 + sid FROM krx_idx_series", (d,))
        daily_b = _one(c, "SELECT pg_total_relation_size('krx_idx')") / n
        print(f"[합성 적재] 날짜별 늘리기 {n}행 행당 {daily_b:.1f} B 합 {n * daily_b / 1048576:.3f} MiB")
        assert daily_b <= 92
