"""OpenProxy MCP 서버 — FastMCP 진입점"""

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("open-proxy-dart")

# tool 등록 — 도메인별 모듈에서 import
from open_proxy_mcp.tools.shareholder import register_tools

register_tools(mcp)
