"""성과 분석 순수 계산(`services/perf_math.py`) — 손계산 고정값 시험 (DB·네트워크 없음).

기대값은 구현과 따로 정수 분수·단순 식으로 계산한 것이다(주석에 풀이). 이 시험은 모듈이 없던 main(39e6a672)에서 ImportError 로 실패한다.
"""
from __future__ import annotations

import math
from datetime import timedelta

import pytest

from open_proxy_mcp.services import perf_math as M


# ── ① 세 종목·세 기간: 누적·연율·표본 변동성·MDD ─────────────────────────────────
#   A: +10%, 0, −10% / B: +20%, −5%, +5% / C: 0, +10%, +10%, 동일 비중 표류(사고 팔지 않음).
#   보유 가치 = 종목별 부(wealth)의 평균 → P0=1, P1=1.1, P2=1.1133.., P3=1.13233..
#   구간 수익률 = 1/10, 2/165, 57/3340 · 누적 = 397/3000 · 표본 표준편차 0.04937138373305904 (분수 계산)
RET3 = [[0.10, 0.0, -0.10], [0.20, -0.05, 0.05], [0.0, 0.10, 0.10]]


def test_simulate_equal_weight_drift_matches_hand_values():
    sim = M.simulate(RET3, {0: [1 / 3] * 3})
    assert sim.rp == pytest.approx([1 / 10, 2 / 165, 57 / 3340], abs=1e-12)
    assert M.cumulative(sim.rp) == pytest.approx(397 / 3000, abs=1e-12)
    assert M.stdev(sim.rp) == pytest.approx(0.04937138373305904, abs=1e-12)
    # 구간 시작 비중은 합이 1 이고 첫 구간은 시작 비중 그대로 — 둘째 구간부터 표류한다
    assert sim.weights_start[0] == pytest.approx([1 / 3] * 3)
    assert all(sum(w) == pytest.approx(1.0, abs=1e-12) for w in sim.weights_start)
    assert sim.weights_start[1][1] > sim.weights_start[1][2]       # +20% 한 B 가 0% 한 C 보다 커졌다


def test_annualize_needs_one_year_and_uses_365_25():
    assert M.annualize(0.2, 730) == pytest.approx(1.2 ** (365.25 / 730) - 1, abs=1e-12)
    assert M.annualize(0.2, 364) is None                            # 365일 미만은 연율을 내지 않는다
    assert M.obs_per_year(245, 365) == pytest.approx(245 * 365.25 / 365)


def test_max_drawdown_dates_from_wealth_curve():
    # 부: 1 → 1.1 → 0.88 → 0.924 → 0.8316 → 1.08108 · 고점 1.1(관측 1), 저점 0.8316(관측 4) → 0.8316/1.1 − 1 = −0.244
    dates = ["20260102", "20260105", "20260106", "20260107", "20260108", "20260109"]
    mdd, frm, to = M.max_drawdown([0.10, -0.20, 0.05, -0.10, 0.30], dates)
    assert mdd == pytest.approx(-0.244, abs=1e-12)
    assert (frm, to) == ("20260105", "20260108")
    assert M.max_drawdown([0.01, 0.02], ["20260102", "20260105", "20260106"]) == (0.0, "20260102", "20260102")


def test_downside_vol_and_sortino_use_rf_as_threshold():
    ex = [0.0119, -0.0301, 0.0399, 0.0009]                          # r − rf (RP − 0.0001), 아래 상대 지표 시험과 같은 열
    dd = M.downside_vol_ann(ex, 52)
    assert dd == pytest.approx(0.10852709339146607, abs=1e-12)      # sqrt(mean(min(x,0)²)) × √52
    assert M.sortino(ex, 52) == pytest.approx(2.7071581005144907, abs=1e-9)
    assert M.sharpe(ex, 52) == pytest.approx(1.4077842631910578, abs=1e-9)
    assert M.sharpe([0.01, 0.01, 0.01], 52) is None                 # 분산 0


# ── 상대 지표 ─────────────────────────────────────────────────────────────────────
RP = [0.012, -0.03, 0.04, 0.001]
RB = [0.010, -0.02, 0.03, 0.0]


def test_relative_metrics_beta_alpha_te_ir_corr():
    rf = [0.0001] * 4
    ex_p = [x - y for x, y in zip(RP, rf)]
    ex_b = [x - y for x, y in zip(RB, rf)]
    beta, alpha = M.beta_alpha(ex_p, ex_b, 52)
    assert beta == pytest.approx(1.3884615384615384, abs=1e-12)
    assert alpha == pytest.approx(-0.05997999999999994, abs=1e-12)  # (mean ex_p − β·mean ex_b) × 52
    assert M.correlation(RP, RB) == pytest.approx(0.9986904323228513, abs=1e-12)
    assert M.tracking_error(RP, RB, 52) == pytest.approx(0.05928181283777794, abs=1e-12)
    assert M.info_ratio(RP, RB, 52) == pytest.approx(0.6578746184217042, abs=1e-12)
    assert M.beta_alpha([0.1, 0.2], [0.0, 0.0], 52) == (None, None)  # 벤치마크 분산 0


# ── ⑥ 무위험 금리 ACT/365 단리 ───────────────────────────────────────────────────
def test_rf_accrual_act365_simple_and_no_lookahead():
    dates = ["20260105", "20260112", "20260119"]                    # 7일씩
    obs = [("20260102", 3.65), ("20260113", 7.30)]                  # 두 번째 관측은 둘째 구간 시작(01-12) 뒤 → 쓰지 않는다
    rf = M.rf_accrual(obs, dates)
    assert rf.per_interval[0] == pytest.approx(0.0007, abs=1e-12)    # 연 3.65% × 7/365
    assert rf.per_interval[1] == pytest.approx(0.0007, abs=1e-12)    # 아직 모르던 7.30 을 쓰지 않았다
    assert rf.asof == "20260102" and rf.missing == 0
    rf2 = M.rf_accrual([("20260110", 3.65)], dates)                  # 첫 구간 시작(01-05) 이전 관측이 없다
    assert rf2.per_interval[0] is None and rf2.missing == 1 and rf2.per_interval[1] == pytest.approx(0.0007, abs=1e-12)
    assert M.fixed_rf_accrual(3.65, dates).per_interval == pytest.approx([0.0007, 0.0007], abs=1e-12)


# ── ② Brinson-Fachler: 업종 둘 + 미분류 ──────────────────────────────────────────
#   P 비중 .5/.3/.2, 수익률 .10/.02/.05 · B 비중 .4/.4/.2, 수익률 .06/.03/.04 → RP=.066, RB=.044, active=.022
#   배분 .0016+.0014+0=.003 · 선택 .016−.004+.002=.014 · 상호작용 .004+.001+0=.005 → 합 .022
def test_brinson_fachler_hand_values_sum_to_active():
    g = {"S1": M.GroupPeriod(.5, .10, .4, .06), "S2": M.GroupPeriod(.3, .02, .4, .03), "_UNCLASSIFIED": M.GroupPeriod(.2, .05, .2, .04)}
    p = M.brinson_fachler(g)
    assert p.r_p == pytest.approx(0.066, abs=1e-12) and p.r_b == pytest.approx(0.044, abs=1e-12)
    assert p.effects["S1"] == pytest.approx((0.0016, 0.016, 0.004), abs=1e-12)
    assert p.effects["S2"] == pytest.approx((0.0014, -0.004, 0.001), abs=1e-12)
    assert p.effects["_UNCLASSIFIED"] == pytest.approx((0.0, 0.002, 0.0), abs=1e-12)
    alloc = sum(e[0] for e in p.effects.values())
    sel = sum(e[1] for e in p.effects.values())
    inter = sum(e[2] for e in p.effects.values())
    assert (alloc, sel, inter) == pytest.approx((0.003, 0.014, 0.005), abs=1e-12)
    assert alloc + sel + inter == pytest.approx(0.022, abs=1e-12)


def test_brinson_neutral_fill_when_a_side_holds_nothing():
    # 대상이 S3 를 안 갖고(wP=0) 벤치마크가 S4 를 안 가져도(wB=0) 합 = rP − rB 가 유지된다
    g = {"S1": M.GroupPeriod(.6, .08, .5, .05), "S3": M.GroupPeriod(0.0, None, .5, .02), "S4": M.GroupPeriod(.4, .03, 0.0, None)}
    p = M.brinson_fachler(g)
    total = sum(sum(e) for e in p.effects.values())
    assert total == pytest.approx(p.r_p - p.r_b, abs=1e-12)
    assert p.effects["S3"][1] == 0.0 and p.effects["S3"][2] == 0.0       # 못 가진 묶음: 선택·상호작용 0
    assert p.effects["S4"][0] == 0.0 and p.effects["S4"][1] == 0.0       # 벤치마크가 안 가진 묶음: 배분·선택 0


def test_brinson_multi_period_carino_residual_is_zero():
    periods = []
    rp_all, rb_all = [], []
    data = [
        ({"S1": (.5, .04, .4, .03), "S2": (.5, -.02, .6, -.01)}),
        ({"S1": (.45, .01, .4, .02), "S2": (.55, .03, .6, .00)}),
        ({"S1": (.6, -.03, .4, -.02), "S2": (.4, .05, .6, .04)}),
    ]
    for d in data:
        p = M.brinson_fachler({k: M.GroupPeriod(*v) for k, v in d.items()})
        periods.append(p)
        rp_all.append(p.r_p)
        rb_all.append(p.r_b)
    link = M.link_attribution(periods)
    assert link.active == pytest.approx(M.cumulative(rp_all) - M.cumulative(rb_all), abs=1e-15)
    assert abs(link.residual) < 1e-9
    assert link.allocation + link.selection + link.interaction == pytest.approx(link.active, abs=1e-9)
    assert set(link.by_group) == {"S1", "S2"}


# ── ③ Carino: 종목 기여 합 = 포트폴리오 누적 ─────────────────────────────────────
def test_carino_contributions_sum_to_cumulative_and_k_is_one_at_zero():
    sim = M.simulate(RET3, {0: [1 / 3] * 3})
    contribs = [[sim.weights_start[j][p] * RET3[p][j] for p in range(3)] for j in range(3)]
    for j in range(3):
        assert sum(contribs[j]) == pytest.approx(sim.rp[j], abs=1e-12)   # 기간별 기여 합 = 기간 수익률
    linked = M.link_contributions(contribs, sim.rp)
    assert sum(linked) == pytest.approx(397 / 3000, abs=1e-12)
    assert linked[0] < 0 < min(linked[1], linked[2])                    # A 는 기간 누적 −1%(1.1×1.0×0.9), B·C 는 플러스
    assert M.carino_k(0.0) == 1.0 and M.carino_k(1e-15) == 1.0
    assert M.carino_k(0.05) == pytest.approx(math.log(1.05) / 0.05)
    # 기간 수익률이 0 인 구간이 끼어도 합은 누적과 같다
    c0 = [[0.01, 0.01], [0.0, 0.0], [-0.005, 0.015]]
    rp0 = [0.02, 0.0, 0.01]
    assert sum(M.link_contributions(c0, rp0)) == pytest.approx(M.cumulative(rp0), abs=1e-12)


# ── ④ 블렌드 표류 대 월간 리밸런스 ───────────────────────────────────────────────
#   A 는 매 구간 +10%, B 는 −10%, 50:50. 표류: (1.1⁴ + 0.9⁴)/2 − 1 = 0.0601 · 4번째 구간 앞(3월 첫 관측)에서 리밸런스하면
#   3구간 뒤 P=(1.331+0.729)/2=1.03, 되돌린 50:50 의 넷째 구간 수익률 0 → 0.03
DATES_M = ["20260130", "20260202", "20260203", "20260227", "20260302"]


def test_blend_drift_vs_monthly_rebalance():
    ret = [[0.10] * 4, [-0.10] * 4]
    drift = M.simulate(ret, {0: [.5, .5]})
    assert M.cumulative(drift.rp) == pytest.approx(0.0601, abs=1e-12)
    reb = M.rebalance_intervals(DATES_M, "monthly")
    assert reb == frozenset({3})                                        # 2월→3월을 넘는 구간 하나 (첫 구간은 시작 비중)
    monthly = M.simulate(ret, {0: [.5, .5]}, reb)
    assert M.cumulative(monthly.rp) == pytest.approx(0.03, abs=1e-12)
    assert monthly.weights_start[3] == pytest.approx([.5, .5])          # 리밸런스 구간 앞에서 되돌렸다
    assert M.rebalance_intervals(DATES_M, "none") == frozenset()
    assert M.rebalance_intervals(["20260227", "20260302", "20260401", "20260701"], "quarterly") == frozenset({1, 2})
    with pytest.raises(ValueError):
        M.rebalance_intervals(DATES_M, "weekly")


def test_rebalance_leaves_delisted_holding_as_cash():
    # B 는 둘째 관측에서 상장폐지(마지막 가격 번호 2) → 이후 수익률 0, 리밸런스에서도 사고팔지 않는다
    ret = [[0.10, 0.10, 0.10, 0.10], [-0.50, 0.0, 0.0, 0.0]]
    sim = M.simulate(ret, {0: [.5, .5]}, {3}, last_idx=[4, 1])
    # B 의 가치: 0.5 × 0.5 = 0.25 가 3구간 동안 그대로, A: 0.5 × 1.1³ = 0.6655 → 셋째 구간 뒤 합 0.9155
    assert sim.weights_start[3][1] == pytest.approx(0.25 / 0.9155, abs=1e-12)    # 리밸런스해도 B(현금) 몫은 표류한 그대로
    assert sim.weights_start[3][0] == pytest.approx(1 - 0.25 / 0.9155, abs=1e-12)


def test_rebalance_with_cap_targets_uses_that_days_target():
    ret = [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]]
    sim = M.simulate(ret, {0: [.5, .5], 2: [.8, .2]}, {2})
    assert sim.weights_start[2] == pytest.approx([.8, .2])


# ── ⑤ 배당락일 다섯 ──────────────────────────────────────────────────────────────
def _cal(*holidays: str) -> list[str]:
    """2023-09-01 ~ 2026-12-31 평일에서 12-31·주어진 휴일을 뺀 거래일 달력(YYYYMMDD)."""
    d, out = M.parse_dd("20230901"), []
    end = M.parse_dd("20261231")
    while d <= end:
        dd = M.fmt_dd(d)
        if d.isoweekday() <= 5 and not (d.month == 12 and d.day == 31) and dd not in holidays:
            out.append(dd)
        d += timedelta(days=1)
    return out


@pytest.mark.parametrize("record,holidays,want", [
    ("20251231", (), "20251229"),                       # 기준일 12-31 은 휴장 → 마지막 거래일 12-30 의 전 거래일 12-29
    ("20260331", (), "20260330"),                       # 기준일이 거래일(화) → 바로 앞 거래일
    ("20260228", (), "20260226"),                       # 토요일 → 마지막 거래일 금(27) → 목(26)
    ("20230930", ("20230928", "20230929"), "20230926"),  # 추석 연휴 토요일 → 마지막 거래일 9/27(수) → 9/26(화)
    ("20240630", (), "20240627"),                       # 일요일 → 금(28) → 목(27)
])
def test_ex_dividend_date_rule(record, holidays, want):
    assert M.ex_dividend_date(record, _cal(*holidays)) == (want, "daily_calendar")


def test_ex_dividend_date_weekday_approximation_outside_calendar():
    # 달력 밖(일별 창 밖)은 평일 달력 + 12-31 휴장으로 근사하고 그렇게 밝힌다 — 평일 휴장(추석)은 못 본다
    assert M.ex_dividend_date("20191231", []) == ("20191227", "weekday_approx")     # 12-31(화) 휴장 → 12-30(월) → 전 거래일 12-27(금)
    assert M.ex_dividend_date("20230930", []) == ("20230928", "weekday_approx")
    # 달력의 끝 뒤 기준일도 근사 — 달력이 12-29 까지인데 기준일이 12-31 이면
    assert M.ex_dividend_date("20251231", _cal()[:_cal().index("20251230")]) == ("20251229", "weekday_approx")


def test_locate_interval():
    d = ["20260105", "20260112", "20260119"]
    assert M.locate_interval(d, "20260112") == 0 and M.locate_interval(d, "20260113") == 1
    assert M.locate_interval(d, "20260105") is None and M.locate_interval(d, "20260120") is None


# ── ⑦ 주간 수익률: 사건 원값 곱, 호가단위 맞춤 제외 ──────────────────────────────
def test_weekly_leg_multiplies_raw_event_values_not_rounded_factor():
    # 5분할(prev_close 33333 → 기준가 6667): 원값 계수 6667/33333 = 0.2000060..., 반올림 0.2 를 쓰면 기준가 6666.6
    ev = M.AdjEvent("20260115", 33333, 6667, "KS")
    adj = M.adjustment_factor([ev], "20260109", "20260116")
    assert adj.applied == 1 and adj.tick_skipped == 0
    leg = M.weekly_leg(7000, 33333, adj.factor)
    assert leg.ret == pytest.approx(7000 / 6667 - 1, abs=1e-12)            # 원값 기준가 6667 로 나눈다
    assert abs(leg.ret - (7000 / (33333 * 0.2) - 1)) > 4e-5               # 반올림 계수와는 다르다
    assert M.weekly_leg(7000, 33333, 1.0).ret == pytest.approx(7000 / 33333 - 1)   # 보정이 없으면 −79% 로 보인다
    # 구간 밖 사건은 곱하지 않는다 — (lo, hi] 반열린
    assert M.adjustment_factor([ev], "20260115", "20260116").applied == 0
    assert M.adjustment_factor([ev], "20260109", "20260114").applied == 0


def test_tick_rounding_event_is_skipped_and_counted():
    tick = M.AdjEvent("20260115", 2082, 2085, "KQ")                         # 하나31호스팩: 호가단위 5원 안쪽
    real = M.AdjEvent("20260116", 11820, 11800, "KS")                       # 맥쿼리인프라: 호가단위 한 칸 이상 → 진짜 조정
    labelled = M.AdjEvent("20260117", 1000, 1002, "KS", "excluded_tick_rounding")
    r = M.adjustment_factor([tick, real, labelled], "20260109", "20260123")
    assert (r.applied, r.tick_skipped) == (1, 2)
    assert r.factor == pytest.approx(11800 / 11820)
    bad = M.adjustment_factor([M.AdjEvent("20260115", None, 2085)], "20260109", "20260123")
    assert bad.invalid == 1 and bad.factor == 1.0


# ── ⑧ 일별: cmp 기준, NULL 이면 보충, 호가단위 맞춤 날, 총수익 ──────────────────
def test_daily_leg_uses_published_cmp_and_fallback_when_null():
    leg = M.daily_leg(11000, 1000, 10000)
    assert leg.ret == pytest.approx(0.10) and leg.base == 10000 and not leg.cmp_missing
    # cmp 가 공표한 기준가는 분할을 이미 반영 — 종가끼리 나누면 −78%
    split = M.daily_leg(11000, 1000, 50000)
    assert split.ret == pytest.approx(0.10)
    # cmp NULL(옛 행) 보충: 전일 종가 × 그날 사건 계수
    fb = M.daily_leg(11000, None, 50000, factor=0.2)
    assert fb.ret == pytest.approx(0.10) and fb.cmp_missing and fb.base == pytest.approx(10000)
    assert M.daily_leg(11000, None, None).ret is None and M.daily_leg(11000, None, None).cmp_missing
    # 호가단위 맞춤 날: 공표 기준가(2085)가 아니라 전일 종가(2082)를 기준으로
    tick = M.daily_leg(2100, 15, 2082, tick_day=True)
    assert tick.ret == pytest.approx(2100 / 2082 - 1) and tick.base == 2082 and not tick.cmp_missing


def test_total_return_adds_dps_over_adjusted_base():
    leg = M.daily_leg(9900, -100, 10000)                                   # 가격 −1%
    assert M.total_return(leg, 300) == pytest.approx((9900 + 300) / 10000 - 1)
    assert M.total_return(leg, None) == pytest.approx(leg.ret)
    wk = M.weekly_leg(10500, 10000)                                        # 주간: DPS / (close_{t−1} × 계수)
    assert M.total_return(wk, 250) == pytest.approx(0.05 + 250 / 10000)
    assert M.total_return(M.Leg(None, None), 100) is None


def test_group_returns_and_series_stats():
    out = M.group_returns([.2, .3, .5], [.10, .00, .02], ["S1", "S1", "S2"])
    assert out["S1"] == (pytest.approx(.5), pytest.approx((.2 * .10) / .5)) and out["S2"] == (pytest.approx(.5), pytest.approx(.02))
    assert M.group_returns([0.0], [0.05], ["S9"])["S9"][1] is None
    st = M.series_stats([0.1, -0.05], ["20260102", "20260105", "20260112"])
    assert st.cum == pytest.approx(0.045) and st.ann is None and st.days == 10
