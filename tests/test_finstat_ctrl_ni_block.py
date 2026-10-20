"""분기 재무 수집 파서(`scripts/market_fund_quarterly.py`)의 지배 순이익 줄 고르기 — 총포괄이익의 지배 몫·순이익이 아닌 줄을 고르던 것 (261020).

종전 규칙(① 지배 귀속 순이익 계정 id 줄 → ② 이름에 「지배」가 든 줄, ord 최선두)이 고르던 잘못된 줄(10-20 운영·원문 캐시 대조):
  · 순이익 귀속 줄에 총포괄 계정 id 를 붙인 회사 — 그 줄을 빼고 「당기총포괄손익의 귀속 → 지배기업지분」을 골랐다(056360 FY2021).
  · 계정을 id 알파벳 순으로 내준 보고서 — 총포괄 아래 지배 줄의 ord 가 당기순이익 아래 지배 줄보다 앞선다(00177870 꼴).
  · 지배 순이익 계정 id 줄에 총포괄 값을 적은 회사(336570 2022 반기 · 00140061 꼴).
  · 주당이익(원/주) · 「종속기업, 공동지배기업과 관계기업으로부터의 기타이익」 · 계속영업 몫만 · 값이 0 인 지배 줄.
새 규칙은 후보 줄마다 값 구조(지배 + 비지배 = 당기순이익)를 본다 — 아래 각 시험의 원문 꼴은 운영 보고서에서 줄였다(값은 원문 그대로).
DART 0콜.
"""
from __future__ import annotations

import pytest

from scripts import market_fund_quarterly as Q


def row(sj, aid, nm, ordv, cur="KRW", **amt):
    return {"sj_div": sj, "account_id": aid, "account_nm": nm, "ord": str(ordv), "currency": cur,
            **{k: ("" if v is None else str(v)) for k, v in amt.items()}}


NOSTD = "-표준계정코드 미사용-"
F = "ifrs-full_"


def cis(aid, nm, ordv, v, v3=None):
    """누적 칸(thstrm_add)만 있는 손익 줄 — 사업보고서·1분기 꼴은 v3 없이 thstrm 에."""
    return row("CIS", aid, nm, ordv, thstrm_amount=v if v3 is None else v3, thstrm_add_amount="" if v3 is None else v)


# ── 056360 FY2021 사업보고서 — 순이익 귀속 줄에 총포괄 계정 id ─────────────────────────────

def rows_056360_fy2021():
    return [
        cis(F + "ProfitLossBeforeTax", "법인세비용차감전순이익(손실)", 10, 7057402758),
        cis(F + "ProfitLoss", "당기순이익", 12, 7012006146),
        cis(F + "OtherComprehensiveIncome", "기타포괄손익", 13, -156459757),
        cis(F + "ComprehensiveIncome", "총포괄이익", 16, 6855546389),
        cis(F + "ComprehensiveIncomeAttributableToOwnersOfParent", "지배기업지분", 18, 7012006146),
        cis(NOSTD, "당기총포괄손익의 귀속", 19, 6855546389),
        cis(NOSTD, "지배기업지분", 20, 6855546389),
        cis(F + "BasicEarningsLossPerShare", "기본주당이익(손실)", 22, 750),
    ]


def test_056360_ni_line_tagged_with_ci_id_is_net_income():
    assert Q.extract_controlling_ni_cum(rows_056360_fy2021()) == (7012006146.0, "CIS_CIID")


def test_056360_prior_period_rows_use_same_rule():
    """다음 해 보고서의 전기 칸(전기 칸 보충 경로 `_as_prior_rows`)도 같은 규칙 — 전기 누적 칸으로 옮겨 본다."""
    rows = [{**r, "frmtrm_amount": r["thstrm_amount"], "frmtrm_add_amount": r["thstrm_amount"]} for r in rows_056360_fy2021()]
    assert Q.extract_controlling_ni_cum(Q._as_prior_rows(rows, 2)) == (7012006146.0, "CIS_CIID")


# ── 알파벳 순 보고서 — 총포괄 아래 지배 줄이 ord 가 앞선다 ─────────────────────────────────

def test_sorted_layout_picks_ni_block_not_ci_block():
    """00177870 2026 반기 꼴 — 총포괄 지배 몫 342,721,676(ord 12) · 순이익 지배 몫 549,644,933(ord 29) · 비지배 −652,021,687."""
    rows = [
        cis(F + "ComprehensiveIncome", "총포괄손익", 10, -459362637),
        cis(NOSTD, "총포괄손익, 비지배지분", 11, -802084313),
        cis(NOSTD, "총포괄손익, 지배기업의 소유주에게 귀속되는 지분", 12, 342721676),
        cis(F + "OtherComprehensiveIncome", "기타포괄손익", 21, -356985883),
        cis(F + "ProfitLoss", "당기순이익(손실)", 27, -102376754),
        cis(NOSTD, "비지배지분에 귀속되는 당기순이익(손실)", 28, -652021687),
        cis(NOSTD, "지배기업의 소유주에게 귀속되는 당기순이익(손실)", 29, 549644933),
    ]
    assert Q.extract_controlling_ni_cum(rows) == (549644933.0, "CIS_NM")


def test_ci_total_falls_back_to_profit_plus_oci():
    """00852087 꼴 — 총포괄 총계 줄이 없어도 당기순이익 + 기타포괄손익으로 총포괄 쪽을 알아본다."""
    rows = [
        cis(NOSTD, "지배기업소유주지분", 11, 3633618358),
        cis(F + "BasicEarningsLossPerShare", "기본및희석주당순이익", 14, 2035),
        cis(F + "OtherComprehensiveIncome", "기타포괄손익", 19, 217455212),
        cis(F + "ProfitLoss", "당기순이익(손실)", 20, 3416163146),
    ]
    assert Q.extract_controlling_ni_cum(rows) == (3416163146.0, "TOTAL_CIREJ")


# ── 지배 순이익 계정 id 줄에 총포괄 값 ────────────────────────────────────────────────

def test_id_line_with_ci_value_is_rejected():
    """00140061 2026 반기 꼴 — 지배 순이익 id 줄 = 총포괄 −5,050,131,939, 당기순이익 −5,012,176,793, 비지배 0."""
    rows = [
        cis(F + "ComprehensiveIncome", "총포괄손익", 10, -5050131939),
        cis(F + "OtherComprehensiveIncome", "기타포괄손익", 19, -37955146),
        cis(F + "ProfitLoss", "당기순이익(손실)", 21, -5012176793),
        cis(F + "ProfitLossAttributableToNoncontrollingInterests", "비지배지분에 귀속되는 당기순이익(손실)", 23, 0),
        cis(F + "ProfitLossAttributableToOwnersOfParent", "지배기업의 소유주에게 귀속되는 당기순이익(손실)", 24, -5050131939),
    ]
    assert Q.extract_controlling_ni_cum(rows) == (-5012176793.0, "TOTAL_MINUS_MINORITY_CIREJ")


def test_id_line_with_ci_value_then_name_line_fits():
    """01031502 2026 반기 꼴 — id 줄 7,194,501,946 + (−57,656,269) = 총포괄, 이름 줄 1,276,743,532 + (−490,371,317) = 당기순이익."""
    rows = [
        cis(NOSTD, "비지배지분", 11, -490371317),
        cis(NOSTD, "지배기업소유주지분", 12, 1276743532),
        cis(NOSTD, "당기총포괄손익", 23, 7136845677),
        cis(F + "ProfitLoss", "반기순이익(손실)", 27, 786372215),
        cis(F + "ProfitLossAttributableToNoncontrollingInterests", "비지배지분", 29, -57656269),
        cis(F + "ProfitLossAttributableToOwnersOfParent", "지배기업소유주지분", 30, 7194501946),
    ]
    assert Q.extract_controlling_ni_cum(rows) == (1276743532.0, "CIS_NM")


def test_minority_line_also_wrong_when_ci_has_no_minority():
    """263860 2026 1분기 꼴 — 지배·비지배 순이익 줄 둘 다 총포괄 값, 총포괄 비지배 0 → 비지배를 빼지 않는다."""
    rows = [
        cis(F + "ComprehensiveIncome", "총포괄손익", 11, 1984897886),
        cis(F + "ComprehensiveIncomeAttributableToNoncontrollingInterests", "비지배지분", 13, 0),
        cis(F + "ComprehensiveIncomeAttributableToOwnersOfParent", "지배기업의소유주지분", 14, 1984897886),
        cis(F + "OtherComprehensiveIncome", "기타포괄손익", 23, 17234698),
        cis(F + "ProfitLoss", "당기순이익(손실)", 30, 1967663188),
        cis(F + "ProfitLossAttributableToNoncontrollingInterests", "비지배지분", 32, 1984897886),
        cis(F + "ProfitLossAttributableToOwnersOfParent", "지배기업의소유주지분", 33, 1984897886),
    ]
    assert Q.extract_controlling_ni_cum(rows) == (1967663188.0, "TOTAL_CIREJ")


# ── 「지배」가 든 다른 줄 ────────────────────────────────────────────────────────────

def test_joint_venture_income_line_is_not_controlling_ni():
    """00123781 2026 반기(별도) 꼴 — 「종속기업, 공동지배기업과 관계기업으로부터의 기타이익(비용)」 −8,954,060 을 순이익으로 골랐다."""
    rows = [
        cis(F + "ComprehensiveIncome", "총포괄손익", 10, 993035935),
        cis(F + "OtherComprehensiveIncome", "기타포괄손익", 18, -84212967),
        cis(F + "OtherIncomeExpenseFromSubsidiariesJointlyControlledEntitiesAndAssociates",
            "종속기업, 공동지배기업과 관계기업으로부터의 기타이익(비용)", 22, -8954060),
        cis(F + "ProfitLoss", "당기순이익(손실)", 23, 1077248902),
    ]
    assert Q.extract_controlling_ni_cum(rows) == (1077248902.0, "TOTAL")


def test_eps_line_is_not_controlling_ni_and_parts_sum():
    """00365387 2026 반기 꼴 — 「지배기업지분에 대한 주당순손익」 824(원/주)를 순이익으로 골랐다. 지배 몫은 계속 + 중단영업 몫."""
    rows = [
        cis(F + "ComprehensiveIncome", "총포괄손익", 9, 46863367000),
        cis(F + "ComprehensiveIncomeAttributableToNoncontrollingInterests", "비지배지분", 11, 10007000),
        cis(F + "ComprehensiveIncomeAttributableToOwnersOfParent", "지배기업소유주지분", 12, 46853360000),
        cis(F + "BasicEarningsLossPerShareFromContinuingOperations", "지배기업지분에대한주당순손익", 15, 824),
        cis(F + "OtherComprehensiveIncome", "기타포괄손익", 24, 9998568000),
        cis(F + "ProfitLoss", "반기순이익", 32, 36864799000),
        cis(F + "ProfitLossAttributableToNoncontrollingInterests", "비지배지분귀속반기순이익(손실)", 33, 10007000),
        cis(F + "IncomeFromContinuingOperationsAttributableToOwnersOfParent", "계속영업반기순이익", 37, 36855209000),
        cis(F + "IncomeFromDiscontinuedOperationsAttributableToOwnersOfParent", "중단영업반기순이익", 38, -416000),
    ]
    assert Q.extract_controlling_ni_cum(rows) == (36854793000.0, "CIS_PARTS")


def test_continuing_only_line_needs_discontinued_part():
    """00234412 2026 반기 꼴 — 계속영업 지배 몫 18,789,299,480 만 골랐다(중단영업 지배 몫 2,197,598,949)."""
    rows = [
        cis(F + "ProfitLoss", "당기순이익(손실)", 31, 21005250292),
        cis(F + "IncomeFromContinuingOperationsAttributableToOwnersOfParent", "지배기업의 소유주에게 귀속될 계속영업손익", 33, 18789299480),
        cis(F + "IncomeFromDiscontinuedOperationsAttributableToOwnersOfParent", "지배기업의 소유주에게 귀속될 중단영업손익", 34, 2197598949),
        cis(F + "ProfitLossAttributableToNoncontrollingInterests", "비지배지분에 귀속되는 당기순이익(손실)", 35, 18351863),
    ]
    assert Q.extract_controlling_ni_cum(rows) == (20986898429.0, "CIS_PARTS")


def test_two_unnamed_part_lines_sum():
    """101530 2020 3분기 꼴 — 머리 줄 「계속영업분기순이익」·「중단영업분기순이익」 아래 이름이 같은 「지배기업소유주지분」 두 줄."""
    rows = [
        cis(NOSTD, "관계기업투자및공동지배기업투자손익", 9, 330020787),
        cis(F + "ProfitLoss", "분기순이익", 14, 23374460894),
        cis(F + "ComprehensiveIncome", "총포괄이익", 20, 23332575886),
        cis(NOSTD, "계속영업분기순이익", 22, 17625491732),
        cis(NOSTD, "지배기업소유주지분", 23, 17566479790),
        cis(NOSTD, "비지배지분", 24, 59011942),
        cis(NOSTD, "중단영업분기순이익", 25, 5748969162),
        cis(NOSTD, "지배기업소유주지분", 26, 5748969162),
        cis(F + "ComprehensiveIncomeAttributableToOwnersOfParent", "지배기업소유주지분", 29, 23273563944),
    ]
    assert Q.extract_controlling_ni_cum(rows) == (23315448952.0, "CIS_PARTS")


def test_zero_controlling_line_is_blank():
    """045660 2025 1분기 꼴 — 순이익 쪽 「지배기업소유주지분」 0(당기순이익 165.7억 · 비지배 없음), 총포괄 쪽은 총포괄 값."""
    rows = [
        cis(NOSTD, "지배기업소유주지분", 12, 16568961406),
        cis(F + "OtherComprehensiveIncome", "기타포괄손익", 21, -5697727),
        cis(F + "ProfitLoss", "당기순이익", 25, 16574659133),
        cis(NOSTD, "지배기업소유주지분", 26, 0),
    ]
    assert Q.extract_controlling_ni_cum(rows) == (16574659133.0, "TOTAL_CIREJ")


def test_minority_named_line_equal_to_total_is_not_subtracted():
    """089970 2025 3분기 꼴 — 순이익 쪽 지배 몫 줄 이름이 「비지배지분」(값 = 당기순이익)."""
    rows = [
        cis(NOSTD, "당기총포괄이익(손실)", 10, 14209890155),
        cis(NOSTD, "지배기업의 소유주", 12, 14209890155),
        cis(F + "OtherComprehensiveIncome", "기타포괄손익", 21, -760855359),
        cis(F + "ProfitLoss", "당기순이익(손실)", 24, 14970745514),
        cis(NOSTD, "비지배지분", 25, 14970745514),
    ]
    assert Q.extract_controlling_ni_cum(rows) == (14970745514.0, "TOTAL_CIREJ")


def test_blank_first_name_line_no_longer_skips_statement():
    """00129509 2023 3분기 꼴 — ord 가 앞선 빈 「지배기업소유주지분」 줄 때문에 그 표를 건너뛰어 비지배를 빼지 않은 총계로 갔다."""
    rows = [
        cis(NOSTD, "당기순이익(손실)", 11, 264091052),
        cis(NOSTD, "당기총포괄손익", 12, 506903740),
        cis(NOSTD, "중단반기순이익의귀속", 13, None),
        cis(NOSTD, "지배기업소유주지분", 14, None),
        cis(NOSTD, "계속반기순이익의귀속", 16, 264091052),
        cis(NOSTD, "지배기업소유주지분", 17, -231121861),
        cis(NOSTD, "비지배지분", 18, 495212913),
        cis(NOSTD, "총포괄손익의 귀속", 19, 506903740),
        cis(NOSTD, "비지배지분", 20, 553753470),
        cis(NOSTD, "지배기업소유주지분", 21, -46849730),
    ]
    assert Q.extract_controlling_ni_cum(rows) == (-231121861.0, "CIS_NM")


def test_minority_typo_is_recognised():
    """130740 2023 3분기 꼴 — 비지배 줄 이름이 「비재배지분귀속당기순이익」(원문 오타). 총포괄 지배 몫이 ord 가 앞선다."""
    rows = [
        cis(NOSTD, "지배기업귀속총포괄손익", 12, -2938774163),
        cis(NOSTD, "비지배지분귀속총포괄손익", 13, -2443444449),
        cis(NOSTD, "지배기업귀속당기순이익", 15, -2911738598),
        cis(NOSTD, "비재배지분귀속당기순이익", 16, -2407767216),
        cis(F + "ComprehensiveIncome", "총포괄손익", 21, -5382218612),
        cis(F + "ProfitLoss", "당기순이익(손실)", 34, -5319505814),
    ]
    v, case = Q.extract_controlling_ni_cum(rows)
    assert (v, case) == (-2911738598.0, "CIS_NM")
    assert Q.ni_attrib_side(v, Q._ni_shape(rows)) == "ni"


# ── 종전 결과 불변 · 가를 수 없는 줄 ────────────────────────────────────────────────

def test_standard_layout_unchanged():
    """표준 꼴(지배 순이익 id 줄 + 비지배 = 당기순이익)은 종전 그대로."""
    rows = [
        cis(F + "ProfitLoss", "당기순이익", 12, 1000),
        cis(F + "ComprehensiveIncome", "총포괄이익", 16, 900),
        cis(F + "ProfitLossAttributableToOwnersOfParent", "지배기업 소유주지분", 18, 800),
        cis(F + "ProfitLossAttributableToNoncontrollingInterests", "비지배지분", 19, 200),
        cis(F + "ComprehensiveIncomeAttributableToOwnersOfParent", "지배기업 소유주지분", 21, 720),
        cis(F + "ComprehensiveIncomeAttributableToNoncontrollingInterests", "비지배지분", 22, 180),
    ]
    assert Q.extract_controlling_ni_cum(rows) == (800.0, "CIS_ID")


def test_unverifiable_line_keeps_previous_value():
    """00256502 2026 반기 꼴 — 지배 + 비지배가 당기순이익에도 총포괄에도 맞지 않는 회사 원문 — 가를 수 없으니 종전 값 그대로."""
    rows = [
        cis(NOSTD, "지배기업소유주지분", 12, 13415982645),
        cis(NOSTD, "비지배지분", 13, 514317391),
        cis(F + "OtherComprehensiveIncome", "기타포괄손익", 22, -68447852),
        cis(F + "ProfitLoss", "당기순이익", 26, 13643501348),
    ]
    assert Q.extract_controlling_ni_cum(rows) == (13415982645.0, "CIS_NM")


def test_oci_zero_both_blocks_same_value():
    """기타포괄손익 0 이면 두 지배 몫 값이 같다 — 어느 줄이든 같은 값(종전 그대로)."""
    rows = [
        cis(F + "ComprehensiveIncome", "총포괄손익", 10, -1865974037),
        cis(F + "ComprehensiveIncomeAttributableToNoncontrollingInterests", "비지배지분", 12, 152156708),
        cis(F + "ComprehensiveIncomeAttributableToOwnersOfParent", "지배기업지분", 13, -2018130745),
        cis(F + "OtherComprehensiveIncome", "기타포괄손익", 22, 0),
        cis(F + "ProfitLoss", "당기순이익(손실)", 25, -1865974037),
        cis(NOSTD, "지배기업지분", 26, -2018130745),
        cis(NOSTD, "비지배지분", 27, 152156708),
    ]
    assert Q.extract_controlling_ni_cum(rows) == (-2018130745.0, "CIS_NM")


@pytest.mark.parametrize("x, tol", [(7012006146.0, 7012.006146), (36864799000.0, 36864.799), (12345678000.0, 12345.678), (1234567000000.0, 1500000.0), (1000.0, 1500.0), (7.0, 2.0), (0.0, 2.0)])
def test_amount_tolerance_follows_display_unit(x, tol):
    assert Q._amt_tol(x) == pytest.approx(tol)


# ── 261020 검토: 순이익 쪽 비지배가 없는 보고서 · 가를 수 없는 총포괄 id 줄 · 주당이익 이름 줄 ─────────────────────

def test_ci_side_minority_used_when_no_ni_side_minority():
    """018620 2019 3분기 꼴 — 지배·비지배 몫을 총포괄 쪽에만 냈다(지배 몫은 총포괄이라 버림). 빼지 않으면 비지배 순이익만큼 크다
    (총계 −28.4억). 총포괄 쪽 비지배로 빼면 −25.87억 — 다음 해 3분기 보고서 전기 칸의 지배 순이익 −25.85억에 비지배 몫 기타포괄만큼 가깝다."""
    rows = [
        cis(F + "ProfitLoss", "당기순이익(손실)", 16, -2841717467, -727097514),
        cis(F + "OtherComprehensiveIncome", "기타포괄손익", 17, -64597871, -34185055),
        cis(F + "ComprehensiveIncome", "총포괄손익", 20, -2906315338, -761282569),
        cis(NOSTD, "지배기업 소유주지분", 21, -2651506486, -676832910),
        cis(NOSTD, "비지배지분", 22, -254808852, -84449659),
    ]
    assert Q.extract_controlling_ni_cum(rows) == (-2586908615.0, "TOTAL_MINUS_MINORITY_CIREJ_CINCI")


def test_ci_id_minority_used_after_ci_id_ctrl_rejected():
    """238090 2021 반기 꼴 — 지배 귀속 줄은 총포괄 id 줄뿐(값 구조도 총포괄), 비지배도 총포괄 id 줄뿐. 참값(2022 반기 보고서 전기 칸) 23.27억."""
    rows = [
        cis(NOSTD, "종속기업, 공동지배기업과 관계기업으로부터의 기타수익(비용)", 9, 0, 0),
        cis(F + "ProfitLoss", "당기순이익(손실)", 12, 1765779834, 1278828495),
        cis(F + "OtherComprehensiveIncome", "기타포괄손익", 13, -77880054, -73241272),
        cis(F + "ComprehensiveIncome", "총포괄손익", 16, 1687899780, 1205587223),
        cis(F + "ComprehensiveIncomeAttributableToOwnersOfParent", "총 포괄손익, 지배기업의 소유주에게 귀속되는 지분", 18, 2248711146, 1338960891),
        cis(F + "ComprehensiveIncomeAttributableToNoncontrollingInterests", "총 포괄손익, 비지배지분", 19, -560811366, -133373668),
    ]
    assert Q.extract_controlling_ni_cum(rows) == (2326591200.0, "TOTAL_MINUS_MINORITY_CINCI")


def test_ci_side_minority_not_subtracted_when_oci_same_sign_and_larger():
    """044480 2020 1분기 꼴(2차 검토 부호 가드) — 총포괄 쪽 비지배 9.13억이 기타포괄 9.76억과 같은 부호로 그보다 작다 = 비지배 몫이 통째로
    기타포괄일 수 있다. 빼면 −11.04억(총포괄 지배 몫 −1.28억과도 동떨어짐) — 빼지 않고 총계(−1.92억, 종전 값)."""
    rows = [
        cis(F + "ProfitLossBeforeTax", "법인세비용차감전순이익(손실)", 9, 121779917),
        cis(F + "ProfitLoss", "당기순이익(손실)", 11, -191537154),
        cis(F + "OtherComprehensiveIncome", "기타포괄손익", 12, 976359965),
        cis(F + "ComprehensiveIncome", "당기총포괄이익(손실)", 18, 784822811),
        cis(F + "ComprehensiveIncomeAttributableToOwnersOfParent", "지배기업소유주지분", 20, -127912154),
        cis(F + "ComprehensiveIncomeAttributableToNoncontrollingInterests", "비지배지분", 21, 912734965),
    ]
    assert Q.extract_controlling_ni_cum(rows) == (-191537154.0, "TOTAL")


def test_ci_side_minority_subtracted_when_oci_opposite_sign():
    """123410 2021 반기 꼴 — 기타포괄(+30.4억)과 총포괄 쪽 비지배(−1.29억)가 반대 부호면 비지배 순이익은 그 비지배보다 더 크다(같은 쪽으로) —
    빼면 총계와 참값 사이로 가므로 뺀다(가드는 같은 부호일 때만)."""
    rows = [
        cis(F + "ProfitLoss", "당기순이익(손실)", 12, 10153045084, 3440555734),
        cis(F + "OtherComprehensiveIncome", "기타포괄손익", 13, 3038474879, 1697935024),
        cis(F + "ComprehensiveIncome", "총포괄손익", 18, 13191519963, 5138490758),
        cis(F + "ComprehensiveIncomeAttributableToOwnersOfParent", "총포괄손익, 지배기업의 소유주에게 귀속되는 지분", 20, 13320779814, 5213716610),
        cis(F + "ComprehensiveIncomeAttributableToNoncontrollingInterests", "총포괄손익, 비지배지분", 21, -129259851, -75225852),
    ]
    assert Q.extract_controlling_ni_cum(rows) == (10282304935.0, "TOTAL_MINUS_MINORITY_CINCI")


def test_ci_side_minority_zero_is_not_subtracted():
    """총포괄 쪽 비지배가 0 이면 빼지 않는다(종전 표시 그대로)."""
    rows = [
        cis(F + "ComprehensiveIncome", "총포괄손익", 10, 9512345671),
        cis(NOSTD, "지배기업소유주지분", 11, 9512345671),
        cis(NOSTD, "비지배지분", 12, 0),
        cis(F + "OtherComprehensiveIncome", "기타포괄손익", 20, -511111111),
        cis(F + "ProfitLoss", "당기순이익", 25, 10023456782),
    ]
    assert Q.extract_controlling_ni_cum(rows) == (10023456782.0, "TOTAL_CIREJ")


def test_unverifiable_ci_id_line_is_not_used():
    """101670 2025 반기 꼴 — 지배 순이익 id 줄 = 총포괄(버림), 총포괄 지배 id 줄 = 기타포괄 값(5,882,958 — 어느 구조에도 안 맞음).
    총포괄 id 줄은 값 구조가 순이익일 때만 쓴다 — 가를 수 없으면 쓰지 않고 총계(비지배 없음)."""
    rows = [
        cis(F + "ComprehensiveIncome", "총포괄손익", 10, -33049669377, -28324410107),
        cis(F + "ComprehensiveIncomeAttributableToOwnersOfParent", "포괄손익, 지배기업의 소유주에게 귀속되는 지분", 12, 5882958, 41898629),
        cis(F + "OtherComprehensiveIncome", "기타포괄손익", 21, 5882958, 41898629),
        cis(F + "ProfitLoss", "당기순이익(손실)", 23, -33055552335, -28366308736),
        cis(F + "ProfitLossAttributableToOwnersOfParent", "지배기업의 소유주에게 귀속되는 당기순이익(손실)", 25, -33049669377, -28324410107),
    ]
    assert Q.extract_controlling_ni_cum(rows) == (-33055552335.0, "TOTAL_CIREJ")


def test_eps_name_line_is_never_the_fallback():
    """086280 2023 3분기 꼴에서 비지배 줄이 응답에 빠졌다면 — 지배 몫 줄을 가를 수 없어 종전처럼 쓰는데, ord 가 앞선 주당이익 줄(원/주, 표준 코드 없음)을
    대신 집으면 안 된다(종전 규칙은 21,704원을 지배 순이익으로 골랐다)."""
    rows = [
        row("IS", NOSTD, "지배기업의 소유주지분에 대한 기본및희석주당이익", 13, thstrm_amount=6505, thstrm_add_amount=21704),
        row("IS", F + "ProfitLoss", "분기순이익", 18, thstrm_amount=246006924684, thstrm_add_amount=820336758454),
        row("IS", NOSTD, "지배기업의 소유주지분", 19, thstrm_amount=243952949627, thstrm_add_amount=813890787315),
    ]
    assert Q.extract_controlling_ni_cum(rows) == (813890787315.0, "IS_NM")


# ── 연간 수집(`scripts/market_val_series.py`)의 `ctrl_ni` ─────────────────────────────────────────

def _series():
    import importlib.util
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("market_val_series_ctrl_ni", root / "scripts" / "market_val_series.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def yrow(aid, nm, ordv, cur, prev=None, sj="CIS"):
    return {"sj_div": sj, "account_id": aid, "account_nm": nm, "ord": str(ordv), "currency": "KRW",
            "thstrm_amount": str(cur), "frmtrm_amount": "" if prev is None else str(prev), "thstrm_add_amount": ""}


def rows_000680_fy2024():
    """000680 2024 사업보고서 꼴 — 지배 순이익 id 줄 = 총포괄 지배 몫(−448.8억), 비지배 120.7억, 당기순이익 −318.9억. 전기 칸(FY2023)은 비지배 0."""
    return [
        yrow(F + "ComprehensiveIncome", "총포괄손익", 9, -32814275801, 9592881336),
        yrow(F + "ComprehensiveIncomeAttributableToNoncontrollingInterests", "포괄손익, 비지배지분", 11, 12067190440, 0),
        yrow(F + "ComprehensiveIncomeAttributableToOwnersOfParent", "포괄손익, 지배기업의 소유주에게 귀속되는 지분", 12, -44881466241, 9592881336),
        yrow(F + "OtherComprehensiveIncome", "기타포괄손익", 21, -923447500, -1112102292),
        yrow(F + "ProfitLoss", "당기순이익(손실)", 24, -31890828301, 10704983628),
        yrow(F + "ProfitLossAttributableToNoncontrollingInterests", "비지배지분에 귀속되는 당기순이익(손실)", 26, 12067190440, 0),
        yrow(F + "ProfitLossAttributableToOwnersOfParent", "지배기업의 소유주에게 귀속되는 당기순이익(손실)", 27, -44881466241, 9592881336),
    ]


def test_annual_id_line_with_ci_value_and_minority():
    v, note = _series().ctrl_ni(rows_000680_fy2024())
    assert v == -43958018741.0 and "TOTAL_MINUS_MINORITY_CIREJ" in note


def test_annual_restated_field_uses_same_rule():
    """정정치(다음 해 보고서의 전기 칸 — `backfill_restated`)도 같은 규칙: FY2023 지배 순이익 107.05억(총포괄 95.93억이던 것)."""
    v, note = _series().ctrl_ni(rows_000680_fy2024(), "frmtrm_amount")
    assert v == 10704983628.0 and "CIREJ" in note


def test_annual_id_line_with_ci_value_no_minority():
    """075180 2025 사업보고서 꼴 — 지배 순이익 id 줄 27.68억 = 총포괄, 당기순이익 −5.15억(비지배 없음)."""
    rows = [
        yrow(F + "ComprehensiveIncome", "총포괄손익", 23, 2768099768),
        yrow(F + "ComprehensiveIncomeAttributableToOwnersOfParent", "포괄손익, 지배기업의 소유주에게 귀속되는 지분", 25, 2768099768),
        yrow(F + "OtherComprehensiveIncome", "기타포괄손익", 34, 3283520391),
        yrow(F + "ProfitLoss", "당기순이익(손실)", 41, -515420623),
        yrow(F + "ProfitLossAttributableToOwnersOfParent", "지배기업의 소유주에게 귀속되는 당기순이익(손실)", 43, 2768099768),
    ]
    v, note = _series().ctrl_ni(rows)
    assert v == -515420623.0 and note.endswith("TOTAL_CIREJ")


def test_annual_id_line_ci_value_then_name_line():
    """302430 2025 사업보고서 꼴 — 지배 순이익 id 줄(총포괄 아래) = 총포괄 −42.77억, 순이익 아래 이름 줄 = 당기순이익 −42.96억(비지배 없음)."""
    rows = [
        yrow(NOSTD, "당기총포괄이익(손실)", 11, -4277362668),
        yrow(F + "ProfitLossAttributableToOwnersOfParent", "지배기업의 소유주", 13, -4277362668),
        yrow(F + "OtherComprehensiveIncome", "법인세비용차감후기타포괄손익", 22, 18780347),
        yrow(F + "ProfitLoss", "당기순이익(손실)", 26, -4296143015),
        yrow(NOSTD, "지배기업의 소유주", 28, -4296143015),
    ]
    v, note = _series().ctrl_ni(rows)
    assert v == -4296143015.0 and note.endswith("CIS_NM")


def test_annual_standard_layout_unchanged():
    """표준 꼴(지배 + 비지배 = 당기순이익)은 종전 그대로 — 거른 까닭 없음.
    지배 계정 0 인데 비지배 200 이 따로 있으면 종전 규칙은 총계 1000(비지배 포함), 261020(p1e firm-ctrl-ni)부터는 분기·서버와 같은 규칙으로 총계 − 비지배."""
    S = _series()
    rows = [
        yrow(F + "ProfitLoss", "당기순이익", 12, 1000),
        yrow(F + "ComprehensiveIncome", "총포괄이익", 16, 900),
        yrow(F + "ProfitLossAttributableToOwnersOfParent", "지배기업 소유주지분", 18, 800),
        yrow(F + "ProfitLossAttributableToNoncontrollingInterests", "비지배지분", 19, 200),
    ]
    assert S.ctrl_ni(rows) == (800.0, None)
    zero = [yrow(F + "ProfitLoss", "당기순이익", 12, 1000123457), yrow(F + "ComprehensiveIncome", "총포괄이익", 16, 900111111),
            yrow(F + "ProfitLossAttributableToOwnersOfParent", "지배기업 소유주지분", 18, 0),
            yrow(F + "ProfitLossAttributableToNoncontrollingInterests", "비지배지분", 19, 200045679)]
    v, note = S.ctrl_ni(zero)
    assert v == 800077778.0 and note == "종전 규칙 값 1000123457원 → TOTAL_MINUS_MINORITY"
    zero[3] = yrow(F + "ProfitLossAttributableToNoncontrollingInterests", "비지배지분", 19, 0)
    assert S.ctrl_ni(zero) == (1000123457.0, None)                   # 비지배 0 — 총계 그대로(종전과 같다)
