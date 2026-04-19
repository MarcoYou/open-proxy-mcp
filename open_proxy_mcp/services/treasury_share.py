"""v2 treasury_share facade 서비스.

자기주식 이벤트(취득·처분·소각·신탁) 전용 data tool.
주주환원 관점에서 소각 중심 신호를 애널리스트에게 제공한다.

데이터 소스:
  1. tsstkAqDecsn        — 자기주식 취득결정
  2. tsstkDpDecsn        — 자기주식 처분결정
  3. tsstkAqTrctrCnsDecsn — 자기주식 취득 신탁계약 체결
  4. tsstkAqTrctrCcDecsn  — 자기주식 취득 신탁계약 해지
  5. list.json + keyword  — 자기주식 소각결정 (별도 API 없음)
  6. tesstkAcqsDspsSttus  — 연간 사업보고서 기반 누적 잔고·소각 (기존 재사용)
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.services.company import _company_id, resolve_company_query
from open_proxy_mcp.services.contracts import (
    AnalysisStatus,
    EvidenceRef,
    SourceType,
    ToolEnvelope,
)
from open_proxy_mcp.services.date_utils import (
    format_iso_date,
    format_yyyymmdd,
    resolve_date_window,
)
from open_proxy_mcp.services.filing_search import search_filings_by_report_name


_SUPPORTED_SCOPES = {"summary", "events", "acquisition", "disposal", "cancelation", "annual"}
_CANCELATION_KEYWORDS = ("자기주식소각결정", "자사주소각결정", "자기주식소각")


def _to_int(value: Any) -> int:
    try:
        return int(str(value).replace(",", "").strip() or 0)
    except Exception:
        return 0


def _rcept_dt_from_no(rcept_no: str) -> str:
    if len(rcept_no) >= 8 and rcept_no[:8].isdigit():
        return rcept_no[:8]
    return ""


def _normalize_acquisition(item: dict[str, Any]) -> dict[str, Any]:
    """자기주식 취득결정 (tsstkAqDecsn) — 보통주+우선주 수량·금액 합산.

    `aq_pp`(취득목적)에 "소각" 포함 시 `for_cancelation=True` — 소각결정 별도 공시 없이
    취득 단계에서 소각 의도를 밝히는 케이스(예: 미래에셋증권)를 잡아낸다.
    """

    shares = _to_int(item.get("aqpln_stk_ostk")) + _to_int(item.get("aqpln_stk_estk"))
    amount = _to_int(item.get("aqpln_prc_ostk")) + _to_int(item.get("aqpln_prc_estk"))
    purpose = (item.get("aq_pp") or "").strip()
    for_cancelation = "소각" in purpose
    return {
        "event": "acquisition_decision",
        "rcept_no": item.get("rcept_no", ""),
        "rcept_dt": _rcept_dt_from_no(item.get("rcept_no", "")),
        "corp_name": item.get("corp_name", ""),
        "report_nm": "자기주식 취득 결정",
        "shares": shares,
        "amount_krw": amount,
        "purpose": purpose,
        "method": (item.get("aq_mth") or "").strip(),
        "start_date": (item.get("aqexpd_bgd") or "").strip(),
        "end_date": (item.get("aqexpd_edd") or "").strip(),
        "board_date": (item.get("aq_dd") or "").strip(),
        "for_cancelation": for_cancelation,
    }


def _normalize_disposal(item: dict[str, Any]) -> dict[str, Any]:
    """자기주식 처분결정 (tsstkDpDecsn)."""

    shares = _to_int(item.get("dppln_stk_ostk")) + _to_int(item.get("dppln_stk_estk"))
    amount = _to_int(item.get("dppln_prc_ostk")) + _to_int(item.get("dppln_prc_estk"))
    return {
        "event": "disposal_decision",
        "rcept_no": item.get("rcept_no", ""),
        "rcept_dt": _rcept_dt_from_no(item.get("rcept_no", "")),
        "corp_name": item.get("corp_name", ""),
        "report_nm": "자기주식 처분 결정",
        "shares": shares,
        "amount_krw": amount,
        "purpose": (item.get("dp_pp") or "").strip(),
        "start_date": (item.get("dpprpd_bgd") or "").strip(),
        "end_date": (item.get("dpprpd_edd") or "").strip(),
        "board_date": (item.get("dp_dd") or "").strip(),
    }


def _normalize_trust(item: dict[str, Any], event: str, label: str) -> dict[str, Any]:
    """자기주식 신탁체결/해지 — DART 필드명 추정. 필드가 없어도 rcept_no 기준으로 노출."""

    amount = 0
    for key in ("ctr_prc", "ctr_prc_am", "ctr_pr"):
        amount = _to_int(item.get(key, 0))
        if amount:
            break
    return {
        "event": event,
        "rcept_no": item.get("rcept_no", ""),
        "rcept_dt": _rcept_dt_from_no(item.get("rcept_no", "")),
        "corp_name": item.get("corp_name", ""),
        "report_nm": label,
        "shares": 0,
        "amount_krw": amount,
        "purpose": (item.get("ctr_pp") or "").strip(),
        "start_date": (item.get("ctr_cns_prd_bgd") or item.get("ctr_prd_bgd") or "").strip(),
        "end_date": (item.get("ctr_cns_prd_edd") or item.get("ctr_prd_edd") or "").strip(),
        "board_date": (item.get("ctr_cns_dd") or item.get("ctr_cc_dd") or "").strip(),
    }


def _normalize_cancelation_row(item: dict[str, Any]) -> dict[str, Any]:
    """자기주식 소각결정은 list.json 기반이라 본문 상세가 아닌 메타만 포함."""

    return {
        "event": "cancelation_decision",
        "rcept_no": item.get("rcept_no", ""),
        "rcept_dt": item.get("rcept_dt", ""),
        "report_nm": item.get("report_nm", ""),
        "corp_name": item.get("corp_name", ""),
        "filer_name": item.get("flr_nm", ""),
    }


async def _fetch_decisions(corp_code: str, bgn_de: str, end_de: str) -> tuple[dict[str, list[dict]], list[str]]:
    """취득·처분·신탁체결·신탁해지 4개 API 병렬 호출 + 소각결정 list.json 검색."""

    client = get_dart_client()

    async def safe(coro, label: str) -> tuple[list[dict[str, Any]], str | None]:
        try:
            res = await coro
            return res.get("list", []) or [], None
        except DartClientError as exc:
            return [], f"{label} 조회 실패: {exc.status}"

    acq_task = safe(client.get_treasury_acquisition(corp_code, bgn_de, end_de), "취득결정")
    dsp_task = safe(client.get_treasury_disposal(corp_code, bgn_de, end_de), "처분결정")
    trc_task = safe(client.get_treasury_trust_contract(corp_code, bgn_de, end_de), "신탁계약 체결결정")
    trt_task = safe(client.get_treasury_trust_termination(corp_code, bgn_de, end_de), "신탁계약 해지결정")

    async def cancelation_search():
        items, _notices, error = await search_filings_by_report_name(
            corp_code=corp_code,
            bgn_de=bgn_de,
            end_de=end_de,
            pblntf_tys=("B", "I"),
            keywords=_CANCELATION_KEYWORDS,
            strip_spaces=True,
        )
        if error:
            return [], f"자사주 소각결정 조회 실패: {error}"
        return items, None

    (acq, w1), (dsp, w2), (trc, w3), (trt, w4), (ret, w5) = await asyncio.gather(
        acq_task, dsp_task, trc_task, trt_task, cancelation_search()
    )
    warnings = [w for w in (w1, w2, w3, w4, w5) if w]

    return {
        "acquisition": [_normalize_acquisition(i) for i in acq],
        "disposal": [_normalize_disposal(i) for i in dsp],
        "trust_contract": [_normalize_trust(i, "trust_contract", "자기주식 취득 신탁계약 체결 결정") for i in trc],
        "trust_termination": [_normalize_trust(i, "trust_termination", "자기주식 취득 신탁계약 해지 결정") for i in trt],
        "cancelation": [_normalize_cancelation_row(i) for i in ret],
    }, warnings


def _combined_events(bundles: dict[str, list[dict]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("acquisition", "disposal", "trust_contract", "trust_termination", "cancelation"):
        rows.extend(bundles.get(key, []))
    rows.sort(key=lambda r: (r.get("rcept_dt", ""), r.get("rcept_no", "")), reverse=True)
    return rows


def _summary_counts(bundles: dict[str, list[dict]]) -> dict[str, Any]:
    acq = bundles.get("acquisition", [])
    # 취득목적에 "소각" 명시된 건. 별도 소각결정 공시 없는 기업(예: 미래에셋증권)에서 주주환원 신호로 쓰임.
    acq_for_cancelation = [r for r in acq if r.get("for_cancelation")]
    return {
        "acquisition_count": len(acq),
        "acquisition_for_cancelation_count": len(acq_for_cancelation),
        "disposal_count": len(bundles.get("disposal", [])),
        "trust_contract_count": len(bundles.get("trust_contract", [])),
        "trust_termination_count": len(bundles.get("trust_termination", [])),
        "cancelation_count": len(bundles.get("cancelation", [])),
        "total_event_count": sum(len(bundles.get(k, [])) for k in ("acquisition", "disposal", "trust_contract", "trust_termination", "cancelation")),
        "acquisition_shares_total": sum(r.get("shares", 0) for r in acq),
        "acquisition_amount_total_krw": sum(r.get("amount_krw", 0) for r in acq),
        "acquisition_for_cancelation_shares_total": sum(r.get("shares", 0) for r in acq_for_cancelation),
        "acquisition_for_cancelation_amount_total_krw": sum(r.get("amount_krw", 0) for r in acq_for_cancelation),
        "disposal_shares_total": sum(r.get("shares", 0) for r in bundles.get("disposal", [])),
        "trust_contract_amount_total_krw": sum(r.get("amount_krw", 0) for r in bundles.get("trust_contract", [])),
    }


async def build_treasury_share_payload(
    company_query: str,
    *,
    scope: str = "summary",
    year: int | None = None,
    start_date: str = "",
    end_date: str = "",
    lookback_months: int = 24,
) -> dict[str, Any]:
    if scope not in _SUPPORTED_SCOPES:
        return ToolEnvelope(
            tool="treasury_share",
            status=AnalysisStatus.REQUIRES_REVIEW,
            subject=company_query,
            warnings=[f"`{scope}` scope는 아직 지원하지 않는다."],
            data={"query": company_query, "scope": scope},
        ).to_dict()

    resolution = await resolve_company_query(company_query)
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        return ToolEnvelope(
            tool="treasury_share",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[f"'{company_query}'에 해당하는 상장사를 찾지 못했다."],
            data={"query": company_query, "scope": scope},
        ).to_dict()
    if resolution.status == AnalysisStatus.AMBIGUOUS:
        return ToolEnvelope(
            tool="treasury_share",
            status=AnalysisStatus.AMBIGUOUS,
            subject=company_query,
            warnings=["회사 식별이 애매해 자사주 공시를 자동 선택하지 않았다."],
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
    default_end = date(year, 12, 31) if year else date.today()
    window_start, window_end, window_warnings = resolve_date_window(
        start_date=start_date,
        end_date=end_date,
        default_end=default_end,
        lookback_months=lookback_months,
    )
    bgn_de = format_yyyymmdd(window_start)
    end_de = format_yyyymmdd(window_end)
    warnings: list[str] = list(window_warnings)

    bundles, fetch_warnings = await _fetch_decisions(selected["corp_code"], bgn_de, end_de)
    warnings.extend(fetch_warnings)

    counts = _summary_counts(bundles)
    events = _combined_events(bundles)

    data: dict[str, Any] = {
        "query": company_query,
        "company_id": _company_id(selected),
        "canonical_name": selected.get("corp_name", ""),
        "identifiers": {
            "ticker": selected.get("stock_code", ""),
            "corp_code": selected.get("corp_code", ""),
        },
        "window": {
            "start_date": bgn_de,
            "end_date": end_de,
            "lookback_months": lookback_months,
        },
        "summary": counts,
        "available_scopes": sorted(_SUPPORTED_SCOPES),
    }

    if scope == "events":
        data["events"] = events
    if scope == "acquisition":
        data["events"] = bundles.get("acquisition", []) + bundles.get("trust_contract", [])
    if scope == "disposal":
        data["events"] = bundles.get("disposal", []) + bundles.get("trust_termination", [])
    if scope == "cancelation":
        data["events"] = bundles.get("cancelation", [])
    if scope == "summary":
        data["latest_events"] = events[:5]
    if scope == "annual":
        # 연간 누적은 ownership_structure(scope="treasury")에서 가져온다.
        from open_proxy_mcp.services.ownership_structure import build_ownership_structure_payload
        own_payload = await build_ownership_structure_payload(company_query, scope="treasury", year=year)
        data["annual"] = own_payload.get("data", {}).get("treasury", {})

    # evidence_refs — 최신 5건 이벤트의 공시
    evidence_refs: list[EvidenceRef] = []
    for ev in events[:5]:
        if not ev.get("rcept_no"):
            continue
        evidence_refs.append(
            EvidenceRef(
                evidence_id=f"ev_treasury_{ev['event']}_{ev['rcept_no']}",
                source_type=SourceType.DART_API if ev["event"] != "cancelation_decision" else SourceType.DART_XML,
                rcept_no=ev["rcept_no"],
                rcept_dt=format_iso_date(ev.get("rcept_dt", "")),
                report_nm=ev.get("report_nm", ""),
                section=ev["event"],
                note=f"{ev.get('shares', 0):,}주" if ev.get("shares") else "",
            )
        )

    status = AnalysisStatus.EXACT if events else AnalysisStatus.PARTIAL
    if not events:
        warnings.append("요청 구간에 자사주 이벤트 공시가 확인되지 않았다. 연간 누적은 `scope='annual'`로 확인할 수 있다.")

    return ToolEnvelope(
        tool="treasury_share",
        status=status,
        subject=selected.get("corp_name", company_query),
        warnings=warnings,
        data=data,
        evidence_refs=evidence_refs,
        next_actions=[
            "scope=`cancelation`으로 소각결정만 확인" if scope == "summary" else "value_up 교차 참조로 주주환원 정책 신호 함께 해석",
            "scope=`annual`로 사업보고서 기준 연간 잔고·소각 누적 확인",
        ],
    ).to_dict()
