"""v2 related_party_transaction data tool.

타법인주식 거래 + 단일판매·공급계약 공시 통합. 일감몰아주기·내부거래 모니터링 소스.

DART 전용 구조화 API가 없어 list.json + report_nm 키워드 매칭 방식.
상세 수치(거래금액, 상대방)는 evidence tool로 원문 링크 제공.
"""

from __future__ import annotations

import asyncio
import re
from datetime import date
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
from open_proxy_mcp.services.filing_search import search_filings_by_report_name


_SUPPORTED_SCOPES = {"summary", "equity_deal", "supply_contract"}


# 타법인주식 거래 — 취득/양수 및 처분/양도
_EQUITY_DEAL_KEYWORDS = (
    "타법인주식및출자증권양수결정",
    "타법인주식및출자증권양도결정",
    "타법인주식및출자증권취득결정",
    "타법인주식및출자증권처분결정",
)

# 단일판매·공급계약 — 체결/해지
_SUPPLY_CONTRACT_KEYWORDS = (
    "단일판매ㆍ공급계약체결",
    "단일판매ㆍ공급계약해지",
    "단일판매·공급계약체결",
    "단일판매·공급계약해지",
)


def _classify_equity_deal(report_nm: str) -> str:
    compact = (report_nm or "").replace(" ", "")
    if "양수" in compact or "취득" in compact:
        return "acquire"
    if "양도" in compact or "처분" in compact:
        return "dispose"
    return "unknown"


def _classify_supply_contract(report_nm: str) -> str:
    compact = (report_nm or "").replace(" ", "")
    if "해지" in compact:
        return "terminate"
    if "체결" in compact:
        return "conclude"
    return "unknown"


def _is_self_filing(flr_nm: str, corp_name: str) -> bool:
    """공시 제출인이 회사 본인인지 (자회사 주요경영사항 구분)."""
    a = (flr_nm or "").strip()
    b = (corp_name or "").strip()
    return bool(a and b and (a == b or b in a or a in b))


def _is_autonomous(report_nm: str) -> bool:
    compact = (report_nm or "").replace(" ", "")
    return "자율공시" in compact


def _is_subsidiary_report(report_nm: str) -> bool:
    compact = (report_nm or "").replace(" ", "")
    return "자회사의주요경영사항" in compact or "자회사의주요경영사항" in report_nm


# ── 원문 파싱 helpers ─────────────────────────────────────────────

def _extract_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "lxml")
    return soup.get_text("\n", strip=True)


def _find_value_after(lines: list[str], label: str, max_distance: int = 3) -> str:
    """라벨 뒤에 나오는 값 추출 (최대 N줄 이내)."""
    for i, line in enumerate(lines):
        if label in line:
            for j in range(1, max_distance + 1):
                if i + j < len(lines):
                    v = lines[i + j].strip()
                    # 다음 라벨이면 skip
                    if v and not v.endswith(":") and not v.startswith("-") and len(v) < 200:
                        return v
    return ""


def _find_pct_near(text: str, label_pattern: str) -> str:
    """라벨 근처 % 값 추출."""
    m = re.search(label_pattern + r"[^\d]*(\d+(?:\.\d+)?)", text, re.MULTILINE)
    return m.group(1) if m else ""


def _find_amount_near(text: str, label_pattern: str) -> str:
    """라벨 근처 금액 추출 (콤마 포함 숫자)."""
    m = re.search(label_pattern + r"[^\d]*(\d{1,3}(?:,\d{3})+)", text, re.MULTILINE)
    return m.group(1) if m else ""


_RELATIONSHIP_VALUES = {
    "자회사", "종속회사", "손자회사", "계열회사", "계열사",
    "관계회사", "특수관계인", "특수관계자", "제3자", "해당없음", "해당사항없음",
}


def _extract_relationship(text: str) -> str:
    """'회사와의 관계' 값 추출 — 정해진 관계 값 후보만 허용."""
    for pattern in (r"회사와\s*관계[^\n]*\n+([^\n]+)",
                    r"본\s*회사와의\s*관계[^\n]*\n+([^\n]+)",
                    r"당사와의?\s*관계[^\n]*\n+([^\n]+)"):
        for m in re.finditer(pattern, text):
            val = m.group(1).strip()
            if val in _RELATIONSHIP_VALUES:
                return val
            # 값 안에 관계 단어 포함되는 경우
            for kw in _RELATIONSHIP_VALUES:
                if kw in val and len(val) < 50:
                    return kw
    return ""


def _parse_equity_deal_document(html: str) -> dict[str, Any]:
    """타법인주식 및 출자증권 취득/처분결정 원문 파싱."""
    text = _extract_text(html)
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    # 발행회사 (거래 대상회사)
    counterparty_name = _find_value_after(lines, "회사명(국적)", 2) or _find_value_after(lines, "회사명", 2)
    relationship = _extract_relationship(text)
    business = _find_value_after(lines, "주요사업", 2)

    # 취득내역
    acquisition_amount = _find_amount_near(text, r"취득금액\(원\)")
    if not acquisition_amount:
        acquisition_amount = _find_amount_near(text, r"처분금액\(원\)")
    equity_ratio = _find_pct_near(text, r"자기자본대비\(%\)")
    asset_ratio = _find_pct_near(text, r"취득가액/자산총액\(%\)")
    if not asset_ratio:
        asset_ratio = _find_pct_near(text, r"처분가액/자산총액\(%\)")

    # 취득 후 지분
    post_ownership_pct = ""
    m = re.search(r"취득후\s*소유주식수\s*및\s*지분비율.{0,200}?지분비율\(%\)\s*\n*\s*(\d+(?:\.\d+)?)", text, re.DOTALL)
    if m:
        post_ownership_pct = m.group(1)

    # 방법·목적
    method = _find_value_after(lines, "취득방법", 1) or _find_value_after(lines, "처분방법", 1)
    purpose = _find_value_after(lines, "취득목적", 1) or _find_value_after(lines, "처분목적", 1)

    # 풋옵션
    put_option = ""
    m = re.search(r"풋옵션[^\n]*\n+([^\n]+)", text)
    if m:
        put_option = m.group(1).strip()
    if put_option in ("", "-"):
        put_option = ""

    # 특수관계 판단 (key signal)
    special_relation_hint = ""
    for kw in ("자회사", "종속회사", "계열회사", "계열사", "관계회사"):
        if relationship and kw in relationship:
            special_relation_hint = kw
            break

    # 최대주주·임원과의 관계 (원문에 특수관계 명시되는 경우)
    maj_relation = ""
    m = re.search(r"최대주주ㆍ?임원과\s*상대방과의\s*관계.{0,500}?(본인|계열사|지배|[가-힣]+자회사)", text, re.DOTALL)
    if m:
        maj_relation = m.group(1)

    return {
        "counterparty_name": counterparty_name,
        "counterparty_business": business,
        "counterparty_relationship": relationship,
        "special_relation_hint": special_relation_hint,
        "major_shareholder_relation": maj_relation,
        "amount_won": acquisition_amount,
        "equity_ratio_pct": equity_ratio,
        "asset_ratio_pct": asset_ratio,
        "post_ownership_pct": post_ownership_pct,
        "method": method,
        "purpose": purpose,
        "put_option": put_option,
    }


def _parse_supply_contract_document(html: str) -> dict[str, Any]:
    """단일판매·공급계약 체결/해지 원문 파싱."""
    text = _extract_text(html)
    lines = [l.strip() for l in text.split("\n") if l.strip()]

    # 계약 기본
    contract_type = _find_value_after(lines, "판매ㆍ공급계약 구분", 2) or _find_value_after(lines, "판매·공급계약 구분", 2)
    contract_name = _find_value_after(lines, "체결계약명", 2)

    # 계약내역
    contract_amount = _find_amount_near(text, r"계약금액\(원\)")
    recent_revenue = _find_amount_near(text, r"최근매출액\(원\)")
    revenue_ratio = _find_pct_near(text, r"매출액대비\(%\)")

    # 상대방
    counterparty_name = _find_value_after(lines, "계약상대", 2) or _find_value_after(lines, "거래처", 2)
    # "계약상대\n회사명\n(주)XXX" 패턴 고려
    m = re.search(r"계약상대[^\n]*\n+[^\n]*회사명[^\n]*\n+([^\n]+)", text)
    if m:
        counterparty_name = m.group(1).strip()

    # 상대방과의 관계
    relationship = _extract_relationship(text)

    # 특수관계 힌트
    special_relation_hint = ""
    for kw in ("자회사", "종속회사", "계열회사", "계열사", "관계회사"):
        if relationship and kw in relationship:
            special_relation_hint = kw
            break

    # 계약기간
    period_start = _find_value_after(lines, "계약시작일", 2) or _find_value_after(lines, "계약 시작일", 2)
    period_end = _find_value_after(lines, "계약종료일", 2) or _find_value_after(lines, "계약 종료일", 2)
    signing_date = _find_value_after(lines, "계약체결일", 2) or _find_value_after(lines, "계약 체결일", 2)

    return {
        "contract_type": contract_type,
        "contract_name": contract_name,
        "contract_amount_won": contract_amount,
        "recent_revenue_won": recent_revenue,
        "revenue_ratio_pct": revenue_ratio,
        "counterparty_name": counterparty_name,
        "counterparty_relationship": relationship,
        "special_relation_hint": special_relation_hint,
        "period_start": period_start,
        "period_end": period_end,
        "signing_date": signing_date,
    }


async def _enrich_with_document_details(
    rows: list[dict[str, Any]],
    max_docs: int = 5,
) -> tuple[list[dict[str, Any]], list[str], int]:
    """rows의 앞쪽 N개에 원문 파싱 결과 details 추가.

    각 문서는 독립적으로 fetch 가능 — asyncio.gather 병렬 실행.
    DART rate limit은 client._throttle_api에서 0.1초 간격을 강제하므로
    동시 실행되더라도 실제로는 순차 throttle된다.
    """
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
        if not html:
            continue
        if row.get("type") == "equity_deal":
            row["details"] = _parse_equity_deal_document(html)
        elif row.get("type") == "supply_contract":
            row["details"] = _parse_supply_contract_document(html)
    return rows, warnings, doc_calls


async def _fetch_equity_deals(corp_code: str, corp_name: str, bgn_de: str, end_de: str) -> tuple[list[dict[str, Any]], list[str], int]:
    items, notices, error = await search_filings_by_report_name(
        corp_code=corp_code,
        bgn_de=bgn_de,
        end_de=end_de,
        pblntf_tys=("B", "I"),
        keywords=_EQUITY_DEAL_KEYWORDS,
        strip_spaces=True,
    )
    rows: list[dict[str, Any]] = []
    api_calls = 1  # helper가 내부에서 페이지 순회하지만 기본 1회 이상
    warnings = []
    if error:
        warnings.append(f"타법인주식 거래 조회 실패: {error}")
        return rows, notices + warnings, api_calls

    for item in items:
        report_nm = item.get("report_nm", "")
        rows.append({
            "type": "equity_deal",
            "direction": _classify_equity_deal(report_nm),  # acquire/dispose
            "event_label": "타법인주식·출자증권 거래",
            "rcept_no": item.get("rcept_no", ""),
            "rcept_dt": item.get("rcept_dt", ""),
            "report_nm": report_nm,
            "filer_name": item.get("flr_nm", ""),
            "subsidiary_report": _is_subsidiary_report(report_nm),
            "autonomous_disclosure": _is_autonomous(report_nm),
            "self_filing": _is_self_filing(item.get("flr_nm", ""), corp_name),
            "is_correction": report_nm.startswith("[기재정정]"),
        })
    return rows, notices + warnings, api_calls


async def _fetch_supply_contracts(corp_code: str, corp_name: str, bgn_de: str, end_de: str) -> tuple[list[dict[str, Any]], list[str], int]:
    items, notices, error = await search_filings_by_report_name(
        corp_code=corp_code,
        bgn_de=bgn_de,
        end_de=end_de,
        pblntf_tys=("I",),
        keywords=_SUPPLY_CONTRACT_KEYWORDS,
        strip_spaces=True,
    )
    rows: list[dict[str, Any]] = []
    api_calls = 1
    warnings = []
    if error:
        warnings.append(f"단일판매·공급계약 조회 실패: {error}")
        return rows, notices + warnings, api_calls

    for item in items:
        report_nm = item.get("report_nm", "")
        rows.append({
            "type": "supply_contract",
            "direction": _classify_supply_contract(report_nm),  # conclude/terminate
            "event_label": "단일판매·공급계약",
            "rcept_no": item.get("rcept_no", ""),
            "rcept_dt": item.get("rcept_dt", ""),
            "report_nm": report_nm,
            "filer_name": item.get("flr_nm", ""),
            "subsidiary_report": _is_subsidiary_report(report_nm),
            "autonomous_disclosure": _is_autonomous(report_nm),
            "self_filing": _is_self_filing(item.get("flr_nm", ""), corp_name),
            "is_correction": report_nm.startswith("[기재정정]"),
        })
    return rows, notices + warnings, api_calls


def _unsupported_scope_payload(company_query: str, scope: str) -> dict[str, Any]:
    return ToolEnvelope(
        tool="related_party_transaction",
        status=AnalysisStatus.REQUIRES_REVIEW,
        subject=company_query,
        warnings=[f"`{scope}` scope 미지원."],
        data={
            "query": company_query,
            "scope": scope,
            "supported_scopes": sorted(_SUPPORTED_SCOPES),
        },
    ).to_dict()


async def build_related_party_transaction_payload(
    company_query: str,
    *,
    scope: str = "summary",
    start_date: str = "",
    end_date: str = "",
    include_details: bool = False,
    details_limit: int = 5,
) -> dict[str, Any]:
    if scope not in _SUPPORTED_SCOPES:
        return _unsupported_scope_payload(company_query, scope)

    resolution = await resolve_company_query(company_query)
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        return ToolEnvelope(
            tool="related_party_transaction",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[f"'{company_query}'에 해당하는 회사를 찾지 못했다."],
            data={"query": company_query, "scope": scope},
            next_actions=["company tool로 회사 식별 확인"],
        ).to_dict()
    if resolution.status == AnalysisStatus.AMBIGUOUS:
        return ToolEnvelope(
            tool="related_party_transaction",
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
    window_start, window_end, window_warnings = resolve_date_window(
        start_date=start_date,
        end_date=end_date,
        default_end=date.today(),
        lookback_months=24,
    )
    bgn_de = format_yyyymmdd(window_start)
    end_de = format_yyyymmdd(window_end)

    warnings = list(window_warnings)
    all_rows: list[dict[str, Any]] = []
    total_api_calls = 0

    tasks: list[Any] = []
    if scope in ("summary", "equity_deal"):
        tasks.append(_fetch_equity_deals(selected["corp_code"], selected.get("corp_name", ""), bgn_de, end_de))
    if scope in ("summary", "supply_contract"):
        tasks.append(_fetch_supply_contracts(selected["corp_code"], selected.get("corp_name", ""), bgn_de, end_de))

    results = await asyncio.gather(*tasks)
    for rows, notices, api_calls in results:
        all_rows.extend(rows)
        warnings.extend(notices)
        total_api_calls += api_calls

    all_rows.sort(key=lambda row: (row.get("rcept_dt", ""), row.get("rcept_no", "")), reverse=True)

    # 원문 파싱 보강 (include_details=True)
    if include_details and all_rows:
        all_rows, detail_warnings, doc_calls = await _enrich_with_document_details(all_rows, max_docs=details_limit)
        warnings.extend(detail_warnings)
        total_api_calls += doc_calls

    by_type: dict[str, list[dict[str, Any]]] = {"equity_deal": [], "supply_contract": []}
    acquire_count = dispose_count = conclude_count = terminate_count = 0
    subsidiary_count = autonomous_count = 0
    for row in all_rows:
        by_type.setdefault(row.get("type", ""), []).append(row)
        if row.get("type") == "equity_deal":
            if row.get("direction") == "acquire":
                acquire_count += 1
            elif row.get("direction") == "dispose":
                dispose_count += 1
        elif row.get("type") == "supply_contract":
            if row.get("direction") == "conclude":
                conclude_count += 1
            elif row.get("direction") == "terminate":
                terminate_count += 1
        if row.get("subsidiary_report"):
            subsidiary_count += 1
        if row.get("autonomous_disclosure"):
            autonomous_count += 1

    usage = {
        "dart_api_calls": total_api_calls,
        "mcp_tool_calls": 1,
        "dart_daily_limit_per_minute": 1000,
    }

    # 사건 발견 vs 진짜 partial 분리.
    # include_details=True면 원문 파싱 시도 — `details` 필드가 비어 있으면 partial 실패.
    parsing_failures = 0
    if include_details:
        for row in all_rows[:details_limit]:
            details = row.get("details") or {}
            # details가 dict면 OK, 빈 dict이면 파싱 실패
            if not details:
                parsing_failures += 1
    filing_meta = build_filing_meta(
        filing_count=len(all_rows),
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
        "scope": scope,
        "window": {"start_date": bgn_de, "end_date": end_de},
        "event_count": {
            "total": len(all_rows),
            "equity_deal_total": len(by_type["equity_deal"]),
            "equity_acquire": acquire_count,
            "equity_dispose": dispose_count,
            "supply_contract_total": len(by_type["supply_contract"]),
            "supply_conclude": conclude_count,
            "supply_terminate": terminate_count,
            "subsidiary_reports": subsidiary_count,
            "autonomous_disclosures": autonomous_count,
        },
        **filing_meta,
        "usage": usage,
        "supported_scopes": sorted(_SUPPORTED_SCOPES),
    }

    if scope == "summary":
        data["events_timeline"] = [
            {
                "type": row.get("type", ""),
                "direction": row.get("direction", ""),
                "rcept_dt": row.get("rcept_dt", ""),
                "report_nm": row.get("report_nm", ""),
                "filer": row.get("filer_name", ""),
                "subsidiary": row.get("subsidiary_report", False),
                "autonomous": row.get("autonomous_disclosure", False),
                "rcept_no": row.get("rcept_no", ""),
            }
            for row in all_rows
        ]
    if scope == "equity_deal":
        data["equity_deal_events"] = by_type["equity_deal"]
    if scope == "supply_contract":
        data["supply_contract_events"] = by_type["supply_contract"]

    evidence_refs: list[EvidenceRef] = []
    for row in all_rows[:5]:
        rcept_no = row.get("rcept_no", "")
        if rcept_no:
            evidence_refs.append(
                EvidenceRef(
                    evidence_id=f"ev_rpt_{rcept_no}",
                    source_type=SourceType.DART_API,
                    rcept_no=rcept_no,
                    rcept_dt=format_iso_date(row.get("rcept_dt", "")),
                    report_nm=row.get("report_nm", ""),
                    section="list.json + keyword",
                    note=f"{row.get('type', '')} / {row.get('direction', '')}",
                )
            )

    status = status_from_filing_meta(filing_meta)
    if filing_meta["no_filing"]:
        warnings.append(f"조사 구간 ({bgn_de}~{end_de}) 내 타법인주식 거래·단일공급계약 공시 없음 (정상)")
    elif filing_meta["parsing_failures"] > 0:
        warnings.append(f"원문 파싱 실패 {filing_meta['parsing_failures']}건 — details 필드 비어 있음")

    return ToolEnvelope(
        tool="related_party_transaction",
        status=status,
        subject=selected.get("corp_name", company_query),
        warnings=warnings,
        data=data,
        evidence_refs=evidence_refs,
        next_actions=[
            "개별 거래의 상대방·금액·특수관계 여부는 evidence tool로 원문 확인",
            "자회사 주요경영사항 공시는 모회사 관점에서 연결됨 (중복 집계 주의)",
        ],
    ).to_dict()
