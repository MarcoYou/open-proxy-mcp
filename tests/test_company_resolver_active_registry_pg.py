"""이름 해석의 활성 상장 명부를 **실제 쓰기 → 실제 읽기 → 보호 → 해석** 한 경로로 진짜 Postgres 17 에서 돌린다.

261018: 완전성 판정이 'KOSPI'/'KOSDAQ' 을 세는데 DB 값은 260823 개명 뒤 KS/KQ 라 `active_tickers` 가 늘 None 이었다.
활성 명부 필터(`_active_preferred`)와 이전 맥락 유지 보호(`_should_keep_previous`, PR #86)가 운영에서 한 번도 켜지지 않았는데,
기존 시험은 `MarketContext(..., active_tickers=frozenset(...))` 를 손으로 만들어 넣어 그 경로를 한 번도 지나지 않았다.
여기서는 단면을 서버의 실제 시세 쓰기(`_krx_db_upsert` — krx_daily 에 쓰고, 금요일이면 그 주를 krx_weekly 에 닫아 넣는다)로 만들고
맥락은 실제 읽기로만 얻는다.
그래서 읽기 원천이 krx_weekly 든 krx_daily 든(재설계 5-1 읽는 쪽) 같은 시험이 그대로 통한다.

로컬 Docker 의 `postgres:17` 이 필요하다(`tests/conftest.py` 의 `pg17`). Docker 가 없으면 건너뛴다. 네트워크 0.
"""
from __future__ import annotations

import asyncio
from datetime import date

import pytest

psycopg = pytest.importorskip("psycopg")

from test_krx_daily_pg import KRX_WEEKLY_DDL, _apply, _row  # noqa: E402 — 스키마·KRX 행 모양은 그 시험과 같은 것을 쓴다

from open_proxy_mcp import company_resolver as CR  # noqa: E402
from open_proxy_mcp.company_resolver import CompanyResolver, MarketContext  # noqa: E402
from open_proxy_mcp.services import price_multiple_data as pmd  # noqa: E402

TODAY = date(2026, 10, 20)
DAY, NEXT_DAY = "20261016", "20261019"          # 금 · 다음 주 월
KS_N, KQ_N = 800, 1_300                          # 판정 기준(700·1,200)을 넘는 전 종목 단면
KS_TICKERS = [f"1{i:05d}" for i in range(KS_N)]
KQ_TICKERS = [f"2{i:05d}" for i in range(KQ_N)]
ACTIVE, DELISTED = KS_TICKERS[0], "010620"       # 단면에 있는 회사 · 상장폐지돼 단면에 없지만 DART 가 종목코드를 남긴 회사

CORPS = [
    {"corp_code": "00164830", "corp_name": "에이치디현대중공업", "corp_eng_name": "HD HYUNDAI HEAVY INDUSTRIES",
     "stock_code": ACTIVE, "modify_date": "20250101"},
    {"corp_code": "00164609", "corp_name": "에이치디현대미포", "corp_eng_name": "HD HYUNDAI MIPO",
     "stock_code": DELISTED, "modify_date": "20251201"},
]


def _write(dd: str, ks: list[str], kq: list[str]) -> None:
    kospi = [_row(t, 10_000 + i, shrs=1_000) for i, t in enumerate(ks)]
    kosdaq = [_row(t, 5_000 + i, shrs=1_000) for i, t in enumerate(kq)]
    assert pmd._krx_db_upsert(dd, kospi, kosdaq).daily_ok


@pytest.fixture
def full_snapshot(pg17, monkeypatch):
    """서버의 실제 쓰기로 KS 800 · KQ 1,300 전 종목 단면 하루를 쓴다. 시장 맥락 캐시는 비운 상태로 시작한다."""
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(KRX_WEEKLY_DDL)
    _apply(pg17)
    monkeypatch.setenv("DATABASE_URL", pg17)
    monkeypatch.setattr(CR, "today_kst", lambda: TODAY)
    _write(DAY, KS_TICKERS, KQ_TICKERS)
    with psycopg.connect(pg17) as c:                   # 쓰기는 DB 저장값(KS/KQ)으로 한다 — 결함의 조건 그대로
        assert sorted(c.execute("SELECT DISTINCT market FROM krx_weekly").fetchall()) == [("KQ",), ("KS",)]
    monkeypatch.setattr(CR, "_market_context", None)
    monkeypatch.setattr(CR, "_market_context_loaded_at", 0.0)
    monkeypatch.setattr(CR, "_market_context_valid_for", CR._MARKET_CONTEXT_TTL_SECONDS)
    monkeypatch.setattr(CR, "_local_market_context", lambda: MarketContext({}, source="local_popularity_prior"))
    return pg17


def _load() -> MarketContext:
    CR._market_context_loaded_at = 0.0                 # 유효 시간이 지났다고 치고 다시 읽게 한다
    return asyncio.run(CR.load_market_context())


def test_a_full_ks_kq_snapshot_written_by_the_server_turns_the_active_registry_on(full_snapshot):
    ctx = CR._database_market_context()
    assert ctx is not None and ctx.as_of_date == DAY
    assert ctx.active_tickers == frozenset(KS_TICKERS + KQ_TICKERS)
    assert CR._is_complete_recent(ctx)                  # 지킬 만한 맥락으로 인정된다(보호가 켜질 수 있다)


@pytest.mark.parametrize("refresh,keeps", [
    ("db_unreachable", True),       # 접속 실패·시간 초과 → None
    ("half_snapshot", True),        # 다음 날 단면이 반쪽만 쓰였다 → active 없음
    ("complete_snapshot", False),   # 다음 날 전 종목 → 정상 교체
])
def test_keep_previous_matrix_through_the_real_read(full_snapshot, monkeypatch, refresh, keeps):
    """보호 행렬(`test_should_keep_previous_matrix`)을 손으로 만든 맥락이 아니라 실제 읽기가 만든 맥락으로 다시 돈다."""
    first = _load()
    assert first.active_tickers is not None and first.as_of_date == DAY      # 종전엔 여기서 None — 보호가 시작도 못 했다

    if refresh == "db_unreachable":
        monkeypatch.setenv("DATABASE_URL", "postgresql://postgres:test@127.0.0.1:1/none")
    elif refresh == "half_snapshot":
        _write(NEXT_DAY, KS_TICKERS[:100], KQ_TICKERS[:100])
    else:
        _write(NEXT_DAY, KS_TICKERS, KQ_TICKERS)

    second = _load()
    if keeps:
        assert second is first and CR._market_context_valid_for == CR._MARKET_CONTEXT_RETRY_SECONDS
    else:
        assert second.as_of_date == NEXT_DAY and second.active_tickers is not None
        assert CR._market_context_valid_for == CR._MARKET_CONTEXT_TTL_SECONDS


def test_inferred_search_flags_a_delisted_company_but_its_exact_name_still_resolves(full_snapshot):
    """활성 명부가 켜졌을 때 사용자에게 보이는 차이 — 운영 실측 「현대미포」→ 에이치디현대미포(상장폐지).
    261018: 활성 회사엔 맞는 이름이 없고 비활성 회사 하나만 맞으면 그 회사를 **비활성 표지와 함께** 고른다(종전 이 시험은 「못 찾음」을 단언했다)."""
    ctx = _load()
    resolver = CompanyResolver(CORPS, {}, ctx)
    hit = resolver.search("현대미포")
    assert [c["stock_code"] for c in hit] == [DELISTED]
    assert hit[0]["_resolution"]["inactive_listing"] is True and hit[0]["_resolution"]["inferred"]
    exact = resolver.search("에이치디현대미포")                                 # 정식명은 그대로 찾는다
    assert [c["stock_code"] for c in exact] == [DELISTED]
    assert exact[0]["_resolution"]["active_registry_used"] is True
    assert [c["stock_code"] for c in resolver.search("현대중공업")] == [ACTIVE]

    # 대조: 같은 시총·기준일에 명부만 끄면(고치기 전 운영) 폐지 회사가 추정 1순위로 나온다
    off = CompanyResolver(CORPS, {}, MarketContext(ctx.market_caps, None, ctx.as_of_date, ctx.source))
    assert [c["stock_code"] for c in off.search("현대미포")] == [DELISTED]
    assert "inactive_listing" not in off.search("현대미포")[0]["_resolution"]   # 명부가 꺼지면 비활성인지 모른다 — 표지도 없다
