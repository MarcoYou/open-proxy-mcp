# OpenProxy MCP

## 프로젝트 개요
DART(전자공시시스템) 데이터를 MCP 프로토콜로 제공하는 Python 서버.
주주총회 소집공고를 시작으로, 재무정보 등 DART 전체 공시 데이터로 확장 예정.

## 기술 스택
- Python
- MCP SDK (`mcp`)
- OpenDART API

## 프로젝트 구조
```
open_proxy_mcp/       # 메인 패키지
  __init__.py
  server.py           # MCP 서버 진입점
  tools/              # MCP tool 정의
    shareholder.py    # 주주총회 소집공고 관련
  dart/               # OpenDART API 클라이언트
    client.py         # API 호출 래퍼
```

## 설계 원칙
- 각 DART API 도메인(공시, 재무, 지분 등)은 `dart/` 하위 모듈로 분리
- MCP tool은 `tools/` 하위에 도메인별로 분리
- API 키는 `.env`에서 관리, 절대 커밋하지 않음

## 주요 커맨드
```bash
pip install -r requirements.txt    # 의존성 설치
python -m open_proxy_mcp           # 서버 실행
```
