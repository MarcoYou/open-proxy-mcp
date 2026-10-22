# -*- coding: utf-8 -*-
"""business_details 매출 분해 표의 기간 표기와 제품별 자가검산의 △ 부호 처리. network 0콜.

배경(삼성전자 2026 반기보고서 실측):
  ① 제품별 표 「기타 | 부문간 내부거래 제거 등 | △271,085 | △8.9%」 의 △(원문 각주 「△는 부(-)의 값임」)를
     양수로 읽어 자가검산이 「비율합 117.7%·항목합≠합계행 17.8%」로 나왔다. 부호를 지키면 99.9%·일치다.
  ② 부문표(III 주석)는 「3개월」「누적」 열이 나란히 있는 표에서 3개월 열을 읽는데 응답에 기간 표기가 없었다.
"""
from __future__ import annotations

from open_proxy_mcp.services.biz_fields import _mix_self_check
from open_proxy_mcp.services.biz_period import (
    UNKNOWN,
    attach_mix_period,
    attach_segment_period,
    read_mix_period,
    segment_period,
)
from open_proxy_mcp.services.business_details import OK, SegmentProfit
from open_proxy_mcp.services.segment_grid import _parse_table
from open_proxy_mcp.tools.business_details import _mix_lines, _seg_lines

_SAMSUNG_MIX_MD = (
    "| (단위 : 억원, %) |\n|---|\n\n\n"
    "| 부  문 | 주요 제품 | 매출액 | 비중 |\n|---|---|---|---|\n"
    "| DX 부문 | TV, 냉장고 등 | 1,006,771 | 33.0% |\n"
    "| DS 부문 | DRAM, NAND Flash 등 | 2,092,317 | 68.5% |\n"
    "| SDC | 스마트폰용 OLED패널 등 | 141,785 | 4.6% |\n"
    "| Harman | 디지털 콕핏 등 | 83,941 | 2.7% |\n"
    "| 기타 | 부문간 내부거래 제거 등 | △271,085 | △8.9% |\n"
    "| 총 계 | 총 계 | 3,053,729 | 100.0% |\n\n\n"
    "| ※ 각 부문별 매출액은 부문 등 간 내부거래를 포함하고 있습니다. | [△는 부(-)의 값임] |\n|---|---|\n"
)


def test_triangle_negative_row_keeps_its_sign_in_self_check():
    """△ 는 음수 표기다 — 양수로 읽으면 비율합 117.7%·항목합≠합계행 17.8% 로 거짓 불일치가 난다."""
    sc = _mix_self_check(_SAMSUNG_MIX_MD)
    assert sc["pct_sum"] == 99.9 and sc["pct_sum_is_100"] is True
    assert sc["item_sum"] == 3053729.0 and sc["declared_total"] == 3053729.0
    assert sc["tie_out"] == "항목합≈합계행 일치"


def test_real_mismatch_is_still_reported():
    """△ 를 고쳐도 진짜 불일치는 그대로 잡는다 — 합계행만 틀리게 둔 표."""
    md = _SAMSUNG_MIX_MD.replace("3,053,729 | 100.0%", "2,500,000 | 100.0%")
    sc = _mix_self_check(md)
    assert sc["tie_out"].startswith("항목합≠합계행")


_GROWTH_MD = ("| 부  문 | 매출액 | 비중 | 전년비 |\n|---|---|---|---|\n"
              "| A | 600 | 60.0% | ▲5.0% |\n| B | 400 | 40.0% | ▲3.0% |\n"
              "| 합 계 | 1,000 | 100.0% | |\n")


def test_up_marker_is_not_negated_without_a_footnote():
    """▲·▽ 는 표에 따라 증가·감소 표시(전년비 ▲5.0%)다 — 각주가 부(-)라고 밝히지 않으면 음수로 읽지 않는다(BP2)."""
    sc = _mix_self_check(_GROWTH_MD)
    assert sc["pct_sum"] is not None and sc["pct_sum"] > 0


def test_up_marker_is_negated_when_a_footnote_says_so():
    sc = _mix_self_check(_GROWTH_MD + "\n| ※ [▲는 부(-)의 값임] |\n|---|\n")
    assert sc["pct_sum"] == -8.0
    sc2 = _mix_self_check(_GROWTH_MD + "\n| ※ [△,▲는 부(-)의 값임] |\n|---|\n")
    assert sc2["pct_sum"] == -8.0


def test_triangle_is_negated_even_without_a_footnote():
    md = "| 부 문 | 매출액 |\n|---|---|\n| A | 5,000 |\n| 기타 | △1,000 |\n| 합 계 | 4,000 |\n"
    sc = _mix_self_check(md)
    assert sc["item_sum"] == 4000.0 and sc["tie_out"] == "항목합≈합계행 일치"


def _tbl(rows: list[list[str]]) -> str:
    trs = "".join("<TR>" + "".join(f"<TD>{c}</TD>" for c in r) + "</TR>" for r in rows)
    pad = "<TR><TD>" + " " * 1500 + "</TD></TR>"
    return f'<TABLE ACLASS="NORMAL"><TBODY>{trs}{pad}</TBODY></TABLE>'


# 삼성전자 반기보고서 부문 주석 꼴 — 부문명 행 아래 「3개월 / 누적」 소제목 행.
_SEG_ROWS = [
    ["(단위 : 백만원)", "", "", "", "", "", ""],
    ["구분", "DX 부문", "DX 부문", "DS 부문", "DS 부문", "SDC", "SDC"],
    ["", "3개월", "누적", "3개월", "누적", "3개월", "누적"],
    ["매출액", "48,022,433", "100,677,119", "127,516,071", "209,231,714", "7,485,065", "14,178,522"],
    ["영업이익", "(818,805)", "2,148,877", "89,196,019", "142,859,293", "680,607", "1,043,353"],
]


def test_grid_reads_period_of_chosen_column():
    p = _parse_table(_tbl(_SEG_ROWS))
    assert p is not None
    assert [s["revenue"] for s in p["segments"]] == [48022433.0, 127516071.0, 7485065.0]   # 값 선택은 종전 그대로(3개월 열)
    assert p["period_label"] == "3개월"
    assert p["period_columns"] == ["3개월", "누계", "3개월", "누계", "3개월", "누계"]


def test_grid_without_period_columns_has_no_label():
    rows = [["(단위 : 백만원)", "", ""], ["구분", "화학", "전지"], ["매출액", "1,000", "2,000"],
            ["영업이익", "100", "50"]]
    p = _parse_table(_tbl(rows))
    assert p is not None
    assert not p.get("period_label") and not p.get("period_columns")


def test_segment_period_notes_the_other_column_it_did_not_read():
    d = segment_period("3개월", ["3개월", "누계", "3개월", "누계"], "반기보고서 (2026.06)")
    assert d["label"] == "분기 단독(3개월)" and d["basis"] == "원문 표 머리"
    assert d["columns_in_source"] == ["3개월", "누계"]
    assert "누계 열의 값은 이 표에 없습니다" in d["note"]


def test_segment_period_report_kind_fallback_is_annual_only():
    """보고서 종류 대체는 사업보고서(연간)에만 — 반기·분기 주석 표는 3개월·누적 열이 함께 있을 수 있어
    표 머리를 못 읽었을 때 「반기 누계」로 단정하면 3개월 값에 누계 라벨이 붙는다(BP1)."""
    a = segment_period(None, None, "사업보고서 (2025.12)")
    assert a["label"] == "연간" and "원문 표에 기간 표기 없음" not in a["basis"]
    h = segment_period(None, None, "반기보고서 (2026.06)")
    assert h["label"] == UNKNOWN and h["basis"] == "미상"
    assert "3개월·누적 열이 함께 있을 수 있어" in h["note"]
    q = segment_period(None, None, "분기보고서 (2026.03)")
    assert q["label"] == UNKNOWN and "1분기 3개월 또는 3분기 누계" in q["note"]
    assert "3개월·누적 열이 함께 있을 수 있어" in q["note"]
    assert segment_period(None, None, None)["label"] == UNKNOWN


# 기간 묶음 머리가 부문명 행 「위」에 있는 표 — 「당반기 3개월 | 당반기 누적」 아래에 부문명(BP1).
_SEG_ROWS_PERIOD_ABOVE = [
    ["(단위 : 백만원)", "", "", "", ""],
    ["구분", "당반기 3개월", "당반기 3개월", "당반기 누적", "당반기 누적"],
    ["구분", "A부문", "B부문", "A부문", "B부문"],
    ["매출액", "100", "200", "250", "450"],
    ["영업이익", "10", "20", "25", "45"],
]


def test_grid_reads_period_row_above_segment_name_row():
    p = _parse_table(_tbl(_SEG_ROWS_PERIOD_ABOVE))
    assert p is not None
    assert [s["revenue"] for s in p["segments"]] == [100.0, 200.0]      # 값 선택은 종전 그대로(먼저 나오는 3개월 열)
    assert p["period_label"] == "3개월"
    assert p["period_columns"] == ["3개월", "3개월", "누계", "누계"]


def test_period_above_header_is_not_mislabeled_as_cumulative():
    """3개월 값에 반기 누계 라벨이 붙으면 안 된다 — 격자가 읽으면 3개월, 못 읽으면 기간 미상."""
    p = _parse_table(_tbl(_SEG_ROWS_PERIOD_ABOVE))
    d = segment_period(p["period_label"], p["period_columns"], "반기보고서 (2026.06)")
    assert d["label"] == "분기 단독(3개월)" and d["basis"] == "원문 표 머리"
    assert d["columns_in_source"] == ["3개월", "누계"]


def test_text_path_half_report_without_period_label_is_unknown():
    """격자 재판독이 실패해 텍스트 경로 SegmentProfit(period_label 빈 값)이 쓰여도 반기를 누계로 단정하지 않는다."""
    sp = SegmentProfit(status=OK)
    seg = {"status": OK, "items": []}
    attach_segment_period(seg, sp, "반기보고서 (2026.06)")
    assert seg["period"]["label"] == UNKNOWN
    assert "3개월·누적 열이 함께 있을 수 있어" in seg["period"]["note"]
    out = "\n".join(_seg_lines({"status": OK, "items": [{"name": "A", "revenue": 1.0, "profit": 1.0}],
                               "unit": "백만원", "reconciliation": "x", "period": seg["period"]}, "###"))
    assert "기간: 기간 미상" in out and "3개월·누적 열이" in out


def test_attach_segment_period_only_on_ok_segment():
    sp = SegmentProfit(status=OK)
    sp.period_label, sp.period_columns = "누계", ["3개월", "누계"]
    seg = {"status": OK, "items": []}
    attach_segment_period(seg, sp, "반기보고서 (2026.06)")
    assert seg["period"]["label"] == "누계"
    review = {"status": "NEEDS_REVIEW"}
    attach_segment_period(review, sp, "반기보고서 (2026.06)")
    assert "period" not in review


def test_mix_period_from_table_head_beats_report_kind():
    md = "| 구분 | 당반기 3개월 | 당반기 누적 |\n|---|---|---|\n| A | 1,000 | 2,000 |\n"
    assert read_mix_period(md, "반기보고서 (2026.06)")["basis"] == "원문 표 머리"


def test_mix_period_samsung_half_year_is_cumulative_by_report_kind():
    d = read_mix_period(_SAMSUNG_MIX_MD, "반기보고서 (2026.06)")
    assert d["label"] == "반기 누계(6개월)"
    assert d["basis"].startswith("보고서 종류")


def test_mix_period_quarter_report_without_clue_is_unknown_not_guessed():
    d = read_mix_period(_SAMSUNG_MIX_MD, "분기보고서 (2026.03)")
    assert d["label"] == UNKNOWN
    d3 = read_mix_period("2026년 3분기 매출은 100억원입니다.\n" + _SAMSUNG_MIX_MD, "분기보고서 (2026.09)")
    assert d3["label"] == "3분기" and d3["basis"] == "원문 문장"


def test_data_rows_are_not_read_as_table_head():
    """데이터 행에 「누적」이 들어 있어도(품목명) 머리가 아니면 기간으로 읽지 않는다."""
    md = "| 구분 | 금액 |\n|---|---|\n| 누적형 상품 | 1,000 |\n"
    assert read_mix_period(md, "사업보고서 (2025.12)")["label"] == "연간"


def test_attach_mix_period_and_render_lines():
    node = {"status": "MARKDOWN", "markdown": _SAMSUNG_MIX_MD}
    attach_mix_period(node, "반기보고서 (2026.06)")
    assert node["period"]["label"] == "반기 누계(6개월)"
    out = "\n".join(_mix_lines(node, "###"))
    assert "기간: 반기 누계(6개월)" in out
    seg = {"status": "OK", "items": [{"name": "DX", "revenue": 1.0, "profit": 1.0}], "unit": "백만원",
           "reconciliation": "x", "period": segment_period("3개월", ["3개월", "누계"], "반기보고서 (2026.06)")}
    seg_out = "\n".join(_seg_lines(seg, "###"))
    assert "기간: 분기 단독(3개월)" in seg_out and "누계 열의 값은 이 표에 없습니다" in seg_out


# 머리가 3단인 표 — 맨 위 줄이 열 전체에 걸친 「6개월」, 그 아래 줄이 「3개월 | 누적」(BP4).
_SEG_ROWS_THREE_TIER = [
    ["구분", "2026.6.30 로 종료하는 6개월", "2026.6.30 로 종료하는 6개월",
     "2026.6.30 로 종료하는 6개월", "2026.6.30 로 종료하는 6개월"],
    ["구분", "3개월", "3개월", "누적", "누적"],
    ["구분", "A부문", "B부문", "A부문", "B부문"],
    ["매출액", "100", "200", "250", "450"],
    ["영업이익", "10", "20", "25", "45"],
]


def test_grid_prefers_the_period_nearest_to_the_metric_row():
    """열별 기간 말은 지표 행에 가까운(가장 구체적인) 줄이 이긴다 — 값은 3개월 열인데 맨 위 줄의 6개월이
    이기면 3개월 값에 누계 라벨이 붙는다(BP4)."""
    p = _parse_table(_tbl(_SEG_ROWS_THREE_TIER))
    assert p is not None
    assert [s["revenue"] for s in p["segments"]] == [100.0, 200.0]
    assert p["period_label"] == "3개월"
    assert p["period_columns"] == ["3개월", "3개월", "누계", "누계"]
    d = segment_period(p["period_label"], p["period_columns"], "반기보고서 (2026.06)")
    assert d["label"] == "분기 단독(3개월)" and d["basis"] == "원문 표 머리"


def test_up_marker_footnote_variants_with_extra_words_are_recognized():
    """각주가 「▲ 표시는 부(-)의 값임」「▲ 표시 금액은 부(-)」 꼴이어도 ▲ 를 음수로 읽는다(BP5)."""
    for note in ("※ ▲ 표시는 부(-)의 값임", "※ ▲ 표시 금액은 부(-)", "※ [▲표시는 부(-)의 값임]"):
        sc = _mix_self_check(_GROWTH_MD + f"\n| {note} |\n|---|\n")
        assert sc["pct_sum"] == -8.0, note
