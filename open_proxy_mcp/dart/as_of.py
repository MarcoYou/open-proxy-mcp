"""공시 조회의 시점 경계 — `as_of` 게이트.

**「지금 아는 것」과 「그때 알 수 있던 것」을 가른다** (CLAUDE.md 작업 원칙 5).
우리는 전 기간 DART 를 손에 쥐고 있지만, 판단 기준일에 그 공시가 **접수돼 있었는지**는
별개다. 섞으면 look-ahead — 그때는 볼 수 없던 문서로 그때의 판단을 만들게 된다.

실측 사고(2026-08-28, 금호석유화학 2026-03-26 정기주총 사전 권고):
  · 기업지배구조보고서공시 **2026-06-01**(주총 66일 후) — 미준수 지표 6건이 여기서 인용됐다.
    그 보고서의 회사 설명문에는 「2026년 3월 26일 정기주주총회에서 정관변경을 **완료**하였으며」
    가 그대로 실려 있었다. 우리가 표를 던지라고 조언하는 그 주총의 **결과**를 근거로 쓴 셈이다.
  · 대량보유 상황보고 **2026-04-07**(주총 12일 후) — 지분 구조 근거.

이 모듈은 **호출 스택 전체에 걸리는 한 겹의 문**이다. `DartClient.search_filings` 는
모든 공시 목록 조회(list.json)의 유일한 통로라, 여기서 `end_de` 를 잘라 두면 어느 upstream
서비스가 무엇을 부르든 기준일 이후 접수분은 애초에 손에 들어오지 않는다. 서비스마다
`as_of` 인자를 심는 방식은 한 곳만 빠뜨려도 구멍이 나고, 그 구멍은 조용하다.

기본값은 빈 문자열 = **게이트 없음**. 켠 적 없는 호출의 동작은 종전과 완전히 같다.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

#: YYYYMMDD. "" 이면 게이트 없음(종전 동작).
_AS_OF: ContextVar[str] = ContextVar("dart_as_of", default="")

#: 이 게이트가 실제로 잘라낸 조회 기록 — 「무엇을 안 봤나」를 산출물에 밝히기 위한 것.
#: 값은 (원래 end_de, 잘린 end_de) 튜플의 리스트. None 이면 수집하지 않는다.
_CLAMPS: ContextVar[list[tuple[str, str]] | None] = ContextVar("dart_as_of_clamps", default=None)


def set_as_of(as_of: str, *, collect: bool = True) -> tuple[Token, Token]:
    """게이트를 켠다. `reset_as_of(tokens)` 로 되돌린다.

    `asyncio.gather`/`create_task` 로 갈라지는 하위 코루틴은 생성 시점의 컨텍스트를
    복사해 가므로, gather **앞에서** 켜면 모든 worker 에 그대로 걸린다.
    """
    normalized = as_of if (len(as_of) == 8 and as_of.isdigit()) else ""
    return _AS_OF.set(normalized), _CLAMPS.set([] if collect else None)


def reset_as_of(tokens: tuple[Token, Token]) -> None:
    as_of_token, clamps_token = tokens
    _AS_OF.reset(as_of_token)
    _CLAMPS.reset(clamps_token)


def get_as_of() -> str:
    return _AS_OF.get()


def clamps() -> list[tuple[str, str]]:
    return list(_CLAMPS.get() or [])


def note_row_drop(endpoint: str, dropped: int) -> None:
    """날짜 인자가 없는 API 에서 기준일 이후 행을 걷어냈다는 기록."""
    log = _CLAMPS.get()
    if log is not None and dropped > 0:
        log.append((f"{endpoint}:{dropped}rows", _AS_OF.get()))


def clamp_end_de(end_de: str) -> str:
    """검색 종료일을 기준일까지 당긴다. 게이트가 꺼져 있으면 원값 그대로."""
    as_of = _AS_OF.get()
    if not as_of or not end_de:
        return end_de
    if end_de <= as_of:
        return end_de
    log = _CLAMPS.get()
    if log is not None:
        log.append((end_de, as_of))
    return as_of


def window_is_empty(bgn_de: str, end_de: str) -> bool:
    """기준일을 적용하고 나니 창이 뒤집혔나 — 즉 이 조회 구간 전체가 기준일 이후인가."""
    return bool(bgn_de and end_de and bgn_de > end_de)
