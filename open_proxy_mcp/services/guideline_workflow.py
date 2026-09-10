"""Request-scoped voting preferences and per-agenda preparation routing.

This module does not submit ballots or attest that an LLM judgment is correct.
Policy stance, recommendation, and a user's manual-review choice are separate.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt, StrictStr


class WorkflowSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stance: Literal["standard", "conservative"] = "standard"
    automation: Literal["automatic", "selective", "manual"] = "selective"
    decision_posture: Annotated[StrictInt | StrictFloat, Field(ge=0, le=1, allow_inf_nan=False)] = 0.75
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
    except Exception:
        raise ValueError("guideline_workflow: invalid settings") from None
    return settings.model_dump()


def decision_guidance(settings: dict | None = None) -> dict:
    """Continuous caller-LLM preference, never a probability or vote rewrite."""
    value = resolve_workflow_settings(settings)['decision_posture']
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


def apply_workflow_policy(policy: dict, settings: dict | None = None) -> dict:
    """Bind the effective request preferences into the policy/task digest.

    Conservative stance does not invent a different numerical threshold. Only
    an explicit attendance_min_pct changes the existing policy parameter.
    """
    effective = resolve_workflow_settings(settings)
    result = deepcopy(policy)
    result["workflow_settings"] = effective
    result["decision_guidance"] = decision_guidance(effective)
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
        elif effective["automation"] == "manual" or title in manual_titles:
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
        elif effective["automation"] == "selective" and row.get("decision") != "FOR":
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
