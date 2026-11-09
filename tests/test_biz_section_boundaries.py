"""소절 경계 색인은 기존의 문자열 좌표·대소문자·손상 태그 처리를 보존한다."""
import random

import pytest

from open_proxy_mcp.services import biz_fields as bf


def _legacy_end(html, pos):
    """색인을 도입하기 전의 기준 동작. 파서 복구 대신 문자열 경계를 사용한다."""
    for tag in ("section-2", "section-1"):
        opened = max(html.rfind(f"<{tag}", 0, pos), html.rfind(f"<{tag.upper()}", 0, pos))
        closed = max(html.rfind(f"</{tag}", 0, pos), html.rfind(f"</{tag.upper()}", 0, pos))
        if opened < 0 or closed > opened:
            continue
        ends = [x for x in (html.find(f"</{tag}>", pos), html.find(f"</{tag.upper()}>", pos)) if x >= 0]
        if ends:
            return min(ends)
    return None


@pytest.mark.parametrize("html, closing", [
    ("<section-2>대상</section-2>", "</section-2>"),
    ("<SECTION-2>대상</SECTION-2>", "</SECTION-2>"),
    ("<SECTION-2>대상</section-2>", "</section-2>"),
    ('<section-2 A="x">대상</section-2>', "</section-2>"),
    ("<section-1><section-2>대상</section-2></section-1>", "</section-2>"),
    ("<section-1><section-2>앞</section-2>대상</section-1>", "</section-1>"),
    ("<section-1><section-2>대상</section-1>", "</section-1>"),
    ("<section-1><Section-2>대상</Section-2></section-1>", "</section-1>"),
    ("<section-1><section-2>대상</section-2 ></section-1>", "</section-1>"),
    ("<section-20>대상</section-2>", "</section-2>"),
    ("<!--<section-2>-->대상</section-2>", "</section-2>"),
])
def test_same_enclosing_boundary_including_legacy_lexical_cases(html, closing):
    index = bf.build_region_index(html)
    assert bf._enclosing_section_end(html, html.index("대상"), index) == html.index(closing)


@pytest.mark.parametrize("html", [
    "대상",
    "대상</section-2>",
    "<section-2>앞</section-2>대상</section-2>",
    "<Section-2>대상</Section-2>",
    "<section-2>대상</section-2 >",
])
def test_missing_or_unusable_boundary_stays_missing(html):
    assert bf._enclosing_section_end(html, html.index("대상"), bf.build_region_index(html)) is None


def test_coordinates_inside_partial_prefixes_and_outside_document_keep_legacy_meaning():
    html = "<section-2>x</section-2>tail</section-2>"
    index = bf.build_region_index(html)
    for pos in range(-len(html) - 2, len(html) + 3):
        assert bf._enclosing_section_end(html, pos, index) == _legacy_end(html, pos)


def test_malformed_and_mixed_case_documents_match_legacy_at_tag_edges():
    rng = random.Random(261107)
    fragments = ["x", "<section-1>", "</section-1>", "<SECTION-2 a='x'>",
                 "</SECTION-2>", "<section-2", "</section-2 >", "<section-20>",
                 "</section-20>", "<Section-2>", "</section-2>", "<!--", "-->"]
    for _ in range(100):
        html = "".join(rng.choices(fragments, k=20))
        index = bf.build_region_index(html)
        positions = {0, len(html), *[rng.randrange(len(html)) for _ in range(10)]}
        positions.update(i + delta for i, ch in enumerate(html) if ch in "<>" for delta in (0, 1))
        for pos in positions:
            assert bf._enclosing_section_end(html, pos, index) == _legacy_end(html, pos)


def test_manual_heading_index_and_two_argument_call_remain_supported():
    html = "<section-2>대상</section-2>"
    pos = html.index("대상")
    assert bf._enclosing_section_end(html, pos) == html.index("</section-2>")
    assert bf._enclosing_section_end(html, pos, bf.BizRegionIndex(())) == html.index("</section-2>")


def test_cached_boundary_does_not_scan_document_again():
    class NoBoundaryScan(str):
        def find(self, *args, **kwargs):
            raise AssertionError("색인이 있는 경계 조회는 문서를 다시 훑지 않는다")

        def rfind(self, *args, **kwargs):
            raise AssertionError("색인이 있는 경계 조회는 문서를 다시 훑지 않는다")

    html = "<section-1><section-2>대상</section-2></section-1>"
    index = bf.build_region_index(html)
    pos, end = html.index("대상"), html.index("</section-2>")
    assert bf._enclosing_section_end(html, pos, index) == end
    assert bf._enclosing_section_end(NoBoundaryScan(html), pos, index) == end


def test_unused_or_few_distinct_queries_do_not_prepare_tag_index(monkeypatch):
    def unwanted(html):
        raise AssertionError("소수 경계 조회에 전체 태그 색인을 만들지 않는다")

    monkeypatch.setattr(bf, "_section_tag_positions", unwanted)
    html = "<section-2>abcdef</section-2>"
    index = bf.build_region_index(html)
    pos = html.index("abcdef")
    for offset in (0, 0, 1, 2, 1):
        assert bf._enclosing_section_end(html, pos + offset, index) == html.index("</section-2>")


def test_repeated_queries_prepare_tag_index_only_once(monkeypatch):
    original = bf._section_tag_positions
    calls = []

    def counted(html):
        calls.append(True)
        return original(html)

    monkeypatch.setattr(bf, "_section_tag_positions", counted)
    html = "<section-2>abcdef</section-2>"
    index = bf.build_region_index(html)
    pos, end = html.index("abcdef"), html.index("</section-2>")
    for offset in range(3):
        assert bf._enclosing_section_end(html, pos + offset, index) == end
    assert calls == []
    for offset in (3, 4, 5, 3):
        assert bf._enclosing_section_end(html, pos + offset, index) == end
    assert calls == [True]


def test_missing_boundary_is_reused_too(monkeypatch):
    original = bf._enclosing_section_end_scan
    calls = []

    def counted(html, pos):
        calls.append(True)
        return original(html, pos)

    monkeypatch.setattr(bf, "_enclosing_section_end_scan", counted)
    html = "경계 없는 원문"
    index = bf.build_region_index(html)
    assert bf._enclosing_section_end(html, 2, index) is None
    assert bf._enclosing_section_end(html, 2, index) is None
    assert calls == [True]


def test_boundary_reuse_stays_inside_its_original_document():
    first = "<section-2>대상</section-2>"
    second = "<section-2>대상과 다른 길이의 본문</section-2>"
    first_index, second_index = bf.build_region_index(first), bf.build_region_index(second)
    pos = first.index("대상")
    for _ in range(2):
        assert bf._enclosing_section_end(first, pos, first_index) == first.index("</section-2>")
        assert bf._enclosing_section_end(second, pos, second_index) == second.index("</section-2>")
