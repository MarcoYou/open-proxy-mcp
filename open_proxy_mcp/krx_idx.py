# -*- coding: utf-8 -*-
"""`krx_idx` — KRX 공식 지수 종가(규모 대표·업종·섹터)의 응답 해석과 DB 쓰기·정리. 스키마는 `scripts/krx_idx_ddl.py`(서버는 DDL 을 실행하지 않는다).

261021 신설(P0 지수 종가). 서버 갱신기(`services/krx_refresher.py`)가 그날 시세(`krx_px`)를 기록한 뒤 **같은 권고 잠금 안에서**
지수 엔드포인트 셋(`krx_api.fetch_index_day` — 하루 +3콜, 호출 장부 `ops_krx_calls` 에 같이 센다)을 부르고, 이 모듈이 해석해 쓴다.

■ 열쇠 — (엔드포인트 글자 src, 앞뒤 공백을 지운 이름). 응답에 지수 코드 칸은 없고, 20개 업종 이름이 코스피·코스닥 양쪽에 똑같이 있다.
  계열 사전 `krx_idx_series` 가 (src, name) → sid 를 준다. 값을 DB 에 두는 계열은 `in_db` 인 것뿐이다(규모 대표 약 12 + 공식 업종·섹터 약 71).
  `in_db` 가 거짓인 계열은 이름만 알려 주고 값은 opm-cold(`krx_idx_hist`)에 있다 — 서버는 그 계열 값을 쓰지 않는다.
■ 새 이름 — 사전에 없는 (src, name) 이 종가와 함께 오면 `in_db = false`(기본값)·종류 `t` 로 등록하고 경고한다. 값은 넣지 않는다
  (in_db 를 정하는 것은 사람 몫 — 분류해 사전에 반영한 뒤 백필). 등록 행의 first_dd·last_dd 는 비워 둔다(값을 모은 곳이 아직 없다).
  `EXCLUDED`(종가가 없는 「(외국주포함)」·지수 수준이 아닌 「K-샤프지수」)는 등록하지 않는다.
■ 값 — 종가 문자열(쉼표 포함, 소수 둘째 자리) × 100 정수. 0 이하·소수 셋째 자리·빈칸은 그 계열만 건너뛴다(경고).
  응답의 기준일(`BAS_DD`)이 부른 날과 다르면 그 행은 버린다.
■ 휴장 판정은 하지 않는다 — 지수 응답이 빈 것 하나로 휴장이라 읽지 않는다(DATA_OPS §11). 서버는 시세 달력(`krx_day`)이 이미 「행 있는 거래일」로
  적은 날만 지수를 부른다. 지수 응답이 비면 「아직 게시 전」이고 다음 틱에 다시 묻는다(프로세스당 하루 상한 — 갱신기).
■ 보관 모양(마스터 10-21 「3년 모양」) — DB = 일별 창(`krx_day` 의 날들) + 주간 3년(창 앞 `krx_wk_day` 의 날 중 3년 하한 이후), 그 앞은 opm-cold.
  · 창 밖 일별: 시세 보관 창 정리(`krx_daily.prune_window`)가 `krx_px` 를 지우는 **바로 그 날들**(콜드에 들어간 날)에서, 주 닫는 날이 아닌 날의
    행을 **달력 행(`krx_day`)을 지우기 전에** 지운다(`prune_daily`) — 주 닫는 날은 `krx_wk_day` 로 옮겨지므로 남는다. 서버 계정(opm_app)의
    DELETE 정책이 「달력에 콜드이면서 주 닫는 날이 아닌 날」이라 순서가 바뀌면 지울 수 없다.
  · 3년 밖: `krx_wk_day` 의 날 중 3년 하한(`krx_retention.krx_weekly_cutoff` — 마지막 주 닫는 날 기준) 이전이면서, 콜드 서빙본
    `krx_idx_hist` 의 coverage.max_dd 이하이고, 그 판이 발행된 지 `OPM_COLD_MANIFEST_MAX_AGE_SEC`(기본 72시간)가 지났을 때만 지운다
    (`prune_old` — 판정은 `cold_store.settled_max_dd`). 콜드가 꺼져 있거나 목록을 못 읽으면 지우지 않는다.
■ 켜고 끄기 — `OPM_KRX_IDX_WRITE=0` 이면 지수를 부르지도 쓰지도 않는다(갱신기 자체는 그대로). 기본은 켜짐이지만 사전에 `in_db` 계열이 하나도
  없거나 표가 없으면(DDL·적재 전) 아무것도 하지 않는다. 창 밖 일별 정리(`prune_daily`)는 스위치와 상관없이 시세 정리를 따라간다 — 표에 그 날 행이
  있을 때만 지우고, 없으면 아무것도 안 한다.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from open_proxy_mcp import krx_api
from open_proxy_mcp.krx_retention import krx_weekly_cutoff

logger = logging.getLogger(__name__)

ENV_FLAG = "OPM_KRX_IDX_WRITE"
_FALSE = {"0", "false", "off", "no"}

#: 엔드포인트 글자 — 사전의 src CHECK(P·Q·X)와 `krx_api.IDX_ENDPOINTS` 의 열쇠가 같다.
SOURCES = tuple(krx_api.IDX_ENDPOINTS)
#: 응답의 IDX_CLSS(계열 분류) 기대값 — 다르면 경고만 한다(값은 쓴다).
CLSS = {"P": "KOSPI", "Q": "KOSDAQ", "X": "KRX"}
#: 사전에 올리지 않는 이름(src, name) → 까닭. 「(외국주포함)」 둘은 종가가 비어 오고, K-샤프지수 넷은 지수 수준이 아니다(비율).
EXCLUDED: dict[tuple[str, str], str] = {
    ("P", "코스피 (외국주포함)"): "no_close", ("Q", "코스닥 (외국주포함)"): "no_close",
    ("X", "K-샤프지수(1년)"): "not_index_level", ("X", "K-샤프지수(3년)"): "not_index_level",
    ("X", "K-샤프지수(5년)"): "not_index_level", ("X", "K-샤프지수(10년)"): "not_index_level",
}
#: 개명 이음 — 옛 이름(src, name) → 지금 이름. 같은 지수가 이름만 바뀌었다고 확인한 것만(지금은 없다).
ALIASES: dict[tuple[str, str], str] = {}
#: 서버가 등록하는 새 계열의 종류(z 규모·시장 대표 / s 업종·섹터 / t 그 밖). 분류는 사람이 고친다.
NEW_KIND = "t"
#: 지수가 빠진 거래일을 찾는 범위(오늘 KST 에서 달력 일). 서버 계정의 INSERT 정책 창(최근 60일)보다 좁아야 한다.
LOOKBACK_DAYS = 45
#: 이 비율보다 적은 DB 계열만 온 날은 쓰지 않는다(반쪽 응답·대량 개명 — 경고 후 다음에 다시).
MIN_SHARE = 0.5
#: 3년 밖 행의 사본이 있는 콜드 서빙 데이터셋.
COLD_DATASET = "krx_idx_hist"


def enabled() -> bool:
    """지수 받기·쓰기가 켜져 있나(`OPM_KRX_IDX_WRITE`). 비었거나 없으면 켜짐."""
    return (os.environ.get(ENV_FLAG) or "").strip().lower() not in _FALSE


def _d(dd: str) -> date:
    return date(int(dd[:4]), int(dd[4:6]), int(dd[6:8]))


# ── 응답 해석 ──────────────────────────────────────────────────────────────────────────────────

def norm_name(v: Any) -> str:
    return str(v or "").strip()


def close_x100(v: Any) -> int | None:
    """종가 문자열 → ×100 정수. 빈칸·'-' 은 None. 0 이하·소수 셋째 자리·숫자 아님은 ValueError."""
    s = str(v if v is not None else "").strip().replace(",", "")
    if s in ("", "-"):
        return None
    try:
        q = Decimal(s) * 100
    except InvalidOperation:
        raise ValueError(f"숫자가 아니다: {s[:20]!r}") from None
    if q != q.to_integral_value() or q <= 0:
        raise ValueError(f"종가 {s} — 0 이하이거나 소수 둘째 자리 밖")
    n = int(q)
    if n > 2**31 - 1:
        raise ValueError(f"종가 {s} — int4 범위 밖")
    return n


@dataclass
class ParsedDay:
    """한 날의 해석 결과. closes = {(src, name): 종가×100}(종가가 있는 계열만), problems = 사람이 볼 문장들."""
    dd: str
    closes: dict[tuple[str, str], int] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)


def parse_day(dd: str, rows_by_src: dict[str, list]) -> ParsedDay:
    """`krx_api.fetch_index_day` 의 행들 → `ParsedDay`. 같은 (src, name) 이 두 번 오면 그 계열은 버린다(어느 값인지 모른다)."""
    out = ParsedDay(dd)
    dup: set[tuple[str, str]] = set()
    for src in SOURCES:
        for r in rows_by_src.get(src) or ():
            name = norm_name(r.get("IDX_NM"))
            if not name:
                continue
            name = ALIASES.get((src, name), name)
            key = (src, name)
            if key in EXCLUDED:
                continue
            if str(r.get("BAS_DD") or "").strip() != dd:
                out.problems.append(f"{src}|{name} 기준일 {r.get('BAS_DD')!r} ≠ {dd} — 버림")
                continue
            clss = norm_name(r.get("IDX_CLSS"))
            if clss and clss != CLSS[src]:
                out.problems.append(f"{src}|{name} 계열 분류 {clss!r} ≠ {CLSS[src]}")
            try:
                v = close_x100(r.get("CLSPRC_IDX"))
            except ValueError as exc:
                out.problems.append(f"{src}|{name} {exc} — 건너뜀")
                continue
            if v is None:
                continue
            if key in out.closes or key in dup:
                dup.add(key)
                out.closes.pop(key, None)
                continue
            out.closes[key] = v
    for key in sorted(dup):
        out.problems.append(f"{key[0]}|{key[1]} 가 한 응답에 두 번 — 버림")
    return out


# ── 무엇을 받을지 ────────────────────────────────────────────────────────────────────────────────

READY_SQL = ("SELECT to_regclass('public.krx_idx') IS NOT NULL AND to_regclass('public.krx_idx_series') IS NOT NULL "
             "AND to_regclass('public.krx_day') IS NOT NULL")
#: (기준 계열 sid, 지수가 없는 거래일들 — 최근 날부터). 기준 계열 = in_db 계열 중 가장 작은 sid(코스피). 그날 쓰기는 한 트랜잭션이라
#: 기준 계열 행이 있으면 그날은 쓴 것이다. 기본키 (sid, trd_dd) 로 날마다 한 번 찾는다.
PENDING_SQL = """\
WITH a AS (SELECT min(sid) AS sid FROM krx_idx_series WHERE in_db)
SELECT (SELECT sid FROM a),
       ARRAY(SELECT d.dd FROM krx_day d
             WHERE d.n > 0 AND NOT d.closed AND d.trd_dd >= %(lo)s
               AND NOT EXISTS (SELECT 1 FROM krx_idx i WHERE i.sid = (SELECT sid FROM a) AND i.trd_dd = d.trd_dd)
             ORDER BY d.trd_dd DESC)
"""


@dataclass(frozen=True)
class Pending:
    """ready = 표와 in_db 계열이 있다(거짓이면 DDL·적재 전 — 아무것도 안 한다). days = 지수가 빠진 거래일(최근 날부터)."""
    ready: bool
    days: tuple[str, ...] = ()


def pending(read_rows: Callable[..., Any], today: str) -> Pending | None:
    """지수를 받아야 할 날. 못 읽으면 None(`read_rows` 는 `db.pg_rows` 계약 — None = DB 장애)."""
    r = read_rows(READY_SQL)
    if r is None:
        return None
    if not r or not r[0][0]:
        return Pending(False)
    rows = read_rows(PENDING_SQL, {"lo": _d(today) - timedelta(days=LOOKBACK_DAYS)})
    if rows is None:
        return None
    anchor, days = rows[0] if rows else (None, [])
    if anchor is None:
        return Pending(False)
    return Pending(True, tuple(days or ()))


# ── 쓰기 ──────────────────────────────────────────────────────────────────────────────────────────

SERIES_SQL = "SELECT sid, src::text, name, in_db FROM krx_idx_series"
NEW_SERIES_SQL = ("INSERT INTO krx_idx_series (src, name, kind) VALUES (%s::\"char\", %s, %s::\"char\") "
                  "ON CONFLICT (src, name) DO NOTHING")
DAY_DELETE_SQL = "DELETE FROM krx_idx WHERE trd_dd = %s"
#: 한 문장 INSERT(sid 순 — 기본키 색인이 촘촘히 찬다). RLS 표라 비소유자 계정은 COPY 를 못 쓴다.
INSERT_SQL = ("INSERT INTO krx_idx (sid, trd_dd, close_x100) "
              "SELECT u.sid, %s::date, u.c FROM unnest(%s::smallint[], %s::int[]) AS u(sid, c) ORDER BY u.sid")
#: 받은 날로 사전의 범위를 민다(DB 계열만 — 값이 DB 에 있는 계열). 바뀔 때만 쓴다.
SPAN_SQL = ("UPDATE krx_idx_series SET first_dd = LEAST(coalesce(first_dd, %(d)s), %(d)s), "
            "last_dd = GREATEST(coalesce(last_dd, %(d)s), %(d)s) "
            "WHERE sid = ANY(%(sids)s::smallint[]) AND (first_dd IS NULL OR last_dd IS NULL OR first_dd > %(d)s OR last_dd < %(d)s)")


@dataclass(frozen=True)
class IdxWrite:
    """ok = 그날 행을 썼다. rows = 넣은 행 수. new = 이번에 사전에 등록한 새 이름들. absent = 응답에 없던 DB 계열 이름들.
    old = 3년 밖으로 지운 행 수. reason = 쓰지 않은 까닭(ok 가 거짓일 때)."""
    ok: bool
    rows: int = 0
    new: tuple[str, ...] = ()
    absent: tuple[str, ...] = ()
    old: int = 0
    reason: str = ""


def write_day(conn: Any, parsed: ParsedDay) -> IdxWrite:
    """한 날의 DB 계열 종가를 쓴다 — 새 이름 등록 → 그 날 행 지우고 넣기 → 사전 범위. `conn` 은 트랜잭션 안의 psycopg 연결이다.
    DB 계열이 `MIN_SHARE` 보다 적게 왔으면 쓰지 않는다(새 이름 등록도 하지 않는다 — 반쪽 응답의 이름을 사전에 남기지 않게)."""
    dd = parsed.dd
    series = {(src, name): (sid, in_db) for sid, src, name, in_db in conn.execute(SERIES_SQL).fetchall()}
    in_db = {k: v[0] for k, v in series.items() if v[1]}
    if not in_db:
        return IdxWrite(False, reason="사전에 DB 계열이 없다(적재 전)")
    got = {k: v for k, v in parsed.closes.items() if k in in_db}
    if len(got) < MIN_SHARE * len(in_db):
        return IdxWrite(False, reason=f"DB 계열 {len(got)}/{len(in_db)} 만 왔다 — 쓰지 않음")
    new = sorted(k for k in parsed.closes if k not in series)
    for src, name in new:
        conn.execute(NEW_SERIES_SQL, (src, name, NEW_KIND))
    day = _d(dd)
    conn.execute(DAY_DELETE_SQL, (day,))
    pairs = sorted((in_db[k], v) for k, v in got.items())
    conn.execute(INSERT_SQL, (day, [p[0] for p in pairs], [p[1] for p in pairs]))
    conn.execute(SPAN_SQL, {"d": day, "sids": [p[0] for p in pairs]})
    absent = sorted(f"{k[0]}|{k[1]}" for k in in_db if k not in got)
    return IdxWrite(True, len(pairs), tuple(f"{k[0]}|{k[1]}" for k in new), tuple(absent))


# ── 정리 ──────────────────────────────────────────────────────────────────────────────────────────

TABLE_SQL = "SELECT to_regclass('public.krx_idx') IS NOT NULL"
#: 창 밖 일별 — 시세 정리가 지우는 날들 중 주 닫는 날이 아닌 날. 달력 행이 아직 있어야 한다(`krx_daily.prune_window` 가 PRUNE_DAY 전에 부른다).
PRUNE_DAILY_SQL = ("DELETE FROM krx_idx i USING krx_day d WHERE d.trd_dd = i.trd_dd AND d.trd_dd = ANY(%s::date[]) AND NOT d.wk_close")
#: 지우지 못했을 때 — 그 날들 중 지수 행이 남아 있는 날(시세 정리도 그 날은 미룬다).
PRUNE_DAILY_LEFT_SQL = ("SELECT DISTINCT i.trd_dd FROM krx_idx i JOIN krx_day d ON d.trd_dd = i.trd_dd "
                        "WHERE d.trd_dd = ANY(%s::date[]) AND NOT d.wk_close ORDER BY 1")
#: 3년 밖 — 주 달력의 날 중 하한 이전이면서 콜드 coverage 안.
PRUNE_OLD_SQL = ("DELETE FROM krx_idx i USING krx_wk_day w WHERE w.wk_dd = i.trd_dd AND i.trd_dd < %s::date AND i.trd_dd <= %s::date")
WK_LATEST_SQL = "SELECT max(dd) FROM krx_day WHERE wk_close"


def _rowcount(cur: Any) -> int:
    n = getattr(cur, "rowcount", 0)
    return n if isinstance(n, int) and n > 0 else 0


def prune_daily(conn: Any, days: list[date]) -> tuple[int, tuple[date, ...]]:
    """시세 보관 창 정리가 지우려는 날(`days`)의 지수 행 중 주 닫는 날이 아닌 날을 지운다 → (지운 행 수, 시세 정리도 미뤄야 할 날들).

    `conn` 은 트랜잭션 안의 psycopg 연결이다. 자기 SAVEPOINT 에서 돈다. 표가 없으면 (0, ()). 지우기가 실패하면(권한·잠금 시간 초과) 그 날들 중
    지수 행이 남은 날을 돌려준다 — 호출자는 그 날의 달력 행을 지우지 않는다(달력 행이 사라지면 서버 계정으로는 그 지수 행을 다시 지울 수 없다).
    행이 없으면(적재 전) 실패해도 미룰 날이 없다."""
    if not days:
        return 0, ()
    try:
        with conn.transaction():
            if not conn.execute(TABLE_SQL).fetchone()[0]:
                return 0, ()
            return _rowcount(conn.execute(PRUNE_DAILY_SQL, (days,))), ()
    except Exception as exc:                         # noqa: BLE001 — 시세 정리는 계속한다(남은 날만 미룬다)
        err = f"{type(exc).__name__}: {exc}"
    try:
        with conn.transaction():
            left = tuple(r[0] for r in conn.execute(PRUNE_DAILY_LEFT_SQL, (days,)).fetchall())
    except Exception:                                # noqa: BLE001 — 모르면 전부 미룬다
        left = tuple(days)
    if left:
        logger.error("지수 종가 창 밖 정리 실패 — 그 날 %d일(%s~%s)은 시세 정리도 미룬다(다음 기록 때 다시) %s",
                     len(left), left[0], left[-1], err)
    else:
        logger.warning("지수 종가 창 밖 정리 실패(지울 행은 없었다 — 계속) %s", err)
    return 0, left


def prune_old(conn: Any, cold_max_dd: str | None) -> int:
    """3년 밖 주간 점을 지운다 → 지운 행 수. `cold_max_dd` = 콜드 서빙본이 덮는 마지막 날(발행 뒤 충분히 지난 판 — `cold_store.settled_max_dd`).
    None 이면 지우지 않는다. 하한 = `krx_weekly_cutoff(마지막 주 닫는 날)`(주간 시세와 같은 정의)."""
    if not cold_max_dd:
        return 0
    wk = conn.execute(WK_LATEST_SQL).fetchone()
    if not wk or not wk[0]:
        return 0
    cut = krx_weekly_cutoff(wk[0])
    return _rowcount(conn.execute(PRUNE_OLD_SQL, (_d(cut), _d(cold_max_dd))))
