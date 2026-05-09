"""financial_metrics — DART 재무 4 endpoint 통합 facade.

Phase 1: 6 scope (summary / yearly / quarterly / yoy / qoq / audit_opinion).

한국 표준:
- 연결 (CFS) default — 지배주주 귀속
- 분모 0 / 음수 / None graceful
- 모든 금액 raw KRW int (`_krw` suffix), % float (`_pct`), decimal (`_ratio`)
- render에서만 사람가독 변환 (조/억)
"""

from __future__ import annotations

import asyncio
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
)


_SUPPORTED_SCOPES = {
    "summary",
    "yearly",
    "quarterly",
    "yoy",
    "qoq",
    "audit_opinion",
}

_REPRT_BUSINESS = "11011"  # 사업보고서 (연간)
_QUARTER_REPRT_CODES = ("11013", "11012", "11014", "11011")  # Q1, Q2(반기), Q3, Q4(사업)


# DART 사업보고서 fnlttSinglAcnt 표준 account_nm 매칭 키워드.
# fnlttSinglAcntAll에는 더 세분화된 account_nm이 들어 있어 별도 패턴.
_BS_ACCOUNT_PATTERNS = {
    "current_assets": ("유동자산",),
    "non_current_assets": ("비유동자산",),
    "total_assets": ("자산총계",),
    "current_liabilities": ("유동부채",),
    "non_current_liabilities": ("비유동부채",),
    "total_liabilities": ("부채총계",),
    "capital_stock": ("자본금",),
    "retained_earnings": ("이익잉여금",),
    "total_equity": ("자본총계",),
}

_IS_ACCOUNT_PATTERNS = {
    "revenue": ("매출액", "수익(매출액)", "영업수익"),
    "operating_profit": ("영업이익", "영업이익(손실)"),
    "income_before_tax": ("법인세차감전 순이익", "법인세비용차감전순이익", "법인세차감전순이익"),
    "net_income": ("당기순이익(손실)", "당기순이익", "분기순이익", "반기순이익"),
    "comprehensive_income": ("총포괄손익",),
}

# fnlttSinglAcntAll 전용 패턴 (현금흐름표 + 추가 IS 항목).
_CF_ACCOUNT_PATTERNS = {
    "cfo": ("영업활동현금흐름", "영업활동으로 인한 현금흐름", "영업활동으로인한현금흐름"),
    "cfi": ("투자활동현금흐름", "투자활동으로 인한 현금흐름"),
    "cff": ("재무활동현금흐름", "재무활동으로 인한 현금흐름"),
    "capex": (
        "유형자산의 취득",
        "유형자산의취득",
        "유형자산취득",
    ),
    "depreciation": (
        "감가상각비",
    ),
    "amortization": (
        "무형자산상각비",
        "무형자산 상각비",
    ),
    "interest_paid": (
        "이자의 지급",
        "이자지급",
    ),
    "dividends_paid": (
        "배당금의 지급",
        "배당금지급",
        "배당금 지급",
    ),
}

# fnlttSinglAcntAll IS/CIS 추가 항목.
_IS_DETAIL_PATTERNS = {
    "gross_profit": ("매출총이익", "매출총이익(손실)"),
    "operating_revenue": ("매출액", "수익(매출액)", "영업수익"),
    "cogs": ("매출원가",),
    "interest_expense": ("이자비용", "금융비용"),
    "minority_interest_income": ("비지배지분", "비지배주주지분 순이익", "비지배지분순이익"),
    "controlling_interest_income": ("지배기업 소유주지분", "지배기업소유주지분", "지배주주지분 순이익"),
    "diluted_eps": ("희석주당이익", "희석주당순이익", "희석주당이익(손실)"),
    "basic_eps": ("기본주당이익", "기본주당순이익", "주당이익", "주당순이익"),
    "accounts_receivable": ("매출채권", "매출채권 및 기타채권"),
    "inventory": ("재고자산",),
    "accounts_payable": ("매입채무", "매입채무 및 기타채무"),
    "cash_and_equivalents": ("현금및현금성자산", "현금 및 현금성자산"),
    "short_term_debt": ("단기차입금",),
    "long_term_debt": ("장기차입금", "사채"),
}


def _strip(s: Any) -> str:
    return ("" if s is None else str(s)).strip()


def normalize_amount(raw: Any) -> int | None:
    """DART 응답 금액 → int (원).

    DART OpenAPI fnlttSinglAcnt / fnlttSinglAcntAll 응답은 항상 **원 단위 raw + 콤마 포맷**
    으로 표준화. 별도 unit 필드(백만원/천원)는 응답에 없음 (currency 필드만 KRW 표기).
    → 콤마 strip + 괄호 음수 + None graceful만 처리하면 충분.

    처리:
    - None / "" / "-" → None
    - "227,062,266,000,000" → 227_062_266_000_000
    - "(500)" → -500 (괄호 음수, 한국 회계 관행 — T19 fix 패턴)
    - 부호 prefix "-500" → -500
    - 잘못된 포맷 → None
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s or s in ("-", "—", "–"):
        return None
    # 괄호 음수
    is_neg = False
    if s.startswith("(") and s.endswith(")"):
        is_neg = True
        s = s[1:-1].strip()
    s = s.replace(",", "").replace(" ", "")
    if not s:
        return None
    try:
        n = int(float(s))
    except (ValueError, TypeError):
        return None
    return -n if is_neg else n


def normalize_pct(raw: Any) -> float | None:
    """DART 지표 값 → float (% 형식, 11.5 = 11.5%).

    fnlttSinglIndx의 idx_val은 string. None/공란 graceful.
    """
    if raw is None:
        return None
    s = str(raw).strip().rstrip("%").replace(",", "")
    if not s or s == "-":
        return None
    try:
        return float(s)
    except (ValueError, TypeError):
        return None


def _match_account(account_nm: str, patterns: tuple[str, ...]) -> bool:
    if not account_nm:
        return False
    nm = account_nm.strip().replace(" ", "")
    for p in patterns:
        if p.replace(" ", "") in nm:
            return True
    return False


def _extract_period_amount(row: dict[str, Any], period: str = "thstrm") -> int | None:
    """row에서 특정 기간 금액 추출. period: thstrm(당기) / frmtrm(전기) / bfefrmtrm(전전기)."""
    return normalize_amount(row.get(f"{period}_amount"))


def _build_account_map(
    rows: list[dict[str, Any]],
    bs_patterns: dict[str, tuple[str, ...]] = _BS_ACCOUNT_PATTERNS,
    is_patterns: dict[str, tuple[str, ...]] = _IS_ACCOUNT_PATTERNS,
    period: str = "thstrm",
) -> dict[str, int | None]:
    """fnlttSinglAcnt rows → 표준 키 매핑 dict (BS + IS).

    같은 키에 여러 행이 매칭되면 첫 매칭만 사용 (DART 응답 순서 = 사업보고서 순서).
    """
    out: dict[str, int | None] = {k: None for k in {**bs_patterns, **is_patterns}}
    for row in rows:
        sj_div = _strip(row.get("sj_div"))
        account_nm = _strip(row.get("account_nm"))
        if sj_div == "BS":
            for key, patterns in bs_patterns.items():
                if out[key] is None and _match_account(account_nm, patterns):
                    out[key] = _extract_period_amount(row, period)
                    break
        elif sj_div == "IS":
            for key, patterns in is_patterns.items():
                if out[key] is None and _match_account(account_nm, patterns):
                    out[key] = _extract_period_amount(row, period)
                    break
    return out


def _build_account_map_all(
    rows: list[dict[str, Any]],
    period: str = "thstrm",
) -> dict[str, int | None]:
    """fnlttSinglAcntAll rows → 표준 키 매핑 (CF + IS detail + BS detail).

    sj_div: BS / IS / CIS / CF / SCE.
    """
    out: dict[str, int | None] = {}
    out.update({k: None for k in _BS_ACCOUNT_PATTERNS})
    out.update({k: None for k in _IS_ACCOUNT_PATTERNS})
    out.update({k: None for k in _IS_DETAIL_PATTERNS})
    out.update({k: None for k in _CF_ACCOUNT_PATTERNS})

    for row in rows:
        sj_div = _strip(row.get("sj_div"))
        account_nm = _strip(row.get("account_nm"))
        amount = _extract_period_amount(row, period)

        if sj_div == "BS":
            for key, patterns in _BS_ACCOUNT_PATTERNS.items():
                if out[key] is None and _match_account(account_nm, patterns):
                    out[key] = amount
                    break
            for key, patterns in _IS_DETAIL_PATTERNS.items():
                if key in {"accounts_receivable", "inventory", "accounts_payable",
                           "cash_and_equivalents", "short_term_debt", "long_term_debt"}:
                    if out[key] is None and _match_account(account_nm, patterns):
                        out[key] = amount
                        break
        elif sj_div in ("IS", "CIS"):
            for key, patterns in _IS_ACCOUNT_PATTERNS.items():
                if out[key] is None and _match_account(account_nm, patterns):
                    out[key] = amount
                    break
            for key, patterns in _IS_DETAIL_PATTERNS.items():
                if key in {"gross_profit", "cogs", "interest_expense",
                           "minority_interest_income", "controlling_interest_income",
                           "diluted_eps", "basic_eps"}:
                    if out[key] is None and _match_account(account_nm, patterns):
                        out[key] = amount
                        break
        elif sj_div == "CF":
            for key, patterns in _CF_ACCOUNT_PATTERNS.items():
                if out[key] is None and _match_account(account_nm, patterns):
                    out[key] = amount
                    break
    return out


def _safe_div(
    numer: int | float | None,
    denom: int | float | None,
    *,
    positive_denom_only: bool = False,
) -> float | None:
    """0 분모 / None graceful.

    positive_denom_only=True: 분모가 음수면 None 반환 (ROE/ROA/배당성향 등 — 자본 음수 = 채무초과 회사).
    positive_denom_only=False (default): 분모 부호 보존 (이자보상배율 — 영업이익 음수면 음수 ratio가 의미 있음).
    """
    if numer is None or denom is None:
        return None
    if denom == 0:
        return None
    if positive_denom_only and denom < 0:
        return None
    return numer / denom


def _safe_pct(
    numer: int | float | None,
    denom: int | float | None,
    *,
    positive_denom_only: bool = False,
) -> float | None:
    """비율을 % (×100) 로. round 2자리."""
    r = _safe_div(numer, denom, positive_denom_only=positive_denom_only)
    if r is None:
        return None
    return round(r * 100, 2)


def _safe_ratio(
    numer: int | float | None,
    denom: int | float | None,
    *,
    positive_denom_only: bool = False,
) -> float | None:
    """비율을 decimal 그대로. round 4자리."""
    r = _safe_div(numer, denom, positive_denom_only=positive_denom_only)
    if r is None:
        return None
    return round(r, 4)


def _avg(a: int | float | None, b: int | float | None) -> float | None:
    if a is None or b is None:
        return None
    return (a + b) / 2


def _compute_metrics(
    *,
    bs_is: dict[str, int | None],
    bs_is_prev: dict[str, int | None] | None,
    detail: dict[str, int | None] | None,
    detail_prev: dict[str, int | None] | None,
    indx_map: dict[str, float | None] | None,
) -> dict[str, Any]:
    """단일 사업연도 metrics 계산 (수익성/안정성/현금흐름/운전자본/회계risk/배당유보/NAV).

    bs_is: 당기 fnlttSinglAcnt 매핑.
    bs_is_prev: 전기 fnlttSinglAcnt 매핑 (평균자산/평균자본 계산용. None이면 thstrm 단독 사용).
    detail: 당기 fnlttSinglAcntAll 매핑 (CF + 세부 IS/BS).
    detail_prev: 전기 fnlttSinglAcntAll 매핑 (NWC 변동 계산용).
    indx_map: fnlttSinglIndx에서 추출한 DART 산출 지표 (보조 — 자체 계산 우선).
    """
    detail = detail or {}
    detail_prev = detail_prev or {}
    indx_map = indx_map or {}

    revenue = bs_is.get("revenue")
    operating_profit = bs_is.get("operating_profit")
    net_income = bs_is.get("net_income")
    total_assets = bs_is.get("total_assets")
    total_equity = bs_is.get("total_equity")
    total_liabilities = bs_is.get("total_liabilities")
    current_assets = bs_is.get("current_assets")
    current_liabilities = bs_is.get("current_liabilities")
    retained_earnings = bs_is.get("retained_earnings")
    capital_stock = bs_is.get("capital_stock")  # 자본금 (액면가 × 발행주식수)

    # ── 자본잠식 (Capital Impairment) ──
    # 잠식률 = (자본금 - 자본총계) / 자본금
    # - 0% 미만: 정상 (자본총계 > 자본금)
    # - 0~50%: 부분 자본잠식
    # - 50%↑: KOSDAQ 관리종목 사유 / KOSPI 사업보고서 미공시 등 trigger
    # - 100%↑ (자본총계 ≤ 0): 완전 자본잠식 = 상장폐지 사유
    capital_impairment_ratio_pct = None  # 잠식률 (% — 양수 = 잠식 진행, 음수 = 정상)
    capital_impairment_status = None  # "normal" / "partial" / "partial_50plus" / "full"
    if capital_stock is not None and capital_stock > 0 and total_equity is not None:
        ratio = (capital_stock - total_equity) / capital_stock * 100
        capital_impairment_ratio_pct = round(ratio, 2)
        if total_equity <= 0:
            capital_impairment_status = "full"
        elif ratio >= 50:
            capital_impairment_status = "partial_50plus"
        elif ratio > 0:
            capital_impairment_status = "partial"
        else:
            capital_impairment_status = "normal"

    # 평균값 (BS 전기 데이터 있으면)
    avg_assets = _avg(total_assets, (bs_is_prev or {}).get("total_assets")) if bs_is_prev else total_assets
    avg_equity = _avg(total_equity, (bs_is_prev or {}).get("total_equity")) if bs_is_prev else total_equity

    # detail (fnlttSinglAcntAll)
    gross_profit = detail.get("gross_profit")
    cogs = detail.get("cogs")
    if gross_profit is None and revenue is not None and cogs is not None:
        gross_profit = revenue - cogs

    cfo = detail.get("cfo")
    capex = detail.get("capex")
    depreciation = detail.get("depreciation")
    amortization = detail.get("amortization")
    da = None
    if depreciation is not None or amortization is not None:
        da = (depreciation or 0) + (amortization or 0)
    interest_expense = detail.get("interest_expense")
    cash_and_equivalents = detail.get("cash_and_equivalents")
    short_term_debt = detail.get("short_term_debt")
    long_term_debt = detail.get("long_term_debt")
    accounts_receivable = detail.get("accounts_receivable")
    inventory = detail.get("inventory")
    accounts_payable = detail.get("accounts_payable")
    diluted_eps_per_share = detail.get("diluted_eps")  # 원/주
    basic_eps_per_share = detail.get("basic_eps")
    controlling_ni = detail.get("controlling_interest_income")

    # net_income은 한국 표준 = 지배주주 귀속. fnlttSinglAcnt의 "당기순이익(손실)"은 보통 합계.
    # detail에서 controlling 분리되면 우선 사용.
    if controlling_ni is not None:
        net_income_controlling = controlling_ni
    else:
        net_income_controlling = net_income

    # ── prev year (yoy 계산용) — 260505 ralph precision iter 2 ──
    prev_revenue = (bs_is_prev or {}).get("revenue") if bs_is_prev else None
    prev_operating_profit = (bs_is_prev or {}).get("operating_profit") if bs_is_prev else None
    prev_net_income_total = (bs_is_prev or {}).get("net_income") if bs_is_prev else None
    prev_controlling_ni = (detail_prev or {}).get("controlling_interest_income") if detail_prev else None
    prev_net_income_controlling = prev_controlling_ni if prev_controlling_ni is not None else prev_net_income_total

    def _yoy_pct(curr, prev):
        if curr is None or prev is None or prev == 0:
            return None
        return round((curr - prev) / abs(prev) * 100, 2)

    revenue_yoy_pct = _yoy_pct(revenue, prev_revenue)
    operating_profit_yoy_pct = _yoy_pct(operating_profit, prev_operating_profit)
    net_income_yoy_pct = _yoy_pct(net_income_controlling, prev_net_income_controlling)

    # 총차입금 (단기 + 장기)
    total_debt = None
    if short_term_debt is not None or long_term_debt is not None:
        total_debt = (short_term_debt or 0) + (long_term_debt or 0)

    # 순현금
    net_cash = None
    if cash_and_equivalents is not None and total_debt is not None:
        net_cash = cash_and_equivalents - total_debt

    # ── 수익성 ──
    operating_margin_pct = _safe_pct(operating_profit, revenue)
    gross_margin_pct = _safe_pct(gross_profit, revenue)
    net_profit_margin_pct = _safe_pct(net_income_controlling, revenue)
    ebitda_krw = None
    if operating_profit is not None or da is not None:
        ebitda_krw = (operating_profit or 0) + (da or 0)
        if operating_profit is None and da is None:
            ebitda_krw = None
    ebitda_margin_pct = _safe_pct(ebitda_krw, revenue)

    # ROE / ROA — 평균자산/평균자본 (전기 없으면 기말 단독).
    # 분모 음수 (채무초과) 시 None — 적자 회사 ROE 부호 왜곡 방지.
    roe_pct = _safe_pct(net_income_controlling, avg_equity, positive_denom_only=True)
    roa_pct = _safe_pct(net_income_controlling, avg_assets, positive_denom_only=True)

    # ROIC = NOPAT / 투하자본. 단순 근사: 영업이익 × (1 - 0.22 평균법인세율) / (자본 + 총차입)
    nopat = None
    invested_capital = None
    roic_pct = None
    if operating_profit is not None:
        nopat = operating_profit * (1 - 0.22)  # 한국 평균 법인세 22%
    if total_equity is not None and total_debt is not None:
        invested_capital = total_equity + total_debt
    # 투하자본도 음수면 None (자본+차입이 동시에 음수 = 비정상)
    roic_pct = _safe_pct(nopat, invested_capital, positive_denom_only=True)

    # ── 듀퐁 3단 ──
    asset_turnover_ratio = _safe_ratio(revenue, avg_assets, positive_denom_only=True)
    # 평균자본 음수 (채무초과) 시 equity_multiplier는 None — ROE 분해 의미 없음
    equity_multiplier = _safe_ratio(avg_assets, avg_equity, positive_denom_only=True)
    # ROE 검증 (3단 곱)
    roe_dupont_pct = None
    if net_profit_margin_pct is not None and asset_turnover_ratio is not None and equity_multiplier is not None:
        roe_dupont_pct = round(
            (net_profit_margin_pct / 100) * asset_turnover_ratio * equity_multiplier * 100, 2
        )

    # ── 안정성 ──
    # 부채비율 — 분모 자본 양수 가정 (채무초과 시 비율 의미 X → None)
    debt_ratio_pct = _safe_pct(total_liabilities, total_equity, positive_denom_only=True)
    current_ratio_pct = _safe_pct(current_assets, current_liabilities, positive_denom_only=True)
    # 이자보상배율 — 영업이익(분자) 음수면 ratio 음수가 의미 있음 (적자 가시성). 분모만 양수 요구.
    interest_coverage_ratio = _safe_ratio(operating_profit, interest_expense, positive_denom_only=True) if (
        interest_expense is not None and interest_expense > 0
    ) else None
    debt_dependency_pct = _safe_pct(total_debt, total_assets, positive_denom_only=True)

    # ── 현금흐름 ──
    fcf_krw = None
    if cfo is not None or capex is not None:
        # capex는 보통 음수 (현금유출). 절대값 처리.
        fcf_krw = (cfo or 0) - abs(capex or 0)
        if cfo is None and capex is None:
            fcf_krw = None
    fcf_margin_pct = _safe_pct(fcf_krw, revenue)
    cfo_to_op_ratio = _safe_ratio(cfo, operating_profit)
    capex_to_da_ratio = None
    if capex is not None and da:
        capex_to_da_ratio = _safe_ratio(abs(capex), abs(da))

    # ── 운전자본 ──
    working_capital_krw = None
    if current_assets is not None and current_liabilities is not None:
        working_capital_krw = current_assets - current_liabilities
    nwc_krw = None
    if accounts_receivable is not None or inventory is not None or accounts_payable is not None:
        nwc_krw = (accounts_receivable or 0) + (inventory or 0) - (accounts_payable or 0)
        # 모두 None이면 진짜 없음
        if accounts_receivable is None and inventory is None and accounts_payable is None:
            nwc_krw = None
    nwc_change_yoy_krw = None
    if nwc_krw is not None and detail_prev:
        prev_ar = detail_prev.get("accounts_receivable")
        prev_inv = detail_prev.get("inventory")
        prev_ap = detail_prev.get("accounts_payable")
        if prev_ar is not None or prev_inv is not None or prev_ap is not None:
            prev_nwc = (prev_ar or 0) + (prev_inv or 0) - (prev_ap or 0)
            nwc_change_yoy_krw = nwc_krw - prev_nwc
    nwc_to_revenue_pct = _safe_pct(nwc_krw, revenue)

    # ── 회계 risk ──
    accruals_gap_pct = None
    if operating_profit is not None and cfo is not None and operating_profit != 0:
        accruals_gap_pct = round((operating_profit - cfo) / operating_profit * 100, 2)
    ar_to_revenue_pct = _safe_pct(accounts_receivable, revenue)
    inv_to_revenue_pct = _safe_pct(inventory, revenue)

    # ── 배당 / 유보 ──
    # 배당총액 = -dividends_paid (CF는 음수). 별도 alotMatter 호출은 dividend tool 책임.
    dividend_paid_krw = None
    dp = detail.get("dividends_paid")
    if dp is not None:
        dividend_paid_krw = abs(dp)
    payout_ratio_pct = _safe_pct(dividend_paid_krw, net_income_controlling) if (
        net_income_controlling is not None and net_income_controlling > 0
    ) else None
    dividend_to_fcf_pct = _safe_pct(dividend_paid_krw, fcf_krw) if (
        fcf_krw is not None and fcf_krw > 0
    ) else None

    # ── NAV / 주식 ──
    nav_krw = total_equity  # 자본총계 = 자산-부채
    # BPS는 발행주식수 필요 — fnltt에는 없으므로 stockTotqySttus 별도 호출 필요 (Phase 2).
    # detail에 basic_eps_per_share / diluted_eps_per_share가 있으면 그대로 사용 (원/주).
    eps_krw = basic_eps_per_share
    diluted_eps_krw = diluted_eps_per_share
    bps_krw = None  # Phase 2 — stockTotqySttus 호출 통합 시 채움

    # ── 지배구조 cross-check ──
    # subsidiary_count: 종속회사 수. DART OpenAPI 4 endpoint 어디에도 직접 반환 X.
    # 사업보고서 본문 (XML/PDF) "종속회사 명단" 섹션 파싱 필요 — Phase 2 (3-tier fallback 추가).
    subsidiary_count = None

    return {
        # ── 수익성 ──
        "revenue_krw": revenue,
        "gross_profit_krw": gross_profit,
        "operating_profit_krw": operating_profit,
        "operating_margin_pct": operating_margin_pct,
        "gross_margin_pct": gross_margin_pct,
        "ebitda_krw": ebitda_krw,
        "ebitda_margin_pct": ebitda_margin_pct,
        "net_income_krw": net_income_controlling,  # 지배주주 귀속 (한국 표준)
        "net_income_total_krw": net_income,  # 합계 (참고용)
        "net_profit_margin_pct": net_profit_margin_pct,
        # ── prev year + yoy (260505 ralph precision iter 2 — 흑자+yoy<0 trigger 활성화용) ──
        "prev_revenue_krw": prev_revenue,
        "prev_operating_profit_krw": prev_operating_profit,
        "prev_net_income_krw": prev_net_income_controlling,
        "revenue_yoy_pct": revenue_yoy_pct,
        "operating_profit_yoy_pct": operating_profit_yoy_pct,
        "net_income_yoy_pct": net_income_yoy_pct,
        "eps_krw": eps_krw,
        "diluted_eps_krw": diluted_eps_krw,
        "roe_pct": roe_pct,
        "roa_pct": roa_pct,
        "roic_pct": roic_pct,
        # ── 듀퐁 ──
        "asset_turnover_ratio": asset_turnover_ratio,
        "equity_multiplier": equity_multiplier,
        "roe_dupont_pct": roe_dupont_pct,  # 검증값 — roe_pct와 일치 확인용
        # ── 안정성 ──
        "total_assets_krw": total_assets,
        "total_liabilities_krw": total_liabilities,
        "total_equity_krw": total_equity,
        "current_assets_krw": current_assets,
        "current_liabilities_krw": current_liabilities,
        "debt_ratio_pct": debt_ratio_pct,
        "current_ratio_pct": current_ratio_pct,
        "interest_coverage_ratio": interest_coverage_ratio,
        "debt_dependency_pct": debt_dependency_pct,
        "total_debt_krw": total_debt,
        "net_cash_krw": net_cash,
        "cash_and_equivalents_krw": cash_and_equivalents,
        # ── 현금흐름 ──
        "cfo_krw": cfo,
        "capex_krw": capex,
        "fcf_krw": fcf_krw,
        "fcf_margin_pct": fcf_margin_pct,
        "cfo_to_op_ratio": cfo_to_op_ratio,
        "capex_to_da_ratio": capex_to_da_ratio,
        # ── 운전자본 ──
        "working_capital_krw": working_capital_krw,
        "nwc_krw": nwc_krw,
        "nwc_change_yoy_krw": nwc_change_yoy_krw,
        "nwc_to_revenue_pct": nwc_to_revenue_pct,
        # ── 회계 risk ──
        "accruals_gap_pct": accruals_gap_pct,
        "ar_to_revenue_pct": ar_to_revenue_pct,
        "inv_to_revenue_pct": inv_to_revenue_pct,
        # ── 배당/유보 ──
        "dividend_paid_krw": dividend_paid_krw,
        "payout_ratio_pct": payout_ratio_pct,
        "dividend_to_fcf_pct": dividend_to_fcf_pct,
        "retained_earnings_krw": retained_earnings,
        # ── NAV/주식 ──
        "nav_krw": nav_krw,
        "bps_krw": bps_krw,  # Phase 2 — None until stockTotqySttus 통합
        "capital_stock_krw": capital_stock,
        # ── 자본잠식 (KOSDAQ 관리/폐지 사유 detect) ──
        "capital_impairment_ratio_pct": capital_impairment_ratio_pct,
        "capital_impairment_status": capital_impairment_status,
        # ── 지배구조 cross-check ──
        "subsidiary_count": subsidiary_count,  # Phase 2 — 사업보고서 본문 파싱 필요
        # ── DART 산출 지표 (보조) ──
        "dart_indx": indx_map,
    }


def _detect_yoy_signals(curr: dict[str, Any], prev: dict[str, Any] | None,
                        audit_curr: dict[str, Any] | None = None,
                        audit_prev: dict[str, Any] | None = None) -> list[str]:
    """전년 대비 alerts 자동 detect.

    curr/prev: _compute_metrics 결과.
    audit_curr/audit_prev: audit_opinion scope 결과 ({adt_opinion: ...}).
    """
    alerts: list[str] = []

    ni_curr = curr.get("net_income_krw")
    ni_prev = prev.get("net_income_krw") if prev else None
    op_curr = curr.get("operating_profit_krw")
    rev_curr = curr.get("revenue_krw")
    rev_prev = prev.get("revenue_krw") if prev else None

    # 수익성
    if ni_curr is not None and ni_prev is not None:
        if ni_prev > 0 and ni_curr < 0:
            alerts.append("loss_conversion")
        if ni_prev < 0 and ni_curr > 0:
            alerts.append("turnaround")
        if ni_prev < 0 and ni_curr < 0:
            alerts.append("continued_loss")
    if op_curr is not None and op_curr < 0:
        alerts.append("operating_loss")
    if rev_curr is not None and rev_prev is not None and rev_prev > 0:
        if (rev_prev - rev_curr) / rev_prev > 0.30:
            alerts.append("revenue_decline")

    # 부채/유동성
    debt_curr = curr.get("total_liabilities_krw")
    debt_prev = prev.get("total_liabilities_krw") if prev else None
    if debt_curr is not None and debt_prev is not None and debt_prev > 0:
        if (debt_curr - debt_prev) / debt_prev > 0.30:
            alerts.append("debt_surge")
    icov = curr.get("interest_coverage_ratio")
    if icov is not None and icov < 2:
        alerts.append("interest_coverage_low")

    # 자본잠식 (KOSDAQ 관리종목 / 상장폐지 사유 detect)
    cap_status = curr.get("capital_impairment_status")
    if cap_status == "full":
        alerts.append("capital_impairment_full")  # 자본총계 ≤ 0, KOSDAQ 상장폐지 사유
    elif cap_status == "partial_50plus":
        alerts.append("capital_impairment_50plus")  # 잠식률 50%↑, KOSDAQ 관리종목 사유
    elif cap_status == "partial":
        alerts.append("capital_impairment_partial")  # 잠식률 0~50%, 조기 경고

    # 현금흐름
    cfo_quality = curr.get("cfo_to_op_ratio")
    if cfo_quality is not None and cfo_quality < 0.7:
        alerts.append("cfo_quality_red")
    fcf = curr.get("fcf_krw")
    if fcf is not None and fcf < 0:
        alerts.append("negative_fcf")
    div_to_fcf = curr.get("dividend_to_fcf_pct")
    if div_to_fcf is not None and 0 < div_to_fcf < 20:
        alerts.append("low_dividend_capacity_use")

    # 운전자본
    nwc_change = curr.get("nwc_change_yoy_krw")
    nwc_prev = prev.get("nwc_krw") if prev else None
    if nwc_change is not None and nwc_prev is not None and nwc_prev > 0:
        if nwc_change / nwc_prev > 0.30:
            alerts.append("nwc_surge")
    nwc_eff = curr.get("nwc_to_revenue_pct")
    if nwc_eff is not None and nwc_eff > 25:
        alerts.append("nwc_efficiency_low")

    # 듀퐁 — 레버리지 의존도
    em = curr.get("equity_multiplier")
    if em is not None and em > 2.0:  # 자기자본 비중 50% 미만 = 부채 의존
        alerts.append("roe_driven_by_leverage")
    # ROE decline 분해
    roe_curr = curr.get("roe_pct")
    roe_prev = prev.get("roe_pct") if prev else None
    if roe_curr is not None and roe_prev is not None and roe_curr < roe_prev:
        npm_curr = curr.get("net_profit_margin_pct")
        npm_prev = prev.get("net_profit_margin_pct") if prev else None
        ato_curr = curr.get("asset_turnover_ratio")
        ato_prev = prev.get("asset_turnover_ratio") if prev else None
        margin_drop = (npm_prev or 0) - (npm_curr or 0) if (npm_prev is not None and npm_curr is not None) else 0
        ato_drop = (ato_prev or 0) - (ato_curr or 0) if (ato_prev is not None and ato_curr is not None) else 0
        if margin_drop > 0 and margin_drop * 5 > ato_drop:
            alerts.append("roe_decline_margin_driven")
        elif ato_drop > 0:
            alerts.append("roe_decline_turnover_driven")

    # 회계 risk
    acc = curr.get("accruals_gap_pct")
    if acc is not None and abs(acc) > 30:
        alerts.append("accruals_red")
    ar = curr.get("ar_to_revenue_pct")
    ar_prev_pct = prev.get("ar_to_revenue_pct") if prev else None
    if ar is not None and ar_prev_pct is not None and ar_prev_pct > 0:
        if (ar - ar_prev_pct) / ar_prev_pct > 0.30:
            alerts.append("receivables_surge")
    inv = curr.get("inv_to_revenue_pct")
    inv_prev_pct = prev.get("inv_to_revenue_pct") if prev else None
    if inv is not None and inv_prev_pct is not None and inv_prev_pct > 0:
        if (inv - inv_prev_pct) / inv_prev_pct > 0.30:
            alerts.append("inventory_surge")

    # 감사의견
    if audit_curr:
        op = (audit_curr.get("adt_opinion") or "").strip()
        if op and "적정" not in op:
            alerts.append("non_clean_audit_opinion")
        if audit_prev:
            prev_op = (audit_prev.get("adt_opinion") or "").strip()
            if op and prev_op and op != prev_op and "적정" in prev_op and "적정" not in op:
                alerts.append("audit_opinion_change")

    # 배당
    div_curr = curr.get("dividend_paid_krw")
    div_prev = prev.get("dividend_paid_krw") if prev else None
    if div_prev is not None and div_prev > 0 and (div_curr is None or div_curr == 0):
        alerts.append("dividend_halt")

    return sorted(set(alerts))


# ── DART 호출 헬퍼 (try/except + AnalysisStatus mapping) ──

async def _safe_fetch_acnt(corp_code: str, year: int, reprt_code: str, fs_div: str) -> tuple[list[dict[str, Any]], str | None]:
    client = get_dart_client()
    try:
        data = await client.get_fnltt_singl_acnt(corp_code, str(year), reprt_code, fs_div)
        return data.get("list", []) or [], None
    except DartClientError as exc:
        if exc.status == "013":
            return [], "no_filing"
        return [], f"fnlttSinglAcnt({reprt_code}, {fs_div}) 실패: {exc.status} {exc}"


async def _safe_fetch_acnt_all(corp_code: str, year: int, reprt_code: str, fs_div: str) -> tuple[list[dict[str, Any]], str | None]:
    client = get_dart_client()
    try:
        data = await client.get_fnltt_singl_acnt_all(corp_code, str(year), reprt_code, fs_div)
        return data.get("list", []) or [], None
    except DartClientError as exc:
        if exc.status == "013":
            return [], "no_filing"
        return [], f"fnlttSinglAcntAll({reprt_code}, {fs_div}) 실패: {exc.status} {exc}"


async def _safe_fetch_indx(corp_code: str, year: int, reprt_code: str) -> dict[str, float | None]:
    """4개 idx_cl_code 모두 호출 → 통합 dict (idx_nm: idx_val)."""
    client = get_dart_client()
    out: dict[str, float | None] = {}
    for cl_code in ("M210000", "M220000", "M230000", "M240000"):
        try:
            data = await client.get_fnltt_singl_indx(corp_code, str(year), reprt_code, cl_code)
            for row in (data.get("list") or []):
                key = _strip(row.get("idx_nm"))
                if key and key not in out:
                    out[key] = normalize_pct(row.get("idx_val"))
        except DartClientError:
            continue
    return out


async def _safe_fetch_audit(corp_code: str, year: int) -> tuple[list[dict[str, Any]], str | None]:
    client = get_dart_client()
    try:
        data = await client.get_audit_opinion(corp_code, str(year), "11011")
        rows = data.get("list", []) or []
        return rows, None
    except DartClientError as exc:
        if exc.status == "013":
            return [], "no_filing"
        return [], f"accnutAdtorNmNdAdtOpinion 실패: {exc.status} {exc}"


# ── scope dispatchers ──

async def _fetch_acnt_with_fallback(
    corp_code: str,
    year: int,
    fs_div: str,
) -> tuple[list[dict[str, Any]], str, str | None]:
    """사업보고서(11011) → 3분기(11014) → 반기(11012) → 1분기(11013) 순서로 fallback.

    가장 최근에 가용한 분기 보고서를 사용 (사업보고서 미공시 시).

    return (rows, used_reprt_code, warning_or_None)
    """
    fallback_order = ("11011", "11014", "11012", "11013")
    last_err = None
    for rc in fallback_order:
        rows, err = await _safe_fetch_acnt(corp_code, year, rc, fs_div)
        if rows:
            return rows, rc, None
        if err == "no_filing":
            last_err = f"{year}년 reprt_code={rc} no_filing"
            continue
        if err:
            last_err = err
    return [], "11011", last_err or f"{year}년 모든 reprt_code (사업/반기/분기) 미공시"


async def _fetch_year_metrics(
    corp_code: str,
    year: int,
    fs_div: str,
    *,
    include_prev: bool = True,
    allow_quarterly_fallback: bool = True,
) -> tuple[dict[str, Any], list[str], int]:
    """단일 사업연도 metrics. 당기+전기 fnlttSinglAcnt를 모두 호출.

    Phase 1 v2 최적화 (iteration 11):
    - fnlttSinglIndx 호출 제거 (DART 산출 지표는 자체 계산값 우선이라 사실상 미사용).
    - 당기/전기 × acnt/acntAll = 4 호출을 asyncio.gather로 병렬화.
    - 결과: 8 sequential → (1 sequential fallback + 3 parallel) — 평균 응답 시간 ~3-4배 단축.

    allow_quarterly_fallback=True (default): 사업보고서 미공시 시 분기/반기 보고서로 fallback.
    return (metrics, warnings, evidence_count)
    """
    warnings: list[str] = []
    # 1단계: 당기 fnlttSinglAcnt — fallback 발생 가능성 있어 sequential 유지.
    if allow_quarterly_fallback:
        rows_curr, used_rc, fb_err = await _fetch_acnt_with_fallback(corp_code, year, fs_div)
        if not rows_curr:
            return {}, [fb_err or f"{year}년 데이터 미공시"], 0
        if used_rc != _REPRT_BUSINESS:
            warnings.append(f"{year}년 사업보고서 미공시 — reprt_code={used_rc}로 fallback (반기/분기)")
    else:
        rows_curr, err_curr = await _safe_fetch_acnt(corp_code, year, _REPRT_BUSINESS, fs_div)
        if err_curr == "no_filing":
            return {}, [f"{year}년 사업보고서 미공시 (fnlttSinglAcnt no_filing)"], 0
        if err_curr:
            warnings.append(err_curr)
        used_rc = _REPRT_BUSINESS

    # 2단계: 나머지 3 호출 병렬 (전기 acnt + 당기 acntAll + 전기 acntAll).
    # used_rc는 당기에서 결정된 reprt_code 그대로 사용 (전기도 같은 code로 비교).
    tasks: list[Any] = []
    task_keys: list[str] = []
    if include_prev:
        tasks.append(_safe_fetch_acnt(corp_code, year - 1, _REPRT_BUSINESS, fs_div))
        task_keys.append("prev_acnt")
    tasks.append(_safe_fetch_acnt_all(corp_code, year, _REPRT_BUSINESS, fs_div))
    task_keys.append("curr_acnt_all")
    if include_prev:
        tasks.append(_safe_fetch_acnt_all(corp_code, year - 1, _REPRT_BUSINESS, fs_div))
        task_keys.append("prev_acnt_all")

    parallel_results = await asyncio.gather(*tasks, return_exceptions=False)
    by_key: dict[str, tuple[list[dict[str, Any]], str | None]] = dict(zip(task_keys, parallel_results))

    rows_prev, err_prev = by_key.get("prev_acnt", ([], None))
    if err_prev and err_prev != "no_filing":
        warnings.append(err_prev)

    rows_detail, err_detail = by_key.get("curr_acnt_all", ([], None))
    if err_detail and err_detail != "no_filing":
        warnings.append(err_detail)

    rows_detail_prev, err_dp = by_key.get("prev_acnt_all", ([], None))
    if err_dp and err_dp != "no_filing":
        warnings.append(err_dp)

    bs_is = _build_account_map(rows_curr) if rows_curr else {}
    bs_is_prev = _build_account_map(rows_prev) if rows_prev else None
    detail = _build_account_map_all(rows_detail) if rows_detail else None
    detail_prev = _build_account_map_all(rows_detail_prev) if rows_detail_prev else None

    if not bs_is:
        return {}, warnings + [f"{year}년 BS/IS 핵심 데이터 파싱 실패"], 0

    metrics = _compute_metrics(
        bs_is=bs_is,
        bs_is_prev=bs_is_prev,
        detail=detail,
        detail_prev=detail_prev,
        indx_map=None,  # fnlttSinglIndx 제거 — 자체 계산값 우선이라 미사용
    )
    metrics["year"] = year
    metrics["fs_div"] = fs_div
    metrics["reprt_code"] = used_rc
    return metrics, warnings, 1


def _audit_row_to_dict(row: dict[str, Any]) -> dict[str, Any]:
    """accnutAdtorNmNdAdtOpinion row → 표준화 dict."""
    bsns_label = _strip(row.get("bsns_year"))  # "제56기\n(당기)"
    period_tag = ""
    if "(당기)" in bsns_label:
        period_tag = "current"
    elif "(전기)" in bsns_label:
        period_tag = "prior"
    elif "(전전기)" in bsns_label:
        period_tag = "prior_prior"
    return {
        "bsns_year_raw": bsns_label,
        "period_tag": period_tag,
        "stlm_dt": _strip(row.get("stlm_dt")),
        "adtor": _strip(row.get("adtor")),
        "adt_opinion": _strip(row.get("adt_opinion")),
        "adt_reprt_spcmnt_matter": _strip(row.get("adt_reprt_spcmnt_matter")),
        "emphs_matter": _strip(row.get("emphs_matter")),
        "core_adt_matter": _strip(row.get("core_adt_matter")),
        "rcept_no": _strip(row.get("rcept_no")),
    }


async def _build_audit_opinion_data(
    corp_code: str,
    end_year: int,
    years_back: int = 3,
) -> tuple[dict[str, Any], list[str], list[EvidenceRef]]:
    """감사의견 N년 추이 (end_year 기준 최근 사업보고서 1건이 3년치 반환)."""

    warnings: list[str] = []
    rows, err = await _safe_fetch_audit(corp_code, end_year)
    if err == "no_filing":
        return (
            {"opinions": [], "summary": {"latest_opinion": None, "all_clean": None, "history_years": 0}},
            [f"{end_year}년 감사의견 미공시"],
            [],
        )
    if err:
        warnings.append(err)
        return (
            {"opinions": [], "summary": {"latest_opinion": None, "all_clean": None, "history_years": 0}},
            warnings,
            [],
        )

    # rows는 (당기/전기/전전기) × (CFS+OFS) → 6개. 중복 제거: bsns_year + period_tag 우선, CFS 우선.
    parsed = [_audit_row_to_dict(r) for r in rows]
    # 같은 stlm_dt에서 중복 — 첫 번째만 유지 (DART 응답 순서가 CFS 우선).
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for p in parsed:
        key = f"{p['stlm_dt']}|{p['adt_opinion']}"
        if key in seen:
            continue
        seen.add(key)
        deduped.append(p)

    # 최신 → 과거 순 정렬
    deduped.sort(key=lambda x: x.get("stlm_dt", ""), reverse=True)

    latest = deduped[0] if deduped else None
    all_clean = all("적정" in p.get("adt_opinion", "") for p in deduped) if deduped else None

    evidence: list[EvidenceRef] = []
    if latest and latest.get("rcept_no"):
        evidence.append(EvidenceRef(
            evidence_id=f"ev_audit_{corp_code}_{end_year}",
            source_type=SourceType.DART_API,
            rcept_no=latest["rcept_no"],
            section="회계감사인의 감사의견 (사업보고서)",
            note=f"{latest.get('adt_opinion', '-')} / 감사인 {latest.get('adtor', '-')}",
        ))

    return (
        {
            "opinions": deduped,
            "summary": {
                "latest_opinion": latest.get("adt_opinion") if latest else None,
                "latest_auditor": latest.get("adtor") if latest else None,
                "latest_emphs_matter": latest.get("emphs_matter") if latest else None,
                "latest_kam": latest.get("core_adt_matter") if latest else None,
                "all_clean": all_clean,
                "history_years": len(deduped),
            },
        },
        warnings,
        evidence,
    )


async def _build_yearly(corp_code: str, end_year: int, years: int, fs_div: str) -> tuple[list[dict[str, Any]], list[str]]:
    year_list = list(range(end_year - years + 1, end_year + 1))
    tasks = [_fetch_year_metrics(corp_code, y, fs_div, include_prev=True) for y in year_list]
    results = await asyncio.gather(*tasks)
    out: list[dict[str, Any]] = []
    warnings: list[str] = []
    for metrics, ws, _ev in results:
        warnings.extend(ws)
        if metrics:
            out.append(metrics)
    return out, warnings


async def _build_quarterly(corp_code: str, end_year: int, fs_div: str, num_quarters: int = 12) -> tuple[list[dict[str, Any]], list[str]]:
    """4Q × 3년 = 12분기. fnlttSinglAcnt + reprt_code 4개 × 3년 = 12 호출."""

    warnings: list[str] = []
    out: list[dict[str, Any]] = []
    years = list(range(end_year - 2, end_year + 1))
    quarter_labels = {
        "11013": "Q1",
        "11012": "Q2",
        "11014": "Q3",
        "11011": "Q4",
    }
    tasks = []
    keys = []
    for y in years:
        for rc, label in quarter_labels.items():
            tasks.append(_safe_fetch_acnt(corp_code, y, rc, fs_div))
            keys.append((y, rc, label))
    results = await asyncio.gather(*tasks)
    for (year, rc, label), (rows, err) in zip(keys, results):
        if err == "no_filing":
            continue
        if err:
            warnings.append(err)
            continue
        if not rows:
            continue
        bs_is = _build_account_map(rows)
        if not bs_is:
            continue
        out.append({
            "year": year,
            "quarter": label,
            "reprt_code": rc,
            "revenue_krw": bs_is.get("revenue"),
            "operating_profit_krw": bs_is.get("operating_profit"),
            "net_income_krw": bs_is.get("net_income"),
            "total_assets_krw": bs_is.get("total_assets"),
            "total_equity_krw": bs_is.get("total_equity"),
            "total_liabilities_krw": bs_is.get("total_liabilities"),
            "operating_margin_pct": _safe_pct(bs_is.get("operating_profit"), bs_is.get("revenue")),
            "net_profit_margin_pct": _safe_pct(bs_is.get("net_income"), bs_is.get("revenue")),
        })
    out.sort(key=lambda x: (x["year"], list(quarter_labels.values()).index(x["quarter"])))
    return out[-num_quarters:], warnings


# ── public payload builder ──

def _unsupported_scope_payload(company_query: str, scope: str) -> dict[str, Any]:
    return ToolEnvelope(
        tool="financial_metrics",
        status=AnalysisStatus.REQUIRES_REVIEW,
        subject=company_query,
        warnings=[f"`{scope}` scope는 아직 지원하지 않는다."],
        data={"query": company_query, "scope": scope, "available_scopes": sorted(_SUPPORTED_SCOPES)},
    ).to_dict()


# ── Phase 3 F2 — 응답 caching (TTL 5분) ──
# 같은 (company, scope, year, consolidated) 조합 재호출 시 동일 결과 보장.
# advise_vote의 3 run 호출 시 모든 run에서 동일 fm_payload 반환 → cash_dividend 결정 결정성.
import time as _time_mod
_FM_CACHE: dict[tuple, tuple[float, dict[str, Any]]] = {}
_FM_CACHE_TTL = 300.0  # 5분


def _fm_cache_get(key: tuple) -> dict[str, Any] | None:
    entry = _FM_CACHE.get(key)
    if not entry:
        return None
    ts, payload = entry
    if _time_mod.time() - ts > _FM_CACHE_TTL:
        _FM_CACHE.pop(key, None)
        return None
    return payload


def _fm_cache_set(key: tuple, payload: dict[str, Any]) -> None:
    _FM_CACHE[key] = (_time_mod.time(), payload)


async def build_financial_metrics_payload(
    company_query: str,
    *,
    scope: str = "summary",
    year: int | None = None,
    years: int = 3,
    consolidated: bool = True,
) -> dict[str, Any]:
    if scope not in _SUPPORTED_SCOPES:
        return _unsupported_scope_payload(company_query, scope)

    # F2 cache check
    cache_key = (company_query, scope, year, years, consolidated)
    cached = _fm_cache_get(cache_key)
    if cached is not None:
        return cached

    fs_div = "CFS" if consolidated else "OFS"
    client = get_dart_client()
    calls_start = client.api_call_snapshot()

    resolution = await resolve_company_query(company_query)
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        return ToolEnvelope(
            tool="financial_metrics",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[f"'{company_query}'에 해당하는 회사를 찾지 못했다."],
            data={
                "query": company_query,
                "scope": scope,
                "usage": build_usage(client.api_call_snapshot() - calls_start),
            },
        ).to_dict()
    if resolution.status == AnalysisStatus.AMBIGUOUS:
        return ToolEnvelope(
            tool="financial_metrics",
            status=AnalysisStatus.AMBIGUOUS,
            subject=company_query,
            warnings=["회사 식별이 애매해 재무 데이터를 자동 선택하지 않았다."],
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
                "usage": build_usage(client.api_call_snapshot() - calls_start),
            },
        ).to_dict()

    selected = resolution.selected
    corp_code = selected["corp_code"]
    target_year = year or _default_recent_year()

    warnings: list[str] = []
    evidence_refs: list[EvidenceRef] = []
    data: dict[str, Any] = {
        "query": company_query,
        "company_id": _company_id(selected),
        "canonical_name": selected.get("corp_name", ""),
        "identifiers": {
            "ticker": selected.get("stock_code", ""),
            "corp_code": corp_code,
        },
        "scope": scope,
        "year": target_year,
        "fs_div": fs_div,
        "consolidated": consolidated,
        "available_scopes": sorted(_SUPPORTED_SCOPES),
    }

    parsing_failures = 0
    filing_count = 0

    if scope == "summary":
        metrics, ws, ev_count = await _fetch_year_metrics(corp_code, target_year, fs_div, include_prev=True)
        warnings.extend(ws)
        if metrics:
            data["summary"] = metrics
            filing_count = 1
            evidence_refs.append(EvidenceRef(
                evidence_id=f"ev_fm_summary_{corp_code}_{target_year}",
                source_type=SourceType.DART_API,
                section=f"사업보고서 ({target_year}) 단일회사 주요계정 + 전체재무제표 + 주요지표",
                note=f"{selected.get('corp_name', '')} {target_year}년 {fs_div}",
            ))
        else:
            parsing_failures = 1

    elif scope == "yearly":
        rows, ws = await _build_yearly(corp_code, target_year, years, fs_div)
        warnings.extend(ws)
        data["yearly"] = rows
        filing_count = len(rows)
        if rows:
            evidence_refs.append(EvidenceRef(
                evidence_id=f"ev_fm_yearly_{corp_code}_{target_year}",
                source_type=SourceType.DART_API,
                section=f"사업보고서 ({rows[0]['year']}~{rows[-1]['year']}) {len(rows)}년 추이",
                note=f"{selected.get('corp_name', '')} 연간 추이",
            ))

    elif scope == "quarterly":
        rows, ws = await _build_quarterly(corp_code, target_year, fs_div)
        warnings.extend(ws)
        data["quarterly"] = rows
        filing_count = len(rows)
        if rows:
            evidence_refs.append(EvidenceRef(
                evidence_id=f"ev_fm_quarterly_{corp_code}_{target_year}",
                source_type=SourceType.DART_API,
                section=f"분기/반기/사업보고서 {len(rows)}분기 추이",
                note=f"{selected.get('corp_name', '')} 분기 추이",
            ))

    elif scope == "yoy":
        # 당기 + 전기 metrics + 감사의견 cross-check — 3 독립 호출 병렬화 (5-8초 ↓)
        curr_t, prev_t, audit_t = await asyncio.gather(
            _fetch_year_metrics(corp_code, target_year, fs_div, include_prev=True),
            _fetch_year_metrics(corp_code, target_year - 1, fs_div, include_prev=True),
            _build_audit_opinion_data(corp_code, target_year, years_back=2),
        )
        curr, ws_curr, _ev1 = curr_t
        prev, ws_prev, _ev2 = prev_t
        audit_data, audit_ws, _audit_ev = audit_t
        warnings.extend(ws_curr)
        warnings.extend(ws_prev)
        warnings.extend(audit_ws)
        audit_curr = audit_data.get("opinions", [{}])[0] if audit_data.get("opinions") else None
        audit_prev = audit_data.get("opinions", [{}, {}])[1] if len(audit_data.get("opinions", [])) >= 2 else None

        signals = _detect_yoy_signals(curr or {}, prev or {}, audit_curr, audit_prev) if curr else []
        data["yoy"] = {
            "current": curr,
            "prior": prev,
            "alerts": signals,
            "audit_opinion": {
                "current": audit_curr,
                "prior": audit_prev,
            },
        }
        filing_count = (1 if curr else 0) + (1 if prev else 0)
        if curr:
            evidence_refs.append(EvidenceRef(
                evidence_id=f"ev_fm_yoy_{corp_code}_{target_year}",
                source_type=SourceType.DART_API,
                section=f"전년 대비 ({target_year - 1} → {target_year}) — alerts {len(signals)}개",
                note=", ".join(signals[:5]) if signals else "alerts 없음",
            ))

    elif scope == "qoq":
        rows, ws = await _build_quarterly(corp_code, target_year, fs_div, num_quarters=4)
        warnings.extend(ws)
        # 직전 분기 vs 당기 비교
        if len(rows) >= 2:
            curr_q = rows[-1]
            prev_q = rows[-2]
            data["qoq"] = {
                "current": curr_q,
                "prior": prev_q,
                "alerts": _detect_qoq_alerts(curr_q, prev_q),
            }
        else:
            data["qoq"] = {"current": rows[-1] if rows else None, "prior": None, "alerts": []}
        filing_count = len(rows)
        if rows:
            evidence_refs.append(EvidenceRef(
                evidence_id=f"ev_fm_qoq_{corp_code}_{target_year}",
                source_type=SourceType.DART_API,
                section=f"전분기 대비 (최근 {len(rows)}분기 기준)",
                note=f"{selected.get('corp_name', '')} 전분기 비교",
            ))

    elif scope == "audit_opinion":
        audit_data, audit_ws, audit_ev = await _build_audit_opinion_data(corp_code, target_year, years_back=years)
        warnings.extend(audit_ws)
        data["audit_opinion"] = audit_data
        filing_count = audit_data.get("summary", {}).get("history_years", 0)
        evidence_refs.extend(audit_ev)

    filing_meta = build_filing_meta(filing_count=filing_count, parsing_failures=parsing_failures)
    if filing_meta["no_filing"]:
        status = AnalysisStatus.NO_FILING
        warnings.append(f"{target_year}년 사업보고서 재무 공시 미존재 (정상 — 비상장/신규/폐업 회사 가능성)")
    elif parsing_failures > 0:
        status = AnalysisStatus.PARTIAL
    else:
        status = AnalysisStatus.EXACT

    data.update(filing_meta)
    data["usage"] = build_usage(client.api_call_snapshot() - calls_start)

    payload = ToolEnvelope(
        tool="financial_metrics",
        status=status,
        subject=selected.get("corp_name", company_query),
        warnings=warnings,
        data=data,
        evidence_refs=evidence_refs,
        next_actions=_next_actions(scope, data),
    ).to_dict()
    _fm_cache_set(cache_key, payload)  # F2 — TTL 5분 cache
    return payload


def _detect_qoq_alerts(curr: dict[str, Any], prev: dict[str, Any]) -> list[str]:
    """전분기 대비 alerts (간소화 — 분기 데이터는 비교 의미 제한적)."""
    alerts: list[str] = []
    op = curr.get("operating_profit_krw")
    op_prev = prev.get("operating_profit_krw")
    if op is not None and op < 0 and (op_prev is None or op_prev >= 0):
        alerts.append("operating_loss_quarter")
    rev = curr.get("revenue_krw")
    rev_prev = prev.get("revenue_krw")
    if rev is not None and rev_prev is not None and rev_prev > 0:
        chg = (rev - rev_prev) / rev_prev
        if chg < -0.20:
            alerts.append("revenue_decline_qoq")
    return alerts


def _next_actions(scope: str, data: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    if scope == "summary":
        actions.append("scope=`yearly`로 최근 3년 추이 비교")
        actions.append("scope=`audit_opinion`으로 감사인 변경/한정의견 추적")
    if scope == "yoy":
        alerts = (data.get("yoy", {}) or {}).get("alerts", [])
        if "operating_loss" in alerts or "loss_conversion" in alerts:
            actions.append("적자 원인 분석: scope=`quarterly` + 사업보고서 본문 (corp_gov_report)")
        if "accruals_red" in alerts or "receivables_surge" in alerts:
            actions.append("회계 risk 강화 cross-check: 분기 영업CF 추세 + 감사의견 KAM")
    if scope == "audit_opinion":
        opinions = (data.get("audit_opinion", {}) or {}).get("opinions", [])
        if any("적정" not in o.get("adt_opinion", "") for o in opinions):
            actions.append("non-clean 의견: 후보 사외이사 재직 시점 cross-check (이사 회계 risk 이력 검증)")
    return actions


def _default_recent_year() -> int:
    """현재 시점 기준 가장 최근 완료된 사업연도.

    DART 사업보고서는 결산일 90일 이내 (3월말까지) 제출 의무.
    오늘이 4월 이후면 전년도 사업보고서 가용, 그 전이면 전전년.
    """
    from datetime import date
    today = date.today()
    if today.month >= 4:
        return today.year - 1
    return today.year - 2
