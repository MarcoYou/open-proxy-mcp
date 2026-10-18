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

이 모듈은 **DB 와 순수 함수만** 다룬다(네트워크 없음). KRX 를 부르는 쪽은 `services/krx_refresher.py`.
`services/price_multiple_data.py` 를 import 하지 않는다 — 그쪽이 이 모듈을 import 한다(순환 방지).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

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

    `conn` 은 **이미 트랜잭션 안에 있는** psycopg 연결(`krx_weekly` 를 쓰던 그 연결)이다. 이 함수는 중첩 트랜잭션(SAVEPOINT)
    안에서 일하고, 실패해도 예외를 밖으로 내지 않는다 — `krx_daily` 가 아직 없거나 고장 나도 호출자의 `krx_weekly`
    쓰기(지금도 읽는 쪽이 쓰는 표)는 커밋되게 하려는 것이다. 실패는 로그(ERROR)와 반환값으로 알린다.

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
    except Exception as exc:                         # noqa: BLE001 — krx_weekly 쓰기를 막지 않는다
        logger.error("krx_daily 기록 실패(krx_weekly 는 계속) — 마이그레이션 적용 여부 확인: %s: %s",
                     type(exc).__name__, exc)
        return False, 0, 0


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


def latest_day(read_rows: Callable[..., Any]) -> str | None:
    """서빙 기준일(YYYYMMDD) — `krx_daily` 의 가장 늦은 거래일. 비었거나 못 읽으면 None.

    261018: 유니버스·시총 단면의 기준일(`services/screener._krx_latest_dd`)과 종목 최신가의 기준일
    (`services/price_multiple_data._krx_daily_latest_dd`)이 같은 질의를 따로 적고 있었다 — 정의는 여기 한 곳이고 둘은 위임한다.
    `read_rows` 는 **호출 모듈의 DB 읽기 함수**(`db.pg_rows` 계약: None = 미설정·장애, [] = 없음)다. 각 모듈의 시험이 그 모듈의
    DB 읽기(`screener.pg_rows`·`price_multiple_data._pg_rows`)를 바꿔 끼우므로, 어느 함수로 읽을지는 호출자가 정한다.
    비거나 장애여도 `krx_weekly` 로 대신하지 않는다 — 기준일과 행은 같은 원천에서 읽는다(호출자가 기존 강등 경로를 탄다)."""
    rows = read_rows(LATEST_DAY_SQL)
    return rows[0][0] if rows and rows[0][0] else None
