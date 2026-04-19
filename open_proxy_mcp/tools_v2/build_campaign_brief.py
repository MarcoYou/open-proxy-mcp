"""v2 build_campaign_brief public tool."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.campaign_brief import build_campaign_brief_payload
from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.tools_v2._shared import _format_evidence_line


def _render_error(payload: dict[str, Any]) -> str:
    lines = [f"# build_campaign_brief: {payload.get('subject', '')}", "", "캠페인 사실 브리프를 만들지 못했다."]
    for warning in payload.get("warnings", []):
        lines.append(f"- {warning}")
    return "\n".join(lines)


def _render_ambiguous(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    lines = [
        f"# build_campaign_brief: {data.get('query', payload.get('subject', ''))}",
        "",
        "회사 식별이 애매해 캠페인 브리프를 자동 선택하지 않았다.",
        "",
        "| 회사명 | ticker | corp_code | company_id |",
        "|------|--------|-----------|------------|",
    ]
    for item in data.get("candidates", []):
        lines.append(f"| {item['corp_name']} | `{item['ticker']}` | `{item['corp_code']}` | `{item['company_id']}` |")
    return "\n".join(lines)


def _render(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    meeting = data.get("meeting_context", {})
    control_context = data.get("control_context", {})
    proxy_context = data.get("proxy_context", {})
    players = data.get("players", {})
    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))} campaign brief", ""]
    lines.append(f"- company_id: `{data.get('company_id', '')}`")
    lines.append(f"- status: `{payload.get('status', '')}`")
    if data.get("brief_note"):
        lines.append(f"- note: {data['brief_note']}")
    requested_window = data.get("requested_window", {})
    if requested_window:
        lines.append(f"- 조사 구간: `{requested_window.get('start_date', '')}` ~ `{requested_window.get('end_date', '')}`")
    lines.append("")

    quality = data.get("quality", {})
    if quality:
        lines.append("## 품질")
        lines.append(f"- notice_parse_source: `{quality.get('notice_parse_source', '')}`")
        lines.append(f"- meeting_summary_status: `{quality.get('meeting_summary_status', '')}`")
        lines.append(f"- agenda_status: `{quality.get('agenda_status', '')}`")
        lines.append(f"- board_status: `{quality.get('board_status', '')}`")
        lines.append(f"- ownership_status: `{quality.get('ownership_status', '')}`")
        lines.append(f"- proxy_status: `{quality.get('proxy_status', '')}`")
        lines.append(f"- meeting_phase: `{quality.get('meeting_phase', '')}`")
        lines.append(f"- result_status: `{quality.get('result_status', '')}`")
        lines.append("")

    lines.append("## 회의 맥락")
    lines.append(f"- 선택 회차: {meeting.get('summary', {}).get('meeting_type', '-')}")
    lines.append(f"- 회의일: {meeting.get('summary', {}).get('meeting_date') or '-'}")
    lines.append(f"- 현재 단계: {meeting.get('summary', {}).get('meeting_phase', '-')}")
    lines.append(f"- 결과 상태: {meeting.get('summary', {}).get('result_status', '-')}")
    if meeting.get("summary", {}).get("selection_basis"):
        lines.append(f"- 선택 근거: {meeting['summary']['selection_basis']}")
    coverage = meeting.get("coverage", {})
    if coverage:
        lines.append(f"- 최근 12개월 커버리지: {coverage.get('presence_flag', '-')}")
    lines.append(f"- 안건 수: {meeting.get('summary', {}).get('agenda_count', 0)}")
    lines.append(f"- 후보자 수: {meeting.get('summary', {}).get('candidate_count', 0)}")
    if meeting.get("agenda_titles"):
        lines.append("- 핵심 안건")
        for title in meeting.get("agenda_titles", [])[:10]:
            lines.append(f"  - {title}")
    if meeting.get("board_candidates"):
        lines.append("- 후보자")
        for candidate in meeting.get("board_candidates", [])[:10]:
            extras = []
            if candidate.get("role_type"):
                extras.append(candidate["role_type"])
            if candidate.get("recommender"):
                extras.append(f"추천인 {candidate['recommender']}")
            if candidate.get("major_relation"):
                extras.append(f"최대주주 관계 {candidate['major_relation']}")
            suffix = f" | {' / '.join(extras)}" if extras else ""
            lines.append(f"  - {candidate.get('name', '-')}{suffix}")
    lines.append("")

    lines.append("## 플레이어")
    lines.append(f"- 회사측 제출인: {', '.join(players.get('company_side_filers', [])) or '없음'}")
    lines.append(f"- 주주측 제출인: {', '.join(players.get('shareholder_side_filers', [])) or '없음'}")
    lines.append(f"- 명부와 안 겹치는 능동 5% 블록: {', '.join(players.get('active_external_blocks', [])) or '없음'}")
    lines.append(f"- 명부와 겹치는 능동 5% 블록: {', '.join(players.get('active_overlap_blocks', [])) or '없음'}")
    lines.append("")

    lines.append("## 지배구조")
    summary = control_context.get("summary", {})
    control_map = control_context.get("control_map", {})
    top_holder = summary.get("top_holder", {})
    lines.append(f"- 명부상 최대주주: {top_holder.get('name', '-') or '-'} {top_holder.get('ownership_pct', 0):.2f}%")
    lines.append(f"- 특수관계인 합계: {summary.get('related_total_pct', 0):.2f}%")
    lines.append(f"- 자사주: {summary.get('treasury_pct', 0):.2f}%")
    flags = control_map.get("flags", {})
    if flags:
        lines.append(f"- 플래그: 50%={flags.get('registry_majority', False)}, 30%={flags.get('registry_over_30pct', False)}, treasury>5%={flags.get('treasury_over_5pct', False)}")
    if control_map.get("observations"):
        lines.append("- 관찰 포인트")
        for item in control_map.get("observations", [])[:10]:
            lines.append(f"  - {item}")
    lines.append("")

    lines.append("## 분쟁 개요")
    proxy_summary = proxy_context.get("summary", {})
    lines.append(f"- 위임장/공개매수 공시: {proxy_summary.get('proxy_filing_count', 0)}건")
    lines.append(f"- 주주측 문서: {proxy_summary.get('shareholder_side_count', 0)}건")
    lines.append(f"- 소송/분쟁 공시: {proxy_summary.get('litigation_count', 0)}건")
    lines.append(f"- 능동적 5% 시그널: {proxy_summary.get('active_signal_count', 0)}건")
    lines.append(f"- 분쟁 신호 존재: {'예' if proxy_summary.get('has_contest_signal', False) else '아니오'}")
    lines.append("")

    lines.append("## 타임라인")
    lines.append("| 날짜 | 카테고리 | 주체 | 분류 | 이벤트 | rcept_no |")
    lines.append("|------|----------|------|------|--------|----------|")
    for row in data.get("timeline", [])[:30]:
        lines.append(f"| {row.get('date', '')} | {row.get('category', '')} | {row.get('actor', '')} | {row.get('side', '')} | {row.get('title', '')} | `{row.get('rcept_no', '')}` |")
    lines.append("")

    flags = data.get("key_flags", []) or []
    if flags:
        lines.append("## 핵심 플래그")
        for flag in flags[:15]:
            lines.append(f"- {flag}")
        lines.append("")

    evidence_refs = payload.get("evidence_refs", []) or []
    if evidence_refs:
        lines.append("## 근거")
        for ref in evidence_refs[:10]:
            lines.append(_format_evidence_line(ref))

    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def build_campaign_brief(
        company: str,
        meeting_type: str = "auto",
        year: int = 0,
        start_date: str = "",
        end_date: str = "",
        lookback_months: int = 12,
        format: str = "md",
    ) -> str:
        """desc: 캠페인 사실 브리프 action tool. 위임장 + 소송 + 5% 시그널 + 주총 회차 + 지배구조를 timeline/players/key_flags로 정리.
        when: 주주활동 맥락을 빠르게 파악, 누가 언제 어떤 문서를 냈는지 이벤트 시퀀스 정리.
        rule: **fact brief 전용** — 자동 추천, vote math 예측 금지. timeline, players, control_context, meeting_context, key_flags만 정리. upstream의 retail_activism 분리와 교차 힌트 그대로 노출.
        upstream: proxy_contest + ownership_structure + shareholder_meeting + evidence.
        ref: proxy_contest, ownership_structure, shareholder_meeting, evidence
        """
        payload = await build_campaign_brief_payload(
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
            if payload.get("status") == "ambiguous":
                return _render_ambiguous(payload)
            return _render_error(payload)
        return _render(payload)
