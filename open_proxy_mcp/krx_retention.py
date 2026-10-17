# -*- coding: utf-8 -*-
"""`krx_weekly` 보존 정책 — 배치(`scripts/`)와 서버(`services/`)가 같은 경계를 쓰도록 한 곳에 둔다.

261017 신설. 저장 용량 한도 때문에 주간 시세(`krx_weekly`, 2015-12~, 136만 행·190MB)를
**최근 3년 + 종목별로 해마다 마지막 행(연말 행)** 으로 줄인다. 그보다 오래된 주는 별도 백업에 있다.

이 경계는 코드 세 곳이 같은 숫자를 알아야 한다 — ① 표를 자르는 쪽(하한 이전을 지운다),
② 자른 뒤에도 옛 구간을 다시 쓰면 안 되는 배치(재집계·백필·상장 관측·딱지), ③ 옛 구간을
조용히 섞어 내보내면 안 되는 서버(주간 곡선). 세 곳이 서로 다른 경계를 쓰면 한쪽이 지워진
구간을 있는 줄 알고 읽거나, 되살릴 수 없는 값을 덮어쓴다.

정의(고정 — 코디네이터 확정 261018. 바꾸려면 이 파일 한 곳만):

    보존 하한  cutoff_dd = 최신 price_dd 의 **달력 3년 전**(같은 월·일, 2월 29일이면 2월 28일)
    보존 행    price_dd >= cutoff_dd
               **또는** 그 종목의 그 달력연도 **마지막 행**이고 그 마지막 행이 하한보다 이른 것
               (= (ticker, max(price_dd)) per (ticker, left(price_dd, 4)) 중 max(price_dd) < cutoff_dd)

    예) 최신 20261016 → 하한 20231016 → 가장 오래된 보존 주 20231020 → 157주
        실측(261017): 하한 이후 429,087 + 종목별 연말 행 18,561 = 보존 447,648행 / 전체 1,364,894행 (삭제 917,246행)

    종목별로 남기는 이유: 서버의 연말 질의(`_annual_pit_band` 등, 종목별 12월 마지막 행 DISTINCT ON 해)가 컷 전후에
    같은 답을 내야 한다. 그해 12월에 상장폐지한 종목의 마지막 행도 남고, 하한이 속한 해에 하한 전에 끝난 종목의
    마지막 행도 남는다(전역 마지막 주 한 줄만 남기면 (종목, 연도) 19쌍의 연말 점이 사라진다).

순수 함수만 둔다 — DB·시계·네트워크를 부르지 않는다. 호출부가 **그 시점 표의 `max(price_dd)`**
를 `latest_dd` 로 넘긴다(서버도 오늘 날짜가 아니라 저장된 최신 주를 넘겨야 표를 자르는 쪽과 어긋나지 않는다).

■ SQL 조각 (표를 자르는 쪽·롤링 정리가 같은 정의를 쓰도록) — 파이썬 판과 같은 집합이다(테스트가 SQLite 로 대조).

    keep_predicate  보존 행 조건 — 의미의 정본. `price_dd >= 하한 OR (ticker, price_dd) IN (… HAVING max(price_dd) < 하한)` 꼴
    prune_where     그 보수(지울 행)를 `NOT EXISTS` 로 — **큰 표에서는 이쪽을 쓴다**
    prune_statement `DELETE … WHERE prune_where`
    keep_select     보존 행 SELECT(조인 꼴) — 새 표로 갈아 끼우는 컷용

  🔴 성능 함정(운영 실측) — `keep_predicate` 를 큰 표에 **그대로** 쓰면 끝나지 않는다. OR 안의 `IN (서브쿼리)` 는
  반조인으로 풀리지 않고, 서브쿼리 결과가 `work_mem` 에 안 들어가면 해시되지 않은 `SubPlan` 이 행마다 결과를 선형 탐색한다.
  계획기가 묶음 수를 약 14만으로 과대추정하지만 실제는 약 1.9만이다(추정 크기가 `work_mem` 을 넘겨 해시 불가로 판정).
  실측(261017, 136만 행): `work_mem`=2MB(기본) 에서 120초 시간 초과(계획 추정 비용 5.6e10), `work_mem`=64MB 에서는 수 초(해시).
  그래서 표에 대해 이 술어를 돌리는 코드(롤링 삭제 포함)는 둘 중 하나를 해야 한다:
    ① 같은 트랜잭션에서 `SET LOCAL work_mem = '64MB'` 를 걸거나,
    ② 조인 꼴을 쓴다 — `prune_where`(Merge/Hash Anti Join) · `keep_select`(Join). 둘 다 기본 설정에서 끝난다.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from typing import Collection, Iterable

#: 보존 기간(달력 연). 하한 = 최신 주의 이만큼 전.
KRX_WEEKLY_RETENTION_YEARS = 3

#: `krx_weekly` 의 원래 첫 주 — 상장 관측창의 시작. 표에서 `min(price_dd)` 를 읽지 않고 이 상수를 쓴다.
#: (표를 자르면 가장 이른 행이 무엇인지가 자르는 방식에 달려 버린다 — 연말 행 보존 덕에 지금은 같은 값이지만
#:  그건 우연한 일치다. 「관측창이 언제 시작했나」는 사실이라 상수가 맞다.)
KRX_WEEKLY_ORIGIN_DD = "20151230"

#: 하한 직후 몇 일까지는 「직전 주간 점」이 하한 아래(연말 행)일 수 있다. 사건 전후 두 점을 비교하는
#: 계산(딱지·계수 라벨)은 이만큼 안쪽 사건만 믿는다. 설·추석으로 한 주가 통째로 비어도(최대 14일 간격) 닿는 값.
_PAIR_MARGIN_DAYS = 14

_DD_RE = re.compile(r"[0-9]{8}")
_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_COLUMNS_RE = re.compile(r"[A-Za-z0-9_.*, ]+")


def _parse_dd(dd: str) -> date:
    if not isinstance(dd, str) or not _DD_RE.fullmatch(dd):
        raise ValueError(f"YYYYMMDD 가 아니다: {dd!r}")
    try:
        return date(int(dd[:4]), int(dd[4:6]), int(dd[6:]))
    except ValueError as exc:
        raise ValueError(f"달력에 없는 날짜: {dd!r}") from exc


def _fmt(d: date) -> str:
    return f"{d.year:04d}{d.month:02d}{d.day:02d}"


def krx_weekly_cutoff(latest_dd: str) -> str:
    """보존 하한 `price_dd`(YYYYMMDD) — 이 날짜 **이상**의 주는 전부 남긴다.

    `latest_dd` = 그 시점 표의 `max(price_dd)`. 달력 3년 전 같은 월·일이고, 그날이 없으면(2월 29일) 2월 28일.
    """
    d = _parse_dd(latest_dd)
    year = d.year - KRX_WEEKLY_RETENTION_YEARS
    try:
        return _fmt(d.replace(year=year))
    except ValueError:  # 윤일 → 평년
        return _fmt(date(year, 2, 28))


def year_end_rows(rows: Iterable[tuple[str, str]], latest_dd: str) -> frozenset[tuple[str, str]]:
    """(ticker, price_dd) 행 목록에서 **종목별·해별 마지막 행** 중 하한보다 이른 것(연말 행).

    SQL 의 `SELECT ticker, max(price_dd) … GROUP BY ticker, left(price_dd, 4) HAVING max(price_dd) < 하한` 과 같다.
    """
    cutoff = krx_weekly_cutoff(latest_dd)
    last: dict[tuple[str, str], str] = {}
    for ticker, dd in rows:
        key = (ticker, dd[:4])
        if key not in last or dd > last[key]:
            last[key] = dd
    return frozenset((ticker, dd) for (ticker, _year), dd in last.items() if dd < cutoff)


def is_retained(ticker: str, price_dd: str, latest_dd: str, year_end_set: Collection[tuple[str, str]]) -> bool:
    """이 행이 정리 뒤에도 남는가 — 하한 이상이거나, 하한보다 이른 그 종목의 그 해 마지막 행.

    `year_end_set` 은 `year_end_rows(표 전체 행, latest_dd)` 결과를 호출부가 한 번 만들어 넘긴다.
    이미 잘린 표에 다시 적용해도 같은 결과다(멱등) — 하한이 밀려도 롤링 정리가 같은 함수를 쓴다.
    """
    return price_dd >= krx_weekly_cutoff(latest_dd) or (ticker, price_dd) in year_end_set


def krx_weekly_pair_floor(latest_dd: str) -> str:
    """「사건 전후 주간 점 두 개」를 비교하는 계산이 믿을 수 있는 가장 이른 사건일.

    하한 바로 뒤의 사건은 직전 주간 점이 하한 아래의 연말 행(최대 1년 전)으로 잡혀 비교가 틀어진다.
    하한에 `_PAIR_MARGIN_DAYS` 를 더한 날 이후의 사건만 직전·직후 점이 모두 주 단위로 촘촘하다.
    """
    return _fmt(_parse_dd(krx_weekly_cutoff(latest_dd)) + timedelta(days=_PAIR_MARGIN_DAYS))


def weekly_window_note(cutoff_dd: str) -> str:
    """서버가 주간 곡선을 하한(`krx_weekly_cutoff` 결과)에서 자를 때 사용자에게 알리는 문장(한국어)."""
    c = cutoff_dd
    return (f"주간 시세는 최근 3년까지 제공(연말 값은 전체 기간) — 주간 구간은 "
            f"{c[:4]}-{c[4:6]}-{c[6:]} 이후입니다. 그 이전은 종목별로 해마다 마지막 한 점씩만 보관하므로 "
            "주간 시계열에 섞지 않았습니다.")


# ── SQL 조각 ───────────────────────────────────────────────────────────────────
# 자리표시자는 psycopg 의 `%s` 다. 표 이름·별칭·열 목록은 식별자 형태만 받는다(문자열 끼워 넣기를 막는다).

def _ident(name: str) -> str:
    if not isinstance(name, str) or not _IDENT_RE.fullmatch(name):
        raise ValueError(f"식별자가 아니다: {name!r}")
    return name


def _year_end_subquery(table: str) -> str:
    """종목별·해별 마지막 행 중 하한보다 이른 것 — 자리표시자 1개(하한)."""
    return (f"SELECT ticker, max(price_dd) AS price_dd FROM {_ident(table)} "
            "GROUP BY ticker, left(price_dd, 4) HAVING max(price_dd) < %s")


def keep_predicate(latest_dd: str, *, table: str = "krx_weekly", alias: str | None = None) -> tuple[str, tuple]:
    """보존 행 조건 — **의미의 정본**. 반환 (SQL 조각, 파라미터 (하한, 하한)).

    `(price_dd >= 하한 OR (ticker, price_dd) IN (SELECT ticker, max(price_dd) FROM … GROUP BY ticker, left(price_dd, 4)
    HAVING max(price_dd) < 하한))`

    🔴 큰 표에 그대로 쓰면 끝나지 않는다(모듈 docstring 「성능 함정」) — 같은 트랜잭션에서 `SET LOCAL work_mem = '64MB'` 를
    걸거나, 지우는 일은 `prune_statement`, 새 표로 옮기는 일은 `keep_select`(둘 다 조인 꼴)를 쓴다.
    `alias` 는 바깥 표의 별칭(열 앞에 붙는다). `ticker`·`price_dd` 는 PK 라 NULL 이 없어 `NOT (…)` 도 안전하다.
    """
    a = f"{_ident(alias)}." if alias else ""
    sql = (f"({a}price_dd >= %s OR ({a}ticker, {a}price_dd) IN ({_year_end_subquery(table)}))")
    cutoff = krx_weekly_cutoff(latest_dd)
    return sql, (cutoff, cutoff)


def prune_where(latest_dd: str, *, table: str = "krx_weekly", alias: str = "w") -> tuple[str, tuple]:
    """**지울 행** 조건 — `keep_predicate` 의 보수. 반환 (SQL 조각, 파라미터 (하한, 하한)).

    `w.price_dd < 하한 AND NOT EXISTS (종목별 연말 행 서브쿼리와 (ticker, price_dd) 일치)` — 최상위 AND 라서 계획기가
    **반조인**으로 푼다(기본 `work_mem` 에서도 끝난다). `DELETE`/`SELECT` 의 WHERE 최상위에 그대로 쓰고,
    다른 조건과 **OR 로 묶지 않는다**(OR 안에 들어가면 `keep_predicate` 와 같은 함정이 된다).
    """
    a = _ident(alias)
    sql = (f"{a}.price_dd < %s AND NOT EXISTS (SELECT 1 FROM ({_year_end_subquery(table)}) ye "
           f"WHERE ye.ticker = {a}.ticker AND ye.price_dd = {a}.price_dd)")
    cutoff = krx_weekly_cutoff(latest_dd)
    return sql, (cutoff, cutoff)


def prune_statement(latest_dd: str, *, table: str = "krx_weekly") -> tuple[str, tuple]:
    """`DELETE FROM krx_weekly AS w WHERE <prune_where>` — 롤링 정리·컷이 쓰는 지우기. 반환 (SQL, 파라미터)."""
    where, params = prune_where(latest_dd, table=table, alias="w")
    return f"DELETE FROM {_ident(table)} AS w WHERE {where}", params


def keep_select(latest_dd: str, *, table: str = "krx_weekly", columns: str = "w.*") -> tuple[str, tuple]:
    """**보존 행** SELECT — 조인 꼴. 새 표로 옮겨 담아 갈아 끼우는 컷에 쓴다. 반환 (SQL, 파라미터 (하한, 하한)).

    하한 이상 행 `UNION ALL` 종목별 연말 행. 두 갈래는 겹치지 않는다(연말 행은 하한보다 이르다).
    `columns` 는 `w.*` 또는 `w.ticker, w.price_dd` 같은 열 목록.
    """
    if not isinstance(columns, str) or not _COLUMNS_RE.fullmatch(columns):
        raise ValueError(f"열 목록 형식이 아니다: {columns!r}")
    t = _ident(table)
    sql = (f"SELECT {columns} FROM {t} w WHERE w.price_dd >= %s "
           f"UNION ALL SELECT {columns} FROM {t} w JOIN ({_year_end_subquery(t)}) ye "
           "ON ye.ticker = w.ticker AND ye.price_dd = w.price_dd")
    cutoff = krx_weekly_cutoff(latest_dd)
    return sql, (cutoff, cutoff)
