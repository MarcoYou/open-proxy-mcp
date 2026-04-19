"""v2 ownership_structure public tool."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.ownership_structure import build_ownership_structure_payload


def _render_error(payload: dict[str, Any]) -> str:
    lines = [f"# ownership_structure: {payload.get('subject', '')}", "", "지분 구조를 확정하지 못했다."]
    for warning in payload.get("warnings", []):
        lines.append(f"- {warning}")
    return "\n".join(lines)


def _render_ambiguous(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    lines = [
        f"# ownership_structure: {data.get('query', payload.get('subject', ''))}",
        "",
        "회사 식별이 애매해 지분 구조를 자동 선택하지 않았다.",
        "",
        "| 회사명 | ticker | corp_code | company_id |",
        "|------|--------|-----------|------------|",
    ]
    for item in data.get("candidates", []):
        lines.append(
            f"| {item.get('corp_name', '')} | `{item.get('ticker', '')}` | `{item.get('corp_code', '')}` | `{item.get('company_id', '')}` |"
        )
    return "\n".join(lines)


def _render(payload: dict[str, Any], scope: str) -> str:
    data = payload.get("data", {})
    summary = data.get("summary", {})
    window = data.get("window", {})
    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))} 지분 구조", ""]
    lines.append(f"- company_id: `{data.get('company_id', '')}`")
    lines.append(f"- status: `{payload.get('status', '')}`")
    if data.get("as_of_date"):
        lines.append(f"- as_of_date: `{data.get('as_of_date', '')}`")
    if window:
        lines.append(f"- 조사 구간: `{window.get('start_date', '')}` ~ `{window.get('end_date', '')}`")
    lines.append("")
    if payload.get("warnings"):
        lines.append("## 유의사항")
        for warning in payload["warnings"]:
            lines.append(f"- {warning}")
        lines.append("")

    if scope == "summary":
        top = summary.get("top_holder") or {}
        lines.append("## 요약")
        lines.append(f"- 최대주주: {top.get('name', '-') or '-'} {top.get('ownership_pct', 0):.2f}%")
        lines.append(f"- 특수관계인 합계: {summary.get('related_total_pct', 0):.2f}%")
        lines.append(f"- 자사주: {summary.get('treasury_shares', 0):,}주 ({summary.get('treasury_pct', 0):.2f}%)")
        lines.append(f"- 능동적 5% 시그널: {summary.get('active_signal_count', 0)}건")

    if scope in {"summary", "major_holders", "control_map"}:
        lines.extend(["", "## 최대주주/특수관계인", "| 이름 | 관계 | 지분율 | 보유주식수 |", "|------|------|--------|-----------|"])
        for row in data.get("major_holders", [])[:20]:
            lines.append(f"| {row['name']} | {row['relation'] or '-'} | {row['ownership_pct']:.2f}% | {row['shares']:,} |")

    if scope in {"summary", "blocks", "control_map"}:
        lines.extend(["", "## 5% 대량보유 최신", "| 보고자 | 지분율 | 보유목적 | 날짜 | rcept_no |", "|--------|--------|----------|------|----------|"])
        for row in data.get("blocks", [])[:15]:
            lines.append(f"| {row['reporter']} | {row['ownership_pct']:.2f}% | {row['purpose']} | {row['report_date']} | `{row['rcept_no']}` |")

    if scope in {"summary", "treasury"}:
        treasury = data.get("treasury", {})
        lines.extend(["", "## 자사주", f"- 발행주식수: {treasury.get('issued_shares', 0):,}주", f"- 자사주: {treasury.get('treasury_shares', 0):,}주", f"- 자사주 비중: {treasury.get('treasury_pct', 0):.2f}%"])

    if scope == "timeline":
        lines.extend(["", "## 지분 변화 타임라인", "| 날짜 | 보고자 | 지분율 | 목적 | rcept_no |", "|------|--------|--------|------|----------|"])
        for row in data.get("timeline", [])[:30]:
            lines.append(f"| {row['report_date']} | {row['reporter']} | {row['ownership_pct']:.2f}% | {row['purpose']} | `{row['rcept_no']}` |")

    if scope == "control_map":
        control_map = data.get("control_map", {})
        core = control_map.get("core_holder_block", {})
        top = core.get("top_holder") or {}
        treasury = control_map.get("treasury_block", {})
        flags = control_map.get("flags", {})

        lines.extend([
            "",
            "## control_map 요약",
            f"- 명부상 최대주주: {top.get('name', '-') or '-'} {top.get('ownership_pct', 0):.2f}%",
            f"- 명부상 특수관계인 합계: {core.get('related_total_pct', 0):.2f}%",
            f"- 자사주: {treasury.get('shares', 0):,}주 ({treasury.get('pct', 0):.2f}%)",
            f"- 비중 플래그: 50% 이상={flags.get('registry_majority', False)}, 30% 이상={flags.get('registry_over_30pct', False)}, 자사주 5% 이상={flags.get('treasury_over_5pct', False)}",
        ])

        observations = control_map.get("observations", [])
        if observations:
            lines.extend(["", "## 관찰 포인트"])
            for item in observations:
                lines.append(f"- {item}")

        lines.extend(["", "## 명부와 겹치지 않는 능동적 5% 블록", "| 보고자 | 지분율 | 목적 | 날짜 |", "|--------|--------|------|------|"])
        active_non_overlap_blocks = control_map.get("active_non_overlap_blocks", [])
        if active_non_overlap_blocks:
            for row in active_non_overlap_blocks[:10]:
                lines.append(f"| {row['reporter']} | {row['ownership_pct']:.2f}% | {row['purpose']} | {row['report_date']} |")
        else:
            lines.append("| - | - | - | - |")

        lines.extend(["", "## 명부와 이름이 겹치는 5% 블록", "| 보고자 | 지분율 | 목적 | 명부상 이름 | 날짜 |", "|--------|--------|------|-------------|------|"])
        overlap_blocks = control_map.get("overlap_blocks", [])
        if overlap_blocks:
            for row in overlap_blocks[:10]:
                lines.append(
                    f"| {row['reporter']} | {row['ownership_pct']:.2f}% | {row['purpose']} | {row.get('matched_major_holder') or '-'} | {row['report_date']} |"
                )
        else:
            lines.append("| - | - | - | - | - |")

        notes = control_map.get("notes", [])
        if notes:
            lines.extend(["", "## 해석 유의사항"])
            for note in notes:
                lines.append(f"- {note}")

    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def ownership_structure(
        company: str,
        scope: str = "summary",
        year: int = 0,
        as_of_date: str = "",
        start_date: str = "",
        end_date: str = "",
        format: str = "md",
    ) -> str:
        """desc: 최대주주·특수관계인·5% 대량보유·자사주를 한 탭에서 보는 지분 구조 tool. 판의 구조(who holds what)를 그린다.
        when: 지배력 구조, 최대주주 비중, 특수관계인 지분 합, 자사주 규모, 5% 활성 시그널을 보고 싶을 때.
        rule: 사업보고서 기반 DART 공식 API 우선. 5% 대량보유 목적은 최신 원문(document.xml)으로 보강. KIND 비사용 (false match 위험). partial match 자동 확정 안 함.
        scope: `summary`(기본) / `major_holders`(최대주주+특수관계인) / `blocks`(5% 대량보유 최신) / `treasury`(자사주) / `control_map`(3대 카테고리 정리: 명부 등재/외부 능동/수동) / `timeline`(5% 보고 이력).
        ref: company, proxy_contest (분쟁 맥락), evidence
        """
        payload = await build_ownership_structure_payload(
            company,
            scope=scope,
            year=year or None,
            as_of_date=as_of_date,
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
