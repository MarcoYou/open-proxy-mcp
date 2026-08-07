"""정관변경 판정은 제목이 아니라 조문 본문을 읽는다.

회사는 제목을 완곡하게 쓴다 — 「이사 수 상한 설정의 건」·「이사회 규모 정상화」·「이사회 운영의
효율성 제고의 건」·「상법 개정에 따른 변경」. 제목만 보면 넷 다 위험 신호 0인데, 본문은 각각
정원 상한 신설·11인→9인·11인→7인·전자주주총회 배제다. **그 본문은 이미 fetch 해서 산출물에
첨부까지 하고 있었고, 판정에만 안 쓰였다.**
"""

from __future__ import annotations

import pytest

from open_proxy_mcp.services.proxy_advise import _articles_body_risks, _decide_articles_amendment


def _am(before: str, after: str) -> dict:
    return {"label": "테스트", "before": before, "after": after}


@pytest.mark.parametrize(
    ("label", "before", "after", "expect"),
    [
        (
            "카카오 — 「이사회 운영의 효율성 제고의 건」",
            "제 23조 (이사의 수) 1. 당 회사의 이사는 삼(3)인 이상 십일(11)인 이하로 두고, (생략)",
            "제 23조 (이사의 수) 1. 당 회사의 이사는 삼(3)인 이상 칠인(7)인 이하로 두고, (생략)",
            "이사 정원 상한 축소 (11인 → 7인)",
        ),
        (
            "한진칼 — 「이사회 규모 정상화」. 상한 표기가 「이내」다",
            "제30조(이사의 수) 이사는 3인 이상 11인 이내로 한다.",
            "제30조(이사의 수) 이사는 3인 이상 9인 이내로 한다.",
            "이사 정원 상한 축소 (11인 → 9인)",
        ),
        (
            "태광산업 — 없던 상한이 새로 생겼다",
            "제24조(이사의 수) ① 이사는 3인 이상 으로 하고, 사외이사는 이사 총수의 과반수로 한다.",
            "제24조(이사의 수) ① 이사는 3인 이상 7인 이하 로 하고, 독립이사는 이사 총수의 과반수로 한다.",
            "이사 정원 상한 신설 (7인)",
        ),
        (
            "가비아 — 제목은 「상법 개정에 따른 변경」인데 개정 방향의 정반대다",
            "제24조(소집지) 주주총회는 본점소재지에서 개최한다. <제2항 신설>",
            "제24조(소집지와 개최방식) ② 회사는 총회일에 주주가 소집지에 직접 출석하는 방식으로만 총회를 개최한다.",
            "전자주주총회 배제 — 대면 개최로 한정하는 조항",
        ),
        (
            "수권주식 증가",
            "제6조(발행할 주식의 총수) 발행할 주식의 총수는 1,600,000주로 한다.",
            "제6조(발행할 주식의 총수) 발행할 주식의 총수는 80,000,000주로 한다.",
            "수권주식 증가 (1,600,000주 → 80,000,000주)",
        ),
    ],
)
def test_the_clause_body_gives_up_what_the_title_hid(
    label: str, before: str, after: str, expect: str
) -> None:
    assert expect in _articles_body_risks(_am(before, after)), label


@pytest.mark.parametrize(
    ("label", "before", "after"),
    [
        ("목적사업 추가", "제2조(목적) 1~50. (생 략)", "제2조(목적) 51. 인쇄 및 인쇄관련 사업"),
        (
            "사외이사→독립이사 명칭 변경 — 정원(9인 이내)은 그대로다",
            "제25조(이사의 인원수) 대표이사 사장 1인과 9인 이내의 이사를 두며, 사외이사를 과반수로 한다.",
            "제25조(이사의 인원수) 대표이사 사장 1인과 9인 이내의 이사를 두며, 독립이사를 과반수로 한다.",
        ),
        ("정원 확대는 제한이 아니다", "이사는 3인 이상 7인 이내로 한다.", "이사는 3인 이상 11인 이내로 한다."),
    ],
)
def test_ordinary_amendments_are_not_flagged(label: str, before: str, after: str) -> None:
    assert _articles_body_risks(_am(before, after)) == [], label


def test_a_hidden_reduction_is_held_for_review() -> None:
    decision, reason = _decide_articles_amendment(
        "이사회 운영의 효율성 제고의 건 (제23조 제1항)",
        amendment=_am("이사는 삼(3)인 이상 십일(11)인 이하로 두고", "이사는 삼(3)인 이상 칠인(7)인 이하로 두고"),
    )
    assert decision == "REVIEW"
    assert "11인 → 7인" in reason


def test_the_reason_says_only_what_it_actually_checked() -> None:
    """조문을 보지도 않고 「이사 축소 … 없음」이라 안심시키던 문구 — 미탐지보다 나쁘다."""
    _, with_body = _decide_articles_amendment("목적사업 추가의 건", amendment=_am("제2조", "제2조 51. 인쇄업"))
    assert "제목과 조문 본문" in with_body
    _, title_only = _decide_articles_amendment("목적사업 추가의 건")
    assert "제목에서" in title_only and "조문 본문" not in title_only


def test_no_amendment_attached_is_not_a_clean_bill() -> None:
    assert _articles_body_risks(None) == []
    assert _articles_body_risks({"before": "", "after": ""}) == []
