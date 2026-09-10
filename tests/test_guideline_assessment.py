"""Contract failures and recommendation guards, separate from judgment accuracy."""
from copy import deepcopy

import pytest

from open_proxy_mcp.services.guideline_assessment import (
    GuidelineAssessment, accept_assessment, assessment_metrics,
    build_assessment_task, pilot_recommendation, prepare_assessments,
)
from open_proxy_mcp.services.guideline_policy import evaluate_guideline_policy, load_pilot_guideline_policy


def packet(**changes):
    args = dict(candidate={"name": "홍길동", "birth_date": "1970.1.1", "role_type": "사외이사"},
                corp_code="00000001", agenda_title="홍길동 선임", notice_rcept="20260301000001",
                notice_text="홍길동은 이번에 새로 선임되는 사외이사 후보이며 외부기관 소속이다.",
                as_of="20260302", policy=load_pilot_guideline_policy(), attendance={})
    args.update(changes)
    return build_assessment_task(**args)


def submission(task):
    judgment = {"rationale": "계약 검사용 합성 판단. 정확성 검증이 아님.",
                "evidence_refs": [{"source_id": "notice:20260301000001",
                                   "quote": "홍길동은 이번에 새로 선임되는 사외이사 후보"}],
                "counterevidence": [], "unresolved": []}
    return {"task_id": task["task_id"], "evaluator": "synthetic-test",
            "appointment": {**deepcopy(judgment), "value": "new"},
            "independence": {**deepcopy(judgment), "value": "no_concern"}}


def recommendation(task, data):
    accepted = accept_assessment(task, GuidelineAssessment.model_validate(data))
    trace = evaluate_guideline_policy(load_pilot_guideline_policy(), assessment_metrics(accepted))
    return accepted, pilot_recommendation(trace)


def test_accepted_assessment_drives_rule_and_preserves_unreviewed_label():
    task = packet()
    data = submission(task)
    accepted, decision = recommendation(task, data)
    assert decision == "FOR"
    assert accepted["human_reviewed"] is False
    assert accepted["status"] == "accepted_unreviewed"
    data["independence"]["value"] = "concern"
    assert recommendation(task, data)[1] == "AGAINST"
    data["appointment"]["value"] = "unknown"
    data["appointment"]["unresolved"] = ["재직기간 확인 필요"]
    assert recommendation(task, data)[1] == "AGAINST"  # known opposition survives missing attendance


@pytest.mark.parametrize("change", [
    {"as_of": "20260303"}, {"corp_code": "00000002"},
    {"agenda_title": "다른 선임 안건"},
    {"notice_text": "홍길동은 이번에 새로 선임되는 사외이사 후보이며 새 정정사항이 있다."},
    {"candidate": {"name": "홍길동", "birth_date": "1971.1.1", "role_type": "사외이사"}},
    {"policy": {**load_pilot_guideline_policy(), "version": "new"}},
])
def test_stale_wrong_subject_policy_or_source_is_rejected(change):
    task = packet()
    accepted, decision = recommendation(packet(**change), submission(task))
    assert accepted["status"] == "rejected"
    assert decision == "REVIEW"


@pytest.mark.parametrize("mutation", ["invented_quote", "wrong_source", "no_quote", "unknown_without_issue"])
def test_unverifiable_judgments_cannot_vote(mutation):
    task = packet()
    data = submission(task)
    judgment = data["independence"]
    if mutation == "invented_quote":
        judgment["evidence_refs"][0]["quote"] = "원문에는 존재하지 않는 조작된 인용 문구입니다"
    elif mutation == "wrong_source":
        judgment["evidence_refs"][0]["source_id"] = "notice:20990101000001"
    elif mutation == "no_quote":
        judgment["evidence_refs"] = []
    else:
        judgment["value"] = "unknown"
    assert recommendation(task, data)[0]["status"] == "rejected"


def test_missing_unknown_renewal_and_unresolved_support_require_review():
    task = packet()
    assert assessment_metrics(accept_assessment(task, None))["coverage_complete"] is False
    data = submission(task)
    data["appointment"]["value"] = "renewed"
    assert recommendation(task, data)[1] == "REVIEW"  # no verified prior-term attendance
    data["appointment"]["value"] = "new"
    data["independence"]["unresolved"] = ["현재 거래관계의 중요성이 미확인"]
    assert recommendation(task, data)[1] == "REVIEW"


def test_input_cannot_forge_human_review_or_duplicate_task():
    data = submission(packet())
    with pytest.raises(ValueError, match="duplicate"):
        prepare_assessments([data, data])
    data["human_reviewed"] = True
    with pytest.raises(ValueError, match="schema"):
        prepare_assessments([data])
    del data["human_reviewed"]
    data["independence"]["value"] = True
    with pytest.raises(ValueError, match="schema"):
        prepare_assessments([data])


def test_future_notice_provides_no_citable_source():
    task = packet(as_of="20260228")
    assert task["sources"] == []
    assert recommendation(task, submission(task))[0]["status"] == "rejected"


def test_unavailable_private_details_are_followups_not_an_automatic_review():
    task = packet()
    data = submission(task)
    data["independence"]["value"] = "no_public_concern"
    data["independence"]["information_gaps"] = [{
        "question": "미공개 자문 보수 확인 필요",
        "kind": "private_employment_advisory_compensation",
        "availability": "not_found_in_reviewed_public_sources",
        "search_scope": ["공고와 회사 공식 관계 자료 검토"],
        "disposition": "follow_up_only",
    }]
    accepted, decision = recommendation(task, data)
    assert decision == "FOR"
    assert accepted["assessment"]["independence"]["information_gaps"]
    assert accepted["human_reviewed"] is False
    data["independence"]["unresolved"] = ["공개된 중요 거래 주장과 반증이 충돌함"]
    assert recommendation(task, data)[1] == "REVIEW"
    data["independence"]["information_gaps"][0]["availability"] = "fetch_failed"
    with pytest.raises(ValueError, match="schema"):
        prepare_assessments([data])


def test_supplemental_source_is_bound_to_task_and_quote_validation():
    src = {"rcept_no": "20260202000001", "source_url": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260202000001",
           "text": "홍길동은 해당 회사에 이번에 신규로 선임되는 후보이다.", "status": "read"}
    task = packet(supplemental=[src])
    assert task["task_id"] != packet()["task_id"]
    data = submission(task)
    data["appointment"]["evidence_refs"] = [{"source_id": "filing:20260202000001",
                                            "quote": "홍길동은 해당 회사에 이번에 신규로 선임되는 후보이다."}]
    assert recommendation(task, data)[0]["status"] == "accepted_unreviewed"


def test_candidate_citations_obey_visual_uncertainty_too():
    task = packet(); data = submission(task)
    source=task['sources'][0]
    text=source['excerpts'][0]
    source['visual_reading']={'readings':[{'page':1,'text':text,'uncertainties':['unclear'],
        'uncertain_spans':[{'start':0,'end':len(text),'reason':'unclear'}]}]}
    assert recommendation(task,data)[0]['status']=='rejected'


def test_candidate_posture_changes_guidance_and_invalidates_prior_assessment():
    from open_proxy_mcp.services.guideline_workflow import apply_workflow_policy
    low=packet(policy=apply_workflow_policy(load_pilot_guideline_policy(), {'decision_posture':0}))
    high=packet(policy=apply_workflow_policy(load_pilot_guideline_policy(), {'decision_posture':1}))
    assert high['decision_guidance']['value']==1
    assert low['task_id']!=high['task_id']
    assert accept_assessment(high,GuidelineAssessment.model_validate(submission(low)))['status']=='rejected'
