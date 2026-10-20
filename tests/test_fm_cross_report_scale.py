# -*- coding: utf-8 -*-
"""재무 지표 도구(financial_metrics)의 보고서 사이 단위 가드 — 이미 받은 사업보고서끼리 대 10ⁿ 배 틀린 보고서를 가린다(261021 fin-eq).

회사가 단위를 잘못 적어 보고서가 **통째로** 10ⁿ 배면 그 보고서 안의 검사(자산 = 부채 + 자본)는 다 맞아 아무도 반응하지 않는다. 같은 날짜의 같은 숫자를 낸
**다른 보고서**와 대야 드러난다 — 사업보고서 N 의 재무상태표 전기 칸은 사업보고서 N−1 의 당기 칸이다(`scale_guard.check_prior_period_mismatch`).
어느 쪽이 틀렸는지는 이어 붙인 보고서들의 다수결(`scale_guard.reconcile_report_scales`)로 가리고, 틀린 보고서는 금액 칸만 10ⁿ 으로 나눠 쓴다(주당 금액은 그대로).
DART 추가 0콜 — 이미 받은 응답끼리만 대고, 다년 조회는 같은 요청의 `_FetchMemo` 가 겹치는 조회를 하나로 합친다.

  · 065150 FY2023 사업보고서(접수 20240408003770) — 금액이 통째로 1,000배(자산총계 56조 7,172억 = 앞뒤 해 보고서의 같은 날짜 칸 567.17억·FY2022 전기 칸 739.67억의 1,000배).
    주요계정(fnlttSinglAcnt)도 같다(DART 확인: 자산총계 56,717,158,975,000). 종전 기본 yearly(2023~2025): FY2023 금액 1,000배 · FY2024 순이익 전년비 +99.79% ·
    ROE −0.09% (전기 평균 분모에 1,000배 자본).
  · 032680 FY2022 사업보고서(접수 20230323001157) — 100만 배(자본총계 78,925,112,592,000,000 = 2023 보고서 전기 칸 789.25억의 100만 배). 종전 FY2023 순이익 전년비 −100.0% · ROE −0.0%.

수치는 DART 원문에서 총계·지배자본·순이익 줄만 남긴 것(튜플 꼴은 `test_fm_controlling_equity.py` 와 같다). DART 0콜(가짜 응답).
"""
from __future__ import annotations

import asyncio

import pytest

from open_proxy_mcp.dart import client as dart_client
from open_proxy_mcp.dart.client import DartClientError
from open_proxy_mcp.services import financial_metrics as FM
from open_proxy_mcp.services.company import CompanyResolution
from open_proxy_mcp.services.contracts import AnalysisStatus


# ── 가짜 DART 응답 ────────────────────────────────────────────────────────────────────────────────
def _acnt_rows(year: int, rep: dict) -> list[dict]:
    return [{"rcept_no": rep["rcept"], "reprt_code": "11011", "bsns_year": str(year), "fs_div": "CFS", "sj_div": sj, "account_nm": nm,
             "thstrm_dt": f"{year}.12.31 현재", "thstrm_amount": th, "frmtrm_dt": f"{year - 1}.12.31 현재", "frmtrm_amount": frm,
             "ord": str(i), "currency": "KRW"} for i, (sj, nm, th, frm) in enumerate(rep["acnt"], 1)]


def _all_rows(year: int, rep: dict) -> list[dict]:
    return [{"rcept_no": rep["rcept"], "reprt_code": "11011", "bsns_year": str(year), "fs_div": "CFS", "sj_div": sj, "account_id": aid,
             "account_nm": nm, "thstrm_amount": th, "frmtrm_amount": frm, "ord": od, "currency": "KRW"} for sj, aid, nm, th, frm, od in rep["all"]]


def _install(mp, reports: dict[int, dict], calls: list | None = None) -> None:
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
        return (_all_rows(year, rep), None) if rep and rc == "11011" else ([], "no_filing")

    async def none(*a, **k):
        return None

    mp.setattr(FM, "_safe_fetch_acnt", acnt)
    mp.setattr(FM, "_safe_fetch_acnt_all", acnt_all)
    mp.setattr(FM, "_periodic_filing_ref", none)
    mp.setattr(FM, "_accrual_payout_pct", none)


def _year(mp, reports: dict[int, dict], year: int) -> tuple[dict, list[str]]:
    _install(mp, reports)
    metrics, ws, _n = asyncio.run(FM._fetch_year_metrics("00000000", year, "CFS", allow_quarterly_fallback=False))
    return metrics, ws


def _yearly(mp, reports: dict[int, dict], end: int, years: int, calls: list | None = None) -> tuple[dict[int, dict], list[str]]:
    _install(mp, reports, calls)
    rows, ws = asyncio.run(FM._build_yearly("00000000", end, years, "CFS"))
    return {m["year"]: m for m in rows}, ws


def _scale_ws(ws: list[str]) -> list[str]:
    return [w for w in ws if w.startswith(("🔧", "🚨"))]


# ── 원문 줄(줄임) ─────────────────────────────────────────────────────────────────────────────────
A_065150_2022 = {
    "rcept": '20230324000010',
    "all": [
        ('BS', 'ifrs-full_Assets', '자산총계', '73967255832', '68216630997', '19'),
        ('BS', 'ifrs-full_Liabilities', '부채총계', '41291511955', '27884949795', '35'),
        ('BS', 'ifrs-full_EquityAttributableToOwnersOfParent', '지배기업 소유주 지분', '32762155217', '40331681202', '37'),
        ('BS', 'ifrs-full_IssuedCapital', '자본금', '12528240500', '12528240500', '38'),
        ('BS', 'ifrs-full_NoncontrollingInterests', '비지배지분', '-86411340', '', '43'),
        ('BS', 'ifrs-full_Equity', '자본총계', '32675743877', '40331681202', '44'),
        ('BS', 'ifrs-full_EquityAndLiabilities', '자본과부채총계', '73967255832', '68216630997', '45'),
        ('CIS', 'ifrs-full_Revenue', '매출액', '150035748696', '104305802113', '0'),
        ('CIS', 'dart_OperatingIncomeLoss', '영업이익(손실)', '-5371300392', '-894487828', '4'),
        ('CIS', 'ifrs-full_ProfitLossBeforeTax', '법인세비용차감전순이익(손실)', '-5583783431', '15597753999', '10'),
        ('CIS', 'ifrs-full_ProfitLossFromContinuingOperations', '계속영업당기순이익(손실)', '-5583783431', '14258669254', '12'),
        ('CIS', 'ifrs-full_ProfitLossFromDiscontinuedOperations', '중단영업이익(손실)', '', '-3852708006', '13'),
        ('CIS', 'ifrs-full_ProfitLoss', '당기순이익(손실)', '-5583783431', '10405961248', '14'),
        ('CIS', '-표준계정코드 미사용-', '지배기업 소유주 지분', '-5318464213', '13022484603', '15'),
        ('CIS', '-표준계정코드 미사용-', '계속영업이익(손실)', '-5318464213', '14258669254', '16'),
        ('CIS', '-표준계정코드 미사용-', '중단영업이익(손실)', '', '-1236184651', '17'),
        ('CIS', '-표준계정코드 미사용-', '비지배지분', '-265319218', '-2616523355', '18'),
        ('CIS', '-표준계정코드 미사용-', '계속영업이익(손실)', '-265319218', '', '19'),
        ('CIS', '-표준계정코드 미사용-', '중단영업이익(손실)', '', '-2616523355', '20'),
        ('CIS', '-표준계정코드 미사용-', '지배기업 소유주 지분', '-7569525985', '13475621485', '29'),
        ('CIS', '-표준계정코드 미사용-', '비지배지분', '-265319218', '-2594275170', '30'),
    ],
    "acnt": [
        ('BS', '자산총계', '73967255832', '68216630997'),
        ('BS', '부채총계', '41291511955', '27884949795'),
        ('BS', '자본금', '12528240500', '12528240500'),
        ('BS', '자본총계', '32675743877', '40331681202'),
        ('IS', '매출액', '150035748696', '104305802113'),
        ('IS', '당기순이익(손실)', '-5583783431', '10405961248'),
    ],
}

A_065150_2023 = {
    "rcept": '20240408003770',
    "all": [
        ('BS', 'ifrs-full_Assets', '자산총계', '56717158975000', '73967255832000', '7'),
        ('BS', 'ifrs-full_Equity', '자본총계', '23225320472000', '32675743877000', '27'),
        ('BS', 'ifrs-full_EquityAttributableToOwnersOfParent', '지배기업의 소유주에게 귀속되는 자본', '25548439335000', '32762155217000', '28'),
        ('BS', 'ifrs-full_IssuedCapital', '자본금', '12528240500000', '12528240500000', '33'),
        ('BS', 'ifrs-full_NoncontrollingInterests', '비지배지분', '-2323118863000', '-86411340000', '35'),
        ('BS', 'ifrs-full_EquityAndLiabilities', '자본과부채총계', '56717158975000', '73967255832000', '36'),
        ('BS', 'ifrs-full_Liabilities', '부채총계', '33491838503000', '41291511955000', '45'),
        ('CIS', 'dart_OperatingIncomeLoss', '영업이익(손실)', '-634869097000', '-4018213545000', '6'),
        ('CIS', '-표준계정코드 미사용-', '비지배기준', '-2236707523000', '-265319218000', '12'),
        ('CIS', '-표준계정코드 미사용-', '지배기업 소유주 지분', '-6693055252000', '-7569525985000', '13'),
        ('CIS', 'ifrs-full_ProfitLoss', '당기순이익(손실)', '-8080143052000', '-5583783431000', '32'),
        ('CIS', '-표준계정코드 미사용-', '비지배지분', '-2236707523000', '-265319218000', '33'),
        ('CIS', '-표준계정코드 미사용-', '계속영업이익(손실)', '-2236707523000', '-265319218000', '34'),
        ('CIS', '-표준계정코드 미사용-', '중단영업이익(손실)', '', '', '35'),
        ('CIS', '-표준계정코드 미사용-', '지배기업 소유주 지분', '-5843435529000', '-5318464213000', '36'),
        ('CIS', '-표준계정코드 미사용-', '계속영업이익(손실)', '-5329559759000', '-5318464213000', '37'),
        ('CIS', '-표준계정코드 미사용-', '중단영업이익(손실)', '-513875770000', '', '38'),
        ('CIS', 'ifrs-full_ProfitLossBeforeTax', '법인세비용차감전순이익(손실)', '-7566320242000', '-4827731884000', '39'),
        ('CIS', 'ifrs-full_ProfitLossFromContinuingOperations', '계속영업이익(손실)', '-7566267282000', '-4827731884000', '40'),
        ('CIS', 'ifrs-full_ProfitLossFromDiscontinuedOperations', '중단영업이익(손실)', '-513875770000', '-756051547000', '41'),
        ('CIS', 'ifrs-full_Revenue', '매출액', '128819482459000', '121302188368000', '42'),
    ],
    "acnt": [
        ('BS', '자산총계', '56717158975000', '73967255832000'),
        ('BS', '자본총계', '23225320472000', '32675743877000'),
        ('BS', '자본금', '12528240500000', '12528240500000'),
        ('BS', '부채총계', '33491838503000', '41291511955000'),
        ('IS', '당기순이익(손실)', '-8080143052000', '-5583783431000'),
        ('IS', '매출액', '128819482459000', '121302188368000'),
    ],
}

A_065150_2024 = {
    "rcept": '20250321001891',
    "all": [
        ('BS', 'ifrs-full_Assets', '자산총계', '38453173974', '56404435547', '7'),
        ('BS', 'ifrs-full_Equity', '자본총계', '10654231800', '22912597044', '25'),
        ('BS', 'ifrs-full_EquityAttributableToOwnersOfParent', '지배기업의 소유주에게 귀속되는 지분', '10654231800', '25235715907', '26'),
        ('BS', 'ifrs-full_IssuedCapital', '자본금', '12528240500', '12528240500', '30'),
        ('BS', 'ifrs-full_NoncontrollingInterests', '비지배지분', '', '-2323118863', '32'),
        ('BS', 'ifrs-full_EquityAndLiabilities', '자본과부채총계', '38453173974', '56404435547', '33'),
        ('BS', 'ifrs-full_Liabilities', '부채총계', '27798942174', '33491838503', '42'),
        ('CIS', 'dart_OperatingIncomeLoss', '영업이익(손실)', '-820612174', '756965202', '6'),
        ('CIS', 'ifrs-full_ComprehensiveIncomeAttributableToNoncontrollingInterests', '비지배지분', '', '-2236707523', '11'),
        ('CIS', 'ifrs-full_ComprehensiveIncomeAttributableToOwnersOfParent', '지배기업소유주지분', '-12258355244', '-7005778680', '12'),
        ('CIS', 'ifrs-full_ProfitLoss', '당기순이익(손실)', '-12108460870', '-8392866480', '31'),
        ('CIS', 'ifrs-full_ProfitLossAttributableToNoncontrollingInterests', '비지배지분에 귀속되는 당기순이익(손실)', '', '-2236707523', '32'),
        ('CIS', 'ifrs-full_ProfitLossAttributableToOwnersOfParent', '지배기업 소유주 지분', '-12108460870', '-6156158957', '33'),
        ('CIS', 'ifrs-full_ProfitLossBeforeTax', '법인세비용차감전순이익(손실)', '-9617932662', '-3405575665', '34'),
        ('CIS', 'ifrs-full_ProfitLossFromContinuingOperations', '계속영업이익(손실)', '-9617932662', '-3405575665', '35'),
        ('CIS', 'ifrs-full_ProfitLossFromDiscontinuedOperations', '중단영업이익(손실)', '-2490528208', '-4987290815', '36'),
    ],
    "acnt": [
        ('BS', '자산총계', '38453173974', '56404435547'),
        ('BS', '자본총계', '10654231800', '22912597044'),
        ('BS', '자본금', '12528240500', '12528240500'),
        ('BS', '이익잉여금', '-62160920065', '-50052459195'),
        ('BS', '부채총계', '27798942174', '33491838503'),
        ('IS', '당기순이익(손실)', '-12108460870', '-8392866480'),
    ],
}

A_065150_2025 = {
    "rcept": '20260323001661',
    "all": [
        ('BS', 'ifrs-full_Assets', '자산총계', '37466437775', '40407182931', '7'),
        ('BS', 'ifrs-full_Equity', '자본총계', '24345267606', '12608240757', '26'),
        ('BS', 'ifrs-full_EquityAttributableToOwnersOfParent', '지배기업 소유주 지분', '24345267606', '12608240757', '27'),
        ('BS', 'ifrs-full_IssuedCapital', '자본금', '15938240500', '12528240500', '30'),
        ('BS', 'ifrs-full_NoncontrollingInterests', '비지배지분', '', '', '32'),
        ('BS', 'ifrs-full_EquityAndLiabilities', '자본과부채총계', '37466437775', '40407182931', '33'),
        ('BS', 'ifrs-full_Liabilities', '부채총계', '13121170169', '27798942174', '42'),
        ('CIS', 'dart_OperatingIncomeLoss', '영업이익(손실)', '4554449774', '-758637729', '6'),
        ('CIS', 'ifrs-full_ComprehensiveIncomeAttributableToNoncontrollingInterests', '비지배지분', '', '', '11'),
        ('CIS', 'ifrs-full_ComprehensiveIncomeAttributableToOwnersOfParent', '지배기업소유주지분', '8327026849', '-10304346287', '12'),
        ('CIS', 'ifrs-full_ProfitLoss', '당기순이익(손실)', '8315318465', '-10154451913', '28'),
        ('CIS', 'ifrs-full_ProfitLossAttributableToNoncontrollingInterests', '비지배지분에 귀속되는 당기순이익(손실)', '', '', '29'),
        ('CIS', 'ifrs-full_ProfitLossAttributableToOwnersOfParent', '지배기업 소유주지분', '8315318465', '-10154451913', '30'),
        ('CIS', 'ifrs-full_ComprehensiveIncomeFromContinuingOperationsAttributableToNoncontrollingInterests', '계속영업이익(손실)', '9089754844', '-7487349336', '31'),
        ('CIS', 'ifrs-full_ProfitLossBeforeTax', '법인세비용차감전순이익(손실)', '7770364085', '-7487349336', '33'),
        ('CIS', 'ifrs-full_ProfitLossFromContinuingOperations', '계속영업이익(손실)', '9089754844', '-7487349336', '34'),
        ('CIS', 'ifrs-full_ProfitLossFromDiscontinuedOperations', '중단영업이익(손실)', '-774436379', '-2667102577', '35'),
    ],
    "acnt": [
        ('BS', '자산총계', '37466437775', '40407182931'),
        ('BS', '자본총계', '24345267606', '12608240757'),
        ('BS', '자본금', '15938240500', '12528240500'),
        ('BS', '부채총계', '13121170169', '27798942174'),
        ('IS', '당기순이익(손실)', '8315318465', '-10154451913'),
    ],
}

A_032680_2022 = {
    "rcept": '20230323001157',
    "all": [
        ('BS', 'ifrs-full_Assets', '자산총계', '122129876850000000', '101715378575000000', '19'),
        ('BS', 'ifrs-full_Liabilities', '부채총계', '43204764258000000', '37436571050000000', '34'),
        ('BS', 'ifrs-full_EquityAttributableToOwnersOfParent', '지배기업의 소유주에게 귀속되는 자본', '68063289162000000', '56220232258000000', '36'),
        ('BS', 'ifrs-full_IssuedCapital', '자본금', '19202422500000000', '18477438000000000', '37'),
        ('BS', 'ifrs-full_NoncontrollingInterests', '비지배지분', '10861823430000000', '8058575267000000', '42'),
        ('BS', 'ifrs-full_Equity', '자본총계', '78925112592000000', '64278807525000000', '43'),
        ('BS', 'ifrs-full_EquityAndLiabilities', '자본과부채총계', '122129876850000000', '101715378575000000', '44'),
        ('CIS', 'ifrs-full_Revenue', '매출액', '73373050121000000', '92500127100000000', '0'),
        ('CIS', 'dart_OperatingIncomeLoss', '영업이익(손실)', '16284103976000000', '20571461479000000', '4'),
        ('CIS', 'ifrs-full_ProfitLossBeforeTax', '법인세비용차감전순이익(손실)', '14966286330000000', '18803097563000000', '10'),
        ('CIS', 'ifrs-full_ProfitLossFromContinuingOperations', '계속영업이익(손실)', '11587270444000000', '15958319610000000', '12'),
        ('CIS', 'ifrs-full_ProfitLossFromDiscontinuedOperations', '중단영업이익(손실)', '0', '-159914048000000', '13'),
        ('CIS', 'ifrs-full_ProfitLoss', '당기순이익(손실)', '11587270444000000', '15798405562000000', '14'),
        ('CIS', 'ifrs-full_ProfitLossAttributableToOwnersOfParent', '지배기업의 소유주에게 귀속되는 당기순이익(손실)', '10723795687000000', '15030454636000000', '16'),
        ('CIS', 'ifrs-full_ProfitLossAttributableToNoncontrollingInterests', '비지배지분에 귀속되는 당기순이익(손실)', '863474757000000', '767950926000000', '17'),
        ('CIS', 'ifrs-full_ComprehensiveIncomeAttributableToOwnersOfParent', '지배기업의 소유주에게 귀속되는 총 포괄손익', '9922970555000000', '17451226779000000', '25'),
        ('CIS', 'ifrs-full_ComprehensiveIncomeAttributableToNoncontrollingInterests', '비지배지분에 귀속되는 총 포괄손익', '603248163000000', '1488299542000000', '26'),
    ],
    "acnt": [
        ('BS', '자산총계', '122129876850000000', '101715378575000000'),
        ('BS', '부채총계', '43204764258000000', '37436571050000000'),
        ('BS', '자본금', '19202422500000000', '18477438000000000'),
        ('BS', '자본총계', '78925112592000000', '64278807525000000'),
        ('IS', '매출액', '73373050121000000', '92500127100000000'),
        ('IS', '당기순이익(손실)', '11587270444000000', '15798405562000000'),
    ],
}

A_032680_2023 = {
    "rcept": '20240322000002',
    "all": [
        ('BS', 'ifrs-full_Assets', '자산총계', '102567983755', '122129876850', '7'),
        ('BS', 'ifrs-full_Equity', '자본총계', '65396418307', '78925112592', '27'),
        ('BS', 'ifrs-full_EquityAttributableToOwnersOfParent', '지배기업의 소유주에게 귀속되는 자본', '53100129434', '68063289162', '28'),
        ('BS', 'ifrs-full_IssuedCapital', '자본금', '21196723600', '19202422500', '32'),
        ('BS', 'ifrs-full_NoncontrollingInterests', '비지배지분', '12296288873', '10861823430', '34'),
        ('BS', 'ifrs-full_EquityAndLiabilities', '부채와자본총계', '102567983755', '122129876850', '35'),
        ('BS', 'ifrs-full_Liabilities', '부채총계', '37171565448', '43204764258', '46'),
        ('CIS', 'dart_OperatingIncomeLoss', '영업이익(손실)', '-1418785527', '16284103976', '6'),
        ('CIS', 'ifrs-full_ComprehensiveIncomeAttributableToNoncontrollingInterests', '비지배지분에 귀속되는 총 포괄손익', '1434465443', '603248163', '12'),
        ('CIS', 'ifrs-full_ComprehensiveIncomeAttributableToOwnersOfParent', '지배기업의 소유주에게 귀속되는 총 포괄손익', '-24688966008', '9922970555', '13'),
        ('CIS', 'ifrs-full_ProfitLoss', '당기순이익(손실)', '-9968876097', '11587270444', '24'),
        ('CIS', 'ifrs-full_ProfitLossAttributableToNoncontrollingInterests', '비지배지분에 귀속되는 당기순이익(손실)', '1489208675', '863474757', '26'),
        ('CIS', 'ifrs-full_ProfitLossAttributableToOwnersOfParent', '지배기업의 소유주에게 귀속되는 당기순이익(손실)', '-11458084772', '10723795687', '27'),
        ('CIS', 'ifrs-full_ProfitLossBeforeTax', '법인세비용차감전순이익(손실)', '-10605839215', '14966286330', '28'),
        ('CIS', 'ifrs-full_Revenue', '매출액', '61085045079', '73373050121', '29'),
    ],
    "acnt": [
        ('BS', '자산총계', '102567983755', '122129876850'),
        ('BS', '자본총계', '65396418307', '78925112592'),
        ('BS', '자본금', '21196723600', '19202422500'),
        ('BS', '이익잉여금', '5835848441', '17293933213'),
        ('BS', '부채총계', '37171565448', '43204764258'),
        ('IS', '당기순이익(손실)', '-9968876097', '11587270444'),
        ('IS', '매출액', '61085045079', '73373050121'),
    ],
}

A_032680_2024 = {
    "rcept": '20250320001159',
    "all": [
        ('BS', 'ifrs-full_Assets', '자산총계', '96907853710', '102567983755', '7'),
        ('BS', 'ifrs-full_Equity', '자본총계', '66890101036', '65396418307', '26'),
        ('BS', 'ifrs-full_EquityAttributableToOwnersOfParent', '지배기업의 소유주에게 귀속되는 자본', '52751465487', '53100129434', '27'),
        ('BS', 'ifrs-full_IssuedCapital', '자본금', '21196723600', '21196723600', '31'),
        ('BS', 'ifrs-full_NoncontrollingInterests', '비지배지분', '14138635549', '12296288873', '33'),
        ('BS', 'ifrs-full_EquityAndLiabilities', '부채와자본총계', '96907853710', '102567983755', '34'),
        ('BS', 'ifrs-full_Liabilities', '부채총계', '30017752674', '37171565448', '45'),
        ('CIS', 'dart_OperatingIncomeLoss', '영업이익(손실)', '-1375131016', '-1418785527', '6'),
        ('CIS', 'ifrs-full_ComprehensiveIncomeAttributableToNoncontrollingInterests', '비지배지분에 귀속되는 총 포괄손익', '1842346676', '1434465443', '12'),
        ('CIS', 'ifrs-full_ComprehensiveIncomeAttributableToOwnersOfParent', '지배기업의 소유주에게 귀속되는 총 포괄손익', '-348663947', '-24688966008', '13'),
        ('CIS', 'ifrs-full_ProfitLoss', '당기순이익(손실)', '-2797081407', '-9968876097', '24'),
        ('CIS', 'ifrs-full_ProfitLossAttributableToNoncontrollingInterests', '비지배지분에 귀속되는 당기순이익(손실)', '669460538', '1489208675', '26'),
        ('CIS', 'ifrs-full_ProfitLossAttributableToOwnersOfParent', '지배기업의 소유주에게 귀속되는 당기순이익(손실)', '-3466541945', '-11458084772', '27'),
        ('CIS', 'ifrs-full_ProfitLossBeforeTax', '법인세비용차감전순이익(손실)', '-2163667138', '-10605839215', '28'),
        ('CIS', 'ifrs-full_Revenue', '매출액', '59901019376', '61085045079', '29'),
    ],
    "acnt": [
        ('BS', '자산총계', '96907853710', '102567983755'),
        ('BS', '자본총계', '66890101036', '65396418307'),
        ('BS', '자본금', '21196723600', '21196723600'),
        ('BS', '이익잉여금', '2369306496', '5835848441'),
        ('BS', '부채총계', '30017752674', '37171565448'),
        ('IS', '당기순이익(손실)', '-2797081407', '-9968876097'),
        ('IS', '매출액', '59901019376', '61085045079'),
    ],
}



R065150 = {2022: A_065150_2022, 2023: A_065150_2023, 2024: A_065150_2024, 2025: A_065150_2025}
R032680 = {2022: A_032680_2022, 2023: A_032680_2023, 2024: A_032680_2024}


# ═══ 1. 다수결로 가려 바로잡는다 ═══════════════════════════════════════════════════════════════════════
def test_065150_fy2023_report_x1000_is_divided(monkeypatch):
    calls: list = []
    y, ws = _yearly(monkeypatch, R065150, 2025, 3, calls)
    # 틀린 보고서(FY2023)의 금액은 ÷1,000 — 종전 자산총계 56,717,158,975,000(56.7조)
    assert y[2023]["total_assets_krw"] == 56_717_158_975
    assert y[2023]["total_equity_krw"] == 23_225_320_472
    assert y[2023]["net_income_krw"] == -5_843_435_529                    # 종전 ×1,000
    fix = y[2023]["data_quality"]["scale_fix"]
    assert len(fix) == 1 and fix[0]["report"] == "2023 사업보고서" and fix[0]["rcept_no"] == "20240408003770"
    assert fix[0]["divisor"] == 1000 and fix[0]["method"] == "majority"
    assert {e["compared_with"] for e in fix[0]["evidence"]} == {"2022 사업보고서", "2024 사업보고서"}
    # 그 보고서를 전기로 읽는 FY2024 도 같은 값 — 순이익 전년비 +99.79%(종전: 1,000배 전기 대비) → −107.21%, ROE −0.09% → −66.89%
    assert y[2024]["data_quality"]["scale_fix"] == fix
    assert y[2024]["net_income_yoy_pct"] == pytest.approx(-107.21, abs=0.005)
    assert y[2024]["roe_pct"] == pytest.approx(-66.89, abs=0.005)
    assert y[2024]["prev_controlling_equity_krw"] == 25_548_439_335       # 종전 25,548,439,335,000
    # 맞는 보고서(FY2025)에는 칸을 만들지 않는다 — 종전 응답 그대로
    assert "data_quality" not in y[2025]
    # 경고는 한 번만(FY2023·FY2024 두 해가 같은 문장을 내므로 합친다)
    notes = _scale_ws(ws)
    assert len(notes) == 1 and notes[0].startswith("🔧") and "÷1,000" in notes[0] and "20240408003770" in notes[0]
    # DART 추가 0콜 — 보고서마다 주요계정·전체 재무제표 한 번씩(FY2022 는 FY2023 의 전기 보고서)
    assert sorted(c for c in calls if c[0] == "acnt") == [("acnt", yy, "11011") for yy in (2022, 2023, 2024, 2025)]
    assert sorted(c for c in calls if c[0] == "all") == [("all", yy, "11011") for yy in (2022, 2023, 2024, 2025)]


def test_032680_fy2022_report_x1e6_is_divided(monkeypatch):
    y, ws = _yearly(monkeypatch, R032680, 2024, 3)
    assert y[2022]["total_assets_krw"] == 122_129_876_850                  # 종전 122,129,876,850,000,000
    assert y[2022]["data_quality"]["scale_fix"][0]["divisor"] == 1_000_000
    assert y[2022]["data_quality"]["scale_fix"][0]["rcept_no"] == "20230323001157"
    # FY2023 은 FY2022 를 전기로 읽는다 — 순이익 전년비 −100.0%(종전, 전기가 100만 배)·ROE −0.0% → −206.85%·−18.91%
    assert y[2023]["net_income_yoy_pct"] == pytest.approx(-206.85, abs=0.005)
    assert y[2023]["roe_pct"] == pytest.approx(-18.91, abs=0.005)
    assert "data_quality" not in y[2024]
    assert len(_scale_ws(ws)) == 1 and "÷1,000,000" in _scale_ws(ws)[0]


def test_per_share_lines_are_not_divided():
    """주당 금액(EPS) 줄은 단위 선언과 따로 적혀 나누지 않는다 — 금액 줄만 ÷1,000."""
    rows = [{"sj_div": "BS", "account_id": "ifrs-full_Assets", "account_nm": "자산총계", "thstrm_amount": "5,000", "frmtrm_amount": "4,000"},
            {"sj_div": "CIS", "account_id": "ifrs-full_BasicEarningsLossPerShare", "account_nm": "기본주당이익", "thstrm_amount": "-11"}]
    plan = {"status": "fixed", "divisors": {2023: 1000}}
    out = FM._apply_scale(rows, plan, 2023)
    assert out[0]["thstrm_amount"] == "5" and out[0]["frmtrm_amount"] == "4"
    assert out[1]["thstrm_amount"] == "-11"
    assert FM._apply_scale(rows, plan, 2022) is rows                       # 다른 연도·바로잡을 게 없는 plan 은 그대로


# ═══ 2. 가릴 수 없으면 값은 원문 그대로 두고 경고만 ══════════════════════════════════════════════════════
def test_two_reports_cannot_vote_so_only_warns(monkeypatch):
    """요약(단년)은 당기·전기 두 보고서뿐이라 다수결이 안 선다 — 값은 원문 그대로, 어긋남을 경고·`data_quality.scale_cross_report` 로 알린다."""
    m, ws = _year(monkeypatch, {2022: A_065150_2022, 2023: A_065150_2023}, 2023)
    assert m["total_assets_krw"] == 56_717_158_975_000                     # 고치지 않았다
    cross = m["data_quality"]["scale_cross_report"]
    assert cross["status"] == "undecided" and all(p["power"] == 3 for p in cross["pairs"])
    assert "scale_fix" not in m["data_quality"]
    notes = _scale_ws(ws)
    assert len(notes) == 1 and notes[0].startswith("🚨") and "원문 그대로" in notes[0] and "자본총계" in notes[0]


def test_clean_reports_leave_the_response_as_before(monkeypatch):
    m, ws = _year(monkeypatch, {2024: A_065150_2024, 2025: A_065150_2025}, 2025)
    assert "data_quality" not in m and _scale_ws(ws) == []
    assert m["total_assets_krw"] == 37_466_437_775


def _eq_rows(year: int, th: str, frm: str, ccy: str = "KRW") -> list[dict]:
    return [{"rcept_no": f"{year}0000", "sj_div": "BS", "account_nm": "자본총계", "thstrm_dt": f"{year}.12.31 현재", "thstrm_amount": th,
             "frmtrm_dt": f"{year - 1}.12.31 현재", "frmtrm_amount": frm, "currency": ccy}]


def test_reconcile_ignores_restatements_sign_flips_and_ties():
    # 재작성(1.07배)은 단위 오류가 아니다
    r = FM._reconcile_annual_scales({2023: _eq_rows(2023, "1,070", "900"), 2024: _eq_rows(2024, "1,300", "1,000")})
    assert r["status"] == "clean" and r["divisors"] == {}
    # 부호가 다르면 같은 숫자가 아니다
    r = FM._reconcile_annual_scales({2023: _eq_rows(2023, "-1,000", "900"), 2024: _eq_rows(2024, "1,300", "1,000,000")})
    assert r["status"] == "clean"
    # 한 보고서뿐이면 대 볼 상대가 없다
    assert FM._reconcile_annual_scales({2024: _eq_rows(2024, "1,300", "1,000")})["status"] == "clean"
    # 2 대 2 — 다수가 없으면 고르지 않는다(undecided)
    four = {2021: _eq_rows(2021, "1,000", "900"), 2022: _eq_rows(2022, "1,100", "1,000"),
            2023: _eq_rows(2023, "1,200,000", "1,100,000"), 2024: _eq_rows(2024, "1,300,000", "1,200,000")}
    r = FM._reconcile_annual_scales(four)
    assert r["status"] == "undecided" and r["divisors"] == {}


def test_too_small_report_is_scaled_up(monkeypatch):
    """반대 방향 — 한 보고서가 1,000배 **작게** 적혔으면 ×1,000 (`divisor` < 1)."""
    r = FM._reconcile_annual_scales({2022: _eq_rows(2022, "1,100,000", "1,000,000"), 2023: _eq_rows(2023, "1,300", "1,100"),
                                     2024: _eq_rows(2024, "1,500,000", "1,300,000"), 2025: _eq_rows(2025, "1,700,000", "1,500,000")})
    assert r["status"] == "fixed" and r["divisors"] == {2023: pytest.approx(0.001)}
    rows = FM._apply_scale(_eq_rows(2023, "1,300", "1,100"), r, 2023)
    assert rows[0]["thstrm_amount"] == "1300000" and rows[0]["frmtrm_amount"] == "1100000"


# ═══ 3. yoy 도 같은 값 ═════════════════════════════════════════════════════════════════════════════════
def test_yoy_scope_uses_the_same_corrected_reports(monkeypatch):
    """scope=yoy — 앞뒤 세 해(FY2022~2024) 보고서끼리 대 FY2023 을 바로잡는다. 두 해가 겹치는 보고서는 한 번만 받는다."""
    calls: list = []
    _install(monkeypatch, R065150, calls)
    monkeypatch.setenv("OPENDART_API_KEY", "test-key-not-real")

    async def no_data(self, endpoint, params):
        raise DartClientError("013", "가짜 응답")
    monkeypatch.setattr(dart_client.DartClient, "_request", no_data)

    async def resolve(query, *a, **k):
        return CompanyResolution(status=AnalysisStatus.EXACT, query=query, candidates=[],
                                 selected={"corp_code": "00390860", "corp_name": "라온시큐어", "stock_code": "065150"})

    async def info(cc):
        return {"acc_mt": "12", "induty_code": "582"}, None

    async def audit(*a, **k):
        return {"opinions": []}, [], []
    monkeypatch.setattr(FM, "resolve_company_query", resolve)
    monkeypatch.setattr(FM, "_safe_company_info", info)
    monkeypatch.setattr(FM, "_build_audit_opinion_data", audit)
    monkeypatch.setattr(FM, "_FM_CACHE", type(FM._FM_CACHE)(16 * 1024 * 1024, 300.0, "test-fm-xr"))
    p = asyncio.run(FM.build_financial_metrics_payload("065150", scope="yoy", year=2024))
    d = p["data"]["yoy"]
    assert d["prior"]["total_assets_krw"] == 56_717_158_975                # FY2023 ÷1,000
    assert d["current"]["net_income_yoy_pct"] == pytest.approx(-107.21, abs=0.005)
    assert d["prior"]["data_quality"]["scale_fix"][0]["divisor"] == 1000
    assert len([w for w in p["warnings"] if w.startswith("🔧")]) == 1
    acnt = [c for c in calls if c[0] == "acnt"]
    assert sorted(acnt) == [("acnt", yy, "11011") for yy in (2022, 2023, 2024)]    # 겹치는 보고서(FY2023)를 한 번만


# ═══ 4. 대는 범위 — 사업보고서만 · 날짜로 짝 · 자산총계로도 ════════════════════════════════════════════════
def _bs_rows(year: int, dt: str, th: str, frm: str, frm_dt: str, nm: str = "자본총계") -> list[dict]:
    return [{"rcept_no": f"{year}0000", "sj_div": "BS", "account_nm": nm, "thstrm_dt": dt, "thstrm_amount": th,
             "frmtrm_dt": frm_dt, "frmtrm_amount": frm, "currency": "KRW"}]


def test_reports_are_paired_by_the_date_they_state_not_by_label():
    """같은 날짜를 적은 칸끼리만 짝이다 — 사업연도 표기가 같아도 다른 날짜(900,000 @2022.12.31 vs 1,000 @2022.06.30)를 대지 않는다."""
    a = _bs_rows(2023, "2023.12.31 현재", "1,000,000", "900,000", "2022.12.31 현재")
    b = _bs_rows(2022, "2022.06.30 현재", "1,000", "800", "2021.06.30 현재")
    r = FM._reconcile_annual_scales({2023: a, 2022: b})
    assert r["status"] == "clean", "날짜가 다르면 같은 숫자가 아니다"
    # 같은 날짜를 적으면 짝이 된다
    b2 = _bs_rows(2022, "2022.12.31 현재", "1,000", "800", "2021.12.31 현재")
    assert FM._reconcile_annual_scales({2023: a, 2022: b2})["status"] == "undecided"          # 둘뿐 — 어긋남은 보이나 가리지 못한다


def test_total_assets_alone_can_pair_the_reports():
    """자본총계 줄이 없거나 0 인 보고서도 자산총계로 짝이 된다 — 한 보고서가 1,000배면 다수결로 가린다."""
    rows = {2022: _bs_rows(2022, "2022.12.31 현재", "1,100,000", "1,000,000", "2021.12.31 현재", "자산총계"),
            2023: _bs_rows(2023, "2023.12.31 현재", "1,300,000,000", "1,100,000,000", "2022.12.31 현재", "자산총계"),
            2024: _bs_rows(2024, "2024.12.31 현재", "1,500,000", "1,300,000", "2023.12.31 현재", "자산총계"),
            2025: _bs_rows(2025, "2025.12.31 현재", "1,700,000", "1,500,000", "2024.12.31 현재", "자산총계")}
    r = FM._reconcile_annual_scales(rows)
    assert r["status"] == "fixed" and r["divisors"] == {2023: 1000} and r["pairs"][0]["line"] == "자산총계"


def test_quarterly_substituted_year_is_not_compared(monkeypatch):
    """사업보고서가 없어 3분기 보고서로 읽는 해는 대지 않는다 — 3분기 보고서의 전기 칸은 사업연도 말이라 전년 3분기 보고서 당기 칸(9월 말)과 같은 날짜가 아니다.
    날짜 칸이 없는 응답에서도(연도 표기로 짝지을 때) 서로 다른 시점(12월 말 1,000,000 · 9월 말 1,000)을 같은 숫자로 대면 안 된다."""
    def q3(year: int, th: str, frm: str) -> list[dict]:
        return [{"rcept_no": f"{year}Q3", "fs_div": "CFS", "sj_div": "BS", "account_nm": "자본총계", "thstrm_amount": th, "frmtrm_amount": frm, "currency": "KRW",
                 "ord": "1"}]

    async def acnt(cc, year, rc, fs):
        if rc == "11014" and year in (2025, 2024):
            return (q3(2025, "1,200,000", "1,000,000") if year == 2025 else q3(2024, "1,000", "900"), None)
        return [], "no_filing"

    async def none(*a, **k):
        return None

    async def no_all(cc, year, rc, fs):
        return [], "no_filing"
    monkeypatch.setattr(FM, "_safe_fetch_acnt", acnt)
    monkeypatch.setattr(FM, "_safe_fetch_acnt_all", no_all)
    monkeypatch.setattr(FM, "_periodic_filing_ref", none)
    monkeypatch.setattr(FM, "_accrual_payout_pct", none)
    m, ws, _ = asyncio.run(FM._fetch_year_metrics("00000000", 2025, "CFS"))
    assert m["reprt_code"] == "11014"
    assert "data_quality" not in m and _scale_ws(ws) == []
