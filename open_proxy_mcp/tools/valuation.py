"""valuation public tool — DART(공시)+KRX(공식시세) 상대가치 배수 (기업·시장·산업 + 히스토리)."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.valuation import (
    build_valuation_payload,
    build_market_val_payload,
    build_sector_val_payload,
    build_firm_history_payload,
)
from open_proxy_mcp.market_codes import to_label as mkt_label

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
        lines.append(f"| {mkt_label(h['market'])} | {_f(h['per_fy0'])} | {_f(h['per_ttm'])} | "
                     f"{_f(h['pbr_fy0'])} | {_f(h['pbr_mrq'])} | {(h['cap_krw'] or 0)/1e12:,.0f}조 "
                     f"| {(h.get('cap_pref_krw') or 0)/1e12:,.1f}조 |")
    hist = d["history"]
    dds = sorted({h["snap_dd"] for h in hist})
    if len(dds) > 1:
        lines += ["", "## 주간 히스토리", "", "| 주(기준일) | KOSPI PER/PBR | KOSDAQ PER/PBR |", "|---|---|---|"]
        for dd in reversed(dds):
            by = {mkt_label(h["market"]): h for h in hist if h["snap_dd"] == dd}
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
        lines += [f"**{c['name']}({c['ticker']})** → {c['sector_label']} [{mkt_label(c['market'])}]",
                  f"- 기업 PER(TTM) {_f(c['firm_per_ttm'])} vs 섹터 {_f(c['sector_per_ttm'])} · "
                  f"기업 PBR {_f(c['firm_pbr_mrq'])} vs 섹터 {_f(c['sector_pbr_mrq'])}", ""]
        shist = c.get("sector_history") or []
        if len(shist) > 1:
            yearly = {h["snap_dd"][:4]: h for h in shist if h["snap_dd"][4:6] == "12"}
            if yearly:
                lines += ["## 소속 섹터 히스토리 (연말)", "",
                          "| 연말 | PER(FY0) | PER(TTM) | PBR(FY0) | PBR(MRQ) |", "|---|---|---|---|---|"]
                for yr in sorted(yearly):
                    h = yearly[yr]
                    lines.append(f"| {yr} | {_f(h.get('per_fy0'))} | {_f(h.get('per_ttm'))} "
                                 f"| {_f(h.get('pbr_fy0'))} | {_f(h.get('pbr_mrq'))} |")
                lines.append("")
            lines.append(f"> 📈 섹터 전체 시계열 {len(shist)}개월({shist[0]['snap_dd']}~{shist[-1]['snap_dd']}) = "
                         "`data.company.sector_history`(월별 FY0/TTM/MRQ). 위 표는 연말만 발췌.")
            lines.append("")
    for mkt in ("KOSPI", "KOSDAQ"):
        rows = [s for s in d["sectors"] if mkt_label(s["market"]) == mkt]
        if not rows:
            continue
        # company 지정 시 소속 시장의 상위 10 + 소속 섹터만 — 전체 100행 덤프 방지(실사용 QA P1)
        if c:
            if mkt != mkt_label(c["market"]):
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
    summ = d.get("summary") or []
    band = [h for h in d["history"] if str(h.get("source", "")).startswith("연말")]
    series = d.get("series") or []
    lines = [f"# {p['subject']} 밸류에이션 히스토리 ({mkt_label(d['market'])} · 섹터 {d['sector']})", ""]
    # ── 최근 12개월 월말(텍스트 요약) — 주간 곡선의 월말 다운샘플 + 분기공시 마커 ──
    if summ:
        lines += ["## 최근 12개월 (월말)", "",
                  "| 월 | PER(FY0) | PER(TTM) | PBR | PBR(MRQ) | 시총(보통주) | 공시 |",
                  "|---|---|---|---|---|---|---|"]
        for s in reversed(summ):   # 최신 월이 위로
            ym = f"{s['asof'][:4]}-{s['asof'][4:6]}"
            lines.append(f"| {ym} | {_f(s.get('per_fy0'))} | {_f(s.get('per_ttm'))} "
                         f"| {_f(s.get('pbr'))} | {_f(s.get('pbr_mrq'))} "
                         f"| {(s.get('cap_krw') or 0)/1e12:,.2f}조 | {s.get('marker','')} |")
    # ── 연말 PIT 밴드(장기 맥락) — 연 1점, FY0 기준(그 시점 최신 확정 연재무) ──
    if band:
        lines += ["", "## 연말 밴드 (장기 · FY0 기준)", "",
                  "| 연말 | PER(FY0) | PBR(FY0) | 시총(보통주) |", "|---|---|---|---|"]
        for h in reversed(band):
            lines.append(f"| {h['period']} | {_f(h.get('per_fy0'))} | {_f(h.get('pbr'))} "
                         f"| {(h.get('cap_krw') or 0)/1e12:,.2f}조 |")
    if series:
        lines += ["", f"> 📈 차트용 전 구간 주간 곡선 {len(series)}개"
                  f"({series[0]['asof']}~{series[-1]['asof']}) = `data.series`(per_fy0·per_ttm·pbr·pbr_mrq). "
                  "위 표는 그 월말 다운샘플. `▲`=분기 재무 공시로 분모 갱신(배수 변화가 가격 vs 실적 구분)."]
    lines += ["", f"> {d['method']}"]
    for w in p.get("warnings", []):
        lines.append(f"> {w}")
    return "\n".join(lines)


_METHODOLOGY = """# valuation 방법론·기준·출처 (수치 근거)

## 산식 (firm — 기업 심층)
| 지표 | 산식 | 기준 |
|---|---|---|
| EPS(FY0) | DART **공시 기본주당이익** (가중평균 주식수·우선주 배분 반영) | 계속+중단영업 분리 공시는 합산, 결측 시 지배순이익÷보통주 폴백 |
| EPS(TTM) | **공시 EPS 조립** = FY0 EPS + 당해 분기누적 EPS − 전년동기누적 EPS | FY0과 같은 공시 기준(대칭). 기중 액면분할·무상증자·주식배당은 수정계수(krx_adj_events)로 각 조각을 현재 기준 정렬 |
| BPS | 지배자본(최근분기 MRQ, 부재 시 FY말) ÷ 합계 유통주식수(보통+우선, 자기주식 제외) | 지배주주 귀속 |
| PER | **보통주 시총 ÷ 지배순이익** | FY0·TTM 각각 |
| PBR | **보통주 시총 ÷ 지배자본** | MRQ (부재 시 FY말) |
| 배당수익률 | 주당 현금배당(DPS) ÷ 종가 × 100 | 보통주 결의 기준 |

## 산식 (market/sector/firm_history — 주간 스냅샷)
- PER = **Σ보통주 시총 ÷ Σ지배순이익** (시총가중 조화평균, KRX 지수 PER 관행) · PBR = Σ보통주 시총 ÷ Σ지배자본(MRQ)
- Σ지배순이익에 **적자기업 포함**(흑자만 쓰는 일부 벤더와 상이) — 적자 우세 시장(KOSDAQ)의 PER이
  크게 높아짐. PBR 병행 해석 권장. trailing(과거 실적) 기준 — 컨센서스 선행 PER와 다름
- **우선주 시총은 배수에서 제외**(cap_pref로 별도 노출) — 분모의 이익·자본엔 우선주 몫이 포함되어
  배수는 소폭 하향 편향(클래스별 이익·자본 분리는 공시 부재로 불가, KRX 공표 PER도 동일 관행)
- **firm 과 같은 정의다(260823~)** — 종전에는 firm 이 주가÷EPS 라 같은 `per_ttm` 이름으로 서로
  다른 지표가 나갔다. 이제 개별종목과 시장·섹터를 직접 비교해도 된다(집계는 시총가중 조화평균이라
  개별 배수의 단순평균과는 여전히 다르다 — 큰 종목이 더 무겁다)
- 섹터 분류 = KSIC 하이브리드(자체 매핑) · 소규모(5사 미만) 섹터는 '기타(소규모)'로 합산

## 산출 범위 — 이 셋뿐입니다
PER · PBR · 배당수익률. RIM·EV/EBITDA·PSR·FCF·5년밴드·PIT 시계열·주당 수정주가 시계열은
**만들지 않습니다**(260823, 종전의 「v1.1 예정」 표기를 걷어냄). 현금흐름·FCF·듀퐁은
`financial_metrics`, 배당 상세는 `dividend` 를 쓰세요.

## 판단 기준 (게이팅)
- **N/M**: 지배순이익·지배자본 ≤0(적자·자본잠식) 또는 완전자본잠식 → 배수 미산출(음수 PER 금지)
- **지배주주 귀속**: 순이익·자본 모두 지배지분 기준(비지배 NCI 제외) — 지주사 과대평가 방지
- **비KRW 기능통화**(두산밥캣 USD 등 22사): 회계기말 환율(한국은행 ECOS 매매기준율)로 KRW 환산
- **스케일가드**: 재무 단위오류(예: 100만배) 의심 시 개별조회는 값 유지+강한 경고, 시장 집계는 제외
- **수정주가**: PER/PBR/시총은 **전 스코프가 시총 기반**이라 액면분할·병합·무상증자에 불변(계수 불요).
  유증·소각·분할의 시총 점프는 실제 이벤트라 보존. 260823 전환 이전 firm 은 주가÷EPS 라 계수가
  필요했고, 계수 파이프라인이 밀리면 배수가 틀렸다(실측 4.1%가 그 영향권이었다)
- **EPS·BPS 는 인풋으로만 노출**: 회사 공시 공식값(가중평균 반영)이라 대조에 쓴다. 배수 산출에는
  안 쓴다 — 주식수가 들어가 조정성 이벤트에 흔들리기 때문

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
                     "옛 분모 기준인 공시 EPS를 현재 기준으로 정렬 (krx_adj_events, 거래소 기준가 리셋 실측)")
    elif i.get("net_income_ttm_krw") is not None and i.get("shares_common"):
        L.append(f"- EPS(TTM) = 폴백: TTM 지배순이익 ÷ 보통주 = {i['net_income_ttm_krw']:,} ÷ "
                 f"{i['shares_common']:,} = **{i.get('eps_ttm_krw') and format(i['eps_ttm_krw'], ',')}원**"
                 "  (공시 EPS 결측 — FY0과 기준 다름 주의)")
    if i.get("controlling_equity_krw") is not None and i.get("shares_total"):
        L.append(f"- BPS = 지배자본(MRQ) ÷ 합계주식수 = {i['controlling_equity_krw']:,} ÷ "
                 f"{i['shares_total']:,} = **{i.get('bps_krw') and format(i['bps_krw'], ',')}원**")
    # 260823: 배수는 **시총 기반**(주가÷EPS 에서 전환) — 주식수가 분자·분모에서 상쇄돼
    #   액면분할·병합에 불변이고, 스냅샷 스코프와 정의가 같아진다. 기준을 계산식에 그대로 쓴다.
    cap = i.get("common_market_cap_krw")
    _calc("PER(FY0)", "보통주 시총 ÷ 지배순이익(FY0)", cap, i.get("net_income_fy0_krw"), m.get("per_fy0"))
    _calc("PER(TTM)", "보통주 시총 ÷ 지배순이익(TTM)", cap, i.get("net_income_ttm_krw"), m.get("per_ttm"))
    _calc("PBR(MRQ)", "보통주 시총 ÷ 지배자본(MRQ)", cap, i.get("controlling_equity_krw"), m.get("pbr_mrq"))
    if i.get("dps_krw") and price:
        L.append(f"- 배당수익률 = DPS ÷ 주가 = {i['dps_krw']:,} ÷ {price:,} = **{m.get('dividend_yield_pct')}%**")
    dq = d.get("data_quality") or {}
    L += ["", "## 신뢰도",
          f"- 스케일가드: {dq.get('scale_tier', '-')} (재무 단위오류 검사 — 항등식·시장최댓값 기준)",
          f"- 자본잠식 상태: {i.get('capital_impairment_status', '-')}"]
    # 조건은 둘 다 보면서 출력은 `or` 로 하나만 봤다 — 데이터 경고가 있으면 봉투 경고
    # (「이 회사가 맞나」 추정 고지)가 통째로 사라졌다. 봉투를 앞에 두고 둘 다 싣는다.
    _seen: set[str] = set()
    _warns = [w for w in list(p.get("warnings") or []) + list(d.get("warnings") or [])
              if not (w in _seen or _seen.add(w))]
    if _warns:
        L += ["", "## 유의(원문 경고)"] + [f"- {w}" for w in _warns]
    L += ["",
          "> **배수 기준(260823~)**: PER·PBR 은 **보통주 시총 ÷ 지배주주 귀속 이익·자본**입니다. "
          "주가÷EPS 가 아니라서 액면분할·병합에 흔들리지 않고, `scope=market/sector/firm_history` 와 "
          "같은 정의입니다(종전에는 같은 이름으로 다른 정의가 나갔습니다). "
          "대가 둘 — ① 가중평균이 아닙니다(공시 EPS 는 기중 주식수 변동을 가중평균으로 반영하지만 "
          "시총은 오늘 주식수만 봅니다. 연중 유상증자한 회사는 벌어집니다) "
          "② 우선주 편향(분자는 보통주 시총인데 분모엔 우선주 몫이 포함돼 소폭 낮게 나옵니다). "
          "위 EPS·BPS 는 회사 공식 공시값이라 대조용으로 함께 싣습니다.",
          "",
          "> 방법론·기준 전문: `valuation(scope=\"explain\")` (company 없이)."]
    return "\n".join(L)


def register_tools(mcp):

    @mcp.tool()
    async def valuation(company: str = "", scope: str = "firm", format: str = "md") -> str:
        """desc: 상대가치 밸류에이션 — 기업 심층(PER·PBR·배당수익률) + 시장 전체·산업별·종목 히스토리(주간 스냅샷). 한국 표준(연결, 지배주주 귀속). 비KRW 기능통화 자동 KRW 환산(ECOS), 스케일가드, N/M 게이팅.
        when: "PER/PBR 얼마"·"싼가 비싼가"(scope=firm) / "코스피·코스닥 전체 밸류"(market) / "업종별 PER·PBR"·"섹터 대비 어디"(sector, company 지정 시 소속 섹터 비교) / "밸류 추이"(firm_history) / **"이 수치 근거·계산 과정이 뭐야?"(explain — company 지정 시 실제 값 대입 계산, 미지정 시 방법론·기준·출처 전문)**. 재무 펀더멘탈 자체는 financial_metrics, 배당 상세는 dividend.
        rule: scope=firm(기본, company 필수) = 실시간 DART 재무 × krx_weekly 시세 — **PER=보통주 시총÷지배순이익 · PBR=보통주 시총÷지배자본(MRQ)** (260823 전환: 주가÷EPS 는 액면분할·병합 때 옛 주식수 기준 EPS 와 새 주가가 섞여 틀렸다). 주식수가 상쇄돼 조정성 이벤트에 불변이고 **스냅샷 스코프와 정의가 같다**. EPS(공시 기본주당이익)·BPS 는 회사 공식값이라 인풋으로 함께 싣되 배수 산출엔 안 쓴다. 대가 — 가중평균이 아니고(연중 유상증자 시 공시 EPS 와 벌어짐), 분자는 보통주 시총인데 분모엔 우선주 몫이 포함돼 소폭 하향 편향. 분모≤0·완전자본잠식=N/M. scope=market/sector/firm_history = Supabase 주간 스냅샷(opm_val_market·opm_val_market·opm_val_firm, market_val_weekly 배치가 갱신) — PER=**Σ보통주 시총**÷Σ지배순이익(시총가중 조화평균, 우선주 시총은 제외·cap_pref 별도 노출), 시총 기반이라 수정주가 조정 불변. 섹터 분류=KSIC 하이브리드. firm과 스냅샷 방법론 차이(보통주 주가 vs 총시총) 有 — 각 출력에 명시. 값 raw KRW int(_krw), % float(_pct).
        status: ok / invalid / not_found(우선주는 보통주 코드로) / unlisted / no_financials / no_data(배치 미실행).
       
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
