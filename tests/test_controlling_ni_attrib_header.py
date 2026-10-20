"""지배 귀속 순이익 줄 고르기 — 비지배를 품은 값을 지배 몫으로 고르던 두 꼴(261020 p1j ctrl-rule-068290).

  · 귀속 머리 줄: 「…의 귀속」 머리 줄에 지배 이름·id 를 붙여 비지배를 품은 소계(= 다음 지배 줄 + 비지배 줄)를 적은 보고서 — 그 줄을 지배 몫으로 읽었다
    (068290 2022 사업보고서 당기 칸 164.42억 = 비지배 포함 당기순이익, 참값 117.91억 · 014130 2025 반기 · 080160 2025 사업보고서 전전기 칸 · 417860 2026 반기).
  · 「비지배 0」 읽기: 지배 줄 값 = 당기순이익 총계인데 같은 보고서에 0 이 아닌 비지배 줄이 있고 다른 지배 줄 + 그 비지배 = 총계(073540 2022 반기).
아래 줄은 그 보고서(DART 전체 재무제표 응답)를 줄였다 — 계정 id·이름·ord·값은 원문 그대로, 손익 줄만. 참값은 같은 원문의 자본변동표 지배 몫이나 다른 해 보고서 값과 맞춰 봤다.
DART 0콜 · DB 0.
"""
from __future__ import annotations

import pytest

from open_proxy_mcp.services import controlling_ni as C

F = "ifrs-full_"
NOSTD = "-표준계정코드 미사용-"


def r(sj, aid, nm, ordv, cur, prev=None, prev2=None, cum=None):
    """손익 줄 — `cur` = 당기, `prev`·`prev2` = 전기·전전기 칸, `cum` = 당기 누적(반기·3분기 보고서)."""
    s = lambda x: "" if x is None else str(x)  # noqa: E731
    return {"sj_div": sj, "account_id": aid, "account_nm": nm, "ord": str(ordv), "currency": "KRW",
            "thstrm_amount": s(cur), "frmtrm_amount": s(prev), "bfefrmtrm_amount": s(prev2), "thstrm_add_amount": s(cum)}


def rows_068290_fy2022():
    """068290 2022 사업보고서(접수 20230713000163) — 계속영업 귀속(지배 34.92억 · 비지배 0) 다음에 「중단영업순손익의 귀속」 자리에 지배 이름 줄
    129.50억(= 중단영업이익), 그 아래 지배 82.99억 · 비지배 46.51억. 당기순이익 164.42억 · 참 지배 몫 117.91억(2023 사업보고서 전기 칸 · 2024 전전기 칸).
    전기(FY2021) 칸 참값 31.59억 · 전전기(FY2020) 칸 참값 532.22억(그 해 사업보고서 자본변동표 지배 몫)."""
    return [
        r("CIS", F + "ProfitLossBeforeTax", "법인세비용차감전순이익(손실)", 11, 4582058487, 1425509491, 68773341105),
        r("CIS", F + "ProfitLossFromContinuingOperations", "계속영업이익(손실)", 13, 3491993648, 758594044, 53179897892),
        r("CIS", F + "ProfitLossFromDiscontinuedOperations", "중단영업이익(손실)", 14, 12949999975, 5176382608, 90233444),
        r("CIS", F + "ProfitLoss", "당기순이익(손실)", 15, 16441993623, 5934976652, 53270131336),
        r("CIS", F + "OtherComprehensiveIncome", "기타포괄손익", 16, -21886974425, 45809453391, -32238422),
        r("CIS", F + "ComprehensiveIncome", "총포괄손익", 20, -5444980802, 51744430043, 53237892914),
        r("CIS", NOSTD, "계속영업순손익의 귀속", 21, 3491993648, 758594044, 53179897892),
        r("CIS", NOSTD, "지배기업소유주지분의 귀속", 22, 3491993648, 754981699, 53179917105),
        r("CIS", NOSTD, "비지배지분 귀속", 23, 0, 3612345, -19213),
        r("CIS", NOSTD, "지배기업소유주지분의 귀속", 24, 12949999975, 5176382608, 90233444),
        r("CIS", NOSTD, "지배기업소유주지분의 귀속", 25, 8299114283, 2404370902, 41912409),
        r("CIS", NOSTD, "비지배지분 귀속", 26, 4650885692, 2772011706, 48321035),
        r("CIS", NOSTD, "총포괄손익의 귀속", 27, -5444980802, 51744430043, 53237892914),
        r("CIS", NOSTD, "지배기업소유주지분의 귀속", 28, -10120637819, 48968805992, 53189591092),
        r("CIS", NOSTD, "비지배지분 귀속", 29, 4675657017, 2775624051, 48301822),
        r("CIS", F + "BasicEarningsLossPerShare", "기본주당이익(손실)", 31, 1179, 316, 5322),
    ]


@pytest.mark.parametrize("field, want", [(None, 11791107931.0), ("thstrm_amount", 11791107931.0),
                                         ("frmtrm_amount", 3159352601.0), ("bfefrmtrm_amount", 53221829514.0)],
                         ids=["누적", "당기", "전기", "전전기"])
def test_068290_discontinued_header_named_owner_is_not_the_owner_share(field, want):
    """종전: 당기 164.42억(34.92 + 머리 줄 129.50 — 비지배 0 줄로 맞음) · 전기 59.31억 · 전전기 532.70억(모두 비지배 포함)."""
    assert C.controlling_ni(rows_068290_fy2022(), field) == (want, "CIS_PARTS")


def test_068290_pair_with_minority_wins_when_the_header_is_not_adjacent():
    """두 번째 그물(합성 — 068290 줄에 주당이익 줄 하나를 머리 줄과 그 짝 사이에 끼움): 머리 줄 판정이 못 잡아도(다음 두 줄이 주당이익·지배 줄) 두 줄 합 가운데
    0 이 아닌 비지배와 짝이 맞는 합(34.92 + 82.99 = 117.91억)을 「비지배 0」으로만 맞는 합(34.92 + 129.50 = 164.42억 = 당기순이익)보다 먼저 쓴다."""
    rows = rows_068290_fy2022()
    k = next(i for i, x in enumerate(rows) if x["ord"] == "24")
    rows.insert(k + 1, r("CIS", F + "BasicEarningsLossPerShareFromDiscontinuedOperations", "중단영업기본주당이익(손실)", 24, 879, 240))
    for i, x in enumerate(rows):
        x["ord"] = str(i + 1)
    assert C._attrib_header(rows[k], sorted(rows, key=C._ordv), C._cum_is, C._ni_shape(rows)) is False
    assert C.controlling_ni(rows) == (11791107931.0, "CIS_PARTS")


def rows_014130_2025h1():
    """014130 2025 반기(접수 20250829001916) — 「계속영업반기순이익의 귀속」(지배 기업 id) −76.12억 = 지배 −71.37억 + 비지배 −4.75억, 중단영업 지배 −3.79억.
    반기순이익 −79.91억 · 자본변동표 지배 몫 −75.16억. 3개월 칸: 반기순이익 −21.32억 · 참 지배 몫 −18.56억."""
    A = F + "ProfitLossFromContinuingOperationsAttributableToOrdinaryEquityHoldersOfParentEntity"
    return [
        r("CIS", F + "ComprehensiveIncome", "당기총포괄손익", 11, -3716839833, cum=-9705526444),
        r("CIS", F + "ComprehensiveIncomeAttributableToNoncontrollingInterests", "비지배지분", 13, -289518552, cum=-771449735),
        r("CIS", F + "ComprehensiveIncomeAttributableToOwnersOfParent", "지배기업 소유주지분", 16, -3427321281, cum=-8934076709),
        r("CIS", F + "ComprehensiveIncomeFromContinuingOperationsAttributableToOwnersOfParent", "계속영업 총포괄손익", 17, -3154725161, cum=-8554725267),
        r("CIS", F + "ComprehensiveIncomeFromDiscontinuedOperationsAttributableToOwnersOfParent", "중단영업 총포괄손익", 18, -272596120, cum=-379351442),
        r("CIS", F + "ComprehensiveIncomeFromDiscontinuedOperations", "중단영업반기순이익의 귀속", 19, -272596120, cum=-379351442),
        r("CIS", F + "IncomeFromDiscontinuedOperationsAttributableToOwnersOfParent", "지배기업 소유주지분", 20, -272596120, cum=-379351442),
        r("CIS", F + "OtherComprehensiveIncome", "기타포괄손익", 31, -1584452558, cum=-1714212966),
        r("CIS", F + "ProfitLoss", "반기순이익(손실)", 37, -2132387275, cum=-7991313478),
        r("CIS", F + "ProfitLossFromContinuingOperations", "반기 계속영업이익(손실)", 39, -1859791155, cum=-7611962036),
        r("CIS", A, "계속영업반기순이익의 귀속", 40, -1859791155, cum=-7611962036),
        r("CIS", F + "IncomeFromContinuingOperationsAttributableToOwnersOfParent", "지배기업 소유주지분", 41, -1583658105, cum=-7136591204),
        r("CIS", F + "ProfitLossFromContinuingOperationsAttributableToNoncontrollingInterests", "비지배지분", 42, -276133050, cum=-475370832),
        r("CIS", F + "ProfitLossFromDiscontinuedOperations", "중단영업이익(손실)", 43, -272596120, cum=-379351442),
    ]


def test_014130_continuing_header_with_parent_entity_id_is_not_the_owner_share():
    """종전: 계속영업 몫 = 머리 줄 −76.12억 → 누적 −79.91억(= 반기순이익), 3개월 −21.32억."""
    rows = rows_014130_2025h1()
    assert C.controlling_ni(rows) == (-7515942646.0, "CIS_PARTS")
    assert C.controlling_ni(rows, "thstrm_amount") == (-1856254225.0, "CIS_PARTS")


def rows_080160_fy2023_in_fy2025_report():
    """080160 2025 사업보고서(접수 20260319000605)의 전전기(FY2023) 칸 — 「중단영업 당기순이익(손실)의 귀속」 57.13억 = 지배 27.89억 + 비지배 29.24억.
    당기순이익 131.88억 · 참 지배 몫 102.78억(2023 사업보고서 자본변동표 · 2024 사업보고서 전기 칸)."""
    P = F + "ProfitLossFromDiscontinuedOperationsAttributableToOrdinaryEquityHoldersOfParentEntity"
    return [
        r("CIS", F + "ComprehensiveIncome", "총포괄손익", 19, None, None, 12861519526),
        r("CIS", F + "ComprehensiveIncomeAttributableToNoncontrollingInterests", "비지배지분", 21, None, None, 2909502571),
        r("CIS", F + "ComprehensiveIncomeAttributableToOwnersOfParent", "지배기업소유주지분", 22, None, None, 9952016955),
        r("CIS", F + "OtherComprehensiveIncome", "기타포괄손익", 29, None, None, -326081948),
        r("CIS", F + "ProfitLoss", "당기순이익", 36, None, None, 13187601474),
        r("CIS", F + "ProfitLossAttributableToNoncontrollingInterests", "비지배지분", 38, None, None, -14441229),
        r("CIS", F + "ProfitLossAttributableToOwnersOfParent", "지배기업소유주지분", 39, None, None, 7488977908),
        r("CIS", F + "ProfitLossFromContinuingOperations", "계속영업 당기순이익(손실)", 41, None, None, 7474536679),
        r("CIS", F + "ProfitLossFromDiscontinuedOperations", "중단영업 당기순이익(손실)", 42, None, None, 5713064795),
        r("CIS", P, "중단영업 당기순이익(손실)의 귀속", 43, None, None, 5713064795),
        r("CIS", F + "IncomeFromDiscontinuedOperationsAttributableToOwnersOfParent", "지배기업소유주지분", 44, None, None, 2789112709),
        r("CIS", F + "ProfitLossFromDiscontinuedOperationsAttributableToNoncontrollingInterests", "비지배지분", 45, None, None, 2923952086),
    ]


def test_080160_prior2_column_discontinued_header_is_not_the_owner_share():
    """종전 132.02억(지배 id 74.89 + 머리 줄 57.13) — 가장 늦은 보고서 값을 쓰는 연간 정정 칸이 이 칸을 읽으면 비지배를 품은 값이 들어간다."""
    assert C.controlling_ni(rows_080160_fy2023_in_fy2025_report(), "bfefrmtrm_amount") == (10278090617.0, "CIS_PARTS")


def rows_417860_2026h1():
    """417860 2026 반기(접수 20260814003882) — 지배 순이익 id 줄 「반기순이익(손실)의 귀속」 = 반기순이익 38.39억 = 「비지배지분」 8,363원 +
    「지배기업소유주지분」(계속영업 id) 3,838,945,053원(자본변동표 지배 몫과 같음)."""
    return [
        r("CIS", F + "ComprehensiveIncome", "당기총포괄손익", 10, 4322513765, cum=3838953416),
        r("CIS", F + "ComprehensiveIncomeAttributableToNoncontrollingInterests", "총포괄이익(손실)의 귀속", 11, 4322513765, cum=3838953416),
        r("CIS", F + "ComprehensiveIncomeAttributableToOwnersOfParent", "지배기업소유주지분", 12, 4322512149, cum=3838945053),
        r("CIS", F + "OtherComprehensiveIncomeAttributableToNoncontrollingInterests", "비지배지분", 13, 1616, cum=8363),
        r("CIS", F + "ProfitLossAttributableToOwnersOfParent", "반기순이익(손실)의 귀속", 14, 4322513765, cum=3838953416),
        r("CIS", F + "ProfitLossAttributableToNoncontrollingInterests", "비지배지분", 15, 1616, cum=8363),
        r("CIS", F + "ProfitLossFromContinuingOperations", "지배기업소유주지분", 16, 4322512149, cum=3838945053),
        r("CIS", F + "BasicEarningsLossPerShare", "기본주당손익", 18, 920, cum=817),
        r("CIS", F + "ProfitLoss", "반기순손익", 22, 4322513765, cum=3838953416),
    ]


def test_417860_owner_id_on_the_attribution_header():
    """종전 3,838,953,416원(= 반기순이익, 비지배 8,363원 포함). 머리 줄 판정은 짝 두 줄이 (비지배, 지배) 순이어도 잡는다 — 이 보고서는 「비지배지분」이 먼저다
    (값만 보면 「비지배 0」 읽기 미루기도 같은 값을 내므로 판정 자체를 따로 본다)."""
    rows = rows_417860_2026h1()
    assert C.controlling_ni(rows) == (3838945053.0, "CIS_PARTS")
    seq = sorted(rows, key=C._ordv)
    assert C._attrib_header(rows[4], seq, C._cum_is, C._ni_shape(rows)) is True
    # 짝 줄은 순이익 쪽만 — 총포괄 지배 id 줄·기타포괄 비지배 id 줄(이름 「비지배지분」)은 짝이 아니다
    assert C._is_owner_row(rows[2]) is False
    assert C._is_nci_row(rows[3]) is False


def test_417860_three_month_minority_below_the_amount_tolerance():
    """261020 p1j 검토: 3개월 칸 — 머리 줄 4,322,513,765 = 지배 4,322,512,149 + 비지배 1,616. 비지배가 허용 오차(총계의 1e-6 = 4,322원)보다 작아
    종전엔 0 으로 읽어 머리 줄을 놓치고 총계를 골랐다(누적 차 3,838,945,053 − (−483,567,096) = 4,322,512,149 와 1,616원 어긋남)."""
    assert C.controlling_ni(rows_417860_2026h1(), "thstrm_amount") == (4322512149.0, "CIS_PARTS")


def rows_073540_2022h1():
    """073540 2022 반기(접수 20220812000334) — 「지배기업 소유주지분」 −14.62억 · 「비지배지분」 −0.22억(표준코드 없음) 다음, 지배 순이익 id 줄에
    반기순이익 −14.85억을 그대로 적었다. 자본변동표 지배 몫 −14.62억 · 2023 반기 보고서 전년 동기 칸 −14.62억."""
    return [
        r("CIS", F + "ProfitLossBeforeTax", "법인세비용차감전이익(손실)", 10, -112903862, cum=-1484559101),
        r("CIS", F + "ProfitLoss", "당기순이익(손실)", 12, -112903862, cum=-1484559101),
        r("CIS", NOSTD, "지배기업 소유주지분", 13, -90260235, cum=-1462319188),
        r("CIS", NOSTD, "비지배지분", 14, -22643627, cum=-22239913),
        r("CIS", F + "OtherComprehensiveIncome", "기타포괄손익", 15, -330436550, cum=-396061259),
        r("CIS", F + "ProfitLossAttributableToOwnersOfParent", "지배기업의 소유주에게 귀속되는 당기순이익(손실)", 22, -112903862, cum=-1484559101),
        r("CIS", F + "ComprehensiveIncomeAttributableToOwnersOfParent", "총 포괄손익, 지배기업의 소유주에게 귀속되는 지분", 24, -420696785, cum=-1858380447),
        r("CIS", F + "BasicEarningsLossPerShare", "기본 및 희석주당이익(손실)", 26, -13, cum=-130),
    ]


def test_073540_owner_id_equal_to_total_yields_to_the_line_that_pairs_with_minority():
    """종전 −14.85억(지배 id 줄 = 반기순이익 — 「비지배 0」 읽기). 비지배 −0.22억과 짝이 맞는 이름 줄을 쓴다."""
    rows = rows_073540_2022h1()
    assert C.controlling_ni(rows) == (-1462319188.0, "CIS_NM")
    assert C.controlling_ni(rows, "thstrm_amount") == (-90260235.0, "CIS_NM")


# ── 바뀌지 않아야 하는 꼴 ─────────────────────────────────────────────────────────────────────────────────────────────────


def test_065150_owner_id_equal_to_total_without_profit_side_minority_stays():
    """065150 2025 사업보고서 — 비지배 순이익 줄은 비었고, 「계속영업이익(손실)」 줄에 총포괄 계속영업 비지배 id(90.90억)를 붙였다. 지배 id 줄 83.15억 =
    당기순이익(자본변동표 지배 몫 83.15억 · 비지배 없음). 그 엉뚱한 비지배 id 줄 + 중단영업 지배 몫 −7.74억 = 83.15억이라 머리 줄처럼 보이지만,
    비지배 줄은 이름이나 순이익 비지배 id 로만 본다 — 종전 그대로(케이스도)."""
    rows = [
        r("CIS", F + "ComprehensiveIncomeAttributableToNoncontrollingInterests", "비지배지분", 11, None),
        r("CIS", F + "ComprehensiveIncomeAttributableToOwnersOfParent", "지배기업소유주지분", 12, 8327026849),
        r("CIS", F + "OtherComprehensiveIncome", "기타포괄손익", 25, 11708384),
        r("CIS", F + "ProfitLoss", "당기순이익(손실)", 28, 8315318465),
        r("CIS", F + "ProfitLossAttributableToNoncontrollingInterests", "비지배지분에 귀속되는 당기순이익(손실)", 29, None),
        r("CIS", F + "ProfitLossAttributableToOwnersOfParent", "지배기업 소유주지분", 30, 8315318465),
        r("CIS", F + "ComprehensiveIncomeFromContinuingOperationsAttributableToNoncontrollingInterests", "계속영업이익(손실)", 31, 9089754844),
        r("CIS", F + "IncomeFromDiscontinuedOperationsAttributableToOwnersOfParent", "중단영업손실", 32, -774436379),
        r("CIS", F + "ProfitLossFromContinuingOperations", "계속영업이익(손실)", 34, 9089754844),
        r("CIS", F + "ProfitLossFromDiscontinuedOperations", "중단영업이익(손실)", 35, -774436379),
    ]
    assert C.controlling_ni(rows) == (8315318465.0, "CIS_ID")
    # 짝 판정 두 겹을 따로 고정한다 — 「중단영업손실」 줄은 지배 짝이 아니고(이름에 「지배」 없음 · 지배 순이익 id 아님), 총포괄 비지배 id 줄은 비지배 짝이 아니다
    assert C._is_owner_row(rows[7]) is False
    assert C._is_nci_row(rows[6]) is False


def test_owner_equal_to_total_is_kept_when_nothing_pairs_with_the_minority():
    """지배 줄 = 당기순이익인데 0 이 아닌 비지배 줄(총포괄 쪽)만 있고 그 비지배와 짝이 맞는 다른 후보가 없으면 종전처럼 그 줄을 쓴다."""
    rows = [
        r("CIS", F + "ProfitLoss", "당기순이익", 1, 1000000000),
        r("CIS", F + "OtherComprehensiveIncome", "기타포괄손익", 2, 50000000),
        r("CIS", F + "ComprehensiveIncome", "총포괄손익", 3, 1050000000),
        r("CIS", F + "ProfitLossAttributableToOwnersOfParent", "지배기업소유주지분", 4, 1000000000),
        r("CIS", F + "ComprehensiveIncomeAttributableToOwnersOfParent", "지배기업소유주지분", 5, 1020000000),
        r("CIS", F + "ComprehensiveIncomeAttributableToNoncontrollingInterests", "비지배지분", 6, 30000000),
    ]
    assert C.controlling_ni(rows) == (1000000000.0, "CIS_ID")


def test_header_needs_a_nonzero_minority_line():
    """다음 두 줄이 지배 줄 + 0 인 비지배 줄이면(= 같은 값) 머리 줄이 아니다 — 비지배 없는 회사의 「지배 몫」 줄이 그 아래 세부 줄과 같아도 그대로."""
    rows = [
        r("CIS", F + "ProfitLoss", "당기순이익", 1, 700000000),
        r("CIS", NOSTD, "지배기업소유주지분", 2, 700000000),
        r("CIS", NOSTD, "비지배지분", 3, 0),
        r("CIS", NOSTD, "지배기업소유주지분", 4, 700000000),
    ]
    assert C._attrib_header(rows[1], rows, C._cum_is) is False
    assert C.controlling_ni(rows)[0] == 700000000.0
    # 261020 p1j 검토: 비지배가 0 이 아니어도 합이 비지배 반보다 촘촘하게 맞아야 머리 줄이다 — 지배 줄 = 머리 줄 값이고 비지배 3원이면(합 오차 3원 >
    # 1.5원) 비지배를 품은 소계가 아니다(허용 오차 150만 원 안이라고 머리 줄로 읽지 않는다).
    rows[2]["thstrm_amount"] = "3"
    assert C._attrib_header(rows[1], rows, C._cum_is) is False


def test_owner_line_that_pairs_with_minority_is_never_a_header():
    """지배 줄 자신이 0 이 아닌 비지배와 짝이 맞으면(800 + 200 = 당기순이익 1,000) 다음 두 줄(비지배 200 + 이름 줄 600)의 합과 같아도 머리 줄이 아니다 —
    백만 원 단위 보고서는 반올림 허용 오차가 커 우연히 맞을 수 있다. 다음 줄이 총포괄 쪽 지배 줄이면 처음부터 짝으로 보지 않는다(표준 꼴 시험 참조)."""
    rows = [
        r("CIS", F + "ProfitLoss", "당기순이익", 12, 1000000000),
        r("CIS", F + "ProfitLossAttributableToOwnersOfParent", "지배기업 소유주지분", 18, 800000000),
        r("CIS", F + "ProfitLossAttributableToNoncontrollingInterests", "비지배지분", 19, 200000000),
        r("CIS", NOSTD, "지배기업 소유주지분(계속영업)", 20, 600000000),
    ]
    assert C.controlling_ni(rows) == (800000000.0, "CIS_ID")
