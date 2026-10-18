# -*- coding: utf-8 -*-
"""krx_weekly 를 최근 3년 + 연말 주로 줄이기 전에 고친 배치들 — 잘린 뒤에도 옛 구간을 건드리지 않는지 고정한다.

대상: krx_listing(증분) · krx_cap_agg(하한 가드·안전 삭제) · market_val_weekly(백필 가드).
(감사 목록 밖에서 추가로 고친 krx_stock_flags · adj_factor_v3 는 test_krx_weekly_event_guards.py)
network 0 · DB 0 — SQL 문자열로 응답하는 가짜 연결을 쓴다.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import sys

import pytest

from open_proxy_mcp import krx_retention as R
from scripts import krx_cap_agg as C
from scripts import krx_listing as L
from scripts import market_val_weekly as W


# ── 가짜 연결 ───────────────────────────────────────────────────────────────────
class _Res:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class _Copy:
    def __init__(self, con):
        self.con = con

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def write_row(self, rec):
        self.con.copied.append(rec)


class _Cur:
    def __init__(self, con):
        self.con = con
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.con.statements.append((sql, params))
        if sql.lstrip().upper().startswith("DELETE"):
            self.rowcount = 7

    def executemany(self, sql, seq):
        self.con.many.append((sql, list(seq)))

    def copy(self, sql):
        return _Copy(self.con)


class FakeCon:
    """handler(sql, params) → 행 목록. 실행된 모든 문장을 `statements` 에, executemany 를 `many` 에 쌓는다."""

    def __init__(self, handler):
        self.handler = handler
        self.statements: list[tuple] = []
        self.many: list[tuple] = []
        self.copied: list[tuple] = []
        self.committed = 0
        self.autocommit = False
        self.closed = False

    def execute(self, sql, params=None):
        self.statements.append((sql, params))
        return _Res(self.handler(sql, params))

    def cursor(self):
        return _Cur(self)

    def commit(self):
        self.committed += 1

    def rollback(self):
        pass

    def close(self):
        self.closed = True

    def sqls(self) -> list[str]:
        return [" ".join(s.split()) for s, _ in self.statements]


def _norm(sql: str) -> str:
    return " ".join(sql.split())


# ═══════════════════════════════════════════════════════════════════════════════
# 1. krx_listing — 증분
# ═══════════════════════════════════════════════════════════════════════════════
def test_listing_window_start_is_a_constant_and_daily_query_is_bounded():
    assert L.WINDOW_LO == R.KRX_WEEKLY_ORIGIN_DD == "20151230"
    # 일상 경로가 쓰는 쿼리는 하한(>=)이 걸려 있고, 전량 GROUP BY 는 비교 전용(Q_FULL)으로만 남는다
    assert "WHERE price_dd >= %(start)s" in L.Q_RECENT and "price_dd >=" not in L.Q_FULL and "price_dd <=" not in L.Q_FULL
    # 261018(재설계 5-1 쓰는 쪽): krx_weekly 는 닫힌 주만 담는다 — 이번 주 관측은 일별 저장분에서 같이 읽는다(같은 하한)
    # 261019: 하한은 사실값 표의 날짜 칸에 직접 건다(호환 뷰에 걸면 하한이 달력 쪽에 걸려 1년치 사실값을 다 훑는다)
    assert "FROM krx_px p JOIN krx_ticker k ON k.tid = p.tid WHERE p.trd_dd >= to_date(%(start)s, 'YYYYMMDD')" in _norm(L.Q_RECENT)


def test_listing_merge_one_only_shrinks_first_and_grows_last():
    # 새 종목
    assert L.merge_one(None, ("KS", "20260109", "20261016")) == ("KS", "20260109", "20261016", False)
    # 관측창 첫 주부터 보인 종목은 before_window
    assert L.merge_one(None, ("KS", "20151230", "20261016"))[3] is True
    # 기존 first 가 더 이르면 새 관측(최근 주)이 first 를 뒤로 밀지 못한다 — 표를 자른 뒤에도 값이 유지되는 이유
    assert L.merge_one(("KS", "20160108", "20260918"), ("KS", "20231020", "20261016")) == ("KS", "20160108", "20261016", False)
    # 더 이른 관측이 들어오면 앞당긴다
    assert L.merge_one(("KS", "20190105", "20260918"), ("KS", "20180105", "20261016"))[1] == "20180105"
    # 관측창 첫 주로 앞당겨지면 before_window 가 켜진다
    assert L.merge_one(("KS", "20190105", "20260918"), ("KS", "20151230", "20261016"))[3] is True
    # last 는 줄지 않는다 / market 은 더 늦게 본 쪽
    assert L.merge_one(("KQ", "20190105", "20261016"), ("KS", "20230106", "20260918")) == ("KQ", "20190105", "20261016", False)
    assert L.merge_one(("KQ", "20190105", "20260918"), ("KS", "20230106", "20261016"))[0] == "KS"


def test_listing_merge_rows_counts_and_leaves_unobserved_tickers_alone():
    existing = {"A": ("KS", "20151230", "20261009"), "B": ("KQ", "20200103", "20261009"),
                "DEL": ("KS", "20160108", "20200103")}                # DEL = 상장폐지 — 최근 창에 안 보인다
    observed = {"A": ("KS", "20260821", "20261016"),                  # last 만 늘었다
                "B": ("KQ", "20260821", "20261009"),                  # 그대로
                "NEW": ("KQ", "20261002", "20261016")}                # 신규
    changed, stats = L.merge_rows(existing, observed)
    assert stats == {"new": 1, "changed": 1, "same": 1}
    assert set(changed) == {"A", "NEW"} and "DEL" not in changed
    assert changed["A"] == ("KS", "20151230", "20261016", True)       # first 는 기존(20151230) 그대로
    after = L.overlay(existing, changed)
    assert after["DEL"] == ("KS", "20160108", "20200103", False) and len(after) == 4


def test_listing_plan_window():
    latest = "20261016"
    # 평소: 직전 구축 주에서 8주 전부터
    assert L.plan_window(latest, "20261016")[0] == "20260821"
    # 구축이 멎은 기간이 길어도(하한 안쪽이면) 그 시점부터 읽는다
    assert L.plan_window(latest, "20260206")[0] == "20251212"
    # 시작이 보존 하한보다 이르면 거절 — 사이 주가 이미 지워졌다
    with pytest.raises(L.WindowError, match="보존 하한"):
        L.plan_window(latest, "20231201")
    # 표가 비었고 krx_weekly 가 아직 안 잘렸으면(table_cut=False) 처음부터
    assert L.plan_window(latest, None, False)[0] == "20151230"
    # 표가 비었는데 krx_weekly 가 이미 잘렸으면 만들 수 없다(첫 관측일을 복원할 수 없다)
    with pytest.raises(L.WindowError, match="이미 보존 하한 이전을 잘랐다"):
        L.plan_window(latest, None, True)
    # 잘렸는지 모르면(None) 거절 — 안전한 쪽
    with pytest.raises(L.WindowError):
        L.plan_window(latest, None, None)


def _listing_handler(*, latest="20261016", existing=(), observed=(), table_cut=False):
    seen = {"recent": [], "full": 0, "cut_checks": []}

    def handler(sql, params):
        s = _norm(sql)
        if s == "SELECT max(price_dd) FROM krx_weekly":
            return [(latest,)]
        if s.startswith("SELECT ticker, market, first_seen_dd, last_seen_dd FROM krx_listing"):
            return list(existing)
        if sql is L.Q_RECENT:
            seen["recent"].append(params)
            return list(observed)
        if sql is L.Q_FULL:
            seen["full"] += 1
            return []
        if s.startswith("SELECT NOT EXISTS (SELECT 1 FROM krx_weekly w WHERE"):
            seen["cut_checks"].append(params)               # 표를 자르는 쪽과 같은 prune_where 조각으로 본다
            return [(table_cut,)]
        if s.startswith("SELECT count(*) FROM krx_listing"):
            return [(len(existing) + 1,)]
        return []
    return handler, seen


def _obs(n, first="20260821", last="20261016"):
    return [(f"{i:06d}", "KS", first, last) for i in range(n)]


def test_listing_build_reads_only_recent_weeks_and_upserts_changed_rows(monkeypatch, capsys):
    monkeypatch.setattr(L, "MIN_TICKERS", 3)
    existing = [("000000", "KS", "20151230", "20261009"), ("000001", "KS", "20200103", "20261009"),
                ("DELIST", "KQ", "20160108", "20200103")]
    observed = [("000000", "KS", "20260821", "20261016"), ("000001", "KS", "20260821", "20261016"),
                ("000002", "KQ", "20261002", "20261016")]
    handler, seen = _listing_handler(existing=existing, observed=observed)
    con = FakeCon(handler)
    assert L.build(con) == 0
    # 전량 GROUP BY 는 한 번도 안 돈다. 읽기 시작은 직전 구축 주(20261009)에서 56일 전
    assert seen["full"] == 0 and seen["recent"] == [{"start": "20260814"}]
    assert seen["cut_checks"] == []                                       # 표가 비지 않았으면 컷 판별(전체 훑기)을 하지 않는다
    (sql, rows), = con.many
    assert sql is L.UPSERT and "LEAST(krx_listing.first_seen_dd, EXCLUDED.first_seen_dd)" in sql \
        and "GREATEST(krx_listing.last_seen_dd, EXCLUDED.last_seen_dd)" in sql
    by_ticker = {r[0]: r for r in rows}
    assert set(by_ticker) == {"000000", "000001", "000002"}           # 상장폐지 종목은 쓰지 않는다
    assert by_ticker["000000"][2:] == ("20151230", "20261016", True, "20151230")   # first 유지 · before_window
    assert by_ticker["000002"][2:] == ("20261002", "20261016", False, "20151230")
    assert any(s.startswith("GRANT SELECT ON krx_listing") for s in con.sqls())
    assert "읽은 구간       : 20260814 ~ 20261016" in capsys.readouterr().out


def test_listing_build_skips_write_when_nothing_changed(monkeypatch):
    monkeypatch.setattr(L, "MIN_TICKERS", 2)
    existing = [("000000", "KS", "20151230", "20261016"), ("000001", "KS", "20200103", "20261016")]
    handler, _ = _listing_handler(existing=existing, observed=[(t, m, f, l) for t, m, f, l in existing])
    con = FakeCon(handler)
    assert L.build(con) == 0
    assert con.many == []


def test_listing_build_dry_neither_writes_nor_runs_ddl(monkeypatch):
    monkeypatch.setattr(L, "MIN_TICKERS", 2)
    existing = [("000000", "KS", "20151230", "20261009")]
    handler, _ = _listing_handler(existing=existing, observed=[("000000", "KS", "20260821", "20261016"), ("000001", "KQ", "20261002", "20261016")])
    con = FakeCon(handler)
    assert L.build(con, dry=True) == 0
    assert con.many == [] and not any(s.startswith("CREATE TABLE") for s in con.sqls())


def test_listing_build_refuses_a_thin_upstream(monkeypatch):
    """krx_weekly 를 자르는 30~60초(표가 비는 구간)에 도는 경우 — 아무것도 쓰지 않고 실패한다."""
    existing = [("000000", "KS", "20151230", "20261009")]
    handler, _ = _listing_handler(existing=existing, observed=_obs(10))      # MIN_TICKERS(1,500) 미만
    con = FakeCon(handler)
    assert L.build(con) == 1 and con.many == []
    handler, _ = _listing_handler(latest=None)                                # 표가 아예 비었다
    con = FakeCon(handler)
    assert L.build(con) == 1 and con.many == []


def test_listing_build_refuses_to_bootstrap_from_a_cut_table(monkeypatch, capsys):
    handler, seen = _listing_handler(existing=[], observed=_obs(2000), table_cut=True)
    con = FakeCon(handler)
    assert L.build(con) == 1
    assert con.many == [] and seen["recent"] == []                            # 읽지도 쓰지도 않는다
    assert seen["cut_checks"] == [("20231016", "20231016")]                   # 표를 자르는 쪽과 같은 조각(하한, 하한)으로 판별
    assert "이미 보존 하한 이전을 잘랐다" in capsys.readouterr().err


def test_listing_compare_refuses_on_a_cut_table(capsys):
    """잘린 표의 전량 재집계는 옛 구간을 못 봐 기준이 될 수 없다 — 차이가 나도 정상이므로 대조 자체를 거절한다."""
    handler, seen = _listing_handler(existing=[("000000", "KS", "20151230", "20261016")], table_cut=True)
    con = FakeCon(handler)
    assert L.compare(con) == 2
    assert seen["full"] == 0                                              # 전량 GROUP BY 를 돌리지도 않는다
    assert "이미 보존 하한 이전을 잘랐다" in capsys.readouterr().err


def test_listing_build_bootstraps_from_full_history(monkeypatch):
    monkeypatch.setattr(L, "MIN_TICKERS", 2)
    handler, seen = _listing_handler(existing=[], observed=[("000000", "KS", "20151230", "20261016"), ("000001", "KQ", "20200103", "20261016")], table_cut=False)
    con = FakeCon(handler)
    assert L.build(con) == 0
    assert seen["recent"] == [{"start": "20151230"}]
    (_, rows), = con.many
    assert {r[0]: r[4] for r in rows} == {"000000": True, "000001": False}    # before_window


# ═══════════════════════════════════════════════════════════════════════════════
# 2. krx_cap_agg — 하한 가드 · 안전 삭제
# ═══════════════════════════════════════════════════════════════════════════════
CUTOFF = "20231016"


def test_cap_agg_guard_since():
    assert C._guard_since(since="20260928", reason="최신 주 갱신", cutoff=CUTOFF, allow=False) == (True, "")
    assert C._guard_since(since=CUTOFF, reason="지정 구간", cutoff=CUTOFF, allow=False) == (True, "")   # 하한 당일은 안쪽
    ok, msg = C._guard_since(since="20200101", reason="지정 구간", cutoff=CUTOFF, allow=False)
    assert ok is False and "거절" in msg and "--allow-before-cutoff" in msg and CUTOFF in msg
    ok, msg = C._guard_since(since="20151230", reason="전 구간", cutoff=CUTOFF, allow=False)
    assert ok is False
    ok, msg = C._guard_since(since="20200101", reason="지정 구간", cutoff=CUTOFF, allow=True)
    assert ok is True and "원천이 온전한 날짜(해마다 마지막 주)만 다시 집계" in msg and "나머지 옛 집계는 그대로" in msg
    # 최초 적재는 지울 집계가 없어 통과
    assert C._guard_since(since="20151230", reason="최초 적재", cutoff=CUTOFF, allow=False)[0] is True


def test_cap_agg_delete_never_touches_rows_without_a_whole_source():
    sql, params = C._delete_stmt("20260928", CUTOFF, [])
    assert sql == "DELETE FROM krx_cap_agg WHERE price_dd >= %s" and params == ("20260928",)
    sql, params = C._delete_stmt("20200101", CUTOFF, ["20201230", "20221229"])
    assert "price_dd = ANY(%s)" in sql and "price_dd >= %s OR" in sql
    assert params == ("20200101", CUTOFF, ["20201230", "20221229"])


def test_cap_agg_source_filter_reads_only_whole_dates_below_the_cutoff():
    """종목별 연말 행 보존이라 하한 아래 날짜 대부분은 상장폐지 종목의 마지막 행뿐인 부분 행이다 — 그 날짜로 집계하면 옛 집계를 망친다."""
    assert C._src_filter("20260928", CUTOFF, [], "price_dd") == ("", ())              # 평소 쿼리는 그대로
    sql, params = C._src_filter("20200101", CUTOFF, ["20201230"], "k.price_dd")
    assert sql == " AND (k.price_dd >= %s OR k.price_dd = ANY(%s))" and params == (CUTOFF, ["20201230"])


def _cap_agg_handler(weekly_max="20261016", daily_max="20261016", agg_latest="20261016"):
    def handler(sql, params):
        s = _norm(sql)
        if s.startswith("SELECT DISTINCT snap_dd FROM wise_sector"):
            return [("20260821",)]
        if s.startswith("SELECT min(price_dd), max(price_dd) FROM krx_weekly"):
            return [("20151230", weekly_max)]
        if s.startswith("SELECT max(dd) FROM krx_day WHERE n > 0"):
            return [(daily_max,)]
        if s.startswith("SELECT max(sector_asof) FILTER (WHERE scheme <> 'market'), max(price_dd) FILTER (WHERE scheme = 'market') FROM krx_cap_agg"):
            return [("20260821", agg_latest)]
        if s.startswith("SELECT price_dd, market, count(*), sum(mktcap)::bigint FROM (SELECT price_dd, ticker, market, close, mktcap, list_shrs FROM krx_weekly UNION ALL"):
            return [("20261016", "KS", 900, 10 ** 15)]
        if s.startswith("SELECT k.price_dd, k.market,"):
            return [("20261016", "KS", "G10", "에너지", 10, 10 ** 12)]
        if s.startswith("SELECT max(price_dd) FROM krx_weekly WHERE price_dd >= %s AND price_dd < %s GROUP BY left(price_dd, 4)"):
            return [("20201230",), ("20221229",)]                       # 해마다 마지막 주 — 원천이 온전한 날짜
        return []
    return handler


def _run_cap_agg(monkeypatch, argv, capsys, **state):
    con = FakeCon(_cap_agg_handler(**state))
    monkeypatch.setattr(C.psycopg, "connect", lambda *a, **k: con)
    monkeypatch.setenv("DATABASE_URL", "unused")
    monkeypatch.setattr(sys, "argv", ["krx_cap_agg.py", *argv])
    rc = C.main()
    return rc, con, capsys.readouterr()


def test_cap_agg_daily_run_is_unchanged(monkeypatch, capsys):
    rc, con, out = _run_cap_agg(monkeypatch, [], capsys)
    assert rc == 0
    deletes = [(s, p) for s, p in con.statements if s.lstrip().upper().startswith("DELETE")]
    assert deletes == [("DELETE FROM krx_cap_agg WHERE price_dd >= %s", ("20261012",))]     # 최신 주 월요일부터, 종전과 같다
    # 평소 쿼리는 날짜 제한 조각이 붙지 않는다(종전 그대로)
    reads = [(_norm(s), p) for s, p in con.statements if "FROM krx_weekly" in s and ("sum(mktcap)" in s or "k.price_dd" in s)]
    assert reads and not any("ANY(%s)" in q for q, _ in reads) and reads[0][1] == ("20261012",)
    assert "보존 하한 20231016" in out.out


def test_cap_agg_reads_this_weeks_latest_day_from_the_daily_store(monkeypatch, capsys):
    """261018(재설계 5-1 쓰는 쪽): krx_weekly 는 닫힌 주만 담는다. 원천은 「닫힌 주 + 일별 최신일」이라 이번 주 임시 점이 들어오고,
    재계산은 그 주 첫날부터다. 보존 하한은 표를 자르는 쪽과 같게 krx_weekly(닫힌 주) 최신에서 잰다."""
    rc, con, out = _run_cap_agg(monkeypatch, [], capsys, weekly_max="20261016", daily_max="20261021", agg_latest="20261020")
    assert rc == 0
    deletes = [(s, p) for s, p in con.statements if s.lstrip().upper().startswith("DELETE")]
    assert deletes == [("DELETE FROM krx_cap_agg WHERE price_dd >= %s", ("20261019",))]     # 이번 주 월요일부터(어제의 임시 점을 갈아 끼운다)
    src = [_norm(s) for s, _ in con.statements if "sum(mktcap)" in s]
    assert src and "FROM krx_daily d WHERE d.trd_dd = (SELECT max(dd) FROM krx_day WHERE n > 0)" in src[0]
    assert "보존 하한 20231016" in out.out and "일별 최신 20261021" in out.out


def test_cap_agg_rebuilds_from_the_week_of_a_stale_provisional_point(monkeypatch, capsys):
    """배치가 며칠 걸러 집계 표의 최신 점이 지난주 임시 점(목요일)으로 남아 있으면, 그 주 첫날부터 다시 만들어 닫힌 날(금요일)로 바꾸고
    이번 주까지 채운다 — 종전처럼 원천 최신 주만 다시 만들면 지난주 임시 점이 남고 닫힌 금요일 집계가 빠진다."""
    rc, con, _ = _run_cap_agg(monkeypatch, [], capsys, weekly_max="20261016", daily_max="20261020", agg_latest="20261015")
    assert rc == 0
    deletes = [(s, p) for s, p in con.statements if s.lstrip().upper().startswith("DELETE")]
    assert deletes == [("DELETE FROM krx_cap_agg WHERE price_dd >= %s", ("20261012",))]


def test_cap_agg_refuses_a_since_before_the_cutoff(monkeypatch, capsys):
    rc, con, out = _run_cap_agg(monkeypatch, ["--since", "20200101"], capsys)
    assert rc == 2
    assert not any(s.lstrip().upper().startswith(("DELETE", "INSERT")) for s, _ in con.statements) and con.many == []
    assert "거절" in out.err and "--allow-before-cutoff" in out.err
    rc, con, out = _run_cap_agg(monkeypatch, ["--full"], capsys)             # 전 구간 재집계도 같다
    assert rc == 2 and con.many == []


def test_cap_agg_override_deletes_only_dates_that_can_be_recomputed(monkeypatch, capsys):
    rc, con, out = _run_cap_agg(monkeypatch, ["--since", "20200101", "--allow-before-cutoff"], capsys)
    assert rc == 0
    (sql, params), = [(s, p) for s, p in con.statements if s.lstrip().upper().startswith("DELETE")]
    assert "price_dd = ANY(%s)" in sql
    assert params == ("20200101", "20231016", ["20201230", "20221229"])
    # 다시 집계하는 원천도 같은 날짜로 제한된다(시장·섹터 쿼리 모두) — 부분 행 날짜로 옛 집계를 덮지 않는다
    reads = [(_norm(s), p) for s, p in con.statements if s.lstrip().startswith("SELECT") and "FROM krx_weekly" in s
             and ("sum(mktcap)" in s or "k.price_dd" in s)]
    assert reads and all("= ANY(%s)" in q and p[-2:] == ("20231016", ["20201230", "20221229"]) for q, p in reads)
    ye_q = [(s, p) for s, p in con.statements if "GROUP BY left(price_dd, 4)" in s]
    assert ye_q and ye_q[0][1] == ("20200101", "20230101")               # 온전한 날짜(해마다 마지막 주)는 하한이 속한 해(20230101) 이전에서 구한다
    assert "--allow-before-cutoff" in out.out and con.many               # 다시 넣기는 진행


# ═══════════════════════════════════════════════════════════════════════════════
# 3. market_val_weekly — 백필 가드
# ═══════════════════════════════════════════════════════════════════════════════
def test_backfill_week_candidates_split_the_iso_week_at_the_year_boundary():
    """연도를 넘는 ISO 주는 두 주로 — 종전엔 그 주의 금요일(다음 해)만 잡혀 그해 마지막 거래일이 백필되지 않았다(2019 연말 행 20191227)."""
    weeks = W._week_candidates(dt.date(2025, 12, 22), dt.date(2026, 1, 14))
    assert weeks == [["20251226", "20251225", "20251224", "20251223", "20251222"],
                     ["20251231", "20251230", "20251229"],              # 연말 주 — 12/31 휴장이면 12/30 이 잡힌다
                     ["20260102", "20260101"],                          # 새해 첫 주
                     ["20260109", "20260108", "20260107", "20260106", "20260105"]]
    w2019 = W._week_candidates(dt.date(2019, 12, 23), dt.date(2020, 1, 15))
    assert ["20191231", "20191230"] in w2019 and ["20200103", "20200102", "20200101"] in w2019


def test_backfill_split_weeks_by_cutoff():
    weeks = W._week_candidates(dt.date(2023, 9, 1), dt.date(2023, 11, 10))
    keep, skipped = W._split_weeks_by_cutoff(weeks, "20231016")
    assert [w[4] for w in skipped] == ["20230828", "20230904", "20230911", "20230918", "20230925", "20231002", "20231009"]
    assert keep[0][4] == "20231016" and keep[0][0] == "20231020"          # 하한 주(금 20231020)는 남는다
    # 금요일이 하한 이전이면 그 주 행은 어느 요일로 잡혀도 하한 이전이다
    assert W._split_weeks_by_cutoff([["20231013", "20231012", "20231011", "20231010", "20231009"]], "20231016")[1]


class _FixedDate(dt.date):
    @classmethod
    def today(cls):
        return cls(2026, 10, 17)


def _backfill_run(monkeypatch, since, **kwargs):
    done = [("20261016",), ("20261009",)]
    table_stat = (1_364_894, 563, "20151230", "20261016")

    def handler(sql, params):
        s = _norm(sql)
        if s.startswith("SELECT DISTINCT price_dd FROM krx_weekly"):
            return done
        if s.startswith("SELECT count(*), count(DISTINCT price_dd)"):
            return [table_stat]
        return []

    con = FakeCon(handler)
    fetched: list[str] = []

    async def fake_fetch(h, url, key, price_dd):
        fetched.append(price_dd)
        return [{"ISU_CD": "000001", "TDD_CLSPRC": "100", "MKTCAP": "1000", "LIST_SHRS": "10"}]

    monkeypatch.setattr(W.psycopg, "connect", lambda *a, **k: con)
    monkeypatch.setattr(W, "_backfill_fetch", fake_fetch)
    monkeypatch.setattr(W, "date", _FixedDate)
    monkeypatch.setenv("DATABASE_URL", "unused")
    monkeypatch.setenv("KRX_API_KEY", "not-a-real-key")
    asyncio.run(W.backfill(since, **kwargs))
    return con, fetched


def test_backfill_skips_weeks_older_than_the_cutoff_and_says_so(monkeypatch, capsys):
    con, fetched = _backfill_run(monkeypatch, dt.date(2023, 9, 1), limit_weeks=3)
    out = capsys.readouterr().out
    assert fetched and min(fetched) >= "20231016", fetched               # 하한 이전 주는 KRX 를 부르지 않는다
    assert fetched[0] == "20231020"
    assert "보존 하한 20231017" in out and "이전 7주(20230828~20231013)는 건너뜀" in out and "--allow-before-cutoff" in out
    deleted = [p for s, p in con.statements if s.lstrip().upper().startswith("DELETE")]
    assert deleted == [("20231020",), ("20231027",), ("20231103",)]


def test_backfill_override_flag_fetches_old_weeks(monkeypatch, capsys):
    con, fetched = _backfill_run(monkeypatch, dt.date(2023, 9, 1), limit_weeks=2, allow_before_cutoff=True)
    assert fetched[0] == "20230901"
    assert "건너뜀" not in capsys.readouterr().out
