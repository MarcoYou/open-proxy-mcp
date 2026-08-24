"""영업(잠정)실적 (I002 공정공시) 파서 + 오케스트레이션.

DART 정기보고서(확정치, financial_metrics)보다 **먼저** 나오는 분기 잠정 실적:
- 분기말 며칠 뒤 거래소 공정공시(I002)로 자가 발표 → 가장 빠른 실적 신호.
- 정형 API 없음 → search_filings(I002) 발견 + get_document 원문파싱(공시검색+원문파싱 패턴).
- 서식 2종: ① 재무형(매출액·영업이익·법인세전이익·당기순이익 × 당해/누계 × 당기/전기/전년동기)
  ② 비재무형(자동차 판매대수 등 — 재무표는 전부 '-', 별도 도메인표) → raw 마크다운 degrade.

Layer: data tool (파싱, 판단 X). screener가 detail_kind="earnings"로 재사용.
"""
from __future__ import annotations

import re
import asyncio
from datetime import date, timedelta
from typing import Any

from bs4 import BeautifulSoup

from open_proxy_mcp.services.company import resolve_company_query
from open_proxy_mcp.services.contracts import AnalysisStatus, ToolEnvelope
from open_proxy_mcp.services.segment_candidates import _table_to_grid

_UNIT = {"조원": 1e12, "십억원": 1e9, "억원": 1e8, "백만원": 1e6, "천원": 1e3, "원": 1.0}
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


def _period_metadata(period: dict[str, str] | None, *, annual: bool = False) -> dict[str, Any]:
    """실적기간을 달력분기가 아닌 회사 사업연도 기준 메타데이터로 정규화한다.

    I001 결산 공시는 종료월이 결산월이므로 이를 직접 사용한다. I002 분기 공시는
    당해 분기의 시작월로 결산월을 역산한다(예: 4~6월 → 3월 결산). 원문 기간을
    해석할 수 없으면 숫자 라벨을 만들지 않고 metadata만 비운다.
    """
    if not period:
        return {}
    try:
        start = date.fromisoformat(period["start"])
        end = date.fromisoformat(period["end"])
    except (KeyError, TypeError, ValueError):
        return {}

    if annual:
        fiscal_end_month = end.month
    else:
        # 분기 시작월은 결산월 다음 달이다. 1월 시작이면 12월 결산.
        fiscal_end_month = 12 if start.month == 1 else start.month - 1
    fiscal_year = end.year if end.month == fiscal_end_month else end.year + (1 if end.month > fiscal_end_month else 0)
    months_from_fy_start = (start.month - fiscal_end_month - 1) % 12
    quarter = (months_from_fy_start // 3) + 1 if not annual else None
    duration_days = (end - start).days + 1
    period_kind = "annual" if annual or duration_days >= 300 else "quarter"
    return {
        "fiscal_year": fiscal_year,
        "fiscal_year_end_month": fiscal_end_month,
        "period_kind": period_kind,
        "fiscal_quarter": quarter,
        "comparison_basis": "직전사업연도 대비" if period_kind == "annual" else "전년동기 대비",
    }


def _is_structure_change_report(report_nm: str) -> bool:
    """I001 손익구조 변경 공시 중 실적표 본문인 것만 통과시킨다."""
    compact = re.sub(r"\s+", "", report_nm or "")
    return compact.startswith("매출액또는손익구조30%") and "이상변경" in compact


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
        key0 = (row[0] or "").replace(" ", "")
        if key0 in _METRICS and row[1].strip() == "당해실적":
            k = _METRICS[key0]
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
        labels = [(i, re.sub(r"^[-\s]+", "", re.sub(r"\s+", "", c or "")))
                  for i, c in enumerate(row)]
        hit = next(((i, _METRICS[label]) for i, label in labels if label in _METRICS), None)
        if not hit:
            continue
        idx, key = hit
        tail = row[idx + 1:]
        numeric = [n for n in (_num(c) for c in tail) if n is not None]
        if not numeric:
            continue
        # 정정전·정정후 순서가 표준이며, 비표준 표도 마지막 숫자를 정정후로 본다.
        head[key] = {"value_krw": numeric[-1] * factor,
                     "prior_value_krw": numeric[0] * factor if len(numeric) > 1 else None}
    return head


def parse_provisional_earnings(html: str, report_nm: str) -> dict[str, Any]:
    """영업(잠정)실적 원문 → markdown-primary. table_markdown(항상, colspan확장) + headline(best-effort).
    재무형=매출/영업익 표, 비재무형(자동차 판매대수 등)=도메인 표. 둘 다 table_markdown이 통째로 담음."""
    if _is_structure_change_report(report_nm):
        return _parse_structure_change(html)

    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    um = re.search(r"단위\s*[:：]\s*([가-힣]+)\s*,", text)
    unit_label = um.group(1) if um else "백만원"
    factor = _UNIT.get(unit_label, 1e6)
    consolidated = "연결재무제표기준" in (report_nm or "") or "연결재무제표 기준" in text
    pm = re.search(r"(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})", text)
    if pm:
        period = {"start": pm.group(1), "end": pm.group(2)}
    else:
        ym = re.search(r"(\d{4})[.-](\d{2})\s*~\s*(\d{4})[.-](\d{2})", text)
        period = {"start": f"{ym.group(1)}-{ym.group(2)}", "end": f"{ym.group(3)}-{ym.group(4)}"} if ym else None

    tables = soup.find_all("table")
    correction_headline = _correction_headline(soup, factor) if "정정" in (report_nm or "") else {}
    if correction_headline:
        return {"consolidated": consolidated, "unit_raw": unit_label, "period": period,
                **_period_metadata(period), "kind": "financial", "correction": True,
                "headline": correction_headline,
                "table_markdown": "\n\n".join(p for p in (_clean_render(t) for t in tables) if p)[:6000] or None}
    fin_table = next((t for t in tables if "당기실적" in t.get_text()
                      and ("매출액" in t.get_text() or "영업수익" in t.get_text())), None)
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
            **_period_metadata(period), "kind": kind, "headline": headline,
            "table_markdown": table_markdown}


def _parse_structure_change(html: str) -> dict[str, Any]:
    """I001 「매출액 또는 손익구조 30% 이상 변경」 표를 같은 headline 계약으로 변환."""
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    um = re.search(r"단위\s*[:：]\s*([가-힣]+)", text)
    unit_label = um.group(1) if um else "천원"
    factor = _UNIT.get(unit_label, 1e3)
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
    for row in grid:
        labels = [(i, re.sub(r"^[-\s]+", "", re.sub(r"\s+", "", c or "")))
                  for i, c in enumerate(row)]
        hit = next(((i, _METRICS[label]) for i, label in labels if label in _METRICS), None)
        if not hit:
            continue
        idx, key = hit
        tail = row[idx + 1:]
        nums = [_num(c) for c in tail]
        numeric = [n for n in nums if n is not None]
        if not numeric:
            continue
        yoy = numeric[3] if len(numeric) >= 4 else None
        turn = next((c.strip() for c in tail if c.strip() in ("흑자전환", "적자전환")), None)
        headline[key] = {"value_krw": numeric[0] * factor,
                         "prior_value_krw": numeric[1] * factor if len(numeric) > 1 else None,
                         "change_krw": numeric[2] * factor if len(numeric) > 2 else None,
                         "yoy_pct": yoy, "turnover": turn}

    table_markdown = _clean_render(table)[:6000] if table is not None else None
    return {"consolidated": consolidated, "unit_raw": unit_label, "period": period,
            **_period_metadata(period, annual=True),
            "kind": "financial", "provisional_type": "fiscal_year_change",
            "headline": headline, "table_markdown": table_markdown}


async def _find_latest_provisional(client, corp_code: str, bgn_de: str, end_de: str) -> dict | None:
    """I002와 I001 실적공시를 함께 검색하되 제목으로 엄격히 필터링."""
    results = await asyncio.gather(
        client.search_filings(bgn_de=bgn_de, end_de=end_de, corp_code=corp_code,
                              pblntf_ty="I", pblntf_detail_ty="I002", page_count=40),
        client.search_filings(bgn_de=bgn_de, end_de=end_de, corp_code=corp_code,
                              pblntf_ty="I", pblntf_detail_ty="I001", page_count=40),
    )
    cands = []
    for r in results:
        for item in (r.get("list") or []):
            nm = item.get("report_nm", "")
            if _PROV_PAT.search(nm) or _is_structure_change_report(nm):
                cands.append(item)
    if not cands:
        return None
    cands.sort(key=lambda x: x.get("rcept_dt", ""), reverse=True)
    return cands[0]


async def build_provisional_earnings_payload(
    company_query: str, *, months: int = 6, start_date: str | None = None,
    end_date: str | None = None, format: str = "md",
) -> dict[str, Any]:
    """회사의 최신 영업(잠정)실적을 구조화 반환. screener는 start_date=end_date=filed_at(좁은창)로 호출."""
    from open_proxy_mcp.dart.client import get_dart_client, DartClientError
    res = await resolve_company_query(company_query)
    if not res.selected:
        return ToolEnvelope(tool="provisional_earnings", status=AnalysisStatus.AMBIGUOUS
                            if res.candidates else AnalysisStatus.ERROR,
                            subject=company_query,
                            data={"candidates": [c.get("corp_name") for c in (res.candidates or [])][:8]},
                            warnings=["회사 식별 실패"]).to_dict()
    corp = res.selected
    client = get_dart_client()
    bgn_de = start_date or (date.today() - timedelta(days=months * 31)).strftime("%Y%m%d")
    end_de = end_date or date.today().strftime("%Y%m%d")
    try:
        rept = await _find_latest_provisional(client, corp["corp_code"], bgn_de, end_de)
    except DartClientError as e:
        return ToolEnvelope(tool="provisional_earnings", status=AnalysisStatus.ERROR,
                            subject=corp.get("corp_name", ""),
                            warnings=[f"공시 검색 실패(DART {getattr(e, 'status', '?')})"]).to_dict()
    if not rept:
        return ToolEnvelope(tool="provisional_earnings", status=AnalysisStatus.NO_FILING,
                            subject=corp.get("corp_name", ""),
                            warnings=[f"최근 {months}개월 영업(잠정)실적 공시 없음"]).to_dict()
    url = f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={rept['rcept_no']}"
    try:
        doc = await client.get_document_cached(rept["rcept_no"])
        html = doc.get("html", "") if isinstance(doc, dict) else ""
        parsed = parse_provisional_earnings(html, rept.get("report_nm", ""))
    except Exception as e:
        return ToolEnvelope(tool="provisional_earnings", status=AnalysisStatus.ERROR,
                            subject=corp.get("corp_name", ""),
                            data={"report": {"rcept_no": rept["rcept_no"], "url": url}},
                            warnings=[f"원문 파싱 실패: {type(e).__name__}"]).to_dict()
    warnings = ["잠정치 — 향후 확정치와 다를 수 있음(감사 전)"]
    if parsed.get("kind") == "non_financial":
        warnings.append("표준 재무표(매출/영업이익) 미기재 — 도메인 실적표(지역별매출·판매대수·수주·판매량 등)로 공시, table_markdown 참조")
    data = {
        "company": {"name": corp.get("corp_name"), "corp_code": corp.get("corp_code"),
                    "stock_code": corp.get("stock_code")},
        "report": {"rcept_no": rept["rcept_no"], "report_nm": rept.get("report_nm"),
                   "rcept_dt": rept.get("rcept_dt"), "url": url},
        **parsed,
    }
    return ToolEnvelope(tool="provisional_earnings", status=AnalysisStatus.EXACT,
                        subject=corp.get("corp_name", ""), data=data, warnings=warnings).to_dict()
