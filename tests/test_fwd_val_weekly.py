"""fwd_val_weekly — 선행 배수 집계 배치 (DB·네트워크 0콜).

지키는 것: ① 새 날짜 + 가장 최근 날짜만 다시 계산 ② 업종 분류·시장 구분은 추정 날짜 이하 가장 최근
스냅샷 — 더 새 스냅샷이 있어도 끌어오지 않는다(없을 때만 가장 이른 것) ③ 그 주 뒤 상장 종목의 시장은
가장 최근이 아니라 그 뒤 첫 시세 ④ 배당 분모 표기는 수집 머신과 같다 ⑤ 영문 보통주를 포함하고
종류 미확인 코드와 우선주는 제외한다 ⑥ 쓰기는 칸 이름으로 ⑦ 보관은 전체 — 원본 창 안에서 원본에 없는
날짜만 지운다 ⑧ `--check` 는 저장분을 저장할 때의 분류·시세 날짜로 다시 내서 대조한다.
"""
import types
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


def test_hist_window_starts_on_the_iso_monday_of_the_oldest_source_day():
    assert fv.hist_window_start([dt.date(2026, 10, 17), dt.date(2026, 8, 29)]) == dt.date(2026, 8, 24)  # 토 → 그 주 월
    assert fv.hist_window_start([dt.date(2026, 8, 24)]) == dt.date(2026, 8, 24)                          # 월은 그대로
    assert fv.hist_window_start([]) is None


class _DuckCon:
    """prune 의 실제 DELETE 를 DuckDB 메모리 DB 에서 돌린다(psycopg 자리표시 %s → ?)."""

    def __init__(self, con):
        self.con = con
        self.seen = []

    def execute(self, sql, params):
        self.seen.append((sql, params))
        n = self.con.execute(sql.replace("%s", "?"), list(params)).fetchone()[0]
        return types.SimpleNamespace(rowcount=n)


def _prune(stored_days, hist_days):
    import duckdb

    with duckdb.connect(":memory:") as con:
        con.execute("create table opm_val_fwd(as_of date)")
        for d in stored_days:
            con.execute("insert into opm_val_fwd values (?)", [d])
        n = fv.prune_stale_days(_DuckCon(con), hist_days)
        return n, [r[0] for r in con.execute("select as_of from opm_val_fwd order by 1").fetchall()]


def test_prune_keeps_aggregates_older_than_the_source_window():
    """원본(13주 롤링)에서 빠진 옛 날짜는 남긴다 — 이 표가 선행 배수의 유일한 이력이다(전체 보관)."""
    old = [dt.date(2026, 6, 6), dt.date(2026, 6, 13)]
    hist = [dt.date(2026, 8, 29), dt.date(2026, 9, 5), dt.date(2026, 10, 10), dt.date(2026, 10, 17)]
    n, left = _prune(old + hist, hist)
    assert n == 0 and left == old + hist


def test_prune_drops_dates_the_source_replaced_inside_its_window():
    """창 안에서 원본에 없는 날짜 = 같은 ISO 주 재송출로 대체된 것 — 지운다. 가장 이른 원본 주도 주 단위로 본다."""
    hist = [dt.date(2026, 8, 29), dt.date(2026, 9, 5), dt.date(2026, 10, 10)]
    stored = [dt.date(2026, 8, 21),                        # 창(8/24 월~)보다 이른 주 — 남긴다
              dt.date(2026, 8, 28), dt.date(2026, 8, 29),  # 8/28(금)을 8/29(토)가 덮었다 — 같은 주라 지운다
              dt.date(2026, 9, 5),
              dt.date(2026, 10, 5), dt.date(2026, 10, 10)]  # 월요일 보강분을 토요일분이 덮었다 — 지운다
    n, left = _prune(stored, hist)
    assert n == 2
    assert left == [dt.date(2026, 8, 21), dt.date(2026, 8, 29), dt.date(2026, 9, 5), dt.date(2026, 10, 10)]


def test_prune_never_deletes_when_the_source_is_empty():
    con = _DuckCon(None)
    assert fv.prune_stale_days(con, []) == 0
    assert con.seen == []                                               # 원본이 비면 질의조차 안 한다


def test_prune_sql_bounds_the_delete_to_the_source_window():
    con = _DuckCon(None)
    con.execute = lambda sql, params: (con.seen.append((sql, params)), types.SimpleNamespace(rowcount=0))[1]
    fv.prune_stale_days(con, D)
    sql, params = con.seen[-1]
    assert sql.startswith("DELETE FROM opm_val_fwd WHERE as_of >= %s AND NOT (as_of = ANY(%s))")
    assert params == (dt.date(2026, 8, 24), D)


class _Cur:
    def __init__(self, con):
        self.con = con
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params):
        self.con.seen.append((sql, params))
        self.rows = self.con.stored if sql == fv.STORED_SQL else self.con.agg

    def fetchall(self):
        return [dict(r) for r in self.rows]


class _Con:
    def __init__(self, stored=(), agg=()):
        self.seen = []
        self.stored = list(stored)
        self.agg = list(agg)

    def cursor(self, row_factory=None):
        return _Cur(self)


def test_compute_uses_the_common_share_population_and_market_rule():
    con = _Con()
    fv.compute(con, D[-1], "20260828", "20260911")
    sql, params = con.seen[-1]
    assert "right(h.stock_code, 1) = '0'" in sql and "^[0-9]{6}$" not in sql
    assert params == {"as_of": D[-1], "class_dd": "20260828", "mk_dd": "20260911"}
    # 시장 구분: mk_dd 부터 **가장 이른** 시세 — 그 주에 있으면 그 값, 뒤에 상장했으면 첫 관측(최신이 아니라)
    assert "WHERE price_dd >= %(mk_dd)s" in sql and "ORDER BY ticker, price_dd\n" in sql


def _agg(market="KS", scheme="market", bucket="_ALL", **kw):
    row = {"market": market, "scheme": scheme, "bucket": bucket, "label": None, "parent": None,
           "n_total": 10, "n_per": 9, "n_per_pos": 8, "n_pbr": 10, "n_pbr_pos": 10, "n_psr": 10, "n_dps": 7,
           "fy_main": 2026, "fy_min": 2026, "fy_max": 2027,
           "cap_krw": 3.0e14, "ni_krw": 2.0e13, "fwd_per": 15.0, "ni_pos_krw": 2.1e13, "fwd_per_pos": 14.2857,
           "eq_krw": 2.5e14, "fwd_pbr": 1.2, "fwd_pbr_pos": 1.2, "rev_krw": 4.0e14, "fwd_psr": 0.75,
           "dps_total_krw": 6.0e12, "dps_cap_krw": 2.8e14, "fwd_div_yield_pct": 2.142857}
    row.update(kw)
    return row


def _stored(class_dd="20260828", mk_dd="20260911", **kw):
    return dict(_agg(**kw), class_dd=class_dd, mk_dd=mk_dd)


def test_diff_rows_is_exact_on_counts_and_tolerates_only_summation_noise():
    assert fv.diff_rows([_stored()], [_agg()]) == []
    assert fv.diff_rows([_stored()], [_agg(cap_krw=3.0e14 * (1 + 1e-12))]) == []    # 합산 순서 차이
    lines = fv.diff_rows([_stored()], [_agg(cap_krw=3.0e14 * (1 + 1e-6), n_total=11)])
    assert len(lines) == 1 and "n_total 10 → 11" in lines[0] and "cap_krw" in lines[0]
    assert fv.diff_rows([_stored(fwd_per=None)], [_agg(fwd_per=None)]) == []
    assert "fwd_per" in fv.diff_rows([_stored(fwd_per=None)], [_agg()])[0]       # 비었다가 생긴 것도 어긋남


def test_diff_rows_reports_rows_on_either_side_only():
    stored = [_stored(), _stored(scheme="wics_industry", bucket="G1010")]
    fresh = [_agg(), _agg(scheme="wics_sector", bucket="G10")]
    lines = fv.diff_rows(stored, fresh)
    assert lines == ["('KS', 'wics_industry', 'G1010'): 저장돼 있으나 다시 내면 없다",
                     "('KS', 'wics_sector', 'G10'): 다시 내면 있으나 저장분에 없다"]
    assert fv.diff_rows(stored, fresh, report_missing=False) == lines[:1]


def test_check_reuses_this_runs_result_when_the_snapshot_dates_match(capsys):
    con = _Con(stored=[_stored(), _stored(market="ALL")], agg=[])
    assert fv.check_against_stored(con, D[-1], "20260828", "20260911", [_agg(), _agg(market="ALL")]) == 0
    assert [sql for sql, _ in con.seen] == [fv.STORED_SQL]                      # 다시 계산하지 않는다
    assert con.seen[0][1] == (D[-1],)
    assert "어긋남 0건" in capsys.readouterr().out


def test_check_recomputes_with_the_stored_snapshot_dates(capsys):
    """저장 뒤 업종분류가 새로 들어와도(10-17 행 = 0828 분류) 원본만 비교 대상이 되게 저장할 때의 날짜로 다시 낸다."""
    con = _Con(stored=[_stored(class_dd="20260828", mk_dd="20261016")], agg=[_agg(n_total=12)])
    bad = fv.check_against_stored(con, D[-1], "20261016", "20261016", [_agg()])
    assert bad == 1
    sql, params = con.seen[-1]
    assert sql == fv.AGG_SQL and params == {"as_of": D[-1], "class_dd": "20260828", "mk_dd": "20261016"}
    out = capsys.readouterr().out
    assert "저장할 때의 날짜로" in out and "n_total 10 → 12" in out


def test_check_without_stored_rows_skips():
    con = _Con()
    assert fv.check_against_stored(con, D[-1], "20260828", "20260911", [_agg()]) == 0


def test_check_no_longer_reads_the_retired_collector_aggregate():
    src = Path(fv.__file__).read_text()
    assert "FROM fwd_agg" not in src and "check_against_collector" not in src


def test_actual_aggregate_includes_alpha_common_and_excludes_unconfirmed_and_preferred():
    import duckdb

    with duckdb.connect(":memory:") as con:
        con.execute("create table wise_sector(ticker varchar, sector_code varchar, sector varchar, "
                    "industry_code varchar, industry varchar, snap_dd varchar)")
        con.execute("create table krx_weekly(ticker varchar, market varchar, price_dd varchar)")
        con.execute("create table krx_daily(trd_dd varchar, ticker varchar, market varchar)")
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
        market = [row for row in rows if row["scheme"] == "market"]
        assert len(market) == 2
        assert all(row["n_total"] == 2 for row in market)
        assert all(row["cap_krw"] == 3000 and row["ni_krw"] == 300 and row["fwd_per"] == 10 for row in market)
        # 업종분류가 없는 두 종목은 대분류 「미분류」 버킷에 같은 값으로 든다(261018, 감사 8) — 하위업종에는 없다
        unc = [row for row in rows if row["scheme"] != "market"]
        assert {(r["scheme"], r["market"], r["bucket"], r["label"]) for r in unc} == {
            ("wics_sector", "KS", fv.UNCLASSIFIED, "미분류"), ("wics_sector", "ALL", fv.UNCLASSIFIED, "미분류")}
        assert all(r["n_total"] == 2 and r["cap_krw"] == 3000 and r["fwd_per"] == 10 for r in unc)


def _sector_agg_db(con):
    """대분류 둘(분류 있음 2종목 + 분류 없음 1종목)의 작은 원본 — 미분류 버킷 시험용."""
    con.execute("create table wise_sector(ticker varchar, sector_code varchar, sector varchar, "
                "industry_code varchar, industry varchar, snap_dd varchar)")
    con.execute("create table krx_weekly(ticker varchar, market varchar, price_dd varchar)")
    con.execute("create table krx_daily(trd_dd varchar, ticker varchar, market varchar)")
    con.execute("create table fwd_hist(stock_code varchar, fiscal_year integer, mktcap_krw double, "
                "ni_ctrl_krw double, rev_krw double, bps_krw double, price_krw double, dps_krw double, "
                "as_of date, is_estimate boolean, period_type varchar, basis varchar, period varchar)")
    con.execute("insert into wise_sector values ('005930', 'G45', 'IT', 'G4530', '반도체', '20260828'), "
                "('000660', 'G45', 'IT', 'G4530', '반도체', '20260828')")
    for code, mk, cap in (("005930", "KS", 1000), ("000660", "KS", 500), ("123450", "KQ", 70)):
        con.execute("insert into krx_weekly values (?, ?, '20260911')", [code, mk])
        con.execute("insert into fwd_hist values (?, 2027, ?, 10, 1000, 20, 100, 1, "
                    "DATE '2026-09-13', true, 'FY', 'IFRS연결', '2027/12')", [code, cap])
    sql = fv.AGG_SQL.replace("%(class_dd)s", "'20260828'") \
        .replace("%(mk_dd)s", "'20260911'").replace("%(as_of)s", "DATE '2026-09-13'")
    result = con.execute(sql)
    return [dict(zip((c[0] for c in result.description), r)) for r in result.fetchall()]


def test_unclassified_bucket_makes_sector_rows_add_up_to_the_market():
    """감사 8(261018): 은퇴한 수집 머신 집계에 있던 「미분류」 대분류 버킷을 되살린다 — 대분류 합 = 시장 행."""
    import duckdb

    with duckdb.connect(":memory:") as con:
        rows = _sector_agg_db(con)
    by = {(r["scheme"], r["market"], r["bucket"]): r for r in rows}
    for mk in ("KS", "KQ", "ALL"):
        sector_sum = sum(r["n_total"] for k, r in by.items() if k[0] == "wics_sector" and k[1] == mk)
        assert sector_sum == by[("market", mk, "_ALL")]["n_total"]
    assert by[("wics_sector", "KQ", fv.UNCLASSIFIED)]["label"] == "미분류"
    assert by[("wics_sector", "KQ", fv.UNCLASSIFIED)]["cap_krw"] == 70
    assert ("wics_sector", "KS", fv.UNCLASSIFIED) not in by           # 코스피는 둘 다 분류돼 있다 — 빈 버킷을 만들지 않는다
    assert not [k for k in by if k[0] == "wics_industry" and k[2] == fv.UNCLASSIFIED]   # 하위업종에는 두지 않는다
    assert by[("wics_sector", "KS", "G45")]["n_total"] == 2           # 분류된 버킷은 그대로


def test_check_does_not_count_the_new_bucket_as_drift_on_days_written_before_it(capsys):
    """미분류 버킷이 생기기 전에 쓴 날짜: 다시 내면 그 행이 있지만 원본이 바뀐 것이 아니다 — 어긋남 0, 이유를 한 줄."""
    stored = [_stored(market="KS", scheme="market", bucket="_ALL")]
    fresh = [_agg(market="KS"), _agg(market="KS", scheme="wics_sector", bucket=fv.UNCLASSIFIED, label="미분류")]
    con = _Con(stored=stored)
    assert fv.check_against_stored(con, dt.date(2026, 9, 13), "20260828", "20260911", fresh) == 0
    assert "미분류 버킷은 아직 저장 전" in capsys.readouterr().out
    # 버킷이 이미 저장된 날짜면 평소대로 센다(한쪽에만 있는 행은 어긋남)
    stored2 = stored + [_stored(market="KS", scheme="wics_sector", bucket=fv.UNCLASSIFIED, label="미분류")]
    fresh2 = [_agg(market="KS")]
    assert fv.check_against_stored(_Con(stored=stored2), dt.date(2026, 9, 13), "20260828", "20260911", fresh2) == 1


def test_market_split_sees_this_weeks_new_listing_from_the_daily_store():
    """261018(재설계 5-1 쓰는 쪽): krx_weekly 는 닫힌 주만 담는다. 이번 주 신규 상장 종목은 주가 닫힐 때까지 krx_weekly 에 없으므로
    시장 구분 원천에 일별 최신 거래일을 덧붙인다 — 종전 krx_weekly(이번 주 행 = 그날 최신일)와 같은 모집단."""
    import duckdb

    with duckdb.connect(":memory:") as con:
        con.execute("create table wise_sector(ticker varchar, sector_code varchar, sector varchar, "
                    "industry_code varchar, industry varchar, snap_dd varchar)")
        con.execute("create table krx_weekly(ticker varchar, market varchar, price_dd varchar)")
        con.execute("create table krx_daily(trd_dd varchar, ticker varchar, market varchar)")
        con.execute("create table fwd_hist(stock_code varchar, fiscal_year integer, mktcap_krw double, "
                    "ni_ctrl_krw double, rev_krw double, bps_krw double, price_krw double, dps_krw double, "
                    "as_of date, is_estimate boolean, period_type varchar, basis varchar, period varchar)")
        con.execute("insert into krx_weekly values ('005930', 'KS', '20260911')")            # 지난주 금요일(닫힌 주)
        con.execute("insert into krx_daily values ('20260915', '005930', 'KS'), ('20260915', '123450', 'KQ')")
        for code in ("005930", "123450"):
            con.execute("insert into fwd_hist values (?, 2027, 1000, 100, 1000, 20, 100, 1, "
                        "DATE '2026-09-16', true, 'FY', 'IFRS연결', '2027/12')", [code])
        days = sorted(r[0] for r in con.execute(f"SELECT DISTINCT price_dd FROM {fv.WEEKLY_MARKET} w").fetchall())
        assert days == ["20260911", "20260915"]                       # 일별 최신일이 주간 최신보다 늦어 덧붙는다
        mk_dd = fv.snapshot_at_or_before("20260916", days)
        sql = fv.AGG_SQL.replace("%(class_dd)s", "'20260828'") \
            .replace("%(mk_dd)s", f"'{mk_dd}'").replace("%(as_of)s", "DATE '2026-09-16'")
        result = con.execute(sql)
        rows = [dict(zip((column[0] for column in result.description), row)) for row in result.fetchall()]
        assert {"KS", "KQ"} <= {r["market"] for r in rows if r["scheme"] == "market"}   # 신규 상장(코스닥)이 빠지지 않는다
        # 금요일이 닫혀 krx_weekly 에 들어오면 일별 최신일은 덧붙지 않는다(같은 날을 두 번 세지 않는다)
        con.execute("insert into krx_weekly values ('005930', 'KS', '20260915'), ('123450', 'KQ', '20260915')")
        assert con.execute(f"SELECT count(*) FROM {fv.WEEKLY_MARKET} w WHERE price_dd='20260915'").fetchone()[0] == 2


def test_upsert_names_every_column_and_keeps_the_key_out_of_the_update():
    assert len(set(fv.COLS)) == len(fv.COLS)
    for c in fv.COLS:
        assert f"%({c})s" in fv.UPSERT
    upd = fv.UPSERT.split("DO UPDATE SET", 1)[1]
    assert "as_of = EXCLUDED" not in upd and "bucket = EXCLUDED" not in upd and "fwd_per = EXCLUDED.fwd_per" in upd
    assert "PRIMARY KEY (as_of, market, scheme, bucket)" in fv.DDL
