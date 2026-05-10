---
type: audit
title: financial_metrics audit 통합 정리 — 6기업 sanity부터 200기업 전수까지
updated: 2026-05-10
related_audits:
  - 260501_2030_audit_financial_metrics-200기업
status: canonical
---

# financial_metrics audit 통합 정리

## 목적

`financial_metrics` 계열 audit를 한 문서로 묶는다.

## 포함 범위

- `260501_1820_audit_financial_metrics-6기업.md` (초기 sanity, 통합 후 원문 삭제)
- [[260501_2030_audit_financial_metrics-200기업]]

## 최종 결론

현재 기준 문서는 [[260501_2030_audit_financial_metrics-200기업]] 이다.

6기업 문서는 독립 기준 문서로 둘 가치가 낮아 이 통합 문서에 흡수했다.

## 흐름 요약

### 1. 6기업 sanity

- 문서: `260501_1820_audit_financial_metrics-6기업.md`
- 역할:
  - 신규 tool이 기본 패턴에서 망가지지 않는지 보는 초기 검증
- 성격:
  - 소표본 sanity
  - production readiness의 최종 판단 근거는 아님

### 2. 200기업 전수 audit

- 문서: [[260501_2030_audit_financial_metrics-200기업]]
- 역할:
  - KOSPI/KOSDAQ 대형 표본에서 실제 readiness 평가
- 의미:
  - 지금 `financial_metrics` 상태를 설명할 때 이 문서를 기준으로 써야 한다

## 지금 무엇을 기준으로 봐야 하나

- 현재 상태 / readiness / 실패 케이스:
  - [[260501_2030_audit_financial_metrics-200기업]]

- 초기 설계 의도와 sanity:
  - 이 통합 문서의 `6기업 sanity` 절

## 정리 원칙

- 6기업 문서는 “시작점”
- 200기업 문서는 “현재 기준”

## 추천 읽기 순서

1. 이 문서
2. [[260501_2030_audit_financial_metrics-200기업]]
3. 필요하면 이 문서의 `6기업 sanity` 절
