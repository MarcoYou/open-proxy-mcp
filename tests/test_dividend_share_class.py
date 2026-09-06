# -*- coding: utf-8 -*-
"""배당 주당값의 종류 구분 — 「우선주」 글자가 없는 종류주식이 보통주를 덮지 않는다 (260906 live 실측).

한국금융지주 FY2024: 사업보고서 alotMatter 에 보통주 3,980 / 「1종 종류주식」 4,042 두 줄.
옛 규칙(「우선주 아니면, 값이 있으면 보통주」)은 뒷줄이 앞줄을 덮어 「연간 DPS(보통주) 4,042원」·
현재가 기준 수익률·history·`price_multiple_data` 배당수익률까지 같이 틀렸다. 두산도
2,000 → 2,050(종류주식). 코스피 원장 실측: 우선주 표기 없는 종류 행 235건(그중 DPS>0 50건).
network 0콜.
"""
from __future__ import annotations

from open_proxy_mcp.services.dividend import _alot_multiyear_summaries
from open_proxy_mcp.services.dividend_parser import (
    build_dividend_summary,
    share_class,
    split_by_share_class,
)
from open_proxy_mcp.tools.dividend_disclosure import _render


def _items(rows, *, total="232,767", payout="22.4", yields=None):
    """alotMatter 행 최소 구성. rows = [(주식종류 표기, 주당 현금배당금 당기)]."""
    out = [{"category": "주당 현금배당금(원)", "stock_type": t, "current": v} for t, v in rows]
    out.append({"category": "현금배당금총액(백만원)", "stock_type": "-", "current": total})
    out.append({"category": "(연결)현금배당성향(%)", "stock_type": "-", "current": payout})
    for t, y in (yields or []):
        out.append({"category": "현금배당수익률(%)", "stock_type": t, "current": y})
    return out


# ── 분류기 ────────────────────────────────────────────────────────────────
def test_share_class_recognises_class_shares_without_the_word_preferred():
    for t in ("종류주식", "종류주", "1종 종류주식", "기타주식", "의결권 없는 주식", "전환주"):
        assert share_class(t) == "class", t
    for t in ("우선주", "제1우선주", "전환우선주", "2우선주"):
        assert share_class(t) == "preferred", t
    for t in ("보통주", "보통주식", "의결권 있는 주식", " 보통주 ", "보통주(최대주주)", "보동주"):
        assert share_class(t) == "common", t
    for t in ("-", "", "해당없음", None):
        assert share_class(t) == "unspecified", repr(t)


def test_split_keeps_common_when_a_class_row_follows():
    c, o, label = split_by_share_class([("보통주", 3980), ("1종 종류주식", 4042)])
    assert (c, o, label) == (3980, 4042, "1종 종류주식")
    c, o, label = split_by_share_class([("보통주식", 2000), ("종류주식", 2050)])
    assert (c, o, label) == (2000, 2050, "종류주식")


def test_split_treats_unlabelled_row_as_common_and_ignores_empty_rows():
    assert split_by_share_class([("-", 500)]) == (500, None, "")
    assert split_by_share_class([("보통주", 500), ("-", 0)]) == (500, None, "")
    assert split_by_share_class([("보통주", 500), ("우선주", 0)]) == (500, None, "")
    # 종류 표기 행뿐인 회사(「우량주」처럼 표기만 다른 단일 종류) — 그 행이 보통주다.
    assert split_by_share_class([("우량주", 300)]) == (300, None, "")
    # 보통주가 무배당이고 우선주만 배당 — 보통주는 0 이지 우선주 값이 아니다.
    assert split_by_share_class([("보통주", 0), ("우선주", 100)]) == (None, 100, "우선주")


# ── 사업보고서 기말 요약 ──────────────────────────────────────────────────
def test_summary_keeps_common_dps_for_korea_investment_holdings_shape():
    s = build_dividend_summary(_items([("보통주", "3,980"), ("1종 종류주식", "4,042")],
                                      yields=[("보통주", "5.7"), ("1종 종류주식", "7.2")]), "사업보고서")
    assert s["cash_dps"] == 3980
    assert s["cash_dps_preferred"] == 4042
    assert s["cash_dps_preferred_label"] == "1종 종류주식"
    assert s["yield_dart"] == 5.7 and s["yield_preferred_dart"] == 7.2
    assert s["total_amount_mil"] == 232_767 and s["payout_ratio_dart"] == 22.4


def test_summary_samsung_shape_is_unchanged():
    s = build_dividend_summary(_items([("보통주", "1,668"), ("우선주", "1,669")],
                                      yields=[("보통주", "1.50"), ("우선주", "1.90")]), "사업보고서")
    assert (s["cash_dps"], s["cash_dps_preferred"], s["cash_dps_preferred_label"]) == (1668, 1669, "우선주")
    assert (s["yield_dart"], s["yield_preferred_dart"]) == (1.5, 1.9)


def test_summary_single_unlabelled_row_is_common():
    s = build_dividend_summary(_items([("-", "500")]), "사업보고서")
    assert s["cash_dps"] == 500 and s["cash_dps_preferred"] == 0


# ── 다년 컬럼 history 경로도 같은 규칙 ─────────────────────────────────────
def test_multiyear_history_uses_the_same_share_class_rule():
    items = [
        {"category": "주당액면가액(원)", "stock_type": "-", "current": "5,000", "previous": "5,000"},
        {"category": "주당 현금배당금(원)", "stock_type": "보통주", "current": "3,980", "previous": "2,650"},
        {"category": "주당 현금배당금(원)", "stock_type": "1종 종류주식", "current": "4,042", "previous": "2,700"},
        {"category": "현금배당수익률(%)", "stock_type": "보통주", "current": "5.7", "previous": "4.0"},
        {"category": "현금배당수익률(%)", "stock_type": "1종 종류주식", "current": "7.2", "previous": "5.0"},
        {"category": "(연결)현금배당성향(%)", "stock_type": "-", "current": "22.4", "previous": "20.0"},
        {"category": "현금배당금총액(백만원)", "stock_type": "-", "current": "232,767", "previous": "150,000"},
    ]
    out = _alot_multiyear_summaries({"items": items, "stlm_dt": "2024-12-31"})
    assert out[2024]["cash_dps"] == 3980 and out[2024]["cash_dps_preferred"] == 4042
    assert out[2023]["cash_dps"] == 2650 and out[2023]["cash_dps_preferred"] == 2700
    assert out[2024]["yield_dart"] == 5.7 and out[2024]["yield_preferred_dart"] == 7.2
    assert out[2024]["cash_dps_preferred_label"] == "1종 종류주식"


# ── 렌더 라벨 — 주당값은 종류별, 총액·배당성향은 전 종류 합산 ───────────────
def test_render_labels_share_class_and_company_wide_bases():
    payload = {"status": "ok", "subject": "테스트", "warnings": [], "data": {
        "canonical_name": "테스트", "stock_code": "071050",
        "summary": {"fiscal_year": 2024, "cash_dps": 3980, "cash_dps_preferred": 4042,
                    "cash_dps_preferred_label": "1종 종류주식", "total_amount_mil": 232_767,
                    "payout_ratio_dart": 22.4, "yield_dart": 5.7, "yield_preferred_dart": 7.2, "items": []},
        "latest_decisions": [], "policy_signals": {}}}
    md = _render(payload, "summary")
    assert "- 연간 DPS(보통주): 3,980원" in md
    assert "- 연간 DPS(1종 종류주식): 4,042원" in md
    assert "전 종류 합산 신고총액" in md
    assert "시가배당률(보통주): 5.7% · (1종 종류주식) 7.2%" in md
