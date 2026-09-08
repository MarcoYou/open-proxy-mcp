#!/usr/bin/env python3
"""tool 을 **MCP 호출로** 두드려 본다 — 직접 import 가 아니라 사용자가 보는 경로로.

왜 필요한가: CLAUDE.md 는 「검증은 MCP 호출」이라고 못박는다(직접 import 는 wrapper·렌더러를
건너뛰어 사용자가 보는 것과 다르다). 그런데 레포에 그걸 할 얇은 클라이언트가 없어서, 검증할
때마다 새로 짜게 됐다 — 260908~09 두 라운드에서 실제로 두 번 짰다.

DART 콜은 **부르는 tool 이 쓰는 만큼** 난다(이 스크립트 자체는 0). 분당 910 을 넘으면 그 키가
2~3시간 막히므로, 표본을 늘릴 때는 `--sleep` 을 함께 올린다.

키는 `.env`(또는 환경변수)에서 읽고 **출력하지 않는다** — URL 도 마스킹해서 찍는다.

    # 로컬 pilot 의 한 tool
    python3 scripts/mcp_probe.py financial_metrics company=삼성전자 scope=summary

    # 배포본과 대조(같은 인자, 두 서버)
    python3 scripts/mcp_probe.py --url http://127.0.0.1:8000/mcp \\
        --diff https://open-proxy-mcp.fly.dev/mcp financial_metrics company=두산밥캣

    # 목록만
    python3 scripts/mcp_probe.py --list
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import sys
import time
import urllib.request

_DEFAULT_URL = "http://127.0.0.1:8000/mcp"


def _api_key() -> str:
    key = os.environ.get("OPENDART_API_KEY")
    if key:
        return key
    # `.env` 는 상위로 올라가며 찾는다 — git worktree 에서 돌리면 워크트리 안에는 없고
    # 본 체크아웃에 있다(런타임의 `load_dotenv()` 도 같은 방식으로 찾는다).
    d = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    while True:
        env = os.path.join(d, ".env")
        if os.path.exists(env):
            for line in open(env, encoding="utf-8"):
                line = line.strip()
                if line.startswith("OPENDART_API_KEY="):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    sys.exit("OPENDART_API_KEY 가 없다 — 환경변수나 .env 에 둔다.")


def _mask(url: str) -> str:
    """로그·출력용. 쿼리스트링의 키는 절대 남기지 않는다."""
    return url.split("?", 1)[0]


class MCP:
    """streamable-http MCP 최소 클라이언트. 세션 헤더와 SSE 한 겹만 처리한다."""

    def __init__(self, url: str, key: str):
        self.url = url + ("&" if "?" in url else "?") + "opendart=" + key
        self.sid: str | None = None
        self._id = 0
        self._rpc("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                 "clientInfo": {"name": "mcp_probe", "version": "1"}})
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}, notify=True)

    def _post(self, body: dict, notify: bool = False, timeout: int = 600):
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), method="POST")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json, text/event-stream")
        if self.sid:
            req.add_header("mcp-session-id", self.sid)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            self.sid = resp.headers.get("mcp-session-id") or self.sid
            raw = resp.read().decode("utf-8", "replace")
        if notify:
            return None
        for line in raw.splitlines():
            if line.startswith("data:"):
                return json.loads(line[5:].strip())
        return json.loads(raw) if raw.strip() else None

    def _rpc(self, method: str, params: dict, timeout: int = 600):
        self._id += 1
        msg = self._post({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params},
                         timeout=timeout)
        if msg is None:
            raise RuntimeError(f"{_mask(self.url)}: {method} 빈 응답")
        if "error" in msg:
            raise RuntimeError(f"{_mask(self.url)} {method}: {msg['error']}")
        return msg["result"]

    def tools(self) -> list[str]:
        return sorted(t["name"] for t in self._rpc("tools/list", {})["tools"])

    def call(self, tool: str, args: dict, timeout: int = 600) -> tuple[str, float]:
        t0 = time.time()
        res = self._rpc("tools/call", {"name": tool, "arguments": args}, timeout)
        text = "".join(c.get("text", "") for c in res.get("content", []) if c.get("type") == "text")
        return text or json.dumps(res, ensure_ascii=False), time.time() - t0


def _coerce(v: str):
    """`k=v` 의 v 를 JSON 으로 읽어 보고, 아니면 문자열. `scope=summary` 와 `years=10` 을 함께 받으려고."""
    try:
        return json.loads(v)
    except Exception:
        return v


def main() -> int:
    ap = argparse.ArgumentParser(description="MCP tool 을 실제 프로토콜로 호출한다")
    ap.add_argument("tool", nargs="?", help="tool 이름")
    ap.add_argument("args", nargs="*", help="k=v (값은 JSON 으로 읽어보고 아니면 문자열)")
    ap.add_argument("--url", default=os.environ.get("OPM_MCP_URL", _DEFAULT_URL))
    ap.add_argument("--diff", help="이 URL 과 응답을 대조한다(배포 전후 회귀 확인)")
    ap.add_argument("--list", action="store_true", help="tool 목록만")
    ap.add_argument("--json", action="store_true", help="format=json 을 붙여 부른다")
    ap.add_argument("--sleep", type=float, default=0.0, help="두 서버 호출 사이 대기(초)")
    ap.add_argument("--full", action="store_true", help="응답 전문 출력(기본은 앞 2,000자)")
    a = ap.parse_args()

    key = _api_key()
    primary = MCP(a.url, key)

    if a.list or not a.tool:
        names = primary.tools()
        print(f"{_mask(a.url)} — tool {len(names)}개")
        for n in names:
            print(" ", n)
        return 0

    args = {k: _coerce(v) for k, _, v in (x.partition("=") for x in a.args) if k}
    if a.json:
        args["format"] = "json"

    text, secs = primary.call(a.tool, args)
    print(f"# {_mask(a.url)} · {a.tool}({json.dumps(args, ensure_ascii=False)})")
    print(f"# {len(text.encode()):,} bytes · {secs:.2f}s")
    try:                                    # usage 는 payload 안에 있다(json 일 때만)
        u = (json.loads(text).get("data") or {}).get("usage") or {}
        if u:
            print(f"# usage: {json.dumps(u, ensure_ascii=False)}")
    except Exception:
        pass
    print(text if a.full else text[:2000])

    if not a.diff:
        return 0

    if a.sleep:
        time.sleep(a.sleep)
    other, osecs = MCP(a.diff, key).call(a.tool, args)
    print(f"\n# {_mask(a.diff)} · {len(other.encode()):,} bytes · {osecs:.2f}s")
    if other == text:
        print("== 두 응답이 바이트 단위로 같다.")
        return 0
    print("!! 응답이 다르다 — diff:")
    for line in list(difflib.unified_diff(other.splitlines(), text.splitlines(),
                                          fromfile=_mask(a.diff), tofile=_mask(a.url),
                                          lineterm=""))[:120]:
        print(line)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
