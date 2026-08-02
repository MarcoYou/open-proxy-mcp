"""사용 통계 기록기 — inbound MCP 요청을 누적(요청 1건 = 이벤트 1건).

백엔드 2가지(환경변수로 자동 선택):
  - **DATABASE_URL 설정됨 → Postgres**(Supabase). 머신 바깥 중앙 DB → 무손실·합산 불필요.
  - 미설정 → sqlite(`OPM_USAGE_DB_PATH`, 로컬 개발/폴백).

키는 **SHA-256 해시로만** 저장(평문 DART 키 미보관). 본인 키(SELF_HASHES)는 기록 스킵.

설계 원칙(프로덕션 요청 경로 보호):
- 기록은 **백그라운드 워커 스레드 큐** → 요청 경로에서 DB I/O를 만지지 않음(지연 0).
- 큐 풀·DB 오류가 나도 **요청을 절대 깨지 않음**(전부 swallow). 워커는 죽지 않고 재연결.

CLI:  python -m open_proxy_mcp.usage dump   # (sqlite 백엔드용) events를 JSONL로 출력
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
import queue
import sqlite3
import sys
import threading
import time

DATABASE_URL = os.environ.get("DATABASE_URL")  # 있으면 Postgres, 없으면 sqlite
DB_PATH = os.environ.get("OPM_USAGE_DB_PATH", "/data/usage.db")
MACHINE = os.environ.get("FLY_MACHINE_ID", "local")
_USE_PG = bool(DATABASE_URL)

# 본인(운영자) 키 — 아예 기록하지 않음. 평문 미보관, SHA-256 해시로만 비교.
#   6f02e8… = 운영자 opendart 키의 SHA-256 (평문 프리픽스는 주석에도 남기지 않음)
SELF_HASHES = {
    "6f02e8598b1bdcda660c970ca9c07c1ffba1d4d8ec193157991f7dc2a9173c30",
}

_q: "queue.Queue[tuple]" = queue.Queue(maxsize=10000)
_counter = itertools.count()
_worker_started = False
_lock = threading.Lock()


# ── 백엔드: sqlite ─────────────────────────────────────────────────────────
def _sqlite_connect():
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS events(
            event_id TEXT PRIMARY KEY, ts_ns INTEGER NOT NULL,
            key_hash TEXT NOT NULL, status INTEGER);
        CREATE INDEX IF NOT EXISTS idx_events_hash ON events(key_hash);
        CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts_ns);
        """
    )
    for col in ("tool TEXT", "latency_ms INTEGER", "is_error INTEGER", "error_kind TEXT",
                "doc_cache_hit INTEGER", "response_bytes INTEGER"):  # 기존 테이블 마이그레이션
        try:
            con.execute(f"ALTER TABLE events ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass  # 이미 있음
    return con


def _sqlite_write(con, batch):
    con.executemany(
        "INSERT OR IGNORE INTO events(event_id, ts_ns, key_hash, status, tool, latency_ms, is_error, error_kind, "
        "doc_cache_hit, response_bytes) VALUES(?,?,?,?,?,?,?,?,?,?)", batch
    )
    con.commit()


# ── 백엔드: Postgres ───────────────────────────────────────────────────────
def _pg_connect():
    import psycopg
    con = psycopg.connect(DATABASE_URL, connect_timeout=15, autocommit=False)
    con.execute(
        "CREATE TABLE IF NOT EXISTS tool_call_events("
        "event_id text PRIMARY KEY, ts_ns bigint NOT NULL, key_hash text NOT NULL, status int)"
    )
    con.execute("ALTER TABLE tool_call_events ADD COLUMN IF NOT EXISTS tool text")
    con.execute("ALTER TABLE tool_call_events ADD COLUMN IF NOT EXISTS latency_ms int")
    con.execute("ALTER TABLE tool_call_events ADD COLUMN IF NOT EXISTS is_error boolean")
    con.execute("ALTER TABLE tool_call_events ADD COLUMN IF NOT EXISTS error_kind text")
    # 260802: 캐시를 키울 값어치가 있나 · 어느 tool 이 토큰을 많이 먹나 — 두 질문에
    # 답하려고 더한다. **회사·인자는 남기지 않는다**(「사용자 조회 결과 저장 안 함」).
    con.execute("ALTER TABLE tool_call_events ADD COLUMN IF NOT EXISTS doc_cache_hit boolean")
    con.execute("ALTER TABLE tool_call_events ADD COLUMN IF NOT EXISTS response_bytes int")
    con.execute("CREATE INDEX IF NOT EXISTS idx_events_hash ON tool_call_events(key_hash)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON tool_call_events(ts_ns)")
    con.commit()
    return con


def _pg_write(con, batch):
    con.cursor().executemany(
        "INSERT INTO tool_call_events(event_id, ts_ns, key_hash, status, tool, latency_ms, is_error, error_kind, "
        "doc_cache_hit, response_bytes) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (event_id) DO NOTHING",
        batch,
    )
    con.commit()


_connect = _pg_connect if _USE_PG else _sqlite_connect
_write = _pg_write if _USE_PG else _sqlite_write


# ── 워커 ───────────────────────────────────────────────────────────────────
def _worker() -> None:
    """큐를 비우며 배치 기록. 요청 스레드와 분리돼 지연을 만들지 않음. 절대 죽지 않음."""
    con = None
    while True:
        try:
            batch = [_q.get()]
            try:
                for _ in range(199):
                    batch.append(_q.get_nowait())
            except queue.Empty:
                pass
            if con is None:
                con = _connect()
            _write(con, batch)
        except Exception as e:
            sys.stderr.write(f"[usage] write 실패(무시·재연결): {e}\n")
            try:
                if con:
                    con.close()
            except Exception:
                pass
            con = None
            time.sleep(2)


def _ensure_worker() -> None:
    global _worker_started
    if _worker_started:
        return
    with _lock:
        if _worker_started:
            return
        threading.Thread(target=_worker, name="usage-writer", daemon=True).start()
        _worker_started = True


def record(opendart_key: str, status: int, tool=None, latency_ms=None, is_error=None,
           error_kind=None, doc_cache_hit=None, response_bytes=None) -> None:
    """요청 1건 기록. 요청 경로에서 호출 — 절대 예외를 던지지 않음, 절대 블록하지 않음.
    tool=호출한 MCP method/tool명, latency_ms=처리 시간(ms),
    is_error=tools/call 응답의 isError(툴 내부 실패; HTTP 200이어도 True 가능),
    error_kind=is_error일 때 예외 분류(timeout/upstream/crash/unknown; tools 래퍼가 붙인
    `[ekind=...]` 태그에서 추출). 에러 메시지 원문은 저장하지 않음.
    doc_cache_hit=DART 문서를 캐시에서 꺼냈는지(캐시 확대 가치 판단용),
    response_bytes=응답 본문 바이트(호출측이 무는 토큰 비용의 대리 지표 —
    한글 UTF-8 은 글자당 3바이트라 토큰 수와 비례한다).
    **어느 회사를 조회했는지는 기록하지 않는다** — 「사용자 조회 결과 저장 안 함」."""
    try:
        khash = hashlib.sha256(opendart_key.lower().encode()).hexdigest()
        if khash in SELF_HASHES:
            return  # 본인 키는 기록하지 않음
        _ensure_worker()
        ts_ns = time.time_ns()
        ev_id = f"{ts_ns}-{MACHINE}-{next(_counter)}"
        _q.put_nowait((ev_id, ts_ns, khash, int(status), tool, latency_ms, is_error,
                       error_kind, doc_cache_hit, response_bytes))
    except Exception:
        pass


def _dump() -> None:
    """(sqlite 백엔드) events를 JSONL로 stdout 출력."""
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=10)
    except Exception:
        return
    for row in con.execute("SELECT event_id, ts_ns, key_hash, status FROM events"):
        sys.stdout.write(json.dumps(row) + "\n")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "dump":
        _dump()
