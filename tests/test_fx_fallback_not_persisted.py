"""환율 야후 대체값은 영구 표(ecos_fx_rate)에 넣지 않는다 — 짧게만 기억하고 출처를 적는다 (261019). network 0 · DB 0.

10-19 운영 환율 표 296행 중 6행(JPY 20241231·20250228·20260228·20260331, GBP 20260131·20260331)이 ECOS 매매기준율이 아니라
야후 종가였다(ECOS 와 −0.08~+0.62%). ECOS 키가 없는 GitHub Actions 배치가 캐시 미스를 낸 날짜에 `fx_to_krw` 가
ECOS → 야후로 넘어가 그 값을 `_db_put` 으로 굳혔다. 굳은 값은 그 뒤 서버(ECOS 키 있음)도 표에서 읽어 그대로 썼고,
도구 응답은 출처를 늘 「한국은행 ECOS」로 적었다(JTC FY0 9.1907 — ECOS 9.1338).
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from open_proxy_mcp.dart import fx


@pytest.fixture
def src(monkeypatch):
    """세 소스와 시계를 가짜로 — 영구 표 미스, ECOS 실패, 야후 9.19."""
    monkeypatch.setattr(fx, "_MEM", {})
    monkeypatch.setattr(fx, "_MEM_NEG", {})
    monkeypatch.setattr(fx, "_MEM_FALLBACK", {})
    clock = {"t": 1000.0}
    monkeypatch.setattr(fx.time, "monotonic", lambda: clock["t"])
    m = {"_db_get": Mock(return_value=None), "_db_put": Mock(),
         "_ecos": AsyncMock(return_value=None), "_yahoo": AsyncMock(return_value=9.19069290161133)}
    for k, v in m.items():
        monkeypatch.setattr(fx, k, v)
    m["clock"] = clock
    return m


def _q(ccy="JPY", dd="20260228"):
    return asyncio.run(fx.fx_to_krw(ccy, dd))


def test_yahoo_value_is_used_but_never_written_to_the_permanent_table(src):
    assert _q() == 9.19069290161133                       # 값은 쓴다(환산 누락보다 낫다)
    src["_db_put"].assert_not_called()                     # 종전: _db_put("JPY", "20260228", 9.19069…) — 표에 굳었다
    assert fx.fx_source("JPY", "20260228") == fx.SRC_YAHOO
    assert ("JPY", "20260228") not in fx._MEM               # 프로세스 영구 기억에도 넣지 않는다


def test_yahoo_value_is_remembered_briefly_then_ecos_is_asked_again(src):
    _q()
    _q()                                                   # 30분 안 — 두 소스 모두 다시 묻지 않는다
    assert src["_ecos"].await_count == 1 and src["_yahoo"].await_count == 1
    src["clock"]["t"] += fx._FALLBACK_TTL_SEC + 1           # 만료 → ECOS 부터 다시
    src["_ecos"].return_value = 913.38 / 100
    assert _q() == 913.38 / 100
    assert src["_ecos"].await_count == 2 and src["_yahoo"].await_count == 1
    src["_db_put"].assert_called_once_with("JPY", "20260228", 913.38 / 100)   # ECOS 값만 영구 표로
    assert fx.fx_source("JPY", "20260228") == fx.SRC_ECOS
    assert ("JPY", "20260228") not in fx._MEM_FALLBACK


def test_expired_yahoo_value_is_refreshed_from_yahoo_while_ecos_stays_down(src):
    _q()
    src["clock"]["t"] += fx._FALLBACK_TTL_SEC + 1
    src["_yahoo"].return_value = 9.2
    assert _q() == 9.2
    assert src["_ecos"].await_count == 2 and src["_yahoo"].await_count == 2
    src["_db_put"].assert_not_called()
    assert fx.fx_source("JPY", "20260228") == fx.SRC_YAHOO


def test_ecos_value_is_persisted_and_marked_ecos(src):
    src["_ecos"].return_value = 1989.56
    assert _q("GBP", "20260430") == 1989.56
    src["_db_put"].assert_called_once_with("GBP", "20260430", 1989.56)
    src["_yahoo"].assert_not_called()
    assert fx.fx_source("GBP", "20260430") == fx.SRC_ECOS


def test_permanent_table_hit_is_ecos_without_network(src):
    src["_db_get"].return_value = 9.4556
    assert _q("JPY", "20260531") == 9.4556
    src["_ecos"].assert_not_called()
    src["_yahoo"].assert_not_called()
    assert fx.fx_source("JPY", "20260531") == fx.SRC_ECOS


def test_implausible_yahoo_value_is_dropped_and_the_failure_remembered_briefly(src):
    src["_yahoo"].return_value = 1e9
    assert _q() is None
    assert fx.fx_source("JPY", "20260228") is None
    assert ("JPY", "20260228") in fx._MEM_NEG
    src["_db_put"].assert_not_called()


def test_implausible_ecos_value_falls_through_to_yahoo(src):
    src["_ecos"].return_value = 0.0
    assert _q() == 9.19069290161133
    src["_db_put"].assert_not_called()
    assert fx.fx_source("JPY", "20260228") == fx.SRC_YAHOO


def test_today_ecos_value_is_not_persisted_but_is_ecos(src, monkeypatch):
    monkeypatch.setattr(fx, "_today", lambda: "20261019")
    src["_ecos"].return_value = 1420.0
    assert asyncio.run(fx.fx_to_krw("USD")) == 1420.0
    src["_db_get"].assert_not_called()                     # 오늘(변동값)은 영구 표를 읽지도 쓰지도 않는다 — 종전과 같다
    src["_db_put"].assert_not_called()
    assert fx.fx_source("USD") == fx.SRC_ECOS


def test_both_sources_failing_is_none_and_clears_a_stale_fallback(src):
    _q()
    src["clock"]["t"] += fx._FALLBACK_TTL_SEC + 1
    src["_yahoo"].return_value = None
    assert _q() is None
    assert fx.fx_source("JPY", "20260228") is None
    assert ("JPY", "20260228") not in fx._MEM_FALLBACK


def test_krw_has_no_source(src):
    assert asyncio.run(fx.fx_to_krw("KRW", "20260228")) == 1.0
    assert fx.fx_source("KRW", "20260228") is None
    assert fx.fx_source(None) is None


def test_source_labels_for_responses():
    assert fx.fx_source_label(fx.SRC_ECOS) == "한국은행 ECOS 매매기준율"
    assert "야후" in fx.fx_source_label(fx.SRC_YAHOO) and "대체값" in fx.fx_source_label(fx.SRC_YAHOO)
    assert fx.fx_source_label(None) == "출처 미확인"
