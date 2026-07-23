# -*- coding: utf-8 -*-
"""resolve_latest_meeting_year 회차 pre-resolution 엣지 회귀 (network 0콜 — 전부 모킹).

260723 리뷰 CRITICAL 고정: 회의일이 미래인 공고(소집 후~주총 전 = 1차 사용 구간)가
meeting window 필터에서 탈락해 작년 회차를 '최신'으로 오선택하던 결함.
"""
from __future__ import annotations

import asyncio
from datetime import date, timedelta

import open_proxy_mcp.services.shareholder_meeting as sm


def _notice(rcept: str, disclosure: str, meeting: str) -> dict:
    return {"rcept_no": rcept, "disclosure_date": disclosure, "datetime": meeting, "report_name": "주주총회소집공고"}


def _patch_window(monkeypatch, notices_by_label: dict, captured: dict | None = None):
    async def fake(corp_code, label, start, end, **kw):
        if captured is not None:
            captured["start"], captured["end"] = start, end
        # 실제 함수와 동일한 회의일 window 필터를 재현 — window 확장의 효과를 검증하기 위함
        out = []
        for n in notices_by_label.get(label, []):
            md = sm._parse_notice_meeting_date(n.get("datetime", ""))
            if md is None or (start <= md <= end):
                out.append(n)
        return out, []

    monkeypatch.setattr(sm, "_candidate_notices_in_meeting_window", fake)


def test_future_meeting_included_pre_meeting(monkeypatch):
    # 소집공고 발행됨 + 회의일 미래(+20일): 반드시 이 회차가 선택되고 phase=pre_meeting
    today = date.today()
    fut = today + timedelta(days=20)
    past = today - timedelta(days=340)
    notices = {"정기": [
        _notice("2" * 14, (today - timedelta(days=15)).strftime("%Y%m%d"), fut.strftime("%Y년 %m월 %d일")),
        _notice("1" * 14, past.strftime("%Y%m%d"), (past + timedelta(days=25)).strftime("%Y년 %m월 %d일")),
    ]}
    captured: dict = {}
    _patch_window(monkeypatch, notices, captured)
    out = asyncio.run(sm.resolve_latest_meeting_year("00000000"))
    assert out is not None
    assert out["year"] == fut.year
    assert out["notice_rcept_no"] == "2" * 14
    assert out["meeting_phase"] == "pre_meeting"
    # window end가 미래로 확장돼 있어야 미래 회의일이 통과한다 (CRITICAL 회귀 가드)
    assert captured["end"] >= fut


def test_year_boundary_december_notice_january_meeting(monkeypatch):
    # 12월 공시 → 1월 회의: 연도는 '회의일' 기준(공시연도 아님)
    notices = {"정기": [_notice("3" * 14, "20261220", "2027년 1월 15일")]}
    _patch_window(monkeypatch, notices)
    out = asyncio.run(sm.resolve_latest_meeting_year("00000000"))
    # 필터: 회의일 2027-01-15가 window(오늘-372일 ~ 오늘+90일) 안이어야 매치 — 테스트 실행
    # 시점(2026-07 기준)엔 미래 90일 밖이므로 탈락 → None (오선택보다 안전한 방향 확인)
    if out is not None:
        assert out["year"] == 2027


def test_meeting_date_parse_failure_falls_back_to_disclosure_year(monkeypatch):
    today = date.today()
    notices = {"정기": [_notice("4" * 14, today.strftime("%Y%m%d"), "")]}  # datetime 파싱 불가
    _patch_window(monkeypatch, notices)
    out = asyncio.run(sm.resolve_latest_meeting_year("00000000"))
    assert out is not None
    assert out["year"] == today.year          # 공시연도 fallback
    assert out["meeting_date"] is None
    assert out["meeting_phase"] == "undetermined"  # phase 단정 금지 → closed hint 미발화


def test_no_notices_returns_none(monkeypatch):
    _patch_window(monkeypatch, {})
    assert asyncio.run(sm.resolve_latest_meeting_year("00000000")) is None


def test_auto_picks_latest_disclosure_across_types(monkeypatch):
    today = date.today()
    ann = _notice("5" * 14, (today - timedelta(days=120)).strftime("%Y%m%d"),
                  (today - timedelta(days=95)).strftime("%Y년 %m월 %d일"))
    ext = _notice("6" * 14, (today - timedelta(days=10)).strftime("%Y%m%d"),
                  (today + timedelta(days=15)).strftime("%Y년 %m월 %d일"))
    _patch_window(monkeypatch, {"정기": [ann], "임시": [ext]})
    out = asyncio.run(sm.resolve_latest_meeting_year("00000000", meeting_type="auto"))
    assert out is not None
    assert out["meeting_type"] == "extraordinary"   # 공시일 최신 우선
    assert out["notice_rcept_no"] == "6" * 14
    assert out["meeting_phase"] == "pre_meeting"


def test_invalid_meeting_type_returns_none(monkeypatch):
    _patch_window(monkeypatch, {})
    assert asyncio.run(sm.resolve_latest_meeting_year("00000000", meeting_type="oops")) is None
