"""Experiment contract tests with synthetic HTTP responses, not model quality scores."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path

import httpx
import pytest

from open_proxy_mcp.harness.experiment_config import (
    Budget, CaseSpec, ExperimentError, ExperimentPlan, Ledger, ModelSpec, attempt_state, code_identity, digest,
)
from open_proxy_mcp.harness.experiment import (
    CapturedTransport, ExperimentAdapter, core_identity, outcome_rows, schedule,
    task_for, unresolved_by_agenda, validate_packet, verify_manifest,
)
from open_proxy_mcp.harness.experiment_model import ResponsesModel
from open_proxy_mcp.harness.experiment_report import CHECKS, grading_packet, report, validate_grade
from open_proxy_mcp.harness.runner import ModelContext, ModelUnavailableError
from test_election_structure import setup_task, submission


def sample():
    _, task = setup_task()
    task['execution_context']['engine_bundle_sha256'] = 'e' * 64
    case = CaseSpec(id='example', company='합성회사', year=2025, meeting_type='annual',
        cutoff_at=task['execution_context']['cutoff_at'], notice_rcept_no='20250301000001',
        agenda_ids=[task['agendas'][0]['agenda_id']])
    payload = {'status': 'exact', 'data': {'guideline_harness': {
        'notice_rcept_no': case.notice_rcept_no, 'source_manifest': {'notice': 'a'*64}},
        'guideline_application': {'structure_tasks': [{'task': task, 'assessment': {}}]},
        'agenda_decisions': []}}
    return case, task, payload


def plan():
    case, _, _ = sample()
    return ExperimentPlan(name='test', models=[ModelSpec(id='model-a'), ModelSpec(id='model-b')],
        cases=[case], methods=['direct', 'staged'], prompts=[{'id': 'same', 'question': 'Compare using supplied sources and policy.'}],
        repetitions=2)


def context(task):
    return ModelContext(tasks=(task,), research_plan={}, binding={}, feedback=(), remaining_model_rounds=3,
                        action_schema={})


def test_plan_rejects_silent_unknown_dimensions_and_duplicates():
    config = plan().model_dump()
    config['source_mode'] = 'silently_use_live_web'
    with pytest.raises(ValueError): ExperimentPlan.model_validate(config)
    config = plan().model_dump(); config['methods'].append('direct')
    with pytest.raises(ValueError): ExperimentPlan.model_validate(config)


def test_schedule_complete_reproducible_and_balanced():
    p = plan(); jobs = schedule(p)
    assert len(jobs) == 8 and len({j['id'] for j in jobs}) == 8
    assert jobs == schedule(p)
    other = p.model_copy(update={'order_seed': 42})
    assert {j['id'] for j in jobs} == {j['id'] for j in schedule(other)}


def test_source_date_and_requested_agenda_checked_before_model():
    case, task, payload = sample()
    assert validate_packet(payload, case) == task
    task['sources'][0]['published'] = '20250319'
    with pytest.raises(ExperimentError, match='future_source'): validate_packet(payload, case)
    task['sources'][0]['published'] = None
    with pytest.raises(ExperimentError, match='publication_date'): validate_packet(payload, case)


def test_same_raw_hash_different_excerpts_is_input_drift():
    _, task, _ = sample()
    other = deepcopy(task); other['sources'][0]['excerpts'].append('반증을 추가했다.')
    assert core_identity(task) != core_identity(other)
    other = deepcopy(task); other['execution_context']['structure_protocol'] = 'staged'
    task['execution_context']['structure_protocol'] = 'direct'
    assert core_identity(task) == core_identity(other)


def test_ledger_never_overwrites_and_redacts_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv('TEST_API_KEY', 'test-credential-never-log')
    store = Ledger(tmp_path)
    store.write('first.json', {'text': 'test-credential-never-log', 'authorization': 'secret'})
    assert 'test-credential' not in (tmp_path/'first.json').read_text()
    with pytest.raises(ExperimentError, match='immutable'): store.write('first.json', {'text': 'changed'})


def test_manifest_refuses_changed_plan_code_or_snapshot(tmp_path):
    store = Ledger(tmp_path); p = plan()
    snapshot = {'evidence': 'original'}; store.write('packet.json', snapshot)
    m = {'plan_sha256': digest(p.model_dump()), 'code_sha256': digest(code_identity()),
         'snapshots': {'packet.json': digest(snapshot)}}
    store.write('manifest.json', m)
    assert verify_manifest(p, store) == m
    changed = p.model_copy(update={'repetitions': 3})
    with pytest.raises(ExperimentError, match='plan_changed'): verify_manifest(changed, store)
    (tmp_path/'packet.json').write_text('{}')
    with pytest.raises(ExperimentError, match='integrity'): verify_manifest(p, store)


def api_response(model='model-a', status='completed', text='{"ok":true}'):
    return {'id': 'response-fixture', 'model': model, 'status': status,
            'usage': {'input_tokens': 12, 'output_tokens': 4},
            'reasoning': {'effort': 'xhigh'},
            'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': text}]}]}


def probe_packet():
    return {'question': 'Same source and question as JSON', 'output_schema': {
        'type': 'object', 'properties': {'ok': {'type': 'boolean'}},
        'required': ['ok'], 'additionalProperties': False}}


def test_api_calls_start_fresh_and_model_swap_preserves_exact_input(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-credential-never-log')
    requests = []
    def respond(req):
        body = json.loads(req.content); requests.append(body)
        return httpx.Response(200, json=api_response(body['model']))
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            for model in ['model-a','model-b']:
                executor = ResponsesModel(ModelSpec(id=model), Budget(), Ledger(tmp_path), model, client)
                await executor.complete(probe_packet(), stage='direct')
                await executor.complete(probe_packet(), stage='direct')
    asyncio.run(run())
    assert all(r['input'] == requests[0]['input'] for r in requests)
    assert all(r['text']['format']['type'] == 'json_schema' and r['text']['format']['strict'] for r in requests)
    assert all(r['store'] is False and not {'conversation','previous_response_id','tools'} & r.keys() for r in requests)
    assert all('model-a' not in json.dumps(r['input']) and 'model-b' not in json.dumps(r['input']) for r in requests)
    assert 'test-credential-never-log' not in ''.join(p.read_text() for p in tmp_path.rglob('*.json'))


@pytest.mark.parametrize('status,error', [(401,'model_auth_unavailable'), (404,'model_not_available'), (429,'model_budget_unavailable')])
def test_provider_failures_are_typed_and_never_record_remote_error_body(tmp_path, monkeypatch, status, error):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-credential-never-log')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(status,
                json={'error': 'test-credential-never-log'}))) as client:
            model = ResponsesModel(ModelSpec(id='model-a'), Budget(http_retries=0), Ledger(tmp_path), 'trial', client)
            with pytest.raises(ModelUnavailableError) as caught: await model.complete(probe_packet(), stage='direct')
            assert caught.value.code == error
    asyncio.run(run())
    assert 'test-credential' not in ''.join(p.read_text() for p in tmp_path.rglob('*.json'))


def test_incomplete_generation_not_accepted_as_success(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-credential-never-log')
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200,
                json=api_response(status='incomplete')))) as client:
            model = ResponsesModel(ModelSpec(id='model-a'), Budget(), Ledger(tmp_path), 'trial', client)
            with pytest.raises(ExperimentError, match='incomplete'): await model.complete(probe_packet(), stage='direct')
    asyncio.run(run())


def test_captured_transport_refuses_changed_initial_packet(tmp_path):
    case, task, payload = sample(); expected = deepcopy(task)
    payload['data']['guideline_application']['structure_tasks'][0]['task']['sources'][0]['excerpts'] += ['changed']
    class Peer:
        async def call_tool(self, name, arguments): return payload
    transport = CapturedTransport(Peer(), case, Ledger(tmp_path), 'trial', expected)
    with pytest.raises(ExperimentError, match='frozen_task_changed'):
        asyncio.run(transport.call_tool('proxy_advise_before_meeting', {}))
    assert transport.failure == 'frozen_task_changed'


def test_direct_review_has_declared_revision_calls_and_does_not_change_policy():
    case, task, _ = sample()
    class Model:
        budget = Budget(direct_review_rounds=3)
        def __init__(self): self.calls=[]
        async def complete(self, packet, stage):
            self.calls.append(deepcopy(packet)); return submission(task)
    model = Model(); adapter = ExperimentAdapter(model, case, 'neutral question', 'direct_with_review')
    result = asyncio.run(adapter.assess(context(task)))
    assert len(model.calls) == 4
    assert result.assessments[0]['task_id'] == task['task_id']
    assert all(p['task']['policy'] == task['policy'] for p in model.calls)


def test_rejected_or_unsubmitted_model_vote_is_never_shown_as_applied():
    case, task, payload = sample()
    payload['data']['agenda_decisions'] = [{'agenda_id': case.agenda_ids[0], 'decision': 'FOR'}]
    rows = outcome_rows(payload, task, case)
    assert rows[0]['mcp_recommendation'] is None and not rows[0]['assessment_applied']
    payload['data']['guideline_application']['structure_tasks'][0]['assessment']['judgments'] = submission(task)['judgments']
    assert outcome_rows(payload, task, case)[0]['mcp_recommendation'] == 'FOR'


def test_grader_requires_full_coverage_and_actual_quotes():
    case, task, _ = sample(); cite = {'source_id': task['sources'][0]['source_id'], 'quote': task['sources'][0]['excerpts'][0]}
    grade = {'checks': [{'agenda_id':case.agenda_ids[0], 'check_id': c, 'outcome':'supported',
                        'rationale':'원문과 일치', 'evidence_refs':[cite]} for c in CHECKS]}
    assert validate_grade(grade, case.agenda_ids, task['sources'])
    bad = deepcopy(grade); bad['checks'][0]['evidence_refs'][0]['quote'] = 'fabricated evidence'
    with pytest.raises(ExperimentError, match='citation'): validate_grade(bad, case.agenda_ids, task['sources'])
    bad = deepcopy(grade); bad['checks'].pop()
    with pytest.raises(ExperimentError, match='coverage'): validate_grade(bad, case.agenda_ids, task['sources'])


def test_report_keeps_unstarted_jobs_and_does_not_claim_accuracy(tmp_path):
    p = plan(); store = Ledger(tmp_path)
    store.write('manifest.json', {'contract':p.contract,'schedule':schedule(p),'plan_sha256':digest(p.model_dump())})
    store.write('plan.json', p.model_dump())
    result = report(store)
    assert result['statuses'] == {'not_started':8}
    saved = json.loads(Path(result['report']).with_name('report.json').read_text())
    assert saved['semantic_accuracy_certified'] is False and saved['grades'] == []
    assert all(not row['latest_attempt_votes'] for row in saved['summary'])


def test_retry_does_not_replace_missing_first_attempt_or_hide_latest_interruption(tmp_path):
    p = plan(); store = Ledger(tmp_path); job = schedule(p)[0]
    store.write('manifest.json', {'contract':p.contract, 'schedule':[job], 'plan_sha256':digest(p.model_dump())})
    store.write('plan.json', p.model_dump())
    base = f'runs/{job["id"]}'
    store.write(base + '/attempt-001/started.json', {'job':job})
    store.write(base + '/attempt-002/started.json', {'job':job})
    row = {'agenda_id':p.cases[0].agenda_ids[0], 'title':'합성 안건', 'assessment_applied':True,
           'target_complete':True, 'mcp_recommendation':'FOR', 'workflow_status':'ready', 'judgments':[]}
    store.write(base + '/attempt-002/result.json', {'job':job, 'status':'target_complete', 'rows':[row]})
    state = attempt_state(store, job['id'])
    assert state['status'] == 'target_complete' and state['first'] is None and state['next_attempt'] == 3
    result = report(store)
    saved = json.loads(Path(result['report']).with_name('report.json').read_text())
    assert saved['summary'][0]['first_attempt_votes'] == {'UNASSESSED':1}
    assert saved['summary'][0]['latest_attempt_votes'] == {'FOR':1}
    store.write(base + '/attempt-003/started.json', {'job':job})
    state = attempt_state(store, job['id'])
    assert state['status'] == 'interrupted' and state['latest'] is None
    result = report(store)
    saved = json.loads(Path(result['report']).with_name('report.json').read_text())
    assert saved['summary'][0]['latest_attempt_votes'] == {'UNASSESSED':1}
    assert len(saved['attempts']) == 1


def test_conditional_branches_preserved_and_rejected_peer_not_counted_complete():
    case, task, payload = sample(); key = case.agenda_ids[0]
    accepted = payload['data']['guideline_application']['structure_tasks'][0]['assessment']
    accepted['judgments'] = [{'agenda_id':key, 'condition_id':'condition:one', 'recommendation':'FOR'},
                             {'agenda_id':key, 'condition_id':'condition:two', 'recommendation':'AGAINST'}]
    payload['data']['agenda_decisions'] = [{'agenda_id':key, 'decision':'REVIEW'}]
    row = outcome_rows(payload, task, case)[0]
    assert row['conditional'] and row['llm_recommendation'] == 'CONDITIONAL'
    assert len(row['judgments']) == 2 and row['target_complete']
    accepted['rejected_items'] = [{'item_id':'finding:peer', 'code':'invalid_finding_dependency'}]
    submitted = {'findings':[{'finding_id':'finding:peer', 'agenda_ids':[key]}]}
    row = outcome_rows(payload, task, case, submitted)[0]
    assert row['assessment_applied'] and not row['target_complete']


def test_rejection_scope_does_not_block_an_unrelated_requested_agenda():
    assessment = {'rejected_items':[{'item_id':'fact:other', 'code':'invalid_fact_data_or_citation'}]}
    submitted = {'facts':[{'fact_id':'fact:other', 'agenda_ids':['agenda:other']}]}
    result = unresolved_by_agenda(assessment, submitted, ['agenda:target','agenda:other'])
    assert not result['agenda:target'] and result['agenda:other']
    result = unresolved_by_agenda(assessment, None, ['agenda:target'])
    assert result['agenda:target']  # Unmapped rejection cannot certify a selected agenda.


def test_adapter_repairs_partial_branch_instead_of_finishing():
    case, task, _ = sample(); key = case.agenda_ids[0]
    task['previous_assessment'] = {'judgments':[{'agenda_id':key, 'recommendation':'FOR'}],
        'rejected_items':[{'item_id':key, 'code':'invalid_judgment_dependency'}]}
    class Model:
        budget = Budget()
        def __init__(self): self.calls = 0
        async def complete(self, packet, stage):
            self.calls += 1
            return submission(task)
    model = Model(); adapter = ExperimentAdapter(model, case, 'neutral question', 'direct')
    result = asyncio.run(adapter.assess(context(task)))
    assert model.calls == 1 and result.assessments


def test_judge_gets_independent_subject_and_cutoff_without_method_identity():
    case, task, _ = sample()
    task['execution_context']['structure_protocol'] = 'staged'
    task['execution_context']['model'] = 'hidden-model-name'
    packet = grading_packet(task, case, {'judgments':[]})
    assert packet['agendas'] == task['agendas']
    assert packet['case']['cutoff_at'] == case.cutoff_at
    assert packet['execution_context']['effective_as_of'] == task['execution_context']['effective_as_of']
    assert not {'structure_protocol','model'} & packet['execution_context'].keys()


def test_unsupported_structured_outputs_never_silently_falls_back(tmp_path, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', 'test-credential-never-log'); bodies = []
    def respond(req):
        bodies.append(json.loads(req.content))
        return httpx.Response(400, json={'error':'unsupported schema'})
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            model = ResponsesModel(ModelSpec(id='fixture'), Budget(), Ledger(tmp_path), 'trial', client)
            with pytest.raises(ExperimentError, match='request_rejected'):
                await model.complete(probe_packet(), stage='probe')
    asyncio.run(run())
    assert len(bodies) == 1 and bodies[0]['text']['format']['type'] == 'json_schema'
