---
type: source
title: benchmark_personnel_results.json 요약
source_path: raw/benchmarks/benchmark_personnel_results.json
ingested: 2026-04-05
tags: [benchmark, personnel, parser, xml]
related: [경력-파서-벤치마크-2026-04, 파서-판정-등급, 3-tier-fallback]
---

# benchmark_personnel_results.json 요약

## 핵심 내용

KOSPI 200 대상 personnel(이사/감사 선임) XML 파서의 전수 벤치마크 결과. 후보자 878명의 개별 판정.

## XML 파서 요약

| 항목 | 값 |
|------|-----|
| 대상 기업 | 199개 |
| 클린 기업 (후보자 있음) | 168개 |
| 총 후보자 | 878명 |
| SUCCESS | 697명 (79.4%) |
| SOFT_FAIL | 103명 (11.7%) |
| HARD_FAIL | 78명 (8.9%) |
| 에러 | 0건 |
| 총 소요 시간 | 84.56초 |
| 기업당 평균 | 424.9ms |
| 추정 총 토큰 | 25,651,954 |

## 주요 실패 패턴

- **no_career**: 경력 데이터 없음 (HARD_FAIL 주요 원인)
- **merged:N**: N개 경력이 1줄로 병합됨 (SOFT_FAIL 주요 원인)
- **안건번호가 이름으로 파싱**: "제3-1호" 등이 후보자명으로 잡힘

## 기업별 특이사항

- BGF리테일: 4명 중 3명 HARD_FAIL (안건번호가 이름으로 파싱)
- DB손해보험: 13명 중 2명 SOFT_FAIL (경력 병합)
- BNK금융지주: 후보자 0명 (선임 안건 없음, 정상)
