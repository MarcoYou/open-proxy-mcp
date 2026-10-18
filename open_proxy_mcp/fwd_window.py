"""컨센서스 이력의 DB 보관 창 — 「몇 주를 DB 에 두나」와 「날짜 목록을 어디서 읽나」를 한 곳에 둔다 (261018).

읽는 쪽 셋(서버 `forward_estimates`, 선행 집계 배치 `scripts/fwd_val_weekly.py`, 밀림 감시
`scripts/drain_backlog_check.py`)이 이 정의 하나를 쓴다. 사본을 두면 감시가 실제 창과 다른 주를 센다.

■ 저장 모양 두 가지 — 읽는 이름 `fwd`(88칸)·`fwd_hist`(18칸)는 어느 쪽에서도 같은 이름·같은 칸이다.
  · 주별 모양(legacy) — 주마다 통째로 담은 사실값 표 위의 뷰. 보관 13주(그중 최근 4주만 88칸).
    날짜 목록은 뷰의 `DISTINCT as_of`(13주라 싸다).
  · 달력 모양(calendar) — 구간형 저장(값이 바뀔 때만 새 판을 쓰고 판마다 유효 구간 [from, to) 를 적는 방식) 위의 뷰.
    게시 달력 `fwd_asof`(게시된 주 한 줄씩)와 그 날 유효한 판을 잇는다. 보관 = 창 `FWD_HOT_WEEKS`(기본 52주),
    창보다 먼저 닫힌 판은 DB 밖 보관본으로 옮겨진다. 날짜 목록은 달력에서 읽는다 — 뷰의 `DISTINCT as_of` 는
    이력 2년이면 0.8초, 달력은 0.05ms(로컬 Postgres 17 합성 이력 실측).
  가르는 법: `fwd_hist` 뷰의 정의가 `fwd_asof` 를 읽는가(`pg_depend`). 표가 **있다**는 것만으로는 가르지 않는다 —
  달력 표는 뷰를 바꾸기 전부터 그림자 뷰용으로 채워진다.

■ 창: `as_of ≥ 최신 as_of − FWD_HOT_WEEKS×7일`.
  창 정리는 `valid_to ≤ 최신 − 창` 인 판만 지운다. 창의 첫날에 유효한 판은 `valid_to` 가 그보다 늦으니 전부 남는다.
  그보다 이른 날짜는 판 일부가 지워졌을 수 있어 **읽지 않는다**(일부 종목만 있는 날을 한 벌로 읽으면 틀린다).
  그래서 창 안 날짜는 주 1벌이면 많아야 FWD_HOT_WEEKS+1 개(양 끝 포함)이고, 52주 전 같은 주 대비 리비전이 된다.
"""
from __future__ import annotations

import os
from datetime import date, timedelta

#: 창 길이 기본값(주). 예산 규칙으로 줄일 수 있는 설정값이다 — 줄여도 창 밖 판은 DB 밖 보관본에 남는다.
DEFAULT_HOT_WEEKS = 52
#: 주별 모양의 보관(교체 전): `fwd` 최신 4주, `fwd_hist` 13주. 감시 기본값과 안내 문구가 쓴다.
LEGACY_FWD_WEEKS = 4
LEGACY_HIST_WEEKS = 13

#: (`fwd_hist` 가 있나, 그 뷰가 게시 달력 `fwd_asof` 를 읽나). 없는 표를 직접 질의하지 않는다 —
#: 서버의 `db.pg_rows` 는 질의 오류를 장애로 보고 풀을 60초 내린다. `to_regclass`·카탈로그 조회는 표가 없어도 오류가 아니다.
PROBE_SQL = (
    "SELECT to_regclass('public.fwd_hist') IS NOT NULL, "
    "to_regclass('public.fwd_asof') IS NOT NULL AND EXISTS ("
    "SELECT 1 FROM pg_depend d JOIN pg_rewrite r ON r.oid = d.objid "
    "WHERE r.ev_class = to_regclass('public.fwd_hist') "
    "AND d.refobjid = to_regclass('public.fwd_asof'))"
)

#: 날짜 목록. 주별 모양은 뷰에서, 달력 모양은 달력에서 창 안만(창 일수는 `%s` 하나 — `window_days()`).
DAYS_SQL_LEGACY = "SELECT DISTINCT as_of FROM fwd_hist ORDER BY 1"
DAYS_SQL_CALENDAR = ("SELECT as_of FROM fwd_asof "
                     "WHERE as_of >= (SELECT max(as_of) FROM fwd_asof) - %s::int ORDER BY 1")
LATEST_SQL_CALENDAR = "SELECT max(as_of) FROM fwd_asof"


def hot_weeks() -> int:
    """창 길이(주) — 환경변수 `FWD_HOT_WEEKS`, 없거나 잘못되면 52. 부를 때마다 읽는다(시험이 바꿀 수 있게)."""
    raw = (os.getenv("FWD_HOT_WEEKS") or "").strip()
    try:
        n = int(raw) if raw else DEFAULT_HOT_WEEKS
    except ValueError:
        return DEFAULT_HOT_WEEKS
    return n if n > 0 else DEFAULT_HOT_WEEKS


def window_days(weeks: int | None = None) -> int:
    """창 일수 = 주 × 7."""
    return 7 * (hot_weeks() if weeks is None else weeks)


def window_start(latest: date, weeks: int | None = None) -> date:
    """창의 첫날(포함) — `latest − 창`. 이 날보다 이른 날짜는 읽지 않는다."""
    return latest - timedelta(days=window_days(weeks))


def window_cond(col: str = "as_of") -> str:
    """달력 모양에서 이력 질의에 붙이는 창 조건(파라미터 `%s` 하나 = 일수). 주별 모양에는 붙이지 않는다 — 그 모양은
    13주만 담아 언제나 창 안이고, 그 질의에 `fwd_asof` 를 쓰면 표가 없어 오류다."""
    return f"{col} >= (SELECT max(as_of) FROM fwd_asof) - %s::int"


def parse_probe(row) -> tuple[bool, bool]:
    """`PROBE_SQL` 한 줄 → (이력 있음, 달력 모양). 모양이 다른 행(시험의 가짜 DB 등)은 (있음, 주별)로 읽는다."""
    if not row:
        return False, False
    exists = bool(row[0])
    on_calendar = bool(row[1]) if len(row) > 1 else False
    return exists, exists and on_calendar
