# -*- coding: utf-8 -*-
"""스크리너 실적 카드의 「컨센서스 대비」 줄 — 네트워크·DB 0.

지키는 것:
① 계산은 다시 하지 않는다 — `provisional_earnings.consensus_gap` 을 카드 크기로 옮기기만(규칙 6).
② 카드엔 맞댄 지표만 · 출처·계산식 문장은 카드마다 싣지 않고 md 각주에 한 번.
③ ok 카드: 지표별 차이%·예상치·기준 스냅샷일 한 줄. 적자 예상 대비면 그렇다고 적는다.
④ 맞대지 못한 카드(월간·커버리지 밖·이력 밖·장애·연결/별도 불일치)·다른 유형 카드엔 줄도 칸도 없다.
"""
from __future__ import annotations

from open_proxy_mcp.services import screener as S
from open_proxy_mcp.tools import screener as T

JO = 10**12

# provisional_earnings 가 삼성전자 2026-10-08 잠정실적에 실제로 낸 칸(운영 DB 261018) 꼴
GAP_OK = {
    "status": "ok", "as_of": "2026-09-26", "lag_days": 12, "period": "2026.09E", "basis": "IFRS연결",
    "source": "컨센서스 추정치 주간 스냅샷 `fwd_hist` …", "note": "차이 %는 …",
    "metrics": {
        "revenue": {"actual_krw": 195 * JO, "consensus_krw": 204_570_360_000_000,
                    "diff_krw": 195 * JO - 204_570_360_000_000, "gap_pct": -4.68},
        "operating_profit": {"actual_krw": 107_400_000_000_000, "consensus_krw": 110_573_640_000_000,
                             "diff_krw": 107_400_000_000_000 - 110_573_640_000_000, "gap_pct": -2.87},
        "net_income_controlling": {"actual_krw": None, "consensus_krw": 90_526_380_000_000,
                                   "why": "잠정공시에 지배주주순이익 값이 없다 — …"},
    },
}


def _payload(gap):
    return {"status": "exact", "data": {
        "headline": {"revenue": {"value_krw": 195 * JO, "yoy_pct": 126.59},
                     "operating_profit": {"value_krw": 107.4 * JO, "yoy_pct": 782.5}},
        "kind": "financial", "consolidated": True, "period_kind": "quarter", "fiscal_year": 2026,
        "fiscal_quarter": 3, "period": {"start": "2026-07-01", "end": "2026-09-30"},
        "consensus_gap": gap}}


def _card(fields, code="earnings"):
    return {"type": {"code": code, "label": "잠정실적"}, "detail_fields": fields, "detail_status": "parsed",
            "corp_name": "삼성전자", "stock_code": "005930", "stage": "잠정", "title": "연결재무제표기준영업(잠정)실적"}


# ── ① ② 추출 ──────────────────────────────────────────────────────────────────
def test_extract_moves_gap_compactly():
    f = S._extract_earnings(_payload(GAP_OK), "20261008800004")
    g = f["consensus_gap"]
    assert g["status"] == "ok" and g["as_of"] == "2026-09-26" and g["lag_days"] == 12
    assert g["period"] == "2026.09E" and g["basis"] == "IFRS연결"
    assert set(g["metrics"]) == {"revenue", "operating_profit"}          # 맞댄 지표만
    assert g["metrics"]["operating_profit"] == {"consensus_krw": 110_573_640_000_000,
                                                "diff_krw": 107_400_000_000_000 - 110_573_640_000_000,
                                                "gap_pct": -2.87}
    assert "source" not in g and "note" not in g                          # 카드마다 싣지 않는다
    assert f["revenue_krw"] == 195 * JO                                   # 원문 값은 그대로


def test_not_compared_cards_carry_nothing():
    # 모르거나 맞댈 수 없으면 카드엔 싣지 않는다(261018 결정) — 줄도, json 칸도.
    for status, why in (("not_comparable", "월간 실적 — …"), ("out_of_coverage", "공시 전 … — 커버리지 밖"),
                        ("out_of_history", "이력 … — 모른다"), ("unknown", "DB 조회 실패 — 장애")):
        f = S._extract_earnings(_payload({"status": status, "why": why, "as_of": "2026-10-10"}), "x")
        assert f["consensus_gap"] is None, status
        assert T._gap_lines(_card(f)) == [], status


def test_extract_without_gap_leaves_no_field():
    f = S._extract_earnings(_payload(None), "x")
    assert f["consensus_gap"] is None                                     # _fetch_detail 이 None 칸을 걸러 낸다


# ── ③ ok 줄 ───────────────────────────────────────────────────────────────────
def test_ok_line():
    f = S._extract_earnings(_payload(GAP_OK), "x")
    assert T._gap_lines(_card(f)) == [
        "    - 컨센서스 대비 매출 -4.7%(예상 204.57조원) · 영업이익 -2.9%(예상 110.57조원) "
        "— 공시 12일 전 2026-09-26 스냅샷"]


def test_loss_consensus_and_zero_denominator():
    gap = {"status": "ok", "as_of": "2026-10-10", "lag_days": 6, "metrics": {
        "operating_profit": {"consensus_krw": -2_000_000_000, "diff_krw": 3_000_000_000, "gap_pct": 150.0,
                             "gap_caveat": "…"},
        "net_income_controlling": {"consensus_krw": 0, "diff_krw": 500_000_000}}}
    line = T._gap_lines(_card({"consensus_gap": gap}))[0]
    assert "영업이익 +150.0%(예상 -20억원·적자 예상 대비)" in line
    assert "지배순이익 5억원 차(예상 –)" in line                          # 분모 0 — %가 없으니 차액으로


# ── ④ 다른 카드 ───────────────────────────────────────────────────────────────
def test_other_cards_get_no_line():
    assert T._gap_lines(_card({"consensus_gap": GAP_OK}, code="order")) == []
    assert T._gap_lines(_card({})) == []
    assert T._gap_lines(_card({"revenue_krw": 1})) == []


def _digest(hits):
    return {"status": "ok", "as_of": "2026-10-18T09:00:00", "period": {"bgn_de": "20261008", "end_de": "20261008"},
            "universe": {"label": "전체시장"}, "counts": {"scanned": 10, "hits": len(hits), "returned": len(hits)},
            "paging": {"offset": 0, "matched": len(hits), "returned": len(hits), "has_more": False},
            "coverage": [], "hits": hits, "warnings": []}


def test_digest_footnote_once_when_a_line_is_shown():
    card = _card(S._extract_earnings(_payload(GAP_OK), "x"))
    out = T._render_digest(_digest([card, dict(card, corp_name="LG전자")]))
    assert out.count("컨센서스 대비 매출") == 2
    assert out.count(T._GAP_FOOTNOTE) == 1


def test_digest_no_footnote_without_lines():
    card = _card({"revenue_krw": 1})
    assert T._GAP_FOOTNOTE not in T._render_digest(_digest([card]))


def test_card_percent_rounds_once_from_raw_values():
    # 국도화학 2026.09 매출: (4,398.57억 − 4,652억) ÷ 4,652억 = −5.4478% → 「−5.4%」(공시알리미 카드와 같다)
    gap = {"status": "ok", "as_of": "2026-10-10", "lag_days": 6, "metrics": {
        "revenue": {"consensus_krw": 465_200_000_000, "diff_krw": 439_857_000_000 - 465_200_000_000,
                    "gap_pct": -5.45}}}
    line = T._gap_lines(_card({"consensus_gap": gap}))[0]
    assert "매출 -5.4%(" in line and "-5.5%" not in line


def test_monthly_card_is_not_labelled_as_a_quarter():
    # 신세계 2026-09 월 매출 — 종전엔 「2026 사업연도 3분기 · 기간 2026-09-01~2026-09-30」
    payload = {"status": "exact", "data": {
        "headline": {"revenue": {"value_krw": 1981 * 10**8}}, "kind": "financial", "consolidated": False,
        "period_kind": "month", "period_month": 9, "fiscal_year": 2026, "fiscal_quarter": 3,
        "period": {"start": "2026-09-01", "end": "2026-09-30"}, "comparison_basis": "전년동월 대비"}}
    line = T._detail_line(_card(S._extract_earnings(payload, "x")))
    assert line.startswith("2026년 9월(월간) · 기간 2026-09-01~2026-09-30"), line
    assert "분기" not in line
