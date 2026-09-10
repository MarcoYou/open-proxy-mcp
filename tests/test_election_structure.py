"""Source-bound structure contracts, not an independent voting accuracy benchmark."""
from copy import deepcopy
import importlib
import importlib.util

import pytest


def module():
    assert importlib.util.find_spec('open_proxy_mcp.services.election_structure'), 'structure task missing'
    return importlib.import_module('open_proxy_mcp.services.election_structure')


def setup_task():
    payload = {'data': {'agenda_decisions': [
        {'agenda_title': '정관 제25조 임기 변경', 'agenda_category': 'articles_amendment', 'decision': 'FOR'},
        {'agenda_title': '후보 선임', 'agenda_category': 'director_election', 'decision': 'AGAINST'},
    ]}}
    sources = [{'source_id': 'notice:20250301000001', 'document_sha256': 'a' * 64,
                'excerpts': ['변경 전 이사의 임기는 3년으로 한다. 변경 후 이사의 임기는 3년 이내에서 주주총회 결의로 정한다.'],
                'published': '20250301'}]
    task = module().build_structure_task(payload, sources=sources,
        binding={'meeting_pin': 'm', 'effective_as_of': '20250318', 'cutoff_at': '2025-03-19T00:00:00+09:00'},
        policy={'id': 'fixture', 'version': '1', 'workflow_settings': {}})
    return payload, task


def submission(task):
    agenda = task['agendas'][0]['agenda_id']
    cite = {'source_id': task['sources'][0]['source_id'], 'quote': task['sources'][0]['excerpts'][0]}
    return {'task_id': task['task_id'], 'evaluator': 'test-model', 'facts': [
        {'fact_id': 'term', 'kind': 'clause_change', 'agenda_ids': [agenda],
         'description': '고정 임기를 상한과 주총의 결정 재량으로 바꾸는 제안이다.',
         'evidence_refs': [cite], 'data': {
             'before_type': 'fixed', 'after_type': 'maximum', 'before_value': 3, 'after_value': 3,
             'unit': 'years', 'role': 'director', 'authority': 'shareholders', 'incumbent_effect': 'unknown'}}],
        'gaps': [], 'findings': [
            {'finding_id': 'effect', 'criterion_id': 'ES-02', 'agenda_ids': [agenda],
             'fact_ids': ['term'], 'effect': 'neutral', 'materiality': 'limited',
             'rationale': '상한 연장이 아니며 이번 후보별 임기 효과와 구분한다.', 'counterevidence_refs': [], 'gap_ids': []}],
        'judgments': [{'agenda_id': agenda, 'finding_ids': ['effect'], 'gap_ids': [],
                       'recommendation': 'FOR', 'rationale': '확인된 임기 상한은 동일하며 선택 정책의 반대 요건을 충족하지 않는다.',
                       'condition_id': None, 'exception_evidence_refs': []}]}


def test_fixed_to_maximum_is_cited_meaning_not_a_keyword_direction_rule():
    _, task = setup_task()
    accepted = module().accept_structure_assessment(task, submission(task))
    assert accepted['status'] == 'accepted_unreviewed'
    assert accepted['accepted_facts'][0]['data']['before_type'] == 'fixed'
    assert accepted['human_reviewed'] is False


def test_bad_citation_rejects_only_its_dependencies():
    _, task = setup_task()
    item = submission(task)
    item['facts'].append({**deepcopy(item['facts'][0]), 'fact_id': 'invented',
                          'evidence_refs': [{'source_id': task['sources'][0]['source_id'], 'quote': '원문에 없는 별개의 주장입니다.'}]})
    result = module().accept_structure_assessment(task, item)
    assert [r['fact_id'] for r in result['accepted_facts']] == ['term']
    assert result['rejected_items'][0]['item_id'] == 'invented'
    assert result['judgments'][0]['recommendation'] == 'FOR'


def test_source_change_invalidates_task_and_submission():
    payload, task = setup_task()
    next_task = module().build_structure_task(payload, sources=[{**task['sources'][0], 'document_sha256': 'b'*64}],
        binding=task['execution_context'], policy=task['policy'])
    assert next_task['task_id'] != task['task_id']
    assert module().accept_structure_assessment(next_task, submission(task))['status'] == 'rejected'


def test_unread_or_budget_exhausted_cannot_be_skipped_as_nonpublic():
    _, task = setup_task()
    for kind in ('not_read', 'budget_exhausted', 'conflicting_evidence'):
        item = submission(task)
        item['gaps'] = [{'gap_id': 'g', 'agenda_ids': [task['agendas'][0]['agenda_id']],
                         'criterion_id': 'ES-03', 'kind': kind, 'disposition': 'skip_criterion',
                         'question': '후보별 임기 확인', 'reviewed_source_ids': []}]
        result = module().accept_structure_assessment(task, item)
        assert any(r['item_id'] == 'g' for r in result['rejected_items'])


def test_missing_term_skips_schedule_and_retains_other_reasoning():
    payload, task = setup_task()
    item = submission(task)
    item['gaps'] = [{'gap_id': 'g', 'agenda_ids': [task['agendas'][0]['agenda_id']],
                     'criterion_id': 'ES-03', 'kind': 'not_disclosed_in_reviewed_sources', 'disposition': 'skip_criterion',
                     'question': '후보별 임기는 검토 원문에 기재되지 않았다.',
                     'reviewed_source_ids': [task['sources'][0]['source_id']]}]
    item['judgments'][0]['gap_ids'] = ['g']
    result = module().accept_structure_assessment(task, item)
    module().apply_structure_results(payload, task, result, {'automation': 'automatic'})
    first, second = payload['data']['agenda_decisions']
    assert first['decision'] == 'FOR'
    assert first['structure_trace']['skipped_criteria'] == ['ES-03']
    assert second['decision'] == 'AGAINST'


@pytest.mark.parametrize('mode,decision,expected', [('automatic','AGAINST','ready_for_auto'),
    ('selective','AGAINST','manual_review'),('manual','FOR','manual_review')])
def test_policy_decision_and_automation_are_independent(mode, decision, expected):
    payload, task = setup_task()
    item = submission(task)
    if decision == 'AGAINST':
        item['findings'][0].update(effect='adverse', materiality='material')
    item['judgments'][0]['recommendation'] = decision
    result = module().accept_structure_assessment(task, item)
    module().apply_structure_results(payload, task, result, {'automation': mode})
    assert payload['data']['agenda_decisions'][0]['decision'] == decision
    assert payload['data']['agenda_decisions'][0]['voting_workflow']['status'] == expected


def test_material_adverse_finding_cannot_be_ignored_for_approval():
    _, task = setup_task()
    item = submission(task)
    item['findings'][0].update(effect='adverse', materiality='material')
    result = module().accept_structure_assessment(task, item)
    assert not result['judgments']
    assert any(r['code'] == 'material_adverse_without_exception' for r in result['rejected_items'])


def test_bad_seat_count_and_duplicate_candidate_do_not_become_valid_facts():
    _, task = setup_task()
    for data in ({'seats': 3, 'candidate_ids': ['p1','p2'], 'method': 'cumulative'},
                 {'seats': 2, 'candidate_ids': ['p1','p1'], 'method': 'cumulative'}):
        item = submission(task)
        item['facts'] = [{**item['facts'][0], 'kind': 'election_pool', 'data': data}]
        result = module().accept_structure_assessment(task, item)
        assert not result['accepted_facts']
        assert not result['judgments']


def test_one_new_separate_auditor_does_not_infer_total_one():
    _, task = setup_task()
    item = submission(task)
    item['facts'] = [{**item['facts'][0], 'kind': 'board_counts', 'data': {
        'charter_min':3,'charter_max':8,'actual_total':7,'proposed_total':8,
        'separate_continuing':1,'separate_new':1,'separate_total':2}}]
    result = module().accept_structure_assessment(task, item)
    assert result['accepted_facts'][0]['data']['separate_total'] == 2


def test_no_evaluable_findings_cannot_be_default_for():
    _, task = setup_task()
    item = submission(task)
    item['facts'] = []; item['findings'] = []; item['judgments'][0]['finding_ids'] = []
    assert not module().accept_structure_assessment(task, item)['judgments']


def test_structure_approval_cannot_certify_an_unassessed_candidate():
    payload, task = setup_task()
    row = payload['data']['agenda_decisions'][0]
    row.update(agenda_category='director_election', decision='REVIEW',
               guideline_trace={'llm_assessment': {'status': 'pending'}})
    result = module().accept_structure_assessment(task, submission(task))
    module().apply_structure_results(payload, task, result, {'automation': 'automatic'})
    assert row['decision'] == 'REVIEW'
    assert row['voting_workflow']['status'] != 'ready_for_auto'


def test_ocr_uncertain_page_cannot_supply_a_concrete_fact():
    _, task = setup_task()
    task['sources'][0]['visual_reading'] = {'readings': [
        {'page': 1, 'text': task['sources'][0]['excerpts'][0], 'uncertainties': ['3년/8년 판독 불명']}]}
    assert not module().accept_structure_assessment(task, submission(task))['judgments']


def test_finding_gap_cannot_be_hidden_by_omitting_it_from_judgment():
    _, task = setup_task()
    item = submission(task)
    item['gaps'] = [{'gap_id': 'g', 'agenda_ids': [task['agendas'][0]['agenda_id']],
        'criterion_id': 'ES-02', 'kind': 'meaning_unresolved', 'disposition': 'retry_read',
        'question': '임기 결정권 불명', 'reviewed_source_ids': [task['sources'][0]['source_id']]}]
    item['findings'][0]['gap_ids'] = ['g']
    assert not module().accept_structure_assessment(task, item)['judgments']


def test_conflicting_duplicate_judgments_accept_neither():
    _, task = setup_task()
    item = submission(task)
    item['judgments'].append({**item['judgments'][0], 'recommendation': 'REVIEW'})
    assert not module().accept_structure_assessment(task, item)['judgments']


def test_attachment_request_identity_and_provenance_survive_packet():
    from open_proxy_mcp.harness.runner import SourceRead, _source_id
    from open_proxy_mcp.services.guideline_evidence import build_source_packet, source_read_window_key
    request = {'type': 'dart_attachment', 'rcept_no': '20250301000001', 'dcm_no': '12345678'}
    assert SourceRead.model_validate(request).request()['dcm_no'] == '12345678'
    assert source_read_window_key(request) != source_read_window_key({**request, 'dcm_no': '12345679'})
    item = {**request, 'source_id': _source_id(request), 'source_url': 'https://dart.fss.or.kr/',
            'status': 'read', 'text': '정관 이사의 임기는 3년으로 한다.',
            'document_sha256': 'a'*64, 'document_hash_basis': 'original_source_bytes',
            'visual_reading': {'status': 'model_read_unreviewed', 'readings': []}}
    packet = build_source_packet(item)
    assert packet['read_next']['source_request']['dcm_no'] == request['dcm_no']
    assert packet['original_document_sha256'] == 'a'*64
    assert packet['visual_reading']['status'] == 'model_read_unreviewed'


def test_runner_discovers_structure_task_separately():
    from open_proxy_mcp.harness.runner import _tasks, _task_identity, HarnessRequest
    payload, task = setup_task()
    payload['data']['guideline_application'] = {'structure_tasks': [
        {'task': task, 'assessment': {'status': 'pending'}}]}
    tasks, states = _tasks(payload)
    assert task['task_id'] in tasks
    assert states[task['task_id']] == 'pending'
    assert _task_identity(task)[0] == 'election_structure'
    request = HarnessRequest(company='fixture', year=2025, meeting_type='annual',
        cutoff_at='2025-03-19T00:00:00+09:00', notice_rcept_no='20250301000001', structure=True)
    assert request.arguments()['guideline_structure'] == {'assessments': []}


def test_accepted_structure_preserves_existing_contested_routing():
    payload, task = setup_task()
    row = payload['data']['agenda_decisions'][0]
    row.update(agenda_category='director_election', decision='FOR',
        guideline_trace={'llm_assessment': {'status': 'accepted_unreviewed'}},
        agenda_relation_links=[{'type': 'contested'}])
    module().apply_structure_results(payload, task, module().accept_structure_assessment(task, submission(task)), {'automation': 'automatic'})
    assert row['voting_workflow']['status'] == 'manual_review'


def test_structure_manual_agenda_id_is_supported_configuration():
    from open_proxy_mcp.services.guideline_workflow import resolve_workflow_settings
    assert resolve_workflow_settings({'manual_agenda_ids': ['agenda:1']})['manual_agenda_ids'] == ['agenda:1']


def test_ocr_uncertainty_excludes_only_its_bound_span():
    _, task = setup_task()
    quote = task['sources'][0]['excerpts'][0]
    task['sources'][0]['visual_reading'] = {'readings': [
        {'page': 1, 'text': quote + '\n7', 'uncertainties': ['unclear marker'],
         'uncertain_spans': [{'start': len(quote)+1, 'end': len(quote)+2, 'reason': 'unclear marker'}]}]}
    assert module().accept_structure_assessment(task, submission(task))['judgments']


def test_skipped_only_criterion_cannot_still_support_for():
    _, task = setup_task()
    item = submission(task)
    item['gaps'] = [{'gap_id': 'g', 'agenda_ids': [task['agendas'][0]['agenda_id']],
        'criterion_id': 'ES-02', 'kind': 'not_disclosed_in_reviewed_sources',
        'disposition': 'skip_criterion', 'question': '판정에 필요한 범위 미기재',
        'reviewed_source_ids': [task['sources'][0]['source_id']]}]
    assert not module().accept_structure_assessment(task, item)['judgments']


def test_partial_structure_is_returned_to_runner_for_local_repair():
    from open_proxy_mcp.harness.runner import _tasks
    payload, task = setup_task()
    result = module().accept_structure_assessment(task, submission(task))
    payload['data']['guideline_application'] = {'structure_tasks': [{'task':task,'assessment':result}]}
    assert result['completion_status'] == 'partial'
    assert _tasks(payload)[1][task['task_id']] == 'partial'
    item=submission(task)
    item['out_of_scope_agenda_ids']=[task['agendas'][1]['agenda_id']]
    assert module().accept_structure_assessment(task,item)['completion_status'] == 'complete'


def test_native_html_can_be_cited_when_another_image_page_is_uncertain():
    _, task = setup_task()
    quote=task['sources'][0]['excerpts'][0]
    task['sources'][0].update(native_text_excerpts=[quote],visual_reading={'readings':[
        {'page':1,'text':'다른 페이지 불명','uncertainties':['흐림']}]})
    assert module().accept_structure_assessment(task,submission(task))['judgments']


def test_attachment_index_reaches_model_navigation(monkeypatch):
    from open_proxy_mcp.services import guideline_harness
    context={'structure_enabled':True}
    monkeypatch.setattr(guideline_harness,'get_harness_context',lambda:context)
    module().capture_structure_sources('20250301000001','소집공고',[
        {'status':'read','type':'dart_attachments','attachments':[{'dcm_no':'12345678'}]}])
    assert context['structure_reading_requests'][0]['attachments'][0]['dcm_no']=='12345678'


def test_conditional_fact_cannot_support_an_unconditional_judgment():
    _,task=setup_task(); item=submission(task)
    condition={**deepcopy(item['facts'][0]),'fact_id':'passed','kind':'condition',
               'data':{'agenda_id':task['agendas'][0]['agenda_id'],'outcome':'passed'}}
    item['facts'][0].update(kind='term', data={'person_id':'p1','role':'director','term_years':2,'condition_id':'passed'})
    item['facts'].append(condition)
    assert not module().accept_structure_assessment(task,item)['judgments']


def test_declared_unresolved_gap_cannot_be_omitted_from_a_judgment():
    _,task=setup_task(); item=submission(task)
    item['gaps']=[{'gap_id':'g','agenda_ids':[task['agendas'][0]['agenda_id']],
        'criterion_id':'ES-03','kind':'not_read','disposition':'retry_read','question':'핵심 임기 분포 미독',
        'reviewed_source_ids':[]}]
    assert not module().accept_structure_assessment(task,item)['judgments']


def test_manual_id_selection_applies_even_without_structure_submission():
    payload, task = setup_task()
    row = payload['data']['agenda_decisions'][1]
    row['voting_workflow'] = {'status':'ready_for_auto','recommendation':'AGAINST'}
    result = module().accept_structure_assessment(task,None)
    module().apply_structure_results(payload,task,result,{'automation':'automatic','manual_agenda_ids':[row['agenda_id']]})
    assert row['voting_workflow']['status']=='manual_review'
    assert row['decision']=='AGAINST'
