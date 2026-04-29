"""v2 ownership_structure facade 서비스."""

from __future__ import annotations

import asyncio
from datetime import date
import re
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
    build_usage,
    status_from_filing_meta,
)
from open_proxy_mcp.services.date_utils import format_iso_date, format_yyyymmdd, parse_date_param, resolve_date_window
from open_proxy_mcp.tools.formatters import _parse_holding_purpose, _parse_holding_purpose_from_document

_SUPPORTED_SCOPES = {
    "summary",
    "major_holders",
    "blocks",
    "treasury",
    "control_map",
    "timeline",
    "changes",
}

# 정기보고서 reprt_code: 사업보고서가 가장 정식이지만, 시기에 따라 미공시일 수 있어
# (사업 → 3분기 → 반기 → 1분기) 순으로 fallback. 모두 빈 응답이면 직전 사업연도까지 시도.
_REPRT_CODE_FALLBACK = ["11011", "11014", "11012", "11013"]

_SUBTOTAL_NAMES = {"계", "합계", "소계", "총계", "총합계"}


def _to_float(value: Any) -> float:
    """문자열/숫자 → 실수 (괄호 음수 처리 포함, 한국 회계 관행 대응)."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    is_negative = text.startswith("(") and text.endswith(")")
    if is_negative:
        text = text[1:-1]
    try:
        result = float(text.replace(",", ""))
        return -result if is_negative else result
    except (TypeError, ValueError):
        return 0.0


def _to_int(value: Any) -> int:
    """문자열 → 정수 (괄호 음수 처리 포함).

    delta 필드 등 음수 발생 가능 영역에서 일관성 보장.
    """
    if value is None:
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return 0
    is_negative = text.startswith("(") and text.endswith(")")
    if is_negative:
        text = text[1:-1]
    try:
        digits = re.sub(r"[^\d-]", "", text) or "0"
        result = int(digits)
        return -result if is_negative else result
    except ValueError:
        return 0


def _normalize_stock_label(value: str) -> str:
    """공백·개행 제거. stock_knd / nm 변형 비교용."""
    return re.sub(r"\s+", "", (value or "").strip())


def _is_voting_common_stock(stock_kind: str) -> bool:
    """`최대주주` 합산 대상이 되는 보통주(=의결권 있는 주식) 여부 판정.

    DART hyslrSttus의 `stock_knd`는 회사·시기에 따라 표기가 매우 다양하다:
      - "보통주", "보통주식", " 보통주" (공백 변형)
      - "의결권 있는 주식", "의결권있는 주식", "의결권이 있는 주식"
      - "의결권 있는 주식\\n(보통주)", "의결권\\n있는 주식" (개행 포함)
    공통점: "보통" 혹은 "있는"을 포함. 반대로 우선/없는/기타/-/종류/합계는 제외.

    빈 stock_knd는 보수적으로 보통주로 간주(과거 일부 회사가 빈 값으로 보고하는 케이스).
    """
    norm = _normalize_stock_label(stock_kind)
    if not norm:
        return True
    if "없는" in norm:
        return False
    return ("보통" in norm) or ("있는" in norm)


def _is_subtotal_row(name: str) -> bool:
    """`계`, `합계`, `소계` 등 합계 행 판별 (공백·개행 무시)."""
    return _normalize_stock_label(name) in _SUBTOTAL_NAMES


def _clean_name(name: str) -> str:
    """이름에서 줄바꿈·중복 공백을 정리한다."""
    return re.sub(r"\s+", " ", (name or "").strip())


def _major_holders_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    """hyslrSttus list → 본인+특수관계인 의결권 보통주 행만 추출.

    legacy 필터 ``("보통" not in stock_kind and stock_kind)``는 SK하이닉스/현대차/LG전자
    등 ``의결권 있는 주식`` 표기를 사용하는 회사를 모두 누락시켰다.
    실표기 변형을 분석해 normalize 후 positive matching으로 교체.
    """
    rows: list[dict[str, Any]] = []
    for item in data.get("list", []):
        stock_kind = item.get("stock_knd", "")
        name = _clean_name(item.get("nm", ""))
        if not name or _is_subtotal_row(name):
            continue
        if not _is_voting_common_stock(stock_kind):
            continue
        rows.append({
            "name": name,
            "relation": _clean_name(item.get("relate", "")),
            "shares": _to_int(item.get("trmend_posesn_stock_co", "0")),
            "ownership_pct": _to_float(item.get("trmend_posesn_stock_qota_rt", "0")),
            "settlement_date": item.get("stlm_dt", ""),
        })
    rows.sort(key=lambda row: row["ownership_pct"], reverse=True)
    return rows


async def _fetch_major_with_fallback(
    client,
    corp_code: str,
    bsns_year: str,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    """hyslrSttus를 다중 (year, reprt_code) 조합으로 시도.

    1) 요청된 bsns_year의 (사업 → 3분기 → 반기 → 1분기) 순회
    2) 모두 빈 응답이면 직전 사업연도 사업보고서 시도
    3) 모두 실패면 빈 list 반환 (호출자가 5% 보고서 fallback 결정)

    Returns:
        (major_rows, source_meta, warnings)
        - source_meta: {"endpoint", "bsns_year", "reprt_code", "fallback_used", "no_data"}
    """
    warnings: list[str] = []
    attempts: list[tuple[str, str]] = [(bsns_year, code) for code in _REPRT_CODE_FALLBACK]
    try:
        prev_year = str(int(bsns_year) - 1)
        attempts.append((prev_year, "11011"))
    except ValueError:
        pass

    last_status: str | None = None
    for try_year, try_code in attempts:
        try:
            data = await client.get_major_shareholders(corp_code, try_year, try_code)
        except DartClientError as exc:
            last_status = exc.status
            if exc.status != "013":
                warnings.append(
                    f"hyslrSttus 호출 실패 ({try_year}/{try_code}): {exc.status}"
                )
            continue
        rows = _major_holders_rows(data)
        if rows:
            source_meta = {
                "endpoint": "hyslrSttus",
                "bsns_year": try_year,
                "reprt_code": try_code,
                "raw_count": len(data.get("list", [])),
                "parsed_count": len(rows),
                "fallback_used": (try_year, try_code) != (bsns_year, "11011"),
            }
            if source_meta["fallback_used"]:
                warnings.append(
                    f"최대주주: bsns_year={try_year} reprt_code={try_code} fallback 사용"
                )
            return rows, source_meta, warnings

    source_meta = {
        "endpoint": "hyslrSttus",
        "bsns_year": bsns_year,
        "reprt_code": "11011",
        "raw_count": 0,
        "parsed_count": 0,
        "fallback_used": False,
        "no_data": True,
        "last_status": last_status,
    }
    return [], source_meta, warnings


async def _fetch_largest_shareholder_from_blocks(
    client,
    corp_code: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """5% 대량보유(majorstock)에서 최대주주 후보를 추정.

    hyslrSttus가 비어 있는 ‘ownerless’(국민연금 6%대만 있는 회사 등) 또는
    미공개 케이스의 보조 source. 5% 보고는 ‘외부 주주 시점’이라
    본인+특수관계인 합산 개념과 다르므로 추정치임을 명시한다.

    KT&G 같은 회사도 hyslrSttus에 본인 7%대 데이터가 있어 1차 fallback이
    먼저 처리하는 것이 정상. 이 함수는 그것마저 실패한 잔여 케이스용.
    """
    warnings: list[str] = []
    try:
        data = await client.get_block_holders(corp_code)
    except DartClientError as exc:
        if exc.status != "013":
            warnings.append(f"5% 대량보유 fallback 실패: {exc.status}")
        return [], warnings

    latest_by_reporter: dict[str, dict[str, Any]] = {}
    for item in data.get("list", []):
        reporter = (item.get("repror", "") or "").strip()
        if not reporter:
            continue
        rcept_dt = item.get("rcept_dt", "")
        if reporter not in latest_by_reporter or rcept_dt > latest_by_reporter[reporter].get("rcept_dt", ""):
            latest_by_reporter[reporter] = item

    rows: list[dict[str, Any]] = []
    for reporter, item in latest_by_reporter.items():
        rows.append({
            "name": _clean_name(reporter),
            "relation": "5% 보유자(추정)",
            "shares": _to_int(item.get("stkqy", 0)),
            "ownership_pct": _to_float(item.get("stkrt", 0)),
            "settlement_date": item.get("rcept_dt", ""),
        })
    rows.sort(key=lambda row: row["ownership_pct"], reverse=True)
    return rows, warnings


def _top_holder_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {}
    return rows[0]


def _related_total(rows: list[dict[str, Any]]) -> float:
    return round(sum(row["ownership_pct"] for row in rows), 2)


def _normalize_entity_name(name: str) -> str:
    normalized = (name or "").strip()
    normalized = normalized.replace("\n", " ")
    normalized = re.sub(r"\(주\)|㈜|주식회사|유한회사|유한책임회사|\(유\)|\(유한\)", "", normalized)
    normalized = re.sub(r"[^\w가-힣]", "", normalized)
    return normalized.lower()


def _is_active_purpose(purpose: str) -> bool:
    return purpose not in ("단순투자", "단순투자/일반투자", "일반투자", "불명")


def _is_material_block(row: dict[str, Any]) -> bool:
    return _to_float(row.get("ownership_pct", 0)) >= 5.0


def _build_control_map(
    major_rows: list[dict[str, Any]],
    latest_blocks: list[dict[str, Any]],
    treasury_snapshot: dict[str, Any],
) -> dict[str, Any]:
    major_name_map = {
        _normalize_entity_name(row["name"]): row
        for row in major_rows
        if _normalize_entity_name(row["name"])
    }

    overlap_blocks: list[dict[str, Any]] = []
    non_overlap_blocks: list[dict[str, Any]] = []
    active_non_overlap_blocks: list[dict[str, Any]] = []
    active_overlap_blocks: list[dict[str, Any]] = []

    for row in latest_blocks:
        reporter_key = _normalize_entity_name(row.get("reporter", ""))
        matched_major = major_name_map.get(reporter_key)
        enriched = {
            **row,
            "registry_overlap": bool(matched_major),
            "matched_major_holder": matched_major.get("name") if matched_major else None,
            "active_purpose": _is_active_purpose(row.get("purpose", "")),
        }
        if enriched["registry_overlap"]:
            overlap_blocks.append(enriched)
            if enriched["active_purpose"] and _is_material_block(enriched):
                active_overlap_blocks.append(enriched)
        else:
            non_overlap_blocks.append(enriched)
            if enriched["active_purpose"] and _is_material_block(enriched):
                active_non_overlap_blocks.append(enriched)

    related_total_pct = _related_total(major_rows)
    treasury_pct = treasury_snapshot["treasury_pct"]

    flags = {
        "registry_majority": related_total_pct >= 50,
        "registry_over_30pct": related_total_pct >= 30,
        "treasury_over_5pct": treasury_pct >= 5,
        "active_non_overlap_block_exists": bool(active_non_overlap_blocks),
        "active_overlap_block_exists": bool(active_overlap_blocks),
    }

    observations: list[str] = []
    if flags["registry_majority"]:
        observations.append("명부상 특수관계인 합계가 50% 이상이다.")
    elif flags["registry_over_30pct"]:
        observations.append("명부상 특수관계인 합계가 30% 이상이다.")
    if flags["treasury_over_5pct"]:
        observations.append("자사주 비중이 5% 이상이다.")
    if flags["active_non_overlap_block_exists"]:
        observations.append("명부상 최대주주 테이블과 겹치지 않는 능동적 5% 블록이 있다.")
    elif flags["active_overlap_block_exists"]:
        observations.append("능동적 5% 블록이 있으나 명부상 최대주주 테이블과 이름이 겹친다.")

    return {
        "core_holder_block": {
            "top_holder": _top_holder_summary(major_rows),
            "related_total_pct": related_total_pct,
            "holder_count": len(major_rows),
        },
        "treasury_block": {
            "shares": treasury_snapshot["treasury_shares"],
            "pct": treasury_pct,
        },
        "overlap_blocks": overlap_blocks,
        "active_overlap_blocks": active_overlap_blocks,
        "non_overlap_blocks": non_overlap_blocks,
        "active_non_overlap_blocks": active_non_overlap_blocks,
        "flags": flags,
        "observations": observations,
        "notes": [
            "5% 블록은 최대주주 명부와 단순 합산하지 않는다.",
            "registry_overlap은 같은 이름이 최대주주 명부에 있는지를 뜻하며, 현재 이해관계가 완전히 같다는 의미는 아니다.",
        ],
    }


async def _latest_block_rows(corp_code: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str | None]:
    client = get_dart_client()
    try:
        data = await client.get_block_holders(corp_code)
    except DartClientError as exc:
        return [], [], f"대량보유 공시 조회 실패: {exc.status}"

    latest_by_reporter: dict[str, dict[str, Any]] = {}
    for item in data.get("list", []):
        reporter = item.get("repror", "").strip()
        if not reporter:
            continue
        if reporter not in latest_by_reporter or item.get("rcept_dt", "") > latest_by_reporter[reporter].get("rcept_dt", ""):
            latest_by_reporter[reporter] = item

    latest_rows: list[dict[str, Any]] = []
    timeline_rows: list[dict[str, Any]] = []
    for item in data.get("list", []):
        timeline_rows.append({
            "reporter": item.get("repror", "").strip(),
            "report_date": item.get("rcept_dt", ""),
            "rcept_no": item.get("rcept_no", ""),
            "ownership_pct": _to_float(item.get("stkrt", 0)),
            "purpose": _parse_holding_purpose(item.get("report_tp", ""), item.get("report_resn", "")),
            "report_name": item.get("report_tp", ""),
        })
    timeline_rows.sort(key=lambda row: (row["report_date"], row["rcept_no"]), reverse=True)

    for reporter, item in latest_by_reporter.items():
        purpose = _parse_holding_purpose(item.get("report_tp", ""), item.get("report_resn", ""))
        rcept_no = item.get("rcept_no", "")
        if purpose in ("불명", "단순투자/일반투자") and rcept_no:
            try:
                doc = await client.get_document_cached(rcept_no)
                parsed = _parse_holding_purpose_from_document(doc.get("html", "") or "")
                if parsed != "불명":
                    purpose = parsed
            except Exception:
                pass
        latest_rows.append({
            "reporter": reporter,
            "report_date": item.get("rcept_dt", ""),
            "rcept_no": rcept_no,
            "ownership_pct": _to_float(item.get("stkrt", 0)),
            "purpose": purpose,
            "report_type": item.get("report_tp", ""),
            "report_reason": item.get("report_resn", ""),
        })
    latest_rows.sort(key=lambda row: row["ownership_pct"], reverse=True)
    return latest_rows, timeline_rows, None


def _treasury_snapshot(stock_total: dict[str, Any], treasury_data: dict[str, Any]) -> dict[str, Any]:
    issued = 0
    treasury = 0
    distributable = 0
    for item in stock_total.get("list", []):
        se = item.get("se", "")
        if "보통" in se:
            issued = _to_int(item.get("istc_totqy", "0"))
            treasury = _to_int(item.get("tesstk_co", "0"))
            distributable = _to_int(item.get("distb_stock_co", "0"))
            break

    rows = []
    for item in treasury_data.get("list", []):
        rows.append({
            "category": item.get("se", ""),
            "begin_shares": _to_int(item.get("bsis_qy", "0")),
            "acquired_shares": _to_int(item.get("acqs_qy", "0")),
            "disposed_shares": _to_int(item.get("dsps_qy", "0")),
            "retired_shares": _to_int(item.get("inciner_qy", "0")),
            "end_shares": _to_int(item.get("trmend_qy", "0")),
        })

    return {
        "issued_shares": issued,
        "treasury_shares": treasury,
        "tradable_shares": distributable,
        "treasury_pct": round(treasury / issued * 100, 2) if issued else 0.0,
        "rows": rows,
    }


def _parse_change_filing(html: str, rcept_no: str, rcept_dt: str) -> dict[str, Any]:
    """KIND HTML에서 최대주주등소유주식변동신고서 파싱."""
    soup = BeautifulSoup(html, "lxml")
    tables = soup.find_all("table")

    if len(tables) < 4:
        return {"rcept_no": rcept_no, "rcept_dt": rcept_dt, "parse_error": f"테이블 {len(tables)}개 (최소 4개 필요)"}

    def cell_texts(table) -> list[list[str]]:
        return [
            [td.get_text(" ", strip=True) for td in row.find_all(["td", "th"])]
            for row in table.find_all("tr")
            if row.find_all(["td", "th"])
        ]

    # 섹션 3 (index 2): 보고의 개요 - 직전/금번 주식수+비율
    overview: dict[str, Any] = {}
    try:
        for cells in cell_texts(tables[2]):
            joined = "".join(cells)
            nums = [_to_float(re.sub(r"[^\d.]", "", c)) for c in cells if re.sub(r"[^\d.]", "", c)]
            if "직전" in joined and nums:
                overview["before_shares"] = int(nums[0]) if nums else 0
                overview["before_pct"] = nums[1] if len(nums) > 1 else 0.0
            elif "금번" in joined and nums:
                overview["after_shares"] = int(nums[0]) if nums else 0
                overview["after_pct"] = nums[1] if len(nums) > 1 else 0.0
    except Exception:
        pass

    # 섹션 4~N-1 (index 3 to -2): 개인별 세부변동사항
    individual_changes: list[dict[str, Any]] = []
    for t in tables[3:-1]:
        # 주주명은 테이블 직전 bold span에서 추출
        holder_name = ""
        prev_span = t.find_previous("span")
        if prev_span:
            holder_name = prev_span.get_text(strip=True)

        change_rows: list[dict[str, Any]] = []
        header_found = False
        for cells in cell_texts(t):
            joined = "".join(cells)
            if not header_found:
                if "변경일" in joined or "변경원인" in joined:
                    header_found = True
                continue
            if len(cells) >= 5:
                change_rows.append({
                    "date": cells[0],
                    "reason": cells[1],
                    "stock_type": cells[2] if len(cells) > 2 else "",
                    "before": _to_int(cells[3]) if len(cells) > 3 else 0,
                    "delta": _to_int(cells[4]) if len(cells) > 4 else 0,
                    "after": _to_int(cells[5]) if len(cells) > 5 else 0,
                })
        individual_changes.append({"holder_name": holder_name, "changes": change_rows})

    # 마지막 테이블: 최대주주등 주식소유현황 (총괄)
    total_holders: list[dict[str, Any]] = []
    try:
        header_found = False
        for cells in cell_texts(tables[-1]):
            joined = "".join(cells)
            if not header_found:
                if "성명" in joined or "관계" in joined:
                    header_found = True
                continue
            if not cells[0] or cells[0] in ("계", "합계", "소계"):
                continue
            if len(cells) >= 3:
                # 컬럼: 성명 / 관계 / 보통주수 / 보통주비율 / ... / 합계수 / 합계비율
                total_holders.append({
                    "name": cells[0],
                    "relation": cells[1] if len(cells) > 1 else "",
                    "shares": _to_int(cells[2]) if len(cells) > 2 else 0,
                    "pct": _to_float(cells[3]) if len(cells) > 3 else 0.0,
                })
    except Exception:
        pass

    return {
        "rcept_no": rcept_no,
        "rcept_dt": rcept_dt,
        "overview": overview,
        "individual_changes": individual_changes,
        "total_holders": total_holders,
    }


async def _fetch_change_filings(
    corp_code: str,
    window_start: date,
    window_end: date,
    client,
) -> tuple[list[dict[str, Any]], list[str]]:
    """DART 검색 → KIND 크롤링 → 변동신고서 리스트 반환."""
    warnings: list[str] = []
    try:
        result = await client.search_filings(
            bgn_de=format_yyyymmdd(window_start),
            end_de=format_yyyymmdd(window_end),
            pblntf_ty="I",
            corp_code=corp_code,
            page_count=20,
        )
    except DartClientError as exc:
        return [], [f"변동신고서 DART 검색 실패: {exc.status}"]

    filings_raw = [
        item for item in result.get("list", [])
        if "최대주주등소유주식변동신고서" in item.get("report_nm", "")
    ]
    if not filings_raw:
        return [], []

    filings: list[dict[str, Any]] = []
    for item in filings_raw[:5]:
        rcept_no = item.get("rcept_no", "")
        rcept_dt = item.get("rcept_dt", "")
        if not (rcept_no and len(rcept_no) == 14 and rcept_no[8:10] == "80"):
            warnings.append(f"변동신고서 rcept_no 포맷 불일치: {rcept_no}")
            continue
        acptno = rcept_no[:8] + "00" + rcept_no[10:]
        try:
            html = await client.kind_fetch_document(acptno)
            parsed = _parse_change_filing(html, rcept_no, rcept_dt)
            parsed["report_nm"] = item.get("report_nm", "최대주주등소유주식변동신고서")
            parsed["acptno"] = acptno
            filings.append(parsed)
        except DartClientError as exc:
            warnings.append(f"KIND 변동신고서 본문 조회 실패 ({rcept_no}): {exc.status}")

    return filings, warnings


def _unsupported_scope_payload(company_query: str, scope: str) -> dict[str, Any]:
    envelope = ToolEnvelope(
        tool="ownership_structure",
        status=AnalysisStatus.REQUIRES_REVIEW,
        subject=company_query,
        warnings=[f"`{scope}` scope는 아직 v2에서 열지 않았다."],
        data={"query": company_query, "scope": scope},
    )
    return envelope.to_dict()


async def build_ownership_structure_payload(
    company_query: str,
    *,
    scope: str = "summary",
    year: int | None = None,
    as_of_date: str = "",
    start_date: str = "",
    end_date: str = "",
) -> dict[str, Any]:
    if scope not in _SUPPORTED_SCOPES:
        return _unsupported_scope_payload(company_query, scope)

    client = get_dart_client()
    _calls_start = client.api_call_snapshot()
    resolution = await resolve_company_query(company_query)
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        return ToolEnvelope(
            tool="ownership_structure",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[f"'{company_query}'에 해당하는 회사를 찾지 못했다."],
            data={
                "query": company_query,
                "scope": scope,
                "usage": build_usage(client.api_call_snapshot() - _calls_start),
            },
            next_actions=["company tool로 회사 식별 확인"],
        ).to_dict()
    if resolution.status == AnalysisStatus.AMBIGUOUS:
        return ToolEnvelope(
            tool="ownership_structure",
            status=AnalysisStatus.AMBIGUOUS,
            subject=company_query,
            warnings=["회사 식별이 애매해 지분 구조를 자동 선택하지 않았다."],
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
            },
        ).to_dict()

    selected = resolution.selected
    as_of = parse_date_param(as_of_date)
    as_of_year = (as_of.year - 1) if as_of else None
    bsns_year = str(year or as_of_year or (date.today().year - 1))
    window_start, window_end, window_warnings = resolve_date_window(
        start_date=start_date,
        end_date=end_date,
        default_end=as_of or date.today(),
        lookback_months=12,
    )
    warnings: list[str] = list(window_warnings)

    # 3개 정기보고서 API는 같은 corp_code/bsns_year로 병렬 호출 가능 (independent).
    # major는 실패 시 즉시 ERROR return하던 기존 동작 유지(asyncio.gather + return_exceptions).
    major_task = client.get_major_shareholders(selected["corp_code"], bsns_year)
    stock_total_task = client.get_stock_total(selected["corp_code"], bsns_year)
    treasury_task = client.get_treasury_stock(selected["corp_code"], bsns_year)
    major_res, stock_total_res, treasury_res = await asyncio.gather(
        major_task, stock_total_task, treasury_task, return_exceptions=True,
    )

    # 1차: 사업보고서 hyslrSttus.
    # 빈 응답(013)은 ERROR가 아니라 fallback 경로로 보낸다 — KOSPI 대형주 다수가 정기보고서
    # 표기 형식 변형(의결권 있는 주식 등)으로 legacy 파서에서 0건이었기 때문.
    major_source: dict[str, Any] = {
        "endpoint": "hyslrSttus",
        "bsns_year": bsns_year,
        "reprt_code": "11011",
        "fallback_used": False,
    }
    if isinstance(major_res, DartClientError):
        if major_res.status != "013":
            return ToolEnvelope(
                tool="ownership_structure",
                status=AnalysisStatus.ERROR,
                subject=selected.get("corp_name", company_query),
                warnings=[f"최대주주 API 조회 실패: {major_res.status}"],
                data={
                    "query": company_query,
                    "scope": scope,
                    "year": bsns_year,
                    "usage": build_usage(client.api_call_snapshot() - _calls_start),
                },
            ).to_dict()
        major = {"list": []}
        major_source["last_status"] = "013"
    elif isinstance(major_res, BaseException):
        raise major_res
    else:
        major = major_res
        major_source["raw_count"] = len(major.get("list", []))

    if isinstance(stock_total_res, DartClientError):
        stock_total = {"list": []}
        warnings.append(f"주식총수 API 조회 실패: {stock_total_res.status}")
    elif isinstance(stock_total_res, BaseException):
        raise stock_total_res
    else:
        stock_total = stock_total_res

    if isinstance(treasury_res, DartClientError):
        treasury_data = {"list": []}
        warnings.append(f"자사주 API 조회 실패: {treasury_res.status}")
    elif isinstance(treasury_res, BaseException):
        raise treasury_res
    else:
        treasury_data = treasury_res

    major_rows = _major_holders_rows(major)
    major_source["parsed_count"] = len(major_rows)

    # 2차 fallback: 1차에서 0건 → 다른 reprt_code (반기/분기) + 직전연도 사업 시도.
    if not major_rows:
        fb_rows, fb_source, fb_warnings = await _fetch_major_with_fallback(
            client, selected["corp_code"], bsns_year
        )
        warnings.extend(fb_warnings)
        if fb_rows:
            major_rows = fb_rows
            major_source = fb_source

    latest_blocks, timeline_rows, block_warning = await _latest_block_rows(selected["corp_code"])
    if block_warning:
        warnings.append(block_warning)

    # 3차 fallback: 정기보고서 모두 빈 응답 → 5% 대량보유에서 추정.
    # ‘본인+특수관계인 합산’ 개념과는 다르므로 추정치임을 명시한다.
    if not major_rows:
        block_fb_rows, block_fb_warnings = await _fetch_largest_shareholder_from_blocks(
            client, selected["corp_code"]
        )
        warnings.extend(block_fb_warnings)
        if block_fb_rows:
            major_rows = block_fb_rows
            major_source = {
                "endpoint": "majorstock",
                "fallback_used": True,
                "estimated_from_5pct": True,
                "parsed_count": len(block_fb_rows),
            }
            warnings.append(
                "최대주주를 5% 대량보유 보고에서 추정 — 본인+특수관계인 합산이 아니므로 정확도 제한"
            )
    start_ymd = format_yyyymmdd(window_start)
    end_ymd = format_yyyymmdd(window_end)
    latest_blocks = [
        row for row in latest_blocks
        if start_ymd <= row.get("report_date", "").replace("-", "") <= end_ymd
    ]
    timeline_rows = [
        row for row in timeline_rows
        if start_ymd <= row.get("report_date", "").replace("-", "") <= end_ymd
    ]

    top_holder = _top_holder_summary(major_rows)
    treasury_snapshot = _treasury_snapshot(stock_total, treasury_data)
    active_signals = [
        row for row in latest_blocks
        if row["purpose"] not in ("단순투자", "단순투자/일반투자", "불명")
    ]

    # 사건 발견 vs 진짜 partial 분리.
    # ownership은 두 종류 — 정기보고서(major_holders)는 대부분 항상 있고
    # 5% 보고서·변동신고서는 회사·구간에 따라 없을 수 있다.
    # filing_count = major_holders rows + latest_blocks (5% 보고).
    filing_count = len(major_rows) + len(latest_blocks)
    parsing_failures = 0
    # major_rows가 비어 있으면 정기보고서 파싱 실패 = 진짜 partial.
    if not major_rows:
        parsing_failures += 1
    filing_meta = build_filing_meta(
        filing_count=filing_count,
        parsing_failures=parsing_failures,
    )

    data: dict[str, Any] = {
        "query": company_query,
        "company_id": _company_id(selected),
        "canonical_name": selected.get("corp_name", ""),
        "identifiers": {
            "ticker": selected.get("stock_code", ""),
            "corp_code": selected.get("corp_code", ""),
        },
        "year": bsns_year,
        "as_of_date": as_of.isoformat() if as_of else "",
        "window": {
            "start_date": start_ymd,
            "end_date": end_ymd,
        },
        "summary": {
            "top_holder": top_holder,
            "related_total_pct": _related_total(major_rows),
            "treasury_shares": treasury_snapshot["treasury_shares"],
            "treasury_pct": treasury_snapshot["treasury_pct"],
            "active_signal_count": len(active_signals),
        },
        "largest_shareholder_source": major_source,
        **filing_meta,
        "available_scopes": sorted(_SUPPORTED_SCOPES),
    }

    if scope in {"summary", "major_holders", "control_map"}:
        data["major_holders"] = major_rows
    if scope in {"summary", "blocks", "control_map"}:
        data["blocks"] = latest_blocks
    if scope in {"summary", "treasury"}:
        data["treasury"] = treasury_snapshot
    if scope == "timeline":
        data["timeline"] = timeline_rows[:50]
    if scope == "control_map":
        data["control_map"] = _build_control_map(major_rows, latest_blocks, treasury_snapshot)
    if scope == "changes":
        change_filings, change_warnings = await _fetch_change_filings(
            selected["corp_code"], window_start, window_end, client
        )
        data["change_filings"] = change_filings
        warnings.extend(change_warnings)

    evidence_refs: list[EvidenceRef] = [
        EvidenceRef(
            evidence_id=f"ev_ownership_api_{selected['corp_code']}_{bsns_year}",
            source_type=SourceType.DART_API,
            section="hyslrSttus/stockTotqySttus",
            note=f"{selected.get('corp_name', '')} {bsns_year}년 정기보고서 기준 최대주주/주식총수",
        )
    ]
    if latest_blocks:
        first = latest_blocks[0]
        if first.get("rcept_no"):
            evidence_refs.append(
                EvidenceRef(
                    evidence_id=f"ev_block_{first['rcept_no']}",
                    source_type=SourceType.DART_XML,
                    rcept_no=first["rcept_no"],
                    rcept_dt=format_iso_date(first.get("report_date", "")),
                    report_nm=first.get("report_name", ""),
                    section="대량보유 상황보고",
                    note=f"{first['reporter']} / {first['ownership_pct']}% / {first['purpose']}",
                )
            )
    for filing in data.get("change_filings", []):
        if filing.get("rcept_no") and not filing.get("parse_error"):
            evidence_refs.append(
                EvidenceRef(
                    evidence_id=f"ev_change_{filing['rcept_no']}",
                    source_type=SourceType.KIND_HTML,
                    rcept_no=filing["rcept_no"],
                    rcept_dt=format_iso_date(filing.get("rcept_dt", "")),
                    report_nm="최대주주등소유주식변동신고서",
                    section="최대주주등 소유주식 변동",
                    note=f"acptno={filing.get('acptno', '')}",
                )
            )

    status = status_from_filing_meta(filing_meta)
    if filing_meta["no_filing"]:
        warnings.append(f"조사 구간 ({start_ymd}~{end_ymd}) 내 정기보고서/5% 대량보유 공시 없음 (정상)")
    elif filing_meta["parsing_failures"] > 0 and not major_rows:
        warnings.append("최대주주 구조를 충분히 읽지 못해 partial 상태로 표시한다.")

    data["usage"] = build_usage(client.api_call_snapshot() - _calls_start)

    return ToolEnvelope(
        tool="ownership_structure",
        status=status,
        subject=selected.get("corp_name", company_query),
        warnings=warnings,
        data=data,
        evidence_refs=evidence_refs,
        next_actions=[
            "blocks scope로 5% 대량보유 최신 보고 확인" if scope == "summary" else "proxy_contest와 함께 보면 분쟁 맥락이 더 잘 보인다.",
        ],
    ).to_dict()
