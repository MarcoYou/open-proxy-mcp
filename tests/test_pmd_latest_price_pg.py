"""price_multiple_data 최신값 서빙 — 일별 시세 저장분(`krx_daily`·`krx_daily_v`·`price_latest`)으로 옮긴 읽기를 Postgres 17 에서 검증한다.

261018(Supabase 재설계 5-1, 읽는 쪽). 옮긴 읽기 넷: 서빙 기준일(`_serving_price_dd`) · 종목 한 줄(`_krx_db_get` → `_market_for`) ·
주식수 배율의 최신 주식수(`_shares_ratio`) · 비상장 질의의 상장 후보 시총 정렬. 같은 코드를 두 세계에서 돌린다.

  ① 지금 세계 — 쓰는 쪽이 매 거래일 krx_weekly 의 이번 주 행을 지우고 다시 넣으므로 두 표의 최신값이 같다.
     옮긴 읽기가 **종전 krx_weekly 읽기(아래 `OLD_*`, 바꾸기 전 코드의 SQL 그대로)와 같은 값**을 내야 한다.
  ② 다음 단계 세계 — krx_weekly 에는 닫힌 지난주 행만 있고 이번 주는 krx_daily 에만 있다.
     옮긴 읽기가 **이번 주 최신일 값**을 내야 한다(종전 읽기는 지난주 종가를 냈을 것이다).

로컬 Docker 의 `postgres:17` 이 필요하다(`tests/conftest.py` 의 `pg17`). 없으면 건너뛴다. network 0 — KRX 라이브 경로는 가짜로 막고 호출만 센다.
"""
from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

psycopg = pytest.importorskip("psycopg")

from open_proxy_mcp.services import price_multiple_data as S  # noqa: E402
from test_krx_daily_pg import KRX_WEEKLY_DDL, _ddl  # noqa: E402 — 스키마는 krx_daily 시험과 같은 것을 쓴다

# 10/9(금)은 한글날 휴장 — 지난주 마지막 거래일은 목요일이다. 일별 저장분은 최근 7거래일.
DAYS = ["20261007", "20261008", "20261012", "20261013", "20261014", "20261015", "20261016"]
LAST_WEEK, THIS_WEEK = "20261008", "20261016"
OLD_YEAR_END = "20260102"           # 주식수 배율의 기준일(20251231) 뒤 첫 주

A, B, C, D, E = "005930", "000660", "469900", "0088M0", "005110"
CANDS = {"900001": "테스트전자", "900002": "테스트바이오", "900003": "테스트물산"}
A_SHRS, B_SHRS = 5_919_637_922, 728_002_365


def _daily() -> dict[str, dict[str, tuple[str, int, int]]]:
    """{종목: {거래일: (시장, 종가, 상장주식수)}}."""
    out: dict[str, dict[str, tuple[str, int, int]]] = {}
    out[A] = {d: ("KS", 69_000 + 1_000 * i, A_SHRS) for i, d in enumerate(DAYS)}                     # 75,000 이 최신 종가
    out[B] = {d: (("KS", 200_000, B_SHRS) if d < "20261015" else ("KS", 41_000 if d == "20261015" else 42_000, B_SHRS * 5))
              for d in DAYS}                                                                         # 10/15 액면분할 5:1
    out[C] = {d: ("KQ", 5_000, 10_000_000) for d in DAYS if d <= "20261014"}                         # 10/14 이 마지막 거래(상장폐지)
    out[D] = {d: ("KQ", 20_000 if d == "20261015" else 21_000, 30_000_000) for d in DAYS if d >= "20261015"}  # 10/15 신규상장
    out["900001"] = {d: ("KS", 300_000, 1_000_000) for d in DAYS}
    out["900002"] = {d: ("KS", 200_000, 1_000_000) for d in DAYS}
    out["900003"] = {d: ("KS", 100_000 if d <= LAST_WEEK else 350_000, 1_000_000) for d in DAYS}      # 이번 주에 시총 1위로 올라선다
    return out                                                                                      # E 는 일별 저장분에 없다(창 밖 거래정지 등)


def _weekly(this_week_too: bool) -> list[tuple]:
    """krx_weekly 행 (price_dd, ticker, market, close, mktcap, list_shrs). 시총은 KRX 가 주는 값 = 종가 × 상장주식수."""
    daily = _daily()
    rows = [(OLD_YEAR_END, A, "KS", 60_000, 60_000 * A_SHRS, A_SHRS),
            (OLD_YEAR_END, B, "KS", 180_000, 180_000 * B_SHRS, B_SHRS),
            (OLD_YEAR_END, E, "KS", 9_000, 9_000 * 50_000_000, 50_000_000),
            (LAST_WEEK, E, "KS", 9_500, 9_500 * 55_000_000, 55_000_000)]
    days = [LAST_WEEK] + ([THIS_WEEK] if this_week_too else [])
    for t, by_day in daily.items():
        for d in days:
            if d in by_day:
                m, close, shrs = by_day[d]
                rows.append((d, t, m, close, close * shrs, shrs))
    return rows


@pytest.fixture
def db(pg17, monkeypatch):
    """두 표를 만들고 `_pg_rows` 를 로컬 컨테이너로 돌린다(풀 없이 매번 직접 접속 — 운영 `pg_rows` 와 같은 None/[] 계약). KRX 라이브는 막는다."""
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(KRX_WEEKLY_DDL)
    _ddl().apply(pg17)
    sqls: list[tuple[str, tuple]] = []

    def pg_rows(sql, params=()):
        sqls.append((" ".join(sql.split()), tuple(params)))
        try:
            with psycopg.connect(pg17, autocommit=True) as c:
                return c.execute(sql, params).fetchall()
        except Exception:
            return None
    monkeypatch.setattr("open_proxy_mcp.db.pg_rows", pg_rows)
    live: list[str] = []

    async def no_live(basDd):
        live.append(basDd)
        return {}
    monkeypatch.setattr(S, "_krx_market_live", no_live)
    S._serving_dd_invalidate()
    yield SimpleNamespace(url=pg17, sqls=sqls, live=live)
    S._serving_dd_invalidate()


def _load(url: str, *, this_week_in_weekly: bool) -> None:
    with psycopg.connect(url) as c:
        with c.cursor() as cur:
            cur.executemany("INSERT INTO krx_daily (trd_dd, ticker, market, close, list_shrs) VALUES (%s,%s,%s,%s,%s)",
                            [(d, t, m, close, shrs) for t, by_day in _daily().items() for d, (m, close, shrs) in by_day.items()])
            cur.executemany("INSERT INTO krx_weekly (price_dd, ticker, market, close, mktcap, list_shrs) VALUES (%s,%s,%s,%s,%s,%s)",
                            _weekly(this_week_in_weekly))
        c.commit()


# ── 종전(바꾸기 전 코드) 읽기 — SQL 을 그대로 옮겨 적었다 ─────────────────────────────────────────────
OLD_LATEST = "SELECT MAX(price_dd) FROM krx_weekly"
OLD_GET = "SELECT close, mktcap, list_shrs FROM krx_weekly WHERE price_dd=%s AND ticker=%s"
OLD_SHARES_FIRST = ("SELECT list_shrs FROM krx_weekly WHERE ticker=%s AND price_dd>%s AND list_shrs>0 "
                    "ORDER BY price_dd ASC LIMIT 1")
OLD_SHARES_LAST = "SELECT list_shrs FROM krx_weekly WHERE ticker=%s AND list_shrs>0 ORDER BY price_dd DESC LIMIT 1"
OLD_CAPS = "SELECT ticker, mktcap FROM krx_weekly WHERE price_dd=%s AND ticker = ANY(%s)"


def _q(url, sql, params=()):
    with psycopg.connect(url) as c:
        return c.execute(sql, params).fetchall()


def _old_market_for(url, ticker) -> dict:
    """종전 `_market_for` 의 DB 갈래. 미스면 라이브로 갔고, 이 시험의 라이브는 빈 응답이라 {}."""
    latest = _q(url, OLD_LATEST)[0][0]
    rows = _q(url, OLD_GET, (latest, ticker)) if latest else []
    if rows and rows[0][0]:
        r = rows[0]
        return {"price": r[0], "date": latest, "common_mktcap": r[1], "list_shrs": r[2]}
    return {}


def _mf(ticker) -> dict:
    """`_market_for` 의 값 칸(출처 표시 `source` 를 뺀 것) — 종전 반환과 같은 키로 값을 대조한다.
    261018: 반환에 출처 표시(`source` = "db" / "krx_live")가 더해졌다. 출처는 따로 단언한다."""
    return {k: v for k, v in asyncio.run(S._market_for(ticker)).items() if k != "source"}


def _old_shares_ratio(url, ticker, after_dd):
    first, last = _q(url, OLD_SHARES_FIRST, (ticker, after_dd)), _q(url, OLD_SHARES_LAST, (ticker,))
    if not first or not last or not first[0][0] or not last[0][0]:
        return None
    return last[0][0] / first[0][0]


def _old_candidate_order(url) -> list[str]:
    latest = _q(url, OLD_LATEST)[0][0]
    caps = dict(_q(url, OLD_CAPS, (latest, list(CANDS))))
    return sorted(CANDS, key=lambda t: -(caps.get(t) or 0))


def _candidate_order(monkeypatch, cands: dict[str, str] = CANDS) -> list[str]:
    """비상장 질의(「테스트」)의 상장 후보 안내 순서 — 빌더를 끝까지 돌려 경고 문장에서 읽는다."""
    class _Client:
        async def lookup_corp_code(self, q):
            return {"corp_code": "99999999", "corp_name": "테스트", "stock_code": ""}

        async def lookup_corp_code_all(self, q):
            return []

        async def _load_corp_codes(self):
            return [{"corp_code": f"0{t}", "corp_name": n, "stock_code": t} for t, n in cands.items()]

    async def unresolved(q):
        return None, None
    monkeypatch.setattr(S, "get_dart_client", lambda: _Client())
    monkeypatch.setattr(S, "_resolve_listed", unresolved)
    p = asyncio.run(S.build_valuation_payload("테스트", format="json"))
    assert p["status"] == "unlisted"
    text = p["warnings"][0]
    return sorted(cands, key=lambda t: text.index(f"({t})"))


def _weekly_reads(sqls) -> list[str]:
    return [s for s, _ in sqls if "FROM krx_weekly" in s]


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# ① 지금 세계 — 옮긴 읽기 = 종전 읽기
# ═════════════════════════════════════════════════════════════════════════════════════════════════
def test_current_world_moved_reads_give_the_same_values_as_the_old_krx_weekly_reads(db, monkeypatch):
    _load(db.url, this_week_in_weekly=True)
    assert asyncio.run(S._serving_price_dd()) == _q(db.url, OLD_LATEST)[0][0] == THIS_WEEK
    for t in [A, B, C, D, E, *CANDS]:
        assert _mf(t) == _old_market_for(db.url, t), t
        assert asyncio.run(S._market_for(t)).get("source") == ("db" if _old_market_for(db.url, t) else None), t   # DB 에서 받은 값은 출처가 db
    assert _mf(A) == {"price": 75_000, "date": THIS_WEEK, "common_mktcap": 75_000 * A_SHRS, "list_shrs": A_SHRS}
    assert asyncio.run(S._market_for(D))["price"] == 21_000            # 이번 주 신규상장도 두 길 모두 DB 에서
    for t in (A, B, E):
        assert asyncio.run(S._shares_ratio(t, "20251231")) == _old_shares_ratio(db.url, t, "20251231"), t
    assert asyncio.run(S._shares_ratio(B, "20251231")) == 5.0          # 분할을 본다(계수 누락 탐지의 근거)
    assert _candidate_order(monkeypatch) == _old_candidate_order(db.url) == ["900003", "900001", "900002"]


def test_current_world_delisted_ticker_misses_on_both_paths_and_goes_live(db):
    """보관 창 안에서 상장폐지된 종목 — `price_latest` 에는 옛 날짜 행이 남지만 기준일보다 이르니 최신값으로 내지 않는다(종전과 같게 라이브 확인)."""
    _load(db.url, this_week_in_weekly=True)
    with psycopg.connect(db.url) as c:
        assert c.execute("SELECT price_dd FROM price_latest WHERE ticker=%s", (C,)).fetchone()[0] == "20261014"
    db.live.clear()
    assert asyncio.run(S._market_for(C)) == {} and db.live, "옛 날짜(10/14) 종가를 최신처럼 내면 안 된다"


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# ② 다음 단계 세계 — krx_weekly 는 닫힌 지난주까지, 이번 주는 일별 저장분에만
# ═════════════════════════════════════════════════════════════════════════════════════════════════
def test_next_world_serves_this_weeks_latest_day_not_last_weeks_close(db, monkeypatch):
    _load(db.url, this_week_in_weekly=False)
    assert _q(db.url, OLD_LATEST)[0][0] == LAST_WEEK                    # 종전 기준일이라면 지난주 목요일
    assert asyncio.run(S._serving_price_dd()) == THIS_WEEK
    db.live.clear()
    assert _mf(A) == {"price": 75_000, "date": THIS_WEEK, "common_mktcap": 75_000 * A_SHRS, "list_shrs": A_SHRS}
    assert _old_market_for(db.url, A)["price"] == 70_000                # 종전 읽기였다면 낡은 값
    assert _mf(D) == {"price": 21_000, "date": THIS_WEEK, "common_mktcap": 21_000 * 30_000_000,
                                             "list_shrs": 30_000_000}  # 이번 주 신규상장 — 주간 표엔 아직 없다
    assert db.live == [], "일별 저장분에 있는 종목인데 라이브를 불렀다"
    assert _weekly_reads(db.sqls) == [], "최신값 경로가 krx_weekly 를 읽었다"


def test_next_world_shares_ratio_sees_this_weeks_split(db):
    """주간 표의 최신 행은 분할 전(지난주)이다 — 일별 저장분을 먼저 봐야 이번 주 분할을 배율에 반영한다."""
    _load(db.url, this_week_in_weekly=False)
    assert _old_shares_ratio(db.url, B, "20251231") == 1.0               # 종전 읽기로는 분할을 못 본다
    assert asyncio.run(S._shares_ratio(B, "20251231")) == 5.0
    assert asyncio.run(S._shares_ratio(A, "20251231")) == 1.0


def test_next_world_candidate_order_uses_the_latest_day(db, monkeypatch):
    _load(db.url, this_week_in_weekly=False)
    assert _old_candidate_order(db.url) == ["900001", "900002", "900003"]   # 지난주 시총 순
    assert _candidate_order(monkeypatch) == ["900003", "900001", "900002"]  # 이번 주 최신일 시총 순
    assert _weekly_reads(db.sqls) == []


def test_a_day_stale_serving_date_cache_still_serves_the_new_day_without_live_calls(db, monkeypatch):
    """다른 머신의 갱신기가 새 거래일(10/16)을 쓴 직후 — 이 머신의 기준일 캐시(60초)는 아직 10/15 다.
    종목 한 줄은 기준일 이후 행을 받으므로 새 거래일 값을 그 행의 날짜와 함께 DB 에서 내고, KRX 라이브를 부르지 않는다.
    같은 날짜만 찾았다면 모든 종목이 미스가 나 요청마다 라이브로 갔을 것이다. 상장폐지 종목(마지막 거래 10/14)은 여전히 미스."""
    _load(db.url, this_week_in_weekly=False)
    S._SERVING_DD.update(dd="20261015", at=time.monotonic())
    assert asyncio.run(S._serving_price_dd()) == "20261015"              # 캐시가 하루 낡았다
    db.live.clear()
    assert _mf(A) == {"price": 75_000, "date": THIS_WEEK, "common_mktcap": 75_000 * A_SHRS, "list_shrs": A_SHRS}
    assert _mf(B) == {"price": 42_000, "date": THIS_WEEK, "common_mktcap": 42_000 * B_SHRS * 5,
                                             "list_shrs": B_SHRS * 5}
    assert asyncio.run(S._market_for(D))["date"] == THIS_WEEK
    assert db.live == [], "캐시된 기준일이 하루 낡았다고 라이브를 불렀다"
    assert asyncio.run(S._market_for(C)) == {} and db.live, "옛 날짜(10/14) 종가를 최신처럼 내면 안 된다"
    # 후보 시총 정렬은 캐시된 기준일(10/15)의 행을 그대로 읽는다 — 그날 행도 보관 창 안에 남아 있다
    assert _candidate_order(monkeypatch) == ["900003", "900001", "900002"]
    assert _weekly_reads(db.sqls) == []


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 대신 읽기와 강등 — 일별 저장분에 행이 없거나 표가 없을 때
# ═════════════════════════════════════════════════════════════════════════════════════════════════
def test_shares_ratio_falls_back_to_the_weekly_latest_row_when_the_daily_store_lacks_the_ticker(db):
    _load(db.url, this_week_in_weekly=False)
    assert asyncio.run(S._shares_ratio(E, "20251231")) == pytest.approx(1.1)      # 55,000,000 ÷ 50,000,000 (주간 표의 가장 최근 행)
    assert OLD_SHARES_LAST in [s for s, _ in db.sqls]


def test_without_the_daily_store_serving_degrades_to_live_and_never_to_krx_weekly(db, monkeypatch):
    """마이그레이션 전 배포·표 장애 — 기준일을 못 읽으면 종전 강등 경로(KRX 라이브)를 탄다. 서빙 단면에 krx_weekly 대신 읽기를 새로 만들지 않는다."""
    _load(db.url, this_week_in_weekly=True)
    with psycopg.connect(db.url, autocommit=True) as c:
        c.execute("DROP VIEW price_latest; DROP VIEW krx_daily_v; DROP TABLE krx_daily")
    db.live.clear()
    assert asyncio.run(S._serving_price_dd()) is None
    assert asyncio.run(S._market_for(A)) == {} and db.live              # 라이브로 갔다(이 시험의 라이브는 빈 응답)
    assert _candidate_order(monkeypatch) == list(CANDS)                 # 시총을 못 읽으면 정렬 없이 원래 순서
    assert _weekly_reads(db.sqls) == []
    # 주식수 배율만은 예외 — 최신 주식수를 못 읽으면 종전 krx_weekly 질의로 대신한다(배율이 비면 계수 누락을 못 잡는다)
    assert asyncio.run(S._shares_ratio(B, "20251231")) == 5.0


# ═════════════════════════════════════════════════════════════════════════════════════════════════
# 실행 계획 — 종목 조건이 뷰(LAG 창·DISTINCT ON) 아래 (ticker, trd_dd) 인덱스로 내려가 전 종목을 훑지 않는다
# ═════════════════════════════════════════════════════════════════════════════════════════════════
def test_moved_queries_use_the_index_on_a_full_size_window(db, monkeypatch):
    with psycopg.connect(db.url) as c:
        c.execute("""INSERT INTO krx_daily (trd_dd, ticker, market, open, high, low, close, volume, value, list_shrs)
                     SELECT to_char(date '2026-10-05' + d, 'YYYYMMDD'), lpad(t::text, 6, '0'), 'KS',
                     1000, 1100, 900, 1000 + t % 50, 100, 100000, 1000000
                     FROM generate_series(0, 6) d, generate_series(1, 2800) t""")
        c.execute("ANALYZE krx_daily")
        c.commit()
    db.sqls.clear()
    dd = asyncio.run(S._serving_price_dd())
    assert dd == "20261011"
    S._krx_db_get(dd, "000777")
    asyncio.run(S._shares_ratio("000777", "20251231"))        # 최신 주식수 읽기는 기준일 뒤 첫 주와 함께 늘 나간다
    _candidate_order(monkeypatch, {"000777": "테스트하나", "000778": "테스트둘"})
    daily_sqls = [(s, p) for s, p in db.sqls if "krx_daily" in s or "price_latest" in s]
    assert {s.split(" WHERE ")[0] for s, _ in daily_sqls} == {
        "SELECT max(trd_dd) FROM krx_daily", "SELECT close, mktcap, list_shrs, price_dd FROM price_latest",
        "SELECT list_shrs FROM price_latest", "SELECT ticker, mktcap FROM krx_daily_v"}
    with psycopg.connect(db.url) as c:
        for sql, params in daily_sqls:
            plan = "\n".join(r[0] for r in c.execute("EXPLAIN (COSTS OFF) " + sql, params).fetchall())
            assert "Seq Scan on krx_daily" not in plan, f"{sql}\n{plan}"
            assert "idx_krx_daily_isu" in plan or "krx_daily_pkey" in plan, f"{sql}\n{plan}"
