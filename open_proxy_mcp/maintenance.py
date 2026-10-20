"""Short, authenticated drain leases; expiry reopens admission if the operator disappears.

261019: 열린 /mcp 요청 장부도 여기 둔다 — `/health` 의 `open_requests` 와 종료 신호 때 한 줄 기록.
  배포 때 한 머신이 「도구 호출이 아닌 POST」(active_posts 1~3, inflight 0) 하나 때문에 84~90초를 기다렸다 강제 종료됐는데,
  그 POST 가 무엇인지 볼 수단이 수(active_posts)뿐이었다. 그래서 요청마다 나이·HTTP 메서드·경로·JSON-RPC 메서드·단계를 적는다.
  **값은 싣지 않는다** — 쿼리스트링(사용자 DART 키)·인자·IP·User-Agent 는 장부에 들어오지도 않는다. 이름 칸(JSON-RPC 메서드·
  도구 이름·프로토콜 버전)은 정해진 꼴만 받고 나머지는 "other" 로 적는다(`/health` 는 인증 없는 공개 주소다).
"""
import re
import time
import weakref
from collections import Counter


class Maintenance:
    def __init__(self):
        self.until = 0.0
        self.active_posts = 0
        #: 지금 열린 /mcp 요청 — id → `OpenRequest`. AdmissionMiddleware 가 넣고 뺀다.
        self.open: dict[int, "OpenRequest"] = {}

    @property
    def draining(self):
        return time.monotonic() < self.until

    def set_drain(self, seconds):
        self.until = time.monotonic() + seconds if seconds else 0.0

    def stats(self):
        return {"draining": self.draining, "active_posts": self.active_posts,
                "lease_seconds": max(0, round(self.until - time.monotonic(), 1))}

    def open_stats(self, limit: int = 5) -> dict:
        """열린 /mcp 요청 요약 — 총수 · 종류별 수(「HTTP메서드 JSON-RPC메서드 단계」) · 가장 오래된 `limit` 개."""
        now = time.monotonic()
        items = sorted(self.open.values(), key=lambda r: r.t0)
        kinds = Counter(r.kind() for r in items)
        return {"total": len(items),
                "by_kind": dict(kinds.most_common(12)),
                "oldest": [r.view(now) for r in items[:limit]]}


maintenance = Maintenance()


#: `/health` 에 싣는 JSON-RPC 메서드 이름 — MCP 명세에 있는 것만. 그 밖(오타·임의 문자열)은 "other".
_RPC_METHODS = frozenset({
    "initialize", "ping", "server/discover",
    "tools/list", "tools/call", "prompts/list", "prompts/get",
    "resources/list", "resources/templates/list", "resources/read",
    "resources/subscribe", "resources/unsubscribe", "subscriptions/listen",
    "logging/setLevel", "completion/complete",
    "tasks/get", "tasks/list", "tasks/cancel", "tasks/result",
    "notifications/initialized", "notifications/cancelled", "notifications/progress",
    "notifications/roots/list_changed",
})
_HTTP_METHODS = frozenset({"GET", "POST", "DELETE", "PUT", "PATCH", "HEAD", "OPTIONS"})
_PV_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_TOOL_RE = re.compile(r"[a-z][a-z0-9_]{0,47}")
_PV_HEADER = b"mcp-protocol-version"


class OpenRequest:
    """열린 /mcp 요청 하나. 단계(phase):
    body      요청 본문을 받는 중(POST)
    handling  본문을 다 받았고 응답 머리를 아직 안 보냄
    streaming 응답 머리를 보냈고 본문이 아직 안 끝남(SSE 스트림이면 여기서 오래 머문다)
    sent      응답을 다 보냈는데 앱이 아직 안 끝남
    """

    __slots__ = ("t0", "method", "path", "pv", "rpc", "tool", "phase", "status", "bytes_out", "client_gone",
                 "counted")

    def __init__(self, scope):
        self.t0 = time.monotonic()
        m = scope.get("method") or "?"
        self.method = m if m in _HTTP_METHODS else "OTHER"
        p = scope.get("path") or ""
        self.path = "/mcp" if p in ("/mcp", "/mcp/") else "/mcp/*"
        pv = None
        for k, v in scope.get("headers") or ():
            if k.lower() == _PV_HEADER:
                s = v.decode("latin-1").strip()
                pv = s if _PV_RE.fullmatch(s) else "other"
                break
        self.pv = pv
        self.rpc = None
        self.tool = None
        self.phase = "body" if self.method == "POST" else "handling"
        self.status = None
        self.bytes_out = 0
        self.client_gone = False
        #: `maintenance.active_posts` 에 세어 둔 요청인가 — listen 스트림으로 밝혀지면 뺀다(`note_rpc`).
        self.counted = False

    def note_rpc(self, method, tool=None) -> None:
        """본문에서 읽은 JSON-RPC 메서드(·도구 이름)를 적는다 — 정해진 꼴만."""
        if not isinstance(method, str):
            self.rpc = "other" if method is not None else None
            return
        self.rpc = method if method in _RPC_METHODS else "other"
        if method == "tools/call" and isinstance(tool, str):
            self.tool = tool if _TOOL_RE.fullmatch(tool) else "other"
        # 261019: `subscriptions/listen`(2026-07-28 프로토콜)은 일이 아니라 **알림 통로**다 — 예전 GET 대기 스트림의 후신이고,
        #   클라이언트가 끊을 때까지 열려 있다. `active_posts` 는 「진행 중인 일」(드레인 뒤 0 이 되기를 기다리는 수)이라 GET 처럼
        #   여기서도 뺀다. 종전엔 이 스트림 하나가 수를 1 로 붙들어, 메모리 가드의 「한가하면 재시작」이 끝내 한가해지지 않았다.
        #   목록(`open_requests`)에는 그대로 남는다.
        if method == "subscriptions/listen" and self.counted:
            self.counted = False
            maintenance.active_posts -= 1

    def on_receive(self, message) -> None:
        t = message.get("type")
        if t == "http.disconnect":
            self.client_gone = True
        elif t == "http.request" and not message.get("more_body", False) and self.phase == "body":
            self.phase = "handling"

    def on_send(self, message) -> None:
        t = message.get("type")
        if t == "http.response.start":
            self.status = message.get("status")
            self.phase = "streaming"
        elif t == "http.response.body":
            self.bytes_out += len(message.get("body", b"") or b"")
            if not message.get("more_body", False):
                self.phase = "sent"

    def kind(self) -> str:
        return f"{self.method} {self.rpc or '-'} {self.phase}"

    def view(self, now: float) -> dict:
        return {"age_s": round(now - self.t0, 1), "method": self.method, "path": self.path,
                "rpc": self.rpc, "tool": self.tool, "protocol": self.pv, "phase": self.phase,
                "status": self.status, "bytes_out": self.bytes_out, "client_gone": self.client_gone}


def note_rpc(scope, method, tool=None) -> None:
    """안쪽 미들웨어(ApiKeyMiddleware)가 본문을 읽은 뒤 부른다 — 장부가 없으면(시험·/mcp 밖) 아무 일도 안 한다."""
    entry = scope.get("opm.open")
    if entry is not None:
        entry.note_rpc(method, tool)


#: 종료 때 닫을 `subscriptions/listen` 처리기(약한 참조 — 시험이 서버를 여러 번 만들어도 쌓이지 않는다).
_LISTENS: "weakref.WeakSet[DrainableListen]" = weakref.WeakSet()


class DrainableListen:
    """SDK 의 `subscriptions/listen` 처리기(`ListenHandler`)를 감싸 **종료 신호 때 열린 listen 스트림을 정상 종료**시킨다(261019).

    왜 — 2026-07-28 프로토콜 클라이언트는 `subscriptions/listen` POST 를 SSE 로 열어 두고 끊지 않는다. SDK(mcp 2.0.0)는 그
    스트림을 `ListenHandler.close()` 로만 정상 종료하는데 **부르는 곳이 없다**. uvicorn 은 종료 신호 뒤 연결이 닫히기를 기다리고
    (예전 GET 대기 스트림은 sse_starlette 가 신호 때 닫아 주지만 이 POST 스트림은 아니다), fly 는 kill_timeout(90초) 뒤 SIGTERM,
    다시 5초 뒤 VM 을 죽였다 — 배포마다 그 머신이 84~90초를 기다리고 강제 종료됐다(로컬 재현: SIGINT 뒤 「Waiting for connections
    to close」에서 멈춤).

    `close()` — 열린 스트림마다 SDK 의 정상 종료(쌓인 알림을 보내고 마지막 결과 프레임으로 끝냄)를 시작하고, 그 뒤에 들어오는 listen 도
    응답(확인 프레임) 직후 같은 방식으로 끝낸다(클라이언트는 다른 머신에 다시 listen 한다). OPM 은 목록 변경 알림을 내지 않으므로
    스트림을 닫아도 잃는 알림이 없다.
    """

    def __init__(self, inner):
        self._inner = inner
        self.closing = False
        _LISTENS.add(self)

    async def __call__(self, ctx, params):
        if self.closing:
            # 이 listen 도 등록되자마자 닫히게 한 칸 뒤에 close 를 건다 — 확인 프레임을 보내는 await 에서 돈다.
            import asyncio
            asyncio.get_running_loop().call_soon(self._inner.close)
        return await self._inner(ctx, params)

    def close(self) -> None:
        self.closing = True
        self._inner.close()


def install_listen_drain(server) -> "DrainableListen | None":
    """서버의 `subscriptions/listen` 처리기를 `DrainableListen` 으로 감싼다. SDK 가 바뀌어 처리기를 못 찾으면 None
    (시험 `test_listen_drain_is_installed_on_the_served_app` 이 그 경우를 잡는다)."""
    low = getattr(server, "_lowlevel_server", None)
    get = getattr(low, "get_request_handler", None)
    entry = get("subscriptions/listen") if get else None
    inner = getattr(entry, "handler", None)
    if inner is None or not callable(getattr(inner, "close", None)):
        return None
    if isinstance(inner, DrainableListen):
        return inner
    wrapped = DrainableListen(inner)
    low.add_request_handler("subscriptions/listen", entry.params_type, wrapped)
    return wrapped


def close_listen_streams() -> int:
    """종료 신호 때 부른다 — 열린 listen 스트림을 정상 종료하고, 감싼 처리기 수를 돌려준다."""
    n = 0
    for h in list(_LISTENS):
        try:
            h.close()
            n += 1
        except Exception:                 # noqa: BLE001 — 이것 때문에 종료가 막히면 안 된다
            pass
    return n


def shutdown_report(limit: int = 10) -> str:
    """종료 신호 때 stderr(=fly logs)에 남길 한 줄 — 열린 요청이 무엇이었나. 값·키 없음."""
    import json
    s = maintenance.open_stats(limit)
    return "종료 신호: 열린 /mcp 요청 %d — %s" % (s["total"], json.dumps(
        {"by_kind": s["by_kind"], "oldest": s["oldest"]}, ensure_ascii=False, separators=(",", ":")))


class AdmissionMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        is_mcp = scope["type"] == "http" and scope.get("path", "").startswith("/mcp")
        if is_mcp and maintenance.draining:
            from starlette.responses import JSONResponse
            await JSONResponse({"error": "temporarily draining; retry shortly"},
                               status_code=503, headers={"Retry-After": "5"})(scope, receive, send)
            return
        if not is_mcp:
            await self.app(scope, receive, send)
            return
        tracked = scope.get("method") == "POST"
        entry = OpenRequest(scope)
        scope["opm.open"] = entry
        key = id(entry)
        maintenance.open[key] = entry

        async def receive_seen():
            message = await receive()
            entry.on_receive(message)
            return message

        async def send_seen(message):
            entry.on_send(message)
            await send(message)

        if tracked:
            maintenance.active_posts += 1
            entry.counted = True
        try:
            await self.app(scope, receive_seen, send_seen)
        finally:
            maintenance.open.pop(key, None)
            if entry.counted:
                maintenance.active_posts -= 1
