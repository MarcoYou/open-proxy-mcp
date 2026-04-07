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
- OpenDART API + KIND 크롤링

## Tool 구성 (40개)

- AGM 33개: 오케스트레이터 + 8 Parsers x 3 Tiers + Search/Meta + 결과
- Ownership 7개: 지분구조 분석
- DIV 5개: 배당 분석 (추가 예정)

## 프로젝트 구조

```
open_proxy_mcp/
  server.py           # FastMCP entry point
  tools/
    shareholder.py    # 33 AGM tools
    ownership.py      # 7 ownership tools
    parser.py         # XML parsers
    pdf_parser.py     # PDF + OCR fallback
    formatters.py     # 27 shared formatters
  dart/client.py      # API client + KIND + singleton
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
