"""지배 귀속 순이익 줄 고르기 — 값 구조로 못 가르는 꼴을 같은 응답의 자본변동표(SCE) 지배 몫으로 가른다(261021 ni-sce, DATA-NI3).

공용 규칙(`services/controlling_ni`)은 후보 줄마다 값 구조(지배 + 비지배 = 당기순이익)를 본다. 그것으로 못 가르는 세 곳이 남았다:
  · 가를 수 없는 줄로 물러남 — 086520 2021 반기 27.60억(계속영업 몫만) → 5,613.54억 · 069510 2019 지배 id 줄 = 기타포괄 · 289220 2020 손실을 양수로.
  · 지배·비지배 짝이 대칭(v + y = 총계면 y + v 도) — 원문이 두 값을 바꿔 적은 009070 2022(18.08억 → 262.89억) · 004590 2023 사업보고서(전기 칸 −724.5만 → 117.90억).
  · 총계 규칙으로 물러난 길(`_CIREJ`·`_CINCI`·`_IDX`) — 052770 2021 반기 · 347860 2023 1분기 · 145270 2025 · 196490 2020 · 138360 2020 별도.
자본변동표 지배 몫은 SCE 지배 + SCE 비지배 = 손익계산서 당기순이익일 때만 쓴다(`_sce_ctrl_ni`). 값 구조로 이미 가른 길은 그대로다.
아래 줄은 그 보고서(DART 전체 재무제표 응답)를 줄였다 — 계정 id·이름·ord·값·자본변동표 열(account_detail)은 원문 그대로, 규칙이 보는 손익·자본변동표 순이익 줄만.
DART 0콜 · DB 0.

261021 ni-sce-fp: 위 대조가 거짓으로 맞는 두 꼴을 막는다(DATA-NI3 열린 결함 — 081580 2022 1분기 · 089470 2023 반기).
  ① 1분기·사업보고서인데 당기순이익 줄의 당기 칸과 누적 칸이 다르면 그 총계 줄을 못 믿는다 — 총계가 확인(총포괄 − 기타포괄 · 세전 − 법인세)되지 않으면 자본변동표를 쓰지 않는다.
  ② 당기순이익 줄이 계속영업 몫뿐이고 중단영업 순이익 줄이 따로 있으면 자본변동표 계속영업 줄 + 중단영업 줄 두 줄이 각각 맞을 때만 쓴다.
"""
from __future__ import annotations

import pytest

from open_proxy_mcp.services import controlling_ni as C
from open_proxy_mcp.services import financial_metrics as FM
from open_proxy_mcp.services import period_cells as PC
from open_proxy_mcp.services import price_multiple_data as S
from scripts import market_fund_quarterly as Q

F = "ifrs-full_"
NOSTD = "-표준계정코드 미사용-"


def _s(x):
    return "" if x is None else str(x)


def r(sj, aid, nm, ordv, cur, prev=None, prev2=None, cum=None):
    """손익 줄 — `cur` = 당기, `prev`·`prev2` = 전기·전전기 칸, `cum` = 당기 누적(반기·3분기 보고서)."""
    return {"sj_div": sj, "account_id": aid, "account_nm": nm, "ord": str(ordv), "currency": "KRW",
            "thstrm_amount": _s(cur), "frmtrm_amount": _s(prev), "bfefrmtrm_amount": _s(prev2), "thstrm_add_amount": _s(cum)}


def s(aid, nm, detail, ordv, cur, prev=None, prev2=None):
    """자본변동표 당기순이익 줄 — `detail` = 열(자본 구성요소 경로), 칸은 당기·전기·전전기."""
    return {"sj_div": "SCE", "account_id": aid, "account_nm": nm, "account_detail": detail, "ord": str(ordv), "currency": "KRW",
            "thstrm_amount": _s(cur), "frmtrm_amount": _s(prev), "bfefrmtrm_amount": _s(prev2)}


def rep(reprt_code, rows):
    """보고서 코드(사업보고서 11011 · 반기 11012 · 1분기 11013 · 3분기 11014)를 줄마다 — 자본변동표 칸 짝짓기(`_sce_field`)가 본다."""
    return [{**x, "reprt_code": reprt_code} for x in rows]


def rows_086520_2021h1():
    """086520 2021 반기(접수 20210817002149) — 「계속영업 반기순이익의 귀속」 아래 지배 27.60억 · 비지배 165.57억만 있고 중단영업 몫 귀속 줄이 없다.
    반기순이익(누적) 5,779.11억 · 자본변동표 지배 몫 5,613.54억 + 비지배 165.57억 = 반기순이익(합계 열 5,668.34억은 3개월 값 — 원문 오기)."""
    return rep("11012", [
        r("CIS", F + 'IncomeTaxExpenseContinuingOperations', '법인세비용(수익)', 11, 13796058802, cum=25289549075),
        r("CIS", F + 'ProfitLossFromContinuingOperations', '계속영업 반기순이익', 12, 9560571007, cum=19316844570),
        r("CIS", F + 'ProfitLossFromDiscontinuedOperations', '중단영업 반기순이익', 13, 557272932205, cum=558594233317),
        r("CIS", F + 'ProfitLoss', '반기순이익', 14, 566833503212, cum=577911077887),
        r("CIS", F + 'ComprehensiveIncome', '반기총포괄손익', 21, 564246622269, cum=577233157624),
        r("CIS", NOSTD, '계속영업 반기순이익의 귀속', 22, 9560571007, cum=19316844570),
        r("CIS", NOSTD, '지배기업 소유주지분', 23, -862571322, cum=2759768035),
        r("CIS", NOSTD, '비지배지분', 24, 10423142329, cum=16557076535),
        r("CIS", NOSTD, '반기 총포괄손익의 귀속', 25, 564246622269, cum=577233157624),
        r("CIS", NOSTD, '지배기업 소유주지분', 26, 553823479940, cum=560676081089),
        r("CIS", NOSTD, '비지배지분', 27, 10423142329, cum=16557076535),
        s(F + 'ProfitLoss', '반기순이익', '자본 [member]|지배기업의 소유주에게 귀속되는 자본 [member]|이익잉여금 [member]', 2, 561354001352),
        s(F + 'ProfitLoss', '반기순이익', '자본 [member]|지배기업의 소유주에게 귀속되는 자본 [member]', 2, 561354001352),
        s(F + 'ProfitLoss', '반기순이익', '자본 [member]|비지배지분 [member]', 2, 16557076535),
        s(F + 'ProfitLoss', '반기순이익', '연결재무제표 [member]', 2, 566833503212),
    ])


def rows_009070_fy2022():
    """009070 2022 사업보고서(접수 20230316001262) — 「지배기업의 소유주지분」 이름 줄에 비지배 id, 「비지배지분」 이름 줄에 지배 id.
    자본변동표 지배 몫 262.89억(전기 203.57억 · 전전기 129.40억) — 다음 해 보고서 전기 칸과 같다."""
    return rep("11011", [
        r("CIS", F + 'IncomeTaxExpenseContinuingOperations', '법인세비용', 11, 2576115102, 5143865709, 3240862996),
        r("CIS", F + 'ProfitLoss', '당기순이익(손실)', 12, 28097629811, 21324908411, 13798003744),
        r("CIS", F + 'ProfitLossAttributableToNoncontrollingInterests', '지배기업의 소유주지분', 14, 26289323218, 20356915194, 12940217916),
        r("CIS", F + 'ProfitLossAttributableToOwnersOfParent', '비지배지분', 15, 1808306593, 967993217, 857785828),
        r("CIS", F + 'OtherComprehensiveIncome', '기타포괄손익', 16, 2879258994, 105136990, -353363334),
        r("CIS", F + 'ComprehensiveIncome', '총포괄손익', 24, 30976888805, 21430045401, 13444640410),
        r("CIS", F + 'ComprehensiveIncomeAttributableToOwnersOfParent', '지배기업의 소유주지분', 26, 29154098773, 20460326280, 12626019179),
        r("CIS", F + 'ComprehensiveIncomeAttributableToNoncontrollingInterests', '비지배지분', 27, 1822790032, 969719121, 818621231),
        s(NOSTD, '당기순이익', '연결재무제표 [member]', 3, 28097629811, 21324908411, 13798003744),
        s(NOSTD, '당기순이익', '자본 [member]|지배기업의 소유주에게 귀속되는 자본 [member]|이익잉여금 [member]', 3, 26289323218, 20356915194, 12940217916),
        s(NOSTD, '당기순이익', '자본 [member]|지배기업의 소유주에게 귀속되는 자본 [member]', 3, 26289323218, 20356915194, 12940217916),
        s(NOSTD, '당기순이익', '자본 [member]|비지배지분 [member]', 3, 1808306593, 967993217, 857785828),
    ])


def rows_004590_fy2023():
    """004590 2023 사업보고서(접수 20240314000691) — 지배 id 줄 −534만 · 비지배 id 줄 110.23억(값을 바꿔 적음, 전기 칸도). 자본변동표 지배 몫
    110.23억 · 전기 117.90억(2022 사업보고서 지배 줄과 같음)."""
    return rep("11011", [
        r("CIS", F + 'ComprehensiveIncome', '총포괄익손익', 11, 10160408982, 38533016359, 20108360122),
        r("CIS", F + 'ComprehensiveIncomeAttributableToNoncontrollingInterests', '비지배지분', 13, -5340566, -7245130, 2227928),
        r("CIS", F + 'ComprehensiveIncomeAttributableToOwnersOfParent', '지배기업 소유주 지분', 14, 10165749548, 38540261489, 20106132194),
        r("CIS", F + 'IncomeTaxExpenseContinuingOperations', '법인세비용', 22, 2928928208, 3108492691, 6034312636),
        r("CIS", F + 'OtherComprehensiveIncome', '기타포괄손익', 23, -856940929, 26750168597, -1772492435),
        r("CIS", F + 'ProfitLoss', '당기순이익', 30, 11017349911, 11782847762, 21880852557),
        r("CIS", F + 'ProfitLossAttributableToNoncontrollingInterests', '비지배지분에 귀속되는 당기순이익(손실)', 32, 11022690477, 11790092892, 21878624629),
        r("CIS", F + 'ProfitLossAttributableToOwnersOfParent', '지배기업의 소유주에게 귀속되는 당기순이익(손실)', 33, -5340566, -7245130, 2227928),
        s(F + 'ProfitLoss', '당기순이익', '자본 [구성요소]|비지배지분 [구성요소]', 14, -5340566, -7245130, 2227928),
        s(F + 'ProfitLoss', '당기순이익', '자본 [구성요소]|지배기업의 소유주에게 귀속되는 지분 [구성요소]|기타포괄손익누계액 [구성요소]', 14, 0),
        s(F + 'ProfitLoss', '당기순이익', '자본 [구성요소]|지배기업의 소유주에게 귀속되는 지분 [구성요소]', 14, 11022690477, 11790092892, 21878624629),
        s(F + 'ProfitLoss', '당기순이익', '자본 [구성요소]|지배기업의 소유주에게 귀속되는 지분 [구성요소]|이익잉여금 [구성요소]', 14, 11022690477, 11790092892, 21878624629),
        s(F + 'ProfitLoss', '당기순이익', '연결재무제표 [member]', 14, 11017349911, 11782847762, 21880852557),
    ])


def rows_004590_fy2022():
    """004590 2022 사업보고서(접수 20230315000218) — 지배 117.90억 · 비지배 −724.5만, 자본변동표도 같다(값 구조로 가른 그대로)."""
    return rep("11011", [
        r("CIS", F + 'IncomeTaxExpenseContinuingOperations', '법인세비용', 11, 3108492691, 6034312636, 2589938193),
        r("CIS", F + 'ProfitLoss', '당기순이익(손실)', 12, 11782847762, 21880852557, 9254842348),
        r("CIS", F + 'ProfitLossAttributableToOwnersOfParent', '지배기업의 소유주에게 귀속되는 당기순이익(손실)', 14, 11790092892, 21878624629, 9254842348),
        r("CIS", F + 'ProfitLossAttributableToNoncontrollingInterests', '비지배지분에 귀속되는 당기순이익(손실)', 15, -7245130, 2227928, 0),
        r("CIS", F + 'OtherComprehensiveIncome', '기타포괄손익', 19, 26750168597, -1772492435, 5512628682),
        r("CIS", F + 'ComprehensiveIncome', '총포괄손익', 26, 38533016359, 20108360122, 14767471030),
        r("CIS", F + 'ComprehensiveIncomeAttributableToOwnersOfParent', '총 포괄손익, 지배기업의 소유주에게 귀속되는 지분', 28, 38540261489, 20106132194, 14767471030),
        r("CIS", F + 'ComprehensiveIncomeAttributableToNoncontrollingInterests', '총 포괄손익, 비지배지분', 29, -7245130, 2227928, 0),
        s(F + 'ProfitLoss', '당기순이익(손실)', '자본 [member]|지배기업의 소유주에게 귀속되는 자본 [member]', 3, 11790092892, 21878624629, 9254842348),
        s(F + 'ProfitLoss', '당기순이익(손실)', '자본 [member]|지배기업의 소유주에게 귀속되는 자본 [member]|이익잉여금 [member]', 3, 11790092892, 21878624629, 9254842348),
        s(F + 'ProfitLoss', '당기순이익(손실)', '자본 [member]|비지배지분 [member]', 3, -7245130, 2227928, None),
        s(F + 'ProfitLoss', '당기순이익(손실)', '연결재무제표 [member]', 3, 11782847762, 21880852557, 9254842348),
    ])


def rows_196490_fy2020():
    """196490 2020 사업보고서(접수 20210406001324) — 지배 id 줄 1.18억(= 비지배 몫) · 「비지배지분」 줄 −1.90억(= 기타포괄), 당기순손익 −294.87억.
    자본변동표는 손실을 양수로 적었다(지배 +296.05억 · 비지배 +1.18억 · 합계 +294.87억) — 지배 −296.05억(2021 보고서 전기 칸 · 2022 전전기 칸과 같음)."""
    return rep("11011", [
        r("CIS", F + 'IncomeTaxExpenseContinuingOperations', '법인세비용(수익)', 11, -3200043858, 750073102, 90367933),
        r("CIS", F + 'ProfitLoss', '연결당기순손익', 12, -29486583282, -20783129440, -17223004551),
        r("CIS", F + 'ProfitLossAttributableToOwnersOfParent', '지배기업 소유주지분', 14, 118061897, None, -17223004551),
        r("CIS", F + 'ProfitLossAttributableToNoncontrollingInterests', '비지배지분', 15, -190153326, -205248405, 0),
        r("CIS", F + 'OtherComprehensiveIncome', '연결 당기 세후 기타포괄손익', 16, -190153326, -205248405, 140180260),
        r("CIS", F + 'ComprehensiveIncome', '연결총포괄손익', 22, -29676736608, -20988377845, -17082824291),
        r("CIS", F + 'ComprehensiveIncomeAttributableToOwnersOfParent', '지배기업 소유주지분', 24, -29778784968, -20988377845, -17082824291),
        r("CIS", F + 'ComprehensiveIncomeAttributableToNoncontrollingInterests', '비지배지분', 25, 102048360, None, 0),
        s(NOSTD, '당기순이익', '연결재무제표 [member]', 6, 29486583282, -20783129440, -17223004551),
        s(NOSTD, '당기순이익', '자본 [member]|비지배지분 [member]', 6, 118061897, None, 0),
        s(NOSTD, '당기순이익', '자본 [member]|지배기업의 소유주에게 귀속되는 자본 [member]', 6, 29604645179, -20783129440, -17223004551),
        s(NOSTD, '당기순이익', '자본 [member]|지배기업의 소유주에게 귀속되는 자본 [member]|기타포괄손익누계액 [member]', 6, None, None, 0),
        s(NOSTD, '당기순이익', '자본 [member]|지배기업의 소유주에게 귀속되는 자본 [member]|이익잉여금 [member]', 6, 29604645179, -20783129440, -17223004551),
        s(NOSTD, '당기순이익', '자본 [member]|지배기업의 소유주에게 귀속되는 자본 [member]|자본금 [member]', 6, None, None, 0),
        s(NOSTD, '당기순이익', '자본 [member]|지배기업의 소유주에게 귀속되는 자본 [member]|자본잉여금 [member]', 6, None, None, 0),
        s(NOSTD, '당기순이익', '자본 [member]|지배기업의 소유주에게 귀속되는 자본 [member]|자본조정', 6, None, None, 0),
    ])


def rows_052770_2021h1():
    """052770 2021 반기(접수 20210817002000) — 「지배회사지분순이익」 −50.34억은 총포괄 지배 몫(버림), 비지배 −23만 원은 총포괄 짝이라 안 뺐다.
    자본변동표 지배 몫 −47.34억(−4,734,195,889) + 비지배 열(지배 멤버 아래 「비지배지분 이익잉여금」) −230,594 = 반기순이익."""
    return rep("11012", [
        r("CIS", F + 'IncomeTaxExpenseContinuingOperations', '법인세비용', 11, 0, cum=0),
        r("CIS", F + 'ProfitLossFromContinuingOperations', '계속영업이익(손실)', 12, -3892905747, cum=-4734426483),
        r("CIS", F + 'ProfitLoss', '당기순이익(손실)', 13, -3892905747, cum=-4734426483),
        r("CIS", F + 'OtherComprehensiveIncome', '기타포괄손익', 14, -299999700, cum=-299999700),
        r("CIS", F + 'ComprehensiveIncome', '총포괄손익', 17, -4192905447, cum=-5034426183),
        r("CIS", NOSTD, '지배회사지분순이익(손실)', 18, -4193832135, cum=-5034195589),
        r("CIS", NOSTD, '비지배지분순이익(손실)', 19, 926688, cum=-230594),
        s(F + 'ProfitLoss', '당기순이익(손실)', '자본 [member]|지배기업의 소유주에게 귀속되는 자본 [member]|비지배지분 이익잉여금', 2, -230594),
        s(F + 'ProfitLoss', '당기순이익(손실)', '자본 [member]|지배기업의 소유주에게 귀속되는 자본 [member]|이익잉여금 [member]', 2, -4734195889),
        s(F + 'ProfitLoss', '당기순이익(손실)', '자본 [member]|지배기업의 소유주에게 귀속되는 자본 [member]', 2, -4734195889),
        s(F + 'ProfitLoss', '당기순이익(손실)', '연결재무제표 [member]', 2, -3892905747),
    ])


def rows_347860_2023q1():
    """347860 2023 1분기(접수 20240809000499) — 지배 id 줄 −88.44억 = 총포괄 총계(버림), 비지배 −0.63억. 자본변동표 지배 몫 −87.85억."""
    return rep("11013", [
        r("CIS", F + 'IncomeTaxExpenseContinuingOperations', '법인세비용', 9, None),
        r("CIS", F + 'ProfitLoss', '당기순이익(손실)', 10, -8847717736, cum=-8847717736),
        r("CIS", F + 'OtherComprehensiveIncome', '세후기타포괄이익(손실)', 11, 3644201, cum=3644201),
        r("CIS", F + 'ComprehensiveIncome', '당기총포괄이익(손실)', 16, -8844073535, cum=-8844073535),
        r("CIS", F + 'ProfitLossAttributableToOwnersOfParent', '지배기업소유주지분', 18, -8844073535, cum=-8844073535),
        r("CIS", F + 'ProfitLossAttributableToNoncontrollingInterests', '비지배지분', 19, -62710793, cum=-62710793),
        r("CIS", F + 'ComprehensiveIncomeAttributableToOwnersOfParent', '지배기업소유주지분', 21, -8786807544, cum=-8786807544),
        r("CIS", F + 'ComprehensiveIncomeAttributableToNoncontrollingInterests', '비지배지분', 22, -62710793, cum=-62710793),
        s(F + 'ProfitLoss', '당기순이익(손실)', '자본 [member]|지배기업의 소유주에게 귀속되는 자본 [member]', 3, -8785006943),
        s(F + 'ProfitLoss', '당기순이익(손실)', '자본 [member]|지배기업의 소유주에게 귀속되는 자본 [member]|결손금', 3, -8785006943),
        s(F + 'ProfitLoss', '당기순이익(손실)', '자본 [member]|비지배지분 [member]', 3, -62710793),
        s(F + 'ProfitLoss', '당기순이익(손실)', '연결재무제표 [member]', 3, -8847717736),
    ])


def rows_145270_fy2025():
    """145270 2025 사업보고서(접수 20260312001183) — 손익계산서 비지배 줄 2.97억이 틀렸다: 지배 62.16억 + 2.97억 ≠ 당기순이익 65.07억.
    자본변동표 비지배 2.91억(= 재무상태표 비지배지분 12.51억 → 15.42억 증가) · 지배 62.16억 = 지배 id 줄."""
    return rep("11011", [
        r("CIS", F + 'ComprehensiveIncome', '총포괄손익', 10, 6546458705, 5574962075, 12458593523),
        r("CIS", F + 'ComprehensiveIncomeAttributableToNoncontrollingInterests', '포괄손익, 비지배지분', 12, 297334446, 95534007, 135117496),
        r("CIS", F + 'ComprehensiveIncomeAttributableToOwnersOfParent', '포괄손익, 지배기업의 소유주에게 귀속되는 지분', 13, 66255124259, 5479428068, 12323476027),
        r("CIS", F + 'IncomeTaxExpenseContinuingOperations', '법인세비용(수익)', 19, 2534167638, 434110854, 3074788082),
        r("CIS", F + 'OtherComprehensiveIncome', '기타포괄손익', 26, 39109943, 309486516, 692435543),
        r("CIS", F + 'ProfitLoss', '당기순이익(손실)', 30, 6507348762, 5265475559, 11766157980),
        r("CIS", F + 'ProfitLossAttributableToNoncontrollingInterests', '비지배지분에 귀속되는 당기순이익(손실)', 32, 297334446, 95534007, 135117496),
        r("CIS", F + 'ProfitLossAttributableToOwnersOfParent', '지배기업의 소유주에게 귀속되는 당기순이익(손실)', 33, 6216014316, 5169941552, 11631040484),
        s(F + 'ProfitLoss', '당기순이익(손실)', '자본 [구성요소]|지배기업의 소유주에게 귀속되는 지분 [구성요소]', 11, 6216014316, 5169941552, 11631040484),
        s(F + 'ProfitLoss', '당기순이익(손실)', '자본 [구성요소]|비지배지분 [구성요소]', 11, 291334446, 95534007, 135117496),
        s(F + 'ProfitLoss', '당기순이익(손실)', '연결재무제표 [member]', 11, 6507348762, 5265475559, 11766157980),
        s(F + 'ProfitLoss', '당기순이익(손실)', '자본 [구성요소]|지배기업의 소유주에게 귀속되는 지분 [구성요소]|이익잉여금 [구성요소]', 11, 6216014316, 5169941552, 11631040484),
    ])


def rows_289220_fy2020():
    """289220 2020 사업보고서(접수 20210331003230) — 당기순손실 −14.22억(전기 −31.42억)을 지배 id 줄·자본변동표가 양수로 적었다, 비지배 없음.
    2021 사업보고서 전기 칸 자본변동표 지배 몫 −14.22억."""
    return rep("11011", [
        r("CIS", F + 'IncomeTaxExpenseContinuingOperations', '법인세비용', 8, 17024743, 600006137, None),
        r("CIS", NOSTD, '계속영업손실', 9, -1476902131, -2379731013, None),
        r("CIS", F + 'ProfitLossFromDiscontinuedOperations', '중단영업이익(손실)', 10, 54734488, -762452395, None),
        r("CIS", F + 'ProfitLoss', '당기순이익(손실)', 11, -1422167643, -3142183408, None),
        r("CIS", F + 'OtherComprehensiveIncome', '기타포괄손익', 12, -25744115, -82842510, None),
        r("CIS", F + 'ComprehensiveIncome', '총포괄손실', 19, -1447911758, -3225025918, None),
        r("CIS", F + 'ProfitLossAttributableToOwnersOfParent', '지배기업주주지분', 21, 1422167643, 3142183408, None),
        r("CIS", F + 'ComprehensiveIncomeAttributableToOwnersOfParent', '지배기업주주지분', 24, 1447911758, 3225025918, None),
        s(NOSTD, '당기순손실', '연결재무제표 [member]', 3, 1422167643, 3142183408, None),
        s(NOSTD, '당기순손실', '총계|이익잉여금', 3, 1422167643, 3142183408, None),
        s(NOSTD, '당기순손실', '총계|지배기업 소유주지분', 3, 1422167643, 3142183408, None),
    ])


def rows_069510_fy2019():
    """069510 2019 사업보고서(접수 20200330001784) — 지배 id 줄에 확정급여 재측정(기타포괄) 값 −4.54억, 비지배 없음. 자본변동표는 지배 몫 합계 열 없이
    지배 멤버 아래 「이익잉여금」 열만 — 171.99억(= 당기순이익)."""
    return rep("11011", [
        r("CIS", F + 'ProfitLoss', '당기순이익(손실)', 0, 17199546115, 19953774158, 9906762825),
        r("CIS", F + 'OtherComprehensiveIncome', '기타포괄손익', 1, 2030434949, -496578272, -6925428693),
        r("CIS", F + 'ComprehensiveIncome', '총포괄손익', 6, 19229981064, 19457195886, 2981334132),
        r("CIS", F + 'ComprehensiveIncomeAttributableToOwnersOfParent', '총 포괄손익, 지배기업의 소유주에게 귀속되는 지분', 8, 19229981064, 19457195886, 2981334132),
        r("IS", F + 'IncomeTaxExpenseContinuingOperations', '법인세비용', 10, 3112906483, 1819941115, 5690069230),
        r("IS", F + 'ProfitLoss', '당기순이익(손실)', 11, 17199546115, 19953774158, 9906762825),
        r("IS", F + 'ProfitLossAttributableToOwnersOfParent', '지배기업의 소유주에게 귀속되는 당기순이익(손실)', 13, -454146973, -1623681154, 46269608),
        s(F + 'ProfitLoss', '당기순이익(손실)', '자본 [member]|지배기업의 소유주에게 귀속되는 자본 [member]|이익잉여금 [member]', 3, 17199546115, 19953774158, 9906762825),
        s(F + 'ProfitLoss', '당기순이익(손실)', '연결재무제표 [member]', 3, 17199546115, 19953774158, 9906762825),
    ])


def rows_138360_fy2020_ofs():
    """138360 2020 사업보고서 별도(접수 20210323000822) — 전기 칸: 「지배기업의 소유주」 −1.17억은 총포괄 지배 몫(버림). 자본변동표에 지배 멤버가 없고
    합계 −0.88억 · 비지배 −0.15억 · 이익잉여금 −0.73억 → 지배 몫 = 합계 − 비지배 −0.73억."""
    return rep("11011", [
        r("CIS", F + 'IncomeTaxExpenseContinuingOperations', '법인세비용', 11, None, None, 1343860442),
        r("CIS", F + 'ProfitLossFromContinuingOperations', '계속영업이익(손실)', 12, -2546640821, -467881664, -34347789550),
        r("CIS", F + 'ProfitLossFromDiscontinuedOperations', '중단영업이익(손실)', 13, -5944887508, 379667106, 638958919),
        r("CIS", F + 'ProfitLoss', '당기순이익(손실)', 14, -8491528329, -88214558, -33708830631),
        r("CIS", F + 'OtherComprehensiveIncome', '기타포괄손익', 15, -4303351, -43827818, 46026424),
        r("CIS", F + 'ComprehensiveIncome', '총포괄손익', 16, -8495831680, -132042376, -33662804207),
        r("CIS", NOSTD, '계속영업이익(손실)', 19, -2525833351, -452877074, -34347789550),
        r("CIS", NOSTD, '중단영업이익(손실)', 20, -5944887508, 379667106, 638958919),
        r("CIS", NOSTD, '계속영업이익(손실)', 22, -20807470, -15004590, None),
        r("CIS", NOSTD, '지배기업의 소유주', 25, -8475024210, -117037786, -33662804207),
        r("CIS", NOSTD, '비지배지분', 26, -20807470, -15004590, None),
        s(F + 'ProfitLoss', '당기순이익(손실)', '자본 [member]|비지배지분', 2, -20807470, -15004590, None),
        s(F + 'ProfitLoss', '당기순이익(손실)', '자본 [member]|이익잉여금 [member]', 2, -8470720859, -73209968, -33708830631),
        s(F + 'ProfitLoss', '당기순이익(손실)', '별도재무제표 [member]', 2, -8491528329, -88214558, -33708830631),
    ])


CASES = [
    # (자료, 칸, 새 값, 새 케이스, 종전 값 — 고치기 전 규칙)
    (rows_086520_2021h1, None, 561354001352.0, "CIS_SCE", 2759768035.0),
    (rows_009070_fy2022, "thstrm_amount", 26289323218.0, "CIS_SWAP_SCE", 1808306593.0),
    (rows_009070_fy2022, "frmtrm_amount", 20356915194.0, "CIS_SWAP_SCE", 967993217.0),
    (rows_009070_fy2022, "bfefrmtrm_amount", 12940217916.0, "CIS_SWAP_SCE", 857785828.0),
    (rows_004590_fy2023, "thstrm_amount", 11022690477.0, "CIS_SWAP_SCE", -5340566.0),
    (rows_004590_fy2023, "frmtrm_amount", 11790092892.0, "CIS_SWAP_SCE", -7245130.0),
    (rows_196490_fy2020, "thstrm_amount", -29604645179.0, "CIS_SCE", -29296429956.0),
    (rows_052770_2021h1, None, -4734195889.0, "TOTAL_CIREJ_SCE", -4734426483.0),
    (rows_347860_2023q1, None, -8785006943.0, "TOTAL_CIREJ_SCE", -8847717736.0),
    (rows_145270_fy2025, "thstrm_amount", 6216014316.0, "TOTAL_MINUS_MINORITY_IDX_SCE", 6210014316.0),
    (rows_289220_fy2020, "thstrm_amount", -1422167643.0, "CIS_SCE", 1422167643.0),
    (rows_289220_fy2020, "frmtrm_amount", -3142183408.0, "CIS_SCE", 3142183408.0),
    (rows_069510_fy2019, "thstrm_amount", 17199546115.0, "IS_SCE", -454146973.0),
    (rows_069510_fy2019, "frmtrm_amount", 19953774158.0, "IS_SCE", -1623681154.0),
    (rows_069510_fy2019, "bfefrmtrm_amount", 9906762825.0, "IS_SCE", 46269608.0),
    (rows_138360_fy2020_ofs, "frmtrm_amount", -73209968.0, "TOTAL_CIREJ_SCE", -88214558.0),
]
IDS = ["086520-2021반기", "009070-2022당기", "009070-2022전기", "009070-2022전전기", "004590-2023당기", "004590-2023전기", "196490-2020",
       "052770-2021반기", "347860-2023-1분기", "145270-2025", "289220-2020당기", "289220-2020전기", "069510-2019당기", "069510-2019전기",
       "069510-2019전전기", "138360-2020별도전기"]


@pytest.mark.parametrize("rows, field, want, case, old", CASES, ids=IDS)
def test_sce_decides_when_value_structure_cannot(rows, field, want, case, old):
    """종전 값(`old`)은 고치기 전 규칙이 낸 값 — 가를 수 없는 줄·바꿔 적은 지배 줄·총계 규칙. 이제 자본변동표 지배 몫과 같은 값."""
    got = C.controlling_ni(rows(), field)
    assert got == (want, case)
    assert abs(got[0] - old) > 1


@pytest.mark.parametrize("rows, field, want", [(rows_004590_fy2022, "thstrm_amount", (11790092892.0, "CIS_ID")),
                                               (rows_086520_2021h1, "thstrm_amount", (-862571322.0, "CIS_NM"))],
                         ids=["값 구조로 가른 길", "반기 3개월 칸 — 자본변동표 칸 없음"])
def test_unchanged_where_sce_does_not_apply(rows, field, want):
    """값 구조로 이미 가른 줄(지배 + 비지배 = 당기순이익, 자본변동표도 같음)과 자본변동표에 맞는 칸이 없는 반기 3개월 칸은 종전 그대로."""
    assert C.controlling_ni(rows(), field) == want


def test_structure_decided_line_is_not_overridden_by_a_different_sce_split():
    """값 구조로 가른 줄은 자본변동표가 다른 나눔을 적어도 그대로다(바꿔 적은 꼴 — 짝 비지배 줄 = 자본변동표 지배 몫 — 일 때만 고친다).
    합성: 004590 2022 자본변동표 지배·비지배를 117.80억 · 1.03억(합은 당기순이익 그대로)으로 바꿈."""
    rows = rows_004590_fy2022()
    for x in rows:
        if x["sj_div"] == "SCE" and "비지배" not in x["account_detail"] and "연결" not in x["account_detail"]:
            x["thstrm_amount"] = "11780000000"
        elif x["sj_div"] == "SCE" and "비지배" in x["account_detail"]:
            x["thstrm_amount"] = "2847762"
    assert C._sce_ctrl_ni(rows) == 11780000000.0
    assert C.controlling_ni(rows, "thstrm_amount") == (11790092892.0, "CIS_ID")


def _drop_sce(rows, pred):
    return [x for x in rows if not (x["sj_div"] == "SCE" and pred(x))]


def test_sce_must_add_up_to_the_income_statement_total():
    """대조 문: 자본변동표 지배 + 비지배 ≠ 손익계산서 당기순이익이면 쓰지 않는다(종전 규칙) — 합성: 086520 자본변동표 비지배를 100만 원 바꿈."""
    rows = rows_086520_2021h1()
    for x in rows:
        if x["sj_div"] == "SCE" and "비지배" in x["account_detail"]:
            x["thstrm_amount"] = str(16557076535 + 1_000_000)
    assert C._sce_ctrl_ni(rows) is None
    assert C.controlling_ni(rows) == (2759768035.0, "CIS_NM")


def test_no_sce_rows_keeps_the_old_rule():
    rows = _drop_sce(rows_086520_2021h1(), lambda x: True)
    assert C._sce_ctrl_ni(rows) is None
    assert C.controlling_ni(rows) == (2759768035.0, "CIS_NM")


def test_ambiguous_sce_owner_column_is_not_used():
    """같은 열에 값이 둘(정정 전·후 줄 등)이면 애매 — 쓰지 않는다."""
    rows = rows_086520_2021h1()
    rows.append({**next(x for x in rows if x["sj_div"] == "SCE" and x["account_detail"].endswith("자본 [member]")), "thstrm_amount": "561354001000"})
    assert C._sce_ctrl_ni(rows) is None
    assert C.controlling_ni(rows) == (2759768035.0, "CIS_NM")


def test_sce_without_minority_column_while_income_statement_has_one_is_not_used():
    """손익계산서엔 0 이 아닌 비지배가 있는데 자본변동표엔 비지배 열이 없다 — 두 표가 어긋나 쓰지 않는다. 합성: 145270 에서 비지배 열을 빼고 지배 몫 열을
    당기순이익 총계로(「비지배 0」 자본변동표) — 그대로 쓰면 비지배 포함 총계 65.07억이 지배 몫이 된다 → 종전 값."""
    rows = _drop_sce(rows_145270_fy2025(), lambda x: "비지배" in x["account_detail"])
    for x in rows:
        if x["sj_div"] == "SCE" and "지배" in x["account_detail"]:
            x["thstrm_amount"] = "6507348762"
    assert C._sce_ctrl_ni(rows, "thstrm_amount") is None
    assert C.controlling_ni(rows, "thstrm_amount") == (6210014316.0, "TOTAL_MINUS_MINORITY_IDX")


def test_sce_field_pairs_the_same_period():
    """손익 칸 → 자본변동표 칸: 사업보고서·1분기는 당기·전기(·전전기) 그대로, 반기·3분기의 3개월 칸은 맞는 칸이 없다."""
    assert C._sce_field("thstrm_amount", "11011") == "thstrm_amount"
    assert C._sce_field("bfefrmtrm_amount", "11011") == "bfefrmtrm_amount"
    assert C._sce_field("frmtrm_amount", "11013") == "frmtrm_amount"
    assert C._sce_field("thstrm_amount", "11012") is None
    assert C._sce_field("frmtrm_amount", "11014") is None
    assert C._sce_field("thstrm_add_amount", "11012") == "thstrm_amount"


def test_every_caller_gets_the_same_value():
    """공용 규칙을 부르는 경로(서버 실시간 firm · 결산기 칸 · 재무 지표 · 분기 수집)가 같은 값을 받는다 — 009070 2022 · 086520 2021 반기."""
    fy = rows_009070_fy2022()
    assert S._ctrl_ni(fy) == 26289323218
    assert PC.ctrl_ni(fy, "frmtrm_amount") == (20356915194.0, "CIS_SWAP_SCE")
    assert FM._build_account_map_all(fy)["controlling_interest_income"] == 26289323218
    h1 = rows_086520_2021h1()
    assert S._ctrl_ni_cum(h1) == 561354001352
    assert Q.extract_controlling_ni_cum(h1) == (561354001352.0, "CIS_SCE")


def test_prior_rows_moved_by_the_quarterly_collector_do_not_borrow_this_period_sce():
    """분기 수집의 전년 동기 칸(`_as_prior_rows`)은 자본변동표도 전년 동기 누적 칸(`frmtrm_q_amount`)으로 옮긴다 — 그 칸이 빈 자본변동표 줄은 비우고 당기 값을 빌려 쓰지 않는다.
    합성: 086520 반기 손익 줄에 전기 누적 칸을 넣음(반기순이익 265.03억 · 지배 이름 줄 127.11억 · 비지배 96.46억 — 원문 전년 반기 일부 값). 자본변동표엔 전기 칸이 없다.
    (전기 칸이 있는 보고서는 `tests/test_prior_rows_sce.py`)"""
    rows = rows_086520_2021h1()
    prev = {"반기순이익": 26502950204, "지배기업 소유주지분": 12710531544, "비지배지분": 9646438982}
    for x in rows:
        if x["sj_div"] in ("IS", "CIS"):
            x["frmtrm_add_amount"] = str(prev.get(x["account_nm"], ""))
    pr = Q._as_prior_rows(rows, 2)
    assert C._sce_ctrl_ni(pr) is None
    assert Q.extract_controlling_ni_cum(pr)[1] != "CIS_SCE"


# ───────────────────────── 261021 ni-sce-fp — 거짓 양성 두 꼴 ─────────────────────────
def rows_081580_2022q1():
    """081580 2022 1분기(접수 20220516001078) — 「당기순이익」 줄 당기 칸 24.76억(= 세전 25.39억 − 법인세 0.63억), 누적 칸 30.90억(오기).
    자본변동표 지배 열 33.26억(오기) + 비지배 −2.36억 = 30.90억이 누적 칸 오기와 우연히 맞는다. 참값 27.12억 = 지배 id 줄 = 반기 보고서 누적 53.56억 − 반기 3개월 26.43억."""
    return rep("11013", [
        r('CIS', F + 'ProfitLossBeforeTax', '법인세비용차감전순이익(손실)', 9, 2539327436, cum=2539327436),
        r('CIS', F + 'IncomeTaxExpenseContinuingOperations', '법인세비용', 10, 63249195, cum=63249195),
        r('CIS', F + 'ProfitLoss', '당기순이익(손실)', 11, 2476078241, cum=3090338741),
        r('CIS', F + 'ProfitLossAttributableToOwnersOfParent', '지배기업의 소유주에게 귀속되는 당기순이익(손실)', 13, 2712238401, cum=2712238401),
        r('CIS', F + 'ProfitLossAttributableToNoncontrollingInterests', '비지배지분에 귀속되는 당기순이익(손실)', 14, -236160160, cum=-236160160),
        r('CIS', F + 'OtherComprehensiveIncome', '기타포괄손익', 15, 777579991, cum=777579991),
        r('CIS', F + 'ComprehensiveIncomeAttributableToOwnersOfParent', '총 포괄손익, 지배기업의 소유주에게 귀속되는 지분', 23, 3517352281, cum=3517352281),
        r('CIS', F + 'ComprehensiveIncomeAttributableToNoncontrollingInterests', '총 포괄손익, 비지배지분', 24, -263694049, cum=-263694049),
        s(F + 'ProfitLoss', '당기순이익(손실)', '자본 [member]|지배기업의 소유주에게 귀속되는 자본 [member]|이익잉여금 [member]', 2, 3326498901),
        s(F + 'ProfitLoss', '당기순이익(손실)', '자본 [member]|지배기업의 소유주에게 귀속되는 자본 [member]', 2, 3326498901),
        s(F + 'ProfitLoss', '당기순이익(손실)', '자본 [member]|비지배지분 [member]', 2, -236160160),
        s(F + 'ProfitLoss', '당기순이익(손실)', '연결재무제표 [member]', 2, 2476078241),
    ])


def rows_089470_2023h1():
    """089470 2023 반기(접수 20230814002021) — 「ProfitLoss」 줄이 계속영업 반기순이익 166.84억뿐(중단영업 30.60억은 표준 코드 없는 따로 줄).
    자본변동표 계속영업 줄 지배 183.45억 + 비지배 −16.61억 = 166.84억이 맞는다. 참값 214.05억 = 계속 183.45억 + 중단 30.60억 = 지배 id 줄 =
    2024 반기 보고서 전년 동기 칸."""
    return rep("11012", [
        r('IS', F + 'ProfitLossBeforeTax', '법인세차감전반기순이익(손실)', 19, 13731131402, cum=24760985926),
        r('IS', F + 'IncomeTaxExpenseContinuingOperations', '법인세비용', 20, 3475416459, cum=8076713056),
        r('IS', F + 'ProfitLoss', '계속영업반기순이익', 21, 10255714943, cum=16684272870),
        r('IS', NOSTD, '중단영업반기순이익', 22, 3037467033, cum=3059969816),
        r('IS', F + 'ProfitLossAttributableToOwnersOfParent', '지배기업의 소유주에게 귀속되는 분기순이익(손실)', 24, 14103298901, cum=21404933485),
        r('IS', F + 'ProfitLossAttributableToNoncontrollingInterests', '비지배지분에 귀속되는 분기순이익(손실)', 25, -810116925, cum=-1660690799),
        r('CIS', F + 'ProfitLoss', '계속영업반기순이익', 0, 10255714943, cum=16684272870),
        r('CIS', NOSTD, '중단영업반기순이익', 1, 3037467033, cum=3059969816),
        r('CIS', F + 'OtherComprehensiveIncome', '기타포괄손익', 2, -946398660, cum=2112150826),
        r('CIS', F + 'ComprehensiveIncome', '반기총포괄손익', 9, 12346783316, cum=21856393512),
        s(F + 'ProfitLoss', '계속영업반기순이익', '자본 [member]|지배기업의 소유주에게 귀속되는 자본 [member]', 3, 18344963669),
        s(F + 'ProfitLoss', '계속영업반기순이익', '자본 [member]|지배기업의 소유주에게 귀속되는 자본 [member]|이익잉여금 [member]', 3, 18344963669),
        s(F + 'ProfitLoss', '계속영업반기순이익', '자본 [member]|비지배지분 [member]', 3, -1660690799),
        s(F + 'ProfitLoss', '계속영업반기순이익', '연결재무제표 [member]', 3, 10255714943),
        s(NOSTD, '중단영업반기순이익', '자본 [member]|지배기업의 소유주에게 귀속되는 자본 [member]|이익잉여금 [member]', 4, 3059969816),
        s(NOSTD, '중단영업반기순이익', '자본 [member]|지배기업의 소유주에게 귀속되는 자본 [member]', 4, 3059969816),
        s(NOSTD, '중단영업반기순이익', '연결재무제표 [member]', 4, 3059969816),
    ])


def rows_annual_cont_disc():
    """합성 사업보고서(11011) — 계속영업 당기순이익이 총계 줄(`ProfitLoss`), 중단영업은 따로 줄. 당기: 계속 100(지배 95 + 비지배 5) · 중단 30(지배 30) → 지배 125.
    전기: 계속 80(지배 76 + 비지배 4) · 중단 20(지배 20) → 지배 96. 자본변동표 전기 칸도 같은 짝."""
    return rep("11011", [
        r('CIS', F + 'ProfitLoss', '계속영업당기순이익', 1, 100, 80),
        r('CIS', NOSTD, '중단영업당기순이익', 2, 30, 20),
        r('CIS', F + 'ProfitLossAttributableToNoncontrollingInterests', '비지배지분', 4, 5, 4),
        s(F + 'ProfitLoss', '계속영업당기순이익', '자본 [member]|지배기업의 소유주에게 귀속되는 자본 [member]', 3, 95, 76),
        s(F + 'ProfitLoss', '계속영업당기순이익', '자본 [member]|비지배지분 [member]', 3, 5, 4),
        s(F + 'ProfitLoss', '계속영업당기순이익', '연결재무제표 [member]', 3, 100, 80),
        s(NOSTD, '중단영업당기순이익', '자본 [member]|지배기업의 소유주에게 귀속되는 자본 [member]', 4, 30, 20),
        s(NOSTD, '중단영업당기순이익', '연결재무제표 [member]', 4, 30, 20),
    ])


def test_q1_total_line_with_different_columns_does_not_trust_sce():
    """① 081580 2022 1분기 — 당기 칸 24.76억 ≠ 누적 칸 30.90억, 세전 − 법인세(24.76억)와 맞지 않는다 → 자본변동표를 쓰지 않는다.
    고치기 전엔 자본변동표 지배 열 오기 33.26억(`CIS_SCE`)을 지배 순이익으로 썼다."""
    rows = rows_081580_2022q1()
    assert C.controlling_ni(rows) == (2712238401.0, "CIS_ID")      # 운영 값 · 반기 보고서 누적 − 3개월
    assert C._tot_cols_disagree(rows)
    assert C._sce_ctrl_ni(rows) is None


@pytest.mark.parametrize("patch, why", [
    ({"ProfitLossBeforeTax": 3153587936, "IncomeTaxExpenseContinuingOperations": 63249195}, "세전 − 법인세 = 누적 칸 총계"),
    ({"ComprehensiveIncome": 3867918732}, "총포괄 − 기타포괄 = 누적 칸 총계"),
], ids=["세전−법인세", "총포괄−기타포괄"])
def test_q1_columns_differ_but_total_is_confirmed_keeps_sce(patch, why):
    """① 의 반대 — 당기 칸과 누적 칸이 달라도 총계가 다른 줄로 확인되면(누적 칸이 맞는 쪽) 자본변동표를 그대로 쓴다. 합성: 081580 에 확인 줄을 넣거나 고침."""
    rows = rows_081580_2022q1()
    for x in rows:
        if x["account_id"].replace(F, "") in patch:
            x["thstrm_add_amount"] = x["thstrm_amount"] = str(patch[x["account_id"].replace(F, "")])
    if "ComprehensiveIncome" in patch:
        rows.append(r("CIS", F + "ComprehensiveIncome", "당기총포괄이익(손실)", 20, patch["ComprehensiveIncome"], cum=patch["ComprehensiveIncome"]))
    assert C._tot_cols_disagree(rows)
    assert C._sce_ctrl_ni(rows) == 3326498901.0, why


def test_columns_that_legitimately_differ_are_not_flagged():
    """반기·3분기 보고서의 당기 칸(3개월)과 누적 칸은 원래 다르다 — 1분기·사업보고서만 본다(위 ① 규칙은 그 둘에서만)."""
    assert not C._tot_cols_disagree(rows_086520_2021h1())
    assert not C._tot_cols_disagree(rows_089470_2023h1())
    assert not C._tot_cols_disagree(rows_145270_fy2025())
    q1 = rows_081580_2022q1()
    assert C._tot_cols_disagree(q1)
    assert not C._tot_cols_disagree([{**x, "reprt_code": "11012"} for x in q1])


def test_continuing_only_total_needs_continuing_plus_discontinued_sce():
    """② 089470 2023 반기 — 총계 줄이 계속영업 166.84억뿐, 중단영업 30.60억 줄이 따로 있다. 자본변동표 계속영업 줄만 맞추면 183.45억이었다(중단 몫 빠짐)
    → 계속 + 중단 = 214.05억(지배 id 줄과 같음)."""
    rows = rows_089470_2023h1()
    assert C.controlling_ni(rows) == (21404933485.0, "IS_ID")      # 고치기 전 (18344963669.0, "IS_SCE")
    assert C._disc_ni(rows) == 3059969816.0
    assert C._sce_ctrl_ni(rows) == 18344963669.0 + 3059969816.0


def test_discontinued_sce_line_missing_or_not_adding_up_is_not_used():
    """② — 중단영업 줄이 자본변동표에 없거나(계속 줄만 맞는 꼴) 중단영업 순이익과 안 맞으면 쓰지 않는다 → 종전(자본변동표 없는 규칙) 값."""
    no_disc = [x for x in rows_089470_2023h1() if not (x["sj_div"] == "SCE" and "중단" in x["account_nm"])]
    assert C._sce_ctrl_ni(no_disc) is None
    assert C.controlling_ni(no_disc) == (21404933485.0, "IS_ID")
    off = rows_089470_2023h1()
    for x in off:
        if x["sj_div"] == "SCE" and "중단" in x["account_nm"] and "연결" not in x["account_detail"]:
            x["thstrm_amount"] = "3000000000"
    assert C._sce_ctrl_ni(off) is None
    assert C.controlling_ni(off) == (21404933485.0, "IS_ID")


def test_disc_line_only_counts_when_total_line_is_continuing_only():
    """총계 줄 이름이 계속영업이 아니면(086520 「반기순이익」 = 계속 + 중단 합계, 중단영업 줄이 따로 있어도) 중단영업을 더하지 않는다."""
    assert C._disc_ni(rows_086520_2021h1()) is None
    assert C._disc_ni(rows_289220_fy2020()) is None
    assert C._disc_ni(rows_089470_2023h1()) is not None
    zero = rows_089470_2023h1()
    for x in zero:
        if x["sj_div"] in ("IS", "CIS") and "중단" in x["account_nm"]:
            x["thstrm_amount"] = x["thstrm_add_amount"] = "0"
    assert C._disc_ni(zero) is None             # 중단영업 순이익 0 이면 더할 것이 없다


@pytest.mark.parametrize("field, want", [("thstrm_amount", 125.0), ("frmtrm_amount", 96.0)], ids=["당기", "전기"])
def test_discontinued_sce_line_follows_the_same_period_column(field, want):
    """② — 사업보고서의 전기 칸은 계속·중단 두 줄 다 전기 칸을 본다(`_field_rows` 가 중단영업 자본변동표 줄도 같은 기간 칸으로 옮긴다)."""
    rows = rows_annual_cont_disc()
    assert C._sce_ctrl_ni(C._field_rows(rows, field), "thstrm_amount") == want


# ───── 261021 ni-sce-fp 검토 — 원문 캐시엔 아직 해당 보고서가 없는 갈래(부분 변이에서 시험이 비어 있던 곳) ─────
def test_disc_id_and_name_are_discontinued_net_income_only():
    """중단영업 순이익 줄만 — 주당이익(089470 원문 30번 줄 「기본주당중단영업이익(손실)」)·중단영업 총포괄·현금흐름 조정 줄·귀속 줄은 아니다."""
    assert C._disc_id(F + "ProfitLossFromDiscontinuedOperations")
    assert C._disc_id("ifrs_ProfitLossFromDiscontinuedOperations")
    for aid in (F + "BasicEarningsLossPerShareFromDiscontinuedOperations", F + "ComprehensiveIncomeFromDiscontinuedOperations",
                "dart_AdjustmentsForProfitLossFromDiscontinuedOperations", F + "ProfitLossFromContinuingOperations"):
        assert not C._disc_id(aid), aid
    for nm in ("중단영업반기순이익", "Ⅴ. 중단영업이익(손실)", "중단사업 당기순손익"):
        assert C._disc_ni_name({"account_nm": nm}), nm
    for nm in ("중단영업이익(손실)의 귀속", "중단영업순이익 지배기업 소유주지분", "기본주당중단영업이익(손실)", "계속영업반기순이익"):
        assert not C._disc_ni_name({"account_nm": nm}), nm


def test_q1_total_confirmed_by_pbt_minus_tax_plus_discontinued_keeps_sce():
    """① 의 반대 — 세전 − 법인세에 중단영업 순이익을 더해야 총계가 확인되는 보고서(합성: 081580 에 중단영업 5억 줄을 넣고 세전을 그만큼 낮춤)."""
    rows = rows_081580_2022q1()
    for x in rows:
        if x["account_id"] == F + "ProfitLossBeforeTax":
            x["thstrm_amount"] = x["thstrm_add_amount"] = str(3090338741 + 63249195 - 500000000)
    rows.append(rep("11013", [r("CIS", F + "ProfitLossFromDiscontinuedOperations", "중단영업이익(손실)", 10, 500000000, cum=500000000)])[0])
    assert C._tot_cols_disagree(rows)
    assert C._sce_ctrl_ni(rows) == 3326498901.0


def test_q1_total_line_found_by_name_is_checked_too():
    """① — 표준 계정코드 없이 이름으로만 적은 총계 줄(「당기순이익(손실)」)도 당기 칸 ≠ 누적 칸을 본다(합성: 081580 총계 줄 id 를 지움)."""
    rows = rows_081580_2022q1()
    for x in rows:
        if x["sj_div"] == "CIS" and x["account_id"] == F + "ProfitLoss":
            x["account_id"] = NOSTD
    assert C._tot_cols_disagree(rows)
    assert C._sce_ctrl_ni(rows) is None
    assert C.controlling_ni(rows)[0] == 2712238401.0


def test_continuing_line_without_sce_nci_column_is_not_used_when_is_has_nci():
    """② — 계속영업 자본변동표 줄에 비지배 열이 없는데 손익계산서엔 0 이 아닌 비지배 줄이 있으면(두 표가 어긋남) 중단영업 줄이 맞아도 쓰지 않는다
    (합성: 089470 자본변동표 비지배 열을 지우고 지배 열을 계속영업 총계 166.84억으로)."""
    rows = [x for x in rows_089470_2023h1() if not (x["sj_div"] == "SCE" and "비지배" in x["account_detail"])]
    for x in rows:
        if x["sj_div"] == "SCE" and "계속" in x["account_nm"]:
            x["thstrm_amount"] = "16684272870"
    assert C._sce_ctrl_ni(rows) is None
