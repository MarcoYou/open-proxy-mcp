# -*- coding: utf-8 -*-
"""드레인이 **백업 없이 지우지 못하게** 하는 안전장치. network/DB 0콜.

260810: `events_drain.py` 가 컬럼 7개만 CSV 로 빼고 **행 전체를 DELETE** 하고 있었다.
그 사이 컬럼은 260802·260804·260810 세 번 늘었는데(error_kind·response_bytes·doc_*·
corp_codes·fetch_*·web_wait_ms) 드레인은 한 번도 안 따라와, 돌리는 순간 9컬럼이
**영구 소멸**하는 상태였다. 아직 안 돌아서 손실은 없었다.

260904: 백업 형식이 CSV → parquet(zstd). 타입은 PG 스키마에서 명시해 넘긴다 — 추론에 맡기면
빈칸 많은 정수 열이 DOUBLE 로 굳고, 되읽는 쪽이 int 를 기대하면 조용히 갈린다.

이 파일이 지키는 것은 하나 — **지우기 전에 백업을 되읽어 온전한지 확인한다.**
컬럼 목록은 이제 information_schema 에서 파생하므로, 누군가 다시 손으로 박아도
컬럼이 스키마와 어긋나 이 검증에서 걸린다(드리프트의 최종 방어선이기도 하다).
"""
from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")  # 드레인은 Postgres 전용
duckdb = pytest.importorskip("duckdb")    # parquet 쓰기·되읽기 — dev 그룹

_SRC = Path(__file__).resolve().parent.parent / "scripts" / "events_drain.py"


def _mod():
    spec = importlib.util.spec_from_file_location("events_drain", _SRC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)          # DB 접속은 main() 안에서만 — import 는 안전
    return m


def _write(m, path: Path, cols, rows):
    """드레인과 **같은 경로**로 쓴다 — 임시 CSV → `_write_parquet`(PG 타입 명시)."""
    tmp = path.with_suffix(".tmp.csv")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([n for n, _t in cols])
        w.writerows(rows)
    m._write_parquet(tmp, path, cols)
    tmp.unlink()


#: (컬럼, PG data_type) — `_table_columns` 가 information_schema 에서 주는 모양 그대로
COLS = [("event_id", "text"), ("ts_ns", "bigint"), ("key_hash", "text")]
ROWS = [("a", 1, "h1"), ("b", 2, "h2")]


def test_intact_backup_passes(tmp_path):
    m = _mod()
    p = tmp_path / "ok.parquet"
    _write(m, p, COLS, ROWS)
    assert m._verify_backup(p, COLS, len(ROWS)) is True


def test_types_come_from_schema_not_inference(tmp_path):
    """빈칸이 섞인 정수 열도 BIGINT 로 남아야 한다 — 추론이면 DOUBLE 이 된다."""
    m = _mod()
    p = tmp_path / "typed.parquet"
    cols = COLS + [("latency_ms", "bigint"), ("is_error", "boolean")]
    _write(m, p, cols, [("a", 1, "h1", "", "t"), ("b", 2, "h2", 7, "f")])
    types = {r[0]: r[1] for r in duckdb.sql(f"DESCRIBE SELECT * FROM read_parquet('{p.as_posix()}')").fetchall()}
    assert types["ts_ns"] == "BIGINT" and types["latency_ms"] == "BIGINT" and types["is_error"] == "BOOLEAN"
    rows = duckdb.sql(f"SELECT latency_ms, is_error FROM read_parquet('{p.as_posix()}') ORDER BY ts_ns").fetchall()
    assert rows == [(None, True), (7, False)]


def test_missing_columns_is_rejected(tmp_path):
    """**이게 260810 의 실제 상태다.** 스키마엔 17컬럼인데 7개만 적고 지우려 했다."""
    m = _mod()
    p = tmp_path / "short.parquet"
    _write(m, p, COLS[:2], [r[:2] for r in ROWS])
    assert m._verify_backup(p, COLS, len(ROWS)) is False, (
        "컬럼이 빠진 백업을 통과시켰다 — 이 상태로 DELETE 하면 영구 소멸이다")


def test_truncated_backup_is_rejected(tmp_path):
    """쓰기가 반쯤 끊겨도 파일은 남는다. 「썼다」와 「제대로 썼다」는 다르다."""
    m = _mod()
    p = tmp_path / "trunc.parquet"
    _write(m, p, COLS, ROWS[:1])
    assert m._verify_backup(p, COLS, len(ROWS)) is False


def test_duplicated_event_ids_are_rejected(tmp_path):
    """행수는 맞는데 event_id 가 겹치면 — 되읽을 때 중복 제거로 한 건이 사라진다. 지우면 안 된다."""
    m = _mod()
    p = tmp_path / "dup.parquet"
    _write(m, p, COLS, [("a", 1, "h1"), ("a", 2, "h2")])
    assert m._verify_backup(p, COLS, 2) is False


def test_unreadable_backup_is_rejected(tmp_path):
    assert _mod()._verify_backup(tmp_path / "없음.parquet", COLS, 1) is False


def test_columns_are_derived_not_hardcoded():
    """컬럼 목록을 코드에 박으면 늘어날 때 조용히 빠지고, 그 뒤에 행이 지워진다 —
    드리프트가 곧 되돌릴 수 없는 손실이 되는 구조라 파생이 유일하게 안전하다."""
    m = _mod()
    assert hasattr(m, "_table_columns"), "스키마에서 컬럼을 읽는 함수가 없다"
    src = _SRC.read_text(encoding="utf-8")
    assert "information_schema.columns" in src
    assert '"SELECT event_id, ts_ns, key_hash, status, tool, latency_ms, is_error' not in src, (
        "옛 하드코딩 SELECT 가 되살아났다")


def test_module_imports_without_duckdb(monkeypatch):
    """usage_tracker·drain_backlog_check 는 이 모듈의 **경로·시계 상수만** 가져온다 —
    duckdb 가 없는 환경(서버 이미지는 --no-dev)에서도 import 는 되어야 한다."""
    import builtins
    real = builtins.__import__

    def fake(name, *a, **k):
        if name == "duckdb":
            raise ImportError("no duckdb")
        return real(name, *a, **k)
    monkeypatch.setattr(builtins, "__import__", fake)
    m = _mod()
    assert m.OUT_DIR.name == "events" and m.OUT_DIR.parent.name == "usage"


def test_rerun_preserves_previous_archive_and_receipt(tmp_path):
    import datetime as dt
    import json
    m = _mod()
    start = dt.datetime(2026, 8, 31, tzinfo=m.KST)
    end = start + dt.timedelta(days=7)
    p = tmp_path / "260831-0906.parquet"
    source = tmp_path / "source.csv"

    def archive(rows):
        with source.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([n for n, _ in COLS])
            writer.writerows(rows)
        m._archive_week(source, p, COLS, start, end)

    a = ("a", m._to_ns(start), "h1")
    b = ("b", m._to_ns(start + dt.timedelta(days=1)), "h2")
    archive([a])
    archive([b])
    receipt = p.with_suffix(".manifest.json").read_bytes()
    assert json.loads(receipt)["rows"] == 2
    archive([b])
    assert p.with_suffix(".manifest.json").read_bytes() == receipt
    assert duckdb.sql(f"SELECT event_id FROM read_parquet('{p}') ORDER BY event_id").fetchall() == [("a",), ("b",)]


def test_conflicting_event_does_not_replace_backup(tmp_path):
    import datetime as dt
    m = _mod()
    start = dt.datetime(2026, 8, 31, tzinfo=m.KST)
    end = start + dt.timedelta(days=7)
    p, source = tmp_path / "week.parquet", tmp_path / "source.csv"
    _write(m, p, COLS, [("a", m._to_ns(start), "h1")])
    original = p.read_bytes()
    with source.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([n for n, _ in COLS])
        writer.writerow(("a", m._to_ns(start), "changed"))
    with pytest.raises(ValueError, match="내용이 달라"):
        m._archive_week(source, p, COLS, start, end)
    assert p.read_bytes() == original


def test_rerun_after_columns_were_added_keeps_old_rows_and_new_columns(tmp_path):
    """261018: 기록 표에 `scope`·`as_of` 가 붙었다(재설계 T8). 옛 칸 구성으로 이미 보관된 주에 늦은 행이 와서 새 칸 구성으로
    다시 드레인하면, 옛 행은 그대로(새 칸은 NULL) · 새 행은 새 칸 값까지 남아야 한다 — 이름으로 맞춰 합친다(union_by_name)."""
    import datetime as dt
    m = _mod()
    start = dt.datetime(2026, 10, 12, tzinfo=m.KST)
    end = start + dt.timedelta(days=7)
    p, source = tmp_path / "261012-1018.parquet", tmp_path / "source.csv"
    old = ("a", m._to_ns(start), "h1")
    _write(m, p, COLS, [old])                              # 옛 칸 구성의 보관본
    new_cols = COLS + [("scope", "text"), ("as_of", "date")]
    with source.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([n for n, _ in new_cols])
        writer.writerow(("b", m._to_ns(start + dt.timedelta(days=1)), "h2", "quote", "2026-10-16"))
    m._archive_week(source, p, new_cols, start, end)
    got = duckdb.sql(f'SELECT event_id, key_hash, "scope", CAST(as_of AS VARCHAR) FROM read_parquet(\'{p}\') '
                     "ORDER BY event_id").fetchall()
    assert got == [("a", "h1", None, None), ("b", "h2", "quote", "2026-10-16")]
