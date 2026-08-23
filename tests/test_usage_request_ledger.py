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
        "fetch_viewer": 0, "fetch_kind": 0, "web_wait_ms": 0,
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

    # **값을 전부 서로 다르게** 준다. 같은 값이 섞여 있으면 컬럼이 어긋나도 통과한다 —
    # 위치 의존 INSERT 가 조용히 다른 컬럼에 넣는 사고(260704)를 잡으려면 이게 조건이다.
    usage.record("some-other-users-key", 200, "dividend", 1234,
                 is_error=False, error_kind=None, response_bytes=3001,
                 doc_mem_hits=3, doc_disk_hits=2, doc_misses=1,
                 corp_codes=["00126380", "00164779"],
                 fetch_viewer=7, fetch_kind=11, web_wait_ms=4200)

    for _ in range(100):                      # 워커가 비동기라 잠깐 기다린다
        if db.exists():
            con = sqlite3.connect(db)
            try:
                row = con.execute(
                    "SELECT tool, latency_ms, response_bytes, doc_mem_hits, doc_disk_hits, "
                    "doc_misses, fetch_viewer, fetch_kind, web_wait_ms "
                    "FROM events").fetchone()
            except sqlite3.OperationalError:
                row = None
            con.close()
            if row:
                break
        import time as _t
        _t.sleep(0.05)

    assert row is not None, "이벤트가 기록되지 않았다"
    assert row == ("dividend", 1234, 3001, 3, 2, 1, 7, 11, 4200)

    # **둘 다 간다** (260817). 이벤트 행에 기업이 적히고(세션·재방문 분석용),
    # 사용자를 뗀 (날짜, 기업) 집계도 함께 올라간다(드레인 뒤에도 남는 장기 계열).
    con = sqlite3.connect(db)
    val = con.execute("SELECT corp_codes FROM events").fetchone()[0]
    assert val == "00126380,00164779", f"이벤트 행에 기업이 안 실렸다: {val!r}"
    agg = dict(((c, n) for _, c, n in con.execute(
        "SELECT day, corp_code, requests FROM ops_corp_daily").fetchall()))
    con.close()
    # 이벤트를 되살렸다고 집계를 끄면 안 된다 — 드레인이 이벤트를 가져가면 순위가 사라진다.
    assert agg == {"00126380": 1, "00164779": 1}, agg


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


# ── 폴백 경로 계기 (260810) ────────────────────────────────────────────────
def test_web_throttle_counts_the_fallback_and_the_time_it_cost(monkeypatch):
    """**「2초 간격이 비싼가」를 재려고 붙인 계기다.**

    빈도만으론 답이 안 나온다 — 폴백이 드물면 간격은 아무 비용도 아니고, 잦으면 간격이
    아니라 주 경로(document.xml)가 자주 실패한다는 뜻이다. 그래서 「몇 번 갔나」와
    「그래서 얼마나 기다렸나」를 함께 센다.

    계기는 스로틀 **안**에 둔다 — viewer/KIND 요청은 전부 그 함수를 지나므로 호출측이
    빠뜨릴 수 없다. 호출측에 두면 새 fetch 함수가 늘 때마다 조용히 누락된다.
    """
    import open_proxy_mcp.dart.client as C
    from open_proxy_mcp.dart.client import DartClient

    monkeypatch.setenv("OPENDART_API_KEY", "0" * 40)
    monkeypatch.setattr(C, "_WEB_INTERVAL_RANGE", (0.05, 0.05))   # 테스트를 빠르게·결정적으로
    led = new_request_ledger()
    c = DartClient()
    c._last_web_request = 0.0                      # 직전 요청이 아주 오래됨 → 대기 없음

    asyncio.run(c._throttle_web())
    assert led["fetch_viewer"] == 1, "viewer 폴백이 안 세어졌다"
    assert led["web_wait_ms"] == 0, "대기가 없었는데 시간이 잡혔다"

    asyncio.run(c._throttle_web())                 # 바로 이어서 → 간격만큼 잔다
    assert led["fetch_viewer"] == 2
    assert led["web_wait_ms"] > 0, "간격이 시간으로 안 잡혔다"


def test_web_and_kind_share_one_rule_and_one_clock(monkeypatch):
    """260810 통일 — 종전엔 DART 웹 2.0 고정 / KIND 1~3 랜덤이었는데, 둘은 이미
    `_last_web_request` 라는 **같은 시계**를 쓰고 있었다. 두 정책이 아니라 한 흐름의
    간격만 호출 경로에 따라 달랐던 것이라 하나로 합쳤다.

    지켜야 할 셋(숫자가 아니라 이쪽이 규칙이다) — 하한 1.0초 · 시계 공유 · 배치/병렬 금지.
    """
    import open_proxy_mcp.dart.client as C
    from open_proxy_mcp.dart.client import DartClient

    lo, hi = C._WEB_INTERVAL_RANGE
    assert lo >= 1.0, f"하한이 1.0초 아래로 내려갔다: {lo}"
    assert lo < hi, "고정값이면 요청 간격이 정확히 규칙적이라 기계 티가 난다 — 지터를 둔다"
    assert not hasattr(C, "_MIN_INTERVAL_WEB"), "옛 고정 상수가 남아 규칙이 둘로 보인다"

    monkeypatch.setenv("OPENDART_API_KEY", "0" * 40)
    monkeypatch.setattr(C, "_WEB_INTERVAL_RANGE", (0.08, 0.08))
    new_request_ledger()
    c = DartClient()
    c._last_web_request = 0.0

    asyncio.run(c._throttle_kind())          # 시계가 오래됐으니 대기 없이 통과 + 시계를 민다
    led = new_request_ledger()               # 장부를 새로 — 이 다음 대기만 잰다
    asyncio.run(c._throttle_web())           # KIND 가 민 시계 때문에 **잠들어야** 한다

    # `_last_web_request` 가 커졌는지만 보면 판별이 안 된다 — DART 쪽이 자기 값을 쓰기만 해도
    # 커지기 때문이다(실제로 그렇게 썼다가 「KIND 가 시계를 안 민다」 변이를 놓쳤다).
    # **다음 요청이 실제로 기다렸는가**를 봐야 시계가 이어져 있다는 증거가 된다.
    assert led["web_wait_ms"] > 0, (
        "KIND 직후의 DART 웹 요청이 안 기다렸다 — 시계가 갈라졌다. "
        "갈라지면 호스트마다 따로 세므로 우리 총 요청률이 2배가 된다")


def test_kind_throttle_is_counted_separately(monkeypatch):
    """KIND 는 DART 웹과 **규칙이 다르다**(1~3초 랜덤 vs 고정 2초). 한 칸에 섞어 세면
    어느 규칙이 무엇을 물리는지 알 수 없다."""
    import open_proxy_mcp.dart.client as C
    from open_proxy_mcp.dart.client import DartClient

    monkeypatch.setenv("OPENDART_API_KEY", "0" * 40)
    led = new_request_ledger()
    c = DartClient()
    c._last_web_request = 0.0

    asyncio.run(c._throttle_kind())
    assert led["fetch_kind"] == 1
    assert led["fetch_viewer"] == 0, "KIND 요청이 viewer 로 세어졌다"


def test_instrumentation_is_silent_without_a_ledger(monkeypatch):
    """스크립트·테스트처럼 미들웨어를 안 거친 경로에서 절대 터지면 안 된다 —
    계기가 사용자 요청을 깨뜨리면 본말전도다."""
    from open_proxy_mcp.dart.client import DartClient, _ctx_ledger

    monkeypatch.setenv("OPENDART_API_KEY", "0" * 40)
    _ctx_ledger.set(None)
    c = DartClient()
    c._last_web_request = 0.0
    asyncio.run(c._throttle_web())      # 예외가 안 나면 통과
    asyncio.run(c._throttle_kind())


# ── 기업 조회를 사용자와 떼어 두는가 (260810) ─────────────────────────────
def test_corp_counts_never_carries_the_user():
    """**여기가 연결을 끊는 자리다.** 이 함수가 key_hash 를 들고 나오기 시작하면
    「이 사용자가 언제 어느 기업을 조사했는지」가 되살아난다 — 조사 이력이다.

    회사 이름·티커 자체는 공개 정보라 문제가 아니다. 문제는 **연결**이다:
    key_hash 는 익명이 아니라 가명이고(같은 사람인지는 안다), DART 키는 실명 등록에 묶인다.
    """
    import open_proxy_mcp.usage as usage

    batch = [
        ("e1", 1_754_000_000_000_000_000, "HASH_SECRET", 200, "dividend", 1, False, None,
         0, 0, 0, 0, "00126380,00164779", 0, 0, 0, None),
        ("e2", 1_754_000_000_000_000_000, "HASH_SECRET2", 200, "dividend", 1, False, None,
         0, 0, 0, 0, "00126380", 0, 0, 0, None),
    ]
    agg = usage._corp_counts(batch)
    assert all(len(k) == 2 and isinstance(k[1], str) for k in agg), agg
    assert "HASH_SECRET" not in str(agg), "집계가 사용자를 들고 나왔다"
    assert sorted((c, n) for (_, c), n in agg.items()) == [("00126380", 2), ("00164779", 1)]


def test_event_insert_carries_corp_codes_and_keeps_the_aggregate():
    """260817: 이벤트 행에 기업을 **적는다**. 다만 집계(`ops_corp_daily`)를 끄지 않는다.

    이 테스트가 지키는 건 「적는다」가 아니라 **둘 다 간다**는 쪽이다. 이벤트만 남기면
    드레인이 완결 주를 가져갈 때 기업 조회 순위가 통째로 사라진다 — 드레인은 부채의
    수명 상한이라 멈출 수 없고, 그러면 장기 계열이 없어진다."""
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "open_proxy_mcp" / "usage.py"
    text = src.read_text(encoding="utf-8")
    for stmt in ("INSERT OR IGNORE INTO events(", "INSERT INTO ops_tool_calls("):
        i = text.index(stmt)
        head = text[i:i + 600]
        cols = head[:head.index("VALUES")]
        assert "corp_codes" in cols, f"이벤트 INSERT 가 기업을 안 싣는다: {stmt}"
        # 컬럼 수 = 플레이스홀더 수. 하나라도 어긋나면 **조용히 밀려 다른 컬럼에 들어간다**
        # (260704 mkt_fund_hist 사고와 같은 실패 모드).
        ph = head[head.index("VALUES"):]
        ph = ph[:ph.index(")") + 1]
        assert cols.count(",") + 1 == ph.count("%s") + ph.count("?"), \
            f"컬럼 수와 값 자리 수가 다르다: {stmt}"
    assert "ops_corp_daily" in text, "집계 테이블로 올리는 경로가 없다"


def test_dead_column_doc_cache_hit_stays_dead():
    """260817: `doc_cache_hit` 은 스키마에서 **지운다**.

    260804 에 「폐기하되 컬럼은 남긴다」로 뒀던 것이 문제였다. 남은 컬럼은 값이 0건인데도
    스키마·백업 CSV·드레인 산출물에 계속 실려 다니고, **읽는 사람에게는 지표처럼 보인다.**
    백업 358,205행 전수 확인에서 값이 들어온 적이 한 번도 없었다.
    안 쓰는 컬럼을 남기는 비용은 용량이 아니라 **오해**다.

    되살리려면 그건 결정이어야 한다 — 그때는 이 테스트를 지우면서 이유를 남겨야 한다.
    """
    from pathlib import Path

    src = Path(__file__).resolve().parent.parent / "open_proxy_mcp" / "usage.py"
    text = src.read_text(encoding="utf-8")
    code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))

    assert "ADD COLUMN IF NOT EXISTS doc_cache_hit" not in code, \
        "Postgres 스키마가 죽은 컬럼을 다시 만든다"
    assert "DROP COLUMN IF EXISTS doc_cache_hit" in code, \
        "이미 컬럼이 있는 운영 DB 에서 지우는 경로가 사라졌다"
    assert '"doc_cache_hit INTEGER"' not in code, \
        "sqlite 마이그레이션 목록에 죽은 컬럼이 남아 있다"

    # 세 대체 지표는 살아 있어야 한다 — 이걸 안 보면 「지웠더니 아무것도 안 남았다」가 된다.
    for alive in ("doc_mem_hits", "doc_disk_hits", "doc_misses"):
        assert alive in code, f"대체 지표가 없다: {alive}"
