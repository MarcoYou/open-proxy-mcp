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


def test_segment_period_falls_back_to_report_kind_then_unknown():
    assert segment_period(None, None, "사업보고서 (2025.12)")["label"] == "연간"
    assert segment_period(None, None, "반기보고서 (2026.06)")["label"].startswith("반기 누계")
    q = segment_period(None, None, "분기보고서 (2026.03)")
    assert q["label"] == UNKNOWN and "1분기 3개월 또는 3분기 누계" in q["note"]
    assert segment_period(None, None, None)["label"] == UNKNOWN


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
