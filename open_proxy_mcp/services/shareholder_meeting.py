"""v2 shareholder_meeting facade 서비스."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
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
)
from open_proxy_mcp.tools.formatters import _parse_agm_result_table
from open_proxy_mcp.tools.parser import (
    parse_agenda_xml,
    parse_compensation_xml,
    parse_corrections_xml,
    parse_meeting_info_xml,
    parse_personnel_xml,
    validate_agenda_result,
)


_SUPPORTED_SCOPES = {"summary", "agenda", "board", "compensation", "results"}
_MEETING_TYPE_MAP = {
    "annual": "정기",
    "extraordinary": "임시",
}
_ALLOWED_MEETING_TYPES = {"auto", "annual", "extraordinary"}
_NOTICE_LEAD_BUFFER_DAYS = 90


def _agenda_nodes(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    nodes: list[dict[str, Any]] = []
    for item in items:
        agenda_id = item["number"].replace("제", "").replace("호", "")
        nodes.append({
            "agenda_id": agenda_id,
            "number": item.get("number", ""),
            "title": item.get("title", ""),
            "source": item.get("source"),
            "conditional": item.get("conditional"),
            "children": _agenda_nodes(item.get("children", [])),
        })
    return nodes


def _flatten_agendas(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for item in items:
        flattened.append({
            "number": item.get("number", ""),
            "title": item.get("title", ""),
            "source": item.get("source"),
            "conditional": item.get("conditional"),
        })
        flattened.extend(_flatten_agendas(item.get("children", [])))
    return flattened


def _normalize_notice_row(item: dict[str, Any], meeting_info: dict[str, Any]) -> dict[str, Any]:
    return {
        "rcept_no": item.get("rcept_no", ""),
        "report_name": (item.get("report_nm") or "").strip(),
        "disclosure_date": item.get("rcept_dt", ""),
        "filer_name": item.get("flr_nm", ""),
        "meeting_type": meeting_info.get("meeting_type"),
        "meeting_term": meeting_info.get("meeting_term"),
        "is_correction": meeting_info.get("is_correction", False),
        "datetime": meeting_info.get("datetime"),
        "location": meeting_info.get("location"),
    }


async def _candidate_notices_range(
    corp_code: str,
    meeting_type_label: str,
    bgn_de: str,
    end_de: str,
) -> list[dict[str, Any]]:
    client = get_dart_client()
    result = await client.search_filings(
        corp_code=corp_code,
        bgn_de=bgn_de,
        end_de=end_de,
        pblntf_ty="E",
        page_count=100,
    )
    filings = [
        item for item in result.get("list", [])
        if "소집" in (item.get("report_nm") or "")
    ]
    filings.sort(key=lambda row: row.get("rcept_dt", ""))
    if not filings:
        return []

    docs = await asyncio.gather(*[
        client.get_document_cached(item["rcept_no"]) for item in filings
    ])

    matched: list[dict[str, Any]] = []
    for item, doc in zip(filings, docs):
        text = doc.get("text", "")
        html = doc.get("html", "")
        info = parse_meeting_info_xml(text, html=html)
        normalized = _normalize_notice_row(item, info)
        if normalized["meeting_type"] == meeting_type_label:
            matched.append(normalized)
    return matched


async def _candidate_notices(
    corp_code: str,
    meeting_type_label: str,
    year: int,
) -> list[dict[str, Any]]:
    return await _candidate_notices_in_meeting_window(
        corp_code,
        meeting_type_label,
        date(year, 1, 1),
        date(year, 12, 31),
    )


async def _candidate_notices_in_meeting_window(
    corp_code: str,
    meeting_type_label: str,
    meeting_start: date,
    meeting_end: date,
) -> list[dict[str, Any]]:
    search_start = meeting_start - timedelta(days=_NOTICE_LEAD_BUFFER_DAYS)
    notices = await _candidate_notices_range(
        corp_code,
        meeting_type_label,
        search_start.strftime("%Y%m%d"),
        meeting_end.strftime("%Y%m%d"),
    )
    matched: list[dict[str, Any]] = []
    for notice in notices:
        meeting_date = _parse_notice_meeting_date(notice.get("datetime", ""))
        if meeting_date and meeting_start <= meeting_date <= meeting_end:
            matched.append(notice)
    return matched


def _pick_latest_notice(notices: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not notices:
        return None
    notices = sorted(notices, key=lambda row: (row.get("disclosure_date", ""), row.get("rcept_no", "")))
    return notices[-1]


def _correction_summary(html: str) -> dict[str, Any] | None:
    parsed = parse_corrections_xml(html)
    if not parsed:
        return None
    return {
        "is_correction": parsed.get("is_correction", False),
        "date": parsed.get("date"),
        "original_date": parsed.get("original_date"),
        "reason": parsed.get("reason"),
        "items": parsed.get("items", []),
    }


def _parse_notice_meeting_date(datetime_text: str) -> date | None:
    if not datetime_text:
        return None
    match = None
    compact = re.sub(r"\s+", "", datetime_text)
    for pattern in (
        r"(\d{4})[.-](\d{1,2})[.-](\d{1,2})",
        r"(\d{4})년(\d{1,2})월(\d{1,2})일",
    ):
        match = re.search(pattern, compact)
        if match:
            break
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


async def _find_meeting_result_filing(
    corp_code: str,
    target_year: int,
    notice: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    client = get_dart_client()
    try:
        filings = await client.search_filings(
            corp_code=corp_code,
            bgn_de=f"{target_year}0101",
            end_de=f"{target_year}1231",
            pblntf_ty="I",
            page_count=100,
        )
    except DartClientError as exc:
        return None, f"주주총회결과 공시 검색 실패: {exc.status}"

    result_items = [
        item for item in filings.get("list", [])
        if "주주총회결과" in (item.get("report_nm") or "")
    ]
    if not result_items:
        return None, "주주총회결과 공시를 찾지 못했다."

    meeting_date = _parse_notice_meeting_date(notice.get("datetime", ""))

    def sort_key(item: dict[str, Any]) -> tuple[int, str]:
        rcept_dt = item.get("rcept_dt", "")
        if meeting_date and len(rcept_dt) == 8:
            try:
                filing_date = datetime.strptime(rcept_dt, "%Y%m%d").date()
                distance = abs((filing_date - meeting_date).days)
            except ValueError:
                distance = 9999
        else:
            distance = 9999
        return (distance, rcept_dt)

    result_items.sort(key=sort_key)
    return result_items[0], None


def _result_reference(result_filing: dict[str, Any] | None) -> dict[str, Any] | None:
    if not result_filing:
        return None

    rcept_no = result_filing.get("rcept_no", "")
    whitelist_ok = bool(rcept_no and len(rcept_no) == 14 and rcept_no[8:10] == "80")
    kind_acptno = rcept_no[:8] + "00" + rcept_no[10:] if whitelist_ok else None
    return {
        "rcept_no": rcept_no,
        "report_name": result_filing.get("report_nm", ""),
        "disclosure_date": result_filing.get("rcept_dt", ""),
        "kind_acptno": kind_acptno,
        "whitelist_ok": whitelist_ok,
    }


def _meeting_phase(
    meeting_info: dict[str, Any],
    result_filing: dict[str, Any] | None,
    result_reference: dict[str, Any] | None,
) -> tuple[str, str]:
    meeting_date = _parse_notice_meeting_date(meeting_info.get("datetime", ""))
    today = date.today()

    if result_filing:
        if result_reference and result_reference.get("whitelist_ok"):
            return "post_result", "available"
        return "post_result", "requires_review"

    if meeting_date:
        if meeting_date > today:
            return "pre_meeting", "not_due_yet"
        return "post_meeting_pre_result", "pending_or_missing"

    return "undetermined", "unknown"


def _phase_priority(meeting_phase: str) -> int:
    return {
        "pre_meeting": 3,
        "post_result": 2,
        "post_meeting_pre_result": 1,
        "undetermined": 0,
    }.get(meeting_phase, 0)


def _candidate_meta(candidate: dict[str, Any]) -> dict[str, Any]:
    notice = candidate["notice"]
    result_reference = candidate.get("result_reference") or {}
    meeting_date = candidate.get("meeting_date")
    return {
        "meeting_type": candidate.get("meeting_type"),
        "meeting_phase": candidate.get("meeting_phase"),
        "result_status": candidate.get("result_status"),
        "meeting_date": meeting_date.isoformat() if meeting_date else None,
        "notice_rcept_no": notice.get("rcept_no", ""),
        "notice_date": notice.get("disclosure_date", ""),
        "notice_report_name": notice.get("report_name", ""),
        "result_rcept_no": result_reference.get("rcept_no", ""),
        "result_date": result_reference.get("disclosure_date", ""),
    }


def _meeting_presence_flag(has_annual: bool, has_extraordinary: bool) -> str:
    if has_annual and has_extraordinary:
        return "annual_and_extraordinary"
    if has_annual:
        return "annual_only"
    if has_extraordinary:
        return "extraordinary_only"
    return "none"


def _auto_window_end(target_year: int | None) -> date:
    today = date.today()
    if not target_year:
        return today
    if target_year < today.year:
        return date(target_year, 12, 31)
    return today


def _auto_selection_basis(candidate: dict[str, Any], scope: str, candidates: list[dict[str, Any]]) -> str:
    if len(candidates) == 1:
        return "후보가 1개라 해당 회차를 자동 선택했다."

    if scope == "results":
        if candidate.get("result_status") == "available":
            return "결과 조회 요청이라 결과공시가 확인된 회차 중 가장 최신 회차를 선택했다."
        return "결과 조회 요청이었지만 결과공시가 확인된 회차가 없어 가장 관련성 높은 회차를 선택했다."

    basis = []
    basis.append("일반 주총 조회라 정기/임시를 가리지 않고 가장 현재적인 회차를 우선했다.")
    phase = candidate.get("meeting_phase")
    if phase == "pre_meeting":
        basis.append("아직 회의 전인 예정 회차라 현재 안건 검토 대상에 가깝다.")
    elif phase == "post_result":
        basis.append("결과공시까지 확인된 최신 회차다.")
    elif phase == "post_meeting_pre_result":
        basis.append("회의는 종료됐지만 결과공시는 아직 확인되지 않았다.")
    return " ".join(basis)


def _auto_rank_key(candidate: dict[str, Any], scope: str) -> tuple[int, int, int, int]:
    meeting_date = candidate.get("meeting_date")
    meeting_ordinal = meeting_date.toordinal() if meeting_date else 0
    is_annual = 1 if candidate.get("meeting_type") == "annual" else 0
    has_result = 1 if candidate.get("result_status") == "available" else 0
    phase_priority = _phase_priority(candidate.get("meeting_phase", ""))

    if scope == "results":
        return (has_result, meeting_ordinal, phase_priority, is_annual)
    return (phase_priority, meeting_ordinal, has_result, is_annual)


async def _build_candidate(
    corp_code: str,
    meeting_type: str,
    target_year: int,
    notice: dict[str, Any],
) -> dict[str, Any]:
    meeting_date = _parse_notice_meeting_date(notice.get("datetime", ""))
    result_search_year = meeting_date.year if meeting_date else target_year
    result_filing, result_filing_warning = await _find_meeting_result_filing(
        corp_code,
        result_search_year,
        notice,
    )
    result_reference = _result_reference(result_filing)
    meeting_phase, result_status = _meeting_phase(notice, result_filing, result_reference)
    return {
        "meeting_type": meeting_type,
        "meeting_type_label": _MEETING_TYPE_MAP[meeting_type],
        "notice": notice,
        "meeting_date": meeting_date,
        "result_search_year": result_search_year,
        "result_filing": result_filing,
        "result_filing_warning": result_filing_warning,
        "result_reference": result_reference,
        "meeting_phase": meeting_phase,
        "result_status": result_status,
    }


async def _select_notice_candidate(
    corp_code: str,
    target_year: int,
    requested_meeting_type: str,
    scope: str,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str | None, str | None]:
    if requested_meeting_type == "auto":
        window_end = _auto_window_end(target_year)
        window_start = window_end - timedelta(days=365)
        annual_notices, extraordinary_notices = await asyncio.gather(
            _candidate_notices_in_meeting_window(
                corp_code,
                _MEETING_TYPE_MAP["annual"],
                window_start,
                window_end,
            ),
            _candidate_notices_in_meeting_window(
                corp_code,
                _MEETING_TYPE_MAP["extraordinary"],
                window_start,
                window_end,
            ),
        )
        latest_by_type: list[tuple[str, dict[str, Any]]] = []
        annual_latest = _pick_latest_notice(annual_notices)
        extraordinary_latest = _pick_latest_notice(extraordinary_notices)
        if annual_latest:
            latest_by_type.append(("annual", annual_latest))
        if extraordinary_latest:
            latest_by_type.append(("extraordinary", extraordinary_latest))
        if not latest_by_type:
            return None, [], None, f"최근 12개월 내 정기/임시 주주총회 소집공고를 찾지 못했다."

        candidates = await asyncio.gather(*[
            _build_candidate(corp_code, meeting_type, target_year, notice)
            for meeting_type, notice in latest_by_type
        ])
        selected = sorted(candidates, key=lambda row: _auto_rank_key(row, scope), reverse=True)[0]
        alternatives = [_candidate_meta(candidate) for candidate in candidates if candidate is not selected]
        basis = _auto_selection_basis(selected, scope, candidates)
        return selected, alternatives, basis, None

    meeting_type_label = _MEETING_TYPE_MAP[requested_meeting_type]
    notices = await _candidate_notices(corp_code, meeting_type_label, target_year)
    latest_notice = _pick_latest_notice(notices)
    if not latest_notice:
        return None, [], None, f"{target_year}년 {meeting_type_label} 주주총회 소집공고를 찾지 못했다."
    selected = await _build_candidate(corp_code, requested_meeting_type, target_year, latest_notice)
    basis = f"사용자가 {meeting_type_label} 주주총회를 명시해 해당 회차를 선택했다."
    return selected, [], basis, None


async def _meeting_window_coverage(
    corp_code: str,
    anchor_date: date,
    months: int = 12,
) -> dict[str, Any]:
    end_date = anchor_date
    start_date = anchor_date - timedelta(days=365)
    annual_notices, extraordinary_notices = await asyncio.gather(
        _candidate_notices_in_meeting_window(
            corp_code,
            _MEETING_TYPE_MAP["annual"],
            start_date,
            end_date,
        ),
        _candidate_notices_in_meeting_window(
            corp_code,
            _MEETING_TYPE_MAP["extraordinary"],
            start_date,
            end_date,
        ),
    )

    annual_latest = _pick_latest_notice(annual_notices)
    extraordinary_latest = _pick_latest_notice(extraordinary_notices)
    has_annual = annual_latest is not None
    has_extraordinary = extraordinary_latest is not None

    return {
        "window_months": months,
        "window_start": start_date.isoformat(),
        "window_end": end_date.isoformat(),
        "has_annual": has_annual,
        "has_extraordinary": has_extraordinary,
        "presence_flag": _meeting_presence_flag(has_annual, has_extraordinary),
        "annual_count": len(annual_notices),
        "extraordinary_count": len(extraordinary_notices),
        "latest_annual": {
            "meeting_date": _parse_notice_meeting_date(annual_latest.get("datetime", "")).isoformat()
            if annual_latest and _parse_notice_meeting_date(annual_latest.get("datetime", ""))
            else None,
            "notice_rcept_no": annual_latest.get("rcept_no", ""),
            "notice_date": annual_latest.get("disclosure_date", ""),
        } if annual_latest else None,
        "latest_extraordinary": {
            "meeting_date": _parse_notice_meeting_date(extraordinary_latest.get("datetime", "")).isoformat()
            if extraordinary_latest and _parse_notice_meeting_date(extraordinary_latest.get("datetime", ""))
            else None,
            "notice_rcept_no": extraordinary_latest.get("rcept_no", ""),
            "notice_date": extraordinary_latest.get("disclosure_date", ""),
        } if extraordinary_latest else None,
    }


async def _meeting_result_data(
    corp_name: str,
    result_reference: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, str | None]:
    if not result_reference:
        return None, "주주총회결과 공시를 찾지 못했다."

    rcept_no = result_reference.get("rcept_no", "")
    kind_acptno = result_reference.get("kind_acptno")
    if not kind_acptno:
        return None, "주주총회결과 공시가 whitelist 규칙에 맞지 않아 자동 매핑하지 않았다."

    client = get_dart_client()
    if not rcept_no or len(rcept_no) != 14 or rcept_no[8:10] != "80":
        return None, "주주총회결과 공시가 whitelist 규칙에 맞는 DART 결과번호가 아니다."

    try:
        html = await client.kind_fetch_document(kind_acptno)
    except DartClientError as exc:
        return None, f"KIND 결과 본문 조회 실패: {exc.status}"

    soup = BeautifulSoup(html, "lxml")
    items = _parse_agm_result_table(soup)
    if not items:
        return None, "KIND 결과 표를 찾지 못했다."

    return {
        "corp_name": corp_name,
        "rcept_no": rcept_no,
        "kind_acptno": kind_acptno,
        "rcept_dt": result_reference.get("disclosure_date", ""),
        "report_name": result_reference.get("report_name", ""),
        "items": items,
    }, None


def _unsupported_scope_payload(
    company_query: str,
    scope: str,
) -> dict[str, Any]:
    envelope = ToolEnvelope(
        tool="shareholder_meeting",
        status=AnalysisStatus.REQUIRES_REVIEW,
        subject=company_query,
        warnings=[f"`{scope}` scope는 아직 v2에서 열지 않았다."],
        data={"query": company_query, "scope": scope},
        next_actions=["summary, agenda, board, compensation, results 중 하나 사용"],
    )
    return envelope.to_dict()


async def build_shareholder_meeting_payload(
    company_query: str,
    *,
    meeting_type: str = "auto",
    scope: str = "summary",
    year: int | None = None,
) -> dict[str, Any]:
    """주총 summary/agenda facade."""

    if scope not in _SUPPORTED_SCOPES:
        return _unsupported_scope_payload(company_query, scope)

    resolution = await resolve_company_query(company_query)
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        envelope = ToolEnvelope(
            tool="shareholder_meeting",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[f"'{company_query}'에 해당하는 회사를 찾지 못했다."],
            data={"query": company_query, "meeting_type": meeting_type, "scope": scope},
            next_actions=["company tool로 먼저 회사 식별 확인"],
        )
        return envelope.to_dict()

    if resolution.status == AnalysisStatus.AMBIGUOUS:
        envelope = ToolEnvelope(
            tool="shareholder_meeting",
            status=AnalysisStatus.AMBIGUOUS,
            subject=company_query,
            warnings=["회사 식별이 애매해 주총 공시를 자동 선택하지 않았다."],
            data={
                "query": company_query,
                "meeting_type": meeting_type,
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
            next_actions=["ticker 또는 corp_code로 다시 조회"],
        )
        return envelope.to_dict()

    if meeting_type not in _ALLOWED_MEETING_TYPES:
        envelope = ToolEnvelope(
            tool="shareholder_meeting",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[f"meeting_type=`{meeting_type}`는 지원하지 않는다. auto, annual, extraordinary만 사용 가능하다."],
            data={"query": company_query, "meeting_type": meeting_type, "scope": scope},
        )
        return envelope.to_dict()

    target_year = year or date.today().year
    selected = resolution.selected

    try:
        selected_candidate, alternative_meetings, selection_basis, candidate_error = await _select_notice_candidate(
            selected["corp_code"],
            target_year,
            meeting_type,
            scope,
        )
    except DartClientError as exc:
        envelope = ToolEnvelope(
            tool="shareholder_meeting",
            status=AnalysisStatus.ERROR,
            subject=selected.get("corp_name", company_query),
            warnings=[f"DART 공시 검색 실패: {exc.status}"],
            data={"query": company_query, "meeting_type": meeting_type, "scope": scope, "year": target_year},
        )
        return envelope.to_dict()

    if not selected_candidate:
        envelope = ToolEnvelope(
            tool="shareholder_meeting",
            status=AnalysisStatus.ERROR,
            subject=selected.get("corp_name", company_query),
            warnings=[candidate_error or "주주총회 소집공고를 찾지 못했다."],
            data={
                "query": company_query,
                "company_id": _company_id(selected),
                "requested_meeting_type": meeting_type,
                "scope": scope,
                "year": target_year,
            },
            next_actions=["meeting_type 또는 year를 바꿔 재조회"],
        )
        return envelope.to_dict()

    selected_meeting_type = selected_candidate["meeting_type"]
    latest_notice = selected_candidate["notice"]
    selected_meeting_date = selected_candidate.get("meeting_date")
    meeting_phase = selected_candidate["meeting_phase"]
    result_status = selected_candidate["result_status"]
    result_reference = selected_candidate["result_reference"]
    result_filing_warning = selected_candidate["result_filing_warning"]
    coverage_12m = await _meeting_window_coverage(
        selected["corp_code"],
        selected_meeting_date or date.today(),
        months=12,
    )

    client = get_dart_client()
    doc = await client.get_document_cached(latest_notice["rcept_no"])
    text = doc.get("text", "")
    html = doc.get("html", "")
    meeting_info = parse_meeting_info_xml(text, html=html)
    agenda = parse_agenda_xml(text, html=html)
    agenda_valid = validate_agenda_result(agenda)
    board = parse_personnel_xml(html) if html else {"appointments": [], "summary": {}}
    compensation = parse_compensation_xml(html) if html else {"items": [], "summary": {}}

    warnings: list[str] = []
    status = AnalysisStatus.EXACT
    if not agenda_valid:
        status = AnalysisStatus.REQUIRES_REVIEW
        warnings.append("안건 파싱 신뢰도가 낮아 원문 재검토가 필요하다.")
    if not html:
        status = AnalysisStatus.REQUIRES_REVIEW
        warnings.append("HTML 구조를 확보하지 못해 XML 텍스트 기준으로만 파싱했다.")

    correction = _correction_summary(html) if html else None
    agenda_nodes = _agenda_nodes(agenda)
    flat_agendas = _flatten_agendas(agenda)
    agenda_summary = {
        "root_count": len(agenda_nodes),
        "total_count": len(flat_agendas),
        "titles": [item["title"] for item in flat_agendas[:10]],
    }
    board_summary = board.get("summary", {})
    compensation_summary = compensation.get("summary", {})

    data: dict[str, Any] = {
        "query": company_query,
        "company_id": _company_id(selected),
        "canonical_name": selected.get("corp_name", ""),
        "identifiers": {
            "ticker": selected.get("stock_code", ""),
            "corp_code": selected.get("corp_code", ""),
        },
        "requested_meeting_type": meeting_type,
        "meeting_type": selected_meeting_type,
        "selection_basis": selection_basis,
        "year": target_year,
        "notice": latest_notice,
        "meeting_info": meeting_info,
        "meeting_phase": meeting_phase,
        "result_status": result_status,
        "agenda_summary": agenda_summary,
        "board_summary": board_summary,
        "compensation_summary": compensation_summary,
        "available_scopes": ["summary", "agenda", "board", "compensation", "results"],
        "selected_meeting": _candidate_meta(selected_candidate),
        "alternative_meetings": alternative_meetings,
        "meeting_coverage_12m": coverage_12m,
    }
    if result_reference:
        data["result_reference"] = result_reference
    if correction:
        data["correction_summary"] = correction
    if scope == "agenda":
        data["agendas"] = agenda_nodes
    if scope == "board":
        data["board"] = board
        if not board.get("appointments"):
            warnings.append("선임/해임 인사 안건이 없거나 파싱되지 않았다.")
    if scope == "compensation":
        data["compensation"] = compensation
        if not compensation.get("items"):
            warnings.append("보수한도 안건이 없거나 파싱되지 않았다.")
    if scope == "results":
        if meeting_phase == "pre_meeting":
            warnings.append("회의일 전이라 아직 주주총회결과 공시가 나올 시점이 아니다.")
            status = AnalysisStatus.PARTIAL
        else:
            result_data, result_warning = await _meeting_result_data(
                selected.get("corp_name", company_query),
                result_reference,
            )
            if result_warning:
                warnings.append(result_warning)
                status = AnalysisStatus.REQUIRES_REVIEW
            if result_data:
                data["results"] = result_data
    elif result_filing_warning and meeting_phase != "pre_meeting":
        warnings.append(result_filing_warning)

    evidence_refs = [
        EvidenceRef(
            evidence_id=f"ev_notice_{latest_notice['rcept_no']}",
            source_type=SourceType.DART_XML,
            rcept_no=latest_notice["rcept_no"],
            section="주주총회 소집공고",
            snippet=f"{latest_notice.get('report_name', '')} / {meeting_info.get('datetime') or ''}",
            parser="parse_meeting_info_xml+parse_agenda_xml",
        )
    ]
    if scope == "board":
        evidence_refs.append(
            EvidenceRef(
                evidence_id=f"ev_board_{latest_notice['rcept_no']}",
                source_type=SourceType.DART_XML,
                rcept_no=latest_notice["rcept_no"],
                section="후보자/이사 선임",
                snippet=f"후보자 {board_summary.get('total_candidates', 0)}명",
                parser="parse_personnel_xml",
            )
        )
    if scope == "compensation":
        evidence_refs.append(
            EvidenceRef(
                evidence_id=f"ev_comp_{latest_notice['rcept_no']}",
                source_type=SourceType.DART_XML,
                rcept_no=latest_notice["rcept_no"],
                section="보수한도 승인",
                snippet=f"보수 안건 {len(compensation.get('items', []))}건",
                parser="parse_compensation_xml",
            )
        )
    if scope == "results" and data.get("results"):
        evidence_refs.append(
            EvidenceRef(
                evidence_id=f"ev_result_{data['results']['rcept_no']}",
                source_type=SourceType.KIND_HTML,
                rcept_no=data["results"]["rcept_no"],
                section="주주총회결과",
                snippet=f"투표 결과 {len(data['results'].get('items', []))}건",
                parser="kind_vote_table",
            )
        )

    envelope = ToolEnvelope(
        tool="shareholder_meeting",
        status=status,
        subject=selected.get("corp_name", company_query),
        warnings=warnings,
        data=data,
        evidence_refs=evidence_refs,
        next_actions=[
            "agenda, board, compensation, results scope로 세부 탭 확인" if scope == "summary" else "evidence tool로 원문 근거 재확인",
            "결과가 아직 없으면 meeting_phase와 result_status를 먼저 확인",
        ],
    )
    return envelope.to_dict()
