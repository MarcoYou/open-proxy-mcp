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
        """desc: 주주총회 **의결 결과** (사후). 안건별 가결/부결 + 찬반율. DART API 우선, KIND fallback.
        when: 주총 종료 후 실제 의결 결과 확인. 사전 안건은 `shareholder_meeting_notice`. 종합 보고는 `proxy_result_after_meeting`.
        rule: rcept_no 80 prefix (수시공시) 본문 fetch. 결과 미공시(KIND 노출 지연) 시 status=pending_or_missing.
        meeting_type: `auto` / `annual` / `extraordinary`
        ref: shareholder_meeting_notice, proxy_result_after_meeting, evidence
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
