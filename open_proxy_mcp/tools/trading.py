"""trading_data public tool — 주가·시총·상장주식수 시계열 + 시장·섹터 시총 + 단일시점 시세."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.market_codes import to_label as mkt_label
from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.tools._shared import krw_scaled
from open_proxy_mcp.services.trading_screen import (
    build_index_payload,
    build_quote_rank_payload,
    build_rate_payload,
    index_display_name,
)
from open_proxy_mcp.services.trading import (
    build_cap_agg_payload,
    build_firm_series_payload,
    build_quote_payload,
    build_universe_payload,
    declare_inferred,
)

_STATUS_TITLE = {
    "invalid": "입력 오류",
    "not_found": "조회 결과 없음",
    "unlisted": "비상장 — 시세 없음",
    "ambiguous": "회사 식별 모호 — 후보에서 선택",
    "no_data": "데이터 없음",
    "db_error": "DB 연결 실패 (일시 장애 — 잠시 후 재시도)",
    # ★ 위와 반드시 갈라 둔다. 「일시 장애」는 재시도가 답이지만 미연결은 **재시도가 답이 아니다.**
    #   한 문구로 합치면 영원히 안 될 조회를 계속 다시 부르게 된다(260828 U 지적 A-1).
    "db_unconfigured": "스냅샷 DB 미연결 — 이 서버에서는 제공되지 않음",
}


_조 = krw_scaled   # md 표기 전용 — 값 크기에 맞춰 조원/억원/원 (260828)


def _render_status(p: dict[str, Any]) -> str:
    lines = [f"# trading_data: {p.get('subject', '')} — "
             f"{_STATUS_TITLE.get(p.get('status'), p.get('status'))}", ""]
    lines += [f"- {w}" for w in p.get("warnings", [])]
    for c in (p.get("data") or {}).get("candidates") or []:
        lines.append(f"- 후보: {c.get('corp_name')} `{c.get('stock_code') or '-'}` / `{c.get('corp_code')}`")
    return "\n".join(lines)


def _monthly(series: list[dict], key: str = "asof") -> list[dict]:
    """월말 다운샘플 — 전 구간 555주를 표로 쏟지 않는다(JSON 에는 전량 남는다)."""
    out: dict[str, dict] = {}
    for s in series:
        out[s[key][:6]] = s          # 같은 달이면 뒤가 이김 = 그 달 마지막 관측
    return [out[k] for k in sorted(out)]


#: 응답 `data.freq`(실제 해상도) → 머리말 낱말. 261031: 종전에는 해상도와 상관없이 「주간」이라 적었다.
_FIRM_FREQ_WORD = {"daily": "일간", "weekly": "주간", "monthly": "월말"}


def _render_firm(p: dict[str, Any]) -> str:
    d = p["data"]
    L = [f"# {p['subject']} 시세 시계열 [{mkt_label(d['market'])}]", "",
         f"기준 {d['as_of']} · {d['from']}~ {d['points']}개 {_FIRM_FREQ_WORD.get(d.get('freq'), '주간')} 관측"
         + (f"(일간은 {d['daily_from']}~, 그 앞은 주간)" if d.get("daily_from") and d["from"] < d["daily_from"] else ""), ""]
    lt = d["latest"]
    L += ["| 항목 | 값 |", "|---|---|",
          f"| 종가 | {lt['close_krw']:,}원 |",
          f"| 시가총액 | {_조(lt['mktcap_krw'])} |",
          f"| 상장주식수 | {lt['list_shrs']:,}주 |", ""]
    rows = _monthly(d["series"])[-36:]
    L += ["## 월말 추이 (최근 36개월)", "",
          "| 월 | 종가 | 시총 | 상장주식수 |", "|---|---|---|---|"]
    for s in reversed(rows):
        L.append(f"| {s['asof'][:4]}-{s['asof'][4:6]} | {s['close_krw']:,} | "
                 f"{_조(s['mktcap_krw'])} | {s['list_shrs']:,} |")
    L += ["", (f"> 📈 일간 곡선 {d['points']}개 = `data.series`(일간 {d['daily_from']}~). 위 표는 월말 다운샘플."
               if d.get("daily_from") else
               f"> 📈 {_FIRM_FREQ_WORD.get(d.get('freq'), '주간')} 곡선 {d['points']}개(최근 3년) = `data.series`. 위 표는 월말 다운샘플.")]
    # 호가단위 맞춤(`tick_rounding`)은 불연속이 아니라 세지 않는다 — 경고 줄(아래)이 따로 밝힌다(261019).
    real = [e for e in d.get("adj_events") or [] if not e.get("tick_rounding")]
    if real:
        L.append(f"> ⚠ 기준가 조정 {len(real)}회 — "
                 + ", ".join(f"{e['event_dd']}(×{e['adj_factor']:g})" for e in real[:8]))
    L += ["", f"> {d['method']}"] + [f"> {w}" for w in p.get("warnings", [])]
    return _cold_span("\n".join(L), d)


def _cold_span(md: str, d: dict[str, Any]) -> str:
    """261019 콜드 서빙 — 3년 하한 이전을 보관본에서 이었으면 곡선 안내의 「(최근 3년)」을 실제 시작일·이음매로(보관본에 그 종목 행이 없었으면 시작일만).
    잇지 않은 응답(꺼짐·강등)은 손대지 않는다."""
    cu = d.get("cold_until")
    if not cu:
        return md
    span = f"{d['from']}~ — {cu} 이전은 보관본" if d["from"] < cu else f"{d['from']}~"
    return md.replace("개(최근 3년) = `data.series`", f"개({span}) = `data.series`", 1)


def _render_firm_live(p: dict[str, Any]) -> str:
    """DB 미연결 폴백 — 최신 1시점만. **시계열이 아니라는 것**을 표 위아래로 두 번 말한다."""
    d = p["data"]; lt = d["latest"]
    def n(v, unit="원"):
        return f"{v:,}{unit}" if v is not None else "-"
    L = [f"# {p['subject']} 최신 시세 [{d['market']}]", "",
         f"> ⛔ **시계열 없음** — 이 서버에는 스냅샷 DB 가 연결돼 있지 않습니다. "
         f"아래는 KRX 에서 직접 받아온 **{d['as_of']} 한 시점**입니다.", "",
         "| 항목 | 값 |", "|---|---|",
         f"| 기준일 | {d['as_of']} |",
         f"| 종가 | {n(lt.get('close_krw'))} |",
         f"| 시가총액 | {_조(lt.get('mktcap_krw'))} |",
         f"| 상장주식수 | {n(lt.get('list_shrs'), '주')} |",
         f"| 출처 | {d.get('source')} |", "",
         f"> {d['method']}"]
    L += [f"> {w}" for w in p.get("warnings", [])]
    return "\n".join(L)


def _render_market(p: dict[str, Any]) -> str:
    d = p["data"]
    L = [f"# 시장 시가총액 — KOSPI·KOSDAQ (기준 {d['as_of']})", "",
         "| 시장 | 시가총액 | 종목수 |", "|---|---|---|"]
    for s in d["latest"]:
        L.append(f"| {mkt_label(s['market'])} | {_조(s['cap_krw'])} | {s['n']:,} |")
    tot = sum(s["cap_krw"] or 0 for s in d["latest"])
    L.append(f"| **합계** | **{_조(tot)}** | **{sum(s['n'] for s in d['latest']):,}** |")
    ms = _monthly([s for s in d["series"]])
    yearly = {}
    for s in d["series"]:
        if s["asof"][4:6] == "12":
            yearly.setdefault(s["asof"][:4], []).append(s)
    if yearly:
        L += ["", "## 연말 추이", "", "| 연말 | KOSPI | KOSDAQ |", "|---|---|---|"]
        for yr in sorted(yearly):
            by = {mkt_label(s["market"]): s for s in yearly[yr]}
            L.append(f"| {yr} | {_조(by.get('KOSPI', {}).get('cap_krw'))} "
                     f"| {_조(by.get('KOSDAQ', {}).get('cap_krw'))} |")
    # 시계열이 없을 수도 있다(DB 미연결 → KRX 라이브 폴백). 그때 "전 구간 곡선" 이라고 쓰면 거짓말이다.
    L += ["", (f"> 📈 전 구간 주간 곡선 {d['points']}시점 = `data.series`." if d.get("series")
               else "> ⛔ **시계열 없음** — 위는 KRX 에서 직접 받아온 한 시점입니다."),
          "", f"> {d['method']}"]
    L += [f"> {w}" for w in p.get("warnings", [])]
    return "\n".join(L)


def _render_sector(p: dict[str, Any]) -> str:
    d = p["data"]
    L = [f"# 섹터 시가총액 ({d['scheme_desc']} · 기준 {d['as_of']})", ""]
    want = d.get("bucket")
    for mkt in ("KS", "KQ"):
        rows = [b for b in d["buckets"] if b["market"] == mkt]
        if not rows:
            continue
        tot = sum(b["cap_krw"] or 0 for b in rows) or 1
        # bucket 을 지정했다는 건 **그 섹터를 물은 것**이다. 57행을 앞에 쏟으면 답이 묻힌다
        #   — 맥락으로 상위 5개만 두고 지정 섹터를 끼워 넣는다(전체 표는 bucket 없이).
        note = ""
        if want:
            top = rows[:5]
            if not any(b["label"] == want for b in top):
                top += [b for b in rows if b["label"] == want]
            rows, note = top, " (시총 상위 5 + 지정 섹터 — 전체 표는 bucket 없이)"
        L += [f"## {mkt_label(mkt)}{note}", "", "| 섹터 | 시총 | 비중 | 종목수 |", "|---|---|---|---|"]
        for b in rows:
            mark = " ◀" if want and b["label"] == want else ""
            L.append(f"| {b['label']}{mark} | {_조(b['cap_krw'])} | "
                     f"{(b['cap_krw'] or 0)/tot:.1%} | {b['n']:,} |")
        L.append("")
    if d.get("series"):
        yearly = {}
        for s in d["series"]:
            if s["asof"][4:6] == "12":
                yearly.setdefault(s["asof"][:4], []).append(s)
        if yearly:
            L += [f"## {d['bucket']} — 연말 추이", "", "| 연말 | KOSPI | KOSDAQ |", "|---|---|---|"]
            for yr in sorted(yearly):
                by = {mkt_label(s["market"]): s for s in yearly[yr]}
                L.append(f"| {yr} | {_조(by.get('KOSPI', {}).get('cap_krw'))} "
                         f"| {_조(by.get('KOSDAQ', {}).get('cap_krw'))} |")
            L += ["", f"> 📈 전 구간 주간 곡선 {len(d['series'])}행 = `data.series`."]
    else:
        L.append("> 특정 섹터의 시계열은 `bucket=\"섹터명\"` 으로 요청하세요.")
    L += ["", f"> {d['method']}"] + [f"> {w}" for w in p.get("warnings", [])]
    return "\n".join(L)


def _render_quote(p: dict[str, Any]) -> str:
    d = p["data"]; q = d["quote"]
    def n(k, unit="원"):
        v = q.get(k)
        return f"{v:,}{unit}" if v is not None else "-"
    L = [f"# {p['subject']}", "", "| 항목 | 값 |", "|---|---|",
         f"| 종가 | {n('close_krw')} |",
         f"| 대비 | {n('change_krw')} ({'-' if q.get('change_pct') is None else str(q['change_pct']) + '%'}) |",
         f"| 시가 / 고가 / 저가 | {n('open_krw')} / {n('high_krw')} / {n('low_krw')} |",
         f"| 거래량 | {n('volume', '주')} |",
         f"| 거래대금 | {_조(q.get('value_krw'))} |",
         f"| 시가총액 | {_조(q.get('mktcap_krw'))} |",
         f"| 상장주식수 | {n('list_shrs', '주')} |", "",
         f"> {d['method']}"]
    L += [f"> {w}" for w in p.get("warnings", [])]
    return "\n".join(L)


_UNIVERSE_MD_MAX = 300   # md 표 상한 — 전체시장(2,700여 종목)은 json 의 data.rows
_RATE_MD_MONTHS = 13     # 금리 md 표의 월말 추이 개월 수 로


def _render_universe(p: dict[str, Any]) -> str:
    d = p["data"]
    rows = d["rows"]
    shown = rows[:_UNIVERSE_MD_MAX]
    wcol = bool(d.get("weight_basis"))        # 지수(코스피200·코스닥150) 구성종목 원장 표 — 시총 비중 칸
    L = [f"# {d['label']} — 시가총액 순위 (기준 {d['as_of']} · {d['n']:,}종목)", "",
         "| 순위 | 종목 | 코드 | 시장 | 시가총액 | " + ("시총 비중 | " if wcol else "") + "종가 |",
         "|---|---|---|---|---|" + ("---|" if wcol else "") + "---|"]
    for r in shown:
        close = f"{r['close_krw']:,}원" if r.get("close_krw") is not None else "-"
        w = (f"{r['weight_pct']:.2f}% | " if r.get("weight_pct") is not None else "- | ") if wcol else ""
        L.append(f"| {r['rank']} | {r['name']} | `{r['ticker']}` | {r['market']} | "
                 f"{_조(r['mktcap_krw'])} | {w}{close} |")
    if len(rows) > len(shown):
        L += ["", f"> 표는 상위 {len(shown):,}종목까지. 전체 {len(rows):,}종목은 format=\"json\" 의 data.rows."]
    if wcol:
        L += ["", f"> {d['weight_basis']}"]
    L += ["", f"> {d['method']}"] + [f"> {w}" for w in p.get("warnings", []) if w != d.get("weight_basis")]
    return "\n".join(L)


def _render_universe_sector(p: dict[str, Any]) -> str:
    """업종 구성·비중표(261021) — 순위·종목·시총·업종 안 비중·누적. 대분류는 중분류별 구성을 함께."""
    d = p["data"]
    sec = d["sector"]
    rows = d["rows"]
    shown = rows[:_UNIVERSE_MD_MAX]
    scope = f"{sec['level']} {sec['name']}" + (f" · {sec['market']}" if sec.get("market") else "")
    head = (f"# {scope} — 구성 종목·시총 비중 (기준 {d['as_of']}{' · 주간 시세' if d.get('granularity') == 'weekly' else ''}"
            f" · {sec['n']:,}종목 · 시총 {_조(sec['cap_krw'])})")
    L = [head, ""]
    L += [f"- 시장 시총 대비 {sec['share_of_market_pct']:.2f}% · 분류 스냅샷 {sec['classification_asof'] or '-'}"
          + (f" · 우선주 {sec['preferred_n']}종목 제외(시총 {_조(sec['preferred_cap_krw'])} — 우선주 포함 합 {_조(sec['cap_incl_preferred_krw'])})"
             if sec.get("preferred_n") else ""), ""]
    if sec.get("children"):
        L += ["## 중분류 구성", "", "| 중분류 | 종목수 | 시총 | 비중 |", "|---|---|---|---|"]
        for c in sec["children"]:
            L.append(f"| {c['name']} | {c['n']:,} | {_조(c['cap_krw'])} | {c['weight_pct']:.2f}% |")
        L.append("")
    mid_col = bool(rows and "mid" in rows[0])
    L += ["| 순위 | 종목 | 코드 | 시장 | " + ("중분류 | " if mid_col else "") + "시가총액 | 비중 | 누적 | 종가 |",
          "|---|---|---|---|" + ("---|" if mid_col else "") + "---|---|---|---|"]
    for r in shown:
        close = f"{r['close_krw']:,}원" if r.get("close_krw") is not None else "-"
        name = r["name"]
        L.append(f"| {r['rank']} | {name} | `{r['ticker']}` | {r['market']} | " + (f"{r.get('mid') or '-'} | " if mid_col else "")
                 + f"{_조(r['mktcap_krw'])} | {r['weight_pct']:.2f}% | {r['cum_weight_pct']:.2f}% | {close} |")
    if len(rows) > len(shown):
        L += ["", f"> 표는 상위 {len(shown):,}종목까지. 전체 {len(rows):,}종목은 format=\"json\" 의 data.rows."]
    L += ["", f"> {d['method']}"] + [f"> {w}" for w in p.get("warnings", [])]
    return "\n".join(L)


def _px(v: float | None) -> str:
    return f"{v:,.2f}" if v is not None else "-"


def _sg(v: float | None, unit: str = "%") -> str:
    return f"{v:+,.2f}{unit}" if v is not None else "-"


def _render_index(p: dict[str, Any]) -> str:
    """지수 한 시점 — 한 지수면 요약 줄 + 기간 표, 여러 지수(이름 없이 부름)면 한 표."""
    d = p["data"]
    rows = d["indices"]
    L = []
    if len(rows) == 1:
        r = rows[0]
        L += [f"# {index_display_name(r['market'], r['name'])} — {_px(r['close'])} 포인트 (기준 {r['as_of']} 종가)", ""]
        if r.get("change") is not None:
            L.append(f"- 전일({r['prev_dd']}) 대비 {_sg(r['change'], '')}포인트 ({_sg(r['change_pct'])}) — 전일 종가 {_px(r['prev_close'])}")
        L += ["", "| 기간 | 수익률 | 기준일 | 기준 종가 |", "|---|---|---|---|"]
        for pr in r["periods"].values():
            L.append(f"| {pr['label']} | {_sg(pr['return_pct'])} | {pr['base_dd'] or '-'} | {_px(pr['base_close'])} |")
    else:
        L += [f"# 대표 지수 (기준 {d['as_of']} 종가)", "",
              "| 지수 | 시장 | 종가 | 전일 대비 | 등락률 | 1주 | 1개월 | 연초 이후 | 1년 |", "|---|---|---|---|---|---|---|---|---|"]
        for r in rows:
            pr = r["periods"]
            L.append(f"| {r['name']} | {r['market']} | {_px(r['close'])} | {_sg(r['change'], '')} | {_sg(r['change_pct'])} | "
                     f"{_sg(pr['1w']['return_pct'])} | {_sg(pr['1m']['return_pct'])} | {_sg(pr['ytd']['return_pct'])} | {_sg(pr['1y']['return_pct'])} |")
        L += ["", "> 다른 지수(업종 지수 등)는 `company=\"반도체\"` 처럼 이름(부분 일치)으로 부르세요."]
    L += ["", f"> {d['method']}"] + [f"> {w}" for w in p.get("warnings", [])]
    return "\n".join(L)


def _render_rate(p: dict[str, Any]) -> str:
    d = p["data"]
    lt = d["latest"]
    L = [f"# 국고채 1년 금리 — 연 {lt['rate']:.3f}% (기준 {lt['asof']})", "",
         "| 비교 시점 | 그때 금리 | 변화 |", "|---|---|---|"]
    for c in d["changes"]:
        L.append(f"| {c['label']} ({c['base_dd'] or '-'}) | "
                 f"{('%.3f%%' % c['base_rate']) if c['base_rate'] is not None else '-'} | "
                 f"{('%+.1fbp' % c['change_bp']) if c['change_bp'] is not None else '-'} |")
    rg = d["range"]
    L += ["", f"- 조회 구간 {rg['from']}~{rg['to']} ({rg['n']}일): 최저 {rg['min']['rate']:.3f}% ({rg['min']['asof']}) · "
              f"최고 {rg['max']['rate']:.3f}% ({rg['max']['asof']})"]
    month_end: dict[str, dict] = {}
    for s in d["series"]:
        month_end[s["asof"][:6]] = s
    rows = [month_end[k] for k in sorted(month_end)][-_RATE_MD_MONTHS:]
    if len(rows) > 1:
        L += ["", f"## 월말 추이 (최근 {len(rows)}개월)", "", "| 월 | 금리 |", "|---|---|"]
        for s in reversed(rows):
            L.append(f"| {s['asof'][:4]}-{s['asof'][4:6]} ({s['asof']}) | {s['rate']:.3f}% |")
    L += ["", f"> 📈 {d['freq']} 곡선 {d['points']}개 = `data.series`. 위 표는 월말 발췌.",
          f"> {d['method']}"] + [f"> {w}" for w in p.get("warnings", [])]
    return "\n".join(L)


def _w52(r: dict[str, Any], hi: bool) -> str:
    if r.get("week52_blocked"):
        return "산출 불가*"
    v, pc = (r.get("high52_krw"), r.get("from_high52_pct")) if hi else (r.get("low52_krw"), r.get("from_low52_pct"))
    return f"{v:,} ({pc:+.1f}%)" if v is not None and pc is not None else "-"


def _render_quote_rank(p: dict[str, Any]) -> str:
    """여러 종목 시세 순위표 — 정렬 기준·52주 고가/저가·신고가. 300행을 넘으면 양 끝만."""
    d = p["data"]
    rows = d["rows"]
    w = d["window52"]
    L = [f"# {d['label']} — 시세 순위표 ({d['sort']} · 기준 {d['as_of']} · {d['n']:,}종목)", "",
         "| 순위 | 종목 | 코드 | 종가 | 등락률 | 거래대금 | 거래량 | 52주 고가(대비) | 52주 저가(대비) | 신고가 |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    half = len(rows) // 2
    for i, r in enumerate(rows):
        if d["truncated"] and i == half:
            L.append(f"| … | 가운데 {d['omitted']:,}종목 생략 | | | | | | | | |")
        close = f"{r['close_krw']:,}" if r.get("close_krw") is not None else "-"
        pct = f"{r['change_pct']:+.2f}%" if r.get("change_pct") is not None else "-"
        vol = f"{r['volume']:,}" if r.get("volume") is not None else "-"
        nh = "★" if r.get("new_high52") else ("-" if r.get("week52_blocked") else "")
        L.append(f"| {r['rank']} | {r['name']} | `{r['ticker']}` | {close} | {pct} | {_조(r.get('value_krw'))} | {vol} | "
                 f"{_w52(r, True)} | {_w52(r, False)} | {nh} |")
    br = d["breadth"]
    L += ["", f"> 52주 창 {w['from']}~{w['to']}(기준일 −{w['days']}일). 신고가 ★ {d['new_high52_n']}종목 · 상승 {br['up']} · 하락 {br['down']} · 보합 {br['flat']}."]
    if d["week52_blocked_n"]:
        L.append(f"> * 산출 불가 {d['week52_blocked_n']}종목 — 52주 안에 액면분할·병합 같은 기준가 조정 이벤트가 있어 원 시세로는 52주 고가·저가·신고가를 낼 수 없다(조정 이벤트로 산출 불가).")
    if d["truncated"]:
        L.append(f"> 표는 정렬 상위 {len(rows) // 2}개와 하위 {len(rows) // 2}개까지. 전체 {d['n']:,}종목 중 가운데 {d['omitted']:,}종목은 생략.")
    L += ["", f"> {d['method']}"] + [f"> {x}" for x in p.get("warnings", [])]
    return "\n".join(L)


def register_tools(mcp):

    @mcp.tool()
    async def trading_data(company: str = "", scope: str = "firm", format: str = "md",
                           as_of: str = "", since: str = "", scheme: str = "중분류",
                           bucket: str = "", freq: str = "", universe: str = "", sort: str = "") -> str:
        """desc: 거래·규모 데이터 — 종목의 주가·시가총액·상장주식수 시계열(주간 — DB 3년 + 보관본: 그 이전(2015-12~)은 DB 밖 보관본에서 이어 붙이고 `data.cold_until`·`data.sources` 로 밝힘, 보관본을 못 읽으면 최근 3년 + 안내 · 끝점은 보관된 최신 거래일 — 연말 값은 전체 기간), 시장·섹터 시총 집계 시계열(2015-12~ 전 구간), 특정 거래일 전체 시세(OHLC·거래량·거래대금·등락률), 업종(WICS 대분류 10·중분류 28)의 구성 종목·시총·업종 안 비중, **공식 지수(코스피·코스닥·코스피 200·코스닥 150·업종 지수 등)의 최신 포인트·전일 대비·1주·1개월·연초 이후·1년 수익률**, **여러 종목의 시세 순위표(등락률·거래대금·거래량 정렬 + 52주 고가·저가·신고가)**, **국고채 1년 금리의 최신값과 추이**. KRX 정보데이터시스템 공식값.
        when: "주가 추이"·"시총 얼마"·"상장주식수 변화"(scope=firm — **일간 주가 차트는 freq="daily"**, 비우면 주간) / "코스피 전체 시총"·"시장 규모 추이"(market) / "업종별 시총"·"반도체 섹터 비중"(sector, bucket 으로 특정 섹터 시계열) / "그날 거래량·거래대금·시고저가"(quote, as_of=YYYYMMDD) / **"코스피 시총 상위 100 이 뭐야"·"코스닥 상위 50 목록"·"유니버스 뽑아줘"(universe, universe="코스피 시총 상위 100" — 종목 순위표. 시총 순위 목록은 여기가 유일한 일괄 조회이며 종목마다 firm 을 부르지 말 것)** / **"반도체 업종 구성 종목이 뭐야"·"IT 대분류에서 시총 비중 큰 종목"·"코스닥 바이오 업종 상위 10"·"작년 말 기준 반도체 업종 비중"(universe, universe="반도체 업종"·"중분류 반도체와반도체장비"·"대분류 IT"·"코스피 IT 섹터 상위 10", 날짜는 as_of — 업종 합이 아니라 **구성 종목과 종목별 비중**)** / **"오늘 코스피 몇 포인트"·"코스닥 1년 수익률"·"코스피 200 연초 이후"(index, company="코스피" — 이름은 부분 일치, 이름 없이 부르면 대표 지수 목록)** / **"코스피 상위 100 등락률 순위"·"오늘 거래대금 상위"·"52주 신고가 종목"(quote, company 없이 universe="코스피 시총 상위 100", sort="등락률"·"거래대금"·"거래량")** / **"국고채 1년 금리"·"금리 추이"(rate — 기준금리·환율은 DB 에 없다고 밝힌다)**. **PER·PBR·배당수익률 배수는 `price_multiple_data`** — 여기는 가격·규모 그 자체만.
        rule: scope=firm = 주간 시세 저장분(krx_weekly, 최근 3년 — 그 이전(2015-12~)은 DB 밖 보관본 서빙본을 앞에 잇고 이음매는 `data.cold_until`) + 끝점(최신값)은 일별 시세 저장분의 그 종목 최신 거래일 한 줄(주간 마지막 점보다 늦을 때만 — 같은 주면 그 점을 교체(주 = ISO 주를 달력 연도로 자른 것), 더 늦은 주면 추가) · scope=market/sector = 시총 집계 저장분(krx_cap_agg) — 모두 DART·KRX 0콜. scope=quote 는 **최근 약 1년(최신 거래일 − 1년 − 1주)이 서버 DB 저장분**(KRX 0콜 — 서버가 매 거래일 아침 백그라운드로 채운다), 그 이전 날짜만 KRX 라이브(최대 2콜, 같은 날짜는 캐시되어 1회). 보관 창 안의 휴장일(주말·거래일 달력에 휴장으로 적힌 평일)은 KRX 를 부르지 않고 no_data + `data.market_closed:true`·앞뒤 거래일(`prev_trading_day`·`next_trading_day`)로 답한다. 보관 창 안의 등락률·대비는 함께 보관한 KRX 공표값(기준가 대비 — 휴장 다음 날·권리락일에도 KRX 와 같다)이다. **`close_krw` 는 수정주가가 아니다** — 액면분할·병합 시점에 불연속이며 산출물이 `price_adjusted:false` 와 조정 이벤트 목록으로 명시한다(기준가를 호가단위에 맞춘 것뿐인 날은 `adj_events[].tick_rounding:true` — 불연속으로 세지 않는다). 연속 비교에는 `mktcap_krw`(조정 불변)를 쓴다. **시총 = 그 날 상장 전 종목(우선주 포함) 합** — `price_multiple_data` 의 Σ시총(배수 분모를 가진 종목만)보다 3~4% 크다. 섹터는 업종분류(기본 중분류)이며 미분류 종목을 버리지 않고 `_UNCLASSIFIED` 로 남겨 **섹터 합 == 시장 합**이 성립한다. 업종분류는 2026-08 부터 관측이라 그 이전은 소급(sector_asof 로 명시). 시계열 해상도 `freq` — daily / weekly / monthly(일간·주간·월간도 받는다), 기본은 firm=weekly / market·sector=monthly(집계는 주간 해상도가 payload 만 4배로 키우고 알려주는 게 없다). **firm 의 `freq="daily"`(일간 차트)는 일별 시세 저장분(최근 약 1년)의 전 거래일**이고, 요청 구간이 그보다 이르면 그 앞은 주간 점으로 잇는다(이음매 `data.daily_from`, `data.points_daily`). 시장·섹터 시총 집계에는 일간이 없어 주간으로 주고 경고한다. 모르는 해상도 말은 invalid. **응답의 `data.freq` 는 실제로 준 해상도다** — 차트 제목·축은 이 값을 따른다. `data.points_weekly` 로 원본(주간) 관측수를 함께 준다. md 표는 다시 월말·연말 발췌. 값 raw KRX int(_krw). scope=universe 는 `screener` 와 같은 유니버스 문법(「코스피 시총 상위 N」·「코스닥 상위 N」·「시총 상위 N」·「코스피200」·「코스닥150」·「코스피 전체」·이름/코드 나열)을 받아 시세 저장분 1콜로 순위·코드·이름·시장·시총·종가를 준다(DART 0콜). 「코스피200」·「코스닥150」은 **지수 구성종목 원장**(KRX 공식 지수 합계와 맞춰 본 구성, `data.members_dd` = 마지막 확인 거래일)에서 종목을 받아 원장 1콜이 더 든다 — 정원으로 자르지 않는다. 원장을 못 읽으면 그 시장 시총 상위 정원으로 대체하고 경고로 밝힌다. 지수·시장 이름 나열(「코스피200, 코스닥150」)은 합집합이고, 원장이 없는 지수 이름(KRX300 등)은 추측하지 않고 지수라고 되묻는다(invalid). 기준일은 보관된 최신 거래일(`data.as_of`)이지 오늘(장중)이 아니다. md 표는 300종목까지, 그 이상은 json. **업종 구성**(scope=universe 에 업종 말 — 「업종」「섹터」「대분류」「중분류」가 들어간 문구: 「반도체 업종」「중분류 반도체와반도체장비」「대분류 IT」「코스피 IT 섹터 상위 10」)은 그 날(as_of, 비우면 보관된 최신 거래일)의 시세 단면과 그 날 이하 가장 최근 업종분류 스냅샷을 맞대 **구성 종목·시총·업종 안 비중(`weight_pct`, 합 100%)·누적 비중·순위**를 준다(DART·KRX 0콜, DB 읽기만 — 새 표 없이 읽을 때 계산). 시장(코스피/코스닥)을 주면 그 시장 안의 업종이고 분모도 그 안의 합이다. 상위 N 으로 잘라도 비중 분모는 업종 전체 시총이다(잘린 표의 합 < 100%, `data.sector.shown_weight_pct`). 요청일이 휴장이면 앞 거래일로 풀고(`data.as_of` 가 실제 기준일, `requested_as_of` 가 요청일) 일별 보관 창(약 1년)보다 오래면 주간 시세(주 마지막 거래일 한 점 — `data.granularity:"weekly"`)에서 읽는다. 주간 시세 구간(최근 약 3년)보다 이르면 invalid 로 안내한다(`scope="sector"` 합계 시계열은 2015-12~). **우선주는 싣지 않는다**(순위표와 같다) — 우선주는 업종·성과 분석 대상이 아니고 괴리율·배당 분석에만 쓰므로 종목 코드로 부르는 개별 시세(`scope="firm"`·`quote`)만 있으면 된다. 우선주 판정은 업종분류가 보통주 업종을 이어 준 행(`src='pref'`)이거나 분류가 없는 행의 종목코드 끝자리 규칙이다. 순위·비중(`weight_pct`)·누적 비중·업종 시총(`data.sector.cap_krw`)·시장 몫(`share_of_market_pct`)의 분모는 모두 **보통주** 시총 합이고, 뺀 우선주 수·시총은 경고 한 줄과 `data.sector.preferred_n`·`preferred_cap_krw` 로 밝힌다. `scope="sector"` 시총 집계(`krx_cap_agg`)는 우선주를 포함한 합이라 같은 날 같은 업종·시장이어도 업종 구성의 시총 합과 **우선주 몫만큼 다르다** — 업종 구성 시총 + 우선주 몫 = `data.sector.cap_incl_preferred_krw` 가 집계 값과 같은 정의의 수다. 업종 안 종목이 우선주뿐이면 빈 표 대신 그 사실을 알린다(no_data). 업종분류는 2026-08-21 부터 관측이라 그 이전 날짜는 첫 관측을 **소급**한 것이고 경고한다(그때 그 업종이 아니었던 회사가 섞이고 상장폐지 종목은 빠짐 — 생존 편향). 분류 스냅샷에 없는 종목(신규 상장 등)은 `data.sector.unclassified` 로 밝히고 어느 업종에도 넣지 않는다. 업종 이름은 정확 → 별칭(바이오·헬스케어) → 부분 일치 순이며 **여럿에 걸리면 추측하지 않고 후보를 주며 되묻는다**(invalid). 대분류·중분류에 이름이 같고 그 날 구성도 같은 에너지·소재·유틸리티는 중분류로 풀어 밝힌다. 대분류는 `data.sector.children` 에 중분류별 시총·비중을 함께 준다. KSIC(한국표준산업분류) 기준 구성은 없다(KSIC 축은 20260821 동결 — 안내만). `as_of` 는 업종 구성과 `scope="quote"` 에서만 쓴다. **scope=index**(DART·KRX 0콜, 지수 일별 종가 저장분 — 가격지수이며 배당을 반영한 총수익지수가 아님)는 `company`(없으면 `universe`)에 공식 지수 이름을 받는다 — 띄어쓰기·가운뎃점·영문 kospi/kosdaq 는 무시하고 정확 일치를 먼저, 「코스닥 제조」처럼 시장 이름이 앞에 붙은 말은 그 시장의 계열로, 그 밖에는 부분 일치로 찾으며 **여럿에 걸리면 추측하지 않고 후보를 주며 되묻는다**(invalid — 같은 이름의 업종 지수가 코스피·코스닥 양쪽에 있다). 지수 사전에는 있지만 DB 에 일별 종가가 없는 계열(`in_db` 거짓 — 코스피 200 건설 등)은 no_data 로 밝히고 값을 만들지 않는다. 응답은 기준일(보관된 최신 거래일 — 오늘 장중이 아님)의 종가 포인트·전일 대비(포인트·%)와 1주·1개월·연초 이후·1년 수익률을 주며 각 수익률의 기준 날짜(`base_dd` — 목표일 이하의 가장 늦은 보관 종가)를 함께 적는다. 이름을 비우면 DB 에 종가가 있는 규모·시장 대표 지수 목록. `as_of` 는 그 날 이하 가장 늦은 보관 거래일. **scope=quote 순위표**(`company` 를 비우고 `universe` 를 준 호출 — 「company 가 필요합니다」 오류가 나지 않는다)는 `screener` 와 같은 유니버스 문법(업종 말 포함)을 받아 기준일(보관된 최신 거래일, `as_of` 는 일별 보관 창 안의 날짜만 — 창 밖은 KRX 를 종목마다 부르지 않고 invalid)의 종가·등락률(KRX 공표값 그대로)·거래대금·거래량·시총과 **52주 고가·저가·대비·신고가**를 준다. 정렬 `sort`: 등락률(기본·큰 순) / 하락률 / 거래대금 / 거래량. **52주 정의**: 기준일 −52주(364일) 이후 일별 보관 행의 고가 칸 최댓값·저가 칸 최솟값(고가·저가가 0 인 거래 없는 날은 뺀다), 대비 = 종가 ÷ 52주 고가(저가) − 1, 신고가 = 기준일 고가가 52주 고가와 같음. 보관된 시세가 52주보다 짧으면 실제 시작일을 적는다. **원 시세는 수정주가가 아니라서** 52주 창 안에 액면분할·병합 같은 기준가 조정 이벤트(수정계수 원장에서 호가단위 맞춤·제외 표시가 아닌 것)가 있는 종목은 52주 고가·저가·대비·신고가를 비우고 「조정 이벤트로 산출 불가」로 표시하며 그 종목 수를 표 아래에 적는다. 표·json 행은 300행까지(넘으면 정렬 상위·하위 절반씩, 잘린 사실과 전체 수 `data.n`·`data.omitted`). 코스피200·코스닥150 **지수 구성종목 표**(scope=universe)에는 시총 비중(`weight_pct`, 구성종목 시총 합 100%) 칸이 붙는다 — 유동비율을 반영한 공식 지수 비중이 아니다(원장을 못 읽어 시총 순위로 대체한 표에는 없음). **scope=rate**(DART·KRX 0콜)는 국고채 1년 금리(한국은행 ECOS, 일별 저장분)의 최신값과 1주·1개월·3개월·연초·1년 전 대비 변화(bp), `since`(비우면 최근 1년) 이후 추이(`freq` daily/weekly/monthly — 비우면 300점 이하 일별, 넘으면 주간)를 준다. 기준금리·원/달러 환율·다른 만기 금리는 DB 에 없다고 밝히고 값을 지어내지 않는다.
        status: ok / invalid / not_found(우선주는 그 우선주 코드로 직접) / unlisted / no_data(휴장일·상장 전·배치 미실행) / db_error(일시 장애 — 재시도 유효) / **db_unconfigured**(이 서버에 스냅샷 DB 미연결 — 재시도해도 안 됨. firm·market 은 KRX 라이브 최신 1시점으로 폴백하며 그때 `data.scope="firm_live"` · `timeseries_available:false` · `source:"KRX 라이브"` 가 붙는다. sector 는 업종분류 매핑이 DB 에만 있어 폴백 없음).
        params: company 는 종목명 또는 종목코드(KRX 단축코드 6자 — 영문이 섞인 신형 코드 0126Z0 포함). **우선주·종류주 코드(005935·00088K)는 회사를 발행 회사로 식별하되 firm·quote 의 값은 그 종류주 자체**의 종가·시총·상장주식수다(`data.issuer_ticker` 에 보통주 코드, 맨 앞 경고로 밝힌다). 「삼성전자우」 같은 우선주 **이름**은 회사 원장에 없어 보통주 회사로 추정되며 그 추정을 맨 앞 경고로 밝힌다 — 우선주 시세는 코드로 부른다. scope=index·rate 에서는 지수/금리 이름(비우면 목록·국고채 1년)으로도 쓴다. sort 는 quote 순위표 전용(등락률·하락률·거래대금·거래량, 비우면 등락률).
       
        ref: price_multiple_data, screener, financial_metrics
        """
        sc = (scope or "firm").strip().lower()
        if sc == "firm":
            payload = declare_inferred(
                await build_firm_series_payload(company, format=format, since=since, freq=freq))
        elif sc == "quote":
            if not (company or "").strip() and (universe or "").strip():
                payload = await build_quote_rank_payload(universe, sort=sort, as_of=as_of)
            else:
                payload = declare_inferred(await build_quote_payload(company, format=format, as_of=as_of))
        elif sc == "index":
            payload = await build_index_payload(company or universe, as_of=as_of)
        elif sc == "rate":
            payload = await build_rate_payload(company or universe, since=since, as_of=as_of, freq=freq)
        elif sc == "market":
            payload = await build_cap_agg_payload("market", format=format, since=since, freq=freq)
        elif sc == "sector":
            payload = await build_cap_agg_payload(scheme, format=format, since=since,
                                                  bucket=bucket, freq=freq)
        elif sc == "universe":
            payload = await build_universe_payload(universe or company, format=format, as_of=as_of)
        else:
            payload = {"tool": "trading_data", "status": "invalid", "subject": scope,
                       "warnings": [f"scope '{scope}' 없음 — `firm / quote / market / sector / universe / index / rate` 중 선택."]}
        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") != "ok":
            return _render_status(payload)
        out = payload["data"]["scope"]
        if out == "universe" and payload["data"].get("sector"):
            return _render_universe_sector(payload)
        if out == "quote" and payload["data"].get("view") == "universe":
            return _render_quote_rank(payload)
        return {"firm": _render_firm, "firm_live": _render_firm_live, "quote": _render_quote,
                "market": _render_market, "sector": _render_sector,
                "universe": _render_universe, "index": _render_index, "rate": _render_rate}[out](payload)
