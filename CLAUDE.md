# OPM (OpenProxy MCP)

DART 공시를 MCP로 제공하는 Python 서버. 한국 상장사 거버넌스 분석 — 주총·지분·배당·위임장·의결권 보조.

## 작업 수행 원칙 (모든 작업에 우선 적용)

1. **정확성 > 속도.** 빠른 결론보다 맞는 결론. 스크립트가 숫자를 내도 단정하지 말고 검증한다.
2. **정확성 = 큰 표본 × 이중 검증.** ① 기계적(스크립트·전수 diff) **그리고** ② 사람-독자 관점(직접 표본을 눈으로 읽음) — 둘 다 한다. 측정 도구의 가정(production 경로·ground truth·패턴 엄격도)을 먼저 의심한다. 사용자가 시키기 전에 default로. 상세·5패턴·체크리스트: `wiki/lessons/agenda-parser-validation-260621.md`.
3. **작업이 아니라 목표를 본다.** 시킨 일만 수행하지 말고 — 그 작업의 목표·원칙·전체 프로젝트/환경과의 연관성을 함께 고려해 판단한다.

## wiki 참조 (wiki-first)

도메인 지식·설계·결정은 모두 wiki에 있다. **질문이 오면 wiki에서 필요한 페이지만 골라 읽는다**
(전체 로드 X). LLM이 wiki를 유지하며 `/ship`이 영향 페이지를 갱신한다.

**판단의 모호성이 있을 경우 — 추측·서사로 덮지 말고** 아래 매핑표 → 관련 `lessons/` 순으로 확인하고,
그래도 불명확하면 사용자에게 물어라(`AskUserQuestion`). (작업 수행 원칙 2·3과 연동)

**무엇이 필요한지 → 어디를 보나:**

| 필요 | wiki 위치 |
|---|---|
| 사람에게 OPM 설명 (개요·아키텍처·발표자료) | `guide/` |
| tool 사용법·입출력·데이터 출처 | `tools/README` → 개별 tool · `tools/tool_call_budget.md`(DART 콜 budget) |
| 공시 유형·검색 코드 매핑 | `rules/disclosures/공시유형코드체계.md` |
| 법령 / 도메인 개념 | `rules/laws/` · `rules/concepts/` |
| 시스템 설계·데이터 수집·폴백 | `architecture/` (`data-collection` · `3-tier-fallback` · `multi-upstream-pattern`) |
| 의결권 정책·판단 구조 | `decisions/open-proxy-guideline` · `architecture/proxy-voting-decision-tree` |
| 설계·기술 결정 (왜 이렇게 만들었나) | `decisions/` (BeautifulSoup·XML/PDF·free-paid·LLM-fallback 등) |
| 작업 이유·회고 | `lessons/` |
| **작업·데이터 검증 방법** (전수·표본·측정 함정·프로토콜) | `lessons/README` ④ 검증 방법론 카테고리 (대표 `agenda-parser-validation-260621`: 측정 함정 5패턴 + 체크리스트) |
| 전체 색인 / 트리·명명·link 정책 | `index.md` / `WIKI_SCHEMA.md` |

**wiki 작성 규칙** (상세 [[WIKI_SCHEMA]]):
- **명명**: 시점작업 `yymmdd_hhmm_{type}_{title}` · 정체성 `{name}` · lessons `{topic}-yymmdd`. 시점작업은 4축 양방향 link(ralph↔audit↔lesson↔decision).
- **link & README**: raw→rules→큰가지 단방향 / 큰가지↔잔가지 양방향 · **폴더에 파일 추가/삭제 시 해당 README를 `[[]]` 인덱스로 갱신**. 변경 시 `python3 scripts/wiki_lint.py --strict` 필수 — link 방향 + 양방향 + **README drift([3])** 자동 검증(누락 시 실패).
- **`raw/` 절대 수정 금지** (외부 원본). 신규 tool/공시/개념 = 코드 + wiki 페이지 + `index.md` 동반 갱신.
- DART 콜 수 바뀌면 `tools/tool_call_budget.md` 갱신 — **per-firm vs market-scan** 모드 구분 필수.

## 프로젝트 구조
```
open_proxy_mcp/
  server.py            # FastMCP 진입점
  tools_v2/            # 17 public tool (active)
  services/            # 도메인 분석 로직 (tool과 분리)
  dart/client.py       # DART API + KIND + 네이버 시세
  data/asset_managers/ # 운용사 정책(익명) + 행사내역 + 12 매트릭스(설계 자산)
                       #   ※ 의결권 엔진 = 법령 layer + vote_style 정책 + _decide_* 함수.
                       #     12 매트릭스 자동채점은 미사용(dead code) — 사내이사 성과 2x3만 실사용.
  data/ksic/           # 산업분류 코드→업종명
scripts/               # wiki_lint.py(link 검증) · spot_*.py(회귀)
wiki/                  # 도메인 지식 (위 'wiki 참조' 표 참조)
.github/workflows/     # wiki-lint.yml · deploy.yml(fly.io)
```

## 핵심 규칙
- **호출 우선순위**: ① MCP 호출(production 검증) → ② 직접 import(테스트·디버깅).
- **데이터 접근**: ① DART API(병렬) → ② DART 웹(2초 간격) → ③ KIND(2초). 상위 해결 시 하위 금지.
- **DART API 분당 1,000회 초과 → 24h IP 차단 (hard rule, 절대 위반 X)**: cap **910**(키 2개 fallback) · batch **최대 30사 + 사이 sleep**(100+사는 fly machine) · **독립 스크립트는 동시성 1~2 + sleep + ReadError 즉시 중단**. 차단 시 키 회전 무효(IP level)·24h. 메커니즘·260607 사고: [[hard-rate-limit]].
- **웹 스크래핑**: 최소 2초 간격, 배치 금지.
- **3-tier fallback**: XML → PDF(4s+) → OCR(Upstage).
- **rcept_no 포맷**: `00`=소집공고(DART 정기) / `80`=주총결과(거래소 수시). agm_*_xml에는 `00` 사용.
- **공시 검색**: `list.json`에서 `pblntf_ty`+`pblntf_detail_ty`로 범위 먼저 좁히고 제목 매칭(전체 순회
  금지). 코드 매핑은 `rules/disclosures/공시유형코드체계.md`. corp_code 없는 시장검색은 3개월 한도.
- **파이프라인**: 전체 재실행 금지, 누락분만 처리.
- **저장 안 함**: 실시간 조회 (master.db는 corp_code 캐시일 뿐).
- **순서/위치 기반 접근 금지 — 이름 기반으로.** SQL `INSERT`는 컬럼명을 반드시 명시(`INSERT INTO t
  (a,b,c) VALUES(...)`) — 위치 의존(`VALUES(...)`만)은 `ALTER TABLE ADD COLUMN`으로 물리적 컬럼
  순서가 바뀌면 **조용히 다른 컬럼에 값이 들어가는 사고**로 이어짐(260704 mkt_fund_hist 사고: DDL
  선언 순서와 실제 테이블 순서가 어긋나 문자열이 `double precision` 컬럼에 들어가 에러). 같은 원리로
  튜플 인덱스·위치 언패킹보다 dict/네임드튜플/컬럼명 매핑을 우선.

## 셋업 · 개발
```bash
git clone https://github.com/MarcoYou/open-proxy-mcp.git && cd open-proxy-mcp
uv sync && cp .env.example .env   # OPENDART_API_KEY 설정
```
- Build → Check → Pass. 의미 있는 변경마다 커밋. `/ship`이 wiki 자동 갱신.
- 커밋/푸시/배포는 사용자가 명시적으로 요청할 때만.
- **작업용 script는 지시마다 갱신할 것**: 사용자 지시를 수행하려고 만든 일회성 script(audit·census·
  diagnosis·전수조사 등)는, 지시가 바뀌거나 세부가 업데이트될 때마다 **script를 그 지시에 맞게 함께
  수정한 뒤 실행**한다. 이전에 만든 script를 그대로 재사용해 진행하지 말 것 — stale 로직(옛 필터·옛
  대상·옛 필드명)이 지시와 어긋난 잘못된 결과를 낸다.
