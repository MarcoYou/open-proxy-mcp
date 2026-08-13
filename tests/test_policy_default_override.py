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


def test_for_default_does_not_overwrite_review_either():
    """**260814 2차 — 판단이 바뀌었다.**

    아침에는 「기본값은 판단이 안 선 자리를 채운다」로 REVIEW 만 덮게 뒀다.
    그런데 그 REVIEW 가 두 뜻을 겸하고 있었다:
      ① 사람이 봐야 한다   자본잠식·감사의견 미확인·자사주 처분 — **사실**이 만든 것
      ② OPM 기준으로 걸었다 소진율 30%·배당성향 200% — 우리 임계가 만든 것
    ②만 덮는 게 옳지만 둘을 가르는 표시를 REVIEW 75곳에 손으로 달면 또 이중장부이고,
    실측상 ①이 대부분이라 덮으면 증거가 사라진다.

    판정은 그대로 두고 정책 입장은 **사유에 덧붙여** 보여준다 — 사용자가 둘을 보고
    고르는 편이, 우리가 대신 골라 한쪽을 지우는 것보다 낫다.
    """
    out, reason = _apply_policy_default("for", "REVIEW", "소진율 22% — 한도 적정성 검토")
    assert out == "REVIEW", "정책 기본값이 사실 기반 검토를 덮었다"
    assert "소진율 22%" in reason, "원래 검토 사유가 사라졌다"
    assert "정책의 기본 입장은 찬성" in reason, "정책 입장이 보이지 않는다"


def test_for_default_is_now_informational_only():
    """정책 「기본 찬성」은 이제 **어떤 판정도 바꾸지 않는다** — 정보로만 남는다.
    바꾸려면 REVIEW 의 두 뜻을 코드가 먼저 갈라야 한다."""
    for decision in ("FOR", "AGAINST", "REVIEW", "NO_DATA", "NO_VOTE"):
        assert _apply_policy_default("for", decision, "근거")[0] == decision, decision


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


def test_for_default_keeps_the_engine_reason_when_both_say_for():
    """판정이 같으면 **근거는 엔진 것을 남긴다.**

    실측 지역난방 제1호 — 종전에는 사유까지 「적용 정책의 기본 입장이 찬성」으로 덮어써서
    「감사의견 적정 / 자본잠식 없음」이라는 실제 근거가 화면에서 사라졌다. 판정은 안 바뀌는데
    근거만 날아가는 형태라 눈에 안 띈다. 정책 입장은 「적용 정책」 줄이 따로 싣는다.
    """
    engine_reason = "감사의견 적정(2025사업연도, 삼일회계법인) / 자본잠식 없음"
    out, reason = _apply_policy_default("for", "FOR", engine_reason)
    assert out == "FOR"
    assert reason == engine_reason, "정책이 엔진 근거를 덮어썼다"
