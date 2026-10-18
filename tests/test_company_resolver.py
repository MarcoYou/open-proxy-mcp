from __future__ import annotations

import asyncio
import logging
import sqlite3
from datetime import date

import pytest

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


#: 활성 명부 밖(합병·상장폐지·코넥스) 회사들 — 이름 조각이 이 회사들을 가리키면 검색은 「못 찾음」이고 제안만 남는다.
_GONE = [
    {"corp_code": "g1", "corp_name": "에이치디현대미포", "corp_eng_name": "HD HYUNDAI MIPO", "stock_code": "010620", "modify_date": "20251201"},
    {"corp_code": "g2", "corp_name": "에이치디현대인프라코어", "corp_eng_name": "HD HYUNDAI INFRACORE", "stock_code": "042670", "modify_date": "20250101"},
    {"corp_code": "g3", "corp_name": "인프라웨어테크놀러지", "corp_eng_name": "INFRAWARE TECHNOLOGY", "stock_code": "247300", "modify_date": "20240101"},
    {"corp_code": "g4", "corp_name": "루트락", "corp_eng_name": "ROOTLOCK", "stock_code": "253610", "modify_date": "20260101"},
    {"corp_code": "g5", "corp_name": "에이플러스에셋", "corp_eng_name": "A PLUS ASSET", "stock_code": "244920", "modify_date": "20260101"},
]


def _gone_resolver(extra: list[dict] | None = None, caps: dict[str, int] | None = None) -> CompanyResolver:
    corps = [*CORPS, *_GONE, *(extra or [])]
    active = frozenset({*MARKET.active_tickers, "244920", *(caps or {})})
    return CompanyResolver(corps, ALIASES, MarketContext({**MARKET.market_caps, **(caps or {})}, active, "20261016"))


def test_suggest_shows_inactive_company_whose_name_contains_the_query_but_never_selects_it():
    resolver = _gone_resolver()
    assert resolver.search("현대미포") == []            # 검색은 비활성 회사를 되살리지 않는다(자동 선택 없음)
    near = resolver.suggest("현대미포")
    assert [c["corp_name"] for c in near] == ["에이치디현대미포"]
    assert "_resolution" not in near[0]                  # 제안은 해석 결과가 아니다 — 확정 표지를 달지 않는다
    assert [c["corp_name"] for c in resolver.suggest("Mipo")] == ["에이치디현대미포"]


def test_suggest_full_query_infix_ranks_before_a_shorter_prefix_match():
    # 앞자르기 「인프라」(3글자)만 맞는 쪽이 시총(로컬 prior)이 커도, 질의 5글자를 다 품은 쪽이 먼저다.
    market = MarketContext({**MARKET.market_caps, "247300": 999}, MARKET.active_tickers, "20261016")
    resolver = CompanyResolver([*CORPS, *_GONE], ALIASES, market)
    assert resolver.search("인프라코어") == []
    near = resolver.suggest("인프라코어")
    assert [c["corp_name"] for c in near] == ["에이치디현대인프라코어", "인프라웨어테크놀러지"]


#: 「루트」를 이름 가운데·앞(혼합 토큰)에 품은 활성 상장사 — 토큰 색인의 「루트」는 코넥스 루트락에만 걸린다.
_ROOTS = [
    {"corp_code": "r1", "corp_name": "에이루트", "corp_eng_name": "A ROOT", "stock_code": "096690", "modify_date": "20250101"},
    {"corp_code": "r2", "corp_name": "루트K", "corp_eng_name": "ROOT K", "stock_code": "195990", "modify_date": "20250101"},
]


def test_suggest_short_fragment_orders_by_market_cap_and_respects_limit():
    resolver = _gone_resolver(_ROOTS, {"096690": 50, "195990": 30})
    assert [c["corp_name"] for c in resolver.suggest("루트")] == ["에이루트", "루트K", "루트락"]
    assert [c["corp_name"] for c in resolver.suggest("루트", limit=2)] == ["에이루트", "루트K"]
    assert resolver.suggest("트") == []                  # 한 글자는 제안하지 않는다


def test_suggest_keeps_prefix_candidates_when_no_name_contains_the_query():
    near = _gone_resolver().suggest("에이플러스에셋어드바이저")
    assert [c["corp_name"] for c in near] == ["에이플러스에셋"]


def test_search_falls_past_inactive_token_hits_to_an_active_substring_match():
    # 「세이」의 토큰(이름 앞부분)은 코넥스 세이프아시아에만 걸려 활성 필터가 통째로 비운다 —
    # 거기서 멈추면 「못 찾음」. 최후 수단으로 부분일치까지 내려가 이름에 「세이」를 품은 활성 상장사를 고른다.
    extra = [
        {"corp_code": "s1", "corp_name": "세이프아시아", "corp_eng_name": "SAFE ASIA", "stock_code": "066330", "modify_date": "20260101"},
        {"corp_code": "s2", "corp_name": "세이브존I&C", "corp_eng_name": "SAVEZONE I&C", "stock_code": "067830", "modify_date": "20250101"},
    ]
    matches = _gone_resolver(extra, {"067830": 40}).search("세이")
    assert [m["corp_name"] for m in matches] == ["세이브존I&C"]
    meta = matches[0]["_resolution"]
    assert meta["match_kind"] == "substring" and meta["inferred"] and meta["auto_selected"]
    status, selected, _ = _resolve_match("세이", matches)
    assert status == AnalysisStatus.EXACT and selected and selected["stock_code"] == "067830"

    roots = _gone_resolver(_ROOTS, {"096690": 50, "195990": 30}).search("루트")
    assert [m["corp_name"] for m in roots] == ["에이루트", "루트K"]      # 루트락(비활성)은 되살리지 않는다
    assert _gone_resolver().search("루트") == []                         # 비활성뿐이면 여전히 못 찾음


def test_last_resort_runs_after_latinized_retry_so_existing_answers_stay():
    # 「엘비」: 토큰은 비활성 엘비라이프뿐. 지금 답(역음차 'lb' → LB세미콘)을 한글 부분일치 「티엘비」가
    # 가로채면 안 된다 — 내려가기는 다른 경로가 모두 빈 뒤에만 한다(시총은 일부러 티엘비가 크다).
    extra = [
        {"corp_code": "l0", "corp_name": "엘비라이프", "corp_eng_name": "LBLIFE", "stock_code": "900010", "modify_date": "20200101"},
        {"corp_code": "l1", "corp_name": "LB세미콘", "corp_eng_name": "LB SEMICON INC", "stock_code": "061970", "modify_date": "20250101"},
        {"corp_code": "l2", "corp_name": "티엘비", "corp_eng_name": "TLB CO., LTD.", "stock_code": "356860", "modify_date": "20250101"},
    ]
    matches = _gone_resolver(extra, {"061970": 30, "356860": 60}).search("엘비")
    assert matches and matches[0]["corp_name"] == "LB세미콘"


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


@pytest.mark.parametrize("markets,expected", [
    (["KS"] * 700 + ["KQ"] * 1_200, True),                     # DB 저장값(260823 개명 뒤) — 종전 판정은 여기서 거짓이었다
    (["KOSPI"] * 700 + ["KOSDAQ"] * 1_200, True),              # 옛 표기 행도 같은 시장으로 센다(to_db)
    ([" ks"] * 700 + ["kq "] * 1_200, True),
    (["KS"] * 699 + ["KQ"] * 1_200, False),                    # 반쪽 단면
    (["KS"] * 2_000, False),                                   # 한 시장만
    (["KOSPI200"] * 1_000 + ["X"] * 1_000 + [None], False),    # 모르는 표기는 어느 시장으로도 세지 않는다
])
def test_complete_universe_counts_markets_by_db_code(markets, expected):
    assert resolver_module._is_complete_universe(markets) is expected


class _FakeConn:
    """`_database_market_context` 가 여는 접속 흉내 — 첫 질의(인자 없음)는 기준일, 둘째(기준일 인자)는 단면 행."""

    def __init__(self, as_of: str, rows: list[tuple]):
        self.as_of, self.rows = as_of, rows

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        one, rows = (self.as_of,), self.rows
        return type("Cur", (), {"fetchone": lambda _s: one, "fetchall": lambda _s: rows})()


def _snapshot(ks: int, kq: int, ks_label: str = "KS", kq_label: str = "KQ") -> list[tuple]:
    return ([(f"{i:06d}", 1_000 + i, ks_label) for i in range(ks)]
            + [(f"{ks + i:06d}", 1_000 + i, kq_label) for i in range(kq)])


def test_database_market_context_turns_the_active_registry_on_for_a_full_ks_kq_snapshot(monkeypatch):
    """실제 함수 경로 — DB 가 주는 KS/KQ 그대로의 전 종목 단면이면 활성 명부가 켜진다(종전엔 늘 None)."""
    import psycopg
    rows = _snapshot(800, 1_300)
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: _FakeConn("20261016", rows))
    monkeypatch.setattr(resolver_module, "today_kst", lambda: date(2026, 10, 18))
    ctx = resolver_module._database_market_context()
    assert ctx is not None and ctx.active_tickers == frozenset(t for t, _c, _m in rows)
    # 같은 단면이라도 낡았으면(14일 초과) 켜지 않는다
    monkeypatch.setattr(resolver_module, "today_kst", lambda: date(2026, 11, 1))
    assert resolver_module._database_market_context().active_tickers is None


def test_full_size_snapshot_with_unknown_market_labels_stays_off_and_says_why(monkeypatch, caplog):
    """행 수는 전 종목인데 시장 표기를 못 알아보면 끄되, 이번엔 조용히 끄지 않는다 — 로그에 시장 칸 값을 남긴다."""
    import psycopg
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake")
    monkeypatch.setattr(psycopg, "connect",
                        lambda *a, **k: _FakeConn("20261016", _snapshot(800, 1_300, "STK", "KSQ")))
    monkeypatch.setattr(resolver_module, "today_kst", lambda: date(2026, 10, 18))
    with caplog.at_level(logging.WARNING, logger=resolver_module.__name__):
        ctx = resolver_module._database_market_context()
    assert ctx is not None and len(ctx.market_caps) == 2_100 and ctx.active_tickers is None
    assert any("활성 명부를 끈다" in r.getMessage() and "STK" in r.getMessage() for r in caplog.records)
    # 반쪽 단면(행 수 자체가 모자람)은 평소 상황이라 경고하지 않는다
    caplog.clear()
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: _FakeConn("20261016", _snapshot(300, 300)))
    with caplog.at_level(logging.WARNING, logger=resolver_module.__name__):
        assert resolver_module._database_market_context().active_tickers is None
    assert not caplog.records


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
