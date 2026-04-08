# OPM (OpenProxy MCP)

DART 데이터를 MCP로 제공하는 Python 서버. 약칭 **OPM**.
한국 상장사 거버넌스 분석 (주총, 지분, 배당, 위임장).

## 지식 체계 (wiki-first)

**도메인 지식, 아키텍처 결정, 공시 유형 등 상세는 위키 참조.**
위키는 LLM이 유지하며, 매 `/ship` 시 자동 업데이트.

- **위키 인덱스**: `wiki/index.md` — 전체 64페이지 카탈로그. 여기서 시작.
- **위키 스키마**: `wiki/WIKI_SCHEMA.md` — 페이지 타입, 워크플로우, 컨벤션.
- **카테고리**: concepts(26) / entities(9) / analysis(8) / sources(10) / disclosures(11)

질문이 오면 `wiki/index.md`를 먼저 읽고, 관련 페이지만 선택적으로 읽을 것.
전체 위키를 한 번에 로드하지 말 것.

## 프로젝트 구조
```
open_proxy_mcp/        # MCP 서버 코드
  server.py            # FastMCP 진입점
  tools/               # agm_*(34) + own_*(8) + div_*(5) = 47 tools
  dart/client.py       # DART API + KIND + 네이버 시세
  *_RULE.md            # tool별 규칙 (AGM/OWN/DIV)
wiki/                  # LLM 도메인 지식 위키 (Karpathy 아키텍처)
  concepts/            # 배당성향, 의결권, 프록시파이트 등
  entities/            # DART, KRX, 국민연금 등
  analysis/            # 벤치마크, 아키텍처 결정 등
  disclosures/         # 공시 유형 (소집공고, 배당결정 등)
  sources/             # 원본 소스 요약
  index.md             # 전체 인덱스 (여기서 시작)
```

## 핵심 규칙 (간략)
- **DART API**: 분당 1,000회 초과 시 24시간 IP 차단. Rate limiter 내장.
- **웹 스크래핑**: 최소 2초 간격. 배치 금지.
- **3-tier fallback**: XML → PDF (4s+) → OCR (Upstage)
- **파이프라인**: 전체 재실행 금지, 누락분만 처리.
- **데이터 방향**: 공고→결과 참조 OK, 결과→공고 금지 (시간 역전).
- **저장 안 함**: OPM은 실시간 조회, 데이터 저장 X.

상세 규칙은 위키의 해당 페이지 참조:
- DART API → `wiki/entities/DART-OpenAPI.md`
- fallback → `wiki/concepts/3-tier-fallback.md`
- 공시 유형 → `wiki/disclosures/`

## 문서 포인터
- 미완료 작업 → `TO_DO.md`
- 개발 히스토리 → `DEVLOG.md`
- tool 규칙 → `open_proxy_mcp/*_RULE.md`

## 로컬 셋업
```bash
git clone https://github.com/MarcoYou/open-proxy-mcp.git && cd open-proxy-mcp
uv sync && cp .env.example .env  # OPENDART_API_KEY 설정
```

## 개발 방식
- Build → Check → Pass 사이클. 의미 있는 변경마다 커밋.
- `/ship` 시 wiki 자동 업데이트 (코드 변경 → 관련 위키 페이지 갱신).
