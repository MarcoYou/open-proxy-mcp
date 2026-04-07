---
type: source
title: OWN_CASE_RULE.md 요약
source_path: raw/rules/OWN_CASE_RULE.md
ingested: 2026-04-05
tags: [ownership, case-rule]
related: [지분구조, 최대주주, 5%-대량보유, 자사주, 파서-판정-등급]
---

# OWN_CASE_RULE.md 요약

## 핵심 내용

지분 tool 6개의 성공/실패 판정 기준. AGM/DIV와 동일한 3등급 체계.

## 판정 기준

### own_major
- SUCCESS: list >= 1, 최대주주(본인) 존재, 지분율 양수
- HARD_FAIL: list 비어있음 (사업보고서 미제출 기업)

### own_total
- SUCCESS: 보통주 행 존재, istc_totqy > 0
- HARD_FAIL: list 비어있음

### own_treasury
- SUCCESS: 기말 보유 수량 존재
- SOFT_FAIL: 수량 0 (자사주 없는 기업이면 정상)

### own_block
- SUCCESS: list >= 1, 보유목적 파싱 성공
- SOFT_FAIL: 보유목적이 "불명"
- HARD_FAIL: list 비어있음 (5% 이상 보유자 없으면 정상)

### own_latest
- SUCCESS: major + block 모두 존재
- SOFT_FAIL: 한쪽만 존재

## 공통 규칙

- 안건이 없어서 비어있는 것은 실패가 아님
- 보통주 기준 지분율
- stlm_dt(사업보고서)과 rcept_dt(수시 공시) 구분 필수
