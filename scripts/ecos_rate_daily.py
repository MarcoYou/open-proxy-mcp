#!/usr/bin/env python3
"""ECOS 무위험 금리 일별 배치 — `ecos_rate` (CD91·KOFR·국고채 1·3년·기준금리). DART 0콜 · KRX 0콜 · **ECOS 하루 5콜**.

■ 원천 — 한국은행 ECOS `StatisticSearch`(JSON, 연율 %)
    C  CD(91일)          817Y002 / 010502000   일별(거래일)
    K  KOFR(공시RFR)     817Y002 / 010901000   일별(2021-11-25~, 끝 날짜가 다른 계열보다 하루 앞서 있다)
    1  국고채(1년)       817Y002 / 010190000   일별
    3  국고채(3년)       817Y002 / 010200000   일별
    B  한국은행 기준금리 722Y001 / 0101000     **달력 날짜마다 같은 값**을 준다(실측 261021: 2015-12-01~ 3,976행, 값이 바뀐 날 24곳)
                                              → 바뀐 날(적용일)만 한 행으로 접어 둔다. 읽는 법은 다른 계열과 같다(`d <= 기준일` 최신 행).
  시장금리는 하루 늦게 올라온다(마지막 날짜가 어제). 원천이 정정되는 일이 드물게 있다 → 최근 `RECHECK_DAYS`(30)일은 매번 다시 받아 비교한다.

■ 쓰기 — 한 트랜잭션, 쓰기 스위치가 켜졌을 때만
  `ECOS_RATE_WRITE=1`(저장소 변수) 이 아니면 받아서 계획만 낸다(표가 아직 없어도 정상 종료). 켜면:
  · 새 행은 넣고, 이미 있는 행과 값이 다르면 **옛 값 조건**으로 고친다(`UPDATE … WHERE rate = 읽어 둔 옛 값` — 그 사이 바뀌었으면 행 수가 어긋나 전체를 되돌린다).
  · 같은 값은 건드리지 않는다(죽은 행 0). 원천에서 사라진 행은 지우지 않고 경고만 한다.
  · `REVISION_ALERT`(0.50%p) 넘게 바뀐 옛 값은 계열 정의가 달라진 것으로 보고 **아무것도 쓰지 않고** 실패한다.
  · `lock_timeout` 3초 · `extra_float_digits` 3 · 같은 배치끼리 겹치지 않게 advisory lock.
  · 창: 하한 = `krx_retention.krx_weekly_cutoff(오늘)` − `WINDOW_PAD_DAYS`(14일). 주간 시세 창이 최신 주(최대 일주일 안팎 늦다)에서 3년을 세므로 그보다 항상 이르다.
    계열마다 창 앞 마지막 행 하나(carry-in)를 더 둔다 — 하한이 휴장일이어도 「그날 이하 최신 값」이 DB 에서 나온다.
  · 표가 비어 있으면(첫 실행) 같은 코드가 창 전체를 계열당 1콜로 채운다. 며칠 쉬었다 돌아와도 `최신 행 − 3일`부터 이어 받는다.

■ 창 밖 지우기 — `--prune` (일별 실행에는 들어 있지 않다)
  DB 는 3년 창만 두고 그 앞은 보관본(콜드 서빙 `ecos_rate_hist`)이 맡는다. 지우는 쪽이 보관본보다 앞서면 이음매에 구멍이 나므로 세 가드를 **모두** 통과해야 한다:
    ① 보관본 coverage 가 지울 날짜 끝(창 하한 − 1일)을 덮는다(`--cold-coverage-max`, 또는 서빙 목록 `--manifest` 에서 읽는다)
    ② 보관본이 발행된 지 `COLD_DELETE_WAIT_SEC`(72시간 — 서버가 묵은 목록을 쓰는 최대 나이와 같다) 이상 지났다(`--cold-published-at`, 목록이면 `updated_at`)
    ③ 지울 행이 표의 `MAX_PRUNE_FRACTION`(25%) 이하다(창 계산이 틀렸을 때의 안전판)
  계열마다 창 앞 마지막 행(carry-in)은 남긴다.

■ 유지보수 — `--maintain`(쓰기가 켜진 채 KST 매월 1일의 일별 실행에서는 자동)
  `VACUUM (ANALYZE)` + `REINDEX INDEX CONCURRENTLY ecos_rate_pkey`. 계열 다섯 개가 키 공간 다섯 곳의 **오른쪽 끝**에 날마다 한 줄씩 들어가는데,
  맨 오른쪽이 아닌 쪽의 쪽 나누기는 반반으로 갈라진다. 그대로 두면 2년 뒤 기본키 색인이 처음의 두 배(약 0.08 → 0.17 MiB, 로컬 PG17 모의)가 된다.
  한 달에 한 번 다시 만들면 처음 크기 근처(약 0.09 MiB)에 머문다. 읽기·쓰기를 막지 않고 락 대기는 3초다. `--prune` 이 행을 지웠을 때도 한다.

■ 종료코드 — 0: 썼거나 이미 최신 / 계획만 냈다 · 1: 원천·DB 이상(모양·범위·신선도·잘림·정정 과다) · 2: 설정 누락(키·DB).
  🔴 ECOS 키는 URL 경로에 들어간다. URL 을 담은 예외·로그를 그대로 내지 않는다 — 오류는 종류만 적고 `httpx` 로거는 낮춘다.

용례:
  python3 scripts/ecos_rate_daily.py                         # 최근 30일 받아 비교 (스위치가 꺼져 있으면 계획만)
  python3 scripts/ecos_rate_daily.py --dry-run               # 스위치가 켜져 있어도 쓰지 않는다
  python3 scripts/ecos_rate_daily.py --series C,K --since 20260901
  python3 scripts/ecos_rate_daily.py --prune --cold-coverage-max 20231001 --cold-published-at 2026-10-20T00:00:00+00:00
필요 환경: ECOS_API_KEY · DATABASE_URL(DB 를 볼 때) · ECOS_RATE_WRITE=1(쓸 때). 스키마는 `scripts/ecos_rate_ddl.py`.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import struct
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:  # Windows cp949 콘솔에서도 한글 출력 안전
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

KST = timezone(timedelta(hours=9))
ECOS_BASE = "https://ecos.bok.or.kr/api"
TABLE = "ecos_rate"
#: ECOS 한 번에 받을 수 있는 최대 행(요청 끝 번호). 응답 `list_total_count` 가 이보다 크면 잘린 것이다.
PAGE_ROWS = 100_000
#: 매번 다시 받아 옛 값과 비교하는 최근 일수(원천 정정 확인).
RECHECK_DAYS = 30
#: DB 창 하한 = 주간 시세 보존 하한(오늘 기준) − 이 일수.
WINDOW_PAD_DAYS = 14
#: 일별 계열의 마지막 날짜가 오늘보다 이만큼(달력 일) 넘게 늦으면 원천이 멈춘 것이다. 연휴가 열흘 안팎으로 이어지는 해(추석 + 임시공휴일)와
#: 마지막 날짜가 하루 더 늦은 KOFR 을 거짓 경보 없이 넘기는 값이다. 멈춤을 이틀 안에 잡는 용도가 아니라 한두 주 멈춘 원천을 잡는 용도다.
STALE_DAYS = 14
#: 이 값(%p)보다 크게 바뀐 옛 값은 정정이 아니라 계열 정의 변경으로 본다.
REVISION_ALERT = 0.50
#: 상식 범위(연율 %) — 이 밖의 값이 오면 받은 전체를 쓰지 않는다. DDL 의 CHECK 와 같다.
RATE_MIN, RATE_MAX = -1.0, 30.0
#: 서버 콜드 목록 최대 나이(`cold_store.OPM_COLD_MANIFEST_MAX_AGE_SEC` 기본 72시간)와 같다 — 시험이 둘이 같음을 지킨다.
COLD_DELETE_WAIT_SEC = 72 * 3600
MAX_PRUNE_FRACTION = 0.25
#: 같은 배치 둘이 겹쳐 쓰지 않게 — `pg_try_advisory_xact_lock` 키(ASCII "OPMECOSR").
LOCK_KEY = int.from_bytes(b"OPMECOSR", "big")
COLD_DATASET = "ecos_rate_hist"
_TIME_RE = re.compile(r"[0-9]{8}")


@dataclass(frozen=True)
class Series:
    code: str      # 표의 series ("char")
    stat: str      # ECOS 통계표 코드
    item: str      # ECOS 항목 코드
    name: str      # ECOS 항목 이름(바뀌면 경고 — 코드가 바뀌면 실패)
    step: bool     # True 면 값이 바뀐 날만 행으로 둔다
    first_dd: str  # ECOS 가 값을 주는 첫 날(YYYYMMDD)


SERIES: tuple[Series, ...] = (
    Series("C", "817Y002", "010502000", "CD(91일)", False, "19950103"),
    Series("K", "817Y002", "010901000", "KOFR(공시RFR)", False, "20211125"),
    Series("1", "817Y002", "010190000", "국고채(1년)", False, "20000201"),
    Series("3", "817Y002", "010200000", "국고채(3년)", False, "19981113"),
    Series("B", "722Y001", "0101000", "한국은행 기준금리", True, "19990506"),
)
BY_CODE = {s.code: s for s in SERIES}


class EcosError(RuntimeError):
    """원천 오류. **메시지에 URL(키 포함)을 넣지 않는다** — 호출부가 그대로 출력한다."""


class ConflictError(RuntimeError):
    """쓰는 사이 다른 쪽이 같은 행을 바꿨다 — 트랜잭션 전체를 되돌린다."""


Point = tuple[date, float]


# ── 순수 함수 ──────────────────────────────────────────────────────────────────────
def f32(x: float) -> float:
    """real(4바이트)에 저장했다 읽은 값 — DB 값과 비교할 때 같은 잣대로."""
    return struct.unpack("f", struct.pack("f", x))[0]


def parse_dd(s: str) -> date:
    return datetime.strptime(s, "%Y%m%d").date()


def fmt_dd(d: date) -> str:
    return d.strftime("%Y%m%d")


def window_lo(today: date) -> date:
    """DB 창 하한(포함). 주간 시세 보존 하한(오늘 기준) − `WINDOW_PAD_DAYS`."""
    from open_proxy_mcp.krx_retention import krx_weekly_cutoff
    return parse_dd(krx_weekly_cutoff(fmt_dd(today))) - timedelta(days=WINDOW_PAD_DAYS)


def parse_payload(s: Series, payload: Any) -> list[Point]:
    """ECOS `StatisticSearch` 응답 → [(날짜, 값)] 날짜순. 이상하면 EcosError. 자료 없음(INFO-200)은 빈 목록."""
    if not isinstance(payload, dict):
        raise EcosError(f"{s.code}: 응답이 객체가 아니다")
    st = payload.get("StatisticSearch")
    if st is None:
        res = payload.get("RESULT")
        code = res.get("CODE") if isinstance(res, dict) else None
        if code == "INFO-200":                       # 해당하는 데이터가 없습니다
            return []
        raise EcosError(f"{s.code}: ECOS 오류 응답 {code if isinstance(code, str) and len(code) <= 16 else '(코드 없음)'}")
    if not isinstance(st, dict):
        raise EcosError(f"{s.code}: StatisticSearch 모양이 다르다")
    rows = st.get("row") or []
    try:
        total = int(st.get("list_total_count"))
    except (TypeError, ValueError):
        raise EcosError(f"{s.code}: list_total_count 가 없다") from None
    if total != len(rows):
        raise EcosError(f"{s.code}: 응답이 잘렸다 — 전체 {total}행 중 {len(rows)}행")
    out: dict[date, float] = {}
    for r in rows:
        if not isinstance(r, dict):
            raise EcosError(f"{s.code}: 행 모양이 다르다")
        if r.get("STAT_CODE") != s.stat or r.get("ITEM_CODE1") != s.item:
            raise EcosError(f"{s.code}: 통계표·항목 코드가 요청과 다르다")
        t = r.get("TIME")
        if not isinstance(t, str) or not _TIME_RE.fullmatch(t):
            raise EcosError(f"{s.code}: TIME 이 YYYYMMDD 가 아니다")
        try:
            d = parse_dd(t)
        except ValueError:
            raise EcosError(f"{s.code}: TIME 이 달력에 없다") from None
        raw = r.get("DATA_VALUE")
        if raw is None or str(raw).strip() == "":
            continue                                  # 값이 비어 있는 날은 없는 것으로
        try:
            v = float(str(raw).replace(",", ""))
        except ValueError:
            raise EcosError(f"{s.code}: DATA_VALUE 가 숫자가 아니다") from None
        if not math.isfinite(v) or not (RATE_MIN < v < RATE_MAX):
            raise EcosError(f"{s.code}: 값이 상식 범위 ({RATE_MIN}, {RATE_MAX}) 밖이다")
        if d in out:
            raise EcosError(f"{s.code}: 같은 날짜가 두 번 왔다")
        out[d] = v
    return sorted(out.items())


def fold_steps(points: Sequence[Point], prior: float | None) -> list[Point]:
    """달력 날짜마다 같은 값을 주는 계열을 값이 바뀐 날만으로 접는다. `prior` = 첫 날 바로 앞에서 DB 가 아는 값(없으면 None → 첫 날이 행이 된다)."""
    out: list[Point] = []
    prev = None if prior is None else f32(prior)
    for d, v in points:
        if prev is None or f32(v) != prev:
            out.append((d, v))
            prev = f32(v)
    return out


def with_carry_in(points: Sequence[Point], lo: date) -> list[Point]:
    """창 안(d >= lo)의 점 + 창 앞 마지막 점 하나(carry-in)."""
    before = [p for p in points if p[0] < lo]
    inside = [p for p in points if p[0] >= lo]
    return ([before[-1]] if before else []) + inside


def fetch_start(s: Series, today: date, lo: date, db_max: date | None, since: date | None = None) -> date:
    """이 계열을 어느 날부터 받을까."""
    first = parse_dd(s.first_dd)
    if since is not None:
        return max(since, first)
    if db_max is None:                                # 비어 있다 → 창 전체 + carry-in. 접는 계열은 첫 관측일부터(접은 첫 점이 진짜 변경일이 되게)
        return first if s.step else max(first, lo - timedelta(days=10))
    start = max(first, min(today - timedelta(days=RECHECK_DAYS), db_max - timedelta(days=3)))
    return start if s.step else max(start, lo - timedelta(days=10))          # 창 앞은 carry-in 몫(열흘)만 — 그 앞은 필요 없다


# ── 원천 ───────────────────────────────────────────────────────────────────────────
def _quiet_http_loggers() -> None:
    """httpx 는 요청 URL 을 INFO 로 남긴다 — ECOS 키가 경로에 있으므로 낮춘다."""
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)


def ecos_get(client: Any, key: str, path: str, *, tries: int = 3, sleep=time.sleep) -> Any:
    """ECOS GET → JSON. 일시 오류(연결·5xx·JSON 아님)는 짧게 다시 시도한다. 예외 메시지에 URL 을 넣지 않는다."""
    url = f"{ECOS_BASE}/{path.replace('{key}', key)}"
    last = "?"
    for i in range(tries):
        try:
            r = client.get(url)
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    last = "JSON 아님"
            else:
                last = f"HTTP {r.status_code}"
                if r.status_code < 500 and r.status_code != 429:
                    break
        except Exception as exc:                      # httpx.HTTPError 외에 InvalidURL 등도 메시지에 URL 이 들 수 있다 — 종류만
            last = type(exc).__name__
        if i + 1 < tries:
            sleep(2.0 * (i + 1))
    raise EcosError(f"ECOS 요청 실패 ({last})")


def fetch_series(client: Any, key: str, s: Series, start: date, end: date, *, sleep=time.sleep) -> list[Point]:
    path = f"StatisticSearch/{{key}}/json/kr/1/{PAGE_ROWS}/{s.stat}/D/{fmt_dd(start)}/{fmt_dd(end)}/{s.item}"
    try:
        payload = ecos_get(client, key, path, sleep=sleep)
    except EcosError as exc:
        raise EcosError(f"{s.code}: {exc}") from None
    pts = parse_payload(s, payload)
    _warn_name_drift(s, payload)
    return pts


def _warn_name_drift(s: Series, payload: Any) -> None:
    try:
        row = (payload.get("StatisticSearch") or {}).get("row") or []
        if row and row[0].get("ITEM_NAME1") != s.name:
            print(f"::warning::{s.code}: ECOS 항목 이름이 달라졌다 ({row[0].get('ITEM_NAME1')!r} ≠ {s.name!r}) — 코드는 같다. 확인할 것")
    except Exception:
        pass


# ── 계획 ───────────────────────────────────────────────────────────────────────────
@dataclass
class Plan:
    insert: list[tuple[str, date, float]] = field(default_factory=list)
    update: list[tuple[str, date, float, float]] = field(default_factory=list)    # (series, d, 새 값, 옛 값)
    same: int = 0
    orphans: list[tuple[str, date]] = field(default_factory=list)                 # DB 에 있으나 원천 구간에 없는 행

    @property
    def writes(self) -> int:
        return len(self.insert) + len(self.update)


def make_plan(points: Mapping[str, Sequence[Point]], existing: Mapping[tuple[str, date], float],
              ranges: Mapping[str, tuple[date, date]]) -> Plan:
    """받은 점들과 DB 의 기존 행을 맞대 쓸 일을 센다. `ranges[code]` = 이 계열을 받은 [시작, 끝] (고아 판정 구간)."""
    plan = Plan()
    for code, pts in points.items():
        seen = set()
        for d, v in pts:
            seen.add(d)
            old = existing.get((code, d))
            if old is None:
                plan.insert.append((code, d, v))
            elif f32(old) == f32(v):
                plan.same += 1
            else:
                plan.update.append((code, d, v, old))
        lo, hi = ranges[code]
        for (c, d), _ in existing.items():
            if c == code and lo <= d <= hi and d not in seen:
                plan.orphans.append((c, d))
    plan.insert.sort(key=lambda r: (r[0], r[1]))
    plan.update.sort(key=lambda r: (r[0], r[1]))
    plan.orphans.sort()
    return plan


def check_revisions(plan: Plan) -> None:
    big = [(c, d, new, old) for c, d, new, old in plan.update if abs(f32(new) - f32(old)) > REVISION_ALERT]
    if big:
        c, d, new, old = big[0]
        raise EcosError(f"옛 값이 {REVISION_ALERT}%p 넘게 바뀌었다 ({len(big)}건, 첫 건 {c} {d} {old:g}→{new:g}) — 계열 정의가 달라졌는지 확인 전에는 쓰지 않는다")


def check_freshness(points: Mapping[str, Sequence[Point]], today: date) -> None:
    for code, pts in points.items():
        s = BY_CODE[code]
        if s.step:
            continue
        if not pts:
            raise EcosError(f"{code}: 받은 값이 없다 — 최근 {RECHECK_DAYS}일 거래일이 있는데 비어 있으면 원천 이상이다")
        if (today - pts[-1][0]).days > STALE_DAYS:
            raise EcosError(f"{code}: 마지막 값이 {pts[-1][0]} — 오늘({today})보다 {STALE_DAYS}일 넘게 늦다")


# ── DB ─────────────────────────────────────────────────────────────────────────────
def table_exists(con) -> bool:
    return con.execute("SELECT to_regclass(%s) IS NOT NULL", (f"public.{TABLE}",)).fetchone()[0]


def db_max_by_series(con) -> dict[str, date]:
    return {r[0]: r[1] for r in con.execute(f'SELECT series::text, max(d) FROM {TABLE} GROUP BY series')}


def db_prior(con, code: str, before: date) -> float | None:
    r = con.execute(f'SELECT rate::float8 FROM {TABLE} WHERE series = %s::"char" AND d < %s ORDER BY d DESC LIMIT 1',
                    (code, before)).fetchone()
    return None if r is None else r[0]


def read_existing(con, ranges: Mapping[str, tuple[date, date]], *, lock: bool) -> dict[tuple[str, date], float]:
    out: dict[tuple[str, date], float] = {}
    for code, (lo, hi) in ranges.items():
        q = (f'SELECT series::text, d, rate::float8 FROM {TABLE} WHERE series = %s::"char" AND d BETWEEN %s AND %s'
             + (" FOR UPDATE" if lock else ""))
        for c, d, v in con.execute(q, (code, lo, hi)):
            out[(c, d)] = v
    return out


def apply_plan(con, plan: Plan) -> None:
    """같은 트랜잭션 안에서 쓴다. 고치는 행은 옛 값 조건 — 행 수가 어긋나면 ConflictError."""
    for code, d, new, old in plan.update:
        cur = con.execute(f'UPDATE {TABLE} SET rate = %s::real WHERE series = %s::"char" AND d = %s AND rate = %s::real',
                          (new, code, d, old))
        if cur.rowcount != 1:
            raise ConflictError(f"{code} {d}: 읽은 뒤 값이 바뀌었다")
    if plan.insert:
        with con.cursor() as cur:
            cur.executemany(f'INSERT INTO {TABLE} (series, d, rate) VALUES (%s::"char", %s, %s::real)', plan.insert)


@dataclass
class Fetched:
    points: dict[str, list[Point]]
    ranges: dict[str, tuple[date, date]]
    calls: int


def settle(con, fetched: Fetched, lo: date, *, write: bool) -> Plan:
    """한 트랜잭션: 잠금 → 기존 행 읽기 → 계획 → (write 면) 쓰기. `fetched.points` 는 접기·carry-in 전의 원천 점이다.

    🔴 `con` 은 autocommit 연결이어야 한다 — 아니면 `con.transaction()` 이 바깥 트랜잭션의 세이브포인트가 되어
    연결을 닫을 때 쓴 것이 통째로 되돌아간다."""
    _need_autocommit(con)
    with con.transaction():
        con.execute("SET LOCAL lock_timeout = '3s'")
        con.execute("SET LOCAL statement_timeout = '60s'")
        con.execute("SET LOCAL extra_float_digits = 3")
        if write:
            if not con.execute("SELECT pg_try_advisory_xact_lock(%s)", (LOCK_KEY,)).fetchone()[0]:
                raise ConflictError("다른 ecos_rate 배치가 쓰는 중이다")
        else:
            con.execute("SET TRANSACTION READ ONLY")
        points: dict[str, list[Point]] = {}
        for code, pts in fetched.points.items():
            if BY_CODE[code].step:
                pts = fold_steps(pts, db_prior(con, code, fetched.ranges[code][0]))
            points[code] = with_carry_in(pts, lo)
        existing = read_existing(con, fetched.ranges, lock=write)
        plan = make_plan(points, existing, fetched.ranges)
        check_revisions(plan)
        if write and plan.writes:
            apply_plan(con, plan)
        return plan


def _need_autocommit(con) -> None:
    if not getattr(con, "autocommit", False):
        raise ValueError("autocommit 연결이 필요하다 (psycopg.connect(url, autocommit=True))")


def verify(con, today: date, codes: Sequence[str] | None = None) -> list[str]:
    """쓴 뒤 산출물 확인 — 문제 목록(비면 정상). `codes` = 이번에 받은 계열(기본 전부)."""
    bad = []
    stats = {r[0]: (r[1], r[2], r[3]) for r in con.execute(
        f"SELECT series::text, count(*), min(d), max(d) FROM {TABLE} GROUP BY series")}
    for s in (BY_CODE[c] for c in (codes or [x.code for x in SERIES])):
        if s.code not in stats:
            bad.append(f"{s.code}: 표에 행이 없다")
            continue
        n, mn, mx = stats[s.code]
        if not s.step and (today - mx).days > STALE_DAYS:
            bad.append(f"{s.code}: 최신 행이 {mx} — 오늘({today})보다 {STALE_DAYS}일 넘게 늦다")
    return bad


# ── 유지보수 ────────────────────────────────────────────────────────────────────────
PK_INDEX = f"{TABLE}_pkey"


def table_sizes(con) -> dict[str, int]:
    r = con.execute(f"SELECT pg_total_relation_size('{TABLE}'), pg_relation_size('{TABLE}'), pg_indexes_size('{TABLE}'), "
                    f"(SELECT count(*) FROM {TABLE})").fetchone()
    return {"total": r[0], "heap": r[1], "index": r[2], "rows": r[3]}


def maintain(con) -> tuple[dict[str, int], dict[str, int]]:
    """VACUUM (ANALYZE) + 기본키 색인 다시 만들기(CONCURRENTLY). (전, 후) 크기. 중간에 실패하면 남은 임시 색인을 치운다."""
    import psycopg
    _need_autocommit(con)
    before = table_sizes(con)
    con.execute("SET lock_timeout = '3s'")
    con.execute("SET statement_timeout = '120s'")
    con.execute(f"VACUUM (ANALYZE) {TABLE}")
    try:
        con.execute(f"REINDEX INDEX CONCURRENTLY {PK_INDEX}")
    except psycopg.Error:
        for (name,) in con.execute("SELECT c.relname FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                                   "WHERE i.indrelid = %s::regclass AND NOT i.indisvalid", (TABLE,)).fetchall():
            con.execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{name}"')
        raise
    return before, table_sizes(con)


# ── 창 밖 지우기 ───────────────────────────────────────────────────────────────────
def cold_guard_from_manifest(raw: bytes, dataset: str = COLD_DATASET) -> tuple[date, date, datetime]:
    """서빙 목록(JSON) → (coverage.min_dd, coverage.max_dd, 목록 updated_at). 서버가 읽는 검증(`cold_store`)을 그대로 쓴다."""
    from open_proxy_mcp import cold_store
    m = cold_store._validate_manifest(json.loads(raw))
    _, f = cold_store._dataset_entry(m, dataset)
    upd = m.get("updated_at")
    if not isinstance(upd, str):
        raise EcosError("서빙 목록에 updated_at 이 없다")
    return parse_dd(f["coverage"]["min_dd"]), parse_dd(f["coverage"]["max_dd"]), parse_aware(upd)


def parse_aware(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise EcosError("시각에 시간대가 없다 — 「+00:00」처럼 적을 것")
    return dt


def prune_rows(con, lo: date, *, cold_min: date, cold_max: date, cold_published_at: datetime,
               now: datetime, write: bool) -> tuple[int, int]:
    """창 밖 행을 지운다. (지울 행 수, 표 행 수). 가드를 못 넘으면 EcosError, 아무것도 지우지 않는다."""
    _need_autocommit(con)
    age = (now - cold_published_at).total_seconds()
    if age < COLD_DELETE_WAIT_SEC:
        raise EcosError(f"보관본 발행 뒤 {age / 3600:.1f}시간 — {COLD_DELETE_WAIT_SEC // 3600}시간이 지나기 전에는 지우지 않는다")
    end = lo - timedelta(days=1)
    if cold_max < end:
        raise EcosError(f"보관본 coverage 가 {cold_max} 까지 — 지울 끝 날짜 {end} 를 덮지 못한다")
    sel = (f"FROM {TABLE} r WHERE r.d < (SELECT max(x.d) FROM {TABLE} x WHERE x.series = r.series AND x.d < %s)")
    with con.transaction():
        con.execute("SET LOCAL lock_timeout = '3s'")
        con.execute("SET LOCAL statement_timeout = '60s'")
        if not con.execute("SELECT pg_try_advisory_xact_lock(%s)", (LOCK_KEY,)).fetchone()[0]:
            raise ConflictError("다른 ecos_rate 배치가 쓰는 중이다")
        total = con.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0]
        mn = con.execute(f"SELECT min(d) FROM {TABLE}").fetchone()[0]
        n = con.execute(f"SELECT count(*) {sel}", (lo,)).fetchone()[0]
        if n == 0:
            return 0, total
        if cold_min > mn:
            raise EcosError(f"보관본 coverage 가 {cold_min} 부터 — 표의 가장 이른 날 {mn} 을 덮지 못한다")
        if n > total * MAX_PRUNE_FRACTION:
            raise EcosError(f"지울 행 {n} 이 표 {total} 행의 {MAX_PRUNE_FRACTION:.0%} 를 넘는다 — 창 계산이 틀렸는지 확인")
        if write:
            cur = con.execute(f"DELETE FROM {TABLE} r WHERE r.d < (SELECT max(x.d) FROM {TABLE} x WHERE x.series = r.series AND x.d < %s)", (lo,))
            if cur.rowcount != n:
                raise ConflictError(f"지운 행 {cur.rowcount} ≠ 센 행 {n}")
    return n, total


# ── 실행 ───────────────────────────────────────────────────────────────────────────
def write_enabled(env: Mapping[str, str] = os.environ) -> bool:
    return (env.get("ECOS_RATE_WRITE") or "").strip() == "1"


def run_fetch(client: Any, key: str, codes: Sequence[str], today: date, lo: date, db_max: Mapping[str, date],
              since: date | None, *, sleep=time.sleep) -> Fetched:
    """계열마다 1콜. 값이 바뀐 날만 있는 계열(B)의 따라잡기 기준은 일별 계열의 가장 이른 최신 행이다(B 자신의 최신 행은 마지막 변경일이라 오래됐을 수 있다)."""
    daily_max = [db_max[c] for c in db_max if not BY_CODE[c].step]
    points: dict[str, list[Point]] = {}
    ranges: dict[str, tuple[date, date]] = {}
    for code in codes:
        s = BY_CODE[code]
        ref = db_max.get(code)
        if s.step and ref is not None and daily_max:
            ref = min(daily_max)
        start = fetch_start(s, today, lo, ref, since)
        points[code] = fetch_series(client, key, s, start, today, sleep=sleep)
        ranges[code] = (start, today)
    check_freshness(points, today)
    return Fetched(points, ranges, calls=len(codes))


def summarize(fetched: Fetched, plan: Plan, wrote: bool) -> None:
    for code in fetched.points:
        ins = [r for r in plan.insert if r[0] == code]
        upd = [r for r in plan.update if r[0] == code]
        lo, hi = fetched.ranges[code]
        last = fetched.points[code][-1][0] if fetched.points[code] else None
        print(f"  {code} {BY_CODE[code].name}: 받은 구간 {lo}~{hi} · 원천 {len(fetched.points[code])}행 · 마지막 {last} · "
              f"새 {len(ins)} · 정정 {len(upd)}")
    for c, d, new, old in plan.update[:10]:
        print(f"  정정 {c} {d}: {old:g} → {new:g}")
    if plan.orphans:
        print(f"::warning::원천 구간에 없는데 DB 에 있는 행 {len(plan.orphans)}건 (지우지 않음) — 첫 건 {plan.orphans[0][0]} {plan.orphans[0][1]}")
    print(f"계획: 새 {len(plan.insert)} · 정정 {len(plan.update)} · 같음 {plan.same} → {'썼다' if wrote else '쓰지 않았다'}")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="스위치가 켜져 있어도 쓰지 않고 계획만")
    ap.add_argument("--series", help="쉼표로 구분한 계열 코드(기본 전부: C,K,1,3,B)")
    ap.add_argument("--since", help="이 날(YYYYMMDD)부터 받는다 — 며칠 쉰 뒤 따라잡기. 창 하한 앞은 자른다")
    ap.add_argument("--today", help="기준일 YYYYMMDD(시험·재현용, 기본 KST 오늘)")
    ap.add_argument("--prune", action="store_true", help="받지 않고 창 밖 행을 지운다(가드 필요)")
    ap.add_argument("--maintain", action="store_true", help="끝에 VACUUM (ANALYZE) + 기본키 색인 다시 만들기 (쓰기가 켜져 있을 때만; KST 매월 1일엔 자동)")
    ap.add_argument("--cold-coverage-max", help="보관본 coverage 끝(YYYYMMDD)")
    ap.add_argument("--cold-coverage-min", help="보관본 coverage 시작(YYYYMMDD, 기본 20151201)")
    ap.add_argument("--cold-published-at", help="보관본 발행 시각(ISO, 시간대 포함)")
    ap.add_argument("--manifest", help="서빙 목록(https 또는 file://) — coverage·발행 시각을 여기서 읽는다")
    args = ap.parse_args(argv)
    _quiet_http_loggers()

    today = parse_dd(args.today) if args.today else datetime.now(KST).date()
    lo = window_lo(today)
    codes = [c.strip() for c in args.series.split(",")] if args.series else [s.code for s in SERIES]
    bad = [c for c in codes if c not in BY_CODE]
    if bad:
        print(f"알 수 없는 계열 {bad} — {','.join(BY_CODE)} 중에서", file=sys.stderr)
        return 2
    url = os.environ.get("DATABASE_URL")
    write = write_enabled() and not args.dry_run
    print(f"ecos_rate 일별 — 기준일 {today} · 창 하한 {lo} · 계열 {','.join(codes)} · 쓰기 {'켜짐' if write else '꺼짐(계획만)'}")

    import psycopg
    if args.prune:
        return _main_prune(args, url, lo, write, psycopg)

    key = os.environ.get("ECOS_API_KEY")
    if not key:
        print("ECOS_API_KEY 가 없습니다.", file=sys.stderr)
        return 2
    if write and not url:
        print("DATABASE_URL 이 없습니다 — 쓸 수 없습니다.", file=sys.stderr)
        return 2

    import httpx
    db_max: dict[str, date] = {}
    con = None
    try:
        if url:
            con = psycopg.connect(url, connect_timeout=20, autocommit=True)
            if table_exists(con):
                db_max = db_max_by_series(con)
            else:
                print("::notice::ecos_rate 표가 아직 없다 (scripts/ecos_rate_ddl.py 를 적용하기 전) — 받기만 확인한다.")
                if write:
                    print("::error::쓰기 스위치가 켜져 있는데 표가 없다.")
                    return 1
                con.close()
                con = None
        since = parse_dd(args.since) if args.since else None
        with httpx.Client(timeout=30) as client:
            fetched = run_fetch(client, key, codes, today, lo, db_max, since)
        if con is None:
            for code in codes:
                pts = fetched.points[code]
                print(f"  {code} {BY_CODE[code].name}: 원천 {len(pts)}행 · 마지막 {pts[-1][0] if pts else None}")
            print("계획만 냈다 (DB 비교 없음).")
            return 0
        plan = settle(con, fetched, lo, write=write)
        summarize(fetched, plan, wrote=write and plan.writes > 0)
        if write:
            problems = verify(con, today, codes)
            for p in problems:
                print(f"::error::{p}")
            if problems:
                return 1
            if args.maintain or today.day == 1:
                _report_maintain(con)
        return 0
    except (EcosError, ConflictError) as exc:
        print(f"::error::{exc}")
        return 1
    except psycopg.Error as exc:
        print(f"::error::DB 오류 ({type(exc).__name__})")        # 접속 문자열이 메시지에 들어갈 수 있어 종류만
        return 1
    finally:
        if con is not None:
            con.close()


def _report_maintain(con) -> None:
    before, after = maintain(con)
    print(f"유지보수: 표+색인 {before['total']} → {after['total']} B (색인 {before['index']} → {after['index']}) · {after['rows']}행")


def _main_prune(args, url: str | None, lo: date, write: bool, psycopg) -> int:
    if not url:
        print("DATABASE_URL 이 없습니다.", file=sys.stderr)
        return 2
    try:
        if args.manifest:
            from open_proxy_mcp import cold_store
            raw = cold_store._fetch_bytes_sync(args.manifest, max_bytes=1 << 20, timeout=15.0)
            cmin, cmax, published = cold_guard_from_manifest(raw)
        else:
            if not (args.cold_coverage_max and args.cold_published_at):
                print("--prune 은 --manifest 또는 (--cold-coverage-max 와 --cold-published-at) 가 필요합니다.", file=sys.stderr)
                return 2
            cmin = parse_dd(args.cold_coverage_min or "20151201")
            cmax = parse_dd(args.cold_coverage_max)
            published = parse_aware(args.cold_published_at)
        with psycopg.connect(url, connect_timeout=20, autocommit=True) as con:
            n, total = prune_rows(con, lo, cold_min=cmin, cold_max=cmax, cold_published_at=published,
                                  now=datetime.now(timezone.utc), write=write)
            print(f"창 밖 행 {n} / 표 {total} → {'지웠다' if write and n else '지우지 않았다'} (창 하한 {lo}, 보관본 {cmin}~{cmax})")
            if write and (args.maintain or n):
                _report_maintain(con)
        return 0
    except (EcosError, ConflictError, ValueError) as exc:
        print(f"::error::{exc}")
        return 1
    except psycopg.Error as exc:
        print(f"::error::DB 오류 ({type(exc).__name__})")
        return 1
    except Exception as exc:        # 서빙 목록 받기(ColdError 등)
        print(f"::error::서빙 목록을 읽지 못했다 ({type(exc).__name__})")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
