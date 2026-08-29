"""value_up facade 서비스."""

from __future__ import annotations

import asyncio
from datetime import date
from html import unescape
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

_SUPPORTED_SCOPES = {"summary", "plan", "commitments", "timeline"}
_VALUATION_KEYWORDS = ("기업가치제고", "기업가치 제고", "밸류업")
_COMMITMENT_KEYWORDS = ("주주환원", "자사주", "배당", "ROE", "ROIC", "PBR", "가이드", "중장기")


_NOISE_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\.xforms|font-family|font-size|padding|margin|\{[^}]*\}"),
    re.compile(r"<[^>]+>"),
    re.compile(r"&(?:nbsp|amp|lt|gt|quot|apos);"),
)


# 섹션 태그 → 한글. tool 렌더러가 그대로 재사용한다(사전을 두 벌 두면 한쪽만 고쳐져 샌다).
SECTION_LABELS_KO = {
    "implementation_result": "이행결과",
    "implementation_status": "이행현황",
    "implementation_outlook": "이행전망",
    "future_plan": "향후계획",
    "meta_reference": "메타/참조",
}


def _is_noise(chunk: str) -> bool:
    for pattern in _NOISE_PATTERNS:
        if pattern.search(chunk):
            return True
    alnum = sum(1 for ch in chunk if ch.isalnum())
    return alnum < 10


def _extract_highlights(text: str, keywords: tuple[str, ...], limit: int = 6) -> list[str]:
    clean = re.sub(r"\s+", " ", text or "")
    chunks = re.split(r"(?<=[.!?])\s+|(?<=다\.)\s+", clean)
    hits: list[str] = []
    for chunk in chunks:
        trimmed = chunk.strip()
        if not trimmed or _is_noise(trimmed):
            continue
        if any(keyword in trimmed for keyword in keywords):
            if trimmed not in hits:
                hits.append(trimmed[:240])
        if len(hits) >= limit:
            break
    return hits


# ── 수치 목표 ↔ 최신 실적 대조 (260828) ────────────────────────────────────────
# U 실사용 시험(260828): 「목표 4개 중 ROE·PBR 달성 여부를 도구가 대조해주지 않아
# 내가 직접 나눠 계산했다」. 그래서 **뽑히는 목표만** 실적과 나란히 놓는다.
#
# 원칙 (CLAUDE.md 「이 서버가 무엇을 하는 물건인가」):
#   - 원문을 지우고 표로 대체하지 않는다. `highlights`(핵심 문장)는 그대로 두고 표를 **더한다.**
#   - 정형화가 안 돼 못 뽑는 목표는 억지로 맞히지 않는다 — **원문 조각을 그대로 싣고
#     「대조 못 함」**이라 적는다.
#   - 실적값은 새로 계산하지 않는다. `financial_metrics` · `price_multiple_data` 가
#     이미 내는 값을 **그대로 가져다** 쓰고, 그 값의 기준(사업연도·연결·확정/정정·주가일)을
#     행마다 붙인다. 기준이 다른 값을 기준 없이 나란히 놓지 않는다.
_TARGET_METRICS: tuple[tuple[str, str, tuple[str, ...], str, str], ...] = (
    # (key, 라벨, 원문 표기, 실적 출처, 단위)
    ("debt_ratio", "부채비율", ("부채비율",), "fm:debt_ratio_pct", "%"),
    ("operating_margin", "영업이익률", ("영업이익률",), "fm:operating_margin_pct", "%"),
    ("net_margin", "순이익률", ("순이익률", "당기순이익률"), "fm:net_profit_margin_pct", "%"),
    ("roe", "ROE", ("ROE", "자기자본이익률"), "fm:roe_pct", "%"),
    ("roa", "ROA", ("ROA", "총자산이익률"), "fm:roa_pct", "%"),
    ("roic", "ROIC", ("ROIC", "투하자본이익률"), "fm:roic_pct", "%"),
    ("payout_ratio", "배당성향", ("배당성향",), "fm:payout_ratio_pct", "%"),
    ("current_ratio", "유동비율", ("유동비율",), "fm:current_ratio_pct", "%"),
    ("pbr", "PBR", ("PBR", "주가순자산비율"), "val:pbr_mrq", "배"),
    ("per", "PER", ("PER", "주가수익비율"), "val:per_fy0", "배"),
    ("dividend_yield", "배당수익률", ("배당수익률",), "val:dividend_yield_pct", "%"),
)

# 지표 표기 바로 뒤의 「숫자(범위) + 단위 + 비교어」. 범위(30~40%)를 함께 잡는다.
_TARGET_VALUE_RE = re.compile(
    r"(?P<low>\d+(?:\.\d+)?)\s*(?:[~∼～\-–]\s*(?P<high>\d+(?:\.\d+)?))?\s*"
    r"(?P<unit>%|퍼센트|배|배수)?\s*"
    r"(?P<cmp>이상|이하|초과|미만|수준|내외|달성|유지)?"
)
_CMP_KO = {"이상": "이상", "초과": "초과", "이하": "이하", "미만": "미만"}

# 기준이 갈리는 지표에만 붙는 상시 주의. 특정 회사 사정이 아니라 **지표의 성질**이다.
_METRIC_CAVEATS: dict[str, str] = {
    "payout_ratio": "배당성향은 「귀속 사업연도」 기준과 「실제 지급 시점」 기준이 다르다. "
                    "회사가 본문에서 「아직 미지급」이라고 밝히는 경우 이 실적값과 어긋난다 — "
                    "`dividend` 로 지급 여부를 따로 확인해라.",
    "dividend_yield": "배당수익률은 주가(시세일)와 DPS(사업연도)의 시점이 섞인 값이다.",
    "pbr": "PBR·PER 은 시세 기준일 값이라 사업연도 재무비율과 시점이 다르다.",
    "per": "PBR·PER 은 시세 기준일 값이라 사업연도 재무비율과 시점이 다르다.",
}


# 원문 조각을 낱말 중간에서 자르지 않는다 — 앞쪽 절 구분자(`3.` · `a.` · `-` 등)에 맞춘다.
_CLAUSE_BREAK_RE = re.compile(r"[.·\-–\]]\s")


def _clause_start(text: str, pos: int, lead: int = 45) -> int:
    head = text[max(0, pos - lead):pos]
    last = None
    for m in _CLAUSE_BREAK_RE.finditer(head):
        last = m
    return max(0, pos - lead) + last.end() if last else pos


def _num(value: str | None) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def extract_numeric_targets(text: str) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """밸류업 계획 원문에서 수치 목표를 뽑는다.

    돌려주는 것 둘:
      1. `targets`   — 지표·목표값·비교방향 + **그 목표가 적힌 원문 조각**
      2. `unparsed`  — 지표는 언급됐는데 수치를 못 읽은 자리. **원문 조각을 그대로 담는다.**
                       비워 두지 않는 것이 요점이다 — 읽는 쪽이 직접 판단할 수 있어야 한다.

    `target_text`·`source_text` 는 손대지 않은 원문이다. 인용해도 된다.
    """
    clean = re.sub(r"\s+", " ", text or "")
    targets: list[dict[str, Any]] = []
    unparsed: list[dict[str, str]] = []
    seen: set[str] = set()

    for key, label, aliases, source, unit in _TARGET_METRICS:
        for alias in aliases:
            for m in re.finditer(re.escape(alias), clean):
                if key in seen:
                    break
                tail = clean[m.end():m.end() + 45]
                vm = _TARGET_VALUE_RE.search(tail)
                snippet_start = _clause_start(clean, m.start())
                if not vm or not vm.group("low"):
                    unparsed.append({
                        "metric_key": key,
                        "metric_label": label,
                        "source_text": clean[snippet_start:m.end() + 70].strip(),
                        "reason": "지표는 언급됐으나 목표 수치를 읽지 못했다 — 원문을 직접 읽어라.",
                    })
                    continue
                low, high = _num(vm.group("low")), _num(vm.group("high"))
                comparator = _CMP_KO.get(vm.group("cmp") or "")
                if high is not None:
                    comparator = "범위"
                seen.add(key)
                targets.append({
                    "metric_key": key,
                    "metric_label": label,
                    "unit": unit,
                    "target_low": low,
                    "target_high": high,
                    "comparator": comparator or "",           # "" = 방향 불명 → 달성 판정 보류
                    "comparator_raw": vm.group("cmp") or "",
                    "actual_source": source,
                    # 원문 그대로 — 표가 원문을 대체하지 않는다는 뜻이다.
                    "target_text": clean[m.start():m.end() + vm.end()].strip(),
                    "source_text": clean[snippet_start:m.end() + vm.end() + 55].strip(),
                })
    # 같은 지표를 여러 번 말했는데 한 번도 수치를 못 읽은 것만 남긴다.
    unparsed = [u for u in unparsed if u["metric_key"] not in seen]
    deduped: list[dict[str, str]] = []
    for u in unparsed:
        if u["metric_key"] not in {d["metric_key"] for d in deduped}:
            deduped.append(u)
    return targets, deduped


def _judge(actual: float | None, target: dict[str, Any]) -> tuple[str, str]:
    """달성 여부 판정. 판정할 수 없으면 그렇다고 말한다 — 추정하지 않는다."""
    if actual is None:
        return "대조 못 함", "실적값 미확보"
    low, high, cmp_ = target["target_low"], target["target_high"], target["comparator"]
    if cmp_ == "범위" and low is not None and high is not None:
        return ("달성" if low <= actual <= high else "미달"), ""
    if cmp_ == "이상":
        return ("달성" if actual >= low else "미달"), ""
    if cmp_ == "초과":
        return ("달성" if actual > low else "미달"), ""
    if cmp_ == "이하":
        return ("달성" if actual <= low else "미달"), ""
    if cmp_ == "미만":
        return ("달성" if actual < low else "미달"), ""
    return "판정 보류", f"목표 문구에 방향(이상/이하)이 없다 — 원문 「{target['target_text']}」 확인"


async def compare_targets_with_actuals(
    targets: list[dict[str, Any]],
    company_ref: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """목표에 최신 실적값을 붙인다. **다른 도구가 이미 내는 값을 그대로 가져온다.**

    - `fm:` = `financial_metrics` summary (연결·최신 확정 사업연도)
    - `val:` = `price_multiple_data` firm (주가 기준일 명시)
    두 기준은 **시점이 다르다** — 행마다 「실적 기준」을 붙이고, 섞였으면 경고로 말한다.
    """
    warnings: list[str] = []
    if not targets:
        return [], warnings
    need_fm = any(t["actual_source"].startswith("fm:") for t in targets)
    need_val = any(t["actual_source"].startswith("val:") for t in targets)

    fm_data: dict[str, Any] = {}
    fm_basis = ""
    val_data: dict[str, Any] = {}
    val_basis = ""

    async def _load_fm():
        from open_proxy_mcp.services.financial_metrics import build_financial_metrics_payload
        return await build_financial_metrics_payload(company_ref, scope="summary")

    async def _load_val():
        from open_proxy_mcp.services.price_multiple_data import build_valuation_payload
        return await build_valuation_payload(company=company_ref)

    tasks = []
    if need_fm:
        tasks.append(("fm", _load_fm()))
    if need_val:
        tasks.append(("val", _load_val()))
    results = await asyncio.gather(*(t[1] for t in tasks), return_exceptions=True)

    for (tag, _), res in zip(tasks, results):
        if isinstance(res, Exception) or not isinstance(res, dict):
            warnings.append(f"목표 대조용 실적 조회 실패({tag}) — 목표는 원문 그대로 싣고 대조는 비운다.")
            continue
        data = res.get("data") or {}
        if tag == "fm":
            fm_data = data.get("summary") or {}
            period = (data.get("fiscal_period") or {}).get("label", "")
            report = data.get("source_report") or {}
            corrected = " · 정정본" if report.get("is_correction") else ""
            basis_kind = "연결" if data.get("consolidated") else "별도"
            fm_basis = " · ".join(x for x in (
                f"사업연도 {period}" if period else "",
                basis_kind,
                f"{report.get('report_nm', '')}{corrected}".strip(),
            ) if x)
        else:
            val_data = data.get("multiples") or {}
            price_date = data.get("price_date") or ""
            val_basis = " · ".join(x for x in (
                f"주가 {price_date} 종가" if price_date else "",
                f"FY{data.get('fiscal_year')} 재무" if data.get("fiscal_year") else "",
            ) if x)

    rows: list[dict[str, Any]] = []
    for t in targets:
        tag, field = t["actual_source"].split(":", 1)
        actual = (fm_data if tag == "fm" else val_data).get(field)
        basis = fm_basis if tag == "fm" else val_basis
        verdict, note = _judge(actual if isinstance(actual, (int, float)) else None, t)
        rows.append({
            **t,
            "actual": actual if isinstance(actual, (int, float)) else None,
            "actual_basis": basis or "실적 기준 미확인",
            "actual_tool": "financial_metrics" if tag == "fm" else "price_multiple_data",
            "verdict": verdict,
            "verdict_note": note,
            "caveat": _METRIC_CAVEATS.get(t["metric_key"], ""),
        })

    if fm_basis and val_basis and any(r["actual_tool"] == "price_multiple_data" for r in rows):
        warnings.append(
            "목표 대조표에 **기준이 다른 두 실적**이 섞여 있다 — 재무비율은 확정 사업연도 결산치, "
            f"PER/PBR·배당수익률은 시장 시세({val_basis}) 기준이다. 같은 시점 값이 아니다.")
    return rows, warnings


def _clean_text(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def _extract_plan_title(text: str) -> str:
    """본문의 `계획서 명칭` 값 추출.

    DART report_nm은 "기업가치제고계획(자율공시)"로 동일해도 본문 plan title이
    "2025년 ... 이행현황"처럼 실제 문서 성격을 담는 경우가 있다.
    """

    clean = _clean_text(text)
    m = re.search(
        r"계획서\s*명칭\s+(.+?)(?=\s*(?:2\.\s*주요\s*내용|주요\s*내용|3\.\s*결정일자|$))",
        clean,
    )
    if not m:
        return ""
    title = re.sub(r"\s+", " ", m.group(1)).strip(" :-")
    return title[:180]


def _extract_main_content(text: str) -> str:
    clean = _clean_text(text)
    m = re.search(
        r"2\.\s*주요\s*내용\s+(.+?)(?=\s*(?:3\.\s*(?:결정일자|조세특례제한법)|4\.\s*관련|5\.\s*기타|※\s*관련공시|$))",
        clean,
    )
    if not m:
        return ""
    return m.group(1).strip()


def _split_main_content_units(main_content: str) -> list[str]:
    content = _clean_text(main_content)
    if not content:
        return []
    # Progress reports use top-level numbered bullets; high-dividend republications
    # often use dash bullets. Do not split metric sub-bullets like "- [주당배당금]".
    if re.search(r"\d+\)", content):
        pieces = re.split(r"\s+(?=\d+\))", content)
    else:
        pieces = re.split(r"\s+(?=(?:-\s+(?!\[)|※\s*\(?참고\)?))", content)
    units: list[str] = []
    for piece in pieces:
        unit = piece.strip(" -")
        if unit:
            units.append(unit)
    return units or [content]


def _tag_implementation_unit(text: str) -> str | None:
    raw = text or ""
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return None
    if re.fullmatch(r"\[[^\]]+\]", (text or "").strip()):
        return None
    if "고배당기업" in compact or "참조" in compact or "재공시" in compact:
        return "meta_reference"
    if "이행결과" in raw:
        return "implementation_result"
    if "이행전망" in compact or "예상" in compact or "전망" in compact:
        return "implementation_outlook"
    if "배분원칙" in compact or "Upgrade" in text or "업그레이드" in compact or "중장기" in compact:
        return "future_plan"
    if (
        re.search(r"이행\s*현황", raw)
        or re.search(r"이행\s*내역", raw)
        or re.search(r"진행\s+현황", raw)
        or re.search(r"(?:'?\d{2}년|20\d{2}년).{0,24}(?:vs|대비|\+|-)", raw)
    ):
        return "implementation_status"
    return None


def _extract_implementation_sections(text: str) -> list[dict[str, str]]:
    main_content = _extract_main_content(text)
    if not main_content:
        return []
    sections: list[dict[str, str]] = []
    labels = SECTION_LABELS_KO
    for unit in _split_main_content_units(main_content):
        tag = _tag_implementation_unit(unit)
        if not tag:
            if sections and unit:
                sections[-1]["text"] = (sections[-1]["text"] + " - " + unit)[:600]
            continue
        sections.append({
            "tag": tag,
            "label": labels[tag],
            "text": unit[:600],
        })
    return sections


def _unsupported_scope_payload(company_query: str, scope: str) -> dict[str, Any]:
    return ToolEnvelope(
        tool="value_up",
        status=AnalysisStatus.REQUIRES_REVIEW,
        subject=company_query,
        warnings=[f"`{scope}` scope는 아직 지원하지 않는다."],
        data={"query": company_query, "scope": scope},
    ).to_dict()


def _yyyymmdd_to_kind_date(value: str) -> str:
    if len(value) == 8 and value.isdigit():
        return f"{value[:4]}-{value[4:6]}-{value[6:]}"
    return value


def _kind_html_to_text(html: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _filter_value_up_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered = [
        item for item in items
        if any(keyword in (item.get("report_nm") or "").replace(" ", "") for keyword in _VALUATION_KEYWORDS)
    ]
    filtered.sort(key=lambda row: (row.get("rcept_dt", ""), row.get("rcept_no", "")), reverse=True)
    return filtered


def _classify_value_up_item(report_name: str, plan_title: str = "") -> str:
    """기업가치제고 공시를 카테고리로 분류.

    - pre_announcement: 계획 수립/공시 예정 안내 (실제 계획 본문 없음)
    - meta_amendment: "고배당기업 표시" 같은 형식 재공시 (실제 본문 계획은 원본에 있음)
    - progress: "이행현황" 관련 재공시
    - plan: 실제 계획 본문 (원본 또는 개정)
    """

    name = (report_name or "").replace(" ", "")
    title = (plan_title or "").replace(" ", "")
    if "기업가치제고계획예고" in name or "기업가치제고계획예고" in title:
        return "pre_announcement"
    if "고배당기업" in name or "고배당법인" in name:
        return "meta_amendment"
    if "이행결과" in name or "이행결과" in title:
        return "progress"
    if re.search(r"이행\s*현황|진행\s+현황", name) or re.search(r"이행\s*현황|진행\s+현황", title):
        return "progress"
    return "plan"


def _item_report_name(item: dict[str, Any]) -> str:
    """DART item은 report_nm, KIND item은 report_name."""

    return item.get("report_nm") or item.get("report_name") or ""


def _item_key(item: dict[str, Any]) -> str:
    return item.get("rcept_no") or item.get("acptno") or f"{item.get('rcept_dt', item.get('disclosure_date', ''))}:{_item_report_name(item)}"


def _item_disclosure_date(item: dict[str, Any]) -> str:
    return item.get("rcept_dt", item.get("disclosure_date", ""))


def _item_to_value_up_ref(item: dict[str, Any], *, category: str, plan_title: str, note: str = "") -> dict[str, Any]:
    data = {
        "rcept_no": item.get("rcept_no", ""),
        "acptno": item.get("acptno", ""),
        "disclosure_date": _item_disclosure_date(item),
        "report_name": _item_report_name(item),
        "category": category,
        "plan_title": plan_title,
    }
    if note:
        data["note"] = note
    return data


def _select_latest_plan_item(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    """items 중 실제 계획 본문을 담은 최신 항목 선택.

    meta_amendment(고배당기업 형식 재공시)는 실제 계획 본문이 없으므로 제외한다.
    plan 카테고리가 없으면 progress도 허용, 그것도 없으면 None.

    [기재정정] 제외 우선 — 정정 본문이 변경 부분만 담을 위험 회피.
    제외 후 빈 결과면 정정 포함 첫 번째 fallback.
    참조: [[architecture/multi-upstream-pattern]] (정정공고 처리 표준).
    """

    plan_items = [it for it in items if _classify_value_up_item(_item_report_name(it)) == "plan"]
    if plan_items:
        non_corr = [it for it in plan_items if not _item_report_name(it).startswith("[기재정정]")]
        return (non_corr or plan_items)[0]
    progress_items = [it for it in items if _classify_value_up_item(_item_report_name(it)) == "progress"]
    if progress_items:
        non_corr = [it for it in progress_items if not _item_report_name(it).startswith("[기재정정]")]
        return (non_corr or progress_items)[0]
    return None


async def _search_value_up_items(
    corp_code: str,
    *,
    bgn_de: str,
    end_de: str,
) -> tuple[list[dict[str, Any]], list[str], str | None]:
    items, notices, error = await search_filings_by_report_name(
        corp_code=corp_code,
        bgn_de=bgn_de,
        end_de=end_de,
        pblntf_tys="",
        pblntf_detail_ty="I001",  # 기업가치제고계획 ∈ I001 (차집합 0 검증) — I 전체 페이지컷 회피
        keywords=_VALUATION_KEYWORDS,
        strip_spaces=True,
    )
    if error == "013":
        return [], notices, None
    return items, notices, error


async def _search_kind_value_up_items(
    stock_code: str,
    corp_name: str,
    *,
    bgn_de: str,
    end_de: str,
) -> tuple[list[dict[str, Any]], str | None]:
    client = get_dart_client()
    try:
        result = await client.kind_search_value_up(
            stock_code=stock_code,
            corp_name=corp_name,
            from_date=_yyyymmdd_to_kind_date(bgn_de),
            to_date=_yyyymmdd_to_kind_date(end_de),
        )
    except Exception as exc:  # KIND는 공식 API가 아니므로 에러 문자열 그대로 진단
        return [], str(exc)
    return result, None


def _build_value_up_evidence(
    latest: dict[str, Any],
    latest_source: str,
    source_type: SourceType,
    best_plan_item: dict[str, Any] | None,
) -> list[EvidenceRef]:
    refs = [
        EvidenceRef(
            evidence_id=f"ev_valueup_{latest.get('rcept_no') or latest.get('acptno', '')}",
            source_type=source_type,
            rcept_no=latest.get("rcept_no", latest.get("acptno", "")),
            rcept_dt=format_iso_date(latest.get("rcept_dt", latest.get("disclosure_date", ""))),
            report_nm=latest.get("report_nm", latest.get("report_name", "")),
            section="기업가치제고계획",
            note=f"최신 공시 ({'DART' if latest_source == 'dart' else 'KIND'})",
        )
    ]
    if best_plan_item and best_plan_item is not latest:
        plan_rcept = best_plan_item.get("rcept_no") or best_plan_item.get("acptno", "")
        plan_src_type = SourceType.DART_XML if best_plan_item.get("rcept_no") else SourceType.KIND_HTML
        refs.append(
            EvidenceRef(
                evidence_id=f"ev_valueup_plan_{plan_rcept}",
                source_type=plan_src_type,
                rcept_no=plan_rcept,
                rcept_dt=format_iso_date(best_plan_item.get("rcept_dt", best_plan_item.get("disclosure_date", ""))),
                report_nm=_item_report_name(best_plan_item),
                section="기업가치제고계획 원본/이행현황",
                note="commitment 문장 추출에 사용한 실계획 본문",
            )
        )
    return refs


async def build_value_up_payload(
    company_query: str,
    *,
    scope: str = "summary",
    year: int | None = None,
    start_date: str = "",
    end_date: str = "",
) -> dict[str, Any]:
    total_started_at = time.perf_counter()
    timings_ms: dict[str, int] = {}

    def _mark(stage: str, started_at: float) -> None:
        timings_ms[stage] = int((time.perf_counter() - started_at) * 1000)

    if scope not in _SUPPORTED_SCOPES:
        return _unsupported_scope_payload(company_query, scope)

    client = get_dart_client()
    _calls_start = client.api_call_snapshot()
    stage_started_at = time.perf_counter()
    resolution = await resolve_company_query(company_query)
    _mark("resolve_company", stage_started_at)
    if resolution.status == AnalysisStatus.ERROR or not resolution.selected:
        timings_ms["total"] = int((time.perf_counter() - total_started_at) * 1000)
        return ToolEnvelope(
            tool="value_up",
            status=AnalysisStatus.ERROR,
            subject=company_query,
            warnings=[company_not_found_warning(company_query)],
            data={
                "query": company_query,
                "scope": scope,
                "usage": build_usage(client.api_call_snapshot() - _calls_start),
                "timings_ms": timings_ms,
            },
        ).to_dict()
    if resolution.status == AnalysisStatus.AMBIGUOUS:
        timings_ms["total"] = int((time.perf_counter() - total_started_at) * 1000)
        return ToolEnvelope(
            tool="value_up",
            status=AnalysisStatus.AMBIGUOUS,
            subject=company_query,
            warnings=["회사 식별이 애매해 밸류업 공시를 자동 선택하지 않았다."],
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
                "timings_ms": timings_ms,
            },
        ).to_dict()

    async def timed_call(stage: str, coro):
        started_at = time.perf_counter()
        try:
            return await coro
        finally:
            _mark(stage, started_at)

    selected = resolution.selected
    target_year = year or date.today().year
    explicit_window = bool(start_date or end_date)
    default_end = date(target_year, 12, 31) if year else date.today()
    window_start, window_end, window_warnings = resolve_date_window(
        start_date=start_date,
        end_date=end_date,
        default_end=default_end,
        lookback_months=12,
    )
    warnings: list[str] = list(window_warnings)

    requested_bgn = format_yyyymmdd(window_start)
    requested_end = format_yyyymmdd(window_end)
    stage_started_at = time.perf_counter()
    items, search_notices, search_error = await timed_call(
        "dart_search.requested_window",
        _search_value_up_items(
            selected["corp_code"],
            bgn_de=requested_bgn,
            end_de=requested_end,
        ),
    )
    _mark("dart_search", stage_started_at)
    warnings.extend(search_notices)
    if search_error:
        return ToolEnvelope(
            tool="value_up",
            status=AnalysisStatus.ERROR,
            subject=selected.get("corp_name", company_query),
            warnings=[f"기업가치제고 공시 검색 실패: {search_error}"],
            data={
                "query": company_query,
                "scope": scope,
                "year": target_year,
                "usage": build_usage(client.api_call_snapshot() - _calls_start),
            },
        ).to_dict()

    kind_items: list[dict[str, Any]] = []
    kind_search_error: str | None = None
    if not items:
        stage_started_at = time.perf_counter()
        kind_items, kind_search_error = await timed_call(
            "kind_search.requested_window",
            _search_kind_value_up_items(
                selected.get("stock_code", ""),
                selected.get("corp_name", company_query),
                bgn_de=requested_bgn,
                end_de=requested_end,
            ),
        )
        _mark("kind_search", stage_started_at)

    if not items and not kind_items:
        diagnostic_bgn = f"{target_year - 2}0101"
        diagnostic_end = f"{target_year}1231"
        # DART 진단검색과 KIND 진단검색은 independent → 병렬.
        diag_dart_task = timed_call(
            "diagnostic_search.dart",
            _search_value_up_items(
                selected["corp_code"],
                bgn_de=diagnostic_bgn,
                end_de=diagnostic_end,
            ),
        )
        diag_kind_task = timed_call(
            "diagnostic_search.kind",
            _search_kind_value_up_items(
                selected.get("stock_code", ""),
                selected.get("corp_name", company_query),
                bgn_de=diagnostic_bgn,
                end_de=diagnostic_end,
            ),
        )
        stage_started_at = time.perf_counter()
        (diagnostic_items, diagnostic_notices, diagnostic_error), (diagnostic_kind_items, diagnostic_kind_error) = await asyncio.gather(
            diag_dart_task, diag_kind_task,
        )
        _mark("diagnostic_search", stage_started_at)
        warnings.extend(diagnostic_notices)
        diagnostics = {
            "requested_window": {
                "start_date": requested_bgn,
                "end_date": requested_end,
                "dart_filing_count": 0,
                "kind_filing_count": 0,
            },
            "diagnostic_window": {
                "start_date": diagnostic_bgn,
                "end_date": diagnostic_end,
                "dart_filing_count": len(diagnostic_items),
                "kind_filing_count": len(diagnostic_kind_items),
            },
        }
        if diagnostic_error:
            warnings.append(f"진단 검색 실패: {diagnostic_error}")
        if kind_search_error:
            warnings.append(f"KIND 검색 실패: {kind_search_error}")
        if diagnostic_kind_error:
            warnings.append(f"KIND 진단 검색 실패: {diagnostic_kind_error}")
        availability_status = "no_filing_found"
        if diagnostic_items or diagnostic_kind_items:
            availability_status = "exists_outside_requested_window"
            warnings.append(
                "요청 구간에는 기업가치제고 공시가 없지만, 진단 구간에서는 관련 공시가 확인된다."
            )
            sample_filings: list[dict[str, Any]] = [
                {
                    "source": "dart",
                    "rcept_no": item.get("rcept_no", ""),
                    "disclosure_date": item.get("rcept_dt", ""),
                    "report_name": item.get("report_nm", ""),
                }
                for item in diagnostic_items[:5]
            ]
            sample_filings.extend(
                {
                    "source": "kind",
                    "acptno": item.get("acptno", ""),
                    "disclosure_date": item.get("disclosure_date", ""),
                    "report_name": item.get("report_name", ""),
                }
                for item in diagnostic_kind_items[:5]
            )
            diagnostics["diagnostic_window"]["sample_filings"] = sample_filings[:10]
        else:
            if explicit_window:
                warnings.append("요청 구간에는 관련 공시가 없고, DART/KIND 진단 구간에서도 공시를 찾지 못했다.")
            else:
                warnings.append("요청 구간과 DART/KIND 진단 구간 모두에서 기업가치제고 공시를 찾지 못했다.")
        # 요청 구간 기준으로는 공시가 없으므로 NO_FILING으로 둔다.
        # 과거/진단 구간 존재 여부는 availability_status와 diagnostics로 보존한다.
        no_filing_meta = build_filing_meta(filing_count=0, parsing_failures=0)
        response_status = AnalysisStatus.NO_FILING
        return ToolEnvelope(
            tool="value_up",
            status=response_status,
            subject=selected.get("corp_name", company_query),
            warnings=warnings,
            data={
                "query": company_query,
                "company_id": _company_id(selected),
                "year": target_year,
                "window": {
                    "start_date": requested_bgn,
                    "end_date": requested_end,
                },
                "availability_status": availability_status,
                "search_diagnostics": diagnostics,
                "items": [],
                **no_filing_meta,
                "usage": build_usage(client.api_call_snapshot() - _calls_start),
                "timings_ms": {**timings_ms, "total": int((time.perf_counter() - total_started_at) * 1000)},
            },
        ).to_dict()

    latest_source = "dart"
    # [기재정정] 제외 우선, 비면 정정 포함 fallback ([[architecture/multi-upstream-pattern]])
    if items:
        non_corr = [it for it in items if not _item_report_name(it).startswith("[기재정정]")]
        latest = (non_corr or items)[0]
    else:
        non_corr_kind = [it for it in kind_items if not (it.get("report_nm") or "").startswith("[기재정정]")]
        latest = (non_corr_kind or kind_items)[0]
    availability_status = "found_in_requested_window" if items else "found_in_requested_window_kind_only"
    latest_text = ""
    latest_excerpt = ""
    source_type = SourceType.DART_XML
    if items:
        stage_started_at = time.perf_counter()
        latest_doc = await client.get_document_cached(latest["rcept_no"])
        latest_text = latest_doc.get("text", "")
        latest_excerpt = latest_text[:2000]
        source_type = SourceType.DART_XML
        _mark("load_latest_document", stage_started_at)
    else:
        latest_source = "kind"
        try:
            stage_started_at = time.perf_counter()
            latest_html = await client.kind_fetch_document(latest["acptno"])
            latest_text = _kind_html_to_text(latest_html)
            latest_excerpt = latest_text[:2000]
            _mark("load_latest_kind_document", stage_started_at)
        except DartClientError as exc:
            warnings.append(f"KIND 본문 조회 실패: {exc.status}")
        source_type = SourceType.KIND_HTML

    latest_plan_title = _extract_plan_title(latest_text)
    latest_category = _classify_value_up_item(_item_report_name(latest), plan_title=latest_plan_title)
    latest_implementation_sections = _extract_implementation_sections(latest_text)

    loaded_value_up_docs: dict[str, dict[str, Any]] = {
        _item_key(latest): {
            "item": latest,
            "text": latest_text,
            "plan_title": latest_plan_title,
            "category": latest_category,
            "implementation_sections": latest_implementation_sections,
            "source_type": source_type,
        }
    }

    async def load_value_up_doc(item: dict[str, Any]) -> dict[str, Any]:
        key = _item_key(item)
        if key in loaded_value_up_docs:
            return loaded_value_up_docs[key]
        text = ""
        doc_source_type = SourceType.DART_XML
        if item.get("rcept_no"):
            try:
                doc = await client.get_document_cached(item["rcept_no"])
                text = doc.get("text", "")
            except DartClientError as exc:
                warnings.append(f"밸류업 본문 조회 실패({item.get('rcept_no', '')}): {exc.status}")
        elif item.get("acptno"):
            doc_source_type = SourceType.KIND_HTML
            try:
                html = await client.kind_fetch_document(item["acptno"])
                text = _kind_html_to_text(html)
            except DartClientError as exc:
                warnings.append(f"밸류업 KIND 본문 조회 실패({item.get('acptno', '')}): {exc.status}")
        plan_title = _extract_plan_title(text)
        category = _classify_value_up_item(_item_report_name(item), plan_title=plan_title)
        loaded_value_up_docs[key] = {
            "item": item,
            "text": text,
            "plan_title": plan_title,
            "category": category,
            "implementation_sections": _extract_implementation_sections(text),
            "source_type": doc_source_type,
        }
        return loaded_value_up_docs[key]

    latest_plan_info: dict[str, Any] | None = None
    latest_status_info: dict[str, Any] | None = None
    meta_amendment_info: dict[str, Any] | None = None
    latest_result_info: dict[str, Any] | None = None
    candidates = items + kind_items

    async def classify_role_candidates(role_candidates: list[dict[str, Any]]) -> None:
        nonlocal latest_plan_info, latest_status_info, meta_amendment_info, latest_result_info
        for item in role_candidates:
            info = await load_value_up_doc(item)
            category = info["category"]
            sections = info["implementation_sections"]
            if category == "plan" and latest_plan_info is None:
                latest_plan_info = info
            elif category == "progress" and latest_status_info is None:
                latest_status_info = info
            elif category == "meta_amendment" and meta_amendment_info is None:
                meta_amendment_info = info
            if latest_result_info is None and any(section.get("tag") == "implementation_result" for section in sections):
                latest_result_info = info
            if latest_plan_info and latest_status_info:
                # Result is derived from already loaded plan/status/latest meta; do not fetch
                # extra historical filings solely to prove that result is absent.
                break

    stage_started_at = time.perf_counter()
    await classify_role_candidates(candidates)
    _mark("classify_value_up_roles", stage_started_at)

    if not explicit_window and (latest_plan_info is None or latest_status_info is None):
        backfill_bgn = f"{target_year - 2}0101"
        stage_started_at = time.perf_counter()
        backfill_items, backfill_notices, backfill_error = await timed_call(
            "role_backfill_search.dart",
            _search_value_up_items(
                selected["corp_code"],
                bgn_de=backfill_bgn,
                end_de=requested_end,
            ),
        )
        _mark("role_backfill_search", stage_started_at)
        warnings.extend(backfill_notices)
        if backfill_error:
            warnings.append(f"밸류업 역할 보강 검색 실패: {backfill_error}")
        seen_keys = {_item_key(item) for item in candidates}
        new_backfill_items = [item for item in backfill_items if _item_key(item) not in seen_keys]
        if new_backfill_items:
            stage_started_at = time.perf_counter()
            await classify_role_candidates(new_backfill_items)
            _mark("classify_value_up_roles.backfill", stage_started_at)
            candidates.extend(new_backfill_items)

    # Backward-compatible evidence/highlight source: commitments come from plan first,
    # then latest status, then latest.
    best_plan_item = latest_plan_info["item"] if latest_plan_info else None
    best_plan_text = latest_plan_info["text"] if latest_plan_info else ""
    best_plan_title = latest_plan_info["plan_title"] if latest_plan_info else ""
    best_plan_implementation_sections = latest_plan_info["implementation_sections"] if latest_plan_info else []

    highlight_source_text = best_plan_text or (latest_status_info["text"] if latest_status_info else latest_text)
    highlight_source_length = len(highlight_source_text)
    highlights = _extract_highlights(highlight_source_text, _COMMITMENT_KEYWORDS)

    # 수치 목표 ↔ 최신 실적 대조 (260828). commitments scope 에서만 — 실적 조회가 2 tool 붙는다.
    # **원문(highlights·implementation_sections)은 그대로 두고 표를 더한다.**
    target_rows: list[dict[str, Any]] = []
    unparsed_targets: list[dict[str, str]] = []
    if scope == "commitments" and highlight_source_text:
        parsed_targets, unparsed_targets = extract_numeric_targets(highlight_source_text)
        if parsed_targets:
            company_ref = selected.get("stock_code") or selected.get("corp_name") or company_query
            stage_started_at = time.perf_counter()
            try:
                target_rows, target_warnings = await compare_targets_with_actuals(
                    parsed_targets, company_ref)
                warnings.extend(target_warnings)
            except Exception as exc:  # 대조 실패가 밸류업 공시 자체를 못 보게 만들면 안 된다
                target_rows = []
                warnings.append(
                    f"수치 목표 대조 실패({type(exc).__name__}) — 목표 원문은 「핵심 문장」에 그대로 있다. "
                    "financial_metrics·price_multiple_data 로 직접 확인해라.")
            _mark("compare_targets_with_actuals", stage_started_at)
        if unparsed_targets:
            warnings.append(
                "수치를 읽지 못한 목표 지표가 있다 — 원문 조각을 「대조 못 한 목표」에 그대로 실었다. "
                f"({', '.join(u['metric_label'] for u in unparsed_targets)})")

    # 자사주 소각 교차참조: 정책 tool이라도 최근 소각 건수·규모를 함께 보여줘
    # "약속 vs 이행"의 한 축을 드러낸다.
    treasury_cross_ref: dict[str, Any] = {}
    try:
        from open_proxy_mcp.services.treasury_share import fetch_treasury_signal_summary
        treasury_window_start, _treasury_window_end, _ = resolve_date_window(
            start_date="",
            end_date="",
            default_end=window_end,
            lookback_months=24,
        )
        treasury_summary, treasury_warnings = await fetch_treasury_signal_summary(
            selected["corp_code"],
            bgn_de=format_yyyymmdd(treasury_window_start),
            end_de=requested_end,
        )
        # cancelation_count는 별도 "자기주식소각결정" 공시 건수. 일부 기업은 별도 공시 없이
        # 취득결정 공시의 `aq_pp`(취득목적)에 "소각"을 명시하므로 `acquisition_for_cancelation_count`도 함께 노출.
        treasury_cross_ref = {
            "cancelation_decision_count_24m": treasury_summary.get("cancelation_count", 0),
            "acquisition_count_24m": treasury_summary.get("acquisition_count", 0),
            "acquisition_for_cancelation_count_24m": treasury_summary.get("acquisition_for_cancelation_count", 0),
            "acquisition_for_cancelation_amount_krw_24m": treasury_summary.get("acquisition_for_cancelation_amount_total_krw", 0),
            "trust_contract_count_24m": treasury_summary.get("trust_contract_count", 0),
            "note": "최근 24개월 자사주 이벤트 요약. 상세는 `treasury_share`로 확인.",
        }
    except Exception:
        pass
    if not highlights:
        if highlight_source_length < 500:
            warnings.append(f"원본 공시 본문이 매우 얇다(text_length={highlight_source_length}). PDF 첨부 중심 공시일 가능성이 높으니 viewer_url로 DART/KIND 뷰어에서 직접 확인한다.")
        else:
            warnings.append("원문 텍스트는 확보됐으나 commitment 키워드 매칭 문장이 없다. viewer_url로 원문 구조 확인 필요.")

    # 사건 발견 + 파싱 결과 메타.
    # latest 본문 텍스트가 비어 있거나 highlight 추출 실패면 partial 신호로 간주.
    parsing_failures = 0
    if items or kind_items:
        # 본문 텍스트가 너무 얇으면 파싱 실패로 카운트
        if highlight_source_length < 500 and not highlights:
            parsing_failures = 1
    filing_meta = build_filing_meta(
        filing_count=len(items) + len(kind_items),
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
        "year": target_year,
        "window": {
            "start_date": requested_bgn,
            "end_date": requested_end,
        },
        "availability_status": availability_status,
        "primary_source": latest_source,
        **filing_meta,
        "search_diagnostics": {
            "requested_window": {
                "start_date": requested_bgn,
                "end_date": requested_end,
                "dart_filing_count": len(items),
                "kind_filing_count": len(kind_items),
            }
        },
        "latest": {
            "rcept_no": latest.get("rcept_no", ""),
            "acptno": latest.get("acptno", ""),
            "disclosure_date": latest.get("rcept_dt", latest.get("disclosure_date", "")),
            "report_name": latest.get("report_nm", latest.get("report_name", "")),
            "filer_name": latest.get("flr_nm", latest.get("filer_name", "")),
            "source_type": getattr(source_type, "value", source_type),
            "category": latest_category,
            "plan_title": latest_plan_title,
        },
        "available_scopes": sorted(_SUPPORTED_SCOPES),
    }
    if latest_implementation_sections:
        data["latest"]["implementation_sections"] = latest_implementation_sections
    if latest_plan_info:
        data["latest_plan"] = _item_to_value_up_ref(
            latest_plan_info["item"],
            category=latest_plan_info["category"],
            plan_title=latest_plan_info["plan_title"],
            note="가장 최신 본계획/개정계획. 무엇을 하겠다는 계획인지 확인하는 기준 문서.",
        )
        if latest_plan_info["implementation_sections"]:
            data["latest_plan"]["implementation_sections"] = latest_plan_info["implementation_sections"]
    else:
        data["latest_plan"] = None
    if latest_status_info:
        data["latest_status"] = _item_to_value_up_ref(
            latest_status_info["item"],
            category=latest_status_info["category"],
            plan_title=latest_status_info["plan_title"],
            note="가장 최신 이행현황/이행내역. 계획 대비 어디까지 진행됐는지 확인하는 기준 문서.",
        )
        if latest_status_info["implementation_sections"]:
            data["latest_status"]["implementation_sections"] = latest_status_info["implementation_sections"]
    else:
        data["latest_status"] = None
    if latest_result_info:
        result_sections = [
            section for section in latest_result_info["implementation_sections"]
            if section.get("tag") == "implementation_result"
        ]
        data["latest_result"] = _item_to_value_up_ref(
            latest_result_info["item"],
            category=latest_result_info["category"],
            plan_title=latest_result_info["plan_title"],
            note="명시적 `이행결과`가 발견된 경우에만 노출한다.",
        )
        data["latest_result"]["implementation_sections"] = result_sections
    else:
        data["latest_result"] = None
    if meta_amendment_info:
        data["meta_amendment"] = _item_to_value_up_ref(
            meta_amendment_info["item"],
            category=meta_amendment_info["category"],
            plan_title=meta_amendment_info["plan_title"],
            note="고배당기업 표시 등 형식 재공시. 본계획이나 최신 이행현황을 대체하지 않는다.",
        )
        if meta_amendment_info["implementation_sections"]:
            data["meta_amendment"]["implementation_sections"] = meta_amendment_info["implementation_sections"]
    implementation_sections = (
        (latest_status_info["implementation_sections"] if latest_status_info else [])
        or best_plan_implementation_sections
        or latest_implementation_sections
    )
    if implementation_sections:
        data["implementation_sections"] = implementation_sections
    if meta_amendment_info and meta_amendment_info["implementation_sections"]:
        data["embedded_results"] = [
            section for section in meta_amendment_info["implementation_sections"]
            if section.get("tag") in {"implementation_result", "implementation_status", "implementation_outlook"}
        ]
    if scope in {"summary", "timeline"}:
        data["items"] = [
            {
                "source": "dart",
                "rcept_no": item.get("rcept_no", ""),
                "acptno": "",
                "disclosure_date": item.get("rcept_dt", ""),
                "report_name": item.get("report_nm", ""),
                "filer_name": item.get("flr_nm", ""),
            }
            for item in items[:10]
        ]
        data["items"].extend(
            {
                "source": "kind",
                "rcept_no": "",
                "acptno": item.get("acptno", ""),
                "disclosure_date": item.get("disclosure_date", ""),
                "report_name": item.get("report_name", ""),
                "filer_name": item.get("filer_name", ""),
            }
            for item in kind_items[:10]
        )
    if scope in {"summary", "plan", "commitments"}:
        data["latest_excerpt"] = latest_excerpt
        data["highlights"] = highlights
        data["highlight_source_text_length"] = highlight_source_length
    if scope == "commitments":
        # 표는 원문을 대체하지 않는다 — highlights 와 **함께** 나간다.
        data["numeric_targets"] = target_rows
        data["numeric_targets_unparsed"] = unparsed_targets
    if scope in {"summary", "commitments"} and treasury_cross_ref:
        data["treasury_cross_ref"] = treasury_cross_ref

    data["usage"] = build_usage(client.api_call_snapshot() - _calls_start)
    timings_ms["total"] = int((time.perf_counter() - total_started_at) * 1000)
    data["timings_ms"] = timings_ms

    return ToolEnvelope(
        tool="value_up",
        status=status_from_filing_meta(filing_meta),
        subject=selected.get("corp_name", company_query),
        warnings=warnings,
        data=data,
        evidence_refs=_build_value_up_evidence(latest, latest_source, source_type, best_plan_item),
        next_actions=[
            "commitments scope로 주주환원/ROE 관련 문장 확인" if scope == "summary" else "dividend, ownership_structure와 함께 보면 주주환원 맥락이 더 잘 보인다.",
        ],
    ).to_dict()
