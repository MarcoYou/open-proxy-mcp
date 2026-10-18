"""일별 시세 약 1년 보관(261019 재설계 v03) — 공표 대비·휴장 판정·달력·영숫자 코드·상장폐지·연말을 Postgres 17 에서 검증한다.

로컬 Docker 의 `postgres:17` 이 필요하다(`tests/conftest.py` 의 `pg17`). Docker 가 없으면 이 파일 전체가 건너뛰어진다.
스키마는 `scripts/krx_daily_ddl.py` 의 STATEMENTS(새 DB 판)로 만든다.
"""
from __future__ import annotations

import asyncio
import pathlib
import sys
from datetime import date, timedelta

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

psycopg = pytest.importorskip("psycopg")

from open_proxy_mcp import krx_daily  # noqa: E402
from test_krx_daily_pg import KRX_WEEKLY_DDL, _apply, _row, _write  # noqa: E402


@pytest.fixture
def db(pg17, monkeypatch):
    """스키마를 깔고, 요청 경로의 DB 읽기(`db.pg_rows`)를 이 DB 로 돌린다. 반환: (url, 질의 목록)."""
    _apply(pg17)
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(KRX_WEEKLY_DDL)
    calls = []

    def fake_pg_rows(sql, params=()):
        calls.append(sql)
        try:
            with psycopg.connect(pg17, autocommit=True) as c:
                return c.execute(sql, params).fetchall()
        except Exception:
            return None
    monkeypatch.setattr("open_proxy_mcp.db.pg_rows", fake_pg_rows)
    return pg17, calls


def _quote(monkeypatch, company_code: str, as_of: str):
    """`build_quote_payload` 를 돌린다 — KRX 라이브를 부르면 그 날짜를 기록하고 None 을 돌려준다(부르지 않아야 하는 시험용)."""
    from open_proxy_mcp.services import trading as T

    async def fake_resolve(q):
        return {"corp_name": "시험회사", "stock_code": company_code, "corp_cls": "Y"}, None
    monkeypatch.setattr(T, "_resolve_listed", fake_resolve)
    live = []

    async def no_live(dd, ticker):
        live.append(dd)
        return None
    monkeypatch.setattr(T, "_krx_quote_row", no_live)
    return asyncio.run(T.build_quote_payload(company_code, as_of=as_of)), live


# ── 공표 대비(krx_chg) ─────────────────────────────────────────────────────

def test_quote_reads_the_published_change_for_005930_and_it_equals_the_stored_previous_close_change(db, monkeypatch):
    url, calls = db
    _write(url, "20261015", [_row("005930", 100_000, shrs=5_919_637_922, cmp=-1_000)], [])
    _write(url, "20261016", [_row("005930", 105_500, shrs=5_919_637_922, cmp=5_500)], [])
    got = krx_daily.read_quote("005930", "")
    assert len(calls) == 1 and got.day == "20261016"
    assert got.row["krx_chg"] == 5_500 == got.row["chg"] and float(got.row["krx_chg_pct"]) == 5.5 == float(got.row["chg_pct"])
    p, live = _quote(monkeypatch, "005930", "")
    q = p["data"]["quote"]
    assert live == [] and p["data"]["source"] == "db" and (q["change_krw"], q["change_pct"]) == (5_500, 5.5)
    assert q["mktcap_krw"] == 105_500 * 5_919_637_922


def test_day_after_a_holiday_uses_the_published_change_without_calling_krx(db, monkeypatch):
    """한글날(20261009 금) 다음 거래일 — 직전 평일 행이 없어 종전에는 KRX 를 2콜 불러 공표값을 받았다. 이제 보관한 공표값으로 0콜이다."""
    url, _ = db
    _write(url, "20261008", [_row("005930", 100_000, cmp=0)], [])
    with psycopg.connect(url) as c:
        krx_daily.record_closed(c, "20261009")
        c.commit()
    _write(url, "20261012", [_row("005930", 102_000, cmp=2_000)], [])
    p, live = _quote(monkeypatch, "005930", "20261012")
    assert live == [] and p["data"]["source"] == "db"
    assert (p["data"]["quote"]["change_krw"], p["data"]["quote"]["change_pct"]) == (2_000, 2.0)


def test_split_day_uses_the_base_price_change_not_the_previous_close(db, monkeypatch):
    """5:1 액면분할 — 직전 종가 대비는 −79.8% 지만 KRX 공표는 기준가(100,000) 대비 +1.00%. 보관한 공표값을 그대로 쓴다(0콜)."""
    url, _ = db
    _write(url, "20261015", [_row("005930", 500_000, shrs=1_000_000, cmp=0)], [])
    _write(url, "20261016", [_row("005930", 101_000, shrs=5_000_000, cmp=1_000)], [])
    got = krx_daily.read_quote("005930", "20261016")
    assert float(got.row["chg_pct"]) == pytest.approx(-79.8) and float(got.row["krx_chg_pct"]) == 1.0
    p, live = _quote(monkeypatch, "005930", "20261016")
    assert live == [] and p["data"]["quote"]["change_pct"] == 1.0 and p["data"]["quote"]["change_krw"] == 1_000


def test_alphanumeric_codes_are_numbered_stored_and_quoted(db, monkeypatch):
    url, _ = db
    _write(url, "20261015", [_row("0001A0", 9_000, cmp=0)], [_row("0004B5", 1_200, cmp=0)])
    _write(url, "20261016", [_row("0001A0", 9_100, cmp=100)], [_row("0004B5", 1_150, cmp=-50)])
    with psycopg.connect(url) as c:
        assert c.execute("SELECT ticker FROM krx_ticker ORDER BY tid").fetchall() == [("0001A0",), ("0004B5",)]
        assert c.execute("SELECT ticker, market, krx_chg FROM krx_daily_v WHERE trd_dd='20261016' ORDER BY 1").fetchall() == [
            ("0001A0", "KS", 100), ("0004B5", "KQ", -50)]
    p, live = _quote(monkeypatch, "0004B5", "")
    assert live == [] and p["data"]["quote"]["close_krw"] == 1_150 and p["data"]["quote"]["change_pct"] == -4.17


# ── 상장폐지(창 안) ────────────────────────────────────────────────────────

def test_a_ticker_delisted_inside_the_window_keeps_its_last_row_but_never_serves_as_today(db, monkeypatch):
    """창 안에서 상장폐지된 종목 — `price_latest` 에는 마지막 거래일 행이 남는다(1년 창이라 종전보다 오래 남는다).
    서빙 기준일 이후 행만 받는 종목 한 줄(`_krx_db_get`)은 미스로 돌리고, 최근 상장주식수(`_shares_ratio` 분자)는 그 마지막 값이다."""
    from open_proxy_mcp.services import price_multiple_data as pmd
    url, _ = db
    _write(url, "20260310", [_row("005930", 70_000), _row("DL0001", 500, shrs=7_000)], [])
    _write(url, "20261016", [_row("005930", 71_000)], [])
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("INSERT INTO krx_weekly VALUES ('20260306','DL0001','KS',480,480*6000,6000), ('20260313','DL0001','KS',500,500*7000,7000)")
        assert c.execute("SELECT ticker, price_dd FROM price_latest ORDER BY 1").fetchall() == [("005930", "20261016"), ("DL0001", "20260310")]
    monkeypatch.setattr(pmd, "_pg_rows", lambda sql, params=(): psycopg.connect(url, autocommit=True).execute(sql, params).fetchall())
    assert pmd._krx_db_get("20261016", "DL0001") == {}                  # 옛 날짜를 오늘 값처럼 내지 않는다(호출자는 라이브로)
    assert pmd._krx_db_get("20261016", "005930")["price"] == 71_000

    async def rows(sql, params=()):
        with psycopg.connect(url, autocommit=True) as c:
            return c.execute(sql, params).fetchall()
    monkeypatch.setattr(pmd, "_db_rows", rows)
    assert asyncio.run(pmd._shares_ratio("DL0001", "20260301")) == pytest.approx(7_000 / 6_000)


# ── 휴장 판정: 기준가 연속성 ─────────────────────────────────────────────────

def test_continuity_tells_a_holiday_from_a_trading_day_krx_returned_empty(db):
    """20260702 사례 재현: 0701 → (0702 빈 응답) → 0703. 0702 가 진짜 휴장이면 0703 기준가 = 0701 종가, 거래일이었으면 0703 기준가 = 0702 종가."""
    url, _ = db
    tickers = [f"{i:06d}" for i in range(1, 201)]
    _write(url, "20260701", [_row(t, 1_000 + i) for i, t in enumerate(tickers)], [])
    # 휴장이었다면: 0703 의 전일대비는 0701 종가 기준
    _write(url, "20260703", [_row(t, 1_010 + i, cmp=10) for i, t in enumerate(tickers)], [])
    with psycopg.connect(url) as c:
        cont = krx_daily.continuity(c, "20260702")
        assert cont == (200, 0)
        assert krx_daily.closed_verdict(True, True, krx_daily.weekdays_after("20260702", "20260706"), cont) == "closed"
        assert krx_daily.record_closed(c, "20260702") is True and krx_daily.record_closed(c, "20260702") is False
        c.commit()
        assert c.execute("SELECT n, closed FROM krx_day WHERE dd='20260702'").fetchone() == (0, True)
    # 거래일이었다면(0702 종가가 0701 과 달랐다): 0703 기준가가 0701 종가와 거의 모든 종목에서 어긋난다
    _write(url, "20260710", [_row(t, 1_000 + i) for i, t in enumerate(tickers)], [])
    _write(url, "20260714", [_row(t, 1_100 + i, cmp=40) for i, t in enumerate(tickers)], [])   # 기준가 1,060+i ≠ 0710 종가
    with psycopg.connect(url) as c:
        cont = krx_daily.continuity(c, "20260713")
        assert cont == (200, 200)
        assert krx_daily.closed_verdict(True, True, 3, cont) == "trading"
        assert krx_daily.continuity(c, "20261030") is None                 # 다음 거래일이 아직 없다 — 볼 수 없다


def test_a_closed_day_never_overwrites_a_trading_day_and_writing_rows_reopens_it(db):
    url, _ = db
    _write(url, "20261016", [_row("005930", 1)], [])
    with psycopg.connect(url) as c:
        assert krx_daily.record_closed(c, "20261016") is False             # 행이 있는 날은 휴장으로 적지 않는다
        assert krx_daily.record_closed(c, "20261009") is True
        c.commit()
    _write(url, "20261009", [_row("005930", 1)], [])                        # 휴장으로 적었던 날에 행이 오면 거래일이다
    with psycopg.connect(url) as c:
        assert c.execute("SELECT dd, n, closed FROM krx_day ORDER BY 1").fetchall() == [("20261009", 1, False), ("20261016", 1, False)]


# ── 달력: 빠진 날 · 보관 창 경계 · 주 닫는 날 ────────────────────────────────────

@pytest.mark.parametrize("latest", ["20261016", "20261019", "20280229", "20270105", "20250301"])
def test_sql_window_start_matches_the_python_definition(db, latest):
    url, _ = db
    with psycopg.connect(url) as c:
        got = c.execute("SELECT to_char((to_date(%s, 'YYYYMMDD') - interval '1 year' - interval '7 days')::date, 'YYYYMMDD')",
                        (latest,)).fetchone()[0]
    assert got == krx_daily.window_lo(latest)


def test_missing_days_are_weekdays_between_the_first_stored_day_and_the_expected_day(db):
    url, _ = db
    for d in ("20261005", "20261006", "20261008", "20261013"):
        _write(url, d, [_row("005930", 1)], [])
    with psycopg.connect(url) as c:
        krx_daily.record_closed(c, "20261009")
        c.commit()

    def read(sql, params=()):
        with psycopg.connect(url, autocommit=True) as c:
            return c.execute(sql, params).fetchall()
    latest, missing = krx_daily.missing_days(read, "20261016")
    assert latest == "20261013"
    assert missing == ["20261016", "20261015", "20261014", "20261012", "20261007"]    # 주말·휴장(10/9)·저장일 제외, 최근 날부터
    assert krx_daily.missing_days(lambda sql, params=(): None, "20261016") is None


def test_missing_days_never_reach_before_the_window_or_backfill_an_empty_table(db):
    url, _ = db

    def read(sql, params=()):
        with psycopg.connect(url, autocommit=True) as c:
            return c.execute(sql, params).fetchall()
    assert krx_daily.missing_days(read, "20261016") == (None, ["20261016"])          # 빈 달력 — 기대 거래일만(1년치를 스스로 받지 않는다)
    _write(url, "20250901", [_row("005930", 1)], [])                                 # 아주 오래된 날 하나 + 최근
    _write(url, "20261014", [_row("005930", 1)], [])
    latest, missing = krx_daily.missing_days(read, "20261016")
    assert latest == "20261014" and min(missing) >= krx_daily.window_lo("20261014")   # 창 첫날(20251007) 이전은 메우지 않는다
    assert missing[:2] == ["20261016", "20261015"]


def _days(lo: str, hi: str, skip=()):
    d, out = date(int(lo[:4]), int(lo[4:6]), int(lo[6:])), []
    end = date(int(hi[:4]), int(hi[4:6]), int(hi[6:]))
    while d <= end:
        s = d.strftime("%Y%m%d")
        if d.isoweekday() < 6 and s not in skip:
            out.append(s)
        d += timedelta(days=1)
    return out


@pytest.mark.parametrize("days", [
    _days("20261005", "20261016", skip={"20261009"}),                         # 금요일 휴장 — 그 주는 목요일로(R2)
    _days("20261228", "20261230"),                                            # 연말 — 12/31 휴장, 아직 다음 해가 없다(열림)
    _days("20261228", "20270105", skip={"20261231"}),                         # 연말 반일·새해 첫 거래일(1/4 월) — 연도에서 주를 나눈다
    _days("20251229", "20260102", skip={"20251231", "20260101"}),             # 2025-12-30(화)·2026-01-02(금) — 같은 ISO 주를 연도로 나눈다
    _days("20261012", "20261016"),                                            # 금요일이 저장되면 그 주가 바로 닫힌다(R1)
])
def test_calendar_week_close_matches_plan_weeks(db, days):
    """달력의 주 닫는 날(`WK_CLOSE_SQL`)이 `plan_weeks`(krx_weekly 로 옮겨 적는 규칙)와 같다 — 두 정의가 어긋나면 주간 뷰(C4)가 다른 날을 낸다."""
    url, _ = db
    for d in days:
        _write(url, d, [_row("005930", 1)], [])
    with psycopg.connect(url) as c:
        got = [r[0] for r in c.execute("SELECT dd FROM krx_day WHERE wk_close ORDER BY 1").fetchall()]
    assert got == [w.last for w in krx_daily.plan_weeks(days) if w.closed]


def test_sync_weekly_only_looks_back_a_few_weeks(db, monkeypatch):
    """보관 창이 1년이 되어도 주간 닫기는 최근 `SYNC_LOOKBACK_DAYS` 일의 주만 확인한다(기록마다 1년치 주를 하나씩 묻지 않는다)."""
    url, _ = db
    for d in _days("20260105", "20260109") + _days("20261012", "20261016"):
        _write(url, d, [_row("005930", 1)], [])
    seen = []
    with psycopg.connect(url) as c:
        real = c.execute

        def spy(sql, params=None):
            seen.append((sql, params))
            return real(sql, params)
        c.execute = spy
        res = krx_daily.sync_weekly(c)
        c.commit()
    assert res.ok and res.closed == ("20261016",)                    # 1월 주는 기간 밖 — 건드리지 않는다
    assert sum(1 for s, _ in seen if s == krx_daily.WEEK_MAX_SQL) == 1


# ── 갱신기 끝까지(가짜 KRX): KRX 오류를 휴장으로 굳히지 않는다 ───────────────────────────

def _refresher(monkeypatch, url, serve):
    """갱신기(`services.krx_refresher`)를 이 DB 에 묶고 KRX 를 `serve(basDd)` → (코스피 행, 코스닥 행) 대역으로 바꾼다. 반환: (R, 받은 날 기록)."""
    from open_proxy_mcp.services import krx_refresher as R
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setattr(R, "_LOGGED_FRESH", {})
    monkeypatch.setattr(R, "_GAP_TRIES", {})
    monkeypatch.setitem(R._COOLDOWN, "n", 0)
    monkeypatch.setitem(R._BUSY_STREAK, "n", 0)
    asked = []

    async def fake_fetch(dd, both=False):
        asked.append(dd)
        ks, kq = serve(dd)
        if not ks and not both:
            return [], [], 1
        return ks, kq, 2
    monkeypatch.setattr(R, "_fetch_day", fake_fetch)
    return R, asked


def _market(close_of, cmp, n=300):
    """코스피·코스닥 각 n 종목(갱신기 최소 행 수 300 이상) — close_of(i) 종가, 공표 대비 cmp."""
    ks = [_row(f"{i:06d}", close_of(i), cmp=cmp) for i in range(n)]
    kq = [_row(f"{900000 + i:06d}", close_of(i), cmp=cmp) for i in range(n)]
    return ks, kq


def _kst(y, m, d, hh, mm):
    from datetime import datetime
    from open_proxy_mcp.clock import KST
    return datetime(y, m, d, hh, mm, tzinfo=KST)


def test_a_trading_day_krx_failed_to_serve_for_a_day_is_not_recorded_as_a_holiday(db, monkeypatch):
    """KRX 가 하루 넘게 오류를 낸다(키 만료·한도·장애 — 갱신기는 오류를 빈 응답으로 받는다). 목 07:30 에는 화(20261013)가 평일 2일이
    지났지만 그 뒤 저장 거래일이 없어 기준가 연속성을 볼 수 없다 → 휴장으로 적지 않는다(종전: 「두 시장 빈 응답 + 평일 2일」로 휴장 →
    달력의 휴장은 다시 묻지 않아 KRX 가 돌아와도 그 거래일이 영영 비었다). KRX 가 돌아오면 그 날을 메운다."""
    url, _ = db
    ks, kq = _market(lambda i: 1_000 + i, 0)
    _write(url, "20261012", ks, kq)
    days = {"20261013": _market(lambda i: 1_010 + i, 10), "20261014": _market(lambda i: 1_020 + i, 10)}
    up = {"on": False}
    R, asked = _refresher(monkeypatch, url, lambda dd: days[dd] if up["on"] and dd in days else ([], []))
    res = asyncio.run(R.run_cycle(_kst(2026, 10, 15, 7, 30)))                   # KRX 장애 중 — 기대 거래일(수)과 화를 묻는다
    assert res.outcome == "not_published" and res.closed_days == () and "20261013" in asked
    with psycopg.connect(url) as c:
        assert c.execute("SELECT count(*) FROM krx_day WHERE dd = '20261013'").fetchone()[0] == 0
    up["on"] = True                                                              # KRX 회복
    res = asyncio.run(R.run_cycle(_kst(2026, 10, 15, 8, 50)))
    assert res.outcome == "refreshed" and res.filled == ("20261013",)
    with psycopg.connect(url) as c:
        assert c.execute("SELECT dd, n, closed FROM krx_day WHERE dd >= '20261013' ORDER BY 1").fetchall() == [
            ("20261013", 600, False), ("20261014", 600, False)]


def test_a_holiday_is_recorded_once_the_next_trading_day_is_stored(db, monkeypatch):
    """한글날(20261009 금) — 화 07:30 에는 다음 거래일(월)이 아직 저장 전이라 판정을 미루고, 월이 저장되는 틱에서 기준가가 이어지는 것을 보고
    휴장으로 적는다. 그 뒤로는 묻지 않는다."""
    url, _ = db
    ks, kq = _market(lambda i: 1_000 + i, 0)
    _write(url, "20261008", ks, kq)
    pub = {"20261012": False}
    mon = _market(lambda i: 1_005 + i, 5)                                       # 기준가 1,000+i = 목요일 종가(이어진다)
    R, asked = _refresher(monkeypatch, url, lambda dd: mon if dd == "20261012" and pub[dd] else ([], []))
    res = asyncio.run(R.run_cycle(_kst(2026, 10, 13, 7, 30)))
    assert res.outcome == "not_published" and res.closed_days == ()            # 월 게시 전 — 금은 아직 모른다
    pub["20261012"] = True
    res = asyncio.run(R.run_cycle(_kst(2026, 10, 13, 8, 50)))
    assert res.outcome == "refreshed" and res.closed_days == ("20261009",)
    with psycopg.connect(url) as c:
        assert c.execute("SELECT dd, n, closed FROM krx_day ORDER BY 1").fetchall() == [
            ("20261008", 600, False), ("20261009", 0, True), ("20261012", 600, False)]
    asked.clear()
    assert asyncio.run(R.run_cycle(_kst(2026, 10, 13, 9, 0))).outcome == "fresh" and asked == []


# ── 보관 창 정리 뒤 BRIN 이 넓어지지 않는다 ─────────────────────────────────────

def _lossy(c, dd):
    """그 날 단면을 BRIN 비트맵으로 읽을 때의 lossy 블록 수."""
    c.execute("SET enable_seqscan = off")
    c.execute("SET enable_indexscan = off")
    plan = "\n".join(r[0] for r in c.execute(
        "EXPLAIN (ANALYZE, COSTS OFF, TIMING OFF, SUMMARY OFF) SELECT count(*) FROM krx_px WHERE trd_dd = %s", (dd,)).fetchall())
    assert "krx_px_dd_brin" in plan, plan
    import re
    return int(re.search(r"lossy=(\d+)", plan).group(1))


def test_pruned_space_reused_by_new_days_does_not_widen_the_brin_for_older_slices(db):
    """창 밖 콜드 날을 지운 빈 자리를 다음 날 행이 다시 써도(기록마다 VACUUM — 갱신기 흐름) 남은 과거일 단면이 그 재사용 블록을 읽지 않는다.
    지우기 직전에 그 범위의 BRIN 요약을 지우지 않으면 요약이 [지운 날, 새 날]로 넓어져 그 사이 모든 날의 단면이 재사용 블록을 다 읽는다
    (10-18 실측: 80일 롤링 뒤 가장 오래된 날 lossy 36 → 2,177 블록)."""
    url, _ = db
    tickers = [f"{i:06d}" for i in range(3_000)]

    def put(dd):
        _write(url, dd, [_row(t, 1_000 + i, cmp=0) for i, t in enumerate(tickers)], [])
        with psycopg.connect(url, autocommit=True) as c:
            c.execute("VACUUM (ANALYZE) krx_px, krx_day")
    old = ["20250106", "20250107", "20250108"]
    for dd in old + ["20251201"]:
        put(dd)
    with psycopg.connect(url, autocommit=True) as c:
        c.execute("UPDATE krx_day SET cold = true WHERE dd = ANY(%s)", (old,))
        base = _lossy(c, date(2025, 12, 1))
        old_last = c.execute("SELECT max((ctid::text::point)[0]) FROM krx_px WHERE trd_dd < '2025-02-01'").fetchone()[0]
    put("20260116")                                                              # 창 첫날 20250109 — 콜드 세 날을 지운다
    with psycopg.connect(url, autocommit=True) as c:
        assert c.execute("SELECT count(*) FROM krx_day WHERE dd = ANY(%s)", (old,)).fetchone()[0] == 0
    for dd in ("20260119", "20260120", "20260121"):                              # 지운 자리를 다시 쓴다
        put(dd)
    with psycopg.connect(url, autocommit=True) as c:
        got = _lossy(c, date(2025, 12, 1))
        reused = c.execute("SELECT count(DISTINCT (ctid::text::point)[0]) FROM krx_px WHERE trd_dd >= '2026-01-19' "
                           "AND (ctid::text::point)[0] <= %s", (old_last,)).fetchone()[0]
    assert reused > 0                                                            # 시험 전제: 새 날 행이 지운 자리를 실제로 다시 썼다
    assert got <= base + krx_daily.BRIN_PAGES_PER_RANGE, (base, got, reused)    # 넓어졌으면 재사용 블록(수십)만큼 는다
