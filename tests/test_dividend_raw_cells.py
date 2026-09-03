# -*- coding: utf-8 -*-
"""자유서술 칸을 **통째로** 내보내는가 (2026-09-03 마스터 지시).

    「테이블에 특정 칸에 항상 들어가는건 상관없는데 어지간한건 그냥 셀을 통으로 반환하던지
     그래서 AI가 읽고 판단하게. 너무 파싱/키워드 기반이면 소용없어」

자리가 정해진 칸(날짜·DPS·금액)은 열로 내도 된다. 그러나 회사가 무엇이든 적는 칸은
정규식으로 하나를 뽑는 순간 나머지가 사라진다. 260903 이전 세 곳이 그랬다 —
  (1) 적재기가 `has_special` 일 때만 비고를 200자까지 남겼다,
  (2) `payment_counts()` 가 `FILTER (WHERE has_special)` 로 원문을 걸렀다,
  (3) 두 렌더러가 파생 불리언만 찍고 원문을 한 글자도 내보내지 않았다.
여기서 막는 것은 **그 구조의 재발**이다 — 「특별배당이 있는 결의만 원문을 준다」로
되돌아가면 아래 테스트가 깨진다.
"""
from __future__ import annotations

import asyncio

from open_proxy_mcp.services import dividend_data as dd
from open_proxy_mcp.tools import dividend_data as dd_tool
from open_proxy_mcp.tools._shared import raw_cell
from open_proxy_mcp.tools.dividend_disclosure import _render


# 실측 형태 — 특별배당 문구가 **없는** 평범한 비고. 옛 구조에서는 통째로 버려지던 칸이다.
# (자기주식 제외 산정·주총 갈음·변동 단서는 서식에 칸이 없어 여기에만 적힌다.)
_PLAIN = (
    "- 상기 배당금총액은 자기주식을 제외한 배당 대상 주식수 기준으로 산정한 금액입니다. "
    "- 상기 내용은 외부감사인의 감사결과 및 주주총회 승인과정에서 변경될 수 있습니다. "
    "- 당사는 정관에 따라 이사회 결의로 주주총회를 갈음합니다."
)
_SPECIAL = "- 금번 결산배당은 특별배당금 성격의 1,578원을 더하여 결정되었습니다."


# ───────────────────────────────────────────────────────── services 계층 ──
def _stub_rows(monkeypatch, rows):
    monkeypatch.setattr(dd, "_rows", lambda sql, params=(): rows)


def _decision_row(fy, board, rcept, dps, *, special=False, remarks=_PLAIN,
                  kind="결산배당", anomaly=None, amended=False):
    #  fiscal_year, board_date, record_date, dividend_type_filed, dividend_type,
    #  dps_common, total_amount, rcept_no, amended, anomaly, has_special, remarks
    return (fy, board, f"{fy}-12-31", kind, kind, dps, dps * 1000, rcept,
            amended, anomaly, special, remarks)


def test_payment_counts_returns_every_remarks_cell_not_just_flagged_ones(monkeypatch):
    """🔴 회귀 방지의 핵심 — 플래그가 꺼진 결의도 원문을 그대로 갖고 나온다."""
    _stub_rows(monkeypatch, [
        _decision_row(2024, "2025-01-30", "20250130800001", 400),
        _decision_row(2024, "2024-07-25", "20240725800002", 300, kind="분기배당"),
    ])
    out = dd.payment_counts("00126380", 2020, 2025)

    assert out["status"] == "ok"
    assert len(out["decisions"]) == 2
    assert all(d["has_special"] is False for d in out["decisions"])
    assert all(d["remarks"] == _PLAIN for d in out["decisions"]), \
        "플래그가 꺼진 결의의 비고가 사라졌다 — 옛 FILTER (WHERE has_special) 구조로 되돌아갔다"


def test_payment_counts_does_not_truncate_a_long_remarks_cell(monkeypatch):
    """실측 최대 1,512자. 200자 자르기(옛 `special_note`)가 돌아오면 여기서 걸린다."""
    long_text = "- 배당 재원에 관한 설명. " * 90          # 1,000자 이상
    _stub_rows(monkeypatch, [_decision_row(2024, "2025-01-30", "20250130800001", 400,
                                           remarks=long_text)])
    got = dd.payment_counts("00126380", 2020, 2025)["decisions"][0]["remarks"]
    assert got == long_text and len(got) > 200


def test_payment_counts_folds_years_without_losing_the_per_decision_rows(monkeypatch):
    """연도 합계와 결의 원문을 **둘 다** 낸다 — 접은 값만 남기면 원문이 다시 사라진다."""
    _stub_rows(monkeypatch, [
        _decision_row(2024, "2025-01-30", "20250130800001", 400, special=True,
                      remarks=_SPECIAL),
        _decision_row(2024, "2024-07-25", "20240725800002", 300, kind="분기배당",
                      anomaly="구분불일치"),
        _decision_row(2023, "2024-01-30", "20240130800003", 350, amended=True),
    ])
    out = dd.payment_counts("00126380", 2020, 2025)

    assert [r["fiscal_year"] for r in out["rows"]] == [2024, 2023]      # 최신 연도부터
    fy24 = out["rows"][0]
    assert fy24["n_payments"] == 2
    assert fy24["dps_sum"] == 700 and fy24["total_sum"] == 700_000
    assert fy24["kinds_filed"] == ["결산배당", "분기배당"] or \
           sorted(fy24["kinds_filed"]) == sorted(["결산배당", "분기배당"])
    assert fy24["anomalies"] == ["구분불일치"]
    assert fy24["has_special"] is True and fy24["amended"] is False
    assert out["rows"][1]["amended"] is True
    assert len(out["decisions"]) == 3


def test_payment_counts_keeps_an_all_empty_year_empty_instead_of_zero(monkeypatch):
    """DPS 가 전부 빈 해를 `0원` 으로 만들지 않는다 — 「무배당」과 「미기재」는 다르다."""
    row = list(_decision_row(2024, "2025-01-30", "20250130800001", 0))
    row[5] = row[6] = None                                   # dps_common, total_amount
    _stub_rows(monkeypatch, [tuple(row)])
    fy = dd.payment_counts("00126380", 2020, 2025)["rows"][0]
    assert fy["dps_sum"] is None and fy["total_sum"] is None


# ─────────────────────────────────────────────── tools/dividend_data 렌더 ──
def _firm_md(monkeypatch, decisions, rows=None):
    pay = {"status": "ok", "complete_years": [2020, 2021, 2022, 2023, 2024],
           "rows": rows if rows is not None else [
               {"fiscal_year": 2024, "n_payments": len(decisions), "dps_sum": 400,
                "total_sum": 400_000, "kinds_filed": ["결산배당"], "amended": False,
                "anomalies": [], "has_special": any(d["has_special"] for d in decisions)}],
           "decisions": decisions}
    return dd_tool._render_firm("테스트회사", "005930", {"status": "ok"}, pay)


def test_firm_render_prints_the_whole_remarks_cell_for_every_decision(monkeypatch):
    md = _firm_md(monkeypatch, [
        {"fiscal_year": 2024, "board_date": "2025-01-30", "record_date": "2024-12-31",
         "dividend_type_filed": "결산배당", "dividend_type": "결산배당", "dps_common": 400.0,
         "total_amount": 400_000.0, "rcept_no": "20250130800001", "amended": False,
         "anomaly": None, "has_special": False, "remarks": _PLAIN},
    ])
    assert "### 결정공시 비고 원문" in md
    assert _PLAIN in md, "특별배당 플래그가 꺼졌다고 비고를 감췄다"
    assert "20250130800001" in md and "FY2024" in md
    # 파생 플래그는 남되, 정본이 아님을 반드시 밝힌다.
    assert "특별배당(힌트)" in md and "정본은 아래 비고 원문이다" in md


def test_firm_render_marks_an_empty_remarks_cell_instead_of_staying_silent(monkeypatch):
    """빈 칸에 침묵하면 「특이사항 없음」으로 읽힌다 — 그건 우리가 아는 사실이 아니다."""
    md = _firm_md(monkeypatch, [
        {"fiscal_year": 2024, "board_date": "2025-01-30", "record_date": "2024-12-31",
         "dividend_type_filed": "결산배당", "dividend_type": "결산배당", "dps_common": 400.0,
         "total_amount": 400_000.0, "rcept_no": "20250130800001", "amended": False,
         "anomaly": None, "has_special": False, "remarks": None},
    ])
    assert "「특이사항 없음」으로 읽지 말 것" in md
    assert "비고 원문이 없는 결의 1건" in md


def test_firm_render_does_not_show_a_failed_lookup_as_no_dividend():
    """🔴 260903 실측 사고 — 표에서 죽은 칸을 지우자 배포본 쿼리가 깨졌는데, 화면에는
    「결정공시 집계가 이 구간에 없다」로 나왔다. 실패를 「안 했다」로 읽히게 두지 않는다."""
    md = dd_tool._render_firm("테스트회사", "005930", {"status": "ok"},
                              {"status": "db_error"})
    assert "조회 실패" in md and "모른다" in md
    assert "결정공시 집계가 이 구간에 없다" not in md


# ──────────────────────────────────── tools/dividend_disclosure 렌더 ──
def _disclosure_payload(scope: str, *, remarks: str | None = _PLAIN,
                        items: list[dict] | None = None) -> dict:
    data: dict = {
        "canonical_name": "테스트회사", "stock_code": "005930",
        "summary": {"fiscal_year": 2025, "cash_dps": 1800, "total_amount_mil": 9_800_000,
                    "items": items or []},
        "latest_decisions": [{
            "rcept_dt": "2026-02-05", "dividend_type": "결산배당", "dps_common": 450,
            "record_date": "2026-03-31", "rcept_no": "20260205000123",
            "has_special": False, "differential_dividend": False, "remarks": remarks,
        }],
        "policy_signals": {"trend": "stable", "has_quarterly_pattern": False,
                           "has_special_dividend": False, "latest_change_pct": 0.0},
    }
    if scope == "detail":
        data["detail"] = {"latest_decisions": data["latest_decisions"],
                          "decision_count": 1, "raw_decision_count": 1}
    return {"status": "ok", "subject": "테스트회사", "data": data, "warnings": []}


def test_disclosure_summary_prints_the_decision_remarks_verbatim():
    md = _render(_disclosure_payload("summary"), "summary")
    assert "### 배당결정 비고 원문 (11. 기타 투자판단과 관련한 중요사항)" in md
    assert _PLAIN in md
    # 파생 요약은 남기되 정본이 아님을 밝힌다 — 축소가 아니라 라벨링이다.
    assert "## 정책 신호" in md and "정본이 아니다" in md
    assert "- 특별배당 이력: 아니오" in md and "**아니오 = 없다가 아니다.**" in md


def test_disclosure_detail_prints_the_raw_alot_matter_rows():
    """연간 요약 숫자는 이 행들에서 골라낸 파생값이다 — 원문 행을 나란히 낸다."""
    items = [
        {"category": "주당 현금배당금(원)", "stock_type": "보통주", "current": "1,800",
         "previous": "1,444", "before_previous": "1,444"},
        {"category": "(연결)현금배당성향(%)", "stock_type": "-", "current": "30.0",
         "previous": "25.0", "before_previous": "17.0"},
    ]
    md = _render(_disclosure_payload("detail", items=items), "detail")
    assert "### 사업보고서 배당 항목 원문 (`alotMatter`)" in md
    assert "| 주당 현금배당금(원) | 보통주 | 1,800 | 1,444 | 1,444 |" in md
    assert "(연결)현금배당성향(%)" in md
    assert "**어긋나면 이 표가 정본이다.**" in md
    # detail 에서도 비고 원문은 그대로 나온다.
    assert _PLAIN in md


def test_disclosure_omits_the_remarks_section_when_no_decision_has_one():
    """빈 제목만 남기지 않는다 — 있는 것처럼 보이는 빈 절이 더 나쁘다."""
    md = _render(_disclosure_payload("summary", remarks=None), "summary")
    assert "### 배당결정 비고 원문" not in md


# ───────────────────────────────────────────────────────────── raw_cell ──
def test_raw_cell_protects_structure_without_dropping_content():
    assert raw_cell("가\n나\r\n다") == "가 나 다"
    assert raw_cell("a|b", inline=True) == "a\\|b"
    assert raw_cell("a|b") == "a|b"                 # 표 밖에서는 손대지 않는다
    assert raw_cell(None) == "" and raw_cell("  ") == ""
    long_text = "가" * 5000
    assert raw_cell(long_text) == long_text, "raw_cell 이 길이를 잘랐다"
