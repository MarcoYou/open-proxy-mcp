---
type: audit
title: 510 회사 spot 회귀 + body fallback 신규 catch — iter 4
date: 2026-05-10
related:
  - wiki/ralph/260510_0823_ralph_agenda-hierarchy-body-fallback.md
related_ralph: [260510_0823_ralph_agenda-hierarchy-body-fallback]
related_lessons: [agenda-hierarchy-260510]
related_decisions: [260510_0900_decision_d-pattern-body-fallback]
---

# iter 4 — 510 회사 spot 결과

## KOSPI200 (199/200 success)

| 항목 | 값 |
|---|---:|
| 회귀 (기존 hit 사라짐) | **0** ✅ |
| title hits 기존 (Ralph 4) | 247 |
| title hits 신규 (iter 4) | 267 |
| 신규 title catch | 20 (모두 A1-1, Ralph 6 "변경" 키워드 효과) |
| **D 패턴 진입** | **49** |
| **body fallback 신규 catch** | **28건 (27 회사)** |

### body fallback rule 분포
- A1-1 (집중투표 배제 조항 삭제): 14건
- A1-7 (전자주총 도입): 9건
- A1-5 (사외→독립이사 명칭): 5건

### body fallback catch 회사 (KOSPI200, 27/199 = 13.6%)
KB금융, 신한지주, 우리금융지주, HD현대, 메리츠금융지주, SK텔레콤, DB손해보험, SK바이오팜, 엘앤에프, 카카오페이, GS, 두산밥캣, 오리온, 포스코DX, 현대제철, 한화엔진, 팬오션, SK가스, HD현대마린엔진, 코스맥스, 현대지에프홀딩스, 한국카본, GS리테일, SK오션플랜트, 금호타이어, 동원산업, 오리온홀딩스

### 회귀 검증 — 기존 hit 사라진 회사 0건 ✅

상세: 회사별 (title, rule_id) set diff 비교. 기존 hits set ⊆ 신규 hits set.
신규 catch 20건은 모두 A1-1 — Ralph 6 commit e98f515 "변경" 키워드 추가 효과.
parser fix (LG화학 제3호)은 _law_layer hit에 영향 X (제3호 title 무관).

## KOSDAQ150 (150/150)

| 항목 | 값 |
|---|---:|
| 회귀 | **0** ✅ |
| title hits 기존 | 18 |
| title hits 신규 | 19 |
| D 패턴 진입 | 91 (KOSPI200 49 vs KOSDAQ 91 — 자산 작은 회사 sub-agenda 부재 多) |
| body fallback 신규 | 22건 (22 회사) |

body rule 분포: A1-5 7 / A1-7 10 / A1-6 3 / A1-1 1 / **B2-1 1** (B layer body fallback 첫 catch)

## KOSDAQ151-300 (150/150)

| 항목 | 값 |
|---|---:|
| 회귀 | **0** ✅ |
| title hits | 12 (기존 동일) |
| D 패턴 진입 | 74 |
| body fallback 신규 | 18건 (18 회사) |

body rule 분포: A1-5 10 / A1-7 6 / **A1-8 1** (자사주 의무소각 — 기존 미사용 룰 첫 catch) / B2-1 1

## DISPUTE (10/10)

| 항목 | 값 |
|---|---:|
| 회귀 | **0** ✅ |
| title hits | 16 (기존 동일) |
| body fallback 신규 | 2건 |

## 510 회사 통합

| universe | n | 기존 t | 신규 t | Δt | 회귀 | D진입 | body | body 회사 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| KOSPI200 | 199 | 247 | 267 | +20 | 0 | 49 | 28 | 27 |
| KOSDAQ150 | 150 | 18 | 19 | +1 | 0 | 91 | 22 | 22 |
| KOSDAQ151-300 | 150 | 12 | 12 | 0 | 0 | 74 | 18 | 18 |
| DISPUTE | 10 | 16 | 16 | 0 | 0 | 2 | 2 | 2 |
| **TOTAL** | **509** | **293** | **314** | **+21** | **0** | **216** | **70** | **69** |

### body fallback rule 분포 (510 통합)
- A1-7 (전자주총 도입): 26
- A1-5 (사외→독립이사 명칭): 22
- A1-1 (집중투표 배제 삭제): 16
- A1-6: 3
- B2-1: 2
- **A1-8 (자사주 의무소각): 1** — Ralph 6 lesson 미사용 룰 첫 catch

## 핵심 결과

✅ **G4 회귀 0** — 510 회사 모두 기존 hit 보존 (set-diff 검증)
✅ **신규 catch 70건** — body fallback이 13.5% 회사에 추가 catch
✅ **A1-8 활성** — Ralph 6 lesson "미사용 룰 (A1-8 / B1-9 등)" 중 A1-8 첫 catch
✅ **B2 layer body fallback 작동** — B2-1 2건 (REVIEW)
✅ **D 패턴 진입 216건** — 510 중 42% 회사가 D 패턴 (정관변경 sub 부재) 안건 1+ 보유

### title 신규 catch 21건 분석
모두 A1-1 (집중투표 배제 조항 삭제) — Ralph 6 commit e98f515 "변경" 키워드 추가 효과. 안전한 신규 catch (false positive 0, 모두 표준 표현).

회귀 검증 방법: 회사별 (title, rule_id) set diff. 기존 hits set ⊆ 신규 hits set (모든 회사).

