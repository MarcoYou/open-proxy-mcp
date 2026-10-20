"""krx_weekly 는 닫힌 주만 — 쓰는 쪽(재설계 5-1)의 닫기 규칙과 배치 가드를 Postgres 17 에서 증명한다 (261018).

쓰는 쪽(`price_multiple_data._krx_db_upsert`)은 종전에 매 거래일 krx_weekly 의 이번 주 행을 지우고 다시 넣었다(하루 약 2.2천 죽은 행).
이제 krx_daily 가 기록의 주(主)이고, krx_weekly 는 닫힌 주만 한 번 옮겨 적는다(`krx_daily.sync_weekly`).

  R1  그 주의 마지막 평일(보통 금요일)이 저장되면 그 주를 그 날로 닫는다.
  R2  더 늦은 주의 거래일이 저장되면 앞 주를 그 주의 가장 늦은 날로 닫는다(금요일 휴장·누락 대비).
  R3  같은 주는 더 늦은 날로만 바꾼다(이미 같거나 늦은 날이 있으면 그대로).
  연도  한 주 = ISO 주를 달력 연도로 자른 것 — 12/30 과 다음 해 1/2 가 같은 ISO 주여도 둘 다 남는다.

배치 가드: 스냅샷 날짜의 시총을 일별 저장분에서 읽고, 종목이 너무 적으면 기존 스냅샷을 지우지 않고 실패한다(종전엔 0행 저장 후 「완료」).
Docker 가 없으면 건너뛴다(`tests/conftest.py` 의 `pg17`).
"""
from __future__ import annotations

import asyncio
import sys
from datetime import date

import pytest

psycopg = pytest.importorskip("psycopg")

from test_krx_daily_pg import KRX_WEEKLY_DDL, _ddl, _row  # noqa: E402 — 스키마·KRX 행 모양 재사용

from open_proxy_mcp import krx_api, krx_daily  # noqa: E402
from open_proxy_mcp.services import price_multiple_data as pmd  # noqa: E402

SHRS = 1_000_000
KS_CODES = ("K00000", "K00001", "K00002")
KQ_CODES = ("Q00000", "Q00001")


def _day(dd: str, extra_kq: tuple[str, ...] = ()):
    """그 날 전종목(코스피 3 · 코스닥 2 + 덧붙일 코스닥). 종가는 날짜마다 달라 어느 날 단면인지 값으로 가릴 수 있다."""
    base = int(dd[-4:])
    kospi = [_row(t, base * 10 + i, shrs=SHRS) for i, t in enumerate(KS_CODES)]
    kosdaq = [_row(t, base + i, shrs=SHRS) for i, t in enumerate(KQ_CODES + extra_kq)]
    return kospi, kosdaq


@pytest.fixture
def wdb(pg17, monkeypatch):
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(KRX_WEEKLY_DDL)
    _ddl().apply(pg17)
    monkeypatch.setenv("DATABASE_URL", pg17)
    import open_proxy_mcp.db as dbm                          # 서버 읽기(`pg_rows`)가 풀 없이 이 컨테이너로 바로 접속하게
    monkeypatch.setattr(dbm, "_pool", None)
    monkeypatch.setattr(dbm, "_pool_disabled_until", float("inf"))
    return pg17


def _write(dd: str, **kw) -> pmd.KrxWrite:
    res = pmd._krx_db_upsert(dd, *_day(dd, **kw))
    assert res.daily_ok and res.weekly_ok, (dd, res)
    return res


def _weekly(url) -> dict[str, int]:
    with psycopg.connect(url) as c:
        return dict(c.execute("SELECT price_dd, count(*) FROM krx_weekly GROUP BY 1 ORDER BY 1").fetchall())


def _q(url, sql, params=()):
    with psycopg.connect(url) as c:
        return c.execute(sql, params).fetchall()


# ── 닫기 규칙 ──────────────────────────────────────────────────────────────

def test_r1_friday_closes_its_week_and_r2_a_later_week_closes_a_holiday_week(wdb):
    # 10/5(월)~10/8(목) — 10/9(금)은 한글날 휴장. 주중에는 krx_weekly 에 아무것도 쓰지 않는다
    for dd in ("20261005", "20261006", "20261007", "20261008"):
        assert _write(dd).closed == ()
    assert _weekly(wdb) == {}
    # R2: 다음 주 첫 거래일이 저장되는 순간 앞 주를 그 주의 가장 늦은 날(목)로 닫는다
    assert _write("20261012").closed == ("20261008",)
    assert _weekly(wdb) == {"20261008": 5}
    for dd in ("20261013", "20261014", "20261015"):
        assert _write(dd).closed == ()
    # R1: 금요일이 저장되면 그 주를 바로 닫는다(토요일 아침 기록)
    assert _write("20261016").closed == ("20261016",)
    assert _weekly(wdb) == {"20261008": 5, "20261016": 5}
    # 닫힌 값 = 그 날 일별 단면(시총 = 종가 × 상장주식수)
    assert _q(wdb, "SELECT ticker, market, close, mktcap, list_shrs FROM krx_weekly WHERE price_dd='20261016' ORDER BY ticker") == \
        _q(wdb, "SELECT ticker, market, close, mktcap, list_shrs FROM krx_daily_v WHERE trd_dd='20261016' ORDER BY ticker")
    # 다음 주 월요일 — 지난주는 이미 닫혀 있어 아무것도 다시 쓰지 않는다
    assert _write("20261019").closed == ()


def test_daily_write_and_weekly_close_share_one_commit(wdb, monkeypatch):
    """일별 기록과 주간 닫기는 바깥 트랜잭션 하나다 — 그 사이에서 죽으면(여기선 닫기 단계가 예외) 일별 행도 남지 않는다."""
    def boom(conn, before=None):
        raise RuntimeError("닫기 직전에 죽었다")
    monkeypatch.setattr(pmd._krx_daily, "sync_weekly", boom)
    assert pmd._krx_db_upsert("20261016", *_day("20261016")) == pmd.KrxWrite(False)
    assert _q(wdb, "SELECT count(*) FROM krx_daily") == [(0,)] and _weekly(wdb) == {}


def test_r3_only_a_later_day_replaces_a_week_and_stale_old_writer_rows_are_cleared(wdb):
    _write("20261012")
    _write("20261013")
    # 전환기: 배포 직전 옛 쓰는 쪽이 넣은 이번 주 화요일 행(매일 지우고 다시 넣던 그 행)
    with psycopg.connect(wdb) as c:
        c.execute("INSERT INTO krx_weekly (price_dd, ticker, market, close, mktcap, list_shrs) "
                  "SELECT trd_dd, ticker, market, close, mktcap, list_shrs FROM krx_daily_v WHERE trd_dd='20261013'")
        c.commit()
    assert _weekly(wdb) == {"20261013": 5}
    _write("20261014")                                        # 이번 주의 더 이른 날 행은 옛 쓰기의 잔재 — 치운다
    assert _weekly(wdb) == {}
    # 백필이 그 주의 금요일 행을 이미 넣어 두었다(값으로 가릴 수 있게 종가 1)
    with psycopg.connect(wdb) as c:
        c.execute("INSERT INTO krx_weekly VALUES ('20261016', 'K00000', 'KS', 1, 1, 1)")
        c.commit()
    _write("20261015")                                        # 열린 주 정리는 최신일보다 늦은 행을 건드리지 않는다
    assert _write("20261019").closed == ()                    # R3: 그 주는 목(15일)으로 닫히지 않는다 — 더 늦은 금요일이 이미 있다
    assert _q(wdb, "SELECT price_dd, ticker, close FROM krx_weekly") == [("20261016", "K00000", 1)]


def test_year_boundary_keeps_the_last_trading_day_of_the_year(wdb):
    """2025-12-30(화)과 2026-01-02(금)은 같은 ISO 주(2026-W01)다 — 종전 정의로는 1/2 를 쓰는 순간 12/30 이 지워졌다
    (운영 실측: 2019·2024·2025 연말 행이 하루~이틀 이른 금요일). 연도에서 주를 나누면 둘 다 남는다."""
    assert date(2025, 12, 30).isocalendar()[:2] == date(2026, 1, 2).isocalendar()[:2]
    for dd in ("20251229", "20251230"):
        assert _write(dd).closed == ()                        # 12/31 은 휴장 — 그 주의 마지막 평일이 오지 않아 아직 열림
    assert _write("20260102").closed == ("20251230", "20260102")   # R2 로 연말 주, R1 로 새해 첫 주(1/2 는 그 주 마지막 평일)
    assert _weekly(wdb) == {"20251230": 5, "20260102": 5}
    # 보존 규칙의 「그 종목의 그해 마지막 행」·연말 밴드가 보는 값이 실제 마지막 거래일이다
    assert _q(wdb, "SELECT max(price_dd) FROM krx_weekly WHERE left(price_dd, 4) = '2025'") == [("20251230",)]
    # 같은 주 정리(배치 스냅샷 수렴)·일별 끝점 잇기가 쓰는 범위도 같은 정의다
    assert pmd._iso_wk_range("20251230") == ("20251229", "20251231")
    assert pmd._iso_wk_range("20260102") == ("20260101", "20260104")


def test_steady_state_never_deletes_or_rewrites_weekly_rows(wdb):
    """죽은 행 0: 한 주는 한 번만 쓰이고, 그 뒤 기록·재실행은 그 행을 지우거나 다시 쓰지 않는다(같은 물리 위치·같은 트랜잭션 번호)."""
    _write("20261009")
    before = _q(wdb, "SELECT ticker, ctid::text, xmin::text FROM krx_weekly ORDER BY ticker")
    assert len(before) == 5
    for dd in ("20261012", "20261013", "20261014", "20261015", "20261016", "20261019"):
        _write(dd)
        with psycopg.connect(wdb) as c:                       # 같은 날 다시 돌아도(배치 예비 닫기) 지우는 행이 없다 — 멱등
            again = krx_daily.sync_weekly(c)
            c.commit()
        assert again.ok and again.closed == () and again.dropped == 0, (dd, again)
    after = _q(wdb, "SELECT ticker, ctid::text, xmin::text FROM krx_weekly WHERE price_dd='20261009' ORDER BY ticker")
    assert after == before
    assert _weekly(wdb) == {"20261009": 5, "20261016": 5}


def test_weekly_with_latest_has_the_old_krx_weekly_shape(wdb):
    """「닫힌 주 + 최신 거래일」 원천은 종전 krx_weekly(주마다 마지막 거래일 + 이번 주 최신일)와 같은 모양이다."""
    for dd in ("20261009", "20261012", "20261013", "20261014"):
        _write(dd)
    src = f"SELECT price_dd, count(*), sum(mktcap) FROM {krx_daily.WEEKLY_WITH_LATEST} w GROUP BY 1 ORDER BY 1"
    got = _q(wdb, src)
    assert [(d, n) for d, n, _ in got] == [("20261009", 5), ("20261014", 5)]
    assert got[1][2] == sum(r[0] for r in _q(wdb, "SELECT mktcap FROM krx_daily_v WHERE trd_dd='20261014'"))
    for dd in ("20261015", "20261016"):
        _write(dd)
    assert [(d, n) for d, n, _ in _q(wdb, src)] == [("20261009", 5), ("20261016", 5)]    # 금요일이 닫히면 겹쳐 세지 않는다


# ── 배치 예비 갱신(`_ensure_krx_fresh`) ─────────────────────────────────────────

def test_ensure_krx_fresh_uses_the_daily_store_and_closes_a_missed_week(wdb, monkeypatch):
    """서버 갱신기가 일별은 썼는데 주간 닫기를 놓친 경우 — 배치가 KRX 를 부르지 않고(일별이 최신) 닫기만 메운다.
    종전엔 최신 판정이 krx_weekly 라 주중 내내 「낡음」으로 보여 매일 KRX 를 다시 훑고 이미 저장된 날을 다시 썼다."""
    with psycopg.connect(wdb) as c:
        for dd in ("20261012", "20261013", "20261014", "20261015", "20261016"):
            assert krx_daily.write_daily(c, dd, *_day(dd))[0]
        c.commit()
    assert _weekly(wdb) == {}
    monkeypatch.setattr(pmd, "today_kst", lambda: date(2026, 10, 17))          # 토요일 — 직전 영업일 = 금요일
    pmd._KRX_STATE.clear()

    async def no_krx():
        raise AssertionError("일별 저장분이 최신이면 KRX 를 부르지 않는다")
    monkeypatch.setattr(pmd, "_fetch_live_snapshot", no_krx)

    async def no_krx_fetch(*a, **k):
        raise AssertionError("일별 저장분이 최신이면 KRX 를 부르지 않는다")
    # 261021: 예비 갱신은 `krx_daily.ensure_fresh` 로 옮겨 KRX 를 `krx_api.fetch_day` 로 부른다 — 막이도 새 자리에 둔다
    monkeypatch.setattr(krx_api, "fetch_day", no_krx_fetch)
    assert asyncio.run(pmd._ensure_krx_fresh()) == "20261016"
    assert _weekly(wdb) == {"20261016": 5}
    pmd._KRX_STATE.clear()


# ── 조용히 깨지던 배치 ─────────────────────────────────────────────────────────

def _val_tables(url):
    """`market_val_weekly` 가 읽고 쓰는 표 — 운영처럼 opm_val_market 에 scheme 칸과 그 기본키가 있다.
    `opm_val_firm` 은 만들지 않는다 — 261018(재설계 6단계)부터 이 배치는 그 표를 쓰지 않고, 운영에서는 백업 뒤 지운다."""
    from scripts import market_val_weekly as W
    with psycopg.connect(url, autocommit=True) as c:
        c.execute(W.DDL_MKT)
        c.execute("ALTER TABLE opm_val_market ADD COLUMN scheme text NOT NULL DEFAULT 'market'")
        c.execute("ALTER TABLE opm_val_market DROP CONSTRAINT opm_val_market_pkey")
        c.execute("ALTER TABLE opm_val_market ADD PRIMARY KEY (snap_dd, market, scheme, sector)")
        c.execute("CREATE TABLE dart_fundamentals (ticker text PRIMARY KEY, market text, induty text, currency text, "
                  "ni_fy double precision, ni_ttm double precision, eq_fy double precision, eq_mrq double precision, fetched text)")
        for t, m in [(t, "KS") for t in KS_CODES] + [(t, "KQ") for t in KQ_CODES]:
            c.execute("INSERT INTO dart_fundamentals VALUES (%s, %s, '', 'KRW', 1e9, 1e9, 1e10, 1e10, 'ok')", (t, m))
        # 같은 주의 지난 스냅샷(월요일) — 성공하면 수렴으로 지워지고, 가드가 걸리면 그대로 남아야 한다
        c.execute("INSERT INTO opm_val_market (snap_dd, market, scheme, sector, cap) VALUES ('20261012', 'KS', 'market', '_ALL', 42)")
    return W


def _run_snapshot(W, monkeypatch, snap_dd):
    async def fresh():
        return snap_dd
    kinds_calls = []

    async def kinds(dd):
        kinds_calls.append(dd)
        return {t: "보통주" for t in KS_CODES + KQ_CODES}
    monkeypatch.setattr(W, "_ensure_krx_fresh", fresh)
    monkeypatch.setattr(W, "_krx_kinds", kinds)
    return asyncio.run(W.run()), kinds_calls


def test_market_val_weekly_refuses_a_thin_snapshot_and_keeps_the_existing_one(wdb, monkeypatch, capsys):
    """종전: 스냅샷 날짜에 krx_weekly 행이 없으면(닫힌 주만 쓰게 되면 주중엔 늘 그렇다) 같은 주 스냅샷을 지우고 0행을 저장한 뒤 「저장 완료」.
    지금: 시총을 일별 저장분에서 읽고, MIN_SNAPSHOT_TICKERS 미만이면 아무것도 지우지 않고 실패(1)로 끝낸다."""
    W = _val_tables(wdb)
    for dd in ("20261012", "20261013", "20261014"):
        _write(dd)
    rc, kinds_calls = _run_snapshot(W, monkeypatch, "20261014")     # 일별 저장분에 5종목뿐 — 하한(1,500) 미만
    assert rc == 1 and kinds_calls == []                             # KRX 종목유형도 부르지 않는다
    assert _q(wdb, "SELECT snap_dd, market, cap FROM opm_val_market") == [("20261012", "KS", 42.0)]
    assert "::error::" in capsys.readouterr().out
    rc, _ = _run_snapshot(W, monkeypatch, "20261009")                # 저장분에 없는 날(창 밖) — 0종목도 같은 가드
    assert rc == 1 and _q(wdb, "SELECT count(*) FROM opm_val_market") == [(1,)]


def test_market_val_weekly_snapshots_the_daily_cross_section_mid_week(wdb, monkeypatch):
    W = _val_tables(wdb)
    monkeypatch.setattr(W, "MIN_SNAPSHOT_TICKERS", 3)
    for dd in ("20261012", "20261013", "20261014"):
        _write(dd)
    rc, _ = _run_snapshot(W, monkeypatch, "20261014")
    assert rc == 0
    caps = _q(wdb, "SELECT market, sum(mktcap) FROM krx_daily_v WHERE trd_dd='20261014' GROUP BY 1 ORDER BY 1")
    mkt = _q(wdb, "SELECT snap_dd, market, cap, n FROM opm_val_market WHERE scheme='market' ORDER BY market")
    assert [r[0] for r in mkt] == ["20261014", "20261014"]           # 같은 주 월요일 스냅샷은 수렴으로 지워졌다
    assert [(m, c, n) for _, m, c, n in mkt] == [("KQ", float(caps[0][1]), 2), ("KS", float(caps[1][1]), 3)]
    # 261018(재설계 6단계): 종목별 표는 만들지도 쓰지도 않는다
    assert _q(wdb, "SELECT to_regclass('opm_val_firm')") == [(None,)]


def test_include_new_lists_tickers_from_the_daily_store(wdb, monkeypatch, capsys):
    """`market_fund_quarterly --include-new` — 종전 `krx_weekly WHERE price_dd=일별 최신일` 은 주중 0종목이라 「신규 0」으로 조용히 끝났다."""
    import types
    from scripts import market_fund_quarterly as Q
    with psycopg.connect(wdb, autocommit=True) as c:
        c.execute(Q.DDL_FUNDAMENTALS)
        c.execute("INSERT INTO dart_fundamentals (ticker, fetched) SELECT unnest(%s::text[]), 'ok'", (list(KS_CODES + KQ_CODES),))
    _write("20261013")
    _write("20261014", extra_kq=("N00000",))                         # 이번 주 수요일 신규 상장 — 주간에는 아직 없다

    async def fresh():
        return "20261014"

    async def kinds(dd):
        return {t: "보통주" for t in KS_CODES + KQ_CODES + ("N00000",)}

    class Client:
        async def _load_corp_codes(self):               # 261020: 등록은 명부의 종목코드 표로 먼저 잇는다
            return [{"corp_code": "00999999", "stock_code": "N00000", "modify_date": "20261014"}]

        async def lookup_corp_code(self, ticker):
            # 261020: 등록은 결과의 종목코드가 물은 코드와 같을 때만 잇는다(`exact_corp_code`) — 해석기 결과 모양 그대로
            return {"corp_code": "00999999", "stock_code": "N00000"} if ticker == "N00000" else None

        async def get_company_info(self, cc):           # 261020: 이은 종목의 업종코드·결산월(회사 개황 1콜)
            return {"induty_code": "264", "acc_mt": "12"}
    monkeypatch.setattr(pmd, "_ensure_krx_fresh", fresh)   # 261020: 등록은 더 부르지 않는다(저장분 최신일만 읽는다) — 불려도 같은 날
    monkeypatch.setitem(sys.modules, "market_val_weekly", types.SimpleNamespace(_krx_kinds=kinds))
    monkeypatch.setattr("open_proxy_mcp.dart.client.get_dart_client", lambda: Client())

    monkeypatch.setattr(Q, "INCLUDE_NEW_MIN_LISTED", 3)
    assert asyncio.run(Q.include_new_tickers()) == 1
    assert _q(wdb, "SELECT ticker, corp_code, market, fetched FROM dart_fundamentals WHERE ticker='N00000'") == \
        [("N00000", "00999999", "KQ", "ok")]
    # 하한 가드 — 저장분이 반쪽이면 「신규 없음」으로 읽지 않고 오류 줄을 남긴다
    monkeypatch.setattr(Q, "INCLUDE_NEW_MIN_LISTED", 1_500)
    assert asyncio.run(Q.include_new_tickers()) == 0
    assert "::error::include-new" in capsys.readouterr().out


# ── 늦어지던 배치: 이번 주 최신 점 ─────────────────────────────────────────────

def test_cap_agg_carries_this_weeks_point_and_replaces_it_when_the_week_closes(wdb, monkeypatch):
    from scripts import krx_cap_agg as C
    with psycopg.connect(wdb, autocommit=True) as c:
        c.execute("CREATE TABLE wise_sector (snap_dd text, ticker text, sector_code text, sector text, "
                  "industry_code text, industry text)")
        c.execute("INSERT INTO wise_sector SELECT '20260821', t, 'G10', '에너지', 'G1010', '에너지' "
                  "FROM unnest(%s::text[]) t", (list(KS_CODES),))       # 코스닥은 미분류로 남는다
    for dd in ("20261009", "20261012", "20261013"):
        _write(dd)
    monkeypatch.setattr(sys, "argv", ["krx_cap_agg.py"])
    assert C.main() == 0
    dates = lambda: [r[0] for r in _q(wdb, "SELECT DISTINCT price_dd FROM krx_cap_agg WHERE scheme='market' ORDER BY 1")]  # noqa: E731
    assert dates() == ["20261009", "20261013"]                       # 닫힌 주 + 이번 주 임시 점(일별 최신일)
    ks = _q(wdb, "SELECT cap, n FROM krx_cap_agg WHERE scheme='market' AND price_dd='20261013' AND market='KS'")
    assert ks == [(sum(r[0] for r in _q(wdb, "SELECT mktcap FROM krx_daily_v WHERE trd_dd='20261013' AND market='KS'")), 3)]
    for dd in ("20261014", "20261015", "20261016"):
        _write(dd)
    assert C.main() == 0
    assert dates() == ["20261009", "20261016"]                       # 임시 점(13일)은 닫힌 날(16일)로 바뀌었다


def test_listing_observes_this_weeks_new_listing_from_the_daily_store(wdb, monkeypatch):
    from scripts import krx_listing as L
    monkeypatch.setattr(L, "MIN_TICKERS", 3)
    _write("20261009")
    with psycopg.connect(wdb) as c:
        for stmt in L.DDL.strip().split(";"):
            if stmt.strip():
                c.execute(stmt)
        c.execute("INSERT INTO krx_listing (ticker, market, first_seen_dd, last_seen_dd, before_window, window_lo) "
                  "SELECT ticker, market, '20151230', '20261009', true, '20151230' FROM krx_weekly")
        c.commit()
    _write("20261012")
    _write("20261013", extra_kq=("N00000",))                         # 이번 주 화요일 상장 — krx_weekly 에는 금요일이 닫혀야 들어온다
    with psycopg.connect(wdb) as c:
        assert L.build(c) == 0
    got = dict((t, (f, l)) for t, f, l in _q(wdb, "SELECT ticker, first_seen_dd, last_seen_dd FROM krx_listing"))
    assert got["N00000"] == ("20261013", "20261013")
    assert got["K00000"] == ("20151230", "20261013")                 # 최종 관측일도 이번 주 최신일까지 나아간다


def test_disclosure_ledger_after_sector_columns_are_dropped_still_stores_the_filing_day_cap(wdb):
    """261018(재설계 6단계): `dart_events` 의 업종 복사본 칸(`sector`·`industry`·`sector_dd`)과 `industry` 색인을 운영에서 지운다.
    옛 모양의 표에서 그것만 지운 뒤에도 배치의 DDL·쓰기가 돌고, 시총(`mktcap_won`·`mktcap_dd`)은 계속 **저장**된다 —
    접수일 이하 가장 늦은 거래일, 일별 창 안이면 일별 시세에서. 일별 창 안 날짜는 지워지지 않으니 재스캔해도 같은 날이 붙는다
    (종전엔 krx_weekly 주중 행이 다음 날 지워져 접수 10-15 공시에 20261008 시총이 붙었다)."""
    from scripts import disclosure_ledger as DL
    old_ddl = DL.DDL.replace("  flr_nm        text,\n", "  flr_nm        text,\n  sector text,\n  industry text,\n  sector_dd text,\n")
    with psycopg.connect(wdb, autocommit=True) as c:
        c.execute(old_ddl)                                               # 옛 표(업종 복사본 칸 있음)
        c.execute("CREATE INDEX dart_events_industry_dt ON dart_events (industry, rcept_dt)")
        c.execute("ALTER TABLE dart_events DROP COLUMN sector, DROP COLUMN industry, DROP COLUMN sector_dd")   # 운영에서 할 일
        c.execute(DL.DDL)                                                # 배치가 매번 거는 DDL 이 깨지지 않는다
    for dd in ("20261008", "20261012", "20261013", "20261014", "20261015"):
        _write(dd)

    def scan():
        rows = [DL.row_from_item({"rcept_no": "20261015000001", "rcept_dt": "20261015", "corp_code": "00000001",
                                  "corp_name": "가", "stock_code": "K00001", "corp_cls": "Y",
                                  "report_nm": "단일판매ㆍ공급계약체결", "flr_nm": "가"}, {"order"}),
                DL.row_from_item({"rcept_no": "20261010000002", "rcept_dt": "20261010", "corp_code": "00000002",
                                  "corp_name": "나", "stock_code": "K00002", "corp_cls": "Y",
                                  "report_nm": "단일판매ㆍ공급계약체결", "flr_nm": "나"}, {"order"})]   # 토요일 접수 → 목(10/8)
        with psycopg.connect(wdb, autocommit=True) as c:
            DL.enrich(c, rows)
            with c.cursor() as cur:
                for r in rows:
                    cur.execute(DL.UPSERT_EVENT, DL.db_row(r))
        return _q(wdb, "SELECT rcept_no, mktcap_dd, mktcap_won FROM dart_events ORDER BY 1")
    first = scan()
    k15 = _q(wdb, "SELECT mktcap FROM krx_daily_v WHERE trd_dd='20261015' AND ticker='K00001'")[0][0]
    k08 = _q(wdb, "SELECT mktcap FROM krx_weekly WHERE price_dd='20261008' AND ticker='K00002'")[0][0]
    assert first == [("20261010000002", "20261008", k08), ("20261015000001", "20261015", k15)]
    _write("20261016")                                                   # 다음 날 재스캔 — 같은 공시에 같은 날·같은 값
    assert scan() == first
