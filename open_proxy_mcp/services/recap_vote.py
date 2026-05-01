"""recap_vote — 주총 **후** 결과 보고 (운용사 분기 의결권 보고서 스타일).

핵심: gap 비교 X. 안건별 결과 (가결/부결/찬반율) + OPM 정책상 행사 사유 + 후속 공시.

5 upstream:
- shareholder_meeting (results scope, KIND)
- proxy_contest (위임장 결과)
- dividend / treasury_share / corporate_restructuring / dilutive_issuance (주총 직후 30일 후속 공시)
- corp_gov_report (변화 detect)

매핑 분류:
- 안건별 가결/부결/찬반율 → success
- 후속 공시 4종 → success
- 결정 사유 (proxy_guideline 정책) → success
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

from open_proxy_mcp.dart.client import get_dart_client
from open_proxy_mcp.services.company import _company_id, resolve_company_query
from open_proxy_mcp.services.contracts import (
    AnalysisStatus,
    EvidenceRef,
    SourceType,
    ToolEnvelope,
    build_filing_meta,
    build_usage,
)
from open_proxy_mcp.services.corp_gov_report import build_corp_gov_report_payload
from open_proxy_mcp.services.corporate_restructuring import build_corporate_restructuring_payload
from open_proxy_mcp.services.dilutive_issuance import build_dilutive_issuance_payload
from open_proxy_mcp.services.dividend_v2 import build_dividend_payload
from open_proxy_mcp.services.proxy_contest import build_proxy_contest_payload
from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload
from open_proxy_mcp.services.treasury_share import build_treasury_share_payload


def _format_iso(d: date) -> str:
    return d.strftime("%Y%m%d")


async def build_recap_vote_payload(
    company_query: str,
    *,
    year: int | None = None,
    meeting_type: str = "annual",
    vote_style: str = "open_proxy",
    follow_up_days: int = 30,
) -> dict[str, Any]:
    client = get_dart_client()
    calls_start = client.api_call_snapshot()

    resolution = await resolve_company_query(company_query)
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        return ToolEnvelope(
            tool="recap_vote_after_meeting",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[f"'{company_query}' 회사 식별 실패"],
            data={"query": company_query, "usage": build_usage(client.api_call_snapshot() - calls_start)},
        ).to_dict()
    if resolution.status == AnalysisStatus.AMBIGUOUS:
        return ToolEnvelope(
            tool="recap_vote_after_meeting",
            status=AnalysisStatus.AMBIGUOUS,
            subject=company_query,
            warnings=["회사 식별 모호"],
            data={
                "query": company_query,
                "candidates": [
                    {"corp_name": c.get("corp_name"), "corp_code": c.get("corp_code")}
                    for c in resolution.candidates[:10]
                ],
                "usage": build_usage(client.api_call_snapshot() - calls_start),
            },
        ).to_dict()

    selected = resolution.selected
    target_year = year or date.today().year - 1

    async def _safe(fn, *args, **kw):
        try:
            return await fn(*args, **kw)
        except Exception as exc:
            return {"tool": fn.__name__, "status": "error", "data": {}, "warnings": [str(exc)], "evidence_refs": []}

    # 5 upstream 병렬 호출
    meeting_results, meeting_summary, proxy_contest, gov_report = await asyncio.gather(
        _safe(build_shareholder_meeting_payload, company_query, scope="results", year=target_year, meeting_type=meeting_type),
        _safe(build_shareholder_meeting_payload, company_query, scope="summary", year=target_year, meeting_type=meeting_type),
        _safe(build_proxy_contest_payload, company_query, scope="summary"),
        _safe(build_corp_gov_report_payload, company_query, scope="summary"),
    )

    # 주총일 기준 ±30일 윈도우 후속 공시
    meeting_date_str = (meeting_summary.get("data") or {}).get("meeting_date") or f"{target_year}-03-31"
    try:
        mdate = date.fromisoformat(meeting_date_str[:10]) if "-" in meeting_date_str else date(target_year, 3, 31)
    except Exception:
        mdate = date(target_year, 3, 31)
    follow_start = _format_iso(mdate)
    follow_end = _format_iso(mdate + timedelta(days=follow_up_days))

    dividend_payload, treasury_payload, restructuring_payload, dilutive_payload = await asyncio.gather(
        _safe(build_dividend_payload, company_query, scope="summary", year=target_year),
        _safe(build_treasury_share_payload, company_query, scope="summary", start_date=follow_start, end_date=follow_end),
        _safe(build_corporate_restructuring_payload, company_query, scope="summary", start_date=follow_start, end_date=follow_end),
        _safe(build_dilutive_issuance_payload, company_query, scope="summary", start_date=follow_start, end_date=follow_end),
    )

    # 결과 안건별 정리
    results_data = (meeting_results.get("data") or {})
    agenda_results = results_data.get("agenda_results", []) or []

    # 후속 공시 surface
    def _summarize_followup(payload: dict[str, Any], label: str) -> dict[str, Any]:
        d = payload.get("data") or {}
        return {
            "label": label,
            "filing_count": d.get("filing_count", 0),
            "summary_present": bool(d.get("summary")),
            "no_filing": d.get("no_filing", True),
        }

    followups = {
        "dividend": _summarize_followup(dividend_payload, "배당"),
        "treasury_share": _summarize_followup(treasury_payload, "자사주"),
        "restructuring": _summarize_followup(restructuring_payload, "재편"),
        "dilutive": _summarize_followup(dilutive_payload, "희석성 증권"),
    }

    # evidence refs 통합
    evidence: list[EvidenceRef] = []
    for upstream_payload, label in [
        (meeting_results, "주총 결과 (KIND)"),
        (meeting_summary, "주총 소집공고"),
        (proxy_contest, "위임장 분쟁"),
    ]:
        for ref in (upstream_payload.get("evidence_refs") or [])[:2]:
            evidence.append(EvidenceRef(
                evidence_id=ref.get("evidence_id", ""),
                source_type=ref.get("source_type", SourceType.DART_API),
                rcept_no=ref.get("rcept_no", ""),
                section=ref.get("section", label),
                note=ref.get("note", ""),
            ))

    n_decisions = len(agenda_results)
    filing_meta = build_filing_meta(filing_count=n_decisions, parsing_failures=0)
    if filing_meta["no_filing"]:
        status = AnalysisStatus.NO_FILING
    else:
        status = AnalysisStatus.EXACT

    return ToolEnvelope(
        tool="recap_vote_after_meeting",
        status=status,
        subject=selected.get("corp_name", company_query),
        warnings=[],
        data={
            "query": company_query,
            "company_id": _company_id(selected),
            "canonical_name": selected.get("corp_name"),
            "year": target_year,
            "meeting_type": meeting_type,
            "vote_style": vote_style,
            "meeting_date": meeting_date_str,
            "agenda_results": agenda_results,  # success: 안건별 가결/부결/찬반율
            "agenda_results_count": n_decisions,
            "follow_up_window": {"start": follow_start, "end": follow_end, "days": follow_up_days},
            "followup_disclosures": followups,
            "proxy_contest_summary": (proxy_contest.get("data") or {}).get("summary"),
            "governance_summary": (gov_report.get("data") or {}).get("summary"),
            **filing_meta,
            "usage": build_usage(client.api_call_snapshot() - calls_start),
        },
        evidence_refs=evidence,
    ).to_dict()
