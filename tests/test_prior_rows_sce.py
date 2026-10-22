"""분기 전년 동기 칸(`_as_prior_rows`)도 자본변동표(SCE) 줄을 같은 기간 칸으로 옮겨 지배 몫 규칙(TOTAL_SCE)을 따른다(DATA-NI3R3).

종전 `_as_prior_rows` 는 손익(IS·CIS) 줄만 전년 동기 누적 칸으로 옮기고 자본변동표 줄은 당기 칸 그대로 뒀다. 그러면 당기 칸은 `TOTAL_SCE`(지배 몫)인데
전기 칸은 대조 문이 안 맞아 `TOTAL`(비지배 포함 총계)로 남아 한 종목 안에서 기준이 섞인다(274400 2022 반기·3분기 · 2023 1분기 → ni_cum_restated).
분기 자본변동표의 전년 동기 누적은 `frmtrm_q_amount` 칸이다(10-22 원문 캐시 반기·1분기·3분기 자본변동표 순이익·중단영업 줄 42,298줄 가운데 이 칸이 있는 줄 41,664줄 = 98.5%).
아래 줄은 그 보고서(DART 전체 재무제표 응답)를 줄였다 — 계정 id·이름·ord·값·열(account_detail)은 원문 그대로, 규칙이 보는 손익·자본변동표 순이익 줄만.
DART 0콜 · DB 0.
"""
from __future__ import annotations

import pytest

from open_proxy_mcp.services import controlling_ni as C
from scripts import market_fund_quarterly as Q
from tests.test_controlling_ni_sce import rows_081580_2022q1, rows_086520_2021h1, rows_089470_2023h1

F = "ifrs-full_"


def _i(sj, aid, nm, ordv, cur, cum, prev_q, prev_cum):
    """손익 줄 — 당기(3개월) · 당기 누적 · 전년 동기(3개월 `frmtrm_q_amount`) · 전년 동기 누적(`frmtrm_add_amount`)."""
    return {"sj_div": sj, "account_id": F + aid, "account_nm": nm, "ord": str(ordv), "currency": "KRW",
            "thstrm_amount": "" if cur is None else str(cur), "thstrm_add_amount": "" if cum is None else str(cum),
            "frmtrm_q_amount": "" if prev_q is None else str(prev_q), "frmtrm_add_amount": "" if prev_cum is None else str(prev_cum)}


def _s(aid, nm, detail, ordv, cur, prev_q):
    """자본변동표 순이익 줄 — 분기 보고서의 당기 누적(`thstrm_amount`)과 전년 동기 누적(`frmtrm_q_amount`)."""
    return {"sj_div": "SCE", "account_id": aid, "account_nm": nm, "account_detail": detail, "ord": str(ordv), "currency": "KRW",
            "thstrm_amount": "" if cur is None else str(cur), "frmtrm_q_amount": "" if prev_q is None else str(prev_q)}

def rows_274400_2023h1():
    """274400 2023 반기(접수 20230811002130) — 2022 반기 칸의 출처. 전기 누적 당기순이익 −57.12억 = 자본변동표 전기 지배 −55.81억 + 비지배 −1.31억."""
    return [{**x, "reprt_code": "11012"} for x in [
        _i('CIS', 'ProfitLossBeforeTax', '법인세비용차감전순이익(손실)', 9, -1339335611, -2012968812, -3452946352, -4319678956),
        _i('CIS', 'IncomeTaxExpenseContinuingOperations', '법인세비용', 10, 14573530, 20485521, -77350863, -77350863),
        _i('CIS', 'ProfitLossFromContinuingOperations', '계속영업이익(손실)', 11, -1353909141, -2033454333, -3375595489, -4242328093),
        _i('CIS', 'ProfitLossFromDiscontinuedOperations', '중단영업이익(손실)', 12, None, None, -1011555462, -1470135372),
        _i('CIS', 'ProfitLoss', '당기순이익(손실)', 13, -1353909141, -2033454333, -4387150951, -5712463465),
        _i('CIS', 'OtherComprehensiveIncome', '기타포괄손익', 14, -61308102, -75275464, -194780731, -154161564),
        _i('CIS', 'ComprehensiveIncome', '총포괄손익', 15, -1415217243, -2108729797, -4581931682, -5866625029),
        _s('ifrs-full_ProfitLoss', '당기순이익(손실)', '자본 [member]|지배기업의 소유주에게 귀속되는 자본 [member]', 4, -2014777113, -5581375364),
        _s('ifrs-full_ProfitLoss', '당기순이익(손실)', '자본 [member]|지배기업의 소유주에게 귀속되는 자본 [member]|이익잉여금 [member]', 4, -2014777113, -5581375364),
        _s('ifrs-full_ProfitLoss', '당기순이익(손실)', '자본 [member]|비지배지분 [member]', 4, -18677220, -131088101),
        _s('ifrs-full_ProfitLoss', '당기순이익(손실)', '연결재무제표 [member]', 4, -1353909141, -4387150951),
    ]]


def rows_274400_2023q3():
    """274400 2023 3분기 — 2022 3분기 칸의 출처. 전기 누적 −51.59억 = 자본변동표 전기 지배(이익잉여금 열만 있음) −50.04억 + 비지배 −1.55억."""
    return [{**x, "reprt_code": "11014"} for x in [
        _i('CIS', 'ComprehensiveIncome', '총포괄손익', 10, -980146739, -3088876536, 537706810, -5328918219),
        _i('CIS', 'IncomeTaxExpenseContinuingOperations', '법인세비용(수익)', 19, 20485521, 20485521, None, -77350863),
        _i('CIS', 'OtherComprehensiveIncome', '기타포괄손익', 20, -9986307, -85261771, -15483933, -169645497),
        _i('CIS', 'ProfitLoss', '당기순이익(손실)', 21, -970160432, -3003614765, 553190743, -5159272722),
        _i('CIS', 'ProfitLossBeforeTax', '법인세비용차감전순이익(손실)', 22, -970160432, -2983129244, -376865380, -4696544336),
        _i('CIS', 'ProfitLossFromContinuingOperations', '계속영업이익(손실)', 23, -970160432, -3003614765, -376865380, -4619193473),
        _i('CIS', 'ProfitLossFromDiscontinuedOperations', '중단영업이익(손실)', 24, None, None, 930056123, -540079249),
        _s('ifrs-full_ProfitLoss', '당기순이익(손실)', '연결재무제표 [member]', 13, -970160432, 553190743),
        _s('ifrs-full_ProfitLoss', '당기순이익(손실)', '자본 [구성요소]|지배기업의 소유주에게 귀속되는 지분 [구성요소]|이익잉여금 [구성요소]', 13, -2981776360, -5004004870),
        _s('ifrs-full_ProfitLoss', '당기순이익(손실)', '자본 [구성요소]|비지배지분 [구성요소]', 13, -21838405, -155267852),
    ]]


def rows_274400_2024q1():
    """274400 2024 1분기 — 2023 1분기 칸의 출처. 전기 −6.80억 = 자본변동표 전기 지배(구성요소 경로) −6.70억 + 비지배 −0.10억."""
    return [{**x, "reprt_code": "11013"} for x in [
        _i('CIS', 'ComprehensiveIncome', '총포괄손익', 10, -890020173, -890020173, -693512554, -693512554),
        _i('CIS', 'IncomeTaxExpenseContinuingOperations', '법인세비용(수익)', 19, 0, 0, -5911991, -5911991),
        _i('CIS', 'OtherComprehensiveIncome', '기타포괄손익', 20, -15779511, -15779511, -13967362, -13967362),
        _i('CIS', 'ProfitLoss', '당기순이익(손실)', 21, -874240662, -874240662, -679545192, -679545192),
        _i('CIS', 'ProfitLossBeforeTax', '법인세비용차감전순이익(손실)', 22, -874240662, -874240662, -673633201, -673633201),
        _i('CIS', 'ProfitLossFromContinuingOperations', '계속영업이익(손실)', 23, -874240662, -874240662, -679545192, -679545192),
        _i('CIS', 'ProfitLossFromDiscontinuedOperations', '중단영업이익(손실)', 24, 0, 0, 0, 0),
        _s('ifrs-full_ProfitLoss', '당기순이익(손실)', '자본 [구성요소]|지배기업의 소유주에게 귀속되는 지분 [구성요소]', 9, -870505935, -669828711),
        _s('ifrs-full_ProfitLoss', '당기순이익(손실)', '연결재무제표 [member]', 9, -874240662, -679545192),
        _s('ifrs-full_ProfitLoss', '당기순이익(손실)', '자본 [구성요소]|비지배지분 [구성요소]', 9, -3734727, -9716481),
        _s('ifrs-full_ProfitLoss', '당기순이익(손실)', '자본 [구성요소]|지배기업의 소유주에게 귀속되는 지분 [구성요소]|이익잉여금 [구성요소]', 9, -870505935, -669828711),
    ]]


def rows_302550_2020q3():
    """302550 2020 3분기 — 2019 3분기 칸의 출처. 자본변동표 전기 비지배 칸이 비어 있고 지배 몫 25.98억 = 전기 누적 당기순이익 25.98억(전기엔 비지배가 없었다)."""
    return [{**x, "reprt_code": "11014"} for x in [
        _i('CIS', 'ProfitLossBeforeTax', '법인세비용차감전순이익(손실)', 58, -9797219, 2370634900, 1726726038, 2493205708),
        _i('CIS', 'IncomeTaxExpenseContinuingOperations', '법인세비용', 59, 42197181, -408398308, 211481968, 104793903),
        _i('CIS', 'ProfitLoss', '당기순이익(손실)', 60, -51994400, 2779033208, 1938208006, 2597999611),
        _i('CIS', 'OtherComprehensiveIncome', '기타포괄손익', 61, 63009188, 58051631, 13288, -382286),
        _i('CIS', 'ComprehensiveIncome', '총포괄손익', 64, 11014788, 2837084839, 1938221294, 2597617325),
        _s('ifrs-full_ProfitLoss', '당기순이익(손실)', '자본 [member]|지배기업의 소유주에게 귀속되는 자본 [member]', 3, 2887025192, 2597999611),
        _s('ifrs-full_ProfitLoss', '당기순이익(손실)', '자본 [member]|지배기업의 소유주에게 귀속되는 자본 [member]|이익잉여금 [member]', 3, 2887025192, 2597999611),
        _s('ifrs-full_ProfitLoss', '당기순이익(손실)', '자본 [member]|비지배지분 [member]', 3, -107991984, None),
        _s('ifrs-full_ProfitLoss', '당기순이익(손실)', '연결재무제표 [member]', 3, -51994400, 1938208006),
    ]]


def rows_307180_2020q3():
    """307180 2020 3분기 — 2019 3분기 칸의 출처. 자본변동표 전기 비지배 칸이 비어 있고 지배 몫 1.54억 = 전기 누적 당기순이익 1.54억."""
    return [{**x, "reprt_code": "11014"} for x in [
        _i('CIS', 'ProfitLossBeforeTax', '법인세비용차감전순이익(손실)', 9, 24803224, -3111425797, -1148373208, -145371895),
        _i('CIS', 'IncomeTaxExpenseContinuingOperations', '법인세비용', 10, 47410827, 78947169, None, 299503455),
        _i('CIS', 'ProfitLoss', '당기순이익(손실)', 11, 72214051, -3032478628, -1148373208, 154131560),
        _i('CIS', 'ComprehensiveIncome', '총포괄손익', 13, 72214051, -3032478628, -1148373208, 154131560),
        _s('ifrs-full_ProfitLoss', '당기순이익(손실)', '자본 [member]|지배기업의 소유주에게 귀속되는 자본 [member]', 4, -2960213580, 154131560),
        _s('ifrs-full_ProfitLoss', '당기순이익(손실)', '자본 [member]|지배기업의 소유주에게 귀속되는 자본 [member]|이익잉여금 [member]', 4, -2960213580, 154131560),
        _s('ifrs-full_ProfitLoss', '당기순이익(손실)', '자본 [member]|비지배지분 [member]', 4, -72265048, None),
        _s('ifrs-full_ProfitLoss', '당기순이익(손실)', '연결재무제표 [member]', 4, 72214051, -1148373208),
    ]]


def rows_480370_2025q3():
    """480370 2025 3분기 — 2024 3분기 칸의 출처. 자본변동표 전기 비지배 0 · 지배 몫 98.09억 = 전기 누적 당기순이익 98.09억."""
    return [{**x, "reprt_code": "11014"} for x in [
        _i('CIS', 'ComprehensiveIncome', '총포괄손익', 11, -6479966730, -23929927297, -3529440419, 9647277910),
        _i('CIS', 'IncomeTaxExpenseContinuingOperations', '법인세비용(수익)', 19, 663420967, 6570383947, 1840466011, 1592982843),
        _i('CIS', 'OtherComprehensiveIncome', '기타포괄손익', 20, 431254039, 1001382319, -374088715, -161846560),
        _i('CIS', 'ProfitLoss', '당기순이익(손실)', 23, -6911220769, -24931309616, -3155351704, 9809124470),
        _i('CIS', 'ProfitLossBeforeTax', '법인세비용차감전순이익(손실)', 24, -7574641736, -31501693563, -4995817715, 11402107313),
        _s('ifrs-full_ProfitLoss', '당기순이익(손실)', '자본 [구성요소]|비지배지분 [구성요소]', 11, -231388, 0),
        _s('ifrs-full_ProfitLoss', '당기순이익(손실)', '자본 [구성요소]|지배기업의 소유주에게 귀속되는 지분 [구성요소]', 11, -24931078228, 9809124470),
        _s('ifrs-full_ProfitLoss', '당기순이익(손실)', '자본 [구성요소]|지배기업의 소유주에게 귀속되는 지분 [구성요소]|기타포괄손익누계액 [구성요소]', 11, 0, 0),
        _s('ifrs-full_ProfitLoss', '당기순이익(손실)', '자본 [구성요소]|지배기업의 소유주에게 귀속되는 지분 [구성요소]|이익잉여금 [구성요소]', 11, -24931078228, 9809124470),
        _s('ifrs-full_ProfitLoss', '당기순이익(손실)', '자본 [구성요소]|지배기업의 소유주에게 귀속되는 지분 [구성요소]|자본금 [구성요소]', 11, 0, 0),
        _s('ifrs-full_ProfitLoss', '당기순이익(손실)', '자본 [구성요소]|지배기업의 소유주에게 귀속되는 지분 [구성요소]|자본잉여금 [구성요소]', 11, 0, 0),
        _s('ifrs-full_ProfitLoss', '당기순이익(손실)', '자본 [구성요소]|지배기업의 소유주에게 귀속되는 지분 [구성요소]|자본조정', 11, 0, 0),
        _s('ifrs-full_ProfitLoss', '당기순이익(손실)', '연결재무제표 [member]', 11, -6911220769, -3155351704),
    ]]




def _prior(rows, quarter):
    return Q.extract_controlling_ni_cum(Q._as_prior_rows(rows, quarter))


def _twin(rows):
    """당기 칸을 전년 동기 칸으로 복제한 보고서 — 전년 동기 값이 당기 값과 같은 합성. 가드(총계 확인·중단영업)가 전기 칸에서도 당기 칸과 같이 움직이는지 본다."""
    out = []
    for x in rows:
        y = dict(x)
        if x["sj_div"] in ("IS", "CIS"):
            y["frmtrm_q_amount"], y["frmtrm_add_amount"] = x.get("thstrm_amount", ""), x.get("thstrm_add_amount", "")
        elif x["sj_div"] == "SCE":
            y["frmtrm_q_amount"] = x.get("thstrm_amount", "")
        out.append(y)
    return out


# ───────────── 고치기 전엔 실패하는 시험 — 274400 세 칸 (종전 값은 전부 `TOTAL`, 비지배 포함 총계) ─────────────
@pytest.mark.parametrize("make, quarter, want, was", [
    (rows_274400_2023h1, 2, -5581375364.0, -5712463465.0),
    (rows_274400_2023q3, 3, -5004004870.0, -5159272722.0),
    (rows_274400_2024q1, 1, -669828711.0, -679545192.0),
], ids=["2022반기", "2022_3분기", "2023_1분기"])
def test_274400_prior_cell_follows_the_sce_controlling_share(make, quarter, want, was):
    """274400 — 같은 보고서의 당기 칸은 이미 `TOTAL_SCE`(지배 몫)인데 전년 동기 칸은 `TOTAL`(총계)이었다. 자본변동표 전기 칸(지배 + 비지배 = 전기 누적 당기순이익)을
    같은 기간 칸으로 옮기면 지배 몫이다. 종전 값(`was`)은 비지배 몫만큼 크다(2.3% · 3.1% · 1.4%)."""
    rows = make()
    assert C.controlling_ni(rows)[1] == "TOTAL_SCE"                                   # 당기 칸은 이미 지배 몫 기준
    assert _prior(rows, quarter) == (want, "TOTAL_SCE")                                # 종전 (was, "TOTAL")
    assert was != want


# ───────────── 그대로여야 하는 칸 — 전기에 비지배가 없다(자본변동표 전기 비지배 칸이 비었거나 0) ─────────────
@pytest.mark.parametrize("make, quarter, want", [
    (rows_302550_2020q3, 3, 2597999611.0),
    (rows_307180_2020q3, 3, 154131560.0),
    (rows_480370_2025q3, 3, 9809124470.0),
], ids=["302550_2019_3분기", "307180_2019_3분기", "480370_2024_3분기"])
def test_prior_cell_without_prior_nci_stays_total(make, quarter, want):
    """302550 · 307180 · 480370 — 당기 칸은 `TOTAL_SCE` 인데 전기 칸은 총계 그대로다. 그러나 자본변동표 전기 지배 몫이 전기 누적 당기순이익과 같다(전기엔 비지배 몫이
    없거나 0) — 총계가 곧 지배 몫이라 기준이 섞인 것이 아니다. 옮겨도 값은 그대로(자본변동표를 쓴 값 = 총계)."""
    rows = make()
    assert C.controlling_ni(rows)[1] == "TOTAL_SCE"
    assert _prior(rows, quarter) == (want, "TOTAL")
    pr = Q._as_prior_rows(rows, quarter)
    assert C._sce_ctrl_ni(pr) == want                                                  # 자본변동표 전기 지배 몫 = 총계 — 서로 확인


# ───────────── 가드는 전기 칸에도 ─────────────
def test_prior_sce_that_does_not_fit_the_prior_total_is_not_used():
    """대조 문(지배 + 비지배 = 전기 누적 당기순이익)이 안 맞으면 자본변동표를 쓰지 않는다 — 합성: 274400 반기 전기 비지배를 −1억으로."""
    rows = rows_274400_2023h1()
    for x in rows:
        if x["sj_div"] == "SCE" and "비지배" in x["account_detail"]:
            x["frmtrm_q_amount"] = "-100000000"
    assert C._sce_ctrl_ni(Q._as_prior_rows(rows, 2)) is None
    assert _prior(rows, 2) == (-5712463465.0, "TOTAL")


def test_prior_sce_blank_prior_column_does_not_borrow_this_period():
    """전기 칸이 빈 자본변동표 줄은 비운다 — 당기 값을 빌려 쓰지 않는다. 합성: 274400 반기 자본변동표 전기 칸을 모두 지움 + 당기 칸을 전기 총계에 맞춰 둠(빌리면 맞아 떨어진다)."""
    rows = rows_274400_2023h1()
    cur = {"지배": -5581375364, "비지배": -131088101}
    for x in rows:
        if x["sj_div"] == "SCE":
            if "지배" in x["account_detail"] and "비지배" not in x["account_detail"]:
                x["thstrm_amount"] = str(cur["지배"])
            elif "비지배" in x["account_detail"]:
                x["thstrm_amount"] = str(cur["비지배"])
            x["frmtrm_q_amount"] = ""
    assert C._sce_ctrl_ni(Q._as_prior_rows(rows, 2)) is None
    assert _prior(rows, 2) == (-5712463465.0, "TOTAL")


def test_prior_q1_total_line_with_different_columns_does_not_trust_sce():
    """① 081580 2022 1분기 꼴이 전기 칸에서도 — 전년 동기 3개월 칸 ≠ 전년 동기 누적 칸이면 그 총계 줄을 못 믿고, 총계가 확인되지 않으면 자본변동표를 쓰지 않는다.
    합성: 081580 의 당기 칸을 전기 칸으로 복제(`_twin`). 종전엔 옮긴 손익 줄의 두 칸을 같은 값으로 채워 이 가드가 전기 칸에서 꺼졌다."""
    rows = rows_081580_2022q1()
    assert C.controlling_ni(rows) == (2712238401.0, "CIS_ID")
    pr = Q._as_prior_rows(_twin(rows), 1)
    assert C._tot_cols_disagree(pr)
    assert C._sce_ctrl_ni(pr) is None
    assert Q.extract_controlling_ni_cum(pr) == (2712238401.0, "CIS_ID")


def test_prior_q1_total_confirmed_by_other_lines_keeps_sce():
    """① 의 반대 — 274400 2024 1분기의 전기 3개월 칸을 틀리게 해도(전기 누적 칸이 맞는 쪽) 총포괄 − 기타포괄이 전기 누적 총계와 맞으면 자본변동표를 쓴다."""
    rows = rows_274400_2024q1()
    for x in rows:
        if x["sj_div"] == "CIS" and x["account_id"] == F + "ProfitLoss":
            x["frmtrm_q_amount"] = "-600000000"
    pr = Q._as_prior_rows(rows, 1)
    assert C._tot_cols_disagree(pr)
    assert _prior(rows, 1) == (-669828711.0, "TOTAL_SCE")


def test_prior_q1_total_not_confirmed_falls_back():
    """① — 위에서 확인 줄(총포괄·기타포괄·세전·법인세)을 지우면 총계를 못 믿으니 자본변동표를 쓰지 않는다(종전 값 = 전기 누적 총계)."""
    rows = [x for x in rows_274400_2024q1() if x["sj_div"] != "CIS" or x["account_id"] == F + "ProfitLoss"]
    for x in rows:
        if x["sj_div"] == "CIS":
            x["frmtrm_q_amount"] = "-600000000"
    pr = Q._as_prior_rows(rows, 1)
    assert C._tot_cols_disagree(pr)
    assert C._sce_ctrl_ni(pr) is None
    assert _prior(rows, 1) == (-679545192.0, "TOTAL")


def test_prior_continuing_only_total_needs_continuing_plus_discontinued_sce():
    """② 089470 2023 반기 꼴이 전기 칸에서도 — 총계 줄이 계속영업 몫뿐이고 중단영업 순이익 줄이 따로 있으면 자본변동표 계속영업 줄 + 중단영업 줄이 각각 맞을 때만 쓴다.
    합성: 089470 의 당기 칸을 전기 칸으로 복제. 중단영업 줄의 전기 칸을 지우면(그 줄은 당기 칸이 남아 있다) 쓰지 않는다 — 종전엔 자본변동표를 안 옮겨 당기 값이 남아 맞았다."""
    rows = _twin(rows_089470_2023h1())
    pr = Q._as_prior_rows(rows, 2)
    assert C._sce_ctrl_ni(pr) == 18344963669.0 + 3059969816.0
    for x in rows:
        if x["sj_div"] == "SCE" and "중단" in x["account_nm"]:
            x["frmtrm_q_amount"] = ""
    assert C._sce_ctrl_ni(Q._as_prior_rows(rows, 2)) is None


@pytest.mark.parametrize("make, quarter", [
    (rows_086520_2021h1, 2), (rows_089470_2023h1, 2), (rows_081580_2022q1, 1),
], ids=["086520", "089470", "081580"])
def test_prior_twin_gives_the_same_value_as_the_current_cell(make, quarter):
    """전년 동기 값이 당기 값과 같은 보고서(`_twin`)면 전기 칸 결과 = 당기 칸 결과 — 값 고르기·자본변동표 대조·가드가 두 칸에서 같다."""
    rows = make()
    assert Q.extract_controlling_ni_cum(Q._as_prior_rows(_twin(rows), quarter)) == Q.extract_controlling_ni_cum(rows)


def test_non_income_sce_rows_are_left_alone():
    """자본변동표 순이익·중단영업 줄만 옮긴다 — 다른 자본변동표 줄(기초·기말 자본)은 그대로(전기 말 자본은 `prior_equity_from_sce` 가 따로 읽는다)."""
    eq = {"sj_div": "SCE", "account_id": F + "Equity", "account_nm": "기말자본", "account_detail": "연결재무제표 [member]",
          "thstrm_amount": "10", "frmtrm_q_amount": "7", "reprt_code": "11012"}
    assert Q._as_prior_rows([eq], 2) == [eq]
