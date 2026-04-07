---
type: entity
title: OpenProxy AI (OPA)
tags: [project, pipeline, frontend, private]
related: [OpenProxy-MCP, v4-스키마, 파이프라인-아키텍처]
---

# OpenProxy AI (OPA)

## 개요

비공개 프로젝트. KOSPI 200 주총 분석 파이프라인 + 프론트엔드 대시보드. [[OpenProxy-MCP]]에서 파서/API를 pip 패키지로 가져옴.

## 구성

- **pipeline/**: run_pipeline.py - [[3-tier-fallback]] 자동 체이닝, 199개 기업 [[v4-스키마]] JSON 생성. [[파이프라인-아키텍처]] 참조
- **frontend/**: React + Vite 대시보드, 의안분석 + 주총결과 탭
- **data/**: filing_tracker.json, market_cap.json

## v4 JSON 스키마

```
schemaVersion, meetingInfo, agendas[].keyData, voteResults
```

## 데이터 현황

- 199/199 기업 v4 JSON 완료
- 투표결과 통합 완료
- 시가총액 정렬 제공

## 규칙

- 파이프라인 전체 재실행 금지 (누락분만)
- 파일명에 _vX 버전 태그 금지
- 백그라운드 파이프라인 실행 금지 (좀비 방지)
