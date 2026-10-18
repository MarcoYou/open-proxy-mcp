# -*- coding: utf-8 -*-
"""기업 조회 순위(`usage_tracker --corps`)가 **롤링 전후로 같은 답**을 내는가.

`ops_corp_daily` 는 90일 롤링이다 — 완결 주를 비공개 저장소 `usage/corp_daily/{주}.parquet` 로
내보내고 DB 에서 지운다. DB 만 읽으면 「전 기간」 합계가 조용히 최근 90~96일로 줄어든다
(이벤트 드레인 260817 과 같은 모양). 여기서 지키는 것:

  · DB 첫날 **이전**만 보관본에서 — 경계에서 두 번 세지도, 빠뜨리지도 않는다
  · 보관본과 DB 가 겹쳐도(내보낸 뒤 지우기 전에 멈춘 주) DB 값을 쓴다
  · 칸 순서·개수가 다른 주도 이름으로 맞춘다(`union_by_name`)
  · 보관본이 없으면 종전처럼 DB 만 읽고 **알린다**

Parquet 쪽은 network 0 · DB 0. `corp_report` 전체는 로컬 Docker Postgres 17(`pg17`)에서 돈다.
"""
from __future__ import annotations

import datetime as dt
import importlib
import sys
from pathlib import Path

import pytest

duckdb = pytest.importorskip("duckdb")  # dev 그룹 — 서버 런타임 의존성이 아니다

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

D = dt.date


@pytest.fixture
def ut(tmp_path, monkeypatch):
    """usage_tracker 를 임시 보관 폴더(`<tmp>/corp_daily`)에 물려 놓는다."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    mod = importlib.reload(importlib.import_module("usage_tracker"))
    (tmp_path / "corp_daily").mkdir()
    monkeypatch.setattr(mod, "CORP_ARCHIVE_DIR", tmp_path / "corp_daily")
    return mod


def _week(path: Path, rows, cols=("log_dd", "corp_code", "requests"), extra: dict | None = None):
    """롤링 스크립트가 쓰는 것과 같은 타입(date · 문자열 · int32)의 주 파일."""
    types = {"log_dd": "DATE", "corp_code": "VARCHAR", "requests": "INTEGER"}
    con = duckdb.connect()
    defs = [f"{c} {types[c]}" for c in cols] + [f"{k} VARCHAR" for k in (extra or {})]
    con.execute(f"CREATE TABLE t ({', '.join(defs)})")
    for r in rows:
        rec = dict(zip(("log_dd", "corp_code", "requests"), r))
        con.execute(f"INSERT INTO t VALUES ({', '.join(['?'] * len(defs))})",
                    [rec[c] for c in cols] + list((extra or {}).values()))
    con.execute(f"COPY (SELECT * FROM t) TO '{path.as_posix()}' (FORMAT parquet)")
    con.close()


W1 = [(D(2026, 1, 5), "99000001", 3), (D(2026, 1, 6), "99000002", 2), (D(2026, 1, 11), "99000001", 1)]
W2 = [(D(2026, 1, 12), "99000001", 4), (D(2026, 1, 15), "99000003", 5)]
W3 = [(D(2026, 1, 19), "99000002", 7), (D(2026, 1, 20), "99000001", 1)]


# ── 보관본 읽기 (DB 없음) ─────────────────────────────────────────────────────
def test_no_archive_returns_none(ut, tmp_path):
    assert ut.corp_archive_counts() is None
    ut.CORP_ARCHIVE_DIR = tmp_path / "없는폴더"
    assert ut.corp_archive_counts() is None


def test_weeks_with_different_column_order_line_up_by_name(ut, tmp_path):
    """**핵심.** 나중 주에 칸이 늘거나 순서가 바뀌어도 이름으로 맞춘다 — 위치로 맞추면 값이 밀린다."""
    a = tmp_path / "corp_daily"
    _week(a / "260105-0111.parquet", W1)
    _week(a / "260112-0118.parquet", W2, cols=("requests", "corp_code", "log_dd"), extra={"note": "x"})
    got = ut.corp_archive_counts()
    assert got["counts"] == {"99000001": 8, "99000002": 2, "99000003": 5}
    assert (got["lo"], got["hi"], got["files"]) == (D(2026, 1, 5), D(2026, 1, 15), 2)
    assert got["weeks"] == [D(2026, 1, 5), D(2026, 1, 12)]


def test_boundary_keeps_only_days_before_the_db(ut, tmp_path):
    """DB 첫날부터는 DB 가 정본 — 보관본의 그 날들은 빼고, 몇 날을 뺐는지 알린다."""
    a = tmp_path / "corp_daily"
    _week(a / "260105-0111.parquet", W1)
    _week(a / "260112-0118.parquet", W2)
    got = ut.corp_archive_counts(before=D(2026, 1, 12))
    assert got["counts"] == {"99000001": 4, "99000002": 2}
    assert got["overlap_days"] == 2            # 01-12 · 01-15 (DB 와 겹친 날)
    assert got["hi"] == D(2026, 1, 11)


def test_window_edge_is_exclusive_like_the_db_side(ut, tmp_path):
    """`--corps N` 의 DB 쪽 조건은 `log_dd > current_date - N` — 보관본도 **초과**여야 하루가 안 갈린다."""
    _week(tmp_path / "corp_daily" / "260105-0111.parquet", W1)
    got = ut.corp_archive_counts(after=D(2026, 1, 6))
    assert got["counts"] == {"99000001": 1}
    assert got["lo"] == D(2026, 1, 11)


def test_identical_copies_count_once_conflicting_copies_stop(ut, tmp_path):
    """같은 (날짜, 기업)이 두 파일에 있으면 한 번만 센다. 값이 다르면 어느 쪽이 맞는지 몰라 멈춘다."""
    a = tmp_path / "corp_daily"
    _week(a / "260105-0111.parquet", W1)
    _week(a / "copy-of-week.parquet", W1)      # 이름 규칙 밖의 사본 — 읽되 주 계산에서는 뺀다
    got = ut.corp_archive_counts()
    assert got["counts"] == {"99000001": 4, "99000002": 2} and got["dup_rows"] == 3
    assert got["weeks"] == [D(2026, 1, 5)]
    _week(a / "copy-of-week.parquet", [(D(2026, 1, 5), "99000001", 30)])
    with pytest.raises(SystemExit, match="서로 다른 값"):
        ut.corp_archive_counts()


def test_receipts_and_half_written_files_are_outside_the_glob(ut, tmp_path):
    """롤링이 옆에 두는 영수증(`.manifest.json`)과 내보내기 도중 파일(`.fresh`)은 읽지 않는다."""
    a = tmp_path / "corp_daily"
    _week(a / "260105-0111.parquet", W1)
    (a / "260105-0111.manifest.json").write_text("{}", encoding="utf-8")
    _week(a / ".260112-0118.parquet.fresh", W2)
    assert ut.corp_archive_counts()["files"] == 1


def test_missing_column_stops(ut, tmp_path):
    con = duckdb.connect()
    con.execute(f"COPY (SELECT DATE '2026-01-05' AS log_dd, '99000001' AS corp_code) "
                f"TO '{(tmp_path / 'corp_daily' / '260105-0111.parquet').as_posix()}' (FORMAT parquet)")
    with pytest.raises(SystemExit, match="requests"):
        ut.corp_archive_counts()


def test_gaps_between_weeks_and_before_the_db(ut):
    w = [D(2026, 1, 5), D(2026, 1, 19)]                       # 01-12 주가 빠졌다
    assert ut.corp_archive_gaps(w, db_lo=D(2026, 1, 26)) == [(D(2026, 1, 12), D(2026, 1, 18))]
    # 마지막 보관 주 다음 날부터 DB 첫날 전날까지
    assert ut.corp_archive_gaps([D(2026, 1, 5)], db_lo=D(2026, 1, 19)) == [(D(2026, 1, 12), D(2026, 1, 18))]
    # 롤링 직후의 정상 상태 — 이어져 있으면 0
    assert ut.corp_archive_gaps([D(2026, 1, 5), D(2026, 1, 12)], db_lo=D(2026, 1, 19)) == []
    # 겹쳐도(지우기 전에 멈춘 주) 공백이 아니다
    assert ut.corp_archive_gaps([D(2026, 1, 5), D(2026, 1, 12)], db_lo=D(2026, 1, 12)) == []
    # 창 밖은 빼고, 걸친 것은 창 안만
    assert ut.corp_archive_gaps(w, db_lo=D(2026, 1, 26), after=D(2026, 1, 20)) == []
    assert ut.corp_archive_gaps(w, db_lo=D(2026, 1, 26), after=D(2026, 1, 14)) == [(D(2026, 1, 15), D(2026, 1, 18))]


# ── corp_report 전체 (로컬 Postgres 17) ──────────────────────────────────────
@pytest.fixture
def pgut(ut, pg17, monkeypatch):
    import psycopg
    monkeypatch.setattr(ut, "DATABASE_URL", pg17)
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute("CREATE TABLE ops_corp_daily(log_dd date NOT NULL, corp_code text NOT NULL, "
                  "requests int NOT NULL DEFAULT 0, PRIMARY KEY (log_dd, corp_code))")
    return ut


def _db(url, sql, rows=()):
    import psycopg
    with psycopg.connect(url, autocommit=True) as c:
        if rows:
            c.cursor().executemany(sql, rows)
        else:
            return c.execute(sql).fetchone() if sql.lstrip().upper().startswith("SELECT") else c.execute(sql)


_INS = "INSERT INTO ops_corp_daily(log_dd, corp_code, requests) VALUES (%s, %s, %s)"


def _body(out: str) -> list[str]:
    """보관본 안내 줄을 뺀 본문 — 롤링 전후로 이것이 같아야 한다."""
    return [ln for ln in out.splitlines() if not ln.startswith("  · 보관본")]


def test_rolling_does_not_change_the_answer(pgut, pg17, tmp_path, capsys):
    """**목적 그 자체.** 앞 두 주를 Parquet 으로 옮기고 DB 에서 지워도 순위·합계·기간이 같다."""
    _db(pg17, _INS, W1 + W2 + W3)
    pgut.corp_report(top=10)
    before = capsys.readouterr()
    assert "총 23건 · 3개사 · 2026-01-05 ~ 2026-01-20" in before.out
    assert "보관본이 없다" in before.err                  # 없으면 종전처럼 + 안내

    a = tmp_path / "corp_daily"
    _week(a / "260105-0111.parquet", W1)
    _week(a / "260112-0118.parquet", W2)
    _db(pg17, "DELETE FROM ops_corp_daily WHERE log_dd < '2026-01-19'")
    pgut.corp_report(top=10)
    after = capsys.readouterr()
    assert _body(after.out) == _body(before.out)
    assert "보관본 2026-01-05 ~ 2026-01-15 + DB 2026-01-19 ~ 2026-01-20" in after.out
    assert "보관본이 없다" not in after.err and "어디에도" not in after.err

    # 종전 코드라면 — DB 만 읽어 앞 두 주가 사라진다(이 시험이 헛돌지 않는다는 대조).
    assert _db(pg17, "SELECT sum(requests) FROM ops_corp_daily")[0] == 8


def test_week_exported_but_not_yet_deleted_is_not_counted_twice(pgut, pg17, tmp_path, capsys):
    """롤링이 내보낸 뒤 지우기 전에 멈춘 주 — 보관본과 DB 양쪽에 있어도 한 번만."""
    _db(pg17, _INS, W2 + W3)
    a = tmp_path / "corp_daily"
    _week(a / "260105-0111.parquet", W1)
    _week(a / "260112-0118.parquet", W2)
    pgut.corp_report(top=10)
    got = capsys.readouterr()
    assert "총 23건 · 3개사 · 2026-01-05 ~ 2026-01-20" in got.out
    assert "2일 겹친다" in got.err


def test_window_reaching_into_the_archive(pgut, pg17, tmp_path, capsys):
    """`--corps N` 의 창이 DB 첫날보다 앞으로 가면 그 부분은 보관본에서 — 같은 초과 경계로."""
    _db(pg17, _INS, W3)
    a = tmp_path / "corp_daily"
    _week(a / "260105-0111.parquet", W1)
    _week(a / "260112-0118.parquet", W2)
    today = _db(pg17, "SELECT current_date")[0]
    pgut.corp_report(top=10, days=(today - D(2026, 1, 8)).days)      # 창 = 01-09 부터
    out = capsys.readouterr().out
    assert "총 18건 · 3개사 · 2026-01-11 ~ 2026-01-20" in out
    ranks = {ln.split()[0]: int(ln.split()[1]) for ln in out.splitlines() if ln.startswith("  990")}
    assert ranks == {"99000001": 6, "99000002": 7, "99000003": 5}


def test_window_inside_the_db_needs_no_archive(pgut, pg17, capsys):
    """창이 DB 안에서 끝나면 보관본은 원래 쓸 데가 없다 — 「없다」 안내도 내지 않는다."""
    today = _db(pg17, "SELECT current_date")[0]
    _db(pg17, _INS, [(today - dt.timedelta(days=40), "99000001", 2), (today, "99000002", 3)])
    pgut.corp_report(top=10, days=7)
    got = capsys.readouterr()
    assert "총 3건 · 1개사" in got.out
    assert "보관본" not in got.err


def test_missing_week_between_archive_and_db_is_announced(pgut, pg17, tmp_path, capsys):
    """보관본 클론이 오래돼 마지막 주가 없으면 — 합계가 조용히 줄지 않게 빈 구간을 말한다."""
    _db(pg17, _INS, W3)
    _week(tmp_path / "corp_daily" / "260105-0111.parquet", W1)
    pgut.corp_report(top=10)
    got = capsys.readouterr()
    assert "2026-01-12 ~ 2026-01-18 가 보관본에도 DB 에도 없다" in got.err
