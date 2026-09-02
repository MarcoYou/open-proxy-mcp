# -*- coding: utf-8 -*-
"""드레인된 과거 주 합류 — **DB 만 읽으면 지운 만큼 과거가 사라진다.** network 0콜.

260817 사고: 7주를 드레인하자 events 가 362,994행 → 5,511행이 됐다. 백업 CSV 는 정상이었지만
**그걸 되읽는 코드가 하나도 없어서**, 통계·덱이 「진행 중인 주」만 보게 됐다. 오래 쓴 사용자가
전부 「신규」로 재라벨됐고, 드레인 스크립트는 이 부작용을 경고까지 하고 있었다 —
경고만 있고 합류 지점이 없었다. 백업이 아니라 무덤이었다.

여기서 지키는 것은 「읽는다」가 아니라 **「읽되 어긋나지 않는다」**이다:
  · 주마다 헤더가 다르다(컬럼이 260802·260804·260810·260817 로 늘었다). 헤더를 합집합으로
    먼저 안 잡으면 늦게 생긴 열만 짧아져 **행이 통째로 밀린다**(실측: weak_kinds 에서 깨졌다)
  · 투영 순서가 SELECT 순서와 달라지면 값이 **조용히 다른 컬럼으로** 들어간다
    (260704 mkt_fund_hist 사고와 같은 실패 모드 — 에러가 안 나서 더 위험하다)
"""
from __future__ import annotations

import csv
import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))


@pytest.fixture
def ut(tmp_path, monkeypatch):
    """usage_tracker 를 임시 백업 폴더에 물려 놓는다. DB 는 안 쓴다."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    mod = importlib.reload(importlib.import_module("usage_tracker"))
    monkeypatch.setattr(mod, "DRAINED_DIR", tmp_path)
    monkeypatch.setattr(mod, "_drained_cache", None)
    monkeypatch.setattr(mod, "_db_event_ids", lambda: set())
    return mod


def _write(path: Path, cols, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows(rows)


def test_headers_differ_across_weeks_and_rows_still_line_up(ut, tmp_path):
    """**핵심.** 옛 주에는 없던 컬럼이 새 주에 생긴다 — 그래도 행이 밀리면 안 된다."""
    _write(tmp_path / "260601-0607_user_log.csv",
           ["event_id", "ts_ns", "key_hash", "tool", "latency_ms", "is_error"],
           [["e1", 1000, "hA", "treasury_share", 11, "False"],
            ["e2", 2000, "hB", "company", 22, "True"]])
    # 다음 주에 weak_kinds 가 생겼다(260810 실제 사례)
    _write(tmp_path / "260608-0614_user_log.csv",
           ["event_id", "ts_ns", "key_hash", "tool", "latency_ms", "is_error", "weak_kinds"],
           [["e3", 3000, "hC", "valuation", 33, "", "fuzzy"]])

    d = ut.drained_columns()
    assert len({len(v) for v in d.values()}) == 1, f"열 길이가 어긋났다: {[(k, len(v)) for k, v in d.items()]}"
    assert len(d["ts_ns"]) == 3
    # 옛 주는 그 열을 **가진 적이 없다** — 0 이 아니라 None 이 맞다.
    assert d["weak_kinds"] == [None, None, "fuzzy"]


def test_projection_follows_the_select_order(ut, tmp_path):
    """투영은 SELECT 순서를 그대로 따라야 한다 — 어긋나면 에러 없이 값만 뒤바뀐다."""
    _write(tmp_path / "260601-0607_user_log.csv",
           ["event_id", "ts_ns", "key_hash", "tool", "latency_ms", "is_error"],
           [["e1", 1000, "hA", "treasury_share", 11, "False"]])

    assert ut.merge_drained([], ("tool", "key_hash", "latency_ms", "is_error")) == \
        [("treasury_share", "hA", 11, False)]
    # 순서를 바꾸면 결과도 바뀐다 — 이 대조가 있어야 위 단언이 우연이 아니다.
    assert ut.merge_drained([], ("key_hash", "tool")) == [("hA", "treasury_share")]
    # 타입도 맞아야 한다. CSV 는 전부 문자열이라 안 고치면 하류 계산이 조용히 갈린다.
    (tool, kh, lat, err), = ut.merge_drained([], ("tool", "key_hash", "latency_ms", "is_error"))
    assert isinstance(lat, int) and err is False


def test_db_rows_come_first_and_are_untouched(ut, tmp_path):
    _write(tmp_path / "260601-0607_user_log.csv",
           ["event_id", "ts_ns", "key_hash"], [["e1", 1000, "hA"]])
    out = ut.merge_drained([(9999, "hDB")], ("ts_ns", "key_hash"))
    assert out == [(9999, "hDB"), (1000, "hA")]


def test_rows_already_in_the_db_are_not_counted_twice(ut, tmp_path, monkeypatch):
    """드레인은 내보낸 뒤 지우니 원래 안 겹친다. **중단·재실행이면 겹친다** —
    겹침을 가정하지 않는 쪽이 위험하다(모든 지표가 부풀어도 아무도 모른다)."""
    _write(tmp_path / "260601-0607_user_log.csv",
           ["event_id", "ts_ns", "key_hash"], [["e1", 1000, "hA"], ["e2", 2000, "hB"]])
    monkeypatch.setattr(ut, "_db_event_ids", lambda: {"e1"})
    monkeypatch.setattr(ut, "_drained_cache", None)
    assert ut.merge_drained([], ("ts_ns", "key_hash")) == [(2000, "hB")]


def test_missing_backup_is_loud_not_silent(ut, tmp_path, capsys):
    """**조용한 빈 결과가 이 사고의 원인이었다.** 없으면 반드시 경고한다."""
    assert ut.drained_columns() == {}
    assert "드레인 백업이 없다" in capsys.readouterr().err


def test_only_user_log_files_are_read(ut, tmp_path):
    """같은 폴더의 `*_purged.csv` 는 **일부러 걷어낸 테스트 오염**이라 되살리면 안 된다."""
    _write(tmp_path / "260601-0607_user_log.csv",
           ["event_id", "ts_ns", "key_hash"], [["e1", 1000, "hA"]])
    _write(tmp_path / "260810_test-pollution_purged.csv",
           ["event_id", "ts_ns", "key_hash"], [["bad", 1, "hPollution"]])
    _write(tmp_path / "user_registry.csv", ["key_hash", "note"], [["hA", "x"]])
    assert ut.merge_drained([], ("key_hash",)) == [("hA",)]


def test_select_order_constants_match_the_queries():
    """`_TL_COLS` 등은 **쿼리가 고른 순서의 사본**이다. 사본은 갈라진다 — 여기서 대조한다."""
    src = (Path(__file__).resolve().parent.parent / "scripts" / "usage_tracker.py").read_text("utf-8")
    for const, select in (
        ("_TL_COLS", "SELECT tool, key_hash, latency_ms, is_error FROM"),
        ("_ERR_COLS", "SELECT ts_ns, key_hash, is_error FROM"),
        ("_OUT_COLS", 'cols = "key_hash, tool, is_error, error_kind, weak_kinds"'),
        ("_PATH_COLS", "SELECT ts_ns, tool, doc_misses, fetch_viewer, fetch_kind, web_wait_ms "),
    ):
        i = src.index(f"{const} = (")
        names = [n.strip().strip('"') for n in src[i + len(const) + 4:src.index(")", i)].split(",")
                 if n.strip()]
        assert select in src, f"{const} 이 가리키는 쿼리가 사라졌다: {select}"
        head = src[src.index(select):]
        head = head[:head.index("FROM") if "FROM" in head[:200] else 200]
        for n in names:
            assert n in head, f"{const} 의 {n} 이 실제 SELECT 에 없다 — 투영이 밀린다"
