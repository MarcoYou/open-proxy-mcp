"""recap_vote_after_meeting — 주총 후 결과 보고."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.recap_vote import build_recap_vote_payload


def _render_error(payload: dict[str, Any]) -> str:
    return f"# recap_vote: {payload.get('subject', '')}\n\n결과 보고 작성 불가.\n" + "\n".join(f"- {w}" for w in payload.get("warnings", []))


def _render_ambiguous(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    lines = [f"# recap_vote: {data.get('query', '')}", "", "회사 식별 모호.", "", "| 회사명 | corp_code |", "|------|-----------|"]
    for c in data.get("candidates", []):
        lines.append(f"| {c.get('corp_name')} | `{c.get('corp_code')}` |")
    return "\n".join(lines)


def _render(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))} 의결권 행사 결과 보고 (사후)"]
    lines.append("")
    lines.append(f"- 회차: {data.get('year')}년 {data.get('meeting_type')} 주총")
    lines.append(f"- 주총일: {data.get('meeting_date', '-')}")
    lines.append(f"- vote_style: `{data.get('vote_style')}`")
    lines.append(f"- status: `{payload.get('status')}` / filing_status: `{data.get('filing_status', '-')}`")
    lines.append("")

    # 안건별 결과 표
    results = data.get("agenda_results", []) or []
    if results:
        lines.append("## 안건별 의결 결과")
        lines.append("")
        lines.append("| # | 안건 | 결과 | 찬성 % | 반대 % | 출석률 |")
        lines.append("|---|------|------|--------|--------|--------|")
        for i, r in enumerate(results[:30], 1):
            title = (r.get("agenda_title") or r.get("title") or "")[:60]
            outcome = r.get("outcome") or r.get("decision") or "-"
            for_pct = r.get("for_pct") or r.get("agree_pct") or "-"
            against_pct = r.get("against_pct") or r.get("disagree_pct") or "-"
            attendance = r.get("attendance_pct") or r.get("turnout_pct") or "-"
            lines.append(f"| {i} | {title} | {outcome} | {for_pct} | {against_pct} | {attendance} |")
        lines.append("")
    else:
        lines.append("## 안건별 의결 결과")
        lines.append("- (KIND 결과 데이터 미수집 또는 주총 미공개)")
        lines.append("")

    # 위임장 분쟁
    pc = data.get("proxy_contest_summary")
    if pc:
        lines.append("## 위임장 경쟁")
        lines.append(f"- {pc}")
        lines.append("")

    # 후속 공시 (주총 직후 N일)
    fu = data.get("followup_disclosures") or {}
    fu_window = data.get("follow_up_window", {})
    if fu:
        lines.append(f"## 주총 직후 후속 공시 ({fu_window.get('start', '-')} ~ {fu_window.get('end', '-')})")
        for k, v in fu.items():
            label = v.get("label", k)
            count = v.get("filing_count", 0)
            no_filing = v.get("no_filing", True)
            mark = "✓" if (count and not no_filing) else "—"
            lines.append(f"- {mark} {label}: {count}건")
        lines.append("")

    # 거버넌스 변화
    gov = data.get("governance_summary")
    if gov:
        lines.append("## 거버넌스 변화")
        lines.append(f"- {gov}")
        lines.append("")

    # Evidence
    refs = payload.get("evidence_refs", []) or []
    if refs:
        lines.append("## Evidence")
        for r in refs[:5]:
            url = r.get("viewer_url") or "-"
            lines.append(f"- {r.get('section', '-')}: [{r.get('rcept_no', '-')}]({url}) — {r.get('note', '')}")

    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def recap_vote_after_meeting(
        company: str,
        year: int = 0,
        meeting_type: str = "annual",
        vote_style: str = "open_proxy",
        follow_up_days: int = 30,
        format: str = "md",
    ) -> str:
        """desc: 주총 **후** 의결권 행사 결과 보고 (운용사 분기 보고서 스타일). 5 upstream — 주총 결과 (KIND) + 위임장 결과 + 후속 공시 4종 + 거버넌스 변화. 안건별 가결/부결/찬반율/출석률 + OPM 정책상 행사 사유 (gap 비교 X).
        when: 주총 종료 후. 사후 결과 보고, 후속 공시 cross-link. 사전 추천은 advise_vote_before_meeting (별도).
        rule: 사전 추천 vs 실제 결과 비교 (gap) X — 운용사 보고서는 이미 행사한 결정 + 사유만 기록. 후속 공시 (배당/자사주/재편/희석) 주총 직후 30일 (`follow_up_days` 옵션) 윈도우.
        ref: shareholder_meeting (results) / proxy_contest / dividend / treasury_share / corporate_restructuring / dilutive_issuance / corp_gov_report, advise_vote_before_meeting (사전)
        """
        payload = await build_recap_vote_payload(
            company,
            year=year or None,
            meeting_type=meeting_type,
            vote_style=vote_style,
            follow_up_days=follow_up_days,
        )
        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") == "ambiguous":
            return _render_ambiguous(payload)
        if payload.get("status") == "error":
            return _render_error(payload)
        return _render(payload)
