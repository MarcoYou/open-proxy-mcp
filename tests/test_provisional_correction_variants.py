"""[기재정정] 영업(잠정)실적 표의 변형 서식 — 라벨과 기간이 한 칸에 오는 두 꼴.

DART 원문 표(2026-09-06 캐시 실측, 157건 중 정정 4건이 전부 headline 이 비었다):
- 20260727800078 (억원): 「- 매출액(당해실적)」·「- 매출액(누계실적)」 행 + 정정전/정정후 두 열.
- 20260303801347 (백만원): 「매출액(당기실적)」 행 + 셀이 「당해실적: 478,413 전기대비증감율(%): …」 한 줄 요약.
DART 콜 0 — fixture 는 원문 <table> 그대로.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from open_proxy_mcp.services.provisional_earnings import parse_provisional_earnings, _label_key  # noqa: E402

FIX = Path(__file__).resolve().parent / "dart_responses" / "dart"
_NM = "[기재정정]연결재무제표기준영업(잠정)실적(공정공시)"


def _wrap(table_html: str, unit: str) -> str:
    return f"<html><body><p>단위 : {unit}, %</p>{table_html}</body></html>"


def test_dash_and_period_tag_rows_take_current_period_after_correction():
    html = _wrap((FIX / "provisional_correction_dash_period_tag_20260727800078.html").read_text(encoding="utf-8"), "억원")
    p = parse_provisional_earnings(html, _NM)
    assert p["kind"] == "financial" and p["correction"] is True
    h = p["headline"]
    assert h["revenue"] == {"value_krw": 13_937 * 1e8, "prior_value_krw": 13_000 * 1e8}      # 당해, 누계(25,387)가 아님
    assert h["operating_profit"] == {"value_krw": 4_518 * 1e8, "prior_value_krw": 4_300 * 1e8}


def test_blob_cells_read_the_number_after_current_period_marker():
    html = _wrap((FIX / "provisional_correction_blob_cells_20260303801347.html").read_text(encoding="utf-8"), "백만원")
    p = parse_provisional_earnings(html, _NM)
    assert p["kind"] == "financial"
    h = p["headline"]
    assert h["revenue"] == {"value_krw": 473_133 * 1e6, "prior_value_krw": 478_413 * 1e6}
    assert h["operating_profit"]["value_krw"] == -3_378 * 1e6
    assert h["net_income"]["value_krw"] == 2_248 * 1e6


def test_label_key_normalisation():
    assert _label_key("- 매출액(당해실적)") == ("revenue", "당해실적")
    assert _label_key("매출액(누계실적)") == ("revenue", "누계실적")
    assert _label_key(" 영업이익 (당기실적)") == ("operating_profit", "당기실적")
    assert _label_key("매출액") == ("revenue", None)
    # 공용 어휘 — 잠정실적 표에 이렇게 적는 회사가 있으면 받는다 (캐시 157건엔 없었다: 있으면 라벨은 표준)
    assert _label_key("수익(매출액)") == ("revenue", None)
    assert _label_key("보험수익") == ("revenue", None)
    assert _label_key("순영업수익") == ("revenue", None)
    # 매출이 아닌 것
    assert _label_key("매출원가") == (None, None)
    assert _label_key("기타영업수익") == (None, None)
    assert _label_key("이자수익") == (None, None)
    assert _label_key("정정항목") == (None, None)
