# -*- coding: utf-8 -*-
"""asset_holdings detail — 주석 원문 조회가 모두 실패했을 때 「주석에 기재 없음」으로 단정하지 않는다. network 0.

결함(DATA-U3, 조사 N5): `_safe_getdoc` 이 get_document 와 viewer 폴백이 모두 실패하면 예외를 삼키고 빈 주석을 돌려줘,
호출부는 「조회 실패」와 「빈 주석」을 가를 수 없었다. 그러면 `_absence_verdict` 는 앵커를 못 찾았다며
detail 4항목을 `not_disclosed`(「재무제표 주석에 … 기재가 없습니다」)로 단정했다 — 못 읽은 것이 「없다」로 나갔다.
고친 뒤: 조회 실패는 `fetch_failed=True` 로 표시하고 `absence_kind="not_collected"`(읽지 못함)로 낸다.
합성 고정본이다(실제 회사 원문이 아니라 두 조회 함수를 실패시킨 것).
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from open_proxy_mcp.dart.client import DartClientError
from open_proxy_mcp.services import asset_holdings as A

_KEYS = ("real_estate", "equity_holdings", "pledged_assets", "contingent")


def _patch_fetch(monkeypatch, *, getdoc, viewer):
    monkeypatch.setattr(A._bd, "_fetch_getdoc", getdoc)
    monkeypatch.setattr(A._bd, "_fetch_viewer_sec", viewer)


async def _boom(client, rcept_no):
    raise DartClientError("800", "시스템 점검")


async def _viewer_ok(client, rcept_no):
    return {"note_html": "<p>주석 본문 1,234,567</p>", "full_text": "주석 본문 1,234,567"}


async def _getdoc_empty(client, rcept_no):
    return {"note_html": "", "full_text": ""}


def _build(monkeypatch, scope, getdoc, viewer):
    """`_safe_getdoc` 은 진짜를 쓰고, 그 밑의 두 조회 함수만 바꾼다."""
    _patch_fetch(monkeypatch, getdoc=getdoc, viewer=viewer)

    async def resolve(q):
        return SimpleNamespace(status=A.AnalysisStatus.EXACT,
                               selected={"corp_code": "00000001", "stock_code": "000001", "corp_name": q}, candidates=[])

    async def pick(client, cc, report, year):
        return {"rcept_no": "20260312000856", "report_nm": "사업보고서 (2025.12)"}, "11011", ""

    async def market_cap(isu):
        return None, {"reason": "test"}

    async def fake_mark(client, holdings, doc_text, price_dd=None, qty_dd=None):
        return {"marked": [], "unresolved": [], "price_date": None, "price_source": None, "qty_date": None,
                "qty_adjust_checked": True, "listed_book_krw": 0, "listed_mkt_krw": 0, "unrealized_gap_krw": 0,
                "n_listed": 0, "n_marked": 0, "n_unresolved": 0, "unlisted_book_krw": 0,
                "unlisted_majority_book_krw": 0, "n_unlisted": 0}

    class Cli:
        async def get_fnltt_singl_acnt_all(self, *a):
            return {"list": [{"sj_div": "BS", "account_nm": "현금및현금성자산", "thstrm_amount": "100"}]}

        async def get_company_info(self, cc):
            return {"induty_code": "467"}

        async def get_other_corp_investment(self, *a):
            return {"list": []}

    monkeypatch.setattr(A, "get_dart_client", lambda: Cli())
    monkeypatch.setattr(A, "resolve_company_query", resolve)
    monkeypatch.setattr(A, "_pick_periodic_report", pick)
    monkeypatch.setattr(A, "_market_cap", market_cap)
    monkeypatch.setattr(A, "_mark_listed_stakes", fake_mark)
    return asyncio.run(A._build_asset_holdings_payload_impl("X", scope=scope))


# ── _safe_getdoc: 실패를 표시한다 ──

def test_safe_getdoc_marks_failure_when_getdoc_and_viewer_both_fail(monkeypatch):
    _patch_fetch(monkeypatch, getdoc=_boom, viewer=_boom)
    sec = asyncio.run(A._safe_getdoc(object(), "20260312000856"))
    assert sec["note_html"] == ""
    assert sec.get("fetch_failed") is True


def test_safe_getdoc_marks_failure_when_getdoc_is_empty_and_viewer_fails(monkeypatch):
    # 원문 조회는 됐지만 주석이 비었고, 확인하려는 viewer 도 실패 — 빈 주석이 맞는지 확인하지 못했다
    _patch_fetch(monkeypatch, getdoc=_getdoc_empty, viewer=_boom)
    sec = asyncio.run(A._safe_getdoc(object(), "20260312000856"))
    assert sec.get("fetch_failed") is True


def test_safe_getdoc_viewer_fallback_success_is_not_a_failure(monkeypatch):
    _patch_fetch(monkeypatch, getdoc=_boom, viewer=_viewer_ok)
    sec = asyncio.run(A._safe_getdoc(object(), "20260312000856"))
    assert sec["note_html"].strip() and not sec.get("fetch_failed")


def test_safe_getdoc_still_raises_on_daily_limit(monkeypatch):
    async def lim(client, rcept_no):
        raise DartClientError("020", "한도")
    _patch_fetch(monkeypatch, getdoc=lim, viewer=_viewer_ok)
    with pytest.raises(DartClientError):
        asyncio.run(A._safe_getdoc(object(), "20260312000856"))


# ── detail 응답: 못 읽은 것을 「기재 없음」이라 하지 않는다 ──

def test_detail_marks_all_four_as_not_collected_when_both_fetches_fail(monkeypatch):
    out = _build(monkeypatch, "detail", _boom, _boom)
    d = out["data"]
    for k in _KEYS:
        assert d[k]["status"] == "NOT_APPLICABLE", k
        assert d[k]["absence_kind"] == "not_collected", k
        assert d[k]["fetch_failed"] is True, k
        assert "기재가 없습니다" not in d[k]["absence_note"], k
    assert any("조회" in w and "주석" in w for w in out["warnings"])


def test_detail_real_empty_note_read_normally_stays_not_disclosed(monkeypatch):
    # 주석을 정상으로 읽었고 그 안에 4항목 어휘가 없다 — 지금처럼 「기재 없음」(not_disclosed)
    out = _build(monkeypatch, "detail", _boom, _viewer_ok)
    d = out["data"]
    for k in _KEYS:
        assert d[k]["absence_kind"] == "not_disclosed", k
        assert not d[k].get("fetch_failed"), k
    assert not any("조회" in w and "주석" in w for w in out["warnings"])


def test_summary_warns_that_haircuts_are_unknown_when_notes_could_not_be_read(monkeypatch):
    out = _build(monkeypatch, "summary", _boom, _boom)
    assert any("담보" in w and "조회" in w for w in out["warnings"])
    ok = _build(monkeypatch, "summary", _boom, _viewer_ok)
    assert not any("담보" in w and "조회" in w for w in ok["warnings"])


# ── 렌더러 ──

def test_renderer_says_unread_not_absent():
    from open_proxy_mcp.tools.asset_holdings import _render
    node = {"status": "NOT_APPLICABLE", "fetch_failed": True, "absence_kind": "not_collected",
            "absence_note": "주석 원문 조회에 실패해 읽지 못했습니다."}
    p = {"status": "ok", "subject": "X", "data": {"scope": "detail", "real_estate": node, "equity_holdings": node,
                                                   "pledged_assets": node, "contingent": node}}
    text = _render(p)
    assert "조회 실패" in text and "해당 없음" not in text
