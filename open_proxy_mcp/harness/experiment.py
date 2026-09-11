"""고정한 사례와 근거로 실제 MCP 기반 모델 비교 실험을 구성한다."""
# 파일 접근은 실험 설정으로 결정하며 모델이 임의의 파일을 선택하도록 허용하지 않는다.
from __future__ import annotations

from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import datetime, timezone
import itertools
import os
import random
import time
from urllib.parse import urlsplit

import httpx

from .experiment_config import (CaseSpec, ExperimentError, ExperimentPlan, Ledger,
                                attempt_state, code_identity, digest)
from .experiment_model import ResponsesModel
from .runner import FinishAction, RunBudget, SubmitAction, VotingHarness, TOOL_NAME
from .structure_stages import StageBudget, StageExecutor
from .checkpoint import CheckpointError, FileCheckpoint
from .output_contracts import assessment_output_schema
from .transport import StreamableHTTPTransport
from open_proxy_mcp.services.election_structure import StructureAssessment


def protocol(method: str) -> str:
    return 'staged' if method == 'staged' else 'direct'


def task_for(payload: dict) -> dict:
    try:
        if payload.get('status') in {'error', 'ambiguous'}:
            raise ValueError
        entries = payload['data']['guideline_application']['structure_tasks']
        if len(entries) != 1:
            raise ValueError
        task = deepcopy(entries[0]['task'])
        task.pop('previous_assessment', None)
        if task.get('task_kind') != 'election_structure':
            raise ValueError
        return task
    except (ValueError, KeyError, TypeError):
        raise ExperimentError('structure_task_unavailable') from None


def core_identity(task: dict) -> str:
    context = {k: v for k, v in task['execution_context'].items() if k != 'structure_protocol'}
    return digest({k: task[k] for k in ('agendas', 'sources', 'policy', 'criteria',
                  'fact_data_schemas')} | {'execution_context': context})


def validate_packet(payload: dict, case: CaseSpec) -> dict:
    task = task_for(payload)
    binding = payload['data']['guideline_harness']
    context = task['execution_context']
    if (datetime.fromisoformat(context['cutoff_at']) != datetime.fromisoformat(case.cutoff_at)
        or binding.get('notice_rcept_no') != case.notice_rcept_no
        or not set(case.agenda_ids) <= {a['agenda_id'] for a in task['agendas']}
        or not context.get('engine_bundle_sha256') or not binding.get('source_manifest')):
        raise ExperimentError('case_binding_mismatch')
    # 날짜만 있는 근거도 서버가 보수적으로 정한 기준일보다 늦으면 사용하지 않는다.
    for source in task['sources']:
        published = source.get('published')
        if not isinstance(published, str) or len(published) != 8 or not published.isdigit():
            raise ExperimentError('unverified_source_publication_date')
        if published > context['effective_as_of']:
            raise ExperimentError('future_source_in_packet')
    return task


@asynccontextmanager
async def connect(plan: ExperimentPlan):
    endpoint = os.environ.get(plan.mcp_url_env, '')
    parsed = urlsplit(endpoint)
    if (parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.query
        or parsed.username or parsed.password or parsed.fragment
        or (parsed.scheme == 'http' and parsed.hostname not in {'127.0.0.1', 'localhost', '::1'})):
        raise ExperimentError('invalid_or_missing_mcp_endpoint')
    headers = {}
    for header, env in plan.mcp_header_env.items():
        value = os.environ.get(env)
        if not value:
            raise ExperimentError('mcp_auth_environment_missing')
        headers[header] = value
    async with httpx.AsyncClient(headers=headers, timeout=600, follow_redirects=False) as client:
        async with StreamableHTTPTransport(endpoint, http_client=client,
                                            read_timeout_seconds=600) as transport:
            yield transport


class CapturedTransport:
    def __init__(self, transport, case: CaseSpec, ledger: Ledger, prefix: str,
                 expected_task: dict | None = None):
        self.transport, self.case, self.ledger, self.prefix = transport, case, ledger, prefix
        self.expected_task = expected_task
        self.calls = len(ledger.files(prefix + '/mcp-*-request.json'))
        self.failure: str | None = None

    async def call_tool(self, name, arguments):
        if name != TOOL_NAME or 'guideline_research' in arguments:
            raise ExperimentError('experiment_tool_not_allowed')
        arguments = deepcopy(arguments)
        arguments['guideline_evidence_sources'] = [s.request() for s in self.case.evidence_sources]
        self.calls += 1
        p = f'{self.prefix}/mcp-{self.calls:03}'
        self.ledger.write(p + '-request.json', {'tool': name, 'arguments': arguments})
        try:
            payload = await self.transport.call_tool(name, arguments)
            self.ledger.write(p + '-response.json', payload)
            task = validate_packet(payload, self.case)
            if self.expected_task and digest(task) != digest(self.expected_task):
                raise ExperimentError('frozen_task_changed')
            return payload
        except ExperimentError as error:
            self.failure = str(error)
            raise
        except Exception:
            self.failure = 'mcp_transport_error'
            raise ExperimentError(self.failure) from None


def schedule(plan: ExperimentPlan) -> list[dict]:
    jobs = []
    for case, model, prompt, method, repetition in itertools.product(
            plan.cases, plan.models, plan.prompts, plan.methods, range(1, plan.repetitions + 1)):
        job = {'case': case.id, 'model': model.id, 'prompt': prompt.id,
               'method': method, 'repetition': repetition}
        jobs.append({**job, 'id': digest(job)[:24]})
    random.Random(plan.order_seed).shuffle(jobs)
    return jobs


def verify_manifest(plan: ExperimentPlan, ledger: Ledger) -> dict:
    manifest = ledger.read('manifest.json')
    if manifest['plan_sha256'] != digest(plan.model_dump()):
        raise ExperimentError('experiment_plan_changed')
    if manifest['code_sha256'] != digest(code_identity()):
        raise ExperimentError('experiment_code_changed')
    for path, expected in manifest['snapshots'].items():
        if digest(ledger.read(path)) != expected:
            raise ExperimentError('snapshot_integrity_failed')
    return manifest


async def prepare(plan: ExperimentPlan, ledger: Ledger) -> dict:
    if (ledger.root / 'manifest.json').exists():
        return verify_manifest(plan, ledger)
    ledger.write('plan.json', plan.model_dump())
    code = code_identity()
    ledger.write('code.json', code)
    snapshots = {}
    async with connect(plan) as transport:
        for case in plan.cases:
            common = None
            for proto in sorted({protocol(m) for m in plan.methods}):
                path = f'packets/{case.id}-{proto}.json'
        # 준비가 완료된 근거 패킷은 재개에 재사용하되 다른 내용으로 덮어쓰지 않는다.
                if (ledger.root / path).exists():
                    record = ledger.read(path)
                else:
                    args = case.request(plan.workflow, proto).arguments()
                    capture = CapturedTransport(transport, case, ledger, f'preparation/{case.id}-{proto}')
                    payload = await capture.call_tool(TOOL_NAME, args)
                    record = {'case_id': case.id, 'protocol': proto, 'response': payload}
                    ledger.write(path, record)
                task = validate_packet(record['response'], case)
                shared = core_identity(task)
                if common is not None and common != shared:
                    raise ExperimentError('cross_method_evidence_or_policy_mismatch')
                common = shared
                snapshots[path] = digest(record)
    manifest = {'contract': plan.contract, 'plan_sha256': digest(plan.model_dump()),
                'code_sha256': digest(code), 'snapshots': snapshots, 'schedule': schedule(plan),
                'created_at': datetime.now(timezone.utc).isoformat(),
                'human_reviewed': False, 'source_mode': plan.source_mode,
                'supported_scope': 'election_structure', 'ballots_submitted': 0}
    ledger.write('manifest.json', manifest)
    return manifest


def unresolved_by_agenda(assessment: dict, submitted: dict | None, agenda_ids: list[str]) -> dict:
    """검증에서 거부된 근거와 판단을 영향받는 안건별로 모은다."""
    scopes = {key: {key} for key in agenda_ids}
    for packet in (assessment, submitted or {}):
        for field in ('accepted_facts', 'accepted_gaps', 'accepted_findings',
                      'facts', 'gaps', 'findings', 'judgments'):
            for item in packet.get(field, []):
                refs = item.get('agenda_ids') or [item.get('agenda_id')]
                for id_field in ('fact_id', 'gap_id', 'finding_id', 'agenda_id'):
                    identifier = item.get(id_field)
                    if isinstance(identifier, str):
                        scopes.setdefault(identifier, set()).update(r for r in refs if isinstance(r, str))
    unresolved = {key: [] for key in agenda_ids}
    for rejected in assessment.get('rejected_items', []):
        # 영향 범위를 모르면 전체 대상에 미해결로 남겨 완료로 잘못 표시하지 않는다.
        affected = (scopes.get(rejected.get('item_id'), set()) & set(agenda_ids)) or set(agenda_ids)
        for key in affected & set(agenda_ids):
            unresolved[key].append(deepcopy(rejected))
    for key in (assessment.get('staged_review') or {}).get('failed_agenda_ids', []):
        if key in unresolved:
            unresolved[key].append({'item_id': key, 'code': 'staged_review_failed'})
    return unresolved


class ExperimentAdapter:
    """실험 질문과 모델을 공통 하네스에 연결한다."""
    # 프롬프트에서 모델 이름이 드러나지 않도록 모든 모델에 같은 평가자 이름을 쓴다.
    name, version = 'proxyvo', '1'
    checkpoint_resume_safe = True

    def __init__(self, model: ResponsesModel, case: CaseSpec, prompt: str, method: str):
        self.model, self.case, self.prompt, self.method = model, case, prompt, method
        self.events = []
        self.last_submission = None
        self.executor = StageExecutor(self.name, self.version, self._stage_call,
            budget=StageBudget(max_calls=model.budget.model_calls,
                attempts_per_stage=model.budget.stage_attempts,
                timeout_seconds=model.budget.call_timeout_seconds), allow_research=False)
        self.events = self.executor.events

    def _common(self):
        return {'question': self.prompt, 'target_agenda_ids': self.case.agenda_ids,
            'scope_instructions': 'Assess only these target agendas; preserve access to all related evidence. '
                'Leave other agendas unassessed; do not label them out_of_scope just because not requested. '
                'Only fixed packet excerpts are available in this controlled arm. Unread material stays not_read. '
                'Return the requested JSON object, not a tool action.'}

    async def _stage_call(self, context):
        # 실행 순서는 공통 실행기가 담당한다. 여기서는 실험의 질문과 모델만 연결한다.
        packet = {**self._common(), 'stage': context.stage, 'instructions': context.instructions,
            'task_id': context.task_id, 'packet': context.packet, 'output_schema': context.output_schema,
            'feedback': list(context.feedback), 'previous_output': context.previous_output}
        return await self.model.complete(packet, stage=context.stage)

    def restore_checkpoint_action(self, value):
        if value.get('action') == 'submit':
            self.last_submission = deepcopy(value['assessments'][0])

    async def assess(self, context):
        task = deepcopy(context.tasks[0])
        if task.get('task_kind') != 'election_structure':
            return FinishAction(reason='no_supported_assessment')
        previous = task.pop('previous_assessment', {})
        target = set(self.case.agenda_ids)
        unresolved = unresolved_by_agenda(previous, context.previous_submission,
                                          [a['agenda_id'] for a in task['agendas']])
        if (target <= {j['agenda_id'] for j in previous.get('judgments', [])}
            and not any(unresolved[key] for key in target)):
            return FinishAction(reason='sufficient_evidence')
        common = self._common()
        if self.method == 'staged':
            # 운영용 어댑터와 같은 실행기·재시도·부분 수용 로직을 그대로 사용한다.
            action = await self.executor.assess(replace(context, tasks=(task,)))
            if not isinstance(action, SubmitAction):
                return action
            draft = action.assessments[0]
        else:
            checkpoint = context.checkpoint.child('direct') if context.checkpoint else None
            async def complete(packet, stage):
                async def invoke(_):
                    return await self.model.complete(packet, stage=stage)
                return await checkpoint.call(stage, packet, invoke) if checkpoint else await invoke(None)
            packet = {**common, 'task': task, 'output_schema': assessment_output_schema(),
                      'feedback': list(context.feedback), 'previous_submission': context.previous_submission}
            draft = await complete(packet, stage='direct')
            if self.method == 'direct_with_review':
                for i in range(self.model.budget.direct_review_rounds):
                    draft = await complete({**packet, 'previous_submission': draft,
                        'review_instruction': 'Recheck your complete draft against originals and policy. '
                            'Preserve valid conclusions and counterevidence. Return the complete revised assessment.'},
                        stage=f'direct_review_{i+1}')
        assessment = StructureAssessment.model_validate(draft)
        if assessment.task_id != task['task_id']:
            raise ExperimentError('model_task_id_mismatch')
        if any(j.get('agenda_id') not in target for j in assessment.judgments):
            raise ExperimentError('assessment_outside_requested_scope')
        self.last_submission = assessment.model_dump()
        return SubmitAction(assessments=[self.last_submission])


def outcome_rows(payload: dict | None, task: dict, case: CaseSpec,
                 submitted: dict | None = None) -> list[dict]:
    entries = (((payload or {}).get('data') or {}).get('guideline_application') or {}).get('structure_tasks', [])
    assessment = entries[0].get('assessment', {}) if entries else {}
    accepted = {}
    for judgment in assessment.get('judgments', []):
        accepted.setdefault(judgment['agenda_id'], []).append(judgment)
    unresolved = unresolved_by_agenda(assessment, submitted, [a['agenda_id'] for a in task['agendas']])
    decisions = {(r.get('structure_trace') or {}).get('agenda_id'): r
                 for r in ((payload or {}).get('data') or {}).get('agenda_decisions', [])}
        # 일부 렌더러 버전은 agenda_id를 추적 정보 대신 안건 행에 둔다.
    decisions.update({r['agenda_id']: r for r in ((payload or {}).get('data') or {}).get('agenda_decisions', [])
                      if r.get('agenda_id')})
    titles = {a['agenda_id']: a['title'] for a in task['agendas']}
    rows = []
    for key in case.agenda_ids:
        judgments = accepted.get(key, [])
        conditional = len(judgments) > 1 or any(j.get('condition_id') for j in judgments)
        row = decisions.get(key, {})
        rows.append({'agenda_id': key, 'title': titles[key], 'assessment_applied': bool(judgments),
                     'target_complete': bool(judgments) and not unresolved[key] and bool(row.get('decision')),
                     'unresolved_rejections': unresolved[key], 'conditional': conditional,
                     'llm_recommendation': ('CONDITIONAL' if conditional else judgments[0].get('recommendation'))
                                           if judgments else None,
                     'mcp_recommendation': row.get('decision') if judgments else None,
                     'workflow_status': (row.get('voting_workflow') or {}).get('status') if judgments else None,
                     'judgments': deepcopy(judgments), 'human_reviewed': False})
    return rows


async def run_jobs(plan: ExperimentPlan, ledger: Ledger, *, limit: int | None = None,
                   retry_failed: bool = False, resume: bool = False, progress=print) -> dict:
    if retry_failed and resume:
        raise ExperimentError('conflicting_recovery_modes')
    manifest = verify_manifest(plan, ledger)
    executed = 0
    with (ledger.root / '.run-lock').open('a') as lock:
        import fcntl
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ExperimentError('experiment_already_running') from None
        async with httpx.AsyncClient(follow_redirects=False) as client:
            for job in manifest['schedule']:
                state = attempt_state(ledger, job['id'])
                if state['latest'] and (not retry_failed or state['status'] == 'target_complete'):
                    continue
                if state['status'] == 'interrupted' and not retry_failed and not resume:
                    progress(job['id'] + ' interrupted; use --resume or --retry-failed')
                    continue
                if limit is not None and executed >= limit:
                    break
                case = next(c for c in plan.cases if c.id == job['case'])
                spec = next(m for m in plan.models if m.id == job['model'])
                prompt = next(p.question for p in plan.prompts if p.id == job['prompt'])
                record = ledger.read(f'packets/{case.id}-{protocol(job["method"])}.json')
                task = task_for(record['response'])
                binding = record['response']['data']['guideline_harness']
                recovering = resume and state['status'] == 'interrupted'
                attempt = state['next_attempt'] - 1 if recovering else state['next_attempt']
                prefix = f'runs/{job["id"]}/attempt-{attempt:03}'
                if not recovering:
                    ledger.write(prefix + '/started.json', {'job': job, 'plan_sha256': manifest['plan_sha256'],
                        'started_at': datetime.now(timezone.utc).isoformat()})
                # 재개는 같은 시도, retry-failed는 새 시도다. 실험 표본 수를 부풀리지 않는다.
                checkpoint = FileCheckpoint(ledger.root / prefix / 'checkpoint',
                    identity={'plan_sha256': manifest['plan_sha256'], 'job': job})
                model = ResponsesModel(spec, plan.budget, ledger, prefix, client)
                adapter = ExperimentAdapter(model, case, prompt, job['method'])
                started_at = time.monotonic()
                result, failure = None, None
                try:
                    async with connect(plan) as transport:
                        captured = CapturedTransport(transport, case, ledger, prefix, task)
                        result = await VotingHarness(captured, adapter, RunBudget(
                            max_model_rounds=30, max_tool_calls=12,
                            max_attempts_per_task=plan.budget.submission_attempts,
                            model_timeout_seconds=plan.budget.outer_timeout_seconds), checkpoint=checkpoint).run(
                                case.request(plan.workflow, protocol(job['method']), binding))
                        failure = captured.failure
                except CheckpointError:
                    # 미완료 호출의 복구 여부를 사용자 선택 없이 실패 재시도로 바꾸지 않는다.
                    raise
                except ExperimentError as error:
                    failure = str(error)
                except Exception:
                    failure = 'experiment_execution_error'
                rows = outcome_rows(result.payload if result else None, task, case, adapter.last_submission)
                complete = all(r['target_complete'] for r in rows)
                status = 'target_complete' if complete and not failure else 'incomplete'
                value = {'job': job, 'status': status, 'error_code': failure,
                         'harness_result': asdict(result) if result else None, 'rows': rows,
                         'stage_events': adapter.events, 'model_calls': model.calls,
                         'resumed': recovering, 'checkpoint': prefix + '/checkpoint',
                         'http_attempts': model.http_attempts,
                         'elapsed_seconds': time.monotonic() - started_at,
                         'elapsed_scope': 'current_process_invocation',
                         'meeting_rows': len(task['agendas']), 'target_rows': len(rows),
                         'unrequested_rows': len(task['agendas']) - len(rows),
                         'human_reviewed': False, 'ballots_submitted': 0,
                         'semantic_accuracy_certified': False}
                ledger.write(prefix + '/result.json', value)
                executed += 1
                progress(f'{job["id"]} {job["model"]} {job["method"]}: {status}')
    return {'executed': executed, 'scheduled': len(manifest['schedule'])}
