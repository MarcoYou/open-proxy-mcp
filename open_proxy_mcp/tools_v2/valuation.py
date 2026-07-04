"""v2 valuation public tool — DART(공시)+KRX(공식시세) 상대가치 배수 (기업·시장·산업 + 히스토리)."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.valuation import (
    build_valuation_payload,
    build_market_val_payload,
    build_sector_val_payload,
    build_firm_history_payload,
)

_STATUS_TITLE = {
    "invalid": "입력 오류",
    "not_found": "조회 결과 없음",
    "unlisted": "비상장 — 시장배수 산출 불가",
    "no_financials": "재무 데이터 미확정",
    "no_data": "스냅샷 데이터 없음",
    "db_error": "스냅샷 DB 연결 실패 (일시 장애)",
}


def _f(v, fmt="{:.2f}"):
    return fmt.format(v) if v is not None else "N/M"


def _render_status(payload: dict[str, Any]) -> str:
    """ok가 아닌 상태 렌더."""
    status = payload.get("status", "error")
    title = _STATUS_TITLE.get(status, status)
    lines = [f"# valuation: {payload.get('subject', '')} — {title}", ""]
    for w in payload.get("warnings", []):
        lines.append(f"- {w}")
    if not payload.get("warnings"):
        lines.append(f"- status=`{status}`")
    return "\n".join(lines)


def _render_market(p: dict[str, Any]) -> str:
    d = p["data"]
    lines = [f"# 시장 밸류에이션 — KOSPI·KOSDAQ (기준 {d['as_of']})", ""]
    lines.append("| 시장 | PER(FY0) | PER(TTM) | PBR(FY0) | PBR(MRQ) | Σ시총 |")
    lines.append("|---|---|---|---|---|---|")
    for h in d["latest"]:
        lines.append(f"| {h['mkt']} | {_f(h['per_fy0'])} | {_f(h['per_ttm'])} | "
                     f"{_f(h['pbr_fy0'])} | {_f(h['pbr_mrq'])} | {(h['cap_krw'] or 0)/1e12:,.0f}조 |")
    hist = d["history"]
    dds = sorted({h["snap_dd"] for h in hist})
    if len(dds) > 1:
        lines += ["", "## 주간 히스토리", "", "| 주(기준일) | KOSPI PER/PBR | KOSDAQ PER/PBR |", "|---|---|---|"]
        for dd in reversed(dds):
            by = {h["mkt"]: h for h in hist if h["snap_dd"] == dd}
            k, q = by.get("KOSPI", {}), by.get("KOSDAQ", {})
            lines.append(f"| {dd} | {_f(k.get('per_ttm'))} / {_f(k.get('pbr_mrq'))} "
                         f"| {_f(q.get('per_ttm'))} / {_f(q.get('pbr_mrq'))} |")
    lines += ["", f"> {d['method']}"]
    for w in p.get("warnings", []):
        lines.append(f"> {w}")
    return "\n".join(lines)


def _render_sector(p: dict[str, Any]) -> str:
    d = p["data"]
    lines = [f"# 산업별 밸류에이션 (기준 {d['as_of']})", ""]
    c = d.get("company")
    if c:
        lines += [f"**{c['name']}({c['isu_cd']})** → {c['sector_label']} [{c['mkt']}]",
                  f"- 기업 PER(TTM) {_f(c['firm_per_ttm'])} vs 섹터 {_f(c['sector_per_ttm'])} · "
                  f"기업 PBR {_f(c['firm_pbr_mrq'])} vs 섹터 {_f(c['sector_pbr_mrq'])}", ""]
    for mkt in ("KOSPI", "KOSDAQ"):
        rows = [s for s in d["sectors"] if s["mkt"] == mkt]
        if not rows:
            continue
        lines += [f"## {mkt}", "", "| 섹터 | 사수 | PER(TTM) | PBR(MRQ) | Σ시총 |", "|---|---|---|---|---|"]
        for s in rows:
            lines.append(f"| {s['label']} | {s['n']} | {_f(s['per_ttm'])} | {_f(s['pbr_mrq'])} "
                         f"| {(s['cap_krw'] or 0)/1e12:,.1f}조 |")
        lines.append("")
    for w in p.get("warnings", []):
        lines.append(f"> {w}")
    return "\n".join(lines)


def _render_firm_history(p: dict[str, Any]) -> str:
    d = p["data"]
    lines = [f"# {p['subject']} 밸류에이션 히스토리 ({d['mkt']} · 섹터 {d['sector']})", "",
             "| 주(기준일) | PER(FY0) | PER(TTM) | PBR(MRQ) | 시총 |", "|---|---|---|---|---|"]
    for h in reversed(d["history"]):
        lines.append(f"| {h['snap_dd']} | {_f(h['per_fy0'])} | {_f(h['per_ttm'])} "
                     f"| {_f(h['pbr_mrq'])} | {(h['cap_krw'] or 0)/1e12:,.2f}조 |")
    lines += ["", f"> {d['method']}"]
    for w in p.get("warnings", []):
        lines.append(f"> {w}")
    return "\n".join(lines)


def register_tools(mcp):

    @mcp.tool()
    async def valuation(company: str = "", scope: str = "firm", format: str = "md") -> str:
        """desc: 상대가치 밸류에이션 — 기업 심층(PER·PBR·배당수익률) + 시장 전체·산업별·종목 히스토리(주간 스냅샷). 한국 표준(연결, 지배주주 귀속). 비KRW 기능통화 자동 KRW 환산(ECOS), 스케일가드, N/M 게이팅.
        when: "PER/PBR 얼마"·"싼가 비싼가"(scope=firm) / "코스피·코스닥 전체 밸류"(market) / "업종별 PER·PBR"·"섹터 대비 어디"(sector, company 지정 시 소속 섹터 비교) / "밸류 추이"(firm_history). 재무 펀더멘탈 자체는 financial_metrics, 배당 상세는 dividend.
        rule: scope=firm(기본, company 필수) = 실시간 DART 재무 × krx_weekly 시세 — EPS(FY0)=공시 기본주당이익(가중평균, 없으면 지배순이익÷보통주 폴백), EPS(TTM)=TTM 지배순이익÷보통주, BPS=지배자본(MRQ)÷합계주식수, 분모≤0·완전자본잠식=N/M. scope=market/sector/firm_history = Supabase 주간 스냅샷(mkt_val_history·mkt_sector_val·mkt_valuation, market_val_weekly 배치가 갱신) — PER=Σ시총÷Σ지배순이익(시총가중 조화평균·우선주 시총 보통주 귀속), 시총 기반이라 수정주가 조정 불변. 섹터 분류=KSIC 하이브리드. firm과 스냅샷 방법론 차이(보통주 주가 vs 총시총) 有 — 각 출력에 명시. 값 raw KRW int(_krw), % float(_pct).
        status: ok / invalid / not_found(우선주는 보통주 코드로) / unlisted / no_financials / no_data(배치 미실행).
        note: lean v1 — RIM·EV/EBITDA·PSR·FCF·5년밴드·PIT·주당 수정주가 시계열은 v1.1.
        ref: financial_metrics, dividend, corp_gov_report, evidence
        """
        sc = (scope or "firm").strip().lower()
        if sc == "market":
            payload = await build_market_val_payload(format=format)
        elif sc == "sector":
            payload = await build_sector_val_payload(company, format=format)
        elif sc in ("firm_history", "history"):
            payload = await build_firm_history_payload(company, format=format)
        elif sc == "firm":
            payload = await build_valuation_payload(company, format=format)
        else:  # 오타("markets" 등)를 조용히 firm으로 보내면 의도 밖 DART 콜 — 명시 거절(QA)
            payload = {"tool": "valuation", "status": "invalid", "subject": scope,
                       "warnings": [f"scope '{scope}' 없음 — firm / market / sector / firm_history 중 선택."]}
        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") != "ok":
            return _render_status(payload)
        scope_out = payload.get("data", {}).get("scope")
        if scope_out == "market":
            return _render_market(payload)
        if scope_out == "sector":
            return _render_sector(payload)
        if scope_out == "firm_history":
            return _render_firm_history(payload)
        return payload.get("markdown") or _render_status(payload)
