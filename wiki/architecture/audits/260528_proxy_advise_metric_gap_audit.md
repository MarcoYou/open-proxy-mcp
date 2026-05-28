---
title: 260528 Proxy Advise Metric Gap Audit
type: audit
status: implemented_p0_partial
updated: 2026-05-28
owner: codex
tags:
  - proxy_advise
  - metrics
  - thresholds
  - review
related_notes:
  - wiki/tools/proxy_advise_before_meeting
  - wiki/architecture/audits/260510_proxy_advise_audit_통합정리
  - wiki/architecture/audits/260525_1620_audit_agenda-parser-marketwide
---

# 260528 proxy_advise 지표 gap 전수조사

## 목적

`proxy_advise_before_meeting`이 "법적으로 명확히 안 되는 것은 AGAINST, 정책 판단·애매한 위험은 REVIEW" 원칙을 따르도록 바뀐 이후, 다음 개선 후보를 전수로 점검했다.

- 안건별로 더 필요한 지표
- 현재 임계값의 범위 세분화 필요 지점
- 이미 호출 중이라 추가 비용이 거의 없는 기준 데이터
- 조건부 호출로만 붙여야 하는 고비용 후보
- 구현 우선순위

본 문서는 gap inventory이자 P0 evidence 구조화 적용 기록이다. 새 판단 룰을 늘리는 것이 아니라 이미 수집 중인 데이터를 `facts`와 `risk_factors`에 더 명확히 노출하는 방향으로 적용했다.

## 적용 요약

| 영역 | 적용 내용 | decision 영향 |
|---|---|---|
| 후보 선임 | 후보별 결격, 독립성 세부 사유, 겸직 구간, 추천사유/직무계획 raw, 사내이사 성과 요약을 `candidate_review_profile`로 구조화 | 기존 AGAINST/REVIEW/FOR 기준 유지 |
| 이사 보수한도 | 인상률 구간, 소진율 구간, 1인당 이사 보수한도, 순이익 yoy를 facts로 노출 | 기존 보수 판단 기준 유지 |
| 감사 보수한도 | 감사 1인당 보수 구간, 감사 보수 인상률 구간을 facts/risk로 노출 | 기존 보수 판단 기준 유지 |
| 퇴직금/퇴임위로금 | before/after 배수, 증가율, 지급 대상 확장 여부를 구조화 | 기존 REVIEW 중심 판단 유지 |
| 재무제표/배당 | 자본잠식률, CFO/OP, accruals gap, 이자보상배율, FCF, FCF 대비 배당을 facts/risk로 노출 | 기존 REVIEW 중심 판단 유지 |
| 자사주 | ownership summary의 자사주 비율, 특관인 지분율, active signal count를 안건 facts로 노출 | 기존 판단 기준 유지 |

검증 범위:

- `tests/test_proxy_advise_timings.py`: candidate profile, 보수 구간, 퇴직금 배수, 재무/배당/자사주 evidence 테스트 추가
- `tests/test_shareholder_meeting_parser_edges.py`: 기존 주총 parser edge 회귀 확인

## 현재 decision surface

현재 `proxy_advise_before_meeting`은 기본 호출에서 다음 upstream을 병렬로 부른다.

| upstream | 현재 활용 | gap |
|---|---|---|
| `shareholder_meeting_notice.summary/agenda/compensation/aoi_change` | 안건, 보수한도, 정관변경, raw amendment, relation metadata | 대부분 활용 중 |
| `financial_metrics.summary` | 자본잠식, 순이익, 순이익 yoy, 배당성향, 감사의견 | 이미 있는 회계 risk alerts 일부 미활용 |
| `director_evaluation` | 결격, 독립성, 장기연임, 겸직, 후보 raw | 후보 전문성/추천사유는 raw 노출 위주, 정량화 미흡 |
| `ownership_structure.control_map` | 최종 응답 `ownership_summary`에만 노출 | 안건별 risk/facts에 거의 미반영 |
| `corp_gov_report.summary` | 최종 응답 `governance_summary`에만 노출 | 안건별 risk/facts에 거의 미반영 |
| `agm_first_agenda_fy` | 1호 재무제표 raw 추출 | 재무제표 안건 facts에 반영 중 |

핵심 gap은 `ownership_structure`와 `corp_gov_report`가 이미 호출되는데도 안건별 decision에는 거의 쓰이지 않는다는 점이다.

## 안건별 개선 후보

### 1. 이사/감사/감사위원 선임

현재 기준:

- 결격사유 red flag -> AGAINST
- 감사/audit 장기연임 5년 룰 -> AGAINST
- 일반 사외이사 장기연임 -> REVIEW
- 독립성 우려 -> REVIEW
- 사내이사 성과 `bad`/`weak` -> REVIEW

추가 지표 후보:

| 후보 | source | 비용 | 권장 | 설명 |
|---|---|---:|---|---|
| 이사회/감사위원회 독립성 현황 | `corp_gov_report.summary` 또는 `metrics_summary` | 추가 API 0 | P0 | 이미 호출 중인 지배구조보고서에서 독립이사/감사위원회 지표를 후보 선임 안건 risk에 붙인다. |
| 최대주주 지배력 구간 | `ownership_structure.control_map` | 추가 API 0 | P0 | 최대주주+특수관계인 지분이 30%, 50% 이상인지 후보 선임 안건 facts에 붙인다. |
| active 5% signal 존재 | `ownership_structure.summary.active_signal_count` | 추가 API 0 | P0 | 경영참여/일반투자 등 active signal이 있으면 이사 선임 안건 REVIEW 보조 risk로 붙인다. |
| 사외이사 겸직 2/3개 구간 세분화 | `director_evaluation` | 추가 API 0 | P1 | 현재 facts에 노출되지만 decision 반영은 약하다. 2개는 weak REVIEW risk, 3개 이상은 strong REVIEW risk로 명시한다. |
| 후보 추천사유/직무계획 NLP 분류 | `director_evaluation` raw | CPU/LLM 비용 | P2 | LLM 또는 rule classifier가 필요하다. 지금은 raw 노출 유지가 안전하다. |

권장 범위:

- 최대주주+특관인 `>=50%`: 지배력 강함 fact
- `30~50%`: 지배력 유의 fact
- `<30%`: 분쟁/연합 가능성 상대적으로 높음 fact
- active signal count `>=1`: 선임 안건 risk factor
- 사외이사 겸직 `2개`: REVIEW risk 약
- 사외이사 겸직 `>=3개`: REVIEW risk 강

### 2. 이사 보수한도

현재 기준:

- 소진율 `<30%`
- 인상률 `30~50%`, `>=50%`
- 순이익 yoy `<0`, `<5`
- 자본잠식

추가 지표 후보:

| 후보 | source | 비용 | 권장 | 설명 |
|---|---|---:|---|---|
| 1인당 이사 보수한도 | `shareholder_meeting_notice.compensation` | 추가 API 0 | P0 | 현재 감사 보수에는 1인당 기준이 있으나 이사 보수에는 없다. 총한도/이사 수로 산출 가능하다. |
| 소진율 구간 세분화 | 기존 compensation | 추가 API 0 | P0 | `<30`, `30~70`, `70~100`, `>=100`으로 facts/risk에 명확히 노출한다. |
| 이익/FCF 대비 보수한도 | `financial_metrics.summary` + compensation | 추가 API 0 | P1 | 한도 규모가 순이익/FCF 대비 과도한지 REVIEW 보조 지표로 둔다. |
| 지배구조보고서 보수정책 지표 | `corp_gov_report.summary` | 추가 API 0 | P1 | 보수정책 투명성/위원회 관련 지표가 있으면 risk에 반영한다. |

권장 범위:

- 소진율 `<30%`: 낮은 사용률
- `30~70%`: 중간 사용률
- `70~100%`: 정상 사용률
- `>=100%`: 한도 부족 정당화 가능
- 인상률 `<=10%`: 소폭
- `10~30%`: 중간
- `30~50%`: 큰 폭
- `>=50%`: 대폭

### 3. 감사 보수한도

현재 기준:

- 감사 1인당 평균 `<5천만원`
- `5천만원~1억원`
- `>=1억원`
- 인상률 `30~50%`, `>=50%`

추가 지표 후보:

| 후보 | source | 비용 | 권장 | 설명 |
|---|---|---:|---|---|
| 감사 수 미확인 flag | compensation parser | 추가 API 0 | P0 | 1인당 평균 산출 불가 시 데이터 부족 REVIEW 사유를 더 명확히 한다. |
| 감사위원회 설치/독립성 지표 | `corp_gov_report.summary` | 추가 API 0 | P1 | 감사 보수 판단에 감사기구 품질을 보조 fact로 붙인다. |
| 자산 규모 대비 감사 보수 | `financial_metrics.summary.total_assets_krw` | 추가 API 0 | P2 | 업종/규모 보정 없이는 오판 가능성이 있어 바로 decision 반영은 보류한다. |

권장 범위:

- 1인당 `<5천만원`: 과소 가능성 REVIEW
- `5천만원~1억원`: 경계 REVIEW
- `>=1억원`: 기본적으로 충분
- 인상률 `30~50%`: REVIEW
- `>=50%`: 강한 REVIEW

### 4. 퇴직금/퇴임위로금

현재 기준:

- 황금낙하산/경영권 변동
- 사외이사 퇴직금 신설
- 지급률 2배수 이상 증가 또는 3배수 이상 신설
- 신설 조항

추가 지표 후보:

| 후보 | source | 비용 | 권장 | 설명 |
|---|---|---:|---|---|
| 지급률 변화율 수치화 | retirement amendments raw | 추가 API 0 | P0 | 현재 boolean signal 중심이다. before/after 배수를 facts에 구조화한다. |
| 대상 확장 여부 | retirement amendments raw | 추가 API 0 | P0 | `비등기임원`, `고문`, `상담역`, `사외이사` 등 대상 확장을 별도 flag로 분리한다. |
| 사유가 법령/퇴직연금/용어정비인지 분리 | retirement amendments raw | 추가 API 0 | P1 | 현재 일부 처리됨. facts에 formal_change=true를 명시하면 LLM 오판 방지 가능. |

권장 범위:

- before/after 배수 모두 있으면 `after / before >= 2.0`: 강한 REVIEW
- before 없음 + after `>=3.0배수`: 강한 REVIEW
- 대상 확장: REVIEW
- 법령/용어/퇴직연금 정비만 있고 위험 키워드 없음: FOR 유지

### 5. 배당

현재 기준:

- 완전 자본잠식 -> REVIEW
- 적자 -> REVIEW
- 배당성향 `>200%` -> REVIEW
- 리츠는 FOR

추가 지표 후보:

| 후보 | source | 비용 | 권장 | 설명 |
|---|---|---:|---|---|
| FCF 대비 배당 | `financial_metrics.summary.dividend_to_fcf_pct` | 추가 API 0 | P0 | 이미 계산됨. 배당 재원 적정성에 순이익보다 직접적이다. |
| FCF 음수 여부 | `financial_metrics.summary.fcf_krw` | 추가 API 0 | P0 | 적자 아니어도 현금흐름이 음수이면 REVIEW risk. |
| 배당 중단/급감 | `financial_metrics.alerts` 또는 dividend history | summary는 0, history는 조건부 | P1 | financial_metrics alert에 dividend_halt가 있으나 proxy_advise에는 미반영. |

권장 범위:

- 배당성향 `0~80%`: 일반
- `80~150%`: 높음, fact 노출
- `150~200%`: 경계 REVIEW 후보
- `>200%`: REVIEW
- dividend_to_fcf `>100%` 또는 FCF `<0`: REVIEW risk

### 6. 재무제표 승인

현재 기준:

- 완전 자본잠식 -> AGAINST
- 비적정 감사의견 -> AGAINST
- 그 외 FOR 또는 NO_DATA

추가 지표 후보:

| 후보 | source | 비용 | 권장 | 설명 |
|---|---|---:|---|---|
| capital_impairment_50plus | `financial_metrics.summary` | 추가 API 0 | P0 | 완전 자본잠식 전 단계로 REVIEW flag 필요. |
| financial risk alerts | `financial_metrics` alert logic | 추가 API 0 | P1 | debt_surge, cfo_quality_red, accruals_red 등은 재무제표 안건 risk_factors에 붙일 수 있다. |
| 잠정 FY raw vs DART 확정치 차이 | agm_first_agenda_fy + financial_summary | 추가 API 0 | P2 | 회계연도 차이 때문에 오탐 위험이 있어 신중히 설계해야 한다. |

권장 범위:

- `capital_impairment_status == partial_50plus`: REVIEW
- `partial`: risk factor
- `cfo_to_op_ratio <0.7`, `accruals_gap_pct abs >30`, `interest_coverage_ratio <2`: risk factor

### 7. 정관변경

현재 기준:

- 법령 layer A2 -> AGAINST
- B1/B2 -> REVIEW
- fallback 위험 키워드 -> REVIEW
- 위험 신호 없으면 FOR

추가 지표 후보:

| 후보 | source | 비용 | 권장 | 설명 |
|---|---|---:|---|---|
| amendment별 category 태깅 | aoi amendments raw | 추가 API 0 | P0 | 이사/감사/배당/신주/자사주/전자주총/소집절차 등으로 facts에 붙인다. |
| 자산 규모 적용 근거 노출 | financial_summary.total_assets_krw | 추가 API 0 | P0 | A2/B1/B2 판단이 자산 기준에 걸리는지 사용자에게 보여준다. |
| 미매핑 amendment count | aoi amendments raw | 추가 API 0 | P1 | fallback miss 시 raw 첨부 외에 count/labels를 facts에 넣는다. |

### 8. 자사주 안건

현재 기준:

- 소각 -> FOR
- 처분 -> REVIEW
- 기타 -> NO_DATA

추가 지표 후보:

| 후보 | source | 비용 | 권장 | 설명 |
|---|---|---:|---|---|
| 현재 자사주 비율 | `ownership_structure.summary.treasury_pct` | 추가 API 0 | P0 | 이미 호출 중이다. 처분/출연/소각 판단에 바로 붙일 수 있다. |
| 최근 자사주 취득/소각 계획 | `treasury_share.fetch_treasury_signal_summary` | 조건부 API | P1 | 자사주 안건이 있을 때만 lightweight 24m summary 호출. |
| 자사주 5% 이상 | ownership summary | 추가 API 0 | P0 | `treasury_pct >=5%`는 REVIEW risk. |

권장 범위:

- treasury_pct `<5%`: 낮음
- `5~10%`: 유의
- `>=10%`: 강한 REVIEW risk

## 공통 개선 후보

### 추가 비용 0 후보

이미 호출 중인 payload에서 바로 가져올 수 있다.

- `ownership_summary.related_total_pct`
- `ownership_summary.treasury_pct`
- `ownership_summary.active_signal_count`
- `governance_summary.report_meta.compliance_rate` 또는 equivalent summary
- `financial_summary.capital_impairment_ratio_pct`
- `financial_summary.fcf_krw`
- `financial_summary.dividend_to_fcf_pct`
- `financial_summary.cfo_to_op_ratio`
- `financial_summary.accruals_gap_pct`
- `financial_summary.interest_coverage_ratio`

### 조건부 호출 후보

항상 호출하면 느려질 수 있으므로 특정 안건에서만 호출한다.

| 조건 | 호출 후보 | 목적 |
|---|---|---|
| 자사주 안건 존재 | `fetch_treasury_signal_summary` 또는 `treasury_share.summary` | 최근 취득/신탁/소각 의도 확인 |
| 배당 안건 + 재무 summary 불충분 | `dividend.history` | 배당 중단/급감/정정 여부 |
| 주주제안/분쟁성 안건 | `proxy_contest` | 위임장/소송/5% active signal 강화 |
| 후보 audit history 명시 요청 | `check_audit_history=True` | 과거 회사 회계 risk overlap |

## 우선순위

### P0: 바로 구현해도 비용/회귀 위험 낮음

- 안건별 facts/risk에 `ownership_summary` 핵심 3개를 반영한다.
- 자사주 안건에 `treasury_pct` 구간을 붙인다.
- 재무제표/배당/보수 안건에 `capital_impairment_ratio_pct`, `partial_50plus`, `fcf_krw`, `dividend_to_fcf_pct`, `cfo_to_op_ratio`, `accruals_gap_pct`를 risk factor로 붙인다.
- 이사 보수에 1인당 보수한도와 소진율 구간 label을 추가한다.
- 퇴직금 지급률 before/after 배수를 facts에 구조화한다.

### P1: 조건부 호출 또는 문구/정책 정교화 필요

- 자사주 안건에서만 lightweight treasury signal summary 호출.
- 배당 안건에서만 dividend history 보강.
- corp_gov_report의 metric label을 proxy_advise category에 맞게 mapping한다.
- 주주제안/분쟁성 안건에서만 proxy_contest signal 보강.

### P2: 보류

- 후보 추천사유/직무계획을 LLM/NLP로 분류해 decision에 반영.
- 업종별 보수/배당 peer percentile.
- 머신러닝 기반 decision prediction.

P2는 정확한 label과 ground truth가 없으면 오히려 policy drift를 만든다. 먼저 rule engine의 facts/risk coverage를 늘리고, 이후 loss function은 threshold calibration에만 쓰는 것이 안전하다.

## 주의해야 할 회귀

- 추가 지표는 우선 `facts`와 `risk_factors`에만 붙이고 `decision` 변경은 최소화한다.
- `AGAINST`는 법령 A2, 결격, 감사/audit 장기연임, 비적정 감사의견 같은 hard trigger에만 유지한다.
- ownership/gov 지표는 회사 단위 신호이므로 개별 안건 반대 근거로 쓰지 말고 REVIEW 보조 근거로만 쓴다.
- 조건부 호출은 안건 category가 확정된 뒤에만 실행해야 한다.
- DART rate limit safety 때문에 기본 upstream 수는 늘리지 않는다.
