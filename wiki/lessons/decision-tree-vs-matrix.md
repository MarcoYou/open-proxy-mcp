---
type: lesson
title: 안건 결정 — 결정 트리 vs 매트릭스 (2가지 패턴)
context: 사내이사 성과 (매트릭스) + 보수한도/퇴직금 (결정 트리) 병존
date_learned: 2026-05-05
related_decisions: [260505_1900_decision_compensation-retirement-split]
---

# 결정 트리 vs 매트릭스 — 두 가지 결정 방식

## Context

OPM은 안건별로 FOR/AGAINST/REVIEW 결정을 반환한다. 결정을 만드는 방식은 안건 성격에 따라 크게 두 가지로 갈라진다.

코붕이가 지적: 사내이사 성과는 매트릭스로 갔는데, 보수한도/퇴직금도 매트릭스로 갈 수 있나? — 답은 "안건 성격에 맞는 방식이 따로 있다."

## 두 패턴

### 1. 매트릭스 방식 (multi-view scoring)

**언제 자연스러운가**:
- **시계열 axis가 있을 때** (재직 기간, 연속 N년 데이터 등)
- 같은 지표를 다른 view (예: avg, trend)로 봐서 cell 분해 가능할 때
- N axis × M view = N×M cell, 각 cell 독립 채점 → 종합 score
- score 분포 보고 classification cutoff 정함 (lessons/distribution-calibrated-thresholds 참조)

**장점**:
- 다축 평가 명시 (사용자가 어디서 점수를 잃었는지 visible)
- 종합 score로 객관적 cutoff (good/moderate/weak/bad)
- KOSPI/KOSDAQ 표본으로 distribution calibrate 가능

**단점**:
- 시간축 없으면 부자연스러움 (forced)
- cell 점수 합산이 의미를 가져야 함 (덧셈 정합성)

**예시: 사내이사 재직 중 성과 (2x3)**

```
재직 기간 8년 (2017~2024) → 시계열 데이터
3 지표 (ROE, 부채비율, CSR) × 2 view (avg, trend) = 6 cell
각 cell: good +2 / moderate +1 / weak 0 / bad -1
종합 -6 ~ +12 → classification (≥+7 good / ≥+3 moderate / ≥0 weak / <0 bad)
```

→ wiki/decisions/260505_1700_decision_inside-director-performance-matrix.md

### 2. 결정 트리 방식 (multi-criteria branching)

**언제 자연스러운가**:
- **Single-event 결정** (this year proposal — 보수한도, 퇴직금 변경, 정관변경, 배당 등)
- 시간축 없음 — 한 시점 안건의 다양한 trigger 검사
- 우선순위가 있는 분기 (먼저 검사할 trigger가 있고, 그 다음 fallback)
- 정책 근거가 명시적인 trigger들 (NPS [별표 1] N조 / 운용사 패턴)

**장점**:
- single-event에 자연스러움
- 정책 근거 (NPS 별표 / OPM Guideline) 1:1 매핑 가능
- 분기 trace 명확 (왜 이 결정이 나왔는지 reason 1줄로 표현)
- 우선순위 (first-match) 의미 명확

**단점**:
- 매트릭스 같은 종합 score 없음 — "얼마나 좋은가/나쁜가" 단계적 표현 어려움
- 분기가 많아지면 복잡 (10+ branch는 가독성 저하)

**예시: 보수한도 (이사)**

```
지표 → 정책 → 결정
─────────────────────────────────────────────────────
1. 자본잠식 full?               → AGAINST (OPM Guideline)
2. 소진율<30% AND 인상>0%?      → AGAINST (mainstream "남는데 더 늘림")
3. 인상률≥30% AND 순익yoy<0?    → AGAINST (NPS IV-33②)
4. 인상률≥50%?                  → REVIEW (대폭 인상)
5. 인상률 +30~50% AND 순익둔화? → REVIEW (NPS 보수적)
6. 인상률 -10~+10%?             → FOR (NPS IV-33① 원칙적 찬성)
7. 데이터 부족 + 흑자/자본 정상? → FOR (mainstream fallback)
8. 모든 데이터 부족?             → NO_DATA
```

→ wiki/ralph/260505_1750_ralph_compensation-retirement-split.md

**예시: 퇴직금**

```
1. 황금낙하산/경영권 변동 special 가산 신설? → AGAINST (NPS IV-35①)
2. 지급률 2배수 이상 인상?                  → AGAINST (s_legacy strict)
3. 지급 대상 확장?                         → REVIEW (남용 우려)
4. 위험 키워드 hit ≥1?                     → REVIEW
5. amendments ≥1, 위험 hit 0?              → REVIEW (raw 노출 + 검토)
6. 단순 표현 정정?                         → FOR
7. parser 추출 실패?                       → NO_DATA
```

## 어떻게 고를까

| 안건 성격 | 추천 방식 | 이유 |
|---|---|---|
| 시계열 + 다양한 측면 (재직 N년 평가) | **매트릭스** | 시간축이 자연 |
| Single-event + 다 trigger (this year 안건) | **결정 트리** | 시간축 X, 정책 trigger 1:1 |
| Qualitative 변경 (조항 텍스트) | **결정 트리** (키워드 매칭) | numeric matrix 불가 |
| 단일 지표 (예: 감사의견 적정/한정/부적정) | **결정 트리** (1단 lookup) | 매트릭스 과잉 |

**판단 질문 3개**:
1. **시간축이 있나?** (재직 기간, 연속 N년) — YES → 매트릭스 후보
2. **종합 score 의미가 있나?** (cell 합산이 "전반적 성과" 같은 단일 차원에 mapping) — YES → 매트릭스 후보
3. **정책 근거가 단순 trigger 형태인가?** (NPS 별표 X조 1 trigger = 1 결정) — YES → 결정 트리 후보

3개 다 YES이면 매트릭스가 자연. 3번만 YES면 결정 트리가 자연.

## Trade-off

- **매트릭스 강제 사용 위험**: 보수한도를 매트릭스로 가져가면 (인상률, 소진율, 경영성과) × (level, change) = 3x2 — 가능은 하지만 cell 의미가 약하고 score 덧셈도 임의적이 됨. 결정 trace가 약해짐.
- **결정 트리 강제 사용 위험**: 사내이사 성과를 트리로 짜면 "ROE good AND 부채 good AND CSR good → good" 같은 8-branch 누적 → 매트릭스 1 score보다 trace 어렵고 calibrate 불가.

## Takeaway

- **안건 성격이 결정 방식을 결정한다** (말장난 아님). 사용자/개발자 취향 X.
- 매트릭스: 시계열 + 다축 + 종합 score (사내이사 성과 같은 케이스)
- 결정 트리: single-event + 정책 trigger 1:1 매핑 (보수한도, 퇴직금, 재무제표, 배당 등 대부분의 안건)
- 두 방식 병존 OK — 한 tool 안에서 어떤 안건은 매트릭스로, 어떤 안건은 트리로 결정 가능 (proxy_advise가 그 예).
- 매트릭스 cutoff은 audit 표본 distribution 보고 정함 (lessons/distribution-calibrated-thresholds).
- 결정 트리 분기 순서는 우선순위 (자본잠식 같은 hard trigger 먼저, 데이터 부족 fallback 마지막).

## 관련

- [[distribution-calibrated-thresholds]] — 매트릭스 score cutoff calibrate 패턴
- [[ralph-threshold-realism]] — 데이터 성격이 threshold 결정
- 매트릭스 예시: `wiki/decisions/260505_1700_decision_inside-director-performance-matrix.md`
- 결정 트리 예시: `wiki/ralph/260505_1750_ralph_compensation-retirement-split.md`
