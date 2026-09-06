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
import re

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


def test_company_snapshot_prompt_references_available_tools(client):
    """회사 하나로 열리는 양식이 개명·제거된 도구를 호출하도록 안내하지 않는다."""
    prompts = _post(client, {"jsonrpc": "2.0", "id": 8, "method": "prompts/list",
                             "params": {}}).json()["result"]["prompts"]
    prompt = next(p for p in prompts if p["name"] == "company_snapshot")
    assert {a["name"] for a in prompt["arguments"] if a.get("required")} == {"company"}

    r = _post(client, {"jsonrpc": "2.0", "id": 9, "method": "prompts/get",
                       "params": {"name": "company_snapshot", "arguments": {"company": "삼성전자"}}})
    assert r.status_code == 200, r.text[:120]
    messages = r.json()["result"]["messages"]
    text = "\n".join(m["content"]["text"] for m in messages)
    assert "삼성전자" in text
    references = set(re.findall(r"`([a-z][a-z0-9_]*)`", text))
    tools = _post(client, {"jsonrpc": "2.0", "id": 10, "method": "tools/list",
                           "params": {}}).json()["result"]["tools"]
    assert references and references <= {t["name"] for t in tools}


def test_tools_guide_resource_over_the_wire(client):
    """기능 안내가 발견·열람되고, 서빙 중인 도구를 빠짐없이 한 번씩 소개한다."""
    def rpc(method, params):
        r = _post(client, {"jsonrpc": "2.0", "id": 7, "method": method, "params": params})
        assert r.status_code == 200, r.text[:120]
        return r.json()["result"]

    resources = rpc("resources/list", {})["resources"]
    guide = next(r for r in resources if r["name"] == "tools_guide")
    assert guide["uri"] == "opm://tools_guide"
    assert guide["mimeType"] == "text/markdown"
    contents = rpc("resources/read", {"uri": guide["uri"]})["contents"]
    text = contents[0]["text"]
    names = [line.removeprefix("## ") for line in text.splitlines() if line.startswith("## ")]
    tools = rpc("tools/list", {})["tools"]
    assert len(names) == len(set(names)) == len(tools)
    assert set(names) == {t["name"] for t in tools}
    assert names[0] == "company"
    assert "설명이 등록되지 않았습니다" not in text


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
def test_server_version_is_reported(client):
    """`serverInfo.version` 이 비어 있지 않고 pyproject 와 같아야 한다.

    2.0 은 이 값을 자동으로 안 채운다(기본 ""). `_opm_version()` 이 조용히 실패하면
    (빌드 백엔드 변경·`--no-install-project`·배포명 변경) 빈 문자열이 나가는데,
    **읽는 곳이 없어서 아무도 모른다** — 이 브랜치가 막으려는 실패 모양 그대로다.
    """
    import pathlib as _p
    r = _post(client, _INIT)
    ver = r.json()["result"]["serverInfo"]["version"]
    assert ver, "serverInfo.version 이 비어 있다 — _opm_version() 이 조용히 실패했다"
    txt = (_p.Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    declared = next(l.split("=")[1].strip().strip('"')
                    for l in txt.splitlines() if l.strip().startswith("version ="))
    assert ver == declared, f"wire={ver} vs pyproject={declared}"


# ── 통계가 「모르겠다」를 말할 수 있는가 ────────────────────────────────────
def _drive(client, body, monkeypatch):
    """미들웨어가 **실제로 무엇을 적었는지** 본다. 응답 바이트만 보면 스캐너가 아예 안
    돌아도 통과한다 — 적힌 값을 봐야 「기록이 살아있나」를 잰다."""
    import open_proxy_mcp.usage as usage
    rows = []
    monkeypatch.setattr(usage, "record", lambda *a, **k: rows.append((a, k)))
    client.post("/mcp?opendart=k", json=body, headers={**_HDRS, "Host": PROD_HOST})
    return rows


def test_failure_is_recorded_as_error(client, monkeypatch):
    rows = _drive(client, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "no_such_tool_xyz", "arguments": {}}}, monkeypatch)
    assert rows, "usage 이벤트가 없다 — 통계가 죽었다"
    kw = rows[-1][1]
    assert kw.get("is_error") is True, f"실패가 오류로 안 잡혔다: {kw}"
    assert kw.get("error_kind"), f"error_kind 가 비었다: {kw}"


def test_success_is_recorded_as_not_error(client, monkeypatch):
    rows = _drive(client, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                           "params": {"name": "law_lookup",
                                      "arguments": {"query": "상법 제363조"}}}, monkeypatch)
    kw = rows[-1][1]
    assert kw.get("is_error") is False, f"성공이 오류로 잡혔다 — 에러율이 부풀려진다: {kw}"
    assert kw.get("error_kind") is None, kw


def test_unreadable_response_is_unclassifiable_not_success(client, monkeypatch):
    """**이 테스트가 이번 수정의 전부다.** 스캐너가 응답을 못 읽으면 「성공」이 아니라
    「모르겠다」로 적혀야 한다. 종전엔 둘 다 is_error=False 라, 필드명이 바뀌면
    **에러율이 영원히 0** 이 되고 아무 신호도 안 났다.
    """
    import open_proxy_mcp.server as S
    # 쉼표 없이 `(b"x")` 로 쓰면 튜플이 아니라 바이트열이고, 순회하면 정수가 나와
    # 엉뚱하게 매칭된다(이 테스트를 처음 쓸 때 실제로 그렇게 틀렸다).
    monkeypatch.setattr(S, "_ERR_PATTERNS", (b'"__nope__":true',))   # 스캐너를 눈멀게 한다
    monkeypatch.setattr(S, "_ERR_FIELD", (b'"__nope__"',))
    rows = _drive(client, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                           "params": {"name": "no_such_tool_xyz", "arguments": {}}}, monkeypatch)
    kw = rows[-1][1]
    assert kw.get("is_error") is None, (
        f"못 읽은 응답이 is_error={kw.get('is_error')!r} 로 적혔다 — "
        "「모르겠다」가 「성공」으로 둔갑하면 에러율이 조용히 0이 된다")
    assert kw.get("error_kind") == "unclassifiable", kw


def test_request_body_is_not_buffered_without_bound(client, monkeypatch):
    """미들웨어가 본문을 **끝까지** 모으면 안 된다.

    라우터 밖에 있어 SDK 의 4 MiB 상한보다 먼저 도는데, 종전에는 32 MiB 를 통째로 담은
    뒤에야 413 이 났다(실측). 1 GB VM 에 OOM 이력(260804)이 있다. 도구 이름은 앞부분에
    있으므로 상한까지만 읽고 나머지는 흘려보낸다.
    """
    import open_proxy_mcp.server as S
    assert S._MAX_SNIFF_BYTES <= 1 << 20, f"sniff 상한이 너무 크다: {S._MAX_SNIFF_BYTES}"
    big = "가" * (S._MAX_SNIFF_BYTES // 2)     # UTF-8 3B/자 → 상한을 확실히 넘긴다
    rows = _drive(client, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                           "params": {"name": "law_lookup", "arguments": {"query": big}}},
                  monkeypatch)
    assert rows, "큰 요청에서 기록이 아예 안 남았다"


# ── 로컬 실행이 운영 통계를 오염시키지 않는가 ──────────────────────────────
def test_local_runs_never_write_to_production_stats():
    """**이 파일을 돌리는 것 자체가 운영 통계에 쓰이고 있었다.**

    dart/client.py 가 import 시 load_dotenv() 를 돌려 로컬에서도 DATABASE_URL 이
    채워지므로, 막지 않으면 pytest·pilot·스크립트가 전부 운영 Postgres 에 적힌다.
    260810 실측: 이 파일에만 있는 이름 `no_such_tool_xyz` 20건과 호스트거부 58건이
    운영 통계에 있었고, 키해시는 아래 `_drive` 가 쓰는 리터럴 `"k"` 였다.
    """
    import open_proxy_mcp.usage as usage
    assert usage.MACHINE == "local", (
        "FLY_MACHINE_ID 가 세팅된 채로 테스트가 돈다 — 이 실행은 운영으로 취급된다")
    assert usage._RECORDING is False, "로컬인데 기록이 열려 있다"
    before = usage._q.qsize()
    usage.record("some-test-key", 200, "law_lookup", 5)
    assert usage._q.qsize() == before, "로컬 호출이 큐에 쌓였다 — 운영 DB 로 흘러간다"


def test_recording_still_works_when_the_gate_is_open(monkeypatch):
    """짝 테스트. 위가 「안 쌓인다」만 보면 record() 가 통째로 죽어도 통과한다 —
    게이트를 열었을 때 **쌓이는지**까지 봐야 둘이 서로를 판별한다.
    큐·워커는 갈아끼워 이 테스트가 DB 를 건드리지 않게 한다."""
    import queue as _queue
    import open_proxy_mcp.usage as usage
    sink = _queue.Queue()
    monkeypatch.setattr(usage, "_RECORDING", True)
    monkeypatch.setattr(usage, "_q", sink)
    monkeypatch.setattr(usage, "_ensure_worker", lambda: None)
    usage.record("some-test-key", 200, "law_lookup", 5)
    assert sink.qsize() == 1, "게이트를 열었는데도 기록이 안 된다 — 통계가 죽는다"


# ── 상류 실패가 「성공」으로 잡히지 않는가 (260810) ────────────────────────
def _degrade_body(kind_status: str):
    """실제 degrade 응답 바이트를 만든다 — 리터럴을 복사하지 않는다.
    복사하면 dart_safety 가 표지를 빼도 이 테스트는 영원히 통과한다."""
    from open_proxy_mcp.dart.client import DartClientError
    from open_proxy_mcp.services.dart_safety import degrade_response
    return degrade_response("dividend", "md", DartClientError(kind_status, "x")).encode()


def test_upstream_degrade_is_not_counted_as_success(client, monkeypatch):
    """**이게 이번 수정의 전부다.**

    degrade 는 설계상 정상 응답(`# tool\\n\\n안내문`)이라 `isError` 가 없다. 표지가 없으면
    스캐너는 성공과 구분하지 못한다 — 오늘 넣은 3상태로도 못 잡는다(스캐너가 눈이 먼 게
    아니라 응답이 진짜 성공 모양이라서). 실측: 306,670행 중 오류 28건뿐이었던 원인.
    """
    import open_proxy_mcp.server as S

    body = _degrade_body("020")                     # 분당 한도 초과 = 답을 못 줌
    assert S._DEGRADED_RE.search(body), "degrade 응답에 표지가 없다 — 통계가 성공으로 적는다"
    assert not any(p in body for p in S._ERR_PATTERNS), (
        "degrade 가 isError 를 달고 있다면 이 테스트의 전제가 틀린 것이다")
    kind = S._DEGRADED_RE.search(body).group(1).decode()
    assert kind == "rate_limited", kind


def test_no_data_is_not_counted_as_failure(client):
    """「조회된 자료가 없다」(013)·「회사를 못 찾았다」(404)는 **답이다.** 실패로 세면
    오류율이 부풀고, 진짜 고장이 그 안에 묻힌다. 다만 빈도는 알아야 하므로 표시는 남긴다."""
    import open_proxy_mcp.server as S

    for status, expect in (("013", "no_data"), ("404", "not_found")):
        body = _degrade_body(status)
        assert not S._DEGRADED_RE.search(body), f"{status} 가 실패로 표시됐다"
        m = S._NODATA_RE.search(body)
        assert m and m.group(1).decode() == expect, f"{status}: {body[-40:]!r}"


def test_degrade_marker_rides_json_format_too(client):
    """기본은 md 지만 format='json' 도 있다. 한쪽만 표시하면 포맷에 따라 통계가 갈린다."""
    from open_proxy_mcp.dart.client import DartClientError
    from open_proxy_mcp.services.dart_safety import degrade_response
    import open_proxy_mcp.server as S

    body = degrade_response("dividend", "json", DartClientError("020", "x")).encode()
    assert S._DEGRADED_RE.search(body), "json degrade 에 표지가 없다"


def test_middleware_records_upstream_degrade_as_error(client, monkeypatch):
    """표지를 다는 것과 **통계에 그렇게 적히는 것**은 별개다 — 미들웨어까지 태운다.

    실제 tool 을 DART 실패로 밀어 degrade 를 타게 하고, `usage.record` 가 받은 값을 본다.
    `dart_` 접두는 **우리 크래시와 상류 실패를 가르기 위한 것**이다(대응이 다르다:
    전자는 우리가 고치고, 후자는 사용자에게 조정 안내가 나가야 한다).
    """
    import open_proxy_mcp.tools.law_lookup as T
    from open_proxy_mcp.dart.client import DartClientError

    def _boom(*a, **k):
        raise DartClientError("020", "rate limited")

    monkeypatch.setattr(T, "build_law_lookup_payload", _boom)
    rows = _drive(client, {"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                           "params": {"name": "law_lookup",
                                      "arguments": {"query": "상법 제363조"}}}, monkeypatch)
    assert rows, "기록이 아예 없다"
    kw = rows[-1][1]
    assert kw.get("is_error") is True, (
        f"상류 실패가 성공으로 적혔다 — degrade 표지가 미들웨어에 안 닿는다: {kw}")
    assert kw.get("error_kind") == "dart_rate_limited", kw


def test_middleware_records_no_data_as_success(client, monkeypatch):
    """「자료 없음」은 실패가 아니다. 실패로 세면 진짜 고장이 그 안에 묻힌다."""
    import open_proxy_mcp.tools.law_lookup as T
    from open_proxy_mcp.dart.client import DartClientError

    def _empty(*a, **k):
        raise DartClientError("013", "no data")

    monkeypatch.setattr(T, "build_law_lookup_payload", _empty)
    rows = _drive(client, {"jsonrpc": "2.0", "id": 10, "method": "tools/call",
                           "params": {"name": "law_lookup",
                                      "arguments": {"query": "상법 제363조"}}}, monkeypatch)
    kw = rows[-1][1]
    assert kw.get("is_error") is False, f"「자료 없음」이 실패로 적혔다: {kw}"
    assert kw.get("error_kind") == "no_data", kw


# ── 추정 해석이 통계까지 도달하는가 (260810) ──────────────────────────────
def test_weak_resolution_reaches_usage_and_carries_no_raw_query(client, monkeypatch):
    """**계산해놓고 버리던 값이다.** 이름이 정확히 안 맞아 추정으로 고르면 장부에 적히고
    사용자에게 warning 으로도 나가는데, `usage.record` 로는 안 넘어가고 있었다.

    같이 지키는 것: **사용자가 친 원문은 안 싣는다.** 장부 항목엔 `query`·`corp_name` 이
    들어 있어서 그대로 넘기면 「질의 원문 미보관」 정책이 그 자리에서 깨진다 —
    방식(normalized·token·substring·fuzzy)만 넘긴다.
    """
    import open_proxy_mcp.tools.law_lookup as T
    from open_proxy_mcp.dart.client import note_weak_resolution

    SECRET = "사용자가_친_원문_회사명"

    def _weak(*a, **k):
        note_weak_resolution(SECRET, "삼성전자", "fuzzy", 3)
        note_weak_resolution(SECRET + "2", "현대차", "token", 2)
        return {"status": "ok", "data": {}, "warnings": []}

    monkeypatch.setattr(T, "build_law_lookup_payload", _weak)
    rows = _drive(client, {"jsonrpc": "2.0", "id": 11, "method": "tools/call",
                           "params": {"name": "law_lookup",
                                      "arguments": {"query": "상법 제363조"}}}, monkeypatch)
    kw = rows[-1][1]
    got = kw.get("weak_kinds")
    assert got, f"추정 해석이 통계로 안 넘어갔다: {kw}"
    assert set(got.split(",")) == {"fuzzy", "token"}, got
    assert SECRET not in str(kw), "사용자 원문이 통계로 새어 나갔다"
    assert "삼성전자" not in str(kw), "해석된 회사명이 통계로 새어 나갔다"


def test_no_weak_resolution_records_nothing(client, monkeypatch):
    """정확히 맞은 해석은 아무것도 안 남긴다 — 빈 문자열이 아니라 NULL 이어야
    「추정 0건」과 「기록 안 됨」이 집계에서 갈린다."""
    import open_proxy_mcp.tools.law_lookup as T

    monkeypatch.setattr(T, "build_law_lookup_payload",
                        lambda *a, **k: {"status": "ok", "data": {}, "warnings": []})
    rows = _drive(client, {"jsonrpc": "2.0", "id": 12, "method": "tools/call",
                           "params": {"name": "law_lookup",
                                      "arguments": {"query": "상법 제363조"}}}, monkeypatch)
    assert rows[-1][1].get("weak_kinds") is None


# ── DART 상태코드 안내가 거짓말을 안 하는가 (260810) ──────────────────────
def test_dart_status_guide_covers_the_official_table():
    """공식 개발가이드 표(opendart.fss.or.kr/guide, 260810 대조) 전체를 덮어야 한다.

    종전엔 7개만 덮어 나머지가 전부 「일시적으로 실패했습니다. 잠시 후 다시 시도하세요」로
    떨어졌다 — **키가 만료된 사람이 영원히 재시도하게 되는 안내**다.
    """
    from open_proxy_mcp.services.dart_safety import _DART_STATUS_GUIDE

    official = {"010", "011", "012", "013", "014", "020", "021",
                "100", "101", "800", "900", "901"}
    missing = official - set(_DART_STATUS_GUIDE)
    assert not missing, f"공식 코드인데 안내가 없다(전부 '잠시 후 재시도'로 떨어진다): {sorted(missing)}"


def test_key_problems_never_tell_the_user_to_retry():
    """**재시도해도 절대 안 되는 것에 재시도를 시키면 안 된다.**
    010·011·901 은 키 문제고, 012 는 IP 문제다 — 넷 다 기다린다고 풀리지 않는다."""
    from open_proxy_mcp.services.dart_safety import _DART_STATUS_GUIDE, _RETRYABLE

    for code in ("010", "011", "901", "012"):
        kind, msg = _DART_STATUS_GUIDE[code]
        assert kind not in _RETRYABLE, f"{code}({kind}) 를 재시도 대상으로 뒀다"
        assert "잠시" not in msg and "다시 시도" not in msg, f"{code}: 재시도 안내가 남아 있다 — {msg}"


def test_rate_advice_only_where_frequency_is_the_cause():
    """011·012 는 종전에 「과호출 누적」·「조회 빈도를 낮추라」고 안내했는데 **둘 다 빈도와
    무관**하다(사용할 수 없는 키 / 접근할 수 없는 IP). 안 몰았는데 많이 했다고 오탐하면
    사용자는 엉뚱한 데를 고치게 된다."""
    from open_proxy_mcp.services.dart_safety import _DART_STATUS_GUIDE

    for code, (_, msg) in _DART_STATUS_GUIDE.items():
        if "빈도" in msg or "나눠서" in msg or "간격" in msg:
            assert code in ("020", "021"), f"{code} 에 호출 방식 안내가 붙었다 — {msg}"


def test_failure_axis_is_separate_from_retry_axis():
    """**두 축은 다르다.** `bad_key` 는 명백한 실패지만 재시도는 무의미하고,
    `no_data` 는 실패가 아니면서 역시 재시도가 무의미하다. 한 축으로 판정하면
    「키가 틀렸다」가 조용히 성공으로 잡힌다(260810 초안이 그랬다)."""
    from open_proxy_mcp.services.dart_safety import _NOT_A_FAILURE, _RETRYABLE, degrade_marker

    assert "bad_key" not in _RETRYABLE and "bad_key" not in _NOT_A_FAILURE
    assert degrade_marker("bad_key").startswith("[degraded="), "키 오류가 성공으로 잡힌다"
    assert degrade_marker("no_data").startswith("[nodata="), "자료없음이 실패로 잡힌다"
    assert degrade_marker("no_document").startswith("[nodata=")
