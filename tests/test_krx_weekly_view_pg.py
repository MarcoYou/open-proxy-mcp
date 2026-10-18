"""krx_weekly 를 같은 이름 뷰로(재설계 v03 C4, 261019) — Postgres 17 에서 증명한다.

뷰 = 닫힌 주 압축 표 `krx_wk`(일별 보관 창보다 이른 주 + 연말 행) ∪ 일별 저장분의 주 닫는 날 행(`krx_day.wk_close`).
  · 교체(`scripts/krx_weekly_ddl.py --switch`)는 옛 표와 뷰가 전 행·전 칸 같아야 커밋된다(G2 관문).
  · 쓰는 쪽은 DB 를 보고 고른다(`krx_daily.weekly_is_view`) — 같은 기록 순서를 표 DB 와 뷰 DB 에 주면 `krx_weekly` 가 늘 같다
    (주 닫기·휴장·연도 경계 주·보관 창 정리가 주 닫는 날을 krx_wk 로 옮기기·콜드 아닌 날 남기기·되돌리기·다시 교체).
  · 보관 창 정리가 주 닫는 날을 옮기지 못하면 그 날은 지우지 않는다(일별 기록은 그대로 커밋).
Docker 가 없으면 건너뛴다(`tests/conftest.py` 의 `pg17`).
"""
from __future__ import annotations

import importlib.util
import logging
import pathlib
import uuid
from datetime import date, timedelta

import pytest

psycopg = pytest.importorskip("psycopg")

from test_krx_daily_pg import _ddl as _daily_ddl, _row  # noqa: E402 — 일별 스키마·KRX 행 모양 재사용

from open_proxy_mcp import krx_daily  # noqa: E402
from open_proxy_mcp import krx_retention as R  # noqa: E402
from open_proxy_mcp.services import price_multiple_data as pmd  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: 운영 옛 표와 같은 모양(기본키·색인·RLS 정책 이름).
LEGACY_DDL = """CREATE TABLE krx_weekly (price_dd text NOT NULL, ticker text NOT NULL, market text,
  close bigint, mktcap bigint, list_shrs bigint, PRIMARY KEY (price_dd, ticker));
CREATE INDEX idx_krx_weekly_isu ON krx_weekly (ticker, price_dd);
ALTER TABLE krx_weekly ENABLE ROW LEVEL SECURITY;
CREATE POLICY krx_weekly_read ON krx_weekly FOR SELECT USING (true);
GRANT SELECT ON krx_weekly TO opm_ro;"""

KS = ("K00000", "K00001", "K00002")
KQ = ("Q00000", "Q00001")


def _wddl():
    spec = importlib.util.spec_from_file_location("krx_weekly_ddl", ROOT / "scripts" / "krx_weekly_ddl.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _day(dd: str, *, extra_kq: tuple[str, ...] = ()):
    """그 날 전종목(코스피 3 · 코스닥 2 + 덧붙일 코스닥). 종가·주식수가 날짜마다 달라 어느 날 단면인지 값으로 가린다."""
    base = int(dd[-4:])
    kospi = [_row(t, base * 10 + i, shrs=1_000_000 + base, cmp=1) for i, t in enumerate(KS)]
    kosdaq = [_row(t, base + i, shrs=2_000_000 + base, cmp=-1) for i, t in enumerate(KQ + extra_kq)]
    return kospi, kosdaq


def _q(url, sql, params=()):
    with psycopg.connect(url) as c:
        return c.execute(sql, params).fetchall()


def _x(url, sql, params=()):
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(sql, params)


def _weekly(url):
    return _q(url, "SELECT price_dd, ticker, market, close, mktcap, list_shrs FROM krx_weekly ORDER BY 1, 2")


def _kind(url):
    return _q(url, krx_daily.WEEKLY_KIND_SQL)[0][0]


def _seed(url):
    """옛 세계: 일별 스키마 + 옛 표(창보다 이른 닫힌 주 — 상장폐지 종목 하나·연말 행 포함)."""
    _daily_ddl().apply(url)
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(LEGACY_DDL)
        rows = []
        for d in ("20250829", "20250905", "20251230", "20260904", "20260911", "20260918", "20260925"):
            for i, t in enumerate(KS + KQ):
                rows.append((d, t, "KS" if t in KS else "KQ", 100 + i, (100 + i) * 7, 7))
        rows += [("20250829", "D00000", "KQ", 50, 500, 10), ("20250905", "D00000", "KQ", 51, 510, 10)]   # 2025-09 상장폐지
        with c.cursor() as cur, cur.copy("COPY krx_weekly (price_dd, ticker, market, close, mktcap, list_shrs) FROM STDIN") as cp:
            for r in rows:
                cp.write_row(r)


@pytest.fixture
def two(pg17, _pg17_server, monkeypatch):
    """같은 시작 상태의 DB 두 개(A = 끝까지 표, B = 교체·되돌리기·다시 교체). 보관 창은 2주로 줄인다(정리를 짧게 돌려 보려고)."""
    admin, url_for = _pg17_server
    b = "t_" + uuid.uuid4().hex[:10]
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{b}"')
    urls = (pg17, url_for(b))
    for u in urls:
        with psycopg.connect(u, autocommit=True) as c:
            c.execute("ALTER DEFAULT PRIVILEGES FOR ROLE postgres IN SCHEMA public GRANT SELECT ON TABLES TO anon, authenticated")
        _seed(u)
    monkeypatch.setattr(krx_daily, "KEEP_YEARS", 0)
    monkeypatch.setattr(krx_daily, "KEEP_EXTRA_DAYS", 14)
    import open_proxy_mcp.db as dbm
    monkeypatch.setattr(dbm, "_pool", None)
    monkeypatch.setattr(dbm, "_pool_disabled_until", float("inf"))
    yield urls
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{b}" WITH (FORCE)')


def _write(url, dd, monkeypatch, **kw) -> pmd.KrxWrite:
    monkeypatch.setenv("DATABASE_URL", url)
    res = pmd._krx_db_upsert(dd, *_day(dd, **kw))
    assert res.daily_ok and res.weekly_ok, (url, dd, res)
    return res


def _closed_day(url, dd):
    with psycopg.connect(url) as c:
        krx_daily.record_closed(c, dd)
        c.commit()


def _apply(url, mode):
    _wddl().apply(url, mode)


def _weekdays(lo: str, hi: str):
    d, e = date(int(lo[:4]), int(lo[4:6]), int(lo[6:])), date(int(hi[:4]), int(hi[4:6]), int(hi[6:]))
    while d <= e:
        if d.isoweekday() < 6:
            yield d.strftime("%Y%m%d")
        d += timedelta(days=1)


HOLIDAYS = {"20261009", "20261225", "20261231", "20270101"}


def _run(urls, lo, hi, monkeypatch, *, extra=None):
    """두 DB 에 같은 기록 순서를 준다. 휴장은 달력에 휴장으로 적는다. 날마다 두 `krx_weekly` 와 「이번에 닫은 주」가 같아야 한다."""
    for dd in _weekdays(lo, hi):
        if dd in HOLIDAYS:
            for u in urls:
                _closed_day(u, dd)
            continue
        kw = (extra or {}).get(dd, {})
        res = [_write(u, dd, monkeypatch, **kw) for u in urls]
        assert res[0].closed == res[1].closed, (dd, [_kind(u) for u in urls], res)
        assert _weekly(urls[0]) == _weekly(urls[1]), (dd, [_kind(u) for u in urls])


def _mark_cold(urls, upto):
    """맥미니 연례 작업 흉내 — 그 날까지의 거래일을 콜드 보관본에 들어간 것으로 적는다."""
    for u in urls:
        _x(u, "UPDATE krx_day SET cold = true WHERE dd <= %s AND n > 0", (upto,))


# ── 끝에서 끝까지: 표 DB 와 뷰 DB 가 같은 기록 순서에 늘 같은 krx_weekly ─────────────────────────

def test_view_world_equals_table_world_through_switch_rolling_year_end_rollback_and_reswitch(two, monkeypatch, caplog):
    a, b = two
    # ① 둘 다 표: 10/5~10/16(10/9 금 한글날 휴장)
    _run(two, "20261005", "20261016", monkeypatch)
    # ② B 만 교체(G2 관문 통과해야 커밋)
    _apply(b, "switch")
    assert (_kind(a), _kind(b)) == ("r", "v") and _weekly(a) == _weekly(b)
    assert _q(b, "SELECT count(*), min(dd), max(dd) FROM krx_wk_day") == [(7, "20250829", "20260925")]   # 창 이전 주만(콜드 날 없음)
    # ③ 교체 직후 첫 금요일: 토요일 아침 기록이 그 주를 닫는다 — 두 DB 가 같은 「주간 닫음」을 알린다
    _run(two, "20261019", "20261023", monkeypatch)
    assert _q(b, "SELECT dd FROM krx_day WHERE wk_close ORDER BY 1")[-1] == ("20261023",)
    assert ("20261023",) in [(r[0],) for r in _weekly(b)]
    # ④ 창(2주) 밖으로 나간 날 — 콜드가 아니면 남긴다(두 DB 같음), 콜드가 되면 B 는 주 닫는 날(10/8·10/16)을 krx_wk 로 옮긴 뒤 지운다
    with caplog.at_level(logging.WARNING):
        _run(two, "20261026", "20261030", monkeypatch)
    assert any("콜드 보관본에 없어 지우지 않았다" in r.message for r in caplog.records)
    _mark_cold(two, "20261016")
    _run(two, "20261102", "20261102", monkeypatch)
    assert _q(b, "SELECT dd, n FROM krx_wk_day WHERE dd >= '20261001' ORDER BY 1") == [("20261008", 5), ("20261016", 5)]
    assert _q(b, "SELECT count(*) FROM krx_day WHERE dd <= '20261016'") == [(0,)]       # 일별 저장분에서는 지웠다
    assert _q(a, "SELECT count(*) FROM krx_day WHERE dd <= '20261016'") == [(0,)]
    # ⑤ 연말·연초: 성탄절(금) 휴장 주는 다음 주 첫 기록에 목요일로, 12/31(목)·1/1(금) 휴장이 낀 연말 주는 새해 첫 기록에 12/30 으로 닫힌다.
    #    새해 첫 주(1/1~1/3 은 거래일 없음)는 따로 생기지 않는다. 한 주 = ISO 주 ∩ 달력 연도.
    _run(two, "20261103", "20270108", monkeypatch)
    closes = [r[0] for r in _q(b, "SELECT dd FROM krx_day WHERE wk_close AND dd >= '20261218' ORDER BY 1")]
    assert closes == ["20261218", "20261224", "20261230", "20270108"]
    # ⑥ 2026 년을 콜드로 → 정리가 연말 행(12/30)까지 옮긴다
    _mark_cold(two, "20261231")
    _run(two, "20270111", "20270115", monkeypatch)
    moved = [r[0] for r in _q(b, "SELECT dd FROM krx_wk_day WHERE dd >= '20261201' ORDER BY 1")]
    assert moved == ["20261204", "20261211", "20261218", "20261224", "20261230"]
    assert _q(b, "SELECT ticker, close FROM krx_weekly WHERE price_dd = '20261230' ORDER BY 1") == \
        [("K00000", 12300), ("K00001", 12301), ("K00002", 12302), ("Q00000", 1230), ("Q00001", 1231)]
    # 같은 날이 두 곳(krx_wk·일별)에 동시에 보이지 않는다
    assert _q(b, "SELECT count(*) FROM (SELECT price_dd, ticker FROM krx_weekly GROUP BY 1, 2 HAVING count(*) > 1) x") == [(0,)]
    # ⑦ 되돌리기: 뷰가 보여 주던 행 그대로 표로 → 다음 기록부터 다시 종전(표) 쓰기
    before = _weekly(b)
    _apply(b, "rollback")
    assert _kind(b) == "r" and _weekly(b) == before == _weekly(a)
    _run(two, "20270118", "20270122", monkeypatch)
    # ⑧ 다시 교체(되돌린 뒤의 표에서 새로 채운다) — 그 뒤에도 같다
    _apply(b, "switch")
    assert _kind(b) == "v" and _weekly(b) == _weekly(a)
    _run(two, "20270125", "20270129", monkeypatch)


# ── 교체 관문·계약 ─────────────────────────────────────────────────────────

def test_switch_contract_same_columns_security_and_privileges(two, monkeypatch):
    a, b = two
    _run(two, "20261005", "20261016", monkeypatch)
    legacy_cols = _q(b, "SELECT column_name, data_type FROM information_schema.columns WHERE table_name='krx_weekly' ORDER BY ordinal_position")
    _apply(b, "switch")
    cols = _q(b, "SELECT column_name, data_type FROM information_schema.columns WHERE table_name='krx_weekly' ORDER BY ordinal_position")
    assert cols == legacy_cols == [("price_dd", "text"), ("ticker", "text"), ("market", "text"), ("close", "bigint"),
                                   ("mktcap", "bigint"), ("list_shrs", "bigint")]
    assert _q(b, "SELECT reloptions FROM pg_class WHERE relname='krx_weekly'") == [(["security_invoker=true"],)]
    for rel in ("krx_weekly", "krx_wk", "krx_wk_day"):
        grants = {r[0] for r in _q(b, "SELECT grantee FROM information_schema.role_table_grants WHERE table_name=%s AND privilege_type='SELECT'", (rel,))}
        assert "opm_ro" in grants and not grants & {"anon", "authenticated"}, (rel, grants)
    for t in ("krx_wk", "krx_wk_day"):
        assert _q(b, "SELECT relrowsecurity FROM pg_class WHERE relname=%s", (t,)) == [(True,)]
    # 옛 표는 이름만 바뀌어 남는다(되돌리기용) — 색인·정책 이름도
    assert {r[0] for r in _q(b, "SELECT relname FROM pg_class WHERE relname LIKE '%%krx_weekly_legacy%%'")} == \
        {"krx_weekly_legacy", "krx_weekly_legacy_pkey", "idx_krx_weekly_legacy_isu"}
    assert _q(b, "SELECT polname FROM pg_policy WHERE polrelid='krx_weekly_legacy'::regclass") == [("krx_weekly_legacy_read",)]
    # 두 번 교체하지 않는다
    with pytest.raises(psycopg.errors.RaiseException, match="이미 교체"):
        _apply(b, "switch")
    # opm_ro 로 읽힌다(security_invoker — 아래 표 권한·정책으로 평가)
    with psycopg.connect(b, autocommit=True) as c:
        c.execute("SET ROLE opm_ro")
        assert c.execute("SELECT count(*) FROM krx_weekly").fetchone()[0] == len(_weekly(a))


def test_switch_gate_rolls_everything_back_when_the_view_would_differ(two, monkeypatch):
    a, b = two
    _run(two, "20261005", "20261016", monkeypatch)
    _x(b, "UPDATE krx_weekly SET close = close + 1 WHERE price_dd = '20261016' AND ticker = 'K00000'")   # 옛 표 ≠ 일별 주 닫는 날
    with pytest.raises(psycopg.errors.RaiseException, match="G2 관문 실패.*l_minus_v=1"):
        _apply(b, "switch")
    assert _kind(b) == "r" and _q(b, "SELECT to_regclass('krx_wk') IS NULL") == [(True,)]          # 아무것도 남지 않았다


def test_switch_pre_copies_cold_window_weeks_so_old_code_pruning_cannot_lose_them(two, monkeypatch):
    """교체 때 콜드 보관본에 들어간 창 안 주 닫는 날도 krx_wk 에 미리 둔다 — 옮기지 않는 옛 코드가 그 날을 지워도 주간 행이 남는다."""
    a, b = two
    _run(two, "20261005", "20261016", monkeypatch)
    _mark_cold([b], "20261008")
    _apply(b, "switch")
    assert _q(b, "SELECT dd FROM krx_wk_day WHERE dd >= '20261001'") == [("20261008",)]
    # 옛 코드처럼 옮기지 않고 일별 행만 지운다
    _x(b, "DELETE FROM krx_px WHERE trd_dd = '2026-10-08'")
    _x(b, "DELETE FROM krx_day WHERE trd_dd = '2026-10-08'")
    assert _weekly(b) == _weekly(a)


def test_drop_legacy_only_when_every_old_row_is_still_in_the_view(two, monkeypatch):
    a, b = two
    _run(two, "20261005", "20261016", monkeypatch)
    _apply(b, "switch")
    _x(b, "INSERT INTO krx_weekly_legacy VALUES ('20200103', 'Z00000', 'KS', 1, 1, 1)")             # 뷰에 없는 옛 행
    with pytest.raises(psycopg.errors.RaiseException, match="뷰에 없다"):
        _apply(b, "drop-legacy")
    _x(b, "DELETE FROM krx_weekly_legacy WHERE ticker = 'Z00000'")
    _apply(b, "drop-legacy")
    assert _q(b, "SELECT to_regclass('krx_weekly_legacy') IS NULL") == [(True,)] and _weekly(b) == _weekly(a)


# ── 보관 창 정리: 옮기지 못하면 지우지 않는다 ─────────────────────────────────────────

def test_prune_keeps_a_week_closing_day_it_could_not_move_and_retries_next_time(two, monkeypatch, caplog):
    a, b = two
    _run(two, "20261005", "20261016", monkeypatch)
    _apply(b, "switch")
    _run(two, "20261019", "20261030", monkeypatch)
    _mark_cold(two, "20261016")
    _x(b, """CREATE FUNCTION _boom() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'boom'; END $$;
             CREATE TRIGGER _boom BEFORE INSERT ON krx_wk FOR EACH ROW EXECUTE FUNCTION _boom();""")
    with caplog.at_level(logging.ERROR):
        res = _write(b, "20261102", monkeypatch)
    assert res.daily_ok
    assert any("krx_wk 로 옮기지 못해" in r.message and "20261008" in r.message for r in caplog.records)
    # 주 닫는 날(10/8·10/16)은 일별에 남고 뷰에도 그대로 보인다. 다른 날(콜드)은 지웠다
    assert [r[0] for r in _q(b, "SELECT dd FROM krx_day WHERE dd <= '20261016' ORDER BY 1")] == ["20261008", "20261016"]
    _write(a, "20261102", monkeypatch)
    assert _weekly(a) == _weekly(b)
    _x(b, "DROP TRIGGER _boom ON krx_wk")
    _run(two, "20261103", "20261103", monkeypatch)
    assert _q(b, "SELECT count(*) FROM krx_day WHERE dd <= '20261016'") == [(0,)]
    assert _q(b, "SELECT dd FROM krx_wk_day WHERE dd >= '20261001' ORDER BY 1") == [("20261008",), ("20261016",)]


# ── 읽는 쪽: 표든 뷰든 같은 답 ──────────────────────────────────────────────────

def test_readers_answer_the_same_in_both_worlds(two, monkeypatch):
    a, b = two
    _run(two, "20261005", "20261016", monkeypatch)
    _apply(b, "switch")
    _run(two, "20261019", "20261021", monkeypatch)                    # 이번 주(안 닫힘) — 최신 거래일은 일별에만
    for sql, params in ((krx_daily.WEEKLY_LATEST_SQL, ()),
                        (f"SELECT * FROM {krx_daily.WEEKLY_WITH_LATEST} w ORDER BY 1, 2", ()),
                        (pmd._WEEKLY_START_SQL, ("20231016", "20231016", "20231120", "20231016")),
                        (pmd._WEEKLY_START_SQL, ("20250901", "20250901", "20251006", "20250901"))):
        assert sorted(_q(a, sql, params)) == sorted(_q(b, sql, params)), sql
    assert _q(b, krx_daily.WEEKLY_LATEST_SQL) == [("20261016",)]
    wwl = _q(b, f"SELECT price_dd, count(*) FROM {krx_daily.WEEKLY_WITH_LATEST} w GROUP BY 1 ORDER BY 1 DESC LIMIT 2")
    assert wwl == [("20261021", 5), ("20261016", 5)]
    # 금요일이 닫힌 뒤(토요일 아침)에는 최신 거래일이 이미 주간에 있다 — 두 번 붙이지 않는다
    _run(two, "20261022", "20261023", monkeypatch)
    for u in two:
        assert _q(u, f"SELECT price_dd, count(*) FROM {krx_daily.WEEKLY_WITH_LATEST} w GROUP BY 1 ORDER BY 1 DESC LIMIT 2") == \
            [("20261023", 5), ("20261016", 5)]
    # 롤링 술어: 압축 표 판 = 뷰 위의 정본 술어(같은 (종목, 날짜) 집합)
    for latest in ("20261016", "20280903", "20290115"):
        w, p = R.prune_where(latest, alias="w")
        w2, p2 = R.prune_where_wk(latest, alias="w")
        view_rows = _q(b, f"SELECT w.ticker, w.price_dd FROM krx_weekly w WHERE {w} ORDER BY 1, 2", p)
        wk_rows = _q(b, "SELECT k.ticker, c.dd FROM krx_wk w JOIN krx_ticker k ON k.tid = w.tid JOIN krx_wk_day c ON c.wk_dd = w.wk_dd "
                        f"WHERE {w2} ORDER BY 1, 2", p2)
        table_rows = _q(a, f"SELECT w.ticker, w.price_dd FROM krx_weekly w WHERE {w} ORDER BY 1, 2", p)
        assert view_rows == wk_rows == table_rows, latest
    w, p = R.prune_where("20290115", alias="w")
    assert ("D00000", "20250829") in _q(b, f"SELECT w.ticker, w.price_dd FROM krx_weekly w WHERE {w}", p)   # 연말 행이 아닌 옛 주
    assert ("D00000", "20250905") not in _q(b, f"SELECT w.ticker, w.price_dd FROM krx_weekly w WHERE {w}", p)  # 상장폐지 종목의 그해 마지막 행


# ── 지난 주 백필 쓰기 ─────────────────────────────────────────────────────────

def test_write_week_goes_to_krx_wk_before_the_daily_window_and_refuses_inside_it(two, monkeypatch, caplog):
    a, b = two
    _run(two, "20261005", "20261016", monkeypatch)
    _apply(b, "switch")
    recs = [("20250912", "K00000", "KS", 300, 300 * 9, 9), ("20250912", "N00000", "KQ", 40, 400, 10)]   # 새 종목 하나
    for u in two:
        with psycopg.connect(u) as c:
            assert krx_daily.write_week(c, "20250912", recs) == 2
            c.commit()
    assert _weekly(a) == _weekly(b)
    assert _q(b, "SELECT n FROM krx_wk_day WHERE dd = '20250912'") == [(2,)]
    # 같은 날을 다시 쓰면 통째로 바뀐다(주 단위 멱등)
    with psycopg.connect(b) as c:
        assert krx_daily.write_week(c, "20250912", recs[:1]) == 1
        c.commit()
    assert _q(b, "SELECT ticker FROM krx_weekly WHERE price_dd = '20250912'") == [("K00000",)]
    # 일별 보관 창 안의 날은 쓰지 않는다(그 주는 일별 저장분이 담는다)
    with caplog.at_level(logging.WARNING), psycopg.connect(b) as c:
        assert krx_daily.write_week(c, "20261009", [("20261009", "K00000", "KS", 1, 1, 1)]) == 0
    assert any("일별 보관 창" in r.message for r in caplog.records)


def test_switch_pre_grants_the_server_account_only_what_the_move_needs(two, monkeypatch):
    """서버 전용 계정(opm_app)이 이미 있으면 교체 DDL 이 옮기기에 필요한 만큼만 준다(RLS 정책 포함) — krx_wk 를 지우지는 못한다."""
    a, b = two
    with psycopg.connect(b, autocommit=True) as c:
        if not c.execute("SELECT 1 FROM pg_roles WHERE rolname = 'opm_app'").fetchone():
            c.execute("CREATE ROLE opm_app NOLOGIN BYPASSRLS")   # 클러스터 전역 — 사용 기록 시험(test_usage_write_path)과 같은 속성
    _run(two, "20261005", "20261016", monkeypatch)
    _apply(b, "switch")
    with psycopg.connect(b) as c:
        c.execute("SET ROLE opm_app")
        c.execute("INSERT INTO krx_wk_day (wk_dd, dd, n) VALUES ('2025-09-12', '20250912', 0)")
        tid = c.execute("SELECT min(tid) FROM krx_wk").fetchone()[0]
        c.execute("INSERT INTO krx_wk (list_shrs, close, wk_dd, tid, market) VALUES (1, 1, '2025-09-12', %s, 'S') "
                  "ON CONFLICT (tid, wk_dd) DO NOTHING", (tid,))
        c.execute("UPDATE krx_wk_day SET n = 1 WHERE wk_dd = '2025-09-12'")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("DELETE FROM krx_wk WHERE wk_dd = '2025-09-12'")
        c.rollback()
    # RLS 를 따르는 계정으로 만들어도 되게 정책도 둔다(시험 역할은 다른 시험과 같은 BYPASSRLS — 정책은 있는지만 본다)
    pols = {r[0] for r in _q(b, "SELECT polname FROM pg_policy WHERE polrelid IN ('krx_wk'::regclass, 'krx_wk_day'::regclass)")}
    assert {"krx_wk_app_insert", "krx_wk_day_app_insert", "krx_wk_day_app_update"} <= pols


def test_full_mode_builds_an_empty_view_on_a_new_db_and_is_idempotent(pg17):
    """새 DB(시험·로컬): 일별 스키마 뒤 표 2개 + 같은 이름 뷰. 두 번 돌려도 같다. 옛 표가 있는 DB 에서는 거절한다(운영은 --switch)."""
    _daily_ddl().apply(pg17)
    _apply(pg17, "full")
    _apply(pg17, "full")
    assert _kind(pg17) == "v" and _weekly(pg17) == []
    with psycopg.connect(pg17) as c:                                  # 뷰 모드 쓰기 — 일별 기록과 정리가 그대로 돈다
        assert krx_daily.write_daily(c, "20261016", *_day("20261016"))[0]
        assert krx_daily.sync_weekly(c).ok
        c.commit()
    assert [r[0] for r in _weekly(pg17)] == ["20261016"] * 5          # 금요일 — 주 닫는 날이라 뷰에 보인다
    _x(pg17, "DROP VIEW krx_weekly")
    _x(pg17, LEGACY_DDL)
    with pytest.raises(SystemExit, match="아직 표다"):
        _apply(pg17, "full")
