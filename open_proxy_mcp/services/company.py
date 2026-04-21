"""v2 company facade 서비스."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, timedelta
import re
from typing import Any

from open_proxy_mcp.dart.client import (
    _CORP_ALIASES,
    _normalize_corp_name,
    _sort_corp_results,
    DartClientError,
    get_dart_client,
)
from open_proxy_mcp.services.contracts import AnalysisStatus, ToolEnvelope, build_usage
from open_proxy_mcp.services.date_utils import format_yyyymmdd, resolve_date_window

_RECENT_LOOKBACK_DAYS = 180


@dataclass(slots=True)
class CompanyResolution:
    """후속 data tool용 회사 식별 결과."""

    status: AnalysisStatus
    query: str
    selected: dict[str, Any] | None
    candidates: list[dict[str, Any]]


def _company_id(corp: dict[str, Any]) -> str:
    stock_code = (corp.get("stock_code") or "").strip()
    return f"cmp_{stock_code or corp.get('corp_code', '')}"


def _resolve_match(query: str, matches: list[dict[str, Any]]) -> tuple[AnalysisStatus, dict[str, Any] | None, list[dict[str, Any]]]:
    raw = query.strip()
    if not matches:
        return AnalysisStatus.ERROR, None, []

    if re.fullmatch(r"\d{6}", raw):
        numeric = [corp for corp in matches if corp.get("stock_code") == raw]
        if len(numeric) == 1:
            return AnalysisStatus.EXACT, numeric[0], matches

    if re.fullmatch(r"\d{8}", raw):
        numeric = [corp for corp in matches if corp.get("corp_code") == raw]
        if len(numeric) == 1:
            return AnalysisStatus.EXACT, numeric[0], matches

    alias_query = _CORP_ALIASES.get(raw.lower(), raw)
    exact = [corp for corp in matches if corp.get("corp_name") == alias_query]
    if len(exact) == 1:
        return AnalysisStatus.EXACT, exact[0], matches
    if len(exact) > 1:
        ranked = _sort_corp_results(exact)
        top = ranked[0]
        second = ranked[1] if len(ranked) > 1 else None
        if top.get("stock_code") and (
            second is None
            or not second.get("stock_code")
            or (top.get("modify_date") or "") > (second.get("modify_date") or "")
        ):
            return AnalysisStatus.EXACT, top, ranked
        return AnalysisStatus.AMBIGUOUS, None, ranked

    norm_query = _normalize_corp_name(alias_query)
    normalized = [
        corp for corp in matches
        if _normalize_corp_name(corp.get("corp_name", "")) == norm_query
    ]
    if len(normalized) == 1:
        return AnalysisStatus.EXACT, normalized[0], matches
    if len(normalized) > 1:
        ranked = _sort_corp_results(normalized)
        top = ranked[0]
        second = ranked[1] if len(ranked) > 1 else None
        if top.get("stock_code") and (
            second is None
            or not second.get("stock_code")
            or (top.get("modify_date") or "") > (second.get("modify_date") or "")
        ):
            return AnalysisStatus.EXACT, top, ranked
        return AnalysisStatus.AMBIGUOUS, None, ranked

    return AnalysisStatus.AMBIGUOUS, None, matches


def _aliases_for_company(corp_name: str, query: str) -> list[str]:
    aliases = [key for key, value in _CORP_ALIASES.items() if value == corp_name]
    if query and query not in aliases and query != corp_name:
        aliases.insert(0, query)
    deduped: list[str] = []
    seen: set[str] = set()
    for alias in aliases:
        alias = alias.strip()
        if alias and alias not in seen:
            seen.add(alias)
            deduped.append(alias)
    return deduped[:10]


def _classify_filing(item: dict[str, Any]) -> str:
    report_name = (item.get("report_nm") or "").replace(" ", "")
    if "주주총회소집공고" in report_name:
        return "shareholder_meeting_notice"
    if "주주총회결과" in report_name:
        return "shareholder_meeting_result"
    if "현금ㆍ현물배당결정" in report_name or "현금·현물배당결정" in report_name:
        return "dividend_decision"
    if "기업가치제고계획" in report_name:
        return "value_up"
    if "위임장권유" in report_name:
        return "proxy_solicitation"
    if "공개매수" in report_name:
        return "tender_offer"
    if "대량보유" in report_name or "보유상황보고" in report_name:
        return "ownership_block"
    if "소송" in report_name or "가처분" in report_name:
        return "litigation"
    if item.get("pblntf_ty") == "I":
        return "exchange_disclosure"
    return "other"


async def _safe_company_info(corp_code: str) -> tuple[dict[str, Any], str | None]:
    client = get_dart_client()
    try:
        return await client.get_company_info(corp_code), None
    except DartClientError as exc:
        return {}, f"DART company.json 조회 실패: {exc.status}"


async def _safe_naver_profile(stock_code: str) -> tuple[dict[str, Any], str | None]:
    client = get_dart_client()
    if not stock_code:
        return {}, None
    try:
        return await client.get_naver_corp_profile(stock_code), None
    except Exception:
        return {}, "NAVER 업종 보강 실패"


async def _safe_recent_filings(
    corp_code: str,
    max_items: int,
    *,
    start_date: str = "",
    end_date: str = "",
) -> tuple[list[dict[str, Any]], dict[str, str], str | None]:
    client = get_dart_client()
    begin_date, finish_date, window_warnings = resolve_date_window(
        start_date=start_date,
        end_date=end_date,
        default_end=date.today(),
        lookback_days=_RECENT_LOOKBACK_DAYS,
    )
    try:
        result = await client.search_filings(
            corp_code=corp_code,
            bgn_de=format_yyyymmdd(begin_date),
            end_de=format_yyyymmdd(finish_date),
            page_count=min(max(max_items * 3, 20), 100),
        )
    except DartClientError as exc:
        return [], {"start_date": format_yyyymmdd(begin_date), "end_date": format_yyyymmdd(finish_date)}, f"최근 공시 인덱스 조회 실패: {exc.status}"

    filings: list[dict[str, Any]] = []
    for item in result.get("list", [])[:max_items]:
        filings.append({
            "filing_type": _classify_filing(item),
            "report_name": (item.get("report_nm") or "").strip(),
            "disclosure_date": item.get("rcept_dt", ""),
            "rcept_no": item.get("rcept_no", ""),
            "filer_name": item.get("flr_nm", ""),
            "pblntf_ty": item.get("pblntf_ty", ""),
        })
    warning = " / ".join(window_warnings) if window_warnings else None
    return filings, {"start_date": format_yyyymmdd(begin_date), "end_date": format_yyyymmdd(finish_date)}, warning


def _candidate_row(corp: dict[str, Any]) -> dict[str, Any]:
    return {
        "company_id": _company_id(corp),
        "corp_name": corp.get("corp_name", ""),
        "ticker": corp.get("stock_code", ""),
        "corp_code": corp.get("corp_code", ""),
        "modify_date": corp.get("modify_date", ""),
    }


async def build_company_payload(
    query: str,
    *,
    max_recent_filings: int = 10,
    start_date: str = "",
    end_date: str = "",
) -> dict[str, Any]:
    """회사 식별 + 최근 공시 인덱스."""

    client = get_dart_client()
    _calls_start = client.api_call_snapshot()
    matches = await client.lookup_corp_code_all(query)

    raw = (query or "").strip()
    numeric_query = re.fullmatch(r"\d{6}", raw) or re.fullmatch(r"\d{8}", raw)
    unlisted_only = False
    if not numeric_query and matches:
        listed = [m for m in matches if (m.get("stock_code") or "").strip()]
        if listed:
            matches = listed
        else:
            unlisted_only = True
            matches = []

    status, selected, candidates = _resolve_match(query, matches)

    if status == AnalysisStatus.ERROR:
        warnings = [f"'{query}'에 해당하는 회사를 찾지 못했다."]
        if unlisted_only:
            warnings.append("입력에 일치하는 법인은 비상장이어서 OPM 분석 대상(상장사)에서 제외했다. 정확한 상장사 종목명/종목코드로 다시 조회한다.")
        envelope = ToolEnvelope(
            tool="company",
            status=AnalysisStatus.ERROR,
            subject=query,
            warnings=warnings,
            data={
                "query": query,
                "candidates": [],
                "usage": build_usage(client.api_call_snapshot() - _calls_start),
            },
            next_actions=["정확한 회사명, 종목코드, corp_code 중 하나로 다시 조회"],
        )
        return envelope.to_dict()

    if status == AnalysisStatus.AMBIGUOUS or not selected:
        envelope = ToolEnvelope(
            tool="company",
            status=AnalysisStatus.AMBIGUOUS,
            subject=query,
            warnings=["자동 선택을 하지 않았다. 후보를 확인한 뒤 종목코드 또는 정확한 회사명으로 다시 조회해야 한다."],
            data={
                "query": query,
                "candidates": [_candidate_row(corp) for corp in candidates[:10]],
                "usage": build_usage(client.api_call_snapshot() - _calls_start),
            },
            next_actions=["ticker 또는 corp_code를 직접 넣어 재조회"],
        )
        return envelope.to_dict()

    company_info_task = _safe_company_info(selected["corp_code"])
    naver_task = _safe_naver_profile(selected.get("stock_code", ""))
    filings_task = _safe_recent_filings(
        selected["corp_code"],
        max_recent_filings,
        start_date=start_date,
        end_date=end_date,
    )

    (company_info, company_warn), (naver, naver_warn), (recent_filings, filings_window, filings_warn) = await asyncio.gather(
        company_info_task,
        naver_task,
        filings_task,
    )

    corp_name = company_info.get("corp_name") or selected.get("corp_name", "")
    corp_name_eng = company_info.get("corp_name_eng", "")
    corp_cls = company_info.get("corp_cls", "")
    market_map = {
        "Y": "KOSPI",
        "K": "KOSDAQ",
        "N": "KONEX",
        "E": "비상장",
    }
    warnings = [warning for warning in (company_warn, naver_warn, filings_warn) if warning]
    if not company_info.get("jurir_no"):
        warnings.append("ISIN은 아직 v2 company tool에 연결되지 않았다.")

    payload = {
        "query": query,
        "company_id": _company_id(selected),
        "canonical_name": corp_name,
        "identifiers": {
            "ticker": selected.get("stock_code", ""),
            "corp_code": selected.get("corp_code", ""),
            "isin": "",
            "jurir_no": company_info.get("jurir_no", ""),
            "bizr_no": company_info.get("bizr_no", ""),
        },
        "classification": {
            "market": market_map.get(corp_cls, corp_cls or "미상"),
            "corp_cls": corp_cls,
            "sector_name": naver.get("sector_name", ""),
            "sector_code": naver.get("sector_code", ""),
            "induty_code": company_info.get("induty_code", ""),
            "fiscal_month": company_info.get("acc_mt", ""),
        },
        "names": {
            "ko": corp_name,
            "en": corp_name_eng,
            "aliases": _aliases_for_company(corp_name, query),
        },
        "basic_info": {
            "ceo_name": company_info.get("ceo_nm", ""),
            "homepage": company_info.get("hm_url", ""),
            "address": company_info.get("adres", ""),
            "established_date": company_info.get("est_dt", ""),
        },
        "recent_filings_window": filings_window,
        "recent_filings": recent_filings,
        "usage": build_usage(client.api_call_snapshot() - _calls_start),
    }

    envelope = ToolEnvelope(
        tool="company",
        status=AnalysisStatus.EXACT,
        subject=corp_name,
        warnings=warnings,
        data=payload,
        next_actions=[
            "shareholder_meeting, ownership_structure, dividend 등 후속 data tool에서 company_id 또는 ticker 사용",
        ],
    )
    return envelope.to_dict()


async def resolve_company_query(query: str) -> CompanyResolution:
    """회사 입력을 exact/ambiguous/error 상태로 정규화.

    OPM은 상장사(주총/배당/지분) 분석 도구이므로 stock_code가 없는 비상장 법인은
    후보에서 제외한다. 단, corp_code/stock_code를 숫자로 직접 입력한 경우는 예외.
    """

    client = get_dart_client()
    matches = await client.lookup_corp_code_all(query)

    raw = (query or "").strip()
    numeric_query = re.fullmatch(r"\d{6}", raw) or re.fullmatch(r"\d{8}", raw)
    if not numeric_query:
        listed = [m for m in matches if (m.get("stock_code") or "").strip()]
        if listed:
            matches = listed
        elif matches:
            # 상장사 후보가 없고 비상장만 남은 경우: OPM 유니버스 밖이므로 error로 유도
            return CompanyResolution(
                status=AnalysisStatus.ERROR,
                query=query,
                selected=None,
                candidates=[],
            )

    status, selected, candidates = _resolve_match(query, matches)
    return CompanyResolution(
        status=status,
        query=query,
        selected=selected,
        candidates=candidates,
    )
