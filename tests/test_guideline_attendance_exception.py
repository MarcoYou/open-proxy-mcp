"""A full-attendance exception is inapplicable, not approved or unknown.

Synthetic source inputs exercise acceptance, arithmetic and policy compatibility;
they are not independent measurements of voting-recommendation accuracy.
"""
from copy import deepcopy

import pytest

from open_proxy_mcp.harness.runner import _assessment_rejection_feedback
from open_proxy_mcp.services.guideline_assessment import (
    GuidelineAssessment, accept_assessment, assessment_metrics, pilot_recommendation,
)
from open_proxy_mcp.services.guideline_policy import evaluate_guideline_policy, load_pilot_guideline_policy
from test_guideline_attendance_contract import attendance_case


REASON = "출석 예외 비적용은 대상 회의 전부의 참석이 확정된 경우에만 허용됩니다."


def full_attendance():
    task, data = attendance_case()
    for meeting in data["attendance"]["meetings"]:
        meeting["attendance"] = "present"
    data["attendance"]["exception"].update(
        value="not_applicable", evidence_refs=[], counterevidence=[], unresolved=[],
        rationale="직무 대상 회의 전부에 참석하여 불참 사유의 예외 심사 대상이 없다.")
    return task, data


def accept(task, data):
    return accept_assessment(task, GuidelineAssessment.model_validate(data))


def verdict(result):
    return pilot_recommendation(evaluate_guideline_policy(load_pilot_guideline_policy(), assessment_metrics(result)))


def test_full_attendance_needs_no_invented_unknown_or_separate_exception_quote():
    task, data = full_attendance()
    result = accept(task, data)
    assert result["status"] == "accepted_unreviewed"
    assert result["human_reviewed"] is False
    assert result["assessment"]["attendance"]["exception"]["value"] == "not_applicable"
    calculation = result["attendance_calculation"]
    assert calculation["eligible_meetings"] == calculation["attended_meetings"] == 3
    assert calculation["attendance_pct"] == 100
    assert calculation["exception_accepted"] is None
    assert assessment_metrics(result)["attendance_exception_accepted"] is None

    legacy = deepcopy(data)
    legacy["attendance"]["exception"].update(value="unknown", unresolved=["불참 사유는 별도 판단하지 않음"])
    prior = accept(task, legacy)
    assert result["attendance_calculation"] == prior["attendance_calculation"]
    assert assessment_metrics(result) == assessment_metrics(prior)
    assert verdict(result) == verdict(prior) == "FOR"


@pytest.mark.parametrize("mutation", [
    "low_attendance", "incomplete_list", "unknown_attendance", "unknown_duty",
    "no_meetings", "no_service", "unresolved_attendance", "unresolved_period",
])
def test_incomplete_or_less_than_full_attendance_cannot_claim_exception_inapplicable(mutation):
    task, data = full_attendance()
    attendance = data["attendance"]
    if mutation == "low_attendance":
        attendance["meetings"][1]["attendance"] = "absent"
    elif mutation == "incomplete_list":
        attendance["all_board_meetings_covered"] = False
    elif mutation == "unknown_attendance":
        attendance["meetings"][1]["attendance"] = "unknown"
    elif mutation == "unknown_duty":
        row = attendance["meetings"][0]
        row["attendance"] = "unknown"
        row["duty_eligibility"] = {"value": "unknown", "boundary": "service_start",
            "rationale": "선임 당일 회의와 선임의 선후관계가 미확정이다.",
            "evidence_refs": deepcopy(row["evidence_refs"])}
    elif mutation == "no_meetings":
        attendance["meetings"] = []
    elif mutation == "no_service":
        attendance["service_intervals"] = []
    elif mutation == "unresolved_attendance":
        attendance.update(value="unknown", unresolved=["회의 목록의 완전성 확인 필요"])
    else:
        task["attendance_period"] = {"status": "unresolved"}
    result = accept(task, data)
    assert result["status"] == "rejected"
    assert result["reason"] == REASON
    assert assessment_metrics(result)["attendance_exception_accepted"] is None


def test_above_policy_threshold_is_not_the_same_as_all_meetings_attended():
    task, data = full_attendance()
    row = data["attendance"]["meetings"][0]
    data["attendance"]["meetings"] = [
        {**deepcopy(row), "meeting_id": str(index), "date": f"2025-0{index}-01",
         "attendance": "present" if index < 5 else "absent"}
        for index in range(1, 6)]
    assert accept(task, data)["reason"] == REASON  # 80% is above the default 75%, but not full attendance.


@pytest.mark.parametrize("mutation", ["unresolved", "missing_kind", "conflict_kind", "not_assessed_kind", "counterevidence"])
def test_inapplicable_exception_cannot_hide_unresolved_information_or_counterevidence(mutation):
    task, data = full_attendance()
    exception = data["attendance"]["exception"]
    if mutation == "unresolved":
        exception["unresolved"] = ["불참 사유 확인 필요"]
    elif mutation == "counterevidence":
        exception["counterevidence"] = deepcopy(data["attendance"]["evidence_refs"])
    else:
        exception["unresolved_kind"] = {"missing_kind": "missing_information", "conflict_kind": "conflicting_evidence",
                                        "not_assessed_kind": "not_assessed"}[mutation]
    assert accept(task, data)["reason"] == REASON


@pytest.mark.parametrize("where", ["meeting", "service", "exception"])
def test_inapplicable_exception_never_bypasses_current_source_citations(where):
    task, data = full_attendance()
    forged = [{"source_id": "annual:20260301000002", "quote": "현재 원문에 없는 가짜 출석 판단의 인용문입니다."}]
    if where == "meeting":
        data["attendance"]["meetings"][0]["evidence_refs"] = forged
    elif where == "service":
        data["attendance"]["service_intervals"][0]["evidence_refs"] = forged
    else:
        data["attendance"]["exception"]["evidence_refs"] = forged
    assert accept(task, data)["status"] == "rejected"


def test_confirmed_duty_exclusion_does_not_require_an_absence_exception():
    task, data = full_attendance()
    row = data["attendance"]["meetings"][0]
    row["attendance"] = "unknown"
    row["duty_eligibility"] = {"value": "not_on_duty", "boundary": "service_start",
        "rationale": "선임 당일의 선임 전 회의라 해당 후보의 직무 대상이 아니다.",
        "evidence_refs": deepcopy(row["evidence_refs"])}
    result = accept(task, data)
    assert result["status"] == "accepted_unreviewed"
    calculation = result["attendance_calculation"]
    assert calculation["attended_meetings"] == calculation["eligible_meetings"] == 2
    assert calculation["excluded_boundary_meetings"] == 1
    assert calculation["exception_accepted"] is None


@pytest.mark.parametrize("value, flag, expected_vote", [
    ("accepted", True, "FOR"), ("rejected", False, "AGAINST"), ("unknown", None, "REVIEW"),
])
def test_existing_exception_values_keep_their_low_attendance_behavior(value, flag, expected_vote):
    task, data = attendance_case()
    data["attendance"]["exception"]["value"] = value
    if value == "unknown":
        data["attendance"]["exception"]["unresolved"] = ["불참 사유 확인 필요"]
    result = accept(task, data)
    assert result["status"] == "accepted_unreviewed"
    assert result["attendance_calculation"]["exception_accepted"] is flag
    assert verdict(result) == expected_vote


def test_empty_unknown_is_not_silently_reinterpreted_as_inapplicable():
    task, data = full_attendance()
    data["attendance"]["exception"]["value"] = "unknown"
    result = accept(task, data)
    assert result["status"] == "rejected"
    assert result["reason"] == "unknown 평가에 미확인 사항이 없습니다."


def test_rejected_inapplicable_exception_has_specific_same_task_model_feedback():
    task, data = full_attendance()
    data["attendance"]["meetings"][1]["attendance"] = "absent"
    result = accept(task, data)
    payload = {"data": {"agenda_decisions": [{"guideline_trace": {
        "assessment_task": task, "llm_assessment": result}}]}}
    assert _assessment_rejection_feedback(payload, task["task_id"]) == (
        "server_rejected_assessment", "assessment_validation_reason: " + REASON)
    assert _assessment_rejection_feedback(payload, "other-task") == ("server_rejected_assessment",)
