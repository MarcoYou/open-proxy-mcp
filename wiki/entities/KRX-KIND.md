---
type: entity
title: KRX KIND
tags: [data-source, krx, crawling]
related: [DART-OpenAPI, KIND-주총결과, 참석률]
---

# KRX KIND

## 개요

한국거래소 기업공시채널(kind.krx.co.kr). DART와 별도로 주총결과 등 거래소 고유 공시를 제공.

## OPM에서의 활용

### 주총결과 크롤링
- agm_result tool이 KIND에서 정기주주총회결과 HTML을 크롤링
- 섹션 분리: 주총결과(100%), 안건세부(100%), 감사위원(85%), 집중투표(62%)

### rcept_no -> acptno 변환
- DART rcept_no의 8번째 이후 "80" -> "00"으로 변환
- 단, 모든 경우에 정확하지는 않음 (별개 번호체계)

### KRX 종가 API
- stk_bydd_trd -> TDD_CLSPRC: 배당수익률 계산용 종가

## 크롤링 제약

- 세션/쿠키 필요
- Rate limit 엄격
- Selenium 방식이 안정적 (httpx 직접 호출보다)
- 랜덤 간격: 2-5초 요청별, 15-30초 배치별

## KOSPI 200 전수 크롤링 결과

199/199 기업 전부 정상 크롤링 완료.
