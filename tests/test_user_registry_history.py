# -*- coding: utf-8 -*-
"""사용자 등록부(`scripts/user_registry.py`)가 **드레인된 과거까지** 센다. network 0 · DB 0.

`ops_tool_calls` 는 주간 드레인으로 진행 중인 주(약 7일)만 남는다. 등록부가 DB 만 세면:
  · 그 주에 안 온 사용자가 등록부에서 **통째로 빠진다** — 그리고 다음 실행에서 가장 큰 번호가
    비면 같은 `user_id` 가 **다른 사람에게** 간다(「한 번 준 ID 는 바꾸지 않는다」가 깨진다)
  · 남은 사용자의 호출 수·활성일·최근 관측이 1주 치로 줄어든다
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

DAY = 86_400 * 10**9


@pytest.fixture
def ur():
    return importlib.reload(importlib.import_module("user_registry"))


def _old(**rows):
    """기존 등록부(CSV 에서 읽은 그대로 — 값이 문자열)."""
    return {k: {"user_id": uid, "key_hash": k, "first_seen_kst": "2026-06-29T00:00:00+09:00",
                "last_seen_kst": "2026-08-01T00:00:00+09:00", "active_days": "3", "calls": str(calls),
                "distinct_tools": "2"} for k, (uid, calls) in rows.items()}


def test_summary_matches_the_old_sql_definition(ur):
    """종전 SQL 과 같은 정의 — 활성일은 `ts_ns / 86400e9` 정수 나눗셈, 도구는 NULL 아닌 것만, 최초 관측 순."""
    rows = [(5 * DAY + 1, "hB", "company"), (2 * DAY + 5, "hA", "company"), (2 * DAY + 9, "hA", None),
            (3 * DAY, "hA", "screener"), (3 * DAY + 1, "hA", "company")]
    assert ur.summarize(rows) == [("hA", 2 * DAY + 5, 3 * DAY + 1, 4, 2, 2),
                                  ("hB", 5 * DAY + 1, 5 * DAY + 1, 1, 1, 1)]


def test_drained_history_is_counted(ur, tmp_path, monkeypatch):
    """**핵심.** DB(이번 주) + 드레인 백업(지난주들) — 지난주에만 온 사용자도, 지난 호출도 센다."""
    duckdb = pytest.importorskip("duckdb")
    import usage_tracker as ut
    ev = tmp_path / "events"
    ev.mkdir()
    con = duckdb.connect()
    con.execute("CREATE TABLE t (event_id VARCHAR, ts_ns BIGINT, key_hash VARCHAR, tool VARCHAR)")
    con.execute("INSERT INTO t VALUES ('e1', 100, 'hOld', 'valuation'), ('e2', 200, 'hA', 'company'), "
                "('e3', 300, 'hA', 'company')")
    con.execute(f"COPY t TO '{(ev / '260601-0607.parquet').as_posix()}' (FORMAT parquet)")
    con.close()
    monkeypatch.setattr(ut, "DRAINED_DIR", ev)
    monkeypatch.setattr(ut, "_drained_cache", None)
    monkeypatch.setattr(ut, "_db_event_ids", lambda: {"e3"})     # e3 는 아직 DB 에도 있다 — 한 번만

    class _Conn:
        def execute(self, sql):
            assert "FROM ops_tool_calls" in sql
            return type("R", (), {"fetchall": lambda s: [(300, "hA", "company"), (400, "hA", "screener")]})()

    got = {k: (calls, tools) for k, _, _, calls, _, tools in ur.summarize(ur.history_rows(_Conn()))}
    # hOld 는 이번 주에 안 왔다 — DB 만 세면 사라진다. 개명된 이름은 현재 이름으로 접힌다.
    assert got == {"hOld": (1, 1), "hA": (3, 2)}
    assert (100, "hOld", "price_multiple_data") in ur.history_rows(_Conn())


def test_keys_not_seen_this_time_stay_and_ids_are_never_reused(ur):
    old = _old(hA=("u0001", 5), hB=("u0002", 7))
    rows = [("hA", 1, 2, 9, 4, 3), ("hNew", 3, 3, 1, 1, 1)]          # hB(가장 큰 번호)가 안 보인다
    reg = ur.merge_registry(rows, old)
    assert [(r["user_id"], r["key_hash"]) for r in reg] == [("u0001", "hA"), ("u0002", "hB"), ("u0003", "hNew")]
    kept = next(r for r in reg if r["key_hash"] == "hB")
    assert kept["calls"] == 7 and kept["active_days"] == 3            # 숫자로 — main() 의 집계가 비교한다
    assert next(r for r in reg if r["key_hash"] == "hA")["first_seen_kst"] == old["hA"]["first_seen_kst"]


def test_shrinking_history_stops_without_writing(ur, tmp_path, monkeypatch):
    """쌓이기만 하는 장부에서 호출 수가 줄었다 = 과거를 못 읽었다(백업 경로·클론). 덮어쓰지 않는다."""
    out = tmp_path / "user_registry.csv"
    out.write_text("user_id,key_hash,first_seen_kst,last_seen_kst,active_days,calls,distinct_tools\n"
                   "u0001,hA,2026-06-29T00:00:00+09:00,2026-08-01T00:00:00+09:00,3,50,2\n", encoding="utf-8")
    before = out.read_text(encoding="utf-8")
    with pytest.raises(SystemExit, match="줄어든"):
        ur.merge_registry([("hA", 1, 2, 9, 1, 1)], ur._load_existing(out))
    assert out.read_text(encoding="utf-8") == before


def test_default_registry_sits_next_to_the_drained_backup(ur):
    """등록부 기본 위치는 드레인 백업과 같은 저장소 — 경로를 두 곳에서 정하면 한쪽만 맞는다."""
    import events_drain
    assert ur.default_out() == events_drain.STORAGE_REPO / "usage" / "user_registry.csv"
    assert ur.default_out().parent == events_drain.OUT_DIR.parent
