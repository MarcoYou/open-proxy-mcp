# OPM (OpenProxy MCP)

DART 공시를 MCP로 제공하는 Python 서버. 한국 상장사 거버넌스 분석.

## Purpose

에이전틱 파싱 — 원문 + 힌트를 함께 줘서 읽는 AI가 판단하게 만드는 서버.
값을 못 뽑으면 0/미상이 아니라 원문 위치 + 넓힐 손잡이 + 대안 후보를 준다.

## Commands

```bash
uv run pytest -q                           # unit/regression (network 0)
python3 scripts/wiki_lint.py --strict      # wiki 검증
```

## Structure

```
open_proxy_mcp/
  server.py       # MCPServer 진입점
  tools/          # MCP tool facades
  services/       # 도메인 로직
  dart/client.py  # DART API + throttle
```

## Rules

1. **DART API 910/min hard cap.** batch 최대 30사 + sleep.
2. **document.xml 우선.** PDF/OCR 없음.
3. **API 키 비노출.** URL·로그·fixture에 전체·prefix 모두.
4. **raw/ 수정 금지.**
5. **이름 기반 접근.** SQL INSERT 컬럼명 명시, dict/key=.
6. **공유 파생지표 재사용.** tool별 독자 재계산 금지.
7. **웹 스크래핑 1~2초 랜덤.** 배치·병렬 금지.
8. **공시 검색은 pblntf_ty 필터 먼저.** 전체 순회 금지.
9. **사용자 조회 결과 저장 안 함.**
10. **이 레포는 PUBLIC.** private 자산은 open-proxy-storage에.

## Workflow

- 검증은 MCP 호출 우선, 직접 import는 테스트·디버깅만.
- 정확성 > 속도. 가설 → 엣지케이스 → 표본 → 통계검증 → 실행.
- wiki-first: 도메인 지식은 `wiki/` 참조.
