"""표 직접 판독은 원래 칸·병합칸·텍스트 경계와 복구 동작을 보존한다."""
import bs4
import pytest

from open_proxy_mcp.services import segment_candidates as sc


TABLE = ('<TABLE><TR><TH rowspan="2">지역</TH><TH colspan="2">매출</TH></TR>'
         '<TR><TD>당기</TD><TD>전기</TD></TR>'
         '<TR><TE>국내</TE><TE>1,000</TE><TU>900</TU></TR></TABLE>')


@pytest.mark.parametrize("html", [
    '', '표 없음', '<table></table>', TABLE,
    '<table><tr><td>앞<span>가운데</span>뒤<br/>끝&nbsp;값</td></tr></table>',
    '<table><tr><td>앞<!-- 제외 -->뒤</td><td><![CDATA[문자]]>끝</td></tr></table>',
    '<table><tr><td>앞<script>제외</script><style>제외</style>뒤</td></tr></table>',
    '<table><tr><td><template><span>제외</span></template>본문'
    '<ruby>값<rt>발음</rt><rp>(</rp></ruby>끝</td></tr></table>',
    '<table><tr><td>바깥' + TABLE + '</td><tu>끝</tu></tr></table>',
    '<table><tr><tdish>다른 이름</tdish><td>1</td><TE>2</TE><tu>3</tu></tr></table>',
    '<table><tr><td>가|나\n끝</td><td>1&amp;2</td></tr></table>',
    '<table><tr><td colspan="0">영</td><td rowspan="-1">음</td></tr></table>',
    '<table><tr><td colspan="">빈 속성</td><td rowspan>불리언</td></tr></table>',
    '<table><tr><td colspan="2" colspan="3">중복 속성</td></tr></table>',
    '<table><tr><td>첫째<td>둘째<tr><td>셋째</table>',
    '<table><tr><th>첫 표</th></tr></table>' + TABLE,
    '</wrong>' + TABLE + '</section-2>',
])
def test_reader_preserves_legacy_text_raw_rows_grid_and_markdown(html):
    tb = bs4.BeautifulSoup(html, 'lxml').find('table')
    expected_rows = ([[cell.get_text(' ', strip=True) for cell in sc._table_cells(row)]
                      for row in tb.find_all('tr')] if tb is not None else [])
    expected_grid = sc._table_to_grid(tb) if tb is not None else []
    reader = sc._TableRead(html)
    assert reader.text == (tb.get_text(' ', strip=True) if tb is not None else '')
    assert reader.rows == expected_rows
    assert reader.grid == expected_grid
    assert reader.markdown == sc._grid_to_markdown(expected_grid)


def test_normal_table_is_parsed_once_without_soup(monkeypatch):
    parse = sc.parse_fragment
    calls = []
    def counted(html):
        calls.append(True)
        return parse(html)
    def unexpected(*args, **kwargs):
        raise AssertionError('직접 판독에서 BeautifulSoup을 만들지 않는다')
    monkeypatch.setattr(sc, 'parse_fragment', counted)
    monkeypatch.setattr(bs4, 'BeautifulSoup', unexpected)
    reader = sc._TableRead(TABLE)
    assert reader.text == '지역 매출 당기 전기 국내 1,000 900'
    assert reader.rows[1] == ['당기', '전기']
    assert reader.grid[1] == ['지역', '당기', '전기']
    assert '| 국내 | 1,000 | 900 |' in reader.markdown
    assert calls == [True]
    assert 'table' not in reader.__dict__


@pytest.mark.parametrize('html', [
    '<?xml version="1.0"?>' + TABLE,
    '<!DOCTYPE html>' + TABLE,
    TABLE.replace('<TABLE>', '<TABLE xmlns="urn:dart">'),
    TABLE.replace('<TABLE>', '<TABLE xmlns:x="urn:dart">'),
    '<html><body>앞</body></html>' + TABLE,
    '<div>' * 270 + TABLE + '</div>' * 270,
    '<section-2></wrong>' * 150 + '<div>' * 270 + TABLE,
])
def test_repair_sensitive_table_reuses_one_legacy_tree(monkeypatch, html):
    original = bs4.BeautifulSoup
    tb = original(html, 'lxml').find('table')
    expected = sc._table_to_grid(tb)
    calls = []
    def counted(*args, **kwargs):
        calls.append(True)
        return original(*args, **kwargs)
    monkeypatch.setattr(bs4, 'BeautifulSoup', counted)
    reader = sc._TableRead(html)
    assert reader.native_table is None
    assert reader.grid == expected
    assert reader.text == tb.get_text(' ', strip=True)
    assert reader.rows
    assert reader.markdown == sc._grid_to_markdown(expected)
    assert reader.table is reader.table
    assert calls == [True]


@pytest.mark.parametrize('attribute', ['colspan', 'rowspan'])
def test_invalid_span_keeps_the_original_exception(attribute):
    html = f'<table><tr><td {attribute}="invalid">본문</td></tr></table>'
    with pytest.raises(ValueError) as before:
        sc._table_to_grid(bs4.BeautifulSoup(html, 'lxml').table)
    with pytest.raises(ValueError) as after:
        _ = sc._TableRead(html).grid
    assert str(before.value) == str(after.value)
