# OpenProxy MCP (OPM)

[English README](README.md)

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
├─ agm_agenda_xml(rcept_no)  안건 제목 트리 (세부의안 포함)
├─ agm_corrections(rcept_no) 정정 전/후 비교
│
├─ agm_items(rcept_no)       안건 본문 블록 (범용 raw)
│   ├─ agm_financials_xml    재무제표 정규화 (BS/IS/자본변동표/처분계산서)
│   ├─ agm_personnel_xml     이사/감사 선임·해임 정규화
│   ├─ agm_aoi_change_xml    정관변경 정규화 (변경전/변경후 비교)
│   └─ agm_compensation_xml  보수한도 정규화 (당기 한도/전기 실지급)
│
└─ agm_document(rcept_no)    원문 텍스트

안건 유형별 tool 매핑:
  재무제표 승인 → agm_financials_xml (테이블 정규화)
  이사/감사 선임·해임 → agm_personnel_xml (후보자 정보)
  정관변경 → agm_aoi_change_xml (변경전/변경후 비교)
  보수한도 승인 → agm_compensation_xml (당기/전기 보수 비교)
  자사주/기타 → agm_items (raw 블록)
```

| Tool | 기능 | 주요 파라미터 |
|------|------|-------------|
| `agm_search` | 종목코드/회사명으로 소집공고 검색 | ticker, bgn_de, end_de |
| `agm_document` | 소집공고 본문 텍스트 반환 | rcept_no, max_length |
| `agm_agenda_xml` | 안건 트리 파싱 (세부의안 포함) | rcept_no, use_llm, format |
| `agm_info` | 회의 정보 (일시/장소/보고사항/전자투표) | rcept_no |
| `agm_items` | 안건별 상세 내용 (테이블+텍스트) | rcept_no, agenda_no, use_llm, format |
| `agm_financials_xml` | 재무제표 정규화 (BS/IS/자본변동표/처분계산서) | rcept_no, use_llm, format |
| `agm_corrections` | 정정 전/후 비교 + 사유 | rcept_no, format |
| `agm_personnel_xml` | 이사/감사 선임·해임 후보자 정보 | rcept_no, format |
| `agm_aoi_change_xml` | 정관변경 변경전/변경후 비교 | rcept_no, format |
| `agm_compensation_xml` | 보수한도 당기/전기 비교 | rcept_no, format |
| `agm_steward` | 종합 오케스트레이터 (위 tool 자동 조합) | ticker, bgn_de, end_de |
| `agm_guide` | AI 어시스턴트용 사용 가이드 (flow + 판정 기준) | - |

### PDF / OCR Fallback Tools

XML 파싱 실패 시 AI가 자율적으로 호출하는 fallback tool:

| Tool | 소스 | 설명 |
|------|------|------|
| `agm_*_pdf` | opendataloader | PDF 다운로드 + 파싱 (4초+). XML 실패 시 시도. |
| `agm_*_ocr` | Upstage OCR | PDF 이미지 OCR (가장 느림). PDF도 실패 시 시도. UPSTAGE_API_KEY 필요. |

대상: `agm_agenda_xml`, `agm_financials_xml`, `agm_personnel_xml`, `agm_aoi_change_xml`, `agm_compensation_xml`

### Fallback 흐름

```
AI가 agm_personnel_xml(rcept_no) 호출
  → 정상 결과 → 답변
  → 빈 결과 or 품질 이슈
  → AI: "PDF로 재시도할게요" → agm_personnel_pdf(rcept_no)
      → 정상 → 답변
      → 실패 → AI: "OCR로 시도할게요" → agm_personnel_ocr(rcept_no)
```

### 공통 옵션

| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `format` | `"md"` | `"md"` (마크다운, LLM용) / `"json"` (프론트엔드용 v3 스키마) |
| `use_llm` | `false` | 정규식 파싱 실패 시 LLM fallback 사용 여부 |

## 데이터 흐름

```
┌─────────────────────────────────────────────────────────┐
│  1단계: XML 파싱 (기본, 빠름)                            │
│                                                          │
│  DART API (document.xml ZIP)                             │
│    → get_document(rcept_no)  {text, html, images}        │
│    → parser.py (XML 파서)                                │
│       ├─ parse_agenda_xml         안건 트리               │
│       ├─ parse_financials_xml     재무제표                │
│       ├─ parse_personnel_xml      인사 정보               │
│       ├─ parse_aoi_xml            정관변경                │
│       ├─ parse_compensation_xml   보수한도                │
│       └─ bs4(lxml) 우선 → text regex fallback            │
└──────────────────────────┬──────────────────────────────┘
                           │ 실패 시
┌──────────────────────────▼──────────────────────────────┐
│  2단계: PDF 파싱 (느림, 4초+)                            │
│                                                          │
│  DART 웹 → PDF 다운로드 → opendataloader → 마크다운      │
│    → pdf_parser.py (PDF 파서)                            │
│       ├─ parse_agenda_pdf                                │
│       ├─ parse_financials_pdf                            │
│       ├─ parse_personnel_pdf                             │
│       ├─ parse_aoi_pdf                                   │
│       └─ parse_compensation_pdf                          │
└──────────────────────────┬──────────────────────────────┘
                           │ 실패 시
┌──────────────────────────▼──────────────────────────────┐
│  3단계: OCR (가장 느림, UPSTAGE_API_KEY 필요)            │
│                                                          │
│  키워드로 페이지 특정 → PDF 페이지 추출                   │
│    → Upstage OCR API → 마크다운 → PDF 파서 재실행        │
└─────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────┐
│  shareholder.py — MCP tool 레이어 (23개)                 │
│                                                          │
│  agm_*_xml  — 1단계 호출                                 │
│  agm_*_pdf  — 2단계 호출 (AI가 자율 판단)                │
│  agm_*_ocr  — 3단계 호출 (AI가 자율 판단)                │
│  agm_guide  — AI용 사용 가이드                           │
│                                                          │
│  format="md" → LLM용 마크다운                            │
│  format="json" → 프론트엔드용 v3 스키마                  │
└─────────────────────────────────────────────────────────┘
```

## 파싱 성능 (KOSPI 200, 안건 tree 기반 실제 성공률)

| 파서 | XML | PDF | OCR |
|------|-----|-----|-----|
| agenda | 99.5% | 98.0% | 100% |
| financials BS | 97.4% | 97.9% | 100% |
| financials IS | 100% | 95.7% | 100% |
| personnel | 98.9% | 97.9% | 100% |
| aoi (정관변경) | 97.8% | 99.0% | 100% |
| compensation | 98.4% | 99.5% | 100% |

## 프로젝트 구조

```
open_proxy_mcp/
  server.py           # FastMCP 서버 진입점 (stdio + SSE)
  tools/
    shareholder.py    # MCP tool 23개 + 포매터 + format_krw
    parser.py         # XML 파서 — parse_*_xml()
    pdf_parser.py     # PDF 파서 — parse_*_pdf() + Upstage OCR fallback
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
# MCP 서버 실행 (stdio — Claude Code/Desktop)
python -m open_proxy_mcp

# SSE 모드 (웹 연동, 기본 port 9000)
python -m open_proxy_mcp --sse

# 커스텀 포트
python -m open_proxy_mcp --sse 8080
```

### 환경변수 (.env)

```
OPENDART_API_KEY=...          # 필수 — DART API 키
OPENDART_API_KEY_2=...        # 선택 — 보조 키 (속도 제한 시 자동 전환)
ANTHROPIC_API_KEY=...         # 선택 — LLM fallback (Claude)
OPENAI_API_KEY=...            # 선택 — LLM fallback (OpenAI)
UPSTAGE_API_KEY=...           # 선택 — OCR fallback (Upstage Document Parse)
```

### Claude Code 설정 (.mcp.json)

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

### Claude 웹 연결 (SSE + ngrok)

```bash
# 터미널 1: MCP SSE 서버
python -m open_proxy_mcp --sse

# 터미널 2: ngrok 터널
ngrok http 9000
```

ngrok URL + `/sse`를 Claude 웹 Integrations에서 MCP Server로 등록.

### 첫 사용 시

AI에게: "먼저 `agm_guide`를 호출해서 사용법을 읽어줘"

## 라이선스

[CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) - 비상업적 사용만 허용
