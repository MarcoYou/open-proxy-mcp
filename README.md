# OpenProxy MCP (OPM)

DART 공시 데이터를 AI 에이전트에서 쉽게 활용할 수 있게 해주는 MCP (Model Context Protocol) 서버.

## 목표

- 주주총회 소집공고를 구조화하여 쉽게 읽고 활용할 수 있게 제공
- DART 재무정보, 공시 데이터와의 연동 확장
- Claude Desktop, Claude Code 등 MCP 클라이언트에서 tool로 사용 가능

## 데이터 소스

- [OpenDART API](https://opendart.fss.or.kr/) — 금융감독원 전자공시시스템

## MCP Tools

```
agm_steward(ticker)  ← 오케스트레이터 (한 번에 요약)
│
├─ agm_search(ticker)        소집공고 검색 + 정정 태깅
├─ agm_info(rcept_no)        회의 정보 + 정정 요약
├─ agm_agenda(rcept_no)      안건 제목 트리 (세부의안 포함)
├─ agm_corrections(rcept_no) 정정 전/후 비교
│
├─ agm_items(rcept_no)       안건 본문 블록 (범용 raw)
│   ├─ agm_financials        재무제표 정규화 (BS/IS/자본변동표/처분계산서)
│   ├─ agm_personnel         이사/감사 선임·해임 정규화
│   ├─ agm_aoi_change        정관변경 정규화 (변경전/변경후 비교)
│   └─ agm_compensation      보수한도 정규화 (당기 한도/전기 실지급)
│
└─ agm_document(rcept_no)    원문 텍스트

안건 유형별 tool 매핑:
  재무제표 승인 → agm_financials (테이블 정규화)
  이사/감사 선임·해임 → agm_personnel (후보자 정보)
  정관변경 → agm_aoi_change (변경전/변경후 비교)
  보수한도 승인 → agm_compensation (당기/전기 보수 비교)
  자사주/기타 → agm_items (raw 블록)
```

| Tool | 기능 | 주요 파라미터 |
|------|------|-------------|
| `agm_search` | 종목코드/회사명으로 소집공고 검색 | ticker, bgn_de, end_de |
| `agm_document` | 소집공고 본문 텍스트 반환 | rcept_no, max_length |
| `agm_agenda` | 안건 트리 파싱 (세부의안 포함) | rcept_no, use_llm, format |
| `agm_info` | 회의 정보 (일시/장소/보고사항/전자투표) | rcept_no |
| `agm_items` | 안건별 상세 내용 (테이블+텍스트) | rcept_no, agenda_no, use_llm, format |
| `agm_financials` | 재무제표 정규화 (BS/IS/자본변동표/처분계산서) | rcept_no, use_llm, format |
| `agm_corrections` | 정정 전/후 비교 + 사유 | rcept_no, format |
| `agm_personnel` | 이사/감사 선임·해임 후보자 정보 | rcept_no, format |
| `agm_aoi_change` | 정관변경 변경전/변경후 비교 | rcept_no, format |
| `agm_compensation` | 보수한도 당기/전기 비교 | rcept_no, format |
| `agm_steward` | 종합 오케스트레이터 (위 tool 자동 조합) | ticker, bgn_de, end_de |

### 공통 옵션

| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `format` | `"md"` | `"md"` (마크다운, LLM용) / `"json"` (프론트엔드용 v3 스키마) |
| `use_llm` | `false` | 정규식 파싱 실패 시 LLM fallback 사용 여부 |

## 데이터 흐름

```
DART API (document.xml ZIP)
  │
  ▼
get_document(rcept_no)
  │ {text, html, images}
  │ (캐싱: _doc_cache, 30건 LRU)
  ▼
┌────────────────────────────────────────────────────┐
│  parser.py — 파싱 레이어                            │
│                                                     │
│  [공통 소스]                                        │
│  ├─ parse_agenda_items(text, html) → 안건 트리      │
│  ├─ parse_meeting_info(text, html) → 회의 정보      │
│  └─ parse_agenda_details(html)     → 안건 상세 블록 │
│                                                     │
│  [특화 파서] — 각각 HTML에서 독립적으로 파싱         │
│  ├─ parse_financial_statements(html) → 재무제표     │
│  ├─ parse_personnel(html)            → 인사 정보    │
│  ├─ parse_aoi(html)                  → 정관변경     │
│  ├─ parse_compensation(html)         → 보수한도     │
│  └─ parse_correction_details(html)   → 정정 사항    │
│                                                     │
│  모든 파서: bs4(lxml) 우선 → text regex fallback     │
└────────────────────────────────────────────────────┘
  │
  ▼
┌────────────────────────────────────────────────────┐
│  shareholder.py — MCP tool 레이어                   │
│                                                     │
│  각 tool은 parser 결과를 포매팅                     │
│  format="md" → LLM용 마크다운                       │
│  format="json" → 프론트엔드용 v3 스키마             │
│                                                     │
│  format_krw() — 단위 변환 유틸 (백만원→조/억)       │
│  use_llm / max_fallback_length — fallback 옵션      │
└────────────────────────────────────────────────────┘
  │
  ▼
┌────────────────────────────────────────────────────┐
│  프론트엔드 (OpenProxy/frontend)                    │
│                                                     │
│  pipeline/*.json — MCP에서 생성한 v3 JSON           │
│  mockData.ts — JSON → Company 객체 변환             │
│  AgendaAnalysis.tsx — 렌더링                        │
│    ├─ FinancialTable (계층 트리, 변화율)             │
│    ├─ CharterChangesSection (접이식 카드)            │
│    ├─ CandidatesSection (후보자 정보)                │
│    └─ RetainedEarningsTable (처분계산서)             │
└────────────────────────────────────────────────────┘
```

## 파싱 성능 (KOSPI 200 기준)

| 파서 | 성공률 |
|------|--------|
| agenda (안건 트리) | 99.5% |
| financials (재무제표) | 97.4% |
| personnel (인사) | 98.9% |
| aoi (정관변경) | 97.8% |
| compensation (보수한도) | 98.4% |

- **LLM fallback**: Claude Sonnet / OpenAI — hard fail / soft fail 구분
- **PDF 보조 소스**: `get_document_pdf(rcept_no)` — XML 파싱 실패 시 보강용 (향후)

## 프로젝트 구조

```
open_proxy_mcp/
  server.py           # FastMCP 서버 진입점 (stdio + SSE)
  tools/
    shareholder.py    # MCP tool 11개 + 포매터 + format_krw
    parser.py         # 파서 (bs4+regex) — 안건/재무/인사/정관/보수/정정
  dart/
    client.py         # OpenDART API + 웹 PDF 다운로드 (rate limiter 내장)
  llm/
    client.py         # LLM fallback (Claude Sonnet / OpenAI)

OpenProxy/            # 프론트엔드 (React/Vite) — git clone from HojiPark/openproxy
  frontend/
    src/data/
      schema.ts       # v3 통합 스키마 타입
      mockData.ts     # pipeline JSON → Company 객체 변환
      pipeline/       # MCP에서 생성한 v3 JSON (KOSPI 200)
    src/components/
      AgendaAnalysis.tsx  # 안건 상세 렌더링
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
# MCP 서버 실행 (stdio)
python -m open_proxy_mcp

# SSE 모드 (웹 클라이언트 연동)
python -m open_proxy_mcp --sse
```

### MCP 클라이언트 설정 (.mcp.json)

```json
{
  "mcpServers": {
    "open-proxy-mcp": {
      "command": "python",
      "args": ["-m", "open_proxy_mcp"],
      "cwd": "/path/to/open-proxy-mcp"
    }
  }
}
```

## 라이선스

MIT
