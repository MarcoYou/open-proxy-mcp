"""추천 사유가 「남의 것」으로 새지 않는지 — 구간 분할 회귀 테스트.

배경: 「마. 후보자에 대한 이사회의 추천 사유」는 한 구간에 후보 전원의 사유를 담는
서식이 많다. 종전엔 그 구간 전체를 후보 전원에게 복사했고, 렌더가 240자에서 자르는
탓에 후보 8명이 모두 첫 후보의 문단을 자기 사유로 달고 나갔다(고려아연 2026 실측).
공고는 이름으로 구간을 스스로 선언하므로 그 선언을 읽어 가른다.
갈리지 않으면 확정하지 않고 밝힌다 — 남의 문장을 이 후보 것으로 단정하지 않는다.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from open_proxy_mcp.services.shareholder_meeting_parser import (  # noqa: E402
    _split_recommendation_reason,
    _strip_reason_tail_noise,
    parse_personnel_xml,
)


# ── 원문이 이름으로 구간을 선언한 서식 (411건 캐시 소집공고 실측 형태) ──────────

def test_splits_bullet_declared_sections_koreazinc_shape():
    """고려아연 2026: 「- 이름 후보자」 표지 뒤에 각자 문단."""
    text = (
        "- 최윤범 후보자\n최윤범 후보자는 고려아연 회장으로서 신성장 동력 발굴을 주도하였음.\n"
        "- 황덕남 후보자\n황덕남 후보자는 서울고등법원 법관 등을 역임한 법률 전문가임.\n"
        "- Walter Field McLallen 후보자\n주주제안에 의한 후보자에 해당하여, 추천사유는 기재를 생략함."
    )
    names = ["최윤범", "황덕남", "Walter Field McLallen"]

    segs = _split_recommendation_reason(text, names)

    assert set(segs) == set(names)
    assert "회장" in segs["최윤범"] and "법관" not in segs["최윤범"]
    assert "법관" in segs["황덕남"] and "회장" not in segs["황덕남"]
    assert "주주제안" in segs["Walter Field McLallen"]


def test_splits_inline_bracket_sections():
    """한 줄에 이어 붙은 「[이름 후보자]…」 서식도 갈라야 한다."""
    text = ("[김의형 후보자]본 후보자는 삼일회계법인 대표를 역임한 회계 전문가로 추천함"
            "[최희정 후보자]본 후보자는 서울대학교 교수로 생명과학 분야 전문가로 추천함")

    segs = _split_recommendation_reason(text, ["김의형", "최희정"])

    assert "회계법인" in segs["김의형"] and "서울대" not in segs["김의형"]
    assert "서울대" in segs["최희정"]


def test_splits_when_next_section_opens_after_sentence_end():
    """마침표 뒤에 이름으로 다음 구간이 열리는 서식(불릿·괄호 없음)."""
    text = ("곽재선 사내이사 후보자는 KG그룹 회장으로서 기업가치 제고에 기여할 것으로 판단함."
            "곽정현 사내이사 후보자는 그룹 경영 전반에 대한 이해를 축적하였음.")

    segs = _split_recommendation_reason(text, ["곽재선", "곽정현"])

    assert "KG그룹 회장" in segs["곽재선"] and "곽정현" not in segs["곽재선"]
    assert "경영 전반" in segs["곽정현"]


def test_sentence_ending_is_not_mistaken_for_list_numbering():
    """'…추천하였습니다.' 의 '다.' 를 「가.나.다.」 번호로 읽으면 경계 판정이 깨진다."""
    text = ("<송종국 사내이사 후보자>본 후보자는 의료 분야 경영 경험이 풍부하여 추천하였습니다. "
            "<김석진 사내이사 후보자>본 후보자는 화장품 분야 경영 경험이 풍부하여 추천하였습니다.")

    segs = _split_recommendation_reason(text, ["송종국", "김석진"])

    assert "의료" in segs["송종국"] and "화장품" not in segs["송종국"]
    assert "화장품" in segs["김석진"]


def test_splits_names_whose_spacing_differs_from_table():
    """표는 '한 승 희'·'야지마 마사아키(矢島昌明)', 본문은 띄어쓰기가 다르다."""
    text = ("- 이성원본 후보자는 신규 사업을 성공적으로 안착시켰습니다. 후보자로 추천 합니다. "
            "- 야지마 마사아키 (矢島昌明)본 후보자는 일본 와코루홀딩스 대표로서 적임자로 판단합니다.")

    segs = _split_recommendation_reason(text, ["이성원", "야지마 마사아키(矢島昌明)"])

    assert "신규 사업" in segs["이성원"]
    assert "와코루" in segs["야지마 마사아키(矢島昌明)"]
    assert "와코루" not in segs["이성원"]


def test_splits_numbered_items_without_candidate_designator():
    """「2. 정인호 농심켈로그…」처럼 이름 뒤에 '후보자'가 없어도 번호가 구간을 연다."""
    text = ("1. 도세호 본 후보자는 그룹의 성장을 견인한 적임자로 판단됨. "
            "2. 정인호 농심켈로그 한국·대만 시장을 총괄한 적임자로 판단됨.")

    segs = _split_recommendation_reason(text, ["도세호", "정인호"])

    assert "그룹의 성장" in segs["도세호"] and "농심켈로그" not in segs["도세호"]
    assert "농심켈로그" in segs["정인호"]


def test_splits_bracketed_names_after_bullet_line_without_period():
    """개조식이라 앞 줄이 마침표 없이 끝나도 대괄호 표지는 구간을 연다."""
    text = ("[타나카 야수노리(Tanaka Yasunori)] - 전문적 의견을 제시 - 경쟁력 제고에 기여 "
            "[오자키 유타카(Ozaki Yutaka)] - 독자적으로 견제 - 대안 제시에 기여")

    segs = _split_recommendation_reason(text, ["타나카 야수노리(Tanaka Yasunori)",
                                              "오자키 유타카(Ozaki Yutaka)"])

    assert "경쟁력 제고" in segs["타나카 야수노리(Tanaka Yasunori)"]
    assert "대안 제시" in segs["오자키 유타카(Ozaki Yutaka)"]


# ── 가르면 안 되는 것 (확정하지 못하면 확정하지 않는다) ────────────────────────

def test_does_not_split_genuinely_common_text():
    """원문이 후보 전원을 묶어 쓴 문면은 가르지 않는다."""
    text = "후보자들은 폭넓은 경험과 전문성을 겸비한 전문경영인으로 기업경영에 도움이 될 것으로 판단됨."

    assert _split_recommendation_reason(text, ["김준식", "박성욱"]) == {}


def test_does_not_split_on_name_mentioned_inside_a_sentence():
    """문장 중간의 이름 언급은 구간 표지가 아니다."""
    text = "김철수 후보자는 당사 대표이사 이영희 회장이 설립한 계열사에서 근무하며 역량을 쌓았습니다."

    segs = _split_recommendation_reason(text, ["김철수", "이영희"])

    assert set(segs) == {"김철수"}


def test_does_not_confirm_one_sentence_naming_several_candidates():
    """한 문장이 여러 후보를 함께 말하면 첫 후보 것으로 확정하지 않는다."""
    text = ("김태윤 후보자는 당사 연구개발업무 경력을, 전재형 후보자는 당사 영업업무 경력을, "
            "이용균 후보자는 당사 재무업무 경력을 수행한 바 있으며 리더십을 발휘할 것으로 판단.")

    assert _split_recommendation_reason(text, ["김태윤", "전재형", "이용균"]) == {}


def test_strips_only_trailing_non_content_lines():
    assert _strip_reason_tail_noise("후보자는 전문가입니다.\n해당사항 없음") == "후보자는 전문가입니다."
    assert _strip_reason_tail_noise("후보자는 전문가입니다.\n확인서") == "후보자는 전문가입니다."
    # 사유 자체가 '해당사항 없음'이면 그것이 문서의 답이므로 남긴다
    assert _strip_reason_tail_noise("해당사항 없음") == "해당사항 없음"


# ── production 경로 (parse_personnel_xml) ────────────────────────────────────

def _notice_html(rows: str, reason_blocks: str) -> str:
    return f"""
<SECTION-2>
<TITLE>2. 주주총회 목적사항별 기재사항</TITLE>
<LIBRARY>
<SECTION-3>
<TITLE>□ 이사의 선임</TITLE>
<P><SPAN>제3호 의안: 이사 선임의 건</SPAN></P>
<P>가. 후보자의 성명ㆍ생년월일ㆍ추천인ㆍ최대주주와의 관계ㆍ사외이사후보자 등 여부</P>
<TABLE>
<TR><TH>후보자성명</TH><TH>생년월일</TH><TH>사외이사후보자여부</TH><TH>추천인</TH></TR>
{rows}
</TABLE>
<P>마. 후보자에 대한 이사회의 추천 사유</P>
{reason_blocks}
</SECTION-3>
</LIBRARY>
</SECTION-2>
"""


def test_parse_personnel_xml_gives_each_candidate_their_own_reason():
    html = _notice_html(
        "<TR><TD>최윤범</TD><TD>1975.03.17</TD><TD>사내이사</TD><TD>이사회</TD></TR>"
        "<TR><TD>황덕남</TD><TD>1957.05.03</TD><TD>사외이사</TD><TD>이사회</TD></TR>",
        "<P>- 최윤범 후보자</P>"
        "<P>최윤범 후보자는 고려아연 회장으로서 신성장 동력 발굴을 주도하여 왔음.</P>"
        "<P>- 황덕남 후보자</P>"
        "<P>황덕남 후보자는 서울고등법원 법관 등을 역임한 법률 전문가임.</P>",
    )

    cands = {c["name"]: c for ap in parse_personnel_xml(html)["appointments"]
             for c in ap["candidates"]}

    assert "회장" in cands["최윤범"]["recommendationReason"]
    assert "법관" not in cands["최윤범"]["recommendationReason"]
    assert "법관" in cands["황덕남"]["recommendationReason"]
    assert not cands["최윤범"].get("recommendationReasonShared")
    assert not cands["황덕남"].get("recommendationReasonShared")


def test_parse_personnel_xml_flags_reason_it_cannot_attribute():
    """가르지 못한 공통 문면은 붙이되 「확정 못 함」을 밝힌다."""
    html = _notice_html(
        "<TR><TD>김준식</TD><TD>1960.01.01</TD><TD>사내이사</TD><TD>이사회</TD></TR>"
        "<TR><TD>박성욱</TD><TD>1961.02.02</TD><TD>사내이사</TD><TD>이사회</TD></TR>",
        "<P>후보자들은 폭넓은 경험과 전문성을 겸비한 전문경영인으로 판단됨에 따라 추천</P>",
    )

    cands = {c["name"]: c for ap in parse_personnel_xml(html)["appointments"]
             for c in ap["candidates"]}

    for name in ("김준식", "박성욱"):
        assert "전문경영인" in cands[name]["recommendationReason"]
        assert cands[name]["recommendationReasonShared"] is True
