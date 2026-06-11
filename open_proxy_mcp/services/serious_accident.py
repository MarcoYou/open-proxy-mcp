"""v2 serious_accident data tool.

중대재해 공시 통합 — 중대재해발생(본사/종속·자회사) + 중대재해 관련 (형사)처벌사실확인.
중대재해처벌법(2022) 리스크 모니터링 소스.

DART 전용 구조화 API가 없어 list.json + report_nm 키워드 매칭 방식.
검색은 pblntf_detail_ty=I001(주요경영사항)로 좁힘 — 305사(고위험 49 +
KOSPI100·KOSDAQ100 + 건설·전문건설·설계/CM 56) 3.5년 'I 전체' 대비 차집합 0
+ truncation 0 + 전체유형 누수 0 검증 (2026-06-11).

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
from open_proxy_mcp.services.contracts import (
    AnalysisStatus,
    EvidenceRef,
    SourceType,
    ToolEnvelope,
    build_filing_meta,
    status_from_filing_meta,
)
from open_proxy_mcp.services.date_utils import format_iso_date, format_yyyymmdd, resolve_date_window

# 단일 키워드가 변형 5종 모두 매칭 (49사 실측):
# 중대재해발생 / 중대재해발생(종속회사의주요경영사항) / 중대재해발생(자회사의 주요경영사항)
# / [기재정정] 변형 / 중대재해관련(형사)처벌사실확인 계열
_ACCIDENT_KEYWORDS = ("중대재해",)

# 수시공시 항목 신설 시점 (실측 최초 공시 2025-10-29)
_DISCLOSURE_REGIME_START = "20251001"

# 시장 전체 스캔 — 공시가 희소(305사 중 27사)해 per-company보다 "최근 누가 냈나"가
# 실질 수요. corp_code 없이 I001 전체 조회: 실측 30일=36페이지, 90일=162페이지.
_MARKET_SCAN_DEFAULT_DAYS = 30
_MARKET_SCAN_MAX_DAYS = 90
_MARKET_SCAN_PAGE_CAP = 200


def _classify_event(report_nm: str) -> str:
    compact = (report_nm or "").replace(" ", "")
    if "처벌" in compact:
        return "punishment"  # (형사)처벌사실확인
    if "발생" in compact:
        return "occurrence"  # 중대재해발생
    return "unknown"


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


def _find_block_after(text: str, label: str, stop_labels: tuple[str, ...], max_len: int = 500) -> str:
    """라벨 이후 다음 라벨 전까지의 본문 블록 추출 (조치사항·재해내용 등 서술형)."""
    i = text.find(label)
    if i < 0:
        return ""
    block = text[i + len(label):]
    for stop in stop_labels:
        j = block.find(stop)
        if j >= 0:
            block = block[:j]
    return re.sub(r"\s+", " ", block).strip(" -:·").strip()[:max_len]


def _parse_accident_document(html: str, event_type: str) -> dict[str, Any]:
    """중대재해발생/처벌사실확인 원문 파싱.

    발생 공시 정형 필드 (실측 DL이앤씨 본문 기준):
    발생 장소 / 발생 재해 내용 / 사망자 수 / 부상자 수 /
    2. 중대재해 발생일자 / 3. 고용노동부 보고일자 / 4. 조치사항 및 향후대책
    종속·자회사 변형은 회사명 필드 추가.
    """
    text = _extract_text(html)
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    details: dict[str, Any] = {}

    # 종속·자회사 변형 — 대상 회사명
    subsidiary_name = (
        _find_value_after(lines, "종속회사명", 2)
        or _find_value_after(lines, "자회사명", 2)
        or _find_value_after(lines, "법인명", 2)
    )
    if subsidiary_name:
        details["subsidiary_name"] = subsidiary_name

    if event_type == "punishment":
        # 처벌확인 공시는 아직 표본 0건 — 일반 필드 + 본문 발췌로 보수 파싱
        details["confirmed_date"] = (
            _find_value_after(lines, "확인일자", 2) or _find_value_after(lines, "판결일", 2)
        )
        details["summary_excerpt"] = re.sub(r"\s+", " ", text)[:400]
        return details

    details["location"] = _find_value_after(lines, "발생 장소", 2) or _find_value_after(lines, "발생장소", 2)
    details["description"] = _find_block_after(
        text, "재해 내용", ("사망자", "부상자", "2."), max_len=400
    ) or _find_block_after(text, "재해내용", ("사망자", "부상자", "2."), max_len=400)
    details["deaths"] = _find_int_near(text, "사망자 수") or _find_int_near(text, "사망자수") or 0
    details["injuries"] = _find_int_near(text, "부상자 수") or _find_int_near(text, "부상자수") or 0
    details["accident_date"] = (
        _find_value_after(lines, "발생일자", 2) or _find_value_after(lines, "발생 일자", 2)
    )
    details["labor_ministry_report_date"] = _find_value_after(lines, "고용노동부 보고일자", 2)
    details["response_plan"] = _find_block_after(
        text, "조치사항 및 향후대책", ("5.", "기타 투자판단"), max_len=400
    )
    return details


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
            row["details"] = _parse_accident_document(html, row.get("event_type", ""))
    return rows, warnings, doc_calls


def _normalize_location(loc: str) -> str:
    """이중 공시(지주사 vs 사업회사)의 장소 표기 미세 차이 흡수 —
    '㈜' vs '(주)', 공백 차이로 supersede 키가 갈라지는 것 방지 (한화/한화에어로 실측)."""
    s = (loc or "").replace("㈜", "(주)").replace("（주）", "(주)")
    return re.sub(r"\s+", "", s)


def _aggregate_casualties(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """파싱된 발생 공시의 사상자 합계. 같은 사건(발생일자+장소)의 원본·정정·
    지주사/사업회사 이중 공시는 최신 공시(rcept_no 최대)가 대체(supersede)."""
    accident_latest: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        d = r.get("details") or {}
        if d and r.get("event_type") == "occurrence":
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
        "note": "include_details=True로 파싱된 발생 공시만 합산 — 같은 사건의 정정·지주/사업회사 이중 공시는 최신 공시로 대체",
    }


def _row_from_item(item: dict[str, Any]) -> dict[str, Any]:
    report_nm = (item.get("report_nm") or "").strip()
    return {
        "event_type": _classify_event(report_nm),  # occurrence / punishment
        "rcept_no": item.get("rcept_no", ""),
        "rcept_dt": item.get("rcept_dt", ""),
        "report_nm": report_nm,
        "filer_name": item.get("flr_nm", ""),
        "subsidiary_report": _is_subsidiary_report(report_nm),
        "is_correction": report_nm.startswith("[기재정정]"),
    }


async def _build_market_scan_payload(
    *,
    start_date: str = "",
    end_date: str = "",
    include_details: bool = False,
    details_limit: int = 5,
) -> dict[str, Any]:
    """회사 미지정 — 시장 전체 최근 중대재해 공시 스캔 (기본 30일, 최대 90일)."""
    warnings: list[str] = []
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
    try:
        first = await client.search_filings(bgn_de=bgn_de, end_de=end_de, pblntf_detail_ty="I001", page_no=1)
        api_calls += 1
        total_page = min(int(first.get("total_page") or 1), _MARKET_SCAN_PAGE_CAP)
        pages = [first]
        if total_page > 1:
            rest = await asyncio.gather(*[
                client.search_filings(bgn_de=bgn_de, end_de=end_de, pblntf_detail_ty="I001", page_no=p)
                for p in range(2, total_page + 1)
            ], return_exceptions=True)
            for r in rest:
                api_calls += 1
                if isinstance(r, Exception):
                    warnings.append(f"시장 스캔 페이지 일부 실패: {r}")
                else:
                    pages.append(r)
        for page in pages:
            for item in page.get("list", []) or []:
                if "중대재해" in (item.get("report_nm") or ""):
                    row = _row_from_item(item)
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
        "casualties": _aggregate_casualties(rows) if include_details else None,
        "window": {"start_date": bgn_de, "end_date": end_de},
        "event_count": {
            "total": len(rows),
            "occurrence": sum(1 for r in rows if r.get("event_type") == "occurrence"),
            "punishment": sum(1 for r in rows if r.get("event_type") == "punishment"),
            "subsidiary_reports": sum(1 for r in rows if r.get("subsidiary_report")),
            "corrections": sum(1 for r in rows if r.get("is_correction")),
        },
        "by_company": dict(sorted(by_company.items(), key=lambda x: -x[1])),
        "events": rows,
        **filing_meta,
        "usage": {"dart_api_calls": api_calls, "mcp_tool_calls": 1, "dart_daily_limit_per_minute": 1000},
    }
    if filing_meta["no_filing"]:
        warnings.append(f"조사 구간 ({bgn_de}~{end_de}) 내 시장 전체 중대재해 공시 없음.")
    warnings.append("공시는 대형 원청·지주사에 집중된다 — 중소형사·하청의 무공시는 무사고 단정 불가 (산재 통계 정본은 고용노동부).")

    return ToolEnvelope(
        tool="serious_accident",
        status=status_from_filing_meta(filing_meta),
        subject="시장 전체 중대재해 공시",
        warnings=warnings,
        data=data,
        evidence_refs=[
            EvidenceRef(
                evidence_id=f"ev_acc_{r['rcept_no']}",
                source_type=SourceType.DART_API,
                rcept_no=r["rcept_no"],
                rcept_dt=format_iso_date(r.get("rcept_dt", "")),
                report_nm=r.get("report_nm", ""),
                section="list.json I001 market scan",
                note=r.get("event_type", ""),
            )
            for r in rows[:5] if r.get("rcept_no")
        ],
    ).to_dict()


async def build_serious_accident_payload(
    company_query: str,
    *,
    start_date: str = "",
    end_date: str = "",
    include_details: bool = False,
    details_limit: int = 5,
) -> dict[str, Any]:
    from open_proxy_mcp.services.filing_search import search_filings_by_report_name

    # 회사 미지정 → 시장 전체 최근 스캔
    if not (company_query or "").strip():
        return await _build_market_scan_payload(
            start_date=start_date,
            end_date=end_date,
            include_details=include_details,
            details_limit=details_limit,
        )

    resolution = await resolve_company_query(company_query)
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        return ToolEnvelope(
            tool="serious_accident",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[
                f"'{company_query}'에 해당하는 회사를 찾지 못했다.",
                "비상장 자회사(예: 포스코이앤씨)의 중대재해는 상장 모회사가 '(종속/자회사의 주요경영사항)'으로 공시한다 — 상장 모회사명으로 조회할 것.",
            ],
            data={"query": company_query},
            next_actions=["company tool로 회사 식별 확인", "비상장사면 상장 모회사로 재조회"],
        ).to_dict()
    if resolution.status == AnalysisStatus.AMBIGUOUS:
        return ToolEnvelope(
            tool="serious_accident",
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

    items, notices, error = await search_filings_by_report_name(
        corp_code=selected["corp_code"],
        bgn_de=bgn_de,
        end_de=end_de,
        pblntf_tys="",
        pblntf_detail_ty="I001",  # 중대재해 공시 ∈ I001 — 49사 I 전체 대비 차집합 0 검증
        keywords=_ACCIDENT_KEYWORDS,
    )
    warnings.extend(notices)
    total_api_calls = 1
    if error:
        warnings.append(f"중대재해 공시 조회 실패: {error}")

    rows = [_row_from_item(item) for item in items or []]
    rows.sort(key=lambda row: (row.get("rcept_dt", ""), row.get("rcept_no", "")), reverse=True)

    if include_details and rows:
        rows, detail_warnings, doc_calls = await _enrich_with_document_details(rows, max_docs=details_limit)
        warnings.extend(detail_warnings)
        total_api_calls += doc_calls

    occurrence_count = sum(1 for r in rows if r.get("event_type") == "occurrence")
    punishment_count = sum(1 for r in rows if r.get("event_type") == "punishment")
    subsidiary_count = sum(1 for r in rows if r.get("subsidiary_report"))
    correction_count = sum(1 for r in rows if r.get("is_correction"))

    parsing_failures = 0
    if include_details:
        for row in rows[:details_limit]:
            if not (row.get("details") or {}):
                parsing_failures += 1
    filing_meta = build_filing_meta(filing_count=len(rows), parsing_failures=parsing_failures)

    usage = {
        "dart_api_calls": total_api_calls,
        "mcp_tool_calls": 1,
        "dart_daily_limit_per_minute": 1000,
    }

    data: dict[str, Any] = {
        "mode": "company",
        "query": company_query,
        "company_id": _company_id(selected),
        "canonical_name": selected.get("corp_name", ""),
        "identifiers": {
            "ticker": selected.get("stock_code", ""),
            "corp_code": selected.get("corp_code", ""),
        },
        "window": {"start_date": bgn_de, "end_date": end_de},
        "event_count": {
            "total": len(rows),
            "occurrence": occurrence_count,
            "punishment": punishment_count,
            "subsidiary_reports": subsidiary_count,
            "corrections": correction_count,
        },
        "casualties": _aggregate_casualties(rows) if include_details else None,
        "events": rows,
        **filing_meta,
        "usage": usage,
    }

    evidence_refs: list[EvidenceRef] = []
    for row in rows[:5]:
        rcept_no = row.get("rcept_no", "")
        if rcept_no:
            evidence_refs.append(
                EvidenceRef(
                    evidence_id=f"ev_acc_{rcept_no}",
                    source_type=SourceType.DART_API,
                    rcept_no=rcept_no,
                    rcept_dt=format_iso_date(row.get("rcept_dt", "")),
                    report_nm=row.get("report_nm", ""),
                    section="list.json I001 + keyword",
                    note=row.get("event_type", ""),
                )
            )

    status = status_from_filing_meta(filing_meta)
    if filing_meta["no_filing"]:
        warnings.append(f"조사 구간 ({bgn_de}~{end_de}) 내 중대재해 공시 없음.")
    elif filing_meta["parsing_failures"] > 0:
        warnings.append(f"원문 파싱 실패 {filing_meta['parsing_failures']}건 — details 필드 비어 있음")

    # 제도 시점 안내 — 조사 구간이 수시공시 신설 이전을 포함하면 항상 부착
    if bgn_de < _DISCLOSURE_REGIME_START:
        warnings.append(
            "거래소 중대재해 수시공시는 2025-10월부터 관측된다 (실측 최초 2025-10-29). "
            "그 이전 구간의 무공시는 무사고를 의미하지 않는다 — 과거 이력은 뉴스·고용노동부 자료로 보완할 것."
        )

    return ToolEnvelope(
        tool="serious_accident",
        status=status,
        subject=selected.get("corp_name", company_query),
        warnings=warnings,
        data=data,
        evidence_refs=evidence_refs,
    ).to_dict()
