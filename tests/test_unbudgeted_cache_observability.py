"""예산 없는 전역 캐시가 **보이는지** — 관측 계약. 네트워크 0.

배경(260901·260909). 260901 에 두 머신이 동시에 OOM(exit 137) 했을 때 `/health` 의 캐시
점유는 예산 296MB 의 33% 였다 — 「멀쩡하다」로 읽힌다. 260909 에도 같은 모양이 났다:
부팅 직후 242MB 인 프로세스가 30분 만에 708MB 가 되고 2~3시간마다 죽는데,
`cache_stats()` 는 내내 `_used_mb: 0.0` 을 보고했다.

즉 **자라는 것이 관측 밖에 있었다.** `_CACHE_REGISTRY` 는 `LruByteCache` 전용이라
서비스가 모듈 전역에 둔 평범한 dict 캐시(상한도 evict 도 없는 것들)를 못 본다.
이 테스트는 그 장부가 실제로 그것들을 잡는지 못 박는다.
"""
import pytest

from open_proxy_mcp.dart import client as C


@pytest.fixture(autouse=True)
def _isolate_registry():
    """장부는 모듈 전역이라 테스트끼리 샌다 — 앞뒤로 갈아 끼운다."""
    saved, saved_memo = list(C._UNBUDGETED_CACHES), dict(C._unbudgeted_memo)
    C._UNBUDGETED_CACHES.clear(); C._unbudgeted_memo.clear()
    yield
    C._UNBUDGETED_CACHES[:] = saved
    C._unbudgeted_memo.clear(); C._unbudgeted_memo.update(saved_memo)


def test_a_registered_cache_shows_its_size():
    cache = {f"k{i}": "x" * 1000 for i in range(50)}
    C.register_unbudgeted_cache("t", lambda: cache)
    st = C.unbudgeted_cache_stats()
    assert st["t"]["entries"] == 50
    assert st["t"]["bytes_est"] > 40_000        # 50 × ~1KB
    assert st["_total_mb"] >= 0


def test_the_getter_follows_rebinding():
    """설계의 핵심 — dict 를 직접 받으면 `_X = None` 으로 시작하는 캐시를 영영 0 으로 본다.

    `law_index` 가 정확히 그 모양이라(로드 전 None → 로드 시 새 dict 로 재바인딩),
    직접 참조였다면 54MB 를 0 으로 보고했을 것이다.
    """
    box = {"v": None}
    C.register_unbudgeted_cache("late", lambda: box["v"])
    assert C.unbudgeted_cache_stats()["late"]["entries"] == 0

    box["v"] = {f"k{i}": "y" * 500 for i in range(10)}
    st = C.unbudgeted_cache_stats()
    assert st["late"]["entries"] == 10 and st["late"]["bytes_est"] > 0


def test_growth_is_visible_without_waiting_for_the_ttl():
    """개수가 바뀌면 즉시 다시 잰다 — 60초 memo 가 **자라는 것을 가리면** 계기가 무의미하다."""
    cache: dict[str, str] = {}
    C.register_unbudgeted_cache("g", lambda: cache)
    first = C.unbudgeted_cache_stats()["g"]["bytes_est"]
    cache.update({f"k{i}": "z" * 2000 for i in range(100)})
    assert C.unbudgeted_cache_stats()["g"]["bytes_est"] > first


def test_a_broken_getter_does_not_break_the_health_check():
    """관측이 서빙을 깨면 안 된다 — 하나가 죽어도 나머지는 나온다."""
    def _boom():
        raise RuntimeError("등록된 캐시가 사라졌다")

    C.register_unbudgeted_cache("bad", _boom)
    C.register_unbudgeted_cache("good", lambda: {"a": 1})
    st = C.unbudgeted_cache_stats()
    assert "bad" not in st and st["good"]["entries"] == 1


def test_registering_the_same_name_twice_keeps_one():
    """모듈이 두 번 import 돼도 장부가 부풀지 않는다."""
    C.register_unbudgeted_cache("dup", lambda: {"a": 1})
    C.register_unbudgeted_cache("dup", lambda: {"a": 1, "b": 2})
    assert sum(1 for n, _ in C._UNBUDGETED_CACHES if n == "dup") == 1


def test_the_real_caches_are_registered_at_their_definition_site(_isolate_registry=None):
    """나열식이면 캐시를 더할 때 한쪽만 고쳐진다(260824 교훈) — 정의한 자리에서 등록한다."""
    C._UNBUDGETED_CACHES[:] = []
    import importlib
    for mod in ("open_proxy_mcp.services.financial_metrics",
                "open_proxy_mcp.services.law_lookup",
                "open_proxy_mcp.services.trading"):
        importlib.reload(importlib.import_module(mod))
    names = {n for n, _ in C._UNBUDGETED_CACHES}
    assert {"financial_metrics", "law_fulltext", "law_index", "trading_quote"} <= names
