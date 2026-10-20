"""영업(잠정)실적 (I002 공정공시) 파서 + 오케스트레이션.

DART 정기보고서(확정치, financial_metrics)보다 **먼저** 나오는 분기 잠정 실적:
- 분기말 며칠 뒤 거래소 공정공시(I002)로 자가 발표 → 가장 빠른 실적 신호.
- 정형 API 없음 → search_filings(I002) 발견 + get_document 원문파싱(공시검색+원문파싱 패턴).
- 서식 2종: ① 재무형(매출액·영업이익·법인세전이익·당기순이익 × 당해/누계 × 당기/전기/전년동기)
  ② 비재무형(자동차 판매대수 등 — 재무표는 전부 '-', 별도 도메인표) → raw 마크다운 degrade.

Layer: data tool (파싱, 판단 X). screener가 detail_kind="earnings"로 재사용.
"""
from __future__ import annotations
from open_proxy_mcp.clock import today_kst

import re
import asyncio
from datetime import date, timedelta
from typing import Any

from bs4 import BeautifulSoup

from open_proxy_mcp.services.company import (COMPANY_LOOKUP_NEXT_ACTION,
                                             company_ambiguous_warning,
                                             company_not_found_warning,
                                             resolve_company_query)
from open_proxy_mcp.dart.client import DartClientError
from open_proxy_mcp.services.contracts import AnalysisStatus, ToolEnvelope
from open_proxy_mcp.services.dart_safety import is_no_data
from open_proxy_mcp.services.segment_candidates import _table_to_grid
from open_proxy_mcp.services.fiscal_period import period_from_quarter_text, period_metadata
from open_proxy_mcp.services.revenue_account import match_revenue_label

# 기존 내부 테스트·호출부 호환 alias
_period_metadata = period_metadata

_UNIT = {"조원": 1e12, "십억원": 1e9, "억원": 1e8, "백만원": 1e6, "천원": 1e3, "원": 1.0}

#: 단위를 원문에서 못 읽었을 때 응답에 싣는 경고 — 금액을 어느 단위로 가정해 환산하지 않는다(DATA-U1).
UNIT_UNKNOWN_WARNING = ("원문에서 금액 단위(「단위 : …」)를 읽지 못해 headline 금액을 원 단위로 환산하지 않았다 — "
                        "`unit_raw`=None. 표 원문(table_markdown)의 단위를 확인하고, 증감률(%)만 믿을 것.")


def _find_unit(text: str) -> str | None:
    """원문의 「단위 : 백만원」·「(단위:억원)」·「단위 : 백만원, %」 에서 금액 단위를 읽는다. 쉼표 유무와 무관하다.

    단위 표기가 없거나 아는 단위가 아니면 None — 호출부가 어떤 단위를 **가정하지 않는다**
    (종전에는 쉼표가 없는 「(단위 : 억원)」을 못 읽고 백만원으로 환산해 금액이 100배 어긋날 수 있었다).
    """
    longest_first = sorted((k for k in _UNIT if len(k) >= 2), key=len, reverse=True)
    for m in re.finditer(r"단위\s*[:：]\s*([가-힣]+)", text):
        run = m.group(1)
        if run in _UNIT:
            return run
        hit = next((k for k in longest_first if run.startswith(k)), None)   # 「백만원입니다」처럼 한글이 이어 붙은 꼴
        if hit:
            return hit
    return None


def _strip_money(headline: dict[str, Any]) -> dict[str, Any]:
    """단위를 모를 때 headline 의 원 단위 금액을 비우고 원문 숫자를 `*_raw` 로 남긴다(증감률 %는 단위와 무관해 그대로)."""
    out: dict[str, Any] = {}
    for k, v in headline.items():
        nv = dict(v)
        for f in ("value_krw", "prior_value_krw", "change_krw"):
            if f in nv:
                if nv[f] is not None:
                    nv[f.replace("_krw", "_raw")] = nv[f]
                nv[f] = None
        out[k] = nv
    return out
# 재무 잠정실적 표준 5행(별도는 지배주주 line 없어 4행). 정규화 키로 매핑.
_METRICS = {
    "매출액": "revenue", "영업수익": "revenue",
    "영업이익": "operating_profit",
    "법인세비용차감전계속사업이익": "pretax_profit", "법인세비용차감전순이익": "pretax_profit",
    "법인세차감전이익": "pretax_profit", "법인세차감전순이익": "pretax_profit",
    "당기순이익": "net_income",
    "자본금": "capital_stock",
    "지배기업소유주지분순이익": "net_income_controlling",
    "지배기업소유주지분에귀속되는당기순이익": "net_income_controlling",
    "지배기업소유주지분에귀속되는순이익": "net_income_controlling",
}
_PROV_PAT = re.compile(r"영업\s*\(?잠정\)?\s*실적|영업잠정실적")

#: 라벨 뒤에 붙는 기간 꼬리 — [기재정정] 표는 구분 열이 없어 「- 매출액(당해실적)」·「매출액(당기실적)」처럼
#: 라벨과 기간이 한 칸에 온다(260906 캐시 실측 157건 중 정정 4건이 전부 이 꼴이라 headline 이 비었다).
_PERIOD_TAG = re.compile(r"\((당해|당기|누계)실적\)$")


def _label_key(cell: str | None) -> tuple[str | None, str | None]:
    """표 첫 열 라벨 → (표준 키, 기간 꼬리). 공백·항목 대시를 떼고 `_METRICS` 정확 일치, 매출만은
    공용 어휘(`revenue_account.match_revenue_label`: 수익(매출액)·매출·보험수익·순영업수익…)로 한 번 더 본다.
    """
    label = re.sub(r"^[-\s·ㆍ]+", "", re.sub(r"\s+", "", cell or ""))
    tag = None
    m = _PERIOD_TAG.search(label)
    if m:
        tag = m.group(1) + "실적"
        label = label[: m.start()]
    key = _METRICS.get(label)
    if key is None and match_revenue_label(label):
        key = "revenue"
    return key, tag


def _num_after(cell: str, marker: str) -> float | None:
    """「당해실적: 473,133 전기대비증감율(%): 5.0 …」 같은 한 칸 요약에서 marker 바로 뒤 숫자."""
    m = re.search(re.escape(marker) + r"\s*[:：]?\s*(-?[\d,]+(?:\.\d+)?)", cell or "")
    return _num(m.group(1)) if m else None


def _is_structure_change_report(report_nm: str) -> bool:
    """I001 손익구조 변경 공시 중 실적표 본문인 것만 통과시킨다."""
    compact = re.sub(r"\s+", "", report_nm or "")
    # 🔴 **코스피는 「변경」, 코스닥은 「변동」이다** (2026-08-27 실측).
    # `이상변경` 만 보던 탓에 코스닥 공시가 통째로 잠정실적 분기로 새어
    # `당기실적` 표를 못 찾고 period·headline·fiscal_year 가 전부 빠졌다.
    # 표본 5/5 가 4칸만 나왔다(코스피 같은 서식은 10.4칸).
    return compact.startswith("매출액또는손익구조30%") and (
        "이상변경" in compact or "이상변동" in compact)


def _num(s: str) -> float | None:
    s = (s or "").replace(",", "").replace(" ", "").strip()
    if s in ("", "-", "－", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _clean_render(table) -> str:
    """colspan확장 격자 → 마크다운. ※잠정치 boilerplate 행만 제거(데이터·단위·헤더는 유지)."""
    grid = _table_to_grid(table)
    rows = []
    for r in grid:
        # DART 정정/주석 표는 rowspan 확장으로 같은 긴 문장이 여러 셀에 복제된다.
        # 숫자 표의 동일값(당기=전기 등)은 보존해야 하므로, 장문 주석 행에서만 접는다.
        joined_raw = " ".join(c.strip() for c in r if c.strip())
        note_row = "※" in joined_raw or any(len(c.strip()) >= 35 for c in r)
        if note_row:
            compact_r = []
            for cell in r:
                cell = cell.strip()
                if cell and compact_r and cell == compact_r[-1]:
                    continue
                compact_r.append(cell)
            r = compact_r
        joined = " ".join(c.strip() for c in r if c.strip())
        if "정보제공" in joined:   # 이후는 IR 담당·배포일 등 메타 — 실적표 아님
            break
        if not joined or ("잠정치로서" in joined and "확정치" in joined):
            continue
        rows.append("| " + " | ".join(c.strip() for c in r) + " |")
    if len(rows) >= 2:
        rows.insert(1, "|" + "---|" * (rows[0].count("|") - 1))
    return "\n".join(rows)


def _headline_from_grid(grid: list[list[str]], factor: float) -> dict[str, Any]:
    """colspan확장 격자에서 headline(매출·영업익·순익 당기값+YoY%). 열은 **헤더로 식별**(positional
    backward-search 금물 — 적자전환/음수 YoY에서 '전년동기실적' 절대값을 오채택함, 260719 멀티에이전트 검출).
    표준서식: 헤더 '당기실적'=값열 / 두 번째 '증감율(%)'=전년동기대비(YoY)열. best-effort(진실은 table_markdown)."""
    value_col = yoy_col = None
    for r in grid:
        for j, c in enumerate(r):
            if c.replace(" ", "") == "당기실적" and value_col is None:
                value_col = j
        pct_cols = [j for j, c in enumerate(r) if "증감율" in c]
        if len(pct_cols) >= 2 and yoy_col is None:
            yoy_col = pct_cols[-1]   # 마지막 증감율(%) = 전년동기대비(전기대비가 앞)
    if value_col is None:
        value_col = 2
    if yoy_col is None:
        yoy_col = value_col + 5   # 표준: 당기 뒤 [전기, 전기대비%, 흑자적자, 전년동기, 전년대비%]
    head: dict[str, Any] = {}
    for row in grid:
        if len(row) <= value_col or len(row) < 2:
            continue
        k, tag = _label_key(row[0])
        if k is None or tag == "누계실적":
            continue
        # 표준 서식은 구분 열(row[1])이 「당해실적」, 정정·변형 서식은 라벨 꼬리 「(당해실적)」·「(당기실적)」.
        if row[1].strip() == "당해실적" or tag in ("당해실적", "당기실적"):
            val = _num(row[value_col])
            yoy = _num(row[yoy_col]) if len(row) > yoy_col else None   # 음수·'-'(적자전환) 모두 정확 처리
            head[k] = {"value_krw": (val * factor) if val is not None else None, "yoy_pct": yoy}
    return {k: v for k, v in head.items() if v.get("value_krw") is not None}


def _correction_headline(soup: BeautifulSoup, factor: float) -> dict[str, Any]:
    """[기재정정] 표의 정정후 열을 headline으로 노출한다."""
    table = next((t for t in soup.find_all("table")
                  if re.search(r"정정\s*후", t.get_text())
                  and any(label in t.get_text() for label in ("매출액", "영업이익", "당기순이익"))), None)
    if table is None:
        return {}
    head: dict[str, Any] = {}
    for row in _table_to_grid(table):
        hit = next(((i, k, tag) for i, (k, tag) in ((i, _label_key(c)) for i, c in enumerate(row)) if k), None)
        if not hit:
            continue
        idx, key, tag = hit
        if tag == "누계실적" or key in head:   # 당해 행이 먼저 온다 — 누계로 덮지 않는다
            continue
        tail = row[idx + 1:]
        # 셀이 「당해실적: 473,133 전기대비증감율(%): …」 한 줄 요약이면 당해실적 값만 뽑는다.
        numeric = [n for n in ((_num_after(c, "당해실적") if "당해실적" in (c or "") else _num(c)) for c in tail)
                   if n is not None]
        if not numeric:
            continue
        # 정정전·정정후 순서가 표준이며, 비표준 표도 마지막 숫자를 정정후로 본다.
        head[key] = {"value_krw": numeric[-1] * factor,
                     "prior_value_krw": numeric[0] * factor if len(numeric) > 1 else None}
    return head


def parse_provisional_earnings(html: str, report_nm: str, fiscal_end_month: int | None = None) -> dict[str, Any]:
    """영업(잠정)실적 원문 → markdown-primary. table_markdown(항상, colspan확장) + headline(best-effort).
    재무형=매출/영업익 표, 비재무형(자동차 판매대수 등)=도메인 표. 둘 다 table_markdown이 통째로 담음."""
    if _is_structure_change_report(report_nm):
        return _parse_structure_change(html, fiscal_end_month=fiscal_end_month)

    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    unit_label = _find_unit(text)                 # 못 찾으면 None — 백만원으로 가정하지 않는다
    factor = _UNIT[unit_label] if unit_label else 1.0   # 단위를 모르면 1.0 으로 읽고 아래에서 금액을 비운다(증감률·종류 판정만 쓴다)
    unit_extra = {} if unit_label else {"unit_warning": UNIT_UNKNOWN_WARNING}
    consolidated = "연결재무제표기준" in (report_nm or "") or "연결재무제표 기준" in text
    pm = re.search(r"(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})", text)
    if pm:
        period = {"start": pm.group(1), "end": pm.group(2)}
    else:
        ym = re.search(r"(\d{4})[.-](\d{2})\s*~\s*(\d{4})[.-](\d{2})", text)
        period = ({"start": f"{ym.group(1)}-{ym.group(2)}", "end": f"{ym.group(3)}-{ym.group(4)}"} if ym
                  else period_from_quarter_text(text, fiscal_end_month))   # 날짜 범위가 없으면 「2025년 2분기」 문구에서(260907)

    tables = soup.find_all("table")
    correction_headline = _correction_headline(soup, factor) if "정정" in (report_nm or "") else {}
    if correction_headline:
        return {"consolidated": consolidated, "unit_raw": unit_label, "period": period,
                **_period_metadata(period, fiscal_end_month=fiscal_end_month), "kind": "financial", "correction": True,
                "headline": correction_headline if unit_label else _strip_money(correction_headline), **unit_extra,
                "table_markdown": "\n\n".join(p for p in (_clean_render(t) for t in tables) if p)[:6000] or None}
    fin_table = next((t for t in tables if "당기실적" in t.get_text()
                      and any(k in t.get_text() for k in ("매출액", "영업수익", "수익(매출액)", "보험수익", "순영업수익", "공사수익"))), None)
    headline = _headline_from_grid(_table_to_grid(fin_table), factor) if fin_table is not None else {}
    kind = "financial" if headline else "non_financial"

    # table_markdown(primary): 실적표를 통째(보일러플레이트만 제거). 비재무형(현대차)도 판매대수가
    # 같은 표에 들어있어 그대로 담긴다. fin_table 못 찾으면 숫자 있는 데이터표들로 폴백.
    if fin_table is not None:
        table_markdown = _clean_render(fin_table)
    else:
        parts = [_clean_render(t) for t in tables
                 if re.search(r"\d{3,}", t.get_text()) and "정보제공" not in t.get_text()]
        table_markdown = "\n\n".join(p for p in parts if p)
    table_markdown = (table_markdown or "")[:6000] or None
    return {"consolidated": consolidated, "unit_raw": unit_label, "period": period,
            **period_metadata(period, fiscal_end_month=fiscal_end_month), "kind": kind,
            "headline": headline if unit_label else _strip_money(headline), **unit_extra,
            "table_markdown": table_markdown}


# 「매출액 또는 손익구조 변동」 서식은 **절마다 열 구성이 다르다**(2026-08-28 실측, 포시에스).
#   3. 손익구조변동내용 → 당해 · 직전 · 증감금액 · 증감비율(%) · 흑자적자전환여부
#   4. 재무현황       → 당해 · 직전 뿐 (증감 열이 아예 없다)
# 예전엔 라벨 뒤 숫자를 세어 `numeric[3]` 을 증감비율로 썼다. 재무현황 행은 숫자가 당해·직전만
# 반복돼 **자본금 액수(13,660,984,500)가 그대로 % 로 찍혔다**. 열은 절 머리행으로 식별한다.
_STRUCT_COL_ROLES = (
    ("당해사업연도", "value"), ("직전사업연도", "prior"),
    ("증감금액", "change"), ("증감비율", "yoy"), ("흑자적자전환", "turnover"),
)


def _struct_column_map(row: list[str]) -> dict[str, int] | None:
    """구조변동 표의 절 머리행 → {역할: 열번호}. 머리행이 아니면 None."""
    roles: dict[str, int] = {}
    for j, cell in enumerate(row):
        compact = re.sub(r"\s+", "", cell or "")
        for token, role in _STRUCT_COL_ROLES:
            if token in compact and role not in roles:
                roles[role] = j
    return roles if "value" in roles else None


def _parse_structure_change(html: str, fiscal_end_month: int | None = None) -> dict[str, Any]:
    """I001 「매출액 또는 손익구조 30% 이상 변경」 표를 같은 headline 계약으로 변환."""
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    unit_label = _find_unit(text)                 # 일반 서식과 같은 규칙 — 못 찾으면 None(천원으로 가정하지 않는다)
    factor = _UNIT[unit_label] if unit_label else 1.0
    consolidated = "연결" in text and "별도" not in text[:600]
    pm = re.search(r"(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})", text)
    if pm:
        period = {"start": pm.group(1), "end": pm.group(2)}
    else:
        ym = re.search(r"(\d{4})[.]?(\d{2})\s*~\s*(\d{4})[.]?(\d{2})", text)
        if ym:
            period = {"start": f"{ym.group(1)}-{ym.group(2)}", "end": f"{ym.group(3)}-{ym.group(4)}"}
        else:
            starts = re.findall(r"시작일\s+(\d{4}-\d{2}-\d{2})", text)
            ends = re.findall(r"종료일\s+(\d{4}-\d{2}-\d{2})", text)
            period = {"start": starts[0], "end": ends[0]} if starts and ends else None

    table = next((t for t in soup.find_all("table")
                  if "당해사업연도" in t.get_text() and "영업이익" in t.get_text()), None)
    grid = _table_to_grid(table) if table is not None else []
    headline: dict[str, Any] = {}
    cols: dict[str, int] | None = None
    for row in grid:
        section_cols = _struct_column_map(row)
        if section_cols is not None:
            cols = section_cols
            continue
        hit = next(((i, k) for i, (k, _tag) in ((i, _label_key(c)) for i, c in enumerate(row)) if k), None)
        if not hit:
            continue
        idx, key = hit
        tail = row[idx + 1:]
        turn = next((c.strip() for c in tail if c.strip() in ("흑자전환", "적자전환")), None)

        def _cell(role: str) -> float | None:
            j = (cols or {}).get(role)
            return _num(row[j]) if j is not None and idx < j < len(row) else None

        value = _cell("value")
        if cols is None or value is None:
            # 머리행을 못 찾은 비표준 표 — 예전 위치 기반으로 폴백(증감비율은 지어내지 않는다).
            numeric = [n for n in (_num(c) for c in tail) if n is not None]
            if not numeric:
                continue
            headline[key] = {
                "value_krw": numeric[0] * factor,
                "prior_value_krw": numeric[1] * factor if len(numeric) > 1 else None,
                "change_krw": numeric[2] * factor if len(numeric) > 2 else None,
                "yoy_pct": numeric[3] if len(numeric) >= 4 else None,
                "yoy_basis": "filing" if len(numeric) >= 4 else None,
                "turnover": turn,
            }
            continue
        prior = _cell("prior")
        change = _cell("change")
        yoy = _cell("yoy")
        yoy_basis = "filing" if yoy is not None else None
        if yoy is None and "yoy" not in cols and prior:
            # 재무현황 절처럼 원문에 증감비율 열이 **없을 때만** 직접 계산하고 그렇다고 밝힌다.
            # 열은 있는데 값이 '-'(적자전환 등)이면 회사가 비워둔 것이므로 계산하지 않는다.
            yoy = round((value - prior) / abs(prior) * 100, 2)
            yoy_basis = "computed"
        headline[key] = {
            "value_krw": value * factor,
            "prior_value_krw": prior * factor if prior is not None else None,
            "change_krw": change * factor if change is not None else None,
            "yoy_pct": yoy, "yoy_basis": yoy_basis, "turnover": turn,
        }

    table_markdown = _clean_render(table)[:6000] if table is not None else None
    return {"consolidated": consolidated, "unit_raw": unit_label, "period": period,
            **period_metadata(period, annual=True, fiscal_end_month=fiscal_end_month),
            "kind": "financial", "provisional_type": "fiscal_year_change",
            "headline": headline if unit_label else _strip_money(headline),
            **({} if unit_label else {"unit_warning": UNIT_UNKNOWN_WARNING}),
            "table_markdown": table_markdown}


def _is_attachment_only_correction(report_nm: str) -> bool:
    """`[첨부정정]` 은 첨부파일만 바꾼 공시라 본문 document.xml 이 없다(DART 014) — 본문은 원래 공시에 있다(260907)."""
    return "[첨부정정]" in (report_nm or "")


async def _find_provisional_candidates(client, corp_code: str, bgn_de: str, end_de: str) -> list[dict]:
    """창 안의 실적공시를 최신순으로 — `[첨부정정]` 은 뒤로 보낸다(본문이 없어 읽을 수 없으므로 마지막 수단).

    DART 013(그 창·유형에 공시 없음)은 **빈 목록**이다 — 실패가 아니다(261018). 종전엔 두 검색 중 하나만 013 이어도
    예외가 통째로 올라가 다른 쪽에서 찾은 공시까지 버리고 「공시 검색 실패(DART 013)」(status=error)로 끝났다.
    그 밖의 오류(020 한도·800 점검 등)는 그대로 올린다 — 호출부가 실패로 적는다.
    """
    async def _search(detail_ty: str) -> dict:
        try:
            return await client.search_filings(bgn_de=bgn_de, end_de=end_de, corp_code=corp_code,
                                               pblntf_ty="I", pblntf_detail_ty=detail_ty, page_count=40)
        except DartClientError as exc:
            if is_no_data(exc):
                return {"list": []}
            raise

    results = await asyncio.gather(_search("I002"), _search("I001"))
    cands = []
    for r in results:
        for item in (r.get("list") or []):
            nm = item.get("report_nm", "")
            if _PROV_PAT.search(nm) or _is_structure_change_report(nm):
                cands.append(item)
    seen: set[str] = set()
    cands = [x for x in cands if not (x.get("rcept_no") in seen or seen.add(x.get("rcept_no")))]   # I001·I002 검색이 같은 건을 겹쳐 준다
    cands.sort(key=lambda x: x.get("rcept_dt", ""), reverse=True)
    cands.sort(key=lambda x: _is_attachment_only_correction(x.get("report_nm", "")))   # 안정 정렬 — 최신순 유지한 채 첨부정정만 뒤로
    return cands


async def _find_latest_provisional(client, corp_code: str, bgn_de: str, end_de: str) -> dict | None:
    """I002와 I001 실적공시를 함께 검색하되 제목으로 엄격히 필터링. 첨부정정보다 본문 있는 공시를 먼저."""
    cands = await _find_provisional_candidates(client, corp_code, bgn_de, end_de)
    return cands[0] if cands else None


def _gap_not_applicable(parsed: dict[str, Any], stock_code: str | None) -> dict[str, Any] | None:
    """분기 컨센서스와 맞댈 수 없는 공시면 그 이유(`consensus_gap` 칸), 맞댈 수 있으면 None. DB 를 묻기 전에 거른다.

    261018 실측 39건 중 26건이 월간(월 매출·판매량)이었다 — 분기 추정과 기간이 달라 맞대면 거짓 숫자가 된다.
    """
    def _no(status: str, why: str) -> dict[str, Any]:
        return {"status": status, "why": why}
    if not stock_code:
        return _no("out_of_coverage", "상장 종목코드가 없다 — 컨센서스 스냅샷은 상장사만 담는다")
    if parsed.get("unit_warning"):
        return _no("not_comparable", "금액 단위를 원문에서 읽지 못해 원 단위 금액이 없다 — 맞댈 숫자가 없다")
    if parsed.get("kind") != "financial":
        # `non_financial` 은 「headline 이 비었다」는 뜻이다 — 판매대수 표만이 아니라 지표가 한 칸에 뭉친 정정 표도 여기로 온다.
        return _no("not_comparable", "표에서 매출·영업이익 숫자를 뽑지 못했다(판매대수 같은 비재무형 실적표이거나 "
                                     "지표가 한 칸에 뭉친 정정 표) — 맞댈 숫자가 없다")
    kind = parsed.get("period_kind")
    if kind == "month":
        return _no("not_comparable", "월간 실적 — 분기 컨센서스와 기간이 달라 맞대지 않는다")
    if kind == "annual":
        return _no("not_comparable", "연간(결산) 잠정치 — 이 칸은 분기 컨센서스만 맞댄다")
    if kind != "quarter" or not (parsed.get("period") or {}).get("end"):
        return _no("unknown", "실적기간을 날짜로 읽지 못했다 — 어느 분기 컨센서스와 맞댈지 모른다")
    if parsed.get("cumulative"):
        return _no("not_comparable", "누적 기간(반기·3분기 누적) — 분기 컨센서스와 기간이 달라 맞대지 않는다")
    return None


async def _consensus_gap(parsed: dict[str, Any], stock_code: str | None, rcept_dt: str) -> dict[str, Any]:
    """잠정 headline → 공시 직전 분기 컨센서스 대비. 계산은 `forward_estimates`(규칙 6) — 여기선 고르기만 한다.
    원문 값(`headline`·`table_markdown`)은 건드리지 않고 비교 칸만 더한다."""
    skip = _gap_not_applicable(parsed, stock_code)
    if skip:
        return skip
    from open_proxy_mcp.services.forward_estimates import quarter_consensus_gap
    head = parsed.get("headline") or {}

    def _v(key: str) -> float | None:
        return (head.get(key) or {}).get("value_krw")

    actual = {"revenue": _v("revenue"), "operating_profit": _v("operating_profit"),
              "net_income_controlling": _v("net_income_controlling")}
    if actual["net_income_controlling"] is None and parsed.get("consolidated") is False:
        # 별도 재무제표엔 비지배지분이 없다 — 별도 당기순이익이 곧 주주 몫이다(연결이면 맞대지 않는다).
        actual["net_income_controlling"] = _v("net_income")
    gap = await asyncio.to_thread(quarter_consensus_gap, stock_code, parsed["period"]["end"], rcept_dt,
                                  actual, parsed.get("consolidated"))
    if parsed.get("correction") and gap.get("as_of"):
        gap.setdefault("caveats", []).append(
            "정정 공시다 — 기준일을 정정일 전으로 잡아 원공시 뒤 스냅샷일 수 있다(그 추정은 원공시 잠정치를 이미 봤을 수 있다)")
    return gap


async def build_provisional_earnings_payload(
    company_query: str, *, months: int = 6, start_date: str | None = None,
    end_date: str | None = None, format: str = "md",
) -> dict[str, Any]:
    """회사의 최신 영업(잠정)실적을 구조화 반환. screener는 start_date=end_date=filed_at(좁은창)로 호출."""
    from open_proxy_mcp.dart.client import get_dart_client, DartClientError
    res = await resolve_company_query(company_query)
    if not res.selected:
        # 판별자는 candidates 유무가 아니라 **status** 다 — ERROR 도 근접 후보를 실어 오므로
        # candidates 로 가르면 「못 찾음」이 「모호」로 오분류된다.
        _amb = res.status == AnalysisStatus.AMBIGUOUS
        return ToolEnvelope(
            tool="provisional_earnings",
            status=AnalysisStatus.AMBIGUOUS if _amb else AnalysisStatus.ERROR,
            subject=company_query,
            data={"query": company_query,
                  "candidates": [c.get("corp_name") for c in (res.candidates or [])][:8]},
            warnings=[company_ambiguous_warning(company_query, res.candidates) if _amb
                      else company_not_found_warning(company_query)],
            next_actions=[COMPANY_LOOKUP_NEXT_ACTION]).to_dict()
    corp = res.selected
    client = get_dart_client()
    bgn_de = start_date or (today_kst() - timedelta(days=months * 31)).strftime("%Y%m%d")
    end_de = end_date or today_kst().strftime("%Y%m%d")
    try:
        cands = await _find_provisional_candidates(client, corp["corp_code"], bgn_de, end_de)
    except DartClientError as e:
        return ToolEnvelope(tool="provisional_earnings", status=AnalysisStatus.ERROR,
                            subject=corp.get("corp_name", ""),
                            warnings=[f"공시 검색 실패(DART {getattr(e, 'status', '?')})"]).to_dict()
    rept = cands[0] if cands else None
    if not rept:
        return ToolEnvelope(tool="provisional_earnings", status=AnalysisStatus.NO_FILING,
                            subject=corp.get("corp_name", ""),
                            warnings=[(f"{bgn_de}~{end_de} 창에 영업(잠정)실적 공시 없음" if (start_date or end_date)
                                       else f"최근 {months}개월 영업(잠정)실적 공시 없음") + " — 창을 넓히거나(months·start_date) financial_metrics 확정치로"]).to_dict()
    url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rept['rcept_no']}"
    extra_warnings: list[str] = []
    try:
        doc = None
        for i, cand in enumerate(cands):
            try:
                doc = await client.get_document_cached(cand["rcept_no"])
            except DartClientError as e:
                if str(getattr(e, "status", "")) != "014" or i == len(cands) - 1:
                    raise
                # 본문 없는 공시(첨부정정 등) — 다음 후보로 넘어가되 사용자에게 무엇을 건너뛰었는지 말한다
                extra_warnings.append(f"{cand.get('rcept_dt','')} 「{(cand.get('report_nm') or '').strip()}」 은 본문이 없어 건너뜀")
                continue
            rept = cand
            url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rept['rcept_no']}"
            break
        html = doc.get("html", "") if isinstance(doc, dict) else ""
        fem = None
        try:
            ci = await client.get_company_info(corp["corp_code"])
            fem = int(str(ci.get("acc_mt") or "").strip() or 0) or None
        except Exception:  # noqa: BLE001 — 회사 정보가 안 오면 12월 기본값(라벨에 출처를 적는다)
            fem = None
        parsed = parse_provisional_earnings(html, rept.get("report_nm", ""), fiscal_end_month=fem)
    except Exception as e:
        return ToolEnvelope(tool="provisional_earnings", status=AnalysisStatus.ERROR,
                            subject=corp.get("corp_name", ""),
                            data={"report": {"rcept_no": rept["rcept_no"], "url": url}},
                            warnings=[f"원문 파싱 실패: {type(e).__name__}"
                                      + (f"(DART {getattr(e, 'status', '')})" if isinstance(e, DartClientError) else "")]).to_dict()
    warnings = ["잠정치 — 향후 확정치와 다를 수 있음(감사 전)", *extra_warnings]
    if parsed.get("unit_warning"):
        warnings.append(parsed["unit_warning"])
    if parsed.get("kind") == "non_financial":
        warnings.append("표준 재무표(매출/영업이익) 미기재 — 도메인 실적표(지역별매출·판매대수·수주·판매량 등)로 공시, table_markdown 참조")
    data = {
        "company": {"name": corp.get("corp_name"), "corp_code": corp.get("corp_code"),
                    "stock_code": corp.get("stock_code")},
        "report": {"rcept_no": rept["rcept_no"], "report_nm": rept.get("report_nm"),
                   "rcept_dt": rept.get("rcept_dt"), "url": url},
        **parsed,
    }
    # 컨센서스와 맞댔을 때만 칸을 붙인다(261018 결정). 모르거나 없거나 맞댈 수 없으면 칸 없이 파싱 값만 —
    # 월간·커버리지 밖·이력 밖·DB 장애·연결/별도 불일치의 이유는 `_consensus_gap` 이 status·why 로 내지만 싣지 않는다.
    try:
        gap = await _consensus_gap(parsed, corp.get("stock_code"), rept.get("rcept_dt") or "")
    except Exception:  # noqa: BLE001 — 비교 칸이 실패해도 잠정 실적값은 그대로 낸다
        gap = None
    if gap and gap.get("status") == "ok":
        data["consensus_gap"] = gap
    return ToolEnvelope(tool="provisional_earnings", status=AnalysisStatus.EXACT,
                        subject=corp.get("corp_name", ""), data=data, warnings=warnings).to_dict()
