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
        # 신호 메타 — 선배당-후결의, 감액배당.
        if summary.get("pre_dividend_post_resolution"):
            lines.append("- 선배당-후결의 (2024 신법): 채택 (배당기준일결정 별도 공시 확인)")
        elif "pre_dividend_post_resolution" in summary:
            lines.append("- 선배당-후결의 (2024 신법): 미채택 추정")
        if summary.get("capital_reserve_reduction"):
            lines.append("- 감액배당 cross-link: 자본준비금 감소 안건 주총 상정 (이익잉여금 전입 → 배당 재원)")

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
        # 분기별 breakdown — 분기배당 회사 (삼성전자 등) 분기 디테일 검증
        qb = data.get("quarterly_breakdown") or []
        if qb:
            lines.extend(["", "## 분기별 / 결산 breakdown", "| 연도 | 분기 | 보통주 DPS | 우선주 DPS | 시가배당률 | 기준일 | 공시 (rcept_no) |", "|------|------|------------|------------|------------|--------|------------------|"])
            for r in qb:
                amend = " [정정]" if r.get("is_amendment") else ""
                supersed = " ~~superseded~~" if r.get("is_superseded") else ""
                yc = r.get("yield_common_pct")
                yc_str = f"{yc}%" if yc is not None else "-"
                lines.append(f"| {r['year']} | {r['quarter']}{amend}{supersed} | {r['dps_common_krw']:,}원 | {r['dps_preferred_krw']:,}원 | {yc_str} | {r.get('record_date','-')} | `{r.get('rcept_no','-')}` |")
            lines.append("")
            lines.append("> 정정공시는 [정정] 표기, superseded는 같은 (연도/분기/기준일) 내 더 최신 결정으로 대체된 row.")

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
        """desc: 실지급·확정된 배당 **사실** 탭. DPS, 총액, 배당성향, 시가배당률, 분기별 추이. 미래 정책·약속 X.
        when: 이번 기 실제 지급된 배당·확정 지급 수치 확인. 분기배당 회사는 scope=`history`로 분기별 breakdown 확인. 미래 정책/약속·환원율(CSR)은 `value_up`.
        rule: source of truth 2단 — (1) 사업보고서 `alotMatter` (완료 사업연도 공식값) (2) `현금ㆍ현물배당결정` 공시 합산 (alotMatter가 비거나 cash_dps=0일 때 fallback). 결산배당은 record_date 기준 fiscal year bucket (선배당-후결의 신법 cover). 정정공시 `is_superseded` 표시. **정책 예측·미래 약속 추가 금지**.
        scope: `summary`(기본, 선배당-후결의 + 감액배당 메타 포함) / `detail`(요약+최근 결정 50건) / `history`(최근 N년 추이 + 분기별 breakdown + policy_signals).
        ref: value_up (주주환원 정책·약속·CSR), treasury_share (자사주 매입/소각 결정), shareholder_meeting (감액배당 안건), company, ownership_structure, evidence
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
