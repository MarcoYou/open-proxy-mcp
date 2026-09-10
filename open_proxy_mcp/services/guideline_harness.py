"""Request-local, pinned-meeting execution of the existing voting workflow.

The caller owns the model and continuation. No assessments or runs are persisted.
The current policy is applied retrospectively; this is not a reconstruction of
the policy an institution actually used at the historical meeting.
"""
from __future__ import annotations

from contextvars import ContextVar
from collections import Counter
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Awaitable, Callable

from pydantic import BaseModel, ConfigDict, Field, StrictStr


CONTRACT = "opm-voting-harness/2"
_CONTEXT: ContextVar[dict | None] = ContextVar("guideline_harness", default=None)


class HarnessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cutoff_at: StrictStr = Field(min_length=20, max_length=40)
    notice_rcept_no: StrictStr = Field(pattern=r"^\d{14}$")
    expected_run_id: StrictStr | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    expected_policy_sha256: StrictStr | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    expected_sources: dict[str, str] = Field(default_factory=dict, max_length=200)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _public_engine_manifest(package: Path) -> dict[str, str]:
    """Fingerprint public Python and bundled JSON, without following symlinks.

    This is a source-bundle identity, not a claim about interpreter/dependency
    binaries, external extensions, environment configuration, or runtime state.
    Only known package code roots and the bundled data tree are traversed.
    Credentials, raw/cache documents, and user results are outside this scope.
    """
    roots = {"dart", "data", "harness", "services", "tools"}
    excluded = {"raw", "cache", "caches", "results", "userresults", "user_results",
                "output", "logs", "keys", "credentials", "secrets", "__pycache__"}
    manifest: dict[str, str] = {}
    for directory, subdirs, filenames in os.walk(package, followlinks=False):
        current = Path(directory)
        relative = current.relative_to(package)
        subdirs[:] = sorted(
            name for name in subdirs
            if not name.startswith(".") and name not in excluded
            and not (current / name).is_symlink()
            and (relative.parts or name in roots)
        )
        for name in sorted(filenames):
            path = current / name
            member = path.relative_to(package)
            if (name.startswith(".") or path.is_symlink() or re.search(
                r"(?:^|[_-])(?:secrets?|credentials?|(?:api[_-]?)?keys?)(?:[_-]|$)",
                path.stem.lower(),
            )):
                continue
            if path.suffix != ".py" and not (
                member.parts[0] == "data" and path.suffix == ".json"
            ):
                continue
            manifest[member.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return manifest


@lru_cache(maxsize=1)
def engine_bundle_sha256() -> str:
    """One fingerprint per immutable server process; code/data edits need restart."""
    package = Path(__file__).resolve().parents[1]
    return digest({"contract": "opm-public-engine-bundle/1",
                   "files": _public_engine_manifest(package)})


def get_harness_context() -> dict | None:
    return _CONTEXT.get()


def bind_harness_policy(policy: dict) -> dict:
    context = get_harness_context()
    if context is None:
        return policy
    result = deepcopy(policy)
    result["execution_context"] = context["binding"]
    return result


def _sources(payload: dict) -> dict[str, str]:
    manifest: dict[str, str] = {}
    tasks = [(row.get('guideline_trace') or {}).get('assessment_task') or {}
             for row in (payload.get('data') or {}).get('agenda_decisions', [])]
    tasks += [entry['task'] for entry in ((payload.get('data') or {}).get('guideline_application') or {}).get('structure_tasks', [])]
    for task in tasks:
        for source in task.get("sources", []):
            identifier = source["source_id"]
            source_hash = source.get("document_sha256") or digest(source.get("excerpts", []))
            if identifier in manifest and manifest[identifier] != source_hash:
                raise ValueError("source_identity_conflict")
            manifest[identifier] = source_hash
    return manifest


def _error(code: str, exclusions: list | None = None) -> dict:
    messages = {
        "invalid_harness_request": "실행 기준시각·공고·계속 실행 계약을 확인하세요.",
        "pilot_required": "시점 고정 하네스는 v2 pilot 및 사후 자료 제외 설정이 필요합니다.",
        "company_unresolved": "회사를 확정한 뒤 같은 공고로 다시 요청하세요.",
        "meeting_unverified": "해당 회사의 소집공고·회차·공개시점을 확인하지 못했습니다. 다른 회차로 대체하지 않았습니다.",
        "cutoff_after_meeting": "주총 이후 자료가 들어오지 않도록 정보 마감시점을 조정하세요.",
        "run_changed": "회차·기준시각·정책·설정이 바뀌었습니다. 새 실행으로 평가해야 합니다.",
        "policy_changed": "실행 중 정책이 바뀌었습니다. 새 실행으로 평가해야 합니다.",
        "source_changed": "기존 원문이 바뀌었습니다. 기존 평가를 재사용하지 말고 새 원문으로 평가하세요.",
        "meeting_mismatch": "서로 다른 회차의 공고가 섞여 해당 결과를 반환하지 않았습니다.",
        "source_identity_conflict": "동일 출처에 서로 다른 원문이 연결되어 재확인이 필요합니다.",
        "research_requires_harness": "추가 공시 탐색에는 pilot의 회사·회차·마감시점 고정이 필요합니다.",
    }
    return {"tool": "proxy_advise_before_meeting", "status": "error",
            "warnings": [messages.get(code, messages["invalid_harness_request"])],
            "data": {"agenda_decisions": [], "guideline_harness": {
                "contract": CONTRACT, "status": "needs_reassessment", "error_code": code,
                "excluded_information": exclusions or [], "human_reviewed": False,
                "ballots_submitted": 0}}}


async def run_harness(company: str, request: dict, arguments: dict,
                      invoke: Callable[..., Awaitable[dict]]) -> dict:
    """Put every selection and read inside the same temporal and meeting scope."""
    from open_proxy_mcp.dart.as_of import (
        get_strict_as_of, reset_strict_as_of, set_strict_as_of, strict_exclusions,
    )
    from open_proxy_mcp.services.guideline_policy import load_pilot_guideline_policy
    from open_proxy_mcp.services.guideline_workflow import apply_workflow_policy
    from open_proxy_mcp.services.meeting_pin import prepare_meeting_pin, use_meeting_pin
    from open_proxy_mcp.services.company import (
        resolve_company_query, company_ambiguous_warning, company_not_found_warning,
        COMPANY_LOOKUP_NEXT_ACTION,
    )
    from open_proxy_mcp.services.contracts import declare_weak_resolution
    from open_proxy_mcp.dart.client import get_dart_client
    from open_proxy_mcp.services.guideline_research import (
        ResearchQuery, build_meeting_research_plan, discover_research_sources,
    )
    from open_proxy_mcp.services.guideline_evidence import normalize_candidate_selection_name

    try:
        from .election_structure import StructureRequest
        settings = HarnessRequest.model_validate(request)
        arguments = dict(arguments)
        structure_input = arguments.pop('guideline_structure', None)
        structure_request = StructureRequest.model_validate(structure_input) if structure_input is not None else None
        research_input = arguments.pop("guideline_research", None)
        research_query = ResearchQuery.model_validate(research_input) if research_input is not None else None
        if any(not isinstance(k, str) or len(k) > 200 or not re.fullmatch(r"[0-9a-f]{64}", v)
               for k, v in settings.expected_sources.items()):
            return _error("invalid_harness_request")
    except Exception:
        return _error("invalid_harness_request")
    if (arguments.get("vote_style") != "opm_guideline_v2"
            or arguments.get("guideline_mode") != "pilot"
            or arguments.get("include_after_meeting", False)):
        return _error("pilot_required")
    strict_token = context_token = None
    try:
        strict_token = set_strict_as_of(settings.cutoff_at)
        temporal = get_strict_as_of()
        effective = temporal["effective_as_of"]
        if arguments.get("as_of") not in (None, "", effective):
            return _error("invalid_harness_request")
        resolved = await resolve_company_query(company)
        if not resolved.selected or getattr(resolved.status, "value", resolved.status) in {"ambiguous", "error"}:
            response = _error("company_unresolved", strict_exclusions())
            ambiguous = getattr(resolved.status, "value", resolved.status) == "ambiguous"
            response["warnings"] = [company_ambiguous_warning(company, resolved.candidates)
                                    if ambiguous else company_not_found_warning(company)]
            response["next_actions"] = [COMPANY_LOOKUP_NEXT_ACTION]
            if ambiguous:
                response["data"]["candidates"] = resolved.candidates[:10]
            return declare_weak_resolution(response)
        try:
            pin = await prepare_meeting_pin(
                get_dart_client(), resolved.selected["corp_code"], settings.notice_rcept_no,
                meeting_type=arguments.get("meeting_type", "auto"),
                year=arguments.get("year") or None, as_of=effective)
        except Exception:
            return _error("meeting_unverified", strict_exclusions())
        if effective >= pin.meeting_date.strftime("%Y%m%d"):
            return _error("cutoff_after_meeting", strict_exclusions())
        binding = {"contract": CONTRACT, "cutoff_at": temporal["cutoff_at"],
                   "effective_as_of": effective, "meeting_pin": pin.digest,
                   "engine_bundle_sha256": engine_bundle_sha256(),
                   "policy_application": "retrospective_current_policy"}
        if structure_request is not None:
            binding['structure_contract'] = 'opm-election-structure/1'
        context_token = _CONTEXT.set({"binding": binding, "pin": pin, 'structure_enabled': structure_request is not None})
        policy = bind_harness_policy(apply_workflow_policy(
            load_pilot_guideline_policy(), arguments.get("guideline_workflow")))
        # Match the task contract's own canonical hashing convention.
        from open_proxy_mcp.services.guideline_assessment import _digest
        policy_hash = _digest(policy)
        run_id = digest({"binding": binding, "policy_sha256": policy_hash})
        if settings.expected_policy_sha256 and settings.expected_policy_sha256 != policy_hash:
            return _error("policy_changed", strict_exclusions())
        if settings.expected_run_id and settings.expected_run_id != run_id:
            return _error("run_changed", strict_exclusions())
        call_args = {**arguments, "year": pin.year, "meeting_type": pin.meeting_type,
                     "as_of": effective, "include_after_meeting": False}
        with use_meeting_pin(pin):
            payload = await invoke(company, **call_args)
        if payload.get("status") == "error":
            return payload
        data = payload.get("data") or {}
        references = {r.get("evidence_rcept_no") for r in data.get("agenda_decisions", [])
                      if r.get("evidence_rcept_no")}
        if references - {pin.notice_rcept_no} or (data.get("year_resolution") or {}).get("notice_mismatch"):
            return _error("meeting_mismatch", strict_exclusions())
        application = data.setdefault('guideline_application', {})
        structure_counts = {'accepted_unreviewed': 0, 'pending': 0, 'rejected': 0}
        if structure_request is not None:
            from .election_structure import build_structure_task, accept_structure_assessment, apply_structure_results
            context = get_harness_context()
            source_packets = list(context.get('structure_sources', []))
            for row in data.get('agenda_decisions', []):
                source_packets.extend(((row.get('guideline_trace') or {}).get('assessment_task') or {}).get('sources', []))
            task = build_structure_task(payload, sources=source_packets, binding=binding,
                policy={'id': policy.get('id'), 'version': policy['version'],
                        'workflow_settings': policy['workflow_settings'],
                        'structure_policy': policy.get('election_structure', {})})
            task['reading_requests'] = context.get('structure_reading_requests', [])
            # Unread handles are navigation only, outside the citation and task hash.
            assessment = accept_structure_assessment(task, structure_request.assessments[0] if structure_request.assessments else None)
            apply_structure_results(payload, task, assessment, policy['workflow_settings'])
            application['structure_tasks'] = [{'task': task, 'assessment': assessment}]
            application['assessment_scope'] = '후보 평가 + 명시적으로 제출한 선출 구조 평가. 미평가 안건은 기존 엔진 결과.'
            structure_counts[assessment['status']] += 1
            workflow = application.setdefault('voting_workflow', {})
            workflow['counts'] = dict(Counter(
                (r.get('voting_workflow') or {}).get('status', 'not_applicable') for r in data.get('agenda_decisions', [])))
            rows = data.get('agenda_decisions', [])
            workflow['unmatched_manual_agenda_titles'] = sorted(set(policy['workflow_settings']['manual_agenda_titles'])
                - {r.get('agenda_title') for r in rows if (r.get('voting_workflow') or {}).get('status') == 'manual_review'})
            workflow['unmatched_manual_agenda_ids'] = sorted(set(policy['workflow_settings']['manual_agenda_ids'])
                - {r.get('agenda_id') for r in rows if (r.get('voting_workflow') or {}).get('status') == 'manual_review'})
        manifest = _sources(payload)
        # The notice itself is always frozen, even when no candidate was parsed.
        manifest.setdefault(f"pinned_notice:{pin.notice_rcept_no}", pin.source_sha256)
        if any(k in manifest and manifest[k] != v for k, v in settings.expected_sources.items()):
            return _error("source_changed", strict_exclusions())
        unavailable = sorted(set(settings.expected_sources) - set(manifest))
        continuation = {"cutoff_at": temporal["cutoff_at"], "notice_rcept_no": pin.notice_rcept_no,
                        "expected_run_id": run_id, "expected_policy_sha256": policy_hash,
                        "expected_sources": {**settings.expected_sources, **manifest}}
        counts = {"accepted_unreviewed": 0, "pending": 0, "rejected": 0}
        for row in data.get("agenda_decisions", []):
            status = ((row.get("guideline_trace") or {}).get("llm_assessment") or {}).get("status")
            if status in counts:
                counts[status] += 1
        data["guideline_harness"] = {
            **binding, "status": "evaluated" if counts["accepted_unreviewed"] or structure_counts['accepted_unreviewed'] else "awaiting_model",
            "assessment_counts": counts,
            'structure_assessment_counts': structure_counts,
            'capabilities': {'task_kinds': ['candidate', 'election_structure'],
                'structure_contract': 'opm-election-structure/1',
                'source_types': ['dart', 'kind', 'dart_attachments', 'dart_attachment'],
                'scope_patch_supported': False, 'legal_assessment_supported': False,
                'visual_reader': 'optional_registered_adapter', 'ballot_submission': False},
            "run_id": run_id, "policy_sha256": policy_hash, "policy_version": policy["version"],
            "notice_rcept_no": pin.notice_rcept_no, "meeting_date": pin.meeting_date.isoformat(),
            "source_manifest": manifest, "continuation": continuation,
            "unavailable_previous_sources": unavailable,
            "excluded_information": strict_exclusions(), "human_reviewed": False,
            "ballots_submitted": 0, "model_executor": "caller",
            "limitations": [
                "공개 날짜만 확인된 자료는 기준시각의 한국 날짜 전일까지 사용합니다. 당일 자료는 제외합니다.",
                "현재 정책을 당시 공개 원문에 적용하는 실험이며 당시 기관 정책의 재현이 아닙니다.",
                "인용 연결·시간 경계를 검사하며 모델 사전학습 지식이나 의미 정확성은 인증하지 않습니다.",
                "시점 증명이 없는 원천과 최신 파생 캐시는 사용하지 않습니다. 제외 항목은 판단 범위에서 알립니다.",
            ],
        }
        application = data.setdefault("guideline_application", {})
        application["research_plan"] = build_meeting_research_plan(
            data.get("canonical_name") or company, effective, pin.meeting_type,
            pin.meeting_date.isoformat(), pin.notice_rcept_no)
        # Discovery returns unread index entries; it cannot enter a task's
        # citation corpus or change its identity until explicitly read.
        if research_query is not None:
            application["research_discovery"] = await discover_research_sources(
                get_dart_client(), resolved.selected["corp_code"], effective,
                pin.meeting_type, pin.meeting_date.isoformat(), research_query)
        names = {
            normalize_candidate_selection_name(task.get("candidate_name") or "")
            for row in data.get("agenda_decisions", [])
            if (task := (row.get("guideline_trace") or {}).get("assessment_task"))
        }
        application["unused_candidate_scopes"] = [
            {"source_id": item.get("source_id") or f"filing:{item.get('rcept_no', '')}",
             "candidate_names": selected_names,
             "source_request": {
                 **({"type": "dart", "rcept_no": item["rcept_no"]} if item.get("rcept_no") else
                    {"type": "kind", "url": item.get("source_url")}),
                 **item["read_options"],
             },
             "reason": "지정한 후보 이름이 현재 지원 과업에 없어 이 자료를 해당 과업에 연결하지 않았습니다."}
            for item in application.get("supplemental_collection", [])
            if (selected_names := (item.get("read_options") or {}).get("candidate_names"))
            and not any(normalize_candidate_selection_name(name) in names for name in selected_names)
        ]
        data["guideline_harness"]["excluded_information"] = strict_exclusions()
        return declare_weak_resolution(payload)
    except ValueError:
        return _error("invalid_harness_request", strict_exclusions() if strict_token is not None else [])
    finally:
        if context_token is not None:
            _CONTEXT.reset(context_token)
        if strict_token is not None:
            reset_strict_as_of(strict_token)
