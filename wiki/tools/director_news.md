---
type: tool
title: director_news
domain: data
scope: [summary]
data_source: [네이버 뉴스 검색 API]
related_disclosures: [주주총회소집공고]
related_concepts: [이사 선임, 후보 적격성, 부정 뉴스]
created: 2026-08-20
updated: 2026-08-25
---

# director_news

## 한 줄 요약
이사 후보 이름으로 **부정 뉴스를 훑는다.** 주총 안건에서 「이 사람을 뽑아도 되나」를 볼 때
공시 밖 정보가 필요한 자리를 메운다.

## 배경
`shareholder_meeting_notice` 는 후보의 경력·겸직을 공시에서 준다. 그러나 횡령·배임·제재 같은
사건은 공시에 안 나오거나 뒤늦게 나온다. 의결권 판단에서 이 간극이 컸다.

## 사용법
- `director_news(name, ...)` — 후보 이름으로 검색하고 부정 키워드에 걸린 기사를 추린다.
- 키워드 목록은 `open_proxy_mcp/data/news/director_news_keywords.json` 에 있다.

## 하지 않는 것
찬반을 정하지 않는다. **기사를 추려 주는 데까지가 계약이다** — 동명이인 여부와 사실 확인은
이용자가 한다.

## 알려진 한계
- **동명이인을 가르지 못한다.** 흔한 이름일수록 오탐이 는다.
- 네이버 뉴스 색인 범위 밖(지역지·전문지)은 안 잡힌다.
