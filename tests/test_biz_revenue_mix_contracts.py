# -*- coding: utf-8 -*-
"""II-2-가 매출구성 · II-6-가 주요계약 회귀 테스트. network 0콜.

실측에서 실제로 깨졌던 것만 고정한다:
  ① 소절 표제와 표 표제가 형제 레벨이면 구간이 표제 직후에서 끊긴다(신풍제약 52자).
  ② 은행·보험 II-2는 매출구성이 아니라 상품 카탈로그다(KB금융지주 65,050자).
  ③ 「주요 공사 현황」(시공실적)은 매출표가 아니다(남광토건).
  ④ 「가격변동추이」는 같은 소절이어도 product_pricing 몫이다.
  ⑤ 자가검산은 표 단위로 — 구간 통째로 합산하면 항목합이 합계행의 배수가 된다.
"""
from __future__ import annotations

from open_proxy_mcp.dart.client import html_to_text
from open_proxy_mcp.services.biz_fields import (
    _mix_self_check,
    extract_key_contracts,
    extract_product_pricing,
    extract_revenue_mix_form,
)

_MIX_TABLE = (
    "<TABLE><TBODY>"
    "<TR><TD>사업부문</TD><TD>품목</TD><TD>매출액(비율)</TD></TR>"
    "<TR><TD>전기전자</TD><TD>전력기기</TD><TD>2,835,195(69.5%)</TD></TR>"
    "<TR><TD>전기전자</TD><TD>회전기기</TD><TD>588,664(14.4%)</TD></TR>"
    "<TR><TD>전기전자</TD><TD>배전기기</TD><TD>655,639(16.1%)</TD></TR>"
    "<TR><TD>합계</TD><TD>합계</TD><TD>4,079,498(100%)</TD></TR>"
    "</TBODY></TABLE>"
)


def _doc(sub2_body: str, sub6_body: str = "") -> str:
    return (
        "<DOCUMENT>"
        '<TITLE AASSOCNOTE="D-0-2-0-0">II. 사업의 내용</TITLE>'
        '<TITLE AASSOCNOTE="L-0-2-1-L1">1. 사업의 개요</TITLE><P>개요</P>'
        '<TITLE AASSOCNOTE="L-0-2-2-L1">2. 주요 제품 및 서비스</TITLE>' + sub2_body +
        '<TITLE AASSOCNOTE="L-0-2-6-L1">6. 주요계약 및 연구개발활동</TITLE>' + sub6_body +
        '<TITLE AASSOCNOTE="L-0-2-7-L1">7. 기타 참고사항</TITLE><P>기타</P>'
        "</DOCUMENT>"
    )


def _mix(html):
    return extract_revenue_mix_form(html_to_text(html), html, None)


def test_sibling_heading_does_not_truncate_region():
    """소절 표제 바로 뒤 산문 → 표는 형제 표제 아래. 표제 어휘가 「현황」이 아니어도 잡아야 한다."""
    html = _doc("<P>회사는 의약품을 제조·판매합니다.</P>"
                '<P><SPAN USERMARK="ULE B">1. 주요제품 (연결기준)</SPAN></P>' + _MIX_TABLE)
    r = _mix(html)
    assert r["status"] == "MARKDOWN", r
    assert "전력기기" in r["markdown"]


def test_price_trend_stays_with_product_pricing():
    """같은 소절2의 「주요제품 가격변동추이」를 매출구성이 가져가면 안 된다."""
    price = ('<P><SPAN USERMARK="ULE B">2. 주요제품 가격변동추이</SPAN></P>'
             "<TABLE><TBODY><TR><TD>구분</TD><TD>제40기</TD></TR>"
             "<TR><TD>브레트라정</TD><TD>66,100</TD></TR></TBODY></TABLE>")
    html = _doc('<P><SPAN USERMARK="ULE B">가. 주요 제품 등의 현황</SPAN></P>'
                + _MIX_TABLE + price)
    r = _mix(html)
    assert "전력기기" in r["markdown"]
    assert "가격변동추이" not in r["markdown"].split("\n", 1)[0]
    assert extract_product_pricing(html_to_text(html), html, None)["status"] == "MARKDOWN"


def test_financial_product_catalogue_is_flagged_not_reported_as_mix():
    """은행 상품 카탈로그(상품수·가입대상, 단위 「개」)는 매출구성이 아니다."""
    # 실제 KB금융지주는 65,050자 안에 「비율」이 섞여 있어 content-gate 를 통과했다.
    # 게이트를 통과시킨 뒤 카탈로그 차단어(상품수·가입대상)가 잡는지를 본다.
    cat = ('<P><SPAN USERMARK="ULE B">사. 주요 상품ㆍ서비스</SPAN></P>'
           "<TABLE><TBODY><TR><TD>(단위 : 개)</TD></TR>"
           "<TR><TD>구분</TD><TD>상품수</TD><TD>비율</TD><TD>주요상품의 내용</TD></TR>"
           "<TR><TD>예금</TD><TD>27</TD><TD>31%</TD><TD>가입대상 : 개인</TD></TR>"
           "</TBODY></TABLE>")
    r = _mix(_doc(cat))
    assert r["status"] == "NEEDS_REVIEW"
    assert r["not_sales_caption"] is True
    assert r["markdown"]          # 값은 안 내되 원문은 넘긴다


def test_construction_backlog_caption_is_not_a_sales_table():
    cons = ('<P><SPAN USERMARK="ULE B">가. 주요 공사 현황</SPAN></P>'
            "<TABLE><TBODY><TR><TD>공사명</TD><TD>도급액</TD></TR>"
            "<TR><TD>○○아파트</TD><TD>12,345</TD></TR></TBODY></TABLE>")
    r = _mix(_doc(cons))
    assert r["status"] != "MARKDOWN" or r.get("not_sales_caption")


def test_key_contracts_separated_from_rnd():
    sub6 = ('<P><SPAN USERMARK="ULE B">가. 주요계약</SPAN></P>'
            "<TABLE><TBODY><TR><TD>계약상대</TD><TD>계약기간</TD></TR>"
            "<TR><TD>A사</TD><TD>2024~2030</TD></TR></TBODY></TABLE>"
            '<P><SPAN USERMARK="ULE B">나. 연구개발활동</SPAN></P><P>연구개발 개요</P>')
    html = _doc('<P><SPAN USERMARK="ULE B">가. 주요 제품 등의 현황</SPAN></P>' + _MIX_TABLE, sub6)
    r = extract_key_contracts(html_to_text(html), html, None)
    assert r["status"] == "MARKDOWN"
    assert "계약상대" in r["markdown"]


def test_key_contracts_na_is_not_a_failure():
    sub6 = "<P>가. 주요계약 - 해당사항 없음</P><P>나. 연구개발활동</P>"
    html = _doc('<P><SPAN USERMARK="ULE B">가. 주요 제품 등의 현황</SPAN></P>' + _MIX_TABLE, sub6)
    r = extract_key_contracts(html_to_text(html), html, None)
    assert r["status"] == "NOT_APPLICABLE"


def test_self_check_ties_out_and_reads_unit():
    md = ("가. 주요 제품 등의 현황\n\n| (단위 : 백만원) |\n|---|\n\n"
          "| 사업부문 | 매출액(비율) |\n|---|---|\n"
          "| 전력기기 | 2,835,195(69.5%) |\n| 회전기기 | 588,664(14.4%) |\n"
          "| 배전기기 | 655,639(16.1%) |\n| 합계 | 4,079,498(100%) |\n")
    sc = _mix_self_check(md)
    assert sc["unit"] == "백만원"
    assert sc["declared_total"] == 4_079_498
    assert sc["item_sum"] == 4_079_498
    assert "일치" in sc["tie_out"]
    assert sc["pct_sum_is_100"] is True


def test_self_check_flags_instead_of_asserting_when_subtotals_double_count():
    """소계행이 라벨 없이 섞이면 비율합 200% — 틀린 합계를 내지 말고 불일치라고 말해야 한다."""
    md = ("| 품목 | 매출액 | 비중 |\n|---|---|---|\n"
          "| 양약 | 168,932 | 73.13% |\n| 기타 | 65,749 | 26.87% |\n"
          "| - | 168,932 | 73.13% |\n| - | 65,749 | 26.87% |\n"
          "| 총계 | 234,681 | 100.00% |\n")
    sc = _mix_self_check(md)
    assert sc["pct_sum_is_100"] is False
    assert "≠" in sc["tie_out"]


def test_self_check_scopes_to_one_table_when_region_has_many():
    # 금액은 천단위 구분자 있는 실제 규모로 — _NUM_RE 는 순번·개수 오탐을 막으려고
    # 4자리 이상이거나 구분자가 있는 수만 금액으로 본다.
    md = ("| 품목 | 매출액 |\n|---|---|\n| A | 1,000 |\n| 합계 | 1,000 |\n"
          "\n| 품목 | 매출액 |\n|---|---|\n| B | 9,000 |\n| 합계 | 9,000 |\n")
    sc = _mix_self_check(md)
    assert sc["tables_in_region"] >= 2
    assert sc["scope_note"]
    assert sc["declared_total"] in (1_000, 9_000)   # 두 표를 뭉뚱그려 10,000 을 만들지 않는다


def test_renderer_keeps_raw_for_needs_review_and_does_not_say_not_applicable():
    """NEEDS_REVIEW를 「해당없음」으로 렌더하면서 원문을 버리던 결함(남광토건 라이브 실측)."""
    from open_proxy_mcp.tools.business_details import _render as _r
    rendered = _r({
        "status": "ok", "subject": "테스트",
        "data": {"report": {},
                 "revenue_mix_form": {"status": "NEEDS_REVIEW", "extraction_status": "SUCCESS",
                                      "note": "캡션이 매출 구성표가 아니다",
                                      "not_sales_caption": True,
                                      "markdown": "가. 주요 공사 현황\n| 공사명 | 도급액 |"}},
    })
    assert "해당없음" not in rendered
    assert "검토필요" in rendered
    assert "주요 공사 현황" in rendered          # 원문을 버리지 않는다


def test_renderer_omits_dangling_dash_when_na_reason_is_empty():
    from open_proxy_mcp.tools.business_details import _render as _r
    rendered = _r({"status": "ok", "subject": "테스트",
                   "data": {"report": {},
                            "key_contracts": {"status": "NOT_APPLICABLE",
                                              "extraction_status": "NOT_APPLICABLE"}}})
    assert "해당없음 —" not in rendered
    assert "해당없음" in rendered


def test_text_only_table_counts_as_content_against_a_preceding_na_phrase():
    """「가. 주요 계약: 해당사항 없습니다」 뒤의 이름만 있는 표가 빈 표로 버려지던 것(하이브)."""
    from open_proxy_mcp.services.biz_fields import _md_has_data_rows, _md_has_text_rows
    roster = ("| 회 사 명 | 그 룹 | 아 티 스 트 |\n|---|---|---|\n"
              "| ㈜빅히트뮤직 | 방탄소년단 | 김남준 |\n"
              "| ㈜빅히트뮤직 | 방탄소년단 | 김석진 |\n"
              "| ㈜플레디스 | 세븐틴 | 최승철 |\n")
    assert _md_has_data_rows(roster, 1) is False    # 숫자가 없어 기존 판정은 '빈 표'
    assert _md_has_text_rows(roster, 3) is True

    sub6 = ("<P><SPAN USERMARK=\"ULE B\">가. 주요 계약</SPAN></P><P>해당사항 없습니다.</P>"
            "<P><SPAN USERMARK=\"ULE B\">나. 주요 아티스트 전속계약</SPAN></P>"
            "<TABLE><TBODY><TR><TD>회사명</TD><TD>그룹</TD><TD>아티스트</TD></TR>"
            "<TR><TD>빅히트뮤직</TD><TD>방탄소년단</TD><TD>김남준</TD></TR>"
            "<TR><TD>빅히트뮤직</TD><TD>방탄소년단</TD><TD>김석진</TD></TR>"
            "<TR><TD>플레디스</TD><TD>세븐틴</TD><TD>최승철</TD></TR></TBODY></TABLE>")
    html = _doc('<P><SPAN USERMARK="ULE B">가. 주요 제품 등의 현황</SPAN></P>' + _MIX_TABLE, sub6)
    r = extract_key_contracts(html_to_text(html), html, None)
    assert r["status"] == "MARKDOWN"
    assert "방탄소년단" in r["markdown"]


# ── revenue_breakdown: 세 축을 한 필드로 묶되 칸막이(출처·상태)는 유지한다 ──────────

def _rb(by_segment=None, by_product=None, geo=None, **kw):
    from open_proxy_mcp.services.business_details import _REVENUE_AXIS_SOURCE
    node = {k: {**(v or {"status": "NOT_COLLECTED", "extraction_status": "NOT_COLLECTED"}),
                "source": _REVENUE_AXIS_SOURCE[k]}
            for k, v in (("by_segment", by_segment), ("by_product", by_product))}
    data = {"report": {}, "revenue_breakdown": {**node, "guidance": "축을 섞지 말 것", **kw}}
    if geo is not None:
        data["geo_revenue"] = geo          # 지역별은 묶음 밖 독립 필드
    return {"status": "ok", "subject": "테스트", "data": data}


def test_breakdown_renders_both_axes_with_provenance():
    from open_proxy_mcp.tools.business_details import _render as _r
    out = _r(_rb(
        by_segment={"status": "NOT_APPLICABLE", "na_reason": "단일부문 선언"},
        by_product={"status": "MARKDOWN", "markdown": "| 전력기기 | 2,835,195(69.5%) |",
                    "self_check": {"unit": "백만원", "pct_sum": 100.0, "tie_out": "항목합≈합계행 일치"}},
        geo={"status": "SUCCESS", "items": [{"name": "국내", "revenue": 1000}], "unit": "백만원"},
        available=["by_product"], needs_review=[]))
    for ko in ("부문별", "제품별"):
        assert f"### [{ko}]" in out
    assert "### [지역별]" not in out          # 지역별은 묶지 않는다
    # 칸막이 — 어느 축이 감사 대상인지 출력에 남아야 한다
    assert "K-IFRS 1108 영업부문 · 외부감사 대상" in out
    assert "외부감사 대상 아님" in out
    assert "단일부문 선언" in out and "전력기기" in out and "국내" in out
    assert "자가검산: 단위 백만원" in out


def test_breakdown_does_not_call_needs_review_axis_available():
    """남광토건: 제품별이 시공실적 표라 검토필요인데 「값이 나온 축」에 섞이면 안내가 거짓이 된다."""
    from open_proxy_mcp.tools.business_details import _render as _r
    out = _r(_rb(
        by_segment={"status": "OK", "items": [{"name": "건축", "revenue": 103_089_659}],
                    "unit": "천원"},
        by_product={"status": "NEEDS_REVIEW", "not_sales_caption": True,
                    "note": "캡션이 매출 구성표가 아니다", "markdown": "가. 주요공사 현황"},
        available=["by_segment"], needs_review=["by_product"]))
    assert "값이 나온 축: **부문별**" in out
    assert "원문만 있는 축(검토필요): **제품별**" in out
    assert "주요공사 현황" in out          # 원문은 그래도 넘긴다


def test_geo_axis_is_rendered_in_markdown():
    """geo_revenue 는 260724 신설 후 md 렌더가 없어 format=md 에선 보이지 않았다."""
    from open_proxy_mcp.tools.business_details import _render as _r
    out = _r(_rb(geo={"status": "SUCCESS", "unit": "백만원",
                      "items": [{"name": "국내", "revenue": 46_641_151},
                                {"name": "미주", "revenue": 12_000_000}],
                      "basis_caption": "고객 소재지 기준"}))
    assert "46,641,151" in out and "미주" in out and "고객 소재지 기준" in out


def test_default_field_set_returns_bundle_not_flat_duplicates():
    """기본 호출은 묶음만 — 같은 내용이 평평 키로 한 번 더 실리면 응답이 두 배가 된다."""
    from open_proxy_mcp.services.business_details import BUSINESS_DETAILS_FIELDS, _REVENUE_AXES
    assert "revenue_breakdown" in BUSINESS_DETAILS_FIELDS
    for flat in _REVENUE_AXES:
        assert flat not in BUSINESS_DETAILS_FIELDS, flat


def test_axis_names_stay_valid_as_aliases():
    """`fields=segments` 로 부르던 기존 호출이 깨지면 안 된다 — 축 이름은 별칭으로 살아 있다."""
    from open_proxy_mcp.services.business_details import _REVENUE_AXES, _REVENUE_AXIS_SOURCE
    assert set(_REVENUE_AXES) == {"segments", "revenue_mix_form"}
    assert set(_REVENUE_AXES.values()) == set(_REVENUE_AXIS_SOURCE)


def test_breakdown_heading_lists_exactly_the_axes_it_renders():
    """geo 를 뺐는데 제목에 「지역별」이 남아 있던 결함 — 제목을 축 목록에서 파생시킨다."""
    from open_proxy_mcp.tools.business_details import _AXIS_KO, _render as _r
    out = _r(_rb(by_segment={"status": "OK", "items": [], "unit": "원"}, available=["by_segment"]))
    head = next(ln for ln in out.splitlines() if ln.startswith("## 매출 분해"))
    for ko in _AXIS_KO.values():
        assert ko in head
    assert head.count("·") == len(_AXIS_KO) - 1     # 없는 축이 제목에 남지 않는다


def test_output_does_not_scold_the_reader():
    """⚠️·「~하지 말 것」은 읽는 사람이 뭘 잘못한 것처럼 느끼게 한다 — 자료 성격은 서술문으로."""
    from open_proxy_mcp.tools.business_details import _render as _r
    out = _r(_rb(
        by_segment={"status": "NOT_APPLICABLE", "na_reason": "단일부문 선언"},
        by_product={"status": "NEEDS_REVIEW", "note": "캡션이 매출 구성표가 아닙니다.",
                    "markdown": "가. 주요공사 현황"},
        available=[], needs_review=["by_product"]))
    assert "⚠" not in out
    for scold in ("하지 마세요", "하지 말 것", "금지"):
        assert scold not in out, scold


def test_warnings_footer_is_a_processing_note_not_a_warning_sign():
    """푸터에 담기는 건 대개 실패가 아니라 처리 메모다 — 30사 스윕에서 6사가 ⚠ 를 달고 나왔다."""
    from open_proxy_mcp.tools.business_details import _render as _r
    out = _r({"status": "ok", "subject": "테스트", "data": {"report": {}},
              "warnings": ["segment_profit: 정형 저신뢰 → 원문 마크다운/후보 반환"]})
    assert "⚠" not in out
    assert "처리 메모" in out and "정형 저신뢰" in out


def test_explicit_segments_request_keeps_the_flat_key():
    """proxy_advise 는 fields=["segments"] 로 부르고 data["segments"] 를 직접 읽는다.

    묶음을 도입하면서 평평 키를 지우면 이사-부문 시그널이 조용히 죽는다 — 별칭을 계약으로 고정.
    """
    from open_proxy_mcp.services.business_details import _REVENUE_AXES, BUSINESS_DETAILS_FIELDS
    # 기본 세트에는 없고(묶음으로 나감), 이름 자체는 살아 있어야 한다(명시 요청 시 평평 반환)
    assert "segments" not in BUSINESS_DETAILS_FIELDS
    assert "segments" in _REVENUE_AXES


def test_extract_segment_items_reads_the_flat_key_not_the_bundle():
    from open_proxy_mcp.services.director_segment_signal import extract_segment_items
    flat = {"data": {"segments": {"status": "OK", "items": [{"name": "A", "revenue": 1}],
                                  "unit": "백만원"}}}
    assert extract_segment_items(flat) is not None
    # 묶음만 있고 평평 키가 없으면 못 읽는다 → fields=["segments"] 를 계속 명시해야 한다는 뜻
    bundled = {"data": {"revenue_breakdown": {"by_segment": flat["data"]["segments"]}}}
    assert extract_segment_items(bundled) is None


# ── section_chars: 상한은 하드코딩이 아니라 호출 파라미터 ────────────────────────

def test_cap_is_a_parameter_and_says_how_to_get_the_rest():
    from open_proxy_mcp.services.biz_fields import _cap_markdown
    big = {"status": "MARKDOWN", "markdown": "가" * 50_000}
    d = _cap_markdown(big, 20_000)
    assert len(d["markdown"]) == 20_000
    assert d["markdown_truncated"] is True and d["markdown_full_chars"] == 50_000
    assert "section_chars" in d["truncation_note"]          # 어떻게 더 받는지 알려준다
    # 올리면 그만큼 더 온다
    assert len(_cap_markdown(big, 80_000)["markdown"]) == 50_000
    assert "markdown_truncated" not in _cap_markdown(big, 80_000)


def test_cap_default_matches_the_service_default():
    from open_proxy_mcp.services.biz_fields import _BIZ_MD_CAP, _cap_markdown
    from open_proxy_mcp.services.business_details import SECTION_CHARS_DEFAULT
    assert _BIZ_MD_CAP == SECTION_CHARS_DEFAULT
    assert len(_cap_markdown({"markdown": "가" * 30_000})["markdown"]) == SECTION_CHARS_DEFAULT


def test_truncation_note_is_rendered_so_the_caller_can_act_on_it():
    from open_proxy_mcp.tools.business_details import _render as _r
    out = _r({"status": "ok", "subject": "테스트", "data": {"report": {}, "financial_soundness": {
        "status": "MARKDOWN", "markdown": "| BIS비율 | 15.2 |",
        "markdown_truncated": True, "markdown_full_chars": 70_710,
        "truncation_note": "원문 70,710자 중 앞 20,000자입니다. 뒤쪽이 필요하면 "
                           "section_chars 를 올려 다시 조회하세요."}}})
    assert "70,710" in out and "section_chars" in out


def test_section_chars_out_of_range_is_rejected():
    import asyncio
    from open_proxy_mcp.services.business_details import build_business_details_payload
    for bad in (0, 1_999, 200_001, "20000", True):
        r = asyncio.run(build_business_details_payload("삼성전자", section_chars=bad))
        assert r["status"] == "error", bad
        assert "section_chars" in " ".join(r.get("warnings") or [])
