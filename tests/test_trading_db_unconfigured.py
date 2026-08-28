"""trading_data — 「DB 미연결」과 「DB 일시 장애」를 갈라 말한다 (260828 U 실사용 지적 A-1).

BPM 기동 스크립트는 live DB 오염을 막으려고 `DATABASE_URL` 을 **일부러 지운다.** 그 서버에서
scope=firm/market/sector 는 영원히 안 되는데, 종전 안내는 「일시 장애 · 잠시 후 재시도」였다.
재시도가 답이 아닌 상황에 재시도를 권하는 것이라 틀린 안내다.

network 0콜 · DB 0콜 — 라이브 경로는 전부 monkeypatch 한다.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from open_proxy_mcp.services import trading as svc  # noqa: E402
from open_proxy_mcp.tools.trading import (  # noqa: E402
    _STATUS_TITLE,
    _render_firm_live,
    _render_status,
)

_CORP = {"corp_name": "테스트전자", "stock_code": "000000", "corp_code": "00000000"}
_ROW = {"ISU_CD": "000000", "MKT_NM": "KOSPI", "TDD_CLSPRC": "10,000",
        "MKTCAP": "1,000,000,000,000", "LIST_SHRS": "100,000,000"}


@pytest.fixture()
def no_db(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(svc, "_pg_rows", lambda *a, **k: None)          # DB 미설정 = None
    monkeypatch.setattr(svc, "_resolve_listed", _fake_resolve)


async def _fake_resolve(_q):
    return _CORP, None


# ── 1. 두 상태가 절대 한 문구로 합쳐지지 않는다 ──────────────────────────────
def test_status_titles_are_distinct_and_unset_does_not_promise_retry():
    assert _STATUS_TITLE["db_unconfigured"] != _STATUS_TITLE["db_error"]
    unset = _STATUS_TITLE["db_unconfigured"]
    assert "재시도" not in unset and "일시" not in unset, \
        "미연결은 재시도해도 안 된다 — 재시도를 권하는 문구가 들어가면 안 된다"
    assert "재시도" in _STATUS_TITLE["db_error"], "진짜 일시 장애는 재시도가 답이다"


def test_db_error_is_kept_when_url_is_configured(monkeypatch):
    """URL 이 있는데 실패한 것은 여전히 `db_error` — 이 구분을 잃으면 장애가 미연결로 보인다."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")
    assert svc._db_missing_payload("s")["status"] == "db_error"


def test_db_unconfigured_when_url_is_absent(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    p = svc._db_missing_payload("s")
    assert p["status"] == "db_unconfigured"
    assert "재시도해도" in " ".join(p["warnings"])


# ── 2. 미연결이면 최신 1시점을 KRX 라이브로 준다 ────────────────────────────
def test_firm_falls_back_to_krx_live_and_labels_it(no_db, monkeypatch):
    """`price_multiple_data` 는 같은 상황에서 시총을 정상 반환했다 — 같은 경로를 쓴다.
    다만 **시계열이 아니라는 것**이 payload 에 남아야 한다."""
    async def _split():
        return "20260827", {"000000": _ROW}, {}
    monkeypatch.setattr(svc, "_live_split", _split)

    p = asyncio.run(svc.build_firm_series_payload("테스트전자"))
    assert p["status"] == "ok"
    d = p["data"]
    assert d["scope"] == "firm_live", "저장 시계열과 같은 scope 로 위장하면 안 된다"
    assert d["timeseries_available"] is False
    assert d["source"] == "KRX 라이브"
    assert d["series"] == []
    assert d["latest"] == {"asof": "20260827", "close_krw": 10000,
                           "mktcap_krw": 1_000_000_000_000, "list_shrs": 100_000_000}
    joined = " ".join(p["warnings"])
    assert "KRX 라이브" in joined and "시계열 아님" in joined


def test_market_falls_back_to_krx_live_sum(no_db, monkeypatch):
    async def _split():
        return "20260827", {}, {"KOSPI": [_ROW], "KOSDAQ": [dict(_ROW, MKTCAP="500")]}
    monkeypatch.setattr(svc, "_live_split", _split)

    p = asyncio.run(svc.build_cap_agg_payload("market"))
    assert p["status"] == "ok"
    d = p["data"]
    assert d["timeseries_available"] is False and d["series"] == []
    assert {r["market"]: r["cap_krw"] for r in d["latest"]} == {"KS": 1_000_000_000_000, "KQ": 500}


def test_sector_says_it_cannot_fall_back(no_db, monkeypatch):
    """섹터는 WICS 매핑이 DB 에만 있다 — 폴백이 없다는 것을 **그 이유와 함께** 말한다."""
    p = asyncio.run(svc.build_cap_agg_payload("wics_industry"))
    assert p["status"] == "db_unconfigured"
    joined = " ".join(p["warnings"])
    assert "WICS" in joined, "왜 못 주는지가 없으면 사용자는 계속 다시 부른다"
    assert "market" in joined and "firm" in joined, "대신 되는 것을 알려줘야 한다"


def test_live_fallback_failure_is_not_reported_as_ok(no_db, monkeypatch):
    """KRX 키까지 없으면 줄 것이 없다 — 그때 ok 로 위장하지 않는다."""
    async def _split():
        return None, {}, {}
    monkeypatch.setattr(svc, "_live_split", _split)
    for p in (asyncio.run(svc.build_firm_series_payload("테스트전자")),
              asyncio.run(svc.build_cap_agg_payload("market"))):
        assert p["status"] == "db_unconfigured"
        assert "KRX 라이브 폴백도 실패" in " ".join(p["warnings"])


# ── 3. 렌더러가 「시계열 아님」을 실제로 보여준다 ────────────────────────────
def test_firm_live_render_shouts_no_timeseries():
    md = _render_firm_live({
        "subject": "테스트전자(000000)", "warnings": ["W"],
        "data": {"scope": "firm_live", "ticker": "000000", "market": "KOSPI",
                 "as_of": "20260827", "series": [], "timeseries_available": False,
                 "source": "KRX 라이브", "price_adjusted": False, "method": "M",
                 "latest": {"asof": "20260827", "close_krw": 10000,
                            "mktcap_krw": 1_000_000_000_000, "list_shrs": 100_000_000}}})
    assert "시계열 없음" in md and "KRX 라이브" in md
    assert "10,000원" in md and "20260827" in md
    assert "W" in md


def test_unconfigured_status_render_carries_the_reason():
    md = _render_status({"status": "db_unconfigured", "subject": "테스트전자",
                         "warnings": ["이 서버에는 스냅샷 DB 가 연결돼 있지 않습니다"]})
    assert "미연결" in md and "일시 장애" not in md


def test_tool_dispatch_knows_firm_live():
    """payload 에 새 scope 를 넣고 렌더러 등록을 잊으면 KeyError 로 죽는다."""
    import inspect

    from open_proxy_mcp.tools import trading as tool
    assert '"firm_live": _render_firm_live' in inspect.getsource(tool.register_tools)
