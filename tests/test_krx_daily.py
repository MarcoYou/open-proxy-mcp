"""krx_daily 쓰기·조회 — 네트워크 0 · 운영 DB 0. SQLite 와 가짜 연결로 검사한다.

뷰·권한·트랜잭션처럼 Postgres 가 있어야 하는 부분은 `test_krx_daily_pg.py`(Docker, 없으면 건너뜀)가 맡는다.
"""
from __future__ import annotations

import sys
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from open_proxy_mcp import krx_daily  # noqa: E402


def _row(code, close="71,000", **kw):
    base = {"ISU_CD": code, "TDD_CLSPRC": close, "TDD_OPNPRC": "70,500", "TDD_HGPRC": "71,500", "TDD_LWPRC": "70,000",
            "ACC_TRDVOL": "12,345,678", "ACC_TRDVAL": "876,543,210,000", "LIST_SHRS": "5,919,637,922",
            "MKTCAP": "420,294,092,462,000", "MKT_NM": "KOSPI", "FLUC_RT": "1.42", "CMPPREVDD_PRC": "1,000"}
    base.update(kw)
    return base


# ── 레코드 만들기 ──────────────────────────────────────────────────────────

def test_daily_records_carry_every_krx_field_in_column_order():
    recs = krx_daily.daily_records("20261016", [_row("005930")], [_row("247540", "250,000", CMPPREVDD_PRC="-1,500")])
    assert krx_daily.DAILY_COLUMNS == ("trd_dd", "ticker", "market", "open", "high", "low", "close", "volume", "value", "list_shrs", "cmp")
    assert recs == [
        ("20261016", "005930", "KS", 70_500, 71_500, 70_000, 71_000, 12_345_678, 876_543_210_000, 5_919_637_922, 1_000),
        ("20261016", "247540", "KQ", 70_500, 71_500, 70_000, 250_000, 12_345_678, 876_543_210_000, 5_919_637_922, -1_500),
    ]
    assert all(len(r) == len(krx_daily.DAILY_COLUMNS) for r in recs)


def test_daily_records_do_not_store_derived_values():
    """시총·등락률은 저장하지 않는다 — 뷰가 계산한다(한 행 안의 곱셈·나눗셈은 계산). 공표 전일대비(cmp)는 사실값이라 저장한다."""
    assert "mktcap" not in krx_daily.DAILY_COLUMNS and "chg_pct" not in krx_daily.DAILY_COLUMNS
    assert "MKTCAP" not in krx_daily.COPY_PX_SQL and "fluc" not in krx_daily.COPY_PX_SQL.lower()


def test_daily_records_tolerate_blank_dash_and_missing_ids():
    recs = krx_daily.daily_records("20261016", [
        _row("005930", "-", TDD_OPNPRC="", ACC_TRDVOL=None, CMPPREVDD_PRC="-"),   # 값이 비었거나 '-' 면 NULL
        _row(None), _row(""), {"TDD_CLSPRC": "1"},                                # 종목코드가 없는 행은 버린다
    ], [])
    assert len(recs) == 1
    assert recs[0][3] is None and recs[0][6] is None and recs[0][7] is None and recs[0][10] is None
    assert krx_daily.daily_records("20261016", [], []) == []
    assert krx_daily.daily_records("20261016", None, None) == []


# ── 보관 창 (최신 거래일 − 달력 1년 − 1주) ───────────────────────────────────────

@pytest.mark.parametrize("latest,lo", [
    ("20261016", "20251009"),       # 운영 실측: 이 창에 거래일 249일
    ("20261019", "20251012"),
    ("20280229", "20270221"),       # 윤일 → 평년 2월 28일에서 7일
    ("20270105", "20251229"),       # 연초 — 해를 넘는다
])
def test_window_lo_is_one_calendar_year_and_one_week_back(latest, lo):
    assert krx_daily.window_lo(latest) == lo
    assert krx_daily.KEEP_YEARS == 1 and krx_daily.KEEP_EXTRA_DAYS == 7


@pytest.mark.parametrize("dd,today,n", [
    ("20260702", "20260703", 1),    # 다음 날 아침 — 게시 전 빈 응답과 휴장을 가를 수 없다
    ("20260702", "20260706", 2),    # 금·월 — 판정할 수 있다
    ("20261009", "20261012", 1),    # 금요일 휴장 → 월요일 아침은 평일 1일
    ("20261009", "20261013", 2),
    ("20261016", "20261016", 0),
])
def test_weekdays_after_counts_weekdays_up_to_today(dd, today, n):
    assert krx_daily.weekdays_after(dd, today) == n


@pytest.mark.parametrize("ks,kq,since,cont,want", [
    (False, True, 5, None, "trading"),            # 한 시장이라도 행이 있으면 거래일
    (True, False, 5, None, "trading"),
    (True, True, 1, (2700, 3), "unknown"),        # 평일 1일 — 게시 전일 수 있다(20260702 교훈)
    (True, True, 2, (2700, 3), "closed"),         # 다음 거래일 기준가가 앞 거래일 종가와 이어진다(어긋남은 기준가 조정 몇 종목)
    (True, True, 2, (2700, 135), "closed"),       # 5% 까지
    (True, True, 2, (2766, 2546), "trading"),     # 20260703 실측: 거래일을 빠뜨리면 거의 모든 종목이 어긋난다
    (True, True, 2, (2700, 600), "unknown"),      # 그 사이 — 정하지 않는다
    (True, True, 2, None, "unknown"),             # 다음 거래일이 아직 없거나 공표 대비가 없음 — 미룬다(KRX 오류도 빈 응답이다)
    (True, True, 9, None, "unknown"),             # 평일이 많이 지나도 연속성을 못 보면 휴장으로 굳히지 않는다
    (True, True, 2, (50, 2), "unknown"),          # 비교 종목이 너무 적으면 연속성은 근거가 아니다 — 미룬다
])
def test_closed_verdict_never_trusts_a_single_empty_response(ks, kq, since, cont, want):
    assert krx_daily.closed_verdict(ks, kq, since, cont) == want


def test_lock_key_is_a_fixed_positive_bigint():
    assert krx_daily.REFRESH_LOCK_KEY == int.from_bytes(b"OPMKRXD1", "big")
    assert 0 < krx_daily.REFRESH_LOCK_KEY < 2**63


# ── write_daily: 가짜 연결 ─────────────────────────────────────────────────

def _norm(sql):
    return " ".join(sql.split())


class _Copy:
    def __init__(self, conn):
        self.c = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def write_row(self, row):
        self.c.copied.append(tuple(row))


class _Cur:
    def __init__(self, conn):
        self.c = conn
        self.rowcount = -1
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=()):
        n = _norm(sql)
        if self.c.fail_daily and "krx_ticker" in n:
            raise RuntimeError('relation "krx_ticker" does not exist')
        self.c.log.append(("execute", n, params))
        self.rowcount, self._rows = -1, []
        if n == _norm(krx_daily.TIDS_SQL):
            self._rows = [(t, self.c.tids.setdefault(t, len(self.c.tids) + 1)) for t in params[0]]
        elif n == _norm(krx_daily.LATEST_DAY_SQL):
            self._rows = [(self.c.latest,)]
        elif n == _norm(krx_daily.PRUNE_CANDIDATES_SQL):
            self._rows = list(self.c.cands)
        elif n == _norm(krx_daily.PRUNE_PX_SQL):
            self.rowcount = 5 * len(params[0])
        return self

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows

    def copy(self, sql):
        self.c.log.append(("copy", _norm(sql), None))
        return _Copy(self.c)


class _Tx:
    def __init__(self, conn):
        self.c = conn

    def __enter__(self):
        self.c.log.append(("savepoint",))
        return self

    def __exit__(self, et, ev, tb):
        self.c.log.append(("release",) if et is None else ("rollback_to_savepoint",))
        return False                                   # 예외는 그대로 올린다(psycopg 와 같다)


class FakeConn:
    """psycopg 연결 대역. tids = 이미 번호가 있는 종목(새 종목은 차례로 번호), latest = 달력 최신일, cands = 보관 창 밖 달력 행."""

    def __init__(self, fail_daily=False, tids=None, latest="20261016", cands=()):
        self.log, self.commits, self.fail_daily = [], 0, fail_daily
        self.tids = dict(tids or {})
        self.latest, self.cands, self.copied = latest, list(cands), []

    def cursor(self):
        return _Cur(self)

    def execute(self, sql, params=()):
        return _Cur(self).execute(sql, params)

    def transaction(self):
        return _Tx(self)

    def commit(self):
        self.commits += 1
        self.log.append(("commit",))

    def rollback(self):
        self.log.append(("rollback",))

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _sqls(c):
    return [e[1] for e in c.log if e[0] in ("execute", "copy")]


def _entry(c, sql):
    return next(e for e in c.log if e[0] == "execute" and e[1] == _norm(sql))


def test_write_daily_numbers_new_tickers_then_calendar_then_copies_in_ticker_number_order():
    c = FakeConn(tids={"247540": 7, "005930": 9})                      # 이미 번호가 있는 두 종목 + 새 종목 하나
    ok, n, pruned = krx_daily.write_daily(c, "20261016", [_row("005930"), _row("0001A0", "9,000", CMPPREVDD_PRC="-100")],
                                          [_row("247540", "250,000")])
    assert (ok, n, pruned) == (True, 3, 0)
    sqls = _sqls(c)
    assert sqls[:5] == [_norm(krx_daily.NEW_TICKERS_SQL), _norm(krx_daily.TIDS_SQL), _norm(krx_daily.DAY_UPSERT_SQL),
                        _norm(krx_daily.DAY_ROWS_DELETE_SQL), _norm(krx_daily.COPY_PX_SQL)]
    assert sqls[5] == _norm(krx_daily.WK_CLOSE_SQL)                    # 주 닫는 날을 기록마다 다시 계산
    new = _entry(c, krx_daily.NEW_TICKERS_SQL)
    assert new[2] == (["0001A0", "005930", "247540"],)                 # 새 종목만 넣는 문장 — 후보는 정렬해서 넘긴다
    assert "WHERE NOT EXISTS" in krx_daily.NEW_TICKERS_SQL             # ON CONFLICT 만 쓰면 번호 생성기 값을 매일 태운다
    day = _entry(c, krx_daily.DAY_UPSERT_SQL)
    assert day[2][1:] == ("20261016", 3) and str(day[2][0]) == "2026-10-16"
    assert [r[9] for r in c.copied] == [3, 7, 9]                       # 종목 번호순(같은 날 행이 물리적으로 모인다) — 새 종목은 대역이 3번
    by_tid = {r[9]: r for r in c.copied}
    assert by_tid[3][:8] == (876_543_210_000, 12_345_678, 5_919_637_922, 70_500, 71_500, 70_000, 9_000, -100)   # 영숫자 코드
    assert by_tid[7][10] == "Q" and by_tid[9][10] == "S" and str(by_tid[9][8]) == "2026-10-16"
    assert c.commits == 0                                              # 커밋은 호출자(krx_weekly 와 같은 트랜잭션)가 한다
    assert c.log[0] == ("savepoint",) and c.log[-1] == ("release",)


def test_write_daily_same_ticker_twice_keeps_the_last_row():
    c = FakeConn()
    ok, n, _ = krx_daily.write_daily(c, "20261016", [_row("005930", "1"), _row("005930", "2")], [])
    assert (ok, n) == (True, 1) and [r[6] for r in c.copied] == [2]


def test_prune_deletes_only_closed_or_cold_days_and_warns_about_the_rest(caplog):
    """보관 창 밖의 날 — 휴장일·콜드 보관본에 들어간 날만 지운다. 들어가지 않은 거래일은 남기고 경고한다(지운 뒤에는 되살릴 곳이 없다)."""
    cands = [("20251008", 0, True, False),        # 휴장(행 없음)
             ("20251010", 2764, False, True),     # 콜드에 있음
             ("20251013", 2765, False, False),    # 콜드에 없음 → 남긴다
             ("20251014", 2765, False, False)]
    c = FakeConn(latest="20261021", cands=cands)                       # 창 첫날 20251014 미만이 후보(대역은 그대로 돌려준다)
    with caplog.at_level("WARNING"):
        ok, n, pruned = krx_daily.write_daily(c, "20261021", [_row("005930")], [])
    assert ok and pruned == 10                                         # 대역: 지운 날 × 5행
    px = _entry(c, krx_daily.PRUNE_PX_SQL)
    assert [str(d) for d in px[2][0]] == ["2025-10-08", "2025-10-10"]
    day = _entry(c, krx_daily.PRUNE_DAY_SQL)
    assert [str(d) for d in day[2][0]] == ["2025-10-08", "2025-10-10"]
    cand = _entry(c, krx_daily.PRUNE_CANDIDATES_SQL)
    assert str(cand[2][0]) == "2025-10-14"                             # window_lo(20261021)
    # 행이 있는 날(콜드)만, 지우기 직전에, 자기 SAVEPOINT 안에서 BRIN 요약을 지운다(휴장일은 행이 없어 뺀다)
    des = _entry(c, krx_daily.PRUNE_DESUMMARIZE_SQL)
    assert des[2][:3] == ("krx_px_dd_brin", 16, 16) and [str(d) for d in des[2][3]] == ["2025-10-10"]
    i = c.log.index(des)
    assert c.log[i - 1] == ("savepoint",) and c.log[i + 1] == ("release",) and c.log.index(px) > i
    assert any("콜드 보관본에 없어 지우지 않았다" in r.message and "20251013~20251014" in r.message for r in caplog.records)


def test_prune_with_nothing_outside_the_window_deletes_nothing():
    c = FakeConn(latest="20261016", cands=[])
    ok, _, pruned = krx_daily.write_daily(c, "20261016", [_row("005930")], [])
    assert ok and pruned == 0 and not any(x in (_norm(krx_daily.PRUNE_PX_SQL), _norm(krx_daily.PRUNE_DAY_SQL),
                                                _norm(krx_daily.PRUNE_DESUMMARIZE_SQL)) for x in _sqls(c))


def test_prune_keeps_deleting_when_the_brin_desummarize_fails(caplog):
    """BRIN 요약 지우기는 성능 장치다 — 권한 등으로 실패해도 그 SAVEPOINT 만 되돌리고 보관 창 정리는 그대로 한다."""
    c = FakeConn(latest="20261021", cands=[("20251010", 2764, False, True)])
    real = c.execute

    def failing(sql, params=()):
        if _norm(sql) == _norm(krx_daily.PRUNE_DESUMMARIZE_SQL):
            raise RuntimeError("must be owner of index krx_px_dd_brin")
        return real(sql, params)
    c.execute = failing
    with caplog.at_level("WARNING"):
        ok, _, pruned = krx_daily.write_daily(c, "20261021", [_row("005930")], [])
    assert ok and pruned == 5 and ("rollback_to_savepoint",) in c.log
    assert any("BRIN 요약 지우기 실패" in r.message for r in caplog.records)


def test_write_daily_failure_never_raises_so_the_weekly_write_still_commits(caplog):
    c = FakeConn(fail_daily=True)
    with caplog.at_level("ERROR"):
        assert krx_daily.write_daily(c, "20261016", [_row("005930")], []) == (False, 0, 0)
    assert ("rollback_to_savepoint",) in c.log
    assert any("krx_daily 기록 실패" in r.message for r in caplog.records)    # 조용히 삼키지 않는다


def test_write_daily_with_no_rows_touches_nothing():
    c = FakeConn()
    assert krx_daily.write_daily(c, "20261016", [], []) == (False, 0, 0)
    assert c.log == []


# ── _krx_db_upsert: krx_daily 가 주(主), 닫힌 주만 krx_weekly(부) — 한 트랜잭션 ─────────────

def _patch_connect(monkeypatch, conn):
    import psycopg
    seen = {}

    def fake_connect(url, **kw):
        seen["url"], seen["kw"] = url, kw
        return conn
    monkeypatch.setattr(psycopg, "connect", fake_connect)
    return seen


def _patch_sync(monkeypatch, conn, result):
    """주간 닫기(`krx_daily.sync_weekly`)를 기록만 하는 대역으로 — 실제 SQL 은 pg17 시험(test_krx_weekly_close_pg.py)이 본다."""
    from open_proxy_mcp.services import price_multiple_data as pmd

    def fake_sync(c):
        assert c is conn                                      # 일별 기록과 같은 연결(같은 트랜잭션)
        conn.log.append(("sync_weekly",))
        return result
    monkeypatch.setattr(pmd._krx_daily, "sync_weekly", fake_sync)


def test_upsert_writes_daily_then_closes_weeks_before_the_single_commit(monkeypatch):
    """261018(재설계 5-1 쓰는 쪽): 매 거래일 krx_weekly 의 이번 주 행을 지우고 다시 넣던 것을 멈췄다 — 이 함수는 krx_weekly 에 직접
    아무것도 쓰지 않고, 일별 기록 뒤 같은 트랜잭션에서 주간 닫기를 부른다(닫힌 주만 옮겨 적는다)."""
    from open_proxy_mcp.services import price_multiple_data as pmd
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/x")
    c = FakeConn()
    _patch_connect(monkeypatch, c)
    _patch_sync(monkeypatch, c, krx_daily.WeeklySync(True, ("20261009",), 0))
    res = pmd._krx_db_upsert("20261015", [_row("005930")], [_row("247540", "250,000")])
    assert res == pmd.KrxWrite(True, True, ("20261009",)) and res.daily_ok
    # 바깥 트랜잭션 하나(첫 savepoint·마지막 release) 안에 일별 기록(SAVEPOINT)과 주간 닫기 — 한 커밋
    kinds = [e[0] for e in c.log]
    assert kinds[:2] == ["savepoint", "savepoint"] and kinds[-3:] == ["release", "sync_weekly", "release"]
    assert "copy" in kinds and len(c.copied) == 2
    assert not any("krx_weekly" in (e[1] if len(e) > 1 and isinstance(e[1], str) else "") for e in c.log)   # 이번 주 행을 지우고 다시 넣지 않는다


def test_upsert_commits_the_daily_write_even_when_the_weekly_close_fails(monkeypatch):
    """주간 닫기는 부(副)다 — 실패해도 일별 기록(서빙 기준일)은 커밋되고 성공으로 보고한다. 다음 기록이 다시 닫는다."""
    from open_proxy_mcp.services import price_multiple_data as pmd
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/x")
    c = FakeConn()
    _patch_connect(monkeypatch, c)
    _patch_sync(monkeypatch, c, krx_daily.WeeklySync(False))
    assert pmd._krx_db_upsert("20261016", [_row("005930")], [_row("247540")]) == pmd.KrxWrite(True, False, ())
    assert c.log[0] == ("savepoint",) and c.log[-1] == ("release",)          # 바깥 트랜잭션은 그대로 커밋된다


def test_upsert_commits_nothing_when_the_daily_write_fails(monkeypatch):
    """일별 기록이 주(主)다 — 실패하면 주간 닫기도 부르지 않고 아무것도 커밋하지 않는다(서빙 기준일과 주간 표가 어긋나지 않게)."""
    from open_proxy_mcp.services import price_multiple_data as pmd
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/x")
    c = FakeConn(fail_daily=True)
    _patch_connect(monkeypatch, c)
    _patch_sync(monkeypatch, c, krx_daily.WeeklySync(True))
    assert pmd._krx_db_upsert("20261015", [_row("005930")], [_row("247540")]) == pmd.KrxWrite(False)
    assert ("rollback_to_savepoint",) in c.log and ("sync_weekly",) not in c.log
    assert not c.copied                                                       # 일별 행도 하나 남지 않는다(SAVEPOINT 로 되돌림)


def test_upsert_returns_false_without_db_or_rows_and_on_connection_error(monkeypatch, caplog):
    from open_proxy_mcp.services import price_multiple_data as pmd
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert pmd._krx_db_upsert("20261015", [_row("005930")], []) == pmd.KrxWrite(False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/x")
    assert pmd._krx_db_upsert("20261015", [], []) == pmd.KrxWrite(False)
    assert pmd._krx_db_upsert("20261015", [{"TDD_CLSPRC": "1"}], []) == pmd.KrxWrite(False)        # 종목코드 없는 행뿐

    import psycopg

    def boom(*a, **k):
        raise psycopg.OperationalError("connection reset")
    monkeypatch.setattr(psycopg, "connect", boom)
    with caplog.at_level("WARNING"):
        assert pmd._krx_db_upsert("20261015", [_row("005930")], []) == pmd.KrxWrite(False)
    assert any("KRX 스냅샷 기록 실패" in r.message for r in caplog.records)


# ── plan_weeks: 어느 주가 닫혔나 (순수 함수) ────────────────────────────────────

def test_plan_weeks_closes_a_week_on_its_friday_or_when_a_later_week_appears():
    W = krx_daily.WeekPlan
    # 이번 주(목요일까지) — 아직 안 닫힘. 지난주는 금요일이 있어 닫힘.
    assert krx_daily.plan_weeks(["20261008", "20261012", "20261013", "20261014", "20261015"]) == [
        W("20261005", "20261011", "20261008", True),        # 금(10/9) 휴장 — 더 늦은 주가 있어 목요일로 닫힌다(R2)
        W("20261012", "20261018", "20261015", False),
    ]
    # 금요일이 저장되면 그 주가 바로 닫힌다(R1 — 토요일 아침 기록)
    assert krx_daily.plan_weeks(["20261015", "20261016"]) == [W("20261012", "20261018", "20261016", True)]
    assert krx_daily.plan_weeks([]) == []


def test_plan_weeks_splits_the_iso_week_at_the_year_boundary():
    """2025-12-30(화)과 2026-01-02(금)은 같은 ISO 주(2026-W01)다. 한 주로 묶으면 12/30 이 지워진다 — 연도에서 나눈다."""
    W = krx_daily.WeekPlan
    assert krx_daily.plan_weeks(["20251229", "20251230"]) == [W("20251229", "20251231", "20251230", False)]   # 12/31 은 휴장 — 아직 열림
    assert krx_daily.plan_weeks(["20251229", "20251230", "20260102"]) == [
        W("20251229", "20251231", "20251230", True),         # 다음 해 첫 거래일이 저장되면 그해 마지막 거래일로 닫힌다
        W("20260101", "20260104", "20260102", True),         # 1/2 는 금요일 — 그 주의 마지막 평일이라 바로 닫힌다
    ]
    # 2024-12-30(월) · 2025-01-02(목): 연말 주는 월요일 하루, 새해 주는 목·금
    assert krx_daily.plan_weeks(["20241230", "20250102"]) == [
        W("20241230", "20241231", "20241230", True), W("20250101", "20250105", "20250102", False)]


def test_weekly_with_latest_only_names_weekly_columns():
    sql = krx_daily.weekly_with_latest("price_dd, ticker, market")
    assert sql.startswith("(SELECT price_dd, ticker, market FROM krx_weekly UNION ALL SELECT d.trd_dd AS price_dd,")
    assert "d.trd_dd = (SELECT max(dd) FROM krx_day WHERE n > 0)" in sql and "> (SELECT COALESCE(max(price_dd), '') FROM krx_weekly)" in sql
    assert "d.close * d.list_shrs AS mktcap" in krx_daily.WEEKLY_WITH_LATEST
    with pytest.raises(ValueError):
        krx_daily.weekly_with_latest("price_dd, volume")


# ── read_quote: 한 왕복의 결과를 해석한다 ────────────────────────────────────────

def _quote_result(dd="20261016", lo="20261008", hi="20261016", present=True, with_row=True, prev=100_000, prev_dd="20261015"):
    row = ("KS", 101_000, 104_000, 100_500, 103_000, 777, 99_999, 10, 1_030_000, prev_dd if prev else None, prev,
           3_000 if prev else None, 3.0 if prev else None, 3_000, 3.0) if with_row else (None,) * 15
    return [(dd, lo, hi, present) + row]


def test_read_quote_maps_the_single_row(monkeypatch):
    calls = []
    monkeypatch.setattr("open_proxy_mcp.db.pg_rows", lambda sql, params=(): calls.append((sql, params)) or _quote_result())
    got = krx_daily.read_quote("005930", " 20261016 ")
    assert len(calls) == 1 and calls[0][1] == ("20261016", "005930")        # 한 번만 묻는다 · as_of 앞뒤 공백 제거
    assert got.ok and got.day == "20261016" and got.window == ("20261008", "20261016") and got.day_present
    assert got.row["close"] == 103_000 and got.row["prev_close"] == 100_000 and got.row["market"] == "KS"
    assert got.row["prev_dd"] == "20261015"
    assert set(got.row) >= {"open", "high", "low", "close", "volume", "value", "list_shrs", "mktcap", "prev_dd", "prev_close", "chg", "chg_pct",
                            "krx_chg", "krx_chg_pct"}
    assert got.row["krx_chg"] == 3_000


def test_read_quote_row_none_when_the_day_has_no_such_ticker(monkeypatch):
    monkeypatch.setattr("open_proxy_mcp.db.pg_rows", lambda sql, params=(): _quote_result(with_row=False))
    got = krx_daily.read_quote("000000", "")
    assert got.ok and got.day_present and got.row is None


def test_read_quote_not_ok_when_db_unreadable(monkeypatch):
    monkeypatch.setattr("open_proxy_mcp.db.pg_rows", lambda sql, params=(): None)     # 미설정·장애·표 없음
    got = krx_daily.read_quote("005930", "")
    assert got.ok is False and got.row is None and not got.day_present


def test_read_quote_sql_is_one_statement_with_two_params():
    assert krx_daily.QUOTE_SQL.count("%s") == 2
    assert ";" not in krx_daily.QUOTE_SQL
    assert "krx_daily_v" in krx_daily.QUOTE_SQL and "LATERAL" in krx_daily.QUOTE_SQL
    # 창·보관 여부는 달력에서 — 호환 뷰 위의 min/max·EXISTS 는 1년치 조인을 다 푼다(운영 실측 304 ms → 0.23 ms)
    assert "FROM krx_day WHERE n > 0" in krx_daily.QUOTE_SQL and "FROM krx_daily x" not in krx_daily.QUOTE_SQL
    assert "v.krx_chg, v.krx_chg_pct" in krx_daily.QUOTE_SQL


def test_ddl_script_prints_by_default_and_never_applies_without_the_flag(capsys, monkeypatch):
    """스키마 스크립트는 기본이 「출력만」이다 — 실수로 DB 를 만지지 않는다. --no-tx 는 BEGIN/COMMIT 없는 본문."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("krx_daily_ddl", ROOT / "scripts" / "krx_daily_ddl.py")
    ddl = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ddl)
    applied = []
    monkeypatch.setattr(ddl, "apply", lambda url, mode="full": applied.append((url, mode)))
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@localhost/x")
    monkeypatch.setattr(sys, "argv", ["krx_daily_ddl.py"])
    assert ddl.main() == 0
    out = capsys.readouterr().out
    assert out.startswith("BEGIN;") and out.rstrip().endswith("COMMIT;") and not applied
    for needle in ("CREATE TABLE IF NOT EXISTS krx_px", "CREATE TABLE IF NOT EXISTS krx_day", "CREATE TABLE IF NOT EXISTS krx_ticker",
                   "CREATE OR REPLACE VIEW krx_daily ", "CREATE OR REPLACE VIEW krx_daily_v ", "CREATE OR REPLACE VIEW price_latest ",
                   "ENABLE ROW LEVEL SECURITY", "CREATE POLICY krx_px_read", "security_invoker = true",
                   "REVOKE ALL ON krx_ticker, krx_day, krx_px FROM anon, authenticated",
                   "REVOKE ALL ON krx_daily, krx_daily_v, price_latest FROM anon, authenticated"):
        assert needle in out, needle
    assert "_s " not in out and "krx_daily_legacy" not in out                     # 새 DB 판에는 그림자·교체가 없다
    grants = [ln for ln in out.splitlines() if ln.upper().startswith("GRANT")]
    assert grants == ["GRANT SELECT ON krx_ticker, krx_day, krx_px TO opm_ro;",
                      "GRANT SELECT ON krx_daily, krx_daily_v, price_latest TO opm_ro;"]     # 새로 주는 권한은 읽기 전용 역할 하나뿐
    assert not any(role in ln for ln in grants for role in ("anon", "authenticated"))
    monkeypatch.setattr(sys, "argv", ["krx_daily_ddl.py", "--no-tx"])
    assert ddl.main() == 0
    body = capsys.readouterr().out
    assert "BEGIN" not in body and "COMMIT" not in body and body.startswith("-- 종목 번호표") and not applied
    monkeypatch.setattr(sys, "argv", ["krx_daily_ddl.py", "--shadow"])
    assert ddl.main() == 0
    sh = capsys.readouterr().out
    assert "CREATE OR REPLACE VIEW krx_daily_s " in sh and "CREATE OR REPLACE VIEW krx_daily " not in sh
    monkeypatch.setattr(sys, "argv", ["krx_daily_ddl.py", "--switch"])
    assert ddl.main() == 0
    sw = capsys.readouterr().out
    for needle in ("pg_advisory_xact_lock(5715152913032561713)", "ALTER TABLE krx_daily RENAME TO krx_daily_legacy",
                   "CREATE VIEW krx_daily ", "DROP VIEW IF EXISTS price_latest_s, krx_daily_v_s, krx_daily_s",
                   "UPDATE krx_day d SET wk_close", "G2 관문 실패"):
        assert needle in sw, needle
    assert str(krx_daily.REFRESH_LOCK_KEY) in sw
    monkeypatch.setattr(sys, "argv", ["krx_daily_ddl.py", "--apply"])
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None, raising=False)
    assert ddl.main() == 0 and applied == [("postgresql://u:p@localhost/x", "full")]     # 명시했을 때만 적용
    monkeypatch.setattr(sys, "argv", ["krx_daily_ddl.py", "--apply", "--switch"])
    assert ddl.main() == 0 and applied[-1] == ("postgresql://u:p@localhost/x", "switch")
