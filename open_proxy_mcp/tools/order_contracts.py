"""order_contracts public tool — 수주(단일판매·공급계약) 추적."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.order_contracts import build_order_contracts_payload


def _render_error(payload: dict[str, Any]) -> str:
    lines = [f"# order_contracts: {payload.get('subject', '')}", ""]
    for warning in payload.get("warnings", []):
        lines.append(f"- {warning}")
    return "\n".join(lines)


def _render_ambiguous(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    lines = [
        f"# order_contracts: {data.get('query', '')}",
        "",
        "회사 식별이 애매해 자동 선택하지 않았다.",
        "",
        "| 회사명 | ticker | corp_code |",
        "|------|--------|-----------|",
    ]
    for item in data.get("candidates", []):
        lines.append(f"| {item.get('corp_name', '')} | `{item.get('ticker', '')}` | `{item.get('corp_code', '')}` |")
    return "\n".join(lines)


def _won(n: int | None) -> str:
    """금액 → '환산 (raw원)' 병기. 환산 절삭 보완 위해 정밀 raw 괄호 노출. 1억 미만은 raw만."""
    if not n:
        return "-"
    raw = f"{n:,}원"
    if n >= 1_0000_0000_0000:  # 1조
        return f"{n/1_0000_0000_0000:.2f}조원 ({raw})"
    if n >= 1_0000_0000:  # 1억
        return f"{n/1_0000_0000:,.0f}억원 ({raw})"
    return raw


def _pct(v) -> str:
    """매출대비% — None이면 'None%' 노출 방지 ('-'). 0%는 그대로."""
    return f"{v}" if v is not None else "-"


def _render(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    s = data.get("signal_summary", {})
    orders = data.get("orders", []) or []
    win = data.get("window", {})
    lines = [
        f"# {data.get('canonical_name', '')} 수주 현황",
        "",
        f"- company_id: `{data.get('company_id', '')}` / 구간: `{win.get('start_date', '')}`~`{win.get('end_date', '')}`",
        "",
    ]
    if payload.get("warnings"):
        lines.append("## 유의사항")
        lines += [f"- {w}" for w in payload["warnings"]]
        lines.append("")

    lines += [
        "## 수주 시그널 요약",
        f"- 유효 계약 **{s.get('order_count', 0)}건** (외부 {s.get('external_count', 0)} / 내부·계열 {s.get('internal_count', 0)})",
        f"- 외부 수주 총액 **{_won(s.get('external_total_amount_won'))}**",
        f"- 최근 매출액 대비 — 단일 최대 **{_pct(s.get('max_revenue_ratio_pct'))}%** / 합계 {_pct(s.get('sum_revenue_ratio_pct'))}%",
        f"- 기재정정 {s.get('correction_count', 0)}건 (변경계약 — 아래 diff)",
    ]
    if s.get("terminated_count"):
        lines.append(
            f"- ⚠️ 계약 해지 **{s.get('terminated_count')}건** {_won(s.get('terminated_total_amount_won'))}"
            + (f" (해지 매출대비 최대 {s.get('max_terminated_revenue_ratio_pct')}%)" if s.get("max_terminated_revenue_ratio_pct") else "")
        )
    lines.append("")

    lines += [
        "## 계약별 (최신순, 정정 반영 후)",
        "| 공시일 | 계약명 | 상대방 | 계약금액 | 매출대비 | 외부 | 정정 |",
        "|--------|--------|--------|---------|---------|------|------|",
    ]
    for o in orders[:25]:
        corr = ""
        cd = o.get("correction_diff") or {}
        if cd.get("amount_change_pct") is not None:
            sign = "↑" if cd["amount_change_pct"] > 0 else "↓"
            corr = f"{sign}{abs(cd['amount_change_pct'])}%"
        elif o.get("correction_count"):
            corr = f"{o['correction_count']}회"
        rev_cell = f"{_pct(o.get('revenue_ratio_pct'))}%"
        if o.get("ratio_warning"):  # 공시값과 불일치해 계산값 채택 — 공시값 병기
            rev_cell += f" ⚠(공시 {o.get('revenue_ratio_disclosed_pct')}%)"
        lines.append(
            f"| {o.get('rcept_dt', '')} | {(o.get('contract_name') or '-')[:24]} | {(o.get('counterparty') or '-')[:14]} "
            f"| {_won(o.get('contract_amount_won'))} | {rev_cell} | {'O' if o.get('is_external') else '계열'} | {corr or '-'} |"
        )

    terminations = data.get("terminations", []) or []
    if terminations:
        lines += ["", f"## 계약 해지 {len(terminations)}건 (부정 시그널)",
                  "| 공시일 | 해지 계약명 | 상대방 | 해지금액 | 매출대비 |",
                  "|--------|-----------|--------|---------|---------|"]
        for t in terminations[:10]:
            lines.append(
                f"| {t.get('rcept_dt', '')} | {(t.get('contract_name') or '-')[:24]} | {(t.get('counterparty') or '-')[:14]} "
                f"| {_won(t.get('terminated_amount_won'))} | {_pct(t.get('revenue_ratio_pct'))}% |"
            )

    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def order_contracts(
        company: str,
        start_date: str = "",
        end_date: str = "",
        max_documents: int = 30,
        format: str = "md",
    ) -> str:
        """desc: 회사의 **수주**(단일판매·공급계약체결) 추적 — 계약금액·**매출액 대비%**·상대방·계약기간. 적자 디폴트인 코스닥 바이오/기술주에서 수주 = 미래 매출 가시성 시그널. 기재정정(변경계약) 자동 dedup + 증액/감액 diff.
        when: 얼마짜리 수주를 따냈나, 수주가 매출 대비 얼마나 큰가(적자기업 가시성), 최근 수주 모멘텀, 외부 수주 vs 계열 일감(공시 관계필드 기준), 계약 해지, 수주 증액/감액 변경. 지분 인수/매각(타법인주식)은 `corporate_deals`.
        rule: DART list.json I001 — 단일판매ㆍ공급계약체결/해지 (일반+자율공시 모두 I001, 자회사 변형 포함). 본문 파싱: 계약금액(단위 원/천원/백만원 환산)·최근매출액·매출액대비%·상대방·관계(외부/계열)·계약기간. dedup: (계약명+상대방) 그룹 + 정정본 정정전금액으로 원본↔정정 매칭(같은 키라도 금액 체인 불일치 시 별개). 기본 lookback 24개월.
        max_documents: 본문 파싱 상한 (기본 30).
        ref: corporate_deals (타법인주식 지분 인수/매각), financial_metrics (매출·수익성), evidence (원문 확인)
        """
        payload = await build_order_contracts_payload(
            company,
            start_date=start_date,
            end_date=end_date,
            max_documents=max(5, min(max_documents, 50)),
        )
        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") == "ambiguous":
            return _render_ambiguous(payload)
        if payload.get("status") == "error":
            return _render_error(payload)
        return _render(payload)
