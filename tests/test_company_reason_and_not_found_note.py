"""회사 식별 근거 문구 · 「못 찾았다」 안내의 명부 기준 시각 · 시세·배당 도구의 정본 안내 (261018). network 0.

① company 의 종목코드·corp_code 조회가 근거 문구(`company_resolution.reason`)를 「공식명 또는 등록 별칭 일치」로 냈다 —
   `matched_on` 은 ticker 인데 문장은 이름 일치였다.
② 「못 찾았다」 안내에 회사 명부 기준 시각을 **늘** 싣는다 — 그 뒤 상장·사명 변경은 명부에 아직 없을 수 있어 사용자가 신규상장 여부를 가늠한다.
③ `trading_data`(firm·quote)·`dividend_data`(firm)는 같은 입력을 다른 도구와 달리 이름만 되풀이한 한 줄로 답했다 —
   사명 변경 행선지·종목코드 탈출구·명부 기준 시각이 없었다(공용 해석기 `_resolve_listed` 를 거쳐 안내 일관성 시험에 안 걸렸다).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from open_proxy_mcp.company_resolver import CompanyResolver, MarketContext
from open_proxy_mcp.dart import client as dart_client
from open_proxy_mcp.dart.client import corp_registry_as_of
from open_proxy_mcp.services import company as C
from open_proxy_mcp.services import trading as T

CORPS = [
    {"corp_code": "00126380", "corp_name": "삼성전자", "corp_eng_name": "SAMSUNG ELECTRONICS CO,.LTD",
     "stock_code": "005930", "modify_date": "20251201"},
]
MARKET = MarketContext({"005930": 1_000}, frozenset({"005930"}), "20261016", "krx_daily")


class _FakeClient:
    def __init__(self):
        self.r = CompanyResolver(CORPS, {}, MARKET)

    def api_call_snapshot(self):
        return 0

    async def lookup_corp_code_all(self, q):
        return self.r.search(q)

    async def periodic_filers(self):
        return frozenset({"00126380"})

    async def suggest_corp_candidates(self, q, limit=5):
        return self.r.suggest(q, limit)

    async def get_company_info(self, corp_code):
        return {"corp_name": "삼성전자", "corp_name_eng": "SAMSUNG", "corp_cls": "Y", "jurir_no": "1",
                "induty_code": "264", "acc_mt": "12"}

    async def search_filings(self, **_kw):
        return {"list": []}


@pytest.fixture
def registry_at(monkeypatch):
    """메모리 명부를 받은 시각을 정해 둔다 — 시간대 없는 로컬 시각(`_corp_registry_state.fetched_at` 과 같은 기준)."""
    def _set(when: datetime | None):
        monkeypatch.setitem(dart_client._corp_registry_state, "fetched_at", when)
    return _set


def _local_naive(utc: datetime) -> datetime:
    """UTC 시각 → 이 머신 로컬의 시간대 없는 시각(운영 머신은 로컬=UTC, 개발 맥은 KST — 어느 쪽이든 같은 순간)."""
    return utc.astimezone().replace(tzinfo=None)


# ── ① 근거 문구 ──────────────────────────────────────────────────────────────
@pytest.mark.parametrize("query, kind, ko, en", [
    ("005930", "ticker", "종목코드 일치", "Matched the ticker (stock code)"),
    ("00126380", "corp_code", "DART 고유번호(corp_code) 일치", "Matched the DART corp_code"),
    ("삼성전자", "official", "공식명 또는 등록 별칭 일치", "Matched an official name or registered alias"),
])
def test_reason_names_the_kind_of_match(monkeypatch, query, kind, ko, en):
    monkeypatch.setattr(C, "get_dart_client", _FakeClient)
    res = asyncio.run(C.build_company_payload(query))["data"]["company_resolution"]
    assert res["matched_on"] == kind
    assert res["reason"] == ko and res["reason_i18n"] == {"ko": ko, "en": en}


# ── ② 명부 기준 시각 ─────────────────────────────────────────────────────────
def test_registry_time_is_shown_in_kst(registry_at):
    registry_at(_local_naive(datetime(2026, 10, 18, 13, 4, tzinfo=timezone.utc)))
    assert corp_registry_as_of() == "2026-10-18 22:04 KST"
    registry_at(_local_naive(datetime(2026, 10, 18, 15, 30, tzinfo=timezone.utc)))   # UTC 로는 18일, KST 로는 19일
    assert corp_registry_as_of() == "2026-10-19 00:30 KST"
    registry_at(None)
    assert corp_registry_as_of() is None


def test_not_found_always_carries_the_registry_time(registry_at):
    registry_at(_local_naive(datetime(2026, 10, 18, 13, 4, tzinfo=timezone.utc)))     # 방금 받은 명부여도 싣는다
    msg = C.company_not_found_warning("없는회사", listed_only=True)
    assert msg.startswith("'없는회사'에 해당하는 상장사를 찾지 못했다.")
    assert msg.endswith(" 회사 명부 기준: 2026-10-18 22:04 KST(DART 회사 목록을 받은 시각) — "
                        "그 뒤 상장·사명 변경은 아직 명부에 없을 수 있다.")
    registry_at(None)                                                                   # 시각을 모르면 붙이지 않는다
    assert "회사 명부 기준" not in C.company_not_found_warning("없는회사")


def test_company_tool_english_not_found_carries_it_too(monkeypatch, registry_at):
    registry_at(_local_naive(datetime(2026, 10, 18, 13, 4, tzinfo=timezone.utc)))
    monkeypatch.setattr(C, "get_dart_client", _FakeClient)
    p = asyncio.run(C.build_company_payload("Nonexistent Holdings", language="en"))
    assert p["status"] == "error"
    assert "Company registry as of 2026-10-18 22:04 KST" in p["warnings"][0]


# ── ③ 시세·배당 도구의 정본 안내 ─────────────────────────────────────────────
@pytest.fixture
def nobody(monkeypatch, registry_at):
    registry_at(_local_naive(datetime(2026, 10, 18, 13, 4, tzinfo=timezone.utc)))

    async def _none(_q):
        return None, None
    monkeypatch.setattr(T, "_resolve_listed", _none)
    from open_proxy_mcp.services import price_multiple_data as P
    monkeypatch.setattr(P, "_resolve_listed", _none)


def test_trading_firm_and_quote_use_the_canonical_not_found(nobody):
    canonical = C.company_not_found_warning("179880", listed_only=True)
    for p in (asyncio.run(T.build_firm_series_payload("179880", format="json")),
              asyncio.run(T.build_quote_payload("179880"))):
        assert p["status"] == "not_found" and p["warnings"] == [canonical]
        assert p["next_actions"] == [C.COMPANY_LOOKUP_NEXT_ACTION]
        assert "회사 명부 기준: 2026-10-18 22:04 KST" in p["warnings"][0]


def test_dividend_data_firm_uses_the_canonical_not_found(nobody, monkeypatch):
    from open_proxy_mcp.services import dividend_data as dd
    from open_proxy_mcp.tools import dividend_data as dd_tool

    monkeypatch.setattr(dd, "year_range", lambda: (2020, 2025))

    class _Reg:
        tools: dict = {}

        def tool(self, *a, **k):
            def deco(fn):
                self.tools[fn.__name__] = fn
                return fn
            return deco

    reg = _Reg()
    dd_tool.register_tools(reg)
    fn = reg.tools["dividend_data"]
    canonical = C.company_not_found_warning("없는회사", listed_only=True)
    md = asyncio.run(fn(company="없는회사", scope="firm"))
    assert md.startswith("## '없는회사' 을(를) 찾지 못했다 (`status=not_found`)")
    assert f"> {canonical}" in md and C.COMPANY_LOOKUP_NEXT_ACTION in md
    js = json.loads(asyncio.run(fn(company="없는회사", scope="firm", format="json")))
    assert js["status"] == "not_found" and js["warnings"] == [canonical]
