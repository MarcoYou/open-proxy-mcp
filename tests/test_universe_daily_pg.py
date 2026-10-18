"""유니버스·screener 단면을 일별 시세 저장분에서 읽는다 — Postgres 17 에서 두 세계를 증명한다 (261018).

옮긴 것: `screener` 의 기준일(`_krx_latest_dd`)·시총 상위 N(`_krx_top_mktcap`)·시장 전 종목(`_krx_market_codes`)·
카드 시총(`_krx_mktcap_map`)과 `universe` 순위표 행(`_krx_rows`). 종전엔 `krx_weekly` 의 이번 주 행(max(price_dd))을 읽었다.

① **지금 세계** — 쓰는 쪽(`_krx_db_upsert`)이 매 거래일 이번 주 `krx_weekly` 행을 지우고 다시 넣는다. 이때 옮긴 읽기는
   종전 `krx_weekly` 질의와 **같은 값**을 낸다(응답이 바뀌지 않는다).
② **다음 단계 세계** — `krx_weekly` 에 닫힌 지난주 행만 있고 이번 주 최신일은 `krx_daily` 에만 있다. 옮긴 읽기는 최신일
   단면을 내고, 종전 질의는 지난주 값(그 사이 상장폐지된 종목 포함)으로 낡는다.
③ 보관 창 안에서 상장폐지된 종목은 종목별 최신 행 뷰(`price_latest`)에 옛 날짜 행으로 남는다 — 단면 읽기에 섞이지 않는다.
④ 일별 저장분이 없으면(마이그레이션 전) `krx_weekly` 로 돌아가지 않고 기존 강등 경로를 탄다.

데이터는 운영 쓰기 함수(`_krx_db_upsert`)로 넣는다 — 두 표가 운영과 같은 모양이 되도록. Docker 가 없으면 건너뛴다.
"""
from __future__ import annotations

import asyncio

import pytest

psycopg = pytest.importorskip("psycopg")

from test_krx_daily_pg import KRX_WEEKLY_DDL, _ddl, _row  # noqa: E402 — 스키마·KRX 행 모양 재사용

from open_proxy_mcp.market_codes import KQ, KS  # noqa: E402
from open_proxy_mcp.services import screener  # noqa: E402
from open_proxy_mcp.services import universe as uni  # noqa: E402

# 지난주 목·금(20261008·20261009) + 이번 주 월~수(20261012~20261014). 「오늘」은 20261014(수).
A, B, F, D = "900010", "900020", "900030", "900040"      # 코스피 — D 는 20261013 을 끝으로 상장폐지
C, E = "800010", "800020"                                # 코스닥 — E 는 20261013 신규상장
SHRS = 1_000_000
LAST_WEEK, TODAY = "20261009", "20261014"


def _no_shares(code, close):
    """상장주식수가 빈 행 — 시총을 계산할 수 없다(`mktcap IS NOT NULL` 조건이 거르는 경우)."""
    r = _row(code, close, shrs=SHRS)
    r["LIST_SHRS"] = None
    r["MKTCAP"] = None
    return r


#: 날짜 → (코스피 행, 코스닥 행). 종가 × 100만 주 = 시총.
DAYS = {
    "20261008": ([_row(A, 100_000), _row(B, 80_000), _row(D, 500_000), _row(F, 10_000)], [_row(C, 50_000)]),
    "20261009": ([_row(A, 101_000), _row(B, 81_000), _row(D, 500_000), _row(F, 10_000)], [_row(C, 50_000)]),
    "20261012": ([_row(A, 100_000), _row(B, 90_000), _row(D, 400_000), _row(F, 10_000)], [_row(C, 50_000)]),
    "20261013": ([_row(A, 100_000), _row(B, 95_000), _row(D, 300_000), _row(F, 10_000)],
                 [_row(C, 50_000), _row(E, 30_000)]),
    # 오늘: B 가 A 를 앞지르고, D 는 없고(폐지), F 는 상장주식수가 비고, 코스닥은 E 가 C 를 앞지른다
    "20261014": ([_row(A, 100_000), _row(B, 120_000), _no_shares(F, 10_500)], [_row(C, 52_000), _row(E, 60_000)]),
}

#: 오늘 단면의 정답 (ticker, market, mktcap, close, list_shrs) — 시총 내림차순.
TODAY_ROWS = [
    (B, KS, 120_000 * SHRS, 120_000, SHRS),
    (A, KS, 100_000 * SHRS, 100_000, SHRS),
    (E, KQ, 60_000 * SHRS, 60_000, SHRS),
    (C, KQ, 52_000 * SHRS, 52_000, SHRS),
]

#: 종전 읽기(261018 전 `screener`·`universe` 의 SQL 그대로) — 같은 값을 내는지 대조하는 기준.
OLD_LATEST = "SELECT MAX(price_dd) FROM krx_weekly"
OLD_TOP = "SELECT ticker FROM krx_weekly WHERE price_dd=%s AND mktcap IS NOT NULL"
OLD_MARKET = "SELECT ticker FROM krx_weekly WHERE price_dd=%s AND market=%s AND mktcap IS NOT NULL"
OLD_MAP = "SELECT ticker, mktcap FROM krx_weekly WHERE price_dd=%s AND ticker = ANY(%s)"
OLD_ROWS = ("SELECT ticker, market, mktcap, close, list_shrs FROM krx_weekly "
            "WHERE price_dd=%s AND mktcap IS NOT NULL ORDER BY mktcap DESC")


@pytest.fixture
def db(pg17, monkeypatch):
    """두 표를 만들고, 서버 읽기(`pg_rows`)가 풀 없이 이 컨테이너로 직접 접속하게 한다(풀 싱글턴을 남기지 않는다)."""
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(KRX_WEEKLY_DDL)
    monkeypatch.setenv("DATABASE_URL", pg17)
    import open_proxy_mcp.db as dbm
    monkeypatch.setattr(dbm, "_pool", None)
    monkeypatch.setattr(dbm, "_pool_disabled_until", float("inf"))

    async def names():
        return {A: "가나", B: "다라", F: "마바", D: "사아", C: "자차", E: "카타"}
    monkeypatch.setattr(uni, "_names_by_ticker", names)     # DART 회사 원장(네트워크)을 부르지 않는다
    return pg17


def _fill(url):
    """운영 쓰기 함수로 닷새를 차례로 넣는다 — krx_weekly 는 지난주 마지막 날 + 이번 주 최신일, krx_daily 는 닷새 전부."""
    from open_proxy_mcp.services import price_multiple_data as pmd
    _ddl().apply(url)
    for dd, (kospi, kosdaq) in DAYS.items():
        assert pmd._krx_db_upsert(dd, kospi, kosdaq) == (True, True)
    with psycopg.connect(url) as c:
        weekly = [r[0] for r in c.execute("SELECT DISTINCT price_dd FROM krx_weekly ORDER BY 1").fetchall()]
        daily = [r[0] for r in c.execute("SELECT DISTINCT trd_dd FROM krx_daily ORDER BY 1").fetchall()]
    assert weekly == [LAST_WEEK, TODAY]
    assert daily == list(DAYS)


def _old(url, sql, params=()):
    with psycopg.connect(url, autocommit=True) as c:
        return c.execute(sql, params).fetchall()


def _old_top(url, dd, n, market=None):
    q, params = OLD_TOP, [dd]
    if market:
        q += " AND market=%s"
        params.append(market)
    q += " ORDER BY mktcap DESC LIMIT %s"
    params.append(n)
    return {r[0] for r in _old(url, q, tuple(params))}


def _drop_this_week_from_weekly(url):
    """다음 단계 세계 — 쓰는 쪽이 이번 주 krx_weekly 행을 더는 쓰지 않는다(닫힌 주의 마지막 거래일만 남는다)."""
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("DELETE FROM krx_weekly WHERE price_dd > %s", (LAST_WEEK,))


# ── ① 지금 세계: 값이 같다 ──────────────────────────────────────────────────

def test_current_world_moved_reads_equal_the_old_weekly_reads(db):
    _fill(db)
    dd = screener._krx_latest_dd()
    assert dd == _old(db, OLD_LATEST)[0][0] == TODAY
    for market in (None, KS, KQ):
        for n in range(1, 7):
            assert screener._krx_top_mktcap(n, dd, market) == _old_top(db, dd, n, market), (market, n)
    for market in (KS, KQ):
        assert screener._krx_market_codes(market, dd) == {r[0] for r in _old(db, OLD_MARKET, (dd, market))}
    every = [A, B, F, D, C, E]
    assert screener._krx_mktcap_map(every, dd) == {t: int(m) for t, m in _old(db, OLD_MAP, (dd, every)) if m}
    assert uni._krx_rows(dd, None) == _old(db, OLD_ROWS, (dd,)) == TODAY_ROWS
    assert uni._krx_rows(dd, [A, C, D]) == [r for r in TODAY_ROWS if r[0] in (A, C)]


def test_current_world_universe_table_is_today(db):
    _fill(db)
    ul = asyncio.run(uni.list_universe("코스피 시총 상위 2"))
    assert ul.resolved and ul.db_ok and ul.as_of == TODAY
    assert [(r["ticker"], r["mktcap_krw"], r["close_krw"]) for r in ul.rows] == [
        (B, 120_000 * SHRS, 120_000), (A, 100_000 * SHRS, 100_000)]


# ── ② 다음 단계 세계: krx_weekly 에 지난주만 있어도 최신일 단면 ───────────────

def test_next_world_moved_reads_give_the_latest_day_while_old_reads_go_stale(db):
    _fill(db)
    _drop_this_week_from_weekly(db)
    # 종전 읽기는 지난주로 낡는다 — 기준일이 지난 금요일이고, 1위가 그 사이 상장폐지된 D 다
    assert _old(db, OLD_LATEST)[0][0] == LAST_WEEK
    assert _old_top(db, LAST_WEEK, 1, KS) == {D}

    dd = screener._krx_latest_dd()
    assert dd == TODAY
    assert screener._krx_top_mktcap(1, dd, KS) == {B}                 # 오늘 B 가 A 를 앞질렀다
    assert screener._krx_top_mktcap(1, dd, KQ) == {E}                 # 이번 주 신규상장 E
    assert screener._krx_top_mktcap(10, dd, None) == {A, B, C, E}     # D(폐지)·F(시총 없음) 없음
    assert screener._krx_market_codes(KS, dd) == {A, B}
    assert screener._krx_market_codes("KOSDAQ", dd) == {C, E}         # 시장 철자 정규화는 그대로
    assert screener._krx_mktcap_map([A, B, F, D, C, E], dd) == {t: m for t, _, m, _, _ in TODAY_ROWS}
    assert uni._krx_rows(dd, None) == TODAY_ROWS


def test_next_world_universe_and_screener_filter_are_today(db):
    _fill(db)
    _drop_this_week_from_weekly(db)
    uf = asyncio.run(screener.resolve_universe("kospi:1"))
    assert uf.resolved and uf.price_dd == TODAY and uf.allowed == {B}
    whole = asyncio.run(screener.resolve_universe("market:kosdaq"))
    assert whole.resolved and whole.allowed == {C, E} and whole.corp_cls is None

    ul = asyncio.run(uni.list_universe("코스피 시총 상위 2"))
    assert ul.as_of == TODAY
    assert [(r["rank"], r["ticker"], r["mktcap_krw"]) for r in ul.rows] == [
        (1, B, 120_000 * SHRS), (2, A, 100_000 * SHRS)]
    every = asyncio.run(uni.list_universe("전체"))
    assert [r["ticker"] for r in every.rows] == [B, A, E, C]


# ── ③ 폐지 종목의 옛 행은 단면에 섞이지 않는다 ─────────────────────────────

def test_delisted_ticker_lingers_in_price_latest_but_not_in_the_cross_section(db):
    _fill(db)
    _drop_this_week_from_weekly(db)
    latest = dict(_old(db, "SELECT ticker, price_dd FROM price_latest"))
    assert latest[D] == "20261013"                     # 함정이 실제로 있다 — 날짜 조건 없는 종목별 최신 뷰엔 D 가 남는다
    dd = screener._krx_latest_dd()
    assert D not in screener._krx_top_mktcap(100, dd, None)
    assert D not in screener._krx_market_codes(KS, dd)
    assert D not in screener._krx_mktcap_map([D, A], dd)
    assert D not in {r[0] for r in uni._krx_rows(dd, None)}
    assert D not in {r["ticker"] for r in asyncio.run(uni.list_universe("코스피 시총 상위 10")).rows}


# ── ④ 일별 저장분이 없으면 기존 강등 경로 (krx_weekly 로 돌아가지 않는다) ─────

def test_without_daily_store_it_degrades_instead_of_reading_weekly(db):
    from open_proxy_mcp.services import price_multiple_data as pmd
    # 마이그레이션 전: krx_weekly 에만 행이 있다(쓰는 쪽은 그래도 주간을 커밋한다)
    assert pmd._krx_db_upsert(TODAY, *DAYS[TODAY]) == (True, False)
    assert screener._krx_latest_dd() is None
    uf = asyncio.run(screener.resolve_universe("kospi:2"))
    assert uf.resolved is False and uf.allowed is None
    assert uf.notice == "시세 저장분 조회 실패 → 전체시장으로 대체."
    ks = asyncio.run(screener.resolve_universe("market:kospi"))
    assert ks.resolved is False and ks.corp_cls == "Y" and "시세 저장분 조회 실패" in ks.notice
    ul = asyncio.run(uni.list_universe("코스피 시총 상위 2"))
    assert ul.db_ok is False and ul.rows == []
