"""v2 proxy_contest facade 서비스."""

from __future__ import annotations

from datetime import date
import re
from typing import Any

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.services.company import _company_id, resolve_company_query
from open_proxy_mcp.services.contracts import AnalysisStatus, EvidenceRef, SourceType, ToolEnvelope
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


async def _proxy_items(corp_code: str, corp_name: str, year: int) -> tuple[list[dict[str, Any]], str | None]:
    client = get_dart_client()
    try:
        result = await client.search_filings(
            corp_code=corp_code,
            bgn_de=f"{year}0101",
            end_de=f"{year}1231",
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


async def _litigation_items(corp_code: str, year: int) -> tuple[list[dict[str, Any]], str | None]:
    client = get_dart_client()
    all_rows: list[dict[str, Any]] = []
    for pblntf_ty in ("I", "B"):
        try:
            result = await client.search_filings(
                corp_code=corp_code,
                bgn_de=f"{year - 1}0101",
                end_de=f"{year}1231",
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
    target_year = year or date.today().year
    warnings: list[str] = []

    proxy_rows, proxy_warning = await _proxy_items(selected["corp_code"], selected.get("corp_name", ""), target_year)
    litigation_rows, lit_warning = await _litigation_items(selected["corp_code"], target_year)
    signal_rows, signal_warning = await _block_signals(selected["corp_code"])

    for warning in (proxy_warning, lit_warning, signal_warning):
        if warning:
            warnings.append(warning)

    activist_signals = [
        row for row in signal_rows
        if row["purpose"] not in ("단순투자", "단순투자/일반투자", "불명")
    ]
    combined_timeline = [
        *[
            {"date": row["disclosure_date"], "category": "fight", "title": row["report_name"], "rcept_no": row["rcept_no"]}
            for row in proxy_rows
        ],
        *[
            {"date": row["disclosure_date"], "category": "litigation", "title": row["report_name"], "rcept_no": row["rcept_no"]}
            for row in litigation_rows
        ],
        *[
            {"date": row["report_date"], "category": "signal", "title": f"{row['reporter']} {row['purpose']}", "rcept_no": row["rcept_no"]}
            for row in activist_signals
        ],
    ]
    combined_timeline.sort(key=lambda row: (row["date"], row["rcept_no"]), reverse=True)

    data: dict[str, Any] = {
        "query": company_query,
        "company_id": _company_id(selected),
        "canonical_name": selected.get("corp_name", ""),
        "identifiers": {
            "ticker": selected.get("stock_code", ""),
            "corp_code": selected.get("corp_code", ""),
        },
        "year": target_year,
        "summary": {
            "proxy_filing_count": len(proxy_rows),
            "shareholder_side_count": len([row for row in proxy_rows if row["side"] == "shareholder"]),
            "litigation_count": len(litigation_rows),
            "active_signal_count": len(activist_signals),
            "has_contest_signal": bool(proxy_rows or litigation_rows or activist_signals),
        },
        "available_scopes": ["summary", "fight", "litigation", "signals", "timeline"],
    }
    if scope in {"summary", "fight"}:
        data["fight"] = proxy_rows
    if scope in {"summary", "litigation"}:
        data["litigation"] = litigation_rows
    if scope in {"summary", "signals"}:
        data["signals"] = activist_signals
    if scope == "timeline":
        data["timeline"] = combined_timeline[:50]

    evidence_refs: list[EvidenceRef] = []
    if proxy_rows:
        evidence_refs.append(
            EvidenceRef(
                evidence_id=f"ev_proxy_{proxy_rows[0]['rcept_no']}",
                source_type=SourceType.DART_XML,
                rcept_no=proxy_rows[0]["rcept_no"],
                section="위임장/공개매수 공시",
                snippet=f"{proxy_rows[0]['report_name']} / {proxy_rows[0]['filer_name']}",
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

    status = AnalysisStatus.EXACT if (proxy_rows or litigation_rows or activist_signals) else AnalysisStatus.PARTIAL
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

