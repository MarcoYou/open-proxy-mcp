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
- **묶어 쓴다**(261018, 재설계 4단계 T7): 첫 행부터 `_FLUSH_SEC`(기본 5초) 동안 모아 한 트랜잭션. 종전엔 호출 1건 = 확정 1번.
- **못 쓴 묶음은 들고 있다가 다시 쓴다** — 종전엔 버렸다. 다시 써도 두 번 세지 않는다(`_pg_write`).
- **종료 때 마지막 묶음을 쓴다** — 종료 신호(`begin_shutdown`, server._serve)에서 모으기를 멈추고 바로 쓰고, atexit(`flush`)에서 끝낸다.
- **서버는 DDL 을 보내지 않는다**(261018). Postgres 스키마 정본은 `scripts/ops_ddl.py` — 배포 전에 사람이 적용한다.

CLI:  python -m open_proxy_mcp.usage dump   # (sqlite 백엔드용) events를 JSONL로 출력
"""
from __future__ import annotations

import atexit
import datetime as _dt
import hashlib
import itertools
import json
import os
import queue
import re
import sqlite3
import sys
import threading
import time

DATABASE_URL = os.environ.get("DATABASE_URL")  # 있으면 Postgres, 없으면 sqlite
DB_PATH = os.environ.get("OPM_USAGE_DB_PATH", "/data/usage.db")
MACHINE = os.environ.get("FLY_MACHINE_ID", "local")
_USE_PG = bool(DATABASE_URL)

# 본인(운영자) 키 — 아예 기록하지 않음. 평문 미보관, SHA-256 해시로만 비교.
#   6f02e8… = 운영자 opendart 키 1번 · fa30e2… = 2번 (평문·프리픽스는 주석에도 남기지 않음)
# 여기 넣으면 **두 방향 모두** 처리된다: 앞으로는 기록을 스킵하고(아래 record 경로),
# 이미 쌓인 이벤트도 startup_metrics.load()가 SELF_HASHES를 걸러 통계·덱에서 빠진다.
# 260817: 2번 키가 1,034건 쌓여 사용자 1명·호출로 집계되고 있었다.
SELF_HASHES = {
    "6f02e8598b1bdcda660c970ca9c07c1ffba1d4d8ec193157991f7dc2a9173c30",
    "fa30e25e593c8d4676cf7e548c1911c7ef44f14c8b7aebad47cfe05eecd61421",
}

#: **프로토콜(핸드셰이크) 요청인가** — MCP 클라이언트가 *사람이 시키지 않아도* 보내는 것.
#: `initialize`·`ping`·`tools/list`·`notifications/*` 등. tool 이 None 인 것(본문 파싱 실패:
#: 배치 요청·GET/DELETE)도 도구 호출이 아니므로 여기 넣는다.
#: **이 함수가 정의의 SSOT** — `scripts/usage_tracker.py` 가 이걸 import 한다(정의가 둘이면 갈라진다).
def is_protocol(tool) -> bool:
    return (not tool) or ("/" in tool) or tool in {"initialize", "ping"}


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

#: 묶음 간격(초). 첫 행이 들어온 뒤 이만큼 더 모아 **한 트랜잭션**으로 쓴다(261018 T7). 종전엔 호출 1건마다
#: INSERT+집계+COMMIT 이었다. 늘리면 확정 횟수가 줄고, 종료 신호 없이 죽을 때(강제 종료) 잃을 수 있는 폭이 커진다.
_FLUSH_SEC = float(os.environ.get("OPM_USAGE_FLUSH_SEC", "5") or 5)
#: 한 묶음의 상한. 실패한 묶음은 이보다 커지지 않는다 — 다음 묶음은 들고 있던 것 뒤에 **상한까지만** 덧붙이므로,
#: DB 가 오래 죽어 있어도 기억에 남는 것은 이 묶음 하나 + 큐(10,000)다. 넘치면 `record()` 가 새 것을 버린다.
_MAX_BATCH = 500
#: 실패 뒤 다시 쓰기까지 기다리는 상한(초). 2·4·8…초로 늘어 여기서 멈춘다 — 스키마가 덜 맞은 채 배포돼도
#: 접속을 두드리는 횟수가 분당 1번을 넘지 않는다(종전: 2초마다 재접속).
_RETRY_MAX_SEC = 60.0
#: 끝내는 중에 실패했을 때 다시 해 보는 횟수(0.2초 간격). 종료를 붙들지 않을 만큼만 — `flush(timeout)` 안에 든다.
_STOP_RETRIES = 2
#: 잠든 워커를 깨우는 표지. 큐에 행 대신 들어간다 — 워커는 세지 않고 버린다.
_WAKE = object()
_draining = threading.Event()   # 종료 신호를 받음 — 더 모으지 않고 바로 쓴다(워커는 계속 돈다)
_stopping = threading.Event()   # 프로세스가 끝남 — 남은 것을 쓰고 워커가 끝난다
_stopped = threading.Event()


_KST = _dt.timezone(_dt.timedelta(hours=9))
#: corp_codes 는 이벤트 행에 **적는다** — 260817 결정. 260810~260817 사이에만 안 적혔다.
#:
#: 260810 에 뺐던 이유는 지금도 그대로 유효하다: 같은 행에 `key_hash`·`ts_ns` 가 있어서
#: 셋이 붙으면 「이 사용자가 언제 어느 기업을 조사했는지」가 한 줄 쿼리로 나온다. 재무분석가·
#: 기관투자자에게 **무엇을 언제 조사했는가는 그 자체가 정보**다(회사 이름이 공개인 것과 무관 —
#: 공시 전에 어떤 회사를 며칠 들여다봤는지가 드러나는 문제다). key_hash 는 익명이 아니라
#: **가명**이라 같은 사람인지는 알 수 있고, DART 키는 실명 등록에 묶여 있다.
#:
#: 그래도 되돌리는 이유는 **`ops_corp_daily` 로는 답할 수 없는 질문이 남아서**다. 집계는
#: 「어느 기업이 많이 조회되나」까지만 답한다. 사용자별 세션·재방문·워크플로(한 사람이
#: 한 기업을 며칠에 걸쳐 어떤 순서로 파는가)는 연결이 있어야 보이고, 그건 제품 판단에 쓴다.
#:
#: **그래서 이건 「부채가 없어졌다」가 아니라 「부채를 알고 진다」이다.** 260810 의 판단이
#: 틀렸던 게 아니라, 그때는 안 쓰던 값어치를 이제 쓰기로 한 것이다. 지는 쪽인 이상 조건이 붙는다:
#:   · `ops_corp_daily` 는 **그대로 둔다**(이중 기록). 연결이 필요 없는 질문은 계속 그쪽으로 답한다 —
#:     드레인으로 이벤트가 빠져나가도 기업 순위는 남아야 하고, 실제로 그게 유일한 장기 계열이다.
#:   · 남기는 것은 **정규화된 8자리 코드뿐**이다. 사용자가 친 질의 원문은 여전히 안 남긴다.
#:   · 드레인(`scripts/events_drain.py`)이 이 부채의 **수명 상한**이다 — 완결 주가 DB 를 떠나면
#:     연결도 함께 떠난다. 드레인이 멈추면 부채만 무한히 쌓이므로 운영 루틴으로 묶는다.
#: 운영 절차·근거: private `wiki-private/architecture/usage-telemetry-operations.md`.
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


#: 큐 튜플에서 corp_codes 의 자리. 이벤트 INSERT 는 이 자리를 **그대로 싣고**(260817),
#: `_corp_counts` 는 같은 자리를 읽어 사용자를 뗀 집계도 함께 올린다 — 둘 다 간다.
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
        CREATE TABLE IF NOT EXISTS ops_corp_daily(
            log_dd TEXT NOT NULL, corp_code TEXT NOT NULL, requests INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (log_dd, corp_code));
        """
    )
    # sqlite 는 폐기 컬럼을 되살리지 않는다 — 목록에서 뺀다(260817, doc_cache_hit).
    # 이미 그 컬럼이 있는 로컬 파일은 그냥 둔다: sqlite DROP COLUMN 은 버전을 타고,
    # 로컬은 운영 통계가 아니라 폴백이라 지울 값어치가 없다.
    for col in ("tool TEXT", "latency_ms INTEGER", "is_error INTEGER", "error_kind TEXT",
                "response_bytes INTEGER",
                "doc_mem_hits INTEGER", "doc_disk_hits INTEGER", "doc_misses INTEGER",
                "corp_codes TEXT",
                "fetch_viewer INTEGER", "fetch_kind INTEGER",
                "web_wait_ms INTEGER", "weak_kinds TEXT",
                "degraded TEXT",
                "inflight INTEGER", "cpu_ms INTEGER",
                "lag_ms INTEGER",
                "scope TEXT", "as_of TEXT"):  # 기존 테이블 마이그레이션 (로컬 파일 — 잠글 상대가 없다)
        try:
            con.execute(f"ALTER TABLE events ADD COLUMN {col}")
        except sqlite3.OperationalError:
            pass  # 이미 있음
    return con


#: **이벤트 1건의 컬럼 순서 — 여기가 단일 출처다.**
#:
#: 종전엔 이 목록이 네 곳(sqlite DDL·sqlite INSERT·PG INSERT·`record()` 튜플)에 손으로
#: 나열돼 있었다. 이름 기반 ALTER 는 빠뜨려도 **에러가 나서** 바로 보이지만, INSERT 목록과
#: 튜플 순서가 어긋나면 **조용히 다른 컬럼에 값이 들어간다**(260704 mkt_fund_hist 사고).
#: 컬럼을 더할 때 한쪽만 고쳐지는 자리를 없앤다 — 260824 에 이 레포에서 네 번 겪은 형태다.
#:
#: Postgres 의 형(型)·표 정의는 `scripts/ops_ddl.py` 에 있다(261018 — 종전엔 `_pg_connect` 가 접속마다 맞췄다).
#: 칸을 더할 때는 여기·`_sqlite_connect`·`ops_ddl.py` 셋을 함께 고치고 DDL 을 **배포 전에** 적용한다.
#: 각 칸을 왜 더했나(종전 `_pg_connect` 주석을 옮김):
#:   260802 response_bytes — 어느 tool 이 토큰을 많이 먹나(한글 UTF-8 은 글자당 3바이트라 토큰 수에 비례).
#:   260817 doc_cache_hit 은 **지웠다.** 260804 에 「폐기하되 컬럼은 남긴다」로 뒀는데, 남긴 컬럼은 스키마·백업 CSV·
#:          드레인에 계속 실려 다니면서 **읽는 사람에게는 지표처럼 보인다.** 백업 358,205행 전수 확인 결과 값이 들어온
#:          적이 한 번도 없다(0건). 대신 doc_mem_hits/doc_disk_hits/doc_misses 셋이 답한다. 안 쓰는 컬럼을 남기는 비용은
#:          용량이 아니라 **오해**다(원래 실패 원인: 하류가 ContextVar 를 set 하고 상위 미들웨어가 get 하는 구조).
#:   260804 doc_mem_hits·doc_disk_hits·doc_misses — 문서 출처를 **건수로** 나눠 센다. 메모리 예산의 효과를 보려면
#:          디스크 적중과 섞으면 안 된다(디스크는 예산과 무관하다).
#:   260804 corp_codes — 이 요청이 해석해 낸 기업(8자리, 쉼표 구분). 사용자가 친 원문은 남기지 않는다.
#:   260810 fetch_viewer·fetch_kind·web_wait_ms — 원문을 **어느 경로로** 받았나(폴백만: viewer HTML 2초·KIND 1~3초)와
#:          그 간격 때문에 **실제로 잠든** 시간. 「2초가 비싼가」는 빈도만으론 못 정한다.
#:   260810 weak_kinds — 이름이 정확히 안 맞아 **추정으로 고른** 해석의 방식(normalized·token·substring·fuzzy)만.
#:          **사용자가 친 원문(`query`)은 절대 싣지 않는다.**
#:   260824 degraded — **조용한 대체**의 종류만. screener 유니버스 폴백이 kospi200 호출마다 100% 발화하는데
#:          사용자 응답에만 실리고 우리가 보는 곳엔 안 쌓였다(오류율 1% 대로 조용했다).
#:   260824 inflight·cpu_ms — **느린 이유**를 가른다(줄에 서 있었나 / 스스로 무겁나 / 네트워크 대기).
#:   260827 lag_ms — **CPU 차례를 못 받은 것**을 네트워크 대기와 가른다(law_lookup 15초 — 기다릴 상대가 없는 tool).
#:   261018 scope·as_of — 호출 인자 둘(재설계 T8: quote/firm 비중, 일별 시세 7거래일 창이 충분한가). 정규화한 값만
#:          싣는다 — `_call_dims` 참조. 그 밖의 인자(회사명·질의 원문·문서번호)는 여전히 싣지 않는다.
_EVENT_COLUMNS = (
    "event_id", "ts_ns", "key_hash", "status",
    "tool", "latency_ms", "is_error", "error_kind", "response_bytes",
    "doc_mem_hits", "doc_disk_hits", "doc_misses", "corp_codes",
    "fetch_viewer", "fetch_kind", "web_wait_ms", "weak_kinds",
    "degraded",
    "inflight", "cpu_ms", "lag_ms",
    "scope", "as_of",
)


def _insert_sql(table: str, ph: str) -> str:
    cols = ", ".join(_EVENT_COLUMNS)
    return f"INSERT INTO {table}({cols}) VALUES({', '.join([ph] * len(_EVENT_COLUMNS))})"


def _sqlite_write(con, batch):
    # 컬럼명을 반드시 명시한다 — ADD COLUMN 으로 물리적 순서가 바뀌면 위치 의존 INSERT 는
    # **조용히 다른 컬럼에 값을 넣는다**(260704 mkt_fund_hist 사고).
    sql = _insert_sql("events", "?").replace("INSERT INTO", "INSERT OR IGNORE INTO")
    fresh = [r for r in batch if con.execute(sql, r).rowcount == 1]   # 이번에 실제로 들어간 행만 집계(`_pg_write` 참조)
    rows = [(str(d), c, n) for (d, c), n in _corp_counts(fresh).items()]
    if rows:
        con.executemany(
            "INSERT INTO ops_corp_daily(log_dd, corp_code, requests) VALUES(?,?,?) "
            "ON CONFLICT(log_dd, corp_code) DO UPDATE SET requests = requests + excluded.requests",
            rows)
    con.commit()


# ── 백엔드: Postgres ───────────────────────────────────────────────────────
def _pg_connect():
    """접속만 한다 — **DDL 을 보내지 않는다**(261018 T7). 표·칸은 `scripts/ops_ddl.py` 가 배포 전에 만든다.

    종전엔 접속할 때마다 CREATE/ALTER 22문장을 한 트랜잭션으로 보냈다. `ALTER TABLE ... IF NOT EXISTS` 는 칸이
    이미 있어도 표 전체 잠금(ACCESS EXCLUSIVE)을 **먼저** 잡고 COMMIT 까지 쥔다 — 그동안 다른 머신의 기록과
    그 표를 읽는 모든 질의가 멈추고, 오래 읽는 질의가 앞에 있으면 거꾸로 이쪽이 그 뒤에 서서 전부를 세운다.
    배포·재시작마다 두 머신이 한 번씩 했다. 더 큰 문제는 권한이다 — 표 소유자가 아닌 역할에서는 칸이 다 있어도
    `must be owner` 로 거절되고, 워커는 2초마다 재접속하며 한 행도 못 썼을 것이다(서버 DB 계정 분리가 승인돼 있다).
    각 칸을 왜 더했는지(260802~)는 `_EVENT_COLUMNS` 위 주석으로 옮겼다.
    """
    import psycopg
    return psycopg.connect(DATABASE_URL, connect_timeout=15, autocommit=False)


def _pg_write(con, batch):
    """묶음 하나를 한 트랜잭션으로 — 이벤트 행 + 기업 일별 집계. 실패하면 예외를 올린다(워커가 묶음을 들고 다시 쓴다).

    **다시 써도 두 번 세지 않는다.** 이벤트는 event_id 로 중복이 걸러지고(ON CONFLICT DO NOTHING), 집계는
    `RETURNING` 으로 돌아온 **이번에 실제로 들어간 행만** 더한다. COMMIT 은 됐는데 응답이 끊긴 묶음을 다시 써도
    이벤트는 0행·집계는 +0 이다. 이게 없으면 「못 쓴 묶음을 다시 쓴다」가 기업 순위를 부풀린다.
    """
    # 컬럼명 명시 — 위치 의존 INSERT 는 ADD COLUMN 후 조용히 어긋난다(260704 사고).
    cur = con.cursor()
    cur.executemany(
        _insert_sql("ops_tool_calls", "%s") + " ON CONFLICT (event_id) DO NOTHING RETURNING event_id",
        batch, returning=True)
    inserted = set()
    while True:
        inserted.update(r[0] for r in cur.fetchall())
        if not cur.nextset():
            break
    fresh = [r for r in batch if r[0] in inserted]
    # ops_corp_daily 는 corp_codes 를 되돌린 뒤에도 **계속 쓴다**(260817, 위 모듈 주석).
    # 이벤트는 드레인으로 주 단위로 DB 를 떠나지만 이 집계는 남는다 — 기업 조회 순위의
    # **유일한 장기 계열**이라, 이게 없으면 드레인이 지표를 지우는 일이 된다.
    rows = [(d, c, n) for (d, c), n in _corp_counts(fresh).items()]
    if rows:
        cur.executemany(
            "INSERT INTO ops_corp_daily(log_dd, corp_code, requests) VALUES(%s,%s,%s) "
            "ON CONFLICT (log_dd, corp_code) DO UPDATE SET "
            "requests = ops_corp_daily.requests + EXCLUDED.requests", rows)
    con.commit()


_connect = _pg_connect if _USE_PG else _sqlite_connect
_write = _pg_write if _USE_PG else _sqlite_write


# ── 워커 ───────────────────────────────────────────────────────────────────
def _gather(batch: list) -> list:
    """묶음 하나를 모은다 — 들고 있던 것(지난번에 못 쓴 묶음) 뒤에 큐에서 더 받는다.

    비어 있으면 첫 행이 올 때까지 잔다(한가할 땐 깨지 않는다). 첫 행부터 `_FLUSH_SEC` 동안 더 받고,
    상한(`_MAX_BATCH`)에서 멈춘다. 종료 신호를 받은 뒤로는 기다리지 않고 큐에 있는 것만 쓸어 담는다."""
    if not batch and not _stopping.is_set():
        item = _q.get()
        if item is not _WAKE:
            batch.append(item)
    deadline = time.monotonic() + _FLUSH_SEC
    while len(batch) < _MAX_BATCH:
        left = 0.0 if _draining.is_set() else deadline - time.monotonic()
        try:
            item = _q.get(timeout=left) if left > 0 else _q.get_nowait()
        except queue.Empty:
            break
        if item is not _WAKE:
            batch.append(item)
    return batch


def _recover(con):
    """실패한 트랜잭션을 되돌린다. 되돌리기도 실패하면(접속이 끊김) 닫고 None — 다음 차례에 다시 접속한다.
    스키마·권한 오류처럼 접속은 멀쩡한 실패에는 **접속을 그대로 쓴다** — 재시도마다 새로 접속하면 그게 또 접속 폭주다."""
    if con is None:
        return None
    try:
        con.rollback()
        return con
    except Exception:
        try:
            con.close()
        except Exception:
            pass
        return None


def _worker() -> None:
    """큐를 묶음으로 비워 기록. 요청 스레드와 분리돼 지연을 만들지 않음. 절대 죽지 않음.

    못 쓴 묶음은 **버리지 않고** 들고 있다가 다음 차례에 다시 쓴다(종전엔 버렸다 — 실패 1번 = 최대 200행 유실).
    기다림은 2·4·8…초에서 `_RETRY_MAX_SEC` 로 멈춘다. 끝내는 중에는 0.2초 간격으로 `_STOP_RETRIES` 번만 — 종료를 붙들지 않는다."""
    con = None
    held: list = []
    fails = 0
    stop_fails = 0
    try:
        while True:
            batch = held
            try:
                batch = _gather(held)
                held = []
                if batch:
                    if con is None:
                        con = _connect()
                    _write(con, batch)
                    fails = 0
            except Exception as e:
                fails += 1
                held = batch
                con = _recover(con)
                if _stopping.is_set():
                    stop_fails += 1
                    if stop_fails > _STOP_RETRIES:
                        sys.stderr.write(f"[usage] 종료 중 write 실패 — {len(held)}건 못 씀: {type(e).__name__}: {e}\n")
                        return
                    time.sleep(0.2)
                    continue
                sys.stderr.write(f"[usage] write 실패 — {len(held)}건 들고 재시도: {type(e).__name__}: {e}\n")
                _stopping.wait(min(_RETRY_MAX_SEC, 2.0 ** fails))
                continue
            if _stopping.is_set() and _q.empty():
                return
    finally:
        _stopped.set()


def _ensure_worker() -> None:
    global _worker_started
    if _worker_started:
        return
    with _lock:
        if _worker_started:
            return
        threading.Thread(target=_worker, name="usage-writer", daemon=True).start()
        _worker_started = True


def _wake_worker() -> None:
    try:
        _q.put_nowait(_WAKE)
    except queue.Full:
        pass      # 큐가 차 있으면 워커는 잠들어 있지 않다 — 신호만 보고 남은 것을 쓸어 간다


def begin_shutdown() -> None:
    """종료 신호를 받았다 — **더 모으지 않고 바로 쓴다.** 워커는 계속 돈다(진행 중인 요청의 기록도 곧장 나간다). 비차단.

    atexit 만으로는 모자라다: uvicorn 은 진행 중인 요청(몇 분짜리 도구 호출도 있다)이 끝나기를 기한 없이 기다리고, fly 는
    몇 초 뒤 강제 종료한다 — 그러면 atexit 은 돌지 못하고 모으던 묶음이 사라진다. 그래서 신호를 받는 순간부터 쓴다(server._serve)."""
    _draining.set()
    _wake_worker()


def flush(timeout: float = 5.0) -> None:
    """남은 것을 다 쓰고 워커를 끝낸다 — 프로세스 종료(atexit). 워커가 없으면(로컬·기록 안 함) 아무것도 안 한다."""
    if not _worker_started:
        return
    _draining.set()
    _stopping.set()
    _wake_worker()
    _stopped.wait(timeout)


atexit.register(flush)


def _call_dims(scope, as_of) -> tuple[str | None, str | None]:
    """호출 인자 `scope`·`as_of` → 저장해도 되는 값(정규화) 한 쌍. **저장 경계의 문지기**다(261018 T8).

    두 값은 사용자가 보낸 그대로 들어온다(`server._extract_call_dims` — 형도 모른다: 문자열·숫자·None·dict…).
    여기를 지난 값만 `ops_tool_calls.scope`(text)·`as_of`(date, 'YYYY-MM-DD' 문자열로 넘긴다)에 들어간다.
    원칙(`weak_kinds`·`corp_codes` 와 같다): 정규화된 값만, **자유 텍스트는 절대 아님**. 못 읽으면 None.
    """
    # scope — 꼴만 본다(닫힌 목록이 아니라): 도구들의 값은 summary·firm·quote 같은 짧은 영문 소문자 식별자다.
    #   목록을 닫으면 scope 가 늘 때마다 여기를 고쳐야 하고, 빠뜨리면 그 scope 만 조용히 안 쌓인다. 꼴 검사만으로도
    #   공백·한글·문장(자유 텍스트)은 다 걸러진다. 대소문자·앞뒤 공백은 바로잡는다("Quote " → quote).
    s = scope.strip().lower() if isinstance(scope, str) else None
    s = s if s and _SCOPE_RE.fullmatch(s) else None
    # as_of — 달력에 있는 날짜만(`publication_day`: 'YYYYMMDD'·'YYYY-MM-DD'). 정수 20261016 도 받는다(JSON 으로 숫자가 온다).
    #   연도("2024")·월("2024-09")은 날짜가 아니다 — None. 미래 날짜도 남긴다: 「창이 충분한가」를 잴 때 잘못 보낸 것도 보여야 한다.
    from open_proxy_mcp.dart.as_of import publication_day
    raw = str(as_of) if isinstance(as_of, int) and not isinstance(as_of, bool) else as_of
    day = publication_day(raw.strip() if isinstance(raw, str) else raw)
    return s, (f"{day[:4]}-{day[4:6]}-{day[6:]}" if day else None)


#: scope 로 받아 주는 꼴 — 영문 소문자로 시작, 소문자·숫자·밑줄 32자 이내.
_SCOPE_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")


def record(opendart_key: str, status: int, tool=None, latency_ms=None, is_error=None,
           error_kind=None, response_bytes=None,
           doc_mem_hits=None, doc_disk_hits=None, doc_misses=None, corp_codes=None,
           fetch_viewer=None, fetch_kind=None, web_wait_ms=None, weak_kinds=None,
           degraded=None, inflight=None, cpu_ms=None, lag_ms=None,
           scope=None, as_of=None) -> None:
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
    degraded=원래 답을 못 줘서 다른 것으로 **대체해 답한** 경우의 종류만(260824). 이 값이
    갑자기 늘면 우리가 무언가를 조용히 깨뜨린 것이다 — 오류율로는 안 보이는 고장이다.
    inflight=이 요청이 도는 동안 함께 돌던 요청의 최대 수(260824),
    cpu_ms=그동안 **프로세스 전체**가 쓴 CPU 시간(ms — 이 요청 자신의 것이 아니다).
    lag_ms=그동안 이벤트루프가 밀린 시간(ms — 260827). cpu 가 낮은 요청이 「네트워크를
    기다린 것」인지 「CPU 차례를 못 받은 것」인지는 이 값만 가른다. 셋을 함께 읽는다:
    cpu 높음+lag 낮음 → 이 호출이 무겁다 / cpu 높음+lag 높음+inflight>1 → 줄 /
    cpu 낮음+lag 높음 → CPU 를 못 받았다(VM 스로틀) / cpu 낮음+lag 낮음 → 네트워크 대기.
    corp_codes=이 요청이 **해석해 낸** 기업 코드 목록(사용자가 친 원문은 남기지 않는다).
    scope/as_of=호출 인자 둘, 보낸 그대로(261018 T8). `_call_dims` 를 지난 값만 남는다 — 못 읽으면 None.

    260804 이전에는 「회사는 기록하지 않는다」였다. 집계로 무엇이 많이 쓰이는지 보려고
    바꿨다 — 다만 남기는 것은 질의 원문이 아니라 정규화된 8자리 코드뿐이고,
    key_hash 와 함께 남으므로 **사용자별 조사 이력**이 된다는 점을 알고 켠 것이다.
    260810 에 그 연결을 끊었다가 260817 에 되돌렸다(모듈 상단 주석에 조건 셋)."""
    if not _RECORDING:
        return  # 로컬(pytest·pilot·스크립트)은 운영 통계를 오염시키지 않는다
    # 핸드셰이크는 **쓰지 않는다**(260918). 읽는 쪽(`usage_tracker.is_protocol`)이 이미 전부
    # 버리고 있었는데 쓰는 쪽은 그대로여서, 5일간 57,092행 중 83.6% 가 적히자마자 버려졌다.
    # 계측할 것도 없다 — 이 경로는 status·latency 만 채우고 나머지 15개 컬럼이 NULL 이다.
    if is_protocol(tool):
        return
    try:
        khash = hashlib.sha256(opendart_key.lower().encode()).hexdigest()
        if khash in SELF_HASHES:
            return  # 본인 키는 기록하지 않음
        _ensure_worker()
        ts_ns = time.time_ns()
        ev_id = f"{ts_ns}-{MACHINE}-{next(_counter)}"
        codes = ",".join(corp_codes) if corp_codes else None
        try:
            scope_v, as_of_v = _call_dims(scope, as_of)
        except Exception:
            scope_v = as_of_v = None   # 문지기가 넘어져도 그 행의 나머지는 남긴다
        # **SSOT 순서로 조립한다** — 손으로 나열하면 컬럼을 더할 때 짝이 어긋난다.
        vals = {"event_id": ev_id, "ts_ns": ts_ns, "key_hash": khash, "status": int(status),
                "tool": tool, "latency_ms": latency_ms, "is_error": is_error,
                "error_kind": error_kind, "response_bytes": response_bytes,
                "doc_mem_hits": doc_mem_hits, "doc_disk_hits": doc_disk_hits,
                "doc_misses": doc_misses, "corp_codes": codes,
                "fetch_viewer": fetch_viewer, "fetch_kind": fetch_kind,
                "web_wait_ms": web_wait_ms, "weak_kinds": weak_kinds, "degraded": degraded,
                "inflight": inflight, "cpu_ms": cpu_ms, "lag_ms": lag_ms,
                "scope": scope_v, "as_of": as_of_v}
        _q.put_nowait(tuple(vals[c] for c in _EVENT_COLUMNS))
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
