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

    if scope == "cash_shareholder_return":
        csr = data.get("cash_shareholder_return", {}) or {}
        lines.extend(["", f"## CSR — Cash Shareholder Return ({csr.get('year', '-')})"])
        lines.append(f"- 정의: {csr.get('definition', '')}")
        pct = csr.get("csr_pct")
        pct_str = f"{pct}%" if pct is not None else "계산 불가"
        lines.append(f"- **CSR**: **{pct_str}**  (= (배당총액 + 자사주 매입) / 지배주주 당기순이익 × 100)")
        lines.append(f"- 배당총액: {csr.get('dividend_total_krw', 0):,}원")
        lines.append(f"- 자사주 매입(acquire) 금액: {csr.get('buyback_total_krw', 0):,}원")
        lines.append(f"- 현금 환원 합계: {csr.get('cash_return_total_krw', 0):,}원")
        lines.append(f"- 지배주주 당기순이익(연결): {csr.get('net_income_krw', 0):,}원")
        lines.append(f"- 비율 상태: `{csr.get('ratio_status', '-')}`")
        comp = csr.get("components", {}) or {}
        if any(v for v in comp.values() if isinstance(v, (int, float))):
            lines.extend([
                "",
                "### 환원 컴포넌트",
                f"- 정기 배당: {comp.get('regular_dividend_krw', 0):,}원",
                f"- 분기/중간 배당: {comp.get('quarterly_dividend_krw', 0):,}원",
                f"- 특별 배당: {comp.get('special_dividend_krw', 0):,}원",
                f"- 자사주 매입: {comp.get('buyback_value_krw', 0):,}원",
            ])
        meta = data.get("meta_signals", {}) or {}
        if meta:
            lines.extend([
                "",
                "### 정책 시그널",
                f"- 선배당-후결의 (2024 신법): {'채택' if meta.get('pre_dividend_post_resolution') else '미채택'}",
                f"- 감액배당 (자본준비금 감소): {'주총 상정' if meta.get('capital_reserve_reduction') else '미해당'}",
            ])
        rows = csr.get("acquisition_rows") or []
        if rows:
            lines.extend([
                "",
                "### 자사주 취득결정 (acquire — 매입 시점)",
                "| 공시일 | 주식수 | 금액(원) | 방법 | 목적(소각?) | rcept_no |",
                "|--------|--------|---------|------|-------------|----------|",
            ])
            for r in rows[:5]:
                purpose_short = (r.get("purpose") or "-")[:30]
                for_canc = "예" if r.get("for_cancelation") else "-"
                lines.append(
                    f"| {r.get('rcept_dt', '-')} | "
                    f"{r.get('shares', 0):,} | {r.get('amount_krw', 0):,} | "
                    f"{r.get('method', '-') or '-'} | {purpose_short} ({for_canc}) | `{r.get('rcept_no', '')}` |"
                )

    if scope == "total_shareholder_return":
        tsr = data.get("total_shareholder_return", {}) or {}
        lines.extend(["", f"## TSR — Total Shareholder Return ({tsr.get('year', '-')})"])
        lines.append(f"- 정의: {tsr.get('definition', '')}")
        pct = tsr.get("tsr_pct")
        pct_str = f"{pct}%" if pct is not None else "계산 불가"
        lines.append(f"- **TSR**: **{pct_str}**  (= (P_end - P_start + DPS) / P_start × 100)")
        comp = tsr.get("components", {}) or {}
        lines.append(f"- 연초 종가 (P_start): {comp.get('price_start_krw', 0):,}원")
        lines.append(f"- 연말 종가 (P_end): {comp.get('price_end_krw', 0):,}원")
        lines.append(f"- 1주당 배당금 합계 (DPS): {comp.get('dps_total_krw', 0):,}원")
        pchg = comp.get("price_change_pct")
        dyld = comp.get("dividend_yield_pct")
        lines.append(f"- 주가 변동률: {pchg}%" if pchg is not None else "- 주가 변동률: -")
        lines.append(f"- 배당 수익률 (P_start 기준): {dyld}%" if dyld is not None else "- 배당 수익률: -")
        lines.append(f"- 비율 상태: `{tsr.get('ratio_status', '-')}`")
        sources = tsr.get("sources", {}) or {}
        if sources:
            lines.append(f"- 데이터 소스: 시세=`{sources.get('price', '-')}`, DPS=`{sources.get('dps', '-')}`")

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
        """desc: 실지급·확정된 배당 **사실** 탭. DPS, 총액, 배당성향, 시가배당률, 추이, CSR(한국식 배당+자사주 매입) + TSR(글로벌 주가+배당). 미래 정책·약속 X.
        when: 이번 기 실제 지급된 배당·확정 지급 수치 확인. 한국식 환원율(배당+자사주 매입/지배순이익)은 scope=`cash_shareholder_return`. 글로벌 정의 1주 수익률(주가변동+배당)은 scope=`total_shareholder_return`. 미래 정책/약속은 `value_up`.
        rule: source of truth 2단 — (1) 사업보고서 `alotMatter` (완료 사업연도 공식값) (2) `현금ㆍ현물배당결정` 공시 합산 (alotMatter가 비거나 cash_dps=0일 때 fallback). CSR 분자=배당+자사주 **매입(acquire)** (소각=retire 아님), 분모=연결 지배주주 당기순이익. TSR 분모=연초 P_start. **정책 예측·미래 약속 추가 금지**.
        scope: `summary`(기본, 선배당-후결의 + 감액배당 메타 포함) / `detail`(요약+최근 결정 10건) / `history`(최근 N년 추이) / `policy_signals`(분기배당·특별배당 등 패턴) / `cash_shareholder_return`(한국식 CSR — 배당+자사주 매입) / `total_shareholder_return`(글로벌 TSR — 주가변동+배당).
        ref: value_up (주주환원 정책·약속), treasury_share (자사주 매입/소각 결정), shareholder_meeting (감액배당 안건), company, ownership_structure, evidence
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
