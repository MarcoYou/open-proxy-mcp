"""분기 재무 수집(`market_fund_quarterly --fetch`)은 야후 대체 환율이어도 **원화로 환산해** 저장한다 (261019, #140 리뷰). network 0.

#140 은 대체값을 영구 환율 표에 굳히지 않게 하면서 수집기에 경고 한 줄을 더했는데, 그 갈래를 `elif` 로 붙여 환산(`else`)을 건너뛰었다 —
ECOS 를 못 받은 날짜의 외화 보고사 분기 칸이 **원통화 그대로**(USD 면 원화의 약 1/1,400) `dart_finstat_q` 에 「ok」로 들어갈 뻔했다.
연간 수집(`market_val_series`)·정정 칸 채우기 두 경로는 경고 뒤 환산하는 꼴이라 맞았다. 종전(#140 앞)엔 대체값으로 환산해 담았다.

DART 0콜 — 가짜 클라이언트. Postgres 17(도커 없으면 건너뜀, `tests/conftest.py` 의 `pg17`).
"""
from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import AsyncMock

import pytest

from scripts import market_fund_quarterly as Q

psycopg = pytest.importorskip("psycopg")

FINSTAT_Q_DDL = """CREATE TABLE dart_finstat_q(
  ticker text, fy int, quarter int, reprt_code text, fs text,
  ni_cum double precision, eq double precision, fetched text,
  ni_case text, eq_case text,
  ni_cum_raw double precision, eq_raw double precision,
  ni_cum_restated double precision, eq_restated double precision, restate_why text,
  PRIMARY KEY(ticker, fy, quarter))"""
FINSTAT_Y_DDL = """CREATE TABLE dart_finstat_y(
  ticker text, fy int, fs text, ni double precision, eq double precision, fetched text,
  ni_restated double precision, eq_restated double precision, PRIMARY KEY(ticker, fy))"""
RATE = 1385.2


class _Today(date):
    @classmethod
    def today(cls):
        return cls(2026, 10, 19)


def _row(sj, aid, nm, ordv, **amt):
    return {"sj_div": sj, "account_id": aid, "account_nm": nm, "ord": str(ordv), "currency": "USD",
            "rcept_no": "20250814000001", **{k: str(v) for k, v in amt.items()}}


def usd_h1():
    """USD 보고 외국 상장사의 2025 반기(연결) — 자산 = 부채 + 자본, 전기 자본 칸 포함. 단위 USD."""
    return [
        _row("BS", "ifrs-full_Assets", "자산총계", 7, thstrm_amount=1_000_000_000, frmtrm_amount=950_000_000),
        _row("BS", "ifrs-full_Liabilities", "부채총계", 20, thstrm_amount=600_000_000, frmtrm_amount=570_000_000),
        _row("BS", "ifrs-full_Equity", "자본총계", 26, thstrm_amount=400_000_000, frmtrm_amount=380_000_000),
        _row("BS", "ifrs-full_EquityAttributableToOwnersOfParent", "지배기업지분", 27, thstrm_amount=390_000_000,
             frmtrm_amount=371_000_000),
        _row("CIS", "ifrs-full_ProfitLossAttributableToOwnersOfParent", "지배기업지분순이익", 30,
             thstrm_amount=20_000_000, thstrm_add_amount=50_000_000),
    ]


class _FakeDart:
    async def get_fnltt_singl_acnt_all(self, corp_code, bsns_year, reprt_code="11011", fs_div="CFS"):
        from open_proxy_mcp.dart.client import DartClientError
        if (corp_code, str(bsns_year), reprt_code, fs_div) == ("0000000U", "2025", "11012", "CFS"):
            return {"status": "000", "list": usd_h1()}
        raise DartClientError("013", "조회된 데이타가 없습니다.")


@pytest.fixture
def udb(pg17, monkeypatch):
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(FINSTAT_Q_DDL)
        c.execute(FINSTAT_Y_DDL)
        c.execute(Q.DDL_FUNDAMENTALS)
        c.execute("INSERT INTO dart_fundamentals (ticker, corp_code, fetched) VALUES ('U', '0000000U', 'ok')")
    monkeypatch.setenv("DATABASE_URL", pg17)
    monkeypatch.setattr(Q, "SLEEP", 0.0)
    monkeypatch.setattr(Q, "date", _Today)
    monkeypatch.setattr("open_proxy_mcp.dart.client.get_dart_client", lambda: _FakeDart())
    monkeypatch.setattr(Q, "fx_to_krw", AsyncMock(return_value=RATE))
    return pg17


def _stored(url):
    with psycopg.connect(url) as c:
        return c.execute("SELECT ni_cum, eq, fetched FROM dart_finstat_q WHERE ticker='U' AND fy=2025 AND quarter=2").fetchall()


@pytest.mark.parametrize("source", ["ecos", "yahoo"])
def test_quarter_cell_is_stored_in_krw_whatever_the_rate_source(udb, monkeypatch, capsys, source):
    monkeypatch.setattr(Q, "fx_source", lambda cur, dd=None: source)
    asyncio.run(Q.fetch([2025], conc=1, only={("U", 2025, 2)}))
    # 대체값이어도 원화로 — #140 머리(elif)에선 (50,000,000.0, 390,000,000.0, 'ok') 원통화 그대로였다
    assert _stored(udb) == [(50_000_000 * RATE, 390_000_000 * RATE, "ok")]
    out = capsys.readouterr().out
    assert ("::warning::[환율] U 2025Q2 USD 20250630 — ECOS 대신 야후 대체값" in out) == (source == "yahoo")
