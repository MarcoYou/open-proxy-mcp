---
type: entity
title: Upstage OCR
tags: [api, ocr, fallback, upstage]
related: [3-tier-fallback, DART-OpenAPI]
---

# Upstage OCR

## 개요

Upstage Document Parse API. PDF/이미지를 시각적으로 파싱하여 텍스트를 추출하는 OCR 서비스. [[3-tier-fallback]]의 3번째(최종) tier.

## OPM에서의 활용

- `agm_*_ocr` tool 8개의 백엔드
- XML/PDF 파서가 모두 실패한 케이스에서 최후의 수단
- UPSTAGE_API_KEY가 .env에 필요 (유료)

## 성능

| 파서 | OCR 성공률 |
|------|-----------|
| 모든 파서 | 100% (XML/PDF 실패 11건 전부 OCR 성공) |

## 처리 흐름

1. opendataloader(PDF 변환)가 실패한 경우
2. 키워드로 PDF 내 관련 페이지 특정
3. Upstage OCR로 해당 페이지 파싱
4. 추출된 텍스트를 기존 파서에 재투입

## 속도

- 10s+ (가장 느림)
- XML(빠름) -> PDF(4s+) -> OCR(10s+) 순으로 비용 증가
