---
type: analysis
title: KIND 검색 API 조사
tags: [kind, crawling, api, investigation]
related: [KRX-KIND, DART-OpenAPI, 주주총회소집공고, 주주총회결과]
---

# KIND 검색 API 조사 (2026-04-08)

## 결론
KIND 검색 endpoint (`details.do?method=searchDetailsMainSub`)는 httpx로 접근 가능하나, 폼 직렬화를 정확히 해야 함. 요청 빈도 초과 시 rate limit 차단 (시간 경과 후 해제).

## 발견 사항

### 1. ajaxSubmit은 단순 $.ajax wrapper
`common-ajax.js`의 `ajaxSubmit` 함수 분석 결과:
- 추가 토큰, CSRF, 커스텀 헤더 없음
- `$(this).serialize()`로 폼 직렬화 → `$.ajax` POST
- **JS 렌더링이 아니라 표준 AJAX 요청**

### 2. 폼 필드명 차이
- 날짜: `fromDate`/`toDate` (NOT `startDate`/`endDate`)
- 323개 hidden input 존재 — `$(this).serialize()`가 전부 보냄
- 중복 name 다수 (disclosureTypeArr01 등)

### 3. Rate Limit
- 짧은 시간에 여러 요청 시 "잠시 후 다시 이용해 주세요" 에러 (1472 bytes)
- IP 차단이 아님 (브라우저에서는 접속 가능)
- 시간 경과 후 해제 (정확한 해제 시간 미확인)

### 4. KIND viewer는 별개
- `disclsviewer.do?method=search&acptno=` — 검색과 무관하게 작동
- acptno만 알면 바로 접근 가능
- 단, DART rcept_no ≠ KIND acptno (변환 불가)

## 다음 단계
1. rate limit 해제 후 full form serialize로 1건 테스트
2. 성공 시 ticker → acptno 매핑 확보
3. `kind_search_agm_notice(ticker)` 구현

## 관련 파일
- `/Users/marcoyou/Projects/open-proxy-mcp/open_proxy_mcp/dart/client.py` — `kind_fetch_document()`
- `https://kind.krx.co.kr/js/common-ajax.js` — `ajaxSubmit` 함수
