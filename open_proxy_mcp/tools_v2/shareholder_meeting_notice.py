"""주총 소집공고 (사전) — DART 기반."""

from __future__ import annotations

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload
from open_proxy_mcp.tools_v2._shareholder_meeting_render import (
    render_aoi,
    render_board,
    render_compensation,
    render_ambiguous,
    render_error,
    render_summary,
)


# 260505 ralph: scope 정리 — full / agenda 폐지 (summary 흡수)
_NOTICE_SCOPES = {"summary", "board", "compensation", "aoi_change"}
_DEPRECATED_SCOPES = {"agenda", "full"}  # silent fallback to summary


def register_tools(mcp):

    @mcp.tool()
    async def shareholder_meeting_notice(
        company: str,
        meeting_type: str = "auto",
        scope: str = "summary",
        year: int = 0,
        start_date: str = "",
        end_date: str = "",
        lookback_months: int = 12,
        format: str = "md",
    ) -> str:
        """desc: 주주총회 **소집공고** (사전) — DART 기반. 안건·이사 후보·보수한도·정관변경. 빠르고 안정적 (DART API/XML).
        when: 주총 일정, 안건, 후보자, 보수한도, 정관변경 확인. 주총 결과는 별도 `shareholder_meeting_results`. 종합 분석은 `proxy_advise_before_meeting`.
        rule: 회사 식별이 exact가 아니면 자동 선택 안 함. 정정공시 있으면 최신 정정본 자동 선택. 소스는 DART 공시검색 + DART XML. PDF 다운로드 미사용.
        meeting_type: `auto`=정기/임시 최신 회차 비교 후 대표성 높은 쪽 / `annual`=정기만 / `extraordinary`=임시만
        scope: `summary`(기본, 메타+안건 hierarchy+1호 안건 메타) / `board`(이사·감사 후보 경력) / `compensation`(보수한도) / `aoi_change`(정관변경 변경전/후 + 퇴직금 변경 raw)
        ref: company, ownership_structure, proxy_contest, shareholder_meeting_results (사후 결과), proxy_advise_before_meeting (종합 분석), evidence
        """
        # 260505 ralph: deprecated scopes (agenda, full) — silent fallback to summary
        if scope in _DEPRECATED_SCOPES:
            scope = "summary"
        if scope not in _NOTICE_SCOPES:
            return f"# shareholder_meeting_notice\n\n지원 scope: {sorted(_NOTICE_SCOPES)} — `{scope}`는 미지원 (`agenda`/`full`은 `summary`에 통합됨, 결과는 `shareholder_meeting_results`)."
        payload = await build_shareholder_meeting_payload(
            company,
            meeting_type=meeting_type,
            scope=scope,
            year=year or None,
            start_date=start_date,
            end_date=end_date,
            lookback_months=lookback_months,
        )
        if format == "json":
            return as_pretty_json(payload)
        status = payload.get("status")
        if status == "ambiguous":
            return render_ambiguous(payload, "shareholder_meeting_notice")
        if scope == "board":
            return render_board(payload)
        if scope == "compensation":
            return render_compensation(payload)
        if scope == "aoi_change":
            return render_aoi(payload)
        if status in {"exact", "partial", "requires_review", "conflict"}:
            return render_summary(payload)
        return render_error(payload, "shareholder_meeting_notice")
