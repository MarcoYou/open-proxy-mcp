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


def test_the_total_equity_figure_is_mentioned_too() -> None:
    """판정은 규정대로 지배지분 기준이지만, 비지배 포함 값도 함께 말한다.

    두 값의 간격이 그 회사의 자회사 구조를 말해주고(간격이 크면 소수주주 몫이 크다),
    다른 자료(연결 자본총계 기준)와 대조할 때 필요하다. 실측 아시아나항공 13.02% vs 2.61%.
    """
    from open_proxy_mcp.services.proxy_advise import _capital_clause

    _, both = _capital_clause({
        "capital_impairment_status": "partial",
        "capital_impairment_ratio_pct": 13.02,
        "capital_impairment_ratio_total_pct": 2.61,
    }, "2024사업연도")
    assert "13.02%" in both and "비지배지분 포함" in both and "2.61%" in both

    # 간격이 작으면 군더더기다 — 언급하지 않는다
    _, close = _capital_clause({
        "capital_impairment_status": "partial",
        "capital_impairment_ratio_pct": 13.02,
        "capital_impairment_ratio_total_pct": 12.80,
    }, "2024사업연도")
    assert "비지배지분 포함" not in close

    # 정상(음수)일 때도 군더더기다
    _, normal = _capital_clause({
        "capital_impairment_status": "normal",
        "capital_impairment_ratio_pct": -500.0,
        "capital_impairment_ratio_total_pct": -600.0,
    }, "2024사업연도")
    assert "비지배지분 포함" not in normal


def test_it_derives_the_controlling_share_instead_of_falling_back() -> None:
    """지배지분 소계를 아예 안 적는 표가 있다 — 자본총계로 물러나면 규정이 금지한 과소 산정이다.

    실측 고려아연·비덴트·미래에셋증권은 자본 섹션을 「자본금·자본잉여금·…·비지배지분·자본총계」로
    평면 나열해 지배지분 행이 없다. 그때 자본총계를 쓰면 비지배 몫만큼 자기자본이 부풀어
    잠식률이 실제보다 작아 보인다. 비지배지분이 있으면 빼서 만든다.
    """
    from open_proxy_mcp.services.financial_metrics import compute_capital_impairment

    got = compute_capital_impairment(
        capital_stock=100_000_000_000,
        controlling_equity=None,          # 소계 행이 없다
        total_equity=70_000_000_000,
        nci=30_000_000_000,
    )
    assert got["basis"] == "derived"
    assert got["equity_used"] == 40_000_000_000
    assert got["ratio_pct"] == 60.0                 # 자본총계로 쟀으면 30% — 50% 선을 못 넘는다
    assert got["status"] == "partial_50plus"

    # 비지배지분조차 없으면 만들어내지 않고 자본총계로 물러나되 그 사실을 남긴다
    fallback = compute_capital_impairment(
        capital_stock=100_000_000_000, controlling_equity=None,
        total_equity=70_000_000_000, nci=None)
    assert fallback["basis"] == "total"
    assert fallback["ratio_pct"] == 30.0


def test_no_ratio_is_produced_without_a_denominator() -> None:
    """자본금이 없거나 0 이하면 나눗셈 자체가 성립하지 않는다 — 값을 만들지 않는다."""
    from open_proxy_mcp.services.financial_metrics import compute_capital_impairment

    for cap in (None, 0, -1):
        got = compute_capital_impairment(capital_stock=cap, controlling_equity=10,
                                         total_equity=10, nci=None)
        assert got["ratio_pct"] is None and got["status"] is None


def test_the_sentence_names_the_equity_it_actually_measured() -> None:
    """지배주주 지분을 못 구해 자본총계로 물러났으면, 그렇다고 써야 한다.

    예전에는 basis 를 한 번도 읽지 않고 늘 「지배주주 귀속 자기자본」이라 썼다. 별도재무제표
    에서는 우연히 참이지만(비지배지분이 없다), **연결에서 폴백한 경우엔 재지 않은 것을 쟀다고
    말하는 것**이다. 두 경우는 읽는 쪽에 뜻이 정반대라 — 별도는 정상, 연결 폴백은 「규정 기준이
    아닐 수 있다」는 신호 — 하나로 뭉치면 안 된다.
    """
    from open_proxy_mcp.services.proxy_advise import _capital_clause

    base = {"capital_impairment_status": "full", "capital_impairment_ratio_pct": 120.0}

    # ① 지배지분을 실제로 읽음 — 현행 문구 그대로
    _, ctrl = _capital_clause({**base, "capital_impairment_basis": "controlling"}, "")
    assert "지배주주 귀속 자기자본" in ctrl
    assert "확인하지 못해" not in ctrl

    # ② 별도재무제표 — 자본총계가 곧 지배지분이다. 결함이 아니라 정의다
    _, sep = _capital_clause(
        {**base, "capital_impairment_basis": "total", "fs_div": "OFS"}, "")
    assert "지배주주 귀속" not in sep
    assert "별도재무제표" in sep

    # ③ 연결인데 지배지분을 못 구함 — 여기가 예전에 거짓을 쓰던 자리
    _, fallback = _capital_clause(
        {**base, "capital_impairment_basis": "total", "fs_div": "CFS"}, "")
    assert "지배주주 귀속 자기자본 0 이하" not in fallback
    assert "확인하지 못해" in fallback

    # 잠식률 갈래에도 같은 단서가 붙는다 — 비율만 보고 규정 기준이라 오해하면 안 된다
    _, partial = _capital_clause({
        "capital_impairment_status": "partial_50plus",
        "capital_impairment_ratio_pct": 63.0,
        "capital_impairment_basis": "total",
        "fs_div": "CFS",
    }, "")
    assert "확인하지 못해" in partial
