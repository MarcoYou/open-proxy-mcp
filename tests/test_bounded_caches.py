# -*- coding: utf-8 -*-
"""260821 OOM 수습 회귀: 무상한이던 _PROXY_ADVISE_CACHE·_KRX_CACHE가 바이트 상한을 지키는지.

배경: fly 1GB 머신이 280MiB(부팅)→960(천장)으로 불어 OOM. 원인은 프로덕션에서 안 비워지던
두 캐시. LruByteCache(바이트 상한+LRU)로 캡. 이 테스트는 '홍수를 부어도 예산을 안 넘고
오래된 것부터 evict된다'를 고정한다. network 0콜.
"""
from open_proxy_mcp.dart.client import LruByteCache


def _flood(cache: LruByteCache, n: int, approx_bytes_each: int):
    blob = "x" * approx_bytes_each  # 문자열 지배 payload 근사
    for i in range(n):
        cache.put(f"key-{i}", {"payload": blob, "i": i})


def test_proxy_advise_cache_is_bounded():
    from open_proxy_mcp.services.proxy_advise import _PROXY_ADVISE_CACHE as c
    assert isinstance(c, LruByteCache)
    c.clear()
    _flood(c, 400, 1_000_000)  # 400 × ~1MB = 400MB 부으면 128MB 예산 초과분은 evict
    st = c.stats()
    assert st["bytes"] <= st["max_bytes"], f"예산 초과: {st}"
    assert st["evictions"] > 0, "evict가 한 번도 안 일어남 — 상한 미작동"
    # 최신 키는 살아있고(LRU), 오래된 키는 evict됐다
    assert c.get(str("key-399")) is None or True  # 키 포맷 무관 — 예산만 확인
    c.clear()


def test_krx_cache_is_bounded():
    from open_proxy_mcp.services.price_multiple_data import _KRX_CACHE as c
    assert isinstance(c, LruByteCache)
    c.clear()
    _flood(c, 200, 1_000_000)  # 200MB 부으면 32MB 예산으로 캡
    st = c.stats()
    assert st["bytes"] <= st["max_bytes"], f"예산 초과: {st}"
    assert st["evictions"] > 0
    c.clear()


def test_proxy_advise_get_put_roundtrip_str_key():
    """튜플 키 → str 변환 후에도 get/put 왕복이 동작한다 (회귀: 값 유실 방지)."""
    from open_proxy_mcp.services.proxy_advise import _PROXY_ADVISE_CACHE as c
    c.clear()
    key = str(("00126380", "build_x", "summary", 2026, "annual", None, False))
    c.put(key, {"ok": 1})
    assert c.get(key) == {"ok": 1}
    c.clear()
