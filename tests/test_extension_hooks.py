# -*- coding: utf-8 -*-
"""확장 훅 — 설치된 확장이 없으면 no-op, 있으면 도구 결과에 「원문 위치」 줄이 붙는다. network 0.

공개 레포에는 훅만 있다. 확장이 무엇을 하든 공개 서버의 동작 계약은 같다: 확장이 있으면 줄이 하나 더,
없으면 그 줄이 없다. 어느 쪽이든 도구 본문은 같다.
"""
from __future__ import annotations

import pytest

from open_proxy_mcp import extensions as ext

RNO = "20260312001399"


@pytest.fixture
def no_ext(monkeypatch):
    monkeypatch.setattr(ext, "_hint_providers", [])
    yield


@pytest.fixture
def fake_ext(monkeypatch):
    def provider(rcept_no, title=None, no=None):
        return f"원문 절: ext://{rcept_no}/{no or 'toc'}" + (f" 「{title}」" if title else "")
    monkeypatch.setattr(ext, "_hint_providers", [provider])
    yield


def test_hint_is_empty_without_an_extension(no_ext):
    assert ext.origin_hint(RNO, "x", "1") == ""
    assert ext.origin_hint("", "x") == ""


def test_hint_delegates_to_the_installed_provider(fake_ext):
    assert ext.origin_hint(RNO, "사용제한", "33") == f"원문 절: ext://{RNO}/33 「사용제한」"


def test_a_broken_extension_does_not_kill_the_server(monkeypatch):
    class _EP:
        name = "broken"
        def load(self):
            raise RuntimeError("boom")
    monkeypatch.setattr(ext, "entry_points", lambda group: [_EP()] if group == ext._GROUP_REGISTER else [])
    assert ext.load_extensions(object()) == []


def _fn_payload():
    return {"company": "삼성화재", "report": {"report_nm": "사업보고서 (2025.12)", "rcept_no": RNO},
            "fields": ["사용제한"], "sections": [{"basis": "연결", "no": "33", "title": "11. 사용제한 예금 (연결)"}],
            "notes": {"사용제한": {"status": "NOT_FOUND", "note": "표를 못 찾음"}}}


def test_financial_notes_render_without_extension_names_sections_but_prints_no_address(no_ext):
    from open_proxy_mcp.tools.financial_notes import _render
    out = _render(_fn_payload())
    assert "- 읽은 절: 「11. 사용제한 예금 (연결)」" in out
    assert "://" not in out.split("## 사용제한")[1]


def test_financial_notes_render_with_extension_prints_addresses(fake_ext):
    from open_proxy_mcp.tools.financial_notes import _render
    out = _render(_fn_payload())
    assert f"- 읽은 절: 원문 절: ext://{RNO}/33 「11. 사용제한 예금 (연결)」" in out
    assert f"> 원문 절: ext://{RNO}/toc 「사용제한」" in out


def test_asset_holdings_render_hooks_only_the_unfound_branches(no_ext, fake_ext, monkeypatch):
    from open_proxy_mcp.tools.asset_holdings import _render
    def payload(kind, excerpt=None):
        fd = {"status": "NOT_APPLICABLE", "absence_kind": kind, "absence_note": "주석 절을 못 찾음"}
        if excerpt:
            fd["absence_excerpt"] = excerpt
        return {"status": "ok", "subject": "삼성화재", "data": {"report_nm": "사업보고서 (2025.12)", "fs_div": "CFS",
                "rcept_no": RNO, "scope": "detail", "real_estate": fd}}
    assert f"ext://{RNO}/toc" in _render(payload("extraction_failed"))
    assert "ext://" not in _render(payload("not_disclosed"))
    assert f"ext://{RNO}/toc" in _render(payload("not_disclosed", excerpt="…담보로 제공…"))
    monkeypatch.setattr(ext, "_hint_providers", [])
    assert "ext://" not in _render(payload("extraction_failed"))


def test_meeting_notice_weak_parse_warning_keeps_the_full_text_address(no_ext, monkeypatch):
    import asyncio
    from open_proxy_mcp.services import shareholder_meeting as sm
    parsed = {"meeting_info": {}, "agenda": [], "agenda_valid": False, "html": "", "board": {}}
    monkeypatch.setattr(sm, "_parse_notice_bundle", lambda *a, **k: parsed)
    monkeypatch.setattr(sm, "_needs_notice_viewer_fallback", lambda p, *, scope: ["agenda_parse_low_confidence"])
    class _Client:
        async def get_document_cached(self, rcept_no):
            return {"text": "", "html": ""}
        async def get_viewer_document(self, rcept_no, section_keywords=None):
            raise RuntimeError("viewer down")
    monkeypatch.setattr(sm, "get_dart_client", lambda: _Client())
    _, warnings, _ = asyncio.run(sm._load_notice_bundle_with_fallback(RNO, scope="full", soup_cache={}))
    assert f"원문: opm://filing/{RNO}" in " ".join(warnings)
