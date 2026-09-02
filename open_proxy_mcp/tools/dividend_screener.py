"""dividend_screener public tool — 한 사업연도에서 배당 조건으로 회사를 거른다(스팟)."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services import dividend_data as dd


def _won(v: Any) -> str:
    if v is None:
        return "-"
    n = float(v)
    if abs(n) >= 1e12:
        return f"{n / 1e12:,.2f}조원"
    if abs(n) >= 1e8:
        return f"{n / 1e8:,.0f}억원"
    return f"{n:,.0f}원"


def _num(v: Any, suffix: str = "", fmt: str = "{:,.2f}") -> str:
    return fmt.format(v) + suffix if v is not None else "-"


def _render(year: int, cond: list[str], d: dict[str, Any], limit: int) -> str:
    rows = d.get("rows") or []
    L = [f"## 배당 스크리닝 — FY{year}", "",
         f"_조건: {' · '.join(cond) if cond else '없음(배당 확정 전체)'}_",
         "",
         f"**{len(rows)}사** (모집단 {d.get('n_universe')}사 중 · 상위 {limit}까지 표시)",
         "",
         "### 자(尺)",
         "- **단위**: DPS 주당 원 · 총액 원(KRW) · 배당성향 %",
         "- **출처**: DART 정기보고서 `alotMatter` 사업보고서 — **확정치**",
         "- **대상**: 보통주 행만. `status='확정'` 인 회사만(무배당·항목없음 제외)",
         "- **정렬**: 배당총액 큰 순",
         "",
         "| 회사 | 종목코드 | DPS | 배당총액(신고) | 배당성향 | 공시번호 |",
         "|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['name']} | `{r['ticker']}` | {_num(r.get('dps_krw'), '원', '{:,.0f}')} | "
                 f"{_won(r.get('div_total_krw'))} | {_num(r.get('payout_pct'), '%')} | "
                 f"`{r.get('rcept_no') or '-'}` |")
    if not rows:
        L += ["", "> 조건에 맞는 회사가 없다. 조건을 넓히거나 사업연도를 바꿔 보라. "
              "**「그런 회사가 없다」이지 「조회가 실패했다」가 아니다.**"]
    L += ["", "> 총액은 **신고총액** 하나만 낸다 — 보통/우선 배분값은 종류별 발행주식수가 "
          "없어 검산에 실패해 내지 않는다(2026-09-02). 회사 하나를 깊게 보려면 `dividend`, "
          "여러 해를 보려면 `dividend_history_data`."]
    return "\n".join(L)


def register_tools(mcp):

    @mcp.tool()
    async def dividend_screener(
        bsns_year: int = 0,
        min_payout: float = -1,
        max_payout: float = -1,
        min_dps: float = -1,
        quarterly_only: bool = False,
        sector: str = "",
        limit: int = 50,
        format: str = "md",
    ) -> str:
        """desc: 한 사업연도에서 **배당 조건으로 회사를 거른다**. 배당성향 범위·최소 DPS·분기배당 여부·WICS 섹터. DART 정기보고서 전수 수집본(코스피 828사 × 2020~2025)을 DB 에서 읽는다.
        when: 「배당성향 30% 넘는 곳」·「분기배당 하는 회사」처럼 **여럿을 가로로** 찾을 때. 회사 하나는 `dividend`, 한 회사의 여러 해는 `dividend_history_data`.
        rule: 보통주 행·확정치만. 결과에 **분모(모집단 회사 수)** 를 함께 낸다 — 몇 중 몇인지 없으면 결과를 읽을 수 없다. 0건은 「그런 회사가 없다」이지 조회 실패가 아니다.
        quarterly_only: 그 해 4분기 원장이 **모두 확정**인 회사만 — 실질적으로 분기·중간배당을 하는 곳. 코스피 96개사뿐이다(2026-09-02 실측).
        ref: dividend, dividend_history_data, price_multiple_data, screener, evidence
        """
        rng = dd.year_range()
        if rng is None:
            return "## 배당 원장 DB 조회 실패 (`status=db_error`)\n\n> 일시 장애일 수 있다."
        lo, hi = rng
        year = bsns_year or hi
        if not (lo <= year <= hi):
            return (f"## 사업연도 범위 밖 (`status=invalid`)\n\n"
                    f"> 이 표가 담고 있는 구간은 **FY{lo}~FY{hi}** 다.")

        cond = []
        if min_payout >= 0:
            cond.append(f"배당성향 ≥ {min_payout}%")
        if max_payout >= 0:
            cond.append(f"배당성향 ≤ {max_payout}%")
        if min_dps >= 0:
            cond.append(f"DPS ≥ {min_dps:,.0f}원")
        if quarterly_only:
            cond.append("분기 4칸 모두 확정")
        if sector:
            cond.append(f"섹터 {sector}")

        data = dd.screen(
            year,
            min_payout=min_payout if min_payout >= 0 else None,
            max_payout=max_payout if max_payout >= 0 else None,
            min_dps=min_dps if min_dps >= 0 else None,
            quarterly_only=quarterly_only,
            sector=sector,
            limit=max(1, min(limit, 300)),
        )
        if data.get("status") != "ok":
            return "## 배당 스크리닝 조회 실패 (`status=db_error`)"
        if format == "json":
            return as_pretty_json({"status": "ok", "data": data,
                                   "conditions": cond, "bsns_year": year})
        return _render(year, cond, data, max(1, min(limit, 300)))
