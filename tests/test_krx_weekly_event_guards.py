# -*- coding: utf-8 -*-
"""감사 목록 밖에서 추가로 확인해 고친 두 배치 — krx_stock_flags(딱지) · adj_factor_v3(수정계수 라벨).

둘 다 이벤트 전후의 krx_weekly 점(직전 주·직후 주)을 읽는다. 표를 최근 3년 + 연말 주로 줄이면 하한 이전 이벤트의
전후 점이 1년 간격 연말 주로 잡혀 가짜 경고·영구 오라벨이 생긴다(실측 261017). 하한(+14일) 이전 이벤트는 평가하지 않는다.
network 0 · DB 0 — SQL 문자열로 응답하는 가짜 연결을 쓴다.
"""
from __future__ import annotations

import sys

from open_proxy_mcp import krx_retention as R
from scripts import adj_factor_v3 as A
from scripts import krx_stock_flags as F


# ── 가짜 연결 ───────────────────────────────────────────────────────────────────
class _Res:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class _Copy:
    def __init__(self, con):
        self.con = con

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def write_row(self, rec):
        self.con.copied.append(rec)


class _Cur:
    def __init__(self, con):
        self.con = con
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.con.statements.append((sql, params))
        if sql.lstrip().upper().startswith("DELETE"):
            self.rowcount = 7

    def executemany(self, sql, seq):
        self.con.many.append((sql, list(seq)))

    def copy(self, sql):
        return _Copy(self.con)


class FakeCon:
    """handler(sql, params) → 행 목록. 실행된 모든 문장을 `statements` 에, executemany 를 `many` 에 쌓는다."""

    def __init__(self, handler):
        self.handler = handler
        self.statements: list[tuple] = []
        self.many: list[tuple] = []
        self.copied: list[tuple] = []
        self.committed = 0
        self.autocommit = False
        self.closed = False

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        return _Res(self.handler(sql, params))

    def cursor(self):
        return _Cur(self)

    def commit(self):
        self.committed += 1

    def rollback(self):
        pass

    def close(self):
        self.closed = True

    def sqls(self) -> list[str]:
        return [" ".join(s.split()) for s, _ in self.statements]


def _norm(sql: str) -> str:
    return " ".join(sql.split())


def test_stock_flags_evaluates_only_events_after_the_pair_floor(monkeypatch, capsys):
    seen = {}

    def handler(sql, params):
        s = _norm(sql)
        if s == "SELECT max(price_dd) FROM krx_weekly":
            return [("20261016",)]
        if s.startswith("SELECT count(*) FROM krx_shares_ledger"):
            seen["n_old_params"] = params
            return [(26,)]
        if s.startswith("WITH e AS"):
            seen["events_params"] = params
            return [("017670", "20260105", 0.5, 1.0)]
        if s.startswith("SELECT flag, count(*) FROM krx_stock_flags"):
            return [("unresolved_adjustment", 1)]
        return []

    con = FakeCon(handler)
    monkeypatch.setattr(F.psycopg, "connect", lambda *a, **k: con)
    monkeypatch.setenv("DATABASE_URL", "unused")
    monkeypatch.setattr(sys, "argv", ["krx_stock_flags.py", "--dry"])
    assert F.main() == 0
    floor = R.krx_weekly_pair_floor("20261016")
    assert floor == "20231030"
    assert seen["events_params"] == (0.90, floor, 0.15) and seen["n_old_params"] == (0.90, floor)
    assert "평가 제외       : 26이벤트" in capsys.readouterr().out
    assert con.many == []


def test_stock_flags_stops_when_prices_are_empty(monkeypatch):
    con = FakeCon(lambda sql, params: [(None,)] if "max(price_dd)" in sql else [])
    monkeypatch.setattr(F.psycopg, "connect", lambda *a, **k: con)
    monkeypatch.setenv("DATABASE_URL", "unused")
    monkeypatch.setattr(sys, "argv", ["krx_stock_flags.py"])
    assert F.main() == 1
    assert not any("WITH e AS" in s for s in con.sqls()) and con.many == []


def test_adj_factor_skips_unlabeled_events_before_the_pair_floor(monkeypatch, capsys):
    seen = {}

    def handler(sql, params):
        s = _norm(sql)
        if s == "SELECT max(price_dd) FROM krx_weekly":
            return [("20261016",)]
        if s.startswith("SELECT e.ticker, e.event_dd, e.adj_factor_raw"):
            seen["resets_params"] = params
            return [("005930", "20260105", 0.5, "KS", "KS")]
        if s.startswith("SELECT count(*) FROM krx_adj_events WHERE (label_confidence IS NULL"):
            seen["n_old_params"] = params
            return [(1240,)]
        if s == "SELECT count(*) FROM krx_adj_events":
            return [(3760,)]
        if s.startswith("SELECT count(*) FROM krx_adj_events WHERE label_confidence <>"):
            return [(1635,)]
        return []

    con = FakeCon(handler)
    monkeypatch.setattr(A.psycopg, "connect", lambda *a, **k: con)
    monkeypatch.setenv("DATABASE_URL", "unused")
    A.main()
    assert seen["resets_params"] == ("20231030",) and seen["n_old_params"] == ("20231030",)
    out = capsys.readouterr().out
    assert "라벨 미판정 1건 처리" in out and "미판정 1,240건은 전후 주간 점이 연말 행이라 건너뜀" in out
    (_, rows), = con.many
    assert rows[0][-2:] == ("005930", "20260105")                      # 하한 안쪽 이벤트는 종전대로 UPDATE
