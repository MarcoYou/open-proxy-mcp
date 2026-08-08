"""자본잠식률은 자기자본에서 **비지배지분을 뺀 값**으로 잰다.

코스닥시장 공시·상장관리 해설서:
    자본잠식률[(자본금-자기자본)/자본금*100]이 50% 이상
    적용기준 ① 연결재무제표 작성대상법인의 경우에는 연결재무제표를 기준으로 하되
             **자기자본에서 비지배지분을 제외**

규정마다 다르다는 점이 중요하다 — 바로 옆 「법인세비용차감전계속사업손실」 기준은 「연결 기준,
**비지배지분 포함**」이다. 일부러 갈라놓은 것이라 한쪽 관행을 다른 쪽에 쓰면 안 된다.
비지배지분을 포함하면 자회사 소수주주 몫만큼 자기자본이 부풀어 **잠식률이 과소 산정**된다.
"""

from __future__ import annotations

from open_proxy_mcp.services.financial_metrics import _compute_metrics


def _metrics(*, capital_stock: int, total_equity: int, controlling: int | None):
    bs_is = {"total_equity": total_equity, "capital_stock": capital_stock}
    if controlling is not None:
        bs_is["controlling_equity"] = controlling
    return _compute_metrics(bs_is=bs_is, bs_is_prev=None, detail=None,
                            detail_prev=None, indx_map=None)


def test_non_controlling_interest_is_excluded() -> None:
    """비지배지분을 포함하면 자기자본이 부풀어 잠식이 실제보다 작아 보인다.

    실측 아시아나항공 FY2024: 자본총계 기준 2.61% → 지배지분 기준 **13.02%**.
    """
    m = _metrics(capital_stock=1_030_000_000_000,
                 total_equity=1_003_100_000_000,
                 controlling=895_900_000_000)
    assert m["capital_impairment_basis"] == "controlling"
    assert 12.0 < m["capital_impairment_ratio_pct"] < 14.0


def test_it_falls_back_to_total_equity_and_says_so() -> None:
    """별도재무제표는 비지배지분이 없어 둘이 같다 — 못 구하면 물러나되 기준을 밝힌다."""
    m = _metrics(capital_stock=1_000_000_000, total_equity=800_000_000, controlling=None)
    assert m["capital_impairment_basis"] == "total"
    assert m["capital_impairment_ratio_pct"] == 20.0


def test_full_impairment_is_judged_on_the_same_basis() -> None:
    """자기자본이 0 이하면 완전 자본잠식 — 비지배지분이 그 판정을 가려서는 안 된다."""
    m = _metrics(capital_stock=100_000_000_000,
                 total_equity=50_000_000_000,      # 비지배 포함하면 양수
                 controlling=-10_000_000_000)      # 지배지분은 음수
    assert m["capital_impairment_status"] == "full"


def test_the_fifty_percent_line_uses_controlling_equity() -> None:
    """지배지분 기준으로 50%를 넘으면 관리종목 구간이다 — 자본총계로는 안 넘는 경우."""
    m = _metrics(capital_stock=100_000_000_000,
                 total_equity=70_000_000_000,      # 자본총계 기준 30%
                 controlling=40_000_000_000)       # 지배지분 기준 60%
    assert m["capital_impairment_status"] == "partial_50plus"
