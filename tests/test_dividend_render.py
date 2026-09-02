# -*- coding: utf-8 -*-
"""dividend 의 **md 경로를 실제로 실행**한다. network 0콜.

260804: `_TREND_KO` 를 조회만 하고 정의하지 않아 md 경로가 통째로 NameError 로 죽었는데
기존 테스트가 전부 통과했다 — 렌더러를 **한 번도 호출하지 않았기 때문**이다. payload(json)
계약만 보는 테스트는 사람이 실제로 받는 문자열을 검증하지 않는다. 여기서는 tool 함수를
format=md · json 양쪽으로 호출해 분기 자체를 태운다(payload 는 대역, DART 호출 없음).
"""
from __future__ import annotations

import asyncio
import ast
import inspect
import json
import re
import textwrap

import pytest

from open_proxy_mcp.services.contracts import ToolEnvelope
from open_proxy_mcp.services.dividend import _SUPPORTED_SCOPES, _policy_signals
from open_proxy_mcp.tools import dividend_disclosure as dividend_tool

_LATIN = re.compile(r"[A-Za-z]")


def _trend_ko() -> dict:
    """사전을 **모듈 최상단에서 import 하지 않는다** — 없으면 수집 단계에서 죽어 정작
    「렌더가 터진다」는 사실이 가려진다(사전 삭제 변이로 확인). 프로덕션과 같은 증상으로
    실패하게 둔다."""
    ko = getattr(dividend_tool, "_TREND_KO", None)
    assert isinstance(ko, dict), "tools/dividend.py 에 _TREND_KO 사전이 없다"
    return ko


# 실측 형태(삼성전자류 분기배당사)를 축약한 history — 최신연도가 직전 대비 +5% 초과라 increasing.
_HISTORY = [
    {"year": 2023, "annual_dps": 1444, "decision_count": 4, "payout_ratio": 17.0,
     "yield_pct": 2.1, "has_special": False, "pattern": "분기배당"},
    {"year": 2024, "annual_dps": 1444, "decision_count": 4, "payout_ratio": 25.0,
     "yield_pct": 2.5, "has_special": False, "pattern": "분기배당"},
    {"year": 2025, "annual_dps": 1800, "decision_count": 4, "payout_ratio": 30.0,
     "yield_pct": 2.8, "has_special": True, "pattern": "분기배당"},
]


def _payload(scope: str, *, history: list[dict] | None = None) -> dict:
    """services/dividend.py 가 scope 별로 싣는 키만 실어 준다(1063~1160행 대조)."""
    hist = _HISTORY if history is None else history
    data: dict = {
        "query": "테스트회사",
        "canonical_name": "테스트회사",
        "stock_code": "005930",
        "year": 2025,
        "window": {"start_date": "2023-01-01", "end_date": "2025-12-31"},
        "summary": {
            "fiscal_year": 2025, "cash_dps": 1800, "cash_dps_preferred": 1801,
            "total_amount_mil": 9_800_000, "payout_ratio_dart": 30.0, "yield_dart": 2.8,
            "yield_current_pct": 2.4, "yield_current_price_krw": 75_000,
            "yield_current_price_date": "2026-08-03",
            "pre_dividend_post_resolution": True, "capital_reserve_reduction": True,
        },
    }
    if scope in {"summary", "detail"}:
        data["latest_decisions"] = [{
            "rcept_dt": "2026-02-05", "dividend_type": "결산배당", "dps_common": 450,
            "record_date": "2026-03-31", "rcept_no": "20260205000123",
        }]
    if scope == "summary":
        data["policy_signals"] = _policy_signals(hist)
    if scope == "history":
        data["history"] = hist
        data["policy_signals"] = _policy_signals(hist)
        data["quarterly_full"] = [
            {"quarter": "Q1", "dps_common": 450, "dps_preferred": 451, "total_mil": 2_450_000},
            {"quarter": "Q2", "dps_common": 450, "dps_preferred": 451, "total_mil": 2_450_000},
        ]
        data["quarterly_breakdown"] = [{
            "year": 2025, "quarter": "Q1", "dps_common_krw": 450, "dps_preferred_krw": 451,
            "record_date": "2025-03-31", "rcept_no": "20250404000456",
            "is_amendment": False, "is_superseded": False,
        }]
    return ToolEnvelope(
        tool="dividend", status="ok", subject="테스트회사",
        warnings=["샘플 경고 — 유의사항 블록도 태운다"], data=data,
    ).to_dict()


class _Registry:
    """FastMCP 대역 — `@mcp.tool()` 데코레이터 계약만 흉내내 tool 함수를 붙잡아 둔다."""

    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self, *args, **kwargs):
        def deco(fn):
            self.tools[fn.__name__] = fn
            return fn
        return deco


def _call(monkeypatch, payload: dict, **kwargs) -> str:
    """실제 tool 함수를 호출한다 — format 분기까지 프로덕션과 같은 경로로 탄다."""
    async def _fake_payload(*_a, **_k):
        return payload
    monkeypatch.setattr(dividend_tool, "build_dividend_payload", _fake_payload)
    reg = _Registry()
    dividend_tool.register_tools(reg)
    return asyncio.run(reg.tools["dividend_disclosure"](company="테스트회사", **kwargs))


@pytest.mark.parametrize("scope", sorted(_SUPPORTED_SCOPES))
def test_markdown_path_renders_for_every_supported_scope(monkeypatch, scope):
    """scope 마다 md 를 끝까지 만들어 본다 — 어느 분기의 미정의 이름도 여기서 터진다."""
    out = _call(monkeypatch, _payload(scope), scope=scope, format="md")
    assert out.startswith("# 테스트회사 배당"), out[:80]
    assert "## 연간 요약 (FY2025)" in out
    assert "- 종목코드 005930" in out
    assert "샘플 경고" in out
    if scope == "summary":
        assert "## 정책 신호" in out and "- 추세: 증가" in out
    if scope in {"summary", "detail"}:
        assert "## 최근 배당결정" in out and "20260205000123" in out
    if scope == "history":
        assert "## 최근 연도 추이" in out and "1,800원" in out
        assert "## 최신연도 분기별 (정기보고서 누적차분)" in out


def test_markdown_path_survives_a_company_with_no_dividend_history(monkeypatch):
    """이력이 없으면 `insufficient_data` 가 온다 — 사전에 없으면 영문이 그대로 나갔다."""
    out = _call(monkeypatch, _payload("summary", history=[]), scope="summary", format="md")
    assert "- 추세: 판단 불가 (확정된 배당 이력 없음)" in out
    assert "insufficient_data" not in out


def test_json_path_still_returns_the_raw_envelope(monkeypatch):
    """json 은 렌더러를 타지 않는다 — 한글화가 payload 계약을 건드리지 않았는지 함께 본다."""
    out = _call(monkeypatch, _payload("summary"), scope="summary", format="json")
    got = json.loads(out)
    assert got["status"] == "ok"
    assert got["data"]["policy_signals"]["trend"] == "increasing"   # enum 은 enum 대로 남는다


def _producer_trend_values() -> set[str]:
    """`_policy_signals()` 가 실제로 뱉는 trend 리터럴 — 사전을 손으로 고르면 절반이 샌다."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(_policy_signals)))
    out: set[str] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
                and any(isinstance(t, ast.Name) and t.id == "trend" for t in node.targets)):
            out.add(node.value.value)                      # `trend = "increasing"`
        if isinstance(node, ast.Dict):                     # `{"trend": "insufficient_data"}`
            for key, val in zip(node.keys, node.values):
                if (isinstance(key, ast.Constant) and key.value == "trend"
                        and isinstance(val, ast.Constant) and isinstance(val.value, str)):
                    out.add(val.value)
    return out


def test_trend_dictionary_matches_the_producer_exactly():
    """양방향으로 본다 — 누락은 영문 노출, 잉여 키는 「있는 줄 알았던」 오해(`flat` 등)다."""
    produced = _producer_trend_values()
    assert produced, "producer 리터럴을 못 읽었다 — 테스트가 무력화됐다"
    ko_map = _trend_ko()
    assert produced == set(ko_map), {
        "사전에 없는 producer 값": sorted(produced - set(ko_map)),
        "producer 에 없는 사전 키": sorted(set(ko_map) - produced),
    }
    for key, ko in ko_map.items():
        assert not _LATIN.search(ko), f"{key} → {ko}"


@pytest.mark.parametrize("trend", sorted(_producer_trend_values()))
def test_no_trend_enum_reaches_the_reader(monkeypatch, trend):
    payload = _payload("summary")
    payload["data"]["policy_signals"]["trend"] = trend
    out = _call(monkeypatch, payload, scope="summary", format="md")
    assert f"- 추세: {_trend_ko()[trend]}" in out
    assert trend not in out
