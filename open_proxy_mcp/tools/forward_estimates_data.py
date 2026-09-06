"""forward_estimates_data public tool — 컨센서스 포워드 추정치(내년·내후년 예상 실적·PER)."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.forward_estimates import build_forward_estimates_payload

_STATUS_TITLE = {
    "not_found": "종목을 찾지 못함",
    "unlisted": "비상장",
    "ambiguous": "동명 후보 여러 건",
    "no_estimates": "컨센서스 추정치 없음",
    "db_error": "추정치 DB 장애",
    "invalid": "입력 오류",
}


def _won(v: Any) -> str:
    """원 단위 정수를 사람이 읽는 자로. **자를 문구에 붙여** 숫자만 떼어가지 못하게 한다."""
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


def _render_status(p: dict[str, Any]) -> str:
    st = p.get("status", "?")
    L = [f"## {p.get('subject') or '-'} — {_STATUS_TITLE.get(st, st)}  (`status={st}`)"]
    cands = (p.get("data") or {}).get("candidates")
    if cands:
        L += ["", "| 회사 | 종목코드 |", "|---|---|"]
        L += [f"| {c.get('corp_name')} | {c.get('stock_code') or '비상장'} |" for c in cands]
    for w in p.get("warnings") or []:
        L += ["", f"> {w}"]
    return "\n".join(L)


def _render(p: dict[str, Any]) -> str:
    d = p["data"]
    r = d["ruler"]
    cov = d["coverage"]
    L = [f"## {p.get('subject')} ({d.get('ticker')}·{d.get('market') or '-'}) — 컨센서스 추정치",
         "",
         f"_스냅샷 {r.get('as_of')} · **주가 {r.get('price_dd')} 종가 {_num(r.get('price_krw'), '원', '{:,.0f}')}** "
         f"· 보통주 시총 {_won(r.get('mktcap_krw'))} · {d.get('sector') or '-'}_",
         "",
         "### 이 표의 숫자를 읽는 기준",
         f"- **단위**: {r.get('unit')}",
         f"- **PER**: {r.get('per_def')}",
         f"- **PBR**: {r.get('pbr_def')} · **PSR**: {r.get('psr_def')}",
         f"- **배수 범위**: {r.get('multiple_scope')}",
         f"- **블록**: {r.get('row_split')}",
         f"- **빈칸**: {r.get('null_policy')}",
         f"- **출처**: {r.get('source')}",
         f"- 추정 행 {cov.get('estimate_rows')}개 / 스냅샷 전체 {cov.get('total_rows')}행 "
         f"· bundle={'+'.join(d.get('bundle') or [])} · period_type={d.get('period_type')}"]

    rows = d.get("rows") or []
    if rows:
        L += ["", "### 추정·실적", "",
              "| 기간 | 구분 | 매출 | 영업이익 | 지배순이익 | EPS | BPS | DPS | PER | PBR | PSR |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
        for row in rows:
            rep = row.get("reported") or {}
            der = row.get("derived") or {}
            kind = "추정E" if row.get("row_kind") == "estimate" else "실적A"
            per = _num(der.get("per"), "배") if der.get("per") is not None else "-"
            L.append(
                f"| {row.get('period')} | {kind}·{row.get('period_type')} | "
                f"{_won(rep.get('rev_krw'))} | {_won(rep.get('op_krw'))} | {_won(rep.get('ni_ctrl_krw'))} | "
                f"{_num(rep.get('eps_krw'), '원', '{:,.0f}')} | {_num(rep.get('bps_krw'), '원', '{:,.0f}')} | "
                f"{_num(rep.get('dps_krw'), '원', '{:,.0f}')} | {per} | "
                f"{_num(der.get('pbr'), '배')} | {_num(der.get('psr'), '배')} |")
        # 배수가 빠진 행은 **왜 뺐는지**를 한 번 밝힌다 — 빈칸을 「자료 없음」으로 읽지 않게.
        whys = {row["derived"]["per_why"] for row in rows
                if (row.get("derived") or {}).get("per_why")}
        for w in sorted(whys):
            L.append(f"> 배수 빈 행: {w}")

    growth = [row for row in rows if (row.get("derived") or {}).get("prev_period")]
    if growth:
        L += ["", "### 성장률 (전기 대비)", "",
              f"_{r.get('growth_caveat')}_", "",
              "| 기간 | 전기 | 매출 | 영업이익 | 지배순이익 | EPS |", "|---|---|---|---|---|---|"]
        for row in growth:
            g = row["derived"]
            L.append(f"| {row.get('period')} | {g.get('prev_period')} | "
                     f"{g.get('rev_growth_disp') or _num(g.get('rev_growth_pct'), '%')} | "
                     f"{g.get('op_growth_disp') or _num(g.get('op_growth_pct'), '%')} | "
                     f"{g.get('ni_ctrl_growth_disp') or _num(g.get('ni_ctrl_growth_pct'), '%')} | "
                     f"{g.get('eps_growth_disp') or _num(g.get('eps_growth_pct'), '%')} |")

    rev = d.get("revision")
    if rev and rev.get("rows"):
        wins = list(rev.get("baselines") or {})
        L += ["", "### 리비전 — 추정치가 어디서 왔나", "", f"_{rev.get('note')}_", ""]
        if wins:
            L.append("기준일: " + " · ".join(
                f"**{w}** = {rev['baselines'][w]['as_of']} ({rev['baselines'][w]['days']}일 전"
                + (", 이력 짧음" if rev['baselines'][w].get('partial') else "") + ")" for w in wins))
            L += ["", "| 기간 | 지금 영업이익 | " + " | ".join(f"매출 {w}" for w in wins)
                  + " | " + " | ".join(f"영업이익 {w}" for w in wins)
                  + " | " + " | ".join(f"EPS {w}" for w in wins) + " |",
                  "|---|---|" + "---|" * (3 * len(wins))]
            for row in rev["rows"]:
                if row.get("period_type") != "FY":
                    continue
                vs = row.get("vs") or {}
                cell = lambda w, m: ("없었음" if (vs.get(w) or {}).get("absent")
                                     else _num((vs.get(w) or {}).get(f"{m}_pct"), "%", "{:+.1f}"))
                L.append(f"| {row['period']} | {_won((row.get('now') or {}).get('op_krw'))} | "
                         + " | ".join(cell(w, "rev_krw") for w in wins) + " | "
                         + " | ".join(cell(w, "op_krw") for w in wins) + " | "
                         + " | ".join(cell(w, "eps_krw") for w in wins) + " |")
            s = rev.get("summary") or {}
            L.append("")
            L.append("방향(FY 영업이익): " + " · ".join(
                f"{w} 상향 {s[w]['up']} / 하향 {s[w]['down']} / 유지 {s[w]['flat']} (n={s[w]['n']})"
                for w in wins if w in s))
        else:
            L.append(f"이력 {rev.get('snapshots')}개 스냅샷 — 비교할 기준일이 아직 없다.")

    absent = d.get("fields_absent_by_design")
    if absent:
        L += ["", "### 추정 행에 원래 없는 칸", "", f"_{d.get('fields_absent_note')}_", ""]
        L += [f"- `{k}` — {v}" for k, v in absent.items()]

    for w in p.get("warnings") or []:
        L += ["", f"> {w}"]
    if d.get("more"):
        L += ["", f"> {d['more']}"]
    return "\n".join(L)


def register_tools(mcp):

    @mcp.tool()
    async def forward_estimates_data(company: str = "", bundle: str = "core",
                                     period_type: str = "FY", actual_years: int = 2,
                                     format: str = "md") -> str:
        """desc: 컨센서스 **포워드 추정치**(내년·내후년 예상 매출·영업이익·EPS·PER/PBR/PSR·성장률) + 대조용 최근 실적. 애널리스트 추정 스냅샷(`fwd`) 기반 — DART 공시가 아니다.
        when: "삼성전자 내년 예상 PER"·"2027년 컨센서스 영업이익"·"내년 실적 전망"·"포워드 밸류에이션"·"추정 EPS 성장률"·**"컨센서스 상향/하향됐나"·"한 달 전보다 추정치가 올랐나"(bundle=revision)**. 확정 실적 기반 현재 배수는 `price_multiple_data`(scope=firm), 재무 원본은 `financial_metrics`, 배당 상세는 `dividend_disclosure`.
        rule: **숫자의 기준을 두 겹으로 싣는다** — 봉투 `ruler`(as_of·**price_dd**·단위·PER 정의·배수 범위)에 한 번, 행마다 또(`period`·`row_kind`·`basis`). 🔴 `as_of` 와 `price_dd` 는 다르다(주말·휴일) — 배수는 **price_dd 종가** 기준이므로 "as_of 기준 PER"이라고 쓰면 틀린다. 행은 실적/추정이 아니라 **`reported`(벤더 원천, 틀리면 벤더 책임) / `derived`(우리 계산, 검산 대상)** 로 가른다 — 성장률이 실적/추정 경계를 넘나들기 때문. **PER=보통주 시총÷지배주주순이익**으로 `price_multiple_data` 와 정의를 맞췄다(벤더 원본은 주가÷EPS인데 그 식은 260823 에 하우스에서 버렸다 — 액면분할 때 옛 주식수 EPS 와 새 주가가 섞인다). 10% 이상 갈리면 경고로 밝힌다. **배수는 추정 FY·최신 확정 FY 행에만** 둔다(오늘 주가÷과거 실적은 배수가 아니다). **금액은 전부 원(KRW) 정수** — 억원 안 쓴다. 빈칸은 채우지 않고 뺀다(0 아님·자료 없음). bundle=core(기본, 좁게) / growth(성장률·전기값·PEG) / quality(수익성·재무비율) / keys(내부키·회계연도) / **revision(4주·12주 전 대비 추정 변화율 + FY 영업이익 상향/하향 개수 — "컨센서스가 오르고 있나"·"최근 리비전 방향"은 이것)** / all — 기본이 정답이 아니라 크기 때문에 자른 것이니 필요하면 넓혀 부를 것. revision 출처는 `fwd_hist`(주 1회 토요일 스냅샷, 13주 롤링, 260904 신설) — 기준일은 목표일 이전 가장 가까운 스냅샷이고 이력이 짧으면 partial 로 밝힌다. period_type=FY(기본)/Q/all · actual_years=대조용 실적 행 수(기본 2 — 직전 확정 실적 2개년. 추세를 보려면 넓혀 부를 것).
        status: ok / **no_estimates**(그 종목은 애널리스트 미커버 — 전체 2,764종목 중 추정 보유 713종목뿐, 74%가 여기 해당. 자료 없음이지 오류 아님) / not_found(그런 종목 없음·오탈자·비상장) / unlisted / ambiguous(동명 후보표) / **db_error**(DB 장애 — 자료 없음과 다르다, 재시도) / invalid. 🔴 셋을 뭉뚱그리지 말 것: no_estimates는 다른 도구로, not_found는 이름 재확인, db_error는 재시도.

        ref: price_multiple_data, financial_metrics, dividend_disclosure, company
        """
        payload = await build_forward_estimates_payload(
            company=company, bundle=bundle, period_type=period_type,
            actual_years=actual_years, format=format)
        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") not in ("ok", "no_estimates"):
            return _render_status(payload)
        if not (payload.get("data") or {}).get("rows"):
            return _render_status(payload)
        return _render(payload)
