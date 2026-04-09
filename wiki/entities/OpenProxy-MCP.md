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

## 기술 스택

- Python + [[FastMCP]] + httpx
- BeautifulSoup (lxml 파서) + regex
- [[DART-OpenAPI]] + [[KRX-KIND]] 크롤링

## Tool 구성 (33개)

- AGM 18개: 8 XML 파서 + agm_parse_fallback(Dispatch Table) + 오케스트레이터/검색/메타. [[agm-tool-rule]] 참조
- OWN 9개: [[지분구조]] 분석 + own_full_analysis(Chain Tool). [[own-tool-rule]] 참조
- DIV 5개: [[배당성향|배당]] 분석. [[div-tool-rule]] 참조
- NEWS 1개: [[news_check]] 후보자 부정 뉴스 검색 (네이버 API)

### 아키텍처 패턴
- **Dispatch Table**: 16 PDF/OCR tool → agm_parse_fallback 1개로 통합 (parser+tier 파라미터)
- **Chain Tool**: own_full_analysis = own + div_history + treasury_tx 체이닝
- **[[3-tier-fallback]]**: XML → PDF → OCR
- **[[proxy-voting-decision-tree]]**: FOR/AGAINST/REVIEW 판정

## 프로젝트 구조

```
open_proxy_mcp/
  server.py           # FastMCP entry point
  tools/
    shareholder.py    # AGM 18 tools (Dispatch Table)
    ownership.py      # OWN 9 tools (Chain Tool)
    dividend.py       # DIV 5 tools
    news.py           # NEWS 1 tool (Naver API)
    parser.py         # XML parsers
    pdf_parser.py     # PDF + OCR fallback
    formatters.py     # 27 shared formatters
  dart/client.py      # DART + KRX + Naver API client
  llm/client.py       # LLM fallback
```

## 설치

```bash
pip install open-proxy-mcp          # Core (XML)
pip install open-proxy-mcp[pdf]     # + PDF/OCR
pip install open-proxy-mcp[llm]     # + LLM
pip install open-proxy-mcp[all]     # Everything
```

## 연결

Claude Desktop, Claude Code 모두 지원 (.mcp.json 또는 claude_desktop_config.json).
