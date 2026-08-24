"""Supabase Postgres 커넥션 풀 — 서빙 경로의 DB 질의 한 곳.

260824 신설. 종전엔 질의마다 `psycopg.connect` 로 새로 접속했다.

실측: **핸드셰이크 124ms · 질의 63ms** — 연결이 질의보다 2배 비싸다.
  `price_multiple_data(scope="firm_history")` 는 이 경로를 8번 지나므로
  1.68초 중 약 1초가 순수하게 「전화 거는 시간」이었다. 실사용 p50 2,788ms 로
  전체 4번째로 느린 tool 이었다(전체 평균 p50 616ms).

★ 서버 쪽에 이미 Supabase 풀러(pgbouncer)가 있지만 그건 **반대쪽 끝**이다.
  풀러와 DB 사이 연결을 아껴줄 뿐, 우리가 풀러까지 TCP→TLS→인증을 거치는 비용은 그대로다.
  우리가 재고 있는 124ms 가 정확히 그 구간이라 클라이언트 쪽 풀이 따로 필요하다.

★ **fail-open.** 풀을 못 만들면 예전처럼 직접 접속한다. 풀은 빠르게 하려고 두는 것이지
  없으면 못 도는 것이 아니다 — 풀 문제로 조회 전체가 죽으면 고치려던 것보다 나쁘다.

크기 5인 이유: 프로덕션이 1 CPU 라 `asyncio.to_thread` 기본 풀이 min(32, cpu+4)=**5칸**이다.
  DB 커넥션을 그보다 많이 열어도 그 칸을 넘어 동시에 쓰지 못한다. 머신 2대 × 5 = 10 이고
  DB `max_connections` 60(실측 사용 16) 안에 넉넉히 든다.
"""
from __future__ import annotations

import logging
import os
import threading

logger = logging.getLogger(__name__)

#: 머신당 커넥션. `asyncio.to_thread` 칸 수(1 CPU → 5)와 맞춘다 — 그보다 크면 놀고,
#: 작으면 대기가 생긴다. 운영 중 조정용으로 환경변수를 연다.
_POOL_MAX = int(os.environ.get("OPM_PG_POOL_MAX", "5") or 5)
_POOL_MIN = int(os.environ.get("OPM_PG_POOL_MIN", "1") or 1)
#: 풀이 꽉 찼을 때 기다리는 한계. 넘으면 직접 접속으로 빠진다(막히느니 느린 게 낫다).
_POOL_TIMEOUT = float(os.environ.get("OPM_PG_POOL_TIMEOUT", "5") or 5)

_pool = None
_pool_lock = threading.Lock()
_pool_failed = False        # 한 번 실패하면 매 질의마다 재시도하지 않는다


def _get_pool():
    """지연 생성 싱글턴. DATABASE_URL 이 없거나 생성이 실패하면 None(→ 직접 접속)."""
    global _pool, _pool_failed
    if _pool is not None or _pool_failed:
        return _pool
    with _pool_lock:
        if _pool is not None or _pool_failed:
            return _pool
        url = os.getenv("DATABASE_URL")
        if not url:
            _pool_failed = True
            return None
        try:
            from psycopg_pool import ConnectionPool
            _pool = ConnectionPool(
                url, min_size=_POOL_MIN, max_size=_POOL_MAX,
                timeout=_POOL_TIMEOUT,
                # 빌려줄 때 살아 있는지 본다. 끊긴 커넥션을 그냥 주면 질의가 죽는데,
                #   그건 풀이 없을 때는 아예 없던 실패 방식이다.
                check=ConnectionPool.check_connection,
                max_lifetime=600,        # 10분마다 재생성 — 상류가 조용히 끊는 것 대비
                max_idle=120,
                open=True, name="opm",
            )
            logger.info("PG 커넥션 풀 생성 (min=%d max=%d)", _POOL_MIN, _POOL_MAX)
        except Exception as exc:              # noqa: BLE001 — 풀 없이도 돌아야 한다
            _pool_failed = True
            logger.warning("PG 커넥션 풀 생성 실패 — 직접 접속으로 진행: %s", exc)
            return None
    return _pool


def pg_rows(sql: str, params: tuple = ()) -> list[tuple] | None:
    """조회 결과 행. **None = DB 미설정/장애** (no_data 와 구분 — 오진 방지), [] = 데이터 없음.

    이 구분을 지우면 「배치가 안 돌았다」와 「DB 가 죽었다」가 한 화면에 같은 말로 나온다.
    """
    url = os.getenv("DATABASE_URL")
    if not url:
        return None
    pool = _get_pool()
    if pool is not None:
        try:
            with pool.connection() as conn:
                return conn.execute(sql, params).fetchall()
        except Exception as exc:              # noqa: BLE001
            # 풀 고갈·커넥션 문제일 수 있다 → 직접 접속으로 한 번 더 시도한다.
            #   질의 자체가 틀린 경우에도 한 번 더 가지만, 그건 아래에서 같은 예외로 끝난다.
            logger.warning("풀 경유 조회 실패 — 직접 접속 재시도: %s", exc)
    try:
        import psycopg
        with psycopg.connect(url, connect_timeout=8) as c:
            return c.execute(sql, params).fetchall()
    except Exception as exc:                  # noqa: BLE001
        logger.warning("DB 조회 실패: %s", exc)
        return None


def pool_stats() -> dict:
    """/health 용. 풀이 없으면 그 사실을 낸다 — 「있는데 비어 있음」과 구분되어야 한다."""
    pool = _pool
    if pool is None:
        return {"enabled": False, "reason": "미생성 또는 생성 실패"}
    try:
        s = pool.get_stats()
    except Exception:                         # noqa: BLE001
        return {"enabled": True, "stats": "unavailable"}
    return {"enabled": True, "min": _POOL_MIN, "max": _POOL_MAX,
            "size": s.get("pool_size"), "available": s.get("pool_available"),
            "waiting": s.get("requests_waiting"),
            "requests": s.get("requests_num"), "errors": s.get("connections_errors")}
