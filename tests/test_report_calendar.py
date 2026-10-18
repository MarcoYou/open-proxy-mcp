"""DART 정기보고서 칸 ↔ 실제 기간 말·공시 시점 — 결산월을 아는 매핑(`open_proxy_mcp/services/report_calendar.py`, 261018 재설계 6단계).

고정하는 것
  ① 칸의 기간 말 = DART 표기 그대로의 해 · 결산월 + 3 × 보고서 순번 달. 2026-10-18 DART 직접 대조(칸마다 접수번호 → 보고서 이름) 6개사 표를 그대로 옮겼다.
  ② 12월 결산은 종전 함수와 **모든 날** 같다(2016~2031) — `pit_cell`·`pit_annual` 의 12월 지름길도, 일반 식도.
  ③ 공시 시점: 분기·반기는 기간 말 + 45일, 사업보고서는 결산월 다음 넷째 달 1일. 그 전날에는 그 칸을 고르지 않는다(look-ahead 방지).
  ④ TTM 의 직전 연간 표기(`prior_annual`)와 1년 전 같은 칸.
network 0 · DB 0.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from open_proxy_mcp.services import price_multiple_data as S
from open_proxy_mcp.services import report_calendar as R


# ── 종전 식(12월 결산 전용) — 바꾸기 전 `price_multiple_data` 의 두 함수를 글자 그대로 옮겼다 ──────────────────────
def _old_pit_fy(price_dd: str) -> int:
    y, m = int(price_dd[:4]), int(price_dd[4:6])
    return y - 1 if m >= 4 else y - 2


def _old_pit_quarter(price_dd: str) -> tuple[int, int]:
    y, m, d = int(price_dd[:4]), int(price_dd[4:6]), int(price_dd[6:8])
    md = m * 100 + d
    if md >= 1114:
        return (y, 3)
    if md >= 814:
        return (y, 2)
    if md >= 515:
        return (y, 1)
    if m >= 4:
        return (y - 1, 4)
    return (y - 1, 3)


def _old_ttm_ni(fin, finq, fy, q):
    if q == 4:
        return fin.get(fy, (None, None))[0]
    cum_now = finq.get((fy, q), (None, None))[0]
    cum_prev = finq.get((fy - 1, q), (None, None))[0]
    fy_prev = fin.get(fy - 1, (None, None))[0]
    if cum_now is None or cum_prev is None or fy_prev is None:
        return None
    return fy_prev + cum_now - cum_prev


def _days(a=date(2016, 1, 1), b=date(2031, 12, 31)):
    d = a
    while d <= b:
        yield d
        d += timedelta(days=1)


# ── ① DART 표기 → 기간 말 (10-18 DART 직접 대조) ───────────────────────────────────────────────────────
# (결산월, 사업연도 표기, quarter) → 보고서 이름의 기준월 「(YYYY.MM)」
DART_FACTS = {
    12: {(2026, 1): "2026.03", (2026, 2): "2026.06", (2025, 3): "2025.09", (2025, 4): "2025.12"},   # 삼성전자
    3:  {(2026, 1): "2026.06", (2025, 2): "2025.09", (2025, 3): "2025.12", (2026, 4): "2026.03"},   # 동원모빌리티
    6:  {(2025, 1): "2025.09", (2025, 2): "2025.12", (2026, 3): "2026.03", (2026, 4): "2026.06"},   # MH머트리얼즈
    9:  {(2025, 1): "2025.12", (2026, 2): "2026.03", (2026, 3): "2026.06", (2025, 4): "2025.09"},   # 방림
    8:  {(2025, 1): "2025.11", (2026, 2): "2026.02", (2026, 3): "2026.05", (2025, 4): "2025.08"},   # 풍강
    2:  {(2026, 1): "2026.05", (2025, 2): "2025.08", (2025, 3): "2025.11", (2026, 4): "2026.02"},   # 제이티씨
}


@pytest.mark.parametrize("fye", sorted(DART_FACTS))
def test_cell_end_matches_the_report_names_dart_returns(fye):
    for (fy, q), ym in DART_FACTS[fye].items():
        e = R.cell_end(fy, q, fye)
        assert e.strftime("%Y.%m") == ym, (fye, fy, q)
        assert (e + timedelta(days=1)).day == 1, "기간 말은 그 달 말일"


def test_unknown_or_bad_month_reads_as_december():
    for v in (None, "", "13", 0, "x", "12", 12):
        assert R.fye_month(v) == 12
    assert R.fye_month("03") == 3 and R.fye_month(6) == 6


# ── ② 12월 결산 — 종전과 모든 날 같다 ───────────────────────────────────────────────────────────────
def test_december_is_identical_to_the_old_rule_on_every_day():
    bad = []
    for d in _days():
        s = d.strftime("%Y%m%d")
        old_q, old_fy = _old_pit_quarter(s), _old_pit_fy(s)
        if (R.pit_cell(s) != old_q or R.pit_cell(s, 12) != old_q or R.pit_annual(s, 12) != old_fy
                or S._pit_quarter(s) != old_q or S._pit_fy(s) != old_fy):
            bad.append(s)
    assert bad == []


def test_the_general_rule_reduces_to_the_old_rule_for_december():
    """12월은 지름길을 탄다 — 일반 식이 12월에서도 같은 답을 내는지 따로 본다(모형이 한 규칙이라는 증거)."""
    assert all(R._pit_cell_any(d, 12) == _old_pit_quarter(d.strftime("%Y%m%d")) for d in _days())
    assert all(R._pit_annual_any(d, 12) == _old_pit_fy(d.strftime("%Y%m%d")) for d in _days())


def test_december_ttm_is_the_old_formula():
    fin = {2024: (100.0, 1000.0), 2025: (120.0, 1100.0)}
    finq = {(2025, q): (10.0 * q, 1000.0 + q) for q in (1, 2, 3)} | {(2026, q): (15.0 * q, 1100.0 + q) for q in (1, 2, 3)}
    for fy, q in [(2025, 1), (2025, 2), (2025, 3), (2026, 1), (2026, 2), (2026, 3), (2025, 4), (2026, 4)]:
        assert S._ttm_ni(fin, finq, fy, q) == _old_ttm_ni(fin, finq, fy, q) == S._ttm_ni(fin, finq, fy, q, 12)


# ── ③ 공시 시점(마감일 PIT) ──────────────────────────────────────────────────────────────────────────
def test_december_availability_is_the_statutory_calendar():
    assert [R.cell_available(2026, q).isoformat() for q in (1, 2, 3)] == ["2026-05-15", "2026-08-14", "2026-11-14"]
    assert R.cell_available(2025, 4) == date(2026, 4, 1)


@pytest.mark.parametrize("fye, cases", [
    # 3월 결산: 사업보고서(3월 말) 7/1 · 1분기(6월 말) 8/14 · 반기(9월 말) 11/14 · 3분기(12월 말) 이듬해 2/14
    (3, [("20260630", (2025, 3)), ("20260701", (2026, 4)), ("20260813", (2026, 4)), ("20260814", (2026, 1)),
         ("20261016", (2026, 1)), ("20261113", (2026, 1)), ("20261114", (2026, 2)), ("20270213", (2026, 2)),
         ("20270214", (2026, 3)), ("20270701", (2027, 4)), ("20260515", (2025, 3))]),
    # 6월 결산: 3분기(3월 말) 5/15 · 사업보고서(6월 말) 10/1 · 1분기(9월 말) 11/14 · 반기(12월 말) 이듬해 2/14
    (6, [("20260514", (2025, 2)), ("20260515", (2026, 3)), ("20260930", (2026, 3)), ("20261001", (2026, 4)),
         ("20261016", (2026, 4)), ("20261114", (2026, 1)), ("20270214", (2026, 2))]),
    # 9월 결산: 반기(3월 말) 5/15 · 3분기(6월 말) 8/14 · 사업보고서(9월 말) 이듬해 1/1 · 1분기(12월 말) 이듬해 2/14
    (9, [("20260515", (2026, 2)), ("20260814", (2026, 3)), ("20261016", (2026, 3)), ("20270101", (2026, 4)),
         ("20270214", (2026, 1))]),
    # 2월 결산: 사업보고서(2월 말) 6/1 · 1분기(5월 말) 7/15 · 반기(8월 말) 10/15 · 3분기(11월 말) 이듬해 1/14
    (2, [("20260531", (2025, 3)), ("20260601", (2026, 4)), ("20260715", (2026, 1)), ("20261015", (2026, 2)),
         ("20270114", (2026, 3))]),
])
def test_non_december_picks_the_cell_by_its_real_period_end_and_deadline(fye, cases):
    for dd, want in cases:
        assert R.pit_cell(dd, fye) == want, (fye, dd)
        assert S._pit_quarter(dd, fye) == want


def test_no_cell_is_picked_before_its_deadline():
    """모든 결산월·모든 날 — 고른 칸의 공시 시점은 그 날 이하이고, 그보다 기간 말이 늦은 칸은 아직 공시 전이다."""
    for fye in range(1, 13):
        for d in _days(date(2024, 1, 1), date(2027, 12, 31)):
            fy, q = R.pit_cell(d, fye)
            assert R.cell_available(fy, q, fye) <= d
            nxt = [(y, k) for y in (d.year - 1, d.year, d.year + 1) for k in (1, 2, 3, 4)
                   if R.cell_end(y, k, fye) > R.cell_end(fy, q, fye)]
            assert all(R.cell_available(y, k, fye) > d for y, k in nxt), (fye, d)
            a = R.pit_annual(d, fye)
            assert R.cell_available(a, 4, fye) <= d < R.cell_available(a + 1, 4, fye)


def test_march_year_end_annual_and_the_old_calendar_look_ahead():
    """종전 달력 규칙은 3월 결산의 2026-05-15 에 「2026 1분기」 칸을 골랐다 — 그 칸은 2026.06 말 보고서(8/14 마감)다."""
    assert _old_pit_quarter("20260515") == (2026, 1)
    assert R.cell_available(2026, 1, 3) == date(2026, 8, 14)
    assert R.pit_cell("20260515", 3) == (2025, 3)          # 2025.12 말 3분기보고서(2/14 마감)
    assert R.pit_annual("20261231", 3) == 2026 and _old_pit_fy("20261231") == 2025   # 연말 밴드: 2026.03 사업보고서(6월 제출)


# ── ④ TTM 의 직전 연간 ───────────────────────────────────────────────────────────────────────────────
def test_prior_annual_label_by_fiscal_month():
    for q in (1, 2, 3):
        assert R.prior_annual(2026, q, 12) == 2025               # 종전 그대로
        assert R.prior_annual(2026, q, 3) == 2026                # 6·9·12월 말 → 그해 3월 말 사업보고서
    assert [R.prior_annual(2026, q, 6) for q in (1, 2, 3)] == [2026, 2026, 2025]   # 9·12월 말 → 2026.06, 3월 말 → 2025.06
    assert [R.prior_annual(2026, q, 9) for q in (1, 2, 3)] == [2026, 2025, 2025]   # 12월 말 → 2026.09, 3·6월 말 → 2025.09
    assert R.prior_annual(2026, 4, 3) == 2025


def test_march_ttm_uses_the_annual_that_just_closed():
    """3월 결산 2026 1분기(2026.06 말) TTM = 2026.03 사업보고서 + 1분기 누적(2026.06) − 1분기 누적(2025.06).
    종전 식은 2025.03 사업보고서를 썼다(한 해 어긋남)."""
    fin = {2025: (180.0, 1300.0), 2026: (260.0, 1900.0)}
    finq = {(2025, 1): (50.0, 1290.0), (2026, 1): (140.0, 1950.0)}
    assert S._ttm_ni(fin, finq, 2026, 1, 3) == 260.0 + 140.0 - 50.0
    assert _old_ttm_ni(fin, finq, 2026, 1) == 180.0 + 140.0 - 50.0


def test_quarter_labels_after_an_annual():
    assert R.quarter_labels_after(2025, 12) == {1: 2026, 2: 2026, 3: 2026}
    assert R.quarter_labels_after(2026, 3) == {1: 2026, 2: 2026, 3: 2026}
    assert R.quarter_labels_after(2026, 6) == {1: 2026, 2: 2026, 3: 2027}
    assert R.quarter_labels_after(2025, 9) == {1: 2025, 2: 2026, 3: 2026}


def test_latest_annual_for_collection():
    t = date(2026, 10, 18)
    assert R.latest_annual(t, 12) == 2025
    assert {m: R.latest_annual(t, m) for m in (1, 2, 3, 4, 5, 6)} == {m: 2026 for m in (1, 2, 3, 4, 5, 6)}
    assert {m: R.latest_annual(t, m) for m in (7, 8, 9, 10, 11)} == {m: 2025 for m in (7, 8, 9, 10, 11)}


# ── ⑤ 외국 상장사(12월 결산이 아닐 때만) — 분기 60일 · 사업보고서 120일 ─────────────────────────────────
def test_foreign_issuer_deadlines_apply_only_off_december():
    assert R.fiscal("06", "900120") == R.Fiscal(6, True)
    assert R.fiscal("12", "900070") == R.Fiscal(12, False)          # 12월 결산 외국 상장사는 종전 그대로
    assert R.fiscal("03", "018500") == R.Fiscal(3, False)
    f = R.fiscal(6, "900120")
    # 씨엑스아이(6월 결산·중국): 2025.06 사업보고서 2025-10-28 접수 · 2026.03 3분기 2026-06-01 접수 (DART 실측)
    assert R.cell_available(2025, 4, f) == date(2025, 11, 1) and R.cell_available(2026, 3, f) == date(2026, 5, 30)
    assert R.cell_available(2025, 4, 6) == date(2025, 10, 1)        # 국내 6월 결산
    assert R.pit_annual("20261016", f) == 2025 and R.pit_cell("20261016", f) == (2026, 3)
    assert R.pit_annual("20261016", 6) == 2026 and R.pit_cell("20261016", 6) == (2026, 4)
    # 제이티씨(2월 결산·일본): 2026.08 반기 기한 10/29(10-30 접수 관행) → 10/16 에는 2026.05 1분기
    assert R.pit_cell("20261016", R.fiscal(2, "950170")) == (2026, 1)
    # 12월 결산 외국 상장사는 모든 날 종전과 같다
    assert all(R.pit_cell(d, R.fiscal(12, "900070")) == _old_pit_quarter(d.strftime("%Y%m%d")) for d in _days(date(2024, 1, 1)))


def test_foreign_cells_are_never_picked_before_the_longer_deadline():
    for m in range(1, 12):
        f = R.Fiscal(m, True)
        for d in _days(date(2025, 1, 1), date(2026, 12, 31)):
            fy, q = R.pit_cell(d, f)
            assert R.cell_available(fy, q, f) <= d
            lag = (R.cell_available(fy, q, f) - R.cell_end(fy, q, f)).days
            assert lag >= (60 if q != 4 else 120)
