"""계정 매칭이 **행 순서에 기대지 않도록** 한다.

`_match_account`는 부분문자열 매칭이다. 그래서 서로 올라타는 조합이 있다:
    「유동자산」  ⊂ 「비유동자산」
    「유동부채」  ⊂ 「비유동부채」
    「자본총계」  ⊂ 「부채및자본총계」   ← 이건 자산총계다
    「자본금」    ⊂ 「우선주자본금」

지금 이것들이 안 터지는 유일한 이유는 DART 가 표준 순서로 행을 주고 첫 매칭이 이기기
때문이다. 순서가 하나만 달라져도 유동자산 자리에 비유동자산이 들어가고 비유동자산은 빈 채로
남는다 — 값이 비는 게 아니라 **다른 값이 들어앉는** 종류라 검산으로도 잘 안 걸린다.
"""

from __future__ import annotations

from open_proxy_mcp.services.financial_metrics import _build_account_map


def _bs(*pairs: tuple[str, int]) -> list[dict]:
    return [{"sj_div": "BS", "account_nm": nm, "thstrm_amount": str(v)} for nm, v in pairs]


def test_non_current_first_does_not_steal_the_current_slot() -> None:
    """비유동이 먼저 와도 유동자산 자리를 뺏지 않는다."""
    out = _build_account_map(_bs(("비유동자산", 700), ("유동자산", 300)))
    assert out["current_assets"] == 300
    assert out["non_current_assets"] == 700


def test_the_same_holds_for_liabilities() -> None:
    out = _build_account_map(_bs(("비유동부채", 500), ("유동부채", 200)))
    assert out["current_liabilities"] == 200
    assert out["non_current_liabilities"] == 500


def test_total_equity_never_takes_the_assets_line() -> None:
    """「부채및자본총계」는 자산총계다 — 자기자본으로 읽으면 잠식 판정이 통째로 무의미해진다."""
    out = _build_account_map(_bs(("부채및자본총계", 1000), ("자본총계", 400)))
    assert out["total_equity"] == 400

    # 진짜 자본총계 행이 아예 없으면 값을 만들어내지 말고 비운다
    only_sum = _build_account_map(_bs(("부채와자본총계", 1000)))
    assert only_sum["total_equity"] is None


def test_a_share_class_alone_is_not_the_capital_stock() -> None:
    """우선주자본금은 자본금의 **일부**다. 단독으로 쓰면 분모가 반쪽이 되어 잠식률이 부풀어 오른다."""
    out = _build_account_map(_bs(("우선주자본금", 119), ("보통주자본금", 778), ("자본금", 897)))
    assert out["capital_stock"] == 897          # 부모 행이 있으면 그것


def test_share_classes_are_summed_when_there_is_no_parent_row() -> None:
    """부모 행이 없는 표도 있다 — 하나만 집으면 반쪽이라, 합쳐야 자본금이다.

    실측 삼성전자 소집공고는 「Ⅰ.자본금 897,514 = 우선주 119,467 + 보통주 778,047」로 부모가
    있지만, 부모 없이 종류별로만 적는 표가 존재한다. 자본금은 자본잠식률의 분모라 반쪽이면
    잠식률이 두 배가 되고 50% 선을 넘나든다.
    """
    out = _build_account_map(_bs(("우선주자본금", 119), ("보통주자본금", 778)))
    assert out["capital_stock"] == 897
