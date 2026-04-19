"""v2 proxy_contest facade 서비스."""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta
import re
from typing import Any

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.services.company import _company_id, resolve_company_query
from open_proxy_mcp.services.contracts import AnalysisStatus, EvidenceRef, SourceType, ToolEnvelope
from open_proxy_mcp.services.date_utils import format_iso_date, format_yyyymmdd, resolve_date_window
from open_proxy_mcp.services.filing_search import search_filings_by_report_name
from open_proxy_mcp.services.ownership_structure import (
    _build_control_map,
    _latest_block_rows,
    _major_holders_rows,
    _normalize_entity_name,
    _related_total,
    _top_holder_summary,
    _treasury_snapshot,
)
from open_proxy_mcp.services.shareholder_meeting import build_shareholder_meeting_payload
from open_proxy_mcp.tools.formatters import _parse_holding_purpose, _parse_holding_purpose_from_document

_SUPPORTED_SCOPES = {"summary", "fight", "litigation", "signals", "timeline", "vote_math"}
_PROXY_KEYWORDS = (
    "의결권대리행사권유",
    "위임장권유참고서류",
    "의결권대리행사참고서류",
    "공개매수신고서",
    "공개매수설명서",
    "공개매수결과보고서",
    "공개매수에관한의견표명서",
)
_LITIGATION_KEYWORDS = (
    "소송등의제기",
    "소송등의신청",
    "소송등의판결",
    "소송등의결정",
    "경영권분쟁소송",
)


def _strip_corp_name(name: str) -> str:
    return re.sub(r"[\(（]?주[\)）]?$|㈜$|주식회사\s*$", "", (name or "").strip()).strip()


def _is_company_side(filer_name: str, corp_name: str) -> bool:
    left = _strip_corp_name(filer_name)
    right = _strip_corp_name(corp_name)
    return bool(left and right and (left == right or right in left))


# 소액주주 집단 위임 플랫폼 운영사. 이들이 제출하는 `의결권대리행사권유참고서류`는
# 경영권 분쟁(proxy_fight)도 주주제안 지지(proxy_campaign)도 아닌 소액주주 반대·찬성 집단
# 위임 캠페인(retail_activism)이며, shareholder_side_count / has_contest_signal 판정에서 분리한다.
_RETAIL_ACTIVISM_PLATFORMS: frozenset[str] = frozenset({
    "컨두잇",        # ACT (act.ag)
    "헤이홀더",      # heyholder.com
    "비사이드코리아",  # bside.ai
})


def _is_retail_activism_side(filer_name: str) -> bool:
    normalized = _strip_corp_name(filer_name)
    return normalized in _RETAIL_ACTIVISM_PLATFORMS


def _window_bounds(
    target_year: int | None,
    *,
    start_date: str = "",
    end_date: str = "",
    lookback_months: int = 12,
) -> tuple[str, str, int, list[str]]:
    if start_date or end_date:
        window_start, window_end, warnings = resolve_date_window(
            start_date=start_date,
            end_date=end_date,
            default_end=date.today(),
            lookback_months=lookback_months,
        )
        return format_yyyymmdd(window_start), format_yyyymmdd(window_end), window_end.year, warnings

    today = date.today()
    if target_year and target_year < today.year:
        window_end = date(target_year, 12, 31)
    else:
        window_end = today
    window_start = window_end - timedelta(days=max(30, lookback_months * 30))
    return format_yyyymmdd(window_start), format_yyyymmdd(window_end), window_end.year, []


def _unique_nonempty(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        cleaned = (value or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        ordered.append(cleaned)
    return ordered


def _normalize_date_key(value: str) -> str:
    return re.sub(r"[^\d]", "", value or "")


def _in_window(value: str, bgn_de: str, end_de: str) -> bool:
    date_key = _normalize_date_key(value)
    return bool(date_key) and bgn_de <= date_key <= end_de


async def _proxy_items(
    corp_code: str,
    corp_name: str,
    bgn_de: str,
    end_de: str,
) -> tuple[list[dict[str, Any]], list[str], str | None]:
    items, notices, error = await search_filings_by_report_name(
        corp_code=corp_code,
        bgn_de=bgn_de,
        end_de=end_de,
        pblntf_tys="D",
        keywords=_PROXY_KEYWORDS,
    )
    if error:
        return [], notices, f"위임장/공개매수 공시 조회 실패: {error}"
    rows = []
    for item in items:
        filer = item.get("flr_nm", "")
        if _is_company_side(filer, corp_name):
            side = "company"
        elif _is_retail_activism_side(filer):
            side = "retail_activism"
        else:
            side = "shareholder"
        rows.append({
            "rcept_no": item.get("rcept_no", ""),
            "disclosure_date": item.get("rcept_dt", ""),
            "report_name": item.get("report_nm", ""),
            "filer_name": filer,
            "side": side,
        })
    rows.sort(key=lambda row: (row["disclosure_date"], row["rcept_no"]), reverse=True)
    return rows, notices, None


async def _litigation_items(
    corp_code: str,
    bgn_de: str,
    end_de: str,
) -> tuple[list[dict[str, Any]], list[str], str | None]:
    items, notices, error = await search_filings_by_report_name(
        corp_code=corp_code,
        bgn_de=bgn_de,
        end_de=end_de,
        pblntf_tys=("I", "B"),
        keywords=_LITIGATION_KEYWORDS,
        strip_spaces=True,
    )
    if error:
        return [], notices, f"소송/분쟁 공시 조회 실패: {error}"
    all_rows: list[dict[str, Any]] = []
    for item in items:
        all_rows.append({
            "rcept_no": item.get("rcept_no", ""),
            "disclosure_date": item.get("rcept_dt", ""),
            "report_name": item.get("report_nm", ""),
            "filer_name": item.get("flr_nm", ""),
        })
    all_rows.sort(key=lambda row: (row["disclosure_date"], row["rcept_no"]), reverse=True)
    return all_rows, notices, None


async def _block_signals(corp_code: str) -> tuple[list[dict[str, Any]], str | None]:
    client = get_dart_client()
    try:
        result = await client.get_block_holders(corp_code)
    except DartClientError as exc:
        return [], f"5% 대량보유 공시 조회 실패: {exc.status}"
    latest_by_reporter: dict[str, dict[str, Any]] = {}
    for item in result.get("list", []):
        reporter = item.get("repror", "").strip()
        if not reporter:
            continue
        if reporter not in latest_by_reporter or item.get("rcept_dt", "") > latest_by_reporter[reporter].get("rcept_dt", ""):
            latest_by_reporter[reporter] = item
    rows: list[dict[str, Any]] = []
    for reporter, item in latest_by_reporter.items():
        purpose = _parse_holding_purpose(item.get("report_tp", ""), item.get("report_resn", ""))
        if purpose in ("불명", "단순투자/일반투자") and item.get("rcept_no"):
            try:
                doc = await client.get_document_cached(item["rcept_no"])
                parsed = _parse_holding_purpose_from_document(doc.get("html", "") or "")
                if parsed != "불명":
                    purpose = parsed
            except Exception:
                pass
        rows.append({
            "reporter": reporter,
            "report_date": item.get("rcept_dt", ""),
            "rcept_no": item.get("rcept_no", ""),
            "ownership_pct": float(item.get("stkrt", 0) or 0),
            "purpose": purpose,
        })
    rows.sort(key=lambda row: (row["report_date"], row["rcept_no"]), reverse=True)
    return rows, None


async def _control_context(corp_code: str, company_query: str, target_year: int | None) -> tuple[dict[str, Any], list[str]]:
    client = get_dart_client()
    warnings: list[str] = []
    bsns_year = str((target_year or date.today().year) - 1)

    try:
        major = await client.get_major_shareholders(corp_code, bsns_year)
    except DartClientError as exc:
        warnings.append(f"지분 명부 API 조회 실패: {exc.status}")
        return {
            "year": bsns_year,
            "top_holder": {},
            "related_total_pct": 0.0,
            "treasury_pct": 0.0,
            "control_map": {},
        }, warnings

    try:
        stock_total = await client.get_stock_total(corp_code, bsns_year)
    except DartClientError as exc:
        stock_total = {"list": []}
        warnings.append(f"주식총수 API 조회 실패: {exc.status}")

    try:
        treasury_data = await client.get_treasury_stock(corp_code, bsns_year)
    except DartClientError as exc:
        treasury_data = {"list": []}
        warnings.append(f"자사주 API 조회 실패: {exc.status}")

    major_rows = _major_holders_rows(major)
    latest_blocks, _, block_warning = await _latest_block_rows(corp_code)
    if block_warning:
        warnings.append(block_warning)
    treasury_snapshot = _treasury_snapshot(stock_total, treasury_data)
    control_map = _build_control_map(major_rows, latest_blocks, treasury_snapshot)
    return {
        "year": bsns_year,
        "top_holder": _top_holder_summary(major_rows),
        "related_total_pct": _related_total(major_rows),
        "treasury_pct": treasury_snapshot["treasury_pct"],
        "control_map": control_map,
    }, warnings


def _signal_actor_side(row: dict[str, Any]) -> str:
    if row.get("registry_overlap"):
        return "registry_overlap"
    if row.get("active_purpose"):
        return "external_active_block"
    return "external_or_passive"


def _fight_actor_group(row: dict[str, Any], active_external_names: set[str], overlap_names: set[str]) -> str:
    if row.get("side") == "company":
        return "company"
    if row.get("side") == "retail_activism":
        return "retail_activism"
    filer_key = _normalize_entity_name(row.get("filer_name", ""))
    if filer_key in active_external_names:
        return "external_active_block"
    if filer_key in overlap_names:
        return "registry_overlap"
    return "shareholder"


def _unsupported_scope_payload(company_query: str, scope: str) -> dict[str, Any]:
    return ToolEnvelope(
        tool="proxy_contest",
        status=AnalysisStatus.REQUIRES_REVIEW,
        subject=company_query,
        warnings=[f"`{scope}` scope는 아직 지원하지 않는다."],
        data={"query": company_query, "scope": scope},
    ).to_dict()


def _to_float(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _vote_math_exclusion_reason(item: dict[str, Any]) -> str | None:
    resolution_type = (item.get("resolution_type") or "").strip()
    agenda = (item.get("agenda") or "").strip()
    attendance = item.get("estimated_attendance")

    if attendance in (None, ""):
        return "참석률 역산이 불가능하다."
    if "보통" not in resolution_type:
        return "보통결의 안건이 아니다."
    if "감사" in resolution_type or "감사위원" in agenda or "감사위원" in resolution_type:
        return "감사·감사위원 안건은 3% 제한으로 분모가 다를 수 있다."
    if "집중" in resolution_type or "집중투표" in agenda:
        return "집중투표 안건은 일반 찬성률 구조와 다르다."
    return None


def _representative_attendance(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], float | None]:
    comparable: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []

    for item in items:
        exclusion = _vote_math_exclusion_reason(item)
        normalized = {
            "number": item.get("number", ""),
            "agenda": item.get("agenda", ""),
            "resolution_type": item.get("resolution_type", ""),
            "passed": item.get("passed", ""),
            "approval_rate_issued": _to_float(item.get("approval_rate_issued")),
            "approval_rate_voted": _to_float(item.get("approval_rate_voted")),
            "opposition_rate": _to_float(item.get("opposition_rate")),
            "estimated_attendance": round(_to_float(item.get("estimated_attendance")), 1) if item.get("estimated_attendance") is not None else None,
        }
        if exclusion:
            excluded.append({**normalized, "reason": exclusion})
            continue
        comparable.append(normalized)

    if not comparable:
        return comparable, excluded, None

    counts = Counter(item["estimated_attendance"] for item in comparable if item.get("estimated_attendance") is not None)
    representative = counts.most_common(1)[0][0] if counts else None
    return comparable, excluded, representative


def _high_opposition_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        opposition = _to_float(item.get("opposition_rate"))
        if opposition >= 10:
            rows.append({
                "number": item.get("number", ""),
                "agenda": item.get("agenda", ""),
                "opposition_rate": round(opposition, 1),
                "passed": item.get("passed", ""),
            })
    return rows


def _failed_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in items:
        passed = (item.get("passed") or "").strip()
        if "부결" in passed:
            rows.append({
                "number": item.get("number", ""),
                "agenda": item.get("agenda", ""),
                "passed": passed,
            })
    return rows


def _signal_level(
    shareholder_side_count: int,
    litigation_count: int,
    active_external_total_pct: float,
    active_overlap_total_pct: float,
    high_opposition_count: int,
    failed_count: int,
) -> str:
    if failed_count > 0:
        return "contestable"
    if shareholder_side_count > 0 and (active_external_total_pct >= 5 or high_opposition_count > 0):
        return "contestable"
    if litigation_count > 0 or active_external_total_pct >= 5 or active_overlap_total_pct >= 5 or high_opposition_count > 0:
        return "watch"
    return "stable"


async def _vote_math_scope_data(
    company_query: str,
    *,
    year: int | None,
    start_date: str,
    end_date: str,
    lookback_months: int,
    summary: dict[str, Any],
    players: dict[str, Any],
    control_map: dict[str, Any],
) -> tuple[dict[str, Any], str, list[str], list[dict[str, Any]], list[str]]:
    warnings: list[str] = []
    result_payload = await build_shareholder_meeting_payload(
        company_query,
        meeting_type="auto",
        scope="results",
        year=year,
        start_date=start_date,
        end_date=end_date,
        lookback_months=lookback_months,
    )

    warnings.extend(result_payload.get("warnings", []))
    result_data = result_payload.get("data", {})
    meeting_ref = {
        "meeting_type": result_data.get("meeting_type", ""),
        "meeting_phase": result_data.get("meeting_phase", ""),
        "result_status": result_data.get("result_status", ""),
        "meeting_date": (result_data.get("selected_meeting") or {}).get("meeting_date"),
        "notice_rcept_no": (result_data.get("selected_meeting") or {}).get("notice_rcept_no", ""),
        "result_rcept_no": (result_data.get("result_reference") or {}).get("rcept_no", ""),
        "result_date": (result_data.get("result_reference") or {}).get("disclosure_date", ""),
    }

    result_items = (result_data.get("results") or {}).get("items", []) or []
    comparable_items, excluded_items, representative_attendance = _representative_attendance(result_items)
    high_opposition_items = _high_opposition_items(result_items)
    failed_items = _failed_items(result_items)

    related_total_pct = _to_float(summary.get("related_total_pct"))
    treasury_pct = _to_float(summary.get("treasury_pct"))
    voting_share_base_pct = round(max(100.0 - treasury_pct, 0.0), 2)
    active_external_total_pct = round(sum(_to_float(row.get("ownership_pct")) for row in control_map.get("active_non_overlap_blocks", [])), 2)
    active_overlap_total_pct = round(sum(_to_float(row.get("ownership_pct")) for row in control_map.get("active_overlap_blocks", [])), 2)

    contestable_turnout_pct = None
    ex_related_turnout_pct = None
    if representative_attendance is not None:
        contestable_turnout_pct = round(max(representative_attendance - related_total_pct, 0.0), 1)
        free_float_base_pct = max(voting_share_base_pct - related_total_pct, 0.0)
        if free_float_base_pct > 0:
            ex_related_turnout_pct = round(contestable_turnout_pct / free_float_base_pct * 100, 1)

    status = AnalysisStatus.EXACT
    interpretation_notes: list[str] = [
        "vote_math는 승패 예측이 아니라 표 구조 신호를 보는 참고 지표다.",
        "대표 추정참석률은 보통결의 안건의 발행기준/행사기준 찬성률 역산값 최빈값을 사용한다.",
    ]
    if result_payload.get("status") == AnalysisStatus.ERROR or result_data.get("result_status") != "available":
        status = AnalysisStatus.REQUIRES_REVIEW
        warnings.append("결과공시가 확보되지 않아 vote_math를 계산하지 못했다.")
    elif representative_attendance is None:
        status = AnalysisStatus.REQUIRES_REVIEW
        warnings.append("비교 가능한 보통결의 안건이 없어 대표 추정참석률을 만들지 못했다.")
    else:
        attendance_values = [item["estimated_attendance"] for item in comparable_items if item.get("estimated_attendance") is not None]
        if len(comparable_items) == 1:
            status = AnalysisStatus.PARTIAL
            warnings.append("비교 가능한 보통결의 안건이 1건뿐이라 대표 추정참석률 신뢰도가 낮다.")
        elif attendance_values and (max(attendance_values) - min(attendance_values)) > 10:
            status = AnalysisStatus.PARTIAL
            warnings.append("보통결의 안건 간 추정참석률 편차가 커 대표값 해석에 주의가 필요하다.")
        if excluded_items:
            interpretation_notes.append("감사위원·집중투표 등 분모가 달라질 수 있는 안건은 대표 참석률 계산에서 제외했다.")

    signal_level = _signal_level(
        shareholder_side_count=summary.get("shareholder_side_count", 0),
        litigation_count=summary.get("litigation_count", 0),
        active_external_total_pct=active_external_total_pct,
        active_overlap_total_pct=active_overlap_total_pct,
        high_opposition_count=len(high_opposition_items),
        failed_count=len(failed_items),
    )

    if signal_level == "contestable":
        interpretation_notes.append("주주측 문서, 능동적 블록, 반대율 신호가 겹쳐 표 대결 가능성을 봐야 한다.")
    elif signal_level == "watch":
        interpretation_notes.append("즉각적인 표 대결 예측보다는 관찰이 필요한 신호가 있다.")
    else:
        interpretation_notes.append("현재 공시 기준으로는 표 계산상 급한 경합 신호는 제한적이다.")

    data = {
        "meeting_reference": meeting_ref,
        "attendance_estimate": {
            "representative_pct": representative_attendance,
            "comparable_item_count": len(comparable_items),
            "excluded_item_count": len(excluded_items),
            "min_pct": min((item["estimated_attendance"] for item in comparable_items), default=None),
            "max_pct": max((item["estimated_attendance"] for item in comparable_items), default=None),
            "methodology": "보통결의 안건의 발행기준 찬성률 / 출석주식수 기준 찬성률 역산값 최빈값",
            "items": comparable_items[:10],
            "excluded_items": excluded_items[:10],
        },
        "capital_structure": {
            "related_total_pct": related_total_pct,
            "treasury_pct": treasury_pct,
            "voting_share_base_pct": voting_share_base_pct,
            "contestable_turnout_pct": contestable_turnout_pct,
            "ex_related_turnout_pct": ex_related_turnout_pct,
            "active_external_block_total_pct": active_external_total_pct,
            "active_overlap_block_total_pct": active_overlap_total_pct,
        },
        "pressure_signals": {
            "shareholder_side_filers": players.get("shareholder_side_filers", []),
            "shareholder_side_count": summary.get("shareholder_side_count", 0),
            "litigation_count": summary.get("litigation_count", 0),
            "active_external_blocks": players.get("active_external_blocks", []),
            "active_overlap_blocks": players.get("active_overlap_blocks", []),
            "high_opposition_items": high_opposition_items[:10],
            "failed_items": failed_items[:10],
        },
        "interpretation": {
            "signal_level": signal_level,
            "notes": interpretation_notes,
        },
    }

    return data, status, warnings, result_payload.get("evidence_refs", []), result_payload.get("next_actions", [])


async def build_proxy_contest_payload(
    company_query: str,
    *,
    scope: str = "summary",
    year: int | None = None,
    start_date: str = "",
    end_date: str = "",
    lookback_months: int = 12,
) -> dict[str, Any]:
    if scope not in _SUPPORTED_SCOPES:
        return _unsupported_scope_payload(company_query, scope)

    resolution = await resolve_company_query(company_query)
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        return ToolEnvelope(
            tool="proxy_contest",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[f"'{company_query}'에 해당하는 회사를 찾지 못했다."],
            data={"query": company_query, "scope": scope},
        ).to_dict()
    if resolution.status == AnalysisStatus.AMBIGUOUS:
        return ToolEnvelope(
            tool="proxy_contest",
            status=AnalysisStatus.AMBIGUOUS,
            subject=company_query,
            warnings=["회사 식별이 애매해 분쟁 공시를 자동 선택하지 않았다."],
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
    bgn_de, end_de, window_year, window_warnings = _window_bounds(
        year,
        start_date=start_date,
        end_date=end_date,
        lookback_months=lookback_months,
    )
    warnings: list[str] = list(window_warnings)

    proxy_rows, proxy_notices, proxy_warning = await _proxy_items(selected["corp_code"], selected.get("corp_name", ""), bgn_de, end_de)
    litigation_rows, litigation_notices, lit_warning = await _litigation_items(selected["corp_code"], bgn_de, end_de)
    signal_rows, signal_warning = await _block_signals(selected["corp_code"])
    control_context, control_warnings = await _control_context(selected["corp_code"], company_query, window_year)

    warnings.extend(proxy_notices)
    warnings.extend(litigation_notices)

    for warning in (proxy_warning, lit_warning, signal_warning, *control_warnings):
        if warning:
            warnings.append(warning)

    control_map = control_context.get("control_map", {})
    overlap_names = {
        _normalize_entity_name(row.get("reporter", ""))
        for row in control_map.get("overlap_blocks", [])
        if _normalize_entity_name(row.get("reporter", ""))
    }
    active_external_names = {
        _normalize_entity_name(row.get("reporter", ""))
        for row in control_map.get("active_non_overlap_blocks", [])
        if _normalize_entity_name(row.get("reporter", ""))
    }

    # 교차 참조 힌트 — 주체(filer) 중심 annotation.
    # 자동 binary 분류(proxy_fight/proxy_campaign) 대신 사실 플래그만 제공하고
    # 애널리스트가 종합 판단하도록 한다.
    litigation_filer_keys = {
        _normalize_entity_name(row.get("filer_name", ""))
        for row in litigation_rows
        if _normalize_entity_name(row.get("filer_name", ""))
    }
    # filer가 5% 경영참여 신고한 주체인지 (external / overlap 여부 무관).
    # 영풍처럼 과거 계열사 이력으로 registry_overlap에 남아있지만 현재는 분쟁 주체인 경우도 포함.
    active_block_all_names = {
        _normalize_entity_name(row.get("reporter", ""))
        for row in (control_map.get("active_non_overlap_blocks", []) + control_map.get("active_overlap_blocks", []))
        if _normalize_entity_name(row.get("reporter", ""))
    }

    enriched_proxy_rows: list[dict[str, Any]] = []
    for row in proxy_rows:
        filer_key = _normalize_entity_name(row.get("filer_name", ""))
        enriched_proxy_rows.append({
            **row,
            "actor_group": _fight_actor_group(row, active_external_names, overlap_names),
            "filer_has_5pct_active_block": filer_key in active_block_all_names,
            "filer_in_litigation": filer_key in litigation_filer_keys,
        })

    enriched_signal_rows: list[dict[str, Any]] = []
    for row in control_map.get("overlap_blocks", []):
        enriched_signal_rows.append({
            **row,
            "actor_side": _signal_actor_side(row),
        })
    for row in control_map.get("non_overlap_blocks", []):
        enriched_signal_rows.append({
            **row,
            "actor_side": _signal_actor_side(row),
        })
    enriched_signal_rows = [
        row for row in enriched_signal_rows
        if _in_window(row.get("report_date", ""), bgn_de, end_de)
    ]
    enriched_signal_rows.sort(key=lambda row: (row.get("report_date", ""), row.get("rcept_no", "")), reverse=True)

    activist_signals = [row for row in enriched_signal_rows if row.get("active_purpose")]
    combined_timeline = [
        *[
            {
                "date": row["disclosure_date"],
                "category": "fight",
                "actor": row["filer_name"],
                "side": row["actor_group"],
                "title": row["report_name"],
                "rcept_no": row["rcept_no"],
            }
            for row in enriched_proxy_rows
        ],
        *[
            {
                "date": row["disclosure_date"],
                "category": "litigation",
                "actor": row["filer_name"],
                "side": "litigation",
                "title": row["report_name"],
                "rcept_no": row["rcept_no"],
            }
            for row in litigation_rows
        ],
        *[
            {
                "date": row["report_date"],
                "category": "signal",
                "actor": row["reporter"],
                "side": row["actor_side"],
                "title": f"{row['reporter']} {row['purpose']}",
                "rcept_no": row["rcept_no"],
            }
            for row in activist_signals
        ],
    ]
    combined_timeline.sort(key=lambda row: (row["date"], row["rcept_no"]), reverse=True)

    company_side_filers = _unique_nonempty([row["filer_name"] for row in enriched_proxy_rows if row["side"] == "company"])
    shareholder_side_filers = _unique_nonempty([row["filer_name"] for row in enriched_proxy_rows if row["side"] == "shareholder"])
    retail_activism_filers = _unique_nonempty([row["filer_name"] for row in enriched_proxy_rows if row["side"] == "retail_activism"])
    active_external_blocks = _unique_nonempty([row["reporter"] for row in activist_signals if row.get("actor_side") == "external_active_block"])
    overlap_blocks = _unique_nonempty([row["reporter"] for row in activist_signals if row.get("actor_side") == "registry_overlap"])

    shareholder_side_rows = [row for row in enriched_proxy_rows if row["side"] == "shareholder"]
    retail_activism_rows = [row for row in enriched_proxy_rows if row["side"] == "retail_activism"]
    # has_contest_signal: 실제 경영권 분쟁 신호만 (주주측 위임장 / 소송 / 외부 활성 5%).
    # retail_activism(소액주주 집단 위임 플랫폼)과 registry_overlap(회사 측 계열사 경영참여 신고)은
    # 분쟁이 아니므로 제외한다.
    external_active_signals = [row for row in activist_signals if row.get("actor_side") == "external_active_block"]
    has_contest_signal = bool(shareholder_side_rows or litigation_rows or external_active_signals)

    data: dict[str, Any] = {
        "query": company_query,
        "company_id": _company_id(selected),
        "canonical_name": selected.get("corp_name", ""),
        "identifiers": {
            "ticker": selected.get("stock_code", ""),
            "corp_code": selected.get("corp_code", ""),
        },
        "window": {
            "start_date": bgn_de,
            "end_date": end_de,
            "anchor_year": window_year,
            "lookback_months": lookback_months,
        },
        "summary": {
            "proxy_filing_count": len(enriched_proxy_rows),
            "shareholder_side_count": len(shareholder_side_rows),
            "retail_activism_count": len(retail_activism_rows),
            "litigation_count": len(litigation_rows),
            "active_signal_count": len(activist_signals),
            "has_contest_signal": has_contest_signal,
            "top_holder": control_context.get("top_holder", {}),
            "related_total_pct": control_context.get("related_total_pct", 0.0),
            "treasury_pct": control_context.get("treasury_pct", 0.0),
            "active_external_block_count": len(active_external_blocks),
            "active_overlap_block_count": len(overlap_blocks),
        },
        "players": {
            "company_side_filers": company_side_filers,
            "shareholder_side_filers": shareholder_side_filers,
            "retail_activism_filers": retail_activism_filers,
            "active_external_blocks": active_external_blocks,
            "active_overlap_blocks": overlap_blocks,
        },
        "control_context": control_map,
        "available_scopes": ["summary", "fight", "litigation", "signals", "timeline", "vote_math"],
    }
    if scope in {"summary", "fight"}:
        data["fight"] = enriched_proxy_rows
    if scope in {"summary", "litigation"}:
        data["litigation"] = litigation_rows
    if scope in {"summary", "signals"}:
        data["signals"] = activist_signals
    if scope == "timeline":
        data["timeline"] = combined_timeline[:50]

    evidence_refs: list[EvidenceRef] = []
    if enriched_proxy_rows:
        top_proxy = enriched_proxy_rows[0]
        evidence_refs.append(
            EvidenceRef(
                evidence_id=f"ev_proxy_{top_proxy['rcept_no']}",
                source_type=SourceType.DART_XML,
                rcept_no=top_proxy["rcept_no"],
                rcept_dt=format_iso_date(top_proxy.get("disclosure_date", "")),
                report_nm=top_proxy.get("report_name", ""),
                section="위임장/공개매수 공시",
                note=f"{top_proxy.get('filer_name', '')}",
            )
        )
    if litigation_rows:
        top_lit = litigation_rows[0]
        evidence_refs.append(
            EvidenceRef(
                evidence_id=f"ev_litigation_{top_lit['rcept_no']}",
                source_type=SourceType.DART_XML,
                rcept_no=top_lit["rcept_no"],
                rcept_dt=format_iso_date(top_lit.get("disclosure_date", "")),
                report_nm=top_lit.get("report_name", ""),
                section="소송/분쟁 공시",
                note=top_lit.get("filer_name", ""),
            )
        )
    if activist_signals and activist_signals[0].get("rcept_no"):
        top_signal = activist_signals[0]
        evidence_refs.append(
            EvidenceRef(
                evidence_id=f"ev_signal_{top_signal['rcept_no']}",
                source_type=SourceType.DART_XML,
                rcept_no=top_signal["rcept_no"],
                rcept_dt=format_iso_date(top_signal.get("report_date", "")),
                report_nm=top_signal.get("report_name", ""),
                section="대량보유 상황보고",
                note=f"{top_signal.get('reporter', '')} / {top_signal.get('purpose', '')}",
            )
        )

    next_actions = [
        "timeline scope로 전체 이벤트 순서 확인" if scope == "summary" else "shareholder_meeting, ownership_structure와 함께 보면 표대결 맥락이 더 선명해진다.",
    ]
    status = AnalysisStatus.EXACT if (enriched_proxy_rows or litigation_rows or activist_signals) else AnalysisStatus.PARTIAL
    if scope == "vote_math":
        vote_math, vote_math_status, vote_math_warnings, vote_math_evidence, vote_math_actions = await _vote_math_scope_data(
            company_query,
            year=year,
            start_date=start_date,
            end_date=end_date,
            lookback_months=lookback_months,
            summary=data["summary"],
            players=data["players"],
            control_map=control_map,
        )
        data["vote_math"] = vote_math
        warnings.extend(vote_math_warnings)
        for ref in vote_math_evidence:
            evidence_refs.append(ref)
        if vote_math_actions:
            next_actions = vote_math_actions
        status = vote_math_status
    elif status == AnalysisStatus.PARTIAL:
        warnings.append("분쟁 관련 공시가 없거나 충분하지 않아 partial 상태로 표시한다.")

    return ToolEnvelope(
        tool="proxy_contest",
        status=status,
        subject=selected.get("corp_name", company_query),
        warnings=warnings,
        data=data,
        evidence_refs=evidence_refs,
        next_actions=next_actions,
    ).to_dict()
