---
type: decision
title: Tool Changelog — 제거/통합/리네임 이력
tags: [decision, tools, architecture]
date: 2026-04-11
---

# Tool Changelog

## 제거된 Tool

| Tool | 이유 | 대안 |
|------|------|------|
| `agm_document` | 원문 텍스트를 그대로 반환하는 저레벨 유틸. AI가 직접 파싱하기 어려운 raw 데이터. | `agm_items` (구조화된 안건 원문 블록) |
| `agm_extract` | `agm_items`와 기능 중복. 핵심 데이터포인트 추출이 `agm_items`에 포함됨. | `agm_items` |
| `agm_info` | 공시 기본정보(회사명, 날짜, rcept_no)만 반환. `agm` 오케스트레이터가 동일 정보 포함. | `agm_pre_analysis` 또는 `agm_search` 결과 |

## 통합된 Tool (manual → opm_guide)

5개 도메인 manual이 `opm_guide` 하나로 통합됨.

| 제거된 Tool | 이유 | 통합된 곳 |
|------------|------|-----------|
| `agm_manual` | RULE.md 파일을 그대로 읽어서 반환만 함. `opm_guide`가 동일 정보를 더 구조적으로 제공. | `opm_guide(domain="agm")` |
| `own_manual` | 동일 이유. | `opm_guide(domain="own")` |
| `div_manual` | 동일 이유. | `opm_guide(domain="div")` |
| `prx_manual` | 동일 이유. | `opm_guide(domain="prx")` |
| `corp_manual` | 동일 이유. alias dict, 동명기업 처리 등 포함. | `opm_guide(domain="corp")` |

## 통합된 Tool (기능 통합)

| 제거된 Tool | 이유 | 통합된 곳 |
|------------|------|-----------|
| `own` | `own_full_analysis`와 기능 95% 중복. `own_full_analysis`가 사업보고서 vs 수시공시 비교 테이블로 더 구조적. | `own_full_analysis` |
| `own_latest` | 유일한 고유 기능인 임원 주식 보유현황(`get_executive_holdings`)을 `own_full_analysis` 마지막 섹션으로 이전 후 제거. | `own_full_analysis` (임원 주식 섹션 추가됨) |

## 리네임

| 기존 이름 | 새 이름 | 이유 |
|-----------|---------|------|
| `agm` | `agm_pre_analysis` | 소집공고 기반 사전 분석임을 명확히. 투표결과 미포함. |
| `div` | `div_full_analysis` | 다른 체인 tool(`own_full_analysis`, `agm_post_analysis`)과 네이밍 일관성. |

## 신규 추가

| Tool | 이유 |
|------|------|
| `agm_post_analysis` | 주총 종료 후 사전(소집공고)+사후(투표결과) 통합 분석. `agm_pre_analysis + agm_result` 체이닝. |
| `opm_guide` | 5개 manual 통합. tier 체계 + canonical chain + 도메인 rule 한 곳에. |

## 최종 Tool 수

| 시점 | 개수 |
|------|------|
| 리팩토링 전 | 41개 |
| 리팩토링 후 | 32개 |
| 변화 | -9 (제거 10, 추가 1) |
