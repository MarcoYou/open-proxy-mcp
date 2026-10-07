"""fwd_val_weekly — 선행 배수 집계 배치 (DB·네트워크 0콜).

지키는 것: ① 새 날짜 + 가장 최근 날짜만 다시 계산 ② 업종 분류·시장 구분은 추정 날짜 이하 가장 최근
스냅샷 — 더 새 스냅샷이 있어도 끌어오지 않는다(없을 때만 가장 이른 것) ③ 그 주 뒤 상장 종목의 시장은
가장 최근이 아니라 그 뒤 첫 시세 ④ 배당 분모 표기는 수집 머신과 같다 ⑤ 영문 보통주를 포함하고
종류 미확인 코드와 우선주는 제외한다 ⑥ 쓰기는 칸 이름으로.
"""
import datetime as dt
import importlib.util
from pathlib import Path

import pytest

from open_proxy_mcp.services.sector_class import DB_SCHEME, MAJOR

pytest.importorskip("psycopg")

_SPEC = importlib.util.spec_from_file_location(
    "fwd_val_weekly", Path(__file__).resolve().parents[1] / "scripts" / "fwd_val_weekly.py")
fv = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fv)

D = [dt.date(2026, 8, 28), dt.date(2026, 9, 5), dt.date(2026, 9, 13)]


def test_pick_days_new_dates_plus_the_latest_only():
    assert fv.pick_days(D, set(D), recompute=False) == [D[-1]]          # 체인이 최근 날짜를 다시 올릴 수 있다
    assert fv.pick_days(D, {D[0]}, recompute=False) == D[1:]
    assert fv.pick_days(D[::-1] + [D[0]], set(), recompute=False) == D  # 정렬·중복 제거
    assert fv.pick_days(D, set(D), recompute=True) == D
    assert fv.pick_days([], set(), recompute=False) == []


def test_snapshot_is_at_or_before_the_estimate_date():
    dds = ["20260904", "20260828", "20260911", "20260828"]
    assert fv.snapshot_at_or_before("20260830", dds) == "20260828"
    assert fv.snapshot_at_or_before("20260911", dds) == "20260911"
    assert fv.snapshot_at_or_before("20260801", dds) == "20260828"    # 더 이른 것이 없으면 가장 이른 것(소급)
    assert fv.snapshot_at_or_before("20260830", []) is None
    # 송출이 늦어 월초 업종분류 갱신(0925) 뒤에 계산돼도 추정 날짜(0919) 뒤의 분류를 쓰지 않는다
    assert fv.snapshot_at_or_before("20260919", ["20260828", "20260925"]) == "20260828"


def test_finish_rows_marks_dividend_denominator_like_the_collector():
    src = [{"scheme": "market", "market": "KS", "bucket": "_ALL", "n_dps": 3},
           {"scheme": DB_SCHEME[MAJOR], "market": "KQ", "bucket": "G10", "n_dps": 0}]
    out = fv.finish_rows(D[-1], "20260828", "20260911", src)
    assert [r["div_denom_basis"] for r in out] == ["covered", None]
    assert all(r["as_of"] == D[-1] and r["class_dd"] == "20260828" and r["mk_dd"] == "20260911" for r in out)
    assert "as_of" not in src[0]                                        # 입력을 건드리지 않는다


def test_prune_stale_days_uses_the_source_days_as_allowlist():
    class Result:
        rowcount = 12

    class Con:
        def __init__(self):
            self.seen = None

        def execute(self, sql, params):
            self.seen = (sql, params)
            return Result()

    con = Con()
    assert fv.prune_stale_days(con, D) == 12
    sql, params = con.seen
    assert "DELETE FROM opm_val_fwd" in sql and "as_of = ANY" in sql
    assert params == (D,)


class _Cur:
    def __init__(self, seen):
        self.seen = seen

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        self.seen.append((sql, params))

    def fetchall(self):
        return []


class _Con:
    def __init__(self):
        self.seen = []

    def cursor(self, row_factory=None):
        return _Cur(self.seen)


def test_compute_and_collector_check_use_the_same_population():
    con = _Con()
    fv.compute(con, D[-1], "20260828", "20260911")
    sql, params = con.seen[-1]
    assert "right(h.stock_code, 1) = '0'" in sql and "^[0-9]{6}$" not in sql
    assert params == {"as_of": D[-1], "class_dd": "20260828", "mk_dd": "20260911"}
    # 시장 구분: mk_dd 부터 **가장 이른** 시세 — 그 주에 있으면 그 값, 뒤에 상장했으면 첫 관측(최신이 아니라)
    assert "WHERE price_dd >= %(mk_dd)s" in sql and "ORDER BY ticker, price_dd\n" in sql
    seen = con.seen[-1]
    fv.check_against_collector(con, D[-1], "20260828", "20260911")
    assert con.seen[-2] == seen


def test_actual_aggregate_includes_alpha_common_and_excludes_unconfirmed_and_preferred():
    import duckdb

    with duckdb.connect(":memory:") as con:
        con.execute("create table wise_sector(ticker varchar, sector_code varchar, sector varchar, "
                    "industry_code varchar, industry varchar, snap_dd varchar)")
        con.execute("create table krx_weekly(ticker varchar, market varchar, price_dd varchar)")
        con.execute("create table fwd_hist(stock_code varchar, fiscal_year integer, mktcap_krw double, "
                    "ni_ctrl_krw double, rev_krw double, bps_krw double, price_krw double, dps_krw double, "
                    "as_of date, is_estimate boolean, period_type varchar, basis varchar, period varchar)")
        for code, cap, profit in (("005930", 1000, 100), ("0126Z0", 2000, 200),
                                  ("0161M0", 999000, 1), ("005935", 888000, 1)):
            con.execute("insert into krx_weekly values (?, 'KS', '20260911')", [code])
            con.execute("insert into fwd_hist values (?, 2027, ?, ?, 1000, 20, 100, 1, "
                        "DATE '2026-09-13', true, 'FY', 'IFRS연결', '2027/12')", [code, cap, profit])
        sql = fv.AGG_SQL.replace("%(class_dd)s", "'20260828'") \
            .replace("%(mk_dd)s", "'20260911'").replace("%(as_of)s", "DATE '2026-09-13'")
        result = con.execute(sql)
        rows = [dict(zip((column[0] for column in result.description), row)) for row in result.fetchall()]
        assert len(rows) == 2
        assert all(row["scheme"] == "market" and row["n_total"] == 2 for row in rows)
        assert all(row["cap_krw"] == 3000 and row["ni_krw"] == 300 and row["fwd_per"] == 10 for row in rows)


def test_upsert_names_every_column_and_keeps_the_key_out_of_the_update():
    assert len(set(fv.COLS)) == len(fv.COLS)
    for c in fv.COLS:
        assert f"%({c})s" in fv.UPSERT
    upd = fv.UPSERT.split("DO UPDATE SET", 1)[1]
    assert "as_of = EXCLUDED" not in upd and "bucket = EXCLUDED" not in upd and "fwd_per = EXCLUDED.fwd_per" in upd
    assert "PRIMARY KEY (as_of, market, scheme, bucket)" in fv.DDL
