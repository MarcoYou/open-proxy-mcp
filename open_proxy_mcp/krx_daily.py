# -*- coding: utf-8 -*-
"""`krx_daily` — 최근 7거래일 일별 시세(KRX 응답 전종목·전 필드) 저장·조회.

261018 신설(Supabase 재설계 3단계). 배경: `trading_data(scope="quote")` 가 하루 약 400번 KRX 를 불러 전종목 응답을
버리고 한 줄만 캐시했고, 하루 첫 시세 질문은 KRX 갱신을 떠안아 2~12초를 기다렸다. KRX `stk_bydd_trd`·`ksq_bydd_trd`
응답에는 이미 시·고·저·종가, 거래량, 거래대금, 상장주식수가 들어 있어 **추가 호출 없이** 그대로 저장할 수 있다.

■ 저장하는 것(사실값만)
    trd_dd(YYYYMMDD) · ticker · market(KS/KQ) · open · high · low · close · volume · value(거래대금) · list_shrs
  시가총액(= close × list_shrs)과 등락률(전 거래일 종가 대비)은 **뷰** `krx_daily_v` 가 읽을 때 계산한다. 저장하지 않는다
  (한 행 안의 곱셈·나눗셈은 계산하는 것이 원칙 — 재설계 2-4).
■ 보관: 가장 최근 **서로 다른 거래일 7개**(롤링). 달력으로는 약 10일.
■ 스키마(표·뷰·권한)는 `scripts/krx_daily_ddl.py` 가 낸다 — 서버는 DDL 을 실행하지 않는다.
■ 주간 닫기(261018, 재설계 5-1 쓰는 쪽): 이 표가 시세 기록의 **주(主)** 다. `krx_weekly` 는 **닫힌 주만** 여기서 한 번 옮겨 적는다
  (`sync_weekly`). 종전에는 매 거래일 이번 주 `krx_weekly` 행을 지우고 다시 넣어 하루 약 2.2천 행의 죽은 행이 생겼다.
    · 한 주 = ISO 주를 달력 연도 안으로 자른 것(`krx_retention.week_unit`) — 연말 거래일이 다음 해 첫 주에 지워지지 않게.
    · 닫는 때: 그 주의 마지막 평일(보통 금요일)이 저장됐거나, 더 늦은 주의 거래일이 저장됐을 때(금요일 휴장·누락 대비).
    · 닫는 값: 그 주에서 일별 저장분에 있는 가장 늦은 거래일. 같은 주는 **더 늦은 날로만** 바꾼다(이미 같거나 늦은 날이 있으면 그대로).
  이번 주(아직 안 닫힌 주) 최신값은 일별 저장분에만 있다 — 「닫힌 주 + 최신 거래일」 모양이 필요한 배치는 `WEEKLY_WITH_LATEST` 를 읽는다.

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

#: 보관할 서로 다른 거래일 수. 바꾸려면 이 한 곳만.
KEEP_TRADING_DAYS = 7

#: 갱신 담당 하나만 돌게 하는 `pg_try_advisory_lock` 키(고정). ASCII "OPMKRXD1" — 다른 용도와 겹치지 않는 값.
REFRESH_LOCK_KEY = int.from_bytes(b"OPMKRXD1", "big")
assert 0 < REFRESH_LOCK_KEY < 2**63

DAILY_COLUMNS = ("trd_dd", "ticker", "market", "open", "high", "low", "close", "volume", "value", "list_shrs")

UPSERT_DAILY_SQL = (
    "INSERT INTO krx_daily (trd_dd, ticker, market, open, high, low, close, volume, value, list_shrs) "
    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
    "ON CONFLICT (trd_dd, ticker) DO UPDATE SET "
    "market=EXCLUDED.market, open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, "
    "close=EXCLUDED.close, volume=EXCLUDED.volume, value=EXCLUDED.value, list_shrs=EXCLUDED.list_shrs"
)

#: 가장 최근 N개 거래일보다 오래된 날을 지운다. 보관 일수가 N 미만이면 서브쿼리 최솟값이 가장 오래된 날이라 아무것도 안 지운다.
PRUNE_DAILY_SQL = (
    "DELETE FROM krx_daily WHERE trd_dd < ("
    "SELECT min(trd_dd) FROM (SELECT DISTINCT trd_dd FROM krx_daily ORDER BY trd_dd DESC LIMIT %s) k)"
)


def _num(v: Any) -> int | None:
    """KRX 숫자 문자열(쉼표 포함)을 정수로. 비었거나 '-' 면 None."""
    try:
        return int(str(v).replace(",", "")) if v not in (None, "", "-") else None
    except Exception:
        return None


def daily_records(price_dd: str, kospi: list, kosdaq: list) -> list[tuple]:
    """KRX 응답(코스피·코스닥)을 `krx_daily` 행으로. 열 순서는 `DAILY_COLUMNS` — 쓰는 쪽은 열 이름을 명시한다.

    market 은 endpoint 기준으로 태깅한다(`krx_weekly` 와 같은 방식 — DB 는 KS/KQ).
    종목코드(`ISU_CD`)가 없는 행은 버린다. 같은 종목이 두 번 오면 마지막 것이 이긴다(upsert).
    """
    recs: list[tuple] = []
    for market, rows in ((MKT_KS, kospi), (MKT_KQ, kosdaq)):
        for row in rows or ():
            isu = row.get("ISU_CD")
            if not isu:
                continue
            recs.append((price_dd, isu, market,
                         _num(row.get("TDD_OPNPRC")), _num(row.get("TDD_HGPRC")), _num(row.get("TDD_LWPRC")),
                         _num(row.get("TDD_CLSPRC")), _num(row.get("ACC_TRDVOL")), _num(row.get("ACC_TRDVAL")),
                         _num(row.get("LIST_SHRS"))))
    return recs


def write_daily(conn: Any, price_dd: str, kospi: list, kosdaq: list) -> tuple[bool, int, int]:
    """한 거래일 전종목을 `krx_daily` 에 넣고 7거래일보다 오래된 날을 지운다.

    `conn` 은 **이미 트랜잭션 안에 있는** psycopg 연결이다. 이 함수는 중첩 트랜잭션(SAVEPOINT) 안에서 일하고, 실패해도 예외를
    밖으로 내지 않는다 — 실패는 로그(ERROR)와 반환값으로 알린다. 261018(재설계 5-1 쓰는 쪽)부터 이 표가 시세 기록의 주(主)라
    호출자(`price_multiple_data._krx_db_upsert`)는 실패하면 아무것도 커밋하지 않는다.

    반환 (성공 여부, 넣은 행 수, 지운 행 수).
    """
    recs = daily_records(price_dd, kospi, kosdaq)
    if not recs:
        return False, 0, 0
    try:
        with conn.transaction():                     # 이미 트랜잭션 안이면 SAVEPOINT
            with conn.cursor() as cur:
                cur.executemany(UPSERT_DAILY_SQL, recs)
                cur.execute(PRUNE_DAILY_SQL, (KEEP_TRADING_DAYS,))
                pruned = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        return True, len(recs), pruned
    except Exception as exc:                         # noqa: BLE001 — 호출자가 반환값으로 가른다
        logger.error("krx_daily 기록 실패 — 마이그레이션 적용 여부·권한 확인: %s: %s", type(exc).__name__, exc)
        return False, 0, 0


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

#: `weekly_with_latest` 가 일별 행을 주간 칸 이름으로 옮기는 법. 시총은 저장하지 않으므로 종가 × 상장주식수(뷰 `krx_daily_v` 와 같다).
_DAILY_AS_WEEKLY = {"price_dd": "d.trd_dd", "ticker": "d.ticker", "market": "d.market", "close": "d.close",
                    "mktcap": "d.close * d.list_shrs", "list_shrs": "d.list_shrs"}


def weekly_with_latest(columns: str = "price_dd, ticker, market, close, mktcap, list_shrs") -> str:
    """「닫힌 주 + 최신 거래일」 원천(괄호 친 서브쿼리 SQL) — `FROM {weekly_with_latest()} w` 로 쓴다.

    쓰는 쪽이 닫힌 주만 `krx_weekly` 에 쓰기 전(~261018)의 그 표와 같은 모양이다 — 주마다 그 주 마지막 거래일, 이번 주는 최신 거래일.
    이번 주의 최신값을 「최신 점」으로 쓰던 배치(시총 집계·상장 관측·딱지·선행 배수 시장 구분·수정계수 직전 시장 등)가 표 이름 대신 읽는다.
    일별 최신일이 `krx_weekly` 의 최신일보다 늦을 때만 그 하루를 덧붙인다(금요일이 닫힌 뒤에는 덧붙일 것이 없다).
    `columns` 는 `krx_weekly` 칸 이름의 부분집합(쉼표 구분) — 필요한 칸만 읽게 한다."""
    cols = [c.strip() for c in columns.split(",") if c.strip()]
    unknown = [c for c in cols if c not in _DAILY_AS_WEEKLY]
    if unknown:
        raise ValueError(f"krx_weekly 칸이 아니다: {unknown}")
    daily = ", ".join(f"{_DAILY_AS_WEEKLY[c]} AS {c}" for c in cols)
    return (f"(SELECT {', '.join(cols)} FROM krx_weekly "
            "UNION ALL "
            f"SELECT {daily} FROM krx_daily d "
            "WHERE d.trd_dd = (SELECT max(trd_dd) FROM krx_daily) "
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
    """일별 저장분에 보이는 주들을 `krx_weekly` 에 맞춘다 — 닫힌 주는 한 번 옮겨 적고, 이번 주는 옮기지 않는다.

    · 닫힌 주: `krx_weekly` 에 그 주의 같거나 더 늦은 날이 이미 있으면 그대로(R3 — 더 늦은 날로만 바꾼다, 백필이 넣은 금요일을
      일별 저장분의 목요일로 덮지 않는다). 없으면 그 주의 더 이른 날 행을 지우고 가장 늦은 날을 옮겨 적는다.
    · 이번 주: 그 주의 최신일보다 이른 날의 `krx_weekly` 행만 지운다 — 종전 쓰기(매일 이번 주 행을 다시 넣던 것)가 남긴 행이다.
      평소에는 지울 것이 없다(0행 — 죽은 행이 생기지 않는다). 최신일과 같거나 늦은 행은 건드리지 않는다.
    매 기록마다 다시 돌아도 같은 결과다(멱등). 닫기를 한 번 놓쳐도(장애) 그 주가 일별 보관 창(7거래일)에 남아 있는 동안 다음 호출이 메운다.

    `conn` 은 이미 트랜잭션 안의 psycopg 연결(또는 autocommit 연결)이다. SAVEPOINT 안에서 일하고 예외를 밖으로 내지 않는다 —
    주간 닫기는 부(副)라, 실패해도 같은 트랜잭션의 일별 기록은 커밋돼야 한다. 실패는 ERROR 로그와 `ok=False` 로 알린다."""
    try:
        with conn.transaction():
            days = [r[0] for r in conn.execute("SELECT DISTINCT trd_dd FROM krx_daily").fetchall()]
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
#: `krx_daily_v` 의 LAG 는 종목별 창이라, ticker 조건이 뷰 안으로 밀려 들어가 (ticker, trd_dd) 인덱스를 쓴다.
QUOTE_SQL = (
    "WITH win AS (SELECT min(trd_dd) AS lo, max(trd_dd) AS hi FROM krx_daily), "
    "d AS (SELECT win.lo, win.hi, COALESCE(NULLIF(%s, ''), win.hi) AS dd FROM win) "
    "SELECT d.dd, d.lo, d.hi, "
    "EXISTS (SELECT 1 FROM krx_daily x WHERE x.trd_dd = d.dd) AS present, "
    "v.market, v.open, v.high, v.low, v.close, v.volume, v.value, v.list_shrs, v.mktcap, "
    "v.prev_dd, v.prev_close, v.chg, v.chg_pct "
    "FROM d LEFT JOIN LATERAL ("
    "SELECT * FROM krx_daily_v WHERE ticker = %s AND trd_dd = d.dd) v ON true"
)
_QUOTE_COLS = ("dd", "lo", "hi", "present", "market", "open", "high", "low", "close", "volume", "value",
               "list_shrs", "mktcap", "prev_dd", "prev_close", "chg", "chg_pct")


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


#: 서빙 기준일 — 일별 시세 저장분에 보관된 가장 늦은 거래일. 기본키 (trd_dd, ticker) 역방향 스캔 한 번이다.
LATEST_DAY_SQL = "SELECT max(trd_dd) FROM krx_daily"


def latest_day(read_rows: Callable[..., Any], on_error: Any = None) -> Any:
    """서빙 기준일(YYYYMMDD) — `krx_daily` 의 가장 늦은 거래일. 비었으면 None, 못 읽으면 `on_error`(기본 None).

    261018: 유니버스·시총 단면의 기준일(`services/screener._krx_latest_dd`)과 종목 최신가의 기준일
    (`services/price_multiple_data._krx_daily_latest_dd`)이 같은 질의를 따로 적고 있었다 — 정의는 여기 한 곳이고 둘은 위임한다.
    갱신기(`services/krx_refresher._db_latest_dd`)와 배치 갱신(`_ensure_krx_fresh`)도 여기로 모였다(재설계 5-1 쓰는 쪽) — 갱신기는
    「비었다」와 「못 읽었다」를 갈라야 해서 `on_error` 로 표지를 넘긴다.
    `read_rows` 는 **호출 모듈의 DB 읽기 함수**(`db.pg_rows` 계약: None = 미설정·장애, [] = 없음)다. 각 모듈의 시험이 그 모듈의
    DB 읽기(`screener.pg_rows`·`price_multiple_data._pg_rows`)를 바꿔 끼우므로, 어느 함수로 읽을지는 호출자가 정한다.
    비거나 장애여도 `krx_weekly` 로 대신하지 않는다 — 기준일과 행은 같은 원천에서 읽는다(호출자가 기존 강등 경로를 탄다)."""
    rows = read_rows(LATEST_DAY_SQL)
    if rows is None:
        return on_error
    return rows[0][0] if rows and rows[0][0] else None
