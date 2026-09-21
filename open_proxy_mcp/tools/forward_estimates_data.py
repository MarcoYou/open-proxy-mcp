"""forward_estimates_data public tool — 컨센서스 포워드 추정치(내년·내후년 예상 실적·PER)."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.services import forward_estimates as _fe
from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.forward_estimates import (build_forward_estimates_payload,
                                                      build_revision_screen_payload)

_STATUS_TITLE = {
    "not_found": "종목을 찾지 못함",
    "unlisted": "비상장",
    "ambiguous": "동명 후보 여러 건",
    "no_estimates": "컨센서스 추정치 없음",
    "db_error": "추정치 DB 장애",
    "db_unconfigured": "저장분 DB 미연결 — 이 서버에서는 제공되지 않음",
    "no_data": "해당 없음",
    "invalid": "입력 오류",
}

_SCREEN_MD_MAX = 300         # 유니버스 리비전 순위 표 상한 — 넘으면 양 끝만 남긴다
_SCREEN_GUARD_MD_MAX = 20    # 순위 밖(분모 가드) 묶음은 양 끝만 — %를 못 믿는 행을 길게 실을 이유가 없다
_RANK_MIN_OP_EOK = _fe._REV_RANK_MIN_OP // 10**8   # 억원 단위 — 문구가 상수와 어긋나지 않게


_PERIOD_KO = {"FY": "연간", "Q": "분기", "all": "연간+분기"}

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
         f"· 묶음 {'+'.join(d.get('bundle') or [])} · 기간 {_PERIOD_KO.get(d.get('period_type'), d.get('period_type'))}"]

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


def _fold_screen_rows(rows: list[dict[str, Any]], cap: int) -> tuple[list, list, int]:
    """상한을 넘는 표를 **양 끝만 남기고** 접는다 → (머리, 꼬리, 접은 수).

    정렬이 내림차순이라 앞에서만 자르면 **하향이 통째로 사라진다.** 260921 실측: 전체 유니버스
    685행(순위 538행)을 앞에서 300행으로 자르면 하향 51종목이 616~666위에 몰려 있어 **0종목**만
    남는다 — 「가장 많이 하향된 종목」을 md 로는 영원히 못 뽑는다."""
    if len(rows) <= cap:
        return rows, [], 0
    n = cap // 2
    return rows[:n], rows[-n:], len(rows) - 2 * n


def _render_revision_screen(p: dict[str, Any]) -> str:
    d = p["data"]
    win = d["window"]
    cov = d["coverage"]; dr = d["direction"]
    L = [f"## {p.get('subject')} — 영업이익 추정 {win} 변화 (최신 스냅샷 {d.get('as_of_latest') or '-'})",
         "",
         f"_유니버스 {cov['universe']}종목 · 추정 보유 {cov['with_estimates']} · 비교 가능 {cov['comparable']} "
         f"· 이력 짧음 {cov['history_short']} · 초점: {d['focus']}_",
         "",
         f"방향(영업이익 {win}): 상향 {dr['up']} / 하향 {dr['down']} / 유지 {dr['flat']} / 비교 불가 {dr['not_comparable']}"]
    rows = d.get("rows") or []
    ranked = [r for r in rows if r.get("rank_basis", "ranked") == "ranked"]
    guarded = [r for r in rows if r.get("rank_basis") in ("base_loss", "base_small")]

    def table(rs: list[dict[str, Any]], folded: int = 0, foot: list | None = None) -> None:
        L.extend(["", f"| 순위 | 종목 | 코드 | 시장 | 기간 | 지금 영업이익 | 영업이익 {win} | 이동 "
                      f"| 매출 {win} | 지배순이익 {win} | EPS {win} | 기준일 |",
                  "|---|---|---|---|---|---|---|---|---|---|---|---|"])

        def emit(part):
            for r in part:
                def pct(m):
                    if r.get("absent_at_baseline"):
                        return "없었음"
                    return _num(r.get(f"{m}_{win}_pct"), "%", "{:+.1f}")
                base = r.get("baseline_as_of") or "비교 불가"
                if r.get("history_short"):
                    base += " (이력 짧음)"
                # 순위 칸은 등수가 없으면 **왜 없는지**를 대신 말한다.
                place = {"base_loss": "적자기준", "base_small": "소액기준"}.get(
                    r.get("rank_basis"), r.get("rank")) or "-"
                name = r["name"] + ("†" if r.get("sole_update") else "")
                L.append(f"| {place} | {name} | `{r['ticker']}` | {r.get('market') or '-'} | {r['period']} | "
                         f"{_won(r.get('op_krw'))} | {pct('op_krw')} | {_won(r.get(f'op_krw_{win}_delta'))} | "
                         f"{pct('rev_krw')} | {pct('ni_ctrl_krw')} | {pct('eps_krw')} | {base} |")

        emit(rs)
        if foot:
            L.append(f"| ⋯ | **가운데 {folded}종목 접음** | " + " | ".join(["⋯"] * 10) + " |")
            emit(foot)

    if ranked:
        head, foot, folded = _fold_screen_rows(ranked, _SCREEN_MD_MAX)
        table(head, folded, foot)
        if foot:
            L += ["", f"> 순위 {len(ranked)}종목 중 **위 {len(head)} · 아래 {len(foot)}** 만 싣고 가운데 "
                      f"{folded}종목은 접었다 — 앞에서만 자르면 하향이 통째로 사라진다. "
                      "전체는 format=\"json\" 의 data.rows."]
    if guarded:
        ghead, gfoot, gfolded = _fold_screen_rows(guarded, _SCREEN_GUARD_MD_MAX)
        L += ["", f"**순위 밖 — 분모 가드 {len(guarded)}종목** (기준 영업이익이 적자·0 이거나 "
                  f"{_RANK_MIN_OP_EOK}억원 미만. %의 분모가 작아 몇 억원 움직임도 수백 %가 된다. "
                  "지운 게 아니라 등수만 안 매겼다 — 크기는 「이동」 칸으로 볼 것.)"]
        table(ghead, gfolded, gfoot)
        if gfolded:
            L += ["", f"> 이 묶음도 양 끝 {len(ghead)}+{len(gfoot)} 만 싣고 {gfolded}종목을 접었다."]
    if any(r.get("sole_update") for r in rows):
        L += ["", "> † = 관측 구간 내내 값이 같다가 **이번 한 번만** 움직였다. 이 %는 주마다 쌓인 추세가 "
                  "아니라 단발 갱신이다 — 상향·하향 양쪽에 다 있고, **왜 한 번에 움직였는지는 이 표가 "
                  "말해 주지 않는다.**"]
    L += ["", f"_{d.get('note')}_"]
    for w in p.get("warnings") or []:
        L += ["", f"> {w}"]
    return "\n".join(L)


def render_payload(payload: dict[str, Any], format: str = "md") -> str:
    """응답 모양에 맞는 렌더러로. company 자리에 유니버스 문장이 와서 스크린 응답이 돌아오는 경우
    (260916 live 실측: 종목 렌더러로 보내 `ruler` 없음으로 죽었다)도 여기서 갈린다."""
    if format == "json":
        return as_pretty_json(payload)
    data = payload.get("data") or {}
    if payload.get("status") not in ("ok", "no_estimates") or not data.get("rows"):
        return _render_status(payload)
    if data.get("scope") == "revision_screen":
        return _render_revision_screen(payload)
    return _render(payload)


def register_tools(mcp):

    @mcp.tool()
    async def forward_estimates_data(company: str = "", bundle: str = "core",
                                     period_type: str = "FY", actual_years: int = 2,
                                     format: str = "md", universe: str = "",
                                     window: str = "4w") -> str:
        """desc: 컨센서스 **포워드 추정치**(내년·내후년 예상 매출·영업이익·EPS·PER/PBR/PSR·성장률) + 대조용 최근 실적. 애널리스트 추정 스냅샷(`fwd`) 기반 — DART 공시가 아니다.
        when: "삼성전자 내년 예상 PER"·"2027년 컨센서스 영업이익"·"내년 실적 전망"·"포워드 밸류에이션"·"추정 EPS 성장률"·**"컨센서스 상향/하향됐나"·"한 달 전보다 추정치가 올랐나"(bundle=revision)** / **"코스피 상위 100 중 영업이익 컨센서스가 가장 많이 오른 종목"·"유니버스 전체 리비전 순위"·"이익 모멘텀 스크린"(universe="코스피 시총 상위 100" — 종목마다 부르지 말 것, 한 번에 표로 준다)**. 확정 실적 기반 현재 배수는 `price_multiple_data`(scope=firm), 재무 원본은 `financial_metrics`, 배당 상세는 `dividend_disclosure`.
        rule: **숫자의 기준을 두 겹으로 싣는다** — 봉투 `ruler`(as_of·**price_dd**·단위·PER 정의·배수 범위)에 한 번, 행마다 또(`period`·`row_kind`·`basis`). 🔴 `as_of` 와 `price_dd` 는 다르다(주말·휴일) — 배수는 **price_dd 종가** 기준이므로 "as_of 기준 PER"이라고 쓰면 틀린다. 행은 실적/추정이 아니라 **`reported`(벤더 원천, 틀리면 벤더 책임) / `derived`(우리 계산, 검산 대상)** 로 가른다 — 성장률이 실적/추정 경계를 넘나들기 때문. **PER=보통주 시총÷지배주주순이익**으로 `price_multiple_data` 와 정의를 맞췄다(벤더 원본은 주가÷EPS인데 그 식은 260823 에 하우스에서 버렸다 — 액면분할 때 옛 주식수 EPS 와 새 주가가 섞인다). 10% 이상 갈리면 경고로 밝힌다. **배수는 추정 FY·최신 확정 FY 행에만** 둔다(오늘 주가÷과거 실적은 배수가 아니다). **금액은 전부 원(KRW) 정수** — 억원 안 쓴다. 빈칸은 채우지 않고 뺀다(0 아님·자료 없음). bundle=core(기본, 좁게) / growth(성장률·전기값·PEG) / quality(수익성·재무비율) / keys(내부키·회계연도) / **revision(1주·4주·12주 전 대비 추정 변화율 + FY 영업이익 상향/하향 개수 — "컨센서스가 오르고 있나"·"최근 리비전 방향"은 이것)** / all — 기본이 정답이 아니라 크기 때문에 자른 것이니 필요하면 넓혀 부를 것. revision 출처는 `fwd_hist`(주 1회 토요일 스냅샷, 13주 롤링, 260904 신설) — 기준일은 목표일 이전 가장 가까운 스냅샷이고 이력이 짧으면 partial 로 밝힌다. period_type=FY(기본)/Q/all · actual_years=대조용 실적 행 수(기본 2 — 직전 확정 실적 2개년. 추세를 보려면 넓혀 부를 것). **universe 를 주면 유니버스 리비전 스크린**: `screener`·`trading_data(scope=universe)` 와 같은 유니버스 문법(「코스피 시총 상위 N」·「코스닥 상위 N」·「코스피200」·「전체」·이름/코드 나열)으로 종목 집합을 만들고 `fwd_hist` 를 한 질의로 읽어 종목별 영업이익·매출·지배순이익·EPS 의 window(1w / 4w 기본 / 12w) 변화율을 영업이익 변화율 내림차순으로 준다(DB 2콜, DART 0콜). 초점은 종목마다 가장 가까운 연간 추정 기간 한 행. 추정 없는 종목은 표에서 빠지고 개수로만 남는다. md 표는 300종목까지 — **넘으면 양 끝만 싣고 가운데를 접는다**(앞만 자르면 하향이 통째로 사라진다). 🔴 **기준 영업이익이 적자·0 이거나 100억원 미만이면 순위에서 빼 뒤에 따로 묶는다**(등수를 비우고 `rank_basis` 에 이유 — 분모가 작아 %가 폭주한다). 크기는 변화율 말고 **이동 절대액**(`op_krw_<win>_delta`)으로 볼 것.
        status: ok / **no_estimates**(그 종목은 애널리스트 미커버 — 전체 2,764종목 중 추정 보유 713종목뿐, 74%가 여기 해당. 자료 없음이지 오류 아님) / not_found(그런 종목 없음·오탈자·비상장) / unlisted / ambiguous(동명 후보표) / **db_error**(DB 장애 — 자료 없음과 다르다, 재시도) / invalid. 🔴 셋을 뭉뚱그리지 말 것: no_estimates는 다른 도구로, not_found는 이름 재확인, db_error는 재시도.

        ref: price_multiple_data, financial_metrics, dividend_disclosure, company
        """
        if (universe or "").strip():
            payload = await build_revision_screen_payload(
                universe=universe, window=window, period_type=period_type, format=format)
            if format == "json":
                return as_pretty_json(payload)
            if payload.get("status") not in ("ok", "no_estimates") or not (payload.get("data") or {}).get("rows"):
                return _render_status(payload)
            return _render_revision_screen(payload)
        payload = await build_forward_estimates_payload(
            company=company, bundle=bundle, period_type=period_type,
            actual_years=actual_years, format=format)
        return render_payload(payload, format)
