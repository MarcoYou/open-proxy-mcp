# OPM Wiki — LLM Knowledge Base

## 개요
OpenProxy MCP(OPM) 도메인 지식 위키. Karpathy LLM-wiki 아키텍처 기반.
LLM이 작성/유지하고, 사용자는 소싱과 질문에 집중.
OPM repo 안에 `wiki/` 디렉토리로 존재.

## 원본 소스 (raw)
별도 raw/ 디렉토리 없음. OPM repo의 기존 파일이 원본:
- `open_proxy_mcp/AGM_TOOL_RULE.md`, `AGM_CASE_RULE.md` — AGM 규칙
- `open_proxy_mcp/OWN_TOOL_RULE.md`, `OWN_CASE_RULE.md` — 지분 규칙
- `open_proxy_mcp/DIV_TOOL_RULE.md`, `DIV_CASE_RULE.md` — 배당 규칙
- `DEVLOG.md` — 개발 히스토리
- `README.md`, `README_KR.md` — 프로젝트 설명
- `test/benchmark_*.json` — 벤치마크 결과

LLM은 이 파일들을 읽기만 하고 수정하지 않음.

## 위키 구조
```
wiki/
  concepts/       # 도메인 개념 (배당, 의결권, 프록시파이트, 집중투표 등)
  entities/       # 엔티티 (DART API, KRX, 삼성전자, 국민연금 등)
  analysis/       # 분석 (파서 성능, tool 비교, 아키텍처 결정 등)
  sources/        # 원본 소스 요약 (1 소스 = 1 요약)
  index.md        # 전체 위키 인덱스
  log.md          # 작업 로그
  WIKI_SCHEMA.md  # 이 파일
```

## 페이지 타입

### concept (개념)
```yaml
---
type: concept
title: 배당성향
tags: [dividend, financial-metric]
related: [배당수익률, 당기순이익, 주주환원]
---
```

### entity (엔티티)
```yaml
---
type: entity
title: DART OpenAPI
tags: [api, data-source]
related: [KRX, KIND, alotMatter]
---
```

### analysis (분석)
```yaml
---
type: analysis
title: 경력 파서 벤치마크 2026-04
tags: [parser, personnel, benchmark]
---
```

### source (소스 요약)
```yaml
---
type: source
title: AGM_TOOL_RULE.md 요약
source_path: open_proxy_mcp/AGM_TOOL_RULE.md
---
```

## 워크플로우

### ingest
1. OPM에 새 RULE/문서 추가 시
2. LLM에게 "wiki ingest 해줘" 지시
3. LLM이 원본 읽고 → wiki/sources/ 요약 + concepts/entities/analysis 업데이트 + index.md 갱신

### query
1. 위키 대상으로 질문
2. LLM이 index.md → 관련 페이지 탐색 → 답변

### lint
- 페이지 간 모순, 고아 페이지, 누락 개념, 교차 참조 누락 점검

## 컨벤션
- 파일명: kebab-case, 한국어 OK
- 내부 링크: Obsidian wikilink `[[페이지명]]`
- 프론트매터: 모든 페이지에 YAML (type, title, tags, related)
- index.md: 모든 페이지 한 줄 요약 카탈로그
- log.md: `## [YYYY-MM-DD] ingest | 소스명` 형식

## Self-Learning 원칙

이 위키는 정적 문서가 아니라 **살아있는 지식 시스템**:

### 자동 학습 (/ship 연동)
- 코드 변경 시 `/ship`이 관련 위키 페이지 자동 업데이트
- 새 tool → concept/entity 페이지 생성/갱신
- 파서 개선 → analysis 벤치마크 수치 갱신
- 새 공시 연동 → disclosures/ 페이지 생성
- 변경 없으면 위키 안 건드림

### 토큰 절약
- CLAUDE.md는 최소한으로 유지 (~55줄)
- "상세는 wiki 참조"로 위임
- AI는 `wiki/index.md`만 먼저 읽고, 필요한 페이지만 선택적 로드
- 전체 위키를 한 번에 로드하지 않음

### 자기 개선 (lint)
- 주기적 건강 점검: 모순, 고아 페이지, 누락 개념, 교차 참조
- 새 세션에서 질문받으면 답변 후 위키에 새 인사이트 반영 가능
- 탐색 결과가 쌓여서 위키가 점점 풍부해짐

## 규칙
- 원본(RULE 파일 등)은 LLM이 수정하지 않음
- wiki/ 페이지는 LLM이 소유, 사용자는 읽기만
- .env, API 키 등 민감 정보 절대 넣지 않음
