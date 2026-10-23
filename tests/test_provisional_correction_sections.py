"""[기재정정] 영업(잠정)실적 정정 표 파서 — 절 머리·열 머리·접두 세 결함 (DATA-U3·DATA-PIT1).

고정본은 **합성**이다: 실제 회사 정정 표(`provisional_correction_dash_period_tag_20260727800078.html` 등)의
서식(정정항목 | 정정전 | 정정후 세 열, 「당기실적」「전기대비 증감율(%)」 절 머리행)을 따라 만들었다.
실제 회사 원문에서 같은 결함 꼴을 찾는 일은 DART 시장 검색(거래소공시 3개월 1만 1천 건)이 커서 하지 않았다.
DART 콜 0 — 네트워크 없음.

① 절 머리: 「전기대비 증감율(%)」 절의 「- 영업이익(당해실적)」 행이 금액 행으로 채택돼 % 가 value_krw 로 나갔다.
② 열 머리: 마지막 숫자를 정정후로 읽어서, 정정후 칸이 '-' 면 정정전 값이 value_krw 로 나가고
   정정 표 끝에 숫자 비고 열이 있으면 그 숫자가 value_krw 가 됐다.
③ 접두: `[기재정정]매출액또는손익구조30%…` 가 손익구조 변경 공시로 판정되지 않아 후보에서 빠졌다
   (원공시가 최신으로 나갔다).
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from open_proxy_mcp.services.provisional_earnings import (  # noqa: E402
    _find_provisional_candidates,
    _is_structure_change_report,
    parse_provisional_earnings,
)

_NM = "[기재정정]연결재무제표기준영업(잠정)실적(공정공시)"
_STRUCT_NM = "매출액또는손익구조30%(대규모법인은15%)이상변경"


def _table(rows: list[list[str]], unit: str = "억원, %", extra: str = "") -> str:
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<html><body><p>단위 : {unit}</p>{extra}<table>{body}</table></body></html>"


_HEAD = ["정정항목", "정정전", "정정후"]
_NUMBERED_IN_OTHER_WARNING = ("정정 표에서 비교 절 안에 번호 붙은 지표 행(예: 2. 영업이익)이 있어 당기 값으로 읽지 않았다 — "
                              "원문 정정 표를 직접 확인할 것")


# ── ① 절 머리 ────────────────────────────────────────────────────────────────
def test_percent_section_rows_are_not_taken_as_amounts():
    """당기실적 절의 영업이익 금액 칸이 비어('-') 있으면, 뒤 「전기대비 증감율(%)」 절의 같은 라벨 행(%)을 금액으로 읽던 결함."""
    html = _table([
        _HEAD,
        ["1. 연결실적내용", "-", "-"],
        ["당기실적(26.2Q)", "-", "-"],
        ["- 매출액(당해실적)", "13,000", "13,937"],
        ["- 영업이익(당해실적)", "-", "-"],
        ["전기대비 증감율(%)", "-", "-"],
        ["- 매출액(당해실적)", "13.54", "21.72"],
        ["- 영업이익(당해실적)", "33.58", "40.35"],
        ["전년동기대비 증감율(%)", "-", "-"],
        ["- 영업이익(당해실적)", "77.32", "86.31"],
        ["2. 정보제공내역", "-", "-"],
    ])
    h = parse_provisional_earnings(html, _NM)["headline"]
    assert h["revenue"] == {"value_krw": 13_937 * 1e8, "prior_value_krw": 13_000 * 1e8}
    assert "operating_profit" not in h or h["operating_profit"]["value_krw"] is None   # 40.35 억원이 아니다


def test_prior_year_amount_section_is_not_the_current_period():
    """「전년동기실적」 절 행이 당기 절 앞에 와도 당기 절 행만 채택한다."""
    html = _table([
        _HEAD,
        ["전년동기실적", "-", "-"],
        ["매출액", "9,000", "9,100"],
        ["당기실적", "-", "-"],
        ["매출액", "13,000", "13,937"],
    ])
    h = parse_provisional_earnings(html, _NM)["headline"]
    assert h["revenue"]["value_krw"] == 13_937 * 1e8


def test_table_without_section_rows_still_parses():
    """절 머리행이 없는 단순 표(정정전|정정후 두 열)는 전과 같다."""
    html = _table([["구분", "정정전", "정정후"], ["매출액", "17,000", "17,150"], ["영업이익", "8,800", "8,949"]])
    h = parse_provisional_earnings(html, _NM)["headline"]
    assert h["revenue"] == {"value_krw": 17_150 * 1e8, "prior_value_krw": 17_000 * 1e8}
    assert h["operating_profit"] == {"value_krw": 8_949 * 1e8, "prior_value_krw": 8_800 * 1e8}


def test_rowspan_expanded_section_column_keeps_the_rows():
    """첫 열이 절 이름, 둘째 열이 지표인 꼴(rowspan 을 펼친 「당기실적 | 매출액 | 정정전 | 정정후」)은 절은 갱신하되 행은 읽는다.
    절 판정만 하고 건너뛰면 headline 이 빈다(옛 코드는 같은 표에서 아래 값을 냈다)."""
    html = _table([
        ["정정항목", "정정항목", "정정전", "정정후"],
        ["당기실적", "매출액", "13,000", "13,937"],
        ["당기실적", "영업이익", "4,000", "4,518"],
        ["전기대비 증감율(%)", "매출액", "13.54", "21.72"],
        ["전기대비 증감율(%)", "영업이익", "33.58", "40.35"],
    ])
    p = parse_provisional_earnings(html, _NM)
    assert p["kind"] == "financial"
    assert p["headline"]["revenue"] == {"value_krw": 13_937 * 1e8, "prior_value_krw": 13_000 * 1e8}
    assert p["headline"]["operating_profit"] == {"value_krw": 4_518 * 1e8, "prior_value_krw": 4_000 * 1e8}   # 증감율 절 40.35 가 아니다


# ── ① 손익구조 정정(I001) 어휘: 당해사업연도·직전사업연도·증감금액·증감비율(%) ──────────────────────
_STRUCT_CORR_NM = "[기재정정]" + _STRUCT_NM


def test_struct_correction_section_rows_do_not_leak_prior_year_or_percent():
    """절 행 꼴 — 당해사업연도 절의 영업이익 칸이 '-' 이면 뒤 직전사업연도·증감비율(%) 절의 같은 라벨 행을 금액으로 읽던 결함."""
    html = _table([
        _HEAD,
        ["당해사업연도", "-", "-"],
        ["- 매출액", "1,000", "1,200"],
        ["- 영업이익", "-", "-"],
        ["직전사업연도", "-", "-"],
        ["- 매출액", "900", "900"],
        ["- 영업이익", "80", "80"],
        ["증감금액", "-", "-"],
        ["- 영업이익", "5", "6"],
        ["증감비율(%)", "-", "-"],
        ["- 영업이익", "25.0", "50.0"],
    ], unit="천원")
    p = parse_provisional_earnings(html, _STRUCT_CORR_NM)
    assert p["headline"]["revenue"] == {"value_krw": 1_200 * 1e3, "prior_value_krw": 1_000 * 1e3}
    assert "operating_profit" not in p["headline"]   # 직전 80·증감 6·% 50.0 어느 것도 영업이익 value 가 아니다


def test_struct_correction_division_column_does_not_leak_percent():
    """구분 열 꼴 — 「- 영업이익 | 증감비율(%) | 정정전 | 정정후」: 둘째 열이 절 이름이면 그 행은 당해 금액이 아니다."""
    html = _table([
        ["정정항목", "구분", "정정전", "정정후"],
        ["- 매출액", "당해사업연도", "1,000", "1,200"],
        ["- 영업이익", "당해사업연도", "-", "-"],
        ["- 영업이익", "직전사업연도", "80", "80"],
        ["- 영업이익", "증감금액", "5", "6"],
        ["- 영업이익", "증감비율(%)", "25.0", "50.0"],
    ], unit="천원")
    p = parse_provisional_earnings(html, _STRUCT_CORR_NM)
    assert p["headline"]["revenue"] == {"value_krw": 1_200 * 1e3, "prior_value_krw": 1_000 * 1e3}
    assert "operating_profit" not in p["headline"]


def test_struct_correction_reads_period_from_start_end_dates():
    """손익구조 정정은 연간 기간이다 — 「시작일 … 종료일 …」 에서 사업연도를 읽는다(원공시 경로와 같은 규칙)."""
    html = _table([_HEAD, ["- 매출액", "1,000", "1,200"]], unit="천원",
                  extra="<p>시작일 2025-01-01 종료일 2025-12-31</p>")
    p = parse_provisional_earnings(html, _STRUCT_CORR_NM)
    assert p["provisional_type"] == "fiscal_year_change"
    assert p["period"] == {"start": "2025-01-01", "end": "2025-12-31"}
    assert p["period_kind"] == "annual" and p["fiscal_year"] == 2025


# ── ② 열 머리 ────────────────────────────────────────────────────────────────
def test_after_column_dash_is_none_not_the_before_value():
    html = _table([
        _HEAD,
        ["매출액", "17,000", "17,150"],
        ["영업이익", "8,800", "-"],
        ["당기순이익", "7,000", ""],
    ])
    h = parse_provisional_earnings(html, _NM)["headline"]
    assert h["revenue"]["value_krw"] == 17_150 * 1e8
    assert h["operating_profit"] == {"value_krw": None, "prior_value_krw": 8_800 * 1e8}   # 정정전 값이 value 로 나가지 않는다
    assert h["net_income"] == {"value_krw": None, "prior_value_krw": 7_000 * 1e8}


def test_columns_follow_the_header_row_not_the_last_number():
    """정정후 뒤에 숫자 비고 열이 있어도, 정정전·정정후 열 위치는 머리 행이 정한다."""
    html = _table([
        ["구분", "정정전", "정정후", "비고"],
        ["매출액", "17,000", "17,150", "3"],
    ])
    h = parse_provisional_earnings(html, _NM)["headline"]
    assert h["revenue"] == {"value_krw": 17_150 * 1e8, "prior_value_krw": 17_000 * 1e8}


def test_header_order_after_before_is_respected():
    html = _table([["구분", "정정후", "정정전"], ["매출액", "17,150", "17,000"]])
    h = parse_provisional_earnings(html, _NM)["headline"]
    assert h["revenue"] == {"value_krw": 17_150 * 1e8, "prior_value_krw": 17_000 * 1e8}


# ── ③ 접두 ───────────────────────────────────────────────────────────────────
def test_structure_change_report_with_correction_prefix_is_detected():
    assert _is_structure_change_report(_STRUCT_NM)
    assert _is_structure_change_report("매출액또는손익구조30%(대규모법인은15%)이상변동")
    assert _is_structure_change_report("[기재정정]" + _STRUCT_NM)
    assert _is_structure_change_report("[첨부정정] 매출액 또는 손익구조 30%(대규모법인은 15%) 이상 변경")
    assert not _is_structure_change_report("[기재정정]단일판매ㆍ공급계약체결")
    assert _is_structure_change_report("[첨부추가]" + _STRUCT_NM)
    assert not _is_structure_change_report("[투자유의]" + _STRUCT_NM)   # 정정 접두 목록 밖의 대괄호 접두는 떼지 않는다
    assert not _is_structure_change_report("")


def test_correction_of_structure_change_is_a_candidate_and_wins_by_date():
    class _C:
        async def search_filings(self, **kw):
            if kw.get("pblntf_detail_ty") != "I001":
                return {"list": []}
            return {"list": [
                {"rcept_no": "20261020000002", "rcept_dt": "20261020", "report_nm": "[기재정정]" + _STRUCT_NM},
                {"rcept_no": "20261001000001", "rcept_dt": "20261001", "report_nm": _STRUCT_NM},
            ]}

    got = asyncio.run(_find_provisional_candidates(_C(), "00000000", "20260901", "20261023"))
    assert [x["rcept_no"] for x in got] == ["20261020000002", "20261001000001"]   # 정정 공시가 최신


def test_attachment_only_structure_correction_goes_last():
    class _C:
        async def search_filings(self, **kw):
            if kw.get("pblntf_detail_ty") != "I001":
                return {"list": []}
            return {"list": [
                {"rcept_no": "3", "rcept_dt": "20261021", "report_nm": "[첨부정정]" + _STRUCT_NM},
                {"rcept_no": "2", "rcept_dt": "20261020", "report_nm": "[기재정정]" + _STRUCT_NM},
                {"rcept_no": "1", "rcept_dt": "20261001", "report_nm": _STRUCT_NM},
            ]}

    got = asyncio.run(_find_provisional_candidates(_C(), "00000000", "20260901", "20261023"))
    assert [x["rcept_no"] for x in got] == ["2", "1", "3"]


def test_structure_change_correction_body_reads_the_correction_table():
    """접두를 떼도 정정 공시 본문(정정전|정정후 표)은 정정 표 파서가 읽는다 — 원공시 서식 파서로 가서 headline 이 비지 않는다."""
    html = _table([_HEAD, ["매출액", "1,000", "1,200"], ["영업이익", "100", "150"]])
    p = parse_provisional_earnings(html, "[기재정정]" + _STRUCT_NM)
    assert p["correction"] is True
    assert p["headline"]["revenue"] == {"value_krw": 1_200 * 1e8, "prior_value_krw": 1_000 * 1e8}


def test_structure_change_original_still_uses_structure_parser():
    html = """<table>
  <tr><td>3. 매출액 또는 손익구조 변동내용(단위:천원)</td><td>당해사업연도</td><td>직전사업연도</td><td>증감금액</td><td>증감비율(%)</td><td>흑자적자전환여부</td></tr>
  <tr><td>- 매출액</td><td>177,803,500</td><td>166,650,466</td><td>11,153,035</td><td>6.7</td><td>-</td></tr>
  <tr><td>- 영업이익</td><td>8,415,374</td><td>6,641,339</td><td>1,774,035</td><td>26.7</td><td>-</td></tr>
</table>"""
    p = parse_provisional_earnings(html, _STRUCT_NM)
    assert p["provisional_type"] == "fiscal_year_change" and not p.get("correction")
    assert p["headline"]["revenue"]["value_krw"] == 177_803_500_000


# ── 재감수(2차) ──────────────────────────────────────────────────────────────
def test_standard_three_column_table_does_not_judge_value_cell_as_section():
    """정정항목|정정전|정정후 표에서 정정전 칸이 「전기대비증감율: 5 당해실적: 100」 한 줄 요약이어도 그 칸을 절 이름으로 보지 않는다 (PROV-R2-S1)."""
    html = _table([_HEAD, ["매출액", "전기대비증감율: 5 당해실적: 100", "전기대비증감율: 6 당해실적: 120"]])
    h = parse_provisional_earnings(html, _NM)["headline"]
    assert h["revenue"] == {"value_krw": 120 * 1e8, "prior_value_krw": 100 * 1e8}


def test_structure_correction_period_prefers_start_end_over_quarter_text():
    """손익구조 정정 본문에 「2025년 4분기」 문구가 있어도 기간은 시작일·종료일(연간)로, 석 달 기간에 연간 표지가 붙지 않는다 (PROV-R2-S2)."""
    html = _table([_HEAD, ["매출액", "1,000", "1,200"]], unit="천원",
                  extra="<p>2025년 4분기 사업연도 시작일 2025-01-01 종료일 2025-12-31</p>")
    p = parse_provisional_earnings(html, "[기재정정]" + _STRUCT_NM)
    assert p["period"] == {"start": "2025-01-01", "end": "2025-12-31"}
    assert p["period_kind"] == "annual"


def test_numbered_metric_label_is_not_a_numbered_heading():
    """「1. 매출액」처럼 번호를 뗀 나머지가 지표 라벨이면 큰 제목이 아니라 지표 행이다 (PROV-R2-N1)."""
    html = _table([_HEAD, ["1. 매출액", "100", "120"], ["2. 정보제공내역", "-", "-"]])
    h = parse_provisional_earnings(html, _NM)["headline"]
    assert h["revenue"] == {"value_krw": 120 * 1e8, "prior_value_krw": 100 * 1e8}


# ── 3차 감수 ─────────────────────────────────────────────────────────────────
def test_numbered_metric_row_after_comparison_section_is_not_read_as_current():
    """비교 절(전년동기실적) 뒤에 「2. 영업이익」 번호 붙은 지표 행이 오면, 5차 규칙(명시적 절 머리행이 나온 뒤의 번호 붙은 지표 행은 절을 바꾸지 않는다)에
    따라 뒤 하위 행 「- 영업이익 | 10 | 12」 는 여전히 전년동기 절이라 버린다 — 영업이익은 비고 경고가 붙는다 (PROV-R3-S1 ①, 5차에서 기대가 바뀜).
    이유: 이 꼴은 구조상 A1(적자전환으로 % 절 안 「2. 영업이익 | - | -」 뒤에 오는 % 값 행)과 가를 수 없다 — 틀린 값(%·전년 값을 금액으로)이
    빈 값보다 나쁘므로 빈 값 + 경고를 택한다. 이전 기대(영업이익 12억)는 4차까지의 규칙이었다."""
    html = _table([_HEAD, ["1. 매출액", "-", "-"], ["당기실적", "-", "-"], ["- 매출액", "100", "120"],
                   ["전년동기실적", "-", "-"], ["- 매출액", "90", "95"],
                   ["2. 영업이익", "-", "-"], ["- 영업이익", "10", "12"]])
    p = parse_provisional_earnings(html, _NM)
    h = p["headline"]
    assert h["revenue"] == {"value_krw": 120 * 1e8, "prior_value_krw": 100 * 1e8}
    assert "operating_profit" not in h
    assert p["parse_warnings"] == [_NUMBERED_IN_OTHER_WARNING]


def test_numbered_metric_row_with_values_inside_percent_section_is_not_adopted():
    """값이 든 번호 붙은 지표 행은 데이터 행이라 절을 바꾸지 않는다 — 「전기대비증감율(%)」 절 안의 「2. 영업이익 | 10 | 12」 는 % 절 값이므로
    채택하지 않는다(기대값: 매출 120억, 영업이익 없음) (PROV-R3-S1 ② → 4차 규칙으로 바뀜: 이전 시험은 영업이익 12억을 요구했으나,
    틀린 값은 빈 값보다 나쁘다 — 같은 꼴의 R4-S1 이 % 값을 금액으로 내는 회귀였다)."""
    html = _table([_HEAD, ["1. 매출액", "100", "120"], ["전기대비증감율(%)", "5", "6"], ["2. 영업이익", "10", "12"]])
    h = parse_provisional_earnings(html, _NM)["headline"]
    assert h["revenue"] == {"value_krw": 120 * 1e8, "prior_value_krw": 100 * 1e8}
    assert "operating_profit" not in h


# ── 4차 감수: 번호 붙은 지표 행 — 값 칸이 모두 비었거나 '-' 면 제목 행(절 초기화), 값이 있으면 데이터 행(절 유지) ──
def test_numbered_metric_title_before_any_section_header_is_a_title():
    """명시적 절 머리행(당기실적·전기대비증감율 …)이 나오기 전의 값 없는 번호 붙은 지표 행은 제목(neutral)이다 — 뒤 하위 행이 읽힌다.
    기대값: 영업이익 12억 · 경고 없음 (PROV-R3-S1 에서 갈라진, 5차 규칙의 앞쪽 반)."""
    html = _table([_HEAD, ["2. 영업이익", "-", "-"], ["- 영업이익", "10", "12"]])
    p = parse_provisional_earnings(html, _NM)
    assert p["headline"]["operating_profit"] == {"value_krw": 12 * 1e8, "prior_value_krw": 10 * 1e8}
    assert "parse_warnings" not in p


def test_numbered_metric_data_rows_inside_percent_section_are_not_taken_as_amounts():
    """당기 절 → 번호 지표 데이터 행 → 「전기대비증감율(%)」 절 안의 번호 지표 행(5·6 / 33.5·40.3)은 % 라서 채택하지 않는다.
    기대값: 매출 120억(당기 절 값), 영업이익 없음(「2. 영업이익 | -  | -」 는 제목 행, % 절의 값 40.3 은 금액이 아님) (PROV-R4-S1 ①)."""
    html = _table([_HEAD, ["당기실적", "-", "-"], ["1. 매출액", "100", "120"], ["2. 영업이익", "-", "-"],
                   ["전기대비증감율(%)", "-", "-"], ["1. 매출액", "5", "6"], ["2. 영업이익", "33.5", "40.3"]])
    p = parse_provisional_earnings(html, _NM)
    h = p["headline"]
    assert h["revenue"] == {"value_krw": 120 * 1e8, "prior_value_krw": 100 * 1e8}
    assert "operating_profit" not in h
    assert p["parse_warnings"] == [_NUMBERED_IN_OTHER_WARNING]


def test_numbered_metric_data_row_inside_prior_period_section_does_not_beat_current():
    """전년동기 절 안의 번호 지표 데이터 행(「1. 매출액 | 90 | 95」)은 비교 기간 값이라 버리고, 뒤 당기 절의 「1. 매출액 | 100 | 120」 을 읽는다.
    기대값: 매출 120억(전년동기 95억이 아님) (PROV-R4-S1 ②)."""
    html = _table([_HEAD, ["전년동기실적", "-", "-"], ["1. 매출액", "90", "95"],
                   ["당기실적", "-", "-"], ["1. 매출액", "100", "120"]])
    h = parse_provisional_earnings(html, _NM)["headline"]
    assert h["revenue"] == {"value_krw": 120 * 1e8, "prior_value_krw": 100 * 1e8}


def test_numbered_metric_data_rows_in_normal_order_keep_current_section():
    """대조: 당기 절 안에 번호 지표 데이터 행이 줄줄이 오고 그 뒤 % 절이 오는 정상 순서는 당기 값 그대로.
    기대값: 매출 120억 · 영업이익 12억 (이전 HEAD 와 같음)."""
    html = _table([_HEAD, ["당기실적", "-", "-"], ["1. 매출액", "100", "120"], ["2. 영업이익", "10", "12"],
                   ["전기대비증감율(%)", "-", "-"], ["1. 매출액", "5", "6"], ["2. 영업이익", "3", "4"]])
    h = parse_provisional_earnings(html, _NM)["headline"]
    assert h["revenue"]["value_krw"] == 120 * 1e8
    assert h["operating_profit"]["value_krw"] == 12 * 1e8


def test_malformed_grid_row_with_extra_cell_still_judges_division_column():
    """머리행은 3칸인데 데이터 행만 4칸인 어긋난 격자에서 「증감비율(%)」 구분 칸 행의 % 가 금액으로 나가지 않는다 (PROV-R3-N1)."""
    html = _table([_HEAD, ["- 매출액", "1,000", "1,200"], ["- 영업이익", "증감비율(%)", "25.0", "50.0"]], unit="천원")
    h = parse_provisional_earnings(html, "[기재정정]" + _STRUCT_NM)["headline"]
    assert h["revenue"]["value_krw"] == 1_200 * 1e3
    assert "operating_profit" not in h


# ── 5차 감수: 명시적 절 머리행이 나온 뒤에는 번호 붙은 지표 행이 절을 바꾸지 않는다 (PROV-R5-M1) ──
def test_percent_section_with_blank_numbered_row_does_not_leak_percent_as_amount():
    """A1: 적자전환 등으로 % 절 안 「1. 매출액 | - | -」 가 비어도, 그 행이 절을 neutral 로 되돌리지 않는다 — 뒤 「2. 영업이익 | 33.5 | 40.3」(%)을
    금액으로 읽지 않는다. 기대값: 매출 120억, 영업이익 없음 + 경고 (origin/main 도 비워 둔 꼴 — 틀린 값 회귀 방지)."""
    html = _table([_HEAD, ["당기실적", "-", "-"], ["1. 매출액", "100", "120"], ["2. 영업이익", "-", "-"],
                   ["전기대비증감율(%)", "-", "-"], ["1. 매출액", "-", "-"], ["2. 영업이익", "33.5", "40.3"]])
    p = parse_provisional_earnings(html, _NM)
    assert p["headline"]["revenue"] == {"value_krw": 120 * 1e8, "prior_value_krw": 100 * 1e8}
    assert "operating_profit" not in p["headline"]
    assert p["parse_warnings"] == [_NUMBERED_IN_OTHER_WARNING]


def test_percent_section_blank_numbered_row_with_empty_cells_is_same():
    """A6: 값 칸이 빈칸과 '-' 가 섞여도 같다 (기대값: 영업이익 없음 + 경고)."""
    html = _table([_HEAD, ["당기실적", "-", "-"], ["1. 매출액", "100", "120"], ["전기대비증감율(%)", "-", "-"],
                   ["1. 매출액", "", "-"], ["2. 영업이익", "33.5", "40.3"]])
    p = parse_provisional_earnings(html, _NM)
    assert "operating_profit" not in p["headline"]
    assert p["parse_warnings"] == [_NUMBERED_IN_OTHER_WARNING]


def test_prior_first_blank_numbered_row_does_not_let_prior_value_beat_current():
    """A2b: 전년동기 절이 먼저 오고 그 안의 「1. 영업이익 | - | -」 뒤 「2. 당기순이익 | 8 | 9」(전년 값)가 당기 절 값(12)보다 먼저 들어가던 꼴.
    기대값: 당기순이익 12억(전년동기 9억이 아님) — 뒤 당기 절에서 채워지므로 경고는 없다."""
    html = _table([_HEAD, ["전년동기실적", "-", "-"], ["1. 영업이익", "-", "-"], ["2. 당기순이익", "8", "9"],
                   ["당기실적", "-", "-"], ["1. 영업이익", "10", "12"], ["2. 당기순이익", "10", "12"]])
    p = parse_provisional_earnings(html, _NM)
    h = p["headline"]
    assert h["net_income"] == {"value_krw": 12 * 1e8, "prior_value_krw": 10 * 1e8}
    assert h["operating_profit"] == {"value_krw": 12 * 1e8, "prior_value_krw": 10 * 1e8}
    assert "parse_warnings" not in p


def test_standard_order_with_numbered_rows_in_percent_section_has_no_warning():
    """대조(A4): 당기 절이 다 채워진 정상 순서에서 % 절 안 번호 지표 행은 버려도 빠진 지표가 없으므로 경고를 달지 않는다."""
    html = _table([_HEAD, ["당기실적", "-", "-"], ["1. 매출액", "100", "120"], ["2. 영업이익", "10", "12"],
                   ["전기대비증감율(%)", "-", "-"], ["1. 매출액", "5", "6"], ["2. 영업이익", "-", "-"], ["3. 당기순이익", "33", "40"]])
    p = parse_provisional_earnings(html, _NM)
    assert p["headline"]["revenue"]["value_krw"] == 120 * 1e8
    assert p["headline"]["operating_profit"]["value_krw"] == 12 * 1e8
    assert "net_income" not in p["headline"]
    # 순이익은 % 절 값만 있고 비어 있다 — 버린 지표가 빠졌으므로 경고는 달린다
    assert p["parse_warnings"] == [_NUMBERED_IN_OTHER_WARNING]


def test_numbered_other_section_warning_reaches_service_warnings(monkeypatch):
    """경고는 응답의 기존 warnings 에 한 줄로 실리고, 새 응답 키(parse_warnings)는 data 에 남지 않는다."""
    from open_proxy_mcp.services import provisional_earnings as pe
    from open_proxy_mcp.services.company import CompanyResolution
    from open_proxy_mcp.services.contracts import AnalysisStatus

    html = _table([_HEAD, ["당기실적", "-", "-"], ["1. 매출액", "100", "120"], ["2. 영업이익", "-", "-"],
                   ["전기대비증감율(%)", "-", "-"], ["1. 매출액", "-", "-"], ["2. 영업이익", "33.5", "40.3"]])

    class _Client:
        async def search_filings(self, **kw):
            if kw.get("pblntf_detail_ty") != "I002":
                return {"list": []}
            return {"list": [{"rcept_no": "20261016800456", "rcept_dt": "20261016", "report_nm": _NM}]}

        async def get_document_cached(self, rcept_no):
            return {"html": html}

        async def get_company_info(self, corp_code):
            return {"acc_mt": "12"}

    async def resolve(q, *a, **k):
        return CompanyResolution(status=AnalysisStatus.EXACT, query=q, candidates=[],
                                 selected={"corp_code": "00000001", "corp_name": "시험", "stock_code": "000001"})

    monkeypatch.setattr(pe, "resolve_company_query", resolve)
    monkeypatch.setattr("open_proxy_mcp.dart.client.get_dart_client", lambda: _Client())
    out = asyncio.run(pe.build_provisional_earnings_payload("시험"))
    assert _NUMBERED_IN_OTHER_WARNING in out["warnings"]
    assert "parse_warnings" not in out["data"]
    assert "operating_profit" not in out["data"]["headline"]
