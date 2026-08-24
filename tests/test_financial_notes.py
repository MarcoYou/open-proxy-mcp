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

    # 「7. …의 내역」 쪽이 골라진다. ✅/🔴 는 제목에 「수준별」 같은 말이 섞였는지에
    # 따라 갈리므로 여기서 고정하지 않는다 — 여기서 보는 것은 **어느 표를 골랐나**다.
    assert table["heading"] is True
    assert table["rows"][1][0]["text"] == "채무증권"


def test_extract_refuses_an_off_topic_table_instead_of_returning_it() -> None:
    """제목도 못 맞추고 주제도 다르면 **내보내지 않는다.**

    260823 census 재검증 — 우리은행 FVPL 은 「신용위험의 최대노출액」, 하나은행은
    「공정가치체계」, 국민은행 상각후원가는 316행 「특수관계자와의 채권ㆍ채무」를 물었다.
    전부 계정과목 이름이 그 안에 나오기 때문이다. 유형별 구성으로는 쓸 수 없다.
    """
    html = _doc(
        '<P>당반기말 및 전기말 현재 신용위험의 최대노출액은 다음과 같습니다(단위:백만원).</P>'
        '<TABLE><TR><TD>구분</TD><TD>금액</TD></TR>'
        '<TR><TD>당기손익-공정가치측정금융자산</TD><TD>111</TD></TR>'
        '<TR><TD>합계</TD><TD>222</TD></TR></TABLE>'
    )

    assert extract(html, ["FVPL"])["FVPL"]["status"] == NOT_APPLICABLE


def test_extract_flags_an_untitled_but_on_topic_table() -> None:
    """제목이 없을 뿐인 표는 값을 내되 **그렇다고 말한다.** 조용히 내보내면 인용된다."""
    html = _doc(
        '<P>(4) 금융상품 보유 현황</P>'
        '<TABLE><TR><TD>구분</TD><TD>장부금액</TD></TR>'
        '<TR><TD>당기손익-공정가치측정금융자산</TD><TD>111</TD></TR>'
        '<TR><TD>합계</TD><TD>222</TD></TR></TABLE>'
    )

    assert extract(html, ["FVPL"])["FVPL"]["tables"][0]["title_matched"] is False


def test_title_matches_accepts_a_sibling_anchor_name() -> None:
    """제목에 쓰인 이름이 앵커와 다를 수 있다 — 삼성증권·삼성화재 실측."""
    from open_proxy_mcp.services.financial_notes import ANCHORS, title_matches

    kws = [k for k, kind in ANCHORS["상각후원가"] if kind == "amortized"]
    cap = "8. 상각후원가측정금융자산 (연결) 상각후원가측정금융자산의 내역 당반기말 (단위 : 천원)"

    assert title_matches(cap, "상각후원가측정유가증권") is False
    assert title_matches(cap, kws) is True


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


def test_find_unit_reads_a_bare_won_unit() -> None:
    """「(단위 : 원)」 — 「원」 앞에 글자가 없는 표기를 못 잡고 있었다.

    260823 census — 현대해상 사업보고서 5표·미래에셋생명 4표가 이것 때문에
    「단위 표기 없음」으로 나갔다. 원 단위 회사를 백만원 회사와 나란히 놓으면
    10**6 이 어긋난다. 현대해상은 같은 회사인데 분기가 원, 반기가 천원이다.
    """
    from open_proxy_mcp.services.financial_notes import find_unit

    assert find_unit("", "담보로 제공된 금융자산에 대한 공시 당분기말 (단위 : 원)") == "원"
    assert find_unit("", "(단위 : 천원)") == "천원"
    assert find_unit("", "(단위: 백만원)") == "백만원"
    assert find_unit("", "단위 표기가 아예 없는 문장") is None


def test_title_only_strips_the_previous_tables_numbers() -> None:
    """caption 꼬리에 붙어 오는 **앞 표의 숫자**를 떼어낸다.

    260823 실측 — KB손보 caption 이 「…합계 91,701 450,935 (3) 보고기간말 현재 사용이
    제한되어 있는 예치금 내역은…」인데 91,701 은 **예치금 총액 표**의 합계다
    (사용제한은 26,356). 시험자도 나도 이걸 「빠진 사용제한 표」로 읽었다.
    """
    from open_proxy_mcp.services.financial_notes import title_only

    cap = ("차감 : 대손충당금 (6,578) 합계 91,701 450,935 "
           "(3) 보고기간말 현재 사용이 제한되어 있는 예치금 내역은 다음과 같습니다(단위:백만원).")

    assert title_only(cap).startswith("(3) 보고기간말")
    assert "91,701" not in title_only(cap)


def test_note_heading_beats_a_look_alike_table() -> None:
    """「N. <계정명>」 표제가 붙은 표를 고른다 — KB손보 상각후원가 실측.

    「2) …상각후원가로 측정하는 금융상품의 장부금액과 공정가치」와
    「9. 상각후원가측정유가증권 … 내역」이 둘 다 제목 대조를 통과한다.
    """
    html = _doc(
        '<P>2) 보고기간말 현재 상각후원가로 측정하는 금융상품의 장부금액과 공정가치는 '
        '다음과 같습니다(단위:백만원).</P>'
        '<TABLE><TR><TD>구분</TD><TD>장부금액</TD></TR>'
        '<TR><TD>상각후원가측정유가증권</TD><TD>111</TD></TR>'
        '<TR><TD>합계</TD><TD>222</TD></TR></TABLE>'
        '<P>9. 상각후원가측정유가증권 보고기간말 현재 상각후원가측정유가증권의 내역은 '
        '다음과 같습니다(단위:백만원).</P>'
        '<TABLE><TR><TD>구분</TD><TD>장부금액</TD></TR>'
        '<TR><TD>특수채</TD><TD>20,000</TD></TR>'
        '<TR><TD>합계</TD><TD>29,871</TD></TR></TABLE>'
    )

    table = extract(html, ["상각후원가"])["상각후원가"]["tables"][0]

    assert table["heading"] is True
    assert table["rows"][1][0]["text"] == "특수채"
    # 「장부금액과 공정가치」 표는 원문이 스스로 「내역이 아니다」라고 말한 것이라
    # 골라도 ✅ 를 주지 않는다 — 여기서는 표제가 붙은 쪽이 골라진다.


def test_one_table_holding_both_kinds_is_emitted_once() -> None:
    """「사용이 제한된 예치금 **및** 담보제공자산 등」 — 한 표를 두 번 내면 두 배가 된다.

    260823 메리츠증권 실측: 문서위치 14바이트 차이로 같은 표가 사용제한·담보제공
    양쪽에 잡혔고, 합계 8,396,466,252 천원이 성격별로 더해져 두 배가 됐다.
    """
    html = _doc(
        '<P>31. 사용이 제한된 예치금 및 담보제공자산 등 31-1. 보고기간종료일 현재 '
        '사용이 제한된 예치금의 내역은 다음과 같습니다. (단위: 천원)</P>'
        '<TABLE><TR><TD>구분</TD><TD>금액</TD></TR>'
        '<TR><TD>투자자예탁금</TD><TD>3,345,312</TD></TR>'
        '<TR><TD>합계</TD><TD>8,396,466</TD></TR></TABLE>'
    )

    tables = extract(html, ["사용제한"])["사용제한"]["tables"]

    assert len(tables) == 1
    assert tables[0]["also_kinds"] == ["pledged"]


def test_restricted_table_carries_the_account_it_sits_in() -> None:
    """뺄 대상 계정을 붙인다 — 이게 없으면 unencumbered 계산을 시작할 수 없다."""
    from open_proxy_mcp.services.financial_notes import account_of

    assert account_of("(2) 당반기말 현재 사용이 제한된 현금및현금성자산의 내용은 "
                      "다음과 같습니다.") == "현금및현금성자산"
    assert account_of("(3) 보고기간말 현재 사용이 제한되어 있는 예치금 내역은 "
                      "다음과 같습니다.") == "예치금"


def test_tables_are_labelled_consolidated_or_separate_by_position() -> None:
    """HTML 표에는 연결/별도 표시가 없다 — 「4. 재무제표」 경계로 가른다.

    260823 시험자 지적: 같은 제목의 표가 두 번 나오는데 어느 쪽인지 몰라 합산하면
    이중계상이 난다. XBRL 은 값마다 ACONTEXT 에 박혀 있지만 표구조 경로에는 없다.
    실측 경계 — KB손보 1,378,097 · 국민은행 1,755,152 · 신한 1,847,471 · 우리 1,885,740.
    """
    html = _doc(
        '<P>(3) 보고기간말 현재 사용이 제한되어 있는 예치금 내역은 다음과 같습니다.</P>'
        '<TABLE><TR><TD>구분</TD><TD>당반기말</TD></TR>'
        '<TR><TD>기타예금</TD><TD>14,963</TD></TR>'
        '<TR><TD>합계</TD><TD>26,356</TD></TR></TABLE>'
        '<P>4. 재무제표</P>'
        '<P>(3) 보고기간말 현재 사용이 제한되어 있는 예치금 내역은 다음과 같습니다.</P>'
        '<TABLE><TR><TD>구분</TD><TD>당반기말</TD></TR>'
        '<TR><TD>기타예금</TD><TD>14,000</TD></TR>'
        '<TR><TD>합계</TD><TD>15,007</TD></TR></TABLE>'
    )

    tables = extract(html, ["사용제한"])["사용제한"]["tables"]

    assert [t["table_basis"] for t in tables] == ["연결", "별도"]


def test_basis_is_unknown_when_the_document_has_no_separate_section() -> None:
    """별도재무제표가 없는 문서는 「판별 못함」이다 — 없는 것을 있다고 하지 않는다."""
    from open_proxy_mcp.services.financial_notes import basis_at, separate_offset

    html = _doc('<P>연결만 실린 문서</P>')

    assert separate_offset(html) is None
    assert basis_at(1_000, None) is None


def test_basis_defaults_to_consolidated_and_halves_the_fetch() -> None:
    """기본은 **연결** — 두 기준을 다 받으면 받는 양도 호출 수도 두 배다(260824 마스터 지시).

    실측(NH투자증권 사업2025) — FVPL 1필드: 연결만 18,339자 1.4초 / 전체 36,682자 5.1초.
    """
    from open_proxy_mcp.tools.financial_notes import _basis_wanted

    assert _basis_wanted("") == {"연결"}          # 기본
    assert _basis_wanted("연결") == {"연결"}
    assert _basis_wanted("별도") == {"별도"}
    assert _basis_wanted("전체") is None          # 제한 없음
    assert _basis_wanted("둘다") is None


# ── 260824 NH투자증권 — 병합 격자·열 경로·검산 ──────────────────────────────
#
# 발단: 「7. 상각후원가측정금융자산」(머리 8행 × 값 1행 × 27열 전치표)에서 시험자도
# 렌더 UI 도 열을 밀려 읽어 예치금 소계를 12,731,887 로 냈다. 원문 합계표의 값은
# 12,131,887 이다. 원인 셋을 여기서 못 박는다.

_NH = (
    "<TABLE border='1'>"
    "<THEAD>"
    "<TR><TH>　</TH><TH colspan='6'>금융자산의 범주</TH></TR>"
    "<TR><TH>　</TH><TH colspan='3'>예치금</TH><TH colspan='3'>기타금융자산</TH></TR>"
    "<TR><TH>　</TH>"
    "<TH rowspan='2'>청약예치금</TH><TH rowspan='2'>기타 예치금</TH>"
    "<TH rowspan='2'>당좌개설보증금</TH>"
    "<TH colspan='3'>미수금</TH></TR>"
    "<TR><TH>　</TH><TH>증권미수금</TH><TH>기타 미수금</TH><TH>미수수수료</TH></TR>"
    "</THEAD>"
    "<TBODY><TR><TD>상각후원가로 측정하는 금융자산</TD>"
    "<TD>17</TD><TD>2,853,625</TD><TD>47</TD>"
    "<TD>21,733,958</TD><TD>1,173,435</TD><TD>88,403</TD></TR></TBODY>"
    "</TABLE>"
)


def test_rowspan_is_counted_so_a_rectangular_table_is_not_called_ragged() -> None:
    # colspan 만 더하던 시절 이 표는 [2, 3, 5, 5] 로 세어져 🔴 오경보가 나갔다.
    parsed = parse_table(_NH)

    assert parsed["widths"] == [7, 7, 7, 7, 7]
    assert parsed["ragged"] is False


def test_single_quoted_colspan_is_read() -> None:
    # DART 뷰어가 내려주는 절 HTML 은 속성을 홑따옴표로 쓴다. 겹따옴표만 받으면
    # 병합 폭을 한 번도 못 잡고, 그러면 격자를 펼 수 없다.
    parsed = parse_table("<TABLE><TR><TD colspan='3'>가</TD></TR>"
                         "<TR><TD>1</TD><TD>2</TD><TD>3</TD></TR></TABLE>")

    assert parsed["rows"][0][0]["colspan"] == 3
    assert parsed["widths"] == [3, 3]


def test_column_view_gives_every_value_its_full_column_name() -> None:
    from open_proxy_mcp.services.financial_notes import column_view

    view = column_view(parse_table(_NH))
    labels = {c["label"]: c["values"][0] for c in view["columns"]}

    assert view["n_cols"] == 7
    assert view["rows"] == ["상각후원가로 측정하는 금융자산"]
    # 🔴 이 한 줄이 이번 사건의 핵심이다 — 21,733,958 은 총계가 아니라 증권미수금이다.
    assert labels["기타금융자산 › 미수금 › 증권미수금"] == "21,733,958"
    assert labels["예치금 › 청약예치금"] == "17"


def test_checksum_sums_each_column_group_of_a_transposed_table() -> None:
    from open_proxy_mcp.services.financial_notes import checksums, column_view

    got = {c["group"]: c["sum"] for c in checksums(column_view(parse_table(_NH)))}

    assert got["예치금"] == "2,853,689"                     # 17 + 2,853,625 + 47
    assert got["기타금융자산 › 미수금"] == "22,995,796"


def test_checksum_refuses_to_add_measure_columns() -> None:
    # 묶음마다 잎 이름이 똑같으면 그건 항목이 아니라 측정 축이다 — 더하면 뜻이 없다.
    from open_proxy_mcp.services.financial_notes import checksums, column_view

    html = ("<TABLE><THEAD>"
            "<TR><TH>　</TH><TH colspan='2'>예치금</TH><TH colspan='2'>대출채권</TH></TR>"
            "<TR><TH>　</TH><TH>총장부금액</TH><TH>손상차손누계액</TH>"
            "<TH>총장부금액</TH><TH>손상차손누계액</TH></TR></THEAD>"
            "<TBODY><TR><TD>금융자산</TD><TD>12,131,887</TD><TD>(7,010)</TD>"
            "<TD>14,735,015</TD><TD>(313,781)</TD></TR></TBODY></TABLE>")

    assert checksums(column_view(parse_table(html))) == []


def test_ordinary_table_is_checked_by_row_not_by_column() -> None:
    # KB손해보험 사용제한표 — 열이 「당반기말·전기말」이라 열을 더하면 44+44=88 이 나온다.
    from open_proxy_mcp.services.financial_notes import (
        checksums, column_view, row_checksums,
    )

    html = ("<TABLE>"
            "<TR><TH>구분</TH><TH>당반기말</TH><TH>전기말</TH><TH>사용제한 내용</TH></TR>"
            "<TR><TD>특정예금</TD><TD>44</TD><TD>44</TD><TD>당좌개설보증금</TD></TR>"
            "<TR><TD>기타예금</TD><TD>14,963</TD><TD>380,800</TD><TD>압류계좌</TD></TR>"
            "<TR><TD>외화정기예금 등</TD><TD>11,349</TD><TD>10,238</TD><TD>영업보증금</TD></TR>"
            "<TR><TD>합계</TD><TD>26,356</TD><TD>391,082</TD><TD></TD></TR></TABLE>")
    view = column_view(parse_table(html))

    assert checksums(view, transposed=False) == []
    got = {r["column"]: r for r in row_checksums(view)}
    assert got["당반기말"]["sum"] == "26,356" and got["당반기말"]["ok"] is True
    assert got["전기말"]["sum"] == "391,082" and got["전기말"]["ok"] is True


def test_row_checksum_flags_a_total_that_does_not_add_up() -> None:
    from open_proxy_mcp.services.financial_notes import column_view, row_checksums

    html = ("<TABLE>"
            "<TR><TH>구분</TH><TH>금액</TH></TR>"
            "<TR><TD>가</TD><TD>10</TD></TR>"
            "<TR><TD>나</TD><TD>20</TD></TR>"
            "<TR><TD>합계</TD><TD>31</TD></TR></TABLE>")

    (rec,) = row_checksums(column_view(parse_table(html)))
    assert rec["sum"] == "30" and rec["stated"] == "31" and rec["ok"] is False


def test_caption_stops_at_the_previous_value_table() -> None:
    # 「…, 합계」 제목표가 앞 표의 숫자 잔해에 밀려 「제목 없음」으로 나가던 문제.
    from open_proxy_mcp.services.financial_notes import caption_before

    html = ("<TABLE><TR><TD>1,111</TD><TD>2,222</TD><TD>3,333</TD></TR></TABLE>"
            "<TABLE class='nb'><TR><TD>상각후원가측정금융자산의 내역, 합계</TD></TR>"
            "<TR><TD>당반기말</TD><TD>(단위 : 백만원)</TD></TR></TABLE>"
            "<TABLE border='1'>")

    cap = caption_before(html, html.rindex("<TABLE border='1'>"))
    assert "1,111" not in cap
    assert "상각후원가측정금융자산의 내역, 합계" in cap
