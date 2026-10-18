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
        # 서버가 쓰는 역할과 읽기 전용 역할은 그대로 읽는다 — opm_ro 는 이 테스트의 기본 권한에 없으므로 DDL 의 GRANT 덕분이다
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
        rows = {(t, d): (m, pdd, p, ch, pc) for t, d, m, pdd, p, ch, pc in c.execute(
            "SELECT ticker, trd_dd, mktcap, prev_dd, prev_close, chg, chg_pct FROM krx_daily_v").fetchall()}
    # 시총 = 종가 × 상장주식수
    assert rows[("A00001", "20261014")][0] == 10_000
    # 보관 창의 첫날은 직전 행이 없어 비어 있다
    assert rows[("A00001", "20261014")][1:] == (None, None, None, None)
    assert rows[("A00002", "20261014")][1:] == (None, None, None, None)
    # 직전 보관 행 대비 (소수 2자리) — 직전 행의 날짜(prev_dd)도 함께 낸다
    assert rows[("A00001", "20261015")][1:] == ("20261014", 1000, 100, pytest.approx(10.00))
    assert rows[("A00001", "20261016")][1:] == ("20261015", 1100, -110, pytest.approx(-10.00))
    # 종목마다 자기 직전 행을 본다 — A00002 의 직전은 15일이 아니라 14일(prev_dd 가 이틀 전을 가리켜 「직전 거래일이 아님」을 알린다)
    assert rows[("A00002", "20261016")][1:] == ("20261014", 200, 10, pytest.approx(5.00))


def test_view_change_is_null_not_error_when_previous_close_is_zero_or_null(pg17):
    _apply(pg17)
    _write(pg17, "20261015", [_row("Z00001", 0), _row("Z00002", 100)], [])
    _write(pg17, "20261016", [_row("Z00001", 500), {**_row("Z00002", 0), "TDD_CLSPRC": "-"}], [])
    with psycopg.connect(pg17) as c:
        got = {t: (pc, ch, cp) for t, pc, ch, cp in c.execute(
            "SELECT ticker, prev_close, chg, chg_pct FROM krx_daily_v WHERE trd_dd='20261016'").fetchall()}
    assert got["Z00001"] == (0, None, None)           # 직전 종가 0 → 대비·등락률 모두 NULL(종가가 그대로 「대비」로 새지 않는다)
    assert got["Z00002"] == (100, None, None)         # 종가 '-' → NULL


def test_price_latest_is_one_row_per_ticker_at_its_own_latest_day(pg17):
    _apply(pg17)
    _write(pg17, "20261014", [_row("L00001", 10), _row("L00002", 20)], [])
    _write(pg17, "20261015", [_row("L00001", 11)], [])                                    # L00002 는 15일 이후 없음(상폐 등)
    _write(pg17, "20261016", [_row("L00001", 12), _row("L00003", 30)], [])
    with psycopg.connect(pg17) as c:
        got = c.execute("SELECT ticker, trd_dd, price_dd, close FROM price_latest ORDER BY ticker").fetchall()
        assert [r[0] for r in c.execute("SELECT column_name FROM information_schema.columns WHERE table_name='price_latest' "
                                        "AND column_name IN ('price_dd','prev_dd','chg_pct','mktcap') ORDER BY 1").fetchall()] == \
            ["chg_pct", "mktcap", "prev_dd", "price_dd"]
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


def test_upsert_writes_daily_first_and_copies_only_closed_weeks_to_weekly(pg17, monkeypatch):
    """`_krx_db_upsert`(261018 재설계 5-1 쓰는 쪽): krx_daily 가 주(主)다 — 그 표가 없으면 아무것도 커밋하지 않는다.
    krx_weekly 에는 이번 주 행을 쓰지 않고, 금요일이 저장되는 순간 그 주를 그 날 단면으로 한 번 옮겨 적는다(시총 = 종가 × 주식수)."""
    from open_proxy_mcp.services import price_multiple_data as pmd
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(KRX_WEEKLY_DDL)
    monkeypatch.setenv("DATABASE_URL", pg17)

    # ① 일별 표가 없을 때 — 실패로 알리고 krx_weekly 에도 아무것도 남기지 않는다(서빙 기준일과 주간 표가 어긋나지 않게)
    assert pmd._krx_db_upsert("20261013", [_row("005930", 70_000)], [_row("247540", 250_000)]) == pmd.KrxWrite(False)
    with psycopg.connect(pg17) as c:
        assert c.execute("SELECT count(*) FROM krx_weekly").fetchone()[0] == 0

    # ② 마이그레이션 적용 뒤 — 주중(화·목)은 일별만 쓴다. 이번 주 행은 krx_weekly 에 없다
    _apply(pg17)
    assert pmd._krx_db_upsert("20261013", [_row("005930", 70_000)], [_row("247540", 250_000)]) == pmd.KrxWrite(True, True, ())
    assert pmd._krx_db_upsert("20261015", [_row("005930", 71_000)], [_row("247540", 251_000)]) == pmd.KrxWrite(True, True, ())
    with psycopg.connect(pg17) as c:
        assert c.execute("SELECT count(*) FROM krx_weekly").fetchone()[0] == 0
        assert c.execute("SELECT trd_dd, ticker, close, volume, value FROM krx_daily WHERE trd_dd='20261015' ORDER BY ticker").fetchall() == [
            ("20261015", "005930", 71_000, 1000, 5_000_000), ("20261015", "247540", 251_000, 1000, 5_000_000)]

    # ③ 금요일 — 그 주를 닫는다(R1)
    assert pmd._krx_db_upsert("20261016", [_row("005930", 72_000)], [_row("247540", 252_000)]) == \
        pmd.KrxWrite(True, True, ("20261016",))
    with psycopg.connect(pg17) as c:
        assert c.execute("SELECT price_dd, ticker, market, close, mktcap, list_shrs FROM krx_weekly ORDER BY ticker").fetchall() == [
            ("20261016", "005930", "KS", 72_000, 72_000 * 1_000_000, 1_000_000),
            ("20261016", "247540", "KQ", 252_000, 252_000 * 1_000_000, 1_000_000)]


def test_upsert_failure_in_daily_commits_nothing(pg17, monkeypatch):
    """krx_daily 쪽 SQL 이 실패하면(열이 모자란 표) 주간 닫기도 하지 않고 아무것도 커밋하지 않는다 — 실패는 반환값·로그로 드러난다."""
    from open_proxy_mcp.services import price_multiple_data as pmd
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(KRX_WEEKLY_DDL)
        c.execute("CREATE TABLE krx_daily (trd_dd text NOT NULL, ticker text NOT NULL, PRIMARY KEY (trd_dd, ticker))")  # 열이 모자란 가짜 표
    monkeypatch.setenv("DATABASE_URL", pg17)
    assert pmd._krx_db_upsert("20261016", [_row("005930", 71_000)], [_row("247540", 251_000)]) == pmd.KrxWrite(False)
    with psycopg.connect(pg17) as c:
        assert c.execute("SELECT count(*) FROM krx_weekly").fetchone()[0] == 0
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
    assert (r["prev_dd"], r["prev_close"], r["chg"], float(r["chg_pct"]), r["market"]) == ("20261015", 100_000, 3_000, 3.0, "KS")


def test_read_quote_specific_day_missing_ticker_and_absent_day(pg17, quote_db):
    _write(pg17, "20261015", [_row("005930", 100_000)], [])
    _write(pg17, "20261016", [_row("005930", 103_000), _row("000660", 90_000)], [])
    hit = krx_daily.read_quote("005930", "20261015")
    assert hit.row["close"] == 100_000 and hit.row["prev_close"] is None and hit.row["prev_dd"] is None   # 창의 첫날 — 직전 행 없음
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
        assert c.execute("SELECT count(*), min(price_dd) FROM krx_weekly").fetchone() == (800, "20261016")   # 금요일이라 같은 커밋에 주간도 닫힌다
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
    import time
    deadline, got = time.time() + 5, False
    while not got and time.time() < deadline:              # 서버가 끊긴 연결의 백엔드를 정리하는 것은 비동기다 — 잠깐 기다린다
        got = b.execute("SELECT pg_try_advisory_lock(%s)", (krx_daily.REFRESH_LOCK_KEY,)).fetchone()[0]
        if not got:
            time.sleep(0.05)
    assert got is True
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
    assert weekly == ["20261016"]                             # krx_weekly 는 닫힌 주만 — 지난주 금요일. 이번 주(19~21)는 일별에만 있다


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


def test_quote_payload_end_to_end_on_postgres_is_json_serializable_and_matches_live_shape(pg17, quote_db, monkeypatch):
    """진짜 뷰가 돌려준 값(Decimal·bigint)이 `build_quote_payload` 를 거쳐 JSON 으로 나가고, 라이브 경로와 같은 모양인지."""
    import asyncio
    import json
    from open_proxy_mcp.services import trading as T
    _write(pg17, "20261015", [_row("005930", 70_000, o=69_000, h=70_500, l=68_500, vol=1_000, val=70_000_000, shrs=5_919_637_922)], [])
    _write(pg17, "20261016", [_row("005930", 71_000, o=70_500, h=71_500, l=70_000, vol=12_345_678, val=876_543_210_000, shrs=5_919_637_922)], [])

    async def fake_resolve(q):
        return {"corp_name": "삼성전자", "stock_code": "005930", "corp_cls": "Y"}, None
    monkeypatch.setattr(T, "_resolve_listed", fake_resolve)

    async def no_krx(*a, **k):
        raise AssertionError("7거래일 안은 KRX 를 부르면 안 된다")
    monkeypatch.setattr(T, "_krx_quote_row", no_krx)

    p = asyncio.run(T.build_quote_payload("삼성전자", as_of=""))
    json.dumps(p, ensure_ascii=False)                                   # Decimal 이 새면 여기서 터진다
    q = p["data"]["quote"]
    assert p["data"]["as_of"] == "20261016" and p["data"]["source"] == "db"
    assert q == {"close_krw": 71_000, "open_krw": 70_500, "high_krw": 71_500, "low_krw": 70_000, "change_krw": 1_000,
                 "volume": 12_345_678, "value_krw": 876_543_210_000, "mktcap_krw": 71_000 * 5_919_637_922,
                 "list_shrs": 5_919_637_922, "change_pct": 1.43}
    live_q, _ = T._quote_from_krx_row({"TDD_CLSPRC": "71,000", "TDD_OPNPRC": "70,500", "TDD_HGPRC": "71,500", "TDD_LWPRC": "70,000",
                                       "CMPPREVDD_PRC": "1,000", "ACC_TRDVOL": "12,345,678", "ACC_TRDVAL": "876,543,210,000",
                                       "MKTCAP": str(71_000 * 5_919_637_922), "LIST_SHRS": "5,919,637,922",
                                       "FLUC_RT": "1.43"})
    assert live_q == q                                                    # 같은 하루를 KRX 원본으로 읽은 값과 한 칸도 다르지 않다

    # 보관 창의 첫날(20261015)은 직전 행이 없어 DB 만으로는 등락률을 못 낸다 → KRX 값으로 채운다(여기선 KRX 없음 → 값만 DB, 등락률 비움)
    async def none(dd, t):
        return None
    monkeypatch.setattr(T, "_krx_quote_row", none)
    first = asyncio.run(T.build_quote_payload("삼성전자", as_of="20261015"))
    json.dumps(first, ensure_ascii=False)
    assert first["data"]["quote"]["close_krw"] == 70_000 and first["data"]["quote"]["change_pct"] is None
    assert any("등락률" in w for w in first["warnings"])


def test_missing_migration_degrades_quotes_to_live_without_taking_the_shared_pool_down(pg17, monkeypatch):
    """마이그레이션 전에 배포돼도(`krx_daily_v` 없음) quote 는 라이브로 답하고, 그 SQL 오류가 공용 풀을 내려 다른 질의를 느리게 만들지 않는다."""
    import importlib
    import open_proxy_mcp.db as dbm
    dbm = importlib.reload(dbm)                      # 싱글턴 풀을 새로 — 앞선 테스트의 풀 상태를 물려받지 않는다
    monkeypatch.setenv("DATABASE_URL", pg17)
    try:
        for _ in range(3):
            assert krx_daily.read_quote("005930", "").ok is False        # 뷰가 없다 → 읽을 수 없음(호출자는 KRX 라이브로)
        assert dbm._pool is not None and dbm._pool_disabled_until == 0.0, "SQL 오류가 풀을 내렸다"
        assert dbm.pg_rows("SELECT 1") == [(1,)]                          # 같은 풀로 다른 질의는 정상
        assert dbm.pool_stats()["enabled"] is True
    finally:
        if dbm._pool is not None:
            dbm._pool.close()
        importlib.reload(dbm)


def test_opm_ro_reads_only_because_the_ddl_grants_it(pg17):
    """픽스처의 기본 권한에는 opm_ro 가 없다 — DDL 에서 GRANT 를 빼면 읽기 전용 역할이 막힌다(권한 테스트가 헛돌지 않는다는 대조)."""
    ddl = _ddl()
    without_grant = ddl.STATEMENTS.replace("GRANT SELECT ON krx_daily, krx_daily_v, price_latest TO opm_ro;\n", "")
    assert without_grant != ddl.STATEMENTS
    with psycopg.connect(pg17) as c:
        c.execute(without_grant)
        c.commit()
        assert c.execute("SELECT has_table_privilege('opm_ro','krx_daily','SELECT')").fetchone()[0] is False
    _apply(pg17)
    with psycopg.connect(pg17) as c:
        assert c.execute("SELECT has_table_privilege('opm_ro','krx_daily','SELECT')").fetchone()[0] is True
        assert c.execute("SELECT has_table_privilege('opm_ro','price_latest','SELECT')").fetchone()[0] is True


def _quote_with(pg17, monkeypatch, rows_by_day, as_of="", krx_row=None):
    """주어진 일별 행으로 진짜 뷰를 채우고 `build_quote_payload` 를 돌린다. KRX 라이브는 `krx_row`(없으면 None)를 돌려준다."""
    import asyncio
    from open_proxy_mcp.services import trading as T
    for dd, rows in rows_by_day.items():
        _write(pg17, dd, rows, [])

    async def fake_resolve(q):
        return {"corp_name": "삼성전자", "stock_code": "005930", "corp_cls": "Y"}, None
    monkeypatch.setattr(T, "_resolve_listed", fake_resolve)
    live_calls = []

    async def live(dd, ticker):
        live_calls.append(dd)
        return krx_row
    monkeypatch.setattr(T, "_krx_quote_row", live)
    return asyncio.run(T.build_quote_payload("삼성전자", as_of=as_of)), live_calls


def test_a_missed_trading_day_is_not_reported_as_a_daily_change(pg17, quote_db, monkeypatch):
    """KRX 가 한 날을 0행으로 줘서 빠졌다면(10/13 없음) 10/14 의 「전일 대비」는 이틀 변동이다 — 조용히 20.00% 로 내지 않고 KRX 공표값으로 확인한다."""
    krx = {"ISU_CD": "005930", "MKT_NM": "KOSPI", "TDD_CLSPRC": "120,000", "TDD_OPNPRC": "118,000", "TDD_HGPRC": "121,000",
           "TDD_LWPRC": "117,000", "CMPPREVDD_PRC": "2,000", "ACC_TRDVOL": "1,000", "ACC_TRDVAL": "120,000,000",
           "MKTCAP": "1", "LIST_SHRS": "1,000,000", "FLUC_RT": "1.69"}
    p, live = _quote_with(pg17, monkeypatch, {"20261012": [_row("005930", 100_000)], "20261014": [_row("005930", 120_000)]},
                          as_of="20261014", krx_row=krx)
    assert live == ["20261014"] and p["data"]["source"] == "krx_live"
    assert p["data"]["quote"]["change_pct"] == 1.69                       # KRX 공표값, DB 의 이틀치 20.0 이 아니다


def test_a_missed_day_without_krx_blanks_the_change_instead_of_guessing(pg17, quote_db, monkeypatch):
    p, live = _quote_with(pg17, monkeypatch, {"20261012": [_row("005930", 100_000)], "20261014": [_row("005930", 120_000)]},
                          as_of="20261014", krx_row=None)
    q = p["data"]["quote"]
    assert p["status"] == "ok" and p["data"]["source"] == "db" and q["close_krw"] == 120_000
    assert q["change_pct"] is None and q["change_krw"] is None            # 이틀치 20.0% 를 하루 변동처럼 내지 않는다
    assert any("대비·등락률은 비웠습니다" in w for w in p["warnings"])


def test_a_stock_split_day_is_taken_from_krx_not_from_the_stored_previous_close(pg17, quote_db, monkeypatch):
    """5:1 액면분할(500,000 → 101,000): 직전 행 대비는 −79.8% 지만 KRX 공표는 기준가 대비 +1.00% — ±30% 를 넘으면 DB 값을 쓰지 않는다."""
    krx = {"ISU_CD": "005930", "MKT_NM": "KOSPI", "TDD_CLSPRC": "101,000", "TDD_OPNPRC": "100,000", "TDD_HGPRC": "102,000",
           "TDD_LWPRC": "99,000", "CMPPREVDD_PRC": "1,000", "ACC_TRDVOL": "5,000", "ACC_TRDVAL": "505,000,000",
           "MKTCAP": "1", "LIST_SHRS": "5,000,000", "FLUC_RT": "1.00"}
    rows = {"20261015": [_row("005930", 500_000, shrs=1_000_000)], "20261016": [_row("005930", 101_000, shrs=5_000_000)]}
    p, live = _quote_with(pg17, monkeypatch, rows, as_of="20261016", krx_row=krx)
    assert live == ["20261016"] and p["data"]["quote"]["change_pct"] == 1.0
    p2, live2 = _quote_with(pg17, monkeypatch, {}, as_of="20261016", krx_row=None)       # KRX 가 없으면 −79.8 을 내지 않고 비운다
    assert p2["data"]["quote"]["change_pct"] is None and p2["data"]["quote"]["close_krw"] == 101_000


def test_a_normal_day_after_a_weekend_uses_the_stored_change_without_krx(pg17, quote_db, monkeypatch):
    """금요일 → 월요일은 정상 거래일 간격이다 — DB 값을 믿고 KRX 를 부르지 않는다."""
    p, live = _quote_with(pg17, monkeypatch, {"20261016": [_row("005930", 100_000)], "20261019": [_row("005930", 103_000)]}, as_of="20261019")
    assert live == [] and p["data"]["source"] == "db" and p["data"]["quote"]["change_pct"] == 3.0


def test_a_zero_previous_close_does_not_leak_the_price_as_the_change(pg17, quote_db, monkeypatch):
    p, live = _quote_with(pg17, monkeypatch, {"20261015": [_row("005930", 0)], "20261016": [_row("005930", 500)]}, as_of="20261016")
    q = p["data"]["quote"]
    assert q["change_krw"] is None and q["change_pct"] is None and q["close_krw"] == 500
    assert live == ["20261016"]
