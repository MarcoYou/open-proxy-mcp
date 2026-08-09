#!/usr/bin/env python3
"""이 브랜치를 띄워 **배포본(live)과 대조**한다 — 배포해도 되는지 한 번에 본다.

왜 필요한가: 테스트가 초록이어도 실제로 서빙되는 것이 다를 수 있다. 실측(260810) —
사용자 전원이 421 을 받는 완전 다운 상태에서 706/708 이 통과했다. 그래서 「테스트가
통과했다」가 아니라 **「live 와 같은 답을 내고, 막을 것은 막는다」**를 잰다.

DART 콜 0. 쓰는 메서드(tools/list·prompts/list·initialize)와 거부 경로는 전부 DART 를
안 친다. 키는 `.env` 에서 읽고 **출력하지 않는다**.

    python3 scripts/check_branch_against_live.py
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
LIVE = "https://open-proxy-mcp.fly.dev"
PROD_HOST = "open-proxy-mcp.fly.dev"


def _key() -> str:
    m = re.search(r"^OPENDART_API_KEY\s*=\s*(\S+)", (ROOT / ".env").read_text(), re.M)
    if not m:
        sys.exit(".env 에 OPENDART_API_KEY 가 없습니다")
    return m.group(1).strip().strip("'\"")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _post(base: str, body: dict, key: str = "", host: str | None = None):
    url = f"{base}/mcp" + (f"?opendart={key}" if key else "")
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream",
                 "MCP-Protocol-Version": "2025-06-18",
                 **({"Host": host} if host else {})})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except Exception as e:                       # 연결 실패도 결과다
        return 0, str(e).encode()


def _canon(raw: bytes) -> str:
    """JSON 키 순서 차이는 무시한다 — SDK 마다 순서가 다르지만 의미는 같다."""
    txt = raw.decode("utf-8", "replace")
    if txt.startswith("data: "):
        txt = txt[6:]
    return json.dumps(json.loads(txt), sort_keys=True, ensure_ascii=False)


def main() -> int:
    key = _key()
    port = _free_port()
    env = {**os.environ, "FASTMCP_PORT": str(port),
           "FASTMCP_ALLOWED_HOSTS": f"127.0.0.1:{port},{PROD_HOST}"}
    uv = shutil.which("uv") or "uv"
    print(f"브랜치 서버를 :{port} 에 띄웁니다 …")
    proc = subprocess.Popen(
        [uv, "run", "python", "-m", "open_proxy_mcp.server", "--transport", "streamable-http"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    local = f"http://127.0.0.1:{port}"
    try:
        for _ in range(45):
            try:
                urllib.request.urlopen(f"{local}/health", timeout=2).read()
                break
            except Exception:
                time.sleep(2)
        else:
            print("  ✗ 브랜치 서버가 안 뜹니다")
            return 1

        rows: list[tuple[str, bool, str]] = []

        # ── live 와 같은 답을 내는가 (의미 기준) ──────────────────────────────
        for method in ("tools/list", "prompts/list"):
            body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": {}}
            _, a = _post(LIVE, body, key)
            _, b = _post(local, body, key, host=PROD_HOST)
            try:
                ok = _canon(a) == _canon(b)
                note = f"{len(a)}B / {len(b)}B"
            except Exception as e:
                ok, note = False, f"파싱 실패 {e}"
            rows.append((f"{method} 가 live 와 같은가", ok, note))

        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "check", "version": "0"}}}
        for v in ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"):
            b_ = {**init, "params": {**init["params"], "protocolVersion": v}}
            _, a = _post(LIVE, b_, key)
            _, b = _post(local, b_, key, host=PROD_HOST)
            try:
                pa = json.loads(a.decode().replace("data: ", "", 1))["result"]["protocolVersion"]
                pb = json.loads(b.decode().replace("data: ", "", 1))["result"]["protocolVersion"]
                rows.append((f"프로토콜 {v} 협상", pa == pb, f"live={pa} / 브랜치={pb}"))
            except Exception as e:
                rows.append((f"프로토콜 {v} 협상", False, str(e)[:40]))

        # ── 막아야 할 것을 막는가 (여기가 핵심 — 뚫리면 조용하다) ────────────
        for host, label in ((PROD_HOST, None),
                            ("evil.example.com", "낯선 호스트"),
                            ("evil-open-proxy-mcp.fly.dev", "닮은 호스트")):
            if label is None:
                continue
            code, _ = _post(local, {"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                                    "params": {}}, key, host=host)
            rows.append((f"{label} 거부(421)", code == 421, f"→ {code}"))

        for q, label in (("", "키 없음"), ("%20", "공백 키"), ("%09", "탭 키")):
            code, _ = _post(local, {"jsonrpc": "2.0", "id": 1, "method": "tools/list",
                                    "params": {}}, q, host=PROD_HOST)
            rows.append((f"{label} 거부(401)", code == 401, f"→ {code}"))

        print()
        for name, ok, note in rows:
            print(f"  {'✅' if ok else '❌'} {name:32} {note}")
        bad = [r for r in rows if not r[1]]
        print()
        if bad:
            print(f"  ❌ {len(bad)}건 실패 — 배포하면 안 됩니다")
            return 1
        print(f"  ✅ {len(rows)}건 전부 통과 — live 와 같은 답을 내고, 막을 것은 막습니다")
        return 0
    finally:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        print("  (브랜치 서버 종료)")


if __name__ == "__main__":
    raise SystemExit(main())
