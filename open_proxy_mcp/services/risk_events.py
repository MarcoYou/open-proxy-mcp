"""risk_events data tool.

기업 리스크 이벤트 공시 통합 — 중대재해 / 횡령·배임 / 파생상품거래손실 /
회생절차·부도 / 생산중단·영업정지 / 해산. 본사·종속/자회사 변형 포함.

채널: I001(거래소 주요경영사항) + B001(주요사항보고서) 동시 조회.
- 중대재해·횡령배임·파생손실·생산중단 → I001 전용 (B001 90일 0건 실측)
- 회생절차개시'신청'·부도·영업정지·해산 → B001 (개시'결정' 등 법원발은 I001)
검증: 305사 × 3.5년 I 전체 대비 I001 차집합 0 (중대재해, 2026-06-11) +
시장 90일 B001/I001/I003 전수 sweep으로 카테고리·채널 매핑 확정.

시장 전체 스캔: 공시가 희소해 per-company보다 "최근 누가 냈나"가 실질 수요.
실측 30일 I001 36페이지 + B001 7페이지 ≈ 45콜.

주의: 거래소 중대재해 수시공시는 2025-10월부터 관측(최초 2025-10-29 실측).
그 이전 구간의 무공시는 무사고를 의미하지 않는다.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date, timedelta
from typing import Any

from bs4 import BeautifulSoup

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.services.company import _company_id, resolve_company_query
from open_proxy_mcp.services.company import company_not_found_warning
from open_proxy_mcp.services.contracts import (
    AnalysisStatus,
    EvidenceRef,
    SourceType,
    ToolEnvelope,
    build_filing_meta,
    status_from_filing_meta,
)
from open_proxy_mcp.services.date_utils import format_iso_date, format_yyyymmdd, resolve_date_window

# ── 카테고리 정의 ──────────────────────────────────────────────
# keywords: report_nm 부분 매칭 (공백 제거 후). 순서 = 분류 우선순위.
_CATEGORIES: dict[str, dict[str, Any]] = {
    "serious_accident": {"label": "중대재해", "keywords": ("중대재해",)},
    "embezzlement": {"label": "횡령·배임", "keywords": ("횡령",)},
    "derivative_loss": {"label": "파생상품손실", "keywords": ("파생상품거래손실",)},
    "rehabilitation": {"label": "회생·부도", "keywords": ("회생절차", "부도", "은행거래정지")},
    "production_halt": {"label": "생산중단·영업정지", "keywords": ("생산중단", "영업정지")},
    "dissolution": {"label": "해산", "keywords": ("해산사유",)},
}

# 활성 스콥 — 기본(category 미지정) 조회는 이 3종만. 나머지는 mute:
# 파서·검증(359건)은 완료된 채 보존, 명시적 category 요청 시에만 동작 (2026-06-11 결정).
_ACTIVE_CATEGORIES = ("serious_accident", "embezzlement", "production_halt")
_MUTED_CATEGORIES = tuple(c for c in _CATEGORIES if c not in _ACTIVE_CATEGORIES)

_ALL_KEYWORDS = tuple(kw for cat in _ACTIVE_CATEGORIES for kw in _CATEGORIES[cat]["keywords"])

# 단계 추출 — 카테고리 공통 (제목 내 표지 우선순위)
_STAGE_MARKERS = (
    ("처벌", "처벌확인"),
    ("혐의발생", "혐의발생"),
    ("진행사항", "진행사항"),
    ("사실확인", "사실확인"),
    ("개시신청", "개시신청"),
    ("개시결정", "개시결정"),
    ("폐지", "폐지"),
    ("종결", "종결"),
    ("부도", "부도"),
    ("거래정지", "거래정지"),
    ("해제", "해제"),
    ("발생", "발생"),
)

# 수시공시 항목 신설 시점 (중대재해 — 실측 최초 공시 2025-10-29)
_DISCLOSURE_REGIME_START = "20251001"

_MARKET_SCAN_DEFAULT_DAYS = 30
_MARKET_SCAN_MAX_DAYS = 90
_MARKET_SCAN_PAGE_CAP = 200


def _classify(report_nm: str) -> tuple[str, str]:
    """report_nm → (category, stage). 미매칭 시 ('', '')."""
    compact = (report_nm or "").replace(" ", "")
    for cat, cfg in _CATEGORIES.items():
        if any(kw in compact for kw in cfg["keywords"]):
            for marker, stage in _STAGE_MARKERS:
                if marker in compact:
                    return cat, stage
            return cat, "기타"
    return "", ""


def _is_subsidiary_report(report_nm: str) -> bool:
    compact = (report_nm or "").replace(" ", "")
    return "종속회사" in compact or "자회사" in compact


def _extract_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "lxml")
    return soup.get_text("\n", strip=True)


def _find_value_after(lines: list[str], label: str, max_distance: int = 3) -> str:
    for i, line in enumerate(lines):
        if label in line:
            for j in range(1, max_distance + 1):
                if i + j < len(lines):
                    v = lines[i + j].strip()
                    if v and not v.endswith(":") and len(v) < 300:
                        return v
    return ""


def _find_int_near(text: str, label: str) -> int | None:
    m = re.search(re.escape(label) + r"[^\d]{0,20}(\d+)", text)
    return int(m.group(1)) if m else None


def _find_amount_near(text: str, label: str) -> str:
    m = re.search(re.escape(label) + r"[^\d]{0,30}(\d{1,3}(?:,\d{3})+|\d+)", text)
    return m.group(1) if m else ""


def _find_pct_near(text: str, label: str) -> str:
    m = re.search(re.escape(label) + r"[^\d]{0,30}(\d+(?:\.\d+)?)", text)
    return m.group(1) if m else ""


def _find_block_after(text: str, label: str, stop_labels: tuple[str, ...], max_len: int = 500) -> str:
    i = text.find(label)
    if i < 0:
        return ""
    block = text[i + len(label):]
    for stop in stop_labels:
        j = block.find(stop)
        if j >= 0:
            block = block[:j]
    return re.sub(r"\s+", " ", block).strip(" -:·").strip()[:max_len]


# ── 카테고리별 원문 파서 ────────────────────────────────────────

def _parse_serious_accident(text: str, lines: list[str], stage: str) -> dict[str, Any]:
    d: dict[str, Any] = {}
    subsidiary_name = (
        _find_value_after(lines, "종속회사명", 2)
        or _find_value_after(lines, "자회사명", 2)
        or _find_value_after(lines, "법인명", 2)
    )
    if subsidiary_name:
        d["subsidiary_name"] = subsidiary_name
    if stage == "처벌확인":
        d["confirmed_date"] = _find_value_after(lines, "확인일자", 2) or _find_value_after(lines, "판결일", 2)
        d["summary_excerpt"] = re.sub(r"\s+", " ", text)[:400]
        return d
    d["location"] = _find_value_after(lines, "발생 장소", 2) or _find_value_after(lines, "발생장소", 2)
    d["description"] = _find_block_after(text, "재해 내용", ("사망자", "부상자", "2."), max_len=400) \
        or _find_block_after(text, "재해내용", ("사망자", "부상자", "2."), max_len=400)
    d["deaths"] = _find_int_near(text, "사망자 수") or _find_int_near(text, "사망자수") or 0
    d["injuries"] = _find_int_near(text, "부상자 수") or _find_int_near(text, "부상자수") or 0
    d["accident_date"] = _find_value_after(lines, "발생일자", 2) or _find_value_after(lines, "발생 일자", 2)
    d["labor_ministry_report_date"] = _find_value_after(lines, "고용노동부 보고일자", 2)
    d["response_plan"] = _find_block_after(text, "조치사항 및 향후대책", ("5.", "기타 투자판단"), max_len=400)
    return d


def _parse_embezzlement(text: str, lines: list[str], stage: str) -> dict[str, Any]:
    return {
        "suspect": _find_value_after(lines, "혐의자", 2) or _find_value_after(lines, "고소대상자", 2),
        "amount_won": _find_amount_near(text, "혐의발생금액") or _find_amount_near(text, "횡령등 금액") or _find_amount_near(text, "금액"),
        "equity_ratio_pct": _find_pct_near(text, "자기자본대비") or _find_pct_near(text, "자기자본 대비"),
        "summary_excerpt": re.sub(r"\s+", " ", text)[:300],
    }


def _parse_derivative_loss(text: str, lines: list[str], stage: str) -> dict[str, Any]:
    return {
        # 양식 라벨 = "손실누계잔액(원)(기신고분 제외)" 또는 "손실발생금액(원)" (SK하이닉스형, 180일 62건 실측)
        "loss_amount_won": _find_amount_near(text, "손실누계잔액") or _find_amount_near(text, "손실발생금액") or _find_amount_near(text, "손실누계액") or _find_amount_near(text, "거래손실액") or _find_amount_near(text, "손실액"),
        "equity_ratio_pct": _find_pct_near(text, "자기자본대비") or _find_pct_near(text, "자기자본 대비"),
        "summary_excerpt": re.sub(r"\s+", " ", text)[:300],
    }


def _parse_production_halt(text: str, lines: list[str], stage: str) -> dict[str, Any]:
    return {
        # 생산중단 양식 = "생산중단분야", 영업정지 양식 = "영업정지 분야" (90일 27건 실측)
        "halted_business": _find_value_after(lines, "생산중단분야", 2) or _find_value_after(lines, "생산중단 분야", 2)
        or _find_value_after(lines, "영업정지 분야", 2) or _find_value_after(lines, "영업정지분야", 2)
        or _find_value_after(lines, "중단(정지)된", 3) or _find_value_after(lines, "중단내용", 2),
        "revenue_ratio_pct": _find_pct_near(text, "매출액대비") or _find_pct_near(text, "매출액 대비"),
        "reason": _find_block_after(text, "생산중단사유", ("5.", "향후")) or _find_block_after(text, "영업정지사유", ("5.", "향후"))
        or _find_block_after(text, "중단사유", ("3.", "향후")) or _find_block_after(text, "정지사유", ("3.", "향후")),
        "summary_excerpt": re.sub(r"\s+", " ", text)[:300],
    }


def _parse_rehabilitation(text: str, lines: list[str], stage: str) -> dict[str, Any]:
    event_date = (
        _find_value_after(lines, "신청일자", 2) or _find_value_after(lines, "결정일자", 2)
        or _find_value_after(lines, "최종부도(당좌거래정지)일자", 2) or _find_value_after(lines, "발생일자", 2)
    )
    court = _find_value_after(lines, "관할법원", 2) or _find_value_after(lines, "법원", 2)
    # 부도발생 양식엔 법원 필드가 없어 느슨한 "법원" 라벨이 본문 문장을 잡을 수 있음 — 법원명 형태만 채택
    if court and not ("법원" in court and len(court) < 40):
        court = ""
    return {
        "court": court,
        "event_date": "" if event_date == "-" else event_date,
        "amount_won": _find_amount_near(text, "부도금액"),  # 부도발생 양식만 존재
        "summary_excerpt": re.sub(r"\s+", " ", text)[:300],
    }


def _parse_generic(text: str, lines: list[str], stage: str) -> dict[str, Any]:
    return {"summary_excerpt": re.sub(r"\s+", " ", text)[:400]}


_PARSERS = {
    "serious_accident": _parse_serious_accident,
    "embezzlement": _parse_embezzlement,
    "derivative_loss": _parse_derivative_loss,
    "production_halt": _parse_production_halt,
    "rehabilitation": _parse_rehabilitation,
    "dissolution": _parse_generic,
}


def _parse_document(html: str, category: str, stage: str) -> dict[str, Any]:
    text = _extract_text(html)
    # 일부 거래소 채널 공시(예: 영업정지(종속회사)) 본문이 빈 문서 — parsing failure로 집계
    if len(text.strip()) < 30:
        return {}
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    parser = _PARSERS.get(category, _parse_generic)
    return parser(text, lines, stage)


async def _enrich_with_document_details(
    rows: list[dict[str, Any]],
    max_docs: int = 5,
) -> tuple[list[dict[str, Any]], list[str], int]:
    client = get_dart_client()
    warnings: list[str] = []
    targets = [row for row in rows[:max_docs] if row.get("rcept_no")]
    if not targets:
        return rows, warnings, 0

    async def _safe_fetch(rcept_no: str) -> tuple[str, str | None]:
        try:
            doc = await client.get_document_cached(rcept_no)
            return (doc.get("html", "") if isinstance(doc, dict) else ""), None
        except DartClientError as exc:
            return "", f"원문 조회 실패 ({rcept_no}): {exc.status}"
        except Exception as exc:
            return "", f"원문 파싱 실패 ({rcept_no}): {exc}"

    results = await asyncio.gather(*[_safe_fetch(row["rcept_no"]) for row in targets])
    doc_calls = 0
    for row, (html, err) in zip(targets, results):
        if err:
            warnings.append(err)
            continue
        doc_calls += 1
        if html:
            row["details"] = _parse_document(html, row.get("category", ""), row.get("stage", ""))
    return rows, warnings, doc_calls


def _normalize_location(loc: str) -> str:
    """이중 공시(지주사 vs 사업회사)의 장소 표기 미세 차이 흡수 —
    '㈜' vs '(주)', 공백 차이로 supersede 키가 갈라지는 것 방지 (한화/한화에어로 실측)."""
    s = (loc or "").replace("㈜", "(주)").replace("（주）", "(주)")
    return re.sub(r"\s+", "", s)


def _aggregate_casualties(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """중대재해 발생 공시의 사상자 합계. 같은 사건(발생일자+장소 정규화)의 원본·정정·
    지주사/사업회사 이중 공시는 최신 공시(rcept_no 최대)가 대체(supersede)."""
    accident_latest: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        d = r.get("details") or {}
        if d and r.get("category") == "serious_accident" and r.get("stage") == "발생":
            key = (d.get("accident_date") or r.get("rcept_dt", ""), _normalize_location(d.get("location") or ""))
            prev = accident_latest.get(key)
            if prev is None or r.get("rcept_no", "") > prev.get("rcept_no", ""):
                accident_latest[key] = r
    deaths = injuries = 0
    for r in accident_latest.values():
        d = r.get("details") or {}
        deaths += d.get("deaths") or 0
        injuries += d.get("injuries") or 0
    return {
        "deaths": deaths,
        "injuries": injuries,
        "parsed_rows": len(accident_latest),
        "비고": "include_details=True로 파싱된 중대재해 발생 공시만 합산 — 같은 사건의 정정·지주/사업회사 이중 공시는 최신 공시로 대체",
    }


def _row_from_item(item: dict[str, Any], category: str, stage: str) -> dict[str, Any]:
    report_nm = (item.get("report_nm") or "").strip()
    return {
        "category": category,
        "category_label": _CATEGORIES.get(category, {}).get("label", category),
        "stage": stage,
        "rcept_no": item.get("rcept_no", ""),
        "rcept_dt": item.get("rcept_dt", ""),
        "report_nm": report_nm,
        "filer_name": item.get("flr_nm", ""),
        "subsidiary_report": _is_subsidiary_report(report_nm),
        "is_correction": report_nm.startswith("[기재정정]"),
    }


def _event_counts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    counts: dict[str, Any] = {"total": len(rows)}
    for cat in _CATEGORIES:
        counts[cat] = sum(1 for r in rows if r.get("category") == cat)
    counts["subsidiary_reports"] = sum(1 for r in rows if r.get("subsidiary_report"))
    counts["corrections"] = sum(1 for r in rows if r.get("is_correction"))
    return counts


def _category_filter_keywords(category: str) -> tuple[str, ...]:
    if category and category in _CATEGORIES:
        return _CATEGORIES[category]["keywords"]
    return _ALL_KEYWORDS


def _invalid_category_payload(company_query: str, category: str) -> dict[str, Any]:
    return ToolEnvelope(
        tool="risk_events",
        status=AnalysisStatus.REQUIRES_REVIEW,
        subject=company_query or "시장 전체",
        warnings=[f"`{category}` category 미지원."],
        data={"query": company_query, "category": category, "supported_categories": list(_ACTIVE_CATEGORIES), "muted_categories": list(_MUTED_CATEGORIES)},
    ).to_dict()


async def _build_market_scan_payload(
    *,
    category: str = "",
    start_date: str = "",
    end_date: str = "",
    include_details: bool = False,
    details_limit: int = 5,
) -> dict[str, Any]:
    """회사 미지정 — 시장 전체 최근 리스크 공시 스캔 (기본 30일, 최대 90일)."""
    warnings: list[str] = []
    if category in _MUTED_CATEGORIES:
        warnings.append(f"`{category}`는 mute 상태 카테고리 — 기본 스캔에선 제외되며 명시 요청으로만 조회된다.")
    end = date.today()
    if end_date:
        try:
            end = date(int(end_date[:4]), int(end_date[4:6]), int(end_date[6:8]))
        except ValueError:
            warnings.append(f"end_date '{end_date}' 형식 오류 — 오늘로 대체.")
    start = end - timedelta(days=_MARKET_SCAN_DEFAULT_DAYS)
    if start_date:
        try:
            start = date(int(start_date[:4]), int(start_date[4:6]), int(start_date[6:8]))
        except ValueError:
            warnings.append(f"start_date '{start_date}' 형식 오류 — 기본 {_MARKET_SCAN_DEFAULT_DAYS}일로 대체.")
    if (end - start).days > _MARKET_SCAN_MAX_DAYS:
        start = end - timedelta(days=_MARKET_SCAN_MAX_DAYS)
        warnings.append(f"시장 전체 스캔은 최대 {_MARKET_SCAN_MAX_DAYS}일 — 구간을 {start:%Y%m%d}~ 로 줄였다. 특정 회사의 긴 이력은 company 지정.")
    bgn_de, end_de = f"{start:%Y%m%d}", f"{end:%Y%m%d}"

    client = get_dart_client()
    rows: list[dict[str, Any]] = []
    api_calls = 0
    keywords = _category_filter_keywords(category)

    async def _sweep(detail_ty: str) -> tuple[list[dict[str, Any]], int]:
        first = await client.search_filings(bgn_de=bgn_de, end_de=end_de, pblntf_detail_ty=detail_ty, page_no=1)
        calls = 1
        total_page = min(int(first.get("total_page") or 1), _MARKET_SCAN_PAGE_CAP)
        pages = [first]
        if total_page > 1:
            rest = await asyncio.gather(*[
                client.search_filings(bgn_de=bgn_de, end_de=end_de, pblntf_detail_ty=detail_ty, page_no=p)
                for p in range(2, total_page + 1)
            ], return_exceptions=True)
            for r in rest:
                calls += 1
                if isinstance(r, Exception):
                    warnings.append(f"시장 스캔({detail_ty}) 페이지 일부 실패: {r}")
                else:
                    pages.append(r)
        items = []
        for pg in pages:
            items.extend(pg.get("list", []) or [])
        return items, calls

    try:
        # I001(거래소 주요경영사항) + B001(주요사항보고서 — 회생신청/부도/영업정지/해산) 양 채널
        (i_items, i_calls), (b_items, b_calls) = await asyncio.gather(_sweep("I001"), _sweep("B001"))
        api_calls += i_calls + b_calls
        for item in i_items + b_items:
            nm = (item.get("report_nm") or "").replace(" ", "")
            if not any(kw in nm for kw in keywords):
                continue
            cat, stage = _classify(item.get("report_nm") or "")
            if not cat or (category and cat != category) or (not category and cat not in _ACTIVE_CATEGORIES):
                continue
            row = _row_from_item(item, cat, stage)
            row["corp_name"] = item.get("corp_name", "")
            row["stock_code"] = item.get("stock_code", "")
            rows.append(row)
    except DartClientError as exc:
        warnings.append(f"시장 전체 스캔 실패: {exc.status}")
    rows.sort(key=lambda r: (r.get("rcept_dt", ""), r.get("rcept_no", "")), reverse=True)

    if include_details and rows:
        rows, detail_warnings, doc_calls = await _enrich_with_document_details(rows, max_docs=details_limit)
        warnings.extend(detail_warnings)
        api_calls += doc_calls

    by_company: dict[str, int] = {}
    for r in rows:
        nm = r.get("corp_name") or r.get("filer_name") or "?"
        by_company[nm] = by_company.get(nm, 0) + 1

    filing_meta = build_filing_meta(filing_count=len(rows), parsing_failures=0)
    data: dict[str, Any] = {
        "mode": "market_scan",
        "category": category or "all",
        "casualties": _aggregate_casualties(rows) if include_details else None,
        "window": {"start_date": bgn_de, "end_date": end_de},
        "event_count": _event_counts(rows),
        "by_company": dict(sorted(by_company.items(), key=lambda x: -x[1])),
        "events": rows,
        **filing_meta,
        "usage": {"dart_api_calls": api_calls, "mcp_tool_calls": 1, "dart_daily_limit_per_minute": 1000},
        "supported_categories": list(_ACTIVE_CATEGORIES), "muted_categories": list(_MUTED_CATEGORIES),
    }
    if filing_meta["no_filing"]:
        warnings.append(f"조사 구간 ({bgn_de}~{end_de}) 내 시장 전체 리스크 공시 없음.")
    warnings.append("공시는 대형 원청·지주사에 집중된다 — 중소형사·하청의 무공시는 무사고·무사건 단정 불가 (산재 통계 정본은 고용노동부).")

    return ToolEnvelope(
        tool="risk_events",
        status=status_from_filing_meta(filing_meta),
        subject="시장 전체 리스크 이벤트 공시",
        warnings=warnings,
        data=data,
        evidence_refs=[
            EvidenceRef(
                evidence_id=f"ev_risk_{r['rcept_no']}",
                source_type=SourceType.DART_API,
                rcept_no=r["rcept_no"],
                rcept_dt=format_iso_date(r.get("rcept_dt", "")),
                report_nm=r.get("report_nm", ""),
                section="list.json I001+B001 market scan",
                note=f"{r.get('category', '')}/{r.get('stage', '')}",
            )
            for r in rows[:5] if r.get("rcept_no")
        ],
    ).to_dict()


async def build_risk_events_payload(
    company_query: str,
    *,
    category: str = "",
    start_date: str = "",
    end_date: str = "",
    include_details: bool = False,
    details_limit: int = 5,
) -> dict[str, Any]:
    from open_proxy_mcp.services.filing_search import search_filings_by_report_name

    if category and category not in _CATEGORIES:
        return _invalid_category_payload(company_query, category)

    # 회사 미지정 → 시장 전체 최근 스캔
    if not (company_query or "").strip():
        return await _build_market_scan_payload(
            category=category,
            start_date=start_date,
            end_date=end_date,
            include_details=include_details,
            details_limit=details_limit,
        )

    resolution = await resolve_company_query(company_query)
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        return ToolEnvelope(
            tool="risk_events",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[
                company_not_found_warning(company_query),
                "비상장 자회사(예: 포스코이앤씨)의 리스크 공시는 상장 모회사가 '(종속/자회사의 주요경영사항)'으로 공시한다 — 상장 모회사명으로 조회할 것.",
            ],
            data={"query": company_query},
            next_actions=["company tool로 회사 식별 확인", "비상장사면 상장 모회사로 재조회"],
        ).to_dict()
    if resolution.status == AnalysisStatus.AMBIGUOUS:
        return ToolEnvelope(
            tool="risk_events",
            status=AnalysisStatus.AMBIGUOUS,
            subject=company_query,
            warnings=["회사 식별이 애매해 자동 선택하지 않았다."],
            data={
                "query": company_query,
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
    window_start, window_end, window_warnings = resolve_date_window(
        start_date=start_date,
        end_date=end_date,
        default_end=date.today(),
        lookback_months=24,
    )
    bgn_de = format_yyyymmdd(window_start)
    end_de = format_yyyymmdd(window_end)

    warnings = list(window_warnings)
    if category in _MUTED_CATEGORIES:
        warnings.append(f"`{category}`는 mute 상태 카테고리 — 기본 조회에선 제외되며 명시 요청으로만 조회된다.")
    keywords = _category_filter_keywords(category)

    items, notices, error = await search_filings_by_report_name(
        corp_code=selected["corp_code"],
        bgn_de=bgn_de,
        end_de=end_de,
        pblntf_tys="",
        pblntf_detail_ty=["I001", "B001"],  # 거래소 주요경영사항 + 주요사항보고서 양 채널
        keywords=keywords,
        strip_spaces=True,
    )
    warnings.extend(notices)
    total_api_calls = 2
    if error:
        warnings.append(f"리스크 공시 조회 실패: {error}")

    rows: list[dict[str, Any]] = []
    for item in items or []:
        cat, stage = _classify(item.get("report_nm") or "")
        if not cat or (category and cat != category) or (not category and cat not in _ACTIVE_CATEGORIES):
            continue
        rows.append(_row_from_item(item, cat, stage))
    rows.sort(key=lambda row: (row.get("rcept_dt", ""), row.get("rcept_no", "")), reverse=True)

    if include_details and rows:
        rows, detail_warnings, doc_calls = await _enrich_with_document_details(rows, max_docs=details_limit)
        warnings.extend(detail_warnings)
        total_api_calls += doc_calls

    parsing_failures = 0
    if include_details:
        for row in rows[:details_limit]:
            if not (row.get("details") or {}):
                parsing_failures += 1
    filing_meta = build_filing_meta(filing_count=len(rows), parsing_failures=parsing_failures)

    data: dict[str, Any] = {
        "mode": "company",
        "query": company_query,
        "company_id": _company_id(selected),
        "canonical_name": selected.get("corp_name", ""),
        "identifiers": {
            "ticker": selected.get("stock_code", ""),
            "corp_code": selected.get("corp_code", ""),
        },
        "category": category or "all",
        "window": {"start_date": bgn_de, "end_date": end_de},
        "event_count": _event_counts(rows),
        "casualties": _aggregate_casualties(rows) if include_details else None,
        "events": rows,
        **filing_meta,
        "usage": {"dart_api_calls": total_api_calls, "mcp_tool_calls": 1, "dart_daily_limit_per_minute": 1000},
        "supported_categories": list(_ACTIVE_CATEGORIES), "muted_categories": list(_MUTED_CATEGORIES),
    }

    evidence_refs: list[EvidenceRef] = []
    for row in rows[:5]:
        rcept_no = row.get("rcept_no", "")
        if rcept_no:
            evidence_refs.append(
                EvidenceRef(
                    evidence_id=f"ev_risk_{rcept_no}",
                    source_type=SourceType.DART_API,
                    rcept_no=rcept_no,
                    rcept_dt=format_iso_date(row.get("rcept_dt", "")),
                    report_nm=row.get("report_nm", ""),
                    section="list.json I001+B001 + keyword",
                    note=f"{row.get('category', '')}/{row.get('stage', '')}",
                )
            )

    status = status_from_filing_meta(filing_meta)
    if filing_meta["no_filing"]:
        warnings.append(f"조사 구간 ({bgn_de}~{end_de}) 내 리스크 이벤트 공시 없음.")
    elif filing_meta["parsing_failures"] > 0:
        warnings.append(f"원문 파싱 실패 {filing_meta['parsing_failures']}건 — details 필드 비어 있음")

    # 제도 시점 안내 — 중대재해 수시공시 신설(2025-10) 이전 구간 포함 시
    if (not category or category == "serious_accident") and bgn_de < _DISCLOSURE_REGIME_START:
        warnings.append(
            "거래소 중대재해 수시공시는 2025-10월부터 관측된다 (실측 최초 2025-10-29). "
            "그 이전 구간의 무공시는 무사고를 의미하지 않는다 — 과거 이력은 뉴스·고용노동부 자료로 보완할 것."
        )

    return ToolEnvelope(
        tool="risk_events",
        status=status,
        subject=selected.get("corp_name", company_query),
        warnings=warnings,
        data=data,
        evidence_refs=evidence_refs,
    ).to_dict()
