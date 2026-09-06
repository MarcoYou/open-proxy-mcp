"""매출(top line) 계정 선택 회귀 — DART 응답 경계 fixture (CLAUDE.md 규칙 15).

fixture 는 2026-09-06 에 받은 DART OpenAPI 응답 행 그대로다(fnlttSinglAcnt / fnlttSinglAcntAll,
사업보고서 11011, 2024 사업연도). 전체재무제표는 이 회귀에 필요한 BS·IS·CIS 행만 남겼다.

실측 사실(왜 이 테스트가 있나):
- 리파인(63991 정보서비스): 주요계정에 매출 행이 **없고**, 전체재무제표 「영업수익」은
  account_id 가 `-표준계정코드 미사용-`. 코드로만 찾으면 4개년 매출이 다 빈다.
- 삼성생명(65110): 「기타영업수익」(ord 28)이 「보험서비스수익」(63)·「일반보험서비스수익」
  (65, ifrs-full_InsuranceRevenue)보다 앞에 온다 — substring 첫 매칭이면 기타영업수익을 집는다.
- KB금융(64992): 「보험수익」이 두 행(dart_OperatingIncomeInsurance 11.456조 = 보험수익
  11.017조 + 재보험수익 0.439조). 은행지주의 매출은 보험수익이 아니다 — 영업수익 행이 없으니
  이자수익으로 내려가야 하고, 업종을 모르면(None) 아무것도 고르지 않아야 한다.
- 현대건설(41221): 「수익(매출액)」 ifrs-full_Revenue 가 ord 38 로 매출원가(17)·매출총이익(23)
  뒤에 온다.
- 롯데렌탈(76110): 「영업수익」 ifrs-full_Revenue. 세부행 「렌탈 및 기타수익」에 걸리면 안 된다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from open_proxy_mcp.services.financial_metrics import (  # noqa: E402
    _build_account_map,
    _build_account_map_all,
    _compute_metrics,
)
from open_proxy_mcp.services.revenue_account import pick_revenue_row, revenue_family  # noqa: E402

FIX = Path(__file__).resolve().parent / "dart_responses" / "dart"


def _rows(name: str) -> list[dict]:
    return json.load(open(FIX / name, encoding="utf-8"))["list"]


# ── 리파인: 주요계정 → None, 전체재무제표 폴백으로 영업수익 ─────────────────────────────

def test_refine_major_accounts_have_no_revenue_row():
    bs_is = _build_account_map(_rows("refine_377450_2024_11011_fnlttSinglAcnt.json"), induty_code="63991")
    assert bs_is["revenue"] is None
    assert bs_is["operating_profit"] == 20_505_136_523  # 매출만 없고 나머지는 정상


def test_refine_full_statement_falls_back_to_operating_revenue_without_standard_code():
    detail = _build_account_map_all(
        _rows("refine_377450_2024_11011_fnlttSinglAcntAll_OFS.json"), induty_code="63991")
    assert detail["revenue"] == 67_848_478_041
    assert detail["revenue_account_nm"] == "영업수익"
    assert detail["revenue_account_id"] is None          # -표준계정코드 미사용-
    assert detail["revenue_standard"] is True             # 영업수익은 매출액과 같은 뜻으로 읽는다


def test_refine_compute_metrics_uses_detail_revenue_and_reports_source():
    bs_is = _build_account_map(_rows("refine_377450_2024_11011_fnlttSinglAcnt.json"), induty_code="63991")
    detail = _build_account_map_all(
        _rows("refine_377450_2024_11011_fnlttSinglAcntAll_OFS.json"), induty_code="63991")
    m = _compute_metrics(bs_is=bs_is, bs_is_prev=None, detail=detail, detail_prev=None, indx_map=None,
                         induty_code="63991")
    assert m["revenue_krw"] == 67_848_478_041
    assert m["revenue_source"] == "fnlttSinglAcntAll"
    assert m["revenue_account_nm"] == "영업수익"
    assert m["operating_margin_pct"] == pytest.approx(30.22, abs=0.01)


def test_refine_prev_year_revenue_also_falls_back_so_yoy_is_not_one_sided():
    bs_is = _build_account_map(_rows("refine_377450_2024_11011_fnlttSinglAcnt.json"), induty_code="63991")
    detail = _build_account_map_all(
        _rows("refine_377450_2024_11011_fnlttSinglAcntAll_OFS.json"), induty_code="63991")
    detail_prev = _build_account_map_all(
        _rows("refine_377450_2024_11011_fnlttSinglAcntAll_OFS.json"), period="frmtrm", induty_code="63991")
    m = _compute_metrics(bs_is=bs_is, bs_is_prev=None, detail=detail, detail_prev=detail_prev,
                         indx_map=None, induty_code="63991")
    assert m["prev_revenue_krw"] == 66_484_437_442      # 2023 영업수익
    assert m["revenue_yoy_pct"] == pytest.approx(2.05, abs=0.01)


# ── 업종별 top line ───────────────────────────────────────────────────────────────

def test_insurer_picks_ifrs17_insurance_revenue_not_other_operating_income():
    pick = pick_revenue_row(_rows("samsunglife_032830_65110_2024_11011_fnlttSinglAcntAll_CFS_IS.json"), "65110")
    assert pick is not None
    assert pick.account_id == "ifrs-full_InsuranceRevenue"
    assert pick.account_nm == "일반보험서비스수익"
    assert int(pick.row["thstrm_amount"]) == 9_011_262_000_000
    assert pick.standard is False
    assert pick.basis == "보험수익"          # 표기는 표준 개념명 — 원문 「일반보험서비스수익」은 account_nm 에


def test_bank_holding_goes_to_interest_income_never_insurance_revenue():
    rows = _rows("kbfinancial_105560_64992_2024_11011_fnlttSinglAcntAll_CFS_IS.json")
    pick = pick_revenue_row(rows, "64992")
    assert pick is not None
    assert pick.account_nm == "이자수익"
    assert pick.account_id == "ifrs-full_RevenueFromInterest"
    assert int(pick.row["thstrm_amount"]) == 30_491_385_000_000
    assert pick.standard is False
    assert pick.basis == "이자수익"


def test_bank_holding_without_industry_picks_nothing():
    """업종을 모르면 이자수익·보험수익으로 내려가지 않는다 — 제조업의 이자수익은 매출이 아니다."""
    rows = _rows("kbfinancial_105560_64992_2024_11011_fnlttSinglAcntAll_CFS_IS.json")
    assert pick_revenue_row(rows, None) is None


def test_insurance_id_priority_beats_dart_order():
    """KB금융 「보험수익」 두 행: ifrs-full_InsuranceRevenue(ord 14) 가 dart_OperatingIncomeInsurance
    (ord 12, 재보험 포함) 보다 뒤에 오지만 코드 우선순위가 이긴다. (보험 업종으로 읽었을 때)"""
    rows = _rows("kbfinancial_105560_64992_2024_11011_fnlttSinglAcntAll_CFS_IS.json")
    pick = pick_revenue_row(rows, "65110")
    assert pick.account_id == "ifrs-full_InsuranceRevenue"
    assert int(pick.row["thstrm_amount"]) == 11_017_155_000_000


def test_construction_revenue_id_wins_over_earlier_cost_and_gross_profit_rows():
    pick = pick_revenue_row(_rows("hyundaienc_000720_41221_2024_11011_fnlttSinglAcntAll_CFS_IS.json"), "41221")
    assert pick.account_id == "ifrs-full_Revenue"
    assert pick.account_nm == "수익(매출액)"
    assert pick.standard is True


def test_rental_revenue_is_top_line_not_sub_line():
    pick = pick_revenue_row(_rows("lotterental_089860_76110_2024_11011_fnlttSinglAcntAll_CFS_IS.json"), "76110")
    assert pick.account_nm == "영업수익"
    assert pick.account_id == "ifrs-full_Revenue"


def test_bio_operating_revenue_without_code():
    pick = pick_revenue_row(_rows("tiumbio_321550_70113_2024_11011_fnlttSinglAcntAll_CFS_IS.json"), "70113")
    assert pick.account_nm == "영업수익"
    assert pick.account_id is None
    assert int(pick.row["thstrm_amount"]) == 6_792_235_001


# ── 이름 매칭 규칙 (합성 행) ──────────────────────────────────────────────────────

def _is(nm: str, amt: int | str = 100, ord_: int = 1, aid: str = "-표준계정코드 미사용-") -> dict:
    return {"sj_div": "IS", "account_nm": nm, "account_id": aid, "thstrm_amount": str(amt), "ord": str(ord_)}


def test_item_marker_and_spaces_are_stripped_before_prefix_match():
    assert pick_revenue_row([_is("Ⅰ. 매출액")]).account_nm == "Ⅰ. 매출액"
    assert pick_revenue_row([_is("수익 (매출액)")]).account_nm == "수익 (매출액)"
    assert pick_revenue_row([_is("- 영업수익")]).account_nm == "- 영업수익"


def test_exclusions_never_become_revenue():
    rows = [_is("매출원가", ord_=1), _is("매출총이익", ord_=2), _is("매출총손실", ord_=3),
            _is("매출채권", ord_=4), _is("기타영업수익", ord_=5), _is("영업수익원가", ord_=6),
            _is("출재보험서비스수익", ord_=7)]
    assert pick_revenue_row(rows, None) is None
    assert pick_revenue_row(rows, "65110") is None
    assert pick_revenue_row(rows, "64121") is None


def test_lower_ord_wins_within_same_priority_regardless_of_response_order():
    rows = [_is("영업수익", amt=1, ord_=11), _is("매출액", amt=2, ord_=3), _is("매출액", amt=3, ord_=1)]
    assert int(pick_revenue_row(rows).row["thstrm_amount"]) == 3


def test_interest_income_is_exact_match_only():
    """「이자수익(유효이자율법)」 같은 세부행은 은행 폴백에서도 안 잡는다."""
    assert pick_revenue_row([_is("당기손익-공정가치 측정 금융자산의 이자수익")], "64121") is None
    assert pick_revenue_row([_is("이자수익")], "64121").account_nm == "이자수익"
    assert pick_revenue_row([_is("이자수익")], None) is None


def test_empty_amount_rows_are_skipped():
    rows = [_is("매출액", amt="", ord_=1), _is("영업수익", amt=5, ord_=2)]
    assert int(pick_revenue_row(rows).row["thstrm_amount"]) == 5


def test_cumulative_extraction_reads_thstrm_add_for_quarterly_reports():
    row = {**_is("영업수익", amt=30), "thstrm_add_amount": "90"}
    assert _build_account_map([row], cumulative_is=True)["revenue"] == 90
    assert _build_account_map([row])["revenue"] == 30


def test_revenue_family_buckets():
    assert revenue_family("65110") == "insurance"
    assert revenue_family("64992") == "finance"
    assert revenue_family("66120") == "finance"
    assert revenue_family("41221") == "construction"
    assert revenue_family("63991") == "general"
    assert revenue_family(None) == "general"
