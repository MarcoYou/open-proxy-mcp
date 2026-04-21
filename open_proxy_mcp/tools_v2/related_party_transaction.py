"""v2 related_party_transaction public tool."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.related_party_transaction import build_related_party_transaction_payload


def _render_error(payload: dict[str, Any]) -> str:
    lines = [f"# related_party_transaction: {payload.get('subject', '')}", ""]
    for warning in payload.get("warnings", []):
        lines.append(f"- {warning}")
    return "\n".join(lines)


def _render_ambiguous(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    lines = [
        f"# related_party_transaction: {data.get('query', '')}",
        "",
        "회사 식별이 애매해 자동 선택하지 않았다.",
        "",
        "| 회사명 | ticker | corp_code | company_id |",
        "|------|--------|-----------|------------|",
    ]
    for item in data.get("candidates", []):
        lines.append(
            f"| {item.get('corp_name', '')} | `{item.get('ticker', '')}` | `{item.get('corp_code', '')}` | `{item.get('company_id', '')}` |"
        )
    return "\n".join(lines)


def _link(rcept_no: str) -> str:
    url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}" if rcept_no else ""
    return f"[{rcept_no}]({url})" if url else f"`{rcept_no}`"


def _direction_label(row: dict[str, Any]) -> str:
    t, d = row.get("type", ""), row.get("direction", "")
    if t == "equity_deal":
        return {"acquire": "취득·양수", "dispose": "처분·양도"}.get(d, "기타")
    if t == "supply_contract":
        return {"conclude": "체결", "terminate": "해지"}.get(d, "기타")
    return d


def _render(payload: dict[str, Any], scope: str) -> str:
    data = payload.get("data", {})
    window = data.get("window", {})
    counts = data.get("event_count", {})
    usage = data.get("usage", {})
    lines = [
        f"# {data.get('canonical_name', payload.get('subject', ''))} 내부거래·일감몰아주기 (related_party_transaction)",
        "",
        f"- company_id: `{data.get('company_id', '')}`",
        f"- scope: `{scope}`",
        f"- 조사 구간: `{window.get('start_date', '')}` ~ `{window.get('end_date', '')}`",
        f"- 사건 수: 타법인주식 {counts.get('equity_deal_total', 0)} (취득 {counts.get('equity_acquire', 0)} / 처분 {counts.get('equity_dispose', 0)}) / 단일공급계약 {counts.get('supply_contract_total', 0)} (체결 {counts.get('supply_conclude', 0)} / 해지 {counts.get('supply_terminate', 0)})",
        f"- 자회사 공시: {counts.get('subsidiary_reports', 0)}건 / 자율공시: {counts.get('autonomous_disclosures', 0)}건",
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

    lines.append("> 📋 본 tool은 list.json 메타만 수집. 거래 상대방·금액·특수관계 여부는 evidence tool로 원문 확인 필요.\n")

    if scope == "summary":
        timeline = data.get("events_timeline", [])
        if not timeline:
            lines.append("조사 구간 내 거래 공시 없음.")
            return "\n".join(lines)
        lines.extend([
            "## 사건 타임라인",
            "| 날짜 | 종류 | 방향 | 제목 | 제출인 | 자회사 | 자율 | 원문 |",
            "|------|------|------|------|--------|--------|------|------|",
        ])
        for ev in timeline:
            type_label = "주식거래" if ev.get("type") == "equity_deal" else "공급계약"
            sub = "Y" if ev.get("subsidiary") else "-"
            auto = "Y" if ev.get("autonomous") else "-"
            lines.append(
                f"| {ev.get('rcept_dt', '')} | {type_label} | {_direction_label(ev)} | {ev.get('report_nm', '')[:40]} | {ev.get('filer', '')[:20]} | {sub} | {auto} | {_link(ev.get('rcept_no', ''))} |"
            )

    if scope == "equity_deal":
        events = data.get("equity_deal_events", [])
        if not events:
            lines.append("타법인주식·출자증권 거래 없음.")
        else:
            lines.extend([
                "## 타법인주식·출자증권 거래",
                "| 날짜 | 방향 | 제목 | 제출인 | 자회사 | 자율 | 정정 | 원문 |",
                "|------|------|------|--------|--------|------|------|------|",
            ])
            for row in events:
                sub = "Y" if row.get("subsidiary_report") else "-"
                auto = "Y" if row.get("autonomous_disclosure") else "-"
                corr = "Y" if row.get("is_correction") else "-"
                lines.append(
                    f"| {row.get('rcept_dt', '')} | {_direction_label(row)} | {row.get('report_nm', '')[:50]} | {row.get('filer_name', '')[:20]} | {sub} | {auto} | {corr} | {_link(row.get('rcept_no', ''))} |"
                )

    if scope == "supply_contract":
        events = data.get("supply_contract_events", [])
        if not events:
            lines.append("단일판매·공급계약 없음.")
        else:
            lines.extend([
                "## 단일판매·공급계약",
                "| 날짜 | 방향 | 제목 | 제출인 | 자회사 | 자율 | 정정 | 원문 |",
                "|------|------|------|--------|--------|------|------|------|",
            ])
            for row in events:
                sub = "Y" if row.get("subsidiary_report") else "-"
                auto = "Y" if row.get("autonomous_disclosure") else "-"
                corr = "Y" if row.get("is_correction") else "-"
                lines.append(
                    f"| {row.get('rcept_dt', '')} | {_direction_label(row)} | {row.get('report_nm', '')[:50]} | {row.get('filer_name', '')[:20]} | {sub} | {auto} | {corr} | {_link(row.get('rcept_no', ''))} |"
                )

    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def related_party_transaction(
        company: str,
        scope: str = "summary",
        start_date: str = "",
        end_date: str = "",
        format: str = "md",
    ) -> str:
        """desc: 타법인주식 거래(취득/처분) + 단일판매·공급계약(체결/해지) 공시 통합. 일감몰아주기·내부거래 모니터링용 timeline tool. **list.json 메타만 수집** — 거래 상대방·금액·특수관계 여부는 원문 파싱이 필요해 evidence tool로 drill-down.
        when: 타법인주식 빈번 매매(자회사·관계회사 출자 변경), 단일공급계약 체결 패턴(특정 거래처 의존도), 자회사 주요경영사항 공시 흐름 추적. 일감몰아주기 사전 신호.
        rule: DART list.json + 제목 키워드 — 타법인주식: `B/I` pblntf_ty + ("타법인주식및출자증권양수/양도/취득/처분결정") / 공급계약: `I` pblntf_ty + ("단일판매ㆍ공급계약체결/해지"). 자회사 주요경영사항/자율공시/[기재정정] 플래그 별도 표시. 기본 lookback 24개월.
        scope: `summary`(기본, 통합 timeline) / `equity_deal`(타법인주식 거래) / `supply_contract`(단일공급계약).
        ref: ownership_structure (지분 변화), corporate_restructuring (M&A 맥락), evidence (원문 확인)
        """
        payload = await build_related_party_transaction_payload(
            company,
            scope=scope,
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
