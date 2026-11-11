import asyncio
from pathlib import Path

from open_proxy_mcp.services import tender_offer as to

FIX = Path(__file__).parent / "dart_responses" / "tender_offer"


def _html(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_filing_terms_are_raw_cells_with_section_hints():
    got = to.parse_tender_offer_document(_html("offer_filing_20240913000040.html"), "공개매수신고서")
    assert got["status"] == "parsed" and got["kind"] == "filing"
    f = got["fields"]
    assert f["offers"][0]["price"] == "660,000원"
    assert "최소 1,445,036주" in f["offers"][0]["quantity"] and "최대 3,024,881" in f["offers"][0]["quantity"]
    assert f["period_start"].replace(" ", "") == "2024년9월13일"
    assert f["period_end"].replace(" ", "") == "2024년10월4일"
    assert f["settlement_date"] == "2024년 10월 10일"
    assert "경영권을 공고히" in f["purpose_excerpt"]
    assert got["read_hints"] == {"purpose": 11, "terms": 12}  # 절 번호 = filing_section 의 no


def test_result_report_reads_planned_tendered_purchased():
    f = to.parse_tender_offer_document(_html("result_20241017000004.html"), "공개매수결과보고서")["fields"]
    assert (f["planned"], f["tendered"], f["purchased"]) == ("최대 3,024,881주", "1,105,163주", "1,105,163주")
    assert f["price"] == "주당 830,000원" and f["post_stake_pct"] == "38.47"


def test_opinion_statement_reads_stated_opinion_only():
    got = to.parse_tender_offer_document(_html("opinion_20240913000068.html"), "공개매수에관한의견표명서")
    assert got["status"] == "parsed" and got["fields"]["opinion"] == "반대"


def test_unknown_form_says_so_instead_of_inventing_terms():
    got = to.parse_tender_offer_document("<DOCUMENT><P>본문만</P></DOCUMENT>", "공개매수신고서")
    assert got["status"] == "terms_absent" and got["fields"] == {}


def test_enrich_marks_fetch_failed_and_defers_prospectus(monkeypatch):
    class Fake:
        async def get_document_cached(self, rcept_no):
            if rcept_no == "BAD":
                raise RuntimeError("boom")
            return {"html": _html("result_20241017000004.html")}

    monkeypatch.setattr(to, "get_dart_client", lambda: Fake())
    rows = [{"rcept_no": "P1", "report_name": "공개매수설명서"},
            {"rcept_no": "R1", "report_name": "공개매수결과보고서"},
            {"rcept_no": "BAD", "report_name": "공개매수신고서"}]
    stats = asyncio.run(to.enrich_tender_offer_rows(rows, max_lookups=2))
    assert [r["document_status"] for r in rows] == ["not_looked_up", "parsed", "fetch_failed"]
    assert stats["not_looked_up"] == 1 and stats["fetch_failed"] == 1 and rows[1]["tender"]["kind"] == "result"


def test_attachment_only_correction_is_no_body_not_fetch_failed(monkeypatch):
    class Fake:
        async def get_document_cached(self, rcept_no):
            raise to.DartClientError("014", "파일이 존재하지 않습니다.")

    monkeypatch.setattr(to, "get_dart_client", lambda: Fake())
    rows = [{"rcept_no": "A1", "report_name": "[첨부정정]공개매수신고서"}]
    stats = asyncio.run(to.enrich_tender_offer_rows(rows))
    assert rows[0]["document_status"] == "no_body" and stats["no_body"] == 1 and stats["fetch_failed"] == 0


def test_offerors_and_purpose_checkboxes_from_summary_and_cover():
    f = to.parse_tender_offer_document(_html("offer_filing_20240913000040.html"), "공개매수신고서")["fields"]
    assert [o["name"] for o in f["offerors"]] == ["주식회사 한국기업투자홀딩스", "주식회사 영풍"]
    assert f["offerors"][1]["relation_to_target"] == "대상회사의 최대주주 또는 임원"
    assert f["purpose_checked"] == ["경영권안정"]
    # 결과보고서·의견표명서에도 누가 제안했는지는 표지에서 읽힌다
    assert [o["name"] for o in to.parse_tender_offer_document(_html("result_20241017000004.html"), "공개매수결과보고서")["fields"]["offerors"]] \
        == ["주식회사 한국기업투자홀딩스", "주식회사 영풍"]
    assert [o["name"] for o in to.parse_tender_offer_document(_html("opinion_20240913000068.html"), "공개매수에관한의견표명서")["fields"]["offerors"]] \
        == ["주식회사 영풍", "주식회사 한국기업투자홀딩스"]


def test_name_stops_before_address_and_same_company_written_two_ways_is_one_offeror():
    cover = ('<TABLE><TR><TD>공 개 매 수 자</TD><TD>성 명 : (주)에스원 주 소 : 서울</TD></TR></TABLE>'
             '<TABLE><TR><TD>공개매수자</TD><TD>공개매수자</TD><TD>ㆍ성명 : 주식회사 에스원 ■ 회사 □ 개인</TD></TR>'
             '<TR><TD>공개매수자</TD><TD>공개매수자</TD><TD>ㆍ대상회사와의 관계 ■ 대상회사 본인 □ 계열사</TD></TR>'
             '<TR><TD>공개매수 목적</TD><TD>공개매수 목적</TD><TD>□ 경영권안정 ■ 기타 - 내용: 자기주식 소각</TD></TR></TABLE>')
    f = to.parse_tender_offer_document(cover, "공개매수신고서")["fields"]
    assert f["offerors"] == [{"name": "(주)에스원", "type": "회사", "relation_to_target": "대상회사 본인"}]
    assert f["purpose_checked"] == ["기타"] and f["purpose_note"] == "자기주식 소각"


def test_opinion_wording_variants():
    neutral = "<P>3. 공개매수에 대한 의견과 그 이유 당사 이사회는 본건 공개매수에 대하여 중립의 의견을 표명합니다.</P>"
    got = to.parse_tender_offer_document(neutral, "공개매수에관한의견표명서")
    assert got["status"] == "parsed" and got["fields"]["opinion"] == "중립" and "중립의 의견" in got["fields"]["opinion_context"]


def test_result_report_with_depositary_receipts_reads_증권수():
    html = ('<TABLE><TR><TD>공개매수대상 주식등</TD><TD>예정수량</TD><TD>매수가격</TD></TR>'
            '<TR><TD>JTC KDR</TD><TD>20,867,000증권</TD><TD>증권당 4,309원</TD></TR></TABLE>'
            '<TABLE><TR><TD>주식등의 종류</TD><TD>예정증권수</TD><TD>응모증권수</TD><TD>매수증권수</TD></TR>'
            '<TR><TD>JTC KDR</TD><TD>20,867,000증권</TD><TD>18,000,000증권</TD><TD>18,000,000증권</TD></TR></TABLE>')
    got = to.parse_tender_offer_document(html, "공개매수결과보고서")
    assert got["status"] == "parsed" and got["fields"]["tendered"] == "18,000,000증권" and got["fields"]["price"] == "증권당 4,309원"


def test_old_format_period_in_summary_line_and_cr_entity_stripped():
    old = ('<TABLE><TR><TD>공 개 매 수 자</TD><TD>성 명 : (주)이지홀딩스 &cr;</TD></TR></TABLE>'
           '<TABLE><TR><TD>공개매수기간</TD><TD>2021. 04. 22. - 2021. 05. 12.(결제일: 2021. 05. 17)</TD></TR></TABLE>')
    got = to.parse_tender_offer_document(old, "공개매수신고서")
    f = got["fields"]
    assert got["status"] == "parsed" and [o["name"] for o in f["offerors"]] == ["(주)이지홀딩스"]
    assert (f["period_start"], f["period_end"], f["settlement_date"]) == ("2021. 04. 22.", "2021. 05. 12.", "2021. 05. 17")


def test_amendment_without_cover_name_inherits_offeror_from_same_filer_and_says_where(monkeypatch):
    named = '<TABLE><TR><TD>공 개 매 수 자</TD><TD>성 명 : 알파홀딩스 주식회사</TD></TR></TABLE>'
    bare = '<TABLE><TR><TD>항 목</TD><TD>정정사유</TD></TR></TABLE>'

    class Fake:
        async def get_document_cached(self, rcept_no):
            return {"html": named if rcept_no == "ORIG" else bare}

    monkeypatch.setattr(to, "get_dart_client", lambda: Fake())
    rows = [{"rcept_no": "ORIG", "report_name": "공개매수신고서", "filer_name": "알파홀딩스"},
            {"rcept_no": "FIX", "report_name": "[기재정정]공개매수신고서", "filer_name": "알파홀딩스"},
            {"rcept_no": "OTHER", "report_name": "[기재정정]공개매수신고서", "filer_name": "베타"}]
    asyncio.run(to.enrich_tender_offer_rows(rows))
    fixed = rows[1]["tender"]["fields"]
    assert fixed["offerors"] == [{"name": "알파홀딩스 주식회사"}] and fixed["offerors_from"] == "ORIG"
    assert "offerors" not in rows[2]["tender"]["fields"]  # 제출인이 다르면 가져오지 않는다
