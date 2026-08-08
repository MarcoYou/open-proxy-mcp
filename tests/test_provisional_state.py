"""사업보고서가 아직 안 나온 구간에서는 **공고의 잠정치(FY(N-1)P)**로 판단한다.

시장 전수(12월 결산 2,731사) 기준 사업보고서는 소집공고 +7일(중앙값)에 나오고, 상법 §363
(주총 2주 전 통지)과 겹치면 최소 81.7%가 주총 전에 확정된다. 남는 18% 구간에서는 확정치가
없는데, 그때 **2년 전** 확정치로 자본잠식을 판단하면 그 사이 감자·증자를 통째로 놓친다.
승인 대상 연도의 잠정치가 2년 전 확정치보다 가깝다 — 주주가 승인하려는 대상이 바로 그 숫자다.

다만 잠정치는 규정 판정이 아니다. 코스닥 해설서 자본잠식 **적용기준 ②**는 「감사보고서상
감사의견이 적정인 재무제표 기준 적용」이라, 감사 전 수치를 명시적으로 배제한다.
"""

from __future__ import annotations

from open_proxy_mcp.services.proxy_advise import (
    _capital_clause,
    _provisional_state_payload,
)


def _raw(**kw):
    return {"extraction_status": "success", **kw}


def test_it_builds_a_state_payload_from_the_notice() -> None:
    got = _provisional_state_payload(_raw(
        fy_current_capital_stock_krw=100_000_000_000,
        fy_current_controlling_equity_krw=40_000_000_000,
        fy_current_nci_krw=30_000_000_000,
        fy_current_total_equity_krw=70_000_000_000,
    ))
    s = got["data"]["summary"]
    assert s["capital_impairment_status"] == "partial_50plus"   # 지배 기준 60%
    assert s["capital_impairment_basis"] == "controlling"
    assert s["is_provisional"] is True


def test_a_failed_cross_check_yields_nothing() -> None:
    """지배 + 비지배 = 자본총계 가 어긋나면 라벨을 잘못 집은 것이다.

    소집공고에는 XBRL 계정 코드가 없어(실측 62건 중 0건) 확정치보다 오집 여지가 크다.
    실측 121건 중 검산 가능한 78건이 **전부** 성립했으므로, 어긋나는 쪽이 비정상이다.
    틀린 값으로 반대표를 권하느니 값을 내지 않는다.
    """
    assert _provisional_state_payload(_raw(
        fy_current_capital_stock_krw=100,
        fy_current_controlling_equity_krw=40,
        fy_current_nci_krw=30,
        fy_current_total_equity_krw=999,          # 40 + 30 ≠ 999
    )) is None


def test_no_denominator_means_no_judgment() -> None:
    """자본금을 못 뽑으면 잠식률 자체가 성립하지 않는다."""
    assert _provisional_state_payload(_raw(
        fy_current_controlling_equity_krw=40, fy_current_total_equity_krw=40)) is None
    assert _provisional_state_payload({"extraction_status": "no_data"}) is None
    assert _provisional_state_payload(None) is None


def test_net_income_is_not_carried_over() -> None:
    """공고는 연결 총액을, 확정치는 지배주주 귀속을 적는다 — 개념이 달라 섞으면 안 된다.

    배당성향 분모로 총액을 쓰면 성향이 낮게 나와 **과도 배당을 정상처럼** 보이게 한다.
    안전한 방향이 아니므로 비운 채로 두고, 배당 판단이 「확인 필요」로 가게 한다.
    """
    got = _provisional_state_payload(_raw(
        fy_current_capital_stock_krw=100,
        fy_current_total_equity_krw=80,
        fy_current_net_income_krw=36_600_000_000,
    ))
    assert "net_income_krw" not in got["data"]["summary"]


def test_the_sentence_says_it_is_pre_audit_and_not_a_listing_verdict() -> None:
    """규정 판정과 추정치를 구분하지 않으면 읽는 쪽이 관리종목 판정으로 받아들인다."""
    _, clause = _capital_clause({
        "capital_impairment_status": "partial_50plus",
        "capital_impairment_ratio_pct": 63.0,
        "capital_impairment_basis": "controlling",
        "is_provisional": True,
    }, "2025사업연도")
    assert "감사 전" in clause
    assert "관리종목 판정은 감사 후" in clause

    # 확정치일 때는 이 단서가 붙지 않는다 — 매번 붙으면 신호가 죽는다
    _, confirmed = _capital_clause({
        "capital_impairment_status": "partial_50plus",
        "capital_impairment_ratio_pct": 63.0,
        "capital_impairment_basis": "controlling",
    }, "2025사업연도")
    assert "감사 전" not in confirmed
