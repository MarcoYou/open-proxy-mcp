---
type: concept
title: 3-Tier Fallback
tags: [architecture, parser, fallback]
related: [DART-OpenAPI, Upstage-OCR, 파서-판정-등급, XML-vs-PDF, agm-case-rule]
---

# 3-Tier Fallback

## 개념

OPM의 핵심 아키텍처 패턴. 8개 AGM 파서 각각이 3단계 소스를 순차적으로 시도하여 데이터 품질을 보장하는 전략.

## 3단계 구조

| Tier | 소스 | 속도 | 정확도 | 비용 |
|------|------|------|--------|------|
| `_xml` | DART API (HTML/XML) | 빠름 | 98%+ | 무료 |
| `_pdf` | PDF + opendataloader | 4s+ | 98%+ | 무료 |
| `_ocr` | [[Upstage-OCR]] API | 10s+ | 100% | 유료 (API 키 필요) |

## 흐름

1. AI가 `agm_*_xml` 호출
2. 결과를 [[파서-판정-등급]] 기준으로 검증
3. SUCCESS -> 즉시 답변 (AI가 포맷 보정 가능)
4. SOFT_FAIL -> AI 자체 보정 시도 (구분자 분리, 누락 추론 등)
5. 보정 불가 -> 유저에게 PDF fallback 제안
6. PDF도 부족 -> 유저에게 OCR fallback 제안
7. OCR도 실패 -> AI가 원문 기반으로 직접 재구성

## 거버넌스 분석에서의 의미

주총 소집공고는 기업마다 형식이 다르고, DART의 HTML/XML 변환 품질도 일정하지 않음. 단일 파싱 전략으로는 98% 이상 커버가 불가능하며, 3단계 fallback으로 100%에 근접하는 커버리지를 달성.

## free vs paid 차이

- **free (MCP)**: AI가 유저와 대화하면서 점진적 fallback. 유저 동의 필요.
- **paid (파이프라인)**: XML -> PDF -> OCR -> LLM 자동 체이닝. 배치로 최선 데이터 미리 생성.

## 관련 데이터

[[agm-case-rule]]에서 파서별 tier 독립 성능 확인 가능.
