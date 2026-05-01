---
type: source
title: OPA_README.md 요약
source_path: raw/rules/OPA_README.md
ingested: 2026-04-05
tags: [opa, pipeline, frontend, private]
related: [OpenProxy-AI, OpenProxy-MCP, v4-스키마, 파이프라인-아키텍처]
---

# OPA_README.md 요약

## 핵심 내용

[[OpenProxy-AI]] (OPA)의 비공개 README. KOSPI 200 주총 분석 파이프라인 + 프론트엔드 대시보드. [[OpenProxy-MCP]]에서 파서/API 클라이언트를 패키지로 가져옴.

## 아키텍처

- **open-proxy-mcp (public)**: 파서, API 클라이언트 (pip install)
- **open-proxy-ai (private)**: 파이프라인(run_pipeline.py), 프론트엔드, 데이터

## 파이프라인

- filing_tracker.json 기반 199개 기업
- XML -> PDF -> OCR fallback + 투표결과 합치기
- 전체 재실행 금지, 누락분만 처리
- 출력: `A{code}_v4_parsed_{name}.json`

## [[v4-스키마]]

```
schemaVersion, meetingInfo, agendas[].keyData (compensation/candidates/charterChanges/financialStatements/treasuryStock), voteResults (items/attendance)
```

## 프론트엔드

- 199개 기업 리스트 + 시가총액 정렬
- 의안분석 (안건별 상세), 주총결과 (투표+참석률+집중투표)
- React + Vite, Radix UI

## 데이터 파일

- filing_tracker.json: 199개 기업 rcept_no 매핑
- market_cap.json: 시가총액 (네이버 금융)
- pipeline/: v4 parsed JSON (199개)
- pipeline_result/: KIND 투표결과 원본
