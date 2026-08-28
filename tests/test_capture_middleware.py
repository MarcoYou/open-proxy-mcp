"""도구 응답 보관 — 260828 신설. network 0콜·DB 0콜.

계기: 시험자 세션 기록에 **도구가 실제로 준 응답이 한 줄도 없었다.** 남은 것은 AI 의
답변뿐이라, 「우리가 못 준 것을 그 AI 가 자기 지식으로 그럴듯하게 메웠는지」를 대조할
왼쪽이 아예 없었다. 채점 자체가 불가능한 상태였다.

이 파일이 지키는 계약은 넷이다.
  ① 환경변수가 없으면 **아무 일도 일어나지 않는다**(운영 = fly 에는 그 변수가 없다)
  ② 남기는 것은 `tools/call` 뿐 — 핸드셰이크·목록 조회는 잡음이다
  ③ `response_text` 를 **자르지 않는다** — 자르면 대조가 무의미해진다
  ④ 기록이 실패해도 **서빙은 통과한다** — 기록은 응답을 막을 자격이 없다
"""
from __future__ import annotations

import json
import os

import pytest

from open_proxy_mcp.capture import CaptureMiddleware, _mask, _parse_response


# ── 가짜 ASGI 왕복 ────────────────────────────────────────────────────
def _rpc(name="proxy_advise_before_meeting", **args):
    return json.dumps({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": args or {"corp": "대림제지"}},
    }).encode()


def _jsonrpc_result(text, is_error=False):
    return json.dumps({
        "jsonrpc": "2.0", "id": 1,
        "result": {"content": [{"type": "text", "text": text}], "isError": is_error},
    }).encode()


def _run(body: bytes, response: bytes, *, path="/mcp", method="POST",
         ctype=b"application/json", chunks=1):
    """미들웨어를 실제 ASGI 인터페이스로 한 번 왕복시키고 클라이언트가 받은 바이트를 돌려준다."""
    import asyncio

    async def app(scope, receive, send):
        while True:                      # 하류는 본문을 온전히 받아야 한다(replay 계약)
            msg = await receive()
            if not msg.get("more_body", False):
                break
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", ctype)]})
        size = max(1, len(response) // chunks + 1)
        parts = [response[i:i + size] for i in range(0, len(response), size)] or [b""]
        for i, part in enumerate(parts):
            await send({"type": "http.response.body", "body": part,
                        "more_body": i < len(parts) - 1})

    seen: list[bytes] = []

    async def send(message):
        if message["type"] == "http.response.body":
            seen.append(message.get("body", b""))

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    scope = {"type": "http", "method": method, "path": path, "headers": []}
    asyncio.run(CaptureMiddleware(app)(scope, receive, send))
    return b"".join(seen)


def _lines(d):
    out = []
    for fn in sorted(os.listdir(d)):
        with open(os.path.join(d, fn), encoding="utf-8") as f:
            out += [json.loads(ln) for ln in f if ln.strip()]
    return out


@pytest.fixture
def capture_dir(tmp_path, monkeypatch):
    d = tmp_path / "captures"
    monkeypatch.setenv("OPM_CAPTURE_DIR", str(d))
    return d


# ── ① 기본은 꺼짐 ─────────────────────────────────────────────────────
def test_no_env_no_files(tmp_path, monkeypatch):
    """★ 운영 계약. fly 에는 이 변수가 없으니 파일도 오버헤드도 없어야 한다."""
    monkeypatch.delenv("OPM_CAPTURE_DIR", raising=False)
    d = tmp_path / "captures"
    body = _run(_rpc(), _jsonrpc_result("본문"))
    assert body == _jsonrpc_result("본문")     # 서빙은 그대로
    assert not d.exists()


def test_empty_env_counts_as_off(tmp_path, monkeypatch):
    """공백만 든 값은 없는 것으로 친다 — 파이썬에서 " " 는 참이라 그냥 두면 켜진다."""
    monkeypatch.setenv("OPM_CAPTURE_DIR", "   ")
    _run(_rpc(), _jsonrpc_result("본문"))
    assert not (tmp_path / "captures").exists()


# ── ② tools/call 만 ───────────────────────────────────────────────────
def test_records_a_tool_call(capture_dir):
    _run(_rpc("company", corp="삼성전자"), _jsonrpc_result("# company\n\n삼성전자"))
    rows = _lines(capture_dir)
    assert len(rows) == 1
    r = rows[0]
    assert r["tool"] == "company"
    assert r["arguments"] == {"corp": "삼성전자"}
    assert r["response_text"] == "# company\n\n삼성전자"
    assert r["is_error"] is False
    assert r["bytes"] > 0 and r["duration_ms"] >= 0
    assert r["ts"].startswith("20")


@pytest.mark.parametrize("method", ["initialize", "tools/list", "ping", "notifications/initialized"])
def test_handshake_is_not_recorded(capture_dir, method):
    """잡음을 남기면 채점기가 그것부터 걸러야 한다. 애초에 안 적는다."""
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method}).encode()
    _run(body, b'{"jsonrpc":"2.0","id":1,"result":{}}')
    assert not capture_dir.exists() or _lines(capture_dir) == []


def test_get_stream_is_not_recorded(capture_dir):
    """streamable-http 클라이언트는 `GET /mcp` 으로 스트림을 세션 내내 붙들고 있다."""
    _run(b"", b"", method="GET")
    assert not capture_dir.exists() or _lines(capture_dir) == []


def test_non_mcp_path_untouched(capture_dir):
    _run(b"", b'{"status":"ok"}', path="/health", method="GET")
    assert not capture_dir.exists() or _lines(capture_dir) == []


# ── ③ 전문을 자르지 않는다 ────────────────────────────────────────────
def test_a_long_response_is_not_truncated(capture_dir):
    """★ 이 파일의 목적. `proxy_advise_before_meeting` 은 수만 자를 낸다 — 잘리면
    「AI 가 지어냈나」를 판별할 수 없다(잘린 자리가 곧 지어낼 자리가 된다)."""
    text = "".join(f"{i}행 의안 원문 가나다\n" for i in range(20000))
    assert len(text) > 200_000
    _run(_rpc(), _jsonrpc_result(text), chunks=97)   # 청크 경계를 일부러 많이 만든다
    r = _lines(capture_dir)[0]
    assert r["response_text"] == text


def test_multiple_blocks_are_joined_and_non_text_is_flagged(capture_dir):
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"content": [
        {"type": "text", "text": "앞"},
        {"type": "image", "data": "..."},
        {"type": "text", "text": "뒤"},
    ]}}).encode()
    _run(_rpc(), payload)
    assert _lines(capture_dir)[0]["response_text"] == "앞\n[non-text content block: image]\n뒤"


# ── 오류도 남긴다 ─────────────────────────────────────────────────────
def test_tool_error_is_recorded(capture_dir):
    _run(_rpc(), _jsonrpc_result("[ekind=not_found] 회사를 못 찾았다", is_error=True))
    r = _lines(capture_dir)[0]
    assert r["is_error"] is True
    assert "회사를 못 찾았다" in r["response_text"]


def test_protocol_error_is_recorded(capture_dir):
    payload = b'{"jsonrpc":"2.0","id":1,"error":{"code":-32602,"message":"bad args"}}'
    _run(_rpc(), payload)
    r = _lines(capture_dir)[0]
    assert r["is_error"] is True
    assert "bad args" in r["response_text"]


def test_unreadable_payload_keeps_the_raw_bytes(capture_dir):
    """못 읽었다고 빈칸으로 두지 않는다 — 「응답이 비었다」와 구분되지 않게 된다."""
    _run(_rpc(), b"<html>502 Bad Gateway</html>")
    assert "502 Bad Gateway" in _lines(capture_dir)[0]["response_text"]


def test_sse_transport_is_read_too(capture_dir):
    """지금은 json_response=True 지만, SSE 로 바뀌어도 기록이 조용히 비면 안 된다."""
    payload = b"event: message\ndata: " + _jsonrpc_result("SSE 본문") + b"\n\n"
    _run(_rpc(), payload, ctype=b"text/event-stream")
    assert _lines(capture_dir)[0]["response_text"] == "SSE 본문"


# ── 이어쓰기 ──────────────────────────────────────────────────────────
def test_appends_never_overwrites(capture_dir):
    for i in range(5):
        _run(_rpc("company", corp=f"회사{i}"), _jsonrpc_result(f"본문{i}"))
    rows = _lines(capture_dir)
    assert [r["response_text"] for r in rows] == [f"본문{i}" for i in range(5)]
    assert len(os.listdir(capture_dir)) == 1        # 날짜별 파일 하나
    assert os.listdir(capture_dir)[0].startswith("calls-")


# ── ④ 기록은 서빙을 막지 않는다 ───────────────────────────────────────
def test_a_write_failure_does_not_break_serving(tmp_path, monkeypatch):
    """★ 디렉터리를 못 만드는 자리로 지정해도 응답은 온전히 나가야 한다."""
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("나는 파일이다")
    monkeypatch.setenv("OPM_CAPTURE_DIR", str(blocker / "captures"))
    import open_proxy_mcp.capture as cap
    monkeypatch.setattr(cap, "_warned", False)
    out = _run(_rpc(), _jsonrpc_result("본문"))
    assert out == _jsonrpc_result("본문")
    assert cap._warned is True          # 한 번은 경고했다


def test_the_warning_is_printed_only_once(tmp_path, monkeypatch, caplog):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    monkeypatch.setenv("OPM_CAPTURE_DIR", str(blocker / "captures"))
    import open_proxy_mcp.capture as cap
    monkeypatch.setattr(cap, "_warned", False)
    with caplog.at_level("WARNING", logger="open_proxy_mcp.capture"):
        for _ in range(20):
            _run(_rpc(), _jsonrpc_result("본문"))
    assert len(caplog.records) == 1     # 매 호출 찍으면 진짜 신호가 묻힌다


# ── 키를 남기지 않는다 ────────────────────────────────────────────────
def test_keys_in_urls_are_masked(capture_dir):
    url = "https://opendart.fs.or.kr/api/x.xml?crtfc_key=deadbeefdeadbeef&rcept_no=1"
    _run(_rpc(), _jsonrpc_result(f"근거: {url}"))
    raw = (capture_dir / os.listdir(capture_dir)[0]).read_text(encoding="utf-8")
    assert "deadbeefdeadbeef" not in raw
    assert "crtfc_key=***" in raw


def test_mask_covers_both_key_names():
    assert _mask("?opendart=abc123&x=1") == "?opendart=***&x=1"
    assert _mask("crtfc_key=abc123") == "crtfc_key=***"


# ── 하류는 온전한 본문을 받는다 ───────────────────────────────────────
def test_downstream_still_gets_the_whole_request(capture_dir):
    """버퍼링한 뒤 replay 로 되돌려주므로 하류가 읽는 본문이 달라지면 안 된다."""
    import asyncio
    body = _rpc("company", corp="삼성전자")
    got = {}

    async def app(scope, receive, send):
        acc = b""
        while True:
            m = await receive()
            acc += m.get("body", b"")
            if not m.get("more_body", False):
                break
        got["body"] = acc
        await send({"type": "http.response.start", "status": 200,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": _jsonrpc_result("ok"),
                    "more_body": False})

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        pass

    scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": []}
    asyncio.run(CaptureMiddleware(app)(scope, receive, send))
    assert got["body"] == body


# ── 프로덕션 앱에 실제로 붙어 있나 ────────────────────────────────────
def test_the_middleware_is_wired_into_the_served_app():
    """260729 사고의 교훈 — 「붙였다」가 아니라 **서빙되는 객체에** 붙었나를 본다."""
    from open_proxy_mcp.server import build_app
    from open_proxy_mcp.server import ApiKeyMiddleware
    app = build_app()
    cls = [m.cls for m in app.user_middleware]
    assert CaptureMiddleware in cls
    # 키 게이트가 바깥이어야 401 이 기록에 섞이지 않는다(add_middleware 는 앞에 끼운다).
    assert cls.index(ApiKeyMiddleware) < cls.index(CaptureMiddleware)
