"""Fault-injection contracts. These do not establish small-model accuracy."""
import asyncio
from copy import deepcopy

import pytest

from open_proxy_mcp.harness import StageBudget, StagedStructureAdapter, StructureWorkbench
from open_proxy_mcp.harness.runner import ModelContext, SubmitAction
from open_proxy_mcp.services.election_structure import (
    DATA_TYPES, accept_structure_assessment, build_structure_task,
)
from open_proxy_mcp.services.structure_protocol import (
    evidence_identity, numeric_observations, ontology, preflight, review_basis,
)
from test_election_structure import setup_task, submission


def task_and_draft():
    payload, old = setup_task()
    task = build_structure_task(payload, sources=old['sources'], binding=old['execution_context'],
                                policy=old['policy'], protocol='staged')
    draft = submission(task)
    agenda = task['agendas'][0]['agenda_id']
    draft['facts'].append({'fact_id': 'scope', 'kind': 'ballot_scope', 'agenda_ids': [agenda],
        'description': '독립 표결', 'evidence_refs': draft['facts'][0]['evidence_refs'],
        'data': {'row_kind': 'ballot', 'scope_rationale': '해당 정관 조문 변경 표결'}})
    return task, draft


def reviews_for(task, draft):
    result = preflight(task, draft)
    return [{'agenda_id': key, 'basis_sha256': review_basis(task, result, key), 'reviewer': 'fixture',
        'checks': [{'check_id': check, 'outcome': 'supported', 'rationale': '원문과 대조한 테스트 응답',
                    'evidence_refs': deepcopy(draft['facts'][0]['evidence_refs'])} for check in ontology()['qa_checks']]}
        for key in sorted({j['agenda_id'] for j in result['judgments']})]


def test_ontology_reuses_fact_schemas_and_policy_criteria():
    import json
    from pathlib import Path
    policy = json.loads((Path(__file__).resolve().parents[1] /
        'open_proxy_mcp/data/guideline/opm-guideline-v2-pilot.json').read_text())
    data = ontology()
    assert data['criteria'].keys() == policy['election_structure']['criteria'].keys()
    for criterion in data['criteria'].values():
        assert set(criterion['fact_kinds']) <= DATA_TYPES.keys()
        assert set(criterion['sources']) <= data['source_routes'].keys()


def test_missing_review_never_applies_a_binary_staged_vote():
    task, draft = task_and_draft()
    result = accept_structure_assessment(task, draft)
    assert not result['judgments']
    assert result['rejected_items'][0]['code'] == 'missing_or_duplicate_review'
    assert result['accepted_facts']  # the original facts survive


@pytest.mark.parametrize('mutation,code', [
    ('rationale', 'stale_review_basis'), ('vote', 'stale_review_basis'),
    ('fact', 'stale_review_basis'), ('checks', 'incomplete_review_checks'),
    ('issue', 'unresolved_semantic_challenge'), ('quote', 'invalid_review_citation'),
])
def test_tampered_or_challenged_assessment_cannot_reuse_review(mutation, code):
    task, draft = task_and_draft()
    draft['reviews'] = reviews_for(task, draft)
    if mutation == 'rationale': draft['judgments'][0]['rationale'] += ' 추가 주장'
    if mutation == 'vote': draft['judgments'][0]['recommendation'] = 'REVIEW'
    if mutation == 'fact': draft['facts'][0]['description'] += ' 다른 주체로 수정'
    if mutation == 'checks': draft['reviews'][0]['checks'].pop()
    if mutation == 'issue': draft['reviews'][0]['checks'][0]['outcome'] = 'issue'
    if mutation == 'quote': draft['reviews'][0]['checks'][0]['evidence_refs'][0]['quote'] = '현재 원문 어디에도 존재하지 않는 가공한 문장입니다.'
    result = accept_structure_assessment(task, draft)
    assert not result['judgments']
    assert code in {r['code'] for r in result['rejected_items']}


def test_qa_failure_is_agenda_local_and_cannot_erase_the_good_peer():
    task, draft = task_and_draft()
    peer = task['agendas'][1]['agenda_id']
    for f in deepcopy(draft['facts']):
        f['fact_id'] += '-peer'; f['agenda_ids'] = [peer]; draft['facts'].append(f)
    finding = deepcopy(draft['findings'][0])
    finding.update(finding_id='peer-effect', agenda_ids=[peer], fact_ids=['term-peer'])
    draft['findings'].append(finding)
    judgment = deepcopy(draft['judgments'][0]); judgment.update(agenda_id=peer, finding_ids=['peer-effect'])
    draft['judgments'].append(judgment)
    draft['reviews'] = reviews_for(task, draft)
    draft['reviews'][0]['checks'][0]['outcome'] = 'issue'
    failed = draft['reviews'][0]['agenda_id']
    result = accept_structure_assessment(task, draft)
    assert len(result['judgments']) == 1
    assert result['judgments'][0]['agenda_id'] != failed
    assert result['completion_status'] == 'partial'


@pytest.mark.parametrize('kind,vote', [('parent','FOR'), ('ballot','NO_VOTE'), ('unclear','FOR')])
def test_firmness_cannot_change_the_frozen_ballot_kind(kind, vote):
    task, draft = task_and_draft()
    draft['facts'][1]['data']['row_kind'] = kind
    draft['judgments'][0]['recommendation'] = vote
    draft['reviews'] = reviews_for(task, draft)
    result = accept_structure_assessment(task, draft)
    assert not result['judgments']
    assert 'ballot_scope_decision_conflict' in {r['code'] for r in result['rejected_items']}


def test_fact_identity_and_evidence_prompt_do_not_depend_on_voting_preferences():
    task, _ = task_and_draft()
    other = deepcopy(task)
    other['policy']['workflow_settings'] = {'firmness': .9, 'stance': 1, 'automation': 1}
    other['decision_guidance']['value'] = .9
    other['task_id'] = 'changed-task-id'
    assert evidence_identity(task) == evidence_identity(other)
    a, b = [StructureWorkbench(t, 'fixture').context() for t in (task, other)]
    assert a.packet == b.packet
    assert 'policy' not in a.packet and 'decision_guidance' not in a.packet
    other['sources'][0]['excerpts'].append('정정된 조건')
    assert evidence_identity(task) != evidence_identity(other)


def test_seven_under_max_eight_is_not_a_violation_and_unknown_is_not_zero():
    observations = numeric_observations([{'fact_id': 'n', 'kind': 'board_counts',
        'data': {'charter_max': 8, 'actual_total': 7, 'proposed_total': None}}])
    assert [o['comparison'] for o in observations] == ['within_maximum', 'unknown']
    assert all(o['legal_or_voting_conclusion'] is False for o in observations)


def test_frozen_evidence_is_revalidated_and_invalidated_by_new_sources():
    task, draft = task_and_draft()
    bench = StructureWorkbench(task, 'fixture')
    bench.accept({k: draft[k] for k in ('facts','gaps')})
    frozen = bench.freeze_evidence()
    other = deepcopy(task); other['policy']['workflow_settings'] = {'firmness': .9}
    next_bench = StructureWorkbench(other, 'different-model')
    next_bench.reuse_evidence(frozen)
    assert next_bench.stage == 'findings'
    other['sources'][0]['excerpts'].append('새로 읽은 문서 구간')
    with pytest.raises(ValueError, match='frozen_evidence_mismatch'):
        StructureWorkbench(other, 'fixture').reuse_evidence(frozen)


def test_interpretation_gets_only_frozen_quotes_but_review_gets_counterevidence():
    task, draft = task_and_draft()
    task['sources'][0]['excerpts'].append('추가로 읽어야 하는 반증 조항 원문')
    bench = StructureWorkbench(task, 'fixture')
    bench.accept({k: draft[k] for k in ('facts','gaps')})
    assert '추가로 읽어야 하는 반증' not in str(bench.context().packet['sources'])
    bench.accept({'findings': draft['findings']}); bench.accept({'judgments': draft['judgments']})
    assert '추가로 읽어야 하는 반증' in str(bench.context().packet['sources'])


def test_rewind_invalidates_only_stage_and_downstream_outputs():
    task, draft = task_and_draft()
    bench = StructureWorkbench(task, 'fixture')
    assert bench.accept({k: draft[k] for k in ('facts','gaps')})['accepted']
    assert bench.accept({'findings': draft['findings']})['accepted']
    frozen = deepcopy(bench.outputs['evidence'])
    bench.rewind('findings')
    assert bench.outputs == {'evidence': frozen}
    assert bench.stage == 'findings'


def test_malformed_fact_peer_survives_as_quarantine_without_blocking_valid_facts():
    task, draft = task_and_draft()
    bench = StructureWorkbench(task, 'fixture')
    evidence = {k: deepcopy(draft[k]) for k in ('facts','gaps')}
    evidence['facts'].append({'fact_id': 'bad-peer', 'kind': 'term'})
    assert not bench.accept(evidence)['accepted']
    assert bench.accept(evidence, allow_partial=True)['accepted']
    assert bench.stage == 'findings'
    assert 'bad-peer' not in {f['fact_id'] for f in bench.context().packet['facts']}
    assert bench.context().packet['quarantined_items']
    assert bench.draft()['facts'][-1]['fact_id'] == 'bad-peer'
    with pytest.raises(ValueError, match='validated_evidence_required'):
        bench.freeze_evidence()


def test_adapter_reuses_same_contract_across_provider_callbacks():
    task, draft = task_and_draft()
    context = ModelContext(tasks=(task,), research_plan={}, binding={}, feedback=(),
                           remaining_model_rounds=1, action_schema={})
    for name in ('provider-a-fixture', 'provider-b-fixture'):
        stages = []
        async def callback(ctx):
            stages.append(ctx.stage)
            if ctx.stage == 'evidence': return {k: draft[k] for k in ('facts','gaps')}
            if ctx.stage == 'findings': return {'findings': draft['findings']}
            if ctx.stage == 'judgments': return {'judgments': draft['judgments']}
            return {'reviews': reviews_for(task, draft)}
        adapter = StagedStructureAdapter(name, 'fixture', callback)
        result = asyncio.run(adapter.assess(context))
        assert isinstance(result, SubmitAction)
        assert stages == ['evidence','findings','judgments','review']
        assert adapter.model_calls == 4
        assert accept_structure_assessment(task, result.assessments[0])['judgments']


def test_model_errors_have_bounded_retries_and_no_provider_payload_leak():
    task, _ = task_and_draft()
    async def fail(_): raise RuntimeError('private-token-response')
    adapter = StagedStructureAdapter('fixture', '1', fail, budget=StageBudget(max_calls=2))
    context = ModelContext(tasks=(task,), research_plan={}, binding={}, feedback=(),
                           remaining_model_rounds=1, action_schema={})
    result = asyncio.run(adapter.assess(context))
    assert result.action == 'finish'
    assert adapter.model_calls == 2
    assert 'private-token-response' not in str(adapter.events)


def test_declared_choice_groups_remain_execution_constraints_in_full_automation():
    from open_proxy_mcp.services.guideline_workflow import _execution_constraints
    row = {'structure_trace': {'accepted_facts': [{'kind':'ballot_scope',
            'data': {'row_kind':'ballot','choice_group':'dividend-alternatives','max_selections':1}}]}}
    assert 'conditional_ballot' in _execution_constraints(row)
