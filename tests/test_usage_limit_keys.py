# -*- coding: utf-8 -*-
"""사용량 블록의 DART 한도 칸 — 옛 키 유지 + 새 키 3개, 손으로 쓴 블록 금지. network 0콜.

- 옛 키 `dart_daily_limit_per_minute` 은 이름과 달리 **분당** 공표 한도(1000)다. 값·이름 모두 계약으로 유지한다.
- 새 키: `dart_limit_per_minute`(1000 공표), `dart_client_cap_per_minute`(910 OPM 상한), `dart_limit_per_day`(20000 하루).
- 사용량 블록은 `build_usage(n)` 한 곳에서만 만든다. 손 dict 가 다시 생기면 이 시험이 잡는다.
- 렌더러 문구는 새 키에서 읽는다(값을 바꿔 넣어 확인 — 1000·20000 을 문장에 박아 두지 않았다는 뜻).
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from open_proxy_mcp.dart.client import _API_RATE_LIMIT_PER_MINUTE
from open_proxy_mcp.services.contracts import build_usage
from open_proxy_mcp.tools import corp_gov_report, corporate_deals, risk_events

PKG = Path(__file__).resolve().parents[1] / "open_proxy_mcp"
_USAGE_KEYS = {"dart_api_calls", "dart_daily_limit_per_minute", "dart_limit_per_minute",
               "dart_client_cap_per_minute", "dart_limit_per_day"}


def test_build_usage_keeps_old_key_and_adds_new_keys():
    u = build_usage(7)
    assert u["dart_api_calls"] == 7
    assert u["mcp_tool_calls"] == 1
    # 옛 키: 이름은 daily 지만 값은 분당 공표 한도 — 바꾸지 않는다
    assert u["dart_daily_limit_per_minute"] == 1000
    assert u["dart_limit_per_minute"] == 1000
    assert u["dart_client_cap_per_minute"] == _API_RATE_LIMIT_PER_MINUTE == 910
    assert u["dart_limit_per_day"] == 20000


def test_usage_blocks_are_built_by_build_usage_only():
    """services·tools 어디에도 사용량 dict 를 손으로 쓰지 않는다(contracts.py 의 build_usage 만 예외)."""
    offenders = []
    for path in sorted(PKG.rglob("*.py")):
        if path == PKG / "services" / "contracts.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            keys = {k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if keys & _USAGE_KEYS:
                offenders.append(f"{path.relative_to(PKG)}:{node.lineno}")
    assert offenders == [], f"손으로 쓴 사용량 블록: {offenders}"


def _usage_line(md: str) -> str:
    return next(line for line in md.splitlines() if "DART API 호출" in line)


def _render_with_usage(render, usage: dict) -> str:
    payload = {"subject": "테스트", "data": {"usage": usage}}
    if render is risk_events._render:  # 리스크 이벤트 렌더러만 scope 인자가 없다
        return render(payload)
    return render(payload, "summary")


@pytest.mark.parametrize("render", [
    corporate_deals._render,
    corp_gov_report._render,
    risk_events._render,
])
def test_renderers_read_the_limits_from_the_usage_keys(render):
    usage = {"dart_api_calls": 3, "mcp_tool_calls": 1,
             "dart_limit_per_minute": 500, "dart_limit_per_day": 9000}
    md = _render_with_usage(render, usage)
    line = _usage_line(md)
    assert "3회" in line, line
    assert "분당 한도 500회(키당)" in line, line
    assert "하루 9,000회" in line, line


@pytest.mark.parametrize("render", [
    corporate_deals._render,
    corp_gov_report._render,
    risk_events._render,
])
def test_renderers_show_the_real_limits_for_build_usage_blocks(render):
    md = _render_with_usage(render, build_usage(2))
    line = _usage_line(md)
    assert "분당 한도 1,000회(키당)·하루 20,000회" in line, line
