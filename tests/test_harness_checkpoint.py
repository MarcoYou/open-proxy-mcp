"""합성 콜백/가짜 MCP로 복구 계약만 검증한다. 외부 API나 실제 MCP를 호출하지 않는다."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json

import pytest

from open_proxy_mcp.harness import (CallbackModelAdapter, CheckpointError, FileCheckpoint,
    StageExecutor, StagedStructureAdapter, SubmitAction, VotingHarness)
from open_proxy_mcp.harness.runner import ModelContext
from open_proxy_mcp.harness.experiment import ExperimentAdapter
from open_proxy_mcp.harness.experiment_config import Budget, CaseSpec
from open_proxy_mcp.harness.output_contracts import assessment_output_schema, response_format, wire_schema
from test_voting_harness_runner import FakeMCP, always_submit, request
from test_structure_stages import task_and_draft, reviews_for


def store(tmp_path):
    return FileCheckpoint(tmp_path / 'checkpoint', identity={'model': 'fixture', 'prompt_revision': '1'})


def test_completed_model_call_is_not_repeated_after_interruption(tmp_path):
    checkpoint = store(tmp_path); peer = FakeMCP(); seen = []
    async def callback(context):
        seen.append(context.tasks[0]['task_id'])
        return await always_submit(context)
    adapter = CallbackModelAdapter('fixture', '1', callback)
    original = checkpoint.state
    def interrupt(phase, operation=None, status='running'):
        original(phase, operation, status)
        if phase == 'completed_operation' and operation == 'model':
            checkpoint.state = original
            raise asyncio.CancelledError
    checkpoint.state = interrupt
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(VotingHarness(peer, adapter, checkpoint=checkpoint).run(request()))
    assert seen == ['a']
    result = asyncio.run(VotingHarness(peer, adapter, checkpoint=store(tmp_path)).run(request()))
    assert result.status == 'complete' and seen == ['a', 'b']
    assert result.counts['model_calls'] == 2


def test_uncertain_model_call_is_not_reissued(tmp_path):
    checkpoint = store(tmp_path); count = 0
    async def interrupted(context):
        nonlocal count
        count += 1
        raise asyncio.CancelledError
    adapter = CallbackModelAdapter('fixture', '1', interrupted); peer = FakeMCP()
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(VotingHarness(peer, adapter, checkpoint=checkpoint).run(request()))
    with pytest.raises(CheckpointError, match='inflight_uncertain'):
        asyncio.run(VotingHarness(peer, adapter, checkpoint=store(tmp_path)).run(request()))
    assert count == 1


def test_resume_refuses_changed_sources_before_new_model_work(tmp_path):
    peer = FakeMCP(); seen = []
    async def callback(context):
        seen.append(context.tasks[0]['task_id'])
        return await always_submit(context)
    adapter = CallbackModelAdapter('fixture', '1', callback)
    asyncio.run(VotingHarness(peer, adapter, checkpoint=store(tmp_path)).run(request()))
    peer.change = 'source'
    with pytest.raises(CheckpointError, match='sources_or_policy_changed'):
        asyncio.run(VotingHarness(peer, adapter, checkpoint=store(tmp_path)).run(request()))
    assert seen == ['a','b']


def test_resume_refuses_changed_request_or_corrupt_receipt(tmp_path):
    checkpoint = store(tmp_path)
    with checkpoint.lease({'cutoff': 'before'}):
        checkpoint.state('running')
    with pytest.raises(CheckpointError, match='binding_changed'):
        with checkpoint.lease({'cutoff': 'after'}):
            pass
    (checkpoint.root / 'manifest.json').write_text('{}')
    with pytest.raises(CheckpointError, match='corrupt'):
        with checkpoint.lease({'cutoff': 'before'}):
            pass


def test_same_checkpoint_cannot_be_leased_twice(tmp_path):
    with store(tmp_path).lease({'test': True}):
        with pytest.raises(CheckpointError, match='already_running'):
            with store(tmp_path).lease({'test': True}):
                pass


def test_stage_resume_uses_completed_evidence_and_finishes_other_stages(tmp_path):
    task, draft = task_and_draft(); seen = []; checkpoint = store(tmp_path)
    context = ModelContext(tasks=(task,), research_plan={}, binding={}, feedback=(),
                           remaining_model_rounds=1, action_schema={})
    async def callback(ctx):
        seen.append(ctx.stage)
        if ctx.stage == 'evidence': return {k:draft[k] for k in ('facts','gaps')}
        if ctx.stage == 'findings': return {'findings':draft['findings']}
        if ctx.stage == 'judgments': return {'judgments':draft['judgments']}
        return {'reviews':reviews_for(task, draft)}
    original = checkpoint.state
    def interrupt(phase, operation=None, status='running'):
        original(phase, operation, status)
        if phase == 'completed_operation' and operation == 'stage:evidence':
            checkpoint.state = original
            raise asyncio.CancelledError
    checkpoint.state = interrupt
    with checkpoint.lease({'fixture':1}):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(StagedStructureAdapter('fixture','1',callback).assess(
                replace(context, checkpoint=checkpoint.cursor('model/1'))))
    with store(tmp_path).lease({'fixture':1}):
        pass
    with checkpoint.lease({'fixture':1}):
        value = asyncio.run(StagedStructureAdapter('fixture','1',callback).assess(
            replace(context, checkpoint=checkpoint.cursor('model/1'))))
    assert isinstance(value, SubmitAction)
    assert seen == ['evidence','findings','judgments','review']


def test_experiment_and_product_adapters_use_same_executor():
    task, draft = task_and_draft()
    context = ModelContext(tasks=(task,), research_plan={}, binding={}, feedback=(),
                           remaining_model_rounds=1, action_schema={})
    outputs = {'evidence': {k:draft[k] for k in ('facts','gaps')}, 'findings': {'findings':draft['findings']},
        'judgments': {'judgments':draft['judgments']}, 'review': {'reviews':reviews_for(task, draft)}}
    async def callback(ctx): return deepcopy(outputs[ctx.stage])
    class Model:
        budget = Budget()
        async def complete(self, packet, stage): return deepcopy(outputs[stage])
    case = CaseSpec(id='fixture', company='합성회사', year=2025, meeting_type='annual',
        cutoff_at=task['execution_context']['cutoff_at'], notice_rcept_no='20250301000001',
        agenda_ids=[task['agendas'][0]['agenda_id']])
    product = StagedStructureAdapter('proxyvo','1',callback)
    experiment = ExperimentAdapter(Model(),case,'같은 질문과 허용 자료를 사용한다.','staged')
    assert isinstance(product.executor, StageExecutor) and isinstance(experiment.executor, StageExecutor)
    a, b = asyncio.run(product.assess(context)), asyncio.run(experiment.assess(context))
    assert a.model_dump() == b.model_dump()
    assert product.events == experiment.events


def test_strict_schema_closes_every_object_without_losing_fact_data():
    schema = assessment_output_schema()
    assert schema == wire_schema(schema)  # 두 번 변환해도 사실 종류가 불어나지 않는다.
    kinds = schema['properties']['facts']['items']['anyOf']
    term = next(s for s in kinds if s['properties']['kind']['enum'] == ['term'])
    assert 'term_years' in term['properties']['data']['properties']
    def check(node):
        if isinstance(node, dict):
            if node.get('type') == 'object':
                assert node['additionalProperties'] is False
                assert set(node['required']) == set(node['properties'])
            for value in node.values(): check(value)
        elif isinstance(node, list):
            for value in node: check(value)
    check(schema)
    assert response_format(schema,'json_schema')['strict'] is True
    assert response_format(schema,'json_object') == {'type':'json_object'}


def test_arbitrary_dictionary_is_not_silently_stripped():
    with pytest.raises(ValueError, match='open_output_object'):
        wire_schema({'type':'object','additionalProperties':True})


def test_charter_event_constraints_survive_structured_output_conversion():
    facts = assessment_output_schema()['properties']['facts']['items']['anyOf']
    event = next(f for f in facts if f['properties']['kind']['enum'] == ['charter_event'])
    branches = {b['properties']['event_kind']['enum'][0]:b['properties'] for b in event['properties']['data']['anyOf']}
    for kind in ('snapshot','proposal'):
        assert branches[kind]['target_event_id']['type'] == 'null'
        assert branches[kind]['outcome']['enum'] == ['unknown']
    assert branches['correction']['target_event_id']['type'] == 'string'
    assert branches['correction']['target_event_id']['minLength'] == 1


def test_whole_harness_resumes_inside_shared_stage_executor(tmp_path):
    from open_proxy_mcp.services.election_structure import accept_structure_assessment
    task, draft = task_and_draft(); seen = []; checkpoint = store(tmp_path)
    class StructurePeer(FakeMCP):
        async def call_tool(self, name, arguments):
            response = await super().call_tool(name, arguments)
            submissions = arguments.get('guideline_structure', {}).get('assessments', [])
            response['data']['guideline_application']['structure_tasks'] = [{
                'task':deepcopy(task), 'assessment':accept_structure_assessment(task, submissions[0] if submissions else None)}]
            return response
    async def callback(ctx):
        seen.append(ctx.stage)
        if ctx.stage == 'evidence': return {k:draft[k] for k in ('facts','gaps')}
        if ctx.stage == 'findings': return {'findings':draft['findings']}
        if ctx.stage == 'judgments': return {'judgments':draft['judgments']}
        return {'reviews':reviews_for(task, draft)}
    original = checkpoint.state
    def interrupt(phase, operation=None, status='running'):
        original(phase, operation, status)
        if phase == 'completed_operation' and operation == 'stage:evidence':
            checkpoint.state = original
            raise asyncio.CancelledError
    checkpoint.state = interrupt
    # 이 합성 과업에서 평가하지 않을 구조 안건은 명시적으로 범위 밖으로 둔다.
    draft['out_of_scope_agenda_ids'] = [a['agenda_id'] for a in task['agendas'][1:]]
    async def scoped_callback(ctx):
        value = await callback(ctx)
        if ctx.stage == 'evidence': value['out_of_scope_agenda_ids'] = draft['out_of_scope_agenda_ids']
        return value
    peer = StructurePeer(candidates=())
    def harness(cp): return VotingHarness(peer, StagedStructureAdapter('fixture','1',scoped_callback), checkpoint=cp)
    req = request(structure=True, structure_protocol='staged')
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(harness(checkpoint).run(req))
    result = asyncio.run(harness(store(tmp_path)).run(req))
    assert seen == ['evidence','findings','judgments','review']
    assert result.counts['accepted'] == 1 and result.human_reviewed is False
