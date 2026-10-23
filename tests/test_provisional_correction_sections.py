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
