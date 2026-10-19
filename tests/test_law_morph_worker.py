"""law_lookup 형태소 분석기(Kiwi)를 별도 프로세스에서 — 쉬면 내려 메모리를 돌려준다 (261019). network 0콜.

로컬 리눅스 컨테이너 실측(uv.lock 그대로): 첫 law_lookup 한 번에 서버 RSS +511 MiB 중 Kiwi 가 473(원문 캐시 ≤4).
같은 프로세스에서 놓으면 233 만 돌아오고, 별도 프로세스는 끝나면 전부 돌아온다(cgroup 635 → 140 MiB).
여기서는 **응답(토큰)이 같은가**와 **실패해도 응답이 같은가**, **쉬면 끝나는가**를 본다 — RSS 는 플랫폼마다 달라 재지 않는다.
"""
from __future__ import annotations

import json
import os
import sys
import time

import pytest

pytest.importorskip("kiwipiepy")

import open_proxy_mcp.services.law_lookup as L
from open_proxy_mcp.services import morph_worker as MW

pytestmark = pytest.mark.skipif(os.name != "posix", reason="별도 프로세스 경로는 POSIX 전용(윈도는 같은 프로세스)")

_TEXTS = [
    "상법 제363조 주주총회 소집 통지",
    "감사위원 분리선출 의결권 3% 제한",
    "한국회계기준원",                 # 다어절 사전이 묶는 이름 — 가벼운 설정이면 갈렸다
    "제542조의8",
    "",
    "A\nB 이사\t감사 — 「따옴표」 ①항",
]


@pytest.fixture
def fresh(monkeypatch):
    """모듈 싱글턴을 새것으로 — 시험끼리 자식·계수를 섞지 않는다."""
    monkeypatch.delenv("OPM_MORPH_WORKER", raising=False)
    w = MW.MorphWorker()
    monkeypatch.setattr(MW, "_WORKER", w)
    yield w
    w.stop()


def _inproc(text):
    k = L._get_kiwi()
    return [(m.form, m.tag) for m in k.tokenize(text or "")]


def test_worker_tokens_equal_in_process_tokens(fresh):
    for t in _TEXTS:
        assert fresh.tokenize(t) == _inproc(t), t
    st = fresh.stats()
    assert st["mode"] == "subprocess" and st["spawns"] == 1 and st["requests"] == len(_TEXTS), st
    assert st["fallbacks"] == 0 and st["last_error"] is None, st


def test_morph_tokens_go_through_the_worker_not_this_process(fresh, monkeypatch):
    monkeypatch.setattr(L, "_KIWI", None)
    got = L.morph_tokens(_TEXTS[0])
    assert got == ["상법", "363", "주주", "총회", "소집", "통지"], got
    assert L._KIWI is None, "별도 프로세스가 되는데 이 프로세스에 Kiwi 를 올렸다 — 약 470 MiB 를 다시 잡는다"
    assert fresh.stats()["alive"] is True


def test_child_exits_when_idle_and_comes_back(fresh, monkeypatch):
    monkeypatch.setattr(MW, "_idle_sec", lambda: 1)
    first = fresh.tokenize(_TEXTS[1])
    deadline = time.time() + 15
    while fresh._proc is not None and fresh._proc.poll() is None and time.time() < deadline:
        time.sleep(0.1)
    st = fresh.stats()
    assert st["alive"] is False and st["exits"] == 1, st
    assert fresh.tokenize(_TEXTS[1]) == first
    assert fresh.stats()["spawns"] == 2


def test_spawn_failure_falls_back_to_the_same_answer(fresh, monkeypatch):
    """자식을 못 띄워도 응답은 종전(같은 프로세스 Kiwi)과 같다 — 실패가 결과를 바꾸지 않는다."""
    monkeypatch.setattr(MW.sys, "executable", "/nonexistent/python")
    want = [f for f, t in _inproc(_TEXTS[0]) if (t in L._KIWI_KEEP and len(f) > 1) or t in ("SN", "NR")]
    for _ in range(MW._MAX_FAILURES):
        assert L.morph_tokens(_TEXTS[0]) == want
    st = fresh.stats()
    assert st["fallbacks"] == MW._MAX_FAILURES and st["disabled_reason"], st
    assert st["mode"] == "inprocess", "연달아 실패하면 그 프로세스 동안은 자식을 쓰지 않는다(매번 기다리지 않게)"
    assert L.morph_tokens(_TEXTS[0]) == want and fresh.stats()["fallbacks"] == MW._MAX_FAILURES


def test_child_without_kiwi_disables_at_once(fresh, monkeypatch):
    """자식이 「Kiwi 없음」이라 답하면 한 번에 끈다 — 다시 띄워도 같은 답이다."""
    import subprocess

    def _fake_spawn(self=fresh):
        code = "import json,sys;sys.stdin.readline();print(json.dumps({'err':'unavailable:ModuleNotFoundError'}),flush=True)"
        self._proc = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE)
        self.spawns += 1
        self._served = 0
    monkeypatch.setattr(fresh, "_spawn", _fake_spawn)
    with pytest.raises(MW.MorphWorkerError):
        fresh.tokenize("x")
    st = fresh.stats()
    assert st["spawns"] == 1 and st["disabled_reason"].startswith("child:unavailable"), st


def test_env_switch_keeps_the_old_in_process_path(fresh, monkeypatch):
    monkeypatch.setenv("OPM_MORPH_WORKER", "0")
    want = [f for f, t in _inproc(_TEXTS[1]) if (t in L._KIWI_KEEP and len(f) > 1) or t in ("SN", "NR")]
    assert L.morph_tokens(_TEXTS[1]) == want and want
    assert fresh.stats()["spawns"] == 0 and fresh.stats()["mode"] == "inprocess"


def test_child_gets_no_secrets(fresh, monkeypatch):
    """자식은 키·DB 주소가 필요 없다 — 환경을 최소로 넘기고, 표준 라이브러리를 가리지 않게 -I 로 띄운다."""
    import subprocess
    seen = {}
    real = subprocess.Popen

    def _spy(args, **kw):
        seen["args"], seen["env"] = list(args), dict(kw.get("env") or {})
        return real(args, **kw)
    monkeypatch.setattr(subprocess, "Popen", _spy)
    monkeypatch.setenv("OPENDART_API_KEY", "fixture-not-a-key")
    monkeypatch.setenv("DATABASE_URL", "postgres://fixture")
    fresh.tokenize("이사")
    assert "-I" in seen["args"] and seen["args"][-2] == "--serve", seen["args"]
    assert "OPENDART_API_KEY" not in seen["env"] and "DATABASE_URL" not in seen["env"], sorted(seen["env"])


def test_stop_is_skipped_while_a_request_holds_the_lock(fresh):
    fresh.tokenize("이사")
    with fresh._lock:
        assert fresh.stop() is False
    assert fresh.stop() is True and fresh.stats()["alive"] is False


def test_health_reports_the_worker():
    from starlette.testclient import TestClient
    from open_proxy_mcp.server import mcp
    body = TestClient(mcp.streamable_http_app()).get("/health").json()
    lm = body["law_morph"]
    assert {"mode", "alive", "rss_mb", "idle_exit_sec", "spawns", "fallbacks"} <= set(lm), lm
    assert "pid" not in json.dumps(lm), "자식 PID 는 싣지 않는다"


def test_admin_reclaim_stops_the_child_only_when_it_clears_caches(fresh, monkeypatch):
    """관리자 회수(`/admin/cache`)는 캐시를 비우는 모드(all·pressure)에서만 자식을 내린다 — 가드의 낮은 문턱(`cache=0`)과
    만료분만 비우는 `expired` 는 자식을 둔다(다음 법령 질의가 1~2초 다시 띄우는 값을 치르지 않게)."""
    from starlette.testclient import TestClient
    from open_proxy_mcp import server as S
    monkeypatch.setenv("OPM_ADMIN_KEY", "fixture")
    c, h = TestClient(S.build_app()), {"x-admin-key": "fixture"}
    fresh.tokenize("이사")
    for q in ("cache=0&trim=0", "mode=expired&trim=0"):
        body = c.post(f"/admin/cache?{q}", headers=h).json()
        assert "law_morph_stopped" not in body and fresh.stats()["alive"] is True, (q, body)
    for q in ("mode=pressure&target_mb=1&trim=0", "trim=0"):
        fresh.tokenize("이사")
        body = c.post(f"/admin/cache?{q}", headers=h).json()
        assert body["law_morph_stopped"] is True and fresh.stats()["alive"] is False, (q, body)
    assert fresh.tokenize("이사") == _inproc("이사") and fresh.stats()["spawns"] == 3


def test_child_near_its_idle_exit_is_replaced_before_writing(fresh, monkeypatch):
    """쉬어 끝나기 직전(쉼 한도의 90% 넘게 놀았음)인 자식에게는 쓰지 않고 새로 띄운다 — 자식이 막 끝나는 순간 쓴 요청이
    답 없이 닫히는 경합을 피한다."""
    fresh.tokenize("이사")
    first = fresh._proc
    fresh._last_used -= MW._idle_sec() * 0.95
    assert fresh.tokenize("감사") == _inproc("감사")
    assert fresh._proc is not first and fresh.stats()["spawns"] == 2 and fresh.stats()["fallbacks"] == 0
