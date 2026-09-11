"""Request-scoped voting preferences and per-agenda preparation routing.

This module does not submit ballots or attest that an LLM judgment is correct.
Policy stance, recommendation, and a user's manual-review choice are separate.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr


UnitInterval = Annotated[StrictInt | StrictFloat, Field(ge=0, le=1, allow_inf_nan=False)]


class WorkflowSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stance: UnitInterval = 0.5
    automation: UnitInterval = 0.5
    firmness: UnitInterval = 0.75
    manual_agenda_titles: Annotated[
        list[Annotated[StrictStr, Field(min_length=1, max_length=6000)]],
        Field(max_length=100),
    ] = Field(default_factory=list)
    attendance_min_pct: Annotated[StrictInt | StrictFloat, Field(ge=50, le=100)] | None = None
    manual_agenda_ids: Annotated[list[Annotated[StrictStr, Field(min_length=1, max_length=200)]], Field(max_length=100)] = Field(default_factory=list)


def resolve_workflow_settings(value: dict | None = None) -> dict:
    """Validate a complete request configuration without echoing invalid input."""
    try:
        settings = WorkflowSettings.model_validate({} if value is None else value)
        titles = settings.manual_agenda_titles
        if any(not title.strip() for title in titles) or len(set(titles)) != len(titles):
            raise ValueError("invalid manual agenda selection")
        if any(not value.strip() for value in settings.manual_agenda_ids) or len(set(settings.manual_agenda_ids)) != len(settings.manual_agenda_ids):
            raise ValueError('invalid manual agenda selection')
        if settings.automation == 1 and (titles or settings.manual_agenda_ids):
            raise ValueError('full automation conflicts with manual selection')
    except Exception:
        raise ValueError("guideline_workflow: invalid settings") from None
    return settings.model_dump()


def decision_guidance(settings: dict | None = None) -> dict:
    """Continuous caller-LLM preference, never a probability or vote rewrite."""
    value = resolve_workflow_settings(settings)['firmness']
    return {'value': value, 'default': 0.75, 'executor': 'caller_llm',
            'anchors': {'0': '결론에 영향을 주는 애매함은 해당 안건 검토.',
                        '0.5': '불확실성이 권고를 뒤집는지 평가하고 영향이 작으면 판단.',
                        '1': '확인한 근거로 찬성·반대를 최대한 판단하고 누락·가정·영향을 공개.'},
            'instructions': ('값이 클수록 근거 있는 권고를 내리는 방향으로 판단한다. 중간값은 연속적인 선호이며 '
                '확률·temperature·찬성 성향이 아니다. 자료 누락 자체를 불이익이나 전체 중단으로 만들지 않는다. '
                '정원 상한은 충원 목표가 아니다. 상한 이내라는 이유만으로 미달·위반을 추정하지 않는다. '
                '누락의 결정 영향을 사실에 연결해 설명하며 핵심 충돌·근거 전무는 1에서도 검토한다. '
                '미독·충돌을 미공개로 바꾸거나 사실을 가정으로 채우지 않는다. 사후 정보 금지·인용·'
                '고정 수치 기준·선행 조건·개인 평가·자동화 설정을 우회하지 않는다.')}


def stance_guidance(settings: dict | None = None) -> dict:
    """Intervention preference and evidence questions for any caller model."""
    return {
        'value': resolve_workflow_settings(settings)['stance'], 'default': 0.5,
        'executor': 'caller_llm',
        'anchors': {
            '0': '매우 수동적. 임박한 금전 손실이 입증되지 않으면 찬성을 기본 성향으로 삼는다.',
            '0.5': '주주 이익·책임성과 회사 설명·반증을 함께 평가한다.',
            '1': ('매우 적극적. 애매한 정관 조항·표현, 재무 여력 대비 미흡한 주주환원, '
                  '이사·경영진 성과에 대한 근거 있는 불만은 검토 대상으로 삼는다. '
                  '중대한 위반·결격, 매우 부진한 성과의 장기 지속과 책임이 확인되면 반대한다.'),
        },
        'evidence_questions': {
            'monetary_loss': '손실의 금액·발생 시기·인과관계·영향 주주·반증을 원문으로 확인한다.',
            'shareholder_rights': '정관 전후 문구·결정 재량·실제 임기·교체 기회·적용 대상을 비교한다.',
            'capital_return': ('현금흐름·순현금/부채·투자와 상환 필요·배당·자사주 소각·환원 약속 이행을 '
                               '동일 기간·연결/별도 기준으로 대조한다. 낮은 배당률 하나로 반대하지 않는다.'),
            'management_performance': ('다년 성과·비교 가능한 동종업계·산업 사이클·재임 기간·담당 역할·'
                                       '개선 계획 이행과 개인 책임을 확인한다. 단년 부진이나 회사 부진을 개인 결격으로 바꾸지 않는다.'),
            'violations': '혐의·기소·판결·확정·불복/취소와 후보 귀속을 나누고, 기사 논조는 근거로 삼지 않는다.',
        },
        'instructions': ('중간값은 개입 성향이며 확률이나 수치 반대 기준이 아니다. 성향만으로 위반을 만들지 않는다. '
            '고정된 결격·수치 기준과 확인된 중대한 반대 사유는 낮은 값에서도 유지한다. 높은 값에서는 '
            '주주에게 불리할 수 있는 문구의 불명확성을 firmness가 높다는 이유만으로 사소하다고 축소하지 않는다. '
            '누락·미공개는 해당 기준만 제외하고 알린다. 미독·충돌은 별도로 남긴다. '
            '판단에는 선택 성향, 원문 사실, 반증과 결정 영향을 연결한다. 시점 제한은 모든 값에서 동일하다.'),
        'scope': {
            'validated_assessments': ['appointment', 'independence', 'attendance', 'election_structure'],
            'research_only': ['capital_return_adequacy', 'management_performance_accountability'],
            'limitation': ('주주환원 적정성과 경영진 장기 성과는 조사 지침이다. 현재 v2 전용 평가 계약은 '
                           '없으며 기존 엔진 결과와 구분한다. 해당 내용을 평가 완료·결격으로 주장하지 않는다.'),
        },
    }


def automation_guidance(settings: dict | None = None) -> dict:
    effective = resolve_workflow_settings(settings)
    return {
        'value': effective['automation'], 'default': 0.5,
        'full_auto_fallback': 'FOR' if effective['stance'] < 0.5 else 'AGAINST',
        'instructions': ('0은 모두 사람 검토, 0 초과 0.5 이하는 찬성만 자동 처리 준비, '
            '0.5 초과 1 미만은 찬반 자동 처리 준비와 미결 안건 사람 검토다. '
            '1은 가능한 범위에서 근거 있는 찬반 결론을 우선한다. 평가 계약을 만족하지 못하는 '
            '미결 사유를 숨기지 말고 제출한다. 모든 평가를 합친 뒤에도 미결이면 서버가 '
            'stance 0.5 미만은 찬성, 이상은 반대로 최종 기본 정책을 적용한다. '
            '이는 사실 판단이 아니다. 원래 판단·불확실성을 보존한다. '
            '평가 미제출·인용 오류·시점 오류는 처리 완료로 바꾸지 않는다. 실제 투표 전송은 없다.'),
    }


def apply_workflow_policy(policy: dict, settings: dict | None = None) -> dict:
    """Bind the effective request preferences into the policy/task digest.

    Stance does not invent a different numerical threshold. Only
    an explicit attendance_min_pct changes the existing policy parameter.
    """
    effective = resolve_workflow_settings(settings)
    result = deepcopy(policy)
    result["workflow_settings"] = effective
    result["decision_guidance"] = decision_guidance(effective)
    result["stance_guidance"] = stance_guidance(effective)
    result["automation_guidance"] = automation_guidance(effective)
    if effective["attendance_min_pct"] is not None:
        parameter = result.get("parameters", {}).get("attendance_min_pct")
        if not isinstance(parameter, dict):
            raise ValueError("guideline_workflow: attendance parameter is unavailable")
        parameter["default"] = effective["attendance_min_pct"]
        # The evaluator prefers value over default if a compiled policy has it.
        if "value" in parameter:
            parameter["value"] = effective["attendance_min_pct"]
    return result


def _has_material_conflict(trace: dict) -> bool:
    return bool(trace.get("material_conflicts") or trace.get("unresolved_conflicts")
                or trace.get("critical_issues"))


def route_workflow(rows: list[dict], settings: dict | None = None) -> dict:
    """Annotate final recommendations without changing a decision or blocking peers.

    ``ready_for_auto`` means an accepted recommendation is ready for an external
    execution workflow. There is no ballot transmission or human approval here.
    Exact manual titles apply to in-scope pilot agendas only; unmatched titles
    are reported so a misspelling cannot silently disappear.
    """
    effective = resolve_workflow_settings(settings)
    manual_titles = set(effective["manual_agenda_titles"])
    matched_titles: set[str] = set()
    counts = {state: 0 for state in (
        "ready_for_auto", "manual_review", "awaiting_assessment", "not_applicable")}
    for row in rows:
        trace = row.get("guideline_trace") or {}
        assessment = trace.get("llm_assessment")
        title = row.get("agenda_title") or ""
        in_scope = (trace.get("mode") == "pilot"
                    and trace.get("status") != "not_applicable"
                    and isinstance(assessment, dict))
        if not in_scope:
            status, reason = "not_applicable", "현재 v2 후보 평가 범위 밖의 안건입니다."
        elif row.get("decision") == "NO_VOTE":
            status, reason = "not_applicable", "표결 대상이 아닌 안건입니다."
        elif effective["automation"] == 0 or title in manual_titles:
            if title in manual_titles:
                matched_titles.add(title)
            status, reason = "manual_review", "사용자가 수동 검토 대상으로 지정했습니다."
        elif assessment.get("status") == "pending":
            status, reason = "awaiting_assessment", "이 안건의 LLM 평가 제출을 기다립니다. 다른 안건은 계속 처리합니다."
        elif assessment.get("status") != "accepted_unreviewed":
            status, reason = "manual_review", "평가 입력이 수용되지 않아 이 안건의 재평가가 필요합니다."
        elif (_has_material_conflict(trace)
              or trace.get("post_constraint_adjusted")
              or trace.get("decision_effect") == "protected_baseline"
              or row.get("law_layer_id") is not None
              or (row.get("facts") or {}).get("election_method") == "집중투표"
              or (row.get("facts") or {}).get("cumulative_voting_threshold")
              or any(link.get("type") in {"contested", "depends_on", "conditional_on"}
                     for link in row.get("agenda_relation_links") or [])
              or row.get("agenda_relation_type") in {"procedural", "alternative", "conditional", "withdrawn"}):
            status, reason = "manual_review", "후보 권고는 유지하며 근거 충돌·표결 제약 또는 집중투표 배분을 별도 검토합니다."
        elif row.get("decision") not in {"FOR", "AGAINST"}:
            status, reason = "manual_review", "현재 권고가 찬성·반대로 확정되지 않았습니다."
        elif effective["automation"] <= 0.5 and row.get("decision") != "FOR":
            status, reason = "manual_review", "일부 수동 모드에서 반대 권고를 검토 대상으로 분리했습니다."
        else:
            status, reason = "ready_for_auto", "현재 범위의 LLM 권고가 자동 처리 준비 상태입니다. 사람 미검토."
        counts[status] += 1
        row["voting_workflow"] = {
            "status": status, "reason": reason,
            "human_reviewed": False, "ballot_submitted": False,
            "recommendation": row.get("decision"),
        }
    return {
        "settings": effective, "counts": counts,
        "unmatched_manual_agenda_titles": sorted(manual_titles - matched_titles),
        "ballot_submission_supported": False, "ballots_submitted": 0,
        "human_reviewed": False,
        "scope": "v2 파일럿 평가 범위의 안건별 권고 준비·수동 검토 분기. 실제 투표 전송 기능 없음.",
    }


def _execution_constraints(row: dict) -> list[str]:
    trace = row.get('guideline_trace') or {}
    structure = row.get('structure_trace') or {}
    constraints = []
    if _has_material_conflict(trace):
        constraints.append('evidence_conflict')
    if row.get('law_layer_id') is not None:
        constraints.append('law_layer')
    if trace.get('post_constraint_adjusted') or trace.get('decision_effect') == 'protected_baseline':
        constraints.append('protected_baseline')
    if ((row.get('facts') or {}).get('election_method') == '집중투표'
        or (row.get('facts') or {}).get('cumulative_voting_threshold')
        or any(f['kind'] == 'election_pool' and 'cumulative' in f['data']['method']
               for f in structure.get('accepted_facts', []))):
        constraints.append('vote_allocation')
    if (any(j.get('condition_id') for j in structure.get('judgments', []))
        or any(f['kind'] == 'ballot_scope' and (
            f['data'].get('choice_group') or f['data'].get('row_kind') == 'conditional')
            for f in structure.get('accepted_facts', []))
        or any(link.get('type') in {'contested', 'depends_on', 'conditional_on'}
               for link in row.get('agenda_relation_links') or [])
        or row.get('agenda_relation_type') in {'procedural', 'alternative', 'conditional', 'withdrawn'}):
        constraints.append('conditional_ballot')
    return constraints


def finalize_workflow(payload: dict, settings: dict | None = None) -> dict:
    """Route once after ALL assessments/constraints, including structure results.

    A full-auto fallback is a user's default voting instruction, never an accepted
    evidentiary judgment. Pending or invalid model submissions cannot trigger it.
    Baseline-only agendas keep their scope label; a fallback does not expand the
    LLM assessment contract. Execution dependencies stay separate from direction.
    """
    data = payload.get('data') or {}
    application = data.get('guideline_application') or {}
    if payload.get('status') == 'error' or application.get('mode') != 'pilot':
        return payload
    effective = resolve_workflow_settings(settings)
    mode = effective['automation']
    manual_titles, manual_ids = set(effective['manual_agenda_titles']), set(effective['manual_agenda_ids'])
    matched_titles, matched_ids = set(), set()
    structure_states = {}
    for entry in application.get('structure_tasks', []):
        result = entry['assessment']
        completed = {j['agenda_id'] for j in result['judgments']} | set(result.get('out_of_scope_agenda_ids', []))
        rejected = {r['item_id'] for r in result.get('rejected_items', [])}
        for agenda in entry['task']['agendas']:
            key = agenda['agenda_id']
            if key not in completed:
                structure_states[key] = 'rejected' if key in rejected or result['status'] == 'rejected' else 'pending'
    counts: dict[str, int] = {}
    fallback_count = 0
    for row in data.get('agenda_decisions', []):
        # Idempotent for formatters and clients that reuse an in-memory payload.
        previous = row.pop('automation_trace', None)
        if previous:
            row['decision'], row['reason'] = previous['assessment_recommendation'], previous['assessment_reason']
        decision = row.get('decision')
        candidate_state = ((row.get('guideline_trace') or {}).get('llm_assessment') or {}).get('status')
        states = [candidate_state, structure_states.get(row.get('agenda_id'))]
        constraints = _execution_constraints(row)
        selected = row.get('agenda_title') in manual_titles or row.get('agenda_id') in manual_ids
        if decision == 'NO_VOTE':
            status, reason = 'not_applicable', '표결 대상이 아닌 안건입니다.'
        elif mode == 0 or selected:
            if row.get('agenda_title') in manual_titles:
                matched_titles.add(row['agenda_title'])
            if row.get('agenda_id') in manual_ids:
                matched_ids.add(row['agenda_id'])
            status, reason = 'manual_review', '사용자가 수동 검토 대상으로 지정했습니다.'
        elif any(state not in {None, 'accepted_unreviewed', 'pending'} for state in states):
            status = 'assessment_error' if mode == 1 else 'manual_review'
            reason = '평가 입력 오류를 수정해 재제출해야 합니다. 기본 정책으로 평가 오류를 숨기지 않습니다.'
        elif 'pending' in states:
            status, reason = 'awaiting_assessment', 'LLM 평가가 진행 중입니다. 아직 최종 권고가 아닙니다.'
        elif mode == 1:
            fallback = decision not in {'FOR', 'AGAINST'}
            final = ('FOR' if effective['stance'] < 0.5 else 'AGAINST') if fallback else decision
            row['automation_trace'] = {
                'assessment_recommendation': decision, 'assessment_reason': row.get('reason'),
                'final_recommendation': final, 'fallback_applied': fallback,
                'basis': 'user_policy_fallback' if fallback else 'assessment',
                'assessment_scope': ('caller_llm' if candidate_state or row.get('structure_trace') else 'baseline_engine'),
                'stance': effective['stance'], 'automation': mode,
                'execution_constraints': constraints, 'human_reviewed': False,
            }
            row['decision'] = final
            if fallback:
                fallback_count += 1
                row['reason'] = (str(row.get('reason') or '') + ' / 자동화 기본 정책 적용: '
                    + ('stance < 0.5이므로 찬성.' if final == 'FOR' else 'stance ≥ 0.5이므로 반대.')
                    + ' 사실 판정이 아니며 원래 미결 사유와 제외한 기준을 보존합니다.')
            status = 'execution_pending' if constraints else 'ready_for_auto'
            reason = ('찬반 방향은 결정됐습니다. 표 배분·조건·기존 제약은 실행 단계에서 별도로 해결해야 합니다.'
                      if constraints else '최종 찬반 권고가 자동 처리 준비 상태입니다. 사람 미검토.')
        elif constraints or decision not in {'FOR', 'AGAINST'} or (mode <= 0.5 and decision != 'FOR'):
            status, reason = 'manual_review', '선택한 자동화 수준에 따라 미결·반대 또는 실행 제약을 사람이 검토합니다.'
        else:
            status, reason = 'ready_for_auto', '현재 권고가 자동 처리 준비 상태입니다. 사람 미검토.'
        counts[status] = counts.get(status, 0) + 1
        row['voting_workflow'] = {'status': status, 'reason': reason, 'recommendation': row.get('decision'),
            'execution_constraints': constraints, 'human_reviewed': False, 'ballot_submitted': False}
    application['voting_workflow'] = {
        'settings': effective, 'counts': counts, 'fallback_count': fallback_count,
        'unmatched_manual_agenda_titles': sorted(manual_titles - matched_titles),
        'unmatched_manual_agenda_ids': sorted(manual_ids - matched_ids),
        'ballot_submission_supported': False, 'ballots_submitted': 0, 'human_reviewed': False,
        'scope': '상정 안건의 최종 권고 준비. 기존 엔진·LLM 평가·사용자 기본 정책의 출처를 구분하며 실제 투표 전송은 없음.',
    }
    return payload
