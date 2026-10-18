# -*- coding: utf-8 -*-
"""열린 /mcp 요청 장부 — `/health` 의 `open_requests` 와 종료 신호 한 줄 (261019). network 0.

배포 때 한 머신이 「도구 호출이 아닌 POST」(health active_posts 1~3, inflight 0) 때문에 84~90초를 기다렸다 강제 종료됐다.
수만 보여서 그 POST 가 무엇인지 가리지 못했다 — 그래서 나이·HTTP 메서드·경로·JSON-RPC 메서드·단계를 싣는다.
`/health` 는 인증 없는 공개 주소라 **값(쿼리스트링의 사용자 키·인자)은 싣지 않는다** — 그 약속을 여기서 함께 잰다.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from open_proxy_mcp import maintenance as M
from open_proxy_mcp.maintenance import AdmissionMiddleware, maintenance

SECRET = "SECRETKEY0123456789abcdef0123456789abcd"


@pytest.fixture(autouse=True)
def _clean():
    maintenance.open.clear(); maintenance.active_posts = 0; maintenance.set_drain(0)
    yield
    maintenance.open.clear(); maintenance.active_posts = 0; maintenance.set_drain(0)


def _scope(method="POST", path="/mcp", pv=None, qs=f"opendart={SECRET}"):
    headers = [(b"content-type", b"application/json")]
    if pv is not None:
        headers.append((b"mcp-protocol-version", pv.encode()))
    return {"type": "http", "method": method, "path": path, "query_string": qs.encode(), "headers": headers}


def _stream_app(rpc="subscriptions/listen", tool=None):
    """본문을 다 읽고 → 장부에 메서드를 적고 → 응답 머리 + 첫 조각을 보낸 뒤 `finish` 까지 붙드는 앱(SSE 스트림 흉내)."""
    st = {"entered": asyncio.Event(), "finish": asyncio.Event()}

    async def app(scope, receive, send):
        while (await receive()).get("more_body", False):
            pass
        M.note_rpc(scope, rpc, tool)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"event: message\r\n\r\n", "more_body": True})
        st["entered"].set()
        await st["finish"].wait()
        await send({"type": "http.response.body", "body": b"", "more_body": False})
    return app, st


def _drive(scope, app, st, check, body=b'{"jsonrpc":"2.0","id":1,"method":"x"}', disconnect=False):
    async def scenario():
        msgs = [{"type": "http.request", "body": body, "more_body": False}]
        if disconnect:
            msgs.append({"type": "http.disconnect"})

        async def receive():
            return msgs.pop(0) if msgs else await asyncio.Event().wait()

        async def send(_m):
            return None
        task = asyncio.create_task(AdmissionMiddleware(app)(scope, receive, send))
        await st["entered"].wait()
        check()
        st["finish"].set()
        await task
    asyncio.run(scenario())


def test_open_stream_post_is_listed_with_age_method_and_phase_but_no_values():
    app, st = _stream_app()

    def check():
        s = maintenance.open_stats()
        assert s["total"] == 1 and maintenance.active_posts == 1
        o = s["oldest"][0]
        assert (o["method"], o["path"], o["rpc"], o["protocol"], o["phase"], o["status"]) == \
            ("POST", "/mcp", "subscriptions/listen", "2026-07-28", "streaming", 200)
        assert o["age_s"] >= 0 and o["bytes_out"] > 0 and o["client_gone"] is False
        assert s["by_kind"] == {"POST subscriptions/listen streaming": 1}
        dumped = json.dumps(s)
        assert SECRET not in dumped and "opendart" not in dumped, "쿼리스트링 값이 장부에 들어왔다"
    _drive(_scope(pv="2026-07-28"), app, st, check)
    assert maintenance.open_stats()["total"] == 0 and maintenance.active_posts == 0, "끝난 요청이 장부에 남았다"


def test_tool_call_records_tool_name_only_in_its_shape():
    app, st = _stream_app("tools/call", "trading_data")

    def check():
        o = maintenance.open_stats()["oldest"][0]
        assert (o["rpc"], o["tool"]) == ("tools/call", "trading_data")
    _drive(_scope(), app, st, check)


@pytest.mark.parametrize("rpc,tool,want", [
    ("made/up", None, ("other", None)),
    ("tools/call", "회사 이름 원문", ("tools/call", "other")),
    ("tools/call", "x" * 200, ("tools/call", "other")),
    (None, None, (None, None)),
    (42, None, ("other", None)),
])
def test_names_outside_the_fixed_vocabulary_become_other(rpc, tool, want):
    """JSON-RPC 메서드·도구 이름은 클라이언트가 보낸 문자열이다 — 정해진 꼴 밖은 그대로 싣지 않는다."""
    r = M.OpenRequest(_scope())
    r.note_rpc(rpc, tool)
    assert (r.rpc, r.tool) == want


@pytest.mark.parametrize("pv,want", [("2025-06-18", "2025-06-18"), ("2026-07-28", "2026-07-28"),
                                     ("abc", "other"), (SECRET, "other"), (None, None)])
def test_protocol_header_kept_only_in_date_shape(pv, want):
    assert M.OpenRequest(_scope(pv=pv)).pv == want


def test_path_and_method_are_normalised():
    assert M.OpenRequest(_scope(path="/mcp/")).path == "/mcp"
    assert M.OpenRequest(_scope(path=f"/mcp/{SECRET}")).path == "/mcp/*"
    assert M.OpenRequest(_scope(method="BREW")).method == "OTHER"


def test_get_stream_is_listed_but_not_counted_as_active_post():
    app, st = _stream_app(rpc=None)

    def check():
        s = maintenance.open_stats()
        assert s["total"] == 1 and maintenance.active_posts == 0
        assert s["by_kind"] == {"GET - streaming": 1}
    _drive(_scope(method="GET"), app, st, check, body=b"")


def test_client_disconnect_is_visible():
    """클라이언트가 떠났는데도 앱이 안 끝났는지 — 서버 쪽 멈춤과 클라이언트가 붙든 연결을 가르는 칸."""
    st = {"entered": asyncio.Event(), "finish": asyncio.Event()}

    async def app(scope, receive, send):
        await receive()                                  # 본문
        await send({"type": "http.response.start", "status": 200, "headers": []})
        assert (await receive())["type"] == "http.disconnect"
        st["entered"].set()
        await st["finish"].wait()

    def check():
        assert maintenance.open_stats()["oldest"][0]["client_gone"] is True
    _drive(_scope(), app, st, check, disconnect=True)


def test_ledger_is_cleaned_when_the_request_is_cancelled():
    async def scenario():
        app, st = _stream_app()

        async def receive():
            return {"type": "http.request", "body": b"{}", "more_body": False}

        async def send(_m):
            return None
        task = asyncio.create_task(AdmissionMiddleware(app)(_scope(), receive, send))
        await st["entered"].wait()
        assert maintenance.open_stats()["total"] == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert maintenance.open_stats()["total"] == 0 and maintenance.active_posts == 0
    asyncio.run(scenario())


def test_non_mcp_paths_are_not_tracked():
    async def scenario():
        seen = {}

        async def app(scope, receive, send):
            seen["total"] = maintenance.open_stats()["total"]
            seen["key"] = "opm.open" in scope

        async def io(*_a):
            return {}
        await AdmissionMiddleware(app)({"type": "http", "method": "GET", "path": "/health", "headers": []}, io, io)
        assert seen == {"total": 0, "key": False}
    asyncio.run(scenario())


def test_oldest_is_sorted_and_capped():
    rs = []
    for i in range(8):
        r = M.OpenRequest(_scope())
        r.t0 -= i * 10
        maintenance.open[id(r)] = r
        rs.append(r)
    s = maintenance.open_stats()
    assert s["total"] == 8 and len(s["oldest"]) == 5
    ages = [o["age_s"] for o in s["oldest"]]
    assert ages == sorted(ages, reverse=True) and ages[0] >= 70


def test_shutdown_report_is_one_line_without_values():
    r = M.OpenRequest(_scope(pv="2026-07-28"))
    r.note_rpc("subscriptions/listen")
    maintenance.open[id(r)] = r
    line = M.shutdown_report()
    assert "\n" not in line and "subscriptions/listen" in line and "열린 /mcp 요청 1" in line
    assert SECRET not in line and "opendart" not in line


def test_health_carries_open_requests():
    from starlette.testclient import TestClient

    from open_proxy_mcp.server import mcp
    body = TestClient(mcp.streamable_http_app()).get("/health").json()
    assert set(body["open_requests"]) == {"total", "by_kind", "oldest"}


def test_api_key_middleware_notes_the_rpc_method_on_the_real_app(monkeypatch):
    """실제 서빙 앱(`build_app`)에서 ApiKeyMiddleware 가 장부에 메서드를 적는지 — 바깥(Admission)과 안쪽이 같은 장부를 본다."""
    from starlette.testclient import TestClient

    import open_proxy_mcp.server as S
    seen = []
    real = M.OpenRequest.note_rpc

    def spy(self, method, tool=None):
        real(self, method, tool)
        seen.append((self.method, self.rpc, self.tool))
    monkeypatch.setattr(M.OpenRequest, "note_rpc", spy)
    monkeypatch.setenv("OPM_KRX_REFRESHER", "0")
    monkeypatch.setenv("OPM_CORP_REFRESHER", "0")
    app = S.build_app()
    with TestClient(app, base_url="http://localhost:8000") as c:
        r = c.post(f"/mcp?opendart={SECRET}", json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                   headers={"accept": "application/json, text/event-stream", "mcp-protocol-version": "2025-06-18"})
    assert r.status_code == 200, r.text
    assert seen == [("POST", "ping", None)]
    assert maintenance.open_stats()["total"] == 0
