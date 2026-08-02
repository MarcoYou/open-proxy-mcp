# OPM (OpenProxy MCP)

DART 공시를 MCP로 제공하는 Python 서버. 한국 상장사 재무·사업·지배구조·주총·지분·배당·위임장·의결권 분석.

## 작업 수행 원칙 (모든 작업에 우선 적용)

1. **정확성 > 속도.** 빠른 결론보다 맞는 결론. 스크립트가 숫자를 내도 단정하지 말고 검증한다.
2. **정확성 = 큰 표본 × 이중 검증.** ① 기계적(스크립트·전수 diff) **그리고** ② 사람-독자 관점(직접 표본을 눈으로 읽음) — 둘 다 한다. 측정 도구의 가정(production 경로·ground truth·패턴 엄격도)을 먼저 의심한다. 사용자가 시키기 전에 default로. 상세·5패턴·체크리스트: private lessons(`~/Projects/open-proxy-storage/wiki-private/lessons/agenda-parser-validation-260621.md`).
   - **회귀 캐시는 DART 응답 경계(`get_document_cached`)에서만 만든다 — 중간 함수의 출력은 회귀 입력이 될 수 없다.** 「production 함수를 import했다」는 검증의 근거가 못 된다. **함수가 아니라 입력이 기준**이다. 260731 사고: `_fetch_biz`/`_fetch_note`(viewer HTML) 결과를 캐시해 geo 회귀를 돌리고 「검출 8→20사」를 보고했는데, 프로덕션 주 경로는 `_fetch_getdoc`(document.xml)이라 실제로는 14→16이었다. 두 원본은 구조 표지가 정반대다(document.xml=AASSOCNOTE·ACODE 100%·toc앵커 0% / viewer=반대). 이미 `opm_cache`(=`get_document_cached` 디스크 캐시)라는 올바른 캐시가 있으니 그것만 재생 소재로 쓴다.
   - **실적·재무 큰 수치가 "이상하다/불가능하다" 싶으면 — 서사(오류일 것) 먼저 만들지 말고 웹서칭으로 제3의 소스(뉴스·IR·공시)부터 검증한다.** 실제로 맞는 값을 "오류"로 단정해 가드·플래그를 넣으면 진짜 데이터를 오탐한다(260705 삼성전자 2026 1Q 영업이익 57조 슈퍼사이클 신기록을 "분기 57조는 불가능"이라 오판할 뻔 → 웹 검증으로 실제 확인). 큰 수치 의심 = ground truth 웹 확인이 default.
   - **회계·재무 판단이 들어간 로직(자산분류·지표계산·밸류에이션 등)은 K-IFRS 정합성 검토 + Data QA(측정·회귀 검증)를 거친 뒤 반영한다.** 사람 직관만으로 분류·수식을 설계하면 잘못된 회계개념을 코드에 굳힐 위험이 있다. 필요시 부동산·가치투자·공시 관점 검토를 병행한다. 실측 사례: [[260721_1500_decision_asset-holdings-purpose-buckets]] · private lessons(`.../asset-holdings-census-260720.md`).
3. **작업이 아니라 목표를 본다.** 시킨 일만 수행하지 말고 — 그 작업의 목표·원칙·전체 프로젝트/환경과의 연관성을 함께 고려해 판단한다.
4. **가설은 바로 실행하지 않는다 — 엣지케이스 상상 → 테스트 → 통계검증까지 마친 뒤 실행한다.** "A를 B로 고치면 정확도 오른다"는 가설을 세우면 곧장 코드/실행으로 가지 말고, ① 이 가설이 **깨질 엣지케이스를 먼저 상상해 나열**하고 ② 표본으로 **테스트**해 ③ **통계적으로**(일치율·오탐율·before/after 전수 diff·부분집단 슬라이스) 검증을 마친 뒤 실행한다. 260710 이사 교차검증 사례: "정형 데이터로 텍스트 파싱을 override하면 정확도↑" 가설이 신임 후보 폼아티팩트에서 깨졌고(naive override면 clean 사외이사 오탐), **연임만 슬라이스해 통계검증**하니 진짜 성과(재선임→신임 오분류)가 드러났다. 가설→즉시실행이었으면 정반대 결과. (원칙 2와 연동 — 통계검증은 기계+사람-독자 이중검증 위에서)
5. **「지금 아는 것」과 「그때 알 수 있던 것」을 가른다.** 우리는 전 기간 데이터를 손에 쥐고 있지만, 판단의 기준 시점에 그 데이터가 **존재했는지**는 별개다. 섞으면 look-ahead — 그때는 알 수 없던 정보로 그때의 판단을 채점하게 된다. ① 어떤 값을 쓸 때 **그 값이 공시된 날**과 **판단 기준일**을 함께 확인한다. ② 공시 의무의 기한이 다르면 순서가 갈린다 — 소집공고는 상법 §363(주총 2주 전), 사업보고서는 자본시장법(결산 후 90일). 실측 88사 중 78사(89%)가 소집공고 먼저, 중앙값 7일. 그래서 `proxy_advise`의 `fin_year = target_year - 2`(마지막 확정 감사 재무제표)는 편의가 아니라 **시점 제약**이다. ③ 시점이 다른 두 소스를 합칠 땐 어느 쪽이 언제 것인지 산출물에 밝힌다(예: 등기 재직기간의 `fiscal_year`). ④ **반대로, 같은 문서 안에 있는 정보는 시점 문제가 아니다** — 260729 이사 경력 사례가 그랬다. 「그때는 몰랐을 것」이라는 서사를 먼저 만들지 말고 원문에 있었는지부터 확인한다(원칙 2와 연동).

## wiki 참조 (wiki-first)

도메인 지식·설계·결정은 모두 wiki에 있다. **질문이 오면 wiki에서 필요한 페이지만 골라 읽는다**
(전체 로드 X). LLM이 wiki를 유지하며 `/ship`이 영향 페이지를 갱신한다.

**판단의 모호성이 있을 경우 — 추측·서사로 덮지 말고** 아래 매핑표 → 관련 lessons(private, `open-proxy-storage/wiki-private/lessons/`) 순으로 확인하고,
그래도 불명확하면 사용자에게 물어라. (작업 수행 원칙 2·3과 연동)

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
| 작업 이유·회고 | **private** `open-proxy-storage/wiki-private/lessons/` (260720 이관 — 새 lesson도 여기에) |
| **작업·데이터 검증 방법** (전수·표본·측정 함정·프로토콜) | private lessons README ④ 검증 방법론 (대표 `agenda-parser-validation-260621`: 측정 함정 5패턴 + 체크리스트) |
| 전체 색인 / 트리·명명·link 정책 | `wiki/wiki_index.md` / `wiki/wiki_schema.md` |

**wiki 작성 규칙** (상세 [[wiki_schema]]):
- **명명**: 시점작업 `yymmdd_hhmm_{type}_{title}` · 정체성 `{name}` · lessons `{topic}-yymmdd`. public 시점작업과 private lesson의 연결 규칙은 `wiki/wiki_schema.md`와 private lessons README를 따른다.
- **link & README**: raw→rules→큰가지 단방향 / 큰가지↔잔가지 양방향 · **폴더에 파일 추가/삭제 시 해당 README를 `[[]]` 인덱스로 갱신**. 변경 시 `python3 scripts/wiki_lint.py --strict` 필수 — link 방향 + 양방향 + **README drift([3])** + index 카운트([4]) + 경로 오링크([5]) + archive superseded([6]) + **상법 시행일 3자 정합([7])** + **규칙 이중장부([8]: 규칙 SSOT=`wiki_schema.md`, `wiki_index.md`엔 규칙 서술 금지)** 자동 검증(누락 시 실패). 시행일은 `wiki/rules/laws/law_provisions.json`(SSOT)만 고치고 `scripts/gen_law_timeline.py`로 md 표 재생성 — 엔진 `applies_after`는 layer별(A2=시행일/A1=공포·시행)로 검사됨.
- **wiki_index 카운트는 손으로 고치지 말 것** — 폴더-앵커 카운트(`### X (N) - `folder/``·archive 서브·총계)는 `scripts/gen_index.py`가 filesystem에서 파생 생성(gen_index와 lint [4]는 로직 공유로 불일치 불가). 파일 추가/삭제 후 `python3 scripts/gen_index.py`로 재동기화, CI는 `--check`로 강제. 규칙 SSOT는 [[wiki_schema]].
- **`raw/` 절대 수정 금지** (외부 원본). 신규 tool/공시/개념 = 코드 + wiki 페이지 + `wiki_index.md` 동반 갱신.
- DART 콜 수 바뀌면 `tools/tool_call_budget.md` 갱신 — **per-firm vs market-scan** 모드 구분 필수.

## 프로젝트 구조
```
open_proxy_mcp/
  server.py            # FastMCP 진입점
  tools/               # public MCP tool facades
  services/            # 도메인 분석 로직 (tool과 분리)
  dart/client.py       # DART API + KIND + 네이버 시세
  data/asset_managers/ # 운용사 정책(익명) + 행사내역 + 12 매트릭스(설계 자산)
                       #   ※ 의결권 엔진 = 법령 layer + vote_style 정책 + _decide_* 함수.
                       #     12 매트릭스 자동채점은 미사용(dead code) — 사내이사 성과 2x3만 실사용.
  data/ksic/           # 산업분류 코드→업종명
scripts/               # wiki_lint.py(link 검증) · spot_*.py(회귀) · verify_law_against_corpus.py(SSOT↔legalize-kr 원문대조)
                       #   live_pilot_diff.py(live↔pilot 코드 시점 차이 추적)
wiki/                  # 도메인 지식 (위 'wiki 참조' 표 참조)
.github/workflows/     # wiki-lint.yml · deploy.yml(fly.io)
```

## 핵심 규칙
- **호출 우선순위**: ① MCP 호출(production 검증) → ② 직접 import(테스트·디버깅).
- **데이터 접근**: ① DART API(병렬) → ② DART 웹(2초 간격) → ③ KIND(2초). 상위 해결 시 하위 금지.
- **DART API 분당 1,000회 초과 → 24h IP 차단 (hard rule, 절대 위반 X)**: cap **910**(요청별 키 + `OPENDART_API_KEY_2`, `_3`… 다중 fallback; 키 수와 무관하게 IP cap은 동일) · batch **최대 30사 + 사이 sleep**(100+사는 별도 운영 환경) · **독립 스크립트는 동시성 1~2 + sleep + ReadError 즉시 중단**. 차단 시 키 회전 무효(IP level)·24h. 메커니즘·260607 사고: [[hard-rate-limit]].
- **웹 스크래핑**: 최소 2초 간격, 배치 금지.
- **문서 본문은 `document.xml`/XML 우선** (OPM): AGM·이사보팅·proxy_advise 핵심 경로는
  `get_document_cached`를 사용한다. 일부 service가 명시적으로 둔 DART viewer HTML fallback은 허용하되,
  상위 소스에서 해결되면 호출하지 않는다. XML 불완전 시 원문을 AI에 노출해 보정(soft-fail)하고, 조작된 FOR는 내지 않는다. **PDF 다운로드·
  OCR(Upstage)·opendataloader 폴백은 2026-07-12 OPM에서 폐기하고 고급 프로덕트 open-proxy-ai로 이관**
  (sibling private project `open-proxy-ai` — `pipeline/pdf_parser.py` + `pipeline/pdf_download.py`, 폴백 전용).
- **rcept_no 포맷**: `00`=소집공고(DART 정기) / `80`=주총결과(거래소 수시). agm_*_xml에는 `00` 사용.
- **공시 검색**: `list.json`에서 `pblntf_ty`+`pblntf_detail_ty`로 범위 먼저 좁히고 제목 매칭(전체 순회
  금지). 코드 매핑은 `rules/disclosures/공시유형코드체계.md`. corp_code 없는 시장검색은 3개월 한도.
- **파이프라인**: 전체 재실행 금지, 누락분만 처리.
- **사용자 조회 결과 저장 안 함**: 실시간 조회가 원칙. corp-code/document cache, 시장 snapshot, 운영 usage telemetry는 명시적 인프라 예외다.
- **키 비노출**: API 키가 든 URL·query·예외는 전체뿐 아니라 prefix도 stdout/log/fixture에 남기지 않는다.
- **public/private 분리**: 이 레포=PUBLIC, `open-proxy-storage`=PRIVATE. 실측 usage 메트릭·LinkedIn용
  자산·Supabase 스키마 등 **비공개는 private로 이관**. private 스킬/자산은 `.claude/skills/`에 **심링크로
  연결하되 그 심링크는 반드시 `.gitignore`**(public에 커밋 금지 — 절대경로·비공개 구조 노출 방지). public
  실체 스킬(예: `opm-tool-validation`)만 추적. **스킬/자산 수정은 private 원본에서 커밋** · 커밋도
  public·private 각각 분리. 상세 배치는 private `wiki-private/`.
- **순서/위치 기반 접근 금지 — 이름 기반으로.** SQL `INSERT`는 컬럼명을 반드시 명시(`INSERT INTO t
  (a,b,c) VALUES(...)`) — 위치 의존(`VALUES(...)`만)은 `ALTER TABLE ADD COLUMN`으로 물리적 컬럼
  순서가 바뀌면 **조용히 다른 컬럼에 값이 들어가는 사고**로 이어짐(260704 mkt_fund_hist 사고: DDL
  선언 순서와 실제 테이블 순서가 어긋나 문자열이 `double precision` 컬럼에 들어가 에러). 같은 원리로
  튜플 인덱스·위치 언패킹보다 dict/네임드튜플/컬럼명 매핑을 우선. **정렬도 마찬가지** — 튜플 전체비교
  (`sort(reverse=True)`)는 첫 원소가 동률일 때 다음 원소(dict 등 비교불가 타입)까지 비교돼 크래시하니
  `key=` 명시.
- **계산 지표는 단일 소스 재사용 — 독자 재계산 금지.** 시총·주식수 등 여러 tool이 공유하는 파생지표는
  이미 검증된 곳(예: `valuation.py`의 `_market_for`, KRX 캐시)을 재사용한다. 각자 다르게 계산하면
  tool마다 다른 숫자가 나와도 아무도 모르고 지나간다. 실측 사례: [[asset_holdings]] 변경 이력(260721).

## 셋업 · 개발
```bash
git clone https://github.com/MarcoYou/open-proxy-mcp.git && cd open-proxy-mcp
uv sync
# 환경변수: 루트에 .env 생성 후 필요한 키 채움. 어떤 키가 왜 필요한지·어디 넣는지(로컬 .env +
# fly secrets)는 wiki `architecture/environment-secrets.md` 참조(단일 출처, .env.example 대체).
```
- Build → Check → Pass. 의미 있는 변경마다 검증한다. `/ship` 시 영향 wiki를 갱신한다.
- 커밋/푸시/배포는 사용자가 명시적으로 요청할 때만 수행한다.
- 기본 `pytest` 수집 경계는 `pyproject.toml`의 `tests/`로 고정되어 있다. `uv run pytest -q`를 사용하고 unit/regression은 기본 network 0콜로 유지한다.
- toolset 버전 분기는 없다.
- **MCP는 둘뿐이고, 같은 서버의 두 사본이 아니라 목적이 다른 별개 대상이다 — 따로 관리한다.**
  상세(pilot이 받아내는 변경·전송방식·상태확인·키취급): [[mcp-endpoints]].
  - `pilot-opm` = `http://127.0.0.1:8000/mcp` — **바꾼 것을 시험하는 곳.** 파서·tool·필드·
    파라미터를 고치거나 더하거나 뺐을 때 반영시킨 뒤 문제 없는지 확인한다. 사람이 띄우고 내린다.
  - `live-opm` = `https://open-proxy-mcp.fly.dev/mcp` — **사람들에게 배포해서 쓰게 하는 것.**
    관리 주체는 `deploy.yml`(fly 배포).
  - 둘 다 `streamable-http`(무상태)라 프로토콜 차이가 없다. **남는 차이는 코드 시점 하나.**
  - 그 차이는 **항상 추적한다** — `python3 scripts/live_pilot_diff.py` (배포된 커밋은 GitHub
    Deployments, tool 개수는 양쪽 `/health`). SessionStart 훅이 세션마다 자동으로 띄운다.
- **`--transport stdio`로 OPM MCP를 띄우지 않는다.** stdio는 세션이 뜰 때 **그 시점 코드를
  메모리에 붙들어**, 코드를 고쳐도 그 세션의 MCP는 계속 옛 결과를 낸다(260802 확인). 로컬 검증은
  pilot(HTTP)으로만 한다.
- **개발 중 검증은 `pilot-opm`, 배포 후 확인은 `live-opm` (260731 이후 표준).**
  `preview_start(name="pilot-opm")` → `POST http://127.0.0.1:8000/mcp?opendart=<키>`
  (키는 `.env`에서 읽고 **출력하지 않는다**). 코드 고치면 `preview_stop` → `preview_start`.
  코드가 맞는 것과 배포가 반영된 것은 **별개 문제**라 배포 뒤 live로 한 번 더 본다.
  **payload가 맞아도 렌더러가 안 쓰면 사용자는 못 본다** — pilot이 그걸 잡는다
  (260731: 해외비중·부재 신호·II 수출이 payload엔 있는데 md 렌더에 없었다).
- **작업용 script는 지시마다 갱신할 것**: 사용자 지시를 수행하려고 만든 일회성 script(audit·census·
  diagnosis·전수조사 등)는, 지시가 바뀌거나 세부가 업데이트될 때마다 **script를 그 지시에 맞게 함께
  수정한 뒤 실행**한다. 이전에 만든 script를 그대로 재사용해 진행하지 말 것 — stale 로직(옛 필터·옛
  대상·옛 필드명)이 지시와 어긋난 잘못된 결과를 낸다.
