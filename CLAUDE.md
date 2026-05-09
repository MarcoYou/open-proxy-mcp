# OPM (OpenProxy MCP)

DART 데이터를 MCP로 제공하는 Python 서버. 약칭 **OPM**.
한국 상장사 거버넌스 분석 (주총, 지분, 배당, 위임장).

## 지식 체계 (wiki-first)

**도메인 지식, 아키텍처 결정, 공시 유형 등 상세는 위키 참조.**
위키는 LLM이 유지하며, `/ship` 시 LLM이 영향 페이지 update + commit + push.

질문이 오면 `wiki/index.md` (전체 카탈로그)를 먼저 읽고 관련 페이지만 선택 로드. 전체 wiki 한 번에 로드 X.

- **첫 진입**: [[tools/README]] (16 tool 카탈로그)
- **위키 스키마**: [[WIKI_SCHEMA]] — 트리 정책 + 카테고리 + 명명 + frontmatter + 워크플로우
- **트리 흐름**: `raw` (뿌리) → `rules` (줄기) → `tools/decisions/architecture/core` (큰가지) → `ralph/audits/fixes/lessons` (잔가지) → `archive` (낙엽)
- **Link 정책**: 뿌리→줄기→큰가지 단방향 / 큰가지↔잔가지 양방향 / 잎↔잎 자유

### 명명 규칙 (2026-05-01~)

| 종류 | 패턴 | 예시 |
|---|---|---|
| 시점 작업 | `yymmdd_hhmm_{type}_{title}.md` | `260508_0700_decision_law-layer-precision.md` |
| 정체성 | `{name}.md` | `shareholder_meeting_notice.md` / `자사주.md` |
| lessons | 정체성 또는 `{topic}-yymmdd.md` | `parser-precision-260508.md` |

시점 type: audit / fix / decision / debate / ralph / improvement / changelog / release / log (자세한 type 정의는 [[WIKI_SCHEMA]]).

신규 페이지 추가 시 [[WIKI_SCHEMA]] 워크플로우 따를 것.

### 시점 작업 4축 (양방향 link 강제)

ralph → audit → lesson → decision 신규 시 frontmatter `related_ralph/audits/lessons/decisions: [...]`로 4축 양방향 link. 상세 + snippet: [[WIKI_SCHEMA#0.3 같은 시점 작업의 4축 표준]]

### raw/ 절대 수정 금지
`wiki/raw/`는 외부 원본 (운용사 정책 PDF, 행사내역 xlsx, 외부 reference markdown).
LLM도 사람도 절대 수정 X. 분석/요약은 별도 페이지에 작성 (`architecture/`, `decisions/`, `rules/`).

## 프로젝트 구조
```
open_proxy_mcp/        # MCP 서버 코드
  server.py            # FastMCP 진입점
  tools_v2/            # 16 public tools (v2 — active)
  services/            # 도메인별 분석 로직 (tool과 분리)
  dart/client.py       # DART API + KIND + 네이버 시세
  data/asset_managers/ # 8 운용사 정책 (익명화) + 행사내역 + Open Proxy Guideline + 12 매트릭스
scripts/
  wiki_lint.py         # wiki link 정책 자동 검증 (단방향/양방향)
  spot_*.py            # 회귀 spot 스크립트
wiki/                  # LLM 도메인 지식 (트리 layer는 위 섹션 참조)
  raw/                 # 외부 원본 (정책 PDF + xlsx + reference). 절대 수정 금지
  rules/               # concepts/ + disclosures/ + laws/
  tools/               # 16 tool 카탈로그 (사용자 진입점)
  decisions/           # OPM 정책 (open-proxy-guideline 등)
  architecture/        # 시스템 설계 + audits/ + fixes/
  ralph/ + lessons/    # 작업 plan (yymmdd_hhmm) + 회고
  archive/             # 흡수/대체 보존 (신규 X)
                       #   archive/tools/legacy_rules/ — 구 *_RULE.md 7개
  index.md             # 전체 인덱스 (시작점)
  WIKI_SCHEMA.md       # 트리 정책 + 카테고리 + 명명 규칙
  log.md               # 작업 로그
.github/workflows/
  wiki-lint.yml        # wiki/ 변경 시 lint --strict 자동 (PR/push)
  deploy.yml           # fly.io 배포
```

## 핵심 규칙 (간략)
- **사용 호출 우선순위**: ① **MCP 호출** (default — production 동작 검증) → ② 직접 코드 import (단위 테스트 / 디버깅 시).
- **데이터 접근 우선순위**: ① DART API (병렬 가능) → ② DART 웹 크롤링 (2초 간격) → ③ KIND 크롤링 (2초 간격). 상위에서 해결되면 하위 접근 금지.
- **DART API**: 분당 1,000회 초과 시 24시간 IP 차단 — **hard rule, 절대 위반 X**.
  - `dart/client.py`에 rolling window rate limiter (`_throttle_api`) 내장 — 분당 cap **900** (10% buffer + race 방지).
  - 새 batch script 작성 시: 회사수 × 평균 호출수 estimate, **최대 30 회사 단위** + batch 사이 sleep. 100+ 회사 측정은 fly machine (다른 IP) 활용.
  - 차단 시 키 회전 무효 (IP/fingerprint level 차단). 24h cool-down.
  - **DART API 키 2개 fallback (ContextVar 자동)**: 사용자 요청별 키 격리.
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
- **wiki 변경 시 link 정책 검증 필수**:
  ```bash
  python3 scripts/wiki_lint.py --strict
  ```
  - 단방향 위반 (rules → 큰가지) + 양방향 결손 (큰가지 ↔ 가지) 자동 검출
  - GitHub Actions `wiki-lint.yml`이 PR/push 시 자동 실행
  - 정책 상세: [[WIKI_SCHEMA#0.2 Link 방향 정책]]
