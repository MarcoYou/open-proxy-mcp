"""260907 director_board 프로파일 뒤: 소집공고 파싱은 스레드에서, scope 가 안 쓰는 표는 건너뛰고, soup 캐시는 요청 컨텍스트에."""
import asyncio
import threading

from open_proxy_mcp.services import shareholder_meeting as sm
from open_proxy_mcp.services import shareholder_meeting_parser as parser_mod

_HTML = "<html><body><table><tr><td>제1호 의안</td></tr></table></body></html>"


def test_scope_skips_parsers_it_does_not_use(monkeypatch):
    calls = []
    monkeypatch.setattr(sm, "parse_personnel_xml", lambda html: calls.append("personnel") or {"appointments": [], "summary": {}})
    monkeypatch.setattr(sm, "parse_compensation_xml", lambda html: calls.append("compensation") or {"items": [], "summary": {}})
    sm._parse_notice_bundle("t", _HTML, rcept_no="1", scope="compensation")
    assert calls == ["compensation"]
    calls.clear(); sm._parse_notice_bundle("t", _HTML, rcept_no="1", scope="agenda")
    assert calls == []
    calls.clear(); sm._parse_notice_bundle("t", _HTML, rcept_no="1")          # scope 없으면 종전처럼 전부
    assert sorted(calls) == ["compensation", "personnel"]


def test_soup_cache_lives_in_request_context_only():
    cache: dict = {}
    with sm._cached_notice_parser_soup(cache, "1"):
        a = parser_mod.BeautifulSoup(_HTML, "lxml"); b = parser_mod.BeautifulSoup(_HTML, "lxml")
    assert a is b and len(cache) == 1
    c = parser_mod.BeautifulSoup(_HTML, "lxml")                                 # 컨텍스트 밖 — 캐시 안 씀
    assert c is not a and len(cache) == 1


def test_bundle_parse_runs_off_the_event_loop(monkeypatch):
    seen = {}

    def fake_bundle(text, html, *, rcept_no, soup_cache=None, scope=None, meeting_info=None):
        seen["thread"] = threading.current_thread(); seen["scope"] = scope
        return {"text": text, "html": html, "meeting_info": {"meeting_type": "annual", "datetime": "2026-03-20 09:00"},
                "agenda": [], "agenda_valid": True, "board": {"appointments": []}, "compensation": {"items": []}, "correction": None}

    class _Client:
        async def get_document_cached(self, rcept_no):
            return {"text": "t", "html": _HTML}

    monkeypatch.setattr(sm, "_parse_notice_bundle", fake_bundle)
    monkeypatch.setattr(sm, "get_dart_client", lambda: _Client())
    asyncio.run(sm._load_notice_bundle_with_fallback("1", scope="compensation"))
    assert seen["thread"] is not threading.main_thread() and seen["scope"] == "compensation"


def test_bundle_reuses_meeting_info_parsed_during_candidate_selection(monkeypatch):
    calls = []
    monkeypatch.setattr(sm, "parse_meeting_info_xml", lambda text, html=None: calls.append("info") or {"meeting_type": "annual", "datetime": "2026-03-20"})
    sm._INFO_CTX.set({})
    info, src = asyncio.run(sm._notice_info_with_fallback("1", "t", _HTML))
    assert src == "dart_xml" and calls == ["info"] and sm._INFO_CTX.get()["1"] is info
    sm._parse_notice_bundle("t", _HTML, rcept_no="1", scope="agenda", meeting_info=sm._INFO_CTX.get().get("1"))
    assert calls == ["info"]                       # 번들이 다시 파싱하지 않았다
    sm._parse_notice_bundle("t", _HTML, rcept_no="2", scope="agenda")
    assert calls == ["info", "info"]               # 모르는 공고는 파싱한다
