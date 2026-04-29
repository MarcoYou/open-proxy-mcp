"""v2 dilutive_issuance data tool.

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
    "capital_reduction",
}


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
    try:
        return int(re.sub(r"[^\d-]", "", str(value or "0")) or "0")
    except ValueError:
        return 0


def _to_float(value: Any) -> float:
    try:
        return float(re.sub(r"[^\d.-]", "", str(value or "0")) or "0")
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
    if scope in ("summary", "warrant_bond"):
        tasks.append(fetch_endpoint(client.get_warrant_bond_decision, _normalize_warrant_bond, "신주인수권부사채"))
    if scope in ("summary", "capital_reduction"):
        tasks.append(fetch_endpoint(client.get_capital_reduction_decision, _normalize_capital_reduction, "감자"))

    results = await asyncio.gather(*tasks)
    rows: list[dict[str, Any]] = []
    for r in results:
        rows.extend(r)
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
        "capital_reduction": [],
    }
    for row in rows:
        by_type.setdefault(row.get("type", ""), []).append(row)

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
            "warrant_bond": len(by_type.get("warrant_bond", [])),
            "capital_reduction": len(by_type.get("capital_reduction", [])),
        },
        **filing_meta,
        "usage": usage,
        "supported_scopes": sorted(_SUPPORTED_SCOPES),
    }

    if scope == "summary":
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
    if scope == "rights_offering":
        data["rights_offering_events"] = by_type.get("rights_offering", [])
    if scope == "convertible_bond":
        data["convertible_bond_events"] = by_type.get("convertible_bond", [])
    if scope == "warrant_bond":
        data["warrant_bond_events"] = by_type.get("warrant_bond", [])
    if scope == "capital_reduction":
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
        warnings.append(f"조사 구간 ({bgn_de}~{end_de}) 내 희석성 증권(유증/CB/BW/감자) 발행 공시 없음 (정상)")

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
    if t == "warrant_bond":
        w = row.get("warrant", {})
        return f"{row.get('total_issue_amount', '-')}원 / 행사가 {w.get('exercise_price', '-')} / 희석 {w.get('pct_of_total_shares', '-')}% / {w.get('detachable', '-')}"
    if t == "capital_reduction":
        return f"감자비율 {row.get('reduction_ratio_common', '-')}% / {row.get('reason', '-')}"
    return ""
