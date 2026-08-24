from open_proxy_mcp.services.provisional_earnings import parse_provisional_earnings


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
  <tr><td>- 당기순이익</td><td>7,427,504</td><td>-1,295,107</td><td>8,722,612</td><td>-</td><td>흑자전환</td></tr>
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
    assert parsed["headline"]["revenue"]["value_krw"] == 177_803_500_000
    assert parsed["headline"]["operating_profit"]["yoy_pct"] == 26.7
    assert parsed["headline"]["net_income"]["turnover"] == "흑자전환"
