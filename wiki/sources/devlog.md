---
type: source
title: DEVLOG.md 요약
source_path: raw/devlog/DEVLOG.md
ingested: 2026-04-05
tags: [devlog, history, architecture, parser]
related: [OpenProxy-MCP, OpenProxy-AI, 3-tier-fallback, 파서-성능-추이, BeautifulSoup-파서-선택, LLM-fallback-설계, XML-vs-PDF, free-paid-분리]
---

# DEVLOG.md 요약

## 핵심 내용

2026-03-19부터 2026-04-06까지의 OPM 개발 히스토리. 프로젝트 초기 설정부터 KOSPI 200 전수 파싱 완료까지.

## 주요 마일스톤 (시간순)

### 2026-03-19 - 프로젝트 초기 설정
- GitHub 레포 생성, FastMCP + httpx + OpenDART 스택 결정
- dart-mcp, Kensho, FactSet 참고 프로젝트 리서치
- DartClient 구현, corpCode.xml 캐싱

### 2026-03-20 - 안건 파서 구현
- 정정공고 중복 파싱 해결 (_strip_correction_preamble)
- 155개 기업 테스트: 정규식만으로 90%
- 하이브리드 LLM fallback 구현 (gpt-5.4-mini)

### 2026-03-21 - 상세 파서 완성
- agm_financials (BS/IS), agm_personnel (경력분리), agm_aoi_change
- bs4 + lxml 도입, HTML 구조 직접 활용
- 안건 트리 87% -> 93% (250건, regression 0)

### 2026-03-22 - 811건 배치 테스트
- KOSPI 200 포함 전수 테스트 인프라 (test_batch.py)
- agenda 93% -> 97-98%, 5가지 파서 개선
- API 아키텍처 분석: _doc_cache(30건 LRU) 효과적

### 2026-03-23 - KOSPI 200 검증
- agenda 99.5%, financials 97.4%, personnel 98.9%, aoi 97.8%
- 199개 기업 pipeline JSON 생성
- 디스크 캐시 추가

### 2026-03-28 - PDF fallback
- agm_compensation 신규 (98.4%)
- opendataloader PDF 파싱: XML 실패 케이스에서 PDF 유효 데이터 추출 성공
- CASE_DEFINITION.md 작성 (판정 기준)

### 2026-03-29 - PDF 파서 + OCR fallback
- 5개 PDF 파서 구현 (comp/pers/fin/aoi/agenda)
- Upstage OCR: 11건 실패 전부 OCR 성공 (100%)
- treasury_share/capital_reserve/retirement_pay 추가

### 2026-03-31 - Ownership tool 7개
- own_* 지분 구조 tool 개발 (DART API 11개 활용)
- KOSPI 200 전수: 199/199 OK (205초)
- KT&G 집중투표 사례 분석

### 2026-04-01 - KIND 크롤링 + 주총결과
- agm_result tool: KIND 크롤링 -> 투표결과 + 참석률 역산
- KOSPI 200 전수: 평균 참석률 73.3%, 최소 30.4%(호텔신라), 최대 94.3%(삼성카드)
- 40개 tool 완성

### 2026-04-04 - free/paid 2-repo 분리
- open-proxy-mcp (public) / open-proxy-ai (private)

### 2026-04-06 - 문서 구조 개편 + 경력 파서 개선
- CASE_DEFINITION -> CASE_RULE, AGM_MANUAL -> TOOL_RULE 리네임
- 경력 병합 분리: 現/前 구분자 + 연도 토큰 할당
- KOSPI 200 벤치마크: 878명 중 697 SUCCESS / 103 SOFT_FAIL / 78 HARD_FAIL

## 핵심 아키텍처 결정

- [[XML-vs-PDF]]: XML 1차 + PDF 보강이 최적 (PDF-only는 financials/agenda에서 역효과)
- [[BeautifulSoup-파서-선택]]: lxml 채택 (html.parser 대비 30% 빠름, 결과 동일)
- [[LLM-fallback-설계]]: 정규식 -> zone 추출 -> LLM (gpt-5.4-mini), 토큰 최소화
- [[free-paid-분리]]: MCP(free) + Pipeline+Frontend(paid) 2-repo 구조
