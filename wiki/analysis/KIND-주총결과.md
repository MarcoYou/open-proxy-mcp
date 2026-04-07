---
type: analysis
title: KIND 주총결과 파싱
tags: [kind, crawling, voting, attendance]
sources: [DEVLOG.md, AGM_TOOL_RULE.md]
related: [KRX-KIND, 참석률, 감사위원-의결권-제한, 집중투표]
---

# KIND 주총결과 파싱

## 개요

KRX KIND에서 정기주주총회결과 HTML을 크롤링하여 투표결과와 참석률을 추출하는 분석.

## 크롤링 구조

- DART rcept_no -> KIND acptno 변환 (8번째 이후 "80" -> "00")
- searchDetailsMainSub + reportNm=주주총회소집공고
- 섹션 분리: span 태그 기반

## 섹션 커버리지 (KOSPI 200)

| 섹션 | 커버리지 |
|------|----------|
| 주총결과 | 100% |
| 안건세부 | 100% |
| 감사위원 | 85% |
| 집중투표 | 62% |

## 참석률 역산

```
전체 참석률 = 발행기준 찬성률 / 행사기준 찬성률
```

- 보통결의 안건 중 최빈값이 대표 참석률
- 감사위원 안건은 3% 의결권 제한으로 분모 변경

## KOSPI 200 참석률 통계

- 평균 73.3%, 중위 75.1%
- 최소 30.4% (호텔신라), 최대 94.3% (삼성카드)
- 삼성전자: 전체 74.0%, 일반주주 68.9%

## 한계

- 정확한 행사 주식수는 DART 미공시 -> 추정치
- KIND는 세션/쿠키 필요, rate limit 엄격
