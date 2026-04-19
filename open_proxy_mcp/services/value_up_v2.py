"""v2 value_up facade 서비스."""

from __future__ import annotations

from datetime import date
from html import unescape
import re
from typing import Any

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.services.company import _company_id, resolve_company_query
from open_proxy_mcp.services.contracts import AnalysisStatus, EvidenceRef, SourceType, ToolEnvelope
from open_proxy_mcp.services.date_utils import format_yyyymmdd, resolve_date_window
from open_proxy_mcp.services.filing_search import search_filings_by_report_name

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


def _yyyymmdd_to_kind_date(value: str) -> str:
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value


def _kind_html_to_text(html: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _filter_value_up_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered = [
        item for item in items
        if any(keyword in (item.get("report_nm") or "").replace(" ", "") for keyword in _VALUATION_KEYWORDS)
    ]
    filtered.sort(key=lambda row: (row.get("rcept_dt", ""), row.get("rcept_no", "")), reverse=True)
    return filtered


async def _search_value_up_items(
    corp_code: str,
    *,
    bgn_de: str,
    end_de: str,
) -> tuple[list[dict[str, Any]], list[str], str | None]:
    return await search_filings_by_report_name(
        corp_code=corp_code,
        bgn_de=bgn_de,
        end_de=end_de,
        pblntf_tys="I",
        keywords=_VALUATION_KEYWORDS,
        strip_spaces=True,
    )


async def _search_kind_value_up_items(
    stock_code: str,
    corp_name: str,
    *,
    bgn_de: str,
    end_de: str,
) -> tuple[list[dict[str, Any]], str | None]:
    client = get_dart_client()
    try:
        result = await client.kind_search_value_up(
            stock_code=stock_code,
            corp_name=corp_name,
            from_date=_yyyymmdd_to_kind_date(bgn_de),
            to_date=_yyyymmdd_to_kind_date(end_de),
        )
    except Exception as exc:  # KIND는 공식 API가 아니므로 에러 문자열 그대로 진단
        return [], str(exc)
    return result, None


async def build_value_up_payload(
    company_query: str,
    *,
    scope: str = "summary",
    year: int | None = None,
    start_date: str = "",
    end_date: str = "",
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
    explicit_window = bool(start_date or end_date)
    default_end = date(target_year, 12, 31) if year else date.today()
    window_start, window_end, window_warnings = resolve_date_window(
        start_date=start_date,
        end_date=end_date,
        default_end=default_end,
        lookback_months=12,
    )
    warnings: list[str] = list(window_warnings)

    requested_bgn = format_yyyymmdd(window_start)
    requested_end = format_yyyymmdd(window_end)
    items, search_notices, search_error = await _search_value_up_items(
        selected["corp_code"],
        bgn_de=requested_bgn,
        end_de=requested_end,
    )
    warnings.extend(search_notices)
    if search_error:
        return ToolEnvelope(
            tool="value_up",
            status=AnalysisStatus.ERROR,
            subject=selected.get("corp_name", company_query),
            warnings=[f"기업가치제고 공시 검색 실패: {search_error}"],
            data={"query": company_query, "scope": scope, "year": target_year},
        ).to_dict()

    kind_items: list[dict[str, Any]] = []
    kind_search_error: str | None = None
    if not items:
        kind_items, kind_search_error = await _search_kind_value_up_items(
            selected.get("stock_code", ""),
            selected.get("corp_name", company_query),
            bgn_de=requested_bgn,
            end_de=requested_end,
        )

    if not items and not kind_items:
        diagnostic_bgn = f"{target_year - 2}0101"
        diagnostic_end = f"{target_year}1231"
        diagnostic_items, diagnostic_notices, diagnostic_error = await _search_value_up_items(
            selected["corp_code"],
            bgn_de=diagnostic_bgn,
            end_de=diagnostic_end,
        )
        warnings.extend(diagnostic_notices)
        diagnostic_kind_items, diagnostic_kind_error = await _search_kind_value_up_items(
            selected.get("stock_code", ""),
            selected.get("corp_name", company_query),
            bgn_de=diagnostic_bgn,
            end_de=diagnostic_end,
        )
        diagnostics = {
            "requested_window": {
                "start_date": requested_bgn,
                "end_date": requested_end,
                "dart_filing_count": 0,
                "kind_filing_count": 0,
            },
            "diagnostic_window": {
                "start_date": diagnostic_bgn,
                "end_date": diagnostic_end,
                "dart_filing_count": len(diagnostic_items),
                "kind_filing_count": len(diagnostic_kind_items),
            },
        }
        if diagnostic_error:
            warnings.append(f"진단 검색 실패: {diagnostic_error}")
        if kind_search_error:
            warnings.append(f"KIND 검색 실패: {kind_search_error}")
        if diagnostic_kind_error:
            warnings.append(f"KIND 진단 검색 실패: {diagnostic_kind_error}")
        availability_status = "no_filing_found"
        if diagnostic_items or diagnostic_kind_items:
            availability_status = "exists_outside_requested_window"
            warnings.append(
                "요청 구간에는 기업가치제고 공시가 없지만, 진단 구간에서는 관련 공시가 확인된다."
            )
            sample_filings: list[dict[str, Any]] = [
                {
                    "source": "dart",
                    "rcept_no": item.get("rcept_no", ""),
                    "disclosure_date": item.get("rcept_dt", ""),
                    "report_name": item.get("report_nm", ""),
                }
                for item in diagnostic_items[:5]
            ]
            sample_filings.extend(
                {
                    "source": "kind",
                    "acptno": item.get("acptno", ""),
                    "disclosure_date": item.get("disclosure_date", ""),
                    "report_name": item.get("report_name", ""),
                }
                for item in diagnostic_kind_items[:5]
            )
            diagnostics["diagnostic_window"]["sample_filings"] = sample_filings[:10]
        else:
            if explicit_window:
                warnings.append("요청 구간에는 관련 공시가 없고, DART/KIND 진단 구간에서도 공시를 찾지 못했다.")
            else:
                warnings.append("요청 구간과 DART/KIND 진단 구간 모두에서 기업가치제고 공시를 찾지 못했다.")
        return ToolEnvelope(
            tool="value_up",
            status=AnalysisStatus.PARTIAL,
            subject=selected.get("corp_name", company_query),
            warnings=warnings,
            data={
                "query": company_query,
                "company_id": _company_id(selected),
                "year": target_year,
                "window": {
                    "start_date": requested_bgn,
                    "end_date": requested_end,
                },
                "availability_status": availability_status,
                "search_diagnostics": diagnostics,
                "items": [],
            },
        ).to_dict()

    client = get_dart_client()
    latest_source = "dart"
    latest = items[0] if items else kind_items[0]
    availability_status = "found_in_requested_window" if items else "found_in_requested_window_kind_only"
    latest_text = ""
    latest_excerpt = ""
    source_type = SourceType.DART_XML
    if items:
        latest_doc = await client.get_document_cached(latest["rcept_no"])
        latest_text = latest_doc.get("text", "")
        latest_excerpt = latest_text[:2000]
        source_type = SourceType.DART_XML
    else:
        latest_source = "kind"
        try:
            latest_html = await client.kind_fetch_document(latest["acptno"])
            latest_text = _kind_html_to_text(latest_html)
            latest_excerpt = latest_text[:2000]
        except DartClientError as exc:
            warnings.append(f"KIND 본문 조회 실패: {exc.status}")
        source_type = SourceType.KIND_HTML

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
        "window": {
            "start_date": requested_bgn,
            "end_date": requested_end,
        },
        "availability_status": availability_status,
        "primary_source": latest_source,
        "search_diagnostics": {
            "requested_window": {
                "start_date": requested_bgn,
                "end_date": requested_end,
                "dart_filing_count": len(items),
                "kind_filing_count": len(kind_items),
            }
        },
        "latest": {
            "rcept_no": latest.get("rcept_no", ""),
            "acptno": latest.get("acptno", ""),
            "disclosure_date": latest.get("rcept_dt", latest.get("disclosure_date", "")),
            "report_name": latest.get("report_nm", latest.get("report_name", "")),
            "filer_name": latest.get("flr_nm", latest.get("filer_name", "")),
            "source_type": getattr(source_type, "value", source_type),
        },
        "available_scopes": sorted(_SUPPORTED_SCOPES),
    }
    if scope in {"summary", "timeline"}:
        data["items"] = [
            {
                "source": "dart",
                "rcept_no": item.get("rcept_no", ""),
                "acptno": "",
                "disclosure_date": item.get("rcept_dt", ""),
                "report_name": item.get("report_nm", ""),
                "filer_name": item.get("flr_nm", ""),
            }
            for item in items[:10]
        ]
        data["items"].extend(
            {
                "source": "kind",
                "rcept_no": "",
                "acptno": item.get("acptno", ""),
                "disclosure_date": item.get("disclosure_date", ""),
                "report_name": item.get("report_name", ""),
                "filer_name": item.get("filer_name", ""),
            }
            for item in kind_items[:10]
        )
    if scope in {"summary", "plan", "commitments"}:
        data["latest_excerpt"] = latest_excerpt
        data["highlights"] = highlights

    return ToolEnvelope(
        tool="value_up",
        status=AnalysisStatus.EXACT,
        subject=selected.get("corp_name", company_query),
        warnings=warnings,
        data=data,
        evidence_refs=[
            EvidenceRef(
                evidence_id=f"ev_valueup_{latest.get('rcept_no') or latest.get('acptno', '')}",
                source_type=source_type,
                rcept_no=latest.get("rcept_no", latest.get("acptno", "")),
                section="기업가치제고계획",
                snippet=latest.get("report_nm", latest.get("report_name", "")),
                parser="document_excerpt" if latest_source == "dart" else "kind_search_document",
            )
        ],
        next_actions=[
            "commitments scope로 주주환원/ROE 관련 문장 확인" if scope == "summary" else "dividend, ownership_structure와 함께 보면 주주환원 맥락이 더 잘 보인다.",
        ],
    ).to_dict()
