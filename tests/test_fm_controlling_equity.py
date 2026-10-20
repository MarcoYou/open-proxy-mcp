# -*- coding: utf-8 -*-
"""재무 지표 도구(financial_metrics)의 지배자본 — 수집·배수 도구와 **같은 공용 규칙**(`services/controlling_eq`)으로 고른다(261021 fin-eq).

지배자본(ROE 분모·자본잠식의 자기자본)을 종전엔 지배자본 계정 id(`…EquityAttributableToOwnersOfParent`)가 든 **첫 줄 그대로** 썼다. 회사가 그 계정을 「자본금」
줄에 붙인 보고서에서는 자본금(수십억)이 지배자본이 되어 ROE 가 수십~수백 % 로 나오고 자본잠식은 「정상」이 됐다. 같은 원문을 읽는 분기·연간 수집과
배수 도구(`price_multiple_data`)는 그 줄을 거르고 자본총계 − 비지배로 가는데 이 도구만 그 규칙을 안 거쳤다. 지배자본 줄이 없는 보고서(비지배 몫이
있는 회사)는 ROE 분모가 비지배를 품은 자본총계였다.

수치는 DART 전체 재무제표(fnlttSinglAcntAll)·주요계정(fnlttSinglAcnt) 원문에서 지배자본·순이익·총계 줄만 남긴 것이다(줄인 뒤에도 같은 값이 나오는 것을
전체 줄과 대조했다). 줄 튜플 = (재무제표, 계정 id, 계정명, 당기, 전기, ord), 주요계정 튜플 = (재무제표, 계정명, 당기, 전기). DART 0콜(가짜 응답).

  · 065680 FY2024 사업보고서 — 지배자본 계정이 「자본금」 줄(49.67억, 자본총계 2,463.74억 · 비지배 −0.09억 → 지배자본 2,463.83억).
    종전 summary(FY2025) ROE 16.11% · yearly FY2024 400.34% · FY2023 113.06% → 8.47% · 8.45% · 2.51%.
  · 069960 FY2019·FY2022 — 납입자본 줄(1,170.12억 = 자본금)에 지배자본 계정: 단년 ROE 165.92% → 4.58%, 123.13% → 3.14%.
  · 000040 FY2019 — 납입자본 줄(1,054.57억)에 지배자본 계정(자본총계 352.10억 − 비지배 166.69억 = 지배자본 185.41억): 자본잠식 「정상」(−11.33%) → 관리종목 꼴 partial_50plus 80.43%.
  · 056360 FY2022 — 지배자본 계정이 자본금 줄(48.98억)에 붙고 지배기업 지분(1,317.01억)은 표준 코드 없는 이름 줄에: ROE 91.73% → 3.41%.
  · 419050 FY2025 — 지배자본 줄 없음(비지배 있음): ROE 분모 자본총계 916.66억 → 지배자본 641.84억.
"""
from __future__ import annotations

import asyncio

import pytest

from open_proxy_mcp.services import financial_metrics as FM

# ── 가짜 DART 응답 ────────────────────────────────────────────────────────────────────────────────
def _acnt_rows(year: int, rep: dict) -> list[dict]:
    return [{"rcept_no": rep["rcept"], "reprt_code": "11011", "bsns_year": str(year), "fs_div": "CFS", "sj_div": sj, "account_nm": nm,
             "thstrm_dt": f"{year}.12.31 현재", "thstrm_amount": th, "frmtrm_dt": f"{year - 1}.12.31 현재", "frmtrm_amount": frm,
             "ord": str(i), "currency": "KRW"} for i, (sj, nm, th, frm) in enumerate(rep["acnt"], 1)]


def _all_rows(year: int, rep: dict) -> list[dict]:
    return [{"rcept_no": rep["rcept"], "reprt_code": "11011", "bsns_year": str(year), "fs_div": "CFS", "sj_div": sj, "account_id": aid,
             "account_nm": nm, "thstrm_amount": th, "frmtrm_amount": frm, "ord": od, "currency": "KRW"} for sj, aid, nm, th, frm, od in rep["all"]]


def _install(mp, reports: dict[int, dict], calls: list | None = None, *, no_all: bool = False) -> None:
    """연도 → 보고서 픽스처. 없는 연도·다른 보고서 코드는 DART 013(자료 없음)."""
    async def acnt(cc, year, rc, fs):
        if calls is not None:
            calls.append(("acnt", year, rc))
        rep = reports.get(year)
        return (_acnt_rows(year, rep), None) if rep and rc == "11011" else ([], "no_filing")

    async def acnt_all(cc, year, rc, fs):
        if calls is not None:
            calls.append(("all", year, rc))
        rep = reports.get(year)
        return (_all_rows(year, rep), None) if rep and rc == "11011" and not no_all else ([], "no_filing")

    async def none(*a, **k):
        return None

    mp.setattr(FM, "_safe_fetch_acnt", acnt)
    mp.setattr(FM, "_safe_fetch_acnt_all", acnt_all)
    mp.setattr(FM, "_periodic_filing_ref", none)
    mp.setattr(FM, "_accrual_payout_pct", none)


def _year(mp, reports: dict[int, dict], year: int) -> dict:
    _install(mp, reports)
    metrics, _ws, _n = asyncio.run(FM._fetch_year_metrics("00000000", year, "CFS", allow_quarterly_fallback=False))
    return metrics


def _yearly(mp, reports: dict[int, dict], end: int, years: int) -> dict[int, dict]:
    _install(mp, reports)
    rows, _ws = asyncio.run(FM._build_yearly("00000000", end, years, "CFS"))
    return {m["year"]: m for m in rows}


# ── 원문 줄(줄임) ─────────────────────────────────────────────────────────────────────────────────

A_065680_2023 = {
    "rcept": '20240320000915',
    "all": [
        ('BS', 'ifrs-full_Assets', '자산총계', '298458120380', '292905254795', '7'),
        ('BS', 'ifrs-full_Equity', '자본총계', '223924851066', '225535606214', '29'),
        ('BS', 'ifrs-full_EquityAttributableToOwnersOfParent', '자본금', '4967110000', '4967110000', '30'),
        ('BS', 'ifrs-full_NoncontrollingInterests', '비지배지분', '-78415589', '-78415589', '31'),
        ('BS', 'ifrs-full_EquityAndLiabilities', '자본과부채총계', '298458120380', '292905254795', '33'),
        ('BS', 'ifrs-full_Liabilities', '부채총계', '74533269314', '67369648581', '43'),
        ('CIS', 'dart_OperatingIncomeLoss', '영업이익', '8014187683', '20547097627', '6'),
        ('CIS', 'ifrs-full_ComprehensiveIncomeAttributableToOwnersOfParent', '지배기업의 소유주에 귀속될 총포괄이익(손실)', '5166656802', '13367431444', '13'),
        ('CIS', 'ifrs-full_ProfitLoss', '당기순이익(손실)', '5615967439', '12219357336', '28'),
        ('CIS', 'ifrs-full_ProfitLossAttributableToOwnersOfParent', '지배기업의 소유주에게 귀속되는 당기순이익(손실)', '5615967439', '12219357336', '31'),
        ('CIS', 'ifrs-full_ProfitLossBeforeTax', '법인세비용차감전순이익(손실)', '8800690901', '20306364148', '32'),
        ('CIS', 'ifrs-full_ProfitLossFromContinuingOperations', '계속영업이익', '6407295254', '14949737667', '33'),
        ('CIS', 'ifrs-full_ProfitLossFromDiscontinuedOperations', '중단영업이익(손실)', '791327815', '2730380331', '34'),
        ('CIS', 'ifrs-full_Revenue', '매출액', '159897623025', '186384259092', '35'),
    ],
    "acnt": [
        ('BS', '자산총계', '298458120380', '292905254795'),
        ('BS', '자본총계', '223924851066', '225535606214'),
        ('BS', '자본금', '4967110000', '4967110000'),
        ('BS', '이익잉여금', '205809494128', '205778455189'),
        ('BS', '부채총계', '74533269314', '67369648581'),
        ('IS', '영업이익', '8014187683', '20547097627'),
        ('IS', '당기순이익(손실)', '5615967439', '12219357336'),
        ('IS', '매출액', '159897623025', '186384259092'),
    ],
}

A_065680_2024 = {
    "rcept": '20250318000538',
    "all": [
        ('BS', 'ifrs-full_Assets', '자산총계', '327733689956', '298458120380', '7'),
        ('BS', 'ifrs-full_Equity', '자본총계', '246373997170', '223924851066', '29'),
        ('BS', 'ifrs-full_EquityAttributableToOwnersOfParent', '자본금', '4967110000', '4967110000', '30'),
        ('BS', 'ifrs-full_NoncontrollingInterests', '비지배지분', '-8988087', '-78415589', '31'),
        ('BS', 'ifrs-full_EquityAndLiabilities', '자본과부채총계', '327733689956', '298458120380', '33'),
        ('BS', 'ifrs-full_Liabilities', '부채총계', '81359692786', '74533269314', '43'),
        ('CIS', 'dart_OperatingIncomeLoss', '영업이익', '20125236440', '8014187683', '6'),
        ('CIS', '-표준계정코드 미사용-', '중단영업이익(손실)', '', '-791327815', '10'),
        ('CIS', 'ifrs-full_ComprehensiveIncomeAttributableToNoncontrollingInterests', '비지배지분에 귀속되는 총포괄손익', '-50572498', '', '13'),
        ('CIS', 'ifrs-full_ComprehensiveIncomeAttributableToOwnersOfParent', '지배기업의 소유주에 귀속될 총포괄손익', '23585489252', '5166656802', '14'),
        ('CIS', 'ifrs-full_ProfitLoss', '당기순이익(손실)', '19834818298', '5615967439', '31'),
        ('CIS', 'ifrs-full_ProfitLossAttributableToNoncontrollingInterests', '비지배지분에 귀속되는 당기순이익(손실)', '-50572498', '', '33'),
        ('CIS', 'ifrs-full_ProfitLossAttributableToOwnersOfParent', '지배기업의 소유주에게 귀속되는 당기순이익(손실)', '19885390796', '5615967439', '34'),
        ('CIS', 'ifrs-full_ProfitLossBeforeTax', '법인세비용차감전순이익(손실)', '25235390692', '8800690901', '35'),
        ('CIS', 'ifrs-full_ProfitLossFromContinuingOperations', '계속영업이익(손실)', '19834818298', '6407295254', '36'),
        ('CIS', 'ifrs-full_Revenue', '매출액', '176181835330', '159897623025', '37'),
    ],
    "acnt": [
        ('BS', '자산총계', '327733689956', '298458120380'),
        ('BS', '자본총계', '246373997170', '223924851066'),
        ('BS', '자본금', '4967110000', '4967110000'),
        ('BS', '이익잉여금', '224489114274', '205809494128'),
        ('BS', '부채총계', '81359692786', '74533269314'),
        ('IS', '영업이익', '20125236440', '8014187683'),
        ('IS', '당기순이익(손실)', '19834818298', '5615967439'),
        ('IS', '매출액', '176181835330', '159897623025'),
    ],
}

A_065680_2025 = {
    "rcept": '20260814001264',
    "all": [
        ('BS', 'ifrs-full_Assets', '자산총계', '345782147365', '327733689956', '7'),
        ('BS', 'ifrs-full_Equity', '자본총계', '262951977035', '246373997170', '27'),
        ('BS', 'ifrs-full_EquityAttributableToOwnersOfParent', '지배기업의 소유주에게 귀속되는 자본', '262899617160', '246382985257', '28'),
        ('BS', 'ifrs-full_IssuedCapital', '자본금', '4967110000', '4967110000', '32'),
        ('BS', 'ifrs-full_NoncontrollingInterests', '비지배지분', '52359875', '-8988087', '34'),
        ('BS', 'ifrs-full_EquityAndLiabilities', '자본과부채총계', '345782147365', '327733689956', '35'),
        ('BS', 'ifrs-full_Liabilities', '부채총계', '82830170330', '81359692786', '45'),
        ('CIS', 'dart_OperatingIncomeLoss', '영업이익(손실)', '21809545822', '20125236440', '7'),
        ('CIS', 'ifrs-full_ComprehensiveIncomeAttributableToNoncontrollingInterests', '포괄손익, 비지배지분', '-58652038', '-50572498', '14'),
        ('CIS', 'ifrs-full_ComprehensiveIncomeAttributableToOwnersOfParent', '포괄손익, 지배기업의 소유주에게 귀속되는 지분', '18928173203', '23585489252', '15'),
        ('CIS', 'ifrs-full_ProfitLoss', '당기순이익(손실)', '21515008749', '19834818298', '32'),
        ('CIS', 'ifrs-full_ProfitLossAttributableToNoncontrollingInterests', '비지배지분에 귀속되는 당기순이익(손실)', '-58652038', '-50572498', '34'),
        ('CIS', 'ifrs-full_ProfitLossAttributableToOwnersOfParent', '지배기업의 소유주에게 귀속되는 당기순이익(손실)', '21573660787', '19885390796', '35'),
        ('CIS', 'ifrs-full_ProfitLossBeforeTax', '법인세비용차감전순이익(손실)', '24604544050', '25235390692', '36'),
        ('CIS', 'ifrs-full_ProfitLossFromContinuingOperations', '계속영업이익(손실)', '21515008749', '19834818298', '37'),
        ('CIS', 'ifrs-full_ProfitLossFromDiscontinuedOperations', '중단영업이익(손실)', '0', '0', '38'),
    ],
    "acnt": [
        ('BS', '자산총계', '345782147365', '327733689956'),
        ('BS', '자본총계', '262951977035', '246373997170'),
        ('BS', '자본금', '4967110000', '4967110000'),
        ('BS', '부채총계', '82830170330', '81359692786'),
        ('IS', '당기순이익(손실)', '21515008749', '19834818298'),
    ],
}

A_069960_2019 = {
    "rcept": '20200330004495',
    "all": [
        ('BS', 'ifrs-full_Assets', '자산총계', '7623094297000', '6988562089000', '29'),
        ('BS', 'ifrs-full_Liabilities', '부채총계', '2643807874000', '2159469660000', '53'),
        ('BS', 'ifrs-full_EquityAttributableToOwnersOfParent', '납입자본', '117012205000', '117012205000', '55'),
        ('BS', 'ifrs-full_IssuedCapital', '자본금', '117012205000', '117012205000', '56'),
        ('BS', 'ifrs-full_NoncontrollingInterests', '비지배지분', '737279173000', '696593599000', '61'),
        ('BS', '-표준계정코드 미사용-', '자본총계', '4979286423000', '4829092429000', '62'),
        ('BS', 'ifrs-full_EquityAndLiabilities', '자본과부채총계', '7623094297000', '6988562089000', '63'),
        ('IS', 'ifrs-full_Revenue', '매출액', '2198949660000', '1862205782000', '0'),
        ('IS', 'dart_OperatingIncomeLoss', '영업이익(손실)', '292244731000', '356659054000', '4'),
        ('IS', 'ifrs-full_ProfitLossBeforeTax', '법인세비용차감전순이익(손실)', '338613481000', '398285395000', '12'),
        ('IS', 'ifrs-full_ProfitLossAttributableToOwnersOfParent', '지배기업소유주지분', '194142823000', '238964130000', '15'),
        ('IS', 'ifrs-full_ProfitLossAttributableToNoncontrollingInterests', '비지배지분', '48867742000', '48454834000', '16'),
        ('CIS', 'ifrs-full_ProfitLoss', '당기순이익(손실)', '243010565000', '287418964000', '0'),
        ('CIS', 'ifrs-full_ComprehensiveIncomeAttributableToOwnersOfParent', '지배기업소유주지분', '162238586000', '227986490000', '13'),
        ('CIS', 'ifrs-full_ComprehensiveIncomeAttributableToNoncontrollingInterests', '비지배지분', '47797492000', '47520874000', '14'),
    ],
    "acnt": [
        ('BS', '자산총계', '7623094297000', '6988562089000'),
        ('BS', '부채총계', '2643807874000', '2159469660000'),
        ('BS', '자본금', '117012205000', '117012205000'),
        ('BS', '자본총계', '4979286423000', '4829092429000'),
        ('IS', '매출액', '2198949660000', '1862205782000'),
        ('IS', '당기순이익(손실)', '243010565000', '287418964000'),
    ],
}

A_069960_2022 = {
    "rcept": '20230320001061',
    "all": [
        ('BS', 'ifrs-full_Assets', '자산총계', '12069249655000', '8969196456000', '28'),
        ('BS', 'ifrs-full_Liabilities', '부채총계', '5702018783000', '3743568385000', '53'),
        ('BS', 'ifrs-full_EquityAttributableToOwnersOfParent', '납입자본', '117012205000', '117012205000', '55'),
        ('BS', 'ifrs-full_IssuedCapital', '자본금', '117012205000', '117012205000', '56'),
        ('BS', 'ifrs-full_NoncontrollingInterests', '비지배지분', '1782888806000', '804696342000', '61'),
        ('BS', '-표준계정코드 미사용-', '자본총계', '6367230872000', '5225628071000', '62'),
        ('BS', 'ifrs-full_EquityAndLiabilities', '자본과부채총계', '12069249655000', '8969196456000', '63'),
        ('IS', 'ifrs-full_Revenue', '매출액', '5014123629000', '3572433182000', '0'),
        ('IS', 'dart_OperatingIncomeLoss', '영업이익(손실)', '320928734000', '264365188000', '4'),
        ('IS', 'ifrs-full_ProfitLossBeforeTax', '법인세비용차감전순이익(손실)', '246711565000', '316861267000', '10'),
        ('IS', 'ifrs-full_ProfitLossAttributableToOwnersOfParent', '지배기업소유주지분', '144080692000', '189377183000', '13'),
        ('IS', 'ifrs-full_ProfitLossAttributableToNoncontrollingInterests', '비지배지분', '41945684000', '44011742000', '14'),
        ('CIS', 'ifrs-full_ProfitLoss', '당기순이익', '186026376000', '233388925000', '0'),
        ('CIS', 'ifrs-full_ComprehensiveIncomeAttributableToOwnersOfParent', '지배기업소유주지분', '187735419000', '201148869000', '13'),
        ('CIS', 'ifrs-full_ComprehensiveIncomeAttributableToNoncontrollingInterests', '비지배지분', '32468562000', '45420630000', '14'),
    ],
    "acnt": [
        ('BS', '자산총계', '12069249655000', '8969196456000'),
        ('BS', '부채총계', '5702018783000', '3743568385000'),
        ('BS', '자본금', '117012205000', '117012205000'),
        ('BS', '자본총계', '6367230872000', '5225628071000'),
        ('IS', '매출액', '5014123629000', '3572433182000'),
        ('IS', '당기순이익', '186026376000', '233388925000'),
    ],
}

A_000040_2019 = {
    "rcept": '20200519000184',
    "all": [
        ('BS', 'ifrs-full_Assets', '자산총계', '144081159835', '154901066995', '15'),
        ('BS', 'ifrs-full_Liabilities', '부채총계', '108871143114', '85006462283', '34'),
        ('BS', 'ifrs-full_EquityAttributableToOwnersOfParent', '납입자본', '105457383987', '103795192684', '36'),
        ('BS', 'ifrs-full_IssuedCapital', '자본금', '94722037500', '94722037500', '37'),
        ('BS', 'ifrs-full_NoncontrollingInterests', '비지배지분', '16668673499', '16977977901', '42'),
        ('BS', 'ifrs-full_Equity', '자본총계', '35210016721', '69894604712', '43'),
        ('BS', 'ifrs-full_EquityAndLiabilities', '부채및자본총계', '144081159835', '154901066995', '44'),
        ('CIS', 'ifrs-full_Revenue', '매출액', '132579261122', '36563363388', '0'),
        ('CIS', 'dart_OperatingIncomeLoss', '영업이익(손실)', '-24650440438', '-16257022695', '4'),
        ('CIS', 'ifrs-full_ProfitLossBeforeTax', '법인세비용차감전순이익(손실)', '-40493805679', '-24431116639', '31'),
        ('CIS', 'ifrs-full_ProfitLoss', '당기순이익(손실)', '-41041685086', '-24442080215', '33'),
        ('CIS', 'ifrs-full_ProfitLossAttributableToOwnersOfParent', '지배주주 지분순이익', '-40478571496', '-23374529244', '43'),
        ('CIS', 'ifrs-full_ProfitLossAttributableToNoncontrollingInterests', '비지배지분', '-563113590', '-1067550971', '44'),
        ('CIS', 'ifrs-full_ComprehensiveIncomeAttributableToOwnersOfParent', '지배지주 소유주지분', '-27060279270', '-23437513496', '46'),
        ('CIS', 'ifrs-full_ComprehensiveIncomeAttributableToNoncontrollingInterests', '비지재지분', '-417149458', '-1245732923', '47'),
    ],
    "acnt": [
        ('BS', '자산총계', '144081159835', '154901066995'),
        ('BS', '부채총계', '108871143114', '85006462283'),
        ('BS', '자본금', '94722037500', '94722037500'),
        ('BS', '자본총계', '35210016721', '69894604712'),
        ('IS', '매출액', '132579261122', '36563363388'),
        ('IS', '당기순이익(손실)', '-41041685086', '-24442080215'),
    ],
}

A_056360_2022 = {
    "rcept": '20230725000318',
    "all": [
        ('BS', 'ifrs-full_Assets', '자산총계', '168894369829', '159423195629', '17'),
        ('BS', 'ifrs-full_Liabilities', '부채총계', '37193425163', '31450398252', '30'),
        ('BS', '-표준계정코드 미사용-', '지배기업지분', '131700944666', '127972797377', '32'),
        ('BS', 'ifrs-full_EquityAttributableToOwnersOfParent', '자본금', '4898400000', '4898400000', '33'),
        ('BS', 'ifrs-full_Equity', '자본총계', '131700944666', '127972797377', '39'),
        ('BS', 'ifrs-full_EquityAndLiabilities', '자본과부채총계', '168894369829', '159423195629', '40'),
        ('CIS', 'ifrs-full_Revenue', '매출액', '89638154238', '85732205955', '0'),
        ('CIS', 'dart_OperatingIncomeLoss', '영업이익(손실)', '296798885', '5682727660', '4'),
        ('CIS', 'ifrs-full_ProfitLossBeforeTax', '법인세비용차감전순이익(손실)', '3615578397', '7057402758', '9'),
        ('CIS', 'ifrs-full_ProfitLoss', '당기순이익', '4493107897', '7012006146', '11'),
        ('CIS', 'ifrs-full_ComprehensiveIncomeAttributableToOwnersOfParent', '지배기업지분', '4493107897', '7012006146', '18'),
        ('CIS', '-표준계정코드 미사용-', '지배기업지분', '4382399139', '6855546389', '20'),
    ],
    "acnt": [
        ('BS', '자산총계', '168894369829', '159423195629'),
        ('BS', '부채총계', '37193425163', '31450398252'),
        ('BS', '자본금', '4898400000', '4898400000'),
        ('BS', '이익잉여금', '115612775799', '111767118510'),
        ('BS', '자본총계', '131700944666', '127972797377'),
        ('IS', '매출액', '89638154238', '85732205955'),
        ('IS', '당기순이익', '4493107897', '7012006146'),
    ],
}

A_419050_2025 = {
    "rcept": '20260318000999',
    "all": [
        ('BS', 'ifrs-full_Assets', '자산총계', '288112815623', '288138134495', '7'),
        ('BS', '-표준계정코드 미사용-', '자본금', '5719624000', '5719624000', '29'),
        ('BS', 'ifrs-full_Equity', '자본총계', '91666285982', '104242726812', '32'),
        ('BS', 'ifrs-full_NoncontrollingInterests', '비지배지분', '27482269842', '30405553054', '33'),
        ('BS', 'ifrs-full_EquityAndLiabilities', '자본과부채총계', '288112815623', '288138134495', '34'),
        ('BS', 'ifrs-full_Liabilities', '부채총계', '196446529641', '183895407683', '48'),
        ('CIS', 'dart_OperatingIncomeLoss', '영업이익', '5432802909', '-6261933413', '6'),
        ('CIS', '-표준계정코드 미사용-', '지배회사지분순손익', '-9815073952', '1682192277', '11'),
        ('CIS', '-표준계정코드 미사용-', '비지배회사지분순손익', '-2923283212', '-93975551', '12'),
        ('CIS', 'ifrs-full_ProfitLoss', '당기순이익(손실)', '-11073412972', '-4810043656', '25'),
        ('CIS', '-표준계정코드 미사용-', '지배회사지분순손익', '-8895556883', '-631836243', '26'),
        ('CIS', '-표준계정코드 미사용-', '비지배회사지분순손익', '-2177856089', '-4178207413', '27'),
        ('CIS', 'ifrs-full_ProfitLossBeforeTax', '법인세비용차감전순이익', '-9573584566', '-4691198520', '28'),
    ],
    "acnt": [
        ('BS', '자산총계', '288112815623', '288138134495'),
        ('BS', '자본금', '5719624000', '5719624000'),
        ('BS', '이익잉여금', '1408203502', '12667823777'),
        ('BS', '자본총계', '91666285982', '104242726812'),
        ('BS', '부채총계', '196446529641', '183895407683'),
        ('IS', '영업이익', '5432802909', '-6261933413'),
        ('IS', '당기순이익(손실)', '-11073412972', '-4810043656'),
    ],
}



R065680 = {2023: A_065680_2023, 2024: A_065680_2024, 2025: A_065680_2025}


# ═══ 1. 지배자본 계정을 「자본금」 줄에 붙인 보고서 ═══════════════════════════════════════════════════════
def test_065680_roe_does_not_use_the_capital_stock_line(monkeypatch):
    """065680 — FY2024 사업보고서의 지배자본 계정 줄은 자본금 49.67억이다. 종전 yearly FY2024 ROE 400.34%·FY2023 113.06%·FY2025 16.11%."""
    y = _yearly(monkeypatch, R065680, 2025, 3)
    assert y[2024]["roe_pct"] == pytest.approx(8.45, abs=0.005)            # 종전 400.34
    assert y[2025]["roe_pct"] == pytest.approx(8.47, abs=0.005)            # 종전 16.11
    assert y[2023]["roe_pct"] == pytest.approx(2.51, abs=0.005)            # 종전 113.06
    # 믿지 않은 줄은 자본총계 − 비지배(2,463.74억 − (−0.09억)) 로 간다 — 케이스에 까닭이 붙는다
    assert y[2024]["controlling_equity_krw"] == 246_373_997_170 + 8_988_087
    assert y[2024]["controlling_equity_case"] == "TOTAL_MINUS_MINORITY_CAPLINE"
    assert y[2024]["prev_controlling_equity_krw"] == 223_924_851_066 + 78_415_589
    # FY2025 은 지배자본 줄이 제 줄이다 — 종전과 같은 값 · 케이스 BS_ID
    assert y[2025]["controlling_equity_krw"] == 262_899_617_160
    assert y[2025]["controlling_equity_case"] == "BS_ID"


def test_065680_capital_impairment_uses_the_shared_equity(monkeypatch):
    """자본잠식의 자기자본도 같은 값 — 종전엔 자본금 줄(= 자본금) 이라 잠식률 0.0%(자본금 − 자기자본 = 0)였다. 이제 자본총계 − 비지배(`derived`)."""
    m = _year(monkeypatch, {2024: A_065680_2024}, 2024)
    assert m["capital_impairment_basis"] == "derived"
    assert m["capital_impairment_status"] == "normal"
    assert m["capital_impairment_ratio_pct"] == pytest.approx((4_967_110_000 - 246_382_985_257) / 4_967_110_000 * 100, abs=0.01)   # 종전 0.0


def test_069960_single_year_roe(monkeypatch):
    """069960 — 납입자본 줄(1,170.12억)에 지배자본 계정을 붙였다. 단년(전기 보고서 없음) ROE: FY2019 165.92% → 4.58%, FY2022 123.13% → 3.14%."""
    m19 = _year(monkeypatch, {2019: A_069960_2019}, 2019)
    assert m19["roe_pct"] == pytest.approx(4.58, abs=0.005)
    assert m19["controlling_equity_krw"] == 4_242_007_250_000
    assert m19["controlling_equity_case"] == "TOTAL_MINUS_MINORITY_CAPLINE"
    m22 = _year(monkeypatch, {2022: A_069960_2022}, 2022)
    assert m22["roe_pct"] == pytest.approx(3.14, abs=0.005)
    assert m22["controlling_equity_krw"] == 4_584_342_066_000


def test_000040_capital_impairment_is_flagged(monkeypatch):
    """000040 FY2019 — 납입자본 줄(1,054.57억)을 지배자본으로 읽어 잠식률 −11.33%(정상)이던 것이 지배자본 185.41억(자본금 947.22억)이면 80.43%(관리종목 꼴 partial_50plus)."""
    m = _year(monkeypatch, {2019: A_000040_2019}, 2019)
    assert m["capital_impairment_status"] == "partial_50plus"
    assert m["capital_impairment_ratio_pct"] == pytest.approx(80.43, abs=0.005)
    assert m["capital_impairment_basis"] == "derived"
    assert m["controlling_equity_krw"] == 18_541_343_222


def test_056360_name_rule_line_that_is_the_capital_stock(monkeypatch):
    """056360 FY2022 — 지배자본 계정이 자본금 줄(48.98억)에 붙어 있다(지배기업 지분 1,317.01억은 표준 코드 없는 이름 줄): ROE 91.73% → 3.41%."""
    m = _year(monkeypatch, {2022: A_056360_2022}, 2022)
    assert m["roe_pct"] == pytest.approx(3.41, abs=0.005)
    assert m["controlling_equity_krw"] == 131_700_944_666
    assert m["controlling_equity_case"] == "BS_NM_CAPLINE"


# ═══ 2. 지배자본 줄이 없는 보고서 — ROE 분모에서 자본총계 대신 자본총계 − 비지배 ═══════════════════════════════
def test_419050_roe_denominator_is_total_minus_minority(monkeypatch):
    """419050 FY2025 — 지배자본 줄이 없다(비지배 있음). 분모 자본총계 916.66억 → 자본총계 − 비지배 = 지배자본 641.84억."""
    m = _year(monkeypatch, {2025: A_419050_2025}, 2025)
    assert m["total_equity_krw"] == 91_666_285_982                       # 비지배를 품은 자본총계 — 이 칸은 그대로
    assert m["controlling_equity_krw"] == 64_184_016_140
    assert m["controlling_equity_case"] == "TOTAL_MINUS_MINORITY"
    assert m["roe_pct"] == pytest.approx(round(m["net_income_krw"] / 64_184_016_140 * 100, 2), abs=0.005)
    assert m["roe_pct"] == pytest.approx(-13.86, abs=0.005)               # 종전 분모(자본총계)면 −9.70%
    assert m["capital_impairment_basis"] == "derived"


def test_old_keys_stay_and_new_keys_are_added(monkeypatch):
    """응답 옛 키는 그대로, 새 키(`controlling_equity_krw`·`controlling_equity_case`·`prev_controlling_equity_krw`)만 더한다."""
    m = _year(monkeypatch, R065680, 2025)
    for k in ("roe_pct", "roa_pct", "equity_multiplier", "roe_dupont_pct", "total_equity_krw", "net_income_krw", "capital_stock_krw",
              "capital_impairment_ratio_pct", "capital_impairment_status", "capital_impairment_basis", "capital_impairment_ratio_total_pct"):
        assert k in m, k
    assert {"controlling_equity_krw", "controlling_equity_case", "prev_controlling_equity_krw"} <= set(m)
    assert "data_quality" not in m                                         # 단위 오류가 없는 응답에는 칸을 만들지 않는다


# ═══ 3. 공용 규칙이 못 낼 때의 물러남 ═══════════════════════════════════════════════════════════════════
def test_without_full_statements_the_denominator_falls_back_to_total_equity(monkeypatch):
    """전체 재무제표(fnlttSinglAcntAll)를 못 받으면 종전처럼 주요계정 자본총계 — 케이스 `TOTAL_FALLBACK`(지배자본이라고 하지 않는다)."""
    _install(monkeypatch, {2025: A_419050_2025}, no_all=True)
    m, _ws, _n = asyncio.run(FM._fetch_year_metrics("00000000", 2025, "CFS", allow_quarterly_fallback=False))
    assert m["controlling_equity_case"] == "TOTAL_FALLBACK"
    assert m["controlling_equity_krw"] == 91_666_285_982


def test_minority_known_but_no_total_row_subtracts_it(monkeypatch):
    """(합성) 전체 재무제표에 지배자본·자본총계 줄은 없고 비지배 줄만 있으면 — 주요계정 자본총계에서 비지배를 뺀다(`TOTAL_MINUS_NCI`)."""
    rep = {"rcept": "20260101000001",
           "all": [("BS", "ifrs-full_NoncontrollingInterests", "비지배지분", "300", "250", "5"),
                   ("CIS", "ifrs-full_ProfitLossAttributableToOwnersOfParent", "지배기업의 소유주에게 귀속되는 당기순이익", "70", "50", "9")],
           "acnt": [("BS", "자산총계", "2000", "1800"), ("BS", "자본총계", "1000", "900"), ("BS", "자본금", "100", "100"),
                    ("IS", "영업이익", "90", "70"), ("IS", "당기순이익(손실)", "80", "60")]}
    m = _year(monkeypatch, {2025: rep}, 2025)
    assert m["controlling_equity_krw"] == 700 and m["controlling_equity_case"] == "TOTAL_MINUS_NCI"
    assert m["capital_impairment_basis"] == "derived"
    assert m["roe_pct"] == pytest.approx(70 / 700 * 100)


def test_capital_impairment_basis_follows_the_shared_case():
    assert FM._ctrl_basis("BS_ID") == "controlling" and FM._ctrl_basis("BS_NM") == "controlling"
    assert FM._ctrl_basis("TOTAL_MINUS_MINORITY") == "derived" and FM._ctrl_basis("TOTAL_MINUS_MINORITY_CAPLINE") == "derived"
    assert FM._ctrl_basis("TOTAL") == "total" and FM._ctrl_basis("TOTAL_OFFTOTAL") == "total"
    # 기본 인자는 종전과 같다 — 지배지분을 직접 읽었다
    got = FM.compute_capital_impairment(capital_stock=100, controlling_equity=60, total_equity=90, nci=30)
    assert got["basis"] == "controlling" and got["status"] == "partial"
    got = FM.compute_capital_impairment(capital_stock=100, controlling_equity=60, total_equity=90, nci=30, controlling_basis="derived")
    assert got["basis"] == "derived" and got["equity_used"] == 60
