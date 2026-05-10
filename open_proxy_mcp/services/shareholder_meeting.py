"""v2 shareholder_meeting facade 서비스."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import date, datetime, timedelta
import re
from typing import Any

from bs4 import BeautifulSoup
from open_proxy_mcp.dart.client import DartClientError, get_dart_client
import open_proxy_mcp.tools.parser as notice_parser_mod
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
from open_proxy_mcp.services.date_utils import format_iso_date, parse_date_param, resolve_date_window
from open_proxy_mcp.services.filing_search import search_filings_by_report_name
from open_proxy_mcp.tools.formatters import _parse_agm_result_summary, _parse_agm_result_table
from open_proxy_mcp.tools.parser import (
    parse_agenda_details_xml,
    parse_agenda_xml,
    parse_aoi_xml,
    parse_compensation_xml,
    parse_corrections_xml,
    parse_meeting_info_xml,
    parse_personnel_xml,
    validate_agenda_result,
)


_SUPPORTED_SCOPES = {"summary", "agenda", "board", "compensation", "aoi_change", "prov_financials", "results", "full"}
_MEETING_TYPE_MAP = {
    "annual": "정기",
    "extraordinary": "임시",
}
_ALLOWED_MEETING_TYPES = {"auto", "annual", "extraordinary"}
_NOTICE_LEAD_BUFFER_DAYS = 90


class _RequestLocalSoupFactory:
    """One-request soup cache keyed by rcept_no + raw HTML."""

    def __init__(
        self,
        original: Any,
        cache: dict[tuple[str, str, Any], Any],
        rcept_no: str,
    ) -> None:
        self.original = original
        self.cache = cache
        self.rcept_no = rcept_no

    def __call__(self, markup: Any = "", features: Any = None, *args: Any, **kwargs: Any) -> Any:
        if not isinstance(markup, str):
            return self.original(markup, features, *args, **kwargs)
        key = (self.rcept_no, markup, features)
        cached = self.cache.get(key)
        if cached is not None:
            return cached
        soup = self.original(markup, features, *args, **kwargs)
        self.cache[key] = soup
        return soup


@contextmanager
def _cached_notice_parser_soup(
    soup_cache: dict[tuple[str, str, Any], Any] | None,
    rcept_no: str,
):
    if soup_cache is None:
        yield
        return

    original = notice_parser_mod.BeautifulSoup
    notice_parser_mod.BeautifulSoup = _RequestLocalSoupFactory(original, soup_cache, rcept_no)
    try:
        yield
    finally:
        notice_parser_mod.BeautifulSoup = original


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
) -> tuple[list[dict[str, Any]], list[str]]:
    client = get_dart_client()
    # last_reprt_at='Y' — 정정공시 자동 정리 (최종본만). 정정 다수 회사
    # (현대차/삼성전자 등)에서 candidate 개수 N=2-3 → 1로 줄어듦.
    filings, notices, error = await search_filings_by_report_name(
        corp_code=corp_code,
        bgn_de=bgn_de,
        end_de=end_de,
        pblntf_tys="E",
        keywords=("소집",),
        last_reprt_at="Y",
    )
    if error and error != "013":
        raise DartClientError(error, "주총 소집공고 검색 실패")
    if error == "013":
        filings = []
    # E type 결과 부족 시 모든 type fallback (에스엠/고려아연 등 누락 대응).
    if not filings:
        try:
            data = await client.search_filings(
                corp_code=corp_code, bgn_de=bgn_de, end_de=end_de,
                pblntf_ty=None,  # 전 type
                last_reprt_at="Y",
            )
            all_items = data.get("list", []) or []
            filings = [
                i for i in all_items
                if "주주총회소집공고" in i.get("report_nm", "") or "소집" in i.get("report_nm", "")
            ]
        except Exception:
            pass
    # 최신 정정공시 우선 (rcept_dt + rcept_no desc).
    # 일반적으로 최신 1-2건이 사용자 의도와 일치 — 정기 1번 + 정정 1-2 또는 임시.
    filings.sort(key=lambda row: (row.get("rcept_dt", ""), row.get("rcept_no", "")), reverse=True)
    if not filings:
        return [], notices

    async def _resolve_batch(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
        docs = await asyncio.gather(*[
            client.get_document_cached(item["rcept_no"]) for item in batch
        ])
        out: list[dict[str, Any]] = []
        for item, doc in zip(batch, docs):
            text = doc.get("text", "")
            html = doc.get("html", "")
            info, info_source = await _notice_info_with_fallback(item["rcept_no"], text, html)
            normalized = _normalize_notice_row(item, info)
            normalized["notice_source"] = info_source
            if normalized["meeting_type"] == meeting_type_label:
                out.append(normalized)
        return out

    # 1차: 상위 2건만 doc fetch (정기 + 정정 cover, LG화학 등 대형사 대응).
    TOP_N = 2
    matched = await _resolve_batch(filings[:TOP_N])

    # 2차 fallback: 1차에서 meeting_type 일치 못 찾으면 나머지 전체 fetch.
    # rare case (정기/임시 섞임 + 임시가 최신 + 사용자가 annual 요청 등).
    if not matched and len(filings) > TOP_N:
        matched = await _resolve_batch(filings[TOP_N:])

    return matched, notices


async def _candidate_notices(
    corp_code: str,
    meeting_type_label: str,
    year: int,
) -> tuple[list[dict[str, Any]], list[str]]:
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
) -> tuple[list[dict[str, Any]], list[str]]:
    search_start = meeting_start - timedelta(days=_NOTICE_LEAD_BUFFER_DAYS)
    notices, search_notices = await _candidate_notices_range(
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
            continue
        # 회의일자 파싱 실패 케이스(예: CJ ENM 공시 본문 구조 불일치) fallback:
        # 공시 접수일(rcept_dt)이 meeting window 안에 있고 NOTICE_LEAD_BUFFER_DAYS 이내이면 포함.
        # 실제 회의일은 후속 파싱 단계에서 확보할 수 있으며, 여기서 버리면 아예 공시를 놓친다.
        if not meeting_date:
            disclosure_date = notice.get("disclosure_date", "")
            if len(disclosure_date) >= 8 and disclosure_date[:8].isdigit():
                try:
                    rcept = date(int(disclosure_date[:4]), int(disclosure_date[4:6]), int(disclosure_date[6:8]))
                    if search_start <= rcept <= meeting_end:
                        matched.append(notice)
                except ValueError:
                    pass
    return matched, search_notices


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


def _parse_notice_bundle(
    text: str,
    html: str,
    *,
    rcept_no: str,
    soup_cache: dict[tuple[str, str, Any], Any] | None = None,
) -> dict[str, Any]:
    with _cached_notice_parser_soup(soup_cache, rcept_no):
        meeting_info = parse_meeting_info_xml(text, html=html)
        agenda = parse_agenda_xml(text, html=html)
        board = parse_personnel_xml(html) if html else {"appointments": [], "summary": {}}
        compensation = parse_compensation_xml(html) if html else {"items": [], "summary": {}}
    return {
        "text": text,
        "html": html,
        "meeting_info": meeting_info,
        "agenda": agenda,
        "agenda_valid": validate_agenda_result(agenda),
        "board": board,
        "compensation": compensation,
        "correction": _correction_summary(html) if html else None,
    }


def _agenda_titles(items: list[dict[str, Any]]) -> list[str]:
    titles: list[str] = []
    for item in items:
        title = (item.get("title") or "").strip()
        if title:
            titles.append(title)
        titles.extend(_agenda_titles(item.get("children", [])))
    return titles


def _needs_notice_viewer_fallback(parsed: dict[str, Any], *, scope: str) -> list[str]:
    reasons: list[str] = []
    meeting_info = parsed["meeting_info"]
    if not parsed["html"]:
        reasons.append("api_html_missing")
    if not meeting_info.get("meeting_type"):
        reasons.append("meeting_type_missing")
    if not meeting_info.get("datetime"):
        reasons.append("meeting_datetime_missing")
    if not parsed["agenda_valid"]:
        reasons.append("agenda_parse_low_confidence")

    agenda_titles = _agenda_titles(parsed["agenda"])
    board_expected = any(("선임" in title or "해임" in title) and ("이사" in title or "감사" in title) for title in agenda_titles)
    compensation_expected = any("보수" in title and "한도" in title for title in agenda_titles)

    if scope in {"board", "full"} and board_expected and not parsed["board"].get("appointments"):
        reasons.append("board_parse_empty")
    if scope in {"compensation", "full"} and compensation_expected and not parsed["compensation"].get("items"):
        reasons.append("compensation_parse_empty")
    return reasons


async def _load_notice_bundle_with_fallback(
    rcept_no: str,
    *,
    scope: str,
    soup_cache: dict[tuple[str, str, Any], Any] | None = None,
) -> tuple[dict[str, Any], list[str], str]:
    client = get_dart_client()
    doc = await client.get_document_cached(rcept_no)
    parsed = _parse_notice_bundle(
        doc.get("text", ""),
        doc.get("html", ""),
        rcept_no=rcept_no,
        soup_cache=soup_cache,
    )
    reasons = _needs_notice_viewer_fallback(parsed, scope=scope)
    warnings: list[str] = []
    source_used = "dart_xml"

    if not reasons:
        return parsed, warnings, source_used

    section_keywords = ["주주총회 소집공고", "주주총회소집공고"]
    if scope in {"board", "compensation", "aoi_change", "full"}:
        section_keywords.extend(["목적사항별 기재사항", "주주총회 목적사항별 기재사항"])

    warnings.append(f"API/XML 파싱이 약해 DART viewer HTML crawl fallback을 시도했다. ({', '.join(reasons)})")
    try:
        viewer_doc = await client.get_viewer_document(rcept_no, section_keywords=section_keywords)
    except Exception as exc:
        warnings.append(f"DART viewer HTML crawl fallback도 실패했다: {exc}")
        return parsed, warnings, source_used

    viewer_parsed = _parse_notice_bundle(
        viewer_doc.get("text", ""),
        viewer_doc.get("html", ""),
        rcept_no=rcept_no,
        soup_cache=soup_cache,
    )
    improved = False

    if (not parsed["meeting_info"].get("meeting_type")) and viewer_parsed["meeting_info"].get("meeting_type"):
        parsed["meeting_info"] = viewer_parsed["meeting_info"]
        improved = True
    if (not parsed["meeting_info"].get("datetime")) and viewer_parsed["meeting_info"].get("datetime"):
        parsed["meeting_info"] = viewer_parsed["meeting_info"]
        improved = True
    if (not parsed["agenda_valid"]) and viewer_parsed["agenda_valid"]:
        parsed["agenda"] = viewer_parsed["agenda"]
        parsed["agenda_valid"] = True
        parsed["text"] = viewer_parsed["text"]
        parsed["html"] = viewer_parsed["html"]
        improved = True
    if scope in {"board", "full"} and len(viewer_parsed["board"].get("appointments", [])) > len(parsed["board"].get("appointments", [])):
        parsed["board"] = viewer_parsed["board"]
        improved = True
    if scope in {"compensation", "full"} and len(viewer_parsed["compensation"].get("items", [])) > len(parsed["compensation"].get("items", [])):
        parsed["compensation"] = viewer_parsed["compensation"]
        improved = True

    if improved:
        source_used = "dart_html"
        warnings.append("DART viewer HTML crawl 결과를 반영해 notice 파싱 품질을 보정했다.")
    else:
        # viewer HTML은 확보했지만 구조 파싱 개선 안 된 경우.
        # viewer text가 XML text보다 풍부하면(표·섹션 구조 보존) raw text fallback에 쓸 수 있게 교체.
        viewer_text = (viewer_parsed.get("text") or "").strip()
        xml_text = (parsed.get("text") or "").strip()
        if len(viewer_text) > len(xml_text):
            parsed["text"] = viewer_text
            warnings.append("DART viewer HTML crawl 결과의 원문 텍스트가 XML 텍스트보다 풍부해 raw text fallback 소스로 교체했다.")
        else:
            warnings.append("DART viewer HTML crawl을 재시도했지만 구조화 결과는 기존 API/XML보다 개선되지 않았다.")
    return parsed, warnings, source_used


async def _notice_info_with_fallback(
    rcept_no: str,
    text: str,
    html: str,
) -> tuple[dict[str, Any], str]:
    meeting_info = parse_meeting_info_xml(text, html=html)
    if meeting_info.get("meeting_type") and meeting_info.get("datetime"):
        return meeting_info, "dart_xml"

    client = get_dart_client()
    try:
        viewer_doc = await client.get_viewer_document(
            rcept_no,
            section_keywords=["주주총회 소집공고", "주주총회소집공고"],
        )
    except Exception:
        return meeting_info, "dart_xml"

    viewer_info = parse_meeting_info_xml(viewer_doc.get("text", ""), html=viewer_doc.get("html", ""))
    if viewer_info.get("meeting_type") or viewer_info.get("datetime"):
        return viewer_info, "dart_html"
    return meeting_info, "dart_xml"


async def _find_meeting_result_filing(
    corp_code: str,
    target_year: int,
    notice: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None, list[str]]:
    result_items, notices, error = await search_filings_by_report_name(
        corp_code=corp_code,
        bgn_de=f"{target_year}0101",
        end_de=f"{target_year}1231",
        pblntf_tys="I",
        keywords=("주주총회결과",),
    )
    if error:
        return None, f"주주총회결과 공시 검색 실패: {error}", notices
    if not result_items:
        return None, "주주총회결과 공시를 찾지 못했다.", notices

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
    # [기재정정] 제외 우선 — 정정 본문이 변경 부분만 담을 위험 회피.
    # 비면 정정 포함 fallback ([[architecture/multi-upstream-pattern]]).
    non_corr = [it for it in result_items if not (it.get("report_nm") or "").startswith("[기재정정]")]
    return (non_corr or result_items)[0], None, notices


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


def _selection_window(
    target_year: int | None,
    *,
    start_date: str = "",
    end_date: str = "",
    lookback_months: int = 12,
) -> tuple[date, date, list[str]]:
    if start_date or end_date:
        return resolve_date_window(
            start_date=start_date,
            end_date=end_date,
            default_end=date.today(),
            lookback_months=lookback_months,
        )
    if target_year:
        return date(target_year, 1, 1), date(target_year, 12, 31), []
    return resolve_date_window(
        start_date="",
        end_date="",
        default_end=date.today(),
        lookback_months=lookback_months,
    )


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
    *,
    fetch_result_filing: bool = True,
) -> dict[str, Any]:
    """notice candidate에 result_filing 정보 결합.

    fetch_result_filing=False일 때 (예: scope=summary/board/compensation):
    - DART 결과 공시 검색 생략 (5초+ 단축)
    - meeting_phase는 meeting_date 기준 단순 분류 (pre/post)
    - result_filing / result_reference 는 None (필요한 scope에서 별도 fetch)
    """
    meeting_date = _parse_notice_meeting_date(notice.get("datetime", ""))
    result_search_year = meeting_date.year if meeting_date else target_year

    if fetch_result_filing:
        result_filing, result_filing_warning, result_search_notices = await _find_meeting_result_filing(
            corp_code,
            result_search_year,
            notice,
        )
        result_reference = _result_reference(result_filing)
        meeting_phase, result_status = _meeting_phase(notice, result_filing, result_reference)
    else:
        # date 기반 단순 phase 판단 (DART 호출 0)
        result_filing = None
        result_filing_warning = None
        result_search_notices = []
        result_reference = None
        meeting_phase, result_status = _meeting_phase(notice, None, None)

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
        "search_notices": result_search_notices,
    }


async def _select_notice_candidate(
    corp_code: str,
    target_year: int | None,
    requested_meeting_type: str,
    scope: str,
    *,
    start_date: str = "",
    end_date: str = "",
    lookback_months: int = 12,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], str | None, str | None, list[str]]:
    search_notices: list[str] = []
    window_start, window_end, _ = _selection_window(
        target_year,
        start_date=start_date,
        end_date=end_date,
        lookback_months=lookback_months,
    )
    if requested_meeting_type == "auto":
        annual_result, extraordinary_result = await asyncio.gather(
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
        annual_notices, annual_search_notices = annual_result
        extraordinary_notices, extraordinary_search_notices = extraordinary_result
        search_notices.extend(annual_search_notices)
        search_notices.extend(extraordinary_search_notices)
        latest_by_type: list[tuple[str, dict[str, Any]]] = []
        annual_latest = _pick_latest_notice(annual_notices)
        extraordinary_latest = _pick_latest_notice(extraordinary_notices)
        if annual_latest:
            latest_by_type.append(("annual", annual_latest))
        if extraordinary_latest:
            latest_by_type.append(("extraordinary", extraordinary_latest))
        if not latest_by_type:
            return None, [], None, f"{window_start.isoformat()}~{window_end.isoformat()} 구간에 정기/임시 주주총회 소집공고를 찾지 못했다.", search_notices

        # results / full scope에서만 result_filing 검색 (사후 결과 데이터가 핵심).
        # notice tool (summary/board/compensation/aoi_change/prov_financials)은 미사용.
        # auto 모드 ranking도 date 기반 phase로 충분 (post_meeting_pre_result rank 1 통합).
        # 정기/임시 분류 자체는 _candidate_notices_in_meeting_window가 doc 파싱으로 결정 — result_filing 무관.
        fetch_result = scope in {"results", "full"}
        candidates = await asyncio.gather(*[
            _build_candidate(
                corp_code, meeting_type, target_year or window_end.year, notice,
                fetch_result_filing=fetch_result,
            )
            for meeting_type, notice in latest_by_type
        ])
        for candidate in candidates:
            search_notices.extend(candidate.get("search_notices", []))
        selected = sorted(candidates, key=lambda row: _auto_rank_key(row, scope), reverse=True)[0]
        alternatives = [_candidate_meta(candidate) for candidate in candidates if candidate is not selected]
        basis = _auto_selection_basis(selected, scope, candidates)
        return selected, alternatives, basis, None, search_notices

    meeting_type_label = _MEETING_TYPE_MAP[requested_meeting_type]
    notices, notice_search_notices = await _candidate_notices_in_meeting_window(
        corp_code,
        meeting_type_label,
        window_start,
        window_end,
    )
    search_notices.extend(notice_search_notices)
    latest_notice = _pick_latest_notice(notices)
    if not latest_notice:
        return None, [], None, f"{window_start.isoformat()}~{window_end.isoformat()} 구간에 {meeting_type_label} 주주총회 소집공고를 찾지 못했다.", search_notices
    fetch_result = scope in {"results", "full"}
    selected = await _build_candidate(
        corp_code, requested_meeting_type, target_year or window_end.year, latest_notice,
        fetch_result_filing=fetch_result,
    )
    search_notices.extend(selected.get("search_notices", []))
    basis = f"사용자가 {meeting_type_label} 주주총회를 명시해 해당 회차를 선택했다."
    return selected, [], basis, None, search_notices


async def _meeting_window_coverage(
    corp_code: str,
    start_date: date,
    end_date: date,
    months: int = 12,
) -> dict[str, Any]:
    annual_result, extraordinary_result = await asyncio.gather(
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
    annual_notices, _ = annual_result
    extraordinary_notices, _ = extraordinary_result

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

    # 1차: DART API로 본문 fetch (~0.5-1.5s, KIND scraping 4-5s 대비 빠름).
    # html 구조가 KIND와 거의 동일해 기존 parser 호환.
    source_used = "dart_api"
    html = ""
    try:
        doc = await client.get_document_cached(rcept_no)
        html = doc.get("html") or ""
    except DartClientError:
        html = ""

    soup = BeautifulSoup(html, "lxml") if html else None
    items = _parse_agm_result_table(soup) if soup else []
    result_format = "table" if items else None
    if soup and not items:
        items = _parse_agm_result_summary(soup)
        if items:
            result_format = "summary"

    # 2차 fallback: DART에서 본문 빈 응답 또는 파싱 실패 시 KIND scraping.
    if not items:
        try:
            html = await client.kind_fetch_document(kind_acptno)
        except DartClientError as exc:
            return None, f"DART API + KIND fallback 모두 실패: {exc.status}"
        soup = BeautifulSoup(html, "lxml")
        items = _parse_agm_result_table(soup)
        result_format = "table" if items else None
        if not items:
            items = _parse_agm_result_summary(soup)
            if items:
                result_format = "summary"
        if not items:
            return None, "DART/KIND 본문에서 안건 결과를 찾지 못했다."
        source_used = "kind_scraping"

    return {
        "corp_name": corp_name,
        "rcept_no": rcept_no,
        "kind_acptno": kind_acptno,
        "rcept_dt": result_reference.get("disclosure_date", ""),
        "report_name": result_reference.get("report_name", ""),
        "result_format": result_format,
        "numerical_vote_table_available": result_format == "table",
        "items": items,
        "source": source_used,
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
    start_date: str = "",
    end_date: str = "",
    lookback_months: int = 12,
) -> dict[str, Any]:
    """주총 summary/agenda facade."""

    if scope not in _SUPPORTED_SCOPES:
        return _unsupported_scope_payload(company_query, scope)

    _client = get_dart_client()
    _calls_start = _client.api_call_snapshot()
    resolution = await resolve_company_query(company_query)
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
                "usage": build_usage(_client.api_call_snapshot() - _calls_start),
            },
            next_actions=["ticker 또는 corp_code로 다시 조회"],
        )
        return envelope.to_dict()

    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        envelope = ToolEnvelope(
            tool="shareholder_meeting",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[f"'{company_query}'에 해당하는 회사를 찾지 못했다."],
            data={
                "query": company_query,
                "meeting_type": meeting_type,
                "scope": scope,
                "usage": build_usage(_client.api_call_snapshot() - _calls_start),
            },
            next_actions=["company tool로 먼저 회사 식별 확인"],
        )
        return envelope.to_dict()

    if meeting_type not in _ALLOWED_MEETING_TYPES:
        envelope = ToolEnvelope(
            tool="shareholder_meeting",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[f"meeting_type=`{meeting_type}`는 지원하지 않는다. auto, annual, extraordinary만 사용 가능하다."],
            data={
                "query": company_query,
                "meeting_type": meeting_type,
                "scope": scope,
                "usage": build_usage(_client.api_call_snapshot() - _calls_start),
            },
        )
        return envelope.to_dict()

    target_year = year
    soup_cache: dict[tuple[str, str, Any], Any] = {}
    selected = resolution.selected
    requested_window_start, requested_window_end, window_warnings = _selection_window(
        target_year,
        start_date=start_date,
        end_date=end_date,
        lookback_months=lookback_months,
    )

    try:
        selected_candidate, alternative_meetings, selection_basis, candidate_error, candidate_notices = await _select_notice_candidate(
            selected["corp_code"],
            target_year,
            meeting_type,
            scope,
            start_date=start_date,
            end_date=end_date,
            lookback_months=lookback_months,
        )
    except DartClientError as exc:
        envelope = ToolEnvelope(
            tool="shareholder_meeting",
            status=AnalysisStatus.ERROR,
            subject=selected.get("corp_name", company_query),
            warnings=[f"DART 공시 검색 실패: {exc.status}"],
            data={
                "query": company_query,
                "meeting_type": meeting_type,
                "scope": scope,
                "year": target_year,
                "usage": build_usage(_client.api_call_snapshot() - _calls_start),
            },
        )
        return envelope.to_dict()

    if not selected_candidate:
        # 회사 식별이 정상이고 DART 검색이 성공했지만 주총 소집공고가 없는 경우는
        # 사건 자체가 없는 정상 케이스 (NO_FILING). 호출이 실제 실패한 경우는 ERROR.
        no_filing_meta = build_filing_meta(filing_count=0, parsing_failures=0)
        envelope = ToolEnvelope(
            tool="shareholder_meeting",
            status=AnalysisStatus.NO_FILING,
            subject=selected.get("corp_name", company_query),
            warnings=[*(candidate_notices or []), candidate_error or f"조사 구간 ({requested_window_start.isoformat()}~{requested_window_end.isoformat()}) 내 주주총회 소집공고 없음 (정상)"],
            data={
                "query": company_query,
                "company_id": _company_id(selected),
                "requested_meeting_type": meeting_type,
                "scope": scope,
                "year": target_year or requested_window_end.year,
                "requested_window": {
                    "start_date": requested_window_start.isoformat(),
                    "end_date": requested_window_end.isoformat(),
                    "lookback_months": lookback_months,
                },
                **no_filing_meta,
                "usage": build_usage(_client.api_call_snapshot() - _calls_start),
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
    coverage_anchor_end = requested_window_end if (start_date or end_date or not target_year) else (selected_meeting_date or date.today())
    coverage_anchor_start = requested_window_start if (start_date or end_date or not target_year) else (coverage_anchor_end - timedelta(days=365))
    coverage_12m = await _meeting_window_coverage(
        selected["corp_code"],
        coverage_anchor_start,
        coverage_anchor_end,
        months=lookback_months if (start_date or end_date or not target_year) else 12,
    )

    parsed_notice, parse_warnings, notice_parse_source = await _load_notice_bundle_with_fallback(
        latest_notice["rcept_no"],
        scope=scope,
        soup_cache=soup_cache,
    )
    text = parsed_notice["text"]
    html = parsed_notice["html"]
    meeting_info = parsed_notice["meeting_info"]
    agenda = parsed_notice["agenda"]
    agenda_valid = parsed_notice["agenda_valid"]
    board = parsed_notice["board"]
    compensation = parsed_notice["compensation"]
    correction = parsed_notice["correction"]

    warnings: list[str] = list(window_warnings) + list(candidate_notices) + parse_warnings
    # 사건은 발견됨 (소집공고 1건 이상). 파싱 신뢰도는 별도 카운트.
    parse_failure_count = 0
    if not agenda_valid:
        parse_failure_count += 1
    if not html:
        parse_failure_count += 1
    # scope별 추가 카운트는 아래 include_* 분기에서 보강.
    status = AnalysisStatus.EXACT
    parsing_failed = False
    if not agenda_valid:
        status = AnalysisStatus.REQUIRES_REVIEW
        parsing_failed = True
        warnings.append("안건 파싱 신뢰도가 낮아 원문 재검토가 필요하다. data.raw_text_excerpt에 DART 원문 텍스트 발췌를 함께 제공하니 직접 해석한다.")
    if not html:
        status = AnalysisStatus.REQUIRES_REVIEW
        warnings.append("HTML 구조를 확보하지 못해 XML 텍스트 기준으로만 파싱했다.")

    agenda_nodes = _agenda_nodes(agenda)
    flat_agendas = _flatten_agendas(agenda)
    agenda_summary = {
        "root_count": len(agenda_nodes),
        "total_count": len(flat_agendas),
        "titles": [item["title"] for item in flat_agendas[:10]],
    }
    board_summary = board.get("summary", {})
    compensation_summary = compensation.get("summary", {})

    # filing_meta — 소집공고 1건 발견. 파싱 실패는 위 parse_failure_count로 누적.
    filing_meta = build_filing_meta(
        filing_count=1,
        parsing_failures=parse_failure_count,
    )

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
        "year": target_year or requested_window_end.year,
        "requested_window": {
            "start_date": requested_window_start.isoformat(),
            "end_date": requested_window_end.isoformat(),
            "lookback_months": lookback_months,
        },
        "notice": latest_notice,
        "notice_parse_source": notice_parse_source,
        "meeting_info": meeting_info,
        "meeting_phase": meeting_phase,
        "result_status": result_status,
        "agenda_summary": agenda_summary,
        "board_summary": board_summary,
        "compensation_summary": compensation_summary,
        **filing_meta,
        "available_scopes": ["summary", "board", "compensation", "aoi_change", "prov_financials"],
        "selected_meeting": _candidate_meta(selected_candidate),
        "alternative_meetings": alternative_meetings,
        "meeting_coverage_12m": coverage_12m,
    }
    if result_reference:
        data["result_reference"] = result_reference
    if correction:
        data["correction_summary"] = correction
    if parsing_failed:
        # 구조 파싱이 두 단계(API/XML + viewer HTML) 모두 실패한 경우 raw text fallback.
        # `text`는 viewer HTML이 더 풍부했으면 그 text로 교체돼 있음 (_load_notice_bundle_with_fallback 참조).
        # LLM/애널리스트가 원문을 직접 해석. PDF 다운로드 없이 종료.
        raw = (text or "").strip()
        if raw:
            data["raw_text_excerpt"] = raw[:6000]
            data["raw_text_full_length"] = len(raw)
    # 260505 ralph: agenda 트리는 summary에도 항상 포함 (parsing 이미 완료, 비용 0)
    include_agenda = scope in {"agenda", "full", "summary"}
    include_board = scope in {"board", "full"}
    include_compensation = scope in {"compensation", "full"}
    include_aoi = scope in {"aoi_change", "full"}
    include_prov_financials = scope in {"prov_financials", "full"}
    include_results = scope in {"results", "full"}

    if include_agenda:
        data["agendas"] = agenda_nodes
    if include_board:
        data["board"] = board
        if not board.get("appointments"):
            warnings.append("선임/해임 인사 안건이 없거나 파싱되지 않았다.")
    if include_compensation:
        data["compensation"] = compensation
        if not compensation.get("items"):
            warnings.append("보수한도 안건이 없거나 파싱되지 않았다.")
    if include_aoi:
        if not html:
            warnings.append("HTML을 확보하지 못해 정관변경 상세를 파싱할 수 없다.")
            data["aoi_change"] = {"amendments": [], "retirement_amendments": [], "summary": {}}
        else:
            charter_subs: list[dict] = []
            for item in agenda:
                if "정관" in (item.get("title") or ""):
                    charter_subs = item.get("children", [])
                    break
            aoi_result = parse_aoi_xml(html, sub_agendas=charter_subs if charter_subs else None)
            # 260505 ralph: aoi_change에 퇴직금 변경 raw도 통합 (data tool 원칙 — raw + 키워드 hit count, 판단 X)
            from open_proxy_mcp.tools.parser import parse_retirement_pay_xml
            retire_result = parse_retirement_pay_xml(html)
            retire_amendments = retire_result.get("amendments") or []
            aoi_result["retirement_amendments"] = retire_amendments
            aoi_result.setdefault("summary", {})["retirement_amendments_count"] = len(retire_amendments)
            data["aoi_change"] = aoi_result
            if not aoi_result.get("amendments") and not retire_amendments:
                warnings.append("정관변경 / 퇴직금 변경 안건이 없거나 파싱되지 않았다.")
    if include_prov_financials:
        if not html:
            warnings.append("HTML을 확보하지 못해 잠정 재무제표 표를 파싱할 수 없다.")
            data["prov_financials"] = {"consolidated": {}, "separate": {}, "metrics": {"extraction_status": "no_data"}}
        else:
            from open_proxy_mcp.services.provisional_financial_statement import (
                parse_provisional_financial_statement, extract_metrics
            )
            pfs = parse_provisional_financial_statement(html)
            metrics = extract_metrics(pfs)
            data["prov_financials"] = {**pfs, "metrics": metrics}
            if metrics.get("extraction_status") == "no_data":
                warnings.append("잠정 재무제표 추출 실패 — 1호 안건 본문 비표준 형식")
    if include_results:
        if meeting_phase == "pre_meeting":
            warnings.append("회의일 전이라 아직 주주총회결과 공시가 나올 시점이 아니다.")
            if scope == "results":
                status = AnalysisStatus.PARTIAL
        else:
            result_data, result_warning = await _meeting_result_data(
                selected.get("corp_name", company_query),
                result_reference,
            )
            if result_warning:
                warnings.append(result_warning)
                if scope == "results":
                    status = AnalysisStatus.REQUIRES_REVIEW
            if result_data:
                data["results"] = result_data
                if result_data.get("result_format") == "summary":
                    warnings.append("요약형 결과공시라 안건별 가결·부결은 확인되지만 찬성률/참석률 수치는 제공되지 않는다.")
    elif result_filing_warning and meeting_phase != "pre_meeting":
        warnings.append(result_filing_warning)

    notice_source_type = SourceType.DART_HTML if notice_parse_source == "dart_html" else SourceType.DART_XML
    notice_rcept_dt = format_iso_date(latest_notice.get("disclosure_date", ""))
    notice_report_nm = latest_notice.get("report_name", "")

    evidence_refs = [
        EvidenceRef(
            evidence_id=f"ev_notice_{latest_notice['rcept_no']}",
            source_type=notice_source_type,
            rcept_no=latest_notice["rcept_no"],
            rcept_dt=notice_rcept_dt,
            report_nm=notice_report_nm,
            section="주주총회 소집공고",
            note=f"회의일 {meeting_info.get('datetime') or '미확정'}",
        )
    ]
    if include_board:
        evidence_refs.append(
            EvidenceRef(
                evidence_id=f"ev_board_{latest_notice['rcept_no']}",
                source_type=notice_source_type,
                rcept_no=latest_notice["rcept_no"],
                rcept_dt=notice_rcept_dt,
                report_nm=notice_report_nm,
                section="후보자/이사 선임",
                note=f"후보자 {board_summary.get('total_candidates', 0)}명",
            )
        )
    if include_compensation:
        evidence_refs.append(
            EvidenceRef(
                evidence_id=f"ev_comp_{latest_notice['rcept_no']}",
                source_type=notice_source_type,
                rcept_no=latest_notice["rcept_no"],
                rcept_dt=notice_rcept_dt,
                report_nm=notice_report_nm,
                section="보수한도 승인",
                note=f"보수 안건 {len(compensation.get('items', []))}건",
            )
        )
    if include_aoi and data.get("aoi_change"):
        aoi_meta = data["aoi_change"]
        evidence_refs.append(
            EvidenceRef(
                evidence_id=f"ev_aoi_{latest_notice['rcept_no']}",
                source_type=notice_source_type,
                rcept_no=latest_notice["rcept_no"],
                rcept_dt=notice_rcept_dt,
                report_nm=notice_report_nm,
                section="정관변경 상세",
                note=f"정관변경 안건 {len(aoi_meta.get('amendments', []))}건",
            )
        )
    if include_results and data.get("results"):
        result_meta = data["results"]
        evidence_refs.append(
            EvidenceRef(
                evidence_id=f"ev_result_{result_meta['rcept_no']}",
                source_type=SourceType.KIND_HTML,
                rcept_no=result_meta["rcept_no"],
                rcept_dt=format_iso_date(result_meta.get("rcept_dt", "")),
                report_nm=result_meta.get("report_name", ""),
                section="주주총회결과",
                note=f"투표 결과 {len(result_meta.get('items', []))}건",
            )
        )

    data["usage"] = build_usage(_client.api_call_snapshot() - _calls_start)

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
