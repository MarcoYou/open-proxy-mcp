"""Routing policy boundaries, independent of source-reading accuracy."""
from copy import deepcopy

import pytest

from open_proxy_mcp.services.guideline_workflow import (
    apply_workflow_policy, resolve_workflow_settings, route_workflow,
)


def row(decision="FOR", **trace_changes):
    return {"agenda_title": "후보 선임", "decision": decision,
            "guideline_trace": {"mode": "pilot", "status": "evaluated",
                "decision_effect": "pilot_applied", "post_constraint_adjusted": False,
                "llm_assessment": {"status": "accepted_unreviewed"}, **trace_changes}}


def test_selective_routes_each_agenda_and_keeps_recommendations():
    rows = [row(), row("AGAINST"), row("REVIEW"),
            row(llm_assessment={"status": "pending"}),
            {"agenda_title": "재무제표", "decision": "FOR"}]
    decisions = [item["decision"] for item in rows]
    result = route_workflow(rows)
    assert result["counts"] == {"ready_for_auto": 1, "manual_review": 2,
                                "awaiting_assessment": 1, "not_applicable": 1}
    assert [item["decision"] for item in rows] == decisions
    assert result["ballot_submission_supported"] is False
    assert result["ballots_submitted"] == 0
    assert all(not item["voting_workflow"]["human_reviewed"]
               and not item["voting_workflow"]["ballot_submitted"] for item in rows)


def test_automatic_prepares_accepted_opposition_but_rejected_item_does_not_block_peer():
    rows = [row("AGAINST"), row(llm_assessment={"status": "rejected"}), row()]
    route_workflow(rows, {"automation": "automatic"})
    assert [item["voting_workflow"]["status"] for item in rows] == [
        "ready_for_auto", "manual_review", "ready_for_auto"]


def test_cumulative_candidate_support_is_not_a_completed_allocation():
    rows = [{**row(), "facts": {"election_method": "집중투표"}}, row()]
    route_workflow(rows, {"automation": "automatic"})
    assert rows[0]["decision"] == "FOR"
    assert rows[0]["voting_workflow"]["status"] == "manual_review"
    assert rows[1]["voting_workflow"]["status"] == "ready_for_auto"


@pytest.mark.parametrize("trace_change", [
    {"material_conflicts": ["동일인 귀속 충돌"]},
    {"post_constraint_adjusted": True},
    {"decision_effect": "protected_baseline"},
])
def test_automatic_does_not_mark_conflicts_or_protected_final_decisions_ready(trace_change):
    rows = [row(**trace_change)]
    route_workflow(rows, {"automation": "automatic"})
    assert rows[0]["voting_workflow"]["status"] == "manual_review"


def test_skipped_missing_information_alone_does_not_require_manual_review():
    rows = [row(skipped_criteria=[{"reason": "보수 세부 미공개"}])]
    route_workflow(rows)
    assert rows[0]["voting_workflow"]["status"] == "ready_for_auto"


def test_exact_manual_selection_and_unmatched_title_are_visible():
    rows = [row(), {**row(), "agenda_title": "다른 후보 선임"}]
    result = route_workflow(rows, {"automation": "automatic",
        "manual_agenda_titles": ["후보 선임", "없는 안건"]})
    assert rows[0]["voting_workflow"]["status"] == "manual_review"
    assert rows[1]["voting_workflow"]["status"] == "ready_for_auto"
    assert result["unmatched_manual_agenda_titles"] == ["없는 안건"]


def test_manual_routes_pending_pilot_but_does_not_claim_scope_over_other_agendas():
    rows = [row(llm_assessment={"status": "pending"}), row(mode="shadow"), row("NO_VOTE")]
    route_workflow(rows, {"automation": "manual"})
    assert [item["voting_workflow"]["status"] for item in rows] == [
        "manual_review", "not_applicable", "not_applicable"]


@pytest.mark.parametrize("value", [
    {"attendance_min_pct": True}, {"attendance_min_pct": "80"},
    {"attendance_min_pct": 49.9}, {"attendance_min_pct": 100.1},
    {"attendance_min_pct": float("nan")}, {"automation": "auto"},
    {"human_reviewed": True}, {"manual_agenda_titles": [" "]},
    {"manual_agenda_titles": ["중복", "중복"]},
])
def test_settings_reject_invalid_values_without_echo(value):
    with pytest.raises(ValueError, match="^guideline_workflow: invalid settings$"):
        resolve_workflow_settings(value)


def test_stance_and_automation_are_independent_and_threshold_requires_explicit_value():
    policy = {"parameters": {"attendance_min_pct": {"default": 75}},
              "assessment_rubric": {"scope": "기존 범위"}}
    original = deepcopy(policy)
    conservative = apply_workflow_policy(policy, {"stance": "conservative", "automation": "automatic"})
    assert conservative["parameters"]["attendance_min_pct"]["default"] == 75
    assert conservative["workflow_settings"]["automation"] == "automatic"
    custom = apply_workflow_policy(policy, {"attendance_min_pct": 85})
    assert custom["parameters"]["attendance_min_pct"]["default"] == 85
    assert policy == original
    rows = [row()]
    route_workflow(rows, conservative["workflow_settings"])
    assert rows[0]["voting_workflow"]["status"] == "ready_for_auto"


def test_explicit_threshold_updates_compiled_value_as_well_as_default():
    policy = {"parameters": {"attendance_min_pct": {"default": 75, "value": 80}}}
    assert apply_workflow_policy(policy, {"attendance_min_pct": 90})["parameters"][
        "attendance_min_pct"] == {"default": 90, "value": 90}
