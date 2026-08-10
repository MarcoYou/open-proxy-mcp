# -*- coding: utf-8 -*-
"""요청 장부(request ledger)가 **자식 task 에서 고쳐도 위에서 보이는지** 검증. network 0콜.

260804 사고: 종전 `_ctx_doc_cache_hit` 은 하류가 `.set()` 하고 미들웨어가 `.get()` 하는
구조였다. ContextVar 는 아래로만 흐르므로 이건 원리상 동작하지 않는다 — 실측으로
266,615건 전부 NULL 이었다. 그런데 **테스트는 하나도 없었다.** 값이 안 들어오는 건
아무것도 깨뜨리지 않아(요청은 정상, 통계만 빈다) 조용히 두 달을 갔다.

그래서 여기서는 「기록기가 값을 받는가」가 아니라 **「하류에서 적은 게 위에서 보이는가」**
를 본다. 그게 실제로 깨졌던 지점이다.
"""
from __future__ import annotations

import asyncio
import sqlite3

import pytest

from open_proxy_mcp.dart.client import (
    _LEDGER_MAX_CORPS,
    _ctx_ledger,
    _note_corp,
    _note_doc,
    new_request_ledger,
)


@pytest.fixture(autouse=True)
def _restore_usage_module():
    """아래 두 테스트는 `importlib.reload` 로 usage 를 갈아끼운다. 그 상태는
    **모듈 객체에 남아 뒤 테스트로 샌다** — monkeypatch 는 환경변수만 되돌리고
    모듈은 안 되돌린다. 실제로 이것 때문에 다른 파일의 「로컬은 기록 안 한다」
    게이트 테스트가 `_RECORDING=True` 를 보고 깨졌다. 여기서 원상복구한다."""
    import importlib
    import os
    yield
    for name in ("OPM_USAGE_LOCAL", "OPM_USAGE_DB_PATH"):
        os.environ.pop(name, None)
    importlib.reload(importlib.import_module("open_proxy_mcp.usage"))


def test_ledger_starts_empty():
    led = new_request_ledger()
    assert led == {
        "doc_mem_hits": 0, "doc_disk_hits": 0, "doc_misses": 0,
        "corp_codes": [], "weak_resolutions": [],
    }


def test_note_without_ledger_is_silent():
    """스크립트·테스트처럼 미들웨어를 안 거친 경로에서도 절대 터지지 않아야 한다."""
    _ctx_ledger.set(None)
    _note_doc("doc_mem_hits")
    _note_corp("00126380")          # 예외가 안 나면 통과


def test_child_task_writes_are_visible_to_the_parent():
    """**이게 핵심이다.** 종전 방식(ContextVar.set)이 깨졌던 바로 그 지점.

    미들웨어(부모)가 장부를 만들고, tool(자식 task)이 적고, 미들웨어가 읽는다.
    """
    async def tool_running_in_child():
        _note_doc("doc_mem_hits")
        _note_doc("doc_misses")
        _note_corp("00126380")

    async def middleware():
        led = new_request_ledger()
        await asyncio.create_task(tool_running_in_child())   # 별도 task = 문맥 사본
        return led

    led = asyncio.run(middleware())
    assert led["doc_mem_hits"] == 1
    assert led["doc_misses"] == 1
    assert led["corp_codes"] == ["00126380"]


def test_old_contextvar_style_would_have_failed():
    """대조군 — 종전 방식이 왜 못 쓰는지 못박아 둔다. 이 테스트가 깨지면 파이썬 의미론이
    바뀐 것이므로, 위 구조를 되돌려도 되는지 다시 판단해야 한다."""
    from contextvars import ContextVar
    v: ContextVar[bool | None] = ContextVar("probe", default=None)

    async def child():
        v.set(True)

    async def parent():
        v.set(None)
        await asyncio.create_task(child())
        return v.get()

    assert asyncio.run(parent()) is None      # 자식이 set 해도 부모는 못 본다


def test_corp_codes_dedupe_and_cap():
    """같은 기업을 여러 번 봐도 한 번, 시장 스캔이 장부를 부풀리지 못하게 상한."""
    new_request_ledger()
    for _ in range(3):
        _note_corp("00126380")
    assert _ctx_ledger.get()["corp_codes"] == ["00126380"]

    new_request_ledger()
    for i in range(_LEDGER_MAX_CORPS + 50):
        _note_corp(f"{i:08d}")
    assert len(_ctx_ledger.get()["corp_codes"]) == _LEDGER_MAX_CORPS


def test_empty_corp_code_is_ignored():
    new_request_ledger()
    _note_corp(None)
    _note_corp("")
    assert _ctx_ledger.get()["corp_codes"] == []


def test_doc_counts_separate_memory_from_disk():
    """메모리 예산의 효과는 doc_mem_hits 로만 봐야 한다 — 디스크는 예산 밖이다."""
    new_request_ledger()
    for _ in range(3):
        _note_doc("doc_mem_hits")
    for _ in range(2):
        _note_doc("doc_disk_hits")
    _note_doc("doc_misses")
    led = _ctx_ledger.get()
    assert (led["doc_mem_hits"], led["doc_disk_hits"], led["doc_misses"]) == (3, 2, 1)


def test_record_writes_every_ledger_field_to_sqlite(tmp_path, monkeypatch):
    """기록기까지 실제로 값이 도달하는지 — sqlite 백엔드로 끝까지 태운다.

    운영자 키는 SELF_HASHES 로 스킵되므로 **다른 키**를 써야 한다(그걸 모르면
    「기록이 안 된다」고 오진하기 쉽다).
    기록은 fly 머신에서만 열리므로 `OPM_USAGE_LOCAL=1` 로 명시해 연다 — 이 테스트가
    보려는 것이 바로 그 기록 경로다."""
    import importlib

    db = tmp_path / "usage.db"
    monkeypatch.setenv("OPM_USAGE_DB_PATH", str(db))
    monkeypatch.setenv("OPM_USAGE_LOCAL", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    usage = importlib.reload(importlib.import_module("open_proxy_mcp.usage"))
    assert usage._RECORDING, "테스트가 기록 게이트를 못 열었다 — 아래 단언이 무의미해진다"

    usage.record("some-other-users-key", 200, "dividend", 1234,
                 is_error=False, error_kind=None, response_bytes=3001,
                 doc_mem_hits=3, doc_disk_hits=2, doc_misses=1,
                 corp_codes=["00126380", "00164779"])

    for _ in range(100):                      # 워커가 비동기라 잠깐 기다린다
        if db.exists():
            con = sqlite3.connect(db)
            try:
                row = con.execute(
                    "SELECT tool, response_bytes, doc_mem_hits, doc_disk_hits, doc_misses, "
                    "corp_codes FROM events").fetchone()
            except sqlite3.OperationalError:
                row = None
            con.close()
            if row:
                break
        import time as _t
        _t.sleep(0.05)

    assert row is not None, "이벤트가 기록되지 않았다"
    assert row == ("dividend", 3001, 3, 2, 1, "00126380,00164779")


def test_record_still_skips_operator_key(tmp_path, monkeypatch):
    """본인 키는 여전히 기록하지 않는다 — 기업 컬럼이 생겼다고 이게 풀리면 안 된다.

    기록 게이트도 함께 열어야 한다. 안 열면 SELF_HASHES 가 죽어도 「안 적혔다」가
    참이 되어 **이 테스트가 아무것도 판별하지 못한다.**"""
    import importlib

    db = tmp_path / "usage2.db"
    monkeypatch.setenv("OPM_USAGE_DB_PATH", str(db))
    monkeypatch.setenv("OPM_USAGE_LOCAL", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    usage = importlib.reload(importlib.import_module("open_proxy_mcp.usage"))
    assert usage._RECORDING, "기록 게이트가 닫힌 채라 SELF_HASHES 를 판별할 수 없다"

    self_key = next(iter(usage.SELF_HASHES))
    assert len(self_key) == 64                # 평문이 아니라 해시로만 들고 있다
    import hashlib
    # 해시가 SELF_HASHES 에 든 키를 만들 수는 없으므로, 스킵 분기 자체를 직접 확인한다.
    monkeypatch.setattr(usage, "SELF_HASHES",
                        {hashlib.sha256("operator".encode()).hexdigest()})
    usage.record("operator", 200, "dividend", 1, corp_codes=["00126380"])
    import time as _t
    _t.sleep(0.3)
    assert not db.exists() or not sqlite3.connect(db).execute(
        "SELECT count(*) FROM events").fetchone()[0]
