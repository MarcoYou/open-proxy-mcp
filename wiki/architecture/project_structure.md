---
type: architecture
title: project-structure
updated: 2026-05-28
---

# Project Structure

이 문서는 OpenProxy MCP의 코드와 wiki 구조를 설명합니다. README에는 빠른 시작과 사용자용 요약만 두고, 개발자용 디렉터리 설명은 이 문서에서 관리합니다.

## 코드 구조

```text
open_proxy_mcp/
  server.py                # FastMCP 서버입니다. stdio와 HTTP 진입점을 제공합니다.
  tools_v2/                # 16개 public tool의 active entrypoint입니다.
  services/                # 도메인별 분석 로직입니다. tool wrapper와 분리되어 있습니다.
  dart/client.py           # DART API, 보조 공시 조회, rate limiter를 담당합니다.
  data/asset_managers/     # 내부 정책 corpus와 Open Proxy Guideline 데이터입니다.

scripts/
  wiki_lint.py             # wiki link 정책을 검증합니다.
  spot_*.py                # KOSPI/KOSDAQ batch 회귀 점검 스크립트입니다.

.github/workflows/
  wiki-lint.yml            # wiki 변경 시 lint --strict를 실행합니다.
  deploy.yml               # Fly.io 배포 workflow입니다.

Dockerfile                 # Fly.io 배포용 컨테이너 정의입니다.
fly.toml                   # Fly.io 설정입니다.
```

## Wiki 구조

```text
wiki/
  raw/                     # 외부 원본입니다. 원칙적으로 수정하지 않습니다.
  rules/                   # 한국 자본시장 사실, 공시 유형, 법령 layer입니다.
  tools/                   # 16개 public tool 카탈로그와 개별 tool 문서입니다.
  decisions/               # OPM 정책 결정과 changelog입니다.
  architecture/            # architecture, audit, fix, goal 문서입니다.
  ralph/                   # 시간순 작업 plan과 실행 기록입니다.
  lessons/                 # 회고와 반복 실수 방지 메모입니다.
  archive/                 # 흡수되었거나 대체된 과거 자료입니다.
  index.md                 # 전체 wiki 진입점입니다.
  WIKI_SCHEMA.md           # wiki 카테고리와 명명 규칙입니다.
  log.md                   # 작업 로그입니다.
```

## 문서 배치 원칙

README는 제품 설명, 빠른 시작, 첫 사용 예시만 유지합니다.

Tool별 사용법과 schema는 `wiki/tools/`에 둡니다.

공시 유형, 법령, 개념 설명은 `wiki/rules/`에 둡니다.

검증 결과와 성능/회귀 audit은 `wiki/architecture/audits/`에 둡니다.

이미 흡수된 설계 초안이나 과거 분석은 삭제하지 않고 `wiki/archive/`에 보관합니다.
