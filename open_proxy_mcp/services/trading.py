"""trading_data — 주가·시총·상장주식수 시계열 + 시장·섹터 시총 집계 + 단일시점 시세.

`price_multiple_data`(배수)와 갈라져 있다. 저쪽은 **배수**(PER·PBR·배당수익률), 여기는 **양(量)** —
가격·시총·주식수 그 자체다. 두 축이 한 tool 에 있으면 「PER 시계열」과 「시총 시계열」이 같은
파라미터를 두고 다투게 되고, 실제로 그래서 갈랐다(260824).

★ 시총의 정의가 `opm_val_market.cap` 과 다르다. 저쪽은 배수 분모를 가진 종목만 더한 값이라
  분자·분모 모집단이 맞는 대신 시장 전체가 아니다(실측 3.8% 낮음). 여기 `cap` 은 그 날 상장된
  **전 종목**(우선주 포함) 합이다 — KRX 공표 시총과 같은 모집단. 표를 아예 나눠 뒀다(krx_cap_agg).

★ 저장 시계열에 OHLC·거래량·거래대금이 없다. `krx_weekly` 는 종가·시총·상장주식수 3개뿐이다.
  그 넷은 `scope=quote` 가 KRX 를 직접 불러 그 날짜만 준다(2콜).

★ **수정주가가 아니다.** `close` 는 그 날의 실제 종가다 — 액면분할·병합 전후로 끊긴다.
  배수(PER·PBR)는 시총 기반이라 불변이지만 **가격 시계열은 그렇지 않다.** 산출물이 그 사실을
  `price_adjusted: false` 로 명시하고, 구간 안에 조정 이벤트가 있으면 경고를 단다.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

from open_proxy_mcp.market_codes import to_label as mkt_label
from open_proxy_mcp.services.valuation import (
    _DB_ERROR_PAYLOAD_WARN,
    _KRX_CACHE,
    _num,
    _pg_rows,
    _resolve_listed,
)

TOOL = "trading_data"

_SCHEMES = {
    "market": "시장 전체 (KOSPI·KOSDAQ)",
    "wics_sector": "WICS 대분류 10 (WiseIndex)",
    "wics_industry": "WICS 하위업종 28 (WiseIndex)",
}

#: 단일시점 시세 캐시 — **종목 한 줄만** 담는다. 전종목 스냅샷을 `_KRX_CACHE`(32MB)에 넣으면
#: 임의 과거일 조회가 오늘 스냅샷을 밀어내고, 그 스냅샷은 `_market_for`(배수 산출)가 쓴다.
#: 남의 캐시를 오염시키지 않으려고 작은 장부를 따로 둔다(행 하나 ≈ 400B, 512행 ≈ 200KB).
_QUOTE_CACHE: dict[tuple[str, str], dict] = {}
_QUOTE_CACHE_MAX = 512


#: 시계열 해상도. 저장분은 **주간**이라 `weekly` 가 원본이고 `monthly` 는 월말 다운샘플이다.
#: 기본값이 스코프마다 다른 이유 — 종목 주가는 주 단위 움직임이 분석 대상이지만, 시장·섹터
#: **시총 집계**는 월 단위면 충분하다. 전 구간 주간이면 payload 가 134KB(≈35k 토큰)까지
#: 커지는데, 지수 수준에서 그 4배 해상도가 더 알려주는 것이 없다. 필요하면 명시로 올린다.
_FREQ_DEFAULT = {"firm": "weekly", "market": "monthly", "sector": "monthly"}


def _downsample(series: list[dict], freq: str) -> list[dict]:
    """월말 다운샘플. 시장·섹터는 (asof, market) 이 키라 **시장별로 따로** 접어야 한다 —
    한 덩어리로 접으면 같은 달의 KOSPI 가 KOSDAQ 에 덮여 한 시장이 통째로 사라진다."""
    if freq != "monthly":
        return series
    keep: dict[tuple, dict] = {}
    for s in series:
        keep[(s["asof"][:6], s.get("market"))] = s
    return [keep[k] for k in sorted(keep, key=lambda k: (k[0], k[1] or ""))]


def _err(subject: str, status: str, *warns: str) -> dict[str, Any]:
    return {"tool": TOOL, "status": status, "subject": subject, "warnings": list(warns)}


# ── 1. 종목 시계열 ────────────────────────────────────────────────────────────
async def build_firm_series_payload(company: str, format: str = "md",
                                    since: str = "", freq: str = "") -> dict[str, Any]:
    """한 종목의 종가·시총·상장주식수 시계열 (krx_weekly). DB 1콜."""
    if not (company or "").strip():
        return _err(company, "invalid", "company 가 필요합니다 — 종목명 또는 6자리 코드.")
    corp, early = await _resolve_listed(company)
    if early:
        early["tool"] = TOOL
        return early
    if not corp:
        return _err(company, "not_found", f"'{company}' 상장사를 찾지 못했습니다.")
    ticker = (corp.get("stock_code") or "").strip()
    if not ticker:
        return _err(corp.get("corp_name", company), "unlisted", "비상장 — 시세 시계열이 없습니다.")

    sql = ("SELECT price_dd, market, close, mktcap, list_shrs FROM krx_weekly "
           "WHERE ticker=%s" + (" AND price_dd>=%s" if since else "") + " ORDER BY price_dd")
    args = (ticker, since) if since else (ticker,)
    rows = await asyncio.to_thread(_pg_rows, sql, args)
    if rows is None:
        return _err(corp.get("corp_name", company), "db_error", _DB_ERROR_PAYLOAD_WARN)
    if not rows:
        return _err(corp.get("corp_name", company), "no_data",
                    f"krx_weekly 에 {ticker} 시계열이 없습니다 (신규상장·상장폐지 가능).")

    freq = (freq or _FREQ_DEFAULT["firm"]).strip().lower()
    full = [{"asof": r[0], "close_krw": r[2], "mktcap_krw": r[3], "list_shrs": r[4]} for r in rows]
    series = _downsample(full, freq)
    latest, first = full[-1], full[0]        # 최신·시작은 **원본** 기준(다운샘플이 잘라내도 사실은 그대로)

    # 조정 이벤트 — 가격 시계열은 수정주가가 아니라 여기서 끊긴다. 있으면 반드시 말한다.
    ev = await asyncio.to_thread(
        _pg_rows, "SELECT event_dd, adj_factor FROM krx_adj_events "
                  "WHERE ticker=%s AND event_dd>=%s AND event_dd<=%s AND adj_factor IS NOT NULL "
                  "AND adj_factor <> 1 ORDER BY event_dd",
        (ticker, first["asof"], latest["asof"])) or []

    warns = [f"**수정주가 아님** — `close_krw` 는 그 날 실제 종가입니다. 시총·주식수는 조정 전후로 "
             f"연속이지만 주가는 끊깁니다."]
    if ev:
        warns.append(f"⚠ 구간 내 기준가 조정 {len(ev)}회 "
                     f"({', '.join(f'{d}(×{c:g})' for d, c in ev[:5])}"
                     f"{' …' if len(ev) > 5 else ''}) — 가격 시계열이 그 지점에서 불연속입니다. "
                     f"연속 비교가 필요하면 `mktcap_krw` 를 쓰세요(조정 불변).")
    return {"tool": TOOL, "status": "ok",
            "subject": f"{corp.get('corp_name', company)}({ticker})",
            "data": {"scope": "firm", "ticker": ticker, "market": rows[-1][1],
                     "as_of": latest["asof"], "from": first["asof"], "points": len(series),
                     "freq": freq, "points_weekly": len(full),
                     "latest": latest, "series": series,
                     "price_adjusted": False,
                     "adj_events": [{"event_dd": d, "adj_factor": float(c)} for d, c in ev],
                     "method": "KRX 정보데이터시스템 → krx_weekly(주 마지막 거래일 보존, 매일 갱신). "
                               "mktcap=상장주식수×종가(우선주는 별도 종목). "
                               "OHLC·거래량·거래대금은 저장하지 않습니다 — `scope=quote` 참조."},
            "warnings": warns}


# ── 2. 시장·섹터 시총 집계 ────────────────────────────────────────────────────
async def build_cap_agg_payload(scheme: str = "market", format: str = "md",
                                since: str = "", bucket: str = "",
                                freq: str = "") -> dict[str, Any]:
    """시장 또는 섹터의 시총 시계열 (krx_cap_agg 사전계산). DB 1~2콜.

    섹터는 버킷이 28개라 전 구간 × 전 버킷이면 15,540행(≈1.5MB)이 된다. 그래서
      · 기본 = **최신 시점 전 버킷** + (bucket 지정 시) 그 버킷의 전 구간 시계열
    로 나눈다. 「어디가 큰가」와 「이 섹터가 어떻게 변했나」는 다른 질문이고, 둘을 한 번에
    다 부으면 읽는 쪽이 토큰만 태운다.
    """
    scheme = (scheme or "market").strip().lower()
    if scheme not in _SCHEMES:
        return _err(scheme, "invalid", f"scheme '{scheme}' 없음 — {' / '.join(_SCHEMES)} 중 선택.")

    if scheme == "market":
        sql = ("SELECT price_dd, market, cap, n FROM krx_cap_agg WHERE scheme='market'"
               + (" AND price_dd>=%s" if since else "") + " ORDER BY price_dd, market")
        rows = await asyncio.to_thread(_pg_rows, sql, (since,) if since else ())
        if rows is None:
            return _err("시장 시총", "db_error", _DB_ERROR_PAYLOAD_WARN)
        if not rows:
            return _err("시장 시총", "no_data", "krx_cap_agg 비어있음 — krx_cap_agg.py 배치 미실행.")
        freq = (freq or _FREQ_DEFAULT["market"]).strip().lower()
        full = [{"asof": r[0], "market": r[1], "cap_krw": r[2], "n": r[3]} for r in rows]
        series = _downsample(full, freq)
        as_of = full[-1]["asof"]
        return {"tool": TOOL, "status": "ok", "subject": "시장 시가총액 (KOSPI·KOSDAQ)",
                "data": {"scope": "market", "scheme": "market", "scheme_desc": _SCHEMES["market"],
                         "as_of": as_of, "points": len({s["asof"] for s in series}),
                         "freq": freq, "points_weekly": len({s["asof"] for s in full}),
                         "latest": [s for s in full if s["asof"] == as_of],
                         "series": series,
                         "method": "Σ시총 = 그 날 상장된 **전 종목**(우선주 포함) 합 — KRX 공표 시총과 "
                                   "같은 모집단. `price_multiple_data` 의 Σ시총(배수 분자)은 재무를 "
                                   "가진 종목만이라 3~4% 작습니다. 두 값은 서로 다른 질문의 답입니다."},
                "warnings": [f"주간 스냅샷 기준(최신 {as_of}) — 주 마지막 거래일."]}

    # ── 섹터 ──
    # 최신일 조회와 스냅샷 조회를 **한 왕복**으로 합친다. `_pg_rows` 는 호출마다 새 커넥션을
    #   열고(실측 핸드셰이크 124ms vs 질의 63ms — 연결이 질의보다 2배 비싸다) 프로덕션은
    #   1 CPU 라 `asyncio.to_thread` 풀이 5칸뿐이다. 왕복 하나가 곧 그 5칸 중 하나다.
    snap = await asyncio.to_thread(
        _pg_rows, "SELECT market, bucket, label, cap, n, sector_asof, price_dd FROM krx_cap_agg "
                  "WHERE scheme=%s AND price_dd=(SELECT max(price_dd) FROM krx_cap_agg WHERE scheme=%s) "
                  "ORDER BY market, cap DESC", (scheme, scheme))
    if snap is None:
        return _err("섹터 시총", "db_error", _DB_ERROR_PAYLOAD_WARN)
    if not snap:
        return _err("섹터 시총", "no_data", "krx_cap_agg 비어있음 — krx_cap_agg.py 배치 미실행.")
    as_of = snap[0][6]
    buckets = [{"market": r[0], "bucket": r[1], "label": r[2], "cap_krw": r[3], "n": r[4]}
               for r in snap]
    sector_asof = snap[0][5]

    series: list[dict] = []
    if bucket:
        want = bucket.strip()
        match = next((b for b in buckets
                      if want in (b["bucket"], b["label"]) or want.lower() == (b["label"] or "").lower()),
                     None)
        if not match:
            return _err(want, "not_found",
                        f"'{want}' 버킷이 {scheme} 에 없습니다. "
                        f"가능: {', '.join(sorted({b['label'] for b in buckets}))}")
        code = match["bucket"]
        sql = ("SELECT price_dd, market, cap, n FROM krx_cap_agg WHERE scheme=%s AND bucket=%s"
               + (" AND price_dd>=%s" if since else "") + " ORDER BY price_dd, market")
        args = (scheme, code, since) if since else (scheme, code)
        series = _downsample([{"asof": r[0], "market": r[1], "cap_krw": r[2], "n": r[3]}
                              for r in (await asyncio.to_thread(_pg_rows, sql, args) or [])],
                             (freq or _FREQ_DEFAULT["sector"]).strip().lower())
        bucket = match["label"]

    warns = [f"주간 스냅샷 기준(최신 {as_of})."]
    if sector_asof and sector_asof > as_of:
        warns.append(f"⚠ 업종분류는 {sector_asof} 관측을 과거에 **소급** 적용한 것입니다 "
                     "— 그때 그 업종이 아니었던 회사가 섞입니다.")
    unc = [b for b in buckets if b["bucket"] == "_UNCLASSIFIED"]
    if unc:
        tot = sum(b["cap_krw"] for b in buckets) or 1
        warns.append(f"미분류 {sum(b['n'] for b in unc)}종목 · 시총 "
                     f"{sum(b['cap_krw'] for b in unc)/tot:.1%} — 우선주·신규상장 등 WICS 구성종목에 "
                     "없는 것. 버리지 않고 `_UNCLASSIFIED` 로 남겨 섹터 합 = 시장 합을 유지합니다.")
    return {"tool": TOOL, "status": "ok", "subject": f"섹터 시가총액 ({_SCHEMES[scheme]})",
            "data": {"scope": "sector", "scheme": scheme, "scheme_desc": _SCHEMES[scheme],
                     "as_of": as_of, "sector_asof": sector_asof,
                     "buckets": buckets, "bucket": bucket or None, "series": series,
                     "freq": (freq or _FREQ_DEFAULT["sector"]).strip().lower(),
                     "method": "Σ시총 = 그 날 상장된 전 종목(우선주 포함) 합. 섹터 합 == 시장 합 "
                               "(미분류를 버리지 않으므로 항등). bucket 지정 시 그 섹터의 전 구간 시계열."},
            "warnings": warns}


# ── 3. 단일시점 시세 (KRX 라이브 — OHLC·거래량·거래대금·등락률) ────────────────
_QUOTE_FIELDS = (
    ("TDD_CLSPRC", "close_krw", "종가"), ("TDD_OPNPRC", "open_krw", "시가"),
    ("TDD_HGPRC", "high_krw", "고가"), ("TDD_LWPRC", "low_krw", "저가"),
    ("CMPPREVDD_PRC", "change_krw", "대비"), ("ACC_TRDVOL", "volume", "거래량"),
    ("ACC_TRDVAL", "value_krw", "거래대금"), ("MKTCAP", "mktcap_krw", "시가총액"),
    ("LIST_SHRS", "list_shrs", "상장주식수"),
)


async def _krx_quote_row(basDd: str, ticker: str) -> dict | None:
    """그 날짜 그 종목의 KRX 원본 행. 이미 캐시된 전종목 스냅샷이 있으면 공짜, 없으면 2콜.

    ★ 새로 받아온 전종목 스냅샷을 `_KRX_CACHE` 에 넣지 **않는다.** 그 캐시(32MB)는
      `_market_for` 가 쓰는 오늘 스냅샷을 담고 있고, 임의 과거일 조회가 그걸 밀어내면
      배수 산출이 매번 KRX 를 다시 부르게 된다. 여기서는 요청된 한 줄만 따로 기억한다.
    """
    hit = _QUOTE_CACHE.get((basDd, ticker))
    if hit:
        return hit
    cached = _KRX_CACHE.get(basDd)          # 일일 갱신이 이미 받아뒀으면 그대로 쓴다(0콜)
    if cached is not None:
        return cached.get(ticker)

    key = os.getenv("KRX_API_KEY") or os.getenv("KRX_OPEN_API_KEY")
    if not key:
        return None
    import httpx

    from open_proxy_mcp.dart.krx_meter import bump
    from open_proxy_mcp.services.valuation import _KRX_URL, _KSQ_URL

    async def _one(h, url):
        try:
            bump()
            r = await h.get(url, headers={"AUTH_KEY": key}, params={"basDd": basDd})
            return next((v for v in r.json().values() if isinstance(v, list)), [])
        except Exception:
            return []

    async with httpx.AsyncClient(timeout=30) as h:
        kospi, kosdaq = await asyncio.gather(_one(h, _KRX_URL), _one(h, _KSQ_URL))
    row = next((r for rows in (kospi, kosdaq) for r in rows if r.get("ISU_CD") == ticker), None)
    # ★ **빈 결과는 캐시하지 않는다.** KRX 는 이미 게시한 거래일을 일시적으로 0행으로 돌려줄 때가
    #   있다(260703 실측: 금요일 데이터가 이틀째 0행 / 260824 실측: 20260821 이 0행인데
    #   20260820 은 2,763행). 그 순간의 빈 응답을 캐시하면 프로세스가 살아 있는 동안 그 종목·
    #   그 날짜가 **영영 없는 것**이 된다 — 상류가 복구돼도 우리만 계속 못 본다.
    if row:
        if len(_QUOTE_CACHE) >= _QUOTE_CACHE_MAX:
            _QUOTE_CACHE.clear()            # 단순 상한 — 행이 작아 LRU 를 둘 값어치가 없다
        _QUOTE_CACHE[(basDd, ticker)] = row
    return row


async def build_quote_payload(company: str, format: str = "md",
                              as_of: str = "") -> dict[str, Any]:
    """특정 거래일 전체 시세 — OHLC·거래량·거래대금·등락률. KRX 최대 2콜(캐시 적중 시 0콜)."""
    if not (company or "").strip():
        return _err(company, "invalid", "company 가 필요합니다 — 종목명 또는 6자리 코드.")
    dd = (as_of or "").strip().replace("-", "")
    if dd and (len(dd) != 8 or not dd.isdigit()):
        return _err(company, "invalid", f"as_of '{as_of}' 형식 오류 — YYYYMMDD.")
    corp, early = await _resolve_listed(company)
    if early:
        early["tool"] = TOOL
        return early
    if not corp:
        return _err(company, "not_found", f"'{company}' 상장사를 찾지 못했습니다.")
    ticker = (corp.get("stock_code") or "").strip()
    if not ticker:
        return _err(corp.get("corp_name", company), "unlisted", "비상장 — 시세가 없습니다.")

    if not dd:
        # ★ 저장분의 최신일(`_ensure_krx_fresh`)을 그대로 KRX 에 묻지 않는다. krx_weekly 에 있는
        #   날짜를 KRX 가 0행으로 돌려줄 수 있어서다(260824 실측: 20260821 저장분은 있는데
        #   API 는 0행, 20260820 은 2,763행). **KRX 가 실제로 가진 최신 거래일**을 찾는다 —
        #   `_fetch_live_snapshot` 이 평일을 거슬러 올라가며 그걸 한다. 이 경로는 전종목
        #   스냅샷을 `_KRX_CACHE` 에 넣지만, 최신일 스냅샷은 `_market_for` 도 쓰는 것이라
        #   오염이 아니라 공유다(임의 과거일만 따로 처리한다).
        from open_proxy_mcp.services.valuation import _fetch_live_snapshot
        dd, snap = await _fetch_live_snapshot()
        if not dd:
            return _err(corp.get("corp_name", company), "no_data",
                        "KRX 최근 거래일 시세를 받지 못했습니다 (키 미설정·상류 장애).")
        row = snap.get(ticker)
    else:
        row = await _krx_quote_row(dd, ticker)
    if not row:
        return _err(corp.get("corp_name", company), "no_data",
                    f"{dd} 에 {ticker} 시세가 없습니다 — 휴장일·상장 전·거래정지 또는 KRX 키 미설정.")

    q = {out: _num(row.get(src)) for src, out, _ in _QUOTE_FIELDS}
    try:
        q["change_pct"] = float(str(row.get("FLUC_RT", "")).replace(",", "") or 0)
    except Exception:
        q["change_pct"] = None
    warns = []
    if not q.get("volume"):
        # 실측 함정(field_registry): 무거래일은 OHLC 가 0 으로 온다 — 종가 0 은 가격이 아니다.
        warns.append("⚠ 거래량 0 — 무거래일입니다. KRX 는 이 경우 시·고·저가를 0 으로 보냅니다"
                     "(가격이 0 이라는 뜻이 아닙니다).")
    return {"tool": TOOL, "status": "ok",
            "subject": f"{corp.get('corp_name', company)}({ticker}) {dd} 시세",
            "data": {"scope": "quote", "ticker": ticker, "as_of": dd,
                     "market": mkt_label(row.get("MKT_NM") or ""), "quote": q,
                     "price_adjusted": False,
                     "method": "KRX 정보데이터시스템 일별매매정보(stk/ksq_bydd_trd) 원본. "
                               "그 날짜 실제 체결가 — 수정주가가 아닙니다. "
                               "시가(TDD_OPNPRC)는 첫 체결가라 기준가와 다릅니다."},
            "warnings": warns}
