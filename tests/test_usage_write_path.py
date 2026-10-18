"""사용 기록 쓰기 경로 — 접속 때 DDL 0건 · 묶어 쓰기 · 유실 없음 · 종료 때 마지막 묶음 (261018, Supabase 재설계 4단계 T7·T8).

종전 `usage._pg_connect` 는 워커가 접속할 때마다 CREATE/ALTER 22문장을 보냈다. `ALTER TABLE` 은 칸이 이미 있어도 표 전체 잠금을
잡고, 표 소유자가 아닌 역할에선 `must be owner` 로 거절된다 — 최소 권한 계정이면 워커는 2초마다 재접속하며 한 행도 못 쓴다.
기록은 호출 1건마다 확정했고, 실패한 묶음은 버렸다.

앞쪽 시험은 DB 없이 돈다(가짜 `_connect`/`_write`). 뒤쪽은 `tests/conftest.py` 의 `pg17`(로컬 Docker Postgres 17)이 필요하고,
Docker 가 없으면 그 시험만 건너뛴다.
"""
from __future__ import annotations

import importlib
import importlib.util
import sqlite3
import threading
import time
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _ops_ddl():
    spec = importlib.util.spec_from_file_location("ops_ddl", ROOT / "scripts" / "ops_ddl.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def load_usage(monkeypatch, tmp_path):
    """`usage` 를 기록 켠 상태로 다시 읽는다(모듈이 import 때 환경변수를 읽는다). 끝나면 워커를 끝내고 원래 환경으로 되돌린다."""
    loaded = []

    def _load(database_url: str | None = None, **attrs):
        monkeypatch.setenv("OPM_USAGE_LOCAL", "1")
        monkeypatch.setenv("OPM_USAGE_DB_PATH", str(tmp_path / "usage.db"))
        if database_url:
            monkeypatch.setenv("DATABASE_URL", database_url)
        else:
            monkeypatch.delenv("DATABASE_URL", raising=False)
        usage = importlib.reload(importlib.import_module("open_proxy_mcp.usage"))
        assert usage._RECORDING, "기록 게이트가 닫힌 채면 아래 단언이 전부 공짜로 통과한다"
        for k, v in attrs.items():
            monkeypatch.setattr(usage, k, v)
        loaded.append(usage)
        return usage

    yield _load
    for usage in loaded:
        usage.flush(timeout=5)               # 워커를 끝낸다 — 다음 시험의 큐를 비우지 않게
    monkeypatch.undo()
    importlib.reload(importlib.import_module("open_proxy_mcp.usage"))


def _wait(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def _fake_backend(usage, monkeypatch, *, fail_first: int = 0):
    """DB 대신 묶음을 받아 적는 가짜. `fail_first` 번째까지는 실패한다."""
    seen: dict = {"batches": [], "connects": 0, "calls": 0}

    def _connect():
        seen["connects"] += 1
        return object()

    def _write(con, batch):
        seen["calls"] += 1
        if seen["calls"] <= fail_first:
            raise RuntimeError("일부러 실패")
        seen["batches"].append(list(batch))

    monkeypatch.setattr(usage, "_connect", _connect)
    monkeypatch.setattr(usage, "_write", _write)
    monkeypatch.setattr(usage, "_recover", lambda con: con)
    return seen


def _rows(seen):
    return [r for b in seen["batches"] for r in b]


# ── DB 없이 ──────────────────────────────────────────────────────────────

def test_records_inside_the_window_go_out_as_one_batch(load_usage, monkeypatch):
    """묶어 쓰기 — 창(`_FLUSH_SEC`) 안에 들어온 30건이 **한 번에** 나간다. 종전엔 30번 확정했다."""
    usage = load_usage(_FLUSH_SEC=0.5)
    seen = _fake_backend(usage, monkeypatch)
    for i in range(30):
        usage.record(f"user-{i}", 200, "company", 10)
    assert _wait(lambda: len(_rows(seen)) == 30)
    assert len(seen["batches"]) == 1, [len(b) for b in seen["batches"]]
    assert seen["connects"] == 1


def test_batch_size_is_capped(load_usage, monkeypatch):
    usage = load_usage(_FLUSH_SEC=0.3, _MAX_BATCH=50)
    seen = _fake_backend(usage, monkeypatch)
    for i in range(120):
        usage.record("user", 200, "company", 10)
    assert _wait(lambda: len(_rows(seen)) == 120)
    assert max(len(b) for b in seen["batches"]) <= 50


def test_failed_batch_is_held_and_retried_not_dropped(load_usage, monkeypatch):
    """종전 워커는 실패한 묶음을 버렸다. 이제 들고 있다가 다시 쓴다 — 두 번 실패해도 20건이 다 나간다."""
    usage = load_usage(_FLUSH_SEC=0.1, _RETRY_MAX_SEC=0.05)
    seen = _fake_backend(usage, monkeypatch, fail_first=2)
    for i in range(20):
        usage.record("user", 200, "company", i)
    assert _wait(lambda: len(_rows(seen)) == 20)
    ids = [r[0] for r in _rows(seen)]
    assert len(set(ids)) == 20, "다시 쓴 묶음이 겹쳐 실렸다"
    assert seen["connects"] == 1, "접속은 멀쩡한데 실패마다 새로 접속했다 — 그게 접속 폭주다"


def test_shutdown_signal_writes_the_open_batch_without_waiting_the_window(load_usage, monkeypatch):
    """종료 신호(`begin_shutdown`) — 창이 60초라도 모으던 것을 **곧장** 쓴다. 신호 뒤의 기록도 바로 나간다."""
    usage = load_usage(_FLUSH_SEC=60.0)
    seen = _fake_backend(usage, monkeypatch)
    for i in range(5):
        usage.record("user", 200, "company", i)
    time.sleep(0.2)
    assert _rows(seen) == [], "전제: 창이 60초면 아직 안 나갔어야 한다"
    t0 = time.monotonic()
    usage.begin_shutdown()
    assert _wait(lambda: len(_rows(seen)) == 5, timeout=3)
    assert time.monotonic() - t0 < 3
    usage.record("user", 200, "company", 99)        # 진행 중이던 요청이 끝나며 남기는 기록
    assert _wait(lambda: len(_rows(seen)) == 6, timeout=3), "종료 신호 뒤 기록이 다시 창을 기다린다"


def test_flush_at_exit_writes_the_last_batch_and_stops_the_worker(load_usage, monkeypatch):
    """atexit(`flush`) — 마지막 묶음을 쓰고 워커가 끝난다. 돌아올 때 이미 써져 있어야 한다(기다릴 시간이 없다)."""
    usage = load_usage(_FLUSH_SEC=60.0)
    seen = _fake_backend(usage, monkeypatch)
    for i in range(7):
        usage.record("user", 200, "company", i)
    usage.flush(timeout=5)
    assert len(_rows(seen)) == 7
    assert usage._stopped.is_set(), "워커가 안 끝났다"


def test_flush_retries_a_transient_failure_but_does_not_hang(load_usage, monkeypatch):
    """끝내는 중 한 번 실패 — 짧게 다시 해서 쓴다. 계속 실패하면 몇 번 만에 포기하고 돌아온다(종료를 붙들지 않는다)."""
    usage = load_usage(_FLUSH_SEC=60.0)
    seen = _fake_backend(usage, monkeypatch, fail_first=1)
    for i in range(4):
        usage.record("user", 200, "company", i)
    usage.flush(timeout=5)
    assert len(_rows(seen)) == 4

    usage = load_usage(_FLUSH_SEC=60.0)
    seen = _fake_backend(usage, monkeypatch, fail_first=10**6)
    usage.record("user", 200, "company", 1)
    t0 = time.monotonic()
    usage.flush(timeout=5)
    assert usage._stopped.is_set() and time.monotonic() - t0 < 2


def test_flush_without_a_worker_is_a_no_op(load_usage):
    usage = load_usage()
    usage.flush(timeout=0.1)                  # 기록이 한 번도 없었으면 워커도 없다 — 막히지 않아야 한다
    assert not usage._worker_started


def test_sqlite_retry_does_not_double_count_companies(load_usage):
    """같은 묶음을 두 번 써도(COMMIT 은 됐는데 응답이 끊긴 경우) 기업 집계는 한 번만 더해진다."""
    usage = load_usage()
    con = usage._sqlite_connect()
    batch = []
    for i in range(3):
        vals = {c: None for c in usage._EVENT_COLUMNS}
        vals.update(event_id=f"e{i}", ts_ns=1_760_000_000_000_000_000 + i, key_hash="h", status=200,
                    corp_codes="00126380")
        batch.append(tuple(vals[c] for c in usage._EVENT_COLUMNS))
    usage._sqlite_write(con, batch)
    usage._sqlite_write(con, batch)
    assert con.execute("SELECT count(*) FROM events").fetchone()[0] == 3
    assert con.execute("SELECT sum(requests) FROM ops_corp_daily").fetchone()[0] == 3


def test_pg_connect_sends_no_ddl():
    """서버는 DDL 을 보내지 않는다 — 소스에 CREATE/ALTER 가 없다(정본은 scripts/ops_ddl.py)."""
    import inspect

    from open_proxy_mcp import usage
    for fn in (usage._pg_connect, usage._pg_write):
        src = inspect.getsource(fn).split('"""', 2)[-1]          # 독스트링(종전 설명) 뒤의 실제 코드만
        code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
        for kw in ("CREATE ", "ALTER ", "DROP "):
            assert kw not in code, f"{fn.__name__} 에 DDL 이 남아 있다: {kw.strip()}"


# ── Postgres 17 ──────────────────────────────────────────────────────────

psycopg = pytest.importorskip("psycopg")

#: 종전 서버가 접속할 때마다 보내던 문장 중 하나 — 대조군(이게 거절되거나 잡혀야 검사가 눈을 뜨고 있다는 뜻).
_OLD_DDL = "ALTER TABLE ops_tool_calls ADD COLUMN IF NOT EXISTS tool text"


def _admin(url):
    return psycopg.connect(url, autocommit=True)


def _apply_ddl(url):
    _ops_ddl().apply(url)


def _install_ddl_counter(url):
    """DDL 이 **시작될 때마다** 한 줄 적는 이벤트 트리거. IF NOT EXISTS 로 아무것도 안 바뀌어도 적힌다."""
    with _admin(url) as c:
        c.execute("CREATE TABLE ddl_seen(tag text, at timestamptz DEFAULT now())")
        c.execute("""CREATE FUNCTION _note_ddl() RETURNS event_trigger LANGUAGE plpgsql SECURITY DEFINER AS
                     $$ BEGIN INSERT INTO public.ddl_seen(tag) VALUES (tg_tag); END $$""")
        c.execute("CREATE EVENT TRIGGER note_ddl ON ddl_command_start EXECUTE FUNCTION _note_ddl()")


def _ddl_count(url) -> int:
    with _admin(url) as c:
        return c.execute("SELECT count(*) FROM ddl_seen").fetchone()[0]


def _with_role(url, role):
    return f"{url}?options={urllib.parse.quote(f'-c role={role}')}"


def _grant_writer_role(url, role="opm_app"):
    """최소 권한 서버 계정의 모양 — 표 소유자 아님 · 스키마 생성 권한 없음 · 쓰는 데 필요한 권한만."""
    with _admin(url) as c:
        if not c.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
            c.execute(f"CREATE ROLE {role} NOLOGIN BYPASSRLS")
        c.execute(f"GRANT SELECT, INSERT ON ops_tool_calls TO {role}")
        c.execute(f"GRANT SELECT, INSERT, UPDATE ON ops_corp_daily, ops_krx_calls TO {role}")
    return _with_role(url, role)


def _counts(url):
    with _admin(url) as c:
        n = c.execute("SELECT count(*), count(DISTINCT event_id) FROM ops_tool_calls").fetchone()
        corp = dict(c.execute("SELECT corp_code, sum(requests) FROM ops_corp_daily GROUP BY 1").fetchall())
    return n, corp


def test_ddl_script_is_idempotent_and_matches_the_column_list(pg17):
    from open_proxy_mcp import usage
    _apply_ddl(pg17)
    _apply_ddl(pg17)                           # 멱등 — 두 번째도 오류 없이
    with _admin(pg17) as c:
        cols = [r[0] for r in c.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name='ops_tool_calls' "
            "ORDER BY ordinal_position").fetchall()]
        types = dict(c.execute("SELECT column_name, data_type FROM information_schema.columns "
                               "WHERE table_name='ops_tool_calls'").fetchall())
        rls = dict(c.execute("SELECT relname, relrowsecurity FROM pg_class WHERE relname IN "
                             "('ops_tool_calls','ops_corp_daily','ops_krx_calls')").fetchall())
        anon = c.execute("SELECT count(*) FROM information_schema.role_table_grants WHERE grantee IN "
                         "('anon','authenticated') AND table_name LIKE 'ops\\_%'").fetchone()[0]
        ro = c.execute("SELECT count(*) FROM information_schema.role_table_grants WHERE grantee='opm_ro' "
                       "AND privilege_type='SELECT' AND table_name LIKE 'ops\\_%'").fetchone()[0]
    assert set(cols) == set(usage._EVENT_COLUMNS), set(cols) ^ set(usage._EVENT_COLUMNS)
    assert types["as_of"] == "date" and types["scope"] == "text"
    assert rls == {"ops_tool_calls": True, "ops_corp_daily": True, "ops_krx_calls": True}
    assert anon == 0, "기본 권한이 anon/authenticated 에 준 SELECT 를 거두지 않았다"
    assert ro == 3


def test_ddl_script_upgrades_the_production_shape_without_touching_rows(pg17):
    """운영 모양(261018: 21칸·scope/as_of 없음)에서 돌리면 두 칸이 붙고 행은 그대로다. 폐기 칸이 남아 있으면 지운다."""
    with _admin(pg17) as c:
        c.execute("CREATE TABLE ops_tool_calls(event_id text PRIMARY KEY, ts_ns bigint NOT NULL, "
                  "key_hash text NOT NULL, status int, tool text, doc_cache_hit boolean)")
        c.execute("INSERT INTO ops_tool_calls(event_id, ts_ns, key_hash, status, tool) VALUES ('e1', 1, 'h', 200, 'company')")
    _apply_ddl(pg17)
    with _admin(pg17) as c:
        cols = {r[0] for r in c.execute("SELECT column_name FROM information_schema.columns "
                                        "WHERE table_name='ops_tool_calls'").fetchall()}
        row = c.execute("SELECT event_id, tool, scope, as_of FROM ops_tool_calls").fetchall()
    assert {"scope", "as_of", "lag_ms"} <= cols and "doc_cache_hit" not in cols
    assert row == [("e1", "company", None, None)]


def test_writer_sends_zero_ddl_and_works_as_a_least_privilege_role(pg17, load_usage):
    """접속 때 DDL 0건 — 이벤트 트리거로 센다. 그리고 표 소유자가 아닌 역할로도 쓴다(종전 코드는 여기서 거절됐다)."""
    _apply_ddl(pg17)
    url = _grant_writer_role(pg17)
    _install_ddl_counter(pg17)                 # GRANT 도 DDL 로 세어진다 — 권한을 다 준 뒤에 세기 시작한다
    # 대조군 ① — 트리거가 눈을 뜨고 있다: 종전 문장 하나가 잡힌다(그리고 되돌린다)
    with psycopg.connect(pg17) as c:
        c.execute(_OLD_DDL)
        assert c.execute("SELECT count(*) FROM ddl_seen").fetchone()[0] == 1
        c.rollback()
    # 대조군 ② — 종전 코드가 이 역할에서 실제로 실패했을 것이다
    with psycopg.connect(url) as c:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute(_OLD_DDL)
    assert _ddl_count(pg17) == 0

    usage = load_usage(url, _FLUSH_SEC=0.2)
    for i in range(40):
        usage.record(f"user-{i % 3}", 200, "company", i, corp_codes=["00126380"])
    usage.flush(timeout=10)
    (n, distinct), corp = _counts(pg17)
    assert (n, distinct) == (40, 40)
    assert corp == {"00126380": 40}
    assert _ddl_count(pg17) == 0, "쓰기 경로가 DDL 을 보냈다"


def test_retry_after_lost_ack_neither_loses_nor_double_counts(pg17, load_usage, monkeypatch):
    """COMMIT 은 됐는데 응답이 끊긴 척 → 워커는 같은 묶음을 다시 쓴다. 이벤트는 그대로, 기업 집계도 그대로여야 한다."""
    _apply_ddl(pg17)
    usage = load_usage(pg17, _FLUSH_SEC=0.2, _RETRY_MAX_SEC=0.05)
    real = usage._write
    state = {"n": 0}

    def flaky(con, batch):
        real(con, batch)
        state["n"] += 1
        if state["n"] == 1:
            raise psycopg.OperationalError("응답이 끊긴 척 — 실제로는 들어갔다")

    monkeypatch.setattr(usage, "_write", flaky)
    for i in range(25):
        usage.record("user", 200, "company", i, corp_codes=["00126380", "00164779"])
    assert _wait(lambda: state["n"] >= 2), "전제: 다시 쓰기가 일어났어야 한다"
    usage.flush(timeout=10)
    (n, distinct), corp = _counts(pg17)
    assert (n, distinct) == (25, 25)
    assert corp == {"00126380": 25, "00164779": 25}, "다시 쓴 묶음이 기업 순위를 부풀렸다"


def test_deploy_before_ddl_holds_rows_until_the_schema_catches_up(pg17, load_usage, monkeypatch):
    """DDL 을 늦게 적용한 배포 — 새 칸이 없어 쓰기가 거절되는 동안 행을 들고 있다가, 적용하는 순간 다 들어간다."""
    with _admin(pg17) as c:                    # 운영의 지금 모양: scope·as_of 가 아직 없다
        c.execute("CREATE TABLE ops_tool_calls(event_id text PRIMARY KEY, ts_ns bigint NOT NULL, key_hash text NOT NULL, status int)")
        c.execute("CREATE TABLE ops_corp_daily(log_dd date NOT NULL, corp_code text NOT NULL, "
                  "requests int NOT NULL DEFAULT 0, PRIMARY KEY (log_dd, corp_code))")
    usage = load_usage(pg17, _FLUSH_SEC=0.1, _RETRY_MAX_SEC=0.1)
    connects = {"n": 0}
    real_connect = usage._connect

    def counting_connect():
        connects["n"] += 1
        return real_connect()

    monkeypatch.setattr(usage, "_connect", counting_connect)
    for i in range(10):
        usage.record("user", 200, "company", i)
    time.sleep(0.6)
    assert _counts(pg17)[0] == (0, 0), "전제: 스키마가 덜 맞아 아직 못 썼어야 한다"
    _apply_ddl(pg17)
    usage.flush(timeout=10)
    assert _counts(pg17)[0] == (10, 10)
    assert connects["n"] == 1, f"거절될 때마다 새로 접속했다({connects['n']}번)"


def test_scope_and_as_of_land_in_their_columns(pg17, load_usage, monkeypatch):
    """T8 배관 — `record(scope=, as_of=)` 가 정규화(`_call_dims`)를 지나 제 칸에 들어간다(정규화 규칙은 따로 시험)."""
    _apply_ddl(pg17)
    usage = load_usage(pg17, _FLUSH_SEC=0.1)
    monkeypatch.setattr(usage, "_call_dims", lambda scope, as_of: ("quote", "2026-10-16"))
    usage.record("user", 200, "trading_data", 5, scope="QUOTE ", as_of="20261016")
    usage.flush(timeout=10)
    import datetime as dt
    with _admin(pg17) as c:
        assert c.execute("SELECT scope, as_of FROM ops_tool_calls").fetchall() == [("quote", dt.date(2026, 10, 16))]


@pytest.mark.parametrize("scope, as_of, want", [
    ("quote", "20261016", ("quote", "2026-10-16")),
    ("Quote ", "2026-10-16", ("quote", "2026-10-16")),      # 대소문자·공백은 바로잡는다
    ("firm_history", 20261016, ("firm_history", "2026-10-16")),   # JSON 숫자로 온 날짜
    (None, None, (None, None)),                             # 안 보냄
    ("", "", (None, None)),
    ("삼성전자 최근 주가", "2024", (None, None)),             # 자유 텍스트 · 연도는 날짜가 아니다
    ("quote; drop table", "2024-09", (None, None)),
    ("x" * 40, "20240230", (None, None)),                   # 너무 긴 식별자 · 달력에 없는 날
    ({"scope": "quote"}, ["20261016"], (None, None)),       # 형이 엉뚱하다
    (True, True, (None, None)),
    ("market", "20991231", ("market", "2099-12-31")),       # 미래 날짜도 남긴다 — 잘못 보낸 것도 보여야 한다
])
def test_call_dims_keeps_only_normalized_values(scope, as_of, want):
    from open_proxy_mcp import usage
    assert usage._call_dims(scope, as_of) == want


def test_a_tripping_gatekeeper_keeps_the_rest_of_the_row(load_usage, monkeypatch):
    """정규화가 예외를 던져도 그 행의 나머지(도구·지연 등)는 남는다 — 새 칸 때문에 기존 계측을 잃지 않는다."""
    usage = load_usage(_FLUSH_SEC=0.1)
    seen = _fake_backend(usage, monkeypatch)

    def boom(scope, as_of):
        raise ValueError("넘어짐")

    monkeypatch.setattr(usage, "_call_dims", boom)
    usage.record("user", 200, "trading_data", 5, scope="quote", as_of="20261016")
    assert _wait(lambda: len(_rows(seen)) == 1)
    row = dict(zip(usage._EVENT_COLUMNS, _rows(seen)[0]))
    assert row["tool"] == "trading_data" and row["scope"] is None and row["as_of"] is None
