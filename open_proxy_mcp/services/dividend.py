"""dividend facade 서비스."""

from __future__ import annotations

import asyncio
import calendar
from datetime import date
import re
import time
from typing import Any

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.services.company import _company_id, resolve_company_query
from open_proxy_mcp.services.company import company_not_found_warning
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
from open_proxy_mcp.services.filing_search import (
    fetch_filings_for_title_scan,
    report_name_matches,
)
from open_proxy_mcp.services.dividend_parser import (
    DIVIDEND_KEYWORDS,
    build_dividend_summary,
    parse_dividend_decision,
    parse_dividend_items,
    safe_float,
    safe_int,
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


# ── 회계연도 라벨 기준 ────────────────────────────────────────────────
# 이 도구의 FY는 **결산 종료연도** 기준이다 — FY2025 = 2025년에 끝나는 사업연도.
# 6월 결산 회사는 회사 IR 문서가 시작연도로 부르는 일이 많아(포시에스 「FY2025」=
# 2025-07-01~2026-06-30) 같은 라벨이 다른 12개월을 가리킨다. 2026-08-28 U 지적 B-6 —
# 「어느 쪽도 기준을 밝히지 않아 40% 약속이 어느 해 것인지 확정하지 못했다」.
# → 라벨 옆에 항상 날짜 구간과 결산월을 붙이고, 기준을 한 줄로 적는다.
FISCAL_YEAR_BASIS = "이 도구의 FY는 결산 종료연도 기준 (FY2025 = 2025년에 끝나는 사업연도)"


def _fiscal_end_month(stlm_dt: str | None) -> int | None:
    """alotMatter `stlm_dt`(결산기준일, 예 `2025-06-30`) → 결산월. 모르면 None."""
    digits = "".join(ch for ch in (stlm_dt or "") if ch.isdigit())
    if len(digits) < 6:
        return None
    month = int(digits[4:6])
    return month if 1 <= month <= 12 else None


def _fiscal_period(fiscal_year: int, end_month: int | None) -> dict[str, str] | None:
    """FY(결산 종료연도) + 결산월 → 사업연도 날짜 구간. 결산월을 모르면 지어내지 않는다."""
    if not end_month:
        return None
    last_day = calendar.monthrange(fiscal_year, end_month)[1]
    end = date(fiscal_year, end_month, last_day)
    start = date(fiscal_year, 1, 1) if end_month == 12 else date(fiscal_year - 1, end_month + 1, 1)
    return {"start": start.isoformat(), "end": end.isoformat()}


def _fiscal_period_fields(fiscal_year: int, end_month: int | None) -> dict[str, Any]:
    """summary/history row 에 붙일 회계연도 메타."""
    period = _fiscal_period(fiscal_year, end_month)
    return {
        "fiscal_year": fiscal_year,
        "fiscal_year_end_month": end_month,
        "period_start": (period or {}).get("start"),
        "period_end": (period or {}).get("end"),
    }


def _latest_completed_fiscal_year(today: date, end_month: int | None) -> int | None:
    """오늘 기준 **결산이 이미 끝난** 가장 최근 사업연도(종료연도 라벨)."""
    if not end_month:
        return None
    return today.year if today.month > end_month else today.year - 1


async def _search_dividend_filings(
    corp_code: str, start_year: int, end_year: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], str | None]:
    """I001 raw를 1회 받아 배당결정 + 명부폐쇄(기준일) 둘 다 필터한다.

    배당결정·명부폐쇄 둘 다 I001(주요경영사항) 하위라, 예전엔 검색을 2번(메인 +
    pre_dividend) 돌렸다. raw 1회 fetch 후 client-side로 양쪽을 필터해 검색 1회·DART
    호출 1개를 절약한다. 반환: (배당결정 filings, 명부폐쇄 notices, fetch notices, error).
    """
    items, notices, error = await fetch_filings_for_title_scan(
        corp_code=corp_code,
        bgn_de=f"{start_year}0101",
        end_de=f"{end_year + 1}1231",
        pblntf_tys="",
        pblntf_detail_ty="I001",
        keyword_label="배당결정+명부폐쇄",
    )
    if error:
        return [], [], notices, f"배당결정 공시 검색 실패: {error}"
    dividend_filings = [i for i in items if report_name_matches(i, DIVIDEND_KEYWORDS)]
    record_notices = [
        i for i in items if report_name_matches(i, _RECORD_DATE_NOTICE_KEYWORDS, strip_spaces=True)
    ]
    return dividend_filings, record_notices, notices, None


def _in_window(date_value: str, start_ymd: str, end_ymd: str) -> bool:
    digits = "".join(ch for ch in (date_value or "") if ch.isdigit())
    return bool(digits) and start_ymd <= digits <= end_ymd


async def _decision_details(filings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """배당 결정 공시 본문 파싱. 병렬 조회 (260607 — 순차 N건 → max 1건 시간).

    문서 순서를 보존하기 위해 gather 결과를 입력 순서대로 조립한다.
    """
    client = get_dart_client()

    async def _fetch(item: dict[str, Any]) -> dict[str, Any] | None:
        try:
            doc = await client.get_document_cached(item["rcept_no"])
        except Exception:
            return None
        parsed = parse_dividend_decision(doc.get("text", ""))
        if not parsed:
            return None
        parsed["rcept_no"] = item.get("rcept_no", "")
        parsed["rcept_dt"] = item.get("rcept_dt", "")
        parsed["report_name"] = item.get("report_nm", "")
        return parsed

    results = await asyncio.gather(*[_fetch(item) for item in filings])
    return [r for r in results if r is not None]


async def _annual_summary(corp_code: str, year: int) -> tuple[dict[str, Any], str | None]:
    client = get_dart_client()
    try:
        data = await client.get_dividend_info(corp_code, str(year), "11011")
    except DartClientError as exc:
        return {}, f"alotMatter 조회 실패: {exc.status}"
    items = parse_dividend_items(data)
    if not items:
        return {}, None
    summary = build_dividend_summary(items, "사업보고서(기말)")
    if summary:
        summary["source"] = "alotMatter"
        end_month = _fiscal_end_month(summary.get("stlm_dt"))
        digits = "".join(ch for ch in (summary.get("stlm_dt") or "") if ch.isdigit())
        fy = int(digits[:4]) if len(digits) >= 4 else year
        summary.update(_fiscal_period_fields(fy, end_month))
    return summary, None


def _alot_multiyear_summaries(latest_summary: dict[str, Any] | None) -> dict[int, dict[str, Any]]:
    """최신 사업보고서 alotMatter 1회 응답의 당기/전기/전전기 컬럼으로
    최근 3개 사업연도 요약을 구성한다.

    연도별 alotMatter 개별 호출은 (1) 특정 연도 DPS=0 반환 (2) 비면 자회사·정정
    공시 합산 fallback 유발 등으로 불안정하다. 반면 최신 보고서의 다년 컬럼
    (current/previous/before_previous)은 단일 출처·동일 기준이라 권위 있다.
    이 값을 history 연간 DPS/배당성향/수익률의 source of truth로 쓴다.
    """
    if not latest_summary:
        return {}
    items = latest_summary.get("items") or []
    if not items:
        return {}
    stlm = (latest_summary.get("stlm_dt") or "").strip()
    digits = "".join(ch for ch in stlm if ch.isdigit())
    if len(digits) < 4:
        return {}
    base_year = int(digits[:4])
    # 결산월을 stlm_dt에서 읽는다. 예전엔 아래에서 stlm_dt를 f"{fy}-12-31"로 박아
    # 6월 결산 회사의 사업연도 구간이 통째로 틀렸다(2026-08-28 U 지적 B-6).
    end_month = _fiscal_end_month(stlm)
    # 컬럼 → 사업연도 offset (당기=base, 전기=-1, 전전기=-2)
    columns = {"current": 0, "previous": -1, "before_previous": -2}
    out: dict[int, dict[str, Any]] = {}
    for col, offset in columns.items():
        fy = base_year + offset
        cash_dps = 0
        cash_dps_pref = 0
        payout: float | None = None
        yld: float | None = None
        yld_pref: float | None = None
        total_amount = 0
        net_income = 0
        col_has_face_value = False  # 액면가 = 그 해 회사 존재(보고서 범위 내) 신호
        for item in items:
            cat = item.get("category", "")
            sknd = item.get("stock_type", "")
            val_raw = item.get(col, "")
            if "주당액면가액" in cat and safe_int(val_raw) > 0:
                col_has_face_value = True
            if "주당 현금배당금" in cat:
                v = safe_int(val_raw)
                if "우선주" in sknd:
                    cash_dps_pref = v
                elif "보통주" in sknd or v > 0:
                    # stock_type="-" 빈 행("-"→0)이 보통주 실제값을 덮어쓰지 않도록
                    # 보통주 명시이거나 값이 있을 때만 반영.
                    cash_dps = v
            elif "현금배당금총액" in cat:
                total_amount = safe_int(val_raw)
            elif "현금배당성향" in cat:
                v = safe_float(val_raw)
                if v > 0 and (payout is None or "연결" in cat):
                    payout = v
            elif "현금배당수익률" in cat:
                v = safe_float(val_raw)
                if v > 0:
                    if "우선주" in sknd:
                        yld_pref = v
                    else:
                        yld = v
            elif "연결" in cat and "당기순이익" in cat:
                net_income = safe_int(val_raw)
        # 컬럼 전체가 비어 있으면(보고서 범위 밖/회사 미존재) 스킵 → fallback 위임.
        # 회사가 존재했는데(액면가/순이익 있음) 배당만 0이면 = 무배당이므로 0-summary로
        # 유지한다 — pending_annual 제거 후 history 윈도우에서 무배당 연도가 빠지지 않게.
        existed = col_has_face_value or net_income != 0
        if cash_dps <= 0 and total_amount <= 0 and payout is None and not existed:
            continue
        fiscal = _fiscal_period_fields(fy, end_month)
        out[fy] = {
            "period": f"{fy} 사업보고서(기말)",
            "stlm_dt": fiscal["period_end"] or f"{fy}-12-31",
            **fiscal,
            "cash_dps": cash_dps,
            "cash_dps_preferred": cash_dps_pref,
            "stock_dps": 0,
            "special_dps": 0,
            "total_dps": cash_dps,
            "total_amount_mil": total_amount,
            "payout_ratio_dart": payout,
            "yield_dart": yld,
            "yield_preferred_dart": yld_pref,
            "net_income_consolidated_mil": net_income,
            "source": "alotMatter_multiyear",
        }
    return out


# 최신 미확정 사업연도의 '중간배당 진행분'을 분기/반기 alotMatter 누적 컬럼에서 읽는다.
# 가장 최근 기간 우선: 3분기(11014) → 반기(11012) → 1분기(11013).
_INTERIM_REPRT: tuple[tuple[str, str], ...] = (
    ("11014", "3분기까지"),
    ("11012", "반기까지"),
    ("11013", "1분기까지"),
)


async def _interim_dividend_from_quarterly(corp_code: str, year: int) -> dict[str, Any] | None:
    """사업보고서가 배당을 아직 확정하지 않은 최신 연도의 '중간배당 진행분'을
    분기/반기 alotMatter의 당기(current) 누적 컬럼에서 읽는다.

    분기/반기 alotMatter 당기값 = 해당 기간까지 누적 배당 (현대차 3분기=Q1-Q3 합).
    사업보고서(11011)와 동일한 15행 구조라 `parse_dividend_items`로 파싱해 보통주
    주당현금배당금·총액·배당성향을 추출한다. 가장 최근 기간부터 시도해 양수 DPS가 잡히는
    첫 보고서를 반환, 없으면 None. (출처 맵 B — wiki/rules/disclosures/배당공시유형.md)
    """
    client = get_dart_client()
    for reprt_code, period_label in _INTERIM_REPRT:
        try:
            data = await client.get_dividend_info(corp_code, str(year), reprt_code)
        except DartClientError:
            continue
        items = parse_dividend_items(data)
        if not items:
            continue
        cash_dps = 0
        total_amount = 0
        payout: float | None = None
        for item in items:
            cat = item.get("category", "")
            sknd = item.get("stock_type", "")
            val_raw = item.get("current", "")
            if "주당 현금배당금" in cat:
                v = safe_int(val_raw)
                if "우선주" in sknd:
                    continue
                if "보통주" in sknd or v > 0:  # 빈 행("-"→0)이 보통주 실제값 덮어쓰지 않게
                    cash_dps = v
            elif "현금배당금총액" in cat:
                total_amount = safe_int(val_raw)
            elif "현금배당성향" in cat:
                v = safe_float(val_raw)
                if v > 0 and (payout is None or "연결" in cat):
                    payout = v
        if cash_dps > 0:
            return {
                "interim_dps": cash_dps,
                "interim_total_mil": total_amount,
                "interim_payout_ratio": payout,
                "period": period_label,
                "reprt_code": reprt_code,
            }
    return None


def _common_cash_dps_from_items(items: list[dict[str, Any]], column: str = "current") -> int:
    """alotMatter items에서 보통주 주당현금배당금을 읽는다 (빈 행 덮어쓰기 가드 포함)."""
    dps = 0
    for item in items:
        if "주당 현금배당금" not in item.get("category", ""):
            continue
        sknd = item.get("stock_type", "")
        v = safe_int(item.get(column, ""))
        if "우선주" in sknd:
            continue
        if "보통주" in sknd or v > 0:
            dps = v
    return dps


async def _quarterly_dps_from_cumulative(corp_code: str, year: int) -> list[dict[str, Any]]:
    """분기/반기/사업 alotMatter의 누적 보통주 주당현금배당금을 차분해 분기별 권위 DPS를 만든다.

    Q1 = 1분기(11013) / Q2 = 반기(11012) - Q1 / Q3 = 3분기(11014) - 반기 / 결산 = 연간(11011) - 3분기.
    누락 보고서가 있으면 차분 불가한 분기는 제외. 결정공시 fiscal-year 추론(_bucket_fiscal_year)이
    전환기 경계에서 어긋날 때 권위 출처(B)로 교정하는 용도. (wiki/rules/disclosures/배당공시유형.md #2)
    """
    client = get_dart_client()
    cum: dict[str, int] = {}
    for reprt_code in ("11013", "11012", "11014", "11011"):
        try:
            data = await client.get_dividend_info(corp_code, str(year), reprt_code)
        except DartClientError:
            continue
        items = parse_dividend_items(data)
        if items:
            cum[reprt_code] = _common_cash_dps_from_items(items, "current")
    out: list[dict[str, Any]] = []
    c1, c2, c3, ann = cum.get("11013"), cum.get("11012"), cum.get("11014"), cum.get("11011")
    if c1 is not None:
        out.append({"quarter": "Q1", "dps_common_krw": c1})
    if c1 is not None and c2 is not None:
        out.append({"quarter": "Q2", "dps_common_krw": c2 - c1})
    if c2 is not None and c3 is not None:
        out.append({"quarter": "Q3", "dps_common_krw": c3 - c2})
    if c3 is not None and ann is not None:
        out.append({"quarter": "결산", "dps_common_krw": ann - c3})
    return out


def _cum_full_from_items(items: list[dict[str, Any]], column: str = "current") -> dict[str, Any] | None:
    """alotMatter 한 컬럼에서 보통/우선 DPS + 배당총액(백만)을 종합 추출. 회사 미존재(컬럼 공란)는 None."""
    common = 0
    pref = 0
    total_mil = 0
    exists = False
    for item in items:
        cat = item.get("category", "")
        sknd = item.get("stock_type", "")
        v = safe_int(item.get(column, ""))
        if "주당액면가액" in cat and v > 0:
            exists = True
        elif "연결" in cat and "당기순이익" in cat and v != 0:
            exists = True
        if "주당 현금배당금" in cat:
            if "우선주" in sknd:
                pref = v
            elif "보통주" in sknd or v > 0:
                common = v
        elif "현금배당금총액" in cat:
            total_mil = v
    if not exists:
        return None
    return {"dps_common": common, "dps_preferred": pref, "total_mil": total_mil}


async def _quarterly_full_from_cumulative(corp_code: str, year: int) -> list[dict[str, Any]]:
    """분기/반기/사업 alotMatter 누적값을 차분해 분기별 **보통+우선 DPS + 배당총액**을 만든다.

    `_quarterly_dps_from_cumulative`(보통주 DPS만)의 확장판. 결정공시 버킷팅보다 정확하고
    우선주·총액·무배당 분기(0)까지 잡는다. 최신/미완료 연도의 분기 분해에 권위 사용 (정확도 우선).
    각 row: {quarter, dps_common, dps_preferred, total_mil}. 차분 불가 분기는 제외.
    """
    client = get_dart_client()
    cum: dict[str, dict[str, Any]] = {}
    for reprt_code in ("11013", "11012", "11014", "11011"):
        try:
            data = await client.get_dividend_info(corp_code, str(year), reprt_code)
        except DartClientError:
            continue
        col = _cum_full_from_items(parse_dividend_items(data), "current")
        if col is not None:
            cum[reprt_code] = col
    seq = [("Q1", "11013"), ("반기", "11012"), ("3분기", "11014"), ("연간", "11011")]
    have = [(lbl, cum[rc]) for lbl, rc in seq if rc in cum]
    out: list[dict[str, Any]] = []
    prev = {"dps_common": 0, "dps_preferred": 0, "total_mil": 0}
    labels = {"Q1": "Q1", "반기": "Q2", "3분기": "Q3", "연간": "결산"}
    for lbl, cur in have:
        out.append({
            "quarter": labels[lbl],
            "dps_common": cur["dps_common"] - prev["dps_common"],
            "dps_preferred": cur["dps_preferred"] - prev["dps_preferred"],
            "total_mil": cur["total_mil"] - prev["total_mil"],
        })
        prev = cur
    return out


# 주주명부폐쇄(기준일)결정 공시 §2 의 배당기준일. 보일러플레이트("기준일을 정할 수…")는
# 날짜가 바로 안 붙어 매칭 안 됨 (whitespace만 허용).
_NOTICE_RECORD_DATE_RE = re.compile(r"기준일[\s]*(\d{4}-\d{2}-\d{2})")


async def _record_date_from_notices(notices: list[dict[str, Any]], target_year: int) -> str | None:
    """주주명부폐쇄(기준일)결정 공시(출처 D) 본문에서 **target_year 결산 배당기준일**을 추출한다.

    선배당-후결의 '확정 전' 케이스: 현금배당결정(금액)이 아직 없어도 결산 기준일은 이 공시에
    이미 설정돼 있다. 단 결산 배당기준일은 target_year 11-12월 또는 target_year+1 1-4월에
    찍히므로(전년 결산 기준일과 혼동 방지), 해당 연도 결산에 맞는 기준일만 반환.
    자회사 공시(자회사의 주요경영사항)는 모회사 기준일이 아니므로 제외.
    """
    if not notices:
        return None
    client = get_dart_client()
    for n in sorted(notices, key=lambda x: x.get("rcept_dt", ""), reverse=True):
        if "자회사" in (n.get("report_nm") or ""):
            continue
        rcept = n.get("rcept_no")
        if not rcept:
            continue
        try:
            doc = await client.get_document_cached(rcept)
        except Exception:
            continue
        m = _NOTICE_RECORD_DATE_RE.search(doc.get("text", ""))
        if not m:
            continue
        rd = m.group(1)
        y, mo = int(rd[:4]), int(rd[5:7])
        # target_year 결산 기준일: 그 해 11-12월 또는 다음 해 1-4월.
        if (y == target_year and mo >= 11) or (y == target_year + 1 and mo <= 4):
            return rd
    return None


def _decisions_summary_for_year(
    decisions: list[dict[str, Any]], year: int, end_month: int | None = None
) -> dict[str, Any]:
    """해당 연도 배당결정 공시를 합산해 summary 형식으로 반환.

    `alotMatter`가 비어 있을 때(사업보고서 미제출 또는 무배당 회사가 특별배당·분기배당
    결정만 공시한 경우) 확정된 배당 결정을 source of truth로 사용하기 위한 fallback.
    """

    year_decisions = [d for d in decisions if _bucket_fiscal_year(d) == year]
    # 정정/재공시 중복 제거 (같은 fiscal_year/분기/기준일 → 최신 1건). 자회사 공시는
    # 상위 details 단계에서 이미 제외됨.
    year_decisions = _effective_decisions(year_decisions)

    if not year_decisions:
        return {}

    cash_dps_total = sum(int(d.get("dps_common") or 0) for d in year_decisions)
    cash_dps_pref_total = sum(int(d.get("dps_preferred") or 0) for d in year_decisions)
    total_amount_mil = sum(int((d.get("total_amount") or 0)) for d in year_decisions) // 1_000_000
    # 🔴 `special_dps`는 배당원장 경로에서 **특별배당분만**을 뜻한다(total_dps = cash + special).
    # 예전에는 여기서 특별배당이 낀 결의의 주당배당금 **전액**을 더했다 — 삼성전자 FY2020이
    # 1,578(실제 특별분)이 아니라 1,932(전액)로 나왔다. 비고에서 뽑아낸 특별분만 더한다.
    # 금액을 못 뽑으면 0이다. 특별배당이 있었다는 사실 자체는 `has_special`이 따로 나른다.
    special_dps = sum(int(d.get("special_dps_krw") or 0) for d in year_decisions)

    fiscal = _fiscal_period_fields(year, end_month)
    return {
        "period": f"{year} 배당결정 공시 합산",
        "stlm_dt": fiscal["period_end"] or f"{year}-12-31",
        **fiscal,
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
        # 정정/재공시 중복 제거 후 집계 — 단일 결산배당이 정정 때문에 2건으로 잡혀
        # "분기/중간 포함"으로 오분류되는 것 방지 (자회사는 상위에서 이미 제외).
        yearly = _effective_decisions(decisions_by_year.get(year, []))
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
            "period_start": summary.get("period_start"),
            "period_end": summary.get("period_end"),
            "fiscal_year_end_month": summary.get("fiscal_year_end_month"),
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


def _effective_decisions(decisions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """같은 (fiscal_year, 분기, 기준일) 그룹에서 최신(rcept_dt, rcept_no) 1건만 남긴다.

    정정공시(`[기재정정]…`)나 동일 결의 재공시가 합산에 중복 반영돼 DPS가 부풀려지는
    것을 막기 위한 dedup. `_quarterly_breakdown` 의 is_superseded 판정과 같은 키를 쓴다.
    """
    ordered = sorted(
        decisions,
        key=lambda d: (d.get("rcept_dt", ""), d.get("rcept_no", "")),
    )
    effective: dict[tuple, dict[str, Any]] = {}
    for d in ordered:
        key = (_bucket_fiscal_year(d), _quarter_label(d), (d.get("record_date") or "").strip())
        effective[key] = d  # 뒤(최신)가 앞을 덮어씀
    return list(effective.values())


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
        tool="dividend_disclosure",
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
            tool="dividend_disclosure",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[company_not_found_warning(company_query)],
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
            tool="dividend_disclosure",
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

    async def timed_call(stage: str, coro):
        started_at = time.perf_counter()
        try:
            return await coro
        finally:
            _mark(stage, started_at)

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

    # ── 메타 cross-link: 선배당-후결의 + 감액배당 ────────────────────────
    # summary/CSR/TSR/history scope에서 추가 호출 발생. 배당 요약/공시 파싱과 독립이므로
    # 먼저 시작해 downstream DART/API 대기와 겹친다 (추가 지연 ~0).
    # history는 최신 사업연도가 "확정 전(기준일만 설정)"인지 판정하려고 선배당 신호만 쓴다
    # (감액배당 cross-link은 불필요해 생략 → 호출 1회만 추가).
    pre_dividend_post_resolution = False
    record_date_notices: list[dict[str, Any]] = []
    capital_reserve_reduction = False
    capital_reserve_agendas: list[dict[str, Any]] = []
    meta_task: asyncio.Task[None] | None = None
    # 선배당-후결의(명부폐쇄) 신호는 메인 I001 검색에 통합됨 (gather 직후 record_notices 재사용).
    # 아래 meta_task는 감액배당 cross-link(shareholder_meeting 의존)만 담당한다.
    if scope == "summary":
        async def run_capital_reserve_detection() -> None:
            nonlocal capital_reserve_reduction, capital_reserve_agendas
            stage_started_at = time.perf_counter()
            try:
                capital_reserve_reduction, capital_reserve_agendas = await _detect_capital_reserve_reduction(
                    company_query, target_year
                )
            except Exception as exc:
                warnings.append(f"감액배당 메타 추출 실패: {exc}")
            finally:
                _mark("capital_reserve_detection", stage_started_at)

        meta_task = asyncio.create_task(run_capital_reserve_detection())

    # 연도별 값은 target_year 사업보고서의 **다년 컬럼(당기/전기/전전기)**이 권위라,
    # 최근 3개 연도는 latest_summary 1콜로 충분하다(_alot_multiyear_summaries). 과거엔
    # 비-target 연도마다 alotMatter를 개별 호출(pending_annual)했으나 multiyear와 중복이라
    # 제거했다 — multiyear 미커버 연도는 배당결정 공시 합산(_decisions_summary_for_year,
    # 추가 호출 0)으로 fallback. (감사 #1: 중복 호출 -2/회사)

    # latest_summary와 filings 검색은 independent — 병렬 호출.
    latest_summary_task = timed_call(
        "summary_and_filings.annual_summary",
        _annual_summary(selected["corp_code"], target_year),
    )
    filings_task = timed_call(
        "summary_and_filings.search_filings",
        _search_dividend_filings(selected["corp_code"], year_list[0], target_year),
    )
    stage_started_at = time.perf_counter()
    (latest_summary, summary_warning), (filings, record_notices_search, filing_notices, filing_warning) = await asyncio.gather(
        latest_summary_task, filings_task,
    )
    _mark("summary_and_filings", stage_started_at)
    if summary_warning:
        warnings.append(summary_warning)
    warnings.extend(filing_notices)
    if filing_warning:
        warnings.append(filing_warning)
        filings = []
    # pre_dividend 통합: 메인 검색에서 같이 필터한 명부폐쇄 notice를 재사용 (별도 검색 제거).
    # 신호 윈도우는 기존과 동일(target_year ~ +1년 4월)로 좁혀 동작 보존. 기준일 본문 파싱은
    # latest_year_classification 블록에서 _record_date_from_notices가 연도매칭으로 수행.
    if scope in {"summary", "history"}:
        _pd_bgn, _pd_end = f"{target_year}0101", f"{target_year + 1}0430"
        record_date_notices = [
            n for n in record_notices_search
            if _pd_bgn <= "".join(c for c in (n.get("rcept_dt") or "") if c.isdigit())[:8] <= _pd_end
        ]
        pre_dividend_post_resolution = bool(record_date_notices)
    # 결정 공시만 정밀 타겟 (cap 방식이 아니라 "해당 기간의 해당 공시만"):
    #  - 기간(bgn_de/end_de)·공시유형(I001)은 검색 단계에서 서버가 이미 좁힘
    #  - "배당결정" 제목만 (주주명부폐쇄/배당락 등 비결정 공시 제외)
    #  - "자회사의 주요경영사항"(지주사 산하 자회사가 모회사에 주는 배당) 제외
    #    — 지주사 DPS 과대계상의 주원인.
    # 이렇게 거른 집합 자체가 "그 기간 모회사 배당결정 공시 전부"라 개수가 자연히
    # 작다(분기배당사 ~연 4건). 임의 cap 없이 타겟된 공시만 파싱한다 — 구버전의
    # raw [:20] 절단처럼 과거 연도가 통째로 누락되는 일이 없다.
    decision_filings = [
        f for f in filings
        if "배당결정" in (f.get("report_nm") or "")
        and "자회사" not in (f.get("report_nm") or "")
    ]
    stage_started_at = time.perf_counter()
    details = await _decision_details(decision_filings) if decision_filings else []
    _mark("decision_details", stage_started_at)

    # alotMatter가 비어있거나 cash_dps=0이면 해당 연도 배당결정 공시 합산을 source of truth로 대체.
    # 결산월은 사업보고서 alotMatter의 stlm_dt가 권위. 못 읽으면 None으로 두고
    # 사업연도 구간을 아예 표시하지 않는다(12월 결산으로 단정하지 않는다).
    fiscal_end_month = _fiscal_end_month((latest_summary or {}).get("stlm_dt"))
    # 창(window)으로 잘라내기 전의 결정공시 — 「그 사업연도에 결의가 있었나」 판정용.
    details_all = list(details)
    if (not latest_summary or int(latest_summary.get("cash_dps") or 0) == 0) and details:
        fallback = _decisions_summary_for_year(details, target_year, fiscal_end_month)
        if fallback and fallback.get("cash_dps", 0) > 0:
            latest_summary = fallback
            warnings.append(f"{target_year}년 사업보고서 배당 요약이 비어 있어 해당 연도 배당결정 공시 {fallback.get('decision_count', 0)}건을 합산해 요약을 구성했습니다.")
    start_ymd = format_yyyymmdd(window_start)
    end_ymd = format_yyyymmdd(window_end)
    details = [
        item for item in details
        if _in_window(item.get("rcept_dt", ""), start_ymd, end_ymd)
    ]

    # 현재가 기준 배당수익률 (valuation 로직 이식, 260705) — yield_dart(결의 당시 시가배당률)와
    # 별개로 '지금 사면 몇 %인가'를 답한다. 시세 = krx_weekly(Supabase, serve-time KRX 0콜),
    # 기준일(price_date) 명시. 지역 import — valuation이 이 모듈(_annual_summary)을 import하므로
    # 모듈 top import는 순환.
    if latest_summary and int(latest_summary.get("cash_dps") or 0) > 0:
        _isu = (resolution.selected or {}).get("stock_code")
        if _isu:
            from open_proxy_mcp.services.price_multiple_data import _market_for
            _mk = await _market_for(_isu)
            if _mk.get("price"):
                latest_summary["yield_current_pct"] = round(
                    latest_summary["cash_dps"] / _mk["price"] * 100, 2)
                latest_summary["yield_current_price_krw"] = _mk["price"]
                latest_summary["yield_current_price_date"] = _mk["date"]

    # 비-target 연도는 multiyear 컬럼 또는 결정공시 합산 fallback으로 채운다 (개별 호출 없음).
    annual_summaries: dict[int, dict[str, Any]] = {}
    year_to_result: dict[int, tuple[dict[str, Any], str | None]] = {target_year: (latest_summary, None)}

    # 권위 소스: 최신 사업보고서 alotMatter 의 다년 컬럼(당기/전기/전전기).
    # 연도별 개별 호출/결정 합산보다 우선 적용한다.
    alot_multi = _alot_multiyear_summaries(latest_summary)

    for y in year_list:
        summary, warning = year_to_result.get(y, (None, None))
        if warning:
            warnings.append(f"{y}년 {warning}")
        if y in alot_multi:
            summary = alot_multi[y]
        elif (not summary or int(summary.get("cash_dps") or 0) == 0):
            fallback = _decisions_summary_for_year(details, y, fiscal_end_month)
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

    # 결산은 끝났는데 배당 결의가 아직 없는 사업연도를 「미결의」로 세운다.
    # 비12월 결산 회사는 target_year(=작년) 뒤에 이미 끝난 사업연도가 하나 더 있는데,
    # 예전엔 그 해가 화면에서 통째로 빠져 직전 연도 배당이 최신인 것처럼 읽혔다
    # (포시에스: 회사 공시는 「FY2025 결산 배당 아직 없음」인데 도구는 FY2025 지급 완료로 표시).
    undecided_fiscal_years: list[dict[str, Any]] = []
    latest_completed_fy = _latest_completed_fiscal_year(date.today(), fiscal_end_month)
    newest_reported_fy = max(annual_summaries) if annual_summaries else target_year
    if latest_completed_fy is not None:
        decided_fys = {_bucket_fiscal_year(d) for d in details_all}
        for fy in range(newest_reported_fy + 1, latest_completed_fy + 1):
            if fy in decided_fys or fy in annual_summaries:
                continue
            fiscal = _fiscal_period_fields(fy, fiscal_end_month)
            undecided_fiscal_years.append(fiscal)
            history.append({
                **fiscal,
                "year": fy,
                "annual_dps": 0,
                "decision_count": 0,
                "payout_ratio": None,
                "yield_pct": None,
                "has_special": False,
                "pattern": "미결의 (결산 종료 · 배당 결의 공시·사업보고서 모두 미확인)",
                "pending_confirmation": True,
            })
            warnings.append(
                f"FY{fy}({fiscal['period_start']}~{fiscal['period_end']})은 결산이 끝났으나 "
                "배당 결의 공시도 사업보고서 배당 항목도 확인되지 않는다 — 미결의. "
                "아래 수치는 그 이전 사업연도의 것이다."
            )

    if meta_task is not None:
        await meta_task

    # 선배당-후결의(2024 신법) 신호가 있으면, 최신 사업연도가 결정공시·alotMatter 모두
    # 비어 "무배당"으로 보이는 것을 "확정 전"으로 바로잡는다 (예: 메리츠금융지주 —
    # 배당기준일만 설정하고 금액은 주총/사업보고서로 확정). 진짜 무배당(신규상장 등,
    # 기준일 공시 자체가 없음)은 신호가 False라 그대로 "무배당".
    _latest_class_started = time.perf_counter()
    if scope == "history" and pre_dividend_post_resolution:
        for row in history:
            if row["year"] == target_year and row["pattern"] == "무배당" and not row.get("annual_dps"):
                # 출처 맵 B: 사업보고서가 미확정이어도 분기/반기 alotMatter에 중간배당
                # 누적값이 있으면 "확정 전(금액 미정)" 대신 "중간배당 확정 (N분기까지)"로 보강.
                # '확정 전'은 **target연도 증거가 있을 때만** 선언한다 (false-positive 방지):
                #   ① 분기/반기 alotMatter에 중간배당 누적이 있거나(출처 B),
                #   ② target연도 결산 배당기준일이 설정됐거나(출처 D, 연도매칭).
                # 둘 다 없으면 pre_dividend 신호가 전년 notice에 발동한 오발동이므로 '무배당' 유지.
                interim = await _interim_dividend_from_quarterly(selected["corp_code"], target_year)
                if interim:
                    row["pattern"] = f"중간배당 확정 ({interim['period']}) · 결산 미정"
                    row["interim_dps"] = interim["interim_dps"]
                    row["interim_payout_ratio"] = interim["interim_payout_ratio"]
                    row["interim_period"] = interim["period"]
                    row["interim_source"] = f"분기보고서 alotMatter ({interim['reprt_code']})"
                    row["pending_confirmation"] = True
                    warnings.append(
                        f"{target_year} 사업연도는 {interim['period']} 중간배당 "
                        f"{interim['interim_dps']:,}원이 확정됐고 결산배당은 아직 미정이다 (선배당-후결의)."
                    )
                else:
                    rd = await _record_date_from_notices(record_date_notices, target_year)
                    if rd:
                        row["pattern"] = f"확정 전 (배당기준일 {rd} 설정 · 금액 미정)"
                        row["record_date"] = rd
                        row["pending_confirmation"] = True
                        warnings.append(
                            f"{target_year} 사업연도는 배당기준일({rd})만 설정되고 금액이 아직 확정되지 않았다 "
                            "(선배당-후결의). 무배당이 아니라 확정 전 상태다."
                        )
                    else:
                        # target연도 결산 기준일·중간배당 모두 없음. 직전 연도 배당 이력으로
                        # '무배당'(진짜 0) vs '미공시'(payer인데 결산 미확정) 구분.
                        prior_paid = any(
                            (r.get("annual_dps") or 0) > 0
                            for r in history if r["year"] < target_year
                        )
                        if prior_paid:
                            # 시간축 판정: 해당 사업연도 정기주총(12월결산 ≈ 익년 3월)이 지났으면
                            # '미확정'은 stale이다. 주총에서 배당이 결정됐는데도 결정공시·기준일이
                            # 전혀 없다는 것은 결산 현금배당을 하지 않기로 확정한 것으로 본다
                            # (예: FY2025 배당재원을 전액 자사주 소각으로 전환한 메리츠금융지주 →
                            # 종전엔 '미확정'으로 고착됐음). 익년 5월말 이후를 경과 기준으로(버퍼).
                            agm_passed = date.today() > date(target_year + 1, 5, 31)
                            if agm_passed:
                                row["pattern"] = "무배당 (확정 · 결산 현금배당 없음)"
                                warnings.append(
                                    f"{target_year} 사업연도 정기주총이 지났고 결산 현금배당 결정·기준일이 "
                                    "확인되지 않는다 — 결산 현금배당을 하지 않기로 확정된 것으로 본다(배당재원을 "
                                    "자사주 소각 등으로 전환했을 수 있다). 미확정이 아니다."
                                )
                            else:
                                row["pattern"] = "미공시 (결산 배당 미확정)"
                                row["pending_confirmation"] = True
                                warnings.append(
                                    f"{target_year} 사업연도 결산배당이 아직 공시되지 않았다 — 직전 배당 이력이 있는 "
                                    "회사라 무배당으로 단정하지 않는다 (선배당-후결의로 금액이 사업보고서에 미반영되었거나 결정 전)."
                                )
                        # else: 직전도 배당 이력 없음 → '무배당' 유지 (진짜 무배당)
    _mark("latest_year_classification", _latest_class_started)

    # 추세는 확정된 연도만으로 계산 (미확정 최신 연도의 DPS=0 이 -100% 로 왜곡 방지).
    policy = _policy_signals([r for r in history if not r.get("pending_confirmation")])

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
        "fiscal_year_basis": FISCAL_YEAR_BASIS,
        "fiscal_year_end_month": fiscal_end_month,
        "undecided_fiscal_years": undecided_fiscal_years,
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
    # latest_decisions 노출용 — 정정/재공시 중복 제거 (연도·분기·기준일 최신 1건) + 최신순.
    # 연간 집계와 동일한 _effective_decisions 키를 써서 표시/합산 일관성 유지.
    effective_details = sorted(
        _effective_decisions(details),
        key=lambda d: (d.get("rcept_dt", ""), d.get("rcept_no", "")),
        reverse=True,
    )
    # 감사 #3: per-decision 시가배당률은 개별 배당결정 공시에 자주 0/미기재라 오해를 준다.
    # 0/falsy는 None으로 억제한다 (연간 시가배당률=alotMatter yield_dart가 권위). 유효 비0값은 유지.
    for _d in effective_details:
        if not _d.get("yield_common"):
            _d["yield_common"] = None
        if not _d.get("yield_preferred"):
            _d["yield_preferred"] = None
    if scope in {"summary", "detail"}:
        # 분기배당 회사 (삼성전자 등) 3년치 = 최대 12 quarters + 결산 → 20건 노출.
        data["latest_decisions"] = effective_details[:20]
    if scope == "history":
        _qb_started = time.perf_counter()
        data["history"] = history
        # quarterly_breakdown: details에서 연도/분기별 grouping (분기배당 회사 분기별 검증용)
        quarterly_breakdown = _quarterly_breakdown(details, history_years or year_list)
        data["quarterly_breakdown"] = quarterly_breakdown
        # policy_signals: history scope에 통합 (별도 scope 폐지)
        data["policy_signals"] = policy
        # 정합성 경고: 분기 breakdown 합(정정 제외) ≠ 사업보고서 연간 DPS.
        # 깜깜이배당 해소 전환기엔 전년 결산과 올해 Q1이 같은 봄에 공시되고 결산 기준일이
        # 다음 해로 밀려, 공시별 fiscal-year 추론이 경계에서 어긋날 수 있다. 이때 연간값
        # (사업보고서)이 정확하고 분기 표가 ±. 헤드라인을 못 믿게 만들지 않도록 명시한다.
        qb_year_sum: dict[int, int] = {}
        for r in quarterly_breakdown:
            if not r.get("is_superseded"):
                qb_year_sum[r["year"]] = qb_year_sum.get(r["year"], 0) + r["dps_common_krw"]
        for row in history:
            if row.get("pending_confirmation"):
                continue
            annual = int(row.get("annual_dps") or 0)
            qsum = qb_year_sum.get(row["year"])
            if annual > 0 and qsum and qsum != annual:
                # #2: 불일치 연도만 정기보고서 누적 차분으로 권위 분기값을 교정 (출처 B).
                # 결정공시 fiscal-year 추론이 전환기 경계에서 어긋난 케이스를 바로잡는다.
                auth = await _quarterly_dps_from_cumulative(selected["corp_code"], row["year"])
                auth_sum = sum(q["dps_common_krw"] for q in auth)
                if auth and auth_sum == annual:
                    row["quarterly_dps_authoritative"] = auth
                    warnings.append(
                        f"{row['year']} 분기 breakdown을 정기보고서 누적 차분으로 교정했다 "
                        f"(결정공시 귀속 합 {qsum:,}원 → 권위 분기합 {auth_sum:,}원 = 연간 {annual:,}원). "
                        "row.quarterly_dps_authoritative 참조."
                    )
                else:
                    warnings.append(
                        f"⚠ {row['year']} 분기 breakdown 합({qsum:,}원)이 사업보고서 연간 DPS"
                        f"({annual:,}원)와 다르다 — 전환기 결산/분기 기준일 경계 귀속 이슈로 보인다. "
                        "연간값(사업보고서)이 정확하며 분기 표는 참고용."
                    )
        # 최신연도(target_year)는 결정공시 버킷팅이 어긋나기 쉬움(중복 결산·예비결산 등).
        # 정기보고서 누적차분으로 보통/우선 DPS + 배당총액을 권위 산출 (정확도 우선, 무배당 분기 0 포함).
        target_paid = any(
            r.get("year") == target_year and (r.get("annual_dps") or 0) > 0 for r in history
        )
        if target_paid:
            data["quarterly_full"] = await _quarterly_full_from_cumulative(
                selected["corp_code"], target_year
            )
        _mark("quarterly_breakdown", _qb_started)
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
            "latest_decisions": effective_details[:50],
            "decision_count": len(effective_details),
            "raw_decision_count": len(details),
        }
        # alotMatter (사업보고서) vs filings 합산 mismatch warning
        if latest_summary and latest_summary.get("source") == "alotMatter":
            alot_dps = int(latest_summary.get("cash_dps") or 0)
            # 해당 사업연도 bucket 결정 공시 합산 (정정/재공시 dedup 후 — 자회사는 이미 제외)
            year_eff = _effective_decisions([d for d in details if _bucket_fiscal_year(d) == target_year])
            decisions_dps = sum(int(d.get("dps_common") or 0) for d in year_eff)
            if alot_dps and decisions_dps and abs(alot_dps - decisions_dps) > max(1, alot_dps * 0.05):
                warnings.append(f"⚠ {target_year}년 사업보고서 alotMatter DPS({alot_dps:,}원)와 배당결정 공시 합산 DPS({decisions_dps:,}원) 불일치 — 정정 또는 신규 결정 누락 가능성, latest_decisions 원문 검토 권장.")

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
            "detail scope로 최근 배당 결정 공시 확인",
            "treasury_share로 자사주 매입·소각까지 포함한 주주환원 확인",
        ]
    else:
        next_actions = ["ownership_structure와 함께 보면 주주환원 맥락이 더 잘 보인다."]

    return ToolEnvelope(
        tool="dividend_disclosure",
        status=status,
        subject=selected.get("corp_name", company_query),
        warnings=warnings,
        data=data,
        evidence_refs=evidence_refs,
        next_actions=next_actions,
    ).to_dict()
