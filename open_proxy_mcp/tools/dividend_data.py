"""dividend_data public tool — 확정 배당 원장 + 결정공시 집계. `dividend_history_data`·
`dividend_screener` 를 260903 에 합쳤다(스코프로 갈랐을 뿐 대체가 아니다 — 각자 다른 소스를 그대로 낸다).

합친 이유 — 셋이 표를 나눠 갖고 있던 게 아니라 **하나(구 `dividend_screener`)가 틀린 답을
내고 있었다.** 분기배당 판정이 원장(`div_quarterly`)의 빈칸을 봤는데, 그 칸은 회사 절반이
비워 둔다. 실측(FY2024 「2회 이상」): 원장 기준 20사 vs 결정공시 기준 84사(누락 64, 오탐 0).
회사 시계열과 스크리너가 어차피 같은 서비스 모듈(`services/dividend_data.py`)을 썼으므로
따로 둘 이유가 없었고, 합치며 그 자리에서 옳은 소스로 갈아끼웠다.
"""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services import dividend_data as dd
from open_proxy_mcp.tools._shared import raw_cell

_RULER = [
    "### 이 표의 숫자를 읽는 기준",
    "- **단위**: 금액 원(KRW) · DPS 주당 원 · 배당성향 %",
    "- **배당성향의 분모**: 공시 원문 `(연결)현금배당성향(%)` 을 그대로 싣는다 — **연결 기준이며 우리가 계산한 값이 아니다.** 같은 회사에서 해마다 크게 튀면 대개 분모(연결 지배순이익)가 움직인 것이다 — 삼성전자 FY2022 17.9% ↔ FY2023 67.8% 는 배당총액 9.81조원이 같은데 지배순이익이 54.7조 → 14.5조원으로 급감한 결과다. 튀는 값은 원문(`evidence`)과 순이익 추이로 먼저 확인한다",
    "- **기간**: 사업연도(`bsns_year`). 12월 결산이 아니면 `결산일` 칸이 실제 결산일이다",
    "- **금액 출처**: DART 정기보고서 `alotMatter` — **확정치**. 추정도 결정공시 예고도 아니다",
    "- **횟수 출처**: 「현금ㆍ현물배당결정」 원문(결정공시) — 이사회 결의 시점 그대로. FY2020~2024 만 온전하다",
    "- **빈칸**: `확정`/`무배당`/`항목없음`/`보고서없음`을 가른다. 0 으로 메우지 않았다",
    "- **분기**: 누적 차분(3분기 누계 − 반기 누계). 앞 원장이 없으면 `미산출`",
    "- **자유서술 칸**: 결정공시 비고(11번 항목)는 **요약하지 않고 원문 전문**을 싣는다 — "
    "회사가 무엇이든 적는 칸이라 정규식으로 한 가지를 뽑으면 나머지가 사라진다. "
    "파생 플래그(`특별배당(힌트)`)는 힌트일 뿐이고 **정본은 원문**이다",
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


def _won_short(v: Any) -> str:
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


def _remarks_block(decisions: list[dict[str, Any]], complete: set[int]) -> list[str]:
    """결의 한 건 = 한 덩어리. 비고(11번 「기타 투자판단과 관련한 중요사항」) **전문**을 싣는다.

    🔴 표 셀에 넣지 않고 인용 블록으로 낸다 — 중앙값 245자·최대 1,512자라 표 칸에 넣으면
    다른 열이 읽히지 않는다. 대신 어느 결의의 비고인지 머리줄에 사업연도·결의일·구분·
    DPS·접수번호를 붙여 못 헷갈리게 한다.
    🔴 「특별배당이 있는 것만」 같은 조건을 걸지 않는다 — 그 구조가 260903 에 고친 문제다.
    """
    if not decisions:
        return []
    L = ["", "### 결정공시 비고 원문 — 11. 기타 투자판단과 관련한 중요사항", "",
         "> 서식에 칸이 없는 사실이 전부 여기 몰려 있다 — 특별·기념배당, 자기주식 제외 산정, "
         "감액배당 재원, 주총 갈음, 차등배당, 「감사·주총 과정에서 변동될 수 있음」 단서. "
         "**요약하지 않고 원문 그대로** 싣는다. 판단은 읽는 쪽에서 한다.", ""]
    n_empty = 0
    for d in decisions:
        text = raw_cell(d.get("remarks"))
        fy = d.get("fiscal_year")
        mark = "" if fy in complete else " ⚠️미완"
        head = [f"FY{fy}{mark}"]
        if d.get("board_date"):
            head.append(f"{d['board_date']} 결의")
        if d.get("dividend_type_filed"):
            head.append(str(d["dividend_type_filed"]))
        if d.get("dps_common") is not None:
            head.append(f"DPS {d['dps_common']:,.0f}원")
        if d.get("amended"):
            head.append("정정")
        if d.get("has_special"):
            head.append("특별배당 힌트")
        L.append(f"- **{' · '.join(head)}** `{d.get('rcept_no') or '-'}`")
        if text:
            L.append(f"  > {text}")
        else:
            n_empty += 1
            # 「비고가 비었다」와 「원문을 못 붙였다」를 가른다 — 둘 다 침묵하면 같아 보인다.
            L.append("  > _(비고 칸이 비어 있거나 원문을 붙이지 못했다 — "
                     "「특이사항 없음」으로 읽지 말 것. `evidence` 로 원문을 확인하라.)_")
    if n_empty:
        L += ["", f"> 비고 원문이 없는 결의 {n_empty}건 — 위에 그 줄마다 표시했다."]
    return L


def _history_cell(years: list[int], vals: list[int | None] | None) -> str:
    """`[4,4,4,4,4]` → `4·4·4·4·4` (열 머리가 `20·21·22·23·24` 로 같은 순서 — 오름차순).
    `null` 은 `-`(상장 여부를 모름: 상장 전이거나 관측표에 없음)."""
    if not vals:
        return "-"
    parts = [("-" if v is None else str(v)) for v in vals]
    return "·".join(parts)


# ─────────────────────────────────────────────────────────────────── firm ──
def _render_firm(name: str, ticker: str, d: dict[str, Any], pay: dict[str, Any]) -> str:
    L = [f"## {name} ({ticker}) — 확정 배당", ""]
    L += _RULER

    pay_rows = pay.get("rows") or []
    if pay_rows:
        L += ["", "### 결정공시 — 몇 번 배당했나 (이사회 결의 기준)", "",
              "| 사업연도 | 횟수 | 배당구분(원문) | DPS 합(보통주) | 총액 합(전 종류) | 특별배당(힌트) | 이상 |",
              "|---|---|---|---|---|---|---|"]
        complete = set(pay.get("complete_years") or [])
        for r in pay_rows:
            fy = r["fiscal_year"]
            mark = "" if fy in complete else " ⚠️미완"
            L.append(
                f"| {fy}{mark} | {r['n_payments']}회 | "
                f"{', '.join(r.get('kinds_filed') or [])} | "
                f"{_num(r.get('dps_sum'), '원', '{:,.0f}')} | {_won_short(r.get('total_sum'))} | "
                f"{'있음' if r.get('has_special') else '-'} | "
                f"{', '.join(r.get('anomalies') or []) or '-'} |")
        L += ["", "> ⚠️미완 표시가 붙은 해는 수집창 경계라 그 해 결의가 통째로 빠졌을 수 있다 "
              "— 「0회」로 읽지 말 것.",
              "> `특별배당(힌트)` 은 파서가 비고에서 「특별배당」·「기념배당」을 본 결의가 그 해에 "
              "있었다는 **표시일 뿐 정본이 아니다.** 정본은 아래 비고 원문이다 — "
              "특별배당 말고도 감액배당 재원·자기주식 제외 산정·주총 갈음·차등배당처럼 "
              "**서식에 칸이 없어 비고에만 적히는 사실**은 원문에서만 읽힌다.",
              "> 정기·특별분이 한 결의에 섞여도 **금액은 가르지 않는다** — 서식에 분리 칸이 없다."]
        L += _remarks_block(pay.get("decisions") or [], complete)
    elif pay.get("status") != "ok":
        # 🔴 **조회 실패를 「없다」로 렌더하지 않는다.** 260903 실측: 표에서 죽은 칸을
        # 지웠더니 배포본의 옛 쿼리가 깨졌는데, 화면에는 「결정공시 집계가 없다」로 나왔다
        # — 「모른다」가 「안 했다」로 읽히는, 이 서비스가 내내 막아 온 바로 그 사고다.
        L += ["", "> 🔴 **결정공시 집계를 읽지 못했다 (조회 실패).** "
              "「배당 결의가 없다」가 아니라 **모른다**이다 — 아래 원장 표만 보고 "
              "「몇 번 배당했나」를 판단하지 말라. 잠시 뒤 다시 시도하라."]
    else:
        L += ["", "> 이 회사의 결정공시 집계가 이 구간에 없다."]

    ann = d.get("annual") or []
    if ann:
        L += ["", "### 원장 — 얼마 · 배당성향 (사업보고서)", "",
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
    elif d.get("annual_failed"):
        # 🔴 연간 질의만 실패한 것 — 「원장에 없다」가 아니라 **못 읽었다**다(260903 점검).
        L += ["", "> 🔴 **연간 원장을 읽지 못했다 (조회 실패).** 「이 표에 없다」도 「배당이 없다」도 "
              "아니라 **모른다**이다 — 잠시 뒤 다시 시도하거나 `dividend_disclosure` 로 실시간 확인하라."]
    else:
        L += ["", "> 이 회사의 연간 확정 배당 원장이 이 구간에 없다. "
              "「배당이 없다」가 아니라 「이 표에 없다」이다 — `dividend_disclosure` 로 실시간 확인하라."]

    qtr = [q for q in (d.get("quarterly") or []) if q.get("row_status") == "확정"]
    if qtr:
        L += ["", "### 원장 — 분기 (정기보고서 누적차분)", "",
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
    elif d.get("quarterly_failed"):
        L += ["", "> 🔴 **분기 원장을 읽지 못했다 (조회 실패).** 「분기 확정 없음」이 아니라 **모른다**이다."]
    else:
        n_all = len(d.get("quarterly") or [])
        L += ["", f"> 원장 분기 확정 없음 (그 구간 {n_all}칸 모두 미산출·무배당·보고서없음). "
              "위 결정공시 표의 횟수가 **몇 번 배당했나**의 정본이다."]
    return "\n".join(L)


# ──────────────────────────────────────────────────────────────── screen ──
def _render_screen(year: int, cond: list[str], d: dict[str, Any], limit: int,
                    hist_years: list[int], hist: dict[str, list[int | None]] | None) -> str:
    """`hist=None` 은 이력열 **조회 실패**다(빈 dict 는 「셀 회사가 없다」) — 둘을 가른다."""
    rows = d.get("rows") or []
    matched = d.get("matched")
    n_uni = d.get("n_universe")
    uni = f"모집단 {n_uni}사" if n_uni is not None else "모집단 조회 실패"
    # 🔴 매칭 수·모집단 질의만 실패해도 예외로 죽거나 「None사」를 찍지 않는다 — 실은 행은
    #   실은 행대로 내고, 못 센 것은 못 셌다고 말한다(260903 점검: `len(rows) < None` 이
    #   TypeError 였다). 실은 수를 매칭 수로 읽히게 두지도 않는다.
    if matched is None:
        head = (f"**조건에 걸린 회사 수를 세지 못했다 (조회 실패)** — 아래 {len(rows)}사는 실은 "
                f"것일 뿐 전체 매칭 수가 아니다 ({uni})")
    else:
        head = f"**조건에 걸린 회사 {matched}사** ({uni} 중"
        if len(rows) < matched:
            head += f" · 아래에는 상위 {len(rows)}사만 실었다. 전부 보려면 `limit`을 올린다"
        head += ")"
    hist_label = "·".join(str(y - 2000) for y in hist_years) if hist_years else ""
    L = [f"## 배당 스크리닝 — FY{year}", "",
         f"_조건: {' · '.join(cond) if cond else '없음(배당 확정 전체)'}_",
         "",
         head,
         "",
         "### 이 표의 숫자를 읽는 기준",
         "- **단위**: DPS 주당 원 · 총액 원(KRW) · 배당성향 %",
         "- **금액 출처**: DART 정기보고서 `alotMatter` 사업보고서 — **확정치**",
         "- **횟수 출처**(`min_payments`·이력열): 결정공시 원문 — 실제 이사회 결의 건수. "
         "FY2020~2024 만 온전하다",
         "- **배당성향의 분모**: 공시 원문의 `(연결)현금배당성향(%)` 값을 그대로 싣는다 — "
         "**연결 기준이며 우리가 계산한 값이 아니다.**",
         "- **대상**: 보통주(`보통`·`미구분`) 행만. DPS 0원은 배당한 것이 아니므로 뺀다",
         "- **정렬**: 배당총액 큰 순",
         "",
         f"| 회사 | 종목코드 | DPS | 배당총액(신고) | 배당성향 | 결정공시 이력({hist_label}) | 공시번호 |",
         "|---|---|---|---|---|---|---|"]
    for r in rows:
        cell = "?" if hist is None else _history_cell(hist_years, hist.get(r["corp_code"]))
        L.append(f"| {r['name']} | `{r['ticker']}` | {_num(r.get('dps_krw'), '원', '{:,.0f}')} | "
                 f"{_won_short(r.get('div_total_krw'))} | {_num(r.get('payout_pct'), '%')} | "
                 f"{cell} | `{r.get('rcept_no') or '-'}` |")
    if not rows and matched is not None:
        L += ["", "> 조건에 맞는 회사가 없다. 조건을 넓히거나 사업연도를 바꿔 보라. "
              "**「그런 회사가 없다」이지 「조회가 실패했다」가 아니다.**"]
    if hist is None:
        L += ["", "> 🔴 **결정공시 이력열을 읽지 못했다 (조회 실패).** `?` 는 「결의 없음」도 "
              "「상장 전」도 아니라 **모른다**이다 — 잠시 뒤 다시 시도하라."]
    L += ["", "> 이력열의 `-` 는 **상장 여부를 모른다**는 뜻이다 — 그 사업연도 말일 시점에 아직 "
          "상장 전이거나, 티커가 상장 관측표(`krx_listing`)에 없다. 0회로 읽지 말 것. "
          "`0` 은 상장 중인데 실제로 결의가 없었다는 뜻이다.",
          "> 총액은 **신고총액** 하나만 낸다 — 보통/우선 배분값은 종류별 발행주식수가 "
          "없어 검산에 실패해 내지 않는다. 회사 하나를 깊게 보려면 `dividend_disclosure`."]
    return "\n".join(L)


# ─────────────────────────────────────────────────────────────── market/sector ──
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
          "`price_multiple_data` 의 `div_yield` 를 보라 — 그건 기준이 다른 값이다."]
    return "\n".join(L)


def register_tools(mcp):

    @mcp.tool()
    async def dividend_data(
        company: str = "",
        scope: str = "firm",
        sector: str = "",
        year_from: int = 0,
        year_to: int = 0,
        bsns_year: int = 0,
        min_payout: float = -1,
        max_payout: float = -1,
        min_dps: float = -1,
        min_payments: int = 0,
        quarterly_only: bool = False,
        limit: int = 50,
        format: str = "md",
    ) -> str:
        """desc: 확정 배당 — 회사 시계열 / 조건 스크리닝 / 시장·섹터 집계. DART 정기보고서(alotMatter) 전수 수집본(코스피 828사 × 2020~2025)과 결정공시 집계(FY2020~2024)를 DB 에서 읽는다. DART 를 실시간 호출하지 않는다.
        when: 여러 해를 가로로 보거나(firm) · 조건으로 회사를 거르거나(screen) · 시장·섹터를 볼 때(market/sector). 회사 하나를 깊게(정책신호·최신 미확정분·실시간 원문)는 `dividend_disclosure`. 시총가중 배당수익률·forward DPS·DY 는 `price_multiple_data`/`forward_estimates_data`.
        scope: `firm` 회사 하나(company 필요, 시계열+결정공시 횟수+**결의별 비고 원문 전문**) / `screen` 조건으로 거르기(bsns_year) / `market` 코스피 전체 / `sector` WICS 섹터(sector 필요)
        rule: 금액(DPS·총액·배당성향)은 원장 `alotMatter` 확정치, **횟수**(min_payments·이력열)는 결정공시 원문 — 서로 다른 소스라 합치지 않는다. 결정공시는 FY2020~2024 만 온전(그 밖은 `scope_incomplete`). 총액은 신고총액 하나만(보통/우선 배분 불가). 비고(11번 「기타 투자판단과 관련한 중요사항」)는 **결의마다 전문을 그대로** 낸다 — 특별·기념배당, 감액배당 재원, 자기주식 제외 산정, 주총 갈음, 차등배당은 그 칸에만 적힌다. 파생 플래그는 힌트일 뿐이니 **원문을 읽고 판단하라.**
        min_payments: screen 전용. **그 해에 실제로 결의된 배당 횟수**가 이 값 이상인 회사(결정공시 기준 — 원장 분기 빈칸 추정이 아니다). `quarterly_only=True` 는 `min_payments=2` 의 별칭(하위호환).
        ref: dividend_disclosure, price_multiple_data, forward_estimates_data, screener, evidence
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

        if scope == "screen":
            year = bsns_year or hi
            if not (lo <= year <= hi):
                return (f"## 사업연도 범위 밖 (`status=invalid`)\n\n"
                        f"> 이 표가 담고 있는 구간은 **FY{lo}~FY{hi}** 다.")
            mp = min_payments if min_payments > 0 else (2 if quarterly_only else None)

            cond = []
            if min_payout >= 0:
                cond.append(f"배당성향 ≥ {min_payout}%")
            if max_payout >= 0:
                cond.append(f"배당성향 ≤ {max_payout}%")
            if min_dps >= 0:
                cond.append(f"DPS ≥ {min_dps:,.0f}원")
            if mp:
                cond.append(f"그 해 배당 {mp}회 이상 (결정공시)")
            if sector:
                cond.append(f"섹터 {sector}")

            lim = max(1, min(limit, 300))
            data = dd.screen(
                year,
                min_payout=min_payout if min_payout >= 0 else None,
                max_payout=max_payout if max_payout >= 0 else None,
                min_dps=min_dps if min_dps >= 0 else None,
                min_payments=mp,
                sector=sector,
                limit=lim,
            )
            if data.get("status") == "scope_incomplete":
                cy = data.get("complete_years") or []
                return (f"## FY{year} 결정공시 집계 미완 (`status=scope_incomplete`)\n\n"
                        f"> `min_payments` 는 결정공시가 온전한 해만 쓸 수 있다 — "
                        f"**FY{min(cy)}~FY{max(cy)}** (그 밖 해는 결의 누락 구간).")
            if data.get("status") != "ok":
                return "## 배당 스크리닝 조회 실패 (`status=db_error`)"

            complete = dd.payment_scope_years()
            hist_years = list(range(min(complete), max(complete) + 1)) if complete else []
            pairs = [(r["corp_code"], r["ticker"]) for r in (data.get("rows") or [])]
            # None = 이력 질의 실패(렌더가 「모른다」로 낸다) · {} = 셀 회사가 없다.
            hist = dd.payment_history(pairs, hist_years[0], hist_years[-1]) if pairs and hist_years else {}

            if format == "json":
                return as_pretty_json({"status": "ok", "data": data, "conditions": cond,
                                       "bsns_year": year, "payment_history": hist,
                                       "payment_history_failed": hist is None,
                                       "payment_history_years": hist_years})
            return _render_screen(year, cond, data, lim, hist_years, hist)

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
        pay = dd.payment_counts(corp.get("corp_code", ""), y0, y1)
        if format == "json":
            return as_pretty_json({"status": "ok", "subject": corp.get("corp_name"),
                                   "data": data, "payment_counts": pay})
        return _render_firm(corp.get("corp_name", company), corp.get("stock_code", "-"), data, pay)
