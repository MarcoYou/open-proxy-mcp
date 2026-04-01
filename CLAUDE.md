# OPM (OpenProxy MCP)

## 프로젝트 개요
DART(전자공시시스템) 데이터를 MCP 프로토콜로 제공하는 Python 서버. 약칭 **OPM**.
주주총회 소집공고를 시작으로, 재무정보 등 DART 전체 공시 데이터로 확장 예정.

## 기술 스택
- Python
- FastMCP (`mcp.server.fastmcp`) — 데코레이터 기반 MCP 서버 프레임워크
- httpx — async HTTP 클라이언트
- python-dotenv — 환경변수 관리
- BeautifulSoup4 + lxml — DART 문서 HTML 파싱 (테이블 구조 보존, lxml 30% 빠름)
- OpenDART API (https://opendart.fss.or.kr/)

## 프로젝트 구조
```
open_proxy_mcp/       # MCP 서버 (Python)
  server.py           # FastMCP 진입점 (auto-discovery)
  CASE_DEFINITION.md  # 파서 성공 기준 + LLM few-shot 예시
  tools/
    __init__.py       # register_all_tools() — tool 자동 탐색
    shareholder.py    # AGM tool 33개 (agm_*)
    ownership.py      # 지분 구조 tool 7개 (own_*)
    formatters.py     # 포매터 27개 함수 (shareholder + ownership 공용)
    errors.py         # 공통 에러 헬퍼 (tool_error, tool_not_found)
    parser.py         # XML 파서 (bs4+regex) — parse_*_xml()
    pdf_parser.py     # PDF 파서 (opendataloader md) — parse_*_pdf() + Upstage OCR fallback
  dart/
    client.py         # OpenDART API + KIND 크롤링 + 싱글턴 (get_dart_client)
  llm/
    client.py         # LLM fallback (Claude Sonnet / OpenAI)
data/
  filing_tracker.json # KOSPI 200 소집공고 이력 트래킹
test/                 # 테스트 스크립트 + 데이터 (gitignore)

OpenProxy/            # 프론트엔드 (React/Vite) — git clone from HojiPark/openproxy
  frontend/
    src/data/
      schema.ts       # v3 통합 스키마 타입
      mockData.ts     # JSON → 프론트엔드 데이터 변환
      pipeline/       # MCP에서 생성한 v3 JSON 파일
    src/components/
      AgendaAnalysis.tsx  # 안건 상세 렌더링 (재무 테이블, 후보자 등)

samples/              # 로컬 샘플 (gitignore)
README.md             # 영문 — tool 체계 + 데이터 흐름
README_KR.md          # 한국어 — 상세 설명
```

## 출력 포맷
- **Markdown (md)**: LLM이 MCP 연결해서 사용할 때의 출력. 사람이 읽기 좋은 형태.
- **JSON**: 프론트엔드에 붙이는 용도. v3 스키마(`schema.ts`) 호환.
- 모든 tool은 `format="md"` (기본) / `format="json"` 선택 가능.

## 프론트엔드 수정 가이드
- display/UI 관련 수정 → `OpenProxy/frontend/src/` 안의 파일 수정
- 데이터/파싱 관련 수정 → `open_proxy_mcp/tools/` 안의 파일 수정
- v3 JSON 스키마 변경 → `OpenProxy/frontend/src/data/schema.ts` + `mockData.ts` 동시 수정
- OpenProxy는 별도 git repo (HojiPark/openproxy) — 서브디렉토리로 클론한 것

## DART API 호출 규칙
- **속도 제한**: 분당 1,000회 초과 시 **24시간 IP 차단**. `BadZipFile` 에러로 나타남.
- **일일 한도**: 개인 기준 20,000회/일. 초과 시 호출 실패.
- **차단 해제**: 보통 다음 날 자동 해제. 지속 시 API Key 발급 사이트에서 IP 해제 요청.
- **안전 호출**: 배치 테스트 시 **매 호출 1초 이상 간격**, 50건마다 10초 대기. 사전에 총 호출 수 계산.
- **캐싱 활용**: `_doc_cache` (30건 LRU)가 동일 rcept_no 중복 호출 방지. 같은 문서에 여러 파서 돌릴 때 반드시 캐시 통해 호출.
- **키 전환**: `.env`에 `OPENDART_API_KEY_2` 설정 시, 에러 발생하면 자동으로 보조 키로 전환 (단 IP 차단은 키 전환으로 해결 불가).
- **Rate Limiter**: `client.py`에 자동 throttle 적용 — API 0.1초, 웹 2초 최소 간격.

## DART 웹 스크래핑 규칙 (PDF 다운로드 등)
- ⚠️ **공식 API가 아님** — 과도한 요청은 DDoS로 오해받을 수 있음. IP 차단 시 해제 불가.
- **최소 2초 간격**: `_MIN_INTERVAL_WEB = 2.0` (client.py에서 강제 적용)
- **배치 금지**: PDF 다운로드는 한 번에 1건씩, 필요할 때만. 절대 루프로 대량 다운로드하지 않을 것.
- **User-Agent 명시**: 프로젝트 정보를 User-Agent에 포함하여 봇 목적을 투명하게 밝힘.
- **PDF 경로**: `get_document_pdf(rcept_no)` → 웹에서 dcm_no 추출 → PDF 다운로드 (총 2회 요청, 4초+ 소요).
- **용도**: XML 파싱 실패 시 보조 소스, LLM fallback 품질 향상. 1차 소스는 항상 OpenDART API(document.xml).

## 설계 원칙
- 각 DART API 도메인(공시, 재무, 지분 등)은 `dart/` 하위 모듈로 분리
- MCP tool은 `tools/` 하위에 도메인별로 분리
- API 키는 `.env`에서 관리, 절대 커밋하지 않음
- corpCode.xml 등 무거운 데이터는 캐싱 적용
- 입력값(날짜 형식 등) 검증 처리
- 단일 파일 모놀리스 지양, 모듈 분리 유지

## CLAUDE.md 작성 원칙
**이 파일은 가볍게 유지할 것.** 상세 내용을 여기에 직접 쓰지 않고, 특정 케이스에 어떤 문서를 참고해야 하는지 포인터로 안내하는 방식으로 작성한다.
- 파서 상세/벤치마크/실패 케이스 → `DEVLOG.md` 참조
- 미완료 작업 → `TO_DO.md` 참조
- 파서 성공 기준 / LLM fallback 예시 → `open_proxy_mcp/CASE_DEFINITION.md` 참조
- fallback 대상 기업 목록 → `test/fallback_targets.json` 참조
- 소집공고 이력 트래킹 → `data/filing_tracker.json` 참조
- 프로젝트 히스토리 → `git log` 참조

## 개발 방식
- **점진적 빌드**: 한 번에 하나씩 만들고 확인하고 넘어감
- **Build → Check → Pass** 사이클:
  1. 작은 단위 하나 구현 (build 1 case)
  2. 실행/테스트로 동작 확인 (check)
  3. 문제 있으면 수정, 통과하면 다음 단계로 (pass)
- 유저가 각 단계를 이해하고 넘어가는 것이 우선 — 속도보다 이해
- 새로운 개념이 나오면 설명 먼저, 코드 나중
- DEVLOG.md에 날짜별 작업 내역을 지속적으로 기록 (뭘 했는지, 다음 단계는 뭔지). 작업 중간중간 꾸준히 업데이트할 것. 하루 끝에 **오늘의 성과**와 **오늘의 실패/한계**를 반드시 기록.
- commit + push를 자주, 꾸준히 할 것 (유저가 별도 지시하지 않아도). 의미 있는 변경이 생길 때마다 커밋.
- TO_DO.md를 확인하고 대화 시작 시 미완료 항목을 유저에게 리마인드. 완료된 항목은 제거.

## 파서 아키텍처
파서 상세(패턴 목록, 실패 케이스 분류, 벤치마크 결과 등)는 **DEVLOG.md**의 해당 날짜 항목 참조.

**파싱 파이프라인:**
1. `client.py` — DART API에서 HTML+text 동시 반환
2. `parser.py` — bs4(lxml)로 HTML 섹션 경계 추출 → regex로 안건 패턴 매치
3. 실패 시 text-only regex fallback → 그래도 실패 시 LLM fallback

**현재 처리율:** 811건 기준 93%→97-98% (bs4+regex), count=0 실패 16건은 LLM fallback 대상.

## 파서 테스트-개선 루프
1. 소집공고 검색 (811건, `test_batch.py` 사용)
2. `parse_agenda_items()` 실행, `validate_agenda_result()` 체크
3. 실패 케이스 zone 텍스트 확인 → 패턴 추가
4. **전수 regression 테스트** — 기존 성공 깨짐 없는지 반드시 확인
5. 반복
6. ⚠️ DART API 속도 제한 주의 — 위 "DART API 호출 규칙" 참조

## 개발 환경 & 로컬 셋업
- **집**: Mac (Darwin) — 주 개발 환경
- **직장**: Windows — 보조 개발 환경
- 환경 전환 시: git pull/push로 최신 상태 유지, 대화 시작 시 `git status`로 확인하고 이전 작업 이어갈 것
- `.env`, `.mcp.json`은 환경별로 다르므로 gitignore됨

```bash
# OPM (백엔드)
git clone https://github.com/MarcoYou/open-proxy-mcp.git
cd open-proxy-mcp && git checkout feat/mcp-driven
pip install -r requirements.txt
cp .env.example .env               # OPENDART_API_KEY 설정

# OpenProxy (프론트엔드)
git clone https://github.com/HojiPark/openproxy.git OpenProxy
cd OpenProxy && git checkout feat/unified-schema
cd frontend && npm install
npx vite --port 8090               # http://localhost:8090
```

**MCP 연결** — `.mcp.json` (gitignore됨):
```json
{ "mcpServers": { "open-proxy-mcp": {
    "command": "python",
    "args": ["-m", "open_proxy_mcp"],
    "cwd": "/path/to/open-proxy-mcp"
}}}
```
- Mac: `"command": "python3"` 또는 venv 경로
- Windows: `"command": "C:\\...\\python.exe"` (절대경로)

**환경변수** (`.env`):
```
OPENDART_API_KEY=your_key_here
OPENDART_API_KEY_2=backup_key       # 선택사항
OPENAI_API_KEY=your_key             # LLM fallback용, 선택사항
```

## 주요 커맨드
```bash
pip install -r requirements.txt    # 의존성 설치
python -m open_proxy_mcp           # 서버 실행
```
