"""OpenProxy MCP 서버 — FastMCP 진입점"""

import argparse
import os
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from open_proxy_mcp.tools import register_all_tools
from open_proxy_mcp.tools_v2 import register_all_tools_v2


def build_mcp(toolset: str) -> FastMCP:
    """toolset별 MCP 인스턴스 생성."""

    mcp = FastMCP("open-proxy-mcp")
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
        mcp.settings.transport_security = TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[
                "open-proxy-mcp.fly.dev",
                "localhost:8000",
                "127.0.0.1:8000",
                "0.0.0.0:8000",
            ],
        )

    if args.transport == "streamable-http":
        import uvicorn
        from starlette.middleware import Middleware
        from starlette.types import ASGIApp, Receive, Scope, Send
        from open_proxy_mcp.dart.client import set_request_api_key

        class ApiKeyMiddleware:
            """URL 쿼리 파라미터 ?opendart=키 → contextvar 세팅"""

            def __init__(self, app: ASGIApp):
                self.app = app

            async def __call__(self, scope: Scope, receive: Receive, send: Send):
                if scope["type"] == "http":
                    from urllib.parse import parse_qs
                    qs = parse_qs(scope.get("query_string", b"").decode())
                    opendart = qs.get("opendart", [None])[0]
                    if opendart:
                        set_request_api_key(opendart)
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
