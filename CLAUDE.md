# OPM (OpenProxy MCP)

## 프로젝트 개요
DART(전자공시시스템) 데이터를 MCP 프로토콜로 제공하는 Python 서버. 약칭 **OPM**.
주주총회 소집공고를 시작으로, 재무정보 등 DART 전체 공시 데이터로 확장 예정.

## 기술 스택
- Python + FastMCP (`mcp.server.fastmcp`)
- httpx (async HTTP), python-dotenv
- BeautifulSoup4 + lxml (HTML 파싱, lxml 30% 빠름)
- OpenDART API (https://opendart.fss.or.kr/)

## 프로젝트 구조
```
open_proxy_mcp/
  server.py           # FastMCP 진입점 (auto-discovery)
  CASE_RULE.md  # 파서 성공 기준 + LLM few-shot 예시
  tools/
    __init__.py       # register_all_tools() — tool 자동 탐색
    shareholder.py    # AGM tool 33개 (agm_*)
    ownership.py      # 지분 구조 tool 7개 (own_*)
    formatters.py     # 포매터 27개 함수 (공용)
    errors.py         # 공통 에러 헬퍼
    parser.py         # XML 파서 (bs4+regex)
    pdf_parser.py     # PDF 파서 + Upstage OCR fallback
  dart/
    client.py         # OpenDART API + KIND 크롤링 + 싱글턴
  llm/
    client.py         # LLM fallback (Claude Sonnet / OpenAI)
```

## 출력 포맷
- **Markdown**: MCP 연결 시 기본 출력. `format="md"` (기본)
- **JSON**: 파이프라인/프론트엔드용. `format="json"`

## 파서 아키텍처
1. `client.py` — DART API에서 HTML+text 동시 반환
2. `parser.py` — bs4(lxml)로 HTML 섹션 경계 추출 → regex로 안건 패턴 매치
3. 실패 시 text-only regex fallback → 그래도 실패 시 LLM fallback

**3-tier fallback:** XML (DART API) → PDF (opendataloader) → OCR (Upstage)
**처리율:** KOSPI 200 (199개) 기준 XML 97-99%, PDF 97-100%, OCR 100%.

## 파서 테스트-개선 루프
1. KOSPI 200 (199개) 대상 `test/` 스크립트 사용
2. `parse_*_xml()` 실행, 실패 시 PDF/OCR fallback 확인
3. 실패 케이스 zone 텍스트 확인 → 패턴 추가
4. **전수 regression 테스트** — 기존 성공 깨짐 없는지 반드시 확인

## DART API 호출 규칙
- **속도 제한**: 분당 1,000회 초과 시 **24시간 IP 차단**. `BadZipFile` 에러로 나타남.
- **일일 한도**: 개인 기준 20,000회/일.
- **안전 호출**: 배치 시 **매 호출 1초 이상 간격**, 50건마다 10초 대기.
- **캐싱**: `_doc_cache` (30건 LRU)가 동일 rcept_no 중복 호출 방지.
- **키 전환**: `OPENDART_API_KEY_2` 설정 시 에러 발생하면 자동 전환 (IP 차단은 키 전환으로 해결 불가).
- **Rate Limiter**: `client.py`에 자동 throttle — API 0.1초, 웹 2초 최소 간격.

## DART 웹 스크래핑 규칙
- **공식 API가 아님** — 과도한 요청은 IP 차단, 해제 불가.
- **최소 2초 간격**: `_MIN_INTERVAL_WEB = 2.0`
- **배치 금지**: PDF 다운로드는 1건씩, 루프 대량 다운로드 금지.
- **용도**: XML 파싱 실패 시 보조 소스. 1차 소스는 항상 OpenDART API.

## 설계 원칙
- DART API 도메인별 `dart/` 하위 모듈 분리
- MCP tool은 `tools/` 하위에 도메인별 분리
- API 키는 `.env`에서 관리, 절대 커밋 안 함
- 단일 파일 모놀리스 지양, 모듈 분리 유지

## 문서 포인터
- 파서 상세/벤치마크 → `DEVLOG.md`
- 미완료 작업 → `TO_DO.md`
- 파서 성공 기준 → `open_proxy_mcp/CASE_RULE.md`
- fallback 대상 기업 → `test/fallback_targets.json`
- 소집공고 이력 → `data/filing_tracker.json`

## 로컬 셋업
```bash
git clone https://github.com/MarcoYou/open-proxy-mcp.git
cd open-proxy-mcp
uv sync
cp .env.example .env    # OPENDART_API_KEY 설정
```

**MCP 연결** — `.mcp.json` (gitignore됨):
```json
{ "mcpServers": { "open-proxy-mcp": {
    "command": "python",
    "args": ["-m", "open_proxy_mcp"],
    "cwd": "/path/to/open-proxy-mcp"
}}}
```

**환경변수** (`.env`):
```
OPENDART_API_KEY=...              # 필수
OPENDART_API_KEY_2=...            # 선택 (백업 키)
OPENAI_API_KEY=...                # 선택 (LLM fallback)
```

## 개발 환경
- **집**: Mac (Darwin) — 주 개발 환경
- **직장**: Windows — 보조 개발 환경
- 환경 전환 시: git pull/push로 최신 유지

## 개발 방식
- **점진적 빌드**: Build → Check → Pass 사이클
- DEVLOG.md에 날짜별 작업 내역 기록. 하루 끝에 성과/실패 반드시 기록.
- TO_DO.md 확인하고 대화 시작 시 미완료 항목 리마인드.
- commit + push 자주 할 것. 의미 있는 변경마다 커밋.
