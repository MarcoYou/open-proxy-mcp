"""컨센서스 이력의 DB 보관 창 — 「몇 주를 DB 에 두나」와 「날짜 목록을 어디서 읽나」를 한 곳에 둔다 (261018).

읽는 쪽 셋(서버 `forward_estimates`, 선행 집계 배치 `scripts/fwd_val_weekly.py`, 밀림 감시
— 비공개 저장소)이 이 정의 하나를 쓴다. 사본을 두면 감시가 실제 창과 다른 주를 센다.

■ 저장 모양 — 읽는 이름 `fwd`(88칸)·`fwd_hist`(18칸)는 같은 이름·같은 칸의 뷰다. 뷰는 게시 달력 `fwd_asof`
  (게시된 주 한 줄씩)와 그 날 유효한 판(구간형 저장 — 값이 바뀔 때만 새 판을 쓰고 판마다 유효 구간 [from, to) 를
  적는다)을 잇는다. 보관 = 창 `FWD_HOT_WEEKS`(기본 52주). 창보다 먼저 닫힌 판은 DB 밖 보관본으로 옮겨진다.
  날짜 목록은 달력에서 읽는다 — 뷰의 `DISTINCT as_of` 는 이력 2년이면 0.8초, 달력은 0.05ms(로컬 Postgres 17
  합성 이력 실측).
  (옛 주별 모양 — 주마다 통째로 담은 사실값 표 — 은 구간형 저장으로 바뀌면서 걷어냈다.)

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

#: 날짜 목록 — 게시 달력에서 창 안만(창 일수는 `%s` 하나 — `window_days()`).
DAYS_SQL_CALENDAR = ("SELECT as_of FROM fwd_asof "
                     "WHERE as_of >= (SELECT max(as_of) FROM fwd_asof) - %s::int ORDER BY 1")
#: 게시 달력의 최신 날짜 — 「최신 스냅샷에 없음」 판정과 창의 기준일.
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
    """이력 질의에 붙이는 창 조건(파라미터 `%s` 하나 = 일수)."""
    return f"{col} >= (SELECT max(as_of) FROM fwd_asof) - %s::int"
