# -*- coding: utf-8 -*-
"""KRX 시세 백그라운드 갱신기 — 요청 경로에서 KRX 갱신을 떼어낸다 (재설계 T2 · 원칙 P8 「쓰는 주체는 하나」).

261018 신설. 종전에는 **하루 첫 시세 질문**이 KRX 스냅샷 확인·기록을 떠안아 그 사용자가 2~12초를 기다렸고, KRX 가 게시하기 전에
하루 1회 래치(`_KRX_STATE`)가 굳으면 그날 종일 옛 날짜를 썼다. 이제 갱신은 서버가 뜰 때 함께 시작하는 asyncio 작업 하나가 한다.
요청 경로(`price_multiple_data._market_for`·`trading_data(quote)`)는 DB 상태(`max(price_dd)`·`krx_daily`)만 읽는다.

■ 언제
  평일(월~금) 07:30 ≤ 시각 < 20:00 KST 에 10분마다(10분 경계 + 0~20초 지터), 그리고 서버가 뜬 직후 한 번(창 밖이어도).
  KRX 는 전일 데이터를 익일 오전에 게시한다(T+1) — 07:30 에는 보통 아직 없다. 게시될 때까지 10분마다 확인하고, 저장되면 그날은 멈춘다.
■ 무엇을 기다리나(「기대 거래일」)
  오늘 이전의 마지막 평일 — `price_multiple_data._price_dates` 가 이 서버의 유일한 거래일 달력이다(주말만 안다).
  평일 휴장일은 KRX 가 빈 응답을 주는 것으로 알 수밖에 없다 → 휴장 다음 날에는 하루 종일 「아직 안 게시」로 보이고 확인만 반복한다
  (콜 상한은 `docs`/PR 의 표 참조: 머신 2대 × 약 75틱 × 1콜). 깨지는 것은 없다.
■ 어떻게(머신 2대 중 하나만)
  ① `max(trd_dd)` 가 기대 거래일 이상이면 아무것도 안 한다(DB 조회 1번, KRX 0콜, 잠금도 안 잡음).
  ② 아니면 전용 연결에서 `pg_try_advisory_lock(고정키)` — 못 잡으면(다른 머신이 일하는 중) 이번 틱은 건너뛴다.
  ③ 잡았으면 DB 를 다시 확인한다(잠금을 기다리는 사이 상대가 끝냈을 수 있다) → 아직이면 KRX 에서 그 날을 받는다(코스피 1콜, 있으면 코스닥 1콜).
  ④ 두 시장이 모두 충분히 오면 `_krx_db_upsert` 로 `krx_weekly` + `krx_daily` 를 한 트랜잭션에 쓰고 7거래일 밖을 지운다. 잠금 해제.
  전용 연결을 쓰는 이유: 세션 잠금은 연결 수명을 따른다. 연결을 닫으면 해제가 보장돼, 해제 호출이 실패해도 잠금이 새지 않는다.
  🔴 Supabase 풀러는 **세션 모드(5432)** 여야 한다. 트랜잭션 모드(6543)에서는 세션 잠금이 보장되지 않는다.
■ 끄기
  환경변수 `OPM_KRX_REFRESHER=0`(테스트·로컬). `DATABASE_URL` 이나 KRX 키가 없으면 저절로 꺼진다.
■ 안전
  한 번의 실패(DB·KRX)는 로그만 남기고 다음 틱에 다시 한다. 루프는 예외로 죽지 않는다.
  한 쪽 시장만 오거나 행이 너무 적으면(`MIN_ROWS_PER_MARKET`) 쓰지 않는다 — 반쪽 스냅샷으로 덮지 않는다.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import random
from dataclasses import dataclass
from datetime import datetime, time as dtime

from open_proxy_mcp import krx_daily
from open_proxy_mcp.clock import KST

logger = logging.getLogger(__name__)

ENV_FLAG = "OPM_KRX_REFRESHER"
INTERVAL_SEC = 600                      # 10분
WINDOW_START = dtime(7, 30)             # 평일 07:30 KST 부터
WINDOW_END = dtime(20, 0)               # 20:00 KST 전까지
#: 확인하는 요일(월=0). 설계는 평일만이다. ⚠ 전일 데이터를 익일 오전에 게시하는 규칙(T+1)대로라면 금요일 데이터는 토요일 아침에 나오므로,
#: 평일만 돌면 금요일 종가가 월요일 아침에야 반영된다(주말에는 목요일 종가가 최신으로 서빙 — 토요일 게시는 실측으로 확인하지 못했다).
#: 토요일(5)을 넣으면 그 공백이 사라진다 — 하루 약 8콜 추가. 바꾸려면 이 한 곳만.
WINDOW_WEEKDAYS = frozenset({0, 1, 2, 3, 4})
STARTUP_DELAY_SEC = 15.0                # 서버가 뜬 뒤 첫 확인까지(부팅 부하와 겹치지 않게)
#: 코스피 약 950·코스닥 약 1,800 행이 정상이다. 이보다 적으면 반쪽 응답으로 보고 쓰지 않는다.
MIN_ROWS_PER_MARKET = 300
#: 잠금이 이만큼 연속으로(10분 × 3 = 30분) 「다른 머신이 쥐고 있다」로만 보이면 비정상이다(보통 한 번 쓰는 데 몇 초). 잠금 보유자가 죽었는데
#: 풀러가 그 연결을 바로 정리하지 못한 경우를 상정한다 — 이 경우 잠금 없이 진행한다. 잠금은 KRX 호출을 아끼려는 장치일 뿐이고 쓰기는 멱등이다.
LOCK_BUSY_STREAK_LIMIT = 3

_FALSE = {"0", "false", "off", "no"}
_DB_DOWN = object()                     # 「DB 를 못 읽었다」 — 값이 비었다(None)와 구분


# ── 켜짐·달력 ────────────────────────────────────────────────────────────────

def disabled_reason() -> str | None:
    """갱신기를 못 켜는 이유. None 이면 켠다."""
    flag = os.environ.get(ENV_FLAG, "1").strip().lower()
    if flag in _FALSE:
        return f"{ENV_FLAG}={flag}"
    if not os.getenv("DATABASE_URL"):
        return "DATABASE_URL 없음"
    if not (os.getenv("KRX_API_KEY") or os.getenv("KRX_OPEN_API_KEY")):
        return "KRX 키 없음"
    return None


def now_kst() -> datetime:
    return datetime.now(KST)


def in_window(now: datetime) -> bool:
    """평일 07:30 ≤ 시각 < 20:00 (KST) 인가. `now` 는 KST 기준 시각이어야 한다."""
    return now.weekday() in WINDOW_WEEKDAYS and WINDOW_START <= now.time() < WINDOW_END


def expected_dd(now: datetime) -> str:
    """지금 DB 가 가져야 할 가장 최신 거래일 = 오늘 이전의 마지막 평일(KRX 는 T+1 게시).
    달력은 기존 `_price_dates`(주말만 안다) — 새 달력을 만들지 않는다."""
    from open_proxy_mcp.services.price_multiple_data import _price_dates
    today = now.strftime("%Y%m%d")
    return next(d for d in _price_dates(now.date()) if d < today)


def seconds_to_next_tick(now: datetime) -> float:
    """다음 10분 경계(:00·:10·:20…)까지 남은 초. 최소 1초."""
    into = (now.minute * 60 + now.second + now.microsecond / 1e6) % INTERVAL_SEC
    return max(1.0, INTERVAL_SEC - into)


# ── DB·KRX 입출력 (테스트에서 이 작은 함수들을 바꿔 끼운다) ─────────────────────────

def _db_latest_dd():
    """저장된 최신 거래일. `krx_daily` 가 기준이고, 그 표가 아직 없으면(마이그레이션 전 배포) `krx_weekly` 로 대신한다 —
    그래야 표가 없어도 지금처럼 `krx_weekly` 갱신은 이어진다. 비었으면 None, 읽을 수 없으면 `_DB_DOWN`."""
    from open_proxy_mcp.db import pg_rows
    rows = pg_rows("SELECT max(trd_dd) FROM krx_daily")
    if rows is None:
        rows = pg_rows("SELECT max(price_dd) FROM krx_weekly")
        if rows is None:
            return _DB_DOWN
    return (rows[0][0] if rows and rows[0][0] else None)


def _open_lock_conn():
    import psycopg
    return psycopg.connect(os.environ["DATABASE_URL"], autocommit=True, connect_timeout=10)


def _try_lock(conn) -> bool:
    return bool(conn.execute("SELECT pg_try_advisory_lock(%s)", (krx_daily.REFRESH_LOCK_KEY,)).fetchone()[0])


def _unlock(conn) -> None:
    conn.execute("SELECT pg_advisory_unlock(%s)", (krx_daily.REFRESH_LOCK_KEY,))


def _close(conn) -> None:
    with contextlib.suppress(Exception):
        conn.close()                    # 연결을 닫으면 세션 잠금도 풀린다 — 해제 호출이 실패해도 새지 않는다


@contextlib.asynccontextmanager
async def _advisory_lock():
    """전용 연결에서 고정키 세션 잠금을 시도한다. 값: True=잡았다 / False=다른 머신이 쥐고 있다 / None=연결 실패."""
    conn = None
    try:
        conn = await asyncio.to_thread(_open_lock_conn)
    except Exception as exc:            # noqa: BLE001
        logger.warning("KRX 갱신기: 잠금용 DB 연결 실패 %s: %s", type(exc).__name__, exc)
    if conn is None:
        yield None
        return
    held = False
    try:
        held = await asyncio.to_thread(_try_lock, conn)
        yield held
    finally:
        if held:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(_unlock, conn)
        await asyncio.to_thread(_close, conn)


async def _fetch_day(basDd: str) -> tuple[list, list, int]:
    """KRX 에서 한 거래일 전종목을 받는다 → (코스피 행, 코스닥 행, KRX 호출 수).
    코스피가 비면(아직 게시 전·휴장) 코스닥은 부르지 않는다 — 확인만 하는 틱은 1콜이다."""
    import httpx
    from open_proxy_mcp.dart.krx_meter import bump
    from open_proxy_mcp.services.price_multiple_data import _KRX_URL, _KSQ_URL

    key = os.getenv("KRX_API_KEY") or os.getenv("KRX_OPEN_API_KEY")
    calls = 0

    async def _one(h, url) -> list:
        nonlocal calls
        calls += 1
        await asyncio.to_thread(bump)   # 일별 사용량 장부(DB 를 만질 수 있어 루프 밖에서)
        try:
            r = await h.get(url, headers={"AUTH_KEY": key}, params={"basDd": basDd})
            if r.status_code != 200:
                logger.warning("KRX 응답 %s (basDd=%s) — 키·한도 확인", r.status_code, basDd)
                return []
            return next((v for v in r.json().values() if isinstance(v, list)), [])
        except Exception as exc:        # noqa: BLE001 — 빈 응답으로 취급하고 다음 틱에 다시
            logger.warning("KRX 호출 실패 basDd=%s: %s: %s", basDd, type(exc).__name__, exc)
            return []

    async with httpx.AsyncClient(timeout=30) as h:
        kospi = await _one(h, _KRX_URL)
        if not kospi:
            return [], [], calls
        kosdaq = await _one(h, _KSQ_URL)
    return kospi, kosdaq, calls


async def _write(dd: str, kospi: list, kosdaq: list) -> bool:
    from open_proxy_mcp.services import price_multiple_data as pmd
    ok = await asyncio.to_thread(pmd._krx_db_upsert, dd, kospi, kosdaq)
    if ok:
        pmd._serving_dd_invalidate()    # 이 머신은 바로 새 날짜를 본다(다른 머신은 TTL 로)
    return ok


# ── 한 번의 확인 ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class CycleResult:
    """outcome: fresh(이미 최신 — 0콜) · refreshed(새로 씀) · not_published(KRX 에 아직 없음) · lock_busy(다른 머신이 일하는 중)
    · incomplete(반쪽 응답이라 안 씀) · write_failed · db_error."""
    outcome: str
    expected: str | None = None
    stored: str | None = None
    krx_calls: int = 0
    rows: int = 0


_BUSY_STREAK = {"n": 0}               # 잠금이 연속으로 막힌 횟수(이 프로세스 기준)
_LOGGED_FRESH: dict[str, str] = {}       # 「이미 최신」은 기대 거래일마다 한 번만 INFO 로 남긴다(틱마다 찍으면 하루 140줄)


async def run_cycle(now: datetime | None = None) -> CycleResult:
    """DB 가 기대 거래일을 갖고 있으면 아무것도 안 하고(0콜), 아니면 잠금을 잡은 머신 하나만 KRX 에서 받아 쓴다."""
    now = now or now_kst()
    exp = expected_dd(now)
    stored = await asyncio.to_thread(_db_latest_dd)
    if stored is _DB_DOWN:
        logger.warning("KRX 갱신기: DB 를 읽지 못했다 — 이번 틱 건너뜀")
        return CycleResult("db_error", exp)
    if stored and stored >= exp:
        _BUSY_STREAK["n"] = 0
        if _LOGGED_FRESH.get("dd") != exp:
            _LOGGED_FRESH["dd"] = exp
            logger.info("KRX 갱신기: 기대 거래일 %s 는 이미 저장됨(최신 %s) — 오늘은 더 확인하지 않는다", exp, stored)
        return CycleResult("fresh", exp, stored)

    async with _advisory_lock() as held:
        if held is None:
            return CycleResult("db_error", exp, stored)
        if held:
            _BUSY_STREAK["n"] = 0
        else:
            _BUSY_STREAK["n"] += 1
            if _BUSY_STREAK["n"] < LOCK_BUSY_STREAK_LIMIT:
                logger.debug("KRX 갱신기: 다른 머신이 갱신 중 — 건너뜀 (%d회째)", _BUSY_STREAK["n"])
                return CycleResult("lock_busy", exp, stored)
            logger.warning("KRX 갱신기: 잠금이 %d틱 연속 잡혀 있다 — 보유자가 사라진 것으로 보고 잠금 없이 진행(쓰기는 멱등)",
                           _BUSY_STREAK["n"])
        return await _refresh_locked(exp)


async def _refresh_locked(exp: str) -> CycleResult:
    """잠금을 잡은(또는 잠금이 비정상으로 막혀 건너뛴) 상태에서 하는 일 — 다시 확인하고, 받고, 쓴다."""
    stored = await asyncio.to_thread(_db_latest_dd)           # 잠금을 잡은 뒤 다시 본다 — 상대가 방금 끝냈을 수 있다
    if stored is _DB_DOWN:
        return CycleResult("db_error", exp)
    if stored and stored >= exp:
        _BUSY_STREAK["n"] = 0
        return CycleResult("fresh", exp, stored)
    kospi, kosdaq, calls = await _fetch_day(exp)
    if not kospi:
        logger.info("KRX 갱신기: %s 아직 게시 전이거나 휴장 (KRX %d콜, 저장 최신 %s)", exp, calls, stored)
        return CycleResult("not_published", exp, stored, calls)
    if len(kospi) < MIN_ROWS_PER_MARKET or len(kosdaq) < MIN_ROWS_PER_MARKET:
        logger.warning("KRX 갱신기: %s 응답이 반쪽(KOSPI %d / KOSDAQ %d) — 기록하지 않는다", exp, len(kospi), len(kosdaq))
        return CycleResult("incomplete", exp, stored, calls)
    if not await _write(exp, kospi, kosdaq):
        return CycleResult("write_failed", exp, stored, calls)
    rows = len(kospi) + len(kosdaq)
    logger.info("KRX 갱신기: %s 기록 완료 (%d행, KRX %d콜)", exp, rows, calls)
    return CycleResult("refreshed", exp, exp, calls, rows)


async def _safe_cycle(now: datetime | None = None) -> CycleResult | None:
    try:
        return await run_cycle(now)
    except asyncio.CancelledError:
        raise
    except Exception:                   # noqa: BLE001 — 루프는 어떤 실패에도 죽지 않는다
        logger.exception("KRX 갱신기: 확인 중 예외 — 다음 틱에 다시")
        return None


# ── 루프와 앱 연결 ───────────────────────────────────────────────────────────

async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> bool:
    """seconds 만큼 자거나 stop 이 서면 즉시 깬다. 멈추라는 신호면 True."""
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
        return True
    except asyncio.TimeoutError:
        return False


async def _loop(stop: asyncio.Event, startup_delay: float | None = None) -> None:
    """서버가 뜬 직후 한 번(창 밖이어도), 이후 평일 창 안에서 10분마다."""
    delay = STARTUP_DELAY_SEC if startup_delay is None else startup_delay      # 호출 시점의 값(테스트가 바꿀 수 있게)
    if await _sleep_or_stop(stop, delay + random.uniform(0, min(10.0, delay))):
        return
    await _safe_cycle()
    while True:
        if await _sleep_or_stop(stop, seconds_to_next_tick(now_kst()) + random.uniform(0, 20)):
            return
        now = now_kst()
        if in_window(now):
            await _safe_cycle(now)


@dataclass
class Handle:
    task: asyncio.Task
    stop_event: asyncio.Event


#: INFO 줄이 운영 로그(fly logs)에 보이게 할 로거들. 루트 로거는 WARNING 이라 손대지 않으면 「기록 완료」·「이미 최신」이 버려진다.
_LOGGERS = ("open_proxy_mcp.services.krx_refresher", "open_proxy_mcp.krx_daily", "open_proxy_mcp.services.price_multiple_data")


def _install_log() -> None:
    """갱신기 관련 로거에 stderr 핸들러(INFO)를 단다. 여러 번 불려도 한 번만 단다. 전파는 끊어 두 번 찍히지 않게 한다."""
    import sys
    for name in _LOGGERS:
        lg = logging.getLogger(name)
        if any(getattr(h, "_opm_krx_refresher", False) for h in lg.handlers):
            continue
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s", "%H:%M:%S"))
        h._opm_krx_refresher = True                        # type: ignore[attr-defined]
        lg.addHandler(h)
        lg.setLevel(logging.INFO)
        lg.propagate = False


def start() -> Handle | None:
    """실행 중인 이벤트 루프에서 갱신 작업을 띄운다. 켤 수 없으면 이유를 로그에 남기고 None."""
    reason = disabled_reason()
    if reason:
        logger.info("KRX 갱신기 꺼짐: %s", reason)
        return None
    _install_log()
    stop_event = asyncio.Event()
    task = asyncio.get_running_loop().create_task(_loop(stop_event), name="krx-refresher")
    logger.info("KRX 갱신기 시작: 평일 %s~%s KST 10분 간격 + 기동 직후 1회",
                WINDOW_START.strftime("%H:%M"), WINDOW_END.strftime("%H:%M"))
    return Handle(task, stop_event)


#: 서버가 꺼질 때 갱신 작업이 정리를 끝내길 기다리는 상한(초). DB 가 멈춰 정리가 걸려도 배포·종료가 끌리지 않게 한다.
STOP_TIMEOUT_SEC = 5.0


async def stop(handle: Handle | None) -> None:
    if handle is None:
        return
    handle.stop_event.set()
    handle.task.cancel()
    with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError, Exception):
        await asyncio.wait_for(handle.task, timeout=STOP_TIMEOUT_SEC)


def attach_to_app(app) -> None:
    """Starlette 앱의 lifespan 을 감싸 서버가 뜰 때 갱신기를 띄우고 꺼질 때 멈춘다.
    갱신기를 못 띄워도 앱은 뜬다(서빙이 갱신기에 의존하지 않는다 — 옛 날짜를 읽을 뿐이다)."""
    inner = getattr(getattr(app, "router", None), "lifespan_context", None)
    if inner is None:                   # Starlette 이 아닌 앱(테스트용 대역 등) — 서빙을 깨지 않고 갱신기만 포기한다
        logger.warning("KRX 갱신기: 앱에 lifespan 이 없어 연결하지 못했다")
        return

    @contextlib.asynccontextmanager
    async def _lifespan(a):
        async with inner(a) as state:
            handle = None
            try:
                handle = start()
            except Exception:           # noqa: BLE001
                logger.exception("KRX 갱신기를 띄우지 못했다 — 서빙은 계속")
            try:
                yield state
            finally:
                await stop(handle)

    app.router.lifespan_context = _lifespan
