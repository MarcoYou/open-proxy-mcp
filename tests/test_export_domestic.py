"""수출·내수의 표 순서·선행 단위·재귀 칸 판독을 직접 트리 판독에서도 보존한다."""
import pytest

from open_proxy_mcp.services import business_details as bd


TABLE = (
    "<TABLE><TR><TH>매출액</TH><TH>당기</TH></TR>"
    "<TR><TE>수출</TE><TE>1,000</TE></TR>"
    "<TR><TE>내수</TE><TE>2,000</TE></TR></TABLE>"
)


@pytest.mark.parametrize("unit_markup", [
    "<P>(단위 : 천원)</P>",
    "<DIV><SPAN>(단위 : 천원)</SPAN></DIV>",
    "<PRE>  (단위 : 천원)\n</PRE>",
    "<TEXTAREA>(단위 : 천원)</TEXTAREA>",
    "<SCRIPT>(단위 : 천원)</SCRIPT>",
    "<STYLE>(단위 : 천원)</STYLE>",
    "<!-- (단위 : 천원) -->",
    "<P>단&#50948; : &#52380;원</P>",
    "<TABLE><TR><TU>(단위 : 천원)</TU></TR></TABLE>",
])
def test_unit_outside_or_inside_previous_table_keeps_same_scale(unit_markup):
    result = bd._export_from_biz_table(unit_markup + TABLE)
    assert result["export_krw"] == 1_000_000
    assert result["domestic_krw"] == 2_000_000
    assert result["rows_summed"] == 1
    assert result["export_share_pct"] == 33.3


@pytest.mark.parametrize("html", [
    TABLE,
    "<P>단<SPAN>위</SPAN> : 천원</P>" + TABLE,
    "<P>(단위 : 천원)</P><P>단위에 관한 안내</P>" + TABLE,
    TABLE + "<P>(단위 : 천원)</P>",
    TABLE.replace("<TH>매출액</TH>", "<TH>매출액 (단위 : 천원)</TH>"),
])
def test_only_matching_preceding_string_controls_unit(html):
    """단위가 나뉘거나 현재 표 안/뒤에만 있으면 기존 기본 배수를 유지한다."""
    result = bd._export_from_biz_table(html)
    assert result["export_krw"] == 1_000_000_000
    assert result["domestic_krw"] == 2_000_000_000


def test_inline_export_label_and_malformed_cells_are_still_read():
    inline = TABLE.replace("<TE>수출</TE>", "<TE>수<SPAN>출</SPAN></TE>")
    malformed = (
        "<DIV>(단위 : 천원)<P><TABLE><TR><TD>매출액<TD>당기"
        "<TR><TD>수출<TD>1,000<TR><TD>내수<TD>2,000"
    )
    for html in ["<P>(단위 : 천원)</P>" + inline, malformed]:
        result = bd._export_from_biz_table(html)
        assert result["export_krw"] == 1_000_000
        assert result["domestic_krw"] == 2_000_000


def test_nested_table_keeps_existing_recursive_row_semantics():
    html = "<P>(단위 : 천원)</P><TABLE><TR><TD>매출액" + TABLE + "</TD></TR></TABLE>"
    result = bd._export_from_biz_table(html)
    assert result["rows_summed"] == 2
    assert result["export_krw"] == 2_000_000
    assert result["domestic_krw"] == 2_000_000


def test_first_matching_table_and_nearest_unit_are_preserved():
    html = "<P>단위 : 억원</P><P>단위 : 천원</P>" + TABLE + "<P>단위 : 원</P>" + TABLE
    result = bd._export_from_biz_table(html)
    assert result["export_krw"] == 1_000_000
    assert result["rows_summed"] == 1


@pytest.mark.parametrize("prefix", [
    "<!DOCTYPE html>", "<?xml version='1.0'?>",
    '<DIV xmlns="urn:sample"></DIV>', '<DIV xmlns:x="urn:sample"></DIV>',
])
def test_special_declarations_keep_full_parse_fallback(monkeypatch, prefix):
    original = bd._export_candidate_rows_bs4
    calls = []

    def recorded(html):
        calls.append(True)
        yield from original(html)

    monkeypatch.setattr(bd, "_export_candidate_rows_bs4", recorded)
    result = bd._export_from_biz_table(prefix + "<P>(단위 : 천원)</P>" + TABLE)
    assert calls == [True]
    assert result["export_krw"] == 1_000_000
    assert result["domestic_krw"] == 2_000_000


@pytest.mark.parametrize("tag", ["script", "style", "template", "rt", "rp"])
def test_non_content_strings_inside_table_do_not_become_amounts(tag):
    html = TABLE.replace("<TE>1,000</TE>", f"<TE><{tag}>9,999</{tag}>1,000</TE>")
    result = bd._export_from_biz_table(html)
    assert result["export_krw"] == 1_000_000_000


@pytest.mark.parametrize("html", [
    "<html><body><p>단위 : 천원</p></body></html>" + TABLE,
    "<html><body>첫 문서</body></html><html><body><p>단위 : 천원</p>" + TABLE + "</body></html>",
    "<div>" * 270 + "<p>단위 : 천원</p>" + TABLE + "</div>" * 270,
    "<section-2></wrong>" * 150 + "<div>" * 270 + "<p>단위 : 천원</p>" + TABLE,
])
def test_silent_tree_truncation_falls_back_without_losing_following_table(html):
    """닫힌 루트/깊이 초과에서 직접 DOM이 버리는 후속 표도 기존처럼 읽는다."""
    result = bd._export_from_biz_table(html)
    assert result["export_krw"] == 1_000_000
    assert result["domestic_krw"] == 2_000_000


def test_large_fragment_preserves_original_path(monkeypatch):
    calls = []
    original = bd._export_candidate_rows_bs4

    def recorded(html):
        calls.append(True)
        yield from original(html)

    monkeypatch.setattr(bd, "_export_candidate_rows_bs4", recorded)
    result = bd._export_from_biz_table(" " * (8 * 1024 * 1024) + "<P>단위 : 천원</P>" + TABLE)
    assert calls == [True]
    assert result["export_krw"] == 1_000_000


@pytest.mark.parametrize("html", ["", "<P>매출액 수출 1,000</P>", "<TABLE><TR><TD>매출액 1,000</TD></TR></TABLE>"])
def test_absent_export_table_stays_absent(html):
    assert bd._export_from_biz_table(html) is None
