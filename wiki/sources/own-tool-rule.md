---
type: source
title: OWN_TOOL_RULE.md 요약
source_path: raw/rules/OWN_TOOL_RULE.md
ingested: 2026-04-05
tags: [ownership, tools, shareholder, treasury]
related: [지분구조, 최대주주, 5%-대량보유, 자사주, DART-OpenAPI]
---

# OWN_TOOL_RULE.md 요약

## 핵심 내용

지분구조 분석을 위한 7개 MCP tool의 구조, 출력 형태, 데이터 소스 우선순위를 정의.

## Tool 구조 (7개)

- `own(ticker)` - 오케스트레이터
- `own_major(ticker, year)` - 최대주주 + 특수관계인
- `own_total(ticker, year)` - 발행주식 / 자사주 / 유통주식 / 소액주주
- `own_treasury(ticker, year)` - 자사주 취득방법별 기초-취득-처분-소각-기말
- `own_treasury_tx(ticker)` - 자사주 이벤트
- `own_block(ticker)` - [[5%-대량보유]]자 (보유목적 포함)
- `own_latest(ticker, year)` - 통합 스냅샷

## 출력 형태

헤더 카드 (발행주식, 소액주주 수, 특관인 합계 지분율) + 주주 테이블 (주체/관계/지분율/기준날짜/비고).

## 표시 규칙

- 보통주 기준 지분율 (우선주 제외)
- 지분율 1% 미만 생략 가능
- 기준날짜가 다른 데이터 혼합 시 반드시 명시
- "계" 행은 합산에서 제외 (중복 방지)

## 데이터 소스 우선순위

1. **사업보고서** (own_major, own_total, own_treasury) - 연 1회 baseline
2. **수시 공시** (own_block, own_treasury_tx) - 변동 시 즉시
3. **own_latest** - 1+2 합산 스냅샷

## API 호출량

- own(종합): 5 + 보고자 수
- own_block: 1 + 보고자 수 (원문 파싱)
- own_treasury_tx: 4회 (DS005 4개 API)
