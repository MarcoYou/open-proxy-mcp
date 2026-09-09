"""Synthetic boundary tests; these are not an independent accuracy benchmark."""
from copy import deepcopy
import hashlib
import json

import pytest

from open_proxy_mcp.services.guideline_assessment import (
    GuidelineAssessment, accept_assessment, assessment_metrics, pilot_recommendation,
)
from open_proxy_mcp.services.guideline_correction import (
    build_candidate_findings, candidate_correction_eligibility,
)
from open_proxy_mcp.services.guideline_policy import evaluate_guideline_policy, load_pilot_guideline_policy
from open_proxy_mcp.services.proxy_advise import _decide_director_election
from test_guideline_assessment import packet, submission


def candidate():
    return {"name": "홍길동", "birth_date": "1970.1.1", "role_type": "사외이사",
            "disqualification": {"summary": "unknown_no_field"},
            "independence": {"summary": "long_tenure_concerns", "sub_factors": {
                "five_year_rule": {"result": "potential_long_tenure", "source": "roster_tenure", "years": 9}}},
            "faithfulness": {"concurrent_outside_directors": {"summary": "single_position", "total": 1}}}


def case(ev=None):
    ev = deepcopy(ev or candidate())
    task = packet(candidate=ev)
    # Build the contract independently of the service's forthcoming wiring.
    task["baseline_findings"] = build_candidate_findings(ev)
    unhashed = {k: v for k, v in task.items() if k != "task_id"}
    task["task_id"] = hashlib.sha256(json.dumps(unhashed, ensure_ascii=False, sort_keys=True,
                                               separators=(",", ":")).encode()).hexdigest()
    data = submission(task)
    data["finding_reviews"] = [
        {"finding_id": finding["finding_id"], "disposition": "incorrect_extraction",
         "rationale": "합성 신규 선임 공시를 확인하여 회사 근무기간을 이사 재직으로 읽은 관측을 교정함.",
         "evidence_refs": deepcopy(data["appointment"]["evidence_refs"]), "counterevidence": [], "unresolved": []}
        for finding in task["baseline_findings"] if finding["correctable"]]
    return ev, task, data


def correction(task, data, **changes):
    accepted = accept_assessment(task, GuidelineAssessment.model_validate(data))
    args = {"baseline_decision": "REVIEW", "proposed_decision": "FOR", "baseline_is_candidate_only": True}
    args.update(changes)
    return accepted, candidate_correction_eligibility(task, accepted, **args)


def test_company_service_misattributed_as_director_tenure_can_be_corrected():
    ev, task, data = case()
    assert _decide_director_election(ev)[0] == "REVIEW"
    accepted, effect = correction(task, data)
    assert accepted["status"] == "accepted_unreviewed"
    assert effect["eligible"] is True
    assert effect["human_reviewed"] is False
    assert effect["retained_finding_ids"] == []
    assert len(effect["corrected_finding_ids"]) == 1
    # Preserve the baseline and source observation for audit; do not mutate them.
    assert ev["independence"]["sub_factors"]["five_year_rule"]["years"] == 9
    assert task["baseline_findings"][0]["observed"]["years"] == 9


def test_historical_office_and_tenure_are_separate_findings():
    ev = candidate()
    ev["faithfulness"]["concurrent_outside_directors"] = {
        "summary": "strong_concerns_concurrent", "total": 3,
        "signals": [{"company": "옛직장", "period": "2020–2024", "role": "사외이사"}]}
    _, task, data = case(ev)
    assert {item["kind"] for item in task["baseline_findings"]} == {"tenure", "concurrent_positions"}
    assert correction(task, data)[1]["eligible"] is True
    data["finding_reviews"].pop()
    assert correction(task, data)[1]["eligible"] is False


def test_tenure_early_return_does_not_hide_a_real_transaction_concern():
    ev = candidate()
    ev["independence"]["sub_factors"]["recent_3y_transactions"] = {
        "result": "transactions_exist", "raw": "자문거래가 계속됨"}
    _, task, data = case(ev)
    relation = next(item for item in data["finding_reviews"] if item["finding_id"].startswith("relationship:"))
    relation["disposition"] = "confirmed"
    _, effect = correction(task, data)
    assert effect["eligible"] is False
    assert relation["finding_id"] in effect["retained_finding_ids"]
    assert any(key.startswith("tenure:") for key in effect["corrected_finding_ids"])


@pytest.mark.parametrize("protected", ["eligibility", "audit_history", "unmapped"])
def test_out_of_scope_adverse_findings_survive_tenure_correction(protected):
    ev = candidate()
    if protected == "eligibility":
        ev["disqualification"] = {"summary": "red_flag", "sub_factors": {
            "eligibility": {"result": "red_flag", "raw_flags": {"legalDisqualification": "기재된 사실"}}}}
    elif protected == "audit_history":
        ev["faithfulness"]["audit_history_check"] = {"summary": "red_flag", "red_flags": [{"type": "risk"}]}
    else:
        ev["faithfulness"]["summary"] = "future_material_concern"
    _, task, data = case(ev)
    accepted, effect = correction(task, data)
    assert accepted["status"] == "accepted_unreviewed"
    assert effect["eligible"] is False
    assert len(effect["retained_finding_ids"]) == 1


@pytest.mark.parametrize("changes", [
    {"baseline_decision": "AGAINST"}, {"baseline_decision": "NO_VOTE"},
    {"baseline_is_candidate_only": False}, {"law_layer_id": "A2"},
    {"agenda_relation_type": "conditional"}, {"agenda_relation_type": "withdrawn"},
    {"agenda_relation_type": "alternative"}, {"agenda_relation_type": "procedural"},
])
def test_correction_cannot_relax_other_decision_or_ballot_constraints(changes):
    _, task, data = case()
    assert correction(task, data, **changes)[1]["eligible"] is False


@pytest.mark.parametrize("mutation", ["wrong_id", "duplicate", "invented_quote", "wrong_source", "no_quote", "empty_reason", "counterevidence", "unresolved"])
def test_malformed_or_unresolved_correction_is_not_accepted(mutation):
    _, task, data = case()
    review = data["finding_reviews"][0]
    if mutation == "wrong_id":
        review["finding_id"] = "different-candidate-or-observation"
    elif mutation == "duplicate":
        data["finding_reviews"].append(deepcopy(review))
    elif mutation == "invented_quote":
        review["evidence_refs"][0]["quote"] = "검토한 원문에 존재하지 않는 임의의 신규 선임 설명입니다."
    elif mutation == "wrong_source":
        review["evidence_refs"][0]["source_id"] = "notice:20990101000001"
    elif mutation == "no_quote":
        review["evidence_refs"] = []
    elif mutation == "empty_reason":
        review["rationale"] = " "
    elif mutation == "counterevidence":
        review["counterevidence"] = deepcopy(review["evidence_refs"])
    else:
        review["unresolved"] = ["현재 직책인지 귀속을 확인할 수 없음"]
    accepted, effect = correction(task, data)
    assert accepted["status"] == "rejected"
    assert effect["eligible"] is False


def test_unresolved_review_is_valid_but_does_not_clear_the_finding():
    _, task, data = case()
    review = data["finding_reviews"][0]
    review.update(disposition="unresolved", evidence_refs=[], unresolved=["이사 최초 선임일을 판독하지 못함"])
    accepted, effect = correction(task, data)
    assert accepted["status"] == "accepted_unreviewed"
    assert effect["eligible"] is False


def test_missing_optional_reviews_do_not_reject_the_candidate():
    _, task, data = case()
    del data["finding_reviews"]
    accepted, effect = correction(task, data)
    assert accepted["status"] == "accepted_unreviewed"
    assert effect["eligible"] is False


def test_cannot_claim_to_correct_a_protected_eligibility_finding():
    ev = candidate()
    ev["disqualification"] = {"summary": "red_flag"}
    _, task, data = case(ev)
    protected = next(item for item in task["baseline_findings"] if not item["correctable"])
    data["finding_reviews"].append({**deepcopy(data["finding_reviews"][0]), "finding_id": protected["finding_id"]})
    assert correction(task, data)[0]["status"] == "rejected"


def test_new_independence_opposition_is_not_removed_by_a_tenure_correction():
    _, task, data = case()
    data["independence"]["value"] = "concern"
    accepted = accept_assessment(task, GuidelineAssessment.model_validate(data))
    proposed = pilot_recommendation(evaluate_guideline_policy(load_pilot_guideline_policy(), assessment_metrics(accepted)))
    assert proposed == "AGAINST"
    assert candidate_correction_eligibility(task, accepted, baseline_decision="REVIEW",
                                          proposed_decision=proposed, baseline_is_candidate_only=True)["eligible"] is False


def test_findings_have_stable_subject_and_observation_bound_ids_without_mutation():
    ev = candidate()
    snapshot = deepcopy(ev)
    original = build_candidate_findings(ev)
    assert ev == snapshot
    assert original == build_candidate_findings(deepcopy(ev))
    ev["name"] = "다른후보"
    assert original[0]["finding_id"] != build_candidate_findings(ev)[0]["finding_id"]
    ev = candidate()
    ev["independence"]["sub_factors"]["five_year_rule"]["years"] = 10
    assert original[0]["finding_id"] != build_candidate_findings(ev)[0]["finding_id"]


def test_missing_data_is_not_invented_as_an_active_correction_hurdle():
    assert build_candidate_findings({"name": "홍길동", "role_type": "사외이사",
        "disqualification": {"summary": "unknown_no_field"},
        "faithfulness": {"concurrent_outside_directors": {"summary": "unknown_no_career_data"},
                         "audit_history_check": {"summary": "not_checked"}}}) == []


def test_no_findings_or_a_stale_task_cannot_unlock_review():
    _, task, data = case()
    accepted = accept_assessment(task, GuidelineAssessment.model_validate(data))
    accepted["task_id"] = "a-previous-source-packet"
    assert candidate_correction_eligibility(task, accepted, baseline_decision="REVIEW", proposed_decision="FOR",
                                          baseline_is_candidate_only=True)["eligible"] is False
    task["baseline_findings"] = []
    data["finding_reviews"] = []
    assert correction(task, data)[1]["eligible"] is False
