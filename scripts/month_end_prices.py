"""과거 월말 시세 원천 — 시장·업종 배수 백필(`market_val_history_backfill.py`·`sector_val_backfill.py`)이 함께 쓴다. DART·KRX 0콜.

261019 신설. 두 백필은 월말마다 「그 달 마지막 주간 시세일」의 시총으로 배수를 낸다. 종전엔 그 날짜를 DB `krx_weekly` 에서
`DISTINCT ON (월) max(price_dd)` 로 골랐다. 그런데 `krx_weekly` 는 보존 하한 이전을 **종목별 연말 행만** 남긴다(`krx_retention`).
그래서 하한 이전 달은 그 달에 상장폐지한 종목의 마지막 행 날짜가 「월말」로 뽑힌다(10-19 운영: 2020-01~2023-09 의 41개 달 중 37개가
1~8종목 날짜 — 20200131 대신 20200110 등. 업종분류 월간 일정이 `--since 20200101` 로 돈다). 지금은 그 종목들이 분류·재무 표에 없어
그 날짜의 집계가 0행이라 저장된 행을 덮지는 않는다(같은 날 대조). 분류·재무가 남은 종목이 그런 날짜에 걸리면 몇 종목짜리 집계가 월말을
덮거나 엉뚱한 날짜에 새 행을 만든다 — 그리고 하한 이전 달을 다시 낼 길(콜드 서빙본)이 없었다.

규칙 — 달마다 월말 e = 그 달의 쓸 수 있는 주간 날짜 중 가장 늦은 날. **e 가 그 달의 진짜 마지막 주간 날짜라고 말할 수 있을 때만** 쓴다.
  · DB 의 촘촘한 주간 구간 안(시작 `floor` = 서버 곡선과 같은 `_weekly_run_start`): 그대로.
  · 하한 아래 **연말 날짜**(해마다 가장 늦은 날짜 = 그해 말 살아 있던 모든 종목의 그해 마지막 행): 12월 월말로 그대로 — 원천이 온전하다
    (`krx_cap_agg` 의 「해마다 마지막 주」와 같은 판단).
  · 콜드 서빙본(`--cold-sqlite`, 하한 이전 전 행 — 서버가 3년 이전 곡선을 잇는 같은 sqlite)이 있으면 하한 이전 달도 — 서버 곡선과 같은
    이음매(`serving_seam`: 서빙본은 S 이전, DB 는 그 뒤)로 잇는다. 서빙본 끝과 DB 촘촘한 구간 사이에 거래일 틈(`seam_gap`)이 있으면
    그 틈에 걸친 달은 뺀다.
  · 그 밖의 하한 이전 달은 **건너뛰고 저장된 행을 그대로 둔다** — 되살릴 원천이 없는 값을 부분 행으로 덮지 않는다. 건너뛴 달은 실행 줄에 적는다.

최소 커버리지(261019): 배수 하나를 낸 회사들의 시총이 그 버킷(시장·업종)에서 재무가 하나라도 있는 회사 시총의 절반에 못 미치면
그 배수와 그 분모 합을 비운다(`covered`). 예 — 2020-01~03 PER(TTM)은 직전 해 같은 분기(2018 1~3분기) 칸이 있는 회사만 셀 수 있어
코스피 시총 8%·코스닥 0.3% 로 낸 값이었다(코스닥 68.31 = 3종목). 비우면 화면은 「자료없음」이다 — 순이익을 더한 회사가 없거나
더한 회사가 버킷 시총의 절반에 못 미쳐 대표값이 아니라는 뜻으로 넓힌다(`tools/price_multiple_data._fni`).
"""
from __future__ import annotations

import calendar
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote

from open_proxy_mcp.krx_retention import krx_weekly_cutoff, seam_gap, serving_seam
from open_proxy_mcp.services.price_multiple_data import _WEEKLY_START_SQL, _weekly_run_start

#: 서빙본 sqlite 머리(application_id) — 엉뚱한 파일을 원천으로 쓰지 않는다(`cold_store.APP_ID` 와 같은 값).
COLD_APP_ID = 0x4F504D43

#: 배수를 낸 회사 시총 ÷ 재무가 하나라도 있는 회사 시총 이 이보다 작으면 그 배수를 비운다.
MIN_COVER = 0.5


def covered(cap_metric: float, cap_any: float, min_cover: float | None = None) -> bool:
    """그 배수를 낸 회사들의 시총(`cap_metric`)이 버킷 시총(`cap_any` — 재무가 하나라도 있어 버킷에 센 회사)의 `min_cover`(기본 `MIN_COVER`) 이상인가."""
    return cap_any > 0 and cap_metric >= (MIN_COVER if min_cover is None else min_cover) * cap_any


def _month_last_day(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[4:6])
    return f"{ym}{calendar.monthrange(y, m)[1]:02d}"


class MonthPlan(NamedTuple):
    """계산할 월말과 원천. `src[d]` = "db" | "cold". `skipped` = 건너뛴 달(YYYYMM, 저장된 행을 그대로 둔다)."""
    dates: list[str]
    src: dict[str, str]
    skipped: list[str]


def plan_month_ends(db_dates, floor: str, *, cold_dates=(), seam: str | None = None, gap: bool = False) -> MonthPlan:
    """월말 고르기(순수 함수 — 시험 대상).

    db_dates   DB `krx_weekly` 의 날짜들(--since 이후, 하한 아래 부분 날짜까지 섞여 있다)
    floor      DB 촘촘한 주간 구간의 시작(그 이상은 모든 주가 온전하다)
    cold_dates 서빙본 날짜들(없으면 빈 것) · seam 서빙본은 이 날짜 **미만**만 쓴다 · gap 서빙본 끝과 floor 사이에 거래일 틈이 있나
    """
    db_dates = sorted(set(db_dates))
    dense = [d for d in db_dates if d >= floor]
    year_max: dict[str, str] = {}
    for d in db_dates:
        if d > year_max.get(d[:4], ""):
            year_max[d[:4]] = d
    whole = {d for d in year_max.values() if d < floor}                     # 하한 아래 연말 날짜 — 원천이 온전하다
    cold = [d for d in sorted(set(cold_dates)) if seam and d < seam and d < floor]

    cands: dict[str, list[tuple[str, str]]] = defaultdict(list)
    seen: set[str] = {d[:6] for d in db_dates} | {d[:6] for d in cold}
    for d in dense:
        cands[d[:6]].append((d, "db"))
    for d in cold:
        cands[d[:6]].append((d, "cold"))
    cold_set = set(cold)
    for d in whole:
        if d not in cold_set:                                               # 같은 날짜가 서빙본에도 있으면 서빙본(같은 값)으로
            cands[d[:6]].append((d, "db"))

    dates, src, skipped = [], {}, []
    for ym in sorted(seen):
        if not cands.get(ym):
            skipped.append(ym)
            continue
        e, s = max(cands[ym])
        ok = (e >= floor                                                    # 촘촘한 구간 — 그 뒤 날짜도 DB 에 다 있다
              or (s == "db" and e in whole)                                 # 하한 아래 연말
              or (s == "cold" and (not gap or _month_last_day(ym) < seam)))  # 서빙본 — 틈이 있으면 달 전체가 서빙본 안일 때만
        if ok:
            dates.append(e)
            src[e] = s
        else:
            skipped.append(ym)
    return MonthPlan(dates, src, skipped)


class MonthEndPrices:
    """월말 시세 읽기 — 날짜마다 원천(DB 또는 서빙본)을 골라 (종목, 시장, 시총)을 준다.

    `con` 은 psycopg 연결(읽기만 한다). `cold_sqlite` 가 있으면 서빙본을 읽기 전용(`immutable`)으로 연다.
    """

    def __init__(self, con, since: str, cold_sqlite: str | None = None):
        self.con = con
        latest = con.execute("SELECT max(price_dd) FROM krx_weekly").fetchone()[0]
        if not latest:
            raise SystemExit("krx_weekly 가 비었다 — 먼저 시세를 적재한다")
        self.cutoff = krx_weekly_cutoff(latest)
        self.floor = _weekly_run_start(con.execute(_WEEKLY_START_SQL, (self.cutoff, self.cutoff)).fetchall(), self.cutoff)
        db_dates = [r[0] for r in con.execute("SELECT DISTINCT price_dd FROM krx_weekly WHERE price_dd >= %s", (since,))]
        self.lite = None
        self.cold_max = self.seam = None
        self.gap = False
        cold_dates: list[str] = []
        if cold_sqlite:
            path = Path(cold_sqlite)
            self.lite = sqlite3.connect(f"file:{quote(str(path))}?mode=ro&immutable=1", uri=True)
            if self.lite.execute("PRAGMA application_id").fetchone()[0] != COLD_APP_ID:
                raise SystemExit(f"{path.name} 는 콜드 서빙본이 아니다(application_id 다름) — 아무것도 쓰지 않았다")
            meta = dict(self.lite.execute("SELECT k, v FROM meta").fetchall())
            # coverage_max_dd = 「이 날까지 원천의 모든 행이 서빙본에 있다」(발행기의 약속). 없으면 마지막 행 날짜로(더 보수적이지 않다 —
            # 시험용 서빙본만 그렇다).
            self.cold_max = meta.get("coverage_max_dd") or self.lite.execute("SELECT max(price_dd) FROM krx_weekly").fetchone()[0]
            self.seam = serving_seam(self.cutoff, self.cold_max)
            self.gap = seam_gap(self.seam, self.floor)
            cold_dates = [r[0] for r in self.lite.execute(
                "SELECT DISTINCT price_dd FROM krx_weekly WHERE price_dd >= ? AND price_dd < ?", (since, self.seam))]
        self.plan = plan_month_ends(db_dates, self.floor, cold_dates=cold_dates, seam=self.seam, gap=self.gap)
        #: DB 촘촘한 주간 구간의 날짜(그 날 시세가 온전하다) — 월말이 아닌 저장된 주간 점을 다시 낼 때 쓴다.
        self.dense = frozenset(d for d in db_dates if d >= self.floor)
        self.n_cold_reads = 0

    def rows(self, dd: str) -> list[tuple[str, str, float]]:
        """그 월말의 (종목, 시장, 시총) — 계획에 있는 날짜만."""
        s = self.plan.src[dd]
        if s == "cold":
            self.n_cold_reads += 1
            return self.lite.execute("SELECT ticker, market, mktcap FROM krx_weekly WHERE price_dd = ?", (dd,)).fetchall()
        return self.con.execute("SELECT ticker, market, mktcap FROM krx_weekly WHERE price_dd = %s", (dd,)).fetchall()

    def describe(self) -> str:
        p = self.plan
        n_cold = sum(1 for d in p.dates if p.src[d] == "cold")
        head = (f"월말 원천: DB 촘촘한 주간 {self.floor}~ · 보존 하한 {self.cutoff}"
                + (f" · 콜드 서빙본 ~{self.cold_max}(이음매 {self.seam}{', 틈 있음' if self.gap else ''}) 월말 {n_cold}개" if self.lite else ""))
        if p.skipped:
            head += (f"\n  ⚠ 하한 이전 {len(p.skipped)}개 달 건너뜀(저장된 행 그대로) — DB 는 하한 이전을 종목별 연말 행만 보존해 그 달 월말 "
                     f"시세가 없다: {p.skipped[0]}~{p.skipped[-1]}"
                     + ("" if self.lite else ". 하한 이전까지 다시 내려면 --cold-sqlite <서빙본>"))
        return head

    def close(self) -> None:
        if self.lite is not None:
            self.lite.close()
