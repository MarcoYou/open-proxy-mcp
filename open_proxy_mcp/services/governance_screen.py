"""Stateless, source-bound governance triage for explicit company batches.

The caller LLM reads and assesses; the server validates provenance and routes
attention. Event discovery is never itself a negative governance conclusion.
"""
from __future__ import annotations

from collections import Counter
from datetime import date
from decimal import Decimal
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
from open_proxy_mcp.services.governance_patterns import PATTERNS

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

    def names(self, candidate: str) -> bool:
        # A complete name may be followed by a Korean case particle in prose.
        return bool(re.search(rf"(?<!\w){re.escape(candidate)}(?:은|는|이|가|을|를|의|와|과)?(?!\w)", self.quote))


AuditRefs = Annotated[list[GovernanceCitation], Field(min_length=1, max_length=4)]
IsoDay = Annotated[StrictStr, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")]


class EarlyAuditReelection(_Strict):
    """Evidence for the same incumbent's remaining term, resignation and separate election."""
    stage: Literal["proposed", "passed"]
    prior_term_end: IsoDay
    resignation_date: IsoDay | None = None
    prior_term_refs: AuditRefs
    resignation_refs: AuditRefs
    election_refs: AuditRefs


def _date_in_quotes(day: date, refs: AuditRefs) -> bool:
    compact = day.strftime("%Y%m%d")
    separated = rf"{day.year}\s*(?:[./-]|년)\s*0?{day.month}\s*(?:[./-]|월)\s*0?{day.day}"
    return any(re.search(rf"(?<!\d)(?:{compact}|{separated})(?!\d)", ref.quote) for ref in refs)


def _voting_cap_timing(meeting: str, previous_end: str, as_of: str) -> dict | None:
    """Compare dates only. The company's and nominee's legal scope requires source review."""
    from open_proxy_mcp.services.proxy_advise import _load_law_provisions

    provision = _load_law_provisions().get("§542의12_감사위원_합산3%_1차", {})
    promulgated = provision.get("promulgation_date")
    effective = provision.get("effective_date")
    if not effective or not promulgated or promulgated.replace("-", "") > as_of:
        return None
    return {"effective_date": effective, "between_meeting_and_prior_term_end": meeting < effective <= previous_end}


class GovernanceGap(_Strict):
    category: Category
    question: Text
    kind: Literal["missing_information", "truncated_context", "fetch_failed",
                  "conflicting_evidence", "identity_uncertain", "not_assessed"]
    next_action: Text


class SeveranceAmount(_Strict):
    """A single, explicitly denominated KRW amount in a literal disclosure quote."""
    amount_text: Annotated[StrictStr, Field(max_length=42, pattern=r"^(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+\s*(?:억|백만|천)|\s*(?:억|백만|천)?)\s*원$")]
    citation: GovernanceCitation

    def krw(self) -> int:
        from open_proxy_mcp.services.shareholder_meeting_parser import _UNIT_MULT_KRW
        number, unit = re.fullmatch(r"([\d.]+)(\D+)", re.sub(r"[\s,]", "", self.amount_text)).groups()
        amount = Decimal(number) * _UNIT_MULT_KRW[unit]
        if amount != amount.to_integral_value():
            raise ValueError("Quote a whole KRW amount")
        return int(amount)

    @model_validator(mode="after")
    def disclosed(self):
        if not re.search(r"(?<![\d,.])" + re.escape(self.amount_text), self.citation.quote) or self.krw() <= 0:
            raise ValueError("Quote the positive amount together with its currency unit")
        return self


class SeveranceComparison(_Strict):
    additional_total: SeveranceAmount
    cash_and_equivalents: SeveranceAmount | None = None
    acquisition_price: SeveranceAmount | None = None

    @model_validator(mode="after")
    def labelled_amounts(self):
        labels = [(self.additional_total, r"(?:특별|추가|별도).{0,16}(?:보상|퇴직|지급).{0,12}(?:합계|총액|총\s*금액)"),
                  (self.cash_and_equivalents, r"현금\s*및\s*현금성\s*자산"),
                  (self.acquisition_price, r"(?:인수|취득|양수)\s*(?:가액|대금|금액|가격)")]
        for amount, label in labels:
            if amount and not re.search(label + r"[\s():：은는이가의,·약총계액합]*" + re.escape(amount.amount_text),
                                        amount.citation.quote):
                raise ValueError("Quote the corresponding total, cash or transaction label beside the amount")
        return self


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
    event_date: IsoDay | None = None
    case_or_round: Text | None = None
    ownership_basis: Literal["not_applicable", "contractual", "settled", "unknown"] = "not_applicable"
    voting_rights: Literal["not_applicable", "confirmed", "unresolved"] = "not_applicable"
    relevance: Text
    candidate_name: Text | None = None
    early_audit_reelection: EarlyAuditReelection | None = None
    severance_comparison: SeveranceComparison | None = None
    pattern: Literal["seat_wording", "retirement_limit", "control_severance", "conditional_ballot", "bundled_amendments", "staggered_terms", "allotment_authority",
                     "allotment_after_amendment", "third_party_allotment", "convertible_bond", "exchangeable_bond"] | None = None
    risk_basis: Literal["substantive_conduct", "event_presence_only", "not_applicable", "unknown"]
    rationale: Text
    evidence_refs: Annotated[list[GovernanceCitation], Field(max_length=12)]
    counterevidence: Annotated[list[GovernanceCitation], Field(max_length=12)] = Field(default_factory=list)
    gaps: Annotated[list[GovernanceGap], Field(max_length=12)] = Field(default_factory=list)

    @model_validator(mode="after")
    def coherent_reelection(self):
        case = self.early_audit_reelection
        if case:
            if not self.event_date or not self.candidate_name or not self.candidate_name.strip():
                raise ValueError("An early reelection needs the candidate and meeting day")
            meeting = date.fromisoformat(self.event_date)
            term_end = date.fromisoformat(case.prior_term_end)
            if term_end <= meeting or not _date_in_quotes(term_end, case.prior_term_refs):
                raise ValueError("Cite the prior term ending after the meeting")
            if not _date_in_quotes(meeting, case.election_refs):
                raise ValueError("Cite the meeting day in the notice or result")
            if case.resignation_date:
                resigned = date.fromisoformat(case.resignation_date)
                if resigned > meeting or not _date_in_quotes(resigned, case.resignation_refs):
                    raise ValueError("Cite a resignation by the meeting day")
            if case.stage == "passed" and (self.fact_status != "disclosed_fact" or not case.resignation_date):
                raise ValueError("A passed reelection requires an effective resignation and a disclosed result")
            if case.stage == "proposed" and self.fact_status != "conditional_plan":
                raise ValueError("A proposed election is a conditional plan")
            if any(g.kind in {"identity_uncertain", "conflicting_evidence"} for g in self.gaps):
                raise ValueError("Resolve candidate and source conflicts before marking a reelection")
            for refs in (case.prior_term_refs, case.resignation_refs, case.election_refs):
                if not any(ref.names(self.candidate_name) for ref in refs):
                    raise ValueError("Identify the same candidate in each cited event")
        return self

    @model_validator(mode="after")
    def coherent_fact_status(self):
        if self.pattern and not self.evidence_refs:
            raise ValueError("A governance pattern requires a literal source citation")
        if self.severance_comparison and self.pattern != "control_severance":
            raise ValueError("A severance comparison requires a change-of-control compensation finding")
        if self.pattern == "allotment_after_amendment" and len({r.source_id for r in self.evidence_refs}) < 2:
            raise ValueError("Cite both the charter change and the subsequent allotment")
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
    "정관·발행 검토는 pattern을 사용한다. 원문 단서는 검토 후보이며 회사의 사유·반대 근거·주주 영향과 안건 또는 발행 회차를 따로 인용한다.",
    *[label + " · " + check for label, check in PATTERNS.values()],
    "임원 특별퇴직금은 변동만으로 지급되는지 해임 등 추가 조건이 필요한지 확인한다. 분쟁 개시일과 배수 인상·수혜자 확대의 제안일, 가결일을 각각 인용한다. 조건부 금액과 실제 지급액을 구별한다. 대상 인원·산식·총액을 확보한 경우만 합산하고 회사 현금및현금성자산과 공시된 인수가격에 대비한다. 분모의 기준일·주체·통화·취득 지분을 맞추고 자료가 빠지면 해당 비율은 미확인으로 남긴다.",
    "severance_comparison에는 공시에 금액과 원화 단위가 함께 적힌 추가 보상 총액과 같은 거래의 인수대금 또는 회사 현금및현금성자산을 각각 인용한다. 개인별 금액과 이사 보수한도는 총액으로 쓸 수 없다.",
    "allotment_after_amendment는 같은 회사의 정관 권한 확대가 가결·시행된 뒤 해당 조항에 근거해 배정했는지 날짜와 조문을 대조한다. 소집공고와 발행결정만으로 시행·납입을 단정하지 않는다.",
    "CB·EB의 발행결정, 정정, 철회, 납입, 권리행사, 주식 교부를 구별한다. 자기주식 EB와 타사주식 EB를 구별하고 대상·관계가 불명확하면 미확인으로 둔다. 잠재 주식 비율을 실현 희석이나 행사 의결권으로 계산하지 않는다.",
    "Read the source excerpts and use their next-reading handles when context is truncated. Parsing failure is not source absence.",
    "Assess minority treatment, concrete related-party conflicts, board accountability, disclosure reliability and control procedures. Other is available for a material issue outside these checks.",
    "Tender offers, activism, shareholder proposals and litigation are not adverse findings by themselves. Identify the concrete conduct, affected shareholder interest and why it matters.",
    "Separate issuer or proponent claims, disclosed transaction terms, applications and court rulings. An interim dismissal is not a final merits judgment. Read conditions, objections and appeals.",
    "Preserve receipt date, event date, correction lineage and separate case/offer rounds. Contractual or deemed holdings do not establish completed acquisition or exercisable voting rights.",
    "Relate each fact to this company or a named candidate. Do not infer a hostile camp or employment relationship from names, nominations or activist presence.",
    "Retain counterevidence. Unknown dates do not erase an otherwise disclosed role. Report missing checks individually and continue the remaining checks and companies.",
    "근로복지기금 자사주 출연은 screener의 기금출연 보기에서 찾은 결정·결과 접수번호를 대조한다. 주식 이전과 처분대금의 현금 출연, 결정과 실제 수령을 구별하고 다른 상대방 몫을 빼고 읽는다. 유·무상, 정정·철회, 의결권 행사 주체와 주주에게 미친 영향을 공시로 확인할 수 있을 때만 적는다. 기금 출연 사실만으로 부정 판단이나 반대 권고를 만들지 않는다.",
    "임기 중 감사위원 분리선출은 후보별로 early_audit_reelection에 기록한다. 동일 회사·동일인(생년월·경력 대조)의 종전 감사위원 재직과 주총 전에 공시한 기존 임기만료일, 그 임기 중 사임과 이번 분리선출의 연결, 주총일과 해당 안건을 각각 인용한다. 이사·감사 직함만으로 종전 감사위원 재직을 추정하지 않는다. 정정·철회·부결·미취임을 확인한다. 날짜나 신원이 미확인이면 표식을 생략하고 gaps에 남긴다.",
    "소집공고·사임 예정만 있으면 proposed, 실제 사임일 및 해당 안건 가결을 확인하면 passed다. 회사가 밝힌 사유와 주주 선택권에 미친 영향을 따로 적는다. 주총일 당시의 사외이사 여부와 의결권 제한을 확인한다. 발행주식 기준·행사주식 기준 찬성률과 최대주주 측 지분만으로 합산 적용 시 표결 결과나 회피 의도를 추정하지 않는다. 이 절차 표식 자체는 supported_risk의 근거가 아니다.",
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
    pattern_sources = {}
    reports = {r["rcept_no"]: r for r in discovery.get("matches", []) if r.get("rcept_no")}
    for item in [*discovery.get("filings", []), *(supplemental or [])]:
        if item.get("status") != "read" or not item.get("text"):
            continue
        published = item.get("published") or item.get("rcept_no", "")[:8]
        if published and published > as_of:
            continue
        packet = build_source_packet(item)
        if packet and packet.get("source_id"):
            if item.get("pattern_signals"):
                terms = []
                for signal in item["pattern_signals"]:
                    fields = signal.get("terms", {})
                    terms += [p["quote"] for p in signal.get("provisions", [])]
                    terms += [signal.get("quote", ""), fields.get("target_stock", ""), fields.get("charter_basis", "")]
                    terms += [c["after"] for c in signal.get("clauses", [])]
                    terms += [q["quote"] for q in signal.get("nominee_terms", [])]
                    for b in signal.get("ballots", []):
                        terms.extend(b["clauses_and_topics"])
                focus = list(dict.fromkeys(t[:80] for t in terms if t))[:6]
                pattern_sources[packet["source_id"]] = {
                    "source_id": packet["source_id"], "source_url": packet["source_url"], "published": published,
                    "report_name": item.get("report_nm") or reports.get(item.get("rcept_no"), {}).get("report_nm"),
                    "signals": item["pattern_signals"],
                    "read_evidence": {**packet["read_next"]["source_request"], "text_offset": 0, "focus_terms": focus}}
            indexed[packet["source_id"]] = {**packet, "published": published,
                "report_name": item.get("report_nm"), "family": item.get("family"),
                "document_role": item.get("document_role"),
                "same_day_time_unverified": published == as_of}
    coverage = {key: discovery.get(key) for key in (
        "status", "scans", "matches", "complete_history", "next_reads", "budget", "hint", "temporal_scope")}
    sources = list(indexed.values())
    patterns = list(pattern_sources.values())
    issuer_filings = [p for p in patterns if not re.search(r"(?:종속|자)회사(?:의)?\s*주요경영사항",
                                                          p.get("report_name") or "")]
    authorities = [p for p in issuer_filings if any(s["pattern"] == "allotment_authority" for s in p["signals"])]
    issues = [p for p in issuer_filings if any(s["pattern"] == "third_party_allotment" for s in p["signals"])]
    pairs = [{"amendment": a["source_id"], "allotment": i["source_id"]} for a in authorities for i in issues
             if a["published"] < i["published"]]
    disputes = [r for r in reports.values() if "경영권분쟁" in re.sub(r"\s+", "", r.get("report_nm", ""))
                and r.get("family") == "litigation" and r.get("document_role") == "제기신청"]
    reviews = []
    for p in issuer_filings:
        provisions = [v for s in p["signals"] if s["pattern"] == "control_severance" for v in s.get("provisions", [])]
        if not any(v["change"] == "new" or v["change_notes"] for v in provisions):
            continue
        report = reports.get(p["source_id"].removeprefix("filing:"))
        if not report or report.get("is_correction") or "정정" in (p.get("report_name") or ""):
            continue
        for r in disputes:
            if r.get("published") and r["published"] < p["published"]:
                reviews.append({"dispute_receipt": r["rcept_no"], "dispute_published": r["published"],
                                "amendment": p["source_id"], "amendment_published": p["published"]})
    task = {"contract_version": CONTRACT_VERSION,
            "company": {key: company.get(key, "") for key in ("corp_code", "corp_name", "stock_code")},
            "as_of": as_of, "review_materiality": review_materiality,
            "sources": sources, "discovery": coverage,
            "pattern_sources": patterns, "allotment_followups": pairs, "severance_dispute_followups": reviews,
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
    reelections = set()
    for finding in assessment.findings:
        if finding.event_date and finding.event_date.replace("-", "") > task["as_of"] and finding.fact_status != "conditional_plan":
            errors.append("future_event_not_marked_conditional")
        refs = [*finding.evidence_refs, *finding.counterevidence]
        if comp := finding.severance_comparison:
            refs.extend(a.citation for a in (comp.additional_total, comp.cash_and_equivalents, comp.acquisition_price) if a)
        if finding.pattern == "allotment_after_amendment" and finding.disposition != "unresolved":
            timed = [(r.source_id, sources.get(r.source_id, {}).get("published", ""), re.sub(r"\s+", "", r.quote))
                     for r in finding.evidence_refs]
            authority_dates = [day for _, day, q in timed if day and re.search(r"정관|신주인수권", q)
                               and re.search(r"가결|승인|개정(?:하였|되었)|시행(?:하였|되었)", q)
                               and not re.search(r"(?:가결|승인)(?:될|시|예정|되지않|을?요청)|부결", q)]
            issue_sources = {source for source, day, q in timed if re.search(r"제3자배정|제3자에게배정", q)
                             and any(a < day for a in authority_dates)}
            if not issue_sources:
                errors.append("missing_ordered_charter_and_allotment_evidence")
            if finding.impact_state == "observed" and not any(
                source in issue_sources and re.search(r"납입.{0,16}(?:완료|되었|이루어)|(?:신주|주식).{0,16}발행(?:하였|되었)", q)
                and not re.search(r"납입.{0,16}(?:예정|미완료|완료되지않|완료시|완료후)", q) for source, _, q in timed
            ):
                errors.append("missing_allotment_execution_evidence")
        if case := finding.early_audit_reelection:
            refs.extend([*case.prior_term_refs, *case.resignation_refs, *case.election_refs])
            meeting_day = finding.event_date.replace("-", "")
            identity = (finding.candidate_name.strip(), meeting_day)
            if identity in reelections:
                errors.append("duplicate_candidate_reelection")
            reelections.add(identity)
            prior_refs = [ref for ref in case.prior_term_refs if ref.names(finding.candidate_name)
                          and "" < sources.get(ref.source_id, {}).get("published", "") < meeting_day]
            if not _date_in_quotes(date.fromisoformat(case.prior_term_end), prior_refs):
                errors.append("missing_pre_meeting_term_source")
            if case.stage == "passed":
                if not any(ref.names(finding.candidate_name) and
                           sources.get(ref.source_id, {}).get("published", "") >= meeting_day
                           for ref in case.election_refs):
                    errors.append("reelection_result_predates_meeting")
                resigned = date.fromisoformat(case.resignation_date)
                actual_refs = [ref for ref in case.resignation_refs if ref.names(finding.candidate_name)
                               and sources.get(ref.source_id, {}).get("published", "") >= resigned.strftime("%Y%m%d")]
                if not _date_in_quotes(resigned, actual_refs):
                    errors.append("missing_effective_resignation_source")
        for citation in refs:
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
    for finding, validated in zip(result["findings"], assessment.findings):
        if comp := validated.severance_comparison:
            total = comp.additional_total.krw()
            finding["severance_comparison"].update(
                additional_total_krw=total,
                pct_of_cash=round(100 * total / comp.cash_and_equivalents.krw(), 2) if comp.cash_and_equivalents else None,
                pct_of_acquisition_price=round(100 * total / comp.acquisition_price.krw(), 2) if comp.acquisition_price else None)
        if case := finding.get("early_audit_reelection"):
            case["remaining_days_at_meeting"] = (
                date.fromisoformat(case["prior_term_end"]) - date.fromisoformat(finding["event_date"])
            ).days
            case["voting_cap_change"] = _voting_cap_timing(finding["event_date"], case["prior_term_end"], task["as_of"])
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
    procedures = [f["finding_id"] for f in findings if f.get("early_audit_reelection")
                  and _MATERIALITY[f["materiality"]] >= _MATERIALITY[review_materiality]]
    # A partial no-adverse conclusion must not hide its own material conflict.
    # Inspect declared gaps independently of the caller's disposition label.
    context_needed = [f for f in findings
                      if _MATERIALITY[f["materiality"]] >= _MATERIALITY[review_materiality]
                      and any(g["kind"] in {"conflicting_evidence", "identity_uncertain"} for g in f["gaps"])]
    if actionable:
        disposition = "priority_review" if any(f["materiality"] == "high" for f in actionable) else "review"
    elif context_needed:
        disposition = "needs_evidence"
    elif procedures:
        disposition = "procedure_review"
    elif risks:
        disposition = "monitor"
    elif any(f["disposition"] == "no_adverse_signal" for f in findings):
        disposition = "no_adverse_signal_in_reviewed_scope"
    else:
        disposition = "not_assessed"
    return {**base, "disposition": disposition,
            "supported_risk_ids": [f["finding_id"] for f in risks],
            "attention_finding_ids": [f["finding_id"] for f in actionable],
            "procedure_review_finding_ids": procedures,
            "context_followup_finding_ids": [f["finding_id"] for f in context_needed],
            "unresolved_finding_ids": [f["finding_id"] for f in findings if f["disposition"] == "unresolved"],
            "skipped_check_count": len(accepted.get("skipped_checks", [])) + len(accepted.get("implicit_skipped_checks", [])),
            "reason": "Attention follows cited findings and the selected materiality threshold. Procedure review flags the election timeline for inspection."}


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
    since: str = "", known_receipts: list[str] | None = None, pattern_review: bool | Literal["퇴직금", "이사정원", "정관"] = False,
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
                                                         include_meeting_results=True, allow_same_day=True,
                                                         include_officer_changes=True, review_patterns=pattern_review)
            supplemental, source_errors = [], []
            if query in invalid_groups:
                source_errors.append("duplicate_company_source_group")
            elif query in groups:
                try:
                    options = {"review_titles": {r["rcept_no"]: r["report_nm"] for r in discovery.get("matches", [])}} if pattern_review else {}
                    if pattern_review == "정관":
                        options["charter_view"] = True
                    supplemental = await collect_supplemental_sources(client, groups[query], as_of, **options)
                except Exception:
                    source_errors.append("invalid_or_unavailable_supplemental_sources")
            task = build_governance_task(company, as_of, discovery, supplemental, review_materiality)
            if pattern_review == "정관":
                from .charter_vote_view import charter_meetings
                row["charter_meetings"] = charter_meetings([*discovery.get("filings", []), *supplemental])
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
