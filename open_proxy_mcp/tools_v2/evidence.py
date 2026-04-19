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
        """desc: 인용 정보 제공자. rcept_no 문자열만으로 공시일·소스·뷰어 URL 유도. **API 호출 없음, 원문 스니펫 추출 없음** — 순수 메타 가공기.
        when: data tool 결과 evidence_refs의 출처 재확인. 또는 raw rcept_no의 공시일/소스를 빠르게 판단.
        rule: rcept_no 앞 8자리 → `rcept_dt`(YYYY-MM-DD). 9~10자리 → `source_type` (`80`=KIND, 그 외=DART). `viewer_url` 자동 생성 (DART: `dart.fss.or.kr/dsaf001/main.do?rcpNo=`, KIND: `kind.krx.co.kr/common/disclsviewer.do?method=search&acptno=`). 공시명(`report_nm`)은 upstream evidence_refs에만 있음. 14자리 숫자 아니면 `requires_review`.
        ref: 모든 data/action tool
        """
        payload = await build_evidence_payload(
            evidence_id=evidence_id,
            rcept_no=rcept_no,
        )
        if format == "json":
            return as_pretty_json(payload)
        return _render(payload)
