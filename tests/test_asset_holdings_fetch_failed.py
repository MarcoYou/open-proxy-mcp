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


# ── 요약: 상장·비상장 구분도 못 하면 지분 NAV 를 틀린 채로 내지 않는다 (감수 ASSET-S1) ──
# `_mark_listed_stakes` 는 가짜로 바꾸지 않는다 — 빈 원문이 실제 그 함수 경로를 지날 때의 결과를 본다.

_HOLD = [{"inv_prm": "삼성전자", "trmend_blce_acntbk_amount": "1000", "trmend_blce_qy": "10",
          "trmend_blce_qota_rt": "1.0", "recent_bsns_year_fnnr_sttus_tot_assets": "0"}]


def _build_real_mark(monkeypatch, getdoc, viewer, *, mcap=10_000):
    _patch_fetch(monkeypatch, getdoc=getdoc, viewer=viewer)

    async def resolve(q):
        return SimpleNamespace(status=A.AnalysisStatus.EXACT,
                               selected={"corp_code": "00000001", "stock_code": "000001", "corp_name": q}, candidates=[])

    async def pick(client, cc, report, year):
        return {"rcept_no": "20260312000856", "report_nm": "사업보고서 (2025.12)"}, "11011", ""

    async def market_cap(isu):
        return mcap, {"date": "20261016"}

    class Cli:
        async def get_fnltt_singl_acnt_all(self, *a):
            return {"list": [{"sj_div": "BS", "account_nm": "현금및현금성자산", "thstrm_amount": "100"}]}

        async def get_company_info(self, cc):
            return {"induty_code": "467"}

        async def get_other_corp_investment(self, *a):
            return {"list": _HOLD}

    monkeypatch.setattr(A, "get_dart_client", lambda: Cli())
    monkeypatch.setattr(A, "resolve_company_query", resolve)
    monkeypatch.setattr(A, "_pick_periodic_report", pick)
    monkeypatch.setattr(A, "_market_cap", market_cap)
    return asyncio.run(A._build_asset_holdings_payload_impl("X", scope="summary"))


def test_summary_blanks_nav_and_listed_totals_when_notes_unreadable(monkeypatch):
    out = _build_real_mark(monkeypatch, _boom, _boom)
    d = out["data"]
    nav, ls = d["nav"], d["listed_stakes"]
    assert nav["equity_nav_cov"] is None and nav["equity_nav_krw"] is None
    assert nav["listed_unrealized_gap_krw"] is None and nav["haircut_flags"] is None
    assert nav["equity_nav_unavailable"]
    for k in ("n_listed", "n_marked", "n_unlisted", "listed_mkt_krw", "unlisted_book_krw", "unrealized_gap_krw"):
        assert k in ls and ls[k] is None, k            # 키는 남기고 값만 비운다
    assert d["notes_fetch_failed"] is True
    assert any("상장" in w and "비상장" in w and "다시 조회" in w for w in out["warnings"])


def test_summary_same_holdings_still_computes_nav_when_notes_read_normally(monkeypatch):
    out = _build_real_mark(monkeypatch, _boom, _viewer_ok)
    nav, ls = out["data"]["nav"], out["data"]["listed_stakes"]
    assert ls["n_unlisted"] == 1 and ls["unlisted_book_krw"] == 1000       # 종전 계산 그대로
    assert nav["equity_nav_krw"] == 1000 and round(nav["equity_nav_cov"], 2) == 0.10
    assert nav["haircut_flags"] == [] and "equity_nav_unavailable" not in nav
    assert "notes_fetch_failed" not in out["data"]


def test_summary_renderer_says_nav_unavailable_not_missing(monkeypatch):
    from open_proxy_mcp.tools.asset_holdings import _render
    text = _render(_build_real_mark(monkeypatch, _boom, _boom))
    assert "지분 NAV" in text and "산출 못 함" in text and "시가마크: 산출 못 함" in text


# ── 사용자에게 찍히는 문장에 엔진 내부 이름을 내지 않는다 (감수 ASSET-S2) ──

_INTERNAL = ("get_document", "viewer", "haircut_flags", "_mark_listed_stakes", "fetch_failed")


def test_user_facing_sentences_have_no_internal_names(monkeypatch):
    s_out = _build_real_mark(monkeypatch, _boom, _boom)
    d_out = _build(monkeypatch, "detail", _boom, _boom)
    texts = list(s_out["warnings"]) + list(d_out["warnings"])
    texts += [d_out["data"][k]["absence_note"] + d_out["data"][k]["na_reason"] for k in _KEYS]
    for t in texts:
        for bad in _INTERNAL:
            assert bad not in t, (bad, t)


# ── 못 읽은 갈래에도 원문으로 가는 길을 준다 (감수 ASSET-N1) ──

def test_renderer_not_collected_gives_filing_section_path():
    from open_proxy_mcp.tools.asset_holdings import _render
    node = {"status": "NOT_APPLICABLE", "fetch_failed": True, "absence_kind": "not_collected",
            "absence_note": "읽지 못했습니다."}
    p = {"status": "ok", "subject": "삼성전자", "data": {"scope": "detail", "company": "삼성전자", "rcept_no": "20260312000856",
                                                          "real_estate": node, "equity_holdings": node,
                                                          "pledged_assets": node, "contingent": node}}
    text = _render(p)
    assert text.count("filing_section(") == 4 and 'company="삼성전자"' in text
