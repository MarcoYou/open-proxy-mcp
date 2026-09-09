"""Absence, conflict, invalid input, and direct-reading boundaries.

Synthetic source packets exercise the workflow contract, not LLM accuracy.
"""
from copy import deepcopy
import asyncio

import pytest

from open_proxy_mcp.services.guideline_evidence import collect_guideline_evidence


def test_readable_raw_is_returned_when_extraction_format_is_unavailable():
    raw = "사업연도 2025년 01월 01일 부터 2025년 12월 31일 까지. 이사회에 관한 사항 홍길동 참석 기록 원문"
    class Client:
        async def get_document_cached(self, receipt):
            return {"text": raw}
    result = asyncio.run(collect_guideline_evidence(Client(), {"rcept_no": "20260301000002"}, "20260302"))
    assert result["document_read"] is True
    assert "홍길동 참석 기록 원문" in result["raw_text"]
    assert result["status"] == "format_unsupported"

from open_proxy_mcp.services.guideline_assessment import (
    GuidelineAssessment, accept_assessment, apply_missing_information_policy,
    assessment_metrics, build_assessment_task, pilot_recommendation,
    prepare_assessment_batch,
)
from open_proxy_mcp.services.guideline_policy import evaluate_guideline_policy, load_pilot_guideline_policy


def case(*, period_resolved=True, all_present=False, policy=None):
    notice = "홍길동은 해당 회사 사외이사 재선임 후보이며 공개된 관계에서 구체적 독립성 우려는 확인되지 않는다."
    annual = ("사업보고서의 보고 사업연도는 2025-01-01에서 2025-12-31이다. "
              "홍길동은 그 기간 전체 재직했다. 이사회는 전체 3회로 1월 1일 참석, "
              f"2월 1일 {'참석' if all_present else '불참'}, 3월 1일 참석했다.")
    policy = policy if policy is not None else load_pilot_guideline_policy()
    task = build_assessment_task(
        candidate={"name": "홍길동", "birth_date": "1970.01.01", "role_type": "사외이사"},
        corp_code="00000001", agenda_title="홍길동 선임", notice_rcept="20260301000001",
        notice_text=notice, as_of="20260302", policy=policy,
        attendance={"filing": {"rcept_no": "20260301000002"}, "raw_text": annual,
                    "attendance_period": ({"status": "resolved", "start": "2025-01-01", "end": "2025-12-31"}
                                          if period_resolved else {"status": "unresolved"})})
    notice_refs = [{"source_id": "notice:20260301000001", "quote": notice}]
    annual_refs = [{"source_id": "annual:20260301000002", "quote": annual}]
    def judgment(value, refs):
        return {"value": value, "rationale": "합성 원문에 따른 계약 경계 검증",
                "evidence_refs": deepcopy(refs), "counterevidence": [], "unresolved": []}
    data = {"task_id": task["task_id"], "evaluator": "synthetic-contract-test",
            "appointment": judgment("renewed", notice_refs),
            "independence": judgment("no_public_concern", notice_refs),
            "attendance": {**judgment("known", annual_refs),
                "period_start": "2025-01-01", "period_end": "2025-12-31",
                "all_board_meetings_covered": True,
                "service_intervals": [{"start": "2025-01-01", "end": "2025-12-31",
                    "rationale": "원문에 연간 재직 명시", "evidence_refs": deepcopy(annual_refs)}],
                "legal_suspension_intervals": [],
                "meetings": [{"meeting_id": f"2025-0{i}-01:{i}", "date": f"2025-0{i}-01",
                              "attendance": attended, "evidence_refs": deepcopy(annual_refs)}
                             for i, attended in enumerate(["present", "present" if all_present else "absent", "present"], 1)],
                "exception": judgment("rejected", annual_refs)}}
    return task, data


def unknown(judgment, kind="missing_information"):
    judgment.update(value="unknown", unresolved_kind=kind,
                    unresolved=["해당 항목의 자료를 검토했으나 확인하지 못함"],
                    evidence_refs=[], counterevidence=[])


def evaluate(task, data, *, policy=None):
    accepted = accept_assessment(task, GuidelineAssessment.model_validate(data))
    metrics = assessment_metrics(accepted)
    policy = policy if policy is not None else load_pilot_guideline_policy()
    trace = apply_missing_information_policy(
        evaluate_guideline_policy(policy, metrics), accepted, policy=policy)
    return accepted, metrics, trace, pilot_recommendation(trace)


def test_explicit_missing_attendance_skips_check_without_inventing_rate():
    task, data = case()
    unknown(data["attendance"])
    unknown(data["attendance"]["exception"])
    accepted, metrics, trace, decision = evaluate(task, data)
    assert accepted["status"] == "accepted_unreviewed"
    assert metrics["attendance_pct"] is None
    assert decision == "FOR"
    assert any(item["check"] == "attendance" for item in trace["skipped_checks"])
    assert trace["support_basis"] == "available_evidence_with_explicit_skips"


def test_skipped_attendance_does_not_require_an_absence_exception_assessment():
    task, data = case()
    unknown(data["attendance"])
    unknown(data["attendance"]["exception"], "not_assessed")
    _, metrics, trace, decision = evaluate(task, data)
    assert metrics["attendance_pct"] is None
    assert decision == "FOR"
    assert trace["unresolved"] == []


def test_confirmed_opposition_survives_unrelated_missing_checks():
    task, data = case()
    data["independence"]["value"] = "concern"
    unknown(data["appointment"])
    unknown(data["attendance"])
    unknown(data["attendance"]["exception"])
    accepted, metrics, trace, decision = evaluate(task, data)
    assert accepted["status"] == "accepted_unreviewed"
    assert metrics["independence_concern_accepted"] is True
    assert decision == "AGAINST"
    assert {"rule_id": "PILOT-IND", "effect": "oppose"} in trace["fired_effects"]


def test_missing_exception_does_not_excuse_confirmed_low_attendance():
    task, data = case()
    unknown(data["attendance"]["exception"])
    _, metrics, trace, decision = evaluate(task, data)
    assert metrics["attendance_pct"] == pytest.approx(200 / 3)
    assert metrics["attendance_exception_accepted"] is None
    assert decision == "AGAINST"
    assert {"rule_id": "PILOT-ATT", "effect": "oppose"} in trace["fired_effects"]


@pytest.mark.parametrize("exception_kind", [None, "missing_information", "conflicting_evidence", "not_assessed"])
def test_missing_appointment_retains_observed_low_attendance_for_applicability_review(exception_kind):
    task, data = case()
    unknown(data["appointment"])
    if exception_kind:
        unknown(data["attendance"]["exception"], exception_kind)
    accepted, metrics, trace, decision = evaluate(task, data)
    assert accepted["status"] == "accepted_unreviewed"
    assert metrics["attendance_pct"] == pytest.approx(200 / 3)
    assert metrics["is_reelection"] is None
    assert decision == "REVIEW"
    attendance_rule = next(r for r in trace["rule_results"] if r["rule_id"] == "PILOT-ATT")
    assert attendance_rule["state"] == "unresolved"
    assert "선임구분이 미확정" in attendance_rule["note"]
    assert not trace["fired_effects"]
    assert any(row["rule_id"] == "PILOT-ATT" for row in trace["unresolved"])


@pytest.mark.parametrize(("threshold", "decision"), [(50, "FOR"), (75, "REVIEW"), (100, "REVIEW")])
def test_missing_appointment_attendance_review_respects_selected_threshold(threshold, decision):
    from open_proxy_mcp.services.guideline_workflow import apply_workflow_policy
    policy = apply_workflow_policy(load_pilot_guideline_policy(), {"attendance_min_pct": threshold})
    task, data = case(policy=policy)
    unknown(data["appointment"])
    _, metrics, trace, actual = evaluate(task, data, policy=policy)
    assert metrics["attendance_pct"] == pytest.approx(200 / 3)
    assert actual == decision
    assert not trace["fired_effects"]


@pytest.mark.parametrize("appointment_missing", [False, True])
def test_full_attendance_is_not_held_by_missing_appointment_or_exception(appointment_missing):
    task, data = case(all_present=True)
    if appointment_missing:
        unknown(data["appointment"])
    unknown(data["attendance"]["exception"])
    _, metrics, trace, decision = evaluate(task, data)
    assert metrics["attendance_pct"] == 100
    assert decision == "FOR"
    assert not trace["unresolved"]


def test_accepted_attendance_exception_does_not_require_missing_appointment_classification():
    task, data = case()
    unknown(data["appointment"])
    data["attendance"]["exception"]["value"] = "accepted"
    _, metrics, trace, decision = evaluate(task, data)
    assert metrics["attendance_exception_accepted"] is True
    assert decision == "FOR"
    assert not trace["unresolved"]


@pytest.mark.parametrize("kind", ["conflicting_evidence", "identity_uncertain"])
def test_conflicts_cannot_be_skipped_like_missing_information(kind):
    task, data = case()
    unknown(data["attendance"])
    unknown(data["attendance"]["exception"])
    unknown(data["independence"], kind)
    _, _, trace, decision = evaluate(task, data)
    assert decision == "REVIEW"
    assert "independence" in trace["material_conflicts"]
    assert "independence" not in {item["check"] for item in trace["skipped_checks"]}


def test_missing_label_with_existing_counterevidence_is_rejected():
    task, data = case()
    refs = deepcopy(data["independence"]["evidence_refs"])
    unknown(data["independence"])
    data["independence"]["counterevidence"] = refs
    assert evaluate(task, data)[0]["status"] == "rejected"


def test_direct_source_reading_resolves_period_when_regex_did_not():
    task, data = case(period_resolved=False)
    data["attendance"].update(period_basis="llm_reading",
                              period_evidence_refs=deepcopy(data["attendance"]["evidence_refs"]))
    accepted, metrics, _, decision = evaluate(task, data)
    assert accepted["status"] == "accepted_unreviewed"
    assert accepted["attendance_calculation"]["period_basis"] == "llm_reading"
    assert metrics["attendance_pct"] == pytest.approx(200 / 3)
    assert decision == "AGAINST"


@pytest.mark.parametrize("mutation", ["future", "invalid_date", "no_citation", "wrong_source", "invented_quote"])
def test_direct_reading_still_rejects_invalid_time_or_source_contract(mutation):
    task, data = case(period_resolved=False)
    attendance = data["attendance"]
    attendance.update(period_basis="llm_reading", period_evidence_refs=deepcopy(attendance["evidence_refs"]))
    if mutation == "future":
        attendance["period_end"] = "2026-03-02"
    elif mutation == "invalid_date":
        attendance["period_start"] = "2025-02-30"
    elif mutation == "no_citation":
        attendance["period_evidence_refs"] = []
    elif mutation == "wrong_source":
        attendance["period_evidence_refs"] = deepcopy(data["appointment"]["evidence_refs"])
    else:
        attendance["period_evidence_refs"][0]["quote"] = "사업보고서 원문에는 없는 임의의 사업연도 설명입니다."
    accepted, metrics, _, _ = evaluate(task, data)
    assert accepted["status"] == "rejected"
    assert metrics["attendance_pct"] is None


def test_one_invalid_submission_does_not_abort_valid_peer():
    task, data = case()
    invalid = {**deepcopy(data), "human_reviewed": True}
    valid, errors = prepare_assessment_batch([invalid, data])
    assert list(valid) == [task["task_id"]]
    assert errors == [{"index": 0, "reason": "invalid_assessment_schema"}]
    assert accept_assessment(task, valid[task["task_id"]])["status"] == "accepted_unreviewed"


def test_duplicate_submissions_invalidate_only_their_own_task():
    _, data = case()
    peer = {**deepcopy(data), "task_id": "separate-valid-task"}
    valid, errors = prepare_assessment_batch([data, peer, deepcopy(data)])
    assert list(valid) == [peer["task_id"]]
    assert errors == [{"index": 2, "reason": "duplicate_task_id"}]
