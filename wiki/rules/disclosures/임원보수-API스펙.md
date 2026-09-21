---
type: reference
title: 임원보수-API스펙
tags: [dart-api, director, compensation, exctvSttus, mendngSttus, empSttus]
source: DART OpenAPI 개발가이드 (opendart.fss.or.kr/guide, DS002 정기보고서 주요정보)
related: [사업보고서, 공시유형코드체계]
purpose: 임원·보수 정형 API 구축·검증용 — 필드명 추론이 아닌 DART 공식 문서 기반 정확한 스펙
updated: 2026-09-02
---

# 임원·보수 관련 API 스펙 (DS002 정기보고서 주요정보)

> 임원·보수 분석이 쓰는 6개 정형 API의 **공식 필드명 사전**. 영문 Element ↔ 한글 항목명
> 대응은 DART 개발가이드(opendart.fss.or.kr/guide, DS002)가 정본이다.

관련: [[사업보고서]] · [[공시유형코드체계]]

---

## 1. 임원 현황 — `exctvSttus.json` (apiId 2019010)

구현: `open_proxy_mcp/dart/client.py`의 `get_executive_status`.

| 영문 Element | 한글 항목명 |
|---|---|
| rcept_no | 접수번호 |
| corp_cls | 법인구분 |
| corp_code | 고유번호 |
| corp_name | 법인명 |
| nm | 성명 |
| sexdstn | 성별 |
| birth_ym | 출생 년월 |
| ofcps | 직위 |
| rgist_exctv_at | 등기 임원 여부 |
| fte_at | 상근 여부 |
| chrg_job | 담당 업무 |
| main_career | 주요 경력 |
| mxmm_shrholdr_relate | 최대 주주 관계 |
| hffc_pd | 재직 기간 |
| tenure_end_on | 임기 만료 일 |
| stlm_dt | 결산기준일 |

**주의**: `rgist_exctv_at`(등기 임원 여부)의 실제 값은 "등기"/"미등기"가 아니라
**이사 구분**(사내이사·사외이사·기타비상무이사·감사)으로 온다 — 필드명과 실제 값의 의미가
다르다.

## 2. 이사·감사 전체 보수현황 — 주주총회 승인금액 `drctrAdtAllMendngSttusGmtsckConfmAmount.json` (apiId 2020014)

구현: `get_director_pay_limit`.

| 영문 Element | 한글 항목명 |
|---|---|
| rcept_no | 접수번호 |
| corp_cls | 법인구분 |
| corp_code | 고유번호 |
| corp_name | 회사명 |
| se | 구분 |
| nmpr | 인원수 |
| gmtsck_confm_amount | 주주총회 승인금액 |
| rm | 비고 |
| stlm_dt | 결산기준일 |
| fscl_year | 사업연도 |

**주의**: `se`(구분)는 회사마다 자유 텍스트다. 상임/비상임·등기/사외로 **행을 나눠 공시**하는
회사(한국전력·기업은행·강원랜드)가 있어 한 회사의 승인 한도가 여러 행에 흩어진다 — 한 행만
읽으면 한도가 축소된다. "감사위원회 위원 또는 감사"처럼 이사와 감사를 한 칸에 묶은 표기도 있다.

## 3. 이사·감사 전체 보수현황 — 유형별 지급금액 `drctrAdtAllMendngSttusMendngPymntamtTyCl.json` (apiId 2020015)

구현: `get_director_pay_actual`.

| 영문 Element | 한글 항목명 |
|---|---|
| rcept_no | 접수번호 |
| corp_cls | 법인구분 |
| corp_code | 고유번호 |
| corp_name | 회사명 |
| se | 구분 |
| nmpr | 인원수 |
| pymnt_totamt | 보수총액 |
| psn1_avrg_pymntamt | 1인당 평균보수액 |
| rm | 비고 |
| stlm_dt | 결산기준일 |
| fscl_year | 사업연도 |
| stk_bsd_pd_mendng_totamt | 보수총액 중 주식기준보상 지급액 |
| stk_opt_exrcsbl_qty | 주식매수선택권 행사가능수량 |
| stk_opt_unexrcsbl_qty | 주식매수선택권 행사불가수량 |
| stk_opt_rmn_blce | 주식매수선택권 잔여금액 |
| othr_stk_bsd_cmpn_unpyd_qty | 그 외 주식기준 보상 미지급수량 |
| othr_stk_bsd_cmpn_mkt_vl | 그 외 주식기준 보상 시장가치 |

`psn1_avrg_pymntamt`(1인평균)는 API가 이미 계산해 제공하며 `pymnt_totamt÷nmpr`와 일치한다.
스톡옵션 관련 필드(`stk_opt_*`)는 값이 채워지지 않고 공백으로 오는 회사가 많아, 보수총액에
행사이익이 섞였는지를 이 필드들로 확인할 수는 없다.

## 4. 이사·감사 개인별 보수현황 (5억원 이상) `hmvAuditIndvdlBySttus.json` (apiId 2019012)

구현: `get_individual_pay`. (Ver 2.0인 `apiId 2026001`은 아직 미사용)

| 영문 Element | 한글 항목명 |
|---|---|
| rcept_no | 접수번호 |
| corp_cls | 법인구분 |
| corp_code | 고유번호 |
| corp_name | 법인명 |
| nm | 이름 |
| ofcps | 직위 |
| mendng_totamt | 보수 총액 |
| mendng_totamt_ct_incls_mendng | (보수총액 중 산정기준 비포함 보수 — 성과보수 이연지급 등 비고성 breakdown, DART 원문 자유텍스트) |
| stlm_dt | 결산기준일 |

5억원 미만은 법정 비공개(개별 미공시, 유형별 평균만 3번 API로 확인 가능).

## 5. 미등기임원 보수현황 `unrstExctvMendngSttus.json` (apiId 2020013)

구현: `get_unregistered_pay`.

| 영문 Element | 한글 항목명 |
|---|---|
| rcept_no | 접수번호 |
| corp_cls | 법인구분 |
| corp_code | 고유번호 |
| corp_name | 회사명 |
| se | 구분 |
| nmpr | 인원수 |
| fyer_salary_totamt | 연간급여 총액 |
| jan_salary_am | 1인평균 급여액 |
| rm | 비고 |
| stlm_dt | 결산기준일 |

## 6. 직원 현황 `empSttus.json` (apiId 2019011)

구현: `get_employee_status`.

| 영문 Element | 한글 항목명 |
|---|---|
| rcept_no | 접수번호 |
| corp_cls | 법인구분 |
| corp_code | 고유번호 |
| corp_name | 법인명 |
| fo_bbm | 사업부문 |
| sexdstn | 성별 |
| reform_bfe_emp_co_rgllbr | 개정 전 직원 수 정규직 |
| reform_bfe_emp_co_cnttk | 개정 전 직원 수 계약직 |
| reform_bfe_emp_co_etc | 개정 전 직원 수 기타 |
| rgllbr_co | 정규직 수 |
| rgllbr_abacpt_labrr_co | 정규직 단시간 근로자 수 |
| cnttk_co | 계약직 수 |
| cnttk_abacpt_labrr_co | 계약직 단시간 근로자 수 |
| **sm** | **합계** |
| avrg_cnwk_sdytrn | 평균 근속 연수 |
| fyer_salary_totamt | 연간 급여 총액 |
| jan_salary_am | 1인평균 급여 액 |
| rm | 비고 |
| stlm_dt | 결산기준일 |

**`sm`은 그 행의 인원 합계**다 (현대차: 남 정규직 58,225 + 계약직 8,456 = 66,681 = `sm`).
정규직/계약직 개별 필드는 원문 자체에 오타가 실리기도 하지만(클로봇: `rgllbr_co`가 981로 오기재)
`sm`은 DART가 따로 계산해 넣어 다른 행들과 내부 정합이 유지된다. 단 `sm` 자체가 없는 회사도 있다.

**부문별 응답 구조는 2갈래다.** 대부분은 부문·성별 상세행에 급여가 채워진다(현대차 남/여).
삼성전자류(부문별+성별합계 양식)는 부문 상세행(DX/DS 등)이 공백("-")이고 **'성별합계' 행에만
실제 총액**이 온다 — 상세행이 전부 공백인 회사가 있다는 뜻이다.

## 7. 사외이사 및 그 변동현황 `outcmpnyDrctrNdChangeSttus.json` (apiId 2020012)

구현: `open_proxy_mcp/dart/client.py`의 `get_outside_director_changes`.

| 영문 Element | 한글 항목명 |
|---|---|
| rcept_no | 접수번호 |
| corp_cls | 법인구분 |
| corp_code | 고유번호 |
| corp_name | 회사명 |
| drctr_co | 이사의 수 |
| otcmp_drctr_co | 사외이사 수 |
| apnt | 사외이사 변동현황(선임) |
| rlsofc | 사외이사 변동현황(해임) |
| mdstrm_resig | 사외이사 변동현황(중도퇴임) |
| stlm_dt | 결산기준일 |

**개별 성명 없이 회사 전체 집계**만 준다. 이 집계는 **사외이사만·선임/해임/중도퇴임 건수**라,
임원 명부를 연도끼리 비교해 얻은 변동과는 모집단이 다르다 — 명부 비교에는 미등기 임원까지 섞이고,
재선임은 명부상 "변동 없음"이지만 공식 집계는 선임으로 셀 수 있다.
