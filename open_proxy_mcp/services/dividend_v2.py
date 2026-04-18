"""v2 dividend facade 서비스."""

from __future__ import annotations

from datetime import date
from typing import Any

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.services.company import _company_id, resolve_company_query
from open_proxy_mcp.services.contracts import AnalysisStatus, EvidenceRef, SourceType, ToolEnvelope
from open_proxy_mcp.tools.dividend import (
    _DIV_KEYWORDS,
    _build_dividend_summary,
    _parse_dividend_decision,
    _parse_dividend_items,
)

_SUPPORTED_SCOPES = {"summary", "detail", "history", "policy_signals"}


def _year_window(end_year: int, years: int) -> list[int]:
    return list(range(end_year - years + 1, end_year + 1))


async def _search_dividend_filings(corp_code: str, start_year: int, end_year: int) -> tuple[list[dict[str, Any]], str | None]:
    client = get_dart_client()
    try:
        result = await client.search_filings(
            corp_code=corp_code,
            bgn_de=f"{start_year}0101",
            end_de=f"{end_year + 1}1231",
            pblntf_ty="I",
            page_count=100,
        )
    except DartClientError as exc:
        return [], f"배당결정 공시 검색 실패: {exc.status}"

    filings = [
        item for item in result.get("list", [])
        if any(keyword in (item.get("report_nm") or "") for keyword in _DIV_KEYWORDS)
    ]
    filings.sort(key=lambda row: (row.get("rcept_dt", ""), row.get("rcept_no", "")), reverse=True)
    return filings, None


async def _decision_details(filings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    client = get_dart_client()
    details: list[dict[str, Any]] = []
    for item in filings:
        try:
            doc = await client.get_document_cached(item["rcept_no"])
        except Exception:
            continue
        parsed = _parse_dividend_decision(doc.get("text", ""))
        if not parsed:
            continue
        parsed["rcept_no"] = item.get("rcept_no", "")
        parsed["rcept_dt"] = item.get("rcept_dt", "")
        parsed["report_name"] = item.get("report_nm", "")
        details.append(parsed)
    return details


async def _annual_summary(corp_code: str, year: int) -> tuple[dict[str, Any], str | None]:
    client = get_dart_client()
    try:
        data = await client.get_dividend_info(corp_code, str(year), "11011")
    except DartClientError as exc:
        return {}, f"alotMatter 조회 실패: {exc.status}"
    items = _parse_dividend_items(data)
    if not items:
        return {}, None
    return _build_dividend_summary(items, "사업보고서(기말)"), None


def _history_rows(end_year: int, annual_summaries: dict[int, dict[str, Any]], decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    decisions_by_year: dict[int, list[dict[str, Any]]] = {}
    for item in decisions:
        base = item.get("record_date") or item.get("rcept_dt", "")
        if not base:
            continue
        year = int(base[:4])
        decisions_by_year.setdefault(year, []).append(item)

    history: list[dict[str, Any]] = []
    for year, summary in sorted(annual_summaries.items()):
        yearly = decisions_by_year.get(year, [])
        annual_dps = summary.get("total_dps", 0)
        history.append({
            "year": year,
            "annual_dps": annual_dps,
            "decision_count": len(yearly),
            "payout_ratio": summary.get("payout_ratio_dart"),
            "yield_pct": summary.get("yield_dart"),
            "has_special": any(item.get("has_special") for item in yearly),
            "pattern": "분기/중간 포함" if len(yearly) > 1 else ("연간배당" if yearly else "무배당"),
        })
    return history


def _policy_signals(history: list[dict[str, Any]]) -> dict[str, Any]:
    if not history:
        return {
            "trend": "insufficient_data",
            "has_quarterly_pattern": False,
            "has_special_dividend": False,
            "latest_change_pct": None,
        }
    sorted_history = sorted(history, key=lambda item: item["year"])
    latest = sorted_history[-1]
    prev = sorted_history[-2] if len(sorted_history) >= 2 else None
    latest_change_pct = None
    trend = "stable"
    if prev and prev.get("annual_dps"):
        latest_change_pct = round((latest["annual_dps"] - prev["annual_dps"]) / prev["annual_dps"] * 100, 2)
        if latest_change_pct > 5:
            trend = "increasing"
        elif latest_change_pct < -5:
            trend = "decreasing"
    return {
        "trend": trend,
        "has_quarterly_pattern": any(item.get("decision_count", 0) > 1 for item in history),
        "has_special_dividend": any(item.get("has_special") for item in history),
        "latest_change_pct": latest_change_pct,
    }


def _unsupported_scope_payload(company_query: str, scope: str) -> dict[str, Any]:
    return ToolEnvelope(
        tool="dividend",
        status=AnalysisStatus.REQUIRES_REVIEW,
        subject=company_query,
        warnings=[f"`{scope}` scope는 아직 지원하지 않는다."],
        data={"query": company_query, "scope": scope},
    ).to_dict()


async def build_dividend_payload(
    company_query: str,
    *,
    scope: str = "summary",
    year: int | None = None,
    years: int = 3,
) -> dict[str, Any]:
    if scope not in _SUPPORTED_SCOPES:
        return _unsupported_scope_payload(company_query, scope)

    resolution = await resolve_company_query(company_query)
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        return ToolEnvelope(
            tool="dividend",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[f"'{company_query}'에 해당하는 회사를 찾지 못했다."],
            data={"query": company_query, "scope": scope},
        ).to_dict()
    if resolution.status == AnalysisStatus.AMBIGUOUS:
        return ToolEnvelope(
            tool="dividend",
            status=AnalysisStatus.AMBIGUOUS,
            subject=company_query,
            warnings=["회사 식별이 애매해 배당 데이터를 자동 선택하지 않았다."],
            data={
                "query": company_query,
                "scope": scope,
                "candidates": [
                    {
                        "company_id": _company_id(corp),
                        "corp_name": corp.get("corp_name", ""),
                        "ticker": corp.get("stock_code", ""),
                        "corp_code": corp.get("corp_code", ""),
                    }
                    for corp in resolution.candidates[:10]
                ],
            },
        ).to_dict()

    selected = resolution.selected
    target_year = year or (date.today().year - 1)
    year_list = _year_window(target_year, max(1, years))
    warnings: list[str] = []

    latest_summary, summary_warning = await _annual_summary(selected["corp_code"], target_year)
    if summary_warning:
        warnings.append(summary_warning)

    filings, filing_warning = await _search_dividend_filings(selected["corp_code"], year_list[0], target_year)
    if filing_warning:
        warnings.append(filing_warning)
        filings = []
    details = await _decision_details(filings[:20]) if filings else []

    annual_summaries: dict[int, dict[str, Any]] = {}
    for y in year_list:
        summary, warning = await _annual_summary(selected["corp_code"], y)
        if warning:
            warnings.append(f"{y}년 {warning}")
        if summary:
            annual_summaries[y] = summary

    history = _history_rows(target_year, annual_summaries, details)
    policy = _policy_signals(history)

    latest_decision = details[0] if details else None
    data: dict[str, Any] = {
        "query": company_query,
        "company_id": _company_id(selected),
        "canonical_name": selected.get("corp_name", ""),
        "identifiers": {
            "ticker": selected.get("stock_code", ""),
            "corp_code": selected.get("corp_code", ""),
        },
        "year": target_year,
        "summary": latest_summary,
        "available_scopes": sorted(_SUPPORTED_SCOPES),
    }
    if scope in {"summary", "detail"}:
        data["latest_decisions"] = details[:5]
    if scope == "history":
        data["history"] = history
    if scope == "policy_signals":
        data["policy_signals"] = policy
        data["history"] = history
    if scope == "summary":
        data["policy_signals"] = policy
    if scope == "detail":
        data["detail"] = {
            "annual_summary": latest_summary,
            "latest_decisions": details[:10],
        }

    evidence_refs: list[EvidenceRef] = []
    if latest_summary:
        evidence_refs.append(
            EvidenceRef(
                evidence_id=f"ev_dividend_api_{selected['corp_code']}_{target_year}",
                source_type=SourceType.DART_API,
                section="alotMatter",
                snippet=f"{selected.get('corp_name', '')} {target_year}년 사업보고서 배당 요약",
                parser="dart_api",
            )
        )
    if latest_decision and latest_decision.get("rcept_no"):
        evidence_refs.append(
            EvidenceRef(
                evidence_id=f"ev_dividend_{latest_decision['rcept_no']}",
                source_type=SourceType.DART_XML,
                rcept_no=latest_decision["rcept_no"],
                section="현금ㆍ현물배당결정",
                snippet=f"{latest_decision.get('dividend_type', '')} / DPS {latest_decision.get('dps_common', 0):,}원",
                parser="decision_parser",
            )
        )

    status = AnalysisStatus.EXACT if latest_summary or details else AnalysisStatus.PARTIAL
    if status == AnalysisStatus.PARTIAL:
        warnings.append("사업보고서 요약이나 배당결정 공시 일부가 비어 있어 partial 상태로 표시한다.")

    return ToolEnvelope(
        tool="dividend",
        status=status,
        subject=selected.get("corp_name", company_query),
        warnings=warnings,
        data=data,
        evidence_refs=evidence_refs,
        next_actions=[
            "history scope로 최근 3년 배당 추이 확인" if scope == "summary" else "ownership_structure와 함께 보면 주주환원 맥락이 더 잘 보인다.",
        ],
    ).to_dict()

