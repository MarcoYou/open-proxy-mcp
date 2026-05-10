---
type: lesson
title: 법령 layer body 매칭 시도 — 회귀 위험으로 보류 + 광범위 sample 검증
date: 2026-05-10
related:
  - wiki/ralph/260510_0747_ralph_law-layer-body-matching.md
  - wiki/lessons/law-layer-precision-260508.md
  - wiki/architecture/audits/data/260510_law_layer_450/README.md
  - wiki/architecture/audits/data/260510_law_layer_body/
related_decisions: [260508_0700_decision_law-layer-precision, 260510_0900_decision_d-pattern-body-fallback]
related_audits: [260510_agenda_hierarchy/iter1_findings]
---

# Ralph 6 — body 매칭 시도 + 광범위 검증 회고

## 배경

Ralph 4 (260508 법령 layer 정밀화) + 후속 audit (260510_law_layer_450)에서 4 회사 (에코프로비엠 / 카카오게임즈 / 에스엠 / 메리츠금융지주) 가 sub-agenda 안 펼쳐지는 top-level "정관 일부 변경의 건"만 노출 → spot 미매칭 발견. body 매칭 추가 시도.

## 시도 + 결과 (자체 설계 한계 발견)

### Design 시도 1 — 모든 amendments 통합 body 매칭

```python
body_text = sum(am.before + am.after + am.reason for am in amendments)
if any rule pattern matches body_text → hit
```

**결과 — Regression 발생**:
- LG화학 "정관 정비" / "권고적 주주제안 도입" / "선임독립이사 선임" sub-agenda 모두 A1-1로 잘못 hit
- amendments 통합본을 검사하니 한 안건의 본문 키워드가 모든 sub-agenda에 적용됨

### Design 시도 2 — title fuzzy 매칭 + 자기 amendment body만

```python
matched_am = _find_amendment_for_title(title, amendments)
if matched_am: check matched_am.body only
```

**결과 — 또 Regression**:
- LG화학 "정관 정비" → fuzzy 매칭으로 잘못된 amendment 매칭 → A1-1 false positive
- title fuzzy 매칭 (≥2 키워드 overlap)이 일반 표현 ("정관 정비") 시 임의 amendment 매칭

### 핵심 문제

**amendments는 통합본, sub-agenda hierarchy와 1:1 매칭 어려움**:
- LG화학: top "정관 변경의 건" + 8 sub-agenda. amendments는 별도 list (clause/label로만 식별).
- 4 미매치 회사: top "정관 일부 변경의 건"만 + sub-agenda 없음. amendments에 의존해야 catch.

같은 logic을 두 케이스에 적용 어려움 → **Phase 1 보류**.

## 광범위 sample 검증 (성과)

### Ralph 6 신규 spot (160 회사)

| Source | 회사 | hits |
|---|---|---|
| KOSDAQ 시총 151~300위 | 150 | 12 |
| 분쟁 신규 (두산밥캣/태영건설/HYBE/SOOP/카카오/LS/두산/한화솔루션/삼성SDI/삼성E&A) | 10 | 16 |
| **합계** | **160** | **28** (2.3%) |

### 누적 (Ralph 4 + 5 + 6) = ~510 회사

- A1 시리즈 (강행규정 정합) 광범위 catch — 회사 자발 정관 정비 활발
- B1-7 (이사회 정원 축소) — 하이브 catch ✓
- A1-2 (집중투표 도입) — 삼성SDI 자발 catch ✓
- B1-4b (후보 임기 1년) — 분쟁 회사 (영풍/현대엘리베이터) 핵심 신호

### 미사용 룰 (~30개)

광범위 sample (510)에도 미사용 — 다음 분류:
- **시행 전 (5)**: A2-1~A2-5 (2026-07-23 / 09-10 시행 후 자연 catch)
- **C signal (4)**: C-1~C-4 (agenda 비대상 — ownership signal)
- **자산 2조+ + 본문 검사 필요 (5)**: A1-8 (자사주 의무소각), B1-6, B1-8, B1-9 (감사위 정원 5명+) — title 매칭 한계
- **본문 검사 필요 (10)**: B1-1~B1-3, B1-5, B2-1~B2-7, B2-9 (시차임기 / 보수 정관 명시 / 자사주 재단 출연 등 — 모두 본문 키워드)

**활성화는 body 매칭 또는 LLM raw 판단 필요** — 다음 ralph 영역.

## 핵심 교훈

### 1. amendments hierarchy 매칭의 본질적 어려움
- DART aoi_change scope의 amendments[]는 정관 조항 단위 (clause/label). 안건 hierarchy의 sub-agenda와 1:1 매핑 어려움.
- LG화학처럼 sub-agenda 명확한 회사 + 4 미매치 회사처럼 일반 표현 회사 — 같은 logic 적용 어려움.

### 2. body 매칭은 별도 architect 필요
- 안건 hierarchy 외부에 amendments 별 가상 안건 노출 (proxy_advise 응답 구조 변경)
- 또는 LLM이 정관 본문 raw 직접 판단 (B1/B2 raw 첨부 패턴 확장 — A1/A2도 raw 첨부)
- 단순 patch로 안 됨 — design ralph 별도 필요.

### 3. 광범위 sample = 강행규정 정합 회사 비율 측정 가치
- 350 + 160 = 510 회사. 자산 2조+ 회사 대다수 정관 정비 활발 (1·2·3차 상법 개정 정합).
- KOSDAQ 자산 2조 미만은 자발 정합 (의무 X 회사들도 사외이사→독립이사 명칭 등 수용).

### 4. spot script의 한계 = title 매칭만
- spot_law_layer.py = shareholder_meeting_notice scope=summary + _law_layer (title only)
- body 검사 없음. 4 미매치 회사는 spot에서 catch 불가.
- 전체 검증은 proxy_advise (full action tool) 호출 필요.

## promise 미달 (정직한 결정)

Ralph 6 success criteria:
- G1 body 매칭 추가 — **미달** (regression 위험으로 보류)
- G2 광범위 sample 추가 — **달성** (160 회사)
- G3 미사용 룰 활성화 — **부분 달성** (B1-7 활성, B1-4 / B1-10 / 시차임기 등 미활성)
- G4 회귀 0% — **달성** (revert로 회귀 0)

→ G1 미달 → `LAW_LAYER_BODY_MATCHING_VERIFIED` promise 출력 X (false promise 거부).

## 다음 ralph 후보

1. **body 매칭 architect ralph** — amendments 별 가상 안건 노출 또는 raw 첨부 패턴 확장
2. **A2 시행 후 자연 검증** — 2026-07-23 / 09-10 후 KOSPI 200 재spot
3. **분쟁 회사 광범위 spot** — 60+ 분쟁 회사 모음 + B1/B2 우회 패턴 verification

## archive

- `wiki/architecture/audits/data/260510_law_layer_body/kosdaq_151-300.json` (150)
- `wiki/architecture/audits/data/260510_law_layer_body/dispute_new_10.json` (10)
- `wiki/architecture/audits/data/260510_law_layer_body/dispute_new_10.csv` (universe)
