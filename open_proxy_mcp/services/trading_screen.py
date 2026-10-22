"""trading_data — 지수 조회(scope=index) · 금리 조회(scope=rate) · 여러 종목 시세 순위표(scope=quote + universe).

저장분에 이미 있는 자료만 읽는다(새 표·새 적재·외부 호출 없음 — DART 0콜, KRX 0콜):
  · 공식 지수 일별 종가 `krx_idx` + 계열 사전 `krx_idx_series`        → scope=index
  · 국고채 1년 금리 `ecos_rate`(series '1')                          → scope=rate
  · 일별 시세 저장분 `krx_daily_v`(최근 약 1년) + 수정계수 원장 `krx_adj_events` → scope=quote 순위표·52주 고가/저가

★ 52주 고가·저가는 **원 시세**(수정주가가 아니다)의 고가·저가 칸에서 읽는다. 52주 창 안에 액면분할·병합 같은 기준가 조정이 있으면 창 앞쪽 가격이
  다른 기준이라 고가·저가·대비가 틀린다(분할 0.1 이면 52주 고가가 현재가의 10배로 나온다). 그런 종목은 값을 만들지 않고 「조정 이벤트로 산출 불가」로 비운다.
  기준가를 호가단위에 맞춘 것뿐인 날(`tick_rounding`)은 기업 행위가 아니라 조정으로 세지 않는다 — `trading_data(scope=firm)` 과 같은 규칙.
★ 등락률은 KRX 공표값(`krx_chg_pct` — 기준가 대비, 휴장 다음 날·권리락일에도 KRX 와 같다) 그대로다. 우리가 다시 계산하지 않는다.
★ 기준일은 거래일 달력(`krx_day`)에서 먼저 구한다. 시세 뷰 위의 전체 MIN/MAX 는 쓰지 않는다(DATA-PX7).
"""
from __future__ import annotations

import asyncio
import calendar
import re
from datetime import date, timedelta
from typing import Any

from open_proxy_mcp import db
from open_proxy_mcp.krx_ticks import is_tick_rounding
from open_proxy_mcp.services.perf_data import SRC_LABEL, IndexSeries, _norm_index
from open_proxy_mcp.services.price_multiple_data import _db_call, _gather_db
from open_proxy_mcp.services.trading import TOOL, _db_missing_payload, _err

# ── 공통 ──────────────────────────────────────────────────────────────────────


def _pg(sql: str, params: tuple = ()) -> list[tuple] | None:
    """DB 읽기 한 번 — `db.pg_rows` 계약(None = 미설정·장애, [] = 데이터 없음). 시험이 `db.pg_rows` 를 바꿔 끼운다."""
    return db.pg_rows(sql, params)


def _to_date(dd: str) -> date:
    return date(int(dd[:4]), int(dd[4:6]), int(dd[6:]))


def _dd(d: date) -> str:
    return f"{d.year:04d}{d.month:02d}{d.day:02d}"


def _norm_dd(v: str) -> str | None:
    """'' → ''(비움), 2026-10-21·2026.10.21·20261021 → 20261021, 그 밖·없는 날짜 → None."""
    s = re.sub(r"[-./\s]", "", (v or ""))
    if not s:
        return ""
    if len(s) != 8 or not s.isdigit():
        return None
    try:
        _to_date(s)
    except ValueError:
        return None
    return s


def _months_back(d: date, n: int) -> date:
    y, m = d.year, d.month - n
    while m <= 0:
        m += 12
        y -= 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _years_back(d: date, n: int) -> date:
    y = d.year - n
    return date(y, d.month, min(d.day, calendar.monthrange(y, d.month)[1]))


_FAR = "2999-12-31"          # as_of 를 안 줬을 때 「상한 없음」으로 넘기는 날짜


def _base_at(rows: list[tuple[str, float]], target: str) -> tuple[str, float] | None:
    """rows(날짜 오름차순) 중 target(YYYYMMDD) 이하의 가장 늦은 관측. 없으면 None."""
    hit = None
    for r in rows:
        if r[0] <= target:
            hit = r
        else:
            break
    return hit


# ══════════════════════════════════════════════════════════════════════════════
#  scope=index — 공식 지수 한 시점
# ══════════════════════════════════════════════════════════════════════════════

#: 이름이 없을 때 보여 주는 대표 지수 — 계열 사전에서 DB 일별 종가가 있는(`in_db`) 규모·시장 대표(kind z) 계열.
_OVERVIEW_KIND = "z"

#: 한 지수의 최근 종가를 읽는다 — 계열마다 그 계열의 가장 늦은 거래일(상한 `as_of`)에서 400일 앞까지(1년 전 기준점 + 주간 간격 여유).
#: 최신일은 계열별 MAX 한 번(기본키 정렬) — 시세 뷰 전체를 훑지 않는다.
_IDX_CLOSES_SQL = (
    "WITH m AS (SELECT sid, max(trd_dd) AS hi FROM krx_idx WHERE sid = ANY(%s) AND trd_dd <= %s::date GROUP BY sid) "
    "SELECT k.sid, to_char(k.trd_dd, 'YYYYMMDD'), k.close_x100 FROM krx_idx k JOIN m ON m.sid = k.sid "
    "WHERE k.trd_dd <= m.hi AND k.trd_dd >= m.hi - 400 ORDER BY k.sid, k.trd_dd"
)

_INDEX_METHOD = ("KRX 공식 지수 일별 종가(가격지수 — 배당 재투자를 반영한 총수익지수가 아니다). 전일 대비는 보관된 직전 거래일 종가 기준, "
                 "1주·1개월·연초 이후·1년 수익률은 각 목표일(최신 거래일 −7일·−1개월·전년 12월 31일·−1년) **이하의 가장 늦은 보관 종가**를 기준점으로 하고 "
                 "그 기준 날짜(`base_dd`)를 함께 적는다.")


def _label(s: IndexSeries) -> str:
    return f"{SRC_LABEL.get(s.src, s.src)} {s.name}"


def resolve_index_name(name: str, series: list[IndexSeries]) -> tuple[list[IndexSeries], str, str]:
    """지수 이름 → (맞은 계열들, 되물을 문장, 풀이 한 줄). 맞은 계열이 하나면 질문이 비고, 여럿이면 추측하지 않고 질문을 낸다.

    순서: ① 정규화 일치(띄어쓰기·가운뎃점·영문 kospi/kosdaq 무시) — 같은 이름이 코스피·코스닥 양쪽에 있는 업종은 여럿이라 되묻는다
          ② 「코스닥 제조」처럼 시장 이름이 앞에 붙은 말은 그 시장의 계열로 ③ 부분 일치(정규화한 이름 안에 포함).
    """
    want = _norm_index(name)
    if not want:
        return [], "지수 이름이 비어 있습니다.", ""

    def ask(hits: list[IndexSeries], why: str) -> tuple[list[IndexSeries], str, str]:
        opts = " · ".join(f"{_label(s)}{'' if s.in_db else '(DB 에 일별 종가 없음)'}" for s in hits[:12])
        more = f" 외 {len(hits) - 12}개" if len(hits) > 12 else ""
        return hits, (f"「{name}」{why}해 하나로 정하지 않았습니다 — 골라 주세요: {opts}{more}. "
                      f"예: company=\"{_label(hits[0])}\""), ""

    exact = [s for s in series if _norm_index(s.name) == want]
    if len(exact) > 1:
        return ask(exact, "은 같은 이름의 공식 지수가 여러 시장에 있어")
    if len(exact) == 1:
        s = exact[0]
        return exact, "", ("" if s.name == name.strip() else f"「{name}」 → 공식 지수 「{_label(s)}」 로 풀었다.")
    for src, word in (("P", "코스피"), ("Q", "코스닥")):
        if want.startswith(word) and len(want) > len(word):
            rest = want[len(word):]
            hits = [s for s in series if s.src == src and _norm_index(s.name) == rest]
            if len(hits) == 1:
                return hits, "", f"「{name}」 → {word} 공식 지수 「{hits[0].name}」 로 풀었다."
    part = [s for s in series if want in _norm_index(s.name)]
    if len(part) == 1:
        return part, "", f"「{name}」 → 공식 지수 「{_label(part[0])}」 로 풀었다(부분 일치)."
    if len(part) > 1:
        return ask(part, "은 이름이 겹치는 공식 지수가 여럿이라")
    return [], (f"공식 지수 「{name}」을 지수 사전에서 찾지 못했습니다. 예: 코스피 · 코스닥 · 코스피 200 · 코스닥 150 · KRX 300 · 반도체. "
                "이름 없이 부르면 대표 지수 목록을 줍니다."), ""


def _index_row(s: IndexSeries, rows: list[tuple[str, float]]) -> dict[str, Any]:
    """한 계열의 최신 종가·전일 대비·기간 수익률. rows = [(YYYYMMDD, 종가 포인트)] 날짜 오름차순."""
    last_dd, close = rows[-1]
    out: dict[str, Any] = {"sid": s.sid, "name": s.name, "market": SRC_LABEL.get(s.src, s.src), "kind": s.kind,
                           "as_of": last_dd, "close": round(close, 2)}
    prev = rows[-2] if len(rows) > 1 else None
    if prev:
        chg = close - prev[1]
        out.update({"prev_dd": prev[0], "prev_close": round(prev[1], 2), "change": round(chg, 2),
                    "change_pct": round(chg / prev[1] * 100, 2) if prev[1] else None})
    else:
        out.update({"prev_dd": None, "prev_close": None, "change": None, "change_pct": None})
    d = _to_date(last_dd)
    targets = {"1w": ("1주", d - timedelta(days=7)), "1m": ("1개월", _months_back(d, 1)),
               "ytd": ("연초 이후", date(d.year - 1, 12, 31)), "1y": ("1년", _years_back(d, 1))}
    periods: dict[str, Any] = {}
    for key, (label, tgt) in targets.items():
        base = _base_at(rows, _dd(tgt))
        if base is None or base[0] >= last_dd or not base[1]:
            periods[key] = {"label": label, "target_dd": _dd(tgt), "base_dd": None, "base_close": None, "return_pct": None,
                            "note": "보관된 종가가 이 기간을 덮지 못함"}
            continue
        gap = (tgt - _to_date(base[0])).days
        periods[key] = {"label": label, "target_dd": _dd(tgt), "base_dd": base[0], "base_close": round(base[1], 2),
                        "return_pct": round((close / base[1] - 1) * 100, 2), "gap_days": gap}
    out["periods"] = periods
    return out


async def build_index_payload(name: str = "", as_of: str = "") -> dict[str, Any]:
    """공식 지수의 최신 거래일 포인트·전일 대비·1주·1개월·연초 이후·1년 수익률. `name` 이 비면 대표 지수 목록."""
    dd = _norm_dd(as_of)
    if dd is None:
        return _err(name or "지수", "invalid", f"as_of '{as_of}' 형식 오류 — YYYYMMDD.")
    series = await asyncio.to_thread(_pg, "SELECT sid, src::text, name, kind::text, in_db, to_char(first_dd, 'YYYYMMDD'), "
                                           "to_char(last_dd, 'YYYYMMDD') FROM krx_idx_series ORDER BY sid")
    if series is None:
        return _db_missing_payload("공식 지수", "지수 종가는 저장분에서만 읽을 수 있어 KRX 라이브로 대체하지 않습니다.")
    series_l = [IndexSeries(*r) for r in series]
    if not series_l:
        return _err("공식 지수", "no_data", "공식 지수 계열 사전이 비어 있습니다(적재 전).")
    note = ""
    name = (name or "").strip()
    if name:
        hits, question, note = resolve_index_name(name, series_l)
        if question:
            return _err(name, "invalid" if hits else "not_found", question)
        s = hits[0]
        if not s.in_db:
            span = f"사전상 {s.first_dd}~{s.last_dd}" if s.first_dd else "사전에만 있음"
            return _err(_label(s), "no_data",
                        f"「{_label(s)}」은 공식 지수 사전에는 있지만 이 서버 DB 에 일별 종가가 없습니다({span}) — 포인트·수익률을 만들지 않습니다. "
                        "DB 에 종가가 있는 지수: 코스피 · 코스피 200 · 코스닥 · 코스닥 150 · 업종 지수 · KRX 300 등(이름 없이 부르면 대표 지수 목록).")
        chosen = [s]
        subject = f"{_label(s)} 지수"
    else:
        chosen = [s for s in series_l if s.in_db and s.kind == _OVERVIEW_KIND]
        subject = "대표 지수"
        if not chosen:
            return _err(subject, "no_data", "DB 에 일별 종가가 있는 대표 지수가 없습니다.")
    rows = await asyncio.to_thread(_pg, _IDX_CLOSES_SQL, ([s.sid for s in chosen], dd or _FAR))
    if rows is None:
        return _db_missing_payload(subject)
    by_sid: dict[int, list[tuple[str, float]]] = {}
    for sid, d_, v in rows:
        by_sid.setdefault(int(sid), []).append((d_, v / 100.0))
    out_rows = [_index_row(s, by_sid[s.sid]) for s in chosen if by_sid.get(s.sid)]
    if not out_rows:
        return _err(subject, "no_data", f"{'%s 이하 ' % dd if dd else ''}보관된 지수 종가가 없습니다.")
    as_of_eff = max(r["as_of"] for r in out_rows)
    warns = [f"기준일 {as_of_eff} — 보관된 최신 거래일의 종가이지 오늘 장중 값이 아닙니다."]
    if note:
        warns.insert(0, note)
    if len({r["as_of"] for r in out_rows}) > 1:
        warns.append("계열마다 보관된 최신 거래일이 다릅니다 — 각 행의 as_of 를 보세요.")
    for r in out_rows:
        gaps = [p for p in r["periods"].values() if p.get("gap_days") is not None and p["gap_days"] > 7]
        if gaps:
            warns.append(f"{r['name']}: " + ", ".join(f"{p['label']} 기준점이 목표일에서 {p['gap_days']}일 앞(보관 간격)" for p in gaps))
    if dd and dd != as_of_eff:
        warns.append(f"요청한 as_of {dd} 는 휴장일이거나 보관된 최신 거래일보다 늦어, 그 이하 가장 늦은 보관 거래일 {as_of_eff} 로 답했습니다.")
    return {"tool": TOOL, "status": "ok", "subject": subject,
            "data": {"scope": "index", "as_of": as_of_eff, "n": len(out_rows), "indices": out_rows,
                     "price_index": True, "method": _INDEX_METHOD},
            "warnings": warns}


# ══════════════════════════════════════════════════════════════════════════════
#  scope=rate — 국고채 1년 금리
# ══════════════════════════════════════════════════════════════════════════════

_RATE_SQL = (
    "WITH m AS (SELECT max(d) AS hi FROM ecos_rate WHERE series = '1' AND d <= %s::date) "
    "SELECT to_char(e.d, 'YYYYMMDD'), e.rate::numeric FROM ecos_rate e, m "
    "WHERE e.series = '1' AND e.d <= m.hi AND e.d >= LEAST(COALESCE(%s::date, m.hi - 365), m.hi - 375) ORDER BY e.d"
)
_RATE_NOT_IN_DB = ("기준금리", "원/달러 환율")
_RATE_SERIES_WORDS = ("국고채", "국채", "1년", "금리")      # 이 말이 들어 있으면(또는 비었으면) 국고채 1년으로 읽는다
_RATE_OTHER_WORDS = ("기준금리", "환율", "달러", "usd", "cd", "콜", "회사채", "3년", "5년", "10년")
_RATE_METHOD = ("한국은행 ECOS 국고채 1년 금리(연 %, 일별). 기준금리·원/달러 환율은 이 서버 DB 에 없어 값을 만들지 않습니다.")
_RATE_MD_MONTHS = 13


def _rate_freq(freq: str, n: int) -> str:
    f = (freq or "").strip().lower()
    if f in ("daily", "weekly", "monthly"):
        return f
    return "daily" if n <= 300 else "weekly"


def _rate_downsample(series: list[dict], freq: str) -> list[dict]:
    if freq == "daily":
        return series
    keep: dict[str, dict] = {}
    for s in series:
        d = _to_date(s["asof"])
        k = s["asof"][:6] if freq == "monthly" else "%04d-%02d" % d.isocalendar()[:2]
        keep[k] = s
    return [keep[k] for k in sorted(keep)]


async def build_rate_payload(name: str = "", since: str = "", as_of: str = "", freq: str = "") -> dict[str, Any]:
    """국고채 1년 금리의 최신값과 기간 추이. 다른 금리·환율을 물으면 DB 에 없다고 밝히고 값을 만들지 않는다."""
    nm = (name or "").strip()
    low = nm.lower().replace(" ", "")
    if nm and any(w in low for w in _RATE_OTHER_WORDS) and "국고채1년" not in low:
        return _err(nm, "no_data",
                    f"「{nm}」은 이 서버 DB 에 없습니다 — 금리는 국고채 1년(일별)만 있고 기준금리·원/달러 환율·CD·회사채·다른 만기 국고채는 없습니다. "
                    "값을 지어내지 않습니다. 국고채 1년이면 이름 없이 `scope=\"rate\"` 로 부르세요.")
    if nm and not any(w in low for w in _RATE_SERIES_WORDS):
        return _err(nm, "no_data", f"「{nm}」을 금리 계열로 읽지 못했습니다 — 이 서버 DB 의 금리는 국고채 1년(일별)뿐입니다.")
    sd = _norm_dd(since)
    ad = _norm_dd(as_of)
    if sd is None or ad is None:
        return _err("국고채 1년 금리", "invalid", "since·as_of 는 YYYYMMDD 꼴이어야 합니다.")
    rows = await asyncio.to_thread(_pg, _RATE_SQL, (ad and f"{ad[:4]}-{ad[4:6]}-{ad[6:]}" or _FAR,
                                                     (f"{sd[:4]}-{sd[4:6]}-{sd[6:]}" if sd else None)))
    if rows is None:
        return _db_missing_payload("국고채 1년 금리", "금리는 저장분에서만 읽을 수 있어 외부로 대체하지 않습니다.")
    if not rows:
        return _err("국고채 1년 금리", "no_data", "보관된 국고채 1년 금리가 없습니다(적재 전이거나 조회 구간이 비었습니다).")
    obs = [(r[0], float(r[1])) for r in rows]
    hi_dd, hi_v = obs[-1]
    hi = _to_date(hi_dd)
    lo_dd = sd or _dd(hi - timedelta(days=365))
    view = [o for o in obs if o[0] >= lo_dd] or obs[-1:]
    changes = []
    for key, label, tgt in (("1w", "1주 전", hi - timedelta(days=7)), ("1m", "1개월 전", _months_back(hi, 1)),
                            ("3m", "3개월 전", _months_back(hi, 3)), ("ytd", "연초(전년 말)", date(hi.year - 1, 12, 31)),
                            ("1y", "1년 전", _years_back(hi, 1))):
        base = _base_at(obs, _dd(tgt))
        if base is None or base[0] >= hi_dd:
            changes.append({"key": key, "label": label, "base_dd": None, "base_rate": None, "change_bp": None})
        else:
            changes.append({"key": key, "label": label, "base_dd": base[0], "base_rate": round(base[1], 3),
                            "change_bp": round((hi_v - base[1]) * 100, 1)})
    lo_o = min(view, key=lambda o: o[1])
    hi_o = max(view, key=lambda o: o[1])
    fq = _rate_freq(freq, len(view))
    series = _rate_downsample([{"asof": d, "rate": round(v, 3)} for d, v in view], fq)
    warns = [f"기준일 {hi_dd} — 보관된 최신 값입니다.",
             "기준금리와 원/달러 환율은 이 서버 DB 에 없습니다 — 값을 지어내지 않습니다."]
    if ad and ad != hi_dd:
        warns.append(f"요청한 as_of {ad} 는 관측이 없는 날이거나 보관된 최신일보다 늦어, 그 이하 가장 늦은 관측일 {hi_dd} 로 답했습니다.")
    if sd and sd < obs[0][0]:
        warns.append(f"요청한 since {sd} 보다 보관이 늦게 시작해 {view[0][0]} 부터 줍니다.")
    return {"tool": TOOL, "status": "ok", "subject": "국고채 1년 금리",
            "data": {"scope": "rate", "series": "국고채 1년", "unit": "연 %", "as_of": hi_dd,
                     "latest": {"asof": hi_dd, "rate": round(hi_v, 3)}, "changes": changes,
                     "range": {"from": view[0][0], "to": hi_dd, "n": len(view),
                               "min": {"asof": lo_o[0], "rate": round(lo_o[1], 3)},
                               "max": {"asof": hi_o[0], "rate": round(hi_o[1], 3)}},
                     "freq": fq, "points": len(series), "series": series,
                     "not_in_db": list(_RATE_NOT_IN_DB), "method": _RATE_METHOD},
            "warnings": warns}


# ══════════════════════════════════════════════════════════════════════════════
#  scope=quote + universe — 여러 종목 시세 순위표 (등락률·거래대금·거래량 정렬, 52주 고가·저가)
# ══════════════════════════════════════════════════════════════════════════════

#: 52주 = 364일(52 × 7). 창은 [기준일 − 364일, 기준일] 닫힌 구간이다.
WEEK52_DAYS = 364
#: md 표·json 행의 상한. 넘으면 양 끝(정렬 상위 · 하위)만 싣고 잘린 사실과 전체 수를 적는다.
RANK_ROWS_MAX = 300
_RANK_HALF = RANK_ROWS_MAX // 2

#: 정렬 별칭 → (키, 내림차순 여부, 이름)
_SORTS: dict[str, tuple[str, bool, str]] = {}
for _names, _spec in (
    (("", "등락률", "상승률", "change", "change_pct", "pct", "등락"), ("change_pct", True, "등락률 큰 순")),
    (("하락률", "낙폭", "change_asc", "loss"), ("change_pct", False, "등락률 작은 순(하락률)")),
    (("거래대금", "대금", "value", "turnover"), ("value_krw", True, "거래대금 큰 순")),
    (("거래량", "volume", "vol"), ("volume", True, "거래량 큰 순")),
):
    for _n in _names:
        _SORTS[_n] = _spec

_DAY_SQL = ("SELECT ticker, close, high, low, volume, value, mktcap, krx_chg, krx_chg_pct, list_shrs "
            "FROM krx_daily_v WHERE trd_dd = %s AND ticker = ANY(%s)")
_WIN_SQL = ("SELECT ticker, max(high) FILTER (WHERE high > 0), min(low) FILTER (WHERE low > 0), min(trd_dd), count(*) "
            "FROM krx_daily_v WHERE ticker = ANY(%s) AND trd_dd >= %s AND trd_dd <= %s GROUP BY ticker")
#: 조정 이벤트 — 호가단위 맞춤(라벨)과 제외 표시(excluded*) 행은 뺀다. 라벨이 아직 비었어도(unlabeled) 호가단위 판정은 아래에서 코드로 다시 한다.
_ADJ_SQL = ("SELECT ticker, event_dd, adj_factor, prev_close, base_price, market FROM krx_adj_events "
            "WHERE ticker = ANY(%s) AND event_dd >= %s AND event_dd <= %s AND adj_factor IS NOT NULL AND adj_factor <> 1 "
            "AND COALESCE(event_type, '') <> 'tick_rounding' AND left(COALESCE(label_confidence, ''), 8) <> 'excluded'")
_CAL_SQL = "SELECT min(dd), max(dd), max(dd) FILTER (WHERE dd <= %s) FROM krx_day WHERE n > 0"

_RANK_METHOD = ("일별 시세 저장분(KRX 일별매매정보 원본, 최근 약 1년)의 기준일 단면. 종가·거래량·거래대금·시가총액은 그 날 실제 값이고 "
                "등락률은 KRX 공표값(기준가 대비)이다. 52주 고가·저가 = 기준일 −364일 이후 보관 행의 고가·저가 칸 최댓값·최솟값(거래 없는 날의 0 은 뺀다), "
                "대비 = 종가 ÷ 52주 고가(저가) − 1, 신고가 = 기준일 고가가 52주 고가와 같음. 원 시세는 수정주가가 아니라서 52주 창 안에 "
                "액면분할·병합 같은 기준가 조정이 있는 종목은 52주 칸을 비운다(조정 이벤트로 산출 불가).")


def _sort_spec(sort: str) -> tuple[str, bool, str] | None:
    return _SORTS.get((sort or "").strip().lower().replace(" ", ""))


def _adjusted_tickers(ev_rows: list[tuple], first_dd: dict[str, str]) -> set[str]:
    """52주 창 안에 조정 이벤트가 낀 종목. 이벤트 날 앞에 창 안 보관 행이 있어야(= 그 종목 창의 첫 보관일보다 늦은 날) 가격이 끊긴다.
    기준가가 전일 종가를 호가단위에 맞춘 것뿐인 날(`is_tick_rounding`)은 라벨이 비었어도 조정으로 세지 않는다."""
    out: set[str] = set()
    for t, ev_dd, _f, prev_close, base_price, mk in ev_rows:
        if t in out:
            continue
        if ev_dd <= first_dd.get(t, ""):
            continue
        if is_tick_rounding(prev_close, base_price, mk, ev_dd):
            continue
        out.add(t)
    return out


def _pct(a: float, b: float) -> float | None:
    return round((a / b - 1) * 100, 2) if a and b else None


def _rank_row(u: dict, day: tuple | None, win: tuple | None, blocked: bool) -> dict[str, Any]:
    """순위표 한 줄. day = _DAY_SQL 한 행(없으면 None), win = _WIN_SQL 한 행."""
    row: dict[str, Any] = {"ticker": u["ticker"], "name": u.get("name") or "-", "market": u.get("market")}
    _t, close, high, _low, vol, val, cap, krx_chg, krx_pct, shrs = day
    row.update({"close_krw": int(close) if close is not None else None,
                "change_krw": int(krx_chg) if krx_chg is not None else None,
                "change_pct": float(krx_pct) if krx_pct is not None else None,
                "volume": int(vol) if vol is not None else None,
                "value_krw": int(val) if val is not None else None,
                "mktcap_krw": int(cap) if cap is not None else None})
    hi52 = lo52 = None
    if win is not None and not blocked:
        hi52 = int(win[1]) if win[1] is not None else None
        lo52 = int(win[2]) if win[2] is not None else None
    row.update({"high52_krw": hi52, "low52_krw": lo52,
                "from_high52_pct": _pct(close, hi52) if hi52 else None,
                "from_low52_pct": _pct(close, lo52) if lo52 else None,
                "new_high52": bool(hi52 and high and int(high) >= hi52) if not blocked else None,
                "week52_blocked": bool(blocked),
                "window_days": int(win[4]) if win is not None else 0})
    return row


async def build_quote_rank_payload(universe: str, sort: str = "", as_of: str = "") -> dict[str, Any]:
    """`scope=quote` 에 `company` 없이 `universe` 를 준 호출 — 여러 종목의 기준일 시세 순위표(등락률·거래대금·거래량 정렬, 52주 고가·저가·신고가)."""
    from open_proxy_mcp.services.universe import list_universe

    raw = (universe or "").strip()
    spec = _sort_spec(sort)
    if spec is None:
        return _err(raw or "유니버스", "invalid",
                    f"sort '{sort}' 를 알 수 없습니다 — 등락률(기본) / 하락률 / 거래대금 / 거래량 중 선택.")
    key, desc, sort_label = spec
    dd_req = _norm_dd(as_of)
    if dd_req is None:
        return _err(raw, "invalid", f"as_of '{as_of}' 형식 오류 — YYYYMMDD.")
    if not raw:
        return _err("유니버스", "invalid", "universe 를 지정하세요 — 예: \"코스피 시총 상위 100\" · \"코스닥 상위 50\" · \"코스피200\" · \"전체\" · \"삼성전자, SK하이닉스\".")

    ul = await list_universe(raw)
    subject = ul.label or raw
    if ul.question:
        return _err(subject, "invalid", ul.question)
    if not ul.db_ok:
        return _db_missing_payload(subject, "여러 종목 시세 순위표는 시세 저장분에서만 만들 수 있어 KRX 라이브로 대체하지 않습니다.")
    if not ul.resolved:
        return _err(subject, "no_data", ul.notice or "유니버스를 해석하지 못했습니다 — 표현을 바꿔 다시 부르세요.")
    if not ul.rows:
        return _err(subject, "no_data", f"{ul.as_of} 기준 저장분에 해당 종목이 없습니다.")

    # 기준일은 거래일 달력에서 먼저 구한다(뷰 전체 MIN/MAX 금지, DATA-PX7)
    cal = await asyncio.to_thread(_pg, _CAL_SQL, (dd_req or "99999999",))
    if cal is None:
        return _db_missing_payload(subject)
    lo_stored, hi_stored, eff = (cal[0] if cal else (None, None, None))
    if not hi_stored:
        return _err(subject, "no_data", "일별 시세 저장분이 비어 있습니다(적재 전).")
    if dd_req and lo_stored and dd_req < lo_stored:
        return _err(subject, "invalid",
                    f"as_of {dd_req} 는 일별 시세 보관 창({lo_stored}~{hi_stored}) 밖입니다 — 여러 종목 순위표는 KRX 라이브를 종목마다 부르지 않아 "
                    "보관 창 안의 날짜만 받습니다. 한 종목의 과거 시세는 `company` 를 주어 `scope=quote` 로 부르세요.")
    dd = eff or hi_stored
    tickers = [u["ticker"] for u in ul.rows]
    w_lo = _dd(_to_date(dd) - timedelta(days=WEEK52_DAYS))
    # 서로 입력이 필요 없는 세 읽기 — 호출당 동시 상한을 지키는 공용 겹쳐 읽기(`_gather_db`)로 한 왕복에 묶는다.
    day_rows, win_rows, ev_rows = await _gather_db(
        _db_call(_pg, _DAY_SQL, (dd, tickers)),
        _db_call(_pg, _WIN_SQL, (tickers, w_lo, dd)),
        _db_call(_pg, _ADJ_SQL, (tickers, w_lo, dd)))
    if day_rows is None or win_rows is None or ev_rows is None:
        return _db_missing_payload(subject)
    day = {r[0]: r for r in day_rows}
    win = {r[0]: r for r in win_rows}
    first_dd = {t: w[3] for t, w in win.items() if w[3]}
    blocked = _adjusted_tickers(ev_rows, first_dd)

    rows = [_rank_row(u, day[u["ticker"]], win.get(u["ticker"]), u["ticker"] in blocked) for u in ul.rows if u["ticker"] in day]
    missing = len(ul.rows) - len(rows)
    if not rows:
        return _err(subject, "no_data", f"{dd} 에 이 유니버스 종목의 시세 행이 없습니다(휴장일이거나 보관되지 않은 날).")
    # 정렬 — 값이 없는 행은 방향과 상관없이 맨 뒤. 같은 값은 입력 순서(시총 큰 순)를 유지한다.
    have = [r for r in rows if r.get(key) is not None]
    none = [r for r in rows if r.get(key) is None]
    have.sort(key=lambda r: r[key], reverse=desc)
    ranked = have + none
    for i, r in enumerate(ranked, 1):
        r["rank"] = i
    n = len(ranked)
    truncated = n > RANK_ROWS_MAX
    shown = ranked[:_RANK_HALF] + ranked[-_RANK_HALF:] if truncated else ranked
    new_high = [r["ticker"] for r in ranked if r.get("new_high52")]
    pcts = [r["change_pct"] for r in ranked if r["change_pct"] is not None]

    warns = [f"기준일 {dd} 종가 기준 — 보관된 거래일 단면이지 오늘 장중 값이 아닙니다."]
    if dd_req and dd != dd_req:
        warns.append(f"요청한 as_of {dd_req} 는 거래일이 아니거나 보관된 최신일보다 늦어 {dd} 로 답했습니다.")
    if dd != ul.as_of:
        warns.append(f"종목 구성은 최신 거래일({ul.as_of}) 기준으로 골랐고, 시세는 {dd} 값입니다(과거 날짜의 시총 순위로 다시 고르지 않습니다).")
    if ul.excluded_pref and ul.sector is not None:        # 업종 문구(261023) — 우선주는 업종 구성에서 빠진다
        warns.append(f"우선주 {ul.excluded_pref}종목은 업종 구성에서 뺐습니다 — 우선주는 업종·성과 분석 대상이 아니라 종목 코드로 부르는 개별 시세만 둡니다.")
    elif ul.excluded_pref:
        warns.append(f"우선주 {ul.excluded_pref}종목은 순위에서 뺐습니다 — 같은 회사의 보통주가 순위에 있습니다.")
    if ul.notice:
        warns.append(ul.notice)
    if missing:
        warns.append(f"유니버스 {len(ul.rows)}종목 중 {missing}종목은 {dd} 시세 행이 없어(거래정지·상장 전 등) 표에서 뺐습니다.")
    window = {"from": w_lo, "to": dd, "days": WEEK52_DAYS, "stored_from": lo_stored, "covers_full": bool(lo_stored and lo_stored <= w_lo)}
    if not window["covers_full"]:
        warns.append(f"보관된 시세가 {lo_stored} 부터라 52주({w_lo}~{dd})를 다 덮지 못합니다 — 52주 고가·저가는 실제 시작일 {lo_stored} ~ {dd} 의 값입니다.")
    nb = sum(1 for r in rows if r["week52_blocked"])
    if nb:
        warns.append(f"52주 칸을 비운 종목 {nb}개 — 52주 안에 액면분할·병합 같은 기준가 조정이 있어 원 시세로는 52주 고가·저가를 낼 수 없습니다(조정 이벤트로 산출 불가).")
    if truncated:
        warns.append(f"전체 {n:,}종목 중 정렬 상위 {_RANK_HALF}개와 하위 {_RANK_HALF}개만 싣습니다(가운데 {n - 2 * _RANK_HALF:,}종목 생략).")
    return {"tool": TOOL, "status": "ok", "subject": f"{subject} — 시세 순위표",
            "data": {"scope": "quote", "view": "universe", "universe": raw, "spec": ul.spec, "label": ul.label,
                     "as_of": dd, "sort": sort_label, "sort_key": key, "descending": desc,
                     "n": n, "rows": shown, "truncated": truncated,
                     "omitted": (n - 2 * _RANK_HALF) if truncated else 0,
                     "window52": window,
                     "week52_blocked_n": nb,
                     "new_high52_n": len(new_high), "new_high52_tickers": new_high[:100],
                     "breadth": {"up": sum(1 for p in pcts if p > 0), "down": sum(1 for p in pcts if p < 0),
                                 "flat": sum(1 for p in pcts if p == 0)},
                     "price_adjusted": False, "method": _RANK_METHOD},
            "warnings": warns}
