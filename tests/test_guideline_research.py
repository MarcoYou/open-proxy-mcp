"""Public-source discovery contracts; synthetic filings do not measure LLM accuracy."""
import asyncio
import json

import pytest

from open_proxy_mcp.services.guideline_research import (
    build_guideline_research_plan, discover_guideline_context,
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
