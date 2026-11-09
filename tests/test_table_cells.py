"""고정 셀 순회는 기존 다중 이름 검색과 같은 노드·순서·재귀 범위를 반환한다."""
from bs4 import BeautifulSoup
import pytest

from open_proxy_mcp.services.segment_candidates import _table_cells, _table_to_grid, _table_to_markdown
from open_proxy_mcp.services.segment_grid import _TableRead


@pytest.mark.parametrize("parser", ["lxml", "html.parser", "xml"])
@pytest.mark.parametrize("markup", [
    "<table><tr><th>제목</th><td>값</td><te>1,000</te><tu>단위</tu></tr></table>",
    "<table><tr>문자<!-- 주석 --><span>설명</span><td>셀<span>안쪽</span></td>꼬리</tr></table>",
    "<table><tr><td>바깥<table><tr><te>안쪽</te></tr></table></td><tu>끝</tu></tr></table>",
    "<table><tr><tdish>다른 이름</tdish><header>제목</header><td>1</td><TD>2</TD><TE>3</TE></tr></table>",
    '<table xmlns:x="urn:test"><tr><x:td>접두사</x:td><td>일반</td><x:tu>단위</x:tu></tr></table>',
    "<table><tr><td></td><th> </th><td>&lt;td&gt;</td><te><![CDATA[문자]]></te></tr></table>",
])
def test_cells_are_the_same_objects_in_the_same_order(parser, markup):
    soup = BeautifulSoup(markup, parser)
    for node in [soup, *soup.find_all("tr")]:
        expected = node.find_all(["td", "th", "te", "tu"])
        assert [id(cell) for cell in _table_cells(node)] == [id(cell) for cell in expected]


def test_cell_root_is_excluded_but_nested_cells_are_preserved():
    soup = BeautifulSoup("<td>바깥<td>안쪽</td></td>", "xml")
    root = soup.find("td")
    assert _table_cells(root) == [root.find("td")]


def test_grid_keeps_rowspan_colspan_and_recursive_nested_cells():
    soup = BeautifulSoup(
        '<table><tr><th rowspan="2">지역</th><th colspan="2">매출</th></tr>'
        '<tr><td>당기</td><td>전기</td></tr>'
        '<tr><te>국내</te><te>1,000</te><tu>900</tu></tr></table>', "lxml")
    assert _table_to_grid(soup.table) == [
        ["지역", "매출", "매출"], ["지역", "당기", "전기"], ["국내", "1,000", "900"],
    ]
    assert _table_to_markdown(soup.table) == (
        "| 지역 | 매출 | 매출 |\n|---|---|---|\n| 지역 | 당기 | 전기 |\n| 국내 | 1,000 | 900 |"
    )


def test_raw_rows_keep_nested_table_recursion_and_text_boundaries():
    markup = "<table><tr><td>가<span>나</span><table><tr><te>1,000</te></tr></table></td><tu>끝</tu></tr></table>"
    table = _TableRead(markup)
    expected = [[c.get_text(" ", strip=True) for c in row.find_all(["td", "th", "te", "tu"])]
                for row in table.table.find_all("tr")]
    assert table.rows == expected


def test_fixed_cell_lookup_does_not_build_a_generic_search(monkeypatch):
    row = BeautifulSoup("<table><tr><te>1,000</te><td>2,000</td></tr></table>", "lxml").tr

    def unexpected(*args, **kwargs):
        raise AssertionError("셀 이름만 확인하는 경로에서 범용 검색기를 만들지 않는다")

    monkeypatch.setattr(row, "find_all", unexpected)
    assert [cell.get_text() for cell in _table_cells(row)] == ["1,000", "2,000"]
