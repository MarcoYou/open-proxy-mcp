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
    row["pattern_signals"] = [{**changed, "pattern": "retirement_limit"}]
    assert not build_governance_task({}, "20260401", discovery)["severance_dispute_followups"]
    row["pattern_signals"] = [changed]
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


def test_allotment_reason_identifies_an_explicit_share_issuance_clause():
    before = "발행주식총수의 100분의 10을 초과하지 않는 범위에서 국내외 금융기관에 신주를 발행하는 경우"
    after = before.replace("10을", "20을")
    reason = "3자배정 유상증자 시 발행가능주식 수 산정법 변경"
    signals = inspect_filing("주주총회소집공고", charter([("2-1", [(before, after, reason)])]))
    authority, = [s for s in signals if s["pattern"] == "allotment_authority"]
    assert authority["clauses"][0]["cap_direction"] == "increased"
    unrelated = inspect_filing("주주총회소집공고", charter([("2-1", [("이사 선임", "이사 해임", reason)])]))
    assert "allotment_authority" not in {s["pattern"] for s in unrelated}


def test_retirement_annex_keeps_rate_with_reason_and_award():
    def annex(rows):
        return ('<table><tr><th>구분</th><th>주요내용</th><th>지급한도</th></tr>' + rows + '</table>')

    rows = ('<tr><td rowspan="2">퇴직위로금</td><td>업무상 사망</td><td>퇴직금의 100% 이내</td></tr>'
            '<tr><td>업무 외 부상</td><td>퇴직금의 25% 이내</td></tr>'
            '<tr><td>특별공로금</td><td>특허 취득</td><td>퇴직금의 50% 이내</td></tr>')
    old = annex(rows.replace('100%', '80%'))
    heading = '<p>임원 퇴직금 지급규정 변경의 건</p>'
    comparison = ('<table><tr><th>변경전 내용</th><th>변경후 내용</th></tr>'
                  '<tr><td>' + old + '</td><td>' + annex(rows) + '</td></tr></table>')
    def limits(html):
        return next((s['provisions'] for s in inspect_filing('주주총회소집공고', document(html))
                     if s['pattern'] == 'retirement_limit'), [])
    result = limits(heading + comparison)
    assert [(p['award_type'], p['condition'], p['limit_formula_text']) for p in result] == [
        ('퇴직위로금', '업무상 사망', '퇴직금의 100% 이내'), ('퇴직위로금', '업무 외 부상', '퇴직금의 25% 이내'),
        ('특별공로금', '특허 취득', '퇴직금의 50% 이내')]
    assert all(p['separate_limit'] and not p['outside_pay_limit'] and p['change'] != 'new' for p in result)
    assert all(p['change'] == 'new' for p in limits(heading + comparison.replace(old, '별표(신설)')))
    assert not limits(heading + comparison.replace(annex(rows), '삭제'))
    assert not limits('<p>직원 퇴직금 지급규정</p>' + annex(rows))


def test_spelled_out_severance_preserves_and_normalizes_won():
    def provision(amount):
        text = f'임원이 적대적 M&A로 해임되는 경우 퇴직금 외에 특별퇴직금으로 금 {amount}을 추가 지급한다.'
        return inspect_filing('주주총회소집공고', document(text))[0]['provisions'][0]
    for amount in (r'삼십억원(\3,000,000,000)', '삼십억원', '30억원', '₩3,000,000,000'):
        p = provision(amount)
        assert p['award_amounts'] == [{'text': amount, 'amount_krw': 3_000_000_000}]
        assert amount in p['award_terms']
    assert not provision('삼십억원(₩300,000,000)')['award_amounts']


def test_discretion_on_each_directors_term_belongs_to_the_term_clause():
    before = '제34조(이사의 임기) 이사의 임기는 3년으로 한다.'
    discretionary = '이사회 구성의 전문성 및 다양성 등을 고려하여 이사별로 달리 정할 수 있다.'
    def term(after):
        return next((s for s in inspect_filing('주주총회소집공고', charter([('2', [(before, after, '')])]))
                     if s['pattern'] == 'staggered_terms'), None)
    after = '제34조(이사의 임기) 이사의 임기는 3년 이내로 하되, ' + discretionary
    assert term(after)['clause_change']['after'] == after
    removed = charter([('2', [(after, before, '임기 단일화')])])
    assert 'staggered_terms' not in {v['pattern'] for v in inspect_filing('주주총회소집공고', removed)}
    assert inspect_filing('정관', document(after))[0]['pattern'] == 'staggered_terms'
    assert not term(after.replace('달리 정할 수 있다', '달리 정할 수 없다'))
    assert not term('제34조(이사의 보수) 보수액은 ' + discretionary)


@pytest.mark.parametrize('target,expected', [
    ('주식회사 가람 발행 기명식 보통주(자기주식)', 'issuer_shares'),
    ('㈜가람 기명식 보통주식（자사주）', 'issuer_shares'),
    ('주식회사 나래 발행 기명식 보통주식', 'other_company_shares'),
    ('주식회사 나래 발행 기명식 보통주(자기주식)', 'unverified'),
    ('기명식 보통주', 'unverified'),
])
def test_exchange_issuer_and_target_annotation(target, expected):
    from open_proxy_mcp.services.governance_patterns import _exchange_target
    assert _exchange_target('<COMPANY-NAME>(주)가람</COMPANY-NAME>', target) == expected


def test_total_pay_limit_is_not_an_extra_retirement_limit():
    cap = "이사 전원에게 지급하는 보수의 연간 총액 한도는 [ 이십억 ] 원으로 한다."
    clause = "임원퇴직금지급규정에 따라 지급되는 퇴직금이나 특별공로금은 포함하지 아니한다."
    def check(body):
        return {s["pattern"]: s for s in inspect_filing("주주총회소집공고", document(body))}
    p, = check("<TABLE><TR><TD>" + cap + " 제4조 " + clause + "</TD></TR></TABLE>")["retirement_limit"]["provisions"]
    assert p["outside_pay_limit"] and not p["separate_limit"] and not p["limit_amount_text"]
    assert p["pay_cap_won"] == 2_000_000_000
    assert not check(cap + clause.replace("포함하지 아니한다", "포함한다"))
    assert not check(cap + " 이사의 퇴직금은 별도 규정에 따른다.")


def test_distinguish_actual_board_rejection_and_bundling_from_future_conditional_vote():
    def kinds(text):
        return {s["pattern"] for s in inspect_filing("주주총회소집결의", document(text))}
    assert kinds("주주 10명이 제1호 의안을 청구했습니다. 이사회에서 부결되어 주주총회 안건으로 상정되지 않았습니다.") == {"proposal_not_tabled"}
    assert kinds("일부 조항만 가결될 경우 조문 정합성을 위해 하나의 의안으로 상정합니다.") == {"bundled_amendments"}
    assert kinds("제1호 의안이 가결될 경우 제2호 의안은 폐기한다.") == {"conditional_ballot"}
    assert not kinds("주주제안이 이사회에서 가결되어 주주총회 안건으로 상정되었습니다.")


def test_exercise_matches_only_same_bond_and_current_conversion_date():
    from open_proxy_mcp.services.governance_disclosure_evidence import confirmed_followup
    issue = {"pattern":"conversion_advanced", "series":"3", "after_date":"2026-11-13"}
    exercises = [{"pattern":"conversion_exercised","exercises":[
        {"date":"2026-11-20","series":"4","amount":"900원","shares":"99"},
        {"date":"2026-11-12","series":"3","amount":"850원","shares":"88"},
        {"date":"2026-11-20","series":"3","amount":"200원","shares":"20"}]}]
    quote = confirmed_followup(issue, {}, exercises)
    assert "200원" in quote and "900원" not in quote and "850원" not in quote


def test_treasury_exchange_does_not_report_compensation_or_superseded_quantity():
    def read(text):
        return inspect_filing("[기재정정]자기주식처분결과보고서", document(text))
    assert not read("자기주식을 임직원에게 지급한다. 처분목적 주식보상.")
    source = ("정정 전 100주는 교환청구된 것으로 간주하였다. 1. 발행인의 명칭 및 주소 "
              "교환대상 자기주식 중 50주는 교환청구된 것으로 간주하였다. 잔여 50주는 환입 예정이다.")
    signal, = read(source)
    assert "50주는 교환청구" in signal["quote"] and "100주는" not in signal["quote"]


def test_call_option_ceiling_is_distinct_from_whole_bond():
    html = '<TE ACODE="OPT_FCT">매도청구권. 취득규모: 최대 3,000원(Call Option 30%). 전환가액 조정 후에는 최대 250주까지 취득 가능하다.</TE>'
    terms = inspect_filing("전환사채권발행결정", document(html))[0]["terms"]
    assert (terms["call_limit_won"], terms["call_limit_pct"], terms["call_adjusted_max_shares"]) == ("3,000", "30", "250")
    assert "reported_share_pct" not in terms
