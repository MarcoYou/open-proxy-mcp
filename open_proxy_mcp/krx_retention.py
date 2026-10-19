# -*- coding: utf-8 -*-
"""`krx_weekly` 보존 정책 — 배치(`scripts/`)와 서버(`services/`)가 같은 경계를 쓰도록 한 곳에 둔다.

261017 신설. 저장 용량 한도 때문에 주간 시세(`krx_weekly`, 2015-12~, 136만 행·190MB)를
**최근 3년 + 종목별로 해마다 마지막 행(연말 행)** 으로 줄인다. 그보다 오래된 주는 별도 백업(보관본)에 있고, 서버는 그 보관본에서
만든 서빙본을 콜드 서빙(`cold_store`, 261019)으로 읽어 하한 이전 주간 곡선을 DB 행 앞에 잇는다 — 이음매 규칙은 아래 「콜드 서빙과의 이음매」.

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


def week_unit(dd: str) -> tuple[str, str]:
    """`krx_weekly` 의 「한 주」 — dd 가 속한 ISO 주(월~일)를 **그 달력 연도 안으로 자른** (첫날, 끝날) YYYYMMDD.

    261018(재설계 5-1 쓰는 쪽). 종전 정의는 ISO 주 그대로(`_iso_wk_range`)였다. 12/31 이 화·수·목인 해에는 그해 마지막
    거래일(12/29~30)과 다음 해 첫 거래일(1/2~)이 같은 ISO 주라, 1/2 를 쓰는 순간 같은 주의 옛 날짜로 12/30 이 지워졌다 —
    보존 규칙의 「그해 마지막 행」·연말 밴드·연말 배당수익률이 하루~이틀 이른 금요일 값이 됐다.
    실측(운영 DB 261018): 2019·2024·2025 연말 행이 20191227·20241227·20251226(실제 마지막 거래일은 각각 12/30).
    다음으로 겹치는 해는 2030·2031 이다. 연도 경계에서 주를 둘로 나누면 「주마다 그 주 마지막 거래일 한 점」과
    「해마다 마지막 거래일 한 점」이 함께 성립한다(연말 주만 주간 점이 하나 더 생긴다).
    쓰는 쪽(주 닫기·같은 주 정리)과 읽는 쪽(일별 끝점 잇기)이 모두 이 정의 하나를 쓴다."""
    d = _parse_dd(dd)
    mon = d - timedelta(days=d.isoweekday() - 1)
    sun = mon + timedelta(days=6)
    return _fmt(max(mon, date(d.year, 1, 1))), _fmt(min(sun, date(d.year, 12, 31)))


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


# ── 콜드 서빙과의 이음매 (261019, 설계 v03 D2) ──────────────────────────────────
# 하한 이전 주간 곡선은 DB 밖 서빙본(`cold_store`, 데이터셋 krx_weekly_hist)에서 읽어 DB 행과 잇는다.
#   S1 경계는 하나다 — B = `krx_weekly_cutoff(저장된 최신 주)`. 자르는 쪽·롤링·서버가 같은 함수를 쓴다.
#   S2 반열린 구간, 중복 0 — 콜드는 price_dd < S, DB 는 price_dd >= S. S = min(B, C + 1일), C = 서빙본 coverage.max_dd
#      (그 날까지 원천의 모든 행이 서빙본에 있다는 약속). 오늘(10-19)은 C = 20231015 라 S = B = 20231016.
#   S3 DB 에 남은 S 이전 연말 행은 곡선에 쓰지 않는다 — 같은 값이 서빙본에 있다(발행기·주간 검증기가 전수 대조). 연말 행은 DB 에 계속
#      남아 연말 밴드(`_annual_pit_band`)·연말 종가·강등 경로가 쓴다.
#   S4 주 정의는 양쪽이 같다(`week_unit`) — 서빙본은 DB 와 같은 원천(보관본)의 행 그대로다. 종가는 양쪽 모두 원 종가(수정주가 아님),
#      시총은 조정에 불변이라 이음매가 새 불연속을 만들지 않는다. 조정 이벤트(`krx_adj_events`)는 전량 DB 에 있다.
#   S5 전부 아니면 강등 — [요청 하한, S) 를 서빙본이 다 덮지 못하면(목록·파일 검증 실패, 이음매 틈) 그 요청은 DB 곡선 + 하한 안내다.
#   S7 쓰는 쪽 불변식 — DB 에서 주간 행을 지우는 롤링은 그 행이 서빙본에 같은 값으로 있을 때만(날짜 ≤ C) 지운다. 그래서 DB 는 언제나
#      S 이후를 빠짐없이 갖는다. 그 불변식이 깨지면(틈) `seam_gap` 이 잡아 강등한다.

def serving_seam(cutoff_dd: str, cold_max_dd: str) -> str:
    """이음매 S = min(B, C + 1일) — 콜드는 price_dd < S, DB 는 price_dd >= S."""
    _parse_dd(cutoff_dd)                                  # 형식 검사(YYYYMMDD 문자열끼리 비교한다)
    return min(cutoff_dd, _fmt(_parse_dd(cold_max_dd) + timedelta(days=1)))


def seam_gap(seam_dd: str, db_floor_dd: str) -> bool:
    """콜드 끝(S)과 DB 주간 구간 시작(floor) 사이에 **거래일이 있을 수 있는 틈**이 있나.

    S ≥ floor 면 틈이 없다(DB 가 S 이후를 다 갖는다). S < floor 여도 그 사이가 주말·1월 1일뿐이면 거래 주가 빠지지 않는다
    (예: coverage 끝이 토요일, DB 주간 구간 시작이 다음 월요일). 그 밖이면 틈 — 곡선에 빈 주가 생기므로 강등한다."""
    if seam_dd >= db_floor_dd:
        return False
    d, end = _parse_dd(seam_dd), _parse_dd(db_floor_dd)
    while d < end:
        if d.isoweekday() <= 5 and not (d.month == 1 and d.day == 1):
            return True
        d += timedelta(days=1)
    return False


def weekly_cold_note(start_dd: str, seam_dd: str) -> str:
    """주간 곡선을 보관본(DB 밖)과 이었을 때의 출처 문장(한국어) — 값·주 정의는 같다."""
    a, s = start_dd, seam_dd
    return (f"주간 시세 {a[:4]}-{a[4:6]}-{a[6:]} ~ {s[:4]}-{s[4:6]}-{s[6:]} 전날은 보관본(DB 밖 서빙본), 그 이후는 DB 에서 읽어 "
            "이었습니다 — 같은 원천·같은 주 정의(주 마지막 거래일)이고 이음매에 겹치는 날짜는 없습니다.")


#: 콜드 서빙이 켜져 있는데 이번에 읽지 못했을 때 하한 안내 뒤에 붙는 한 줄(일시) — 꺼져 있으면 붙지 않는다.
COLD_DEGRADED_NOTE = ("3년 이전 주간 곡선 보관본을 지금 읽지 못해 최근 3년만 줍니다(일시 — 잠시 뒤 다시 부르면 이어 붙여 줍니다).")


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
