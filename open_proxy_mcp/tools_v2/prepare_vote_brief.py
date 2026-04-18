"""v2 prepare_vote_brief public tool."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.vote_brief import build_vote_brief_payload


def _render_error(payload: dict[str, Any]) -> str:
    lines = [f"# prepare_vote_brief: {payload.get('subject', '')}", "", "투표 메모를 만들지 못했다."]
    for warning in payload.get("warnings", []):
        lines.append(f"- {warning}")
    return "\n".join(lines)


def _render(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    meeting = data.get("meeting", {})
    meeting_summary = meeting.get("summary", {})
    ownership_context = data.get("ownership_context", {})
    control_map = ownership_context.get("control_map", {})
    board_brief = data.get("board_brief", {})
    comp_brief = data.get("compensation_brief", {})
    result_brief = data.get("result_brief", {})
    vote_math_brief = data.get("vote_math_brief", {})

    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))} vote brief", ""]
    lines.append(f"- company_id: `{data.get('company_id', '')}`")
    lines.append(f"- status: `{payload.get('status', '')}`")
    requested_window = data.get("requested_window", {})
    if requested_window:
        lines.append(f"- 조사 구간: `{requested_window.get('start_date', '')}` ~ `{requested_window.get('end_date', '')}`")
    lines.append("")

    lines.append("## 회차")
    lines.append(f"- 선택 회차: {meeting_summary.get('meeting_type', '-')}")
    lines.append(f"- 회의일: {meeting_summary.get('meeting_date') or '-'}")
    lines.append(f"- 현재 단계: {meeting_summary.get('meeting_phase', '-')}")
    lines.append(f"- 결과 상태: {meeting_summary.get('result_status', '-')}")
    if meeting_summary.get("selection_basis"):
        lines.append(f"- 선택 근거: {meeting_summary.get('selection_basis')}")
    lines.append("")

    lines.append("## 판 구조")
    top_holder = meeting_summary.get("top_holder", {}) or {}
    lines.append(f"- 명부상 최대주주: {top_holder.get('name', '-') or '-'} {top_holder.get('ownership_pct', 0):.2f}%")
    lines.append(f"- 특수관계인 합계: {meeting_summary.get('related_total_pct', 0):.2f}%")
    lines.append(f"- 자사주: {meeting_summary.get('treasury_pct', 0):.2f}%")
    active_external = control_map.get("active_non_overlap_blocks", [])
    active_overlap = control_map.get("active_overlap_blocks", [])
    if active_external:
        lines.append(f"- 외부 능동 5% 블록: {', '.join(item.get('reporter', '') for item in active_external)}")
    if active_overlap:
        lines.append(f"- 명부 겹침 능동 블록: {', '.join(item.get('reporter', '') for item in active_overlap)}")
    lines.append("")

    lines.append("## 안건")
    lines.append(f"- 전체 안건 수: {meeting_summary.get('agenda_count', 0)}")
    for title in (data.get("agenda_brief", {}).get("titles", []) or [])[:10]:
        lines.append(f"- {title}")
    lines.append("")

    lines.append("## 후보자")
    lines.append(f"- 총 후보자 수: {meeting_summary.get('candidate_count', 0)}명")
    lines.append(f"- 사외이사 후보: {meeting_summary.get('outside_director_count', 0)}명")
    for candidate in (board_brief.get("candidates", []) or [])[:10]:
        line = f"- {candidate.get('name', '-')}"
        extras = []
        if candidate.get("role_type"):
            extras.append(candidate["role_type"])
        if candidate.get("recommender"):
            extras.append(f"추천인 {candidate['recommender']}")
        if candidate.get("major_relation"):
            extras.append(f"최대주주 관계 {candidate['major_relation']}")
        if extras:
            line += " | " + " / ".join(extras)
        lines.append(line)
    lines.append("")

    lines.append("## 보수")
    lines.append(f"- 보수 안건 수: {comp_brief.get('total_items', 0)}")
    if comp_brief.get("current_total_limit") is not None:
        lines.append(f"- 당기 한도 총액: {comp_brief.get('current_total_limit'):,}원")
    if comp_brief.get("prior_total_paid") is not None:
        lines.append(f"- 전기 실제 지급: {comp_brief.get('prior_total_paid'):,}원")
    if comp_brief.get("prior_utilization") is not None:
        lines.append(f"- 전기 소진율: {comp_brief.get('prior_utilization')}%")
    lines.append("")

    if result_brief:
        lines.append("## 결과")
        lines.append(f"- 의결 결과 확보 안건 수: {result_brief.get('agenda_count', 0)}")
        lines.append(f"- 가결 안건 수: {result_brief.get('passed_count', 0)}")
        if result_brief.get("result_format"):
            lines.append(f"- 결과공시 형식: `{result_brief.get('result_format')}`")
        if result_brief.get("numerical_vote_table_available") is not None:
            lines.append(f"- 수치표 제공 여부: `{result_brief.get('numerical_vote_table_available')}`")
        high_opp = result_brief.get("high_opposition_items", [])
        if high_opp:
            lines.append("- 반대율이 높았던 안건")
            for item in high_opp[:10]:
                lines.append(f"  - {item.get('number', '')} {item.get('agenda', '')} / 반대율 {item.get('opposition_rate', 0):.2f}%")
        lines.append("")

    if vote_math_brief:
        lines.append("## vote_math")
        lines.append(f"- vote_math status: `{vote_math_brief.get('status', '-')}`")
        if vote_math_brief.get("representative_pct") is not None:
            lines.append(f"- 대표 추정참석률: {vote_math_brief.get('representative_pct')}%")
        lines.append(f"- 비교 가능한 보통결의 안건 수: {vote_math_brief.get('comparable_item_count', 0)}건")
        if vote_math_brief.get("contestable_turnout_pct") is not None:
            lines.append(f"- 특수관계인 제외 추정 참석분: {vote_math_brief.get('contestable_turnout_pct')}%")
        if vote_math_brief.get("ex_related_turnout_pct") is not None:
            lines.append(f"- 특수관계인 제외 추정 참석률: {vote_math_brief.get('ex_related_turnout_pct')}%")
        if vote_math_brief.get("signal_level"):
            lines.append(f"- signal_level: `{vote_math_brief.get('signal_level')}`")
        for note in (vote_math_brief.get("notes", []) or [])[:5]:
            lines.append(f"- {note}")
        lines.append("")

    flags = data.get("key_flags", []) or []
    if flags:
        lines.append("## 체크 포인트")
        for flag in flags[:15]:
            lines.append(f"- {flag}")
        lines.append("")

    evidence_refs = payload.get("evidence_refs", []) or []
    if evidence_refs:
        lines.append("## 근거")
        for ref in evidence_refs[:10]:
            snippet = ref.get("snippet", "")
            label = f"{ref.get('section', '-')}"
            rcept_no = ref.get("rcept_no", "")
            if rcept_no:
                lines.append(f"- `{rcept_no}` {label}: {snippet}")
            else:
                lines.append(f"- {label}: {snippet}")

    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def prepare_vote_brief(
        company: str,
        meeting_type: str = "auto",
        year: int = 0,
        start_date: str = "",
        end_date: str = "",
        lookback_months: int = 12,
        format: str = "md",
    ) -> str:
        """desc: 이번 주총에서 봐야 할 회차, 지분 구조, 핵심 안건, 후보자, 보수, 결과를 한 장 메모로 묶는 action tool.
        when: 의결권 행사 준비, 내부 투자위원회 보고, 주총 전후 핵심 쟁점을 빠르게 정리할 때.
        rule: 현재는 추천 찬반을 단정하지 않고, 사실과 근거를 묶은 vote brief를 만든다. 회차 선택은 shareholder_meeting 규칙을 그대로 따른다.
        ref: shareholder_meeting, ownership_structure, evidence
        """
        payload = await build_vote_brief_payload(
            company,
            meeting_type=meeting_type,
            year=year or None,
            start_date=start_date,
            end_date=end_date,
            lookback_months=lookback_months,
        )
        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") in {"error", "ambiguous"}:
            return _render_error(payload)
        return _render(payload)
