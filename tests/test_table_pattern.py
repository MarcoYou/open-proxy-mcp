"""빠른 표 구간 탐색도 기존의 첫 닫는 태그·좌표·원문을 그대로 반환한다."""
import random
import re

import pytest

from open_proxy_mcp.services.segment_candidates import _TABLE_RE

_LEGACY_TABLE = re.compile(r"<table\b.*?</table>", re.DOTALL | re.IGNORECASE)


@pytest.mark.parametrize("html", [
    "",
    "<TABLE><TR><TE>1,000</TE></TR></TABLE>",
    "<table>A<table>B</table>C</table><table>D</table>",
    "<table/>문자</table>",
    "<tabletop>아님</table><table>맞음</table>",
    '<table data="</table>">나머지</table>',
    "<!--<TABLE>주석</TABLE>--><table>실제</table>",
    "<table>불완전한 닫음</table ><table>다음</table>",
    "<table><<<<<이상한 문자\n<&nbsp;\x00</TABLE>",
    "<table>닫음 없음<table>역시 없음",
    "<table\u200b>비표준 구분자</table><tableſ>경계 아님</table>",
])
def test_literal_boundaries_and_returned_original_match_legacy(html):
    assert [(m.span(), m.group()) for m in _TABLE_RE.finditer(html)] == [
        (m.span(), m.group()) for m in _LEGACY_TABLE.finditer(html)
    ]


def test_search_bounds_and_malformed_inputs_match_legacy():
    rng = random.Random(261108)
    fragments = ["글자", "\n", "<table>", "<TABLE a='x'>", "</table>", "</TABLE>",
                 "<table", "</table >", "<tabletop>", "<", ">", "<!--", "-->"]
    for _ in range(150):
        html = "".join(rng.choices(fragments, k=20))
        for _ in range(15):
            start = rng.randrange(len(html) + 1)
            end = rng.randrange(start, len(html) + 1)
            assert [(m.span(), m.group()) for m in _TABLE_RE.finditer(html, start, end)] == [
                (m.span(), m.group()) for m in _LEGACY_TABLE.finditer(html, start, end)
            ]


def test_many_less_than_characters_and_large_text_keep_first_close():
    html = "<table>" + "문자" * 10000 + "<" * 1000 + "</table>꼬리</table>"
    assert _TABLE_RE.search(html).group() == _LEGACY_TABLE.search(html).group()
