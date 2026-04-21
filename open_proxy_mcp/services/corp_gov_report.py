"""v2 corp_gov_report data tool.

기업지배구조보고서 (2024년 사업연도부터 전체 KOSPI 의무공시).
- DART 전용 구조화 API 없음 → list.json + 원문 파싱 방식
- 원문에서 15개 핵심지표 준수 여부, 기업개요, 준수율 추출
- KOSDAQ 대상은 자율공시 (일부만 제출)
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
    build_usage,
)
from open_proxy_mcp.services.date_utils import format_iso_date, format_yyyymmdd
from open_proxy_mcp.services.filing_search import search_filings_by_report_name


_SUPPORTED_SCOPES = {"summary", "metrics", "principles", "filings"}

_GOV_KEYWORDS = ("기업지배구조보고서공시", "기업지배구조보고서")

# 15개 핵심지표 표준 라벨(원문 변형 허용)
_METRIC_LABELS = [
    "주주총회 4주 전에 소집공고 실시",
    "전자투표 실시",
    "주주총회의 집중일 이외 개최",
    "현금 배당관련 예측가능성 제공",
    "배당정책 및 배당실시 계획을 연 1회 이상 주주에게 통지",
    "최고경영자 승계정책 마련 및 운영",
    "위험관리 등 내부통제정책 마련 및 운영",
    "사외이사가 이사회 의장인지 여부",
    "집중투표제 채택",
    "기업가치 훼손 또는 주주권익 침해에 책임이 있는 자의 임원 선임을 방지하기 위한 정책 수립 여부",
    "이사회 구성원 모두 단일성(性)이 아님",
    "독립적인 내부감사부서 (내부감사업무 지원 조직)의 설치",
    "내부감사기구에 회계 또는 재무 전문가 존재 여부",
    "내부감사기구가 분기별 1회 이상 경영진 참석 없이 외부감사인과 회의 개최",
    "경영 관련 중요정보에 내부감사기구가 접근할 수 있는 절차 마련 여부",
]


def _extract_text(html: str) -> str:
    soup = BeautifulSoup(html or "", "lxml")
    return soup.get_text("\n", strip=True)


def _parse_compliance_rate(text: str) -> float | None:
    """'준수율' 근처 숫자 추출."""
    m = re.search(r"준수율\s*\n+\s*(\d+(?:\.\d+)?)", text)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            return None
    return None


def _parse_company_summary(text: str) -> dict[str, Any]:
    """기업개요(표 1-0-0): 최대주주, 지분율, 소액주주, 업종, 기업집단, 요약 재무."""
    out: dict[str, Any] = {}

    def _after(label: str, max_lines: int = 2) -> str:
        m = re.search(rf"{re.escape(label)}\s*\n+([^\n]+)", text)
        return m.group(1).strip() if m else ""

    out["max_shareholder"] = _after("최대주주")
    out["max_shareholder_pct"] = _after("최대주주등의 지분율")
    out["minority_shareholder_pct"] = _after("소액주주 지분율")
    out["industry"] = _after("업종")
    out["main_products"] = _after("주요 제품")
    out["corporate_group"] = _after("기업집단명")
    out["reporting_period_end"] = _after("공시대상 기간 종료일")

    # 요약 재무 (당기 기준)
    m = re.search(r"\(연결\)\s*매출액\s*\n+([\d,]+)", text)
    if m:
        out["revenue_current"] = m.group(1)
    m = re.search(r"\(연결\)\s*영업이익\s*\n+([\d,]+)", text)
    if m:
        out["operating_income_current"] = m.group(1)
    m = re.search(r"\(연결\)\s*당기순이익\s*\n+([\d,]+)", text)
    if m:
        out["net_income_current"] = m.group(1)
    m = re.search(r"\(연결\)\s*자산총액\s*\n+([\d,]+)", text)
    if m:
        out["total_assets_current"] = m.group(1)
    return out


def _parse_metrics(text: str) -> list[dict[str, Any]]:
    """15개 핵심지표 표 파싱.

    원문 패턴 (현대차 2024 기준):
      핵심지표
      (공시대상기간)준수여부
      (직전 공시대상기간)준수여부
      비고
      [지표명]
      [O/X/해당없음]
      [O/X/해당없음]
      [비고 최대 100자]
    """
    # 지표 표 블록 찾기
    start_idx = text.find("핵심지표\n(공시대상기간)")
    if start_idx == -1:
        start_idx = text.find("지배구조핵심지표 준수 현황")
    if start_idx == -1:
        return []
    # XBRL 태그 시작 또는 II. 다음 섹션으로 끝
    end_idx = text.find("krx-cg_", start_idx)
    if end_idx == -1:
        end_idx = text.find("II.", start_idx + 50)
    if end_idx == -1:
        end_idx = start_idx + 5000
    block = text[start_idx:end_idx]
    lines = [l.strip() for l in block.split("\n") if l.strip()]

    # 헤더 이후 4줄씩 반복
    metrics: list[dict[str, Any]] = []
    i = 0
    header_found = False
    while i < len(lines):
        if not header_found:
            if lines[i].startswith("비고"):
                header_found = True
            i += 1
            continue
        # 4줄: 지표명, 당기 준수, 전기 준수, 비고
        if i + 3 >= len(lines):
            break
        label = lines[i]
        current = lines[i + 1]
        prior = lines[i + 2]
        note = lines[i + 3]
        # 라벨이 _METRIC_LABELS의 prefix와 매칭되면 수집
        matched = False
        for known in _METRIC_LABELS:
            if known[:15] in label or label[:15] in known:
                matched = True
                break
        # 준수값이 O/X/해당없음 형식이어야 유효
        valid_val = lambda v: v in ("O", "X", "○", "×", "해당없음", "해당 없음") or re.match(r"^(준수|미준수)$", v)
        if matched or (valid_val(current) and valid_val(prior)):
            metrics.append({
                "label": label,
                "current": current,
                "prior": prior,
                "note": note[:200],
            })
            i += 4
        else:
            i += 1
    return metrics


def _parse_principles(text: str) -> list[dict[str, Any]]:
    """세부원칙별 준수여부 텍스트(100자 이내) 추출."""
    principles: list[dict[str, Any]] = []
    for m in re.finditer(
        r"([가-힣A-Za-z0-9\s·ㆍ\(\)]+?)[\n]+상기 세부원칙에 대한 준수여부를[^\n]*\n+([^\n]{5,300})",
        text,
    ):
        principle_desc = m.group(1).strip().split("\n")[-1][:120]
        response = m.group(2).strip()[:200]
        principles.append({
            "principle_snippet": principle_desc,
            "response": response,
        })
    return principles


async def _fetch_latest_reports(
    corp_code: str,
    years: int = 3,
) -> tuple[list[dict[str, Any]], list[str], int]:
    """최근 N년 기업지배구조보고서 리스트."""
    client = get_dart_client()
    today = date.today()
    start = date(today.year - years, 1, 1)
    calls_before = client.api_call_snapshot()
    items, notices, error = await search_filings_by_report_name(
        corp_code=corp_code,
        bgn_de=format_yyyymmdd(start),
        end_de=format_yyyymmdd(today),
        pblntf_tys=("I",),
        keywords=_GOV_KEYWORDS,
        strip_spaces=True,
    )
    api_calls = client.api_call_snapshot() - calls_before
    warnings: list[str] = list(notices)
    if error:
        warnings.append(f"기업지배구조보고서 검색 실패: {error}")
        return [], warnings, api_calls
    rows = [
        {
            "rcept_no": it.get("rcept_no", ""),
            "rcept_dt": it.get("rcept_dt", ""),
            "report_nm": it.get("report_nm", ""),
        }
        for it in items
    ]
    return rows, warnings, api_calls


def _unsupported_scope_payload(company_query: str, scope: str) -> dict[str, Any]:
    return ToolEnvelope(
        tool="corp_gov_report",
        status=AnalysisStatus.REQUIRES_REVIEW,
        subject=company_query,
        warnings=[f"`{scope}` scope 미지원."],
        data={
            "query": company_query,
            "scope": scope,
            "supported_scopes": sorted(_SUPPORTED_SCOPES),
            "usage": build_usage(0),
        },
    ).to_dict()


async def build_corp_gov_report_payload(
    company_query: str,
    *,
    scope: str = "summary",
    year: int = 0,
) -> dict[str, Any]:
    if scope not in _SUPPORTED_SCOPES:
        return _unsupported_scope_payload(company_query, scope)

    client = get_dart_client()
    calls_start = client.api_call_snapshot()

    resolution = await resolve_company_query(company_query)
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        return ToolEnvelope(
            tool="corp_gov_report",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[f"'{company_query}'에 해당하는 회사를 찾지 못했다."],
            data={
                "query": company_query,
                "scope": scope,
                "usage": build_usage(client.api_call_snapshot() - calls_start),
            },
            next_actions=["company tool로 회사 식별 확인"],
        ).to_dict()
    if resolution.status == AnalysisStatus.AMBIGUOUS:
        return ToolEnvelope(
            tool="corp_gov_report",
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
                "usage": build_usage(client.api_call_snapshot() - calls_start),
            },
        ).to_dict()

    selected = resolution.selected
    corp_code = selected["corp_code"]

    filings, fetch_warnings, _ = await _fetch_latest_reports(corp_code, years=4)
    warnings: list[str] = list(fetch_warnings)

    # 시장 구분 힌트 (KOSDAQ은 자율공시)
    corp_cls = ""
    try:
        info = await client.get_company_info(corp_code)
        corp_cls = (info.get("corp_cls") or "").strip()
    except DartClientError:
        pass
    market_label = {"Y": "KOSPI", "K": "KOSDAQ", "N": "KONEX", "E": "기타"}.get(corp_cls, corp_cls or "미상")
    if corp_cls == "K" and not filings:
        warnings.append("KOSDAQ은 기업지배구조보고서 자율공시 — 제출되지 않았을 수 있음.")

    # 필요한 연도 결정
    target_filing = None
    if year:
        year_str = str(year)
        for f in filings:
            dt = f.get("rcept_dt", "")
            # 보고서 rcept_dt는 제출연도. 공시대상연도는 -1일 수 있으므로 둘 다 체크
            if dt.startswith(year_str) or dt.startswith(str(year + 1)):
                target_filing = f
                break
    if not target_filing and filings:
        target_filing = filings[0]  # 최신

    data: dict[str, Any] = {
        "query": company_query,
        "company_id": _company_id(selected),
        "canonical_name": selected.get("corp_name", ""),
        "identifiers": {
            "ticker": selected.get("stock_code", ""),
            "corp_code": corp_code,
        },
        "market": market_label,
        "mandatory": corp_cls == "Y",  # KOSPI면 의무, KOSDAQ은 자율
        "scope": scope,
        "filings_count": len(filings),
        "supported_scopes": sorted(_SUPPORTED_SCOPES),
    }

    if scope == "filings":
        data["filings"] = filings[:10]
        data["usage"] = build_usage(client.api_call_snapshot() - calls_start)
        status = AnalysisStatus.EXACT if filings else AnalysisStatus.PARTIAL
        if not filings:
            warnings.append("조회된 기업지배구조보고서 없음")
        return ToolEnvelope(
            tool="corp_gov_report",
            status=status,
            subject=selected.get("corp_name", company_query),
            warnings=warnings,
            data=data,
            evidence_refs=[
                EvidenceRef(
                    evidence_id=f"ev_cgr_filing_{f.get('rcept_no', '')}",
                    source_type=SourceType.DART_API,
                    rcept_no=f.get("rcept_no", ""),
                    rcept_dt=format_iso_date(f.get("rcept_dt", "")),
                    report_nm=f.get("report_nm", ""),
                    section="기업지배구조보고서 list",
                )
                for f in filings[:5]
            ],
        ).to_dict()

    if not target_filing:
        data["usage"] = build_usage(client.api_call_snapshot() - calls_start)
        warnings.append("기업지배구조보고서 원문을 찾지 못함")
        return ToolEnvelope(
            tool="corp_gov_report",
            status=AnalysisStatus.PARTIAL,
            subject=selected.get("corp_name", company_query),
            warnings=warnings,
            data=data,
            next_actions=["scope=filings로 제출 이력 확인"],
        ).to_dict()

    # 원문 파싱
    rcept_no = target_filing["rcept_no"]
    try:
        doc = await client.get_document_cached(rcept_no)
        html = doc.get("html", "") if isinstance(doc, dict) else ""
    except DartClientError as exc:
        warnings.append(f"원문 조회 실패: {exc.status}")
        html = ""

    text = _extract_text(html) if html else ""
    compliance_rate = _parse_compliance_rate(text) if text else None
    summary_block = _parse_company_summary(text) if text else {}
    metrics = _parse_metrics(text) if text else []
    principles = _parse_principles(text) if text else []

    compliant = sum(1 for m in metrics if m.get("current") in ("O", "○", "준수"))
    non_compliant = sum(1 for m in metrics if m.get("current") in ("X", "×", "미준수"))

    report_meta = {
        "rcept_no": rcept_no,
        "rcept_dt": target_filing.get("rcept_dt", ""),
        "report_nm": target_filing.get("report_nm", ""),
        "reporting_period_end": summary_block.get("reporting_period_end", ""),
        "compliance_rate": compliance_rate,
        "metrics_parsed_count": len(metrics),
        "metrics_compliant": compliant,
        "metrics_non_compliant": non_compliant,
    }
    data["report_meta"] = report_meta
    data["company_overview"] = summary_block

    if scope == "summary":
        # metrics 압축 요약
        data["metrics_summary"] = [
            {"label": m["label"], "current": m["current"]}
            for m in metrics
        ]
    if scope == "metrics":
        data["metrics"] = metrics
    if scope == "principles":
        data["principles"] = principles[:30]  # 최대 30개

    data["usage"] = build_usage(client.api_call_snapshot() - calls_start)

    evidence_refs = [
        EvidenceRef(
            evidence_id=f"ev_cgr_{rcept_no}",
            source_type=SourceType.DART_API,
            rcept_no=rcept_no,
            rcept_dt=format_iso_date(target_filing.get("rcept_dt", "")),
            report_nm=target_filing.get("report_nm", "기업지배구조보고서"),
            section="기업지배구조보고서 원문",
            note=f"준수율 {compliance_rate}% | 지표 {compliant}/{len(metrics)} 준수" if compliance_rate is not None else f"지표 파싱 {len(metrics)}개",
        )
    ]

    if len(metrics) < 10:
        warnings.append(f"핵심지표 파싱 {len(metrics)}개만 추출됨 — 원문 서식 차이 가능성")
    status = AnalysisStatus.EXACT if metrics else AnalysisStatus.PARTIAL

    return ToolEnvelope(
        tool="corp_gov_report",
        status=status,
        subject=selected.get("corp_name", company_query),
        warnings=warnings,
        data=data,
        evidence_refs=evidence_refs,
        next_actions=[
            "scope=metrics로 15개 지표 상세 확인",
            "scope=principles로 세부원칙 응답 텍스트 확인",
            "scope=filings로 연도별 변화 추적",
        ],
    ).to_dict()
