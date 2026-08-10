# -*- coding: utf-8 -*-
"""메모리 캐시 예산 — 260804 OOM(exit_code=137, fly 머신 kill) 회귀 방어. network 0콜.

사고의 뿌리는 예산을 **항목 수**로 잡은 것이었다. 「200 entry × ~500KB = ~100MB」라는
주석이 있었지만 실측하면 사업보고서 한 건이 8.7~29.0MB(중앙값 18.9MB)라 35~58배 어긋났고,
게다가 같은 상한 200 을 쓰는 캐시가 doc·viewer 둘이라 실제 수용량은 문서화된 값의 두 배였다.
여기서 지키는 건 「가정이 틀려도 예산은 안 틀린다」 하나다.
"""
from __future__ import annotations

import time

import pytest

from open_proxy_mcp.dart.client import (
    LruByteCache,
    _cache_entry_bytes,
    _doc_key,
    _viewer_key,
    cache_stats,
)


def _doc(mb: float) -> dict:
    """대략 mb 메가바이트짜리 문서 페이로드. 한글은 파이썬 str 이 문자당 2바이트를 쓴다."""
    return {"text": "가" * int(mb * 1024 * 1024 / 2), "html": "", "images": []}


# ── 크기 측정 ──

def test_entry_bytes_counts_nested_payload_not_just_the_container():
    """dict 껍데기만 재면 사업보고서 20MB 를 64바이트로 착각한다 — 그게 사고의 형태였다."""
    small, big = _doc(0.1), _doc(4.0)
    assert _cache_entry_bytes(big) > _cache_entry_bytes(small) * 20
    assert _cache_entry_bytes(big) > 4 * 1024 * 1024


def test_entry_bytes_survives_self_reference():
    payload: dict = {"text": "가" * 100}
    payload["self"] = payload
    assert _cache_entry_bytes(payload) > 0   # 무한재귀로 죽지 않는다


# ── 예산이 개수가 아니라 바이트로 걸리는가 ──

def test_budget_is_bytes_so_a_few_huge_entries_cannot_blow_past_it():
    """옛 규칙(개수 200)으로 실제 문서 200건을 통과시키면 828MB 가 쌓였다(실측)."""
    cache = LruByteCache(max_bytes=50 * 1024 * 1024, ttl_sec=3600, name="t")
    for i in range(200):
        cache.put(f"doc:{i}", _doc(18.9))     # 사업보고서 상위 구간
    assert cache.stats()["bytes"] <= 50 * 1024 * 1024
    assert len(cache) <= 3, "18.9MB 항목이 50MB 예산에 3건 넘게 들어갈 수 없다"


def test_bound_holds_as_distinct_documents_keep_flowing():
    """사고에서 실제로 깨진 성질 — 서로 다른 문서가 계속 들어와도 평평해야 한다.
    실측 분포(p50 0.78MB · p90 10.5MB · 최대 62MB)를 섞어 흘린다."""
    import random

    random.seed(3)
    cache = LruByteCache(max_bytes=96 * 1024 * 1024, ttl_sec=3600, name="t")
    for i in range(400):
        mb = random.choice([0.78] * 5 + [3.78, 10.5, 26.6, 62.0])
        cache.put(f"doc:{i}", _doc(mb))
        assert cache.stats()["bytes"] <= 96 * 1024 * 1024, f"{i}번째에서 예산 초과"
    assert cache.evictions > 0, "예산이 실제로 걸렸어야 한다"


def test_budget_holds_for_many_small_entries_too():
    cache = LruByteCache(max_bytes=8 * 1024 * 1024, ttl_sec=3600, name="t")
    for i in range(5000):
        cache.put(f"doc:{i}", _doc(0.034))    # 소집공고 중앙값 34KB
    assert cache.stats()["bytes"] <= 8 * 1024 * 1024
    assert len(cache) > 100, "작은 항목은 넉넉히 담겨야 캐시 값어치가 있다"


def test_oversized_entry_is_skipped_instead_of_wiping_the_cache():
    """예산보다 큰 단일 항목을 담으려 하면 캐시를 다 비우고도 못 들어간다 — 담지 않는다."""
    cache = LruByteCache(max_bytes=4 * 1024 * 1024, ttl_sec=3600, name="t")
    cache.put("doc:keep", _doc(1.0))
    cache.put("doc:huge", _doc(29.0))         # 실측 최대 사업보고서
    assert cache.get("doc:huge") is None
    assert cache.get("doc:keep") is not None, "거대 항목 하나가 멀쩡한 캐시를 쓸어버리면 안 된다"
    assert cache.stats()["rejections"] == 1


# ── LRU·TTL ──

def test_eviction_order_is_least_recently_used_not_insertion():
    cache = LruByteCache(max_bytes=3 * 1024 * 1024, ttl_sec=3600, name="t")
    cache.put("doc:a", _doc(1.0))
    cache.put("doc:b", _doc(1.0))
    cache.get("doc:a")                        # a 를 최근 사용으로 끌어올린다
    cache.put("doc:c", _doc(1.0))             # 예산 초과 → 가장 안 쓰인 b 가 나가야 한다
    assert cache.get("doc:a") is not None
    assert cache.get("doc:b") is None
    assert cache.get("doc:c") is not None


def test_expired_entry_is_dropped_and_frees_its_bytes():
    cache = LruByteCache(max_bytes=8 * 1024 * 1024, ttl_sec=0.05, name="t")
    cache.put("doc:a", _doc(1.0))
    assert cache.stats()["bytes"] > 0
    time.sleep(0.06)
    assert cache.get("doc:a") is None
    assert cache.stats()["bytes"] == 0, "만료 항목을 지우면서 바이트 회계도 같이 줄어야 한다"


def test_replacing_a_key_does_not_double_count_its_bytes():
    cache = LruByteCache(max_bytes=8 * 1024 * 1024, ttl_sec=3600, name="t")
    cache.put("doc:a", _doc(1.0))
    first = cache.stats()["bytes"]
    for _ in range(5):
        cache.put("doc:a", _doc(1.0))
    assert cache.stats()["bytes"] == pytest.approx(first, rel=0.01)
    assert len(cache) == 1


def test_pop_frees_bytes():
    cache = LruByteCache(max_bytes=8 * 1024 * 1024, ttl_sec=3600, name="t")
    cache.put("doc:a", _doc(1.0))
    assert cache.pop("doc:a") is not None
    assert cache.stats()["bytes"] == 0
    assert cache.pop("doc:a", "없음") == "없음"


# ── doc 와 viewer 가 예산을 나눠 쓰는가 (사고의 두 번째 축) ──

def test_document_and_viewer_share_one_budget():
    """예전엔 각자 200개씩 — 문서화된 예산의 두 배를 담을 수 있었다."""
    cache = LruByteCache(max_bytes=10 * 1024 * 1024, ttl_sec=3600, name="t")
    for i in range(20):
        cache.put(_doc_key(f"2026031800{i:04d}"), _doc(1.0))
        cache.put(_viewer_key(f"2026031800{i:04d}", ("사업의 내용",)), _doc(1.0))
    assert cache.stats()["bytes"] <= 10 * 1024 * 1024


def test_doc_and_viewer_keys_never_collide():
    rcept = "20260318001423"
    assert _doc_key(rcept) != _viewer_key(rcept, ())
    assert _viewer_key(rcept, ("가",)) != _viewer_key(rcept, ("나",))


# ── 프로세스 전역 공유 (API 키 수만큼 예산이 곱해지지 않는가) ──

def test_caches_are_shared_across_client_instances(monkeypatch):
    """`_instances` 는 API 키마다 DartClient 를 만든다. 캐시가 인스턴스 소유면
    사용자 20명 = 예산 20배가 되어 예산이라는 말이 무의미해진다."""
    from open_proxy_mcp.dart.client import DartClient

    monkeypatch.setenv("OPENDART_API_KEY", "0" * 40)
    a, b = DartClient(), DartClient()
    assert a._doc_cache is b._doc_cache
    assert a._dividend_cache is b._dividend_cache


def test_dividend_cache_is_bounded():
    """원래 「영구 캐시」라 상한도 TTL 도 없었다 — 프로세스 수명 내내 자랐다."""
    from open_proxy_mcp.dart.client import _DIVIDEND_CACHE

    assert _DIVIDEND_CACHE._max_bytes > 0
    assert _DIVIDEND_CACHE._ttl_sec > 0


def test_health_cache_stats_expose_budget_fill():
    stats = cache_stats()
    for name in ("document", "dividend"):
        assert stats[name]["max_bytes"] > 0
        assert 0 <= stats[name]["fill_pct"] <= 100
        assert stats[name]["bytes"] <= stats[name]["max_bytes"]


def test_total_budget_fits_the_1gb_vm():
    """baseline 172MB(import 87 + corpCode 66 + 인터프리터 20) + 캐시 + 파싱 transient 여유."""
    from open_proxy_mcp.dart.client import _DIVIDEND_CACHE, _DOC_CACHE

    total_mb = (_DOC_CACHE._max_bytes + _DIVIDEND_CACHE._max_bytes) / 1024 / 1024
    assert total_mb + 172 + 150 < 1024, f"캐시 예산 {total_mb}MB 는 1GB VM 에서 여유가 없다"


# ── 디스크 캐시: 배포를 견디는가 · 예산을 지키는가 ────────────────────────
def test_fly_points_the_disk_cache_at_the_volume():
    """**이 테스트가 A 수정의 전부다.**

    코드가 경로를 env 로 읽게 만들어도 fly 가 안 넘겨주면 아무것도 안 바뀐다.
    종전 기본값 `/tmp/opm_cache` 는 컨테이너 이미지 안이라 배포마다 사라졌고,
    메모리 캐시가 죽는 그 순간 받침도 같이 죽었다(260810 실측: 배포 직후 적중률 0%).
    """
    import pathlib
    import re

    txt = (pathlib.Path(__file__).resolve().parent.parent / "fly.toml").read_text(encoding="utf-8")
    mount = re.search(r"destination\s*=\s*'([^']+)'", txt)
    cache = re.search(r"OPM_DOC_CACHE_DIR\s*=\s*'([^']+)'", txt)
    assert mount, "fly.toml 에 볼륨 마운트가 없다"
    assert cache, "fly.toml 이 OPM_DOC_CACHE_DIR 를 안 넘긴다 — 캐시가 /tmp 로 되돌아간다"
    assert cache.group(1).startswith(mount.group(1).rstrip("/") + "/"), (
        f"디스크 캐시 {cache.group(1)} 가 볼륨 {mount.group(1)} 밖이다 — 배포를 못 견딘다")


def test_disk_cache_sweep_evicts_oldest_first_until_under_budget(tmp_path, monkeypatch):
    """볼륨엔 배포가 청소해 주던 자동 정리가 없다. 같은 볼륨에 master.db(원장)가 살아서
    캐시가 볼륨을 채우면 **원장 쓰기가 실패한다** — 그래서 예산은 선택이 아니다."""
    import os

    import open_proxy_mcp.dart.client as C

    monkeypatch.setattr(C, "_DISK_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(C, "_DISK_CACHE_MAX_BYTES", 300)
    for i in range(5):
        p = tmp_path / f"doc{i}.json"
        p.write_text("x" * 100, encoding="utf-8")
        os.utime(p, (1000 + i, 1000 + i))          # 오래된 것 = 작은 i

    freed = C._sweep_disk_cache(force=True)

    assert freed == 200, f"예산 300 에 500 이 있었는데 {freed} 만 지웠다"
    left = sorted(p.name for p in tmp_path.glob("*.json"))
    assert left == ["doc2.json", "doc3.json", "doc4.json"], f"오래된 순으로 안 지웠다: {left}"
    assert C._sweep_disk_cache(force=True) == 0, "예산 이하인데 또 지웠다"


def test_corrupt_disk_entry_is_a_miss_not_a_crash(tmp_path, monkeypatch):
    """`/tmp` 시절엔 잘린 파일이 배포 때 사라져 저절로 나았다. 볼륨에서는 안 낫는다 —
    한 번 잘린 json 이 그 rcept_no 를 영구히 못 읽게 만든다."""
    from open_proxy_mcp.dart.client import DartClient

    c = DartClient()
    monkeypatch.setattr(c, "_disk_cache_dir", str(tmp_path))
    (tmp_path / "20260101000001.json").write_text('{"body": "잘린', encoding="utf-8")

    assert c._load_from_disk("20260101000001") is None, "깨진 파일에서 예외가 났거나 값을 냈다"
    assert not (tmp_path / "20260101000001.json").exists(), "깨진 파일이 안 지워졌다 — 영구 miss"


def test_disk_write_is_atomic_and_leaves_no_tmp(tmp_path, monkeypatch):
    """쓰다 죽어도 부분 파일이 캐시로 읽히면 안 된다 — 임시 파일에 쓰고 rename 한다."""
    from open_proxy_mcp.dart.client import DartClient

    c = DartClient()
    monkeypatch.setattr(c, "_disk_cache_dir", str(tmp_path))
    c._save_to_disk("20260101000002", {"rcept_no": "20260101000002", "body": "본문"})

    assert c._load_from_disk("20260101000002")["body"] == "본문"
    assert not list(tmp_path.glob("*.tmp")), "임시 파일이 남았다"


def test_disk_cache_stats_say_whether_it_survives_deploys(monkeypatch):
    """`persistent` 가 /health 에 보여야 한다 — 종전 사고가 정확히 그 지점이었고,
    숫자만 보면 「캐시가 있다」와 「배포를 견딘다」를 구분할 수 없다."""
    import tempfile

    import open_proxy_mcp.dart.client as C

    monkeypatch.setattr(C, "_DISK_CACHE_DIR", tempfile.gettempdir() + "/opm_cache")
    assert C._disk_cache_stats()["persistent"] is False
    monkeypatch.setattr(C, "_DISK_CACHE_DIR", "/data/opm_cache")
    assert C._disk_cache_stats()["persistent"] is True
    assert C.cache_stats()["document_disk"]["max_bytes"] > 0


def test_writing_documents_keeps_the_volume_under_budget(tmp_path, monkeypatch):
    """앞 테스트들은 청소 **함수**를 본다. 이건 쓰기 경로가 그 함수를 실제로 부르는지를
    본다 — 함수만 맞고 안 불리면 볼륨은 그대로 찬다(가장 조용한 실패 모양)."""
    import open_proxy_mcp.dart.client as C
    from open_proxy_mcp.dart.client import DartClient

    monkeypatch.setattr(C, "_DISK_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(C, "_DISK_CACHE_MANAGED", True)
    monkeypatch.setattr(C, "_DISK_CACHE_MAX_BYTES", 4096)
    monkeypatch.setattr(C, "_DISK_SWEEP_EVERY", 1)
    monkeypatch.setattr(C, "_disk_writes_since_sweep", 0)
    c = DartClient()
    monkeypatch.setattr(c, "_disk_cache_dir", str(tmp_path))

    for i in range(12):
        c._save_to_disk(f"2026010100{i:04d}", {"body": "가" * 400})

    used = sum(p.stat().st_size for p in tmp_path.glob("*.json"))
    assert used <= 4096, f"예산 4096B 인데 {used}B 가 남았다 — 쓰기가 청소를 안 부른다"
    assert list(tmp_path.glob("*.json")), "전부 지워졌다 — 방금 쓴 것까지 날렸다"


def test_the_local_regression_corpus_is_never_swept(tmp_path, monkeypatch):
    """`/tmp/opm_cache` 는 그냥 캐시가 아니라 **회귀 재생의 유일한 소재**다
    (CLAUDE.md: 회귀 캐시는 DART 응답 경계에서만 만든다). 경로를 명시하지 않은 곳에
    예산을 집행하면 그 소재를 우리 손으로 지운다 — 260810 실측 로컬 1.35GB/2,350건이
    파일럿 한 번에 256MB 로 깎일 뻔했다."""
    import open_proxy_mcp.dart.client as C
    from open_proxy_mcp.dart.client import DartClient

    monkeypatch.setattr(C, "_DISK_CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(C, "_DISK_CACHE_MANAGED", False)     # 로컬 기본값
    monkeypatch.setattr(C, "_DISK_CACHE_MAX_BYTES", 100)     # 일부러 턱없이 작게
    monkeypatch.setattr(C, "_DISK_SWEEP_EVERY", 1)
    monkeypatch.setattr(C, "_disk_writes_since_sweep", 0)
    c = DartClient()
    monkeypatch.setattr(c, "_disk_cache_dir", str(tmp_path))

    for i in range(5):
        c._save_to_disk(f"2026010100{i:04d}", {"body": "가" * 400})

    assert len(list(tmp_path.glob("*.json"))) == 5, "로컬 회귀 소재가 청소됐다"
    assert C._disk_cache_stats()["swept"] is False
