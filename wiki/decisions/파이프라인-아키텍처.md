---
type: decision
title: 파이프라인 아키텍처
tags: [pipeline, architecture, v4]
sources: [OPA_README.md, DEVLOG.md]
related: [OpenProxy-AI, v4-스키마, 3-tier-fallback, 시간순서-규칙]
---

# 파이프라인 아키텍처

## 개요

[[OpenProxy-AI]]의 배치 파이프라인. 199개 KOSPI 200 기업의 주총 데이터를 [[v4-스키마]] JSON으로 생성.

## 처리 흐름

```
filing_tracker.json (199개 기업 rcept_no)
  |
  v
run_pipeline.py
  |-- XML 파싱 (8개 파서)
  |-- PDF fallback (XML 실패 시)
  |-- OCR fallback (PDF 실패 시)
  |-- 투표결과 합치기 (KIND 크롤링)
  |
  v
A{code}_v4_parsed_{name}.json (199개)
```

## 핵심 규칙

- **전체 재실행 금지**: 누락분만 처리
- **캐시 활용**: XML/PDF 디스크 캐시, API 호출 최소화
- **시간순서**: 공고 데이터와 결과 데이터 분리 ([[시간순서-규칙]])
- **파일명 규칙**: _vX 버전 태그 금지, 개선 시 기존을 backup/으로 이동

## v4 스키마

v3에서 v4로 전환: compensation/voteResults 통합. 199/199 완료.

## 효율성

- 디스크 캐시로 [[DART-OpenAPI]] 호출 0회 가능 (이미 다운로드된 경우)
- _doc_cache (30건 LRU)로 동일 rcept_no 중복 호출 방지
- [[3-tier-fallback]] 전략에 따라 XML -> PDF -> OCR 순차 시도
