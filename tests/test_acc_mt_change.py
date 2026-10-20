# -*- coding: utf-8 -*-
"""결산월을 바꾼 회사 — 최근 결산월을 따르고, 바꾸기 전 칸은 옛 결산월로, 전환기는 연간 칸에 넣지 않는다(261021 DATA-FY2 · API3).

종전: ① `decide_acc_mt` 는 한 번 사업보고서 기준월이 둘 보이면 영원히 12(334970 은 2023.06 부터 6월 결산인데 12월로 읽혔다) ② 12월 결산이 아닌 회사는 결산월
하나로 모든 사업연도 칸을 읽어 바꾸기 전 칸이 틀렸다 ③ 결산월을 바꾼 첫 사업보고서(전환기 — 3·6·9개월)가 연간 칸에 들어갔다.
이제 `Fiscal(month, foreign, cells, prev, since)` — 표기 `fy` 의 결산월은 `month_of(fy)`(`fy < since` 면 옛 결산월), 전환기(`fy == since`)의 순이익은 연간이 아니다.
결산월을 안 바꾼 회사(12월 결산 포함)는 2016~2031 모든 날 종전 식과 글자까지 같다(아래 대조).

DART 0콜. DB 는 Postgres 17(도커 없으면 건너뜀).
"""
from __future__ import annotations

import asyncio
import calendar
import importlib.util
import pathlib
import sys
from datetime import date, timedelta

import pytest

from open_proxy_mcp.services import price_multiple_data as S
from open_proxy_mcp.services import report_calendar as rcal
from tests.pmd_fin_rows import pad_fin_rows
from scripts import market_fund_quarterly as Q

ROOT = pathlib.Path(__file__).resolve().parent.parent
CAP = 1.0e12

F334 = rcal.Fiscal(6, False, (), 12, 2023)       # 334970 — 12월 → 6월, 첫 사업연도 2023(2023.01~06 전환기 6개월)
F032 = rcal.Fiscal(6, False, (), 12, 2022)       # 032800 — 12월 → 6월, 첫 사업연도 2022
F154 = rcal.Fiscal(12, False, (), 9, 2025)       # 154030 — 9월 → 12월, 2025(2025.10~12 전환기 3개월)
F222 = rcal.Fiscal(12, False, (), 3, 2024)       # 222160 — 3월 → 12월, 2024(2024.04~12 전환기 9개월)

# 2026-10-20 DART 정기공시 목록(list.json, 사업보고서 이름의 (표기 연도, 기준월) — 정정·첨부 공시는 같은 달이라 한 번만)
REPORTS = {
    "334970": [(2019, 12), (2020, 12), (2021, 12), (2022, 12), (2023, 6), (2024, 6), (2025, 6), (2026, 6)],
    "032800": [(2017, 12), (2018, 12), (2019, 12), (2020, 12), (2021, 12), (2022, 6), (2023, 6), (2024, 6), (2025, 6), (2026, 6)],
    "154030": [(2017, 9), (2018, 9), (2019, 9), (2020, 9), (2021, 9), (2022, 9), (2023, 9), (2024, 9), (2025, 9), (2025, 12)],
    "222160": [(2018, 3), (2019, 3), (2020, 3), (2021, 3), (2022, 3), (2023, 3), (2024, 3), (2024, 12), (2025, 12)],
}


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(ROOT / "scripts"))
    spec.loader.exec_module(mod)
    return mod


# ═══ A. 안 바꾼 회사는 종전 식과 글자까지 같다 ═════════════════════════════════════════════════════
# 종전(결산월 하나) 구현 그대로 — 261018 `report_calendar` 의 식을 옮겼다.
_STEP = {1: 3, 2: 6, 3: 9, 4: 0}


def _old_last(y, m):
    return date(y, m, calendar.monthrange(y, m)[1])


def _old_add(y, m, n):
    k = (m - 1) + n
    return y + k // 12, k % 12 + 1


def _old_cell_end(fy, q, m):
    return _old_last(fy, (m + _STEP[q] - 1) % 12 + 1)


def _old_available(fy, q, m, foreign):
    if q == 4:
        y, mm = _old_add(fy, m, 5 if foreign else 4)
        return date(y, mm, 1)
    return _old_cell_end(fy, q, m) + timedelta(days=60 if foreign else 45)


def _old_pit_cell(t, m, foreign):
    if m == 12:
        md = t.month * 100 + t.day
        if md >= 1114:
            return (t.year, 3)
        if md >= 814:
            return (t.year, 2)
        if md >= 515:
            return (t.year, 1)
        return (t.year - 1, 4) if t.month >= 4 else (t.year - 1, 3)
    best, best_end = None, None
    for fy in (t.year - 2, t.year - 1, t.year):
        for q in (1, 2, 3, 4):
            if _old_available(fy, q, m, foreign) <= t:
                e = _old_cell_end(fy, q, m)
                if best_end is None or e > best_end:
                    best, best_end = (fy, q), e
    return best


def _old_pit_annual(t, m, foreign):
    if m == 12:
        return t.year - 1 if t.month >= 4 else t.year - 2
    fy = t.year
    while _old_available(fy, 4, m, foreign) > t:
        fy -= 1
    return fy


def _old_cell_month(q, m):
    return (m + _STEP[q] - 1) % 12 + 1


def _old_prior_annual(fy, q, m):
    return fy - 1 if q == 4 else (fy if m < _old_cell_month(q, m) else fy - 1)


def _days(a, b):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


def test_unchanged_calendars_equal_the_old_rules_every_day_2016_to_2031():
    """결산월 1~12 × 외국 상장사 기한 여부 — int·`Fiscal` 두 꼴로 `cell_end`·`cell_available`·`pit_cell`·`pit_annual`·`prior_annual`·`latest_annual`·
    `quarter_labels_after` 가 종전 식과 같다. 12월 결산은 빠른 길(`_pit_cell_dec`)도 그대로다."""
    days = list(_days(date(2016, 1, 1), date(2031, 12, 31)))
    n = 0
    for m in range(1, 13):
        for foreign in (False, True):
            fi = rcal.Fiscal(m, foreign)
            foreign = foreign and m != 12          # 12월 결산 외국 상장사는 종전 그대로(45·90일)
            for fy in range(2014, 2034):
                for q in (1, 2, 3, 4):
                    assert rcal.cell_end(fy, q, m) == rcal.cell_end(fy, q, fi) == _old_cell_end(fy, q, m)
                    assert rcal.cell_available(fy, q, fi) == _old_available(fy, q, m, foreign)
                    assert rcal.prior_annual(fy, q, m) == rcal.prior_annual(fy, q, fi) == _old_prior_annual(fy, q, m)
                assert rcal.quarter_labels_after(fy, m) == {q: fy + (1 if m + _STEP[q] > 12 else 0) for q in (1, 2, 3)}
                assert rcal.month_of(fy, fi) == m and not rcal.is_transition(fy, fi)
            for d in days:
                n += 1
                assert rcal.pit_cell(d, fi) == _old_pit_cell(d, m, foreign), (m, foreign, d)
                assert rcal.pit_annual(d, fi) == rcal.latest_annual(d, fi) == _old_pit_annual(d, m, foreign), (m, foreign, d)
                if not foreign:
                    assert rcal.pit_cell(d, m) == _old_pit_cell(d, m, False)
    assert n == 12 * 2 * len(days)


# ═══ B. 결산월을 바꾼 네 회사의 달력 ═══════════════════════════════════════════════════════════════
def test_334970_reads_the_june_fiscal_month_after_the_change():
    f = F334
    assert [rcal.month_of(y, f) for y in (2021, 2022, 2023, 2024)] == [12, 12, 6, 6]
    assert rcal.cell_end(2022, 4, f) == date(2022, 12, 31) and rcal.cell_end(2023, 4, f) == date(2023, 6, 30)   # 2023 사업보고서 = 전환기 2023.06
    assert rcal.is_transition(2023, f) and not rcal.is_transition(2022, f) and not rcal.is_transition(2024, f)
    # spec 바뀌는 값: (2025,1) 칸 = 2025.09 분기보고서 — 공시 시점 2025-05-15(12월 결산 읽기) → 2025-11-14
    assert rcal.cell_end(2025, 1, f) == date(2025, 9, 30) and rcal.cell_available(2025, 1, f) == date(2025, 11, 14)
    assert rcal.cell_available(2025, 1, 12) == date(2025, 5, 15)
    # 2025-05-15 에 쓸 수 있는 가장 늦은 칸: 종전 (2025,1)(= 실은 6개월 뒤에야 나오는 2025.09) → 이제 (2025,3) = 2025.03 분기보고서(5/15 공시)
    assert rcal.pit_cell(date(2025, 5, 15), 12) == (2025, 1) and rcal.pit_cell(date(2025, 5, 15), f) == (2025, 3)
    assert rcal.pit_cell(date(2025, 11, 13), f) == (2025, 4) and rcal.pit_cell(date(2025, 11, 14), f) == (2025, 1)
    # firm FY0: 오늘(2026-10-21) 최신 사업보고서 — 종전 2025(2025.06) → 2026(2026.06)
    assert rcal.latest_annual(date(2026, 10, 21), 12) == 2025 and rcal.latest_annual(date(2026, 10, 21), f) == 2026
    assert rcal.latest_annual(date(2026, 9, 30), f) == 2025 and rcal.latest_annual(date(2026, 10, 1), f) == 2026
    # TTM 직전 연간: 2024 1분기 칸(2024.09) 앞 결산 = 2024.06 → 2024 · 전환기 안의 칸은 전환기(2023)를 가리킨다(순이익을 가려 TTM 이 비는 자리)
    assert rcal.prior_annual(2024, 1, f) == 2024 and rcal.prior_annual(2023, 2, f) == 2023 and rcal.prior_annual(2025, 3, f) == 2024
    assert rcal.quarter_labels_after(2024, f) == {1: 2024, 2: 2024, 3: 2025}
    assert rcal.quarter_labels_after(2022, f) == {1: 2023, 2: 2023, 3: 2023}            # 바꾸기 전 표기(2022 = 12월 결산) 다음 분기 말은 모두 다음 해


def test_032800_changed_one_year_earlier():
    assert rcal.month_of(2021, F032) == 12 and rcal.month_of(2022, F032) == 6 and rcal.is_transition(2022, F032)
    assert rcal.latest_annual(date(2026, 10, 21), F032) == 2026 and rcal.latest_annual(date(2022, 10, 1), F032) == 2022
    assert rcal.latest_annual(date(2022, 9, 30), F032) == 2021          # 2022.06 전환기 보고서는 10월 1일부터(결산월 다음 넷째 달)


def test_154030_nine_to_twelve_reads_old_cells_with_the_old_month():
    f = F154
    assert [rcal.month_of(y, f) for y in (2024, 2025, 2026)] == [9, 12, 12] and rcal.is_transition(2025, f)
    assert rcal.cell_end(2024, 4, f) == date(2024, 9, 30) and rcal.cell_available(2024, 4, f) == date(2025, 1, 1)
    assert rcal.cell_end(2025, 4, f) == date(2025, 12, 31) and rcal.cell_available(2025, 4, f) == date(2026, 4, 1)
    # 바꾸기 전 표기(2024)는 9월 결산으로 읽는다: 1분기 = 2024.12 말(FY2025.09 의 첫 분기) · 3분기 = 2024.06 말 — 바꾼 뒤 표기(2025)는 12월 결산: 1분기 = 2025.03 말
    assert rcal.cell_end(2024, 1, f) == date(2024, 12, 31) and rcal.cell_end(2024, 3, f) == date(2024, 6, 30)
    assert rcal.cell_end(2025, 1, f) == date(2025, 3, 31) and rcal.cell_end(2025, 3, f) == date(2025, 9, 30)
    assert rcal.pit_annual(date(2025, 2, 1), f) == 2024 and rcal.pit_annual(date(2025, 2, 1), 12) == 2023      # 2024.09 사업보고서는 2025-01-01 부터
    assert rcal.pit_annual(date(2026, 1, 15), f) == 2024 and rcal.pit_annual(date(2026, 4, 1), f) == 2025
    assert rcal.latest_annual(date(2026, 10, 21), f) == 2025
    assert rcal.prior_annual(2026, 1, f) == 2025 and rcal.prior_annual(2025, 3, f) == 2024       # 전환기 뒤 첫 TTM 은 전환기(순이익 없음)를 가리킨다


def test_222160_three_to_december():
    f = F222
    assert [rcal.month_of(y, f) for y in (2023, 2024, 2025)] == [3, 12, 12] and rcal.is_transition(2024, f)
    assert rcal.cell_end(2023, 4, f) == date(2023, 3, 31) and rcal.cell_available(2023, 4, f) == date(2023, 7, 1)
    assert rcal.cell_end(2024, 4, f) == date(2024, 12, 31)
    assert rcal.pit_annual(date(2023, 8, 1), f) == 2023 and rcal.pit_annual(date(2023, 8, 1), 12) == 2022         # 옛 읽기는 1년 늦다
    assert rcal.pit_annual(date(2024, 8, 1), f) == 2023 and rcal.pit_annual(date(2025, 4, 1), f) == 2024
    assert rcal.latest_annual(date(2026, 10, 21), f) == 2025
    # 바꾸기 전 표기(2023)는 3월 결산 — 지금 결산월(12)로 읽으면 한 해 어긋난다: 2023 1분기 칸(2023.06)의 직전 결산은 2023.03 → 2023
    assert rcal.prior_annual(2023, 1, f) == 2023 and rcal.prior_annual(2023, 3, f) == 2023 and rcal.prior_annual(2025, 1, f) == 2024
    assert rcal.quarter_labels_after(2023, f) == {1: 2023, 2: 2023, 3: 2023}            # 2023.03 결산 다음 분기 말 6·9·12월 — 모두 같은 해
    assert rcal.quarter_labels_after(2025, f) == {1: 2026, 2: 2026, 3: 2026}


def test_every_changed_company_pit_is_monotone_and_never_looks_ahead():
    """바꾼 회사의 모든 날: 고른 칸은 그 날 이미 공시된 칸이고(`cell_available <= 날`), 날짜가 지날수록 칸의 기간 말은 뒤로만 간다."""
    for f in (F334, F032, F154, F222, rcal.Fiscal(9, True, (), 12, 2020), rcal.Fiscal(2, False, (), 6, 2019)):
        last = None
        for d in _days(date(2016, 1, 1), date(2031, 12, 31)):
            cell = rcal.pit_cell(d, f)
            assert rcal.cell_available(cell[0], cell[1], f) <= d, (f, d, cell)
            end = rcal.cell_end(cell[0], cell[1], f)
            assert last is None or end >= last, (f, d, cell)
            last = end
            fy = rcal.pit_annual(d, f)
            assert rcal.cell_available(fy, 4, f) <= d and rcal.cell_available(fy + 1, 4, f) > d, (f, d, fy)


def test_invalid_change_columns_are_ignored():
    assert rcal.fiscal(6, "000001", 6, 2023) == rcal.Fiscal(6)                    # 옛 결산월이 같으면 바꾼 게 아니다
    assert rcal.fiscal(6, "000001", 13, 2023) == rcal.Fiscal(6)
    assert rcal.fiscal(6, "000001", 12, None) == rcal.Fiscal(6)                   # 둘 다 있어야 한다
    assert rcal.fiscal(6, "000001", None, 2023) == rcal.Fiscal(6)
    assert rcal.fiscal(6, "000001", "12", "2023") == F334                          # 문자열도 받는다
    assert rcal.fiscal(F334, "334970") == F334 and rcal.fiscal(F334, "334970", 3, 2020).prev == 3
    assert rcal.fiscal(12, "950170", 3, 2024) == rcal.Fiscal(12, False, (), 3, 2024)     # 외국 상장사 기한은 12월 결산이 아닐 때만
    assert rcal.fiscal(6, "950170", 12, 2024).foreign is True
    assert rcal.changes_from_rows([("334970", 6, 12, 2023), ("X", 6, 6, 2023), ("Y", None, 9, 2020), ("Z", 12, None, None), ("W", "bad", 1, 1)]) == \
        {"334970": (6, 12, 2023), "Y": (12, 9, 2020)}


# ═══ C. 사업보고서 목록 → 결산월 ═════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("t, want", [
    ("334970", Q.AccMt(6, 12, 2023)),
    ("032800", Q.AccMt(6, 12, 2022)),
    ("154030", Q.AccMt(12, 9, 2025)),       # 같은 해에 사업보고서 둘(2025.09 + 2025.12 전환기)
    ("222160", Q.AccMt(12, 3, 2024)),       # 2024.03 + 2024.12 전환기
])
def test_listing_of_the_four_companies(t, want):
    assert Q.classify_reports(REPORTS[t]) == want
    assert rcal.fiscal(want.month, t, want.prev, want.since).since == want.since


def test_listing_edge_cases():
    assert Q.classify_reports([]) is None
    assert Q.classify_reports([(2026, 3), (2026, 3), (2025, 3)]) == Q.AccMt(3)                  # 정정 공시는 같은 달
    reit_semi = [(2023, 6), (2023, 12), (2024, 6), (2024, 12), (2025, 6)]
    assert Q.classify_reports(reit_semi) == Q.AccMt(12)                                         # 반기 결산 — 결산기 칸 몫
    reit_q = [(y, m) for y in (2024, 2025) for m in (3, 6, 9, 12)]
    assert Q.classify_reports(reit_q) == Q.AccMt(12)                                            # 분기 결산
    assert Q.classify_reports([(2023, 12), (2024, 12), (2025, 12)]) == Q.AccMt(12)
    # 두 번 바꿨으면 가장 최근 변경만 안다
    assert Q.classify_reports([(2019, 12), (2020, 3), (2021, 3), (2022, 9), (2023, 9)]) == Q.AccMt(9, 3, 2022)


# ═══ D. 재무 요약 파생(--derive) · Q4 seed ══════════════════════════════════════════════════════
psycopg = pytest.importorskip("psycopg")

FINSTAT_Q_DDL = """CREATE TABLE dart_finstat_q(
  ticker text, fy int, quarter int, reprt_code text, fs text,
  ni_cum double precision, eq double precision, fetched text, ni_case text, eq_case text,
  ni_cum_raw double precision, eq_raw double precision,
  ni_cum_restated double precision, eq_restated double precision, restate_why text,
  PRIMARY KEY(ticker, fy, quarter))"""
FINSTAT_Y_DDL = """CREATE TABLE dart_finstat_y(
  ticker text, fy int, fs text, ni double precision, eq double precision, fetched text,
  ni_restated double precision, eq_restated double precision, PRIMARY KEY(ticker, fy))"""


def _q(url, sql, params=()):
    with psycopg.connect(url) as c:
        return c.execute(sql, params).fetchall()


@pytest.fixture
def fdb(pg17, monkeypatch):
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(FINSTAT_Q_DDL)
        c.execute(FINSTAT_Y_DDL)
        c.execute(Q.DDL_FUNDAMENTALS)
    monkeypatch.setenv("DATABASE_URL", pg17)
    return pg17


def _seed_cells(c, t, cells):
    for (fy, q), (ni, eq) in cells.items():
        c.execute("INSERT INTO dart_finstat_q (ticker, fy, quarter, reprt_code, fs, ni_cum, eq, fetched) "
                  "VALUES (%s,%s,%s,%s,'CFS',%s,%s,'ok')", (t, fy, q, Q.Q_TO_RC[q], ni, eq))


# 334970 꼴(12 → 6, 2023): 2022 사업(12월) · 2023 전환기(6개월) · 2024 사업(2024.06) · 2023 1분기 칸 = 2023.09(DART 가 같은 (2023,11013)에 둘 중 늦은 것을 준다)
CELLS_334 = {(2022, 4): (-497.0, 1176.0), (2023, 4): (-180.0, 998.0), (2023, 1): (-60.0, 940.0), (2023, 2): (-86.0, 916.0),
             (2024, 4): (-294.0, 717.0), (2024, 1): (-65.0, 1551.0)}
# 154030 꼴(9 → 12, 2025): FY2024.09 · 2025 전환기(3개월) · 2025 반기 칸 = 옛 결산월의 2025.03 반기보고서 · 2026 1·2분기(2026.03·06)
CELLS_154 = {(2024, 4): (-6.3, 227.0), (2025, 4): (-13.7, 241.0), (2025, 2): (11.4, 248.1), (2026, 1): (15.0, 256.0), (2026, 2): (27.0, 253.0)}


def test_derive_follows_the_changed_fiscal_month_and_skips_the_transition_net_income(fdb, capsys):
    with psycopg.connect(fdb, autocommit=True) as c:
        for t, m, p, s in (("TA", 6, 12, 2023), ("TB", 12, None, None), ("TC", 12, 9, 2025), ("TD", 12, None, None)):
            c.execute("INSERT INTO dart_fundamentals (ticker, corp_code, fetched, acc_mt, acc_mt_prev, acc_mt_since) VALUES (%s,%s,'ok',%s,%s,%s)",
                      (t, "c" + t, m, p, s))
        for t, cells in (("TA", CELLS_334), ("TB", CELLS_334), ("TC", CELLS_154), ("TD", CELLS_154)):
            _seed_cells(c, t, cells)
    Q.derive_fundamentals()
    got = {r[0]: r[1:] for r in _q(fdb, "SELECT ticker, ni_fy, ni_ttm, eq_fy, eq_mrq FROM dart_fundamentals")}
    # TA — 최신 칸 (2024,1)=2024.09: TTM = FY2024(2024.06) + 누적 2024.09 − 누적 2023.09 = −294 + (−65) − (−60)
    assert got["TA"] == (-294.0, -299.0, 717.0, 1551.0)
    # TB — 같은 칸을 12월 결산으로 읽던 종전: 마지막 칸을 (2024,4) 로 보고 TTM 이 그 연간 값(−294)·MRQ 자본이 717
    assert got["TB"] == (-294.0, -294.0, 717.0, 717.0)
    # TC — 전환기 FY2025 의 순이익(−13.7)은 연간 값이 아니다: ni_fy 없음(자본은 그 날 잔액 241) · TTM 의 직전 연간이 전환기라 비어 있다
    assert got["TC"] == (None, None, 241.0, 253.0)
    # TD — 종전: 전환기 석 달 순이익이 ni_fy 로, TTM 은 −13.7 + 27.0 − 11.4(밀린 반기 칸) = 1.9 로 섞여 나갔다
    assert got["TD"] == (-13.7, pytest.approx(1.9), 241.0, 253.0)


def test_seed_empties_the_transition_net_income_once_and_leaves_the_rest(fdb):
    with psycopg.connect(fdb, autocommit=True) as c:
        c.execute("INSERT INTO dart_fundamentals (ticker, corp_code, fetched, acc_mt, acc_mt_prev, acc_mt_since) VALUES "
                  "('TC','cTC','ok',12,9,2025), ('TD','cTD','ok',12,NULL,NULL)")
        for t in ("TC", "TD"):
            c.execute("INSERT INTO dart_finstat_y (ticker, fy, fs, ni, eq, fetched) VALUES (%s,2024,'CFS',-6.3,227,'ok'), (%s,2025,'CFS',-13.7,241,'ok')", (t, t))
        # 종전 seed 가 전환기 칸의 순이익을 이미 옮겨 놓았다
        c.execute("INSERT INTO dart_finstat_q (ticker, fy, quarter, reprt_code, fs, ni_cum, eq, fetched) VALUES "
                  "('TC',2025,4,'11011','CFS',-13.7,241,'ok'), ('TD',2025,4,'11011','CFS',-13.7,241,'ok')")
    Q.seed_q4()
    rows = {(t, fy): r for t, fy, *r in _q(fdb, "SELECT ticker, fy, ni_cum, eq, ni_case, fetched FROM dart_finstat_q WHERE quarter=4")}
    assert rows[("TC", 2025)] == [None, 241.0, "TRANSITION", "ok"]                    # 비우고 표시(자본 유지)
    assert rows[("TC", 2024)] == [-6.3, 227.0, None, "ok"]                             # 다른 해는 그대로
    assert rows[("TD", 2025)] == [-13.7, 241.0, None, "ok"]                            # 결산월을 안 바꾼 회사는 건드리지 않는다
    x1 = _q(fdb, "SELECT xmin::text FROM dart_finstat_q")
    Q.seed_q4()
    assert _q(fdb, "SELECT xmin::text FROM dart_finstat_q") == x1                      # 두 번째 실행은 아무것도 다시 쓰지 않는다
    # 연간 표는 그대로(순이익은 수집기가 다시 받을 때 비운다) — seed 는 연간 표를 바꾸지 않는다
    assert _q(fdb, "SELECT ni FROM dart_finstat_y WHERE ticker='TC' AND fy=2025") == [(-13.7,)]


def test_seed_without_the_change_columns_is_the_old_seed(pg17, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", pg17)
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(FINSTAT_Q_DDL)
        c.execute(FINSTAT_Y_DDL)
        c.execute("CREATE TABLE dart_fundamentals(ticker text PRIMARY KEY, corp_code text, fetched text, acc_mt smallint)")
        c.execute("INSERT INTO dart_fundamentals VALUES ('TC','cTC','ok',12)")
        c.execute("INSERT INTO dart_finstat_y (ticker, fy, fs, ni, eq, fetched) VALUES ('TC',2025,'CFS',-13.7,241,'ok')")
    Q.seed_q4()
    assert _q(pg17, "SELECT ni_cum, eq FROM dart_finstat_q WHERE quarter=4") == [(-13.7, 241.0)]


# ═══ E. 읽는 쪽 목록(load_fye_map) · DDL ═════════════════════════════════════════════════════════
def test_load_fye_map_returns_fiscal_for_changed_companies(fdb):
    with psycopg.connect(fdb, autocommit=True) as c:
        for t, m, p, s in (("D", 12, None, None), ("M", 3, None, None), ("TA", 6, 12, 2023), ("TC", 12, 9, 2025), ("N", None, None, None)):
            c.execute("INSERT INTO dart_fundamentals (ticker, corp_code, fetched, acc_mt, acc_mt_prev, acc_mt_since) VALUES (%s,%s,'ok',%s,%s,%s)", (t, "c" + t, m, p, s))
        with c.cursor() as cur:
            got = rcal.load_fye_map(c)
    assert got == {"M": 3, "TA": F334, "TC": F154} and got["M"] == 3 and isinstance(got["TA"], rcal.Fiscal)


def test_load_fye_map_without_the_change_columns_is_the_old_map(pg17):
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute("CREATE TABLE dart_fundamentals(ticker text PRIMARY KEY, fetched text, acc_mt smallint)")
        c.execute("INSERT INTO dart_fundamentals VALUES ('D','ok',12), ('M','ok',3), ('N','ok',NULL)")
        assert rcal.load_fye_map(c) == {"M": 3}
        c.execute("DROP TABLE dart_fundamentals")
        c.execute("CREATE TABLE dart_fundamentals(ticker text PRIMARY KEY, fetched text)")
        assert rcal.load_fye_map(c) == {}


def test_change_columns_ddl_is_idempotent_and_keeps_the_pair_together(pg17):
    ddl = _load("fundamentals_ddl")
    assert "acc_mt_prev" in ddl.render() and "acc_mt_since" in ddl.render() and "acc_mt_change_check" in ddl.render()
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute("CREATE TABLE dart_fundamentals(ticker text PRIMARY KEY, fetched text, acc_mt smallint)")   # 결산월 칸만 있는 운영 표 꼴
        c.execute("ALTER TABLE dart_fundamentals ADD CONSTRAINT dart_fundamentals_acc_mt_check CHECK (acc_mt BETWEEN 1 AND 12)")
        c.execute("INSERT INTO dart_fundamentals VALUES ('A', 'ok', 12)")
        st = ddl.check(c, out=lambda *_: None)
        assert st[0] == "todo" and "acc_mt_prev" in st[1] and "acc_mt_since" in st[1]
    assert ddl.apply(pg17, out=lambda *_: None) == (True, "칸·확인 제약 있음")
    assert ddl.apply(pg17, out=lambda *_: None) == (True, "칸·확인 제약 있음")                           # 멱등
    with psycopg.connect(pg17, autocommit=True) as c:
        assert ddl.check(c, out=lambda *_: None)[0] == "done"
        c.execute("UPDATE dart_fundamentals SET acc_mt_prev = 9, acc_mt_since = 2025")
        for bad in ("acc_mt_prev = 13, acc_mt_since = 2025", "acc_mt_prev = 9, acc_mt_since = 1800", "acc_mt_prev = 9, acc_mt_since = NULL"):
            with pytest.raises(psycopg.errors.CheckViolation):
                c.execute(f"UPDATE dart_fundamentals SET {bad}")
        c.execute("DROP TABLE dart_fundamentals")
    # 새 DB: `market_fund_quarterly` 의 CREATE TABLE 이 같은 칸·제약을 만들고, DDL 은 아무것도 더하지 않는다
    with psycopg.connect(pg17, autocommit=True) as c:
        c.execute(Q.DDL_FUNDAMENTALS)
        assert ddl.check(c, out=lambda *_: None)[0] == "done"
    assert ddl.apply(pg17, out=lambda *_: None)[0] is True


# ═══ F. 서버 ═══════════════════════════════════════════════════════════════════════════════════
@pytest.fixture(autouse=True)
def _fresh_changes_cache():
    S._fye_changes_cache = None
    yield
    S._fye_changes_cache = None


def test_server_reads_the_changes_once_and_tolerates_a_table_without_the_columns(monkeypatch):
    calls = []

    def rows(sql, params=()):
        calls.append(" ".join(sql.split()))
        return [("334970", 6, 12, 2023), ("154030", 12, 9, 2025)]
    monkeypatch.setattr(S, "_pg_rows", rows)
    ch = asyncio.run(S._fye_changes())
    assert ch == {"334970": (6, 12, 2023), "154030": (12, 9, 2025)}
    assert "to_jsonb(d)->>'acc_mt_prev'" in calls[0] and calls[0].startswith("SELECT ticker, acc_mt")
    assert asyncio.run(S._fye_changes()) is ch and len(calls) == 1                             # 프로세스에 잠깐 둔다
    f = asyncio.run(S._fiscal_for("334970", 6))
    assert f == F334 and asyncio.run(S._fiscal_for("154030", 12)) == F154
    assert asyncio.run(S._fiscal_for("005930", 12)) == rcal.Fiscal()                          # 안 바꾼 회사는 종전 그대로
    assert asyncio.run(S._fiscal_for("018500", 3)) == rcal.Fiscal(3)
    # 칸이 없는 표(DDL 전): `to_jsonb` 질의는 빈 목록 — 종전 읽기
    S._fye_changes_cache = None
    monkeypatch.setattr(S, "_pg_rows", lambda sql, params=(): [])
    assert asyncio.run(S._fye_changes()) == {}
    # DB 장애(None)·예외도 종전 읽기 — 짧게만 두고 다시 읽는다
    S._fye_changes_cache = None
    monkeypatch.setattr(S, "_pg_rows", lambda sql, params=(): None)
    assert asyncio.run(S._fye_changes()) == {}
    S._fye_changes_cache = None

    def boom(sql, params=()):
        raise RuntimeError("db")
    monkeypatch.setattr(S, "_pg_rows", boom)
    assert asyncio.run(S._fye_changes()) == {} and S._fye_changes_cache[0] - __import__("time").monotonic() <= S._FYE_CHANGES_FAIL_TTL


FIN_334 = {2022: (100.0e8, 1000.0e8), 2023: (50.0e8, 1050.0e8), 2024: (210.0e8, 1300.0e8), 2025: (260.0e8, 1900.0e8)}
FINQ_334 = {(2023, 1): (15.0e8, 1040.0e8), (2023, 2): (40.0e8, 1100.0e8), (2024, 1): (55.0e8, 1290.0e8), (2024, 2): (110.0e8, 1450.0e8),
            (2025, 1): (70.0e8, 1700.0e8), (2025, 3): (190.0e8, 1850.0e8)}


def test_curve_point_uses_the_cells_of_the_new_fiscal_month_and_never_the_transition_net_income():
    fin = S._rpit.PitFin(FIN_334)
    fin = _mask(fin, F334)
    assert fin[2023] == (None, 1050.0e8)                          # 전환기 6개월 순이익 50억은 연간이 아니다 · 자본 잔액은 그대로
    # 2025-11-13: 2025.09 분기보고서(11/14 공시) 전 — 가장 늦은 공시 칸은 2025.06 사업보고서 (2025,4)
    p = S._pit_raw("20251113", CAP, fin, FINQ_334, F334)
    assert (p["pit_fy"], p["pit_q"]) == (2025, "2025Q4") and p["per_ttm"] == CAP / 260.0e8 and p["pbr_mrq"] == CAP / 1900.0e8
    # 2025-11-14: (2025,1) = 2025.09 — TTM = FY2025 + 누적(2025.09) − 누적(2024.09)
    q = S._pit_raw("20251114", CAP, fin, FINQ_334, F334)
    assert q["pit_q"] == "2025Q1" and q["per_ttm"] == CAP / (260.0e8 + 70.0e8 - 55.0e8) and q["pbr_mrq"] == CAP / 1700.0e8
    # 종전 읽기(12월 결산): 같은 2025-11-13 에 (2025,2)(= 실은 2025.12 말, 아직 나올 수 없는 보고서)를 쓰려다 칸이 비어 TTM 이 비었다
    old = S._pit_raw("20251113", CAP, FIN_334, FINQ_334, 12)
    assert old["pit_q"] == "2025Q2" and old["per_ttm"] is None
    # 2023 전환기 안(2023-12-01): 직전 연간이 전환기라 TTM 이 비고(N/M) 자본(MRQ)은 낸다
    r = S._pit_raw("20231201", CAP, fin, FINQ_334, F334)
    assert r["pit_q"] == "2023Q1" and r["per_ttm"] is None and r["pbr_mrq"] == CAP / 1040.0e8


def _mask(fin, f):
    return rcal.mask_transition(fin, f)


def test_mask_transition_hides_only_the_net_income_of_the_transition_year():
    fin = S._rpit.PitFin(FIN_334)
    fin.gate(2023, ("20240101", (49.0e8, 1000.0e8)))
    out = rcal.mask_transition(fin, F334)
    assert out is fin and fin[2023] == (None, 1050.0e8) and fin.gates[2023] == ("20240101", (None, 1000.0e8))
    assert fin[2022] == (100.0e8, 1000.0e8) and fin[2024] == (210.0e8, 1300.0e8)
    plain = dict(FIN_334)
    assert rcal.mask_transition(plain, 12) == FIN_334 and rcal.mask_transition(plain, rcal.Fiscal(6)) == FIN_334
    assert rcal.mask_transition({2022: (1.0, 2.0)}, F334) == {2022: (1.0, 2.0)}               # 전환기 해가 없으면 그대로


class _ChRows:
    """`_pg_rows` 대역 — 결산월을 바꾼 회사(334970 꼴: 12 → 6, 2023) 한 종목."""

    def __init__(self, acc_mt=6, changes=((("334970", 6, 12, 2023)),)):
        self.acc_mt, self.changes, self.log = acc_mt, list(changes), []

    def __call__(self, sql, params=()):
        s = " ".join(sql.split())
        self.log.append(s)
        if "to_jsonb(d)->>'acc_mt_prev'" in s:
            return self.changes
        if s.startswith("SELECT market, induty, currency, fetched, acc_mt FROM dart_fundamentals"):
            return [("KQ", "212", "KRW", "ok", self.acc_mt)]
        if s == "SELECT ni_fy, eq_fy FROM dart_fundamentals WHERE ticker=%s":
            return [(260.0e8, 1900.0e8)]
        if "FROM dart_finstat_y" in s:
            return pad_fin_rows(sql, [(fy, ni, eq, None, None) for fy, (ni, eq) in FIN_334.items()])
        if "FROM dart_finstat_q" in s:
            return pad_fin_rows(sql, [(fy, q, ni, eq) for (fy, q), (ni, eq) in FINQ_334.items()])
        if "UNION ALL" in s and "FROM krx_weekly" in s and "AS src" in s:
            return [(CAP, "KQ", 1)]
        if s.startswith("SELECT MAX(price_dd) FROM krx_weekly"):
            return [("20251114",)]
        raise AssertionError("모르는 SQL: " + s)


def test_sector_compare_firm_row_uses_the_stored_change(monkeypatch):
    """업종 비교 기업 행(`_firm_val_at`) — 저장된 결산월 칸(6)만으로는 바꾸기 전 칸이 틀리게 읽힌다. 변경 목록을 함께 읽어 전환기 순이익을 가린다."""
    monkeypatch.setattr(S, "_pg_rows", _ChRows())
    monkeypatch.setattr(S, "today_kst", lambda: date(2025, 11, 20))
    sec, mkt, per_ttm, pbr_mrq, note = asyncio.run(S._firm_val_at("334970", "20251114"))
    assert per_ttm == CAP / (260.0e8 + 70.0e8 - 55.0e8) and pbr_mrq == CAP / 1700.0e8 and note == {}
    # 전환기 안(2023-10-20, 전환기 사업보고서 2023.06 이 공시된 뒤): 6개월짜리 순이익 50억을 12개월 TTM 으로 쓰지 않는다 — 비어서 앞선 칸(2022 사업보고서)으로 채우고 표시한다
    sec, mkt, per, pbr, note = asyncio.run(S._firm_val_at("334970", "20231020"))
    assert per == CAP / 100.0e8 and pbr == CAP / 1050.0e8 and note == {"basis": {"pit_q": "2023Q4", "per_ttm": "2022Q4"}}
    # 변경 목록을 못 읽던 종전 읽기(결산월 6 하나): 전환기 6개월 순이익이 그대로 TTM 이 되어 PER 이 두 배(200) 로 나갔다
    S._fye_changes_cache = None
    monkeypatch.setattr(S, "_pg_rows", _ChRows(changes=()))
    *_, per_old, pbr_old, note_old = asyncio.run(S._firm_val_at("334970", "20231020"))
    assert per_old == CAP / 50.0e8 and pbr_old == pbr and note_old == {}


def test_note_tells_how_to_read_the_cells_of_a_changed_december_company():
    assert S._fye_note(rcal.Fiscal()) is None and S._fye_note(12) is None                   # 안 바꾼 12월 결산은 종전 응답 그대로
    note = S._fye_note(F154)
    assert note.startswith(S._FYE_NOTE_HEAD + "12월") and "결산월을 9월에서 12월로 바꿨다" in note and "2025 사업보고서는 전환기" in note
    assert "결산월을 " not in (S._fye_note(rcal.Fiscal(3)) or "")                           # 3월 결산(안 바꿈) 문구는 그대로


def test_firm_does_not_present_the_transition_report_as_a_full_year(monkeypatch):
    """실시간 firm — 154030(9 → 12) 의 최신 사업보고서는 2025.10~12 석 달짜리 전환기다. 종전엔 그 석 달을 FY0(순이익 −13.66억·EPS −5원·ROE 1.7%·매출 708.8억)로 냈다.
    이제 연간 흐름 값을 비우고 까닭을 적는다. 자본 잔액(PBR)은 그 날 값이라 그대로 낸다."""
    from tests.test_pmd_reit_fy0_year import _annual, _interim, _run
    reports = {(2025, "11011"): _annual("-1,366,478,768", "24,100,295,993", "1,905,122,329"),
               (2026, "11012"): _interim("1,203,428,623", "25,279,446,526"), (2025, "11012"): _interim("1,143,094,692", "24,814,861,748")}

    async def changes():
        return {"154030": (12, 9, 2025)}
    monkeypatch.setattr(S, "_fye_changes", changes)
    p, seen = _run(monkeypatch, overview_acc_mt="12", db_acc_mt=12, reports=reports, alot={}, ticker="154030", cap=80_000_000_000, price=2500)
    d, i, m = p["data"], p["data"]["inputs"], p["data"]["multiples"]
    assert d["fiscal_year"] == 2025 and seen["fm"] == [2025]
    assert (i["net_income_fy0_krw"], i["eps_fy0_krw"], i["roe_pct"], i["revenue_fy0_krw"]) == (None, None, None, None)
    assert (m["per_fy0"], m["per_ttm"]) == (None, None) and m["pbr_mrq"] == 3.16
    assert any("전환기 — 12개월이 아님" in w and "9월에서 12월로" in w for w in d["warnings"])
    # 결산월 변경 목록을 못 읽은 종전 읽기는 석 달 순이익을 FY0 로 냈다(같은 응답)
    async def none():
        return {}
    monkeypatch.setattr(S, "_fye_changes", none)
    p0, _ = _run(monkeypatch, overview_acc_mt="12", db_acc_mt=12, reports=reports, alot={}, ticker="154030", cap=80_000_000_000, price=2500)
    assert p0["data"]["inputs"]["net_income_fy0_krw"] == -1_366_478_768 and p0["data"]["inputs"]["roe_pct"] == 1.7
    assert not any("전환기" in w for w in p0["data"]["warnings"])


def test_firm_of_a_december_company_is_unchanged_by_the_change_lookup(monkeypatch):
    from tests.test_pmd_reit_fy0_year import _alot, _annual, _interim, _run
    reports = {(2025, "11011"): _annual("100,000,000,000", "1,000,000,000,000"),
               (2026, "11012"): _interim("60,000,000,000", "1,050,000,000,000"), (2025, "11012"): _interim("40,000,000,000", "950,000,000,000")}

    async def changes():
        return {"154030": (12, 9, 2025)}
    monkeypatch.setattr(S, "_fye_changes", changes)
    p, seen = _run(monkeypatch, overview_acc_mt="12", db_acc_mt=None, reports=reports, alot={2025: _alot(("2025-12-31", "1,668"))}, ticker="005930")
    assert seen["fm"] == [2025] and not any("acc_mt" in s for s in seen["sql"])
    assert p["data"]["inputs"]["net_income_ttm_krw"] == 120_000_000_000 and not any("전환기" in w for w in p["data"]["warnings"])
