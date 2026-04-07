---
type: entity
title: FastMCP
tags: [framework, mcp, python]
related: [OpenProxy-MCP]
---

# FastMCP

## 개요

Python 기반 MCP(Model Context Protocol) 서버 프레임워크. [[OpenProxy-MCP]]의 서버 엔트리포인트.

## OPM에서의 활용

- server.py에서 FastMCP 인스턴스 생성
- tools/ 디렉토리의 tool들을 auto-discovery로 등록 (register_all_tools)
- SSE transport 지원 (--sse 옵션, Claude 웹 Cowork 커넥터 연결)
- Claude Desktop, Claude Code 모두 연동

## 선택 이유 (DEVLOG 2026-03-19)

dart-mcp, Kensho, FactSet 등 참고 프로젝트 리서치 후 결정. LLM 친화적 구조화, 도메인별 tool 분리, 캐싱 필수가 공통 교훈.
