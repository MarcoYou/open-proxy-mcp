---
type: audit
title: 510 회사 spot 회귀 + sub fallback 75건 신규 catch — iter 4
date: 2026-05-10
related:
  - wiki/ralph/260510_0950_ralph_subagenda-amendment-mapping.md
related_ralph: [260510_0950_ralph_subagenda-amendment-mapping]
related_lessons: [subagenda-mapping-260510]
related_decisions: [260510_1015_decision_subagenda-mapping]
---

# Ralph 8 iter 4 — 510 회사 spot 결과

## 통합

| universe | n | 기존 hits | 신규 hits | 회귀 | sub 신규 | sub 회사 |
|---|---:|---:|---:|---:|---:|---:|
| KOSPI200 | 199 | 287 | 344 | **0** | 65 | 46 |
| KOSDAQ150 | 150 | 40 | 48 | **0** | 8 | 7 |
| KOSDAQ151-300 | 150 | 30 | 30 | **0** | 0 | 0 |
| DISPUTE | 10 | 18 | 20 | **0** | 2 | 2 |
| **TOTAL** | **509** | **375** | **442** | **0** | **75** | **55** |

회귀 검증 = (회사명, rule_id) set diff. 기존 set ⊆ 신규 set (510/510).

## 신규 sub catch — 75건 / 55 회사 (10.8%)

### sub fallback rule 분포 (Ralph 8 신규)

| rule | catch | 비고 |
|---|---:|---|
| A1-3 (감사위원 분리선출 의무 2명) | 18 | Ralph 7 미사용 룰 lesson 첫 활성 |
| A1-5 (사외→독립이사 명칭) | 15 | Ralph 7 D 패턴과 누적 |
| A1-1 (집중투표 배제 삭제) | 13 | |
| A1-7 (전자주총 도입) | 12 | |
| B2-1 / B2-7 | 4 / 4 | B2 layer 활성 |
| A1-4 / A1-6 | 3 / 3 | |
| B1-8 / B1-8b | 1 / 1 | B1 layer 활성 |
| A1-2 (집중투표 도입) | 1 | 미사용 룰 첫 활성 |

A1-3 / B1-8 / A1-2 — Ralph 7 lesson "미사용 룰" 항목 다수 활성.

### KOSDAQ 패턴 차이

KOSPI200: sub 65 / 46 회사 (23.1%)
KOSDAQ150: sub 8 / 7 회사 (4.7%)
KOSDAQ151-300: sub 0 / 0 회사 (0%)

→ 카카오게임즈 패턴 (sub-agenda + 조항 명시)은 **KOSPI 대형사 多** — KOSPI 자산 2조+ 회사가 sub-agenda hierarchy 명확 표기 + 조항 번호 직접 명시.
→ KOSDAQ 작은 회사는 sub-agenda 부재 (D 패턴) 또는 단순 표기로 매핑 어려움.

## 회귀 검증 디테일

### "body → title 자리 이동" — KOSPI200 2건 (실질 회귀 X)

| 회사 | Ralph 7 위치 | Ralph 8 위치 | 같은 hit? |
|---|---|---|---|
| HD현대 "집중투표제가 배제된 정관 변경의 건" | body | title | ✓ |
| 엘앤에프 "집중투표제 배제 관련 정관 변경의 건" | body | title | ✓ |

원인: 룰 catalog reload 시점 차이 (모듈 cache). 코드 logic 동일. 회사 단위 catch 동일. 사실 title 매칭이 더 명시적이라 정확성 ↑.

### body 전체 분포

KOSPI200 body 26 (Ralph 7 28) — 2건 title 이동.
KOSDAQ150 body 21 (Ralph 7 22) — 1건 sub 이동 또는 title 이동.
KOSDAQ151-300 body 18 (Ralph 7 18) — 동일.
DISPUTE body 2 (Ralph 7 2) — 동일.

→ 차이는 모두 위치 이동, 회사 단위 catch 회귀 0.

## 핵심 결과

✅ **G4 회귀 0** — 510 회사 (회사, rule) 단위 회귀 0
✅ **신규 catch 75건 / 55 회사 (10.8%)** — sub fallback 효과
✅ **A1-3 / B1-8 / A1-2 미사용 룰 활성** — Ralph 7 lesson 항목
✅ **cross-match 회피 작동** — 회사별 used_amendments track

## archive

- `iter4_spot_kospi200.json` (199 records)
- `iter4_spot_kosdaq150.json` (150)
- `iter4_spot_kosdaq300.json` (150)
- `iter4_spot_dispute.json` (10)
