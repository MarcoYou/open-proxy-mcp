"""조용한 대체 계측 — 260824 신설. network 0콜·DB 0콜.

계기: `screener` 유니버스 폴백이 「krx_weekly 조회 실패 → 전체시장으로 대체」를
**모든 kospi200 호출에서 100% 발화**하고 있었다(260823 개명이 KS/KQ 로 바꾸면서 질의가
0건을 냈다). 그 문장은 사용자 응답에 실려 나갔지만 우리가 보는 곳 어디에도 안 쌓였고,
오류율은 1% 대로 조용했다. 실측 타임라인:

    08-23 16:26  개명 커밋 (KOSPI → KS)
    08-23 09시   screener 1건   ← 정상
    08-23 19시   screener 12건  ← 개명 2시간 반 뒤
    08-23~24 밤  58건

**에러가 아니라 대체로 나타나는 고장**을 보려면 대체를 세야 한다. 이 파일이 그 계약이다.
"""
from __future__ import annotations

import pytest

from open_proxy_mcp.dart.client import (
    DEGRADATION_KINDS,
    _ctx_ledger,
    degradations,
    new_request_ledger,
    note_degradation,
)


@pytest.fixture(autouse=True)
def _clean():
    _ctx_ledger.set(None)
    yield
    _ctx_ledger.set(None)


def test_note_without_ledger_is_silent():
    """미들웨어를 안 거친 경로(스크립트·배치·테스트)에서 절대 터지면 안 된다."""
    note_degradation("universe_fallback")
    assert degradations() == []


def test_note_records_kind_only():
    new_request_ledger()
    note_degradation("universe_fallback")
    assert degradations() == ["universe_fallback"]


def test_same_kind_counts_once_per_request():
    """한 요청이 같은 대체를 여러 번 밟아도 한 번이다 — 안 그러면 재시도 많은 요청이
    지표를 혼자 끌어올린다."""
    new_request_ledger()
    for _ in range(5):
        note_degradation("universe_fallback")
    note_degradation("period_fallback")
    assert degradations() == ["universe_fallback", "period_fallback"]


def test_ledger_is_bounded():
    new_request_ledger()
    for i in range(100):
        note_degradation(f"k{i}")
    assert len(degradations()) <= 16


def test_kinds_are_a_closed_vocabulary():
    """오타로 새 범주가 생기면 집계가 조용히 갈라진다 — 그게 이 계측이 고치려는 병이다."""
    assert "universe_fallback" in DEGRADATION_KINDS
    assert "period_fallback" in DEGRADATION_KINDS
    assert all(k.islower() and " " not in k for k in DEGRADATION_KINDS)


def test_every_call_site_uses_a_declared_kind():
    """★ 정적 검사 — 호출부의 문자열이 닫힌 목록 안에 있는지 본다.

    런타임에 막으면(예외) 서빙 경로가 위험해지고, 조용히 받으면 오타가 유령 범주를 만든다.
    그래서 **테스트 시점에** 잡는다.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent / "open_proxy_mcp"
    found = []
    for f in root.rglob("*.py"):
        for m in re.finditer(r'note_degradation\(\s*"([^"]+)"\s*\)', f.read_text(encoding="utf-8")):
            found.append((f.name, m.group(1)))
    assert found, "계측 호출부가 하나도 없다"
    bad = [(f, k) for f, k in found if k not in DEGRADATION_KINDS]
    assert not bad, f"닫힌 목록에 없는 종류: {bad}"


def test_the_universe_fallback_that_bit_us_is_instrumented():
    """이 계측을 만든 바로 그 지점이 실제로 물려 있나."""
    import inspect

    from open_proxy_mcp.services import screener
    src = inspect.getsource(screener.resolve_universe)
    assert src.count('note_degradation("universe_fallback")') >= 3, \
        "유니버스 폴백 경로 일부가 계측 없이 남았다"


def test_middleware_passes_degraded_column():
    import inspect

    from open_proxy_mcp import server
    src = inspect.getsource(server)
    assert "degraded=(" in src, "미들웨어가 degraded 를 안 넘긴다"


def test_recorder_accepts_and_orders_degraded():
    """컬럼 순서 SSOT 에 들어 있어야 값이 제 칸에 들어간다."""
    from open_proxy_mcp.usage import _EVENT_COLUMNS
    assert _EVENT_COLUMNS[-1] == "degraded"
    assert len(set(_EVENT_COLUMNS)) == len(_EVENT_COLUMNS), "컬럼 이름이 중복이다"


# ── 집계 쪽 ───────────────────────────────────────────────────────────
def _tracker():
    import importlib.util
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    spec = importlib.util.spec_from_file_location("ut", root / "scripts" / "usage_tracker.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_drained_rows_without_the_column_are_not_a_fake_category():
    """★ 이 지표가 **첫 실행에서 조용히 틀렸던** 지점이다.

    드레인 백업에는 이 컬럼이 없어 `merge_drained` 가 None 으로 채운다. DB 쪽
    `WHERE degraded IS NOT NULL` 은 합류분에 안 걸리므로, 거르지 않으면 `str(None)` 이
    "None" 이라는 유령 범주가 되어 65,500건으로 잡힌다(260824 실측).
    """
    ut = _tracker()
    rows = [(1, "h1", "screener", None),          # 드레인 합류분
            (2, "h1", "screener", ""),            # 빈 값
            (3, "h2", "screener", "universe_fallback"),
            (4, "h3", "screener", "universe_fallback,period_fallback")]
    kinds, per_tool, per_day, users = ut.degradation_stats(rows)
    assert "None" not in kinds and "" not in kinds
    assert kinds == {"universe_fallback": 2, "period_fallback": 1}
    assert len(users["universe_fallback"]) == 2


def test_protocol_and_self_calls_are_excluded():
    ut = _tracker()
    rows = [(1, "h1", "initialize", "universe_fallback"),
            (2, "h1", None, "universe_fallback"),
            (3, "h2", "screener", "universe_fallback")]
    kinds, *_ = ut.degradation_stats(rows)
    assert kinds == {"universe_fallback": 1}, "핸드셰이크가 섞였다"
