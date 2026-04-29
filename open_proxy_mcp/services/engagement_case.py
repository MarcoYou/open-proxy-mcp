"""v2 prepare_engagement_case action service."""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any

from open_proxy_mcp.services.contracts import AnalysisStatus, EvidenceRef, ToolEnvelope
from open_proxy_mcp.services.date_utils import format_yyyymmdd, resolve_date_window
from open_proxy_mcp.services.ownership_structure import build_ownership_structure_payload
from open_proxy_mcp.services.proxy_contest import build_proxy_contest_payload
from open_proxy_mcp.services.value_up_v2 import build_value_up_payload


def _merge_status(*statuses: str) -> str:
    """NO_FILING은 EXACT 동급(PARTIAL 아래)."""
    if any(status == AnalysisStatus.ERROR for status in statuses):
        return AnalysisStatus.ERROR
    if any(status == AnalysisStatus.REQUIRES_REVIEW for status in statuses):
        return AnalysisStatus.REQUIRES_REVIEW
    if any(status == AnalysisStatus.CONFLICT for status in statuses):
        return AnalysisStatus.CONFLICT
    if any(status == AnalysisStatus.PARTIAL for status in statuses):
        return AnalysisStatus.PARTIAL
    if any(status == AnalysisStatus.AMBIGUOUS for status in statuses):
        return AnalysisStatus.AMBIGUOUS
    if statuses and all(status == AnalysisStatus.NO_FILING for status in statuses):
        return AnalysisStatus.NO_FILING
    return AnalysisStatus.EXACT


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        cleaned = (value or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        ordered.append(cleaned)
    return ordered


def _dedupe_evidence(refs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    ordered: list[dict[str, Any]] = []
    for ref in refs:
        evidence_id = ref.get("evidence_id", "")
        if evidence_id in seen:
            continue
        seen.add(evidence_id)
        ordered.append(ref)
    return ordered


def _value_up_snapshot(value_payload: dict[str, Any]) -> dict[str, Any]:
    data = value_payload.get("data", {}) or {}
    latest = data.get("latest", {}) or {}
    items = data.get("items", []) or []
    highlights = data.get("highlights", []) or []
    return {
        "latest": latest,
        "filing_count": len(items) or (1 if latest else 0),
        "highlights": highlights[:6],
        "items": items[:8],
    }


async def build_engagement_case_payload(
    company_query: str,
    *,
    year: int | None = None,
    start_date: str = "",
    end_date: str = "",
    lookback_months: int = 12,
) -> dict[str, Any]:
    target_year = year or date.today().year
    default_end = date(target_year, 12, 31) if year else date.today()
    window_start, window_end, window_warnings = resolve_date_window(
        start_date=start_date,
        end_date=end_date,
        default_end=default_end,
        lookback_months=lookback_months,
    )
    window_start_ymd = format_yyyymmdd(window_start)
    window_end_ymd = format_yyyymmdd(window_end)

    ownership_payload, proxy_payload, value_payload = await asyncio.gather(
        build_ownership_structure_payload(
            company_query,
            scope="control_map",
            as_of_date=window_end_ymd,
            start_date=start_date,
            end_date=end_date,
        ),
        build_proxy_contest_payload(
            company_query,
            scope="summary",
            year=window_end.year,
            start_date=start_date,
            end_date=end_date,
            lookback_months=lookback_months,
        ),
        build_value_up_payload(
            company_query,
            scope="summary",
            year=year or window_end.year,
            start_date=start_date,
            end_date=end_date,
        ),
    )

    if ownership_payload.get("status") in {AnalysisStatus.ERROR, AnalysisStatus.AMBIGUOUS}:
        return ToolEnvelope(
            tool="prepare_engagement_case",
            status=ownership_payload.get("status", AnalysisStatus.ERROR),
            subject=ownership_payload.get("subject", company_query),
            warnings=ownership_payload.get("warnings", []),
            data=ownership_payload.get("data", {}),
            evidence_refs=ownership_payload.get("evidence_refs", []),
            next_actions=[],
        ).to_dict()

    merged_status = _merge_status(
        ownership_payload.get("status"),
        proxy_payload.get("status"),
        value_payload.get("status"),
    )

    ownership_data = ownership_payload.get("data", {}) or {}
    proxy_data = proxy_payload.get("data", {}) or {}
    value_data = value_payload.get("data", {}) or {}
    control_map = ownership_data.get("control_map", {}) or {}
    ownership_summary = ownership_data.get("summary", {}) or {}
    proxy_summary = proxy_data.get("summary", {}) or {}
    proxy_players = proxy_data.get("players", {}) or {}
    value_snapshot = _value_up_snapshot(value_payload)

    issue_points: list[str] = []
    top_holder = ownership_summary.get("top_holder", {}) or {}
    if top_holder:
        issue_points.append(f"명부상 최대주주: {top_holder.get('name', '-') or '-'} {top_holder.get('ownership_pct', 0):.2f}%")
    if ownership_summary.get("related_total_pct") is not None:
        issue_points.append(f"명부상 특수관계인 합계: {ownership_summary.get('related_total_pct', 0):.2f}%")
    if ownership_summary.get("treasury_pct") is not None:
        issue_points.append(f"자사주 비중: {ownership_summary.get('treasury_pct', 0):.2f}%")
    for observation in control_map.get("observations", []) or []:
        issue_points.append(observation)

    contest_points: list[str] = [
        f"위임장/공개매수 공시: {proxy_summary.get('proxy_filing_count', 0)}건",
        f"주주측 문서: {proxy_summary.get('shareholder_side_count', 0)}건",
        f"소송/분쟁 공시: {proxy_summary.get('litigation_count', 0)}건",
        f"능동적 5% 시그널: {proxy_summary.get('active_signal_count', 0)}건",
    ]
    if proxy_players.get("company_side_filers"):
        contest_points.append(f"회사측 제출인: {', '.join(proxy_players.get('company_side_filers', []))}")
    if proxy_players.get("shareholder_side_filers"):
        contest_points.append(f"주주측 제출인: {', '.join(proxy_players.get('shareholder_side_filers', []))}")
    if proxy_players.get("active_external_blocks"):
        contest_points.append(f"명부와 안 겹치는 능동 5% 블록: {', '.join(proxy_players.get('active_external_blocks', []))}")
    if proxy_players.get("active_overlap_blocks"):
        contest_points.append(f"명부와 겹치는 능동 5% 블록: {', '.join(proxy_players.get('active_overlap_blocks', []))}")

    return ToolEnvelope(
        tool="prepare_engagement_case",
        status=merged_status,
        subject=ownership_data.get("canonical_name", company_query),
        warnings=[
            *window_warnings,
            *ownership_payload.get("warnings", []),
            *proxy_payload.get("warnings", []),
            *value_payload.get("warnings", []),
        ],
        data={
            "query": company_query,
            "company_id": ownership_data.get("company_id", ""),
            "canonical_name": ownership_data.get("canonical_name", ""),
            "identifiers": ownership_data.get("identifiers", {}),
            "window": {
                "start_date": window_start_ymd,
                "end_date": window_end_ymd,
                "anchor_year": window_end.year,
                "lookback_months": lookback_months,
            },
            "quality": {
                "ownership_status": ownership_payload.get("status", ""),
                "proxy_status": proxy_payload.get("status", ""),
                "value_up_status": value_payload.get("status", ""),
                "proxy_has_contest_signal": proxy_summary.get("has_contest_signal", False),
                "value_up_filing_count": value_snapshot.get("filing_count", 0),
            },
            "issue_framing": {
                "points": _dedupe_strings(issue_points)[:15],
                "control_flags": control_map.get("flags", {}),
                "control_observations": control_map.get("observations", [])[:10],
            },
            "contest_signals": {
                "summary": {
                    "proxy_filing_count": proxy_summary.get("proxy_filing_count", 0),
                    "shareholder_side_count": proxy_summary.get("shareholder_side_count", 0),
                    "litigation_count": proxy_summary.get("litigation_count", 0),
                    "active_signal_count": proxy_summary.get("active_signal_count", 0),
                    "has_contest_signal": proxy_summary.get("has_contest_signal", False),
                },
                "players": proxy_players,
                "points": _dedupe_strings(contest_points)[:15],
            },
            "return_context": value_snapshot,
            "key_flags": _dedupe_strings(
                [
                    *control_map.get("observations", []),
                    *contest_points,
                    *(value_data.get("highlights", []) or []),
                ]
            )[:20],
        },
        evidence_refs=_dedupe_evidence([
            *(ownership_payload.get("evidence_refs", []) or []),
            *(proxy_payload.get("evidence_refs", []) or []),
            *(value_payload.get("evidence_refs", []) or []),
        ]),
        next_actions=[],
    ).to_dict()
