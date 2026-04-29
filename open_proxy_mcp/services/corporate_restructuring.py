"""v2 corporate_restructuring data tool.

회사합병 / 분할 / 분할합병 / 주식교환·이전 4종 주요사항보고서 결정을 한 탭에서 제공.
지배구조 재편의 정형화된 4개 경로를 하나의 tool로 통합.
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


_SUPPORTED_SCOPES = {"summary", "merger", "split", "share_exchange"}


def _clean(value: Any) -> str:
    text = str(value or "").strip()
    if text in ("-", "해당사항없음"):
        return ""
    return text


def _truncate(value: str, limit: int = 200) -> str:
    text = _clean(value)
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    return text if len(text) <= limit else text[:limit].rstrip() + "…"


def _normalize_merger(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "merger",
        "event_label": "회사합병결정",
        "rcept_no": item.get("rcept_no", ""),
        "rcept_dt": item.get("rcept_dt", ""),
        "board_decision_date": _clean(item.get("bddd", "")),
        "scale": _clean(item.get("mg_stn", "")),  # 소규모합병 / 일반합병
        "method": _truncate(item.get("mg_mth", "")),  # 흡수합병/신설합병
        "purpose": _truncate(item.get("mg_pp", "")),
        "ratio": _clean(item.get("mg_rt", "")),
        "ratio_basis": _truncate(item.get("mg_rt_bs", "")),
        "new_shares_common": _clean(item.get("mgnstk_ostk_cnt", "")),
        "new_shares_preferred": _clean(item.get("mgnstk_cstk_cnt", "")),
        "counterparty": {
            "name": _clean(item.get("mgptncmp_cmpnm", "")),
            "business": _truncate(item.get("mgptncmp_mbsn", ""), 100),
            "relationship": _clean(item.get("mgptncmp_rl_cmpn", "")),
            "financial": {
                "total_assets": _clean(item.get("rbsnfdtl_tast", "")),
                "total_debt": _clean(item.get("rbsnfdtl_tdbt", "")),
                "total_equity": _clean(item.get("rbsnfdtl_teqt", "")),
                "revenue": _clean(item.get("rbsnfdtl_sl", "")),
                "net_income": _clean(item.get("rbsnfdtl_nic", "")),
            },
        },
        "external_evaluator": _clean(item.get("exevl_intn", "")),
        "external_eval_opinion": _truncate(item.get("exevl_op", ""), 150),
        "audit_opinion_on_counterparty": _clean(item.get("eadtat_op", "")),
        "appraisal_right_price": _clean(item.get("aprskh_plnprc", "")),
        "put_option_applicable": _clean(item.get("popt_ctr_atn", "")),
        "put_option_content": _truncate(item.get("popt_ctr_cn", ""), 150),
        "listed_counterparty": _clean(item.get("bdlst_atn", "")),
    }


def _normalize_split(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "split",
        "event_label": "회사분할결정",
        "rcept_no": item.get("rcept_no", ""),
        "rcept_dt": item.get("rcept_dt", ""),
        "board_decision_date": _clean(item.get("bddd", "")),
        "split_form": _truncate(item.get("ex_sm_r", ""), 100),  # 단순·물적분할 등
        "method": _truncate(item.get("dv_mth", ""), 300),
        "impact_on_ownership": _truncate(item.get("dv_impef", ""), 300),
        "ratio": _truncate(item.get("dv_rt", ""), 200),
        "transferred_business": _truncate(item.get("dv_trfbsnprt_cn", ""), 200),
        "surviving_company": {
            "name": _clean(item.get("atdv_excmp_cmpnm", "")),
            "business": _truncate(item.get("atdv_excmp_mbsn", ""), 100),
            "will_remain_listed": _clean(item.get("atdv_excmp_atdv_lstmn_atn", "")),
            "financial": {
                "total_assets": _clean(item.get("atdvfdtl_tast", "")),
                "total_equity": _clean(item.get("atdvfdtl_teqt", "")),
            },
        },
        "new_company": {
            "name": _clean(item.get("dvfcmp_cmpnm", "")),
            "business": _truncate(item.get("dvfcmp_mbsn", ""), 100),
            "projected_revenue": _clean(item.get("dvfcmp_nbsn_rsl", "")),
            "will_relist": _clean(item.get("dvfcmp_rlst_atn", "")),
        },
        "shareholder_meeting_date": _clean(item.get("gmtsck_prd", "")),
        "split_date": _clean(item.get("dvdt", "")),
        "split_registration_date": _clean(item.get("dvrgsprd", "")),
        "put_option_applicable": _clean(item.get("popt_ctr_atn", "")),
    }


def _normalize_stock_exchange(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "share_exchange",
        "event_label": "주식교환·이전결정",
        "rcept_no": item.get("rcept_no", ""),
        "rcept_dt": item.get("rcept_dt", ""),
        "board_decision_date": _clean(item.get("bddd", "")),
        "exchange_kind": _clean(item.get("extr_sen", "")),  # 주식교환 / 주식이전
        "scale": _clean(item.get("extr_stn", "")),  # 소규모 등
        "ratio": _truncate(item.get("extr_rt", ""), 200),
        "ratio_basis": _truncate(item.get("extr_rt_bs", ""), 200),
        "purpose": _truncate(item.get("extr_pp", "")),
        "target_company": {
            "name": _clean(item.get("extr_tgcmp_cmpnm", "")),
            "representative": _clean(item.get("extr_tgcmp_rp", "")),
            "business": _truncate(item.get("extr_tgcmp_mbsn", ""), 100),
            "relationship": _clean(item.get("extr_tgcmp_rl_cmpn", "")),  # 자회사 등
            "outstanding_common": _clean(item.get("extr_tgcmp_tisstk_ostk", "")),
            "outstanding_preferred": _clean(item.get("extr_tgcmp_tisstk_cstk", "")),
            "financial": {
                "total_assets": _clean(item.get("rbsnfdtl_tast", "")),
                "total_equity": _clean(item.get("rbsnfdtl_teqt", "")),
                "revenue": _clean(item.get("rbsnfdtl_sl", "")),
            },
        },
        "external_evaluator": _clean(item.get("exevl_intn", "")),
        "external_eval_opinion": _truncate(item.get("exevl_op", ""), 150),
        "appraisal_right_price": _clean(item.get("aprskh_plnprc", "")),
        "schedule": {
            "exchange_contract": _clean(item.get("extrsc_extrctrd", "")),
            "shareholders_meeting": _clean(item.get("extrsc_gmtsck_prd", "")),
            "exchange_date": _clean(item.get("extrsc_extrdt", "")),
        },
        "put_option_applicable": _clean(item.get("popt_ctr_atn", "")),
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
    if scope in ("summary", "merger"):
        tasks.append(fetch_endpoint(client.get_merger_decision, _normalize_merger, "합병결정"))
    if scope in ("summary", "split"):
        tasks.append(fetch_endpoint(client.get_division_decision, _normalize_split, "분할결정"))
        tasks.append(fetch_endpoint(
            client.get_division_merger_decision,
            lambda it: {**_normalize_split(it), "event_label": "회사분할합병결정", "type": "division_merger"},
            "분할합병결정",
        ))
    if scope in ("summary", "share_exchange"):
        tasks.append(fetch_endpoint(client.get_stock_exchange_decision, _normalize_stock_exchange, "주식교환·이전결정"))

    results = await asyncio.gather(*tasks)
    rows: list[dict[str, Any]] = []
    for r in results:
        rows.extend(r)
    rows.sort(key=lambda row: (row.get("rcept_dt", ""), row.get("rcept_no", "")), reverse=True)
    return rows, warnings, api_calls


def _unsupported_scope_payload(company_query: str, scope: str) -> dict[str, Any]:
    return ToolEnvelope(
        tool="corporate_restructuring",
        status=AnalysisStatus.REQUIRES_REVIEW,
        subject=company_query,
        warnings=[f"`{scope}` scope 미지원."],
        data={
            "query": company_query,
            "scope": scope,
            "supported_scopes": sorted(_SUPPORTED_SCOPES),
        },
    ).to_dict()


async def build_corporate_restructuring_payload(
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
            tool="corporate_restructuring",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[f"'{company_query}'에 해당하는 회사를 찾지 못했다."],
            data={"query": company_query, "scope": scope},
            next_actions=["company tool로 회사 식별 확인"],
        ).to_dict()
    if resolution.status == AnalysisStatus.AMBIGUOUS:
        return ToolEnvelope(
            tool="corporate_restructuring",
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
    # 기본 lookback 24개월 (M&A 이벤트는 드물어서 길게 봄)
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

    by_type: dict[str, list[dict[str, Any]]] = {"merger": [], "split": [], "share_exchange": [], "division_merger": []}
    for row in rows:
        by_type.setdefault(row.get("type", ""), []).append(row)

    usage = {
        "dart_api_calls": api_calls,
        "mcp_tool_calls": 1,
        "dart_daily_limit_per_minute": 1000,
    }

    # 사건 발견 vs 진짜 partial 분리 메타.
    # corporate_restructuring은 모든 fetch가 DART API 구조화 응답이라
    # API가 "결과 없음(013)"으로 응답하면 사건 자체가 없는 정상 케이스다.
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
            "merger": len(by_type.get("merger", [])),
            "split": len(by_type.get("split", [])),
            "division_merger": len(by_type.get("division_merger", [])),
            "share_exchange": len(by_type.get("share_exchange", [])),
        },
        **filing_meta,
        "usage": usage,
        "supported_scopes": sorted(_SUPPORTED_SCOPES),
    }

    if scope == "summary":
        data["events_timeline"] = [
            {
                "type": row["type"],
                "event_label": row.get("event_label", ""),
                "rcept_dt": row.get("rcept_dt", ""),
                "board_decision_date": row.get("board_decision_date", ""),
                "counterparty_or_new_entity": (
                    row.get("counterparty", {}).get("name")
                    or row.get("target_company", {}).get("name")
                    or row.get("new_company", {}).get("name")
                    or ""
                ),
                "ratio": row.get("ratio", ""),
                "rcept_no": row.get("rcept_no", ""),
            }
            for row in rows
        ]
    if scope == "merger":
        data["merger_events"] = by_type.get("merger", [])
    if scope == "split":
        data["split_events"] = by_type.get("split", []) + by_type.get("division_merger", [])
    if scope == "share_exchange":
        data["share_exchange_events"] = by_type.get("share_exchange", [])

    evidence_refs: list[EvidenceRef] = []
    for row in rows[:5]:
        rcept_no = row.get("rcept_no", "")
        if rcept_no:
            evidence_refs.append(
                EvidenceRef(
                    evidence_id=f"ev_restructuring_{rcept_no}",
                    source_type=SourceType.DART_API,
                    rcept_no=rcept_no,
                    rcept_dt=format_iso_date(row.get("rcept_dt", "")),
                    report_nm=row.get("event_label", ""),
                    section="주요사항보고서",
                    note=f"{row.get('type', '')} / bddd={row.get('board_decision_date', '')}",
                )
            )

    status = status_from_filing_meta(filing_meta)
    if filing_meta["no_filing"]:
        warnings.append(f"조사 구간 ({bgn_de}~{end_de}) 내 지배구조 재편(합병/분할/주식교환) 공시 없음 (정상)")

    return ToolEnvelope(
        tool="corporate_restructuring",
        status=status,
        subject=selected.get("corp_name", company_query),
        warnings=warnings,
        data=data,
        evidence_refs=evidence_refs,
        next_actions=[
            "개별 사건 원문 확인은 evidence tool 사용",
            "지분 변화는 ownership_structure(scope=changes)와 교차 확인",
        ],
    ).to_dict()
