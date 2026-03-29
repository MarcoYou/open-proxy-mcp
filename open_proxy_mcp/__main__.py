"""python -m open_proxy_mcp 으로 서버 실행

Usage:
    python -m open_proxy_mcp              # stdio (Claude Desktop/Code)
    python -m open_proxy_mcp --sse        # SSE (웹 연결, port 9000)
    python -m open_proxy_mcp --sse 8080   # SSE (커스텀 포트)
"""

import sys
import uvicorn
from open_proxy_mcp.server import mcp

if "--sse" in sys.argv:
    port = 9000
    # 커스텀 포트
    idx = sys.argv.index("--sse")
    if idx + 1 < len(sys.argv) and sys.argv[idx + 1].isdigit():
        port = int(sys.argv[idx + 1])

    app = mcp.sse_app()
    uvicorn.run(app, host="0.0.0.0", port=port)
else:
    mcp.run(transport="stdio")
