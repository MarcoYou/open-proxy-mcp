"""직접 HTML 트리 판독은 기존 문단·표·공백·중첩·복구 결과를 보존한다."""
import pytest

from open_proxy_mcp.services import segment_candidates as sc


TABLE = ('<TABLE><TR><TH>구분</TH><TH>당기</TH></TR>'
         '<TR><TE>수출</TE><TE>1,000</TE></TR><TR><TU>내수</TU><TD>2,000</TD></TR></TABLE>')


@pytest.mark.parametrize("fragment", [
    "", "   ", "태그 없는 본문", "<p>가</p><p>가나</p>",
    "<p>앞<span>가운데</span>뒤</p><span>가운데</span><span>가운</span>",
    "<p>앞<br/>뒤&nbsp;끝</p><span>다음</span>",
    "<p>앞<!-- 제외 -->뒤</p>",
    "<p><![CDATA[텍스트]]>뒤</p>",
    "<p>첫 문단</p>" + TABLE + "<p>뒤 문단</p>",
    '<table><tr><th rowspan="2">지역</th><th colspan="2">매출</th></tr>'
    '<tr><td>당기</td><td>전기</td></tr><tr><te>국내</te><te>1,000</te><te>900</te></tr></table>',
    '<table><tr><td>바깥' + TABLE + '</td></tr></table>',
    '<table><tr><td>가|나\n끝</td><te><span>1</span> 2</te></tr></table>',
    "<p><script>안 보임</script>보이는 본문</p>",
    "<p><style>안 보임</style>보이는 본문</p>",
    "<p><template><span>안 보임</span></template>보이는 본문</p>",
    "<p><ruby>본문<rt><span>보조</span></rt><rp>(</rp></ruby>끝</p>",
    "<DIV><p>처음<p>다음</DIV>",
    "<section-2></wrong><p>본문</p>" + TABLE + "</section-2>",
])
def test_direct_render_matches_legacy_output(fragment):
    assert sc._render_html_region_md(fragment, 0, len(fragment)) == sc._render_html_region_md_bs4(fragment, 0, len(fragment))


def test_plain_fragment_uses_direct_path_and_keeps_exact_markdown(monkeypatch):
    def unexpected(*args):
        raise AssertionError("정상 HTML 조각을 다시 객체로 조립하지 않는다")

    monkeypatch.setattr(sc, "_render_html_region_md_bs4", unexpected)
    html = "<p>제목</p>" + TABLE
    assert sc._render_html_region_md(html, 0, len(html)) == (
        "제목\n\n| 구분 | 당기 |\n|---|---|\n| 수출 | 1,000 |\n| 내수 | 2,000 |"
    )


@pytest.mark.parametrize("fragment", [
    '<!DOCTYPE html><p>본문</p>' + TABLE,
    '<?xml version="1.0"?><p>본문</p>' + TABLE,
    '<div xmlns="urn:test"><p>본문</p>' + TABLE + '</div>',
    '<div xmlns:x="urn:test"><p>본문</p>' + TABLE + '</div>',
    '<html><body>앞</body></html><p>뒤 본문</p>' + TABLE,
    '<html><body>앞</body></html><html><body><p>뒤 본문</p>' + TABLE,
    '<div>' * 270 + '<p>깊은 본문</p>' + TABLE + '</div>' * 270,
    '<section-2></wrong>' * 150 + '<div>' * 270 + '<p>깊은 본문</p>' + TABLE,
])
def test_repair_sensitive_fragments_keep_the_legacy_path(monkeypatch, fragment):
    original = sc._render_html_region_md_bs4
    expected = original(fragment, 0, len(fragment))
    calls = []

    def counted(*args):
        calls.append(True)
        return original(*args)

    monkeypatch.setattr(sc, "_render_html_region_md_bs4", counted)
    assert sc._render_html_region_md(fragment, 0, len(fragment)) == expected
    assert calls == [True]


def test_fragment_slice_not_surrounding_html_decides_the_render_path(monkeypatch):
    prefix = '<html><head></head><body>'
    html = prefix + '<p>본문</p>' + TABLE + '</body></html>'
    start, end = len(prefix), len(html) - len('</body></html>')
    expected = sc._render_html_region_md_bs4(html, start, end)

    def unexpected(*args):
        raise AssertionError("실제 조각에 없는 문서 선언으로 폴백하지 않는다")

    monkeypatch.setattr(sc, "_render_html_region_md_bs4", unexpected)
    assert sc._render_html_region_md(html, start, end) == expected


def test_large_fragment_is_not_truncated(monkeypatch):
    fragment = ' ' * (8 * 1024 * 1024) + '<p>끝 본문</p>'
    original = sc._render_html_region_md_bs4
    calls = []

    def counted(*args):
        calls.append(True)
        return original(*args)

    monkeypatch.setattr(sc, "_render_html_region_md_bs4", counted)
    assert sc._render_html_region_md(fragment, 0, len(fragment)) == '끝 본문'
    assert calls == [True]


def test_invalid_span_keeps_the_original_error():
    html = '<table><tr><td colspan="invalid">본문</td></tr></table>'
    with pytest.raises(ValueError):
        sc._render_html_region_md(html, 0, len(html))
