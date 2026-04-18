# Architecture (v1 stable)

> This document describes the current stable architecture.  
> For the next-release tool surface and validation model, see [docs/v2/README.md](v2/README.md).

## System Overview

```
User (자연어 질문)
  ↓
Claude (AI) ←→ MCP Protocol ←→ FastMCP Server (open-proxy-mcp)
                                      ↓
                          ┌───────────┼───────────┐
                          ↓           ↓           ↓
                      DART API    DART Web    KIND / Naver
                     (공식 API)   (PDF/OCR)   (투표결과/시세)
```

- **Transport**: stdio (로컬) / streamable-http (Fly.io 프로덕션)
- **API 키**: URL 쿼리 `?opendart=키` → ContextVar → DartClient가 요청별로 읽음
- **배포**: Fly.io (iad), python:3.12-slim, auto-suspend, 1 vCPU / 1GB

---

## Tool Tier System (36 tools)

```
Tier 1  Entity        corp_identifier ─────────── 항상 첫 번째
        │
Tier 2  Context       tool_guide ──────────────── 불명확하면 참조
        │
Tier 3  Search        agm_search, div_search ──── rcept_no 획득
        │
Tier 4  Orchestrate   agm_pre/post_analysis       ┐
                      ownership_full_analysis       │ 종합 분석
                      div_full_analysis             │ 진입점
                      proxy_fight/full_analysis     │
                      proxy_litigation              │
                      governance_report ◄───────── 5개 도메인 통합
                      value_up_plan                ┘
        │
Tier 5  Detail        agm_*_xml (9개)             ┐
                      agm_items, agm_result        │ drill-down
                      ownership_major/total/...     │ 또는
                      div_detail, div_history       │ chain 내부
                      proxy_detail/direction/search │
                      news_check                   ┘
```

AI는 Tier 1 → 2 → 3 → 4 순서로 호출. Tier 5는 사용자 drill-down 요청 시만.
Tier 4 오케스트레이터가 내부에서 Tier 5를 `asyncio.gather` 병렬 호출.

---

## Domain Map

| Domain | Module | Tools | Orchestrator |
|--------|--------|-------|-------------|
| AGM | shareholder.py | 14 | agm_pre_analysis, agm_post_analysis |
| OWN | ownership.py | 6 | ownership_full_analysis |
| DIV | dividend.py | 4 | div_full_analysis |
| PRX | proxy.py | 6 | proxy_fight, proxy_full_analysis |
| VUP | value_up.py | 1 | (단독) |
| GOV | governance.py | 1 | governance_report (5개 도메인 통합) |
| CORP | corp.py | 1 | corp_identifier |
| GUIDE | guide.py | 1 | tool_guide |
| NEWS | news.py | 1 | news_check |

---

## Chain Tool Architecture

### governance_report (최상위 체인)

```
governance_report(ticker)
├── [gather] agm_post_analysis ─→ agm_pre_analysis ─→ 9개 XML tool 병렬
│                                └→ agm_result (KIND)
├── [gather] ownership_full_analysis ─→ major + total + block 병렬
├── [gather] div_full_analysis ─→ detail + history 순차
├── [gather] proxy_full_analysis ─→ fight + litigation + block 병렬
│                                   └→ [조건부] agm_result (fight 감지 시)
└── [gather] value_up_plan
```

### Cross-Module Tool 참조 패턴

```python
# _NullMCP / _CrossMCP: 다른 모듈의 register_tools에서 함수를 수집
class _NullMCP:
    def tool(self):
        def d(fn):
            _domain_tools[fn.__name__] = fn  # dict에 수집
            return fn                        # 함수 변형 없이 반환
        return d

# governance.py: 5개 모듈에서 오케스트레이터 함수 수집
# proxy.py (_CrossMCP): ownership + shareholder에서 block + result 수집
```

---

## Data Flow

### Request Path

```
1. AI → MCP tool 호출 (ticker + params)
2. resolve_ticker(ticker) → DartClient.lookup_corp_code → corp_code
3. DartClient.search_filings(corp_code, ...) → 공시 목록 [_search_cache]
4. DartClient.get_document(rcept_no) → 공시 원문 ZIP [_doc_cache + disk]
5. parser.py → XML 파싱 → 구조화 데이터
6. formatters.py → Markdown 또는 JSON 응답
```

### Data Sources + Access Priority

```
순위 1: DART API (병렬 가능, 분당 1,000회 한도)
  └─ list.json, company.json, majorstock.json, alotMatter.json, ...
순위 2: DART 웹 크롤링 (2초 간격)
  └─ PDF 다운로드, document viewer
순위 3: KIND 크롤링 (1-3초 랜덤 간격)
  └─ 주총 투표결과 (3단계 iframe)
순위 4: 네이버 (시세/뉴스)
  └─ 일별 종가, 뉴스 검색 API
```

상위 소스로 해결되면 하위 소스 접근 금지.

### 3-Tier Fallback (공시 파싱)

```
XML 파싱 (즉시) → PDF 파싱 (4초+) → OCR (Upstage, 선택)
```

---

## Cache Layers

| Cache | Scope | Size | Storage | Eviction |
|-------|-------|------|---------|----------|
| `_corp_code_cache` | 프로세스 | 전체 기업 | 메모리 (global) | 재시작 시 |
| `_search_cache` | 세션 | 50건 | 메모리 (인스턴스) | FIFO |
| `_doc_cache` | 세션 | 30건 | 메모리 + 디스크 | FIFO (메모리), 영구 (디스크) |

캐시 키 (search): `{corp_code}|{bgn_de}|{end_de}|{pblntf_ty}`
캐시 키 (doc): `{rcept_no}`

---

## Rate Limiting

| Target | Interval | Limit |
|--------|----------|-------|
| DART API | 0.1초 | 분당 600회 (공식 한도 1,000) |
| DART Web | 2.0초 | DDoS 방지 |
| KIND | 1.0-3.0초 (랜덤) | 보수적 접근 |
| API Key Rotation | 자동 | status 020 시 보조 키로 전환 |

---

## File Structure

```
open-proxy-mcp/
  open_proxy_mcp/
    server.py               # FastMCP 진입점 (stdio + streamable-http)
    dart/
      client.py             # DartClient — API + 크롤링 + rate limiter + cache
    tools/
      __init__.py           # auto-discovery (pkgutil.iter_modules)
      shareholder.py        # AGM 14 tools
      ownership.py          # OWN 6 tools
      dividend.py           # DIV 4 tools
      proxy.py              # PRX 6 tools (_CrossMCP)
      value_up.py           # VUP 1 tool
      governance.py         # GOV 1 tool (_NullMCP, 5개 도메인 chain)
      corp.py               # CORP 1 tool
      guide.py              # GUIDE 1 tool
      news.py               # NEWS 1 tool
      formatters.py         # 공유 유틸 (resolve_ticker, strip_css, 포매터)
      errors.py             # tool_error, tool_not_found, tool_empty
      parser.py             # AGM XML 파서
      pdf_parser.py         # PDF/OCR fallback 파서
    *_RULE.md               # 도메인별 파싱 규칙
  wiki/                     # 도메인 지식 (68페이지)
  docs/                     # 사용자 문서
  Dockerfile                # python:3.12-slim, non-root
  fly.toml                  # Fly.io (iad, auto-suspend, 1GB)
```

---

## Deployment

```
로컬 (개발):    stdio transport ← Claude Desktop / Claude Code
프로덕션:       streamable-http ← claude.ai 웹 커넥터
                Fly.io (iad), auto-suspend, min 0 machines
                URL: https://open-proxy-mcp.fly.dev/mcp?opendart=키
```
