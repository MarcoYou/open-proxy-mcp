---
type: tool
title: director_board
domain: data
scope: [단일 조회]
data_source: [exctvSttus(임원현황), drctrAdtAllMendngSttusGmtsckConfmAmount(주총승인 보수한도), drctrAdtAllMendngSttusMendngPymntamtTyCl(유형별 실지급·1인평균), hmvAuditIndvdlBySttus(개인별 5억+), 기업지배구조보고서(출석률·겸직 — v2)]
related_disclosures: [사업보고서, 기업지배구조보고서]
related_concepts: [이사보수, 보수한도, 소진율, 이사회, 사외이사]
related_decisions: []
created: 2026-07-08
---

# director_board — 이사회/개별 이사 프로필

## 한 줄 요약
**개별 이사 단위** 정보 — 이사 인당 보수·보수한도 소진율·임원 재직/사퇴 변동·(v2)이사회 출석률·겸직.
`corp_gov_report`가 "회사 15지표 준수 여부"라면, 이 tool은 "누가 얼마 받고, 한도를 얼마나 썼고,
인원이 어떻게 바뀌었나".

## 왜 만들었나 (사용자 260708)
답하려는 질문 3개:
1. 이사 **인당 보수가 적절한가** → 인당 보수 수치 (DART가 직접 제공)
2. **보수한도 소진율**은 얼마인가 → 이사류 실지급 ÷ 주총 승인한도
3. **사퇴/인원변동으로 인당보수·소진율이 바뀌었나** → 연도간 임원 diff × 보수 변동 교차

가치판단(과다/적절)은 하지 않는다 — 동종·규모 대비 판단이 필요해, 수치·전년비 변동·flag만 제공.

## 사용법
`director_board(company, scope="summary", year=0, lookback_years=3, format="md")`
- scope: `compensation` · `roster` · `attendance`(v2 stub) · `summary`(기본)
- year: 기준 사업연도(0=최근 확정 전년). lookback_years: 조회 기간(년)

## scope별 내용

| scope | 내용 | 소스 | 상태 |
|---|---|---|---|
| `compensation` | 등기이사 인당보수·보수한도·**소진율** (연도별) | 정형 API | ✅ v1 |
| `roster` | 임원현황 + **재직/사퇴 감지**(연도 diff) | exctvSttus | ✅ v1 |
| `individual` | 개인별 **5억+ 실명** 보수 (누가 얼마) | hmvAuditIndvdlBySttus | ✅ v1 |
| `unregistered` | **미등기 집행임원** 인당보수 (등기 밖 경영진) | unrstExctvMendngSttus | ✅ v1 |
| `pay_gap` | 경영진 vs **직원 평균** 보수 배수 | empSttus 조합 | ✅ v1 |
| `pay_agenda` | 주총 보수한도 안건 **올해 제안 vs 작년 실적**(인상률·작년소진율) | shareholder_meeting notice 재사용 | ✅ v1 |
| `attendance` | 개별 이사 출석률·선임변동(표4-2-1)·겸직(표5-2-1) | 지배구조보고서 원문 파서 | ⏳ v2 stub |
| `summary` | 위 전부 종합 + 신호 | 전부 | ✅ v1 |

## 신규 계산 로직

### 소진율
```
소진율(%) = (감사 단독 제외한 이사류 실지급 합) ÷ 이사 주총 승인한도 × 100
```
- 실지급은 유형별 여러 행(등기이사(사외·감사위 제외)/사외이사/감사위원 등). **감사위원회 위원은
  이사 한도 안**(등기이사이므로) — IR 검증(260708)으로 확증: 현대차 한도 12명 = 실지급 버킷
  5+2+5 헤드카운트와 정확히 일치. 순수 '감사'(비위원회)만 별도 한도.
- **한도 공백** — 새 주총 결의 없는 해엔 `gmtsck_confm_amount="-"` → 최근 유효연도 한도로 lookback,
  `limit_source`에 명시.

### 인당 보수
`psn1_avrg_pymntamt`(1인평균)이 API에서 이미 계산되어 옴 — 검증 결과 실지급÷인원과 정합.

### 재직/사퇴 감지
`exctvSttus` 연도간 diff. 동일인 판정은 **OR 매칭**(이름 일치 OR 생년월 일치):
- 로마자 표기 변동(이름 다름·생년월 같음) → 생년월으로 매칭 (예: José Muñoz "Jose Munoz"↔"호세무뇨스")
- 원문 birth_ym 오타(이름 같음·생년월 다름) → 이름으로 매칭 (QA 260708 발견: 기아 신재용
  2023 `1972.12` vs 2024 `1972.02` — 복합키였다면 이탈+신규 이중 오탐)
- 스냅샷이라 이탈 "사유"(사퇴·임기만료·해임)는 미확정 — 별도 수시공시로 확인 필요.

### 경영진-직원 보수 격차 (pay_gap)
```
격차 배수 = 등기이사(사외·감사위 제외) 인당보수 ÷ 직원 전체 가중평균 급여
직원 가중평균 = Σ(부문·성별 행 연급여총액) ÷ Σ(정규+계약 인원)   (합계행 중복 제외)
```
현대차 실측(2024): 등기이사 32.0억 ÷ 직원 1.24억 ≈ **25.8배**. 배수 자체가 과다/적정 판단은 아님
(업종·직군 구성 차이) — 비교 신호로만.

### 보수한도 주총안건 비교 (pay_agenda)
`shareholder_meeting` 소집공고 보수한도 안건은 **하나의 공고에 current(올해 제안)·prior(작년 한도+
실지급)**를 모두 담는다. 이를 재사용해:
```
인상률 = (올해 제안 한도 − 작년 한도) ÷ 작년 한도
작년 소진율 = 작년 실지급 ÷ 작년 한도
```
현대차 실측: 제안 284억 / 작년 237억(인상 +19.8%) / 작년 실지급 236.9억(**작년 소진율 100%**) →
"한도 거의 소진 + 인상 요구 = 인상 근거 있음" 신호. **주의**: 이 '작년 소진율'은 주총공고 prior 컬럼
기준이라, `compensation` scope의 사업보고서 기반 소진율과 연도 기준·집계 정의가 다를 수 있음(둘 다
사실, 출처 다름). 찬반 판단 자체는 하지 않음(→ [[proxy_advise_before_meeting]]).
**주의**: pay_agenda는 `year` 파라미터를 무시하고 항상 **최근 주총 소집공고**를 본다(주총은 연 1회라
'올해 안건' 자체가 최신). 보수한도 안건이 없는 주총(기아 등 그 해 이사선임만)은 `status=no_agenda`로
정상 폴백. empSttus '성별합계'만 총액을 담는 삼성류 양식은 상세행 공백 시 합계행 폴백으로 처리
(QA 260708: `"합계" in se` 부분매칭이 '성별합계'를 버려 삼성 pay_gap이 None이던 버그 수정).

## 알려진 issue + TODO
- **attendance scope 미구현**: 지배구조보고서 원문의 개별 이사 출석률 매트릭스·표4-2-1·표5-2-1은
  실존 확인(스튜어드십 조사 260708)했으나 파서는 v2. **금융지주는 PDF 별도양식**(금융회사
  지배구조법)이라 OCR tier 필요.
- **birth_ym 결측 시**: 이름 단독 매칭으로 fallback → 로마자 오탐 재발 가능(관측 표본 결측 0%였으나
  보장 안 됨). 결측 시 `hffc_pd`(재직시작일) 보조키 검토 — TODO.
- **개인별 보수 5억 미만 비공개**: `hmvAuditIndvdlBySttus`는 상위 일부만(범주 평균은 전원).
- **지연 제출 rcept**: 통상 3월 기한보다 늦은 rcept_no(예 미래에셋 2024=8월)는 정정공시 가능성 —
  버킷 내부 정합 확인 권장.

## Data sources (회사당 ~5 DART 콜, per-firm)
| 소스 | 무엇 |
|---|---|
| `exctvSttus` | 성명·직위·이사구분·상근·담당업무·재직기간·임기만료 |
| `drctrAdtAllMendngSttusGmtsckConfmAmount` | 주총 승인 보수한도 + 인원 |
| `drctrAdtAllMendngSttusMendngPymntamtTyCl` | 유형별 실지급 총액·인원·1인평균 |
| `hmvAuditIndvdlBySttus` | 개인별 보수(5억+) |
| `unrstExctvMendngSttus` | 미등기 집행임원 인원·연급여총액·1인평균 |
| `empSttus` | 직원 부문·성별 인원·평균급여 (pay_gap 분모) |
| shareholder_meeting notice (재사용) | 보수한도 주총안건 current/prior (pay_agenda) |
| 기업지배구조보고서 원문 | 출석률·선임변동·겸직 (v2) |

## 관련
- [[corp_gov_report]] — 회사 지배구조 15지표 준수(정성). 이 tool은 개별 이사 정량.
- [[director_evaluation]] — 이사 후보 독립성·결격(주총 안건). 이 tool은 재직 중 보수·재직변동.
- [[shareholder_meeting_notice]] — 보수한도 '안건'. 이 tool은 실제 지급·소진율.
