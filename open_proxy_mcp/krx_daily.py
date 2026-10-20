# -*- coding: utf-8 -*-
"""`krx_daily` — 최근 약 1년(최신 거래일 − 달력 1년 − 1주) 일별 시세(KRX 응답 전종목·전 필드) 저장·조회.

261018 신설(Supabase 재설계 3단계). 배경: `trading_data(scope="quote")` 가 하루 약 400번 KRX 를 불러 전종목 응답을
버리고 한 줄만 캐시했고, 하루 첫 시세 질문은 KRX 갱신을 떠안아 2~12초를 기다렸다. KRX `stk_bydd_trd`·`ksq_bydd_trd`
응답에는 이미 시·고·저·종가, 거래량, 거래대금, 상장주식수, 공표 전일대비가 들어 있어 **추가 호출 없이** 그대로 저장할 수 있다.

■ 저장 모양(261019 재설계 v03 — 스키마는 `scripts/krx_daily_ddl.py`. 서버는 DDL 을 실행하지 않는다)
    `krx_px`     사실값: 종목 번호 · 거래일(date) · 시장 1바이트 · 시·고·저·종·공표 전일대비(int4) · 거래량·거래대금·상장주식수(int8)
    `krx_day`    보관 거래일 달력: 그날 행 수 · 휴장 · 주 닫는 날 · 콜드 보관 여부 — 최신일·날짜 목록·창 경계는 여기서 읽는다
    `krx_ticker` 종목 번호표(6자리 단축코드 ↔ 정수, 영숫자 코드 포함)
  옛 이름 `krx_daily`(표)·`krx_daily_v`·`price_latest` 는 같은 칸의 **뷰**다 — 읽는 쪽은 그대로 읽는다.
  시가총액(= close × list_shrs)과 등락률은 **뷰**가 읽을 때 계산한다. `krx_daily_v` 는 직전 보관 행 대비(`chg`·`chg_pct`)와
  KRX 공표 대비(`krx_chg`·`krx_chg_pct` = 기준가 대비 — 휴장 다음 날·권리락일에도 공표값과 같다)를 함께 낸다.
■ 보관: 최신 거래일 − 달력 1년 − 1주(그 날 포함) 이후. 거래일로 약 250~255일. 그보다 오래된 날은 **콜드 보관본에 들어간 날만**
  지운다(`krx_day.cold` — 보관본 연도 파일을 올린 쪽이 켠다). 들어가지 않은 날은 남기고 경고한다(지운 뒤에는 되살릴 곳이 없다).
■ 주간 닫기(261018, 재설계 5-1 쓰는 쪽): 이 저장분이 시세 기록의 **주(主)** 다. `krx_weekly` 는 **닫힌 주만** 여기서 한 번 옮겨 적는다
  (`sync_weekly`). 종전에는 매 거래일 이번 주 `krx_weekly` 행을 지우고 다시 넣어 하루 약 2.2천 행의 죽은 행이 생겼다.
    · 한 주 = ISO 주를 달력 연도 안으로 자른 것(`krx_retention.week_unit`) — 연말 거래일이 다음 해 첫 주에 지워지지 않게.
    · 닫는 때: 그 주의 마지막 평일(보통 금요일)이 저장됐거나, 더 늦은 주의 거래일이 저장됐을 때(금요일 휴장·누락 대비).
    · 닫는 값: 그 주에서 일별 저장분에 있는 가장 늦은 거래일. 같은 주는 **더 늦은 날로만** 바꾼다(이미 같거나 늦은 날이 있으면 그대로).
  이번 주(아직 안 닫힌 주) 최신값은 일별 저장분에만 있다 — 「닫힌 주 + 최신 거래일」 모양이 필요한 배치는 `WEEKLY_WITH_LATEST` 를 읽는다.
  같은 규칙의 주 닫는 날 표시가 달력(`krx_day.wk_close`)에도 있다(`WK_CLOSE_SQL`).
■ 휴장 판정(`closed_verdict`): 코스피·코스닥 **둘 다** 비고, 그 날 뒤로 평일이 2일 이상 지났고, 다음 저장 거래일의 기준가(종가 − 공표 대비)가
  그 앞 저장 거래일 종가와 이어질 때만 휴장이다. 빈 응답 한 번으로 정하지 않는다 — KRX 는 게시 전·일시적으로
  거래일을 0행으로 준다(20260702 실측: 코스피 빈 응답 한 번을 휴장으로 적어 그날 기준가 리셋 1건·주식수 변동 8건이 어긋났다).
  다음 거래일이 아직 저장되지 않았거나 그 날 공표 대비가 없으면 **판정을 미룬다**(휴장으로 적지 않는다) — KRX 오류(키 만료·한도·장애)도
  빈 응답으로 오므로, 미루지 않으면 하루 넘게 KRX 가 막힌 거래일이 휴장으로 굳어 다시 메워지지 않는다(10-19 검토에서 재현).

이 모듈은 **DB 와 순수 함수만** 다룬다(네트워크 없음). KRX 를 부르는 쪽은 `services/krx_refresher.py`.
`services/price_multiple_data.py` 를 import 하지 않는다 — 그쪽이 이 모듈을 import 한다(순환 방지).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Callable, Iterable

from open_proxy_mcp.krx_retention import week_unit
from open_proxy_mcp.market_codes import KQ as MKT_KQ
from open_proxy_mcp.market_codes import KS as MKT_KS

logger = logging.getLogger(__name__)

#: 보관 창 — 최신 거래일에서 달력으로 이만큼 전까지(그 날 포함). 거래일로 약 250~255일. 바꾸려면 이 두 값만.
KEEP_YEARS = 1
KEEP_EXTRA_DAYS = 7

#: 갱신 담당 하나만 돌게 하는 `pg_try_advisory_lock` 키(고정). ASCII "OPMKRXD1" — 다른 용도와 겹치지 않는 값.
REFRESH_LOCK_KEY = int.from_bytes(b"OPMKRXD1", "big")
assert 0 < REFRESH_LOCK_KEY < 2**63

#: `daily_records` 한 행의 칸 순서. cmp = KRX 공표 전일대비(CMPPREVDD_PRC, 기준가 = close − cmp).
DAILY_COLUMNS = ("trd_dd", "ticker", "market", "open", "high", "low", "close", "volume", "value", "list_shrs", "cmp")

#: 시장 표기 — 뷰·읽는 쪽은 KS/KQ, 사실값 표는 1바이트("char").
_MKT_CHAR = {MKT_KS: "S", MKT_KQ: "Q"}


def _d(dd: str) -> date:
    return date(int(dd[:4]), int(dd[4:6]), int(dd[6:8]))


def _dd(d: date) -> str:
    return f"{d.year:04d}{d.month:02d}{d.day:02d}"


def window_lo(latest: str) -> str:
    """보관 창의 첫날(YYYYMMDD, 그 날 포함) = 최신 거래일 − 달력 `KEEP_YEARS` 년 − `KEEP_EXTRA_DAYS` 일.
    같은 월·일이 없으면(2월 29일) 2월 28일에서 뺀다(`krx_retention.krx_weekly_cutoff` 와 같은 규칙).
    예) 20261016 → 20251009. SQL 판(`MISSING_SQL` 안의 interval 계산)과 같은 값이다(시험이 대조)."""
    d = _d(latest)
    try:
        y = d.replace(year=d.year - KEEP_YEARS)
    except ValueError:                               # 윤일 → 평년
        y = date(d.year - KEEP_YEARS, 2, 28)
    return _dd(y - timedelta(days=KEEP_EXTRA_DAYS))


def weekdays_after(dd: str, today: str) -> int:
    """`dd` 뒤 `today` 까지(그 날 포함) 평일 수 — 휴장 판정의 「평일이 이만큼 지났나」."""
    d, t = _d(dd), _d(today)
    n = 0
    while d < t:
        d += timedelta(days=1)
        if d.isoweekday() < 6:
            n += 1
    return n


def _num(v: Any) -> int | None:
    """KRX 숫자 문자열(쉼표 포함)을 정수로. 비었거나 '-' 면 None."""
    try:
        return int(str(v).replace(",", "")) if v not in (None, "", "-") else None
    except Exception:
        return None


def daily_records(price_dd: str, kospi: list, kosdaq: list) -> list[tuple]:
    """KRX 응답(코스피·코스닥)을 일별 행으로. 칸 순서는 `DAILY_COLUMNS`.

    market 은 endpoint 기준으로 태깅한다(`krx_weekly` 와 같은 방식 — 읽는 쪽은 KS/KQ).
    종목코드(`ISU_CD`)가 없는 행은 버린다. 같은 종목이 두 번 오면 쓰는 쪽(`write_daily`)에서 마지막 것이 이긴다."""
    recs: list[tuple] = []
    for market, rows in ((MKT_KS, kospi), (MKT_KQ, kosdaq)):
        for row in rows or ():
            isu = row.get("ISU_CD")
            if not isu:
                continue
            recs.append((price_dd, isu, market,
                         _num(row.get("TDD_OPNPRC")), _num(row.get("TDD_HGPRC")), _num(row.get("TDD_LWPRC")),
                         _num(row.get("TDD_CLSPRC")), _num(row.get("ACC_TRDVOL")), _num(row.get("ACC_TRDVAL")),
                         _num(row.get("LIST_SHRS")), _num(row.get("CMPPREVDD_PRC"))))
    return recs


# ── 쓰기 (한 거래일 → 번호표·달력·사실값, 주 닫는 날, 보관 창 정리) ──────────────────

#: 새 종목만 번호를 받는다 — `ON CONFLICT DO NOTHING` 만 쓰면 이미 있는 종목마다 번호 생성기 값을 태운다(하루 약 2.8천 개).
#: 뒤의 ON CONFLICT 는 두 쓰는 쪽이 같은 새 종목을 동시에 넣는 드문 경우만 막는다.
NEW_TICKERS_SQL = ("INSERT INTO krx_ticker (ticker) SELECT u.t FROM unnest(%s::text[]) AS u(t) "
                   "WHERE NOT EXISTS (SELECT 1 FROM krx_ticker k WHERE k.ticker = u.t) ORDER BY u.t "
                   "ON CONFLICT (ticker) DO NOTHING")
TIDS_SQL = "SELECT ticker, tid FROM krx_ticker WHERE ticker = ANY(%s)"
#: 그 날을 거래일로 적는다(휴장으로 적혀 있었어도 행이 왔으면 거래일이다).
DAY_UPSERT_SQL = ("INSERT INTO krx_day (trd_dd, dd, n, closed) VALUES (%s, %s, %s, false) "
                  "ON CONFLICT (trd_dd) DO UPDATE SET n = EXCLUDED.n, closed = false")
#: 같은 날을 다시 쓰면(드묾 — 갱신기는 없는 날만 쓴다) 그 날 행을 통째로 바꾼다.
DAY_ROWS_DELETE_SQL = "DELETE FROM krx_px WHERE trd_dd = %s"
#: 한 거래일 전종목을 **한 문장**으로 넣는다 — 칸마다 배열 하나(`PX_COLUMNS` 순서, 종목 번호순으로 정렬해 넘긴다)를 `unnest` 가 행으로 펼치고,
#: WITH ORDINALITY 순서대로 넣는다. 같은 날 행이 물리적으로 모여 BRIN(날짜 범위 요약 색인)이 촘촘하다.
#: 🔴 COPY 를 쓰지 않는다 — RLS(행 단위 접근 제어)가 켜진 표에는 표 소유자·BYPASSRLS 가 아닌 역할(최소 권한 서버 계정)이
#:    `COPY FROM` 을 못 한다(PG17 `COPY FROM not supported with row-level security`, 261019 로컬 실측). INSERT 는 RLS 정책을 그대로 따른다.
PX_COLUMNS = ("value", "volume", "list_shrs", "open", "high", "low", "close", "cmp", "tid", "market")
INSERT_PX_SQL = (
    "INSERT INTO krx_px (value, volume, list_shrs, open, high, low, close, cmp, trd_dd, tid, market) "
    "SELECT u.value, u.volume, u.list_shrs, u.open, u.high, u.low, u.close, u.cmp, %s::date, u.tid, u.market::\"char\" "
    "FROM unnest(%s::bigint[], %s::bigint[], %s::bigint[], %s::integer[], %s::integer[], %s::integer[], %s::integer[], "
    "%s::integer[], %s::integer[], %s::text[]) "
    "WITH ORDINALITY AS u(value, volume, list_shrs, open, high, low, close, cmp, tid, market, i) ORDER BY u.i")
#: 휴장 기록 — 행이 있는 날(거래일)은 절대 덮지 않는다.
CLOSED_DAY_SQL = ("INSERT INTO krx_day (trd_dd, dd, n, closed) VALUES (%s, %s, 0, true) "
                  "ON CONFLICT (trd_dd) DO NOTHING")

#: 주 닫는 날 다시 계산(멱등). 한 주 = ISO 주 ∩ 달력 연도(`krx_retention.week_unit`). 그 주에서 보관된 가장 늦은 거래일이
#: 그 주의 마지막 평일 이상이거나(R1), 더 늦은 주가 보관돼 있으면(R2) 닫힌 주다(`plan_weeks` 와 같은 규칙 — 시험이 대조).
WK_CLOSE_SQL = """\
WITH td AS (
  SELECT trd_dd, (trd_dd - (extract(isodow FROM trd_dd)::int - 1)) AS mon, extract(year FROM trd_dd)::int AS y
  FROM krx_day WHERE NOT closed AND n > 0
), wk AS (
  SELECT mon, y, max(trd_dd) AS last, least(mon + 6, make_date(y, 12, 31)) AS wend FROM td GROUP BY mon, y
), wk2 AS (
  SELECT last, (wend - CASE extract(isodow FROM wend)::int WHEN 6 THEN 1 WHEN 7 THEN 2 ELSE 0 END) AS last_wd,
         last = max(last) OVER () AS newest
  FROM wk
), want AS (
  SELECT last AS trd_dd FROM wk2 WHERE last >= last_wd OR NOT newest
)
UPDATE krx_day d SET wk_close = (d.trd_dd IN (SELECT trd_dd FROM want))
WHERE d.wk_close IS DISTINCT FROM (d.trd_dd IN (SELECT trd_dd FROM want))
"""

#: 보관 창 밖(첫날보다 이른) 달력 날짜들.
PRUNE_CANDIDATES_SQL = "SELECT dd, n, closed, cold FROM krx_day WHERE trd_dd < %s ORDER BY trd_dd"
PRUNE_PX_SQL = "DELETE FROM krx_px WHERE trd_dd = ANY(%s)"
PRUNE_DAY_SQL = "DELETE FROM krx_day WHERE trd_dd = ANY(%s)"
#: 지울 날의 행이 있던 BRIN(날짜 범위 요약 색인) 블록 범위의 요약을 **지우기 직전에** 지운다. 지운 빈 자리는 기록 뒤 VACUUM 으로
#: 곧 다음 날 행이 다시 쓰는데, BRIN 요약은 줄지 않고 합쳐져 그 범위가 [지운 날, 새 날]로 넓어진다 — 과거일 단면이 재사용된 범위를
#: 모두 읽게 된다(10-18 실측, 운영과 같은 적재 뒤 80일 롤링: 가장 오래된 날 단면 lossy 36 → 2,177 블록, 1년이면 표 전체).
#: 요약을 지운 범위는 다음 VACUUM 이 남은 행으로 다시 요약한다(같은 실측에서 lossy 약 50 유지). 범위 크기는 색인의 pages_per_range
#: (`scripts/krx_daily_ddl.py` 의 16)와 같아야 한다. 못 부르면 경고만 하고 지운다.
#: 🔴 `brin_desummarize_range` 는 **색인 소유자만** 부를 수 있다 — PG17 의 MAINTAIN 권한으로도 `must be owner of index`(261019 로컬 PG17 실측).
#:    그래서 소유자가 만든 SECURITY DEFINER 함수 `krx_px_brin_desummarize(date[])`(`scripts/krx_daily_ddl.py` — 같은 문장을 소유자 권한으로)를
#:    거친다. 함수가 없는 DB(DDL 적용 전)에서는 종전처럼 직접 부른다(소유자 계정에서만 된다).
BRIN_INDEX = "krx_px_dd_brin"
BRIN_PAGES_PER_RANGE = 16
PRUNE_DESUMMARIZE_SQL = (
    "SELECT r.b, brin_desummarize_range(%s::regclass, r.b) FROM ("
    "SELECT DISTINCT ((p.ctid::text::point)[0]::bigint / %s) * %s AS b FROM krx_px p WHERE p.trd_dd = ANY(%s)) r")
PRUNE_DESUMMARIZE_FN = "krx_px_brin_desummarize"
PRUNE_DESUMMARIZE_FN_EXISTS_SQL = f"SELECT to_regprocedure('public.{PRUNE_DESUMMARIZE_FN}(date[])') IS NOT NULL"
PRUNE_DESUMMARIZE_FN_SQL = f"SELECT public.{PRUNE_DESUMMARIZE_FN}(%s::date[])"


@dataclass(frozen=True)
class Pruned:
    """보관 창 정리 결과. held = 창 밖인데 콜드 보관본에 들어가지 않아 남긴 날(YYYYMMDD)."""
    rows: int = 0
    days: tuple[str, ...] = ()
    held: tuple[str, ...] = ()


def _desummarize(conn: Any, days: list[date]) -> int:
    """지울 날(`days`)의 행이 있는 BRIN 블록 범위 요약을 지운다 → 지운 범위 수. 소유자 함수(`PRUNE_DESUMMARIZE_FN`)가 있으면 그것으로,
    없으면 직접(`PRUNE_DESUMMARIZE_SQL`). 실패해도(권한 등) 보관 창 정리는 계속한다 — 요약이 넓어지는 것은 과거일 단면이 느려질 뿐
    값은 같다. 자기 SAVEPOINT 안에서 돈다."""
    try:
        with conn.transaction():
            if conn.execute(PRUNE_DESUMMARIZE_FN_EXISTS_SQL).fetchone()[0]:
                return int(conn.execute(PRUNE_DESUMMARIZE_FN_SQL, (days,)).fetchone()[0] or 0)
            return len(conn.execute(PRUNE_DESUMMARIZE_SQL, (BRIN_INDEX, BRIN_PAGES_PER_RANGE, BRIN_PAGES_PER_RANGE, days)).fetchall())
    except Exception as exc:                         # noqa: BLE001 — 성능 장치일 뿐이다
        logger.warning("일별 시세 보관 창 정리: BRIN 요약 지우기 실패(지우기는 계속 — 과거일 단면이 느려질 수 있다) %s: %s",
                       type(exc).__name__, exc)
        return 0


def prune_window(conn: Any) -> Pruned:
    """보관 창(`window_lo`) 밖의 날을 지운다 — 휴장일·행 없는 날·**콜드 보관본에 들어간 날(`cold`)만**.

    `conn` 은 트랜잭션 안의 psycopg 연결이다(예외는 호출자의 SAVEPOINT 가 받는다). 콜드에 없는 날은 지우지 않고 WARNING 을 남긴다 —
    보관본 연도 파일을 올린 쪽이 `krx_day.cold` 를 켜면 다음 기록 때 지운다. 행이 있는 날을 지우기 직전에 그 행들의 BRIN 블록 범위 요약을
    지운다(`PRUNE_DESUMMARIZE_SQL` — 빈 자리를 다음 날 행이 다시 써도 요약이 넓어지지 않게)."""
    latest = conn.execute(LATEST_DAY_SQL).fetchone()[0]
    if not latest:
        return Pruned()
    lo = window_lo(latest)
    cands = conn.execute(PRUNE_CANDIDATES_SQL, (_d(lo),)).fetchall()
    go = [dd for dd, n, closed, cold in cands if closed or not n or cold]
    held = tuple(dd for dd, n, closed, cold in cands if not (closed or not n or cold))
    rows = 0
    if go:
        days = [_d(x) for x in go]
        with_rows = [_d(dd) for dd, n, closed, cold in cands if dd in go and n]
        if with_rows:
            _desummarize(conn, with_rows)
        rows = _rowcount(conn.execute(PRUNE_PX_SQL, (days,)))
        conn.execute(PRUNE_DAY_SQL, (days,))
    if held:
        years = sorted({x[:4] for x in held})
        logger.warning("일별 시세 보관 창(최신 %s → 첫날 %s) 밖 %d일(%s~%s)이 콜드 보관본에 없어 지우지 않았다 — "
                       "%s년 보관본을 올린 뒤 krx_day.cold 를 켜면 다음 기록 때 지운다",
                       latest, lo, len(held), held[0], held[-1], "·".join(years))
    return Pruned(rows, tuple(go), held)


def write_daily(conn: Any, price_dd: str, kospi: list, kosdaq: list) -> tuple[bool, int, int]:
    """한 거래일 전종목을 쓴다 — 새 종목 번호 → 달력(거래일) → 사실값(종목순 한 문장 INSERT) → 주 닫는 날 → 보관 창 정리.

    `conn` 은 **이미 트랜잭션 안에 있는** psycopg 연결이다. 이 함수는 중첩 트랜잭션(SAVEPOINT) 안에서 일하고, 실패해도 예외를
    밖으로 내지 않는다 — 실패는 로그(ERROR)와 반환값으로 알린다. 261018(재설계 5-1 쓰는 쪽)부터 이 저장분이 시세 기록의 주(主)라
    호출자(`price_multiple_data._krx_db_upsert`)는 실패하면 아무것도 커밋하지 않는다.

    반환 (성공 여부, 넣은 행 수, 보관 창 정리로 지운 행 수)."""
    recs = daily_records(price_dd, kospi, kosdaq)
    if not recs:
        return False, 0, 0
    by: dict[str, tuple] = {}
    for r in recs:                                   # 같은 종목이 두 번 오면 마지막 것(종전 upsert 와 같은 뜻)
        by[r[1]] = r
    day = _d(price_dd)
    try:
        with conn.transaction():                     # 이미 트랜잭션 안이면 SAVEPOINT
            with conn.cursor() as cur:
                cur.execute(NEW_TICKERS_SQL, (sorted(by),))
                tids = dict(cur.execute(TIDS_SQL, (list(by),)).fetchall())
                cur.execute(DAY_UPSERT_SQL, (day, price_dd, len(by)))
                cur.execute(DAY_ROWS_DELETE_SQL, (day,))
                # PX_COLUMNS 순서(value, volume, list_shrs, open, high, low, close, cmp, tid, market) — 종목 번호순
                rows = [(r[8], r[7], r[9], r[3], r[4], r[5], r[6], r[10], tid, _MKT_CHAR[r[2]])
                        for tid, r in sorted((tids[t], r) for t, r in by.items())]
                cur.execute(INSERT_PX_SQL, (day, *(list(col) for col in zip(*rows))))
                cur.execute(WK_CLOSE_SQL)
                pruned = prune_window(conn)              # 연결로 — BRIN 요약 지우기가 자기 SAVEPOINT 를 건다
        return True, len(by), pruned.rows
    except Exception as exc:                         # noqa: BLE001 — 호출자가 반환값으로 가른다
        logger.error("krx_daily 기록 실패 — 스키마(scripts/krx_daily_ddl.py) 적용 여부·권한 확인: %s: %s",
                     type(exc).__name__, exc)
        return False, 0, 0


def record_closed(conn: Any, price_dd: str) -> bool:
    """그 날을 휴장으로 적는다(이미 달력에 있으면 — 거래일이든 휴장이든 — 그대로). 적었으면 True.
    판정은 호출자가 `closed_verdict` 로 한다. 주 닫는 날도 다시 계산한다(금요일 휴장이면 그 주가 목요일로 닫힌다)."""
    with conn.transaction():
        cur = conn.execute(CLOSED_DAY_SQL, (_d(price_dd), price_dd))
        wrote = _rowcount(cur) > 0
        if wrote:
            conn.execute(WK_CLOSE_SQL)
    return wrote


# ── 휴장 판정 (빈 응답 한 번으로 정하지 않는다) ─────────────────────────────────

#: 그 날 뒤로 이만큼 평일이 지나야 「둘 다 빈 응답」을 휴장 근거로 쓴다(KRX 는 T+1 아침 게시 — 게시 전 빈 응답을 거르기).
CLOSED_MIN_WEEKDAYS = 2
#: 기준가 연속성 판정에 필요한 최소 비교 종목 수와 문턱. 휴장이면 다음 거래일 기준가 = 그 앞 거래일 종가(어긋나는 것은 그날 기준가를
#: 조정한 몇 종목뿐 — 1년치 실측 하루 최대 66건), 거래일을 빠뜨렸으면 거의 모든 종목이 어긋난다(20260703 실측 2,546종목).
CONTINUITY_MIN_N = 100
CONTINUITY_CLOSED_MAX = 0.05
CONTINUITY_TRADING_MIN = 0.5

#: 그 날의 앞·뒤 저장 거래일(d0·d2)과, 두 날 모두에 있는 종목 중 「d2 기준가(종가 − 공표 대비) ≠ d0 종가」인 종목 수.
#: 문턱은 스윕(`scripts/krx_base_resets.py`)의 기준가 리셋 판정과 같다(차이 > max(종가 × 0.1%, 1원)).
CONTINUITY_SQL = """\
WITH b AS (SELECT (SELECT max(trd_dd) FROM krx_day WHERE n > 0 AND trd_dd < %(d)s) AS d0,
                  (SELECT min(trd_dd) FROM krx_day WHERE n > 0 AND trd_dd > %(d)s) AS d2)
SELECT count(*) AS n,
       count(*) FILTER (WHERE abs((p2.close - p2.cmp) - p0.close) > greatest(p0.close * 0.001, 1)) AS bad
FROM b
JOIN krx_px p2 ON p2.trd_dd = b.d2 AND p2.cmp IS NOT NULL AND p2.close > 0
JOIN krx_px p0 ON p0.trd_dd = b.d0 AND p0.tid = p2.tid AND p0.close > 0
"""


def continuity(conn: Any, price_dd: str) -> tuple[int, int] | None:
    """(비교 종목 수, 어긋난 종목 수) — 앞·뒤 저장 거래일이 없거나 공표 대비가 비어 비교할 수 없으면 None."""
    n, bad = conn.execute(CONTINUITY_SQL, {"d": _d(price_dd)}).fetchone()
    return (int(n), int(bad)) if n else None


def closed_verdict(kospi_empty: bool, kosdaq_empty: bool, weekdays_since: int,
                   cont: tuple[int, int] | None) -> str:
    """빈 응답을 받은 날의 판정 — "closed"(휴장으로 적는다) · "trading"(거래일인데 KRX 가 비워 줬다 — 적지 않는다) · "unknown"(아직 모른다).

    · 한 시장이라도 행이 있으면 거래일이다.
    · 둘 다 비어도 그 날 뒤로 평일이 `CLOSED_MIN_WEEKDAYS` 일 지나기 전에는 모른다(게시 전 빈 응답).
    · 다음 저장 거래일로 기준가 연속성을 볼 수 있으면(`cont`, 비교 `CONTINUITY_MIN_N` 종목 이상) 그것으로 정한다 —
      어긋남 ≤ 5% 면 휴장, ≥ 50% 면 거래일(KRX 일시 빈 응답), 그 사이는 모른다.
    · 볼 수 없으면(다음 거래일이 아직 없음·공표 대비 없음·비교 종목이 너무 적음) **모른다** — 다음 거래일이 저장된 뒤 다시 판정한다.
      KRX 오류(키 만료·한도·장애)도 빈 응답으로 온다. 「두 시장 빈 응답 + 평일 경과」만으로 휴장을 적으면 하루 넘게 KRX 가 막힌
      거래일이 휴장으로 굳는다 — 달력의 휴장은 다시 묻지 않으므로 그 날은 영영 메워지지 않고, 금요일이면 그 주가 목요일로 닫힌다."""
    if not (kospi_empty and kosdaq_empty):
        return "trading"
    if weekdays_since < CLOSED_MIN_WEEKDAYS:
        return "unknown"
    if cont and cont[0] >= CONTINUITY_MIN_N:
        ratio = cont[1] / cont[0]
        if ratio <= CONTINUITY_CLOSED_MAX:
            return "closed"
        if ratio >= CONTINUITY_TRADING_MIN:
            return "trading"
    return "unknown"


# ── 갱신기가 읽는 달력 상태 ─────────────────────────────────────────────────

#: 한 왕복으로 (저장된 최신 거래일, 메워야 할 평일들 — 최근 날부터). 메울 범위는 [max(보관 창 첫날, 달력 첫날), exp] —
#: 저장분 **사이**와 그 뒤만 메운다(빈 DB 가 1년치를 스스로 받아 오지 않는다 — 과거 백필은 따로 한다).
#: 보관 창 첫날 = 최신 − 1년 − 7일(`window_lo` 와 같은 규칙: PostgreSQL 의 date − interval '1 year' 도 2월 29일 → 2월 28일).
MISSING_SQL = """\
WITH b AS (SELECT max(trd_dd) FILTER (WHERE n > 0) AS latest, min(trd_dd) AS first FROM krx_day)
SELECT to_char(b.latest, 'YYYYMMDD'),
       ARRAY(SELECT to_char(g::date, 'YYYYMMDD')
             FROM generate_series(COALESCE(GREATEST(b.first, (b.latest - interval '1 year' - interval '7 days')::date),
                                           %(exp)s::date),
                                  %(exp)s::date, interval '1 day') g
             WHERE extract(isodow FROM g) < 6
               AND NOT EXISTS (SELECT 1 FROM krx_day d WHERE d.trd_dd = g::date)
             ORDER BY g DESC)
FROM b
"""


def missing_days(read_rows: Callable[..., Any], exp: str) -> tuple[str | None, list[str]] | None:
    """(저장된 최신 거래일, 달력에 없는 평일 — 최근 날부터). 못 읽으면 None. `read_rows` 는 `db.pg_rows` 계약."""
    rows = read_rows(MISSING_SQL, {"exp": _d(exp)})
    if rows is None:
        return None
    if not rows:
        return None, [exp]
    latest, missing = rows[0]
    return latest, list(missing or [])


# ── 주간 닫기 (krx_weekly 는 닫힌 주만) ──────────────────────────────────────────

#: 닫는 값 — 그 날의 일별 행을 그대로 옮긴다. 시총 = 종가 × 상장주식수(뷰 `krx_daily_v` 와 같은 계산.
#: 실측 20261016: KRX 응답의 MKTCAP 을 그대로 쓰던 종전 krx_weekly 값과 2,762종목 차이 0).
CLOSE_WEEK_SQL = (
    "INSERT INTO krx_weekly (price_dd, ticker, market, close, mktcap, list_shrs) "
    "SELECT trd_dd, ticker, market, close, close * list_shrs, list_shrs FROM krx_daily WHERE trd_dd = %s "
    "ON CONFLICT (price_dd, ticker) DO NOTHING"
)
#: 한 주 안에서 그 날보다 이른 날의 행 — 더 늦은 날로 다시 닫을 때, 그리고 종전 쓰기가 남긴 이번 주 행을 치울 때 지운다.
DROP_EARLIER_IN_WEEK_SQL = "DELETE FROM krx_weekly WHERE price_dd >= %s AND price_dd < %s"
#: 한 주 안에 이미 있는 가장 늦은 날. 기본키 (price_dd, ticker) 범위 스캔.
WEEK_MAX_SQL = "SELECT max(price_dd) FROM krx_weekly WHERE price_dd >= %s AND price_dd <= %s"
#: 주간 닫기가 보는 날 — 저장된 최신 거래일에서 이만큼(달력 일) 안의 거래일. 보관 창이 1년이 되면서(261019) 매 기록마다
#: 1년치 주를 하나씩 확인하지 않게 줄였다 — 닫기를 놓쳐도(장애) 이 기간 안에서는 다음 기록이 메운다(종전 7거래일 창보다 넓다).
SYNC_LOOKBACK_DAYS = 35
SYNC_DAYS_SQL = ("SELECT dd FROM krx_day WHERE n > 0 AND trd_dd >= "
                 "(SELECT max(trd_dd) FROM krx_day WHERE n > 0) - %s ORDER BY trd_dd")

#: `weekly_with_latest` 가 일별 행을 주간 칸 이름으로 옮기는 법. 시총은 저장하지 않으므로 종가 × 상장주식수(뷰 `krx_daily_v` 와 같다).
_DAILY_AS_WEEKLY = {"price_dd": "d.trd_dd", "ticker": "d.ticker", "market": "d.market", "close": "d.close",
                    "mktcap": "d.close * d.list_shrs", "list_shrs": "d.list_shrs"}


def weekly_with_latest(columns: str = "price_dd, ticker, market, close, mktcap, list_shrs") -> str:
    """「닫힌 주 + 최신 거래일」 원천(괄호 친 서브쿼리 SQL) — `FROM {weekly_with_latest()} w` 로 쓴다.

    쓰는 쪽이 닫힌 주만 `krx_weekly` 에 쓰기 전(~261018)의 그 표와 같은 모양이다 — 주마다 그 주 마지막 거래일, 이번 주는 최신 거래일.
    이번 주의 최신값을 「최신 점」으로 쓰던 배치(시총 집계·상장 관측·딱지·선행 배수 시장 구분·수정계수 직전 시장 등)가 표 이름 대신 읽는다.
    일별 최신일이 `krx_weekly` 의 최신일보다 늦을 때만 그 하루를 덧붙인다(금요일이 닫힌 뒤에는 덧붙일 것이 없다).
    최신일은 달력(`krx_day`)에서 읽는다(261019 — 호환 뷰 위의 max(trd_dd)는 조인을 다 푼다).
    `columns` 는 `krx_weekly` 칸 이름의 부분집합(쉼표 구분) — 필요한 칸만 읽게 한다."""
    cols = [c.strip() for c in columns.split(",") if c.strip()]
    unknown = [c for c in cols if c not in _DAILY_AS_WEEKLY]
    if unknown:
        raise ValueError(f"krx_weekly 칸이 아니다: {unknown}")
    daily = ", ".join(f"{_DAILY_AS_WEEKLY[c]} AS {c}" for c in cols)
    return (f"(SELECT {', '.join(cols)} FROM krx_weekly "
            "UNION ALL "
            f"SELECT {daily} FROM krx_daily d "
            "WHERE d.trd_dd = (SELECT max(dd) FROM krx_day WHERE n > 0) "
            "AND d.trd_dd > (SELECT COALESCE(max(price_dd), '') FROM krx_weekly))")


#: 전 칸 판 — `FROM {WEEKLY_WITH_LATEST} w`.
WEEKLY_WITH_LATEST = weekly_with_latest()


@dataclass(frozen=True)
class WeekPlan:
    """일별 저장분에 보이는 한 주. start~end 는 `week_unit`, last 는 그 주에서 저장된 가장 늦은 거래일."""
    start: str
    end: str
    last: str
    closed: bool


def _last_weekday(end: str) -> str:
    """한 주(달력 연도로 자른 ISO 주)의 마지막 평일. 보통 금요일, 연말에 잘린 주는 12/31 이전 마지막 평일."""
    d = date(int(end[:4]), int(end[4:6]), int(end[6:]))
    while d.isoweekday() > 5:
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")


def plan_weeks(days: Iterable[str]) -> list[WeekPlan]:
    """일별 저장분의 거래일들 → 주별 계획(이른 주부터). 순수 함수.

    닫힌 주 = 그 주의 마지막 평일이 저장됐거나(R1 — 보통 금요일, 토요일 아침 기록 때 닫힌다),
              더 늦은 주의 거래일이 저장된 주(R2 — 금요일 휴장·누락이면 다음 주 첫 기록 때 그 주의 가장 늦은 날로 닫힌다).
    가장 늦은 주가 아직 안 닫혔으면 그 주가 「이번 주」다."""
    by: dict[tuple[str, str], str] = {}
    for d in days:
        if d:
            u = week_unit(d)
            by[u] = max(by.get(u, ""), d)
    if not by:
        return []
    newest = max(by)
    return [WeekPlan(s, e, last, last >= _last_weekday(e) or (s, e) != newest)
            for (s, e), last in sorted(by.items())]


@dataclass(frozen=True)
class WeeklySync:
    """`sync_weekly` 결과. ok=False 면 SAVEPOINT 가 되돌려져 krx_weekly 는 그대로다(일별 기록은 영향 없음)."""
    ok: bool
    closed: tuple[str, ...] = ()     # 이번에 krx_weekly 에 닫아 넣은 거래일들
    dropped: int = 0                 # 지운 krx_weekly 행(더 늦은 날로 다시 닫기 · 종전 쓰기가 남긴 이번 주 행)


def sync_weekly(conn: Any) -> WeeklySync:
    """일별 저장분에 보이는 (최근 `SYNC_LOOKBACK_DAYS` 일의) 주들을 `krx_weekly` 에 맞춘다 — 닫힌 주는 한 번 옮겨 적고, 이번 주는 옮기지 않는다.

    · 닫힌 주: `krx_weekly` 에 그 주의 같거나 더 늦은 날이 이미 있으면 그대로(R3 — 더 늦은 날로만 바꾼다, 백필이 넣은 금요일을
      일별 저장분의 목요일로 덮지 않는다). 없으면 그 주의 더 이른 날 행을 지우고 가장 늦은 날을 옮겨 적는다.
    · 이번 주: 그 주의 최신일보다 이른 날의 `krx_weekly` 행만 지운다 — 종전 쓰기(매일 이번 주 행을 다시 넣던 것)가 남긴 행이다.
      평소에는 지울 것이 없다(0행 — 죽은 행이 생기지 않는다). 최신일과 같거나 늦은 행은 건드리지 않는다.
    매 기록마다 다시 돌아도 같은 결과다(멱등). 닫기를 한 번 놓쳐도(장애) 그 주가 이 기간 안에 있는 동안 다음 호출이 메운다.
    보는 기간의 첫 주가 잘려도 그 주의 가장 늦은 거래일은 기간 안에 있다(기간은 앞쪽만 자른다).

    `conn` 은 이미 트랜잭션 안의 psycopg 연결(또는 autocommit 연결)이다. SAVEPOINT 안에서 일하고 예외를 밖으로 내지 않는다 —
    주간 닫기는 부(副)라, 실패해도 같은 트랜잭션의 일별 기록은 커밋돼야 한다. 실패는 ERROR 로그와 `ok=False` 로 알린다."""
    try:
        with conn.transaction():
            days = [r[0] for r in conn.execute(SYNC_DAYS_SQL, (SYNC_LOOKBACK_DAYS,)).fetchall()]
            closed: list[str] = []
            dropped = 0
            for w in plan_weeks(days):
                have = conn.execute(WEEK_MAX_SQL, (w.start, w.end)).fetchone()[0]
                if w.closed:
                    if have is not None and have >= w.last:
                        continue
                    dropped += _rowcount(conn.execute(DROP_EARLIER_IN_WEEK_SQL, (w.start, w.last)))
                    conn.execute(CLOSE_WEEK_SQL, (w.last,))
                    closed.append(w.last)
                elif have is not None and have < w.last:
                    dropped += _rowcount(conn.execute(DROP_EARLIER_IN_WEEK_SQL, (w.start, w.last)))
        if closed or dropped:
            logger.info("krx_weekly 주간 닫기: 닫음 %s · 지움 %d행", ",".join(closed) or "-", dropped)
        return WeeklySync(True, tuple(closed), dropped)
    except Exception as exc:                         # noqa: BLE001 — 일별 기록을 막지 않는다
        logger.error("krx_weekly 주간 닫기 실패(일별 기록은 계속 — 다음 기록 때 다시 닫는다): %s: %s",
                     type(exc).__name__, exc)
        return WeeklySync(False)


def _rowcount(cur: Any) -> int:
    n = getattr(cur, "rowcount", 0)
    return n if n and n > 0 else 0


# ── 조회 ────────────────────────────────────────────────────────────────────

#: 한 번의 왕복으로 「보관 창(lo~hi)·그 날이 보관돼 있나·그 종목의 그 날 행」을 모두 받는다.
#: 첫 `%s` = as_of(빈 문자열이면 보관된 최신일), 둘째 `%s` = 종목코드.
#: 창·보관 여부는 달력(`krx_day`)에서 읽는다(261019 — 호환 뷰 위의 min/max·EXISTS 는 1년치 조인을 다 풀어 약 0.3초 걸렸다).
#: 그 종목의 그 날 행은 `krx_daily_v` 에서 — ticker·trd_dd 조건이 표의 기본키까지 내려간다.
QUOTE_SQL = (
    "WITH win AS (SELECT min(dd) AS lo, max(dd) AS hi FROM krx_day WHERE n > 0), "
    "d AS (SELECT win.lo, win.hi, COALESCE(NULLIF(%s, ''), win.hi) AS dd FROM win) "
    "SELECT d.dd, d.lo, d.hi, "
    "EXISTS (SELECT 1 FROM krx_day x WHERE x.dd = d.dd AND x.n > 0) AS present, "
    "v.market, v.open, v.high, v.low, v.close, v.volume, v.value, v.list_shrs, v.mktcap, "
    "v.prev_dd, v.prev_close, v.chg, v.chg_pct, v.krx_chg, v.krx_chg_pct "
    "FROM d LEFT JOIN LATERAL ("
    "SELECT * FROM krx_daily_v WHERE ticker = %s AND trd_dd = d.dd) v ON true"
)
_QUOTE_COLS = ("dd", "lo", "hi", "present", "market", "open", "high", "low", "close", "volume", "value",
               "list_shrs", "mktcap", "prev_dd", "prev_close", "chg", "chg_pct", "krx_chg", "krx_chg_pct")


@dataclass(frozen=True)
class QuoteLookup:
    """`read_quote` 의 결과.

    ok            DB 를 읽었나(미설정·접속 실패·표 없음이면 False → 호출자는 KRX 라이브로 간다)
    day           조회한 거래일(as_of 가 비면 보관된 최신일). 표가 비면 None
    window        (가장 이른 보관일, 가장 늦은 보관일) — 표가 비면 (None, None)
    day_present   그 날이 보관돼 있나(그 날 행이 하나라도 있나). False 면 휴장일·보관 창 밖·구멍 중 하나다
    row           그 종목의 그 날 행(없으면 None). 키는 `_QUOTE_COLS` 중 market 이후
    """
    ok: bool
    day: str | None = None
    window: tuple[str | None, str | None] = (None, None)
    day_present: bool = False
    row: dict | None = None


def read_quote(ticker: str, as_of: str = "") -> QuoteLookup:
    """종목 한 줄 + 그 날의 보관 여부를 **DB 한 왕복**으로 읽는다. 읽을 수 없으면 `QuoteLookup(ok=False)`."""
    from open_proxy_mcp.db import pg_rows
    rows = pg_rows(QUOTE_SQL, ((as_of or "").strip(), ticker))
    if not rows:                                     # None = DB 미설정·장애·표 없음 / [] 는 이 질의로는 나올 수 없다
        return QuoteLookup(ok=False)
    r = dict(zip(_QUOTE_COLS, rows[0]))
    cols = _QUOTE_COLS[4:]                           # LEFT JOIN 이 맞는 행을 못 찾으면 이 열이 전부 NULL
    row = {k: r[k] for k in cols} if any(r[k] is not None for k in cols) else None
    return QuoteLookup(ok=True, day=r["dd"], window=(r["lo"], r["hi"]), day_present=bool(r["present"]), row=row)


#: 서빙 기준일 — 일별 시세 저장분에 보관된 가장 늦은 거래일. 달력(`krx_day`, 수백 행)에서 읽는다(261019).
LATEST_DAY_SQL = "SELECT max(dd) FROM krx_day WHERE n > 0"


def latest_day(read_rows: Callable[..., Any], on_error: Any = None) -> Any:
    """서빙 기준일(YYYYMMDD) — 일별 저장분의 가장 늦은 거래일. 비었으면 None, 못 읽으면 `on_error`(기본 None).

    261018: 유니버스·시총 단면의 기준일(`services/screener._krx_latest_dd`)과 종목 최신가의 기준일
    (`services/price_multiple_data._krx_daily_latest_dd`)이 같은 질의를 따로 적고 있었다 — 정의는 여기 한 곳이고 둘은 위임한다.
    배치 갱신(`_ensure_krx_fresh`)도 여기로 모였다(재설계 5-1 쓰는 쪽).
    `read_rows` 는 **호출 모듈의 DB 읽기 함수**(`db.pg_rows` 계약: None = 미설정·장애, [] = 없음)다. 각 모듈의 시험이 그 모듈의
    DB 읽기(`screener.pg_rows`·`price_multiple_data._pg_rows`)를 바꿔 끼우므로, 어느 함수로 읽을지는 호출자가 정한다.
    비거나 장애여도 `krx_weekly` 로 대신하지 않는다 — 기준일과 행은 같은 원천에서 읽는다(호출자가 기존 강등 경로를 탄다)."""
    rows = read_rows(LATEST_DAY_SQL)
    if rows is None:
        return on_error
    return rows[0][0] if rows and rows[0][0] else None
