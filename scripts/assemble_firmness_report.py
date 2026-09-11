#!/usr/bin/env python3
"""Assemble a user-authorized comparison without modifying raw inputs.

Usage: python3 scripts/assemble_firmness_report.py REPORT_DIRECTORY [--issues FILE]

Reads case-manifest.json, packets/, records/, reviews/, optional issues.json and
source-update followups/. Follow-ups remain outside primary inventory counters.
Writes report.json, report.md, validation.json. Exit 2 means validation failures
were faithfully retained as incomplete results; exit 1 means assembly failed.
Run render_firmness_report.py report.json index.html separately for HTML.

Optional issues input is either an array of renderer issue objects or an object
with an "issues" array. Findings and fixed/verified status are never invented.
This program does not call a model, MCP, network, or submit a ballot. Raw review
files are read only. Its exact-quote checks do not establish semantic accuracy.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import copy
from datetime import date, datetime, timedelta
import hashlib
from html import escape
import json
from pathlib import Path
import re
import sys
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from render_firmness_report import (
    DECISION, ROW_KIND, ISSUE_STATUS, MEETING_TYPE, changed, decision_counts, native_changed, pair_kind,
    safe_url, validate as validate_report,
)

ARMS = {"0.1": "low", "0.9": "high"}
KST = ZoneInfo("Asia/Seoul")
WIKI = "../../wiki/decisions/opm-guideline-roadmap.md"
PRIOR_CENSUS_COMPANIES = {
    "가비아", "골프존홀딩스", "태광산업", "영풍", "영원무역",
    "LG화학", "삼성전자", "KT&G", "SK하이닉스",
}


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def without_firmness(packet: dict) -> dict:
    value = copy.deepcopy(packet)
    value.pop("packet_id", None)
    # Arm-specific response paths/run IDs are validated against their own records.
    value.pop("supplemental_reading_provenance", None)
    for keys in (("settings", "firmness"), ("policy", "workflow_settings", "firmness"),
                 ("guidance", "decision_guidance", "value")):
        parent = value
        for key in keys[:-1]:
            parent = parent.get(key, {}) if isinstance(parent, dict) else {}
        if isinstance(parent, dict):
            parent.pop(keys[-1], None)
    return value


def normalized_sources(sources: list) -> list:
    result = copy.deepcopy(sources)
    for source in result:
        texts = list(dict.fromkeys(source.get("excerpts", [])))
        source["excerpts"] = [text for text in texts if not any(text != other and text in other for other in texts)]
        source.pop("read_windows", None)
    return result


def sources_from_records(audit, case, arm, packet, record, task):
    """Prove every report excerpt came from a bound native MCP response."""
    originals = {source["source_id"]: source for source in task.get("sources", [])}
    allowed = {sid: list(source.get("excerpts", [])) for sid, source in originals.items()}
    principal = record["response"]["data"]["guideline_harness"]
    for provenance in packet.get("supplemental_reading_provenance", []):
        relative = provenance.get("record_file", "")
        path = (audit.root / relative).resolve()
        if not path.is_relative_to((audit.root / "records/supplemental").resolve()):
            raise ValueError("supplemental record is outside records/supplemental")
        extra = audit.read(relative, case["id"], arm)
        if not isinstance(extra, dict):
            raise ValueError("supplemental record missing")
        data = extra["response"]["data"]
        extra_task = data["guideline_application"]["structure_tasks"][0]["task"]
        binding = data["guideline_harness"]
        if (extra.get("case") != case or extra.get("record_kind") != "supplemental_notice_read"
                or digest(extra["response"]) != provenance.get("response_sha256")
                or extra.get("principal_run_id") != principal.get("run_id")
                or provenance.get("principal_run_id") != principal.get("run_id")
                or binding.get("run_id") != principal.get("run_id")
                or binding.get("policy_sha256") != principal.get("policy_sha256")
                or extra.get("arguments", {}).get("guideline_evidence_sources") != provenance.get("source_request")
                or any(extra_task.get(key) != task.get(key) for key in ("execution_context", "policy", "agendas"))):
            raise ValueError("supplemental provenance/binding mismatch")
        for source in extra_task.get("sources", []):
            sid = source.get("source_id")
            original = originals.get(sid)
            if original is None:
                continue
            if any(source.get(key) != original.get(key) for key in ("document_sha256", "total_chars", "offset_basis")):
                raise ValueError("supplemental document identity mismatch")
            allowed[sid].extend(source.get("excerpts", []))
    if {source.get("source_id") for source in packet["sources"]} != set(originals):
        raise ValueError("report/native source ID sets differ")
    for source in packet["sources"]:
        sid = source["source_id"]
        original = originals[sid]
        mutable = {"excerpts", "excerpt_offsets", "partial", "read_windows"}
        if {key: value for key, value in source.items() if key not in mutable} != {key: value for key, value in original.items() if key not in mutable}:
            raise ValueError("source metadata differs from native source: " + sid)
        if not all(excerpt in allowed[sid] for excerpt in source.get("excerpts", [])):
            raise ValueError("report excerpt does not appear in principal/supplemental response: " + sid)


def md(value) -> str:
    if value is None:
        return "미확인"
    return escape(str(value), quote=False).replace("|", "\\|").replace("\n", "<br>")


def mdlink(label: str, url: str | None) -> str:
    url = safe_url(url)
    return f"[{md(label)}](<{url}>)" if url else md(label)


class Audit:
    def __init__(self, root: Path):
        self.root = root
        self.failures = []
        self.source_dates = []

    def fail(self, case_id, arm, stage, code, detail, **context):
        value = {"case_id": case_id, "arm": arm, "stage": stage, "code": code, "detail": detail, **context}
        self.failures.append(value)
        return value

    def read(self, relative: str, case_id=None, arm=None, required=True):
        path = self.root / relative
        if not path.exists():
            if required:
                self.fail(case_id, arm, "file", "missing_file", relative, path=relative)
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            self.fail(case_id, arm, "file", "unreadable_json", str(error), path=relative)
            return None


def source_date(source: dict) -> tuple[date, str]:
    explicit = source.get("published") or source.get("published_at")
    receipt = re.fullmatch(r"(?:filing|notice|annual):(\d{14})", str(source.get("source_id", "")))
    if explicit:
        text = str(explicit)
        if re.fullmatch(r"\d{8}", text):
            return datetime.strptime(text, "%Y%m%d").date(), "published"
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            return date.fromisoformat(text), "published"
        timestamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise ValueError("publication timestamp lacks timezone")
        return timestamp.astimezone(KST).date(), "published_at"
    if receipt and source.get("publisher_type") == "company_disclosure":
        rcept_no = receipt.group(1)
        source_url = source.get("source_url")
        if source_url:
            parsed = urlsplit(source_url)
            url_receipt = parse_qs(parsed.query).get("rcpNo", [])
            if parsed.hostname != "dart.fss.or.kr" or url_receipt != [rcept_no]:
                raise ValueError("DART source ID and source URL receipt differ")
        return datetime.strptime(rcept_no[:8], "%Y%m%d").date(), "receipt_date"
    raise ValueError("publication date and usable DART receipt date are missing")


def check_packet(audit: Audit, case: dict, arm: str, packet, record) -> dict:
    cid = case["id"]
    errors = []

    def fail(code, detail, **extra):
        errors.append(audit.fail(cid, arm, "packet", code, detail, **extra))

    result = {"valid": False, "errors": errors, "sources": {}, "agendas": {}, "native": {}, "record_valid": False}
    if not isinstance(packet, dict):
        fail("missing_packet", "평가에 사용한 패킷을 읽을 수 없습니다.")
        return result
    if packet.get("packet_id") != digest({key: value for key, value in packet.items() if key != "packet_id"}):
        fail("packet_digest_mismatch", "packet_id does not match packet content")
    if packet.get("contract") != "opm-all-agenda-report-review/1":
        fail("packet_contract_mismatch", str(packet.get("contract")))
    if packet.get("meeting") != case:
        fail("meeting_binding_mismatch", "packet.meeting differs from manifest")
    context = packet.get("execution_context") or {}
    if not isinstance(context, dict):
        fail("invalid_execution_context", "execution_context must be an object")
        context = {}
    for key in ("meeting_pin", "engine_bundle_sha256", "cutoff_at", "effective_as_of"):
        if not context.get(key):
            fail("missing_binding", key)
    if context.get("cutoff_at") != case.get("cutoff_at"):
        fail("cutoff_binding_mismatch", "context cutoff differs from manifest")
    try:
        cutoff = datetime.fromisoformat(case["cutoff_at"].replace("Z", "+00:00"))
        if cutoff.tzinfo is None:
            raise ValueError("cutoff lacks timezone")
        allowed = cutoff.astimezone(KST).date() - timedelta(days=1)
        if context.get("effective_as_of") != allowed.strftime("%Y%m%d"):
            fail("effective_date_mismatch", f"expected {allowed.isoformat()}")
    except (ValueError, TypeError, KeyError) as error:
        fail("invalid_cutoff", str(error))
        allowed = None
    settings = packet.get("settings") or {}
    policy = packet.get("policy") or {}
    guidance = packet.get("guidance") or {}
    for path, value in (("settings.firmness", settings.get("firmness")),
                        ("policy.workflow_settings.firmness", policy.get("workflow_settings", {}).get("firmness")),
                        ("guidance.decision_guidance.value", guidance.get("decision_guidance", {}).get("value"))):
        if type(value) not in (float, int) or value != float(arm):
            fail("firmness_mismatch", f"{path}: expected {arm}, received {value}")
    agendas = packet.get("agendas")
    if not isinstance(agendas, list) or not agendas:
        fail("missing_agenda_inventory", "packet.agendas must be a nonempty array")
        agendas = []
    for agenda in agendas:
        if not isinstance(agenda, dict) or not isinstance(agenda.get("agenda_id"), str) or not agenda["agenda_id"]:
            fail("invalid_agenda_inventory", "agenda requires nonempty string agenda_id")
            continue
        aid = agenda["agenda_id"]
        if aid in result["agendas"]:
            fail("duplicate_packet_agenda", aid, agenda_id=aid)
        result["agendas"][aid] = agenda
    sources = packet.get("sources")
    if not isinstance(sources, list) or not sources:
        fail("missing_sources", "packet.sources must be a nonempty array")
        sources = []
    if packet.get("source_hash") != digest(sources):
        fail("source_digest_mismatch", "source_hash does not match sources")
    for source in sources:
        if not isinstance(source, dict) or not isinstance(source.get("source_id"), str):
            fail("invalid_source", "source requires a string source_id")
            continue
        sid = source["source_id"]
        if sid in result["sources"]:
            fail("duplicate_source_id", sid, source_id=sid)
        result["sources"][sid] = source
        excerpts = source.get("excerpts")
        if not isinstance(excerpts, list) or not excerpts or not all(isinstance(text, str) and text for text in excerpts):
            fail("invalid_excerpts", "source excerpts must contain nonempty strings", source_id=sid)
        try:
            published, basis = source_date(source)
            audit.source_dates.append({"case_id": cid, "arm": arm, "source_id": sid,
                                       "published_date": published.isoformat(), "date_basis": basis,
                                       "latest_allowed_date": allowed.isoformat() if allowed else None})
            if allowed is None or published > allowed:
                fail("source_after_cutoff", f"{published.isoformat()} > {allowed}", source_id=sid)
        except (ValueError, TypeError) as error:
            fail("unverified_source_date", str(error), source_id=sid)
    if not isinstance(record, dict):
        fail("missing_native_record", "MCP record is missing or unreadable")
    else:
        record_errors = len(errors)
        response = record.get("response") or {}
        data = response.get("data") or {}
        if record.get("case") != case:
            fail("record_case_mismatch", "record.case differs from manifest")
        tasks = (data.get("guideline_application") or {}).get("structure_tasks") or []
        task = tasks[0].get("task", {}) if tasks and isinstance(tasks[0], dict) else {}
        for key in ("agendas", "policy", "execution_context"):
            if task.get(key) != packet.get(key):
                fail("packet_record_mismatch", key)
        try:
            sources_from_records(audit, case, arm, packet, record, task)
        except (TypeError, AttributeError, KeyError, ValueError) as error:
            fail("packet_record_mismatch", str(error))
        arguments = record.get("arguments") or {}
        if arguments.get("guideline_workflow") != settings:
            # Requests omit server-resolved defaults; every requested setting must match.
            requested = arguments.get("guideline_workflow") or {}
            if not requested or any(settings.get(key) != value for key, value in requested.items()):
                fail("request_settings_mismatch", "requested workflow differs from packet settings")
        request_pin = arguments.get("guideline_harness") or {}
        if request_pin.get("cutoff_at") != case.get("cutoff_at") or request_pin.get("notice_rcept_no") != case.get("notice_rcept_no"):
            fail("request_pin_mismatch", "MCP request cutoff or notice differs from manifest")
        harness = data.get("guideline_harness") or {}
        if any(harness.get(key) != value for key, value in context.items()):
            fail("native_binding_mismatch", "native harness differs from packet execution context")
        if harness.get("ballots_submitted") != 0:
            fail("ballot_status_not_zero", "native ballots_submitted must explicitly be 0")
        for count_key in ("assessment_counts", "structure_assessment_counts"):
            if (harness.get(count_key) or {}).get("accepted_unreviewed") != 0:
                fail("native_submission_detected_or_unknown", count_key)
        if arguments.get("guideline_assessments") or arguments.get("guideline_structure"):
            fail("native_submission_detected", "request includes assessment input")
        if response.get("status") in (None, "error", "failed") or data.get("no_filing") is True:
            fail("native_response_incomplete", str(response.get("status")))
        rows = data.get("agenda_decisions")
        if not isinstance(rows, list):
            fail("missing_native_rows", "response.data.agenda_decisions must be an array")
            rows = []
        for row in rows:
            if not isinstance(row, dict) or not row.get("agenda_id"):
                fail("invalid_native_row", "native row lacks agenda_id")
                continue
            aid = row["agenda_id"]
            if aid in result["native"]:
                fail("duplicate_native_row", aid, agenda_id=aid)
            result["native"][aid] = row
            expected = result["agendas"].get(aid)
            if expected is None or (row.get("agenda_title"), row.get("agenda_category")) != (expected.get("title"), expected.get("category")):
                fail("native_agenda_alignment_mismatch", aid, agenda_id=aid)
            if row.get("decision") not in DECISION:
                fail("invalid_native_decision", str(row.get("decision")), agenda_id=aid)
        if set(result["native"]) != set(result["agendas"]):
            fail("native_agenda_set_mismatch", "native and packet agenda IDs differ")
        result["record_valid"] = len(errors) == record_errors
    result["valid"] = not errors
    return result


def check_evidence(audit, cid, arm, stage, evidence, sources, agenda_id=None):
    errors, mapped = [], []
    if not isinstance(evidence, list) or not evidence:
        errors.append(audit.fail(cid, arm, stage, "missing_exact_quote", "at least one source quotation is required", agenda_id=agenda_id))
        return errors, mapped
    for index, item in enumerate(evidence):
        sid = item.get("source_id") if isinstance(item, dict) else None
        quote = item.get("quote") if isinstance(item, dict) else None
        source = sources.get(sid) if isinstance(sid, str) else None
        code = None
        if source is None:
            code = "unknown_evidence_source"
        elif not isinstance(quote, str) or not quote.strip():
            code = "empty_evidence_quote"
        elif not any(quote in excerpt for excerpt in source.get("excerpts", []) if isinstance(excerpt, str)):
            code = "quote_not_exact_substring"
        if code:
            errors.append(audit.fail(cid, arm, stage, code, f"evidence[{index}]", agenda_id=agenda_id,
                                     source_id=sid, quote=quote))
        else:
            mapped.append({"source_id": sid, "label": sid, "quote": quote, "url": source.get("source_url")})
    return errors, mapped


def inventory_title(title):
    """Only remove reporting markers, a leading fiscal label, and excess space."""
    title = re.sub(r"\(\s*보고사항\s*\)", "", title)
    title = re.sub(r"^\s*제\s*\d+\s*기\s*", "", title)
    return re.sub(r"\s+", " ", title).strip()


def reporting_context_proof(title, evidence, sources, notice_rcept_no):
    canonical = inventory_title(title)
    notice_ids = {"notice:" + notice_rcept_no, "filing:" + notice_rcept_no}
    cited_ids = {item["source_id"] for item in evidence} & notice_ids
    if not canonical or canonical in {"보고", "보고사항"}:
        return False
    for sid in cited_ids:
        for excerpt in sources[sid].get("excerpts", []):
            for heading in re.finditer(r"보고사항\s*[:：]", excerpt):
                section = excerpt[heading.end():heading.end() + 2000]
                following = re.search(r"(?:의결사항|의결안건|부의안건|부의사항|결의사항)", section)
                if following is None:
                    continue
                section = re.sub(r"\s+", " ", section[:following.start()])
                if canonical in section:
                    return True
    return False


def combine_missing_agendas(items):
    combined = {}
    for item in items:
        canonical = inventory_title(item["title"])
        group = combined.setdefault(canonical, {"title": canonical, "aliases": [], "arms": [], "evidence": [],
                                               "validated": False, "kind": "unclear", "observations": []})
        group["observations"].append(item)
        for key, value in (("aliases", item["title"]), ("arms", item["arm"])):
            if value not in group[key]:
                group[key].append(value)
        for evidence in item["evidence"]:
            if evidence not in group["evidence"]:
                group["evidence"].append(evidence)
        group["validated"] = group["validated"] or item["validated"]
        if item["validated"] and item["kind"] == "report":
            group["kind"] = "report"
    return list(combined.values())


def check_review(audit: Audit, case: dict, arm: str, packet, checked: dict, review) -> dict:
    cid = case["id"]
    global_errors, row_errors = [], defaultdict(list)
    result = {"rows": {}, "evidence": {}, "errors": row_errors, "global_errors": global_errors,
              "missing_agendas": [], "issues": [], "complete": False}

    def fail(code, detail):
        global_errors.append(audit.fail(cid, arm, "review", code, detail))

    if not isinstance(review, dict):
        fail("missing_review", "review JSON is missing, unreadable, or not an object")
        return result
    if not isinstance(packet, dict) or review.get("packet_id") != packet.get("packet_id"):
        fail("review_packet_id_mismatch", "review packet_id must exactly match its arm's packet")
    if not isinstance(review.get("evaluator"), str) or not review["evaluator"].strip():
        fail("missing_evaluator_label", "evaluator must be a nonempty string; label is not identity authentication")
    judgments = review.get("judgments")
    if not isinstance(judgments, list):
        fail("invalid_judgments", "judgments must be an array")
        judgments = []
    seen = Counter()
    for row in judgments:
        if not isinstance(row, dict) or not isinstance(row.get("agenda_id"), str):
            fail("invalid_review_row", "judgment lacks string agenda_id")
            continue
        aid = row["agenda_id"]
        seen[aid] += 1
        if aid not in checked["agendas"]:
            fail("unexpected_review_agenda", aid)
            continue
        result["rows"][aid] = row
        for key, valid in (("decision", isinstance(row.get("decision"), str) and row["decision"] in DECISION),
                           ("row_kind", isinstance(row.get("row_kind"), str) and row["row_kind"] in ROW_KIND),
                           ("reason", isinstance(row.get("reason"), str) and bool(row["reason"].strip())),
                           ("conditional_on", "conditional_on" in row and (row["conditional_on"] is None or isinstance(row["conditional_on"], str)))):
            if not valid:
                row_errors[aid].append(audit.fail(cid, arm, "row", "invalid_" + key, str(row.get(key)), agenda_id=aid))
        for key in ("skipped_checks", "unknowns"):
            if not isinstance(row.get(key), list) or not all(isinstance(item, str) for item in row[key]):
                row_errors[aid].append(audit.fail(cid, arm, "row", "invalid_" + key, "must be an array of strings", agenda_id=aid))
        evidence_errors, mapped = check_evidence(audit, cid, arm, "row", row.get("evidence"), checked["sources"], aid)
        row_errors[aid].extend(evidence_errors)
        result["evidence"][aid] = mapped
    for aid in checked["agendas"]:
        if seen[aid] != 1:
            row_errors[aid].append(audit.fail(cid, arm, "row", "agenda_count_not_one", f"expected 1, received {seen[aid]}", agenda_id=aid))
    missing = review.get("discovered_missing_agendas")
    if not isinstance(missing, list):
        fail("invalid_missing_agendas", "discovered_missing_agendas must be an array")
        missing = []
    for item in missing:
        if not isinstance(item, dict) or not isinstance(item.get("title"), str) or not item["title"].strip():
            fail("invalid_missing_agenda", "discovered item requires a nonempty title")
            continue
        errors, evidence = check_evidence(audit, cid, arm, "discovered_agenda", item.get("evidence"), checked["sources"])
        explicit_kind = item.get("row_kind")
        if explicit_kind is not None and (not isinstance(explicit_kind, str) or explicit_kind not in ROW_KIND):
            errors.append(audit.fail(cid, arm, "discovered_agenda", "invalid_missing_agenda_row_kind", str(explicit_kind)))
        # Older outputs can omit row_kind and quote only one item from a list.
        # Verify the explicit reporting section in the cited original notice.
        report_context = reporting_context_proof(item["title"], evidence, checked["sources"], case["notice_rcept_no"])
        missing_kind = "report" if explicit_kind == "report" or (explicit_kind is None and report_context) else "unclear"
        result["missing_agendas"].append({"title": item["title"], "evidence": evidence, "arm": arm,
            "validated": not errors, "kind": missing_kind, "reviewer_row_kind": explicit_kind})
    issues = review.get("issues")
    if not isinstance(issues, list) or not all(isinstance(item, str) for item in issues):
        fail("invalid_reviewer_issues", "issues must be an array of strings")
    else:
        result["issues"] = issues
    result["complete"] = checked["valid"] and not global_errors and not any(row_errors.values())
    return result


def compare_pair(audit, case, arms):
    packets = [arms[arm]["packet"] for arm in ARMS]
    controls = False
    native_controls = False
    if all(isinstance(packet, dict) for packet in packets):
        low, high = packets
        controls = all(arms[arm]["packet_check"]["valid"] for arm in ARMS) and without_firmness(low) == without_firmness(high)
        if without_firmness(low) != without_firmness(high):
            differing = [key for key in without_firmness(low) if without_firmness(low)[key] != without_firmness(high).get(key)]
            audit.fail(case["id"], None, "pair", "uncontrolled_packet_difference", ", ".join(differing))
        if controls:
            records = [arms[arm]["record"] for arm in ARMS]
            arguments = [copy.deepcopy(record.get("arguments", {})) for record in records]
            for argument in arguments:
                argument.get("guideline_workflow", {}).pop("firmness", None)
            harnesses = [record["response"]["data"]["guideline_harness"] for record in records]
            shared_keys = ("source_manifest", "notice_rcept_no", "meeting_pin", "engine_bundle_sha256", "cutoff_at", "policy_version")
            native_controls = arguments[0] == arguments[1] and all(harnesses[0].get(key) == harnesses[1].get(key) and harnesses[0].get(key) is not None for key in shared_keys)
            if not native_controls:
                audit.fail(case["id"], None, "pair", "uncontrolled_native_difference", "native request/binding/source manifest controls differ or are missing")
    return controls, native_controls


def side_result(case, arm, aid, info):
    checked, review = info["packet_check"], info["review_check"]
    row = review["rows"].get(aid)
    errors = checked["errors"] + review["global_errors"] + review["errors"].get(aid, [])
    valid = row is not None and not errors
    native = checked["native"].get(aid)
    record = info["record"] or {}
    harness = (record.get("response", {}).get("data", {}).get("guideline_harness") or {})
    has_review_file = info["review_exists"]
    status = "succeeded" if valid else "partial" if row is not None else "failed" if has_review_file else "not_run"
    original_reason = row.get("reason", "") if row else ""
    reason = original_reason if valid else "검증 미통과 또는 미실행으로 비교에서 제외했습니다."
    if not valid and row:
        reason += f" 제출된 판단: {row.get('decision')}. {original_reason}"
    row_type = row.get("row_kind") if row and isinstance(row.get("row_kind"), str) and row["row_kind"] in ROW_KIND else "unclear"
    conditional = row.get("conditional_on") if row and isinstance(row.get("conditional_on"), str) else None
    relation = native.get("agenda_relation_type") if native else None
    constrained = row_type in ("parent", "conditional") or bool(conditional) or relation not in (None, "normal")
    if valid and row["decision"] == "FOR":
        processing = "찬성 판단 · 경합/조건 확인 후 처리 (보고서 전용; 미실행)" if constrained else "보고서 찬성 판단 · 실제 처리 미실행"
    else:
        processing = "보고서 판단 · 실제 처리 미실행" if valid else "평가 미완료 · 처리 미실행"
    native_workflow = native.get("voting_workflow", {}) if native else {}
    workflow_label = {"awaiting_assessment": "LLM 평가 대기", "not_applicable": "표결 대상 아님"}.get(native_workflow.get("status"), "처리 상태 미확인")
    native_reason = (native.get("reason", "") + "\n제품 처리 상태: " + workflow_label + " · " + str(native_workflow.get("reason"))) if native else None
    return {
        "evaluation_kind": "fresh_independent" if has_review_file else "not_evaluated",
        "execution_status": status, "evaluation_source": "report_side_llm", "application_scope": "report_only",
        "decision": row.get("decision") if valid else None, "submitted_decision": row.get("decision") if row else None,
        "reason": reason, "row_kind": row_type, "conditional_on": conditional, "processing": processing,
        "model": "gpt-6-astra", "runtime": "Astra · Codex 데스크탑 평가 세션", "evaluator": info["review"].get("evaluator") if isinstance(info["review"], dict) else None,
        "reading_limits": (info["packet"] or {}).get("reading_limits"),
        "run_id": (info["packet"] or {}).get("packet_id"), "source_hash": (info["packet"] or {}).get("source_hash"),
        "policy_hash": harness.get("policy_sha256"), "accepted": None, "human_reviewed": False,
        "evidence": review["evidence"].get(aid, []),
        "skipped_checks": row.get("skipped_checks", []) if row else [], "unknowns": row.get("unknowns", []) if row else [],
        "error": "; ".join(error["code"] + ": " + error["detail"] for error in errors) if errors else None,
        "mcp_decision": native.get("decision") if native else None, "mcp_reason": native_reason,
        "mcp_stage_label": "평가 제출 전 · " + workflow_label,
        "mcp_execution_status": "succeeded" if checked["record_valid"] and native else "failed",
        "mcp_evidence": [{"label": "MCP 원응답 · LLM 제출 전", "url": f"records/{case['id']}-{arm}.json"}],
    }


def issue_sort(issue):
    return ({"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(issue.get("priority"), 4), issue.get("id", ""))


def change_category(agenda):
    if pair_kind(agenda) != "fresh":
        return "incomplete"
    low, high = agenda["low"], agenda["high"]
    if low["decision"] == high["decision"]:
        return "unchanged"
    if "NO_VOTE" in (low["decision"], high["decision"]) or low["row_kind"] != high["row_kind"]:
        return "scope_interpretation"
    if low["row_kind"] == "unclear":
        return "unclear_scope"
    return "same_type_direction"


CHANGE_LABEL = {"scope_interpretation": "표결유형 분류 차이 동반", "same_type_direction": "동일 분류 내 찬반·검토 변화",
                "unclear_scope": "표결유형 미확정 변화", "unchanged": "판단 동일", "incomplete": "비교 미완료"}


def relationship_context(native, low, high):
    labels = {"normal": "별도 선행·경합 관계가 표시되지 않은 안건입니다.",
              "conditional": "선행 안건의 가결·부결 조건에 따라 표결합니다.",
              "alternative": "다른 제안과 경합하는 대안입니다.",
              "cumulative_related": "집중투표 선출과 연결된 안건입니다.",
              "procedural": "표결 순서나 방식을 정하는 절차 안건입니다.",
              "withdrawn": "제품 관계 기록에서 철회된 안건으로 분류됐습니다."}
    summary = labels.get(native.get("agenda_relation_type"), "안건 간 관계가 미확인입니다.")
    branches = []
    for link in native.get("agenda_relation_links", []):
        seats = link.get("seats") if isinstance(link, dict) else None
        if type(seats) is int and seats > 0:
            branch = ("집중투표 " if link.get("cumulative") is True else "") + f"{seats}인 선출 분기"
            if branch not in branches:
                branches.append(branch)
    if not branches:
        conditions = [side.get("conditional_on") for side in (low, high) if side.get("conditional_on")]
        branches = list(dict.fromkeys(conditions))
    branch = " / ".join(branches)
    if branch:
        summary += " " + branch
    return summary, branch


def assemble_source_updates(root, report):
    """Keep source-update reviews and validation outside the primary counters."""
    folder = root / "followups"
    if not folder.is_dir():
        return [], []
    updates, validations = [], []
    primary = {meeting["id"]: meeting for meeting in report["meetings"]}
    for directory in sorted(path for path in folder.iterdir() if path.is_dir()):
        if not (directory / "source-verification.json").exists():
            continue
        audit = Audit(root)
        relative = directory.relative_to(root).as_posix()
        followup_id = directory.name
        controls = audit.read(relative + "/pair-controls.json") or {}
        verification = audit.read(relative + "/source-verification.json") or {}
        expected_ids = set(verification.get("scope_agenda_ids", []))
        arms, valid_calls = {}, 0
        for arm in ARMS:
            paths = {key: f"{relative}/{sub}/{followup_id}-{arm}.json" for key, sub in (("packet", "packets"), ("record", "records"), ("review", "reviews"))}
            info = {key: audit.read(path, followup_id, arm) for key, path in paths.items()}
            info["review_exists"] = (root / paths["review"]).exists()
            packet, record = info["packet"] or {}, info["record"] or {}
            case = packet.get("meeting") or {}
            errors = []

            def fail(code, detail):
                errors.append(audit.fail(followup_id, arm, "source_update", code, detail))

            checked = {"valid": False, "record_valid": False, "errors": errors, "sources": {}, "agendas": {}, "native": {}}
            original = audit.read(f"packets/{case.get('id')}-{arm}.json", followup_id, arm) or {}
            try:
                if packet.get("contract") != "opm-all-agenda-source-update-review/1" or packet.get("evaluation_kind") != "source_update_re_evaluation":
                    fail("source_update_contract", "source-update packet contract/kind differs")
                if packet.get("packet_id") != digest({key: value for key, value in packet.items() if key != "packet_id"}):
                    fail("packet_digest_mismatch", "follow-up packet ID differs from its content")
                if packet.get("original_packet_id") != original.get("packet_id") or case.get("id") not in primary:
                    fail("original_packet_binding_mismatch", "follow-up cannot be linked to its primary review")
                for key in ("meeting", "execution_context", "settings", "policy", "general_policy"):
                    if packet.get(key) != original.get(key):
                        fail("source_update_control_mismatch", key)
                if packet.get("settings", {}).get("firmness") != float(arm):
                    fail("firmness_mismatch", "follow-up arm does not match its setting")
                checked["agendas"] = {agenda["agenda_id"]: agenda for agenda in packet.get("agendas", [])}
                original_agendas = {agenda["agenda_id"]: agenda for agenda in original.get("agendas", [])}
                if len(expected_ids) != 2 or len(packet.get("agendas", [])) != 2 or set(checked["agendas"]) != expected_ids:
                    fail("followup_scope_mismatch", "expected exactly the two declared conditional agenda IDs")
                if any(original_agendas.get(aid) != agenda for aid, agenda in checked["agendas"].items()):
                    fail("followup_agenda_binding_mismatch", "follow-up agenda is not the unchanged original row")
                checked["sources"] = {source["source_id"]: source for source in packet.get("sources", [])}
                if len(checked["sources"]) != len(packet.get("sources", [])):
                    fail("duplicate_source_id", "follow-up sources contain duplicate IDs")
                additions = packet.get("source_update", {}).get("added_source_ids", [])
                retained = [source for source in packet.get("sources", []) if source["source_id"] not in additions]
                if retained != original.get("sources") or not additions or any(sid not in checked["sources"] for sid in additions):
                    fail("source_update_inventory_mismatch", "original sources must remain unchanged and additions must exist")
                if packet.get("source_hash") != digest(packet.get("sources")):
                    fail("source_digest_mismatch", "follow-up source hash differs")
                cutoff = datetime.fromisoformat(case["cutoff_at"].replace("Z", "+00:00"))
                if cutoff.tzinfo is None:
                    raise ValueError("cutoff lacks timezone")
                allowed = cutoff.astimezone(KST).date() - timedelta(days=1)
                for source in checked["sources"].values():
                    published, basis = source_date(source)
                    audit.source_dates.append({"case_id": followup_id, "arm": arm, "source_id": source["source_id"],
                        "published_date": published.isoformat(), "date_basis": basis, "latest_allowed_date": allowed.isoformat()})
                    if published > allowed:
                        fail("source_after_cutoff", source["source_id"])
                data = record["response"]["data"]
                task = data["guideline_application"]["structure_tasks"][0]["task"]
                harness = data["guideline_harness"]
                if record.get("case") != case or record.get("original_packet_id") != original.get("packet_id"):
                    fail("followup_record_binding_mismatch", "source-call record does not match original packet")
                if task.get("execution_context") != packet.get("execution_context") or task.get("policy") != packet.get("policy"):
                    fail("followup_record_context_mismatch", "source-call policy or cutoff differs")
                # The follow-up call can return shorter old-source excerpts. Those
                # retained full readings are bound to the validated primary packet;
                # only the added source must be proved by this new MCP response.
                native_sources = {source["source_id"]: source for source in task.get("sources", [])}
                for sid, source in checked["sources"].items():
                    native_source = native_sources.get(sid, {})
                    if any(source.get(key) != native_source.get(key) for key in ("document_sha256", "total_chars", "offset_basis")):
                        fail("followup_document_identity_mismatch", sid)
                    if sid in additions:
                        mutable = {"excerpts", "excerpt_offsets", "partial", "read_windows"}
                        if ({key: value for key, value in source.items() if key not in mutable}
                                != {key: value for key, value in native_source.items() if key not in mutable}
                                or not all(excerpt in native_source.get("excerpts", []) for excerpt in source.get("excerpts", []))):
                            fail("added_source_not_in_native_response", sid)
                if harness.get("ballots_submitted") != 0 or any(harness.get(key, {}).get("accepted_unreviewed") != 0 for key in ("assessment_counts", "structure_assessment_counts")):
                    fail("followup_native_submission_detected", "follow-up must remain a source-only MCP call")
                proof = next((item for item in verification.get("evidence", []) if item.get("firmness") == float(arm)), {})
                if (proof.get("actual_response_file") != paths["record"] or proof.get("actual_response_sha256") != digest(record["response"])
                        or proof.get("original_packet_id") != original.get("packet_id") or proof.get("followup_packet_id") != packet.get("packet_id")):
                    fail("source_verification_record_mismatch", "record hash or packet identity differs from source-verification.json")
                source = checked["sources"].get(proof.get("source_id"), {})
                if proof.get("source_id") not in additions or source.get("document_sha256") != proof.get("document_sha256"):
                    fail("source_verification_document_mismatch", "additional document identity differs")
                if proof.get("published") != source.get("published") or proof.get("effective_as_of") != allowed.strftime("%Y%m%d"):
                    fail("source_verification_date_mismatch", "additional source publication/cutoff proof differs")
                quote_errors, _ = check_evidence(audit, followup_id, arm, "source_update", [{"source_id": proof.get("source_id"), "quote": proof.get("new_source_quote")}], checked["sources"])
                errors.extend(quote_errors)
                original_source = "filing:" + case["notice_rcept_no"]
                identity_errors, _ = check_evidence(audit, followup_id, arm, "source_update", [{"source_id": original_source, "quote": proof.get("original_notice_identity_quote")}], checked["sources"])
                errors.extend(identity_errors)
                if controls.get("packet_ids", {}).get(arm) != packet.get("packet_id") or controls.get("source_hashes", {}).get(arm) != packet.get("source_hash"):
                    fail("followup_pair_control_binding", "pair-controls packet/source identity differs")
                checked["native"] = {row["agenda_id"]: row for row in data.get("agenda_decisions", []) if row.get("agenda_id") in expected_ids}
                checked["record_valid"] = checked["valid"] = not errors
                valid_calls += int(checked["record_valid"])
            except (ValueError, TypeError, KeyError, AttributeError, StopIteration) as error:
                fail("invalid_source_update_packet", str(error))
            info["packet_check"] = checked
            info["review_check"] = check_review(audit, case or {"id": followup_id, "notice_rcept_no": ""}, arm, info["packet"], checked, info["review"])
            info["case"], info["paths"] = case, paths
            arms[arm] = info
        normalized = []
        for arm in ARMS:
            value = without_firmness(arms[arm]["packet"] or {})
            value.pop("original_packet_id", None)
            normalized.append(value)
        pair_ok = normalized[0] == normalized[1] and controls.get("release_ready") is True and all(info["packet_check"]["valid"] for info in arms.values())
        if not pair_ok:
            audit.fail(followup_id, None, "source_update_pair", "followup_pair_uncontrolled", "two follow-up source/settings packets do not match")
        if verification.get("actual_mcp_calls") != 2 or controls.get("actual_mcp_calls") != 2:
            audit.fail(followup_id, None, "source_update_pair", "followup_call_count_mismatch", "expected two separate source-call records")
        original_meeting = primary.get(arms["0.1"]["case"].get("id")) or {}
        originals = {agenda["id"]: agenda for agenda in original_meeting.get("agendas", [])}
        rows, evidence = [], []
        for aid in [key for key in originals if key in expected_ids]:
            for arm, key in ARMS.items():
                after = side_result(arms[arm]["case"], arm, aid, arms[arm])
                after["evaluation_kind"] = "source_update_re_evaluation"
                after["mcp_evidence"] = [{"label": "추가 원문을 받은 실제 MCP 응답", "url": arms[arm]["paths"]["record"]}]
                before = copy.deepcopy(originals[aid][key])
                complete = pair_ok and before["execution_status"] == after["execution_status"] == "succeeded"
                changed_decision = before["decision"] != after["decision"] if complete else None
                used_added = any(item["source_id"] in (arms[arm]["packet"] or {}).get("source_update", {}).get("added_source_ids", []) for item in after["evidence"])
                impact = ("판단이 변경됐습니다." if changed_decision else "판단은 유지됐습니다.") + (" 사유에 추가 공시의 인용이 포함됩니다." if used_added else " 추가 공시 인용은 확인되지 않았습니다.") if complete else "추가 원문 재평가가 진행 중이거나 검증을 통과하지 못했습니다."
                rows.append({"agenda_id": aid, "title": originals[aid]["title"], "branch_context": originals[aid].get("branch_context"),
                             "firmness": float(arm), "before": before, "after": after, "complete": complete, "decision_changed": changed_decision, "impact": impact})
        for item in verification.get("evidence", []):
            source_id = item.get("source_id")
            source = arms[str(item.get("firmness"))]["packet_check"]["sources"].get(source_id, {}) if str(item.get("firmness")) in arms else {}
            candidate = {"label": "추가 공시 · " + str(item.get("published")), "source_id": source_id, "quote": item.get("new_source_quote"), "url": source.get("source_url")}
            if candidate not in evidence:
                evidence.append(candidate)
        completed = sum(row["complete"] for row in rows)
        decision_changes = sum(row["decision_changed"] is True for row in rows)
        status = "complete" if completed == 4 and not audit.failures else "in_progress"
        outcome = (f"추가 판단 {completed}개 중 결론 변화는 {decision_changes}개입니다. "
                   "양쪽 설정의 사유가 3월 20일 사임을 반영해 현재 겸직 설명을 바로잡고, 과거 관계에 대한 판단은 별도로 남겼습니다. "
                   "판단 변화가 0이어도 사실의 현재성은 개선될 수 있습니다.") if status == "complete" else "양쪽 설정의 추가 판단과 인용 검증이 모두 끝나면 원문 보강 전후 결과를 확정합니다."
        updates.append({"id": followup_id, "title": "고려아연 정기 · 박병욱 원문 추가 전후", "status": status,
            "cutoff_at": original_meeting.get("cutoff_at"), "actual_source_calls": valid_calls, "expected_judgments": 4, "completed_judgments": completed,
            "decision_changes": decision_changes if status == "complete" else None, "outcome": outcome,
            "summary": f"조건부 안건 2행을 설정별로 다시 검토하는 별도 확인입니다. 추가 원문 MCP 호출 {valid_calls}/2회, 추가 판단 {completed}/4개 완료. 최초 239행 및 firmness 차이 집계에 합산하지 않습니다.",
            "fact_note": "추가 공시의 3월 20일 자진사임 기록과 종전 공고의 영풍 현직 표기를 대조합니다. 당시 재직의 종료와 과거 재직·제안주주 관계의 잔존 여부는 서로 다른 판단입니다.",
            "limit_note": "같은 설정에서 자료를 보강한 후의 재평가입니다. 원문 보강 전후의 관찰이며 최초 firmness 실험의 효과로 해석하지 않습니다. 사람 미검토·보고서 전용이며 MCP 평가 제출과 투표 전송은 없습니다.",
            "evidence": evidence, "rows": rows, "validation_failures": audit.failures,
            "provenance": [{"label": "추가 원문 확인", "url": relative + "/source-verification.json"}, {"label": "추가 평가 설정 대조", "url": relative + "/pair-controls.json"}]})
        validations.append({"id": followup_id, "status": status, "valid": not audit.failures, "primary_counts_unchanged": True,
                            "failure_count": len(audit.failures), "failures": audit.failures, "source_dates": audit.source_dates,
                            "actual_source_calls": valid_calls, "completed_judgments": completed, "expected_judgments": 4})
    return updates, validations


def assemble(root: Path, issues_path: Path | None = None):
    audit = Audit(root)
    cases = audit.read("case-manifest.json")
    if not isinstance(cases, list) or not cases or not all(isinstance(case, dict) and isinstance(case.get("id"), str) for case in cases):
        raise ValueError("case-manifest.json must be a nonempty array with string case IDs")
    if len({case["id"] for case in cases}) != len(cases):
        raise ValueError("manifest has duplicate case IDs")
    meetings, pair_validation, generated_issues = [], [], []
    for case in cases:
        cid = case["id"]
        arms = {}
        case_failure_start = len(audit.failures)
        for arm in ARMS:
            info = {kind: audit.read(f"{folder}/{cid}-{arm}.json", cid, arm) for kind, folder in (("packet", "packets"), ("record", "records"), ("review", "reviews"))}
            info["review_exists"] = (root / f"reviews/{cid}-{arm}.json").exists()
            info["packet_check"] = check_packet(audit, case, arm, info["packet"], info["record"])
            info["review_check"] = check_review(audit, case, arm, info["packet"], info["packet_check"], info["review"])
            arms[arm] = info
        controls, native_controls = compare_pair(audit, case, arms)
        inventory = {}
        for arm in ARMS:
            inventory.update(arms[arm]["packet_check"]["agendas"])
        discovered = combine_missing_agendas([item for arm in ARMS for item in arms[arm]["review_check"]["missing_agendas"]])
        uncertain_missing = [item for item in discovered if item["kind"] != "report" or not item["validated"]]
        reporting_missing = [item for item in discovered if item["kind"] == "report" and item["validated"]]
        complete_reviews = all(arms[arm]["review_check"]["complete"] for arm in ARMS)
        notes = ["분모는 MCP가 공급한 안건 행입니다. 상위 묶음·조건부 대안을 포함하며 고유 표결 수와 다릅니다.",
                 "원문에서 별도로 발견한 보고사항은 정보 목록에 기록하며 누락 표결로 세지 않습니다.",
                 "현재 정책을 해당 마감 이전 자료에 소급 적용했습니다. 당시 법령·기관 정책의 재현이 아닙니다."]
        if reporting_missing:
            notes.append("공급 목록 밖 보고사항: " + " / ".join(item["title"] for item in reporting_missing))
        if uncertain_missing:
            notes.append("원문 추가 안건 또는 유형·인용 미확정: " + " / ".join(item["title"] for item in uncertain_missing) + ". 표결 목록의 완전성은 미확인입니다.")
        if not complete_reviews:
            notes.append("하나 이상의 리뷰가 미완료 또는 검증 미통과입니다. 해당 행은 비교에서 제외했습니다.")
        for arm in ARMS:
            limits = (arms[arm]["packet"] or {}).get("reading_limits", {})
            principal = limits.get("principal_native_coverage")
            if isinstance(principal, dict):
                notes.append(f"{arm}: 보고서 입력에는 추가 MCP 원문 읽기 {limits.get('supplemental_mcp_response_count')}회가 포함됩니다. 보고서 입력 전체 원문 제공 여부={limits.get('notice_full_available')}; 비교한 기존 native 출력의 원문 전체 제공 여부={principal.get('notice_full_available')}. native 출력은 보강 전 기준입니다.")
            elif limits.get("notice_full_available") is False:
                notes.append(f"{arm}: 제공된 소집공고 원문에 미독 구간이 남아 있습니다. 원문 전체 독해로 세지 않습니다.")
            elif limits.get("notice_full_requested") is False:
                notes.append(f"{arm}: 소집공고 원문 전체가 요청되지 않았습니다.")
        meeting = {"id": cid, "company_id": case.get("corp_code"), "company": case.get("company"),
                   "type": {"annual": "AGM", "extraordinary": "EGM"}.get(case.get("meeting_type"), case.get("meeting_type")),
                   "date": case.get("meeting_date"), "cutoff_at": case.get("cutoff_at"), "notice_rcept_no": case.get("notice_rcept_no"),
                   "total_agendas": len(inventory) if native_controls else None,
                   "inventory_status": "complete" if controls and not uncertain_missing else "partial",
                   "reviews_complete": complete_reviews,
                   "notes": notes, "evidence": [{"label": "소집공고", "url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={case.get('notice_rcept_no')}"}],
                   "informational_reports_missing_from_packet": reporting_missing, "discovered_missing_agendas": uncertain_missing,
                   "agendas": []}
        for aid, agenda in inventory.items():
            low = side_result(case, "0.1", aid, arms["0.1"])
            high = side_result(case, "0.9", aid, arms["0.9"])
            native = arms["0.1"]["packet_check"]["native"].get(aid) or arms["0.9"]["packet_check"]["native"].get(aid) or {}
            relation = {key: native.get(key) for key in ("agenda_relation_type", "agenda_relation_reasons", "agenda_relation_links")}
            scope_note, branch = relationship_context(native, low, high)
            comparable = controls and low["execution_status"] == high["execution_status"] == "succeeded"
            common_type = low["row_kind"] if low["row_kind"] == high["row_kind"] else "unclear"
            row_issues = []
            if low["row_kind"] != high["row_kind"]:
                row_issues.append("두 리뷰의 표결단위 분류가 다릅니다. 원문 대조가 필요합니다.")
            report_agenda = {"id": aid, "title": agenda.get("title"), "category": agenda.get("category"),
                "scope_note": scope_note, "branch_context": branch, "native_relations": relation, "row_kind": common_type, "ballot_id": None,
                "low": low, "high": high, "comparison": {"comparable": comparable, "mcp_comparable": native_controls,
                "reason": "동일 원문·회차·마감·엔진·정책에서 firmness만 변경한 별도 리뷰입니다." if comparable else "자료 통제 또는 리뷰 검증이 미완료여서 독립 비교로 세지 않습니다.",
                "note": "MCP 수치는 새 LLM 평가를 제출하기 전 제품 출력입니다. 보고서 리뷰의 적용 결과가 아닙니다."}, "issues": row_issues}
            category = change_category(report_agenda)
            report_agenda["comparison"]["change_category"] = category
            report_agenda["comparison"]["note"] = CHANGE_LABEL[category] + ". " + report_agenda["comparison"]["note"]
            meeting["agendas"].append(report_agenda)
        meetings.append(meeting)
        pair_validation.append({"case_id": cid, "packet_controls_match": controls, "native_controls_match": native_controls,
                                "expected_agenda_rows": len(inventory), "reviews_complete": complete_reviews,
                                "review_valid_rows": {arm: sum(side_result(case, arm, aid, arms[arm])["execution_status"] == "succeeded" for aid in inventory) for arm in ARMS},
                                "informational_report_titles": [item["title"] for item in reporting_missing],
                                "uncertain_missing_agenda_titles": [item["title"] for item in uncertain_missing],
                                "failure_count": len(audit.failures) - case_failure_start})
        if uncertain_missing:
            generated_issues.append({"id": f"missing-{cid}", "type": "data", "priority": "P1", "status": "open",
                "title": f"{case.get('company')} · {MEETING_TYPE.get(meeting['type'], meeting['type'])} 원문 추가 안건 확인",
                "finding": " / ".join(item["title"] for item in uncertain_missing), "impact": "원문 전체 표결 목록의 완전성을 확정할 수 없습니다.",
                "next_action": "해당 인용을 원문 목적사항과 대조해 표결·보고·상위 묶음을 구분하고 누락 표결이면 양쪽 설정으로 추가 평가합니다.",
                "verification": "미완료", "evidence": [e for item in uncertain_missing for e in item["evidence"]]})
        for arm in ARMS:
            observations = arms[arm]["review_check"]["issues"]
            if observations:
                generated_issues.append({"id": f"reviewer-{cid}-{arm}", "type": "data", "priority": "P2", "status": "unknown",
                    "title": f"{case.get('company')} · {MEETING_TYPE.get(meeting['type'], meeting['type'])} · {arm} 리뷰 관찰",
                    "finding": "\n".join(observations), "impact": "리뷰어가 제기한 자료·조건·설명 공백입니다. 개별 관찰의 의미 정확성은 검증하지 않았습니다.",
                    "next_action": "관찰을 인용 원문에 대조하고 실제 제품 영향과 문서 수정 필요를 분류합니다.",
                    "verification": "인용 문자열 검사는 의미 검증을 대신하지 않습니다.",
                    "evidence": [{"label": f"{arm} 원리뷰", "url": f"reviews/{cid}-{arm}.json"}]})
    companies = build_companies(audit, cases, meetings)
    supplied_issues = []
    chosen_issues = issues_path or root / "issues.json"
    if chosen_issues.exists():
        try:
            value = json.loads(chosen_issues.read_text(encoding="utf-8"))
            supplied_issues = value.get("issues") if isinstance(value, dict) else value
            if not isinstance(supplied_issues, list) or not all(isinstance(issue, dict) and issue.get("title") for issue in supplied_issues):
                raise ValueError("issues input must contain an array of objects with title")
        except (OSError, ValueError) as error:
            audit.fail(None, None, "issues", "invalid_issues_input", str(error))
            supplied_issues = []
    if audit.failures:
        generated_issues.insert(0, {"id": "validation-failures", "type": "validation", "priority": "P1", "status": "open",
            "title": "미완료·검증 실패 항목 처리", "finding": f"{len(audit.failures)}개 검증 실패를 validation.json에 원인·회차·설정·안건별로 기록했습니다.",
            "impact": "검증을 통과하지 못한 평가를 비교 완료 또는 정확한 판단으로 집계할 수 없습니다.",
            "next_action": "실패한 파일·인용·누락 행만 확인해 해당 평가를 복구합니다. 원리뷰를 덮어쓰지 않고 재평가 이력을 보존합니다.",
            "verification": "미완료", "evidence": [{"label": "정확한 검증 실패 목록", "url": "validation.json"}]})
    records = [(meeting, agenda) for meeting in meetings for agenda in meeting["agendas"]]
    fresh = [agenda for _, agenda in records if pair_kind(agenda) == "fresh"]
    fresh_changes = sum(changed(agenda) == "yes" for agenda in fresh)
    native_compared = sum(native_changed(agenda) != "unknown" for _, agenda in records)
    native_changes = sum(native_changed(agenda) == "yes" for _, agenda in records)
    change_categories = Counter(change_category(agenda) for _, agenda in records)
    scope_changes = change_categories["scope_interpretation"]
    direction_changes = change_categories["same_type_direction"]
    unclear_changes = change_categories["unclear_scope"]
    # Engine/version inventory comes from every already checked source-date-independent packet.
    engines, policy_versions = set(), set()
    for case in cases:
        for arm in ARMS:
            path = root / f"packets/{case['id']}-{arm}.json"
            try:
                packet = json.loads(path.read_text(encoding="utf-8"))
                engines.add(packet.get("execution_context", {}).get("engine_bundle_sha256"))
                policy_versions.add(packet.get("policy", {}).get("version"))
            except (OSError, ValueError, AttributeError):
                continue
    versions = sorted(value for value in policy_versions if value)
    generated_at = datetime.now(KST).isoformat(timespec="seconds")
    report = {"title": "Firmness 0.1 · 0.9 표본 전체 안건 리뷰 비교", "generated_at": generated_at,
        "summary": f"{len(companies)}개사·{len(meetings)}회차의 MCP가 반환한 안건 {len(records)}행 중 {len(fresh)}행을 Astra · Codex 데스크탑 평가 세션에서 별도로 비교했고 {fresh_changes}행의 판단이 달랐습니다. 변화 중 표결 없음 전환·유형 분류 차이가 동반된 것은 {scope_changes}행, 동일 분류 내 찬반·검토 {direction_changes}행, 표결유형 미확정 {unclear_changes}행입니다. 실제 MCP는 새 LLM 제출 전 출력 {native_compared}행을 비교했으며 {native_changes}행이 달랐습니다. 보고서 리뷰를 제품 권고에 적용하거나 투표를 전송하지 않았습니다.",
        "scope": {"period": "2026-01-01 ~ 2026-09-09 개최 회차; 기존 10개사 census와 카카오 추가 조회",
            "unit": "MCP가 반환한 안건 행 (상위·조건/대안 포함; 별도 보고사항은 정보 목록)", "policy_version": " / ".join(versions) or None,
            "engine_revision": " / ".join(sorted(value for value in engines if value)) or None, "low_firmness": 0.1, "high_firmness": 0.9,
            "notes": ["stance=0.5 · automation=0.5 고정. 각 주총일 00:00 한국시간을 마감으로 삼고 공개일은 전일까지 허용했습니다.",
                "평가 실행은 Astra · Codex 데스크탑 평가 세션(gpt-6-astra)이며 low/high는 별도 문맥입니다. 같은 설정의 한 문맥이 여러 회차를 볼 수 있으나 반대 설정의 답은 보지 않았습니다.",
                "새 독립 리뷰는 이전 답을 재사용하지 않았다는 뜻입니다. 모델 계열·회사 간 완전 독립, 의미 정확도 또는 평가자 신원 인증을 뜻하지 않습니다.",
                "보고서의 FOR는 경합안 최종 선택이나 실제 자동 처리 준비를 확정하지 않습니다. MCP는 native 평가 대기 상태이며 보고서 평가를 제출하지 않았습니다."]},
        "companies": companies, "meetings": meetings, "issues": sorted([*supplied_issues, *generated_issues], key=issue_sort),
        "methodology": ["실제 HTTP MCP로 고정한 회차·원문·정책 패킷을 만들고 기존 엔진 판단을 제외한 상태로 각 firmness를 별도 읽었습니다.",
            "packet_id·원문 hash·안건 ID 전수 일치·원문 인용 문자열·자료 공개일·양쪽 엔진 및 설정 통제를 검사했습니다. 실패는 미완료로 남깁니다.",
            "원문 인용이 존재해도 주체 귀속·정책 해석·조사 충분성이 맞는지는 별도 검증이 필요합니다. 사업보고서·소송·정관은 제공된 발췌 범위에 한정됩니다.",
            "0.1과 0.9 각 한 번의 리뷰로는 firmness의 효과와 모델의 비결정적 차이를 분리할 수 없습니다. 변화 수·동의율·수용률을 정확도로 사용하지 않습니다.",
            "현재 정책의 소급 적용입니다. 당시 법률 시행이나 당시 기관의 실제 정책·투표를 재현한 검증이 아닙니다.",
            "보고서 LLM 리뷰는 native MCP에 제출하지 않았습니다. accepted=null, human_reviewed=false, 실제 투표 전송 0입니다. native 두 응답은 평가 제출 전 기준선 비교입니다.",
            "반대 설정은 별도 문맥이지만 같은 모델을 사용했습니다. 다른 회사·회차로부터 같은 설정 문맥 안의 영향 가능성도 남아 있습니다.",
            "집계기는 리뷰 파일을 수정하지 않습니다. 의미 QA 수정 시 초기본·수정 이유를 보존하며, 자료 보강 재평가는 최초 비교와 분리합니다."],
        "sources": [{"label": "실행 회차 목록", "url": "case-manifest.json"}, {"label": "검증 상세", "url": "validation.json"}, {"label": "로드맵·이행현황", "url": WIKI}],
        "comparison_summary": {"fresh_pairs": len(fresh), "changed_rows": fresh_changes, "change_categories": dict(change_categories),
                               "native_compared_rows": native_compared, "native_changed_rows": native_changes},
        "ballots_submitted": 0, "human_reviewed": False, "native_review_stage": "pre_submission"}
    validation = {"contract": "opm-firmness-report-validation/1", "generated_at": generated_at, "valid": not audit.failures,
        "failure_count": len(audit.failures), "failures": audit.failures, "pairs": pair_validation,
        "source_dates": audit.source_dates, "reviewer_model": "gpt-6-astra", "human_reviewed": False,
        "scope": "구조·문자열·시점·통제 검사. 의미 정확성·평가자 신원·완전 독립은 검증하지 않음."}
    if (root / "native-source-coverage.json").exists():
        report["sources"].append({"label": "비교한 native 출력의 원문 범위", "url": "native-source-coverage.json"})
    report["source_updates"], validation["source_updates"] = assemble_source_updates(root, report)
    paired_counts = {side: Counter(agenda[side]["decision"] for agenda in fresh) for side in ("low", "high")}
    shifts = " · ".join(f"{DECISION[code]} {paired_counts['low'][code]}→{paired_counts['high'][code]}" for code in DECISION)
    report["executive_findings"] = [{"text": f"완료된 독립 비교 {len(fresh)}행에서 firmness 0.1→0.9의 분포는 {shifts}였습니다. 관찰된 분포 변화이며 정확성 향상을 뜻하지 않습니다."}]
    for update in report["source_updates"]:
        if update["id"] == "kz_park" and update["status"] == "complete":
            report["executive_findings"].append({"text": f"박병욱 원문 보강 후 추가 4개 판단의 결론 변화는 {update['decision_changes']}개였고, 3월 20일 사임을 반영해 현재 겸직 설명의 사실 현재성이 개선됐습니다.",
                "evidence": [{"label": "원문 보강 전후", "url": "#source-updates"}]})
    issue_ids = {issue.get("id") for issue in report["issues"] if issue.get("status") in ("open", "in_progress")}
    if {"ALL-01", "ALL-02", "ALL-10"}.issubset(issue_ids):
        report["executive_findings"].append({"text": "제품에 남은 핵심 과제는 공통 표결 목록과 대안의 선택 관계를 먼저 확정하고, 전 안건의 평가 제출을 제품 권고에 연결하는 것입니다.",
            "evidence": [{"label": "ALL-01·02·10 개선 과제", "url": "#issues"}]})
    validation["all_requested_work_complete"] = validation["valid"] and all(item["status"] == "complete" for item in validation["source_updates"])
    validate_report(report)
    return report, validation


def build_companies(audit, cases, meetings):
    groups = {}
    for case in cases:
        groups.setdefault(case.get("corp_code", case["company"]), []).append(case)
    companies = []
    for company_id, group in groups.items():
        name = group[0]["company"]
        annual = [case for case in group if case.get("meeting_type") == "annual"]
        egms = [case for case in group if case.get("meeting_type") == "extraordinary"]
        agm = {"status": "found" if annual else "unknown", "note": f"선정 정기 {len(annual)}회. 회차 목록은 공고·MCP 기록에 고정했습니다.",
               "evidence": [{"label": case["meeting_date"], "url": f"records/{case['id']}-0.1.json"} for case in annual]}
        if egms:
            egm = {"status": "found", "note": f"선정 임시 {len(egms)}회.", "evidence": [{"label": case["meeting_date"], "url": f"records/{case['id']}-0.1.json"} for case in egms]}
        elif name == "카카오":
            census = audit.read("records/kakao-egm-discovery.json", group[0]["id"], None)
            data = (census or {}).get("response", {}).get("data", {})
            window = data.get("requested_window") or {}
            known_none = (data.get("no_filing") is True and data.get("filing_count") == 0
                          and data.get("requested_meeting_type") == "extraordinary"
                          and window.get("start_date") == "2026-01-01" and window.get("end_date") == "2026-09-09")
            egm = {"status": "none_found" if known_none else "unknown", "note": "실제 MCP의 2026-01-01~2026-09-09 검색에서 no_filing을 확인했습니다." if known_none else "임시주총 미발견을 확인할 유효한 MCP 기록이 없습니다.",
                   "evidence": [{"label": "카카오 임시 실제 조회", "url": "records/kakao-egm-discovery.json"}]}
            if not known_none:
                audit.fail(group[0]["id"], None, "census", "unverified_kakao_egm_discovery", "expected extraordinary no_filing=true, filing_count=0")
        elif name in PRIOR_CENSUS_COMPANIES:
            egm = {"status": "none_found", "note": "이전 10개사 census에서 해당 개최 범위의 임시 공고를 찾지 못했습니다. 이 보고서에서 재조회한 결과가 아닙니다.",
                   "evidence": [{"label": "이전 회차 census·검색 범위", "url": WIKI}]}
        else:
            egm = {"status": "unknown", "note": "임시 검색 근거가 입력되지 않았습니다."}
        companies.append({"id": company_id, "name": name, "agm_discovery": agm, "egm_discovery": egm})
    return companies


def markdown(report, validation):
    records = [(meeting, agenda) for meeting in report["meetings"] for agenda in meeting["agendas"]]
    lines = [f"# {report['title']}", "", report["summary"], "",
             "**리뷰 판단의 차이는 정확도나 firmness의 인과 효과가 아닙니다.** 설정별 한 번의 리뷰이며 모델의 비결정적 응답 차이를 분리하지 못합니다. 모든 정성 판단은 사람 미검토입니다.", "",
             "[전체 안건 HTML](index.html) · [구조화 보고서](report.json) · [검증 상세](validation.json)", ""]
    if report.get("executive_findings"):
        lines.extend(["## 이번에 확인한 것", ""])
        lines.extend("- " + md(item["text"]) for item in report["executive_findings"])
        lines.append("")
    lines.extend([
             "## 회사별 결과", "", "찬/반/검/제외 = FOR/AGAINST/REVIEW/NO_VOTE(표결 없음·제외). 분포는 MCP가 반환한 안건 행 기준이며 상위·조건부 행을 포함합니다. 고유 표결 수가 아닙니다.", "",
             "**평가 제출 전 · LLM 평가 대기.** MCP 수치는 보고서 리뷰를 제품에 적용하기 전 결과입니다.", "",
             "| 회사 | 회차 / 행 | 0.1 찬/반/검/제외 | 0.9 찬/반/검/제외 | 리뷰 변화/비교 | 변화 내 방향/유형차이/미확정 | 평가 제출 전 MCP 변화/비교 |", "|---|---:|---|---|---:|---|---:|"])
    groups = defaultdict(list)
    for meeting, agenda in records:
        groups[meeting["company"]].append((meeting, agenda))
    for name, items in [*groups.items(), ("전체", records)]:
        distributions = []
        for arm in ("low", "high"):
            counts = decision_counts(items, arm)
            text = "/".join(str(counts[decision]) for decision in DECISION)
            if counts["incomplete"]:
                text += f" · 미완료 {counts['incomplete']}"
            distributions.append(text)
        fresh = [agenda for _, agenda in items if pair_kind(agenda) == "fresh"]
        native = [native_changed(agenda) for _, agenda in items]
        native_n = sum(value != "unknown" for value in native)
        changes = f"{sum(changed(agenda) == 'yes' for agenda in fresh)}/{len(fresh)}" if fresh else "미확인/0"
        native_changes = f"{sum(value == 'yes' for value in native)}/{native_n}" if native_n else "미확인/0"
        change_counts = Counter(change_category(agenda) for _, agenda in items)
        split = "/".join(str(change_counts[key]) for key in ("same_type_direction", "scope_interpretation", "unclear_scope"))
        lines.append(f"| {md(name)} | {len({meeting['id'] for meeting, _ in items})} / {len(items)} | {distributions[0]} | {distributions[1]} | {changes} | {split} | {native_changes} |")
    lines.extend(["", "MCP 비교는 새 native LLM 제출 전 출력입니다. 보고서의 찬성은 경쟁 대안의 최종 선택·자동 처리 준비를 확정하지 않습니다.", "", "## 판단이 달라진 행", "",
                  "| 회사·회차 | 안건 | 0.1 → 0.9 | 차이 종류 | 낮은 firmness의 이유 | 높은 firmness의 이유 |", "|---|---|---|---|---|---|"])
    changes = [(meeting, agenda) for meeting, agenda in records if pair_kind(agenda) == "fresh" and changed(agenda) == "yes"]
    for meeting, agenda in changes:
        low, high = agenda["low"], agenda["high"]
        title = md(agenda["title"])
        if agenda.get("branch_context"):
            title += "<br>분기·조건: " + md(agenda["branch_context"])
        lines.append(f"| {md(meeting['company'])} {md(MEETING_TYPE.get(meeting['type'], meeting['type']))} {md(meeting['date'])} | {title} | {md(low['decision'])} → {md(high['decision'])} | {CHANGE_LABEL[change_category(agenda)]} | {md(low['reason'])} | {md(high['reason'])} |")
    if not changes:
        lines.append("| 비교 가능한 독립 리뷰 중 변화 행 없음 |  |  | 비교 미완료 행은 동일 판단으로 세지 않음 |  |  |")
    if report.get("source_updates"):
        lines.extend(["", "## 원문 보강 전후의 별도 확인", "", "최초 회사별·전체 안건 집계와 분리된 후속 확인입니다.", ""])
        for update in report["source_updates"]:
            state = "완료" if update["status"] == "complete" else "진행 중"
            lines.extend([f"### {md(update['title'])} · {state}", "", md(update["summary"]), "", "**" + md(update["outcome"]) + "**", "",
                          md(update["fact_note"]), "", md(update["limit_note"]), "",
                          "| 설정·조건 | 최초 → 추가 원문 판단 | 최초 판단의 이유 | 추가 원문을 읽은 이유 | 관찰된 영향 |", "|---|---|---|---|---|"])
            for row in update["rows"]:
                before, after = row["before"], row["after"]
                before_label = DECISION.get(before.get("decision"), "미완료·미확인")
                after_label = DECISION.get(after.get("decision"), "미완료·미확인")
                lines.append(f"| {row['firmness']} · {md(row.get('branch_context'))} | {before_label} → {after_label} | {md(before['reason'])} | {md(after['reason'])} | {md(row['impact'])} |")
            lines.append("")
            for item in update["evidence"]:
                lines.extend([mdlink(item["label"], item.get("url")), "", "> " + md(item.get("quote")), ""])
            lines.append(" · ".join(mdlink(item["label"], item.get("url")) for item in update["provenance"]))
            if update["validation_failures"]:
                lines.extend(["", f"추가 확인의 검증 실패 {len(update['validation_failures'])}건은 최초 실험 검증과 별도로 validation.json에 기록했습니다."])
    lines.extend(["", "## 원문 정보 목록·범위 공백", ""])
    for meeting in report["meetings"]:
        reporting = meeting["informational_reports_missing_from_packet"]
        missing = meeting["discovered_missing_agendas"]
        if reporting or missing or not meeting.get("reviews_complete", False) or meeting["inventory_status"] != "complete":
            notes = []
            if reporting:
                notes.append("반환 목록 밖 보고사항: " + "; ".join(md(item["title"]) for item in reporting) + ". 누락 표결 수로 세지 않습니다.")
            if missing:
                notes.append("표결 범위 확인 필요: " + "; ".join(md(item["title"]) for item in missing) + ".")
            if not meeting.get("reviews_complete", False):
                notes.append("리뷰가 미완료입니다.")
            if meeting["inventory_status"] != "complete":
                notes.append("원문 목록의 완전성 확인이 남았습니다.")
            lines.append(f"- **{md(meeting['company'])} {md(MEETING_TYPE.get(meeting['type'], meeting['type']))}**: " + " ".join(notes))
    lines.extend(["", "## 개선·문서·검증 과제", ""])
    primary = [issue for issue in report["issues"] if not str(issue.get("id", "")).startswith("reviewer-")]
    for issue in primary:
        evidence = " · ".join(mdlink(item.get("label", "근거"), item.get("url")) for item in issue.get("evidence", []) if isinstance(item, dict))
        lines.extend([f"### {md(issue.get('priority'))} · {md(issue.get('title'))}", "", md(issue.get("finding")), "",
                      f"영향: {md(issue.get('impact'))}  ", f"다음 행동: {md(issue.get('next_action'))}  ",
                      f"상태: {md(ISSUE_STATUS.get(issue.get('status'), issue.get('status')))} · 재확인: {md(issue.get('verification'))}", "", evidence, ""])
    reviewer = [issue for issue in report["issues"] if str(issue.get("id", "")).startswith("reviewer-")]
    if reviewer:
        lines.extend(["### 리뷰어 관찰", "", "아래 관찰은 원리뷰를 보존한 확인 과제입니다. 단독 리뷰가 제기한 내용이며 검증된 오류·수정 완료로 표시하지 않았습니다.", ""])
        for issue in reviewer:
            evidence = " · ".join(mdlink(item.get("label", "원리뷰"), item.get("url")) for item in issue.get("evidence", []))
            lines.extend([f"<details><summary>{md(issue['title'])}</summary>", "", md(issue["finding"]), "", evidence, "", "</details>", ""])
    lines.extend(["## 방법·한계", ""])
    lines.extend("- " + md(item) for item in report["methodology"])
    followup_failures = sum(item["failure_count"] for item in validation.get("source_updates", []))
    lines.extend(["", f"최초 비교 검증 실패 {validation['failure_count']}건 · 원문 보강 확인 검증 실패 {followup_failures}건. 문자열·시점·설정 통제 검증은 의미 정확성 검증과 다릅니다.", ""])
    return "\n".join(lines)


def write_json(path: Path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("report_directory", type=Path)
    parser.add_argument("--issues", type=Path)
    args = parser.parse_args()
    root = args.report_directory.resolve()
    try:
        report, validation = assemble(root, args.issues)
        write_json(root / "validation.json", validation)
        write_json(root / "report.json", report)
        (root / "report.md").write_text(markdown(report, validation), encoding="utf-8")
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"Assembly failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"report": str(root / "report.json"), "meetings": len(report["meetings"]),
                      "agenda_rows": sum(len(meeting["agendas"]) for meeting in report["meetings"]),
                      "validation_failures": validation["failure_count"],
                      "source_updates": [{"id": item["id"], "status": item["status"], "validation_failures": item["failure_count"]} for item in validation["source_updates"]]}, ensure_ascii=False))
    return 0 if validation["all_requested_work_complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
