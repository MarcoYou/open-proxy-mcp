"""v2 proxy_contest facade 서비스."""

from __future__ import annotations

from datetime import date, timedelta
import re
from typing import Any

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.services.company import _company_id, resolve_company_query
from open_proxy_mcp.services.contracts import AnalysisStatus, EvidenceRef, SourceType, ToolEnvelope
from open_proxy_mcp.services.date_utils import format_yyyymmdd, resolve_date_window
from open_proxy_mcp.services.ownership_structure import (
    _build_control_map,
    _latest_block_rows,
    _major_holders_rows,
    _normalize_entity_name,
    _related_total,
    _top_holder_summary,
    _treasury_snapshot,
)
from open_proxy_mcp.tools.formatters import _parse_holding_purpose, _parse_holding_purpose_from_document

_SUPPORTED_SCOPES = {"summary", "fight", "litigation", "signals", "timeline", "vote_math"}
_PROXY_KEYWORDS = (
    "의결권대리행사권유",
    "위임장권유참고서류",
    "의결권대리행사참고서류",
    "공개매수신고서",
    "공개매수설명서",
    "공개매수결과보고서",
    "공개매수에관한의견표명서",
)
_LITIGATION_KEYWORDS = (
    "소송등의제기",
    "소송등의신청",
    "소송등의판결",
    "소송등의결정",
    "경영권분쟁소송",
)


def _strip_corp_name(name: str) -> str:
    return re.sub(r"[\(（]?주[\)）]?$|㈜$|주식회사\s*$", "", (name or "").strip()).strip()


def _is_company_side(filer_name: str, corp_name: str) -> bool:
    left = _strip_corp_name(filer_name)
    right = _strip_corp_name(corp_name)
    return bool(left and right and (left == right or right in left))


def _window_bounds(
    target_year: int | None,
    *,
    start_date: str = "",
    end_date: str = "",
    lookback_months: int = 12,
) -> tuple[str, str, int, list[str]]:
    if start_date or end_date:
        window_start, window_end, warnings = resolve_date_window(
            start_date=start_date,
            end_date=end_date,
            default_end=date.today(),
            lookback_months=lookback_months,
        )
        return format_yyyymmdd(window_start), format_yyyymmdd(window_end), window_end.year, warnings

    today = date.today()
    if target_year and target_year < today.year:
        window_end = date(target_year, 12, 31)
    else:
        window_end = today
    window_start = window_end - timedelta(days=max(30, lookback_months * 30))
    return format_yyyymmdd(window_start), format_yyyymmdd(window_end), window_end.year, []


def _unique_nonempty(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        cleaned = (value or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        ordered.append(cleaned)
    return ordered


def _normalize_date_key(value: str) -> str:
    return re.sub(r"[^\d]", "", value or "")


def _in_window(value: str, bgn_de: str, end_de: str) -> bool:
    date_key = _normalize_date_key(value)
    return bool(date_key) and bgn_de <= date_key <= end_de


async def _proxy_items(corp_code: str, corp_name: str, bgn_de: str, end_de: str) -> tuple[list[dict[str, Any]], str | None]:
    client = get_dart_client()
    try:
        result = await client.search_filings(
            corp_code=corp_code,
            bgn_de=bgn_de,
            end_de=end_de,
            pblntf_ty="D",
            page_count=100,
        )
    except DartClientError as exc:
        return [], f"위임장/공개매수 공시 조회 실패: {exc.status}"
    items = [
        item for item in result.get("list", [])
        if any(keyword in (item.get("report_nm") or "") for keyword in _PROXY_KEYWORDS)
    ]
    rows = []
    for item in items:
        rows.append({
            "rcept_no": item.get("rcept_no", ""),
            "disclosure_date": item.get("rcept_dt", ""),
            "report_name": item.get("report_nm", ""),
            "filer_name": item.get("flr_nm", ""),
            "side": "company" if _is_company_side(item.get("flr_nm", ""), corp_name) else "shareholder",
        })
    rows.sort(key=lambda row: (row["disclosure_date"], row["rcept_no"]), reverse=True)
    return rows, None


async def _litigation_items(corp_code: str, bgn_de: str, end_de: str) -> tuple[list[dict[str, Any]], str | None]:
    client = get_dart_client()
    all_rows: list[dict[str, Any]] = []
    for pblntf_ty in ("I", "B"):
        try:
            result = await client.search_filings(
                corp_code=corp_code,
                bgn_de=bgn_de,
                end_de=end_de,
                pblntf_ty=pblntf_ty,
                page_count=100,
            )
        except DartClientError:
            continue
        for item in result.get("list", []):
            if any(keyword in (item.get("report_nm") or "").replace(" ", "") for keyword in _LITIGATION_KEYWORDS):
                all_rows.append({
                    "rcept_no": item.get("rcept_no", ""),
                    "disclosure_date": item.get("rcept_dt", ""),
                    "report_name": item.get("report_nm", ""),
                    "filer_name": item.get("flr_nm", ""),
                })
    all_rows.sort(key=lambda row: (row["disclosure_date"], row["rcept_no"]), reverse=True)
    return all_rows, None


async def _block_signals(corp_code: str) -> tuple[list[dict[str, Any]], str | None]:
    client = get_dart_client()
    try:
        result = await client.get_block_holders(corp_code)
    except DartClientError as exc:
        return [], f"5% 대량보유 공시 조회 실패: {exc.status}"
    latest_by_reporter: dict[str, dict[str, Any]] = {}
    for item in result.get("list", []):
        reporter = item.get("repror", "").strip()
        if not reporter:
            continue
        if reporter not in latest_by_reporter or item.get("rcept_dt", "") > latest_by_reporter[reporter].get("rcept_dt", ""):
            latest_by_reporter[reporter] = item
    rows: list[dict[str, Any]] = []
    for reporter, item in latest_by_reporter.items():
        purpose = _parse_holding_purpose(item.get("report_tp", ""), item.get("report_resn", ""))
        if purpose in ("불명", "단순투자/일반투자") and item.get("rcept_no"):
            try:
                doc = await client.get_document_cached(item["rcept_no"])
                parsed = _parse_holding_purpose_from_document(doc.get("html", "") or "")
                if parsed != "불명":
                    purpose = parsed
            except Exception:
                pass
        rows.append({
            "reporter": reporter,
            "report_date": item.get("rcept_dt", ""),
            "rcept_no": item.get("rcept_no", ""),
            "ownership_pct": float(item.get("stkrt", 0) or 0),
            "purpose": purpose,
        })
    rows.sort(key=lambda row: (row["report_date"], row["rcept_no"]), reverse=True)
    return rows, None


async def _control_context(corp_code: str, company_query: str, target_year: int | None) -> tuple[dict[str, Any], list[str]]:
    client = get_dart_client()
    warnings: list[str] = []
    bsns_year = str((target_year or date.today().year) - 1)

    try:
        major = await client.get_major_shareholders(corp_code, bsns_year)
    except DartClientError as exc:
        warnings.append(f"지분 명부 API 조회 실패: {exc.status}")
        return {
            "year": bsns_year,
            "top_holder": {},
            "related_total_pct": 0.0,
            "treasury_pct": 0.0,
            "control_map": {},
        }, warnings

    try:
        stock_total = await client.get_stock_total(corp_code, bsns_year)
    except DartClientError as exc:
        stock_total = {"list": []}
        warnings.append(f"주식총수 API 조회 실패: {exc.status}")

    try:
        treasury_data = await client.get_treasury_stock(corp_code, bsns_year)
    except DartClientError as exc:
        treasury_data = {"list": []}
        warnings.append(f"자사주 API 조회 실패: {exc.status}")

    major_rows = _major_holders_rows(major)
    latest_blocks, _, block_warning = await _latest_block_rows(corp_code)
    if block_warning:
        warnings.append(block_warning)
    treasury_snapshot = _treasury_snapshot(stock_total, treasury_data)
    control_map = _build_control_map(major_rows, latest_blocks, treasury_snapshot)
    return {
        "year": bsns_year,
        "top_holder": _top_holder_summary(major_rows),
        "related_total_pct": _related_total(major_rows),
        "treasury_pct": treasury_snapshot["treasury_pct"],
        "control_map": control_map,
    }, warnings


def _signal_actor_side(row: dict[str, Any]) -> str:
    if row.get("registry_overlap"):
        return "registry_overlap"
    if row.get("active_purpose"):
        return "external_active_block"
    return "external_or_passive"


def _fight_actor_group(row: dict[str, Any], active_external_names: set[str], overlap_names: set[str]) -> str:
    if row.get("side") == "company":
        return "company"
    filer_key = _normalize_entity_name(row.get("filer_name", ""))
    if filer_key in active_external_names:
        return "external_active_block"
    if filer_key in overlap_names:
        return "registry_overlap"
    return "shareholder"


def _unsupported_scope_payload(company_query: str, scope: str) -> dict[str, Any]:
    if scope == "vote_math":
        return ToolEnvelope(
            tool="proxy_contest",
            status=AnalysisStatus.REQUIRES_REVIEW,
            subject=company_query,
            warnings=["vote_math는 release_v2 후반 단계에서 열 예정이다. 현재는 fight/litigation/signals만 지원한다."],
            data={"query": company_query, "scope": scope},
        ).to_dict()
    return ToolEnvelope(
        tool="proxy_contest",
        status=AnalysisStatus.REQUIRES_REVIEW,
        subject=company_query,
        warnings=[f"`{scope}` scope는 아직 지원하지 않는다."],
        data={"query": company_query, "scope": scope},
    ).to_dict()


async def build_proxy_contest_payload(
    company_query: str,
    *,
    scope: str = "summary",
    year: int | None = None,
    start_date: str = "",
    end_date: str = "",
    lookback_months: int = 12,
) -> dict[str, Any]:
    if scope not in _SUPPORTED_SCOPES:
        return _unsupported_scope_payload(company_query, scope)
    if scope == "vote_math":
        return _unsupported_scope_payload(company_query, scope)

    resolution = await resolve_company_query(company_query)
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        return ToolEnvelope(
            tool="proxy_contest",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[f"'{company_query}'에 해당하는 회사를 찾지 못했다."],
            data={"query": company_query, "scope": scope},
        ).to_dict()
    if resolution.status == AnalysisStatus.AMBIGUOUS:
        return ToolEnvelope(
            tool="proxy_contest",
            status=AnalysisStatus.AMBIGUOUS,
            subject=company_query,
            warnings=["회사 식별이 애매해 분쟁 공시를 자동 선택하지 않았다."],
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
    bgn_de, end_de, window_year, window_warnings = _window_bounds(
        year,
        start_date=start_date,
        end_date=end_date,
        lookback_months=lookback_months,
    )
    warnings: list[str] = list(window_warnings)

    proxy_rows, proxy_warning = await _proxy_items(selected["corp_code"], selected.get("corp_name", ""), bgn_de, end_de)
    litigation_rows, lit_warning = await _litigation_items(selected["corp_code"], bgn_de, end_de)
    signal_rows, signal_warning = await _block_signals(selected["corp_code"])
    control_context, control_warnings = await _control_context(selected["corp_code"], company_query, window_year)

    for warning in (proxy_warning, lit_warning, signal_warning, *control_warnings):
        if warning:
            warnings.append(warning)

    control_map = control_context.get("control_map", {})
    overlap_names = {
        _normalize_entity_name(row.get("reporter", ""))
        for row in control_map.get("overlap_blocks", [])
        if _normalize_entity_name(row.get("reporter", ""))
    }
    active_external_names = {
        _normalize_entity_name(row.get("reporter", ""))
        for row in control_map.get("active_non_overlap_blocks", [])
        if _normalize_entity_name(row.get("reporter", ""))
    }

    enriched_proxy_rows: list[dict[str, Any]] = []
    for row in proxy_rows:
        enriched_proxy_rows.append({
            **row,
            "actor_group": _fight_actor_group(row, active_external_names, overlap_names),
        })

    enriched_signal_rows: list[dict[str, Any]] = []
    for row in control_map.get("overlap_blocks", []):
        enriched_signal_rows.append({
            **row,
            "actor_side": _signal_actor_side(row),
        })
    for row in control_map.get("non_overlap_blocks", []):
        enriched_signal_rows.append({
            **row,
            "actor_side": _signal_actor_side(row),
        })
    enriched_signal_rows = [
        row for row in enriched_signal_rows
        if _in_window(row.get("report_date", ""), bgn_de, end_de)
    ]
    enriched_signal_rows.sort(key=lambda row: (row.get("report_date", ""), row.get("rcept_no", "")), reverse=True)

    activist_signals = [row for row in enriched_signal_rows if row.get("active_purpose")]
    combined_timeline = [
        *[
            {
                "date": row["disclosure_date"],
                "category": "fight",
                "actor": row["filer_name"],
                "side": row["actor_group"],
                "title": row["report_name"],
                "rcept_no": row["rcept_no"],
            }
            for row in enriched_proxy_rows
        ],
        *[
            {
                "date": row["disclosure_date"],
                "category": "litigation",
                "actor": row["filer_name"],
                "side": "litigation",
                "title": row["report_name"],
                "rcept_no": row["rcept_no"],
            }
            for row in litigation_rows
        ],
        *[
            {
                "date": row["report_date"],
                "category": "signal",
                "actor": row["reporter"],
                "side": row["actor_side"],
                "title": f"{row['reporter']} {row['purpose']}",
                "rcept_no": row["rcept_no"],
            }
            for row in activist_signals
        ],
    ]
    combined_timeline.sort(key=lambda row: (row["date"], row["rcept_no"]), reverse=True)

    company_side_filers = _unique_nonempty([row["filer_name"] for row in enriched_proxy_rows if row["side"] == "company"])
    shareholder_side_filers = _unique_nonempty([row["filer_name"] for row in enriched_proxy_rows if row["side"] == "shareholder"])
    active_external_blocks = _unique_nonempty([row["reporter"] for row in activist_signals if row.get("actor_side") == "external_active_block"])
    overlap_blocks = _unique_nonempty([row["reporter"] for row in activist_signals if row.get("actor_side") == "registry_overlap"])

    data: dict[str, Any] = {
        "query": company_query,
        "company_id": _company_id(selected),
        "canonical_name": selected.get("corp_name", ""),
        "identifiers": {
            "ticker": selected.get("stock_code", ""),
            "corp_code": selected.get("corp_code", ""),
        },
        "window": {
            "start_date": bgn_de,
            "end_date": end_de,
            "anchor_year": window_year,
            "lookback_months": lookback_months,
        },
        "summary": {
            "proxy_filing_count": len(enriched_proxy_rows),
            "shareholder_side_count": len([row for row in enriched_proxy_rows if row["side"] == "shareholder"]),
            "litigation_count": len(litigation_rows),
            "active_signal_count": len(activist_signals),
            "has_contest_signal": bool(enriched_proxy_rows or litigation_rows or activist_signals),
            "top_holder": control_context.get("top_holder", {}),
            "related_total_pct": control_context.get("related_total_pct", 0.0),
            "treasury_pct": control_context.get("treasury_pct", 0.0),
            "active_external_block_count": len(active_external_blocks),
            "active_overlap_block_count": len(overlap_blocks),
        },
        "players": {
            "company_side_filers": company_side_filers,
            "shareholder_side_filers": shareholder_side_filers,
            "active_external_blocks": active_external_blocks,
            "active_overlap_blocks": overlap_blocks,
        },
        "control_context": control_map,
        "available_scopes": ["summary", "fight", "litigation", "signals", "timeline"],
    }
    if scope in {"summary", "fight"}:
        data["fight"] = enriched_proxy_rows
    if scope in {"summary", "litigation"}:
        data["litigation"] = litigation_rows
    if scope in {"summary", "signals"}:
        data["signals"] = activist_signals
    if scope == "timeline":
        data["timeline"] = combined_timeline[:50]

    evidence_refs: list[EvidenceRef] = []
    if enriched_proxy_rows:
        evidence_refs.append(
            EvidenceRef(
                evidence_id=f"ev_proxy_{enriched_proxy_rows[0]['rcept_no']}",
                source_type=SourceType.DART_XML,
                rcept_no=enriched_proxy_rows[0]["rcept_no"],
                section="위임장/공개매수 공시",
                snippet=f"{enriched_proxy_rows[0]['report_name']} / {enriched_proxy_rows[0]['filer_name']}",
                parser="filing_search",
            )
        )
    if litigation_rows:
        evidence_refs.append(
            EvidenceRef(
                evidence_id=f"ev_litigation_{litigation_rows[0]['rcept_no']}",
                source_type=SourceType.DART_XML,
                rcept_no=litigation_rows[0]["rcept_no"],
                section="소송/분쟁 공시",
                snippet=litigation_rows[0]["report_name"],
                parser="filing_search",
            )
        )
    if activist_signals and activist_signals[0].get("rcept_no"):
        evidence_refs.append(
            EvidenceRef(
                evidence_id=f"ev_signal_{activist_signals[0]['rcept_no']}",
                source_type=SourceType.DART_XML,
                rcept_no=activist_signals[0]["rcept_no"],
                section="대량보유 상황보고",
                snippet=f"{activist_signals[0]['reporter']} / {activist_signals[0]['purpose']}",
                parser="majorstock",
            )
        )

    status = AnalysisStatus.EXACT if (enriched_proxy_rows or litigation_rows or activist_signals) else AnalysisStatus.PARTIAL
    if status == AnalysisStatus.PARTIAL:
        warnings.append("분쟁 관련 공시가 없거나 충분하지 않아 partial 상태로 표시한다.")

    return ToolEnvelope(
        tool="proxy_contest",
        status=status,
        subject=selected.get("corp_name", company_query),
        warnings=warnings,
        data=data,
        evidence_refs=evidence_refs,
        next_actions=[
            "timeline scope로 전체 이벤트 순서 확인" if scope == "summary" else "shareholder_meeting, ownership_structure와 함께 보면 표대결 맥락이 더 선명해진다.",
        ],
    ).to_dict()
