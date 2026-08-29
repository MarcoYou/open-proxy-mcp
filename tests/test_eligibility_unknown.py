"""결격 칸이 **없는 서식**에서 「결격사유 없음」이라 말하지 않는다.

문서 : 주주총회소집공고 (E006)
위치 : 「제○호 의안 : 이사(감사) 선임의 건」 바로 아래 후보자 표
표   : 서식이 둘이다 (2026-08-29 표본 10사 실측)
       · 6칸형 (7/10) — 성명·생년월일·사외이사여부·감사위원여부·최대주주관계·추천인
         → **결격·체납·부실 칸이 아예 없다**
       · 8칸형 (2/10) — 위 + 주요약력·거래내역·체납·부실기업·결격사유

예전엔 세 칸이 전부 비어도 `has_red=False` 라 `clean` 이 나갔다. 그래서
「없다고 확인함」과 「물어본 적 없음」이 화면에 같은 글자로 찍혔다 — 겸직 「1곳」과 같은 병.
"""

from __future__ import annotations

from open_proxy_mcp.services.director_evaluation import evaluate_disqualification

_ADULT = {"birthDate": "1970-01-01"}


def test_no_eligibility_columns_is_unknown_not_clean() -> None:
    """6칸형 — 칸이 없어 값도 없다. 「없음」이라 말하지 않는다."""
    ev = evaluate_disqualification({**_ADULT}, 2026)
    assert ev["summary"] == "unknown_no_field"
    assert ev["sub_factors"]["eligibility"]["asked"] is False


def test_declared_none_is_clean() -> None:
    """8칸형 — 회사가 「무」라고 적었다. 이건 확인된 「없음」이다."""
    ev = evaluate_disqualification(
        {**_ADULT, "eligibility": {"taxDelinquency": "부", "insolventMgmt": "부",
                                   "legalDisqualification": "무"}}, 2026)
    assert ev["summary"] == "clean"
    assert ev["sub_factors"]["eligibility"]["asked"] is True


def test_declared_problem_is_red_flag() -> None:
    ev = evaluate_disqualification(
        {**_ADULT, "eligibility": {"legalDisqualification": "금고 이상의 형 확정"}}, 2026)
    assert ev["summary"] == "red_flag"


def test_partial_fill_still_counts_as_asked() -> None:
    """한 칸이라도 적혀 있으면 회사가 답한 것이다 — 나머지 결측으로 미상 처리하지 않는다."""
    ev = evaluate_disqualification(
        {**_ADULT, "eligibility": {"taxDelinquency": "없음"}}, 2026)
    assert ev["summary"] == "clean"
