"""성과 분석 순수 계산 — 수익률·블렌딩·위험·상대 지표·기여·Brinson 분해·배당락일·무위험 금리 적립 (261022).

`performance_analysis`(블렌딩·초과수익·기여 분해·위험 지표를 부를 때 계산하는 도구)의 계산부다. **DB·네트워크·시계를 부르지 않는다** —
값을 받아 값을 돌려주는 함수만 둔다(그래서 손계산 고정값으로 시험한다). 표준 라이브러리(`math`·`statistics`·`bisect`)만 쓴다 —
numpy 는 서버 실행 의존성에 없다.

■ 날짜 격자 — `dates` = [기준일, 관측일 1, …, 관측일 n] (YYYYMMDD, 오름차순). 구간 j(0부터)는 `dates[j]` 종가 → `dates[j+1]` 종가이고
  수익률 `r[j]` 는 그 구간의 값이다. 연율화 계수 A = n × 365.25 / (끝 − 기준일 달력 일수) — 일별이면 약 245, 주간이면 약 52(`obs_per_year`).

■ 종목 구간 수익률 — 종가를 그대로 나누지 않는다(DATA-PX4·PX5·PX2).
  · 일별 `daily_leg`: r = cmp / (close − cmp). `cmp` 는 KRX 공표 전일대비라 기준가(= close − cmp)가 분할·권리락을 이미 반영한다.
    cmp 가 NULL 인 옛 행은 close / (전일 종가 × 그날 사건 계수) − 1 로 보충한다(`cmp_missing`). 호가단위 맞춤으로 기준가가 올라간 날은 공표 기준가를
    쓰지 않고 전일 종가를 기준으로 한다(기업 행위가 아니다).
  · 주간 `weekly_leg`: r = close_t / (close_{t−1} × Π base_price/prev_close) − 1. 곱하는 것은 구간 (t−1, t] 안의 수정계수 원장(`krx_adj_events`)
    **원값**이다 — 반올림해 저장한 `adj_factor` 로 곱하면 8주가 0.1~0.2%p 어긋나고, 보정이 없으면 760주가 어긋난다(운영 읽기 대조).
    호가단위 맞춤 사건은 곱하지 않고 센다(`adjustment_factor` → tick_skipped).
  · 총수익(배당 재투자, 세전) `total_return`: 배당락일 구간의 r 에 DPS / 조정 기준가를 더한다 — 현금배당락은 기준가를 조정하지 않는다.

■ 블렌딩 `simulate` — 보유 비중이 구간마다 표류한다: W_{t} = W_{t−1}(1+r)/(1+r_p). 리밸런스 구간 앞에서만 목표 비중으로 되돌린다.
  상장폐지로 가격이 끊긴 종목은 마지막 값에 현금(수익률 0)으로 묶여 있고, 리밸런스에서도 다시 사거나 팔지 않는다.

■ 기여 — 종목 기간별 c = W_{t−1} × r, Carino 연결(`link_contributions`)로 다기간 합이 누적 수익률과 정확히 같다.
■ Brinson-Fachler — 배분 (wP−wB)(rB_j−RB) · 선택 wB(rP_j−rB_j) · 상호작용 (wP−wB)(rP_j−rB_j). 합 = rP − rB. 다기간은 **초과 Carino**
  (`carino_active_coeffs`)로 이어 합이 누적 초과수익률(Rp−Rb)과 같고, 남는 차이(residual)는 부동소수점 오차뿐이다.
"""
from __future__ import annotations

import bisect
import math
import statistics
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Iterable, Mapping, Sequence

from open_proxy_mcp.krx_ticks import TICK_ROUNDING_LABEL, is_tick_rounding

_EPS = 1e-12

# ── 날짜 ──────────────────────────────────────────────────────────────────────


def parse_dd(dd: str) -> date:
    return date(int(dd[:4]), int(dd[4:6]), int(dd[6:8]))


def fmt_dd(d: date) -> str:
    return f"{d.year:04d}{d.month:02d}{d.day:02d}"


def calendar_days(a: str, b: str) -> int:
    """a → b 달력 일수(b − a)."""
    return (parse_dd(b) - parse_dd(a)).days


def obs_per_year(n_obs: int, days: int) -> float | None:
    """연간 관측 수 A = n × 365.25 / 일수. 일수가 0 이하면 None."""
    return n_obs * 365.25 / days if days > 0 and n_obs > 0 else None


# ── 종목 구간 수익률 ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AdjEvent:
    """수정계수 원장(`krx_adj_events`) 한 행 — 원값만. `adj_factor`(반올림 저장값)는 받지 않는다."""
    event_dd: str
    prev_close: float | None
    base_price: float | None
    market: str | None = None
    label: str | None = None          # label_confidence — 'excluded_tick_rounding' 이면 호가단위 맞춤으로 이미 판정된 사건


@dataclass(frozen=True)
class AdjResult:
    factor: float          # Π base_price / prev_close (적용한 사건만)
    applied: int           # 곱한 사건 수
    tick_skipped: int      # 호가단위 맞춤이라 곱하지 않은 사건 수
    invalid: int = 0       # 원값이 비어 있거나 0 이하라 쓰지 못한 사건 수


def is_tick_event(ev: AdjEvent) -> bool:
    """호가단위 맞춤 사건인가 — 라벨이 이미 그렇게 판정했거나 원값이 호가단위 안쪽이다(`krx_ticks`)."""
    if (ev.label or "") == TICK_ROUNDING_LABEL:
        return True
    return is_tick_rounding(ev.prev_close, ev.base_price, ev.market, ev.event_dd)


def adjustment_factor(events: Iterable[AdjEvent], lo_dd: str, hi_dd: str) -> AdjResult:
    """구간 (lo_dd, hi_dd] 안의 사건 계수 곱. 호가단위 맞춤은 곱하지 않고 센다."""
    f, applied, tick, bad = 1.0, 0, 0, 0
    for ev in events:
        if not (lo_dd < ev.event_dd <= hi_dd):
            continue
        if not ev.prev_close or not ev.base_price or ev.prev_close <= 0 or ev.base_price <= 0:
            bad += 1
            continue
        if is_tick_event(ev):
            tick += 1
            continue
        f *= ev.base_price / ev.prev_close
        applied += 1
    return AdjResult(f, applied, tick, bad)


@dataclass(frozen=True)
class Leg:
    """한 종목·한 구간의 가격 수익률 `ret` 과 조정 전일 기준가 `base`(배당 수익률의 분모). 계산할 수 없으면 둘 다 None."""
    ret: float | None
    base: float | None
    cmp_missing: bool = False


def daily_leg(close: float | None, cmp: float | None, prev_close: float | None,
              factor: float = 1.0, tick_day: bool = False) -> Leg:
    """일별 구간. 공표 대비(cmp)가 있으면 r = cmp/(close−cmp). 없거나(옛 행) 호가단위 맞춤 날이면 전일 종가 기준으로 보충한다."""
    if close is None or close <= 0:
        return Leg(None, None)
    if cmp is not None and not tick_day:
        base = close - cmp
        if base <= 0:
            return Leg(None, None)
        return Leg(cmp / base, base)
    if not prev_close or prev_close <= 0:
        return Leg(None, None, cmp_missing=cmp is None)
    base = prev_close * factor
    if base <= 0:
        return Leg(None, None, cmp_missing=cmp is None)
    return Leg(close / base - 1.0, base, cmp_missing=cmp is None)


def weekly_leg(close: float | None, prev_close: float | None, factor: float = 1.0) -> Leg:
    """주간 구간 r = close_t / (close_{t−1} × Π 계수) − 1."""
    if close is None or close <= 0 or not prev_close or prev_close <= 0:
        return Leg(None, None)
    base = prev_close * factor
    if base <= 0:
        return Leg(None, None)
    return Leg(close / base - 1.0, base)


def total_return(leg: Leg, dps: float | None) -> float | None:
    """배당락일을 품은 구간의 총수익률(세전) = 가격 수익률 + DPS / 조정 기준가. 일별이면 (close+DPS)/base − 1 과 같다."""
    if leg.ret is None or leg.base is None:
        return leg.ret
    return leg.ret + (dps or 0.0) / leg.base


# ── 배당락일 ──────────────────────────────────────────────────────────────────


def weekday_trading_day(d: date, step: int) -> date:
    """d 에서 step(−1 또는 +1) 방향으로 평일이면서 12-31 이 아닌 첫 날(d 자신 제외)."""
    d = d + timedelta(days=step)
    while d.isoweekday() > 5 or (d.month == 12 and d.day == 31):
        d += timedelta(days=step)
    return d


def _last_weekday_on_or_before(d: date) -> date:
    while d.isoweekday() > 5 or (d.month == 12 and d.day == 31):
        d -= timedelta(days=1)
    return d


def ex_dividend_date(record_dd: str, days: Sequence[str]) -> tuple[str, str]:
    """배당기준일 → 배당락일. 규칙: **기준일 이하 마지막 거래일의 바로 앞 거래일**(결제일 T+2 — 기준일이 거래일이면 그 전 거래일, 휴장이면 그 전 마지막 거래일의 또 그 전).

    연말 결산배당 12-31 은 휴장이라 「기준일 직전 거래일」로 쓰면 하루 틀린다(마지막 거래일 12-30 → 배당락 12-29; 운영 읽기 대조 20건 중 19건).
    `days` = 거래일 달력(오름차순). 기준일 앞뒤 거래일이 달력 안에 있으면 (배당락일, "daily_calendar"), 아니면 평일 달력에 12-31 휴장만 넣어
    근사하고 (배당락일, "weekday_approx") — 설·추석 같은 평일 휴장은 반영하지 못한다."""
    i = bisect.bisect_right(days, record_dd) - 1
    if days and i >= 1 and days[0] <= record_dd <= days[-1]:
        return days[i - 1], "daily_calendar"
    last = _last_weekday_on_or_before(parse_dd(record_dd))
    return fmt_dd(weekday_trading_day(last, -1)), "weekday_approx"


def locate_interval(dates: Sequence[str], dd: str) -> int | None:
    """dd 를 품은 구간 j (`dates[j] < dd <= dates[j+1]`). 기간 밖이면 None."""
    if len(dates) < 2 or dd <= dates[0] or dd > dates[-1]:
        return None
    return bisect.bisect_left(dates, dd) - 1


# ── 리밸런스·블렌딩 ────────────────────────────────────────────────────────────


def rebalance_intervals(dates: Sequence[str], mode: str) -> frozenset[int]:
    """목표 비중으로 되돌리는 구간 번호 — 새 달(분기)의 첫 관측일을 끝으로 하는 구간 앞에서 되돌린다(그 앞 관측일 종가 기준).
    mode: "none" | "monthly" | "quarterly". 첫 구간(0)은 시작 비중이라 넣지 않는다."""
    if mode == "none" or len(dates) < 3:
        return frozenset()
    if mode == "monthly":
        bucket = lambda dd: (dd[:4], dd[4:6])                              # noqa: E731
    elif mode == "quarterly":
        bucket = lambda dd: (dd[:4], (int(dd[4:6]) - 1) // 3)              # noqa: E731
    else:
        raise ValueError(f"rebalance 는 none/monthly/quarterly 중 하나: {mode!r}")
    return frozenset(j for j in range(1, len(dates) - 1) if bucket(dates[j + 1]) != bucket(dates[j]))


@dataclass
class Simulation:
    weights_start: list[list[float]]     # [구간 j][보유 p] — 구간 시작 비중(합 1)
    rp: list[float]                      # 구간 수익률


def simulate(ret: Sequence[Sequence[float]], targets: Mapping[int, Sequence[float]], rebalance: Iterable[int] = (),
             last_idx: Sequence[int] | None = None) -> Simulation:
    """보유 p 들의 구간 수익률 `ret[p][j]` 로 포트폴리오를 굴린다.

    targets[0] = 시작 비중(합 1). 리밸런스 구간 j 의 목표는 targets[j] 가 있으면 그것(시총 가중의 그 날 시총 비중), 없으면 targets[0].
    last_idx[p] = 그 보유의 마지막 가격 관측 번호(dates 인덱스, 끝까지 살아 있으면 n). 그보다 뒤 구간의 수익률은 0 이어야 하고
    (호출부가 0 으로 채운다), 그 보유는 리밸런스에서 건드리지 않는다(현금)."""
    m = len(ret)
    n = len(ret[0]) if m else 0
    if m == 0 or n == 0:
        return Simulation([], [])
    w = [float(x) for x in targets[0]]
    if len(w) != m:
        raise ValueError("시작 비중 수 ≠ 보유 수")
    reb = frozenset(rebalance)
    weights_start: list[list[float]] = []
    rp: list[float] = []
    for j in range(n):
        if j in reb:
            tgt = targets.get(j, targets[0])
            tradable = [(last_idx is None or last_idx[p] >= j + 1) for p in range(m)]
            dead = sum(w[p] for p in range(m) if not tradable[p])
            tsum = sum(tgt[p] for p in range(m) if tradable[p])
            if tsum > 0:
                scale = (1.0 - dead) / tsum
                w = [w[p] if not tradable[p] else tgt[p] * scale for p in range(m)]
        weights_start.append(list(w))
        r_j = sum(w[p] * ret[p][j] for p in range(m))
        rp.append(r_j)
        denom = 1.0 + r_j
        if denom > _EPS:
            w = [w[p] * (1.0 + ret[p][j]) / denom for p in range(m)]
    return Simulation(weights_start, rp)


# ── 누적·연율·위험 ─────────────────────────────────────────────────────────────


def cumulative(r: Iterable[float]) -> float:
    """R = Π(1+r) − 1."""
    w = 1.0
    for x in r:
        w *= 1.0 + x
    return w - 1.0


def wealth_curve(r: Iterable[float]) -> list[float]:
    """부(wealth) 곡선 [1, W1, …, Wn]."""
    out = [1.0]
    for x in r:
        out.append(out[-1] * (1.0 + x))
    return out


def annualize(cum: float, days: int) -> float | None:
    """연율 = (1+R)^(365.25/일수) − 1. 기간이 365일 미만이면 None(짧은 기간의 연율은 오해를 부른다)."""
    if days < 365 or 1.0 + cum <= 0:
        return None
    return (1.0 + cum) ** (365.25 / days) - 1.0


def stdev(x: Sequence[float]) -> float | None:
    """표본 표준편차(ddof=1). 관측이 둘 미만이면 None."""
    return statistics.stdev(x) if len(x) >= 2 else None


def vol_ann(r: Sequence[float], a: float | None) -> float | None:
    s = stdev(r)
    return None if s is None or a is None else s * math.sqrt(a)


def downside_vol_ann(excess: Sequence[float], a: float | None) -> float | None:
    """하방 변동성 = sqrt(mean(min(r − rf, 0)²)) × √A. 기준선(MAR)은 무위험 금리다."""
    if not excess or a is None:
        return None
    return math.sqrt(sum(min(x, 0.0) ** 2 for x in excess) / len(excess)) * math.sqrt(a)


def max_drawdown(r: Sequence[float], dates: Sequence[str]) -> tuple[float, str, str] | None:
    """부 곡선 기준 최대 낙폭 (낙폭(≤0), 고점 날짜, 저점 날짜). `dates` 는 기준일 포함(길이 n+1)."""
    if not r or len(dates) != len(r) + 1:
        return None
    wealth = wealth_curve(r)
    peak_w, peak_i = wealth[0], 0
    best, best_from, best_to = 0.0, 0, 0
    for i, w in enumerate(wealth):
        if w > peak_w:
            peak_w, peak_i = w, i
        dd = w / peak_w - 1.0
        if dd < best:
            best, best_from, best_to = dd, peak_i, i
    return best, dates[best_from], dates[best_to]


def sharpe(excess: Sequence[float], a: float | None) -> float | None:
    """mean(r − rf) / sd(r − rf) × √A."""
    s = stdev(excess)
    if s is None or s <= _EPS or a is None:
        return None
    return statistics.fmean(excess) / s * math.sqrt(a)


def sortino(excess: Sequence[float], a: float | None) -> float | None:
    """mean(r − rf) × A / 하방 변동성(연율)."""
    dd = downside_vol_ann(excess, a)
    if dd is None or dd <= _EPS or a is None:
        return None
    return statistics.fmean(excess) * a / dd


def beta_alpha(ex_p: Sequence[float], ex_b: Sequence[float], a: float | None) -> tuple[float | None, float | None]:
    """초과수익끼리의 OLS 기울기 β = cov/var, 연율 알파 = (mean ex_p − β·mean ex_b) × A."""
    if len(ex_p) != len(ex_b) or len(ex_p) < 2:
        return None, None
    mp, mb = statistics.fmean(ex_p), statistics.fmean(ex_b)
    var = sum((y - mb) ** 2 for y in ex_b) / (len(ex_b) - 1)
    if var <= _EPS * _EPS:
        return None, None
    cov = sum((x - mp) * (y - mb) for x, y in zip(ex_p, ex_b)) / (len(ex_b) - 1)
    beta = cov / var
    return beta, None if a is None else (mp - beta * mb) * a


def correlation(x: Sequence[float], y: Sequence[float]) -> float | None:
    if len(x) != len(y) or len(x) < 2:
        return None
    try:
        return statistics.correlation(x, y)
    except statistics.StatisticsError:        # 한쪽이 상수
        return None


def tracking_error(rp: Sequence[float], rb: Sequence[float], a: float | None) -> float | None:
    """sd(r_p − r_b) × √A."""
    s = stdev([p - b for p, b in zip(rp, rb)])
    return None if s is None or a is None else s * math.sqrt(a)


def info_ratio(rp: Sequence[float], rb: Sequence[float], a: float | None) -> float | None:
    """mean(r_p − r_b) × A / 추적오차."""
    te = tracking_error(rp, rb, a)
    if te is None or te <= _EPS or a is None or not rp:
        return None
    return statistics.fmean([p - b for p, b in zip(rp, rb)]) * a / te


# ── 무위험 금리 ────────────────────────────────────────────────────────────────


@dataclass
class RfAccrual:
    per_interval: list[float | None]       # 구간별 적립률(소수) — 그 구간 시작 이전 관측이 없으면 None
    asof: str | None                       # 쓴 관측 중 가장 늦은 날짜
    missing: int                           # None 인 구간 수
    avg_pct: float | None                  # 쓴 연율 %(구간 평균)


def rf_accrual(rate_obs: Sequence[tuple[str, float]], dates: Sequence[str]) -> RfAccrual:
    """연율 % → 구간 단리(ACT/365). rf_j = rate(구간 시작일 **이전·당일**의 가장 늦은 관측)/100 × 구간 달력일/365.

    구간 시작 시점의 값만 쓴다(미래 정보 금지). 휴일·지연이면 앞 값을 이어 쓴다. rate_obs = [(YYYYMMDD, 연율 %)] 오름차순."""
    keys = [d for d, _ in rate_obs]
    out: list[float | None] = []
    used: list[float] = []
    asof: str | None = None
    for j in range(len(dates) - 1):
        k = bisect.bisect_right(keys, dates[j]) - 1
        if k < 0:
            out.append(None)
            continue
        rate = rate_obs[k][1]
        out.append(rate / 100.0 * calendar_days(dates[j], dates[j + 1]) / 365.0)
        used.append(rate)
        if asof is None or rate_obs[k][0] > asof:
            asof = rate_obs[k][0]
    return RfAccrual(out, asof, sum(1 for x in out if x is None), statistics.fmean(used) if used else None)


def fixed_rf_accrual(rate_pct: float, dates: Sequence[str]) -> RfAccrual:
    """고정 금리(연 %) 적립 — 사용자가 숫자를 준 경우."""
    return RfAccrual([rate_pct / 100.0 * calendar_days(dates[j], dates[j + 1]) / 365.0 for j in range(len(dates) - 1)],
                     None, 0, rate_pct)


# ── Carino 연결 ────────────────────────────────────────────────────────────────


def carino_k(r: float) -> float:
    """k = ln(1+r)/r, r→0 이면 1."""
    return 1.0 if abs(r) < _EPS else math.log1p(r) / r


def link_contributions(period_contribs: Sequence[Sequence[float]], rp: Sequence[float]) -> list[float]:
    """기간별 기여 c[j][p] (Σ_p c[j][p] = rp[j]) → 보유별 연결 기여 Σ_j c·k_j/K. 합 = 누적 수익률 Π(1+rp)−1."""
    if not period_contribs:
        return []
    m = len(period_contribs[0])
    big_k = carino_k(cumulative(rp))
    out = [0.0] * m
    for c_j, r_j in zip(period_contribs, rp):
        coef = carino_k(r_j) / big_k
        for p in range(m):
            out[p] += c_j[p] * coef
    return out


def _active_k(a: float, b: float) -> float:
    """(ln(1+a) − ln(1+b)) / (a − b); a=b 이면 극한 1/(1+b)."""
    if abs(a - b) < _EPS:
        return 1.0 / (1.0 + b)
    return (math.log1p(a) - math.log1p(b)) / (a - b)


def carino_active_coeffs(rp: Sequence[float], rb: Sequence[float]) -> tuple[list[float], float]:
    """초과수익(대상 − 벤치마크)용 Carino 계수 (k_j, K). Σ_j (rp_j − rb_j)·k_j / K = Rp − Rb 가 정확히 성립한다."""
    return ([_active_k(a, b) for a, b in zip(rp, rb)], _active_k(cumulative(rp), cumulative(rb)))


# ── Brinson-Fachler ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class GroupPeriod:
    """한 기간의 한 묶음(업종) — 시작 비중과 그 기간 수익률. 비중이 0 인 쪽의 수익률은 None 이어도 된다."""
    w_p: float
    r_p: float | None
    w_b: float
    r_b: float | None


@dataclass(frozen=True)
class BrinsonPeriod:
    effects: dict[str, tuple[float, float, float]]     # 묶음 → (배분, 선택, 상호작용)
    r_p: float                                         # 대상 수익률 Σ wP·rP
    r_b: float                                         # 벤치마크 수익률 Σ wB·rB


def brinson_fachler(groups: Mapping[str, GroupPeriod]) -> BrinsonPeriod:
    """한 기간의 Brinson-Fachler 분해. 배분 = (wP−wB)(rB_j−RB), 선택 = wB(rP_j−rB_j), 상호작용 = (wP−wB)(rP_j−rB_j).

    수익률이 없는 쪽(비중 0)은 중립으로 채운다 — 대상이 안 가진 묶음은 rP := rB(선택·상호작용 0), 벤치마크가 안 가진 묶음은 rB := RB(배분 0).
    어느 값으로 채워도 합 Σ(배분+선택+상호작용) = Σ wP·rP − Σ wB·rB 는 변하지 않는다."""
    r_b_total = sum(g.w_b * g.r_b for g in groups.values() if g.w_b and g.r_b is not None)
    r_p_total = sum(g.w_p * g.r_p for g in groups.values() if g.w_p and g.r_p is not None)
    eff: dict[str, tuple[float, float, float]] = {}
    for code, g in groups.items():
        rb = g.r_b if (g.w_b and g.r_b is not None) else r_b_total
        rp = g.r_p if (g.w_p and g.r_p is not None) else rb
        eff[code] = ((g.w_p - g.w_b) * (rb - r_b_total), g.w_b * (rp - rb), (g.w_p - g.w_b) * (rp - rb))
    return BrinsonPeriod(eff, r_p_total, r_b_total)


@dataclass
class AttributionLinked:
    by_group: dict[str, tuple[float, float, float]]
    allocation: float
    selection: float
    interaction: float
    active: float                    # Rp − Rb
    residual: float                  # active − (배분+선택+상호작용)
    r_p: float
    r_b: float


def link_attribution(periods: Sequence[BrinsonPeriod]) -> AttributionLinked:
    """기간별 분해를 초과 Carino 로 잇는다 — 묶음별 연결 효과, 세 효과 합, 연결한 초과수익, residual."""
    rp = [p.r_p for p in periods]
    rb = [p.r_b for p in periods]
    ks, big_k = carino_active_coeffs(rp, rb)
    codes: list[str] = []
    for p in periods:
        for c in p.effects:
            if c not in codes:
                codes.append(c)
    linked = {c: [0.0, 0.0, 0.0] for c in codes}
    for p, k in zip(periods, ks):
        coef = k / big_k if abs(big_k) > _EPS else 0.0
        for c, (a, s, i) in p.effects.items():
            lc = linked[c]
            lc[0] += a * coef
            lc[1] += s * coef
            lc[2] += i * coef
    alloc = sum(v[0] for v in linked.values())
    sel = sum(v[1] for v in linked.values())
    inter = sum(v[2] for v in linked.values())
    big_rp, big_rb = cumulative(rp), cumulative(rb)
    active = big_rp - big_rb
    return AttributionLinked({c: (v[0], v[1], v[2]) for c, v in linked.items()}, alloc, sel, inter, active,
                             active - (alloc + sel + inter), big_rp, big_rb)


# ── 묶음 보조 ──────────────────────────────────────────────────────────────────


def group_returns(weights: Sequence[float], rets: Sequence[float], groups: Sequence[str]) -> dict[str, tuple[float, float | None]]:
    """보유별 (시작 비중, 구간 수익률) → 묶음별 (비중 합, 묶음 내 가중 수익률). 비중 0 인 묶음의 수익률은 None."""
    w: dict[str, float] = {}
    wr: dict[str, float] = {}
    for wi, ri, g in zip(weights, rets, groups):
        w[g] = w.get(g, 0.0) + wi
        wr[g] = wr.get(g, 0.0) + wi * ri
    return {g: (w[g], (wr[g] / w[g]) if w[g] > _EPS else None) for g in w}


@dataclass
class SeriesStats:
    """대상 수익률 열 하나의 요약(`performance_analysis` 가 지표 묶음을 만들 때 쓴다)."""
    cum: float
    ann: float | None
    vol: float | None
    mdd: tuple[float, str, str] | None
    a: float | None
    days: int
    extra: dict[str, float] = field(default_factory=dict)


def series_stats(r: Sequence[float], dates: Sequence[str]) -> SeriesStats:
    days = calendar_days(dates[0], dates[-1])
    a = obs_per_year(len(r), days)
    cum = cumulative(r)
    return SeriesStats(cum, annualize(cum, days), vol_ann(r, a), max_drawdown(r, dates), a, days)
