"""v2 value_up facade 서비스."""

from __future__ import annotations

from datetime import date
import re
from typing import Any

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.services.company import _company_id, resolve_company_query
from open_proxy_mcp.services.contracts import AnalysisStatus, EvidenceRef, SourceType, ToolEnvelope

_SUPPORTED_SCOPES = {"summary", "plan", "commitments", "timeline"}
_VALUATION_KEYWORDS = ("기업가치제고", "기업가치 제고", "밸류업")
_COMMITMENT_KEYWORDS = ("주주환원", "자사주", "배당", "ROE", "ROIC", "PBR", "가이드", "중장기")


def _extract_highlights(text: str, keywords: tuple[str, ...], limit: int = 6) -> list[str]:
    clean = re.sub(r"\s+", " ", text or "")
    chunks = re.split(r"(?<=[.!?])\s+|(?<=다\.)\s+", clean)
    hits: list[str] = []
    for chunk in chunks:
        if any(keyword in chunk for keyword in keywords):
            trimmed = chunk.strip()
            if trimmed and trimmed not in hits:
                hits.append(trimmed[:240])
        if len(hits) >= limit:
            break
    return hits


def _unsupported_scope_payload(company_query: str, scope: str) -> dict[str, Any]:
    return ToolEnvelope(
        tool="value_up",
        status=AnalysisStatus.REQUIRES_REVIEW,
        subject=company_query,
        warnings=[f"`{scope}` scope는 아직 지원하지 않는다."],
        data={"query": company_query, "scope": scope},
    ).to_dict()


async def build_value_up_payload(
    company_query: str,
    *,
    scope: str = "summary",
    year: int | None = None,
) -> dict[str, Any]:
    if scope not in _SUPPORTED_SCOPES:
        return _unsupported_scope_payload(company_query, scope)

    resolution = await resolve_company_query(company_query)
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        return ToolEnvelope(
            tool="value_up",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[f"'{company_query}'에 해당하는 회사를 찾지 못했다."],
            data={"query": company_query, "scope": scope},
        ).to_dict()
    if resolution.status == AnalysisStatus.AMBIGUOUS:
        return ToolEnvelope(
            tool="value_up",
            status=AnalysisStatus.AMBIGUOUS,
            subject=company_query,
            warnings=["회사 식별이 애매해 밸류업 공시를 자동 선택하지 않았다."],
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
    target_year = year or date.today().year
    client = get_dart_client()
    warnings: list[str] = []

    try:
        result = await client.search_filings(
            corp_code=selected["corp_code"],
            bgn_de=f"{target_year - 1}0101",
            end_de=f"{target_year}1231",
            pblntf_ty="I",
            page_count=100,
        )
    except DartClientError as exc:
        return ToolEnvelope(
            tool="value_up",
            status=AnalysisStatus.ERROR,
            subject=selected.get("corp_name", company_query),
            warnings=[f"기업가치제고 공시 검색 실패: {exc.status}"],
            data={"query": company_query, "scope": scope, "year": target_year},
        ).to_dict()

    items = [
        item for item in result.get("list", [])
        if any(keyword in (item.get("report_nm") or "").replace(" ", "") for keyword in _VALUATION_KEYWORDS)
    ]
    items.sort(key=lambda row: (row.get("rcept_dt", ""), row.get("rcept_no", "")), reverse=True)

    if not items:
        return ToolEnvelope(
            tool="value_up",
            status=AnalysisStatus.PARTIAL,
            subject=selected.get("corp_name", company_query),
            warnings=[f"{target_year}년 기준 기업가치제고 공시를 찾지 못했다."],
            data={
                "query": company_query,
                "company_id": _company_id(selected),
                "year": target_year,
                "items": [],
            },
        ).to_dict()

    latest = items[0]
    latest_doc = await client.get_document_cached(latest["rcept_no"])
    latest_text = latest_doc.get("text", "")
    highlights = _extract_highlights(latest_text, _COMMITMENT_KEYWORDS)
    if not highlights:
        warnings.append("원문에서 핵심 commitment 문장을 충분히 뽑지 못했다.")

    data: dict[str, Any] = {
        "query": company_query,
        "company_id": _company_id(selected),
        "canonical_name": selected.get("corp_name", ""),
        "identifiers": {
            "ticker": selected.get("stock_code", ""),
            "corp_code": selected.get("corp_code", ""),
        },
        "year": target_year,
        "latest": {
            "rcept_no": latest.get("rcept_no", ""),
            "disclosure_date": latest.get("rcept_dt", ""),
            "report_name": latest.get("report_nm", ""),
            "filer_name": latest.get("flr_nm", ""),
        },
        "available_scopes": sorted(_SUPPORTED_SCOPES),
    }
    if scope in {"summary", "timeline"}:
        data["items"] = [
            {
                "rcept_no": item.get("rcept_no", ""),
                "disclosure_date": item.get("rcept_dt", ""),
                "report_name": item.get("report_nm", ""),
                "filer_name": item.get("flr_nm", ""),
            }
            for item in items[:10]
        ]
    if scope in {"summary", "plan", "commitments"}:
        data["latest_excerpt"] = latest_text[:2000]
        data["highlights"] = highlights

    return ToolEnvelope(
        tool="value_up",
        status=AnalysisStatus.EXACT,
        subject=selected.get("corp_name", company_query),
        warnings=warnings,
        data=data,
        evidence_refs=[
            EvidenceRef(
                evidence_id=f"ev_valueup_{latest['rcept_no']}",
                source_type=SourceType.DART_XML,
                rcept_no=latest["rcept_no"],
                section="기업가치제고계획",
                snippet=latest.get("report_nm", ""),
                parser="document_excerpt",
            )
        ],
        next_actions=[
            "commitments scope로 주주환원/ROE 관련 문장 확인" if scope == "summary" else "dividend, ownership_structure와 함께 보면 주주환원 맥락이 더 잘 보인다.",
        ],
    ).to_dict()

