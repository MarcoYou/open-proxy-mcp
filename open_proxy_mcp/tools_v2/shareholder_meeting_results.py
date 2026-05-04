"""주총 결과 (사후) — KIND 기반."""

from __future__ import annotations

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload
from open_proxy_mcp.tools_v2._shareholder_meeting_render import (
    render_ambiguous,
    render_error,
    render_results,
)


def register_tools(mcp):

    @mcp.tool()
    async def shareholder_meeting_results(
        company: str,
        meeting_type: str = "auto",
        year: int = 0,
        start_date: str = "",
        end_date: str = "",
        lookback_months: int = 12,
        format: str = "md",
    ) -> str:
        """desc: 주주총회 **의결 결과** (사후) — KIND 기반. 안건별 가결/부결 + 찬반율. 4-5s (KIND 웹 스크래핑).
        when: 주총 종료 후 실제 의결 결과 확인. 사전 안건은 `shareholder_meeting_notice`. 종합 결과 보고는 `proxy_result_after_meeting`.
        rule: rcept_no 80→00 변환으로 KIND whitelist 매칭. PDF 다운로드 X. 결과 미공시 (가결 후 KIND 노출 지연) 시 status=pending_or_missing.
        meeting_type: `auto`=대표성 높은 회차 자동 / `annual` / `extraordinary`
        ref: shareholder_meeting_notice (사전 안건), proxy_result_after_meeting (종합 사후 보고), evidence
        """
        payload = await build_shareholder_meeting_payload(
            company,
            meeting_type=meeting_type,
            scope="results",
            year=year or None,
            start_date=start_date,
            end_date=end_date,
            lookback_months=lookback_months,
        )
        if format == "json":
            return as_pretty_json(payload)
        status = payload.get("status")
        if status == "ambiguous":
            return render_ambiguous(payload, "shareholder_meeting_results")
        if status in {"exact", "partial", "requires_review", "conflict"}:
            return render_results(payload)
        return render_error(payload, "shareholder_meeting_results")
