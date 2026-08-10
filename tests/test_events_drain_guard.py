# -*- coding: utf-8 -*-
"""드레인이 **백업 없이 지우지 못하게** 하는 안전장치. network/DB 0콜.

260810: `events_drain.py` 가 컬럼 7개만 CSV 로 빼고 **행 전체를 DELETE** 하고 있었다.
그 사이 컬럼은 260802·260804·260810 세 번 늘었는데(error_kind·response_bytes·doc_*·
corp_codes·fetch_*·web_wait_ms) 드레인은 한 번도 안 따라와, 돌리는 순간 9컬럼이
**영구 소멸**하는 상태였다. 아직 안 돌아서 손실은 없었다.

이 파일이 지키는 것은 하나 — **지우기 전에 백업을 되읽어 온전한지 확인한다.**
컬럼 목록은 이제 information_schema 에서 파생하므로, 누군가 다시 손으로 박아도
헤더가 스키마와 어긋나 이 검증에서 걸린다(드리프트의 최종 방어선이기도 하다).
"""
from __future__ import annotations

import csv
import importlib.util
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")  # 드레인은 Postgres 전용

_SRC = Path(__file__).resolve().parent.parent / "scripts" / "events_drain.py"


def _mod():
    spec = importlib.util.spec_from_file_location("events_drain", _SRC)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)          # DB 접속은 main() 안에서만 — import 는 안전
    return m


def _write(path: Path, header, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


COLS = ["event_id", "ts_ns", "key_hash"]
ROWS = [("a", 1, "h1"), ("b", 2, "h2")]


def test_intact_backup_passes(tmp_path):
    p = tmp_path / "ok.csv"
    _write(p, COLS, ROWS)
    assert _mod()._verify_backup(p, COLS, len(ROWS)) is True


def test_missing_columns_is_rejected(tmp_path):
    """**이게 260810 의 실제 상태다.** 스키마엔 17컬럼인데 7개만 적고 지우려 했다."""
    p = tmp_path / "short.csv"
    _write(p, COLS[:2], [r[:2] for r in ROWS])
    assert _mod()._verify_backup(p, COLS, len(ROWS)) is False, (
        "컬럼이 빠진 백업을 통과시켰다 — 이 상태로 DELETE 하면 영구 소멸이다")


def test_truncated_backup_is_rejected(tmp_path):
    """쓰기가 반쯤 끊겨도 파일은 남는다. 「썼다」와 「제대로 썼다」는 다르다."""
    p = tmp_path / "trunc.csv"
    _write(p, COLS, ROWS[:1])
    assert _mod()._verify_backup(p, COLS, len(ROWS)) is False


def test_unreadable_backup_is_rejected(tmp_path):
    assert _mod()._verify_backup(tmp_path / "없음.csv", COLS, 1) is False


def test_columns_are_derived_not_hardcoded():
    """컬럼 목록을 코드에 박으면 늘어날 때 조용히 빠지고, 그 뒤에 행이 지워진다 —
    드리프트가 곧 되돌릴 수 없는 손실이 되는 구조라 파생이 유일하게 안전하다."""
    m = _mod()
    assert hasattr(m, "_table_columns"), "스키마에서 컬럼을 읽는 함수가 없다"
    src = _SRC.read_text(encoding="utf-8")
    assert "information_schema.columns" in src
    assert '"SELECT event_id, ts_ns, key_hash, status, tool, latency_ms, is_error' not in src, (
        "옛 하드코딩 SELECT 가 되살아났다")
