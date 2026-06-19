---
type: audit
title: 파싱 audit 통합 정리 — 2026-04-21 ~ 2026-04-29 흐름 요약
updated: 2026-05-10
related_audits:
  - 260421_2308_audit_parsing-10tool-20기업
  - 260422_0005_audit_parsing-14scope-15기업
  - 260429_0216_audit_parsing-200기업-v1
  - 260429_0912_audit_parsing-200기업-v2-no_filing
  - 260429_2053_audit_personnel-878명
status: canonical
---

# 파싱 audit 통합 정리

## 목적

흩어져 있던 파싱 계열 audit를 한 문서로 묶는다.

이 문서는 다음 흐름의 최종 요약본이다.
- 초기 소표본 상태 점검
- scope/필드 채움률 확장 점검
- 200기업 전수 audit
- `no_filing` 분리
- personnel 후보자/경력 파서 별도 심화 점검

## 포함 범위

- [[260421_2308_audit_parsing-10tool-20기업]]
- [[260422_0005_audit_parsing-14scope-15기업]]
- [[260429_0216_audit_parsing-200기업-v1]]
- [[260429_0912_audit_parsing-200기업-v2-no_filing]]
- [[260429_2053_audit_personnel-878명]]

## 최종 결론

현재 파싱 계열의 기준 문서는 사실상 두 축이다.

1. 전반 상태 기준:
- [[260429_0912_audit_parsing-200기업-v2-no_filing]]

2. 후보자/경력 파서 기준:
- [[260429_2053_audit_personnel-878명]]

그 이전 문서들은 모두 “상태 변화 과정”으로 읽어야 한다.

## 흐름 요약

### 1. 2026-04-21 — 10개 tool, 20개 회사 초기 점검

- 문서: [[260421_2308_audit_parsing-10tool-20기업]]
- 의미:
  - 파싱 계열의 첫 health check
  - 어떤 tool이 구조적으로 약한지 찾는 단계
- 한계:
  - 표본이 작고 대형주 편향이 강했다

### 2. 2026-04-22 — 14 scope, 15개 회사 확장 점검

- 문서: [[260422_0005_audit_parsing-14scope-15기업]]
- 의미:
  - `summary` 외 scope와 필드 채움률까지 본 확장판
- 한계:
  - 여전히 소표본이라 전수 상태 판단 기준으로 쓰기 어렵다

### 3. 2026-04-29 v1 — 200기업 전수 audit

- 문서: [[260429_0216_audit_parsing-200기업-v1]]
- 의미:
  - 처음으로 큰 표본 전수 측정이 들어갔다
- 문제:
  - `partial` 안에 “사건이 없는 정상 케이스”와 “진짜 파싱 실패”가 섞여 있었다

### 4. 2026-04-29 v2 — `no_filing` 분리

- 문서: [[260429_0912_audit_parsing-200기업-v2-no_filing]]
- 의미:
  - `partial`을 그대로 보지 않고
    - `no_filing`
    - `partial_failure`
  로 분리했다
- 이 문서가 중요한 이유:
  - 지금도 파싱 전수 상태를 읽는 기준 문서이기 때문이다

### 5. 2026-04-29 — personnel 심화 audit

- 문서: [[260429_2053_audit_personnel-878명]]
- 의미:
  - `shareholder_meeting` 후보자/경력 파서를 별도로 깊게 검증했다
  - 전반 parsing 문서 하나에 묻히기엔 중요도가 높아 별도 유지가 맞다

## 지금 무엇을 기준으로 봐야 하나

### 전반 parsing 상태
- [[260429_0912_audit_parsing-200기업-v2-no_filing]]

### 후보자/경력 parsing 상태
- [[260429_2053_audit_personnel-878명]]

### parser 분류 / 권장 방향 / 후속 우선순위
- [[260508_parser_audit]]

## 정리 원칙

- `260421`, `260422`, `260429 v1`는 독립 기준 문서가 아니라 이력 문서다.
- parsing 상태를 지금 설명할 때는 `v2-no_filing`을 기본으로 삼는다.
- personnel은 영향도가 커서 별도 축으로 유지한다.

## 추천 읽기 순서

1. 이 문서
2. [[260429_0912_audit_parsing-200기업-v2-no_filing]]
3. [[260429_2053_audit_personnel-878명]]
4. [[260508_parser_audit]]

## 관련

- [[260508_parser_audit]]
- [[../audits/README]]
