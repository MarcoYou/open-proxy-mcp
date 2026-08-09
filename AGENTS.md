# OPM (OpenProxy MCP)

DART 공시를 MCP로 제공하는 Python 서버. 한국 상장사 재무·사업·지배구조·주총·지분·배당·위임장·의결권 분석을 지원한다.

## 작업 원칙

1. **정확성 > 속도.** 수치·파싱 결과는 스크립트 출력만 믿지 말고 원문 표본과 함께 검증한다.
2. **가설 → 엣지케이스 → 표본 테스트 → 전수 diff.** 파서·계산 로직을 바로 고치지 말고 오탐/누락과 부분집단을 먼저 측정한다.
3. **production 경로 우선.** 실제 동작 확인은 MCP 호출을 먼저 사용하고, 직접 import는 단위 테스트·디버깅에 쓴다.
4. **공유 파생지표는 재사용.** 시총·주식수·환율 등은 검증된 공통 service/캐시를 사용하고 tool별로 다시 계산하지 않는다.
5. **이름 기반 접근.** SQL `INSERT`는 컬럼명을 명시하고, 튜플 위치 언패킹·암묵적 정렬보다 dict/명시적 `key=`를 우선한다.

## Wiki-first

도메인 지식·아키텍처·공시 유형·결정의 정본은 `wiki/`다. 질문이 오면 **`wiki/wiki_index.md` → 필요한 관련 페이지만** 읽고 전체 wiki를 한꺼번에 로드하지 않는다.

| 필요 | 위치 |
|---|---|
| 처음 진입 / tool 카탈로그 | `wiki/tools/README.md` |
| 사람용 개요·아키텍처·발표자료 | `wiki/guide/` |
| 전체 색인 / 트리·명명·link 정책 | `wiki/wiki_index.md` / `wiki/wiki_schema.md` |
| 공시 유형·검색 코드 | `wiki/rules/disclosures/공시유형코드체계.md` |
| 법령 / 도메인 개념 | `wiki/rules/laws/` / `wiki/rules/concepts/` |
| 데이터 수집·폴백·병렬화 | `wiki/architecture/` |
| 의결권 정책·판단 구조 | `wiki/decisions/open-proxy-guideline.md` / `wiki/architecture/proxy-voting-decision-tree.md` |
| tool별 DART 호출 budget | `wiki/tools/tool_call_budget.md` |
| 작업 이유·회고 | private `open-proxy-storage/wiki-private/lessons/` |

### Wiki 수정 규칙

- `wiki/raw/`는 외부 원본이다. **절대 수정하지 않는다.** 분석·요약은 `rules/`, `architecture/`, `decisions/`에 작성한다.
- 새 tool/공시/개념 추가 시 코드, 해당 wiki 페이지, 폴더 README, `wiki/wiki_index.md`를 함께 갱신한다.
- 시점 문서는 `yymmdd_hhmm_{type}_{title}.md`, 정체성 문서는 `{name}.md`를 사용한다. 상세 schema는 `wiki/wiki_schema.md`가 SSOT다.
- index 카운트는 손으로 고치지 않는다. 파일 추가·삭제 후 `python3 scripts/gen_index.py`를 실행한다.
- wiki 변경 후 반드시 다음을 통과시킨다.

```bash
python3 scripts/gen_index.py --check
python3 scripts/wiki_lint.py --strict
```

## 프로젝트 구조

```text
open_proxy_mcp/
  server.py            # MCPServer 진입점·build_app()·HTTP middleware
  tools/               # public MCP tool facades
  services/            # 도메인 분석·파싱 로직
  dart/client.py       # DART/KIND 접근·throttle·cache·회사 식별
  data/asset_managers/ # 익명화 정책·행사내역·설계 자산
  data/ksic/           # 산업분류 매핑
scripts/               # audit/census/spot/wiki·법령·시장 snapshot 작업
tests/                 # tracked unit/regression tests
wiki/                  # public 도메인 지식과 결정 기록
.github/workflows/     # deploy·wiki lint·법령/시장 snapshot jobs
```

## 데이터·호출 안전 규칙

- **접근 순서**: DART OpenAPI/`document.xml` → 필요한 경우 service가 정의한 DART viewer HTML fallback → 허용된 KIND fallback. 상위 소스에서 해결되면 하위 접근 금지.
- **PDF/OCR 없음**: PDF 다운로드·Upstage OCR·opendataloader는 2026-07-12 OPM에서 제거되어 `open-proxy-ai`로 이관됐다.
- **DART API hard limit**: 분당 1,000회 초과 시 24시간 IP 차단. `DartClient` rolling-window cap은 **910/min**이다.
- batch 전 회사수 × 예상 호출수를 계산한다. 최대 30사 단위로 나누고 batch 사이 sleep. 100사 이상은 별도 운영 환경을 사용한다.
- 독립 script는 client 생성마다 limiter가 분리될 수 있다. 동시성 1~2, 호출 간 sleep, `ReadError` 즉시 중단을 기본으로 한다.
- API 키를 늘려도 IP 단위 분당 cap은 늘지 않는다. `OPENDART_API_KEY`, `_2`, `_3`… 다중 fallback은 일일 quota 대응용이다.
- DART 웹/KIND scraping은 최소 2초 간격이며 배치 scraping은 금지한다.
- API 키가 들어간 URL·쿼리·예외 메시지는 **전체는 물론 prefix도 출력·로그·fixture에 저장하지 않는다.**
- 공시 검색은 `pblntf_ty` + `pblntf_detail_ty`로 먼저 좁히고 제목을 매칭한다. 전체 순회 금지. 회사 없는 시장검색은 최대 3개월.
- `rcept_no`: `00`은 소집공고(DART 정기공시), `80`은 주총결과(거래소 수시공시). AGM XML 경로에는 `00`을 사용한다.
- 사용자 조회 결과는 저장하지 않는다. 예외는 corp-code/document cache, 시장 snapshot, 운영 usage telemetry 같은 명시적 인프라 데이터다.
- 전체 파이프라인 재실행 금지. 누락분만 처리하고 resume 가능한 script로 작성한다.

## Public / Private

이 repository는 PUBLIC이다. usage 원자료·비공개 사업자료·private lessons·내부 schema는 `open-proxy-storage`에 둔다. private 자산의 절대경로·내용·심링크를 public Git에 커밋하지 않는다.

## 테스트·검증

- 기본 `pytest` 수집 경계는 `pyproject.toml`의 `tests/`로 고정되어 있다.

```bash
uv run pytest -q
```

- unit/regression test는 기본적으로 network/DART 0콜이어야 한다. live 검증은 명시적 spot/integration script로 분리하고 호출 budget을 먼저 확인한다.
- wiki-only 변경은 wiki lint, Python 변경은 관련 unit test + 필요한 spot script, tool 동작 변경은 가능하면 production MCP smoke까지 수행한다.
- 테스트 실패를 기존 실패로 가정하지 않는다. 현재 branch에서 재현하고 원인과 영향 범위를 기록한다.

## 셋업·개발

```bash
git clone https://github.com/MarcoYou/open-proxy-mcp.git
cd open-proxy-mcp
uv sync
# 필요한 환경변수는 wiki/architecture/environment-secrets.md 참조
```

```bash
uv run python -m open_proxy_mcp.server --transport stdio
```

- Build → Check → Pass. 의미 있는 변경마다 검증하되 commit/push/deploy는 사용자가 명시적으로 요청할 때만 한다.
- 작업 지시가 바뀌면 일회성 audit/census/diagnosis script도 새 필터·대상·필드에 맞게 갱신한 뒤 실행한다.
