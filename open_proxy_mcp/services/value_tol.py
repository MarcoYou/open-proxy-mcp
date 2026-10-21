"""저장값 비교 허용 오차 — 한 곳 (261021 DATA-Q7).

배치가 다시 계산한 실수 값을 **저장된 값과 견줘 달라진 행만 쓸 때**의 「같다」를 한 곳에서 정한다. 같은 입력이어도 읽는 자릿수에 따라 끝자리가 다른 값이 나온다:
운영 DB 새 연결의 기본 `extra_float_digits` 는 0 이라 실수가 15자리로 읽히는데(운영 배치가 이렇게 읽고 쓴다), 정밀 읽기(3 — 17자리)로 같은 칸을 다시 계산하면 외화 환산 값처럼
17자리를 다 쓰는 입력(900xxx·950xxx 외국 상장사 등 17종목)에서 합이 상대 2e-15 이하로 다르다. 이 차이를 「달라졌다」로 세면 재파생이 17종목을 쓰고, 다음 배치가 다시 되돌린다(매일 왕복).

허용 오차: 상대 `REL_TOL`(1e-9 — 끝자리 잡음의 수만 배, 실제 재무 값 변화의 수천만분의 일) **또는** 절대 `ABS_TOL`(0.01 — 합이 0 근처로 상쇄될 때의 잡음). 둘 중 큰 쪽 안이면 같다.
값이 빈 칸(NULL)이면 빈 칸끼리만 같다. 순수 함수(`same`·`differs`)와 SQL 식(`differs_sql`)이 같은 규칙이다 — 시험이 둘을 대 본다.

부르는 곳: 재무 요약 재파생(`scripts/market_fund_quarterly.derive_fundamentals`). 값이 달라졌는지 견주는 다른 자리가 같은 문제를 겪으면 이 모듈을 쓴다(허용 오차를 곳곳에 따로 두지 않는다).
"""
from __future__ import annotations

#: 상대 허용 오차 — `max(|a|, |b|)` 의 이 비율 안이면 같다.
REL_TOL = 1e-9
#: 절대 허용 오차(원) — 합이 0 근처로 상쇄될 때의 부동소수 잡음.
ABS_TOL = 0.01


def same(a, b) -> bool:
    """두 저장값이 같은가 — 둘 다 빈 칸이면 같고, 하나만 비면 다르다. 값이면 `ABS_TOL` 또는 `REL_TOL * max(|a|, |b|)` 안이면 같다."""
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) <= max(ABS_TOL, REL_TOL * max(abs(a), abs(b)))


def differs(a, b) -> bool:
    return not same(a, b)


def differs_sql(col: str, param: str) -> str:
    """`differs` 의 SQL 식 — 열(또는 식) `col` 이 `param`(예: `%(x)s::float8`)과 허용 오차 밖으로 다른가. 빈 칸 규칙도 같다(`IS DISTINCT FROM` 이 걸러낸 뒤 값끼리만 오차를 본다)."""
    return (f"({col} IS DISTINCT FROM {param} AND NOT ({col} IS NOT NULL AND {param} IS NOT NULL "
            f"AND abs({col} - {param}) <= GREATEST({ABS_TOL!r}, {REL_TOL!r} * GREATEST(abs({col}), abs({param})))))")
