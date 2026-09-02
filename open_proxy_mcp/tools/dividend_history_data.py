"""dividend_history_data public tool — 확정 배당 **시계열**(회사·시장·섹터 × 사업연도)."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services import dividend_data as dd

_RULER = [
    "### 자(尺) — 이 표의 모든 숫자에 붙는 기준",
    "- **단위**: 금액 원(KRW) · DPS 주당 원 · 배당성향 %",
    "- **배당성향의 분모**: 공시 원문 `(연결)현금배당성향(%)` 을 그대로 싣는다 — **연결 기준이며 우리가 계산한 값이 아니다.** 같은 회사에서 해마다 크게 튀면 회사가 신고한 분모가 바뀐 것이다(삼성전자 FY2022 17.9% ↔ FY2023 67.8%는 DPS·총액이 같은데도 그렇다). 원문은 `evidence` 로 본다.",
    "- **기간**: 사업연도(`bsns_year`). 12월 결산이 아니면 `결산일` 칸이 실제 결산일이다",
    "- **출처**: DART 정기보고서 `alotMatter` — **확정치**. 추정도 결정공시 예고도 아니다",
    "- **빈칸**: `확정`/`무배당`/`항목없음`/`보고서없음`을 가른다. 0 으로 메우지 않았다",
    "- **분기**: 누적 차분(3분기 누계 − 반기 누계). 앞 원장이 없으면 `미산출`",
]


def _won(v: Any) -> str:
    if v is None:
        return "-"
    n = float(v)
    if abs(n) >= 1e12:
        return f"{n / 1e12:,.2f}조원 ({n:,.0f}원)"
    if abs(n) >= 1e8:
        return f"{n / 1e8:,.0f}억원 ({n:,.0f}원)"
    return f"{n:,.0f}원"


def _num(v: Any, suffix: str = "", fmt: str = "{:,.2f}") -> str:
    return fmt.format(v) + suffix if v is not None else "-"


def _render_firm(name: str, ticker: str, d: dict[str, Any]) -> str:
    L = [f"## {name} ({ticker}) — 확정 배당 시계열", ""]
    L += _RULER
    ann = d.get("annual") or []
    if ann:
        L += ["", "### 연간 (사업보고서)", "",
              "| 사업연도 | 주식 종류 | DPS | 배당총액(신고) | 배당성향 | 상태 | 공시번호 |",
              "|---|---|---|---|---|---|---|"]
        for r in ann:
            L.append(
                f"| {r['bsns_year']} | {r.get('stock_knd_raw') or r.get('stock_kind') or '-'} | "
                f"{_num(r.get('dps_krw'), '원', '{:,.0f}')} | {_won(r.get('div_total_krw'))} | "
                f"{_num(r.get('payout_pct'), '%')} | {r.get('row_status') or '-'} | "
                f"`{r.get('rcept_no') or '-'}` |")
        L += ["", "> 🔴 **배당총액 칸이 종류별 행에 같은 값으로 반복되는 것은 오류가 아니다.** "
              "신고총액(현금배당금총액)은 **회사 하나에 한 값**이고 보통주·우선주로 나뉘어 "
              "공시되지 않는다. 종류별로 다른 것은 DPS 뿐이다.",
              "> 보통/우선 **배분값은 내지 않는다** — 종류별 발행주식수가 이 서식에 없어 "
              "`DPS × 주식수` 로 만든 값이 신고총액과 57.2% 만 5% 이내로 맞았다(2026-09-02 검산)."]
        _folded = d.get("empty_kind_rows_folded") or 0
        if _folded:
            L += [f"> 종류주식 칸이 비어 있는 줄 {_folded}개는 접었다 — DART 서식이 회사가 "
                  "안 쓴 종류 칸도 줄 수를 맞춰 내보내는 것이라, 그대로 두면 「같은 해에 "
                  "배당했는데 무배당」으로 읽힌다. **무배당 판정이 아니다.**"]
    else:
        L += ["", "> 이 회사의 연간 확정 배당 원장이 이 구간에 없다. "
              "「배당이 없다」가 아니라 「이 표에 없다」이다 — `dividend` 로 실시간 확인하라."]

    qtr = [q for q in (d.get("quarterly") or []) if q.get("row_status") == "확정"]
    if qtr:
        L += ["", "### 분기 (정기보고서 누적차분)", "",
              "| 사업연도 | 분기 | 누계 배당총액 | 그 분기분 | 이상 | 공시번호 |",
              "|---|---|---|---|---|---|"]
        for r in qtr:
            L.append(
                f"| {r['bsns_year']} | {r.get('q_label') or r.get('reprt_code')} | "
                f"{_won(r.get('cum_div_total_krw'))} | {_won(r.get('quarterly_div_krw'))} | "
                f"{r.get('anomaly') or '-'} | `{r.get('rcept_no') or '-'}` |")
        if any(r.get("anomaly") for r in qtr):
            L += ["", "> 🔴 `음수차분` 이 붙은 줄은 **누적이라는 전제가 깨진 구간**이다. "
                  "분기 발표 뒤 결산에서 배당이 하향된 사례가 실재한다 — 그 줄은 신뢰를 낮게 "
                  "잡고 `evidence` 로 원문을 확인하라."]
    else:
        n_all = len(d.get("quarterly") or [])
        L += ["", f"> 분기 확정 원장 없음 (그 구간 {n_all}칸 모두 미산출·무배당·보고서없음). "
              "대다수 회사는 분기보고서 배당칸을 비워 둔다 — 4분기 모두 확정인 곳은 "
              "코스피 96개사뿐이다(2026-09-02 실측)."]
    return "\n".join(L)


def _render_agg(scope: str, key: str, d: dict[str, Any]) -> str:
    L = [f"## {key} — 확정 배당 집계 ({'WICS 섹터' if scope == 'sector' else '시장'})", ""]
    L += _RULER
    L += ["", "| 사업연도 | 모집단 | 배당한 회사 | 배당총액 합 | 배당성향 평균 |",
          "|---|---|---|---|---|"]
    for r in d.get("rows") or []:
        L.append(f"| {r['bsns_year']} | {r['n_universe']}사 | {r['n_payers']}사 | "
                 f"{_won(r.get('div_total_krw'))} | {_num(r.get('payout_avg'), '%')} |")
    L += ["", "> **분모를 두 벌 낸다** — 모집단(그 해 표에 있는 회사 전부)과 배당한 회사. "
          "한 벌만 보면 「배당이 줄었다」와 「배당하는 회사가 줄었다」가 구별되지 않는다.",
          "> 배당성향 평균은 **단순평균**이다(시총가중 아님). 시총가중 배당수익률은 "
          "`price_multiple_data` 의 `div_yield` 를 보라 — 그건 다른 자다."]
    return "\n".join(L)


def register_tools(mcp):

    @mcp.tool()
    async def dividend_history_data(
        company: str = "",
        scope: str = "firm",
        sector: str = "",
        year_from: int = 0,
        year_to: int = 0,
        format: str = "md",
    ) -> str:
        """desc: 확정 배당 **시계열** — 회사·시장·섹터 × 사업연도. DART 정기보고서(alotMatter) 전수 수집본(코스피 828사 × 2020~2025)을 DB 에서 읽는다. DART 를 실시간 호출하지 않는다.
        when: 여러 해를 가로로 볼 때. 회사 하나를 깊게(결정공시·정책신호·최신 미확정분) 볼 때는 `dividend`. 시총가중 배당수익률은 `price_multiple_data`.
        rule: 확정치만. 총액은 **신고총액** 하나만 낸다(보통/우선 배분은 종류별 주식수가 없어 검산 실패 — 내지 않는다). 빈칸은 확정/무배당/항목없음/보고서없음으로 갈라 낸다. 분기는 누적차분이고 `음수차분` 표시가 붙은 줄은 전제가 깨진 구간이다.
        scope: `firm` 회사 하나(company 필요) / `market` 코스피 전체 / `sector` WICS 섹터(sector 필요)
        ref: dividend, price_multiple_data, dividend_screener, evidence
        """
        rng = dd.year_range()
        if rng is None:
            return "## 배당 원장 DB 조회 실패 (`status=db_error`)\n\n> 일시 장애일 수 있다. 잠시 뒤 다시."
        lo, hi = rng
        y0 = year_from or lo
        y1 = year_to or hi

        if scope == "market":
            data = dd.aggregate_history("market", "코스피", y0, y1)
            if data.get("status") != "ok":
                return "## 시장 배당 집계 조회 실패 (`status=db_error`)"
            return as_pretty_json(data) if format == "json" else _render_agg("market", "코스피", data)

        if scope == "sector":
            if not sector:
                names = dd.sector_list()
                return ("## 섹터를 지정하라 (`status=invalid`)\n\n"
                        + "\n".join(f"- {s}" for s in names))
            data = dd.aggregate_history("sector", sector, y0, y1)
            if data.get("status") != "ok":
                return "## 섹터 배당 집계 조회 실패 (`status=db_error`)"
            if not data.get("rows"):
                return (f"## {sector} — 해당 없음 (`status=not_found`)\n\n"
                        "> 그 섹터로 잡힌 회사가 이 표에 없다. 섹터 이름을 확인하라.")
            return as_pretty_json(data) if format == "json" else _render_agg("sector", sector, data)

        # scope == firm
        if not company:
            return "## 회사를 지정하라 (`status=invalid`)\n\n> `company` 에 회사명이나 종목코드."
        from open_proxy_mcp.services.price_multiple_data import _resolve_listed
        corp, early = await _resolve_listed(company)
        if early:
            return as_pretty_json(early) if format == "json" else (
                "## 회사 식별 모호 (`status=ambiguous`)\n\n"
                + "\n".join(f"- {c.get('corp_name')} `{c.get('stock_code')}`"
                            for c in early["data"]["candidates"]))
        if not corp:
            return f"## '{company}' 을(를) 찾지 못했다 (`status=not_found`)"
        data = dd.firm_history(corp.get("corp_code", ""), y0, y1)
        if data.get("status") != "ok":
            return "## 배당 원장 조회 실패 (`status=db_error`)"
        if format == "json":
            return as_pretty_json({"status": "ok", "subject": corp.get("corp_name"),
                                   "data": data})
        return _render_firm(corp.get("corp_name", company), corp.get("stock_code", "-"), data)
