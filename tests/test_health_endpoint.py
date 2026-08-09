# -*- coding: utf-8 -*-
"""헬스 엔드포인트 — 배포가 살아 있는지 외부에서 확인하는 유일한 경로. network 0콜.

260729 사고: `mcp` 2.0.0 이 `mcp.server.fastmcp` 를 제거해 서버가 부팅 즉시 죽었다.
헬스체크가 없어 fly 는 「VM 이 켜졌다」만 보고 배포를 성공 처리했고 GitHub CI 도 초록이었다.
로컬 테스트 361개도 전부 통과했다(.venv 엔 구버전이 깔려 있으니). **배포만 깨지는 구조.**
"""
from __future__ import annotations

import json

from starlette.testclient import TestClient

from open_proxy_mcp.server import mcp

import pytest


def test_health_returns_200_without_auth():
    client = TestClient(mcp.streamable_http_app())
    r = client.get("/health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    # 도구가 0개면 살아 있어도 쓸모가 없다 — 등록까지 확인한다
    assert body["tools"] > 0, body


def test_health_is_registered_on_the_http_app():
    paths = {getattr(r, "path", None) for r in mcp.streamable_http_app().routes}
    assert "/health" in paths and "/mcp" in paths, paths


def test_server_imports_the_module_that_actually_exists():
    """서버가 얹혀 있는 모듈을 테스트가 직접 붙잡는다.

    260729: `mcp.server.fastmcp` 가 2.0.0 에서 사라져 서버가 부팅 즉시 죽었다. 그때 이
    테스트가 「fastmcp 가 있어야 한다」로 세워졌고, 2.0 이관(260810)으로 방향이 뒤집혔다.
    **지우지 않고 뒤집는다** — 사고가 사서 얻은 가드레일이다. 양방향으로 잰다:
    새 경로가 있어야 하고, 옛 경로는 **없어야** 한다(조용한 1.x 다운그레이드도 잡는다).
    """
    import importlib
    m = importlib.import_module("mcp.server.mcpserver")
    assert hasattr(m, "MCPServer")
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("mcp.server.fastmcp")


def test_dependency_has_an_upper_bound_on_mcp():
    """상한이 없으면 새 메이저가 나온 날 배포만 깨진다(260729 실측)."""
    import pathlib
    txt = (pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    line = next(l for l in txt.splitlines() if l.strip().startswith('"mcp['))
    assert "<3" in line, line


def test_health_exists_on_a_freshly_built_instance():
    """`main()` 은 `build_mcp()` 로 **새 인스턴스**를 만든다 — 모듈 레벨 mcp 에만 붙이면
    실제 서빙되는 앱엔 라우트가 없다(260729 2차 사고: 배포 후 /health 404, 배포 실패).
    """
    from open_proxy_mcp.server import build_mcp
    fresh = build_mcp()
    paths = {getattr(r, "path", None) for r in fresh.streamable_http_app().routes}
    assert "/health" in paths, paths
    r = TestClient(fresh.streamable_http_app()).get("/health")
    assert r.status_code == 200 and r.json()["tools"] > 0, r.text
