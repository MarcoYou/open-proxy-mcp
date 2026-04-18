"""v2 ownership_structure facade 서비스."""

from __future__ import annotations

from datetime import date
import re
from typing import Any

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.services.company import _company_id, resolve_company_query
from open_proxy_mcp.services.contracts import AnalysisStatus, EvidenceRef, SourceType, ToolEnvelope
from open_proxy_mcp.tools.formatters import _parse_holding_purpose, _parse_holding_purpose_from_document

_SUPPORTED_SCOPES = {
    "summary",
    "major_holders",
    "blocks",
    "treasury",
    "control_map",
    "timeline",
}


def _to_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _to_int(value: Any) -> int:
    try:
        return int(re.sub(r"[^\d-]", "", str(value or "0")) or "0")
    except ValueError:
        return 0


def _major_holders_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in data.get("list", []):
        stock_kind = item.get("stock_knd", "보통")
        name = item.get("nm", "").strip()
        if not name or name == "계" or ("보통" not in stock_kind and stock_kind):
            continue
        rows.append({
            "name": name,
            "relation": item.get("relate", "").strip(),
            "shares": _to_int(item.get("trmend_posesn_stock_co", "0")),
            "ownership_pct": _to_float(item.get("trmend_posesn_stock_qota_rt", "0")),
            "settlement_date": item.get("stlm_dt", ""),
        })
    rows.sort(key=lambda row: row["ownership_pct"], reverse=True)
    return rows


def _top_holder_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    return rows[0]


def _related_total(rows: list[dict[str, Any]]) -> float:
    return round(sum(row["ownership_pct"] for row in rows), 2)


async def _latest_block_rows(corp_code: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    client = get_dart_client()
    try:
        data = await client.get_block_holders(corp_code)
    except DartClientError as exc:
        return [], [], f"대량보유 공시 조회 실패: {exc.status}"

    latest_by_reporter: dict[str, dict[str, Any]] = {}
    for item in data.get("list", []):
        reporter = item.get("repror", "").strip()
        if not reporter:
            continue
        if reporter not in latest_by_reporter or item.get("rcept_dt", "") > latest_by_reporter[reporter].get("rcept_dt", ""):
            latest_by_reporter[reporter] = item

    latest_rows: list[dict[str, Any]] = []
    timeline_rows: list[dict[str, Any]] = []
    for item in data.get("list", []):
        timeline_rows.append({
            "reporter": item.get("repror", "").strip(),
            "report_date": item.get("rcept_dt", ""),
            "rcept_no": item.get("rcept_no", ""),
            "ownership_pct": _to_float(item.get("stkrt", 0)),
            "purpose": _parse_holding_purpose(item.get("report_tp", ""), item.get("report_resn", "")),
            "report_name": item.get("report_tp", ""),
        })
    timeline_rows.sort(key=lambda row: (row["report_date"], row["rcept_no"]), reverse=True)

    for reporter, item in latest_by_reporter.items():
        purpose = _parse_holding_purpose(item.get("report_tp", ""), item.get("report_resn", ""))
        rcept_no = item.get("rcept_no", "")
        if purpose in ("불명", "단순투자/일반투자") and rcept_no:
            try:
                doc = await client.get_document_cached(rcept_no)
                parsed = _parse_holding_purpose_from_document(doc.get("html", "") or "")
                if parsed != "불명":
                    purpose = parsed
            except Exception:
                pass
        latest_rows.append({
            "reporter": reporter,
            "report_date": item.get("rcept_dt", ""),
            "rcept_no": rcept_no,
            "ownership_pct": _to_float(item.get("stkrt", 0)),
            "purpose": purpose,
            "report_type": item.get("report_tp", ""),
            "report_reason": item.get("report_resn", ""),
        })
    latest_rows.sort(key=lambda row: row["ownership_pct"], reverse=True)
    return latest_rows, timeline_rows, None


def _treasury_snapshot(stock_total: dict[str, Any], treasury_data: dict[str, Any]) -> dict[str, Any]:
    issued = 0
    treasury = 0
    distributable = 0
    for item in stock_total.get("list", []):
        se = item.get("se", "")
        if "보통" in se:
            issued = _to_int(item.get("istc_totqy", "0"))
            treasury = _to_int(item.get("tesstk_co", "0"))
            distributable = _to_int(item.get("distb_stock_co", "0"))
            break

    rows = []
    for item in treasury_data.get("list", []):
        rows.append({
            "category": item.get("se", ""),
            "begin_shares": _to_int(item.get("bsis_qy", "0")),
            "acquired_shares": _to_int(item.get("acqs_qy", "0")),
            "disposed_shares": _to_int(item.get("dsps_qy", "0")),
            "retired_shares": _to_int(item.get("inciner_qy", "0")),
            "end_shares": _to_int(item.get("trmend_qy", "0")),
        })

    return {
        "issued_shares": issued,
        "treasury_shares": treasury,
        "tradable_shares": distributable,
        "treasury_pct": round(treasury / issued * 100, 2) if issued else 0.0,
        "rows": rows,
    }


def _unsupported_scope_payload(company_query: str, scope: str) -> dict[str, Any]:
    envelope = ToolEnvelope(
        tool="ownership_structure",
        status=AnalysisStatus.REQUIRES_REVIEW,
        subject=company_query,
        warnings=[f"`{scope}` scope는 아직 v2에서 열지 않았다."],
        data={"query": company_query, "scope": scope},
    )
    return envelope.to_dict()


async def build_ownership_structure_payload(
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
            tool="ownership_structure",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[f"'{company_query}'에 해당하는 회사를 찾지 못했다."],
            data={"query": company_query, "scope": scope},
            next_actions=["company tool로 회사 식별 확인"],
        ).to_dict()
    if resolution.status == AnalysisStatus.AMBIGUOUS:
        return ToolEnvelope(
            tool="ownership_structure",
            status=AnalysisStatus.AMBIGUOUS,
            subject=company_query,
            warnings=["회사 식별이 애매해 지분 구조를 자동 선택하지 않았다."],
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
    bsns_year = str(year or (date.today().year - 1))
    client = get_dart_client()
    warnings: list[str] = []

    try:
        major = await client.get_major_shareholders(selected["corp_code"], bsns_year)
    except DartClientError as exc:
        return ToolEnvelope(
            tool="ownership_structure",
            status=AnalysisStatus.ERROR,
            subject=selected.get("corp_name", company_query),
            warnings=[f"최대주주 API 조회 실패: {exc.status}"],
            data={"query": company_query, "scope": scope, "year": bsns_year},
        ).to_dict()

    try:
        stock_total = await client.get_stock_total(selected["corp_code"], bsns_year)
    except DartClientError as exc:
        stock_total = {"list": []}
        warnings.append(f"주식총수 API 조회 실패: {exc.status}")

    try:
        treasury_data = await client.get_treasury_stock(selected["corp_code"], bsns_year)
    except DartClientError as exc:
        treasury_data = {"list": []}
        warnings.append(f"자사주 API 조회 실패: {exc.status}")

    major_rows = _major_holders_rows(major)
    latest_blocks, timeline_rows, block_warning = await _latest_block_rows(selected["corp_code"])
    if block_warning:
        warnings.append(block_warning)

    top_holder = _top_holder_summary(major_rows)
    treasury_snapshot = _treasury_snapshot(stock_total, treasury_data)
    active_signals = [
        row for row in latest_blocks
        if row["purpose"] not in ("단순투자", "단순투자/일반투자", "불명")
    ]

    data: dict[str, Any] = {
        "query": company_query,
        "company_id": _company_id(selected),
        "canonical_name": selected.get("corp_name", ""),
        "identifiers": {
            "ticker": selected.get("stock_code", ""),
            "corp_code": selected.get("corp_code", ""),
        },
        "year": bsns_year,
        "summary": {
            "top_holder": top_holder,
            "related_total_pct": _related_total(major_rows),
            "treasury_shares": treasury_snapshot["treasury_shares"],
            "treasury_pct": treasury_snapshot["treasury_pct"],
            "active_signal_count": len(active_signals),
        },
        "available_scopes": sorted(_SUPPORTED_SCOPES),
    }

    if scope in {"summary", "major_holders", "control_map"}:
        data["major_holders"] = major_rows
    if scope in {"summary", "blocks", "control_map"}:
        data["blocks"] = latest_blocks
    if scope in {"summary", "treasury"}:
        data["treasury"] = treasury_snapshot
    if scope == "timeline":
        data["timeline"] = timeline_rows[:50]
    if scope == "control_map":
        data["control_map"] = {
            "major_holders": major_rows,
            "external_blocks": latest_blocks,
            "treasury": {
                "shares": treasury_snapshot["treasury_shares"],
                "pct": treasury_snapshot["treasury_pct"],
            },
        }

    evidence_refs: list[EvidenceRef] = [
        EvidenceRef(
            evidence_id=f"ev_ownership_api_{selected['corp_code']}_{bsns_year}",
            source_type=SourceType.DART_API,
            section="hyslrSttus/stockTotqySttus",
            snippet=f"{selected.get('corp_name', '')} {bsns_year}년 정기보고서 기준 최대주주/주식총수",
            parser="dart_api",
        )
    ]
    if latest_blocks:
        first = latest_blocks[0]
        if first.get("rcept_no"):
            evidence_refs.append(
                EvidenceRef(
                    evidence_id=f"ev_block_{first['rcept_no']}",
                    source_type=SourceType.DART_XML,
                    rcept_no=first["rcept_no"],
                    section="대량보유 상황보고",
                    snippet=f"{first['reporter']} / {first['ownership_pct']}% / {first['purpose']}",
                    parser="majorstock+document",
                )
            )

    status = AnalysisStatus.EXACT if major_rows else AnalysisStatus.PARTIAL
    if not major_rows:
        warnings.append("최대주주 구조를 충분히 읽지 못해 partial 상태로 표시한다.")

    return ToolEnvelope(
        tool="ownership_structure",
        status=status,
        subject=selected.get("corp_name", company_query),
        warnings=warnings,
        data=data,
        evidence_refs=evidence_refs,
        next_actions=[
            "blocks scope로 5% 대량보유 최신 보고 확인" if scope == "summary" else "proxy_contest와 함께 보면 분쟁 맥락이 더 잘 보인다.",
        ],
    ).to_dict()

