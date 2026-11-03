"""요청 키별 속도 조절(261103) — 늦추고 알리기, 넘치면 처리하지 않고 안내. network 0."""
from __future__ import annotations

import asyncio
import json
import pathlib

import pytest

from open_proxy_mcp.dart.client import _ctx_opendart_key
from open_proxy_mcp.services import pacing
from open_proxy_mcp.tools import _wrap_tool_errors


@pytest.fixture(autouse=True)
def fake_clock(monkeypatch):
    clock = [1000.0]

    async def sleep(seconds):
        clock[0] += seconds
        await asyncio.sleep(0)

    pacing._reset_for_tests()
    monkeypatch.setattr(pacing, "_now", lambda: clock[0])
    monkeypatch.setattr(pacing, "_sleep", sleep)
    yield clock
    pacing._reset_for_tests()


def _tool(body="# 결과\n\n본문", delay=0.0):
    async def sample(format="md"):
        if delay:
            await pacing._sleep(delay)
        return body
    return _wrap_tool_errors(sample)


async def _call(fn, key="user-key", **kwargs):
    token = _ctx_opendart_key.set(key)
    try:
        return await fn(**kwargs)
    finally:
        _ctx_opendart_key.reset(token)


def test_no_key_is_never_paced():
    fn = _tool()

    async def run():
        return [await _call(fn, key=None) for _ in range(pacing.BURST + 10)]

    assert all("📢" not in out for out in asyncio.run(run()))
    assert pacing.stats()["keys_tracked"] == 0


def test_burst_is_free_then_calls_are_spaced_and_told(fake_clock):
    fn = _tool()

    async def run():
        return [await _call(fn) for _ in range(pacing.BURST + 1)]

    outs = asyncio.run(run())
    assert all("📢" not in out for out in outs[:-1])
    last = outs[-1]
    assert "요청 속도 조절 중" in last and "2초 늦췄습니다" in last
    assert last.startswith("# 결과\n\n> 📢 공지")
    assert pacing.stats()["slowed"] == 1


def test_concurrency_per_key_waits_for_a_free_slot(fake_clock, monkeypatch):
    # 가짜 시계에서는 앞선 호출의 5초가 기다림보다 먼저 흐른다 — 기다렸는지만 보려고 안내 문턱을 낮춘다.
    monkeypatch.setattr(pacing, "NOTICE_AFTER_SEC", 0.1)
    slow = _tool(delay=5.0)

    async def run():
        return await asyncio.gather(*[_call(slow) for _ in range(pacing.CONCURRENCY + 1)])

    outs = asyncio.run(run())
    told = [out for out in outs if "요청 속도 조절 중" in out]
    assert len(told) == 1                                  # 세 번째만 자리를 기다렸다


def test_other_keys_are_not_slowed_by_a_busy_key(fake_clock):
    fn = _tool()

    async def run():
        for _ in range(pacing.BURST + 3):
            await _call(fn, key="busy")
        return await _call(fn, key="quiet")

    assert "📢" not in asyncio.run(run())


def test_no_call_is_refused_however_many_wait(fake_clock):
    ran = []

    async def sample(format="md"):
        ran.append(1)
        await pacing._sleep(10.0)
        return "# 결과"

    fn = _wrap_tool_errors(sample)
    n = 40

    async def run():
        return await asyncio.gather(*[_call(fn) for _ in range(n)])

    outs = asyncio.run(run())
    assert len(ran) == n and all(out.startswith("# 결과") for out in outs)
    told = [out for out in outs if "요청 속도 조절 중" in out]
    assert told and any("앞에 기다리던 요청" in out for out in told)
    assert pacing.stats()["max_queued"] >= n - pacing.CONCURRENCY - 1
    assert "refused" not in pacing.stats()


def test_json_responses_get_a_notices_field_and_stay_valid_json(fake_clock):
    fn = _tool(body=json.dumps({"status": "ok", "warnings": []}))

    async def run():
        return [await _call(fn) for _ in range(pacing.BURST + 1)]

    obj = json.loads(asyncio.run(run())[-1])
    assert obj["notices"][0]["id"] == "pacing"
    assert list(obj)[:3] == ["status", "warnings", "notices"]


def test_notice_text_has_no_tag_shape_and_fits_notice_limits():
    for item in (pacing.slowed_notice(12.4), pacing.slowed_notice(300.0, 999)):
        assert len(item.title) <= 60 and len(item.body) <= 400
        assert "[" not in item.title + item.body


def test_fly_checks_use_light_route_and_one_machine_stays_up():
    fly = (pathlib.Path(__file__).resolve().parent.parent / "fly.toml").read_text(encoding="utf-8")
    assert "path = '/livez'" in fly
    assert "min_machines_running = 1" in fly
