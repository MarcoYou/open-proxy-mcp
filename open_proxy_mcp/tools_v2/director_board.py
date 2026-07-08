"""director_board — 이사회/개별 이사 프로필 (보수·소진율·재직/사퇴·출석률)."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.director_board import build_director_board_payload
from open_proxy_mcp.services.contracts import as_pretty_json


def _won(v) -> str:
    """원화를 억원 단위로 읽기 쉽게."""
    if v is None:
        return "N/M"
    return f"{v/1e8:,.1f}억원"


def _render_status(payload: dict[str, Any]) -> str:
    status = payload.get("status", "error")
    lines = [f"# director_board: {payload.get('subject', '')} — status=`{status}`", ""]
    for w in payload.get("warnings", []):
        lines.append(f"- {w}")
    cands = (payload.get("data") or {}).get("candidates") or []
    if cands:
        lines += ["", "| 회사명 | ticker | corp_code |", "|---|---|---|"]
        for c in cands:
            lines.append(f"| {c.get('corp_name')} | `{c.get('stock_code') or '-'}` | `{c.get('corp_code')}` |")
    return "\n".join(lines)


def _render(payload: dict[str, Any]) -> str:
    d = payload["data"]
    scope = d.get("scope")
    lines = [f"# {d.get('canonical_name')} 이사회 프로필 — {scope} (기준연도 {d.get('year')})", ""]

    comp = d.get("compensation")
    if comp:
        lines.append("## 이사 보수 · 한도 소진율")
        lines.append("")
        lines.append("| 연도 | 승인한도 | 이사류 실지급 | 소진율 | 등기이사 인당보수 | 비고 |")
        lines.append("|---|---|---|---|---|---|")
        for y in comp.get("per_year", []):
            reg_pc = next((b.get("per_capita_krw") for b in y.get("by_type", [])
                           if "등기이사" in (b.get("type") or "") and "제외" in (b.get("type") or "")), None)
            util = y.get("utilization_pct")
            flag = y.get("utilization_flag")
            util_mark = " 🚨한도초과" if flag == "exceeded_limit" else (" ⚠️" if flag == "high" else "")
            util_str = f"{util}%{util_mark}" if util is not None else "N/M"
            lines.append(
                f"| {y.get('year')} | {_won(y.get('director_pay_limit_krw'))} | "
                f"{_won(y.get('director_paid_total_krw'))} | {util_str} | {_won(reg_pc)} | "
                f"{y.get('limit_source', '')} |"
            )
        lines.append("")
        lines.append("> 소진율 = 이사류 실지급 합 ÷ 주총 승인한도. 감사위원은 이사 한도 안(순수 감사만 별도).")
        lines.append("> '적절성'은 동종·규모 대비 판단이 필요해 수치·변동·flag만 제공(가치판단 안 함).")
        lines.append("")

    roster = d.get("roster")
    if roster:
        lines.append(f"## 임원 현황 (총 {roster.get('headcount_total')}명 · 등기 이사회 {roster.get('headcount_board')}명)")
        lines.append("")
        changes = roster.get("changes_vs_prev_year") or []
        if changes:
            lines.append("### 전년 대비 변동")
            for c in changes:
                yr = c.get("since_year") or c.get("until_year")
                lines.append(f"- **{c.get('name')}** ({c.get('position')}) — {c.get('change')} [{yr}]")
            lines.append("")
        else:
            lines.append("- 전년 대비 이사회 구성 변동 없음(또는 diff 미산출)")
            lines.append("")

    indiv = d.get("individual")
    if indiv:
        lines.append(f"## 개인별 보수 (5억+ 공개, {indiv.get('disclosed_count')}명)")
        lines.append("")
        if indiv.get("people"):
            lines.append("| 성명 | 직위 | 보수총액 |")
            lines.append("|---|---|---|")
            for p in indiv["people"]:
                lines.append(f"| {p.get('name')} | {p.get('position')} | {_won(p.get('total_pay_krw'))} |")
            lines.append("")
        lines.append(f"> {indiv.get('note')}")
        lines.append("")

    unreg = d.get("unregistered")
    if unreg:
        lines.append("## 미등기 집행임원 보수")
        lines.append("")
        for b in unreg.get("buckets", []):
            lines.append(f"- {b.get('type')}: {b.get('headcount')}명 · 인당 {_won(b.get('per_capita_krw'))} "
                         f"(연급여총액 {_won(b.get('annual_total_krw'))})")
        lines.append("> 미등기임원은 주총 승인한도 밖(등기 안 됨) — 등기이사와 별개 지표.")
        lines.append("")

    gap = d.get("pay_gap")
    if gap:
        lines.append("## 경영진 vs 직원 보수 격차")
        lines.append("")
        lines.append(f"- 등기이사 인당보수: {_won(gap.get('director_per_capita_krw'))}")
        lines.append(f"- 직원 평균급여: {_won(gap.get('employee_avg_pay_krw'))} (전체 {gap.get('employee_headcount'):,}명)"
                     if gap.get("employee_headcount") else f"- 직원 평균급여: {_won(gap.get('employee_avg_pay_krw'))}")
        gm = gap.get("gap_multiple")
        lines.append(f"- **격차 배수: {gm}배**" if gm is not None else "- 격차 배수: 산출 불가")
        lines.append(f"> {gap.get('note')}")
        lines.append("")

    agenda = d.get("pay_agenda")
    if agenda:
        lines.append("## 보수한도 주총안건 — 올해 제안 vs 작년 실적")
        lines.append("")
        if agenda.get("status") == "no_agenda":
            lines.append(f"- {agenda.get('note')}")
        else:
            lines.append(f"- 올해 제안 한도: {_won(agenda.get('proposed_limit_krw'))}")
            lines.append(f"- 작년 승인 한도: {_won(agenda.get('prior_limit_krw'))}"
                         + (f" → **인상률 {agenda.get('limit_change_pct'):+.1f}%**" if agenda.get("limit_change_pct") is not None else ""))
            lines.append(f"- 작년 실지급: {_won(agenda.get('prior_actual_krw'))}"
                         + (f" → **작년 소진율 {agenda.get('prior_utilization_pct')}%**" if agenda.get("prior_utilization_pct") is not None else ""))
            if agenda.get("signal"):
                lines.append(f"- 🔎 {agenda.get('signal')}")
        lines.append(f"> {agenda.get('note', '')}")
        lines.append("")

    att = d.get("attendance")
    if att and att.get("status") == "not_implemented":
        lines.append("## 이사회 출석률 · 선임변동 · 겸직")
        lines.append(f"- ⏳ {att.get('note')}")
        lines.append("")

    assess = d.get("assessment")
    if assess:
        lines.append("## 종합 신호")
        lines.append(f"- 최근 소진율: {assess.get('latest_utilization_pct')}%")
        lines.append(f"- 등기이사 인당보수: {_won(assess.get('latest_per_capita_krw'))}")
        pcc = assess.get("per_capita_change_yoy")
        if pcc:
            lines.append(f"- 인당보수 전년비: {_won(pcc.get('prev_krw'))} → {_won(pcc.get('now_krw'))} ({pcc.get('delta_pct'):+.1f}%)")
        deps = assess.get("departures_detected") or []
        if deps:
            lines.append(f"- 감지된 이탈: {', '.join(c.get('name') for c in deps)}")
        lines.append(f"> {assess.get('note', '')}")
        lines.append("")

    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def director_board(
        company: str,
        scope: str = "summary",
        year: int = 0,
        lookback_years: int = 3,
        format: str = "md",
    ) -> str:
        """desc: **개별 이사 단위** 정보 — 이사 인당 보수, 보수한도 소진율, 임원 재직/사퇴 변동(연도 diff),
        (v2)이사회 출석률·겸직 — 를 잡는다. corp_gov_report가 '회사 15지표 준수'라면 이건 '누가 얼마 받고
        인원이 어떻게 바뀌었나'. 소진율·인당보수는 DART 정형 사업보고서 API에서 산출(psn1_avrg_pymntamt,
        주총 승인한도). 가치판단(적절/과다)은 하지 않고 수치·전년비 변동·flag만 제공.
        when: 이사 보수 안건 판단, 스튜어드십 engagement, "인당 보수 적절한가·소진율·사퇴로 보수 변했나".
        rule: exctvSttus(임원현황)+drctrAdtAllMendngSttus*(보수한도·실지급) 정형 API. 소진율 분자는 감사위원
        포함 이사류 실지급 합(순수 감사만 별도 한도), 한도 공백해는 최근 유효연도 lookback. 재직/사퇴 diff는
        이름 OR 생년월 매칭으로 로마자표기·birth 오타 오탐 억제(스냅샷이라 사유는 미확정). attendance scope는
        지배구조보고서 원문 파서 v2 예정(금융지주는 PDF 별도양식).
        scope: compensation | roster | individual(5억+ 실명) | unregistered(미등기임원) |
        pay_gap(경영진 vs 직원 배수) | pay_agenda(보수한도 주총안건 올해vs작년) | attendance(v2 stub) | summary(기본)
        year: 기준 사업연도(0=최근 확정 전년). lookback_years: 조회 기간(년), 기본 3
        ref: corp_gov_report, director_evaluation, shareholder_meeting
        """
        payload = await build_director_board_payload(
            company, scope=scope, year=year, lookback_years=lookback_years, format=format)
        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") in ("ambiguous", "error"):
            return _render_status(payload)
        return _render(payload)
