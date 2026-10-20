"""krx_daily — Postgres 17 에서만 검증되는 부분(뷰·권한·트랜잭션·한 왕복 조회).

로컬 Docker 의 `postgres:17` 이 필요하다(`tests/conftest.py` 의 `pg17`). Docker 가 없으면 이 파일 전체가 건너뛰어진다 —
같은 로직 중 SQLite 로 되는 부분은 `test_krx_daily.py` 가 따로 검사한다.
"""
from __future__ import annotations

import importlib.util
import pathlib
import sys
from datetime import date

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


#: 옛(261018) 일별 표 — 교체 리허설용.
OLD_KRX_DAILY_DDL = """CREATE TABLE krx_daily (trd_dd text NOT NULL, ticker text NOT NULL, market text, open bigint, high bigint,
  low bigint, close bigint, volume bigint, value bigint, list_shrs bigint, PRIMARY KEY (trd_dd, ticker));
CREATE INDEX idx_krx_daily_isu ON krx_daily (ticker, trd_dd);
ALTER TABLE krx_daily ENABLE ROW LEVEL SECURITY;
CREATE POLICY krx_daily_read ON krx_daily FOR SELECT USING (true);
CREATE VIEW krx_daily_v WITH (security_invoker = true) AS
SELECT d.trd_dd, d.ticker, d.market, d.open, d.high, d.low, d.close, d.volume, d.value, d.list_shrs,
       d.close * d.list_shrs AS mktcap, lag(d.trd_dd) OVER w AS prev_dd, lag(d.close) OVER w AS prev_close,
       d.close - NULLIF(lag(d.close) OVER w, 0) AS chg,
       round((d.close - lag(d.close) OVER w)::numeric * 100 / NULLIF(lag(d.close) OVER w, 0), 2) AS chg_pct
FROM krx_daily d WINDOW w AS (PARTITION BY d.ticker ORDER BY d.trd_dd);
CREATE VIEW price_latest WITH (security_invoker = true) AS
SELECT DISTINCT ON (v.ticker) v.trd_dd, v.trd_dd AS price_dd, v.ticker, v.market, v.open, v.high, v.low, v.close,
       v.volume, v.value, v.list_shrs, v.mktcap, v.prev_dd, v.prev_close, v.chg, v.chg_pct
FROM krx_daily_v v ORDER BY v.ticker, v.trd_dd DESC;"""

KRX_WEEKLY_DDL = """CREATE TABLE IF NOT EXISTS krx_weekly (
  price_dd text NOT NULL, ticker text NOT NULL, market text,
  close bigint, mktcap bigint, list_shrs bigint,
  PRIMARY KEY (price_dd, ticker));"""


def _row(code, close, *, o=None, h=None, l=None, vol=1000, val=5_000_000, shrs=1_000_000, cmp=None):
    """KRX 응답 한 행. cmp = 공표 전일대비(CMPPREVDD_PRC) — 주지 않으면 칸이 없다(공표값이 보관되지 않은 행, 예: 교체 때 옮겨 온 행)."""
    f = lambda n: None if n is None else f"{n:,}"   # noqa: E731 — KRX 는 쉼표 문자열로 준다
    r = {"ISU_CD": code, "TDD_CLSPRC": f(close), "TDD_OPNPRC": f(o if o is not None else close),
         "TDD_HGPRC": f(h if h is not None else close), "TDD_LWPRC": f(l if l is not None else close),
         "ACC_TRDVOL": f(vol), "ACC_TRDVAL": f(val), "LIST_SHRS": f(shrs), "MKTCAP": f(close * shrs),
         "MKT_NM": "KOSPI", "FLUC_RT": "0.00"}
    if cmp is not None:
        r["CMPPREVDD_PRC"] = f(cmp)
    return r


def _apply(url):
    _ddl().apply(url)


def put_daily(c, rows):
    """시험용 — 옛 표 모양의 행 (trd_dd, ticker, market KS/KQ, close, list_shrs[, open, high, low, volume, value, cmp]) 을 새 표
    (번호표·달력·사실값)에 넣는다(`INSERT INTO krx_daily …` 대신 — 이제 그 이름은 뷰다). 달력의 행 수·주 닫는 날도 쓰는 쪽처럼 맞춘다.
    `c` 는 psycopg 연결(트랜잭션 또는 autocommit)."""
    rows = [tuple(r) + (None,) * (11 - len(r)) for r in rows]
    tickers = sorted({r[1] for r in rows})
    c.execute(krx_daily.NEW_TICKERS_SQL, (tickers,))
    tids = dict(c.execute(krx_daily.TIDS_SQL, (tickers,)).fetchall())
    for d in sorted({r[0] for r in rows}):
        c.execute("INSERT INTO krx_day (trd_dd, dd, n) VALUES (%s, %s, 0) ON CONFLICT (trd_dd) DO NOTHING",
                  (date(int(d[:4]), int(d[4:6]), int(d[6:])), d))
    for d in sorted({r[0] for r in rows}):
        px = [(val, vol, shrs, o, h, lo, close, cmp, tids[t], {"KS": "S", "KQ": "Q"}[m])
              for dd, t, m, close, shrs, o, h, lo, vol, val, cmp in rows if dd == d]
        c.execute(krx_daily.INSERT_PX_SQL, (date(int(d[:4]), int(d[4:6]), int(d[6:])), *(list(x) for x in zip(*px))))
    c.execute("UPDATE krx_day d SET n = (SELECT count(*) FROM krx_px p WHERE p.trd_dd = d.trd_dd)")
    c.execute(krx_daily.WK_CLOSE_SQL)


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
                              "('krx_px','krx_day','krx_ticker','krx_daily','krx_daily_v','price_latest','krx_px_dd_brin','krx_px_pkey')").fetchall())
        assert rels == {"krx_px": "r", "krx_day": "r", "krx_ticker": "r", "krx_daily": "v", "krx_daily_v": "v", "price_latest": "v",
                        "krx_px_dd_brin": "i", "krx_px_pkey": "i"}
        pk = c.execute("SELECT string_agg(a.attname, ',' ORDER BY k.ord) FROM pg_index i "
                       "JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(attnum, ord) ON true "
                       "JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum "
                       "WHERE i.indrelid='krx_px'::regclass AND i.indisprimary").fetchone()[0]
        assert pk == "tid,trd_dd"
        assert "USING brin (trd_dd)" in c.execute("SELECT pg_get_indexdef('krx_px_dd_brin'::regclass)").fetchone()[0]
        types = c.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name='krx_px' "
                          "ORDER BY ordinal_position").fetchall()
        assert types == [("value", "bigint"), ("volume", "bigint"), ("list_shrs", "bigint"), ("open", "integer"), ("high", "integer"),
                         ("low", "integer"), ("close", "integer"), ("cmp", "integer"), ("trd_dd", "date"), ("tid", "integer"),
                         ("market", '"char"')]
        assert [r[0] for r in c.execute("SELECT column_name FROM information_schema.columns WHERE table_name='krx_day' "
                                        "ORDER BY ordinal_position").fetchall()] == ["trd_dd", "dd", "n", "closed", "wk_close", "cold"]
        # 옛 표와 같은 칸 이름·순서·형(호환 뷰) — 읽는 쪽은 그대로 읽는다
        cols = c.execute("SELECT column_name, data_type FROM information_schema.columns "
                         "WHERE table_name='krx_daily' ORDER BY ordinal_position").fetchall()
        assert cols == [("trd_dd", "text"), ("ticker", "text"), ("market", "text"), ("open", "bigint"), ("high", "bigint"),
                        ("low", "bigint"), ("close", "bigint"), ("volume", "bigint"), ("value", "bigint"), ("list_shrs", "bigint")]
        vcols = [r[0] for r in c.execute("SELECT column_name FROM information_schema.columns WHERE table_name='krx_daily_v' "
                                         "ORDER BY ordinal_position").fetchall()]
        assert vcols == ["trd_dd", "ticker", "market", "open", "high", "low", "close", "volume", "value", "list_shrs", "mktcap",
                         "prev_dd", "prev_close", "chg", "chg_pct", "krx_chg", "krx_chg_pct"]
        for t in ("krx_px", "krx_day", "krx_ticker"):
            assert c.execute("SELECT relrowsecurity FROM pg_class WHERE relname=%s", (t,)).fetchone()[0] is True
            assert c.execute("SELECT policyname, cmd, qual FROM pg_policies WHERE tablename=%s", (t,)).fetchall() == \
                [(f"{t}_read", "SELECT", "true")]
        opts = dict(c.execute("SELECT relname, reloptions FROM pg_class WHERE relname IN ('krx_daily','krx_daily_v','price_latest')").fetchall())
        assert len(opts) == 3 and all(v == ["security_invoker=true"] for v in opts.values())
        assert "autovacuum_vacuum_scale_factor=0.02" in c.execute("SELECT reloptions FROM pg_class WHERE relname='krx_px'").fetchone()[0]


def test_ddl_full_mode_refuses_an_old_table_and_shadow_then_switch_rehearses_the_production_cutover(pg17):
    """운영 순서 리허설: 옛 표(7거래일) → --shadow(새 표 + 그림자 뷰, 옛 표 그대로) → --switch(옛 표를 krx_daily_legacy 로, 같은 이름 뷰, G2 관문)."""
    ddl = _ddl()
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute("CREATE TABLE krx_reset_sweep_checkpoint (price_dd text PRIMARY KEY, n_stocks int)")
        c.execute("INSERT INTO krx_reset_sweep_checkpoint VALUES ('20261009', 0), ('20261008', 2700)")
        c.execute(OLD_KRX_DAILY_DDL)
        for i, dd in enumerate(("20261007", "20261008", "20261012")):
            c.execute("INSERT INTO krx_daily VALUES (%s,'005930','KS',1,2,0,%s,10,100,1000), (%s,'0001A0','KQ',1,2,0,%s,10,100,50)",
                      (dd, 70_000 + i * 100, dd, 9_000 + i))
    with pytest.raises(SystemExit):
        ddl.apply(pg17, "full")                                         # 옛 표가 있는 DB 에 새 DB 판을 덮지 않는다
    ddl.apply(pg17, "shadow")
    with psycopg.connect(pg17) as c:
        assert c.execute("SELECT relkind FROM pg_class WHERE relname='krx_daily'").fetchone()[0] == "r"
        assert c.execute("SELECT count(*) FROM krx_daily_s").fetchone()[0] == 0
    ddl.apply(pg17, "switch")                                           # 델타(옛 표의 3일) 이관 + 휴장 1일 + 이름 바꾸기 + G2 0
    with psycopg.connect(pg17) as c:
        kinds = dict(c.execute("SELECT relname, relkind FROM pg_class WHERE relname IN "
                               "('krx_daily','krx_daily_legacy','krx_daily_s','price_latest_s')").fetchall())
        assert kinds == {"krx_daily": "v", "krx_daily_legacy": "r"}
        assert c.execute("SELECT * FROM krx_daily ORDER BY 1, 2").fetchall() == \
            c.execute("SELECT * FROM krx_daily_legacy ORDER BY 1, 2").fetchall()
        assert c.execute("SELECT dd, n, closed FROM krx_day ORDER BY 1").fetchall() == [
            ("20261007", 2, False), ("20261008", 2, False), ("20261009", 0, True), ("20261012", 2, False)]
        assert c.execute("SELECT count(*) FROM krx_px WHERE cmp IS NULL").fetchone()[0] == 6     # 옮겨 온 행은 공표 대비가 비어 있다
    with pytest.raises(SystemExit):
        ddl.apply(pg17, "switch")                                       # 두 번 교체하지 않는다


def test_no_grants_for_anon_or_authenticated_even_with_default_privileges(pg17):
    """프로젝트의 기본 권한이 새 표·뷰에 anon/authenticated SELECT 를 자동으로 주므로, DDL 이 그것을 거둔다."""
    # 대조군: 기본 권한만 있는 임의의 새 표는 anon 이 읽는다 (픽스처가 운영과 같은 기본 권한을 깔아 둠)
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute("CREATE TABLE control_t (x int)")
        assert c.execute("SELECT has_table_privilege('anon','control_t','SELECT')").fetchone()[0] is True
    _apply(pg17)
    with psycopg.connect(pg17) as c:
        for obj in ("krx_daily", "krx_daily_v", "price_latest", "krx_px", "krx_day", "krx_ticker"):
            for role in ("anon", "authenticated"):
                for priv in ("SELECT", "INSERT", "UPDATE", "DELETE"):
                    assert c.execute("SELECT has_table_privilege(%s,%s,%s)", (role, obj, priv)).fetchone()[0] is False, \
                        f"{role} 에 {obj} {priv} 권한이 남아 있다"
        # 서버가 쓰는 역할과 읽기 전용 역할은 그대로 읽는다 — opm_ro 는 이 테스트의 기본 권한에 없으므로 DDL 의 GRANT 덕분이다
        assert c.execute("SELECT has_table_privilege('opm_ro','krx_daily','SELECT')").fetchone()[0] is True
        assert c.execute("SELECT has_table_privilege('opm_ro','price_latest','SELECT')").fetchone()[0] is True
        assert c.execute("SELECT has_table_privilege('opm_ro','krx_day','SELECT')").fetchone()[0] is True
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


# ── 쓰기: 모든 필드 · 보관 창 정리 · krx_weekly 와 같은 트랜잭션 ─────────────────────

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


def test_rolling_window_is_one_year_and_only_cold_archived_days_are_deleted(pg17, caplog):
    """보관 창 = 최신 거래일 − 달력 1년 − 1주. 그보다 오래된 날은 휴장일·콜드 보관본에 들어간 날(cold)만 지우고, 나머지는 남긴 채 경고한다."""
    _apply(pg17)
    for d in ("20251001", "20251002", "20251010", "20251013"):
        _write(pg17, d, [_row("005930", 100), _row("000660", 200)], [_row("247540", 300)])
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute("INSERT INTO krx_day (trd_dd, dd, n, closed) VALUES ('2025-10-03', '20251003', 0, true)")   # 개천절 휴장
        c.execute("UPDATE krx_day SET cold = true WHERE dd = '20251001'")                                     # 2025년 보관본에 들어감
    with caplog.at_level("WARNING"):
        ok, n, pruned = _write(pg17, "20261016", [_row("005930", 101)], [])                                  # 창 첫날 20251009
    assert ok and n == 1 and pruned == 3                                     # 20251001(콜드) 3행만 — 20251003 은 행 없는 휴장
    with psycopg.connect(pg17) as c:
        assert [r[0] for r in c.execute("SELECT dd FROM krx_day ORDER BY 1").fetchall()] == ["20251002", "20251010", "20251013", "20261016"]
        assert c.execute("SELECT count(*) FROM krx_daily WHERE trd_dd = '20251002'").fetchone()[0] == 3        # 콜드에 없어 남았다
    assert any("콜드 보관본에 없어 지우지 않았다" in r.message and "20251002" in r.message for r in caplog.records)
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute("UPDATE krx_day SET cold = true WHERE trd_dd < '2026-01-01'")                              # 2025년 보관본을 올린 쪽이 켠다
    caplog.clear()
    with caplog.at_level("WARNING"):
        assert _write(pg17, "20261019", [_row("005930", 102)], [])[2] == 6                                   # 다음 기록 때 창 밖(< 20251012) 둘을 지운다
    assert not any("콜드" in r.message for r in caplog.records)
    with psycopg.connect(pg17) as c:
        assert [r[0] for r in c.execute("SELECT dd FROM krx_day ORDER BY 1").fetchall()] == ["20251013", "20261016", "20261019"]
        assert c.execute("SELECT count(*) FROM krx_px").fetchone()[0] == 3 + 1 + 1                           # 창 안의 날은 콜드여도 남는다


def test_calendar_n_matches_the_rows_and_a_rewrite_replaces_the_whole_day(pg17):
    _apply(pg17)
    _write(pg17, "20261016", [_row("005930", 1), _row("000660", 2)], [_row("247540", 3)])
    _write(pg17, "20261016", [_row("005930", 9)], [_row("247540", 3)])                                    # 정정 재수신 — 그 날을 통째로 바꾼다
    with psycopg.connect(pg17) as c:
        assert c.execute("SELECT n, closed FROM krx_day WHERE dd='20261016'").fetchone() == (2, False)
        assert c.execute("SELECT ticker, close FROM krx_daily ORDER BY 1").fetchall() == [("005930", 9), ("247540", 3)]
        # 번호는 새 종목에만 — 다시 써도 번호 생성기를 태우지 않는다
        assert c.execute("SELECT max(tid), count(*) FROM krx_ticker").fetchone() == (3, 3)
        assert c.execute("SELECT last_value FROM krx_ticker_tid_seq").fetchone()[0] == 3


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
        c.execute("DROP VIEW price_latest, krx_daily_v, krx_daily; DROP TABLE krx_px, krx_day, krx_ticker")
    assert krx_daily.read_quote("005930", "").ok is False                           # 읽을 수 없음 → 호출자는 KRX 라이브로


def _fill_big(c, days=120, tickers=2800):
    """보관 창 크기에 가까운 표 — 거래일 `days` 일 × `tickers` 종목. 날짜순으로 넣는다(쓰는 쪽과 같은 물리 순서)."""
    c.execute("INSERT INTO krx_ticker (ticker) SELECT lpad(t::text, 6, '0') FROM generate_series(1, %s) t", (tickers,))
    c.execute("INSERT INTO krx_day (trd_dd, dd, n) SELECT d, to_char(d, 'YYYYMMDD'), %s FROM "
              "(SELECT d::date FROM generate_series(date '2026-03-02', date '2026-12-31', interval '1 day') d "
              " WHERE extract(isodow FROM d) < 6 LIMIT %s) x", (tickers, days))
    c.execute("INSERT INTO krx_px (value, volume, list_shrs, open, high, low, close, cmp, trd_dd, tid, market) "
              "SELECT 100000, 100, 1000000, 1000, 1100, 900, 1000 + k.tid % 50, 10, d.trd_dd, k.tid, 'S' "
              "FROM krx_day d CROSS JOIN krx_ticker k ORDER BY d.trd_dd, k.tid")
    c.execute("ANALYZE krx_px, krx_day, krx_ticker")


def test_request_path_queries_use_the_calendar_and_the_primary_key_on_a_year_sized_table(pg17):
    """quote(한 왕복)·서빙 기준일·빠진 날 질의가 1년치 사실값 표를 훑지 않는다 — 창·날짜는 달력, 행은 기본키(운영 실측 304 ms → 0.23 ms)."""
    _apply(pg17)
    with psycopg.connect(pg17) as c:
        _fill_big(c)
        c.commit()
        plan = "\n".join(r[0] for r in c.execute("EXPLAIN (COSTS OFF) " + krx_daily.QUOTE_SQL, ("20260610", "000777")).fetchall())
        assert "Seq Scan on krx_px" not in plan, plan
        assert "krx_px_pkey" in plan, plan
        for sql, params in ((krx_daily.LATEST_DAY_SQL, None), (krx_daily.MISSING_SQL, {"exp": "2026-08-20"})):
            p = "\n".join(r[0] for r in c.execute("EXPLAIN (COSTS OFF) " + sql, params).fetchall())
            assert "krx_px" not in p, p                                     # 날짜 질의는 달력(수백 행)만 본다
        # 단면(그 날 전 종목) — 직전 행 칸을 고르지 않으면 부분질의가 돌지 않는다
        p = "\n".join(r[0] for r in c.execute("EXPLAIN (COSTS OFF) SELECT ticker, mktcap FROM krx_daily_v WHERE trd_dd = %s",
                                               ("20260610",)).fetchall())
        assert "SubPlan" not in p and "Seq Scan on krx_px" not in p, p


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

    async def fake_fetch(basDd, both=False):
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


def test_refresher_cycles_keep_every_day_and_fill_a_morning_the_server_missed(pg17, refresher_env):
    """평일 아침 틱을 실제 쓰기까지 돌린다 — 보관 창이 1년이라 8일 동안 지워지는 날이 없고, 서버가 하루 멈췄던 날(10/16 아침 틱 없음 → 10/15 빠짐)은
    다음 아침에 기대 거래일과 함께 메운다. krx_weekly 는 닫힌 주만."""
    import asyncio
    from datetime import datetime
    from open_proxy_mcp.clock import KST
    R, fetches = refresher_env
    mornings = [datetime(2026, 10, d, 8, 0, tzinfo=KST) for d in (13, 14, 15, 19, 20, 21, 22)]    # 16일(금) 아침 틱이 없다
    outs = [asyncio.run(R.run_cycle(now)) for now in mornings]
    assert [o.outcome for o in outs] == ["refreshed"] * 7, outs
    assert outs[3].filled == ("20261015",)                    # 월요일 아침: 기대 = 금 16일, 빠진 목 15일도 함께
    assert fetches == ["20261012", "20261013", "20261014", "20261016", "20261015", "20261019", "20261020", "20261021"]
    with psycopg.connect(pg17) as c:
        days = [r[0] for r in c.execute("SELECT dd FROM krx_day WHERE n > 0 ORDER BY 1").fetchall()]
        weekly = [r[0] for r in c.execute("SELECT DISTINCT price_dd FROM krx_weekly ORDER BY 1").fetchall()]
        wk_close = [r[0] for r in c.execute("SELECT dd FROM krx_day WHERE wk_close ORDER BY 1").fetchall()]
    assert days == sorted(fetches)                            # 다 남는다(1년 창)
    assert weekly == ["20261016"] and wk_close == ["20261016"]   # 닫힌 주 — 지난주 금요일. 이번 주(19~21)는 일별에만 있다


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
        raise AssertionError("보관 창 안은 KRX 를 부르면 안 된다")
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
