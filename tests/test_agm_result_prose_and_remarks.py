"""주총결과 — 줄글 서식 읽기, 표 서식의 비고·무효처리 문장.

네트워크 0. 줄글 서식은 실제 서식 꼴을 그대로 옮긴 합성 HTML(줄마다 `<p>`), 표 서식은
`tests/fixtures/agm_result/*.txt`(DART 공개 주총결과 공시의 본문 글, 후보 약력표 앞에서 자름)를
행 단위로 다시 표로 짠다 — 원문 HTML 을 받을 수 없어 칸 값만 그대로 옮겼다.

고치기 전 줄글 파서는: 첫 안건을 잃고(맨 번호 줄을 직전 안건의 하위로 잡고 상위를 버림),
제목에 「의안 :」·결과문이 붙고, 결과 없는 머리줄을 제목의 「승인」 때문에 가결로 읽고,
`=> 미결`·`⇒ 철회`·「기타 결의내용」 머리말을 하나도 못 읽었다.
"""
import re
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from open_proxy_mcp.services.agm_result_parser import (
    parse_agm_result_summary,
    parse_agm_result_table,
    parse_agm_result_void_notice,
)

_FIX = Path(__file__).parent / "fixtures" / "agm_result"


def _prose(*lines: str) -> list[tuple[str, str, str]]:
    html = "<html><body>" + "".join(f"<p>{line}</p>" for line in lines) + "</body></html>"
    return [(i["number"], i["agenda"], i["passed"])
            for i in parse_agm_result_summary(BeautifulSoup(html, "lxml"))]


def test_prose_keeps_first_item_and_strips_uian_prefix():
    assert _prose("1. 결의사항",
                  "제1호 의안 : 재무제표 승인의 건 → 원안대로 승인",
                  "제2호 의안 : 정관 변경의 건 → 원안대로 승인",
                  "제3호 의안 : 이사 선임의 건 → 부결",
                  "2. 주주총회 일자") == [
        ("제1호", "재무제표 승인의 건", "가결"),
        ("제2호", "정관 변경의 건", "가결"),
        ("제3호", "이사 선임의 건", "부결"),
    ]


@pytest.mark.parametrize("lines, want", [
    # `->` 와 결과문 「원안대로 승인 가결」
    (["1. 결의사항", "제1호의안 : 부동산매매계약 체결의 건 -> 원안대로 승인 가결", "2. 주주총회 일자"],
     [("제1호", "부동산매매계약 체결의 건", "가결")]),
    # `▶`, 붙여 쓴 결과문
    (["1. 결의사항", "제1호 의안: 정관 일부 변경의 건 ▶ 원안대로 승인",
      "제2호 의안: 사명 변경의 건 ▶의결정족수 미달로 인한 부결", "2. 주주총회 일자"],
     [("제1호", "정관 일부 변경의 건", "가결"), ("제2호", "사명 변경의 건", "부결")]),
    # 화살표 없이 괄호·붙임표
    (["1. 결의사항", "제1호 의안 : 이사 선임의 건 (부결)", "제2호 의안 : 감사 선임의 건 - 원안대로 가결",
      "2. 주주총회 일자"],
     [("제1호", "이사 선임의 건", "부결"), ("제2호", "감사 선임의 건", "가결")]),
    # 결과가 다음 줄에
    (["1. 결의사항", "제1호 의안 : 재무제표 승인의 건", "원안대로 승인", "2. 주주총회 일자"],
     [("제1호", "재무제표 승인의 건", "가결")]),
    # 미결·철회도 결과다
    (["1. 결의사항", "제1호 의안 : 정관 변경 승인의 건 => 미결", "제2호 의안 : 이사 선임의 건 ⇒ 철회",
      "제3호 의안 : 감사 선임의 건 ☞ 미상정", "제4호 의안 : 보수한도 승인의 건 ➤ 보류",
      "2. 주주총회 일자"],
     [("제1호", "정관 변경 승인의 건", "미결"), ("제2호", "이사 선임의 건", "철회"),
      ("제3호", "감사 선임의 건", "미상정"), ("제4호", "보수한도 승인의 건", "보류")]),
    # 결과 없는 머리줄은 제목의 「승인」 으로 가결이 되지 않는다
    (["1. 결의사항", "제2호 의안 : 이사 보수한도 승인의 건", "제3호 의안 : 정관 변경의 건 → 부결",
      "2. 주주총회 일자"],
     [("제3호", "정관 변경의 건", "부결")]),
])
def test_prose_marks_and_verdict_words(lines, want):
    assert _prose(*lines) == want


@pytest.mark.parametrize("head", ["4. 기타 결의내용", "결의내용", "나. 부의안건"])
def test_prose_other_section_heads(head):
    assert _prose(head, "제1호 의안 : 정관 변경의 건 → 가결", "5. 주주총회 일자") == [
        ("제1호", "정관 변경의 건", "가결")]


def test_prose_children_and_bulk_range_result_kept():
    """「제N-M호 내지 제N-K호 일괄 결과」 줄 — 하위 안건 결과로 상위를 대신한다."""
    assert _prose("1. 결의사항",
                  "제1호 의안 : 재무제표 승인의 건 → 가결",
                  "제2호 의안 : 이사 선임의 건",
                  "제2-1호 : 사내이사 홍길동 선임의 건",
                  "제2-2호 : 사외이사 김갑동 선임의 건",
                  "제2-1호 내지 제2-2호 원안대로 가결",
                  "제3호 의안 : 정관 변경의 건 → 부결",
                  "2. 주주총회 일자") == [
        ("제1호", "재무제표 승인의 건", "가결"),
        ("제2-1호", "사내이사 홍길동 선임의 건", "가결"),
        ("제2-2호", "사외이사 김갑동 선임의 건", "가결"),
        ("제3호", "정관 변경의 건", "부결"),
    ]


def test_prose_bulk_range_over_top_level_items():
    assert _prose("1. 결의사항",
                  "제1호 의안 : 재무제표 승인의 건",
                  "제2호 의안 : 정관 변경의 건",
                  "제1호 내지 제2호 의안 원안대로 가결",
                  "2. 주주총회 일자") == [
        ("제1호", "재무제표 승인의 건", "가결"), ("제2호", "정관 변경의 건", "가결")]


def test_prose_keeps_raw_verdict_text():
    html = ("<p>1. 결의사항</p><p>제1-1호 의안 : 사명 변경의 건 ▶의결정족수 미달로 인한 부결</p>"
            "<p>2. 주주총회 일자</p>")
    item = parse_agm_result_summary(BeautifulSoup(html, "lxml"))[0]
    assert (item["number"], item["passed"], item["passed_raw"]) == (
        "제1-1호", "부결", "의결정족수 미달로 인한 부결")


# ── 표 서식: 비고 칸과 「기타 투자판단」 무효처리 문장 ──

_ROW = re.compile(
    r"(\d+(?:-\d+)?(?:호 의안)?)\s+(보통결의|특별결의|보고사항|집중투표|기타)\s+(.+?)\s+"
    r"(가결|부결|보류|철회|수정가결|미결|미상정|무산)\s+([\d.]+|-)\s+([\d.]+|-)\s+([\d.]+|-)\s*(.*?)"
    r"(?=\s\d+(?:-\d+)?(?:호 의안)?\s+(?:보통결의|특별결의|보고사항|집중투표|기타)\s|$)"
)


def _fixture_text(rcept_no: str) -> str:
    return (_FIX / f"{rcept_no}.txt").read_text("utf-8").strip()


def _table_html(rcept_no: str) -> str:
    """공시 글의 안건 세부내역 행을 OPM 이 받는 표 꼴(8칸: …, 반대·기권, 비고)로 짠다."""
    text = _fixture_text(rcept_no)
    body = text[text.index("반대, 기권 등 비율(%)") + len("반대, 기권 등 비율(%)"):]
    rows = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in m.groups()) + "</tr>"
        for m in _ROW.finditer(body.strip()))
    return (
        "<p>" + text[:text.index("【")] + "</p>"
        "<table><tr><th>번호</th><th>결의구분</th><th>회의목적사항</th><th>가결여부</th>"
        "<th>의결권 있는 발행주식 총수 기준(1)</th><th>(1)중 의결권 행사 주식수 기준</th><th>비고</th></tr>"
        "<tr><th>찬성률(%)</th><th>찬성률(%)</th><th>반대, 기권 등 비율(%)</th></tr>"
        + rows + "</table>")


def test_passed_row_with_void_remark_is_flagged():
    """가결 8건 중 1건이 「후보자 사퇴로 인한 미취임으로 무효처리」 — 결과 칸만으로는 안 보인다."""
    soup = BeautifulSoup(_table_html("20261014800682"), "lxml")
    items = parse_agm_result_table(soup)
    assert len(items) == 8 and {i["passed"] for i in items} == {"가결"}
    flagged = [i for i in items if i["remark_contradicts_pass"]]
    assert len(flagged) == 1
    assert flagged[0]["agenda"] == "감사위원이 되는 독립이사 김태수 선임의 건"
    assert flagged[0]["remark"] == "후보자 사퇴로 인 한 미취임으로 무 효처리"
    # 다른 비고는 원문 그대로 남지만 어긋남으로 표시되지 않는다
    assert any(i["remark"].startswith("전자투표실시") and not i["remark_contradicts_pass"] for i in items)

    notice = parse_agm_result_void_notice(soup)
    assert notice.startswith("총회 종료 후 회사가 후보자 본인으로부터")
    assert notice.endswith("이를 무효처리하였습니다.")
    assert "임시주주총회를 개최할 예정" not in notice


@pytest.mark.parametrize("rcept_no", ["20260908800579", "20260916900146", "20260828900416"])
def test_resign_and_general_condition_are_not_void(rcept_no):
    """분리선출 「사임 후」, 비고 「사임일」, 합병 해제 조건문(「철회되는 경우 … 효력은 발생하지 않습니다」)은
    무효처리가 아니다. 「부결」·「사임」 은 어긋남 낱말이 아니다."""
    soup = BeautifulSoup(_table_html(rcept_no), "lxml")
    items = parse_agm_result_table(soup)
    assert items and not any(i["remark_contradicts_pass"] for i in items)
    assert parse_agm_result_void_notice(soup) == ""


def test_remark_on_non_passed_row_is_not_flagged():
    html = ("<table><tr><th>번호</th><th>결의구분</th><th>회의목적사항</th><th>가결여부</th>"
            "<th>찬성률</th><th>찬성률</th><th>반대</th><th>비고</th></tr>"
            "<tr><th>찬성률(%)</th><th>찬성률(%)</th><th>반대</th></tr>"
            "<tr><td>1</td><td>보통결의</td><td>사내이사 선임의 건</td><td>미결</td>"
            "<td>10.0</td><td>20.0</td><td>80.0</td><td>후보자 사퇴로 인한 안건 철회</td></tr>"
            "<tr><td>2</td><td>보통결의</td><td>사내이사 선임의 건</td><td>부결</td>"
            "<td>10.0</td><td>20.0</td><td>80.0</td><td>선임 무효</td></tr>"
            "<tr><td>3</td><td>특별결의</td><td>정관 변경의 건</td><td>수정가결</td>"
            "<td>80.0</td><td>99.0</td><td>1.0</td><td>일부 조항 취 소</td></tr></table>")
    items = parse_agm_result_table(BeautifulSoup(html, "lxml"))
    assert [i["remark_contradicts_pass"] for i in items] == [False, False, True]
