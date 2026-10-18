# -*- coding: utf-8 -*-
"""`krx_index_members` — 지수 구성종목 원장(코스피200·코스닥150) 읽기. 네트워크 없음.

261018 신설. 종전에는 원장이 없어 「코스피200」을 KOSPI 시총상위 200 으로 대체했다(처음 만든 날부터 그랬다).
구성종목 선정은 시총 순위가 아니라 업종 대표성·유동성 기준이라 둘은 꽤 다르다 — 실측(20261016): 공식 201종목과
대체 목록이 34종목씩 엇갈렸다(공식에만 있는 하이트진로·오뚜기·종근당…, 대체에만 있는 리츠·인프라펀드·케이뱅크…).
`screener(universe="코스피200")` 는 그 34개 회사의 공시를 놓치고 지수 밖 회사의 공시를 섞어 냈다.

■ 표 — 스키마는 `scripts/krx_index_members_ddl.py`, 적재는 `scripts/krx_index_members.py`(매일, KRX 공식 합계로 확인한 것만)
    index_code · snap_dd · ticker · checked_dd. 기본키 (index_code, snap_dd, ticker).
    snap_dd    = 이 구성이 **처음 확인된** 거래일. 구성이 바뀐 날에만 새 묶음(종목 수만큼 행)이 생긴다.
    checked_dd = 이 구성이 **마지막으로 확인된** 거래일. 구성이 그대로면 적재가 이 칸만 올린다.
■ 읽는 쪽: 가장 늦은 묶음 하나(`latest`). 확인이 시세 기준일보다 `STALE_DAYS` 넘게 뒤처지면 경고 문장(`stale_note`).
  원장을 못 읽으면(표 없음·DB 장애·빈 표) None — 호출자가 종전처럼 시총 순위로 대체하고 그 사실을 밝힌다.

`read_rows` 는 호출 모듈의 DB 읽기 함수(`db.pg_rows` 계약: None = 미설정·장애, [] = 없음)다 — `krx_daily.latest_day` 와 같은 방식.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from open_proxy_mcp.market_codes import KQ, KS


@dataclass(frozen=True, slots=True)
class IndexSpec:
    code: str      # 유니버스 문법 이름 = 표의 index_code
    label: str     # 사람이 읽는 이름
    market: str    # KS / KQ — 원장을 못 읽을 때 시총 순위 대체에 쓰는 시장
    size: int      # 정원. 실제 구성은 분할 신설회사 편입 등으로 잠시 늘 수 있다(20261016 코스피200 201종목)
    prefix: str    # 시총 순위 대체 스펙의 접두(kospi:N · kosdaq:N)


INDEXES: dict[str, IndexSpec] = {
    "kospi200": IndexSpec("kospi200", "KOSPI200", KS, 200, "kospi"),
    "kosdaq150": IndexSpec("kosdaq150", "KOSDAQ150", KQ, 150, "kosdaq"),
}

#: 마지막 확인이 시세 기준일보다 이 달력 일수를 넘게 뒤처지면 경고한다. 적재는 매일 돌아 평소 차이는 0~3일(주말),
#: 긴 연휴 뒤 첫 거래일 아침(적재 전)에도 8~9일이다. 그보다 길면 적재가 며칠째 확인에 실패한 것이다.
STALE_DAYS = 10

LATEST_SQL = (
    "SELECT snap_dd, checked_dd, ticker FROM krx_index_members "
    "WHERE index_code=%s AND snap_dd=(SELECT max(snap_dd) FROM krx_index_members WHERE index_code=%s)"
)


@dataclass(frozen=True, slots=True)
class Members:
    index_code: str
    snap_dd: str          # 이 구성이 처음 확인된 거래일
    checked_dd: str       # 마지막으로 확인된 거래일
    tickers: frozenset[str]


def latest(index_code: str, read_rows: Callable[..., Any]) -> Members | None:
    """가장 늦은 구성 묶음. 못 읽었거나(None) 비었으면([]) None — 둘 다 「원장을 쓸 수 없다」로 같다."""
    rows = read_rows(LATEST_SQL, (index_code, index_code))
    if not rows or any(len(r) < 3 for r in rows):    # 모양이 다른 응답도 「쓸 수 없다」 — 서빙 경로를 죽이지 않는다
        return None
    tickers = frozenset((r[2] or "").strip() for r in rows if r[2])
    if not tickers:
        return None
    # 한 묶음의 checked_dd 는 한 트랜잭션에서 함께 오른다. 어긋나 있으면 가장 이른 값(덜 믿는 쪽)을 쓴다.
    return Members(index_code=index_code, snap_dd=rows[0][0], checked_dd=min(r[1] for r in rows), tickers=tickers)


def days_behind(checked_dd: str, ref_dd: str | None) -> int:
    """`ref_dd`(시세 기준일)보다 `checked_dd` 가 며칠(달력) 뒤처졌나. 비교할 수 없거나 앞서 있으면 0."""
    try:
        a = datetime.strptime(checked_dd, "%Y%m%d")
        b = datetime.strptime(ref_dd or "", "%Y%m%d")
    except ValueError:
        return 0
    return max(0, (b - a).days)


def stale_note(spec: IndexSpec, members: Members, ref_dd: str | None) -> str:
    """원장 확인이 오래됐으면 사용자에게 붙일 문장, 아니면 빈 문자열."""
    lag = days_behind(members.checked_dd, ref_dd)
    if lag <= STALE_DAYS:
        return ""
    return (f"{spec.label} 구성종목 원장은 {members.checked_dd} 에 마지막으로 확인됐다(시세 기준일 {ref_dd} 보다 {lag}일 앞) — "
            "그 뒤의 편입·편출은 반영되지 않았을 수 있다.")
