"""Public-source discovery contracts; synthetic filings do not measure LLM accuracy."""
import asyncio
import json

import pytest

from open_proxy_mcp.services.guideline_research import (
    ResearchQuery, build_guideline_research_plan, build_meeting_research_plan,
    discover_guideline_context, discover_research_sources,
)


def filing(receipt, title, published=None, filer="공시제출자"):
    return {"rcept_no": receipt, "rcept_dt": published or receipt[:8],
            "report_nm": title, "flr_nm": filer}


class PublicClient:
    def __init__(self, by_code=None, *, fail=(), total_pages=1, empty_documents=()):
        self.by_code = by_code or {}
        self.fail = set(fail)
        self.total_pages = total_pages
        self.empty_documents = set(empty_documents)
        self.calls = []
        self.documents = []

    async def search_filings(self, **kwargs):
        self.calls.append(kwargs)
        detail = kwargs["pblntf_detail_ty"]
        assert kwargs["corp_code"] == "00000001"
        assert kwargs["pblntf_ty"] == "" and detail
        assert kwargs["last_reprt_at"] == "N"
        if detail in self.fail:
            raise RuntimeError("SECRET_KEY_MUST_NOT_ESCAPE")
        rows = self.by_code.get(detail, [])
        return {"status": "000", "list": rows, "total_count": len(rows),
                "total_page": self.total_pages}

    async def get_document_cached(self, receipt):
        self.documents.append(receipt)
        return {"text": "" if receipt in self.empty_documents else
                "공개 원문. 특정 이사 후보 이름이 없어도 회사·안건 맥락을 읽을 수 있다.",
                "source": "document_xml"}


def run(client, **kwargs):
    return asyncio.run(discover_guideline_context(client, "00000001", "20260909", **kwargs))


def test_candidate_name_is_not_needed_for_company_context_source():
    client = PublicClient({"D004": [filing("20260908000001", "공개매수신고서")]})
    result = run(client)
    assert result["selected_count"] == 1
    assert result["filings"][0]["purpose"] == "company_context"
    assert result["filings"][0]["text"]
    assert result["matches"][0]["family"] == "tender_offer"
    assert "decision" not in result["matches"][0]
    assert result["complete_history"] is False


def test_source_diversity_prevents_tender_amendments_consuming_budget():
    client = PublicClient({
        "D004": [filing(f"2026090800000{i}", "[기재정정]공개매수신고서") for i in range(1, 6)],
        "D001": [filing("20260907000010", "주식등의대량보유상황보고서(일반)")],
        "D003": [filing("20260906000011", "의결권대리행사권유참고서류")],
        "I001": [filing("20260905000012", "소송등의판결ㆍ결정(경영권분쟁소송)")],
    })
    result = run(client)
    assert len(client.documents) == 4
    assert {row["family"] for row in result["filings"]} == {
        "tender_offer", "ownership", "proxy_solicitation", "litigation"}
    tender = next(row for row in result["filings"] if row["family"] == "tender_offer")
    assert tender["rcept_no"] == "20260908000005"
    assert tender["is_correction"] is True and tender["correction_hint"]
    assert result["status"] == "partial"
    assert len(result["next_reads"]) == 4


def test_same_family_different_document_role_can_supply_other_view():
    client = PublicClient({"D004": [
        filing("20260908000001", "공개매수신고서"),
        filing("20260907000001", "공개매수에관한의견표명서"),
        filing("20260906000001", "[기재정정]공개매수신고서"),
    ]})
    result = run(client)
    assert result["selected_count"] == 2
    assert {row["document_role"] for row in result["filings"]} == {"신고서", "의견표명"}


def test_same_day_and_meeting_results_are_visible_followups_not_assessment_inputs():
    client = PublicClient({
        "D004": [filing("20260909000001", "공개매수결과보고서")],
        "I001": [filing("20260908000002", "임시주주총회결과")],
    })
    result = run(client)
    assert result["matched_count"] == 2 and result["selected_count"] == 0
    assert client.documents == []
    assert {row["use"] for row in result["next_reads"]} == {
        "chronology_verification", "post_meeting_followup"}
    assert all(not row["assessment_eligible"] for row in result["matches"])


def test_monitoring_can_read_prior_day_results_without_changing_default():
    client = PublicClient({"I001": [filing("20260908000002", "임시주주총회결과")]})
    result = run(client, include_meeting_results=True)
    assert result["selected_count"] == 1
    assert result["filings"][0]["family"] == "meeting_results"
    assert result["filings"][0]["followup_only"] is False
    assert result["temporal_scope"]["include_meeting_results"] is True
    assert result["temporal_scope"]["allow_same_day"] is False


def test_current_monitoring_can_read_same_day_without_claiming_intraday_order():
    client = PublicClient({
        "D004": [filing("20260909000001", "공개매수결과보고서")],
        "I001": [filing("20260909000002", "임시주주총회결과")],
    })
    result = run(client, allow_same_day=True, include_meeting_results=True)
    assert result["selected_count"] == 2
    assert all(row["same_day_unverified"] for row in result["filings"])
    assert result["temporal_scope"] == {
        "date_granularity": "day", "allow_same_day": True,
        "include_meeting_results": True, "intraday_order_verified": False}


def test_allowing_same_day_does_not_implicitly_allow_meeting_results():
    client = PublicClient({"I001": [filing("20260909000002", "임시주주총회결과")]})
    result = run(client, allow_same_day=True)
    assert result["selected_count"] == 0
    assert result["matches"][0]["followup_only"] is True


def test_monitoring_results_are_not_crowded_out_by_other_filing_families():
    client = PublicClient({
        "D004": [filing("20260908000001", "공개매수신고서")],
        "D001": [filing("20260908000002", "주식등의대량보유상황보고서")],
        "D003": [filing("20260908000003", "의결권대리행사권유참고서류")],
        "I001": [filing("20260908000004", "소송등의판결결정"),
                 filing("20260908000005", "임시주주총회결과")],
    })
    result = run(client, include_meeting_results=True)
    assert result["selected_count"] == 4
    assert result["filings"][0]["family"] == "meeting_results"


def test_future_and_out_of_window_rows_are_not_exposed_even_if_api_returns_them():
    client = PublicClient({"D004": [
        filing("20260910000001", "미래 공개매수신고서"),
        filing("20260911000001", "접수번호가 미래인 공개매수신고서", "20260908"),
        filing("20240101000001", "범위 밖 공개매수신고서"),
    ]})
    result = run(client)
    assert result["matches"] == [] and result["filings"] == []
    assert sum(row["discarded_invalid_or_future"] for row in result["scans"]) == 3


def test_existing_receipt_is_not_read_twice_and_duplicates_are_collapsed():
    repeated = filing("20260908000001", "자기주식취득결과보고서")
    client = PublicClient({"B001": [repeated], "E001": [repeated]})
    result = run(client, exclude=[repeated["rcept_no"]])
    assert result["matched_count"] == 1
    assert result["matches"][0]["already_available"] is True
    assert result["filings"] == [] and result["next_reads"] == []


def test_limited_scan_and_failure_preserve_peers_without_exposing_exception():
    client = PublicClient({"D004": [filing("20260908000001", "공개매수신고서")]},
                          fail=["D001"], total_pages=3)
    result = run(client)
    assert result["status"] == "partial" and result["selected_count"] == 1
    assert max(call["page_no"] for call in client.calls) == 2
    assert len(client.calls) <= 16
    assert any(row["truncated"] for row in result["scans"])
    assert any(row["status"] == "fetch_failed" for row in result["scans"])
    assert "SECRET_KEY" not in json.dumps(result)


def test_acquisition_failure_is_not_reported_as_no_event():
    rc = "20260908000001"
    client = PublicClient({"D004": [filing(rc, "공개매수신고서")]}, empty_documents=[rc])
    result = run(client)
    assert result["status"] == "partial"
    assert result["filings"][0]["status"] == "format_unsupported"
    assert result["matched_count"] == 1


def test_no_matches_never_claims_complete_history_or_clean_company():
    result = run(PublicClient())
    assert result["status"] == "searched"
    assert result["complete_history"] is False
    assert "0건은 사건이나 관계가 없다는 뜻이 아니다" in result["hint"]


def test_incremental_start_date_controls_index_window():
    client = PublicClient()
    result = run(client, start_date="20260907")
    assert result["start_date"] == "20260907"
    assert result["budget"]["lookback_days"] == 2
    assert all(call["bgn_de"] == "20260907" for call in client.calls)


@pytest.mark.parametrize("date_value", ["2026-09-09", "20260230", "", "20260910"])
def test_invalid_or_future_start_date_is_rejected(date_value):
    if not date_value:
        # Empty start_date intentionally selects the bounded default.
        assert run(PublicClient(), start_date=date_value)["budget"]["lookback_days"] == 365
    else:
        with pytest.raises(ValueError):
            run(PublicClient(), start_date=date_value)


def test_research_plan_routes_existing_tools_and_source_expansion():
    client = PublicClient({"D004": [
        filing("20260908000001", "공개매수신고서"),
        filing("20260907000001", "[기재정정]공개매수신고서"),
    ]})
    discovery = run(client)
    plan = build_guideline_research_plan("가비아", "20260909", discovery)
    actions = {row["tool"] + ":" + row["arguments"].get("scope", ""): row
               for row in plan["next_actions"]}
    assert "proxy_contest:timeline" in actions
    assert "proxy_contest:insiders" in actions
    assert "ownership_structure:blocks" in actions
    expansion = actions["proxy_advise_before_meeting:"]
    assert expansion["merge_with_current_request"] is True
    assert expansion["arguments"]["guideline_evidence_sources"] == [
        {"type": "dart", "rcept_no": "20260907000001"}]
    assert plan["server_calls_llm"] is False
    assert "스킵" in plan["stages"][-1]["instruction"]
    assert any("최신 지분" in item for item in plan["constraints"])


def test_failed_or_same_day_sources_are_not_automatically_requested_for_assessment():
    discovery = run(PublicClient({"I001": [filing("20260909000001", "임시주주총회결과")]}))
    plan = build_guideline_research_plan("가비아", "20260909", discovery)
    assert not any(row["purpose"] == "expand_assessment_packet" for row in plan["next_actions"])
    assert plan["followup_reads"]


class MeetingIndexClient:
    """Mock at list.json boundary, never an intermediate classification result."""

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    async def search_filings(self, **kwargs):
        self.calls.append(kwargs)
        channel = kwargs["pblntf_ty"], kwargs["pblntf_detail_ty"]
        payload = self.responses.get(channel, {"status": "013", "message": "조회된 데이타가 없습니다."})
        if isinstance(payload, Exception):
            raise payload
        return payload

    async def get_document_cached(self, receipt):
        pytest.fail("Index discovery must not download documents or consume a read budget")


def index_filing(receipt, title, **kwargs):
    return {"corp_code": "00000001", **filing(receipt, title), **kwargs}


def page_payload(rows, **metadata):
    return {"status": "000", "message": "정상", "page_no": 1, "page_count": 100,
            "total_count": len(rows), "total_page": 1, "list": rows, **metadata}


def discover_page(client, query=None, **kwargs):
    params = {"corp_code": "00000001", "as_of": "20260908", "meeting_type": "extraordinary",
              "meeting_date": "2026-09-09", "query": query or {"kind": "meeting_resolution"}}
    params.update(kwargs)
    return asyncio.run(discover_research_sources(client, **params))


def test_meeting_resolution_uses_exchange_channel_and_retains_amendments():
    rows = [index_filing("20260312000001", "주주총회소집결의"),
            index_filing("20260313000001", "[기재정정]주주총회소집결의"),
            index_filing("20260313000002", "주주총회소집공고"),
            index_filing("20260331000001", "정기주주총회결과")]
    client = MeetingIndexClient({("I", "I001"): page_payload(rows)})
    result = discover_page(client)
    assert len(client.calls) == 1
    assert client.calls[0]["pblntf_ty"] == "I"
    assert client.calls[0]["last_reprt_at"] == "N"
    assert [row["report_nm"] for row in result["candidates"]] == [
        "[기재정정]주주총회소집결의", "주주총회소집결의"]
    assert result["candidates"][0]["status"] == "unread"
    assert result["candidates"][0]["read_source"]["rcept_no"] == "20260313000001"
    assert result["candidates"][0]["document_url"] == "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260313000001"
    assert result["scans"][0]["discarded"]["not_title_match"] == 2
    assert result["no_contents_read"] is True


def test_periodic_discovery_searches_annual_half_and_quarter_without_latest_only():
    client = MeetingIndexClient({
        ("A", "A001"): page_payload([index_filing("20260316000929", "사업보고서 (2025.12)"),
                                     index_filing("20260813001726", "[기재정정]사업보고서 (2025.12)")]),
        ("A", "A002"): page_payload([index_filing("20260814003958", "반기보고서 (2026.06)")]),
        ("A", "A003"): page_payload([index_filing("20260515000001", "분기보고서 (2026.03)")]),
    })
    result = discover_page(client, {"kind": "periodic_reports"})
    assert {call["pblntf_detail_ty"] for call in client.calls} == {"A001", "A002", "A003"}
    assert all(call["pblntf_ty"] == "A" and call["last_reprt_at"] == "N" for call in client.calls)
    assert len(result["candidates"]) == 4
    assert all(row["classification_basis"] == "title_hint" for row in result["candidates"])
    assert result["complete_history"] is False


def test_annual_and_extraordinary_never_share_future_periodic_context():
    rows = [index_filing("20260316000929", "사업보고서 (2025.12)"),
            index_filing("20260813001726", "[기재정정]사업보고서 (2025.12)")]
    client = MeetingIndexClient({("A", "A001"): page_payload(rows)})
    annual = discover_page(client, {"kind": "periodic_reports"}, as_of="20260323",
                           meeting_type="annual", meeting_date="20260324")
    extraordinary = discover_page(client, {"kind": "periodic_reports"})
    assert len(annual["candidates"]) == 1
    assert len(extraordinary["candidates"]) == 2
    assert annual["scans"][0]["discarded"]["outside_window"] == 1


def test_row_company_and_publication_consistency_gate_precedes_title_exposure():
    rows = [index_filing("20260908000001", "주주총회소집결의"),
            index_filing("20260908000002", "WRONG_COMPANY 주주총회소집결의", corp_code="00000002"),
            index_filing("20260909000003", "FUTURE 주주총회소집결의"),
            index_filing("20260908000004", "DATE_CONFLICT 주주총회소집결의", rcept_dt="20260907"),
            index_filing("20260230000001", "INVALID_DATE 주주총회소집결의")]
    client = MeetingIndexClient({("I", "I001"): page_payload(rows)})
    result = discover_page(client)
    assert len(result["candidates"]) == 1
    assert result["scans"][0]["discarded"] == {
        "malformed": 1, "company_mismatch": 1, "date_conflict": 1,
        "outside_window": 1, "not_title_match": 0}
    assert not any(word in json.dumps(result) for word in ("WRONG_COMPANY", "FUTURE", "DATE_CONFLICT", "INVALID_DATE"))


def test_meeting_day_is_excluded_even_when_caller_supplies_later_end():
    client = MeetingIndexClient()
    result = discover_page(client, {"kind": "meeting_resolution", "end_date": "20261001"}, as_of="20260910")
    assert result["effective_end_date"] == "20260908"
    assert client.calls[0]["end_de"] == "20260908"


def test_requested_past_window_remains_narrow_and_empty_clipped_window_calls_nothing():
    client = MeetingIndexClient()
    discover_page(client, {"kind": "officer_changes", "start_date": "20250101", "end_date": "20251231"})
    assert all(call["bgn_de"] == "20250101" and call["end_de"] == "20251231" for call in client.calls)
    client = MeetingIndexClient()
    result = discover_page(client, {"kind": "meeting_resolution", "start_date": "20260909"})
    assert client.calls == []
    assert result["scans"][0]["status"] == "empty_effective_window"


def test_page_coverage_and_cursor_do_not_claim_complete_search():
    client = MeetingIndexClient({("A", "A001"): page_payload(
        [index_filing("20260901000001", "사업보고서")], total_count="31", total_page="4")})
    result = discover_page(client, {"kind": "periodic_reports", "page": 2, "page_count": 10})
    assert len(client.calls) == 3
    assert all(call["page_no"] == 2 and call["page_count"] == 10 for call in client.calls)
    assert result["scans"][0]["pages_read"] == [2]
    assert result["scans"][0]["has_more"] is True
    assert result["next_queries"][0]["page"] == 3
    assert result["status"] == "partial" and result["complete_history"] is False
    capped = discover_page(client, {"kind": "periodic_reports", "page": 20, "page_count": 1})
    # This fixture reports four pages: asking page 20 still does not establish
    # that pages 1..19 have been searched or that history is complete.
    assert capped["next_queries"] == [] and capped["complete_history"] is False


def test_page_limit_and_unknown_pagination_are_explicit():
    client = MeetingIndexClient({("I", "I001"): page_payload([], total_count=2001, total_page=21)})
    result = discover_page(client, {"kind": "meeting_resolution", "page": 20})
    assert result["page_limit_reached"] is True
    assert result["next_queries"] == []
    client = MeetingIndexClient({("I", "I001"): {"status": "000", "list": []}})
    result = discover_page(client)
    assert result["status"] == "partial" and result["scans"][0]["has_more"] is None


def test_historical_officer_titles_and_ceo_changes_are_routing_hints():
    client = MeetingIndexClient({
        ("E", "E005"): page_payload([
            index_filing("20260901000001", "독립이사의선임ㆍ해임또는중도퇴임에관한신고"),
            index_filing("20250901000001", "사외이사의선임ㆍ해임또는중도퇴임에관한신고"),
            index_filing("20250901000002", "감사위원 선임 신고")]),
        ("I", "I001"): page_payload([index_filing("20260901000003", "대표이사변경")]),
    })
    result = discover_page(client, {"kind": "officer_changes"})
    assert len(result["candidates"]) == 4
    assert all("decision" not in row for row in result["candidates"])


def test_dispute_discovery_preserves_tender_outcome_but_never_suggests_meeting_outcome():
    repeated = index_filing("20260901000001", "소송등의판결ㆍ결정(경영권분쟁소송)")
    client = MeetingIndexClient({
        ("B", "B001"): page_payload([repeated]),
        ("I", ""): page_payload([repeated, index_filing("20260901000002", "임시주주총회결과")]),
        ("D", ""): page_payload([index_filing("20260901000003", "공개매수결과보고서"),
                                  index_filing("20260901000004", "주식등의대량보유상황보고서(일반)")]),
    })
    result = discover_page(client, {"kind": "ownership_disputes"})
    assert len(result["candidates"]) == 3
    assert len(next(row for row in result["candidates"] if row["rcept_no"] == repeated["rcept_no"])["discovered_in"]) == 2
    assert any(row["report_nm"] == "공개매수결과보고서" for row in result["candidates"])
    assert all("주주총회결과" not in row["report_nm"] for row in result["candidates"])


@pytest.mark.parametrize("failure", [
    RuntimeError("SECRET_REQUEST_MUST_NOT_ESCAPE"),
    {"status": "020", "message": "SECRET_REQUEST_MUST_NOT_ESCAPE"},
    {"status": "000", "total_count": "SECRET_REQUEST_MUST_NOT_ESCAPE", "list": []},
])
def test_failure_messages_and_malformed_metadata_are_not_published(failure):
    client = MeetingIndexClient({("A", "A001"): failure,
                                 ("A", "A002"): page_payload([index_filing("20260814000001", "반기보고서")])})
    result = discover_page(client, {"kind": "periodic_reports"})
    assert result["status"] == "partial"
    assert result["scans"][0]["status"] == "fetch_failed"
    assert len(result["candidates"]) == 1
    assert "SECRET_REQUEST" not in json.dumps(result)


@pytest.mark.parametrize("query", [
    {"kind": "unknown"}, {"kind": "meeting_resolution", "page": 0},
    {"kind": "meeting_resolution", "page": 21}, {"kind": "meeting_resolution", "page": True},
    {"kind": "meeting_resolution", "page_count": 101}, {"kind": "meeting_resolution", "page_count": 0},
    {"kind": "meeting_resolution", "start_date": "2026-01-01"},
    {"kind": "meeting_resolution", "end_date": "20260230"},
    {"kind": "meeting_resolution", "start_date": "20260102", "end_date": "20260101"},
    {"kind": "meeting_resolution", "unbounded": True},
])
def test_research_queries_reject_invalid_unbounded_and_coerced_input(query):
    with pytest.raises(ValueError):
        ResearchQuery.model_validate(query)


def test_meeting_plans_separate_attendance_from_latest_context_and_never_mandate_docs():
    annual = build_meeting_research_plan("고려아연", "20260323", "annual", "20260324", "20260305001616")
    extraordinary = build_meeting_research_plan("고려아연", "20260908", "extraordinary", "20260909", "20260811000705")
    assert annual["temporal_questions"]["attendance"] == extraordinary["temporal_questions"]["attendance"]
    assert annual["temporal_questions"]["current_context"]["through"] == "20260323"
    assert extraordinary["temporal_questions"]["current_context"]["through"] == "20260908"
    assert "반기·분기" in extraordinary["temporal_questions"]["meeting_specific"]
    assert extraordinary["required_documents"] == []
    assert extraordinary["missing_document_blocks_flow"] is False
    assert {row["state"] for row in extraordinary["information_states"]} == {
        "not_disclosed", "unread", "meaning_unresolved", "conflicting_sources"}
    assert "0건" in extraordinary["information_states"][0]["meaning"]


@pytest.mark.parametrize("overrides", [
    {"corp_code": "1"}, {"meeting_type": "all"}, {"meeting_date": "20260230"},
    {"as_of": "2026-09-08"},
])
def test_invalid_meeting_binding_makes_no_index_calls(overrides):
    client = MeetingIndexClient()
    with pytest.raises(ValueError):
        discover_page(client, **overrides)
    assert client.calls == []


def test_future_notice_cannot_seed_historical_meeting_research():
    with pytest.raises(ValueError):
        build_meeting_research_plan("고려아연", "20260323", "annual", "20260324", "20260811000705")


def test_meeting_plan_suggests_valid_runner_discovery_actions():
    from open_proxy_mcp.harness.runner import _ACTIONS

    plan = build_meeting_research_plan("고려아연", "20260908", "extraordinary", "20260909", "20260811000705")
    for question in plan["questions"]:
        action = question["suggested_action"]
        assert action["action"] == "discover_sources"
        assert ResearchQuery.model_validate(action["query"]).kind == question["kind"]
        # Exercise the actual model-action adapter, not a second schema copy.
        assert _ACTIONS.validate_python(action).action == "discover_sources"
    not_disclosed = plan["information_states"][0]
    assert "미탐색·접근실패·미독해" in not_disclosed["action"]


def test_charter_history_reads_prior_meeting_results_and_periodic_checkpoints_only_before_cutoff():
    client = MeetingIndexClient({
        ('A', 'A001'): page_payload([index_filing('20260318000001', '사업보고서 (2025.12)')]),
        ('A', 'A002'): page_payload([index_filing('20260814000001', '반기보고서 (2026.06)')]),
        ('E', 'E006'): page_payload([index_filing('20260610000001', '주주총회소집공고')]),
        ('I', 'I001'): page_payload([
            index_filing('20260630000001', '임시주주총회결과'),
            index_filing('20260702000001', '[기재정정]임시주주총회결과'),
            index_filing('20260909000001', 'FUTURE 임시주주총회결과')]),
    })
    result = discover_page(client, {'kind': 'charter_history'})
    assert len(client.calls) == 5
    from open_proxy_mcp.harness.runner import SourceRead
    for row in result['candidates']:
        SourceRead.model_validate(row['read_source']).request()
        if row.get('attachment_request'):
            SourceRead.model_validate(row['attachment_request']).request()
    assert len(result['candidates']) == 5
    assert 'FUTURE' not in json.dumps(result)
    assert result['complete_history'] is False and result['no_contents_read'] is True
    rows = {r['rcept_no']: r for r in result['candidates']}
    assert rows['20260318000001']['charter_role_hint'] == 'periodic_checkpoint'
    assert rows['20260318000001']['attachment_request']['type'] == 'dart_attachments'
    assert rows['20260630000001']['charter_role_hint'] == 'resolution_candidate'
    assert rows['20260702000001']['is_correction'] is True
    assert result['charter_workflow']['missing_document_blocks_flow'] is False
    assert result['charter_workflow']['current_charter_verified'] is False


def test_charter_research_plan_is_available_without_mandatory_documents():
    plan = build_meeting_research_plan('fixture', '20260908', 'extraordinary', '20260909', '20260811000705')
    assert any(q['kind'] == 'charter_history' for q in plan['questions'])
