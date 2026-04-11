---
type: decision
title: 경력 파서 벤치마크 2026-04
tags: [parser, personnel, benchmark, xml]
sources: [benchmark_personnel_results.json]
related: [3-tier-fallback, 파서-판정-등급, agm-case-rule]
---

# 경력 파서 벤치마크 (2026-04)

## 요약

KOSPI 200 대상 personnel XML 파서 전수 벤치마크. 878명 후보자 중 SUCCESS 79.4%, SOFT_FAIL 11.7%, HARD_FAIL 8.9%. [[파서-판정-등급]] 체계로 판정하며, 원본 데이터는 [[benchmark-personnel-results]]에 수록.

## 수치

| 항목 | 값 |
|------|-----|
| 대상 기업 | 199개 (후보자 있는 기업 168개) |
| 총 후보자 | 878명 |
| SUCCESS | 697명 (79.4%) |
| SOFT_FAIL | 103명 (11.7%) |
| HARD_FAIL | 78명 (8.9%) |
| 총 소요 시간 | 84.56초 (기업당 424.9ms) |
| 추정 토큰 | 25.6M (기업당 129K) |

## 주요 실패 원인

### SOFT_FAIL (103명)
- **경력 병합 (merged)**: 여러 경력이 구분자 없이 1줄로 합쳐짐
  - 현/前 구분자 분리로 다수 해결 (KCC 손준성: 278자 -> 11건)
  - 연도 토큰 할당: 現=1토큰, 前=2토큰

### HARD_FAIL (78명)
- **no_career**: 경력 데이터 자체가 없음 (DART 원본에 미기재)
- **안건번호가 이름**: "제3-1호" 등이 후보자명으로 파싱됨 (BGF리테일 등)

## 파서 개선 이력

| 시점 | 개선 내용 | 효과 |
|------|----------|------|
| 03-23 | bs4 HTML 경력 파싱 | 삼성전자 김용관 1->7항목 |
| 03-23 | 단독연도(YYYY) 감지 | NAVER 김희철 1->4항목 |
| 03-24 | rowspan 경력 테이블 | 72개 기업, 22건 해결 |
| 03-29 | PDF fallback 5개 파서 | comp 97.5%, pers 93.9% |
| 04-06 | 現/前 구분자 + 연도 토큰 | 878명 중 697 SUCCESS |

## 남은 한계

- 파서로 해결 불가한 [[DART-OpenAPI]] 원본 구조 문제
- personnel 12건은 원본에 경력 데이터 자체가 없음
- [[3-tier-fallback]]의 PDF/OCR tier로 커버 필요. [[agm-case-rule]]의 판정 기준에 따라 HARD_FAIL 시 자동 전환
