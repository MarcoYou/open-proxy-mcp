"""v2 value_up public tool."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.value_up_v2 import build_value_up_payload


def _render_error(payload: dict[str, Any]) -> str:
    lines = [f"# value_up: {payload.get('subject', '')}", "", "밸류업 공시를 확정하지 못했다."]
    for warning in payload.get("warnings", []):
        lines.append(f"- {warning}")
    return "\n".join(lines)


def _render_ambiguous(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    lines = [f"# value_up: {data.get('query', payload.get('subject', ''))}", "", "회사 식별이 애매해 밸류업 공시를 자동 선택하지 않았다.", "", "| 회사명 | ticker | corp_code | company_id |", "|------|--------|-----------|------------|"]
    for item in data.get("candidates", []):
        lines.append(f"| {item['corp_name']} | `{item['ticker']}` | `{item['corp_code']}` | `{item['company_id']}` |")
    return "\n".join(lines)


def _render(payload: dict[str, Any], scope: str) -> str:
    data = payload.get("data", {})
    latest = data.get("latest", {})
    window = data.get("window", {})
    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))} 밸류업", ""]
    lines.append(f"- company_id: `{data.get('company_id', '')}`")
    lines.append(f"- status: `{payload.get('status', '')}`")
    if data.get("availability_status"):
        lines.append(f"- availability_status: `{data.get('availability_status', '')}`")
    if window:
        lines.append(f"- 조사 구간: `{window.get('start_date', '')}` ~ `{window.get('end_date', '')}`")
    lines.append("")
    if payload.get("warnings"):
        lines.append("## 유의사항")
        for warning in payload["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")

    if data.get("no_filing"):
        lines.extend([
            "## 공시 없음",
            "- 조사 구간 내 기업가치제고(밸류업) 공시 없음 (정상 NO_FILING).",
            "",
        ])

    if latest:
        lines.append("## 최신 공시")
        lines.append(f"- 공시일: {latest.get('disclosure_date', '-')}")
        lines.append(f"- 공시명: {latest.get('report_name', '-')}")
        if latest.get("category"):
            lines.append(f"- 카테고리: `{latest.get('category')}`")
        if latest.get("plan_title"):
            lines.append(f"- 계획서 명칭: {latest.get('plan_title')}")
        lines.append(f"- 소스: `{latest.get('source_type', '-')}`")
        if latest.get("rcept_no"):
            lines.append(f"- rcept_no: `{latest.get('rcept_no', '')}`")
        if latest.get("acptno"):
            lines.append(f"- KIND acptno: `{latest.get('acptno', '')}`")

    latest_plan = data.get("latest_plan")
    if latest_plan:
        lines.append("")
        lines.append("## 본계획")
        lines.append(f"- 공시일: {latest_plan.get('disclosure_date', '-')}")
        lines.append(f"- 공시명: {latest_plan.get('report_name', '-')}")
        lines.append(f"- 카테고리: `{latest_plan.get('category', '-')}`")
        if latest_plan.get("plan_title"):
            lines.append(f"- 계획서 명칭: {latest_plan.get('plan_title')}")
        if latest_plan.get("rcept_no"):
            lines.append(f"- rcept_no: `{latest_plan.get('rcept_no', '')}`")
        if latest_plan.get("note"):
            lines.append(f"- note: {latest_plan.get('note')}")
    latest_status = data.get("latest_status")
    if latest_status:
        lines.append("")
        lines.append("## 최신 이행현황")
        lines.append(f"- 공시일: {latest_status.get('disclosure_date', '-')}")
        lines.append(f"- 공시명: {latest_status.get('report_name', '-')}")
        lines.append(f"- 카테고리: `{latest_status.get('category', '-')}`")
        if latest_status.get("plan_title"):
            lines.append(f"- 계획서 명칭: {latest_status.get('plan_title')}")
        if latest_status.get("rcept_no"):
            lines.append(f"- rcept_no: `{latest_status.get('rcept_no', '')}`")
        if latest_status.get("note"):
            lines.append(f"- note: {latest_status.get('note')}")

    latest_result = data.get("latest_result")
    if latest_result:
        lines.append("")
        lines.append("## 이행결과")
        lines.append(f"- 공시일: {latest_result.get('disclosure_date', '-')}")
        lines.append(f"- 공시명: {latest_result.get('report_name', '-')}")
        if latest_result.get("plan_title"):
            lines.append(f"- 계획서 명칭: {latest_result.get('plan_title')}")
        for section in latest_result.get("implementation_sections", [])[:5]:
            lines.append(f"- `{section.get('tag', '')}` {section.get('text', '')}")

    meta_amendment = data.get("meta_amendment")
    if meta_amendment:
        lines.append("")
        lines.append("## 메타/재공시")
        lines.append(f"- 공시일: {meta_amendment.get('disclosure_date', '-')}")
        lines.append(f"- 공시명: {meta_amendment.get('report_name', '-')}")
        lines.append(f"- note: {meta_amendment.get('note', '')}")

    if not latest_plan and not latest_status:
        diagnostic = data.get("search_diagnostics", {}).get("diagnostic_window", {})
        sample_filings = diagnostic.get("sample_filings", [])
        if sample_filings:
            lines.extend(["## 진단 구간에서 확인된 관련 공시", "| 소스 | 날짜 | 공시명 | 식별자 |", "|------|------|--------|--------|"])
            for item in sample_filings:
                filing_id = item.get("rcept_no") or item.get("acptno", "")
                lines.append(
                    f"| {item.get('source', '')} | {item.get('disclosure_date', '')} | {item.get('report_name', '')} | `{filing_id}` |"
                )

    if scope in {"summary", "timeline"}:
        lines.extend(["", "## 공시 타임라인", "| 날짜 | 공시명 | 제출인 | rcept_no |", "|------|--------|--------|----------|"])
        for item in data.get("items", []):
            filing_id = item.get("rcept_no") or item.get("acptno", "")
            lines.append(f"| {item.get('disclosure_date', '')} | {item.get('report_name', '')} | {item.get('filer_name', '')} | `{filing_id}` |")

    if scope in {"summary", "plan", "commitments"}:
        lines.extend(["", "## 핵심 문장"])
        for item in data.get("highlights", []):
            lines.append(f"- {item}")

    sections = data.get("implementation_sections") or []
    if sections and scope in {"summary", "plan", "commitments"}:
        lines.extend(["", "## 이행 태그"])
        for section in sections[:12]:
            lines.append(f"- `{section.get('tag', '')}` {section.get('text', '')}")

    embedded = data.get("embedded_results") or []
    if embedded:
        lines.extend(["", "## 재공시 내 업데이트 결과"])
        for section in embedded[:8]:
            lines.append(f"- `{section.get('tag', '')}` {section.get('text', '')}")

    if scope == "plan":
        lines.extend(["", "## 원문 발췌", "```", data.get("latest_excerpt", "")[:1800], "```"])

    cross = data.get("treasury_cross_ref")
    if cross:
        lines.extend(["", "## 자사주 이행 교차참조 (최근 24개월)"])
        lines.append(f"- 자기주식 소각결정 공시: {cross.get('cancelation_decision_count_24m', 0)}건")
        lines.append(f"- 취득결정 공시: {cross.get('acquisition_count_24m', 0)}건 (소각 목적 {cross.get('acquisition_for_cancelation_count_24m', 0)}건)")
        amt = cross.get("acquisition_for_cancelation_amount_krw_24m", 0)
        if amt:
            lines.append(f"- 소각 목적 취득 총액: {amt:,}원")
        lines.append(f"- 신탁계약 체결: {cross.get('trust_contract_count_24m', 0)}건")
        lines.append(f"- 상세: `treasury_share(scope=\"cancelation\")` 또는 `scope=\"acquisition\"`")

    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def value_up(
        company: str,
        scope: str = "summary",
        year: int = 0,
        start_date: str = "",
        end_date: str = "",
        format: str = "md",
    ) -> str:
        """desc: 기업가치제고계획(밸류업) 공시 + commitment 문장. 주주환원 **정책·미래 약속**. 자사주 소각 이행 교차참조 포함.
        when: 밸류업 계획, ROE/PBR/배당성향 목표, 자사주 소각 계획 등 미래 약속. 실제 배당은 `dividend`, 자사주 사실은 `treasury_share`.
        rule: DART I 밸류업 키워드 → 없으면 KIND 0184 fallback. 공시 카테고리: plan/progress/meta_amendment(고배당기업 재공시). 최신이 meta_amendment면 실계획 본문을 latest_plan으로 별도. summary/commitments에 24개월 자사주 이벤트 treasury_cross_ref 포함.
        scope: `summary` / `plan` 원문 발췌 / `commitments` 핵심 약속+이행 교차참조 / `timeline` 공시 이력
        ref: dividend, treasury_share, ownership_structure, company, evidence
        """
        payload = await build_value_up_payload(
            company,
            scope=scope,
            year=year or None,
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
