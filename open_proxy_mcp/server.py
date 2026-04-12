"""OpenProxy MCP 서버 — FastMCP 진입점"""

import argparse
import os
from mcp.server.fastmcp import FastMCP
from open_proxy_mcp.tools import register_all_tools

mcp = FastMCP("open-proxy-mcp")
register_all_tools(mcp)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
    )
    args = parser.parse_args()

    if args.transport in ("sse", "streamable-http"):
        mcp.settings.host = os.environ.get("FASTMCP_HOST", "0.0.0.0")
        mcp.settings.port = int(os.environ.get("FASTMCP_PORT", "8000"))

    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
