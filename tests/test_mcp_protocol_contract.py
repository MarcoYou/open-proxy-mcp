# -*- coding: utf-8 -*-
"""프로토콜 계약 — **프로덕션이 실제로 서빙하는 앱**(`build_app()`)에 대고 잰다.

왜 따로 필요한가: 나머지 700여 개는 서비스 레이어라 **MCP 층이 통째로 죽어도 초록**이다.
실측(260809, mcp 2.0 이관 실험) — 사용자 전원이 421 을 받는 완전 다운 상태에서 706/708 이
통과했고, 실패한 둘은 「1.x 에 머물러라」는 가드레일이라 서빙과 무관했다. 260729 사고와
같은 모양이다(서버 사망 · fly 배포 성공 · CI 초록).

여기 있는 것은 전부 **부정을 단언**하거나 **실제 wire 바이트**를 본다. 「200 이 나온다」만
재면 보호가 꺼져도 통과한다 — 꺼진 쪽이 더 잘 통과한다.
"""
from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from open_proxy_mcp.server import _ERR_PATTERNS, allowed_hosts, build_app

PROD_HOST = "open-proxy-mcp.fly.dev"
_INIT = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "contract", "version": "0"}}}
_HDRS = {"Content-Type": "application/json",
         "Accept": "application/json, text/event-stream"}


@pytest.fixture()
def client():
    # 앱마다 새 TestClient — StreamableHTTPSessionManager.run() 은 재사용 시 예외를 낸다.
    with TestClient(build_app()) as c:
        yield c


def _post(client, body, host=PROD_HOST, key="k"):
    return client.post(f"/mcp?opendart={key}", json=body,
                       headers={**_HDRS, "Host": host})


# ── 호스트 보호 ──────────────────────────────────────────────────────────────
def test_production_host_is_served(client):
    """운영 호스트로 오는 요청은 통과해야 한다.

    mcp 2.0 은 `transport_security` 를 안 넘기면 허용 목록을 localhost 로만 잡는다.
    그러면 **사용자 전원이 421** 인데 `/health` 는 200 이라 배포·CI·스모크가 전부 초록이다.
    """
    r = _post(client, _INIT)
    assert r.status_code == 200, f"운영 호스트가 거부됐다: {r.status_code} {r.text[:80]}"


def test_foreign_host_is_rejected(client):
    """**보호가 조용히 꺼지는 것을 잡는 유일한 단언.**

    호스트 보호가 없어도 서비스는 멀쩡히 돈다 — 사용자도, 지표도, 다른 테스트도
    아무것도 눈치채지 못한다. 부정을 재지 않으면 이 회귀는 영원히 안 걸린다.
    """
    r = _post(client, _INIT, host="evil.example.com")
    assert r.status_code == 421, f"허용 안 된 Host 가 통과했다 — 보호가 꺼졌다: {r.status_code}"


def test_allowed_hosts_contains_production():
    assert PROD_HOST in allowed_hosts()


# ── 무상태·JSON 응답 ─────────────────────────────────────────────────────────
def test_stateless_and_json_response(client):
    """세션 없이 연속 호출이 되고, 응답이 SSE 가 아니라 JSON 이어야 한다.

    fly 는 머신 2대라 상태를 들면 "Session not found" 가 간헐적으로 난다. kwarg 를
    확인하지 말고 **동작**으로 잰다 — 설정 이름은 SDK 버전마다 바뀐다.
    """
    a = _post(client, _INIT)
    b = _post(client, {**_INIT, "id": 2})       # 세션 id 없이 두 번째
    assert a.status_code == b.status_code == 200, "세션 없이 두 번째 호출이 실패 — 상태를 들고 있다"
    assert "application/json" in a.headers.get("content-type", ""), a.headers.get("content-type")


# ── 도구 표면 ────────────────────────────────────────────────────────────────
def test_tools_list_over_the_wire(client):
    """객체가 아니라 **wire** 로 도구를 센다. `build_mcp().list_tools()` 만 재면
    앱 조립이 깨져도 통과한다."""
    r = _post(client, {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}})
    assert r.status_code == 200, r.text[:120]
    tools = r.json()["result"]["tools"]
    assert len(tools) >= 20, f"도구가 {len(tools)}개 — 등록이 깨졌다"
    assert "company" in {t["name"] for t in tools}


def test_prompts_list_over_the_wire(client):
    r = _post(client, {"jsonrpc": "2.0", "id": 4, "method": "prompts/list", "params": {}})
    assert r.status_code == 200, r.text[:120]
    names = {p["name"] for p in r.json()["result"]["prompts"]}
    assert "company_snapshot" in names, names


# ── 키 게이트 ────────────────────────────────────────────────────────────────
def test_no_key_is_rejected(client):
    """키 없는 서빙 요청은 401. 배포 스모크가 이 값을 도달성 판정에 쓴다."""
    r = client.post("/mcp", json=_INIT, headers={**_HDRS, "Host": PROD_HOST})
    assert r.status_code == 401, r.status_code


def test_health_is_open_and_counts_tools(client):
    r = client.get("/health", headers={"Host": PROD_HOST})
    assert r.status_code == 200
    d = r.json()
    assert d["status"] == "ok" and isinstance(d["tools"], int) and d["tools"] > 0, d


# ── 에러 wire 계약 ───────────────────────────────────────────────────────────
def test_error_bytes_match_the_scanner_patterns(client):
    """usage 미들웨어가 쓰는 **그 상수**로 실제 응답 바이트를 검사한다.

    테스트가 리터럴을 복사해 가지면, 필드명이 바뀌어 서버가 눈이 먼 뒤에도 테스트는
    영원히 통과한다 — 매칭 0건과 「오류가 없었다」는 구분되지 않는다. 그래서 import 한다.
    """
    r = _post(client, {"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                       "params": {"name": "no_such_tool_xyz", "arguments": {}}})
    raw = r.content
    assert any(p in raw for p in _ERR_PATTERNS), (
        f"실패 응답이 스캐너 패턴 중 어느 것과도 안 맞는다 — 에러율이 영원히 0이 된다. "
        f"바이트: {raw[:160]!r}")


def test_success_bytes_do_not_match_the_scanner(client):
    """반대 어형 — 성공 응답이 오류로 잡히면 에러율이 부풀려진다."""
    r = _post(client, {"jsonrpc": "2.0", "id": 6, "method": "tools/list", "params": {}})
    assert r.status_code == 200
    assert not any(p in r.content for p in _ERR_PATTERNS), r.content[:160]
