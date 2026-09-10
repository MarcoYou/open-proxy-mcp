"""Source-bound review of specific legacy extraction findings in the v2 pilot.

The caller LLM may refute a parser observation, not waive a law or confirmed
adverse fact. These helpers bind that claim to the current task; they do not
verify its meaning or certify that the candidate is legally qualified.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json


def build_candidate_findings(candidate: dict) -> list[dict]:
    """Expose all active observations, including ones hidden by an early return.

    Only tenure, concurrent-position and relationship extraction are correctable.
    Unknown concern categories are retained rather than inferred to be harmless.
    Missing data and unperformed optional checks do not themselves add findings.
    """
    findings: list[dict] = []
    identity = {key: candidate.get(key) for key in ("name", "birth_date", "role_type")}

    def add(kind: str, field: str, observed: dict, correctable: bool, label: str):
        content = {"candidate": identity, "kind": kind, "field": field, "observed": observed}
        digest = hashlib.sha256(json.dumps(content, ensure_ascii=False, sort_keys=True,
                                          separators=(",", ":")).encode()).hexdigest()[:20]
        findings.append({"finding_id": f"{kind}:{digest}", "kind": kind, "field": field,
                         "observed": deepcopy(observed), "correctable": correctable,
                         "label": label, "origin": "legacy_extraction_observation"})

    indep = candidate.get("independence") or {}
    sub = indep.get("sub_factors") or {}
    tenure = sub.get("five_year_rule") or {}
    if tenure.get("result") == "potential_long_tenure" or indep.get("summary") == "long_tenure_concerns":
        add("tenure", "independence.sub_factors.five_year_rule", tenure or {"summary": indep["summary"]},
            True, "장기재직 관측: 회사 근무기간·이사 재직기간·타사 경력을 구별해 판독")

    relation_count = 0
    for key, active in (
        ("major_shareholder_relation", {"related"}),
        ("recent_3y_transactions", {"transactions_exist"}),
        ("recent_2y_employee", {"former_employee"}),
        ("proposer_affiliation", {"employed_by_proposer", "proxy_solicitor_self", "affiliated_with_proposer"}),
    ):
        observation = sub.get(key) or {}
        if observation.get("result") in active:
            add("relationship", f"independence.sub_factors.{key}", observation, True,
                "관계 귀속 관측: 주체·회사·역할·시점과 원문 열의 대응을 판독")
            relation_count += 1

    # A summary without a mapped cause cannot be cleared by refuting another cause.
    summary = indep.get("summary")
    known = {None, "", "independent", "proposer_nominated", "weak_concerns", "long_tenure_concerns"}
    if ((summary in {"concerns", "proposer_side_concerns"} and not relation_count)
            or summary not in known | {"concerns", "proposer_side_concerns"}):
        add("unmapped_concern", "independence", indep, False,
            "현재 교정 계약에 원인이 대응되지 않는 독립성 경보")

    faith = candidate.get("faithfulness") or {}
    concurrent = faith.get("concurrent_outside_directors") or {}
    total = concurrent.get("total")
    overboarded = (concurrent.get("summary") == "strong_concerns_concurrent"
                   or isinstance(total, (int, float)) and not isinstance(total, bool) and total >= 3)
    if overboarded:
        add("concurrent_positions", "faithfulness.concurrent_outside_directors", concurrent, True,
            "겸직 관측: 현직·퇴임·임기 만료 및 대상 회사 포함 여부를 판독")

    audit = faith.get("audit_history_check") or {}
    audit_concern = audit.get("summary") == "red_flag" or bool(audit.get("red_flags"))
    if audit_concern:
        add("protected_audit_history", "faithfulness.audit_history_check", audit, False,
            "회계 위험 이력: 이번 추출 교정 범위 밖의 경보")
    faith_summary = faith.get("summary")
    if (faith_summary == "concerns" and not (overboarded or audit_concern)
            or faith_summary not in {None, "", "concerns", "weak_concerns", "raw_disclosed", "clean"}):
        add("unmapped_concern", "faithfulness", faith, False,
            "현재 교정 계약에 원인이 대응되지 않는 충실성 경보")

    disq = candidate.get("disqualification") or {}
    disq_sub = disq.get("sub_factors") or {}
    eligibility = disq_sub.get("eligibility") or {}
    if (disq.get("summary") == "red_flag"
            or (disq_sub.get("age") or {}).get("result") == "minor"
            or eligibility.get("result") == "red_flag" or eligibility.get("raw_flags")):
        add("protected_eligibility", "disqualification", disq, False,
            "결격·미성년 경보: 별도 사실 교정 계약이 필요하며 이번 범위에서 해제하지 않음")
    elif disq.get("summary") not in {None, "", "clean", "unknown_no_field"}:
        add("unmapped_concern", "disqualification", disq, False,
            "현재 교정 계약에 원인이 대응되지 않는 결격 경보")
    return findings


def validate_finding_reviews(task: dict, reviews: list[dict]) -> str | None:
    """Validate IDs and claim shape. Citation matching is done by the caller."""
    findings = {item["finding_id"]: item for item in task.get("baseline_findings", [])}
    seen: set[str] = set()
    for review in reviews:
        key = review["finding_id"]
        if key not in findings or key in seen:
            return "기존 경보 판독이 현재 평가 대상의 경보 ID와 맞지 않거나 중복되었습니다."
        seen.add(key)
        if not review["rationale"].strip() or any(not text.strip() for text in review["unresolved"]):
            return "기존 경보 판독의 사유 또는 미확인 사항이 비어 있습니다."
        disposition = review["disposition"]
        if disposition != "unresolved" and not review["evidence_refs"]:
            return "기존 경보의 확인·추출 교정에 원문 인용이 없습니다."
        if disposition == "unresolved" and not review["unresolved"]:
            return "미해결 경보 판독에 확인할 사항이 없습니다."
        if disposition == "incorrect_extraction":
            if not findings[key]["correctable"]:
                return "이번 범위에서 교정할 수 없는 기존 경보입니다."
            if review["unresolved"] or review["counterevidence"]:
                return "미해결 사항 또는 반증이 있는 경보를 추출 오류로 해제할 수 없습니다."
    return None


def candidate_correction_eligibility(
    task: dict, accepted_assessment: dict, *, baseline_decision: str,
    proposed_decision: str, baseline_is_candidate_only: bool = False,
    law_layer_id: str | None = None, agenda_relation_type: str | None = None,
) -> dict:
    """Whether a source-bound extraction correction can relax this REVIEW.

    The caller must affirm that no later agenda rule changed the candidate's
    baseline. This helper never changes a recommendation or downstream ballot
    constraints, and does not relax AGAINST or NO_VOTE.
    """
    findings = task.get("baseline_findings") or []
    result = {"eligible": False, "human_reviewed": False,
              "review_label": "LLM 평가 · 사람 미검토",
              "validation_scope": "경보·대상·원문 인용 연결만 검증. 교정 주장의 의미 타당성은 LLM 평가.",
              "corrected_finding_ids": [], "retained_finding_ids": [f["finding_id"] for f in findings]}

    def stop(reason: str):
        return {**result, "reason": reason}

    if baseline_decision != "REVIEW" or proposed_decision != "FOR":
        return stop("추출 경보로 발생한 기존 검토 필요를 찬성으로 변경하는 경우에만 적용합니다.")
    if (not baseline_is_candidate_only or law_layer_id is not None
            or agenda_relation_type in {"procedural", "alternative", "conditional", "withdrawn"}):
        return stop("후보 추출 경보 외의 법령·안건 관계 또는 기존 결정 사유를 보존합니다.")
    if accepted_assessment.get("status") != "accepted_unreviewed" or accepted_assessment.get("task_id") != task.get("task_id"):
        return stop("현재 원문 패킷에 연결되어 수용된 평가가 필요합니다.")
    if not findings:
        return stop("현재 과제에 교정 대상으로 명시된 추출 경보가 없습니다.")
    reviews = (accepted_assessment.get("assessment") or {}).get("finding_reviews") or []
    if validate_finding_reviews(task, reviews):
        return stop("현재 경보 판독 계약을 만족하지 않습니다.")
    by_id = {review["finding_id"]: review for review in reviews}
    corrected = [finding["finding_id"] for finding in findings
                 if finding["correctable"] and (by_id.get(finding["finding_id"]) or {}).get("disposition") == "incorrect_extraction"]
    retained = [finding["finding_id"] for finding in findings if finding["finding_id"] not in corrected]
    result.update(corrected_finding_ids=corrected, retained_finding_ids=retained)
    if retained:
        return stop("확인된 경보·미판독 경보 또는 이번 교정 범위 밖의 경보가 남아 있습니다.")
    return {**result, "eligible": True,
            "reason": "활성 추출 경보를 각각 원문으로 반박한 LLM 평가를 수용했습니다. 사람 미검토."}
