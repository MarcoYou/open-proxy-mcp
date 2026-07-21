"""OpenProxy MCP 서버 — FastMCP 진입점"""

import argparse
import os
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from open_proxy_mcp.tools import register_all_tools
from open_proxy_mcp.tools_v2 import register_all_tools_v2


def build_mcp(toolset: str) -> FastMCP:
    """toolset별 MCP 인스턴스 생성."""

    mcp = FastMCP(
        "open-proxy-mcp",
        instructions=(
            "Korean-listed company disclosure (DART) analysis server. Covers financials, "
            "valuation, ownership structure, AGM proxy voting, and asset-holdings screening. "
            "Start with the `company` tool to resolve a company name or ticker. Natural-language "
            "questions work fine — you don't need to know tool names."
        ),
    )
    if toolset == "v2":
        register_all_tools_v2(mcp)
    elif toolset == "hybrid":
        register_all_tools(mcp)
        register_all_tools_v2(mcp)
    else:
        register_all_tools(mcp)
    return mcp


mcp = build_mcp(os.environ.get("OPEN_PROXY_TOOLSET", "v1"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
    )
    parser.add_argument(
        "--sse",
        action="store_true",
        help="하위호환 옵션: --transport sse 와 동일",
    )
    parser.add_argument(
        "--toolset",
        choices=["v1", "v2", "hybrid"],
        default=os.environ.get("OPEN_PROXY_TOOLSET", "v1"),
    )
    args = parser.parse_args()
    if args.sse:
        args.transport = "sse"
    mcp = build_mcp(args.toolset)

    if args.transport in ("sse", "streamable-http"):
        mcp.settings.host = os.environ.get("FASTMCP_HOST", "0.0.0.0")
        mcp.settings.port = int(os.environ.get("FASTMCP_PORT", "8000"))
        allowed_hosts = [
            "open-proxy-mcp.fly.dev",
            "localhost:8000",
            "127.0.0.1:8000",
            "0.0.0.0:8000",
        ]
        extra_hosts = os.environ.get("FASTMCP_ALLOWED_HOSTS", "").strip()
        if extra_hosts:
            allowed_hosts.extend([h.strip() for h in extra_hosts.split(",") if h.strip()])
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
        )

    if args.transport == "streamable-http":
        # 무상태 HTTP: 각 요청이 독립(세션 in-memory 미보관) → fly 다중 머신에서 라우팅이
        # 갈려도 "Session not found" 없음. OPM tool은 무상태(요청마다 키·파라미터 자급)라
        # 세션 유지 불필요. 2머신 유지하면서 세션 어피니티 문제 해결. (2026-06)
        mcp.settings.stateless_http = True
        mcp.settings.json_response = True

        import uvicorn
        from starlette.middleware import Middleware
        from starlette.types import ASGIApp, Receive, Scope, Send
        from open_proxy_mcp.dart.client import set_request_api_key
        from open_proxy_mcp import usage

        def _extract_tool(body: bytes):
            """JSON-RPC 본문에서 호출 대상 추출 → (이름, tools/call 여부).
            tools/call이면 tool명, 아니면 method명."""
            try:
                import json
                d = json.loads(body)
                method = d.get("method", "")
                if method == "tools/call":
                    return d.get("params", {}).get("name") or "tools/call", True
                return method or None, False
            except Exception:
                return None, False

        _ERR_PATTERNS = (b'"isError":true', b'"isError": true',
                         b'"error":{"code"', b'"error": {"code"')
        import re as _re
        _EKIND_RE = _re.compile(rb"\[ekind=(\w+)\]")  # tools_v2 래퍼가 붙인 error_kind 태그

        class ApiKeyMiddleware:
            """URL 쿼리 파라미터 ?opendart=키 → contextvar 세팅 + 사용 통계 기록."""

            def __init__(self, app: ASGIApp):
                self.app = app

            async def __call__(self, scope: Scope, receive: Receive, send: Send):
                if scope["type"] != "http":
                    await self.app(scope, receive, send)
                    return
                from urllib.parse import parse_qs
                qs = parse_qs(scope.get("query_string", b"").decode())
                opendart = qs.get("opendart", [None])[0]
                if opendart:
                    set_request_api_key(opendart)
                elif scope.get("path", "").startswith("/mcp"):
                    # 키 없는 서빙 요청 거절(260705) — fly secrets에 서버용 OPENDART 키(배치·DB
                    # 갱신 내부용)가 있으므로, 거절하지 않으면 env 폴백으로 서버 키가 조용히
                    # 소모된다. 서빙은 반드시 유저 키(?opendart=)로.
                    import json as _json
                    body = _json.dumps({"error": "opendart API key required",
                                        "hint": "connect with ?opendart=<your DART key>"}).encode()
                    await send({"type": "http.response.start", "status": 401,
                                "headers": [(b"content-type", b"application/json")]})
                    await send({"type": "http.response.body", "body": body})
                    return

                # 사용 통계 기록 (요청 1건 = 이벤트 1건). 기록은 비동기 큐라 지연 0.
                # 요청 본문(JSON-RPC)을 버퍼링해 tool명 추출 후 그대로 앱에 재생(replay).
                if opendart and scope.get("path", "").startswith("/mcp"):
                    import time as _t
                    start = _t.monotonic()
                    buffered = []
                    body = b""
                    more = True
                    while more:
                        msg = await receive()
                        buffered.append(msg)
                        if msg["type"] == "http.request":
                            body += msg.get("body", b"")
                            more = msg.get("more_body", False)
                        else:
                            more = False
                    tool, is_call = _extract_tool(body)

                    idx = 0
                    async def replay():
                        nonlocal idx
                        if idx < len(buffered):
                            m = buffered[idx]; idx += 1; return m
                        return await receive()

                    # tools/call은 응답 본문(SSE/JSON)에서 isError를 스캔한 뒤 본문 종료 시 기록.
                    # (툴 내부 실패는 HTTP 200에 실려 오므로 status만으론 못 잡음)
                    # 그 외(핸드셰이크 등)는 기존대로 응답 시작 시 기록.
                    rec = {"status": 0, "latency": None, "err": False, "tail": b"",
                           "done": False, "ekind": None}

                    async def send_wrapper(message):
                        if message["type"] == "http.response.start":
                            rec["latency"] = int((_t.monotonic() - start) * 1000)
                            rec["status"] = message.get("status", 0)
                            if not is_call:
                                usage.record(opendart, rec["status"], tool, rec["latency"])
                        elif message["type"] == "http.response.body" and is_call and not rec["done"]:
                            chunk = message.get("body", b"") or b""
                            if chunk and (not rec["err"] or rec["ekind"] is None):
                                hay = rec["tail"] + chunk
                                if not rec["err"]:
                                    rec["err"] = any(p in hay for p in _ERR_PATTERNS)
                                if rec["ekind"] is None:
                                    m = _EKIND_RE.search(hay)
                                    if m:
                                        rec["ekind"] = m.group(1).decode()
                                rec["tail"] = hay[-64:]  # 태그(~16B)가 청크 경계에 안 잘리게
                            if not message.get("more_body", False):
                                rec["done"] = True
                                # 오류일 때만 error_kind 기록. 태그 없는 오류(인자검증·프로토콜·비래핑
                                # 경로)는 "untagged" sentinel → 배포前 NULL과 배포後 분류실패를 구분.
                                # 성공(not err)은 본문에 우연히 [ekind=]가 있어도 None으로 기록.
                                ekind = (rec["ekind"] or "untagged") if rec["err"] else None
                                usage.record(opendart, rec["status"], tool, rec["latency"],
                                             is_error=rec["err"], error_kind=ekind)
                        await send(message)
                    await self.app(scope, replay, send_wrapper)
                else:
                    await self.app(scope, receive, send)

        app = mcp.streamable_http_app()
        app.add_middleware(ApiKeyMiddleware)

        uvicorn.run(
            app,
            host=mcp.settings.host,
            port=mcp.settings.port,
        )
    else:
        mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
