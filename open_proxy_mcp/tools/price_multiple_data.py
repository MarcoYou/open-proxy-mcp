"""price_multiple_data public tool — DART(공시)+KRX(공식시세) 상대가치 배수 (기업·시장·산업 + 히스토리)."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.tools._shared import krw_scaled
from open_proxy_mcp.services.price_multiple_data import (
    build_valuation_payload,
    build_market_val_payload,
    build_sector_val_payload,
    build_firm_at_payload,
    build_firm_history_payload,
    _norm_as_of,
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


def _fni(v, ni, fmt="{:.2f}"):
    """배수 렌더 — 비었으면 **왜** 비었는지로 갈라 쓴다(260829).

    종전엔 적자든 자료없음이든 `N/M` 하나였다. 둘은 사용자가 취할 행동이 다르다 —
    적자면 「PER 로는 못 본다, PBR 로 보라」이고, 자료없음이면 「우리가 못 채웠다」다.
    실측(20260828): 섹터 11칸이 전부 적자였는데 결측으로 읽혔다.
    ni 는 그 배수의 **분모 합**이고, None = 더한 회사가 하나도 없음(=자료없음).
    """
    if v is not None:
        return fmt.format(v)
    if ni is None:
        return "자료없음"
    return f"적자 {ni / 1e12:+,.2f}조"


def _dy(row: dict[str, Any]) -> str:
    """배당수익률 칸 — 「확정 all(payers) / 선행」. 260831.

    🔴 `all` 만 쓰지 않는다. 코스닥은 두 값이 두 배 차이라 하나만 보이면 오독한다.
       확정이 아예 없으면 `-`, 선행만 있으면 그것만 쓴다 — 0 으로 메우지 않는다.
    """
    a, pay, f = row.get("div_yield_pct_all"), row.get("div_yield_pct_payers"), row.get("fwd_div_yield_pct")
    left = "-" if a is None else (f"{a:.2f}" + (f" ({pay:.2f})" if pay is not None else ""))
    return f"{left} / " + ("-" if f is None else f"{f:.2f}")


def _render_div_note(d: dict[str, Any]) -> list[str]:
    """배당수익률의 기준 — 기준일·모집단. 260831. 없으면 아무것도 안 쓴다."""
    r, m = d.get("div_yield_ruler"), d.get("div_yield_method")
    if not (r or m):
        return []
    out = []
    if m:
        out.append(f"> 💰 {m}")
    bits = []
    if r and r.get("actual_fiscal_year"):
        bits.append(f"확정 FY{r['actual_fiscal_year']}(시총 {r.get('actual_price_dd')})")
    if r and r.get("forward_as_of"):
        bits.append(f"선행 as_of {r['forward_as_of']}")
    if bits:
        out.append("> 배당수익률 기준 — " + " · ".join(bits) + ". PER·PBR 의 주간 스냅샷과 기준일이 다르다.")
    for k in ("actual_error", "forward_error"):
        if r and r.get(k):
            out.append(f"> ⚠️ {r[k]}")
    return out


def _has_fwd(row: dict[str, Any]) -> bool:
    """선행 집계 행이 붙었나 — 추정 종목 수로 가른다(0 으로 메운 행은 없다)."""
    return bool(row.get("fwd_n_total"))


def _fwd_per(row: dict[str, Any]) -> str:
    """선행 PER 칸 — 행이 없으면 `-`, 합이 0 이하면 트레일링처럼 「적자 −N조」."""
    return _fni(row.get("fwd_per"), row.get("fwd_ni_krw")) if _has_fwd(row) else "-"


def _fwd_pbr(row: dict[str, Any]) -> str:
    return _f(row.get("fwd_pbr")) if _has_fwd(row) else "-"


def _render_fwd_note(d: dict[str, Any], by_market: list[dict[str, Any]] | None = None,
                     sector: bool = False) -> list[str]:
    """선행 배수의 기준 — 방식·기준일·모집단. 260918. 없으면 아무것도 안 쓴다."""
    r, m = d.get("fwd_ruler") or {}, d.get("fwd_method")
    out = []
    if m:
        out.append(f"> 🔭 {m}")
    if r.get("as_of"):
        bits = [f"추정 스냅샷 {r['as_of']}"]
        if sector and r.get("class_dd"):
            bits.append(f"업종 분류 {r['class_dd']}")
        if by_market:  # 코스피 먼저 — 표의 행 순서(DB 정렬)와 무관하게 읽는 순서로
            bits.append("추정 종목 " + " · ".join(
                f"{mkt_label(h['market'])} {h['fwd_n_total']}사"
                for h in sorted(by_market, key=lambda x: mkt_label(x["market"]) != "KOSPI") if _has_fwd(h)))
        out.append("> 선행 기준 — " + " · ".join(bits) + ". 트레일링 칸의 주간 시세 스냅샷과 기준일이 다르다.")
    for k in ("error", "note_empty"):
        if r.get(k):
            out.append(f"> ⚠️ {r[k]}")
    return out


def _fwd_trend(hist: list[dict[str, Any]], weeks: int = 8) -> list[str]:
    """선행 배수 추이 — 주(ISO)마다 마지막 추정 스냅샷 하나. 두 주 이상일 때만 그린다."""
    import datetime as _dt

    last: dict[tuple, str] = {}
    for h in hist:
        wk = _dt.date.fromisoformat(h["as_of"]).isocalendar()[:2]
        last[wk] = max(last.get(wk, ""), h["as_of"])
    days = sorted(last.values())[-weeks:]
    if len(days) < 2:
        return []
    by = {(h["as_of"], h["market"]): h for h in hist}
    out = ["", "## 선행 배수 추이 (추정 스냅샷 · 주마다 마지막)", "",
           "| 기준일 | KOSPI 선행 PER / PBR | KOSDAQ 선행 PER / PBR |", "|---|---|---|"]
    for day in reversed(days):
        k, q = by.get((day, "KS"), {}), by.get((day, "KQ"), {})
        out.append(f"| {day} | {_fni(k.get('fwd_per'), k.get('ni_krw'))} / {_f(k.get('fwd_pbr'))} "
                   f"| {_fni(q.get('fwd_per'), q.get('ni_krw'))} / {_f(q.get('fwd_pbr'))} |")
    return out


def _render_status(payload: dict[str, Any]) -> str:
    """ok가 아닌 상태 렌더."""
    status = payload.get("status", "error")
    title = _STATUS_TITLE.get(status, status)
    lines = [f"# price_multiple_data: {payload.get('subject', '')} — {title}", ""]
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
    # 260918: 선행(추정) PER·PBR 을 트레일링 옆에 둔다 — 같은 합산 방식이라 나란히 읽어도 된다.
    fwd = any(_has_fwd(h) for h in d["latest"])
    lines.append("| 시장 | PER(FY0) | PER(TTM) |" + (" PER(선행) |" if fwd else "")
                 + " PBR(FY0) | PBR(MRQ) |" + (" PBR(선행) |" if fwd else "")
                 + " 배당수익률% 확정(배당주)/선행 | Σ시총(보통주) | Σ우선주 |")
    lines.append("|---" * (10 if fwd else 8) + "|")
    for h in d["latest"]:
        lines.append(f"| {mkt_label(h['market'])} | {_fni(h['per_fy0'], h.get('ni_fy0_krw'))} "
                     f"| {_fni(h['per_ttm'], h.get('ni_ttm_krw'))} | "
                     + (f"{_fwd_per(h)} | " if fwd else "")
                     + f"{_f(h['pbr_fy0'])} | {_f(h['pbr_mrq'])} | "
                     + (f"{_fwd_pbr(h)} | " if fwd else "")
                     + f"{_dy(h)} "
                     f"| {(h['cap_krw'] or 0)/1e12:,.0f}조 "
                     f"| {(h.get('cap_pref_krw') or 0)/1e12:,.1f}조 |")
    hist = d["history"]
    dds = sorted({h["snap_dd"] for h in hist})
    if len(dds) > 1:
        lines += ["", "## 주간 히스토리", "", "| 주(기준일) | KOSPI PER/PBR | KOSDAQ PER/PBR |", "|---|---|---|"]
        for dd in reversed(dds):
            by = {mkt_label(h["market"]): h for h in hist if h["snap_dd"] == dd}
            k, q = by.get("KOSPI", {}), by.get("KOSDAQ", {})
            lines.append(f"| {dd} | {_fni(k.get('per_ttm'), k.get('ni_ttm_krw'))} / {_f(k.get('pbr_mrq'))} "
                         f"| {_fni(q.get('per_ttm'), q.get('ni_ttm_krw'))} / {_f(q.get('pbr_mrq'))} |")
    lines += _fwd_trend(d.get("fwd_history") or [])
    lines += ["", f"> {d['method']}"]
    lines += _render_fwd_note(d, by_market=d["latest"])
    lines += _render_div_note(d)
    for w in p.get("warnings", []):
        lines.append(f"> {w}")
    return "\n".join(lines)


def _render_sector(p: dict[str, Any]) -> str:
    d = p["data"]
    lines = [f"# 산업별 밸류에이션 (기준 {d['as_of']} · {d.get('scheme_desc', '')})", ""]
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
                    lines.append(f"| {yr} | {_fni(h.get('per_fy0'), h.get('ni_fy0_krw'))} "
                                 f"| {_fni(h.get('per_ttm'), h.get('ni_ttm_krw'))} "
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
        has_dy = any(x.get("div_yield_pct_all") is not None or x.get("fwd_div_yield_pct") is not None
                     for x in rows)
        # 260918: 선행 PER·PBR — 종목수 옆 괄호가 추정이 있는 종목 수다(모집단이 다르다).
        has_fwd = any(_has_fwd(x) for x in rows)
        head = ("| 섹터 | " + ("종목수(추정)" if has_fwd else "종목수") + " | PER(TTM) |"
                + (" PER(선행) |" if has_fwd else "") + " PBR(MRQ) |" + (" PBR(선행) |" if has_fwd else "")
                + (" 배당수익률% 확정(배당주)/선행 |" if has_dy else "") + " Σ시총 |")
        ncol = 5 + (2 if has_fwd else 0) + (1 if has_dy else 0)
        lines += [f"## {mkt}" + (" (시총 상위 10 + 소속 섹터 — 전체 표는 company 없이)" if c else ""),
                  "", head, "|---" * ncol + "|"]
        for s in rows:
            mark = " ◀" if c and s["sector"] == c["sector"] else ""
            n = f"{s['n']} ({s.get('fwd_n_total') or 0})" if has_fwd else f"{s['n']}"
            lines.append(f"| {s['label']}{mark} | {n} "
                         f"| {_fni(s['per_ttm'], s.get('ni_ttm_krw'))} "
                         + (f"| {_fwd_per(s)} " if has_fwd else "")
                         + f"| {_f(s['pbr_mrq'])} "
                         + (f"| {_fwd_pbr(s)} " if has_fwd else "")
                         + (f"| {_dy(s)} " if has_dy else "")
                         + f"| {krw_scaled(s['cap_krw'])} |")
        lines.append("")
    lines.append("> PER 칸의 **「적자 −N조」** = 섹터 합산 지배순이익이 0 이하라 PER 이 성립하지 않는다"
                 "(옆의 금액이 그 합) — 그 경우 PBR로 비교. **「자료없음」** 은 순이익을 채운 회사가 "
                 "하나도 없다는 뜻으로, 둘은 다른 상태다.")
    lines += _render_fwd_note(d, sector=True)
    if d.get("scheme") == "중분류" and any(s.get("fwd_div_yield_pct") is not None for s in d["sectors"]):
        lines.append("> 확정 배당수익률은 시장 표와 업종 대분류(`scheme=\"대분류\"`)에만 있다 — "
                     "하위업종 표는 선행만 채워진다.")
    lines += _render_div_note(d)
    for w in p.get("warnings", []):
        lines.append(f"> {w}")
    return "\n".join(lines)


def _render_firm_at(p: dict[str, Any]) -> str:
    d = p["data"]
    def f(v): return "-" if v is None else f"{v:.2f}"
    cap = d.get("cap_krw"); cap_s = f"{cap/1e12:,.1f}조" if cap and cap >= 1e12 else (f"{cap/1e8:,.0f}억" if cap else "-")
    L = [f"# {p['subject']} — {d['as_of'][:4]}-{d['as_of'][4:6]}-{d['as_of'][6:]} 기준 밸류에이션 (주간 스냅샷)", "",
         f"- 요청 기준일 {d['as_of_requested']} → 스냅샷 {d['as_of']} · {d.get('market','')} · 섹터 {d.get('sector','') or '-'}", "",
         "| 지표 | 값 |", "|---|---|", f"| 시총(보통주) | {cap_s} |", f"| PER (FY0) | {f(d.get('per_fy0'))} |", f"| PER (TTM) | {f(d.get('per_ttm'))} |",
         f"| PBR (FY0) | {f(d.get('pbr_fy0'))} |", f"| PBR (MRQ) | {f(d.get('pbr_mrq'))} |"]
    for w in p.get("warnings") or []:
        L.append(f"\n> {w}")
    return "\n".join(L)


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
                         f"| {krw_scaled(s.get('cap_krw'))} | {s.get('marker','')} |")
    # ── 연말 PIT 밴드(장기 맥락) — 연 1점, FY0 기준(그 시점 최신 확정 연재무) ──
    if band:
        lines += ["", "## 연말 밴드 (장기 · FY0 기준)", "",
                  "| 연말 | PER(FY0) | PBR(FY0) | 시총(보통주) |", "|---|---|---|---|"]
        for h in reversed(band):
            lines.append(f"| {h['period']} | {_f(h.get('per_fy0'))} | {_f(h.get('pbr'))} "
                         f"| {krw_scaled(h.get('cap_krw'))} |")
    if series:
        lines += ["", f"> 📈 차트용 전 구간 주간 곡선 {len(series)}개"
                  f"({series[0]['asof']}~{series[-1]['asof']}) = `data.series`(per_fy0·per_ttm·pbr·pbr_mrq). "
                  "위 표는 그 월말 다운샘플. `▲`=분기 재무 공시로 분모 갱신(배수 변화가 가격 vs 실적 구분)."]
    lines += ["", f"> {d['method']}"]
    for w in p.get("warnings", []):
        lines.append(f"> {w}")
    return "\n".join(lines)


_METHODOLOGY = """# price_multiple_data 방법론·기준·출처 (수치 근거)

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
- **선행 PER·PBR**(260918, 시장·업종 표) = Σ시총 ÷ Σ추정 지배순이익 · Σ시총 ÷ Σ추정 자기자본
  (시총×BPS÷주가) — 트레일링과 같은 합산 방식(적자 추정도 더한다). 종목마다 가장 가까운 추정 사업연도.
  **추정이 있는 보통주만**(시장 약 650사) 더해 종목수 옆 괄호로 적는다. 업종은 추정 날짜 이하 가장 최근 업종분류.
  흑자 추정만 더한 벤더식은 JSON `fwd_per_pos`

## 산출 범위
재무로 직접 계산하는 것은 PER · PBR · 배당수익률 셋입니다. RIM·EV/EBITDA·PSR·FCF·5년밴드·PIT 시계열·
주당 수정주가 시계열은 **만들지 않습니다**(260823, 종전의 「v1.1 예정」 표기를 걷어냄). 현금흐름·FCF·듀퐁은
`financial_metrics`, 배당 상세는 `dividend_disclosure` 를 쓰세요.
**선행(애널리스트 추정) 배수는 별개입니다**(260918) — 시장·산업(업종 대분류·중분류) 표에 선행 PER·PBR 을
트레일링 옆에 싣고, 선행 PSR 은 JSON 에만 둡니다. 기업 단위 선행 추정은 `forward_estimates_data`.

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
| 주가·시총 | KRX 정보데이터시스템 → 주간 시세 저장분 | 매일 수집(전일 종가), 주 마지막 거래일 보존 |
| 환율 | 한국은행 ECOS 매매기준율(공식) | 회계기말 고정값 캐시 |
| 주간 스냅샷(시장·섹터·종목 히스토리) | 위 조합 재계산 | 매일 배치(주간 수렴) |
| 선행 배수(시장·업종) | 애널리스트 추정 이력 × 업종분류 × 주간 시세의 시장 구분 | 추정은 주 1회(토) 수집 → 다음 날 아침 집계 |

특정 종목의 실제 대입 계산은 `price_multiple_data(company="종목", scope="explain")`."""


def _render_explain_firm(p: dict[str, Any]) -> str:
    """종목별 수치 근거 — 실제 값 대입 계산 과정."""
    d = p["data"]; i = d["inputs"]; m = d["multiples"]
    price, pdate = d.get("price_krw"), d.get("price_date")
    fx, cur = i.get("fx_rate_to_krw"), i.get("functional_currency", "KRW")
    L = [f"# {p['subject']} 수치 근거 (계산 과정)", "",
         f"## 인풋과 출처",
         f"- 주가: **{price:,}원** ({pdate} 종가 — KRX 일별시세, 주간 저장분 서빙)",
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
          "> 방법론·기준 전문: `price_multiple_data(scope=\"explain\")` (company 없이)."]
    return "\n".join(L)


def register_tools(mcp):

    @mcp.tool()
    async def price_multiple_data(company: str = "", scope: str = "firm", format: str = "md",
                        scheme: str = "중분류", as_of: str = "") -> str:
        """desc: 상대가치 밸류에이션 — 기업 심층(PER·PBR·배당수익률) + 시장 전체·산업별·종목 히스토리(주간 스냅샷). 한국 표준(연결, 지배주주 귀속). 비KRW 기능통화 자동 KRW 환산(ECOS), 스케일가드, N/M 게이팅.
        when: "PER/PBR 얼마"·"싼가 비싼가"(scope=firm) / "코스피·코스닥 전체 밸류"(market) / "업종별 PER·PBR"·"섹터 대비 어디"(sector, company 지정 시 소속 섹터 비교) / "업종별 선행 PER"·"코스닥 선행 PBR"(market·sector — 트레일링 옆 선행 칸) / "밸류 추이"(firm_history) / **"이 수치 근거·계산 과정이 뭐야?"(explain — company 지정 시 실제 값 대입 계산, 미지정 시 방법론·기준·출처 전문)**. 재무 펀더멘탈 자체는 financial_metrics, 배당 상세는 dividend_disclosure.
        rule: scope=firm(기본, company 필수) = 실시간 DART 재무 × krx_weekly 시세 — **PER=보통주 시총÷지배순이익 · PBR=보통주 시총÷지배자본(MRQ)** (260823 전환: 주가÷EPS 는 액면분할·병합 때 옛 주식수 기준 EPS 와 새 주가가 섞여 틀렸다). 주식수가 상쇄돼 조정성 이벤트에 불변이고 **스냅샷 스코프와 정의가 같다**. EPS(공시 기본주당이익)·BPS 는 회사 공식값이라 인풋으로 함께 싣되 배수 산출엔 안 쓴다. 대가 — 가중평균이 아니고(연중 유상증자 시 공시 EPS 와 벌어짐), 분자는 보통주 시총인데 분모엔 우선주 몫이 포함돼 소폭 하향 편향. 분모≤0·완전자본잠식=N/M. scope=market/sector/firm_history = Supabase 주간 스냅샷(opm_val_market·opm_val_market·opm_val_firm, market_val_weekly 배치가 갱신) — PER=**Σ보통주 시총**÷Σ지배순이익(시총가중 조화평균, 우선주 시총은 제외·cap_pref 별도 노출), 시총 기반이라 수정주가 조정 불변. 섹터 분류=KSIC 하이브리드. firm과 스냅샷 방법론 차이(보통주 주가 vs 총시총) 有 — 각 출력에 명시. 값 raw KRW int(_krw), % float(_pct). **scope=market 과 scope=sector(scheme=대분류) 에는 시총가중 배당수익률이 함께 실린다**(260831) — 확정=div_yield_hist(사업연도 12월결산 확정 DPS, 연 1회) · 선행=opm_val_fwd(애널리스트 추정 DPS). PER·PBR 과 **출처 표도 기준일도 모집단도 다르다.** 분모 두 벌(all=무배당 포함 시장 관행값 / payers=배당주만)을 나란히 내는데, 코스닥은 두 값이 두 배 차이라 반드시 같이 읽어야 한다 — 눌림의 절반은 배당력이 아니라 배당하는 회사가 적다는 구성 차이다. PER 과 달리 적자여도 배당이 있으면 값이 난다. 확정은 scheme=ksic·중분류 에 안 붙고(집계 버킷이 업종 대분류다) 중분류 에는 선행만 붙는다. **선행 PER·PBR(260918)**: scope=market 과 scope=sector(scheme=대분류·중분류) 표에 애널리스트 추정 기반 선행 PER·PBR 이 트레일링 옆에 실린다 — 트레일링과 같은 합산 방식(Σ시총÷Σ추정 지배순이익, 적자 추정 포함), 추정이 있는 보통주만(종목수 옆 괄호), 기준일은 주 1회 추정 스냅샷(opm_val_fwd). 흑자 추정만 더한 벤더식(fwd_per_pos)·선행 PSR(fwd_psr)은 JSON. KSIC 에는 선행이 없다. 종목 단위 선행은 forward_estimates_data.
        as_of: YYYYMMDD(또는 YYYY-MM-DD) 과거 시점. firm → 그 시점 이하 가장 최근 **주간 스냅샷**(opm_val_firm)의 PER·PBR·시총(배당수익률 없음) / market·sector → 그 시점 이하 스냅샷. 「작년 말 PER」「2024년 12월 코스피 PBR」. 비우면 최신.
        scheme: scope=sector 의 분류 축 — "중분류"(기본, 업종 중분류 28) / "대분류"(업종 대분류 10) / "ksic"(KSIC 하이브리드 62버킷).
        status: ok / invalid / not_found(우선주는 보통주 코드로) / unlisted / no_financials / no_data(배치 미실행).
       
        ref: financial_metrics, dividend_disclosure, forward_estimates_data, corp_gov_report, evidence
        """
        sc = (scope or "firm").strip().lower()
        try:
            asof = _norm_as_of(as_of)
        except ValueError as exc:
            return str(exc)
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
            payload = await build_market_val_payload(format=format, as_of=asof)
        elif sc == "sector":
            payload = await build_sector_val_payload(company, format=format, scheme=scheme, as_of=asof)
        elif sc in ("firm_history", "history"):
            payload = await build_firm_history_payload(company, format=format)
        elif sc == "firm" and asof:
            payload = await build_firm_at_payload(company, asof)
        elif sc == "firm":
            payload = await build_valuation_payload(company, format=format)
        else:  # 오타("markets" 등)를 조용히 firm으로 보내면 의도 밖 DART 콜 — 명시 거절(QA)
            payload = {"tool": "price_multiple_data", "status": "invalid", "subject": scope,
                       "warnings": [f"scope '{scope}' 없음 — firm / market / sector / firm_history / explain 중 선택."]}
        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") != "ok":
            return _render_status(payload)
        scope_out = payload.get("data", {}).get("scope")
        if scope_out == "firm_at":
            return _render_firm_at(payload)
        if scope_out == "market":
            return _render_market(payload)
        if scope_out == "sector":
            return _render_sector(payload)
        if scope_out == "firm_history":
            return _render_firm_history(payload)
        return payload.get("markdown") or _render_status(payload)
