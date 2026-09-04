# -*- coding: utf-8 -*-
"""list.json 검색 캐시 — 오늘을 포함하는 구간은 짧게만 산다. network 0콜.

260904: 캐시에 TTL 이 없어 회차 탐색(오늘+90일까지)의 첫 결과가 프로세스가 사는 동안 굳었다.
그 뒤 접수된 소집공고는 다음 날 키(end_de)가 바뀔 때까지 보이지 않았다 — 「방금 뜬 공시는
사용자가 말해 줘야 안다」의 원인. 과거로 닫힌 구간은 종전대로 세션 동안 유지한다.
"""
from __future__ import annotations

import asyncio

import open_proxy_mcp.dart.client as C
from open_proxy_mcp.clock import today_kst
from open_proxy_mcp.dart.client import DartClient


def _client(calls: list[dict]) -> DartClient:
    client = DartClient.__new__(DartClient)
    client._search_cache = {}
    client._MAX_SEARCH_CACHE = 10

    async def _fake_request(path, params):
        calls.append(dict(params))
        return {"status": "000", "list": [{"rcept_no": str(len(calls))}]}

    client._request = _fake_request
    return client


def test_window_touching_today_expires_after_the_live_ttl(monkeypatch) -> None:
    calls: list[dict] = []
    client = _client(calls)
    today = today_kst().strftime("%Y%m%d")
    now = {"t": 1_000_000.0}
    monkeypatch.setattr(C.time, "time", lambda: now["t"])

    first = asyncio.run(client.search_filings(bgn_de="20260101", end_de=today, corp_code="00106368"))
    again = asyncio.run(client.search_filings(bgn_de="20260101", end_de=today, corp_code="00106368"))
    assert len(calls) == 1 and again is first                      # TTL 안에서는 캐시

    now["t"] += C._SEARCH_CACHE_LIVE_TTL_SEC + 1
    fresh = asyncio.run(client.search_filings(bgn_de="20260101", end_de=today, corp_code="00106368"))
    assert len(calls) == 2 and fresh is not first                  # 지나면 다시 DART


def test_window_closed_in_the_past_keeps_the_session_cache(monkeypatch) -> None:
    calls: list[dict] = []
    client = _client(calls)
    now = {"t": 1_000_000.0}
    monkeypatch.setattr(C.time, "time", lambda: now["t"])

    asyncio.run(client.search_filings(bgn_de="20250101", end_de="20251231", corp_code="00106368"))
    now["t"] += 10 * C._SEARCH_CACHE_LIVE_TTL_SEC
    asyncio.run(client.search_filings(bgn_de="20250101", end_de="20251231", corp_code="00106368"))
    assert len(calls) == 1                                          # 과거 구간은 그대로


def test_future_end_date_counts_as_live() -> None:
    """회차 탐색은 end_de 를 오늘+90일로 준다 — 그 구간도 「아직 접수될 것이 남은」 구간이다."""
    calls: list[dict] = []
    client = _client(calls)
    asyncio.run(client.search_filings(bgn_de="20260101", end_de="20991231", corp_code="00106368"))
    (_val, exp), = client._search_cache.values()
    assert exp is not None
