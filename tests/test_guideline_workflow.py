"""Routing policy boundaries, independent of source-reading accuracy."""
from copy import deepcopy

import pytest

from open_proxy_mcp.services.guideline_workflow import (
    apply_workflow_policy, resolve_workflow_settings, route_workflow, finalize_workflow,
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
    route_workflow(rows, {"automation": 1})
    assert [item["voting_workflow"]["status"] for item in rows] == [
        "ready_for_auto", "manual_review", "ready_for_auto"]


def test_cumulative_candidate_support_is_not_a_completed_allocation():
    rows = [{**row(), "facts": {"election_method": "집중투표"}}, row()]
    route_workflow(rows, {"automation": 1})
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
    route_workflow(rows, {"automation": 1})
    assert rows[0]["voting_workflow"]["status"] == "manual_review"


def test_skipped_missing_information_alone_does_not_require_manual_review():
    rows = [row(skipped_criteria=[{"reason": "보수 세부 미공개"}])]
    route_workflow(rows)
    assert rows[0]["voting_workflow"]["status"] == "ready_for_auto"


def test_exact_manual_selection_and_unmatched_title_are_visible():
    rows = [row(), {**row(), "agenda_title": "다른 후보 선임"}]
    result = route_workflow(rows, {"automation": 0.75,
        "manual_agenda_titles": ["후보 선임", "없는 안건"]})
    assert rows[0]["voting_workflow"]["status"] == "manual_review"
    assert rows[1]["voting_workflow"]["status"] == "ready_for_auto"
    assert result["unmatched_manual_agenda_titles"] == ["없는 안건"]


def test_manual_routes_pending_pilot_but_does_not_claim_scope_over_other_agendas():
    rows = [row(llm_assessment={"status": "pending"}), row(mode="shadow"), row("NO_VOTE")]
    route_workflow(rows, {"automation": 0})
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
    conservative = apply_workflow_policy(policy, {"stance": 1, "automation": 1})
    assert conservative["parameters"]["attendance_min_pct"]["default"] == 75
    assert conservative["workflow_settings"]["automation"] == 1
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

@pytest.mark.parametrize('value', [-0.1, 1.1, True, '0.5', float('nan'), float('inf'), None])
def test_posture_rejects_invalid_values(value):
    with pytest.raises(ValueError):
        resolve_workflow_settings({'firmness': value})


def test_posture_is_numeric_preference_bound_to_policy_not_automation():
    assert resolve_workflow_settings()['firmness'] == 0.75
    for value in [0, 0.25, 0.5, 1]:
        p = apply_workflow_policy({}, {'firmness': value, 'automation': 0})
        assert p['workflow_settings']['firmness'] == value
        assert p['decision_guidance']['value'] == value
        rows = [row()]
        route_workflow(rows, p['workflow_settings'])
        assert rows[0]['decision'] == 'FOR'
        assert rows[0]['voting_workflow']['status'] == 'manual_review'


def payload(rows):
    return {'status': 'ok', 'data': {'agenda_decisions': rows,
        'guideline_application': {'mode': 'pilot'}}}


@pytest.mark.parametrize('name', ['stance', 'automation', 'firmness'])
@pytest.mark.parametrize('value', [-0.001, 1.001, True, False, '0.5', float('nan'), float('inf'), None])
def test_numeric_preferences_reject_coercion_and_nonfinite(name, value):
    with pytest.raises(ValueError, match='^guideline_workflow: invalid settings$'):
        resolve_workflow_settings({name: value})


@pytest.mark.parametrize('key', ['manual_agenda_titles', 'manual_agenda_ids'])
def test_full_automation_rejects_contradictory_manual_selection(key):
    with pytest.raises(ValueError):
        resolve_workflow_settings({'automation': 1, key: ['안건']})


@pytest.mark.parametrize('level,expected', [
    (0, ['manual_review'] * 3),
    (0.25, ['ready_for_auto', 'manual_review', 'manual_review']),
    (0.5, ['ready_for_auto', 'manual_review', 'manual_review']),
    (0.75, ['ready_for_auto', 'ready_for_auto', 'manual_review']),
    (1, ['ready_for_auto'] * 3),
])
def test_numeric_automation_routes_final_results(level, expected):
    data = payload([row('FOR'), row('AGAINST'), row('REVIEW')])
    finalize_workflow(data, {'automation': level})
    rows = data['data']['agenda_decisions']
    assert [r['voting_workflow']['status'] for r in rows] == expected
    assert rows[-1]['decision'] == ('AGAINST' if level == 1 else 'REVIEW')
    assert data['data']['guideline_application']['voting_workflow']['ballots_submitted'] == 0


@pytest.mark.parametrize('stance,expected', [(0, 'FOR'), (0.4999, 'FOR'), (0.5, 'AGAINST'), (1, 'AGAINST')])
def test_full_auto_unresolved_direction_is_explicit_user_policy_not_fact(stance, expected):
    original = {**row('REVIEW'), 'reason': '조항의 적용 범위가 미확인.', 'risk_factors': []}
    data = payload([deepcopy(original), row('AGAINST'), row('NO_VOTE')])
    finalize_workflow(data, {'automation': 1, 'stance': stance, 'firmness': 0})
    first, opposition, nonvote = data['data']['agenda_decisions']
    assert first['decision'] == expected
    assert first['risk_factors'] == []
    assert first['guideline_trace'] == original['guideline_trace']
    assert first['automation_trace']['assessment_recommendation'] == 'REVIEW'
    assert first['automation_trace']['basis'] == 'user_policy_fallback'
    assert '자동화 기본 정책 적용' in first['reason']
    assert original['reason'] in first['reason']
    assert opposition['decision'] == 'AGAINST'
    assert not opposition['automation_trace']['fallback_applied']
    assert nonvote['decision'] == 'NO_VOTE'
    assert 'automation_trace' not in nonvote


@pytest.mark.parametrize('state,status', [('pending', 'awaiting_assessment'), ('rejected', 'assessment_error')])
def test_full_auto_does_not_turn_unfinished_or_invalid_assessment_into_a_vote(state, status):
    data = payload([row('REVIEW', llm_assessment={'status': state}), row('FOR')])
    finalize_workflow(data, {'automation': 1})
    first, peer = data['data']['agenda_decisions']
    assert first['decision'] == 'REVIEW'
    assert 'automation_trace' not in first
    assert first['voting_workflow']['status'] == status
    assert peer['voting_workflow']['status'] == 'ready_for_auto'


def test_fallback_waits_for_structure_then_uses_combined_judgment():
    first = {**row('REVIEW'), 'agenda_id': 'a'}
    data = payload([first])
    result = {'status': 'pending', 'judgments': [], 'rejected_items': []}
    data['data']['guideline_application']['structure_tasks'] = [
        {'task': {'agendas': [{'agenda_id': 'a'}]}, 'assessment': result}]
    finalize_workflow(data, {'automation': 1, 'stance': 1})
    assert first['voting_workflow']['status'] == 'awaiting_assessment'
    assert 'automation_trace' not in first
    result.update(status='accepted_unreviewed', judgments=[{'agenda_id': 'a'}])
    # Structure application can resolve the earlier uncertainty before routing.
    first['decision'] = 'FOR'
    finalize_workflow(data, {'automation': 1, 'stance': 1})
    assert first['decision'] == 'FOR'
    assert first['automation_trace']['fallback_applied'] is False


def test_full_auto_keeps_allocation_constraints_without_a_manual_queue():
    first = {**row('REVIEW'), 'facts': {'election_method': '집중투표'}}
    data = payload([first])
    finalize_workflow(data, {'automation': 1, 'stance': 0})
    assert first['decision'] == 'FOR'
    assert first['voting_workflow']['status'] == 'execution_pending'
    assert first['voting_workflow']['execution_constraints'] == ['vote_allocation']
    assert not first['voting_workflow']['ballot_submitted']


def test_baseline_scope_is_not_relabeled_as_an_llm_assessment():
    data = payload([{'agenda_title': '배당', 'decision': 'REVIEW', 'reason': '배당 자료 미확인'}])
    finalize_workflow(data, {'automation': 1})
    first = data['data']['agenda_decisions'][0]
    assert first['automation_trace']['assessment_scope'] == 'baseline_engine'
    assert first['automation_trace']['basis'] == 'user_policy_fallback'
    assert 'guideline_trace' not in first


def test_finalize_is_idempotent_and_does_not_affect_shadow():
    data = payload([row('REVIEW')])
    finalize_workflow(data, {'automation': 1})
    previous = deepcopy(data)
    finalize_workflow(data, {'automation': 1})
    assert data == previous
    shadow = payload([row('REVIEW')])
    shadow['data']['guideline_application']['mode'] = 'shadow'
    previous = deepcopy(shadow)
    finalize_workflow(shadow, {'automation': 1})
    assert shadow == previous


def test_markdown_does_not_present_policy_fallback_as_a_clean_risk_assessment():
    from open_proxy_mcp.tools.proxy_advise_before_meeting import _render
    data = payload([{'agenda_title': '배당', 'decision': 'REVIEW', 'reason': '배당 자료 미확인'}])
    finalize_workflow(data, {'automation': 1, 'stance': 0})
    text = _render(data)
    assert '최종 방향의 출처: 사용자 자동화 기본 정책' in text
    assert '위험 해소를 확인한 것이 아닙니다' in text
    assert '위험 신호: 확인된 항목 없음' not in text
    assert '처리 상태: 자동 처리 준비' in text
