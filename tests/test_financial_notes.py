from __future__ import annotations

from open_proxy_mcp.services.financial_notes import (
    EXTRACTION_FAILED,
    NOT_APPLICABLE,
    OK,
    extract,
    parse_table,
)


def _doc(body: str) -> str:
    return (
        "<P>II. 사업의 내용</P>"
        "<P>목차: 사용이 제한된 예금</P>"
        "<P>III. 재무에 관한 사항</P>"
        f"{body}"
    )


def test_extract_keeps_plain_table_shape_and_unit() -> None:
    html = _doc(
        '<P>보고기간말 현재 사용이 제한되어 있는 예치금 내역</P>'
        '<TABLE><CAPTION>(단위: 백만원)</CAPTION>'
        '<TR><TH>구분</TH><TH>당반기말</TH><TH>전기말</TH><TH>사용제한 내용</TH></TR>'
        '<TR><TD>기타예금</TD><TD>26,356</TD><TD>391,082</TD><TD>압류계좌</TD></TR>'
        '<TR><TD>합계</TD><TD>26,356</TD><TD>391,082</TD><TD></TD></TR></TABLE>'
    )

    result = extract(html, ["사용제한"])["사용제한"]

    assert result["status"] == OK
    table = result["tables"][0]
    assert table["format"] == "html_table"
    assert table["unit"] == "백만원"
    assert table["rows"][1][1]["text"] == "26,356"
    assert table["rows"][1][3]["text"] == "압류계좌"


def test_extract_reads_xbrl_attributes_without_using_column_position() -> None:
    html = _doc(
        '<P>당기손익-공정가치측정금융자산</P>'
        '<TABLE><TR><TH>구분</TH><TH>금액</TH></TR>'
        '<TR><TD>투자자예탁금별도예치금(신탁)</TD>'
        '<TE ACODE="ifrs-full_FinancialAssets" '
        'ACONTEXT="CFY2026eHYA_ConsolidatedMember_dart_SeparatePortionOfCustomersDepositsMember">8,911,269</TE></TR>'
        '<TR><TD>합계</TD><TE ACODE="dart_Total" ACONTEXT="CFY2026eHYA_SeparateMember">9,000,000</TE></TR>'
        '</TABLE>'
    )

    result = extract(html, ["FVPL"])["FVPL"]

    assert result["status"] == OK
    table = result["tables"][0]
    assert table["format"] == "xbrl_tagged"
    tagged = table["rows"][1][1]
    assert tagged["acode"] == "ifrs-full_FinancialAssets"
    assert tagged["acontext"].endswith("SeparatePortionOfCustomersDepositsMember")
    assert tagged["basis"] == "연결"


def test_extract_does_not_treat_table_of_contents_as_the_note() -> None:
    html = _doc(
        '<P>당기손익-공정가치측정금융자산</P>'
        '<TABLE><TR><TD>목차</TD><TD>1</TD></TR></TABLE>'
        '<P>실제 주석</P>'
        '<TABLE><TR><TD>구분</TD><TD>금액</TD></TR>'
        '<TR><TD>채권</TD><TD>100</TD></TR>'
        '<TR><TD>주식</TD><TD>200</TD></TR></TABLE>'
    )

    result = extract(html, ["FVPL"])["FVPL"]

    assert result["status"] == OK
    assert result["tables"][0]["rows"][1][0]["text"] == "채권"


def test_extract_marks_missing_field_as_not_applicable() -> None:
    result = extract(_doc("<P>주석 없음</P>"), ["FVOCI"])["FVOCI"]

    assert result["status"] == NOT_APPLICABLE
    assert result["tables"] == []


def test_parse_table_handles_empty_or_malformed_table_without_fabricating_rows() -> None:
    result = parse_table("<TABLE><TR><TD>캡션만</TD></TR></TABLE>")

    assert result["n_rows"] == 1
    assert result["n_numeric"] == 0
    assert EXTRACTION_FAILED != result["format"]
