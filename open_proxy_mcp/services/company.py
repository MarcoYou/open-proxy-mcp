"""company facade 서비스."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date, timedelta
import json
from pathlib import Path
import re
import time
from typing import Any

from open_proxy_mcp.dart.client import (
    _CORP_ALIASES,
    _normalize_corp_name,
    _sort_corp_results,
    DartClientError,
    get_dart_client,
)
from open_proxy_mcp.services.contracts import (
    AnalysisStatus,
    ToolEnvelope,
    build_filing_meta,
    build_usage,
)
from open_proxy_mcp.services.date_utils import format_yyyymmdd, resolve_date_window

_RECENT_LOOKBACK_DAYS = 180
_KSIC_PATH = Path(__file__).resolve().parent.parent / "data/ksic/ksic10_ko.json"
try:
    _KSIC_MAP: dict[str, str] = json.loads(_KSIC_PATH.read_text(encoding="utf-8"))
except Exception:
    _KSIC_MAP = {}


def _prefers_english(query: str, language: str = "auto") -> bool:
    if language in {"ko", "en"}:
        return language == "en"
    return bool(re.search(r"[A-Za-z]", query or "")) and not bool(re.search(r"[가-힣]", query or ""))


def _resolve_induty_name(induty_code: str) -> str:
    code = (induty_code or "").strip()
    if not code:
        return ""
    if code in _KSIC_MAP:
        return _KSIC_MAP[code]
    for end in range(len(code) - 1, 1, -1):
        prefix = code[:end]
        if prefix in _KSIC_MAP:
            return _KSIC_MAP[prefix]
    return ""


@dataclass(slots=True)
class CompanyResolution:
    """후속 data tool용 회사 식별 결과."""

    status: AnalysisStatus
    query: str
    selected: dict[str, Any] | None
    candidates: list[dict[str, Any]]
    resolution: dict[str, Any] | None = None


def _company_id(corp: dict[str, Any]) -> str:
    stock_code = (corp.get("stock_code") or "").strip()
    return f"cmp_{stock_code or corp.get('corp_code', '')}"


def _resolve_match(query: str, matches: list[dict[str, Any]]) -> tuple[AnalysisStatus, dict[str, Any] | None, list[dict[str, Any]]]:
    """이름→코드가 **확정되는 유일한 관문**. 확정된 1건만 요청 장부에 적는다.

    호출부마다 따로 걸지 않는 이유: 상위 진입점이 둘이고(`build_company_payload` 는
    company tool, `resolve_company_query` 는 나머지 tool) 실측에서 한쪽만 걸었더니
    company tool 이 통째로 빠졌다. 확정 지점은 여기 하나뿐이라 여기서 적는다.
    AMBIGUOUS(후보만 있고 못 고름)면 selected 가 None 이라 자연히 안 적힌다 —
    「무엇을 조사했나」는 서버가 그 기업이라고 결론 낸 것만이다.
    """
    status, selected, candidates = _resolve_match_impl(query, matches)
    if selected:
        from open_proxy_mcp.dart.client import _note_corp, note_weak_resolution
        _note_corp(selected.get("corp_code"))
        meta = selected.get("_resolution") or {}
        if meta.get("inferred"):
            note_weak_resolution(
                query.strip(),
                selected.get("corp_name", ""),
                meta.get("match_kind", ""),
                int(meta.get("candidate_count") or len(candidates) or 1),
            )
    return status, selected, candidates


def _resolve_match_impl(query: str, matches: list[dict[str, Any]]) -> tuple[AnalysisStatus, dict[str, Any] | None, list[dict[str, Any]]]:
    raw = query.strip()
    if not matches:
        return AnalysisStatus.ERROR, None, []

    resolver_meta = matches[0].get("_resolution") or {}
    if resolver_meta.get("match_kind"):
        kind = resolver_meta.get("match_kind")
        if kind in {"ticker", "corp_code"}:
            return AnalysisStatus.EXACT, matches[0], matches
        if not resolver_meta.get("inferred"):
            if len(matches) == 1 or resolver_meta.get("strong_disambiguated"):
                return AnalysisStatus.EXACT, matches[0], matches
            return AnalysisStatus.AMBIGUOUS, None, matches
        if resolver_meta.get("auto_selected"):
            return AnalysisStatus.EXACT, matches[0], matches
        return AnalysisStatus.AMBIGUOUS, None, matches

    if re.fullmatch(r"\d{6}", raw):
        numeric = [corp for corp in matches if corp.get("stock_code") == raw]
        if len(numeric) == 1:
            return AnalysisStatus.EXACT, numeric[0], matches

    if re.fullmatch(r"\d{8}", raw):
        numeric = [corp for corp in matches if corp.get("corp_code") == raw]
        if len(numeric) == 1:
            return AnalysisStatus.EXACT, numeric[0], matches

    alias_query = _CORP_ALIASES.get(raw.lower(), raw)
    exact = [corp for corp in matches if corp.get("corp_name") == alias_query]
    if len(exact) == 1:
        return AnalysisStatus.EXACT, exact[0], matches
    if len(exact) > 1:
        ranked = _sort_corp_results(exact)
        top = ranked[0]
        second = ranked[1] if len(ranked) > 1 else None
        if top.get("stock_code") and (
            second is None
            or not second.get("stock_code")
            or (top.get("modify_date") or "") > (second.get("modify_date") or "")
        ):
            return AnalysisStatus.EXACT, top, ranked
        return AnalysisStatus.AMBIGUOUS, None, ranked

    norm_query = _normalize_corp_name(alias_query)
    normalized = [
        corp for corp in matches
        if _normalize_corp_name(corp.get("corp_name", "")) == norm_query
    ]
    if len(normalized) == 1:
        return AnalysisStatus.EXACT, normalized[0], matches
    if len(normalized) > 1:
        ranked = _sort_corp_results(normalized)
        top = ranked[0]
        second = ranked[1] if len(ranked) > 1 else None
        if top.get("stock_code") and (
            second is None
            or not second.get("stock_code")
            or (top.get("modify_date") or "") > (second.get("modify_date") or "")
        ):
            return AnalysisStatus.EXACT, top, ranked
        return AnalysisStatus.AMBIGUOUS, None, ranked

    # 완전일치·정규화일치가 없어도 상장사 후보가 유일하면 그 후보를 채택한다.
    # (예: "금호석유"→"금호석유화학" 같은 정식명 축약/부분입력). 후보가 하나뿐이라 모호하지 않다.
    # 여러 개면 위에서 이미 AMBIGUOUS로 처리됨(예: "금호"→타이어/건설/석유화학).
    if len(matches) == 1:
        return AnalysisStatus.EXACT, matches[0], matches

    return AnalysisStatus.AMBIGUOUS, None, matches


def _aliases_for_company(corp_name: str, query: str) -> list[str]:
    aliases = [key for key, value in _CORP_ALIASES.items() if value == corp_name]
    deduped: list[str] = []
    seen: set[str] = set()
    for alias in aliases:
        alias = alias.strip()
        if alias and alias not in seen:
            seen.add(alias)
            deduped.append(alias)
    return deduped[:10]


def _classify_filing(item: dict[str, Any]) -> str:
    report_name = (item.get("report_nm") or "").replace(" ", "")
    if "주주총회소집공고" in report_name:
        return "shareholder_meeting_notice"
    if "주주총회결과" in report_name:
        return "shareholder_meeting_result"
    if "현금ㆍ현물배당결정" in report_name or "현금·현물배당결정" in report_name:
        return "dividend_decision"
    if "기업가치제고계획" in report_name:
        return "value_up"
    if "위임장권유" in report_name:
        return "proxy_solicitation"
    if "공개매수" in report_name:
        return "tender_offer"
    if "대량보유" in report_name or "보유상황보고" in report_name:
        return "ownership_block"
    if "소송" in report_name or "가처분" in report_name:
        return "litigation"
    if item.get("pblntf_ty") == "I":
        return "exchange_disclosure"
    return "other"


async def _safe_company_info(corp_code: str) -> tuple[dict[str, Any], str | None]:
    client = get_dart_client()
    try:
        return await client.get_company_info(corp_code), None
    except DartClientError as exc:
        return {}, f"DART company.json 조회 실패: {exc.status}"

async def _safe_recent_filings(
    corp_code: str,
    max_items: int,
    *,
    start_date: str = "",
    end_date: str = "",
) -> tuple[list[dict[str, Any]], dict[str, str], str | None]:
    client = get_dart_client()
    begin_date, finish_date, window_warnings = resolve_date_window(
        start_date=start_date,
        end_date=end_date,
        default_end=date.today(),
        lookback_days=_RECENT_LOOKBACK_DAYS,
    )
    try:
        result = await client.search_filings(
            corp_code=corp_code,
            bgn_de=format_yyyymmdd(begin_date),
            end_de=format_yyyymmdd(finish_date),
            page_count=min(max(max_items * 3, 20), 100),
        )
    except DartClientError as exc:
        return [], {"start_date": format_yyyymmdd(begin_date), "end_date": format_yyyymmdd(finish_date)}, f"최근 공시 인덱스 조회 실패: {exc.status}"

    filings: list[dict[str, Any]] = []
    for item in result.get("list", [])[:max_items]:
        filings.append({
            "filing_type": _classify_filing(item),
            "report_name": (item.get("report_nm") or "").strip(),
            "disclosure_date": item.get("rcept_dt", ""),
            "rcept_no": item.get("rcept_no", ""),
            "filer_name": item.get("flr_nm", ""),
            "pblntf_ty": item.get("pblntf_ty", ""),
        })
    warning = " / ".join(window_warnings) if window_warnings else None
    return filings, {"start_date": format_yyyymmdd(begin_date), "end_date": format_yyyymmdd(finish_date)}, warning


def _candidate_row(corp: dict[str, Any]) -> dict[str, Any]:
    return {
        "company_id": _company_id(corp),
        "corp_name": corp.get("corp_name", ""),
        "corp_name_eng": corp.get("corp_eng_name", ""),
        "ticker": corp.get("stock_code", ""),
        "corp_code": corp.get("corp_code", ""),
        "modify_date": corp.get("modify_date", ""),
        "resolution": corp.get("_resolution", {}),
    }


def _resolution_reasons(
    kind: str,
    active_registry_used: bool,
    ranking_signal: str,
) -> dict[str, str]:
    ko_universe = "활성 상장사" if active_registry_used else "종목코드 보유 법인"
    en_universe = "active listed companies" if active_registry_used else "companies with a ticker"
    ko_rank = "시가총액" if ranking_signal == "market_cap" else "로컬 인기도 prior"
    en_rank = "market capitalization" if ranking_signal == "market_cap" else "the local popularity prior"
    return {
        "ko": {
            "token": f"입력 토큰을 모두 포함하는 {ko_universe} 후보 중 {ko_rank} 우선",
            "substring": f"부분 회사명과 일치하는 {ko_universe} 후보 중 {ko_rank} 우선",
            "fuzzy": f"제한적 오타 교정 후 {ko_universe} 후보 중 {ko_rank} 우선",
        }.get(kind, "공식명 또는 등록 별칭 일치"),
        "en": {
            "token": f"All query tokens matched; ranked {en_universe} by {en_rank}",
            "substring": f"Partial name matched; ranked {en_universe} by {en_rank}",
            "fuzzy": f"Applied limited typo correction, then ranked {en_universe} by {en_rank}",
        }.get(kind, "Matched an official name or registered alias"),
    }


def _resolution_payload(
    query: str,
    selected: dict[str, Any],
    candidates: list[dict[str, Any]],
    language: str = "auto",
) -> dict[str, Any]:
    meta = selected.get("_resolution") or {}
    inferred = bool(meta.get("inferred"))
    alternatives = [_candidate_row(corp) for corp in candidates[1:4]] if inferred else []
    kind = meta.get("match_kind", "official")
    english = _prefers_english(query, language)
    reason_i18n = _resolution_reasons(
        kind,
        bool(meta.get("active_registry_used")),
        str(meta.get("ranking_signal") or "local_popularity_prior"),
    )
    reason = reason_i18n["en" if english else "ko"]
    return {
        "query": query,
        "response_language": "en" if english else "ko",
        "match_type": "inferred" if inferred else ("alias" if kind == "alias" else "canonical"),
        "matched_on": kind,
        "confidence": "high" if not inferred or meta.get("dominant") else "low",
        "reason": reason,
        "reason_i18n": reason_i18n,
        "market_data_as_of": meta.get("market_data_as_of"),
        "market_data_source": meta.get("market_data_source"),
        "ranking_signal": meta.get("ranking_signal"),
        "alternatives": alternatives,
    }


async def build_company_payload(
    query: str,
    *,
    max_recent_filings: int = 10,
    start_date: str = "",
    end_date: str = "",
    language: str = "auto",
) -> dict[str, Any]:
    """회사 식별 + 최근 공시 인덱스."""

    total_started_at = time.perf_counter()
    timings_ms: dict[str, int] = {}

    def _mark(stage: str, started_at: float) -> None:
        timings_ms[stage] = int((time.perf_counter() - started_at) * 1000)

    client = get_dart_client()
    _calls_start = client.api_call_snapshot()
    stage_started_at = time.perf_counter()
    matches = await client.lookup_corp_code_all(query)
    _mark("lookup_corp_code_all", stage_started_at)

    raw = (query or "").strip()
    numeric_query = re.fullmatch(r"\d{6}", raw) or re.fullmatch(r"\d{8}", raw)
    unlisted_only = False
    if not numeric_query and matches:
        listed = [m for m in matches if (m.get("stock_code") or "").strip()]
        if listed:
            matches = listed
        else:
            unlisted_only = True
            matches = []

    status, selected, candidates = _resolve_match(query, matches)

    if status == AnalysisStatus.ERROR:
        english = _prefers_english(query, language)
        # 못 찾았으면 끝내지 말고 근접 후보를 보여준다 — 개명·상장폐지·접미가 붙은 상호를
        # 사용자가 알아보고 고를 수 있다(「에이플러스에셋어드바이저」→「에이플러스에셋」).
        # 자동 선택은 하지 않는다 — 앞자르기 자동선택은 오답을 낸다(포스코디엑스→POSCO홀딩스).
        try:
            _near = [
                {"corp_name": c.get("corp_name"), "stock_code": c.get("stock_code"),
                 "corp_code": c.get("corp_code")}
                for c in await client.suggest_corp_candidates(query)
            ]
        except Exception:
            _near = []
        warnings = [
            (f"No listed company matched '{query}'. A company that has been renamed will not match "
             "its former name — the registry carries current names only. Retry with the 6-digit "
             "ticker, which survives a rename.")
            if english else company_not_found_warning(query)
        ]
        if unlisted_only:
            warnings.append("The matching entity is unlisted and outside OPM's listed-company universe." if english else "입력에 일치하는 법인은 비상장이어서 OPM 분석 대상(상장사)에서 제외했다. 정확한 상장사 종목명/종목코드로 다시 조회한다.")
        envelope = ToolEnvelope(
            tool="company",
            status=AnalysisStatus.ERROR,
            subject=query,
            warnings=warnings,
            data={
                "query": query,
                # resolution 이 담아온 근접 후보를 버리지 않는다 — 못 찾았을 때 사용자가
                # 고를 수 있게 보여준다(자동 선택은 하지 않는다).
                "candidates": _near,
                "usage": build_usage(client.api_call_snapshot() - _calls_start),
                "timings_ms": {**timings_ms, "total": int((time.perf_counter() - total_started_at) * 1000)},
            },
            next_actions=["Retry with an exact company name, ticker, or corp_code" if english else "정확한 회사명, 종목코드, corp_code 중 하나로 다시 조회"],
        )
        return envelope.to_dict()

    if status == AnalysisStatus.AMBIGUOUS or not selected:
        english = _prefers_english(query, language)
        envelope = ToolEnvelope(
            tool="company",
            status=AnalysisStatus.AMBIGUOUS,
            subject=query,
            warnings=["Multiple equally strong matches are shown in ranked order." if english else "동일하게 강한 후보를 가능성 높은 순서로 표시합니다."],
            data={
                "query": query,
                "candidates": [_candidate_row(corp) for corp in candidates[:10]],
                "usage": build_usage(client.api_call_snapshot() - _calls_start),
                "timings_ms": {**timings_ms, "total": int((time.perf_counter() - total_started_at) * 1000)},
            },
            next_actions=[],
        )
        return envelope.to_dict()

    company_info_task = _safe_company_info(selected["corp_code"])
    filings_task = _safe_recent_filings(
        selected["corp_code"],
        max_recent_filings,
        start_date=start_date,
        end_date=end_date,
    )

    stage_started_at = time.perf_counter()
    (company_info, company_warn), (recent_filings, filings_window, filings_warn) = await asyncio.gather(
        company_info_task,
        filings_task,
    )
    _mark("company_info_and_recent_filings", stage_started_at)

    corp_name = company_info.get("corp_name") or selected.get("corp_name", "")
    corp_name_eng = company_info.get("corp_name_eng") or selected.get("corp_eng_name", "")
    corp_cls = company_info.get("corp_cls", "")
    market_map = {
        "Y": "KOSPI",
        "K": "KOSDAQ",
        "N": "KONEX",
        "E": "비상장",
    }
    warnings = [warning for warning in (company_warn, filings_warn) if warning]
    if not company_info.get("jurir_no"):
        warnings.append("ISIN is not connected to the company tool yet." if _prefers_english(query, language) else "ISIN은 아직 company tool에 연결되지 않았다.")

    # company tool은 회사 정보가 항상 있어 no_filing 케이스가 거의 없다.
    # 다만 recent_filings 0건은 NO_FILING으로 표시 (정상). company_info 자체가
    # 없는 경우 (corp_code 미등록)는 위에서 ERROR로 분기되어 여기 도달 안 함.
    filing_meta = build_filing_meta(
        filing_count=len(recent_filings),
        parsing_failures=0,
    )

    payload = {
        "query": query,
        "company_resolution": _resolution_payload(query, selected, candidates, language),
        "company_id": _company_id(selected),
        "canonical_name": corp_name,
        "identifiers": {
            "ticker": selected.get("stock_code", ""),
            "corp_code": selected.get("corp_code", ""),
            "isin": "",
            "jurir_no": company_info.get("jurir_no", ""),
            "bizr_no": company_info.get("bizr_no", ""),
        },
        "classification": {
            "market": market_map.get(corp_cls, corp_cls or "미상"),
            "corp_cls": corp_cls,
            "sector_name": _resolve_induty_name(company_info.get("induty_code", "")),
            "sector_code": "",
            "induty_code": company_info.get("induty_code", ""),
            "fiscal_month": company_info.get("acc_mt", ""),
        },
        "names": {
            "ko": corp_name,
            "en": corp_name_eng,
            "aliases": _aliases_for_company(corp_name, query),
        },
        "basic_info": {
            "ceo_name": company_info.get("ceo_nm", ""),
            "homepage": company_info.get("hm_url", ""),
            "address": company_info.get("adres", ""),
            "established_date": company_info.get("est_dt", ""),
        },
        "recent_filings_window": filings_window,
        "recent_filings": recent_filings,
        **filing_meta,
        "usage": build_usage(client.api_call_snapshot() - _calls_start),
    }
    timings_ms["total"] = int((time.perf_counter() - total_started_at) * 1000)
    payload["timings_ms"] = timings_ms

    envelope = ToolEnvelope(
        tool="company",
        status=AnalysisStatus.EXACT,
        subject=corp_name,
        warnings=warnings,
        data=payload,
        next_actions=[
            ("Use company_id or ticker with downstream tools such as shareholder_meeting, ownership_structure, and dividend"
             if _prefers_english(query, language) else "shareholder_meeting, ownership_structure, dividend 등 후속 data tool에서 company_id 또는 ticker 사용"),
        ],
    )
    return envelope.to_dict()


def company_not_found_warning(query: str, *, listed_only: bool = False) -> str:
    """회사를 못 찾았을 때의 안내 — 「없다」로 끝내지 않는다.

    DART 회사 목록에는 **현재 사명만** 있다. 그래서 사명을 바꾼 회사는 옛 이름으로 조회하면
    한 건도 안 나오는데, 이때 「찾지 못했다」만 돌려주면 회사가 없는 것인지 이름이 바뀐 것인지
    구분할 수가 없다. 하필 사명 변경은 지배구조 분쟁 직후에 잦아서, 의결권 분석이 가장 필요한
    국면과 겹친다(실측: 영풍정밀 → 케이젯정밀, 종목코드 036560 그대로).

    종목코드는 사명이 바뀌어도 유지되니 그것을 탈출구로 안내한다.
    """
    subject = "상장사" if listed_only else "회사"

    # 사명 이력에 있으면 「없다」가 아니라 **어디로 갔는지**를 말한다.
    from open_proxy_mcp.dart.client import DartClient

    renamed = DartClient.lookup_former_name(query)
    if renamed:
        ticker = f"(종목코드 {renamed['stock_code']}) " if renamed.get("stock_code") else ""
        return (
            f"'{query}'는 사명이 바뀌었다 — 현재 '{renamed['current_name']}' {ticker}다. "
            f"그 이름으로 다시 조회한다."
        )

    return (
        f"'{query}'에 해당하는 {subject}를 찾지 못했다. "
        "사명이 바뀐 회사는 옛 이름으로 조회되지 않는다(회사 목록에 현재 사명만 있다) — "
        "종목코드 6자리로 다시 조회하면 사명이 바뀌어도 찾을 수 있다."
    )


async def resolve_company_query(query: str) -> CompanyResolution:
    """회사 입력을 exact/ambiguous/error 상태로 정규화.

    OPM은 상장사(주총/배당/지분) 분석 도구이므로 stock_code가 없는 비상장 법인은
    후보에서 제외한다. 단, corp_code/stock_code를 숫자로 직접 입력한 경우는 예외.
    """

    client = get_dart_client()
    matches = await client.lookup_corp_code_all(query)

    raw = (query or "").strip()
    numeric_query = re.fullmatch(r"\d{6}", raw) or re.fullmatch(r"\d{8}", raw)
    if not numeric_query:
        listed = [m for m in matches if (m.get("stock_code") or "").strip()]
        if listed:
            matches = listed
        elif matches:
            # 상장사 후보가 없고 비상장만 남은 경우: OPM 유니버스 밖이므로 error로 유도
            return CompanyResolution(
                status=AnalysisStatus.ERROR,
                query=query,
                selected=None,
                candidates=[],
            )

    status, selected, candidates = _resolve_match(query, matches)
    if status == AnalysisStatus.ERROR and not selected:
        # 못 찾았으면 끝내지 말고 근접 후보를 보여준다 — 개명·상장폐지·접미가 붙은
        # 상호를 사용자가 알아보고 고를 수 있다(실측: 「에이플러스에셋어드바이저」→
        # 「에이플러스에셋」). 자동 선택은 하지 않는다 — 앞자르기 자동선택은 오답을 낸다.
        try:
            near = await client.suggest_corp_candidates(query)
        except Exception:
            near = []
        if near:
            return CompanyResolution(
                status=status, query=query, selected=None,
                candidates=near,
            )
    return CompanyResolution(
        status=status,
        query=query,
        selected=selected,
        candidates=candidates,
        resolution=_resolution_payload(query, selected, candidates) if selected else None,
    )
