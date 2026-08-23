"""캐시 고수위/저수위 — 260824 신설.

종전엔 상한에 닿으면 「딱 들어갈 만큼만」 밀어냈다. 그러면 캐시가 100% 에 붙박이고
**삽입마다 evict** 가 난다. 실측(live, 260824): `document` 가 몇 시간에 3,722건을
밀어냈고 항목은 696→528 로 줄었는데 용량은 82→95MB 로 늘었다 — 큰 문서가 작은 것들을
끊임없이 밀어내는 중이었다. 밀려난 문서는 다음 요청에 DART 를 다시 부른다.
"""
from __future__ import annotations

import pytest

from open_proxy_mcp.dart.client import (
    _CACHE_HIGH_RATIO,
    _CACHE_LOW_RATIO,
    _cache_entry_bytes,
    LruByteCache,
)

#: 파이썬 str 자체의 고정 오버헤드. 캐시는 `_cache_entry_bytes`(getsizeof 기반) 로 재므로
#: 「100 바이트짜리」를 만들려면 그만큼 빼야 한다. 상수를 박지 않고 실측한다 —
#: 인터프리터 버전이 바뀌면 값이 달라지고, 박아두면 그때 테스트가 조용히 거짓이 된다.
_STR_OVERHEAD = _cache_entry_bytes("")


def _cache(max_bytes=1000):
    return LruByteCache(max_bytes, 3600, "t")


def _blob(n: int) -> str:
    """`_cache_entry_bytes` 로 재서 **정확히 n 바이트**인 문자열."""
    assert n > _STR_OVERHEAD, f"n 은 {_STR_OVERHEAD} 보다 커야 한다"
    b = "x" * (n - _STR_OVERHEAD)
    assert _cache_entry_bytes(b) == n
    return b


def test_ratios_are_sane():
    """뒤집힌 값이면 evict 가 끝나지 않는다 — 상수 자체를 잠근다."""
    assert 0 < _CACHE_LOW_RATIO < _CACHE_HIGH_RATIO <= 1.0
    assert round(_CACHE_HIGH_RATIO, 2) == 0.95, "한계의 5% 앞에서 발동"
    assert round(_CACHE_LOW_RATIO, 2) == 0.75, "한 번에 20% 확보"


def test_no_eviction_below_high_watermark():
    """고수위 아래에서는 **한 건도** 밀어내지 않는다."""
    c = _cache(1000)
    for i in range(9):
        c.put(f"k{i}", _blob(100))          # 900B = 90%
    st = c.stats()
    assert st["evictions"] == 0 and st["sweeps"] == 0
    assert st["entries"] == 9


def test_crossing_high_watermark_sweeps_to_low():
    """고수위를 넘기는 삽입은 저수위까지 한 번에 쓸어낸다."""
    c = _cache(1000)
    for i in range(9):
        c.put(f"k{i}", _blob(100))          # 900B
    c.put("new", _blob(100))                # 900+100 > 950 → 스윕
    st = c.stats()
    assert st["sweeps"] == 1
    assert st["bytes"] <= 750 + 100, f"저수위까지 안 내려감: {st['bytes']}"
    assert st["entries"] < 10


def test_watermarks_keep_headroom_instead_of_pinning_at_the_limit():
    """★ 이 테스트가 정책의 이유다 — 그리고 **이유를 정확히** 적는다.

    수위는 evict **총량**을 줄이지 못한다. 워킹셋이 예산보다 크면 들어온 바이트만큼
    나가야 하는 산수다. 수위가 주는 것은 **여유 공간**이다 —
      종전: 항상 (상한−항목크기) ~ 상한 사이에 붙어 있었다 (실측 90~100%)
      지금: 저수위 ~ 고수위 사이에 산다 (75~95%)
    1GB 머신에서 그 10MB 가 260804 OOM 여유다.
    """
    c = _cache(1000)
    fills = []
    for i in range(40):
        c.put(f"k{i}", _blob(100))
        fills.append(c.stats()["bytes"])
    settled = fills[10:]                      # 채워진 뒤의 정상 상태만 본다
    assert max(settled) <= 950, f"고수위를 넘겨 앉아 있다: {max(settled)}"
    assert sum(settled) / len(settled) < 900, "평균이 여전히 상한에 붙어 있다"
    assert c.stats()["bytes"] <= 1000


def test_sweeps_are_batched_not_per_insert():
    """스윕 **횟수**는 줄어야 한다. 디스크 쪽은 스윕마다 디렉터리 전체를 stat 하므로
    이 횟수가 곧 비용이다."""
    c = _cache(1000)
    for i in range(40):
        c.put(f"k{i}", _blob(100))
    st = c.stats()
    assert st["sweeps"] < 20, f"삽입마다 스윕한다: {st['sweeps']}/40"


def test_hard_limit_still_holds_at_every_step():
    """수위는 **여유**를 만드는 장치지 상한을 무르는 장치가 아니다."""
    c = _cache(1000)
    import random
    rnd = random.Random(7)
    for i in range(200):
        c.put(f"k{i}", _blob(rnd.choice([50, 100, 250, 400])))
        assert c.stats()["bytes"] <= 1000, f"{i}번째에서 예산 초과"


def test_oversized_item_does_not_wipe_the_cache():
    """저수위보다 큰 항목 하나가 들어올 때 target 이 음수가 되면 캐시가 통째로 비워진다.
    max(0, ...) 로 막았는지 — 들어갈 만큼만 비우고 나머지는 남아야 한다."""
    c = _cache(1000)
    for i in range(5):
        c.put(f"k{i}", _blob(100))          # 500B
    c.put("big", _blob(800))                # 저수위(750)보다 큼
    st = c.stats()
    assert st["bytes"] <= 1000
    assert c.get("big") is not None, "큰 항목 자체는 들어가야 한다"


def test_item_larger_than_budget_is_rejected_not_swept():
    """예산보다 큰 항목은 담지 않는다 — 담으려고 캐시를 비우면 손해만 본다."""
    c = _cache(1000)
    for i in range(5):
        c.put(f"k{i}", _blob(100))
    c.put("huge", _blob(2000))
    st = c.stats()
    assert st["rejections"] == 1
    assert st["entries"] == 5, "거절인데 기존 항목을 비웠다"


def test_stats_expose_watermarks_for_operators():
    """/health 로 「지금 몇 %에서 쓸리나」를 볼 수 있어야 한다."""
    st = _cache(1000).stats()
    assert st["high_pct"] == 95 and st["low_pct"] == 75
    assert "sweeps" in st


@pytest.mark.parametrize("name,mb", [("document", 96), ("dividend", 16),
                                     ("proxy_advise", 128), ("krx", 32)])
def test_all_instances_share_the_policy(name, mb):
    """수위는 한 곳(SSOT)에서 온다 — 인스턴스마다 따로 정하면 한쪽만 고쳐진다."""
    c = LruByteCache(mb * 1024 * 1024, 3600, name)
    assert c._high_bytes == int(mb * 1024 * 1024 * _CACHE_HIGH_RATIO)
    assert c._low_bytes == int(mb * 1024 * 1024 * _CACHE_LOW_RATIO)
