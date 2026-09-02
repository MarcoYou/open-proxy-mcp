# OPM (OpenProxy MCP)

DART 공시를 MCP로 제공하는 Python 서버. 한국 상장사 재무·사업·지배구조·주총·지분·배당·위임장·의결권 분석.

## Purpose

에이전틱 파싱 서버. 완벽한 파싱이 아니라 **원문 + 힌트를 함께 줘서 읽는 AI가 판단하게 만드는 것**이 목적.
- 값을 못 뽑으면 0/미상이 아니라 **원문 위치 + 넓힐 손잡이 + 대안 후보 + 다음 경로**를 준다.
- 원문을 지우지 않는다 — 표를 더하되 원문을 대체하지 않는다.
- 파서를 늘리기 전에 「원문을 통으로 주고 어디를 보라고 알려주면 안 되나?」를 먼저 묻는다.

## Commands

```bash
uv run pytest -q                           # unit/regression (network 0)
python3 scripts/wiki_lint.py --strict      # wiki link/index 검증
python3 scripts/gen_index.py               # wiki index 재생성
```

- pilot: `preview_start(name="pilot-opm")` → `http://127.0.0.1:8000/mcp`
- live: `https://open-proxy-mcp.fly.dev/mcp`
- 코드 시점 차이: `python3 scripts/live_pilot_diff.py`

## Structure

```
open_proxy_mcp/
  server.py            # MCPServer 진입점 (build_app())
  tools/               # public MCP tool facades
  services/            # 도메인 분석 로직
  dart/client.py       # DART API + KIND + throttle + cache
  data/                # asset_managers/ (정책·매트릭스) · ksic/ (산업분류)
scripts/               # wiki lint · 카탈로그 검사 · 법령 검증 · 시장 배치(cron) · 검증 하네스
wiki/                  # 도메인 지식 (wiki/wiki_schema.md 가 계약서)
```

## Rules

1. **DART API 910/min hard cap.** 초과 시 키가 2~3시간 차단. 키별 인스턴스가 각자 스로틀 — 프로세스 2개면 합산 1,820. batch 최대 30사 + sleep.
2. **document.xml 우선.** PDF/OCR 없음(open-proxy-ai 이관). viewer HTML fallback은 service가 명시적으로 둔 것만.
3. **API 키 비노출.** URL·query·예외·로그·fixture에 전체는 물론 prefix도 남기지 않는다.
4. **raw/ 절대 수정 금지.** 외부 원본 무결성 보존.
5. **이름 기반 접근.** SQL INSERT는 컬럼명 명시. 튜플 위치 언패킹·암묵적 정렬 대신 dict/key=.
6. **공유 파생지표 재사용.** 시총·주식수 등은 검증된 service 재사용. tool별 독자 재계산 금지.
7. **웹 스크래핑 1~2초 랜덤.** DART 웹·KIND 시계 공유. 배치·병렬 금지.
8. **공시 검색은 pblntf_ty 필터 먼저.** 전체 순회 금지. corp_code 없는 시장검색 3개월 한도.
9. **rcept_no.** 00=소집공고(DART 정기), 80=주총결과(거래소 수시).
10. **사용자 조회 결과 저장 안 함.** corp-code/document cache, 시장 snapshot, usage telemetry는 예외.
11. **파이프라인 전체 재실행 금지.** 누락분만 처리.
12. **DB 스키마 변경 전** 백업 파일을 열어보고 배포를 먼저 한다.
13. **컬럼·값 치환 후** 양쪽으로 센다 (새 값 N건 / 옛 값 0건 확인).
14. **메모리 변경은 사용자 승인 필수.** 추가·수정·삭제 전에 보여주고 허락받는다.
15. **이 레포는 PUBLIC.** private 자산(usage·lessons·Supabase 스키마)은 open-proxy-storage에.
16. **회귀 캐시는 DART 응답 경계에서만.** `get_document_cached` 결과를 입력으로 쓴다. 중간 함수 결과 금지 — 함수가 아니라 입력이 기준.

## Workflow

- **검증은 MCP 호출 → 직접 import는 테스트·디버깅만.** tool wrapper·렌더러·인자 기본값·직렬화를 건너뛰는 경로는 사용자가 보는 것과 다른 것을 본다.
- **정확성 > 속도.** 가설 → 엣지케이스 상상 → 표본 테스트 → 통계 검증 → 실행. 확인 전에 서사를 만들지 않는다.
- **작업이 아니라 목표를 본다.** 시킨 일만 수행하지 말고 목표·원칙·전체 프로젝트 연관성을 함께 고려.
- **wiki-first.** 도메인 지식은 `wiki/` 참조. `wiki/wiki_schema.md` → `wiki/index.md` → 필요한 페이지만.
- **streamable-http만.** stdio·SSE 없음. 로컬 검증은 pilot, 배포 후 확인은 live.
- **커밋/푸시/배포는 사용자 명시 요청 시만.**

## Out of Scope

- `wiki/raw/` 수정
- PDF 다운로드·OCR (open-proxy-ai 영역)
- 사용자 데이터 저장
- 작업용 일회성 스크립트를 지시 변경 없이 재사용
