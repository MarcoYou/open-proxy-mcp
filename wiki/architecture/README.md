---
type: readme
title: architecture/ — 시스템 설계·데이터 수집·폴백·의결권 엔진
updated: 2026-06-23
---

# 시스템 설계 (architecture)

> OPM이 **어떻게 동작하나** — 데이터 수집 경로·폴백·동시성·의결권 판단·보고서 설계.
> 도메인 지식은 `rules/`, 도구 사용법은 `tools/`, 작업 회고는 `lessons/`.

## 무엇이 궁금한가 → 어디로

| 궁금한 것 | 문서 |
|---|---|
| **데이터를 어디서 어떻게 모으나** | [[data-collection]] · [[pipeline-architecture]] |
| **XML 실패하면?** (3단계 폴백) | [[3-tier-fallback]] (XML → PDF → OCR) |
| **여러 upstream 동시 호출 표준** | [[multi-upstream-pattern]] (concurrency + race fix) |
| **의결권을 어떻게 판단하나** | [[proxy-voting-decision-tree]] · [[matrix-system]] (설계자산, 자동채점은 미사용) |
| **proxy_advise Word 보고서** | [[proxy_advise_word_report_design]] · [[proxy_advise_word_report_spec]] |
| **파싱 성공률 audit 방법** | [[parsing_success_rate_audit_spec]] · [[parsing_success_rate_audit_checklist]] |
| **코드 구조** | [[project_structure]] |
| **환경변수·시크릿** (필요한 키 목록·설정 위치, `.env.example` 대체) | [[environment-secrets]] |
| **MCP 개발 교훈** | [[lessons-learned]] |
| **수정주가 타임시리즈** (기준가 리셋 실측 파이프라인 + 핸드오프) | [[adjusted-price-timeseries]] |

## 하위 폴더
- `audits/` — 데이터·파서 전수조사 기록 ([[audits/README]])
- `fixes/` — 설계·성능 시점 수정 기록 ([[fixes/README]])
- `goals/` — audit 목표·기준 정의 ([[goals/README]])
