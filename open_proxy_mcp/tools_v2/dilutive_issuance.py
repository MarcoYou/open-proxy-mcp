"""v2 dilutive_issuance public tool."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.dilutive_issuance import build_dilutive_issuance_payload


def _render_error(payload: dict[str, Any]) -> str:
    lines = [f"# dilutive_issuance: {payload.get('subject', '')}", ""]
    for warning in payload.get("warnings", []):
        lines.append(f"- {warning}")
    return "\n".join(lines)


def _render_ambiguous(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    lines = [
        f"# dilutive_issuance: {data.get('query', '')}",
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


def _render_rights_card(row: dict[str, Any]) -> list[str]:
    fp = row.get("fund_purpose", {})
    lu = row.get("lock_up", {})
    return [
        f"### 유상증자 — {row.get('rcept_dt', '')} ({_link(row.get('rcept_no', ''))})",
        f"- 이사회결의일: {row.get('board_decision_date', '-') or '-'}",
        f"- 배정방식: **{row.get('issuance_method', '-') or '-'}**",
        f"- 신주(보통): {row.get('new_shares_common', 0):,}주 / 기존: {row.get('existing_shares_common', 0):,}주",
        f"- 희석률 근사: **{row.get('dilution_pct_approx', 0):.2f}%** (기존대비 신주 비율)",
        f"- 액면가: {row.get('face_value_per_share', '-') or '-'}원",
        f"- 자금 목적: 시설 {fp.get('facility', '-') or '-'} / 운영 {fp.get('operating', '-') or '-'} / 채무상환 {fp.get('debt_repayment', '-') or '-'} / 기타법인주식 {fp.get('other_corp_share_acq', '-') or '-'}",
        f"- 보호예수: {lu.get('applicable', '-') or '-'} ({lu.get('begin_date', '-') or '-'} ~ {lu.get('end_date', '-') or '-'})",
        "",
    ]


def _render_cb_card(row: dict[str, Any]) -> list[str]:
    cv = row.get("conversion", {})
    fp = row.get("fund_purpose", {})
    return [
        f"### 전환사채 {row.get('bond_series', '')}회 — {row.get('rcept_dt', '')} ({_link(row.get('rcept_no', ''))})",
        f"- 이사회결의일: {row.get('board_decision_date', '-') or '-'}",
        f"- 종류: {row.get('bond_kind', '-') or '-'}",
        f"- 발행총액: **{row.get('total_issue_amount', '-') or '-'}원** / 방식: {row.get('issuance_method', '-') or '-'}",
        f"- 금리: 표면 {row.get('coupon_rate', '-') or '-'}% / YTM {row.get('yield_to_maturity', '-') or '-'}% / 만기 {row.get('maturity_date', '-') or '-'}",
        f"- 전환조건: 전환가 **{cv.get('price', '-') or '-'}원** / 전환비율 {cv.get('rate', '-') or '-'}% / 대상 {cv.get('target_stock_kind', '-') or '-'}",
        f"- 전환 시 발행주식: {cv.get('shares_if_converted', '-') or '-'}주 (**잠재 희석 {cv.get('pct_of_total_shares', '-') or '-'}%**)",
        f"- 전환청구기간: {cv.get('request_period_begin', '-') or '-'} ~ {cv.get('request_period_end', '-') or '-'}",
        f"- Refixing 하한: {cv.get('refixing_floor', '-') or '-'}",
        f"- 납입일: {row.get('payment_date', '-') or '-'} / 보증인: {row.get('guarantor', '-') or '-'} / 담보: {row.get('collateral', '-') or '-'}",
        f"- 자금 목적: 운영 {fp.get('operating', '-') or '-'} / 채무상환 {fp.get('debt_repayment', '-') or '-'} / 기타법인주식 {fp.get('other_corp_share_acq', '-') or '-'}",
        "",
    ]


def _render_bw_card(row: dict[str, Any]) -> list[str]:
    w = row.get("warrant", {})
    fp = row.get("fund_purpose", {})
    return [
        f"### 신주인수권부사채 {row.get('bond_series', '')}회 — {row.get('rcept_dt', '')} ({_link(row.get('rcept_no', ''))})",
        f"- 이사회결의일: {row.get('board_decision_date', '-') or '-'}",
        f"- 종류: {row.get('bond_kind', '-') or '-'}",
        f"- 발행총액: **{row.get('total_issue_amount', '-') or '-'}원** / 방식: {row.get('issuance_method', '-') or '-'}",
        f"- 금리: 표면 {row.get('coupon_rate', '-') or '-'}% / YTM {row.get('yield_to_maturity', '-') or '-'}% / 만기 {row.get('maturity_date', '-') or '-'}",
        f"- 워런트: 행사가 **{w.get('exercise_price', '-') or '-'}원** / 비율 {w.get('exercise_rate', '-') or '-'}%",
        f"- 분리/비분리: {w.get('detachable', '-') or '-'} / 납입방법: {w.get('payment_method', '-') or '-'}",
        f"- 신주 대상: {w.get('new_stock_kind', '-') or '-'} {w.get('new_stock_count', '-') or '-'}주 (**잠재 희석 {w.get('pct_of_total_shares', '-') or '-'}%**)",
        f"- 행사기간: {w.get('exercise_period_begin', '-') or '-'} ~ {w.get('exercise_period_end', '-') or '-'}",
        f"- 납입일: {row.get('payment_date', '-') or '-'} / 보증인: {row.get('guarantor', '-') or '-'}",
        f"- 자금 목적: 운영 {fp.get('operating', '-') or '-'} / 기타법인주식 {fp.get('other_corp_share_acq', '-') or '-'}",
        "",
    ]


def _render_capital_reduction_card(row: dict[str, Any]) -> list[str]:
    sched = row.get("schedule", {})
    return [
        f"### 감자 — {row.get('rcept_dt', '')} ({_link(row.get('rcept_no', ''))})",
        f"- 이사회결의일: {row.get('board_decision_date', '-') or '-'}",
        f"- 감자비율: **{row.get('reduction_ratio_common', '-') or '-'}%** (보통주)",
        f"- 감소 주식수: {row.get('shares_reduced_common', '-') or '-'}주",
        f"- 자본금: {row.get('capital_before', '-') or '-'}원 → {row.get('capital_after', '-') or '-'}원",
        f"- 발행주식: {row.get('outstanding_before_common', '-') or '-'}주 → {row.get('outstanding_after_common', '-') or '-'}주",
        f"- 감자 방법: {row.get('method', '-') or '-'}",
        f"- 감자 사유: {row.get('reason', '-') or '-'}",
        f"- 기준일: {row.get('reduction_standard_date', '-') or '-'}",
        f"- 일정: 주총 {sched.get('shareholders_meeting', '-') or '-'} / 구주권 제출 {sched.get('old_share_submission_begin', '-') or '-'}~{sched.get('old_share_submission_end', '-') or '-'} / 매매정지 {sched.get('trading_suspension_begin', '-') or '-'}~{sched.get('trading_suspension_end', '-') or '-'} / 신주 상장 {sched.get('new_share_listing', '-') or '-'}",
        "",
    ]


def _render(payload: dict[str, Any], scope: str) -> str:
    data = payload.get("data", {})
    window = data.get("window", {})
    counts = data.get("event_count", {})
    usage = data.get("usage", {})
    lines = [
        f"# {data.get('canonical_name', payload.get('subject', ''))} 희석성 증권 발행 (dilutive_issuance)",
        "",
        f"- company_id: `{data.get('company_id', '')}`",
        f"- scope: `{scope}`",
        f"- 조사 구간: `{window.get('start_date', '')}` ~ `{window.get('end_date', '')}`",
        f"- 사건 수: 유상증자 {counts.get('rights_offering', 0)} / CB {counts.get('convertible_bond', 0)} / BW {counts.get('warrant_bond', 0)} / 감자 {counts.get('capital_reduction', 0)}",
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

    if scope == "summary":
        timeline = data.get("events_timeline", [])
        if not timeline:
            if data.get("no_filing"):
                lines.append("## 공시 없음")
                lines.append("- 조사 구간 내 희석성 증권 발행 사건 없음 (정상 NO_FILING).")
            else:
                lines.append("조사 구간 내 희석성 증권 발행 사건 없음.")
            return "\n".join(lines)
        lines.extend([
            "## 사건 타임라인",
            "| 날짜 | 종류 | 핵심 지표 | 원문 |",
            "|------|------|----------|------|",
        ])
        for ev in timeline:
            lines.append(
                f"| {ev.get('rcept_dt', '')} | {ev.get('event_label', '-')} | {ev.get('headline_metric', '-')} | {_link(ev.get('rcept_no', ''))} |"
            )

    if scope == "rights_offering":
        events = data.get("rights_offering_events", [])
        if not events:
            lines.append("유상증자 결정 없음.")
        else:
            lines.append("## 유상증자 결정 상세")
            for row in events:
                lines.extend(_render_rights_card(row))

    if scope == "convertible_bond":
        events = data.get("convertible_bond_events", [])
        if not events:
            lines.append("전환사채 발행결정 없음.")
        else:
            lines.append("## 전환사채 발행결정 상세")
            for row in events:
                lines.extend(_render_cb_card(row))

    if scope == "warrant_bond":
        events = data.get("warrant_bond_events", [])
        if not events:
            lines.append("신주인수권부사채 발행결정 없음.")
        else:
            lines.append("## 신주인수권부사채 발행결정 상세")
            for row in events:
                lines.extend(_render_bw_card(row))

    if scope == "capital_reduction":
        events = data.get("capital_reduction_events", [])
        if not events:
            lines.append("감자결정 없음.")
        else:
            lines.append("## 감자결정 상세")
            for row in events:
                lines.extend(_render_capital_reduction_card(row))

    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def dilutive_issuance(
        company: str,
        scope: str = "summary",
        start_date: str = "",
        end_date: str = "",
        format: str = "md",
    ) -> str:
        """desc: 희석성 증권 발행 4종(유상증자/전환사채/신주인수권부사채/감자) 결정을 통합 제공. 발행조건, 잠재 희석률, 3자배정 여부, 풋옵션, refixing 조항 같은 분석 핵심 수치 정형화.
        when: 행동주의 대응 자금조달, 경영권 방어용 우호 지분 확보, CB·BW 잠재 희석 평가, 유상증자 3자배정 대상 식별 등. ownership_structure와 교차 확인 권장.
        rule: DART 주요사항보고서(DS005) 4개 구조화 API — `piicDecsn`(유상증자), `cvbdIsDecsn`(CB), `bdwtIsDecsn`(BW), `crDecsn`(감자). 모두 병렬 호출. 기본 lookback 24개월. dilution_pct_approx는 유상증자 신주/기존 단순 비율(근사), CB/BW의 pct_of_total_shares는 원본 공식 비율.
        scope: `summary`(기본, 4종 통합 timeline) / `rights_offering`(유상증자 카드) / `convertible_bond`(CB 카드) / `warrant_bond`(BW 카드) / `capital_reduction`(감자 카드).
        ref: ownership_structure (3자배정 지분 변동), corporate_restructuring (M&A 맥락), proxy_contest (분쟁 자금조달), evidence
        """
        payload = await build_dilutive_issuance_payload(
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
