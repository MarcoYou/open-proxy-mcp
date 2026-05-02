---
type: audit
title: proxy_advise rename + 9 scope 추가 — regression 0 검증
date: 2026-05-04
related_tools: [proxy_advise_before_meeting, proxy_result_after_meeting]
related_audits: [260503_1847_audit_phase4_final, 260503_2304_audit_recap_pattern]
result: regression 0 (P4 baseline 197/197 동일) + 일관성 100%
---

# proxy_advise rename + scope 확장 regression audit

새 통합 action tool `proxy_advise_before_meeting` (옛 `advise_vote_before_meeting` rename + 9 scope 추가) 후 Phase 4 baseline과 동일 결과 보장 검증.

## 작업 요약 (Step 1-4 commits)

| Step | 내용 | commit |
|---|---|---|
| 1 | rename (services/tools_v2 4 file) + 옛 wiki archive | `7b06b75` |
| 3 | scope param + 단순 expose 5 (agenda/candidates/financial/governance/ownership) | `6711228` |
| 4a | policy_basis (모범사례 + 특이케이스 example, 재설계) | `c937505` |
| 4b/c/d | proxy_battle / engagement / evidence | `543293e` |
| 4e | proxy_result.brief (vote_brief 흡수) | `4a75b87` |

총 6 commit. 5 파일 수정 + 1 신규 (`services/policy_comparison.py`).

## 200×3 batch 결과 (Step 2)

- 597 호출 / 16분 (Phase 4 15.7분과 동일 페이스)
- complete 195/197 회사
- Status: **exact 492 / error 6 / no_filing 99 — Phase 4와 완전 동일** ✅
- 일관성 195/195 = **100.0%** ✅

## Cross-match (Phase 4 baseline vs NEW)

| 비교 | 결과 |
|---|---|
| 197/197 회사 run1 (status, F, A, R) | **197/197 match, diff 0** ✅ |

→ rename + scope 추가 후 default `decisions` scope의 logic 완전 그대로.

## Phase 2 → 3 → 4 → NEW 비교

| 지표 | Phase 2 | Phase 3 | Phase 4 | **NEW (proxy_advise)** |
|---|---|---|---|---|
| 일치율 | 91.4% | 91.9% | 100.0% | **100.0%** |
| Status exact | 468 | 477 | 492 | **492** |
| Status error | 24 | 6 | 6 | **6** |
| Status timeout | 3 | 15 | 0 | **0** |
| Batch 시간 | 37분 | 36분 | 15.7분 | **16분** |

## 신규 scope 작동 확인 (spot)

| scope | 검증 결과 (삼성전자) |
|---|---|
| decisions (default) | 10 ag 7F 0A 3R (Phase 4와 동일) |
| financial | financial_full key 추가 |
| all | 5개 _full key 모두 |
| policy_basis | 8 운용사 검색 / 6 데이터 / 2 카테고리 example, consensus FOR (6/8) + outliers ABSTAIN (계열사 회피) |
| proxy_battle | proxy_solicitation/litigation/block_signals 통합 |
| engagement | value_up plan items_count=1 |
| evidence | 10 entries 모두 raw_sources 포함 |
| proxy_result.brief | ownership/agenda/candidates/result_summary 통합 |

## Promise 평가 (검증 ralph 3 gate 사전 체크)

| Gate | 현재 상태 |
|---|---|
| G1 일관성 100% | ✅ 충족 (195/195 + Phase 4 동일) |
| G2 정확도 ≥95% | ⏳ ralph loop에서 검증 (7 운용사 majority baseline) |
| G3 사실 정확성 100% | ⏳ ralph loop에서 검증 (evidence fact-check) |

→ G1 통과. G2/G3는 [[260503_0002_ralph_proxy-advise-verification]] ralph 실행 시 검증.

## 결론

rename + 9 scope 추가 + 옛 3 tool (vote_brief/engagement_case/campaign_brief) 흡수 작업 **regression 0** + **일관성 100%** 유지. Step 4 모든 신규 scope 정상 작동.

다음: Ralph loop 실행 (사용자 confirm 후) — G2/G3 검증.
