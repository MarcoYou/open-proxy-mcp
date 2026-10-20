# -*- coding: utf-8 -*-
"""연간 정정 칸(`dart_finstat_y.ni_restated`·`eq_restated`) — **같은 사업연도를 적은 보고서가 여럿이면 가장 늦게 접수된 보고서의 값**(261020 p1f).

종전: 정정 칸은 손으로 돌리는 `--backfill-restated`(기본 2024 사업보고서)만 썼고 어느 보고서의 값인지 남기지 않았다.
  · #155 의 정정 사업보고서 다시 받기(`--amended`)는 그 해 칸만 바꿨다 — 정정된 보고서의 전기·전전기 칸에서 오는 앞 두 해 정정 칸(운영 2022·2023 행
    약 2,400행의 출처)은 그대로였다.
  · 백필을 더 이른 해 보고서로 다시 돌리면 늦은 보고서의 값을 이른 보고서의 값으로 덮었다.
  · 그 해 보고서가 정정 칸 출처보다 늦게 정정돼도 정정 칸이 늘 이겨(`COALESCE(ni_restated, ni)`) 정정 보고서 값이 안 쓰였다.
이제 사업보고서를 읽는 모든 길이 같은 응답의 전기·전전기 칸을 같은 규칙(`market_val_series.restated_wins`)으로 정정 칸에 쓰고 출처 접수번호
(`restated_rcept_no`)를 남긴다. DART 콜은 늘지 않는다(이미 받은 응답).

DART 0콜 — 가짜 클라이언트. Postgres 17(도커 없으면 건너뜀, `tests/conftest.py` 의 `pg17`).
"""
from __future__ import annotations

import asyncio

import pytest

from scripts import market_fund_quarterly as Q
from scripts import market_val_series as S

psycopg = pytest.importorskip("psycopg")

FINSTAT_Y_DDL = """CREATE TABLE dart_finstat_y(
  ticker text, fy int, fs text, ni double precision, eq double precision, fetched text,
  ni_restated double precision, eq_restated double precision, PRIMARY KEY(ticker, fy))"""
FINSTAT_Q_DDL = """CREATE TABLE dart_finstat_q(
  ticker text, fy int, quarter int, reprt_code text, fs text,
  ni_cum double precision, eq double precision, fetched text,
  ni_case text, eq_case text,
  ni_cum_raw double precision, eq_raw double precision,
  ni_cum_restated double precision, eq_restated double precision, restate_why text,
  PRIMARY KEY(ticker, fy, quarter))"""


def _r(rc: str, *, ni, eq, ni_frm=None, eq_frm=None, ni_bfe=None, eq_bfe=None) -> list[dict]:
    """사업보고서 응답 꼴(지배 귀속 계정 줄) — 당기·전기·전전기 칸."""
    def amt(v):
        return "" if v is None else str(v)
    return [
        {"sj_div": "BS", "account_id": "ifrs-full_EquityAttributableToOwnersOfParent", "account_nm": "지배기업 소유주지분",
         "thstrm_amount": amt(eq), "frmtrm_amount": amt(eq_frm), "bfefrmtrm_amount": amt(eq_bfe), "currency": "KRW", "rcept_no": rc},
        {"sj_div": "BS", "account_id": "ifrs-full_Equity", "account_nm": "자본총계",
         "thstrm_amount": amt(eq), "frmtrm_amount": amt(eq_frm), "bfefrmtrm_amount": amt(eq_bfe), "currency": "KRW", "rcept_no": rc},
        {"sj_div": "IS", "account_id": "ifrs-full_ProfitLoss", "account_nm": "당기순이익",
         "thstrm_amount": amt(ni), "frmtrm_amount": amt(ni_frm), "bfefrmtrm_amount": amt(ni_bfe), "currency": "KRW", "rcept_no": rc},
        {"sj_div": "IS", "account_id": "ifrs-full_ProfitLossAttributableToOwnersOfParent", "account_nm": "지배기업 소유주지분",
         "thstrm_amount": amt(ni), "frmtrm_amount": amt(ni_frm), "bfefrmtrm_amount": amt(ni_bfe), "currency": "KRW", "rcept_no": rc},
    ]


class _FakeDart:
    def __init__(self):
        self.avail: dict = {}
        self.calls: list[tuple] = []

    async def get_fnltt_singl_acnt_all(self, corp_code, bsns_year, reprt_code="11011", fs_div="CFS"):
        from open_proxy_mcp.dart.client import DartClientError
        self.calls.append((corp_code, str(bsns_year), reprt_code, fs_div))
        rows = self.avail.get((corp_code, str(bsns_year), reprt_code, fs_div))
        if rows is None:
            raise DartClientError("013", "조회된 데이타가 없습니다.")
        return {"status": "000", "list": rows}


def _q(url, sql, params=()):
    with psycopg.connect(url) as c:
        return c.execute(sql, params).fetchall()


B = 100_000_000      # 1억


@pytest.fixture
def ydb(pg17, monkeypatch):
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(FINSTAT_Y_DDL)
        c.execute(FINSTAT_Q_DDL)
        c.execute(Q.DDL_FUNDAMENTALS)
        for t in ("000001", "000002", "000003", "000004", "000005"):
            c.execute("INSERT INTO dart_fundamentals (ticker, corp_code, fetched, acc_mt) VALUES (%s, %s, 'ok', 12)", (t, "00" + t))
        ins = "INSERT INTO dart_finstat_y (ticker, fy, fs, ni, eq, fetched, ni_restated, eq_restated) VALUES (%s,%s,'CFS',%s,%s,'ok',%s,%s)"
        # 000001 — FY2022·2023 정정 칸은 이 규칙 전 백필(출처 모름): FY2023 보고서가 FY2022 를 재작성(이연법인세 개정 꼴), FY2024 보고서가 FY2023 을 재작성
        c.execute(ins, ("000001", 2022, 100 * B, 1000 * B, 90 * B, 990 * B))
        c.execute(ins, ("000001", 2023, 200 * B, 1200 * B, 210 * B, 1210 * B))
        c.execute(ins, ("000001", 2024, 300 * B, 1500 * B, None, None))
        # 000002 — FY2023 정정 칸(출처 모름)이 있는데 FY2023 사업보고서 자체가 나중에 정정됨
        c.execute(ins, ("000002", 2023, 50 * B, 500 * B, 55 * B, 505 * B))
        c.execute(ins, ("000002", 2024, 60 * B, 560 * B, None, None))
        # 000003 — 정정 보고서의 전기 칸이 1,000배(보고서 사이 단위 오류 꼴)
        c.execute(ins, ("000003", 2023, 20 * B, 300 * B, None, None))
        c.execute(ins, ("000003", 2024, 25 * B, 320 * B, None, None))
        # 000004 — 반기 결산 리츠(결산기 칸 종목): 연간 정정 칸 = 결산기 12개월 합
        c.execute(ins, ("000004", 2023, 30 * B, 900 * B, 61 * B, 900 * B))
        c.execute(ins, ("000004", 2024, 32 * B, 910 * B, 63 * B, 910 * B))
        c.execute("INSERT INTO dart_finstat_q (ticker, fy, quarter, reprt_code, fs, fetched, ni_cum_restated, eq_restated, restate_why) "
                  "VALUES ('000004', 2024, 4, '11011', 'CFS', 'ok', 63e8, 910e8, '결산기 12개월 202407~202412 결산기 2개 합 · 공시 20250320')")
        # 000005 — FY2024 행만 있고 FY2025 사업보고서가 새로 나옴(정규 수집)
        c.execute(ins, ("000005", 2023, 70 * B, 700 * B, None, None))
        c.execute(ins, ("000005", 2024, 80 * B, 800 * B, None, None))
    fake = _FakeDart()
    monkeypatch.setenv("DATABASE_URL", pg17)
    monkeypatch.setenv("SERIES_SLEEP", "0")
    monkeypatch.setattr("open_proxy_mcp.dart.client.get_dart_client", lambda: fake)
    # 모듈 전역(칸 유무)은 _pg() 가 정한다 — 시험 뒤 되돌린다
    monkeypatch.setattr(S, "_RCEPT_COL", False)
    monkeypatch.setattr(S, "_RESTATED_COL", False)
    return pg17, fake


def _rows(url, t):
    return _q(url, "SELECT fy, ni, eq, ni_restated, eq_restated, restated_rcept_no FROM dart_finstat_y WHERE ticker=%s ORDER BY fy", (t,))


# ── 순수 규칙 ──────────────────────────────────────────────────────────────────────────────────────────

def test_latest_filed_report_wins():
    W = S.restated_wins
    assert W("20260515000001") is True, "정정 칸이 없거나 출처를 모르면(이 규칙 전 백필) 새로 읽은 보고서가 이긴다"
    assert W("20260515000001", restated_rcept="20260515000001") is True, "같은 보고서를 다시 읽은 것은 쓴다(규칙 고친 뒤)"
    assert W("20250318000001", restated_rcept="20260515000001") is False, "더 이른 보고서는 늦은 보고서 출처를 덮지 않는다"
    assert W("20260515000001", own_rcept="20261015000002") is False, "그 해 보고서(정정)가 더 늦으면 그 해 보고서가 이긴다"
    assert W("20260515000001", own_rcept="20250318000001") is True
    assert W(None) is False and W("") is False


def test_prior_candidates_use_the_same_controlling_rule():
    rows = _r("20260515000001", ni=310 * B, eq=1510 * B, ni_frm=205 * B, eq_frm=1205 * B, ni_bfe=90 * B, eq_bfe=990 * B)
    assert S.prior_candidates(rows, 2024) == [(2023, 205.0 * B, 1205.0 * B), (2022, 90.0 * B, 990.0 * B)]
    assert S.prior_candidates(_r("x", ni=1, eq=1), 2024) == [], "전기·전전기 칸이 비면 후보 없음"


# ── 정정 사업보고서 다시 받기(`--amended`) ─────────────────────────────────────────────────────────────

def test_amended_report_rewrites_prior_year_restated_cells(ydb):
    """000001 FY2024 [기재정정](2026-05-15) — 그 해 칸과 함께 FY2023(전기)·FY2022(전전기) 정정 칸도 그 보고서 값으로, 출처 접수번호와 함께."""
    url, fake = ydb
    fake.avail[("00000001", "2024", "11011", "CFS")] = _r("20260515000001", ni=310 * B, eq=1510 * B, ni_frm=205 * B, eq_frm=1205 * B,
                                                          ni_bfe=90 * B, eq_bfe=990 * B)
    asyncio.run(S.fetch(only={("000001", 2024)}, keep_fs=True, amended=True))
    assert _rows(url, "000001") == [
        (2022, 100.0 * B, 1000.0 * B, 90.0 * B, 990.0 * B, "20260515000001"),     # 값 같음 — 출처만 새 보고서로
        (2023, 200.0 * B, 1200.0 * B, 205.0 * B, 1205.0 * B, "20260515000001"),   # 종전 210·1,210 → 정정 보고서의 전기 칸
        (2024, 310.0 * B, 1510.0 * B, None, None, None)]
    assert fake.calls == [("00000001", "2024", "11011", "CFS")], "DART 콜은 그 보고서 하나 그대로"
    # 더 이른 보고서(FY2023 원 사업보고서, 2024-03)로 백필을 다시 돌려도 늦은 보고서 값을 덮지 않는다
    fake.avail[("00000001", "2023", "11011", "CFS")] = _r("20240315000001", ni=200 * B, eq=1200 * B, ni_frm=95 * B, eq_frm=995 * B)
    asyncio.run(S.backfill_restated(["000001"], 2023))
    assert _rows(url, "000001")[0] == (2022, 100.0 * B, 1000.0 * B, 90.0 * B, 990.0 * B, "20260515000001")


def test_amended_own_report_newer_than_the_restated_source_wins(ydb):
    """000002 FY2023 사업보고서가 2026-10-15 정정 — 출처 모르는 정정 칸(그 전 보고서)보다 늦으므로 그 해 칸이 쓰인다(정정 칸 비움 → Q4 seed 가 옮김)."""
    url, fake = ydb
    fake.avail[("00000002", "2023", "11011", "CFS")] = _r("20261015000002", ni=52 * B, eq=502 * B)
    asyncio.run(S.fetch(only={("000002", 2023)}, keep_fs=True, amended=True))
    assert _rows(url, "000002")[0] == (2023, 52.0 * B, 502.0 * B, None, None, None)
    Q.seed_q4()
    assert _q(url, "SELECT ni_cum, eq FROM dart_finstat_q WHERE ticker='000002' AND fy=2023 AND quarter=4") == [(52.0 * B, 502.0 * B)]


def test_amended_own_report_older_than_a_recorded_source_keeps_it(ydb):
    url, fake = ydb
    S._pg().close()     # 칸 만들기
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("UPDATE dart_finstat_y SET restated_rcept_no='20260601000009' WHERE ticker='000002' AND fy=2023")
    fake.avail[("00000002", "2023", "11011", "CFS")] = _r("20260401000002", ni=52 * B, eq=502 * B)
    asyncio.run(S.fetch(only={("000002", 2023)}, keep_fs=True, amended=True))
    assert _rows(url, "000002")[0] == (2023, 52.0 * B, 502.0 * B, 55.0 * B, 505.0 * B, "20260601000009"), \
        "정정 칸 출처(2026-06-01)가 그 해 정정 보고서(2026-04-01)보다 늦다 — 정정 칸이 이긴다"


def test_power_of_ten_prior_column_is_not_written(ydb, capsys):
    url, fake = ydb
    fake.avail[("00000003", "2024", "11011", "CFS")] = _r("20260515000003", ni=26 * B, eq=321 * B, ni_frm=20 * B * 1000, eq_frm=300 * B * 1000)
    asyncio.run(S.fetch(only={("000003", 2024)}, keep_fs=True, amended=True))
    assert _rows(url, "000003")[0] == (2023, 20.0 * B, 300.0 * B, None, None, None)
    assert "10의 거듭제곱 배 다르다" in capsys.readouterr().out


def test_period_cell_reit_restated_cells_are_left_alone(ydb):
    url, fake = ydb
    fake.avail[("00000004", "2024", "11011", "CFS")] = _r("20260515000004", ni=33 * B, eq=911 * B, ni_frm=31 * B, eq_frm=905 * B)
    asyncio.run(S.fetch(only={("000004", 2024)}, keep_fs=True, amended=True))
    got = _rows(url, "000004")
    assert got[0] == (2023, 30.0 * B, 900.0 * B, 61.0 * B, 900.0 * B, None), "앞 결산기 칸을 앞 사업연도에 넣지 않는다"
    assert got[1][3:5] == (63.0 * B, 910.0 * B), "결산기 12개월 합은 그대로"


# ── 정규 수집(새 사업보고서) ────────────────────────────────────────────────────────────────────────────

def test_new_annual_report_fills_restated_cells_only_where_it_restates(ydb):
    """000005 FY2025 사업보고서(정규 수집) — FY2024 는 재작성(80억 → 82억), FY2023 은 그대로(정정 칸 만들지 않음)."""
    url, fake = ydb
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("DELETE FROM dart_fundamentals WHERE ticker <> '000005'")
    fake.avail[("00000005", "2025", "11011", "CFS")] = _r("20260312000005", ni=90 * B, eq=900 * B, ni_frm=82 * B, eq_frm=800 * B,
                                                          ni_bfe=70 * B, eq_bfe=700 * B)
    asyncio.run(S.fetch(["000005"]))
    assert [r for r in _rows(url, "000005") if r[0] >= 2023] == [
        (2023, 70.0 * B, 700.0 * B, None, None, None),
        (2024, 80.0 * B, 800.0 * B, 82.0 * B, 800.0 * B, "20260312000005"),
        (2025, 90.0 * B, 900.0 * B, None, None, None)]
    assert [c for c in fake.calls if c[1] in ("2023", "2024", "2025")] == [("00000005", "2025", "11011", "CFS")], \
        "앞 두 해는 다시 받지 않는다 — 새 보고서 응답의 전기·전전기 칸을 쓴다(DART 콜 0 추가)"


def test_worth_writing_only_restatements_or_existing_restated_cells():
    row = (80 * B, 800 * B, None, None)
    assert S.restated_worth(82 * B, 800 * B, row) is True, "재작성(80 → 82억)"
    big = (17_385 * B, 102_365 * B, None, None)
    assert S.restated_worth(17_385 * B, 102_365 * B - 4_000_000, big) is False, \
        "백만 원 반올림 꼴(10조에 400만 원 — 상대 1e-6 안)은 재작성이 아니다(005830 FY2023 자본 꼴)"
    assert S.restated_worth(80 * B, 800 * B, (80 * B, 800 * B, 79 * B, None)) is True, "정정 칸이 있으면 늘 — 출처를 새 보고서로"
    assert S.restated_worth(None, None, None) is False and S.restated_worth(1.0, None, None) is True
    assert S.unit_mismatch(300 * B * 1000, 300 * B) and not S.unit_mismatch(-300 * B * 1000, 300 * B) and not S.unit_mismatch(None, 1.0)


def test_report_of_another_fiscal_year_is_not_used(ydb, capsys):
    """082660 꼴 — 2024 요청에 2025 사업보고서(bsns_year 2025)가 오면 그 전기·전전기를 2023·2022 정정 칸에 넣지 않는다(한 해 밀림)."""
    url, fake = ydb
    rows = _r("20260324000091", ni=None, eq=None, ni_frm=-194 * B, eq_frm=220 * B, ni_bfe=-58 * B, eq_bfe=346 * B)
    for r in rows:
        r["bsns_year"] = "2025"
    fake.avail[("00000001", "2024", "11011", "CFS")] = rows
    asyncio.run(S.backfill_restated(["000001"], 2024))
    asyncio.run(S.fetch(only={("000001", 2024)}, keep_fs=True, amended=True))
    got = _rows(url, "000001")
    assert got[0][3:5] == (90.0 * B, 990.0 * B) and got[1][3:5] == (210.0 * B, 1210.0 * B), "정정 칸 그대로"
    assert got[2][1:3] == (300.0 * B, 1500.0 * B), "그 해 칸도 그대로(자료 없음은 ok 행을 덮지 않는다)"
    out = capsys.readouterr().out
    assert "2025 사업연도 보고서가 왔다" in out
    assert S.report_year_mismatch(rows, 2024) == "2025" and S.report_year_mismatch(rows, 2025) is None and S.report_year_mismatch([], 2024) is None
