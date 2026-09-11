"""Source-bound charter chronology, not a reconstructed or certified legal text."""
from __future__ import annotations

from datetime import date
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator, model_validator

Text = Annotated[StrictStr, Field(min_length=1, max_length=6000)]
Identifier = Annotated[StrictStr, Field(min_length=1, max_length=200)]


class CharterEventData(BaseModel):
    model_config = ConfigDict(extra='forbid', json_schema_extra={'allOf': [{
        'if': {'properties': {'event_kind': {'enum': ['snapshot', 'proposal']}},
               'required': ['event_kind']},
        'then': {'properties': {'target_event_id': {'type': 'null'},
                                'outcome': {'const': 'unknown'}}},
    }, {
        'if': {'properties': {'event_kind': {'const': 'correction'}}, 'required': ['event_kind']},
        'then': {'required': ['target_event_id'],
                 'properties': {'target_event_id': {'type': 'string', 'minLength': 1}}},
    }]})
    event_kind: Literal['snapshot', 'proposal', 'resolution', 'correction']
    clause_ids: Annotated[list[Identifier], Field(min_length=1, max_length=100)]
    event_date: StrictStr | None = None
    effective_date: StrictStr | None = None
    target_event_id: Identifier | None = Field(default=None, description=(
        'Only resolution/correction links. snapshot and proposal MUST use null with outcome unknown. '
        'This is not a baseline/comparison link. Describe a baseline comparison in a separate context fact.'))
    outcome: Literal['passed', 'rejected', 'withdrawn', 'unknown'] = 'unknown'
    text_scope: Literal['full_text', 'excerpt', 'amendment_only', 'unknown']
    meaning: Text
    conditions: Annotated[list[Text], Field(max_length=20)] = Field(default_factory=list)

    @field_validator('event_date', 'effective_date')
    @classmethod
    def valid_date(cls, value):
        if value is not None and date.fromisoformat(value).isoformat() != value:
            raise ValueError('date must be YYYY-MM-DD')
        return value

    @model_validator(mode='after')
    def consistent_role(self):
        if len(set(self.clause_ids)) != len(self.clause_ids):
            raise ValueError('duplicate clauses')
        if self.event_kind in {'snapshot', 'proposal'} and (self.target_event_id or self.outcome != 'unknown'):
            raise ValueError('snapshot or proposal cannot declare a resolution')
        if self.event_kind == 'correction' and not self.target_event_id:
            raise ValueError('correction requires target')
        return self


def workflow() -> dict:
    return {
        'contract': 'opm-charter-history/1', 'executor': 'caller_llm',
        'missing_document_blocks_flow': False, 'current_charter_verified': False,
        'steps': [
            '정기보고서 본문의 정관 이력과 첨부 목록을 읽고 전문/발췌 및 개정일·부칙을 확인한다.',
            '기준점 이후 정기·임시주총의 소집공고·결과·정정을 마감일까지 연결한다. 분·반기 첨부 존재는 가정하지 않는다.',
            '제안·가결/부결/철회·시행을 구분한다. 결과의 회차와 조항을 원문으로 연결한다.',
            '시행일·조건·현재 정관의 완전성이 불명확하면 해당 범위만 알리고 다른 판단을 계속한다.',
        ],
        'rules': [
            '기준점·제안 사건에는 대상 연결을 넣지 않고 결의 결과는 미확인으로 둔다. 기준점 비교는 별도 맥락 사실로 설명한다.',
            'target_event_id는 결의·정정의 대상 사건 연결이다. 결의는 proposal/correction을, 정정은 정정 대상 사건을 연결한다.',
            '공시 제목은 원문 탐색 힌트이며 정관 변경이나 가결의 증거가 아니다.',
            '보고서 기준일·공개일·결의일·개정일·시행일은 서로 대체하지 않는다.',
            '이번 주총의 사후 결과·사후 정정으로 마감 당시 정관을 재구성하지 않는다.',
            '정정은 대상 조항·사건을 연결하며 원본 전체를 접수 순서만으로 폐기하지 않는다.',
            'OCR 숫자·이하/이내·부정어·부칙의 불확실 구간은 원본 이미지로 재독한다.',
        ],
    }


def invalid_events(facts: dict, sources: dict, cutoff: str) -> set[str]:
    """Check date and graph contracts after ordinary citation validation."""
    events = {key: fact for key, fact in facts.items() if fact['kind'] == 'charter_event'}
    invalid = set()
    for key, fact in events.items():
        data = fact['data']; event_day = (data.get('event_date') or '').replace('-', '')
        publications = [sources[ref['source_id']].get('published', '').replace('-', '')
                        for ref in fact['evidence_refs']]
        if event_day and (event_day > cutoff or not any(p >= event_day for p in publications)):
            invalid.add(key)
        target = data.get('target_event_id')
        if data['event_kind'] == 'correction' and not target:
            invalid.add(key)
        seen = {key}
        while target:
            if target in seen or target not in events:
                invalid.add(key); break
            seen.add(target)
            parent = events[target]['data']
            # Linking another clause/agenda or reversing known chronology cannot
            # silently make a coherent history. Unknown dates remain unknown.
            if (not set(data['clause_ids']) <= set(parent['clause_ids'])
                or not set(fact['agenda_ids']) <= set(events[target]['agenda_ids'])
                or (data.get('event_date') and parent.get('event_date')
                    and data['event_date'] < parent['event_date'])):
                invalid.add(key)
            if data['event_kind'] == 'resolution' and parent['event_kind'] not in {'proposal', 'correction'}:
                invalid.add(key)
            target = parent.get('target_event_id')
    while True:
        dependent = {key for key, fact in events.items() if fact['data'].get('target_event_id') in invalid}
        if dependent <= invalid:
            return invalid
        invalid |= dependent


def summarize(facts: dict, cutoff: str) -> dict:
    rows = []
    for key, fact in facts.items():
        if fact['kind'] != 'charter_event':
            continue
        data = fact['data']; kind = data['event_kind']
        state = {'snapshot': 'snapshot_candidate', 'proposal': 'proposed',
                 'correction': 'correction_requires_reconciliation', 'resolution': 'resolution_unlinked'}[kind]
        if kind == 'resolution' and data.get('target_event_id'):
            if data['outcome'] in {'rejected', 'withdrawn'}:
                state = 'not_adopted'
            elif data['outcome'] == 'passed':
                state = ('adopted_effect_unknown' if not data.get('effective_date') else
                         'adopted_not_yet_effective' if data['effective_date'].replace('-', '') > cutoff else
                         'adopted_condition_unresolved' if data['conditions'] else 'adopted_effective_candidate')
            else:
                state = 'outcome_unknown'
        rows.append({'event_id': key, **data, 'state': state, 'agenda_ids': fact['agenda_ids'],
                     'evidence_refs': fact['evidence_refs']})
    return {'contract': 'opm-charter-history/1', 'as_of': cutoff, 'events': rows,
            'complete_history': False, 'current_charter_verified': False, 'human_reviewed': False,
            'hint': '원문 인용과 연결을 검사한 LLM 판독 이력이다. 유효 후보는 조항 의미·조건·누락 이력의 독립 검증 완료가 아니다.'}
