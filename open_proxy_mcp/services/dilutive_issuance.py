"""dilutive_issuance data tool.

희석성 증권 발행 4종(유상증자/전환사채/신주인수권부사채/감자) 결정을 통합 제공.
행동주의 / 경영권 방어 / 우호지분 형성 분석의 핵심 소스.
"""

from __future__ import annotations

import asyncio
from datetime import date
import re
from typing import Any

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.services.company import _company_id, resolve_company_query
from open_proxy_mcp.services.filing_search import search_filings_by_report_name
from open_proxy_mcp.services.contracts import (
    AnalysisStatus,
    EvidenceRef,
    SourceType,
    ToolEnvelope,
    build_filing_meta,
    status_from_filing_meta,
)
from open_proxy_mcp.services.date_utils import format_iso_date, format_yyyymmdd, resolve_date_window


_SUPPORTED_SCOPES = {
    "summary",
    "rights_offering",
    "convertible_bond",
    "warrant_bond",
    "exchangeable_bond",
    "capital_reduction",
}

# 교환사채(EB) 원본 문서 검색 키워드 (주요사항보고서 B / 상세 B001)
_EB_REPORT_KEYWORDS = ("교환사채권발행결정",)


_DATE_KEY_RE = re.compile(r"_date$")
_KO_DATE_RE = re.compile(r"^(\d{4})\s*[년.\-/]\s*(\d{1,2})\s*[월.\-/]\s*(\d{1,2})\s*일?$")


def _normalize_row_dates(row: dict) -> None:
    """*_date 필드의 '2026년 02월 11일'/'2026.02.11'/'20260211' → ISO. 그 외 형식은 보존."""
    for k, v in row.items():
        if isinstance(v, dict):
            _normalize_row_dates(v)
            continue
        if not isinstance(v, str) or not v or not _DATE_KEY_RE.search(k):
            continue
        s = v.strip()
        m = _KO_DATE_RE.match(s)
        if m:
            row[k] = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        elif re.fullmatch(r"\d{8}", s):
            row[k] = f"{s[:4]}-{s[4:6]}-{s[6:8]}"


def _clean(value: Any) -> str:
    text = str(value or "").strip()
    if text in ("-", "해당사항없음", "해당사항 없음"):
        return ""
    return text


def _truncate(value: Any, limit: int = 200) -> str:
    text = _clean(value)
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _to_int(value: Any) -> int:
    """문자열 → 정수 (괄호 음수 처리 포함, 한국 회계 관행 대응).

    예: "(500)" → -500, "1,000" → 1000, "-500" → -500.
    DART API는 음수를 일반적으로 -500 형식으로 반환하지만, OCR/HTML
    fallback 경로에서 괄호 음수가 들어올 수 있어 일관 처리한다.
    """
    text = str(value or "0").strip()
    if not text:
        return 0
    # 괄호 음수: (500) → -500
    is_negative = text.startswith("(") and text.endswith(")")
    if is_negative:
        text = text[1:-1]
    try:
        digits = re.sub(r"[^\d-]", "", text) or "0"
        result = int(digits)
        return -result if is_negative else result
    except ValueError:
        return 0


def _to_float(value: Any) -> float:
    """문자열 → 실수 (괄호 음수 처리 포함)."""
    text = str(value or "0").strip()
    if not text:
        return 0.0
    is_negative = text.startswith("(") and text.endswith(")")
    if is_negative:
        text = text[1:-1]
    try:
        digits = re.sub(r"[^\d.-]", "", text) or "0"
        result = float(digits)
        return -result if is_negative else result
    except ValueError:
        return 0.0


def _pct_of_existing(new_shares: int, existing_shares: int) -> float:
    """기존 발행주식 대비 신주 비율 (희석률 근사)."""
    if existing_shares <= 0 or new_shares <= 0:
        return 0.0
    return round(new_shares / existing_shares * 100, 2)


def _fdpp_breakdown(item: dict[str, Any]) -> dict[str, str]:
    """자금조달 목적 필드 정리."""
    return {
        "facility": _clean(item.get("fdpp_fclt", "")),           # 시설자금
        "business_acquisition": _clean(item.get("fdpp_bsninh", "")),  # 타법인 증권 취득
        "operating": _clean(item.get("fdpp_op", "")),            # 운영자금
        "debt_repayment": _clean(item.get("fdpp_dtrp", "")),     # 채무상환
        "other_corp_share_acq": _clean(item.get("fdpp_ocsa", "")),  # 기타법인 주식 취득
        "etc": _clean(item.get("fdpp_etc", "")),                 # 기타
    }


def _normalize_rights_offering(item: dict[str, Any]) -> dict[str, Any]:
    existing = _to_int(item.get("bfic_tisstk_ostk", "0"))
    new_common = _to_int(item.get("nstk_ostk_cnt", "0"))
    return {
        "type": "rights_offering",
        "event_label": "유상증자결정",
        "rcept_no": item.get("rcept_no", ""),
        "rcept_dt": item.get("rcept_dt", ""),
        "board_decision_date": _clean(item.get("bddd", "")),
        "issuance_method": _clean(item.get("ic_mthn", "")),  # 제3자배정/주주배정/일반공모
        "face_value_per_share": _clean(item.get("fv_ps", "")),
        "new_shares_common": new_common,
        "new_shares_preferred": _to_int(item.get("nstk_estk_cnt", "0")),
        "existing_shares_common": existing,
        "dilution_pct_approx": _pct_of_existing(new_common, existing),
        "fund_purpose": _fdpp_breakdown(item),
        "lock_up": {
            "applicable": _clean(item.get("ssl_at", "")),
            "begin_date": _clean(item.get("ssl_bgd", "")),
            "end_date": _clean(item.get("ssl_edd", "")),
        },
    }


def _normalize_convertible_bond(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "convertible_bond",
        "event_label": "전환사채발행결정",
        "rcept_no": item.get("rcept_no", ""),
        "rcept_dt": item.get("rcept_dt", ""),
        "board_decision_date": _clean(item.get("bddd", "")),
        "bond_series": _clean(item.get("bd_tm", "")),  # 회차
        "bond_kind": _truncate(item.get("bd_knd", ""), 100),
        "total_issue_amount": _clean(item.get("bd_fta", "")),
        "issuance_method": _clean(item.get("bdis_mthn", "")),  # 사모/공모
        "coupon_rate": _clean(item.get("bd_intr_ex", "")),  # 표면금리
        "yield_to_maturity": _clean(item.get("bd_intr_sf", "")),  # YTM
        "maturity_date": _clean(item.get("bd_mtd", "")),
        "conversion": {
            "rate": _clean(item.get("cv_rt", "")),
            "price": _clean(item.get("cv_prc", "")),
            "target_stock_kind": _clean(item.get("cvisstk_knd", "")),
            "shares_if_converted": _clean(item.get("cvisstk_cnt", "")),
            "pct_of_total_shares": _clean(item.get("cvisstk_tisstk_vs", "")),  # 잠재 희석률 %
            "request_period_begin": _clean(item.get("cvrqpd_bgd", "")),
            "request_period_end": _clean(item.get("cvrqpd_edd", "")),
            "refixing_floor": _clean(item.get("act_mktprcfl_cvprc_lwtrsprc", "")),
            "refixing_basis": _truncate(item.get("act_mktprcfl_cvprc_lwtrsprc_bs", ""), 200),
        },
        "fund_purpose": _fdpp_breakdown(item),
        "payment_date": _clean(item.get("pymd", "")),
        "guarantor": _clean(item.get("rpmcmp", "")),
        "collateral": _clean(item.get("grint", "")),
        "remaining_issue_limit": _clean(item.get("atcsc_rmislmt", "")),
        "limit_under_70pct": _clean(item.get("rmislmt_lt70p", "")),
        "securities_report_required": _clean(item.get("rs_sm_atn", "")),
        "overseas_issue": _truncate(item.get("ovis_ltdtl", ""), 100),
    }


def _normalize_exchangeable_bond(item: dict[str, Any]) -> dict[str, Any]:
    """교환사채(EB) 발행결정. CB와 유사하나 '전환→신주'가 아니라 '교환→기존주식(주로 자기주식)'.

    신주 발행이 아니라 형식적 희석은 없으나, 교환대상이 자기주식이면 교환권 행사 시
    의결권 없던 자사주가 제3자로 이전돼 **의결권 희석** 효과가 발생한다.
    """
    return {
        "type": "exchangeable_bond",
        "event_label": "교환사채발행결정",
        "rcept_no": item.get("rcept_no", ""),
        "rcept_dt": item.get("rcept_dt", ""),
        "board_decision_date": _clean(item.get("bddd", "")),
        "bond_series": _clean(item.get("bd_tm", "")),
        "bond_kind": _truncate(item.get("bd_knd", ""), 100),
        "total_issue_amount": _clean(item.get("bd_fta", "")),
        "issuance_method": _clean(item.get("bdis_mthn", "")),  # 사모/공모
        "coupon_rate": _clean(item.get("bd_intr_ex", "")),
        "yield_to_maturity": _clean(item.get("bd_intr_sf", "")),
        "maturity_date": _clean(item.get("bd_mtd", "")),
        "exchange": {
            "rate": _clean(item.get("ex_rt", "")),
            "price": _clean(item.get("ex_prc", "")),
            "price_method": _truncate(item.get("ex_prc_dmth", ""), 200),
            "target": _clean(item.get("extg", "")),  # 교환대상 (자기주식/타사주식)
            "target_share_count": _clean(item.get("extg_stkcnt", "")),
            "pct_of_total_shares": _clean(item.get("extg_tisstk_vs", "")),  # 발행총수 대비 % (의결권 희석)
            "request_period_begin": _clean(item.get("exrqpd_bgd", "")),
            "request_period_end": _clean(item.get("exrqpd_edd", "")),
        },
        "fund_purpose": _fdpp_breakdown(item),
        "payment_date": _clean(item.get("pymd", "")),
        "bond_issue_date": _clean(item.get("sbd", "")),
        "guarantor": _clean(item.get("rpmcmp", "")),
        "collateral": _clean(item.get("grint", "")),
        "securities_report_required": _clean(item.get("rs_sm_atn", "")),
        "underwriter": "",  # 구조화 미제공 — 원문 복원 시 채워짐
        # 원문 복원 메타 (구조화가 정정/철회로 비었을 때 채워짐)
        "recovered_from_document": False,
        "detection_only": False,  # 공시는 있으나 구조화·원문 모두 추출 불가 (누락 방지용 탐지 행)
        "source_rcept_no": "",
        "latest_status_rcept_no": "",
        "recovery_note": "",
    }


def _normalize_warrant_bond(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "warrant_bond",
        "event_label": "신주인수권부사채발행결정",
        "rcept_no": item.get("rcept_no", ""),
        "rcept_dt": item.get("rcept_dt", ""),
        "board_decision_date": _clean(item.get("bddd", "")),
        "bond_series": _clean(item.get("bd_tm", "")),
        "bond_kind": _truncate(item.get("bd_knd", ""), 100),
        "total_issue_amount": _clean(item.get("bd_fta", "")),
        "issuance_method": _clean(item.get("bdis_mthn", "")),
        "coupon_rate": _clean(item.get("bd_intr_ex", "")),
        "yield_to_maturity": _clean(item.get("bd_intr_sf", "")),
        "maturity_date": _clean(item.get("bd_mtd", "")),
        "warrant": {
            "exercise_rate": _clean(item.get("ex_rt", "")),
            "exercise_price": _clean(item.get("ex_prc", "")),
            "exercise_price_method": _truncate(item.get("ex_prc_dmth", ""), 200),
            "exercise_period_begin": _clean(item.get("expd_bgd", "")),
            "exercise_period_end": _clean(item.get("expd_edd", "")),
            "detachable": _clean(item.get("bdwt_div_atn", "")),  # 분리형/비분리형
            "new_stock_kind": _clean(item.get("nstk_isstk_knd", "")),
            "new_stock_count": _clean(item.get("nstk_isstk_cnt", "")),
            "pct_of_total_shares": _clean(item.get("nstk_isstk_tisstk_vs", "")),
            "payment_method": _clean(item.get("nstk_pym_mth", "")),  # 대용납입 등
            "refixing_floor": _clean(item.get("act_mktprcfl_cvprc_lwtrsprc", "")),
        },
        "fund_purpose": _fdpp_breakdown(item),
        "payment_date": _clean(item.get("pymd", "")),
        "guarantor": _clean(item.get("rpmcmp", "")),
        "collateral": _clean(item.get("grint", "")),
        "remaining_issue_limit": _clean(item.get("atcsc_rmislmt", "")),
        "securities_report_required": _clean(item.get("rs_sm_atn", "")),
    }


def _normalize_capital_reduction(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "capital_reduction",
        "event_label": "감자결정",
        "rcept_no": item.get("rcept_no", ""),
        "rcept_dt": item.get("rcept_dt", ""),
        "board_decision_date": _clean(item.get("bddd", "")),
        "reduction_ratio_common": _clean(item.get("cr_rt_ostk", "")),  # %
        "reduction_ratio_preferred": _clean(item.get("cr_rt_estk", "")),
        "reduction_standard_date": _clean(item.get("cr_std", "")),
        "method": _truncate(item.get("cr_mth", ""), 300),  # 주식병합 등
        "reason": _truncate(item.get("cr_rs", ""), 200),
        "face_value_per_share": _clean(item.get("fv_ps", "")),
        "shares_reduced_common": _clean(item.get("crstk_ostk_cnt", "")),
        "shares_reduced_preferred": _clean(item.get("crstk_estk_cnt", "")),
        "capital_before": _clean(item.get("bfcr_cpt", "")),
        "capital_after": _clean(item.get("atcr_cpt", "")),
        "outstanding_before_common": _clean(item.get("bfcr_tisstk_ostk", "")),
        "outstanding_after_common": _clean(item.get("atcr_tisstk_ostk", "")),
        "schedule": {
            "shareholders_meeting": _clean(item.get("crsc_gmtsck_prd", "")),
            "old_share_submission_begin": _clean(item.get("crsc_osprpd_bgd", "")),
            "old_share_submission_end": _clean(item.get("crsc_osprpd_edd", "")),
            "trading_suspension_begin": _clean(item.get("crsc_trspprpd_bgd", "")),
            "trading_suspension_end": _clean(item.get("crsc_trspprpd_edd", "")),
            "new_share_listing": _clean(item.get("crsc_nstklstprd", "")),
        },
    }


# ── EB 원문 복원 (구조화 응답이 정정/철회로 빈 경우) ──────────────────
#
# DART 주요사항보고서 주요정보 API는 정정·철회된 EB는 최신본(철회)만 반환하며
# 교환 조건이 비어 있다. 이때 list.json으로 원본 공시를 찾아 원문을 파싱해 복원한다.

_EB_DATE_LINE_RE = re.compile(r"\d{4}\s*년|\d{4}[.\-/]\d{1,2}")


def _eb_terms_blank(row: dict[str, Any]) -> bool:
    """EB 행이 핵심 조건(총액·교환가)이 모두 비어 있는 stub인지."""
    if row.get("type") != "exchangeable_bond":
        return False
    return not row.get("total_issue_amount") and not row.get("exchange", {}).get("price")


def _doc_value_after(lines: list[str], label: str, max_distance: int = 1, numeric_only: bool = False) -> str:
    """라벨 줄 뒤 N줄 이내의 값 추출 ('-'·라벨성 줄 skip). 원문 표는 라벨줄→값줄 구조."""
    for i, line in enumerate(lines):
        if label in line:
            for j in range(1, max_distance + 1):
                if i + j >= len(lines):
                    break
                v = lines[i + j].strip()
                if not v or v == "-" or v.endswith(":"):
                    continue
                if numeric_only and not re.search(r"\d", v):
                    continue
                if len(v) < 300:
                    return v
    return ""


_EB_FORMULA_JUNK = ("기발행주식수", "신발행주식수", "발행주식총수", "발행주식 총수", "산식", "조정 전", "조정 후")


def _looks_like_eb_target(s: str) -> bool:
    """교환대상 종목명 후보인지 (교환가액 조정 산식 변수줄 A:/B:/C: 등 배제)."""
    s = (s or "").strip()
    if not s or len(s) > 90:
        return False
    if re.match(r"^[A-Za-z]\s*[:：]", s):  # A: 기발행주식수
        return False
    if any(j in s for j in _EB_FORMULA_JUNK):
        return False
    return ("보통주" in s) or ("자기주식" in s) or ("KDR" in s) or ("기명식" in s) or ("우선주" in s)


def _parse_eb_document(text: str) -> dict[str, Any]:
    """교환사채권발행결정 주요사항보고서 원문(text) 파싱 → 핵심 조건 dict.

    라벨은 probe로 검증한 실제 원문 구조 기준 (라벨줄 → 값줄).
    """
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    series = _doc_value_after(lines, "회차", 1, numeric_only=True)
    # bond_kind: '종류' 단독 줄 뒤 값 (substring '1. 사채의 종류' 헤더 회피).
    # 첫 단독 '종류'=사채 종류, 두번째=교환대상 종류이므로 first match 사용.
    bond_kind = ""
    for i, l in enumerate(lines):
        if l == "종류" and i + 1 < len(lines):
            v = lines[i + 1].strip()
            if v and v != "-":
                bond_kind = v[:100]
                break
    board_decision = _doc_value_after(lines, "이사회결의일", 1)
    total = _doc_value_after(lines, "권면(전자등록)총액 (원)", 1, numeric_only=True)
    method = _doc_value_after(lines, "사채발행방법", 1)
    coupon = _doc_value_after(lines, "표면이자율 (%)", 1, numeric_only=True)
    ytm = _doc_value_after(lines, "만기이자율 (%)", 1, numeric_only=True)
    maturity = _doc_value_after(lines, "사채만기일", 1)
    ex_rate = _doc_value_after(lines, "교환비율 (%)", 1, numeric_only=True)
    ex_price = _doc_value_after(lines, "교환가액 (원/주)", 1, numeric_only=True)

    # 교환대상 종류 + 주식수 (자기주식 or 타사주식)
    # 주의: '교환가액 조정 산식'의 변수줄(A: 기발행주식수 등)을 교환대상으로 오인 금지.
    target = ""
    target_count = ""
    # (1) '교환대상' 표 라벨 앵커 → 종류 셀 + 주식수
    for i, line in enumerate(lines):
        if line == "교환대상" or (line.startswith("교환대상") and len(line) < 12):
            for j in range(i + 1, min(i + 6, len(lines))):
                if not target and _looks_like_eb_target(lines[j]):
                    target = lines[j]
                if "주식수" in lines[j]:
                    for k in range(j + 1, min(j + 3, len(lines))):
                        if re.fullmatch(r"[\d,]+", lines[k]):
                            target_count = lines[k]
                            break
            if target:
                break
    # (2) narrative 폴백: "교환대상 주식 : 발행회사가 보유한 …보통주식"
    if not target:
        m = re.search(r"교환대상\s*주식\s*[:：]?\s*([^\n]+?(?:보통주식?|KDR|우선주))", text)
        if m and _looks_like_eb_target(m.group(1).strip()):
            target = m.group(1).strip()[:90]
    # (3) 그래도 없으면: 자기주식 마커로 최소 신호 복원 (정확 종목명 미상)
    if not target and any(k in text for k in ("자기주식 대상", "기 보유한 자기주식", "자기주식을 활용", "보유 자기주식")):
        target = "발행회사 자기주식(종목명 원문 확인)"

    # 교환청구기간 시작일 (라벨 뒤 첫 날짜)
    request_begin = ""
    for i, line in enumerate(lines):
        if "교환청구기간" in line:
            for j in range(i + 1, min(i + 5, len(lines))):
                if _EB_DATE_LINE_RE.search(lines[j]):
                    request_begin = lines[j]
                    break
            break

    # 인수자 (best-effort) — 인수인은 보통 '○○증권 주식회사' 형태로 기재
    uw = re.search(r"([가-힣A-Za-z()]{2,}(?:투자증권|증권|은행|캐피탈|자산운용))\s*주식회사", text)
    underwriter = uw.group(1) if uw else ""

    return {
        "bond_series": series,
        "bond_kind": bond_kind,
        "board_decision_date": board_decision,
        "total_issue_amount": total,
        "issuance_method": method,
        "coupon_rate": coupon,
        "ytm": ytm,
        "maturity_date": maturity,
        "exchange_rate": ex_rate,
        "exchange_price": ex_price,
        "target": target,
        "target_share_count": target_count,
        "request_period_begin": request_begin,
        "underwriter": underwriter,
    }


def _merge_eb_doc_into_row(
    row: dict[str, Any],
    parsed: dict[str, Any],
    source: dict[str, Any],
    latest: dict[str, Any],
    chain_len: int,
) -> None:
    """원문 파싱 결과를 blank EB 행에 병합 + 복원 메타 기록."""
    row["recovered_from_document"] = True
    row["source_rcept_no"] = source.get("rcept_no", "")
    row["latest_status_rcept_no"] = latest.get("rcept_no", "")
    # 대표 rcept를 조건이 든 원본으로 교체 → evidence 링크가 실 조건 문서를 가리킨다.
    row["rcept_no"] = source.get("rcept_no", "") or row.get("rcept_no", "")
    row["rcept_dt"] = source.get("rcept_dt", "") or row.get("rcept_dt", "")

    if parsed.get("bond_series"):
        row["bond_series"] = parsed["bond_series"]
    if parsed.get("bond_kind"):
        row["bond_kind"] = parsed["bond_kind"]
    if parsed.get("board_decision_date"):
        # 구조화 stub의 bddd(철회 결의일)를 원본 이사회결의일로 교체
        row["board_decision_date"] = parsed["board_decision_date"]
    if parsed.get("total_issue_amount"):
        row["total_issue_amount"] = parsed["total_issue_amount"]
    if parsed.get("issuance_method"):
        row["issuance_method"] = parsed["issuance_method"]
    if parsed.get("coupon_rate"):
        row["coupon_rate"] = parsed["coupon_rate"]
    if parsed.get("ytm"):
        row["yield_to_maturity"] = parsed["ytm"]
    if parsed.get("maturity_date"):
        row["maturity_date"] = parsed["maturity_date"]
    ex = row["exchange"]
    if parsed.get("exchange_rate"):
        ex["rate"] = parsed["exchange_rate"]
    if parsed.get("exchange_price"):
        ex["price"] = parsed["exchange_price"]
    if parsed.get("target"):
        ex["target"] = parsed["target"]
    if parsed.get("target_share_count"):
        ex["target_share_count"] = parsed["target_share_count"]
    if parsed.get("request_period_begin"):
        ex["request_period_begin"] = parsed["request_period_begin"]
    if parsed.get("underwriter"):
        row["underwriter"] = parsed["underwriter"]
    _normalize_row_dates(row)

    row["recovery_note"] = (
        f"DART 구조화 응답이 정정/철회로 비어 원본 문서(rcept {source.get('rcept_no', '')}, "
        f"{source.get('rcept_dt', '')})에서 복원. 교환사채 공시 체인 {chain_len}건, "
        f"최신 {latest.get('rcept_dt', '')}(rcept {latest.get('rcept_no', '')}) — "
        f"발행조건 변경·철회 가능성 있어 원문 확인 권장."
    )


def _dedup_eb_rows(eb_list: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """같은 EB(회차+총액)가 구조화 complete + 정정 stub 복원으로 2행 되는 것 제거.

    그룹 내 우선순위: 구조화 complete > 원문복원 > 탐지전용. (series·total 둘 다 빈 행은 고유 취급)
    """
    def rank(x: dict[str, Any]) -> int:
        if x.get("detection_only"):
            return 0
        if x.get("recovered_from_document"):
            return 1
        return 2

    groups: dict[Any, dict[str, Any]] = {}
    order: list[Any] = []
    for r in eb_list:
        series = r.get("bond_series", "")
        total = r.get("total_issue_amount", "")
        key = (series, total) if (series or total) else ("__uniq__", id(r))
        if key not in groups:
            groups[key] = r
            order.append(key)
        elif rank(r) > rank(groups[key]):
            groups[key] = r
    return [groups[k] for k in order]


async def _find_eb_terms_from_filings(
    filings_sorted: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, int, list[str]]:
    """원본(가장 오래된)부터 최대 4건 문서 파싱, 조건이 든 첫 결과 반환.

    Returns (parsed, source_filing, doc_calls, warnings).
    """
    client = get_dart_client()
    warnings: list[str] = []
    doc_calls = 0
    for f in filings_sorted[:4]:
        rcept_no = f.get("rcept_no", "")
        if not rcept_no:
            continue
        try:
            doc = await client.get_document_cached(rcept_no)
            doc_calls += 1
        except Exception as exc:  # noqa: BLE001 — 문서 실패는 graceful degrade
            warnings.append(f"EB 원문 조회 실패 ({rcept_no}): {exc}")
            continue
        text = doc.get("text", "") if isinstance(doc, dict) else ""
        cand = _parse_eb_document(text)
        if cand.get("total_issue_amount") or cand.get("exchange_price"):
            return cand, f, doc_calls, warnings
    return None, None, doc_calls, warnings


async def _ensure_eb_coverage(
    eb_rows: list[dict[str, Any]],
    corp_code: str,
    bgn_de: str,
    end_de: str,
) -> tuple[list[str], int, list[dict[str, Any]]]:
    """EB 누락/공란 보정.

    구조화 `exbdIsDecsn`은 (1) 정정/철회 시 stub만 주거나(태광형 — blank row),
    (2) 첨부정정만 있는 체인은 013으로 아예 0건을 주기도 한다(한라IMS형 — 누락).
    두 경우 모두 list.json으로 EB 공시를 찾아 원본 문서를 파싱해 복원한다.
    구조화가 EB를 완전히 제공한 경우엔 추가 호출 없이 즉시 반환.

    Returns (warnings, extra_api_calls, new_rows).
    """
    warnings: list[str] = []
    api_calls = 0
    new_rows: list[dict[str, Any]] = []

    blanks = [r for r in eb_rows if _eb_terms_blank(r)]
    if eb_rows and not blanks:
        return warnings, api_calls, new_rows  # 구조화가 EB 완전 제공 — list.json 생략

    try:
        filings, notices, error = await search_filings_by_report_name(
            corp_code=corp_code,
            bgn_de=bgn_de,
            end_de=end_de,
            pblntf_tys="B",
            pblntf_detail_ty="B001",
            keywords=_EB_REPORT_KEYWORDS,
            strip_spaces=True,
            max_pages=3,
        )
        api_calls += 1
    except DartClientError as exc:
        warnings.append(f"EB 원문 검색 실패: {exc.status}")
        return warnings, api_calls, new_rows
    warnings.extend(notices)
    if error:
        warnings.append(f"EB 원문 검색 실패: {error}")
        return warnings, api_calls, new_rows
    if not filings:
        return warnings, api_calls, new_rows  # 진짜 EB 없음 (정상)

    # 오래된 순(원본 먼저) — 원본 공시가 발행조건이 가장 완전하다.
    filings_sorted = sorted(filings, key=lambda x: (x.get("rcept_dt", ""), x.get("rcept_no", "")))
    latest = filings_sorted[-1]
    parsed, source, doc_calls, doc_warnings = await _find_eb_terms_from_filings(filings_sorted)
    api_calls += doc_calls
    warnings.extend(doc_warnings)

    if not parsed:
        # 원문 추출 실패(문서 미제공 014/파싱 실패). EB 존재 자체는 surface해 누락 방지.
        note = (
            f"EB 공시 {len(filings_sorted)}건 발견(최신 {latest.get('rcept_dt', '')}, "
            f"rcept {latest.get('rcept_no', '')})되었으나 구조화 응답 + 원문 모두 추출 불가"
            f"(첨부정정 등으로 document.xml 미제공) — DART 원문 직접 확인 필요."
        )
        warnings.append(note)
        if blanks:
            blanks[0]["detection_only"] = True
            blanks[0]["latest_status_rcept_no"] = latest.get("rcept_no", "")
            blanks[0]["recovery_note"] = note
        else:
            row = _normalize_exchangeable_bond({})
            row["rcept_no"] = latest.get("rcept_no", "")
            row["rcept_dt"] = latest.get("rcept_dt", "")
            row["detection_only"] = True
            row["latest_status_rcept_no"] = latest.get("rcept_no", "")
            row["recovery_note"] = note
            _normalize_row_dates(row)
            new_rows.append(row)
        return warnings, api_calls, new_rows

    if blanks:
        # 태광형: 구조화 stub 행에 병합
        _merge_eb_doc_into_row(blanks[0], parsed, source, latest, len(filings_sorted))
    else:
        # 한라IMS형: 구조화 0건 → 새 EB 행 생성
        row = _normalize_exchangeable_bond({})
        _merge_eb_doc_into_row(row, parsed, source, latest, len(filings_sorted))
        new_rows.append(row)
    return warnings, api_calls, new_rows


async def _fetch_scope(
    scope: str,
    corp_code: str,
    bgn_de: str,
    end_de: str,
) -> tuple[list[dict[str, Any]], list[str], int]:
    """scope별 병렬 fetch. Returns (rows, warnings, api_call_count)."""
    client = get_dart_client()
    warnings: list[str] = []
    api_calls = 0

    async def fetch_endpoint(method, normalizer, label: str):
        nonlocal api_calls
        try:
            result = await method(corp_code, bgn_de, end_de)
            api_calls += 1
            return [normalizer(item) for item in result.get("list", [])]
        except DartClientError as exc:
            if exc.status == "013":
                api_calls += 1
                return []
            warnings.append(f"{label} 조회 실패: {exc.status}")
            return []

    tasks: list[Any] = []
    if scope in ("summary", "rights_offering"):
        tasks.append(fetch_endpoint(client.get_rights_offering_decision, _normalize_rights_offering, "유상증자"))
    if scope in ("summary", "convertible_bond"):
        tasks.append(fetch_endpoint(client.get_convertible_bond_decision, _normalize_convertible_bond, "전환사채"))
    if scope in ("summary", "exchangeable_bond"):
        tasks.append(fetch_endpoint(client.get_exchangeable_bond_decision, _normalize_exchangeable_bond, "교환사채"))
    if scope in ("summary", "warrant_bond"):
        tasks.append(fetch_endpoint(client.get_warrant_bond_decision, _normalize_warrant_bond, "신주인수권부사채"))
    if scope in ("summary", "capital_reduction"):
        tasks.append(fetch_endpoint(client.get_capital_reduction_decision, _normalize_capital_reduction, "감자"))

    results = await asyncio.gather(*tasks)
    rows: list[dict[str, Any]] = []
    for r in results:
        rows.extend(r)
    for row in rows:
        _normalize_row_dates(row)  # DART 원본 '2026년 02월 11일' → ISO (전 tool 관행 통일)
    rows.sort(key=lambda row: (row.get("rcept_dt", ""), row.get("rcept_no", "")), reverse=True)
    return rows, warnings, api_calls


def _unsupported_scope_payload(company_query: str, scope: str) -> dict[str, Any]:
    return ToolEnvelope(
        tool="dilutive_issuance",
        status=AnalysisStatus.REQUIRES_REVIEW,
        subject=company_query,
        warnings=[f"`{scope}` scope 미지원."],
        data={
            "query": company_query,
            "scope": scope,
            "supported_scopes": sorted(_SUPPORTED_SCOPES),
        },
    ).to_dict()


async def build_dilutive_issuance_payload(
    company_query: str,
    *,
    scope: str = "summary",
    start_date: str = "",
    end_date: str = "",
) -> dict[str, Any]:
    if scope not in _SUPPORTED_SCOPES:
        return _unsupported_scope_payload(company_query, scope)

    resolution = await resolve_company_query(company_query)
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        return ToolEnvelope(
            tool="dilutive_issuance",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[f"'{company_query}'에 해당하는 회사를 찾지 못했다."],
            data={"query": company_query, "scope": scope},
            next_actions=["company tool로 회사 식별 확인"],
        ).to_dict()
    if resolution.status == AnalysisStatus.AMBIGUOUS:
        return ToolEnvelope(
            tool="dilutive_issuance",
            status=AnalysisStatus.AMBIGUOUS,
            subject=company_query,
            warnings=["회사 식별이 애매해 자동 선택하지 않았다."],
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
    # 기본 lookback 24개월 (희석성 증권 이벤트는 간헐적)
    window_start, window_end, window_warnings = resolve_date_window(
        start_date=start_date,
        end_date=end_date,
        default_end=date.today(),
        lookback_months=24,
    )
    bgn_de = format_yyyymmdd(window_start)
    end_de = format_yyyymmdd(window_end)

    rows, fetch_warnings, api_calls = await _fetch_scope(
        scope, selected["corp_code"], bgn_de, end_de,
    )
    warnings = list(window_warnings) + fetch_warnings

    by_type: dict[str, list[dict[str, Any]]] = {
        "rights_offering": [],
        "convertible_bond": [],
        "warrant_bond": [],
        "exchangeable_bond": [],
        "capital_reduction": [],
    }
    for row in rows:
        by_type.setdefault(row.get("type", ""), []).append(row)

    # EB 보정: 구조화가 정정/철회로 blank이거나(태광형) 0건이면(한라IMS형)
    # list.json+원문으로 복원/추가. 구조화가 EB 완전 제공 시 추가 호출 없음.
    if scope in ("summary", "exchangeable_bond"):
        eb_rows = by_type.get("exchangeable_bond", [])
        rec_warnings, rec_calls, new_eb_rows = await _ensure_eb_coverage(
            eb_rows, selected["corp_code"], bgn_de, end_de,
        )
        warnings.extend(rec_warnings)
        api_calls += rec_calls
        if new_eb_rows:
            rows.extend(new_eb_rows)
            by_type["exchangeable_bond"].extend(new_eb_rows)
        # 같은 EB가 구조화 complete + 정정 stub 복원으로 중복되는 것 제거.
        eb_list = by_type.get("exchangeable_bond", [])
        if len(eb_list) > 1:
            kept = _dedup_eb_rows(eb_list)
            if len(kept) != len(eb_list):
                kept_ids = {id(k) for k in kept}
                rows = [r for r in rows if r.get("type") != "exchangeable_bond" or id(r) in kept_ids]
                by_type["exchangeable_bond"] = kept
        if rec_calls:
            # 복원으로 행 추가/rcept_dt 변경 반영 — timeline 정렬 갱신.
            rows.sort(key=lambda row: (row.get("rcept_dt", ""), row.get("rcept_no", "")), reverse=True)

    usage = {
        "dart_api_calls": api_calls,
        "mcp_tool_calls": 1,
        "dart_daily_limit_per_minute": 1000,
    }

    # 사건 발견 vs 진짜 partial 분리.
    # 4개 결정 API 모두 DART 구조화 응답이라 결과 0건은 사건 자체가 없는 정상 케이스.
    filing_meta = build_filing_meta(
        filing_count=len(rows),
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
        "scope": scope,
        "window": {"start_date": bgn_de, "end_date": end_de},
        "event_count": {
            "total": len(rows),
            "rights_offering": len(by_type.get("rights_offering", [])),
            "convertible_bond": len(by_type.get("convertible_bond", [])),
            "exchangeable_bond": len(by_type.get("exchangeable_bond", [])),
            "warrant_bond": len(by_type.get("warrant_bond", [])),
            "capital_reduction": len(by_type.get("capital_reduction", [])),
        },
        **filing_meta,
        "usage": usage,
        "supported_scopes": sorted(_SUPPORTED_SCOPES),
    }

    # 단일 통합 응답 — timeline + 4 type detail 모두 노출 (scope 분기 폐지).
    data["events_timeline"] = [
        {
            "type": row.get("type", ""),
            "event_label": row.get("event_label", ""),
            "rcept_dt": row.get("rcept_dt", ""),
            "board_decision_date": row.get("board_decision_date", ""),
            "headline_metric": _summary_headline(row),
            "rcept_no": row.get("rcept_no", ""),
        }
        for row in rows
    ]
    data["rights_offering_events"] = by_type.get("rights_offering", [])
    data["convertible_bond_events"] = by_type.get("convertible_bond", [])
    data["exchangeable_bond_events"] = by_type.get("exchangeable_bond", [])
    data["warrant_bond_events"] = by_type.get("warrant_bond", [])
    data["capital_reduction_events"] = by_type.get("capital_reduction", [])

    evidence_refs: list[EvidenceRef] = []
    for row in rows[:5]:
        rcept_no = row.get("rcept_no", "")
        if rcept_no:
            evidence_refs.append(
                EvidenceRef(
                    evidence_id=f"ev_dilutive_{rcept_no}",
                    source_type=SourceType.DART_API,
                    rcept_no=rcept_no,
                    rcept_dt=format_iso_date(row.get("rcept_dt", "")),
                    report_nm=row.get("event_label", ""),
                    section="주요사항보고서 (DS005)",
                    note=f"{row.get('type', '')} / bddd={row.get('board_decision_date', '')}",
                )
            )

    status = status_from_filing_meta(filing_meta)
    if filing_meta["no_filing"]:
        warnings.append(f"조사 구간 ({bgn_de}~{end_de}) 내 희석성 증권(유증/CB/EB/BW/감자) 발행 공시 없음 (정상)")

    return ToolEnvelope(
        tool="dilutive_issuance",
        status=status,
        subject=selected.get("corp_name", company_query),
        warnings=warnings,
        data=data,
        evidence_refs=evidence_refs,
        next_actions=[
            "잠재 희석률은 convertible_bond/warrant_bond의 pct_of_total_shares 참조",
            "3자배정 유상증자는 ownership_structure(scope=changes)와 교차 확인",
            "EB(교환사채)는 교환대상이 자기주식이면 treasury_share와 교차 확인 (의결권 희석)",
        ],
    ).to_dict()


def _summary_headline(row: dict[str, Any]) -> str:
    """summary timeline에서 한 줄 지표."""
    t = row.get("type", "")
    if t == "rights_offering":
        dilution = row.get("dilution_pct_approx", 0)
        method = row.get("issuance_method", "")
        return f"{method} / 신주 {row.get('new_shares_common', 0):,}주 (기존대비 ~{dilution:.2f}%)"
    if t == "convertible_bond":
        cv = row.get("conversion", {})
        return f"{row.get('total_issue_amount', '-')}원 / 전환가 {cv.get('price', '-')} / 희석 {cv.get('pct_of_total_shares', '-')}%"
    if t == "exchangeable_bond":
        if row.get("detection_only"):
            return "EB 공시 발견 — 구조화·원문 미제공, DART 원문 확인 필요 ⚠️"
        ex = row.get("exchange", {})
        tgt = ex.get("target", "") or "-"
        cnt = ex.get("target_share_count", "")
        flag = " ※정정/철회→원문복원" if row.get("recovered_from_document") else ""
        head = f"{row.get('total_issue_amount', '-')}원 / 교환가 {ex.get('price', '-')} / 대상 {tgt}"
        if cnt:
            head += f" {cnt}주"
        return head + flag
    if t == "warrant_bond":
        w = row.get("warrant", {})
        return f"{row.get('total_issue_amount', '-')}원 / 행사가 {w.get('exercise_price', '-')} / 희석 {w.get('pct_of_total_shares', '-')}% / {w.get('detachable', '-')}"
    if t == "capital_reduction":
        return f"감자비율 {row.get('reduction_ratio_common', '-')}% / {row.get('reason', '-')}"
    return ""
