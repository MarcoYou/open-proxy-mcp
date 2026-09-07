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
    # 260907: 결산월은 회사 정보로 준다. 3월 결산이라고 알려 주면 2025.04~06 은 2026 사업연도 1분기.
    got = _period_metadata({"start": "2025-04-01", "end": "2025-06-30"}, fiscal_end_month=3)
    assert (got["fiscal_year"], got["fiscal_year_end_month"], got["fiscal_quarter"], got["period_kind"]) == (2026, 3, 1, "quarter")
    assert got["fiscal_year_end_month_source"] == "company" and got["comparison_basis"] == "전년동기 대비" and got["cumulative"] is False
    # 결산월을 모르면 시작월에서 추정하지 않는다 — 12월 결산 기본값으로 2025 사업연도 2분기, 출처 default
    got = _period_metadata({"start": "2025-04-01", "end": "2025-06-30"})
    assert (got["fiscal_year"], got["fiscal_year_end_month"], got["fiscal_quarter"], got["fiscal_year_end_month_source"]) == (2025, 12, 2, "default")


def test_period_metadata_quarters_follow_the_period_end_not_the_start():
    """삼성전자 2025.07~09 실적이 「2026 사업연도 1분기 · 6월 결산」으로 나가던 결함(260907)."""
    got = _period_metadata({"start": "2025-07-01", "end": "2025-09-30"}, fiscal_end_month=12)
    assert (got["fiscal_year"], got["fiscal_quarter"]) == (2025, 3)
    got = _period_metadata({"start": "2026-04-01", "end": "2026-06-30"}, fiscal_end_month=12)
    assert (got["fiscal_year"], got["fiscal_quarter"]) == (2026, 2)
    half = _period_metadata({"start": "2025-01-01", "end": "2025-06-30"}, fiscal_end_month=12)   # 반기 누적
    assert (half["fiscal_quarter"], half["cumulative"], half["period_kind"]) == (2, True, "quarter")
    month = _period_metadata({"start": "2026-04-01", "end": "2026-04-30"}, fiscal_end_month=12)  # 현대차 월별 판매실적
    assert (month["period_kind"], month["period_month"], month["fiscal_quarter"], month["comparison_basis"]) == ("month", 4, 2, "전년동월 대비")
    annual = _period_metadata({"start": "2025-07-01", "end": "2026-06-30"})                    # 연간은 끝 달이 결산월
    assert (annual["fiscal_year"], annual["fiscal_year_end_month"], annual["fiscal_year_end_month_source"], annual["period_kind"]) == (2026, 6, "period", "annual")


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


def test_period_from_quarter_text_when_filing_has_no_date_range():
    """2025.07 삼성전자·LG전자 잠정실적엔 실적기간 표기가 없고 「2025년 2분기」 문구만 있다(260907)."""
    from open_proxy_mcp.services.fiscal_period import period_from_quarter_text
    got = period_from_quarter_text("3. 정정사유 2025년 2분기 연결재무제표 기준 영업(잠정)실적")
    assert got == {"start": "2025-04-01", "end": "2025-06-30", "source": "quarter_text"}
    assert period_from_quarter_text("행사명 2025년 반기 실적설명회")["start"] == "2025-01-01"
    assert period_from_quarter_text("행사명 2025년 반기 실적설명회")["end"] == "2025-06-30"
    # 3월 결산이면 2025 사업연도 1분기는 2024-04~06
    assert period_from_quarter_text("2025년 1분기 실적", fiscal_end_month=3) == {"start": "2024-04-01", "end": "2024-06-30", "source": "quarter_text"}
    assert period_from_quarter_text("정보제공 2025년 7월 31일") is None
    md = _period_metadata(got, fiscal_end_month=12)
    assert (md["fiscal_year"], md["fiscal_quarter"], md["period_kind"]) == (2025, 2, "quarter")


def test_candidates_dedupe_and_push_attachment_only_corrections_last():
    import asyncio
    from open_proxy_mcp.services.provisional_earnings import _find_provisional_candidates

    class _C:
        async def search_filings(self, **kw):
            return {"list": [
                {"rcept_no": "3", "rcept_dt": "20250725", "report_nm": "[첨부정정]연결재무제표기준영업(잠정)실적(공정공시)"},
                {"rcept_no": "2", "rcept_dt": "20250725", "report_nm": "[기재정정]연결재무제표기준영업(잠정)실적(공정공시)"},
                {"rcept_no": "1", "rcept_dt": "20250707", "report_nm": "연결재무제표기준영업(잠정)실적(공정공시)"},
            ]}

    got = asyncio.run(_find_provisional_candidates(_C(), "00000000", "20250701", "20250815"))
    assert [x["rcept_no"] for x in got] == ["2", "1", "3"]

