"""business_details — DART "II. 사업의 내용" 사업부문 데이터 추출 (21번째 tool).

정형 파서 primary → 저신뢰 시 부문표 후보 raw 반환(호출측 LLM 추출) → N/A. 내부 LLM/pandas 없음.
설계: wiki/decisions/260717_1220_decision_business-content-tool-roadmap.md
"""
from __future__ import annotations

from open_proxy_mcp.services.business_details import build_business_details_payload
from open_proxy_mcp.services.contracts import as_pretty_json


def _fmt(v):
    if v is None:
        return "-"
    try:
        return f"{v:,.0f}"
    except (TypeError, ValueError):
        return str(v)


def _render(p: dict) -> str:
    status = p.get("status")
    d = p.get("data", {}) or {}
    subj = p.get("subject", "")
    if status in ("error", "ambiguous"):
        return f"**{subj}** — {'; '.join(p.get('warnings') or ['회사 식별 실패'])}"
    if status == "no_filing":
        return f"**{subj}** — {'; '.join(p.get('warnings') or ['정기보고서 없음'])}"
    L = []
    rep = d.get("report", {})
    L.append(f"## {subj} — 사업부문 상세  ({rep.get('report_nm','')}, {d.get('form_type','')})")

    seg = d.get("segments")
    if seg:
        st = seg.get("status")
        if st == "OK":
            L.append(f"\n### 사업부문별 매출·이익  (단위: {seg.get('unit','')}, 출처: 정형파싱)")
            L.append("| 부문 | 매출 | 영업이익 |")
            L.append("|---|--:|--:|")
            for s in seg.get("items", []):
                L.append(f"| {s.get('name','')} | {_fmt(s.get('revenue'))} | {_fmt(s.get('profit'))} |")
            L.append(f"\n_{seg.get('reconciliation','')}_  (지표: {seg.get('revenue_metric','')}/{seg.get('profit_metric','')})")
        elif st == "NEEDS_REVIEW":
            L.append(f"\n### 사업부문 표 후보 (정형 저신뢰 → 아래 원문 표에서 직접 추출)")
            L.append(f"> {seg.get('note','')}")
            for i, c in enumerate(seg.get("candidates", []), 1):
                L.append(f"\n**[후보 {i}]** (score {c.get('score')}, {c.get('rows')}×{c.get('cols')})\n```\n{c.get('rendered','')[:2500]}\n```")
        elif st == "UNSUPPORTED_FORM":
            L.append(f"\n### 사업부문별 이익: **미지원 폼** — {seg.get('na_reason','')}")
        else:
            L.append(f"\n### 사업부문별 이익: 해당없음 — {seg.get('na_reason','')}")

    rnd = d.get("rnd")
    if rnd and rnd.get("status") == "OK":
        ratio = rnd.get("ratio_to_sales_pct")
        ratio_str = f" (매출대비 {ratio}%)" if ratio is not None else ""
        L.append(f"\n**연구개발비**: {_fmt(rnd.get('amount'))} {rnd.get('unit', '') or ''}{ratio_str}")
    bl = d.get("backlog")
    if bl and bl.get("status") == "OK":
        L.append(f"**수주잔고**: {'; '.join(_fmt(x) for x in (bl.get('values') or [])) or bl.get('note','있음')} {bl.get('unit','') or ''}")
    cc = d.get("customers")
    if cc and cc.get("status") == "OK":
        L.append(f"**주요 고객집중** (매출 10%↑): " + ", ".join(f"{c.get('customer')}={_fmt(c.get('revenue'))}" for c in cc.get("customers", [])))

    tm = d.get("timings_ms", {})
    if tm:
        L.append(f"\n_조회 {tm.get('total','?')}ms · 주석fetch={d.get('note_fetched')}_")
    if p.get("warnings"):
        L.append("\n⚠ " + " · ".join(p["warnings"]))
    return "\n".join(L)


def register_tools(mcp):

    @mcp.tool()
    async def business_details(
        company: str,
        period: str = "annual",
        fields: str = "",
        format: str = "md",
    ) -> str:
        """desc: DART 사업보고서 **"II. 사업의 내용"**에서 사업부문별 매출·영업이익·비중, 연구개발비, 수주잔고, 주요고객 집중도를 구조화 추출. SOTP·부문 수익성·적자부문 분석의 1차 소스.
        when: 회사의 사업부문별 실적·구조가 필요할 때. 전사 재무는 `financial_metrics`, 밸류는 `valuation`. 금융지주·REIT는 v1 미지원(폼 다름).
        rule: 정형 파싱 우선 → 저신뢰 시 부문표 원문 후보를 반환하니 그 표를 읽어 부문값 추출(부문합계/조정/총계 열 제외). 단일부문·금융폼은 해당없음.
        period: `annual`(기본) / `quarterly`
        fields: 쉼표구분 선택 — `segments,rnd,backlog,customers` (미지정 시 전체). segments만 필요하면 `segments`로 지정하면 주석 fetch 생략돼 빠름.
        ref: financial_metrics, valuation, order_contracts, company
        """
        flist = [f.strip() for f in fields.split(",") if f.strip()] or None
        payload = await build_business_details_payload(company, period=period, fields=flist)
        if format == "json":
            return as_pretty_json(payload)
        return _render(payload)
