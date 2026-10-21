"""financial_metrics 연간 net_income_krw 의 기준 표시 — 지배 순이익을 못 구해 총계(비지배 포함)로 물러나면 그 사실을 싣고,
당기·전기 기준이 다르면 전년비를 내지 않는다(DATA-NI2 의 연간 변형). network 0 · DB 0.
"""
from open_proxy_mcp.services.financial_metrics import _compute_metrics


def _metrics(cur_ctrl, prev_ctrl, *, nci=180):
    """당기 총계 100(지배 cur_ctrl) · 전기 총계 60(지배 prev_ctrl). cur_ctrl/prev_ctrl 이 None 이면 그 해 지배 순이익을 못 구한 꼴."""
    detail = {"controlling_equity": 420, "nci": nci}
    if cur_ctrl is not None:
        detail["controlling_interest_income"] = cur_ctrl
    detail_prev = {"controlling_equity": 400, "nci": 100}
    if prev_ctrl is not None:
        detail_prev["controlling_interest_income"] = prev_ctrl
    return _compute_metrics(
        bs_is={"net_income": 100, "total_equity": 600, "total_assets": 1000, "revenue": 500, "operating_profit": 50},
        bs_is_prev={"net_income": 60, "total_equity": 500, "total_assets": 900, "revenue": 400},
        detail=detail, detail_prev=detail_prev, indx_map=None)


def test_current_total_fallback_vs_previous_controlling_gives_no_yoy():
    """당기 지배 순이익을 못 구해 총계 100 으로 물러남 · 전기는 지배 50 → 종전 +100% (같은 기준이면 60/50 꼴이 아니다). 기준이 달라 전년비는 비운다."""
    m = _metrics(None, 50)
    assert m["net_income_krw"] == 100 and m["prev_net_income_krw"] == 50     # 값·옛 키는 그대로
    assert m["net_income_basis"] == "total_incl_nci"
    assert m["prev_net_income_basis"] == "controlling"
    assert m["net_income_yoy_pct"] is None


def test_previous_total_fallback_vs_current_controlling_gives_no_yoy():
    m = _metrics(70, None)
    assert m["net_income_basis"] == "controlling" and m["prev_net_income_basis"] == "total_incl_nci"
    assert m["net_income_yoy_pct"] is None


def test_same_basis_keeps_yoy_and_labels_controlling():
    m = _metrics(70, 50)
    assert m["net_income_basis"] == m["prev_net_income_basis"] == "controlling"
    assert m["net_income_yoy_pct"] == 40.0


def test_both_total_fallback_keeps_yoy_but_labels_total():
    m = _metrics(None, None)
    assert m["net_income_basis"] == m["prev_net_income_basis"] == "total_incl_nci"
    assert m["net_income_yoy_pct"] == round((100 - 60) / 60 * 100, 2)


def test_total_basis_with_nci_flags_roe_and_margin_note():
    m = _metrics(None, None, nci=180)
    assert m["net_income_basis_note"] and "ROE" in m["net_income_basis_note"]
    m0 = _metrics(None, None, nci=0)           # 비지배가 0 이면 총계 = 지배 — 경고 없음
    assert m0["net_income_basis_note"] is None
    mc = _metrics(70, 50)
    assert mc["net_income_basis_note"] is None
