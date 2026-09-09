"""Stateless, source-bound governance triage for explicit company batches.

The caller LLM reads and assesses; the server validates provenance and routes
attention. Event discovery is never itself a negative governance conclusion.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
import hashlib
import json
import re
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator

from open_proxy_mcp.clock import today_kst
from open_proxy_mcp.dart.client import get_dart_client
from open_proxy_mcp.services.company import (
    COMPANY_LOOKUP_NEXT_ACTION, company_ambiguous_warning,
    company_not_found_warning, resolve_company_query,
)
from open_proxy_mcp.services.contracts import declare_weak_resolution
from open_proxy_mcp.services.guideline_evidence import collect_supplemental_sources
from open_proxy_mcp.services.guideline_research import discover_guideline_context

CONTRACT_VERSION = "1"
CATEGORIES = (
    "minority_shareholder_treatment", "related_party_conflict", "board_accountability",
    "disclosure_reliability", "control_process", "other",
)
Category = Literal["minority_shareholder_treatment", "related_party_conflict", "board_accountability",
                   "disclosure_reliability", "control_process", "other"]
Materiality = Literal["high", "moderate", "low"]
Text = Annotated[StrictStr, Field(min_length=1, max_length=5000)]
_MATERIALITY = {"low": 1, "moderate": 2, "high": 3}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GovernanceCitation(_Strict):
    source_id: Text
    quote: Annotated[StrictStr, Field(min_length=12, max_length=3000)]


class GovernanceGap(_Strict):
    category: Category
    question: Text
    kind: Literal["missing_information", "truncated_context", "fetch_failed",
                  "conflicting_evidence", "identity_uncertain", "not_assessed"]
    next_action: Text


class GovernanceFinding(_Strict):
    finding_id: Text
    category: Category
    disposition: Literal["supported_risk", "no_adverse_signal", "unresolved"]
    materiality: Materiality
    actor: Text
    event: Text
    observation: Text
    fact_status: Literal["disclosed_fact", "party_claim", "court_ruling", "conditional_plan", "unknown"]
    procedural_state: Literal["not_applicable", "alleged", "filed", "pending", "interim_order",
                              "final_ruling", "appealed", "settled", "withdrawn", "unknown"]
    impact_state: Literal["observed", "prospective", "unknown"]
    event_date: Annotated[StrictStr, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")] | None = None
    case_or_round: Text | None = None
    ownership_basis: Literal["not_applicable", "contractual", "settled", "unknown"] = "not_applicable"
    voting_rights: Literal["not_applicable", "confirmed", "unresolved"] = "not_applicable"
    relevance: Text
    candidate_name: Text | None = None
    risk_basis: Literal["substantive_conduct", "event_presence_only", "not_applicable", "unknown"]
    rationale: Text
    evidence_refs: Annotated[list[GovernanceCitation], Field(max_length=12)]
    counterevidence: Annotated[list[GovernanceCitation], Field(max_length=12)] = Field(default_factory=list)
    gaps: Annotated[list[GovernanceGap], Field(max_length=12)] = Field(default_factory=list)

    @model_validator(mode="after")
    def coherent_fact_status(self):
        if self.event_date:
            date.fromisoformat(self.event_date)
        if self.disposition != "unresolved" and not self.evidence_refs:
            raise ValueError("A substantive finding requires source citations")
        if self.disposition == "supported_risk":
            if self.risk_basis != "substantive_conduct":
                raise ValueError("An event's presence is not adverse governance conduct")
            if self.fact_status in {"party_claim", "unknown"}:
                raise ValueError("An unverified party claim cannot become a supported adverse fact")
            if self.impact_state == "unknown":
                raise ValueError("A supported risk must distinguish observed from prospective impact")
            if self.fact_status == "conditional_plan" and self.impact_state != "prospective":
                raise ValueError("A conditional plan is not completed conduct")
            if any(g.kind in {"conflicting_evidence", "identity_uncertain"} for g in self.gaps):
                raise ValueError("Resolve material attribution conflicts before asserting a supported risk")
        if self.fact_status == "party_claim" and self.procedural_state in {"interim_order", "final_ruling"}:
            raise ValueError("A party claim is not a court ruling")
        if self.fact_status == "court_ruling" and self.procedural_state in {"alleged", "filed"}:
            raise ValueError("An application is not a court ruling")
        if self.ownership_basis == "contractual" and self.voting_rights == "confirmed":
            raise ValueError("Contractual acquisition does not establish settled voting rights")
        if self.disposition == "unresolved" and not self.gaps:
            raise ValueError("An unresolved finding must identify its specific information gap")
        return self


class GovernanceAssessment(_Strict):
    task_id: Text
    evaluator: Text
    findings: Annotated[list[GovernanceFinding], Field(max_length=30)]
    skipped_checks: Annotated[list[GovernanceGap], Field(max_length=30)] = Field(default_factory=list)
    summary: Text


REVIEW_INSTRUCTIONS = [
    "Read the source excerpts and use their next-reading handles when context is truncated. Parsing failure is not source absence.",
    "Assess minority treatment, concrete related-party conflicts, board accountability, disclosure reliability and control procedures. Other is available for a material issue outside these checks.",
    "Tender offers, activism, shareholder proposals and litigation are not adverse findings by themselves. Identify the concrete conduct, affected shareholder interest and why it matters.",
    "Separate issuer or proponent claims, disclosed transaction terms, applications and court rulings. An interim dismissal is not a final merits judgment. Read conditions, objections and appeals.",
    "Preserve receipt date, event date, correction lineage and separate case/offer rounds. Contractual or deemed holdings do not establish completed acquisition or exercisable voting rights.",
    "Relate each fact to this company or a named candidate. Do not infer a hostile camp or employment relationship from names, nominations or activist presence.",
    "Retain counterevidence. Unknown dates do not erase an otherwise disclosed role. Report missing checks individually and continue the remaining checks and companies.",
    "Materiality high means an identified substantial threat to shareholder rights or irreversible value transfer; moderate means a concrete governance concern requiring further attention; low means a bounded issue to monitor. Explain the effect; do not generate numeric risk or confidence scores.",
    "No adverse signal means only the specified reviewed scope. It is not a clean bill of health. A missing document is not positive evidence.",
    "All judgments are LLM assessments, human unreviewed. This tool neither decides nor submits any ballot.",
]


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def _ymd(value: str, *, default_today: bool = False) -> str:
    if not value and default_today:
        return today_kst().strftime("%Y%m%d")
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}", value):
        raise ValueError("Dates must be YYYYMMDD")
    date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    return value


def build_governance_task(company: dict, as_of: str, discovery: dict,
                          supplemental: list[dict] | None = None,
                          review_materiality: str = "moderate") -> dict:
    """Bind a task to current source content, coverage and routing settings."""
    from open_proxy_mcp.services.guideline_evidence import build_source_packet

    indexed = {}
    for item in [*discovery.get("filings", []), *(supplemental or [])]:
        if item.get("status") != "read" or not item.get("text"):
            continue
        published = item.get("published") or item.get("rcept_no", "")[:8]
        if published and published > as_of:
            continue
        packet = build_source_packet(item)
        if packet and packet.get("source_id"):
            indexed[packet["source_id"]] = {**packet, "published": published,
                "report_name": item.get("report_nm"), "family": item.get("family"),
                "document_role": item.get("document_role"),
                "same_day_time_unverified": published == as_of}
    coverage = {key: discovery.get(key) for key in (
        "status", "scans", "matches", "complete_history", "next_reads", "budget", "hint", "temporal_scope")}
    sources = list(indexed.values())
    task = {"contract_version": CONTRACT_VERSION,
            "company": {key: company.get(key, "") for key in ("corp_code", "corp_name", "stock_code")},
            "as_of": as_of, "review_materiality": review_materiality,
            "sources": sources, "discovery": coverage,
            "source_failures": [{key: item.get(key) for key in ("source_id", "rcept_no", "source_url", "status")}
                                for item in [*discovery.get("filings", []), *(supplemental or [])]
                                if item.get("status") != "read"],
            "checks": list(CATEGORIES), "instructions": REVIEW_INSTRUCTIONS,
            "assessment_schema_ref": "response.assessment_schema",
            "human_reviewed": False, "scope_complete": False,
            "same_day_policy": "Day-level as_of; explicitly supplied same-day sources have no verified intraday ordering."}
    task["task_id"] = "governance:" + _digest(task)
    return task


def _source_excerpts(source: dict) -> list[str]:
    excerpts = source.get("excerpts") or []
    if isinstance(excerpts, str):
        return [excerpts]
    return [x if isinstance(x, str) else str(x.get("text", "")) for x in excerpts]


def accept_governance_assessment(task: dict, assessment: GovernanceAssessment) -> dict:
    errors = []
    if assessment.task_id != task["task_id"]:
        errors.append("task_id_mismatch")
    sources = {source["source_id"]: source for source in task["sources"]}
    finding_ids = [finding.finding_id for finding in assessment.findings]
    if len(set(finding_ids)) != len(finding_ids):
        errors.append("duplicate_finding_id")
    for finding in assessment.findings:
        if finding.event_date and finding.event_date.replace("-", "") > task["as_of"] and finding.fact_status != "conditional_plan":
            errors.append("future_event_not_marked_conditional")
        for citation in [*finding.evidence_refs, *finding.counterevidence]:
            source = sources.get(citation.source_id)
            if source is None or not any(citation.quote in excerpt for excerpt in _source_excerpts(source)):
                errors.append("citation_not_in_current_source_excerpt")
    if errors:
        return {"status": "rejected", "errors": sorted(set(errors)), "human_reviewed": False}
    covered = {f.category for f in assessment.findings}
    declared_skips = {g.category for g in assessment.skipped_checks}
    implicit_skips = [{"category": category, "kind": "not_assessed",
                       "question": "This governance check was not assessed.",
                       "next_action": "Read relevant disclosures if this check matters to the user's mandate."}
                      for category in CATEGORIES if category != "other" and category not in covered | declared_skips]
    result = assessment.model_dump()
    return {**result, "status": "accepted_unreviewed", "human_reviewed": False,
            "scope_complete": False, "implicit_skipped_checks": implicit_skips,
            "validation_note": "Task identity and literal citations checked; factual interpretation and materiality remain human unreviewed."}


def governance_triage(accepted: dict | None, review_materiality: str = "moderate") -> dict:
    """Deterministic attention routing from accepted findings, never event counts."""
    base = {"human_reviewed": False, "scope_complete": False,
            "review_materiality": review_materiality, "ballot_action": "none"}
    if not accepted or accepted.get("status") != "accepted_unreviewed":
        return {**base, "disposition": "not_assessed", "reason": "No accepted LLM assessment for this source packet."}
    findings = accepted["findings"]
    risks = [f for f in findings if f["disposition"] == "supported_risk"]
    actionable = [f for f in risks if _MATERIALITY[f["materiality"]] >= _MATERIALITY[review_materiality]]
    # A partial no-adverse conclusion must not hide its own material conflict.
    # Inspect declared gaps independently of the caller's disposition label.
    context_needed = [f for f in findings
                      if _MATERIALITY[f["materiality"]] >= _MATERIALITY[review_materiality]
                      and any(g["kind"] in {"conflicting_evidence", "identity_uncertain"} for g in f["gaps"])]
    if actionable:
        disposition = "priority_review" if any(f["materiality"] == "high" for f in actionable) else "review"
    elif context_needed:
        disposition = "needs_evidence"
    elif risks:
        disposition = "monitor"
    elif any(f["disposition"] == "no_adverse_signal" for f in findings):
        disposition = "no_adverse_signal_in_reviewed_scope"
    else:
        disposition = "not_assessed"
    return {**base, "disposition": disposition,
            "supported_risk_ids": [f["finding_id"] for f in risks],
            "attention_finding_ids": [f["finding_id"] for f in actionable],
            "context_followup_finding_ids": [f["finding_id"] for f in context_needed],
            "unresolved_finding_ids": [f["finding_id"] for f in findings if f["disposition"] == "unresolved"],
            "skipped_check_count": len(accepted.get("skipped_checks", [])) + len(accepted.get("implicit_skipped_checks", [])),
            "reason": "Attention follows cited substantive findings and the selected materiality threshold; missing checks do not stop the batch."}


def _prepare_assessments(items: list[dict] | None) -> tuple[dict, list[dict]]:
    if items is None:
        return {}, []
    if not isinstance(items, list) or len(items) > 30:
        raise ValueError("governance_assessments must contain at most 30 company assessments")
    parsed, errors, duplicates = {}, [], set()
    for index, item in enumerate(items):
        try:
            value = GovernanceAssessment.model_validate(item)
        except Exception:
            errors.append({"index": index, "reason": "invalid_assessment_schema"})
            continue
        if value.task_id in parsed or value.task_id in duplicates:
            parsed.pop(value.task_id, None)
            duplicates.add(value.task_id)
            errors.append({"index": index, "reason": "duplicate_task_id"})
        else:
            parsed[value.task_id] = value
    return parsed, errors


def disclosure_delta(discovery: dict, since: str, known_receipts: list[str]) -> dict:
    """Stateless polling metadata; does not change the assessment source window."""
    known = set(known_receipts)
    matches = discovery.get("matches") or []
    eligible = [row for row in matches if (not since or (row.get("published") or "") >= since)
                and row.get("rcept_no") not in known]
    retained = sorted(known | {row["rcept_no"] for row in matches if row.get("rcept_no")})
    return {"since": since or None, "items": eligible,
            "checkpoint": {"as_of": discovery.get("as_of"),
                           "receipt_ids": retained[-10000:],
                           "older_receipts_not_in_checkpoint": max(0, len(retained) - 10000)},
            "complete": False, "monitor_created": False,
            "note": "Bounded discovery metadata only. Corrections appear as new receipts; the caller retains the checkpoint and polls when wanted. Known receipts survive partial scans. If older receipts are omitted from the 10000-item checkpoint, retain them externally or use since to avoid rediscovering them."}


async def _build_governance_screen_payload_impl(
    companies: list[str], *, as_of: str = "", governance_assessments: list[dict] | None = None,
    evidence_sources: list[dict] | None = None, review_materiality: str = "moderate",
    since: str = "", known_receipts: list[str] | None = None,
) -> dict:
    if (not isinstance(companies, list) or not 1 <= len(companies) <= 30
        or any(not isinstance(c, str) or not c.strip() for c in companies)):
        raise ValueError("companies requires 1 to 30 explicit company names or codes")
    companies = [c.strip() for c in companies]
    if len(set(companies)) != len(companies):
        raise ValueError("Duplicate company queries are not allowed")
    as_of = _ymd(as_of, default_today=True)
    if since:
        since = _ymd(since)
        if since > as_of:
            raise ValueError("since must not be after as_of")
    if review_materiality not in _MATERIALITY:
        raise ValueError("review_materiality must be high, moderate or low")
    known_receipts = known_receipts or []
    if (not isinstance(known_receipts, list) or len(known_receipts) > 10000
        or any(not isinstance(r, str) or not re.fullmatch(r"\d{14}", r) for r in known_receipts)):
        raise ValueError("known_receipts requires at most 10000 DART receipt identifiers")
    assessments, submission_errors = _prepare_assessments(governance_assessments)
    if evidence_sources is not None and (not isinstance(evidence_sources, list) or len(evidence_sources) > 30):
        raise ValueError("evidence_sources requires at most 30 company source groups")
    groups, invalid_groups = {}, set()
    for index, group in enumerate(evidence_sources or []):
        if (not isinstance(group, dict) or set(group) != {"company", "sources"}
            or group.get("company") not in companies or not isinstance(group.get("sources"), list)):
            submission_errors.append({"source_group_index": index, "reason": "invalid_company_source_group"})
            continue
        query = group["company"]
        if query in groups:
            invalid_groups.add(query)
        groups[query] = group["sources"]
    client = get_dart_client()
    rows, matched = [], set()
    # Shared DartClient limits remain authoritative. A batch deliberately visits
    # companies sequentially so 30 firms cannot multiply discovery concurrency.
    for query in companies:
        row = {"query": query, "human_reviewed": False, "scope_complete": False}
        try:
            resolution = await resolve_company_query(query)
            if resolution.status in {"ambiguous", "error"} or not resolution.selected:
                warning = (company_ambiguous_warning(query, resolution.candidates)
                           if resolution.status == "ambiguous" else company_not_found_warning(query))
                row.update(status="company_unresolved", candidates=[{
                    key: candidate.get(key, "") for key in ("corp_name", "corp_code", "stock_code")
                } for candidate in resolution.candidates[:10]],
                           warnings=[warning], next_action=COMPANY_LOOKUP_NEXT_ACTION)
                rows.append(row)
                continue
            company = resolution.selected
            discovery = await discover_guideline_context(client, company["corp_code"], as_of,
                                                         include_meeting_results=True, allow_same_day=True)
            supplemental, source_errors = [], []
            if query in invalid_groups:
                source_errors.append("duplicate_company_source_group")
            elif query in groups:
                try:
                    supplemental = await collect_supplemental_sources(client, groups[query], as_of)
                except Exception:
                    source_errors.append("invalid_or_unavailable_supplemental_sources")
            task = build_governance_task(company, as_of, discovery, supplemental, review_materiality)
            # A failed requested source group must affect identity, rather than
            # silently accepting a task produced from a smaller source request.
            if source_errors:
                task["source_request_errors"] = source_errors
                task["task_id"] = "governance:" + _digest({k: v for k, v in task.items() if k != "task_id"})
            assessment = assessments.get(task["task_id"])
            accepted = None
            if assessment:
                matched.add(task["task_id"])
                accepted = accept_governance_assessment(task, assessment)
            row.update(status="assessed" if accepted and accepted["status"] == "accepted_unreviewed" else "assessment_pending",
                       company=task["company"], assessment_task=task,
                       assessment=accepted, triage=governance_triage(accepted, review_materiality),
                       delta=disclosure_delta(discovery, since, known_receipts), source_request_errors=source_errors)
        except Exception:
            # Do not leak credential-bearing upstream exceptions or invent a
            # negative signal from the failed company acquisition.
            row.update(status="company_failed", error="company_acquisition_failed",
                       next_action="Retry this company separately; completed peers remain usable.")
        rows.append(row)
    unmatched = sorted(set(assessments) - matched)
    return {"tool": "governance_screen", "contract_version": CONTRACT_VERSION,
            "status": "partial" if any(r["status"] in {"company_unresolved", "company_failed"} for r in rows) else "ok",
            "as_of": as_of, "companies": rows, "summary": dict(Counter(r["status"] for r in rows)),
            "assessment_schema": GovernanceAssessment.model_json_schema(),
            "submission_errors": submission_errors, "unmatched_task_ids": unmatched,
            "human_reviewed": False, "scope_complete": False,
            "notice": "LLM 평가 · 사람 미검토. 제한된 공개공시 범위의 거버넌스 검토 순서이며 의결권 찬반이나 실제 투표가 아닙니다.",
            "next_action": "Read each assessment_task, extend relevant source windows if needed, then call governance_screen again with the same inputs and governance_assessments. Missing checks may be skipped individually.",
            "execution": {"company_limit": 30, "company_concurrency": 1, "persistent_results": False,
                          "scheduled": False, "ballots_submitted": False}}


async def build_governance_screen_payload(*args, **kwargs) -> dict:
    """Expose weak company-name matches in every completed batch response."""
    return declare_weak_resolution(await _build_governance_screen_payload_impl(*args, **kwargs))
