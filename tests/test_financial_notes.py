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


def test_extract_skips_risk_table_and_takes_the_titled_note_table() -> None:
    """앵커 첫 출현이 정답이 아니다 — 260823 KB손보·NH증권 실측.

    「당기손익-공정가치측정금융자산」은 공정가치수준별·금리위험 표에도 똑같이 나온다.
    원문이 「<앵커>…내역」이라는 제목을 붙여준 표를 골라야 한다.
    """
    html = _doc(
        '<P>1) 보고기간말 현재 공정가치로 측정되는 금융상품의 공정가치수준별 내역은 '
        '다음과 같습니다(단위:백만원).</P>'
        '<TABLE><TR><TD>구분</TD><TD>수준1</TD></TR>'
        '<TR><TD>당기손익-공정가치측정금융자산</TD><TD>111</TD></TR>'
        '<TR><TD>합계</TD><TD>222</TD></TR></TABLE>'
        '<P>7. 당기손익-공정가치측정금융자산 보고기간말 현재 '
        '당기손익-공정가치측정금융자산의 내역은 다음과 같습니다(단위:백만원).</P>'
        '<TABLE><TR><TD>구분</TD><TD>장부금액</TD></TR>'
        '<TR><TD>채무증권</TD><TD>333</TD></TR>'
        '<TR><TD>합계</TD><TD>444</TD></TR></TABLE>'
    )

    table = extract(html, ["FVPL"])["FVPL"]["tables"][0]

    assert table["title_matched"] is True
    assert table["rows"][1][0]["text"] == "채무증권"


def test_extract_flags_the_table_when_no_title_matched() -> None:
    """제목을 못 맞췄으면 값을 내되 **그렇다고 말한다.** 조용히 내보내면 인용된다."""
    html = _doc(
        '<P>(4) 금융상품의 금리위험 익스포져현황</P>'
        '<TABLE><TR><TD>구분</TD><TD>1년이내</TD></TR>'
        '<TR><TD>당기손익-공정가치측정금융자산</TD><TD>111</TD></TR>'
        '<TR><TD>합계</TD><TD>222</TD></TR></TABLE>'
    )

    table = extract(html, ["FVPL"])["FVPL"]["tables"][0]

    assert table["title_matched"] is False


def test_find_report_candidates_separates_half_year_from_quarter() -> None:
    """`quarterly` 는 분기와 반기를 함께 잡는다 — 분기만 보려면 갈라져야 한다."""
    import asyncio

    from open_proxy_mcp.services.business_details import _find_report_candidates

    class _Client:
        def __init__(self) -> None:
            self.asked: list[str] = []

        async def search_filings(self, **kw):
            self.asked.append(kw["pblntf_detail_ty"])
            return {"list": [
                {"report_nm": "반기보고서 (2026.06)", "rcept_no": "1", "rcept_dt": "20260814"},
                {"report_nm": "분기보고서 (2026.03)", "rcept_no": "2", "rcept_dt": "20260514"},
            ]}

    def _names(period: str) -> list[str]:
        c = _Client()
        got = asyncio.run(_find_report_candidates(c, "X", period))
        return [r["report_nm"].split()[0] for r in got]

    assert _names("half") == ["반기보고서"]
    assert _names("quarter") == ["분기보고서"]
    assert set(_names("quarterly")) == {"반기보고서", "분기보고서"}
