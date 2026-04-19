"""v2 dividend public tool."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.dividend_v2 import build_dividend_payload


def _render_error(payload: dict[str, Any]) -> str:
    lines = [f"# dividend: {payload.get('subject', '')}", "", "배당 데이터를 확정하지 못했다."]
    for warning in payload.get("warnings", []):
        lines.append(f"- {warning}")
    return "\n".join(lines)


def _render_ambiguous(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    lines = [
        f"# dividend: {data.get('query', payload.get('subject', ''))}",
        "",
        "회사 식별이 애매해 배당 데이터를 자동 선택하지 않았다.",
        "",
        "| 회사명 | ticker | corp_code | company_id |",
        "|------|--------|-----------|------------|",
    ]
    for item in data.get("candidates", []):
        lines.append(f"| {item['corp_name']} | `{item['ticker']}` | `{item['corp_code']}` | `{item['company_id']}` |")
    return "\n".join(lines)


def _render(payload: dict[str, Any], scope: str) -> str:
    data = payload.get("data", {})
    summary = data.get("summary", {})
    window = data.get("window", {})
    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))} 배당", ""]
    lines.append(f"- company_id: `{data.get('company_id', '')}`")
    lines.append(f"- status: `{payload.get('status', '')}`")
    if window:
        lines.append(f"- 조사 구간: `{window.get('start_date', '')}` ~ `{window.get('end_date', '')}`")
    lines.append("")
    if payload.get("warnings"):
        lines.append("## 유의사항")
        for warning in payload["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")

    if summary:
        lines.append("## 연간 요약")
        lines.append(f"- 연간 DPS(보통주): {summary.get('cash_dps', 0):,}원")
        if summary.get("cash_dps_preferred"):
            lines.append(f"- 연간 DPS(우선주): {summary.get('cash_dps_preferred', 0):,}원")
        lines.append(f"- 배당총액: {summary.get('total_amount_mil', 0):,}백만원")
        if summary.get("payout_ratio_dart") is not None:
            lines.append(f"- 배당성향: {summary.get('payout_ratio_dart')}%")
        if summary.get("yield_dart") is not None:
            lines.append(f"- 시가배당률: {summary.get('yield_dart')}%")

    if scope in {"summary", "detail"}:
        lines.extend(["", "## 최근 배당결정", "| 공시일 | 구분 | DPS(보통) | 기준일 | rcept_no |", "|--------|------|-----------|--------|----------|"])
        for item in data.get("latest_decisions", [])[:10]:
            lines.append(
                f"| {item.get('rcept_dt', '')} | {item.get('dividend_type', '-') or '-'} | {item.get('dps_common', 0):,}원 | "
                f"{item.get('record_date', '-') or '-'} | `{item.get('rcept_no', '')}` |"
            )

    if scope in {"summary", "policy_signals"}:
        policy = data.get("policy_signals", {})
        lines.extend([
            "",
            "## 정책 신호",
            f"- 추세: {policy.get('trend', '-')}",
            f"- 분기/중간배당 패턴: {'예' if policy.get('has_quarterly_pattern') else '아니오'}",
            f"- 특별배당 이력: {'예' if policy.get('has_special_dividend') else '아니오'}",
            f"- 최근 DPS 변화율: {str(policy.get('latest_change_pct')) + '%' if policy.get('latest_change_pct') is not None else '-'}",
        ])

    if scope == "history":
        lines.extend(["", "## 최근 연도 추이", "| 연도 | 연간 DPS | 공시 수 | 배당성향 | 수익률 | 패턴 |", "|------|----------|--------|----------|--------|------|"])
        for item in data.get("history", []):
            payout = f"{item['payout_ratio']}%" if item.get("payout_ratio") is not None else "-"
            yld = f"{item['yield_pct']}%" if item.get("yield_pct") is not None else "-"
            lines.append(f"| {item['year']} | {item['annual_dps']:,}원 | {item['decision_count']} | {payout} | {yld} | {item['pattern']} |")

    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def dividend(
        company: str,
        scope: str = "summary",
        year: int = 0,
        years: int = 3,
        start_date: str = "",
        end_date: str = "",
        format: str = "md",
    ) -> str:
        """desc: 실지급·확정된 배당 **사실** 탭. DPS, 총액, 배당성향, 시가배당률, 추이. 미래 정책·약속은 포함 X.
        when: 이번 기 실제 지급된 배당 또는 결정 공시로 확정된 지급 수치를 확인할 때. 미래 환원 정책/약속은 `value_up`에서 교차 확인.
        rule: source of truth 2단 — (1) 사업보고서 `alotMatter` (완료 사업연도 공식값) (2) `현금ㆍ현물배당결정` 공시 합산 (alotMatter가 비거나 cash_dps=0일 때 fallback). summary의 `source` 필드로 출처 표기. **정책 예측·미래 약속 추가 금지**.
        scope: `summary`(기본) / `detail`(요약+최근 결정 10건) / `history`(최근 N년 추이) / `policy_signals`(분기배당·특별배당 등 패턴).
        ref: value_up (주주환원 정책·약속), company, ownership_structure, evidence
        """
        payload = await build_dividend_payload(
            company,
            scope=scope,
            year=year or None,
            years=years,
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
