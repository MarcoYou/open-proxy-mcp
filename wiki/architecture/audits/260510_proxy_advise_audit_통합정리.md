---
type: audit
title: proxy_advise / action audit 통합 정리 — sanity, 실패, 수렴, framework까지
updated: 2026-05-10
related_audits:
  - 260502_2300_audit_advise-recap-vote
  - 260503_1847_audit_phase4_final
  - 260504_0028_audit_proxy_advise_rename_regression
  - 260504_0724_audit_parse_personnel_iter1-7
  - 260504_2200_audit_proxy_advise_framework_iter1-8
status: canonical
---

# proxy_advise / action audit 통합 정리

## 목적

`advise / recap / proxy_advise / parse_personnel` 흐름이 시점별 audit로 너무 잘게 쪼개져 있어서, 현재 읽는 기준을 한 문서로 묶는다.

## 포함 범위

- [[260502_2300_audit_advise-recap-vote]]
- `260503_0130_audit_advise-200-virtual.md` (통합 후 원문 삭제)
- `260503_0500_audit_phase3_final.md` (통합 후 원문 삭제)
- [[260503_1847_audit_phase4_final]]
- [[260504_0028_audit_proxy_advise_rename_regression]]
- `260504_0705_audit_proxy_advise_ralph_final.md` (통합 후 원문 삭제)
- [[260504_0724_audit_parse_personnel_iter1-7]]
- [[260504_2200_audit_proxy_advise_framework_iter1-8]]

## 최종 결론

현재 action / advise 계열을 설명할 때 기준 문서는 두 개다.

1. action 정확도 / regression 최종 상태:
- [[260503_1847_audit_phase4_final]]

2. proxy_advise framework / facts / risk / citation 풍부화 상태:
- [[260504_2200_audit_proxy_advise_framework_iter1-8]]

그 외 문서들은 대부분 “중간 진행 과정”이다.

## 흐름 요약

### 1. action tool 재편 sanity

- 문서: [[260502_2300_audit_advise-recap-vote]]
- 역할:
  - 재편 직후 기본 sanity 확인
- 성격:
  - 초기 구조 검증

### 2. 200기업 가상실험 / partial 단계

- 문서: `260503_0130_audit_advise-200-virtual.md`
- 역할:
  - batch hang과 한계 상황 기록
- 성격:
  - 진행 중 상태 보고

### 3. Phase 3 실패 기록

- 문서: `260503_0500_audit_phase3_final.md`
- 역할:
  - 목표 미달과 regression을 정직하게 남긴 문서
- 성격:
  - 실패 기록
  - 현재 기준 문서는 아님

### 4. Phase 4 성공 기록

- 문서: [[260503_1847_audit_phase4_final]]
- 역할:
  - 정확도 100%, regression 0 도달
- 의미:
  - action 정확도 측면의 현재 기준 문서

### 5. proxy_advise rename / scope 확장 회귀 점검

- 문서: [[260504_0028_audit_proxy_advise_rename_regression]]
- 역할:
  - rename과 9 scope 확장 후 기존 baseline 유지 확인
- 성격:
  - 중간 회귀 점검

### 6. proxy_advise 검증 ralph 중간 수렴

- 문서: `260504_0705_audit_proxy_advise_ralph_final.md`
- 역할:
  - iter 1~20 과정의 수렴 기록
- 성격:
  - 중간 상태 보고

### 7. parse_personnel 강화

- 문서: [[260504_0724_audit_parse_personnel_iter1-7]]
- 역할:
  - role / period / careerDetails 품질 개선 기록
- 성격:
  - proxy_advise framework의 하위 품질 개선 이력

### 8. framework enrichment 최종 상태

- 문서: [[260504_2200_audit_proxy_advise_framework_iter1-8]]
- 역할:
  - facts / risk / citation / evidence / 후보 raw까지 포함한 최종 풍부화 상태
- 의미:
  - 지금 `proxy_advise` 표면 품질을 설명할 때의 기준 문서

## 지금 무엇을 기준으로 봐야 하나

### action 정확도 / regression
- [[260503_1847_audit_phase4_final]]

### proxy_advise 표면 품질 / framework
- [[260504_2200_audit_proxy_advise_framework_iter1-8]]

### recap 전용
- [[260503_2304_audit_recap_pattern]]

### parse_personnel 하위 품질
- [[260504_0724_audit_parse_personnel_iter1-7]]

## 정리 원칙

- `260503_0500`은 실패 기록으로 남긴다.
- `260503_1847`이 정확도 축의 최종 기준이다.
- `260504_2200`이 `proxy_advise` 응답 품질 축의 최종 기준이다.
- 그 사이 문서들은 회귀 점검 / 수렴 과정 / 하위 개선 이력으로 본다.

## 추천 읽기 순서

1. 이 문서
2. [[260503_1847_audit_phase4_final]]
3. [[260504_2200_audit_proxy_advise_framework_iter1-8]]
4. 필요하면 [[260503_2304_audit_recap_pattern]]
