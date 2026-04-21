"""v2 related_party_transaction data tool.

타법인주식 거래 + 단일판매·공급계약 공시 통합. 일감몰아주기·내부거래 모니터링 소스.

DART 전용 구조화 API가 없어 list.json + report_nm 키워드 매칭 방식.
상세 수치(거래금액, 상대방)는 evidence tool로 원문 링크 제공.
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

from open_proxy_mcp.dart.client import DartClientError
from open_proxy_mcp.services.company import _company_id, resolve_company_query
from open_proxy_mcp.services.contracts import AnalysisStatus, EvidenceRef, SourceType, ToolEnvelope
from open_proxy_mcp.services.date_utils import format_iso_date, format_yyyymmdd, resolve_date_window
from open_proxy_mcp.services.filing_search import search_filings_by_report_name


_SUPPORTED_SCOPES = {"summary", "equity_deal", "supply_contract"}


# 타법인주식 거래 — 취득/양수 및 처분/양도
_EQUITY_DEAL_KEYWORDS = (
    "타법인주식및출자증권양수결정",
    "타법인주식및출자증권양도결정",
    "타법인주식및출자증권취득결정",
    "타법인주식및출자증권처분결정",
)

# 단일판매·공급계약 — 체결/해지
_SUPPLY_CONTRACT_KEYWORDS = (
    "단일판매ㆍ공급계약체결",
    "단일판매ㆍ공급계약해지",
    "단일판매·공급계약체결",
    "단일판매·공급계약해지",
)


def _classify_equity_deal(report_nm: str) -> str:
    compact = (report_nm or "").replace(" ", "")
    if "양수" in compact or "취득" in compact:
        return "acquire"
    if "양도" in compact or "처분" in compact:
        return "dispose"
    return "unknown"


def _classify_supply_contract(report_nm: str) -> str:
    compact = (report_nm or "").replace(" ", "")
    if "해지" in compact:
        return "terminate"
    if "체결" in compact:
        return "conclude"
    return "unknown"


def _is_self_filing(flr_nm: str, corp_name: str) -> bool:
    """공시 제출인이 회사 본인인지 (자회사 주요경영사항 구분)."""
    a = (flr_nm or "").strip()
    b = (corp_name or "").strip()
    return bool(a and b and (a == b or b in a or a in b))


def _is_autonomous(report_nm: str) -> bool:
    compact = (report_nm or "").replace(" ", "")
    return "자율공시" in compact


def _is_subsidiary_report(report_nm: str) -> bool:
    compact = (report_nm or "").replace(" ", "")
    return "자회사의주요경영사항" in compact or "자회사의주요경영사항" in report_nm


async def _fetch_equity_deals(corp_code: str, corp_name: str, bgn_de: str, end_de: str) -> tuple[list[dict[str, Any]], list[str], int]:
    items, notices, error = await search_filings_by_report_name(
        corp_code=corp_code,
        bgn_de=bgn_de,
        end_de=end_de,
        pblntf_tys=("B", "I"),
        keywords=_EQUITY_DEAL_KEYWORDS,
        strip_spaces=True,
    )
    rows: list[dict[str, Any]] = []
    api_calls = 1  # helper가 내부에서 페이지 순회하지만 기본 1회 이상
    warnings = []
    if error:
        warnings.append(f"타법인주식 거래 조회 실패: {error}")
        return rows, notices + warnings, api_calls

    for item in items:
        report_nm = item.get("report_nm", "")
        rows.append({
            "type": "equity_deal",
            "direction": _classify_equity_deal(report_nm),  # acquire/dispose
            "event_label": "타법인주식·출자증권 거래",
            "rcept_no": item.get("rcept_no", ""),
            "rcept_dt": item.get("rcept_dt", ""),
            "report_nm": report_nm,
            "filer_name": item.get("flr_nm", ""),
            "subsidiary_report": _is_subsidiary_report(report_nm),
            "autonomous_disclosure": _is_autonomous(report_nm),
            "self_filing": _is_self_filing(item.get("flr_nm", ""), corp_name),
            "is_correction": report_nm.startswith("[기재정정]"),
        })
    return rows, notices + warnings, api_calls


async def _fetch_supply_contracts(corp_code: str, corp_name: str, bgn_de: str, end_de: str) -> tuple[list[dict[str, Any]], list[str], int]:
    items, notices, error = await search_filings_by_report_name(
        corp_code=corp_code,
        bgn_de=bgn_de,
        end_de=end_de,
        pblntf_tys=("I",),
        keywords=_SUPPLY_CONTRACT_KEYWORDS,
        strip_spaces=True,
    )
    rows: list[dict[str, Any]] = []
    api_calls = 1
    warnings = []
    if error:
        warnings.append(f"단일판매·공급계약 조회 실패: {error}")
        return rows, notices + warnings, api_calls

    for item in items:
        report_nm = item.get("report_nm", "")
        rows.append({
            "type": "supply_contract",
            "direction": _classify_supply_contract(report_nm),  # conclude/terminate
            "event_label": "단일판매·공급계약",
            "rcept_no": item.get("rcept_no", ""),
            "rcept_dt": item.get("rcept_dt", ""),
            "report_nm": report_nm,
            "filer_name": item.get("flr_nm", ""),
            "subsidiary_report": _is_subsidiary_report(report_nm),
            "autonomous_disclosure": _is_autonomous(report_nm),
            "self_filing": _is_self_filing(item.get("flr_nm", ""), corp_name),
            "is_correction": report_nm.startswith("[기재정정]"),
        })
    return rows, notices + warnings, api_calls


def _unsupported_scope_payload(company_query: str, scope: str) -> dict[str, Any]:
    return ToolEnvelope(
        tool="related_party_transaction",
        status=AnalysisStatus.REQUIRES_REVIEW,
        subject=company_query,
        warnings=[f"`{scope}` scope 미지원."],
        data={
            "query": company_query,
            "scope": scope,
            "supported_scopes": sorted(_SUPPORTED_SCOPES),
        },
    ).to_dict()


async def build_related_party_transaction_payload(
    company_query: str,
    *,
    scope: str = "summary",
    start_date: str = "",
    end_date: str = "",
) -> dict[str, Any]:
    if scope not in _SUPPORTED_SCOPES:
        return _unsupported_scope_payload(company_query, scope)

    resolution = await resolve_company_query(company_query)
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        return ToolEnvelope(
            tool="related_party_transaction",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[f"'{company_query}'에 해당하는 회사를 찾지 못했다."],
            data={"query": company_query, "scope": scope},
            next_actions=["company tool로 회사 식별 확인"],
        ).to_dict()
    if resolution.status == AnalysisStatus.AMBIGUOUS:
        return ToolEnvelope(
            tool="related_party_transaction",
            status=AnalysisStatus.AMBIGUOUS,
            subject=company_query,
            warnings=["회사 식별이 애매해 자동 선택하지 않았다."],
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
    window_start, window_end, window_warnings = resolve_date_window(
        start_date=start_date,
        end_date=end_date,
        default_end=date.today(),
        lookback_months=24,
    )
    bgn_de = format_yyyymmdd(window_start)
    end_de = format_yyyymmdd(window_end)

    warnings = list(window_warnings)
    all_rows: list[dict[str, Any]] = []
    total_api_calls = 0

    tasks: list[Any] = []
    if scope in ("summary", "equity_deal"):
        tasks.append(_fetch_equity_deals(selected["corp_code"], selected.get("corp_name", ""), bgn_de, end_de))
    if scope in ("summary", "supply_contract"):
        tasks.append(_fetch_supply_contracts(selected["corp_code"], selected.get("corp_name", ""), bgn_de, end_de))

    results = await asyncio.gather(*tasks)
    for rows, notices, api_calls in results:
        all_rows.extend(rows)
        warnings.extend(notices)
        total_api_calls += api_calls

    all_rows.sort(key=lambda row: (row.get("rcept_dt", ""), row.get("rcept_no", "")), reverse=True)

    by_type: dict[str, list[dict[str, Any]]] = {"equity_deal": [], "supply_contract": []}
    acquire_count = dispose_count = conclude_count = terminate_count = 0
    subsidiary_count = autonomous_count = 0
    for row in all_rows:
        by_type.setdefault(row.get("type", ""), []).append(row)
        if row.get("type") == "equity_deal":
            if row.get("direction") == "acquire":
                acquire_count += 1
            elif row.get("direction") == "dispose":
                dispose_count += 1
        elif row.get("type") == "supply_contract":
            if row.get("direction") == "conclude":
                conclude_count += 1
            elif row.get("direction") == "terminate":
                terminate_count += 1
        if row.get("subsidiary_report"):
            subsidiary_count += 1
        if row.get("autonomous_disclosure"):
            autonomous_count += 1

    usage = {
        "dart_api_calls": total_api_calls,
        "mcp_tool_calls": 1,
        "dart_daily_limit_per_minute": 1000,
    }

    data: dict[str, Any] = {
        "query": company_query,
        "company_id": _company_id(selected),
        "canonical_name": selected.get("corp_name", ""),
        "identifiers": {
            "ticker": selected.get("stock_code", ""),
            "corp_code": selected.get("corp_code", ""),
        },
        "scope": scope,
        "window": {"start_date": bgn_de, "end_date": end_de},
        "event_count": {
            "total": len(all_rows),
            "equity_deal_total": len(by_type["equity_deal"]),
            "equity_acquire": acquire_count,
            "equity_dispose": dispose_count,
            "supply_contract_total": len(by_type["supply_contract"]),
            "supply_conclude": conclude_count,
            "supply_terminate": terminate_count,
            "subsidiary_reports": subsidiary_count,
            "autonomous_disclosures": autonomous_count,
        },
        "usage": usage,
        "supported_scopes": sorted(_SUPPORTED_SCOPES),
    }

    if scope == "summary":
        data["events_timeline"] = [
            {
                "type": row.get("type", ""),
                "direction": row.get("direction", ""),
                "rcept_dt": row.get("rcept_dt", ""),
                "report_nm": row.get("report_nm", ""),
                "filer": row.get("filer_name", ""),
                "subsidiary": row.get("subsidiary_report", False),
                "autonomous": row.get("autonomous_disclosure", False),
                "rcept_no": row.get("rcept_no", ""),
            }
            for row in all_rows
        ]
    if scope == "equity_deal":
        data["equity_deal_events"] = by_type["equity_deal"]
    if scope == "supply_contract":
        data["supply_contract_events"] = by_type["supply_contract"]

    evidence_refs: list[EvidenceRef] = []
    for row in all_rows[:5]:
        rcept_no = row.get("rcept_no", "")
        if rcept_no:
            evidence_refs.append(
                EvidenceRef(
                    evidence_id=f"ev_rpt_{rcept_no}",
                    source_type=SourceType.DART_API,
                    rcept_no=rcept_no,
                    rcept_dt=format_iso_date(row.get("rcept_dt", "")),
                    report_nm=row.get("report_nm", ""),
                    section="list.json + keyword",
                    note=f"{row.get('type', '')} / {row.get('direction', '')}",
                )
            )

    status = AnalysisStatus.EXACT if all_rows else AnalysisStatus.PARTIAL
    if not all_rows:
        warnings.append("조사 구간 내 타법인주식 거래·단일공급계약 공시 없음")

    return ToolEnvelope(
        tool="related_party_transaction",
        status=status,
        subject=selected.get("corp_name", company_query),
        warnings=warnings,
        data=data,
        evidence_refs=evidence_refs,
        next_actions=[
            "개별 거래의 상대방·금액·특수관계 여부는 evidence tool로 원문 확인",
            "자회사 주요경영사항 공시는 모회사 관점에서 연결됨 (중복 집계 주의)",
        ],
    ).to_dict()
