"""최소 권한 서버 계정으로 일별 시세 쓰기 경로 전체가 도는가 — Postgres 17.

서버 DB 계정을 표 소유자에서 최소 권한 계정(소유자 아님 · BYPASSRLS 없음 · RLS 정책을 따름)으로 바꾸면 세 군데가 달라진다
(261019 로컬 PG17 실측):
  ① RLS 가 켜진 `krx_px` 에 `COPY FROM` 을 못 한다(`COPY FROM not supported with row-level security`) → 쓰는 쪽은 한 문장 INSERT.
  ② `brin_desummarize_range` 는 색인 소유자만 된다 — PG17 MAINTAIN 권한으로도 `must be owner of index` → 소유자 함수
     `krx_px_brin_desummarize(date[])`(`scripts/krx_daily_ddl.py`)를 거친다.
  ③ `VACUUM` 은 권한(MAINTAIN)이 없으면 오류 없이 건너뛰고 서버 경고만 낸다 → 갱신기가 그 경고를 로그로 옮긴다.
이 파일은 그 계정의 모양(`_grant_writer` — 쓰는 표마다 필요한 권한·RLS 정책만)으로 갱신기가 하는 일 — 새 종목 번호·달력·사실값·
주 닫는 날·보관 창 정리(콜드 날 지우기·BRIN 요약)·주간 닫기·휴장 기록·청소 — 을 돌린다. Docker 가 없으면 건너뛴다.

261021(P0 지수 종가): 권한 기대값에 `krx_idx`(SELECT·INSERT·DELETE·MAINTAIN)·`krx_idx_series`(SELECT·INSERT 다섯 칸·UPDATE first_dd/last_dd)와
주간 뷰 모드의 밑 표(`krx_wk`·`krx_wk_day` — 창 밖 주 닫는 날 옮기기)를 더했다(`_grant_writer(view=True, idx=True)`). 지수 표의 행 단위 정책은
서버 계정 행렬과 같은 글자다 — INSERT = 최근 60일 · DB 계열(in_db), DELETE = 최근 60일 · 달력에 콜드이면서 주 닫는 날이 아닌 날 ·
주 달력에 있으면서 3년 하한보다 이른 날. 그래서 창 밖 일별 점은 달력 행이 지워지기 **전에** 지워야 한다(`krx_daily.prune_window` 순서).
정책이 오늘(KST)을 보므로 지수 시험의 날짜는 오늘 기준으로 만든다.
"""
from __future__ import annotations

import logging
import pathlib
import sys
import urllib.parse
from datetime import date

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

psycopg = pytest.importorskip("psycopg")

from test_krx_daily_pg import KRX_WEEKLY_DDL, _ddl, _row, put_daily  # noqa: E402 — 스키마·KRX 행 모양 재사용

from open_proxy_mcp import krx_daily  # noqa: E402
from open_proxy_mcp.services import krx_refresher  # noqa: E402
from open_proxy_mcp.services import price_multiple_data as pmd  # noqa: E402

WRITER = "opm_writer_t"
FN = f"{krx_daily.PRUNE_DESUMMARIZE_FN}(date[])"


def _as(url: str, role: str) -> str:
    return f"{url}?options={urllib.parse.quote(f'-c role={role}')}"


#: 서버 계정 행렬(맥 `opm_app_role.py`)의 지수 표 정책과 같은 글자 — 창 60일.
IDX_RECENT = "krx_idx.trd_dd >= ((now() AT TIME ZONE 'Asia/Seoul')::date - 60)"
IDX_INSERT_CHECK = f"({IDX_RECENT}) AND EXISTS (SELECT 1 FROM public.krx_idx_series s WHERE s.sid = krx_idx.sid AND s.in_db)"
IDX_DELETE_USING = (f"({IDX_RECENT})"
                    " OR EXISTS (SELECT 1 FROM public.krx_day d WHERE d.trd_dd = krx_idx.trd_dd AND d.cold AND NOT d.wk_close)"
                    " OR (EXISTS (SELECT 1 FROM public.krx_wk_day w WHERE w.wk_dd = krx_idx.trd_dd)"
                    " AND krx_idx.trd_dd < (SELECT (max(d.trd_dd) - interval '3 years')::date FROM public.krx_day d WHERE d.n > 0 AND NOT d.closed))")


def _wk_move_ok(col: str) -> str:
    return f"EXISTS (SELECT 1 FROM public.krx_day d WHERE d.trd_dd = {col} AND d.cold AND d.wk_close)"


def _grant_writer(url: str, *, maintain: bool = True, fn: bool = True, view: bool = False, idx: bool = False) -> str:
    """최소 권한 서버 계정의 모양 — 표 소유자 아님 · RLS 를 따름 · 쓰는 표에만 그 일에 필요한 권한과 정책.
    `krx_day` 는 칸 단위(콜드 표시 `cold` 는 못 바꾼다), `krx_ticker` 는 종목코드만 넣는다(번호는 IDENTITY — 시퀀스 권한 없이 된다).
    `view` = 주간 뷰 모드(밑 표 krx_wk·krx_wk_day 읽기·옮기기), `idx` = 지수 종가 표 둘(갱신기의 지수 쓰기·정리·청소)."""
    with psycopg.connect(url, autocommit=True) as c:
        if not c.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (WRITER,)).fetchone():
            c.execute(f"CREATE ROLE {WRITER} NOLOGIN NOINHERIT NOBYPASSRLS")
        x = c.execute
        x(f"GRANT USAGE ON SCHEMA public TO {WRITER}")
        x(f"GRANT SELECT ON krx_px, krx_day, krx_ticker, krx_daily, krx_daily_v, price_latest, krx_weekly TO {WRITER}")
        x(f"GRANT INSERT (ticker) ON krx_ticker TO {WRITER}")
        x(f"GRANT INSERT (trd_dd, dd, n, closed), UPDATE (n, closed, wk_close), DELETE ON krx_day TO {WRITER}")
        x(f"GRANT INSERT, DELETE ON krx_px TO {WRITER}")
        x(f"GRANT INSERT, DELETE ON krx_weekly TO {WRITER}")
        if maintain:
            x(f"GRANT MAINTAIN ON krx_px, krx_day TO {WRITER}")
        if fn:
            x(f"GRANT EXECUTE ON FUNCTION {FN} TO {WRITER}")
        pols = [("krx_ticker", "INSERT", "WITH CHECK (true)"), ("krx_day", "INSERT", "WITH CHECK (true)"),
                ("krx_day", "UPDATE", "USING (true) WITH CHECK (true)"), ("krx_day", "DELETE", "USING (true)"),
                ("krx_px", "INSERT", "WITH CHECK (true)"), ("krx_px", "DELETE", "USING (true)")]
        if view:
            x(f"GRANT SELECT ON krx_wk, krx_wk_day TO {WRITER}")
            x(f"GRANT INSERT (list_shrs, close, wk_dd, tid, market) ON krx_wk TO {WRITER}")
            x(f"GRANT INSERT (wk_dd, dd, n), UPDATE (n) ON krx_wk_day TO {WRITER}")
            pols += [("krx_wk", "INSERT", f"WITH CHECK ({_wk_move_ok('krx_wk.wk_dd')})"),
                     ("krx_wk_day", "INSERT", f"WITH CHECK ({_wk_move_ok('krx_wk_day.wk_dd')})"),
                     ("krx_wk_day", "UPDATE", f"USING ({_wk_move_ok('krx_wk_day.wk_dd')}) WITH CHECK ({_wk_move_ok('krx_wk_day.wk_dd')})")]
        if idx:
            x(f"GRANT SELECT, INSERT, DELETE ON krx_idx TO {WRITER}")
            if maintain:
                x(f"GRANT MAINTAIN ON krx_idx TO {WRITER}")
            x(f"GRANT SELECT, INSERT (src, name, kind, first_dd, last_dd), UPDATE (first_dd, last_dd) ON krx_idx_series TO {WRITER}")
            pols += [("krx_idx", "INSERT", f"WITH CHECK ({IDX_INSERT_CHECK})"), ("krx_idx", "DELETE", f"USING ({IDX_DELETE_USING})"),
                     ("krx_idx_series", "INSERT", "WITH CHECK (true)"), ("krx_idx_series", "UPDATE", "USING (true) WITH CHECK (true)")]
        for t, cmd, expr in pols:
            x(f"CREATE POLICY w_{cmd.lower()} ON {t} FOR {cmd} TO {WRITER} {expr}")
    return _as(url, WRITER)


@pytest.fixture
def ldb(pg17, monkeypatch):
    """스키마(소유자) + 최소 권한 계정. 반환 (소유자 URL, 쓰는 계정 URL). 서버 읽기(`pg_rows`)·쓰기(`DATABASE_URL`)는 쓰는 계정으로."""
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(KRX_WEEKLY_DDL)
    _ddl().apply(pg17)
    w = _grant_writer(pg17)
    monkeypatch.setenv("DATABASE_URL", w)
    import open_proxy_mcp.db as dbm
    monkeypatch.setattr(dbm, "_pool", None)
    monkeypatch.setattr(dbm, "_pool_disabled_until", float("inf"))
    return pg17, w


def _q(url, sql, params=()):
    with psycopg.connect(url) as c:
        return c.execute(sql, params).fetchall()


def _day(dd: str, n: int = 4):
    base = int(dd[-4:])
    return [_row(f"K{i:05d}", base * 10 + i, cmp=0) for i in range(n)], [_row(f"Q{i:05d}", base + i, cmp=0) for i in range(n)]


def test_writer_is_not_owner_and_is_subject_to_rls_so_copy_is_refused(ldb):
    """시험 전제 — 이 계정에 RLS 가 걸리고(COPY 가 막힌다), 그래서 쓰는 쪽은 COPY 를 쓰지 않는다."""
    owner, w = ldb
    with psycopg.connect(w) as c:
        assert c.execute("SELECT current_user, row_security_active('krx_px')").fetchone() == (WRITER, True)
        with pytest.raises(psycopg.errors.FeatureNotSupported, match="row-level security"):
            with c.cursor() as cur, cur.copy("COPY krx_px (trd_dd, tid, market) FROM STDIN") as cp:
                cp.write_row((date(2026, 10, 16), 1, "S"))


def test_writer_writes_a_day_with_new_tickers_and_reads_it_back_through_the_views(ldb):
    owner, w = ldb
    with psycopg.connect(w) as c:
        assert krx_daily.write_daily(c, "20261015", *_day("20261015")) == (True, 8, 0)
        assert krx_daily.write_daily(c, "20261016", *_day("20261016", 5)) == (True, 10, 0)    # 새 종목 2 — 번호는 IDENTITY
        c.commit()
    assert _q(owner, "SELECT dd, n, closed FROM krx_day ORDER BY 1") == [("20261015", 8, False), ("20261016", 10, False)]
    assert _q(owner, "SELECT count(*), count(DISTINCT tid) FROM krx_px") == [(18, 10)]
    q = krx_daily.read_quote("K00004", "20261016")
    assert q.ok and q.row["close"] == 1016 * 10 + 4 and q.day == "20261016"
    # 같은 날을 다시 쓰면 그 날 행을 통째로 바꾼다(DELETE + INSERT, 정책 안에서)
    with psycopg.connect(w) as c:
        assert krx_daily.write_daily(c, "20261016", *_day("20261016", 3)) == (True, 6, 0)
        c.commit()
    assert _q(owner, "SELECT n FROM krx_day WHERE dd='20261016'") == [(6,)]
    assert _q(owner, "SELECT count(*) FROM krx_px WHERE trd_dd='2026-10-16'") == [(6,)]


def test_writer_prunes_cold_days_and_desummarizes_through_the_owner_function(ldb, caplog):
    owner, w = ldb
    with psycopg.connect(owner) as c:                       # 창 밖 옛 날 둘(콜드) — 소유자가 넣는다(백필·이관 몫)
        put_daily(c, [(d, f"K{i:05d}", "KS", 100 + i, 1_000) for d in ("20250106", "20250107") for i in range(4)])
        c.execute("UPDATE krx_day SET cold = true WHERE dd IN ('20250106', '20250107')")
        c.commit()
    with psycopg.connect(w) as c:                           # 함수가 실제로 요약을 지운다(행이 있는 범위 수)
        assert c.execute(f"SELECT {krx_daily.PRUNE_DESUMMARIZE_FN}(%s::date[])", ([date(2025, 1, 6)],)).fetchone()[0] >= 1
        c.rollback()
    with caplog.at_level(logging.WARNING, logger="open_proxy_mcp.krx_daily"):
        with psycopg.connect(w) as c:
            ok, n, pruned = krx_daily.write_daily(c, "20260116", *_day("20260116"))
            c.commit()
    assert (ok, pruned) == (True, 8)
    assert not [r for r in caplog.records if "BRIN 요약 지우기 실패" in r.getMessage()]
    assert _q(owner, "SELECT count(*) FROM krx_day WHERE dd IN ('20250106','20250107')") == [(0,)]
    # 대조군: 함수 실행 권한이 없으면 직접 부르는 길로 떨어져 「소유자가 아님」으로 실패한다 — 그래도 지우기는 계속한다
    with psycopg.connect(owner, autocommit=True) as c:
        c.execute(f"REVOKE EXECUTE ON FUNCTION {FN} FROM {WRITER}")
    with psycopg.connect(owner) as c:
        put_daily(c, [("20250108", f"K{i:05d}", "KS", 100 + i, 1_000) for i in range(4)])
        c.execute("UPDATE krx_day SET cold = true WHERE dd = '20250108'")
        c.commit()
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="open_proxy_mcp.krx_daily"):
        with psycopg.connect(w) as c:
            ok, _, pruned = krx_daily.write_daily(c, "20260119", *_day("20260119"))
            c.commit()
    assert ok and pruned == 4
    assert any("BRIN 요약 지우기 실패" in r.getMessage() for r in caplog.records)


def test_writer_runs_the_refresher_write_with_the_weekly_close_and_a_closed_day(ldb):
    """갱신기의 쓰기(`_krx_db_upsert` — 일별 기록 + 같은 트랜잭션의 주간 닫기)와 휴장 기록이 이 계정으로 된다."""
    owner, w = ldb
    for dd in ("20261012", "20261013", "20261014", "20261015", "20261016", "20261019"):
        res = pmd._krx_db_upsert(dd, *_day(dd))
        assert res.daily_ok and res.weekly_ok, (dd, res)
    assert _q(owner, "SELECT price_dd, count(*) FROM krx_weekly GROUP BY 1") == [("20261016", 8)]
    assert _q(owner, "SELECT dd FROM krx_day WHERE wk_close") == [("20261016",)]
    with psycopg.connect(w) as c:
        assert krx_daily.record_closed(c, "20261009")
        c.commit()
    assert _q(owner, "SELECT closed, n FROM krx_day WHERE dd='20261009'") == [(True, 0)]


def test_vacuum_needs_maintain_and_the_refresher_says_so_when_it_is_missing(pg17, monkeypatch, caplog):
    """권한 없는 VACUUM 은 오류 없이 건너뛴다 — 갱신기가 서버 경고를 로그로 옮겨 조용히 넘어가지 않는다. MAINTAIN 이 있으면 경고 0."""
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(KRX_WEEKLY_DDL)
    _ddl().apply(pg17)
    w = _grant_writer(pg17, maintain=False)
    monkeypatch.setenv("DATABASE_URL", w)
    with caplog.at_level(logging.WARNING, logger="open_proxy_mcp.services.krx_refresher"):
        krx_refresher._vacuum()
    msgs = [r.getMessage() for r in caplog.records if "VACUUM" in r.getMessage()]
    assert msgs and "MAINTAIN" in msgs[0] and "krx_px" in msgs[0]
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(f"GRANT MAINTAIN ON krx_px, krx_day TO {WRITER}")
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="open_proxy_mcp.services.krx_refresher"):
        krx_refresher._vacuum()
    assert not [r for r in caplog.records if "VACUUM" in r.getMessage()]


def test_writer_cannot_flip_cold_truncate_or_desummarize_directly(ldb):
    """권한 밖은 막힌다 — 콜드 표시(보관본을 올린 쪽 몫)·TRUNCATE·BRIN 요약 직접 지우기(MAINTAIN 이 있어도 소유자만)."""
    owner, w = ldb
    with psycopg.connect(w) as c:
        assert krx_daily.write_daily(c, "20261016", *_day("20261016"))[0]
        c.commit()
    for sql in ("UPDATE krx_day SET cold = true", "TRUNCATE krx_px",
                f"SELECT brin_desummarize_range('{krx_daily.BRIN_INDEX}'::regclass, 0)", "UPDATE krx_px SET close = 0"):
        with psycopg.connect(w) as c:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(sql)
    with psycopg.connect(owner) as c:                         # 함수는 SECURITY DEFINER · PUBLIC·anon 실행 권한 없음
        secdef, pub, anon = c.execute(
            "SELECT prosecdef, has_function_privilege('public', %s, 'EXECUTE'), has_function_privilege('anon', %s, 'EXECUTE') "
            "FROM pg_proc WHERE oid = %s::regprocedure", (FN, FN, FN)).fetchone()
    assert secdef and not pub and not anon


# ── 지수 종가(krx_idx) — 같은 최소 권한 계정으로 ───────────────────────────────────────────────────

from test_krx_idx_refresher import idx_rows_for, idx_schema, seed_series  # noqa: E402 — 지수 스키마·사전·응답 모양 재사용

from open_proxy_mcp import krx_idx  # noqa: E402


def _kst_today() -> date:
    from open_proxy_mcp.clock import KST
    from datetime import datetime
    return datetime.now(KST).date()


def _weekdays_back(start_days_ago: int, end_days_ago: int) -> list[str]:
    """오늘(KST) 기준 start~end 일 전 사이의 평일(YYYYMMDD, 오름차순)."""
    from datetime import timedelta
    t = _kst_today()
    out = []
    for k in range(start_days_ago, end_days_ago - 1, -1):
        d = t - timedelta(days=k)
        if d.isoweekday() < 6:
            out.append(d.strftime("%Y%m%d"))
    return out


@pytest.fixture
def lidx(pg17, monkeypatch):
    """주간 뷰 모드 + 지수 표 + 최소 권한 계정(view·idx 권한). 반환 (소유자 URL, 쓰는 계정 URL)."""
    idx_schema(pg17)
    with psycopg.connect(pg17, autocommit=True) as c:
        seed_series(c)
    w = _grant_writer(pg17, view=True, idx=True)
    monkeypatch.setenv("DATABASE_URL", w)
    import open_proxy_mcp.db as dbm
    monkeypatch.setattr(dbm, "_pool", None)
    monkeypatch.setattr(dbm, "_pool_disabled_until", float("inf"))
    return pg17, w


def _write_idx(url, dd, rows=None, cold=None):
    with psycopg.connect(url) as c:
        with c.transaction():
            res = krx_idx.write_day(c, krx_idx.parse_day(dd, rows or idx_rows_for(dd)))
            old = krx_idx.prune_old(c, cold) if res.ok else 0
    return res, old


def test_writer_writes_index_closes_registers_new_names_and_reads_pending(lidx):
    owner, w = lidx
    dd = _weekdays_back(10, 1)[-1]
    with psycopg.connect(w) as c:
        assert krx_daily.write_daily(c, dd, *_day(dd))[0]
        c.commit()
    from open_proxy_mcp.db import pg_rows
    assert krx_idx.pending(pg_rows, _kst_today().strftime("%Y%m%d")) == krx_idx.Pending(True, (dd,))
    extra = ({"BAS_DD": dd, "IDX_CLSS": "KOSPI", "IDX_NM": "새 지수", "CLSPRC_IDX": "100.00"},)
    res, _ = _write_idx(w, dd, idx_rows_for(dd, extra=extra))
    assert res.ok and res.rows == 5 and res.new == ("P|새 지수",)          # 새 이름 등록 — 시퀀스 권한 없이(IDENTITY)
    assert _q(owner, "SELECT in_db, kind::text FROM krx_idx_series WHERE name = '새 지수'") == [(False, "t")]
    assert _q(owner, "SELECT count(*) FROM krx_idx WHERE trd_dd = %s", (date(int(dd[:4]), int(dd[4:6]), int(dd[6:])),)) == [(5,)]
    res2, _ = _write_idx(w, dd)                                                # 같은 날 다시 — 지우고 넣기(정책 안)
    assert res2.ok and _q(owner, "SELECT count(*) FROM krx_idx") == [(5,)]
    assert krx_idx.pending(pg_rows, _kst_today().strftime("%Y%m%d")) == krx_idx.Pending(True, ())


def test_writer_cannot_reach_outside_the_index_policies(lidx):
    """정책·칸 권한 밖 — 콜드 전용 계열에 값 넣기 · 60일보다 옛 날 넣기 · in_db/idx_code 바꾸기 · 비우기 · 3년 안 주간 점 지우기."""
    owner, w = lidx
    old = _weekdays_back(120, 110)[0]
    recent = _weekdays_back(10, 1)[-1]
    with psycopg.connect(owner, autocommit=True) as c:
        c.execute("INSERT INTO krx_wk_day (wk_dd, dd, n) VALUES (%s::date, %s, 0)", (old, old))
        c.execute("INSERT INTO krx_idx (sid, trd_dd, close_x100) VALUES (1, %s, 100)", (old,))
    for sql, params in (("INSERT INTO krx_idx (sid, trd_dd, close_x100) VALUES (6, %s, 1)", (recent,)),     # in_db=false 계열
                        ("INSERT INTO krx_idx (sid, trd_dd, close_x100) VALUES (1, %s, 1)", (old,)),        # 60일 밖
                        ("UPDATE krx_idx_series SET in_db = true WHERE sid = 6", ()),
                        ("UPDATE krx_idx_series SET idx_code = 'x' WHERE sid = 2", ()),
                        ("UPDATE krx_idx SET close_x100 = 1", ()),
                        ("DELETE FROM krx_idx_series WHERE sid = 6", ()),
                        ("TRUNCATE krx_idx", ())):
        with psycopg.connect(w) as c:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(sql, params)
    with psycopg.connect(w) as c:                                              # 3년 안 · 달력에 없는 옛 주간 점은 정책이 감춘다(0행)
        assert c.execute("DELETE FROM krx_idx WHERE trd_dd = %s::date", (old,)).rowcount == 0
        c.commit()
    assert _q(owner, "SELECT count(*) FROM krx_idx WHERE trd_dd = %s::date", (old,)) == [(1,)]


def test_writer_prunes_daily_index_points_before_the_calendar_rows_go(lidx, monkeypatch):
    """60일보다 옛 날(정책의 「최근」 밖)의 일별 점도 시세 정리가 같은 트랜잭션에서 지운다 — 달력 행(콜드·주 닫는 날 아님)이 남아 있을 때만
    정책이 허락하므로 순서가 맞아야 한다. 주 닫는 날은 krx_wk_day 로 옮겨져 남는다."""
    owner, w = lidx
    monkeypatch.setattr(krx_daily, "KEEP_YEARS", 0)
    monkeypatch.setattr(krx_daily, "KEEP_EXTRA_DAYS", 14)
    olds = _weekdays_back(100, 86)                                             # 3주쯤 — 소유자가 넣는다(백필 몫)
    for dd in olds:
        with psycopg.connect(owner) as c:
            krx_daily.write_daily(c, dd, *_day(dd))
            c.commit()
        _write_idx(owner, dd)
    with psycopg.connect(owner, autocommit=True) as c:
        c.execute("UPDATE krx_day SET cold = true")
    wk = [r[0] for r in _q(owner, "SELECT dd FROM krx_day WHERE wk_close ORDER BY 1")]
    assert wk and set(wk) < set(olds)
    # 대조군: 달력 행을 먼저 지우면(순서가 틀리면) 이 계정은 그 날 지수 행을 지울 수 없다
    probe = next(d for d in olds if d not in wk)
    with psycopg.connect(owner, autocommit=True) as c:
        c.execute("DELETE FROM krx_px WHERE trd_dd = %s::date", (probe,))
        c.execute("DELETE FROM krx_day WHERE dd = %s", (probe,))
    with psycopg.connect(w) as c:
        assert c.execute("DELETE FROM krx_idx WHERE trd_dd = %s::date", (probe,)).rowcount == 0
        c.rollback()
    new = _weekdays_back(5, 1)[-1]
    with psycopg.connect(w) as c:
        ok, n, pruned = krx_daily.write_daily(c, new, *_day(new))
        c.commit()
    assert ok and pruned > 0
    left = {r[0] for r in _q(owner, "SELECT DISTINCT to_char(trd_dd, 'YYYYMMDD') FROM krx_idx")}
    wk_day = {r[0] for r in _q(owner, "SELECT dd FROM krx_wk_day")}
    assert wk_day >= set(wk) and wk_day <= set(olds)                          # 마지막 옛 주도 새 기록으로 닫혀 옮겨진다
    assert left == wk_day | {probe}                                           # 주 닫는 날만 남는다(+ 대조군 고아)
    assert _q(owner, "SELECT count(*) FROM krx_day WHERE dd = ANY(%s)", (olds,)) == [(0,)]


def test_writer_prunes_old_weekly_points_only_past_three_years_and_inside_cold(lidx):
    owner, w = lidx
    days = _weekdays_back(12, 1)
    with psycopg.connect(w) as c:
        for dd in days:
            krx_daily.write_daily(c, dd, *_day(dd))
        c.commit()
    from datetime import timedelta
    t = _kst_today()
    far = (t - timedelta(days=4 * 365)).strftime("%Y%m%d")                    # 3년 밖
    near = (t - timedelta(days=2 * 365)).strftime("%Y%m%d")                   # 3년 안
    with psycopg.connect(owner, autocommit=True) as c:
        for dd in (far, near):
            c.execute("INSERT INTO krx_wk_day (wk_dd, dd, n) VALUES (%s::date, %s, 0)", (dd, dd))
            c.execute("INSERT INTO krx_idx (sid, trd_dd, close_x100) VALUES (1, %s::date, 100)", (dd,))
    res, old = _write_idx(w, days[-1], cold=None)
    assert res.ok and old == 0                                                 # coverage 없음(72시간 전·콜드 꺼짐) — 지우지 않는다
    res, old = _write_idx(w, days[-1], cold=t.strftime("%Y%m%d"))
    assert res.ok and old == 1
    assert [r[0] for r in _q(owner, "SELECT to_char(trd_dd, 'YYYYMMDD') FROM krx_idx WHERE trd_dd < %s::date", (days[0],))] == [near]


def test_index_vacuum_needs_maintain_and_says_so_when_missing(pg17, monkeypatch, caplog):
    idx_schema(pg17)
    w = _grant_writer(pg17, maintain=False, view=True, idx=True)
    monkeypatch.setenv("DATABASE_URL", w)
    with caplog.at_level(logging.WARNING, logger="open_proxy_mcp.services.krx_refresher"):
        krx_refresher._vacuum("krx_idx")
    msgs = [r.getMessage() for r in caplog.records if "VACUUM" in r.getMessage()]
    assert msgs and "MAINTAIN" in msgs[0] and "krx_idx" in msgs[0]
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(f"GRANT MAINTAIN ON krx_idx TO {WRITER}")
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="open_proxy_mcp.services.krx_refresher"):
        krx_refresher._vacuum("krx_idx")
    assert not [r for r in caplog.records if "VACUUM" in r.getMessage()]
