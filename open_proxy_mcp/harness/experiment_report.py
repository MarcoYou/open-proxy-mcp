"""저장한 결과로 비교표를 만들고 선택적으로 원문 기반 LLM 평가를 수행한다."""
from __future__ import annotations

from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime, timezone
import html
import json
import re

import httpx
from pydantic import Field
from typing import Literal

from .experiment_config import CaseSpec, ExperimentError, ExperimentPlan, Ledger, Strict, attempt_state, digest
from .experiment_model import ResponsesModel
from .experiment import task_for, protocol, verify_manifest

CHECKS = ('subject_period', 'source_entailment', 'policy_application', 'scope_gaps', 'ballot_conditions')
RUBRIC = {
    'version': 'opm-proxyvo-grader/1',
    'instructions': 'Compare the answer with originals and policy. Treat permitted discretion as such, '
        'not an error just because you prefer another vote. Check subjects, publication and effective dates, '
        'proposal versus effective text, evidence entailment, contrary evidence, scoped gaps and ballot conditions. '
        'Do not infer accuracy from JSON acceptance. Do not infer quality from fluency or length. '
        'For every requested agenda return all check IDs exactly once. Supported/issue require an exact source quote. '
        'Use not_assessable for unresolved meaning, not a guessed score. This is human-unreviewed LLM grading.',
    'check_ids': list(CHECKS),
}


class Citation(Strict):
    source_id: str
    quote: str = Field(min_length=1)


class Check(Strict):
    agenda_id: str
    check_id: Literal['subject_period', 'source_entailment', 'policy_application', 'scope_gaps', 'ballot_conditions']
    outcome: Literal['supported', 'issue', 'not_assessable']
    rationale: str = Field(min_length=1)
    evidence_refs: list[Citation] = Field(default_factory=list)


class Grade(Strict):
    checks: list[Check]


def validate_grade(raw: dict, target_ids: list[str], sources: list[dict]) -> dict:
    grade = Grade.model_validate(raw)
    required = {(a, c) for a in target_ids for c in CHECKS}
    if len(grade.checks) != len(required) or {(c.agenda_id, c.check_id) for c in grade.checks} != required:
        raise ExperimentError('invalid_grader_coverage')
    norm = lambda s: re.sub(r'\s+', ' ', s).strip()
    originals = {s['source_id']: [norm(x) for x in s.get('excerpts', [])] for s in sources}
    for check in grade.checks:
        if check.outcome != 'not_assessable' and not check.evidence_refs:
            raise ExperimentError('unsubstantiated_grader_claim')
        for ref in check.evidence_refs:
            if not norm(ref.quote) or not any(norm(ref.quote) in text for text in originals.get(ref.source_id, [])):
                raise ExperimentError('invalid_grader_citation')
    return grade.model_dump()


def final_submission(ledger: Ledger, result_path) -> dict | None:
    for path in reversed(sorted(result_path.parent.glob('mcp-*-request.json'))):
        args = ledger.read(str(path.relative_to(ledger.root)))['arguments']
        drafts = (args.get('guideline_structure') or {}).get('assessments', [])
        if drafts:
            # 모델과 실행 방식을 가늠할 ID·단계명·QA 필드를 빼고 판단 내용과 근거는 보존한다.
            return {k: deepcopy(drafts[0].get(k, [])) for k in ('facts', 'gaps', 'findings', 'judgments')}
    return None


def grading_packet(task: dict, case: CaseSpec, answer: dict) -> dict:
    """채점 모델에 전달할 사례·근거·답변을 구성한다."""
    # 평가 대상과 기준 시점은 답변이 주장하는 값 대신 미리 고정한 사례에서 가져온다.
    context = task['execution_context']
    return {'rubric': RUBRIC, 'target_agenda_ids': list(case.agenda_ids),
            'case': case.model_dump(include={'company', 'year', 'meeting_type', 'cutoff_at', 'notice_rcept_no'}),
            'agendas': deepcopy(task['agendas']),
            'execution_context': {key: deepcopy(context[key]) for key in
                ('cutoff_at', 'effective_as_of', 'meeting_pin', 'policy_application') if key in context},
            'policy': deepcopy(task['policy']), 'criteria': deepcopy(task['criteria']),
            'sources': deepcopy(task['sources']), 'answer': deepcopy(answer),
            'output_schema': Grade.model_json_schema()}


async def score(plan: ExperimentPlan, ledger: Ledger) -> dict:
    verify_manifest(plan, ledger)
    if not plan.judges:
        return {'graded': 0, 'status': 'no_predeclared_judges', 'semantic_accuracy_certified': False}
    count = 0
    async with httpx.AsyncClient(follow_redirects=False) as client:
        for path in ledger.files('runs/*/attempt-*/result.json'):
            result = ledger.read(str(path.relative_to(ledger.root)))
            job = result['job']
            answer = final_submission(ledger, path)
            if not answer:
                continue
            task = task_for(ledger.read(f'packets/{job["case"]}-{protocol(job["method"])}.json')['response'])
            case = next(c for c in plan.cases if c.id == job['case'])
            ids = case.agenda_ids
            packet = grading_packet(task, case, answer)
            for spec in plan.judges:
                prefix = 'grades/' + digest({'input': packet, 'judge': spec.model_dump(),
                                            'result_path': str(path.relative_to(ledger.root))})[:32]
                if (ledger.root / (prefix + '/grade.json')).exists():
                    continue
                if (ledger.root / (prefix + '/started.json')).exists():
                    continue  # 중단된 유료 채점을 자동으로 반복하지 않는다.
                ledger.write(prefix + '/started.json', {'result_path': str(path.relative_to(ledger.root)),
                    'judge': spec.model_dump(), 'input_sha256': digest(packet), 'rubric': RUBRIC['version']})
                model = ResponsesModel(spec, plan.budget, ledger, prefix, client)
                try:
                    raw = await model.complete(packet, stage='external_grade')
                    grade = {'status': 'graded_unreviewed', 'grade': validate_grade(raw, ids, task['sources'])}
                except Exception:
                    grade = {'status': 'grader_failed', 'grade': None}
                ledger.write(prefix + '/grade.json', {**grade, 'judge': spec.id,
                    'result_path': str(path.relative_to(ledger.root)), 'human_reviewed': False,
                    'semantic_accuracy_certified': False})
                count += 1
    return {'graded': count, 'semantic_accuracy_certified': False}


def report(ledger: Ledger) -> dict:
    manifest = ledger.read('manifest.json')
    plan = ledger.read('plan.json')
    records, trials, issues = [], [], []
    target_counts = {case['id']: len(case['agenda_ids']) for case in plan['cases']}
    for job in manifest['schedule']:
        state = attempt_state(ledger, job['id'])
        paths = state['result_paths']
        attempts = [ledger.read(str(p.relative_to(ledger.root))) for p in paths]
        record = {'job': job, 'status': state['status'], 'attempts': state['count'],
                  'first_status': state['first_status'], 'first': state['first'], 'latest': state['latest']}
        records.append(record)
        for p, result in zip(paths, attempts):
            trials.append({'path': str(p.relative_to(ledger.root)), **result})
            if result['status'] != 'target_complete':
                codes = [e.get('code') for e in (result.get('harness_result') or {}).get('events', [])]
                issues.append({'job_id': job['id'], 'attempt': p.parent.name,
                    'code': result.get('error_code') or (result.get('harness_result') or {}).get('stop_reason'),
                    'observed_event_codes': codes,
                    'proposal': '원문·계약·모델 응답·제출을 대조하고 원인을 분류한다. 정책과 프롬프트는 자동 수정하지 않는다.'})
    groups = defaultdict(list)
    for r in records:
        j = r['job']; groups[(j['model'], j['method'], j['prompt'])].append(r)
    summary = []
    for key, values in sorted(groups.items()):
        votes, initial_votes = Counter(), Counter()
        variation = defaultdict(list)
        for value in values:
            for label, target in [('first', initial_votes), ('latest', votes)]:
                if value[label] is None and value['attempts']:
                    target['UNASSESSED'] += target_counts[value['job']['case']]
                for row in (value[label] or {}).get('rows', []):
                    vote = row['mcp_recommendation'] if row['target_complete'] else (
                        'PARTIAL' if row['assessment_applied'] else 'UNASSESSED')
                    target[vote] += 1
            latest = value['latest']
            if latest and value['status'] != 'interrupted':
                variation[value['job']['case']].append(tuple((r['agenda_id'], r['mcp_recommendation'],
                    r['target_complete'], tuple(sorted((bool(j.get('condition_id')), j['recommendation'])
                        for j in r.get('judgments', [])))) for r in latest['rows']))
        summary.append({'model': key[0], 'method': key[1], 'prompt': key[2], 'scheduled': len(values),
            'statuses': dict(Counter(v['status'] for v in values)),
            'first_attempt_statuses': dict(Counter(v['first_status'] for v in values)),
            'first_attempt_votes': dict(initial_votes), 'latest_attempt_votes': dict(votes),
            'repeat_variation': {case: {'observations': len(rows), 'distinct_vote_patterns': len(set(rows))}
                                 for case, rows in variation.items()}})
    grades = [ledger.read(str(p.relative_to(ledger.root))) for p in ledger.files('grades/*/grade.json')]
    result = {'contract': manifest['contract'], 'plan_sha256': manifest['plan_sha256'],
              'generated_at': datetime.now(timezone.utc).isoformat(), 'summary': summary,
              'records': records, 'attempts': trials, 'grades': grades, 'improvement_proposals': issues,
              'human_reviewed': False, 'semantic_accuracy_certified': False,
              'limitations': ['정관·선출 구조의 지정 안건만 지원', '고정 원문 묶음; 원천 자동 탐색 비교는 미구현',
                  '반복 안정성과 MCP 수용은 정답률이 아님', 'LLM 채점자별 판정은 사람 미검토',
                  '미시작·중단·미평가·일부 수용을 완료에서 제외; PARTIAL은 일부만 수용된 안건',
                  '재시도 결과와 첫 시도는 별도 집계; 반복 변화는 완료 상태·최종 권고·조건부 찬반 구성만 비교',
                  '조건 문구와 근거의 의미 차이는 반복 변화 수치로 측정하지 않으며 원문과 별도 대조해야 함',
                  '편향 변형·AB/BA·독립 calibration은 별도 설계 단계']}
    revision = digest({k: v for k, v in result.items() if k != 'generated_at'})[:20]
    folder = ledger.root / 'reports' / revision
    if not folder.exists():
        ledger.write(f'reports/{revision}/report.json', result)
        ledger.write(f'reports/{revision}/improvements.json', issues)
    h = lambda x: html.escape(str(x))
    rows = ''.join('<tr>' + ''.join(f'<td>{h(value)}</td>' for value in [s['model'],s['method'],s['prompt'],
        s['scheduled'],s['statuses'],s['first_attempt_votes'],s['latest_attempt_votes'],s['repeat_variation']]) + '</tr>'
        for s in summary)
    detail = []
    for r in records:
        j = r['job']
        detail.append(f'<h3>{h(j["case"])} · {h(j["model"])} · {h(j["method"])} · 반복 {j["repetition"]}</h3>'
                      f'<p>{h(r["status"])} · 시도 {r["attempts"]}</p>')
        for row in (r['latest'] or {}).get('rows', []):
            detail.append(f'<p><strong>{h(row["title"])}</strong>: {h(row["mcp_recommendation"] or "미평가")} '
                          f'· {h(row["workflow_status"] or "미적용")} '
                          f'· {"제출 수용 완료" if row["target_complete"] else "미완료/일부 수용"}</p>')
            for branch in row.get('judgments', []):
                detail.append(f'<p>조건 {h(branch.get("condition_id") or "없음")}: '
                              f'{h(branch["recommendation"])} · {h(branch.get("rationale", ""))}</p>')
    doc = f'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ProxyVO 실험 비교</title><style>body{{font:16px/1.7 sans-serif;max-width:1280px;margin:40px auto;padding:24px;color:#17372e}}table{{border-collapse:collapse;min-width:1000px}}td,th{{padding:10px;border:1px solid #cbd5cb;text-align:left;vertical-align:top}}.scroll{{overflow:auto}}h2{{margin-top:40px}}a{{color:#14654a}}</style>
<h1>ProxyVO · {h(plan['name'])}</h1><p><strong>사람 미검토 · 정답률 인증 아님 · 실제 투표 전송 0</strong></p>
<p>설정 {h(manifest['plan_sha256'])} · <a href="report.json">실행별 JSON</a> · <a href="improvements.json">개선 제안</a></p>
<h2>모델·방법 비교</h2><div class="scroll"><table><tr><th>모델</th><th>방법</th><th>프롬프트</th><th>예정</th><th>실행 상태</th><th>첫 시도 찬반</th><th>최신 시도 찬반</th><th>반복 변화</th></tr>{rows}</table></div>
<h2>안건별 결과</h2>{''.join(detail)}<h2>외부 의미 채점</h2><p>{len(grades)}건. 채점자별 결과와 실패는 JSON에 보존한다. 다수결로 정답을 만들지 않는다.</p>
<h2>검증 범위</h2><ul>{''.join('<li>'+h(x)+'</li>' for x in result['limitations'])}</ul></html>'''
    (folder / 'index.html').write_text(doc)
    return {'report': str(folder / 'index.html'), 'scheduled': len(records),
            'statuses': dict(Counter(r['status'] for r in records))}
