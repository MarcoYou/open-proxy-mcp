"""v2 company public tool."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.company import build_company_payload
from open_proxy_mcp.services.contracts import as_pretty_json


def _render_error(payload: dict[str, Any]) -> str:
    warnings = payload.get("warnings", [])
    lines = [
        f"# company: {payload.get('subject', '')}",
        "",
        "회사 식별에 실패했다.",
    ]
    if warnings:
        lines.append("")
        for warning in warnings:
            lines.append(f"- {warning}")
    return "\n".join(lines)


def _render_candidates(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    candidates = data.get("candidates", [])
    lines = [
        f"# company: {data.get('query', payload.get('subject', ''))}",
        "",
        "동일하거나 유사한 회사명이 여러 개라 자동 선택을 하지 않았다.",
        "",
        "| 회사명 | ticker | corp_code | company_id | modify_date |",
        "|------|--------|-----------|------------|-------------|",
    ]
    for item in candidates:
        lines.append(
            f"| {item.get('corp_name', '')} | `{item.get('ticker', '')}` | "
            f"`{item.get('corp_code', '')}` | `{item.get('company_id', '')}` | {item.get('modify_date', '')} |"
        )
    lines.extend([
        "",
        "다음 단계:",
        "- ticker 또는 corp_code를 직접 넣어 다시 조회",
    ])
    return "\n".join(lines)


def _render_exact(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    identifiers = data.get("identifiers", {})
    classification = data.get("classification", {})
    names = data.get("names", {})
    basic_info = data.get("basic_info", {})
    filings = data.get("recent_filings", [])
    filings_window = data.get("recent_filings_window", {})
    warnings = payload.get("warnings", [])

    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))}"]
    if names.get("en"):
        lines.append(f"*{names['en']}*")
    lines.append("")
    lines.append(f"- company_id: `{data.get('company_id', '')}`")
    lines.append(f"- status: `{payload.get('status', '')}`")
    lines.append("")

    if warnings:
        lines.append("## 유의사항")
        for warning in warnings:
            lines.append(f"- {warning}")
        lines.append("")

    lines.extend([
        "## 식별자",
        "| 항목 | 값 |",
        "|------|----|",
        f"| ticker | `{identifiers.get('ticker', '')}` |" if identifiers.get("ticker") else "| ticker | 비상장/미상 |",
        f"| corp_code | `{identifiers.get('corp_code', '')}` |",
        f"| ISIN | `{identifiers.get('isin', '')}` |" if identifiers.get("isin") else "| ISIN | 아직 미연결 |",
        f"| 법인등록번호 | `{identifiers.get('jurir_no', '')}` |" if identifiers.get("jurir_no") else "| 법인등록번호 | - |",
        f"| 사업자번호 | `{identifiers.get('bizr_no', '')}` |" if identifiers.get("bizr_no") else "| 사업자번호 | - |",
        "",
        "## 분류",
        "| 항목 | 값 |",
        "|------|----|",
        f"| 시장 | {classification.get('market', '') or '-'} |",
        f"| 업종 | {classification.get('sector_name', '') or '-'} |",
        f"| 업종코드(DART) | {classification.get('induty_code', '') or '-'} |",
        f"| 결산월 | {classification.get('fiscal_month', '') + '월' if classification.get('fiscal_month') else '-'} |",
        "",
        "## 기본정보",
        "| 항목 | 값 |",
        "|------|----|",
        f"| 대표이사 | {basic_info.get('ceo_name', '') or '-'} |",
        f"| 설립일 | {basic_info.get('established_date', '') or '-'} |",
        f"| 주소 | {basic_info.get('address', '') or '-'} |",
        f"| 홈페이지 | {basic_info.get('homepage', '') or '-'} |",
        "",
    ])

    aliases = names.get("aliases") or []
    if aliases:
        lines.append("## 별칭")
        lines.append(", ".join(f"`{alias}`" for alias in aliases))
        lines.append("")

    lines.extend([
        "## 최근 공시 인덱스",
        f"- 조사 구간: {filings_window.get('start_date', '-') } ~ {filings_window.get('end_date', '-')}",
        "| 날짜 | 분류 | 공시명 | 제출인 | rcept_no |",
        "|------|------|--------|--------|----------|",
    ])
    for item in filings:
        lines.append(
            f"| {item.get('disclosure_date', '')} | {item.get('filing_type', '')} | "
            f"{item.get('report_name', '')} | {item.get('filer_name', '')} | `{item.get('rcept_no', '')}` |"
        )
    if not filings:
        lines.append("| - | - | 최근 공시 없음 | - | - |")
    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def company(
        query: str,
        max_recent_filings: int = 10,
        start_date: str = "",
        end_date: str = "",
        format: str = "md",
    ) -> str:
        """desc: 기업 식별 + 최근 공시 인덱스 허브. 모든 v2 data tool의 공통 입구. 회사명/ticker/corp_code → 시장·업종·최근 공시 확인.
        when: 회사명으로 검색 시작 → ticker/corp_code 확정 → 후속 tool에 넘길 때. 또는 최근 공시 종류/빈도만 빠르게 훑을 때.
        rule: **비상장 법인 자동 제외** (OPM은 상장사 전용). exact 매치 아니면 자동 확정 안 함, ambiguous로 후보 반환. NAVER는 업종·섹터 보강 보조 소스로만 사용, DART 공식값 덮어쓰기 금지.
        params: query(회사명/ticker/corp_code), max_recent_filings(1-20), start_date/end_date(YYYYMMDD).
        ref: shareholder_meeting, ownership_structure, dividend, proxy_contest, value_up
        """
        payload = await build_company_payload(
            query,
            max_recent_filings=max(1, min(max_recent_filings, 20)),
            start_date=start_date,
            end_date=end_date,
        )

        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") == "exact":
            return _render_exact(payload)
        if payload.get("status") == "error":
            return _render_error(payload)
        return _render_candidates(payload)
