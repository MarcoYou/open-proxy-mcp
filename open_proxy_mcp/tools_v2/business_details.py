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
            md = seg.get("segment_note_md")
            if md:
                L.append(f"\n### 사업부문 원문 ({seg.get('region','영업부문 주석')}) — 아래에서 직접 추출")
                L.append(f"> {seg.get('note','')}")
                L.append("\n" + md)
            else:
                L.append(f"\n### 사업부문 표 후보 (정형 저신뢰 → 아래 원문 표에서 직접 추출)")
                L.append(f"> {seg.get('note','')}")
                for i, c in enumerate(seg.get("candidates", []), 1):
                    L.append(f"\n**[후보 {i}]** (score {c.get('score')}, {c.get('rows')}×{c.get('cols')})\n```\n{c.get('rendered','')[:2500]}\n```")
        elif st == "UNSUPPORTED_FORM":
            L.append(f"\n### 사업부문별 이익: **미지원 폼** — {seg.get('na_reason','')}")
        else:
            L.append(f"\n### 사업부문별 이익: 해당없음 — {seg.get('na_reason','')}")

    # 추가 필드(markdown-primary): 소절 원문 마크다운 → 읽어서 추출. hint는 참고용.
    _FIELD_LABEL = {"sites": "사업장·생산설비", "utilization": "생산실적·가동률",
                    "rnd": "연구개발", "backlog": "수주현황", "customers": "주요 고객·매출처",
                    "financial_ops": "영업의 현황(금융)", "financial_soundness": "재무건전성(금융)",
                    "investment_property": "투자부동산(REIT/보험)"}
    for key in ("sites", "utilization", "rnd", "backlog", "customers",
                "financial_ops", "financial_soundness", "investment_property"):
        fd = d.get(key)
        if not fd:
            continue
        label = _FIELD_LABEL[key]
        if fd.get("status") == "MARKDOWN":
            hint = ""
            if fd.get("pct_hint"):
                hint = f" _(가동률 힌트 {', '.join(fd['pct_hint'])}% · 비교금지)_"
            elif fd.get("ratio_to_sales_pct_hint"):
                hint = f" _(연구개발비/매출 힌트 {fd['ratio_to_sales_pct_hint']}%)_"
            L.append(f"\n### {label} (원문){hint}")
            L.append("> 아래 원문에서 값을 읽으세요. 단위·정의는 회사별 상이(비교 주의).")
            L.append("\n" + fd["markdown"])
        else:
            L.append(f"\n**{label}**: 해당없음 — {fd.get('na_reason','')}")

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
        """desc: DART 사업보고서 **"II. 사업의 내용"**에서 사업부문별 매출·영업이익, **사업장·생산설비, 생산실적·가동률, 연구개발, 수주현황, 주요 고객·매출처**를 추출. SOTP·부문 수익성·생산능력·수주·고객집중 분석의 1차 소스.
        when: 회사의 사업부문·생산·수주·고객 구조가 필요할 때. 전사 재무는 `financial_metrics`, 밸류는 `valuation`. **금융/증권/보험/지주는 `financial_ops`·`financial_soundness`, REIT/보험은 `investment_property`** 로 커버(segments 대신).
        rule: segments는 정형→저신뢰 시 원문 마크다운. 나머지 필드는 **해당 소절 원문을 마크다운으로 반환** — 그 표를 읽어 값 추출(단위·정의 회사별 상이, 비교 주의). 금융/REIT 필드는 표준사에선 자동 N/A. 유형자산 장부가 표를 사업장으로 오독 금지.
        period: `annual`(기본) / `quarterly`
        fields: 쉼표구분 — 표준: `segments,sites,utilization,rnd,backlog,customers` / 금융·REIT: `financial_ops,financial_soundness,investment_property` (미지정 시 회사에 맞는 것만 렌더, 나머지 N/A).
        ref: financial_metrics, valuation, order_contracts, company
        """
        flist = [f.strip() for f in fields.split(",") if f.strip()] or None
        payload = await build_business_details_payload(company, period=period, fields=flist)
        if format == "json":
            return as_pretty_json(payload)
        return _render(payload)
