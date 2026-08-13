# -*- coding: utf-8 -*-
"""운용사 정책 기본값이 판정을 덮는 범위. network 0콜.

**기본값은 판단이 서지 않은 자리를 채우는 것**이지 이미 선 판단을 지우는 것이 아니다.
260814 이전에는 `default=for` 가 조건 없이 FOR 로 덮어, 확정 사유까지 지웠다:
  · AGAINST — 완전 자본잠식·감사의견 거절·후보 결격
  · NO_DATA — 자료를 못 읽음 (없는 근거를 있다고 말하는 것)
  · NO_VOTE — **표결 자체가 없는 안건**(철회·상법 §449의2 보고사항)에 표를 냄

`default=against`/`review` 는 종전대로 REVIEW 로 **완화**한다 — 법령 강행규정이
아니면 자동 반대 대신 판단 재료를 애널리스트에게 넘긴다는 설계(문서 §0-A 정합표).
완화는 안전한 방향이라 그대로 둔다. 위험한 방향은 `for` 하나뿐이었다.
"""
from __future__ import annotations

import pytest

from open_proxy_mcp.services.proxy_advise import _apply_policy_default


@pytest.mark.parametrize("decision", ["AGAINST", "NO_DATA", "NO_VOTE"])
def test_for_default_never_overwrites_a_settled_call(decision):
    """정책 「기본 찬성」이 확정 판정을 지우면 안 된다."""
    out, reason = _apply_policy_default("for", decision, "원래 근거")
    assert out == decision, f"{decision} 이 정책 기본값에 덮였다 — 근거가 사라진다"
    assert reason == "원래 근거", "판정을 유지했는데 사유만 정책 문구로 바뀌면 근거가 끊긴다"


def test_for_default_fills_an_unsettled_call():
    """판단 보류(REVIEW)는 기본값이 채우는 자리다 — 이게 「기본값」의 뜻."""
    out, reason = _apply_policy_default("for", "REVIEW", "판단 보류")
    assert out == "FOR"
    assert "기본 입장이 찬성" in reason


def test_for_default_keeps_for():
    assert _apply_policy_default("for", "FOR", "근거")[0] == "FOR"


@pytest.mark.parametrize("decision", ["FOR", "AGAINST", "REVIEW", "NO_DATA", "NO_VOTE"])
def test_against_and_review_defaults_only_soften(decision):
    """반대·검토 기본값은 REVIEW 로 완화한다 — 안전한 방향이라 종전 동작 유지."""
    for pol in ("against", "review"):
        assert _apply_policy_default(pol, decision, "근거")[0] == "REVIEW"


def test_case_by_case_and_none_change_nothing():
    for pol in (None, "", "case_by_case"):
        assert _apply_policy_default(pol, "AGAINST", "근거") == ("AGAINST", "근거")
