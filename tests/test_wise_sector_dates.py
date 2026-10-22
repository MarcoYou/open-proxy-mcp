"""`wise_sector` 가 뷰일 때 날짜·업종 목록 질의는 작은 밑 표를 읽는다(`services/wise_sector_dates`).

뷰(`wise_sector` = 구간 표 × 스냅샷 날짜 표 × 번호표)에 `MAX(snap_dd)`·`DISTINCT snap_dd`·`DISTINCT sector` 를 물으면 서버가 요청마다 약 10만 행을 펼친다(운영 102,895행 ·
스냅샷 3 → 38개로 약 10배 느려짐). 모드를 판별해 뷰일 때만 `wise_sector_snap`(dd)·`wise_sector_iv`(sector) 를 읽는다. 표 모드(교체 전·되돌린 뒤)는 종전 SQL 그대로다.
앞 절 = 가짜 pg_rows(DB 0). 뒤 절 = Postgres 17(docker 없으면 건너뜀 — `tests/conftest.py` 의 `pg17`): 같은 질의가 표 모드·뷰 모드에서 같은 답.
"""
from __future__ import annotations

import pytest

from open_proxy_mcp import db
from open_proxy_mcp.services import dividend_data as dd
from open_proxy_mcp.services import disclosure_flow as df
from open_proxy_mcp.services import sector_universe as su
from open_proxy_mcp.services import wise_sector_dates as W


@pytest.fixture(autouse=True)
def _fresh_mode(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "fake-db-a")      # 접속은 가짜 pg_rows 가 가로챈다
    W.reset()
    yield
    W.reset()


def _kind(monkeypatch, kinds):
    """relkind 질의에 kinds 를 차례로 답한다(마지막 값 반복). 값이 None 이면 DB 장애."""
    seen = []

    def fake(sql, params=()):
        seen.append(sql)
        k = kinds[min(len(seen) - 1, len(kinds) - 1)]
        return None if k is None else [(k,)]
    monkeypatch.setattr(db, "pg_rows", fake)
    return seen


def test_mode_is_view_only_for_relkind_v_and_is_cached(monkeypatch):
    seen = _kind(monkeypatch, ["v", "r"])
    assert W.is_view() is True and W.is_view() is True
    assert len(seen) == 1                                   # 캐시
    W.reset()
    assert W.is_view() is False                             # 표 모드(r)


def test_db_failure_is_not_cached_and_means_table_sql(monkeypatch):
    seen = _kind(monkeypatch, [None, "v"])
    assert W.is_view() is False
    assert W.is_view() is True                              # 장애는 캐시하지 않는다 — 다음 호출이 다시 본다
    assert len(seen) == 2


def test_database_url_change_resets_the_cache(monkeypatch):
    _kind(monkeypatch, ["v", "r"])
    assert W.is_view() is True
    monkeypatch.setenv("DATABASE_URL", "fake-db-b")
    assert W.is_view() is False


def test_sql_pieces_have_no_view_scan_in_fast_mode():
    for piece in (W.max_dd(True), W.min_dd(True), W.max_dd_le(True), W.snaps_select(True), W.sector_names_select(True)):
        assert "wise_sector_snap" in piece or "wise_sector_iv" in piece
        assert "FROM wise_sector)" not in piece and "FROM wise_sector " not in piece and not piece.endswith("FROM wise_sector")
    for piece in (W.max_dd(False), W.min_dd(False), W.max_dd_le(False), W.snaps_select(False), W.sector_names_select(False)):
        assert "wise_sector_snap" not in piece and "wise_sector_iv" not in piece


def test_query_falls_back_to_the_old_sql_when_the_fast_path_fails(monkeypatch):
    _kind(monkeypatch, ["v", "v"])
    sent = []

    def runner(sql, params):
        sent.append(sql)
        return None if "wise_sector_snap" in sql else [(1,)]
    rows = W.query(lambda fast: W.snaps_select(fast), (), runner)
    assert rows == [(1,)] and "wise_sector_snap" in sent[0] and "DISTINCT snap_dd FROM wise_sector" in sent[1]


def test_sector_list_reads_the_interval_table_in_view_mode(monkeypatch):
    _kind(monkeypatch, ["v"])
    sent = []
    monkeypatch.setattr(dd, "_rows", lambda sql, params=(): sent.append(sql) or [("IT",), ("금융",)])
    assert dd.sector_list() == ["IT", "금융"]
    assert "wise_sector_iv" in sent[0] and "FROM wise_sector " not in sent[0]


def test_sector_list_unchanged_in_table_mode(monkeypatch):
    _kind(monkeypatch, ["r"])
    sent = []
    monkeypatch.setattr(dd, "_rows", lambda sql, params=(): sent.append(sql) or [("IT",)])
    dd.sector_list()
    assert sent[0] == "SELECT DISTINCT sector FROM wise_sector WHERE sector IS NOT NULL ORDER BY 1"


@pytest.mark.parametrize("mode,expect_snap", [("v", True), ("r", False)])
def test_dividend_sector_aggregate_and_screen_use_the_snapshot_table_only_in_view_mode(monkeypatch, mode, expect_snap):
    _kind(monkeypatch, [mode])
    sent = []
    monkeypatch.setattr(dd, "_rows", lambda sql, params=(): sent.append(sql) or [])
    dd.aggregate_history("sector", "IT", 2022, 2024)
    dd.screen(2024, sector="IT")
    for sql in sent[:2]:
        assert ("wise_sector_snap" in sql) is expect_snap
        assert ("MAX(snap_dd) FROM wise_sector" in sql) is (not expect_snap)


def test_sector_universe_rows_sql_uses_the_snapshot_table_in_view_mode(monkeypatch):
    _kind(monkeypatch, ["v"])
    sent = []
    monkeypatch.setattr(su, "pg_rows", lambda sql, params=(): sent.append((sql, params)) or [])
    su._read_rows("20261020", "daily")
    sql, params = sent[0]
    assert "FROM wise_sector_snap WHERE dd <= %s" in sql and "min(dd) FROM wise_sector_snap" in sql
    assert "max(snap_dd)" not in sql and "min(snap_dd)" not in sql
    assert params == ("20261020", "20261020")
    assert "LEFT JOIN wise_sector w ON w.snap_dd = s.snap" in sql            # 종목 행은 뷰에서 읽는다(날짜 고르기만 바뀜)


def test_disclosure_flow_snaps_cte_uses_the_snapshot_table_in_view_mode(monkeypatch):
    from datetime import date
    _kind(monkeypatch, ["v"])
    sent = []
    monkeypatch.setattr(df, "pg_rows", lambda sql, params=(): sent.append(sql) or [])
    df._fetch_events(["order"], date(2026, 8, 1), date(2026, 10, 31), None, None)
    assert "snaps AS MATERIALIZED (SELECT dd AS snap_dd FROM wise_sector_snap)" in sent[0]
    W.reset()
    _kind(monkeypatch, ["r"])
    df._fetch_events(["order"], date(2026, 8, 1), date(2026, 10, 31), None, None)
    assert "snaps AS MATERIALIZED (SELECT DISTINCT snap_dd FROM wise_sector)" in sent[1]


# ── Postgres 17: 표 모드와 뷰 모드에서 같은 답 ─────────────────────────────────────────────

def test_same_answers_in_table_and_view_mode(pg17, monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    from test_wise_sector_iv_pg import _base, _switch      # 같은 폴더의 PG 시험 도움 함수(옛 표 + 번호표 + 시세)
    _base(pg17)
    monkeypatch.setenv("DATABASE_URL", pg17)
    W.reset()
    assert W.is_view() is False

    def answers():
        out = []
        with psycopg.connect(pg17) as c:
            for fast in (False, True) if W.is_view() else (False,):
                out.append([
                    c.execute(f"SELECT {W.max_dd(fast)}").fetchall(),
                    c.execute(f"SELECT {W.min_dd(fast)}").fetchall(),
                    c.execute(f"SELECT {W.max_dd_le(fast)}", ("20260901",)).fetchall(),
                    sorted(c.execute(W.snaps_select(fast)).fetchall()),
                    c.execute(W.sector_names_select(fast)).fetchall(),
                ])
        return out
    table_mode = answers()[0]
    _switch(pg17)
    W.reset()
    assert W.is_view() is True
    slow, fast = answers()
    assert slow == table_mode == fast                      # 뷰 위 질의도, 밑 표 질의도 옛 표와 같은 답
    assert fast[0] == [("20261016",)] and fast[2] == [("20260828",)]
