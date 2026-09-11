"""Temporal/claim boundaries; synthetic cases do not certify legal correctness."""
from copy import deepcopy
import pytest
from test_election_structure import setup_task, submission, module


def event(task, id, kind, *, target=None, outcome='unknown', event_date='2025-03-01', effective=None):
    return {'fact_id': id, 'kind': 'charter_event', 'agenda_ids': [task['agendas'][0]['agenda_id']],
            'description': 'fixture source reading',
            'evidence_refs': [{'source_id': task['sources'][0]['source_id'],
                               'quote': task['sources'][0]['excerpts'][0]}],
            'data': {'event_kind': kind, 'clause_ids': ['제25조'], 'event_date': event_date,
                     'effective_date': effective, 'outcome': outcome, 'target_event_id': target,
                     'text_scope': 'excerpt', 'meaning': '임기 조항', 'conditions': []}}


def evaluate(events):
    _, task = setup_task()
    item = submission(task)
    item['facts'].extend(events(task))
    return module().accept_structure_assessment(task, item)


def test_proposal_never_becomes_current_charter_and_missing_dates_stay_unknown():
    result = evaluate(lambda t: [event(t, 'p', 'proposal'), event(t, 's', 'snapshot', event_date=None)])
    assert result['status'] == 'accepted_unreviewed'
    rows = {r['event_id']: r for r in result['charter_history']['events']}
    assert rows['p']['state'] == 'proposed'
    assert rows['s']['state'] == 'snapshot_candidate'
    assert result['charter_history']['current_charter_verified'] is False
    assert result['human_reviewed'] is False


@pytest.mark.parametrize('outcome,effective,state', [
    ('passed','2025-03-01','adopted_effective_candidate'),
    ('passed','2025-04-01','adopted_not_yet_effective'),
    ('passed',None,'adopted_effect_unknown'),
    ('rejected','2025-03-01','not_adopted'),
])
def test_resolution_status_requires_own_outcome_effect_and_link(outcome,effective,state):
    r=evaluate(lambda t:[event(t,'p','proposal'),event(t,'r','resolution',target='p',outcome=outcome,effective=effective)])
    assert r['charter_history']['events'][-1]['state']==state
    assert r['charter_history']['complete_history'] is False


def test_unlinked_resolution_does_not_invent_clause_history():
    r=evaluate(lambda t:[event(t,'r','resolution',outcome='passed',effective='2025-03-01')])
    assert r['charter_history']['events'][0]['state']=='resolution_unlinked'


@pytest.mark.parametrize('mutation', ['future_event','unknown_target','cycle','cross_clause'])
def test_bad_event_isolated_and_other_agenda_reasoning_survives(mutation):
    def events(t):
        p=event(t,'p','proposal');r=event(t,'r','resolution',target='p',outcome='passed',effective='2025-03-01')
        if mutation=='future_event':r['data']['event_date']='2025-03-20'
        if mutation=='unknown_target':r['data']['target_event_id']='missing'
        if mutation=='cycle':p['data']['target_event_id']='r'
        if mutation=='cross_clause':r['data']['clause_ids']=['제99조']
        return [p,r]
    r=evaluate(events)
    assert any(i['item_id']=='r' for i in r['rejected_items'])
    assert r['judgments'][0]['recommendation']=='FOR'


def test_correction_preserves_predecessor_and_never_silently_replaces_entire_snapshot():
    r=evaluate(lambda t:[event(t,'s','snapshot'),event(t,'c','correction',target='s')])
    assert len(r['charter_history']['events'])==2
    assert r['charter_history']['events'][-1]['state']=='correction_requires_reconciliation'


@pytest.mark.parametrize('kind', ['snapshot', 'proposal'])
def test_charter_role_constraint_is_visible_in_json_schema_and_returns_actionable_code(kind):
    import jsonschema
    _, t = setup_task()
    fact = event(t, 's', kind, target='baseline')
    schema = t['fact_data_schemas']['charter_event']
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(fact['data'], schema)
    item = submission(t); item['facts'].append(fact)
    result = module().accept_structure_assessment(t, item)
    assert {'item_id': 's', 'code': 'invalid_charter_event_role'} in result['rejected_items']
    fact['data']['target_event_id'] = None
    jsonschema.validate(fact['data'], schema)
    assert result['judgments']  # The unrelated valid judgment survives.


def test_malformed_charter_role_does_not_crash_other_items():
    _, t = setup_task(); item = submission(t)
    bad = event(t, 'malformed-role', 'proposal')
    bad['data']['event_kind'] = ['proposal']
    item['facts'].append(bad)
    result = module().accept_structure_assessment(t, item)
    assert result['judgments'][0]['recommendation'] == 'FOR'
    assert result['rejected_items'] == [{'item_id': 'malformed-role', 'code': 'invalid_fact_data_or_citation'}]
