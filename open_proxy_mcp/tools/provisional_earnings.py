"""provisional_earnings — 영업(잠정)실적(I002 공정공시) 파싱.

정기보고서 확정치(financial_metrics)보다 먼저 나오는 분기 잠정 실적. 공시검색+원문파싱.
설계: wiki/tools/provisional_earnings.md
"""
from __future__ import annotations

import re

from open_proxy_mcp.services.provisional_earnings import build_provisional_earnings_payload
from open_proxy_mcp.services.contracts import as_pretty_json

_LABEL = {"revenue": "매출액", "operating_profit": "영업이익", "pretax_profit": "법인세차감전이익",
          "net_income": "당기순이익", "net_income_controlling": "지배주주순이익", "capital_stock": "자본금"}


def _won(v):
    if v is None:
        return "-"
    a = abs(v)
    if a >= 1e12:
        return f"{v/1e12:,.2f}조"
    if a >= 1e8:
        return f"{v/1e8:,.0f}억"
    return f"{v:,.0f}"


def _render(p: dict) -> str:
    status = p.get("status")
    d = p.get("data", {}) or {}
    subj = p.get("subject", "")
    if status in ("error", "ambiguous"):
        cands = d.get("candidates")
        tail = f" (후보: {', '.join(cands)})" if cands else ""
        return f"**{subj}** — {'; '.join(p.get('warnings') or ['식별 실패'])}{tail}"
    if status == "no_filing":
        return f"**{subj}** — {'; '.join(p.get('warnings') or ['잠정실적 공시 없음'])}"
    rep = d.get("report", {})
    per = d.get("period") or {}
    label = "결산 잠정치" if d.get("provisional_type") == "fiscal_year_change" else "영업(잠정)실적"
    if d.get("correction"):
        label += " · 정정후"
    if d.get("fiscal_year"):
        if d.get("period_kind") == "annual":
            label = f"{d['fiscal_year']} 사업연도 {label}"
        elif d.get("period_kind") == "month" and d.get("period_month"):
            label = f"{d['fiscal_year']}년 {d['period_month']}월(월간) {label}"
        elif d.get("fiscal_quarter"):
            q = f"{d['fiscal_quarter']}분기" + (" 누적" if d.get("cumulative") else "")
            label = f"{d['fiscal_year']} 사업연도 {q} {label}"
    L = [f"## {subj} — {label}  ({rep.get('report_nm','')}, 공시 {rep.get('rcept_dt','')})"]
    basis = "연결" if d.get("consolidated") else "별도/개별"
    period_note = ""
    if d.get("fiscal_year_end_month"):
        src = "회사 등록" if d.get("fiscal_year_end_month_source") == "company" else "기본값·회사 정보 없음"
        period_note = f" · {d['fiscal_year_end_month']}월 결산({src})"
    # 공시일과 실적기간은 다른 정보다 — 둘을 한 줄에 나란히 적는다(260907)
    per_src = "(원문의 분기 표기에서 계산)" if per.get("source") == "quarter_text" else ""
    L.append(f"_{basis} · 공시일 {rep.get('rcept_dt','?')} · 실적기간 {per.get('start','?')}~{per.get('end','?')}{per_src}{period_note} · 단위원문 {d.get('unit_raw','')}_")

    # headline(best-effort): 재무형이면 매출·영업익·순익 당기+기간에 맞는 비교율
    head = d.get("headline") or {}
    comparison_basis = d.get("comparison_basis") or "전년동기 대비"
    if head:
        parts = []
        for key in ("revenue", "operating_profit", "pretax_profit", "net_income", "capital_stock"):
            m = head.get(key)
            if m and m.get("value_krw") is not None:
                # 원문에 증감비율 열이 없어 우리가 계산한 값은 그렇다고 밝힌다(재무현황 절).
                yoy_note = " · 계산값" if m.get("yoy_basis") == "computed" else ""
                yoy = f" ({comparison_basis} {m['yoy_pct']:+.1f}%{yoy_note})" if m.get("yoy_pct") is not None else ""
                prior = f" (직전 {_won(m['prior_value_krw'])})" if m.get("prior_value_krw") is not None and key == "capital_stock" else ""
                turn = f" · {m['turnover']}" if m.get('turnover') else ""
                parts.append(f"**{_LABEL[key]}** {_won(m['value_krw'])}{prior}{yoy}{turn}")
        if parts:
            L.append("\n" + " · ".join(parts))
    elif d.get("kind") == "non_financial":
        L.append("\n_표준 재무표(매출/영업이익) 미기재 — 도메인 실적표(지역별 매출·판매대수·수주·판매량 등)로 공시. 아래 원문표에서 읽으세요_")

    # table_markdown(primary): 원문 실적표 통째
    if d.get("table_markdown"):
        L.append("\n" + d["table_markdown"])
    L.append("\n_※ 잠정치 — 감사 전. 확정치·재무비율은 정기보고서(`financial_metrics`). 확정과 다를 수 있음._")
    if rep.get("url"):
        L.append(f"\n원문: {rep['url']}")
    if p.get("warnings"):
        L.append("\n⚠ " + " · ".join(p["warnings"]))
    return "\n".join(L)


def register_tools(mcp):

    @mcp.tool()
    async def provisional_earnings(company: str, format: str = "md", months: int = 6, start_date: str = "", end_date: str = "") -> str:
        """desc: DART 영업(잠정)실적(공정공시 I002)과 결산 잠정치(I001)에서 **잠정 매출·영업이익·순이익**과 회계연도 기준 비교율을 추출. 정기보고서 확정치보다 **먼저 나오는 가장 빠른 실적 신호**.
        when: 최신 분기 실적을 정기보고서(financial_metrics 확정치) 나오기 전에 볼 때. **잠정치**(감사 전)라 확정과 다를 수 있음 — 확정 재무비율은 `financial_metrics`.
        rule: 재무형(매출·영업이익 표)은 구조화 반환. 자동차 판매대수 등 **비재무형**은 raw 마크다운(kind=non_financial). 연결/별도 basis·실적기간·단위 명시. 값은 원문 그대로(원 단위 정규화), 잠정치.
        window: 기본 최근 `months`=6개월 안의 **가장 최근** 잠정실적 1건. 과거 분기를 보려면 `start_date`·`end_date`(YYYYMMDD)로 공시일 창을 좁힌다 — 예 2025년 3분기 잠정실적은 `start_date="20251001", end_date="20251115"`. 창 안에 여러 건이면 최신 1건.
        ref: financial_metrics, screener, price_multiple_data
        """
        for nm, v in (("start_date", start_date), ("end_date", end_date)):
            if v and not re.fullmatch(r"\d{8}", v):
                return f"{nm} 는 YYYYMMDD 8자리여야 합니다 (받은 값: {v})"
        payload = await build_provisional_earnings_payload(company, months=max(1, int(months or 6)),
                                                           start_date=start_date or None, end_date=end_date or None)
        if format == "json":
            return as_pretty_json(payload)
        return _render(payload)
