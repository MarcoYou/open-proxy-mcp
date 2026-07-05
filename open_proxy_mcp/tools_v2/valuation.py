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
    "ambiguous": "회사 식별 모호 — 후보에서 선택",
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
    cands = (payload.get("data") or {}).get("candidates") or []
    if cands:  # ambiguous — company 툴과 동일한 후보표
        lines += ["", "| 회사명 | ticker | corp_code |", "|---|---|---|"]
        for c in cands:
            lines.append(f"| {c.get('corp_name')} | `{c.get('stock_code') or '-'}` | `{c.get('corp_code')}` |")
    if not payload.get("warnings") and not cands:
        lines.append(f"- status=`{status}`")
    return "\n".join(lines)


def _render_market(p: dict[str, Any]) -> str:
    d = p["data"]
    lines = [f"# 시장 밸류에이션 — KOSPI·KOSDAQ (기준 {d['as_of']})", ""]
    lines.append("| 시장 | PER(FY0) | PER(TTM) | PBR(FY0) | PBR(MRQ) | Σ시총(보통주) | Σ우선주 |")
    lines.append("|---|---|---|---|---|---|---|")
    for h in d["latest"]:
        lines.append(f"| {h['mkt']} | {_f(h['per_fy0'])} | {_f(h['per_ttm'])} | "
                     f"{_f(h['pbr_fy0'])} | {_f(h['pbr_mrq'])} | {(h['cap_krw'] or 0)/1e12:,.0f}조 "
                     f"| {(h.get('cap_pref_krw') or 0)/1e12:,.1f}조 |")
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
        # company 지정 시 소속 시장의 상위 10 + 소속 섹터만 — 전체 100행 덤프 방지(실사용 QA P1)
        if c:
            if mkt != c["mkt"]:
                continue
            top = rows[:10]
            if not any(s["sector"] == c["sector"] for s in top):
                top += [s for s in rows if s["sector"] == c["sector"]]
            rows = top
        lines += [f"## {mkt}" + (" (시총 상위 10 + 소속 섹터 — 전체 표는 company 없이)" if c else ""),
                  "", "| 섹터 | 종목수 | PER(TTM) | PBR(MRQ) | Σ시총 |", "|---|---|---|---|---|"]
        for s in rows:
            mark = " ◀" if c and s["sector"] == c["sector"] else ""
            lines.append(f"| {s['label']}{mark} | {s['n']} | {_f(s['per_ttm'])} | {_f(s['pbr_mrq'])} "
                         f"| {(s['cap_krw'] or 0)/1e12:,.1f}조 |")
        lines.append("")
    lines.append("> PER N/M = 섹터 합산 지배순이익≤0(적자 우세) — 그 경우 PBR로 비교.")
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


_METHODOLOGY = """# valuation 방법론·기준·출처 (수치 근거)

## 산식 (firm — 기업 심층)
| 지표 | 산식 | 기준 |
|---|---|---|
| EPS(FY0) | DART **공시 기본주당이익** (가중평균 주식수·우선주 배분 반영) | 계속+중단영업 분리 공시는 합산, 결측 시 지배순이익÷보통주 폴백 |
| EPS(TTM) | **공시 EPS 조립** = FY0 EPS + 당해 분기누적 EPS − 전년동기누적 EPS | FY0과 같은 공시 기준(대칭). 기중 액면분할·무상증자·주식배당은 수정계수(krx_adj_factor_v3)로 각 조각을 현재 기준 정렬 |
| BPS | 지배자본(최근분기 MRQ, 부재 시 FY말) ÷ 합계 유통주식수(보통+우선, 자기주식 제외) | 지배주주 귀속 |
| PER | 보통주 종가 ÷ EPS | FY0·TTM 각각 (⚠ 분모 기준이 달라 직접 비교 주의) |
| PBR | 보통주 종가 ÷ BPS | MRQ |
| 배당수익률 | 주당 현금배당(DPS) ÷ 종가 × 100 | 보통주 결의 기준 |

## 산식 (market/sector/firm_history — 주간 스냅샷)
- PER = **Σ보통주 시총 ÷ Σ지배순이익** (시총가중 조화평균, KRX 지수 PER 관행) · PBR = Σ보통주 시총 ÷ Σ지배자본(MRQ)
- Σ지배순이익에 **적자기업 포함**(흑자만 쓰는 일부 벤더와 상이) — 적자 우세 시장(KOSDAQ)의 PER이
  크게 높아짐. PBR 병행 해석 권장. trailing(과거 실적) 기준 — 컨센서스 선행 PER와 다름
- **우선주 시총은 배수에서 제외**(cap_pref로 별도 노출) — 분모의 이익·자본엔 우선주 몫이 포함되어
  배수는 소폭 하향 편향(클래스별 이익·자본 분리는 공시 부재로 불가, KRX 공표 PER도 동일 관행)
- firm(보통주 주가÷공시 EPS)과는 분모 기준(전체 지배이익 vs 주당 가중평균)이 달라 값이 다를 수 있음
- 섹터 분류 = KSIC 하이브리드(자체 매핑) · 소규모(5사 미만) 섹터는 '기타(소규모)'로 합산

## 판단 기준 (게이팅)
- **N/M**: EPS·BPS 분모≤0(적자·자본잠식) 또는 완전자본잠식 → 배수 미산출(음수 PER 금지)
- **지배주주 귀속**: 순이익·자본 모두 지배지분 기준(비지배 NCI 제외) — 지주사 과대평가 방지
- **비KRW 기능통화**(두산밥캣 USD 등 22사): 회계기말 환율(한국은행 ECOS 매매기준율)로 KRW 환산
- **스케일가드**: 재무 단위오류(예: 100만배) 의심 시 개별조회는 값 유지+강한 경고, 시장 집계는 제외
- **수정주가**: PER/PBR/시총 시계열은 시총 기반이라 분할·무상증자에 불변(계수 불요). 유증·소각·
  분할의 시총 점프는 실제 이벤트라 보존

## 데이터 출처·갱신 주기
| 데이터 | 출처 | 갱신 |
|---|---|---|
| 재무(순이익·자본·주식수·배당) | DART OpenAPI (전자공시 원문) | firm=실시간 / 스냅샷 원천=분기 배치 |
| 주가·시총 | KRX 정보데이터시스템 → Supabase krx_weekly | 매일 수집(전일 종가), 주 마지막 거래일 보존 |
| 환율 | 한국은행 ECOS 매매기준율(공식) | 회계기말 고정값 캐시 |
| 주간 스냅샷(시장·섹터·종목 히스토리) | 위 조합 재계산 | 매일 배치(주간 수렴) |

특정 종목의 실제 대입 계산은 `valuation(company="종목", scope="explain")`."""


def _render_explain_firm(p: dict[str, Any]) -> str:
    """종목별 수치 근거 — 실제 값 대입 계산 과정."""
    d = p["data"]; i = d["inputs"]; m = d["multiples"]
    price, pdate = d.get("price_krw"), d.get("price_date")
    fx, cur = i.get("fx_rate_to_krw"), i.get("functional_currency", "KRW")
    L = [f"# {p['subject']} 수치 근거 (계산 과정)", "",
         f"## 인풋과 출처",
         f"- 주가: **{price:,}원** ({pdate} 종가 — KRX 일별시세, Supabase krx_weekly 서빙)",
         f"- 지배순이익 FY0: {i['net_income_fy0_krw']:,}원 / TTM: "
         f"{i['net_income_ttm_krw']:,}원 (DART 재무제표 원문, 지배주주 귀속 계정)"
         if i.get("net_income_fy0_krw") is not None and i.get("net_income_ttm_krw") is not None else
         f"- 지배순이익: FY0={i.get('net_income_fy0_krw')} / TTM={i.get('net_income_ttm_krw')} (일부 미확정)",
         f"- 지배자본(MRQ 우선): {i['controlling_equity_krw']:,}원"
         if i.get("controlling_equity_krw") is not None else "- 지배자본: 미확정",
         f"- 유통주식수(자기주식 제외 — DART stockTotqySttus): 보통주 {i.get('shares_common') and format(i['shares_common'], ',')}"
         f" / 합계(보통+우선) {i.get('shares_total') and format(i['shares_total'], ',')}",
         f"- DPS(보통주 현금배당 — DART alotMatter): {i.get('dps_krw') and format(i['dps_krw'], ',')}원"]
    if fx:
        L.append(f"- ⚠ 기능통화 {cur} — 위 재무는 회계기말 환율 {fx:,.1f}원/{cur}(한국은행 ECOS)로 KRW 환산된 값")
    L += ["", "## 계산 과정"]
    def _calc(lbl, formula, num, den, out, unit=""):
        if num is not None and den:
            L.append(f"- {lbl} = {formula} = {num:,} ÷ {den:,} = **{out}{unit}**")
        else:
            L.append(f"- {lbl} = {formula} → **N/M** (분모≤0·적자·자본잠식 또는 데이터 미확정)")
    L.append(f"- EPS(FY0) = 공시 기본주당이익(가중평균 주식수 반영) = **{i.get('eps_fy0_krw') and format(i['eps_fy0_krw'], ',')}원**"
             " (부재 시 지배순이익÷보통주 폴백)")
    if i.get("eps_ttm_basis") == "disclosed_assembled":
        L.append(f"- EPS(TTM) = **공시 EPS 조립**(FY0 EPS + 당해 분기누적 EPS − 전년동기누적 EPS) = "
                 f"**{i.get('eps_ttm_krw') and format(i['eps_ttm_krw'], ',')}원** — FY0과 같은 공시 가중평균 기준(대칭)")
        adj = i.get("eps_adj_factors")
        if adj:
            parts = []
            if adj.get("current") != 1.0:
                parts.append(f"연간·당해분기 EPS ×{adj['current']:g}")
            if adj.get("prior_q") != 1.0:
                parts.append(f"전년동기 EPS ×{adj['prior_q']:g}")
            L.append(f"  - **수정계수 보정 적용**: {' · '.join(parts)} — 기중 액면분할·무상증자·주식배당으로 "
                     "옛 분모 기준인 공시 EPS를 현재 기준으로 정렬 (krx_adj_factor_v3, 거래소 기준가 리셋 실측)")
    elif i.get("net_income_ttm_krw") is not None and i.get("shares_common"):
        L.append(f"- EPS(TTM) = 폴백: TTM 지배순이익 ÷ 보통주 = {i['net_income_ttm_krw']:,} ÷ "
                 f"{i['shares_common']:,} = **{i.get('eps_ttm_krw') and format(i['eps_ttm_krw'], ',')}원**"
                 "  (공시 EPS 결측 — FY0과 기준 다름 주의)")
    if i.get("controlling_equity_krw") is not None and i.get("shares_total"):
        L.append(f"- BPS = 지배자본(MRQ) ÷ 합계주식수 = {i['controlling_equity_krw']:,} ÷ "
                 f"{i['shares_total']:,} = **{i.get('bps_krw') and format(i['bps_krw'], ',')}원**")
    _calc("PER(FY0)", "주가 ÷ EPS(FY0)", price, i.get("eps_fy0_krw"), m.get("per_fy0"))
    _calc("PER(TTM)", "주가 ÷ EPS(TTM)", price, i.get("eps_ttm_krw"), m.get("per_ttm"))
    _calc("PBR(MRQ)", "주가 ÷ BPS", price, i.get("bps_krw"), m.get("pbr_mrq"))
    if i.get("dps_krw") and price:
        L.append(f"- 배당수익률 = DPS ÷ 주가 = {i['dps_krw']:,} ÷ {price:,} = **{m.get('dividend_yield_pct')}%**")
    dq = d.get("data_quality") or {}
    L += ["", "## 신뢰도",
          f"- 스케일가드: {dq.get('scale_tier', '-')} (재무 단위오류 검사 — 항등식·시장최댓값 기준)",
          f"- 자본잠식 상태: {i.get('capital_impairment_status', '-')}"]
    if p.get("data", {}).get("warnings") or p.get("warnings"):
        L += ["", "## 유의(원문 경고)"]
        for w in (d.get("warnings") or p.get("warnings") or []):
            L.append(f"- {w}")
    L += ["", "> 방법론·기준 전문: `valuation(scope=\"explain\")` (company 없이)."]
    return "\n".join(L)


def register_tools(mcp):

    @mcp.tool()
    async def valuation(company: str = "", scope: str = "firm", format: str = "md") -> str:
        """desc: 상대가치 밸류에이션 — 기업 심층(PER·PBR·배당수익률) + 시장 전체·산업별·종목 히스토리(주간 스냅샷). 한국 표준(연결, 지배주주 귀속). 비KRW 기능통화 자동 KRW 환산(ECOS), 스케일가드, N/M 게이팅.
        when: "PER/PBR 얼마"·"싼가 비싼가"(scope=firm) / "코스피·코스닥 전체 밸류"(market) / "업종별 PER·PBR"·"섹터 대비 어디"(sector, company 지정 시 소속 섹터 비교) / "밸류 추이"(firm_history) / **"이 수치 근거·계산 과정이 뭐야?"(explain — company 지정 시 실제 값 대입 계산, 미지정 시 방법론·기준·출처 전문)**. 재무 펀더멘탈 자체는 financial_metrics, 배당 상세는 dividend.
        rule: scope=firm(기본, company 필수) = 실시간 DART 재무 × krx_weekly 시세 — EPS(FY0)=공시 기본주당이익(가중평균, 없으면 지배순이익÷보통주 폴백), EPS(TTM)=TTM 지배순이익÷보통주, BPS=지배자본(MRQ)÷합계주식수, 분모≤0·완전자본잠식=N/M. scope=market/sector/firm_history = Supabase 주간 스냅샷(mkt_val_history·mkt_sector_val·mkt_valuation, market_val_weekly 배치가 갱신) — PER=**Σ보통주 시총**÷Σ지배순이익(시총가중 조화평균, 우선주 시총은 제외·cap_pref 별도 노출), 시총 기반이라 수정주가 조정 불변. 섹터 분류=KSIC 하이브리드. firm과 스냅샷 방법론 차이(보통주 주가 vs 총시총) 有 — 각 출력에 명시. 값 raw KRW int(_krw), % float(_pct).
        status: ok / invalid / not_found(우선주는 보통주 코드로) / unlisted / no_financials / no_data(배치 미실행).
        note: lean v1 — RIM·EV/EBITDA·PSR·FCF·5년밴드·PIT·주당 수정주가 시계열은 v1.1.
        ref: financial_metrics, dividend, corp_gov_report, evidence
        """
        sc = (scope or "firm").strip().lower()
        if sc in ("explain", "method", "basis"):  # 수치 근거 — 계산 과정·기준·출처 (유저 "근거가 뭐야?")
            if not (company or "").strip():
                return _METHODOLOGY  # 방법론 전문 — API 0콜
            payload = await build_valuation_payload(company, format="md")
            if format == "json":
                return as_pretty_json(payload)
            if payload.get("status") != "ok":
                return _render_status(payload)
            return _render_explain_firm(payload)
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
                       "warnings": [f"scope '{scope}' 없음 — firm / market / sector / firm_history / explain 중 선택."]}
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
