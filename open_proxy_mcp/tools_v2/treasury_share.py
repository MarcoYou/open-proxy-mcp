"""v2 treasury_share public tool."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.treasury_share import build_treasury_share_payload


_EVENT_LABELS = {
    "acquisition_decision": "취득결정",
    "disposal_decision": "처분결정",
    "trust_contract": "신탁체결",
    "trust_termination": "신탁해지",
    "cancelation_decision": "소각결정",
}


def _render_ambiguous(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    lines = [f"# treasury_share: {data.get('query', payload.get('subject', ''))}", "", "회사 식별이 애매해 자사주 공시를 자동 선택하지 않았다.", "", "| 회사명 | ticker | corp_code |", "|------|--------|-----------|"]
    for c in data.get("candidates", []):
        lines.append(f"| {c['corp_name']} | `{c['ticker']}` | `{c['corp_code']}` |")
    return "\n".join(lines)


def _render_error(payload: dict[str, Any]) -> str:
    lines = [f"# treasury_share: {payload.get('subject', '')}", "", "자사주 공시를 확정하지 못했다."]
    for w in payload.get("warnings", []):
        lines.append(f"- {w}")
    return "\n".join(lines)


def _render(payload: dict[str, Any], scope: str) -> str:
    data = payload.get("data", {})
    s = data.get("summary", {}) or {}
    window = data.get("window", {}) or {}
    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))} 자사주 이벤트", ""]
    lines.append(f"- status: `{payload.get('status', '')}`")
    lines.append(f"- 조사 구간: `{window.get('start_date', '-')}` ~ `{window.get('end_date', '-')}`")
    lines.append("")
    if payload.get("warnings"):
        lines.append("## 유의사항")
        for w in payload["warnings"]:
            lines.append(f"- {w}")
        lines.append("")

    if data.get("no_filing"):
        lines.extend([
            "## 공시 없음",
            "- 조사 구간 내 자사주 이벤트 공시 없음 (정상 NO_FILING).",
            f"- 연간 누적은 `scope='annual'`로 확인할 수 있다.",
            "",
        ])

    lines.extend([
        "## 이벤트 집계",
        "| 유형 | 건수 |",
        "|------|------|",
        f"| 취득결정 | {s.get('acquisition_count', 0)} (소각목적 **{s.get('acquisition_for_cancelation_count', 0)}**) |",
        f"| 처분결정 | {s.get('disposal_count', 0)} |",
        f"| 신탁체결 | {s.get('trust_contract_count', 0)} |",
        f"| 신탁해지 | {s.get('trust_termination_count', 0)} |",
        f"| 소각결정 (별도) | {s.get('cancelation_count', 0)} |",
        f"| **합계** | **{s.get('total_event_count', 0)}** |",
        "",
    ])

    if s.get("acquisition_shares_total"):
        lines.append(f"- 취득결정 총 수량: {s['acquisition_shares_total']:,}주")
    if s.get("acquisition_amount_total_krw"):
        lines.append(f"- 취득결정 총 금액: {s['acquisition_amount_total_krw']:,}원")
    if s.get("acquisition_for_cancelation_amount_total_krw"):
        lines.append(f"- **소각목적 취득 총 금액: {s['acquisition_for_cancelation_amount_total_krw']:,}원**")
    if s.get("trust_contract_amount_total_krw"):
        lines.append(f"- 신탁체결 총 규모: {s['trust_contract_amount_total_krw']:,}원")

    events_to_show = data.get("events") or data.get("latest_events") or []
    if events_to_show:
        lines.extend([
            "",
            "## 이벤트 타임라인",
            "| 공시일 | 유형 | 주식수 | 금액(원) | 공시명 | rcept_no |",
            "|--------|------|--------|---------|--------|----------|",
        ])
        for ev in events_to_show[:30]:
            ev_type = _EVENT_LABELS.get(ev.get("event", ""), ev.get("event", ""))
            shares = f"{ev.get('shares', 0):,}" if ev.get("shares") else "-"
            amount = f"{ev.get('amount_krw', 0):,}" if ev.get("amount_krw") else "-"
            lines.append(f"| {ev.get('rcept_dt', '-')} | {ev_type} | {shares} | {amount} | {ev.get('report_nm', '')} | `{ev.get('rcept_no', '')}` |")

    if scope == "annual" and data.get("annual"):
        annual = data["annual"]
        lines.extend([
            "",
            "## 연간 누적 (사업보고서 기준)",
            f"- 발행주식수: {annual.get('issued_shares', 0):,}주",
            f"- 자기주식수: {annual.get('treasury_shares', 0):,}주",
            f"- 자기주식 비율: {annual.get('treasury_pct', 0)}%",
            f"- 유통주식수: {annual.get('tradable_shares', 0):,}주",
        ])
        if annual.get("rows"):
            lines.extend(["", "| 구분 | 기초 | 취득 | 처분 | 소각 | 기말 |", "|------|------|------|------|------|------|"])
            for r in annual["rows"]:
                lines.append(f"| {r.get('category', '')} | {r.get('begin_shares', 0):,} | {r.get('acquired_shares', 0):,} | {r.get('disposed_shares', 0):,} | {r.get('retired_shares', 0):,} | {r.get('end_shares', 0):,} |")

    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def treasury_share(
        company: str,
        scope: str = "summary",
        year: int = 0,
        start_date: str = "",
        end_date: str = "",
        lookback_months: int = 24,
        format: str = "md",
    ) -> str:
        """desc: 자기주식 이벤트 전용 tool. 취득·처분·소각·신탁 결정 공시를 한 탭에서 집계. `value_up`(정책)과 `ownership_structure(scope=treasury)`(잔고)와 함께 주주환원 분석의 사실 축.
        when: 자사주 취득·소각·신탁 이력·규모를 확인할 때. 특히 소각 규모로 실제 주주환원 여부를 검증할 때.
        rule: 5개 DART 공시를 모아서 병렬 조회 — (1) `tsstkAqDecsn` 취득결정 (2) `tsstkDpDecsn` 처분결정 (3) `tsstkAqTrctrCnsDecsn` 신탁체결 (4) `tsstkAqTrctrCcDecsn` 신탁해지 (5) `list.json` keyword="자기주식소각결정" 소각결정 (별도 API 없음). 연간 누적은 `scope=annual`에서 사업보고서 기반 `tesstkAcqsDspsSttus`를 재사용.
        scope: `summary`(기본, 집계 + 최신 5건) / `events`(전 이벤트 타임라인) / `acquisition`(취득·신탁체결만) / `disposal`(처분·신탁해지만) / `cancelation`(소각만) / `annual`(연간 누적 잔고/소각).
        ref: value_up (주주환원 정책), ownership_structure (현재 잔고), dividend (배당과 합친 총 환원), evidence
        """
        payload = await build_treasury_share_payload(
            company,
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
            return _render_ambiguous(payload)
        if status == "error":
            return _render_error(payload)
        return _render(payload, scope)
