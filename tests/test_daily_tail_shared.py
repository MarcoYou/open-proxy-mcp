"""주간 곡선 끝에 일별 한 줄 잇기 — 한 곳(`krx_retention.with_daily_tail`) (261021, 감사 B1 · DATA-PX6 의 남은 한계).

종전에는 같은 규칙이 두 벌이었다 — 시세 도구 종목 시계열 `trading._with_daily_tail`(줄 전체를 잇는다)과 배수 도구 곡선
`price_multiple_data._join_daily_tail`((날짜, 시총) 쌍만). 한쪽만 고치면 같은 종목의 시세 시계열과 배수 곡선의 마지막 점이 갈라진다.
이제 두 이름은 얇은 껍데기(이름 유지)이고 규칙은 한 함수다. 지키는 것:
  ① 계약: 같은 날·더 이른 날 → 받은 객체 그대로 / 같은 주(달력 연도 안) → 마지막 점 교체 / 더 늦은 주 → 뒤에 붙임 / 주간 행이 없으면 그 한 점 /
     하한 아래 꼬리는 버림 / 주간 읽기 실패(None)는 None / 꼬리 없음·실패는 객체 그대로.
  ② 종전 두 구현(아래 `_old_trading`·`_old_pmd`, 종전 코드 그대로)과 **무작위 입력 전부에서 같은 답** — 응답 값 변화 0. 연도 경계·ISO 주 경계 포함.
  ③ 두 껍데기가 모두 그 한 함수를 거친다(껍데기에서 규칙을 다시 쓰지 못하게).
network 0 · DB 0.
"""
from __future__ import annotations

import random
from datetime import date, timedelta

import pytest

from open_proxy_mcp import krx_retention as R
from open_proxy_mcp.services import price_multiple_data as S
from open_proxy_mcp.services import trading as T


# ── 종전 구현(바꾸기 전 코드 그대로 — 무작위 대조의 기준) ───────────────────────────────────────
def _old_trading(rows, tail, lo):
    if rows is None or not tail:
        return rows
    t = tuple(tail[0])
    dd = t[0]
    if not dd or dd < lo or (rows and dd <= rows[-1][0]):
        return rows
    if rows and R.week_unit(dd)[0] == R.week_unit(rows[-1][0])[0]:
        return [*rows[:-1], t]
    return [*rows, t]


def _old_pmd(px, tail, lo):
    if not tail or not tail[0] or not tail[0][0]:
        return px
    dd, cap = tail[0][0], tail[0][1]
    if dd < lo or (px and dd <= px[-1][0]):
        return px
    if px and R.week_unit(dd)[0] == R.week_unit(px[-1][0])[0]:
        return [*px[:-1], (dd, cap)]
    return [*px, (dd, cap)]


def _w(dd, close=1, shrs=10):
    """시세 도구의 한 줄 — (price_dd, market, close, mktcap, list_shrs)."""
    return (dd, "KS", close, close * shrs, shrs)


WEEKLY = [_w("20260925", 70), _w("20261002", 71), _w("20261009", 72)]   # 닫힌 주 셋 · 10-12~16 이 이번 주
LO = "20231009"


# ── ① 계약 ─────────────────────────────────────────────────────────────────────────────────────
def test_same_or_earlier_day_returns_the_same_object():
    assert R.with_daily_tail(WEEKLY, [_w("20261009", 9)], LO) is WEEKLY       # 금요일이 닫힌 직후 — 주간 표에도 그날 행이 있다
    assert R.with_daily_tail(WEEKLY, [_w("20261002", 9)], LO) is WEEKLY       # 더 이른 날(거래정지) — 그대로


def test_same_week_replaces_the_last_point_and_a_later_week_appends():
    assert R.with_daily_tail([_w("20261006", 1)], [_w("20261008", 2)], LO) == [_w("20261008", 2)]
    assert R.with_daily_tail(WEEKLY, [_w("20261013", 3)], LO) == [*WEEKLY, _w("20261013", 3)]
    assert R.with_daily_tail(WEEKLY, [_w("20261027", 3)], LO) == [*WEEKLY, _w("20261027", 3)], "몇 주 뒤여도 붙인다"


def test_the_calendar_year_cuts_the_week():
    """한 주 = ISO 주 ∩ 달력 연도 — 2025-12-30(화)과 2026-01-02(금)는 같은 ISO 주(2026-W01)지만 다른 주다(교체하면 연말 행이 사라진다)."""
    assert R.with_daily_tail([_w("20251230", 1)], [_w("20260102", 2)], LO) == [_w("20251230", 1), _w("20260102", 2)]
    assert R.with_daily_tail([_w("20251229", 1)], [_w("20251230", 2)], LO) == [_w("20251230", 2)], "같은 해 같은 ISO 주는 교체"
    assert R.with_daily_tail([_w("20261231", 1)], [_w("20270104", 2)], LO) == [_w("20261231", 1), _w("20270104", 2)]


def test_empty_weekly_rows_take_the_tail_as_the_only_point_and_the_floor_drops_it():
    assert R.with_daily_tail([], [_w("20261013", 1)], LO) == [_w("20261013", 1)]          # 이번 주 신규상장
    assert R.with_daily_tail([], [_w("20231006", 1)], LO) == []                          # 하한보다 이른 꼬리(오래 거래정지)는 버린다
    assert R.with_daily_tail([], [_w("20231009", 1)], LO) == [_w("20231009", 1)], "하한 당일은 쓴다"


def test_failed_reads_and_missing_tails_pass_through():
    assert R.with_daily_tail(None, [_w("20261013", 1)], LO) is None                      # 주간 읽기 실패 — 종전 장애 경로
    assert R.with_daily_tail([], None, LO) == []
    assert R.with_daily_tail(WEEKLY, None, LO) is WEEKLY and R.with_daily_tail(WEEKLY, [], LO) is WEEKLY
    assert R.with_daily_tail(WEEKLY, [None], LO) is WEEKLY and R.with_daily_tail(WEEKLY, [()], LO) is WEEKLY
    assert R.with_daily_tail(WEEKLY, [(None, 1)], LO) is WEEKLY and R.with_daily_tail(WEEKLY, [("", 1)], LO) is WEEKLY


def test_the_tail_row_keeps_the_callers_shape():
    """잇는 줄은 꼬리의 첫 줄 그대로다 — 시세 도구는 5칸 줄, 배수 곡선은 (날짜, 시총) 2칸. 줄이 리스트여도 튜플로 만든다."""
    assert R.with_daily_tail(WEEKLY, [["20261013", "KS", 3, 30, 10]], LO)[-1] == ("20261013", "KS", 3, 30, 10)
    assert R.with_daily_tail([("20261002", 1.0)], [("20261013", 3.0)], LO) == [("20261002", 1.0), ("20261013", 3.0)]
    assert R.with_daily_tail([("20261002", 1.0)], [("20261013", 3.0, "d")], LO)[-1] == ("20261013", 3.0, "d"), "읽은 줄 그대로"


# ── ② 종전 두 구현과 같은 답 ───────────────────────────────────────────────────────────────────
def _random_case(rng: random.Random):
    """연도 경계 둘레(2019·2024·2025 연말 — 12/31 이 화·수·목인 해)와 한가운데에서 무작위 주간 행 + 꼬리."""
    anchor = rng.choice([date(2019, 12, 20), date(2024, 12, 20), date(2025, 12, 22), date(2026, 3, 2), date(2026, 10, 1), date(2027, 1, 1)])
    days = [anchor + timedelta(days=i) for i in range(0, 40) if (anchor + timedelta(days=i)).isoweekday() < 6]
    n = rng.choice([0, 1, 2, 3, 6])
    weekly = sorted(rng.sample(days, min(n, len(days))))
    rows = [_w(d.strftime("%Y%m%d"), rng.randint(1, 99)) for d in weekly]
    pick = rng.choice(days)
    if weekly and rng.random() < 0.5:                 # 마지막 주간 점과 같은 주(또는 이웃)의 더 늦은 날 — 교체 갈래
        near = [d for d in days if weekly[-1] < d <= weekly[-1] + timedelta(days=6)]
        pick = rng.choice(near) if near else pick
    tail = None if rng.random() < 0.12 else ([] if rng.random() < 0.1 else [_w(pick.strftime("%Y%m%d"), rng.randint(1, 99))])
    lo = rng.choice(["20150101", (anchor + timedelta(days=rng.randint(0, 30))).strftime("%Y%m%d"), "20991231"])
    if rng.random() < 0.05:
        rows = None
    return rows, tail, lo


def test_matches_both_old_implementations_on_random_inputs():
    rng = random.Random(261021)
    n_replace = n_append = n_same = 0
    for _ in range(4000):
        rows, tail, lo = _random_case(rng)
        got = R.with_daily_tail(rows, tail, lo)
        want = _old_trading(rows, tail, lo)
        assert got == want, (rows, tail, lo)
        assert (got is rows) == (want is rows), "객체 그대로 돌려주는 경우도 같다 — 호출자가 `is` 로 꼬리를 이었는지 가린다"
        if rows is not None:                        # 배수 곡선은 (날짜, 시총) 쌍으로만 쓴다 — 같은 규칙, 쌍으로 줄여도 같은 답
            px = [(r[0], r[3]) for r in rows]
            tl = None if tail is None else [(r[0], r[3]) for r in tail]
            assert R.with_daily_tail(px, tl, lo) == _old_pmd(px, tl, lo), (px, tl, lo)
            assert S._join_daily_tail(px, tl, lo) == _old_pmd(px, tl, lo)
        assert T._with_daily_tail(rows, tail, lo) == want
        if got is not rows and rows:
            n_replace += len(got) == len(rows)
            n_append += len(got) == len(rows) + 1
        else:
            n_same += 1
    assert n_replace > 100 and n_append > 100 and n_same > 100, (n_replace, n_append, n_same)   # 세 갈래가 모두 실제로 나왔다


# ── ③ 껍데기는 한 함수를 거친다 ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("mod,name", [(T, "_with_daily_tail"), (S, "_join_daily_tail")])
def test_both_names_are_thin_wrappers_around_the_one_function(monkeypatch, mod, name):
    seen = []
    sentinel = object()

    def spy(rows, tail, lo):
        seen.append((rows, tail, lo))
        return sentinel
    monkeypatch.setattr(mod, "with_daily_tail", spy)
    assert getattr(mod, name)(WEEKLY, [_w("20261013", 3)], LO) is sentinel
    assert seen == [(WEEKLY, [_w("20261013", 3)], LO)]


def test_the_rule_lives_in_one_place():
    """규칙 본문(주 비교)을 두 서비스 파일이 다시 갖지 않는다 — 한쪽만 고쳐 갈라지는 것이 이 PR 이 없애려는 것이다."""
    import inspect
    for fn in (T._with_daily_tail, S._join_daily_tail):
        src = inspect.getsource(fn)
        assert "week_unit" not in src and "_iso_wk_range" not in src and "rows[-1]" not in src and "px[-1]" not in src
    assert "week_unit" in inspect.getsource(R.with_daily_tail)
