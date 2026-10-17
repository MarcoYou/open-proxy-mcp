# -*- coding: utf-8 -*-
"""krx_weekly 보존 경계 — 배치·서버·표 정리가 같은 정의를 쓰는지 고정한다. network 0 · DB 0.

정의(코디네이터 확정): 하한 = 최신 price_dd 의 달력 3년 전 · 하한 이상은 전부 · 그 밖에는 **종목별로 그 해 마지막 행**
(그 마지막 행이 하한보다 이른 것). SQL 조각(keep_predicate · prune_where · prune_statement · keep_select)은 SQLite 에서
실제로 실행해 파이썬 판과 같은 집합인지 본다.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

import pytest

from open_proxy_mcp import krx_retention as R


def _fridays(start: date, end: date) -> list[str]:
    d = start + timedelta(days=(4 - start.weekday()) % 7)
    out = []
    while d <= end:
        out.append(d.strftime("%Y%m%d"))
        d += timedelta(days=7)
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# 1. 하한 (코디네이터 확정 정의)
# ═══════════════════════════════════════════════════════════════════════════════
def test_cutoff_is_latest_minus_three_calendar_years():
    assert R.krx_weekly_cutoff("20261016") == "20231016"
    assert R.krx_weekly_cutoff("20260301") == "20230301"
    assert R.krx_weekly_cutoff("20261231") == "20231231"


@pytest.mark.parametrize("latest,expected", [
    ("20240229", "20210228"),   # 윤일 → 평년 2월 28일
    ("20280229", "20250228"),
    ("20270228", "20240228"),   # 평년 2월 28일은 그대로(윤년이어도 28일)
])
def test_cutoff_feb_29_falls_back_to_feb_28(latest, expected):
    assert R.krx_weekly_cutoff(latest) == expected


@pytest.mark.parametrize("bad", ["", "2026-10-16", "2026101", "abcdefgh", "20260231", "20261301", None, 20261016])
def test_cutoff_rejects_non_dates(bad):
    with pytest.raises(ValueError):
        R.krx_weekly_cutoff(bad)


def test_cutoff_keeps_157_weekly_points():
    """매주 금요일 행이 있는 달력에서 하한 이상은 157주 — 코디네이터가 확정한 숫자."""
    weeks = _fridays(date(2023, 1, 1), date(2026, 10, 16))
    latest = weeks[-1]
    assert latest == "20261016"
    kept = [w for w in weeks if w >= R.krx_weekly_cutoff(latest)]
    assert kept[0] == "20231020" and len(kept) == 157


# ═══════════════════════════════════════════════════════════════════════════════
# 2. 종목별 연말 행 — 파이썬 판
# ═══════════════════════════════════════════════════════════════════════════════
LATEST = "20261016"
ROWS = [
    # A: 계속 거래 — 옛 해(2021·2022)는 그 해 마지막 행만. 하한이 속한 해(2023)는 하한 이후 행만(마지막 행이 하한 이후라서)
    ("A", "20210107"), ("A", "20211230"), ("A", "20220107"), ("A", "20221229"),
    ("A", "20230106"), ("A", "20231013"), ("A", "20231020"), ("A", "20261016"),
    # B: 2021 년 12월에 상장폐지 — 마지막 행(12월)이 그대로 남는다(전역 마지막 주 한 줄 방식에서는 사라지던 것)
    ("B", "20210107"), ("B", "20211217"),
    # C: 2022 년 중간(6월)에 상장폐지 — 12월이 아니어도 그해 마지막 행이다
    ("C", "20220107"), ("C", "20220603"),
    # D: 하한이 속한 해(2023)에 하한 전에 끝남 — 그 해 마지막 행(20230310)이 하한보다 이르니 남긴다(최종 정의)
    ("D", "20221229"), ("D", "20230106"), ("D", "20230310"),
    # E: 하한 이후 신규
    ("E", "20250103"), ("E", "20261016"),
]


def test_year_end_rows_are_the_last_row_per_ticker_per_year_when_it_is_before_the_cutoff():
    ye = R.year_end_rows(ROWS, LATEST)
    assert ye == frozenset({
        ("A", "20211230"), ("A", "20221229"),     # A 의 2023 마지막 행(20231020)은 하한 이후 — 연말 행이 아니라 그냥 하한 이후 행
        ("B", "20211217"),
        ("C", "20220603"),
        ("D", "20221229"), ("D", "20230310"),     # D 는 하한이 속한 해여도 마지막 행이 하한보다 이르면 남는다
    })


def test_is_retained_dense_year_end_and_dropped():
    ye = R.year_end_rows(ROWS, LATEST)
    kept = {r for r in ROWS if R.is_retained(r[0], r[1], LATEST, ye)}
    dropped = set(ROWS) - kept
    assert dropped == {("A", "20210107"), ("A", "20220107"), ("A", "20230106"), ("A", "20231013"),
                       ("B", "20210107"), ("C", "20220107"), ("D", "20230106")}
    assert ("B", "20211217") in kept and ("C", "20220603") in kept and ("D", "20230310") in kept   # 종목별 마지막 행
    assert ("A", "20231020") in kept and ("E", "20250103") in kept                                 # 하한 이후 전부
    assert R.is_retained("X", "20231016", LATEST, frozenset())                                    # 하한 당일은 남는다(>=)
    assert not R.is_retained("X", "20231015", LATEST, frozenset())                                # 하한 하루 전은 연말 행이 아니면 정리


def test_the_last_row_of_a_year_is_kept_only_when_it_is_older_than_the_cutoff():
    """하한을 가로지르는 해: 마지막 행이 하한 이후면 그 해의 하한 전 행은 전부 정리(어차피 하한 이후 행이 있다)."""
    rows = [("A", "20230106"), ("A", "20230929"), ("A", "20231027"),            # A: 마지막 20231027 >= 하한
            ("Z", "20230106"), ("Z", "20230929")]                               # Z: 마지막 20230929 < 하한 — 남는다
    ye = R.year_end_rows(rows, LATEST)
    assert ye == frozenset({("Z", "20230929")})
    kept = {r for r in rows if R.is_retained(r[0], r[1], LATEST, ye)}
    assert kept == {("A", "20231027"), ("Z", "20230929")}


def test_retention_is_idempotent_and_rolls_forward_consistently():
    """이미 자른 표에 다시 적용해도 그대로, 하한이 밀린 뒤에도 앞서 남긴 연말 행이 사라지지 않는다(롤링 정리 전제)."""
    weeks = _fridays(date(2015, 12, 30), date(2026, 10, 16))
    last_of = lambda year: max(w for w in weeks if w[:4] == year)                       # 그 해 마지막 금요일
    b_last = max(w for w in weeks if w <= "20190614")                                   # B: 2019 중반 폐지 — 마지막 행
    rows = [("A", w) for w in weeks] + [("B", w) for w in weeks if w <= b_last]
    ye = R.year_end_rows(rows, LATEST)
    cut = [r for r in rows if R.is_retained(r[0], r[1], LATEST, ye)]
    assert len(cut) < len(rows) and ("B", b_last) in cut and ("A", last_of("2022")) in cut
    # 멱등
    ye2 = R.year_end_rows(cut, LATEST)
    assert [r for r in cut if R.is_retained(r[0], r[1], LATEST, ye2)] == cut
    # 1년 뒤(하한이 20241015 로 밀린다)에도 — 새 주를 얹고 잘린 표에서 다시 구해 적용: 2023 의 종목별 마지막 행이 새로 연말 행이 된다
    later = "20271015"
    table = cut + [("A", later)]
    ye3 = R.year_end_rows(table, later)
    table2 = [r for r in table if R.is_retained(r[0], r[1], later, ye3)]
    assert ("A", last_of("2022")) in table2 and ("B", b_last) in table2         # 앞서 남긴 연말 행 유지
    assert ("A", last_of("2023")) in table2                                     # 2023 은 이제 하한(20241015)보다 이른 해 — 마지막 행만
    assert ("A", "20231027") not in table2 and ("A", "20250103") in table2      # 2023 중간 행은 정리, 하한 이후는 유지


def test_pair_floor_is_cutoff_plus_margin():
    assert R.krx_weekly_pair_floor("20261016") == "20231030"


def test_weekly_window_note_states_policy_and_cutoff():
    note = R.weekly_window_note(R.krx_weekly_cutoff("20261016"))
    assert "주간 시세는 최근 3년까지 제공(연말 값은 전체 기간)" in note
    assert "2023-10-16" in note


def test_origin_constant_is_the_original_window_start():
    assert R.KRX_WEEKLY_ORIGIN_DD == "20151230"
    assert R.KRX_WEEKLY_RETENTION_YEARS == 3


# ═══════════════════════════════════════════════════════════════════════════════
# 3. SQL 조각 — SQLite 에서 실제로 실행해 파이썬 판과 대조
# ═══════════════════════════════════════════════════════════════════════════════
def _db(rows):
    con = sqlite3.connect(":memory:")
    con.create_function("left", 2, lambda s, n: s[:n])        # Postgres 의 left()
    con.execute("CREATE TABLE krx_weekly (price_dd TEXT NOT NULL, ticker TEXT NOT NULL, market TEXT, "
                "PRIMARY KEY (price_dd, ticker))")
    con.executemany("INSERT INTO krx_weekly (price_dd, ticker, market) VALUES (?,?,'KS')", [(d, t) for t, d in rows])
    return con


def _q(con, sql_params):
    sql, params = sql_params
    return con.execute(sql.replace("%s", "?"), params)


def _rows_of(con):
    return {(t, d) for d, t in con.execute("SELECT price_dd, ticker FROM krx_weekly")}


def _universe():
    """여러 종목·여러 해 — 종목별 연말 행 규칙의 모든 갈래(계속 거래·12월 폐지·중간 폐지·하한 해 폐지·신규·하한 가로지름)를 담는다."""
    weeks = _fridays(date(2020, 1, 1), date(2026, 10, 16))
    rows = [("A", w) for w in weeks]
    rows += [("B", w) for w in weeks if w <= "20211217"]
    rows += [("C", w) for w in weeks if w <= "20220603"]
    rows += [("D", w) for w in weeks if w <= "20230310"]
    rows += [("E", w) for w in weeks if w >= "20250103"]
    rows += [("F", w) for w in weeks if "20220401" <= w <= "20240315"]        # 하한을 가로지르는 종목
    return rows


def test_sql_keep_predicate_matches_the_python_definition():
    rows = _universe()
    con = _db(rows)
    ye = R.year_end_rows(rows, LATEST)
    expected = {r for r in rows if R.is_retained(r[0], r[1], LATEST, ye)}
    sql, params = R.keep_predicate(LATEST)
    got = {(t, d) for d, t in con.execute(
        f"SELECT price_dd, ticker FROM krx_weekly WHERE {sql.replace('%s', '?')}", params)}
    assert got == expected and 0 < len(got) < len(rows)
    assert params == ("20231016", "20231016")
    assert ("D", "20230310") in got                                             # 하한이 속한 해에 끝난 종목의 마지막 행


def test_sql_prune_where_is_the_exact_complement_of_keep_predicate():
    rows = _universe()
    con = _db(rows)
    ye = R.year_end_rows(rows, LATEST)
    expected_prune = {r for r in rows if not R.is_retained(r[0], r[1], LATEST, ye)}
    where, params = R.prune_where(LATEST)
    got = {(t, d) for d, t in con.execute(
        f"SELECT w.price_dd, w.ticker FROM krx_weekly w WHERE {where.replace('%s', '?')}", params)}
    assert got == expected_prune
    assert params == ("20231016", "20231016")


def test_sql_prune_statement_deletes_exactly_the_python_complement_and_is_idempotent():
    rows = _universe()
    con = _db(rows)
    ye = R.year_end_rows(rows, LATEST)
    expected_keep = {r for r in rows if R.is_retained(r[0], r[1], LATEST, ye)}
    sql, params = R.prune_statement(LATEST)
    assert sql.startswith("DELETE FROM krx_weekly AS w WHERE ")
    _q(con, (sql, params))
    assert _rows_of(con) == expected_keep
    _q(con, (sql, params))                                  # 두 번째는 아무것도 안 지운다
    assert _rows_of(con) == expected_keep


def test_sql_keep_select_equals_python_keep_set_without_duplicates():
    rows = _universe()
    con = _db(rows)
    ye = R.year_end_rows(rows, LATEST)
    expected_keep = [r for r in rows if R.is_retained(r[0], r[1], LATEST, ye)]
    sql, params = R.keep_select(LATEST, columns="w.ticker, w.price_dd")
    got = [(t, d) for t, d in _q(con, (sql, params)).fetchall()]
    assert sorted(got) == sorted(expected_keep) and len(got) == len(set(got))       # 두 갈래가 겹치지 않는다


def test_sql_rolling_prune_keeps_old_year_ends_and_agrees_with_python_after_the_cutoff_moves():
    """1년 뒤 같은 DELETE 를 다시 돌려도(롤링) 앞서 남긴 연말 행이 사라지지 않고 파이썬 판과 같은 결과다."""
    rows = _universe()
    con = _db(rows)
    _q(con, R.prune_statement(LATEST))
    con.executemany("INSERT INTO krx_weekly (price_dd, ticker, market) VALUES (?,?,'KS')",
                    [("20261023", "A"), ("20271015", "A"), ("20271015", "E")])
    later = "20271015"
    table = sorted(_rows_of(con), key=lambda r: (r[1], r[0]))
    ye = R.year_end_rows(table, later)
    expected = {r for r in table if R.is_retained(r[0], r[1], later, ye)}
    _q(con, R.prune_statement(later))
    assert _rows_of(con) == expected
    assert ("B", "20211217") in expected and ("C", "20220603") in expected and ("D", "20230310") in expected


def test_sql_builders_accept_table_and_alias_but_reject_injection():
    sql, _ = R.keep_predicate(LATEST, table="krx_weekly_backup", alias="k")
    assert "k.price_dd >= %s" in sql and "FROM krx_weekly_backup GROUP BY" in sql and "(k.ticker, k.price_dd) IN" in sql
    for bad in ("krx_weekly; DROP TABLE x", "krx weekly", "1abc", "", "a-b"):
        with pytest.raises(ValueError):
            R.keep_predicate(LATEST, table=bad)
        with pytest.raises(ValueError):
            R.prune_where(LATEST, alias=bad)
    with pytest.raises(ValueError):
        R.keep_select(LATEST, columns="w.*; DROP TABLE x")
    with pytest.raises(ValueError):
        R.prune_statement("2026-10-16")


def test_prune_where_is_a_top_level_and_so_it_can_become_an_anti_join():
    """OR 안의 서브쿼리는 반조인으로 풀리지 않는다(운영 실측: 기본 work_mem 에서 사실상 끝나지 않음). prune_where 는 OR 이 없는 최상위 AND 여야 한다."""
    where, _ = R.prune_where(LATEST)
    assert " OR " not in where and where.startswith("w.price_dd < %s AND NOT EXISTS (")
    keep, _ = R.keep_predicate(LATEST)
    assert " OR " in keep and "HAVING max(price_dd) < %s" in keep               # 정본(코디네이터 확정 꼴)
    # 성능 함정이 docstring 에 적혀 있다 — 이 술어를 DB 에 돌리는 코드가 읽도록
    assert "work_mem" in (R.keep_predicate.__doc__ or "") and "SET LOCAL work_mem" in (R.__doc__ or "")
