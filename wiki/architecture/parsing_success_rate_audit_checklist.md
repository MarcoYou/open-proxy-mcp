---
type: architecture
title: parsing-success-rate-audit-checklist
updated: 2026-05-18
related_notes:
  - parsing_success_rate_audit_spec
  - goals/parsing_success_rate_audit_goal
---

# Parsing Success Rate Audit Checklist

## 목적

이 문서는 [parsing_success_rate_audit_spec.md](./parsing_success_rate_audit_spec.md)를 실제로 실행하기 위한 재실행용 checklist다. 최신 실행 결과와 판정 기준선은 [260517_parsing_success_rate_audit.md](./audits/260517_parsing_success_rate_audit.md)를 본다.

## 1. 감사 범위 고정

- [ ] 1차 핵심 감사 대상 tool을 확정한다.
- [ ] 2차 확장 감사 대상 tool을 확정한다.
- [ ] `evidence`, `proxy_advise_before_meeting`, `proxy_result_after_meeting`를 1차 범위에서 제외한다.

### 1차 핵심 감사 대상

- [ ] `company`
- [ ] `shareholder_meeting_notice`
- [ ] `shareholder_meeting_results`
- [ ] `ownership_structure`
- [ ] `financial_metrics`
- [ ] `corp_gov_report`
- [ ] `dividend`
- [ ] `treasury_share`
- [ ] `value_up`

### 2차 확장 감사 대상

- [ ] `corporate_restructuring`
- [ ] `dilutive_issuance`
- [ ] `proxy_contest`
- [ ] `related_party_transaction`

## 2. 표본 정의 고정

- [ ] 일반 tool은 회사 표본 기준 universe를 고정한다.
- [ ] baseline 회사 표본을 `KOSPI 300 + KOSDAQ 150`으로 고정한다.
- [ ] 비중복 재검증 표본을 `KOSPI 50 + KOSDAQ 50`으로 고정한다.
- [ ] 정정공시는 최신본 우선 규칙을 명시한다.
- [ ] 비중복 추가 표본을 미리 따로 확보한다.

### baseline 회사 표본 파일

- [ ] [universe_kospi300.csv](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/audits/data/260517_parsing_success_rate_audit/universe_kospi300.csv:1)
- [ ] [universe_kosdaq150.csv](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/audits/data/260517_parsing_success_rate_audit/universe_kosdaq150.csv:1)
- [ ] [scripts/parsing_success_rate_audit.py](/Users/marcoyou/Projects/open-proxy-mcp/scripts/parsing_success_rate_audit.py:1)로 baseline을 실행한다.

### 비중복 재검증 표본 파일

- [ ] [universe_kospi50_additional_nonoverlap.csv](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/audits/data/260517_parsing_success_rate_audit/universe_kospi50_additional_nonoverlap.csv:1)
- [ ] [universe_kosdaq50_additional_nonoverlap.csv](/Users/marcoyou/Projects/open-proxy-mcp/wiki/architecture/audits/data/260517_parsing_success_rate_audit/universe_kosdaq50_additional_nonoverlap.csv:1)
- [ ] 같은 스크립트로 `--universe recheck100` 재검증을 실행한다.

### `shareholder_meeting_notice` 특례

- [ ] `2026년 정기 주총 notice 전수` 표본을 만든다.
- [ ] `2026-03-31 이후 현재까지의 임시주총 notice 전수` 표본을 만든다.
- [ ] 이 tool은 회사 표본이 아니라 공시 표본 감사라는 점을 기록한다.
- [ ] 정기주총과 임시주총을 합쳐 단일 success rate로 보지 않는다고 명시한다.

## 3. 실행 배치 고정

- [ ] DART hard rule `1000/min`를 확인한다.
- [ ] repo guardrail `900/min`를 확인한다.
- [ ] 감사용 실효 ceiling을 `500~600/min`으로 둔다.
- [ ] tool별 예상 호출 수를 low / medium / high로 분류한다.
- [ ] high-call tool은 `10~20개` chunk로 실행한다.
- [ ] chunk 사이 `15~30초` sleep을 둔다.
- [ ] heavy tool 사이 `30~60초` sleep을 둔다.
- [ ] 여러 tool 동시 전수 실행은 하지 않고 tool 하나씩 돈다.

## 4. 판정 기준 고정

- [ ] `success` 정의를 문서화한다.
- [ ] `soft fail` 정의를 문서화한다.
- [ ] `hard fail` 정의를 문서화한다.
- [ ] `no_filing`이 정상 outcome인 tool 목록을 따로 표시한다.

### 공통 지표

- [ ] `strict_success_rate`
- [ ] `usable_rate`
- [ ] `soft_fail_rate`
- [ ] `hard_fail_rate`

### `shareholder_meeting_notice` 보조 지표

- [ ] `notice_selection_success_rate`
- [ ] `agenda_hierarchy_complete_rate`
- [ ] `summary_usable_rate`
- [ ] `board_usable_rate`
- [ ] `compensation_usable_rate`
- [ ] `aoi_change_usable_rate`
- [ ] `prov_financials_usable_rate`

## 5. Baseline 수집

- [ ] raw output을 저장한다.
- [ ] status를 저장한다.
- [ ] warnings를 저장한다.
- [ ] filing count를 저장한다.
- [ ] latency를 저장한다.
- [ ] DART call 수를 저장한다.
- [ ] sample metadata를 저장한다.

## 6. Failure cluster 분류

- [ ] 회사 식별 실패 cluster를 분리한다.
- [ ] notice selection 실패 cluster를 분리한다.
- [ ] agenda hierarchy 실패 cluster를 분리한다.
- [ ] ownership table parsing 실패 cluster를 분리한다.
- [ ] governance form variant 실패 cluster를 분리한다.
- [ ] treasury / value_up fallback cluster를 분리한다.

## 7. 개선 설계

- [ ] 한 번에 한 failure cluster만 수정 대상으로 잡는다.
- [ ] semantic drift 가능성이 있는 변경은 분리 검토한다.
- [ ] source priority 변경은 별도 승인 항목으로 남긴다.

## 8. 회귀 검증

- [ ] before / after를 같은 표본에서 비교한다.
- [ ] timestamp / usage counter 등 비본질 필드만 diff에서 제외한다.
- [ ] hard fail 증가가 없는지 확인한다.
- [ ] exact 케이스 semantic drift가 없는지 확인한다.
- [ ] 정상 `no_filing` 분류가 깨지지 않았는지 확인한다.
- [ ] median / p95 latency가 악화되지 않았는지 확인한다.
- [ ] 회사당 DART call 수가 과도하게 늘지 않았는지 확인한다.

## 9. 비중복 추가 표본 재검증

- [ ] 기존 표본과 겹치지 않는 추가 표본을 만든다.
- [ ] 수정한 parser family만 재실행한다.
- [ ] 같은 지표로 재집계한다.
- [ ] 기존 성공률과 의미가 유지되는지 확인한다.

## 10. 최종 정리

- [ ] tool별 결과 표를 만든다.
- [ ] top 3 failure cluster를 기록한다.
- [ ] 개선 후보와 비추천 후보를 분리한다.
- [ ] 남은 리스크와 미검증 범위를 적는다.
- [ ] 후속 작업 순서를 적는다.

## 최종 결과 표 필드

- [ ] tool
- [ ] sample definition
- [ ] total
- [ ] exact
- [ ] legit_no_filing
- [ ] ambiguous
- [ ] partial / requires_review / conflict
- [ ] hard_fail
- [ ] median latency
- [ ] p95 latency
- [ ] avg DART calls
- [ ] top 3 failure clusters
- [ ] linked fix / audit
