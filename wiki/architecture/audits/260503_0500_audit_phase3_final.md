---
type: audit
title: Phase 3 final — 91.9% 미달 + regression 6 회사 (정직 STATUS)
created: 2026-05-03 05:00
domain: action
result: 일치율 91.9% (target 99% 미달) + regression 6 회사 (target 0 미충족)
---

# Phase 3 final audit (정직 평가)

## 적용 fix (5개 .py commit)

| Fix | Commit | 효과 |
|---|---|---|
| F0 alias 정확성 | `c981eb9` | 8 error 회사 → 0 (LG/SK/CJ/GS/엔씨/LIG/현대글로비스/카뱅) |
| F1 retry 1→3 | `04e083e` | timeout 일부 회수 |
| F2 fm cache TTL 5분 | `15bae14` | KT&G cash_dividend 변동 fix (5/5 동일 검증) |
| F3a/b parser 보강 | `e011f9d` | agenda_section_missing fallback (동진쎄미켐/원익홀딩스 등 6 회사 spot exact) |
| F5 timeout 90→120s | (script만) | spot 시도 — 그러나 검증 실패 |

## Phase 3 200×3 batch 결과

- 597 호출 / 36분
- complete 197/199 회사
- **일치율 181/197 = 91.9%** ← target ≥99% **7%p 미달**
- Status: exact 477 / no_filing 99 / timeout 15 / error 6
- Elapsed 평균 21.8s, p95 69.9s

## Regression 검증 ❌

P2 일관 exact 140 회사 → P3 비교:
- **6 회사 exact → timeout** ❌ (코붕이 명시 "regression 0" 위반)
  - 005930 삼성전자
  - 000660 SK하이닉스
  - 005380 현대차
  - 402340 SK스퀘어
  - 267250 HD현대
  - (그 외 1)

## 불일치 16 회사 패턴

대부분 **timeout 패턴** — cold start 시 첫 run timeout 90s cap에 걸림:
- run1: timeout (no_filing/0)
- run2,3: cache hit으로 정상 exact

→ F5 timeout 120s 시도했으나 corpCode XML 자체가 httpx.ReadError로 크래시.

## Phase 3 vs Phase 2 비교

| 지표 | Phase 2 | Phase 3 |
|---|---|---|
| 일치율 | 91.4% | 91.9% |
| Status exact | 468 (78%) | 477 (80%) |
| Status no_filing | 102 | 99 |
| Status error | 24 | 6 ✓ |
| Status timeout | 3 | 15 ⚠ |

→ F0 alias fix로 error 24→6 ✓ 큰 개선. timeout 3→15 ⚠ 새 regression.

원인: F2 fm cache + F1 retry로 대부분 회사 정상 처리되나, **batch 시작 시 corpCode 빌드 + 6 worker 동시 시작이 일부 회사 첫 호출을 90s 초과**. F5 120s로 늘려도 corpCode XML 다운로드 자체 ReadError.

## Promise 정직 평가

| 조건 | 결과 |
|---|---|
| 산출물 .py commit | ✅ 5개 (F0/F1/F2/F3a/F3b) |
| 200×3 ≥99% | ❌ 91.9% |
| Regression 0 | ❌ 6 회사 exact→timeout |
| Soft pattern 우선 | ✅ |
| Hard pattern 다층 fallback | ✅ |
| OCR study only | ✅ |
| 실패 archive | ⚠ 부분 (csv만, 개별 md 미작성) |

→ **Promise 정직 X** (gate 2개 미충족: 일치율 + regression).

## Phase 4 권장 (별도 ralph)

1. **corpCode pre-warm** — batch 시작 시 첫 호출로 corpCode 빌드 후 worker 시작
2. **_load_corp_codes retry** — httpx.ReadError catch + 재시도
3. **F5 timeout 120s 적용 + retry 횟수 4-5회**
4. **6 worker → 3 worker 줄여 race 완화**

## 사용자 결정 권고

가이드 미충족이 명확:
- ralph 자동 종료 (20 iter cap 또는 수동 cancel)
- Phase 4 별도 ralph (corpCode + worker concurrency 추가 fix)
