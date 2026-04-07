---
type: source
title: DIV_CASE_RULE.md 요약
source_path: raw/rules/DIV_CASE_RULE.md
ingested: 2026-04-05
tags: [dividend, case-rule, parser]
related: [배당성향, 배당수익률, 파서-판정-등급, div-tool-rule]
---

# DIV_CASE_RULE.md 요약

## 핵심 내용

배당 tool의 성공/실패 판정 기준. AGM CASE_RULE과 동일한 3등급(SUCCESS/SOFT_FAIL/HARD_FAIL) 체계.

## 판정 기준

### div_detail
- SUCCESS: cash_dps > 0, total_amount > 0
- SOFT_FAIL: DPS는 있지만 배당성향/수익률 없음
- HARD_FAIL: 데이터 없음 (배당 미실시 기업이면 정상)

### div_history
- SUCCESS: 2년+ 데이터, annual_dps > 0
- SOFT_FAIL: 1년만 있음
- HARD_FAIL: 전 기간 데이터 없음

### div_search
- SUCCESS: 배당 관련 공시 >= 1건
- HARD_FAIL: 0건

## 연산 주의사항

- [[배당성향]]: 지배주주 귀속 당기순이익 사용 (연결재무제표)
- 적자 시: "적자 배당" 문자열 표시 (음수% 아님)
- [[배당수익률]]: KRX 종가 없으면 DART 시가배당률 사용, 둘 다 없으면 "-"
- [[특별배당]]: 일회성이므로 추이 분석 시 정기배당과 분리 해석
