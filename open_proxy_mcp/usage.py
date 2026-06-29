"""사용 통계 기록기 — inbound MCP 요청을 Fly 볼륨 sqlite에 직접 누적.

왜: 기존엔 Fly Logs API를 긁었지만(7일 보존·401·로컬 Mac 의존), 앱이 요청 시점에 직접 기록하면
누락 0·무인·항상가동. 키는 **SHA-256 해시로만** 저장(평문 DART 키 미보관).

설계 원칙(프로덕션 요청 경로 보호):
- 기록은 **백그라운드 워커 스레드 큐**로 처리 → 요청 경로에서 디스크 I/O를 만지지 않음(지연 0).
- 큐가 가득 차거나 DB 오류가 나도 **요청을 절대 깨지 않음**(전부 swallow).
- Fly 볼륨은 머신별 분리 → 각 머신이 자기 /data/usage.db에 기록. 합산은 usage_tracker.py --pull.

CLI:  python -m open_proxy_mcp.usage dump   # 이 머신의 events를 JSONL로 출력(--pull이 사용)
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

DB_PATH = os.environ.get("OPM_USAGE_DB_PATH", "/data/usage.db")
MACHINE = os.environ.get("FLY_MACHINE_ID", "local")

# 본인(운영자) 키 — 아예 기록하지 않음. 평문 미보관, SHA-256 해시로만 비교.
#   6f02e8…  = opendart=33ac18b8…(Marco 본인 키)
SELF_HASHES = {
    "6f02e8598b1bdcda660c970ca9c07c1ffba1d4d8ec193157991f7dc2a9173c30",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events(
    event_id TEXT PRIMARY KEY,
    ts_ns    INTEGER NOT NULL,
    key_hash TEXT NOT NULL,
    status   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_events_hash ON events(key_hash);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts_ns);
"""

_q: "queue.Queue[tuple]" = queue.Queue(maxsize=10000)
_counter = itertools.count()
_worker_started = False
_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH, timeout=10)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.executescript(_SCHEMA)
    return con


def _worker() -> None:
    """큐를 비우며 배치 INSERT. 요청 스레드와 분리돼 지연을 만들지 않음."""
    con = None
    while True:
        try:
            batch = [_q.get()]
            # 잠깐 모아 배치 커밋(부하 시 디스크 효율↑)
            try:
                for _ in range(199):
                    batch.append(_q.get_nowait())
            except queue.Empty:
                pass
            if con is None:
                con = _connect()
            con.executemany(
                "INSERT OR IGNORE INTO events(event_id, ts_ns, key_hash, status) VALUES(?,?,?,?)",
                batch,
            )
            con.commit()
        except Exception as e:  # 워커는 절대 죽지 않음 — 다음 배치 계속
            sys.stderr.write(f"[usage] write 실패(무시): {e}\n")
            try:
                if con:
                    con.close()
            except Exception:
                pass
            con = None
            time.sleep(1)


def _ensure_worker() -> None:
    global _worker_started
    if _worker_started:
        return
    with _lock:
        if _worker_started:
            return
        t = threading.Thread(target=_worker, name="usage-writer", daemon=True)
        t.start()
        _worker_started = True


def record(opendart_key: str, status: int) -> None:
    """요청 1건 기록. 요청 경로에서 호출 — 절대 예외를 던지지 않음, 절대 블록하지 않음."""
    try:
        khash = hashlib.sha256(opendart_key.lower().encode()).hexdigest()
        if khash in SELF_HASHES:
            return  # 본인 키는 기록하지 않음
        _ensure_worker()
        ts_ns = time.time_ns()
        ev_id = f"{ts_ns}-{MACHINE}-{next(_counter)}"
        _q.put_nowait((ev_id, ts_ns, khash, int(status)))
    except Exception:
        pass  # 큐 풀·해시 실패 등 어떤 경우에도 요청을 깨지 않음


def _dump() -> None:
    """이 머신의 events를 JSONL로 stdout 출력 (usage_tracker.py --pull 용)."""
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=10)
    except Exception:
        return  # DB 없음 → 빈 출력
    for row in con.execute("SELECT event_id, ts_ns, key_hash, status FROM events"):
        sys.stdout.write(json.dumps(row) + "\n")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "dump":
        _dump()
