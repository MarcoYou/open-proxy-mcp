# -*- coding: utf-8 -*-
"""종료 신호 때 `subscriptions/listen` 스트림을 정상 종료한다 (261019). 외부 network 0 — 127.0.0.1 임시 포트만 쓴다.

운영: 배포 때 한 머신의 옛 프로세스가 도구 호출이 아닌 POST 하나 때문에 「Waiting for connections to close」로 84~90초를 버티다
fly 의 SIGTERM 뒤 VM 째로 죽었다. 그 POST 는 2026-07-28 프로토콜 클라이언트의 `subscriptions/listen` — 응답 자체가 알림 스트림이라
클라이언트가 끊을 때까지 열려 있고, SDK(mcp 2.0.0)는 그것을 닫는 `ListenHandler.close()` 를 부르지 않는다. 로컬 재현: 이 스트림 하나를
열고 SIGINT 를 주면 서버가 멈춘 채 남고, 두 번째 신호가 SIGTERM 이면 그래도 멈춰 있다(uvicorn 은 두 번째 SIGINT 만 강제 종료로 본다).
"""
from __future__ import annotations

import asyncio
import http.client
import json
import signal
import socket
import threading
import time

import pytest

from open_proxy_mcp import maintenance as M
from open_proxy_mcp.maintenance import DrainableListen, maintenance

_META = {"io.modelcontextprotocol/protocolVersion": "2026-07-28",
         "io.modelcontextprotocol/clientInfo": {"name": "t", "version": "1"},
         "io.modelcontextprotocol/clientCapabilities": {}}
_KEY = "0" * 40


@pytest.fixture(autouse=True)
def _clean():
    maintenance.open.clear(); maintenance.active_posts = 0; maintenance.set_drain(0)
    yield
    maintenance.open.clear(); maintenance.active_posts = 0; maintenance.set_drain(0)
    for h in list(M._LISTENS):        # 시험이 닫은 처리기를 되돌린다 — 모듈 레벨 서버도 같은 목록에 있다
        h.closing = False


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _open_listen(port: int) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    body = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "subscriptions/listen",
                       "params": {"notifications": {"toolsListChanged": True}, "_meta": _META}})
    c.request("POST", f"/mcp?opendart={_KEY}", body=body,
              headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                       "mcp-protocol-version": "2026-07-28", "Mcp-Method": "subscriptions/listen"})
    return c, c.getresponse()


def _read_frames(r: http.client.HTTPResponse, until: str, limit_s: float = 10) -> list[str]:
    out, t0 = [], time.monotonic()
    while time.monotonic() - t0 < limit_s:
        line = r.fp.readline()
        if not line:
            out.append("<EOF>")
            break
        s = line.decode("utf-8", "replace").strip()
        if s.startswith("data:"):
            out.append(s)
            if until in s:
                break
    return out


@pytest.fixture
def live_server(monkeypatch):
    """실제 서빙 앱(`build_app`) + 종료 신호를 아는 서버 클래스를 스레드에서 띄운다. 신호는 서버 루프 안에서 부른다(운영과 같은 자리)."""
    import uvicorn

    import open_proxy_mcp.server as S
    from open_proxy_mcp import usage
    monkeypatch.setenv("OPM_KRX_REFRESHER", "0")
    monkeypatch.setenv("OPM_CORP_REFRESHER", "0")
    monkeypatch.setattr(usage, "begin_shutdown", lambda: None)
    port = _free_port()
    monkeypatch.setenv("FASTMCP_ALLOWED_HOSTS", f"127.0.0.1:{port}")
    app = S.build_app()
    srv = S._shutdown_aware_server_class()(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    box: dict = {}

    async def main():
        box["loop"] = asyncio.get_running_loop()
        await srv.serve()

    t = threading.Thread(target=lambda: asyncio.run(main()), daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    assert srv.started, "시험 서버가 뜨지 않았다"

    def signal_(sig):
        box["loop"].call_soon_threadsafe(srv.handle_exit, sig, None)
    yield port, srv, t, signal_
    srv.should_exit = srv.force_exit = True
    t.join(10)


def test_open_listen_stream_ends_gracefully_on_shutdown_signal(live_server):
    port, srv, thread, signal_ = live_server
    conn, r = _open_listen(port)
    assert r.status == 200 and "text/event-stream" in (r.getheader("content-type") or "")
    ack = _read_frames(r, "subscriptions/acknowledged")
    assert ack and "subscriptions/acknowledged" in ack[-1], ack

    # 열린 listen 은 목록에는 보이되 「진행 중인 일」 수에는 안 든다(메모리 가드의 한가함 판정이 이 수를 0 으로 기다린다).
    s = maintenance.open_stats()
    assert s["by_kind"] == {"POST subscriptions/listen streaming": 1}, s
    assert maintenance.active_posts == 0

    t0 = time.monotonic()
    signal_(signal.SIGINT)
    tail = _read_frames(r, '"resultType"')
    thread.join(10)
    took = time.monotonic() - t0
    assert not thread.is_alive(), "종료 신호 뒤에도 서버가 listen 스트림을 기다리며 남아 있다"
    assert took < 5, f"종료에 {took:.1f}초 — 열린 listen 이 종료를 붙들었다"
    # 정상 종료: 끊김이 아니라 같은 요청 id 의 결과 프레임으로 끝난다(클라이언트는 다른 머신에 다시 listen 한다).
    assert any('"id":7' in f and '"result"' in f for f in tail), tail
    conn.close()


def test_listen_drain_is_installed_on_the_served_app():
    """SDK 가 바뀌어 처리기를 못 찾으면 감싸기가 조용히 빠진다 — 그 경우를 여기서 잡는다."""
    import open_proxy_mcp.server as S
    for server in (S.mcp, S.build_mcp()):
        entry = server._lowlevel_server.get_request_handler("subscriptions/listen")
        assert isinstance(entry.handler, DrainableListen), type(entry.handler)
    assert M.install_listen_drain(S.mcp) is S.mcp._lowlevel_server.get_request_handler("subscriptions/listen").handler, \
        "두 번 감쌌다"


def test_listen_arriving_after_close_is_ended_right_after_its_ack():
    """종료가 시작된 뒤 들어온 listen 도 붙들지 않는다 — 등록되자마자 다음 칸에서 닫힌다."""
    class Inner:
        def __init__(self):
            self.closed = 0

        def close(self):
            self.closed += 1

        async def __call__(self, ctx, params):
            await asyncio.sleep(0)            # 확인 프레임을 보내는 await 자리
            await asyncio.sleep(0)
            return self.closed

    inner = Inner()
    w = DrainableListen(inner)
    assert asyncio.run(w(None, None)) == 0, "닫기 전에는 그대로 통과해야 한다"
    w.close()
    assert inner.closed == 1 and w.closing
    assert asyncio.run(w(None, None)) >= 2, "닫힌 뒤 들어온 listen 이 닫히지 않았다"


def test_listen_post_is_not_an_active_post_but_tool_calls_are():
    r = M.OpenRequest({"type": "http", "method": "POST", "path": "/mcp", "headers": []})
    maintenance.active_posts = 1; r.counted = True
    r.note_rpc("subscriptions/listen")
    assert maintenance.active_posts == 0 and r.counted is False
    r.note_rpc("subscriptions/listen")                       # 두 번 빼지 않는다
    assert maintenance.active_posts == 0
    t = M.OpenRequest({"type": "http", "method": "POST", "path": "/mcp", "headers": []})
    maintenance.active_posts = 1; t.counted = True
    t.note_rpc("tools/call", "company")
    assert maintenance.active_posts == 1 and t.counted


def test_second_signal_sigterm_forces_exit_like_a_second_sigint():
    """fly 는 SIGINT → kill_timeout 뒤 SIGTERM → 5초 뒤 VM 강제 종료. uvicorn 은 두 번째 SIGINT 만 강제 종료로 봐서, SIGTERM 이 와도
    기다리다 VM 째로 죽었다. 첫 신호가 SIGTERM 이면 정상 종료로 시작한다."""
    import uvicorn

    import open_proxy_mcp.server as S
    first_term = S._shutdown_aware_server_class()(uvicorn.Config(lambda *a: None))
    first_term.handle_exit(signal.SIGTERM, None)
    assert first_term.should_exit and not first_term.force_exit
    srv = S._shutdown_aware_server_class()(uvicorn.Config(lambda *a: None))
    srv.handle_exit(signal.SIGINT, None)
    assert srv.should_exit and not srv.force_exit
    srv.handle_exit(signal.SIGTERM, None)
    assert srv.force_exit


def test_shutdown_signal_closes_listen_streams(monkeypatch):
    import uvicorn

    import open_proxy_mcp.server as S
    from open_proxy_mcp import usage
    calls = []
    monkeypatch.setattr(usage, "begin_shutdown", lambda: calls.append("usage"))
    monkeypatch.setattr(M, "close_listen_streams", lambda: calls.append("listen") or 1)
    S._shutdown_aware_server_class()(uvicorn.Config(lambda *a: None)).handle_exit(signal.SIGINT, None)
    assert calls == ["usage", "listen"]


def _held_stream_server(monkeypatch):
    """끝나지 않는 스트리밍 응답을 내는 최소 앱 + 종료 신호를 아는 서버 — listen 이 아닌 「무엇이든 안 닫히는 연결」의 대역."""
    import uvicorn

    import open_proxy_mcp.server as S
    from open_proxy_mcp import usage
    monkeypatch.setattr(usage, "begin_shutdown", lambda: None)
    monkeypatch.setattr(M, "close_listen_streams", lambda: 0)

    async def app(scope, receive, send):
        if scope["type"] == "lifespan":
            while True:
                m = await receive()
                if m["type"] == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif m["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/event-stream")]})
        await send({"type": "http.response.body", "body": b"data: hi\n\n", "more_body": True})
        await asyncio.Event().wait()      # 클라이언트도 서버도 닫지 않는 스트림

    port = _free_port()
    srv = S._shutdown_aware_server_class()(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    box: dict = {}

    async def main():
        box["loop"] = asyncio.get_running_loop()
        await srv.serve()

    t = threading.Thread(target=lambda: asyncio.run(main()), daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    assert srv.started
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    c.request("GET", "/")
    r = c.getresponse()
    assert r.readline().startswith(b"data:")       # 청크 인코딩을 푸는 쪽(r.fp 는 청크 길이 줄이 먼저 온다)
    return srv, t, (lambda sig: box["loop"].call_soon_threadsafe(srv.handle_exit, sig, None)), c


@pytest.mark.parametrize("second", [signal.SIGTERM, signal.SIGINT], ids=["SIGTERM", "SIGINT"])
def test_forced_exit_actually_ends_the_process_while_a_connection_stays_open(monkeypatch, second):
    """261019 로컬 실측: force_exit 만 세우면 uvicorn 이 그다음 `asyncio.Server.wait_closed()`(파이썬 3.12 — 열린 연결이 다 닫힐 때까지)
    에서 멈춰, 두 번째 신호를 받고도 프로세스가 끝나지 않았다(운영에선 5초 뒤 VM 강제 종료). 강제 종료면 남은 연결을 끊어 끝나야 한다."""
    srv, t, signal_, conn = _held_stream_server(monkeypatch)
    try:
        signal_(signal.SIGINT)
        t.join(1.0)
        assert t.is_alive(), "첫 신호는 정상 종료 — 열린 연결을 기다려야 한다(진행 중인 도구 호출을 끊지 않는다)"
        t0 = time.monotonic()
        signal_(second)
        t.join(5)
        assert not t.is_alive(), f"두 번째 신호({second.name}) 뒤 5초가 지나도 서버가 열린 연결을 기다리며 남아 있다"
        assert srv.force_exit and time.monotonic() - t0 < 3
    finally:
        srv.should_exit = srv.force_exit = True
        conn.close()
        t.join(5)
