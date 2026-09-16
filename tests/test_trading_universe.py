"""trading_data(scope=universe) — 「코스피 시총 상위 N」 순위표 (DB·DART 0콜).

지키는 것: ① 말 → 스펙 → 순위표가 한 번에 나온다(종목마다 부르지 않는다) ② 순위는 시총 내림차순,
시장 라벨은 KOSPI/KOSDAQ ③ 이름은 회사 원장에서, 없으면 「-」 ④ 저장분 DB 미설정/장애와
「해석 실패」를 status 로 가른다 ⑤ md 표는 300종목에서 자르고 전체는 json 에 남긴다.
"""
import asyncio
import types

import pytest

from open_proxy_mcp.services import screener, universe as uni
from open_proxy_mcp.services.trading import build_universe_payload
from open_proxy_mcp.tools import trading as tool

_CAPS = [  # (ticker, market, mktcap, close, shrs)
    ("005930", "KS", 500_000_000_000_000, 80_000, 5_969_782_550),
    ("005935", "KS", 150_000_000_000_000, 70_000, 822_886_700),      # 삼성전자우 — 원장에 없음
    ("000660", "KS", 300_000_000_000_000, 400_000, 728_002_365),
    ("005380", "KS", 60_000_000_000_000, 280_000, 209_416_191),
    ("196170", "KQ", 20_000_000_000_000, 400_000, 53_000_000),
    ("086520", "KQ", 15_000_000_000_000, 100_000, 133_000_000),
]
_NAMES = {"005930": "삼성전자", "000660": "SK하이닉스", "005380": "현대자동차", "196170": "알테오젠"}


def _stub(monkeypatch, caps=_CAPS, names=_NAMES, dd="20260911"):
    monkeypatch.setattr(screener, "_krx_latest_dd", lambda: dd)

    def top(n, price_dd, market=None):
        rows = sorted((c for c in caps if market is None or c[1] == market), key=lambda c: -c[2])
        return {c[0] for c in rows[:n]}
    monkeypatch.setattr(screener, "_krx_top_mktcap", top)
    monkeypatch.setattr(screener, "_krx_market_codes", lambda market, dd: {c[0] for c in caps if c[1] == market})

    def rows(price_dd, codes):
        return [c for c in sorted(caps, key=lambda c: -c[2]) if codes is None or c[0] in codes]
    monkeypatch.setattr(uni, "_krx_rows", rows)

    async def _names():
        return dict(names)
    monkeypatch.setattr(uni, "_names_by_ticker", _names)


def test_kospi_top_n_is_one_ranked_table_without_preferred(monkeypatch):
    _stub(monkeypatch)
    p = asyncio.run(build_universe_payload("코스피 시총 상위 2"))
    assert p["status"] == "ok"
    d = p["data"]
    assert d["scope"] == "universe" and d["spec"] == "kospi:2" and d["as_of"] == "20260911"
    assert d["label"] == "KOSPI 시총상위 2"                                  # 여유분(22)이 아니라 요청한 2
    assert [r["ticker"] for r in d["rows"]] == ["005930", "000660"]          # 삼성전자우(2위 시총)는 빠지고 채워진다
    assert any("우선주 1종목" in w for w in p["warnings"])
    assert [r["rank"] for r in d["rows"]] == [1, 2]
    assert d["rows"][0]["name"] == "삼성전자" and d["rows"][0]["market"] == "KOSPI"
    assert d["rows"][0]["mktcap_krw"] == 500_000_000_000_000


def test_kosdaq_ranking_stays_inside_its_market_and_unknown_name_is_dash(monkeypatch):
    _stub(monkeypatch)
    p = asyncio.run(build_universe_payload("코스닥 상위 5"))
    rows = p["data"]["rows"]
    assert [r["ticker"] for r in rows] == ["196170", "086520"]      # 코스피 종목이 섞이지 않는다
    assert rows[0]["rank"] == 1 and rows[1]["name"] == "-" and rows[1]["market"] == "KOSDAQ"


def test_market_whole_and_kospi200_notice(monkeypatch):
    _stub(monkeypatch)
    whole = asyncio.run(build_universe_payload("코스피 전체"))
    assert [r["ticker"] for r in whole["data"]["rows"]] == ["005930", "000660", "005380"]   # 우선주 제외
    k200 = asyncio.run(build_universe_payload("코스피200"))
    assert k200["status"] == "ok"
    assert any("KOSPI200" in w and "대체" in w for w in k200["warnings"])


def test_empty_universe_is_invalid(monkeypatch):
    _stub(monkeypatch)
    p = asyncio.run(build_universe_payload(""))
    assert p["status"] == "invalid"


def test_db_unset_and_db_down_are_told_apart(monkeypatch):
    monkeypatch.setattr(screener, "_krx_latest_dd", lambda: None)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    p = asyncio.run(build_universe_payload("코스피 시총 상위 10"))
    assert p["status"] == "db_unconfigured"
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    p = asyncio.run(build_universe_payload("코스피 시총 상위 10"))
    assert p["status"] == "db_error"


def test_unresolved_universe_is_no_data_with_notice(monkeypatch):
    _stub(monkeypatch)
    monkeypatch.setattr(screener, "_krx_top_mktcap", lambda *a, **k: set())
    p = asyncio.run(build_universe_payload("코스피 시총 상위 10"))
    assert p["status"] == "no_data" and "전체시장" in p["warnings"][0]


def test_named_list_resolves_through_company_resolver(monkeypatch):
    _stub(monkeypatch)

    async def resolve(tok):
        code = {"삼성전자": "005930", "알테오젠": "196170"}.get(tok)
        return types.SimpleNamespace(status="ok", selected={"stock_code": code} if code else None, candidates=[])
    monkeypatch.setattr(screener, "resolve_company_query", resolve)
    p = asyncio.run(build_universe_payload("삼성전자, 알테오젠, 005935"))
    assert [r["ticker"] for r in p["data"]["rows"]] == ["005930", "005935", "196170"]   # 시총순 · 직접 고른 우선주는 유지
    assert p["data"]["label"] == "지정 3종목"


def test_md_render_ranks_and_truncates(monkeypatch):
    big = [(f"{i:06d}", "KS", 10**12 * (400 - i), 1000, 1) for i in range(1, 331)]
    _stub(monkeypatch, caps=big, names={})
    p = asyncio.run(build_universe_payload("코스피 시총 상위 330"))
    md = tool._render_universe(p)
    assert md.startswith("# KOSPI 시총상위 330 — 시가총액 순위")
    assert "| 1 | - | `000001` | KOSPI |" in md
    assert "| 300 |" in md and "| 301 |" not in md
    assert "전체 330종목" in md
    assert len(p["data"]["rows"]) == 330                                # json 에는 전량


@pytest.mark.parametrize("phrase,guess", [("코스피 120", "코스피 시총 상위 120"), ("코스닥 50개", "코스닥 시총 상위 50"),
                                          ("120", "시총 상위 120")])
def test_market_plus_number_asks_instead_of_guessing(monkeypatch, phrase, guess):
    """「상위」가 없으면 종목 수인지 이름인지 모른다 — 추측하지 않고 되묻는다."""
    _stub(monkeypatch)
    p = asyncio.run(build_universe_payload(phrase))
    assert p["status"] == "invalid"
    assert "추측하지 않았습니다" in p["warnings"][0] and f'universe="{guess}"' in p["warnings"][0]
    assert uni.clarification("코스피 시총 상위 120") is None and uni.clarification("삼성전자, 120") is None
    assert uni.clarification("코스피200") is None and uni.clarification("코스피 200") is None


def test_is_preferred_rule():
    names = {"005930": "삼성전자", "006800": "미래에셋증권", "196170": "알테오젠"}
    assert uni.is_preferred("005935", names) and uni.is_preferred("00680K", names)
    assert not uni.is_preferred("005930", names) and not uni.is_preferred("196170", names)
    assert not uni.is_preferred("123457", names)                        # 보통주도 원장에 없으면 판정 불가 → 유지


@pytest.mark.parametrize("phrase,spec", [("코스닥 상위 50", "kosdaq:50"), ("시총 상위 100", "top_mktcap:100"),
                                         ("kospi:30", "kospi:30"), ("전체", "all")])
def test_natural_language_specs(monkeypatch, phrase, spec):
    _stub(monkeypatch)
    p = asyncio.run(build_universe_payload(phrase))
    assert p["data"]["spec"] == spec


def test_universe_phrase_in_company_slot_routes_to_ranking(monkeypatch):
    """scope=firm 에 「코스피 시총 상위 2」 같은 문장이 오면 순위표로 답한다 (260916)."""
    import asyncio as _a
    from open_proxy_mcp.services.trading import build_firm_series_payload
    _stub(monkeypatch)
    p = _a.run(build_firm_series_payload("코스피 시총 상위 2 종목 순위표를 달라"))
    assert p["status"] == "ok" and p["data"]["scope"] == "universe"
    assert [r["ticker"] for r in p["data"]["rows"]] == ["005930", "000660"]
    assert "universe 인자" in p["warnings"][0]
