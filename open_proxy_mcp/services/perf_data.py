"""성과 분석 읽기 계층 — `performance_analysis` 가 쓰는 DB·콜드 서빙본 읽기 한 곳 (261022).

부를 때 외부 호출(DART·KRX·ECOS)은 0 이다 — 서버 DB(`opm_app` 이 읽는 표)와 콜드 서빙본(`cold_store`)만 읽는다. 쓰기 0, 새 표·뷰·칸·색인 0.

■ 계약 — 읽기 함수는 **동기**(`db.pg_rows` 를 부른다 — 호출부가 스레드로 보낸다)이고 `None` = DB 미설정·장애(권한 오류 포함 — `pg_rows` 가
  `ProgrammingError` 도 None 으로 돌려준다), `[]`/빈 사전 = 정상인데 데이터 없음이다. 호출부(`performance_analysis`)는 시세·달력 읽기의 None 만
  `db_error` 로 올리고, 무위험 금리·배당·딱지·업종 구간·지수·콜드 읽기의 None 은 그 지표만 비우고 경고를 단다(DATA 함정: 권한 오류가 「DB 장애」로 보이는 것).
■ 읽는 곳과 왕복 수(목표 8~12회)
  달력 1(`read_daily_days`) · 종목 번호 1 + 일별 시세 1(`read_tids`·`read_daily_px`) 또는 주간 시세 1(+ 콜드 종목별 읽기) · 수정 사건 1 · 딱지 1 ·
  업종 구간 2(스냅샷 날짜·구간) · 지수 사전 1 + 지수 종가 1 · 금리 1 · 배당 원장 1 + 범위 1 · 계산 벤치마크 1(일별은 DB 집계 한 질의, 주간은 시장 전 종목 행).
■ 일별 시세는 종목 번호 목록을 먼저 뽑고 `krx_px` 에서 `tid = ANY(...)` 로 읽는다 — 날짜 달력·번호표를 CTE 로 조인하면 2.6 s 가 걸렸다.
  계산 벤치마크(전 시장) 일별은 DB 쪽 GROUP BY 한 질의다: Σcmp·주식수 / Σ(close−cmp)·주식수 를 (날짜, 업종)별로 받아 약 1.4만 행만 온다.
■ 업종은 시점 기준이다 — 기간 j 의 업종 = 그 기간 **시작일**을 덮는 구간(`wise_sector_iv.valid_from <= 날 < valid_to`). 가장 이른 스냅샷보다 앞선 날은 그 첫
  스냅샷을 소급한다(DATA-SEC2, 시장 집계 `krx_cap_agg` 와 같은 규칙). 소급 여부는 `SectorMap.retro` 로 알린다.
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Iterable, Sequence

from open_proxy_mcp import db
from open_proxy_mcp.company_resolver import issuer_ticker
from open_proxy_mcp.krx_retention import WEEKLY_DAYS_SQL, krx_weekly_cutoff
from open_proxy_mcp.services.perf_math import AdjEvent, fmt_dd, parse_dd

#: 읽는 칸 순서를 이름으로 못 박는다(CLAUDE.md 규칙 5 — 튜플 위치 언패킹은 이 모듈 안에서만, 질의 바로 곁에서).
_BATCH = 800          # ANY(...) 에 넣는 종목 수 상한(질의 한 번)


def _chunks(items: Sequence[str], n: int = _BATCH) -> Iterable[list[str]]:
    for i in range(0, len(items), n):
        yield list(items[i:i + n])


def _d(dd: str) -> date:
    return parse_dd(dd)


def shift_dd(dd: str, days: int) -> str:
    return fmt_dd(_d(dd) + timedelta(days=days))


# ── 달력 ──────────────────────────────────────────────────────────────────────

#: 일별 보관 창의 거래일 전부(수백 행) — 일별 달력·창 경계·배당락일 정확 달력이 한 질의로 나온다.
DAILY_DAYS_SQL = "SELECT dd FROM krx_day WHERE n > 0 ORDER BY dd"


def read_daily_days() -> list[str] | None:
    rows = db.pg_rows(DAILY_DAYS_SQL)
    return None if rows is None else [r[0] for r in rows]


_WEEK_PICK_SQL = "SELECT max(dd) FROM (" + WEEKLY_DAYS_SQL + ") c WHERE dd <= %s"
_WEEK_PICK_TABLE_SQL = "SELECT max(price_dd) FROM krx_weekly WHERE price_dd <= %s"


def pick_week_close(req: str) -> str | None | bool:
    """req 이하 마지막 주 닫는 날(YYYYMMDD). 못 읽으면 False, 없으면 None."""
    rows = db.pg_rows(_WEEK_PICK_SQL, (req,))
    if rows is None:                                      # 달력 표가 없는 DB(교체 전·표 모드)는 오류 → 뷰/표를 직접 읽는다
        rows = db.pg_rows(_WEEK_PICK_TABLE_SQL, (req,))
    if rows is None:
        return False
    return rows[0][0] if rows and rows[0][0] else None


_WEEK_DAYS_RANGE_SQL = "SELECT dd FROM (" + WEEKLY_DAYS_SQL + ") c WHERE dd >= %s AND dd <= %s ORDER BY dd"
_WEEK_DAYS_TABLE_SQL = "SELECT DISTINCT price_dd FROM krx_weekly WHERE price_dd >= %s AND price_dd <= %s ORDER BY price_dd"


def read_week_days(lo: str, hi: str) -> list[str] | None:
    """[lo, hi] 안의 주 닫는 날 목록(YYYYMMDD) — 주간 시세 뷰 `krx_weekly` 와 같은 주 정의(DATA-PX6)의 달력이다.
    지수 target 이 종목 시세 없이도 같은 주 격자를 쓰게 한다(지수 일별 행 중 주 닫는 날만 고른다). 못 읽으면 None, 달력 표가 없는 DB(표 모드)는 뷰/표를 직접 읽는다."""
    rows = db.pg_rows(_WEEK_DAYS_RANGE_SQL, (lo, hi))
    if rows is None:
        rows = db.pg_rows(_WEEK_DAYS_TABLE_SQL, (lo, hi))
    return None if rows is None else [r[0] for r in rows]


# ── 시세 ──────────────────────────────────────────────────────────────────────

def read_tids(tickers: Sequence[str]) -> dict[str, int] | None:
    """단축코드 → 종목 번호(`krx_ticker`). 번호표에 없는 코드는 빠진다."""
    out: dict[str, int] = {}
    for part in _chunks(list(tickers)):
        rows = db.pg_rows("SELECT ticker, tid FROM krx_ticker WHERE ticker = ANY(%s)", (part,))
        if rows is None:
            return None
        out.update({t: tid for t, tid in rows})
    return out


_DAILY_PX_SQL = ("SELECT p.tid, to_char(p.trd_dd, 'YYYYMMDD'), p.close, p.cmp, p.list_shrs FROM krx_px p "
                 "WHERE p.tid = ANY(%s) AND p.trd_dd >= %s AND p.trd_dd <= %s ORDER BY p.tid, p.trd_dd")


def read_daily_px(tids: dict[str, int], lo: str, hi: str) -> dict[str, list[tuple[str, int | None, int | None, int | None]]] | None:
    """종목별 [(날짜, 종가, 공표 대비 cmp, 상장주식수)] — 날짜 오름차순, [lo, hi] 닫힌 구간. cmp 는 옛 이관 행에서 NULL 일 수 있다."""
    by_tid = {tid: t for t, tid in tids.items()}
    out: dict[str, list] = {t: [] for t in tids}
    ids = sorted(by_tid)
    for i in range(0, len(ids), _BATCH):
        rows = db.pg_rows(_DAILY_PX_SQL, (ids[i:i + _BATCH], _d(lo), _d(hi)))
        if rows is None:
            return None
        for tid, dd, close, cmp, shrs in rows:
            out[by_tid[tid]].append((dd, close, cmp, shrs))
    return out


_WEEKLY_PX_SQL = ("SELECT ticker, price_dd, close, list_shrs FROM krx_weekly WHERE ticker = ANY(%s) "
                  "AND price_dd >= %s AND price_dd <= %s ORDER BY ticker, price_dd")


def read_weekly_px(tickers: Sequence[str], lo: str, hi: str) -> dict[str, list[tuple[str, int | None, int | None]]] | None:
    """DB 의 주간 시세(`krx_weekly` 뷰) — 종목별 [(주 닫는 날, 종가, 상장주식수)]. 콜드 이음은 `read_weekly`."""
    out: dict[str, list] = {t: [] for t in tickers}
    for part in _chunks(list(tickers)):
        rows = db.pg_rows(_WEEKLY_PX_SQL, (part, lo, hi))
        if rows is None:
            return None
        for t, dd, close, shrs in rows:
            out[t].append((dd, close, shrs))
    return out


_WEEKLY_MARKET_SQL = ("SELECT ticker, price_dd, close, list_shrs FROM krx_weekly WHERE market = ANY(%s) "
                      "AND price_dd >= %s AND price_dd <= %s ORDER BY ticker, price_dd")


_TAIL_MARKET_SQL = "SELECT ticker, trd_dd, close, list_shrs FROM krx_daily WHERE trd_dd = %s AND market = ANY(%s)"


def read_weekly_market_px(markets: Sequence[str], lo: str, hi: str, tail_dd: str | None = None
                          ) -> dict[str, list[tuple[str, int | None, int | None]]] | None:
    """시장(KS/KQ) 전 종목의 주간 시세 — 주간 계산 벤치마크용. 행 수 상한은 호출부가 먼저 어림해 지킨다.
    `tail_dd` 가 주 닫는 날이 아니면(이번 주 등) 일별 저장분의 그 날 행을 끝점으로 잇는다 — 종목 시세와 같은 격자여야 한다."""
    rows = db.pg_rows(_WEEKLY_MARKET_SQL, (list(markets), lo, hi))
    if rows is None:
        return None
    out: dict[str, list] = {}
    seen: set[tuple[str, str]] = set()
    for t, dd, close, shrs in rows:
        out.setdefault(t, []).append((dd, close, shrs))
        seen.add((t, dd))
    if tail_dd and tail_dd > lo:
        tail = db.pg_rows(_TAIL_MARKET_SQL, (tail_dd, list(markets)))
        if tail is None:
            return None
        for t, dd, close, shrs in tail:
            if (t, dd) not in seen:
                out.setdefault(t, []).append((dd, close, shrs))
    return out


@dataclass
class WeeklyRead:
    rows: dict[str, list[tuple[str, int | None, int | None]]] = field(default_factory=dict)
    db_ok: bool = True
    cold_used: bool = False
    seam: str | None = None          # 콜드는 날짜 < seam, DB 는 >= seam
    reason: str | None = None        # 콜드가 필요했는데 못 읽은 까닭(강등) — 이 값이 있으면 하한 이전은 비어 있다
    n_cold_rows: int = 0
    floor: str | None = None         # DB 주간 구간 시작 — 강등이면 곡선은 여기서 시작한다


async def read_weekly(tickers: Sequence[str], lo: str, hi: str, *, latest: str, floor: str) -> WeeklyRead:
    """주간 시세 읽기 — DB(주간 3년) + 하한 이전은 콜드 서빙본(`krx_weekly_hist`)에서 잇는다. 이음매 규칙은 배수 도구와 한 곳(`serving_seam`·`_cold_part`)이다.

    · 콜드 꺼짐·필요 없음(lo ≥ floor): DB 만. 종전 응답과 같은 질의다.
    · 필요하면 종목마다 [lo, S) 를 서빙본에서 읽어 앞에 붙이고 DB 행은 S 이상만 남긴다(겹침 0).
    · 한 종목이라도 서빙본을 못 읽으면(꺼짐·검증 실패·틈·읽기 실패) 부분 곡선을 내지 않고 전체를 DB 하한(floor)에서 시작하는 곡선으로 강등한다(DATA-COLD2)."""
    from open_proxy_mcp import cold_store as _cold
    from open_proxy_mcp.services import price_multiple_data as pmd

    res = WeeklyRead(floor=floor)
    cold_rows: dict[str, list] = {}
    db_lo = max(lo, floor)
    if lo < floor:
        if not _cold.enabled():
            res.reason = "off"
        else:
            parts = await asyncio.gather(*[pmd._cold_part_or_degrade(t, lo, latest, floor, ("price_dd", "close", "list_shrs"))
                                           for t in tickers])
            spans = [p for p in parts if p is not None]
            if not spans:
                res.reason = "seam_after_start"           # 이음매 S 가 lo 이전인데 DB 주간 구간은 lo 뒤에 시작한다 — 서빙본에 읽을 구간이 없다
            elif any((not s.used) or r is None for s, r in spans):
                bad = next(s for s, r in spans if (not s.used) or r is None)
                res.reason = bad.reason or "degraded"
            else:
                seam = spans[0][0].seam
                res.cold_used, res.seam = True, seam
                db_lo = seam or db_lo
                for t, p in zip(tickers, parts):
                    if p is not None and p[1]:
                        cold_rows[t] = [(r[0], r[1], r[2]) for r in p[1]]
                        res.n_cold_rows += len(p[1])
    rows = await asyncio.to_thread(read_weekly_px, list(tickers), db_lo, hi)
    if rows is None:
        res.db_ok = False
        return res
    if res.cold_used:
        for t, cr in cold_rows.items():
            rows[t] = cr + [r for r in rows.get(t, []) if r[0] >= (res.seam or "")]
    res.rows = rows
    return res


_MARKETS_SQL = ("SELECT k.ticker, CASE p.market WHEN 'S' THEN 'KS' WHEN 'Q' THEN 'KQ' END FROM krx_px p JOIN krx_ticker k ON k.tid = p.tid "
                "WHERE p.tid = ANY(%s) AND p.trd_dd = %s")


def read_markets(tids: dict[str, int], dd: str) -> dict[str, str] | None:
    """그 거래일 행의 시장(KS/KQ) — 기준일 시장 비중으로 기본 벤치마크를 고른다. 그 날 행이 없는 종목은 빠진다."""
    out: dict[str, str] = {}
    ids = sorted(tids.values())
    by_tid = {v: k for k, v in tids.items()}
    for i in range(0, len(ids), _BATCH):
        rows = db.pg_rows(_MARKETS_SQL, (ids[i:i + _BATCH], _d(dd)))
        if rows is None:
            return None
        out.update({t: m for t, m in rows})
    return out


def read_market_counts(dd: str) -> dict[str, int] | None:
    """그 거래일의 시장별 종목 수(KS/KQ) — 주간 계산 벤치마크의 행 수를 읽기 전에 어림한다."""
    rows = db.pg_rows("SELECT CASE p.market WHEN 'S' THEN 'KS' WHEN 'Q' THEN 'KQ' END, count(*) FROM krx_px p WHERE p.trd_dd = %s GROUP BY 1",
                      (_d(dd),))
    return None if rows is None else {m: int(n) for m, n in rows}


# ── 수정 사건·딱지 ─────────────────────────────────────────────────────────────

_EVENTS_SQL = ("SELECT ticker, event_dd, prev_close, base_price, market, label_confidence FROM krx_adj_events "
               "WHERE ticker = ANY(%s) AND event_dd > %s AND event_dd <= %s ORDER BY ticker, event_dd")


def read_adj_events(tickers: Sequence[str], lo: str, hi: str) -> dict[str, list[AdjEvent]] | None:
    """기준가 재설정 사건 원값(수정계수 원장) — (lo, hi] 안. 반올림된 `adj_factor` 는 읽지 않는다."""
    out: dict[str, list[AdjEvent]] = {}
    for part in _chunks(list(tickers)):
        rows = db.pg_rows(_EVENTS_SQL, (part, lo, hi))
        if rows is None:
            return None
        for t, dd, pc, bp, mkt, label in rows:
            out.setdefault(t, []).append(AdjEvent(dd, pc, bp, mkt, label))
    return out


def read_stock_flags(tickers: Sequence[str]) -> dict[str, str] | None:
    """해석 주의 딱지(spinoff_break·unresolved_adjustment) — 종목당 하나."""
    out: dict[str, str] = {}
    for part in _chunks(list(tickers)):
        rows = db.pg_rows("SELECT ticker, flag FROM krx_stock_flags WHERE ticker = ANY(%s)", (part,))
        if rows is None:
            return None
        out.update({t: f for t, f in rows})
    return out


# ── 업종 구간 ──────────────────────────────────────────────────────────────────

def read_sector_snaps() -> list[str] | None:
    """업종분류 스냅샷 날짜 목록(YYYYMMDD, 작은 표 `wise_sector_snap`). 뷰 `wise_sector` 의 max(snap_dd) 는 스냅샷 × 구간을 펼치므로 읽지 않는다."""
    rows = db.pg_rows("SELECT dd FROM wise_sector_snap ORDER BY dd")
    return None if rows is None else [r[0] for r in rows]


_IV_SQL = ("SELECT k.ticker, to_char(i.valid_from, 'YYYYMMDD'), to_char(i.valid_to, 'YYYYMMDD'), i.sector_code, i.sector, "
           "i.industry_code, i.industry, i.src::text FROM wise_sector_iv i JOIN krx_ticker k ON k.tid = i.tid "
           "WHERE k.ticker = ANY(%s) ORDER BY k.ticker, i.valid_from")

#: 우선주 판정 중 분류가 없는 행에 쓰는 코드 끝자리 규칙의 SQL 판(261023) — `company_resolver.issuer_ticker`(종류주 코드를 보통주 코드로 잇는 함수)와 같은 규칙:
#: KRX 단축코드 끝자리 ≠ 0 이 종류주다(005935·00680K). 회사 원장도, 「앞 5자 + 0 이 번호표에 있다」는 조건도 보지 않는다 — 번호표(`krx_ticker`)의 코드 모양만 본다(DART 0콜).
CODE_PREF_CTE = "code_pref AS (SELECT k.tid FROM krx_ticker k WHERE substr(k.ticker, 6, 1) <> '0')"


@dataclass(frozen=True)
class SectorKey:
    major_code: str
    major: str
    mid_code: str
    mid: str


UNCLASSIFIED = "_UNCLASSIFIED"


class SectorMap:
    """종목 → 날짜별 업종. 규칙: 기준 날짜 ref = max(날짜, 가장 이른 스냅샷) 를 덮는 구간(valid_from ≤ ref < valid_to, valid_to 비면 열린 구간).
    ref 가 가장 이른 스냅샷으로 끌려 올라온 날짜가 하나라도 있었으면 `retro`. 구간이 없는 종목(스냅샷 뒤 신규·공급처가 분류를 안 준 종목)은 None = 미분류.

    우선주 판정(`is_pref`, 261023 — 업종 구성 `sector_universe`·일별 집계 SQL 과 같은 규칙): 그 날을 덮는 구간이 있으면 그 출처가 'P'(우선주가 보통주 분류를 이음, `pref_iv`)일 때만,
    덮는 구간이 없으면 코드 끝자리 규칙(끝자리 ≠ 0 — 이미 읽은 종목 코드만 본다, 추가 질의 0)으로 우선주다. 업종 읽기(`at`)는 그대로다."""

    def __init__(self, snaps: Sequence[str], ivs: dict[str, list[tuple[str, str | None, SectorKey]]],
                 pref_iv: dict[str, list[tuple[str, str | None]]] | None = None):
        self.snaps = list(snaps)
        self.first = self.snaps[0] if self.snaps else None
        self._iv = ivs
        self._pref_iv = pref_iv or {}
        self.retro = False

    def _ref(self, dd: str) -> str:
        return self.first if (self.first and dd < self.first) else dd

    def at(self, ticker: str, dd: str) -> SectorKey | None:
        ref = dd
        if self.first and dd < self.first:
            ref = self.first
            self.retro = True
        for vf, vt, key in self._iv.get(ticker, ()):
            if vf <= ref and (vt is None or ref < vt):
                return key
        return None

    def is_pref(self, ticker: str, dd: str) -> bool:
        """그 날 이 종목이 우선주인가 — `retro` 는 건드리지 않는다(업종 읽기가 따로 센다)."""
        ref = self._ref(dd)
        if any(vf <= ref and (vt is None or ref < vt) for vf, vt, _k in self._iv.get(ticker, ())):
            return any(vf <= ref and (vt is None or ref < vt) for vf, vt in self._pref_iv.get(ticker, ()))   # 분류가 있으면 출처만
        return bool(issuer_ticker(ticker))                                                                  # 분류가 없으면 코드 끝자리 규칙


def read_sector_map(tickers: Sequence[str]) -> SectorMap | None:
    snaps = read_sector_snaps()
    if snaps is None:
        return None
    ivs: dict[str, list] = {}
    pref_iv: dict[str, list] = {}
    for part in _chunks(list(tickers)):
        rows = db.pg_rows(_IV_SQL, (part,))
        if rows is None:
            return None
        for t, vf, vt, sc, sn, ic, inn, src in rows:
            ivs.setdefault(t, []).append((vf, vt, SectorKey(sc, sn, ic, inn)))
            if src == "P":
                pref_iv.setdefault(t, []).append((vf, vt))
    return SectorMap(snaps, ivs, pref_iv)


# ── 계산 벤치마크(일별, DB 집계) ───────────────────────────────────────────────

#: (날짜, 업종) 한 묶음 = 그날 모든 종목의 Σcmp·주식수(분자) 와 Σ(close−cmp)·주식수(분모 = 전일 기준가 시총). 업종은 **전일**을 덮는 구간(없으면 첫 스냅샷 소급).
#: cmp 가 NULL 인 옛 행은 합에서 빠지고 `n_null` 로 센다. **우선주는 뺀다**(261023) — 우선주는 성과 분석 대상이 아니라 업종 구성(`sector_universe`)과 같은 모집단이어야 한다:
#: 그 전일을 덮는 업종 구간이 있으면 그 출처가 'P'(보통주 분류를 이은 행)일 때만, 덮는 구간이 없으면 번호표의 코드 끝자리 ≠ 0(`code_pref`)일 때 합에 넣지 않는다. 우선주를 넣던 종전 값은 시총 집계
#: `krx_cap_agg`(`scope="sector"`, 우선주 포함 합)와 같은 정의였으므로, 이제 이 합은 그 집계와 우선주 몫만큼 다르다.
BENCH_DAILY_SQL = """\
WITH d AS (
  SELECT trd_dd, dd, lag(trd_dd) OVER (ORDER BY trd_dd) AS prev_dd
  FROM krx_day WHERE n > 0 AND dd >= %s AND dd <= %s
), f AS (SELECT min(snap) AS s0 FROM wise_sector_snap),
""" + CODE_PREF_CTE + """
SELECT d.dd, iv.sector_code, iv.sector, iv.industry_code, iv.industry,
       sum(p.cmp::bigint * p.list_shrs) FILTER (WHERE p.cmp IS NOT NULL AND p.list_shrs > 0 AND p.close - p.cmp > 0) AS num,
       sum((p.close - p.cmp)::bigint * p.list_shrs) FILTER (WHERE p.cmp IS NOT NULL AND p.list_shrs > 0 AND p.close - p.cmp > 0) AS den,
       count(*) AS n, count(*) FILTER (WHERE p.cmp IS NULL) AS n_null
FROM d CROSS JOIN f
JOIN krx_px p ON p.trd_dd = d.trd_dd
LEFT JOIN wise_sector_iv iv ON iv.tid = p.tid AND iv.valid_from <= GREATEST(d.prev_dd, f.s0)
                           AND (iv.valid_to IS NULL OR GREATEST(d.prev_dd, f.s0) < iv.valid_to)
WHERE d.prev_dd IS NOT NULL AND (CASE p.market WHEN 'S' THEN 'KS' WHEN 'Q' THEN 'KQ' END) = ANY(%s)
  AND NOT (COALESCE(iv.src = 'P', false) OR (iv.tid IS NULL AND p.tid IN (SELECT tid FROM code_pref)))
GROUP BY d.dd, iv.sector_code, iv.sector, iv.industry_code, iv.industry
ORDER BY d.dd
"""


@dataclass(frozen=True)
class BenchGroup:
    dd: str
    key: SectorKey | None
    num: float          # Σ cmp × 주식수
    den: float          # Σ 전일 기준가 × 주식수
    n: int
    n_null: int


def read_bench_daily(markets: Sequence[str], lo: str, hi: str) -> list[BenchGroup] | None:
    """[lo(기준일), hi] 일별 계산 벤치마크 묶음. lo 날은 전일 계산용이라 결과에 없다."""
    rows = db.pg_rows(BENCH_DAILY_SQL, (lo, hi, list(markets)))
    if rows is None:
        return None
    out: list[BenchGroup] = []
    for dd, sc, sn, ic, inn, num, den, n, n_null in rows:
        key = SectorKey(sc, sn, ic, inn) if sc else None
        out.append(BenchGroup(dd, key, float(num or 0), float(den or 0), int(n), int(n_null)))
    return out


# ── 공식 지수 ──────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class IndexSeries:
    sid: int
    src: str            # P 코스피 · Q 코스닥 · X KRX
    name: str
    kind: str           # z 규모·시장 대표 · s 업종·섹터 · t 그 밖
    in_db: bool
    first_dd: str | None = None
    last_dd: str | None = None


SRC_LABEL = {"P": "코스피", "Q": "코스닥", "X": "KRX"}


def read_index_series() -> list[IndexSeries] | None:
    rows = db.pg_rows("SELECT sid, src::text, name, kind::text, in_db, to_char(first_dd, 'YYYYMMDD'), to_char(last_dd, 'YYYYMMDD') "
                      "FROM krx_idx_series ORDER BY sid")
    if rows is None:
        return None
    return [IndexSeries(*r) for r in rows]


def read_index_closes(sid: int, lo: str, hi: str) -> list[tuple[str, float]] | None:
    """계열 하나의 [(날짜, 종가)] — [lo, hi] 닫힌 구간. 종가 = close_x100 / 100."""
    rows = db.pg_rows("SELECT to_char(trd_dd, 'YYYYMMDD'), close_x100 FROM krx_idx WHERE sid = %s AND trd_dd >= %s AND trd_dd <= %s "
                      "ORDER BY trd_dd", (sid, _d(lo), _d(hi)))
    return None if rows is None else [(dd, v / 100.0) for dd, v in rows]


_NAME_ALIAS = (("kospi", "코스피"), ("kosdaq", "코스닥"))


def _norm_index(s: str) -> str:
    t = re.sub(r"[\s·ㆍ\-_/]+", "", (s or "").lower())
    for a, b in _NAME_ALIAS:
        t = t.replace(a, b)
    return t


@dataclass
class IndexResolve:
    hit: IndexSeries | None = None
    question: str = ""            # 되물을 문장(모호하거나 없음)
    note: str = ""                # 별칭·정규화로 풀었을 때의 한 줄
    ambiguous: bool = False       # 같은 이름이 여러 시장에 있어 하나로 정하지 않았다
    near: tuple[str, ...] = ()    # 못 찾았을 때 비슷한 이름(최대 8개)


def resolve_index(name: str, series: Sequence[IndexSeries], arg: str = "benchmark", prefix: str = "") -> IndexResolve:
    """공식 지수 이름 → 계열. 열쇠는 (엔드포인트, 이름)이라 같은 이름이 코스피·코스닥 양쪽에 있을 수 있다(20개 업종) — 그때는 **추측하지 않고 되묻는다**.
    「코스피200」·「코스피 200」 같은 띄어쓰기·영문 별칭은 정규화로 풀고, 「코스닥 전기전자」처럼 시장 이름이 앞에 붙은 말은 그 시장의 계열로 푼다."""
    want = _norm_index(name)
    if not want:
        return IndexResolve(question=f"{arg} 이름이 비어 있습니다.")
    exact = [s for s in series if _norm_index(s.name) == want]
    if len(exact) > 1:
        opts = " · ".join(f"{SRC_LABEL.get(s.src, s.src)} {s.name}" for s in exact)
        return IndexResolve(question=f"「{name}」은 여러 시장에 같은 이름의 공식 지수가 있어 하나로 정하지 않았습니다 — 골라 주세요: {opts}. "
                                     f"예: {arg}=\"{prefix}{SRC_LABEL.get(exact[0].src, exact[0].src)} {exact[0].name}\"", ambiguous=True)
    if len(exact) == 1:
        s = exact[0]
        return IndexResolve(hit=s, note="" if s.name == name.strip() else f"{arg} 「{name}」 → 공식 지수 「{s.name}」({SRC_LABEL.get(s.src, s.src)}) 로 풀었다.")
    for src, word in (("P", "코스피"), ("Q", "코스닥")):
        if want.startswith(word) and len(want) > len(word):
            rest = want[len(word):]
            hits = [s for s in series if s.src == src and _norm_index(s.name) == rest]
            if len(hits) == 1:
                s = hits[0]
                return IndexResolve(hit=s, note=f"{arg} 「{name}」 → {word} 공식 지수 「{s.name}」 로 풀었다.")
    near = sorted({s.name for s in series if want in _norm_index(s.name) or _norm_index(s.name) in want})[:8]
    more = f" 비슷한 이름: {', '.join(near)}." if near else ""
    return IndexResolve(question=f"공식 지수 「{name}」을 찾지 못했습니다.{more} 계산 벤치마크는 유니버스 문구(예: \"코스피 전체\", \"대분류 IT\")로 줄 수 있습니다.",
                        near=tuple(near))


# ── 무위험 금리 ────────────────────────────────────────────────────────────────

_RF_SQL = ("SELECT to_char(d, 'YYYYMMDD'), rate::numeric FROM ecos_rate WHERE series = '1' AND d <= %s AND "
           "d >= COALESCE((SELECT max(d) FROM ecos_rate WHERE series = '1' AND d <= %s), %s) ORDER BY d")


def read_rf(lo: str, hi: str) -> list[tuple[str, float]] | None:
    """국고채 1년 [(날짜, 연율 %)] — lo **이전** 마지막 관측부터 hi 까지(적립이 구간 시작 이전 값을 쓰므로)."""
    rows = db.pg_rows(_RF_SQL, (_d(hi), _d(lo), _d(lo)))
    return None if rows is None else [(dd, float(rate)) for dd, rate in rows]


# ── 배당 ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Dividend:
    ticker: str
    record_dd: str          # 배당기준일 YYYYMMDD
    board_dd: str | None    # 결의일 YYYYMMDD
    dps: float              # 보통주 1주당 배당금(원)
    rcept_no: str
    market: str | None = None
    fiscal_year: int | None = None


_DIV_SQL = ("SELECT stock_code, record_date, board_date, dps_common, rcept_no, market, fiscal_year FROM div_payment "
            "WHERE stock_code = ANY(%s) AND record_date >= %s AND record_date <= %s AND dps_common IS NOT NULL AND dps_common > 0 "
            "ORDER BY stock_code, record_date, rcept_no")


def _compact(d: str | None) -> str | None:
    return re.sub(r"\D", "", d) if d else None


def dedupe_dividends(rows: Iterable[Dividend]) -> list[Dividend]:
    """같은 (종목, 기준일)이 여러 행이면 가장 늦은 접수번호 하나(정정 공시 — DATA-DIV4). 취소(DPS 없음)는 읽는 질의가 이미 뺐다."""
    best: dict[tuple[str, str], Dividend] = {}
    for d in rows:
        k = (d.ticker, d.record_dd)
        if k not in best or d.rcept_no > best[k].rcept_no:
            best[k] = d
    return sorted(best.values(), key=lambda d: (d.ticker, d.record_dd))


def read_dividends(tickers: Sequence[str], rec_lo: str, rec_hi: str) -> list[Dividend] | None:
    """기준일이 [rec_lo, rec_hi] 인 현금배당 결정(보통주 DPS > 0) — 종목당 기준일 하나에 가장 늦은 접수만. 우선주는 DPS 칸이 없어 읽지 않는다."""
    lo, hi = f"{rec_lo[:4]}-{rec_lo[4:6]}-{rec_lo[6:]}", f"{rec_hi[:4]}-{rec_hi[4:6]}-{rec_hi[6:]}"
    rows: list[Dividend] = []
    for part in _chunks(list(tickers)):
        got = db.pg_rows(_DIV_SQL, (part, lo, hi))
        if got is None:
            return None
        for code, rec, board, dps, rcept, mkt, fy in got:
            rdd = _compact(rec)
            if rdd:
                rows.append(Dividend(code, rdd, _compact(board), float(dps), rcept or "", mkt, fy))
    return dedupe_dividends(rows)


@dataclass(frozen=True)
class ScopeRow:
    fiscal_year: int
    market: str
    is_complete: bool
    window_lo: int | None


def read_div_scope() -> list[ScopeRow] | None:
    rows = db.pg_rows("SELECT fiscal_year, market, is_complete, window_lo FROM div_payment_scope ORDER BY market, fiscal_year")
    return None if rows is None else [ScopeRow(*r) for r in rows]
