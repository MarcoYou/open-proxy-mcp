#!/bin/sh
# BPM(beta-proxy-mcp) 로컬 기동 — stdio 는 금지돼 있어 로컬도 streamable-http 로 띄운다.
# 운영과 갈라두는 것 셋: 포트(8010) · DB(비움 → sqlite) · DART 키(2번 키).
cd "$(dirname "$0")"
export FASTMCP_HOST=127.0.0.1
export FASTMCP_PORT=8010
export FASTMCP_ALLOWED_HOSTS=127.0.0.1:8010,localhost:8010

# 이미 8010 을 물고 있는 프로세스가 있으면 죽이고 다시 간다.
# (예전엔 [Errno 48] address already in use 로 조용히 실패했다)
OLD_PIDS=$(lsof -ti tcp:"$FASTMCP_PORT" -sTCP:LISTEN 2>/dev/null)
if [ -n "$OLD_PIDS" ]; then
  echo "run_beta: 포트 $FASTMCP_PORT 사용 중 → 기존 프로세스 종료 ($OLD_PIDS)" >&2
  kill $OLD_PIDS 2>/dev/null
  for _ in 1 2 3 4 5 6 7 8 9 10; do
    lsof -ti tcp:"$FASTMCP_PORT" -sTCP:LISTEN >/dev/null 2>&1 || break
    sleep 0.5
  done
  STILL=$(lsof -ti tcp:"$FASTMCP_PORT" -sTCP:LISTEN 2>/dev/null)
  if [ -n "$STILL" ]; then
    echo "run_beta: 정상 종료 실패 → SIGKILL ($STILL)" >&2
    kill -9 $STILL 2>/dev/null
    sleep 1
  fi
fi

export OPENDART_API_KEY="$(cat ~/.openclaw/credentials/opendart-push.key)"
exec uv run python -m open_proxy_mcp --transport streamable-http
