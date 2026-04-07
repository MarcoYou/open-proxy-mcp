---
type: source
title: AGM_CASE_RULE.md 요약
source_path: raw/rules/AGM_CASE_RULE.md
ingested: 2026-04-05
tags: [agm, parser, benchmark, case-rule]
related: [3-tier-fallback, 파서-판정-등급, agm-tool-rule]
---

# AGM_CASE_RULE.md 요약

## 핵심 내용

8개 AGM 파서의 성공/실패 판정 기준을 실데이터 예시와 함께 정의. AI가 파서 결과를 검증하고 fallback 여부를 결정하는 기준.

## 파싱 성능 (KOSPI 200)

| 파서 | XML | PDF | OCR |
|------|-----|-----|-----|
| agenda | 99.5% | 98.0% | 100% |
| financials BS | 97.4% | 97.9% | 100% |
| financials IS | 100% | 95.7% | 100% |
| personnel | 98.9% | 97.9% | 100% |
| aoi (정관변경) | 97.8% | 99.0% | 100% |
| compensation | 98.4% | 99.5% | 100% |

## [[파서-판정-등급]]

| 등급 | 의미 | AI 행동 |
|------|------|---------|
| SUCCESS | 필수 필드 충족, 형태 정상 | 답변 (포맷 보정 가능) |
| SOFT_FAIL | 일부 누락/형태 이상 | AI 보정 시도, 실패 시 PDF fallback |
| HARD_FAIL | 핵심 데이터 없음 | PDF fallback 제안 |

## 파서별 주요 판정 기준

- **agenda**: items >= 1, title 2-150자, number 정규식 정상
- **financials**: BS rows >= 5 + IS rows >= 3, unit 존재, 핵심 계정 포함
- **personnel**: candidates >= 1, name 2-10자, careerDetails >= 1, content <= 100자
- **aoi_change**: amendments >= 1, before/after 5자+, clause에 제N조
- **compensation**: currentLimit > 0, headcount 존재, previous 금액
- **treasury_share**: 수량 >= 1, 목적/방법 존재
- **capital_reserve**: 금액 + 전입 대상 명시
- **retirement_pay**: before/after 텍스트 존재

## 주요 SOFT_FAIL 사례

- **경력 병합** (KCC 손준성): 278자 1줄로 병합된 경력을 AI가 現/前 구분자로 분리
- **전기 데이터 누락** (한미사이언스): 당기 한도는 있지만 전기 비교 불가
- **clause 누락**: 정관변경에서 조문번호 미파싱

## 주요 HARD_FAIL 사례

- **이름이 조문번호**: 정관 텍스트가 후보자 이름으로 잡힌 케이스
- **데이터 없음**: 안건이 있는데 파싱 자체가 실패
