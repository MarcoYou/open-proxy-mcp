# -*- coding: utf-8 -*-
"""정정 칸 출처 접수번호 — **같은 값을 먼저 낸 가장 이른 보고서**가 출처다(261021 DATA-PIT10).

종전: 정정 칸은 가장 늦게 접수된 보고서의 값이 차지하고 출처도 그 보고서로 옮겨 갔다(`restated_wins`) — 더 늦은 보고서가 **같은 값을 다시 적은** 것도 마찬가지였다.
정정 칸은 출처가 공시된 날부터 쓰므로(`restated_pit`) 출처가 한 해 늦으면 그 한 해 동안 원본 칸(재작성 전 값)을 썼다.
  065150 FY2023: 원본 순이익 −58.43억 · 자본 255.48억. 2025-03-21 접수 2024 사업보고서의 전기 칸이 −61.56억 · 252.36억으로 재작성했고 2026-03-23 접수 2025 사업보고서의
  전전기 칸도 같은 값인데, 출처가 2026-03-23 이라 2025-03-21 ~ 2026-03-22 의 곡선·과거 시점이 원본 값을 썼다.
이제 후보가 지금 정정 칸과 같은 값이면 값은 그대로 두고 출처는 둘 중 이른 접수번호로 한다(더 이른 보고서를 나중에 읽어도 출처가 내려온다). 값이 다르면(진짜 재작성) 종전과 같다.

DART 0콜 — 가짜 클라이언트. Postgres 17(도커 없으면 건너뜀).
"""
from __future__ import annotations

import asyncio

import pytest

from open_proxy_mcp.services import restated_pit as rpit
from scripts import market_fund_quarterly as Q
from scripts import market_val_series as S
from tests.test_restated_latest_report_pg import FINSTAT_Q_DDL, FINSTAT_Y_DDL, B, _FakeDart, _q, _r

psycopg = pytest.importorskip("psycopg")

R2024 = "20250321001891"      # 2024 사업보고서(065150 실제 접수번호)
R2025 = "20260323001661"      # 2025 사업보고서


# ═══ 순수 규칙 ═══════════════════════════════════════════════════════════════════════════════════
def test_same_value_means_every_given_cell_equals_the_current_restated_cell():
    same = S.restated_same
    assert same(-61.56 * B, 252.36 * B, -61.56 * B, 252.36 * B) is True
    assert same(-61.56 * B, 252.36 * B, -61.56 * B + 0.4, 252.36 * B) is True                  # 1원 미만
    assert same(25_235_715_907.0, None, None, 25_235_715_907.0 + 4_000) is False                # 지금 정정 칸에 순이익이 없는데 후보에 있다 → 새 값
    assert same(17_385 * B, 102_365 * B - 4_000_000, 17_385 * B, 102_365 * B) is True            # 백만 원 반올림 꼴(상대 1e-6 안, 005830 FY2023 자본 꼴)
    assert same(-61.56 * B, 252.36 * B, -61.56 * B, 253.0 * B) is False                         # 자본이 다르다 = 재작성
    assert same(-61.56 * B, None, -61.56 * B, 252.36 * B) is True                                # 후보가 비운 칸은 보지 않는다
    assert same(None, 252.36 * B, -61.56 * B, 999.0 * B) is False
    assert same(None, None, 1.0, 2.0) is False                                                   # 후보에 값이 하나도 없으면 같다고 하지 않는다
    assert same(1.0, 2.0, None, None) is False


def test_source_is_the_earlier_report_when_the_value_is_the_same():
    src = S.restated_source
    assert src(R2025, R2024, True) == R2024, "같은 값을 늦은 보고서가 다시 적어도 출처는 처음 낸 보고서"
    assert src(R2024, R2025, True) == R2024, "더 이른 보고서를 나중에 읽으면 출처가 내려온다"
    assert src(R2025, R2024, False) == R2025, "값이 다르면(재작성) 늦은 보고서가 출처"
    assert src(R2025, None, True) == R2025, "출처를 모르던 칸은 후보로"
    assert src(R2024, R2024, True) == R2024
    W = S.restated_wins
    assert W(R2025, restated_rcept=R2024) is True and W(R2024, restated_rcept=R2025) is False, "값이 다른 이른 보고서는 늦은 출처를 덮지 못한다(종전 그대로)"
    assert W(R2024, restated_rcept=R2025, same_value=True) is True, "같은 값이면 이른 보고서가 출처를 가져간다"
    assert W(R2024, own_rcept=R2024, restated_rcept=R2025, same_value=True) is False, "그 해 보고서보다 이르거나 같으면 쓰지 않는다"
    assert W(R2024, own_rcept="20250401000001", restated_rcept=R2025, same_value=True) is False
    assert W(None, same_value=True) is False


# ═══ DB: 수집(fetch)·백필(backfill_restated) ════════════════════════════════════════════════════════
@pytest.fixture
def pdb(pg17, monkeypatch):
    """065150 꼴: FY2023 원본 −58.43억·255.48억 · 정정 칸 −61.56억·252.36억(2024 사업보고서 전기 칸, 출처 2024 사업보고서) · 다른 종목 몇 개."""
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(FINSTAT_Y_DDL)
        c.execute(FINSTAT_Q_DDL)
        c.execute(Q.DDL_FUNDAMENTALS)
        for t in ("000001", "000002", "000003", "000004", "000005"):
            c.execute("INSERT INTO dart_fundamentals (ticker, corp_code, fetched, acc_mt) VALUES (%s, %s, 'ok', 12)", (t, "00" + t))
        ins = "INSERT INTO dart_finstat_y (ticker, fy, fs, ni, eq, fetched, ni_restated, eq_restated) VALUES (%s,%s,'CFS',%s,%s,'ok',%s,%s)"
        c.execute(ins, ("000001", 2023, -58.43 * B, 255.48 * B, -61.56 * B, 252.36 * B))         # 출처는 아래에서 단다
        c.execute(ins, ("000001", 2024, 10 * B, 300 * B, None, None))
        c.execute(ins, ("000002", 2023, 200 * B, 1200 * B, 210 * B, 1210 * B))                   # 다른 값 두 번 재작성될 종목
        c.execute(ins, ("000003", 2023, 50 * B, 500 * B, 55 * B, 505 * B))                       # 출처 모르는 옛 정정 칸
        c.execute(ins, ("000004", 2023, 70 * B, 700 * B, 70 * B, 700 * B))                       # 원본 = 정정(값 효과 없는 칸)
    fake = _FakeDart()
    monkeypatch.setenv("DATABASE_URL", pg17)
    monkeypatch.setenv("SERIES_SLEEP", "0")
    monkeypatch.setattr("open_proxy_mcp.dart.client.get_dart_client", lambda: fake)
    monkeypatch.setattr(S, "_RCEPT_COL", False)
    monkeypatch.setattr(S, "_RESTATED_COL", False)
    S._pg().close()               # 정정 칸 출처 칸 만들기
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute("UPDATE dart_finstat_y SET restated_rcept_no=%s WHERE ticker='000001' AND fy=2023", (R2024,))
        c.execute("UPDATE dart_finstat_y SET restated_rcept_no='20250318000001' WHERE ticker='000002' AND fy=2023")
    return pg17, fake


def _row(url, t, fy):
    return _q(url, "SELECT ni, eq, ni_restated, eq_restated, restated_rcept_no FROM dart_finstat_y WHERE ticker=%s AND fy=%s", (t, fy))[0]


def test_a_later_report_repeating_the_same_value_does_not_move_the_source(pdb):
    """065150: 2025 사업보고서(2026-03-23)의 전전기 칸 = 2024 사업보고서 전기 칸과 같은 값 → 값도 출처(2025-03-21)도 그대로."""
    url, fake = pdb
    fake.avail[("00000001", "2025", "11011", "CFS")] = _r(R2025, ni=20 * B, eq=320 * B, ni_frm=10 * B, eq_frm=300 * B,
                                                          ni_bfe=-61.56 * B, eq_bfe=252.36 * B)
    asyncio.run(S.fetch(only={("000001", 2025)}, keep_fs=True, amended=False))
    assert _row(url, "000001", 2023) == (-58.43 * B, 255.48 * B, -61.56 * B, 252.36 * B, R2024)
    # 후보가 반올림으로 조금(상대 1e-6 안) 다른 같은 값이면 — 정정 칸 값은 글자까지 그대로, 출처만 그대로(후보의 값으로 덮어쓰지 않는다)
    fake.avail[("00000001", "2025", "11011", "CFS")] = _r(R2025, ni=20 * B, eq=320 * B, ni_frm=10 * B, eq_frm=300 * B,
                                                          ni_bfe=-61.56 * B - 3_000, eq_bfe=252.36 * B + 4_000)
    asyncio.run(S.fetch(only={("000001", 2025)}, keep_fs=True, amended=False))
    assert _q(url, "SELECT ni_restated = -61.56e8::float8 AND eq_restated = 252.36e8::float8, restated_rcept_no FROM dart_finstat_y WHERE ticker='000001' AND fy=2023") == [(True, R2024)]
    # 허용 폭(상대 1e-6)을 넘으면 다른 값 = 재작성 — 값과 출처가 늦은 보고서로
    fake.avail[("00000001", "2025", "11011", "CFS")] = _r(R2025, ni=20 * B, eq=320 * B, ni_frm=10 * B, eq_frm=300 * B,
                                                          ni_bfe=-61.56 * B, eq_bfe=252.36 * B + 100_000)
    asyncio.run(S.fetch(only={("000001", 2025)}, keep_fs=True, amended=False))
    assert _row(url, "000001", 2023)[2:] == (-61.56 * B, 252.36 * B + 100_000, R2025)


def test_a_later_report_with_a_different_value_still_takes_the_source(pdb):
    """000002: 2025 사업보고서가 FY2023 을 또 다른 값으로 재작성(진짜 재작성) → 값과 출처가 늦은 보고서로(종전과 같다)."""
    url, fake = pdb
    fake.avail[("00000002", "2025", "11011", "CFS")] = _r(R2025, ni=300 * B, eq=1500 * B, ni_frm=250 * B, eq_frm=1400 * B, ni_bfe=212 * B, eq_bfe=1215 * B)
    asyncio.run(S.fetch(only={("000002", 2025)}, keep_fs=True, amended=False))
    assert _row(url, "000002", 2023)[2:] == (212.0 * B, 1215.0 * B, R2025)


def test_partial_candidate_follows_the_cells_it_gives(pdb):
    url, fake = pdb
    # 자본만 있고 같은 값 → 출처 그대로
    fake.avail[("00000001", "2025", "11011", "CFS")] = _r(R2025, ni=20 * B, eq=320 * B, ni_frm=10 * B, eq_frm=300 * B, ni_bfe=None, eq_bfe=252.36 * B)
    asyncio.run(S.fetch(only={("000001", 2025)}, keep_fs=True, amended=False))
    assert _row(url, "000001", 2023)[2:] == (-61.56 * B, 252.36 * B, R2024)
    # 자본이 다르면 재작성 — 순이익은 후보가 비워서 그대로, 자본·출처만 새 보고서
    fake.avail[("00000001", "2025", "11011", "CFS")] = _r(R2025, ni=20 * B, eq=320 * B, ni_frm=10 * B, eq_frm=300 * B, ni_bfe=None, eq_bfe=260 * B)
    asyncio.run(S.fetch(only={("000001", 2025)}, keep_fs=True, amended=False))
    assert _row(url, "000001", 2023)[2:] == (-61.56 * B, 260.0 * B, R2025)


def test_unknown_source_takes_the_candidate_even_with_the_same_value(pdb):
    """출처를 모르는 옛 정정 칸(이 규칙 전 백필)은 같은 값이어도 새로 읽은 보고서가 출처 — 종전 그대로(알 수 없으면 후보)."""
    url, fake = pdb
    fake.avail[("00000003", "2024", "11011", "CFS")] = _r("20250320000003", ni=60 * B, eq=560 * B, ni_frm=55 * B, eq_frm=505 * B)
    asyncio.run(S.fetch(only={("000003", 2024)}, keep_fs=True, amended=False))
    assert _row(url, "000003", 2023)[2:] == (55.0 * B, 505.0 * B, "20250320000003")


def test_backfill_of_the_earlier_report_brings_the_source_down(pdb):
    """2025 사업보고서가 출처로 잡힌 칸(옛 규칙·옛 백필)에 2024 사업보고서를 백필로 읽으면 같은 값이라 출처가 2024 사업보고서로 내려온다. 값이 다르면 늦은 출처를 덮지 못한다."""
    url, fake = pdb
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("UPDATE dart_finstat_y SET restated_rcept_no=%s WHERE ticker='000001' AND fy=2023", (R2025,))
        c.execute("UPDATE dart_finstat_y SET restated_rcept_no=%s WHERE ticker='000002' AND fy=2023", (R2025,))
    fake.avail[("00000001", "2024", "11011", "CFS")] = _r(R2024, ni=10 * B, eq=300 * B, ni_frm=-61.56 * B, eq_frm=252.36 * B)
    fake.avail[("00000002", "2024", "11011", "CFS")] = _r("20250318000002", ni=10 * B, eq=300 * B, ni_frm=211 * B, eq_frm=1211 * B)
    asyncio.run(S.backfill_restated(["000001", "000002"], 2024))
    assert _row(url, "000001", 2023)[2:] == (-61.56 * B, 252.36 * B, R2024)                          # 같은 값 → 이른 출처
    assert _row(url, "000002", 2023)[2:] == (210.0 * B, 1210.0 * B, R2025)                          # 다른 값 + 이른 보고서 → 그대로


def test_refetch_of_the_earlier_report_brings_the_source_down(pdb):
    """수집 경로(`--refetch` 로 2024 사업보고서를 다시 읽음): 출처가 2025 사업보고서인 칸이 같은 값을 낸 이른 보고서로 내려온다."""
    url, fake = pdb
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("UPDATE dart_finstat_y SET restated_rcept_no=%s WHERE ticker='000001' AND fy=2023", (R2025,))
    fake.avail[("00000001", "2024", "11011", "CFS")] = _r(R2024, ni=10 * B, eq=300 * B, ni_frm=-61.56 * B, eq_frm=252.36 * B)
    asyncio.run(S.fetch(only={("000001", 2024)}, keep_fs=True, amended=False))
    assert _row(url, "000001", 2023)[2:] == (-61.56 * B, 252.36 * B, R2024)


def test_upsert_creates_the_row_and_the_same_value_rule_applies_to_existing_rows(pdb):
    url, fake = pdb
    fake.avail[("00000001", "2025", "11011", "CFS")] = _r(R2025, ni=20 * B, eq=320 * B, ni_frm=10 * B, eq_frm=300 * B, ni_bfe=-61.56 * B - 3_000, eq_bfe=252.36 * B + 4_000)
    asyncio.run(S.backfill_restated(["000001"], 2025))
    assert _q(url, "SELECT ni_restated = -61.56e8::float8 AND eq_restated = 252.36e8::float8, restated_rcept_no FROM dart_finstat_y WHERE ticker='000001' AND fy=2023") == [(True, R2024)], \
        "기존 행 + 같은 값(반올림 폭 안) → 값도 출처도 그대로"
    assert _q(url, "SELECT count(*) FROM dart_finstat_y WHERE ticker='000001'") == [(2,)]
    # 허용 폭을 넘는 값 = 재작성
    fake.avail[("00000001", "2025", "11011", "CFS")] = _r(R2025, ni=20 * B, eq=320 * B, ni_frm=10 * B, eq_frm=300 * B, ni_bfe=-61.56 * B, eq_bfe=252.36 * B + 100_000)
    asyncio.run(S.backfill_restated(["000001"], 2025))
    assert _row(url, "000001", 2023)[2:] == (-61.56 * B, 252.36 * B + 100_000, R2025)


def test_own_report_rule_still_wins_over_the_same_value_rule(pdb, monkeypatch):
    """접수번호 칸(`rcept_no`)이 있는 표: 후보가 그 해 보고서(정정 포함)보다 이르거나 같으면 같은 값이라도 쓰지 않는다."""
    url, fake = pdb
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("ALTER TABLE dart_finstat_y ADD COLUMN IF NOT EXISTS rcept_no text")
        c.execute("UPDATE dart_finstat_y SET rcept_no='20240320000555', restated_rcept_no=%s WHERE ticker='000001' AND fy=2023", (R2025,))
    S._pg().close()
    assert S._RCEPT_COL is True
    fake.avail[("00000001", "2024", "11011", "CFS")] = _r(R2024, ni=10 * B, eq=300 * B, ni_frm=-61.56 * B, eq_frm=252.36 * B)
    asyncio.run(S.backfill_restated(["000001"], 2024))
    assert _row(url, "000001", 2023)[4] == R2024                                                      # 그 해 보고서(2024-03-20)보다 늦으므로 내려온다
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("UPDATE dart_finstat_y SET rcept_no='20250401000999', restated_rcept_no=%s WHERE ticker='000001' AND fy=2023", (R2025,))
    asyncio.run(S.backfill_restated(["000001"], 2024))
    assert _row(url, "000001", 2023)[4] == R2025                                                      # 그 해 보고서(2025-04-01)가 후보(2025-03-21)보다 늦다 → 쓰지 않는다


# ═══ 시점 규칙: 출처가 이르면 정정 값을 그만큼 일찍 쓴다 ═══════════════════════════════════════════════════
def test_the_restated_value_is_used_from_the_first_report_that_gave_it():
    ni, eq, ni_r, eq_r = -58.43 * B, 255.48 * B, -61.56 * B, 252.36 * B
    for src, day, want in ((R2025, "20250401", (ni, eq)), (R2024, "20250401", (ni_r, eq_r)), (R2024, "20250320", (ni, eq)), (R2025, "20260323", (ni_r, eq_r))):
        now, gate = rpit.annual_cell(2023, ni, eq, ni_r, eq_r, src)
        assert now == (ni_r, eq_r)
        assert (gate[1] if gate and gate[0] > day else now) == want, (src, day)
