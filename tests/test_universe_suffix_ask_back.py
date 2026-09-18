"""유니버스 말 — 순위 뒤 군말과 「못 찾으면 되묻기」 (260918). network·DB 0.

계기: 「코스피 시가총액 상위 200개 기업」이 회사 이름 나열로 읽혔고, 카드 보기는 회사를 못 찾자 시장 전체로
바꿔 보였다(라이브: 상위 200 이면 22건인데 시장 전체 75건이 「지정종목」 이름으로 나갔다). 아침 수주 루틴 프롬프트가
이 꼴(「코스피 시가총액 상위 50개 기업」)이다.

지키는 것
① 숫자 뒤 「개 기업·개 종목·개 회사·개사·곳」은 순위 유니버스로 읽는다.
② 나열에서 회사를 하나도 못 찾으면 조회하지 않고 되묻는다 — 카드 보기·흐름 보기·유니버스 목록(거래 도구·추정치
   스크린) 모두. DART·원장을 부르지 않는다. 「코스피 120」처럼 수인지 이름인지 모를 꼴은 전용 질문.
③ 일부만 찾으면 찾은 만큼으로 진행한다(종전 그대로).
"""
from __future__ import annotations

import asyncio
import datetime as dt

import pytest

from open_proxy_mcp.server import mcp
from open_proxy_mcp.services import disclosure_flow as df
from open_proxy_mcp.services import screener as S


@pytest.mark.parametrize("raw,spec", [
    ("코스피 시가총액 상위 200개 기업", "kospi:200"), ("코스피 시총 상위 200개 종목", "kospi:200"),
    ("코스피 상위 200개 회사", "kospi:200"), ("코스피 시총 상위 200개사", "kospi:200"),
    ("코스닥 시총 상위 50곳", "kosdaq:50"), ("시총 상위 200개 기업", "top_mktcap:200"),
    ("코스피 시총 상위 50개 기업", "kospi:50"), ("코스피 시총 상위 200", "kospi:200"),
    ("코스피 시총 상위 200 종목", "kospi:200"), ("코스피200", "kospi200"),
    # 순위 말이 없으면 이름 나열로 넘긴다 — 되묻기는 해석기가 한다
    ("코스피 120", "custom:코스피 120"), ("코스피 대형주 200", "custom:코스피 대형주 200"),
    ("삼성전자, SK하이닉스", "custom:삼성전자, SK하이닉스"),
])
def test_rank_suffixes(raw, spec):
    assert S._nl_universe(raw) == spec


class _Res:
    def __init__(self, code=None):
        self.selected = {"stock_code": code} if code else None
        self.status = None


def _stub_companies(monkeypatch, known: dict[str, str]):
    async def _q(tok, *a, **k):
        return _Res(known.get(tok))
    monkeypatch.setattr(S, "resolve_company_query", _q)
    monkeypatch.setattr(S, "_krx_latest_dd", lambda: "20260918")


def test_custom_list_with_nothing_found_asks_back(monkeypatch):
    _stub_companies(monkeypatch, {})
    uf = asyncio.run(S.resolve_universe("custom:코스피 대형주 200"))
    assert uf.allowed is None and not uf.resolved
    assert "하나도 찾지 못해 조회하지 않았다" in uf.question and "시장 전체로 바꿔 보이지 않는다" in uf.question
    uf = asyncio.run(S.resolve_universe("custom:코스피 120"))
    assert "종목 수인지 이름인지 알 수 없어" in uf.question and "코스피 시총 상위 120" in uf.question


def test_partial_list_still_proceeds(monkeypatch):
    _stub_companies(monkeypatch, {"삼성전자": "005930"})
    uf = asyncio.run(S.resolve_universe("custom:삼성전자, 없는회사"))
    assert uf.resolved and uf.allowed == {"005930"} and not uf.question and "미해결" in uf.notice


def _call(tool, args):
    async def go():
        r = await mcp.call_tool(tool, args)
        return "".join(getattr(c, "text", "") for c in (r if isinstance(r, list) else r.content))
    return asyncio.run(go())


def test_card_view_asks_back_without_calling_dart(monkeypatch):
    _stub_companies(monkeypatch, {})
    scanned: list = []

    class _Client:
        def api_call_snapshot(self):
            return 0

    async def _scan(client, code, bgn, end, pages):
        scanned.append(code)
        return S._scan_result([], 0, 1, 1)

    monkeypatch.setattr(S, "_scan_code", _scan)
    monkeypatch.setattr(S, "get_dart_client", lambda: _Client())
    out = _call("screener", {"types": "수주", "universe": "코스피 대형주 200", "period": "오늘"})
    assert out.startswith("# 📬 공시 디제스트 — 조회하지 않음") and "❓" in out
    assert "하나도 찾지 못해 조회하지 않았다" in out and not scanned
    # 군말이 붙은 순위는 되묻지 않는다
    monkeypatch.setattr(S, "_krx_top_mktcap", lambda n, dd, market: {"005930", "000660"})
    out = _call("screener", {"types": "수주", "universe": "코스피 시가총액 상위 200개 기업", "period": "오늘"})
    assert "조회하지 않음" not in out and "KOSPI 시총상위 200" in out and scanned


def test_flow_view_asks_back_without_reading_events(monkeypatch):
    _stub_companies(monkeypatch, {})
    monkeypatch.setattr(df, "_ledger_ready", lambda: True)
    monkeypatch.setattr(df, "_ledger_bounds", lambda: (dt.date(2025, 9, 16), dt.date(2026, 9, 17)))
    fetched: list = []
    monkeypatch.setattr(df, "_fetch_events", lambda *a, **k: fetched.append(a) or [])
    p = asyncio.run(df.build_flow_payload(universe="코스피 대형주 200"))
    assert p["status"] == "needs_input" and "하나도 찾지 못해" in p["warnings"][0] and not fetched
    out = _call("screener", {"view": "흐름", "universe": "코스피 대형주 200"})
    assert "조회하지 않음, 되물음" in out and "하나도 찾지 못해" in out


def test_universe_list_passes_the_question_to_trading(monkeypatch):
    _stub_companies(monkeypatch, {})
    out = _call("trading_data", {"scope": "universe", "universe": "코스피 대형주 200"})
    assert "하나도 찾지 못해 조회하지 않았다" in out
