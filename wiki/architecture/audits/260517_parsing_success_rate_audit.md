---
title: 260517 Parsing Success-Rate Audit
type: audit
status: final
updated: 2026-05-17
owner: codex
tags:
  - parsing
  - audit
  - success-rate
related_notes:
  - wiki/architecture/parsing_success_rate_audit_spec
  - wiki/architecture/parsing_success_rate_audit_checklist
---

# 260517 key data tools parsing 성공률 감사

## 목적

회사 표본 기반 key data tools와 별도 공시 표본 기반 `shareholder_meeting_notice`에 대해 다음을 검증한다.

- 성공률(`success`)
- 보조 실패율(`soft fail`)
- 치명 실패율(`hard fail`)
- latency 분포
- parser family별 실패 cluster
- regression-safe 수정 전후 차이

본 감사의 source of truth는 다음 두 문서다.

- [[parsing_success_rate_audit_spec]]
- [[parsing_success_rate_audit_checklist]]

## 감사 범위

### 회사 표본 기반 tool

- `company`
- `shareholder_meeting_results`
- `ownership_structure`
- `financial_metrics`
- `corp_gov_report`
- `dividend`
- `treasury_share`
- `value_up`
- `corporate_restructuring`
- `dilutive_issuance`
- `proxy_contest`
- `related_party_transaction`

### 별도 공시 표본 기반 tool

- `shareholder_meeting_notice`

## 표본 정의

### 회사 표본 baseline

- `KOSPI 300`
- `KOSDAQ 150`
- 합계 `450개`

근거 파일:

- [universe_kospi300.csv](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/audits/data/260517_parsing_success_rate_audit/universe_kospi300.csv:1)
- [universe_kosdaq150.csv](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/audits/data/260517_parsing_success_rate_audit/universe_kosdaq150.csv:1)

### 회사 표본 비중복 재검증

- `KOSPI 50`
- `KOSDAQ 50`
- 합계 `100개`

근거 파일:

- [universe_kospi50_additional_nonoverlap.csv](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/audits/data/260517_parsing_success_rate_audit/universe_kospi50_additional_nonoverlap.csv:1)
- [universe_kosdaq50_additional_nonoverlap.csv](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/audits/data/260517_parsing_success_rate_audit/universe_kosdaq50_additional_nonoverlap.csv:1)

### `shareholder_meeting_notice` 공시 표본

- `2026년 정기 주총 notice 전수`
- `2026-03-31 이후 현재까지의 임시 주총 notice 전수`

근거 파일:

- [shareholder_meeting_notice_annual_2026.csv](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/audits/data/260517_parsing_success_rate_audit/shareholder_meeting_notice_annual_2026.csv:1)
- [shareholder_meeting_notice_extraordinary_since_20260331.csv](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/audits/data/260517_parsing_success_rate_audit/shareholder_meeting_notice_extraordinary_since_20260331.csv:1)

## 실행 산출물

### 회사 표본 baseline

- [baseline_company_sample_450.json](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/audits/data/260517_parsing_success_rate_audit/baseline_company_sample_450.json:1)
- [baseline_company_sample_450_summary.json](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/audits/data/260517_parsing_success_rate_audit/baseline_company_sample_450_summary.json:1)

### 회사 표본 비중복 재검증

- [recheck100_company_meeting_results_proxy_contest.json](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/audits/data/260517_parsing_success_rate_audit/recheck100_company_meeting_results_proxy_contest.json:1)
- [recheck100_company_meeting_results_proxy_contest_after_alias_retry.json](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/audits/data/260517_parsing_success_rate_audit/recheck100_company_meeting_results_proxy_contest_after_alias_retry.json:1)

### 공시 표본 감사

- [shareholder_meeting_notice_2026_classified.csv](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/audits/data/260517_parsing_success_rate_audit/shareholder_meeting_notice_2026_classified.csv:1)
- [shareholder_meeting_notice_annual_2026.csv](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/audits/data/260517_parsing_success_rate_audit/shareholder_meeting_notice_annual_2026.csv:1)
- [shareholder_meeting_notice_extraordinary_since_20260331.csv](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/audits/data/260517_parsing_success_rate_audit/shareholder_meeting_notice_extraordinary_since_20260331.csv:1)
- [shareholder_meeting_notice_annual_2026_audit.json](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/audits/data/260517_parsing_success_rate_audit/shareholder_meeting_notice_annual_2026_audit.json:1)
- [shareholder_meeting_notice_extraordinary_since_20260331_audit.json](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/audits/data/260517_parsing_success_rate_audit/shareholder_meeting_notice_extraordinary_since_20260331_audit.json:1)

## 최종 요약

회사 표본 baseline `450개` 기준으로, 핵심 company-sample tool `12개` 중 `9개`는 `strict_success_rate 100%`로 수렴했다. baseline에서 의미 있는 soft/hard fail은 `shareholder_meeting_results`, `value_up`, `proxy_contest` 3개 tool에만 집중됐다.

가장 큰 soft fail cluster는 `shareholder_meeting_results`였다.

- baseline `strict_success_rate 66.2%`
- `requires_review 152건`
- 그러나 `usable_rate 100%`, `hard_fail_rate 0%`
- KOSPI `300개`에서는 `strict_success_rate 98.3%`
- KOSDAQ `150개`에서는 `requires_review 147건`으로 사실상 “낮은 신뢰도의 자동 결과 매핑 억제” 성격이었다

두 번째 soft fail cluster는 `value_up`였다.

- baseline `strict_success_rate 92.9%`
- `partial 32건`
- 전부 `hard_fail`이 아니라 “요청 연도에는 공시가 없지만 진단 구간에는 공시가 존재”하는 `exists_outside_requested_window` 계열로 확인됐다
- `usable_rate 100%`

유일한 baseline hard fail cluster는 `proxy_contest`의 `ReadError 4건`과 비중복 재검증에서 드러난 alias 부족이었다. 이 둘은 regression-safe 수정으로 정리됐다.

- audit runner에 transient retry 추가
- corp alias 5건 추가
- 비중복 `100개` 재검증 결과
  - `company 100/100 success`
  - `proxy_contest 100/100 success`
  - `shareholder_meeting_results`는 hard fail 제거, KOSPI `50/50 success`, KOSDAQ는 `48 requires_review + 2 success(no_filing)`

즉 현 시점 결론은 다음과 같다.

- 식별 계열(`company`)과 대부분의 구조화 parser는 안정적이다
- `proxy_contest`의 hard fail은 parser bug라기보다 retry/alias 부족이었고 수정 후 해소됐다
- `shareholder_meeting_results`와 `value_up`의 낮은 strict success는 치명 실패가 아니라 보수적 판정 설계에 가깝다
- `shareholder_meeting_notice` 별도 공시 표본 감사도 정기/임시 전수 기준으로 완료됐다

## 회사 표본 baseline 결과

### 도구별 요약

| tool | strict_success_rate | usable_rate | hard_fail_rate | status 요약 | median ms | p95 ms |
|---|---:|---:|---:|---|---:|---:|
| `company` | `100.0%` | `100.0%` | `0.0%` | `exact 450` | `1618.2` | `1666.7` |
| `financial_metrics` | `100.0%` | `100.0%` | `0.0%` | `exact 450` | `1626.8` | `1678.1` |
| `ownership_structure` | `100.0%` | `100.0%` | `0.0%` | `exact 450` | `1397.3` | `2537.8` |
| `corp_gov_report` | `100.0%` | `100.0%` | `0.0%` | `exact 245`, `no_filing 205` | `1605.2` | `3161.6` |
| `dividend` | `100.0%` | `100.0%` | `0.0%` | `exact 333`, `no_filing 117` | `10790.2` | `24105.6` |
| `treasury_share` | `100.0%` | `100.0%` | `0.0%` | `exact 229`, `no_filing 221` | `2681.8` | `5516.7` |
| `corporate_restructuring` | `100.0%` | `100.0%` | `0.0%` | `exact 71`, `no_filing 379` | `1629.7` | `1669.8` |
| `dilutive_issuance` | `100.0%` | `100.0%` | `0.0%` | `exact 115`, `no_filing 335` | `1630.0` | `1677.8` |
| `related_party_transaction` | `100.0%` | `100.0%` | `0.0%` | `exact 281`, `no_filing 169` | `1233.3` | `2038.6` |
| `shareholder_meeting_results` | `66.2%` | `100.0%` | `0.0%` | `exact 292`, `no_filing 6`, `requires_review 152` | `3197.4` | `7490.4` |
| `value_up` | `92.9%` | `100.0%` | `0.0%` | `exact 192`, `no_filing 226`, `partial 32` | `2444.9` | `5103.9` |
| `proxy_contest` | `99.1%` | `99.1%` | `0.9%` | `exact 427`, `no_filing 19`, `exception 4` | `2442.7` | `2713.2` |

### parser family별 요약

`식별 / entity resolution`

- `company` baseline은 `450/450 exact`
- 다만 비중복 `100개` 재검증에서 alias 부족 4건과 ambiguous 1건이 드러나 추가 alias 보강이 필요했다
- alias 보강 후 `company 100/100 success`로 회복

`meeting results`

- baseline에서 가장 큰 soft fail cluster
- KOSPI는 거의 `exact`
- KOSDAQ는 `requires_review` 비중이 매우 높음
- hard fail은 재검증 최종본에서 제거됨

`value-up orchestration`

- `partial`은 parser exception이 아니라 진단형 상태
- 요청 연도 밖 공시 존재를 경고하는 보수적 상태이므로, 의미상 soft fail로 유지하는 것이 맞다

`contest / dispute`

- baseline hard fail 4건은 모두 `ReadError`
- retry + alias 보강 후 비중복 재검증 `100/100 success`

`structured normalization family`

- `financial_metrics`, `ownership_structure`, `corp_gov_report`, `dividend`, `treasury_share`, `corporate_restructuring`, `dilutive_issuance`, `related_party_transaction`은 baseline 기준 stable
- 다만 `dividend`는 latency가 유의미하게 큼

### 주요 failure cluster

1. `shareholder_meeting_results`

- baseline soft fail `152건`
- 대표 경고:
  - `주주총회결과 공시가 whitelist 규칙에 맞지 않아 자동 매핑하지 않았다`
  - `API/XML 파싱이 약해 DART viewer HTML crawl fallback을 시도했다`
- KOSDAQ small/mid-cap에서 집중적으로 발생

2. `value_up`

- baseline soft fail `32건`
- 진단 결과:
  - `status=partial`
  - `no_filing=true`
  - `availability_status=exists_outside_requested_window`
- 즉 “요청 연도에는 없지만 진단 구간에는 있다”는 상태로, 오류라기보다 보수적 정보 노출이다

3. `proxy_contest`

- baseline hard fail `4건`
- 모두 `ReadError`
- 회사:
  - `이오테크닉스`
  - `펄어비스`
  - `로보티즈`
  - `올릭스`
- retry 후 4/4 success로 해소

4. 비중복 재검증 alias cluster

- `삼화콘덴서 -> 삼화콘덴서공업`
- `DI동일 -> 디아이동일`
- `유진투자증권 -> 유진증권`
- `KCC글라스 -> 케이씨씨글라스`

### latency 관찰

가장 느린 tool은 `dividend`였다.

- median `10790.2ms`
- p95 `24105.6ms`

그 다음은:

- `shareholder_meeting_results` median `3197.4ms`
- `treasury_share` median `2681.8ms`
- `value_up` median `2444.9ms`
- `proxy_contest` median `2442.7ms`

즉 “파싱 성공률”과 “처리 속도”는 다른 문제다.

- 성공률은 대부분 높다
- 하지만 `dividend`, `shareholder_meeting_results`, `treasury_share`, `value_up`, `proxy_contest`는 별도 latency 개선 audit 가치가 있다

## 수정 및 재검증

### 적용한 수정

1. 회사 alias 보강

- [client.py](/Users/marcoyou/Projects/open-proxy-mcp/open_proxy_mcp/dart/client.py:1)
- 추가 alias:
  - `삼화콘덴서 -> 삼화콘덴서공업`
  - `DI동일 -> 디아이동일`
  - `유진투자증권 -> 유진증권`
  - `KCC글라스 -> 케이씨씨글라스`

2. audit runner의 `shareholder_meeting_results` 호출 surface 수정

- 기존: `proxy_result_after_meeting` 계열 `build_proxy_result_payload`
- 수정: 실제 결과 parser인 `build_shareholder_meeting_payload(..., scope="results")`
- 효과:
  - tool 정의와 감사 surface 일치
  - 처리시간 대폭 단축
  - `no_filing` vs `requires_review` 분포가 더 정확해짐

3. audit runner의 transient retry 추가

- [parsing_success_rate_audit.py](/Users/marcoyou/Projects/open-proxy-mcp/scripts/parsing_success_rate_audit.py:1)
- 예외 발생 시 exponential backoff 기반 3회 재시도
- baseline `proxy_contest`의 `ReadError` 4건 해소 목적

### baseline 재실행 결과

재실행 결과, 회사 표본 baseline `450개`는 완료됐다.

- [baseline_company_sample_450.json](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/audits/data/260517_parsing_success_rate_audit/baseline_company_sample_450.json:1)
- [baseline_company_sample_450_summary.json](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/audits/data/260517_parsing_success_rate_audit/baseline_company_sample_450_summary.json:1)

baseline 본문 수치는 “수정 후 기준”으로 해석한다. 단, `proxy_contest`의 4건 hard fail은 retry 패치 전 run의 잔존값이므로 아래 targeted rerun과 recheck 결과를 우선한다.

### 비중복 100개 재검증 결과

1차 비중복 재검증에서는 새로운 alias 부족이 드러났다.

- `company`: `96 success`, `1 ambiguous`, `3 error`
- `shareholder_meeting_results`: `48 success`, `49 soft fail`, `3 hard fail`
- `proxy_contest`: `96 success`, `4 hard fail`

alias 보강 + retry patch 후 다시 돌린 최종 비중복 재검증 결과는 다음과 같다.

- [recheck100_company_meeting_results_proxy_contest_after_alias_retry.json](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/audits/data/260517_parsing_success_rate_audit/recheck100_company_meeting_results_proxy_contest_after_alias_retry.json:1)

최종 재검증 요약:

- `company`: `100/100 success`
- `proxy_contest`: `100/100 success`
- `shareholder_meeting_results`:
  - 전체 `52 success`, `48 soft fail`, `0 hard fail`
  - KOSPI `50/50 success`
  - KOSDAQ `48 requires_review + 2 success(no_filing)`

즉 alias/transport 계열 hard fail은 정리됐고, 남은 것은 `shareholder_meeting_results`의 보수적 `requires_review` 정책뿐이다.

## `shareholder_meeting_notice` 공시 표본 결과

### 정기 주총

먼저 2026년 notice를 실제 공시 본문 기준으로 다시 분류했다.

- 전체 classified: `3585`
- `annual`: `3357`
- `extraordinary`: `79`
- `unknown`: `6`

정기 주총 전수 `3357건` 감사 결과:

- `strict_success_rate 91.8%`
- `usable_rate 96.6%`
- `hard_fail_rate 3.4%`
- `median 185.7ms`
- `p95 10085.2ms`

정기 주총 scope별 usable rate:

- `summary_usable_rate 96.6%`
- `board_usable_rate 98.6%`
- `compensation_usable_rate 97.3%`
- `aoi_change_usable_rate 97.4%`
- `prov_financials_usable_rate 99.3%`

정기 주총의 주된 실패 패턴:

- `agenda_parse_low_confidence`
- `compensation_parse_empty`
- `meeting_datetime_missing`
- viewer HTML fallback 후에도 구조화 품질 개선 실패

대표 hard fail 예시:

- `NH프라임리츠`
- `대아티아이`
- `SNT홀딩스`
- `호텔신라`
- `페니트리움바이오`

### 임시 주총

임시 주총 표본은 `2026-03-31 이후` 공시 `79건`이다.

결과:

- `strict_success_rate 79.7%`
- `usable_rate 83.5%`
- `hard_fail_rate 16.5%`
- `median 26.0ms`
- `p95 5969.0ms`

scope별 usable rate:

- `summary_usable_rate 83.5%`
- `board_usable_rate 100.0%`
- `compensation_usable_rate 89.5%`
- `aoi_change_usable_rate 97.1%`
- `prov_financials_usable_rate 33.3%`

임시 주총의 주된 실패 패턴도 annual과 비슷하지만 더 가파르다.

- `agenda_parse_low_confidence`
- 일부 `compensation_parse_empty`
- `meeting_datetime_missing`

대표 hard fail 예시:

- `에스아이리소스`
- `아이에이`
- `진원생명과학`
- `메타바이오메드`

### scope별 usable rate

`annual`이 전반적으로 안정적이고, `extraordinary`가 더 어렵다.

핵심 차이:

- `summary_usable_rate`
  - annual `96.6%`
  - extraordinary `83.5%`
- `compensation_usable_rate`
  - annual `97.3%`
  - extraordinary `89.5%`
- `prov_financials_usable_rate`
  - annual `99.3%`
  - extraordinary `33.3%`

즉 임시 주총은 정기 주총보다 비정형 agenda와 sparse financial disclosure 때문에 parser 부담이 크다.

## 회귀 및 trade-off

이번 수정은 service semantics를 공격적으로 바꾸지 않았다.

- alias 보강: 식별 정확도만 개선
- `shareholder_meeting_results` 감사 surface 수정: 실제 tool 본체로 정렬
- transient retry: transport noise 완화
- `value_up`: 요청 구간 밖 공시 존재를 `partial`이 아니라 `no_filing + availability_status`로 보존
- `shareholder_meeting_results`: KIND 접수번호 변환 whitelist 대신 DART 원본 접수번호의 `document.xml` 결과 table을 우선 사용

따라서 regression risk는 낮다.

trade-off는 있다.

- retry는 실패 케이스에서 latency를 약간 늘릴 수 있다
- 그러나 hard fail을 그대로 남기는 것보다 audit 결과 신뢰성이 높다
- `value_up`의 과거 공시 존재 여부는 `search_diagnostics`로 남기므로 요청 구간 기준 의미는 유지된다
- `shareholder_meeting_results`는 결과 table이 실제로 파싱된 경우에만 `exact`로 올리고, 본문 결과가 안 잡힌 3건은 `requires_review`로 유지한다

## 후속 개선 검증

### `value_up`

`value_up`은 공시 특성상 매년 같은 시점에 반복되는 의무 공시가 아니다. 2026년 요청 구간에서 확인된 공시는 주로 3월과 4월에 집중됐고, 기존 `partial 32건`은 대부분 2024~2025년에 이미 공시가 있었지만 2026 요청 구간에는 공시가 없는 회사였다.

제목 패턴 probe 결과도 명확했다.

- [value_up_title_pattern_probe_224.json](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/audits/data/260517_parsing_success_rate_audit/value_up_title_pattern_probe_224.json:1)
- `272/272`건이 `기업가치제고`와 `계획`을 포함
- `밸류업`을 제목에 직접 포함한 공시는 `0/272`건
- 주요 제목:
  - `기업가치제고계획(자율공시)`
  - `기업가치제고계획(자율공시)(이행현황)`
  - `기업가치제고계획(자율공시)(고배당기업 표시를 위한 재공시)`
  - `기업가치제고계획예고(안내공시)`

따라서 `밸류업`은 사용자 표현 alias로 유지하되, 공식 제목 매칭은 `기업가치제고`와 `계획` 중심이 맞다.

개선 후 450개 재검증:

- [value_up_after_outside_window_reclass_450.json](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/audits/data/260517_parsing_success_rate_audit/value_up_after_outside_window_reclass_450.json:1)
- `exact 192`, `no_filing 258`
- `strict_success_rate 100.0%`
- `usable_rate 100.0%`
- `hard_fail_rate 0.0%`

비중복 100개 재검증:

- [recheck100_value_up_after_013_fix.json](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/audits/data/260517_parsing_success_rate_audit/recheck100_value_up_after_013_fix.json:1)
- `exact 34`, `no_filing 66`
- `strict_success_rate 100.0%`
- `hard_fail_rate 0.0%`
- KOSDAQ `원텍`에서 확인된 DART `013`은 조회 결과 없음이므로 `no_filing`으로 정리했다

### `shareholder_meeting_results`

기존 `requires_review`의 핵심은 KOSDAQ 결과 공시의 접수번호 패턴이었다.

- [shareholder_meeting_results_requires_review_pattern_probe.json](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/audits/data/260517_parsing_success_rate_audit/shareholder_meeting_results_requires_review_pattern_probe.json:1)
- 기존 `requires_review 152건` 중 `147건`이 KOSDAQ
- KOSDAQ 결과 공시는 접수번호 중간값이 `90`
- 기존 로직은 `80` 패턴만 KIND 변환 대상으로 인정해 자동 매핑을 막았다
- 그러나 DART `document.xml`로 원본 접수번호를 직접 열면 `149/152건`에서 결과 table 파싱이 가능했다

즉 더 좋은 접근은 KIND 번호 변환 whitelist를 넓히는 것이 아니라, **DART 원본 접수번호를 먼저 파싱하고 실패할 때만 KIND fallback을 쓰는 방식**이다.

개선 후 450개 재검증:

- [shareholder_meeting_results_after_dart_first_450.json](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/audits/data/260517_parsing_success_rate_audit/shareholder_meeting_results_after_dart_first_450.json:1)
- raw result: `exact 436`, `no_filing 6`, `requires_review 3`, `exception 5`
- KOSDAQ: `150/150 success`, `hard_fail 0.0%`
- exception 5건은 모두 일시적 `ConnectError`였고 targeted retry에서 `5/5 exact`로 회복
- [shareholder_meeting_results_exception5_retry_probe.json](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/audits/data/260517_parsing_success_rate_audit/shareholder_meeting_results_exception5_retry_probe.json:1)

exception retry까지 반영한 해석상 summary:

- `success 447`
- `soft_fail 3`
- `hard_fail 0`
- `strict_success_rate 99.3%`
- `usable_rate 100.0%`
- `hard_fail_rate 0.0%`

비중복 100개 재검증:

- [recheck100_value_up_shareholder_results_after_fixes.json](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/audits/data/260517_parsing_success_rate_audit/recheck100_value_up_shareholder_results_after_fixes.json:1)
- `exact 97`, `no_filing 3`
- `strict_success_rate 100.0%`
- `usable_rate 100.0%`
- `hard_fail_rate 0.0%`

남은 `requires_review 3건`은 `SNT다이내믹스`, `SNT홀딩스`, `SNT모티브`다. 이들은 결과 공시는 있으나 DART/KIND 본문에서 안건 결과 table을 찾지 못하거나 소집공고 구조 warning이 동반되는 케이스라, 자동 success로 올리지 않는 것이 맞다.

통합 요약 artifact:

- [value_up_shareholder_results_improvement_summary.json](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/audits/data/260517_parsing_success_rate_audit/value_up_shareholder_results_improvement_summary.json:1)

## 결론

회사 표본 기준 key parsing tool은 전반적으로 안정적이다.

- `9개` tool은 baseline `strict_success_rate 100%`
- `proxy_contest`의 hard fail은 transient/alias 문제였고 재검증에서 제거됐다
- `value_up`은 요청 구간 밖 공시 존재를 diagnostics로 남기면서 `strict_success_rate 100%`로 정리됐다
- `shareholder_meeting_results`는 DART-first 결과 파싱으로 KOSDAQ `requires_review` 대량 발생을 해소했다
- 남은 의미 있는 이슈는 `SNT` 계열 3건의 결과 본문 table 미검출이다

즉 다음 우선순위는 명확하다.

1. `shareholder_meeting_results`의 남은 3건에 대해 결과 본문 구조를 별도 분석
2. `value_up`의 title matching은 `기업가치제고` + `계획` 중심으로 유지하고, `밸류업`은 사용자 alias로만 취급
3. 성공률과 별개로 `dividend` latency 개선 audit 착수

`shareholder_meeting_notice`까지 포함하면 최종 해석은 이렇다.

- 회사 표본 기반 tool은 전반적으로 매우 안정적이다
- 남은 회사 표본 이슈는 `shareholder_meeting_results`의 일부 결과 본문 table 미검출뿐이다
- 공시 표본 기반 `shareholder_meeting_notice`는
  - 정기 주총 전수에서는 `usable_rate 96.6%`
  - 임시 주총에서는 `usable_rate 83.5%`

즉 현재 parser stack은 “회사 표본 기반 key tools는 매우 안정적이고, 남은 주요 리스크는 임시 주총 notice와 일부 결과 본문 table 변형” 상태라고 정리할 수 있다.
