"""v2 build_campaign_brief action service."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

from open_proxy_mcp.services.contracts import AnalysisStatus, ToolEnvelope
from open_proxy_mcp.services.ownership_structure import build_ownership_structure_payload
from open_proxy_mcp.services.proxy_contest import build_proxy_contest_payload
from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload


def _merge_status(*statuses: str) -> str:
    """NO_FILING은 EXACT 동급(PARTIAL 아래)."""
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
    if statuses and all(status == AnalysisStatus.NO_FILING for status in statuses):
        return AnalysisStatus.NO_FILING
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


def _meeting_date_for_asof(meeting_payload: dict[str, Any], end_date: str) -> str:
    selected = meeting_payload.get("data", {}).get("selected_meeting", {}) or {}
    meeting_date = selected.get("meeting_date")
    if meeting_date:
        return meeting_date
    requested_window = meeting_payload.get("data", {}).get("requested_window", {}) or {}
    if requested_window.get("end_date"):
        return requested_window["end_date"]
    if end_date:
        return end_date
    return date.today().isoformat()


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


def _campaign_status(
    meeting_status: str,
    agenda_status: str,
    board_status: str,
    ownership_status: str,
    proxy_status: str,
    proxy_has_contest_signal: bool,
) -> str:
    merged = _merge_status(meeting_status, agenda_status, board_status, ownership_status, proxy_status)
    if merged != AnalysisStatus.PARTIAL:
        return merged

    if (
        proxy_status == AnalysisStatus.PARTIAL
        and not proxy_has_contest_signal
        and meeting_status == AnalysisStatus.EXACT
        and agenda_status == AnalysisStatus.EXACT
        and board_status == AnalysisStatus.EXACT
        and ownership_status == AnalysisStatus.EXACT
    ):
        return AnalysisStatus.EXACT
    return merged


async def build_campaign_brief_payload(
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
            tool="build_campaign_brief",
            status=meeting_summary.get("status", AnalysisStatus.ERROR),
            subject=meeting_summary.get("subject", company_query),
            warnings=meeting_summary.get("warnings", []),
            data=meeting_summary.get("data", {}),
            evidence_refs=meeting_summary.get("evidence_refs", []),
            next_actions=["company 또는 shareholder_meeting에서 먼저 회차를 확정"],
        ).to_dict()

    meeting_data = meeting_summary.get("data", {})
    meeting_as_of = _meeting_date_for_asof(meeting_summary, end_date)

    agenda_payload, board_payload, ownership_payload, proxy_payload = await asyncio.gather(
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
        build_ownership_structure_payload(
            company_query,
            scope="control_map",
            as_of_date=meeting_as_of,
            start_date=start_date,
            end_date=end_date,
        ),
        build_proxy_contest_payload(
            company_query,
            scope="timeline",
            year=year,
            start_date=start_date,
            end_date=end_date,
            lookback_months=lookback_months,
        ),
    )

    ownership_control = ownership_payload.get("data", {}).get("control_map", {}) or {}
    ownership_summary = ownership_payload.get("data", {}).get("summary", {}) or {}
    proxy_data = proxy_payload.get("data", {})
    proxy_summary = proxy_data.get("summary", {}) or {}
    proxy_players = proxy_data.get("players", {}) or {}
    board_summary = board_payload.get("data", {}).get("board_summary", {}) or {}
    meeting_summary = {
        "meeting_type": meeting_data.get("meeting_type", ""),
        "meeting_phase": meeting_data.get("meeting_phase", ""),
        "result_status": meeting_data.get("result_status", ""),
        "meeting_date": (meeting_data.get("selected_meeting") or {}).get("meeting_date"),
        "notice_rcept_no": (meeting_data.get("selected_meeting") or {}).get("notice_rcept_no", ""),
        "selection_basis": meeting_data.get("selection_basis", ""),
        "top_holder": ownership_summary.get("top_holder", {}),
        "related_total_pct": ownership_summary.get("related_total_pct", 0),
        "treasury_pct": ownership_summary.get("treasury_pct", 0),
        "agenda_count": meeting_data.get("agenda_summary", {}).get("total_count", 0),
        "candidate_count": board_summary.get("total_candidates", 0),
        "outside_director_count": board_summary.get("outside_directors", 0),
    }

    meeting_context = {
        "requested_meeting_type": meeting_data.get("requested_meeting_type", ""),
        "selected_meeting": meeting_data.get("selected_meeting", {}),
        "alternative_meetings": meeting_data.get("alternative_meetings", []),
        "coverage": meeting_data.get("meeting_coverage_12m", {}),
        "meeting_info": meeting_data.get("meeting_info", {}),
        "summary": meeting_summary,
        "agenda_titles": _agenda_titles(agenda_payload)[:20],
        "board_candidates": _board_candidates(board_payload)[:20],
    }

    control_context = {
        "summary": ownership_summary,
        "control_map": {
            "flags": ownership_control.get("flags", {}),
            "observations": ownership_control.get("observations", []),
            "notes": ownership_control.get("notes", []),
            "core_holder_block": ownership_control.get("core_holder_block", {}),
            "treasury_block": ownership_control.get("treasury_block", {}),
            "active_non_overlap_blocks": ownership_control.get("active_non_overlap_blocks", [])[:10],
            "active_overlap_blocks": ownership_control.get("active_overlap_blocks", [])[:10],
        },
    }

    combined_timeline: list[dict[str, Any]] = []
    selected_meeting = meeting_context["selected_meeting"]
    if selected_meeting.get("notice_date"):
        combined_timeline.append({
            "date": selected_meeting.get("notice_date", ""),
            "category": "meeting_notice",
            "actor": selected_meeting.get("notice_report_name", ""),
            "side": "meeting",
            "title": selected_meeting.get("notice_report_name", "주주총회 소집공고"),
            "rcept_no": selected_meeting.get("notice_rcept_no", ""),
        })
    if selected_meeting.get("result_date"):
        combined_timeline.append({
            "date": selected_meeting.get("result_date", ""),
            "category": "meeting_result",
            "actor": selected_meeting.get("notice_report_name", ""),
            "side": "meeting",
            "title": "주주총회결과",
            "rcept_no": selected_meeting.get("result_rcept_no", ""),
        })
    for proxy_row in proxy_data.get("timeline", [])[:50]:
        combined_timeline.append({
            "date": proxy_row["date"],
            "category": proxy_row["category"],
            "actor": proxy_row["actor"],
            "side": proxy_row["side"],
            "title": proxy_row["title"],
            "rcept_no": proxy_row["rcept_no"],
        })
    combined_timeline.sort(key=lambda row: (row["date"], row["rcept_no"]), reverse=True)

    key_flags = _dedupe_strings(
        [
            *meeting_summary.get("warnings", []),
            *agenda_payload.get("warnings", []),
            *board_payload.get("warnings", []),
            *ownership_payload.get("warnings", []),
            *proxy_payload.get("warnings", []),
            *control_context.get("control_map", {}).get("observations", []),
        ]
    )
    if not proxy_summary.get("has_contest_signal", False):
        key_flags.insert(0, "최근 12개월 내 분쟁성 공시가 뚜렷하지 않다.")
    if meeting_summary.get("selection_basis"):
        key_flags.insert(0, meeting_summary["selection_basis"])
    if meeting_summary.get("meeting_phase") == "pre_meeting":
        key_flags.insert(0, "아직 회의 전이라 결과는 없다.")
    elif meeting_summary.get("meeting_phase") == "post_meeting_pre_result":
        key_flags.insert(0, "회의는 끝났지만 결과공시는 아직 확인되지 않았다.")
    if meeting_data.get("correction_summary"):
        key_flags.insert(0, "정정공고가 반영된 회차다.")

    status = _campaign_status(
        meeting_summary.get("status"),
        agenda_payload.get("status"),
        board_payload.get("status"),
        ownership_payload.get("status"),
        proxy_payload.get("status"),
        proxy_summary.get("has_contest_signal", False),
    )

    data = {
        "query": company_query,
        "company_id": meeting_data.get("company_id", ""),
        "canonical_name": meeting_data.get("canonical_name", ""),
        "brief_note": "이 brief는 사실 브리프이며 자동 추천이나 vote math를 포함하지 않는다.",
        "requested_window": meeting_data.get("requested_window", {}),
        "quality": {
            "meeting_summary_status": meeting_summary.get("status", ""),
            "agenda_status": agenda_payload.get("status", ""),
            "board_status": board_payload.get("status", ""),
            "ownership_status": ownership_payload.get("status", ""),
            "proxy_status": proxy_payload.get("status", ""),
            "notice_parse_source": meeting_data.get("notice_parse_source", ""),
            "meeting_phase": meeting_data.get("meeting_phase", ""),
            "result_status": meeting_data.get("result_status", ""),
        },
        "meeting_context": meeting_context,
        "players": {
            "company_side_filers": proxy_players.get("company_side_filers", []),
            "shareholder_side_filers": proxy_players.get("shareholder_side_filers", []),
            "active_external_blocks": proxy_players.get("active_external_blocks", []),
            "active_overlap_blocks": proxy_players.get("active_overlap_blocks", []),
        },
        "control_context": control_context,
        "proxy_context": {
            "summary": proxy_summary,
            "available_scopes": proxy_data.get("available_scopes", []),
        },
        "timeline": combined_timeline,
        "key_flags": key_flags[:20],
    }

    evidence_refs = _dedupe_evidence([
        *(meeting_summary.get("evidence_refs", []) or []),
        *(agenda_payload.get("evidence_refs", []) or []),
        *(board_payload.get("evidence_refs", []) or []),
        *(ownership_payload.get("evidence_refs", []) or []),
        *(proxy_payload.get("evidence_refs", []) or []),
    ])

    next_actions = [
        "evidence tool로 핵심 공시 원문을 다시 열어 확인",
        "proxy_contest timeline scope로 분쟁 이벤트 순서를 다시 확인",
        "ownership_structure control_map과 함께 보면 판 구조가 더 선명하다.",
    ]

    return ToolEnvelope(
        tool="build_campaign_brief",
        status=status,
        subject=meeting_summary.get("subject", company_query),
        warnings=[],
        data=data,
        evidence_refs=evidence_refs,
        next_actions=next_actions,
    ).to_dict()
