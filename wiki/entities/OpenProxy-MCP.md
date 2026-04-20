---
type: entity
title: OpenProxy MCP (OPM)
tags: [project, mcp, open-source]
related: [OpenProxy-AI, DART-OpenAPI, 3-tier-fallback, FastMCP]
---

# OpenProxy MCP (OPM)

## 개요

AI 기반 MCP(Model Context Protocol) 서버. DART 주주총회 공시를 구조화된 AI-ready 데이터로 변환. 오픈소스 (CC BY-NC 4.0).

GitHub: https://github.com/MarcoYou/open-proxy-mcp

현재 버전: **v2.0.0** (2026-04-19 릴리즈). v1은 `open-proxy-mcp-v1.3.0` 브랜치에 보존.

## 기술 스택

- Python + [[FastMCP]] + httpx
- [[DART-OpenAPI]] + [[KRX-KIND]] 크롤링
- Fly.io (nrt 리전, streamable-http)

## Tool 구성 (13개, v2)

```
company                       # 진입점 (1개 기업 식별)
├─ Discovery Tool (1)
│  └─ screen_events           # 이벤트 → N개 기업 역조회 (14 event_type)
├─ Data Tools (8)
│  ├─ shareholder_meeting     # 주총 (안건/후보/보수/정관/결과)
│  ├─ ownership_structure     # 지분 구조 + control map + changes
│  ├─ dividend                # 배당 사실
│  ├─ treasury_share          # 자사주 이벤트
│  ├─ proxy_contest           # 경영권 분쟁
│  ├─ value_up                # 밸류업 계획
│  ├─ corporate_restructuring # 합병/분할/분할합병/주식교환·이전 결정
│  └─ evidence                # 공시 원문 링크
└─ Action Tools (3)
   ├─ prepare_vote_brief
   ├─ prepare_engagement_case
   └─ build_campaign_brief
```

### 아키텍처 패턴 (v2)

- **tools_v2 / services 분리**: tool은 MCP 인터페이스, services는 도메인 분석 로직
- **scope 기반 drill-down**: summary → board/compensation/results 순차 확장
- **3-tier fallback 제거**: XML + KIND Viewer 기반, PDF 다운로드 기본 경로 제외
- **[[proxy-voting-decision-tree]]**: prepare_vote_brief에서 FOR/AGAINST/REVIEW 판정

### v1 vs v2

| | v1 | v2 |
|--|----|----|
| Tool 수 | 36개 | 13개 |
| 구조 | 5-Tier | Data + Action |
| 파싱 레이어 | tool 내부 | services/ 분리 |

## 프로젝트 구조

```
open_proxy_mcp/
  server.py           # FastMCP entry point (OPEN_PROXY_TOOLSET 분기)
  tools_v2/           # 13개 MCP tool (v2)
  services/           # 도메인 분석 로직
  tools/              # 36개 tool (v1, 보존)
  dart/client.py      # DART + KRX + Naver API client
```

## 연결

Claude.ai 웹 커넥터 (streamable-http):
```
https://open-proxy-mcp.fly.dev/mcp?opendart=API_KEY
```
