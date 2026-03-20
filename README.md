# OpenProxy MCP (OPM)

DART 공시 데이터를 AI 에이전트에서 쉽게 활용할 수 있게 해주는 MCP (Model Context Protocol) 서버.

## 목표

- 주주총회 소집공고를 구조화하여 쉽게 읽고 활용할 수 있게 제공
- DART 재무정보, 공시 데이터와의 연동 확장
- Claude Desktop, Claude Code 등 MCP 클라이언트에서 tool로 사용 가능

## 데이터 소스

- [OpenDART API](https://opendart.fss.or.kr/) — 금융감독원 전자공시시스템

## MCP Tools

| Tool | 기능 |
|------|------|
| `search_shareholder_meetings` | 종목코드/회사명으로 주주총회 소집공고 검색 |
| `get_shareholder_meeting_document` | 접수번호로 소집공고 본문 텍스트 반환 |
| `get_meeting_agenda` | 접수번호로 의안(안건) 트리 파싱 반환 |
| `get_meeting_info` | 접수번호로 비안건 정보 (일시, 장소, 전자투표 등) 반환 |

### get_meeting_agenda 옵션

| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `rcept_no` | (필수) | DART 접수번호 |
| `use_llm` | `false` | 정규식 파싱 실패 시 LLM fallback 사용 여부 |
| `format` | `"md"` | 반환 형식: `"md"` (마크다운, LLM용) / `"json"` (프론트엔드용) |

## 안건 파서

정규식 기반 안건 트리 파싱 + LLM fallback 하이브리드 구조.

- **155개 기업 테스트** — 정규식만으로 90% 처리
- **JSON v1 스키마** — `rceptNo + agendaId` 복합 키로 향후 enrichment join 가능
- **hard/soft fail** 구분 — soft fail 시 LLM fallback, hard fail 시 로그

```
정규식 파싱 → validate → ✅ 반환
                        → ❌ zone 추출 → LLM fallback → ✅ 반환
                                                       → ❌ "파싱 불가"
```

## 프로젝트 구조

```
open_proxy_mcp/
  server.py           # FastMCP 서버 진입점
  tools/
    shareholder.py    # MCP tool 정의 (4 tools + JSON 빌더 + 마크다운 포매터)
    parser.py         # 소집공고 파싱 (안건 트리 + 비안건 정보 + validate)
  dart/
    client.py         # OpenDART API 클라이언트 (인증, 캐싱, 에러핸들링)
  llm/
    client.py         # LLM fallback (Claude Sonnet / OpenAI gpt-5.4-mini)
```

## 설치

```bash
# 가상환경 생성
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 의존성 설치
pip install -r requirements.txt

# 환경변수 설정
cp .env.example .env
# .env 파일에 API 키 입력 (OPENDART_API_KEY 필수, ANTHROPIC/OPENAI는 LLM fallback용)
```

## 사용법

```bash
# MCP 서버 실행
python -m open_proxy_mcp
```

## 라이선스

MIT
