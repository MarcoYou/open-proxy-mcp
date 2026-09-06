# -*- coding: utf-8 -*-
"""도구 결과가 **원문 절 주소를 글자로** 적는다 (260906, handoff 3단계). network 0.

Claude.ai 커넥터는 resource 목록은 못 보지만 URI 를 알면 읽는다. 그래서 파싱이 약하거나
「찾지 못함」일 때 도구가 `opm://filing/{rcept_no}/toc` 또는 `/section/{no}` 를 적어 줘야
AI 가 목차를 거치지 않고 절로 간다. 주소를 만들려고 뷰어를 새로 부르지는 않는다.
"""
from __future__ import annotations

from open_proxy_mcp.services.filing_sections import origin_hint

RNO = "20260312001399"


def test_origin_hint_prefers_section_address_when_number_is_known():
    assert origin_hint(RNO, "11. 사용제한 예금 (연결)", "33") == \
        f"원문 절: opm://filing/{RNO}/section/33 「11. 사용제한 예금 (연결)」"


def test_origin_hint_falls_back_to_toc_with_the_title_to_look_for():
    h = origin_hint(RNO, "사용제한")
    assert h.startswith(f"원문 절 단위: opm://filing/{RNO}/toc")
    assert "「사용제한」" in h and f"opm://filing/{RNO}/section/{{no}}" in h
    assert origin_hint("", "x") == ""


def test_financial_notes_render_lists_read_sections_and_points_failed_fields_to_toc():
    from open_proxy_mcp.tools.financial_notes import _render
    from open_proxy_mcp.services import financial_notes as svc

    payload = {
        "company": "삼성화재", "report": {"report_nm": "사업보고서 (2025.12)", "rcept_no": RNO},
        "fields": ["사용제한", "담보제공"],
        "sections": [{"basis": "연결", "no": "33", "title": "11. 사용제한 예금 (연결)"}],
        "notes": {"사용제한": {"status": "NOT_FOUND", "note": "표를 못 찾음"},
                  "담보제공": {"status": "NOT_FOUND", "note": "표를 못 찾음"}},
    }
    out = _render(payload)
    assert f"- 읽은 절: 「11. 사용제한 예금 (연결)」 opm://filing/{RNO}/section/33" in out
    assert f"> 원문 절 단위: opm://filing/{RNO}/toc — 목차에서 「사용제한」 절을 골라" in out
    assert out.count(f"opm://filing/{RNO}/toc") == 2          # 실패 필드마다 한 줄


def test_asset_holdings_render_points_unfound_fields_to_toc_only_when_a_table_may_exist():
    from open_proxy_mcp.tools.asset_holdings import _render

    def payload(kind):
        return {"status": "ok", "subject": "삼성화재", "data": {
            "report_nm": "사업보고서 (2025.12)", "fs_div": "CFS", "rcept_no": RNO, "scope": "detail",
            "real_estate": {"status": "NOT_APPLICABLE", "absence_kind": kind, "absence_note": "주석 절을 못 찾음"}}}
    found_missing = _render(payload("extraction_failed"))
    assert f"opm://filing/{RNO}/toc" in found_missing and "「토지" in found_missing
    not_disclosed = _render(payload("not_disclosed"))
    assert "opm://filing" not in not_disclosed             # 원문이 「해당 없음」이라 밝힌 것엔 안 붙인다
    p = payload("not_disclosed"); p["data"]["real_estate"]["absence_excerpt"] = "…담보로 제공하였습니다…"
    assert f"opm://filing/{RNO}/toc" in _render(p)         # 발췌를 주며 「인용 위치 확인」이면 붙인다


def test_meeting_notice_weak_parse_warning_carries_the_addresses(monkeypatch):
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
    joined = " ".join(warnings)
    assert f"opm://filing/{RNO}" in joined and f"opm://filing/{RNO}/toc" in joined
