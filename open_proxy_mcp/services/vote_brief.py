"""v2 prepare_vote_brief action service."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

from open_proxy_mcp.services.contracts import AnalysisStatus, ToolEnvelope
from open_proxy_mcp.services.ownership_structure import build_ownership_structure_payload
from open_proxy_mcp.services.proxy_contest import build_proxy_contest_payload
from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload


def _merge_status(*statuses: str) -> str:
    if any(status == AnalysisStatus.ERROR for status in statuses):
        return AnalysisStatus.ERROR
    if any(status == AnalysisStatus.REQUIRES_REVIEW for status in statuses):
        return AnalysisStatus.REQUIRES_REVIEW
    if any(status == AnalysisStatus.CONFLICT for status in statuses):
        return AnalysisStatus.CONFLICT
    if any(status == AnalysisStatus.PARTIAL for status in statuses):
        return AnalysisStatus.PARTIAL
    if any(status == AnalysisStatus.AMBIGUOUS for status in statuses):
        return AnalysisStatus.AMBIGUOUS
    return AnalysisStatus.EXACT


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        cleaned = (value or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        ordered.append(cleaned)
    return ordered


def _dedupe_evidence(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    ordered: list[dict[str, Any]] = []
    for ref in refs:
        evidence_id = ref.get("evidence_id", "")
        if evidence_id in seen:
            continue
        seen.add(evidence_id)
        ordered.append(ref)
    return ordered


def _meeting_date_for_asof(meeting_summary_payload: dict[str, Any], end_date: str) -> str:
    selected = meeting_summary_payload.get("data", {}).get("selected_meeting", {}) or {}
    meeting_date = selected.get("meeting_date")
    if meeting_date:
        return meeting_date
    requested_window = meeting_summary_payload.get("data", {}).get("requested_window", {}) or {}
    if requested_window.get("end_date"):
        return requested_window["end_date"]
    if end_date:
        return end_date
    return date.today().isoformat()


def _result_highlights(result_payload: dict[str, Any]) -> dict[str, Any]:
    result_data = result_payload.get("data", {}).get("results", {}) or {}
    items = result_data.get("items", []) or []
    if not items:
        return {}

    passed = 0
    opposed: list[dict[str, Any]] = []
    for item in items:
        if (item.get("passed") or "").strip() in {"가결", "원안가결", "수정가결"}:
            passed += 1
        opposition = item.get("opposition_rate")
        try:
            if opposition is not None and float(opposition) >= 10:
                opposed.append({
                    "number": item.get("number", ""),
                    "agenda": item.get("agenda", ""),
                    "opposition_rate": float(opposition),
                })
        except (TypeError, ValueError):
            continue
    return {
        "agenda_count": len(items),
        "passed_count": passed,
        "high_opposition_items": opposed,
        "result_format": result_data.get("result_format", ""),
        "numerical_vote_table_available": result_data.get("numerical_vote_table_available"),
    }


def _result_quality_note(result_brief: dict[str, Any]) -> str:
    result_format = result_brief.get("result_format", "")
    numerical_available = result_brief.get("numerical_vote_table_available")
    if result_format == "table" and numerical_available:
        return "세부표형 결과공시라 안건별 찬성률과 추정참석률까지 확인 가능하다."
    if result_format == "summary":
        return "요약형 결과공시라 안건별 가결·부결은 확인되지만 찬성률/참석률 수치는 제공되지 않는다."
    return ""


def _vote_math_brief(vote_math_payload: dict[str, Any] | None) -> dict[str, Any]:
    if not vote_math_payload:
        return {}
    data = vote_math_payload.get("data", {}).get("vote_math", {}) or {}
    attendance = data.get("attendance_estimate", {}) or {}
    capital = data.get("capital_structure", {}) or {}
    interpretation = data.get("interpretation", {}) or {}
    meeting_ref = data.get("meeting_reference", {}) or {}
    return {
        "status": vote_math_payload.get("status", ""),
        "meeting_reference": meeting_ref,
        "representative_pct": attendance.get("representative_pct"),
        "comparable_item_count": attendance.get("comparable_item_count", 0),
        "signal_level": interpretation.get("signal_level", ""),
        "contestable_turnout_pct": capital.get("contestable_turnout_pct"),
        "ex_related_turnout_pct": capital.get("ex_related_turnout_pct"),
        "notes": interpretation.get("notes", []) or [],
        "warnings": vote_math_payload.get("warnings", []) or [],
    }


def _board_candidates(board_payload: dict[str, Any]) -> list[dict[str, Any]]:
    appointments = board_payload.get("data", {}).get("board", {}).get("appointments", []) or []
    rows: list[dict[str, Any]] = []
    for appointment in appointments:
        for candidate in appointment.get("candidates", []):
            rows.append({
                "name": candidate.get("name", ""),
                "role_type": candidate.get("roleType", ""),
                "main_job": candidate.get("mainJob", ""),
                "recommender": candidate.get("recommender", ""),
                "major_relation": candidate.get("majorShareholderRelation", ""),
                "agenda_number": appointment.get("number", ""),
                "agenda_title": appointment.get("title", ""),
            })
    return rows


def _compensation_brief(comp_payload: dict[str, Any]) -> dict[str, Any]:
    summary = comp_payload.get("data", {}).get("compensation_summary", {}) or {}
    return {
        "total_items": summary.get("totalItems", 0),
        "current_total_limit": summary.get("currentTotalLimit"),
        "prior_total_paid": summary.get("priorTotalPaid"),
        "prior_total_limit": summary.get("priorTotalLimit"),
        "prior_utilization": summary.get("priorUtilization"),
    }


def _agenda_titles(agenda_payload: dict[str, Any]) -> list[str]:
    agendas = agenda_payload.get("data", {}).get("agendas", []) or []
    titles: list[str] = []

    def walk(nodes: list[dict[str, Any]]) -> None:
        for node in nodes:
            title = node.get("title", "")
            if title:
                titles.append(title)
            walk(node.get("children", []) or [])

    walk(agendas)
    return titles


async def build_vote_brief_payload(
    company_query: str,
    *,
    meeting_type: str = "auto",
    year: int | None = None,
    start_date: str = "",
    end_date: str = "",
    lookback_months: int = 12,
) -> dict[str, Any]:
    meeting_summary = await build_shareholder_meeting_payload(
        company_query,
        meeting_type=meeting_type,
        scope="summary",
        year=year,
        start_date=start_date,
        end_date=end_date,
        lookback_months=lookback_months,
    )

    if meeting_summary.get("status") in {AnalysisStatus.ERROR, AnalysisStatus.AMBIGUOUS}:
        return ToolEnvelope(
            tool="prepare_vote_brief",
            status=meeting_summary.get("status", AnalysisStatus.ERROR),
            subject=meeting_summary.get("subject", company_query),
            warnings=meeting_summary.get("warnings", []),
            data=meeting_summary.get("data", {}),
            evidence_refs=meeting_summary.get("evidence_refs", []),
            next_actions=["company 또는 shareholder_meeting에서 먼저 회차를 확정"],
        ).to_dict()

    meeting_data = meeting_summary.get("data", {})
    ownership_as_of = _meeting_date_for_asof(meeting_summary, end_date)

    agenda_payload, board_payload, compensation_payload, ownership_payload = await asyncio.gather(
        build_shareholder_meeting_payload(
            company_query,
            meeting_type=meeting_type,
            scope="agenda",
            year=year,
            start_date=start_date,
            end_date=end_date,
            lookback_months=lookback_months,
        ),
        build_shareholder_meeting_payload(
            company_query,
            meeting_type=meeting_type,
            scope="board",
            year=year,
            start_date=start_date,
            end_date=end_date,
            lookback_months=lookback_months,
        ),
        build_shareholder_meeting_payload(
            company_query,
            meeting_type=meeting_type,
            scope="compensation",
            year=year,
            start_date=start_date,
            end_date=end_date,
            lookback_months=lookback_months,
        ),
        build_ownership_structure_payload(
            company_query,
            scope="control_map",
            as_of_date=ownership_as_of,
            start_date=start_date,
            end_date=end_date,
        ),
    )

    result_payload = None
    vote_math_payload = None
    if meeting_data.get("result_status") == "available":
        result_payload = await build_shareholder_meeting_payload(
            company_query,
            meeting_type=meeting_type,
            scope="results",
            year=year,
            start_date=start_date,
            end_date=end_date,
            lookback_months=lookback_months,
        )
        result_brief_for_quality = _result_highlights(result_payload)
        if result_brief_for_quality.get("numerical_vote_table_available"):
            vote_math_payload = await build_proxy_contest_payload(
                company_query,
                scope="vote_math",
                year=year,
                start_date=start_date,
                end_date=end_date,
                lookback_months=lookback_months,
            )

    merged_status = _merge_status(
        meeting_summary.get("status"),
        agenda_payload.get("status"),
        board_payload.get("status"),
        compensation_payload.get("status"),
        ownership_payload.get("status"),
        result_payload.get("status") if result_payload else AnalysisStatus.EXACT,
        vote_math_payload.get("status") if vote_math_payload else AnalysisStatus.EXACT,
    )

    control_map = ownership_payload.get("data", {}).get("control_map", {}) or {}
    summary = {
        "meeting_type": meeting_data.get("meeting_type", ""),
        "meeting_phase": meeting_data.get("meeting_phase", ""),
        "result_status": meeting_data.get("result_status", ""),
        "meeting_date": (meeting_data.get("selected_meeting") or {}).get("meeting_date"),
        "notice_rcept_no": (meeting_data.get("selected_meeting") or {}).get("notice_rcept_no"),
        "selection_basis": meeting_data.get("selection_basis", ""),
        "top_holder": ownership_payload.get("data", {}).get("summary", {}).get("top_holder", {}),
        "related_total_pct": ownership_payload.get("data", {}).get("summary", {}).get("related_total_pct", 0),
        "treasury_pct": ownership_payload.get("data", {}).get("summary", {}).get("treasury_pct", 0),
        "agenda_count": meeting_data.get("agenda_summary", {}).get("total_count", 0),
        "candidate_count": board_payload.get("data", {}).get("board_summary", {}).get("total_candidates", 0),
        "outside_director_count": board_payload.get("data", {}).get("board_summary", {}).get("outside_directors", 0),
        "compensation_items": compensation_payload.get("data", {}).get("compensation_summary", {}).get("totalItems", 0),
    }

    result_brief = _result_highlights(result_payload or {})
    result_quality_note = _result_quality_note(result_brief)
    vote_math_brief = _vote_math_brief(vote_math_payload)

    key_flags = _dedupe_strings(
        [
            *meeting_summary.get("warnings", []),
            *agenda_payload.get("warnings", []),
            *board_payload.get("warnings", []),
            *compensation_payload.get("warnings", []),
            *ownership_payload.get("warnings", []),
            *(result_payload.get("warnings", []) if result_payload else []),
            *(vote_math_payload.get("warnings", []) if vote_math_payload else []),
            *(control_map.get("observations", []) or []),
            result_quality_note,
        ]
    )
    if meeting_data.get("correction_summary"):
        key_flags.insert(0, "정정공고가 반영된 회차다.")
    if meeting_data.get("meeting_phase") == "pre_meeting":
        key_flags.insert(0, "아직 회의 전이라 결과는 없다.")
    elif meeting_data.get("meeting_phase") == "post_meeting_pre_result":
        key_flags.insert(0, "회의는 끝났지만 결과공시는 아직 확인되지 않았다.")

    data = {
        "query": company_query,
        "company_id": meeting_data.get("company_id", ""),
        "canonical_name": meeting_data.get("canonical_name", ""),
        "requested_window": meeting_data.get("requested_window", {}),
        "meeting": {
            "requested_meeting_type": meeting_data.get("requested_meeting_type", ""),
            "selected_meeting": meeting_data.get("selected_meeting", {}),
            "alternative_meetings": meeting_data.get("alternative_meetings", []),
            "coverage": meeting_data.get("meeting_coverage_12m", {}),
            "meeting_info": meeting_data.get("meeting_info", {}),
            "summary": summary,
        },
        "ownership_context": {
            "summary": ownership_payload.get("data", {}).get("summary", {}),
            "control_map": {
                "flags": control_map.get("flags", {}),
                "observations": control_map.get("observations", []),
                "active_non_overlap_blocks": control_map.get("active_non_overlap_blocks", [])[:10],
                "active_overlap_blocks": control_map.get("active_overlap_blocks", [])[:10],
            },
        },
        "agenda_brief": {
            "titles": _agenda_titles(agenda_payload)[:20],
            "summary": meeting_data.get("agenda_summary", {}),
        },
        "board_brief": {
            "summary": board_payload.get("data", {}).get("board_summary", {}),
            "candidates": _board_candidates(board_payload)[:20],
        },
        "compensation_brief": _compensation_brief(compensation_payload),
        "result_brief": result_brief,
        "vote_math_brief": vote_math_brief,
        "key_flags": key_flags[:20],
    }

    evidence_refs = _dedupe_evidence([
        *(meeting_summary.get("evidence_refs", []) or []),
        *(agenda_payload.get("evidence_refs", []) or []),
        *(board_payload.get("evidence_refs", []) or []),
        *(compensation_payload.get("evidence_refs", []) or []),
        *(ownership_payload.get("evidence_refs", []) or []),
        *((result_payload.get("evidence_refs", []) or []) if result_payload else []),
        *((vote_math_payload.get("evidence_refs", []) or []) if vote_math_payload else []),
    ])

    next_actions = [
        "evidence tool로 핵심 공시 원문을 다시 열어 확인",
        "shareholder_meeting results scope로 실제 의결 결과 재확인" if meeting_data.get("result_status") == "available" else "회의 이후 결과공시가 나오면 results scope로 다시 확인",
        "결과가 요약형이면 vote_math 대신 안건별 가결·부결 중심으로 해석",
        "ownership_structure control_map과 함께 보면 표 대결 구도가 더 선명하다.",
    ]

    return ToolEnvelope(
        tool="prepare_vote_brief",
        status=merged_status,
        subject=meeting_summary.get("subject", company_query),
        warnings=[],
        data=data,
        evidence_refs=evidence_refs,
        next_actions=next_actions,
    ).to_dict()
