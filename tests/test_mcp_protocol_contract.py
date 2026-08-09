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
import pathlib

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


# ── main() 이 실제로 무엇을 서빙하나 ────────────────────────────────────────
def test_main_serves_exactly_what_build_app_returns(monkeypatch):
    """**가장 큰 구멍이었다.** 위 게이트는 `build_app()` 을 재는데 프로덕션은 `main()` 이
    서빙한다. `main()` 이 `build_app` 을 우회하면 미들웨어·호스트보호·무상태·JSON 이
    한꺼번에 사라지는데 계약 게이트가 **전부 초록**이다(적대적 감사 실측).
    260729 결함(라우트를 서빙되지 않는 인스턴스에 붙임)이 한 층 위로 옮겨간 모양이다.

    이 이관에서 특히 위험하다 — 다섯 설정이 `streamable_http_app()` 인자로 옮겨가는데,
    인자를 쓰기 가장 자연스러운 자리가 `uvicorn.run` 옆, 즉 `main()` 안이다.

    스텁이 아니라 **스파이**로 감싼다. 스텁으로 갈아치우면 진짜 앱이 안 만들어져
    host·port 가 SDK 기본값으로 보이고, 그게 바로 이 테스트가 잡으려는 결함이다.
    """
    import re as _re
    import sys
    import uvicorn
    import open_proxy_mcp.server as S

    seen = {}
    real_build_app = S.build_app

    def spy(server=None):
        app = real_build_app(server)
        seen["built"] = app
        return app

    monkeypatch.delenv("FASTMCP_HOST", raising=False)   # fly [env] 에 없다 → 기본값이 프로덕션 값
    monkeypatch.delenv("FASTMCP_PORT", raising=False)
    monkeypatch.setattr(sys, "argv", ["open_proxy_mcp.server", "--transport", "streamable-http"])
    monkeypatch.setattr(S, "build_app", spy)
    monkeypatch.setattr(S, "install_api_key_redaction", lambda: seen.update(redacted=True))
    monkeypatch.setattr(uvicorn, "run", lambda app, host, port: seen.update(app=app, host=host, port=port))
    S.main()

    assert "built" in seen, "main() 이 build_app() 을 아예 부르지 않는다"
    assert seen.get("app") is seen["built"], "main() 이 build_app() 이 만든 앱이 아닌 다른 것을 서빙한다"
    assert seen.get("redacted"), "액세스 로그 마스킹이 안 걸렸다 — 유저 DART 키가 평문으로 쌓인다"

    fly = (pathlib.Path(__file__).resolve().parent.parent / "fly.toml").read_text(encoding="utf-8")
    internal_port = int(_re.search(r"internal_port\s*=\s*(\d+)", fly).group(1))
    assert seen["port"] == internal_port, f"바인딩 포트 {seen['port']} != fly internal_port {internal_port}"
    assert seen["host"] == "0.0.0.0", (
        f"VM 밖에서 못 닿는 주소에 바인딩한다: {seen['host']}. SDK 기본값은 127.0.0.1 이라 "
        "설정이 안 걸리면 fly 프록시가 못 닿고 전면 장애가 난다.")


@pytest.mark.parametrize("q", ["", "%20", "%20%20", "%09"])
def test_blank_key_never_passes_the_gate(client, q):
    """공백만 든 키는 없는 것으로 쳐야 한다. 파이썬에서 " " 는 참이라 종전에는 통과했고,
    하류 폴백도 참이라 **공백이 그대로 DART 키로** 쓰였다(실측 200)."""
    r = client.post(f"/mcp?opendart={q}", json=_INIT,
                    headers={**_HDRS, "Host": PROD_HOST})
    assert r.status_code == 401, f"공백 키가 통과했다: {q!r} → {r.status_code}"


@pytest.mark.parametrize("host", [
    "evil.example.com",
    "evil-open-proxy-mcp.fly.dev",       # 접미 일치 매처로 바뀌면 통과한다
    "open-proxy-mcp.fly.dev.evil.com",   # 접두 일치 매처로 바뀌면 통과한다
])
def test_lookalike_hosts_are_rejected(client, host):
    """기존 게이트는 목록과 아무 관계 없는 이름 하나만 던진다 — 매처가 부분 일치로 바뀌어도
    초록이다. **닮은 이름**으로 재야 「목록에 없다」가 아니라 「보호가 산다」를 잰다."""
    assert _post(client, _INIT, host=host).status_code == 421, f"{host} 가 통과했다"


def test_tool_count_matches_the_documented_catalog(client):
    """`>= 20` 은 25개 중 5개가 조용히 사라져도 통과한다(실측). 문서화된 카탈로그와 맞춘다 —
    숫자를 여기 박으면 이중장부가 되므로 `wiki/tools/` 를 단일 출처로 쓴다."""
    r = _post(client, {"jsonrpc": "2.0", "id": 8, "method": "tools/list", "params": {}})
    wire = {t["name"] for t in r.json()["result"]["tools"]}
    # 제외 목록을 여기 복사하지 않는다 — 이중장부가 된다. 카탈로그 검사기의 상수를 그대로 쓴다.
    import sys
    root = pathlib.Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(root / "scripts"))
    from check_tool_catalog import CATALOG_DIR, SUPPORT_PAGES
    docs = {p.stem for p in CATALOG_DIR.glob("*.md") if p.stem not in SUPPORT_PAGES}
    missing = docs - wire
    assert not missing, f"문서에는 있는데 wire 에 없는 도구: {sorted(missing)}"


def test_initialize_actually_succeeds(client):
    """HTTP 200 은 JSON-RPC 성공이 아니다 — 깨진 핸드셰이크도 200 에 error 를 실어 온다.
    본문을 읽어야 「응답했다」가 아니라 「제대로 응답했다」를 잰다."""
    d = _post(client, _INIT).json()
    assert "error" not in d, d.get("error")
    res = d["result"]
    assert res["protocolVersion"] and res["serverInfo"]["name"] == "openproxy", res
    assert res["instructions"], "instructions 가 비었다 — 도구 횡단 규칙이 클라이언트에 안 간다"
