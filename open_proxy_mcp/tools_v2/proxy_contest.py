"""v2 proxy_contest public tool."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.proxy_contest import build_proxy_contest_payload


def _render_error(payload: dict[str, Any]) -> str:
    lines = [f"# proxy_contest: {payload.get('subject', '')}", "", "분쟁 관련 공시를 확정하지 못했다."]
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
    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))} proxy contest", ""]
    lines.append(f"- company_id: `{data.get('company_id', '')}`")
    lines.append(f"- status: `{payload.get('status', '')}`")
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

    if scope in {"summary", "fight"}:
        lines.extend(["", "## fight", "| 날짜 | 구분 | 제출인 | 공시명 | rcept_no |", "|------|------|--------|--------|----------|"])
        for row in data.get("fight", [])[:20]:
            lines.append(f"| {row['disclosure_date']} | {row['side']} | {row['filer_name']} | {row['report_name']} | `{row['rcept_no']}` |")

    if scope in {"summary", "litigation"}:
        lines.extend(["", "## litigation", "| 날짜 | 제출인 | 공시명 | rcept_no |", "|------|--------|--------|----------|"])
        for row in data.get("litigation", [])[:20]:
            lines.append(f"| {row['disclosure_date']} | {row['filer_name']} | {row['report_name']} | `{row['rcept_no']}` |")

    if scope in {"summary", "signals"}:
        lines.extend(["", "## 5% signals", "| 날짜 | 보고자 | 지분율 | 목적 | rcept_no |", "|------|--------|--------|------|----------|"])
        for row in data.get("signals", [])[:20]:
            lines.append(f"| {row['report_date']} | {row['reporter']} | {row['ownership_pct']:.2f}% | {row['purpose']} | `{row['rcept_no']}` |")

    if scope == "timeline":
        lines.extend(["", "## timeline", "| 날짜 | 카테고리 | 이벤트 | rcept_no |", "|------|----------|--------|----------|"])
        for row in data.get("timeline", [])[:30]:
            lines.append(f"| {row['date']} | {row['category']} | {row['title']} | `{row['rcept_no']}` |")

    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def proxy_contest(
        company: str,
        scope: str = "summary",
        year: int = 0,
        format: str = "md",
    ) -> str:
        """desc: 위임장, 공개매수, 소송, 5% 경영참여 시그널을 한 탭에서 모아보는 분쟁 tool.
        when: 표대결 조짐, 주주측 캠페인, 소송, 능동적 5% 보유를 함께 보고 싶을 때.
        rule: DART D/B/I 공시만 사용한다. vote_math는 아직 열지 않고, 싸움이 있었는지와 어떤 문서가 나왔는지를 우선 보여준다.
        ref: company, shareholder_meeting, ownership_structure, evidence
        """
        payload = await build_proxy_contest_payload(company, scope=scope, year=year or None)
        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") == "ambiguous":
            return _render_ambiguous(payload)
        if payload.get("status") in {"error", "requires_review"} and scope == "vote_math":
            return _render_error(payload)
        if payload.get("status") == "error":
            return _render_error(payload)
        return _render(payload, scope)

