"""screener 유니버스 해석·시총 부착의 DB 읽기는 **이벤트 루프 밖(스레드)** 에서 돈다 — 목표 경로 감사 A1.

계기: `resolve_universe` 와 `_build_screener_payload_impl` 이 `pg_rows`(동기 DB 읽기)를 async 함수 안에서
직접 불렀다. 프로덕션은 단일 프로세스라 그 질의가 도는 60~100ms 동안 **이벤트 루프 전체**가 멈춘다
(다른 요청의 응답·타임아웃 계산이 같이 선다). 값은 바뀌지 않아야 한다 — 이 시험은 위치만 잠근다.

이 시험이 잠그는 것 둘:
  ① 유니버스 해석(all 이 아닌 지수·시장·시총 상위 N)의 모든 DB 읽기가 루프 밖에서 불린다
  ② 공시 스캔 뒤 시총 배치 부착(`_krx_mktcap_map`)도 루프 밖에서 불린다

방법: `screener.pg_rows` 를 가로채 「이 읽기가 실행 중인 이벤트 루프가 있는 스레드에서 불렸나」를 기록한다.
`asyncio.to_thread` 의 워커 스레드에는 실행 중 루프가 없으므로 그 경우만 통과한다.
DART·네트워크 0콜 — 스캔과 상세는 합성 목록으로 바꾼다.
"""
from __future__ import annotations

import asyncio

import pytest

from open_proxy_mcp import index_members, krx_daily
from open_proxy_mcp.services import screener as S


def _on_event_loop() -> bool:
    """지금 이 스레드에서 실행 중인 이벤트 루프가 있나 — 있으면 루프를 막는 호출이다."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


@pytest.fixture
def reads(monkeypatch):
    """`screener.pg_rows` 대역 — 호출마다 (질의, 루프 위에서 불렸나)를 남기고 모양 맞는 값을 돌려준다."""
    seen: list[tuple[str, bool]] = []

    def _fake_pg_rows(sql, params=()):
        seen.append((sql, _on_event_loop()))
        if sql == krx_daily.LATEST_DAY_SQL:                 # 기준일
            return [("20260821",)]
        if sql == index_members.LATEST_SQL:                  # 지수 구성종목 원장 — 비어 있으면 시총 순위 대체 경로
            return []
        return [("005930", 10), ("000660", 5)]              # 단면 질의: (단축코드, 시총)

    monkeypatch.setattr(S, "pg_rows", _fake_pg_rows)
    monkeypatch.setenv("DATABASE_URL", "postgresql://x/y")   # `_krx_mktcap_map` 은 설정이 없으면 읽지 않는다
    return seen


@pytest.mark.parametrize("spec", ["kospi200", "market:kosdaq", "kospi:5", "top_mktcap:3"])
def test_universe_reads_never_run_on_the_event_loop(reads, spec):
    uf = asyncio.run(S.resolve_universe(spec))
    assert uf.price_dd == "20260821"
    assert reads, f"{spec}: DB 읽기가 하나도 안 불렸다 — 시험이 빈 말이 된다"
    on_loop = [sql for sql, loop in reads if loop]
    assert not on_loop, f"{spec}: 이벤트 루프 위에서 DB 를 읽었다: {on_loop}"


def test_payload_mktcap_attach_runs_off_the_event_loop(reads, monkeypatch):
    """공시 스캔 뒤 시총 배치 부착까지 루프 밖이다 — 응답 본문은 종전과 같다(시총 칸이 붙는다)."""

    class _FakeClient:
        def api_call_snapshot(self):
            return 0

    def _filings(n: int) -> list[dict]:
        return [
            {"corp_code": f"{i:08d}", "corp_name": f"회사{i}", "stock_code": f"{i:06d}",
             "report_nm": "주요사항보고서(자기주식취득결정)", "rcept_no": f"2026090200{i:04d}",
             "rcept_dt": "20260902", "corp_cls": "Y", "flr_nm": f"회사{i}"}
            for i in range(n)
        ]

    async def _scan(client, code, bgn, end, pages):
        items = _filings(3) if code == "B001" else []
        return S._scan_result(items, len(items), 1, 1)

    monkeypatch.setattr(S, "_scan_code", _scan)
    monkeypatch.setattr(S, "get_dart_client", lambda: _FakeClient())

    p = asyncio.run(S._build_screener_payload_impl(
        types="treasury", period="20260902", universe="all", details=False))
    assert p["counts"]["matched"] == 3

    cap_reads = [loop for sql, loop in reads if "ticker = ANY" in sql]
    assert cap_reads, "시총 배치 부착 질의가 안 불렸다 — 시험이 빈 말이 된다"
    assert not any(cap_reads), "시총 배치 부착을 이벤트 루프 위에서 읽었다"
    on_loop = [sql for sql, loop in reads if loop]
    assert not on_loop, f"응답 경로에서 루프 위 DB 읽기: {on_loop}"
