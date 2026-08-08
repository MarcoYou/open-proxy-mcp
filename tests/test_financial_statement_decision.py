"""재무제표 승인 판정 — 감사의견을 **읽고** 판정하는지.

자동 반대 3경로 중 「비적정 감사의견」이 오랫동안 도달 불가능한 죽은 코드였다. 호출부가
`scope="summary"` 로만 재무지표를 불러 `data["audit_opinion"]` 이 늘 비었고(그 필드는
`scope="audit_opinion"` 전용), 그래서 의견거절 회사와 적정 회사가 **같은 문장**을 받았다.
아무도 못 본 이유는 우량주 표본에서는 둘 다 찬성으로 보였기 때문이다 — 배선을 못박는다.
"""

from __future__ import annotations

import re
from pathlib import Path

from open_proxy_mcp.services.proxy_advise import (
    _audit_opinion_at_meeting,
    _decide_financial_statements,
)

_SRC = Path(__file__).resolve().parents[1] / "open_proxy_mcp" / "services" / "proxy_advise.py"


def _fm(status: str | None) -> dict:
    return {"data": {"summary": {"capital_impairment_status": status, "capital_impairment_ratio_pct": 43.0}}}


def _audit(opinion: str, *, rcept: str = "20230324000006", emphs: str = "") -> dict:
    return {"data": {"audit_opinion": {"opinions": [{
        "period_tag": "current", "stlm_dt": "2022-12-31", "adtor": "대주회계법인",
        "adt_opinion": opinion, "emphs_matter": emphs, "rcept_no": rcept,
    }]}}}


def test_proxy_advise_actually_asks_for_the_audit_opinion() -> None:
    """감사의견은 전용 scope 에서만 온다 — `summary` 로만 부르면 판정이 조용히 죽는다."""
    src = _SRC.read_text(encoding="utf-8")
    assert re.search(r'scope="audit_opinion"', src), "감사의견 upstream 이 사라졌다"


def test_a_disclaimer_of_opinion_is_a_vote_against() -> None:
    decision, reason = _decide_financial_statements(
        _fm("partial"), _audit("의견거절"), "2023-03-31", 2022
    )
    assert decision == "AGAINST"
    assert "의견거절" in reason and "대주회계법인" in reason


def test_partial_impairment_is_never_called_none() -> None:
    """부분 자본잠식을 「없음」이라 쓰면 같은 문서 사실란과 정면으로 어긋난다."""
    _, reason = _decide_financial_statements(_fm("partial"), _audit("적정"), "2023-03-31", 2022)
    assert "부분 자본잠식 43.0%" in reason
    assert "자본잠식 없음" not in reason


def test_fifty_percent_impairment_is_held_for_review() -> None:
    decision, reason = _decide_financial_statements(
        _fm("partial_50plus"), _audit("적정"), "2023-03-31", 2022
    )
    assert decision == "REVIEW"
    # 문구가 아니라 뜻을 고정한다. 단년도 50%와 2년 연속(상장폐지)은 구분해서 말해야 한다.
    assert "50%" in reason and "잠식" in reason
    assert "연속" in reason


def test_full_impairment_does_not_cite_a_market_it_never_checked() -> None:
    """KOSPI 회사에 「KOSDAQ 상장폐지 사유」를 근거로 달면 반대표가 틀린 법규 위에 선다."""
    decision, reason = _decide_financial_statements(_fm("full"), _audit("적정"), "2023-03-31", 2022)
    assert decision == "AGAINST"
    assert "KOSDAQ" not in reason


def test_an_opinion_filed_after_the_meeting_is_not_grounds_for_approval() -> None:
    """오스템 2021사업연도 감사보고서는 주총 뒤에 나왔고, 국일제지의 「적정」은 재감사분이다."""
    decision, reason = _decide_financial_statements(
        _fm("normal"), _audit("적정", rcept="20240222000111"), "2023-03-31", 2022
    )
    assert decision == "REVIEW"
    assert "주주총회 이후" in reason and "감사보고서" in reason


def test_a_restatement_is_named_as_one() -> None:
    """비덴트 2022사업연도 — 조회하면 「적정」이지만 주총 당시는 의견거절이었다."""
    decision, reason = _decide_financial_statements(
        _fm("normal"),
        _audit("적정", rcept="20241231000172", emphs="상장폐지사유 발생,\n재무제표 재작성"),
        "2023-03-31",
        2022,
    )
    assert decision == "REVIEW"
    assert "시점 불일치" in reason


def test_a_late_filing_is_not_reported_as_a_missing_one() -> None:
    """「사업보고서가 늦었다」와 「감사보고서가 없었다」는 다른 말이다 — 실측 현대차·KB금융 오탐."""
    _, reason = _decide_financial_statements(
        _fm("normal"), _audit("적정", rcept="20260401000001"), "2026-03-26", 2025
    )
    assert "확인하지 못했습니다" not in reason


def test_an_empty_opinion_cell_points_at_the_filing_it_found() -> None:
    """현대차 2025사업연도 — 행은 오는데 의견 칸이 비어 있다. 「못 찾았다」와 볼 곳이 다르다."""
    blank = _audit("", rcept="20260318001394")
    decision, reason = _decide_financial_statements(_fm("normal"), blank, "2026-03-26", 2025)
    assert decision == "REVIEW"
    assert "비어 있습니다" in reason and "20260318001394" in reason


def test_conflicting_opinions_resolve_to_the_worst_not_to_dart_row_order() -> None:
    """셀리버리 2022사업연도 = 결산일이 같은 3행(의견거절/적정/해당사항없음).

    정렬 키가 결산일 하나뿐이라 첫 행을 쓰면 DART 응답 순서가 판정을 정한다 — 순서가 바뀌는 날
    반대가 조용히 사라진다.
    """
    rows = [
        {"period_tag": "current", "stlm_dt": "2022-12-31", "adt_opinion": "적정",
         "adtor": "대주회계법인", "rcept_no": "20230324000006", "emphs_matter": ""},
        {"period_tag": "current", "stlm_dt": "2022-12-31", "adt_opinion": "의견거절",
         "adtor": "대주회계법인", "rcept_no": "20230324000006", "emphs_matter": ""},
    ]
    picked = _audit_opinion_at_meeting({"data": {"audit_opinion": {"opinions": rows}}}, "2023-03-31")
    assert picked["opinion"] == "의견거절"
    assert picked["conflict"] is True


def test_a_failed_lookup_is_not_blamed_on_the_company() -> None:
    """조회 실패는 우리 쪽 문제다 — 「사업보고서를 확인하라」고 하면 없는 문제를 찾으러 간다."""
    decision, reason = _decide_financial_statements(_fm("normal"), None, "2023-03-31", 2022)
    assert decision == "NO_DATA"
    assert "조회" in reason


def test_the_capital_impairment_year_matches_the_numbers() -> None:
    """자본잠식 값은 분석 기준연도(FY N-2) 것인데 라벨은 감사의견 연도(FY N-1)를 달고 있었다.

    자본잠식은 적자 누적으로 1년 사이 급변하는 항목이라 1년 어긋난 표기가 위험하다.
    """
    _, reason = _decide_financial_statements(
        _fm("normal"), _audit("적정"), "2026-03-26", 2025, fin_reference_year=2024,
    )
    assert "2024사업연도" in reason and "2025사업연도" not in reason.split("/")[-1]


def test_a_loss_year_is_not_the_same_as_having_no_dividend_capacity() -> None:
    """배당가능이익은 상법 §462① 로 **별도 재무제표** 기준이고, 당기 순손익이 아니다.

    누적 이익잉여금이 두터우면 당기 적자라도 배당은 적법하다(경기 하강기 제조업에 흔하다).
    실측 영풍: 당기 순손실 -2,521억이지만 이익잉여금 3.54조.
    """
    from open_proxy_mcp.services.proxy_advise import _decide_dividend

    def fm(ni, retained):
        return {"data": {"summary": {
            "net_income_krw": ni, "retained_earnings_krw": retained,
            "capital_impairment_status": "normal", "payout_ratio_pct": None}}}

    d, r = _decide_dividend("현금배당 승인의 건", fm(-252_140_554_081, 3_540_000_000_000), "테스트")
    assert d == "REVIEW"
    assert "이익잉여금" in r and "재원 자체는 있을 수 있습니다" in r
    assert "제462조제1항" in r and "별도" in r

    d2, r2 = _decide_dividend("현금배당 승인의 건", fm(-252_140_554_081, -800_000_000_000), "테스트")
    assert d2 == "REVIEW" and "누적 결손" in r2


def test_amounts_are_readable() -> None:
    """「-252,140,554,081원」은 사람이 자릿수를 못 센다."""
    from open_proxy_mcp.services.proxy_advise import _won
    assert _won(-252_140_554_081) == "-2,521억원"
    assert _won(3_602_707_444_005) == "3.60조원"
    assert _won(None) == "-"
