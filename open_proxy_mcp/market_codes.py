# -*- coding: utf-8 -*-
"""시장 코드 — DB 는 `KS`/`KQ`, 사람에게는 `KOSPI`/`KOSDAQ`.

260823: DB 컬럼 `mkt`(6개 표)를 `market` 으로 통일하면서 값도 `KOSPI`/`KOSDAQ` →
`KS`/`KQ` 로 줄였다(141만 행). 블룸버그 접미사(`.KS`/`.KQ`) 관행과 같아 티커 표기에 그대로 쓴다.

**변환은 여기 한 곳에서만 한다.** 리터럴을 코드에 흩뿌리면 값이 또 바뀔 때
`WHERE market='KOSPI'` 가 조용히 빈 결과를 낸다 — 에러가 아니라 0건이라 안 보인다.
KRX API 는 `MKT_NM` 으로 `KOSPI`/`KOSDAQ` 를 주므로 적재 시 `to_db()` 를 태운다.
"""
from __future__ import annotations

KS, KQ = "KS", "KQ"

_TO_DB = {"KOSPI": KS, "KOSDAQ": KQ, KS: KS, KQ: KQ}
_TO_LABEL = {KS: "KOSPI", KQ: "KOSDAQ"}


def to_db(name: str | None) -> str | None:
    """KRX `MKT_NM`(또는 이미 코드) → DB 저장값. 모르는 값은 그대로 통과시킨다."""
    return _TO_DB.get((name or "").strip().upper(), name)


def to_label(code: str | None) -> str | None:
    """DB 저장값 → 사람이 읽는 이름. 렌더·리포트에서 쓴다."""
    return _TO_LABEL.get((code or "").strip().upper(), code)
