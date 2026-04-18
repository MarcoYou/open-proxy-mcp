"""v2 evidence public tool."""

from __future__ import annotations

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.evidence import build_evidence_payload


def _render(payload: dict) -> str:
    data = payload.get("data", {})
    lines = [f"# evidence {data.get('rcept_no', payload.get('subject', ''))}", ""]
    lines.append(f"- status: `{payload.get('status', '')}`")
    if payload.get("warnings"):
        lines.append("")
        for warning in payload["warnings"]:
            lines.append(f"- {warning}")
    if data.get("rcept_no"):
        lines.extend([
            "",
            f"- rcept_no: `{data.get('rcept_no', '')}`",
            f"- html_available: {data.get('html_available')}",
            f"- text_length: {data.get('text_length')}",
            "",
            "## snippet",
            "```",
            data.get("snippet", ""),
            "```",
        ])
    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def evidence(
        evidence_id: str = "",
        rcept_no: str = "",
        keyword: str = "",
        format: str = "md",
    ) -> str:
        """desc: 결과 뒤에 붙은 evidence_id 또는 rcept_no로 원문 발췌를 다시 여는 근거 tool.
        when: 배당, 주총, 분쟁, 밸류업 결과를 보고 실제 공시 원문 문장을 다시 확인하고 싶을 때.
        rule: 현재는 DART XML 기반 evidence를 우선 지원한다. evidence_id에 rcept_no가 없으면 직접 원문으로 펼치지 못할 수 있다.
        ref: company, shareholder_meeting, ownership_structure, dividend, proxy_contest, value_up
        """
        payload = await build_evidence_payload(evidence_id=evidence_id, rcept_no=rcept_no, keyword=keyword)
        if format == "json":
            return as_pretty_json(payload)
        return _render(payload)
