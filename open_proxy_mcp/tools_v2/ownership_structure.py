"""v2 ownership_structure public tool."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.ownership_structure import build_ownership_structure_payload


def _render_error(payload: dict[str, Any]) -> str:
    lines = [f"# ownership_structure: {payload.get('subject', '')}", "", "지분 구조를 확정하지 못했다."]
    for warning in payload.get("warnings", []):
        lines.append(f"- {warning}")
    return "\n".join(lines)


def _render_ambiguous(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    lines = [
        f"# ownership_structure: {data.get('query', payload.get('subject', ''))}",
        "",
        "회사 식별이 애매해 지분 구조를 자동 선택하지 않았다.",
        "",
        "| 회사명 | ticker | corp_code | company_id |",
        "|------|--------|-----------|------------|",
    ]
    for item in data.get("candidates", []):
        lines.append(
            f"| {item.get('corp_name', '')} | `{item.get('ticker', '')}` | `{item.get('corp_code', '')}` | `{item.get('company_id', '')}` |"
        )
    return "\n".join(lines)


def _render(payload: dict[str, Any], scope: str) -> str:
    data = payload.get("data", {})
    summary = data.get("summary", {})
    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))} 지분 구조", ""]
    lines.append(f"- company_id: `{data.get('company_id', '')}`")
    lines.append(f"- status: `{payload.get('status', '')}`")
    lines.append("")
    if payload.get("warnings"):
        lines.append("## 유의사항")
        for warning in payload["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")

    if scope == "summary":
        top = summary.get("top_holder") or {}
        lines.append("## 요약")
        lines.append(f"- 최대주주: {top.get('name', '-') or '-'} {top.get('ownership_pct', 0):.2f}%")
        lines.append(f"- 특수관계인 합계: {summary.get('related_total_pct', 0):.2f}%")
        lines.append(f"- 자사주: {summary.get('treasury_shares', 0):,}주 ({summary.get('treasury_pct', 0):.2f}%)")
        lines.append(f"- 능동적 5% 시그널: {summary.get('active_signal_count', 0)}건")

    if scope in {"summary", "major_holders", "control_map"}:
        lines.extend(["", "## 최대주주/특수관계인", "| 이름 | 관계 | 지분율 | 보유주식수 |", "|------|------|--------|-----------|"])
        for row in data.get("major_holders", [])[:20]:
            lines.append(f"| {row['name']} | {row['relation'] or '-'} | {row['ownership_pct']:.2f}% | {row['shares']:,} |")

    if scope in {"summary", "blocks", "control_map"}:
        lines.extend(["", "## 5% 대량보유 최신", "| 보고자 | 지분율 | 보유목적 | 날짜 | rcept_no |", "|--------|--------|----------|------|----------|"])
        for row in data.get("blocks", [])[:15]:
            lines.append(f"| {row['reporter']} | {row['ownership_pct']:.2f}% | {row['purpose']} | {row['report_date']} | `{row['rcept_no']}` |")

    if scope in {"summary", "treasury"}:
        treasury = data.get("treasury", {})
        lines.extend(["", "## 자사주", f"- 발행주식수: {treasury.get('issued_shares', 0):,}주", f"- 자사주: {treasury.get('treasury_shares', 0):,}주", f"- 자사주 비중: {treasury.get('treasury_pct', 0):.2f}%"])

    if scope == "timeline":
        lines.extend(["", "## 지분 변화 타임라인", "| 날짜 | 보고자 | 지분율 | 목적 | rcept_no |", "|------|--------|--------|------|----------|"])
        for row in data.get("timeline", [])[:30]:
            lines.append(f"| {row['report_date']} | {row['reporter']} | {row['ownership_pct']:.2f}% | {row['purpose']} | `{row['rcept_no']}` |")

    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def ownership_structure(
        company: str,
        scope: str = "summary",
        year: int = 0,
        format: str = "md",
    ) -> str:
        """desc: 최대주주, 특수관계인, 5% 대량보유, 자사주를 한 탭에서 보는 지분 구조 tool.
        when: 지배력 구조, 최대주주 비중, 자사주 규모, 5% 시그널을 빠르게 보고 싶을 때.
        rule: 사업보고서 기반 공식 API를 먼저 쓰고, 5% 대량보유의 목적은 최신 원문으로만 보강한다. partial match는 자동 선택하지 않는다.
        ref: company, proxy_contest, evidence
        """
        payload = await build_ownership_structure_payload(company, scope=scope, year=year or None)
        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") == "ambiguous":
            return _render_ambiguous(payload)
        if payload.get("status") == "error":
            return _render_error(payload)
        return _render(payload, scope)

