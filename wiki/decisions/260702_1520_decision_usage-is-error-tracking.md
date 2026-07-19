---
type: decision
title: 사용 통계에 툴 내부 오류(is_error) 기록 — 오류 기준 정의
date: 2026-07-02 15:20
status: adopted (3c981e9, v648 배포)
related:
  - open_proxy_mcp/usage.py
  - open_proxy_mcp/server.py
  - scripts/usage_tracker.py
---

# 툴 내부 오류(is_error) 기록

## 배경

툴 실행 실패는 MCP 규약상 **HTTP 200에 실려** 반환된다(`isError: true`).
기존 events는 HTTP status만 기록 → 툴 호출 3,600여 건이 전부 200/202로 보여
"툴이 조용히 실패하는 비율"을 알 수 없었다. 간접 신호(`notifications/cancelled` 50여 건)만 존재.

## 구현

- `server.py` 미들웨어: **tools/call 응답 본문**(SSE/JSON)을 청크 단위 스캔.
  패턴 `"isError":true`(± 공백) 또는 `"error":{"code"` 발견 시 오류로 표시.
  청크 경계 대응: 직전 꼬리 24바이트를 이어 붙여 스캔(메모리 상수). 기록은 본문 종료 시점.
  핸드셰이크류(initialize 등)는 기존대로 응답 시작 시 기록.
- `usage.py`: `events.is_error boolean` (sqlite/pg 기동 시 자동 마이그레이션).
- `usage_tracker.py --stats`: 툴별 `오류(측정분)` 열. 배포 이전 행은 NULL → 분모에서 제외.

## 오류 기준 (무엇을 세고, 무엇을 안 세나)

**is_error=True 로 세는 것** — "툴이 일을 끝까지 못 해낸 경우":
1. 툴 실행 실패(`isError: true`): DART 무응답/한도/타임아웃, 코드 예외, 파라미터 검증 실패
2. 프로토콜 오류(JSON-RPC error): 없는 툴 호출, 깨진 요청

**세지 않는 것** (의도적 제외):
- 빈 결과·`not_found` — "없다"도 정답. 툴은 일을 완수함
- 느린 응답 — 지연은 `latency_ms`가 담당
- 사용자 취소 — `notifications/cancelled`로 별도 관찰
- 접속 거부(4xx) — `status` 컬럼이 담당

## 3지표 분업

```
status(HTTP)  →  서버에 접속은 됐나?      (키/경로 문제)
is_error      →  툴이 일을 해냈나?        (툴/DART 문제)
latency_ms    →  얼마나 걸렸나?           (성능 문제)
```

사용자 문의 시: status 200 + is_error=True → 우리 쪽 문제 / status 4xx → 접속 설정 문제 /
둘 다 정상인데 불만 → 지연 문제로 즉시 분류 가능.

## 한계

- "회사명 오타 → 못 찾음" 류는 성공으로 집계(툴 관점 정상). 필요 시 `not_found` 비율 별도 측정.
- 응답 본문 텍스트에 우연히 패턴 문자열이 포함되면 오탐 가능(따옴표·콜론 포함 정확 매칭이라 실제 확률 극히 낮음).

## 후속 (260712): error_kind 분류 + 중앙 degrade

is_error는 "실패했나"만 알려줄 뿐 "내 코드 탓이냐 DART 탓이냐"를 구분 못 했다. 260712 오류율 스파이크
조사에서 이 한계가 드러나 두 가지를 얹었다:

- **`events.error_kind` 컬럼** — 예외 타입이 살아있는 tool 래퍼 지점에서 crash/timeout/upstream을
  분류해 `[ekind=]` 태그로 기록. `--stats [오류종류]`에서 "코드버그 비중"을 바로 본다.
- **중앙 degrade** — DART 외부·부하 예외를 `tools_v2/_wrap_tool_errors` 한 곳에서 잡아 graceful
  응답(is_error=false)으로. 원인별 안내(과호출→나눠서 / timeout→기다려)까지 실어 사용자 행동을 유도.

상세·재현·trade-off: error-safety-degrade-260712.
