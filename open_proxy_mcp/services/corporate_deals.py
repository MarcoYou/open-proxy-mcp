"""corporate_deals data tool.

타법인주식·출자증권 취득/처분(지분 인수·매각) 공시. 계열사 출자·회수, 일감몰아주기·내부거래
모니터링 소스. 단일판매·공급계약(체결/해지)은 order_contracts로 일원화(2026-06-14).

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
from open_proxy_mcp.services.filing_search import search_filings_by_report_name


_SUPPORTED_SCOPES = {"summary", "equity_deal"}


# 타법인주식 거래 — 취득/양수 및 처분/양도
_EQUITY_DEAL_KEYWORDS = (
    "타법인주식및출자증권양수결정",
    "타법인주식및출자증권양도결정",
    "타법인주식및출자증권취득결정",
    "타법인주식및출자증권처분결정",
)

# 단일판매·공급계약(체결/해지)은 order_contracts로 일원화(2026-06-14) — 여기선 타법인주식 거래만.


def _classify_equity_deal(report_nm: str) -> str:
    compact = (report_nm or "").replace(" ", "")
    if "양수" in compact or "취득" in compact:
        return "acquire"
    if "양도" in compact or "처분" in compact:
        return "dispose"
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
    r"""라벨 근처 % 값 추출.

    라벨과 값 사이는 같은 줄 주석("(A/C)" 등, 개행·숫자 제외)까지만 허용하고 값은 라벨 줄 또는
    바로 다음 줄에서만 취한다. 잡은 숫자 뒤에 콤마/숫자가 오면 거부 — 값이 공란("-")일 때
    `[^\d]*`가 개행을 건너뛰어 아래 취득금액의 앞자리를 오취득하는 leak 방지
    (260713 검증: CJ 자기자본대비 공란인데 취득금액 407,725,920,000의 "407"을 잡던 버그).
    """
    m = re.search(label_pattern + r"[^\n\d]*\n?[ \t]*(\d+(?:\.\d+)?)(?![\d,])", text)
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
            row["_detail_status"] = "fetch_error"  # fetch 실패 — 파싱 실패와 구분(별도 카운트)
            continue
        doc_calls += 1
        if not html:
            row["_detail_status"] = "empty_doc"
            continue
        if row.get("type") == "equity_deal":
            details = _parse_equity_deal_document(html)
            row["details"] = details
            # 진짜 파싱 실패 = 문서는 받았으나 앵커 필드(상대회사명·거래금액) 둘 다 공란.
            # _parse_*는 값이 비어도 키 있는 dict를 반환하므로 "빈 dict" 검사로는 못 잡는다.
            core_ok = bool(details.get("counterparty_name")) or bool(details.get("amount_won"))
            row["_detail_status"] = "ok" if core_ok else "parse_empty"
        else:
            row["_detail_status"] = "ok"  # details 대상 아님(비-equity) — 실패로 세지 않음
    return rows, warnings, doc_calls


async def _fetch_equity_deals(corp_code: str, corp_name: str, bgn_de: str, end_de: str) -> tuple[list[dict[str, Any]], list[str], int]:
    items, notices, error = await search_filings_by_report_name(
        corp_code=corp_code,
        bgn_de=bgn_de,
        end_de=end_de,
        pblntf_tys="",
        pblntf_detail_ty=["B001", "I001"],  # 타법인주식 양수도(B001)/취득결정(I001), 차집합0 검증
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


def _unsupported_scope_payload(company_query: str, scope: str) -> dict[str, Any]:
    return ToolEnvelope(
        tool="corporate_deals",
        status=AnalysisStatus.REQUIRES_REVIEW,
        subject=company_query,
        warnings=[f"`{scope}` scope 미지원."],
        data={
            "query": company_query,
            "scope": scope,
            "supported_scopes": sorted(_SUPPORTED_SCOPES),
        },
    ).to_dict()


async def build_corporate_deals_payload(
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
            tool="corporate_deals",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[company_not_found_warning(company_query)],
            data={"query": company_query, "scope": scope},
            next_actions=["company tool로 회사 식별 확인"],
        ).to_dict()
    if resolution.status == AnalysisStatus.AMBIGUOUS:
        return ToolEnvelope(
            tool="corporate_deals",
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

    by_type: dict[str, list[dict[str, Any]]] = {"equity_deal": []}
    acquire_count = dispose_count = 0
    subsidiary_count = autonomous_count = 0
    for row in all_rows:
        by_type.setdefault(row.get("type", ""), []).append(row)
        if row.get("type") == "equity_deal":
            if row.get("direction") == "acquire":
                acquire_count += 1
            elif row.get("direction") == "dispose":
                dispose_count += 1
        if row.get("subsidiary_report"):
            subsidiary_count += 1
        if row.get("autonomous_disclosure"):
            autonomous_count += 1

    usage = {
        "dart_api_calls": total_api_calls,
        "mcp_tool_calls": 1,
        "dart_daily_limit_per_minute": 1000,
    }

    # 사건 발견 vs 진짜 partial 분리. _enrich가 각 대상 row에 _detail_status를 남긴다:
    #   ok / parse_empty(문서는 받았으나 핵심필드 공란=진짜 파싱실패) / fetch_error / empty_doc.
    # 과거엔 "details 빈 dict" 검사라 _parse_*가 항상 키-있는 dict를 반환해 진짜 파싱실패를 못 셌고,
    # 대신 fetch 실패·비대상 row만 잡혀 지표가 오도됐다 (260713 검증).
    parsing_failures = 0
    fetch_failures = 0
    if include_details:
        for row in all_rows[:details_limit]:
            st = row.get("_detail_status")
            if st == "parse_empty":
                parsing_failures += 1
            elif st in ("fetch_error", "empty_doc"):
                fetch_failures += 1
    filing_meta = build_filing_meta(
        filing_count=len(all_rows),
        parsing_failures=parsing_failures,
    )
    filing_meta["fetch_failures"] = fetch_failures

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
        # 내부 마커(_detail_status) 제거 후 노출
        data["equity_deal_events"] = [
            {k: v for k, v in row.items() if not k.startswith("_")}
            for row in by_type["equity_deal"]
        ]

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
        warnings.append(f"조사 구간 ({bgn_de}~{end_de}) 내 타법인주식 거래 공시 없음 (정상)")
    else:
        if filing_meta["parsing_failures"] > 0:
            warnings.append(f"원문 파싱 실패 {filing_meta['parsing_failures']}건 — 문서는 조회됐으나 상대회사명·거래금액 추출 실패")
        if filing_meta.get("fetch_failures", 0) > 0:
            warnings.append(f"원문 조회 실패 {filing_meta['fetch_failures']}건 — DART 문서 fetch 오류(파싱 이전 단계)")

    return ToolEnvelope(
        tool="corporate_deals",
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
