---
type: readme
title: architecture/ — 시스템 설계·데이터 수집·폴백·의결권 엔진
updated: 2026-06-23
---

# 시스템 설계 (architecture)

> OPM이 **어떻게 동작하나** — 데이터 수집 경로·폴백·동시성·의결권 판단·보고서 설계.
> 도메인 지식은 `rules/`, 도구 사용법은 `tools/`. 작업 회고·레슨런은 public wiki 에 없다(private storage).

## 무엇이 궁금한가 → 어디로

| 궁금한 것 | 문서 |
|---|---|
| **데이터를 어디서 어떻게 모으나** | [[data-collection]] · pipeline-architecture |
| **XML 실패하면?** | [[3-tier-fallback]] (OPM은 XML 단독 + AI 원문 보정; PDF/OCR 폴백은 open-proxy-ai로 이관 260712) |
| **여러 upstream 동시 호출 표준** | [[multi-upstream-pattern]] (concurrency + race fix) |
| **의결권을 어떻게 판단하나** | [[proxy-voting-decision-tree]] · 의사결정 매트릭스(설계자산 — **구조**: 안건 카테고리 × 판단 차원 그리드 + 패턴 카탈로그로 for/review/against 산출. 산식·패턴 실체는 private, 자동채점 미사용) |
| **proxy_advise Word 보고서** | [[proxy_advise_word_report_design]] · [[proxy_advise_word_report_spec]] |
| **코드 구조** | [[project_structure]] |
| **MCP 엔드포인트** (live-opm / pilot-opm — 목적이 다르고 따로 관리, stdio 금지) | [[mcp-endpoints]] |
| **환경변수·시크릿** (필요한 키 목록·설정 위치, `.env.example` 대체) | [[environment-secrets]] |
| **PER·PBR 데이터 포인트 전수조사** (보통주·우선주 실측 인벤토리, EPS 조립 경로) | per-pbr-data-points |
| **수정주가 타임시리즈** (기준가 리셋 실측 파이프라인 + 핸드오프) | adjusted-price-timeseries |

## 하위 폴더
- `fixes/` — 설계·성능 시점 수정 기록 ([[architecture/fixes/README]])

> `audits/`(전수조사 기록)·`goals/`(audit 목표 정의)·`lessons-learned`(MCP 개발 회고)는 260806
> private storage 로 이관했다 — `open-proxy-storage/wiki-private/architecture/{audits,goals}/` ·
> `wiki-private/lessons/mcp-development-260419.md`. 시점 작업 기록은 storage, 현재형 사실만 여기
> ([[wiki_schema]] §0.0).
