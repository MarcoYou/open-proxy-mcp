"""v2 dividend facade 서비스."""

from __future__ import annotations

from datetime import date
from typing import Any

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.services.company import _company_id, resolve_company_query
from open_proxy_mcp.services.contracts import AnalysisStatus, EvidenceRef, SourceType, ToolEnvelope
from open_proxy_mcp.services.date_utils import format_iso_date, format_yyyymmdd, parse_date_param, resolve_date_window
from open_proxy_mcp.services.filing_search import search_filings_by_report_name
from open_proxy_mcp.tools.dividend import (
    _DIV_KEYWORDS,
    _build_dividend_summary,
    _parse_dividend_decision,
    _parse_dividend_items,
)

_SUPPORTED_SCOPES = {"summary", "detail", "history", "policy_signals"}


def _year_window(end_year: int, years: int) -> list[int]:
    return list(range(end_year - years + 1, end_year + 1))


async def _search_dividend_filings(corp_code: str, start_year: int, end_year: int) -> tuple[list[dict[str, Any]], list[str], str | None]:
    filings, notices, error = await search_filings_by_report_name(
        corp_code=corp_code,
        bgn_de=f"{start_year}0101",
        end_de=f"{end_year + 1}1231",
        pblntf_tys="I",
        keywords=_DIV_KEYWORDS,
    )
    if error:
        return [], notices, f"배당결정 공시 검색 실패: {error}"
    return filings, notices, None


def _in_window(date_value: str, start_ymd: str, end_ymd: str) -> bool:
    digits = "".join(ch for ch in (date_value or "") if ch.isdigit())
    return bool(digits) and start_ymd <= digits <= end_ymd


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
    summary = _build_dividend_summary(items, "사업보고서(기말)")
    if summary:
        summary["source"] = "alotMatter"
    return summary, None


def _decisions_summary_for_year(decisions: list[dict[str, Any]], year: int) -> dict[str, Any]:
    """해당 연도 배당결정 공시를 합산해 summary 형식으로 반환.

    `alotMatter`가 비어 있을 때(사업보고서 미제출 또는 무배당 회사가 특별배당·분기배당
    결정만 공시한 경우) 확정된 배당 결정을 source of truth로 사용하기 위한 fallback.
    """

    year_decisions: list[dict[str, Any]] = []
    for item in decisions:
        bucket_year = _bucket_fiscal_year(item)
        if bucket_year == year:
            year_decisions.append(item)

    if not year_decisions:
        return {}

    cash_dps_total = sum(int(d.get("dps_common") or 0) for d in year_decisions)
    cash_dps_pref_total = sum(int(d.get("dps_preferred") or 0) for d in year_decisions)
    total_amount_mil = sum(int((d.get("total_amount") or 0)) for d in year_decisions) // 1_000_000
    special_dps = sum(int(d.get("dps_common") or 0) for d in year_decisions if d.get("has_special") or d.get("dividend_type") == "특별배당")

    return {
        "period": f"{year} 배당결정 공시 합산",
        "stlm_dt": f"{year}-12-31",
        "cash_dps": cash_dps_total,
        "cash_dps_preferred": cash_dps_pref_total,
        "stock_dps": 0,
        "special_dps": special_dps,
        "total_dps": cash_dps_total,
        "total_amount_mil": total_amount_mil,
        "payout_ratio_dart": None,
        "yield_dart": None,
        "yield_preferred_dart": None,
        "net_income_consolidated_mil": 0,
        "decision_count": len(year_decisions),
        "source": "decisions",
    }


def _bucket_fiscal_year(item: dict[str, Any]) -> int | None:
    """해당 배당결정 공시를 어느 사업연도로 집계할지 결정.

    한국 결산배당은 사업연도 말일에 귀속되지만 공시는 다음 해 2-3월에 제출된다.
    또한 2024년 이후 시행된 기준일 분리형은 record_date도 다음 해 1-4월로 밀릴 수 있다.

    규칙:
    - dividend_type == "결산배당": 사업연도 = rcept_dt 연도 - 1
      (예: rcept_dt=2024-02-22 결산배당 → 2023 사업연도)
    - 중간배당/분기배당: record_date가 사업연도 안에 있으므로 record_date 기준 연도
    - record_date와 rcept_dt 모두 없으면 None (버킷 불가)
    """

    rcept_dt = (item.get("rcept_dt") or "").strip()
    record_date = (item.get("record_date") or "").strip()
    dtype = (item.get("dividend_type") or "").strip()

    if dtype == "결산배당":
        if rcept_dt and len(rcept_dt) >= 4 and rcept_dt[:4].isdigit():
            return int(rcept_dt[:4]) - 1
        if record_date:
            # 기준일이 1-4월이면 전년도 결산으로 보정, 그 외에는 record_date 연도 사용
            digits = "".join(ch for ch in record_date if ch.isdigit())
            if len(digits) >= 6:
                year, month = int(digits[:4]), int(digits[4:6])
                return year - 1 if month <= 4 else year

    base = record_date or rcept_dt
    if not base:
        return None
    digits = "".join(ch for ch in base if ch.isdigit())
    if len(digits) < 4:
        return None
    return int(digits[:4])


def _history_rows(end_year: int, annual_summaries: dict[int, dict[str, Any]], decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    decisions_by_year: dict[int, list[dict[str, Any]]] = {}
    for item in decisions:
        year = _bucket_fiscal_year(item)
        if year is None:
            continue
        decisions_by_year.setdefault(year, []).append(item)

    history: list[dict[str, Any]] = []
    for year, summary in sorted(annual_summaries.items()):
        yearly = decisions_by_year.get(year, [])
        annual_dps = summary.get("total_dps", 0)
        if len(yearly) > 1:
            pattern = "분기/중간 포함"
        elif yearly:
            pattern = "연간배당"
        elif annual_dps:
            # alotMatter에 DPS가 잡혔으나 결정 공시가 해당 연도에 없는 경우
            # (사업보고서에만 반영된 배당이거나 결정공시 기준일이 다른 연도로 이월된 케이스)
            pattern = "연간배당 (결정 공시 없음)"
        else:
            pattern = "무배당"
        history.append({
            "year": year,
            "annual_dps": annual_dps,
            "decision_count": len(yearly),
            "payout_ratio": summary.get("payout_ratio_dart"),
            "yield_pct": summary.get("yield_dart"),
            "has_special": any(item.get("has_special") for item in yearly),
            "pattern": pattern,
        })
    return history


def _select_history_years(
    annual_summaries: dict[int, dict[str, Any]],
    *,
    requested_years: int,
) -> list[int]:
    available_years = sorted(annual_summaries.keys())
    if not available_years:
        return []
    return available_years[-requested_years:]


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
    start_date: str = "",
    end_date: str = "",
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
    explicit_start = parse_date_param(start_date)
    explicit_end = parse_date_param(end_date)
    if year:
        target_year = year
    elif explicit_end:
        target_year = explicit_end.year
    else:
        target_year = date.today().year - 1
    default_end = date(target_year, 12, 31)
    window_start, window_end, window_warnings = resolve_date_window(
        start_date=start_date,
        end_date=end_date,
        default_end=default_end,
        lookback_months=max(12, years * 12),
    )
    warnings: list[str] = list(window_warnings)
    history_start_year = window_start.year if (explicit_start or explicit_end) else (target_year - max(1, years) + 1)
    # 최근 N개 완료 사업연도를 보여주기 위해 한 해 더 넓게 본다.
    if scope == "history":
        history_start_year = min(history_start_year, target_year - max(1, years))
    year_list = list(range(history_start_year, target_year + 1))

    latest_summary, summary_warning = await _annual_summary(selected["corp_code"], target_year)
    if summary_warning:
        warnings.append(summary_warning)

    filings, filing_notices, filing_warning = await _search_dividend_filings(selected["corp_code"], year_list[0], target_year)
    warnings.extend(filing_notices)
    if filing_warning:
        warnings.append(filing_warning)
        filings = []
    details = await _decision_details(filings[:20]) if filings else []

    # alotMatter가 비어있거나 cash_dps=0이면 해당 연도 배당결정 공시 합산을 source of truth로 대체.
    if (not latest_summary or int(latest_summary.get("cash_dps") or 0) == 0) and details:
        fallback = _decisions_summary_for_year(details, target_year)
        if fallback and fallback.get("cash_dps", 0) > 0:
            latest_summary = fallback
            warnings.append(f"{target_year}년 사업보고서 배당 요약이 비어 있어 해당 연도 배당결정 공시 {fallback.get('decision_count', 0)}건을 합산해 summary를 구성했다.")
    start_ymd = format_yyyymmdd(window_start)
    end_ymd = format_yyyymmdd(window_end)
    details = [
        item for item in details
        if _in_window(item.get("rcept_dt", ""), start_ymd, end_ymd)
    ]

    annual_summaries: dict[int, dict[str, Any]] = {}
    for y in year_list:
        summary, warning = await _annual_summary(selected["corp_code"], y)
        if warning:
            warnings.append(f"{y}년 {warning}")
        if (not summary or int(summary.get("cash_dps") or 0) == 0):
            fallback = _decisions_summary_for_year(details, y)
            if fallback and fallback.get("cash_dps", 0) > 0:
                summary = fallback
        if summary:
            annual_summaries[y] = summary

    history_years = _select_history_years(
        annual_summaries,
        requested_years=max(1, years) if scope == "history" else len(annual_summaries),
    )
    selected_annual_summaries = {
        y: annual_summaries[y]
        for y in history_years
    } if history_years else annual_summaries
    history = _history_rows(target_year, selected_annual_summaries, details)
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
        "window": {
            "start_date": start_ymd,
            "end_date": end_ymd,
        },
        "history_selection": {
            "requested_years": years,
            "selected_years": history_years,
            "available_years": sorted(annual_summaries.keys()),
            "selection_basis": "recent_completed_years" if scope == "history" else "window",
        },
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
        src = latest_summary.get("source")
        if src == "alotMatter":
            evidence_refs.append(
                EvidenceRef(
                    evidence_id=f"ev_dividend_api_{selected['corp_code']}_{target_year}",
                    source_type=SourceType.DART_API,
                    section="alotMatter",
                    note=f"{selected.get('corp_name', '')} {target_year}년 사업보고서 배당 요약 (DART OpenAPI)",
                )
            )
        elif src == "decisions":
            evidence_refs.append(
                EvidenceRef(
                    evidence_id=f"ev_dividend_decisions_{selected['corp_code']}_{target_year}",
                    source_type=SourceType.DART_XML,
                    section="현금ㆍ현물배당결정 합산",
                    note=f"{target_year}년 배당결정 공시 {latest_summary.get('decision_count', 0)}건 합산",
                )
            )
    if latest_decision and latest_decision.get("rcept_no"):
        evidence_refs.append(
            EvidenceRef(
                evidence_id=f"ev_dividend_{latest_decision['rcept_no']}",
                source_type=SourceType.DART_XML,
                rcept_no=latest_decision["rcept_no"],
                rcept_dt=format_iso_date(latest_decision.get("rcept_dt", "")),
                report_nm=latest_decision.get("report_name", ""),
                section="현금ㆍ현물배당결정",
                note=f"{latest_decision.get('dividend_type', '')} / DPS {latest_decision.get('dps_common', 0):,}원",
            )
        )

    status = AnalysisStatus.EXACT if latest_summary or details else AnalysisStatus.PARTIAL
    if status == AnalysisStatus.PARTIAL:
        warnings.append("사업보고서 요약이나 배당결정 공시 일부가 비어 있어 partial 상태로 표시한다.")
    elif scope == "history" and len(history) < max(1, years):
        warnings.append("요청한 연수보다 완료 사업연도 수가 적어, 조회 가능한 최근 완료 사업연도만 반환한다.")

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
