"""KRX 콜 미터 — 일별 사용량 집계 (키당 일 10,000 한도 대비, KST 자정 리셋).

왜: KRX Open API는 잔여 한도를 헤더·포털 어디서도 안 알려줌(실측 260702) → 우리 장부가 유일한 답.
Supabase `ops_krx_calls(log_dd, machine, calls)`에 누적 → 집(Mac)/직장(Windows) 두 PC 사용량 합산 가능.

사용:
  from open_proxy_mcp.dart.krx_meter import bump; bump()      # 콜 직후 +1 (메모리 계수만 — 즉시 돌아온다)
  python -m open_proxy_mcp.dart.krx_meter                     # 오늘/최근 7일 사용량 조회

설계: 20콜이 차거나 `_FLUSH_SEC` 가 지나면 **배경 스레드**가 공용 풀(`db.pg_execute`)로 한 문장 upsert. atexit flush.
PG 실패 시 계수를 되돌려 다음 차례에 다시(호출 경로 절대 안 깨짐) — 집계는 best-effort.

261018(재설계 4단계 T7): 종전 `_flush_locked` 는 **20번째 `bump()` 를 부른 그 자리에서** 새 접속(핸드셰이크 124ms) +
`CREATE TABLE IF NOT EXISTS` + INSERT 를 했다. `bump()` 를 부르는 곳 넷 중 셋(`dart/client.py`·`services/trading.py`·
`services/price_multiple_data.py`)이 async 함수 안이라 그동안 **이벤트 루프 전체가 멈췄다**(락까지 쥔 채).
CREATE TABLE 은 스키마 생성 권한이 없는 역할에선 표가 있어도 거절되고, 실패하면 계수가 20 이상으로 남아
그 뒤로는 `bump()` 마다 새 접속을 시도했다. 이제 표는 `scripts/ops_ddl.py` 가 만들고, 여기서는 표가 **없을 때만** 만든다.
"""
from __future__ import annotations

import atexit
import logging
import os
import platform
import threading
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
MACHINE = os.environ.get("FLY_MACHINE_ID") or platform.node() or "unknown"
_FLUSH_AT = 20
#: 20콜이 안 차도 이만큼 지나면 쓴다(초). 한가한 날 장부가 프로세스 종료 때까지 밀리지 않게.
_FLUSH_SEC = 30.0

_pending = 0
_lock = threading.Lock()          # 계수만 지킨다 — 이 락을 쥔 채 네트워크를 만지지 않는다
_wake = threading.Event()
_flusher_started = False

log = logging.getLogger(__name__)

#: 표 정의. 정본은 `scripts/ops_ddl.py` — 여기서는 **표가 없을 때만** 보낸다(새 DB·로컬). 읽기(`_report`)는 보내지 않는다.
_DDL = ("CREATE TABLE IF NOT EXISTS ops_krx_calls("
        "log_dd date NOT NULL, machine text NOT NULL, calls int NOT NULL DEFAULT 0, "
        "PRIMARY KEY(log_dd, machine))")
_UPSERT = ("INSERT INTO ops_krx_calls(log_dd, machine, calls) VALUES(%s,%s,%s) "
           "ON CONFLICT(log_dd, machine) DO UPDATE SET calls = ops_krx_calls.calls + EXCLUDED.calls")


def _dburl():
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    try:  # 스크립트가 dotenv를 안 불렀어도 동작하게
        from dotenv import load_dotenv
        from pathlib import Path
        load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    except Exception:
        pass
    return os.environ.get("DATABASE_URL")


def _write(n: int, *, create_pool: bool = True) -> None:
    """n 콜을 오늘 장부에 더한다 — 한 문장. 표가 없으면(새 DB) 그때만 만들고 다시 쓴다."""
    import psycopg
    from open_proxy_mcp.db import pg_execute
    row = (datetime.now(KST).date(), MACHINE, n)
    try:
        pg_execute(_UPSERT, row, create_pool=create_pool)
    except psycopg.errors.UndefinedTable:
        pg_execute(_DDL, create_pool=create_pool)
        pg_execute(_UPSERT, row, create_pool=create_pool)


def _flush_once(*, create_pool: bool = True) -> bool:
    """쌓인 계수를 한 번 쓴다. 못 쓰면 되돌려 다음 차례에 다시 — **계수를 잃지 않는다.**"""
    global _pending
    with _lock:
        n, _pending = _pending, 0
    if n <= 0:
        return True
    try:
        if not _dburl():
            return True   # 집계 불가 환경 — 조용히 스킵(종전과 같다)
        _write(n, create_pool=create_pool)
        return True
    except Exception as exc:              # noqa: BLE001 — 장부는 best-effort, 호출 경로를 깨지 않는다
        with _lock:
            _pending += n
        log.debug("KRX 장부 쓰기 실패 — %d콜 보관: %s: %s", n, type(exc).__name__, exc)
        return False


def _flusher() -> None:
    """배경 쓰기 스레드 — 20콜이 차면 깨우고, 아니면 `_FLUSH_SEC` 마다 한 번. 절대 죽지 않는다."""
    while True:
        try:
            _wake.wait(_FLUSH_SEC)
            _wake.clear()
            _flush_once()
        except Exception:                 # noqa: BLE001
            pass


def _ensure_flusher() -> None:
    global _flusher_started
    if _flusher_started:
        return
    with _lock:
        if _flusher_started:
            return
        threading.Thread(target=_flusher, name="krx-meter", daemon=True).start()
        _flusher_started = True


def bump(n: int = 1) -> None:
    """KRX 콜 n회 기록. 예외 없음·**네트워크 없음** — 메모리 계수만 올리고 쓰기는 배경 스레드가 한다."""
    global _pending
    try:
        with _lock:
            _pending += n
            due = _pending >= _FLUSH_AT
        _ensure_flusher()
        if due:
            _wake.set()
    except Exception:
        pass


def flush() -> None:
    """지금 쌓인 것을 **부른 스레드에서** 쓴다 — 프로세스 종료(atexit)·스크립트 끝. 풀을 새로 세우지 않는다."""
    try:
        _flush_once(create_pool=False)
    except Exception:
        pass


atexit.register(flush)


def _report() -> None:
    import psycopg
    url = _dburl()
    if not url:
        print("DATABASE_URL 없음"); return
    with psycopg.connect(url, connect_timeout=10) as con:
        today = datetime.now(KST).date()
        rows = con.execute(
            "SELECT log_dd, machine, calls FROM ops_krx_calls WHERE log_dd >= %s ORDER BY log_dd DESC, machine",
            (today - timedelta(days=7),)).fetchall()
        tot_today = sum(c for d, m, c in rows if d == today)
        print(f"오늘({today} KST) KRX 콜: {tot_today:,} / 10,000 (잔여 ~{10000 - tot_today:,})")
        for d, m, c in rows:
            print(f"  {d} {m}: {c:,}")


if __name__ == "__main__":
    _report()
