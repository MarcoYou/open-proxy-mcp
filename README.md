# OpenProxy MCP

DART 공시 데이터를 AI 에이전트에서 쉽게 활용할 수 있게 해주는 MCP (Model Context Protocol) 서버.

## 목표

- 주주총회 소집공고를 구조화하여 쉽게 읽고 활용할 수 있게 제공
- DART 재무정보, 공시 데이터와의 연동 확장
- Claude Desktop, Claude Code 등 MCP 클라이언트에서 tool로 사용 가능

## 데이터 소스

- [OpenDART API](https://opendart.fss.or.kr/) — 금융감독원 전자공시시스템

## 설치

```bash
# 가상환경 생성
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 의존성 설치
pip install -r requirements.txt

# 환경변수 설정
cp .env.example .env
# .env 파일에 OpenDART API 키 입력
```

## 사용법

```bash
# MCP 서버 실행
python -m open_proxy_mcp
```

## 라이선스

MIT
