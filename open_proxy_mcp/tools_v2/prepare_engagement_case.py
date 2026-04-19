"""v2 prepare_engagement_case public tool."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.engagement_case import build_engagement_case_payload
from open_proxy_mcp.tools_v2._shared import _format_evidence_line


def _render_error(payload: dict[str, Any]) -> str:
    lines = [f"# prepare_engagement_case: {payload.get('subject', '')}", "", "engagement memo를 만들지 못했다."]
    for warning in payload.get("warnings", []):
        lines.append(f"- {warning}")
    return "\n".join(lines)


def _render(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    issue = data.get("issue_framing", {})
    contest = data.get("contest_signals", {})
    return_context = data.get("return_context", {})

    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))} engagement case", ""]
    lines.append(f"- company_id: `{data.get('company_id', '')}`")
    lines.append(f"- status: `{payload.get('status', '')}`")
    window = data.get("window", {})
    if window:
        lines.append(f"- 조사 구간: `{window.get('start_date', '')}` ~ `{window.get('end_date', '')}`")
    lines.append("")

    quality = data.get("quality", {})
    if quality:
        lines.append("## 품질")
        lines.append(f"- ownership_status: `{quality.get('ownership_status', '')}`")
        lines.append(f"- proxy_status: `{quality.get('proxy_status', '')}`")
        lines.append(f"- value_up_status: `{quality.get('value_up_status', '')}`")
        lines.append(f"- proxy_has_contest_signal: `{quality.get('proxy_has_contest_signal', False)}`")
        lines.append(f"- value_up_filing_count: `{quality.get('value_up_filing_count', 0)}`")
        lines.append("")

    if payload.get("warnings"):
        lines.append("## 유의사항")
        for warning in payload["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")

    lines.append("## 쟁점 프레이밍")
    for point in issue.get("points", [])[:10]:
        lines.append(f"- {point}")
    if not issue.get("points"):
        lines.append("- 확인된 쟁점 프레이밍이 없다")
    lines.append("")

    lines.append("## 지배구조 맥락")
    flags = issue.get("control_flags", {})
    lines.append(f"- 50% 이상: {flags.get('registry_majority', False)}")
    lines.append(f"- 30% 이상: {flags.get('registry_over_30pct', False)}")
    lines.append(f"- 자사주 5% 이상: {flags.get('treasury_over_5pct', False)}")
    lines.append(f"- 능동적 비겹침 블록 존재: {flags.get('active_non_overlap_block_exists', False)}")
    lines.append(f"- 능동적 겹침 블록 존재: {flags.get('active_overlap_block_exists', False)}")
    for observation in issue.get("control_observations", [])[:10]:
        lines.append(f"- {observation}")
    lines.append("")

    lines.append("## 분쟁 신호")
    contest_summary = contest.get("summary", {})
    lines.append(f"- 위임장/공개매수 공시: {contest_summary.get('proxy_filing_count', 0)}건")
    lines.append(f"- 주주측 문서: {contest_summary.get('shareholder_side_count', 0)}건")
    lines.append(f"- 소송/분쟁 공시: {contest_summary.get('litigation_count', 0)}건")
    lines.append(f"- 능동적 5% 시그널: {contest_summary.get('active_signal_count', 0)}건")
    players = contest.get("players", {})
    if players.get("company_side_filers"):
        lines.append(f"- 회사측 제출인: {', '.join(players.get('company_side_filers', []))}")
    if players.get("shareholder_side_filers"):
        lines.append(f"- 주주측 제출인: {', '.join(players.get('shareholder_side_filers', []))}")
    if players.get("active_external_blocks"):
        lines.append(f"- 명부와 안 겹치는 능동 5% 블록: {', '.join(players.get('active_external_blocks', []))}")
    if players.get("active_overlap_blocks"):
        lines.append(f"- 명부와 겹치는 능동 5% 블록: {', '.join(players.get('active_overlap_blocks', []))}")
    for point in contest.get("points", [])[:10]:
        lines.append(f"- {point}")
    lines.append("")

    lines.append("## 밸류업/주주환원 맥락")
    latest = return_context.get("latest", {})
    if latest:
        lines.append(f"- 최신 공시: {latest.get('disclosure_date', '-')} / {latest.get('report_name', '-')} / `{latest.get('rcept_no', '')}`")
    lines.append(f"- 확인된 공시 수: {return_context.get('filing_count', 0)}")
    highlights = return_context.get("highlights", [])
    if highlights:
        for item in highlights[:6]:
            lines.append(f"- {item}")
    else:
        lines.append("- 핵심 문장을 충분히 추출하지 못했다")
    lines.append("")

    key_flags = data.get("key_flags", []) or []
    if key_flags:
        lines.append("## 체크 포인트")
        for flag in key_flags[:15]:
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
    async def prepare_engagement_case(
        company: str,
        year: int = 0,
        start_date: str = "",
        end_date: str = "",
        lookback_months: int = 12,
        format: str = "md",
    ) -> str:
        """desc: engagement 메모 action tool. 지배구조 + 분쟁 신호 + 밸류업/주주환원 맥락을 한 장으로 묶음. **추천·처방 생성 X, 사실·근거만**.
        when: 투자자 대화 준비, 경영진 접촉, engagement case 초안, 내부 메모.
        rule: 단정적 결론 문구 금지. upstream evidence 범위를 넘어가는 판단 금지. upstream이 `partial`/`requires_review`이면 그대로 반영.
        upstream: ownership_structure + proxy_contest + value_up + evidence.
        ref: ownership_structure, proxy_contest, value_up, evidence
        """
        payload = await build_engagement_case_payload(
            company,
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
