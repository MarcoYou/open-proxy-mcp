"""업종분류 축의 이름 — 공개 인자·출력은 중립 이름, DB 값은 옛 이름 (260921).

공개 인자와 출력은 「대분류」(10)·「중분류」(28)를 쓴다. DB 의 scheme 값(`opm_val_market`·`opm_val_fwd`·
`krx_cap_agg`·`div_yield_hist`)은 DB 변경 전까지 옛 값을 그대로 쓰므로, 그 대응을 이 모듈 한 곳에 둔다.
옛 인자 값도 계속 받는다(호환).
"""
from __future__ import annotations

MAJOR = "대분류"
MID = "중분류"

#: 중립 이름 → DB scheme 값. DB 를 바꾸기 전까지 옛 값은 여기서만 쓴다.
DB_SCHEME: dict[str, str] = {MAJOR: "wics_sector", MID: "wics_industry"}

_ALIASES: dict[str, str] = {
    MAJOR: MAJOR, "대": MAJOR, "sector": MAJOR, "major": MAJOR,
    MID: MID, "중": MID, "하위업종": MID, "industry": MID, "mid": MID,
    **{db: name for name, db in DB_SCHEME.items()},  # 옛 인자 값
}


def canon(raw: str | None, default: str) -> str:
    """사람 말·옛 값을 중립 이름으로 바꾼다. 업종분류가 아닌 값(ksic·market 등)은 소문자로 그대로 돌려준다."""
    s = (raw or "").strip()
    if not s:
        return default
    return _ALIASES.get(s) or _ALIASES.get(s.lower()) or s.lower()


def db_scheme(name: str) -> str:
    """중립 이름 → DB scheme 값. 업종분류가 아닌 값은 그대로."""
    return DB_SCHEME.get(name, name)
