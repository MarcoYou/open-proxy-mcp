"""업종분류 `wise_sector` 위에서 **날짜·업종 목록을 고르는** 질의의 한 곳 — 뷰 모드면 작은 밑 표를 읽는다.

`wise_sector` 는 구간 표(`wise_sector_iv`) × 스냅샷 날짜 표(`wise_sector_snap`) × 번호표의 **뷰**다(E1, `scripts/wise_sector_iv_ddl.py`).
그 뷰에 `MAX(snap_dd)`·`DISTINCT snap_dd`·`DISTINCT sector` 를 물으면 서버가 요청마다 약 10만 행(운영 102,895행 = 구간 6,159 × 스냅샷 38 …)을 펼친다.
스냅샷이 3개이던 때는 5ms 였는데 p1_wics 백필(10-22)로 38개가 되어 약 10배 느려졌다(서버 실행 시간, 운영 읽기 전용 2회):

    MAX(snap_dd) FROM wise_sector     55.4 / 48.9 ms  →  max(dd) FROM wise_sector_snap      0.4 / 0.3 ms
    DISTINCT sector FROM wise_sector  45.2 / 43.4 ms  →  DISTINCT sector FROM wise_sector_iv  2.0 / 1.9 ms
    DISTINCT snap_dd FROM wise_sector 56.4 / 54.2 ms  →  dd FROM wise_sector_snap            0.3 / 0.3 ms

그래서 `wise_sector` 가 **뷰일 때만** 날짜는 `wise_sector_snap`(dd) 에서, 업종 목록은 `wise_sector_iv`(sector) 에서 읽는다. 같은 답이다 — 운영에서 확인: 최댓값·스냅샷 목록·업종 목록
셋 모두 같고 구간이 하나도 덮지 않는 빈 스냅샷이 0개(`scripts/wise_sector_iv_ddl.py` 의 무결성 검사 「빈 스냅샷」). 표 모드(교체 전·되돌린 뒤)는 종전 SQL 그대로다.

· 모드 판별은 `pg_class.relkind` 한 질의를 `_TTL` 초 캐시한다(DB 주소가 바뀌면 다시). 한 문장 `COALESCE(CASE relkind='v' …)` 는 표 모드 DB 에는 `wise_sector_snap` 이
  없어 구문 분석 단계에서 실패하므로 쓰지 않는다.
· `--rollback`(뷰 → 표)은 `wise_sector_snap` 을 남겨 둬서 되돌린 뒤에는 그 표가 낡는다 — 모드를 읽어 표 모드면 쓰지 않는다. 되돌린 직후 캐시 기간(`_TTL`)에는
  낡은 표를 읽을 수 있으니 되돌리기 절차에서 서버를 다시 띄운다(또는 `reset()`).
· 읽기가 None(미설정·장애·42P01 등)이면 종전 뷰 SQL 로 한 번 더 간다(`query`) — 빠른 길이 막혀도 답은 같다(`sector_universe._WEEK_PICK_SQL` 과 같은 꼴).
"""
from __future__ import annotations

import os
import time
from typing import Any, Callable

from open_proxy_mcp import db

#: 모드 캐시 유지 시간(초). 뷰 ↔ 표 교체는 드문 운영 작업이다.
_TTL = 60.0
KIND_SQL = "SELECT c.relkind::text FROM pg_class c WHERE c.oid = to_regclass('wise_sector')"

_state: dict[str, Any] = {"at": -1e9, "view": False, "url": None}


def reset() -> None:
    """모드 캐시를 비운다(시험·되돌리기 절차용)."""
    _state.update(at=-1e9, view=False, url=None)


def is_view() -> bool:
    """`wise_sector` 가 뷰인가. 표이거나 없거나 못 읽으면 False — 종전(뷰 위 SQL) 동작을 한다."""
    now = time.monotonic()
    url = os.environ.get("DATABASE_URL")
    if _state["url"] == url and now - _state["at"] < _TTL:
        return bool(_state["view"])
    rows = db.pg_rows(KIND_SQL)
    if rows is None:               # DB 미설정·장애 — 캐시하지 않는다(다음 호출이 다시 본다)
        return False
    _state.update(at=now, view=bool(rows) and rows[0][0] == "v", url=url)
    return bool(_state["view"])


def max_dd(fast: bool) -> str:
    """최신 스냅샷 날짜(YYYYMMDD) 스칼라 부분질의. `fast` = 뷰 모드의 빠른 길(`query` 의 `build` 인자)."""
    return "(SELECT max(dd) FROM wise_sector_snap)" if fast else "(SELECT MAX(snap_dd) FROM wise_sector)"


def min_dd(fast: bool) -> str:
    return "(SELECT min(dd) FROM wise_sector_snap)" if fast else "(SELECT min(snap_dd) FROM wise_sector)"


def max_dd_le(fast: bool) -> str:
    """`%s` 이하 가장 최근 스냅샷 날짜 스칼라 부분질의(자리표 하나)."""
    return ("(SELECT max(dd) FROM wise_sector_snap WHERE dd <= %s)" if fast
            else "(SELECT max(snap_dd) FROM wise_sector WHERE snap_dd <= %s)")


def snaps_select(fast: bool) -> str:
    """스냅샷 날짜 목록 SELECT — 칸 이름 `snap_dd`."""
    return "SELECT dd AS snap_dd FROM wise_sector_snap" if fast else "SELECT DISTINCT snap_dd FROM wise_sector"


def sector_names_select(fast: bool) -> str:
    return ("SELECT DISTINCT sector FROM wise_sector_iv WHERE sector IS NOT NULL ORDER BY 1" if fast
            else "SELECT DISTINCT sector FROM wise_sector WHERE sector IS NOT NULL ORDER BY 1")


def query(build: Callable[[bool], str], params: tuple, runner: Callable[[str, tuple], list[tuple] | None]) -> list[tuple] | None:
    """`build(fast)` 가 만든 SQL 을 `runner` 로 돌린다. 빠른 길(뷰 모드)이 None 이면 종전 SQL 로 한 번 더, 그때 모드 캐시도 비운다(교체 직후 오판 방지)."""
    fast = is_view()
    rows = runner(build(fast), params)
    if rows is None and fast:
        reset()
        rows = runner(build(False), params)
    return rows
