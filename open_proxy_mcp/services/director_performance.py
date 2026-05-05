"""사내이사 재직 중 회사 운영 성과 매트릭스 (2x3).

User 비판 (코붕이, 2026-05-05 고려아연 케이스): proxy_advise가 회사·현 경영진 방어 편향.
사내이사 자동 FOR (결격사유만 검증) → status quo 무검증.

해결: 재직 중 회사 운영 성과 axis 추가.

매트릭스:
- ROE × (avg, trend)
- 부채비율 × (avg, trend)
- CSR × (avg, trend) — 배당+소각 / 누적 지배주주순이익

점수: good +2 / moderate +1 / weak 0 / bad -1
종합 (총점 -6 ~ +12):
- ≥+7 good / +3~+6 moderate / 0~+2 weak / <0 bad
  (KOSPI 100 audit 260505: ≥9 cutoff은 good 7.7%로 너무 보수적, ≥7로 26.4%·target 20-40% 충족)

Special rules:
- 자본잠식 (full): ROE/leverage avg 자동 bad
- 적자 + 환원 활동 (배당+소각 > 0): CSR 일괄 weak (자본 잠식 가속)
- 적자 + 환원 자제: CSR moderate (보수성)

ralph: wiki/ralph/260505_1611_ralph_inside-director-performance-matrix.md
"""

from __future__ import annotations

from typing import Any


GOOD = 2
MODERATE = 1
WEAK = 0
BAD = -1


def _label(score: int) -> str:
    return {2: "good", 1: "moderate", 0: "weak", -1: "bad"}.get(score, "n/a")


# ── ROE ──

def _score_roe_avg(avg_pct: float | None, capital_impairment_status: str | None) -> int:
    """ROE 평균 점수.

    bad: <0% 또는 자본잠식 (full)
    weak: 0-5%
    moderate: 5-15%
    good: ≥15%
    """
    if capital_impairment_status == "full":
        return BAD
    if avg_pct is None:
        return WEAK  # 데이터 없으면 보수적 weak
    if avg_pct < 0:
        return BAD
    if avg_pct < 5:
        return WEAK
    if avg_pct < 15:
        return MODERATE
    return GOOD


def _score_roe_trend(trend_pp_per_year: float | None) -> int:
    """ROE 추세 점수 (연평균 변화 %p).

    bad: < -3%p/년 (급격 악화)
    weak: -3 ~ -1%p/년 (악화)
    moderate: -1 ~ +1%p/년 (유지)
    good: ≥ +1%p/년 (개선)
    """
    if trend_pp_per_year is None:
        return WEAK
    if trend_pp_per_year < -3:
        return BAD
    if trend_pp_per_year < -1:
        return WEAK
    if trend_pp_per_year < 1:
        return MODERATE
    return GOOD


# ── Leverage (부채비율) ──

def _score_leverage_avg(avg_pct: float | None, capital_impairment_status: str | None) -> int:
    """부채비율 평균 점수.

    bad: >200% 또는 자본잠식 (full)
    weak: 100-200%
    moderate: 50-100%
    good: <50%
    """
    if capital_impairment_status == "full":
        return BAD
    if avg_pct is None:
        return WEAK
    if avg_pct > 200:
        return BAD
    if avg_pct > 100:
        return WEAK
    if avg_pct > 50:
        return MODERATE
    return GOOD


def _score_leverage_trend(delta_pct_total: float | None) -> int:
    """부채비율 추세 — 재직 누적 변화 (%p).

    bad: > +10%p (큰 악화)
    weak: 0 ~ +10%p (유지/약화)
    moderate: -1 ~ -20%p (개선)
    good: ≤ -20%p (대폭 개선)
    """
    if delta_pct_total is None:
        return WEAK
    if delta_pct_total > 10:
        return BAD
    if delta_pct_total >= 0:
        return WEAK
    if delta_pct_total > -20:
        return MODERATE
    return GOOD


# ── CSR (Cash Shareholder Return) ──

def _score_csr(
    avg_pct: float | None,
    trend_pp_per_year: float | None,
    avg_net_income: int | None,
    total_dividend: int,
    total_cancelation: int,
) -> tuple[int, int]:
    """CSR (avg, trend) 점수 tuple.

    Special rules:
    - 적자 (avg net_income < 0) + 환원 활동 (배당+소각 > 0): 둘 다 weak (자본 잠식 가속)
    - 적자 + 환원 자제 (배당+소각 == 0): 둘 다 moderate (보수성)

    일반:
    avg:
      bad: 0% (배당+소각 모두 0)
      weak: 0-10%
      moderate: 10-30%
      good: ≥30%
    trend:
      bad: 환원 자체 사라짐 (마지막 N년 환원 X)
      weak: 감소 (negative trend)
      moderate: 안정 (≈0)
      good: ≥+5%p/년 증가
    """
    has_return = (total_dividend or 0) > 0 or (total_cancelation or 0) > 0

    # Special: 적자 case
    if avg_net_income is not None and avg_net_income < 0:
        if has_return:
            return WEAK, WEAK  # 적자에서 환원 = 자본 잠식 가속
        else:
            return MODERATE, MODERATE  # 적자에서 환원 자제 = 보수성

    # 일반 — avg score
    if not has_return:
        avg_score = BAD
    elif avg_pct is None:
        avg_score = WEAK
    elif avg_pct < 10:
        avg_score = WEAK
    elif avg_pct < 30:
        avg_score = MODERATE
    else:
        avg_score = GOOD

    # 일반 — trend score
    if not has_return:
        trend_score = BAD  # 환원 자체 없으면 trend도 bad
    elif trend_pp_per_year is None:
        trend_score = WEAK
    elif trend_pp_per_year >= 5:
        trend_score = GOOD
    elif trend_pp_per_year >= -1:
        trend_score = MODERATE
    else:
        trend_score = WEAK

    return avg_score, trend_score


# ── 통합 ──

def _slope(values: list[float]) -> float | None:
    """N년 변화의 연평균 slope (단순 선형). values: [year_0_val, year_1_val, ...]"""
    if not values or len(values) < 2:
        return None
    n = len(values)
    # 단순 평균 변화: (마지막 - 첫째) / (n-1)
    delta = values[-1] - values[0]
    return delta / (n - 1)


def _avg(values: list[float]) -> float | None:
    valid = [v for v in values if v is not None]
    if not valid:
        return None
    return sum(valid) / len(valid)


def compute_performance(
    *,
    tenure_years: list[int],
    roe_yearly: dict[int, float | None],
    leverage_yearly: dict[int, float | None],
    net_income_yearly: dict[int, int | None],
    dividend_yearly: dict[int, int],
    cancelation_yearly: dict[int, int],
    capital_impairment_status: str | None = None,
) -> dict[str, Any]:
    """재직 기간 데이터 → performance 매트릭스 + classification + rationale.

    Args:
        tenure_years: 재직 기간 연도 list (예: [2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025])
        roe_yearly: 연도 → ROE %
        leverage_yearly: 연도 → 부채비율 %
        net_income_yearly: 연도 → 지배주주 당기순이익 (원)
        dividend_yearly: 연도 → 배당 총액 (원)
        cancelation_yearly: 연도 → 자사주 소각 총액 (원)
        capital_impairment_status: financial_metrics summary의 capital_impairment_status

    Returns: 매트릭스 + 점수 + classification dict
    """
    if not tenure_years:
        return {"classification": "n/a", "rationale": "재직 기간 없음 (신임 또는 detect fail)"}

    tenure_years_sorted = sorted(tenure_years)

    # 시계열 데이터 추출 (None 허용)
    roe_series = [roe_yearly.get(y) for y in tenure_years_sorted]
    leverage_series = [leverage_yearly.get(y) for y in tenure_years_sorted]
    net_income_series = [net_income_yearly.get(y) for y in tenure_years_sorted]
    dividend_series = [dividend_yearly.get(y, 0) for y in tenure_years_sorted]
    cancelation_series = [cancelation_yearly.get(y, 0) for y in tenure_years_sorted]

    # ROE
    roe_avg = _avg([v for v in roe_series if v is not None])
    roe_trend = _slope([v for v in roe_series if v is not None])
    roe_avg_score = _score_roe_avg(roe_avg, capital_impairment_status)
    roe_trend_score = _score_roe_trend(roe_trend)

    # Leverage
    lev_avg = _avg([v for v in leverage_series if v is not None])
    lev_valid = [v for v in leverage_series if v is not None]
    lev_delta_total = (lev_valid[-1] - lev_valid[0]) if len(lev_valid) >= 2 else None
    lev_avg_score = _score_leverage_avg(lev_avg, capital_impairment_status)
    lev_trend_score = _score_leverage_trend(lev_delta_total)

    # CSR
    total_dividend = sum(dividend_series)
    total_cancelation = sum(cancelation_series)
    total_return = total_dividend + total_cancelation
    total_net_income = sum(v for v in net_income_series if v is not None and v > 0)
    avg_net_income = _avg([v for v in net_income_series if v is not None])

    csr_avg = round(total_return / total_net_income * 100, 1) if total_net_income > 0 else None

    # CSR trend — 연도별 환원율 시계열 → slope
    csr_yearly = []
    for y in tenure_years_sorted:
        ni = net_income_yearly.get(y)
        if ni and ni > 0:
            ret = dividend_yearly.get(y, 0) + cancelation_yearly.get(y, 0)
            csr_yearly.append(ret / ni * 100)
    csr_trend = _slope(csr_yearly) if len(csr_yearly) >= 2 else None

    csr_avg_score, csr_trend_score = _score_csr(
        avg_pct=csr_avg,
        trend_pp_per_year=csr_trend,
        avg_net_income=int(avg_net_income) if avg_net_income is not None else None,
        total_dividend=total_dividend,
        total_cancelation=total_cancelation,
    )

    # 종합
    total = roe_avg_score + roe_trend_score + lev_avg_score + lev_trend_score + csr_avg_score + csr_trend_score

    if total >= 7:
        classification = "good"
    elif total >= 3:
        classification = "moderate"
    elif total >= 0:
        classification = "weak"
    else:
        classification = "bad"

    # rationale 한국어
    rationale_parts = []
    if roe_avg is not None:
        rationale_parts.append(f"ROE 평균 {roe_avg:.1f}% ({_label(roe_avg_score)})")
    if roe_trend is not None:
        rationale_parts.append(f"ROE 추세 {roe_trend:+.2f}%p/년 ({_label(roe_trend_score)})")
    if lev_avg is not None:
        rationale_parts.append(f"부채비율 평균 {lev_avg:.0f}% ({_label(lev_avg_score)})")
    if lev_delta_total is not None:
        rationale_parts.append(f"부채비율 누적변화 {lev_delta_total:+.0f}%p ({_label(lev_trend_score)})")
    if csr_avg is not None:
        rationale_parts.append(f"CSR 평균 {csr_avg:.1f}% ({_label(csr_avg_score)})")
    if csr_trend is not None:
        rationale_parts.append(f"CSR 추세 {csr_trend:+.1f}%p/년 ({_label(csr_trend_score)})")
    if capital_impairment_status == "full":
        rationale_parts.append("⚠ 자본잠식 (ROE/부채 자동 bad)")
    if avg_net_income is not None and avg_net_income < 0:
        if total_return > 0:
            rationale_parts.append("⚠ 적자에서 환원 (CSR weak)")
        else:
            rationale_parts.append("적자 환원 자제 (보수)")

    return {
        "tenure_period": f"{tenure_years_sorted[0]} ~ {tenure_years_sorted[-1]} ({len(tenure_years_sorted)}년)",
        "matrix": {
            "roe": {
                "avg": roe_avg,
                "trend_pp_per_year": roe_trend,
                "avg_score": roe_avg_score,
                "avg_label": _label(roe_avg_score),
                "trend_score": roe_trend_score,
                "trend_label": _label(roe_trend_score),
            },
            "leverage": {
                "avg": lev_avg,
                "delta_pp_total": lev_delta_total,
                "avg_score": lev_avg_score,
                "avg_label": _label(lev_avg_score),
                "trend_score": lev_trend_score,
                "trend_label": _label(lev_trend_score),
            },
            "csr": {
                "avg_pct": csr_avg,
                "trend_pp_per_year": csr_trend,
                "total_dividend_krw": total_dividend,
                "total_cancelation_krw": total_cancelation,
                "avg_score": csr_avg_score,
                "avg_label": _label(csr_avg_score),
                "trend_score": csr_trend_score,
                "trend_label": _label(csr_trend_score),
            },
        },
        "capital_impairment_status": capital_impairment_status,
        "avg_net_income_krw": int(avg_net_income) if avg_net_income is not None else None,
        "total_score": total,
        "max_score": 12,
        "min_score": -6,
        "classification": classification,
        "rationale": " / ".join(rationale_parts) if rationale_parts else "데이터 부족",
    }
