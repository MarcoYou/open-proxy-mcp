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


_SUPPORTED_SCOPES = {"summary", "metrics", "principles", "filings", "timeline"}

# "기업지배구조보고서공시"만 대상. KB금융 등 금융지주의 "연차보고서" 형식은 제외.
_GOV_KEYWORDS = ("기업지배구조보고서공시",)
_EXCLUDE_REPORT_SUBSTR = ("연차보고서",)

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


_COMPLIANCE_VALUES = {"O", "X", "○", "×", "해당없음", "해당 없음"}


def _is_compliance_val(s: str) -> bool:
    return s in _COMPLIANCE_VALUES


def _parse_metrics(text: str) -> list[dict[str, Any]]:
    """15개 핵심지표 표 파싱 (서식 차이 대응).

    표준 지표 라벨을 기준으로 본문에서 위치를 찾고, 각 지표 블록에서 O/X 패턴 2개와
    비고(선택)를 추출. 삼성(비고 전혀 없음), SK하이닉스(일부만 비고), 현대차(매건 비고)
    모두 지원.
    """
    # XBRL 태그 시작 전까지 유효
    end_idx = text.find("krx-cg_")
    scan_text = text[:end_idx] if end_idx != -1 else text

    # 각 지표의 시작 위치 찾기 (prefix 25자 매칭)
    metric_starts: list[tuple[int, str]] = []
    for label in _METRIC_LABELS:
        key = label[:25]
        idx = scan_text.find(key)
        if idx != -1:
            metric_starts.append((idx, label))
    metric_starts.sort()
    if not metric_starts:
        return []

    results: list[dict[str, Any]] = []
    for i, (idx, label) in enumerate(metric_starts):
        # 다음 지표의 시작 또는 XBRL 태그 시작까지를 블록 범위로
        next_idx = metric_starts[i + 1][0] if i + 1 < len(metric_starts) else len(scan_text)
        block = scan_text[idx:next_idx]
        block_lines = [l.strip() for l in block.split("\n") if l.strip()]
        # 첫 줄: 라벨의 첫 줄 (또는 라벨이 한 줄이면 그대로)
        # 라벨이 여러 줄에 걸친 경우도 있으므로 라벨의 "끝"을 찾음
        # 전체 라벨과 매칭되는 누적 줄 건너뛰기
        joined = ""
        start_idx_in_block = 0
        for k, line in enumerate(block_lines):
            joined += line
            if label[:30].replace(" ", "") in joined.replace(" ", ""):
                start_idx_in_block = k + 1
                break

        current = ""
        prior = ""
        note_lines: list[str] = []
        for line in block_lines[start_idx_in_block:]:
            if _is_compliance_val(line):
                if not current:
                    current = line
                elif not prior:
                    prior = line
                else:
                    # 다음 지표 구간으로 넘어감 (방어)
                    break
            else:
                if current and prior:
                    note_lines.append(line)
                # current만 있고 prior 없는데 텍스트면 — 비고로 오탐 방지 차 skip
        if current:
            results.append({
                "label": label,
                "current": current,
                "prior": prior or "",
                "note": " ".join(note_lines)[:200],
            })
    return results


def _parse_principles(text: str) -> list[dict[str, Any]]:
    """세부원칙별 준수여부 텍스트 추출.

    실제 원문 패턴:
      [201100] (세부원칙 1-1) - 기업은 주주에게 주주총회의 일시, 장소 및 의안...
      상기 세부원칙에 대한 준수여부를 간략하게 기술한다. (100자 이내)
      당사는 주주총회 관련 정보를...

    세부원칙 마커(`(세부원칙 X-Y)`)를 찾고 → 그 원칙 설명 → 응답 순서로 추출.
    """
    principles: list[dict[str, Any]] = []

    # '(세부원칙 X-Y)' ~ '상기 세부원칙에 대한 준수여부를' 사이를 원칙 설명, 그 다음 줄이 응답
    # DOTALL 아님, 줄 단위 DOTALL: 원칙 설명은 여러 줄 가능 (실제로는 1-2줄)
    pattern = re.compile(
        r"\(\s*세부원칙\s+([\d\-]+)\s*\)\s*"     # 원칙 번호 (X-Y)
        r"[-\s]*"                                  # 선택적 하이픈
        r"(.+?)"                                   # 원칙 설명 (DOTALL)
        r"\s*상기 세부원칙에 대한 준수여부를[^\n]*"  # 응답 전 안내문
        r"\s*\n+"                                  # 줄바꿈
        r"([^\n]{5,400})",                         # 응답 텍스트 (1줄)
        re.DOTALL,
    )

    for m in pattern.finditer(text):
        principle_num = m.group(1).strip()
        principle_desc = re.sub(r"\s+", " ", m.group(2).strip())[:300]
        response = m.group(3).strip()[:300]
        # 응답이 "당사는", "본 회사는", "회사는" 등으로 시작하는지로 유효성 검증
        if response in ("-", "해당사항없음"):
            response = ""
        principles.append({
            "principle_number": principle_num,
            "principle_description": principle_desc,
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
    rows: list[dict[str, Any]] = []
    for it in items:
        nm = it.get("report_nm", "")
        # 금융지주 "연차보고서" 등 다른 서식 제외
        if any(excl in nm for excl in _EXCLUDE_REPORT_SUBSTR):
            continue
        rows.append({
            "rcept_no": it.get("rcept_no", ""),
            "rcept_dt": it.get("rcept_dt", ""),
            "report_nm": nm,
            "is_correction": nm.startswith("[기재정정]"),
        })
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

    # filings 검색과 company_info 조회는 independent — 병렬 실행.
    filings_task = _fetch_latest_reports(corp_code, years=4)
    info_task = client.get_company_info(corp_code)
    filings_result, info_result = await asyncio.gather(
        filings_task, info_task, return_exceptions=True,
    )

    if isinstance(filings_result, BaseException):
        raise filings_result
    filings, fetch_warnings, _ = filings_result
    warnings: list[str] = list(fetch_warnings)

    # 시장 구분 힌트 (KOSDAQ은 자율공시)
    corp_cls = ""
    if isinstance(info_result, dict):
        corp_cls = (info_result.get("corp_cls") or "").strip()
    elif isinstance(info_result, DartClientError):
        pass
    elif isinstance(info_result, BaseException):
        raise info_result
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
    if scope == "timeline":
        # 최근 N개 filings(최대 5개) 각각 원문 파싱 → 연도별 비교
        # 최신 건은 이미 위에서 파싱됐으므로 그대로 재사용, 나머지는 병렬 fetch.
        timeline_reports: list[dict[str, Any]] = []
        pending = [f for f in filings[:5] if not (f.get("rcept_no") == rcept_no and metrics)]

        async def _safe_doc(rcpt: str) -> tuple[str, str | None]:
            try:
                d = await client.get_document_cached(rcpt)
                return (d.get("html", "") if isinstance(d, dict) else ""), None
            except DartClientError as exc:
                return "", exc.status

        doc_results = await asyncio.gather(*[_safe_doc(f["rcept_no"]) for f in pending]) if pending else []
        doc_by_rcpt: dict[str, tuple[str, str | None]] = {
            f["rcept_no"]: res for f, res in zip(pending, doc_results)
        }

        for f in filings[:5]:
            if f.get("rcept_no") == rcept_no and metrics:
                # 최신 건은 이미 파싱했으므로 재사용
                timeline_reports.append({
                    "rcept_no": rcept_no,
                    "rcept_dt": f.get("rcept_dt", ""),
                    "report_nm": f.get("report_nm", ""),
                    "is_correction": f.get("is_correction", False),
                    "compliance_rate": compliance_rate,
                    "metrics": {m["label"]: m["current"] for m in metrics},
                })
                continue
            h, err = doc_by_rcpt.get(f["rcept_no"], ("", None))
            if err:
                warnings.append(f"{f.get('rcept_dt', '')} 원문 조회 실패: {err}")
                continue
            t = _extract_text(h) if h else ""
            if not t:
                continue
            cr = _parse_compliance_rate(t)
            m_list = _parse_metrics(t)
            timeline_reports.append({
                "rcept_no": f.get("rcept_no", ""),
                "rcept_dt": f.get("rcept_dt", ""),
                "report_nm": f.get("report_nm", ""),
                "is_correction": f.get("is_correction", False),
                "compliance_rate": cr,
                "metrics": {m["label"]: m["current"] for m in m_list},
            })
        # 연도별 지표 전환 탐지 (newer → older)
        transitions: list[dict[str, Any]] = []
        sorted_reports = sorted(timeline_reports, key=lambda r: r.get("rcept_dt", ""))
        for idx in range(1, len(sorted_reports)):
            older = sorted_reports[idx - 1]
            newer = sorted_reports[idx]
            for label in _METRIC_LABELS:
                old_v = older.get("metrics", {}).get(label)
                new_v = newer.get("metrics", {}).get(label)
                if old_v and new_v and old_v != new_v:
                    if old_v in ("X", "×") and new_v in ("O", "○"):
                        direction = "improved"
                    elif old_v in ("O", "○") and new_v in ("X", "×"):
                        direction = "regressed"
                    else:
                        direction = "changed"
                    transitions.append({
                        "label": label,
                        "from_dt": older.get("rcept_dt", ""),
                        "from_val": old_v,
                        "to_dt": newer.get("rcept_dt", ""),
                        "to_val": new_v,
                        "direction": direction,
                    })
        data["timeline"] = timeline_reports
        data["transitions"] = transitions

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
