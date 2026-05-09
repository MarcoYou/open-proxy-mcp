---
type: lesson
title: 법령 layer 정밀화 — 280 회사 광범위 검증 + Ralph 4 발견
date: 2026-05-08
related:
  - wiki/ralph/260508_0500_ralph_law-layer-precision.md
  - wiki/lessons/law-layer-260508.md
  - wiki/rules/laws/law_layer_rules.json
  - wiki/decisions/260508_0200_decision_law-layer.md
related_audits: [260508_parser_audit]
related_decisions: [260508_0700_decision_law-layer-precision]
---

# 법령 layer 정밀화 — Ralph 4 발견

## 배경

Ralph 3 (2026-05-08 01:30)에서 36 catalog + `_law_layer` 도입 후 90 회사 sample 검증. promise 발행 후 코붕이 review에서 **B1-4 false positive 가능성** + **KT&G 2025 historical 사례 미발견** 발견. Ralph 4 6 iter 정밀화.

## 발견

### 1. B1-4 false positive — 의미 mismatched

**문제**: B1-4 패턴 `all_of=["임기"], any_of=["1년", "단축", "축소"]`이 정관변경 의도였으나 director_election 안건 ("후보: 진호정, 임기1년" / "사외이사 김정호 임기 1년")도 매치. reason은 정관변경 가정이라 의미 불일치.

**fix (iter 1)**:
- B1-4: `parent_must_contain: ["정관"]` 추가 → 정관변경 sub-agenda 한정
- B1-4b 신규: `parent_excludes: ["정관"]` + 후보 임기 reason ("이사/감사위원 후보 임기 1년 — 통상 3년보다 짧음. case-by-case")
- `_agenda_pattern_match()` 함수에 `parent_must_contain` / `parent_excludes` 지원 추가

**검증**: 90 회사 audit 기존 B1-4 hits 2건 모두 B1-4b로 정확 분기. 분쟁 회사 sample에서 B1-4b 8건 폭발 (영풍 6 + 현대엘리베이터 + 효성티앤씨) — **분쟁 시그널 매우 효과적**.

### 2. KT&G 2025 historical 사전 우회

**발견**: KT&G 2025 정기주총 (2025-03-28, 1차 상법 개정 공포 전) 정관변경 본문에:
- 제26조 신설: "집중투표의 방법에 의하여 이사를 선임하는 경우 **대표이사 사장과 그 외의 이사를 별개의 조로 구분한다**"
- 제25조 변경: 이사 정원 + 사외이사 과반

→ 2026-09-10 시행 집중투표 의무화 **사전 우회 정관**.

**문제**: 기존 B1-8 패턴은 본문 키워드 ("별개의 조") 매칭이지만 안건 title은 일반 표현 ("대표이사 사장 선임 방법 명확화" / "이사의 인원수 명확화") → catch 실패.

**fix (iter 2)**: B1-8b 신규
- `parent_must_contain: ["정관"]` + `all_of: ["이사"]` + `any_of: ["선임 방법", "인원수", "정원" ...]`
- `applies_after: 2024-01-01` (1차 공포 전 사전 우회 catch)
- 자산 2조+ 한정

**검증**: KT&G 2025 안건 12개 중 정확 2건 hit. 분쟁 회사 sample에서 하이브 1건 추가 catch.

### 3. B1-7 ("이사 정수 축소") 패턴 협소

**문제**: 하이브 2026 정기주총 안건 "이사회 정원 상한 축소 및 독립이사 최소 인원 상향" — B1-7 매치 실패. 패턴이 `all_of=["이사", "정수"]`라 "정원" 키워드 누락.

**fix (iter 6)**:
- `all_of=["이사"]` + `any_of=["정수", "정원"]` + `secondary=["축소", "감축", "감소", "상한", "줄"]`
- `parent_must_contain=["정관"]` 추가

**검증**: 하이브 정확 catch. 209 unique hits 회귀 → false positive 0.

## 광범위 검증 결과

**누적 280 회사 audit (KOSPI 200 + KOSDAQ 100 + 분쟁 20)**:
- 266 unique 회사 (분쟁 회사 일부 KOSPI200 중복 제거)
- 2792 안건 / 213 hits (7.6%)
- 38 룰 중 11개 사용

| Layer | 룰 | hits | 비고 |
|---|---|---|---|
| A1 (FOR 정합) | A1-1, A1-2, A1-4, A1-5, A1-6, A1-7 | 191 | 1차 개정 정합 (사외이사→독립이사 명칭, 전자주총 등) |
| A2 (AGAINST 위반) | — | 0 | 시행 전 (2026-07-23 / 09-10 후 catch 예상) |
| B1 (REVIEW 강한 의심) | B1-4, B1-4b, B1-7, B1-8b, B1-10 | 20 | 우회 시나리오 |
| B2 (REVIEW 약한 의심) | B2-8 | 2 | 자발 강화 |
| C (signal) | — | 0 | agenda 매칭 비대상 (별도 메커니즘) |

### Hit 비율 segmentation
- 자산 2조+ KOSPI: ~10% (의무 적용 대상)
- KOSDAQ 자산 2조 미만: 1.8% (의무 X, 자발 정합)
- 분쟁 회사: 11.6% (정관 우회 + 후보 임기 단축 활발)

### 미사용 27 룰 분류
- **시행 전 정상 (5)**: A2-1~A2-5 — 2026-07-23 / 09-10 시행 후 자연 catch
- **C signal layer (4)**: C-1~C-4 — agenda 비대상 (ownership 신호)
- **자산 2조+ 한정 (5)**: A1-3, A1-8, B1-6, B1-8, B1-9 — sample 한정에서 catch
- **광범위 sample 부족 (13)**: B1-1~B1-3, B1-5, B2-1~B2-7, B2-9 — specific 패턴 (시차임기/보수 우회 등)

## 핵심 교훈

### 1. agenda title vs aoi_change 본문 매칭의 한계

KT&G 2025 케이스 — 안건 title은 일반 표현이지만 본문에 우회 키워드. `_law_layer`는 title만 보므로 한계. **본문 매칭은 별도 메커니즘 필요** (backlog: aoi_change body 키워드 검사 + _law_layer 호출자에 본문 전달).

### 2. parent_title 매칭의 효과

`parent_must_contain` / `parent_excludes`는 정관변경 sub-agenda vs director_election의 구조적 분리에 매우 효과적. 단순 keyword 매칭의 false positive 큰 부분 해결.

### 3. applies_after 시점 설정의 중요성

B1-8b applies_after를 1차 공포일 (2025-07-22)로 잡으면 KT&G 2025-03-28 사전 우회를 못 잡음. **사전 우회 시나리오는 시점을 더 이른 날짜로 (2024-01-01)** — 법 개정 논의 시작 시점.

### 4. 분쟁 회사 sample은 룰 검증에 핵심

분쟁 회사 hits 11.6% (KOSPI 9.8% / KOSDAQ 1.8% 대비 높음). B1-x 우회 시나리오 패턴 catch에 분쟁 회사 sample 필수. 향후 행동주의 신규 사례 발생 시 룰 검증 우선.

### 5. 회귀 0% — 룰 변경 안전

iter 1+6 cumulative reclassification: 209 unique hits 중 변경 3건 (모두 더 specific reason으로 향상) / 유지 206건. parent 매칭 추가는 회귀 risk 낮음.

## 룰 변경 summary

| iter | 변경 | 효과 |
|---|---|---|
| 1 | B1-4 + B1-4b 분기 (parent_must_contain/excludes 신규) | director_election 후보 임기 1년 catch |
| 2 | B1-8b 신규 | KT&G 사전 우회 catch (안건 title 일반 표현) |
| 6 | B1-7 패턴 보강 ("정원" + "상한") | 하이브 이사회 정원 상한 축소 catch |

**총 룰**: 36 → 38 (B1-4b + B1-8b 신규)

## 향후 backlog

1. **aoi_change 본문 매칭** — 별도 ralph (구조 변경 — `_law_layer` 호출자 본문 전달 필요)
2. **A2 시행 후 catch** — 2026-07-23 / 09-10 후 자연 catch 검증
3. **광범위 sample 부족 룰 (B1-1~B1-3, B1-5, B2-*)** — 추가 분쟁 회사 / specific case 발생 시 검증
4. **C layer signal** — ownership_structure 등 별도 메커니즘 통합

## archive

- `wiki/architecture/audits/data/260508_law_layer/iter08_kospi_130-200.json`
- `wiki/architecture/audits/data/260508_law_layer/iter08_kosdaq_0-100.json`
- `wiki/architecture/audits/data/260508_law_layer/iter08_dispute_companies.json`
- `wiki/architecture/audits/data/260508_law_layer/dispute_universe.csv`
