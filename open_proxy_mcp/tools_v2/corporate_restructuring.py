"""v2 corporate_restructuring public tool."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.corporate_restructuring import build_corporate_restructuring_payload


def _render_error(payload: dict[str, Any]) -> str:
    lines = [f"# corporate_restructuring: {payload.get('subject', '')}", ""]
    for warning in payload.get("warnings", []):
        lines.append(f"- {warning}")
    return "\n".join(lines)


def _render_ambiguous(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    lines = [
        f"# corporate_restructuring: {data.get('query', '')}",
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


def _viewer(rcept_no: str) -> str:
    return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}" if rcept_no else ""


def _link(rcept_no: str) -> str:
    url = _viewer(rcept_no)
    return f"[{rcept_no}]({url})" if url else f"`{rcept_no}`"


def _render_merger_card(row: dict[str, Any]) -> list[str]:
    cp = row.get("counterparty", {})
    fin = cp.get("financial", {})
    lines = [
        f"### 합병 — {row.get('rcept_dt', '')} ({_link(row.get('rcept_no', ''))})",
        f"- 이사회결의일: {row.get('board_decision_date', '-') or '-'}",
        f"- 합병형태: {row.get('scale', '-') or '-'} / 방법: {row.get('method', '-') or '-'}",
        f"- 합병비율: `{row.get('ratio', '-') or '-'}`",
        f"- 신주(보통): {row.get('new_shares_common', '-') or '-'}",
        f"- 합병상대회사: {cp.get('name', '-') or '-'} (관계: {cp.get('relationship', '-') or '-'})",
        f"  - 사업: {cp.get('business', '-') or '-'}",
        f"  - 재무 (자산/부채/자본/매출/순이익): {fin.get('total_assets', '-') or '-'} / {fin.get('total_debt', '-') or '-'} / {fin.get('total_equity', '-') or '-'} / {fin.get('revenue', '-') or '-'} / {fin.get('net_income', '-') or '-'}",
        f"- 외부평가: {row.get('external_evaluator', '-') or '-'} (감사의견: {row.get('audit_opinion_on_counterparty', '-') or '-'})",
        f"- 주식매수청구권 가격: {row.get('appraisal_right_price', '-') or '-'}",
        f"- 풋옵션 약정: {row.get('put_option_applicable', '-') or '-'}",
        f"- 합병목적: {row.get('purpose', '-') or '-'}",
        "",
    ]
    return lines


def _render_split_card(row: dict[str, Any]) -> list[str]:
    sv = row.get("surviving_company", {})
    nw = row.get("new_company", {})
    label = row.get("event_label", "분할결정")
    lines = [
        f"### {label} — {row.get('rcept_dt', '')} ({_link(row.get('rcept_no', ''))})",
        f"- 이사회결의일: {row.get('board_decision_date', '-') or '-'}",
        f"- 분할형태: {row.get('split_form', '-') or '-'}",
        f"- 분할비율: `{row.get('ratio', '-') or '-'}`",
        f"- 분할대상사업: {row.get('transferred_business', '-') or '-'}",
        f"- 존속회사: {sv.get('name', '-') or '-'} (재상장 유지: {sv.get('will_remain_listed', '-') or '-'})",
        f"- 신설회사: {nw.get('name', '-') or '-'} (예상매출: {nw.get('projected_revenue', '-') or '-'}, 재상장: {nw.get('will_relist', '-') or '-'})",
        f"  - 사업: {nw.get('business', '-') or '-'}",
        f"- 주주총회 예정일: {row.get('shareholder_meeting_date', '-') or '-'}",
        f"- 분할기일: {row.get('split_date', '-') or '-'}",
        f"- 영향: {row.get('impact_on_ownership', '-') or '-'}",
        "",
    ]
    return lines


def _render_exchange_card(row: dict[str, Any]) -> list[str]:
    tg = row.get("target_company", {})
    fin = tg.get("financial", {})
    sched = row.get("schedule", {})
    lines = [
        f"### 주식교환·이전 — {row.get('rcept_dt', '')} ({_link(row.get('rcept_no', ''))})",
        f"- 이사회결의일: {row.get('board_decision_date', '-') or '-'}",
        f"- 종류: {row.get('exchange_kind', '-') or '-'} / 형태: {row.get('scale', '-') or '-'}",
        f"- 교환비율: `{row.get('ratio', '-') or '-'}`",
        f"- 대상회사: {tg.get('name', '-') or '-'} (관계: {tg.get('relationship', '-') or '-'})",
        f"  - 대표: {tg.get('representative', '-') or '-'} / 사업: {tg.get('business', '-') or '-'}",
        f"  - 발행주식(보통): {tg.get('outstanding_common', '-') or '-'}",
        f"  - 재무 (자산/자본/매출): {fin.get('total_assets', '-') or '-'} / {fin.get('total_equity', '-') or '-'} / {fin.get('revenue', '-') or '-'}",
        f"- 일정: 교환계약 {sched.get('exchange_contract', '-') or '-'} → 주총 {sched.get('shareholders_meeting', '-') or '-'} → 교환일 {sched.get('exchange_date', '-') or '-'}",
        f"- 외부평가: {row.get('external_evaluator', '-') or '-'}",
        f"- 매수청구권 가격: {row.get('appraisal_right_price', '-') or '-'}",
        f"- 목적: {row.get('purpose', '-') or '-'}",
        "",
    ]
    return lines


def _render(payload: dict[str, Any], scope: str) -> str:
    data = payload.get("data", {})
    window = data.get("window", {})
    counts = data.get("event_count", {})
    usage = data.get("usage", {})
    lines = [
        f"# {data.get('canonical_name', payload.get('subject', ''))} 지배구조 재편 (corporate_restructuring)",
        "",
        f"- company_id: `{data.get('company_id', '')}`",
        f"- scope: `{scope}`",
        f"- 조사 구간: `{window.get('start_date', '')}` ~ `{window.get('end_date', '')}`",
        f"- 사건 수: 합병 {counts.get('merger', 0)} / 분할 {counts.get('split', 0)} / 분할합병 {counts.get('division_merger', 0)} / 주식교환 {counts.get('share_exchange', 0)}",
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
            lines.append("조사 구간 내 지배구조 재편 사건 없음.")
            return "\n".join(lines)
        lines.extend([
            "## 사건 타임라인",
            "| 날짜 | 종류 | 상대방·신설 | 비율 | 원문 |",
            "|------|------|-----------|------|------|",
        ])
        for ev in timeline:
            lines.append(
                f"| {ev.get('rcept_dt', '')} | {ev.get('event_label', '-')} | {ev.get('counterparty_or_new_entity', '-') or '-'} | `{ev.get('ratio', '-') or '-'}` | {_link(ev.get('rcept_no', ''))} |"
            )

    if scope == "merger":
        events = data.get("merger_events", [])
        if not events:
            lines.append("합병 결정 없음.")
        else:
            lines.append("## 합병 결정 상세")
            for row in events:
                lines.extend(_render_merger_card(row))

    if scope == "split":
        events = data.get("split_events", [])
        if not events:
            lines.append("분할/분할합병 결정 없음.")
        else:
            lines.append("## 분할/분할합병 결정 상세")
            for row in events:
                lines.extend(_render_split_card(row))

    if scope == "share_exchange":
        events = data.get("share_exchange_events", [])
        if not events:
            lines.append("주식교환·이전 결정 없음.")
        else:
            lines.append("## 주식교환·이전 결정 상세")
            for row in events:
                lines.extend(_render_exchange_card(row))

    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def corporate_restructuring(
        company: str,
        scope: str = "summary",
        start_date: str = "",
        end_date: str = "",
        format: str = "md",
    ) -> str:
        """desc: 지배구조 재편 4종(회사합병/분할/분할합병/주식교환·이전) 결정 공시를 통합 제공. 합병비율, 상대방, 신주발행, 외부평가, 주식매수청구권 등 핵심 수치 정형화.
        when: M&A·지주회사 전환·자회사 흡수 분석. 주식매수청구권 가격, 합병비율, 상대방 재무 같은 정형 수치 비교가 필요할 때.
        rule: DART 주요사항보고서(DS005) 4개 구조화 API 사용 — `cmpMgDecsn`(합병), `cmpDvDecsn`(분할), `cmpDvmgDecsn`(분할합병), `stkExtrDecsn`(주식교환·이전). 모두 병렬 호출. 기본 lookback 24개월. PDF/원문 파싱 없음, API 응답만 정규화. evidence_refs 최대 5건.
        scope: `summary`(기본, 4종 통합 timeline) / `merger`(합병 카드) / `split`(분할+분할합병 카드) / `share_exchange`(주식교환·이전 카드).
        ref: ownership_structure (지분 변화), shareholder_meeting (관련 주총), evidence (원문 확인)
        """
        payload = await build_corporate_restructuring_payload(
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
