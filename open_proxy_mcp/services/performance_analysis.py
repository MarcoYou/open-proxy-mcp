"""performance_analysis — 블렌딩·초과수익·기여 분해·위험 지표를 **부를 때 계산**하는 도구의 조립부 (261022).

계산식은 `perf_math`(순수), DB·콜드 읽기는 `perf_data`, 여기서는 인자 해석 → 대상 구성 → 읽기 → 계산 → 응답 조립을 맡는다.
부를 때 외부 호출은 DART·KRX·ECOS 모두 0 이고 DB 와 콜드 서빙본만 읽는다(쓰기 0). 예외 하나: 종목 이름표(`universe._names_by_ticker`)가 DART 회사 원장 캐시를
쓰므로 캐시가 비어 있으면 corpCode 받기 1콜이 날 수 있다(`method` 에 밝힌다).

■ 대상 문법(`parse_target`) — `trading_data(scope="universe")` 와 같은 문구를 그대로 받는다(`universe.list_universe` 재사용).
  업종(「반도체 업종」「대분류 IT」「코스피 IT 섹터 상위 10」) · 순위·지수(「코스피 시총 상위 50」「코스피200」) · 나열(「005930, 000660」) ·
  바스켓(「005930:40, 000660:60」 — 종목:비중%) · 블렌드(구성요소를 `;` 로 나누고 `=비중%`: 「반도체 업종=60; 대분류 금융=40」).
  블렌드·바스켓 비중 합이 100(±0.5)이 아니면 **맞춰 채우지 않고** invalid 로 되묻는다. 구성요소는 최대 10개.
■ 구성은 시작일 시점으로 고정한다 — 나중에 편입·상장한 종목이 과거로 들어가지 않게. 업종 문구는 시작일의 분류 스냅샷으로 풀리고, 시총 순위·지수 문구는 이 서버가
  보관한 **최신 거래일** 구성이라(`membership_lookahead`) 시작일에 아직 없던 종목은 가격이 없어 빠진다. 기간 중 상장폐지는 마지막 거래일 뒤 0% 현금이다.
■ 비중: 구성요소 안 가중은 `weights`(시총 = 시작일 기준 시총 / 동일), 바스켓은 적은 비중. 이후 비중은 표류하고 `rebalance` 로 되돌린다.
■ 해상도: 기간이 일별 보관 창(약 1년) 안이면 일별, 아니면 주간(주 마지막 거래일 한 점). 3년 밖 주간은 콜드 서빙본에서 잇고 못 읽으면 DB 하한으로 자른다(부분 곡선 없음).
■ 벤치마크: 공식 지수(`krx_idx_series` 이름 — 외국주 제외, 가격지수) 또는 계산 벤치마크(유니버스 문구). 「코스피 전체」·「코스닥 전체」는 우선주 포함 시총 가중이고
  전일 기준가 시총으로 매일 비중을 잡는다(DB 집계 한 질의). 그 밖의 유니버스 문구는 시작일 구성을 고정해 시총 가중으로 표류시킨다. Brinson 업종 분해는 구성 이력이 있는 계산
  벤치마크만 된다.
■ 도구 겉면은 `tools/performance_analysis.py`(md·json 렌더). 이 모듈은 payload(dict)만 만든다.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Sequence

from open_proxy_mcp.company_resolver import issuer_ticker
from open_proxy_mcp.market_codes import KQ, KS, to_db as mkt_to_db, to_label
from open_proxy_mcp.services import perf_data as pd
from open_proxy_mcp.services import perf_math as pm
from open_proxy_mcp.services.universe import list_universe

logger = logging.getLogger(__name__)

TOOL = "performance_analysis"

MAX_COMPONENTS = 10
MAX_POSITIONS_DAILY = 500
MAX_POSITIONS_WEEKLY = 300
MAX_COLD_READS = 300
#: 이 날짜(YYYYMMDD) 이전 날짜에 업종 분류를 쓰면 체계 단절을 알린다 — WICS 2018-06 ~ 2019-06 개편(미디어와엔터테인먼트 신설).
TAXONOMY_BREAK_DD = "20190628"
UNCLASSIFIED = pd.UNCLASSIFIED

SCOPES = ("summary", "contribution", "attribution", "series")
_GROUPS = {"중분류": "mid", "대분류": "major", "종목": "stock", "구성요소": "component",
           "mid": "mid", "major": "major", "stock": "stock", "component": "component"}
_REBAL = {"none": "none", "없음": "none", "monthly": "monthly", "월간": "monthly", "월": "monthly",
          "quarterly": "quarterly", "분기": "quarterly"}
_BASIS = {"price": "price", "가격": "price", "total": "total", "총수익": "total"}
_WEIGHTS = {"시총": "cap", "시가총액": "cap", "cap": "cap", "동일": "equal", "equal": "equal", "동일가중": "equal"}
_FREQS = {"": "", "daily": "daily", "일별": "daily", "weekly": "weekly", "주간": "weekly"}

_DB_UNSET = ("이 서버에는 스냅샷 DB(`DATABASE_URL`)가 연결돼 있지 않습니다 — **재시도해도 되지 않습니다.** 성과 분석은 저장 시세에서만 계산하며 "
             "KRX 라이브로 대체하지 않습니다.")
_DB_ERROR = "스냅샷 DB 연결 실패 — 일시 장애 가능, 잠시 후 재시도. (배치 미실행과 다름)"


def _max_rows() -> int:
    try:
        return max(1000, int(os.environ.get("OPM_PA_MAX_ROWS", "") or 200_000))
    except ValueError:
        return 200_000


def _pct(x: float | None, nd: int = 4) -> float | None:
    return None if x is None or not math.isfinite(x) else round(x * 100.0, nd)


def _num(x: float | None, nd: int = 4) -> float | None:
    return None if x is None or not math.isfinite(x) else round(x, nd)


def _ymd(dd: str) -> str:
    return f"{dd[:4]}-{dd[4:6]}-{dd[6:]}" if dd and len(dd) == 8 else dd


class Invalid(Exception):
    """사용자에게 그대로 돌려줄 오류 응답(status 포함)."""

    def __init__(self, status: str, *warns: str, codes: Sequence[str] = ()):
        super().__init__(status)
        self.status, self.warns, self.codes = status, list(warns), list(codes)


def _err(subject: str, status: str, *warns: str, codes: Sequence[str] = ()) -> dict[str, Any]:
    out: dict[str, Any] = {"tool": TOOL, "status": status, "subject": subject, "warnings": list(warns)}
    if codes:
        out["warning_codes"] = list(codes)
    return out


def _db_fail() -> Invalid:
    if os.getenv("DATABASE_URL"):
        return Invalid("db_error", _DB_ERROR)
    return Invalid("db_unconfigured", _DB_UNSET)


# ═════════════════════════════════════════════════════════════════════════════
# 대상 문법
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class Component:
    label: str
    spec: str
    weight: float                                  # 블렌드 안 비중(소수, 합 1)
    basket: dict[str, float] | None = None         # 바스켓이면 종목 → 구성요소 안 비중(소수, 합 1)


@dataclass
class Parsed:
    components: list[Component] = field(default_factory=list)
    error: str = ""
    blend: bool = False


_WEIGHT_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*%?\s*$")
_BASKET_ITEM = re.compile(r"^\s*([0-9A-Za-z]{6})\s*:\s*(\d+(?:\.\d+)?)\s*%?\s*$")


def _parse_component(text: str) -> tuple[Component | None, str]:
    raw = text.strip()
    if not raw:
        return None, "빈 구성요소가 있습니다."
    items = [x for x in raw.split(",") if x.strip()]
    hits = [_BASKET_ITEM.match(x) for x in items]
    if items and any(hits):
        if not all(hits):
            return None, ("바스켓은 모든 종목에 비중을 적어야 합니다 — 예: \"005930:40, 000660:60\"(종목코드:비중%). "
                          f"비중이 빠진 항목: {', '.join(x.strip() for x, h in zip(items, hits) if not h)}")
        w: dict[str, float] = {}
        for h in hits:
            t, v = h.group(1).upper(), float(h.group(2))
            if t in w:
                return None, f"바스켓에 종목 {t} 가 두 번 있습니다."
            w[t] = v
        tot = sum(w.values())
        if abs(tot - 100.0) > 0.5 or tot <= 0:
            return None, f"바스켓 비중 합이 100(±0.5)이 아닙니다(현재 {tot:g}) — 맞춰 채우지 않았습니다. 비중을 다시 적어 주세요."
        return Component(label=raw, spec=raw, weight=1.0, basket={t: v / tot for t, v in w.items()}), ""
    return Component(label=raw, spec=raw, weight=1.0), ""


def parse_target(text: str) -> Parsed:
    """대상 문구 → 구성요소들. 블렌드(`;` + `=비중`)·바스켓(`종목:비중`)·그 밖(유니버스 문구 그대로)."""
    raw = (text or "").strip()
    if not raw:
        return Parsed(error="target 을 지정하세요 — 예: \"반도체 업종\" · \"코스피 시총 상위 50\" · \"005930:40, 000660:60\" · "
                            "\"반도체 업종=60; 대분류 금융=40\"(블렌드).")
    if ";" not in raw:
        spec, sep, wtxt = raw.rpartition("=")
        m = _WEIGHT_RE.match(wtxt) if sep and spec.strip() else None
        if m:                                           # 「반도체 업종=100」 — 구성요소가 하나뿐인 블렌드. 100 이 아니면 맞춰 채우지 않고 되묻는다
            if abs(float(m.group(1)) - 100.0) > 0.5:
                return Parsed(error=f"구성요소가 하나뿐인데 비중이 {float(m.group(1)):g} 입니다 — 블렌드는 구성요소를 `;` 로 나누고 비중 합이 100(±0.5)이어야 합니다"
                                    "(예: \"반도체 업종=60; 대분류 금융=40\"). 한 구성요소만이면 `=비중` 을 빼 주세요.")
            raw = spec.strip()
        c, err = _parse_component(raw)
        return Parsed(error=err) if err else Parsed([c])
    parts = [p for p in raw.split(";") if p.strip()]
    if len(parts) > MAX_COMPONENTS:
        return Parsed(error=f"블렌드 구성요소는 최대 {MAX_COMPONENTS}개입니다(현재 {len(parts)}개).")
    if len(parts) < 2:
        return Parsed(error="블렌드는 구성요소를 `;` 로 나누고 각각 `=비중%` 를 적어야 합니다 — 예: \"반도체 업종=60; 대분류 금융=40\".")
    comps: list[Component] = []
    weights: list[float] = []
    for p in parts:
        spec, sep, wtxt = p.rpartition("=")
        m = _WEIGHT_RE.match(wtxt) if sep else None
        if not m or not spec.strip():
            return Parsed(error=f"블렌드 구성요소 「{p.strip()}」에 비중이 없습니다 — `구성요소=비중%` 꼴로 적어 주세요(예: \"반도체 업종=60\").")
        c, err = _parse_component(spec)
        if err:
            return Parsed(error=err)
        comps.append(c)
        weights.append(float(m.group(1)))
    tot = sum(weights)
    if abs(tot - 100.0) > 0.5 or tot <= 0:
        return Parsed(error=f"블렌드 비중 합이 100(±0.5)이 아닙니다(현재 {tot:g}) — 맞춰 채우지 않았습니다. 비중을 다시 적어 주세요.")
    for c, w in zip(comps, weights):
        c.weight = w / tot
        c.label = f"{c.spec.strip()} {w:g}%"
    return Parsed(comps, blend=True)


_MARKET_WIDE = re.compile(r"^\s*(코스피|코스닥|kospi|kosdaq)?\s*(전체\s*시장|전종목|전체)\s*$", re.I)


def market_wide(text: str) -> list[str] | None:
    """「코스피 전체」「코스닥 전체」「전체」 → 시장 코드 목록. 아니면 None. 계산 벤치마크의 전 시장 경로(DB 집계)로 보낸다."""
    m = _MARKET_WIDE.match(text or "")
    if not m:
        return None
    w = (m.group(1) or "").lower()
    if w in ("코스피", "kospi"):
        return [KS]
    if w in ("코스닥", "kosdaq"):
        return [KQ]
    return [KS, KQ]


_INDEX_WORD = re.compile(r"^\s*(?:코스피|코스닥|kospi|kosdaq)\s*$", re.I)
_INDEX_PREFIX = re.compile(r"^\s*지수\s*[:：]\s*(.*?)\s*$")


def index_target(parsed: Parsed) -> str | None:
    """target 이 공식 지수 자체를 가리키면 그 이름, 아니면 None.
    블렌드·바스켓이 아닌 **단일 문구**이고 (a) 「코스피」「코스닥」(kospi·kosdaq 영문, 앞뒤 공백 무시)이거나 (b) 「지수:」 접두사 뒤 이름(빈 이름도 이름으로 돌려 되묻는다)일 때만이다.
    「코스피200」「코스피 200」「코스닥150」「코스닥 150」「코스피 전체」 같은 지금 유니버스 문법으로 풀리는 문구는 종전대로 구성 종목 바스켓이다 — 지수 이름 정규화가
    띄어쓰기를 지워 두 표기를 같게 보므로, 정규화 일치만으로 지수를 고르면 기존 바스켓 호출의 응답이 바뀐다."""
    if parsed.blend or len(parsed.components) != 1 or parsed.components[0].basket is not None:
        return None
    spec = parsed.components[0].spec.strip()
    m = _INDEX_PREFIX.match(spec)
    if m:
        return m.group(1)
    return spec if _INDEX_WORD.match(spec) else None


# ═════════════════════════════════════════════════════════════════════════════
# 기간·해상도 (순수)
# ═════════════════════════════════════════════════════════════════════════════

def norm_date(s: str) -> str | None:
    t = re.sub(r"[-./\s]", "", (s or "").strip())
    if not re.fullmatch(r"\d{8}", t):
        return None
    try:
        date(int(t[:4]), int(t[4:6]), int(t[6:]))
    except ValueError:
        return None
    return t


def one_year_before(dd: str) -> str:
    d = pm.parse_dd(dd)
    try:
        return pm.fmt_dd(d.replace(year=d.year - 1))
    except ValueError:                                  # 2월 29일
        return pm.fmt_dd(date(d.year - 1, 2, 28))


@dataclass
class Plan:
    end: str = ""
    requested_end: str = ""
    since_req: str = ""
    requested_since: str = ""
    freq: str = "daily"
    base: str | None = None                  # 일별이면 기준일(시작일 이하 마지막 거래일)
    notes: list[str] = field(default_factory=list)
    error: str = ""
    old_end: bool = False                    # 끝 기준일이 일별 창보다 오래라 주 닫는 날을 따로 골라야 한다


UNKNOWN = object()


def plan_period(days: Sequence[str], as_of: str, since: str, freq_req: str, end_old: Any = UNKNOWN) -> Plan:
    """보관된 일별 거래일 달력으로 끝·시작·해상도를 정한다. 순수 함수 — `end_old` 는 끝이 일별 창보다 오래일 때 DB 에서 고른 주 닫는 날(없으면 None, 못 읽으면 False).
    끝이 일별 창 밖인데 `end_old` 를 아직 모르면(UNKNOWN) `old_end=True` 만 세우고 돌려준다(호출부가 읽고 다시 부른다)."""
    p = Plan()
    latest, lo = days[-1], days[0]
    req_end = ""
    if (as_of or "").strip():
        req_end = norm_date(as_of) or ""
        if not req_end:
            p.error = f"as_of 「{as_of}」은 날짜가 아닙니다 — YYYYMMDD 또는 YYYY-MM-DD 로 주세요."
            return p
    p.requested_end = req_end or latest
    if not req_end or req_end >= latest:
        p.end = latest
        if req_end and req_end > latest:
            p.notes.append(f"요청한 끝 기준일 {req_end} 은 보관된 최신 거래일 {latest} 보다 뒤라 최신 거래일로 풀었다.")
    elif req_end >= lo:
        p.end = max(d for d in days if d <= req_end)
        if p.end != req_end:
            p.notes.append(f"요청한 끝 기준일 {req_end} 은 거래일이 아니어서(휴장·주말) 그 앞 거래일 {p.end} 로 풀었다.")
    else:
        if end_old is UNKNOWN:
            p.old_end = True
            return p
        if end_old is False:
            p.error = "DB"
            return p
        if not end_old:
            p.error = f"끝 기준일 {req_end} 이하의 주간 시세가 없습니다."
            return p
        p.end = str(end_old)
        if p.end != req_end:
            p.notes.append(f"요청한 끝 기준일 {req_end} 은 주간 시세 구간이라 그 앞 주 마지막 거래일 {p.end} 로 풀었다.")
    sv = ""
    if (since or "").strip():
        sv = norm_date(since) or ""
        if not sv:
            p.error = f"since 「{since}」은 날짜가 아닙니다 — YYYYMMDD 또는 YYYY-MM-DD 로 주세요."
            return p
    p.requested_since = sv or one_year_before(p.end)
    p.since_req = p.requested_since
    if p.since_req >= p.end:
        p.error = f"시작일 {_ymd(p.since_req)} 이 끝 기준일 {_ymd(p.end)} 과 같거나 늦습니다 — 기간이 비었습니다."
        return p
    in_daily = p.since_req >= lo and p.end >= lo
    if freq_req == "daily" and not in_daily:
        p.error = (f"freq=\"daily\" 는 기간 전체가 일별 보관 창({_ymd(lo)}~) 안일 때만 됩니다 — 시작일 {_ymd(p.since_req)} 이 창 밖입니다. "
                   "freq 를 비우면 주간으로 계산합니다.")
        return p
    p.freq = freq_req or ("daily" if in_daily else "weekly")
    if p.freq == "daily":
        p.base = max(d for d in days if d <= p.since_req)
        if p.base != p.since_req:
            p.notes.append(f"시작일 {p.since_req} 은 거래일이 아니어서(휴장·주말) 그 앞 거래일 {p.base} 종가를 기준으로 한다.")
        if p.base >= p.end:
            p.error = "기간 안에 거래일이 없습니다."
    return p


# ═════════════════════════════════════════════════════════════════════════════
# 종목 구간 열 (순수)
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class TickerLegs:
    ret: list[float]                    # n 구간 수익률(총수익이면 배당 포함, 끊긴 구간은 0)
    price_ret: list[float]              # 같은 구간의 가격 수익률(배당 제외)
    base: list[float | None]            # 구간 조정 전일 기준가(끊긴 구간 None)
    close: list[float | None]
    shrs: list[float | None]
    cap: list[float | None]             # 길이 n+1 — 날짜별 시총, 행이 없으면 직전 값을 이어 쓴다
    last_idx: int = 0                   # 마지막 가격 관측 번호(0=기준일 … n=끝)
    priced_at_start: bool = True
    cmp_missing: int = 0
    tick_skipped: int = 0
    events_used: int = 0
    unusable: int = 0                   # 수익률을 계산하지 못한 구간 수(0 으로 둠)
    dps_applied: float = 0.0


def build_ticker_legs(rows: Sequence[tuple], events: Sequence[pm.AdjEvent], dates: Sequence[str], freq: str,
                      dps_by_j: dict[int, float] | None = None) -> TickerLegs:
    """한 종목의 구간 열. rows = [(날짜, 종가, cmp, 주식수)] 오름차순(주간이면 cmp=None). 기준일 행이 없으면 priced_at_start=False."""
    n = len(dates) - 1
    legs = TickerLegs([0.0] * n, [0.0] * n, [None] * n, [None] * n, [None] * n, [None] * (n + 1))
    by = {r[0]: r for r in rows}
    row0 = by.get(dates[0])
    if row0 is None or not row0[1]:
        legs.priced_at_start = False
        return legs
    prev, prev_dd = row0, dates[0]
    legs.cap[0] = float(row0[1]) * float(row0[3]) if row0[3] else None
    evs = list(events)
    for j in range(n):
        d = dates[j + 1]
        row = by.get(d)
        if row is None:
            legs.cap[j + 1] = legs.cap[j]
            continue
        adj = pm.adjustment_factor(evs, prev_dd, d) if evs else pm.AdjResult(1.0, 0, 0)
        legs.tick_skipped += adj.tick_skipped
        if freq == "daily":
            tick_day = adj.tick_skipped > 0 and adj.applied == 0
            leg = pm.daily_leg(row[1], row[2], prev[1], adj.factor, tick_day=tick_day)
            if leg.cmp_missing:
                legs.cmp_missing += 1
            if row[2] is None or tick_day:               # 공표 대비를 쓴 날은 사건 계수를 곱하지 않았다(이미 기준가에 들어 있다) — 쓴 사건만 센다
                legs.events_used += adj.applied
        else:
            leg = pm.weekly_leg(row[1], prev[1], adj.factor)
            legs.events_used += adj.applied
        cap_now = float(row[1]) * float(row[3]) if row[1] and row[3] else legs.cap[j]
        if leg.ret is None:
            legs.unusable += 1
            legs.cap[j + 1] = cap_now
            prev, prev_dd = row, d
            continue
        dps = (dps_by_j or {}).get(j)
        legs.price_ret[j] = leg.ret
        legs.ret[j] = pm.total_return(leg, dps) if dps else leg.ret
        if dps:
            legs.dps_applied += dps
        legs.base[j], legs.close[j] = leg.base, float(row[1])
        legs.shrs[j] = float(row[3]) if row[3] else None
        legs.cap[j + 1] = cap_now
        legs.last_idx = j + 1
        prev, prev_dd = row, d
    return legs


def _group_code(key: pd.SectorKey | None, level: str) -> tuple[str, str]:
    if key is None:
        return UNCLASSIFIED, "미분류"
    return (key.mid_code, key.mid) if level == "mid" else (key.major_code, key.major)


# ═════════════════════════════════════════════════════════════════════════════
# 호출 상태
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class Ctx:
    """한 호출의 공용 상태 — 기간·해상도·옵션과 달력, 경고 모음."""
    days: list[str]
    plan: Plan
    basis: str
    rebalance: str
    weights: str
    dates: list[str] | None = None            # 일별이면 처음부터, 주간이면 첫 시세 읽기 뒤
    base: str = ""
    end: str = ""
    n_rows: int = 0
    latest_weekly: str = ""
    floor_weekly: str = ""
    cold: dict[str, Any] = field(default_factory=dict)
    weekly_lo: str = ""                       # 대상(첫 호출)이 주간 시세를 읽은 하한 — 뒤 호출(계산 벤치마크)이 같은 하한을 쓴다
    warnings: list[str] = field(default_factory=list)
    codes: list[str] = field(default_factory=list)
    names: dict[str, str] | None = None

    def warn(self, code: str | None, text: str) -> None:
        if text and text not in self.warnings:
            self.warnings.append(text)
        if code and code not in self.codes:
            self.codes.append(code)


async def _db(fn: Callable, *a):
    from open_proxy_mcp.services.price_multiple_data import _db_call
    return await _db_call(fn, *a)


def _rss_guard() -> None:
    """무거운 읽기 앞 메모리 가드 — RSS 가 콜드 서빙과 같은 문턱을 넘으면 거절한다."""
    from open_proxy_mcp import cold_store as _cold
    rss = _cold._rss_mb()
    if rss is not None and rss > _cold._rss_guard_mb():
        raise Invalid("invalid", f"서버 메모리 여유가 부족해(RSS {rss:.0f} MB) 이번 계산은 거절했습니다 — 잠시 뒤 다시 부르거나 대상·기간을 줄여 주세요.",
                      codes=["rss_guard"])


def _check_rows(ctx: Ctx, n_tickers: int, n_obs_est: int, what: str) -> None:
    est = n_tickers * (n_obs_est + 1)
    ctx.n_rows += est
    if ctx.n_rows > _max_rows():
        raise Invalid("invalid", f"{what}의 시세 행이 약 {est:,}개라 한 번에 읽는 상한({_max_rows():,}행)을 넘습니다 — 기간을 줄이거나 대상을 좁혀 주세요"
                                 "(주간 계산 벤치마크는 공식 지수로 바꾸면 행이 들지 않습니다).", codes=["row_limit"])


def _est_obs(ctx: Ctx) -> int:
    if ctx.dates:
        return len(ctx.dates) - 1
    if ctx.plan.freq == "daily":
        return len([d for d in ctx.days if ctx.base <= d <= ctx.end])
    return max(2, pm.calendar_days(ctx.plan.since_req, ctx.end) // 7 + 2)


# ═════════════════════════════════════════════════════════════════════════════
# 구성 종목·시세 읽기
# ═════════════════════════════════════════════════════════════════════════════

async def _names() -> dict[str, str]:
    from open_proxy_mcp.services.universe import _names_by_ticker
    try:
        return await _names_by_ticker()
    except Exception:                                 # noqa: BLE001 — 이름이 없다고 계산을 못 할 이유는 없다
        return {}


async def _resolve_members(ctx: Ctx, comp: Component) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """구성요소 → 종목 행(ticker·name·market·w)과 요약 정보."""
    info: dict[str, Any] = {"label": comp.label, "spec": comp.spec, "weight_pct": _pct(comp.weight, 4), "n_members": 0,
                            "sector_asof": None, "classification_retro": None, "membership_asof": None, "kind": "universe"}
    if comp.basket is not None:
        if ctx.names is None:
            ctx.names = await _names()
        rows = [{"ticker": t, "name": ctx.names.get(t) or "-", "market": None, "w": w} for t, w in comp.basket.items()]
        info.update(n_members=len(rows), membership_asof=ctx.plan.since_req, kind="basket")
        return rows, info
    ul = await list_universe(comp.spec, ctx.plan.since_req)
    if ul.question:
        raise Invalid("invalid", ul.question)
    if not ul.db_ok:
        raise _db_fail()
    if not ul.resolved or not ul.rows:
        raise Invalid("not_found" if not ul.resolved else "no_data", ul.notice or f"「{comp.spec}」에 해당하는 종목이 없습니다 — 표현을 바꿔 다시 부르세요.")
    rows = [dict(r) for r in ul.rows]
    info.update(n_members=len(rows), membership_asof=ul.as_of, label_resolved=ul.label,
                kind="custom" if (ul.spec or "").startswith("custom:") else "universe")
    if ul.sector is not None:
        info.update(kind="sector", sector_asof=ul.sector.get("classification_asof"), classification_retro=ul.sector.get("classification_retro"))
    if ul.extra_warnings:
        info["notes"] = list(ul.extra_warnings)
    if ul.notice:
        info.setdefault("notes", []).append(ul.notice)
    return rows, info


def _within_weights(rows: list[dict[str, Any]], mode: str, comp: Component, caps: dict[str, float | None]) -> dict[str, float]:
    """구성요소 안 시작 비중(합 1). 바스켓은 적은 비중, 시총은 시작일 시총, 동일은 1/N."""
    if comp.basket is not None:
        tot = sum(r["w"] for r in rows)
        return {r["ticker"]: r["w"] / tot for r in rows}
    if mode == "equal":
        return {r["ticker"]: 1.0 / len(rows) for r in rows}
    tot = sum((caps.get(r["ticker"]) or 0.0) for r in rows)
    if tot <= 0:
        return {r["ticker"]: 1.0 / len(rows) for r in rows}
    return {r["ticker"]: (caps.get(r["ticker"]) or 0.0) / tot for r in rows}


async def _weekly_bounds_once(ctx: Ctx) -> None:
    if ctx.plan.freq == "weekly" and not ctx.latest_weekly:
        from open_proxy_mcp.services.price_multiple_data import _weekly_bounds
        ctx.latest_weekly, ctx.floor_weekly = await _weekly_bounds()


async def _load_prices(ctx: Ctx, tickers: list[str], tids: dict[str, int]) -> dict[str, list[tuple]]:
    """종목별 시세 행 [(날짜, 종가, cmp, 주식수)]. 일별은 기준일~끝, 주간은 DB + 콜드. 주간이면 여기서(처음 한 번) ctx.dates 를 정한다."""
    plan = ctx.plan
    if plan.freq == "daily":
        px = await _db(pd.read_daily_px, {t: tids[t] for t in tickers if t in tids}, ctx.base, ctx.end)
        if px is None:
            raise _db_fail()
        return px
    lo = pd.shift_dd(plan.since_req, -10)
    first = ctx.dates is None
    if first:
        # 시작일이 DB 주간 하한 안이면 하한에서부터 읽는다 — 시작 10일 앞(기준 주를 찾는 여유)이 하한 앞이라 보관본(콜드)을 부르고 `cold.used`·`cold_coverage` 가 거짓으로 서던 것을 막는다(B1).
        lo_read = max(lo, ctx.floor_weekly) if plan.since_req >= ctx.floor_weekly else lo
    else:
        lo_read = ctx.weekly_lo or lo
    wr = await pd.read_weekly(tickers, lo_read, ctx.end, latest=ctx.latest_weekly, floor=ctx.floor_weekly)
    if not wr.db_ok:
        raise _db_fail()
    if first and lo_read != lo and not any(r[0] <= plan.since_req for rr in wr.rows.values() for r in rr):
        lo_read = lo                                  # 하한(주 첫날)과 첫 주 닫는 날 사이의 시작일 — DB 에 시작 이하 점이 없다. 종전 폭으로 다시 읽어 기준 주를 보관본에서 찾는다
        wr = await pd.read_weekly(tickers, lo_read, ctx.end, latest=ctx.latest_weekly, floor=ctx.floor_weekly)
        if not wr.db_ok:
            raise _db_fail()
    if first:
        ctx.weekly_lo = lo_read
        ctx.cold = {"used": wr.cold_used, "seam": wr.seam, "reason": wr.reason, "n_rows": wr.n_cold_rows, "floor": ctx.floor_weekly,
                    "needed": plan.since_req < ctx.floor_weekly}
    rows: dict[str, list[tuple]] = {t: [(r[0], r[1], None, r[2]) for r in wr.rows.get(t, [])] for t in tickers}
    union = sorted({r[0] for rr in rows.values() for r in rr})
    # 끝 기준일이 주 닫는 날이 아니면(이번 주 등) 일별 저장분의 그 날 한 줄을 끝점으로 잇는다.
    # 대상(첫 호출)과 계산 벤치마크(뒤 호출)가 같은 규칙을 쓰도록 한 곳에서 붙인다 — 뒤 호출은 첫 호출이 정한 격자(ctx.dates)가 끝점을 품고 있으면 그 날을 붙인다.
    if not first:
        need_tail = bool(ctx.dates) and ctx.end in ctx.dates and ctx.end not in union
    else:
        need_tail = bool(union) and ctx.end not in union and ctx.end >= ctx.days[0]
    if need_tail:
        got = await _db(pd.read_daily_px, {t: tids[t] for t in tickers if t in tids}, ctx.end, ctx.end)
        if got is None:
            raise _db_fail()
        if any(got.values()):
            union.append(ctx.end)
            for t, rr in got.items():
                rows[t] = list(rows.get(t, [])) + [(r[0], r[1], None, r[3]) for r in rr]
    if not first:
        dset = set(ctx.dates or [])
        return {t: [r for r in rr if r[0] in dset] for t, rr in rows.items()}
    on_or_before = [d for d in union if d <= plan.since_req]
    base = max(on_or_before) if on_or_before else (min(union) if union else "")
    if not base:
        raise Invalid("no_data", "그 기간의 주간 시세가 없습니다.")
    dates = [base] + [d for d in union if base < d <= ctx.end]
    if len(dates) < 2:
        raise Invalid("no_data", "그 기간에 관측일이 둘 이상 없습니다 — 기간을 넓혀 주세요.")
    ctx.base, ctx.dates = base, dates
    if not on_or_before and plan.since_req < ctx.floor_weekly and not wr.cold_used:
        why = {"off": "콜드 서빙이 꺼져 있어", "seam_after_start": "보관본 이음매가 시작보다 앞서 있어"}.get(wr.reason or "", "보관본을 지금 읽지 못해")
        ctx.cold["truncated_to"] = base
        ctx.warn("cold_coverage", f"요청한 시작일 {_ymd(plan.since_req)} 은 DB 주간 구간({_ymd(ctx.floor_weekly)}~) 밖인데 {why} 시작을 {_ymd(base)} 로 잘랐다 — "
                                  "부분 곡선은 내지 않는다.")
    elif wr.cold_used:
        if wr.seam and base >= wr.seam:                # 기준일이 이음매 이후 — 곡선에 보관본 점이 하나도 들어가지 않았다. 쓰지 않은 것을 썼다고 하지 않는다
            ctx.cold.update(used=False, seam=None)
        else:
            ctx.warn("cold_coverage", f"3년 밖 구간({_ymd(base)}~{_ymd(wr.seam or '')} 앞)은 DB 밖 보관본(콜드 서빙)에서 읽어 이었다 — 같은 원천·같은 주 정의이고 "
                                      f"이음매({_ymd(wr.seam or '')})에 겹치는 날은 없다.")
    dset = set(dates)
    return {t: [r for r in rr if r[0] in dset] for t, rr in rows.items()}


# ═════════════════════════════════════════════════════════════════════════════
# 포트폴리오 (대상·고정 구성 계산 벤치마크 공용)
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class Position:
    ticker: str
    name: str
    comp: int                      # 구성요소 번호
    market: str | None
    target0: float                 # 시작 비중(블렌드 비중 × 구성요소 안 비중)
    cap_weight: bool               # 시총 가중이면 리밸런스 목표를 그 날 시총으로 다시 잡는다


@dataclass
class Portfolio:
    comps: list[Component]
    positions: list[Position]
    legs: dict[str, TickerLegs]
    sim: pm.Simulation
    comp_info: list[dict[str, Any]]
    dates: list[str]
    freq: str
    sector_map: pd.SectorMap | None = None
    delisted: list[dict[str, str]] = field(default_factory=list)
    dropped: list[dict[str, str]] = field(default_factory=list)
    flags: list[dict[str, str]] = field(default_factory=list)
    dividend: dict[str, Any] = field(default_factory=dict)
    cmp_missing: int = 0
    tick_skipped: int = 0
    events_used: int = 0
    unusable: int = 0
    membership_asof: str = ""
    sector_kind: bool = False
    levels: dict[str, float] | None = None      # 지수 target 이면 날짜 → 지수 종가(포인트). 종목 대상은 None


async def _build_portfolio(ctx: Ctx, comps: list[Component], *, weights_mode: str, rebalance: str,
                           need_sector: bool, with_dividend: bool) -> Portfolio:
    """대상(또는 고정 구성 계산 벤치마크)을 구성 → 읽기 → 구간 열 → 시뮬레이션까지."""
    plan = ctx.plan
    await _weekly_bounds_once(ctx)
    max_pos = MAX_POSITIONS_DAILY if plan.freq == "daily" else MAX_POSITIONS_WEEKLY
    per_comp: list[tuple[list[dict[str, Any]], dict[str, Any]]] = [await _resolve_members(ctx, c) for c in comps]
    tickers = sorted({r["ticker"] for rows, _ in per_comp for r in rows})
    if len(tickers) > max_pos:
        raise Invalid("invalid", f"구성 종목이 {len(tickers):,}개로 상한({'일별' if plan.freq == 'daily' else '주간'} {max_pos}개)을 넘습니다 — 대상을 좁혀 주세요"
                                 + ("(기간이 일별 보관 창 밖이라 상한이 더 낮습니다)." if plan.freq == "weekly" else "."), codes=["row_limit"])
    if plan.freq == "weekly" and plan.since_req < ctx.floor_weekly and len(tickers) > MAX_COLD_READS:
        raise Invalid("invalid", f"3년 밖 시세는 종목마다 보관본을 읽어야 해서 종목 {MAX_COLD_READS}개까지만 됩니다(현재 {len(tickers)}개) — 대상을 좁혀 주세요.",
                      codes=["row_limit"])
    _check_rows(ctx, len(tickers), _est_obs(ctx), "대상")
    _rss_guard()
    tids = await _db(pd.read_tids, tickers)
    if tids is None:
        raise _db_fail()
    rows_by = await _load_prices(ctx, tickers, tids)
    dates = ctx.dates or []
    if len(dates) < 2:
        raise Invalid("no_data", "그 기간에 관측일이 둘 이상 없습니다.")
    base_dd, end_dd = dates[0], dates[-1]
    ev = await _db(pd.read_adj_events, tickers, base_dd, end_dd)
    # 시장(KS/KQ)은 일별 창 안의 날 행에서 읽는다 — 기준일이 창 밖(주간)이면 보관된 최신 거래일의 시장, 거기 없는 종목은 유니버스 행의 시장 이름으로 채운다
    markets = await _db(pd.read_markets, tids, base_dd if base_dd >= ctx.days[0] else ctx.days[-1])
    flags = await _db(pd.read_stock_flags, tickers)
    smap = await _db(pd.read_sector_map, tickers) if need_sector else None
    if ev is None or markets is None or (need_sector and smap is None):
        raise _db_fail()

    # 시작일 시세가 없는 종목은 빼고(나중 상장·거래 정지) 구성요소 안 비중을 다시 잡는다
    caps0: dict[str, float | None] = {}
    priced: dict[str, bool] = {}
    for t in tickers:
        r0 = next((r for r in rows_by.get(t, []) if r[0] == base_dd), None)
        priced[t] = bool(r0 and r0[1])
        caps0[t] = float(r0[1]) * float(r0[3]) if r0 and r0[1] and r0[3] else None
    dropped: list[dict[str, str]] = []
    positions: list[Position] = []
    comp_info: list[dict[str, Any]] = []
    for i, (c, (rows, info)) in enumerate(zip(comps, per_comp)):
        keep = [r for r in rows if priced.get(r["ticker"])]
        for r in rows:
            if not priced.get(r["ticker"]):
                dropped.append({"ticker": r["ticker"], "component": c.label, "reason": f"시작일 {base_dd} 시세 없음(그 뒤 상장·거래 정지)"})
        if not keep:
            raise Invalid("no_data", f"「{c.spec}」의 구성 종목 중 시작일 {_ymd(base_dd)} 에 시세가 있는 종목이 없습니다 — 시작일을 늦춰 보세요.")
        w_in = _within_weights(keep, weights_mode, c, caps0)
        for r in keep:
            positions.append(Position(r["ticker"], r.get("name") or "-", i, markets.get(r["ticker"]) or mkt_to_db(r.get("market")), c.weight * w_in[r["ticker"]],
                                      cap_weight=(c.basket is None and weights_mode == "cap")))
        info["n_members"] = len(keep)
        if info.get("kind") == "basket":
            info["membership_asof"] = base_dd              # 바스켓은 사용자가 고른 종목이라 실제 기준일 = 기준일
        comp_info.append(info)
    n = len(dates) - 1
    div_info: dict[str, Any] = {"basis": ctx.basis, "gross": True, "covered_weight_pct": None, "pending": [], "preferred_price_only": 0,
                                "ex_date_basis": None}
    dps_by: dict[str, dict[int, float]] = {}
    if with_dividend and ctx.basis == "total":
        await _dividends(ctx, positions, dates, dps_by, div_info)
    legs: dict[str, TickerLegs] = {}
    for t in {p.ticker for p in positions}:
        legs[t] = build_ticker_legs(rows_by.get(t, []), ev.get(t, []), dates, plan.freq, dps_by.get(t))
    ret = [legs[p.ticker].ret for p in positions]
    last_idx = [legs[p.ticker].last_idx for p in positions]
    targets: dict[int, list[float]] = {0: [p.target0 for p in positions]}
    reb = pm.rebalance_intervals(dates, rebalance)
    by_comp: dict[int, list[int]] = {}
    for pi, p in enumerate(positions):
        by_comp.setdefault(p.comp, []).append(pi)
    for j in reb:
        tgt = [p.target0 for p in positions]
        for k, members in by_comp.items():
            if not positions[members[0]].cap_weight:
                continue
            caps = {pi: ((legs[positions[pi].ticker].cap[j] or 0.0) if last_idx[pi] >= j + 1 else 0.0) for pi in members}
            tot = sum(caps.values())
            if tot > 0:
                for pi in members:
                    tgt[pi] = comps[k].weight * caps[pi] / tot
        targets[j] = tgt
    sim = pm.simulate(ret, targets, reb, last_idx)
    delisted = sorted(({"ticker": t, "last_dd": dates[lg.last_idx]} for t, lg in legs.items() if lg.last_idx < n), key=lambda x: x["ticker"])
    pf = Portfolio(comps, positions, legs, sim, comp_info, dates, plan.freq, smap, delisted, dropped,
                   [{"ticker": t, "flag": f} for t, f in sorted((flags or {}).items()) if t in legs], div_info)
    pf.cmp_missing = sum(lg.cmp_missing for lg in legs.values())
    pf.tick_skipped = sum(lg.tick_skipped for lg in legs.values())
    pf.events_used = sum(lg.events_used for lg in legs.values())
    pf.unusable = sum(lg.unusable for lg in legs.values())
    pf.membership_asof = max((i.get("membership_asof") or "" for i in comp_info), default="")
    pf.sector_kind = any(i.get("kind") == "sector" for i in comp_info)
    if flags is None:
        ctx.warn(None, "해석 주의 딱지(분할·합병 이력)를 읽지 못해 딱지 표시를 비웠다.")
    return pf


async def _dividends(ctx: Ctx, positions: list[Position], dates: list[str], dps_by: dict[str, dict[int, float]], info: dict[str, Any]) -> None:
    """배당 원장 → 종목별 {구간 번호: DPS 합}. 읽기 실패·원장 범위 밖은 그 몫을 가격 수익률로 두고 밝힌다(db_error 로 올리지 않는다)."""
    base, end = dates[0], dates[-1]
    tickers = sorted({p.ticker for p in positions})
    common = [t for t in tickers if not issuer_ticker(t)]
    pref = [t for t in tickers if issuer_ticker(t)]
    w_of: dict[str, float] = {}
    for p in positions:
        w_of[p.ticker] = w_of.get(p.ticker, 0.0) + p.target0
    info["preferred_price_only"] = len(pref)
    info["preferred_weight_pct"] = _pct(sum(w_of[t] for t in pref), 2)
    divs = (await _db(pd.read_dividends, common, base, pd.shift_dd(end, 10))) if common else []
    scope = await _db(pd.read_div_scope)
    if divs is None or scope is None:
        info["covered_weight_pct"] = 0.0
        ctx.warn("dividend_coverage", "배당 원장을 읽지 못해 총수익 대신 가격 수익률로 계산했다(배당 0).")
        return
    # 원장이 온전한 시작 연도인가 — 수집창 시작(window_lo) 이전 결의가 빠진 구간은 가격 수익률로 둔다
    lo_year = min((s.window_lo for s in scope if s.window_lo), default=None)
    ok_start = lo_year is not None and int(base[:4]) >= lo_year
    covered = sum(w_of[t] for t in common) if ok_start else 0.0
    info["covered_weight_pct"] = _pct(covered, 2)
    pending: list[dict[str, Any]] = []
    approx = False
    used = 0
    for d in divs if ok_start else []:
        ex, how = pm.ex_dividend_date(d.record_dd, ctx.days)
        j = pm.locate_interval(dates, ex)
        if j is None:
            continue
        if d.board_dd and d.board_dd > end:
            pending.append({"ticker": d.ticker, "record_dd": d.record_dd, "board_dd": d.board_dd, "dps": d.dps})
            continue
        approx = approx or how != "daily_calendar"
        dps_by.setdefault(d.ticker, {})
        dps_by[d.ticker][j] = dps_by[d.ticker].get(j, 0.0) + d.dps
        used += 1
    info["ex_date_basis"] = "weekday_approx" if (approx or base < ctx.days[0]) else "daily_calendar"
    info["pending"] = pending[:20]
    info["n_pending"] = len(pending)
    info["n_dividends_used"] = used
    uncovered = 1.0 - covered
    if uncovered > 0.005:
        reasons = []
        if pref:
            reasons.append(f"우선주 {len(pref)}종목(DPS 칸 없음)")
        if not ok_start:
            reasons.append(f"배당 원장이 {lo_year or '?'}년 결의부터라 시작 연도가 원장 밖")
        ctx.warn("dividend_coverage", f"배당을 넣지 못한 비중 {uncovered * 100:.1f}% 는 가격 수익률로 두었다 — " + ", ".join(reasons or ["원장 범위 밖"]) + ".")
    if pending:
        ctx.warn("dividend_pending", f"끝 기준일 {_ymd(end)} 뒤에 결의된 배당 {len(pending)}건은 아직 몰랐던 정보라 넣지 않았다(DATA-PIT9).")
    if info["ex_date_basis"] == "weekday_approx":
        ctx.warn(None, "일별 보관 창 밖의 배당락일은 평일 달력(12-31 휴장만 반영)으로 근사했다 — 설·추석 같은 평일 휴장은 못 본다(`ex_date_basis`=weekday_approx).")


# ═════════════════════════════════════════════════════════════════════════════
# 지수 target — 공식 지수 종가 자체를 대상으로
# ═════════════════════════════════════════════════════════════════════════════

NOMINAL_ANN = {"daily": 252, "weekly": 52}


async def _build_index_portfolio(ctx: Ctx, spec: str, name: str) -> tuple[Portfolio, pd.IndexSeries, list[pd.IndexSeries]]:
    """공식 지수 한 계열을 대상으로 구간 수익률 열을 만든다. 격자는 `plan.freq` 를 따른다 — 일별이면 일별 달력의 지수 행만, 주간이면 주 닫는 날(종목 시세와 같은 주 정의,
    DATA-PX6)의 지수 행만 쓰고(끝이 아직 닫히지 않은 주면 일별 행으로 끝점을 잇는다) 한 계산 안에 일별·주간 간격을 섞지 않는다. in_db=false 계열은 값을 만들지 않는다."""
    series = await _db(pd.read_index_series)
    if series is None:
        raise _db_fail()
    res = pd.resolve_index(name, series, arg="target", prefix="지수:")
    hit = res.hit
    if hit is None:
        if res.ambiguous or not name.strip():
            raise Invalid("invalid", res.question)
        more = f" 비슷한 이름: {', '.join(res.near)}." if res.near else ""
        raise Invalid("invalid", f"공식 지수 「{name}」을 찾지 못했습니다.{more} 지수 target 은 「코스피」「코스닥」이나 「지수:」 뒤에 공식 지수 이름을 적습니다(예: target=\"지수:코스피 200\").")
    if res.note:
        ctx.warn(None, res.note)
    if not hit.in_db:
        have = ", ".join(sorted({f"{pd.SRC_LABEL.get(x.src, x.src)} {x.name}" for x in series if x.in_db})[:8])
        raise Invalid("invalid", f"공식 지수 「{hit.name}」({pd.SRC_LABEL.get(hit.src, hit.src)})은 이 지수는 DB 에 일별 종가가 없습니다 — 값이 DB 밖 보관본에만 있어 이번 호출에서 읽지 않았고 값을 만들지 않았습니다."
                                 + (f" DB 에 일별 종가가 있는 지수: {have}." if have else ""))
    plan = ctx.plan
    truncated = False
    if plan.freq == "daily":
        got = await _db(pd.read_index_closes, hit.sid, ctx.base, ctx.end)
        if got is None:
            raise _db_fail()
        levels = dict(got)
        dates = [d for d in (ctx.dates or []) if d in levels]
        truncated = bool(dates) and dates[0] != ctx.base
    else:
        await _weekly_bounds_once(ctx)
        lo = pd.shift_dd(plan.since_req, -10)
        wk = await _db(pd.read_week_days, lo, ctx.end)
        got = await _db(pd.read_index_closes, hit.sid, lo, ctx.end)
        if wk is None or got is None:
            raise _db_fail()
        levels = dict(got)
        grid = sorted({d for d in wk if d in levels})
        # 끝 기준일이 주 닫는 날이 아니면(이번 주 등) 일별 행의 그 날 한 줄을 끝점으로 잇는다 — 종목 시세와 같은 규칙
        if grid and ctx.end not in grid and ctx.end >= ctx.days[0] and ctx.end in levels and ctx.end > grid[-1]:
            grid.append(ctx.end)
        on_or_before = [d for d in grid if d <= plan.since_req]
        base = max(on_or_before) if on_or_before else (min(grid) if grid else "")
        dates = [base] + [d for d in grid if base < d <= ctx.end] if base else []
        truncated = bool(base) and not on_or_before
    if len(dates) < 2:
        raise Invalid("no_data", f"공식 지수 「{hit.name}」의 그 기간 종가가 둘 이상 없습니다 — 기간을 넓히거나 시작일을 늦춰 보세요(지수 이력은 최근 3년뿐입니다).")
    if truncated:
        ctx.warn("index_truncated", f"요청한 시작일 {_ymd(plan.since_req)} 은 공식 지수 「{hit.name}」의 DB 이력(첫 관측 {_ymd(dates[0])}) 앞이라 시작을 {_ymd(dates[0])} 로 잘랐다 — "
                                    "그 앞 지수 값은 DB 에 없다.")
    ctx.base, ctx.dates = dates[0], dates
    lv = {d: levels[d] for d in dates}
    if any(v <= 0 for v in lv.values()):
        raise Invalid("no_data", f"공식 지수 「{hit.name}」의 종가에 0 이하 값이 있어 계산하지 않았습니다.")
    rp = [lv[dates[j + 1]] / lv[dates[j]] - 1.0 for j in range(len(dates) - 1)]
    comp = Component(label=hit.name, spec=spec, weight=1.0)
    info = {"label": hit.name, "spec": spec, "weight_pct": 100.0, "n_members": 0, "sector_asof": None, "classification_retro": None,
            "membership_asof": dates[0], "kind": "index", "label_resolved": hit.name}
    pf = Portfolio([comp], [], {}, pm.Simulation([], rp), [info], dates, plan.freq, levels=lv)
    if plan.freq == "weekly":
        ctx.warn("index_weekly_resolution", "주간 계산이다 — 지수의 주 마지막 거래일 종가 한 점씩만 써서 변동성·최대낙폭이 주간 종가 기준이다. 그 주 안에서 일어난 낙폭과 변동은 놓칠 수 있다"
                                            "(그 사이 낙폭을 보려면 시작일을 일별 보관 창(약 1년) 안으로).")
    return pf, hit, series


# ═════════════════════════════════════════════════════════════════════════════
# 벤치마크
# ═════════════════════════════════════════════════════════════════════════════

@dataclass
class Bench:
    label: str
    source: str                                    # "official" | "computed"
    series: dict[str, Any]
    rb: list[float | None]                         # 구간 수익률(없으면 None)
    reason: str = ""
    groups: list[dict[str, tuple[float, float | None]]] | None = None   # 구간별 업종 (비중, 수익률) — Brinson 용
    group_names: dict[str, str] = field(default_factory=dict)
    cmp_missing: int = 0
    kind: str = ""
    retro: bool = False
    levels: dict[str, float] | None = None         # 공식 지수면 격자 날짜 → 종가(포인트). 계산 벤치마크는 None(까닭은 `_NO_POINTS`)


_NO_POINTS = "계산 벤치마크(유니버스 문구)는 시총 가중으로 이은 수익률이라 지수 종가(포인트)가 없다 — 포인트 칸은 비웠다."


def _levels_to_returns(levels: dict[str, float], dates: Sequence[str]) -> list[float | None]:
    out: list[float | None] = []
    for j in range(len(dates) - 1):
        a, b = levels.get(dates[j]), levels.get(dates[j + 1])
        out.append(b / a - 1.0 if a and b else None)
    return out


async def _official_bench(ctx: Ctx, name: str, *, default_for: str | None, pf: Portfolio) -> Bench:
    series = await _db(pd.read_index_series)
    if series is None:
        raise Invalid("benchmark_unavailable")
    reason = ""
    if default_for is not None:
        label = "코스피" if default_for == KS else "코스닥"
        res = pd.resolve_index(label, series)
        reason = f"benchmark 를 비워 시작일 비중이 더 큰 시장({to_label(default_for)})의 공식 지수 「{label}」을 썼다(외국주 제외 가격지수)."
    else:
        res = pd.resolve_index(name, series)
        if res.question:
            raise Invalid("invalid", res.question)
        if res.note:
            ctx.warn(None, res.note)
    hit = res.hit
    if hit is None:
        raise Invalid("benchmark_unavailable")
    if not hit.in_db:
        raise Invalid("invalid", f"공식 지수 「{hit.name}」({pd.SRC_LABEL.get(hit.src, hit.src)})은 값이 DB 밖 보관본에만 있어 이번 호출에서 읽지 않습니다 — "
                                 "DB 에 값이 있는 지수(코스피·코스닥·규모·업종 지수)나 계산 벤치마크(예: \"코스피 전체\")를 고르세요.")
    dates = pf.dates
    closes = await _db(pd.read_index_closes, hit.sid, dates[0], dates[-1])
    if closes is None:
        raise Invalid("benchmark_unavailable")
    dset = set(dates)
    levels = {dd: v for dd, v in closes if dd in dset}
    rb = _levels_to_returns(levels, dates)
    if hit.kind == "s":
        ctx.warn("official_vs_wics", f"공식 업종 지수 「{hit.name}」은 KRX 가 정한 구성이라 WICS 업종({'' if not pf.sector_kind else '대상의 업종 분류, '}"
                                     "분류 스냅샷)과 구성이 다르다 — 같은 이름이어도 종목이 다를 수 있다.")
    label = hit.name if (hit.src == "X" or hit.kind == "z") else f"{pd.SRC_LABEL.get(hit.src, hit.src)} {hit.name}"
    return Bench(label, "official", {"src": hit.src, "name": hit.name, "kind": hit.kind}, rb, reason, kind=hit.kind, levels=levels)


async def _market_bench(ctx: Ctx, markets: list[str], pf: Portfolio, level: str, label: str, reason: str) -> Bench:
    """전 시장 계산 벤치마크 — 일별은 DB 집계 한 질의, 주간은 시장 전 종목 행을 읽어 같은 식으로 계산한다."""
    dates = pf.dates
    n = len(dates) - 1
    names: dict[str, str] = {}
    per_j: list[dict[str, list[float]]] = [dict() for _ in range(n)]      # j → 업종 코드 → [num, den]
    cmp_missing = 0
    retro = False
    if ctx.plan.freq == "daily":
        got = await _db(pd.read_bench_daily, markets, dates[0], dates[-1])
        if got is None:
            raise Invalid("benchmark_unavailable")
        idx = {d: j for j, d in enumerate(dates[1:])}
        for g in got:
            j = idx.get(g.dd)
            if j is None:
                continue
            code, nm = _group_code(g.key, level)
            names[code] = nm
            acc = per_j[j].setdefault(code, [0.0, 0.0])
            acc[0] += g.num
            acc[1] += g.den
            cmp_missing += g.n_null
        smap_first = await _db(pd.read_sector_snaps)
        retro = bool(smap_first) and dates[0] < smap_first[0]
    else:
        if dates[0] < ctx.floor_weekly:
            raise Invalid("invalid", f"전 시장 계산 벤치마크(주간)는 DB 주간 구간({_ymd(ctx.floor_weekly)}~) 안에서만 됩니다 — 시작일이 그보다 이릅니다. "
                                     "공식 지수(예: benchmark=\"코스피\")는 보관된 범위만큼 겹치는 구간으로 비교합니다.", codes=["benchmark_truncated"])
        counts = await _db(pd.read_market_counts, ctx.days[-1])
        if counts is None:
            raise Invalid("benchmark_unavailable")
        n_t = sum(counts.get(m, 0) for m in markets)
        _check_rows(ctx, n_t, _est_obs(ctx), "계산 벤치마크(전 시장 주간)")
        _rss_guard()
        rows_all = await _db(pd.read_weekly_market_px, markets, dates[0], dates[-1], dates[-1])
        if rows_all is None:
            raise Invalid("benchmark_unavailable")
        dset = set(dates)
        tick = sorted(rows_all)
        ev = await _db(pd.read_adj_events, tick, dates[0], dates[-1])
        smap = await _db(pd.read_sector_map, tick)
        if ev is None or smap is None:
            raise Invalid("benchmark_unavailable")
        for t in tick:
            rr = [(r[0], r[1], None, r[2]) for r in rows_all[t] if r[0] in dset]
            lg = build_ticker_legs(rr, ev.get(t, []), dates, "weekly")
            if not lg.priced_at_start:
                continue
            for j in range(n):
                if lg.base[j] is None or lg.shrs[j] is None:
                    continue
                code, nm = _group_code(smap.at(t, dates[j]), level)
                names[code] = nm
                acc = per_j[j].setdefault(code, [0.0, 0.0])
                acc[0] += (lg.close[j] - lg.base[j]) * lg.shrs[j]
                acc[1] += lg.base[j] * lg.shrs[j]
        retro = smap.retro
    rb: list[float | None] = []
    groups: list[dict[str, tuple[float, float | None]]] = []
    for acc in per_j:
        den = sum(v[1] for v in acc.values())
        num = sum(v[0] for v in acc.values())
        if den <= 0:
            rb.append(None)
            groups.append({})
            continue
        rb.append(num / den)
        groups.append({c: (v[1] / den, (v[0] / v[1]) if v[1] > 0 else None) for c, v in acc.items()})
    wide = "·".join(to_label(m) or m for m in markets)
    return Bench(label, "computed", {"src": "computed", "name": label, "kind": "market"}, rb, reason, groups, names,
                 cmp_missing=cmp_missing, kind="market", retro=retro)


async def _fixed_bench(ctx: Ctx, text: str, pf: Portfolio, need_sector: bool, level: str) -> Bench:
    """유니버스 문구 계산 벤치마크 — 시작일 구성을 고정하고 시총 가중으로 표류(리밸런스 없음). 가격 수익률이다."""
    saved_basis = ctx.basis
    ctx.basis = "price"
    try:
        b = await _build_portfolio(ctx, [Component(text.strip(), text.strip(), 1.0)], weights_mode="cap", rebalance="none",
                                   need_sector=need_sector, with_dividend=False)
    finally:
        ctx.basis = saved_basis
    groups = None
    names: dict[str, str] = {}
    if need_sector and b.sector_map is not None:
        groups = []
        for j in range(len(b.dates) - 1):
            w = b.sim.weights_start[j]
            keys = [b.sector_map.at(p.ticker, b.dates[j]) for p in b.positions]
            acc: dict[str, list[float]] = {}
            for wi, p, k in zip(w, b.positions, keys):
                code, nm = _group_code(k, level)
                names[code] = nm
                a = acc.setdefault(code, [0.0, 0.0])
                a[0] += wi
                a[1] += wi * b.legs[p.ticker].ret[j]
            groups.append({c: (v[0], (v[1] / v[0]) if v[0] > 0 else None) for c, v in acc.items()})
    return Bench(text.strip(), "computed", {"src": "computed", "name": text.strip(), "kind": "universe"}, list(b.sim.rp),
                 f"benchmark 「{text.strip()}」: 시작일 구성을 고정한 시총 가중(리밸런스 없음) 계산 벤치마크.", groups, names,
                 cmp_missing=b.cmp_missing, kind="universe", retro=bool(b.sector_map and b.sector_map.retro))


# ═════════════════════════════════════════════════════════════════════════════
# 지표
# ═════════════════════════════════════════════════════════════════════════════

def _rf_for(ctx: Ctx, rf_arg: str, rate_obs: list[tuple[str, float]] | None, dates: Sequence[str]) -> tuple[pm.RfAccrual | None, dict[str, Any]]:
    """무위험 금리 적립. 숫자(연 %)를 주면 고정 금리, 비우면 DB 의 국고채 1년. 못 읽거나 구간 시작 이전 관측이 없으면 해당 구간은 None."""
    info: dict[str, Any] = {"series": "국고채 1년(ECOS 817Y002/010190000)", "source": "ecos_rate", "avg_pct": None, "asof": None,
                            "convention": "ACT/365", "missing": None}
    arg = (rf_arg or "").strip()
    if arg:
        try:
            v = float(arg.replace("%", ""))
        except ValueError:
            raise Invalid("invalid", f"rf 「{rf_arg}」을 숫자(연 %)로 읽지 못했습니다 — 비우면 국고채 1년, 숫자를 주면 그 고정 금리(예: rf=\"3.0\").")
        acc = pm.fixed_rf_accrual(v, dates)
        info.update(series="고정 금리", source="user", avg_pct=_num(v, 4), missing=0)
        return acc, info
    if rate_obs is None:
        info["missing"] = len(dates) - 1
        ctx.warn("rf_unavailable", "무위험 금리(ECOS 국고채 1년)를 읽지 못해 샤프·소르티노·알파·하방 변동성을 비웠다(빈 값) — 나머지 지표는 그대로다.")
        return None, info
    acc = pm.rf_accrual(rate_obs, dates)
    info.update(avg_pct=_num(acc.avg_pct, 4), asof=acc.asof, missing=acc.missing)
    if acc.missing >= len(dates) - 1:
        ctx.warn("rf_unavailable", "무위험 금리 관측이 이 기간 시작보다 앞서 있지 않아(금리 표는 최근 3년) 샤프·소르티노·알파·하방 변동성을 비웠다(빈 값).")
        return None, info
    if acc.missing:
        ctx.warn("rf_unavailable", f"무위험 금리가 있는 {len(dates) - 1 - acc.missing}개 구간만으로 샤프·소르티노·하방 변동성을 냈다(금리 관측 이전 {acc.missing}개 구간은 뺐다) — "
                                   "알파는 금리가 비는 구간이 맞대는 구간에 끼면 비운다.")
    return acc, info


def _risk_metrics(rp: list[float], dates: list[str], rf: pm.RfAccrual | None) -> dict[str, Any]:
    days = pm.calendar_days(dates[0], dates[-1])
    a = pm.obs_per_year(len(rp), days)
    cum = pm.cumulative(rp)
    mdd = pm.max_drawdown(rp, dates)
    out: dict[str, Any] = {"return_cum_pct": _pct(cum), "return_ann_pct": _pct(pm.annualize(cum, days)),
                           "vol_ann_pct": _pct(pm.vol_ann(rp, a)), "downside_vol_ann_pct": None,
                           "mdd_pct": _pct(mdd[0]) if mdd else None, "mdd_from": mdd[1] if mdd else None, "mdd_to": mdd[2] if mdd else None,
                           "sharpe": None, "sortino": None}
    if rf is not None:
        ex = [r - f for r, f in zip(rp, rf.per_interval) if f is not None]
        out["downside_vol_ann_pct"] = _pct(pm.downside_vol_ann(ex, a))
        out["sharpe"] = _num(pm.sharpe(ex, a))
        out["sortino"] = _num(pm.sortino(ex, a))
    return out


def _relative(rp: list[float], bench: Bench, dates: list[str], rf: pm.RfAccrual | None) -> dict[str, Any]:
    j_ok = [j for j, b in enumerate(bench.rb) if b is not None]
    out: dict[str, Any] = {"benchmark": bench.label, "benchmark_source": bench.source, "benchmark_series": bench.series,
                           "benchmark_reason": bench.reason or None, "benchmark_return_cum_pct": None, "excess_cum_pct": None, "excess_ann_pct": None,
                           "beta": None, "alpha_ann_pct": None, "corr": None, "tracking_error_pct": None, "info_ratio": None,
                           "n_obs_aligned": len(j_ok),
                           # 아래는 벤치마크 블록에 대상과 같은 칸을 맞춘 것 — 모두 **맞대어 본 겹친 기간**(`period`) 기준이다(B2: 대상 전체 기간의 누적과 섞이지 않게 같은 기간 값을 따로 둔다)
                           "period": None, "target_return_cum_pct": None, "benchmark_vol_ann_pct": None, "benchmark_mdd_pct": None, "benchmark_mdd_from": None,
                           "benchmark_mdd_to": None, "benchmark_sharpe": None, "benchmark_start_point": None, "benchmark_end_point": None,
                           "benchmark_points_reason": None if bench.levels is not None else _NO_POINTS}
    if not j_ok:
        return out
    p = [rp[j] for j in j_ok]
    b = [bench.rb[j] for j in j_ok]       # type: ignore[misc]
    d0, d1 = dates[j_ok[0]], dates[j_ok[-1] + 1]
    days = pm.calendar_days(d0, d1)
    a = pm.obs_per_year(len(j_ok), days)
    cp, cb = pm.cumulative(p), pm.cumulative(b)
    out["benchmark_return_cum_pct"] = _pct(cb)
    out["excess_cum_pct"] = _pct(cp - cb)
    out["target_return_cum_pct"] = _pct(cp)
    out["period"] = {"start": d0, "end": d1, "n_obs": len(j_ok)}
    contiguous = j_ok == list(range(j_ok[0], j_ok[-1] + 1))
    rf_b = [rf.per_interval[j] for j in j_ok] if rf is not None else None
    mdd = pm.max_drawdown(b, dates[j_ok[0]:j_ok[-1] + 2]) if contiguous else None      # 겹침이 끊겨 있으면 낙폭 날짜를 내지 않는다
    out["benchmark_vol_ann_pct"] = _pct(pm.vol_ann(b, a))
    out["benchmark_mdd_pct"], out["benchmark_mdd_from"], out["benchmark_mdd_to"] = (_pct(mdd[0]), mdd[1], mdd[2]) if mdd else (None, None, None)
    if rf_b is not None:
        out["benchmark_sharpe"] = _num(pm.sharpe([x - f for x, f in zip(b, rf_b) if f is not None], a))
    if bench.levels is not None:
        out["benchmark_start_point"], out["benchmark_end_point"] = bench.levels.get(d0), bench.levels.get(d1)
    ap, ab = pm.annualize(cp, days), pm.annualize(cb, days)
    out["excess_ann_pct"] = _pct(ap - ab) if ap is not None and ab is not None else None
    have_rf = rf is not None and all(rf.per_interval[j] is not None for j in j_ok)
    if have_rf:
        ex_p = [x - rf.per_interval[j] for x, j in zip(p, j_ok)]          # type: ignore[union-attr,operator]
        ex_b = [x - rf.per_interval[j] for x, j in zip(b, j_ok)]          # type: ignore[union-attr,operator]
        beta, alpha = pm.beta_alpha(ex_p, ex_b, a)
    else:
        beta, _ = pm.beta_alpha(p, b, a)
        alpha = None
    out.update(beta=_num(beta), alpha_ann_pct=_pct(alpha), corr=_num(pm.correlation(p, b)),
               tracking_error_pct=_pct(pm.tracking_error(p, b, a)), info_ratio=_num(pm.info_ratio(p, b, a)))
    return out


def _contributions(pf: Portfolio, group_by: str) -> list[dict[str, Any]]:
    sim, pos, dates = pf.sim, pf.positions, pf.dates
    n = len(dates) - 1
    comps = pf.comps
    c_mat = [[sim.weights_start[j][pi] * pf.legs[p.ticker].ret[j] for pi, p in enumerate(pos)] for j in range(n)]
    linked = pm.link_contributions(c_mat, sim.rp)
    w_end = [sim.weights_start[n - 1][pi] * (1.0 + pf.legs[p.ticker].ret[n - 1]) / (1.0 + sim.rp[n - 1]) if (1.0 + sim.rp[n - 1]) > 1e-12 else 0.0
             for pi, p in enumerate(pos)]
    smap = pf.sector_map

    def key_at(p: Position, j: int) -> pd.SectorKey | None:
        return smap.at(p.ticker, dates[j]) if smap else None

    if group_by == "stock":
        rows = []
        for pi, p in enumerate(pos):
            k = key_at(p, 0)
            rows.append({"ticker": p.ticker, "name": p.name, "component": comps[p.comp].label,
                         "sector": (k.mid if k else (UNCLASSIFIED if smap else None)),
                         "weight_start_pct": _pct(sim.weights_start[0][pi]), "weight_end_pct": _pct(w_end[pi]),
                         "return_pct": _pct(pm.cumulative(pf.legs[p.ticker].ret)), "contribution_pct": _pct(linked[pi])})
        rows.sort(key=lambda r: -(r["contribution_pct"] or 0.0))
        return rows
    if group_by == "component":
        groups = [[pi for pi, p in enumerate(pos) if p.comp == k] for k in range(len(comps))]
        names = [c.label for c in comps]
        codes = [f"C{k + 1}" for k in range(len(comps))]
        per_j = [[sum(c_mat[j][pi] for pi in g) for g in groups] for j in range(n)]
        wj = [[sum(sim.weights_start[j][pi] for pi in g) for g in groups] for j in range(n)]
        wend = [sum(w_end[pi] for pi in g) for g in groups]
    else:
        level = group_by
        order: list[str] = []
        gnames: dict[str, str] = {}
        member_j: list[dict[str, list[int]]] = []
        for j in range(n):
            m: dict[str, list[int]] = {}
            for pi, p in enumerate(pos):
                code, nm = _group_code(key_at(p, j), level)
                gnames[code] = nm
                m.setdefault(code, []).append(pi)
                if code not in order:
                    order.append(code)
            member_j.append(m)
        codes, names = order, [gnames[c] for c in order]
        per_j = [[sum(c_mat[j][pi] for pi in member_j[j].get(c, [])) for c in order] for j in range(n)]
        wj = [[sum(sim.weights_start[j][pi] for pi in member_j[j].get(c, [])) for c in order] for j in range(n)]
        last = member_j[n - 1]
        wend = [sum(w_end[pi] for pi in last.get(c, [])) for c in order]
    linked_g = pm.link_contributions(per_j, sim.rp)
    rows = []
    for gi, (code, nm) in enumerate(zip(codes, names)):
        r_series = [(per_j[j][gi] / wj[j][gi]) for j in range(n) if wj[j][gi] > 1e-12]
        rows.append({"ticker": code, "name": nm, "component": None, "sector": nm if group_by in ("mid", "major") else None,
                     "weight_start_pct": _pct(wj[0][gi]), "weight_end_pct": _pct(wend[gi]),
                     "return_pct": _pct(pm.cumulative(r_series)) if r_series else None, "contribution_pct": _pct(linked_g[gi])})
    rows.sort(key=lambda r: -(r["contribution_pct"] or 0.0))
    return rows


def _attribution(pf: Portfolio, bench: Bench, level: str) -> dict[str, Any]:
    dates, sim, pos = pf.dates, pf.sim, pf.positions
    n = len(dates) - 1
    smap = pf.sector_map
    periods: list[pm.BrinsonPeriod] = []
    names: dict[str, str] = dict(bench.group_names)
    acc_wp: dict[str, float] = {}
    acc_wb: dict[str, float] = {}
    cp: dict[str, float] = {}
    cb: dict[str, float] = {}
    seen_p: dict[str, bool] = {}
    seen_b: dict[str, bool] = {}
    for j in range(n):
        ws = sim.weights_start[j]
        gw: dict[str, list[float]] = {}
        for pi, p in enumerate(pos):
            code, nm = _group_code(smap.at(p.ticker, dates[j]) if smap else None, level)
            names.setdefault(code, nm)
            a = gw.setdefault(code, [0.0, 0.0])
            a[0] += ws[pi]
            a[1] += ws[pi] * pf.legs[p.ticker].ret[j]
        bj = (bench.groups or [{}] * n)[j]
        groups: dict[str, pm.GroupPeriod] = {}
        for code in set(gw) | set(bj):
            wp, rpw = (gw[code][0], (gw[code][1] / gw[code][0]) if gw[code][0] > 1e-12 else None) if code in gw else (0.0, None)
            wb, rbw = bj.get(code, (0.0, None))
            groups[code] = pm.GroupPeriod(wp, rpw, wb, rbw)
            acc_wp[code] = acc_wp.get(code, 0.0) + wp
            acc_wb[code] = acc_wb.get(code, 0.0) + wb
            if rpw is not None and wp > 1e-12:
                cp[code] = (cp.get(code, 1.0)) * (1.0 + rpw)
                seen_p[code] = True
            if rbw is not None and wb > 1e-12:
                cb[code] = (cb.get(code, 1.0)) * (1.0 + rbw)
                seen_b[code] = True
        periods.append(pm.brinson_fachler(groups))
    link = pm.link_attribution(periods)
    rows = []
    for code, (a, s, i) in link.by_group.items():
        rows.append({"code": code, "name": names.get(code, code), "w_p_pct": _pct(acc_wp.get(code, 0.0) / n, 4), "w_b_pct": _pct(acc_wb.get(code, 0.0) / n, 4),
                     "r_p_pct": _pct(cp[code] - 1.0) if seen_p.get(code) else None, "r_b_pct": _pct(cb[code] - 1.0) if seen_b.get(code) else None,
                     "allocation_pct": _pct(a), "selection_pct": _pct(s), "interaction_pct": _pct(i)})
    rows.sort(key=lambda r: -abs((r["allocation_pct"] or 0) + (r["selection_pct"] or 0) + (r["interaction_pct"] or 0)))
    first = (smap.first if smap else None)
    return {"group_by": "중분류" if level == "mid" else "대분류", "linking": "carino", "by_group": rows,
            "allocation_pct": _pct(link.allocation), "selection_pct": _pct(link.selection), "interaction_pct": _pct(link.interaction),
            "active_pct": _pct(link.active), "residual_pct": _pct(link.residual, 8),
            "w_basis": "기간 평균 기초 비중", "r_basis": "업종 구간 수익률의 복리",
            "classification": {"first_snapshot": first, "retro": bool((smap and smap.retro) or bench.retro),
                               "taxonomy_break": dates[0] < TAXONOMY_BREAK_DD}}


def _series_rows(pf: Portfolio, bench: Bench | None) -> list[dict[str, Any]]:
    """누적 시계열. 벤치마크 누적은 **벤치마크가 시작되는 날(첫 구간의 시작일)을 0 으로** 센다 — 대상 이력이 더 길면 그 앞 행의 벤치마크 칸은 비운다(B2: 기간이 다른 값을 0% 로 위장해 섞지 않는다).
    `point`(지수 target 의 지수 종가)·`bench_point`(공식 지수 벤치마크의 종가)는 그 날 포인트이고 없으면 null."""
    dates = pf.dates
    w = pm.wealth_curve(pf.sim.rp)
    blv = bench.levels if bench is not None and bench.levels is not None else {}
    bw: list[float | None] = [None] * len(dates)
    if bench:
        j0 = next((j for j, b in enumerate(bench.rb) if b is not None), None)
        if j0 is not None:
            cur = 1.0
            bw[j0] = cur
            for j in range(j0, len(bench.rb)):
                b = bench.rb[j]
                if b is not None:
                    cur *= (1.0 + b)
                bw[j + 1] = cur                                           # 중간에 빈 구간은 직전 값을 잇는다
    out: list[dict[str, Any]] = []
    for j, dd in enumerate(dates):
        row: dict[str, Any] = {"dd": dd, "cum_pct": 0.0 if j == 0 else _pct(w[j] - 1.0), "bench_cum_pct": None if bw[j] is None else _pct(bw[j] - 1.0)}
        if pf.levels is not None:
            row["point"] = pf.levels.get(dd)
        row["bench_point"] = blv.get(dd)
        out.append(row)
    return out


async def _pick_benchmark(ctx: Ctx, pf: Portfolio, bname: str, sc: str, level: str, main_mkt: str) -> Bench | None:
    """벤치마크를 고른다 — 비우면 공식 지수(attribution 이면 대상 시장의 계산 벤치마크), 공식 지수 이름이면 공식, 유니버스 문구면 계산.
    읽기 실패(`benchmark_unavailable`)는 상대 지표만 비우고 경고한다. Brinson 분해에서는 벤치마크가 필수라 DB 장애로 올린다."""
    try:
        if sc == "attribution":
            wide = market_wide(bname) if bname else [main_mkt]
            if bname and wide is None:
                series = await _db(pd.read_index_series)
                if series is not None and pd.resolve_index(bname, series).hit is not None:
                    raise Invalid("invalid", f"scope=\"attribution\" 은 계산 벤치마크만 됩니다 — 공식 지수 「{bname}」는 구성 이력이 없어 업종 분해를 할 수 없습니다. "
                                             "benchmark 를 비우거나(대상 시장의 \"코스피 전체\"/\"코스닥 전체\") 유니버스 문구(예: \"코스피 전체\", \"대분류 IT\")로 주세요.")
            if wide is not None:
                lab = bname or ("코스피 전체" if main_mkt == KS else "코스닥 전체")
                why = "" if bname else f"benchmark 를 비워 대상 시장({to_label(main_mkt)})의 「{lab}」 계산 벤치마크를 썼다."
                return await _market_bench(ctx, wide, pf, level, lab, why)
            return await _fixed_bench(ctx, bname, pf, True, level)
        if not bname:
            return await _official_bench(ctx, "", default_for=main_mkt, pf=pf)
        wide = market_wide(bname)
        if wide is not None:
            return await _market_bench(ctx, wide, pf, level, bname, "")
        series = await _db(pd.read_index_series)
        res = pd.resolve_index(bname, series) if series is not None else None
        if res is not None and (res.hit is not None or "여러 시장" in res.question):
            return await _official_bench(ctx, bname, default_for=None, pf=pf)
        try:
            return await _fixed_bench(ctx, bname, pf, False, level)
        except Invalid as e:
            if res is not None and e.status in ("invalid", "not_found", "no_data"):
                raise Invalid("invalid", res.question + " 유니버스 문구로도 읽지 못했습니다: " + " ".join(e.warns), codes=e.codes)
            raise
    except Invalid as e:
        if e.status != "benchmark_unavailable":
            raise
        if sc == "attribution":
            raise _db_fail()
        ctx.warn("benchmark_unavailable", "벤치마크를 읽지 못해 상대 지표(초과수익·베타·알파·추적오차·정보비율)를 비웠다 — 대상 자체 지표는 그대로다.")
        return None


# ═════════════════════════════════════════════════════════════════════════════
# 진입점
# ═════════════════════════════════════════════════════════════════════════════

async def build_performance_payload(target: str, *, weights: str = "시총", since: str = "", as_of: str = "", benchmark: str = "",
                                    scope: str = "summary", group_by: str = "중분류", basis: str = "price", rebalance: str = "none",
                                    rf: str = "", freq: str = "") -> dict[str, Any]:
    subject = (target or "").strip() or "성과 분석"
    try:
        return await _run(subject, target, weights, since, as_of, benchmark, scope, group_by, basis, rebalance, rf, freq)
    except Invalid as e:
        return _err(subject, e.status, *e.warns, codes=e.codes)


def _pick(opts: dict[str, str], val: str, name: str, default: str) -> str:
    key = (val or "").strip().lower() if val is not None else ""
    if key == "":
        return default
    if key in opts:
        return opts[key]
    if (val or "").strip() in opts:
        return opts[(val or "").strip()]
    raise Invalid("invalid", f"{name} 「{val}」은 지원하지 않는 값입니다 — {' / '.join(sorted(set(opts.values())))} 중에서 고르세요.")


async def _run(subject: str, target: str, weights: str, since: str, as_of: str, benchmark: str, scope: str, group_by: str,
               basis: str, rebalance: str, rf_arg: str, freq: str) -> dict[str, Any]:
    sc = _pick({s: s for s in SCOPES}, scope, "scope", "summary")
    gb = _pick(_GROUPS, group_by, "group_by", "mid")
    bs = _pick(_BASIS, basis, "basis", "price")
    rb_mode = _pick(_REBAL, rebalance, "rebalance", "none")
    wt = _pick(_WEIGHTS, weights, "weights", "cap")
    fr = _pick(_FREQS, freq, "freq", "")
    parsed = parse_target(target)
    if parsed.error:
        raise Invalid("invalid", parsed.error)
    if sc == "attribution" and gb in ("stock", "component"):
        raise Invalid("invalid", "scope=\"attribution\"(업종 Brinson 분해)의 group_by 는 중분류·대분류만 됩니다.")
    idx_name = index_target(parsed)
    if parsed.blend and any(_INDEX_PREFIX.match(c.spec) for c in parsed.components):
        raise Invalid("invalid", "블렌드 구성요소에는 「지수:」 표기를 쓸 수 없습니다 — 공식 지수 target 은 블렌드·바스켓으로 섞지 않고 단일 문구로만 줍니다(예: target=\"지수:코스피 200\"). "
                                 "지수의 구성 종목을 섞으려면 유니버스 문구(예: \"코스피200\")를 쓰세요.")
    if idx_name is not None and sc in ("attribution", "contribution"):
        raise Invalid("invalid", f"scope=\"{sc}\" 에 공식 지수 target 은 쓸 수 없습니다 — 공식 지수는 구성 이력이 없어 "
                                 f"{'업종 분해' if sc == 'attribution' else '구성 종목별 기여 분해'}를 할 수 없습니다. 구성 종목을 대상으로 하려면 유니버스 문구(예: \"코스피200\", \"코스피 시총 상위 50\")를 target 으로 주세요.")
    days = await _db(pd.read_daily_days)
    if days is None:
        raise _db_fail()
    if not days:
        raise Invalid("no_data", "일별 시세 저장분이 비어 있습니다.")
    plan = plan_period(days, as_of, since, fr)
    if plan.old_end:
        pick = await _db(pd.pick_week_close, norm_date(as_of) or "")
        plan = plan_period(days, as_of, since, fr, pick)
    if plan.error == "DB":
        raise _db_fail()
    if plan.error:
        raise Invalid("invalid", plan.error)
    ctx = Ctx(days=list(days), plan=plan, basis=bs, rebalance=rb_mode, weights=wt, end=plan.end)
    for note in plan.notes:
        ctx.warn(None, note)
    if plan.freq == "daily":
        ctx.base = plan.base or ""
        ctx.dates = [d for d in days if ctx.base <= d <= plan.end]
    need_sector = sc in ("contribution", "attribution")
    level = "major" if gb == "major" else "mid"
    idx_hit: pd.IndexSeries | None = None
    if idx_name is not None:
        pf, idx_hit, idx_series = await _build_index_portfolio(ctx, parsed.components[0].spec.strip(), idx_name)
        if bs == "total":
            bs = "price"
            ctx.warn("index_price_only", "공식 지수는 가격지수라 basis=total(배당 재투자)을 적용하지 않고 가격 수익률로 계산했다 — 총수익지수는 DB 에 없다.")
        main_mkt = KQ if idx_hit.src == "Q" else KS
        bname = (benchmark or "").strip()
        same = not bname
        if bname:
            r_same = pd.resolve_index(bname, idx_series)
            same = r_same.hit is not None and r_same.hit.sid == idx_hit.sid
        if same:                                       # 자기 비교 금지 — benchmark 를 비웠거나 같은 지수면 상대 지표를 만들지 않는다
            bench = None
            ctx.warn("benchmark_same_as_target", "대상이 공식 지수 자체라 benchmark 를 비웠거나 같은 지수를 주면 자기 자신과 비교하게 된다 — 초과수익·베타·알파 같은 상대 지표(data.relative)는 내지 않았다. "
                                                 "다른 공식 지수(예: benchmark=\"코스닥\")나 계산 벤치마크(예: benchmark=\"코스닥 전체\")를 주세요.")
        else:
            bench = await _pick_benchmark(ctx, pf, bname, sc, level, main_mkt)
        dates = pf.dates
    else:
        pf = await _build_portfolio(ctx, parsed.components, weights_mode=wt, rebalance=rb_mode, need_sector=need_sector, with_dividend=True)
        dates = pf.dates
        # 시장 비중으로 기본 벤치마크를 고른다
        share = {KS: 0.0, KQ: 0.0}
        for p in pf.positions:
            if p.market in share:
                share[p.market] += p.target0
        main_mkt = KQ if share[KQ] > share[KS] else KS
        bench = await _pick_benchmark(ctx, pf, (benchmark or "").strip(), sc, level, main_mkt)
    # 무위험 금리
    rate_obs = None if (rf_arg or "").strip() else await _db(pd.read_rf, dates[0], dates[-1])
    rf_acc, rf_info = _rf_for(ctx, rf_arg, rate_obs, dates)
    return _assemble(subject, ctx, pf, bench, rf_acc, rf_info, sc, gb, level, parsed, wt, rb_mode, bs, main_mkt, idx_hit)


def _assemble(subject: str, ctx: Ctx, pf: Portfolio, bench: Bench | None, rf_acc: pm.RfAccrual | None, rf_info: dict[str, Any], sc: str, gb: str,
              level: str, parsed: Parsed, wt: str, rb_mode: str, bs: str, main_mkt: str, idx: pd.IndexSeries | None = None) -> dict[str, Any]:
    plan = ctx.plan
    dates = pf.dates
    rp = pf.sim.rp
    n = len(rp)
    days = pm.calendar_days(dates[0], dates[-1])
    a = pm.obs_per_year(n, days)
    metrics = _risk_metrics(rp, dates, rf_acc)
    relative = _relative(rp, bench, dates, rf_acc) if bench is not None else None
    label = " + ".join(c.label for c in pf.comps) if parsed.blend else pf.comp_info[0].get("label_resolved") or pf.comps[0].label
    asof_all = [i.get("membership_asof") for i in pf.comp_info if i.get("membership_asof")]
    lookahead = [i for i in pf.comp_info if i.get("kind") == "universe" and (i.get("membership_asof") or "") > dates[0]]
    extras: dict[str, Any] = {}
    if sc == "contribution":
        extras["contributions"] = _contributions(pf, gb)
        extras["contributions_by"] = {"stock": "종목", "component": "구성요소", "mid": "중분류", "major": "대분류"}[gb]
    if sc == "attribution" and bench is not None:
        extras["attribution"] = _attribution(pf, bench, level)
    if sc == "series":
        extras["series"] = _series_rows(pf, bench)
    # ── 경고 코드
    used_sector = any(i.get("kind") == "sector" for i in pf.comp_info) or sc in ("contribution", "attribution")
    if used_sector and dates[0] < TAXONOMY_BREAK_DD:
        ctx.warn("sector_taxonomy_break", "2019-06-28 이전 날짜에 업종 분류를 썼다 — WICS 가 2018-06~2019-06 에 체계를 개편해(미디어와엔터테인먼트 신설) 그 앞뒤 업종은 같은 이름이어도 구성이 다르다.")
    retro = bool(pf.sector_map and pf.sector_map.retro) or any(i.get("classification_retro") for i in pf.comp_info) or bool(bench and bench.retro and sc == "attribution")
    if retro:
        ctx.warn("sector_retroactive", "업종 분류는 가장 이른 스냅샷 이후만 관측이라, 그 앞 날짜에는 첫 스냅샷을 **소급** 적용했다(DATA-SEC2) — 그때 그 업종이 아니었던 회사가 섞일 수 있다.")
    sector_effective = any(i.get("kind") == "sector" for i in pf.comp_info) or sc == "attribution" or (sc == "contribution" and gb in ("mid", "major"))
    if sector_effective:
        ctx.warn("survivorship", "업종 원천이 지수 구성종목이라 탈락·상장폐지 종목이 빠질 수 있다(생존 편향) — 대상 구성은 시작일 시점으로 고정했다.")
    if lookahead:
        ctx.warn("membership_lookahead", "시총 순위·지수 구성 문구는 이 서버가 보관한 최신 거래일의 구성이다 — 시작일에 아직 없던 종목은 시세가 없어 빠졌지만, 그 뒤 탈락한 종목은 "
                                         "과거에 들어 있지 못했을 수 있다(구성 이력 없음). 시작일 시점의 구성이 필요하면 종목 나열·업종 문구를 쓰세요.")
    if pf.delisted:
        ctx.warn("delisted_cash", f"기간 중 마지막 가격 관측이 끝 기준일 앞인 종목 {len(pf.delisted)}개(상장폐지 등)는 그 뒤 0% 현금으로 두었다 — 청산·합병 대가는 원천이 없다.")
    if bs == "total":
        ctx.warn("gross_total_return", "총수익은 세전(배당소득세 15.4% 를 빼지 않음)이라 KRX NTR(세후 총수익 지수)과 직접 비교하지 않는다.")
        if bench is not None:
            ctx.warn("benchmark_price_only", "벤치마크는 가격 수익률이다(공식 지수는 가격지수, 계산 벤치마크도 배당 미포함) — 대상만 배당을 포함하므로 초과수익이 배당만큼 커 보인다.")
    if bench is not None and any(b is None for b in bench.rb):
        miss = sum(1 for b in bench.rb if b is None)
        ctx.warn("benchmark_truncated", f"벤치마크가 {n}개 구간 중 {n - miss}개에만 있어(공식 지수·금리는 최근 3년, 계산 벤치마크는 시세 범위) 겹치는 구간만으로 상대 지표를 냈다.")
    if pf.dropped:
        ctx.warn(None, f"구성 종목 {len(pf.dropped)}개는 시작일 시세가 없어 뺐다(나중 상장·거래 정지) — 구성은 시작일 시점으로 고정한다.")
    if pf.flags:
        ctx.warn(None, f"분할·합병 이력 등 해석 주의 딱지가 붙은 종목 {len(pf.flags)}개가 있다(`coverage.excluded_flags`) — 제외하지 않고 알린다.")
    if pf.cmp_missing:
        ctx.warn(None, f"공표 대비(cmp)가 없는 옛 행 {pf.cmp_missing}개는 전일 종가 × 사건 계수로 보충했다(DATA-PX5 남은 한계).")
    if pf.unusable:
        ctx.warn(None, f"가격을 읽을 수 없어 수익률 0 으로 둔 종목-구간 {pf.unusable}개가 있다.")
    warnings = list(ctx.warnings)
    # ── 응답
    comp_out = []
    for info, c in zip(pf.comp_info, pf.comps):
        comp_out.append({k: info.get(k) for k in ("label", "spec", "weight_pct", "n_members", "sector_asof", "classification_retro", "membership_asof")})
    membership_top = max(asof_all) if asof_all else dates[0]
    data: dict[str, Any] = {
        "scope": sc,
        "period": {"start": dates[0], "end": dates[-1], "requested_start": plan.requested_since, "requested_end": plan.requested_end,
                   "freq": pf.freq, "n_obs": n, "calendar_days": days, "annualization": _num(a, 3)},
        "target": {"label": label, "spec": (parsed.components[0].spec if not parsed.blend else (target_spec(parsed))), "n_members": len({p.ticker for p in pf.positions}),
                   "membership_asof": membership_top,
                   "weights_basis": ("해당 없음(공식 지수)" if idx is not None else
                                     {"cap": "시총(시작일)", "equal": "동일"}[wt] if not any(c.basket for c in pf.comps) else "바스켓 비중"),
                   "rebalance": rb_mode, "components": comp_out},
        "metrics": metrics,
        "relative": relative,
        "coverage": {
            "price_adjusted": True, "adj_events_used": pf.events_used, "tick_rounding_skipped": pf.tick_skipped, "cmp_missing_rows": pf.cmp_missing + (bench.cmp_missing if bench else 0),
            "excluded_flags": pf.flags, "delisted_in_period": pf.delisted, "not_priced_at_start": pf.dropped,
            "cold": {"used": bool(ctx.cold.get("used")), "seam": ctx.cold.get("seam"),
                     "reason": (ctx.cold.get("reason") or "degraded") if ctx.cold.get("needed") and not ctx.cold.get("used") else None},
            "dividend": pf.dividend if bs == "total" else {"basis": "price", "gross": True, "covered_weight_pct": None, "pending": [], "preferred_price_only": 0, "ex_date_basis": None},
        },
        "rf": {k: rf_info.get(k) for k in ("series", "source", "avg_pct", "asof", "convention", "missing")},
        "method": _method(ctx, pf, bench, bs, wt, rb_mode, a),
        "sources": ["KRX 일별·주간 시세(krx_px·krx_weekly, 보관본 krx_weekly_hist)", "KRX 기준가 재설정 사건 원장(krx_adj_events)", "KRX 공식 지수(krx_idx)",
                    "ECOS 국고채 1년(ecos_rate)", "업종분류 구간(wise_sector_iv)", "배당 결정공시 원장(div_payment)"],
        "warning_codes": list(ctx.codes),
    }
    if idx is not None and pf.levels is not None:
        data["target"]["index"] = {"sid": idx.sid, "src": idx.src, "name": idx.name, "kind": idx.kind, "price_index": True,
                                   "start_point": pf.levels.get(dates[0]), "end_point": pf.levels.get(dates[-1]),
                                   "resolution": {"freq": pf.freq, "n_obs": n, "ann_factor": _num(a, 3), "nominal_ann_factor": NOMINAL_ANN[pf.freq]}}
    data.update(extras)
    return {"tool": TOOL, "status": "ok", "subject": subject, "data": data, "warnings": warnings, "warning_codes": list(ctx.codes)}


def target_spec(parsed: Parsed) -> str:
    return "; ".join(f"{c.spec}={c.weight * 100:g}" for c in parsed.components)


def _method(ctx: Ctx, pf: Portfolio, bench: Bench | None, bs: str, wt: str, rb_mode: str, a: float | None) -> str:
    is_index = bool(pf.comp_info) and pf.comp_info[0].get("kind") == "index"
    freq = "일별(KRX 공표 대비 cmp 기준 — 분할·권리락 반영)" if pf.freq == "daily" else "주간(주 마지막 거래일 한 점, 종가 ÷ (전 점 종가 × Π 기준가 사건 원값 base/prev) − 1; 호가단위 맞춤 사건은 제외)"
    if is_index:
        freq = ("일별(지수 일별 종가)" if pf.freq == "daily" else "주간(주 마지막 거래일 한 점의 지수 종가 — 종목 시세와 같은 주 정의, 닫히지 않은 이번 주는 일별 행으로 끝점)")
    parts = [f"해상도 {freq}.",
             "대상 = 공식 지수 종가(가격지수, 외국주 제외) 수익률 — 구성 종목을 읽지 않고 일별·주간을 한 계산 안에 섞지 않는다." if is_index else
             f"구간 수익률 → 비중 표류 포트폴리오(구성요소 안 {'시작일 시총' if wt == 'cap' else '동일'} 가중, 리밸런스 {rb_mode}), 상장폐지는 마지막 값에 0% 현금.",
             f"누적 = Π(1+r)−1, 연율 = (1+R)^(365.25/일수)−1(365일 미만이면 빈 값), 연간 관측 수 A = n×365.25/일수{'' if a is None else f' = {a:.2f}'}.",
             "변동성 = 표본 표준편차(ddof=1)×√A, 하방 변동성 기준선 = 무위험 금리, MDD = 부(wealth) 곡선 기준.",
             "무위험 금리 = 연율 % × 구간 달력일/365(ACT/365 단리), 구간 시작 이전의 마지막 관측값(미래 정보 금지).",
             "샤프 = mean(r−rf)/sd(r−rf)×√A, 베타 = 초과수익끼리 OLS, 알파 = (mean ex_p − β·mean ex_b)×A, 추적오차 = sd(r_p−r_b)×√A, 정보비율 = mean(r_p−r_b)×A/추적오차.",
             "기여 = 비중×수익률을 Carino 로 이어 합이 누적 수익률과 같다. 업종 분해 = Brinson-Fachler(배분·선택·상호작용)를 초과 Carino 로 연결."]
    if bs == "total":
        parts.append("총수익 = 배당락일 구간에 DPS/조정 기준가를 더한 세전 값(보통주 DPS, 정정은 가장 늦은 접수만).")
    if bench is not None:
        parts.append("벤치마크: " + ("공식 지수 종가 수익률." if bench.source == "official" else "계산 벤치마크(전일 기준가 시총 가중, 우선주 포함)."))
    parts.append("종목 이름표는 DART 회사 원장 캐시를 쓰며 캐시가 비어 있으면 corpCode 받기 1콜이 날 수 있다(그 밖의 외부 호출 0).")
    return " ".join(parts)
