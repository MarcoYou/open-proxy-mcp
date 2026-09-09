"""Synthetic source-bound governance contracts, not LLM accuracy benchmarks."""
import asyncio
from copy import deepcopy

import pytest
from pydantic import ValidationError

from open_proxy_mcp.services import governance_screen as screen
from open_proxy_mcp.services.company import CompanyResolution
from open_proxy_mcp.services.contracts import AnalysisStatus
from open_proxy_mcp.tools.governance_screen import render_governance_screen

SOURCE = "회사는 공개매수를 제안하였으며 아직 결제되지 않았다. 거래 조건은 모든 주주에게 동일하게 공시되었다."
RC = "20260907000001"


def task_and_assessment(disposition="no_adverse_signal", materiality="moderate"):
    discovery = {"status": "partial", "as_of": "20260909", "scans": [],
        "matches": [{"rcept_no": RC, "published": "20260907", "family": "tender_offer"}],
        "complete_history": False, "next_reads": [], "budget": {},
        "filings": [{"rcept_no": RC, "published": "20260907", "status": "read", "text": SOURCE,
                     "source_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={RC}"}]}
    task = screen.build_governance_task({"corp_code": "00000001", "corp_name": "합성회사"}, "20260909", discovery)
    finding = {"finding_id": "equal_terms", "category": "minority_shareholder_treatment",
        "disposition": disposition, "materiality": materiality, "actor": "공개매수자", "event": "제안된 거래",
        "observation": "모든 주주에게 동일한 조건을 공시했다는 내용", "fact_status": "disclosed_fact",
        "procedural_state": "not_applicable", "impact_state": "observed", "event_date": "2026-09-07",
        "relevance": "합성회사 주주의 거래 조건", "risk_basis": "substantive_conduct" if disposition == "supported_risk" else "not_applicable",
        "rationale": "합성 인용에 대한 계약 동작 검증이며 실제 투자 판단이 아니다.",
        "evidence_refs": [{"source_id": task["sources"][0]["source_id"], "quote": SOURCE}], "counterevidence": [], "gaps": []}
    assessment = {"task_id": task["task_id"], "evaluator": "synthetic-contract-test",
                  "findings": [finding], "skipped_checks": [], "summary": "합성 입력의 제출·수용 계약을 검증했다."}
    return task, assessment, discovery


def accept(task, assessment):
    return screen.accept_governance_assessment(task, screen.GovernanceAssessment.model_validate(assessment))


def test_tender_presence_does_not_create_a_negative_triage():
    task, assessment, _ = task_and_assessment()
    result = accept(task, assessment)
    triage = screen.governance_triage(result)
    assert triage["disposition"] == "no_adverse_signal_in_reviewed_scope"
    assert triage["ballot_action"] == "none"
    assert result["scope_complete"] is False
    assert len(result["implicit_skipped_checks"]) == 4
    assert result["human_reviewed"] is False


@pytest.mark.parametrize("mutation", ["presence", "claim", "application_as_ruling", "contract_votes", "conditional_completed", "human_reviewed"])
def test_schema_rejects_promoting_events_claims_or_plans_to_established_risk(mutation):
    _, assessment, _ = task_and_assessment("supported_risk")
    finding = assessment["findings"][0]
    if mutation == "presence":
        finding["risk_basis"] = "event_presence_only"
    elif mutation == "claim":
        finding["fact_status"] = "party_claim"
    elif mutation == "application_as_ruling":
        finding.update(fact_status="court_ruling", procedural_state="filed")
    elif mutation == "contract_votes":
        finding.update(ownership_basis="contractual", voting_rights="confirmed")
    elif mutation == "conditional_completed":
        finding.update(fact_status="conditional_plan", impact_state="observed")
    else:
        assessment["human_reviewed"] = True
    with pytest.raises(ValidationError):
        screen.GovernanceAssessment.model_validate(assessment)


@pytest.mark.parametrize("change", ["source_text", "unread_source_text", "threshold", "cutoff", "coverage"])
def test_current_task_identity_changes_with_evidence_or_assessment_scope(change):
    task, assessment, discovery = task_and_assessment()
    if change == "source_text":
        discovery["filings"][0]["text"] += " 새로운 공시 조건이 존재한다."
    elif change == "unread_source_text":
        discovery["filings"][0]["text"] += "문맥 " * 6000
        task = screen.build_governance_task(task["company"], "20260909", discovery)
        assessment["task_id"] = task["task_id"]
        discovery["filings"][0]["text"] += " 마지막 문장 변경"
    elif change == "coverage":
        discovery["matches"].append({"rcept_no": "20260908000002", "published": "20260908"})
    new_task = screen.build_governance_task(task["company"], "20260908" if change == "cutoff" else "20260909",
                                           discovery, review_materiality="high" if change == "threshold" else "moderate")
    assert new_task["task_id"] != task["task_id"]
    assert accept(new_task, assessment)["status"] == "rejected"


def test_literal_quote_cannot_bridge_disjoint_source_excerpts():
    task, assessment, _ = task_and_assessment()
    task["sources"][0]["excerpts"] = ["첫 번째 인용 가능한 문장", "두 번째 인용 가능한 문장"]
    assessment["findings"][0]["evidence_refs"][0]["quote"] = "첫 번째 인용 가능한 문장\n두 번째 인용 가능한 문장"
    result = accept(task, assessment)
    assert result["status"] == "rejected"
    assert "citation_not_in_current_source_excerpt" in result["errors"]


@pytest.mark.parametrize("mutation", ["invented_quote", "foreign_source", "future_event"])
def test_source_and_time_claims_are_bound_to_current_task(mutation):
    task, assessment, _ = task_and_assessment()
    finding = assessment["findings"][0]
    if mutation == "invented_quote":
        finding["evidence_refs"][0]["quote"] = "원문에 존재하지 않는 열두 글자 이상의 주장"
    elif mutation == "foreign_source":
        finding["evidence_refs"][0]["source_id"] = "filing:20260901000999"
    else:
        finding["event_date"] = "2026-10-01"
    assert accept(task, assessment)["status"] == "rejected"


def test_missing_check_continues_but_cannot_erase_known_supported_risk():
    task, assessment, _ = task_and_assessment("supported_risk", "high")
    assessment["skipped_checks"] = [{"category": "board_accountability", "kind": "missing_information",
        "question": "이사회 검토 세부 자료 미확인", "next_action": "관련 의사록 공개 여부를 별도 확인"}]
    accepted = accept(task, assessment)
    triage = screen.governance_triage(accepted)
    assert triage["disposition"] == "priority_review"
    assert triage["skipped_check_count"] == 4


def test_threshold_changes_attention_without_removing_the_risk():
    task, assessment, _ = task_and_assessment("supported_risk", "low")
    accepted = accept(task, assessment)
    assert screen.governance_triage(accepted, "moderate")["disposition"] == "monitor"
    result = screen.governance_triage(accepted, "low")
    assert result["disposition"] == "review"
    assert result["supported_risk_ids"] == ["equal_terms"]


def test_missing_only_assessment_is_not_a_clean_result():
    task, assessment, _ = task_and_assessment()
    assessment["findings"] = []
    result = screen.governance_triage(accept(task, assessment))
    assert result["disposition"] == "not_assessed"


@pytest.mark.parametrize("kind,expected", [("conflicting_evidence", "needs_evidence"),
                                            ("identity_uncertain", "needs_evidence"),
                                            ("missing_information", "no_adverse_signal_in_reviewed_scope")])
def test_material_conflicts_are_visible_without_promoting_ordinary_missing_fields(kind, expected):
    task, assessment, _ = task_and_assessment()
    uncertain = deepcopy(assessment["findings"][0])
    uncertain.update(finding_id="uncertain", category="related_party_conflict", disposition="unresolved", materiality="high")
    uncertain["gaps"] = [{"category": "related_party_conflict", "kind": kind,
                          "question": "거래 상대방 동일성에 대한 확인 범위", "next_action": "관련 원문 대조"}]
    assessment["findings"].append(uncertain)
    result = screen.governance_triage(accept(task, assessment))
    assert result["disposition"] == expected
    assert result["supported_risk_ids"] == []


def test_partial_discovery_preserves_the_callers_known_receipts():
    delta = screen.disclosure_delta({"matches": [], "as_of": "20260909"}, "", [RC])
    assert delta["checkpoint"]["receipt_ids"] == [RC]


@pytest.mark.parametrize("kind", ["conflicting_evidence", "identity_uncertain"])
def test_no_adverse_label_cannot_conceal_its_material_conflicting_gap(kind):
    task, assessment, _ = task_and_assessment("no_adverse_signal", "high")
    assessment["findings"][0]["gaps"] = [{"category": "minority_shareholder_treatment", "kind": kind,
        "question": "동일 조건 적용 주체에 충돌하는 자료가 남음", "next_action": "대상 주체·계약 원문 대조"}]
    result = screen.governance_triage(accept(task, assessment))
    assert result["disposition"] == "needs_evidence"
    assert result["supported_risk_ids"] == []
    assert result["context_followup_finding_ids"] == ["equal_terms"]


def test_invalid_peer_and_duplicate_task_are_isolated():
    _, assessment, _ = task_and_assessment()
    peer = {**deepcopy(assessment), "task_id": "different-task"}
    prepared, errors = screen._prepare_assessments([assessment, {"human_reviewed": True}, peer, assessment])
    assert list(prepared) == ["different-task"]
    assert {e["reason"] for e in errors} == {"invalid_assessment_schema", "duplicate_task_id"}


def test_delta_returns_new_correction_receipts_without_scheduling_or_changing_sources():
    _, _, discovery = task_and_assessment()
    discovery["matches"] += [
        {"rcept_no": "20260908000001", "published": "20260908", "is_correction": True},
        {"rcept_no": "20260909000001", "published": "20260909"}]
    delta = screen.disclosure_delta(discovery, "20260908", ["20260909000001"])
    assert [row["rcept_no"] for row in delta["items"]] == ["20260908000001"]
    assert delta["items"][0]["is_correction"] is True
    assert delta["complete"] is False
    assert delta["monitor_created"] is False


class FakeClient:
    """Fake public DART boundary; discovery and source packet code remain real."""
    def __init__(self):
        self.calls = []

    async def search_filings(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs["pblntf_detail_ty"] != "D004":
            return {"status": "013", "list": []}
        return {"status": "000", "total_page": 1, "total_count": 1, "list": [
            {"rcept_no": RC, "rcept_dt": "20260907", "report_nm": "공개매수신고서", "flr_nm": "합성매수자"}]}

    async def get_document_cached(self, receipt):
        return {"text": SOURCE, "source": "document_xml"}


def install_client(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(screen, "get_dart_client", lambda: client)

    async def resolve(query):
        if query == "실패":
            raise RuntimeError("private upstream token must not appear")
        if query == "모호":
            return CompanyResolution(AnalysisStatus.AMBIGUOUS, query, None, [{"corp_name": "후보"}])
        if query == "이전사명":
            return CompanyResolution(AnalysisStatus.ERROR, query, None, [])
        return CompanyResolution(AnalysisStatus.EXACT, query,
                                 {"corp_code": "00000001", "corp_name": query, "stock_code": "000001"}, [])
    monkeypatch.setattr(screen, "resolve_company_query", resolve)
    return client


def test_actual_discovery_pipeline_and_batch_peer_failure_isolation(monkeypatch):
    client = install_client(monkeypatch)
    payload = asyncio.run(screen.build_governance_screen_payload(["합성회사", "실패", "모호"], as_of="20260909"))
    assert [row["status"] for row in payload["companies"]] == ["assessment_pending", "company_failed", "company_unresolved"]
    assert payload["companies"][0]["assessment_task"]["sources"][0]["document_sha256"]
    assert len(client.calls) == 8
    assert all(c["corp_code"] == "00000001" and c["end_de"] == "20260909" for c in client.calls)
    assert "private upstream" not in str(payload)


def test_unresolved_company_hints_preserve_candidates_and_renames_in_both_formats(monkeypatch):
    from open_proxy_mcp.dart.client import DartClient
    install_client(monkeypatch)
    monkeypatch.setattr(DartClient, "lookup_former_name", lambda query: {
        "current_name": "변경된사명", "stock_code": "000002"} if query == "이전사명" else None)
    payload = asyncio.run(screen.build_governance_screen_payload(
        ["합성회사", "모호", "이전사명"], as_of="20260909"))
    normal, ambiguous, renamed = payload["companies"]
    assert normal["status"] == "assessment_pending"
    assert ambiguous["candidates"] == [{"corp_name": "후보", "corp_code": "", "stock_code": ""}]
    assert "후보" in ambiguous["warnings"][0] and "자동 선택하지 않았다" in ambiguous["warnings"][0]
    assert "변경된사명" in renamed["warnings"][0] and "000002" in renamed["warnings"][0]
    assert ambiguous["next_action"] and renamed["next_action"]
    rendered = render_governance_screen(payload)
    assert ambiguous["warnings"][0] in rendered and renamed["warnings"][0] in rendered


def test_service_two_stages_bind_sources_but_delta_does_not_change_task(monkeypatch):
    install_client(monkeypatch)
    first = asyncio.run(screen.build_governance_screen_payload(["합성회사"], as_of="20260909"))
    task = first["companies"][0]["assessment_task"]
    _, assessment, _ = task_and_assessment()
    assessment["task_id"] = task["task_id"]
    assessment["findings"][0]["counterevidence"] = deepcopy(assessment["findings"][0]["evidence_refs"])
    second = asyncio.run(screen.build_governance_screen_payload(["합성회사"], as_of="20260909",
        since="20260908", known_receipts=[RC], governance_assessments=[assessment, {"human_reviewed": True}]))
    assert second["companies"][0]["status"] == "assessed"
    assert second["companies"][0]["delta"]["items"] == []
    assert second["companies"][0]["assessment_task"]["task_id"] == task["task_id"]
    assert second["submission_errors"] == [{"index": 1, "reason": "invalid_assessment_schema"}]
    rendered = render_governance_screen(second)
    assert "사람 미검토" in rendered and "평가에서 건너뛴 항목" in rendered
    assert "반대 근거·보완 공시" in rendered and "근거 성격: 공시된 사실" in rendered
    assert '"receipt_ids"' in rendered and RC in rendered


@pytest.mark.parametrize("kwargs", [
    {"companies": []}, {"companies": ["회사"] * 31}, {"companies": ["회사", "회사"]},
    {"companies": ["회사"], "as_of": "20260230"},
    {"companies": ["회사"], "as_of": "20260909", "since": "20260910"},
    {"companies": ["회사"], "known_receipts": ["https://untrusted.example"]},
])
def test_invalid_batch_and_dates_fail_before_any_acquisition(kwargs):
    with pytest.raises(ValueError):
        asyncio.run(screen.build_governance_screen_payload(**kwargs))


def test_weak_company_match_warning_survives_batch_and_markdown(monkeypatch):
    from open_proxy_mcp.services import contracts
    install_client(monkeypatch)
    warning = "입력 이름과 선택한 회사가 정확히 일치하지 않습니다."
    monkeypatch.setattr(contracts, "_weak_resolution_warnings", lambda: [warning])
    payload = asyncio.run(screen.build_governance_screen_payload(["합성회사", "실패"], as_of="20260909"))
    assert warning in payload["warnings"]
    assert warning in render_governance_screen(payload)
    assert payload["companies"][0]["status"] == "assessment_pending"
