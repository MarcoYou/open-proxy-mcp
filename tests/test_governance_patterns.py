import asyncio
import re

import pytest

from open_proxy_mcp.services.governance_patterns import allotment_method_unknown, inspect_filing
from open_proxy_mcp.services.governance_screen import build_governance_task
from open_proxy_mcp.services import screener
from open_proxy_mcp.tools.screener import _render_patterns


def document(html):
    return {"html": html, "text": re.sub(r"<[^>]+>", " ", html)}


def charter(groups):
    parts = []
    for ballot, rows in groups:
        parts.append(f"<P><SPAN>제{ballot}호 의안: 정관 변경의 건</SPAN></P><P>가. 의안의 요지</P>"
                     "<TABLE><TR><TH>현 행</TH><TH>변 경(안)</TH><TH>변경의 목적</TH></TR>")
        for before, after, reason in rows:
            parts.append(f"<TR><TD>{before}</TD><TD>{after}</TD><TD>{reason}</TD></TR>")
        parts.append("</TABLE>")
    return document('<SECTION-2><TITLE>2. 주주총회 목적사항별 기재사항</TITLE><LIBRARY><SECTION-3>'
                    '<TITLE>□ 정관의 변경</TITLE>' + ''.join(parts) + '</SECTION-3></LIBRARY></SECTION-2>')


ALLOT = ("제10조(신주인수권) 제3자배정은 발행주식총수의 10% 이내",
         "제10조(신주인수권) 제3자배정은 발행주식총수의 20% 이내", "배정 한도 확대")
ELECT = ("제17조 주주총회 장소는 본점", "제17조 전자주주총회를 개최할 수 있다", "전자주총 도입")


def test_clauses_in_one_ballot_vs_distinct_ballots():
    one = inspect_filing("주주총회소집공고", charter([("2-1", [ALLOT, ELECT])]))
    by_kind = {s["pattern"]: s for s in one}
    assert by_kind["allotment_authority"]["clauses"][0]["cap_direction"] == "increased"
    assert by_kind["bundled_amendments"]["ballots"][0]["agenda"] == "2-1"
    apart = inspect_filing("주주총회소집공고", charter([("2-1", [ALLOT]), ("2-2", [ELECT])]))
    assert "bundled_amendments" not in {s["pattern"] for s in apart}


def test_conditions_and_terms_need_specific_language():
    doc = document("제2호 의안이 가결됨에 따라 제3호 의안은 폐기. 이사의 임기를 분산하여 선임한다.")
    rows = inspect_filing("정기주주총회결과", doc)
    assert {s["pattern"] for s in rows} == {"conditional_ballot", "staggered_terms"}
    assert all(s["quote"] in doc["text"] for s in rows)
    assert not inspect_filing("주주총회소집공고", document("정관 변경 및 이사 선임. 이사의 임기는 3년."))
    terms, = inspect_filing("주주총회소집공고", document("사내이사 김후보 선임의 건 (임기 2년) 사외이사 이후보 선임의 건 (임기 3년)"))
    assert [(r["name"], r["years"]) for r in terms["nominee_terms"]] == [("김후보", 2), ("이후보", 3)]


def test_no_change_negative_language_and_unknown_allotment_method():
    assert not inspect_filing("주주총회소집공고", document("시차임기제를 도입하지 않는다. 정관 변경을 일괄표결하지 않음."))
    assert not inspect_filing("주주총회소집공고", charter([("2-1", [(ALLOT[0], "제10조(신주인수권) 좌동", "")])]))
    assert allotment_method_unknown("유상증자결정", document("증자 결정"))
    assert not allotment_method_unknown("유상증자결정", document('<TU AUNIT="CI_MTH">주주배정</TU>'))
    assert not allotment_method_unknown("주주총회소집공고", {})


def bond(target):
    html = f'<COMPANY-NAME>(주)시험회사</COMPANY-NAME><TE ACODE="STK_KND">{target}</TE>'
    return document(html + '<TE ACODE="EXE_PRC">8,700</TE><TE ACODE="STK_CNT">6,000</TE>'
                    '<TE ACODE="STK_RT">1.3</TE><TE ACODE="MIN_PRC">7,000</TE>'
                    '<TE ACODE="OPT_FCT">매도청구권은 발행회사가 지정한 자에게 부여</TE>'
                    '<TR><TE ACODE="ISSU_NM">인수법인</TE><TE ACODE="RLT">특수관계인</TE></TR>')


@pytest.mark.parametrize("target,basis", [("시험회사 주식회사 기명식 보통주", "issuer_shares"),
                                         ("주식회사 다른회사 기명식 보통주", "other_company_shares"),
                                         ("보통주식", "unverified")])
def test_bond_type_and_target_are_tied_to_this_receipt(target, basis):
    eb, = inspect_filing("주요사항보고서(교환사채권발행결정)", bond(target))
    assert eb["terms"]["target_basis"] == basis
    assert eb["subscribers"] == [{"name": "인수법인", "reported_relationship": "특수관계인"}]
    cb, = inspect_filing("주요사항보고서(전환사채권발행결정)", bond(target))
    assert cb["pattern"] == "convertible_bond" and "target_basis" not in cb["terms"]
    assert cb["terms"]["adjustment_floor_won"] == "7,000" and "매도청구권" in cb["terms"]["call_option_excerpt"]
    withdrawn, = inspect_filing("[철회]전환사채권발행결정", bond(target))
    assert withdrawn["filing_state"] == "철회 공시" and "terms" not in withdrawn


def test_later_allotment_is_a_reading_pair_not_an_approved_or_paid_transaction():
    rows = []
    for rc, title, doc in [
        ("20260301000001", "주주총회소집공고", charter([("2-1", [ALLOT])])),
        ("20260305000001", "유상증자결정", document('<TU AUNIT="CI_MTH" AUNITVALUE="6">제3자배정증자</TU>')),
        ("20260201000001", "유상증자결정", document('<TE ACODE="CI_MTH">제3자배정증자</TE>')),
        ("20260306000001", "유상증자결정", document('<TE ACODE="CI_MTH">주주배정</TE>')),
    ]:
        rows.append(dict(rcept_no=rc, published=rc[:8], report_nm=title, status="read", text=doc["text"],
                         source_url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rc}",
                         pattern_signals=inspect_filing(title, doc)))
    task = build_governance_task({"corp_name": "시험회사"}, "20260401", {"filings": rows, "matches": rows})
    assert task["allotment_followups"] == [{"amendment": "filing:20260301000001", "allotment": "filing:20260305000001"}]
    assert len(task["pattern_sources"]) == 3 and task["scope_complete"] is False


def test_market_filters_before_pagination_and_keeps_correction_history(monkeypatch):
    reports = ["주요사항보고서(전환사채권발행결정)", "주요사항보고서(교환사채권발행결정)",
               "[기재정정]주요사항보고서(교환사채권발행결정)", "주요사항보고서(신주인수권부사채권발행결정)"]
    opened = []

    class Client:
        def api_call_snapshot(self):
            return len(opened)

        async def search_filings(self, **args):
            assert args["pblntf_detail_ty"] == "B001"
            items = [{"rcept_no": f"2026030700000{i}", "rcept_dt": "20260307", "corp_code": "00000001",
                      "stock_code": "000001", "corp_name": "시험회사", "corp_cls": "Y", "report_nm": title}
                     for i, title in enumerate(reports)]
            return {"list": items, "total_count": len(items)}

        async def get_document_cached(self, rc):
            opened.append(rc)
            return bond("시험회사 보통주") if rc.endswith("2") else {}

    async def universe(_):
        return screener.UniverseFilter(label="전체시장", resolved=True)

    monkeypatch.setattr(screener, "get_dart_client", Client)
    monkeypatch.setattr(screener, "_scan_code", screener._scan_code_uncached)
    monkeypatch.setattr(screener, "resolve_universe", universe)
    args = dict(start_date="20260307", end_date="20260307", max_hits=1)
    kinds = screener.pattern_types("EB 발행")
    first = asyncio.run(screener.build_pattern_screen(kinds, **args))
    assert first["paging"]["matched"] == 2 and first["paging"]["next_offset"] == 1 and len(opened) == 1
    assert "발행회사 주식" in _render_patterns(first)
    last = asyncio.run(screener.build_pattern_screen(kinds, offset=1, **args))
    assert last["status"] == "partial" and last["hits"][0]["pattern_read_status"] == "unassessed"
    assert len(opened) == 2 and not last["paging"]["has_more"]


def test_digest_cannot_borrow_another_bond_series_on_the_same_date():
    payload = {"data": {"events_timeline": [{"rcept_no": "A"}],
                        "exchangeable_bond_events": [{"rcept_no": "B", "total_issue_amount": "10,000"}]}}
    assert screener._extract_dilutive(payload, "B")["amount_won"] == 10000
    assert screener._extract_dilutive(payload, "C") == {}


def test_control_severance_requires_a_linked_benefit_and_preserves_disclosed_basis():
    mention = ("적대적 기업인수 및 합병으로 대표이사 또는 이사가 임기 중 해임되는 경우에는 "
               "통상 퇴직금 외에 대표이사에게 50억원, 이사에게 30억원의 퇴직보상액을 별도로 지급한다.")
    s, = inspect_filing("증권신고서", document(mention))
    p, = s["provisions"]
    assert p["change"] == "mentioned" and p["trigger"] == "경영권 변동과 해임·사임"
    assert p["award_terms"] == ["50억원", "30억원"] and p["roles_mentioned"] == ["대표이사", "이사"]
    assert p["quote"] == mention
    single = "경영권 변경만으로 대표이사에게 특별퇴직금 50억원을 지급한다."
    s, = inspect_filing("주주총회소집공고", document(single))
    assert s["provisions"][0]["trigger"] == "경영권 변동으로 지급"
    for negative in ("경영권 변경을 검토한다. 이사에게는 퇴직위로금을 지급한다.",
                     "경영권 변경 시 이사에게 통상 퇴직금만 지급한다.",
                     "경영권 변동 시 임원 특별퇴직금을 지급하지 않는다."):
        assert not inspect_filing("주주총회소집공고", document(negative))


def test_severance_tracks_the_side_of_the_change_and_dispute_chronology():
    old = "제10조 경영권 변경 시 해임된 대표이사에게 특별퇴직금으로 연봉의 2배를 지급한다."
    new = old.replace("대표이사에게", "대표이사 및 사외이사에게").replace("2배", "4배")
    for before, after, expected in [("(신설)", new, "new"), (old, new, "amended"),
                                    (old, "(삭제)", "removed"), (old, "좌동", "unchanged")]:
        doc = charter([("2", [(before, after, "")])])
        result, = [s for s in inspect_filing("주주총회소집공고", doc) if s["pattern"] == "control_severance"]
        provision, = result["provisions"]
        assert provision["change"] == expected
        assert before in provision["before"] and after in provision["after"]
        if expected == "amended":
            assert len(provision["change_notes"]) == 2
            changed = result
    pub = "20260305"
    rc = pub + "000001"
    row = dict(rcept_no=rc, published=pub, status="read", report_nm="주주총회소집공고",
               text=new, source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + rc, pattern_signals=[changed])
    disputes = [dict(rcept_no=d+"000003", published=d, family="litigation", document_role="제기신청",
                     report_nm="소송 등의 제기·신청(경영권 분쟁소송)") for d in ("20260301", pub, "20260306")]
    discovery = {"matches": [row, *disputes], "filings": [row]}
    task = build_governance_task({}, "20260401", discovery)
    assert [p["dispute_published"] for p in task["severance_dispute_followups"]] == ["20260301"]
    row["report_nm"] = "[기재정정]주주총회소집공고"
    assert not build_governance_task({}, "20260401", discovery)["severance_dispute_followups"]


def test_severance_only_market_view_filters_other_governance_hits(monkeypatch):
    async def page(**params):
        assert params["pattern_kinds"] == {"meeting"} and params["types"] == "agm_notice,agm_result"
        return {"status": "ok", "coverage": [], "counts": {}, "warnings": [], "hits": [
            dict(title="주주총회소집공고", rcept_no="A", stage="상정", is_correction=False)]}

    class Client:
        def api_call_snapshot(self):
            return 0

        async def get_document_cached(self, rc):
            return document("제1호 의안이 가결되면 제2호 안건은 폐기. 경영권 변경 시 대표이사에게 특별퇴직금 30억원을 지급한다.")

    monkeypatch.setattr(screener, "build_screener_payload", page)
    monkeypatch.setattr(screener, "get_dart_client", Client)
    result = asyncio.run(screener.build_pattern_screen(screener.pattern_types("황금낙하산")))
    assert [s["pattern"] for s in result["hits"][0]["pattern_signals"]] == ["control_severance"]


def test_corrected_registration_excludes_superseded_severance_amount():
    old = "적대적 인수합병으로 대표이사가 해임되면 특별퇴직금 90억원을 지급한다."
    new = old.replace("90억원", "30억원")
    doc = document('<DOCUMENT-NAME>증권신고서(지분증권)</DOCUMENT-NAME><TITLE>정 정 신 고 (보고)</TITLE>'
                   f'<P>{old}</P><COVER-TITLE>증 권 신 고 서</COVER-TITLE><P>{new}</P>')
    signal, = inspect_filing("", doc)
    assert "정정" in signal["filing_state"]
    assert signal["provisions"][0]["award_terms"] == ["30억원"] and "90억원" not in str(signal)
