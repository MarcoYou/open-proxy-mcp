"""명시적 실험 설정과 덮어쓰지 않는 비공개 결과 기록을 관리한다."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .runner import HarnessRequest, SourceRead
from .checkpoint import runtime_identity

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = 'opm-proxyvo-experiment/1'


class ExperimentError(ValueError):
    """고정된 로컬 코드로만 외부에 표시하는 실험 오류다."""
    # 원격 오류나 입력 원문을 출력하면 인증 정보·비공개 데이터가 노출될 수 있다.


def encoded(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value) -> str:
    return hashlib.sha256(encoded(value).encode()).hexdigest()


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class ModelSpec(Strict):
    id: str = Field(pattern=r'^[A-Za-z0-9_.-]{1,100}$')
    provider: Literal['openai_responses'] = 'openai_responses'
    key_env: str = Field(default='OPENAI_API_KEY', pattern=r'^[A-Z][A-Z0-9_]*$')
    effort: Literal['low', 'medium', 'high', 'xhigh', 'max'] = 'xhigh'
    expected_response_model: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2, allow_inf_nan=False)
    seed: int | None = None
    # 지원 여부는 실행 전 명시한다. API 오류를 보고 조용히 JSON mode로 낮추지 않는다.
    output_mode: Literal['json_schema', 'json_object'] = 'json_schema'


class Budget(Strict):
    model_calls: int = Field(default=16, ge=4, le=100)
    max_output_tokens: int = Field(default=24000, ge=128, le=128000)
    call_timeout_seconds: float = Field(default=120, gt=0, le=180, allow_inf_nan=False)
    outer_timeout_seconds: float = Field(default=600, gt=0, le=600, allow_inf_nan=False)
    stage_attempts: int = Field(default=2, ge=1, le=3)
    submission_attempts: int = Field(default=2, ge=1, le=3)
    http_retries: int = Field(default=1, ge=0, le=3)
    direct_review_rounds: int = Field(default=3, ge=1, le=6)


class PromptSpec(Strict):
    id: str = Field(pattern=r'^[a-z0-9_-]{1,60}$')
    question: str = Field(min_length=10, max_length=12000)


class CaseSpec(Strict):
    id: str = Field(pattern=r'^[a-z0-9_-]{1,80}$')
    company: str = Field(min_length=1, max_length=100)
    year: int = Field(ge=1900, le=9999, strict=True)
    meeting_type: Literal['annual', 'extraordinary']
    cutoff_at: str
    notice_rcept_no: str = Field(pattern=r'^\d{14}$')
    agenda_ids: list[str] = Field(min_length=1, max_length=200)
    evidence_sources: list[SourceRead] = Field(default_factory=list, max_length=30)

    @model_validator(mode='after')
    def valid_case(self):
        self.request({}, 'direct').arguments()
        if len(set(self.agenda_ids)) != len(self.agenda_ids):
            raise ValueError('duplicate_agenda_ids')
        for source in self.evidence_sources:
            source.request()
        return self

    def request(self, workflow: dict, protocol: str, binding: dict | None = None) -> HarnessRequest:
        return HarnessRequest(company=self.company, year=self.year, meeting_type=self.meeting_type,
            cutoff_at=self.cutoff_at, notice_rcept_no=self.notice_rcept_no, workflow=workflow,
            structure=True, structure_protocol=protocol,
            expected_run_id=binding['run_id'] if binding else None,
            expected_policy_sha256=binding['policy_sha256'] if binding else None,
            expected_sources=binding['source_manifest'] if binding else None)


class ExperimentPlan(Strict):
    contract: Literal['opm-proxyvo-experiment/1'] = CONTRACT
    name: str = Field(pattern=r'^[a-z0-9_-]{1,80}$')
    models: list[ModelSpec] = Field(min_length=1, max_length=12)
    judges: list[ModelSpec] = Field(default_factory=list, max_length=4)
    methods: list[Literal['direct', 'staged', 'direct_with_review']] = Field(min_length=1)
    prompts: list[PromptSpec] = Field(min_length=1, max_length=20)
    cases: list[CaseSpec] = Field(min_length=1, max_length=30)
    repetitions: int = Field(default=5, ge=1, le=100, strict=True)
    order_seed: int = 20260911
    source_mode: Literal['fixed_packet'] = 'fixed_packet'
    human_reviewed: Literal[False] = False
    workflow: dict = Field(default_factory=lambda: {'stance': .5, 'firmness': .75, 'automation': .5})
    mcp_url_env: str = Field(default='PROXYVO_MCP_URL', pattern=r'^[A-Z][A-Z0-9_]*$')
    mcp_header_env: dict[str, str] = Field(default_factory=lambda: {'x-opendart-key': 'OPENDART_API_KEY'})
    budget: Budget = Field(default_factory=Budget)

    @model_validator(mode='after')
    def unique_and_valid(self):
        for items in ([m.id for m in self.models], [m.id for m in self.judges], self.methods,
                      [p.id for p in self.prompts], [c.id for c in self.cases]):
            if len(items) != len(set(items)):
                raise ValueError('duplicate_experiment_dimension')
        for header, env in self.mcp_header_env.items():
            if not re.fullmatch(r'[a-zA-Z0-9-]+', header) or not re.fullmatch(r'[A-Z][A-Z0-9_]*', env):
                raise ValueError('invalid_header_environment_mapping')
        for case in self.cases:
            case.request(self.workflow, 'direct').arguments()
        return self


def read_plan(path: Path) -> ExperimentPlan:
    try:
        return ExperimentPlan.model_validate(json.loads(path.read_text()))
    except Exception:
        raise ExperimentError('invalid_experiment_plan') from None


def code_identity() -> dict:
    """실험에 사용한 코드·데이터·실행 스크립트의 해시를 반환한다."""
    # git HEAD만으로는 구분할 수 없는 미커밋 변경까지 포함한다.
    return runtime_identity()


class Ledger:
    """실험 결과를 공개 저장소 밖에 보관한다."""
    def __init__(self, root: Path):
        self.root = root.resolve()
        # 결과에는 모델 제공자의 사용량 등 비공개 정보가 포함된다.
        if self.root == ROOT or ROOT in self.root.parents:
            raise ExperimentError('private_output_directory_required')
        self.root.mkdir(parents=True, exist_ok=True)
        self.secrets = [v for k, v in os.environ.items()
                        if any(part in k.upper() for part in ('KEY', 'SECRET', 'TOKEN')) and len(v) >= 8]

    def safe(self, value):
        if isinstance(value, dict):
            return {k: ('[redacted]' if any(t in k.lower() for t in ('api_key', 'authorization', 'secret'))
                        else self.safe(v)) for k, v in value.items()}
        if isinstance(value, list):
            return [self.safe(v) for v in value]
        if isinstance(value, str):
            for secret in self.secrets:
                value = value.replace(secret, '[redacted]')
        return value

    def write(self, relative: str, value) -> str:
        value = self.safe(value)
        data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if path.read_text() != data:
                raise ExperimentError('immutable_artifact_conflict')
            return digest(value)
        # 중단된 쓰기 흔적을 남겨 복구 시 유료 호출을 모르게 반복하지 않도록 한다.
        with path.open('x') as stream:
            stream.write(data)
        return digest(value)

    def read(self, relative: str):
        try:
            return json.loads((self.root / relative).read_text())
        except Exception:
            raise ExperimentError('missing_or_corrupt_artifact') from None

    def files(self, pattern: str) -> list[Path]:
        return sorted(self.root.glob(pattern))


def attempt_state(ledger: Ledger, job_id: str) -> dict:
    """작업의 최초 시도와 최신 시도의 상태를 함께 반환한다."""
    base = f'runs/{job_id}'
    markers = ledger.files(base + '/attempt-*/started.json')
    results = ledger.files(base + '/attempt-*/result.json')
    directories = sorted({p.parent for p in [*markers, *results]},
                         key=lambda p: int(p.name.removeprefix('attempt-')))
    def result_at(directory):
        path = directory / 'result.json'
        return ledger.read(str(path.relative_to(ledger.root))) if path.exists() else None
    first = result_at(ledger.root / base / 'attempt-001')
    # 과거의 미완료 시도가 이후 재시도의 완료 결과를 가리지 않도록 최신 상태를 따로 읽는다.
    latest = result_at(directories[-1]) if directories else None
    return {'first': first, 'latest': latest, 'count': len(directories),
            'status': latest['status'] if latest else ('interrupted' if directories else 'not_started'),
            'first_status': first['status'] if first else ('interrupted' if directories else 'not_started'),
            'next_attempt': int(directories[-1].name.removeprefix('attempt-')) + 1 if directories else 1,
            'result_paths': sorted(results, key=lambda p: int(p.parent.name.removeprefix('attempt-')))}
