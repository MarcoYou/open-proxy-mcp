---
type: audit
title: proxy_contest baseline 200×3 — 패턴 fix 불필요 결정
date: 2026-05-03
related_tools: [proxy_contest]
related_audits: [260503_2304_audit_recap_pattern]
result: baseline 100% 일치 + timeout 0 — multi-upstream-pattern fix 불필요
---

# proxy_contest baseline audit (data-driven decision)

`proxy_contest`는 8 endpoint gather (4 외부 + 4 `_control_context` 내부)이지만 advise/recap의 `build_*_payload`와 달리 **DART API endpoint 직접 호출**로 가벼움. 패턴 fix 효과가 의미 있는지 baseline 측정.

## 200×3 baseline batch 결과 (fix 없이)

- 597 호출 / **3.7분** (advise 15.7분, recap 18.75분 대비 4-5배 빠름)
- complete 195/197 회사
- **일치율 195/195 = 100.0%** ✅
- Status: exact 555 / no_filing 36 / error 6
- **timeout 0** ✅
- Elapsed: mean **1.09s** / p50 1.00s / p95 1.90s / max 7.70s

## 결정 — 패턴 fix 불필요

근거:
- p95 1.9s → race window 거의 없음 (advise/recap는 p95 22-25s)
- timeout 0 + 일치 100% → 현재 구조로도 deterministic
- _safe wrapper / cache 추가 시 코드 복잡도만 증가, 효과 미미

**예외 시나리오**:
- 1000+ 회사 batch 또는 더 긴 lookback 시 race 가능성 재평가
- DART API rate limit margin 줄어들면 재검토

## advise / recap / proxy 비교

| 지표 | advise (Phase 4 fix) | recap (fix) | proxy_contest (no fix) |
|---|---|---|---|
| upstream | 6 build_* | 8 build_* | 8 endpoint 직접 |
| Batch 시간 | 15.7분 | 18.75분 | **3.7분** |
| Mean elapsed | 4.7s | 5.6s | **1.09s** |
| p95 | 21.9s | 24.5s | **1.90s** |
| 일치율 | 100% | 100% | **100%** |

→ **패턴 적용 기준**: upstream이 다른 service의 `build_*_payload` (재귀 호출 多) 인지, vs DART endpoint 직접 호출 인지로 판단. 후자는 가벼워 fix 효과 작음.

## 다음 (TO_DO 갱신)

- ownership_structure (3 upstream) — proxy_contest와 비슷한 패턴? baseline 측정 후 결정
- 정정공고 4건 (`items[0]`) — race와 별개 문제 (parsing fail), 패턴과 독립적으로 fix 필요
