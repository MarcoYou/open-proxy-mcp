from __future__ import annotations

from open_proxy_mcp.services.biz_fields import (
    build_region_index,
    extract_backlog,
    extract_customers,
    extract_rnd,
    extract_sites,
    extract_utilization,
    render_candidate_context,
    render_biz_subsection_markdown,
)
from open_proxy_mcp.services.business_details import _biz_html_region, build_business_details_payload
from open_proxy_mcp.tools.business_details import _render


def _table(label: str, value: str) -> str:
    return (
        '<TABLE BORDER="1"><TR><TD>구분</TD><TD>금액</TD></TR>'
        f'<TR><TD>{label}</TD><TD>{value}</TD></TR></TABLE>'
    )


def _document(body: str) -> str:
    return (
        '<SECTION-1><P USERMARK="F-16 B">II. 사업의 내용</P>'
        f'<SECTION-2>{body}</SECTION-2>'
        '<P USERMARK="F-16 B">III. 재무에 관한 사항</P></SECTION-1>'
    )


def test_backlog_stops_at_next_peer_heading() -> None:
    html = _document(
        '<P USERMARK="F-14 B">4. 매출 및 수주상황</P>'
        '<P USERMARK="F-14 B">가. 매출실적</P>'
        + _table("매출액", "10000")
        + '<P USERMARK="F-14 B">나. 수주상황</P>'
        + _table("수주잔고", "25000")
        + '<P>※ 수주잔고 = 기초잔고 + 신규계약 - 기납품액</P>'
        + '<P USERMARK="F-14 B">다. 시장위험</P>'
        + _table("위험금액", "99999")
    )

    result = extract_backlog("", html, build_region_index(html))

    assert result["status"] == "MARKDOWN"
    assert "수주잔고" in result["markdown"]
    assert "기초잔고 + 신규계약" in result["markdown"]
    assert "시장위험" not in result["markdown"]
    assert "99999" not in result["markdown"]
    assert result["section_source"]["boundary_methods"] == ["peer_heading"]


def test_candidate_context_is_separate_fixed_window_for_a_failed_strict_anchor() -> None:
    html = _document(
        '<P USERMARK="F-14 B">4. 매출 및 수주상황</P>'
        '<P USERMARK="F-14 B">가. 매출실적</P>'
        + _table("매출액", "10000")
        + '<P USERMARK="F-14 B">나. 수주상황</P>'
        '<P>수주 금액의 개념은 없으며 주문형 판매로 운영합니다.</P>'
        + '<P USERMARK="F-14 B">다. 시장위험</P>'
        + _table("위험금액", "99999")
    )

    candidate = render_candidate_context("backlog", html, 1_000, build_region_index(html))

    assert candidate is not None
    assert candidate["status"] == "LOW_CONFIDENCE"
    assert candidate["selection_method"] == "fixed_window_heading"
    assert candidate["context_chars"] == 1_000
    assert "수주상황" in candidate["anchor"]
    assert "99999" in candidate["markdown"]
    assert "공식 추출 결과" in candidate["warning"]


def test_candidate_mode_requires_one_standard_field_before_any_network_work() -> None:
    import asyncio

    result = asyncio.run(
        build_business_details_payload(
            "삼성전자", fields=["backlog", "rnd"], context_mode="candidate"
        )
    )

    assert result["status"] == "error"
    assert "하나의 fields" in result["warnings"][0]


def test_candidate_mode_rejects_oversized_window_before_any_network_work() -> None:
    import asyncio

    result = asyncio.run(
        build_business_details_payload(
            "삼성전자", fields=["backlog"], context_mode="candidate", context_chars=60_001
        )
    )

    assert result["status"] == "error"
    assert "1~60000" in result["warnings"][0]


def test_nested_heading_is_included_until_parent_peer() -> None:
    html = _document(
        '<P USERMARK="F-14 B">3. 원재료 및 생산설비</P>'
        '<P USERMARK="F-14 B">가. 생산능력 및 가동률</P>'
        '<P USERMARK="F-12 B">(1) 생산능력</P>'
        + _table("생산능력", "30000")
        + '<P USERMARK="F-12 B">(2) 가동률</P>'
        + _table("평균가동률", "78.8%")
        + '<P USERMARK="F-14 B">나. 생산설비 현황</P>'
        + _table("설비장부가", "88888")
    )

    md = render_biz_subsection_markdown(
        html, [r"생산능력\s*및\s*가동률"], content_re=None,
        region_index=build_region_index(html),
    )

    assert md is not None
    assert "생산능력" in md
    assert "평균가동률" in md
    assert "생산설비 현황" not in md
    assert "88888" not in md


def test_utilization_can_use_broader_production_capacity_heading() -> None:
    html = _document(
        '<P USERMARK="F-14 B">나. 생산능력</P>'
        '<P>사업부문별 가동률은 다음과 같습니다.</P>'
        + _table("평균가동률 82.8%", "20000")
        + '<P USERMARK="F-14 B">다. 생산설비 및 투자현황</P>'
        + _table("설비장부가", "99999")
    )

    result = extract_utilization("", html, build_region_index(html))

    assert result["status"] == "MARKDOWN"
    assert "82.8%" in result["markdown"]
    assert "99999" not in result["markdown"]


def test_utilization_returns_production_results_without_utilization_word() -> None:
    html = _document(
        '<P USERMARK="F-14 B">나. 생산 및 설비에 관한 사항</P>'
        '<P USERMARK="F-12 B">(1) 생산실적</P>'
        + _table("완제품 생산실적(톤)", "335852")
        + '<P USERMARK="F-12 B">(2) 생산설비 현황</P>'
        + _table("설비장부가", "99999")
    )

    result = extract_utilization("", html, build_region_index(html))

    assert result["extraction_status"] == "SUCCESS"
    assert "335852" in result["markdown"]


def test_utilization_accepts_bare_production_facilities_parent() -> None:
    html = _document(
        '<P USERMARK="F-14 B">2. 생산설비</P>'
        '<P USERMARK="F-12 B">1) 생산능력 및 산출근거</P>'
        + _table("표준 생산능력(D/M)", "359")
        + '<P USERMARK="F-12 B">2) 생산실적</P>'
        + _table("누적 생산실적(D/M)", "122158")
    )

    result = extract_utilization("", html, build_region_index(html))

    assert result["extraction_status"] == "SUCCESS"
    assert "122158" in result["markdown"]


def test_utilization_distinguishes_explicit_production_na() -> None:
    html = _document(
        '<P USERMARK="F-14 B">나. 생산능력 및 생산실적</P>'
        '<P>제품을 전량 외주생산하므로 생산능력 및 생산실적은 해당사항이 없습니다.</P>'
    )

    result = extract_utilization("", html, build_region_index(html))

    assert result["extraction_status"] == "NOT_APPLICABLE"


def test_utilization_does_not_use_production_capacity_inside_sales_strategy() -> None:
    html = _document(
        '<P USERMARK="F-14 B">(가) OEM 품목 - 생산능력 및 전문성 확대</P>'
        '<P>고객사 적기공급 및 품질확보를 추진합니다.</P>'
    )

    result = extract_utilization("", html, build_region_index(html))

    assert result["extraction_status"] == "NOT_COLLECTED"


def test_biz_html_region_prefers_real_title_over_long_cross_reference_span() -> None:
    toc = '<P>II. 사업의 내용을 참조하시기 바랍니다.</P>' + ('<P>회사개요</P>' * 220)
    body = (
        '<TITLE>II. 사업의 내용</TITLE>'
        '<P USERMARK="B">3. 원재료 및 생산설비</P>'
        + ('<P>실제 사업 본문 생산능력 10000</P>' * 100)
        + '<TITLE>III. 재무에 관한 사항</TITLE>'
    )
    html = toc + '<P>III. 재무에 관한 사항</P>' + body

    region = _biz_html_region(html)

    assert region.startswith('<TITLE>II. 사업의 내용</TITLE>')
    assert "실제 사업 본문 생산능력" in region
    assert "회사개요" not in region


def test_sites_supports_production_facilities_variant() -> None:
    html = _document(
        '<P USERMARK="B">나. 생산 설비 등</P>'
        '<P>충남 아산 본사 및 3개의 사업장을 운영하고 있습니다.</P>'
    )

    result = extract_sites("", html, build_region_index(html))

    assert result["extraction_status"] == "SUCCESS"


def test_sites_supports_warehouse_holdings_heading() -> None:
    html = _document(
        '<P USERMARK="B">나. 창고보유현황</P>'
        + _table("영일만신항 창고", "경북 포항시")
    )

    result = extract_sites("", html, build_region_index(html))

    assert result["extraction_status"] == "SUCCESS"


def test_sites_recovers_strong_production_base_statement() -> None:
    html = _document(
        '<P>당사는 경기도 이천시에 위치한 본사를 거점으로 4개의 생산기지를 운영합니다.</P>'
    )

    result = extract_sites("", html, build_region_index(html))

    assert result["extraction_status"] == "SUCCESS"
    assert result["section_source"]["selection_method"] == "signal_paragraph"


def test_sites_recognizes_explicit_no_production_facilities() -> None:
    text = "물리적인 형태의 제품이 없어 생산설비가 존재하지 않습니다."
    html = _document(f"<P>{text}</P>")

    result = extract_sites(text, html, build_region_index(html))

    assert result["extraction_status"] == "NOT_APPLICABLE"


def test_sites_uses_production_table_only_with_named_location() -> None:
    html = _document(
        '<P USERMARK="B">마. 생산실적 및 가동률</P>'
        + _table("부산공장", "컬러강판 100")
    )

    result = extract_sites("", html, build_region_index(html))

    assert result["extraction_status"] == "SUCCESS"


def test_sites_rejects_locationless_production_table() -> None:
    html = _document(
        '<P USERMARK="B">마. 생산실적 및 가동률</P>'
        + _table("사업소(사업부문)", "자동차시트 100")
    )

    result = extract_sites("", html, build_region_index(html))

    assert result["extraction_status"] == "NOT_COLLECTED"


def test_rnd_explicit_na_can_follow_heading_on_next_line() -> None:
    html = _document('<TITLE>6. 주요계약 및 연구개발활동</TITLE><P>해당사항 없음</P>')

    result = extract_rnd("6. 주요계약 및 연구개발활동\n해당사항 없음", html)

    assert result["extraction_status"] == "NOT_APPLICABLE"


def test_rnd_strong_company_action_is_recovered_as_one_paragraph() -> None:
    html = _document(
        '<P>당사는 테크기반 미래성장 영역에서 미래형 서비스 연구개발을 집중적으로 추진하고 있습니다.</P>'
        '<P>업계 전반의 연구개발 투자는 증가하는 추세입니다.</P>'
    )

    result = extract_rnd("", html, build_region_index(html))

    assert result["extraction_status"] == "SUCCESS"
    assert result["section_source"]["selection_method"] == "signal_paragraph"
    assert "업계 전반" not in result["markdown"]


def test_backlog_supports_orders_about_heading() -> None:
    html = _document(
        '<P USERMARK="B">4-2. 수주에 관한 사항</P>'
        '<P>현대제철 판매계약 외 다수이며 정확한 수주액의 개념은 없습니다.</P>'
    )

    result = extract_backlog("", html, build_region_index(html))

    assert result["extraction_status"] == "SUCCESS"
    assert "현대제철" in result["markdown"]


def test_backlog_recovers_strong_future_delivery_statement() -> None:
    html = _document(
        '<P>당사가 기술이전을 통해 수주하게 되었으며 2026년까지 납품을 완료할 계획입니다.</P>'
    )

    result = extract_backlog("", html, build_region_index(html))

    assert result["extraction_status"] == "SUCCESS"
    assert result["section_source"]["selection_method"] == "signal_paragraph"


def test_backlog_recovers_current_supply_contract_statement() -> None:
    html = _document(
        '<P>상해GM과 동펑푸조시트로엥에 수주계약을 체결하고 제품을 공급하고 있습니다.</P>'
    )

    result = extract_backlog("", html, build_region_index(html))

    assert result["extraction_status"] == "SUCCESS"
    assert result["section_source"]["selection_method"] == "signal_paragraph"


def test_backlog_explicit_na_supports_orders_matters_heading() -> None:
    html = _document('<P USERMARK="B">라. 수주사항</P><P>-해당사항없음.</P>')

    result = extract_backlog("라. 수주사항\n-해당사항없음.", html, build_region_index(html))

    assert result["extraction_status"] == "NOT_APPLICABLE"


def test_backlog_does_not_treat_minority_shareholder_rights_as_na() -> None:
    text = "다. 소수주주권\n당사는 소수주주권이 행사된 경우가 없습니다."
    html = _document(f"<P>{text}</P>")

    result = extract_backlog(text, html, build_region_index(html))

    assert result["extraction_status"] == "NOT_COLLECTED"


def test_customers_supports_major_sales_destination_heading() -> None:
    html = _document(
        '<P USERMARK="B">다. 주요판매처</P>'
        + _table("A사 매출비중", "16.66%")
    )

    result = extract_customers("", html, build_region_index(html))

    assert result["extraction_status"] == "SUCCESS"


def test_customers_uses_financial_channel_fallback() -> None:
    html = _document(
        '<P USERMARK="B">가. 영업개황</P>'
        '<P>모집 경로는 임직원, 설계사, 대리점 및 방카슈랑스입니다.</P>'
    )

    result = extract_customers("", html, build_region_index(html))

    assert result["extraction_status"] == "SUCCESS"


def test_customers_uses_broad_heading_only_with_strong_signal() -> None:
    html = _document(
        '<TITLE>2. 주요 제품 및 서비스</TITLE>'
        '<P>주요 고객 정보는 국내 건설사 A사와 석유화학사 B사이며 직접 계약합니다.</P>'
        '<TITLE>3. 원재료 및 생산설비</TITLE>'
    )

    result = extract_customers("", html, build_region_index(html))

    assert result["extraction_status"] == "SUCCESS"


def test_customers_can_reuse_order_table_with_orderer_column() -> None:
    html = _document(
        '<P USERMARK="B">다. 수주상황</P>'
        + _table("발주처 S-Oil", "25000")
    )

    result = extract_customers("", html, build_region_index(html))

    assert result["extraction_status"] == "SUCCESS"


def test_customers_recovers_specific_customer_reference_paragraph() -> None:
    html = _document(
        '<P>20년간 1,000여 고객사와 3,000여 프로젝트를 수행한 레퍼런스를 보유합니다.</P>'
    )

    result = extract_customers("", html, build_region_index(html))

    assert result["extraction_status"] == "SUCCESS"
    assert result["section_source"]["selection_method"] == "signal_paragraph"


def test_cross_reference_and_toc_are_not_selected() -> None:
    html = (
        '<TABLE><TR><TD>4. 매출 및 수주상황 ---------------- 25</TD></TR></TABLE>'
        '<P>자세한 내용은 4. 매출 및 수주상황을 참고하시기 바랍니다.</P>'
        + _document(
            '<P USERMARK="F-14 B">4. 매출 및 수주상황</P>'
            '<P USERMARK="F-14 B">가. 수주상황</P>'
            + _table("수주잔고", "12345")
            + '<P USERMARK="F-14 B">나. 위험관리</P>'
            + _table("위험", "99999")
        )
    )

    result = extract_backlog("", html, build_region_index(html))

    assert "12345" in result["markdown"]
    assert "99999" not in result["markdown"]
    assert "참고하시기 바랍니다" not in result["markdown"]


def test_prose_only_backlog_is_preserved() -> None:
    html = _document(
        '<P USERMARK="F-14 B">나. 수주상황</P>'
        '<P>당사는 주요 고객사와 월별 및 분기별로 공급 물량과 가격을 합의합니다.</P>'
        '<P USERMARK="F-14 B">다. 시장위험</P>'
        + _table("위험", "99999")
    )

    result = extract_backlog("", html, build_region_index(html))

    assert result["status"] == "MARKDOWN"
    assert "공급 물량과 가격" in result["markdown"]
    assert "시장위험" not in result["markdown"]


def test_inline_prose_after_bold_heading_span_is_preserved() -> None:
    html = _document(
        '<P><SPAN USERMARK="F-14 B">나. 수주상황</SPAN>'
        ' 당사는 고객사와 월별 및 분기별로 공급 물량과 가격을 합의합니다.</P>'
        '<P USERMARK="F-14 B">다. 시장위험</P>'
        + _table("위험", "99999")
    )

    result = extract_backlog("", html, build_region_index(html))

    assert result["status"] == "MARKDOWN"
    assert "공급 물량과 가격" in result["markdown"]
    assert "시장위험" not in result["markdown"]


def test_long_inline_prose_uses_leading_bold_span_as_heading() -> None:
    prose = "주요 고객사에 직접 판매하고 해외 법인을 통한 판매도 병행합니다. " * 8
    html = _document(
        '<P><SPAN USERMARK="B">나. 판매경로 및 판매방법 등</SPAN>'
        + prose
        + '</P><P><SPAN USERMARK="B">다. 수주상황</SPAN>'
        '해당사항 없습니다.</P>'
    )

    index = build_region_index(html)
    md = render_biz_subsection_markdown(
        html,
        [r"판매\s*경로(?:\s*및\s*판매\s*방법)?"],
        content_re=None,
        region_index=index,
    )

    assert md is not None
    assert prose.strip() in md
    assert "수주상황" not in md


def test_long_unstyled_marker_led_paragraph_is_indexed() -> None:
    prose = "당사의 주요 매출처는 제조업체이며 고객사에 직접 납품합니다. " * 8
    html = _document(
        '<P>나. 판매경로 및 방법' + prose + '</P>'
        '<P>다. 수주상황 해당사항 없습니다.</P>'
    )

    result = extract_customers("", html, build_region_index(html))

    assert result["status"] == "MARKDOWN"
    assert prose.strip() in result["markdown"]
    assert "수주상황" not in result["markdown"]


def test_heading_after_short_leading_note_is_indexed() -> None:
    html = _document(
        '<P>(*) 연결 편입 이후 실적입니다. 나. 판매경로 주요 고객에게 직접 판매합니다.</P>'
        '<P>다. 판매전략 신규 거래처를 확대합니다.</P>'
    )

    result = extract_customers("", html, build_region_index(html))

    assert result["status"] == "MARKDOWN"
    assert "주요 고객에게 직접 판매" in result["markdown"]
    assert "연결 편입 이후" not in result["markdown"]
    assert "신규 거래처" not in result["markdown"]


def test_heading_marker_fused_to_previous_prose_is_indexed() -> None:
    html = _document(
        '<P>신규 판매처를 발굴마. 주요매출처 주요 고객은 제조업체입니다.</P>'
        '<P>바. 수주상황 해당사항 없습니다.</P>'
    )

    result = extract_customers("", html, build_region_index(html))

    assert result["status"] == "MARKDOWN"
    assert "주요 고객은 제조업체" in result["markdown"]
    assert "신규 판매처를 발굴" not in result["markdown"]
    assert "수주상황" not in result["markdown"]


def test_customer_field_accepts_sales_method_parent_heading() -> None:
    html = _document(
        '<P USERMARK="B">나. 판매방법 및 조건</P>'
        '<P>(1) 판매조직은 제품군별 영업팀으로 구성됩니다.</P>'
        '<P>(2) 판매경로는 고객사 직접 납품 방식입니다.</P>'
        '<P USERMARK="B">다. 수주상황</P>'
        '<P>해당사항 없습니다.</P>'
    )

    result = extract_customers("", html, build_region_index(html))

    assert result["status"] == "MARKDOWN"
    assert "고객사 직접 납품" in result["markdown"]
    assert "수주상황" not in result["markdown"]


def test_inverted_marker_depth_recovers_to_section_boundary() -> None:
    html = _document(
        '<P USERMARK="B">(3) 판매경로 및 판매방법 등</P>'
        '<P USERMARK="B">가. 판매품목 및 판매처</P>'
        + _table("주요 고객", "25000")
        + '<P USERMARK="B">나. 판매방법</P><P>직접 판매합니다.</P>'
    )

    result = extract_customers("", html, build_region_index(html))

    assert result["status"] == "MARKDOWN"
    assert "주요 고객" in result["markdown"]
    assert result["section_source"]["boundary_methods"] == ["section_end_recovery"]
    assert len(result["section_source"]["matched_headings"]) == 1
    assert result["section_source"]["matched_headings"][0].startswith("(3)")


def test_inverted_marker_depth_recovery_stops_at_next_top_level_heading() -> None:
    html = _document(
        '<P USERMARK="B">6. 주요계약 및 연구개발활동</P>'
        '<P USERMARK="B">(3) 연구개발실적</P>'
        '<P USERMARK="B">1) SK텔레콤</P>'
        + _table("연구개발 과제", "25000")
        + '<P USERMARK="B">7. 기타 참고사항</P>'
        + _table("위험", "99999")
    )

    result = extract_rnd("", html, build_region_index(html))

    assert result["status"] == "MARKDOWN"
    assert "25000" in result["markdown"]
    assert "99999" not in result["markdown"]
    assert result["section_source"]["boundary_methods"] == ["top_level_recovery"]


def test_adjacent_duplicate_heading_does_not_truncate_region() -> None:
    html = _document(
        '<SPAN USERMARK="B">마. 생산설비의 현황</SPAN>'
        '<P USERMARK="B">마. 생산설비의 현황 연결회사의 주요 사업장은 다음과 같습니다.</P>'
        + _table("서울 사업장", "25000")
        + '<P USERMARK="B">바. 집행중인 투자</P>'
        + _table("투자", "99999")
    )

    result = extract_sites("", html, build_region_index(html))

    assert result["status"] == "MARKDOWN"
    assert "서울 사업장" in result["markdown"]
    assert "99999" not in result["markdown"]
    assert result["section_source"]["boundary_methods"] == ["peer_heading"]


def test_flattened_table_of_contents_is_not_indexed() -> None:
    html = _document(
        '<P USERMARK="B">1. 사업의 개요2. 주요 제품 및 서비스'
        '3. 원재료 및 생산설비4. 매출 및 수주상황'
        '5. 위험관리 및 파생거래6. 주요계약 및 연구개발활동'
        '7. 기타 참고사항</P>'
        '<P USERMARK="B">다. 수주상황</P>'
        + _table("수주잔고", "25000")
        + '<P USERMARK="B">라. 판매전략</P>'
        + _table("판매", "99999")
    )

    index = build_region_index(html)
    result = extract_backlog("", html, index)

    assert all(heading.text != "1. 사업의 개요2. 주요 제품 및 서비스" for heading in index.headings)
    assert result["section_source"]["matched_headings"] == ["다. 수주상황"]
    assert "25000" in result["markdown"]
    assert "99999" not in result["markdown"]


def test_financial_chapter_candidate_does_not_starve_business_backlog() -> None:
    html = (
        '<SECTION-1><P USERMARK="B">II. 사업의 내용</P><SECTION-2>'
        '<TITLE>4. 매출 및 수주상황</TITLE>'
        '<P USERMARK="B">다. 수주 상황</P>'
        + _table("수주잔고", "25000")
        + '</SECTION-2><P USERMARK="B">III. 재무에 관한 사항</P><SECTION-2>'
        '<P USERMARK="B">라. 수주계약 현황</P>'
        '<P USERMARK="B">(1) 진행률적용 수주계약 현황</P>'
        + _table("주석 수주잔고", "99999")
        + '</SECTION-2></SECTION-1>'
    )

    result = extract_backlog("", html, build_region_index(html))

    assert result["status"] == "MARKDOWN"
    assert "25000" in result["markdown"]
    assert "99999" not in result["markdown"]
    assert result["section_source"]["chapters"] == ["II. 사업의 내용"]


def test_title_recovery_uses_its_section_not_numbered_inner_paragraph() -> None:
    html = _document(
        '<TITLE>6. 주요계약 및 연구개발활동</TITLE>'
        '<P USERMARK="B">1. 연구개발 조직</P>'
        '<P>디지털뉴스본부에서 연구개발 업무를 담당합니다.</P>'
    )

    result = extract_rnd("", html, build_region_index(html))

    assert result["status"] == "MARKDOWN"
    assert "디지털뉴스본부" in result["markdown"]
    assert result["section_source"]["boundary_methods"] == ["section_end_recovery"]


def test_broad_title_requires_field_signal_in_section_body() -> None:
    html = _document(
        '<TITLE>6. 주요계약 및 연구개발활동</TITLE>'
        '<P>당사의 주요 임대차계약은 다음과 같습니다.</P>'
        + _table("임대차계약", "25000")
    )

    result = extract_rnd("", html, build_region_index(html))

    assert result["extraction_status"] == "NOT_COLLECTED"


def test_content_gate_failure_does_not_expand_a_nonempty_peer_region() -> None:
    html = _document(
        '<P USERMARK="B">(2) 생산설비 현황</P>'
        + _table("기계장치", "25000")
        + '<P USERMARK="B">마. 설비 투자계획</P>'
        '<P>서울 사업장에 신규 설비를 투자합니다.</P>'
    )

    result = extract_sites("", html, build_region_index(html))

    assert result["extraction_status"] == "NOT_COLLECTED"


def test_utilization_hint_is_derived_from_returned_markdown() -> None:
    html = _document(
        '<P USERMARK="F-14 B">가. 생산실적 및 가동률</P>'
        '<P>평균 가동률은 78.8%입니다.</P>'
        + _table("생산실적", "20000")
        + '<P USERMARK="F-14 B">나. 생산설비</P>'
        + _table("가동률 11.1%", "99999")
    )
    biz_text_with_unrelated_value = "다른 자회사의 평균 가동률은 99.9%입니다."

    result = extract_utilization(
        biz_text_with_unrelated_value, html, build_region_index(html),
    )

    assert result["pct_hint"] == ["78.8"]
    assert result["hints"][0]["source"] == "returned_markdown"
    assert "99.9" not in result["pct_hint"]


def test_rnd_hint_is_derived_from_returned_markdown() -> None:
    html = _document(
        '<P USERMARK="F-14 B">나. 연구개발 비용</P>'
        '<P>연구개발비 / 매출액 비율 12.4%</P>'
        + _table("연구개발비", "15000")
        + '<P USERMARK="F-14 B">다. 기타사항</P>'
        + _table("연구개발비 / 매출액 비율 77.7%", "99999")
    )

    result = extract_rnd(
        "무관한 연구개발비 / 매출액 비율 88.8%", html, build_region_index(html),
    )

    assert result["ratio_to_sales_pct_hint"] == "12.4"
    assert result["hints"][0]["source"] == "returned_markdown"


def test_anchor_miss_is_not_collected_without_breaking_legacy_status() -> None:
    html = _document(
        '<P USERMARK="F-14 B">1. 사업의 개요</P><P>서비스 사업을 영위합니다.</P>'
    )

    result = extract_backlog("", html, build_region_index(html))

    assert result["status"] == "NOT_APPLICABLE"
    assert result["extraction_status"] == "NOT_COLLECTED"
    assert result["na_reason"] == "해당 소절 미검출"


def test_explicit_negative_disclosure_is_distinguished() -> None:
    html = _document(
        '<P USERMARK="F-14 B">1. 사업의 개요</P><P>서비스 사업을 영위합니다.</P>'
    )

    result = extract_backlog("수주상황은 해당 사항이 없습니다.", html, build_region_index(html))

    assert result["status"] == "NOT_APPLICABLE"
    assert result["extraction_status"] == "NOT_APPLICABLE"


def test_explicit_negative_under_matched_heading_is_not_markdown() -> None:
    html = _document(
        '<P USERMARK="F-14 B">나. 수주상황</P>'
        '<P>당사는 수주산업이 아니므로 수주상황은 해당 사항이 없습니다.</P>'
    )

    result = extract_backlog("", html, build_region_index(html))

    assert result["status"] == "NOT_APPLICABLE"
    assert result["extraction_status"] == "NOT_APPLICABLE"
    assert "markdown" not in result


def test_backlog_excludes_financial_chapter_conflict() -> None:
    html = (
        '<SECTION-1><P USERMARK="B">II. 사업의 내용</P><SECTION-2>'
        '<P USERMARK="B">바. 수주상황</P>'
        + _table("수주잔고", "25000")
        + '</SECTION-2><P USERMARK="B">III. 재무에 관한 사항</P><SECTION-2>'
        '<P USERMARK="B">라. 수주계약 현황</P>'
        '<P>중요한 수주계약은 해당 사항이 없습니다.</P></SECTION-2></SECTION-1>'
    )

    result = extract_backlog("", html, build_region_index(html))

    assert result["status"] == "MARKDOWN"
    assert "25000" in result["markdown"]
    assert "해당 사항이 없습니다" not in result["markdown"]
    assert result["section_source"]["chapters"] == ["II. 사업의 내용"]


def test_markdown_renderer_does_not_call_not_collected_not_applicable() -> None:
    rendered = _render({
        "status": "ok",
        "subject": "테스트",
        "data": {
            "report": {},
            "backlog": {
                "status": "NOT_APPLICABLE",
                "extraction_status": "NOT_COLLECTED",
                "na_reason": "해당 소절 미검출",
            },
        },
    })

    assert "확인하지 못함" in rendered
    assert "수주현황**: 해당없음" not in rendered
