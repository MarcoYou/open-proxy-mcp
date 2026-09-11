#!/usr/bin/env python3
"""Render an evidence-preserving, offline HTML firmness comparison report.

Usage: python3 scripts/render_firmness_report.py INPUT.json OUTPUT.html

Input contract (all prose is plain text, links are optional):
{
  "title": "Firmness 비교", "generated_at": "ISO timestamp",
  "summary": "What this run established and what remains unknown",
  "scope": {"period": "...", "unit": "공시의 고유 안건 행",
            "policy_version": "...", "engine_revision": "...",
            "low_firmness": 0.1, "high_firmness": 0.9,
            "notes": ["..."]},
  "companies": [{"id": "...", "name": "...",
    "agm_discovery": {"status": "found|none_found|not_searched|failed|unknown",
                      "note": "...", "evidence": [{"label": "...", "url": "..."}]},
    "egm_discovery": {"status": "...", "note": "..."}}],
  "meetings": [{"id": "unique", "company_id": "...", "company": "...",
    "type": "AGM|EGM", "date": "YYYY-MM-DD", "cutoff_at": "...",
    "notice_rcept_no": "...", "total_agendas": null,
    "inventory_status": "complete|partial|unknown", "notes": ["..."],
    "evidence": [{"label": "...", "url": "..."}],
    "agendas": [{"id": "unique within meeting", "number": "2-1", "title": "...",
      "category": "...", "candidate": "...", "scope_note": "...",
      "row_kind": "ballot|parent|conditional|report|unclear", "ballot_id": null,
      "baseline": {"decision": "...", "reason": "...", "evidence": []},
      "low": {"evaluation_kind": "fresh_independent|prior_response_replay|baseline_only|not_evaluated",
              "execution_status": "succeeded|partial|failed|not_run|unknown",
              "evaluation_source": "report_side_llm|native_mcp_llm|baseline_engine",
              "application_scope": "report_only|native_mcp_applied|not_applied|unknown",
              "decision": null, "reason": "...", "processing": "...",
              "mcp_decision": null, "mcp_reason": "...", "mcp_evidence": [],
              "mcp_execution_status": "succeeded|partial|failed|not_run|unknown",
              "row_kind": "ballot|parent|conditional|report|unclear", "conditional_on": null,
              "task_id": "...", "run_id": "...", "model": "...",
              "source_hash": "...", "policy_hash": "...",
              "accepted": null, "human_reviewed": null,
              "assessment_decision": "...", "error": "...", "evidence": [],
              "skipped_checks": ["..."], "unknowns": ["..."]},
      "high": {"evaluation_kind": "...", "execution_status": "..."},
      "comparison": {"comparable": null, "mcp_comparable": null, "reason": "...", "note": "..."},
      "issues": ["..."]}]}],
  "issues": [{"id": "...", "type": "improvement|documentation|data|execution|validation",
              "priority": "P1", "status": "open|fixed|verified|unknown",
              "title": "...", "finding": "...", "impact": "...",
              "next_action": "...", "verification": "...", "evidence": []}],
  "methodology": ["..."], "sources": [{"label": "...", "url": "..."}]
}

Optional source_updates is a separate list of source-update follow-ups with
status, summary, outcome, fact_note, limit_note, evidence, provenance and rows.
Each follow-up row has title, branch_context, firmness, before/after evaluation
objects, complete, decision_changed and impact. These rows never enter the
primary meeting inventory, comparison counters or all-agenda filters.

total_agendas is the declared inventory-row denominator, never the number of accepted
candidate tasks. null is unknown. Empty lists are not evidence of absence.
Fresh means independently re-read without the prior answer; replay must stay replay
even after successful MCP submission. execution_status records the run outcome;
accepted records submission acceptance, not correctness. baseline records the
existing engine output separately. decision is the primary reviewer's judgment;
mcp_decision is the actual native product recommendation. Report-side reviews
must use application_scope=report_only even when based on genuine MCP evidence.
A pair counts as independent only when both
sides are fresh, succeeded, have a decision, and comparison.comparable is true.
This flag asserts that the caller checked meeting/cutoff/source/policy controls;
the renderer does not authenticate those controls or judge semantic accuracy.
Native MCP comparison requires comparison.mcp_comparable=true and a known
mcp_decision on both sides; explicit native failures/partial results are excluded.
Agenda-row distributions include parent and conditional rows. Unique ballots are
counted only with explicit ballot_id values for every ballot/conditional row and
no unclear row kinds. ballot_id is scoped to a meeting and may join alternatives.
row_kind prefers the agenda's supplied classification; otherwise it uses agreeing
reviewer classifications, or unclear. Reader-visible reviewer types are retained.
No actual result or runtime invocation is created by this renderer.
"""

from __future__ import annotations

import argparse
from collections import Counter
from html import escape
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit


KIND = {
    "fresh_independent": "새 독립 평가",
    "prior_response_replay": "이전 응답 재제출",
    "baseline_only": "기존 엔진만",
    "not_evaluated": "미평가",
    "source_update_re_evaluation": "원문 보강 재평가",
}
EXECUTION = {
    "succeeded": "실행 완료", "partial": "일부 완료", "failed": "실패",
    "not_run": "미실행", "unknown": "실행 상태 미확인",
}
PAIR = {
    "fresh": "독립 비교 가능", "replay": "재제출 포함 비교", "baseline": "기존 엔진만",
    "failed": "실패 포함", "incomplete": "비교 미완료", "uncontrolled": "통제 미확인",
}
DISCOVERY = {
    "found": "발견", "none_found": "검색 범위에서 미발견",
    "not_searched": "미검색", "failed": "검색 실패", "unknown": "미확인",
}
ISSUE_TYPE = {
    "improvement": "개선", "documentation": "문서 공백", "data": "자료 공백",
    "execution": "실행 문제", "validation": "검증 공백",
}
DECISION = {"FOR": "찬성", "AGAINST": "반대", "REVIEW": "검토", "NO_VOTE": "표결 없음·제외"}
SOURCE = {"report_side_llm": "보고서 LLM 리뷰", "native_mcp_llm": "MCP 연결 LLM 평가", "baseline_engine": "기존 엔진"}
APPLICATION = {"report_only": "보고서에만 반영", "native_mcp_applied": "MCP 권고에 적용", "not_applied": "미적용", "unknown": "적용 미확인"}
ROW_KIND = {"ballot": "독립 표결행", "parent": "상위 묶음", "conditional": "조건·대안행", "report": "보고사항", "unclear": "표결단위 미확인"}
ISSUE_STATUS = {"open": "미해결", "fixed": "수정 완료", "verified": "재확인 완료", "unknown": "확인 필요", "in_progress": "진행 중", "partial": "일부 완료"}
MEETING_TYPE = {"AGM": "정기", "EGM": "임시"}
CATEGORY = {"articles_amendment": "정관 변경", "director_election": "이사 선임", "audit_committee_election": "감사위원 선임",
            "director_compensation": "이사 보수", "financial_statements": "재무제표 승인", "cash_dividend": "현금배당",
            "treasury_share": "자기주식", "shareholder_proposal": "주주제안", "retirement_pay": "퇴직금", "audit_compensation": "감사 보수", "other": "기타"}


def display(value: object) -> str:
    if value is None:
        return "미확인"
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def h(value: object) -> str:
    return escape(display(value), quote=True)


def plain_list(value: object) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def bullets(values: object, empty: str = "") -> str:
    items = plain_list(values)
    return "<ul>" + "".join(f"<li>{h(item)}</li>" for item in items) + "</ul>" if items else empty


def safe_url(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not value or any(ord(char) < 32 or ord(char) == 127 for char in value) or "\\" in value:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme.lower() not in ("", "http", "https") or value.startswith("//"):
        return None
    if parsed.scheme and not parsed.netloc:
        return None
    return value


def links(values: object) -> str:
    parts = []
    for entry in plain_list(values):
        entry = entry if isinstance(entry, dict) else {"label": entry}
        label = entry.get("label", entry.get("source_id", entry.get("url", "근거")))
        url = safe_url(entry.get("url"))
        content = f'<a href="{h(url)}" rel="noopener noreferrer">{h(label)}</a>' if url else h(label)
        if entry.get("quote") is not None:
            content += f'<details class="quote"><summary>원문 인용</summary><blockquote>{h(entry["quote"])}</blockquote></details>'
        parts.append('<div class="evidence-item">' + content + '</div>')
    return '<div class="links">' + " · ".join(parts) + "</div>" if parts else ""


def decision(value: object) -> str:
    return DECISION.get(str(value), display(value))


def badge(label: object, style: str = "neutral") -> str:
    return f'<span class="badge {h(style)}">{h(label)}</span>'


def side(agenda: dict, name: str) -> dict:
    value = agenda.get(name)
    return value if isinstance(value, dict) else {}


def pair_kind(agenda: dict) -> str:
    low, high = side(agenda, "low"), side(agenda, "high")
    if any(item.get("execution_status") == "failed" for item in (low, high)):
        return "failed"
    if all(item.get("evaluation_kind") == "baseline_only" for item in (low, high)):
        return "baseline"
    if not all(item.get("execution_status") == "succeeded" and item.get("decision") is not None
               for item in (low, high)):
        return "incomplete"
    kinds = {item.get("evaluation_kind") for item in (low, high)}
    if not kinds.issubset({"fresh_independent", "prior_response_replay"}):
        return "incomplete"
    if agenda.get("comparison", {}).get("comparable") is not True:
        return "uncontrolled"
    return "fresh" if kinds == {"fresh_independent"} else "replay"


def changed(agenda: dict) -> str:
    if pair_kind(agenda) not in ("fresh", "replay"):
        return "unknown"
    return "yes" if side(agenda, "low").get("decision") != side(agenda, "high").get("decision") else "no"


def native_changed(agenda: dict) -> str:
    if agenda.get("comparison", {}).get("mcp_comparable") is not True:
        return "unknown"
    low, high = side(agenda, "low"), side(agenda, "high")
    if not all(item.get("mcp_decision") is not None
               and item.get("mcp_execution_status") in (None, "succeeded") for item in (low, high)):
        return "unknown"
    return "yes" if low["mcp_decision"] != high["mcp_decision"] else "no"


def row_kind(agenda: dict) -> str:
    kind = agenda.get("row_kind")
    if kind in ROW_KIND:
        return kind
    low, high = side(agenda, "low").get("row_kind"), side(agenda, "high").get("row_kind")
    return low if low == high and low in ROW_KIND else "unclear"


def unique_ballots(records: list[tuple[dict, dict]]) -> int | None:
    if not records:
        return None
    identities = set()
    for meeting, agenda in records:
        kind = row_kind(agenda)
        if kind == "unclear":
            return None
        if kind in ("ballot", "conditional"):
            if agenda.get("ballot_id") in (None, ""):
                return None
            identities.add((str(meeting["id"]), str(agenda["ballot_id"])))
    return len(identities)


def decision_counts(records: list[tuple[dict, dict]], name: str) -> Counter:
    counts = Counter()
    for _, agenda in records:
        item = side(agenda, name)
        if item.get("execution_status") != "succeeded" or item.get("evaluation_kind") not in ("fresh_independent", "prior_response_replay"):
            counts["incomplete"] += 1
        elif item.get("decision") is None:
            counts["incomplete"] += 1
        else:
            counts[str(item["decision"])] += 1
    return counts


def distribution(counts: Counter) -> str:
    values = [f'<span><b>{counts[code]}</b> {h(label)}</span>' for code, label in DECISION.items()]
    if counts["incomplete"]:
        values.append(f'<span class="minor"><b>{counts["incomplete"]}</b> 미완료·미확인</span>')
    for code in sorted(counts.keys() - DECISION.keys() - {"incomplete"}):
        values.append(f'<span><b>{counts[code]}</b> {h(code)}</span>')
    return '<div class="distribution">' + ''.join(values) + '</div>'


def reason_cell(value: dict) -> str:
    kind = value.get("evaluation_kind")
    status = value.get("execution_status")
    style = "bad" if status == "failed" else "good" if kind == "fresh_independent" and status == "succeeded" else "neutral"
    result = '<div class="minor">리뷰 판단</div><div class="result">' + h(decision(value.get("decision"))) + "</div>"
    result += badge(KIND.get(kind, "평가 유형 미확인"), style) + badge(EXECUTION.get(status, "실행 상태 미확인"))
    result += badge(SOURCE.get(value.get("evaluation_source"), "평가 경로 미확인"))
    result += badge(APPLICATION.get(value.get("application_scope"), "적용 미확인"))
    result += f'<p class="reason">{h(value.get("reason"))}</p>'
    if value.get("assessment_decision") is not None:
        result += f'<p class="minor">LLM 단독 판단: {h(decision(value["assessment_decision"]))}</p>'
    if value.get("processing") is not None:
        result += f'<p class="minor">처리: {h(value["processing"])}</p>'
    if value.get("row_kind") is not None:
        result += f'<p class="minor">리뷰 분류: {h(ROW_KIND.get(value["row_kind"], value["row_kind"]))}</p>'
    if value.get("conditional_on") is not None:
        result += f'<p class="minor">표결 조건: {h(value["conditional_on"])}</p>'
    if value.get("error") is not None:
        result += f'<p class="error">{h(value["error"])}</p>'
    result += links(value.get("evidence"))
    if "mcp_decision" in value or "mcp_reason" in value:
        stage = value.get("mcp_stage_label", "실제 MCP 권고")
        result += '<details><summary>' + h(stage) + ': ' + h(decision(value.get("mcp_decision"))) + '</summary>'
        if "mcp_execution_status" in value:
            result += badge("조회 완료" if value["mcp_execution_status"] == "succeeded" else "조회 실패·미완료")
        result += f'<p>{h(value.get("mcp_reason"))}</p>' + links(value.get("mcp_evidence")) + '</details>'
    details = []
    for key, label in (("run_id", "실행 ID"), ("task_id", "과업 ID"), ("model", "모델"),
                       ("source_hash", "원문 hash"), ("policy_hash", "정책 hash"),
                       ("accepted", "제출 수용"), ("human_reviewed", "사람 검토")):
        if key in value:
            details.append(f"<dt>{h(label)}</dt><dd>{h(value[key])}</dd>")
    if details or value.get("skipped_checks") or value.get("unknowns"):
        result += '<details><summary>평가 기록·미확인 항목</summary><dl>' + "".join(details) + "</dl>"
        if value.get("skipped_checks"):
            result += "<strong>제외한 기준</strong>" + bullets(value["skipped_checks"])
        if value.get("unknowns"):
            result += "<strong>남은 미확인</strong>" + bullets(value["unknowns"])
        result += "</details>"
    return result


def validate(data: object) -> dict:
    if not isinstance(data, dict):
        raise ValueError("report must be a JSON object")
    if not isinstance(data.get("scope", {}), dict):
        raise ValueError("scope must be an object")
    for key in ("companies", "meetings", "issues"):
        if not isinstance(data.get(key, []), list):
            raise ValueError(f"{key} must be an array")
        if not all(isinstance(item, dict) for item in data.get(key, [])):
            raise ValueError(f"{key} items must be objects")
    meeting_ids = set()
    for meeting in data.get("meetings", []):
        if not isinstance(meeting, dict) or not meeting.get("id") or str(meeting["id"]) in meeting_ids:
            raise ValueError("meetings require unique nonempty id values")
        meeting_ids.add(str(meeting["id"]))
        count = meeting.get("total_agendas")
        if count is not None and (type(count) is not int or count < 0):
            raise ValueError(f"{meeting['id']}: total_agendas must be a nonnegative integer or null")
        if not isinstance(meeting.get("agendas", []), list):
            raise ValueError(f"{meeting['id']}: agendas must be an array")
        agenda_ids = set()
        for agenda in meeting.get("agendas", []):
            if not isinstance(agenda, dict) or not agenda.get("id") or str(agenda["id"]) in agenda_ids:
                raise ValueError(f"{meeting['id']}: agendas require unique nonempty id values")
            agenda_ids.add(str(agenda["id"]))
            comparison = agenda.get("comparison", {})
            if not isinstance(comparison, dict) or type(comparison.get("comparable")) not in (type(None), bool):
                raise ValueError("comparison.comparable must be a boolean or null")
            if type(comparison.get("mcp_comparable")) not in (type(None), bool):
                raise ValueError("comparison.mcp_comparable must be a boolean or null")
            if agenda.get("row_kind") not in (*ROW_KIND, None):
                raise ValueError(f"unknown row_kind: {agenda.get('row_kind')}")
            for name in ("low", "high"):
                evaluation = agenda.get(name)
                if evaluation is None:
                    continue
                if not isinstance(evaluation, dict):
                    raise ValueError(f"{meeting['id']}/{agenda['id']}/{name}: expected object or null")
                if evaluation.get("evaluation_kind") not in (*KIND, None):
                    raise ValueError(f"unknown evaluation_kind: {evaluation.get('evaluation_kind')}")
                if evaluation.get("execution_status") not in (*EXECUTION, None):
                    raise ValueError(f"unknown execution_status: {evaluation.get('execution_status')}")
                if evaluation.get("evaluation_source") not in (*SOURCE, None):
                    raise ValueError(f"unknown evaluation_source: {evaluation.get('evaluation_source')}")
                if evaluation.get("application_scope") not in (*APPLICATION, None):
                    raise ValueError(f"unknown application_scope: {evaluation.get('application_scope')}")
                if evaluation.get("row_kind") not in (*ROW_KIND, None):
                    raise ValueError(f"unknown row_kind: {evaluation.get('row_kind')}")
                if evaluation.get("mcp_execution_status") not in (*EXECUTION, None):
                    raise ValueError(f"unknown mcp_execution_status: {evaluation.get('mcp_execution_status')}")
    return data


def render(data: dict) -> str:
    validate(data)
    scope = data.get("scope", {})
    meetings = data.get("meetings", [])
    records = [(meeting, agenda) for meeting in meetings for agenda in meeting.get("agendas", [])]
    counts = Counter(pair_kind(agenda) for _, agenda in records)
    independent_changes = sum(changed(agenda) == "yes" and pair_kind(agenda) == "fresh" for _, agenda in records)
    native_compared = sum(native_changed(agenda) != "unknown" for _, agenda in records)
    native_changes = sum(native_changed(agenda) == "yes" for _, agenda in records)
    covered = sum(meeting.get("total_agendas") is not None
                  and meeting["total_agendas"] == len(meeting.get("agendas", [])) for meeting in meetings)
    known_totals = [meeting["total_agendas"] for meeting in meetings if meeting.get("total_agendas") is not None]
    total_label = str(sum(known_totals)) if meetings and len(known_totals) == len(meetings) else "미확인"
    title = data.get("title", "Firmness 0.1 · 0.9 전체 안건 비교")
    low_label = h(scope.get("low_firmness", 0.1))
    high_label = h(scope.get("high_firmness", 0.9))
    native_label = "평가 제출 전 MCP" if data.get("native_review_stage") == "pre_submission" else "실제 MCP"
    source_update_nav = '<a href="#source-updates">원문 보강 전후</a>' if data.get("source_updates") else ''
    out = [f'<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{h(title)}</title><style>{CSS}</style></head><body>']
    out.append('<header><div class="eyebrow">OPM · 실행 결과 보고서</div>'
               f'<h1>{h(title)}</h1><p class="lede">{h(data.get("summary"))}</p>'
               f'<p class="minor">작성 시각 {h(data.get("generated_at"))} · LLM 평가의 수용은 정확도 검증과 다릅니다.</p>'
               '<nav><a href="#company-summary">회사별 결과</a><a href="#coverage">회사·회차 범위</a><a href="#agendas">전체 안건 비교</a>' + source_update_nav + '<a href="#issues">개선·문서 공백</a><a href="#method">방법·근거</a></nav></header><main>')
    cards = [("수록 안건 행", len(records)), ("MCP가 반환한 전체 안건 행", total_label),
             ("목록 수량 일치 회차", f"{covered} / {len(meetings)}"),
             ("독립 비교 가능 안건", counts["fresh"]), ("독립 비교 중 리뷰 변화", independent_changes),
             (native_label + " 변화 / 비교", f"{native_changes if native_compared else '미확인'} / {native_compared}")]
    out.append('<div class="metrics">' + "".join(f'<div class="metric"><strong>{h(value)}</strong><span>{h(label)}</span></div>' for label, value in cards) + '</div>')
    if data.get("executive_findings"):
        out.append('<section id="findings"><h2>이번에 확인한 것</h2><ol>')
        for finding in data["executive_findings"]:
            out.append('<li>' + h(finding.get("text")) + links(finding.get("evidence")) + '</li>')
        out.append('</ol></section>')
    out.append('<div class="callout"><strong>집계 읽는 법</strong><p>수록 행에는 상위 묶음·조건부 대안·보고사항도 포함됩니다. 아래 찬반 분포는 안건 행 기준이며 독립적인 표결 수가 아닙니다. 후보 과업 수·제출 수를 합산하지 않습니다. 전체 안건 분모가 미확인이면 목록의 완전성을 확정하지 않습니다. 새 독립 평가 양쪽이 완료되고 비교 통제가 확인된 행만 독립 비교로 셉니다. 보고서 LLM 리뷰와 실제 MCP 권고를 구분하며, 보고서에서 검토한 안건이 모두 MCP 권고에 적용된 것은 아닙니다.</p><p><strong>차이는 정확도가 아닙니다.</strong> 0.1·0.9 각각 한 번의 리뷰로는 설정 효과와 모델의 비결정적 응답 차이를 분리할 수 없습니다. 같은 답도 정확성의 증거가 아닙니다. 원문 대조와 반복 평가가 별도로 필요합니다.</p></div>')
    out.append('<div class="status-line">' + " ".join(badge(f"{PAIR[key]} {counts[key]}", "bad" if key == "failed" else "neutral") for key in PAIR) + '</div>')
    out.append(f'<section id="company-summary"><h2>회사별 결과 한눈에 보기</h2><p>리뷰 분포는 완료된 새 평가·재제출의 안건 행을 셉니다. 리뷰 변화는 독립 비교가 가능한 행에 한정합니다. {h(native_label)} 권고 변화는 별도로 통제가 확인된 {native_compared}행 중 {native_changes}행입니다.</p>')
    if data.get("native_review_stage") == "pre_submission":
        out.append('<p><strong>평가 제출 전 · LLM 평가 대기.</strong> 아래 MCP 수치는 보고서 리뷰를 제품에 적용하기 전 결과입니다.</p>')
    out.append(f'<div class="table-wrap"><table class="company-summary"><thead><tr><th>회사·회차 수</th><th>안건 행·표결 단위</th><th>{low_label} 리뷰 분포</th><th>{high_label} 리뷰 분포</th><th>리뷰 변화 / 독립 비교</th><th>{h(native_label)} 변화 / 비교</th></tr></thead><tbody>')
    groups = {}
    for meeting in meetings:
        key = str(meeting.get("company_id") or meeting.get("company"))
        groups.setdefault(key, {"company": meeting.get("company"), "meetings": [], "records": []})
        groups[key]["meetings"].append(meeting)
        groups[key]["records"].extend((meeting, agenda) for agenda in meeting.get("agendas", []))
    for group in [*groups.values(), {"company": "전체", "meetings": meetings, "records": records}]:
        group_records = group["records"]
        type_counts = Counter(row_kind(agenda) for _, agenda in group_records)
        fresh = [agenda for _, agenda in group_records if pair_kind(agenda) == "fresh"]
        nc = [native_changed(agenda) for _, agenda in group_records]
        native_count = sum(value != "unknown" for value in nc)
        reviewer_change = str(sum(changed(agenda) == "yes" for agenda in fresh)) if fresh else "미확인"
        native_change = str(sum(value == "yes" for value in nc)) if native_count else "미확인"
        kinds_text = ' · '.join(f'{h(ROW_KIND[kind])} {type_counts[kind]}' for kind in ROW_KIND if type_counts[kind])
        out.append(f'<tr><th>{h(group["company"])}<br><span class="minor">{len(group["meetings"])}회차</span></th>'
                   f'<td><strong>{len(group_records)}행</strong><br><span class="minor">{kinds_text}</span><br><span class="minor">고유 표결 {h(unique_ballots(group_records))}</span></td>'
                   '<td>' + distribution(decision_counts(group_records, "low")) + '</td>'
                   '<td>' + distribution(decision_counts(group_records, "high")) + '</td>'
                   f'<td><strong>{reviewer_change} / {len(fresh)}</strong></td><td><strong>{native_change} / {native_count}</strong></td></tr>')
    out.append('</tbody></table></div><p class="minor">고유 표결 수는 표결·조건행마다 원문에 대조한 ballot_id가 있고 표결단위 미확인 행이 없을 때만 표시합니다. 행 분류는 입력된 안건 분류를 우선하며, 없으면 두 리뷰가 일치한 분류를 사용합니다. 이 분류의 일치도 원문 검증을 대신하지 않습니다.</p></section>')
    out.append('<section id="coverage"><h2>회사·회차별 범위</h2><p>정기·임시의 검색 상태와 발견된 각 회차를 구분합니다. 임시주총 미발견은 주총이 없었다는 확정이 아닙니다.</p>')
    companies = data.get("companies", [])
    if companies:
        out.append('<div class="table-wrap"><table class="coverage"><thead><tr><th>표본 회사</th><th>정기주총 검색</th><th>임시주총 검색</th></tr></thead><tbody>')
        for company in companies:
            out.append(f'<tr><th>{h(company.get("name"))}</th>')
            for key in ("agm_discovery", "egm_discovery"):
                discovery = company.get(key) or {}
                out.append('<td>' + badge(DISCOVERY.get(discovery.get("status"), display(discovery.get("status"))))
                           + f'<p>{h(discovery.get("note"))}</p>' + links(discovery.get("evidence")) + '</td>')
            out.append('</tr>')
        out.append('</tbody></table></div>')
    else:
        out.append('<p class="error">회사별 정기·임시 검색 목록이 제공되지 않았습니다. 전체 표본 검색 범위는 미확인입니다.</p>')
    out.append('<div class="table-wrap"><table class="coverage"><thead><tr><th>회사·회차</th><th>기준·공고</th><th>수록 / 전체 안건</th><th>평가 범위</th><th>한계·근거</th></tr></thead><tbody>')
    for index, meeting in enumerate(meetings):
        agendas = meeting.get("agendas", [])
        mc = Counter(pair_kind(agenda) for agenda in agendas)
        match = meeting.get("total_agendas") is not None and meeting.get("total_agendas") == len(agendas)
        inventory = "반환 목록과 수량 일치" if match else "반환 목록과 수량 일치 미확인"
        if meeting.get("inventory_status") != "complete":
            inventory += " · 원문 목록 완전성 확인 필요"
        out.append(f'<tr><th><a class="meeting-jump" href="#meeting-{index}">{h(meeting.get("company"))}</a><br>{h(MEETING_TYPE.get(meeting.get("type"), meeting.get("type")))} · {h(meeting.get("date"))}</th>'
                   f'<td>마감 {h(meeting.get("cutoff_at"))}<br>공고 {h(meeting.get("notice_rcept_no"))}</td>'
                   f'<td><strong>{len(agendas)} / {h(meeting.get("total_agendas"))}</strong><br>{h(inventory)}</td>'
                   '<td>' + '<br>'.join(f'{h(PAIR[key])} {mc[key]}' for key in PAIR if mc[key]) + '</td>'
                   '<td>' + bullets(meeting.get("notes")) + links(meeting.get("evidence")) + '</td></tr>')
    out.append('</tbody></table></div></section>')
    out.append('<section id="agendas"><h2>전체 안건 비교</h2><p>각 안건의 리뷰 판단과 사유를 나란히 봅니다. 실제 MCP 권고·기존 엔진 결과·적용 여부는 별도로 표시합니다. 미실행·실패는 리뷰 판단 차이로 세지 않습니다.</p>')
    company_names = sorted({display(meeting.get("company")) for meeting in meetings})
    out.append('<div class="filters"><label>회사<select id="company-filter"><option value="">전체 회사</option>'
               + ''.join(f'<option value="{h(name)}">{h(name)}</option>' for name in company_names) + '</select></label>'
               '<label>회차 유형<select id="meeting-filter"><option value="">정기·임시 전체</option><option value="AGM">정기</option><option value="EGM">임시</option></select></label>'
               '<label>비교 상태<select id="pair-filter"><option value="">전체 상태</option>'
               + ''.join(f'<option value="{key}">{h(value)}</option>' for key, value in PAIR.items()) + '</select></label>'
               '<label>리뷰 변화<select id="change-filter"><option value="">전체</option><option value="yes">변화 있음</option><option value="no">판단 동일</option><option value="unknown">비교 미확인</option></select></label>'
               '<label>행 유형<select id="kind-filter"><option value="">전체 유형</option>'
               + ''.join(f'<option value="{key}">{h(value)}</option>' for key, value in ROW_KIND.items()) + '</select></label>'
               '<label>실제 MCP 변화<select id="native-filter"><option value="">전체</option><option value="yes">변화 있음</option><option value="no">권고 동일</option><option value="unknown">비교 미확인</option></select></label>'
               '<label class="search">검색<input id="search" type="search" placeholder="안건·후보·사유 검색"></label><button id="reset" type="button">초기화</button></div>'
               f'<p id="visible-count" class="minor" aria-live="polite">전체 {len(records)}행</p>')
    out.append(f'<div class="table-wrap agenda-wrap"><table class="agenda-table"><thead><tr><th>회사·회차·안건</th><th>Firmness {low_label}</th><th>Firmness {high_label}</th><th>차이·해석·기존 권고</th></tr></thead><tbody>')
    for meeting_index, meeting in enumerate(meetings):
        for agenda_index, agenda in enumerate(meeting.get("agendas", [])):
            pk = pair_kind(agenda)
            ch = changed(agenda)
            row_id = f'meeting-{meeting_index}' if agenda_index == 0 else f'meeting-{meeting_index}-agenda-{agenda_index}'
            nk = native_changed(agenda)
            heading = ((str(agenda["number"]) + " · ") if agenda.get("number") not in (None, "") else "") + str(agenda.get("title", "미확인"))
            out.append(f'<tr id="{row_id}" class="agenda-row" data-company="{h(meeting.get("company"))}" data-meeting="{h(meeting.get("type"))}" data-pair="{pk}" data-change="{ch}" data-kind="{row_kind(agenda)}" data-native="{nk}">'
                       f'<th><span class="minor">{h(meeting.get("company"))} · {h(MEETING_TYPE.get(meeting.get("type"), meeting.get("type")))} · {h(meeting.get("date"))}</span>'
                       f'<div class="agenda-title">{h(heading)}</div>')
            out.append(badge(ROW_KIND[row_kind(agenda)]))
            for key in ("category", "candidate", "scope_note"):
                if agenda.get(key) is not None:
                    value = CATEGORY.get(agenda[key], agenda[key]) if key == "category" else agenda[key]
                    out.append(f'<p class="minor">{h(value)}</p>')
            out.append('<details><summary>안건 식별·관계 원기록</summary><dl>'
                       f'<dt>안건 ID</dt><dd>{h(agenda.get("id"))}</dd>')
            if agenda.get("ballot_id") is not None:
                out.append(f'<dt>표결 단위 ID</dt><dd>{h(agenda["ballot_id"])}</dd>')
            out.append('</dl>')
            if agenda.get("native_relations") is not None:
                out.append('<pre>' + h(json.dumps(agenda["native_relations"], ensure_ascii=False, indent=2)) + '</pre>')
            out.append('</details>')
            out.append('</th><td>' + reason_cell(side(agenda, "low")) + '</td><td>' + reason_cell(side(agenda, "high")) + '</td><td>')
            out.append(badge(PAIR[pk], "good" if pk == "fresh" else "bad" if pk == "failed" else "neutral"))
            if ch != "unknown":
                out.append(f'<p class="change">{"리뷰 판단 변화 있음" if ch == "yes" else "리뷰 판단 동일"}</p>')
            out.append(f'<p class="minor">실제 MCP: {"권고 변화 있음" if nk == "yes" else "권고 동일" if nk == "no" else "비교 미확인"}</p>')
            low_kind, high_kind = side(agenda, "low").get("row_kind"), side(agenda, "high").get("row_kind")
            if low_kind and high_kind and low_kind != high_kind:
                out.append('<p class="error">두 리뷰의 표결단위 분류가 다릅니다. 원문 대조가 필요합니다.</p>')
            comparison = agenda.get("comparison", {})
            for key in ("reason", "note"):
                if comparison.get(key) is not None:
                    out.append(f'<p>{h(comparison[key])}</p>')
            baseline = agenda.get("baseline")
            if isinstance(baseline, dict):
                out.append('<details><summary>기존 엔진 권고: ' + h(decision(baseline.get("decision"))) + '</summary>'
                           f'<p>{h(baseline.get("reason"))}</p>' + links(baseline.get("evidence")) + '</details>')
            if agenda.get("issues"):
                out.append('<strong>후속 확인</strong>' + bullets(agenda["issues"]))
            out.append('</td></tr>')
    out.append('</tbody></table></div><p id="no-results" hidden>선택한 조건에 해당하는 안건이 없습니다.</p></section>')
    if data.get("source_updates"):
        out.append('<section id="source-updates"><h2>원문 보강 전후의 별도 확인</h2><p>최초 회사별·전체 안건 집계와 분리된 후속 확인입니다.</p>')
        for update in data["source_updates"]:
            out.append('<article><h3>' + h(update.get("title")) + '</h3>'
                       + badge("완료" if update.get("status") == "complete" else "진행 중", "good" if update.get("status") == "complete" else "neutral")
                       + '<p>' + h(update.get("summary")) + '</p>'
                       + '<div class="callout"><strong>' + h(update.get("outcome")) + '</strong><p>' + h(update.get("fact_note")) + '</p></div>'
                       + '<p>' + h(update.get("limit_note")) + '</p>' + links(update.get("evidence")))
            out.append('<div class="table-wrap"><table class="agenda-table"><thead><tr><th>설정·조건</th><th>최초 원문으로 한 판단</th><th>추가 원문을 읽은 판단</th><th>관찰된 영향</th></tr></thead><tbody>')
            for row in update.get("rows", []):
                out.append('<tr class="source-update-row"><th>Firmness ' + h(row.get("firmness")) + '<p>' + h(row.get("title")) + '</p><p class="minor">'
                           + h(row.get("branch_context")) + '</p><details><summary>안건 식별</summary>' + h(row.get("agenda_id")) + '</details></th>'
                           + '<td>' + reason_cell(row.get("before") or {}) + '</td><td>' + reason_cell(row.get("after") or {}) + '</td>'
                           + '<td>' + h(row.get("impact")) + '</td></tr>')
            out.append('</tbody></table></div>')
            if update.get("validation_failures"):
                out.append('<p class="error">추가 확인의 검증 실패 ' + h(len(update["validation_failures"])) + '건은 최초 실험 검증과 별도로 기록했습니다.</p>')
            out.append('<details><summary>추가 확인의 출처·검증 기록</summary>' + links(update.get("provenance")) + '</details></article>')
        out.append('</section>')
    out.append('<section id="issues"><h2>개선 사항·문서 공백</h2><p>발견 사항, 결론에 미치는 영향, 다음 행동과 재확인 상태를 함께 기록합니다.</p><div class="issues">')
    for issue in data.get("issues", []):
        out.append('<article class="issue">' + badge(issue.get("priority"))
                   + badge(ISSUE_TYPE.get(issue.get("type"), display(issue.get("type")))) + badge(ISSUE_STATUS.get(issue.get("status"), issue.get("status")))
                   + f'<h3>{h(issue.get("title"))}</h3><p>{h(issue.get("finding"))}</p><dl>')
        for key, label in (("impact", "영향"), ("next_action", "다음 행동"), ("verification", "재확인")):
            out.append(f'<dt>{h(label)}</dt><dd>{h(issue.get(key))}</dd>')
        out.append('</dl>' + links(issue.get("evidence")) + '</article>')
    if not data.get("issues"):
        out.append('<p>기록된 항목이 없습니다. 문제가 없다는 검증을 뜻하지 않습니다.</p>')
    out.append('</div></section><section id="method"><h2>방법·실행 경계·근거</h2><dl>')
    for key, label in (("period", "조회 범위"), ("unit", "집계 단위"), ("policy_version", "정책 버전"), ("engine_revision", "엔진 버전")):
        out.append(f'<dt>{h(label)}</dt><dd>{h(scope.get(key))}</dd>')
    out.append('</dl>' + bullets(scope.get("notes")) + bullets(data.get("methodology")))
    out.append('<p><strong>새 독립 평가</strong>: 이전 답을 재사용하지 않고 원문을 새로 읽은 평가. <strong>이전 응답 재제출</strong>: 기존 판단을 새 계약·요청에 다시 제출한 확인. <strong>기존 엔진만</strong>: firmness에 따른 새 LLM 평가 없이 얻은 기본 권고. <strong>실패</strong>: 실행을 시도했으나 해당 결과를 완성하지 못한 상태입니다. 새 독립 평가 여부와 실행 성공 여부를 별도로 보존합니다.</p>')
    out.append(links(data.get("sources")) + '</section></main><footer>이 문서는 제공된 JSON에서 생성했습니다. 비어 있거나 미확인인 값을 0이나 찬반으로 보충하지 않았습니다.</footer>'
               f'<script>{JS}</script></body></html>')
    return ''.join(out)


CSS = """
pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:11px}
@media print{.company-summary{min-width:0!important;font-size:9px}.distribution{display:block!important;white-space:normal!important}.distribution span{display:block}.distribution b{font-size:11px!important}}
.company-summary{min-width:1060px}.company-summary th:first-child{width:12%}.company-summary td:nth-child(2){width:25%}.company-summary tbody tr:last-child{background:#edf4fa}.distribution{display:grid;grid-template-columns:repeat(2,minmax(65px,1fr));gap:5px 12px;white-space:nowrap}.distribution b{font-size:17px;margin-right:3px}.evidence-item{display:inline-block;max-width:100%;vertical-align:top}.quote{font-size:12px;border-top:0;padding:0;margin:3px 0}.quote blockquote{margin:8px 0;padding:8px 12px;border-left:3px solid #d9e2eb;color:#526277;white-space:pre-wrap}
:root{color-scheme:light;--ink:#17283b;--muted:#526277;--line:#d9e2eb;--blue:#215780;--paper:#fff;--bg:#f3f6fa}*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 system-ui,-apple-system,'Apple SD Gothic Neo','Malgun Gothic',sans-serif}header,main,footer{max-width:1680px;margin:auto;padding:34px 42px}header{padding-top:52px;background:#122c42;color:#fff}header .minor{color:#c0d0df}.eyebrow{letter-spacing:.12em;font-size:12px;color:#a9d1ee}h1{font-size:clamp(27px,3vw,42px);line-height:1.3;margin:12px 0}h2{font-size:25px;line-height:1.4;margin:0 0 12px}h3{font-size:19px;margin:13px 0}p{margin:9px 0;white-space:pre-wrap}.lede{max-width:1150px;font-size:18px}nav{display:flex;gap:20px;flex-wrap:wrap;margin-top:25px}a{color:var(--blue);text-underline-offset:3px}header a{color:#bce1ff}section{margin:40px 0;scroll-margin-top:20px}.metrics{display:grid;grid-template-columns:repeat(6,1fr);gap:12px}.metric,.callout,.issue{background:var(--paper);border:1px solid var(--line);border-radius:10px}.metric{padding:18px}.metric strong{font-size:30px;display:block;line-height:1.3}.metric span{font-size:13px;color:var(--muted)}.callout{margin-top:18px;padding:18px 22px;border-left:4px solid var(--blue)}.status-line{margin-top:16px}.table-wrap{overflow:auto;border:1px solid var(--line);border-radius:9px;background:#fff;margin:18px 0}table{border-collapse:collapse;width:100%;font-size:14px}th,td{padding:16px;vertical-align:top;text-align:left;border-bottom:1px solid var(--line);overflow-wrap:anywhere}thead th{background:#eaf0f6;font-size:13px;color:#40516a}tbody tr:last-child>*{border-bottom:0}tbody th{font-weight:500}.coverage{min-width:860px}.coverage th:first-child{min-width:180px}.agenda-table{min-width:1060px;table-layout:fixed}.agenda-table th:first-child{width:19%}.agenda-table th:nth-child(2),.agenda-table th:nth-child(3){width:29%}.agenda-table th:last-child{width:23%}.agenda-title{font-size:16px;font-weight:700;margin-top:8px}.result{font-size:20px;font-weight:750;margin-bottom:5px}.badge{display:inline-block;font-size:11px;line-height:1.55;font-weight:600;padding:3px 7px;border-radius:5px;background:#edf1f6;color:#44546a;margin:2px 4px 2px 0}.badge.good{background:#e0f1e8;color:#1e684c}.badge.bad{background:#fbe9e7;color:#a33c2d}.minor{font-size:12px;color:var(--muted)}.reason{font-size:14px}.error{color:#a33c2d}.change{font-weight:700}.links{font-size:12px;display:block;margin:8px 0}.filters{display:flex;gap:12px;flex-wrap:wrap;align-items:flex-end}.filters label{font-size:12px;color:var(--muted);display:flex;flex-direction:column;gap:4px}.filters .search{flex:1;min-width:180px}select,input,button{font:inherit;border:1px solid #aebdcd;background:#fff;border-radius:6px;padding:9px;color:var(--ink);min-height:40px}button{cursor:pointer}select:focus,input:focus,button:focus{outline:3px solid #aed5f2;outline-offset:1px}details{border-top:1px solid var(--line);padding-top:8px;margin-top:10px;font-size:12px}summary{cursor:pointer;color:var(--blue)}dl{display:grid;grid-template-columns:100px 1fr;gap:5px 12px;font-size:13px}dt{color:var(--muted)}dd{margin:0;overflow-wrap:anywhere}ul{padding-left:18px;margin:7px 0}.issues{display:grid;grid-template-columns:repeat(2,1fr);gap:16px}.issue{padding:22px}footer{font-size:12px;color:var(--muted);border-top:1px solid var(--line)}[hidden]{display:none!important}@media(max-width:1050px){.metrics{grid-template-columns:repeat(3,1fr)}.issues{grid-template-columns:1fr}}@media(max-width:600px){header,main,footer{padding:25px 18px}.metrics{grid-template-columns:repeat(2,1fr)}.metric{padding:13px}.metric strong{font-size:26px}.filters label{flex:1;min-width:140px}nav{gap:12px}h2{font-size:22px}}@media print{body{background:#fff;font-size:10px}header,main,footer{max-width:none;padding:16px}header{background:#fff;color:#000}header .minor{color:#555}nav,.filters,#visible-count{display:none}.metrics{grid-template-columns:repeat(6,1fr)}.metric{padding:8px}.metric strong{font-size:18px}.table-wrap{overflow:visible;border-radius:0}.agenda-table,.coverage{min-width:0;font-size:9px}th,td{padding:7px}.result{font-size:13px}.reason,.agenda-title{font-size:10px}.badge,.minor,.links{font-size:8px}.issues{grid-template-columns:1fr}thead{display:table-header-group}section{margin:20px 0}.issue{break-inside:avoid}a{color:#111}.agenda-row[hidden]{display:table-row!important}}
"""

JS = """
(() => {
  const controls = ['company-filter','meeting-filter','pair-filter','change-filter','kind-filter','native-filter','search'].map(id => document.getElementById(id));
  const rows = Array.from(document.querySelectorAll('.agenda-row'));
  function filter() {
    const [company,meeting,pair,change,kind,native,search] = controls.map(control => control.value.toLowerCase());
    let visible = 0;
    rows.forEach(row => {
      const match = (!company || row.dataset.company.toLowerCase() === company)
        && (!meeting || row.dataset.meeting.toLowerCase() === meeting)
        && (!pair || row.dataset.pair === pair)
        && (!change || row.dataset.change === change)
        && (!kind || row.dataset.kind === kind)
        && (!native || row.dataset.native === native)
        && (!search || row.textContent.toLowerCase().includes(search));
      row.hidden = !match;
      if (match) visible++;
    });
    document.getElementById('visible-count').textContent = `${visible} / ${rows.length}행 표시`;
    document.getElementById('no-results').hidden = visible !== 0;
  }
  controls.forEach(control => control.addEventListener('input', filter));
  document.getElementById('reset').addEventListener('click', () => { controls.forEach(control => {control.value = '';}); filter(); });
  document.querySelectorAll('.meeting-jump').forEach(link => {
    link.addEventListener('click', () => {
      const target = document.getElementById(link.getAttribute('href').slice(1));
      if (!target) return;
      controls.forEach(control => { control.value = ''; });
      document.getElementById('company-filter').value = target.dataset.company;
      document.getElementById('meeting-filter').value = target.dataset.meeting;
      filter();
      // Default anchor navigation now targets a visible row.
    });
  });
  filter();
})();
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path, help="Report JSON path")
    parser.add_argument("output", type=Path, help="Standalone HTML output path")
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error("input and output must be different files")
    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
        html = render(data)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        print(f"Report could not be rendered: {exc}", file=sys.stderr)
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(html, encoding="utf-8")
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
