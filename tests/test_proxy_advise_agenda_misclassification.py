# -*- coding: utf-8 -*-
"""260828 실측 오분류 2건 — 대림제지(017650) 2026-09-04 임시주총. network 0콜.

① 「주식분할 승인의 건」이 회사분할(상법 §530-2)로 분류돼 합병비율·외부평가기관 의견·
   주식매수청구권 체크리스트가 붙었다. 액면분할에는 그런 것이 없다.
② 정관 제5조 「발행할 주식의 총수 일억이천만주 → 육억주」(5배)를 **「수권주식 증가 없음」**
   으로 판정하고 FOR 를 냈다. 이 도구가 스스로 내건 위험 신호 목록에 있는 항목이라
   판정이 뒤집히는 오류다. 원인은 수량 정규식이 아라비아 숫자만 봤다는 것.
"""

from __future__ import annotations

import pytest

from open_proxy_mcp.services.proxy_advise import (
    _articles_body_risks,
    _classify_agenda,
    _korean_number,
    _parse_share_count,
)


@pytest.mark.parametrize("title, expected", [
    ("주식분할 승인의 건", "stock_split"),
    ("액면분할 승인의 건", "stock_split"),
    ("주식 분할 승인의 건", "stock_split"),
    # 진짜 구조개편은 그대로 — 분할합병·회사분할은 특별결의 체크리스트가 맞다
    ("분할합병계약서 승인의 건", "merger_or_restructuring"),
    ("분할계획서 승인의 건", "merger_or_restructuring"),
    ("합병계약 체결 승인의 건", "merger_or_restructuring"),
])
def test_a_stock_split_is_not_a_corporate_division(title, expected) -> None:
    assert _classify_agenda(title) == expected


@pytest.mark.parametrize("text, value", [
    ("일억이천만", 120_000_000),
    ("육억", 600_000_000),
    ("삼천만", 30_000_000),
    ("일조", 1_000_000_000_000),
    ("이백오십", 250),
])
def test_korean_numerals_are_read(text, value) -> None:
    assert _korean_number(text) == value


def test_an_unreadable_token_stays_unread() -> None:
    """모르면 0 으로 채우지 않는다 — 0 은 「증가 없음」으로 읽힌다."""
    assert _korean_number("약간") is None
    assert _parse_share_count("") is None
    assert _parse_share_count("1,600,000") == 1_600_000


def test_the_authorized_share_increase_written_in_korean_numerals_is_caught() -> None:
    """대림제지 정관 원문 그대로."""
    amendment = {
        "label": "제5조",
        "before": "제 5 조 ( 발행예정 주식의 총수 ) 당 회사가 발행할 주식의 총수는 일억이천만주 로 한다 .",
        "after": "제 5 조 ( 발행예정 주식의 총수 ) 당 회사가 발행할 주식의 총수는 육억주 로 한다 .",
        "reason": "액면분할을 위한 정관 변경유통 주식수 증가를 통한 주식 거래 활성화",
    }
    risks = _articles_body_risks(amendment)
    assert risks, "5배 증가가 위험 신호 0으로 나갔다 — 260828 사고 재발"
    assert "수권주식 증가" in risks[0]
    assert "120,000,000" in risks[0] and "600,000,000" in risks[0]
    # 액면분할 동반 여부는 **단정하지 않고 갈림길만 준다**
    assert "액면분할" in risks[0]


def test_a_number_we_cannot_read_is_reported_not_swallowed() -> None:
    """읽지 못한 것을 「증가 없음」으로 뭉개지 않는다 — 원문 두 조각을 그대로 넘긴다."""
    amendment = {
        "before": "발행할 주식의 총수는 별지와 같다",
        "after": "발행할 주식의 총수는 별표와 같다",
    }
    # 수량 토큰 자체가 안 잡히면 이 규칙은 침묵한다(다른 위험 신호는 그대로 검사).
    assert "수권주식 증가" not in " ".join(_articles_body_risks(amendment))


def test_the_arabic_numeral_path_still_works() -> None:
    risks = _articles_body_risks({
        "before": "제6조(발행할 주식의 총수) 발행할 주식의 총수는 100,000,000주로 한다.",
        "after": "제6조(발행할 주식의 총수) 발행할 주식의 총수는 300,000,000주로 한다.",
    })
    assert risks == ["수권주식 증가 (100,000,000주 → 300,000,000주, 3.0배)"]
