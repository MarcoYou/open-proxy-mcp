#!/bin/sh
# BPM(beta-proxy-mcp) 로컬 기동 — stdio 는 금지돼 있어 로컬도 streamable-http 로 띄운다.
# 운영과 갈라두는 것 셋: 포트(8010) · DB(비움 → sqlite) · DART 키(2번 키).
cd "$(dirname "$0")"
export FASTMCP_HOST=127.0.0.1
export FASTMCP_PORT=8010
export FASTMCP_ALLOWED_HOSTS=127.0.0.1:8010,localhost:8010
unset DATABASE_URL
export OPENDART_API_KEY="$(cat ~/.openclaw/credentials/opendart-push.key)"
exec uv run python -m open_proxy_mcp --transport streamable-http
