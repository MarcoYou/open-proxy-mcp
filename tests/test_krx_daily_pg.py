"""krx_daily — Postgres 17 에서만 검증되는 부분(뷰·권한·트랜잭션·한 왕복 조회).

로컬 Docker 의 `postgres:17` 이 필요하다(`tests/conftest.py` 의 `pg17`). Docker 가 없으면 이 파일 전체가 건너뛰어진다 —
같은 로직 중 SQLite 로 되는 부분은 `test_krx_daily.py` 가 따로 검사한다.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

psycopg = pytest.importorskip("psycopg")

from open_proxy_mcp import krx_daily  # noqa: E402


def _ddl():
    spec = importlib.util.spec_from_file_location("krx_daily_ddl", ROOT / "scripts" / "krx_daily_ddl.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


KRX_WEEKLY_DDL = """CREATE TABLE IF NOT EXISTS krx_weekly (
  price_dd text NOT NULL, ticker text NOT NULL, market text,
  close bigint, mktcap bigint, list_shrs bigint,
  PRIMARY KEY (price_dd, ticker));"""


def _row(code, close, *, o=None, h=None, l=None, vol=1000, val=5_000_000, shrs=1_000_000):
    f = lambda n: None if n is None else f"{n:,}"   # noqa: E731 — KRX 는 쉼표 문자열로 준다
    return {"ISU_CD": code, "TDD_CLSPRC": f(close), "TDD_OPNPRC": f(o if o is not None else close),
            "TDD_HGPRC": f(h if h is not None else close), "TDD_LWPRC": f(l if l is not None else close),
            "ACC_TRDVOL": f(vol), "ACC_TRDVAL": f(val), "LIST_SHRS": f(shrs), "MKTCAP": f(close * shrs),
            "MKT_NM": "KOSPI", "FLUC_RT": "0.00", "CMPPREVDD_PRC": "0"}


def _apply(url):
    _ddl().apply(url)


def _write(url, dd, kospi, kosdaq):
    with psycopg.connect(url) as c:
        out = krx_daily.write_daily(c, dd, kospi, kosdaq)
        c.commit()
    return out


# ── 스키마 ────────────────────────────────────────────────────────────────

def test_ddl_is_idempotent_and_creates_everything(pg17):
    _apply(pg17)
    _apply(pg17)                                        # 두 번째도 오류 없이 같은 결과
    with psycopg.connect(pg17) as c:
        rels = dict(c.execute("SELECT relname, relkind FROM pg_class WHERE relname IN "
                              "('krx_daily','krx_daily_v','price_latest','idx_krx_daily_isu')").fetchall())
        assert rels == {"krx_daily": "r", "krx_daily_v": "v", "price_latest": "v", "idx_krx_daily_isu": "i"}
        pk = c.execute("SELECT string_agg(a.attname, ',' ORDER BY k.ord) FROM pg_index i "
                       "JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(attnum, ord) ON true "
                       "JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum "
                       "WHERE i.indrelid='krx_daily'::regclass AND i.indisprimary").fetchone()[0]
        assert pk == "trd_dd,ticker"
        idx = c.execute("SELECT pg_get_indexdef('idx_krx_daily_isu'::regclass)").fetchone()[0]
        assert "(ticker, trd_dd)" in idx
        cols = [r[0] for r in c.execute("SELECT column_name FROM information_schema.columns "
                                        "WHERE table_name='krx_daily' ORDER BY ordinal_position").fetchall()]
        assert cols == ["trd_dd", "ticker", "market", "open", "high", "low", "close", "volume", "value", "list_shrs"]
        assert "mktcap" not in cols and "chg_pct" not in cols       # 사실값만 저장한다
        assert c.execute("SELECT relrowsecurity FROM pg_class WHERE relname='krx_daily'").fetchone()[0] is True
        pol = c.execute("SELECT policyname, cmd, qual FROM pg_policies WHERE tablename='krx_daily'").fetchall()
        assert pol == [("krx_daily_read", "SELECT", "true")]
        opts = dict(c.execute("SELECT relname, reloptions FROM pg_class WHERE relname IN ('krx_daily_v','price_latest')").fetchall())
        assert all(v == ["security_invoker=true"] for v in opts.values())


def test_no_grants_for_anon_or_authenticated_even_with_default_privileges(pg17):
    """프로젝트의 기본 권한이 새 표·뷰에 anon/authenticated SELECT 를 자동으로 주므로, DDL 이 그것을 거둔다."""
    # 대조군: 기본 권한만 있는 임의의 새 표는 anon 이 읽는다 (픽스처가 운영과 같은 기본 권한을 깔아 둠)
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute("CREATE TABLE control_t (x int)")
        assert c.execute("SELECT has_table_privilege('anon','control_t','SELECT')").fetchone()[0] is True
    _apply(pg17)
    with psycopg.connect(pg17) as c:
        for obj in ("krx_daily", "krx_daily_v", "price_latest"):
            for role in ("anon", "authenticated"):
                for priv in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                    assert c.execute("SELECT has_table_privilege(%s,%s,%s)", (role, obj, priv)).fetchone()[0] is False, \
                        f"{role} 에 {obj} {priv} 권한이 남아 있다"
        # 서버가 쓰는 역할과 읽기 전용 역할은 그대로 읽는다
        assert c.execute("SELECT has_table_privilege('opm_ro','krx_daily','SELECT')").fetchone()[0] is True
        assert c.execute("SELECT has_table_privilege('opm_ro','price_latest','SELECT')").fetchone()[0] is True
        assert c.execute("SELECT has_table_privilege('service_role','krx_daily','SELECT')").fetchone()[0] is True


def test_read_only_role_reads_through_rls_policy_and_views(pg17):
    """opm_ro 는 BYPASSRLS 가 없다 — 정책(USING true)과 security_invoker 뷰를 거쳐 읽혀야 한다."""
    _apply(pg17)
    _write(pg17, "20261015", [_row("005930", 100_000)], [_row("247540", 50_000)])
    _write(pg17, "20261016", [_row("005930", 101_000)], [_row("247540", 49_000)])
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute("SET ROLE opm_ro")
        n = c.execute("SELECT count(*) FROM krx_daily").fetchone()[0]
        v = c.execute("SELECT ticker, chg_pct FROM price_latest ORDER BY ticker").fetchall()
        c.execute("RESET ROLE")
    assert n == 4
    assert [(t, float(p)) for t, p in v] == [("005930", 1.0), ("247540", -2.0)]
    # anon 은 막힌다
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute("SET ROLE anon")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT count(*) FROM krx_daily")


# ── 뷰 계산 ────────────────────────────────────────────────────────────────

def test_view_computes_mktcap_and_change_vs_previous_stored_day(pg17):
    _apply(pg17)
    _write(pg17, "20261014", [_row("A00001", 1000, shrs=10), _row("A00002", 200, shrs=5)], [])
    _write(pg17, "20261015", [_row("A00001", 1100, shrs=10)], [])                       # A00002 는 이 날 행이 없다(정지·구멍)
    _write(pg17, "20261016", [_row("A00001", 990, shrs=10), _row("A00002", 210, shrs=5)], [])
    with psycopg.connect(pg17) as c:
        rows = {(t, d): (m, p, ch, pc) for t, d, m, p, ch, pc in c.execute(
            "SELECT ticker, trd_dd, mktcap, prev_close, chg, chg_pct FROM krx_daily_v").fetchall()}
    # 시총 = 종가 × 상장주식수
    assert rows[("A00001", "20261014")][0] == 10_000
    # 보관 창의 첫날은 직전 행이 없어 비어 있다
    assert rows[("A00001", "20261014")][1:] == (None, None, None)
    assert rows[("A00002", "20261014")][1:] == (None, None, None)
    # 직전 거래일 종가 대비 (소수 2자리)
    assert rows[("A00001", "20261015")][1:] == (1000, 100, pytest.approx(10.00))
    assert rows[("A00001", "20261016")][1:] == (1100, -110, pytest.approx(-10.00))
    # 종목마다 자기 직전 행을 본다 — A00002 의 직전은 15일이 아니라 14일
    assert rows[("A00002", "20261016")][1:] == (200, 10, pytest.approx(5.00))


def test_view_change_is_null_not_error_when_previous_close_is_zero_or_null(pg17):
    _apply(pg17)
    _write(pg17, "20261015", [_row("Z00001", 0), _row("Z00002", 100)], [])
    _write(pg17, "20261016", [_row("Z00001", 500), {**_row("Z00002", 0), "TDD_CLSPRC": "-"}], [])
    with psycopg.connect(pg17) as c:
        got = {t: (pc, ch, cp) for t, pc, ch, cp in c.execute(
            "SELECT ticker, prev_close, chg, chg_pct FROM krx_daily_v WHERE trd_dd='20261016'").fetchall()}
    assert got["Z00001"] == (0, 500, None)            # 직전 종가 0 → 등락률은 NULL(0 으로 나누지 않는다)
    assert got["Z00002"] == (100, None, None)         # 종가 '-' → NULL


def test_price_latest_is_one_row_per_ticker_at_its_own_latest_day(pg17):
    _apply(pg17)
    _write(pg17, "20261014", [_row("L00001", 10), _row("L00002", 20)], [])
    _write(pg17, "20261015", [_row("L00001", 11)], [])                                    # L00002 는 15일 이후 없음(상폐 등)
    _write(pg17, "20261016", [_row("L00001", 12), _row("L00003", 30)], [])
    with psycopg.connect(pg17) as c:
        got = c.execute("SELECT ticker, trd_dd, price_dd, close FROM price_latest ORDER BY ticker").fetchall()
        n = c.execute("SELECT count(*) FROM price_latest").fetchone()[0]
    assert n == 3
    assert got == [("L00001", "20261016", "20261016", 12), ("L00002", "20261014", "20261014", 20),
                   ("L00003", "20261016", "20261016", 30)]


# ── 쓰기: 모든 필드 · 7거래일 롤링 · krx_weekly 와 같은 트랜잭션 ─────────────────────

def test_write_daily_stores_every_field_for_every_ticker_with_market_tags(pg17):
    _apply(pg17)
    kospi = [_row("005930", 71_000, o=70_500, h=71_500, l=70_000, vol=12_345_678, val=876_543_210_000, shrs=5_919_637_922)]
    kosdaq = [_row("247540", 250_000, o=251_000, h=255_000, l=248_000, vol=111, val=27_750_000, shrs=97_000_000)]
    ok, n, pruned = _write(pg17, "20261016", kospi, kosdaq)
    assert (ok, n, pruned) == (True, 2, 0)
    with psycopg.connect(pg17) as c:
        got = c.execute("SELECT trd_dd, ticker, market, open, high, low, close, volume, value, list_shrs "
                        "FROM krx_daily ORDER BY ticker").fetchall()
    assert got == [("20261016", "005930", "KS", 70_500, 71_500, 70_000, 71_000, 12_345_678, 876_543_210_000, 5_919_637_922),
                   ("20261016", "247540", "KQ", 251_000, 255_000, 248_000, 250_000, 111, 27_750_000, 97_000_000)]


def test_write_daily_is_idempotent_for_the_same_day(pg17):
    _apply(pg17)
    _write(pg17, "20261016", [_row("005930", 71_000)], [_row("247540", 250_000)])
    _write(pg17, "20261016", [_row("005930", 72_000)], [_row("247540", 250_000)])       # 정정 재수신
    with psycopg.connect(pg17) as c:
        assert c.execute("SELECT count(*) FROM krx_daily").fetchone()[0] == 2
        assert c.execute("SELECT close FROM krx_daily WHERE ticker='005930'").fetchone()[0] == 72_000


def test_rolling_keeps_only_the_7_most_recent_distinct_days(pg17):
    _apply(pg17)
    days = ["20261005", "20261006", "20261007", "20261008", "20261012", "20261013", "20261014",   # 10/9 한글날 휴장 포함 — 날짜 연속성이 아니라 「서로 다른 날 7개」
            "20261015", "20261016"]
    pruned_seen = []
    for i, d in enumerate(days):
        ok, n, pruned = _write(pg17, d, [_row("005930", 100 + i), _row("000660", 200 + i)], [_row("247540", 300 + i)])
        assert ok and n == 3
        pruned_seen.append(pruned)
        with psycopg.connect(pg17) as c:
            kept = [r[0] for r in c.execute("SELECT DISTINCT trd_dd FROM krx_daily ORDER BY 1").fetchall()]
        assert kept == days[max(0, i - 6): i + 1], f"{d} 직후 보관일: {kept}"
    assert pruned_seen == [0] * 7 + [3, 3]            # 8번째·9번째 날에 가장 오래된 날(3행)이 하나씩 지워진다
    with psycopg.connect(pg17) as c:
        assert c.execute("SELECT count(*) FROM krx_daily").fetchone()[0] == 7 * 3


def test_late_correction_of_old_day_does_not_resurrect_pruned_days(pg17):
    """보관 창 밖의 옛 날짜가 뒤늦게 들어와도(재처리) 곧바로 다시 지워져 7일 창이 유지된다."""
    _apply(pg17)
    for d in ("20261008", "20261012", "20261013", "20261014", "20261015", "20261016", "20261019"):
        _write(pg17, d, [_row("005930", 1)], [])
    _write(pg17, "20261001", [_row("005930", 1)], [])
    with psycopg.connect(pg17) as c:
        kept = [r[0] for r in c.execute("SELECT DISTINCT trd_dd FROM krx_daily ORDER BY 1").fetchall()]
    assert len(kept) == 7 and "20261001" not in kept


def test_upsert_writes_weekly_and_daily_in_one_transaction_and_isolates_daily_failure(pg17, monkeypatch):
    """`_krx_db_upsert`: krx_weekly 는 지금과 같게 쓰고(같은 ISO주의 옛 일자 삭제), krx_daily 는 같은 커밋에 넣는다.
    krx_daily 가 없어도(마이그레이션 전) krx_weekly 쓰기는 커밋된다."""
    from open_proxy_mcp.services import price_multiple_data as pmd
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(KRX_WEEKLY_DDL)
    monkeypatch.setenv("DATABASE_URL", pg17)

    # ① 표가 없을 때 — krx_weekly 는 그대로 동작한다
    ok = pmd._krx_db_upsert("20261013", [_row("005930", 70_000)], [_row("247540", 250_000)])
    assert ok is True
    with psycopg.connect(pg17) as c:
        assert c.execute("SELECT price_dd, ticker, market, close, mktcap, list_shrs FROM krx_weekly ORDER BY ticker").fetchall() == [
            ("20261013", "005930", "KS", 70_000, 70_000 * 1_000_000, 1_000_000),
            ("20261013", "247540", "KQ", 250_000, 250_000 * 1_000_000, 1_000_000)]

    # ② 마이그레이션 적용 뒤 — 같은 호출이 두 표를 함께 쓴다. 같은 주(10/12~18)의 옛 일자 20261013 은 weekly 에서 지워진다
    _apply(pg17)
    assert pmd._krx_db_upsert("20261015", [_row("005930", 71_000)], [_row("247540", 251_000)]) is True
    with psycopg.connect(pg17) as c:
        assert [r[0] for r in c.execute("SELECT DISTINCT price_dd FROM krx_weekly").fetchall()] == ["20261015"]
        assert c.execute("SELECT trd_dd, ticker, close, volume, value FROM krx_daily ORDER BY ticker").fetchall() == [
            ("20261015", "005930", 71_000, 1000, 5_000_000), ("20261015", "247540", 251_000, 1000, 5_000_000)]


def test_upsert_failure_in_daily_rolls_back_only_the_daily_part(pg17, monkeypatch):
    """krx_daily 쪽 SQL 이 실패해도 SAVEPOINT 로 격리돼 krx_weekly 는 커밋된다(오류는 반환값·로그로 드러난다)."""
    from open_proxy_mcp.services import price_multiple_data as pmd
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(KRX_WEEKLY_DDL)
        c.execute("CREATE TABLE krx_daily (trd_dd text NOT NULL, ticker text NOT NULL, PRIMARY KEY (trd_dd, ticker))")  # 열이 모자란 가짜 표
    monkeypatch.setenv("DATABASE_URL", pg17)
    assert pmd._krx_db_upsert("20261015", [_row("005930", 71_000)], [_row("247540", 251_000)]) is True
    with psycopg.connect(pg17) as c:
        assert c.execute("SELECT count(*) FROM krx_weekly").fetchone()[0] == 2
        assert c.execute("SELECT count(*) FROM krx_daily").fetchone()[0] == 0


# ── 한 왕복 조회 ───────────────────────────────────────────────────────────

@pytest.fixture
def quote_db(pg17, monkeypatch):
    """`read_quote` 가 쓰는 `pg_rows` 를 로컬 컨테이너로 돌리고 호출 횟수(=왕복 수)를 센다."""
    _apply(pg17)
    calls = []

    def fake_pg_rows(sql, params=()):
        calls.append(sql)
        try:
            with psycopg.connect(pg17, autocommit=True) as c:
                return c.execute(sql, params).fetchall()
        except Exception:
            return None
    monkeypatch.setattr("open_proxy_mcp.db.pg_rows", fake_pg_rows)
    return calls


def test_read_quote_latest_day_with_change_in_one_round_trip(pg17, quote_db):
    _write(pg17, "20261015", [_row("005930", 100_000, shrs=10)], [])
    _write(pg17, "20261016", [_row("005930", 103_000, o=101_000, h=104_000, l=100_500, vol=777, val=99_999, shrs=10)], [])
    got = krx_daily.read_quote("005930", "")
    assert len(quote_db) == 1                                    # 왕복 한 번
    assert got.ok and got.day == "20261016" and got.day_present and got.window == ("20261015", "20261016")
    r = got.row
    assert (r["open"], r["high"], r["low"], r["close"], r["volume"], r["value"], r["list_shrs"], r["mktcap"]) == \
        (101_000, 104_000, 100_500, 103_000, 777, 99_999, 10, 1_030_000)
    assert (r["prev_close"], r["chg"], float(r["chg_pct"]), r["market"]) == (100_000, 3_000, 3.0, "KS")


def test_read_quote_specific_day_missing_ticker_and_absent_day(pg17, quote_db):
    _write(pg17, "20261015", [_row("005930", 100_000)], [])
    _write(pg17, "20261016", [_row("005930", 103_000), _row("000660", 90_000)], [])
    hit = krx_daily.read_quote("005930", "20261015")
    assert hit.row["close"] == 100_000 and hit.row["prev_close"] is None            # 창의 첫날 — 직전 행 없음
    no_ticker = krx_daily.read_quote("000660", "20261015")                          # 그 날은 있는데 이 종목이 없다
    assert no_ticker.ok and no_ticker.day_present and no_ticker.row is None
    absent = krx_daily.read_quote("005930", "20261012")                             # 창 안이지만 보관된 적 없는 날(휴장·구멍)
    assert absent.ok and not absent.day_present and absent.row is None and absent.window == ("20261015", "20261016")
    old = krx_daily.read_quote("005930", "20260101")                                # 창 밖
    assert old.ok and not old.day_present and old.window[0] == "20261015"


def test_read_quote_on_empty_table_and_when_table_is_missing(pg17, quote_db):
    empty = krx_daily.read_quote("005930", "")
    assert empty.ok and empty.day is None and not empty.day_present and empty.row is None
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute("DROP VIEW price_latest; DROP VIEW krx_daily_v; DROP TABLE krx_daily")
    assert krx_daily.read_quote("005930", "").ok is False                           # 읽을 수 없음 → 호출자는 KRX 라이브로


def test_quote_query_plan_uses_the_ticker_index_for_large_tables(pg17):
    """종목 조건이 뷰(LAG 창) 안으로 밀려 들어가 (ticker, trd_dd) 색인을 쓰는지 — 7일치 약 2만 행에서도 전체 창 계산을 하지 않는다."""
    _apply(pg17)
    with psycopg.connect(pg17) as c:
        c.execute("""INSERT INTO krx_daily SELECT to_char(date '2026-10-05' + d, 'YYYYMMDD'), lpad(t::text, 6, '0'), 'KS',
                     1000, 1100, 900, 1000 + t % 50, 100, 100000, 1000000
                     FROM generate_series(0, 6) d, generate_series(1, 2800) t""")
        c.execute("ANALYZE krx_daily")
        plan = "\n".join(r[0] for r in c.execute("EXPLAIN (COSTS OFF) " + krx_daily.QUOTE_SQL, ("20261010", "000777")).fetchall())
    assert "Seq Scan on krx_daily d" not in plan, plan          # 뷰의 창 계산이 전 종목을 훑지 않는다
    assert "idx_krx_daily_isu" in plan or "krx_daily_pkey" in plan, plan


# ── 갱신기 전체 흐름: 진짜 세션 잠금 + 진짜 쓰기 ───────────────────────────────────

@pytest.fixture
def refresher_env(pg17, monkeypatch):
    """갱신기의 DB 입출력(상태 읽기·잠금·쓰기)을 로컬 컨테이너로 돌리고, KRX 만 가짜로 둔다."""
    from open_proxy_mcp.services import krx_refresher as R
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(KRX_WEEKLY_DDL)
    _apply(pg17)
    monkeypatch.setenv("DATABASE_URL", pg17)

    def pg_rows(sql, params=()):
        try:
            with psycopg.connect(pg17, autocommit=True) as c:
                return c.execute(sql, params).fetchall()
        except Exception:
            return None
    monkeypatch.setattr("open_proxy_mcp.db.pg_rows", pg_rows)
    fetches = []

    async def fake_fetch(basDd):
        import asyncio
        fetches.append(basDd)
        await asyncio.sleep(0.2)                       # 다른 머신이 끼어들 시간
        return ([_row(f"K{i:05d}", 1000 + i) for i in range(400)], [_row(f"Q{i:05d}", 2000 + i) for i in range(400)], 2)
    monkeypatch.setattr(R, "_fetch_day", fake_fetch)
    R._LOGGED_FRESH.clear()
    return R, fetches


def _monday():
    from datetime import datetime
    from open_proxy_mcp.clock import KST
    return datetime(2026, 10, 19, 8, 0, tzinfo=KST)         # 월요일 아침 → 기대 거래일 = 직전 금요일 20261016


def test_two_refreshers_race_on_the_real_advisory_lock_and_exactly_one_writes(pg17, refresher_env):
    import asyncio
    R, fetches = refresher_env

    async def both():
        return await asyncio.gather(R.run_cycle(_monday()), R.run_cycle(_monday()))
    a, b = asyncio.run(both())
    assert sorted([a.outcome, b.outcome]) in (["lock_busy", "refreshed"], ["fresh", "refreshed"]), (a, b)
    assert fetches == ["20261016"]                            # KRX 는 한 번만(2콜 몫)
    with psycopg.connect(pg17) as c:
        assert c.execute("SELECT count(*), count(DISTINCT trd_dd), min(trd_dd) FROM krx_daily").fetchone() == (800, 1, "20261016")
        assert c.execute("SELECT count(*), min(price_dd) FROM krx_weekly").fetchone() == (800, "20261016")   # 같은 커밋에 둘 다
        # 일이 끝나면 잠금이 풀려 있다 — 다른 세션이 바로 잡는다
        assert c.execute("SELECT pg_try_advisory_lock(%s)", (krx_daily.REFRESH_LOCK_KEY,)).fetchone()[0] is True
    # 같은 날 다음 틱: 저장돼 있으니 KRX 0콜(잠금도 안 잡는다)
    assert asyncio.run(R.run_cycle(_monday())).outcome == "fresh" and fetches == ["20261016"]


def test_refresher_yields_while_another_session_holds_the_lock_then_proceeds_once_it_is_released(pg17, refresher_env):
    import asyncio
    R, fetches = refresher_env
    other = psycopg.connect(pg17, autocommit=True)          # 「다른 머신」의 세션
    try:
        assert other.execute("SELECT pg_try_advisory_lock(%s)", (krx_daily.REFRESH_LOCK_KEY,)).fetchone()[0] is True
        res = asyncio.run(R.run_cycle(_monday()))
        assert res.outcome == "lock_busy" and fetches == []   # 못 잡으면 KRX 도 DB 쓰기도 없다
        other.execute("SELECT pg_advisory_unlock(%s)", (krx_daily.REFRESH_LOCK_KEY,))
        assert asyncio.run(R.run_cycle(_monday())).outcome == "refreshed" and fetches == ["20261016"]
    finally:
        other.close()


def test_a_crashed_holder_releases_the_lock_when_its_connection_dies(pg17):
    """잠금 보유자가 죽으면(연결이 끊기면) 잠금이 저절로 풀린다 — 영영 못 잡는 일이 없다."""
    a = psycopg.connect(pg17, autocommit=True)
    assert a.execute("SELECT pg_try_advisory_lock(%s)", (krx_daily.REFRESH_LOCK_KEY,)).fetchone()[0] is True
    b = psycopg.connect(pg17, autocommit=True)
    assert b.execute("SELECT pg_try_advisory_lock(%s)", (krx_daily.REFRESH_LOCK_KEY,)).fetchone()[0] is False
    a.close()                                              # 해제 호출 없이 연결만 닫는다
    assert b.execute("SELECT pg_try_advisory_lock(%s)", (krx_daily.REFRESH_LOCK_KEY,)).fetchone()[0] is True
    b.close()


def test_seven_trading_days_of_refresher_cycles_roll_the_window(pg17, refresher_env):
    """평일 8일치 아침 틱을 실제 쓰기까지 돌려 7거래일 창이 굴러가는지(8일째에 첫날이 빠지는지) 끝에서 끝까지 본다."""
    import asyncio
    from datetime import datetime
    from open_proxy_mcp.clock import KST
    R, fetches = refresher_env
    mornings = [datetime(2026, 10, d, 8, 0, tzinfo=KST) for d in (13, 14, 15, 16, 19, 20, 21, 22)]    # 연속 평일 8일(월요일 아침의 기대일은 금요일)
    for now in mornings:
        res = asyncio.run(R.run_cycle(now))
        assert res.outcome == "refreshed", (now, res)
    assert fetches == ["20261012", "20261013", "20261014", "20261015", "20261016", "20261019", "20261020", "20261021"]
    with psycopg.connect(pg17) as c:
        days = [r[0] for r in c.execute("SELECT DISTINCT trd_dd FROM krx_daily ORDER BY 1").fetchall()]
        weekly = [r[0] for r in c.execute("SELECT DISTINCT price_dd FROM krx_weekly ORDER BY 1").fetchall()]
    assert days == fetches[1:]                                # 8일치를 썼지만 가장 오래된 날이 빠져 7일
    assert weekly == ["20261016", "20261021"]                 # krx_weekly 는 지금과 같다 — 지난주 마지막 거래일 + 이번 주 최신 하루


def test_server_startup_runs_one_refresh_cycle_and_shutdown_is_clean(pg17, refresher_env, monkeypatch):
    """`build_app()` 이 만든 실제 앱을 띄우면(refresher 켬) 기동 직후 한 번 확인해 DB 를 채우고, 종료 때 작업이 깨끗이 멈춘다."""
    import time
    from starlette.testclient import TestClient
    from open_proxy_mcp.server import build_app
    R, fetches = refresher_env
    monkeypatch.setenv(R.ENV_FLAG, "1")
    monkeypatch.setenv("KRX_API_KEY", "test-key")
    monkeypatch.setattr(R, "STARTUP_DELAY_SEC", 0.0)
    task_ref = {}
    real_start = R.start

    def spy_start():
        h = real_start()
        task_ref["h"] = h
        return h
    monkeypatch.setattr(R, "start", spy_start)
    with TestClient(build_app()):
        deadline = time.time() + 15
        while time.time() < deadline:
            with psycopg.connect(pg17) as c:
                n = c.execute("SELECT count(*) FROM krx_daily").fetchone()[0]
            if n:
                break
            time.sleep(0.1)
        assert n == 800 and len(fetches) == 1                  # 기동 직후 한 번: 기대 거래일 하나를 받아 두 표에 썼다
        assert task_ref["h"] is not None and not task_ref["h"].task.done()   # 상시 작업이 돌고 있다
    assert task_ref["h"].task.done()                           # 서버가 꺼지면 작업도 멈춘다
    with psycopg.connect(pg17) as c:                           # 잠금도 남지 않았다
        assert c.execute("SELECT pg_try_advisory_lock(%s)", (krx_daily.REFRESH_LOCK_KEY,)).fetchone()[0] is True
