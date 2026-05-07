---
type: decision
title: 법령 layer 정밀화 — B1-4 분기 + B1-8b 신규 + B1-7 보강
date: 2026-05-08 07:00
status: adopted
related:
  - wiki/decisions/260508_0200_decision_law-layer.md
  - wiki/lessons/law-layer-precision-260508.md
  - wiki/ralph/260508_0500_ralph_law-layer-precision.md
  - wiki/rules/laws/law_layer_rules.json
---

# 법령 layer 정밀화 결정

## 배경

Ralph 4 (260508_0500_ralph_law-layer-precision) 6 iter 광범위 검증 (KOSPI 200 + KOSDAQ 100 + 분쟁 20 = 280 회사) 결과 룰 정밀화 필요 케이스 식별:
1. B1-4 false positive (정관변경 vs director_election 의미 혼선)
2. KT&G 2025 사전 우회 사례 미발견 (안건 title 일반 표현)
3. B1-7 패턴 협소 (하이브 "정원 상한 축소" 미스)

## 결정

### 1. `_agenda_pattern_match()` 함수 보강

`open_proxy_mcp/services/proxy_advise.py`에 신규 패턴 키 2개 추가:

- `parent_must_contain`: parent_title이 이 키워드 중 하나 포함해야 매치 (정관변경 sub-agenda 한정)
- `parent_excludes`: parent_title이 이 키워드 중 하나 포함하면 미매치 (정관변경 sub-agenda 제외)

### 2. B1-4 분기

| 룰 | 카테고리 | 패턴 | reason |
|---|---|---|---|
| **B1-4** (수정) | articles_amendment | `parent_must_contain=["정관"]` + 임기 단축 키워드 | 정관변경에서 이사 임기 단축 (예: 3년→1년) |
| **B1-4b** (신규) | director_election | `parent_excludes=["정관"]` + 임기 1년 키워드 | 이사/감사위원 후보 임기 1년 — 통상 3년 임기보다 짧음 |

### 3. B1-8b 신규 (KT&G 사전 우회)

| 항목 | 값 |
|---|---|
| 카테고리 | articles_amendment |
| 패턴 | `parent_must_contain=["정관"]` + `all_of=["이사"]` + `any_of=["선임 방법", "인원수", "정원" ...]` |
| 자산 한정 | 2조+ |
| applies_after | **2024-01-01** (1차 공포 전 사전 우회) |
| reason | "정관변경에서 이사 선임 방법/정원 관련 조항 변경 — 집중투표·분리선출 사전 우회 의심 (KT&G 2025 사례). 본문 변경 내용 직접 확인 필수" |

### 4. B1-7 패턴 보강

**옛**: `all_of=["이사", "정수"]` + `any_of=["축소", "감축", "감소"]`
**새**: `parent_must_contain=["정관"]` + `all_of=["이사"]` + `any_of=["정수", "정원"]` + `secondary=["축소", "감축", "감소", "상한", "줄"]`

→ "이사회 정원 상한 축소" 같은 표현 catch.

## 근거

### 광범위 검증 결과

280 회사 (266 unique) audit:
- 2792 안건 / 213 hits (7.6%)
- 38 룰 중 11개 사용 (A1 6개 + B1 5개 — A1 정합 156 / B1 우회 20 / B2 자발 2)
- false positive 0건

### 분쟁 회사 segmentation

- KOSPI 200 hit 비율: 9.8%
- KOSDAQ 0-100 hit 비율: 1.8%
- 분쟁 회사 hit 비율: **11.6%**

분쟁 회사에서 B1-4b 8건 폭발 (영풍 6 + 현대엘리베이터 + 효성티앤씨) — 분쟁 시그널 매우 효과적.

### 회귀 안전성

iter 1+6 cumulative reclassification: 209 unique hits 중 변경 3건 / 유지 206건 — 모두 정확 향상.
- 서울보증보험 + 현대엘리베이터: B1-4 → B1-4b
- 하이브: B1-8b → B1-7 (더 specific reason)

## 영향 범위

- `open_proxy_mcp/services/proxy_advise.py` — `_agenda_pattern_match()`에 `parent_must_contain` / `parent_excludes` 지원
- `wiki/rules/laws/law_layer_rules.json` — 36 → **38 룰** (B1-4b + B1-8b 신규)
- `wiki/lessons/law-layer-precision-260508.md` — 정밀화 발견
- `wiki/architecture/audits/data/260508_law_layer/` — 광범위 검증 데이터

## Trade-off

- (+) 분쟁 시그널 효과 ↑ (B1-4b 8건 catch)
- (+) sentence 우회 catch (B1-8b — KT&G 사전 우회)
- (+) parent 매칭으로 false positive 감소
- (+) 회귀 0% — 더 specific reason으로 향상만 발생
- (-) 룰 수 증가 (36 → 38) — 유지 비용 소폭 증가
- (-) aoi_change 본문 매칭은 여전히 한계 (별도 ralph 필요)

## 비목표

- aoi_change 본문 매칭 — 큰 구조 변경 (별도 ralph)
- 시행 전 A2 룰 검증 — 2026-07-23 / 09-10 후 자연 검증
- 광범위 sample 부족 룰 (B1-1~B1-3, B1-5, B2-*) 추가 — sample 추가 시 진행

## archive

`wiki/architecture/audits/data/260508_law_layer/` (iter 1-5 spot 결과)
