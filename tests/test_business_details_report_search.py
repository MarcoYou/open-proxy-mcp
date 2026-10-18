"""business_details 의 연도 없는 보고서 후보 검색 — DART 한 번, 같은 보고서는 한 번만 (261018). network 0.

DART list.json 은 `pblntf_ty` 를 함께 주면 `pblntf_detail_ty` 를 무시하고 대분류 전체를 준다(261018 실측: 삼성전자
pblntf_ty=A + A001 과 + A003 이 같은 11건, detail 만 주면 사업보고서 3건). 종전 코드는 상세유형마다 따로 물어(latest 3번)
같은 목록을 세 번 받았고, 같은 보고서가 후보에 세 번 들었다 — 최신 원문이 014 면 같은 접수번호를 다시 받으러 갔다.
아래 가짜 DART 는 실제처럼 detail 을 무시하고 늘 같은 목록을 준다.
"""
from __future__ import annotations

import asyncio

from open_proxy_mcp.services.business_details import _find_report_candidates

_PERIODIC = [   # DART 가 pblntf_ty=A 에 주는 그대로 — 최신순, 세 종류가 섞여 있다
    {"report_nm": "반기보고서 (2026.06)", "rcept_no": "20260814000001", "rcept_dt": "20260814"},
    {"report_nm": "분기보고서 (2026.03)", "rcept_no": "20260515000002", "rcept_dt": "20260515"},
    {"report_nm": "[기재정정]사업보고서 (2025.12)", "rcept_no": "20260320000004", "rcept_dt": "20260320"},
    {"report_nm": "사업보고서 (2025.12)", "rcept_no": "20260310000003", "rcept_dt": "20260310"},
    {"report_nm": "분기보고서 (2025.09)", "rcept_no": "20251114000005", "rcept_dt": "20251114"},
]


class _Dart:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def search_filings(self, **kw):
        self.calls.append(kw)
        return {"status": "000", "list": [dict(r) for r in _PERIODIC]}   # detail 은 무시된다(실제 DART 처럼)


def _run(period: str) -> tuple[list[str], _Dart]:
    dart = _Dart()
    got = asyncio.run(_find_report_candidates(dart, "00126380", period))
    return [r["rcept_no"] for r in got], dart


def test_latest_asks_dart_once_and_lists_each_report_once():
    got, dart = _run("latest")
    assert len(dart.calls) == 1, f"같은 목록을 {len(dart.calls)}번 받았다"
    assert len(got) == len(set(got)) == 5, got
    assert got == [r["rcept_no"] for r in _PERIODIC]          # 최신순 그대로


def test_quarterly_asks_once_and_has_no_duplicates():
    got, dart = _run("quarterly")
    assert len(dart.calls) == 1
    assert got == ["20260814000001", "20260515000002", "20251114000005"]


def test_single_kind_periods_still_filter_by_report_name():
    assert _run("annual")[0] == ["20260320000004", "20260310000003"]
    assert _run("half")[0] == ["20260814000001"]
    assert _run("quarter")[0] == ["20260515000002", "20251114000005"]


def test_the_search_still_covers_the_whole_periodic_family_since_2024():
    """받는 범위는 종전과 같다 — 정기공시(A) 전체, 2024-01-01 부터, 한 쪽 30건."""
    _, dart = _run("latest")
    kw = dart.calls[0]
    assert kw["pblntf_ty"] == "A" and kw["bgn_de"] == "20240101" and kw["page_count"] == 30
    assert not kw.get("pblntf_detail_ty"), "pblntf_ty 와 함께 준 상세유형은 DART 가 무시한다 — 보내지 않는다"
