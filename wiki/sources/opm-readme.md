---
type: source
title: OPM_README.md 요약
source_path: raw/rules/OPM_README.md
ingested: 2026-04-05
tags: [opm, mcp, open-source, architecture]
related: [OpenProxy-MCP, 3-tier-fallback, DART-OpenAPI, KRX-KIND]
---

# OPM_README.md 요약

## 핵심 내용

[[OpenProxy-MCP]] (OPM)의 공개 README. AI 기반 MCP 서버로, DART 주주총회 공시를 구조화된 데이터로 변환.

## 프로젝트 목적

패시브 투자 확대로 의결권(proxy voting)의 중요성 증대. 수작업으로 분석되던 AGM 공시를 AI가 즉시 구조화하여 모든 투자자가 접근 가능하게 만듦.

## Tool 구성 (40개)

- AGM 33개: 오케스트레이터 + Search/Meta + 8 Parsers x 3 Tiers + 결과
- Ownership 7개: 지분구조 분석

## [[3-tier-fallback]]

| Tier | Source | Speed | Accuracy |
|------|--------|-------|----------|
| _xml | DART API (HTML/XML) | Fast | 98%+ |
| _pdf | PDF + opendataloader | 4s+ | 98%+ |
| _ocr | Upstage OCR API | Slowest | 100% |

## 프로젝트 구조

```
open_proxy_mcp/
  server.py        # FastMCP entry point
  tools/           # shareholder.py, ownership.py, parser.py, pdf_parser.py
  dart/client.py   # OpenDART API + KIND crawling
  llm/client.py    # LLM fallback
```

## 라이선스

CC BY-NC 4.0 (비상업적 사용)
