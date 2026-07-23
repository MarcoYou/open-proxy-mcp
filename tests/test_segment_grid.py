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
