"""KRX 콜 미터 — 일별 사용량 집계 (키당 일 10,000 한도 대비, KST 자정 리셋).

왜: KRX Open API는 잔여 한도를 헤더·포털 어디서도 안 알려줌(실측 260702) → 우리 장부가 유일한 답.
Supabase `krx_call_log(day, machine, calls)`에 누적 → 집(Mac)/직장(Windows) 두 PC 사용량 합산 가능.

사용:
  from open_proxy_mcp.dart.krx_meter import bump; bump()      # 콜 직후 +1 (배치 flush)
  python -m open_proxy_mcp.dart.krx_meter                     # 오늘/최근 7일 사용량 조회

설계: 20콜 배치 flush + atexit flush. PG 실패 시 무시(호출 경로 절대 안 깨짐) — 집계는 best-effort.
"""
from __future__ import annotations

import atexit
import os
import platform
import threading
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
MACHINE = os.environ.get("FLY_MACHINE_ID") or platform.node() or "unknown"
_FLUSH_AT = 20

_pending = 0
_lock = threading.Lock()

_DDL = ("CREATE TABLE IF NOT EXISTS krx_call_log("
        "day date NOT NULL, machine text NOT NULL, calls int NOT NULL DEFAULT 0, "
        "PRIMARY KEY(day, machine))")


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


def _flush_locked() -> None:
    global _pending
    n = _pending
    if n <= 0:
        return
    _pending = 0
    try:
        import psycopg
        url = _dburl()
        if not url:
            return  # 집계 불가 환경 — 조용히 스킵
        with psycopg.connect(url, connect_timeout=10) as con:
            con.execute(_DDL)
            con.execute(
                "INSERT INTO krx_call_log(day, machine, calls) VALUES(%s,%s,%s) "
                "ON CONFLICT(day, machine) DO UPDATE SET calls = krx_call_log.calls + EXCLUDED.calls",
                (datetime.now(KST).date(), MACHINE, n),
            )
            con.commit()
    except Exception:
        _pending += n  # 다음 flush에서 재시도 (호출 경로는 절대 안 깨짐)


def bump(n: int = 1) -> None:
    """KRX 콜 n회 기록. 예외 없음·비차단(배치 flush)."""
    global _pending
    try:
        with _lock:
            _pending += n
            if _pending >= _FLUSH_AT:
                _flush_locked()
    except Exception:
        pass


def flush() -> None:
    try:
        with _lock:
            _flush_locked()
    except Exception:
        pass


atexit.register(flush)


def _report() -> None:
    import psycopg
    url = _dburl()
    if not url:
        print("DATABASE_URL 없음"); return
    with psycopg.connect(url, connect_timeout=10) as con:
        con.execute(_DDL); con.commit()
        today = datetime.now(KST).date()
        rows = con.execute(
            "SELECT day, machine, calls FROM krx_call_log WHERE day >= %s ORDER BY day DESC, machine",
            (today - timedelta(days=7),)).fetchall()
        tot_today = sum(c for d, m, c in rows if d == today)
        print(f"오늘({today} KST) KRX 콜: {tot_today:,} / 10,000 (잔여 ~{10000 - tot_today:,})")
        for d, m, c in rows:
            print(f"  {d} {m}: {c:,}")


if __name__ == "__main__":
    _report()
