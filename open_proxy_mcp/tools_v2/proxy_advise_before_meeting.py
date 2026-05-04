"""proxy_advise_before_meeting — 주총 전 의결권 행사 메모 (운용사 보고서 스타일)."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.proxy_advise import build_proxy_advise_payload
from open_proxy_mcp.services.contracts import as_pretty_json


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
    lines.append(f"- 회차: {data.get('year')}년 {data.get('meeting_type')} 주총")
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
            decision_emoji = {"FOR": "✓ FOR", "AGAINST": "✗ AGAINST", "REVIEW": "? REVIEW"}.get(decision, decision)
            lines.append(f"| {i} | {title} | `{cat}` | **{decision_emoji}** | {reason} |")
        lines.append("")

    # 후보 평가 (사외이사/감사위원 위주)
    cands = data.get("candidates_evaluations", []) or []
    if cands:
        lines.append("## 이사/감사 후보 평가")
        lines.append("")
        lines.append("| 후보 | 직책 | 독립성 | 결격사유 | 이사 회계 risk 이력 | 비고 |")
        lines.append("|------|------|--------|---------|-------|------|")
        for c in cands:
            indep = c.get("independence", {}).get("summary", "-")
            disq = c.get("disqualification", {}).get("summary", "-")
            audit_history = c.get("faithfulness", {}).get("audit_history_check", {}).get("summary", "-")
            note = ""
            if indep == "concerns":
                ind_subs = c.get("independence", {}).get("sub_factors", {})
                concern_factors = [k for k, v in ind_subs.items() if v.get("result") not in ("independent", "no_transactions", "outsider", "first_term_or_short")]
                note = f"독립성 우려: {', '.join(concern_factors)}"
            lines.append(f"| {c.get('name', '?')} | {c.get('role_type', '-')} | {indep} | {disq} | {audit_history} | {note} |")
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

    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def proxy_advise_before_meeting(
        company: str,
        year: int = 0,
        meeting_type: str = "annual",
        vote_style: str = "open_proxy",
        scope: str = "decisions",
        check_audit_history: bool = False,
        format: str = "md",
    ) -> str:
        """desc: 주총 **소집 전** 다각도 심층 분석 + 안건별 의결권 권고. 10 scope (decisions/agenda/candidates/financial/governance/ownership/policy_basis/proxy_battle/engagement/evidence/all). 안건별 FOR/AGAINST/REVIEW + 1-2문장 결정 사유 (정책 근거 + 사실 근거 + rcept_no). 사외이사 후보 3축 (독립성 / 충실성 / 결격사유) 자동 평가.
        when: 주총 소집공고 후 ~ 주총 직전. 의결권 행사 결정 + 내부 보고용. proxy_result_after_meeting는 주총 후 결과 보고용 별도.
        rule: 운용사 의결권 행사 보고서 스타일 (회사명 / 주총일 / 안건별 표). hard-fail 항목 (형사 처벌 / 사적 관계 / 동명이인 등) 메모에서 침묵. 자동 검증 가능 항목만 표기. soft-fail 항목 (후보 약력 자유 텍스트 / 정관 본문) raw 노출 — LLM이 자연어로 추가 판단.
        vote_style: open_proxy (default OPM 자체 정책) / mirae_asset / samsung / samsung_active / kim / truston / align_partners / cha_partners / baring / nps (국민연금).
        scope: decisions (default, 안건별 결정) / agenda (안건 트리) / candidates (후보 평가 raw) / financial (재무 진단) / governance (거버넌스 15지표) / ownership (지배구조) / all (모두 통합). policy_basis/proxy_battle/engagement/evidence는 별도 commit.
        check_audit_history: True 시 후보의 과거 회사 × 재직 기간 × 회계 risk overlap 자동 cross-check (추가 DART 호출 발생).
        ref: shareholder_meeting / ownership_structure / corp_gov_report / financial_metrics / proxy_guideline / director_evaluation, proxy_result_after_meeting (사후)
        """
        payload = await build_proxy_advise_payload(
            company,
            year=year or None,
            meeting_type=meeting_type,
            vote_style=vote_style,
            scope=scope,
            check_audit_history=check_audit_history,
        )
        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") == "ambiguous":
            return _render_ambiguous(payload)
        if payload.get("status") == "error":
            return _render_error(payload)
        return _render(payload)
