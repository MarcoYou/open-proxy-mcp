# Architecture (v2)

> v1 아키텍처는 `open-proxy-mcp-v1.3.0` 브랜치 참조.

## System Overview

```
User (자연어 질문)
  ↓
Claude (AI) ←→ MCP Protocol ←→ FastMCP Server (open-proxy-mcp)
                                      ↓
                              tools_v2/ (11개 tool)
                                      ↓
                              services/ (도메인 분석 로직)
                                      ↓
                          ┌───────────┼───────────┐
                          ↓           ↓           ↓
                      DART API    DART Web       KIND / Naver
                     (공식 API)  (document.xml)  (투표결과/시세)
```

- **Transport**: streamable-http (Fly.io 프로덕션)
- **API 키**: URL 쿼리 `?opendart=키` → ContextVar → DartClient가 요청별로 읽음
- **배포**: Fly.io (nrt), python:3.12-slim, auto-suspend, 1 vCPU / 1GB

---

## Tool Structure (11개)

v2는 Tier 구조 대신 **Data Tools + Action Tools** 2계층으로 단순화됐어요.

```
company                      # 진입점 — 기업 식별 + 최근 공시 인덱스
│
├─ Data Tools (7)
│  ├─ shareholder_meeting    # 주총 (안건 / 이사후보 / 보수한도 / 정관변경 / 결과)
│  ├─ ownership_structure    # 지분 구조 (최대주주 / 5% 블록 / 자사주 / control map)
│  ├─ dividend               # 배당 사실 (DPS / 배당성향 / 추이)
│  ├─ treasury_share         # 자사주 이벤트 (취득 / 처분 / 소각 / 신탁)
│  ├─ proxy_contest          # 경영권 분쟁 (위임장 / 소송 / 5% 시그널)
│  ├─ value_up               # 밸류업 계획 (약속 / 이행현황)
│  └─ evidence               # 공시 원문 링크 (rcept_no → viewer_url)
│
└─ Action Tools (3)
   ├─ prepare_vote_brief      # 의결권 행사 메모
   ├─ prepare_engagement_case # 주주관여 케이스 메모
   └─ build_campaign_brief    # 캠페인 브리프
```

### v1 vs v2 비교

| | v1 | v2 |
|--|----|----|
| Tool 수 | 36개 | 11개 |
| 구조 | 5-Tier (Entity → Context → Search → Orchestrate → Detail) | Data Tools + Action Tools |
| 진입점 | `corp_identifier` → `tool_guide` → `agm_search` | `company` 하나로 시작 |
| 파싱 레이어 | tool 내부에서 직접 파싱 | `services/` 레이어로 분리 |
| PDF 파싱 | 기본 경로 포함 | 제외 (XML/Viewer 우선) |
| 캐시 | XML + PDF 디스크 캐시 | XML 메모리+디스크 캐시만 |

---

## 코드 구조

```
open-proxy-mcp/
  open_proxy_mcp/
    server.py                   # FastMCP 진입점 (OPEN_PROXY_TOOLSET 분기)
    dart/
      client.py                 # DartClient — API + 크롤링 + rate limiter + cache
    tools_v2/                   # MCP tool 정의 (입력 검증 + 응답 포매팅)
      __init__.py               # register_all_tools_v2()
      _shared.py                # 공유 유틸 (resolve_company 등)
      company.py
      shareholder_meeting.py
      ownership_structure.py
      dividend.py
      treasury_share.py
      proxy_contest.py
      value_up.py
      evidence.py
      prepare_vote_brief.py
      prepare_engagement_case.py
      build_campaign_brief.py
    services/                   # 도메인 분석 로직 (tool과 분리)
      shareholder_meeting.py    # 주총 파싱 + 분석
      ownership_structure.py    # 지분 구조 분석
      dividend_v2.py            # 배당 분석
      treasury_share.py         # 자사주 이벤트 분석
      proxy_contest.py          # 위임장/소송 분석
      value_up_v2.py            # 밸류업 분석
      evidence.py               # 공시 원문 링크 생성
      company.py                # 기업 정보 + 공시 인덱스
      vote_brief.py             # 의결권 브리프 생성
      engagement_case.py        # 주주관여 케이스 생성
      campaign_brief.py         # 캠페인 브리프 생성
      filing_search.py          # 공시 검색 공통 로직
      date_utils.py             # 날짜 유틸
      contracts.py              # 데이터 계약 (타입 정의)
    tools/                      # v1 tool (OPEN_PROXY_TOOLSET=v1 시 사용)
  wiki/                         # 도메인 지식 위키
  docs/                         # 사용자 문서
  Dockerfile
  fly.toml
```

### tool과 service의 역할 분리

```
tools_v2/shareholder_meeting.py     ← MCP 인터페이스
  - 입력 파라미터 검증 (company, scope, year 등)
  - services/ 호출
  - 응답 포매팅 (Markdown)

services/shareholder_meeting.py     ← 분석 로직
  - DART 공시 검색
  - XML 파싱
  - scope별 데이터 조립
  - evidence_refs 생성
```

---

## Data Flow

### Request Path

```
1. AI → MCP tool 호출 (company + params)
2. _shared.resolve_company(company) → corp_code + ticker
3. DartClient.search_filings(corp_code, ...) → 공시 목록 [_search_cache]
4. DartClient.get_document_cached(rcept_no) → 공시 원문 [_doc_cache + disk]
5. services/ → 파싱 + 분석 → 구조화 데이터
6. tools_v2/ → Markdown 포매팅 → 응답
```

### shareholder_meeting scope별 동작

```
scope="summary"
  └─ 공시 검색 → XML fetch → meeting_info 파싱 → 안건 상위 목록

scope="board"
  └─ summary + personnel 파서 (이사/감사 후보)

scope="compensation"
  └─ summary + compensation 파서 (보수한도)

scope="results"
  └─ summary + 결과공시 검색 → KIND fetch → vote result 파서
```

summary가 모든 파서를 돌리지 않아요. scope가 열릴 때만 해당 파서 추가 실행.

---

## Data Sources

```
순위 1: DART API (병렬 가능, 분당 1,000회 한도)
  └─ list.json, company.json, majorstock.json, alotMatter.json 등

순위 2: DART document.xml (2초 간격)
  └─ 공시 원문 ZIP → XML 파싱

순위 3: KIND 크롤링 (1-3초 랜덤 간격, 화이트리스트 공시만)
  └─ 주총 의결권 행사 결과

순위 4: 네이버 (참고만)
  └─ 일별 종가, 뉴스 검색
```

상위 소스로 해결되면 하위 소스 접근 금지.

---

## Cache Layers

| Cache | 범위 | 크기 | 저장소 | 교체 방식 |
|-------|------|------|--------|-----------|
| `_corp_code_cache` | 프로세스 전역 | 전체 기업 목록 | 메모리 | 재시작 시 초기화 |
| `_search_cache` | API 키별 세션 | 50건 | 메모리 | FIFO |
| `_viewer_doc_cache` | API 키별 세션 | 30건 | 메모리 | FIFO |
| `_doc_cache` | API 키별 세션 | 30건 | 메모리 + 디스크 | FIFO (메모리), 영구 (디스크) |

캐시 키 (search): `{corp_code}|{bgn_de}|{end_de}|{pblntf_ty}`
캐시 키 (doc): `{rcept_no}`

---

## Rate Limiting

| 대상 | 간격 | 비고 |
|------|------|------|
| DART API | 0.1초 | 분당 600회 (공식 한도 1,000) |
| DART Web | 2.0초 | DDoS 방지 |
| KIND | 1.0-3.0초 (랜덤) | 보수적 접근 |
| API Key Rotation | 자동 | status 020 시 보조 키로 전환 |

---

## Deployment

```
프로덕션:  streamable-http ← claude.ai 웹 커넥터
           Fly.io (nrt), auto-suspend, min 0 machines
           URL: https://open-proxy-mcp.fly.dev/mcp?opendart=키

CI/CD:     GitHub Actions → fly deploy (main push 시 자동)
```

### OPEN_PROXY_TOOLSET 환경변수

```
v2      → tools_v2/ 11개 tool만 등록 (현재 기본값)
v1      → tools/ 36개 tool만 등록
hybrid  → v1 + v2 동시 등록
```
