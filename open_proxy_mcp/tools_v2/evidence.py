"""v2 evidence public tool."""

from __future__ import annotations

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.evidence import build_evidence_payload


_SOURCE_LABELS = {
    "dart_xml": "DART 전자공시 (XML)",
    "dart_html": "DART 전자공시 (HTML 뷰어)",
    "dart_api": "DART OpenAPI",
    "kind_html": "KIND 거래소공시",
    "naver": "Naver",
    "internal": "내부 파생",
}


def _render(payload: dict) -> str:
    data = payload.get("data", {})
    rcept_no = data.get("rcept_no", "")
    rcept_dt = data.get("rcept_dt", "")
    report_nm = data.get("report_nm", "")
    source_type = data.get("source_type", "")
    viewer_url = data.get("viewer_url", "")
    source_label = _SOURCE_LABELS.get(source_type, source_type or "-")

    heading = report_nm or rcept_no or payload.get("subject", "")
    lines = [f"# evidence: {heading}", ""]
    lines.append(f"- status: `{payload.get('status', '')}`")
    if rcept_dt:
        lines.append(f"- 공시일: `{rcept_dt}`")
    if report_nm:
        lines.append(f"- 공시명: {report_nm}")
    if rcept_no:
        lines.append(f"- rcept_no: `{rcept_no}`")
    if source_type:
        lines.append(f"- 소스: {source_label}")
    if viewer_url:
        lines.append(f"- 원문 뷰어: {viewer_url}")

    if payload.get("warnings"):
        lines.append("")
        lines.append("## 유의사항")
        for warning in payload["warnings"]:
            lines.append(f"- {warning}")

    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def evidence(
        evidence_id: str = "",
        rcept_no: str = "",
        format: str = "md",
    ) -> str:
        """desc: data tool 결과에 붙은 evidence_id 또는 rcept_no로 "언제 어떤 공시를 참조했는지" 인용 정보를 확인한다. 원문은 viewer_url로 DART/KIND 뷰어에서 직접 열 수 있다.
        when: 배당, 주총, 분쟁, 밸류업 결과에 붙은 evidence_refs의 출처를 재확인하거나, 특정 rcept_no의 공시일/공시명을 빠르게 확인하고 싶을 때.
        rule: evidence tool은 원문도 공시명도 직접 조회하지 않는다. rcept_no 앞 8자리에서 공시일을, 9~10자리로 DART(00)/KIND(80) 소스를 판단하고 viewer_url을 생성한다. 공시명은 upstream evidence_refs에 이미 있거나, viewer_url로 직접 확인한다.
        ref: company, shareholder_meeting, ownership_structure, dividend, proxy_contest, value_up
        """
        payload = await build_evidence_payload(
            evidence_id=evidence_id,
            rcept_no=rcept_no,
        )
        if format == "json":
            return as_pretty_json(payload)
        return _render(payload)
