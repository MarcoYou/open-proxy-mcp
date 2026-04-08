---
type: entity
title: KRX KIND
tags: [data-source, krx, crawling]
related: [DART-OpenAPI, KIND-주총결과, 참석률, 배당수익률, 현금배당결정]
---

# KRX KIND

## 개요

한국거래소 기업공시채널(kind.krx.co.kr). [[DART-OpenAPI]]와 별도로 주총결과 등 거래소 고유 공시를 제공. [[OpenProxy-MCP]]에서 [[KIND-주총결과]] 크롤링의 소스.

## rcept_no → acptno 변환 규칙

**거래소 공시(pblntf_ty=I)는 100% "80"→"00" 변환으로 KIND viewer 접근 가능.**

```
DART rcept_no: YYYYMMDD80XXXX (거래소 공시)
KIND acptno:   YYYYMMDD00XXXX (같은 문서)
변환: rcept_no.replace("80", "00", 1)  # 8번째 이후 첫 "80"만
```

KOSPI 200 8개 기업 전수 확인 (삼성전자 86건, 현대차 45건, POSCO 79건, KB금융 100건, LG전자 58건, SK하이닉스 69건, NAVER 63건 등): **전부 100%.**

### 변환 가능 공시 유형 (거래소 공시)

| 공시 유형 | OPM 활용 |
|-----------|---------|
| [[주주총회결과]] | agm_result ✅ |
| [[현금배당결정]] | div_detail ✅ |
| 최대주주등소유주식변동신고서 | own_major 보강 가능 |
| 연결재무제표 잠정실적 | 향후 |
| 주식소각결정 | own_treasury 관련 |
| 대표이사 변경 | 거버넌스 향후 |
| 기업가치제고계획 | 거버넌스 향후 |

### 변환 불가 공시 유형 (DART 자체 공시)

| 공시 유형 | pblntf_ty | rcept_no 패턴 |
|-----------|-----------|--------------|
| [[주주총회소집공고]] | A (정기) | YYYYMMDD000XXX ("80" 없음) |
| [[사업보고서]] | A (정기) | YYYYMMDD000XXX |

## 접근 방식

### KIND viewer (httpx OK)
- `disclsviewer.do?method=search&acptno={acptno}` → docNo 추출 → searchContents → 본문 HTML
- acptno를 알면 httpx로 직접 접근 가능
- `kind_fetch_document(acptno)` in client.py

### KIND 검색 (httpx 불가)
- `details.do?method=searchDetailsMainSub` → 봇 감지로 차단
- ticker → acptno 매핑은 검색 없이 DART rcept_no 변환으로 해결

## KRX Open API (종가)

- `stk_bydd_trd` → TDD_CLSPRC: [[배당수익률]] 계산용 종가
- 전 서비스 승인 완료 (2026-04-08)
- `get_stock_price()`: KRX 우선, 네이버 fallback
