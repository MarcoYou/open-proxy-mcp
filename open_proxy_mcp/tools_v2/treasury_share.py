"""v2 treasury_share public tool."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.treasury_share import build_treasury_share_payload


def _viewer(rcept_no: str) -> str:
    return f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}" if rcept_no else "-"


_EVENT_LABELS = {
    # decisions
    "acquisition_decision": "취득결정",
    "disposal_decision": "처분결정",
    "trust_contract": "신탁체결",
    "trust_termination": "신탁해지",
    "cancelation_decision": "소각결정",
    # 일부 코드는 event 키가 없거나 다른 string — fallback
    "acquisition": "취득결정",
    "disposal": "처분결정",
    "cancelation": "소각결정",
    # executions (NEW)
    "acquisition_result": "취득결과",
    "disposal_result": "처분결과",
    "trust_acquisition_status": "신탁취득상황",
    "trust_termination_result": "신탁해지결과",
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
        cycle_matched = data.get("cycle_matched_count")
        cycle_note = f" (사이클 매칭 {cycle_matched}건)" if cycle_matched else ""
        lines.extend([
            "",
            f"## 이벤트 타임라인{cycle_note}",
            "| 공시일 | phase | 유형 | 주식수 | 금액(원) | 사이클 link | rcept_no |",
            "|--------|-------|------|--------|---------|-------------|----------|",
        ])
        for ev in events_to_show[:40]:
            ev_type = _EVENT_LABELS.get(ev.get("event", ""), ev.get("event", ""))
            phase = (ev.get("phase") or "-")
            phase_mark = "[D]" if phase == "decision" else ("[E]" if phase == "execution" else "")
            shares = ev.get("shares") or ev.get("actual_shares") or ev.get("acquired_shares") or 0
            amount = (ev.get("amount_krw") or ev.get("actual_amount_krw")
                      or ev.get("total_amount_krw") or ev.get("acquired_amount_krw") or 0)
            shares_str = f"{shares:,}" if shares else "-"
            amount_str = f"{amount:,}" if amount else "-"
            link = ""
            if ev.get("linked_decision_rcept_no"):
                link = f"→ {ev['linked_decision_rcept_no']}"
            elif ev.get("linked_execution_rcept_nos"):
                link = f"← {','.join(ev['linked_execution_rcept_nos'][:2])}"
            lines.append(f"| {ev.get('rcept_dt', '-')} | {phase_mark} | {ev_type} | {shares_str} | {amount_str} | {link} | `{ev.get('rcept_no', '')}` |")
        lines.append("")
        lines.append("> [D]=결정 (사전 의도) / [E]=결과보고서 (사후 집행). 사이클 link로 결정↔결과 매칭.")
        lines.append("")

        # 결정 detail card — iter10 추가 normalize 필드 노출 (보통/우선주, 위탁사, 사외이사, 보유예상기간)
        decisions = [e for e in events_to_show if e.get("phase") == "decision"]
        if decisions:
            lines.append("## 결정 detail")
            for ev in decisions[:20]:
                ev_type = _EVENT_LABELS.get(ev.get("event", ""), ev.get("event", ""))
                lines.append(f"### {ev_type} — {ev.get('rcept_dt','-')} (`{ev.get('rcept_no','')}`)")
                # 종류별 수량/금액
                sc = ev.get("shares_common") or 0
                sp = ev.get("shares_preferred") or 0
                ac = ev.get("amount_common_krw") or 0
                ap = ev.get("amount_preferred_krw") or 0
                if sc or sp or ac or ap:
                    if sc or ac:
                        lines.append(f"- 보통주: {sc:,}주 / {ac:,}원" + (f" (단가 {ev.get('price_common_krw'):,}원)" if ev.get("price_common_krw") else ""))
                    if sp or ap:
                        lines.append(f"- 우선주(기타): {sp:,}주 / {ap:,}원" + (f" (단가 {ev.get('price_preferred_krw'):,}원)" if ev.get("price_preferred_krw") else ""))
                # 기간
                if ev.get("start_date") or ev.get("end_date"):
                    lines.append(f"- 기간: {ev.get('start_date','-')} ~ {ev.get('end_date','-')}")
                if ev.get("holding_start_date") or ev.get("holding_end_date"):
                    lines.append(f"- 보유예상기간: {ev.get('holding_start_date','-')} ~ {ev.get('holding_end_date','-')}")
                # 결정 본질
                if ev.get("purpose"):
                    lines.append(f"- 목적: {ev['purpose']}")
                if ev.get("method"):
                    lines.append(f"- 방법: {ev['method']}")
                if ev.get("counterparty"):
                    lines.append(f"- **상대방**: {ev['counterparty']}")
                # 거버넌스
                if ev.get("broker_name"):
                    lines.append(f"- 위탁증권사: {ev['broker_name']}")
                if ev.get("trustee_name"):
                    lines.append(f"- 신탁기관: {ev['trustee_name']}")
                attended = ev.get("outside_director_attended")
                absent = ev.get("outside_director_absent")
                if attended or absent:
                    lines.append(f"- 사외이사 참석: {attended}명 (불참 {absent}명)")
                if ev.get("termination_reason"):
                    lines.append(f"- 해지사유: {ev['termination_reason']}")
                if ev.get("for_cancelation"):
                    lines.append(f"- ⚡ **소각 의도** (취득목적에 '소각' 명시)")
                lines.append(f"- [DART 본문]({_viewer(ev.get('rcept_no',''))})")
                lines.append("")

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
        """desc: 자기주식 이벤트 통합. **결정 5종(사전 의도) + 결과보고서 4종(사후 집행)** 통합 집계. 주주환원 검증 = 결정만 X, 실제 집행 cross-check.
        when: 자사주 취득·처분·소각·신탁 이력·규모. 결정↔결과 사이클 매칭으로 집행 검증.
        rule: 9 source 병렬 — Decisions: tsstkAqDecsn(취득)/tsstkDpDecsn(처분)/tsstkAqTrctrCnsDecsn(신탁체결)/tsstkAqTrctrCcDecsn(신탁해지)/소각결정. Executions: 취득결과/처분결과/신탁취득상황/신탁해지결과 보고서. ACODE 본문 파싱. 사이클 매칭은 "주요사항보고서 제출일" / "신탁계약 체결일" ↔ decision rcept_dt.
        scope: `summary` 모든 events + breakdown + cycle 매칭 / `annual` 사업보고서 연간 누적 잔고
        ref: value_up, ownership_structure, dividend, evidence
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
