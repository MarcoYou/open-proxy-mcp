"""screener 페이징 — 260902. details 를 **자르기 전에** 돌리던 순서를 뒤집었다.

종전 순서는 scan → dedup → details(전체 hits) → `[:max_hits]` 였다. 돌려주지도 않을 건의
문서를 열고 그 콜을 버린 셈이라, 유니버스가 300종목을 넘으면 details 를 통째로 끄는
가드로 막고 있었다. 자르고 나서 열면 비용이 **이번 페이지 크기**에 묶이므로 그 가드가
필요 없어진다 — 대신 `offset` 으로 이어받는다.

이 테스트가 잠그는 것 셋:
  ① `offset` 이 겹치지도 빠뜨리지도 않고 이어진다
  ② details 는 **이번 페이지 건만** 연다 (전체 hits 를 열지 않는다)
  ③ 매칭 수(`paging.matched`)와 실은 수(`paging.returned`)가 따로 나온다
"""
from __future__ import annotations

import asyncio

import pytest

from open_proxy_mcp.services import screener as S


class _FakeClient:
    def api_call_snapshot(self):
        return 0


def _filings(n: int) -> list[dict]:
    """자사주 취득결정 n건 — 분류기가 treasury 로 잡는 형태."""
    return [
        {"corp_code": f"{i:08d}", "corp_name": f"회사{i}", "stock_code": f"{i:06d}",
         "report_nm": "주요사항보고서(자기주식취득결정)",
         "rcept_no": f"2026090200{i:04d}", "rcept_dt": "20260902", "corp_cls": "Y",
         "flr_nm": f"회사{i}"}
        for i in range(n)
    ]


@pytest.fixture
def stubbed(monkeypatch):
    """scan 은 합성 목록으로, details 는 「열었다」만 기록하는 대역으로 바꾼다."""
    opened: list[str] = []

    async def _scan(client, code, bgn, end, pages):
        return (_filings(25) if code == "B001" else []), 25, False, None

    async def _detail(h, running):
        opened.append(h["rcept_no"])
        running["calls"] += 1
        return {"detail_status": "parsed", "fields": {"amount_won": 1}}

    monkeypatch.setattr(S, "_scan_code", _scan)
    monkeypatch.setattr(S, "_fetch_detail", _detail)
    monkeypatch.setattr(S, "get_dart_client", lambda: _FakeClient())
    monkeypatch.setattr(S, "_krx_mktcap_map", lambda codes, dd: {})
    return opened


def _run(**kw):
    return asyncio.run(S._build_screener_payload_impl(
        types="treasury", period="20260902", universe="all", **kw))


def test_offset_pages_do_not_overlap_or_skip(stubbed):
    p1 = _run(max_hits=10)
    assert p1["paging"] == {"offset": 0, "page_size": 10, "matched": 25,
                            "returned": 10, "has_more": True, "next_offset": 10}
    p2 = _run(max_hits=10, offset=p1["paging"]["next_offset"])
    p3 = _run(max_hits=10, offset=p2["paging"]["next_offset"])
    assert p3["paging"]["has_more"] is False
    assert p3["paging"]["next_offset"] is None

    seen = [h["rcept_no"] for p in (p1, p2, p3) for h in p["hits"]]
    assert len(seen) == 25, "세 묶음을 합치면 전체가 된다"
    assert len(set(seen)) == 25, "묶음끼리 겹치지 않는다"


def test_details_open_only_this_page(stubbed):
    """②가 이 변경의 요점 — 25건이 걸려도 10건짜리 페이지면 문서는 10번만 연다."""
    p = _run(max_hits=10, details=True)
    assert len(stubbed) == 10
    assert set(stubbed) == {h["rcept_no"] for h in p["hits"]}


def test_matched_and_returned_are_separate(stubbed):
    """③ 「10건」을 전체로 읽으면 안 된다 — U7 에서 실제로 그렇게 읽혔다."""
    p = _run(max_hits=10)
    assert p["counts"]["matched"] == 25
    assert p["counts"]["returned"] == 10


def test_wide_universe_no_longer_disables_details(stubbed):
    """유니버스 크기로 막던 가드를 걷었다 — universe=all 에서도 details 가 돈다."""
    p = _run(max_hits=5, details=True)
    assert p["types"]["details"] is True
    assert len(stubbed) == 5
