"""저장한 호출 기록을 재생하여 중단된 실행을 복구한다."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass, fields, replace
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Literal


def fingerprint(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def runtime_identity() -> dict:
    """현재 코드와 설정 파일의 해시를 수집한다."""
    # 커밋하지 않은 변경도 식별해야 코드가 달라진 실행을 같은 실행으로 재개하지 않는다.
    root = Path(__file__).resolve().parents[2]
    paths = sorted(p for p in (root / 'open_proxy_mcp').rglob('*')
                   if p.is_file() and p.suffix in {'.py', '.json', '.md', '.sql'})
    paths += [root / 'scripts/proxyvo_tester.py', root / 'pyproject.toml', root / 'uv.lock']
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths if p.exists()}


class CheckpointError(Exception):
    """복구 충돌은 모델 재시도로 삼키지 않고 호출자에게 돌려준다."""


@dataclass(frozen=True)
class RunState:
    phase: Literal['running', 'calling', 'completed_operation', 'paused', 'finished']
    operation: str | None = None
    status: Literal['running', 'interrupted', 'complete', 'partial', 'stopped'] = 'running'
    human_reviewed: bool = False


class FileCheckpoint:
    """사용자가 지정한 비공개 디렉터리에 실행 복구 기록을 보관한다."""
    def __init__(self, root: Path, *, identity: dict):
        self.root = Path(root).resolve()
        public = Path(__file__).resolve().parents[2]
        if self.root == public or public in self.root.parents or not identity:
            raise CheckpointError('private_checkpoint_identity_required')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        # 호출자는 모델 설정·프롬프트 버전 등 실행을 구분하는 설정을 identity에 넣는다.
        self.identity = deepcopy(identity)
        self._leased = False

    def _write(self, relative: str, value) -> None:
        # 비밀 값이 섞인 상태를 변조해 복구하기보다 저장 자체를 거부한다.
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
        if any(v in text for k, v in os.environ.items() if len(v) >= 8
               and any(part in k.upper() for part in ('KEY', 'SECRET', 'TOKEN'))):
            raise CheckpointError('checkpoint_contains_credential')
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix='.pending-')
        try:
            with os.fdopen(descriptor, 'w') as stream:
                json.dump({'value': value, 'sha256': fingerprint(value)}, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            # 기록 도중 종료되어 기존 파일까지 깨지는 일을 막도록 완성된 파일로 교체한다.
            os.replace(temporary, path)
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _read(self, relative: str):
        path = self.root / relative
        if not path.exists():
            return None
        try:
            value = json.loads(path.read_text())
            if fingerprint(value['value']) != value['sha256']:
                raise ValueError
            return value['value']
        except Exception:
            raise CheckpointError('checkpoint_corrupt') from None

    @contextmanager
    def lease(self, binding: dict):
        # 같은 상태를 두 프로세스가 동시에 재개하여 이중 호출하는 것을 막는다.
        import fcntl
        if self._leased:
            raise CheckpointError('checkpoint_already_running')
        with (self.root / '.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise CheckpointError('checkpoint_already_running') from None
            self._leased = True
            try:
                manifest = {'contract': 'opm-checkpoint/1', 'identity': self.identity,
                            'binding': binding, 'code': fingerprint(runtime_identity())}
                saved = self._read('manifest.json')
                if saved is not None and saved != manifest:
                    raise CheckpointError('checkpoint_binding_changed')
                if saved is None:
                    self._write('manifest.json', manifest)
                yield
            finally:
                self._leased = False
                fcntl.flock(lock, fcntl.LOCK_UN)

    def state(self, phase: str, operation: str | None = None, status: str = 'running'):
        self._write('state.json', asdict(RunState(phase, operation, status)))

    def cursor(self, namespace: str) -> CheckpointCursor:
        return CheckpointCursor(self, namespace)

    def receipts(self, namespace: str) -> list[dict]:
        folder = 'operations/' + fingerprint(namespace)
        return [self._read(str(p.relative_to(self.root))) for p in sorted((self.root / folder).glob('*.json'))]

    def operation_count(self, prefix: str) -> int:
        return sum(self._read(str(p.relative_to(self.root)))['label'].startswith(prefix)
                   for p in self.root.glob('operations/*/*.json'))


class CheckpointCursor:
    def __init__(self, store: FileCheckpoint, namespace: str):
        self.store, self.namespace, self.position = store, namespace, 0

    def child(self, name: str) -> CheckpointCursor:
        return self.store.cursor(self.namespace + '/' + name)

    async def call(self, label: str, inputs: dict, callback, *, reentrant: bool = False,
                   read_only: bool = False):
        if not self.store._leased:
            raise CheckpointError('checkpoint_lease_required')
        self.position += 1
        path = f'operations/{fingerprint(self.namespace)}/{self.position:06}.json'
        expected = {'namespace': self.namespace, 'position': self.position, 'label': label,
                    'input_sha256': fingerprint(inputs)}
        receipt = self.store._read(path)
        if receipt is not None:
            if any(receipt.get(k) != v for k, v in expected.items()):
                raise CheckpointError('checkpoint_operation_changed')
            if receipt['status'] == 'completed':
                return deepcopy(receipt['output'])
            if receipt['status'] == 'failed':
                if receipt.get('unavailable'):
                    from .runner import ModelUnavailableError
                    raise ModelUnavailableError(receipt['unavailable'])
                raise RuntimeError('recorded_operation_failed')
            # 완료 여부를 모르는 유료 호출은 자동 반복하지 않는다.
            # 재진입은 내부 호출 자체가 체크포인트로 보호되는 어댑터에만 허용한다.
            if not reentrant and not read_only:
                raise CheckpointError('checkpoint_inflight_uncertain')
        self.store._write(path, {**expected, 'status': 'pending', 'input': inputs})
        self.store.state('calling', label)
        try:
            output = await callback(self.child(str(self.position)))
        except CheckpointError:
            raise
        except Exception as error:
            from .runner import ModelUnavailableError
            self.store._write(path, {**expected, 'status': 'failed', 'input': inputs,
                'unavailable': error.code if isinstance(error, ModelUnavailableError) else None})
            raise
        # 취소·프로세스 종료는 pending으로 남는다. 마지막 완료 영수증은 보존된다.
        self.store._write(path, {**expected, 'status': 'completed', 'input': inputs, 'output': output})
        self.store.state('completed_operation', label)
        return deepcopy(output)


def packet_identity(payload: dict) -> str:
    """MCP 응답의 원문·정책·작업을 대조할 해시를 만든다."""
    from .runner import _tasks
    data = payload.get('data') or {}
    binding = data.get('guideline_harness') or {}
    if payload.get('status') in {'error', 'ambiguous'} or not binding.get('source_manifest'):
        raise CheckpointError('checkpoint_source_unavailable')
    tasks, _ = _tasks(payload)
    for task in tasks.values():
        task.pop('previous_assessment', None)
    return fingerprint({'binding': {k: binding.get(k) for k in
        ('run_id', 'policy_sha256', 'source_manifest', 'continuation')}, 'tasks': tasks})


async def run_checkpointed(harness, request):
    """저장한 호출 기록으로 하네스 실행을 이어간다."""
    # 완료한 호출은 저장된 응답을 재사용한다. 복구 성공이 판단의 정확성을 뜻하지는 않는다.
    from .runner import VotingHarness, _ACTIONS
    store = harness.checkpoint
    with store.lease({'request': request.arguments(), 'budget': asdict(harness.budget),
                      'adapter': {'name': harness.adapter.name, 'version': harness.adapter.version}}):
        mcp_cursor, model_cursor = store.cursor('mcp'), store.cursor('model')
        previous = [r for r in store.receipts('mcp') if r['status'] == 'completed']
        if previous:
            latest = previous[-1]
            # 과거 기록만 믿지 않고 현재 MCP의 원문·정책·작업이 같은지 먼저 대조한다.
            # 이 도구는 자문·원문 조회이며 실제 의결권 전송이 아니다.
            fresh = await harness.transport.call_tool(latest['input']['name'], latest['input']['arguments'])
            if packet_identity(deepcopy(fresh)) != packet_identity(deepcopy(latest['output'])):
                raise CheckpointError('checkpoint_sources_or_policy_changed')
        class Transport:
            async def call_tool(self, name, arguments):
                return await mcp_cursor.call('mcp', {'name': name, 'arguments': arguments},
                    lambda _: harness.transport.call_tool(name, arguments), read_only=True)
        class Adapter:
            name, version = harness.adapter.name, harness.adapter.version
            async def assess(self, context):
                inputs = {f.name: deepcopy(getattr(context, f.name)) for f in fields(context) if f.name != 'checkpoint'}
                async def invoke(child):
                    value = await harness.adapter.assess(replace(context, checkpoint=child))
                    return value.model_dump() if hasattr(value, 'model_dump') else value
                value = await model_cursor.call('model', inputs, invoke,
                    reentrant=bool(getattr(harness.adapter, 'checkpoint_resume_safe', False)))
                restore = getattr(harness.adapter, 'restore_checkpoint_action', None)
                if restore:
                    restore(deepcopy(value))
                return _ACTIONS.validate_python(value)
        store.state('running')
        try:
            result = await VotingHarness(Transport(), Adapter(), harness.budget).run(request)
            store.state('finished', status=result.status)
            return result
        except BaseException:
            store.state('paused', status='interrupted')
            raise
