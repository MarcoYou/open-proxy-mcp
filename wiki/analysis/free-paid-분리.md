---
type: analysis
title: free/paid 2-repo 분리
tags: [architecture, business-model]
sources: [DEVLOG.md, OPA_README.md, OPM_README.md]
related: [OpenProxy-MCP, OpenProxy-AI, 3-tier-fallback]
---

# free/paid 2-repo 분리

## 결정 (2026-04-04)

OPM 프로젝트를 공개(free)와 비공개(paid) 2개 저장소로 분리.

## 구조

### open-proxy-mcp (public, free)
- 40개 MCP tool + 파서 + API 클라이언트
- CC BY-NC 4.0 라이선스
- XML -> AI 보강 -> PDF -> OCR 순서
- AI가 유저와 대화하면서 점진적 fallback
- agm_manual + CASE_RULE이 AI 판단 기준

### open-proxy-ai (private, paid)
- 파이프라인 + 프론트엔드 + 데이터
- XML -> PDF -> OCR -> LLM 자동 체이닝
- 배치 파이프라인으로 미리 최선 데이터 생성
- v4 JSON 199개 기업

## 공유 레이어

parser.py, pdf_parser.py, dart/client.py가 공통. OPA가 OPM을 pip install로 의존.

## 분리 이유

- MCP tool은 오픈소스로 커뮤니티 기여 유도
- 파이프라인/프론트엔드는 차별화 가치로 비공개 유지
- pyproject.toml optional deps 분리 (core/pdf/llm/all)
