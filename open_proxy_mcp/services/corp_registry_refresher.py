# -*- coding: utf-8 -*-
"""회사 명부(DART corpCode.xml — 전체 회사의 corp_code·이름·종목코드) 백그라운드 갱신기.

261018 신설. 종전엔 명부의 만료가 sqlite 층(7일)에만 있고 그것도 프로세스가 뜰 때 한 번 봤다 — 메모리에 올라간 명부는
프로세스가 사는 동안 그대로라 신규상장사를 최대 7일+ 「그런 상장사 없다」로 답했다(운영 두 머신 모두 명부 기준일 20261008,
10/15 상장 멜콘(179880)이 price_multiple_data·trading_data 에서 not_found).

■ 언제
  서버가 뜬 뒤 `STARTUP_DELAY_SEC`(+지터) 뒤 한 번, 이후 1시간마다(+지터). 한 번의 확인은 `DartClient.corp_registry_tick` 이 한다:
  ① 명부가 아직 안 올라왔으면 올린다(예열 — 첫 요청이 sqlite 를 읽느라 기다리지 않는다).
  ② DART 에서 받은 지 23시간이 넘었으면 새로 받는다(하루 한 번).
  ③ 일별 시세 저장분(`krx_daily`) 최신일에 있는 보통주 코드가 명부에 없으면(= 새로 상장했는데 명부가 모른다) 새로 받는다(시간당 상한).
  해석 실패(조회가 비었는데 명부가 1시간보다 묵었음) 때 받는 길은 요청 경로(`DartClient.lookup_corp_code_all`)에 있다 — 서버 키가 없어
  갱신기가 안 떠도 돈다(그때는 요청자의 키로 받는다).
■ 실패하면
  옛 명부를 그대로 쓴다(메모리·sqlite 둘 다 손대지 않는다). 받은 명부가 검증을 못 넘거나 상장사 수가 3% 넘게 줄었으면 버린다
  (지금 명부가 3일 넘게 묵었으면 줄어든 명부도 받는다 — 영영 굳지 않게).
  루프는 예외로 죽지 않는다. 상태는 `/health` 의 `registry.corp_master`(`fetched_at`·`fetched_age_hours`·`refresh`)에 보인다.
■ 머신 두 대
  머신마다 볼륨(sqlite)이 따로라 각자 받는다 — 서로 기다리거나 덮어쓸 것이 없다(DART 호출은 머신당 하루 1회 + 신호 때뿐).
  한 프로세스 안에서는 받기가 하나만 돈다(동시 요청은 같은 받기를 기다린다).
■ 켜고 끄기
  서버가 뜨면 기본 켜짐(쓰는 곳은 이 머신의 sqlite 하나뿐이고, DART 호출은 하루 한 번이다). `OPM_CORP_REFRESHER=0` 이면 꺼지고,
  해석 실패 재수신도 함께 꺼진다(자동으로 새로 받는 길 전부 — 시험·비상용). 프로세스가 뜰 때의 첫 적재는 그대로다.
  서버 DART 키(`OPENDART_API_KEY`)가 없으면 저절로 꺼진다 — 사용자 키로 배경 작업을 돌리지 않는다.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import random
from dataclasses import dataclass

logger = logging.getLogger(__name__)

ENV_FLAG = "OPM_CORP_REFRESHER"
INTERVAL_SEC = 3600                     # 1시간
STARTUP_DELAY_SEC = 20.0                # 서버가 뜬 뒤 첫 확인까지(부팅 부하·KRX 갱신기 첫 확인과 겹치지 않게)
#: 서버가 꺼질 때 갱신 작업이 정리를 끝내길 기다리는 상한(초).
STOP_TIMEOUT_SEC = 5.0

_FALSE = {"0", "false", "off", "no"}


def disabled_reason() -> str | None:
    """갱신기를 못 켜는 이유. None 이면 켠다."""
    flag = (os.environ.get(ENV_FLAG) or "").strip().lower()
    if flag in _FALSE:
        return f"{ENV_FLAG}={flag}"
    if not os.getenv("OPENDART_API_KEY"):
        return "서버 DART 키(OPENDART_API_KEY) 없음 — 해석 실패 때 다시 받기만 동작"
    return None


async def run_once() -> str | None:
    """한 번 확인한다. 결과 문자열(`corp_registry_tick` 참조) 또는 실패 시 None — 예외는 새지 않는다."""
    from open_proxy_mcp.dart.client import get_dart_client
    try:
        return await get_dart_client().corp_registry_tick()
    except asyncio.CancelledError:
        raise
    except Exception:                   # noqa: BLE001 — 루프는 어떤 실패에도 죽지 않는다
        logger.exception("회사 명부 갱신기: 확인 중 예외 — 다음 확인 때 다시")
        return None


async def _sleep_or_stop(stop: asyncio.Event, seconds: float) -> bool:
    """seconds 만큼 자거나 stop 이 서면 즉시 깬다. 멈추라는 신호면 True."""
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
        return True
    except asyncio.TimeoutError:
        return False


async def _loop(stop: asyncio.Event, startup_delay: float | None = None, interval: float | None = None) -> None:
    """서버가 뜬 직후 한 번, 이후 1시간마다."""
    delay = STARTUP_DELAY_SEC if startup_delay is None else startup_delay
    every = INTERVAL_SEC if interval is None else interval
    if await _sleep_or_stop(stop, delay + random.uniform(0, min(10.0, delay))):
        return
    while True:
        outcome = await run_once()
        if outcome and outcome != "fresh":
            logger.info("회사 명부 갱신기: %s", outcome)
        if await _sleep_or_stop(stop, every + random.uniform(0, min(60.0, every / 10))):
            return


@dataclass
class Handle:
    task: asyncio.Task
    stop_event: asyncio.Event


def _install_log() -> None:
    """갱신기·DART 클라이언트의 INFO 줄(「새로 받음 — 새 종목코드 …」)이 운영 로그에 보이게 stderr 핸들러를 단다. 한 번만 단다."""
    import sys
    for name in (__name__, "open_proxy_mcp.dart.corp_registry"):
        lg = logging.getLogger(name)
        if any(getattr(h, "_opm_corp_refresher", False) for h in lg.handlers):
            continue
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s", "%H:%M:%S"))
        h._opm_corp_refresher = True                       # type: ignore[attr-defined]
        lg.addHandler(h)
        lg.setLevel(logging.INFO)


def start() -> Handle | None:
    """실행 중인 이벤트 루프에서 갱신 작업을 띄운다. 켤 수 없으면 이유를 로그에 남기고 None."""
    _install_log()
    reason = disabled_reason()
    if reason:
        logger.info("회사 명부 갱신기 꺼짐: %s", reason)
        return None
    stop_event = asyncio.Event()
    task = asyncio.get_running_loop().create_task(_loop(stop_event), name="corp-registry-refresher")
    logger.info("회사 명부 갱신기 시작: 기동 직후 1회 + 1시간마다 확인(하루 한 번 새로 받기 · 신규상장 신호 때 받기)")
    return Handle(task, stop_event)


async def stop(handle: Handle | None) -> None:
    if handle is None:
        return
    handle.stop_event.set()
    handle.task.cancel()
    with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError, Exception):
        await asyncio.wait_for(handle.task, timeout=STOP_TIMEOUT_SEC)


def attach_to_app(app) -> None:
    """Starlette 앱의 lifespan 을 감싸 서버가 뜰 때 갱신기를 띄우고 꺼질 때 멈춘다.
    갱신기를 못 띄워도 앱은 뜬다(서빙은 갱신기에 의존하지 않는다 — 옛 명부를 쓸 뿐이다)."""
    inner = getattr(getattr(app, "router", None), "lifespan_context", None)
    if inner is None:
        logger.warning("회사 명부 갱신기: 앱에 lifespan 이 없어 연결하지 못했다")
        return

    @contextlib.asynccontextmanager
    async def _lifespan(a):
        async with inner(a) as state:
            handle = None
            try:
                handle = start()
            except Exception:           # noqa: BLE001
                logger.exception("회사 명부 갱신기를 띄우지 못했다 — 서빙은 계속")
            try:
                yield state
            finally:
                await stop(handle)

    app.router.lifespan_context = _lifespan
