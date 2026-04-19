"""v2 value_up public tool."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.value_up_v2 import build_value_up_payload


def _render_error(payload: dict[str, Any]) -> str:
    lines = [f"# value_up: {payload.get('subject', '')}", "", "밸류업 공시를 확정하지 못했다."]
    for warning in payload.get("warnings", []):
        lines.append(f"- {warning}")
    return "\n".join(lines)


def _render_ambiguous(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    lines = [f"# value_up: {data.get('query', payload.get('subject', ''))}", "", "회사 식별이 애매해 밸류업 공시를 자동 선택하지 않았다.", "", "| 회사명 | ticker | corp_code | company_id |", "|------|--------|-----------|------------|"]
    for item in data.get("candidates", []):
        lines.append(f"| {item['corp_name']} | `{item['ticker']}` | `{item['corp_code']}` | `{item['company_id']}` |")
    return "\n".join(lines)


def _render(payload: dict[str, Any], scope: str) -> str:
    data = payload.get("data", {})
    latest = data.get("latest", {})
    window = data.get("window", {})
    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))} 밸류업", ""]
    lines.append(f"- company_id: `{data.get('company_id', '')}`")
    lines.append(f"- status: `{payload.get('status', '')}`")
    if data.get("availability_status"):
        lines.append(f"- availability_status: `{data.get('availability_status', '')}`")
    if window:
        lines.append(f"- 조사 구간: `{window.get('start_date', '')}` ~ `{window.get('end_date', '')}`")
    lines.append("")
    if payload.get("warnings"):
        lines.append("## 유의사항")
        for warning in payload["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")

    if latest:
        lines.append("## 최신 공시")
        lines.append(f"- 공시일: {latest.get('disclosure_date', '-')}")
        lines.append(f"- 공시명: {latest.get('report_name', '-')}")
        lines.append(f"- 소스: `{latest.get('source_type', '-')}`")
        if latest.get("rcept_no"):
            lines.append(f"- rcept_no: `{latest.get('rcept_no', '')}`")
        if latest.get("acptno"):
            lines.append(f"- KIND acptno: `{latest.get('acptno', '')}`")
    else:
        diagnostic = data.get("search_diagnostics", {}).get("diagnostic_window", {})
        sample_filings = diagnostic.get("sample_filings", [])
        if sample_filings:
            lines.extend(["## 진단 구간에서 확인된 관련 공시", "| 소스 | 날짜 | 공시명 | 식별자 |", "|------|------|--------|--------|"])
            for item in sample_filings:
                filing_id = item.get("rcept_no") or item.get("acptno", "")
                lines.append(
                    f"| {item.get('source', '')} | {item.get('disclosure_date', '')} | {item.get('report_name', '')} | `{filing_id}` |"
                )

    if scope in {"summary", "timeline"}:
        lines.extend(["", "## 공시 타임라인", "| 날짜 | 공시명 | 제출인 | rcept_no |", "|------|--------|--------|----------|"])
        for item in data.get("items", []):
            filing_id = item.get("rcept_no") or item.get("acptno", "")
            lines.append(f"| {item.get('disclosure_date', '')} | {item.get('report_name', '')} | {item.get('filer_name', '')} | `{filing_id}` |")

    if scope in {"summary", "plan", "commitments"}:
        lines.extend(["", "## 핵심 문장"])
        for item in data.get("highlights", []):
            lines.append(f"- {item}")

    if scope == "plan":
        lines.extend(["", "## 원문 발췌", "```", data.get("latest_excerpt", "")[:1800], "```"])

    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def value_up(
        company: str,
        scope: str = "summary",
        year: int = 0,
        start_date: str = "",
        end_date: str = "",
        format: str = "md",
    ) -> str:
        """desc: 기업가치제고계획(밸류업) 공시와 핵심 commitment 문장을 한 탭에서 보여주는 tool.
        when: 밸류업 계획, 주주환원 commitment, ROE/PBR 관련 문구를 확인하고 싶을 때.
        rule: 먼저 DART 거래소 공시(I)에서 밸류업 키워드를 찾고, 없으면 KIND `기업가치 제고 계획(0184)` 검색으로 재시도한다. partial match는 자동 선택하지 않는다.
        ref: company, dividend, ownership_structure, evidence
        """
        payload = await build_value_up_payload(
            company,
            scope=scope,
            year=year or None,
            start_date=start_date,
            end_date=end_date,
        )
        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") == "ambiguous":
            return _render_ambiguous(payload)
        if payload.get("status") == "error":
            return _render_error(payload)
        return _render(payload, scope)
