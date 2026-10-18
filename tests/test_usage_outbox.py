"""송신함 — #97 의 묶어 쓰기 위에 얹은 디스크 받침(261018).

지키는 것: 워커가 큐에서 꺼낸 묶음은 중앙 DB 의 COMMIT 을 확인할 때까지 볼륨에 남는다 — DB 가 죽은 채 끝나도
다음 프로세스가 이어 쓰고, 다시 써도 기업 집계를 두 번 더하지 않는다. 요청 경로는 디스크를 만지지 않는다.
「재시작」은 모듈을 따로 한 벌 더 읽어 흉내 낸다 — 같은 송신함 파일, 새 워커.
"""
import hashlib
import importlib.util
import queue
import sqlite3
import time
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "open_proxy_mcp" / "usage.py"


def _load():
    spec = importlib.util.spec_from_file_location("isolated_usage", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def boot(tmp_path, monkeypatch):
    """기록을 켠 `usage` 한 벌을 띄운다(부를 때마다 새 프로세스처럼). 끝나면 워커를 끝내고 송신함을 닫는다."""
    monkeypatch.setenv("OPM_USAGE_LOCAL", "1")
    monkeypatch.setenv("OPM_USAGE_DB_PATH", str(tmp_path / "delivered.db"))
    monkeypatch.setenv("OPM_USAGE_OUTBOX_PATH", str(tmp_path / "outbox.db"))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    booted = []

    def _boot(**attrs):
        usage = _load()
        assert usage._RECORDING, "기록 게이트가 닫힌 채면 아래 단언이 전부 공짜로 통과한다"
        for k, v in attrs.items():
            setattr(usage, k, v)
        booted.append(usage)
        return usage

    yield _boot
    for usage in booted:
        usage.flush(timeout=5)
        if usage._outbox_con:
            usage._outbox_con.close()


def _wait(cond, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


def _backend(usage, *, fail=False):
    """DB 대신 받은 행을 적는 가짜. `fail` 이면 늘 실패한다(DB 가 죽은 척)."""
    seen = []

    def _write(con, batch):
        if fail:
            raise ConnectionError("DB 가 죽은 척")
        seen.extend(batch)

    usage._connect = lambda: object()
    usage._write = _write
    usage._recover = lambda con: con
    return seen


def test_rows_the_last_process_could_not_write_go_out_after_restart(boot):
    """DB 가 죽은 채 끝난 프로세스 — 꺼낸 묶음과 **큐의 나머지까지** 송신함에 남고, 다음 프로세스가 새 호출 없이도 쓴다."""
    first = boot(_FLUSH_SEC=60.0, _MAX_BATCH=2)
    _backend(first, fail=True)
    for i in range(5):
        first.record("test-key", 200, "company", i)
    first.flush(timeout=5)               # 2건은 꺼냈다가 못 썼고, 나머지는 큐에 남은 채 끝난다
    assert first._stopped.is_set()
    left = {r[0] for r in first._pending_batch(limit=100)}
    assert len(left) == 5, "끝내며 못 쓴 것이 송신함에 다 남지 않았다"

    second = boot(_FLUSH_SEC=0.05, _MAX_BATCH=2)
    seen = _backend(second)
    second.stats()                       # fly 의 /health 점검 — 새 호출이 없어도 워커가 뜬다
    assert _wait(lambda: len(seen) == 5), f"재시작 뒤 {len(seen)}/5건만 나갔다"
    assert {r[0] for r in seen} == left
    assert _wait(lambda: second._pending_batch(limit=100) == []), "다 썼는데 송신함이 안 비었다"


@pytest.mark.parametrize("commit_was_successful", [False, True])
def test_failed_write_is_resent_without_double_counting(boot, commit_was_successful):
    """실제 sqlite 백엔드. 첫 쓰기가 실패(또는 COMMIT 은 됐는데 응답이 끊김) → 같은 묶음을 다시 쓴다.
    이벤트 1건·기업 집계 1 이어야 하고, COMMIT 을 확인한 뒤 송신함은 비어야 한다."""
    usage = boot(_FLUSH_SEC=0.05, _RETRY_MAX_SEC=0.05)
    real = usage._write
    calls = []

    def flaky(con, batch):
        calls.append([r[0] for r in batch])
        if len(calls) == 1:
            if commit_was_successful:
                real(con, batch)         # 들어갔는데 응답이 끊긴 척
            raise ConnectionError("test failure")
        real(con, batch)

    usage._write = flaky
    usage.record("test-key", 200, "company", corp_codes=["00126380"])
    assert _wait(lambda: len(calls) >= 2), "전제: 다시 쓰기가 일어났어야 한다"
    usage.flush(timeout=5)
    assert calls[0] == calls[1]
    con = sqlite3.connect(usage.DB_PATH)
    try:
        assert con.execute("SELECT count(*) FROM events").fetchone()[0] == 1
        assert con.execute("SELECT requests FROM ops_corp_daily").fetchone()[0] == 1
    finally:
        con.close()
    assert usage._pending_batch() == [], "COMMIT 을 확인했는데 송신함에서 안 지웠다"


def test_request_path_only_queues_and_never_touches_the_outbox(boot):
    """`record()` 는 큐에만 넣는다 — 송신함은 워커가 묶음을 꺼낸 뒤에야 쓴다(#97 이 없앤 「호출마다 확정」을 되살리지 않는다).
    운영자 키·핸드셰이크는 큐에도 안 들어간다."""
    usage = boot()
    usage._ensure_worker = lambda: None          # 워커를 재워 「넣었나」만 남긴다

    def no_disk():
        raise AssertionError("요청 경로가 송신함을 열었다")

    usage._outbox = no_disk
    usage.SELF_HASHES = {hashlib.sha256(b"operator-test-key").hexdigest()}
    usage.record("operator-test-key", 200, "company")
    usage.record("test-key", 200, "ping")
    assert usage._q.qsize() == 0, "운영자 키·핸드셰이크가 큐에 들어갔다"
    usage.record("test-key", 200, "company")
    assert usage._q.qsize() == 1 and usage._telemetry["accepted"] == 1


def test_failures_are_visible_without_breaking_requests_or_logging_details(boot, capsys):
    """송신함이 망가져도 기록은 메모리 경로로 계속 나가고 상태에 드러난다. 큐가 차도 요청은 깨지지 않는다.
    로그에는 예외 종류만 — 오류 원문은 싣지 않는다."""
    usage = boot(_FLUSH_SEC=0.05)
    seen = _backend(usage)

    def broken():
        raise OSError("secret content must not be logged")

    usage._outbox = broken
    usage.record("test-key", 200, "company")
    assert _wait(lambda: len(seen) == 1), "송신함이 망가졌다고 기록이 멈췄다"
    state = usage.stats()
    assert state["status"] == "degraded" and state["outbox_failures"] >= 1 and state["pending"] is None
    assert "secret" not in capsys.readouterr().err

    full = boot()
    full._ensure_worker = lambda: None
    full._q = queue.Queue(maxsize=1)
    full._q.put_nowait(object())
    full.record("test-key", 200, "company")      # 큐가 찼다 — 예외 없이 돌아와야 한다
    assert full._telemetry["record_failures"] == 1
    assert full.stats()["status"] == "degraded"


# ── 거절된 행 격리 (`_is_permanent` · `_isolate`) ─────────────────────────────
# 한 행이 영구적으로 거절되면 그 묶음은 영영 실패하고 새 행이 뒤에 붙어 전부가 막힌다 — 송신함 때문에 재시작해도 안 풀린다.
# 행 탓이면 그 행만 떼어 내고, 행 탓이 아니면(접속·스키마 미적용·권한·읽기 전용) 종전대로 들고 기다린다.
from psycopg import OperationalError as PgOperationalError
from psycopg import errors as pg


def _tool(usage, row):
    return dict(zip(usage._EVENT_COLUMNS, row))["tool"]


def _rejecting_backend(usage, reject, *, transient_first=0):
    """`reject(row)` 가 오류를 돌려주는 행이 든 묶음을 그 오류로 거절하는 가짜 DB. 처음 `transient_first` 번은 접속이 끊긴 척한다."""
    seen, calls = [], []

    def _write(con, batch):
        calls.append(len(batch))
        if len(calls) <= transient_first:
            raise pg.AdminShutdown("접속이 끊긴 척")
        errors = [e for e in map(reject, batch) if e is not None]
        if errors:
            raise errors[0]
        seen.extend(batch)

    usage._connect = lambda: object()
    usage._write = _write
    usage._recover = lambda con: con
    return seen, calls


def _rows(usage, n, poison_at=()):
    rows = []
    for i in range(n):
        vals = {c: None for c in usage._EVENT_COLUMNS}
        vals.update(event_id=f"e{i:03d}", ts_ns=1_760_000_000_000_000_000 + i, key_hash="h", status=200,
                    tool="poison" if i in poison_at else "company")
        rows.append(tuple(vals[c] for c in usage._EVENT_COLUMNS))
    return rows


def _quarantined(usage):
    with usage._outbox_lock:
        return usage._outbox().execute("SELECT event_id, error, sqlstate FROM quarantine ORDER BY event_id").fetchall()


@pytest.mark.parametrize("make_error", [
    lambda: pg.InvalidDatetimeFormat("bad date"),
    lambda: pg.NotNullViolation("null value"),
    lambda: sqlite3.IntegrityError("NOT NULL constraint failed"),
], ids=["pg-data", "pg-integrity", "sqlite-integrity"])
def test_rejected_row_is_isolated_and_does_not_block_the_rest(boot, make_error):
    usage = boot(_FLUSH_SEC=0.05, _RETRY_MAX_SEC=0.05)
    seen, _ = _rejecting_backend(usage, lambda r: make_error() if _tool(usage, r) == "poison" else None)
    for i in range(40):
        usage.record("test-key", 200, "poison" if i == 17 else "company", i)
    assert _wait(lambda: len(seen) == 39), f"{len(seen)}/39 만 나갔다 — 거절된 한 행이 나머지를 막았다"
    assert len(_quarantined(usage)) == 1
    usage.record("test-key", 200, "company", 99)          # 격리 뒤 새 기록도 막히지 않는다
    assert _wait(lambda: len(seen) == 40)
    state = usage.stats()
    assert state["quarantined"] == 1 and state["status"] == "degraded"
    assert _wait(lambda: usage._pending_batch(limit=100) == []), "격리하고 다 썼는데 송신함이 안 비었다"


def test_isolation_cost_is_logarithmic_and_keeps_order(boot):
    """64행 중 1행 — 통째로 거절된 묶음을 다시 쓰지 않고 반씩 나눠 2·log₂64 = 12번 안에 끝낸다(한 행씩 쓰면 64번)."""
    usage = boot()
    seen, calls = _rejecting_backend(
        usage, lambda r: pg.NumericValueOutOfRange("too big") if _tool(usage, r) == "poison" else None)
    batch = _rows(usage, 64, poison_at={37})
    con, left, err, acked = usage._isolate(object(), batch, pg.NumericValueOutOfRange("too big"))   # 통째로 막 거절된 묶음
    assert (left, err, acked) == ([], None, True)
    assert len(calls) <= 12, calls
    assert [r[0] for r in seen] == [r[0] for r in batch if _tool(usage, r) != "poison"], "오래된 행부터 나가지 않았다"
    assert _quarantined(usage) == [("e037", "NumericValueOutOfRange", "22003")]


@pytest.mark.parametrize("make_error", [
    lambda: pg.UndefinedColumn("column scope does not exist"),     # DDL 적용 전 배포
    lambda: pg.InsufficientPrivilege("permission denied"),
    lambda: pg.ReadOnlySqlTransaction("read-only"),                 # Supabase 용량 초과
    lambda: pg.TooManyConnections("too many clients"),
], ids=["schema", "privilege", "read-only", "connections"])
def test_errors_that_are_not_the_rows_fault_hold_and_quarantine_nothing(boot, make_error):
    """고치면 다 들어갈 행이다 — 들고 기다렸다가 다 쓴다. 하나도 격리하지 않는다."""
    usage = boot(_FLUSH_SEC=0.05, _RETRY_MAX_SEC=0.05)
    state = {"n": 0}
    seen = []

    def _write(con, batch):
        state["n"] += 1
        if state["n"] <= 3:
            raise make_error()
        seen.extend(batch)

    usage._connect = lambda: object()
    usage._write = _write
    usage._recover = lambda con: con
    for i in range(10):
        usage.record("test-key", 200, "company", i)
    assert _wait(lambda: len(seen) == 10)
    assert _quarantined(usage) == []


@pytest.mark.parametrize("n", [1, 5])   # 한 행짜리 묶음은 나눌 것 없이 곧장 격리된다 — 워커 쪽 보호가 없으면 여기서 샌다
def test_connection_failures_are_never_judged_as_the_rows_fault(boot, n):
    """접속 단계의 실패는 그 오류가 무엇이든 행 탓이 아니다 — 판정 함수가 뭐라 하든 격리하지 않는다(구조로 막는다)."""
    usage = boot(_FLUSH_SEC=0.05, _RETRY_MAX_SEC=0.05)
    seen = _backend(usage)
    attempts = {"n": 0}

    def flaky_connect():
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise pg.InvalidDatetimeFormat("행 탓처럼 보이는 접속 실패")
        return object()

    usage._connect = flaky_connect
    for i in range(n):
        usage.record("test-key", 200, "company", i)
    assert _wait(lambda: len(seen) == n)
    assert _quarantined(usage) == []

    # `_isolate` 도중의 접속 실패도 같다 — 못 푼 행을 그대로 돌려준다
    usage._connect = lambda: (_ for _ in ()).throw(pg.InvalidDatetimeFormat("x"))
    batch = _rows(usage, 4)
    con, left, err, _ = usage._isolate(None, batch, pg.InvalidDatetimeFormat("x"))
    assert con is None and left == batch and isinstance(err, pg.InvalidDatetimeFormat)
    assert _quarantined(usage) == []


def test_a_tripping_classifier_falls_back_to_holding(boot):
    usage = boot()

    def boom(e):
        raise RuntimeError("판정이 넘어짐")

    usage._is_permanent = boom
    assert usage._permanent(pg.InvalidDatetimeFormat("x")) is False


def test_quarantine_keeps_rows_without_error_text_and_requeue_brings_them_back(boot):
    usage = boot()
    usage._ensure_worker = lambda: None
    rows = _rows(usage, 2)
    usage._spool(rows)
    for r in rows:
        usage._quarantine(r, pg.InvalidDatetimeFormat("secret-value 2026-13-45"))
    assert usage._pending_batch() == []
    assert _quarantined(usage) == [("e000", "InvalidDatetimeFormat", "22007"),
                                   ("e001", "InvalidDatetimeFormat", "22007")], "오류 원문(거절된 값)을 적었다"
    assert [(e, s, n, g) for e, s, n, g, _ in usage._quarantine_summary()] == [("InvalidDatetimeFormat", "22007", 2, 0)]
    assert usage.stats()["quarantined"] == 2
    assert usage._requeue() == 2
    assert usage._pending_batch() == rows
    assert _attempts(usage) == {"e000": 0, "e001": 0}, "되돌리기가 거절 횟수를 안 지웠다 — 자동 재시도가 다시 안 열린다"
    usage._acknowledge(rows)                               # 다시 써서 들어갔다
    assert _quarantined(usage) == [] and usage._pending_batch() == [], "들어간 행이 격리 표에 남았다"


@pytest.mark.parametrize("error, permanent", [
    (pg.InvalidDatetimeFormat("x"), True),
    (pg.InvalidTextRepresentation("x"), True),
    (pg.NumericValueOutOfRange("x"), True),
    (pg.StringDataRightTruncation("x"), True),
    (pg.NotNullViolation("x"), True),
    (pg.CheckViolation("x"), True),
    (sqlite3.IntegrityError("x"), True),
    (pg.UndefinedColumn("x"), False),
    (pg.UndefinedTable("x"), False),
    (pg.InsufficientPrivilege("x"), False),
    (pg.AdminShutdown("x"), False),
    (pg.TooManyConnections("x"), False),
    (pg.SerializationFailure("x"), False),
    (pg.DeadlockDetected("x"), False),
    (pg.QueryCanceled("x"), False),
    (pg.ReadOnlySqlTransaction("x"), False),
    (pg.DiskFull("x"), False),
    (PgOperationalError("connection refused"), False),
    (sqlite3.OperationalError("no such column"), False),
    (ConnectionError("x"), False),
    (TimeoutError("x"), False),
], ids=lambda v: type(v).__name__ if isinstance(v, Exception) else str(v))
def test_classifier_contract_for_unambiguous_errors(boot, error, permanent):
    """값(22)·제약(23) 위반만 행 탓이다. 스키마·권한(42)·접속·과부하·읽기 전용은 고치면 들어갈 행이다."""
    assert boot()._permanent(error) is permanent


def _attempts(usage):
    with usage._outbox_lock:
        return dict(usage._outbox().execute("SELECT event_id, attempts FROM quarantine").fetchall())


@pytest.mark.parametrize("error, permanent", [
    (pg.ProgrammingError("cannot adapt type 'dict'"), True),     # 서버에 닿기 전 — sqlstate 없음
    (sqlite3.ProgrammingError("Error binding parameter 5"), True),
    (OverflowError("Python int too large to convert to SQLite INTEGER"), True),
    (TypeError("x"), True),
    (ValueError("x"), True),
    (pg.InterfaceError("the cursor is closed"), False),           # 드라이버 상태 — 행 탓 아님
    (sqlite3.InterfaceError("x"), False),
], ids=lambda v: type(v).__name__ if isinstance(v, Exception) else str(v))
def test_failures_before_reaching_the_server_are_the_rows_fault(boot, error, permanent):
    """261018 결정 — 값을 DB 로 넘기지도 못한 실패도 그 행 탓으로 본다. 격리는 되돌릴 수 있고, 막힘은 쌓이기만 한다."""
    assert boot()._permanent(error) is permanent


def test_unsendable_value_is_isolated_with_the_real_sqlite_backend(boot):
    """실제 sqlite 백엔드 — 숫자 칸에 넘길 수 없는 값(dict)이 실린 한 행만 격리되고 나머지 9행은 들어간다."""
    usage = boot(_FLUSH_SEC=0.05)
    for i in range(10):
        usage.record("test-key", 200, "company", {"bad": 1} if i == 4 else i)
    assert _wait(lambda: len(_quarantined(usage)) == 1)
    usage.flush(timeout=5)
    con = sqlite3.connect(usage.DB_PATH)
    try:
        assert con.execute("SELECT count(*) FROM events").fetchone()[0] == 9
    finally:
        con.close()
    assert _quarantined(usage)[0][1:] == ("ProgrammingError", None)


def test_quarantined_row_is_retried_when_the_next_process_starts(boot):
    """원인을 고친 배포 뒤 — 다음 프로세스가 시작하며 격리 행을 다시 써 넣고 격리 표에서 지운다."""
    first = boot(_FLUSH_SEC=0.05, _RETRY_MAX_SEC=0.05)
    _rejecting_backend(first, lambda r: pg.NumericValueOutOfRange("x") if _tool(first, r) == "poison" else None)
    first.record("test-key", 200, "poison", 1)
    assert _wait(lambda: len(_quarantined(first)) == 1)
    poison_id = _quarantined(first)[0][0]
    first.flush(timeout=5)

    fixed = boot(_FLUSH_SEC=0.05)
    seen = _backend(fixed)                     # 이번 배포는 그 값을 받아 준다
    fixed.stats()                              # /health 점검 — 새 호출 없이 워커가 뜬다
    assert _wait(lambda: [r[0] for r in seen] == [poison_id]), "격리 행을 다시 해 보지 않았다"
    assert _wait(lambda: _quarantined(fixed) == [])
    assert fixed.stats()["quarantined"] == 0 and fixed.stats()["status"] == "ok"


def test_automatic_retries_stop_after_the_limit_until_requeue(boot):
    """같은 이유로 계속 거절되면 시작할 때마다 한 번씩, 상한(3번)까지만 — 그 뒤엔 사람이 `requeue` 할 때까지 둔다."""
    reject = lambda usage: (lambda r: pg.NumericValueOutOfRange("x") if _tool(usage, r) == "poison" else None)
    first = boot(_FLUSH_SEC=0.05, _RETRY_MAX_SEC=0.05)
    _rejecting_backend(first, reject(first))
    first.record("test-key", 200, "poison", 1)
    assert _wait(lambda: list(_attempts(first).values()) == [1])
    first.flush(timeout=5)
    for expected in (2, 3):                    # 재시작 두 번 — 한 번씩 다시 해 보고 또 거절된다
        again = boot(_FLUSH_SEC=0.05, _RETRY_MAX_SEC=0.05)
        _rejecting_backend(again, reject(again))
        again.stats()
        assert _wait(lambda: list(_attempts(again).values()) == [expected]), _attempts(again)
        again.flush(timeout=5)

    done = boot(_FLUSH_SEC=0.05, _RETRY_MAX_SEC=0.05)
    _, calls = _rejecting_backend(done, reject(done))
    done.stats()
    time.sleep(0.3)
    assert calls == [] and done._pending_batch() == [], "상한을 넘겼는데 또 다시 해 봤다"
    assert [g for *_, g, _ in done._quarantine_summary()] == [1]
    assert done._requeue() == 1 and list(_attempts(done).values()) == [0]
