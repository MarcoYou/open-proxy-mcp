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
        year_notes: list[tuple[int, str, str]] = []  # (year, se, rm) — DART 공시 원문 비고
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
            for b in y.get("by_type", []):
                if b.get("note"):
                    year_notes.append((y.get("year"), b.get("type"), b["note"]))
            for note in y.get("limit_notes", []):
                year_notes.append((y.get("year"), "승인한도", note))
        lines.append("")
        if year_notes:
            lines.append("**DART 공시 비고**(원문 그대로 — 퇴직금·중도사임 등 1회성 사유나 선임/사임")
            lines.append("일자가 담긴 경우 있음, 소진율 해석 시 참고):")
            for yr, se, note in year_notes:
                lines.append(f"- [{yr}·{se}] {note}")
            lines.append("")
        lines.append("> 소진율 = 이사류 실지급 합 ÷ 주총 승인한도. 감사위원은 이사 한도 안(순수 감사만 별도).")
        lines.append("> '적절성'은 동종·규모 대비 판단이 필요해 수치·변동·flag만 제공(가치판단 안 함).")
        lines.append("")

    roster = d.get("roster")
    if roster:
        lines.append(f"## 임원 현황 (총 {roster.get('headcount_total')}명 · 등기 이사회 {roster.get('headcount_board')}명)")
        lines.append("")
        people = roster.get("roster") or []
        if people:
            lines.append("| 성명 | 성별 | 출생년월 | 직위 | 구분 | 상근 | 담당업무 | 재직기간 | 임기만료 | 최대주주 관계 |")
            lines.append("|---|---|---|---|---|---|---|---|---|---|")
            for p in people:
                lines.append(
                    f"| {p.get('name')} | {p.get('gender') or '-'} | {p.get('birth_ym') or '-'} | "
                    f"{p.get('position')} | {p.get('director_type')} | {p.get('full_time') or '-'} | "
                    f"{p.get('duty') or '-'} | {p.get('tenure') or '-'} | {p.get('tenure_end') or '-'} | "
                    f"{p.get('largest_shareholder_relation') or '-'} |"
                )
            lines.append("")
            with_career = [p for p in people if p.get("main_career")]
            if with_career:
                lines.append("**주요 경력**:")
                for p in with_career:
                    lines.append(f"- **{p.get('name')}**: {p['main_career']}")
                lines.append("")
        changes = roster.get("changes_vs_prev_year") or []
        if changes:
            lines.append("### 전년 대비 변동 (이름/생년월 diff 추론)")
            for c in changes:
                yr = c.get("since_year") or c.get("until_year")
                lines.append(f"- **{c.get('name')}** ({c.get('position')}) — {c.get('change')} [{yr}]")
            lines.append("")
        else:
            lines.append("- 전년 대비 이사회 구성 변동 없음(또는 diff 미산출)")
            lines.append("")
        official = roster.get("official_outside_director_changes") or []
        if official:
            lines.append("### 사외이사 변동현황 (DART 공식 집계, 개별 성명 없음 — 교차검증용)")
            lines.append("")
            lines.append("| 연도 | 이사총수 | 사외이사수 | 선임 | 해임 | 중도퇴임 |")
            lines.append("|---|---|---|---|---|---|")
            for o in official:
                def _n(v):
                    return v if v is not None else 0
                lines.append(
                    f"| {o.get('year')} | {_n(o.get('director_count'))} | {_n(o.get('outside_director_count'))} | "
                    f"{_n(o.get('appointed'))} | {_n(o.get('released'))} | {_n(o.get('mid_term_resigned'))} |"
                )
            lines.append("")
            cc = roster.get("diff_cross_check")
            if cc:
                lines.append(f"> {cc.get('note')}")
                lines.append("")

    indiv = d.get("individual")
    if indiv:
        lines.append("## 개인별 보수 (5억+ 공개)")
        lines.append("")
        for y in indiv.get("per_year", []):
            people = y.get("people") or []
            lines.append(f"### {y.get('year')}년 ({y.get('disclosed_count')}명)")
            if people:
                lines.append("| 성명 | 직위 | 보수총액 |")
                lines.append("|---|---|---|")
                for p in people:
                    lines.append(f"| {p.get('name')} | {p.get('position')} | {_won(p.get('total_pay_krw'))} |")
                lines.append("")
                with_breakdown = [p for p in people if p.get("breakdown_note")]
                if with_breakdown:
                    lines.append("**보수총액 미포함 내역**(RSA·스톡옵션 등 향후 확정될 주식 보상 —")
                    lines.append("아직 보수총액엔 안 잡히나 실질적 보상 규모 판단에 참고):")
                    for p in with_breakdown:
                        lines.append(f"- **{p.get('name')}**: {p['breakdown_note']}")
                    lines.append("")
        lines.append(f"> {indiv.get('note')}")
        lines.append("")

    unreg = d.get("unregistered")
    if unreg:
        lines.append("## 미등기 집행임원 보수")
        lines.append("")
        for y in unreg.get("per_year", []):
            lines.append(f"**{y.get('year')}년**")
            for b in y.get("buckets", []):
                lines.append(f"- {b.get('type')}: {b.get('headcount')}명 · 인당 {_won(b.get('per_capita_krw'))} "
                             f"(연급여총액 {_won(b.get('annual_total_krw'))})"
                             + (f" — {b['note']}" if b.get("note") else ""))
        lines.append("> 미등기임원은 주총 승인한도 밖(등기 안 됨) — 등기이사와 별개 지표.")
        lines.append("")

    gap = d.get("pay_gap")
    if gap:
        lines.append("## 경영진 vs 직원 보수 격차")
        lines.append("")
        lines.append("| 연도 | 등기이사 인당보수 | 직원 평균급여 | 직원수 | 격차배수 |")
        lines.append("|---|---|---|---|---|")
        for y in gap.get("per_year", []):
            gm = y.get("gap_multiple")
            head = y.get("employee_headcount")
            lines.append(
                f"| {y.get('year')} | {_won(y.get('director_per_capita_krw'))} | "
                f"{_won(y.get('employee_avg_pay_krw'))} | {f'{head:,}명' if head else 'N/M'} | "
                f"{f'{gm}배' if gm is not None else '산출불가'} |"
            )
        lines.append("")
        latest_gap = (gap.get("per_year") or [{}])[0]
        breakdown = latest_gap.get("employee_breakdown") or []
        if breakdown:
            lines.append(f"**{latest_gap.get('year')}년 부문·성별 직원 세부**:")
            lines.append("| 부문 | 성별 | 정규직 | 계약직 | 합계 | 평균근속(년) | 1인평균급여 |")
            lines.append("|---|---|---|---|---|---|---|")
            for b in breakdown:
                division = f"**{b.get('division') or '-'}(합계)**" if b.get("is_total") else (b.get("division") or "-")
                lines.append(
                    f"| {division} | {b.get('gender') or '-'} | "
                    f"{b.get('regular_headcount', '-')} | {b.get('contract_headcount', '-')} | "
                    f"{b.get('total_headcount', '-')} | {b.get('avg_tenure_years') or '-'} | "
                    f"{_won(b.get('per_capita_salary_krw'))} |"
                )
            if any(b.get("is_total") for b in breakdown) and any(not b.get("is_total") for b in breakdown):
                lines.append("")
                lines.append("> ⚠️ 부문 상세행과 '(합계)' 행이 함께 있는 회사는 급여가 상세행에 없어")
                lines.append("> 합계행에만 실제 총액이 옴 — **합계행 외 다른 행과 합산 금지**(더블카운트).")
            lines.append("")
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
        """desc: **개별 이사 단위** 정보 — 이사 인당 보수, 보수한도 소진율(연도별 rm 비고 원문 포함),
        임원 재직/사퇴 변동(연도 diff + DART 공식 사외이사 변동 집계로 교차검증), 개인별(5억+) 보수와
        RSA/스톡옵션 등 미확정 주식보상, 미등기임원 보수, 경영진-직원 보수 배수(부문별 세부 포함) —
        전부 lookback_years만큼 연도별(YoY) 비교 가능. corp_gov_report가 '회사 15지표 준수'라면 이건
        '누가 얼마 받고 인원이 어떻게 바뀌었나'. 가치판단(적절/과다)은 하지 않고 수치·전년비 변동·flag만.
        when: 이사 보수 안건 판단, 스튜어드십 engagement — 예: "이사 보수한도 소진율 얼마야"(compensation),
        "작년에 이사 누가 오고 나갔어"(roster), "대표이사들 각각 얼마 받아·스톡옵션 있나"(individual),
        "임원-직원 보수 격차 몇 배"(pay_gap), "이번 주총 한도 왜 올려달래"(pay_agenda).
        rule: exctvSttus+drctrAdtAllMendngSttus 2종+hmvAuditIndvdlBySttus+unrstExctvMendngSttus+
        empSttus+outcmpnyDrctrNdChangeSttus 정형 API 6종 전부 재사용. 소진율 분자는 감사위원 포함
        이사류 실지급 합(순수 감사만 별도 한도), 한도 공백해는 최근 유효연도 lookback. 재직/사퇴 diff는
        2-pass 매칭(이름 정확일치로 먼저 확정 → 나머지만 생년월로, 남은 후보군에서 유일할 때만)으로
        로마자표기 변동·동일 생년월 동명이인 오탐 둘 다 억제 — 사외이사 변동현황 API의 공식 집계
        (선임/해임/중도퇴임 수, 사외이사 신규선임만 필터링해 비교)로 규모감 교차검증. attendance scope는
        지배구조보고서 원문 파서 v2 예정(금융지주는 PDF 별도양식).
        scope: compensation | roster | individual(5억+ 실명, RSA/스톡옵션 노트 포함) |
        unregistered(미등기임원) | pay_gap(경영진 vs 직원 배수, 부문별 세부) |
        pay_agenda(보수한도 주총안건 올해vs작년) | attendance(v2 stub) | summary(기본)
        year: 기준 사업연도(0=최근 확정 전년). lookback_years: 조회 기간(년), 기본 3 — 대부분 scope에서 YoY 적용
        ref: corp_gov_report, director_evaluation, shareholder_meeting
        """
        payload = await build_director_board_payload(
            company, scope=scope, year=year, lookback_years=lookback_years, format=format)
        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") in ("ambiguous", "error"):
            return _render_status(payload)
        return _render(payload)
