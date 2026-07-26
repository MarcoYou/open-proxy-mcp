"""business_details — DART "II. 사업의 내용" 사업부문 데이터 추출.

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
            L.append(f"\n### 사업부문별 이익: 이 업종은 다른 tool에서 제공 — {seg.get('na_reason','')}")
        else:
            # '실패'라는 말은 쓰지 않는다 — 대부분은 오류가 아니라 '공시에 없거나 읽을 수 없는 형태'다.
            _HEAD = {"NOT_APPLICABLE": "해당 없음", "NOT_COLLECTED": "공시에 미기재"}
            L.append(f"\n### 사업부문별 이익: {_HEAD.get(st, '확인 불가')} — {seg.get('na_reason','')}")

    # 추가 필드(markdown-primary): 소절 원문 마크다운 → 읽어서 추출. hint는 참고용.
    _FIELD_LABEL = {"sites": "사업장·생산설비", "utilization": "생산실적·가동률",
                    "rnd": "연구개발", "backlog": "수주현황", "customers": "주요 고객·매출처",
                    "raw_materials": "원재료·투입원가", "product_pricing": "제품·서비스 가격 추이",
                    "financial_ops": "영업의 현황(금융)", "financial_soundness": "재무건전성(금융)",
                    "investment_property": "투자부동산(REIT/보험)"}
    for key in ("sites", "utilization", "rnd", "backlog", "customers",
                "raw_materials", "product_pricing",
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
            elif fd.get("note"):
                hint = f" _({fd['note']})_"
            L.append(f"\n### {label} (원문){hint}")
            L.append("> 아래 원문에서 값을 읽으세요. 단위·정의는 회사별 상이(비교 주의).")
            L.append("\n" + fd["markdown"])
        elif fd.get("extraction_status") == "NOT_COLLECTED":
            L.append(f"\n**{label}**: 확인하지 못함 — {fd.get('na_reason','해당 소절 미검출')}")
        else:
            L.append(f"\n**{label}**: 해당없음 — {fd.get('na_reason','')}")

    candidate = d.get("candidate_context")
    if candidate:
        if candidate.get("status") == "LOW_CONFIDENCE":
            L.append(f"\n### 저신뢰 보조 문맥 — {candidate.get('field','')}")
            L.append(f"> {candidate.get('warning','')}")
            L.append(f"> 앵커: {candidate.get('anchor','')} · 고정 문맥: {candidate.get('context_chars','')}자")
            L.append("\n" + candidate.get("markdown", ""))
        elif candidate.get("status") == "NOT_FOUND":
            L.append(f"\n**저신뢰 보조 문맥**: 찾지 못함 — {candidate.get('warning','')}")

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
        period: str = "latest",
        fields: str = "",
        format: str = "md",
        bsns_year: str = "",
        reprt_code: str = "",
        context_mode: str = "strict",
        context_chars: int = 20000,
    ) -> str:
        """desc: DART 정기보고서 **"II. 사업의 내용"**에서 사업부문별 매출·영업이익, **사업장·생산설비, 생산실적·가동률, 연구개발, 수주현황, 주요 고객·매출처, 원재료·투입원가, 제품·서비스 가격 추이**를 추출. SOTP·부문 수익성·생산능력·수주·고객집중·마진 분석의 1차 소스.
        when: 회사의 사업부문·생산·수주·고객 구조가 필요할 때. 전사 재무는 `financial_metrics`, 밸류는 `valuation`. **금융/증권/보험/지주는 `financial_ops`·`financial_soundness`, REIT/보험은 `investment_property`** 로 커버(segments 대신). **여러 분기/연도 추이**가 필요하면 `bsns_year`+`reprt_code`를 지정해 과거 시점을 하나씩 반복 호출.
        rule: segments는 정형→저신뢰 시 원문 마크다운. 나머지 필드는 **해당 소절 원문을 마크다운으로 반환** — 그 표를 읽어 값 추출(단위·정의 회사별 상이, 비교 주의). `context_mode=candidate`는 strict가 `NOT_COLLECTED`일 때만 **저신뢰 고정 윈도우 문맥**을 별도 `candidate_context`로 반환하며, 공식 결과·hint로 사용하면 안 됨. 이 모드는 표준 필드 하나를 지정할 때만 사용. 금융/REIT 필드는 표준사에선 자동 N/A. 유형자산 장부가 표를 사업장으로 오독 금지. **응답 `report.report_nm`으로 어느 보고서인지 확인**(분기/반기/사업). `bsns_year`/`reprt_code`는 **반드시 둘 다** 지정(하나만 주면 에러) — 지정 시 `period`는 무시됨.
        period: `latest`(기본, 사업·반기·분기 중 **가장 최신 제출분**=최신 데이터) / `annual`(연간 사업보고서 고정) / `quarterly`(분기·반기 고정). II.사업의내용은 분기/반기도 완전구조라 동일 필드. `bsns_year`+`reprt_code` 지정 시 이 파라미터는 무시.
        fields: 쉼표구분 — 표준: `segments,sites,utilization,rnd,backlog,customers,raw_materials,product_pricing` / 금융·REIT: `financial_ops,financial_soundness,investment_property`. `raw_materials`는 원재료 구성·매입과 원재료 가격 추이를 별도 소절로 반환하고, `product_pricing`은 판매가격·ASP·가격변동 원인을 반환. (미지정 시 회사에 맞는 표준·금융 필드만). **자산(토지·투자부동산·지분증권 원가vs공정가치)은 별도 tool `asset_holdings`.**
        bsns_year: 특정 과거 사업연도 조회(예: "2025"). `reprt_code`와 함께 지정해야 함 — **추이 조회용**(한 번에 여러 분기 반환 아님, 분기마다 반복 호출).
        reprt_code: DART 표준 보고서유형 — `11011`(사업/연간) `11012`(반기) `11013`(1분기) `11014`(3분기). `bsns_year`와 함께 지정.
        context_mode: `strict`(기본) / `candidate`. candidate는 strict `NOT_COLLECTED`일 때만 단일 표준 필드의 저신뢰 보조 문맥을 별도 반환.
        context_chars: candidate 고정 문맥 길이(기본 20000, 최대 60000). strict에서는 사용하지 않음.
        ref: financial_metrics, valuation, order_contracts, company
        """
        flist = [f.strip() for f in fields.split(",") if f.strip()] or None
        payload = await build_business_details_payload(
            company, period=period, fields=flist, bsns_year=bsns_year, reprt_code=reprt_code,
            context_mode=context_mode, context_chars=context_chars,
        )
        if format == "json":
            return as_pretty_json(payload)
        return _render(payload)
