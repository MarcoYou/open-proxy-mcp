from __future__ import annotations

import asyncio
import sqlite3

from open_proxy_mcp import company_resolver as resolver_module
from open_proxy_mcp.company_resolver import CompanyResolver, MarketContext
from open_proxy_mcp.dart import client as dart_client
from open_proxy_mcp.dart.client import _validate_corp_master
from open_proxy_mcp.services.company import _resolve_match
from open_proxy_mcp.services.contracts import AnalysisStatus
from open_proxy_mcp.tools.company import _render_exact


CORPS = [
    {"corp_code": "00126380", "corp_name": "삼성전자", "corp_eng_name": "SAMSUNG ELECTRONICS CO,.LTD", "stock_code": "005930", "modify_date": "20250101"},
    {"corp_code": "00126256", "corp_name": "삼성화재해상보험", "corp_eng_name": "SAMSUNG FIRE & MARINE INSURANCE CO.,LTD", "stock_code": "000810", "modify_date": "20250101"},
    {"corp_code": "00149655", "corp_name": "케이티앤지", "corp_eng_name": "KT&G Corporation", "stock_code": "033780", "modify_date": "20250101"},
    {"corp_code": "01309401", "corp_name": "HD현대일렉트릭", "corp_eng_name": "HD HYUNDAI ELECTRIC CO.,LTD", "stock_code": "267260", "modify_date": "20250101"},
    {"corp_code": "00181712", "corp_name": "SK", "corp_eng_name": "SK Inc.", "stock_code": "034730", "modify_date": "20250101"},
    {"corp_code": "00164779", "corp_name": "SK하이닉스", "corp_eng_name": "SK hynix Inc.", "stock_code": "000660", "modify_date": "20250101"},
    {"corp_code": "00164742", "corp_name": "현대자동차", "corp_eng_name": "HYUNDAI MOTOR CO", "stock_code": "005380", "modify_date": "20250101"},
    {"corp_code": "00111111", "corp_name": "옛삼성전자", "corp_eng_name": "OLD SAMSUNG ELECTRONICS", "stock_code": "099999", "modify_date": "20100101"},
]

ALIASES = {
    "삼성화재": "삼성화재해상보험",
    "kt&g": "케이티앤지",
    "ktng": "케이티앤지",
    "현대차": "현대자동차",
}

MARKET = MarketContext(
    market_caps={
        "005930": 1_000,
        "000810": 100,
        "033780": 90,
        "267260": 80,
        "034730": 200,
        "000660": 800,
        "005380": 700,
    },
    active_tickers=frozenset({"005930", "000810", "033780", "267260", "034730", "000660", "005380"}),
    as_of_date="20260721",
)


def _resolver() -> CompanyResolver:
    return CompanyResolver(CORPS, ALIASES, MARKET)


def test_official_korean_and_english_variants_resolve_exactly():
    resolver = _resolver()
    expected = {
        "삼성전자": "005930",
        "삼성 전자": "005930",
        "Samsung Electronics": "005930",
        "SAMSUNG ELECTRONICS CO., LTD.": "005930",
        "삼성화재": "000810",
        "삼성 화재": "000810",
        "Samsung Fire": "000810",
        "HD Hyundai Electric": "267260",
        "HD-Hyundai Electric": "267260",
        "KT&G": "033780",
        "KT & G": "033780",
        "KT and G": "033780",
        "KTNG": "033780",
    }
    for query, ticker in expected.items():
        matches = resolver.search(query)
        assert matches and matches[0]["stock_code"] == ticker, query


def test_official_exact_beats_larger_market_cap():
    matches = _resolver().search("SK")
    assert matches[0]["stock_code"] == "034730"
    assert matches[0]["_resolution"]["match_kind"] == "official"


def test_same_korean_name_historical_duplicate_uses_known_current_market_cap():
    corps = [
        {"corp_code": "old", "corp_name": "SK", "corp_eng_name": "SK Holdings", "stock_code": "003600", "modify_date": "20150101"},
        {"corp_code": "new", "corp_name": "SK", "corp_eng_name": "SK Inc.", "stock_code": "034730", "modify_date": "20260101"},
    ]
    resolver = CompanyResolver(corps, {"sk": "SK"}, MarketContext({"034730": 100}, as_of_date="20260402"))
    matches = resolver.search("SK")
    status, selected, _ = _resolve_match("SK", matches)
    assert status == AnalysisStatus.EXACT
    assert selected and selected["stock_code"] == "034730"


def test_brand_query_auto_selects_dominant_company_with_alternatives():
    matches = _resolver().search("Samsung")
    assert matches[0]["stock_code"] == "005930"
    assert matches[0]["_resolution"]["inferred"] is True
    assert matches[0]["_resolution"]["auto_selected"] is True
    status, selected, candidates = _resolve_match("Samsung", matches)
    assert status == AnalysisStatus.EXACT
    assert selected and selected["stock_code"] == "005930"
    assert len(candidates) >= 2
    assert all(not candidate["_resolution"]["auto_selected"] for candidate in candidates[1:])


def test_single_word_brand_does_not_bind_to_corporation_suffix():
    corps = CORPS + [
        {"corp_code": "00999999", "corp_name": "현대코퍼레이션", "corp_eng_name": "HYUNDAI CORPORATION", "stock_code": "011760", "modify_date": "20250101"},
    ]
    caps = dict(MARKET.market_caps)
    caps["011760"] = 10
    market = MarketContext(caps, MARKET.active_tickers | {"011760"}, MARKET.as_of_date)
    matches = CompanyResolver(corps, ALIASES, market).search("Hyundai")
    assert matches[0]["stock_code"] == "005380"
    assert matches[0]["_resolution"]["match_kind"] == "token"


def test_close_market_caps_auto_select_with_low_confidence_metadata():
    market = MarketContext({"005930": 100, "000810": 90})
    resolver = CompanyResolver(CORPS[:2], ALIASES, market)
    matches = resolver.search("Samsung")
    assert matches[0]["_resolution"]["auto_selected"] is True
    assert matches[0]["_resolution"]["dominant"] is False
    status, selected, _ = _resolve_match("Samsung", matches)
    assert status == AnalysisStatus.EXACT
    assert selected and selected["stock_code"] == "005930"


def test_active_registry_filters_historical_name_candidate_but_corp_code_still_works():
    resolver = _resolver()
    assert all(match["stock_code"] != "099999" for match in resolver.search("Samsung Electronics"))
    direct = resolver.search("00111111")
    assert direct and direct[0]["stock_code"] == "099999"


def test_active_registry_does_not_erase_exact_name_missing_from_krx_universe():
    market = MarketContext({}, frozenset({"005930"}), "20260721")
    resolver = CompanyResolver([CORPS[3]], ALIASES, market)
    matches = resolver.search("HD현대일렉트릭")
    assert matches and matches[0]["stock_code"] == "267260"


def test_active_registry_does_not_revive_inactive_inferred_candidates():
    corps = [
        {"corp_code": "old1", "corp_name": "삼성옛회사", "corp_eng_name": "OLD SAMSUNG ONE", "stock_code": "900001", "modify_date": "20200101"},
        {"corp_code": "old2", "corp_name": "삼성과거회사", "corp_eng_name": "OLD SAMSUNG TWO", "stock_code": "900002", "modify_date": "20200101"},
    ]
    market = MarketContext({"900001": 100, "900002": 90}, frozenset({"005930"}), "20260721")
    assert CompanyResolver(corps, {}, market).search("삼성") == []


def test_curated_historical_alias_wins_over_old_official_name():
    corps = [
        {"corp_code": "old", "corp_name": "셀트리온헬스케어", "corp_eng_name": "CELLTRION HEALTHCARE", "stock_code": "091990", "modify_date": "20230101"},
        {"corp_code": "new", "corp_name": "셀트리온", "corp_eng_name": "CELLTRION", "stock_code": "068270", "modify_date": "20260101"},
    ]
    market = MarketContext({"068270": 100}, frozenset({"068270"}), "20260721")
    matches = CompanyResolver(corps, {"셀트리온헬스케어": "셀트리온"}, market).search("셀트리온헬스케어")
    assert matches and matches[0]["corp_code"] == "new"
    assert matches[0]["_resolution"]["match_kind"] == "alias"


def test_duplicate_strong_normalized_names_remain_ambiguous():
    corps = [
        {"corp_code": "a", "corp_name": "에이", "corp_eng_name": "TWIN CO.,LTD", "stock_code": "100001", "modify_date": "20260101"},
        {"corp_code": "b", "corp_name": "비", "corp_eng_name": "TWIN CORPORATION", "stock_code": "100002", "modify_date": "20260101"},
    ]
    market = MarketContext({"100001": 100, "100002": 90}, frozenset({"100001", "100002"}), "20260721")
    matches = CompanyResolver(corps, {}, market).search("TWIN CO LTD")
    status, selected, _ = _resolve_match("TWIN CO LTD", matches)
    assert status == AnalysisStatus.AMBIGUOUS
    assert selected is None


def test_mixed_korean_english_tokens_resolve():
    matches = _resolver().search("삼성 Electronics")
    assert matches and matches[0]["stock_code"] == "005930"


def test_limited_fuzzy_handles_long_typo_but_not_short_risky_name():
    resolver = _resolver()
    matches = resolver.search("Samsng Electronics")
    assert matches and matches[0]["stock_code"] == "005930"
    assert matches[0]["_resolution"]["match_kind"] == "fuzzy"
    assert resolver.search("삼성전지") == []


def test_legacy_sqlite_schema_forces_one_english_refresh(tmp_path, monkeypatch):
    db = tmp_path / "master.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE _meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO _meta VALUES ('last_updated', datetime('now'))")
        conn.execute("CREATE TABLE corp_codes (corp_code TEXT PRIMARY KEY, corp_name TEXT NOT NULL, stock_code TEXT, modify_date TEXT)")
        conn.execute("INSERT INTO corp_codes VALUES ('00126380', '삼성전자', '005930', '20250101')")
    monkeypatch.setattr(dart_client, "_MASTER_DB_PATH", db)

    assert dart_client.DartClient._master_db_load() is None
    with sqlite3.connect(db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(corp_codes)")}
    assert "corp_eng_name" in columns
    fallback = dart_client.DartClient._master_db_load(require_english=False)
    assert fallback and fallback[0]["corp_name"] == "삼성전자"


def test_stale_sqlite_can_be_used_only_for_failure_fallback(tmp_path, monkeypatch):
    db = tmp_path / "master.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE _meta (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO _meta VALUES ('last_updated', '2020-01-01T00:00:00')")
        conn.execute("CREATE TABLE corp_codes (corp_code TEXT PRIMARY KEY, corp_name TEXT NOT NULL, corp_eng_name TEXT NOT NULL DEFAULT '', stock_code TEXT, modify_date TEXT)")
        conn.execute("INSERT INTO corp_codes VALUES ('00126380', '삼성전자', '', '005930', '20250101')")
    monkeypatch.setattr(dart_client, "_MASTER_DB_PATH", db)
    assert dart_client.DartClient._master_db_load(require_english=False) is None
    fallback = dart_client.DartClient._master_db_load(require_english=False, allow_stale=True)
    assert fallback and fallback[0]["stock_code"] == "005930"


def test_download_validation_rejects_empty_or_partial_master():
    try:
        _validate_corp_master([])
    except ValueError as exc:
        assert "validation failed" in str(exc)
    else:
        raise AssertionError("empty master must be rejected")


def test_expired_market_context_is_reloaded(monkeypatch):
    stale = MarketContext({"005930": 1}, as_of_date="20200101", source="krx_weekly")
    fresh = MarketContext({"005930": 2}, as_of_date="20260721", source="krx_weekly")
    calls = []

    def fake_database_context():
        calls.append(True)
        return fresh

    monkeypatch.setattr(resolver_module, "_market_context", stale)
    monkeypatch.setattr(resolver_module, "_market_context_loaded_at", 0.0)
    monkeypatch.setattr(resolver_module, "_database_market_context", fake_database_context)
    loaded = asyncio.run(resolver_module.load_market_context())
    assert loaded.as_of_date == "20260721"
    assert calls == [True]


def test_new_sqlite_round_trip_preserves_english_name(tmp_path, monkeypatch):
    db = tmp_path / "master.db"
    monkeypatch.setattr(dart_client, "_MASTER_DB_PATH", db)
    dart_client.DartClient._master_db_save(CORPS)
    loaded = dart_client.DartClient._master_db_load()
    assert loaded and loaded[0]["corp_eng_name"] == "SAMSUNG ELECTRONICS CO,.LTD"


def test_english_query_renders_english_resolution_guidance():
    payload = {
        "status": "exact",
        "subject": "삼성전자",
        "warnings": [],
        "data": {
            "query": "Samsung",
            "canonical_name": "삼성전자",
            "company_id": "cmp_005930",
            "company_resolution": {
                "query": "Samsung",
                "response_language": "en",
                "match_type": "inferred",
                "reason": "Ranked candidates by market capitalization",
                "market_data_as_of": "20260402",
                "confidence": "low",
                "alternatives": [{"corp_name": "삼성SDI", "corp_name_eng": "SAMSUNG SDI CO.,LTD", "ticker": "006400"}],
            },
            "identifiers": {"ticker": "005930", "corp_code": "00126380"},
            "classification": {},
            "names": {"en": "SAMSUNG ELECTRONICS CO,.LTD", "aliases": []},
            "basic_info": {},
            "recent_filings": [],
            "recent_filings_window": {},
        },
    }
    rendered = _render_exact(payload)
    assert "## Company resolution" in rendered
    assert "- Confidence: low" in rendered
    assert "- Alternatives: SAMSUNG SDI CO.,LTD(006400)" in rendered
    assert "## Recent filings" in rendered
    assert "회사 식별" not in rendered
