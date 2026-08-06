---
type: decision
title: DartClient에 persistent httpx connection pool 도입
date: 2026-05-07 23:30
status: adopted
related:
  - wiki/log.md
---

# DartClient persistent httpx connection pool

## 배경

매 DART API 호출마다 새 `httpx.AsyncClient()`를 만들면 **TLS handshake 100-300ms가 호출 수만큼
누적**된다. fly(iad) ↔ DART(한국)는 거리가 멀어 이 비용이 특히 크다.

## 결정

`DartClient`에 모듈 레벨 persistent `httpx.AsyncClient` 보유 — connection pool 재사용.

```python
self._http = httpx.AsyncClient(
    timeout=httpx.Timeout(30.0, connect=10.0),
    limits=httpx.Limits(
        max_connections=20,
        max_keepalive_connections=10,
        keepalive_expiry=60.0,
    ),
)
```

### 근거

- **TLS handshake 비용**: fly iad ↔ DART (한국) 거리 멀어 매 호출 ~200-400ms TLS overhead
- **호출당 5-10번 DART 요청**: query 1번에 1-3초 누적 낭비
- **Connection pool 효과**: 첫 호출 200ms + 이후 0ms (재사용)
- **기존 `async with httpx.AsyncClient()` 패턴**: 매번 새 client 생성 → 매번 새 TLS handshake

### Connection leak 위험 분석

| 위험 | 평가 |
|---|---|
| Process crash 시 leak | OS가 자동 정리 — fly machine restart로 완전 해결 |
| 코드 버그로 client 객체 다중 생성 | `DartClient`는 모듈 레벨 `_instances` dict로 1개만 캐시됨 — 검증됨 |
| `aclose()` 호출 누락 | fly machine restart 시 OS 정리. 명시 close는 graceful shutdown 보너스 |
| Connection pool 한계 도달 | `max_connections=20` 한도 명시 — DART API 분당 1000회 한도와 무관 |

→ leak 위험 매우 낮음. fly 환경 특성상 OS-level cleanup 신뢰 가능.

## 영향 범위

- `open_proxy_mcp/dart/client.py`:
  - `DartClient.__init__`: `self._http` 추가
  - 16개 호출 위치 (`async with httpx.AsyncClient() as http:` 블록): `self._http` 사용 + 들여쓰기 dedent
- 외부 인터페이스 변화 없음 — 같은 메서드 시그니처

## 효과 (fly iad → DART 한국)

- TLS handshake 절약: 호출당 200-400ms × N
- query 1번에 5-10 DART 호출 → **누적 1-3초 단축**
- fly machine warm 상태에서 효과 더 큼 (cold start 없음)

## 비목표

- HTTP/2 (DART API는 HTTP/1.1 추정)
- 명시적 `close()` lifecycle hook (fly OS 정리 신뢰)
- Connection metric 노출 (overkill)

## Trade-off

- (+) TLS overhead 거의 제거
- (+) 코드 단순화 (`async with` 16개 사라짐)
- (-) 모듈 레벨 connection pool — 단위 테스트 mock 시 약간 복잡 (`_http` 직접 패치 필요)
- (-) DART 측 connection 길게 유지 (60s keepalive) — 측면 영향 없음
