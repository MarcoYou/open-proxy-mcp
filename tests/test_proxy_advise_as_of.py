# -*- coding: utf-8 -*-
"""사전 의결권 권고의 시점 경계 — look-ahead 차단. network 0콜.

260828 실측 사고: 금호석유화학 2026-03-26 정기주총 **사전** 권고가
  · 기업지배구조보고서공시 2026-06-01 (주총 66일 후) — 미준수 지표 6건의 출처
  · 대량보유 상황보고 2026-04-07 (주총 12일 후) — 지분 근거
를 읽고 나왔다. 그 지배구조보고서 본문에는 「2026년 3월 26일 정기주주총회에서 정관변경을
완료하였으며」가 실려 있었다 — 우리가 표를 던지라고 조언하는 주총의 **결과**가 근거였다.
"""

from __future__ import annotations

import asyncio

import pytest

from open_proxy_mcp.dart import as_of as as_of_mod
from open_proxy_mcp.dart.client import DartClient, DartClientError


@pytest.fixture(autouse=True)
def _gate_off():
    """테스트마다 게이트를 확실히 끈 상태에서 시작한다."""
    tokens = as_of_mod.set_as_of("")
    yield
    as_of_mod.reset_as_of(tokens)


def test_gate_is_off_by_default() -> None:
    """켠 적 없으면 아무 일도 하지 않는다 — 종전 동작 그대로."""
    assert as_of_mod.get_as_of() == ""
    assert as_of_mod.clamp_end_de("20261231") == "20261231"


def test_end_date_is_pulled_back_to_the_as_of() -> None:
    tokens = as_of_mod.set_as_of("20260325")
    try:
        assert as_of_mod.clamp_end_de("20261231") == "20260325"
        assert as_of_mod.clamp_end_de("20260101") == "20260101"   # 이미 앞이면 그대로
        assert as_of_mod.clamps() == [("20261231", "20260325")]
    finally:
        as_of_mod.reset_as_of(tokens)


def test_a_window_entirely_after_the_as_of_is_no_data_not_an_error() -> None:
    """구간 전체가 기준일 뒤 — 「그때는 볼 것이 없었다」이지 조회 실패가 아니다."""
    tokens = as_of_mod.set_as_of("20260325")
    try:
        assert as_of_mod.window_is_empty("20260401", as_of_mod.clamp_end_de("20260630"))
    finally:
        as_of_mod.reset_as_of(tokens)


def test_search_filings_never_reaches_dart_for_a_post_as_of_window(monkeypatch) -> None:
    """list.json 은 모든 공시 목록 조회의 유일한 통로 — 여기서 막히면 어느 upstream 도 못 본다."""
    client = DartClient.__new__(DartClient)
    client._search_cache = {}
    client._MAX_SEARCH_CACHE = 10
    called: list[dict] = []

    async def _fake_request(path, params):
        called.append(params)
        return {"status": "000", "list": []}

    client._request = _fake_request

    tokens = as_of_mod.set_as_of("20260325")
    try:
        # ① 구간 전체가 기준일 뒤 → DART 를 부르지 않고 013(데이터 없음)
        with pytest.raises(DartClientError) as exc:
            asyncio.run(client.search_filings(
                bgn_de="20260401", end_de="20260630", corp_code="00106368"))
        assert exc.value.status == "013"
        assert not called

        # ② 걸쳐 있는 구간 → 종료일만 기준일로 당겨서 호출
        asyncio.run(client.search_filings(
            bgn_de="20260101", end_de="20261231", corp_code="00106368"))
        assert called and called[0]["end_de"] == "20260325"
    finally:
        as_of_mod.reset_as_of(tokens)


def test_the_gate_reaches_every_upstream_through_one_door() -> None:
    """서비스마다 인자를 심는 방식이면 한 곳만 빠뜨려도 조용한 구멍이 난다 —
    실제로 게이트를 거는 자리가 `search_filings` 하나인지 소스로 고정한다."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "open_proxy_mcp/dart/client.py").read_text("utf-8")
    assert src.count("clamp_end_de(end_de)") == 1
    # list.json 을 직접 부르는 자리도 하나여야 게이트가 전부를 덮는다
    assert src.count('_request("list.json"') == 1


def test_apis_without_a_date_window_are_filtered_by_row(monkeypatch) -> None:
    """`majorstock.json` 은 corp_code 하나로 전 기간을 준다 — end_de 잘라내기가 안 닿는다.

    260828 실측: 게이트를 걸고도 대량보유 상황보고 2026-04-07(주총 12일 후)이 지분 근거로
    들어왔다. 행의 `rcept_dt` 로 거른다.
    """
    from open_proxy_mcp.dart.client import _as_of_filter_rows

    # 260828 함정: 서식이 API 마다 갈린다. `majorstock.json` 은 **하이픈 표기**로 주는데
    # 문자열로 그냥 비교하면 하이픈 쪽이 항상 작게 나와 필터가 통째로 사문이 된다
    # (실측: 걸어 놓고도 2026-04-07 이 그대로 들어왔다).
    payload = {"status": "000", "list": [
        {"rcept_dt": "2026-02-18", "repror": "박찬구"},
        {"rcept_dt": "2026-04-07", "repror": "박찬구"},     # 주총 뒤 — 그때는 없던 문서
        {"rcept_dt": "20260218", "repror": "국민연금"},     # 하이픈 없는 표기도 같이 온다
        {"rcept_dt": "20260407", "repror": "국민연금"},
    ]}
    tokens = as_of_mod.set_as_of("20260325")
    try:
        out = _as_of_filter_rows("majorstock.json", payload)
        assert [r["rcept_dt"] for r in out["list"]] == ["2026-02-18", "20260218"]
        assert any("majorstock.json" in str(c[0]) for c in as_of_mod.clamps())
    finally:
        as_of_mod.reset_as_of(tokens)


def test_rows_without_a_receipt_date_are_left_alone() -> None:
    """모르는 것을 자르지 않는다 — `rcept_dt` 가 없는 응답(재무제표 API 등)은 그대로 둔다."""
    from open_proxy_mcp.dart.client import _as_of_filter_rows

    payload = {"status": "000", "list": [{"bsns_year": "2025", "account_nm": "매출액"}]}
    tokens = as_of_mod.set_as_of("20260325")
    try:
        assert _as_of_filter_rows("fnlttSinglAcnt.json", payload)["list"] == payload["list"]
    finally:
        as_of_mod.reset_as_of(tokens)
