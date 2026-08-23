"""trading_data public tool — 주가·시총·상장주식수 시계열 + 시장·섹터 시총 + 단일시점 시세."""

from __future__ import annotations

from typing import Any

from open_proxy_mcp.market_codes import to_label as mkt_label
from open_proxy_mcp.services.contracts import as_pretty_json
from open_proxy_mcp.services.trading import (
    build_cap_agg_payload,
    build_firm_series_payload,
    build_quote_payload,
)

_STATUS_TITLE = {
    "invalid": "입력 오류",
    "not_found": "조회 결과 없음",
    "unlisted": "비상장 — 시세 없음",
    "ambiguous": "회사 식별 모호 — 후보에서 선택",
    "no_data": "데이터 없음",
    "db_error": "DB 연결 실패 (일시 장애)",
}


def _조(v):
    return f"{(v or 0)/1e12:,.1f}조"


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


def _render_firm(p: dict[str, Any]) -> str:
    d = p["data"]
    L = [f"# {p['subject']} 시세 시계열 [{mkt_label(d['market'])}]", "",
         f"기준 {d['as_of']} · {d['from']}~ {d['points']}개 주간 관측", ""]
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
    L += ["", f"> 📈 전 구간 주간 곡선 {d['points']}개 = `data.series`. 위 표는 월말 다운샘플."]
    if d.get("adj_events"):
        L.append(f"> ⚠ 기준가 조정 {len(d['adj_events'])}회 — "
                 + ", ".join(f"{e['event_dd']}(×{e['adj_factor']:g})" for e in d["adj_events"][:8]))
    L += ["", f"> {d['method']}"] + [f"> {w}" for w in p.get("warnings", [])]
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
    L += ["", f"> 📈 전 구간 주간 곡선 {d['points']}시점 = `data.series`.", "", f"> {d['method']}"]
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
         f"| 대비 | {n('change_krw')} ({q.get('change_pct')}%) |",
         f"| 시가 / 고가 / 저가 | {n('open_krw')} / {n('high_krw')} / {n('low_krw')} |",
         f"| 거래량 | {n('volume', '주')} |",
         f"| 거래대금 | {_조(q.get('value_krw'))} |",
         f"| 시가총액 | {_조(q.get('mktcap_krw'))} |",
         f"| 상장주식수 | {n('list_shrs', '주')} |", "",
         f"> {d['method']}"]
    L += [f"> {w}" for w in p.get("warnings", [])]
    return "\n".join(L)


def register_tools(mcp):

    @mcp.tool()
    async def trading_data(company: str = "", scope: str = "firm", format: str = "md",
                           as_of: str = "", since: str = "", scheme: str = "wics_industry",
                           bucket: str = "", freq: str = "") -> str:
        """desc: 거래·규모 데이터 — 종목의 주가·시가총액·상장주식수 시계열(주간, 2015-12~), 시장·섹터 시총 집계 시계열, 특정 거래일 전체 시세(OHLC·거래량·거래대금·등락률). KRX 정보데이터시스템 공식값.
        when: "주가 추이"·"시총 얼마"·"상장주식수 변화"(scope=firm) / "코스피 전체 시총"·"시장 규모 추이"(market) / "업종별 시총"·"반도체 섹터 비중"(sector, bucket 으로 특정 섹터 시계열) / "그날 거래량·거래대금·시고저가"(quote, as_of=YYYYMMDD). **PER·PBR·배당수익률 배수는 `price_multiple_data`** — 여기는 가격·규모 그 자체만.
        rule: scope=firm/market/sector = Supabase 저장분(krx_weekly · krx_cap_agg) — DART·KRX 0콜. scope=quote 만 KRX 라이브(최대 2콜, 오늘분은 캐시 적중 시 0콜). **`close_krw` 는 수정주가가 아니다** — 액면분할·병합 시점에 불연속이며 산출물이 `price_adjusted:false` 와 조정 이벤트 목록으로 명시한다. 연속 비교에는 `mktcap_krw`(조정 불변)를 쓴다. **시총 = 그 날 상장 전 종목(우선주 포함) 합** — `price_multiple_data` 의 Σ시총(배수 분모를 가진 종목만)보다 3~4% 크다. 섹터는 WICS 이며 미분류 종목을 버리지 않고 `_UNCLASSIFIED` 로 남겨 **섹터 합 == 시장 합**이 성립한다. 업종분류는 2026-08 부터 관측이라 그 이전은 소급(sector_asof 로 명시). 시계열 해상도 `freq` — 기본은 firm=weekly / market·sector=monthly(집계는 주간 해상도가 payload 만 4배로 키우고 알려주는 게 없다). `data.points_weekly` 로 원본 관측수를 함께 준다. md 표는 다시 월말·연말 발췌. 값 raw KRX int(_krw).
        status: ok / invalid / not_found(우선주는 그 우선주 코드로 직접) / unlisted / no_data(휴장일·상장 전·배치 미실행) / db_error.
       
        ref: price_multiple_data, screener, financial_metrics
        """
        sc = (scope or "firm").strip().lower()
        if sc == "firm":
            payload = await build_firm_series_payload(company, format=format, since=since, freq=freq)
        elif sc == "quote":
            payload = await build_quote_payload(company, format=format, as_of=as_of)
        elif sc == "market":
            payload = await build_cap_agg_payload("market", format=format, since=since, freq=freq)
        elif sc == "sector":
            payload = await build_cap_agg_payload(scheme, format=format, since=since,
                                                  bucket=bucket, freq=freq)
        else:
            payload = {"tool": "trading_data", "status": "invalid", "subject": scope,
                       "warnings": [f"scope '{scope}' 없음 — firm / quote / market / sector 중 선택."]}
        if format == "json":
            return as_pretty_json(payload)
        if payload.get("status") != "ok":
            return _render_status(payload)
        out = payload["data"]["scope"]
        return {"firm": _render_firm, "quote": _render_quote,
                "market": _render_market, "sector": _render_sector}[out](payload)
