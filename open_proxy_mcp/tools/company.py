"""company public tool."""

from __future__ import annotations

import re
from typing import Any

from open_proxy_mcp.services.company import build_company_payload
from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.tools._shared import company_id_line


def _english_response(payload: dict[str, Any]) -> bool:
    data = payload.get("data", {})
    if data.get("display_language"):
        return data["display_language"] == "en"
    resolution = data.get("company_resolution", {})
    if resolution.get("response_language"):
        return resolution["response_language"] == "en"
    query = str(data.get("query") or payload.get("subject") or "")
    return bool(re.search(r"[A-Za-z]", query)) and not bool(re.search(r"[가-힣]", query))


def _render_error(payload: dict[str, Any]) -> str:
    warnings = payload.get("warnings", [])
    english = _english_response(payload)
    lines = [
        f"# company: {payload.get('subject', '')}",
        "",
        "No company found." if english else "회사를 찾지 못했습니다.",
    ]
    if warnings:
        lines.append("")
        for warning in warnings:
            lines.append(f"- {warning}")
    # 못 찾았으면 끝내지 말고 근접 후보를 보여준다 — 개명·상장폐지·접미가 붙은 상호를
    # 사용자가 알아보고 고를 수 있다. 자동 선택은 하지 않는다(앞자르기 자동선택은 오답).
    cands = (payload.get("data") or {}).get("candidates") or payload.get("candidates") or []
    if cands:
        lines.append("")
        lines.append("Did you mean one of these?" if english else "혹시 이 회사인가요?")
        lines.append("")
        lines.append("| 회사명 | ticker | corp_code |")
        lines.append("|------|--------|-----------|")
        for c in cands[:5]:
            lines.append(
                f"| {c.get('corp_name', '')} | `{c.get('stock_code', '')}` | `{c.get('corp_code', '')}` |"
            )
        lines.append("")
        lines.append("맞는 회사가 없으면 ticker(6자리)나 corp_code(8자리)로 다시 물어보세요."
                     if not english else
                     "If none match, retry with a 6-digit ticker or 8-digit corp_code.")
    return "\n".join(lines)


def _render_candidates(payload: dict[str, Any]) -> str:
    data = payload.get("data", {})
    candidates = data.get("candidates", [])
    english = _english_response(payload)
    lines = [
        f"# company: {data.get('query', payload.get('subject', ''))}",
        "",
        "Multiple matches, ranked by likelihood." if english else "후보가 여러 개입니다. 가능성 높은 순서로 표시합니다.",
        "",
        "| Korean name | English name | ticker | corp_code | company_id |" if english else "| 회사명 | English name | ticker | corp_code | company_id |",
        "|------|--------------|--------|-----------|------------|",
    ]
    for item in candidates:
        lines.append(
            f"| {item.get('corp_name', '')} | {item.get('corp_name_eng', '')} | `{item.get('ticker', '')}` | "
            f"`{item.get('corp_code', '')}` | `{item.get('company_id', '')}` |"
        )
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
    resolution = data.get("company_resolution", {})
    english = _english_response(payload)

    lines = [f"# {data.get('canonical_name', payload.get('subject', ''))}"]
    if names.get("en"):
        lines.append(f"*{names['en']}*")
    lines.append("")

    if resolution.get("match_type") == "inferred":
        reason = (resolution.get("reason_i18n") or {}).get("en" if english else "ko") or resolution.get("reason", "")
        lines.extend([
            "## Company resolution" if english else "## 회사 식별",
            (f"- Interpreted `{resolution.get('query', '')}` as **{data.get('canonical_name', '')}**"
             if english else f"- 입력 `{resolution.get('query', '')}`을(를) **{data.get('canonical_name', '')}**로 해석"),
            f"- Basis: {reason}" if english else f"- 근거: {reason}",
            f"- Confidence: {resolution.get('confidence', 'high')}" if english else f"- 확신도: {resolution.get('confidence', 'high')}",
        ])
        if resolution.get("market_data_as_of"):
            lines.append((f"- Market-cap snapshot: {resolution['market_data_as_of']}"
                          if english else f"- 시총 기준일: {resolution['market_data_as_of']}"))
        alternatives = resolution.get("alternatives") or []
        if alternatives:
            lines.append(("- Alternatives: " if english else "- 다른 후보: ") + ", ".join(
                f"{(item.get('corp_name_eng') or item.get('corp_name')) if english else item.get('corp_name')}({item.get('ticker')})"
                for item in alternatives
            ))
        lines.append("")
    _cid = company_id_line(data)
    if _cid:
        lines.append(_cid)
    lines.append("")

    if warnings:
        lines.append("## Notes" if english else "## 유의사항")
        for warning in warnings:
            lines.append(f"- {warning}")
        lines.append("")

    lines.extend([
        "## Identifiers" if english else "## 식별자",
        "| Field | Value |" if english else "| 항목 | 값 |",
        "|-------|-------|" if english else "|------|----|",
        f"| ticker | `{identifiers.get('ticker', '')}` |" if identifiers.get("ticker") else ("| ticker | Unlisted/unknown |" if english else "| ticker | 비상장/미상 |"),
        f"| corp_code | `{identifiers.get('corp_code', '')}` |",
        f"| ISIN | `{identifiers.get('isin', '')}` |" if identifiers.get("isin") else ("| ISIN | Not connected |" if english else "| ISIN | 아직 미연결 |"),
        f"| {'Corporate registration no.' if english else '법인등록번호'} | `{identifiers.get('jurir_no', '')}` |" if identifiers.get("jurir_no") else ("| Corporate registration no. | - |" if english else "| 법인등록번호 | - |"),
        f"| {'Business registration no.' if english else '사업자번호'} | `{identifiers.get('bizr_no', '')}` |" if identifiers.get("bizr_no") else ("| Business registration no. | - |" if english else "| 사업자번호 | - |"),
        "",
        "## Classification" if english else "## 분류",
        "| Field | Value |" if english else "| 항목 | 값 |",
        "|-------|-------|" if english else "|------|----|",
        f"| {'Market' if english else '시장'} | {classification.get('market', '') or '-'} |",
        f"| {'Industry' if english else '업종'} | {classification.get('sector_name', '') or '-'} |",
        f"| {'Industry code (DART)' if english else '업종코드(DART)'} | {classification.get('induty_code', '') or '-'} |",
        f"| {'Fiscal month' if english else '결산월'} | {(classification.get('fiscal_month', '') if english else classification.get('fiscal_month', '') + '월') if classification.get('fiscal_month') else '-'} |",
        "",
        "## Company information" if english else "## 기본정보",
        "| Field | Value |" if english else "| 항목 | 값 |",
        "|-------|-------|" if english else "|------|----|",
        f"| {'CEO' if english else '대표이사'} | {basic_info.get('ceo_name', '') or '-'} |",
        f"| {'Established' if english else '설립일'} | {basic_info.get('established_date', '') or '-'} |",
        f"| {'Address' if english else '주소'} | {basic_info.get('address', '') or '-'} |",
        f"| {'Homepage' if english else '홈페이지'} | {basic_info.get('homepage', '') or '-'} |",
        "",
    ])

    aliases = names.get("aliases") or []
    if aliases:
        lines.append("## Aliases" if english else "## 별칭")
        lines.append(", ".join(f"`{alias}`" for alias in aliases))
        lines.append("")

    lines.extend([
        "## Recent filings" if english else "## 최근 공시 인덱스",
        (f"- Search window: {filings_window.get('start_date', '-') } ~ {filings_window.get('end_date', '-')}"
         if english else f"- 조사 구간: {filings_window.get('start_date', '-') } ~ {filings_window.get('end_date', '-')}"),
        "| Date | Type | Filing | Filer | Receipt No |" if english else "| 날짜 | 분류 | 공시명 | 제출인 | 공시번호 |",
        "|------|------|--------|--------|----------|",
    ])
    for item in filings:
        lines.append(
            f"| {item.get('disclosure_date', '')} | {item.get('filing_type', '')} | "
            f"{item.get('report_name', '')} | {item.get('filer_name', '')} | `{item.get('rcept_no', '')}` |"
        )
    if not filings:
        lines.append("| - | - | No recent filings | - | - |" if english else "| - | - | 최근 공시 없음 | - | - |")
    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def company(
        query: str,
        max_recent_filings: int = 10,
        start_date: str = "",
        end_date: str = "",
        format: str = "md",
        language: str = "auto",
    ) -> str:
        """desc: 기업 식별 + 최근 공시 인덱스. 모든 data tool 공통 입구. 회사명/ticker/corp_code → 시장·업종·최근 공시.
        when: 검색 시작 → ticker/corp_code 확정 후속 tool에 전달. 최근 공시 종류·빈도 훑을 때.
        rule: 비상장 법인 자동 제외 (상장사 전용). 공식 한글·영문명과 별칭을 우선하고, 부분명은 활성 상장·시총 격차가 충분할 때만 자동 추론. 공식명 exact는 시총보다 우선.
        params: query, max_recent_filings(1-20), start_date/end_date(YYYYMMDD), language(auto|ko|en)
        ref: shareholder_meeting_notice, ownership_structure, dividend_disclosure, proxy_contest, value_up
        """
        requested_language = (language or "auto").lower()
        if requested_language not in {"auto", "ko", "en"}:
            requested_language = "auto"
        payload = await build_company_payload(
            query,
            max_recent_filings=max(1, min(max_recent_filings, 20)),
            start_date=start_date,
            end_date=end_date,
            language=requested_language,
        )
        if requested_language == "auto":
            query_is_english = bool(re.search(r"[A-Za-z]", query)) and not bool(re.search(r"[가-힣]", query))
            requested_language = "en" if query_is_english else "ko"
        payload.setdefault("data", {})["display_language"] = requested_language

        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") == "exact":
            return _render_exact(payload)
        if payload.get("status") == "error":
            return _render_error(payload)
        return _render_candidates(payload)
