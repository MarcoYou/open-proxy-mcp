"""분기 재무 수집(`scripts/market_fund_quarterly.py --fetch`)이 「자료 없음」을 다시 보는지 고정한다 (261018).

배경: 재개(resume) 규칙이 ok 와 nodata 를 모두 「완료」로 봤다. 분기 마감 직후 첫 실행 때 아직 보고서를 안 낸
회사는 nodata 로 적히고, 그 뒤에 내도 다시 받지 않았다. 10-18 운영 실측 — 2026 반기 칸이 nodata 인 63종목 가운데
24종목이 마감(8/14) 뒤 8/24~10/07 에 반기보고서를 냈는데도 비어 있었고(외국 상장사 10 포함), 그래서 업종 비교·
`firm_history` 의 PER(TTM)·PBR(MRQ)가 N/M 이었다.

규칙: nodata 는 그 분기의 공시 마감 뒤 `NODATA_RETRY_DAYS`(400)일 동안 매 실행 다시 받는다. ok 는 그대로 완료,
err 는 종전처럼 다음 실행에 다시 받는다. 파생(`--derive`)은 값이 바뀐 종목만 고쳐 쓰고 `--tickers` 로 좁힐 수 있다.

DART 0콜 — 가짜 클라이언트. Postgres 17(도커 없으면 건너뜀, `tests/conftest.py` 의 `pg17`).
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

from scripts import market_fund_quarterly as Q

TODAY = date(2026, 10, 18)


# ── 규칙(DB 없이) ────────────────────────────────────────────────────────────

def test_retry_window_starts_at_the_deadline_and_lasts_nodata_retry_days():
    assert Q.NODATA_RETRY_DAYS == 400
    assert Q._retry_nodata(2026, 2, today=date(2026, 8, 14))            # 마감 당일
    assert not Q._retry_nodata(2026, 3, today=TODAY)                    # 3분기 마감(11/14) 전
    assert Q._retry_nodata(2026, 1, today=TODAY)
    assert Q._retry_nodata(2025, 3, today=TODAY)                        # 338일
    assert not Q._retry_nodata(2025, 2, today=TODAY)                    # 430일 — 지난 해 반기는 다시 안 본다
    assert Q._retry_nodata(2025, 2, today=TODAY, days=600)              # 일회성 쓸어 담기는 기간을 넓혀서
    # 경계: 마감 뒤 400일째까지
    assert Q._retry_nodata(2025, 2, today=date(2026, 9, 18))            # 2025-08-14 + 400일
    assert not Q._retry_nodata(2025, 2, today=date(2026, 9, 19))


def test_resume_sets_keep_ok_done_retry_recent_nodata_and_errors():
    rows = [
        ("A", 2026, 2, "nodata"),     # 마감 뒤 65일 — 다시
        ("B", 2024, 2, "nodata"),     # 2년 전 — 완료로 둔다(리츠 1·3분기처럼 원래 없는 칸이 매번 콜을 먹지 않게)
        ("C", 2026, 2, "ok"),
        ("D", 2026, 1, "err:[020] 한도"),
        ("E", 2026, 1, None),         # 종전과 같이 완료
    ]
    done, retry = Q._resume_sets(rows, today=TODAY)
    assert retry == {("A", 2026, 2)}
    assert done == {("B", 2024, 2), ("C", 2026, 2), ("E", 2026, 1)}
    done, retry = Q._resume_sets(rows, nodata_days=1000, today=TODAY)
    assert retry == {("A", 2026, 2), ("B", 2024, 2)}


# ── 수집 → 파생, Postgres 17 ─────────────────────────────────────────────────

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


def _report(ni_cum: int, eq: int) -> list[dict]:
    """반기보고서 응답 모양 — 지배 순이익(누적)·지배 자본과 항등식(자산=부채+자본)이 맞는 총계."""
    def row(sj, aid, nm, ordv, **amt):
        return {"sj_div": sj, "account_id": aid, "account_nm": nm, "ord": str(ordv), "currency": "KRW",
                **{k: str(v) for k, v in amt.items()}}
    return [
        row("IS", "ifrs-full_ProfitLossAttributableToOwnersOfParent", "지배기업 소유주지분", 1,
            thstrm_amount=ni_cum // 2, thstrm_add_amount=ni_cum),
        row("BS", "ifrs-full_Assets", "자산총계", 1, thstrm_amount=eq * 3),
        row("BS", "ifrs-full_Liabilities", "부채총계", 2, thstrm_amount=eq * 2),
        row("BS", "ifrs-full_Equity", "자본총계", 3, thstrm_amount=eq),
        row("BS", "ifrs-full_EquityAttributableToOwnersOfParent", "지배기업 소유주지분", 4, thstrm_amount=eq),
    ]


class _FakeDart:
    """(corp_code, 사업연도, 보고서코드, 연결/별도) → 응답 행. 없으면 DART 013(자료 없음)."""

    def __init__(self, avail: dict):
        self.avail = avail
        self.calls: list[tuple] = []

    async def get_fnltt_singl_acnt_all(self, corp_code, bsns_year, reprt_code="11011", fs_div="CFS"):
        from open_proxy_mcp.dart.client import DartClientError
        self.calls.append((corp_code, bsns_year, reprt_code, fs_div))
        rows = self.avail.get((corp_code, bsns_year, reprt_code, fs_div))
        if rows is None:
            raise DartClientError("013", "조회된 데이타가 없습니다.")
        return {"status": "000", "list": rows}


class _Today(date):
    @classmethod
    def today(cls):
        return cls(TODAY.year, TODAY.month, TODAY.day)


def _q(url, sql, params=()):
    with psycopg.connect(url) as c:
        return c.execute(sql, params).fetchall()


@pytest.fixture
def fdb(pg17, monkeypatch):
    """A 늦게 낸 회사(2026 반기 nodata → 이제 있음) · B 2년 전 nodata(지금은 있음) · C 이미 ok ·
    D 여전히 안 낸 회사 · E 지난 실행 오류. 나머지 칸은 ok(값 없음)로 채워 이번 실행 대상에서 뺀다."""
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(FINSTAT_Q_DDL)
        c.execute(FINSTAT_Y_DDL)
        c.execute(Q.DDL_FUNDAMENTALS)
        for t in "ABCDE":
            c.execute("INSERT INTO dart_fundamentals (ticker, corp_code, fetched, ni_fy, ni_ttm, eq_fy, eq_mrq) "
                      "VALUES (%s, %s, 'ok', 1, 1, 1, 1)", (t, f"0000000{t}"))
            for fy in (2024, 2026):
                for qq in (1, 2, 3):
                    c.execute("INSERT INTO dart_finstat_q (ticker, fy, quarter, reprt_code, fetched) "
                              "VALUES (%s, %s, %s, %s, 'ok')", (t, fy, qq, Q.Q_TO_RC[qq]))
        upd = "UPDATE dart_finstat_q SET fetched=%s, fs='OFS', ni_case='NODATA', eq_case='NODATA' WHERE ticker=%s AND fy=%s AND quarter=%s"
        c.execute(upd, ("nodata", "A", 2026, 2))
        c.execute(upd, ("nodata", "B", 2024, 2))
        c.execute(upd, ("nodata", "D", 2026, 2))
        c.execute(upd, ("err:[020] 한도", "E", 2026, 1))
        # A 의 파생 재료: 2025 사업보고서(연간) + 2025 반기 누적
        c.execute("INSERT INTO dart_finstat_q (ticker, fy, quarter, reprt_code, fs, ni_cum, eq, fetched) VALUES "
                  "('A', 2025, 4, '11011', 'CFS', 100, 1000, 'ok'), ('A', 2025, 2, '11012', 'CFS', 40, 900, 'ok')")
    monkeypatch.setenv("DATABASE_URL", pg17)
    monkeypatch.setattr(Q, "SLEEP", 0.0)
    monkeypatch.setattr(Q, "date", _Today)
    fake = _FakeDart({
        ("0000000A", "2026", "11012", "CFS"): _report(60, 1100),
        ("0000000B", "2024", "11012", "CFS"): _report(7, 70),
        ("0000000E", "2026", "11013", "OFS"): _report(5, 50),
    })
    monkeypatch.setattr("open_proxy_mcp.dart.client.get_dart_client", lambda: fake)
    return pg17, fake


def test_fetch_refills_recent_nodata_and_leaves_old_nodata_and_ok_alone(fdb, capsys):
    url, fake = fdb
    asyncio.run(Q.fetch([2024, 2026], conc=1))
    called = {(cc[-1], y, rc) for cc, y, rc, _ in fake.calls}
    assert called == {("A", "2026", "11012"), ("D", "2026", "11012"), ("E", "2026", "11013")}
    rows = {r[0]: r[1:] for r in _q(url, "SELECT ticker, fetched, fs, ni_cum, eq FROM dart_finstat_q "
                                         "WHERE (ticker, fy, quarter) IN (('A',2026,2),('B',2024,2),('D',2026,2),('E',2026,1))")}
    assert rows["A"] == ("ok", "CFS", 60.0, 1100.0)           # 늦게 낸 반기 — 채워졌다
    assert rows["B"] == ("nodata", "OFS", None, None)          # 400일 밖 — 건드리지 않음
    assert rows["D"] == ("nodata", "OFS", None, None)          # 아직도 없음 — CFS·OFS 둘 다 물어보고 그대로
    assert rows["E"] == ("ok", "OFS", 5.0, 50.0)               # 오류 재시도(종전과 같다)
    out = capsys.readouterr().out
    assert "그중 nodata 재확인 2" in out
    assert "fetch 종료 — ok 2(그중 nodata → ok 1) · nodata 1 · err 0" in out


def test_fetch_wider_window_sweeps_older_nodata_once(fdb):
    url, fake = fdb
    asyncio.run(Q.fetch([2024], conc=1, nodata_days=1000))
    assert {(cc[-1], y, rc) for cc, y, rc, _ in fake.calls} == {("B", "2024", "11012")}
    assert _q(url, "SELECT fetched, ni_cum FROM dart_finstat_q WHERE ticker='B' AND fy=2024 AND quarter=2") == [("ok", 7.0)]


def test_derive_only_named_tickers_and_only_when_values_change(fdb, capsys):
    url, _ = fdb
    asyncio.run(Q.fetch([2024, 2026], conc=1))
    Q.derive_fundamentals(["A"])
    assert _q(url, "SELECT ni_fy, ni_ttm, eq_fy, eq_mrq FROM dart_fundamentals WHERE ticker='A'") == \
        [(100.0, 120.0, 1000.0, 1100.0)]                       # TTM = 연간 100 + 반기 누적 60 − 전년 반기 40
    assert _q(url, "SELECT count(*) FROM dart_fundamentals WHERE ticker <> 'A' AND ni_fy = 1") == [(4,)]
    assert "1사 재무 4열 파생 · 값이 바뀐 1사 갱신" in capsys.readouterr().out
    xmin = _q(url, "SELECT xmin::text FROM dart_fundamentals WHERE ticker='A'")
    Q.derive_fundamentals(["A"])                              # 같은 값 — 고쳐 쓰지 않는다
    assert "값이 바뀐 0사 갱신" in capsys.readouterr().out
    assert _q(url, "SELECT xmin::text FROM dart_fundamentals WHERE ticker='A'") == xmin
