"""v2 dividend facade 서비스."""

from __future__ import annotations

import asyncio
from datetime import date
import time
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
from open_proxy_mcp.services.date_utils import format_iso_date, format_yyyymmdd, parse_date_param, resolve_date_window
from open_proxy_mcp.services.filing_search import search_filings_by_report_name
from open_proxy_mcp.tools.dividend import (
    _DIV_KEYWORDS,
    _build_dividend_summary,
    _parse_dividend_decision,
    _parse_dividend_items,
)

_SUPPORTED_SCOPES = {
    "summary",
    "detail",
    "history",
}

# 선배당-후결의 (2024 자본시장법 시행령 개정) 식별 키워드.
# 분기/결산마다 별도 "배당기준일결정" 또는 "주주명부폐쇄(기준일)결정"이
# 현금배당결정과 별도로 제출되면 신정관(선배당-후결의) 채택으로 분류한다.
_RECORD_DATE_NOTICE_KEYWORDS = (
    "현금ㆍ현물배당을위한주주명부폐쇄",
    "현금·현물배당을위한주주명부폐쇄",
    "현금현물배당을위한주주명부폐쇄",
    "중간(분기)배당을위한주주명부폐쇄",
    "중간배당을위한주주명부폐쇄",
    "분기배당을위한주주명부폐쇄",
    "배당기준일결정",
)

# 감액배당 (자본준비금 감소 → 이익잉여금 전입 → 배당) 식별 키워드.
# shareholder_meeting의 안건 제목과 매칭한다.
_CAPITAL_RESERVE_KEYWORDS = (
    "자본준비금",
    "이익잉여금 전입",
    "이익잉여금전입",
    "감액배당",
)


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


async def _detect_pre_dividend_post_resolution(
    corp_code: str,
    year: int,
) -> tuple[bool, list[dict[str, Any]]]:
    """선배당-후결의 (2024 신법) 채택 여부 추정.

    배당기준일결정/주주명부폐쇄 공시가 현금배당결정과 별도로 제출됐는지로 판단.
    - 별도 제출 1건 이상 → True (신정관 채택 가능성)
    - 0건 → False (전통 결산일=기준일 방식)

    같은 사업연도(year) 내 + 다음 해 1-4월 (분기배당 후속분 포함)을 넓게 본다.
    """

    bgn_de = f"{year}0101"
    end_de = f"{year + 1}0430"
    filings, _notices, error = await search_filings_by_report_name(
        corp_code=corp_code,
        bgn_de=bgn_de,
        end_de=end_de,
        pblntf_tys="I",
        keywords=_RECORD_DATE_NOTICE_KEYWORDS,
        strip_spaces=True,
    )
    if error:
        return False, []
    rows = [
        {
            "rcept_no": item.get("rcept_no", ""),
            "rcept_dt": item.get("rcept_dt", ""),
            "report_nm": item.get("report_nm", ""),
        }
        for item in filings
    ]
    return bool(rows), rows


async def _detect_capital_reserve_reduction(
    company_query: str,
    year: int,
) -> tuple[bool, list[dict[str, Any]]]:
    """감액배당 cross-link — 자본준비금 감소 안건이 주총에 상정됐는지 확인.

    `shareholder_meeting`의 agenda_summary.titles를 가져와 키워드 매칭한다.
    무한 루프 방지를 위해 import는 함수 내부에서.

    감액배당은 시간순서: 자본준비금 감소 결의 → 이익잉여금 전입 → 배당.
    공고→결과 참조 OK, 결과→공고 금지 (data_direction 규칙 준수).
    """

    try:
        from open_proxy_mcp.services.shareholder_meeting import load_shareholder_meeting_agenda_titles
    except Exception:
        return False, []

    try:
        titles = await load_shareholder_meeting_agenda_titles(
            company_query,
            meeting_type="annual",
            year=year,
        )
    except Exception:
        return False, []

    matched: list[dict[str, Any]] = []
    for title in titles:
        if not title:
            continue
        if any(kw in title for kw in _CAPITAL_RESERVE_KEYWORDS):
            matched.append({"title": title})

    return bool(matched), matched


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
        # record_date 우선 — 2024 신법 선배당-후결의 케이스 보강.
        # record_date month=12면 그 해 사업연도 결산 (예: record_date 2025-12-31 → 2025년).
        # record_date month=1-4면 전년도 결산 (예: record_date 2024-12-31 결의 다음 해 1-4월 공시 → 2024년).
        if record_date:
            digits = "".join(ch for ch in record_date if ch.isdigit())
            if len(digits) >= 6:
                year, month = int(digits[:4]), int(digits[4:6])
                if month >= 12:
                    return year  # 선배당-후결의 또는 12월 결의 → 그 해 사업연도
                if month <= 4:
                    return year - 1  # 다음 해 초 결의 → 전년 사업연도
                # 기타 (5-11월) — 비정상이지만 record_date 연도 사용
                return year
        if rcept_dt and len(rcept_dt) >= 4 and rcept_dt[:4].isdigit():
            # record_date 없을 때만 rcept_dt fallback (정정공시 패턴)
            return int(rcept_dt[:4]) - 1

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


def _quarter_label(item: dict[str, Any]) -> str:
    """배당결정 공시 → 분기 label (Q1/Q2/Q3/Q4 또는 결산/중간/특별)."""
    dtype = (item.get("dividend_type") or "").strip()
    if dtype == "결산배당":
        return "결산"
    record_date = (item.get("record_date") or "").strip()
    digits = "".join(ch for ch in record_date if ch.isdigit())
    if len(digits) >= 6:
        month = int(digits[4:6])
        if month <= 3:
            return "Q1"
        if month <= 6:
            return "Q2 (중간)"
        if month <= 9:
            return "Q3"
        return "Q4 (예비결산)"
    return dtype or "기타"


def _quarterly_breakdown(decisions: list[dict[str, Any]], year_list: list[int]) -> list[dict[str, Any]]:
    """연도별 × 분기별 DPS breakdown — 분기배당 회사 (삼성전자 등) 검증용.

    각 row: {year, quarter, dps, rcept_dt, rcept_no, base_date, type}
    """
    rows: list[dict[str, Any]] = []
    for item in decisions:
        bucket = _bucket_fiscal_year(item)
        if bucket is None or (year_list and bucket not in year_list):
            continue
        rows.append({
            "year": bucket,
            "quarter": _quarter_label(item),
            "dps_common_krw": int(item.get("dps_common") or 0),
            "dps_preferred_krw": int(item.get("dps_preferred") or 0),
            "total_amount_krw": int(item.get("total_amount") or 0),
            "yield_common_pct": item.get("yield_common"),
            "rcept_dt": item.get("rcept_dt", ""),
            "rcept_no": item.get("rcept_no", ""),
            "record_date": item.get("record_date", ""),
            "type": item.get("dividend_type", ""),
            "is_amendment": "정정" in (item.get("report_name", "") or ""),
        })
    rows.sort(key=lambda r: (r["year"], r["rcept_dt"]))
    # dedupe: same (year, quarter, record_date) → keep latest (rcept_dt) only as effective.
    # 나머지는 is_superseded=True 표시 (raw audit 보존, 합계는 effective만).
    seen: dict[tuple, int] = {}
    for i, r in enumerate(rows):
        key = (r["year"], r["quarter"], r["record_date"])
        if key in seen:
            # 이전 entry는 superseded (later iteration이 latest)
            rows[seen[key]]["is_superseded"] = True
        seen[key] = i
        r.setdefault("is_superseded", False)
    return rows


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
    total_started_at = time.perf_counter()
    timings_ms: dict[str, int] = {}

    def _mark(stage: str, started_at: float) -> None:
        timings_ms[stage] = int((time.perf_counter() - started_at) * 1000)

    if scope not in _SUPPORTED_SCOPES:
        return _unsupported_scope_payload(company_query, scope)

    client = get_dart_client()
    _calls_start = client.api_call_snapshot()
    stage_started_at = time.perf_counter()
    resolution = await resolve_company_query(company_query)
    _mark("resolve_company", stage_started_at)
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        timings_ms["total"] = int((time.perf_counter() - total_started_at) * 1000)
        return ToolEnvelope(
            tool="dividend",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[f"'{company_query}'에 해당하는 회사를 찾지 못했다."],
            data={
                "query": company_query,
                "scope": scope,
                "usage": build_usage(client.api_call_snapshot() - _calls_start),
                "timings_ms": timings_ms,
            },
        ).to_dict()
    if resolution.status == AnalysisStatus.AMBIGUOUS:
        timings_ms["total"] = int((time.perf_counter() - total_started_at) * 1000)
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
                "usage": build_usage(client.api_call_snapshot() - _calls_start),
                "timings_ms": timings_ms,
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
    # 결산배당 결정 공시는 보통 fiscal year 종료 후 다음 해 1-3월에 공시됨.
    # window_end를 다음 해 6월까지 확장해 최신 결산 결정 빠짐 방지 (정기주총 시점까지 커버).
    from datetime import date as _date_cls, timedelta as _td
    today = _date_cls.today()
    candidate_end = _date_cls(target_year + 1, 6, 30)
    default_end = candidate_end if candidate_end <= today else today
    window_start, window_end, window_warnings = resolve_date_window(
        start_date=start_date,
        end_date=end_date,
        default_end=default_end,
        # years × 12 + 6개월 buffer (분기배당 회사 첫 분기/중간 기준일 cut 방지)
        lookback_months=max(18, years * 12 + 6),
    )
    warnings: list[str] = list(window_warnings)
    history_start_year = window_start.year if (explicit_start or explicit_end) else (target_year - max(1, years) + 1)
    # 최근 N개 완료 사업연도를 보여주기 위해 한 해 더 넓게 본다.
    if scope == "history":
        history_start_year = min(history_start_year, target_year - max(1, years))
    year_list = list(range(history_start_year, target_year + 1))

    # latest_summary와 filings 검색은 independent — 병렬 호출.
    latest_summary_task = _annual_summary(selected["corp_code"], target_year)
    filings_task = _search_dividend_filings(selected["corp_code"], year_list[0], target_year)
    stage_started_at = time.perf_counter()
    (latest_summary, summary_warning), (filings, filing_notices, filing_warning) = await asyncio.gather(
        latest_summary_task, filings_task,
    )
    _mark("summary_and_filings", stage_started_at)
    if summary_warning:
        warnings.append(summary_warning)
    warnings.extend(filing_notices)
    if filing_warning:
        warnings.append(filing_warning)
        filings = []
    stage_started_at = time.perf_counter()
    details = await _decision_details(filings[:20]) if filings else []
    _mark("decision_details", stage_started_at)

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

    # 연도별 alotMatter 호출을 병렬화 (각 연도 독립).
    # target_year는 위에서 이미 호출했으므로 latest_summary 재사용해 중복 호출 방지.
    annual_summaries: dict[int, dict[str, Any]] = {}
    pending_years = [y for y in year_list if y != target_year]
    stage_started_at = time.perf_counter()
    pending_results = await asyncio.gather(*[
        _annual_summary(selected["corp_code"], y) for y in pending_years
    ]) if pending_years else []
    _mark("annual_summaries", stage_started_at)
    year_to_result: dict[int, tuple[dict[str, Any], str | None]] = {target_year: (latest_summary, None)}
    for y, res in zip(pending_years, pending_results):
        year_to_result[y] = res

    for y in year_list:
        summary, warning = year_to_result[y]
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

    # ── 메타 cross-link: 선배당-후결의 + 감액배당 ────────────────────────
    # summary/CSR/TSR scope에서만 추가 호출 발생. 나머지 scope는 cost-free.
    pre_dividend_post_resolution = False
    record_date_notices: list[dict[str, Any]] = []
    capital_reserve_reduction = False
    capital_reserve_agendas: list[dict[str, Any]] = []
    if scope in {"summary", "cash_shareholder_return", "total_shareholder_return"}:
        try:
            stage_started_at = time.perf_counter()
            pre_dividend_post_resolution, record_date_notices = await _detect_pre_dividend_post_resolution(
                selected["corp_code"], target_year
            )
            _mark("pre_dividend_detection", stage_started_at)
        except Exception as exc:
            warnings.append(f"선배당-후결의 메타 추출 실패: {exc}")
        try:
            stage_started_at = time.perf_counter()
            capital_reserve_reduction, capital_reserve_agendas = await _detect_capital_reserve_reduction(
                company_query, target_year
            )
            _mark("capital_reserve_detection", stage_started_at)
        except Exception as exc:
            warnings.append(f"감액배당 메타 추출 실패: {exc}")

    # latest_summary에 신호 메타 부착 (None safe).
    if latest_summary is not None:
        latest_summary.setdefault("pre_dividend_post_resolution", pre_dividend_post_resolution)
        latest_summary.setdefault("capital_reserve_reduction", capital_reserve_reduction)

    latest_decision = details[0] if details else None
    # 사건 발견 vs 진짜 partial 분리.
    # filing_count = 배당 결정 공시 수 + alotMatter 연간 요약 수.
    # 둘 다 0이면 진짜 무배당(no_filing) — 다만 dividend는 "사건 없음 = 무배당"이므로
    # latest_summary가 있어도 cash_dps=0이면 사실상 no_filing 신호.
    has_dividend_signal = bool(details) or bool(
        latest_summary and int(latest_summary.get("cash_dps") or 0) > 0
    )
    filing_meta = build_filing_meta(
        filing_count=len(details) + (1 if (latest_summary and int(latest_summary.get("cash_dps") or 0) > 0) else 0),
        parsing_failures=0,
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
        **filing_meta,
        "available_scopes": sorted(_SUPPORTED_SCOPES),
    }
    if scope in {"summary", "detail"}:
        # 분기배당 회사 (삼성전자 등) 3년치 = 최대 12 quarters + 결산 + 정정 → 20건 노출 (이전 5건은 truncation 심각).
        data["latest_decisions"] = details[:20]
    if scope == "history":
        data["history"] = history
        # quarterly_breakdown: details에서 연도/분기별 grouping (분기배당 회사 분기별 검증용)
        data["quarterly_breakdown"] = _quarterly_breakdown(details, history_years or year_list)
        # policy_signals: history scope에 통합 (별도 scope 폐지)
        data["policy_signals"] = policy
    if scope == "summary":
        data["policy_signals"] = policy
        data["meta_signals"] = {
            "pre_dividend_post_resolution": pre_dividend_post_resolution,
            "record_date_notice_count": len(record_date_notices),
            "capital_reserve_reduction": capital_reserve_reduction,
            "capital_reserve_agendas": capital_reserve_agendas,
        }
    if scope == "detail":
        # detail scope는 모든 filings 노출 (limit 50 — 3년 × 4분기 + 결산 + 정정 충분).
        data["detail"] = {
            "annual_summary": latest_summary,
            "latest_decisions": details[:50],
            "decision_count": len(details),
        }
        # alotMatter (사업보고서) vs filings 합산 mismatch warning
        if latest_summary and latest_summary.get("source") == "alotMatter":
            alot_dps = int(latest_summary.get("cash_dps") or 0)
            # 해당 사업연도 bucket 결정 공시 합산 (정정공시는 최신만 카운트하기 어려워 단순 합산)
            decisions_dps = sum(int(d.get("dps_common") or 0) for d in details if _bucket_fiscal_year(d) == target_year)
            if alot_dps and decisions_dps and abs(alot_dps - decisions_dps) > max(1, alot_dps * 0.05):
                warnings.append(f"⚠ {target_year}년 사업보고서 alotMatter DPS({alot_dps:,}원)와 배당결정 공시 합산 DPS({decisions_dps:,}원) 불일치 — 정정 또는 신규 결정 누락 가능성, latest_decisions raw 검토 권장.")

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

    # 선배당-후결의 시그널 evidence (배당기준일결정/주주명부폐쇄 공시).
    for notice in record_date_notices[:3]:
        if not notice.get("rcept_no"):
            continue
        evidence_refs.append(
            EvidenceRef(
                evidence_id=f"ev_record_date_{notice['rcept_no']}",
                source_type=SourceType.DART_XML,
                rcept_no=notice["rcept_no"],
                rcept_dt=format_iso_date(notice.get("rcept_dt", "")),
                report_nm=notice.get("report_nm", ""),
                section="배당기준일결정",
                note="선배당-후결의 신정관 채택 시그널 (2024 자본시장법 시행령)",
            )
        )

    # cash_shareholder_return scope 전용 — 자사주 **취득(매입)** 결정 공시 evidence.
    if scope == "cash_shareholder_return":
        csr = data.get("cash_shareholder_return", {}) or {}
        for row in (csr.get("acquisition_rows") or [])[:3]:
            if not row.get("rcept_no"):
                continue
            evidence_refs.append(
                EvidenceRef(
                    evidence_id=f"ev_acquire_{row['rcept_no']}",
                    source_type=SourceType.DART_API,
                    rcept_no=row["rcept_no"],
                    rcept_dt=format_iso_date(row.get("rcept_dt", "")),
                    report_nm=row.get("report_nm", ""),
                    section="자기주식취득결정",
                    note=(
                        f"{row.get('shares', 0):,}주 / {row.get('amount_krw', 0):,}원 매입"
                        if row.get("amount_krw") else "API+본문 파싱 실패 — 금액 미확정"
                    ),
                )
            )

    # total_shareholder_return scope 전용 — 주가 시세 evidence (네이버/KRX).
    if scope == "total_shareholder_return":
        tsr = data.get("total_shareholder_return", {}) or {}
        comp = tsr.get("components", {}) or {}
        if comp.get("price_start_krw") or comp.get("price_end_krw"):
            evidence_refs.append(
                EvidenceRef(
                    evidence_id=f"ev_tsr_price_{selected.get('stock_code', '')}_{target_year}",
                    source_type=SourceType.DART_API,  # 외부 시세 — 가장 가까운 enum
                    section="주가 시세 (네이버 금융 → KRX fallback)",
                    note=(
                        f"P_start={comp.get('price_start_krw', 0):,}원, "
                        f"P_end={comp.get('price_end_krw', 0):,}원, "
                        f"DPS={comp.get('dps_total_krw', 0):,}원"
                    ),
                )
            )

    status = status_from_filing_meta(filing_meta)
    if filing_meta["no_filing"]:
        warnings.append(f"조사 구간 ({start_ymd}~{end_ymd}) 내 배당결정 공시 없음 + 사업보고서 배당 요약도 비어 있어 무배당으로 본다 (정상)")
    if scope == "history" and len(history) < max(1, years):
        warnings.append("요청한 연수보다 완료 사업연도 수가 적어, 조회 가능한 최근 완료 사업연도만 반환한다.")

    data["usage"] = build_usage(client.api_call_snapshot() - _calls_start)
    timings_ms["total"] = int((time.perf_counter() - total_started_at) * 1000)
    data["timings_ms"] = timings_ms

    if scope == "summary":
        next_actions = [
            "history scope로 최근 3년 배당 추이 확인",
            "cash_shareholder_return scope로 한국식 환원율(배당+자사주 매입)/지배주주 순이익 확인",
            "total_shareholder_return scope로 글로벌 정의 1주 수익률(주가변동+배당) 확인",
        ]
    elif scope == "cash_shareholder_return":
        next_actions = [
            "treasury_share(scope=acquisition)로 자사주 매입 결정 본문 확인",
            "treasury_share(scope=cancelation)로 매입 후 소각 진행 상황 확인",
            "total_shareholder_return scope로 글로벌 정의 (주가+배당) 비교",
        ]
    elif scope == "total_shareholder_return":
        next_actions = [
            "cash_shareholder_return scope로 한국식 (배당+자사주 매입)/순이익 비교",
            "history scope로 DPS 추세 확인",
        ]
    else:
        next_actions = ["ownership_structure와 함께 보면 주주환원 맥락이 더 잘 보인다."]

    return ToolEnvelope(
        tool="dividend",
        status=status,
        subject=selected.get("corp_name", company_query),
        warnings=warnings,
        data=data,
        evidence_refs=evidence_refs,
        next_actions=next_actions,
    ).to_dict()
