---
type: audit
title: 26 진정 카카오게임즈 패턴 회사 매핑 가능성 정량화 — iter 1
date: 2026-05-10
related:
  - wiki/ralph/260510_0950_ralph_subagenda-amendment-mapping.md
related_ralph: [260510_0950_ralph_subagenda-amendment-mapping]
---

# Ralph 8 iter 1 — 매핑 가능성 정량화

## 방법

26 진정 카카오게임즈 패턴 회사 + 카카오게임즈 = 27 회사 sample.
각 회사 정관변경 top + 모든 sub-agenda + amendments 받아 매핑 score 측정.

매칭 logic (초기):
- sub title에서 조항 번호 추출 (제N조 / 제N조의M)
- amendment label/reason에서 같은 조항 번호 추출
- clause 매칭 score 10 / keyword 매칭 score 1
- best score 분류:
  - clear: score ≥ 10 (clause 매칭)
  - partial: 1 ≤ score < 10 (keyword 매칭)
  - none: score 0 (매칭 X)

## 결과

| 분류 | sub 수 | 비율 |
|---|---:|---:|
| clear (clause 매칭) | 15 | 14.7% |
| partial (keyword 매칭) | 62 | 60.8% |
| none (매칭 X — generic) | 25 | 24.5% |
| **총합** | 102 | 100% |

→ **75.5% sub-agenda 매핑 가능** (clear + partial).

## 핵심 발견

### 발견 1 — amendments label 빈 string 케이스 (logic 보강 필요)

한미사이언스 예시:
- sub: "제22조 (소집지)" / "제28조 (의결권의 대리행사)" / "제31조 (이사 및 감사의 수)"
- amendments label: 모두 **빈 string ""**
- → clause overlap 0 (매칭 logic은 label에서만 조항 추출)
- → partial 분류 (reason에서 keyword 매칭만)

**Fix**: amendment의 before/after raw에서도 조항 번호 추출. 한미사이언스 같은 경우 raw 본문에 "제22조 (소집지)" 같은 조항 명시 가능.

### 발견 2 — 카카오게임즈 매핑 검증

- "주주총회 기준일 변경의 건" → 제13조의3 (label 매칭) ✓
- "개정 상법 반영의 건" → 제20조 (reason "반영" keyword 매칭) — single match

단 cross-match 위험: 두 amendment의 reason에 "반영" 키워드 동시 존재 가능. 매핑된 amendment 마킹 필수.

### 발견 3 — 강원랜드 매핑 한계

- sub: "관계 법령 및 행정기관·행정체계 개정사항 현행화를 위한 변경"
- amendment label: **"관계 법령 및 행정기관·행정체계 개정사항 현행화를 위한 변경"** (동일 string!)
- 단 keyword 추출 logic은 단어 단위 — score 1만

**Fix**: amendment label 자체가 sub title과 동일하거나 substring이면 자동 매핑 (highest priority).

### 발견 4 — generic title 24.5% — 별도 정책 필요

매핑 X sub: "그 외 정관 변경의 건" / "기타 변경의 건" / "조문 정비" 등.
- 옵션 A: 매핑 안 된 amendment + generic sub 통합 검사 (1번 first hit)
- 옵션 B: skip (운용사 정책 fallback)
- 옵션 C: raw 노출 + LLM 위임

## 회사별 매핑 분포

| 회사 | subs | amends | clear | partial | none |
|---|---:|---:|---:|---:|---:|
| 유한양행 | 8 | 2 | 2 | 2 | 4 |
| 한미사이언스 | 7 | 7 | 0 | **7** | 0 |
| 차바이오텍 | 7 | 4 | **4** | 0 | 3 |
| 쏠리드 | 7 | 7 | 0 | **7** | 0 |
| ISC | 7 | 1 | 1 | 4 | 2 |
| 씨에스윈드 | 5 | 5 | 0 | 4 | 1 |
| 파인엠텍 | 5 | 1 | 1 | 0 | 4 |
| 하나마이크론 | 4 | 4 | **3** | 1 | 0 |
| 원익홀딩스 | 4 | 4 | 0 | 4 | 0 |
| 한라캐스트 | 4 | 1 | 0 | 1 | 3 |
| 나노신소재 | 4 | 1 | 1 | 2 | 1 |
| 동진쎄미켐 | 3 | 4 | 0 | 3 | 0 |
| 솔브레인홀딩스 | 3 | 3 | 0 | 2 | 1 |
| 파이버프로 | 3 | 3 | **3** | 0 | 0 |
| 에코프로에이치엔 | 3 | 4 | 0 | 2 | 1 |
| 시노펙스 | 3 | 3 | 0 | 2 | 1 |
| 인바디 | 3 | 1 | 0 | 3 | 0 |
| 이수페타시스 | 3 | 3 | 0 | 3 | 0 |
| 성호전자 | 3 | 1 | 0 | 2 | 1 |
| 강원랜드 | 2 | 1 | 0 | 1 | 1 |
| 솔브레인 | 2 | 2 | 0 | 1 | 1 |
| 인텔리안테크 | 2 | 2 | 0 | 2 | 0 |
| 엔켐 | 2 | 1 | 0 | 2 | 0 |
| 넥슨게임즈 | 2 | 2 | 0 | 2 | 0 |
| 아난티 | 2 | 1 | 0 | 1 | 1 |
| 대덕전자 | 2 | 2 | 0 | 2 | 0 |
| 카카오게임즈 | 2 | 2 | 0 | 2 | 0 |
| **TOTAL** | **102** | | **15** | **62** | **25** |

## 다음 단계 (iter 2)

- 매핑 logic 정밀화:
  1. amendment label == sub title 자동 매핑 (강원랜드 case)
  2. amendment before/after raw에서 조항 번호 추출 (한미사이언스 case)
  3. clause 매칭 우선 → keyword 매칭 차순위
  4. 매핑된 amendment 마킹 → 다른 sub skip (cross-match 회피)
- generic title 정책 결정 (옵션 A/B/C)

## archive

- `wiki/architecture/audits/data/260510_subagenda_mapping/iter1_26_companies.json`
