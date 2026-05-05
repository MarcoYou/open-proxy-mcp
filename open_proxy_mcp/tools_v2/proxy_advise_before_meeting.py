"""proxy_advise_before_meeting — 주총 전 의결권 행사 메모 (운용사 보고서 스타일)."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.proxy_advise import build_proxy_advise_payload
from open_proxy_mcp.services.contracts import as_pretty_json


# 사용자에게 노출되는 internal code → 한국어 자연어 라벨
_INDEPENDENCE_LABELS = {
    "independent": "독립적 (모든 sub-factor 충족)",
    "weak_concerns": "약한 우려 (1개 sub-factor 위반)",
    "concerns": "우려 (다수 sub-factor 위반)",
    "long_tenure_concerns": "장기연임 우려 (5년 룰 위반)",
    "no_data": "데이터 부족",
    "-": "-",
}

_DISQUALIFICATION_LABELS = {
    "clean": "결격사유 없음",
    "red_flag": "결격사유 발견",
    "not_evaluated": "평가 미실시",
    "no_data": "데이터 부족",
    "-": "-",
}

_AUDIT_HISTORY_LABELS = {
    "not_checked": "미검증 (옵션 비활성)",
    "no_red_flags": "이력 clean",
    "red_flag": "과거 회사 회계 risk 발견",
    "-": "-",
}

_FIVE_YEAR_LABELS = {
    "first_term_or_short": "첫 임기 또는 단기 (5년 룰 통과)",
    "long_tenure_concerns": "장기연임 (5년+, 독립성 훼손)",
    "no_data": "데이터 부족",
    "-": "-",
}

_SUB_FACTOR_LABELS = {
    "major_shareholder_relation": "최대주주 관계",
    "recent_3y_transactions": "최근 3년 거래",
    "recent_2y_employee": "최근 2년 직원 이력",
    "five_year_rule": "5년 임기 룰",
}


def _ind_label(code: str) -> str:
    return _INDEPENDENCE_LABELS.get(code, code)


def _disq_label(code: str) -> str:
    return _DISQUALIFICATION_LABELS.get(code, code)


def _audit_label(code: str) -> str:
    return _AUDIT_HISTORY_LABELS.get(code, code)


def _five_y_label(code: str) -> str:
    return _FIVE_YEAR_LABELS.get(code, code)


def _render_error(payload: dict[str, Any]) -> str:
    lines = [f"# advise_vote: {payload.get('subject', '')}", "", "메모 작성 불가."]
    for w in payload.get("warnings", []):
        lines.append(f"- {w}")
    return "\n".join(lines)


def _render_ambiguous(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    lines = [
        f"# advise_vote: {data.get('query', payload.get('subject', ''))}",
        "",
        "회사 식별 모호.",
        "",
        "| 회사명 | corp_code |",
        "|------|-----------|",
    ]
    for c in data.get("candidates", []):
        lines.append(f"| {c.get('corp_name')} | `{c.get('corp_code')}` |")
    return "\n".join(lines)


def _render(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))} 의결권 행사 메모 (사전)"]
    lines.append("")
    if data.get("scope_all_warning"):
        lines.append(f"> ⚠ **{data['scope_all_warning']}**")
        lines.append("")
    fin_ref = data.get("fin_reference_year")
    fin_ref_note = f" (재무 reference: FY{fin_ref})" if fin_ref else ""
    lines.append(f"- 회차: {data.get('year')}년 {data.get('meeting_type')} 주총{fin_ref_note}")
    lines.append(f"- vote_style: `{data.get('vote_style')}` / 이사 회계 risk 이력 검증: {'활성' if data.get('audit_history_enabled') else '비활성'}")
    lines.append(f"- status: `{payload.get('status')}` / filing_status: `{data.get('filing_status', '-')}`")
    lines.append(f"- 안건: {data.get('agenda_count')} / 후보: {data.get('candidates_count')}")
    lines.append("")

    # 안건별 결정 표 (운용사 보고서 스타일)
    decisions = data.get("agenda_decisions", []) or []
    if decisions:
        lines.append("## 안건별 의결권 행사 결정")
        lines.append("")
        lines.append("| # | 안건 | 카테고리 | 행사방향 | 사유 |")
        lines.append("|---|------|---------|---------|------|")
        for i, ag in enumerate(decisions, 1):
            title = (ag.get("agenda_title") or "")[:60]
            cat = ag.get("agenda_category", "-")
            decision = ag.get("decision", "-")
            reason = (ag.get("reason") or "")[:80]
            decision_emoji = {
                "FOR": "✓ FOR",
                "AGAINST": "✗ AGAINST",
                "REVIEW": "? REVIEW",
                "NO_DATA": "— NO_DATA",
            }.get(decision, decision)
            lines.append(f"| {i} | {title} | `{cat}` | **{decision_emoji}** | {reason} |")
        lines.append("")

        # 안건별 결정 근거 detail (facts + risk + policy citation + 근거 공고)
        lines.append("### 안건별 결정 근거 (사실 + 위험 + 정책 + 출처)")
        lines.append("")
        for i, ag in enumerate(decisions, 1):
            title = (ag.get("agenda_title") or "")[:80]
            facts = ag.get("facts") or {}
            risks = ag.get("risk_factors") or []
            citation = ag.get("policy_citation") or "-"
            policy_basis = ag.get("policy_basis") or "-"
            rcept_no = ag.get("evidence_rcept_no")
            lines.append(f"**{i}. {title}** — {ag.get('decision','-')}")
            if facts:
                fact_str = ", ".join(f"{k}={v}" for k, v in facts.items())
                lines.append(f"- 사실(facts): {fact_str}")
            else:
                lines.append("- 사실(facts): (해당 카테고리에 정량 fact 없음)")
            if risks:
                lines.append(f"- 위험 신호: {', '.join(risks)}")
            else:
                lines.append("- 위험 신호: 없음")
            lines.append(f"- 정책 인용: {citation}")
            lines.append(f"- 적용 정책: {policy_basis}")
            if rcept_no:
                viewer = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}"
                lines.append(f"- 근거 공고: [주주총회소집공고 {rcept_no}]({viewer})")
            lines.append("")

    # 후보 평가 (사외이사/감사위원 위주)
    cands = data.get("candidates_evaluations", []) or []
    if cands:
        lines.append("## 이사/감사 후보 평가")
        lines.append("")
        lines.append("> **판단 framework** — 신임: ① 과거 다른 회사에서의 행적 ② 결격사유 ③ 전문성 ④ 독립성·충실성. 연임: ① 재직 기간 ② 재직 중 회사 운영 성과 (이 회사 데이터 활용).")
        lines.append("")
        lines.append("| 후보 | 직책 | 선임유형 | 임기 | 독립성 | 결격사유 | 이사 회계 risk 이력 | 비고 |")
        lines.append("|------|------|---------|------|--------|---------|-------|------|")
        for c in cands:
            indep_code = c.get("independence", {}).get("summary", "-")
            disq_code = c.get("disqualification", {}).get("summary", "-")
            audit_code = c.get("faithfulness", {}).get("audit_history_check", {}).get("summary", "-")
            action = c.get("agenda_action", "-") or "-"
            five_y_code = ((c.get("independence") or {}).get("sub_factors") or {}).get("five_year_rule", {}).get("result", "-")
            # 비고: independence concerns 시 어떤 sub-factor 위반했는지 한국어로
            note = ""
            if indep_code in ("concerns", "weak_concerns"):
                ind_subs = c.get("independence", {}).get("sub_factors", {})
                concern_kr = [
                    _SUB_FACTOR_LABELS.get(k, k)
                    for k, v in ind_subs.items()
                    if v.get("result") not in ("independent", "no_transactions", "outsider", "first_term_or_short")
                ]
                if concern_kr:
                    note = f"위반: {', '.join(concern_kr)}"
            lines.append(
                f"| {c.get('name', '?')} | {c.get('role_type', '-')} | {action} | {_five_y_label(five_y_code)} | "
                f"{_ind_label(indep_code)} | {_disq_label(disq_code)} | {_audit_label(audit_code)} | {note} |"
            )
        lines.append("")

        # 후보별 detail — 전문성 / 경력 / 과거 회사 행적 raw (framework 적용용)
        lines.append("### 후보별 raw (전문성·경력·추천 사유)")
        lines.append("")
        for c in cands:
            name = c.get("name", "?")
            role = c.get("role_type", "-")
            faith = c.get("faithfulness", {}) or {}
            main_job = faith.get("main_job") or "-"
            rec_reason = (faith.get("recommendation_reason_raw") or "").strip()
            careers = faith.get("career_company_groups") or []
            ah = faith.get("audit_history_check") or {}
            ah_red = ah.get("red_flags") or []

            lines.append(f"**{name}** ({role})")
            lines.append(f"- 주요 직책: {main_job}")
            if rec_reason:
                lines.append(f"- 추천 사유 (raw): {rec_reason[:240]}{'…' if len(rec_reason) > 240 else ''}")
            if careers:
                lines.append("- 경력:")
                for grp in careers[:6]:
                    co = grp.get("company", "?")
                    items = grp.get("items") or []
                    items_str = " / ".join(items[:3])
                    lines.append(f"  - {co} — {items_str}")
            if ah_red:
                lines.append(f"- 과거 회사 회계 risk 이력 (raw): {len(ah_red)}건 발견 — 본문 raw 메모 검토")
            lines.append("")

        # 회계 risk 이력 발견 detail (회사명 / 시점 / risk 유형 raw 노출)
        audit_history_detail = []
        for c in cands:
            rfs = c.get("faithfulness", {}).get("audit_history_check", {}).get("red_flags", []) or []
            for rf in rfs:
                audit_history_detail.append((c.get("name", "?"), rf))
        if audit_history_detail:
            lines.append("### 이사 회계 risk 이력 검증 — 과거 회사 회계 risk overlap (raw)")
            lines.append("> 사외이사 충실의무 단정 X — 사용자 판단 위임. 본 시점에 후보가 그 회사에 재직 중이었음을 의미.")
            lines.append("")
            lines.append("| 후보 | 과거 회사 | 재직 기간 | risk 유형 | 시점 | detail |")
            lines.append("|------|----------|----------|----------|------|--------|")
            for cand_name, rf in audit_history_detail:
                co = rf.get("company", "?")
                tenure = f"{rf.get('tenure_start_year')} ~ {rf.get('tenure_end_year') or '현재'}"
                for r in rf.get("red_flags", []):
                    rtype = r.get("type")
                    yr = r.get("year") or f"{r.get('year_from','?')}→{r.get('year_to','?')}"
                    detail = ""
                    if rtype == "non_clean_audit_opinion":
                        detail = r.get("opinion", "")
                    elif rtype == "capital_impairment_full":
                        detail = f"잠식률 {r.get('ratio_pct')}%"
                    elif rtype == "loss_continued_worsening":
                        detail = f"순이익 {r.get('ni_from'):,} → {r.get('ni_to'):,}"
                    elif rtype == "leverage_surge_op_worsening":
                        detail = f"부채 +{r.get('debt_growth_pct')}% / 영업이익 {r.get('op_from'):,} → {r.get('op_to'):,}"
                    lines.append(f"| {cand_name} | {co} | {tenure} | `{rtype}` | {yr} | {detail} |")
            lines.append("")

    # 회사 펀더멘털 요약 (참고)
    fin = data.get("financial_summary") or {}
    if fin:
        lines.append("## 회사 펀더멘털 (참고)")
        lines.append(f"- 매출액: {fin.get('revenue_krw') or '-'} / 영업이익: {fin.get('operating_profit_krw') or '-'}")
        lines.append(f"- ROE: {fin.get('roe_pct') or '-'}% / 부채비율: {fin.get('debt_ratio_pct') or '-'}%")
        lines.append(f"- 자본잠식 상태: {fin.get('capital_impairment_status') or '-'}")
        lines.append("")

    # Evidence
    refs = payload.get("evidence_refs", []) or []
    if refs:
        lines.append("## Evidence (근거)")
        for r in refs[:5]:
            url = r.get("viewer_url") or "-"
            lines.append(f"- {r.get('section', '-')}: [{r.get('rcept_no', '-')}]({url}) — {r.get('note', '')}")
        lines.append("")

    # 추가 분석 영역 — 짧게. 사용자가 자연스럽게 후속 질문 유도 (도구는 Claude가 알아서 매칭)
    decisions_local = data.get("agenda_decisions", []) or []
    has_director = any(ad.get("agenda_category") in ("director_election", "audit_committee_election") for ad in decisions_local)
    topics: list[str] = ["배당", "지분 구조·행동주의", "가치제고 plan", "운용사별 정책 비교", "재무 detail"]
    if has_director:
        topics.append("후보 회계 risk 이력 (`check_audit_history=True`)")

    lines.append("---")
    lines.append(f"_더 보고 싶은 영역: {' · '.join(topics)} — 이어서 물어보시면 영역별로 더 자세히 분석합니다._")

    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def proxy_advise_before_meeting(
        company: str,
        year: int = 0,
        meeting_type: str = "annual",
        vote_style: str = "open_proxy",
        check_audit_history: bool = False,
        format: str = "md",
    ) -> str:
        """desc: 주총 **소집 전** 안건별 의결권 권고. **1회 호출**로 핵심 모두: 안건별 FOR/AGAINST/REVIEW/NO_DATA + facts + risk_factors + policy_citation + 근거 공고 + 후보 평가 (전문성·경력·추천사유) + 재무/거버넌스 summary.
        when: 주총 소집공고 후 ~ 주총 직전. 의결권 행사 결정 + 내부 보고용. 사후 결과는 `proxy_result_after_meeting` 별도.
        rule: 운용사 의결권 행사 보고서 스타일. hard-fail 항목 (형사 처벌 / 사적 관계 / 동명이인) 메모에서 침묵. 자동 검증 가능 항목만 표기. soft-fail 항목 (후보 약력 / 정관 본문) raw 노출 — LLM이 자연어 판단.
        vote_style: open_proxy (default OPM 자체 정책) / mirae_asset / samsung / samsung_active / kim / truston / align_partners / cha_partners / baring / nps (국민연금)
        check_audit_history: True 시 후보 과거 회사 × 재직 기간 × 회계 risk overlap 자동 cross-check (추가 DART 호출, +30s)
        meeting_type: annual (default 정기) / extraordinary (임시) / auto (본문 자동 detect)
        ref: 영역별 raw — shareholder_meeting_notice (안건/이사후보) / financial_metrics (재무 51 지표) / corp_gov_report (거버넌스) / ownership_structure (지분) / proxy_contest (분쟁) / value_up (가치제고). 사후 결과: proxy_result_after_meeting
        """
        payload = await build_proxy_advise_payload(
            company,
            year=year or None,
            meeting_type=meeting_type,
            vote_style=vote_style,
            scope="decisions",  # 단일 scope — 모든 specialized scope 폐지 (각 tool 직접 호출 권장)
            check_audit_history=check_audit_history,
        )
        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") == "ambiguous":
            return _render_ambiguous(payload)
        if payload.get("status") == "error":
            return _render_error(payload)
        return _render(payload)
