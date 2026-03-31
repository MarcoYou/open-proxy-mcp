"""OpenProxy MCP 서버 — FastMCP 진입점"""

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

mcp = FastMCP("open-proxy-mcp")

# tool 등록 — 도메인별 모듈에서 import
from open_proxy_mcp.tools.shareholder import register_tools as register_shareholder_tools
from open_proxy_mcp.tools.ownership import register_tools as register_ownership_tools

register_shareholder_tools(mcp)
register_ownership_tools(mcp)
