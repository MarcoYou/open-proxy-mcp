"""dividend public tool."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.tools._shared import company_id_line
from open_proxy_mcp.services.dividend import build_dividend_payload


# 추세 enum → 한글. producer 는 services/dividend.py `_policy_signals()` 하나뿐이다.
# 260729 커밋(ec98f31)이 조회(`_TREND_KO.get(...)`)만 넣고 사전을 빠뜨려 md 경로 전체가
# NameError 로 죽었다 — 렌더러를 한 번도 실행하지 않는 테스트만 있어 전부 통과했다.
_TREND_KO = {
    "increasing": "증가",
    "decreasing": "감소",
    "stable": "유지",
    # 「없음」이 아니라 「판단하지 않았다」 — 비교할 확정 연도가 없어 계산 자체를 못 한 상태다
    "insufficient_data": "판단 불가 (확정된 배당 이력 없음)",
}


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


def _won(n) -> str:
    """금액 → '환산 (raw원)' 병기. 환산은 절삭이 있어 정밀 raw를 괄호로 같이 노출.
    1억 미만은 절삭이 없어 raw만. treasury·dividend·order_contracts·proxy_advise 공통 정책."""
    if not n:
        return "-"
    raw = f"{n:,}원"
    if n >= 1_0000_0000_0000:  # 1조
        return f"{n/1_0000_0000_0000:.2f}조원 ({raw})"
    if n >= 1_0000_0000:  # 1억
        return f"{n/1_0000_0000:,.0f}억원 ({raw})"
    return raw


def _fiscal_meta_line(summary: dict[str, Any], data: dict[str, Any]) -> str:
    """비12월 결산일 때만 사업연도 구간·결산월·FY 라벨 기준을 한 줄로 붙인다.
    12월 결산은 FY와 달력연도가 같아 군더더기다. provisional_earnings 의 표기를 따른다."""
    end_month = summary.get("fiscal_year_end_month") or data.get("fiscal_year_end_month")
    if not end_month or end_month == 12:
        return ""
    start, end = summary.get("period_start"), summary.get("period_end")
    span = f"사업연도 {start}~{end} · " if start and end else ""
    return f"_{span}{end_month}월 결산 · {data.get('fiscal_year_basis', '')}_"


def _render(payload: dict[str, Any], scope: str) -> str:
    data = payload.get("data", {})
    summary = data.get("summary", {})
    window = data.get("window", {})
    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))} 배당", ""]
    _cid = company_id_line(data)
    if _cid:
        lines.append(_cid)
    if window:
        lines.append(f"- 조사 구간: `{window.get('start_date', '')}` ~ `{window.get('end_date', '')}`")
    lines.append("")
    if payload.get("warnings"):
        lines.append("## 유의사항")
        for warning in payload["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")

    if summary:
        # 몇 년치인지 없으면 리포트에 인용할 때 위험하다(260728 QA 지적)
        _fy = summary.get("fiscal_year") or summary.get("year") or data.get("year")
        lines.append(f"## 연간 요약" + (f" (FY{_fy})" if _fy else ""))
        # 비12월 결산은 같은 FY 라벨이 회사 IR 문서와 다른 12개월을 가리킨다 —
        # 구간·결산월·라벨 기준을 붙여야 어느 해 이야기인지 확정된다 (U 지적 B-6).
        _meta = _fiscal_meta_line(summary, data)
        if _meta:
            lines.append(_meta)
        lines.append(f"- 연간 DPS(보통주): {summary.get('cash_dps', 0):,}원")
        if summary.get("cash_dps_preferred"):
            lines.append(f"- 연간 DPS(우선주): {summary.get('cash_dps_preferred', 0):,}원")
        lines.append(f"- 배당총액: {_won(summary.get('total_amount_mil', 0) * 1_000_000)}")
        if summary.get("payout_ratio_dart") is not None:
            lines.append(f"- 배당성향: {summary.get('payout_ratio_dart')}% _(공시 원문 `(연결)현금배당성향`. 연결 기준이며 우리가 계산한 값이 아니다)_")
        if summary.get("yield_dart") is not None:
            lines.append(f"- 시가배당률: {summary.get('yield_dart')}% (결의 당시 공시값)")
        if summary.get("yield_current_pct") is not None:
            lines.append(
                f"- 현재가 기준 배당수익률: {summary.get('yield_current_pct')}% "
                f"(DPS {summary.get('cash_dps', 0):,}원 ÷ 종가 {summary.get('yield_current_price_krw', 0):,}원, "
                f"{summary.get('yield_current_price_date', '-')} 기준)")
        # 신호 메타 — 선배당-후결의, 감액배당.
        if summary.get("pre_dividend_post_resolution"):
            lines.append("- 선배당-후결의 (2024 신법): 채택 (배당기준일결정 별도 공시 확인)")
        elif "pre_dividend_post_resolution" in summary:
            lines.append("- 선배당-후결의 (2024 신법): 미채택 추정")
        if summary.get("capital_reserve_reduction"):
            lines.append("- 감액배당 cross-link: 자본준비금 감소 안건 주총 상정 (이익잉여금 전입 → 배당 재원)")

    if scope in {"summary", "detail"}:
        lines.extend(["", "## 최근 배당결정", "| 공시일 | 구분 | DPS(보통) | 기준일 | 공시번호 |", "|--------|------|-----------|--------|----------|"])
        for item in data.get("latest_decisions", [])[:10]:
            lines.append(
                f"| {item.get('rcept_dt', '')} | {item.get('dividend_type', '-') or '-'} | {item.get('dps_common', 0):,}원 | "
                f"{item.get('record_date', '-') or '-'} | `{item.get('rcept_no', '')}` |"
            )

    if scope == "summary":
        policy = data.get("policy_signals", {})
        lines.extend([
            "",
            "## 정책 신호",
            f"- 추세: {_TREND_KO.get(policy.get('trend'), policy.get('trend') or '-')}",
            f"- 분기/중간배당 패턴: {'예' if policy.get('has_quarterly_pattern') else '아니오'}",
            f"- 특별배당 이력: {'예' if policy.get('has_special_dividend') else '아니오'}",
            f"- 최근 DPS 변화율: {str(policy.get('latest_change_pct')) + '%' if policy.get('latest_change_pct') is not None else '-'}",
        ])

    if scope == "history":
        _em = data.get("fiscal_year_end_month")
        _span_col = bool(_em) and _em != 12
        if _span_col:
            lines.extend(["", "## 최근 연도 추이", f"_{_em}월 결산 · {data.get('fiscal_year_basis', '')}_",
                          "| FY | 결산기간 | 연간 DPS | 공시 수 | 배당성향 | 수익률 | 패턴 |",
                          "|----|----------|----------|--------|----------|--------|------|"])
        else:
            lines.extend(["", "## 최근 연도 추이", "| 연도 | 연간 DPS | 공시 수 | 배당성향 | 수익률 | 패턴 |", "|------|----------|--------|----------|--------|------|"])
        for item in data.get("history", []):
            payout = f"{item['payout_ratio']}%" if item.get("payout_ratio") is not None else "-"
            yld = f"{item['yield_pct']}%" if item.get("yield_pct") is not None else "-"
            # 미결의 연도는 0원을 지급액처럼 보이게 두지 않는다.
            dps = "-" if item.get("pattern", "").startswith("미결의") else f"{item['annual_dps']:,}원"
            if _span_col:
                span = f"{item.get('period_start') or '?'}~{item.get('period_end') or '?'}"
                lines.append(f"| {item['year']} | {span} | {dps} | {item['decision_count']} | {payout} | {yld} | {item['pattern']} |")
            else:
                lines.append(f"| {item['year']} | {dps} | {item['decision_count']} | {payout} | {yld} | {item['pattern']} |")
        # 최신연도 분기별 — 정기보고서 누적차분(권위). 결정공시 버킷팅 오귀속·중복 없음, 무배당 분기 0 포함.
        qf = data.get("quarterly_full") or []
        if qf:
            lines.extend(["", "## 최신연도 분기별 (정기보고서 누적차분)", "| 분기 | 보통주 DPS | 우선주 DPS | 배당총액 |", "|------|------------|------------|----------|"])
            for x in qf:
                pref = f"{x['dps_preferred']:,}원" if x.get("dps_preferred") else "-"
                lines.append(f"| {x['quarter']} | {x['dps_common']:,}원 | {pref} | {_won(x['total_mil'] * 1_000_000)} |")
            lines.append("> 분기/반기/사업보고서 누적값을 차분 — 결정공시 귀속 추측이 아니라 권위 출처. 무배당 분기는 0.")
        # 결정공시별 breakdown — 기준일·rcept_no 추적용 (정정 이력 포함).
        qb = data.get("quarterly_breakdown") or []
        if qb:
            lines.extend(["", "## 분기별 결정공시 (공시별·추적용)", "| 연도 | 분기 | 보통주 DPS | 우선주 DPS | 기준일 | 공시번호 |", "|------|------|------------|------------|--------|------------------|"])
            for r in qb:
                amend = " [정정]" if r.get("is_amendment") else ""
                supersed = " (대체됨)" if r.get("is_superseded") else ""
                lines.append(f"| {r['year']} | {r['quarter']}{amend}{supersed} | {r['dps_common_krw']:,}원 | {r['dps_preferred_krw']:,}원 | {r.get('record_date','-')} | `{r.get('rcept_no','-')}` |")
            lines.append("")
            lines.append("> 최신연도 정확값은 위 '누적차분' 표 참조. 이 표는 공시 추적용(기준일·공시번호·정정 이력).")

    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def dividend_disclosure(
        company: str,
        scope: str = "summary",
        year: int = 0,
        years: int = 3,
        start_date: str = "",
        end_date: str = "",
        format: str = "md",
    ) -> str:
        """desc: 실지급·확정된 배당 **사실**. DPS, 총액, 배당성향, 시가배당률(결의 당시)+**현재가 기준 배당수익률**(최신 종가, krx_weekly), 분기별 추이. 미래 정책·약속 X.
        when: 실제 지급된 배당 확인. 분기배당 회사는 `history`로 분기별 breakdown. 미래 정책/약속은 `value_up`.
        rule: source 2단 — (1) 사업보고서 alotMatter(공식값) (2) 현금ㆍ현물배당결정 합산(alotMatter 빈 경우 fallback). 결산배당은 record_date 기준 fiscal year bucket (선배당-후결의 신법). 정정공시 is_superseded 표시. 미래 약속 추가 금지.
        scope: `summary` 선배당-후결의+감액배당 메타 / `detail` 요약+최근 결정 50건 / `history` N년 추이+분기 breakdown+policy_signals
        ref: value_up, treasury_share, shareholder_meeting_notice, company, ownership_structure, evidence
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
