"""v2 proxy_contest public tool."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.proxy_contest import build_proxy_contest_payload


def _render_error(payload: dict[str, Any], scope: str = "summary") -> str:
    message = "분쟁 관련 공시를 확정하지 못했다."
    if scope == "vote_math":
        message = "vote_math를 확정하지 못했다."
    lines = [f"# proxy_contest: {payload.get('subject', '')}", "", message]
    for warning in payload.get("warnings", []):
        lines.append(f"- {warning}")
    return "\n".join(lines)


def _render_ambiguous(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    lines = [f"# proxy_contest: {data.get('query', payload.get('subject', ''))}", "", "회사 식별이 애매해 분쟁 공시를 자동 선택하지 않았다.", "", "| 회사명 | ticker | corp_code | company_id |", "|------|--------|-----------|------------|"]
    for item in data.get("candidates", []):
        lines.append(f"| {item['corp_name']} | `{item['ticker']}` | `{item['corp_code']}` | `{item['company_id']}` |")
    return "\n".join(lines)


def _render(payload: dict[str, Any], scope: str) -> str:
    data = payload.get("data", {})
    summary = data.get("summary", {})
    players = data.get("players", {})
    control_context = data.get("control_context", {})
    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))} proxy contest", ""]
    lines.append(f"- company_id: `{data.get('company_id', '')}`")
    lines.append(f"- status: `{payload.get('status', '')}`")
    window = data.get("window", {})
    if window:
        lines.append(f"- 최근 12개월 조사구간: `{window.get('start_date', '')}` ~ `{window.get('end_date', '')}`")
    lines.append("")
    if payload.get("warnings"):
        lines.append("## 유의사항")
        for warning in payload["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")

    if scope == "summary":
        lines.append("## 요약")
        lines.append(f"- 위임장/공개매수 관련 공시: {summary.get('proxy_filing_count', 0)}건")
        lines.append(f"- 주주측 문서: {summary.get('shareholder_side_count', 0)}건")
        lines.append(f"- 소송/분쟁 공시: {summary.get('litigation_count', 0)}건")
        lines.append(f"- 능동적 5% 시그널: {summary.get('active_signal_count', 0)}건")
        top_holder = summary.get("top_holder", {})
        if top_holder:
            lines.append(f"- 명부상 최대주주: {top_holder.get('name', '')} {top_holder.get('ownership_pct', 0):.2f}%")
        lines.append(f"- 명부상 특수관계인 합계: {summary.get('related_total_pct', 0):.2f}%")
        lines.append(f"- 자사주: {summary.get('treasury_pct', 0):.2f}%")
        lines.extend(["", "## 판 구조", f"- 회사측 제출인: {', '.join(players.get('company_side_filers', [])) or '없음'}"])
        lines.append(f"- 주주측 제출인: {', '.join(players.get('shareholder_side_filers', [])) or '없음'}")
        lines.append(f"- 명부와 안 겹치는 능동 5% 블록: {', '.join(players.get('active_external_blocks', [])) or '없음'}")
        lines.append(f"- 명부와 겹치는 능동 5% 블록: {', '.join(players.get('active_overlap_blocks', [])) or '없음'}")
        if control_context.get("observations"):
            lines.extend(["", "## 관찰 포인트"])
            for item in control_context.get("observations", []):
                lines.append(f"- {item}")

    if scope in {"summary", "fight"}:
        lines.extend(["", "## fight", "| 날짜 | 구분 | 플레이어 분류 | 제출인 | 5%경영참여 | 소송연관 | 공시명 | rcept_no |", "|------|------|---------------|--------|-----------|----------|--------|----------|"])
        for row in data.get("fight", [])[:20]:
            has_5pct = "✓" if row.get("filer_has_5pct_active_block") else "-"
            in_lit = "✓" if row.get("filer_in_litigation") else "-"
            lines.append(f"| {row['disclosure_date']} | {row['side']} | {row.get('actor_group', '')} | {row['filer_name']} | {has_5pct} | {in_lit} | {row['report_name']} | `{row['rcept_no']}` |")

    if scope in {"summary", "litigation"}:
        lines.extend(["", "## litigation", "| 날짜 | 제출인 | 공시명 | rcept_no |", "|------|--------|--------|----------|"])
        for row in data.get("litigation", [])[:20]:
            lines.append(f"| {row['disclosure_date']} | {row['filer_name']} | {row['report_name']} | `{row['rcept_no']}` |")

    if scope in {"summary", "signals"}:
        lines.extend(["", "## 5% signals", "| 날짜 | 보고자 | 분류 | 지분율 | 목적 | rcept_no |", "|------|--------|------|--------|------|----------|"])
        for row in data.get("signals", [])[:20]:
            lines.append(f"| {row['report_date']} | {row['reporter']} | {row.get('actor_side', '')} | {row['ownership_pct']:.2f}% | {row['purpose']} | `{row['rcept_no']}` |")

    if scope == "timeline":
        lines.extend(["", "## timeline", "| 날짜 | 카테고리 | 주체 | 분류 | 이벤트 | rcept_no |", "|------|----------|------|------|--------|----------|"])
        for row in data.get("timeline", [])[:30]:
            lines.append(f"| {row['date']} | {row['category']} | {row.get('actor', '')} | {row.get('side', '')} | {row['title']} | `{row['rcept_no']}` |")

    if scope == "vote_math":
        vote_math = data.get("vote_math", {})
        attendance = vote_math.get("attendance_estimate", {})
        capital = vote_math.get("capital_structure", {})
        pressure = vote_math.get("pressure_signals", {})
        interpretation = vote_math.get("interpretation", {})
        meeting_ref = vote_math.get("meeting_reference", {})

        lines.extend(["", "## vote_math 회차"])
        lines.append(f"- selected_meeting_type: `{meeting_ref.get('meeting_type', '-')}`")
        lines.append(f"- meeting_date: `{meeting_ref.get('meeting_date') or '-'}`")
        lines.append(f"- result_rcept_no: `{meeting_ref.get('result_rcept_no') or '-'}`")
        lines.append(f"- result_status: `{meeting_ref.get('result_status', '-')}`")

        lines.extend(["", "## 대표 추정참석률"])
        lines.append(f"- 대표 추정참석률: {attendance.get('representative_pct') if attendance.get('representative_pct') is not None else '-'}%")
        lines.append(f"- 비교 가능한 보통결의 안건 수: {attendance.get('comparable_item_count', 0)}건")
        lines.append(f"- 제외 안건 수: {attendance.get('excluded_item_count', 0)}건")
        if attendance.get("min_pct") is not None and attendance.get("max_pct") is not None:
            lines.append(f"- 안건별 추정참석률 범위: {attendance.get('min_pct')}% ~ {attendance.get('max_pct')}%")
        lines.append(f"- 방법론: {attendance.get('methodology', '-')}")

        lines.extend(["", "## 표 구조"])
        lines.append(f"- 특수관계인 합계: {capital.get('related_total_pct', 0)}%")
        lines.append(f"- 자사주: {capital.get('treasury_pct', 0)}%")
        lines.append(f"- 의결권 기준 모수(자사주 차감 후): {capital.get('voting_share_base_pct', 0)}%")
        lines.append(f"- 특수관계인 제외 추정 참석분: {capital.get('contestable_turnout_pct') if capital.get('contestable_turnout_pct') is not None else '-'}%")
        lines.append(f"- 특수관계인 제외 추정 참석률: {capital.get('ex_related_turnout_pct') if capital.get('ex_related_turnout_pct') is not None else '-'}%")
        lines.append(f"- 명부와 안 겹치는 능동 블록 합계: {capital.get('active_external_block_total_pct', 0)}%")
        lines.append(f"- 명부와 겹치는 능동 블록 합계: {capital.get('active_overlap_block_total_pct', 0)}%")

        lines.extend(["", "## 압박 신호"])
        lines.append(f"- 주주측 제출인: {', '.join(pressure.get('shareholder_side_filers', [])) or '없음'}")
        lines.append(f"- 소송/분쟁 공시 수: {pressure.get('litigation_count', 0)}건")
        lines.append(f"- 고반대율 안건 수(10%+): {len(pressure.get('high_opposition_items', []))}건")
        lines.append(f"- 부결 안건 수: {len(pressure.get('failed_items', []))}건")
        lines.append(f"- signal_level: `{interpretation.get('signal_level', '-')}`")

        if pressure.get("high_opposition_items"):
            lines.extend(["", "## 고반대율 안건"])
            for item in pressure.get("high_opposition_items", [])[:10]:
                lines.append(f"- {item.get('number', '')} {item.get('agenda', '')} / 반대율 {item.get('opposition_rate', 0)}%")

        if attendance.get("items"):
            lines.extend(["", "## 비교에 사용한 안건"])
            for item in attendance.get("items", [])[:10]:
                lines.append(
                    f"- {item.get('number', '')} {item.get('agenda', '')} / 결의 {item.get('resolution_type', '-')}"
                    f" / 추정참석률 {item.get('estimated_attendance', '-') }%"
                )

        if interpretation.get("notes"):
            lines.extend(["", "## 해석 메모"])
            for note in interpretation.get("notes", []):
                lines.append(f"- {note}")

    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def proxy_contest(
        company: str,
        scope: str = "summary",
        year: int = 0,
        start_date: str = "",
        end_date: str = "",
        lookback_months: int = 12,
        format: str = "md",
    ) -> str:
        """desc: 위임장, 공개매수, 소송, 5% 경영참여 시그널을 한 탭에서 모아보는 분쟁 tool.
        when: 표대결 조짐, 주주측 캠페인, 소송, 능동적 5% 보유를 함께 보고 싶을 때.
        rule: DART D/B/I 공시만 사용한다. vote_math는 주총 결과가 있을 때만 보수적으로 계산하며, 승패 예측이 아니라 추정참석률과 지분 구조 관점의 표 신호만 보여준다.
        ref: company, shareholder_meeting, ownership_structure, evidence
        """
        payload = await build_proxy_contest_payload(
            company,
            scope=scope,
            year=year or None,
            start_date=start_date,
            end_date=end_date,
            lookback_months=lookback_months,
        )
        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") == "ambiguous":
            return _render_ambiguous(payload)
        if payload.get("status") in {"error", "requires_review"} and scope == "vote_math":
            return _render_error(payload, scope=scope)
        if payload.get("status") == "error":
            return _render_error(payload, scope=scope)
        return _render(payload, scope)
