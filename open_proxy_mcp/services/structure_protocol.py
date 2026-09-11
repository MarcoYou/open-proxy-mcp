"""Executable vocabulary and source-bound review for the staged structure path.

No provider calls or stored judgments. A complete QA checklist is a caller claim,
not independent verification of meaning. Arithmetic never determines a vote.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr

from .guideline_assessment import Citation, _digest

PROTOCOL = 'opm-staged-structure/1'
_PATH = Path(__file__).resolve().parents[1] / 'data/guideline/structure-ontology.json'


def ontology() -> dict:
    return json.loads(_PATH.read_text())


class QAItem(BaseModel):
    model_config = ConfigDict(extra='forbid')
    check_id: StrictStr
    outcome: Literal['supported', 'issue', 'not_applicable']
    rationale: Annotated[StrictStr, Field(min_length=1, max_length=3000)]
    evidence_refs: Annotated[list[Citation], Field(max_length=12)] = Field(default_factory=list)


class AgendaReview(BaseModel):
    model_config = ConfigDict(extra='forbid')
    agenda_id: StrictStr
    basis_sha256: Annotated[StrictStr, Field(pattern=r'^[0-9a-f]{64}$')]
    reviewer: Annotated[StrictStr, Field(min_length=1, max_length=160)]
    checks: Annotated[list[QAItem], Field(min_length=1, max_length=12)]


def evidence_identity(task: dict) -> str:
    """A policy-preference-free identity; changing any readable input invalidates it."""
    return _digest({'ontology': ontology(), 'binding': task['execution_context'],
        'agendas': task['agendas'], 'sources': task['sources'],
        'fact_schemas': task['fact_data_schemas'],
        'gap_schema': task['item_schemas']['gaps']})


def review_basis(task: dict, result: dict, agenda_id: str) -> str:
    # Cross-agenda conditions/events can affect this agenda. Include their full
    # closure so changing a referenced prerequisite cannot preserve a QA seal.
    facts = {f['fact_id']: f for f in result['accepted_facts']}
    included = {key for key, f in facts.items() if agenda_id in f['agenda_ids']}
    judgments = [j for j in result['judgments'] if j['agenda_id'] == agenda_id]
    included |= {j['condition_id'] for j in judgments if j.get('condition_id')}
    while True:
        dependencies = {facts[key]['data'].get(field) for key in included if key in facts
                        for field in ('condition_id', 'depends_on_condition', 'target_event_id')}
        expanded = included | (dependencies - {None})
        if expanded == included:
            break
        included = expanded
    return _digest({'task_id': task['task_id'], 'agenda_id': agenda_id,
        'facts': [facts[k] for k in sorted(included) if k in facts],
        'gaps': [g for g in result['accepted_gaps'] if agenda_id in g['agenda_ids']],
        'findings': [f for f in result['accepted_findings'] if agenda_id in f['agenda_ids']],
        'judgments': judgments})


def numeric_observations(facts: list[dict]) -> list[dict]:
    """Compute only declared, comparable quantities; unknown never becomes zero."""
    observations = []
    for fact in facts:
        if fact['kind'] != 'board_counts':
            continue
        data = fact['data']
        for field in ('actual_total', 'proposed_total'):
            value, cap = data.get(field), data.get('charter_max')
            observations.append({'fact_id': fact['fact_id'], 'quantity': field,
                'value': value, 'maximum': cap,
                'comparison': 'unknown' if value is None or cap is None else
                              'within_maximum' if value <= cap else 'exceeds_declared_maximum',
                'legal_or_voting_conclusion': False})
    return observations


def preflight(task: dict, draft: dict) -> dict:
    from .election_structure import accept_structure_assessment
    local = {**task, 'assessment_protocol': 'direct'}
    return accept_structure_assessment(local, {**draft, 'reviews': []})


def validate_reviews(task: dict, result: dict, reviews: list[dict]) -> None:
    """Reject only affected judgments. Preserve all facts and independent peers."""
    from collections import Counter
    from .guideline_evidence import citations_match_readable_sources
    sources = {s['source_id']: s for s in task['sources']}
    expected = set(ontology()['qa_checks'])
    by_agenda: dict[str, list] = {}
    for raw in reviews:
        try:
            parsed = AgendaReview.model_validate(raw).model_dump()
            by_agenda.setdefault(parsed['agenda_id'], []).append(parsed)
        except ValueError:
            # Malformed reviews cannot grant any approval; missing check below
            # rejects the affected judgment without accepting arbitrary IDs.
            continue
    accepted, failed = [], set()
    for agenda_id in {j['agenda_id'] for j in result['judgments']}:
        candidates = by_agenda.get(agenda_id, [])
        code = None
        review = candidates[0] if len(candidates) == 1 else None
        if not review:
            code = 'missing_or_duplicate_review'
        elif review['basis_sha256'] != review_basis(task, result, agenda_id):
            code = 'stale_review_basis'
        elif (Counter(c['check_id'] for c in review['checks']) != Counter(expected)
              or not review['reviewer'].strip()
              or any(not c['rationale'].strip() for c in review['checks'])):
            code = 'incomplete_review_checks'
        elif any(not citations_match_readable_sources(c['evidence_refs'], sources)
                 or (c['outcome'] == 'supported' and not c['evidence_refs']) for c in review['checks']):
            code = 'invalid_review_citation'
        elif any(c['outcome'] == 'issue' for c in review['checks']):
            code = 'unresolved_semantic_challenge'
        # Scope is read once without voting preferences. QA cannot change a
        # parent's classification merely to obtain a more decisive recommendation.
        scopes = [f for f in result['accepted_facts'] if f['kind'] == 'ballot_scope'
                  and agenda_id in f['agenda_ids']]
        if code is None:
            if len(scopes) != 1:
                code = 'missing_or_conflicting_ballot_scope'
            else:
                kind = scopes[0]['data']['row_kind']
                votes = {j['recommendation'] for j in result['judgments'] if j['agenda_id'] == agenda_id}
                if ((kind in {'parent', 'report', 'withdrawn'} and votes != {'NO_VOTE'})
                    or (kind in {'ballot', 'conditional'} and 'NO_VOTE' in votes)
                    or (kind == 'unclear' and votes != {'REVIEW'})):
                    code = 'ballot_scope_decision_conflict'
        if code:
            failed.add(agenda_id)
            result['rejected_items'].append({'item_id': agenda_id, 'code': code})
        else:
            accepted.append(review)
    result['judgments'] = [j for j in result['judgments'] if j['agenda_id'] not in failed]
    result['staged_review'] = {'protocol': PROTOCOL, 'reviews': accepted,
        'challenged_reviews': [deepcopy(r) for key in sorted(failed) for r in by_agenda.get(key, [])],
        'failed_agenda_ids': sorted(failed), 'human_reviewed': False,
        'semantic_accuracy_certified': False}


def work_contract() -> dict:
    return {'protocol': PROTOCOL, 'ontology': ontology(),
        'stages': ['evidence', 'findings', 'judgments', 'review'],
        'review_schema': AgendaReview.model_json_schema(),
        'fact_identity_excludes': ['stance', 'firmness', 'automation'],
        'repair': 'Revise the rejected stage and its dependents. Never erase contrary evidence to obtain acceptance.',
        'model_execution': 'caller_callback', 'automatic_model_escalation': False}
