---
type: concept
title: 3-Tier Fallback
tags: [architecture, parser, fallback]
related: [DART-OpenAPI, Upstage-OCR, 파서-판정-등급, XML-vs-PDF, agm-case-rule]
---

# 3-Tier Fallback

> ⚠️ **2026-07-12 변경 — OPM은 XML 단독.** PDF 다운로드·OCR(Upstage)·opendataloader 폴백은
> OPM에서 폐기하고 고급 프로덕트 **open-proxy-ai**(`/Users/marcoyou/Projects/open-proxy-ai`,
> `pipeline/pdf_parser.py` + `pipeline/pdf_download.py`)로 이관했다. 아래 3단계 전략은 이제
> **open-proxy-ai(폴백 전용 프로덕트)의 아키텍처**로만 유효하며, OPM은 `_xml` tier(XML 단독)만
> 제공한다. OPM에서 XML이 불완전하면 원문을 AI에 노출해 보정(soft-fail)하고, 조작된 FOR는 내지 않는다.

## 개념

거버넌스 분석 파이프라인의 핵심 패턴. 8개 AGM 파서 각각이 3단계 소스를 순차적으로 시도하여 데이터 품질을
보장하는 전략. **OPM은 `_xml` tier만, PDF/OCR tier는 open-proxy-ai에서 수행한다.**

## 3단계 구조

| Tier | 소스 | 속도 | 정확도 | 비용 | 위치 |
|------|------|------|--------|------|------|
| `_xml` | DART API (HTML/XML) | 빠름 | 98%+ | 무료 | **OPM + open-proxy-ai** |
| `_pdf` | PDF + opendataloader | 4s+ | 98%+ | 무료 | open-proxy-ai 전용 |
| `_ocr` | [[Upstage-OCR]] API | 10s+ | 100% | 유료 (API 키 필요) | open-proxy-ai 전용 |

## 흐름 (open-proxy-ai 파이프라인)

1. `agm_*_xml`(OPM) 호출
2. 결과를 [[파서-판정-등급]] 기준으로 검증
3. SUCCESS -> 즉시 답변 (AI가 포맷 보정 가능)
4. SOFT_FAIL -> AI 자체 보정 시도 (구분자 분리, 누락 추론 등)
5. (open-proxy-ai) 보정 불가 -> PDF fallback (`pipeline/pdf_download.py` + `pipeline/pdf_parser.py`)
6. (open-proxy-ai) PDF도 부족 -> OCR fallback (Upstage)
7. OCR도 실패 -> AI가 원문 기반으로 직접 재구성

**OPM 단독 경로**: 4단계까지만. 보정 불가 시 한계를 명시하고 답변한다(PDF/OCR 없음).

## 거버넌스 분석에서의 의미

주총 소집공고는 기업마다 형식이 다르고, DART의 HTML/XML 변환 품질도 일정하지 않음. 단일 파싱 전략으로는
98% 이상 커버가 불가능. open-proxy-ai는 3단계 fallback으로 100%에 근접하는 커버리지를 달성하고, OPM(오픈소스
MCP)은 XML tier + AI 원문 보정으로 실시간 조회 범위를 커버한다.

## free vs paid 차이

- **free (OPM, MCP)**: XML tier 단독. AI가 유저와 대화하며 원문 노출로 보정. PDF/OCR 없음.
- **paid (open-proxy-ai 파이프라인)**: XML -> PDF -> OCR -> LLM 자동 체이닝. 배치로 최선 데이터 미리 생성.

## 관련 데이터

[[agm-case-rule]]에서 파서별 tier 독립 성능 확인 가능.
