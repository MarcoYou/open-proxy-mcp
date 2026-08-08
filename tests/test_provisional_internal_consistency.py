"""본문 잠정 재무제표는 **안에서도** 맞아야 한다.

API 검산은 매출·영업이익만 맞댄다. 순이익을 빼둔 건 의도된 것이다 — 본문은 총 순이익, API 는
지배주주 귀속이라 개념이 달라 비율이 정상적으로 수십 배까지 벌어진다. 그래서 순이익은 그물을 통째로
빠져나갔고, 영풍 2026 회차는 본문 당기 순이익이 3조 6,027억(실제 309억, **116배**)인데
「본문 파싱 정상」이라고 선언됐다.

본문 안에서의 정합성은 그 개념 차이를 타지 않는다 — 같은 표에서 뽑은 숫자끼리 보기 때문이다.
"""

from __future__ import annotations

import pytest

from open_proxy_mcp.services.proxy_advise import (
    _cross_check_provisional_revenue,
    _internal_consistency,
)


def test_net_income_above_revenue_is_caught() -> None:
    """영풍 실측 — 순이익이 매출보다 크면 파싱을 의심한다."""
    issues = _internal_consistency({
        "fy_current_net_income_krw": 3_602_700_000_000,
        "fy_current_revenue_krw": 2_908_900_000_000,
    })
    assert issues and "순이익" in issues[0] and "매출" in issues[0]


def test_a_one_off_gain_near_revenue_is_not_flagged() -> None:
    """대규모 처분이익이면 순이익이 매출에 근접할 수 있다 — 파싱 사고는 배 단위로 벌어진다."""
    assert _internal_consistency({
        "fy_current_net_income_krw": 1_050_000_000_000,
        "fy_current_revenue_krw": 1_000_000_000_000,
    }) == []


def test_ordinary_company_is_quiet() -> None:
    assert _internal_consistency({
        "fy_current_net_income_krw": 110_230_000_000,
        "fy_current_revenue_krw": 6_579_700_000_000,
    }) == []


def test_balance_sheet_identity_is_checked() -> None:
    """자산 = 부채 + 자본. 표를 잘못 읽으면 여기서 어긋난다."""
    assert _internal_consistency({
        "fy_current_total_assets_krw": 14_189_100_000_000,
        "fy_current_total_liabilities_krw": 4_852_900_000_000,
        "fy_current_total_equity_krw": 3_336_100_000_000,
    })
    assert _internal_consistency({
        "fy_current_total_assets_krw": 14_189_100_000_000,
        "fy_current_total_liabilities_krw": 4_852_900_000_000,
        "fy_current_total_equity_krw": 9_336_100_000_000,
    }) == []


@pytest.mark.parametrize("payload", [None, {}, {"fy_current_revenue_krw": None}])
def test_missing_numbers_are_not_an_error(payload) -> None:
    assert _internal_consistency(payload) == []


def test_cross_check_no_longer_declares_the_body_sound() -> None:
    """매출 하나 맞았다고 「본문 파싱 정상」이라 하지 않는다 — 확인한 범위만 말한다."""
    msg = _cross_check_provisional_revenue(
        {"fy_prior_revenue_krw": 1_000_000_000_000,
         "fy_current_net_income_krw": 50_000_000_000,
         "fy_current_revenue_krw": 1_100_000_000_000},
        {"revenue_krw": 1_000_000_000_000},
    )
    assert msg and "정상" not in msg
    assert "대조 항목" in msg


def test_internal_problem_surfaces_even_when_revenue_matches() -> None:
    """매출은 맞는데 순이익이 틀린 경우 — 영풍이 정확히 이 모양이었다."""
    msg = _cross_check_provisional_revenue(
        {"fy_prior_revenue_krw": 2_790_000_000_000,
         "fy_current_revenue_krw": 2_908_900_000_000,
         "fy_current_net_income_krw": 3_602_700_000_000},
        {"revenue_krw": 2_790_000_000_000},
    )
    assert msg and "신뢰하지 마시고" in msg and "순이익" in msg


def test_the_mismatch_says_where_the_number_came_from() -> None:
    """값만 주면 무엇을 잘못 집었는지 알 수 없다 — 영풍은 순이익이 재무상태표 자본에서 왔다."""
    msg = _cross_check_provisional_revenue(
        {"fy_prior_revenue_krw": 2_787_414_358_375,
         "fy_current_revenue_krw": 2_908_974_321_148,
         "fy_current_net_income_krw": 3_602_707_444_005,
         "source_accounts": {
             "net_income_krw": {"account": "I. 지배기업 소유주지분",
                                "statement": "balance_sheet", "scope": "consolidated"},
         }},
        {"revenue_krw": 2_787_414_358_375},
    )
    assert "추출 위치" in msg
    assert "지배기업 소유주지분" in msg and "재무상태표" in msg


def test_mixing_consolidated_and_separate_is_disclosed() -> None:
    """손익은 연결, 재무상태는 별도에서 오는 경우가 있다 — 그대로 비율을 내면 안 된다.

    `scope_used` 는 마지막 하나만 담아 한쪽으로만 보고됐다. metric 별 출처가 `source_accounts` 에
    있으니 그것으로 판정한다. 실측 2건(삼성화재·NH투자증권형).
    """
    from open_proxy_mcp.services.provisional_financial_statement import extract_metrics

    parsed = {
        "consolidated": {"income_statement": {
            "unit": "원", "columns": ["account", "current", "prior"],
            "rows": [["Ⅰ.매출액", "1,000,000,000,000", "900,000,000,000"],
                     ["Ⅶ.당기순이익", "50,000,000,000", "40,000,000,000"]]}, "balance_sheet": None},
        "separate": {"income_statement": None, "balance_sheet": {
            "unit": "원", "columns": ["account", "current", "prior"],
            "rows": [["자산총계", "3,000,000,000,000", "2,800,000,000,000"],
                     ["부채총계", "1,000,000,000,000", "900,000,000,000"],
                     ["자본총계", "2,000,000,000,000", "1,900,000,000,000"]]}},
    }
    m = extract_metrics(parsed)
    assert m.get("scope_mixed") == ["consolidated", "separate"]


def test_a_single_scope_is_not_flagged() -> None:
    from open_proxy_mcp.services.provisional_financial_statement import extract_metrics

    parsed = {
        "consolidated": {"income_statement": {
            "unit": "원", "columns": ["account", "current", "prior"],
            "rows": [["Ⅰ.매출액", "1,000,000,000,000", "900,000,000,000"],
                     ["Ⅶ.당기순이익", "50,000,000,000", "40,000,000,000"],
                     ["자산총계", "3,000,000,000,000", "2,800,000,000,000"]]}, "balance_sheet": None},
        "separate": {"income_statement": None, "balance_sheet": None},
    }
    assert extract_metrics(parsed).get("scope_mixed") is None
