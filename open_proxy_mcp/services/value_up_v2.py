"""v2 value_up facade 서비스."""

from __future__ import annotations

import asyncio
from datetime import date
from html import unescape
import re
from typing import Any

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.services.company import _company_id, resolve_company_query
from open_proxy_mcp.services.contracts import (
    AnalysisStatus,
    EvidenceRef,
    SourceType,
    ToolEnvelope,
    build_filing_meta,
    build_usage,
    status_from_filing_meta,
)
from open_proxy_mcp.services.date_utils import format_iso_date, format_yyyymmdd, resolve_date_window
from open_proxy_mcp.services.filing_search import search_filings_by_report_name

_SUPPORTED_SCOPES = {"summary", "plan", "commitments", "timeline"}
_VALUATION_KEYWORDS = ("기업가치제고", "기업가치 제고", "밸류업")
_COMMITMENT_KEYWORDS = ("주주환원", "자사주", "배당", "ROE", "ROIC", "PBR", "가이드", "중장기")


_NOISE_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\.xforms|font-family|font-size|padding|margin|\{[^}]*\}"),
    re.compile(r"<[^>]+>"),
    re.compile(r"&(?:nbsp|amp|lt|gt|quot|apos);"),
)


def _is_noise(chunk: str) -> bool:
    for pattern in _NOISE_PATTERNS:
        if pattern.search(chunk):
            return True
    alnum = sum(1 for ch in chunk if ch.isalnum())
    return alnum < 10


def _extract_highlights(text: str, keywords: tuple[str, ...], limit: int = 6) -> list[str]:
    clean = re.sub(r"\s+", " ", text or "")
    chunks = re.split(r"(?<=[.!?])\s+|(?<=다\.)\s+", clean)
    hits: list[str] = []
    for chunk in chunks:
        trimmed = chunk.strip()
        if not trimmed or _is_noise(trimmed):
            continue
        if any(keyword in trimmed for keyword in keywords):
            if trimmed not in hits:
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


def _classify_value_up_item(report_name: str) -> str:
    """기업가치제고 공시를 카테고리로 분류.

    - meta_amendment: "고배당기업 표시" 같은 형식 재공시 (실제 본문 계획은 원본에 있음)
    - progress: "이행현황" 관련 재공시
    - plan: 실제 계획 본문 (원본 또는 개정)
    """

    name = (report_name or "").replace(" ", "")
    if "고배당기업" in name or "고배당법인" in name:
        return "meta_amendment"
    if "이행현황" in name:
        return "progress"
    return "plan"


def _item_report_name(item: dict[str, Any]) -> str:
    """DART item은 report_nm, KIND item은 report_name."""

    return item.get("report_nm") or item.get("report_name") or ""


def _select_latest_plan_item(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    """items 중 실제 계획 본문을 담은 최신 항목 선택.

    meta_amendment(고배당기업 형식 재공시)는 실제 계획 본문이 없으므로 제외한다.
    plan 카테고리가 없으면 progress도 허용, 그것도 없으면 None.
    """

    plan_items = [it for it in items if _classify_value_up_item(_item_report_name(it)) == "plan"]
    if plan_items:
        return plan_items[0]
    progress_items = [it for it in items if _classify_value_up_item(_item_report_name(it)) == "progress"]
    if progress_items:
        return progress_items[0]
    return None


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


def _build_value_up_evidence(
    latest: dict[str, Any],
    latest_source: str,
    source_type: SourceType,
    best_plan_item: dict[str, Any] | None,
) -> list[EvidenceRef]:
    refs = [
        EvidenceRef(
            evidence_id=f"ev_valueup_{latest.get('rcept_no') or latest.get('acptno', '')}",
            source_type=source_type,
            rcept_no=latest.get("rcept_no", latest.get("acptno", "")),
            rcept_dt=format_iso_date(latest.get("rcept_dt", latest.get("disclosure_date", ""))),
            report_nm=latest.get("report_nm", latest.get("report_name", "")),
            section="기업가치제고계획",
            note=f"최신 공시 ({'DART' if latest_source == 'dart' else 'KIND'})",
        )
    ]
    if best_plan_item and best_plan_item is not latest:
        plan_rcept = best_plan_item.get("rcept_no") or best_plan_item.get("acptno", "")
        plan_src_type = SourceType.DART_XML if best_plan_item.get("rcept_no") else SourceType.KIND_HTML
        refs.append(
            EvidenceRef(
                evidence_id=f"ev_valueup_plan_{plan_rcept}",
                source_type=plan_src_type,
                rcept_no=plan_rcept,
                rcept_dt=format_iso_date(best_plan_item.get("rcept_dt", best_plan_item.get("disclosure_date", ""))),
                report_nm=_item_report_name(best_plan_item),
                section="기업가치제고계획 원본/이행현황",
                note="commitment 문장 추출에 사용한 실계획 본문",
            )
        )
    return refs


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

    client = get_dart_client()
    _calls_start = client.api_call_snapshot()
    resolution = await resolve_company_query(company_query)
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        return ToolEnvelope(
            tool="value_up",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[f"'{company_query}'에 해당하는 회사를 찾지 못했다."],
            data={
                "query": company_query,
                "scope": scope,
                "usage": build_usage(client.api_call_snapshot() - _calls_start),
            },
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
                "usage": build_usage(client.api_call_snapshot() - _calls_start),
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
            data={
                "query": company_query,
                "scope": scope,
                "year": target_year,
                "usage": build_usage(client.api_call_snapshot() - _calls_start),
            },
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
        # DART 진단검색과 KIND 진단검색은 independent → 병렬.
        diag_dart_task = _search_value_up_items(
            selected["corp_code"],
            bgn_de=diagnostic_bgn,
            end_de=diagnostic_end,
        )
        diag_kind_task = _search_kind_value_up_items(
            selected.get("stock_code", ""),
            selected.get("corp_name", company_query),
            bgn_de=diagnostic_bgn,
            end_de=diagnostic_end,
        )
        (diagnostic_items, diagnostic_notices, diagnostic_error), (diagnostic_kind_items, diagnostic_kind_error) = await asyncio.gather(
            diag_dart_task, diag_kind_task,
        )
        warnings.extend(diagnostic_notices)
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
        # availability_status가 "exists_outside_requested_window"인 경우는 진짜 partial.
        # "no_filing_found"인 경우는 사건 자체가 없는 정상 케이스 = NO_FILING.
        no_filing_meta = build_filing_meta(filing_count=0, parsing_failures=0)
        if availability_status == "exists_outside_requested_window":
            response_status = AnalysisStatus.PARTIAL
        else:
            response_status = AnalysisStatus.NO_FILING
        return ToolEnvelope(
            tool="value_up",
            status=response_status,
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
                **no_filing_meta,
                "usage": build_usage(client.api_call_snapshot() - _calls_start),
            },
        ).to_dict()

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

    latest_category = _classify_value_up_item(_item_report_name(latest))
    # 최신 공시가 meta_amendment(고배당기업 형식 재공시)이면 실제 계획 본문이 없으므로
    # plan 또는 progress 카테고리의 최신 항목을 별도로 fetch해서 commitment 문장 추출에 사용.
    best_plan_item = None
    best_plan_text = ""
    if latest_category == "meta_amendment":
        candidates = items + kind_items
        best_plan_item = _select_latest_plan_item(candidates)
        if best_plan_item and best_plan_item is not latest:
            if best_plan_item.get("rcept_no"):
                try:
                    doc = await client.get_document_cached(best_plan_item["rcept_no"])
                    best_plan_text = doc.get("text", "")
                except DartClientError as exc:
                    warnings.append(f"실계획 본문 조회 실패: {exc.status}")
            elif best_plan_item.get("acptno"):
                try:
                    html = await client.kind_fetch_document(best_plan_item["acptno"])
                    best_plan_text = _kind_html_to_text(html)
                except DartClientError as exc:
                    warnings.append(f"실계획 KIND 본문 조회 실패: {exc.status}")

    highlight_source_text = best_plan_text or latest_text
    highlight_source_length = len(highlight_source_text)
    highlights = _extract_highlights(highlight_source_text, _COMMITMENT_KEYWORDS)

    # 자사주 소각 교차참조: 정책 tool이라도 최근 소각 건수·규모를 함께 보여줘
    # "약속 vs 이행"의 한 축을 드러낸다.
    treasury_cross_ref: dict[str, Any] = {}
    try:
        from open_proxy_mcp.services.treasury_share import build_treasury_share_payload
        ts_payload = await build_treasury_share_payload(
            selected.get("corp_name", company_query),
            scope="summary",
            lookback_months=24,
        )
        full_summary = ts_payload.get("data", {}).get("summary", {}) or {}
        # cancelation_count는 별도 "자기주식소각결정" 공시 건수. 일부 기업은 별도 공시 없이
        # 취득결정 공시의 `aq_pp`(취득목적)에 "소각"을 명시하므로 `acquisition_for_cancelation_count`도 함께 노출.
        treasury_cross_ref = {
            "cancelation_decision_count_24m": full_summary.get("cancelation_count", 0),
            "acquisition_count_24m": full_summary.get("acquisition_count", 0),
            "acquisition_for_cancelation_count_24m": full_summary.get("acquisition_for_cancelation_count", 0),
            "acquisition_for_cancelation_amount_krw_24m": full_summary.get("acquisition_for_cancelation_amount_total_krw", 0),
            "trust_contract_count_24m": full_summary.get("trust_contract_count", 0),
            "note": "최근 24개월 자사주 이벤트 요약. 상세는 `treasury_share`로 확인.",
        }
    except Exception:
        pass
    if not highlights:
        if highlight_source_length < 500:
            warnings.append(f"원본 공시 본문이 매우 얇다(text_length={highlight_source_length}). PDF 첨부 중심 공시일 가능성이 높으니 viewer_url로 DART/KIND 뷰어에서 직접 확인한다.")
        else:
            warnings.append("원문 텍스트는 확보됐으나 commitment 키워드 매칭 문장이 없다. viewer_url로 원문 구조 확인 필요.")

    # 사건 발견 + 파싱 결과 메타.
    # latest 본문 텍스트가 비어 있거나 highlight 추출 실패면 partial 신호로 간주.
    parsing_failures = 0
    if items or kind_items:
        # 본문 텍스트가 너무 얇으면 파싱 실패로 카운트
        if highlight_source_length < 500 and not highlights:
            parsing_failures = 1
    filing_meta = build_filing_meta(
        filing_count=len(items) + len(kind_items),
        parsing_failures=parsing_failures,
    )

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
        **filing_meta,
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
            "category": latest_category,
        },
        "available_scopes": sorted(_SUPPORTED_SCOPES),
    }
    if best_plan_item and best_plan_item is not latest:
        data["latest_plan"] = {
            "rcept_no": best_plan_item.get("rcept_no", ""),
            "acptno": best_plan_item.get("acptno", ""),
            "disclosure_date": best_plan_item.get("rcept_dt", best_plan_item.get("disclosure_date", "")),
            "report_name": _item_report_name(best_plan_item),
            "category": _classify_value_up_item(_item_report_name(best_plan_item)),
            "note": "최신 공시가 고배당기업 표시 등 형식 재공시라 실제 계획 본문을 담은 가장 최신 공시를 별도 표시한다.",
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
        data["highlight_source_text_length"] = highlight_source_length
    if scope in {"summary", "commitments"} and treasury_cross_ref:
        data["treasury_cross_ref"] = treasury_cross_ref

    data["usage"] = build_usage(client.api_call_snapshot() - _calls_start)

    return ToolEnvelope(
        tool="value_up",
        status=status_from_filing_meta(filing_meta),
        subject=selected.get("corp_name", company_query),
        warnings=warnings,
        data=data,
        evidence_refs=_build_value_up_evidence(latest, latest_source, source_type, best_plan_item),
        next_actions=[
            "commitments scope로 주주환원/ROE 관련 문장 확인" if scope == "summary" else "dividend, ownership_structure와 함께 보면 주주환원 맥락이 더 잘 보인다.",
        ],
    ).to_dict()
