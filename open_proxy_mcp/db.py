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

── 260903 · 풀을 세워 놓고도 질의당 65ms 를 물고 있었다 ──────────────────────
같은 커넥션·같은 SQL 로 잰 값(`select ... from div_declared limit 5`, 중앙값):

    직결 커넥션 재사용                10.5ms   ← 왕복 바닥
    풀 (종전 설정)                    65.4ms
    풀 − 빌릴 때 ping                 39.0ms
    풀 − ping + autocommit           10.2ms   ← 바닥과 같다

두 겹이 얹혀 있었다. 둘 다 **왕복이 늘어난 것**이지 계산이 무거워진 게 아니다.
  ① 암묵 트랜잭션(약 26ms) — psycopg3 는 autocommit 이 아니면 첫 `execute` 앞에 `BEGIN`
     을 보내고 풀은 반납할 때 `COMMIT` 한다. SELECT 한 줄에 왕복이 셋이 된다.
     🔴 이 풀로는 **쓰기를 하지 않는다** — 쓰는 곳(`price_multiple_data` 스냅샷 적재·
     `usage`)은 각자 직접 접속해 트랜잭션을 잡는다. 그래서 autocommit 이 안전하다.
     풀에 쓰기를 태우게 되는 날 이 줄부터 다시 읽는다.
  ② 빌릴 때 ping(약 29ms) — `check_connection` 이 커넥션마다 `SELECT 1` 을 한 번 더 왕복
     했다. 죽은 커넥션을 넘기지 않으려던 것인데, 값이 **질의 자체보다 비쌌다**.
     대신 아래 `pg_rows` 에서 **연결 계열 예외일 때만 풀로 한 번 더** 간다. 끊긴 커넥션은
     반납 때 버려지므로 두 번째엔 성한 것이 온다 — 같은 보호를 평상시 0원에 받는다.
     🔴 연결 계열이 아닌 예외는 종전대로 **한 번 물고 곧장 풀을 내린다**. 고장난 풀을
     질의마다 다시 무는 것이 애초에 이 냉각 회로를 만든 이유다.
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
                # 🔴 읽기 전용 풀이다 — autocommit 이라 `BEGIN`/`COMMIT` 왕복이 없다.
                #   쓰기는 이 풀을 타지 않는다(모듈 상단 260903 참조).
                kwargs={"autocommit": True},
                # 빌릴 때 ping 하지 않는다(질의보다 비쌌다). 끊긴 커넥션은 `pg_rows` 의
                #   연결 계열 재시도가 받아 낸다 — 평상시 왕복 0.
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


def _conn_errors() -> tuple[type[BaseException], ...]:
    """「커넥션이 끊겼다」로 볼 예외들. 풀로 한 번 더 가 볼 값어치가 있는 것만 담는다.

    psycopg 를 못 불러오면 빈 튜플 — 아무것도 걸리지 않아 종전 경로(즉시 냉각)로 간다.
    🔴 여기에 `Exception` 을 넣지 않는다. 넣으면 고장난 풀을 두 번씩 물게 되고,
       그건 냉각 회로를 만든 이유를 되돌리는 것이다.
    """
    try:
        import psycopg
    except Exception:                         # noqa: BLE001 - psycopg 없으면 재시도 대상 없음
        return ()
    return (psycopg.OperationalError, psycopg.InterfaceError)


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
        except _conn_errors() as exc:
            # 끊긴 커넥션을 받았을 수 있다. 그 커넥션은 반납 때 버려지므로 **풀로 한 번 더**
            #   가면 성한 것이 온다 — 빌릴 때마다 ping 하던 29ms 를 이 자리로 옮긴 것이다.
            #   🔴 풀을 아직 내리지 않는다. 여기서 내리면 커넥션 한 개가 늙었을 뿐인데
            #      60초 동안 모든 질의가 직접 접속(핸드셰이크 124ms)으로 떨어진다.
            logger.debug("PG 커넥션 재시도: %s", exc)
            try:
                with pool.connection() as conn:
                    return conn.execute(sql, params).fetchall()
            except Exception as exc2:         # noqa: BLE001
                _disable_pool(f"재시도 실패 {type(exc2).__name__}: {exc2}")
        except Exception as exc:              # noqa: BLE001
            # 연결 계열이 아니다 — 풀 고갈·설정 오류 쪽이다. 한 번 물고 곧장 내린다.
            #   질의 자체가 틀린 경우에도 아래로 가지만, 그건 같은 예외로 끝난다.
            #   ★ 안 내리면 다음 질의도 같은 타임아웃을 다시 문다.
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
