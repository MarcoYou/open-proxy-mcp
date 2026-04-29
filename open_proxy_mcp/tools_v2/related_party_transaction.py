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

    has_details = any(row.get("details") for row in (data.get("events_timeline") or data.get("equity_deal_events") or data.get("supply_contract_events") or []))
    if not has_details:
        lines.append("> 📋 기본 모드는 list.json 메타만 수집. `include_details=True`로 원문 파싱(최근 건) 또는 evidence tool로 원문 확인.\n")
    else:
        lines.append("> ✓ 원문 파싱 보강 적용됨 (최근 건). 거래 상대방/금액/특수관계/자산대비비율 포함.\n")

    if scope == "summary":
        timeline = data.get("events_timeline", [])
        if not timeline:
            if data.get("no_filing"):
                lines.append("## 공시 없음")
                lines.append("- 조사 구간 내 거래 공시 없음 (정상 NO_FILING).")
            else:
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
            # 원문 파싱 상세
            for row in events:
                d = row.get("details")
                if not d:
                    continue
                lines.extend([
                    f"\n### 상세 ({row.get('rcept_dt')} — {row.get('report_nm', '')[:40]})",
                    f"- 거래 상대방: **{d.get('counterparty_name', '-') or '-'}** (관계: {d.get('counterparty_relationship', '-') or '-'}, 힌트: {d.get('special_relation_hint', '-') or '-'})",
                    f"- 사업: {d.get('counterparty_business', '-') or '-'}",
                    f"- 거래금액: {d.get('amount_won', '-') or '-'}원 / 자기자본대비 {d.get('equity_ratio_pct', '-') or '-'}% / 자산대비 {d.get('asset_ratio_pct', '-') or '-'}%",
                    f"- 취득 후 지분: {d.get('post_ownership_pct', '-') or '-'}%",
                    f"- 방법: {d.get('method', '-') or '-'}",
                    f"- 목적: {d.get('purpose', '-') or '-'}",
                    f"- 풋옵션: {d.get('put_option', '-') or '-'}",
                    f"- 최대주주·임원 관계: {d.get('major_shareholder_relation', '-') or '-'}",
                ])

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
            # 원문 파싱 상세
            for row in events:
                d = row.get("details")
                if not d:
                    continue
                lines.extend([
                    f"\n### 상세 ({row.get('rcept_dt')} — {row.get('report_nm', '')[:40]})",
                    f"- 계약 종류: {d.get('contract_type', '-') or '-'} / 체결명: {d.get('contract_name', '-') or '-'}",
                    f"- 계약금액: {d.get('contract_amount_won', '-') or '-'}원 / 최근매출: {d.get('recent_revenue_won', '-') or '-'}원 / **매출대비 {d.get('revenue_ratio_pct', '-') or '-'}%**",
                    f"- 상대방: **{d.get('counterparty_name', '-') or '-'}** (관계: {d.get('counterparty_relationship', '-') or '-'}, 힌트: {d.get('special_relation_hint', '-') or '-'})",
                    f"- 계약기간: {d.get('period_start', '-') or '-'} ~ {d.get('period_end', '-') or '-'} (체결일 {d.get('signing_date', '-') or '-'})",
                ])

    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def related_party_transaction(
        company: str,
        scope: str = "summary",
        start_date: str = "",
        end_date: str = "",
        include_details: bool = False,
        details_limit: int = 5,
        format: str = "md",
    ) -> str:
        """desc: 타법인주식 거래(취득/처분) + 단일판매·공급계약(체결/해지) 공시 통합. 일감몰아주기·내부거래 모니터링. 기본은 list.json 메타, `include_details=True`면 원문 파싱으로 거래 상대방/금액/자산대비비율/특수관계 힌트까지 노출.
        when: 타법인주식 빈번 매매(자회사·관계회사 출자 변경), 단일공급계약 체결 패턴(특정 거래처 의존도), 자회사 주요경영사항 공시 흐름 추적. 일감몰아주기 사전 신호. 깊은 분석이 필요하면 `include_details=True`.
        rule: DART list.json + 제목 키워드 — 타법인주식: `B/I` pblntf_ty + ("타법인주식및출자증권양수/양도/취득/처분결정") / 공급계약: `I` pblntf_ty + ("단일판매ㆍ공급계약체결/해지"). 자회사 주요경영사항/자율공시/[기재정정] 플래그 별도 표시. 기본 lookback 24개월. include_details=True 시 최근 N건(기본 5) DART 원문 다운로드 + 파싱(상대방명, 관계, 금액, 자산/매출대비비율, 방법, 목적).
        scope: `summary`(기본, 통합 timeline) / `equity_deal`(타법인주식 거래) / `supply_contract`(단일공급계약).
        include_details: True면 원문 파싱 상세 추가 (DART API 호출 N회 증가). 기본 False.
        details_limit: 원문 파싱 대상 건수 (기본 5, 최대 10 권장).
        ref: ownership_structure (지분 변화), corporate_restructuring (M&A 맥락), evidence (원문 확인)
        """
        payload = await build_related_party_transaction_payload(
            company,
            scope=scope,
            start_date=start_date,
            end_date=end_date,
            include_details=include_details,
            details_limit=max(1, min(details_limit, 10)),
        )
        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") == "ambiguous":
            return _render_ambiguous(payload)
        if payload.get("status") == "error":
            return _render_error(payload)
        return _render(payload, scope)
