# -*- coding: utf-8 -*-
"""콜드 서빙 모듈(`cold_store`) — 서빙 목록을 믿고 서빙본을 받아 검증·캐시하고, 실패하면 강등(None/ColdError)한다.

network 0 · DB 0. 로컬 file:// 서빙본(`cold_serving` 고정물)과 httpx MockTransport(https 흉내)로 본다."""
from __future__ import annotations

import asyncio
import json
import threading
import time

import httpx
import pytest

from open_proxy_mcp import cold_store as C

ROWS = [
    ("005930", "20151230", "KS", 1260000, 147299337),
    ("005930", "20160108", "KS", 1178000, 147299337),
    ("005930", "20231013", "KS", 68000, 5969782550),
    ("000660", "20231013", "KS", 120000, 728002365),
]
UPTO = "20231016"


def _run(coro):
    return asyncio.run(coro)


# ── 꺼짐·기본 ─────────────────────────────────────────────────────────────────
def test_off_by_default_does_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("OPM_COLD_DIR", str(tmp_path / "cache"))
    r = _run(C.ready("krx_weekly_hist"))
    assert isinstance(r, C.ColdError) and r.reason == "off"
    assert not (tmp_path / "cache").exists()          # 꺼져 있으면 디렉터리도 만들지 않는다
    assert C.stats()["enabled"] is False


def test_enabled_without_secret_degrades(tmp_path, monkeypatch):
    monkeypatch.setenv("OPM_COLD", "1")
    monkeypatch.setenv("OPM_COLD_DIR", str(tmp_path / "cache"))
    r = _run(C.ready("krx_weekly_hist"))
    assert isinstance(r, C.ColdError) and r.reason == "no_manifest_url"
    assert C.stats()["degraded"] == {"no_manifest_url": 1}


# ── 받기·검증·재사용 ─────────────────────────────────────────────────────────
def test_ready_downloads_verifies_and_reads(cold_serving, tmp_path):
    info = cold_serving(ROWS, upto=UPTO, max_dd="20231015")
    r = _run(C.ready("krx_weekly_hist"))
    assert isinstance(r, C.Ready), r
    assert (r.min_dd, r.max_dd, r.key, r.date_col, r.view) == ("20151230", "20231015", "ticker", "price_dd", "krx_weekly")
    cached = tmp_path / "cache" / "krx_weekly_hist" / info["entry"]["file"]
    assert cached.read_bytes() == info["sqlite_bytes"]                    # 풀어낸 서빙본 그대로
    rows = _run(C.read(r, ("price_dd", "close", "mktcap"), "005930", "20151230", UPTO))
    assert rows == [("20151230", 1260000, 1260000 * 147299337), ("20160108", 1178000, 1178000 * 147299337),
                    ("20231013", 68000, 68000 * 5969782550)]
    # 반열린 [lo, hi) — hi 날짜는 들어가지 않는다
    assert _run(C.read(r, ("price_dd",), "005930", "20151230", "20231013")) == [("20151230",), ("20160108",)]
    assert _run(C.read(r, ("price_dd",), "005930", UPTO, UPTO)) == []
    st = C.stats()
    assert st["counters"]["downloads"] == 1 and st["files_ok"] == 1
    ds = st["datasets"]["krx_weekly_hist"]
    assert ds["file_ok"] and ds["version"] == info["entry"]["sha256"][:16] and ds["how"] == "downloaded"
    assert ds["received_at"] and ds["coverage"] == {"min_dd": "20151230", "max_dd": "20231015"}


def test_same_version_is_reused_without_download(cold_serving, tmp_path):
    cold_serving(ROWS, upto=UPTO)
    assert isinstance(_run(C.ready("krx_weekly_hist")), C.Ready)
    assert isinstance(_run(C.ready("krx_weekly_hist")), C.Ready)
    assert C.stats()["counters"]["downloads"] == 1
    C.reset_for_tests()                         # 재시작 흉내 — 디스크의 같은 판은 sha 만 다시 보고 쓴다
    r = _run(C.ready("krx_weekly_hist"))
    assert isinstance(r, C.Ready)
    st = C.stats()
    assert st["counters"]["downloads"] == 0 and st["counters"]["verify_reuse"] == 1
    assert st["datasets"]["krx_weekly_hist"]["how"] == "reused"


def test_restart_uses_the_disk_copy_of_the_manifest_when_the_url_fails(cold_serving, monkeypatch):
    cold_serving(ROWS, upto=UPTO)
    assert isinstance(_run(C.ready("krx_weekly_hist")), C.Ready)
    C.reset_for_tests()
    monkeypatch.setenv("OPM_COLD_MANIFEST_URL", "file:///nonexistent/serve/manifest.json")
    r = _run(C.ready("krx_weekly_hist"))           # 마지막 성공본(디스크) + 이미 받은 파일 — 네트워크 없이
    assert isinstance(r, C.Ready)


def test_new_version_replaces_and_prunes_the_old_file(cold_serving, tmp_path):
    first = cold_serving(ROWS, upto=UPTO)
    assert isinstance(_run(C.ready("krx_weekly_hist")), C.Ready)
    second = cold_serving(ROWS + [("035420", "20231013", "KS", 200000, 164000000)], upto=UPTO)   # 발행기가 새 판을 냈다
    assert second["entry"]["sha256"] != first["entry"]["sha256"]
    with C._lock:                                     # 목록 TTL 이 지났다 — 이번 요청은 지금 판, 배경에서 새 목록
        C._state["manifest_fetched"] = time.time() - 7 * 3600

    async def go():
        a = await C.ready("krx_weekly_hist")
        await asyncio.sleep(0.2)
        return a, await C.ready("krx_weekly_hist")
    a, r = _run(go())
    assert isinstance(a, C.Ready) and a.sha256 == first["entry"]["sha256"]
    assert isinstance(r, C.Ready) and r.sha256 == second["entry"]["sha256"]
    names = sorted(p.name for p in (tmp_path / "cache" / "krx_weekly_hist").iterdir())
    assert names == [second["entry"]["file"]]                       # 옛 판·임시 파일 없음


# ── 검증 실패 → 강등 ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("kw", [{"tamper_sha": True}, {"tamper_object_sha": True}])
def test_sha_mismatch_degrades_and_keeps_nothing(cold_serving, tmp_path, kw):
    cold_serving(ROWS, upto=UPTO, **kw)
    r = _run(C.ready("krx_weekly_hist"))
    assert isinstance(r, C.ColdError) and r.reason == "sha_mismatch"
    d = tmp_path / "cache" / "krx_weekly_hist"
    assert not [p for p in d.iterdir() if p.suffix in (".sqlite", ".part")]
    # 같은 판은 한 시간 동안 다시 받지 않는다(매 요청 28 MB 를 받지 않게)
    r2 = _run(C.ready("krx_weekly_hist"))
    assert isinstance(r2, C.ColdError) and r2.reason == "sha_mismatch"
    assert C.stats()["files_bad"] == 1


def test_truncated_object_is_retryable(cold_serving):
    info = cold_serving(ROWS, upto=UPTO)
    good = info["object_path"].read_bytes()
    info["object_path"].write_bytes(good[: len(good) // 2])
    r = _run(C.ready("krx_weekly_hist"))
    assert isinstance(r, C.ColdError) and r.reason == "download"      # 잘린 스트림은 「검증 실패」가 아니다 — 쉬었다가 다시 받는다
    info["object_path"].write_bytes(good)
    again = _run(C.ready("krx_weekly_hist"))                          # 쉬는 동안은 같은 까닭(저장소를 요청마다 두드리지 않는다)
    assert isinstance(again, C.ColdError) and again.reason == "download" and C.stats()["counters"]["downloads"] == 0
    with C._lock:                                                     # 쉬는 시간이 지났다
        C._backoff.clear()
    assert isinstance(_run(C.ready("krx_weekly_hist")), C.Ready)


def test_wrong_file_header_is_rejected(cold_serving, tmp_path):
    import gzip
    import hashlib
    import sqlite3
    info = cold_serving(ROWS, upto=UPTO)
    p = tmp_path / "other.sqlite"
    con = sqlite3.connect(p)
    con.execute("CREATE TABLE x (a)")
    con.close()
    data = p.read_bytes()
    obj = gzip.compress(data, mtime=0)
    info["object_path"].write_bytes(obj)
    m = info["manifest"]
    e = m["datasets"]["krx_weekly_hist"]["files"][0]
    e.update(sha256=hashlib.sha256(data).hexdigest(), bytes=len(data), object_sha256=hashlib.sha256(obj).hexdigest(),
             object_bytes=len(obj))
    info["manifest_path"].write_text(json.dumps(m))
    r = _run(C.ready("krx_weekly_hist"))
    assert isinstance(r, C.ColdError) and r.reason == "bad_file"


@pytest.mark.parametrize("mutate,reason", [
    (lambda m: m.update(schema=2), "manifest_schema"),
    (lambda m: m.update(kind="other"), "manifest_schema"),
    (lambda m: m["datasets"].pop("krx_weekly_hist"), "no_dataset"),
    (lambda m: m["datasets"]["krx_weekly_hist"].update(engine="parquet"), "unsupported"),
    (lambda m: m["datasets"]["krx_weekly_hist"]["files"][0].update(file="../../etc/x.sqlite"), "manifest_schema"),
    (lambda m: m["datasets"]["krx_weekly_hist"].update(view="krx_weekly; DROP"), "manifest_schema"),
    (lambda m: m["datasets"]["krx_weekly_hist"]["files"][0].update(file_schema=2), "unsupported"),
])
def test_manifest_shape_errors_degrade(cold_serving, mutate, reason):
    info = cold_serving(ROWS, upto=UPTO)
    m = info["manifest"]
    mutate(m)
    info["manifest_path"].write_text(json.dumps(m))
    r = _run(C.ready("krx_weekly_hist"))
    assert isinstance(r, C.ColdError) and r.reason == reason


def test_disk_cap(cold_serving, monkeypatch):
    cold_serving(ROWS, upto=UPTO)
    monkeypatch.setenv("OPM_COLD_MAX_MB", "0.001")
    r = _run(C.ready("krx_weekly_hist"))
    assert isinstance(r, C.ColdError) and r.reason == "disk_cap"


def test_rss_guard_and_bad_columns(cold_serving, monkeypatch):
    cold_serving(ROWS, upto=UPTO)
    r = _run(C.ready("krx_weekly_hist"))
    assert _run(C.read(r, ("price_dd", "nope"), "005930", "20151230", UPTO)) is None
    monkeypatch.setattr(C, "_rss_mb", lambda: 1900.0)
    assert _run(C.read(r, ("price_dd",), "005930", "20151230", UPTO)) is None
    assert C.stats()["degraded"] == {"bad_columns": 1, "rss_guard": 1}


def test_read_error_drops_the_verified_mark(cold_serving, tmp_path):
    info = cold_serving(ROWS, upto=UPTO)
    r = _run(C.ready("krx_weekly_hist"))
    (tmp_path / "cache" / "krx_weekly_hist" / info["entry"]["file"]).unlink()
    assert _run(C.read(r, ("price_dd",), "005930", "20151230", UPTO)) is None
    assert C.stats()["files_ok"] == 0
    assert isinstance(_run(C.ready("krx_weekly_hist")), C.Ready)     # 다음 요청이 다시 받는다


# ── https(서명 URL) ──────────────────────────────────────────────────────────
TOKEN = "eyJhbGciOiJIUzI1NiJ9.SECRET-TOKEN-VALUE.sig"
MURL = f"https://storage.example.test/object/sign/b/serve/manifest.json?token={TOKEN}"
OURL = f"https://storage.example.test/object/sign/b/serve/krx_weekly_hist/x.sqlite.gz?token={TOKEN}"


class _Stream(httpx.SyncByteStream):
    """실제 전송처럼 본문을 조각으로 흘린다(서버는 `iter_raw` 로 받는다 — 미리 읽힌 본문이면 StreamConsumed)."""

    def __init__(self, data: bytes, delay: float = 0.0):
        self.data, self.delay = data, delay

    def __iter__(self):
        if self.delay:
            time.sleep(self.delay)
        for i in range(0, len(self.data), 65536):
            yield self.data[i:i + 65536]


def _https_setup(cold_serving, monkeypatch, *, manifest_status=200, object_status=200, entry_url=OURL,
                 url_expires_at=None, delay=0.0, counter=None):
    info = cold_serving(ROWS, upto=UPTO, url=entry_url, url_expires_at=url_expires_at)
    mbytes = info["manifest_path"].read_bytes()

    def handler(request: httpx.Request) -> httpx.Response:
        if counter is not None:
            counter.append(request.url.path)
        if request.url.path.endswith("manifest.json"):
            return httpx.Response(manifest_status, content=mbytes if manifest_status == 200 else b'{"error":"x"}')
        if object_status != 200:
            return httpx.Response(object_status, content=b"denied")
        return httpx.Response(200, stream=_Stream(info["object_bytes"], delay))

    monkeypatch.setattr(C, "_TRANSPORT", httpx.MockTransport(handler))
    monkeypatch.setenv("OPM_COLD_MANIFEST_URL", MURL)
    return info


def _no_secret_in(obj) -> bool:
    s = json.dumps(obj, ensure_ascii=False, default=str)
    return TOKEN not in s and "SECRET-TOKEN" not in s and "storage.example.test" not in s and "/object/sign" not in s


def test_https_signed_urls_happy_path_and_no_secret_in_stats(cold_serving, monkeypatch):
    _https_setup(cold_serving, monkeypatch, url_expires_at="2027-11-20T00:00:00+00:00")
    r = _run(C.ready("krx_weekly_hist"))
    assert isinstance(r, C.Ready)
    st = C.stats()
    assert _no_secret_in(st) and st["manifest_url_set"] and st["datasets"]["krx_weekly_hist"]["url_set"]
    assert st["datasets"]["krx_weekly_hist"]["url_exp_days"] > 300


@pytest.mark.parametrize("which", ["manifest", "object"])
def test_https_403_degrades_without_leaking_the_token(cold_serving, monkeypatch, which):
    _https_setup(cold_serving, monkeypatch, **({"manifest_status": 403} if which == "manifest" else {"object_status": 403}))
    r = _run(C.ready("krx_weekly_hist"))
    assert isinstance(r, C.ColdError) and r.reason == "http_403"
    assert _no_secret_in(C.stats()) and _no_secret_in(str(r))


def test_https_manifest_without_object_url_degrades(cold_serving, monkeypatch):
    _https_setup(cold_serving, monkeypatch, entry_url=None)
    r = _run(C.ready("krx_weekly_hist"))
    assert isinstance(r, C.ColdError) and r.reason == "no_url"


def test_https_manifest_may_not_point_at_local_files(cold_serving, monkeypatch):
    _https_setup(cold_serving, monkeypatch, entry_url="file:///etc/passwd")
    r = _run(C.ready("krx_weekly_hist"))
    assert isinstance(r, C.ColdError) and r.reason == "bad_url"


def test_expired_object_url_degrades_before_any_request(cold_serving, monkeypatch):
    hits: list = []
    _https_setup(cold_serving, monkeypatch, url_expires_at="2020-01-01T00:00:00+00:00", counter=hits)
    r = _run(C.ready("krx_weekly_hist"))
    assert isinstance(r, C.ColdError) and r.reason == "url_expired"
    assert all(h.endswith("manifest.json") for h in hits)


def test_concurrent_requests_download_once(cold_serving, monkeypatch):
    hits: list = []
    _https_setup(cold_serving, monkeypatch, delay=0.2, counter=hits)

    async def many():
        return await asyncio.gather(*(C.ready("krx_weekly_hist") for _ in range(8)))

    res = _run(many())
    assert all(isinstance(r, C.Ready) for r in res)
    assert sum(1 for h in hits if h.endswith(".gz")) == 1
    assert sum(1 for h in hits if h.endswith("manifest.json")) == 1
    assert C.stats()["counters"]["downloads"] == 1


def test_slow_download_degrades_the_request_but_finishes_in_background(cold_serving, monkeypatch):
    hits: list = []
    _https_setup(cold_serving, monkeypatch, delay=0.6, counter=hits)

    async def scenario():
        first = await C.ready("krx_weekly_hist", wait=0.1)
        await asyncio.sleep(1.0)                  # 받기는 배경에서 끝난다
        second = await C.ready("krx_weekly_hist", wait=0.1)
        return first, second

    first, second = _run(scenario())
    assert isinstance(first, C.ColdError) and first.reason == "timeout"
    assert isinstance(second, C.Ready)
    assert sum(1 for h in hits if h.endswith(".gz")) == 1


def test_stale_manifest_is_refreshed_in_background_then_refused_when_too_old(cold_serving, monkeypatch):
    hits: list = []
    _https_setup(cold_serving, monkeypatch, counter=hits)
    assert isinstance(_run(C.ready("krx_weekly_hist")), C.Ready)
    # TTL 이 지났다 — 지금 것을 쓰고 배경에서 새로 받는다
    with C._lock:
        C._state["manifest_fetched"] = time.time() - 7 * 3600

    async def ttl():
        r = await C.ready("krx_weekly_hist")
        await asyncio.sleep(0.2)
        return r
    assert isinstance(_run(ttl()), C.Ready)
    assert sum(1 for h in hits if h.endswith("manifest.json")) == 2
    # 최대 나이를 넘었고 새로 받지도 못한다 — 쓰지 않는다
    monkeypatch.setattr(C, "_TRANSPORT", httpx.MockTransport(lambda req: httpx.Response(403)))
    with C._lock:
        C._state["manifest_fetched"] = time.time() - 80 * 3600
    r = _run(C.ready("krx_weekly_hist"))
    assert isinstance(r, C.ColdError) and r.reason == "manifest_stale"


def test_background_task_attaches_to_lifespan_and_stops(cold_serving, monkeypatch):
    from contextlib import asynccontextmanager

    cold_serving(ROWS, upto=UPTO)
    monkeypatch.setenv("OPM_COLD_PREWARM_SEC", "0.01")

    @asynccontextmanager
    async def inner(app):
        yield {}

    class Router:
        lifespan_context = staticmethod(inner)

    class App:
        router = Router()

    app = App()
    C.attach_to_app(app)

    async def go():
        async with app.router.lifespan_context(app):
            for _ in range(100):
                await asyncio.sleep(0.05)
                if C.stats()["files_ok"]:
                    break
    _run(go())
    assert C.stats()["files_ok"] == 1 and C.stats()["counters"]["downloads"] == 1


def test_stats_is_safe_from_other_threads(cold_serving):
    cold_serving(ROWS, upto=UPTO)
    assert isinstance(_run(C.ready("krx_weekly_hist")), C.Ready)
    out = []
    t = threading.Thread(target=lambda: out.append(C.stats()))
    t.start()
    t.join()
    assert out and out[0]["files_ok"] == 1


def test_scrub_removes_urls_tokens_and_hosts():
    msg = f"ConnectError: failed for {MURL} (host storage.example.test)"
    out = C._scrub(msg, MURL)
    assert TOKEN not in out and "storage.example.test" not in out and "<url>" in out
    assert "https://" not in C._scrub("see https://elsewhere.example/x?y=1")


def test_health_carries_cold_and_never_degrades_because_of_it(monkeypatch, tmp_path):
    from starlette.testclient import TestClient

    from open_proxy_mcp.server import mcp

    client = TestClient(mcp.streamable_http_app())
    body = client.get("/health").json()
    assert body["cold"]["enabled"] is False and body["cold"]["datasets"] == {}
    # 켜졌는데 서빙 목록을 못 받는다 — 서버 상태는 그대로 ok(응답은 최근 3년으로 강등될 뿐)
    monkeypatch.setenv("OPM_COLD", "1")
    monkeypatch.setenv("OPM_COLD_MANIFEST_URL", MURL)
    monkeypatch.setenv("OPM_COLD_DIR", str(tmp_path / "cache"))
    monkeypatch.setattr(C, "_TRANSPORT", httpx.MockTransport(lambda req: httpx.Response(403)))
    assert isinstance(_run(C.ready("krx_weekly_hist")), C.ColdError)
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    cold = r.json()["cold"]
    assert cold["enabled"] and cold["degraded"] == {"http_403": 1} and _no_secret_in(cold)


def test_failed_manifest_fetch_backs_off_instead_of_hammering(cold_serving, monkeypatch):
    hits: list = []
    _https_setup(cold_serving, monkeypatch, manifest_status=403, counter=hits)
    for _ in range(5):
        r = _run(C.ready("krx_weekly_hist"))
        assert isinstance(r, C.ColdError) and r.reason == "http_403"
    assert sum(1 for h in hits if h.endswith("manifest.json")) == 1
    assert C.stats()["degraded"] == {"http_403": 5}


def test_verified_version_does_not_queue_behind_the_download_thread(cold_serving, monkeypatch):
    cold_serving(ROWS, upto=UPTO)
    assert isinstance(_run(C.ready("krx_weekly_hist")), C.Ready)
    blocker = threading.Event()
    C._executor().submit(blocker.wait, 5)          # 받기 스레드가 다른 일(긴 받기)로 막혀 있다
    try:
        t = time.perf_counter()
        r = _run(C.ready("krx_weekly_hist", wait=0.5))
        assert isinstance(r, C.Ready) and time.perf_counter() - t < 0.4
    finally:
        blocker.set()
