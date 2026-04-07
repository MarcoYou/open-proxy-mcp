---
type: source
title: DIV_TOOL_RULE.md 요약
source_path: raw/rules/DIV_TOOL_RULE.md
ingested: 2026-04-05
tags: [dividend, tools, dps, payout-ratio, yield]
related: [배당성향, 배당수익률, 시가배당률, 분기배당, 특별배당, DART-OpenAPI]
---

# DIV_TOOL_RULE.md 요약

## 핵심 내용

배당 분석을 위한 5개 MCP tool의 구조와 연산 규칙을 정의. 배당성향/수익률 계산의 정확한 기준을 명시.

## Tool 구조 (5개)

- `div(ticker)` - 오케스트레이터 (최신 상세 + 3년 추이)
- `div_search(ticker)` - 배당 관련 공시 검색
- `div_detail(ticker, bsns_year, reprt_code)` - 배당 상세
- `div_history(ticker, years)` - 배당 이력
- `div_manual()` - 규칙 문서 반환

## 핵심 연산 규칙

### [[배당성향]] (Payout Ratio)
- 배당금 총액 / **지배주주 귀속 당기순이익** x 100
- 반드시 연결재무제표 기준. 별도 재무제표 사용 금지
- DART alotMatter 값 우선 사용

### [[배당수익률]] / [[시가배당률]]
- DART 공식: 1주당 배당금 / 기준주가(배당기준일 전전거래일부터 1주일 평균 종가)
- 자체 계산: 연간 DPS / 배당기준일 종가 (네이버 금융)

### DPS 데이터 소스 주의
- DART alotMatter(사업보고서): **연간 합산** DPS
- 현금배당결정 공시: **해당 회차분만**
- 삼성전자 2025: 1Q 366 + 2Q 366 + 3Q 370 + 결산 566 = 1,668원

### [[분기배당]]
- 연간 DPS = 1Q + 반기 + 3Q + 기말 (각 분기분만, 누적 아님)
- reprt_code로 분기 구분: 11013(1Q), 11012(반기), 11014(3Q)

### [[특별배당]]
- 연간 DPS에 포함하되 별도 행 표시
- 배당성향 계산에 포함, "특별배당 포함" 주석 필요

## API 호출량

- div_history: 연도당 최대 4회 DART API (기말+3분기). 3년 = 최대 12회
- div_detail: alotMatter x 1
