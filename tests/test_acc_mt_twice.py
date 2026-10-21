# -*- coding: utf-8 -*-
"""결산월을 **두 번** 바꾼 회사(131100) — 더 옛 변경도 바르게 읽고 전환기를 둘 다 가린다(261021 DATA-FY2).

131100 의 사업보고서(DART 정기공시 목록): 2017.12·2018.12 | 2019.06(전환기 6개월)·2020.06·2021.06·2022.06 | 2022.12(전환기 6개월)·2023.12~2025.12.
  · 2019 에 12 → 6: 사업연도 표기 2018 까지는 12월, 2019(2019.01~06 여섯 달)가 전환기.
  · 2022 에 6 → 12: 2022.06(12개월)과 2022.12(2022.07~12 여섯 달)가 같은 표기라 DART 는 늦은 것(전환기)만 준다.
#168(한 번 바꾼 회사만)은 최근 변경(6 → 12, 2022)만 알아서 2018 을 6월로 읽고(`cell_end(2018, 4)` 2018.06 — 실제 2018.12) 2019 전환기 6개월 순이익을 연간 값으로 썼다.

DART 0콜. DB 는 Postgres 17(도커 없으면 건너뜀).
"""
from __future__ import annotations

import calendar
from datetime import date

import pytest

from open_proxy_mcp.services import price_multiple_data as S
from open_proxy_mcp.services import report_calendar as rcal
from scripts import market_fund_quarterly as Q
from tests.test_acc_mt_change import (F032, F154, F222, F334, REPORTS, _days, _load)

#: 2026-10-20 DART 정기공시 목록(사업보고서 이름의 (표기 연도, 기준월), dq2 캐시 list-131100) — 정정·첨부 공시는 같은 달이라 한 번만.
REPORTS_131100 = [(2017, 12), (2018, 12), (2019, 6), (2020, 6), (2021, 6), (2022, 6), (2022, 12), (2023, 12), (2024, 12), (2025, 12)]
F131 = rcal.Fiscal(12, False, (), 6, 2022, 12, 2019)       # 131100 — 12월 → 6월(2019) → 12월(2022)


def test_listing_of_131100_finds_both_changes():
    want = Q.AccMt(12, 6, 2022, 12, 2019)
    assert Q.classify_reports(REPORTS_131100) == want
    assert rcal.fiscal(want.month, "131100", want.prev, want.since, want.prev2, want.since2) == F131
    # 정정·순서가 달라도 같다 · 한 번 바꾼 네 회사는 더 옛 변경이 없다
    assert Q.classify_reports(list(reversed(REPORTS_131100)) + REPORTS_131100) == want
    for t, a in (("334970", Q.AccMt(6, 12, 2023)), ("032800", Q.AccMt(6, 12, 2022)), ("154030", Q.AccMt(12, 9, 2025)), ("222160", Q.AccMt(12, 3, 2024))):
        assert Q.classify_reports(REPORTS[t]) == a and a.prev2 is None and a.since2 is None


@pytest.mark.parametrize("reports, want", [
    # 옛 달이 맨 앞에서 하나뿐이어도(목록이 2018년부터라 잘린 꼴) 그 뒤 달이 둘 이상 이어지면 변경으로 본다
    ([(2018, 12), (2019, 6), (2020, 6), (2021, 6), (2021, 12), (2022, 12)], Q.AccMt(12, 6, 2021, 12, 2019)),
    # 더 옛 달이 목록 맨 앞이 아닌 곳에서 하나뿐이면(결산기마다 달이 바뀌는 리츠와 가를 수 없다) 더 옛 변경은 적지 않는다 — 최근 변경은 그대로
    ([(2018, 12), (2019, 3), (2020, 6), (2021, 6), (2022, 12), (2023, 12)], Q.AccMt(12, 6, 2022)),
    # 더 옛 달이 둘 이상 이어지면 변경이다
    ([(2017, 3), (2018, 3), (2019, 6), (2020, 6), (2021, 9), (2022, 9)], Q.AccMt(9, 6, 2021, 3, 2019)),
    # 세 번 바꾼 회사(맨 앞 달이 하나 더 있다): 더 옛 달이 목록 맨 앞이 아니어도 둘 이상 이어지면 그 변경을 적는다 — 가장 최근 둘만 안다
    ([(2017, 9), (2018, 3), (2019, 3), (2020, 6), (2021, 6), (2022, 12), (2023, 12)], Q.AccMt(12, 6, 2022, 3, 2020)),
    # 가운데 달이 하나뿐이면 지금까지처럼 12(리츠와 가를 수 없다)
    ([(2017, 12), (2018, 12), (2019, 6), (2020, 9), (2021, 9)], Q.AccMt(12)),
])
def test_older_change_needs_the_runs_to_continue(reports, want):
    assert Q.classify_reports(reports) == want


def test_stored_older_change_is_kept_when_the_scan_window_is_partial():
    got = Q.decide_acc_mt(Q.AccMt(12, 6, 2022, 12, 2019), [(2026, 12)], None, complete=False)
    assert got == Q.AccMt(12, 6, 2022, 12, 2019)
    assert Q.decide_acc_mt(None, REPORTS_131100, None, complete=True) == Q.AccMt(12, 6, 2022, 12, 2019)


# ═══ 달력 ═════════════════════════════════════════════════════════════════════════════════════════
def test_month_of_and_transitions_follow_both_changes():
    assert [rcal.month_of(y, F131) for y in range(2016, 2026)] == [12, 12, 12, 6, 6, 6, 12, 12, 12, 12]
    assert rcal.changes(F131) == ((2019, 12, 6), (2022, 6, 12))
    assert rcal.transition_years(F131) == (2019, 2022)
    assert [y for y in range(2016, 2026) if rcal.is_transition(y, F131)] == [2019, 2022]
    assert rcal.transition_change(2019, F131) == (12, 6) and rcal.transition_change(2022, F131) == (6, 12) and rcal.transition_change(2020, F131) is None
    assert rcal.cell_end(2018, 4, F131) == date(2018, 12, 31)               # #168 은 2018.06 이라 읽었다
    assert rcal.cell_end(2019, 4, F131) == date(2019, 6, 30) and rcal.cell_end(2019, 1, F131) == date(2019, 9, 30)
    assert rcal.cell_end(2022, 4, F131) == date(2022, 12, 31) and rcal.cell_end(2023, 1, F131) == date(2023, 3, 31)
    # 한 번 바꾼 네 회사와 안 바꾼 회사의 읽기는 그대로(더 옛 변경이 없다)
    for f, years in ((F334, (2022, 2023)), (F032, (2021, 2022)), (F154, (2024, 2025)), (F222, (2023, 2024))):
        assert rcal.transition_years(f) == (f.since,) and rcal.changes(f) == ((f.since, f.prev, f.month),)
        assert [rcal.month_of(y, f) for y in years] == [f.prev, f.month]
    assert rcal.changes(rcal.Fiscal(3)) == () and rcal.changes(12) == () and rcal.transition_years(6) == ()


def test_invalid_older_change_is_ignored():
    assert rcal.fiscal(12, "131100", 6, 2022, 6, 2019) == rcal.Fiscal(12, False, (), 6, 2022)       # 더 옛 결산월이 그 다음 결산월과 같다
    assert rcal.fiscal(12, "131100", 6, 2022, 12, 2022) == rcal.Fiscal(12, False, (), 6, 2022)      # since2 < since 여야 한다
    assert rcal.fiscal(12, "131100", 6, 2022, 12, 2023) == rcal.Fiscal(12, False, (), 6, 2022)
    assert rcal.fiscal(12, "131100", 6, 2022, 13, 2019) == rcal.Fiscal(12, False, (), 6, 2022)
    assert rcal.fiscal(12, "131100", 6, 2022, 12, None) == rcal.Fiscal(12, False, (), 6, 2022)      # 둘 다 있어야 한다
    assert rcal.fiscal(12, "131100", None, None, 12, 2019) == rcal.Fiscal(12)                       # 최근 변경이 없으면 쓰지 않는다
    assert rcal.fiscal(12, "131100", 6, 2022, "12", "2019") == F131                                 # 문자열도 받는다
    assert rcal.fiscal(F131, "131100") == F131 and rcal.fiscal(F131, "131100", 3, 2021).prev2 == 12
    assert rcal.changes_from_rows([("131100", 12, 6, 2022, 12, 2019), ("A", 12, 6, 2022, 6, 2019), ("B", 12, 6, 2022), ("C", 12, None, None, 12, 2019)]) == \
        {"131100": (12, 6, 2022, 12, 2019), "A": (12, 6, 2022, 0, 0), "B": (12, 6, 2022, 0, 0)}


def test_prior_columns_name_the_cell_each_prior_year_occupies():
    """사업보고서 표기 y 의 (전기, 전전기) 칸이 어느 표기의 자리인가 — (표기, 쓸 수 있나, 까닭)."""
    pc = lambda y: rcal.prior_columns(y, F131)  # noqa: E731
    assert pc(2019) == [(2018, True, ""), (2017, True, "")]                                  # 전환기 보고서의 전기 = 2018.12 (12개월)
    assert pc(2020) == [(2019, False, "transition"), (2018, True, "")]                       # 전기 = 2019.06 (6개월 — 연간 칸으로 쓰지 않는다)
    assert pc(2021) == [(2020, True, ""), (2019, False, "transition")]
    assert pc(2022) == [(2022, False, "collision"), (2021, True, "")]                         # 전환기 보고서(2022.12)의 전기 = 2022.06 (같은 표기를 전환기가 차지)
    assert pc(2023) == [(2022, False, "transition"), (2022, False, "collision")]
    assert pc(2024) == [(2023, True, ""), (2022, False, "transition")]
    assert pc(2025) == [(2024, True, ""), (2023, True, "")]
    # #168 은 2019 를 쓸 수 있는 칸으로 읽었고(6개월 순이익을 연간 정정 칸 후보로), 2018 을 6월 결산으로 읽었다
    assert rcal.prior_columns(2020, rcal.Fiscal(12, False, (), 6, 2022))[0] == (2019, True, "")


def _old_prev_annual_end(e, f):
    """#168 의 `_prev_annual_end`(변경 하나) — 한 번 바꾼 회사의 읽기가 그대로인지 대조한다."""
    last = lambda y, m: date(y, m, calendar.monthrange(y, m)[1])  # noqa: E731
    if not f.since:
        return last(e.year - 1, e.month)
    trans = last(f.since, f.month)
    old_last = last(f.since if f.prev < f.month else f.since - 1, f.prev)
    if e == trans:
        return old_last
    if e <= old_last:
        return last(e.year - 1, f.prev)
    return last(e.year - 1, f.month)


def _year_ends(f):
    """그 회사의 실제 결산일들 — 표기 y 의 결산월 말 + 변경마다 옛 마지막 결산일(전환기 앞)."""
    last = lambda y, m: date(y, m, calendar.monthrange(y, m)[1])  # noqa: E731
    out = {last(y, rcal.month_of(y, f)) for y in range(2012, 2032)}
    for since, old, new in rcal.changes(f):
        out.add(last(since if old < new else since - 1, old))
    return sorted(out)


def test_one_change_reads_exactly_as_before_for_every_year_end():
    for f in (F334, F032, F154, F222, rcal.Fiscal(12), rcal.Fiscal(3), rcal.Fiscal(9, True, (), 12, 2020), rcal.Fiscal(2, False, (), 6, 2019)):
        for e in _year_ends(f):
            assert rcal._prev_annual_end(e, f) == _old_prev_annual_end(e, f), (f, e)


def test_every_year_end_of_131100_chains_back_to_a_real_year_end():
    """사업보고서 끝에서 한 결산기씩 거슬러 올라가면 실제 결산일을 지난다: 2025.12 → 2024.12 → 2023.12 → 2022.12(전환기) → 2022.06 → 2021.06 → … → 2019.06(전환기) → 2018.12 → 2017.12."""
    e, seen = date(2025, 12, 31), []
    for _ in range(11):
        seen.append(e.strftime("%Y.%m"))
        e = rcal._prev_annual_end(e, F131)
    assert seen == ["2025.12", "2024.12", "2023.12", "2022.12", "2022.06", "2021.06", "2020.06", "2019.06", "2018.12", "2017.12", "2016.12"]


def test_131100_pit_never_looks_ahead_and_is_monotone():
    for f in (F131, rcal.Fiscal(12, False, (), 6, 2022, 9, 2020), rcal.Fiscal(6, False, (), 12, 2022, 3, 2019)):
        last = None
        for d in _days(date(2016, 1, 1), date(2031, 12, 31)):
            cell = rcal.pit_cell(d, f)
            assert rcal.cell_available(cell[0], cell[1], f) <= d, (f, d, cell)
            end = rcal.cell_end(cell[0], cell[1], f)
            assert last is None or end >= last, (f, d, cell)
            last = end
            fy = rcal.pit_annual(d, f)
            assert rcal.cell_available(fy, 4, f) <= d and rcal.cell_available(fy + 1, 4, f) > d, (f, d, fy)


def test_131100_picks_the_right_cell_around_both_changes():
    # 2019.12 반기보고서(2020-02-14 공시)가 나오기 전 · 후 — 2019 의 칸은 2019.09 (1분기)·2019.12 (반기)
    assert rcal.pit_cell("20191113", F131) == (2019, 4)             # 2019.06 사업보고서(전환기)가 마지막
    assert rcal.pit_cell("20191114", F131) == (2019, 1)             # 2019.09 분기보고서(2019-11-14 공시)
    assert rcal.pit_cell("20200214", F131) == (2019, 2)
    # 2018 은 12월 결산 — 2018.12 사업보고서는 2019-04-01 부터(#168 은 2018.06 으로 읽어 2018-10-01 부터 썼다)
    assert rcal.pit_annual("20190331", F131) == 2017 and rcal.pit_annual("20190401", F131) == 2018
    assert rcal.cell_available(2018, 4, F131) == date(2019, 4, 1)
    assert rcal.cell_available(2018, 4, rcal.Fiscal(12, False, (), 6, 2022)) == date(2018, 10, 1)
    # 2022.12 전환기 사업보고서 · 그 뒤 12월 결산
    assert rcal.pit_annual("20230331", F131) == 2021 and rcal.pit_annual("20230401", F131) == 2022
    # TTM 의 직전 연간 — 2020 1분기(2020.09)는 FY2020(2020.06). 2019 1분기(2019.09)·반기(2019.12)는 전환기 FY2019(2019.06)이고, 2019 3분기 칸(2019.03)은 그 앞 FY2018(2018.12)
    assert rcal.prior_annual(2020, 1, F131) == 2020 and [rcal.prior_annual(2019, q, F131) for q in (1, 2, 3)] == [2019, 2019, 2018]
    assert rcal.prior_annual(2023, 1, F131) == 2022
    assert rcal.quarter_labels_after(2018, F131) == {1: 2019, 2: 2019, 3: 2019}              # 2018.12 결산 다음 분기 말 3·6·9월 — 모두 2019
    assert rcal.quarter_labels_after(2020, F131) == {1: 2020, 2: 2020, 3: 2021}              # 2020.06 결산 다음 분기 말 9·12·3월


def test_mask_transition_hides_both_transition_years():
    fin = S._rpit.PitFin({2018: (-130.0e8, 312.0e8), 2019: (-72.0e8, 335.0e8), 2020: (-248.0e8, 151.0e8), 2021: (-257.0e8, 258.0e8), 2022: (-24.0e8, 770.0e8), 2023: (-165.0e8, 644.0e8)})
    fin.gate(2019, ("20200214", (-70.0e8, 335.0e8)))
    out = rcal.mask_transition(fin, F131)
    assert out is fin and fin[2019] == (None, 335.0e8) and fin[2022] == (None, 770.0e8) and fin.gates[2019] == ("20200214", (None, 335.0e8))
    assert fin[2018] == (-130.0e8, 312.0e8) and fin[2020] == (-248.0e8, 151.0e8) and fin[2023] == (-165.0e8, 644.0e8)
    only = S._rpit.PitFin({2019: (1.0, 2.0), 2022: (3.0, 4.0)})
    assert rcal.mask_transition(only, rcal.Fiscal(12, False, (), 6, 2022))[2019] == (1.0, 2.0)     # 한 번 바꾼 읽기는 2022 만


# ═══ 서버 안내 문구 ═══════════════════════════════════════════════════════════════════════════════
def test_note_and_warning_tell_each_change():
    note = S._fye_note(F131)
    assert note.startswith(S._FYE_NOTE_HEAD + "12월") and "결산월을 2번 바꿨다" in note and "12월 → 6월(2019 사업연도부터)" in note and "6월 → 12월(2022 사업연도부터)" in note
    assert "2019·2022" in note
    assert "결산월을 6월에서 12월로 바꿨다" in S._fye_note(rcal.Fiscal(12, False, (), 6, 2022))      # 한 번 바꾼 회사 문구는 그대로


@pytest.fixture(autouse=True)
def _fresh_changes_cache():
    S._fye_changes_cache = None
    yield
    S._fye_changes_cache = None


def test_server_reads_the_older_change(monkeypatch):
    import asyncio
    calls = []

    def rows(sql, params=()):
        calls.append(" ".join(sql.split()))
        return [("131100", 12, 6, 2022, 12, 2019), ("334970", 6, 12, 2023, None, None)]
    monkeypatch.setattr(S, "_pg_rows", rows)
    assert asyncio.run(S._fiscal_for("131100", 12)) == F131 and asyncio.run(S._fiscal_for("334970", 6)) == F334
    assert "to_jsonb(d)->>'acc_mt_prev2'" in calls[0] and "to_jsonb(d)->>'acc_mt_since2'" in calls[0] and len(calls) == 1
    # 더 옛 변경 칸이 아직 없는 표(DDL 전)에서는 최근 변경만 — 질의가 깨지지 않는다
    S._fye_changes_cache = None
    monkeypatch.setattr(S, "_pg_rows", lambda sql, params=(): [("131100", 12, 6, 2022, None, None)])
    assert asyncio.run(S._fiscal_for("131100", 12)) == rcal.Fiscal(12, False, (), 6, 2022)


# ═══ 파생·seed·DDL ══════════════════════════════════════════════════════════════════════════════
psycopg = pytest.importorskip("psycopg")
from tests.test_acc_mt_change import FINSTAT_Q_DDL, FINSTAT_Y_DDL, _q, _seed_cells, fdb  # noqa: E402,F401

# 131100 꼴: 2018 사업(12월) · 2019 전환기(6개월) · 2020 사업(2020.06) · 2020 1·2분기 칸 = 2020.09·2020.12 · 2021 사업 · 2021 1·2분기 칸 = 2021.09·2021.12
CELLS_131 = {(2018, 4): (-130.0, 312.0), (2019, 4): (-72.0, 335.0), (2020, 1): (-90.0, 160.0), (2020, 2): (-129.0, 371.0),
             (2020, 4): (-248.0, 151.0), (2021, 1): (-1.0, 357.0), (2021, 2): (-40.0, 407.0), (2021, 4): (-257.0, 258.0)}


def test_derive_skips_both_transition_net_incomes(fdb):
    with psycopg.connect(fdb, autocommit=True) as c:
        # TA = 두 번 바꾼 회사(12→6, 2019 → 12, 2022) · TB = 최근 변경만 아는 읽기 · 둘 다 2021 까지의 칸
        c.execute("INSERT INTO dart_fundamentals (ticker, corp_code, fetched, acc_mt, acc_mt_prev, acc_mt_since, acc_mt_prev2, acc_mt_since2) VALUES "
                  "('TA','cA','ok',12,6,2022,12,2019), ('TB','cB','ok',12,6,2022,NULL,NULL)")
        _seed_cells(c, "TA", CELLS_131)
        _seed_cells(c, "TB", CELLS_131)
    Q.derive_fundamentals()
    got = {r[0]: r[1:] for r in _q(fdb, "SELECT ticker, ni_fy, ni_ttm, eq_fy, eq_mrq FROM dart_fundamentals")}
    # TA — 최신 칸 (2021,2)=2021.12: 최신 완결 연간은 FY2021(2021.06). TTM = FY2021 + 누적(2021.12) − 누적(2020.12) = −257 + (−40) − (−129)
    assert got["TA"] == (-257.0, -168.0, 258.0, 407.0)
    assert got["TB"] == got["TA"]            # 이 칸들은 2020 이후라 최근 변경만 아는 읽기와 같다 — 달라지는 곳은 아래 2019 앞뒤 칸
    # 2019 앞뒤: 전환기 FY2019(2019.06, 6개월)의 순이익은 연간 값이 아니다 → 그것을 직전 연간으로 쓰는 2019 의 세 칸 TTM 은 비어 있고,
    #   2020 1분기(2020.09)는 FY2020(12개월) + 누적(2020.09) − 누적(2019.09) 이다.
    cells = {**CELLS_131, (2019, 1): (-36.0, 369.0), (2019, 2): (-81.0, 323.0)}
    # DB 에서 돌려도 같다 — 2019 까지의 칸만 있는 회사(TC): 2019 반기(2019.12) TTM 의 직전 연간이 전환기라 ni_ttm 은 비고 ni_fy 도 없다(자본은 그 날 잔액)
    with psycopg.connect(fdb, autocommit=True) as c:
        c.execute("INSERT INTO dart_fundamentals (ticker, corp_code, fetched, acc_mt, acc_mt_prev, acc_mt_since, acc_mt_prev2, acc_mt_since2) VALUES ('TC','cC','ok',12,6,2022,12,2019)")
        _seed_cells(c, "TC", {(2018, 2): (-70.0, 300.0), (2018, 4): (-130.0, 312.0), (2019, 1): (-36.0, 369.0), (2019, 2): (-81.0, 323.0), (2019, 4): (-72.0, 335.0)})
    Q.derive_fundamentals(["TC"])
    assert _q(fdb, "SELECT ni_fy, ni_ttm, eq_fy, eq_mrq FROM dart_fundamentals WHERE ticker='TC'") == [(None, None, 335.0, 323.0)]
    assert Q.ttm_from_cells({**cells, (2019, 4): (None, 335.0)}, {}, F131, 2019, 1) == (None, False)
    assert Q.ttm_from_cells({**cells, (2019, 4): (None, 335.0)}, {}, F131, 2019, 2) == (None, False)
    assert Q.ttm_from_cells({**cells, (2019, 4): (None, 335.0)}, {}, F131, 2020, 1) == (-248.0 + (-90.0) - (-36.0), False)


def test_seed_empties_both_transition_years_once(fdb):
    with psycopg.connect(fdb, autocommit=True) as c:
        c.execute("INSERT INTO dart_fundamentals (ticker, corp_code, fetched, acc_mt, acc_mt_prev, acc_mt_since, acc_mt_prev2, acc_mt_since2) VALUES "
                  "('TA','cA','ok',12,6,2022,12,2019), ('TB','cB','ok',12,6,2022,NULL,NULL), ('TD','cD','ok',12,NULL,NULL,NULL,NULL)")
        for t in ("TA", "TB", "TD"):
            c.execute("INSERT INTO dart_finstat_y (ticker, fy, fs, ni, eq, fetched) VALUES (%s,2019,'CFS',-72,335,'ok'), (%s,2021,'CFS',-257,258,'ok'), (%s,2022,'CFS',-24,770,'ok')", (t, t, t))
        c.execute("INSERT INTO dart_finstat_q (ticker, fy, quarter, reprt_code, fs, ni_cum, eq, fetched) VALUES "
                  "('TA',2019,4,'11011','CFS',-72,335,'ok'), ('TA',2022,4,'11011','CFS',-24,770,'ok'), ('TB',2019,4,'11011','CFS',-72,335,'ok'), ('TB',2022,4,'11011','CFS',-24,770,'ok')")
    Q.seed_q4()
    rows = {(t, fy): r for t, fy, *r in _q(fdb, "SELECT ticker, fy, ni_cum, eq, ni_case, fetched FROM dart_finstat_q WHERE quarter=4")}
    assert rows[("TA", 2019)] == [None, 335.0, "TRANSITION", "ok"] and rows[("TA", 2022)] == [None, 770.0, "TRANSITION", "ok"]       # 전환기 둘 다 비우고 표시
    assert rows[("TA", 2021)] == [-257.0, 258.0, None, "ok"]                                                                          # 다른 해는 그대로
    assert rows[("TB", 2019)] == [-72.0, 335.0, None, "ok"] and rows[("TB", 2022)] == [None, 770.0, "TRANSITION", "ok"]             # 최근 변경만 아는 읽기는 2022 만
    assert rows[("TD", 2019)] == [-72.0, 335.0, None, "ok"] and rows[("TD", 2022)] == [-24.0, 770.0, None, "ok"]                    # 안 바꾼 회사는 건드리지 않는다
    x1 = _q(fdb, "SELECT xmin::text FROM dart_finstat_q")
    Q.seed_q4()
    assert _q(fdb, "SELECT xmin::text FROM dart_finstat_q") == x1                                                                    # 두 번째 실행은 아무것도 다시 쓰지 않는다


def test_load_fye_map_returns_the_older_change(fdb):
    with psycopg.connect(fdb, autocommit=True) as c:
        c.execute("INSERT INTO dart_fundamentals (ticker, corp_code, fetched, acc_mt, acc_mt_prev, acc_mt_since, acc_mt_prev2, acc_mt_since2) VALUES "
                  "('TA','cA','ok',12,6,2022,12,2019), ('TB','cB','ok',6,12,2023,NULL,NULL)")
        got = rcal.load_fye_map(c)
    assert got == {"TA": F131, "TB": F334}


def test_older_change_columns_ddl(pg17):
    ddl = _load("fundamentals_ddl")
    assert "acc_mt_prev2" in ddl.render() and "acc_mt_since2" in ddl.render() and "acc_mt_change2_check" in ddl.render()
    with psycopg.connect(pg17, autocommit=True) as c:
        # #168 이 만든 표 꼴(더 옛 변경 칸 없음)
        c.execute("CREATE TABLE dart_fundamentals(ticker text PRIMARY KEY, fetched text, acc_mt smallint, acc_mt_prev smallint, acc_mt_since integer)")
        c.execute("ALTER TABLE dart_fundamentals ADD CONSTRAINT dart_fundamentals_acc_mt_check CHECK (acc_mt BETWEEN 1 AND 12)")
        c.execute("ALTER TABLE dart_fundamentals ADD CONSTRAINT dart_fundamentals_acc_mt_prev_check CHECK (acc_mt_prev BETWEEN 1 AND 12)")
        c.execute("ALTER TABLE dart_fundamentals ADD CONSTRAINT dart_fundamentals_acc_mt_since_check CHECK (acc_mt_since BETWEEN 1900 AND 2200)")
        c.execute("ALTER TABLE dart_fundamentals ADD CONSTRAINT dart_fundamentals_acc_mt_change_check CHECK ((acc_mt_prev IS NULL) = (acc_mt_since IS NULL))")
        c.execute("INSERT INTO dart_fundamentals VALUES ('A','ok',12,6,2022)")
        st = ddl.check(c, out=lambda *_: None)
        assert st[0] == "todo" and "acc_mt_prev2" in st[1] and "acc_mt_since2" in st[1]
    assert ddl.apply(pg17, out=lambda *_: None) == (True, "칸·확인 제약 있음")
    assert ddl.apply(pg17, out=lambda *_: None) == (True, "칸·확인 제약 있음")                                                    # 멱등
    with psycopg.connect(pg17, autocommit=True) as c:
        assert ddl.check(c, out=lambda *_: None)[0] == "done"
        c.execute("UPDATE dart_fundamentals SET acc_mt_prev2 = 12, acc_mt_since2 = 2019")
        for bad in ("acc_mt_prev2 = 13, acc_mt_since2 = 2019", "acc_mt_prev2 = 12, acc_mt_since2 = 1800", "acc_mt_prev2 = 12, acc_mt_since2 = NULL",
                    "acc_mt_prev2 = 12, acc_mt_since2 = 2022", "acc_mt_prev2 = 12, acc_mt_since2 = 2023"):
            with pytest.raises(psycopg.errors.CheckViolation):
                c.execute(f"UPDATE dart_fundamentals SET {bad}")
        c.execute("UPDATE dart_fundamentals SET acc_mt_prev = NULL, acc_mt_since = NULL, acc_mt_prev2 = NULL, acc_mt_since2 = NULL")
        with pytest.raises(psycopg.errors.CheckViolation):                                  # 최근 변경이 없으면 더 옛 변경도 못 적는다
            c.execute("UPDATE dart_fundamentals SET acc_mt_prev2 = 12, acc_mt_since2 = 2019")
        c.execute("DROP TABLE dart_fundamentals")
    with psycopg.connect(pg17, autocommit=True) as c:                                      # 새 DB: CREATE TABLE 이 같은 칸·제약을 만든다
        c.execute(Q.DDL_FUNDAMENTALS)
        assert ddl.check(c, out=lambda *_: None)[0] == "done"
    assert ddl.apply(pg17, out=lambda *_: None)[0] is True


def test_refresh_acc_mt_writes_the_older_change_and_degrades_without_the_columns(fdb, monkeypatch):
    """`--acc-mt --tickers` — 사업보고서 전체 이력에서 두 변경을 읽어 쓴다. 더 옛 변경 칸이 없는 표에서는 최근 변경 하나만 쓴다."""
    import asyncio
    from types import SimpleNamespace
    with psycopg.connect(fdb, autocommit=True) as c:
        c.execute("INSERT INTO dart_fundamentals (ticker, corp_code, fetched, acc_mt, acc_mt_prev, acc_mt_since) VALUES ('131100','c131','ok',12,6,2022), ('005930','c005','ok',12,NULL,NULL)")

    async def hist(c_, cc, today):
        return [(y, m) for y, m in REPORTS_131100] if cc == "c131" else [(2024, 12), (2025, 12)], 1
    monkeypatch.setattr(Q, "_company_reports", hist)
    monkeypatch.setattr(Q, "SLEEP", 0)
    import open_proxy_mcp.dart.client as dc
    monkeypatch.setattr(dc, "get_dart_client", lambda: SimpleNamespace())
    out = asyncio.run(Q.refresh_acc_mt(tickers=["131100", "005930"], today=date(2026, 10, 21)))
    assert out == {"131100": 12}
    assert _q(fdb, "SELECT acc_mt, acc_mt_prev, acc_mt_since, acc_mt_prev2, acc_mt_since2 FROM dart_fundamentals WHERE ticker='131100'") == [(12, 6, 2022, 12, 2019)]
    assert asyncio.run(Q.refresh_acc_mt(tickers=["131100"], today=date(2026, 10, 21))) == {}                      # 멱등 — 바뀐 것 없음
    # 더 옛 변경 칸이 없는 표(#168 꼴): 최근 변경 하나만 쓰고 깨지지 않는다
    with psycopg.connect(fdb, autocommit=True) as c:
        c.execute("ALTER TABLE dart_fundamentals DROP COLUMN acc_mt_prev2, DROP COLUMN acc_mt_since2")
        c.execute("UPDATE dart_fundamentals SET acc_mt_prev = 9, acc_mt_since = 2020 WHERE ticker='131100'")
    out = asyncio.run(Q.refresh_acc_mt(tickers=["131100"], today=date(2026, 10, 21)))
    assert out == {"131100": 12} and _q(fdb, "SELECT acc_mt_prev, acc_mt_since FROM dart_fundamentals WHERE ticker='131100'") == [(6, 2022)]
    assert asyncio.run(Q.refresh_acc_mt(tickers=["131100"], today=date(2026, 10, 21))) == {}                      # 칸이 없어도 멱등(저장할 수 없는 더 옛 변경 때문에 매번 「바뀜」으로 세지 않는다)


# ═══ 정정 칸 후보(전기·전전기 칸의 자리) ═════════════════════════════════════════════════════════════
def test_prior_candidates_skip_the_transition_columns_of_both_changes():
    from scripts import market_val_series as MVS
    from tests.test_restated_term_shift import _rows
    B = 100_000_000
    # 2020 사업보고서(2020.06) — 전기 = 2019.06 전환기 6개월(연간 값이 아니다) · 전전기 = 2018.12 (12개월, FY2018 의 자리)
    r2020 = _rows("20200928000251", (15, 14, 13), (-248 * B, -72 * B, -130 * B), (151 * B, 335 * B, 312 * B))
    assert [c[0] for c in MVS.prior_candidates(r2020, 2020)] == [2019, 2018]                         # 종전 읽기 — 전환기 6개월 순이익이 FY2019 정정 칸 후보
    assert MVS.prior_candidates(r2020, 2020, rcal.Fiscal(12, False, (), 6, 2022)) == [(2019, -72.0 * B, 335.0 * B), (2018, -130.0 * B, 312.0 * B)]   # #168 도 같다(옛 변경 모름)
    assert MVS.prior_candidates(r2020, 2020, F131) == [(2018, -130.0 * B, 312.0 * B)]
    # 2021 사업보고서(2021.06) — 전기 = 2020.06 · 전전기 = 2019.06 전환기
    r2021 = _rows("20210917000602", (16, 15, 14), (-257 * B, -248 * B, -72 * B), (258 * B, 151 * B, 335 * B))
    assert MVS.prior_candidates(r2021, 2021, F131) == [(2020, -248.0 * B, 151.0 * B)]
    # 2023 사업보고서(2023.12) — 전기 = 2022.12 전환기(6개월) · 전전기 = 2022.06 (표기 2022 를 전환기가 차지) → 쓸 칸이 없다
    r2023 = _rows("20240320000545", (21, 20, 19), (-165 * B, -24 * B, -257 * B), (644 * B, 770 * B, 258 * B))
    assert MVS.prior_candidates(r2023, 2023, F131) == []
    # 2024 사업보고서(2024.12) — 전기 = 2023.12 · 전전기 = 2022.12 전환기
    r2024 = _rows("20250321001356", (22, 21, 20), (22 * B, -165 * B, -24 * B), (666 * B, 644 * B, 770 * B))
    assert MVS.prior_candidates(r2024, 2024, F131) == [(2023, -165.0 * B, 644.0 * B)]


def test_firm_warning_names_the_change_of_the_transition_report(monkeypatch):
    """실시간 firm — 전환기 경고의 옛·새 결산월은 그 전환기가 속한 변경의 것이다. (최근 변경이 아니라 더 옛 변경의 전환기일 때도.)"""
    from tests.test_pmd_reit_fy0_year import _annual, _interim, _run
    reports = {(2025, "11011"): _annual("-1,366,478,768", "24,100,295,993", "1,905,122,329"),
               (2026, "11012"): _interim("1,203,428,623", "25,279,446,526"), (2025, "11012"): _interim("1,143,094,692", "24,814,861,748")}

    async def changes():
        return {"154030": (12, 3, 2026, 9, 2025)}                    # 가정: 2025 에 9 → 3 (더 옛 변경의 전환기), 2026 에 3 → 12
    monkeypatch.setattr(S, "_fye_changes", changes)
    p, _ = _run(monkeypatch, overview_acc_mt="12", db_acc_mt=12, reports=reports, alot={}, ticker="154030", cap=80_000_000_000, price=2500)
    ws = [w for w in p["data"]["warnings"] if "전환기 — 12개월이 아님" in w]
    assert len(ws) == 1 and "9월에서 3월로 바꾼 첫 사업보고서(FY2025" in ws[0]
