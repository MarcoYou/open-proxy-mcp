# -*- coding: utf-8 -*-
"""업종분류 축 이름 — 중립 이름·사람 말·옛 인자 값이 같은 축으로 모인다 (260921). network·DB 0.

공개 인자는 「대분류」·「중분류」로 바꿨고, DB scheme 값은 DB 변경 전까지 그대로다. 옛 값을 넣던 호출이
조용히 다른 축으로 떨어지거나 invalid 가 되면 안 된다.
"""
from __future__ import annotations

import pytest

from open_proxy_mcp.services import sector_class as C
from open_proxy_mcp.services import trading as T


@pytest.mark.parametrize("raw,want", [
    ("", C.MID), (None, C.MID), ("  ", C.MID),
    ("중분류", C.MID), ("대분류", C.MAJOR), ("하위업종", C.MID),
    ("industry", C.MID), ("sector", C.MAJOR), ("Industry", C.MID),
    (C.DB_SCHEME[C.MID], C.MID), (C.DB_SCHEME[C.MAJOR], C.MAJOR),
    ("ksic", "ksic"), ("KSIC", "ksic"), ("market", "market"),
])
def test_canon_maps_words_and_old_values_to_one_axis(raw, want):
    assert C.canon(raw, C.MID) == want


def test_db_scheme_round_trip_and_pass_through():
    for name in (C.MAJOR, C.MID):
        assert C.canon(C.db_scheme(name), "x") == name
    assert C.db_scheme("ksic") == "ksic" and C.db_scheme("market") == "market"


def test_trading_sector_schemes_use_neutral_names():
    assert set(T._SCHEMES) == {"market", C.MAJOR, C.MID}
