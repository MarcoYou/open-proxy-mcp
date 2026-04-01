"""OpenProxy MCP 서버 — FastMCP 진입점"""

from mcp.server.fastmcp import FastMCP
from open_proxy_mcp.tools import register_all_tools

mcp = FastMCP("open-proxy-mcp")
register_all_tools(mcp)
