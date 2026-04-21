"""v2 screen_events public tool (discovery)."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.screen_events import (
    SUPPORTED_EVENT_TYPES,
    SUPPORTED_MARKETS,
    build_screen_events_payload,
)


def _render_error(payload: dict[str, Any]) -> str:
    lines = [f"# screen_events: {payload.get('subject', '')}", ""]
    for warning in payload.get("warnings", []):
        lines.append(f"- {warning}")
    data = payload.get("data", {})
    if data.get("supported_event_types"):
        lines.extend(["", "## 지원 event_type"])
        for ev in data["supported_event_types"]:
            lines.append(f"- `{ev}`")
    if data.get("supported_markets"):
        lines.extend(["", "## 지원 market"])
        for m in data["supported_markets"]:
            lines.append(f"- `{m}`")
    return "\n".join(lines)


def _render(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    window = data.get("window", {})
    usage = data.get("usage", {})
    lines = [
        f"# screen_events: {data.get('event_description', data.get('event_type', ''))}",
        "",
        f"- event_type: `{data.get('event_type', '')}`",
        f"- market: `{data.get('market', '')}`",
        f"- 조사 구간: `{window.get('start_date', '')}` ~ `{window.get('end_date', '')}`",
        f"- 결과: {data.get('result_count', 0)}건 / 상한 {data.get('max_results', 0)}",
        f"- status: `{payload.get('status', '')}`",
        "",
        "## 사용량",
        f"- DART API 호출: {usage.get('dart_api_calls', 0)}회 (분당 한도 {usage.get('dart_daily_limit_per_minute', 1000)}회)",
        f"- MCP tool 호출: {usage.get('mcp_tool_calls', 1)}회",
        "",
    ]
    if payload.get("warnings"):
        lines.append("## 유의사항")
        for warning in payload["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")

    results = data.get("results", [])
    if not results:
        lines.append("매칭된 공시 없음.")
        return "\n".join(lines)

    lines.extend([
        "## 결과",
        "| 기업명 | ticker | 시장 | 공시명 | 날짜 | 원문 |",
        "|--------|--------|------|--------|------|------|",
    ])
    for row in results:
        viewer = row.get("dart_viewer", "")
        link = f"[{row['rcept_no']}]({viewer})" if viewer else f"`{row['rcept_no']}`"
        lines.append(
            f"| {row['corp_name']} | `{row['ticker'] or '-'}` | {row['market']} | {row['report_nm']} | {row['rcept_dt']} | {link} |"
        )
    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def screen_events(
        event_type: str,
        start_date: str = "",
        end_date: str = "",
        market: str = "all",
        max_results: int = 50,
        format: str = "md",
    ) -> str:
        """desc: 이벤트 기반 기업 discovery tool. 특정 공시 유형(이벤트)을 최근에 제출한 기업 목록을 역조회. company-centric인 기존 data tool과 달리 N개 기업을 한 번에 추려낸다.
        when: "최근 임시주총 소집한 기업", "최근 30일 자사주 소각 결정한 기업", "최근 60일 대량보유 보고한 기업" 등 이벤트 → 기업 탐색이 필요할 때. 개별 기업 분석은 기존 data tool로 drill-down.
        rule: DART list.json을 pblntf_ty + report_nm 키워드 + corp_cls(market)로 필터. page_count=100, max_pages=20/ty까지 순회 후 중단. 결과는 rcept_dt 내림차순. 기본 lookback 1개월, 최대 max_results=100.
        event_type: [주총] `shareholder_meeting_notice` / [지분] `major_shareholder_change` `ownership_change_filing` `block_holding_5pct` `executive_ownership` / [자사주] `treasury_acquire` `treasury_dispose` `treasury_retire` / [분쟁] `proxy_solicit` `litigation` `management_dispute` / [밸류업] `value_up_plan` / [배당] `cash_dividend` `stock_dividend` / [희석] `rights_offering` `convertible_bond` `warrant_bond` `capital_reduction` / [내부거래] `equity_deal_acquire` `equity_deal_dispose` `supply_contract_conclude` `supply_contract_terminate` (총 22종).
        market: `kospi` / `kosdaq` / `all`(기본, KOSPI+KOSDAQ). KONEX/기타는 분석 유니버스에서 제외.
        ref: company (개별 식별), shareholder_meeting / ownership_structure / treasury_share / proxy_contest / value_up / dividend (drill-down)
        """
        payload = await build_screen_events_payload(
            event_type=event_type,
            start_date=start_date,
            end_date=end_date,
            market=market,
            max_results=max_results,
        )
        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") == "error":
            return _render_error(payload)
        return _render(payload)
