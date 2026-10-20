"""지배 귀속 순이익 줄 고르기 — 원문 전수 대조(261020 p1f ctrl-scan)에서 공용 규칙(`services/controlling_ni`)이 놓친 꼴.

원문(DART 전체 재무제표 응답) 약 9,700건을 다시 읽어 운영 칸과 대조하다 나온 네 꼴이다. 아래 줄은 그 보고서를 줄였다(값은 원문 그대로, 손익 줄만).
  · 주당이익 이름이 「주당」 뒤에 「지배·계속·중단」을 두는 꼴 — 지배 줄로 골랐다(086060 2021 사업보고서 −56원/주).
  · 계속·중단영업을 각각 지배·비지배로 나눈 꼴 — 비지배 한 줄만으로는 값 구조가 안 맞아 첫 이름 줄을 그대로 썼다(002880 2019, 072770 2023).
  · 총계 이름 앞의 「연결」 — 총계를 못 찾아 값 구조를 볼 수 없었다(003720 2023).
  · 계속영업 몫 줄 없이 「중단영업 지배 몫」만 적은 꼴 — 주당이익 줄을 계속영업 몫으로 짝지어 −470원이 섞였다(025560 2023 3분기).
DART 0콜 · DB 0.
"""
from __future__ import annotations

import pytest

from open_proxy_mcp.services import controlling_ni as C

F = "ifrs-full_"
NOSTD = "-표준계정코드 미사용-"


def r(sj, aid, nm, ordv, cur, cum=None):
    """손익 줄 — `cur` = 당기(사업보고서는 누적과 같다), `cum` = 당기 누적(반기·3분기 보고서)."""
    return {"sj_div": sj, "account_id": aid, "account_nm": nm, "ord": str(ordv), "currency": "KRW",
            "thstrm_amount": "" if cur is None else str(cur), "thstrm_add_amount": "" if cum is None else str(cum)}


def rows_086060_fy2021():
    """지배 순이익 id 줄 = 총포괄(−4.93억, 비지배 없음) · 「기본주당지배회사이익」 −56(원/주) · 당기순이익 −4.84억."""
    return [
        r("CIS", F + "ProfitLoss", "당기순이익(손실)", 58, -484399552),
        r("CIS", F + "OtherComprehensiveIncome", "기타포괄손익", 59, -8858952),
        r("CIS", F + "ComprehensiveIncome", "총포괄손익", 65, -493258504),
        r("CIS", F + "ProfitLossAttributableToOwnersOfParent", "지배기업의 소유주에게 귀속되는 당기순이익(손실)", 67, -493258504),
        r("CIS", F + "ProfitLossAttributableToNoncontrollingInterests", "비지배지분에 귀속되는 당기순이익(손실)", 68, None),
        r("CIS", NOSTD, "기본주당지배회사이익", 71, -56),
        r("CIS", F + "BasicEarningsLossPerShareFromContinuingOperations", "계속영업기본주당이익(손실)", 72, -56),
    ]


def rows_002880_fy2019():
    """계속영업 → 지배 101.83억 · 비지배 77.75억, 중단영업 → 지배 −0.60억 · 비지배 −0.11억, 당기순이익 178.87억."""
    return [
        r("CIS", F + "ProfitLossFromContinuingOperations", "계속영업이익(손실)", 12, 17957960670),
        r("CIS", NOSTD, "지배지분귀속", 13, 10182862425),
        r("CIS", NOSTD, "비지배지분귀속", 14, 7775098245),
        r("CIS", F + "ProfitLossFromDiscontinuedOperations", "중단영업이익(손실)", 15, -70721540),
        r("CIS", NOSTD, "지배지분귀속", 16, -59688980),
        r("CIS", NOSTD, "비지배지분귀속", 17, -11032560),
        r("CIS", F + "ProfitLoss", "당기순이익", 18, 17887239130),
        r("CIS", F + "OtherComprehensiveIncome", "기타포괄손익", 19, 429681755),
        r("CIS", F + "ComprehensiveIncome", "총포괄손익", 27, 18316920885),
        r("CIS", F + "ComprehensiveIncomeAttributableToOwnersOfParent", "지배지분귀속", 29, 9921247893),
        r("CIS", F + "ComprehensiveIncomeAttributableToNoncontrollingInterests", "비지배지분귀속", 30, 8395672992),
        r("CIS", F + "BasicEarningsLossPerShare", "기본주당이익(손실)", 32, 95.1),
    ]


def rows_072770_fy2023():
    """같은 이름(「지배기업의 소유주」·「비지배지분」) 네 쌍 — 총포괄 계속·중단 쌍이 순이익 쌍보다 앞선다. 순이익 = 계속 −80.50억 + 중단 −6.50억 지배,
    비지배 −2.23억 + −2.05억, 당기순이익 −91.27억."""
    return [
        r("CIS", NOSTD, "비지배지분", 11, -246133128),
        r("CIS", NOSTD, "지배기업의 소유주", 12, -10080482959),
        r("CIS", NOSTD, "비지배지분", 14, -187341659),
        r("CIS", NOSTD, "지배기업의 소유주", 15, -399681054),
        r("CIS", NOSTD, "비지배지분", 19, -205017038),
        r("CIS", NOSTD, "지배기업의 소유주", 20, -649864720),
        r("CIS", NOSTD, "비지배지분", 22, -222678474),
        r("CIS", NOSTD, "지배기업의 소유주", 23, -8049875596),
        r("CIS", F + "ComprehensiveIncome", "총포괄손익", 25, -10913638800),
        r("CIS", NOSTD, "중단영업 기본및희석주당순손익", 28, -11),
        r("CIS", F + "BasicEarningsLossPerShare", "계속영업 기본및희석주당순손익", 29, -134),
        r("CIS", F + "OtherComprehensiveIncome", "기타포괄손익", 34, -1786202972),
        r("CIS", F + "ProfitLoss", "당기순이익(손실)", 44, -9127435828),
        r("CIS", F + "ProfitLossFromContinuingOperations", "계속영업이익(손실)", 46, -8272554070),
    ]


def rows_003720_fy2023():
    """표준 계정코드 없는 연결 보고서 — 총계 이름 「연결당기순이익」 189.15억, 순이익 쌍(지배 186.33억 · 비지배 2.81억)이 총포괄 쌍보다 앞선다."""
    return [
        r("CIS", NOSTD, "연결중단 당기순이익", 10, 4904420512),
        r("CIS", NOSTD, "연결당기순이익", 13, 18914809850),
        r("CIS", NOSTD, "연결계속 당기순이익", 14, 14010389338),
        r("CIS", NOSTD, "총포괄이익(손실)", 18, 18327337288),
        r("CIS", NOSTD, "지배기업소유지분", 20, 18633494905),
        r("CIS", NOSTD, "비지배지분", 21, 281314945),
        r("CIS", NOSTD, "지배기업소유지분", 23, 18046022343),
        r("CIS", NOSTD, "비지배지분", 24, 281314945),
        r("CIS", NOSTD, "연결기타포괄손익", 25, -587472562),
        r("CIS", NOSTD, "계속영업기본 및 희석 주당손익", 28, 420),
    ]


def rows_025560_2023q3():
    """3분기 누적 — 「당기순이익의 귀속 → 지배기업의 소유주지분」 −106.69억 + 「중단영업 지배기업의 소유주주지분」 −26.07억 = 당기순이익 −132.76억(비지배 없음).
    주당이익 줄 「지배기업의 소유주지분 기본주당계속영업이익(손실)」 −470(원/주)."""
    return [
        r("CIS", NOSTD, "총포괄이익(손실)의 귀속", 10, -24152625, -13362354079),
        r("CIS", NOSTD, "지배기업의 소유주지분", 11, -235947254, -10755088978),
        r("CIS", NOSTD, "중단영업 지배기업의 소유주주지분", 12, 211794629, -2607265101),
        r("CIS", NOSTD, "당기순이익(손실)의 귀속", 13, -34976630, -13276423521),
        r("CIS", NOSTD, "지배기업의 소유주지분", 14, -246771259, -10669158420),
        r("CIS", NOSTD, "중단영업 지배기업의 소유주주지분", 15, 211794629, -2607265101),
        r("CIS", NOSTD, "지배기업의 소유주지분 기본주당계속영업이익(손실)", 20, -1, -470),
        r("CIS", NOSTD, "계속영업 기본주당이익(손실)", 21, -8, -378),
        r("CIS", F + "ComprehensiveIncome", "총포괄손익", 26, -24152625, -13362354079),
        r("CIS", F + "OtherComprehensiveIncome", "기타포괄손익", 31, 10824005, -85930558),
        r("CIS", F + "ProfitLoss", "당기순이익(손실)", 38, -34976630, -13276423521),
    ]


def rows_196450_fy2025():
    """이 갈래의 출발점 — 지배 id 줄에 총포괄 지배 몫(8.61억), 표준코드 없는 이름 줄에 순이익 지배 몫(32.32억). 계정이 id 알파벳 순."""
    return [
        r("CIS", NOSTD, "비지배지분", 11, -517282207),
        r("CIS", NOSTD, "지배기업소유주지분", 12, 3232168635),
        r("CIS", F + "BasicEarningsLossPerShare", "기본주당순손익", 15, 71),
        r("CIS", F + "OtherComprehensiveIncome", "기타포괄이익", 21, -2538671801),
        r("CIS", NOSTD, "당기총포괄손익", 23, 176214627),
        r("CIS", F + "ProfitLoss", "당기순이익(손실)", 29, 2714886428),
        r("CIS", F + "ProfitLossAttributableToNoncontrollingInterests", "비지배지분", 31, -684749623),
        r("CIS", F + "ProfitLossAttributableToOwnersOfParent", "지배기업소유주지분", 32, 860964250),
    ]


@pytest.mark.parametrize("nm", ["기본주당지배회사이익", "지배기업주당계속영업이익", "지배기업지분에대한기본및희석주당중단영업손실",
                                "지배기업소유주지분주당계속사업손익", "지배기업의소유주지분기본주당계속영업이익(손실)", "기본주당이익", "기본주당순이익(손실)"])
def test_eps_names_are_not_owner_lines(nm):
    assert C._EPS_NM.search(nm)


@pytest.mark.parametrize("nm", ["지배주주당기순이익", "지배기업소유주당기총포괄손익", "지배주주보통주당기순이익", "지배기업소유주지분순이익",
                                "기본주당이익계산에사용되는지배기업의보통주에귀속되는계속영업이익(손실)",
                                "희석주당이익계산에사용되는,지배기업의보통주에귀속되는중단영업이익(손실)"])
def test_amount_lines_with_judang_are_not_eps(nm):
    """「주주 + 당기」(주당기)·「주당이익 계산에 사용되는 …」 금액 줄은 주당이익이 아니다 — 종전처럼 후보로 남는다."""
    assert not C._EPS_NM.search(nm)


def test_eps_named_owner_line_is_not_picked():
    assert C.controlling_ni(rows_086060_fy2021()) == (-484399552.0, "TOTAL_CIREJ")


def test_continuing_and_discontinued_split_with_two_minority_lines():
    v, case = C.controlling_ni(rows_002880_fy2019())
    assert (v, case) == (10182862425.0 - 59688980.0, "CIS_PARTS")
    assert v + 7775098245 - 11032560 == 17887239130


def test_four_same_named_pairs_pick_the_profit_pair():
    v, case = C.controlling_ni(rows_072770_fy2023())
    assert (v, case) == (-8049875596.0 - 649864720.0, "CIS_PARTS")
    assert v - 222678474 - 205017038 == pytest.approx(-9127435828, abs=2)


def test_consolidated_total_name_lets_the_shape_be_checked():
    rows = rows_003720_fy2023()
    shape = C._ni_shape(rows)
    assert shape[0] == 18914809850.0 and shape[1] == 18327337288.0
    assert C.ni_attrib_side(18633494905.0, shape) == "ni"
    assert C.ni_attrib_side(18046022343.0, shape) == "ci"
    assert C.controlling_ni(rows) == (18633494905.0, "CIS_NM")


def test_discontinued_owner_line_alone_pairs_with_named_owner_line():
    assert C.controlling_ni(rows_025560_2023q3()) == (-13276423521.0, "CIS_PARTS")


def test_id_line_with_comprehensive_share_falls_to_named_profit_line():
    """회귀 막이(main 도 같은 값) — 196450 FY2025: id 줄 8.61억(총포괄 지배 몫)을 버리고 이름 줄 32.32억."""
    assert C.controlling_ni(rows_196450_fy2025()) == (3232168635.0, "CIS_NM")


def test_two_minority_lines_only_for_two_owner_parts():
    """한 줄 후보는 종전 그대로 — 비지배 두 줄 합으로 맞는 한 줄(우연)은 순이익 줄로 보지 않는다(두 줄의 합에만 쓴다)."""
    rows = [
        r("CIS", F + "ProfitLoss", "당기순이익", 1, 1234567),
        r("CIS", NOSTD, "비지배지분", 2, 111111),
        r("CIS", NOSTD, "비지배지분", 3, 222222),
        r("CIS", NOSTD, "지배기업소유주지분", 4, 901234),
    ]
    assert C.ni_attrib_side(901234.0, C._ni_shape(rows)) is None
    assert C._fits_nci_pair(901234.0, C._ni_shape(rows))


# ── 261020 p1f ctrl-scan 검토 ──────────────────────────────────────────────────────────────────────────────────────────────


def rows_289220_fy2022():
    """당기순손실 −126.76억인데 귀속 두 줄을 양수로 적었다 — 「지배기업주주지분」 +122.72억 · 「비지배주주지분」 +4.04억(총포괄 쪽도 같은 꼴).
    자본변동표: 지배 몫 −122.72억 · 비지배 −4.04억."""
    return [
        r("CIS", F + "ProfitLossBeforeTax", "법인세비용차감전순손실", 7, -12725175611),
        r("CIS", F + "ProfitLoss", "당기순손실", 11, -12676277323),
        r("CIS", F + "OtherComprehensiveIncome", "기타포괄손익", 12, 76606959),
        r("CIS", F + "ComprehensiveIncome", "총포괄손익", 15, -12599670364),
        r("CIS", F + "ProfitLossAttributableToOwnersOfParent", "지배기업주주지분", 17, 12272184976),
        r("CIS", F + "ProfitLossAttributableToNoncontrollingInterests", "비지배주주지분", 18, 404092347),
        r("CIS", F + "ComprehensiveIncomeAttributableToOwnersOfParent", "지배기업주주지분", 20, 12195578017),
        r("CIS", F + "ComprehensiveIncomeAttributableToNoncontrollingInterests", "비지배주주지분", 21, 404092347),
    ]


def rows_284620_fy2021():
    """당기순손실 −162.25억 · 「지배기업의 소유주에게 귀속되는 당기순이익(손실)」 +162.12억 · 비지배 +0.14억 — 자본변동표 지배 몫 −162.12억."""
    return [
        r("CIS", F + "ProfitLoss", "당기순이익(손실)", 11, -16225404831),
        r("CIS", F + "ProfitLossAttributableToOwnersOfParent", "지배기업의 소유주에게 귀속되는 당기순이익(손실)", 13, 16211563377),
        r("CIS", F + "ProfitLossAttributableToNoncontrollingInterests", "비지배지분에 귀속되는 당기순이익(손실)", 14, 13841454),
        r("CIS", F + "OtherComprehensiveIncome", "기타포괄손익", 15, 1367692197),
        r("CIS", F + "ComprehensiveIncome", "총포괄손익", 19, -14857712634),
        r("CIS", F + "ComprehensiveIncomeAttributableToOwnersOfParent", "지배기업의 소유주에게 귀속되는 총포괄손익", 21, -14843353293),
        r("CIS", NOSTD, "비지배지분에 귀속되는 총포괄손익", 22, -14359341),
    ]


@pytest.mark.parametrize("rows, want", [(rows_289220_fy2022, -12272184976.0), (rows_284620_fy2021, -16211563377.0)])
def test_owner_and_minority_lines_both_sign_flipped(rows, want):
    """종전(총계 − 비지배)은 뒤집힌 비지배를 빼 비지배 두 배만큼 어긋났다(289220 −130.80억 · 284620 −162.39억)."""
    assert C.controlling_ni(rows()) == (want, "CIS_IDNEG")


def test_flipped_owner_line_with_zero_minority_stays_total_minus_minority():
    """비지배가 0 이면 −지배 줄 = 총계 − 0 — 가를 필요가 없어 종전 케이스 그대로(298000 꼴)."""
    rows = [r("CIS", F + "ProfitLoss", "당기순손실", 1, -73901234567),
            r("CIS", F + "OtherComprehensiveIncome", "기타포괄손익", 2, 1234567),
            r("CIS", F + "ComprehensiveIncome", "총포괄손익", 3, -73900000000),
            r("CIS", F + "ProfitLossAttributableToOwnersOfParent", "지배기업소유주지분", 4, 73901234567),
            r("CIS", F + "ProfitLossAttributableToNoncontrollingInterests", "비지배지분", 5, 0)]
    assert C.controlling_ni(rows) == (-73901234567.0, "TOTAL_MINUS_MINORITY_IDX")


def test_flipped_pair_needs_a_corroborated_total():
    """총계 줄을 총포괄 − 기타포괄로 믿을 수 없으면(두 줄이 없으면) 지배 줄을 버리지도 뒤집지도 않는다 — 종전 그대로."""
    rows = [x for x in rows_289220_fy2022() if "Comprehensive" not in x["account_id"]]
    v, case = C.controlling_ni(rows)
    assert case != "CIS_IDNEG" and v == 12272184976.0


def test_discontinued_owner_line_alone_is_never_the_answer():
    """계속영업 몫 줄 없는 「중단영업 지배 몫」 줄은 두 줄 합에만 쓴다 — 지배 id 줄을 총포괄 몫이라 버렸거나 지배 줄이 없으면 총계 − 비지배."""
    ci_rejected = [
        r("CIS", F + "ProfitLoss", "당기순이익", 1, 1000000007),
        r("CIS", F + "OtherComprehensiveIncome", "기타포괄손익", 2, 200000003),
        r("CIS", F + "ComprehensiveIncome", "총포괄손익", 3, 1200000010),
        r("CIS", F + "ProfitLossAttributableToOwnersOfParent", "지배기업소유주지분", 4, 1100000009),
        r("CIS", F + "ProfitLossAttributableToNoncontrollingInterests", "비지배지분", 5, 100000001),
        r("CIS", NOSTD, "중단영업지배기업소유주지분", 6, 30000001),
    ]
    assert C.controlling_ni(ci_rejected) == (900000006.0, "TOTAL_MINUS_MINORITY_CIREJ")
    no_owner = [ci_rejected[0], ci_rejected[4],
                r("CIS", F + "ProfitLossFromDiscontinuedOperationsAttributableToOwnersOfParent", "중단영업이익의 지배기업소유주 귀속분", 6, 30000001)]
    assert C.controlling_ni(no_owner) == (900000006.0, "TOTAL_MINUS_MINORITY")


def _ci_share_only(extra):
    """지배 몫 줄이 총포괄 몫(1,100,000,009 + 비지배 100,000,001 = 총포괄 1,200,000,010)뿐인 표준코드 없는 연결 보고서 — 총포괄 총계를 이름으로만 찾을 수 있다."""
    return [r("CIS", NOSTD, "연결당기순이익", 1, 1000000007), *extra,
            r("CIS", NOSTD, "지배기업소유주지분", 4, 1100000009), r("CIS", NOSTD, "비지배지분", 5, 100000001)]


@pytest.mark.parametrize("extra", [[r("CIS", NOSTD, "연결총포괄이익", 3, 1200000010)], [r("CIS", NOSTD, "연결기타포괄손익", 2, 200000003)]],
                         ids=["연결총포괄이익", "연결기타포괄손익"])
def test_consolidated_ci_and_oci_total_names_reject_the_ci_share(extra):
    """「연결」이 붙은 총포괄·기타포괄 총계 이름으로도 총포괄 몫 줄을 가려 버린다(종전엔 가를 수 없는 줄로 그 값을 썼다)."""
    v, case = C.controlling_ni(_ci_share_only(extra))
    assert case.endswith("_CIREJ") and v == 1000000007.0
