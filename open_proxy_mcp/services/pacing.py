# -*- coding: utf-8 -*-
"""요청 속도 조절 — 한 API 키가 서버를 독차지하지 않게 **늦추고 알린다**(261103).

261103 사고: 공유 CPU 두 대가 CPU 할당량을 다 써 `/health` 를 15초 안에 못 내자 fly 가 두 대 모두 빼서 약 1시간 전면 장애가 났다.
그동안 키 14개가 몇 초 간격으로 같은 요청을 다시 보냈다. 키별 제한이 없어서 몇몇 키의 반복 요청이 모두의 CPU 를 먹었다.

■ 무엇을 하나 — 도구 호출(`tools/__init__._wrap_tool_errors`)마다, 요청 키가 있을 때만
  · 동시: 한 키는 머신당 `OPM_PACE_CONCURRENCY`(2)건까지 동시에 돈다. 나머지는 앞선 호출이 끝날 때까지 기다린다.
  · 속도: 한 키는 머신당 분당 `OPM_PACE_PER_MIN`(30)회를 바로 받는다(처음 `OPM_PACE_BURST`(20)회는 몰아서). 넘으면 차례로 간격을 둔다.
  · 거절하지 않는다: 아무리 많이 기다려도 모든 호출을 차례로 처리한다(261103 사용자 결정 — 「처리하지 않는 건 이상하다」).
  · 알림: 1초 넘게 늦춘 응답에는 운영자 공지와 같은 자리(md `> 📢` 블록·JSON `notices`)에 안내를 싣는다. 이 호출이 들어올 때
    같은 키로 앞에 기다리던 호출 수도 적는다.
  기다리는 동안 CPU 는 쓰지 않는다(`asyncio.sleep`). fly.toml 에 hard_limit 를 두지 않은 이유와 같다 — 「느려진다」를 「거절당한다」로 바꾸지 않는다.
  키가 없는 호출(시험·내부 호출)은 조절하지 않는다. 원래 키 값은 남기지 않는다(`usage.record` 와 같은 sha256 해시만 메모리에).
"""
from __future__ import annotations

import asyncio
import contextlib
import datetime as _dt
import hashlib
import os
import time
from collections import OrderedDict
from dataclasses import dataclass, field

from open_proxy_mcp.services import notices as _notices


def _env_int(name: str, default: int, low: int) -> int:
    try:
        return max(low, int(os.environ.get(name, default)))
    except ValueError:
        return default


PER_MIN = _env_int("OPM_PACE_PER_MIN", 30, 1)
BURST = _env_int("OPM_PACE_BURST", 20, 1)
CONCURRENCY = _env_int("OPM_PACE_CONCURRENCY", 2, 1)
#: 이만큼 넘게 늦춘 응답에만 안내를 싣는다(초).
NOTICE_AFTER_SEC = 1.0
#: 동시 자리가 날 때까지 다시 보는 간격(초). 루프마다 잠금을 따로 만들지 않으려고 짧게 잔다.
_POLL_SEC = 0.2
_KEYS_MAX = 10_000
_KST = _dt.timezone(_dt.timedelta(hours=9))


@dataclass
class _KeyState:
    tokens: float
    stamp: float
    active: int = 0
    waiting: int = 0


@dataclass
class Ticket:
    waited: float = 0.0
    queued: int = 0
    notes: list = field(default_factory=list)


_keys: "OrderedDict[str, _KeyState]" = OrderedDict()
_stats = {"slowed": 0, "max_wait_s": 0.0, "max_queued": 0}


def _now() -> float:
    """시계(time.monotonic). 시험이 바꿔 끼운다."""
    return time.monotonic()


async def _sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


def _reset_for_tests() -> None:
    _keys.clear()
    _stats.update(slowed=0, max_wait_s=0.0, max_queued=0)


def _key_hash() -> str:
    from open_proxy_mcp.dart.client import _ctx_opendart_key

    key = _ctx_opendart_key.get()
    return hashlib.sha256(key.lower().encode()).hexdigest() if key else ""


def _state(khash: str, now: float) -> _KeyState:
    st = _keys.get(khash)
    if st is None:
        st = _keys[khash] = _KeyState(tokens=float(BURST), stamp=now)
        while len(_keys) > _KEYS_MAX:
            old, idle = next(iter(_keys.items()))
            if idle.active or idle.waiting:
                break                                   # 쓰이는 키는 밀어내지 않는다
            _keys.popitem(last=False)
    _keys.move_to_end(khash)
    st.tokens = min(float(BURST), st.tokens + (now - st.stamp) * PER_MIN / 60.0)
    st.stamp = now
    return st


@contextlib.asynccontextmanager
async def slot():
    """도구 호출 하나의 자리. 차례가 올 때까지 기다린다 — 거절하지 않는다."""
    khash = _key_hash()
    if not khash:
        yield Ticket()
        return
    t0 = _now()
    st = _state(khash, t0)
    queued = st.waiting
    _stats["max_queued"] = max(_stats["max_queued"], queued)
    st.waiting += 1
    # 토큰을 미리 하나 잡는다 — 모자라면 음수가 되고, 그만큼(차례)을 기다린다. 기다리다 취소되면 돌려놓는다.
    st.tokens -= 1.0
    try:
        if st.tokens < 0:
            await _sleep(-st.tokens * 60.0 / PER_MIN)
        while st.active >= CONCURRENCY:
            await _sleep(_POLL_SEC)
    except BaseException:
        st.tokens += 1.0
        raise
    finally:
        st.waiting -= 1
    st.active += 1
    waited = _now() - t0
    ticket = Ticket(waited=waited, queued=queued)
    if waited > NOTICE_AFTER_SEC:
        _stats["slowed"] += 1
        _stats["max_wait_s"] = max(_stats["max_wait_s"], round(waited, 1))
    try:
        yield ticket
    finally:
        st.active -= 1


def stats() -> dict:
    """`/health` 의 `pacing` — 기준·늦춘 수·가장 오래 기다린 초·가장 긴 대기열. 키는 싣지 않는다."""
    return {"per_min": PER_MIN, "burst": BURST, "concurrency": CONCURRENCY,
            "keys_tracked": len(_keys), "busy_keys": sum(1 for s in _keys.values() if s.active or s.waiting), **_stats}


# ── 안내 ──────────────────────────────────────────────────────────────────────
def _notice(title: str, body: str) -> "_notices.Notice":
    today = _dt.datetime.now(_KST).strftime("%m-%d")
    return _notices.Notice(id="pacing", level="important", title=title, body=body, link=None,
                           starts_at="", expires_at="", show="every_call", starts_ts=0.0, expires_ts=0.0,
                           date_label=today)


def slowed_notice(waited: float, queued: int = 0) -> "_notices.Notice":
    ahead = f"앞에 기다리던 요청 {queued}건 뒤에 처리해 " if queued else ""
    return _notice(
        "요청 속도 조절 중",
        f"이 API 키로 짧은 시간에 요청이 많아 {ahead}이번 응답을 {waited:.0f}초 늦췄습니다. "
        f"OPM은 여러 사용자가 함께 쓰는 서버라 키 하나당 분당 {PER_MIN}회·동시 {CONCURRENCY}건까지 바로 처리하고, "
        "그 이상은 차례로 천천히 처리합니다. 같은 요청을 반복하거나 여러 회사를 한꺼번에 조회할 때는 간격을 두고 요청해 주세요.")


def attach(ticket: Ticket, result):
    """1초 넘게 늦춘 응답에 안내를 싣는다. 그 밖에는 응답을 그대로 돌려준다."""
    if ticket.waited <= NOTICE_AFTER_SEC:
        return result
    return _notices.attach_items(result, [slowed_notice(ticket.waited, ticket.queued)])
