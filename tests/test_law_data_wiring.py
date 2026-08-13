# -*- coding: utf-8 -*-
"""법령 데이터 배선 — **비어도 응답이 평소와 같은 모양**이라 밖에서 안 보이던 자리. network 0콜.

260814 이전: 룰·조항 대장·corpus 를 `wiki/rules/laws/` 에서 **경로로** 찾아갔고,
못 찾으면 조용히 빈 값을 돌려줬다. 배포 이미지에 wiki 가 들어가는 것은
「Dockerfile COPY 한 줄 + 작업 디렉터리 + 실행 방식」 세 우연의 곱이었고,
하나만 어긋나면 **강행규정 판정 40룰이 통째로 사라지는데 경고도 로그도 없었다.**

이제:
  · 규칙 데이터(62KB) → 패키지 데이터. 코드와 함께 배포되고 cwd 에 무관하다
  · corpus(11MB)      → repo 경로 유지(휠에 안 싣는다). 대신 실패가 로그로 남는다
  · /health 가 개수를 실어 배포 직후 눈으로 확인된다
"""
from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from open_proxy_mcp.server import build_app


@pytest.fixture(scope="module")
def health():
    return TestClient(build_app()).get("/health").json()


def test_law_rules_are_actually_loaded(health):
    """룰이 0이면 강행규정 판정이 전부 비활성화된다 — 그 상태로 배포되면 안 된다."""
    assert health["data"]["law_rules"] > 0, "법령 layer 룰이 0개 — 강행규정 판정이 죽었다"


def test_law_provisions_are_actually_loaded(health):
    assert health["data"]["law_provisions"] > 0, "상법 조항 대장(SSOT)이 비었다"


def test_law_corpus_is_actually_loaded(health):
    assert health["data"]["law_corpus_articles"] > 0, "법령 corpus 가 비었다 — law_lookup 이 죽는다"


def test_health_reports_degraded_when_data_is_empty(monkeypatch):
    """**핵심**: 데이터가 비면 status 가 ok 로 남으면 안 된다.
    종전에는 응답이 평소와 완전히 같아 아무도 알아챌 수 없었다."""
    import open_proxy_mcp.services.proxy_advise as PA
    monkeypatch.setattr(PA, "_LAW_LAYER_RULES_CACHE", [])
    d = TestClient(build_app()).get("/health").json()
    assert d["data"]["law_rules"] == 0
    assert d["status"] == "degraded", "룰이 0인데 헬스가 ok 라고 답한다 — 밖에서 안 보인다"


def test_rules_load_from_package_not_repo_path():
    """패키지 데이터로 읽어야 작업 디렉터리·실행 방식에 안 흔들린다."""
    import inspect
    import open_proxy_mcp.services.proxy_advise as PA
    src = inspect.getsource(PA._load_law_layer_rules)
    assert 'files("open_proxy_mcp.data.laws")' in src, "경로 의존으로 되돌아갔다"
    # 주석의 「wiki」 언급은 이력이라 지우지 않는다 — **실행되는 코드**만 본다.
    code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
    assert 'Path(__file__)' not in code, "파일 경로로 되돌아갔다"
    assert '"wiki"' not in code, "wiki 경로 조립이 코드에 남아 있다"


def test_loaders_are_loud_on_failure():
    """조용한 실패 금지 — 실패하면 로그를 남긴다."""
    import inspect
    import open_proxy_mcp.services.proxy_advise as PA
    import open_proxy_mcp.services.law_lookup as LL
    for fn in (PA._load_law_layer_rules, PA._load_law_provisions, LL.load_index):
        src = inspect.getsource(fn)
        assert "logger." in src, f"{fn.__name__} 이 실패를 조용히 삼킨다"
