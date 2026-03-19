# OpenProxy MCP

## 프로젝트 개요
DART(전자공시시스템) 데이터를 MCP 프로토콜로 제공하는 Python 서버.
주주총회 소집공고를 시작으로, 재무정보 등 DART 전체 공시 데이터로 확장 예정.

## 기술 스택
- Python
- FastMCP (`mcp.server.fastmcp`) — 데코레이터 기반 MCP 서버 프레임워크
- httpx — async HTTP 클라이언트
- python-dotenv — 환경변수 관리
- OpenDART API (https://opendart.fss.or.kr/)

## 프로젝트 구조
```
open_proxy_mcp/       # 메인 패키지
  __init__.py
  server.py           # FastMCP 서버 진입점
  tools/              # MCP tool 정의 (도메인별 분리)
    shareholder.py    # 주주총회 소집공고 관련
  dart/               # OpenDART API 클라이언트
    client.py         # API 호출 래퍼 (인증, 에러핸들링, 캐싱)
```

## 설계 원칙
- 각 DART API 도메인(공시, 재무, 지분 등)은 `dart/` 하위 모듈로 분리
- MCP tool은 `tools/` 하위에 도메인별로 분리
- API 키는 `.env`에서 관리, 절대 커밋하지 않음
- corpCode.xml 등 무거운 데이터는 캐싱 적용
- 입력값(날짜 형식 등) 검증 처리
- 단일 파일 모놀리스 지양, 모듈 분리 유지

## 참고 프로젝트
- [dart-mcp](https://github.com/2geonhyup/dart-mcp) — DART 재무제표 MCP 서버
  - 참고: FastMCP 데코레이터 패턴, OpenDART API 호출 구조, XBRL 파싱, 인코딩 폴백
  - 우리와 다른 점: 정기공시(pblntf_ty=A)만 지원, 주주총회 소집공고 없음, 단일 파일 구조, 캐싱/테스트 없음

## 주요 커맨드
```bash
pip install -r requirements.txt    # 의존성 설치
python -m open_proxy_mcp           # 서버 실행
```
