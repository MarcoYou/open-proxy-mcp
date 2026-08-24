"""screener 유니버스 — 260824. 시장 리터럴이 DB 값과 어긋나 **조용히 전체시장으로 빠지던** 결함.

증상이 에러가 아니라 **0건**이었다. `krx_weekly` 는 260823 개명 뒤 KS/KQ 를 담는데
`resolve_universe` 가 "KOSPI" 를 넘겨 질의가 0행을 냈고, `_rank` 가 그걸 「조회 실패」로
읽어 `allowed=None`(=전체시장)로 대체했다. 사용자는 kospi200 을 물었는데 2,764종목을 받고,
그러면 details 도 안 돌아(유니버스가 넓어서) 초점 없는 덤프만 돌아왔다.

실측(260824, 수정 전/후):
  _krx_top_mktcap(market="KOSPI") → 0종목  /  "KS" → 200종목
  screener(kospi200, last_7d, details=ON): hit 581·details 0 → hit 48·details 29

같은 줄 바로 위(`market:kospi`)는 상수를 제대로 쓰고 있었다 — **한쪽만 고쳐진** 형태다.
그래서 호출부 상수와 **경계 정규화를 둘 다** 넣었다. 이 테스트는 경계 쪽을 잠근다.
"""
from __future__ import annotations

import pytest

from open_proxy_mcp.market_codes import KQ, KS


class _Rec:
    """psycopg 대역 — 실제로 어떤 params 로 물었는지 잡는다."""

    def __init__(self, sink, rows):
        self.sink, self.rows = sink, rows

    def __enter__(self): return self
    def __exit__(self, *a): return False

    def execute(self, sql, params=()):
        self.sink.append((sql, params))
        return type("R", (), {"fetchall": lambda s: self.rows})()


@pytest.fixture
def rec(monkeypatch):
    sink: list = []
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")
    import psycopg
    monkeypatch.setattr(psycopg, "connect",
                        lambda *a, **k: _Rec(sink, [("005930",), ("000660",)]))
    return sink


@pytest.mark.parametrize("given,expected", [
    ("KOSPI", KS), ("kospi", KS), (KS, KS),
    ("KOSDAQ", KQ), ("kosdaq", KQ), (KQ, KQ),
])
def test_top_mktcap_normalizes_market_before_querying(rec, given, expected):
    """어떤 철자로 들어와도 **DB 값**으로 바꿔서 묻는다."""
    from open_proxy_mcp.services.screener import _krx_top_mktcap
    _krx_top_mktcap(200, "20260821", given)
    sql, params = rec[-1]
    assert expected in params, f"{given!r} 를 그대로 물었다: {params}"
    assert given not in params or given == expected


@pytest.mark.parametrize("given,expected", [("KOSPI", KS), ("KOSDAQ", KQ), (KS, KS)])
def test_market_codes_normalizes_too(rec, given, expected):
    from open_proxy_mcp.services.screener import _krx_market_codes
    _krx_market_codes(given, "20260821")
    _, params = rec[-1]
    assert expected in params


def test_no_market_filter_when_market_is_none(rec):
    """전체시장 랭킹(top_mktcap:N)은 시장 조건을 걸지 않는다 — 걸면 한 시장만 나온다."""
    from open_proxy_mcp.services.screener import _krx_top_mktcap
    _krx_top_mktcap(50, "20260821", None)
    sql, params = rec[-1]
    assert "market=" not in sql
    assert params == ("20260821", 50)


def test_call_sites_use_constants_not_literals():
    """호출부도 상수를 쓴다. 경계 정규화가 있어도 리터럴을 남기면 다음 개명 때 또 샌다 —
    두 곳 다 지키는 것이 이 결함의 교훈이다."""
    import inspect

    from open_proxy_mcp.services import screener
    src = inspect.getsource(screener.resolve_universe)
    for bad in ('_rank(n, "KOSPI"', '_rank(n, "KOSDAQ"', '_rank(200, "KOSPI"'):
        assert bad not in src, f"리터럴이 남아 있다: {bad}"
    assert "MKT_KS" in src and "MKT_KQ" in src


def test_empty_result_is_reported_as_unresolved_not_silently_widened(rec, monkeypatch):
    """DB 가 정말 비면 `resolved=False` 로 **말하고** 전체시장으로 간다.

    조용히 넓히는 것 자체는 fail-open 이라 유지하되, 산출물이 그 사실을 밝혀야 한다 —
    이번 결함이 아팠던 이유는 넓혀서가 아니라 **말하지 않아서**다.
    """
    import asyncio

    from open_proxy_mcp.services import screener
    monkeypatch.setattr(screener, "_krx_latest_dd", lambda: "20260821")
    monkeypatch.setattr(screener, "_krx_top_mktcap", lambda *a, **k: set())
    uf = asyncio.run(screener.resolve_universe("kospi200"))
    assert uf.resolved is False
    assert uf.allowed is None
    assert "전체시장" in uf.notice
