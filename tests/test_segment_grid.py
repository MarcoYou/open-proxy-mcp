# -*- coding: utf-8 -*-
"""부문표 격자 재판독(segment_grid) 회귀 테스트 — synthetic 표. network 0콜.

핵심 계약: ① 외부매출 행 우선(연도 간 개념 일관) ② dash 열=0.0 자리표시자(부문 소실 금지)
③ 조정·합계 열은 excess로 분리(기존 검산 게이트 호환) ④ 이름 불일치·비주석 소스는 None.
"""
from __future__ import annotations

from open_proxy_mcp.services.business_details import OK, SegmentProfit, _segment_confident
from open_proxy_mcp.services.segment_grid import _parse_table, grid_refine


def _tbl(rows: list[list[str]]) -> str:
    trs = "".join(
        "<TR>" + "".join(f"<TD>{c}</TD>" for c in r) + "</TR>" for r in rows)
    # 스페이서 필터(<1500자 표 skip)를 넘도록 표 내부에 무해한 패딩 셀 포함
    pad = "<TR><TD>" + " " * 1500 + "</TD></TR>"
    return f'<TABLE ACLASS="NORMAL"><TBODY>{trs}{pad}</TBODY></TABLE>'


_ROWS = [
    ["(단위 : 백만원)", "", "", "", ""],
    ["구분", "화학", "전지", "조선", "합계"],
    ["총부문수익", "1,200", "2,300", "-", "3,500"],
    ["부문간수익", "200", "300", "-", "500"],
    ["매출액", "1,000", "2,000", "-", "3,000"],
    ["영업이익(손실)", "100", "(50)", "-", "50"],
]


def test_parse_prefers_external_row_and_keeps_dash_column() -> None:
    p = _parse_table(_tbl(_ROWS))
    assert p is not None
    assert p["revenue_metric"] == "매출액"            # 총부문수익(위) 아닌 외부 계열
    names = [s["name"] for s in p["segments"]]
    assert names == ["화학", "전지", "조선"]           # dash 열(조선) 유지
    assert p["segments"][2]["revenue"] == 0.0
    assert p["segments"][1]["profit"] == -50.0        # 괄호 음수
    assert p["excess"] == [3000.0]                    # 합계 열 → 검산 재료
    assert p["unit"] == "백만원"


def test_parse_adjustment_column_ordered_before_total() -> None:
    rows = [r[:] for r in _ROWS]
    rows[1] = ["구분", "화학", "전지", "연결조정", "합계"]
    rows[4] = ["매출액", "1,000", "2,000", "(500)", "2,500"]
    p = _parse_table(_tbl(rows))
    assert [s["name"] for s in p["segments"]] == ["화학", "전지"]
    assert p["excess"] == [-500.0, 2500.0]            # 조정 → 합계 순(게이트 ⓑ 호환)


def test_gate_passes_grid_output() -> None:
    p = _parse_table(_tbl(_ROWS))
    sp = SegmentProfit(status=OK, source="note_grid", segments=p["segments"])
    sp.adjustments = [{"revenue_excess": p["excess"]}]
    assert _segment_confident(sp)                     # 부문합 3,000 == 총계 3,000


def test_refine_skips_non_note_source_and_name_mismatch() -> None:
    html = "<DOCUMENT>3. 부문정보" + _tbl(_ROWS) + "</DOCUMENT>"
    body_sp = SegmentProfit(status=OK, source="body", anchor="부문정보")
    assert grid_refine(html, body_sp) is None         # 본문표 소스는 v1 미대상
    note_sp = SegmentProfit(status=OK, source="note", anchor="부문정보",
                            segments=[{"name": "완전히다른부문", "revenue": 1.0}])
    assert grid_refine(html, note_sp) is None         # 이름 겹침 0 = 다른 표 의심 → 텍스트 유지


def test_refine_adopts_on_note_anchor() -> None:
    html = "<DOCUMENT>3. 부문정보" + _tbl(_ROWS) + "</DOCUMENT>"
    sp = SegmentProfit(status=OK, source="note", anchor="부문정보",
                       segments=[{"name": "화학", "revenue": 1200.0}])
    g = grid_refine(html, sp)
    assert g is not None and g.source == "note_grid"
    assert g.revenue_metric == "매출액"


def test_geo_share_is_the_unit_proof_metric():
    """해외비중은 단위가 약분되므로 단위 미상일 때도 맞는 유일한 지표.

    각주가 붙은 지역명(카카오 「국내(주1)」)을 국내로 못 읽으면 해외비중이 100%로 나온다 —
    실측에서 실제로 그랬다(정정 후 20.6%).
    """
    from open_proxy_mcp.services.segment_grid import _foreign_share

    got = _foreign_share([{"name": "국내(주1)", "revenue": 800.0},
                          {"name": "아시아", "revenue": 100.0},
                          {"name": "북미", "revenue": 100.0}])
    assert got["foreign_share_pct"] == 20.0
    assert "share_caveat" not in got

    # 표에 국내 구분이 아예 없으면 100%로 계산되지만 그 사실을 밝힌다(대한해운)
    none_dom = _foreign_share([{"name": "아시아", "revenue": 60.0},
                               {"name": "유럽", "revenue": 40.0}])
    assert none_dom["foreign_share_pct"] == 100.0
    assert "국내 구분 항목이 없어" in none_dom["share_caveat"]

    # 「본사 소재지 국가」도 국내다
    hq = _foreign_share([{"name": "본사 소재지 국가", "revenue": 932160.0},
                         {"name": "외국", "revenue": 3147338.0}])
    assert hq["foreign_share_pct"] == 77.2


def test_entity_wide_geo_tables_are_small_and_must_not_be_length_filtered():
    """entity-wide 지역표는 데이터가 한 행뿐이라 작다 — 길이 하한에 걸려선 안 된다.

    실측: HD현대일렉트릭 493자 · 현대차 1,235자로 둘 다 1500자 하한에 걸려 아예 읽히지
    않았다(파싱 자체는 정상이었다). 지역 머리를 가진 표만 하한을 낮춘다.
    캐시 65사 회귀: 검출 8 → 17사, 상실 0 · 기존 값 변경 0.
    """
    from open_proxy_mcp.services.segment_grid import _EW_GEO_MIN_CHARS, _GEO_HEAD_RE

    assert _EW_GEO_MIN_CHARS < 493, "실측 최소 지역표(493자)보다 낮아야 한다"
    assert _GEO_HEAD_RE.search("<TH>본사 소재지 국가</TH>")
    assert _GEO_HEAD_RE.search("<TH>외 국</TH>")
    assert not _GEO_HEAD_RE.search("<TH>차량</TH><TH>금융</TH>")


def test_geo_anchor_falls_back_when_segment_note_anchor_is_missing():
    """부문정보 앵커를 못 찾아도 지역 공시는 따로 있을 수 있다.

    HD현대일렉트릭은 앵커가 안 잡혀(`''`) 스캔 자체를 건너뛰었는데 지역표는 실재했다.
    """
    from open_proxy_mcp.services.segment_grid import _find_geo_anchor_pos

    html = "x" * 5000 + "<TH>본사 소재지 국가</TH>"
    pos = _find_geo_anchor_pos(html)
    assert pos >= 0
    assert pos <= 5000, "표를 포함하도록 조금 앞에서 시작해야 한다"
    assert _find_geo_anchor_pos("<TH>차량</TH><TH>금융</TH>") == -1
