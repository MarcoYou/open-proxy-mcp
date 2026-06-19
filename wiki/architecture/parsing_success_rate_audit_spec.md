---
type: architecture
title: parsing-success-rate-audit-spec
updated: 2026-05-18
related_tools:
  - company
  - shareholder_meeting_notice
  - shareholder_meeting_results
  - ownership_structure
  - financial_metrics
  - corp_gov_report
  - dividend
  - treasury_share
  - value_up
  - corporate_restructuring
  - dilutive_issuance
  - proxy_contest
  - related_party_transaction
related_audits:
  - 260517_parsing_success_rate_audit
  - 260510_parsing_audit_통합정리
  - 260510_data_tools_perf_audit
  - 260429_0912_audit_parsing-200기업-v2-no_filing
  - 260508_parser_audit
related_notes:
  - parsing_success_rate_audit_checklist
  - goals/parsing_success_rate_audit_goal
related_data:
  - 260517_parsing_success_rate_audit/universe_kospi300
  - 260517_parsing_success_rate_audit/universe_kosdaq150
  - 260517_parsing_success_rate_audit/universe_kospi50_additional_nonoverlap
  - 260517_parsing_success_rate_audit/universe_kosdaq50_additional_nonoverlap
---

# Parsing Success Rate Audit Spec

## 목적

이 문서는 **회사 표본 전체를 대상으로 key data tools의 parsing 성공률을 점검하기 위한 source of truth**다.

빠른 실행 순서는 [parsing_success_rate_audit_checklist.md](./parsing_success_rate_audit_checklist.md)를 따른다.

목표는 다음 다섯 가지다.

1. 어떤 tool과 어떤 parser family를 감사할지 고정한다.
2. tool별 sample definition을 고정한다.
3. DART API rate limit을 넘지 않는 실행 배치 전략을 정의한다.
4. `success`, `soft fail`, `hard fail` 판정 기준을 고정한다.
5. 회귀 없이, 그리고 처리 속도를 지나치게 악화시키지 않는 개선 루프를 정의한다.

이 문서는 **감사 실행 전 요구사항 문서**다. 실제 실행 결과와 최신 성공률 기준은 [[260517_parsing_success_rate_audit]]를 본다.

---

## 범위

### 1차 핵심 감사 대상

아래 tool은 parser 성공률 감사의 1차 대상이다.

- `company`
- `shareholder_meeting_notice`
- `shareholder_meeting_results`
- `ownership_structure`
- `financial_metrics`
- `corp_gov_report`
- `dividend`
- `treasury_share`
- `value_up`

### 2차 확장 감사 대상

아래 tool은 1차 감사 이후에 수행한다.

- `corporate_restructuring`
- `dilutive_issuance`
- `proxy_contest`
- `related_party_transaction`

### 1차 감사 제외 대상

- `evidence`
  - utility 성격이 강하고 parsing failure risk가 낮음
- `proxy_advise_before_meeting`
- `proxy_result_after_meeting`
  - 이 둘은 parser 성공률보다 orchestration / downstream quality audit로 별도 분리

---

## Parser Family 구분

감사는 tool 이름만 기준으로 보지 않고, 아래 parser family 기준으로도 묶어서 본다.

### 회사 식별 계열

- company resolution
- alias resolution
- corp_code / ticker / canonical entity matching

### 주총 소집공고 계열

- notice filing selection
- notice HTML/XML parsing
- agenda hierarchy extraction
- board / compensation / aoi_change / prov_financials scope parsing

### 주총 결과 계열

- KIND result scraping
- result-to-meeting matching
- 사후 안건 결과 정규화

### 지분 구조 계열

- 정기보고서 표 파싱
- 최대주주 / 특수관계 / 5% / control_map 추출

### 재무 집계 계열

- DART financial endpoint aggregation
- derived metrics / audit opinion normalization

### 지배구조보고서 계열

- indicator extraction
- 금융지주 / 일반 상장사 form variant 처리

### 배당 / 자사주 / 밸류업 계열

- dividend filing selection + breakdown
- treasury decision/result scan + cycle matching
- value-up search + fallback + treasury cross-ref

### DS005 이벤트 계열

- corporate_restructuring
- dilutive_issuance

### 분쟁 / 내부거래 계열

- proxy_contest
- related_party_transaction

감사 표는 **tool 기준**과 **parser family 기준**을 둘 다 유지한다.

---

## Sample Definition

### 공통 규칙

- 기본 단위는 **회사 표본**이다.
- tool별로 필요한 경우 회사 표본에서 공시 표본으로 좁혀서 본다.
- 표본은 가능한 한 **고정된 universe**를 사용한다.
- 정정공시는 원칙적으로 **최신본 우선 1건**만 사용한다.
- 비중복 추가 표본을 반드시 별도로 유지해 후속 재검증에 사용한다.

### 이번 감사의 기본 회사 표본

이번 parsing success-rate audit의 기본 회사 표본은 아래 두 파일로 고정한다.

- `KOSPI 300`
  - [universe_kospi300.csv](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/audits/data/260517_parsing_success_rate_audit/universe_kospi300.csv:1)
- `KOSDAQ 150`
  - [universe_kosdaq150.csv](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/audits/data/260517_parsing_success_rate_audit/universe_kosdaq150.csv:1)

즉, **full-scale audit universe = KOSPI 300 + KOSDAQ 150 = 450개 회사**다.

### 비중복 재검증 표본

수정 후 재검증은 아래 비중복 추가 표본으로 수행한다.

- `KOSPI 50`
  - [universe_kospi50_additional_nonoverlap.csv](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/audits/data/260517_parsing_success_rate_audit/universe_kospi50_additional_nonoverlap.csv:1)
- `KOSDAQ 50`
  - [universe_kosdaq50_additional_nonoverlap.csv](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/audits/data/260517_parsing_success_rate_audit/universe_kosdaq50_additional_nonoverlap.csv:1)

즉, **non-overlap recheck universe = 100개 회사**다.

### 회사 표본 감사 실행 스크립트

회사 표본 기반 parsing success-rate baseline은 아래 스크립트를 사용한다.

- [scripts/parsing_success_rate_audit.py](/Users/marcoyou/Projects/open-proxy-mcp/scripts/parsing_success_rate_audit.py:1)

기본 baseline 실행 예시:

```bash
uv run python scripts/parsing_success_rate_audit.py \
  --universe combined450 \
  --output wiki/architecture/audits/data/260517_parsing_success_rate_audit/baseline_company_sample_450.json
```

비중복 재검증 실행 예시:

```bash
uv run python scripts/parsing_success_rate_audit.py \
  --universe recheck100 \
  --output wiki/architecture/audits/data/260517_parsing_success_rate_audit/recheck_company_sample_100.json
```

### shareholder_meeting_notice 특례

`shareholder_meeting_notice`는 회사 표본보다 **공시 표본 단위 감사**가 더 적합하므로, 아래 두 트랙으로 나눈다.

#### 트랙 A: 2026년 정기주총

- 대상: `2026년 정기 주총 notice 전수`
- 목적:
  - 표준 annual meeting parsing 품질 측정
  - agenda hierarchy completeness 확인
  - board / compensation / aoi_change / prov_financials scope 성공률 측정

#### 트랙 B: 2026-03-31 이후 임시주총

- 대상: `2026-03-31 이후 현재까지 나온 임시주총 notice 전수`
- 목적:
  - 비정형 agenda
  - 정관변경 / 자본거래 / 특수 이벤트
  - notice candidate selection 안정성
  - body fallback 안정성

`shareholder_meeting_notice`는 정기와 임시를 합쳐 하나의 success rate로 보지 않는다.

### shareholder_meeting_results 샘플

- `shareholder_meeting_notice`와 가능한 한 같은 회사 universe를 기준으로 하되
- 실제로는 결과 공시 존재 여부에 따라 공시 표본 기준 집계를 병행한다.

### no_filing이 정상인 tool

아래 tool은 `no_filing`이 business-normal outcome일 수 있으므로, 표본 정의와 결과 집계에서 분리한다.

- `treasury_share`
- `value_up`
- `corp_gov_report` 일부 표본
- `dividend` 일부 표본
- `corporate_restructuring`
- `dilutive_issuance`
- `proxy_contest`
- `related_party_transaction`

---

## Rate Limit 및 실행 배치 규칙

### 상한

- DART hard rule: `1000/min`
- repo 내부 guardrail: `900/min`

감사 작업은 `900/min`에 맞추지 않는다.
실행 설계 목표는 **실효 500~600/min 이하**다.

### tool별 호출 버킷

#### 저호출 tool

- 회사당 예상 1~3회
- 예: `company`, `dividend`
- 권장 batch: `40~80`

#### 중간 호출 tool

- 회사당 예상 4~8회
- 예: `financial_metrics`, `corp_gov_report`, `corporate_restructuring`, `dilutive_issuance`, `related_party_transaction`
- 권장 batch: `20~40`

#### 고호출 / fallback-heavy tool

- 회사당 예상 8~15회 이상
- 예: `shareholder_meeting_notice`, `ownership_structure`, `treasury_share`, `value_up`, `proxy_contest`
- 권장 batch: `8~20`

### 실행 규칙

- 모든 tool을 한 번에 전수 실행하지 않는다.
- **툴 하나씩** 전체 표본을 도는 방식으로 진행한다.
- heavy tool은 `10~20개` chunk 단위로 실행한다.
- chunk 사이 `15~30초` sleep을 둔다.
- heavy tool 사이 `30~60초` sleep을 둔다.
- action tool은 parser 성공률 감사 1차 루프에서 제외한다.

### 실행 단계

1. `Phase A`
   - 1차 핵심 tool에 대해 baseline 수행
2. `Phase B`
   - 실패 cluster가 많은 tool만 확대 검증
3. `Phase C`
   - action tool 또는 orchestration surface 별도 감사

---

## 판정 기준

### Success

아래 조건이면 `success`로 본다.

- `exact`
- 해당 tool에서 `no_filing`이 정상 business outcome일 경우의 `no_filing`
- schema 정상
- parser exception 없음
- 핵심 필드 존재
- warning이 있어도 downstream 사용 가능

예:

- `company`의 `exact`
- `treasury_share`의 정상 `no_filing`
- 제출 의무 없음으로 해석 가능한 `corp_gov_report`의 `no_filing`

### Soft Fail

아래는 `soft fail`로 본다.

- `ambiguous`
- `partial`
- `requires_review`
- `conflict`
- payload는 있으나 일부 핵심 하위 블록이 불완전
- 사람이 후속 검토하면 쓸 수 있으나 blind downstream 사용은 위험

예:

- company 후보는 잡혔지만 exact 1건으로 확정 못함
- governance report 일부 indicator만 파싱
- meeting result는 찾았지만 join confidence가 낮음

### Hard Fail

아래는 `hard fail`로 본다.

- `error`
- `search_error`
- exception / timeout
- schema break
- filing은 있는데 unusable payload 반환
- 정상처럼 보이지만 핵심 필드가 비어 downstream 사용 불가

예:

- DART `013`을 잘못 infra failure로 처리
- HTML parser exception
- filing 존재에도 payload unusable

### 지표

최소 아래 네 개를 집계한다.

- `strict_success_rate = success / total`
- `usable_rate = (success + soft_fail) / total`
- `soft_fail_rate = soft_fail / total`
- `hard_fail_rate = hard_fail / total`

그리고 `no_filing`이 정상인 tool은 추가로 아래를 분리한다.

- `exact_rate`
- `legit_no_filing_rate`
- `hard_fail_rate`

### shareholder_meeting_notice 전용 보조 지표

이 tool은 아래 지표를 별도 집계한다.

- `notice_selection_success_rate`
- `agenda_hierarchy_complete_rate`
- `summary_usable_rate`
- `board_usable_rate`
- `compensation_usable_rate`
- `aoi_change_usable_rate`
- `prov_financials_usable_rate`

또한 정기주총과 임시주총을 분리 집계한다.

---

## 개선 원칙

### 기본 원칙

- 속도보다 correctness 우선
- 다만 correctness 개선을 핑계로 latency를 과도하게 악화시키지 않는다
- source priority는 성능 때문에 바꾸지 않는다
- `no_filing`과 `error`를 섞지 않는다

### 허용되는 최적화

- request-local cache
- duplicate fetch 제거
- shared scan reuse
- 필요한 sub-signal만 계산
- fallback 진입 조건 정밀화

### 금지 또는 주의 대상

- fallback을 무작정 넓히기
- 모든 회사에서 body parsing 추가 수행
- mutable parsed output을 request 밖으로 공유
- source priority를 바꿔 속도만 얻는 변경

---

## Regression-free 개선 루프

### 1. Baseline 고정

각 tool baseline에서 아래를 저장한다.

- raw output
- status
- warnings
- filing count
- latency
- DART call 수
- sample metadata

### 2. Failure cluster 분류

실패는 tool 단위가 아니라 parser family 단위로 묶는다.

예:

- alias resolution 문제
- notice candidate selection 문제
- governance form variant 문제
- treasury result scan 문제
- value_up fallback 진입 과다 문제

### 3. 한 cluster씩 수정

- 한 번에 한 failure cluster만 수정
- 수정 전후 비교 범위를 고정 표본으로 제한

### 4. Regression gate

before/after 비교 시:

- 무시 가능한 diff만 제외
  - timestamp
  - usage counter
  - 비본질 메타데이터

수정안은 아래를 모두 만족해야 한다.

- hard fail 증가 없음
- exact 케이스 semantic drift 없음
- 정상 `no_filing` 분류 유지
- success 또는 usable rate 개선

### 5. Speed gate

각 수정안마다 아래를 같이 본다.

- median latency
- p95 latency
- 회사당 평균 DART 호출 수
- fallback 진입 비율

### 6. 비중복 추가 표본 재검증

기존 표본에서 통과해도 merge하지 않는다.

- 비중복 추가 표본에서 재실행
- 같은 지표를 다시 집계
- 그 후에만 merge candidate로 승격

---

## 권장 실행 순서

1. `company`
2. `shareholder_meeting_notice`
3. `ownership_structure`
4. `corp_gov_report`
5. `financial_metrics`
6. `dividend`
7. `treasury_share`
8. `value_up`
9. `shareholder_meeting_results`
10. `corporate_restructuring`
11. `dilutive_issuance`
12. `proxy_contest`
13. `related_party_transaction`
14. action tool은 마지막

이 순서를 따르는 이유는 앞단 tool이 downstream 의존성과 parser 복잡도가 높기 때문이다.

---

## 산출물 요구

최종 산출물은 아래 두 가지다.

1. 한국어 `audit spec / execution memo`
2. 실제 실행용 `checklist`

또한 tool별 결과 집계는 최소 아래 열을 포함해야 한다.

- tool
- sample definition
- total
- exact
- legit_no_filing
- ambiguous
- partial / requires_review / conflict
- hard_fail
- median latency
- p95 latency
- avg DART calls
- top 3 failure clusters
- linked fix / audit

---

## Goal 실행 규칙

이 문서를 참조하는 goal은 다음을 수행해야 한다.

- 문서를 source of truth로 간주할 것
- 새로운 sample rule을 임의로 추가하지 말 것
- `shareholder_meeting_notice`의 정기/임시 전수 규칙을 유지할 것
- action tool을 parser 성공률 감사 1차 범위에 넣지 말 것
- 결과는 실행 가능한 spec와 checklist 형태로 정리할 것

추천 invoke 메시지:

```text
/goal Use wiki/architecture/parsing_success_rate_audit_spec.md as the source of truth for executing the key data tools parsing success rate audit. Run the 450 company baseline for company sample tools. Keep shareholder_meeting_notice as a separate filing sample audit with all 2026 annual meeting notices and all extraordinary meeting notices filed since 2026 03 31. Classify results into success soft fail and hard fail. Identify parser family failure clusters. Apply only regression safe fixes. Rerun targeted checks and non overlap recheck when fixes are made. Produce a Korean audit result document with success rates usable rates hard fail rates latency findings regression findings and next priorities.
```

실행용 단일 문서는 [[goals/parsing_success_rate_audit_goal]]를 사용한다.
