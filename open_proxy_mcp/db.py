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
import time

logger = logging.getLogger(__name__)

#: 머신당 커넥션. `asyncio.to_thread` 칸 수(1 CPU → 5)와 맞춘다 — 그보다 크면 놀고,
#: 작으면 대기가 생긴다. 운영 중 조정용으로 환경변수를 연다.
_POOL_MAX = int(os.environ.get("OPM_PG_POOL_MAX", "5") or 5)
_POOL_MIN = int(os.environ.get("OPM_PG_POOL_MIN", "1") or 1)
#: 풀이 꽉 찼을 때 기다리는 한계. 넘으면 직접 접속으로 빠진다(막히느니 느린 게 낫다).
#: ★ 짧아야 한다. 폴백(직접 접속)이 실측 124ms 인데 여기서 5초를 기다리면 **풀이 고장난 순간
#:   모든 질의가 종전보다 40배 느려진다** — 빠르게 하려고 둔 것이 정반대로 작동한다.
#:   260824 실측: 5초일 때 테스트 스위트가 15초 → 76초가 됐다(질의마다 5초를 물었다).
_POOL_TIMEOUT = float(os.environ.get("OPM_PG_POOL_TIMEOUT", "2") or 2)
#: 풀이 실패한 뒤 다시 세워보기까지의 냉각 시간. 영구히 끄면 일시 장애가 재기동 때까지 남고,
#: 냉각 없이 매번 재시도하면 장애 때마다 생성 비용을 다시 문다.
_POOL_RETRY_SEC = float(os.environ.get("OPM_PG_POOL_RETRY_SEC", "60") or 60)

_pool = None
_pool_lock = threading.Lock()
_pool_disabled_until = 0.0  # 이 시각까지는 풀을 쓰지 않는다 (0 = 정상)


def _disable_pool(reason: str) -> None:
    """풀을 내리고 냉각에 들어간다. **한 번 실패하면 그 뒤 질의는 곧장 직접 접속으로 간다** —
    안 그러면 고장난 풀을 질의마다 다시 물어 매번 타임아웃만큼 느려진다."""
    global _pool, _pool_disabled_until
    with _pool_lock:
        if _pool is not None:
            try:
                _pool.close()
            except Exception:             # noqa: BLE001
                pass
        _pool = None
        _pool_disabled_until = time.monotonic() + _POOL_RETRY_SEC
    logger.warning("PG 커넥션 풀 비활성 %.0f초 — 직접 접속으로 진행: %s", _POOL_RETRY_SEC, reason)


def _get_pool():
    """지연 생성 싱글턴. DATABASE_URL 이 없거나 생성이 실패하면 None(→ 직접 접속)."""
    global _pool, _pool_disabled_until
    if _pool is not None:
        return _pool
    if time.monotonic() < _pool_disabled_until:
        return None
    with _pool_lock:
        if _pool is not None or time.monotonic() < _pool_disabled_until:
            return _pool
        url = os.getenv("DATABASE_URL")
        if not url:
            _pool_disabled_until = time.monotonic() + _POOL_RETRY_SEC
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
            _pool_disabled_until = time.monotonic() + _POOL_RETRY_SEC
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
            #   ★ 그리고 풀을 내린다 — 안 내리면 다음 질의도 같은 타임아웃을 다시 문다.
            _disable_pool(f"{type(exc).__name__}: {exc}")
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
        left = max(0.0, _pool_disabled_until - time.monotonic())
        return {"enabled": False,
                "reason": "미생성 또는 실패", "retry_in_sec": round(left)}
    try:
        s = pool.get_stats()
    except Exception:                         # noqa: BLE001
        return {"enabled": True, "stats": "unavailable"}
    return {"enabled": True, "min": _POOL_MIN, "max": _POOL_MAX,
            "size": s.get("pool_size"), "available": s.get("pool_available"),
            "waiting": s.get("requests_waiting"),
            "requests": s.get("requests_num"), "errors": s.get("connections_errors")}
