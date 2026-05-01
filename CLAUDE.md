# OPM (OpenProxy MCP)

DART 데이터를 MCP로 제공하는 Python 서버. 약칭 **OPM**.
한국 상장사 거버넌스 분석 (주총, 지분, 배당, 위임장).

## 지식 체계 (wiki-first)

**도메인 지식, 아키텍처 결정, 공시 유형 등 상세는 위키 참조.**
위키는 LLM이 유지하며, 매 `/ship` 시 자동 업데이트.

- **위키 인덱스**: `wiki/index.md` — 전체 페이지 카탈로그. 여기서 시작.
- **첫 진입은 [[tools/README]]**: 18 tool 카탈로그가 사용자 입장 시작점.
- **위키 스키마**: `wiki/WIKI_SCHEMA.md` — 카테고리 정의 + 명명 규칙 + frontmatter schema + 신규 페이지 워크플로우.
- **카테고리 (5+1)**: `raw / tools / architecture / decisions / rules(concepts+disclosures+laws) / archive`

질문이 오면 `wiki/index.md`를 먼저 읽고, 관련 페이지만 선택적으로 읽을 것.
전체 위키를 한 번에 로드하지 말 것.

### 명명 규칙 (2026-05-01~)
- **시점 있는 문서**: `yymmdd_hhmm_{type}_{title}.md` (audit / fix / decision / debate / improvement / changelog / release / log)
- **정체성 문서**: `{name}.md` (tool / concept / disclosure / law). 시점 prefix 안 붙임.

신규 페이지 추가 시 [[WIKI_SCHEMA]] 워크플로우 따를 것.

### raw/ 절대 수정 금지
`wiki/raw/`는 외부 원본 (운용사 정책 PDF, 행사내역 xlsx, 외부 reference markdown).
LLM도 사람도 절대 수정 X. 분석/요약은 별도 페이지에 작성 (`architecture/`, `decisions/`, `rules/`).

## 프로젝트 구조
```
open_proxy_mcp/        # MCP 서버 코드
  server.py            # FastMCP 진입점
  tools_v2/            # 18 public tools (v2)
  services/            # 도메인별 분석 로직 (tool과 분리)
  dart/client.py       # DART API + KIND + 네이버 시세
  data/asset_managers/ # 8 운용사 정책 (익명화) + 행사내역 + Open Proxy Guideline + 12 매트릭스
  *_RULE.md            # 구 tool별 규칙 (AGM/OWN/DIV) — 흡수 진행 중
wiki/                  # LLM 도메인 지식 위키 (Karpathy 아키텍처)
  raw/                 # 외부 원본 (정책 PDF + 행사내역 xlsx + 외부 reference). 절대 수정 금지
  tools/               # 18 tool 카탈로그 (사용자 진입점)
  architecture/        # 시스템 설계 + audits/ + fixes/
  decisions/           # OPM 정책/판단 (open-proxy-guideline 등)
  rules/               # concepts/ + disclosures/ + laws/
  archive/             # 흡수된 페이지 (역사 보존, 신규 X)
  index.md             # 전체 인덱스 (여기서 시작)
  WIKI_SCHEMA.md       # 카테고리 + 명명 규칙
  log.md               # 작업 로그
```

## 핵심 규칙 (간략)
- **데이터 접근 우선순위**: ① DART API (병렬 가능) → ② DART 웹 크롤링 (2초 간격) → ③ KIND 크롤링 (2초 간격). 상위에서 해결되면 하위 접근 금지.
- **DART API**: 분당 1,000회 초과 시 24시간 IP 차단. Rate limiter 내장.
  - **DART API 키 2개 fallback (ContextVar 자동)**: 사용자 요청별 키 격리, 한도 초과 시 자동 fallback.
- **웹 스크래핑**: 최소 2초 간격. 배치 금지.
- **3-tier fallback**: XML → PDF (4s+) → OCR (Upstage)
- **rcept_no 포맷**: `00`=소집공고(DART 정기공시), `80`=주총결과(거래소 수시공시). agm_*_xml에는 반드시 `00` 포맷 사용.
- **파이프라인**: 전체 재실행 금지, 누락분만 처리.
- **저장 안 함**: OPM은 실시간 조회, 데이터 저장 X.

상세 규칙은 위키의 해당 페이지 참조:
- DART API → `wiki/archive/entities/DART-OpenAPI.md` (구 entity 페이지, archive 보존)
- fallback → `wiki/architecture/3-tier-fallback.md`
- 데이터 수집 전체 → `wiki/architecture/data-collection.md`
- 공시 유형 → `wiki/rules/disclosures/`
- 도메인 개념 → `wiki/rules/concepts/`

## 문서 포인터
- 미완료 작업 → `TO_DO.md` — **완료된 항목은 즉시 삭제. 완료 섹션 없음. 미완료만 유지.**
- 개발 히스토리 → `DEVLOG.md`
- wiki 작업 로그 → `wiki/log.md`
- tool 규칙 (구) → `open_proxy_mcp/*_RULE.md` (점진 흡수)

## 로컬 셋업
```bash
git clone https://github.com/MarcoYou/open-proxy-mcp.git && cd open-proxy-mcp
uv sync && cp .env.example .env  # OPENDART_API_KEY 설정
```

## 개발 방식
- Build → Check → Pass 사이클. 의미 있는 변경마다 커밋.
- `/ship` 시 wiki 자동 업데이트 (코드 변경 → 관련 위키 페이지 갱신).
- 신규 tool/공시/개념 추가 시 [[WIKI_SCHEMA]] 워크플로우 따라 명명 + frontmatter + index.md update.
