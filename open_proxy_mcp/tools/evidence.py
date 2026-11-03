"""evidence public tool."""

from __future__ import annotations

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.evidence import build_evidence_payload


_SOURCE_LABELS = {
    "dart_xml": "DART 전자공시 (XML)",
    "dart_html": "DART 전자공시 (HTML 뷰어)",
    "dart_api": "DART OpenAPI",
    "kind_html": "KIND 거래소공시",
    "portal": "포털 시세",
    "internal": "내부 파생",
}

_CHANNEL_LABELS = {
    "dart": "DART 전자공시 접수",
    "fss": "금융감독원 안내",
    "krx_konex": "거래소 코넥스시장 수시공시",
    "krx_bond": "거래소 채권시장 공시",
    "krx_kospi": "거래소 유가증권시장 수시공시",
    "krx_kosdaq": "거래소 코스닥시장 수시공시",
    "unknown": "접수번호 9번째 자리로 알 수 없음",
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
    if rcept_dt:
        lines.append(f"- 공시일: `{rcept_dt}`")
    if report_nm:
        lines.append(f"- 공시명: {report_nm}")
    if rcept_no:
        lines.append(f"- 공시번호 {rcept_no}")
    if source_type:
        lines.append(f"- 소스: {source_label}")
    channel = data.get("filing_channel", "")
    if channel:
        lines.append(f"- 접수 창구: {_CHANNEL_LABELS.get(channel, channel)}")
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
        """desc: 인용 정보 제공자. rcept_no → 공시일·소스·뷰어 URL 유도. **API 호출 없음, 원문 추출 없음** — 순수 메타 가공기.
        when: evidence_refs 출처 재확인. raw rcept_no의 공시일/소스 빠른 판단.
        rule: 앞 8자리 → rcept_dt. 9번째 자리 = 접수 창구(0 DART · 1 금감원 · 6 코넥스 · 7 채권 · 8 유가 · 9 코스닥, 그 밖은 unknown) → filing_channel, 6·7·8·9 는 source_type=거래소 수시공시. viewer_url은 DART 뷰어로 통일. report_nm은 upstream evidence_refs에만 있음. 14자리 숫자 아니면 requires_review.
        ref: 모든 data/action tool
        """
        payload = await build_evidence_payload(
            evidence_id=evidence_id,
            rcept_no=rcept_no,
        )
        if format == "json":
            return as_pretty_json(payload)
        return _render(payload)
