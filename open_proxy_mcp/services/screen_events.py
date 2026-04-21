"""v2 screen_events discovery 서비스.

event_type 기반으로 기간 내 공시 낸 기업을 역조회한다 (filing-centric).
company-centric인 기존 data tool과 달리 N개 기업 목록을 반환.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any, Iterable

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.services.contracts import AnalysisStatus, EvidenceRef, SourceType, ToolEnvelope
from open_proxy_mcp.services.date_utils import format_yyyymmdd, resolve_date_window


# market → DART corp_cls
# "all" 은 KOSPI + KOSDAQ 만 포함 (KONEX/기타는 분석 유니버스 제외)
_MARKET_MAP: dict[str, tuple[str, ...]] = {
    "kospi": ("Y",),
    "kosdaq": ("K",),
    "all": ("Y", "K"),
}

_MARKET_LABEL = {"Y": "KOSPI", "K": "KOSDAQ", "N": "KONEX", "E": "기타"}


# 각 event_type의 (pblntf_tys, keywords, strip_spaces, annual_hint)
# annual_hint="annual"이면 "정기주주총회소집공고"만 통과, "extraordinary"면 "임시" 포함만 통과
_EVENT_TYPES: dict[str, dict[str, Any]] = {
    "shareholder_meeting_notice": {
        "pblntf_tys": ("E",),
        "keywords": ("주주총회소집공고",),
        "strip_spaces": True,
        "description": "주주총회 소집공고 (정기/임시 구분은 본문 파싱 필요 — shareholder_meeting tool로 drill-down)",
    },
    "major_shareholder_change": {
        "pblntf_tys": ("I", "B"),
        "keywords": ("최대주주변경",),
        "strip_spaces": True,
        "description": "최대주주 변경 공시",
    },
    "ownership_change_filing": {
        "pblntf_tys": ("I",),
        "keywords": ("최대주주등소유주식변동신고서",),
        "strip_spaces": True,
        "description": "최대주주등소유주식변동신고서 (지분 변동 추적)",
    },
    "block_holding_5pct": {
        "pblntf_tys": ("D",),
        "keywords": ("주식등의대량보유상황보고서", "대량보유상황보고서"),
        "strip_spaces": True,
        "description": "5% 대량보유 상황보고서",
    },
    "executive_ownership": {
        "pblntf_tys": ("D",),
        "keywords": ("임원ㆍ주요주주특정증권등소유상황보고서", "임원·주요주주특정증권등소유상황보고서"),
        "strip_spaces": True,
        "description": "임원·주요주주 특정증권 소유상황보고서",
    },
    "treasury_acquire": {
        "pblntf_tys": ("B", "I"),
        "keywords": ("자기주식취득결정", "자사주취득결정"),
        "strip_spaces": True,
        "description": "자기주식 취득결정",
    },
    "treasury_dispose": {
        "pblntf_tys": ("B", "I"),
        "keywords": ("자기주식처분결정", "자사주처분결정"),
        "strip_spaces": True,
        "description": "자기주식 처분결정",
    },
    "treasury_retire": {
        "pblntf_tys": ("I", "B"),
        "keywords": ("주식소각결정", "자기주식소각결정", "자사주소각결정", "자기주식소각"),
        "strip_spaces": True,
        "description": "자기주식 소각결정 (주식소각결정)",
    },
    "proxy_solicit": {
        "pblntf_tys": ("D",),
        "keywords": ("의결권대리행사권유", "위임장권유참고서류", "의결권대리행사참고서류"),
        "strip_spaces": True,
        "description": "의결권 대리행사 권유 (위임장)",
    },
    "litigation": {
        "pblntf_tys": ("I", "B"),
        "keywords": ("소송등의제기", "소송등의신청", "소송등의판결", "소송등의결정"),
        "strip_spaces": True,
        "description": "소송 제기/판결",
    },
    "management_dispute": {
        "pblntf_tys": ("I", "B"),
        "keywords": ("경영권분쟁소송",),
        "strip_spaces": True,
        "description": "경영권 분쟁 소송",
    },
    "value_up_plan": {
        "pblntf_tys": ("I",),
        "keywords": ("기업가치제고계획", "기업가치제고", "밸류업"),
        "strip_spaces": True,
        "description": "기업가치 제고 계획 (밸류업)",
    },
    "cash_dividend": {
        "pblntf_tys": ("I",),
        "keywords": ("현금ㆍ현물배당결정", "현금·현물배당결정", "현금배당결정", "분기ㆍ중간배당결정", "분기배당결정", "중간배당결정"),
        "strip_spaces": True,
        "description": "현금·현물 배당결정",
    },
    "stock_dividend": {
        "pblntf_tys": ("I",),
        "keywords": ("주식배당결정",),
        "strip_spaces": True,
        "description": "주식 배당결정",
    },
    # --- 희석성 증권 발행 (dilutive_issuance) ---
    "rights_offering": {
        "pblntf_tys": ("B",),
        "keywords": ("유상증자결정",),
        "strip_spaces": True,
        "description": "유상증자 결정 (주주배정/3자배정/일반공모)",
    },
    "convertible_bond": {
        "pblntf_tys": ("B",),
        "keywords": ("전환사채권발행결정", "전환사채발행결정"),
        "strip_spaces": True,
        "description": "전환사채(CB) 발행결정",
    },
    "warrant_bond": {
        "pblntf_tys": ("B",),
        "keywords": ("신주인수권부사채권발행결정", "신주인수권부사채발행결정"),
        "strip_spaces": True,
        "description": "신주인수권부사채(BW) 발행결정",
    },
    "capital_reduction": {
        "pblntf_tys": ("B", "I"),
        "keywords": ("감자결정",),
        "strip_spaces": True,
        "description": "감자 결정 (주식병합, 자본금 감소)",
    },
    # --- 내부거래 (related_party_transaction) ---
    "equity_deal_acquire": {
        "pblntf_tys": ("B", "I"),
        "keywords": ("타법인주식및출자증권양수결정", "타법인주식및출자증권취득결정"),
        "strip_spaces": True,
        "description": "타법인주식·출자증권 양수/취득 결정",
    },
    "equity_deal_dispose": {
        "pblntf_tys": ("B", "I"),
        "keywords": ("타법인주식및출자증권양도결정", "타법인주식및출자증권처분결정"),
        "strip_spaces": True,
        "description": "타법인주식·출자증권 양도/처분 결정",
    },
    "supply_contract_conclude": {
        "pblntf_tys": ("I",),
        "keywords": ("단일판매ㆍ공급계약체결", "단일판매·공급계약체결"),
        "strip_spaces": True,
        "description": "단일판매·공급계약 체결 (매출 5%+ 대형 수주)",
    },
    "supply_contract_terminate": {
        "pblntf_tys": ("I",),
        "keywords": ("단일판매ㆍ공급계약해지", "단일판매·공급계약해지"),
        "strip_spaces": True,
        "description": "단일판매·공급계약 해지",
    },
}


SUPPORTED_EVENT_TYPES = tuple(_EVENT_TYPES.keys())
SUPPORTED_MARKETS = tuple(_MARKET_MAP.keys())


def _name_matches(report_nm: str, keywords: Iterable[str], strip_spaces: bool) -> bool:
    haystack = (report_nm or "").replace(" ", "") if strip_spaces else (report_nm or "")
    return any(kw.replace(" ", "") in haystack if strip_spaces else kw in haystack for kw in keywords)


async def _search_market_wide(
    *,
    bgn_de: str,
    end_de: str,
    pblntf_tys: Iterable[str],
    corp_clses: tuple[str, ...],
    keywords: Iterable[str],
    strip_spaces: bool,
    max_results: int,
    max_pages_per_ty: int = 20,
    page_count: int = 100,
) -> tuple[list[dict[str, Any]], list[str], dict[str, Any], str | None]:
    """기간 내 시장 대상으로 공시 검색 (corp_code 없이).

    각 (corp_cls, pblntf_ty) 조합별로 페이지 순회하며 report_nm 키워드 매칭 수집.
    매칭 수가 max_results 도달하면 중단.

    Returns:
        (matched, warnings, stats, error)
        stats = {"api_calls": int, "truncated": bool, "pages_cut_off": bool}
    """
    client = get_dart_client()
    warnings: list[str] = []
    matched: list[dict[str, Any]] = []
    seen_rcept: set[str] = set()
    api_calls = 0
    truncated = False
    pages_cut_off = False

    for corp_cls in corp_clses:
        for pblntf_ty in pblntf_tys:
            if len(matched) >= max_results:
                truncated = True
                break
            try:
                first = await client.search_filings(
                    bgn_de=bgn_de,
                    end_de=end_de,
                    pblntf_ty=pblntf_ty,
                    corp_cls=corp_cls,
                    page_no=1,
                    page_count=page_count,
                )
                api_calls += 1
            except DartClientError as exc:
                return matched, warnings, {"api_calls": api_calls, "truncated": truncated, "pages_cut_off": pages_cut_off}, exc.status

            items = list(first.get("list", []))
            total_count = int(first.get("total_count", len(items)) or 0)
            total_pages = max(1, math.ceil(total_count / page_count)) if total_count else 1
            fetched_pages = min(total_pages, max_pages_per_ty)

            for page_no in range(2, fetched_pages + 1):
                if len(matched) >= max_results:
                    break
                try:
                    page = await client.search_filings(
                        bgn_de=bgn_de,
                        end_de=end_de,
                        pblntf_ty=pblntf_ty,
                        corp_cls=corp_cls,
                        page_no=page_no,
                        page_count=page_count,
                    )
                    api_calls += 1
                except DartClientError as exc:
                    return matched, warnings, {"api_calls": api_calls, "truncated": truncated, "pages_cut_off": pages_cut_off}, exc.status
                items.extend(page.get("list", []))

            if total_pages > max_pages_per_ty:
                pages_cut_off = True
                market_label = _MARKET_LABEL.get(corp_cls, corp_cls or "전체")
                warnings.append(
                    f"{market_label} {pblntf_ty} 공시가 {total_pages}페이지였으나 {max_pages_per_ty}페이지까지만 봤다 (일부 누락 가능)."
                )

            for item in items:
                if len(matched) >= max_results:
                    truncated = True
                    break
                if not _name_matches(item.get("report_nm", ""), keywords, strip_spaces):
                    continue
                rcept_no = item.get("rcept_no", "")
                if rcept_no in seen_rcept:
                    continue
                seen_rcept.add(rcept_no)
                matched.append(item)

    matched.sort(key=lambda row: (row.get("rcept_dt", ""), row.get("rcept_no", "")), reverse=True)
    stats = {"api_calls": api_calls, "truncated": truncated, "pages_cut_off": pages_cut_off}
    return matched, warnings, stats, None


def _unsupported_event_payload(event_type: str) -> dict[str, Any]:
    return ToolEnvelope(
        tool="screen_events",
        status=AnalysisStatus.ERROR,
        subject=event_type,
        warnings=[f"지원하지 않는 event_type: `{event_type}`"],
        data={
            "event_type": event_type,
            "supported_event_types": list(SUPPORTED_EVENT_TYPES),
        },
    ).to_dict()


def _unsupported_market_payload(market: str) -> dict[str, Any]:
    return ToolEnvelope(
        tool="screen_events",
        status=AnalysisStatus.ERROR,
        subject=market,
        warnings=[f"지원하지 않는 market: `{market}`"],
        data={
            "market": market,
            "supported_markets": list(SUPPORTED_MARKETS),
        },
    ).to_dict()


async def build_screen_events_payload(
    *,
    event_type: str,
    start_date: str = "",
    end_date: str = "",
    market: str = "all",
    max_results: int = 50,
) -> dict[str, Any]:
    if event_type not in _EVENT_TYPES:
        return _unsupported_event_payload(event_type)
    if market not in _MARKET_MAP:
        return _unsupported_market_payload(market)

    max_results = max(1, min(max_results, 100))

    window_start, window_end, window_warnings = resolve_date_window(
        start_date=start_date,
        end_date=end_date,
        default_end=date.today(),
        lookback_months=1,
    )
    # 시장 전수 검색은 응답량이 커서 기본 1개월 lookback.
    # start_date/end_date 미지정시 resolve_date_window가 lookback_months=1로 30일 반환.

    bgn_de = format_yyyymmdd(window_start)
    end_de = format_yyyymmdd(window_end)
    corp_clses = _MARKET_MAP[market]

    config = _EVENT_TYPES[event_type]
    warnings = list(window_warnings)

    matched, search_warnings, stats, error = await _search_market_wide(
        bgn_de=bgn_de,
        end_de=end_de,
        pblntf_tys=config["pblntf_tys"],
        corp_clses=corp_clses,
        keywords=config["keywords"],
        strip_spaces=config.get("strip_spaces", False),
        max_results=max_results,
    )
    warnings.extend(search_warnings)

    usage = {
        "dart_api_calls": stats.get("api_calls", 0),
        "mcp_tool_calls": 1,
        "dart_daily_limit_per_minute": 1000,
    }

    if error:
        return ToolEnvelope(
            tool="screen_events",
            status=AnalysisStatus.ERROR,
            subject=event_type,
            warnings=[f"DART 검색 실패: {error}", *warnings],
            data={
                "event_type": event_type,
                "market": market,
                "window": {"start_date": bgn_de, "end_date": end_de},
                "usage": usage,
            },
        ).to_dict()

    if stats.get("truncated"):
        warnings.append(
            f"결과가 max_results({max_results})에 도달해 중단 — 기간 내 추가 매칭이 있을 수 있다. 기간을 좁히거나 max_results를 올려라."
        )

    rows: list[dict[str, Any]] = []
    for item in matched:
        cls = item.get("corp_cls", "")
        rcept_no = item.get("rcept_no", "")
        rows.append({
            "corp_name": item.get("corp_name", ""),
            "ticker": item.get("stock_code", ""),
            "corp_code": item.get("corp_code", ""),
            "corp_cls": cls,
            "market": _MARKET_LABEL.get(cls, cls or "미상"),
            "report_nm": item.get("report_nm", ""),
            "rcept_dt": item.get("rcept_dt", ""),
            "rcept_no": rcept_no,
            "dart_viewer": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rcept_no}" if rcept_no else "",
        })

    status = AnalysisStatus.EXACT if rows else AnalysisStatus.PARTIAL
    if not rows:
        warnings.append("조사 구간 내 매칭 공시 없음")

    data = {
        "event_type": event_type,
        "event_description": config["description"],
        "market": market,
        "window": {"start_date": bgn_de, "end_date": end_de},
        "max_results": max_results,
        "result_count": len(rows),
        "results": rows,
        "usage": usage,
        "supported_event_types": list(SUPPORTED_EVENT_TYPES),
    }

    evidence_refs: list[EvidenceRef] = [
        EvidenceRef(
            evidence_id=f"ev_screen_{event_type}_{bgn_de}_{end_de}_{market}",
            source_type=SourceType.DART_API,
            section="list.json",
            note=f"{config['description']} / {bgn_de}~{end_de} / market={market} / matched={len(rows)}",
        )
    ]

    return ToolEnvelope(
        tool="screen_events",
        status=status,
        subject=event_type,
        warnings=warnings,
        data=data,
        evidence_refs=evidence_refs,
        next_actions=[
            "개별 기업 drill-down은 shareholder_meeting/ownership_structure/proxy_contest 등 기존 data tool 사용",
        ],
    ).to_dict()
