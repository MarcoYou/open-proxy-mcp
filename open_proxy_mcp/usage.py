"""사용 통계 기록기 — inbound MCP 요청을 누적(요청 1건 = 이벤트 1건).

기록 조건: **fly 머신에서만**(`FLY_MACHINE_ID`). 로컬은 `OPM_USAGE_LOCAL=1` 로만 연다
— 근거는 `_RECORDING` 주석.

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

import datetime as _dt
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

#: **기록은 fly 머신에서만 한다.** dart/client.py 가 import 시 `load_dotenv()` 를 돌려
#: 로컬에서도 DATABASE_URL 이 채워지므로, 막지 않으면 **로컬 pytest·pilot·스크립트가
#: 운영 Postgres 에 그대로 쓴다**(260810 실측: 게이트 전용 이름 `no_such_tool_xyz` 20건과
#: 호스트거부 58건이 운영 통계에 섞여 있었고, 그 키해시는 테스트 리터럴 `"k"` 였다).
#: 키 목록(SELF_HASHES)으로는 못 막는다 — 테스트마다 리터럴이 바뀌므로 쫓아다니게 된다.
#: 막을 자리는 「누가 불렀나」가 아니라 **「여기가 운영인가」**다.
#: 기록 경로 자체를 로컬에서 시험해야 하면 `OPM_USAGE_LOCAL=1` 로 연다.
_RECORDING = MACHINE != "local" or os.environ.get("OPM_USAGE_LOCAL") == "1"

_q: "queue.Queue[tuple]" = queue.Queue(maxsize=10000)
_counter = itertools.count()
_worker_started = False
_lock = threading.Lock()


_KST = _dt.timezone(_dt.timedelta(hours=9))
#: corp_codes 는 이벤트 행에 **안 적는다.** 같은 행에 `key_hash`·`ts_ns` 가 있어서, 셋이
#: 붙으면 「이 사용자가 언제 어느 기업을 조사했는지」가 한 줄 쿼리로 나온다 — 재무분석가·
#: 기관투자자에게 **무엇을 언제 조사했는가는 그 자체가 정보**다. 회사 이름이 공개라는 것과
#: 무관하다(공시 전에 어떤 회사를 며칠 들여다봤는지가 드러나는 문제). key_hash 는 익명이
#: 아니라 **가명**이라 같은 사람인지는 알 수 있고, DART 키는 실명 등록에 묶여 있다.
#:
#: 그런데 우리가 원한 답(「어느 기업이 많이 조회되나」)은 **누가 봤는지를 몰라도 된다.**
#: 그래서 쓰는 시점에 사용자를 떼고 `(날짜, 기업)` 카운터로만 올린다 —
#: 값어치는 그대로 남고 부채만 사라진다. 260810 실측 1,041개 기업·상위 100건대.
def _corp_counts(batch):
    """배치 → {(날짜, corp_code): 건수}. **key_hash 를 들고 나오지 않는다** — 여기가 연결을
    끊는 자리다. 이 함수가 해시를 반환하기 시작하면 부채가 되살아난다."""
    from collections import defaultdict
    agg = defaultdict(int)
    for r in batch:
        codes = r[_CORP_IDX]
        if not codes:
            continue
        day = _dt.datetime.fromtimestamp(r[1] / 1e9, _KST).date()
        for c in codes.split(","):
            if c:
                agg[(day, c)] += 1
    return agg


#: 큐 튜플에서 corp_codes 의 자리. 이벤트 INSERT 에서는 이 자리를 **빼고** 넣는다.
_CORP_IDX = 12


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
        CREATE TABLE IF NOT EXISTS corp_daily(
            day TEXT NOT NULL, corp_code TEXT NOT NULL, requests INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (day, corp_code));
        """
    )
    for col in ("tool TEXT", "latency_ms INTEGER", "is_error INTEGER", "error_kind TEXT",
                "doc_cache_hit INTEGER", "response_bytes INTEGER",
                "doc_mem_hits INTEGER", "doc_disk_hits INTEGER", "doc_misses INTEGER",
                "corp_codes TEXT",
                "fetch_viewer INTEGER", "fetch_kind INTEGER",
                "web_wait_ms INTEGER", "weak_kinds TEXT"):  # 기존 테이블 마이그레이션
        try:
            con.execute(f"ALTER TABLE events ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass  # 이미 있음
    return con


def _sqlite_write(con, batch):
    # 컬럼명을 반드시 명시한다 — ADD COLUMN 으로 물리적 순서가 바뀌면 위치 의존 INSERT 는
    # **조용히 다른 컬럼에 값을 넣는다**(260704 mkt_fund_hist 사고).
    con.executemany(
        "INSERT OR IGNORE INTO events(event_id, ts_ns, key_hash, status, tool, latency_ms, is_error, error_kind, "
        "response_bytes, doc_mem_hits, doc_disk_hits, doc_misses, "
        "fetch_viewer, fetch_kind, web_wait_ms, weak_kinds) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        [r[:_CORP_IDX] + r[_CORP_IDX + 1:] for r in batch]
    )
    rows = [(str(d), c, n) for (d, c), n in _corp_counts(batch).items()]
    if rows:
        con.executemany(
            "INSERT INTO corp_daily(day, corp_code, requests) VALUES(?,?,?) "
            "ON CONFLICT(day, corp_code) DO UPDATE SET requests = requests + excluded.requests",
            rows)
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
    # 260802: 캐시를 키울 값어치가 있나 · 어느 tool 이 토큰을 많이 먹나 — 두 질문에 답하려고 더했다.
    # doc_cache_hit 은 **폐기**한다(값이 한 번도 안 들어왔다 — 266,615건 전부 NULL. 하류에서
    # ContextVar 를 set 해 위에서 읽는 구조였는데 그건 원리상 안 된다). 컬럼은 남겨 두되
    # 더는 쓰지 않고, 아래 doc_mem_hits/doc_disk_hits/doc_misses 가 대신한다.
    con.execute("ALTER TABLE tool_call_events ADD COLUMN IF NOT EXISTS doc_cache_hit boolean")
    con.execute("ALTER TABLE tool_call_events ADD COLUMN IF NOT EXISTS response_bytes int")
    # 260804: 문서 출처를 **건수로** 나눠 센다. 메모리 예산의 효과를 보려면 디스크 적중과
    # 섞으면 안 된다(디스크는 예산과 무관하다).
    con.execute("ALTER TABLE tool_call_events ADD COLUMN IF NOT EXISTS doc_mem_hits int")
    con.execute("ALTER TABLE tool_call_events ADD COLUMN IF NOT EXISTS doc_disk_hits int")
    con.execute("ALTER TABLE tool_call_events ADD COLUMN IF NOT EXISTS doc_misses int")
    # 260804: 이 요청이 해석해 낸 기업(8자리 corp_code, 쉼표 구분). 사용자가 친 원문은
    # 남기지 않는다 — 정규화된 코드만 남아야 집계가 뜻을 가지고, 자유 텍스트도 안 쌓인다.
    con.execute("ALTER TABLE tool_call_events ADD COLUMN IF NOT EXISTS corp_codes text")
    # 260810: 원문을 **어느 경로로** 받았나. 주 경로(document.xml API)는 doc_misses 가 이미
    # 세므로 여기엔 폴백만 둔다 — viewer HTML(고정 2초 간격)·KIND(1~3초 랜덤).
    # web_wait_ms 는 그 간격 때문에 **실제로 잠든** 시간이다. 「2초가 비싼가」는 빈도만으론
    # 못 정한다: 폴백이 드물면 2초는 공짜고, 잦으면 간격이 아니라 주 경로를 고쳐야 한다.
    con.execute("ALTER TABLE tool_call_events ADD COLUMN IF NOT EXISTS fetch_viewer int")
    con.execute("ALTER TABLE tool_call_events ADD COLUMN IF NOT EXISTS fetch_kind int")
    con.execute("ALTER TABLE tool_call_events ADD COLUMN IF NOT EXISTS web_wait_ms int")
    # 260810: 이름이 정확히 안 맞아 **추정으로 고른** 해석의 방식(normalized·token·substring·
    # fuzzy). 이미 계산해 사용자에게 warning 으로 보여주면서 기록만 안 하고 있었다.
    # **사용자가 친 원문(`query`)은 절대 싣지 않는다** — 여기 넣으면 「질의 원문 미보관」
    # 정책이 그 자리에서 깨진다. 방식 이름만 남겨도 「우리가 얼마나 자주 찍었나」는 답한다.
    con.execute("ALTER TABLE tool_call_events ADD COLUMN IF NOT EXISTS weak_kinds text")
    con.execute("CREATE INDEX IF NOT EXISTS idx_events_hash ON tool_call_events(key_hash)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_events_ts ON tool_call_events(ts_ns)")
    # corp_codes 컬럼은 **더 쓰지 않는다**(위 _corp_counts 주석). 260810 이전 값은 백필
    # 스크립트가 corp_daily 로 옮긴 뒤 비운다. 컬럼과 인덱스는 남겨 두되 기록하지 않는다.
    con.execute("CREATE TABLE IF NOT EXISTS corp_daily("
                "day date NOT NULL, corp_code text NOT NULL, "
                "requests int NOT NULL DEFAULT 0, PRIMARY KEY (day, corp_code))")
    con.commit()
    return con


def _pg_write(con, batch):
    # 컬럼명 명시 — 위치 의존 INSERT 는 ADD COLUMN 후 조용히 어긋난다(260704 사고).
    con.cursor().executemany(
        "INSERT INTO tool_call_events(event_id, ts_ns, key_hash, status, tool, latency_ms, is_error, error_kind, "
        "response_bytes, doc_mem_hits, doc_disk_hits, doc_misses, "
        "fetch_viewer, fetch_kind, web_wait_ms, weak_kinds) "
        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (event_id) DO NOTHING",
        [r[:_CORP_IDX] + r[_CORP_IDX + 1:] for r in batch],
    )
    rows = [(d, c, n) for (d, c), n in _corp_counts(batch).items()]
    if rows:
        con.cursor().executemany(
            "INSERT INTO corp_daily(day, corp_code, requests) VALUES(%s,%s,%s) "
            "ON CONFLICT (day, corp_code) DO UPDATE SET "
            "requests = corp_daily.requests + EXCLUDED.requests", rows)
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
           error_kind=None, response_bytes=None,
           doc_mem_hits=None, doc_disk_hits=None, doc_misses=None, corp_codes=None,
           fetch_viewer=None, fetch_kind=None, web_wait_ms=None, weak_kinds=None) -> None:
    """요청 1건 기록. 요청 경로에서 호출 — 절대 예외를 던지지 않음, 절대 블록하지 않음.
    tool=호출한 MCP method/tool명, latency_ms=처리 시간(ms),
    is_error=tools/call 응답의 isError(툴 내부 실패; HTTP 200이어도 True 가능),
    error_kind=is_error일 때 예외 분류(timeout/upstream/crash/unknown; tools 래퍼가 붙인
    `[ekind=...]` 태그에서 추출). 에러 메시지 원문은 저장하지 않음.
    response_bytes=응답 본문 바이트(호출측이 무는 토큰 비용의 대리 지표 —
    한글 UTF-8 은 글자당 3바이트라 토큰 수와 비례한다).
    doc_mem_hits/doc_disk_hits/doc_misses=이 요청이 받은 문서를 출처별로 센 건수.
    메모리 예산의 효과는 doc_mem_hits 로만 봐야 한다 — 디스크는 예산 밖이다.
    fetch_viewer/fetch_kind=폴백 경로로 나간 웹 요청 수(주 경로는 doc_misses 가 센다),
    web_wait_ms=그 폴백의 예의 간격 때문에 실제로 잠든 시간(ms),
    weak_kinds=이름이 정확히 안 맞아 추정으로 고른 해석의 **방식**만(원문 미보관).
    corp_codes=이 요청이 **해석해 낸** 기업 코드 목록(사용자가 친 원문은 남기지 않는다).

    260804 이전에는 「회사는 기록하지 않는다」였다. 집계로 무엇이 많이 쓰이는지 보려고
    바꿨다 — 다만 남기는 것은 질의 원문이 아니라 정규화된 8자리 코드뿐이고,
    key_hash 와 함께 남으므로 **사용자별 조사 이력**이 된다는 점을 알고 켠 것이다."""
    if not _RECORDING:
        return  # 로컬(pytest·pilot·스크립트)은 운영 통계를 오염시키지 않는다
    try:
        khash = hashlib.sha256(opendart_key.lower().encode()).hexdigest()
        if khash in SELF_HASHES:
            return  # 본인 키는 기록하지 않음
        _ensure_worker()
        ts_ns = time.time_ns()
        ev_id = f"{ts_ns}-{MACHINE}-{next(_counter)}"
        codes = ",".join(corp_codes) if corp_codes else None
        _q.put_nowait((ev_id, ts_ns, khash, int(status), tool, latency_ms, is_error,
                       error_kind, response_bytes, doc_mem_hits, doc_disk_hits,
                       doc_misses, codes, fetch_viewer, fetch_kind, web_wait_ms,
                       weak_kinds))
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
