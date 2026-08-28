from open_proxy_mcp.services.provisional_earnings import parse_provisional_earnings, _period_metadata


STRUCTURE_CHANGE_HTML = """
<table>
  <tr><td>2. 결산기간</td><td>당해사업연도</td></tr>
  <tr><td>- 시작일</td><td>2025-07-01</td></tr>
  <tr><td>- 종료일</td><td>2026-06-30</td></tr>
  <tr><td>3. 매출액 또는 손익구조 변동내용(단위:천원)</td>
      <td>당해사업연도</td><td>직전사업연도</td><td>증감금액</td><td>증감비율(%)</td>
      <td>흑자적자전환여부</td></tr>
  <tr><td>- 매출액</td><td>177,803,500</td><td>166,650,466</td><td>11,153,035</td><td>6.7</td><td>-</td></tr>
  <tr><td>- 영업이익</td><td>8,415,374</td><td>6,641,339</td><td>1,774,035</td><td>26.7</td><td>-</td></tr>
  <tr><td>- 법인세차감전이익</td><td>10,200,000</td><td>8,000,000</td><td>2,200,000</td><td>27.5</td><td>-</td></tr>
  <tr><td>- 당기순이익</td><td>7,427,504</td><td>-1,295,107</td><td>8,722,612</td><td>-</td><td>흑자전환</td></tr>
  <tr><td>- 자본금</td><td>30,695,000</td><td>19,195,000</td><td>11,500,000</td><td>60.0</td><td>-</td></tr>
</table>
"""

CORRECTION_HTML = """
<table>
  <tr><td>실적기간</td><td>2026-04-01 ~ 2026-06-30</td></tr>
  <tr><td>구분</td><td>정정전</td><td>정정후</td></tr>
  <tr><td>매출액</td><td>170,000,000</td><td>171,500,000</td></tr>
  <tr><td>영업이익</td><td>88,000,000</td><td>89,490,000</td></tr>
</table>
"""


def test_parse_i001_structure_change_table():
    parsed = parse_provisional_earnings(
        STRUCTURE_CHANGE_HTML,
        "매출액또는손익구조30%(대규모법인은15%)이상변경",
    )

    assert parsed["provisional_type"] == "fiscal_year_change"
    assert parsed["unit_raw"] == "천원"
    assert parsed["period"] == {"start": "2025-07-01", "end": "2026-06-30"}
    assert parsed["fiscal_year"] == 2026
    assert parsed["fiscal_year_end_month"] == 6
    assert parsed["period_kind"] == "annual"
    assert parsed["comparison_basis"] == "직전사업연도 대비"
    assert parsed["headline"]["revenue"]["value_krw"] == 177_803_500_000
    assert parsed["headline"]["operating_profit"]["yoy_pct"] == 26.7
    assert parsed["headline"]["pretax_profit"]["value_krw"] == 10_200_000_000
    assert parsed["headline"]["capital_stock"]["prior_value_krw"] == 19_195_000_000
    assert parsed["headline"]["net_income"]["turnover"] == "흑자전환"


def test_period_metadata_handles_march_year_end_quarter():
    got = _period_metadata({"start": "2025-04-01", "end": "2025-06-30"})
    assert got == {
        "fiscal_year": 2026,
        "fiscal_year_end_month": 3,
        "period_kind": "quarter",
        "fiscal_quarter": 1,
        "comparison_basis": "전년동기 대비",
    }


def test_period_metadata_handles_december_year_end_quarter():
    got = _period_metadata({"start": "2026-01-01", "end": "2026-03-31"})
    assert got["fiscal_year"] == 2026
    assert got["fiscal_year_end_month"] == 12
    assert got["fiscal_quarter"] == 1


def test_screener_earnings_keeps_fiscal_metadata():
    from open_proxy_mcp.services.screener import _extract_earnings

    fields = _extract_earnings({"data": {
        "headline": {"revenue": {"value_krw": 100, "yoy_pct": 2.0}},
        "kind": "financial", "consolidated": True,
        "provisional_type": "fiscal_year_change",
        "period": {"start": "2025-07-01", "end": "2026-06-30"},
        "fiscal_year": 2026, "period_kind": "annual",
        "comparison_basis": "직전사업연도 대비",
    }}, "20260101000000")
    assert fields["fiscal_year"] == 2026
    assert fields["period_kind"] == "annual"
    assert fields["comparison_basis"] == "직전사업연도 대비"


def test_parse_i002_correction_prefers_corrected_values():
    parsed = parse_provisional_earnings(CORRECTION_HTML, "[기재정정] 영업(잠정)실적")
    assert parsed["correction"] is True
    assert parsed["headline"]["revenue"]["value_krw"] == 171_500_000_000_000
    assert parsed["headline"]["operating_profit"]["value_krw"] == 89_490_000_000_000


# 「4. 재무현황」 절에는 증감금액·증감비율 열이 없다(당해/직전만 colspan 으로 반복).
# 예전 위치 기반 파싱은 자본금 액수를 증감비율로 읽어 +13660984500.0% 를 찍었다.
FINANCIAL_STATUS_HTML = """
<table>
  <tr><td>2. 결산기간</td><td>당해사업연도</td><td>당해사업연도</td><td>직전사업연도</td><td>직전사업연도</td><td>직전사업연도</td></tr>
  <tr><td>- 시작일</td><td>2025-07-01</td><td>2025-07-01</td><td>2024-07-01</td><td>2024-07-01</td><td>2024-07-01</td></tr>
  <tr><td>- 종료일</td><td>2026-06-30</td><td>2026-06-30</td><td>2025-06-30</td><td>2025-06-30</td><td>2025-06-30</td></tr>
  <tr><td>3. 매출액 또는 손익구조변동내용(단위: 원)</td><td>당해사업연도</td><td>직전사업연도</td>
      <td>증감금액</td><td>증감비율(%)</td><td>흑자적자전환여부</td></tr>
  <tr><td>- 매출액</td><td>35,517,233,415</td><td>32,888,839,235</td><td>2,628,394,180</td><td>7.99</td><td>-</td></tr>
  <tr><td>- 영업이익</td><td>5,813,056,155</td><td>4,399,159,063</td><td>1,413,897,092</td><td>32.14</td><td>-</td></tr>
  <tr><td>4. 재무현황(단위 : 원)</td><td>당해사업연도</td><td>당해사업연도</td><td>직전사업연도</td><td>직전사업연도</td><td>직전사업연도</td></tr>
  <tr><td>- 자본금</td><td>13,660,984,500</td><td>13,660,984,500</td><td>13,660,984,500</td><td>13,660,984,500</td><td>13,660,984,500</td></tr>
</table>
"""


def test_financial_status_section_does_not_read_amount_as_percent():
    parsed = parse_provisional_earnings(
        FINANCIAL_STATUS_HTML,
        "매출액또는손익구조30%(대규모법인은15%)이상변동",
    )
    capital = parsed["headline"]["capital_stock"]
    assert capital["value_krw"] == 13_660_984_500
    assert capital["prior_value_krw"] == 13_660_984_500
    # 원문에 증감비율 열이 없다 → 계산값 0.0%, 그리고 계산했다고 표시한다.
    assert capital["yoy_pct"] == 0.0
    assert capital["yoy_basis"] == "computed"
    # 3절은 원문 증감비율을 그대로 쓴다.
    assert parsed["headline"]["revenue"]["yoy_pct"] == 7.99
    assert parsed["headline"]["revenue"]["yoy_basis"] == "filing"


def test_financial_status_capital_reduction_ratio_is_negative():
    """감자 뒤 자본금 급감(하이퍼코퍼레이션 표본) — 액수가 아니라 감소율이 나와야 한다."""
    html = FINANCIAL_STATUS_HTML.replace(
        "<tr><td>- 자본금</td><td>13,660,984,500</td><td>13,660,984,500</td>"
        "<td>13,660,984,500</td><td>13,660,984,500</td><td>13,660,984,500</td></tr>",
        "<tr><td>- 자본금</td><td>6,667,608,000</td><td>6,667,608,000</td>"
        "<td>53,340,865,500</td><td>53,340,865,500</td><td>53,340,865,500</td></tr>",
    )
    capital = parse_provisional_earnings(html, "매출액또는손익구조30%(대규모법인은15%)이상변동")["headline"]["capital_stock"]
    assert capital["yoy_pct"] == -87.5
    assert capital["yoy_basis"] == "computed"


def test_blank_ratio_cell_is_not_backfilled():
    """증감비율 열은 있는데 '-'(적자전환)이면 회사가 비워둔 것 — 계산해 채우지 않는다."""
    html = FINANCIAL_STATUS_HTML.replace(
        "<td>- 영업이익</td><td>5,813,056,155</td><td>4,399,159,063</td><td>1,413,897,092</td><td>32.14</td>",
        "<td>- 영업이익</td><td>5,813,056,155</td><td>-4,399,159,063</td><td>10,212,215,218</td><td>-</td>",
    )
    op = parse_provisional_earnings(html, "매출액또는손익구조30%(대규모법인은15%)이상변동")["headline"]["operating_profit"]
    assert op["yoy_pct"] is None
    assert op["yoy_basis"] is None
