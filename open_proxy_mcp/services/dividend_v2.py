"""v2 dividend facade 서비스."""

from __future__ import annotations

import asyncio
from datetime import date
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
    "policy_signals",
    "cash_shareholder_return",
    "total_shareholder_return",
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
        from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload
    except Exception:
        return False, []

    try:
        payload = await build_shareholder_meeting_payload(
            company_query,
            scope="summary",
            year=year,
            meeting_type="annual",
        )
    except Exception:
        return False, []

    data = payload.get("data", {}) or {}
    agenda_summary = data.get("agenda_summary", {}) or {}
    titles: list[str] = agenda_summary.get("titles", []) or []
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


def _net_income_from_summary(summary: dict[str, Any]) -> int:
    """summary의 net_income_consolidated_mil(백만원) → 원 단위.

    `decisions` source(fallback) 일 때는 net_income이 0으로 들어오므로
    호출 측에서 별도 처리해야 한다.
    """
    return int(summary.get("net_income_consolidated_mil", 0) or 0) * 1_000_000


def _components_from_decisions(year_decisions: list[dict[str, Any]]) -> dict[str, int]:
    """배당결정 공시 합산 — 정기/분기/특별 컴포넌트 분리 (KRW 원 단위)."""

    regular = 0
    quarterly = 0
    special = 0
    for d in year_decisions:
        amount = int(d.get("total_amount") or 0)
        dtype = (d.get("dividend_type") or "").strip()
        if d.get("has_special") or dtype == "특별배당":
            special += amount
            continue
        if dtype in ("분기배당", "중간배당"):
            quarterly += amount
            continue
        regular += amount
    return {
        "regular_dividend_krw": regular,
        "quarterly_dividend_krw": quarterly,
        "special_dividend_krw": special,
    }


async def _build_cash_shareholder_return(
    company_query: str,
    *,
    selected: dict[str, Any],
    year: int,
    annual_summaries: dict[int, dict[str, Any]],
    details: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    """CSR (Cash Shareholder Return) = (배당총액 + 자사주 **매입** 금액) / 지배주주 당기순이익 × 100.

    한국 시장 정의의 주주환원율. 회사가 주주에게 돌려준 **현금** 관점.
    - 분자 = 배당총액 + 자사주 **매입(acquire)** 금액 — 이사회 결의 시점 현금 유출
      (소각=retire는 회계 정리 단계로 매입 시점에 이미 현금이 나간 상태)
    - 분모 = 연결 지배주주 당기순이익 (한국 표준)

    분모 0 처리, 음수 이익(적자) 처리, 단위 정합(KRW) 일관.
    summary.net_income_consolidated_mil가 alotMatter에서만 채워지므로,
    fallback(decisions)일 때는 ratio=None으로 둔다.
    """

    from open_proxy_mcp.services.treasury_share import fetch_acquisition_summary

    warnings: list[str] = []

    summary = annual_summaries.get(year, {}) or {}
    # 1. 배당총액 — alotMatter total_amount_mil(백만원) 우선.
    dividend_total_krw = int(summary.get("total_amount_mil", 0) or 0) * 1_000_000
    dividend_source = summary.get("source") or "unknown"
    # alotMatter가 비거나 0이면 해당 연도 결정 공시 합산으로 fallback.
    year_decisions = [d for d in details if _bucket_fiscal_year(d) == year]
    if not dividend_total_krw and year_decisions:
        dividend_total_krw = sum(int(d.get("total_amount") or 0) for d in year_decisions)
        dividend_source = "decisions"
        warnings.append(
            f"{year}년 alotMatter 배당총액이 비어 있어 배당결정 공시 합산을 사용한다."
        )

    components = _components_from_decisions(year_decisions)
    components["buyback_value_krw"] = 0  # 아래에서 채움

    # 2. 자사주 **매입** 금액 — treasury_share.fetch_acquisition_summary.
    #    (T22 코드는 소각=retire 사용 — 잘못된 정의. 매입=acquire로 정정.)
    acq_summary = await fetch_acquisition_summary(selected["corp_code"], year=year)
    warnings.extend(acq_summary.get("warnings", []))
    buyback_total_krw = int(acq_summary.get("acquisition_amount_total_krw", 0) or 0)
    components["buyback_value_krw"] = buyback_total_krw

    # 3. 당기순이익 (연결 지배주주) — alotMatter 기반.
    net_income_krw = _net_income_from_summary(summary)

    # 4. 환원 합계 (현금 관점)
    cash_return_total_krw = dividend_total_krw + buyback_total_krw

    # 5. 비율 — 분모 0/음수 처리.
    csr_pct: float | None
    ratio_status: str
    if net_income_krw > 0:
        csr_pct = round(cash_return_total_krw / net_income_krw * 100, 2)
        ratio_status = "computed"
    elif net_income_krw == 0:
        csr_pct = None
        ratio_status = "denominator_zero_or_unknown"
        if dividend_source != "alotMatter":
            warnings.append(
                "당기순이익(연결, 백만원)을 alotMatter에서 가져오지 못해 CSR을 계산하지 못했다."
            )
        else:
            warnings.append("당기순이익이 0으로 보고돼 CSR을 계산할 수 없다.")
    else:
        csr_pct = None
        ratio_status = "negative_net_income"
        warnings.append(
            f"당기순이익이 음수({net_income_krw:,}원)이라 CSR 비율은 계산하지 않는다. 환원 절대 규모만 사용한다."
        )

    return {
        "definition": "Cash Shareholder Return — 한국 시장 정의 (배당 + 자사주 매입) / 지배주주 당기순이익",
        "year": year,
        "dividend_total_krw": dividend_total_krw,
        "buyback_total_krw": buyback_total_krw,
        "cash_return_total_krw": cash_return_total_krw,
        "net_income_krw": net_income_krw,
        "csr_pct": csr_pct,
        "ratio_status": ratio_status,
        "components": components,
        "sources": {
            "dividend": dividend_source,
            "buyback": "treasury_share.acquisition_decision",
            "net_income": "alotMatter.consolidated" if net_income_krw else "unavailable",
        },
        "acquisition_count": acq_summary.get("acquisition_count", 0),
        "acquisition_rows": acq_summary.get("rows", []),
        "decisions_in_year": len(year_decisions),
    }, warnings


async def _build_total_shareholder_return(
    company_query: str,
    *,
    selected: dict[str, Any],
    year: int,
    annual_summaries: dict[int, dict[str, Any]],
    details: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    """TSR (Total Shareholder Return) = (P_end - P_start + Dividends) / P_start × 100.

    글로벌 정의 (Anglo-Saxon). 주식 투자자 관점의 1주 수익률 = 주가 변동 + 배당.
    분모 = 연초 주가 (P_start). 음수 이익이라도 산출 가능 (주가만 있으면 됨).

    데이터 소스:
      - P_start: 사업연도 첫 거래일 종가 (네이버 금융 → KRX fallback)
      - P_end: 사업연도 마지막 거래일 종가
      - DPS: alotMatter cash_dps (연간 1주당 배당금) — 결산+분기 합산
    """

    from open_proxy_mcp.dart.client import get_dart_client

    warnings: list[str] = []

    summary = annual_summaries.get(year, {}) or {}
    stock_code = (selected.get("stock_code") or "").strip()
    client = get_dart_client()

    p_start: int | None = None
    p_end: int | None = None
    if stock_code:
        # 네이버 금융 시세 API는 비거래일이면 7일 fallback 윈도우로 직전/직후 거래일 종가를 반환.
        start_data = await client.get_stock_price(stock_code, f"{year}0102")
        if start_data and start_data.get("closing_price", 0) > 0:
            p_start = int(start_data["closing_price"])
        end_data = await client.get_stock_price(stock_code, f"{year}1230")
        if end_data and end_data.get("closing_price", 0) > 0:
            p_end = int(end_data["closing_price"])
    else:
        warnings.append("종목코드가 없어 TSR 산출에 필요한 주가를 조회할 수 없다.")

    # DPS — alotMatter cash_dps 우선. 없으면 결정 공시 합산.
    dps_total_krw = int(summary.get("cash_dps", 0) or 0)
    dps_source = "alotMatter" if dps_total_krw and summary.get("source") == "alotMatter" else "decisions"
    if not dps_total_krw:
        year_decisions = [d for d in details if _bucket_fiscal_year(d) == year]
        dps_total_krw = sum(int(d.get("dps_common") or 0) for d in year_decisions)
        if dps_total_krw:
            dps_source = "decisions"

    tsr_pct: float | None = None
    price_change_pct: float | None = None
    dividend_yield_pct: float | None = None
    ratio_status = "computed"

    if p_start and p_end:
        price_change_pct = round((p_end - p_start) / p_start * 100, 2)
        dividend_yield_pct = round(dps_total_krw / p_start * 100, 2) if dps_total_krw else 0.0
        tsr_pct = round((p_end - p_start + dps_total_krw) / p_start * 100, 2)
    elif not p_start or not p_end:
        ratio_status = "missing_price_data"
        warnings.append(
            f"TSR 산출에 필요한 주가 데이터 누락: P_start={p_start}, P_end={p_end}"
        )

    return {
        "definition": "Total Shareholder Return — 글로벌 정의 ((P_end - P_start + DPS) / P_start)",
        "year": year,
        "components": {
            "price_start_krw": p_start or 0,
            "price_end_krw": p_end or 0,
            "dps_total_krw": dps_total_krw,
            "price_change_pct": price_change_pct,
            "dividend_yield_pct": dividend_yield_pct,
        },
        "tsr_pct": tsr_pct,
        "ratio_status": ratio_status,
        "sources": {
            "price": "naver_finance|krx",
            "dps": dps_source,
        },
        "stock_code": stock_code,
    }, warnings


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

    client = get_dart_client()
    _calls_start = client.api_call_snapshot()
    resolution = await resolve_company_query(company_query)
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        return ToolEnvelope(
            tool="dividend",
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

    # latest_summary와 filings 검색은 independent — 병렬 호출.
    latest_summary_task = _annual_summary(selected["corp_code"], target_year)
    filings_task = _search_dividend_filings(selected["corp_code"], year_list[0], target_year)
    (latest_summary, summary_warning), (filings, filing_notices, filing_warning) = await asyncio.gather(
        latest_summary_task, filings_task,
    )
    if summary_warning:
        warnings.append(summary_warning)
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

    # 연도별 alotMatter 호출을 병렬화 (각 연도 독립).
    # target_year는 위에서 이미 호출했으므로 latest_summary 재사용해 중복 호출 방지.
    annual_summaries: dict[int, dict[str, Any]] = {}
    pending_years = [y for y in year_list if y != target_year]
    pending_results = await asyncio.gather(*[
        _annual_summary(selected["corp_code"], y) for y in pending_years
    ]) if pending_years else []
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
            pre_dividend_post_resolution, record_date_notices = await _detect_pre_dividend_post_resolution(
                selected["corp_code"], target_year
            )
        except Exception as exc:
            warnings.append(f"선배당-후결의 메타 추출 실패: {exc}")
        try:
            capital_reserve_reduction, capital_reserve_agendas = await _detect_capital_reserve_reduction(
                company_query, target_year
            )
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
        data["latest_decisions"] = details[:5]
    if scope == "history":
        data["history"] = history
    if scope == "policy_signals":
        data["policy_signals"] = policy
        data["history"] = history
    if scope == "summary":
        data["policy_signals"] = policy
        data["meta_signals"] = {
            "pre_dividend_post_resolution": pre_dividend_post_resolution,
            "record_date_notice_count": len(record_date_notices),
            "capital_reserve_reduction": capital_reserve_reduction,
            "capital_reserve_agendas": capital_reserve_agendas,
        }
    if scope == "detail":
        data["detail"] = {
            "annual_summary": latest_summary,
            "latest_decisions": details[:10],
        }
    if scope == "cash_shareholder_return":
        csr_data, csr_warnings = await _build_cash_shareholder_return(
            company_query,
            selected=selected,
            year=target_year,
            annual_summaries=annual_summaries,
            details=details,
        )
        warnings.extend(csr_warnings)
        data["cash_shareholder_return"] = csr_data
        data["meta_signals"] = {
            "pre_dividend_post_resolution": pre_dividend_post_resolution,
            "record_date_notice_count": len(record_date_notices),
            "capital_reserve_reduction": capital_reserve_reduction,
            "capital_reserve_agendas": capital_reserve_agendas,
        }
    if scope == "total_shareholder_return":
        tsr_data, tsr_warnings = await _build_total_shareholder_return(
            company_query,
            selected=selected,
            year=target_year,
            annual_summaries=annual_summaries,
            details=details,
        )
        warnings.extend(tsr_warnings)
        data["total_shareholder_return"] = tsr_data
        data["meta_signals"] = {
            "pre_dividend_post_resolution": pre_dividend_post_resolution,
            "record_date_notice_count": len(record_date_notices),
            "capital_reserve_reduction": capital_reserve_reduction,
            "capital_reserve_agendas": capital_reserve_agendas,
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
