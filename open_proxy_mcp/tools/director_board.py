"""director_board — 이사회/개별 이사 프로필 (보수·소진율·재직/사퇴·출석률)."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.director_board import build_director_board_payload, _is_bare_marker
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
    _SCOPE_KO = {"summary": "요약", "detail": "상세", "compensation": "보수", "changes": "변동"}
    lines = [f"# {d.get('canonical_name')} 이사회 프로필 — {_SCOPE_KO.get(scope, scope)} (기준연도 {d.get('year')})", ""]

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
            # 각주 마커뿐인 비고('(주1)' 등)는 정보가 없어 렌더에서 억제 — data_quality_flags에
            # footnote_marker_unresolved로 대신 표식(120사 census: 9사에서 무의미 마커 라인이 뜨던 문제).
            for b in y.get("by_type", []):
                if b.get("note") and not _is_bare_marker(b.get("note")):
                    year_notes.append((y.get("year"), b.get("type"), b["note"]))
            for note in y.get("limit_notes", []):
                if not _is_bare_marker(note):
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
        if roster.get("roster_as_of"):
            # 어느 보고서 기준인지 밝힌다 — 2~3월엔 사업보고서가 없어 분기보고서로 채운다
            lines.append(f"- 기준: {roster['roster_as_of']}")
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
            lines.append("### 전년 대비 이사회 변동 (등기이사·감사 · 이름/생년월 비교 추론)")
            for c in changes:
                yr = c.get("since_year") or c.get("until_year")
                dt = c.get("director_type")
                lines.append(f"- **{c.get('name')}** ({c.get('position')}{f' · {dt}' if dt else ''}) — {c.get('change')} [{yr}]")
            lines.append("")
        else:
            lines.append("- 전년 대비 이사회 구성 변동 없음(또는 비교 미산출)")
            lines.append("")
        # 사업보고서끼리 비교하면 기중(예: 6월) 사임이 다음 사업보고서까지 안 보인다.
        # 분기·반기 명단을 직전 사업보고서와 대조해 그 사이 변동을 따로 보여준다.
        since_annual = roster.get("changes_since_last_annual") or []
        if roster.get("changes_since_last_annual_basis"):
            lines.append(f"### 직전 사업보고서 이후 변동 ({roster['changes_since_last_annual_basis']})")
            if since_annual:
                for c in since_annual:
                    dt = c.get("director_type")
                    lines.append(f"- **{c.get('name')}** ({c.get('position')}"
                                 f"{f' · {dt}' if dt else ''}) — {c.get('change')}")
            else:
                lines.append("- 이사회 구성 변동 없음")
            n_exec = roster.get("executive_changes_since_last_annual_count") or 0
            if n_exec:
                lines.append(f"- (미등기 집행임원 변동 {n_exec}건 — 이사회 아님)")
            lines.append("")
        # 미등기 집행임원 변동은 이사회 변동과 성격이 달라 참고로만 분리 표기(대형사에서 상무 인사
        # 이동이 이사회 이탈로 오독되던 문제 대응, QA/스튜어드십 260709).
        exec_changes = roster.get("executive_changes_vs_prev_year") or []
        if exec_changes:
            joined = sum(1 for c in exec_changes if "이탈" not in (c.get("change") or ""))
            left = len(exec_changes) - joined
            names = ", ".join(c.get("name") for c in exec_changes[:12])
            more = f" 외 {len(exec_changes)-12}명" if len(exec_changes) > 12 else ""
            lines.append(f"### 미등기 집행임원 변동 (참고 — 이사회 아님): 신규 {joined} · 이탈 {left}")
            lines.append(f"- {names}{more}")
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
                with_breakdown = [p for p in people if p.get("breakdown_note")
                                  and not _is_bare_marker(p.get("breakdown_note"))]
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
                note = b.get("note")
                hc = b.get("headcount")
                # DART가 type만 주고 인원·급여가 전부 없는 정보량-0 bucket에서 'None명'이 찍히던
                # 회귀 수정(regression QA 300사: 펄어비스 등 8사) — None은 N/M로.
                if hc is None and b.get("per_capita_krw") is None and b.get("annual_total_krw") is None:
                    continue  # 완전 빈 bucket은 렌더 억제
                lines.append(f"- {b.get('type')}: {hc if hc is not None else 'N/M'}명 · "
                             f"인당 {_won(b.get('per_capita_krw'))} "
                             f"(연급여총액 {_won(b.get('annual_total_krw'))})"
                             + (f" — {note}" if note and not _is_bare_marker(note) else ""))
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
            def _cell(v):
                # dict 키는 항상 존재하고 값만 None이라 .get(k,'-') 기본값이 발동 못 해 'None'이
                # 문자열로 찍히던 버그(QA 260709: 펩트론 등 15+파일). 0은 유효값이라 살린다.
                return v if v is not None else "-"
            for b in breakdown:
                # 부문명 원문 개행('전력설비\n정비분야')이 표 행을 두 줄로 쪼개던 회귀 수정
                # (regression QA 300사: 한전KPS·피에스케이 등 3사). 개행→공백.
                div_raw = (b.get("division") or "-").replace("\n", " ").strip()
                division = f"**{div_raw}(합계)**" if b.get("is_total") else div_raw
                lines.append(
                    f"| {division} | {b.get('gender') or '-'} | "
                    f"{_cell(b.get('regular_headcount'))} | {_cell(b.get('contract_headcount'))} | "
                    f"{_cell(b.get('total_headcount'))} | {b.get('avg_tenure_years') or '-'} | "
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
        if not agenda.get("proposed_limit_krw"):
            lines.append(f"- {agenda.get('note')}")
            if agenda.get("fallback_limit_recent_krw"):
                chg = agenda.get("fallback_limit_change_pct")
                lines.append(
                    f"- 📄 (참고) 사업보고서 승인한도 추이: {_won(agenda.get('fallback_limit_prev_krw'))} → "
                    f"{_won(agenda.get('fallback_limit_recent_krw'))}"
                    + (f" (**{chg:+.1f}%**)" if chg is not None else ""))
                lines.append(f"  - {agenda.get('fallback_note')}")
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
    if att:
        lines.append("## 이사 출석률 (사업보고서 원문 요약)")
        if att.get("status") == "parsed":
            mc = att.get("board_meeting_count")
            if mc:
                lines.append(f"- 이사회 개최: 총 {mc}회")
            lines.append("")
            lines.append("| 이사 | 출석률 |")
            lines.append("|---|---|")
            for dd in att.get("directors", []):
                mark = " ⚠️저조" if dd.get("low") else ""
                lines.append(f"| {dd.get('name')} | {dd.get('attendance_pct')}%{mark} |")
            lines.append("")
            low = att.get("low_attendance") or []
            if low:
                names = ", ".join(f"{d.get('name')}({d.get('attendance_pct')}%)" for d in low)
                lines.append(f"- ⚠️ 출석률 저조: {names}")
            lines.append(f"> {att.get('note', '')}")
        else:
            lines.append(f"- ⏳ {att.get('note')}")
        lines.append("")

    pc = d.get("pay_criteria")
    if pc:
        lines.append("## 보수 산정기준 (사업보고서 VIII-2 원문)")
        if pc.get("status") == "parsed":
            lines.append(f"- 출처: {pc.get('source')} (rcept_no `{pc.get('rcept_no')}`)")
            lines.append("")
            policy = pc.get("pay_policy") or []
            if policy:
                lines.append("### 보수지급기준 (버킷별 정책)")
                lines.append("| 구분 | 성과급 배수/비율 | 산정기준(원문) |")
                lines.append("|---|---|---|")
                for p in policy:
                    rng = ", ".join(p.get("ranges") or []) or "-"
                    crit = (p.get("criteria") or "").replace("\n", " ")
                    lines.append(f"| {p.get('group')} | {rng} | {crit[:300]} |")
                lines.append("")
            elif pc.get("policy_narrative"):
                lines.append(f"### 보수지급기준: {pc.get('policy_narrative')}")
                lines.append("")
            people = pc.get("individuals") or []
            if people:
                lines.append("### 개인별 산정기준 및 방법 (급여/상여 분해 + KPI)")
                # group별로 묶어 표기 (상위5명 블록은 미등기·직원 포함)
                by_group: dict[str, list] = {}
                for pr in people:
                    by_group.setdefault(pr.get("group") or "", []).append(pr)
                for grp, prs in by_group.items():
                    lines.append(f"**〈{grp}〉**")
                    for pr in prs:
                        comps = [c for c in pr.get("components", []) if c.get("amount_krw") is not None]
                        comp_str = " · ".join(f"{c['pay_type']} {_won(c['amount_krw'])}" for c in comps)
                        # 검증 배지: 정형 API(독립)와 대조 결과를 이름 옆에. ✅=API 총액과 일치,
                        # ❗=불일치(파서 오독 의심), 무표시=API 미공개(5억 미만) 또는 미매칭.
                        badge = ""
                        if pr.get("api_consistent") is True:
                            badge = " ✅API일치"
                        elif pr.get("api_consistent") is False:
                            badge = f" ❗API불일치(API {_won(pr.get('api_total_krw'))}, 차이 {_won(pr.get('api_diff_krw'))})"
                        lines.append(f"- **{pr.get('name')}** (총 {_won(pr.get('total_krw'))}){badge}: {comp_str}")
                        for c in pr.get("components", []):
                            if c.get("ranges") or (c.get("pay_type") in ("상여",) and c.get("basis")):
                                rng = f" [배수/가중치: {', '.join(c['ranges'])}]" if c.get("ranges") else ""
                                lines.append(f"  - {c['pay_type']} 산정: {(c.get('basis') or '')[:220]}{rng}")
                    lines.append("")
            # 검증 요약: ① 파서 자기일치(in-doc 표) ② 하이브리드(정형 API — 독립 교차검증).
            rec = pc.get("reconciliation") or {}
            arec = pc.get("api_reconciliation") or {}
            if rec.get("checkable") or arec.get("checkable"):
                lines.append("### 검증 (개인별 Σ분해액 대조)")
                if rec.get("checkable"):
                    lines.append(f"- 파서 자기일치(원문 개인별표): {rec.get('consistent')}/{rec.get('checkable')} "
                                 f"({rec.get('consistent_rate')}%)")
                if arec.get("checkable"):
                    lines.append(f"- **하이브리드(정형 API 독립대조)**: {arec.get('consistent')}/{arec.get('checkable')} "
                                 f"({arec.get('consistent_rate')}%) — {arec.get('source')}")
                # API엔 5억+로 있는데 파서가 매칭 못한 인물 = 이름 병합/누락 의심(삼성생명류 silent case 적발).
                for u in (arec.get("api_unmatched") or []):
                    lines.append(f"  - ❗ API 5억+ 공개자 **{u.get('name')}**({_won(u.get('api_total_krw'))})가 파서 개인목록에 없음 — 이름 병합/누락 의심")
                lines.append("")
            lines.append(f"> {pc.get('note', '')}")
            lines.append(f"> {pc.get('unit_note', '')}")
        else:
            lines.append(f"- ⏳ {pc.get('note')}")
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

    # 데이터 품질 참고 — 파싱 신뢰도에 영향을 주는 신호를 종류별로 투명하게(120사 census 설계).
    # warn(신뢰도 낮춰 봐야 함)을 위, info(실제값이거나 참고)를 아래로.
    flags = d.get("data_quality_flags") or []
    if flags:
        warns = [f for f in flags if f.get("severity") == "warn"]
        infos = [f for f in flags if f.get("severity") != "warn"]
        lines.append("## 데이터 품질 참고")
        for f in warns + infos:
            mark = "⚠️" if f.get("severity") == "warn" else "ℹ️"
            yr = f" {f['year']}" if f.get("year") else ""
            subj = f" {f['subject']}" if f.get("subject") else ""
            lines.append(f"- {mark} [{f.get('scope')}{yr}]{subj} {f.get('detail')}")
            # 원문 폴백으로 해소된 각주 본문(정형 API가 못 주던 내용을 사업보고서 원문에서 복구).
            if f.get("resolved_text"):
                lines.append(f"  - ↳ **원문 각주**: {f['resolved_text']}")
            elif f.get("raw_text_excerpt"):
                lines.append(f"  - ↳ 원문 발췌(각주 자동추출 실패, 직접 확인): {f['raw_text_excerpt'][:200]}…")
        if any(f.get("kind") == "footnote_marker_unresolved" and not f.get("resolved_text")
               for f in flags):
            lines.append("> 각주 마커(예 `(주1)`)는 정형 API가 본문을 안 줘 원문 각주에만 있음 — 해소 실패 건은 사업보고서 원문 확인.")
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
        resolve_footnotes: bool = True,
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
        (선임/해임/중도퇴임 수, 사외이사 신규선임만 필터링해 비교)로 규모감 교차검증. attendance는
        사업보고서 원문에서 개별 이사 출석률을 파싱하되, 회사가 일부(주로 사외이사)만 '(출석률:%)'로
        기재하면 전체가 아님을 data_quality_flags(attendance_partial)로 표시. 원문 fetch(8MB)라 summary
        기본엔 미포함 — on-demand scope로 조회.
        scope: compensation | roster | individual(5억+ 실명, RSA/스톡옵션 노트 포함) |
        unregistered(미등기임원) | pay_gap(경영진 vs 직원 배수, 부문별 세부) |
        pay_agenda(보수한도 주총안건 올해vs작년) | attendance(개별 이사 출석률·원문, summary 제외) |
        pay_criteria(보수 산정기준·개인별 급여/상여 분해·KPI 가중치, 사업보고서 VIII-2 원문, summary 제외) | summary(기본)
        각주 마커('(주1)' 등 정형 API가 본문을 안 주는 비고)는 resolve_footnotes=True(기본)면 해당
        사업보고서 원문에서 각주 본문을 자동 복구(마커 뜬 공시만 1회 fetch·캐시) — 실패 시 원문 발췌 폴백.
        year: 기준 사업연도(0=최근 확정 전년). lookback_years: 조회 기간(년), 기본 3 — 대부분 scope에서 YoY 적용
        resolve_footnotes: 각주 마커를 원문에서 해소할지(기본 True). False면 원문 fetch 없이 마커만 플래그.
        ref: corp_gov_report, director_evaluation, shareholder_meeting
        """
        payload = await build_director_board_payload(
            company, scope=scope, year=year, lookback_years=lookback_years, format=format,
            resolve_footnotes=resolve_footnotes)
        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") in ("ambiguous", "error"):
            return _render_status(payload)
        return _render(payload)
