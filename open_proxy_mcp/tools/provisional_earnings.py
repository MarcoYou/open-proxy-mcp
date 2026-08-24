"""provisional_earnings — 영업(잠정)실적(I002 공정공시) 파싱.

정기보고서 확정치(financial_metrics)보다 먼저 나오는 분기 잠정 실적. 공시검색+원문파싱.
설계: wiki/tools/provisional_earnings.md
"""
from __future__ import annotations

from open_proxy_mcp.services.provisional_earnings import build_provisional_earnings_payload
from open_proxy_mcp.services.contracts import as_pretty_json

_LABEL = {"revenue": "매출액", "operating_profit": "영업이익", "pretax_profit": "법인세차감전이익",
          "net_income": "당기순이익", "net_income_controlling": "지배주주순이익"}


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
    if d.get("fiscal_year"):
        if d.get("period_kind") == "annual":
            label = f"{d['fiscal_year']} 사업연도 {label}"
        elif d.get("fiscal_quarter"):
            label = f"{d['fiscal_year']} 사업연도 {d['fiscal_quarter']}분기 {label}"
    L = [f"## {subj} — {label}  ({rep.get('report_nm','')}, 공시 {rep.get('rcept_dt','')})"]
    basis = "연결" if d.get("consolidated") else "별도/개별"
    period_note = ""
    if d.get("fiscal_year_end_month"):
        period_note = f" · {d['fiscal_year_end_month']}월 결산"
    L.append(f"_{basis} · 실적기간 {per.get('start','?')}~{per.get('end','?')}{period_note} · 단위원문 {d.get('unit_raw','')}_")

    # headline(best-effort): 재무형이면 매출·영업익·순익 당기+기간에 맞는 비교율
    head = d.get("headline") or {}
    comparison_basis = d.get("comparison_basis") or "전년동기 대비"
    if head:
        parts = []
        for key in ("revenue", "operating_profit", "net_income"):
            m = head.get(key)
            if m and m.get("value_krw") is not None:
                yoy = f" ({comparison_basis} {m['yoy_pct']:+.1f}%)" if m.get("yoy_pct") is not None else ""
                turn = f" · {m['turnover']}" if m.get('turnover') else ""
                parts.append(f"**{_LABEL[key]}** {_won(m['value_krw'])}{yoy}{turn}")
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
    async def provisional_earnings(company: str, format: str = "md") -> str:
        """desc: DART 영업(잠정)실적(공정공시 I002)과 결산 잠정치(I001)에서 **잠정 매출·영업이익·순이익**과 회계연도 기준 비교율을 추출. 정기보고서 확정치보다 **먼저 나오는 가장 빠른 실적 신호**.
        when: 최신 분기 실적을 정기보고서(financial_metrics 확정치) 나오기 전에 볼 때. **잠정치**(감사 전)라 확정과 다를 수 있음 — 확정 재무비율은 `financial_metrics`.
        rule: 재무형(매출·영업이익 표)은 구조화 반환. 자동차 판매대수 등 **비재무형**은 raw 마크다운(kind=non_financial). 연결/별도 basis·실적기간·단위 명시. 값은 원문 그대로(원 단위 정규화), 잠정치.
        ref: financial_metrics, screener, valuation
        """
        payload = await build_provisional_earnings_payload(company)
        if format == "json":
            return as_pretty_json(payload)
        return _render(payload)
