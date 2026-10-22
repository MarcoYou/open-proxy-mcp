"""scale_guard 의 고정 앵커(MARKET_MAX_NI_ANCHOR = 시장 최대 순이익 44.26조)가 새 최대 실적 보고서에서 먼저 울리지 않는다(DATA-SC5).

FY2026 컨센서스 지배순이익은 005930 303.64조 · 000660 255.74조로 문턱(앵커 × 3 = 132.78조)을 넘는다. 앵커는 「다음 회계연도에 더 큰 회사가 나오면 수동 갱신」인데
가드가 바로 그 새 최댓값 보고서에서 먼저 울려 수동 갱신으로는 막을 수 없다. 시장 상대 상한이 혼자 hard 를 내지 못하게 한다(시총 대비·전기 대비 단서가 함께 있어야 hard).
network 0 · DB 0.
"""
from open_proxy_mcp.services import scale_guard as G

A = G.MARKET_MAX_NI_ANCHOR


def test_new_record_profit_of_a_large_cap_is_not_hard():
    """005930 FY2026E 꼴 — 303.64조, 전기 44.26조, 시총 1,500조: 앵커의 6.86배이지만 시총 대비 0.2배라 실적 급증일 뿐 단위 오류가 아니다."""
    v = G.assess(thstrm=303.64e12, frmtrm=44.26e12, mktcap=1.5e15, market_max=A)
    assert v["tier"] != "hard"
    assert "market_relative_cap" not in v["hard_hit"]
    assert "market_relative_cap_uncorroborated" in v["soft_hit"]      # 신호는 soft 로 남는다


def test_second_large_cap_is_not_hard_either():
    v = G.assess(thstrm=255.74e12, frmtrm=36.0e12, mktcap=0.9e15, market_max=A)      # 000660 FY2026E 꼴
    assert v["tier"] != "hard"


def test_softcen_million_fold_error_stays_hard():
    """소프트센 032680 FY2022 꼴 100만 배(1.07×10^16) — 시총 대비 수만 배라 시장 상대 상한이 그대로 hard."""
    v = G.assess(thstrm=1.07e16, frmtrm=2.0e10, mktcap=3.0e11, market_max=A)
    assert v["tier"] == "hard" and "market_relative_cap" in v["hard_hit"]


def test_thousand_fold_error_with_plausible_per_stays_hard():
    """PER 20 인 중형주의 1,000배 오류(실제 1,400억 → 140조): 시총 대비 50배 문턱(soft)에 닿지 못해도 5배 단서로 hard."""
    v = G.assess(thstrm=140e12, frmtrm=130e9, mktcap=2.8e12, market_max=A)
    assert v["tier"] == "hard" and "market_relative_cap" in v["hard_hit"]


def test_without_mktcap_the_cap_alone_is_still_hard():
    """시총을 모르면 단서가 없다 — 종전대로(앵커 3배 초과 = hard)."""
    v = G.assess(thstrm=303.64e12, market_max=A)
    assert v["tier"] == "hard" and "market_relative_cap" in v["hard_hit"]


def test_cap_with_a_ten_power_jump_to_prior_year_is_hard():
    """전기 대비 10^n 점프(보고서 한쪽 칸만 단위가 틀린 꼴)가 함께 걸리면 hard."""
    v = G.assess(thstrm=300e12, frmtrm=300e9, mktcap=1.5e15, market_max=A)
    assert v["tier"] == "hard" and "market_relative_cap" in v["hard_hit"]
