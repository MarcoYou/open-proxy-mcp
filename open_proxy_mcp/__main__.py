"""python -m open_proxy_mcp 으로 서버 실행

Usage:
    python -m open_proxy_mcp          # stdio (Claude Desktop/Code)
    python -m open_proxy_mcp --sse    # SSE (웹 연결, port 8080)
"""

import sys
from open_proxy_mcp.server import mcp

if "--sse" in sys.argv:
    mcp.run(transport="sse")
else:
    mcp.run(transport="stdio")
