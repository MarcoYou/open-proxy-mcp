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

1. **DART API 키당 910/min.** 초과 시 그 키가 2~3시간 차단. batch 최대 30사 + sleep.
2. **document.xml 우선.** PDF/OCR 없음(open-proxy-ai 이관). viewer HTML fallback은 service가 명시적으로 둔 것만.
3. **API 키 비노출.** URL·query·예외·로그·fixture에 전체는 물론 prefix도 남기지 않는다.
4. **raw/ 절대 수정 금지.** 외부 원본 무결성 보존.
5. **이름 기반 접근.** SQL INSERT는 컬럼명 명시. 튜플 위치 언패킹·암묵적 정렬 대신 dict/key=.
6. **공유 파생지표 재사용.** 시총·주식수 등은 검증된 service 재사용. tool별 독자 재계산 금지.
7. **웹 스크래핑은 프로세스 시계 하나.** 0.4~1초 랜덤 + 분당 40건. 차단은 IP 기준이라 그 머신의 전원이 막힌다(`/health` `web_block`).
8. **공시 검색은 pblntf_ty 필터 먼저.** 전체 순회 금지. corp_code 없는 시장검색 3개월 한도.
9. **rcept_no.** 00=소집공고(DART 정기), 80=주총결과(거래소 수시).
10. **사용자 조회 결과 저장 안 함.** 캐시·시장 snapshot·usage telemetry 만 예외.
11. **파이프라인 전체 재실행 금지.** 누락분만 처리.
12. **DB 스키마·값 변경**은 백업 확인 → 배포 → 양쪽 세기(새 값 N건 / 옛 값 0건).
13. **메모리 변경은 사용자 승인 필수.** 메모리는 「일하는 방식」만 — 지식·일화는 storage `wiki-private/anecdotes/`.
14. **이 레포는 PUBLIC.** private 자산(usage·anecdotes·Supabase 스키마·비공개 기능)은 open-proxy-storage·opm-ext 에. 공개 레포엔 확장 훅만.
15. **회귀 캐시는 DART 응답 경계에서만.** `get_document_cached` 결과를 입력으로 쓴다.

## Workflow

- **검증은 MCP 호출.** 직접 import 는 테스트·디버깅만 — wrapper·렌더러를 건너뛰면 사용자가 보는 것과 다르다.
- **정확성 > 속도.** 가설 → 표본 → 통계 검증 → 실행. 확인 전에 서사를 만들지 않는다.
- **작업이 아니라 목표를 본다.**
- **wiki-first.** 도메인 지식은 `wiki/` 참조. `wiki/wiki_schema.md` → `wiki/wiki_index.md` → 필요한 페이지만.
- **무엇을 바꾸면 어디를 고치나** (tool 추가·필드·파라미터·사실·페이지 이동별 고칠 파일 + 돌릴 검사) → `wiki/wiki_schema.md` 「문서 운영 규칙」 표.
- **streamable-http만.** 로컬 검증은 pilot, 배포 후 확인은 live. 배포는 main 푸시(CI)로만 — 수동 fly deploy 는 CI 와 겹치면 실패.
- **커밋/푸시는 사용자 명시 요청 시만.**

## Out of Scope

- `wiki/raw/` 수정
- PDF 다운로드·OCR (open-proxy-ai 영역)
- 사용자 데이터 저장
- 일회성 스크립트의 재사용
