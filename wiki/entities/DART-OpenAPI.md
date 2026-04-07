---
type: entity
title: DART OpenAPI
tags: [api, data-source, dart, fss]
related: [KRX-KIND, 네이버-금융, OpenProxy-MCP, alotMatter]
---

# DART OpenAPI

## 개요

금융감독원 전자공시시스템(dart.fss.or.kr)의 오픈 API. 한국 상장기업의 공시 정보를 프로그래매틱하게 조회할 수 있는 핵심 데이터 소스.

홈페이지: https://opendart.fss.or.kr/

## OPM에서 사용하는 API

### DS001 - 공시검색
- search_filings: 주주총회소집공고 검색 (pblntf_ty=E)

### DS002 - 정기보고서 (지분)
- hyslrSttus: 최대주주 현황
- hyslrChgSttus: 최대주주 변동
- mrhlSttus: 소액주주 현황
- tesstkAcqsDspsSttus: 자사주 취득/처분
- stockTotqySttus: 주식총수

### DS003 - 사업보고서 (배당)
- alotMatter: 배당 상세 (DPS, 총액, 성향, 수익률)

### DS004 - 수시보고 (지분)
- majorstock: 5% 대량보유자
- elestock: 임원 소유

### DS005 - 주요사항 (자사주 이벤트)
- tsstkAqDecsn, tsstkDpDecsn, tsstkAqTrctrCnsDecsn, tsstkAqTrctrCcDecsn

### 문서 관련
- get_document: document.xml ZIP -> XML -> 텍스트/HTML 추출
- corpCode.xml: 종목코드/회사명 -> corp_code 변환 (캐싱)

## API 제한

- 일일 한도 2만건
- 속도 제한: 빠른 연속 호출 시 IP 차단
- 이중 키 운영: OPENDART_API_KEY + OPENDART_API_KEY_2 (자동 전환)

## 특이사항

- majorstock API에 보유목적 필드 없음 -> document.xml PUR_OWN 태그로 해결
- rcept_no: 정정공고 발행 시 기존 번호 무효화
- DART rcept_no와 KIND acptno는 별개 번호체계
