---
type: concept
title: DART OpenAPI (opendart.fss.or.kr)
tags: [data-source, dart, api, rate-limit]
related: [KRX-KIND, 네이버-금융, 공시유형코드체계, 사업보고서]
---

# DART OpenAPI

## 무엇인가

금융감독원 전자공시(DART)의 공개 API(`https://opendart.fss.or.kr/api/...`). 공시 검색(`list.json`)·기업코드(`corpCode.xml`)·원문 ZIP(`document.xml`)과 정기·수시·주요사항보고 항목별 정형 endpoint(DS001~DS005)를 준다. 이용자가 발급받은 **API 키**로 호출한다.

## OPM 이 무엇에 쓰나

- **1순위 데이터 소스.** 정형 endpoint(지분·배당·자기주식·재무제표·임원·주요사항)가 있으면 그것을 쓰고, 없을 때만 `document.xml` 원문을 파싱한다. 원문에서도 못 풀면 DART 웹 viewer, 그다음 [[KRX-KIND]] 순서로 내려간다 — 상위에서 해결되면 하위는 호출하지 않는다.
- 검색은 `pblntf_ty`·`pblntf_detail_ty` 로 범위를 먼저 좁힌다([[공시유형코드체계]]). `rcept_no` 는 `00`=DART 정기·`80`=거래소 수시.
- 사용자 키는 요청마다 `?opendart=<키>` 로 들어오고, 키가 든 URL·예외는 prefix 조차 로그에 남기지 않는다.

## 한도 (하드룰)

| 항목 | 값 |
|---|---|
| 분당 한도 | 공표 1,000회/키 — OPM cap **910**(`_API_RATE_LIMIT_PER_MINUTE`, 9% 여유) |
| 초과 시 | **그 키가 차단**된다 — 실측 2~3시간(종전 문서의 「24h IP 차단」은 근거 없던 값) |
| 격리 단위 | **키마다.** 한 사용자의 과다 호출이 다른 사용자를 막지 않는다. 위험한 쪽은 배치·스크립트가 쓰는 우리 자신의 키 |
| 스로틀 | 키별 클라이언트 인스턴스가 각자 갖는다 — 같은 키로 프로세스가 둘이면 합이 1,820. 배치 재시작 전 이전 프로세스 종료 확인 |
| 배치 | 최대 30사 + 사이 sleep · 독립 스크립트는 동시성 1~2 + `ReadError` 즉시 중단 |

## 상세

endpoint 전수·파싱 방법·캐시 정책은 `decisions/data-collection.md`(1장), 검색 코드 매핑은 [[공시유형코드체계]], 원문 우선 결정은 `decisions/XML-vs-PDF.md`.
