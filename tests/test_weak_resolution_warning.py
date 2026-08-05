"""이름이 정확히 맞지 않아 추정으로 고른 기업은 응답이 그 사실을 밝혀야 한다.

해석기는 `confidence` 를 이미 만들지만 `company` tool 만 그것을 보여 주고 나머지 23개
서비스가 전부 버리고 있었다 — 「지에스」가 「지에스이」로 조용히 바뀌어도 알 수 없었다.
"""

from __future__ import annotations

import pytest

from open_proxy_mcp.dart.client import (
    _LEDGER_MAX_WEAK,
    _ctx_ledger,
    new_request_ledger,
    note_weak_resolution,
    weak_resolutions,
)
from open_proxy_mcp.services.company import _resolve_match
from open_proxy_mcp.services.contracts import AnalysisStatus, ToolEnvelope


@pytest.fixture(autouse=True)
def _ledger():
    new_request_ledger()
    yield
    _ctx_ledger.set(None)


def _corp(name: str, kind: str, inferred: bool, candidates: int = 1) -> dict:
    return {
        "corp_name": name,
        "corp_code": "00000001",
        "stock_code": "000001",
        "_resolution": {
            "match_kind": kind,
            "inferred": inferred,
            "auto_selected": True,
            "candidate_count": candidates,
        },
    }


def _envelope() -> dict:
    return ToolEnvelope(tool="t", status=AnalysisStatus.EXACT, warnings=["기존 경고"]).to_dict()


def test_inferred_pick_is_declared_in_the_response() -> None:
    _resolve_match("지에스", [_corp("지에스이", "substring", True)])
    warning = _envelope()["warnings"][0]
    assert "「지에스」" in warning
    assert "지에스이" in warning
    assert "추정" in warning


def test_existing_warnings_are_kept_and_come_after() -> None:
    _resolve_match("지에스", [_corp("지에스이", "substring", True)])
    assert _envelope()["warnings"][-1] == "기존 경고"


def test_exact_name_match_says_nothing() -> None:
    _resolve_match("삼성전자", [_corp("삼성전자", "official", False)])
    assert _envelope()["warnings"] == ["기존 경고"]


def test_ticker_lookup_says_nothing() -> None:
    _resolve_match("005930", [_corp("삼성전자", "ticker", False)])
    assert _envelope()["warnings"] == ["기존 경고"]


def test_other_candidates_are_counted() -> None:
    _resolve_match("엘에이", [_corp("LG디스플레이", "substring", True, candidates=94)])
    assert "다른 후보 93곳" in _envelope()["warnings"][0]


def test_a_copy_carried_up_from_an_inner_tool_is_not_repeated() -> None:
    """감싸는 tool 이 안쪽 응답의 경고를 「[notice] 」를 붙여 옮긴다 — 같은 문장이다."""
    _resolve_match("지에스", [_corp("지에스이", "substring", True)])
    line = ToolEnvelope(tool="t", status=AnalysisStatus.EXACT).to_dict()["warnings"][0]
    outer = ToolEnvelope(
        tool="t", status=AnalysisStatus.EXACT, warnings=[f"[notice] {line}", "다른 경고"]
    ).to_dict()
    assert outer["warnings"] == [line, "다른 경고"]


def test_the_same_query_is_not_reported_twice() -> None:
    for _ in range(3):
        _resolve_match("지에스", [_corp("지에스이", "substring", True)])
    assert len(weak_resolutions()) == 1


def test_a_market_wide_scan_cannot_flood_the_response() -> None:
    for i in range(_LEDGER_MAX_WEAK + 5):
        note_weak_resolution(f"질의{i}", f"회사{i}", "substring", 1)
    assert len(_envelope()["warnings"]) == _LEDGER_MAX_WEAK + 1


def test_without_a_ledger_nothing_breaks() -> None:
    """스크립트·테스트는 미들웨어를 안 거친다 — 조용히 통과해야 한다."""
    _ctx_ledger.set(None)
    note_weak_resolution("지에스", "지에스이", "substring", 1)
    assert weak_resolutions() == []
    assert _envelope()["warnings"] == ["기존 경고"]
