"""proxy_contest facade 서비스."""

from __future__ import annotations

import asyncio
from collections import Counter
from datetime import date, timedelta
import re
import time
from typing import Any

from open_proxy_mcp.dart.client import DartClientError, get_dart_client
from open_proxy_mcp.services.company import _company_id, resolve_company_query
from open_proxy_mcp.services.company import company_not_found_warning
from open_proxy_mcp.services.contracts import (
    AnalysisStatus,
    EvidenceRef,
    SourceType,
    ToolEnvelope,
    build_filing_meta,
    build_usage,
    status_from_filing_meta,
)
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
from open_proxy_mcp.services.ownership_parser import parse_holding_purpose, parse_holding_purpose_from_document

_SUPPORTED_SCOPES = {"summary", "fight", "litigation", "signals", "timeline", "vote_math", "insiders"}
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
        pblntf_tys="",
        # 5%대량보유/위임장/공개매수. 임원·주요주주 소유상황은 여기 넣지 않는다 —
        # `_PROXY_KEYWORDS` 에 그 제목이 없어 한 건도 안 늘고 페이지컷만 유발한다(삼성 +8콜,
        # 가짜 truncation 경고). 그 정보는 `_insider_holdings`(elestock)가 따로 가져온다.
        pblntf_detail_ty=["D001", "D003", "D004"],
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


# ── 임원·주요주주 특정증권등 소유상황보고 (DART D002 / elestock) ────────────────
#
# 260828 재개방. 종전 주석은 「D002(임원수천건) 제외로 페이지컷 truncation 교정」이었고
# 그 판단 자체는 옳았다 — 다만 **끄는 방식이 정보를 통째로 버렸다.**
#
# 실측(260828, 최근 12개월 창):
#   한국앤컴퍼니 D002 3건 · 태광산업 0건(013) · 금호석유화학 36건 · 고려아연 2건 · 삼성전자 2,739건
#   → 분쟁 종목은 2~36건으로 감당된다. 폭주하는 것은 삼성전자류 대형주뿐이다.
#
# **list.json(D002)로는 이 정보가 애초에 나오지 않는다.** `_proxy_items` 는 제목 키워드
# (`_PROXY_KEYWORDS`)로 거르는데 「임원ㆍ주요주주특정증권등소유상황보고서」는 거기 없다.
# 실측: `_proxy_items` 에 D002 를 넣어도 matched 는 5사 전부 그대로였고, 삼성만 API 콜이
# 3→11 로 늘고 「28페이지 중 10페이지만 확인」이라는 **가짜 truncation 경고**가 붙었다.
# 그래서 `_proxy_items` 의 상세유형은 손대지 않는다(페이지컷 회귀 위험 0).
#
# 같은 공시를 주는 **정형 API `elestock.json` 을 회사 단위에서만** 켠다 — 1콜로 전체 이력을
# 주고 보고자·직위·등기여부·주요주주구분·소유수·증감수·소유비율까지 파싱된 채 온다.
# 분쟁 국면에서 지배주주·특수관계인이 5% 문턱 아래로 매집하는 움직임이 정확히 여기 있다.
_INSIDER_ROWS_LIMIT_DEFAULT = 500
_INSIDER_ROWS_LIMIT_MAX = 5000
_INSIDER_RECENT_DAYS = 90
_INSIDER_REPORT_NAME = "임원ㆍ주요주주 특정증권등 소유상황보고서"
# 「주요주주 아님」을 뜻하는 DART 빈칸 표기.
_INSIDER_BLANKS = frozenset({"", "-", "–", "—", "해당사항없음", "N/A"})


def _insider_int(value: Any) -> int | None:
    """DART 콤마 숫자 → int. 빈칸("-")은 **0이 아니라 None** (회사가 안 적은 것)."""
    text = str(value or "").strip().replace(",", "").replace(" ", "")
    if text in _INSIDER_BLANKS:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _insider_float(value: Any) -> float | None:
    text = str(value or "").strip().replace(",", "").replace("%", "").replace(" ", "")
    if text in _INSIDER_BLANKS:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _insider_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return None if text in _INSIDER_BLANKS else text


def _insider_shift_days(yyyymmdd: str, days: int) -> str:
    try:
        anchor = date(int(yyyymmdd[0:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:8]))
    except (ValueError, IndexError):
        return ""
    return format_yyyymmdd(anchor - timedelta(days=days))


def _aggregate_insider_rows(
    rows: list[dict[str, Any]],
    *,
    recent_since: str,
) -> list[dict[str, Any]]:
    """보고 건별 raw 를 **보고자 단위**로 접는다 — 누가 · 기간 · 순증감 · 최근 매집.

    임원 보고를 건별로 다 나열하면 노이즈다(삼성 2,739건). 분쟁 판단에 쓰이는 축만 남기고
    원문 접근 경로(접수번호)는 보고자마다 최근 5건까지 붙여 둔다.

    🔴 **증감수를 그냥 더하면 안 된다** (260828 실측, 금호석유화학 국민연금공단).
    elestock 에는 `sp_stock_lmp_irds_cnt == sp_stock_lmp_cnt` 인 행이 섞여 있다 — 보유 전량을
    「증감」칸에 적는 **신규·재보고** 행이다. 이걸 같이 더하면 국민연금 순증감이 +13,710,029주로
    나오는데 실제 보유는 2,752,107주다(9.77%). 5배 과장이다.
    그래서 순증감은 **보유 수량의 차이**(창 안 첫 보고 → 최근 보고)로 낸다. 이 값은 신규보고
    행이 끼어도 흔들리지 않는다. 보고서가 스스로 적은 증감 합계는 신규·재보고 행을 빼고
    `reported_change_sum` 으로 **따로** 준다 — 두 값이 갈리면 그 사실 자체가 정보다.

    **못 읽은 값은 0으로 채우지 않는다.** 소유수/증감수가 공시에 「-」로 비어 있으면 None 이
    그대로 올라가고, 그런 보고가 몇 건이었는지 `unparsed_change_count` 로 같이 준다.
    """
    by_reporter: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        name = (row.get("repror") or "").strip()
        if not name:
            continue
        by_reporter.setdefault(name, []).append(row)

    out: list[dict[str, Any]] = []
    for name, group in by_reporter.items():
        chrono = sorted(group, key=lambda r: (_normalize_date_key(r.get("rcept_dt", "")), r.get("rcept_no", "")))

        parsed = []
        for r in chrono:
            shares = _insider_int(r.get("sp_stock_lmp_cnt"))
            change = _insider_int(r.get("sp_stock_lmp_irds_cnt"))
            # 신규·재보고 행 — 보유 전량이 증감칸에 그대로 들어간다. 매수가 아니다.
            initial = shares is not None and change is not None and shares == change and shares != 0
            parsed.append({"row": r, "shares": shares, "change": change, "initial": initial})

        incremental = [p for p in parsed if not p["initial"] and p["change"] is not None]
        reported_sum = sum(p["change"] for p in incremental) if incremental else None
        initial_count = sum(1 for p in parsed if p["initial"])
        unparsed = sum(1 for p in parsed if p["change"] is None)

        levels = [p["shares"] for p in parsed if p["shares"] is not None]
        shares_first = levels[0] if levels else None
        shares_last = levels[-1] if levels else None
        # 순증감 = 보유 수량의 차이(기준 1). 보고가 1건뿐이면 회사가 그 보고에 적은 증감을
        # 쓴다(기준 2) — 다만 신규·재보고 행은 전량이 증감칸에 들어가므로 쓸 수 없다.
        # 둘 다 없으면 **모른다**고 말한다. 0으로 채우지 않는다.
        if len(levels) >= 2:
            net_change = shares_last - shares_first
            net_basis = "levels"
        elif reported_sum is not None:
            net_change = reported_sum
            net_basis = "reported"
        else:
            net_change = None
            net_basis = "initial_report_only" if initial_count else "unknown"

        recent = [
            p for p in parsed
            if recent_since and _normalize_date_key(p["row"].get("rcept_dt", "")) >= recent_since
        ]
        recent_incremental = [p for p in recent if not p["initial"] and p["change"] is not None]
        recent_sum = sum(p["change"] for p in recent_incremental) if recent_incremental else None
        recent_levels = [p["shares"] for p in recent if p["shares"] is not None]
        recent_net = (
            recent_levels[-1] - recent_levels[0] if len(recent_levels) >= 2 else None
        )

        pct_first = _insider_float(parsed[0]["row"].get("sp_stock_lmp_rate"))
        pct_last = _insider_float(parsed[-1]["row"].get("sp_stock_lmp_rate"))

        if net_change is None:
            direction = "unknown"
        elif net_change > 0:
            direction = "increasing"
        elif net_change < 0:
            direction = "decreasing"
        else:
            direction = "flat"

        # 직위·등기여부·주요주주구분은 보고마다 바뀔 수 있어 **최신 보고** 값을 쓴다.
        latest = parsed[-1]["row"]
        out.append({
            "reporter": name,
            "position": _insider_text(latest.get("isu_exctv_ofcps")),
            "registered_executive": _insider_text(latest.get("isu_exctv_rgist_at")),
            # "10%이상주주" / "사실상지배주주" 등. 임원일 뿐이면 None.
            "major_shareholder_type": _insider_text(latest.get("isu_main_shrholdr")),
            "report_count": len(parsed),
            "first_date": format_iso_date(_normalize_date_key(parsed[0]["row"].get("rcept_dt", ""))),
            "last_date": format_iso_date(_normalize_date_key(latest.get("rcept_dt", ""))),
            "shares_first": shares_first,
            "shares_last": shares_last,
            # 보유 수량 차이. 신규보고 행이 섞여도 흔들리지 않는 기준이다.
            "net_change_shares": net_change,
            "net_change_basis": net_basis,
            # 공시가 스스로 적은 증감의 합 (신규·재보고 행 제외). net_change 와 갈릴 수 있다 —
            # 갈리면 신규보고 행에 담긴 변동이 증감칸에 안 잡혔다는 뜻이다.
            "reported_change_sum": reported_sum,
            "initial_report_count": initial_count,
            "initial_report_in_window": bool(parsed and parsed[0]["initial"]),
            "unparsed_change_count": unparsed,
            "ownership_pct_first": pct_first,
            "ownership_pct_last": pct_last,
            "ownership_pct_change_pp": (
                round(pct_last - pct_first, 4) if pct_first is not None and pct_last is not None else None
            ),
            "direction": direction,
            "recent_window": {
                "days": _INSIDER_RECENT_DAYS,
                "since": format_iso_date(recent_since) if recent_since else "",
                "report_count": len(recent),
                "net_change_shares": recent_net,
                "reported_change_sum": recent_sum,
                # 최근 창에서 **순증가**면 매집. 신규보고 행은 빼고 본다(전량이 증감으로 잡혀
                # 「매집」으로 오인된다). 보유 수량 차이가 있으면 그것을 먼저 믿는다.
                "accumulating": bool(
                    (recent_net is not None and recent_net > 0)
                    or (recent_net is None and recent_sum is not None and recent_sum > 0)
                ),
            },
            # 원문으로 가는 길 — 최근 5건. 전건이 필요하면 scope=insiders 의 raw 를 본다.
            "recent_filings": [
                {
                    "date": format_iso_date(_normalize_date_key(p["row"].get("rcept_dt", ""))),
                    "rcept_no": p["row"].get("rcept_no", ""),
                    "change_shares": p["change"],
                    "shares": p["shares"],
                    "initial_report": p["initial"],
                }
                for p in reversed(parsed[-5:])
            ],
        })

    def _rank(item: dict[str, Any]) -> tuple:
        return (
            bool(item.get("major_shareholder_type")),      # 지배주주·10%이상주주 먼저
            item["recent_window"]["accumulating"],          # 최근 매집자
            item.get("initial_report_in_window", False),    # 창 안에서 처음 등장한 보고자
            abs(item.get("net_change_shares") or item.get("reported_change_sum") or 0),
            item.get("ownership_pct_last") or 0.0,
        )

    out.sort(key=_rank, reverse=True)
    return out


async def _insider_holdings(
    corp_code: str,
    bgn_de: str,
    end_de: str,
    *,
    rows_limit: int = _INSIDER_ROWS_LIMIT_DEFAULT,
) -> tuple[dict[str, Any], list[str]]:
    """임원·주요주주 소유상황보고(D002)를 elestock 1콜로 받아 보고자 단위로 접는다.

    반환 data 의 `status_reason` 은 **못 가져온 이유를 구분**한다:
      - `ok`          정상 (건수 0 이어도 조회는 성공)
      - `no_data`     DART 013 — 이 회사엔 D002 보고 자체가 없다 (도구 문제 아님)
      - `fetch_failed` 호출은 했는데 오류가 났다 (키·서버 — 「없음」과 다르다)
    """
    client = get_dart_client()
    limit = max(1, min(int(rows_limit or _INSIDER_ROWS_LIMIT_DEFAULT), _INSIDER_ROWS_LIMIT_MAX))
    warnings: list[str] = []
    base: dict[str, Any] = {
        "source": {
            "endpoint": "elestock",
            "pblntf_detail_ty": "D002",
            "report_name": _INSIDER_REPORT_NAME,
            "note": (
                "5% 대량보유(D001)는 5% 이상만 잡는다. 그 문턱 아래에서 움직이는 "
                "임원·특수관계인 매집은 이 보고에만 남는다."
            ),
        },
        "status_reason": "ok",
        "coverage": {},
        "reporters": [],
        "reporter_count": 0,
    }

    try:
        response = await client.get_executive_holdings(corp_code)
    except DartClientError as exc:
        if exc.status == "013":
            base["status_reason"] = "no_data"
            base["coverage"] = {
                "rows_all_history": 0, "rows_in_window": 0, "rows_analyzed": 0,
                "rows_dropped": 0, "truncated": False, "rows_limit": limit,
            }
            warnings.append(
                "[데이터 없음] 임원·주요주주 특정증권등 소유상황보고(D002)가 DART에 없다 — "
                "조회는 정상이고 해당 보고가 없는 것이다."
            )
            return base, warnings
        base["status_reason"] = "fetch_failed"
        base["reporters"] = None
        base["coverage"] = {"rows_limit": limit}
        warnings.append(
            f"[호출 실패] 임원·주요주주 소유상황보고(elestock/D002) 조회 실패 — DART 응답코드 {exc.status}. "
            "「보고가 없다」는 뜻이 아니다 — 이 구간의 임원 매집 여부는 확인하지 못했다."
        )
        return base, warnings

    all_rows = list(response.get("list") or [])
    in_window = [
        row for row in all_rows
        if _in_window(row.get("rcept_dt", ""), bgn_de, end_de)
    ]
    in_window.sort(key=lambda r: (_normalize_date_key(r.get("rcept_dt", "")), r.get("rcept_no", "")), reverse=True)

    truncated = len(in_window) > limit
    analyzed = in_window[:limit] if truncated else in_window
    oldest = _normalize_date_key(analyzed[-1].get("rcept_dt", "")) if analyzed else ""

    base["coverage"] = {
        "rows_all_history": len(all_rows),
        "rows_in_window": len(in_window),
        "rows_analyzed": len(analyzed),
        "rows_dropped": len(in_window) - len(analyzed),
        "truncated": truncated,
        "rows_limit": limit,
        "analyzed_from_date": format_iso_date(oldest) if oldest else "",
        "window": {"start_date": bgn_de, "end_date": end_de},
    }
    base["reporters"] = _aggregate_insider_rows(
        analyzed,
        recent_since=_insider_shift_days(end_de, _INSIDER_RECENT_DAYS),
    )
    base["reporter_count"] = len(base["reporters"])

    if truncated:
        # 조용히 자르지 않는다 — 무엇을 몇 건 못 셌는지, 어떻게 넓히는지까지 쓴다.
        warnings.append(
            f"[상한 도달] 임원·주요주주 소유상황보고가 조사구간에 {len(in_window):,}건 있어 "
            f"최근 {len(analyzed):,}건({base['coverage']['analyzed_from_date']} 이후)만 집계했다 — "
            f"{base['coverage']['rows_dropped']:,}건은 순증감 합계에 **빠져 있다**. "
            f"전 구간이 필요하면 `insider_rows_limit`을 올리거나(최대 {_INSIDER_ROWS_LIMIT_MAX:,}) "
            "`start_date`/`end_date`로 구간을 좁혀 다시 불러라."
        )
    return base, warnings


def _annotate_insider_reporters(
    insiders: dict[str, Any],
    *,
    registry_names: set[str],
    block_names: set[str],
) -> None:
    """보고자가 명부상 특수관계인인지 / 5% 블록 보고자인지 교차 표시 (판정은 하지 않는다)."""
    for item in insiders.get("reporters") or []:
        key = _normalize_entity_name(item.get("reporter", ""))
        item["in_registry"] = bool(key and key in registry_names)
        item["in_5pct_block"] = bool(key and key in block_names)


_LIT_CORRECTION_MARKERS = ("[기재정정]", "[첨부정정]", "[정정]", "[연장결정]")


# 경영권 분쟁 가처분/소송 사건명 키워드 (260607 확장)
# 발견: 판결 공시("소송등의판결ㆍ결정")는 "경영권분쟁" 단어 없이 괄호에 구체적 사건명만
# 적어 미상으로 빠짐. 사건명 대부분이 전형적 경영권 분쟁 가처분/소송이라 직접 분류 가능.
# 경영권 분쟁 사건명 분류 (260607, 공백 제거 후 매칭 — "경영권 분쟁" 띄어쓰기도 잡음)
#
# A. 단독 확정: 행위 자체가 경영권 분쟁 고유 (명사 불필요)
#    - "발행금지/발행무효/발행유지" → 신주/유상증자/전환사채/교환사채 변형 한 번에
#    - "직무집행정지/위법행위유지/검사인선임/의안상정" → 행위가 곧 경영권
_MGMT_STANDALONE = (
    "경영권분쟁", "경영권변경",
    "직무집행정지", "직무대행",
    "개최금지",  # 총회개최금지 / 주주총회개최금지 / 임시주총개최금지
    "의안상정", "소집허가", "소집청구",  # 주주총회 소집 장악
    "지위부존재", "검사인선임",
    "위법행위유지", "유지청구", "대표소송",  # 상법 §402/§403 소수주주권
    "발행금지", "발행무효", "발행유지", "발행부존재",  # 신주/CB/EB/BW 방어 증권 차단
    "상장금지",
)

# B. 명사 + 행위 조합 (명사 단독은 모호 — substring false positive 방지)
#    예: "회계장부 작성 위반"(상거래) vs "회계장부 열람 가처분"(경영권)
_MGMT_COMBOS = (
    ("회계장부", ("열람", "등사")),     # 정보 청구 (행동주의)
    ("주주명부", ("열람", "등사")),
    ("의결권", ("금지", "허용", "정지")),  # 의결권 가처분
    ("주주총회결의", ("취소", "무효", "효력정지", "부존재")),
    ("이사", ("선임결의", "해임", "지위")),  # 이사 지위 분쟁 (보수 등 제외)
)


def _litigation_dispute_kind(name: str) -> str:
    """소송 공시명/사건명을 경영권 분쟁 / 단순 상거래로 구분 (260607).

    142종목 역추적 재검토에서 발견: 소송 키워드 hit의 절반이 "일정금액이상의청구"
    같은 일상 상거래 소송이라 분쟁 신호로 오인됨 (아시아나항공 11건 등).

    분류 구조:
    - A. 단독 행위 키워드 (발행금지/직무집행정지 등) — 행위 자체가 경영권 분쟁 고유
    - B. 명사+행위 조합 (회계장부+열람 등) — 명사 단독은 모호하므로 행위와 조합
      → "회계장부 작성 위반 손배"(상거래)를 "회계장부 열람"(경영권)과 구분

    - management: A 단독 OR B 조합 매칭
    - commercial: "일정금액이상의청구" — 일상 손배/상거래 소송 (분쟁 아님)
    - unspecified: 그 외 (괄호 사건명 없는 일반 양식 등 — 판단 보류 → LLM 위임)
    """
    name_norm = name.replace(" ", "")
    if "일정금액이상" in name_norm:
        return "commercial"
    if any(kw in name_norm for kw in _MGMT_STANDALONE):
        return "management"
    for noun, acts in _MGMT_COMBOS:
        if noun in name_norm and any(a in name_norm for a in acts):
            return "management"
    return "unspecified"


def _classify_litigation(raw_rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """소송 공시 정정 noise 제거 + 유형 분류 (260605 dedup, 260607 dispute_kind).

    DART는 사건 ID를 주지 않아 완벽한 사건 단위 dedup은 불가하다.
    현실적 dedup: 정정공시([기재정정]/[첨부정정])를 제외하고, 남은 원본 공시를
    - 단계: 제기(filed) / 판결(ruling) / 기타(other)
    - 성격: 경영권(management) / 상거래(commercial) / 미상(unspecified)
    두 축으로 분류한다.

    제기·판결은 같은 소송의 다른 단계일 수 있으나 별개 이벤트(원본 공시)이므로
    유형 태그만 달고 건수는 보존한다 — 판단은 애널리스트/LLM에 위임.
    """
    primary: list[dict[str, Any]] = []
    correction_count = 0
    for r in raw_rows:
        name = r.get("report_name", "")
        if any(m in name for m in _LIT_CORRECTION_MARKERS):
            correction_count += 1
            continue
        if "판결" in name or "결정" in name:
            lit_type = "ruling"
        elif "제기" in name or "신청" in name:
            lit_type = "filed"
        else:
            lit_type = "other"
        primary.append({
            **r,
            "litigation_type": lit_type,
            "dispute_kind": _litigation_dispute_kind(name),
        })

    # 회사 단위 추정 (260607): 판결 공시는 성격이 공시명에 안 적힘("소송등의판결ㆍ결정").
    # 같은 회사에 경영권분쟁소송 제기가 있으면 미상 판결을 경영권으로 추정 (단정 X, _inferred 태그).
    has_mgmt_filing = any(r["dispute_kind"] == "management" for r in primary)
    has_commercial_filing = any(r["dispute_kind"] == "commercial" for r in primary)
    for r in primary:
        if r["dispute_kind"] == "unspecified" and r["litigation_type"] == "ruling":
            if has_mgmt_filing and not has_commercial_filing:
                r["dispute_kind_inferred"] = "management"
            elif has_commercial_filing and not has_mgmt_filing:
                r["dispute_kind_inferred"] = "commercial"
            else:
                r["dispute_kind_inferred"] = "mixed"

    # 공시명 빈도 (LLM 판단 재료 — 간단 집계)
    from collections import Counter as _Counter
    name_freq = _Counter(_dedup_name(r.get("report_name", "")) for r in primary)

    meta = {
        "raw_count": len(raw_rows),
        "correction_excluded": correction_count,
        "primary_count": len(primary),
        "filed_count": sum(1 for r in primary if r["litigation_type"] == "filed"),
        "ruling_count": sum(1 for r in primary if r["litigation_type"] == "ruling"),
        "other_count": sum(1 for r in primary if r["litigation_type"] == "other"),
        # 경영권/상거래 구분 (분쟁 신호 정확도)
        "management_count": sum(1 for r in primary if r["dispute_kind"] == "management"),
        "commercial_count": sum(1 for r in primary if r["dispute_kind"] == "commercial"),
        "unspecified_count": sum(1 for r in primary if r["dispute_kind"] == "unspecified"),
        # 미상 판결 회사단위 추정 (단정 X)
        "unspecified_inferred_mgmt": sum(
            1 for r in primary if r.get("dispute_kind_inferred") == "management"),
        "unspecified_inferred_commercial": sum(
            1 for r in primary if r.get("dispute_kind_inferred") == "commercial"),
        # LLM 판단용 — 공시명 빈도 (정규화 텍스트)
        "report_name_freq": [
            {"name": n, "count": c} for n, c in name_freq.most_common()
        ],
    }
    return primary, meta


def _dedup_name(name: str) -> str:
    """정정 마커 제거 후 공백 정리 (LLM 판단용 정규화)."""
    for m in _LIT_CORRECTION_MARKERS:
        name = name.replace(m, "")
    return re.sub(r"\s+", " ", name).strip()


# ── 소송 공시 본문 파서 ──────────────────────────────────────────────────────
# DART 「소송 등의 제기ㆍ신청」·「소송 등의 판결ㆍ결정」은 번호 붙은 고정 서식이다.
# 목적은 필드를 다 캐내는 것이 **아니다** — 사건명 같은 쉬운 값은 뽑되, 청구취지·주문처럼
# 자유서술인 대목은 **원문 그대로 실어** 읽는 쪽(LLM·애널리스트)이 판단하게 한다(260828).
#
# 서식 두 종의 항목 이름이 다르다:
#   제기ㆍ신청: 1 사건의 명칭 / 사건번호 · 2 원고(신청인) · 3 청구내용 · 4 관할법원
#               · 5 향후대책 · 6 제기ㆍ신청일자 · 7 확인일자 · 8 기타
#   판결ㆍ결정: 1 사건의 명칭 / 사건번호 · 2 원고ㆍ신청인 · 3 판결ㆍ결정내용
#               · 4 판결ㆍ결정사유 · 5 관할법원 · 6 판결ㆍ결정일자 · 7 확인일자 · 8 기타
# 앵커 위치를 찾아 **사이를 통째로 잘라** 값으로 쓴다. 잘린 조각이 곧 원문이다.
_LIT_FIELD_ANCHORS: tuple[tuple[str, str], ...] = (
    ("case_name", r"사건의?\s*명칭"),
    ("case_number", r"사건\s*번호"),
    ("parties", r"원고\s*(?:[ㆍ·・]\s*신청인|\(\s*신청\s*인\s*\))"),
    ("claim", r"청구\s*내용"),
    ("ruling", r"판결\s*[ㆍ·・]?\s*결정\s*내용"),
    ("ruling_reason", r"판결\s*[ㆍ·・]?\s*결정\s*사유"),
    ("court", r"관할\s*법원"),
    ("future_plan", r"향후\s*대책"),
    ("filed_date", r"제기\s*[ㆍ·・]?\s*신청\s*일자"),
    ("decided_date", r"판결\s*[ㆍ·・]?\s*결정\s*일자"),
    ("confirmed_date", r"확인\s*일자"),
    ("other_material", r"기타\s*투자\s*판단(?:과\s*관련한?\s*중요\s*사항)?"),
    ("related_filings", r"관련\s*공시"),
)
# 번호(`3.`)는 있을 때만. 서식이 번호를 떼도 이름만으로 걸린다.
_LIT_ANCHOR_RE = re.compile(
    "|".join(f"(?P<{key}>(?:\\d+\\s*[.)]\\s*)?(?:{pat})\\s*[:：]?)" for key, pat in _LIT_FIELD_ANCHORS)
)

# 회사가 「아직 안 적었다」는 뜻으로 넣는 값들. 파싱 실패와 뜻이 다르다.
_LIT_PLACEHOLDERS = frozenset({"", "-", "–", "—", ".", "-.", "해당사항없음", "해당없음", "없음"})


def _lit_html_to_text(html: str) -> str:
    """공시 HTML → 한 줄 텍스트. 스타일 블록(.xforms{...})을 먼저 걷어낸다."""
    body = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html or "")
    body = re.sub(r"<[^>]+>", " ", body)
    body = re.sub(r"&nbsp;?", " ", body)
    body = re.sub(r"\{[^{}]*\}", " ", body)  # 남은 CSS 규칙 본문
    return re.sub(r"\s+", " ", body).strip()


def _is_placeholder(value: str) -> bool:
    return value.replace(" ", "").strip(" .·ㆍ") in _LIT_PLACEHOLDERS


def parse_litigation_form(html: str) -> dict[str, Any]:
    """소송 공시 본문에서 서식 항목을 잘라낸다 + 원문 발췌를 함께 돌려준다.

    반환 `status`:
      - `parsed`            사건의 명칭을 읽었다
      - `case_name_absent`  서식은 읽혔는데 사건명 칸이 비어 있다(회사가 안 적음). **파싱 실패가 아니다**
      - `form_unrecognized` 아는 서식이 아니다 — 원문을 직접 봐야 한다

    값은 손대지 않은 원문 조각이다. 요약·정규화하지 않는다 — 인용해도 되는 문자열이다.
    """
    text = _lit_html_to_text(html)
    matches = list(_LIT_ANCHOR_RE.finditer(text))
    if not matches:
        return {
            "status": "form_unrecognized",
            "fields": {},
            "excerpt": text[:1500],
        }

    # 1단계 — 진짜 항목 칸만 고른다. 본문 안내문이 "상기 '1. 사건의 명칭'…" 처럼 항목
    # 이름을 다시 부르는 일이 잦아, 그것을 칸으로 세면 뒤 칸이 거기서 잘린다(260828).
    accepted: list[tuple[str, Any]] = []
    seen: set[str] = set()
    for idx, m in enumerate(matches):
        key = m.lastgroup
        if key in seen:
            continue  # 첫 등장만
        # 사건번호는 서식상 「사건의 명칭」 바로 뒤 칸이다. 회사가 그 칸을 비우면
        # 「기타 투자판단」 안내문의 "…사건번호는 서울중앙지방법원 2026가합3015 입니다"가
        # 첫 등장이 돼 값으로 새어 들어왔다 — 인접했을 때만 인정한다.
        # 앞 칸이 **채택된** 사건의 명칭일 때만. 안내문이 항목 이름을 되부르는 경우
        # (`상기 '1. 사건의 명칭'의 사건번호는 …`) 그 사건명은 중복이라 버려지므로 여기서 걸린다.
        if key == "case_number" and not (
            accepted and accepted[-1][0] == "case_name" and accepted[-1][1] is matches[idx - 1]
        ):
            continue
        seen.add(key)
        accepted.append((key, m))

    # 2단계 — 채택된 칸 사이를 통째로 잘라 값으로 쓴다. 잘린 조각이 곧 원문이다.
    fields: dict[str, str] = {}
    for i, (key, m) in enumerate(accepted):
        end = accepted[i + 1][1].start() if i + 1 < len(accepted) else len(text)
        fields[key] = text[m.end():end].strip(" :：.·ㆍ")

    # 서식이 「-」로 비워 둔 칸은 값이 아니라 「회사가 아직 안 적음」이다.
    absent = sorted(k for k, v in fields.items() if _is_placeholder(v))
    for key in absent:
        fields[key] = ""

    excerpt = text[matches[0].start():matches[0].start() + 1500]
    status = "parsed" if fields.get("case_name") else "case_name_absent"
    return {"status": status, "fields": fields, "excerpt": excerpt, "absent_fields": absent}


def _extract_case_name(html: str) -> str:
    """소송 공시 본문 '1. 사건의 명칭' 값 (하위 호환 · 얇은 래퍼)."""
    return parse_litigation_form(html)["fields"].get("case_name", "")


# 서식 항목 → 사람이 읽는 이름. 렌더러가 그대로 쓴다(사전을 두 벌 두면 한쪽만 고쳐진다).
LIT_FIELD_LABELS_KO: dict[str, str] = {
    "case_name": "사건의 명칭",
    "case_number": "사건번호",
    "parties": "원고ㆍ신청인",
    "claim": "청구내용",
    "ruling": "판결ㆍ결정내용",
    "ruling_reason": "판결ㆍ결정사유",
    "court": "관할법원",
    "future_plan": "향후대책",
    "filed_date": "제기ㆍ신청일자",
    "decided_date": "판결ㆍ결정일자",
    "confirmed_date": "확인일자",
    "related_filings": "관련공시",
    "other_material": "기타 투자판단과 관련한 중요사항",
}

# 산출물에 실어 보내는 항목 (기타 투자판단 안내문 등 정형 문구는 뺀다).
_LIT_CARRY_FIELDS = (
    "case_name", "case_number", "parties", "claim", "ruling",
    "ruling_reason", "court", "filed_date", "decided_date", "other_material",
    "related_filings",
)


async def _enrich_litigation_documents(
    primary: list[dict[str, Any]],
    *,
    max_lookups: int = 30,
) -> dict[str, int]:
    """소송 row 전건의 본문을 열어 서식 항목 + 원문 발췌를 붙인다 (260828 전면 개편).

    이전(260607)에는 `dispute_kind=unspecified` 인 row 만 열었고, 사건명을 읽고도
    **경영권/상거래로 재분류될 때만 `case_name` 을 남겼다** — 「손해배상」·「증거보전」·
    「가처분 이의에 대한 즉시항고」처럼 분류가 안 되는 사건명은 뽑아 놓고 버려서
    화면에 「미상」으로만 나갔다. 공시명만 뜨던 row 는 아예 본문을 안 열었다.
    지금은 **전건을 열고, 읽은 것은 분류 여부와 무관하게 전부 남긴다.**

    row 에 붙는 것:
      - `case_fields`   서식 항목 원문 조각 (dict, 값은 손대지 않은 문자열)
      - `case_excerpt`  본문 발췌 원문 (인용 가능)
      - `case_name` / `case_number` / `parties` 편의 평면 필드
      - `document_status`  parsed / case_name_absent / form_unrecognized
                           / fetch_failed / not_looked_up
      - `absent_fields`    회사가 「-」로 비워 둔 항목 (파싱 실패와 구분)

    max_lookups 초과분은 `not_looked_up` 으로 남긴다 — 조용히 버리지 않는다.
    """
    client = get_dart_client()
    stats: dict[str, int] = {
        "wall_ms": 0, "parse_ms": 0, "lookups": 0,
        "parsed": 0, "case_name_absent": 0, "form_unrecognized": 0,
        "fetch_failed": 0, "not_looked_up": 0, "reclassified": 0,
    }
    targets = [r for r in primary if r.get("rcept_no")][:max_lookups]
    for r in primary:
        r.setdefault("document_status", "not_looked_up")
    stats["not_looked_up"] = len(primary) - len(targets)
    if not targets:
        return stats

    async def _fetch(row):
        try:
            return row, await client.get_document_cached(row["rcept_no"])
        except Exception:
            return row, None

    wall0 = time.perf_counter()
    docs = await asyncio.gather(*[_fetch(r) for r in targets])  # 병렬 조회 (cache hit이면 호출 0)
    stats["wall_ms"] = int((time.perf_counter() - wall0) * 1000)

    parse0 = time.perf_counter()
    for r, doc in docs:
        if doc is None:
            r["document_status"] = "fetch_failed"
            stats["fetch_failed"] += 1
            continue
        stats["lookups"] += 1
        parsed = parse_litigation_form(doc.get("html", "") or "")
        r["document_status"] = parsed["status"]
        stats[parsed["status"]] = stats.get(parsed["status"], 0) + 1

        fields = parsed["fields"]
        carried = {k: fields[k] for k in _LIT_CARRY_FIELDS if fields.get(k)}
        if carried:
            r["case_fields"] = carried
        if parsed.get("absent_fields"):
            r["absent_fields"] = [
                LIT_FIELD_LABELS_KO.get(k, k) for k in parsed["absent_fields"]
                if k in _LIT_CARRY_FIELDS
            ]
        if parsed.get("excerpt"):
            r["case_excerpt"] = parsed["excerpt"]

        # 편의 평면 필드 — 표에 바로 쓴다. **분류 성공 여부와 무관하게 남긴다.**
        case_name = fields.get("case_name", "")
        if case_name:
            r["case_name"] = case_name
        if fields.get("case_number"):
            r["case_number"] = fields["case_number"]
        if fields.get("parties"):
            r["parties"] = fields["parties"]

        # 사건명으로 성격 재분류 (되면 좋고, 안 되면 unspecified 유지 → 읽는 쪽에 위임)
        if case_name and r.get("dispute_kind") == "unspecified":
            kind = _litigation_dispute_kind(case_name)
            if kind != "unspecified":
                r["dispute_kind"] = kind
                r["dispute_kind_source"] = "document"
                stats["reclassified"] += 1
    stats["parse_ms"] = int((time.perf_counter() - parse0) * 1000)
    return stats


async def _litigation_items(
    corp_code: str,
    bgn_de: str,
    end_de: str,
    parse_documents: bool = False,
    max_document_lookups: int = 30,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str], str | None]:
    items, notices, error = await search_filings_by_report_name(
        corp_code=corp_code,
        bgn_de=bgn_de,
        end_de=end_de,
        pblntf_tys="",
        pblntf_detail_ty=["I001", "B001"],  # 경영권분쟁소송(I001)/소송등의제기(B001), 차집합0 검증
        keywords=_LITIGATION_KEYWORDS,
        strip_spaces=True,
    )
    if error:
        return [], {}, notices, f"소송/분쟁 공시 조회 실패: {error}"
    raw_rows: list[dict[str, Any]] = []
    for item in items:
        raw_rows.append({
            "rcept_no": item.get("rcept_no", ""),
            "disclosure_date": item.get("rcept_dt", ""),
            "report_name": item.get("report_nm", ""),
            "filer_name": item.get("flr_nm", ""),
        })
    raw_rows.sort(key=lambda row: (row["disclosure_date"], row["rcept_no"]), reverse=True)
    primary_rows, dedup_meta = _classify_litigation(raw_rows)

    # 260828: 전건 본문 파싱 — 사건명·사건번호·원고·청구내용 원문을 row 에 붙인다.
    if parse_documents:
        doc_stats = await _enrich_litigation_documents(
            primary_rows, max_lookups=max_document_lookups)
        # 재분류 후 카운트 갱신
        dedup_meta["management_count"] = sum(1 for r in primary_rows if r["dispute_kind"] == "management")
        dedup_meta["commercial_count"] = sum(1 for r in primary_rows if r["dispute_kind"] == "commercial")
        dedup_meta["unspecified_count"] = sum(1 for r in primary_rows if r["dispute_kind"] == "unspecified")
        dedup_meta["document_resolved"] = doc_stats["reclassified"]
        dedup_meta["document_stats"] = doc_stats
        # 「못 준 것」을 세어서 그대로 노출한다 — 뭉개면 읽는 쪽이 없는 줄 안다.
        dedup_meta["case_name_from_document"] = sum(1 for r in primary_rows if r.get("case_name"))
        dedup_meta["case_name_absent_in_document"] = doc_stats["case_name_absent"]
        dedup_meta["document_fetch_failed"] = doc_stats["fetch_failed"]
        dedup_meta["document_not_looked_up"] = doc_stats["not_looked_up"]

    return primary_rows, dedup_meta, notices, None


async def _control_context(corp_code: str, company_query: str, target_year: int | None) -> tuple[dict[str, Any], list[str]]:
    client = get_dart_client()
    warnings: list[str] = []
    bsns_year = str((target_year or date.today().year) - 1)

    # 3개 정기보고서 + 5% 블록 API를 병렬 호출 (independent endpoints).
    major_task = client.get_major_shareholders(corp_code, bsns_year)
    stock_total_task = client.get_stock_total(corp_code, bsns_year)
    treasury_task = client.get_treasury_stock(corp_code, bsns_year)
    blocks_task = _latest_block_rows(corp_code)
    major_res, stock_total_res, treasury_res, blocks_res = await asyncio.gather(
        major_task, stock_total_task, treasury_task, blocks_task,
        return_exceptions=True,
    )

    if isinstance(major_res, DartClientError):
        warnings.append(f"지분 명부 API 조회 실패: {major_res.status}")
        return {
            "year": bsns_year,
            "top_holder": {},
            "related_total_pct": 0.0,
            "treasury_pct": 0.0,
            "control_map": {},
            "registry_names": [],
        }, warnings
    if isinstance(major_res, BaseException):
        raise major_res
    major = major_res

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
    if isinstance(blocks_res, BaseException):
        latest_blocks: list[dict[str, Any]] = []
        block_timeline: list[dict[str, Any]] = []
        block_warning = f"5% 블록 조회 실패: {blocks_res}"
    else:
        # timeline_rows를 더 이상 버리지 않고 시계열 신호 추출에 사용 (260605)
        latest_blocks, block_timeline, block_warning = blocks_res
    if block_warning:
        warnings.append(block_warning)
    treasury_snapshot = _treasury_snapshot(stock_total, treasury_data)
    control_map = _build_control_map(major_rows, latest_blocks, treasury_snapshot)
    # 5% 대량보유 시계열 신호 (목적 전환 / 지속 추가매입 / 보고 빈도) — 자동 판정 X, 정보 노출
    control_map["block_holder_dynamics"] = _block_holder_dynamics(block_timeline)
    return {
        "year": bsns_year,
        "top_holder": _top_holder_summary(major_rows),
        "related_total_pct": _related_total(major_rows),
        "treasury_pct": treasury_snapshot["treasury_pct"],
        "control_map": control_map,
        # 임원 보고자가 명부상 특수관계인인지 대조하는 데 쓴다 (추가 API 콜 0).
        "registry_names": [row.get("name", "") for row in major_rows if row.get("name")],
    }, warnings


_PASSIVE_PURPOSES = ("단순투자", "일반투자", "단순투자/일반투자")
# 급변 임계값 — 첫 보고 대비 ±5%p 이상이면 경영권 변동 신호 (매집 또는 exit)
_ABRUPT_CHANGE_PP = 5.0


def _block_holder_dynamics(timeline_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """5% 대량보유 보고 이력을 보고자별 시계열로 분석.

    majorstock 전체 이력(timeline_rows)을 buyer별로 묶어 분쟁 선행 신호를 추출한다.
    자동 분류(분쟁 강도 판정)는 하지 않고 "무슨 변화가 언제 떴나"만 정보로 노출한다.

    각 보고자별:
    - purpose_shift: 단순/일반투자 → 경영참여 전환 (분쟁 선행 신호)
    - accumulation: 첫 보고 대비 최신 지분율 증감 (지속 추가매입)
    - report_count / first_date / last_date: 보고 빈도

    timeline_rows row 형식 (ownership_structure._latest_block_rows):
        {reporter, report_date, rcept_no, ownership_pct, purpose, report_name}
    """
    by_reporter: dict[str, list[dict[str, Any]]] = {}
    for row in timeline_rows or []:
        reporter = (row.get("reporter") or "").strip()
        if not reporter:
            continue
        by_reporter.setdefault(reporter, []).append(row)

    out: list[dict[str, Any]] = []
    for reporter, rows in by_reporter.items():
        # 오래된 → 최신 정렬 (시계열 diff)
        chrono = sorted(rows, key=lambda r: (r.get("report_date", ""), r.get("rcept_no", "")))
        if not chrono:
            continue

        # 1. 목적 전환: passive 이력 후 경영참여 등장
        purpose_shift = None
        had_passive = False
        for r in chrono:
            p = r.get("purpose", "")
            if p in _PASSIVE_PURPOSES:
                had_passive = True
            elif p == "경영참여" and had_passive:
                purpose_shift = {
                    "from": "단순/일반투자",
                    "to": "경영참여",
                    "date": r.get("report_date", ""),
                }
                break

        # 2. 지분 추세 (첫 → 최신). 급변(±임계값)은 증감 무관 강조 —
        #    매집(증가)과 exit/매각(감소) 모두 경영권 변동 신호.
        first_pct = chrono[0].get("ownership_pct") or 0.0
        last_pct = chrono[-1].get("ownership_pct") or 0.0
        change_pp = round(last_pct - first_pct, 2)
        if change_pp > 0.01:
            direction = "increasing"
        elif change_pp < -0.01:
            direction = "decreasing"
        else:
            direction = "flat"
        accumulation = {
            "first_pct": round(first_pct, 2),
            "last_pct": round(last_pct, 2),
            "change_pp": change_pp,
            "increasing": direction == "increasing",
            "direction": direction,
            # 급변 = |변동| ≥ 5%p (증가=매집 / 감소=exit·매각 모두 신호)
            "abrupt_change": abs(change_pp) >= _ABRUPT_CHANGE_PP,
        }

        out.append({
            "reporter": reporter,
            "report_count": len(chrono),
            "first_date": chrono[0].get("report_date", ""),
            "last_date": chrono[-1].get("report_date", ""),
            "current_purpose": chrono[-1].get("purpose", ""),
            "purpose_shift": purpose_shift,
            "accumulation": accumulation,
        })

    # 정렬: 목적전환 > 급변 > 최신 지분 순 (강한 신호 우선)
    out.sort(
        key=lambda x: (
            x["purpose_shift"] is not None,
            x["accumulation"]["abrupt_change"],
            x["accumulation"]["last_pct"],
        ),
        reverse=True,
    )
    return out


def _signal_actor_side(row: dict[str, Any]) -> str:
    # 우선순위: 보고자 본인이 명부(registry_overlap)가 가장 명확한 내부 신호.
    # 그 다음 coheld_with_registry — 보고자 이름은 명부에 없어 외부처럼 보이나 특별관계자에
    # 명부상 최대주주가 포함된 공동보유. external_active_block(외부 능동)로 단정하면 안 된다.
    if row.get("registry_overlap"):
        return "registry_overlap"
    if row.get("coheld_with_registry"):
        return "coheld_with_registry"
    if row.get("active_purpose"):
        return "external_active_block"
    return "external_or_passive"


def _fight_actor_group(
    row: dict[str, Any],
    active_external_names: set[str],
    overlap_names: set[str],
    coheld_names: set[str],
) -> str:
    if row.get("side") == "company":
        return "company"
    if row.get("side") == "retail_activism":
        return "retail_activism"
    filer_key = _normalize_entity_name(row.get("filer_name", ""))
    # coheld는 active_external_names에도 들어있으므로 external보다 먼저 확인.
    if filer_key in coheld_names:
        return "coheld_with_registry"
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
            "approval_base": item.get("approval_base", "unknown"),
            "approval_base_label": item.get("approval_base_label", ""),
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


def _dominant_approval_base(items: list[dict[str, Any]]) -> str:
    """비교 대상 안건들이 쓴 찬성률 분모를 하나로 모은다. 갈리면 unknown."""
    bases = {
        item.get("approval_base", "unknown")
        for item in items
        if item.get("estimated_attendance") is not None
    }
    bases.discard("unknown")
    if len(bases) == 1:
        return bases.pop()
    return "unknown"


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
    # coheld(특관에 명부상 최대주주 포함) 블록은 외부 합계에서 제외 — 헤드라인 ownership_pct가
    # 명부 최대주주를 합산한 값이라 related_total_pct와 이중계상되고, 외부 압력으로 오독된다.
    active_external_total_pct = round(sum(
        _to_float(row.get("ownership_pct"))
        for row in control_map.get("active_non_overlap_blocks", [])
        if not row.get("coheld_with_registry")
    ), 2)
    active_overlap_total_pct = round(sum(_to_float(row.get("ownership_pct")) for row in control_map.get("active_overlap_blocks", [])), 2)

    # 참석률과 지분율은 분모가 다르다. 참석률은 결과공시 표의 「의결권 있는 발행주식 총수 기준」
    # 열에서 역산한 값이고, 특수관계인·자사주 지분율은 발행주식총수 기준이다. 이걸 그대로 빼면
    # 자사주가 큰 회사에서 참석률이 모수를 넘는다(태광산업 183.3% 사고). 모두 의결권 기준으로 옮긴다.
    attendance_base = _dominant_approval_base(comparable_items)
    attendance_base_source = "disclosure_header"
    attendance_on_voting_base = None
    if representative_attendance is not None:
        if attendance_base == "unknown":
            # 머리글이 없으면 성립 가능성으로 가른다 — 발행총수 기준이면 참석률이 모수를 못 넘는다.
            attendance_base = "voting" if representative_attendance > voting_share_base_pct else "issued"
            attendance_base_source = "inferred_from_feasibility"
        if attendance_base == "issued":
            attendance_on_voting_base = (
                round(representative_attendance / voting_share_base_pct * 100, 1)
                if voting_share_base_pct > 0 else None
            )
        else:
            attendance_on_voting_base = representative_attendance

    related_pct_voting_base = (
        round(related_total_pct / voting_share_base_pct * 100, 2) if voting_share_base_pct > 0 else None
    )

    contestable_turnout_pct = None
    ex_related_turnout_pct = None
    turnout_warning = None
    if attendance_on_voting_base is not None and related_pct_voting_base is not None:
        contestable_exact = max(attendance_on_voting_base - related_pct_voting_base, 0.0)
        contestable_turnout_pct = round(contestable_exact, 1)
        free_float_base_pct = round(max(100.0 - related_pct_voting_base, 0.0), 2)
        if free_float_base_pct > 0:
            # 반올림한 참석분으로 나누면 비율이 0.2%p까지 밀린다 — 나눗셈은 원값으로 한다.
            raw_pct = round(contestable_exact / (100.0 - related_pct_voting_base) * 100, 1)
            if raw_pct > 100:
                # 100%를 넘으면 계산이 아니라 전제가 깨진 것이다 — 특수관계인 지분 기준일과
                # 의결권행사기준일이 다르거나, 명부 지분이 실제보다 크게 잡혔다는 뜻.
                turnout_warning = (
                    f"특수관계인 제외 참석률 역산값이 {raw_pct}%로 100%를 넘어 값을 내지 않았다. "
                    f"특수관계인 지분({related_total_pct}%, {summary.get('ownership_basis_year') or '최근'}년 정기보고서 기준)과 "
                    "의결권행사기준일 명부가 달라졌을 가능성이 크다."
                )
            else:
                ex_related_turnout_pct = raw_pct

    status = AnalysisStatus.EXACT
    interpretation_notes: list[str] = [
        "vote_math는 승패 예측이 아니라 표 구조 신호를 보는 참고 지표다.",
        "대표 추정참석률은 보통결의 안건의 (1)기준 찬성률 / 행사기준 찬성률 역산값 최빈값을 사용한다.",
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

    if turnout_warning:
        warnings.append(turnout_warning)
        if status == AnalysisStatus.EXACT:
            status = AnalysisStatus.PARTIAL
    if representative_attendance is not None:
        if attendance_base == "voting":
            interpretation_notes.append(
                "참석률의 분모는 의결권 있는 주식이다. 특수관계인·자사주 지분율(발행주식총수 기준)과 "
                "직접 빼지 않고, 의결권 기준으로 환산해 계산했다."
            )
        else:
            interpretation_notes.append("참석률의 분모는 자사주를 포함한 발행주식총수다. 의결권 기준으로 환산해 계산했다.")
        if attendance_base_source == "inferred_from_feasibility":
            warnings.append(
                "결과공시 표에 찬성률 분모 머리글이 없어 참석률 기준을 성립 가능성으로 추정했다. "
                "분모 해석이 바뀌면 특수관계인 제외 참석률도 바뀐다."
            )
            if status == AnalysisStatus.EXACT:
                status = AnalysisStatus.PARTIAL

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
            "methodology": "보통결의 안건의 (1)기준 찬성률 / 출석주식수 기준 찬성률 역산값 최빈값",
            "base": attendance_base,
            "base_label": next((item.get("approval_base_label") for item in comparable_items if item.get("approval_base_label")), ""),
            "base_source": attendance_base_source,
            "items": comparable_items[:10],
            "excluded_items": excluded_items[:10],
        },
        "capital_structure": {
            "related_total_pct": related_total_pct,
            "treasury_pct": treasury_pct,
            "voting_share_base_pct": voting_share_base_pct,
            # 아래 세 값의 분모는 「의결권 있는 주식 = 100%」다. 발행주식총수 기준인 위 두 값과 섞지 않는다.
            "related_total_pct_voting_base": related_pct_voting_base,
            "attendance_pct_voting_base": attendance_on_voting_base,
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
    insider_rows_limit: int = _INSIDER_ROWS_LIMIT_DEFAULT,
) -> dict[str, Any]:
    if scope not in _SUPPORTED_SCOPES:
        return _unsupported_scope_payload(company_query, scope)

    client = get_dart_client()
    _calls_start = client.api_call_snapshot()
    resolution = await resolve_company_query(company_query)
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        return ToolEnvelope(
            tool="proxy_contest",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[company_not_found_warning(company_query)],
            data={
                "query": company_query,
                "scope": scope,
                "usage": build_usage(client.api_call_snapshot() - _calls_start),
            },
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
                "usage": build_usage(client.api_call_snapshot() - _calls_start),
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

    # 3개 fetch를 병렬화 (각각 endpoint가 다르고 독립적).
    # (260606) _block_signals 제거 — control_context의 _latest_block_rows와 같은
    # majorstock API를 중복 호출하면서 결과(signal_rows)는 미사용이었다.
    # 5% 블록 데이터는 control_map(overlap/non_overlap_blocks)에서 전부 만들어진다.
    # litigation scope에서만 본문 '사건의 명칭' 파싱 on (미상 정밀 재분류 — 260607).
    # summary 등은 가볍게 (공시명 키워드 + 회사단위 추정만). 병렬 조회라 cache hit이면 부담 0.
    parse_lit_docs = scope == "litigation"
    proxy_task = _proxy_items(selected["corp_code"], selected.get("corp_name", ""), bgn_de, end_de)
    litigation_task = _litigation_items(
        selected["corp_code"], bgn_de, end_de,
        parse_documents=parse_lit_docs,
    )
    control_task = _control_context(selected["corp_code"], company_query, window_year)
    # 임원·주요주주 소유상황(D002)은 **회사 단위 조회에서만** 켠다. 전종목 스캔 경로가 쓰는
    # `_proxy_items`(list.json)는 손대지 않았다 — 페이지컷 회귀 위험이 그쪽에 있다.
    # elestock 은 corp_code 하나짜리 정형 API 라 1콜이면 끝난다.
    wants_insiders = scope in {"summary", "signals", "insiders"}
    tasks: list[Any] = [proxy_task, litigation_task, control_task]
    if wants_insiders:
        tasks.append(_insider_holdings(
            selected["corp_code"], bgn_de, end_de, rows_limit=insider_rows_limit,
        ))
    gathered = await asyncio.gather(*tasks)
    (proxy_rows, proxy_notices, proxy_warning) = gathered[0]
    (litigation_rows, litigation_dedup, litigation_notices, lit_warning) = gathered[1]
    (control_context, control_warnings) = gathered[2]
    insiders, insider_warnings = gathered[3] if wants_insiders else (None, [])
    warnings.extend(insider_warnings)

    warnings.extend(proxy_notices)
    warnings.extend(litigation_notices)

    for warning in (proxy_warning, lit_warning, *control_warnings):
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
    # 공동보유(특관에 명부상 최대주주 포함) 보고자 — 외부 능동 블록처럼 보여도 최대주주와 한 편.
    coheld_names = {
        _normalize_entity_name(row.get("reporter", ""))
        for row in (control_map.get("non_overlap_blocks", []) + control_map.get("overlap_blocks", []))
        if row.get("coheld_with_registry") and _normalize_entity_name(row.get("reporter", ""))
    }

    # 임원 보고자 ↔ 명부/5%블록 대조 (판정은 하지 않는다 — 사실 플래그만).
    if insiders and insiders.get("reporters"):
        _block_reporter_names = {
            _normalize_entity_name(row.get("reporter", ""))
            for row in (control_map.get("overlap_blocks", []) + control_map.get("non_overlap_blocks", []))
            if _normalize_entity_name(row.get("reporter", ""))
        }
        _annotate_insider_reporters(
            insiders,
            registry_names={
                _normalize_entity_name(name)
                for name in (control_context.get("registry_names") or [])
                if _normalize_entity_name(name)
            },
            block_names=_block_reporter_names,
        )

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
            "actor_group": _fight_actor_group(row, active_external_names, overlap_names, coheld_names),
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
    # has_contest_signal용 소송은 **경영권(management) 확정분만** 센다. unspecified(문서까지
    # 열어봐도 경영권 키워드가 없어 판단보류로 남은 일반 소송)를 분쟁 신호로 세면, 일상 손배·상거래
    # 소송 하나로 has_contest_signal이 켜지는 과탐이 된다(260713). commercial과 마찬가지로 unspecified도
    # 제외하고, dispute_kind=management(직접) 또는 ruling에서 inferred=management(같은 회사 내 경영권
    # 분쟁 맥락으로 추론된 판결)만 분쟁 소송으로 인정. 36사 재검증 has_contest_signal flip 0(무회귀).
    contest_litigation_rows = [
        row for row in litigation_rows
        if row.get("dispute_kind") == "management"
        or row.get("dispute_kind_inferred") == "management"
    ]
    has_contest_signal = bool(shareholder_side_rows or contest_litigation_rows or external_active_signals)

    # 사건 발견 vs 진짜 partial 분리.
    # 위임장(proxy_filing) + 소송(litigation) + 5% 활성 시그널 합산.
    total_signal_filings = len(enriched_proxy_rows) + len(litigation_rows) + len(activist_signals)
    filing_meta = build_filing_meta(
        filing_count=total_signal_filings,
        parsing_failures=0,
    )

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
            "litigation_dedup": litigation_dedup,
            "active_signal_count": len(activist_signals),
            "has_contest_signal": has_contest_signal,
            "top_holder": control_context.get("top_holder", {}),
            "related_total_pct": control_context.get("related_total_pct", 0.0),
            "treasury_pct": control_context.get("treasury_pct", 0.0),
            # 지분율의 기준연도. 참석률(의결권행사기준일)과 기준일이 다를 수 있어 경고에 쓴다.
            "ownership_basis_year": control_context.get("year", ""),
            "active_external_block_count": len(active_external_blocks),
            "active_overlap_block_count": len(overlap_blocks),
            # 임원·주요주주 소유상황(D002). 분쟁 신호 판정(has_contest_signal)에는 넣지 않는다 —
            # 임원 보고는 스톡옵션 행사·상속 등 분쟁과 무관한 사유가 태반이라 자동 판정 재료가 아니다.
            # 여기서는 「누가 문턱 아래에서 움직였나」만 세어 노출하고 판단은 읽는 쪽에 맡긴다.
            "insider_status": (insiders or {}).get("status_reason") if insiders else "not_requested",
            "insider_reporter_count": (insiders or {}).get("reporter_count") if insiders else None,
            "insider_accumulating_count": (
                sum(1 for r in (insiders.get("reporters") or []) if r["recent_window"]["accumulating"])
                if insiders and insiders.get("reporters") is not None else None
            ),
        },
        **filing_meta,
        "players": {
            "company_side_filers": company_side_filers,
            "shareholder_side_filers": shareholder_side_filers,
            "retail_activism_filers": retail_activism_filers,
            "active_external_blocks": active_external_blocks,
            "active_overlap_blocks": overlap_blocks,
        },
        "control_context": control_map,
        "available_scopes": ["summary", "fight", "litigation", "signals", "timeline", "vote_math", "insiders"],
    }
    if scope in {"summary", "fight"}:
        data["fight"] = enriched_proxy_rows
    if scope in {"summary", "litigation"}:
        data["litigation"] = litigation_rows
    if scope in {"summary", "signals"}:
        data["signals"] = activist_signals
        # 5% 대량보유 시계열 신호 (목적 전환 / 추가매입 / 보고 빈도) 명시 노출 (260605)
        data["block_holder_dynamics"] = control_map.get("block_holder_dynamics", [])
    if wants_insiders and insiders is not None:
        data["insider_holdings"] = insiders
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
    status = status_from_filing_meta(filing_meta)
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
    elif status == AnalysisStatus.NO_FILING:
        warnings.append(f"조사 구간 ({bgn_de}~{end_de}) 내 위임장/소송/5% 활성 시그널 없음 (정상)")

    data["usage"] = build_usage(client.api_call_snapshot() - _calls_start)

    return ToolEnvelope(
        tool="proxy_contest",
        status=status,
        subject=selected.get("corp_name", company_query),
        warnings=warnings,
        data=data,
        evidence_refs=evidence_refs,
        next_actions=next_actions,
    ).to_dict()
