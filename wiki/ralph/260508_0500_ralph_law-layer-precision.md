---
type: ralph
title: 법령 layer 정밀화 — false positive/negative 점검 + 룰 보강
created: 2026-05-08 05:00
completion_promise: LAW_LAYER_PRECISION_VERIFIED
max_iterations: 8
ref:
  - wiki/ralph/260508_0130_ralph_law-layer.md
  - wiki/lessons/law-layer-260508.md
  - wiki/rules/laws/상법-2025-2026-종합.md
  - wiki/rules/laws/law_layer_rules.json
---

## Invoke (복붙)

```
/ralph-loop:ralph-loop wiki/ralph/260508_0500_ralph_law-layer-precision.md 가이드 따라 36 catalog 룰 정밀화 점검. 광범위 sample (KOSPI 200 + KOSDAQ 100 + 분쟁 회사 spot) 으로 false positive 와 false negative 식별 후 룰 fix. 새 패턴 발견 시 catalog 추가. B1-4 reason 정밀화, KT&G 종류별 정원 분리 같은 historical 케이스 검증 모두 충족 시 promise. --completion-promise LAW_LAYER_PRECISION_VERIFIED --max-iterations 8
```

# Ralph: 법령 layer 정밀화 점검

## Context

Ralph 3 (260508_0130_ralph_law-layer)에서 36 catalog + _law_layer 도입. promise 발행 후 광범위 검증 (90 회사) 결과:

**hit 분포 (코붕이 노트)**:
- A1-5 (독립이사 명칭) 32 / A1-1 (집중투표 배제 삭제) 30 / A1-7 (전자주총) 20 / A1-4 (의결권 제한) 14
- B1-10 (분리선출 의무 초과) 9 / B1-4 (임기 단축) 2 / A1-2 (집중투표 도입) 5
- 36 룰 중 7개만 hit, 29개 미발견

### 문제 발견

1. **B1-4 false positive 가능성** — "임기 1년" 패턴이 정관변경 의도였는데 이사 선임 안건도 매치됨
   - 서울보증보험: 기타비상무이사 후보 진호정 임기 1년
   - 현대엘리베이터: 사외이사 후보 김정호 임기 1년
   - reason_template 정관변경 가정이라 의미 mismatched

2. **B1-7~9 미발견** — 이사 정수 축소 / 종류별 정원 분리 / 감사위 정원 확대 0 hit
   - KT&G 2025 사례 (이사 종류별 정원 분리 72% 통과) — historical, 우리 2026 audit엔 안 잡힘
   - 다른 회사들도 0 hit — 진짜로 없는 건지 룰 매칭 미흡인지 확인 필요

3. **A2-x 미발견 (정상)** — 시행 전이라 위반 케이스 없음. 2026-09-10 후 자연스럽게 hit 예상

4. **B2 9개 미발견** — sample 한정 또는 매칭 미흡

### 점검 목적

- false positive 줄이기 (의미 명확)
- false negative 줄이기 (놓친 패턴 catch)
- 새 패턴 발견 시 36 catalog 추가
- KT&G 같은 historical 사례로 룰 검증

## 가정

- No conversation context / no web search / MCP only / deterministic
- v2 production
- 8 iter max
- 광범위 sample (KOSPI 200 + KOSDAQ 100 + 분쟁 회사 spot)

## 성공 기준 (모두 충족 시 promise)

### G1. B1-4 fix
- "임기 1년" 패턴이 이사 선임 안건에서 매치되더라도 reason은 적절히 (정관변경 vs 후보 임기 단축 구분)
- 또는 카테고리 분리 (B1-4a 정관변경 / B1-4b 후보)

### G2. False positive 0% (1% 미만)
- 광범위 sample 1000+ 안건에서 잘못된 hit 1% 미만
- 잘못 hit 발견 시 룰 수정

### G3. False negative 점검 — historical 사례
- KT&G 2025 정기주총 이사 종류별 정원 분리 안건 → B1-8 히트되어야 함 (historical agenda 직접 호출)
- 명백한 우회 사례 모두 catch

### G4. 새 패턴 catalog 추가
- 광범위 sample에서 36 외 새 우회 패턴 발견 시 catalog 추가
- agenda title pattern + applies_to + reason

## 작업 plan (8 iter)

### Phase 1 — B1-4 fix + historical 검증 (iter 1-2)

#### iter 1. B1-4 정밀화
- 현재 패턴: `all_of=["임기"], any_of=["1년", "단축", "축소"]`
- 문제: 이사 선임 안건 "임기 1년"도 매치
- fix 옵션:
  - (A) 카테고리 분리 — articles_amendment에 한정
  - (B) parent_title 검사 — 정관 키워드 있을 때만
  - (C) reason_template을 일반화 — "임기 1년 — 정관변경 또는 후보 임기 제한 의심"
- 회귀 spot

#### iter 2. KT&G 2025 historical agenda 검증
- KT&G 2025 정기주총 안건 직접 호출
- B1-8 (이사 종류별 정원 분리) hit 확인
- 매치 안 되면 패턴 보강

### Phase 2 — 광범위 sample (iter 3-5)

#### iter 3. KOSPI 130-200 spot (약 70 회사)
- 누적 KOSPI 0-200 완료
- 새 패턴 / false positive 식별

#### iter 4. KOSDAQ 0-100 spot
- 자산 2조 미만 회사 — 분리선출/집중투표 등 의무 X 회사
- B2 (자발 강화) 케이스 hit 가능

#### iter 5. 분쟁 회사 spot (한진칼/고려아연/SM/효성중공업/SOOP 등)
- 행동주의 받은 회사들
- B1-x 시나리오 (정관 우회) hit 빈도 ↑ 예상
- 새 패턴 발견 가능성

### Phase 3 — 룰 정밀화 (iter 6)

#### iter 6. 통합 분석 + 룰 fix
- false positive 0% 달성하도록 keyword 정교화
- 새 패턴 catalog 추가
- 회귀 spot (이전 90 회사 + 새 sample)

### Phase 4 — 문서화 + promise (iter 7-8)

- lesson 작성 (정밀화 발견)
- decision 작성 (룰 변경)
- log update
- promise 발행

---

## 총 DART 호출 추정

| Phase | iter | 호출 |
|---|---|---|
| Phase 1 (B1-4 + KT&G historical) | 1-2 | ~50 |
| Phase 2 (광범위 sample) | 3-5 | ~600 (200 회사 × 3) |
| Phase 3 (룰 정밀화 회귀) | 6 | ~150 (50 회사) |
| Phase 4 (문서화) | 7-8 | 0 |
| **총합** | 8 iter | **~800 calls** |

→ 분당 cap 안전.

## 영향 범위

- `wiki/rules/laws/law_layer_rules.json` — 룰 정밀화 + 새 패턴 추가
- `services/proxy_advise.py` — 매칭 logic 보강 (필요 시)
- `wiki/architecture/audits/data/260508_law_layer/` — 광범위 검증 데이터
- `wiki/lessons/` — 정밀화 lesson
- `wiki/decisions/` — 룰 변경 결정

## 비목표

- 36 catalog 외 새로운 큰 영역 (예: 후보 평가 / 자사주 정책) — 별도 ralph
- _law_layer 코드 구조 자체 변경 — 패턴 매칭 정밀화만
- vote_style 정책 변경

## archive 폴더

`wiki/architecture/audits/data/260508_law_layer/` (기존 폴더에 누적)

---

## iteration log

### iter 1 — B1-4 정밀화
(작성 예정)

### iter 2 — KT&G historical 검증
(작성 예정)

### iter 3 — KOSPI 130-200 spot
(작성 예정)

### iter 4 — KOSDAQ 0-100 spot
(작성 예정)

### iter 5 — 분쟁 회사 spot
(작성 예정)

### iter 6 — 룰 정밀화 + 회귀
(작성 예정)

### iter 7-8 — 문서화 + promise
(작성 예정)
