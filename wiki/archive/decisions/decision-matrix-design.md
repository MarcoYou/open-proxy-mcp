---
type: decision
title: 12 카테고리별 의사결정 매트릭스 설계
generated: 2026-04-28
related: [open-proxy-guideline, opm-guideline-debate-transcript]
---

# 12 카테고리별 의사결정 매트릭스 설계

## 1. 설계 철학

### 1.1 단일 체크리스트 vs 다차원 매트릭스 vs 빙고 패턴

기존 운용사 정책의 한계:

- **단일 체크리스트**: "X면 against, Y면 for" 라인 단위 룰. ambiguous 룰은 실무자 재량 → case_by_case 표류 → 결국 default for. T행동주의·한투의 정책 문구는 강하나 실행 against rate 5.5-7.7% (낮음)인 이유.
- **자문사 의존**: ISS·대신경제 권고 그대로 따르는 경향. 한국 특수성 누락 (5 운용사 silent 영역).
- **이해상충 처리 부재**: 삼성 director_election against rate 3.0% vs M레거시 26.1%. 그룹사 안건에서 정책-실제 갭.

OPM의 해결책:

- **다차원 매트릭스 (12 × 8)**: 카테고리당 8개 차원으로 다각도 평가
- **정량 자동 채점**: 0/1/2 (red/yellow/green) — OPM data tool 자동 추출
- **빙고 패턴**: 정량 임계 × 정성 신호 결합 (예: "독립성 red + 충실의무 red = 자동 against")
- **충실의무 cross-cutting**: 모든 매트릭스에 fiduciary_duty_signal dim 포함 (A7 §382의3 게임체인저 적용)

### 1.2 운용사·자문사와의 차별화

| 항목 | 5 운용사 | ISS·Glass Lewis·대신경제 | OPM v1 |
| --- | --- | --- | --- |
| 매트릭스 형태 | 없음 (선형 룰) | 없음 (선형 룰 + 분석 첨부) | 12 × 8 + 빙고 패턴 |
| 정량 자동 추출 | 부분 (한도·년수만) | 부분 (P75·percentile) | 전면 (data tool 매핑) |
| 충실의무 cross-cutting | 미적용 | 미적용 | 모든 카테고리 fiduciary_duty_signal dim |
| 2026 신법 자동 트리거 | 미반영 | 부분 | 시점별 자동 (2026.03/05/07/09/2027.01) |
| 그룹사 conflict flag | 자율 | 자율 | 자동 부착 + 별도 통계 |

## 2. 공통 8 Dimension 모델 + 카테고리별 특화

### 2.1 Dimension Schema

각 dim은 다음 5 필드를 가진다:

```yaml
dim_id: snake_case_id
label: 한글 설명
evaluator: OPM data tool (또는 사람 검토)
scoring:
  green_2: 합의 충족 + 표준 초과
  yellow_1: 부분 충족 또는 의심
  red_0: 명백한 위반 또는 강행규정 위반
data_source: [tool_name1, tool_name2]
law_basis: 상법 / 자본시장법 / 외감법 / KRX 핵심지표 / OECD / Sarbanes-Oxley 등
```

### 2.2 Cross-Cutting 4 Dimensions

모든 12 카테고리에 공통 적용:

1. **fiduciary_duty_signal** — §382의3 (2025 강화) 위반 신호
2. **korea_2026_law_compliance** — 2026 신법 7개 시점별 부합성
3. **governance_compliance_rate** — KRX 기업지배구조보고서 핵심지표 준수율
4. **controlling_shareholder_conflict_flag** — 그룹사 안건 자동 부착

### 2.3 카테고리별 특화 Dim 예시

| 카테고리 | 특화 dim |
| --- | --- |
| director_election | outside_director_independence, tenure, concurrent_positions, attendance, adverse_news, diversity |
| director_compensation | utilization_rate, yoy_change, ceo_pay_ratio, performance_link, stock_option_dilution, retirement_pay, company_performance, clawback_say_on_pay_signal |
| treasury_share | burnout_commitment, purpose_clarity, disposal_method, disposal_agm_approval, ownership_structure_signal, treasury_share_ratio, shareholder_return_ratio |
| spin_off | subsidiary_listing_plan, split_method, minority_shareholder_protection, fairness_evaluation, purpose_clarity, info_disclosure |
| merger | merger_ratio_fairness, fairness_opinion_independence, controlling_shareholder_conflict, MoM_simulation, synergy_clarity, appraisal_right, anti_takeover_signal, stakeholder_impact |
| cb_bw | agm_resolution, dilution_rate, refixing_clause, call_option, third_party_independence, conversion_price, issuance_purpose |

## 3. 점수 시스템

### 3.1 0-16 점수 체계

- 각 dim: 0 (red) / 1 (yellow) / 2 (green)
- 8 dim × 2 = 16점 만점

### 3.2 임계값 (Thresholds)

```yaml
for: ≥12 + 모든 dim ≥1
review: 8-11 또는 (≥12이지만 1+ dim red)
against: ≤7 또는 2+ dim red
```

### 3.3 빙고 패턴 우선순위

빙고 패턴 매칭 시 점수 결과 무관 패턴 결정 우선. 예:

```yaml
pattern_id: independence_red_x_fiduciary_red
condition: outside_director_independence=0 AND fiduciary_duty_signal=0
decision: against
rationale: 독립성 결여 + 충실의무 위반 신호 — 강행규정 §542의8 + §382의3 동시 위반
```

이 경우 다른 6개 dim이 모두 green이어도 against (강행규정 위반 절대).

## 4. 빙고 패턴 카탈로그 (12 매트릭스 × 5+ = 67개 패턴)

### 4.1 패턴 분류

- **강행규정 위반 패턴**: 다른 dim 무관 against 절대 (예: audit_3pct_evasion, korea_2026_evasion)
- **이중 신호 패턴**: 두 dim red 결합 시 against (예: independence_red_x_fiduciary_red)
- **시점 조건 패턴**: 안건 일자 + dim 조건 (예: korea_2026_burnout_red — 2026-03-06 이후)
- **회사 속성 조건 패턴**: 회사 자산 + dim 조건 (예: korea_2026_separate_election_red — 자산 2조+ + 2026-09-10 이후)
- **컨텍스트 결합 패턴**: dim + 분쟁 상태 (예: korea_dispute_for — 분쟁 중 + 주총 승인 요구)

### 4.2 카테고리별 대표 패턴

| 카테고리 | 대표 빙고 | 결정 | 근거 |
| --- | --- | --- | --- |
| director_election | independence_red_x_fiduciary_red | against | A1+A3+A7 |
| director_compensation | deficit_x_increase | against | A1+A3+A5+A7 |
| articles_amendment | korea_2026_evasion | against | 2026 신법 회피 |
| audit_committee_election | 3pct_evasion | against | 5/5 + 7 전문가 |
| treasury_share | korea_2026_burnout_red | against | 2026.03 신법 위반 |
| treasury_share | disposal_third_party_red | against | 자사주 마법 (한진칼 패턴) |
| spin_off | physical_split_x_listing | against | LG엔솔 패턴 |
| spin_off | korea_2026_violation | against | 2026.07 신법 위반 |
| merger | ratio_red_x_controlling_red | against | 삼성물산-제일모직 패턴 |
| merger | MoM_red | against | A1+A3+A5+A7 신규 |
| capital_increase_decrease | issuance_red_x_preemptive_red | against | 강행 §418 ② |
| cb_bw | refixing_red | against | A1+A3 한국 핵심 폐단 |
| shareholder_proposal | esg_for | for | BlackRock 2024 |
| shareholder_proposal | korea_dispute_for | for | 한진칼 KCGI |
| financial_statements | non_appropriate_opinion | against | 5/5 + 7 전문가 |

## 5. OPM Data Tool 매핑

### 5.1 Tool 카테고리

OPM 36 tools 중 매트릭스 평가에 사용:

```yaml
shareholder_meeting:
  - agm_*_xml (소집공고 XML)
  - agm_disclosure
  - agm_candidate_career
  - agm_candidate_concurrent
  - agm_candidate_tenure
  - agm_board_diversity
  - agm_compensation
  - agm_stock_option

ownership_structure:
  - own_shareholder_relations
  - own_*

naver_news:
  - search_naver_news
  - naver_news_aggregator

corp_gov_report:
  - gov_compliance_rate
  - gov_kosdaq_attendance

corp_identifier:
  - corp_identifier (그래프 중심 노드)

merger_disclosure:
  - merger_disclosure
  - fairness_opinion_disclosure

treasury_share:
  - treasury_share_history
  - buyback_disclosure

dividend:
  - dividend_history
  - dividend_policy_disclosure

audit_fee:
  - audit_fee_disclosure
  - external_auditor

cb_bw_terms:
  - cb_bw_disclosure
  - kind_disclosure

compensation:
  - compensation_2026_disclosure
  - compensation_history

spin_off:
  - spin_off_disclosure
  - subsidiary_listing_plan
```

### 5.2 자동 추출 가능 Dim (Priority 1)

다음 dim은 OPM data tool에서 100% 자동 추출 가능:

- 사외이사 5년 룰 (agm_candidate_career → 5년 내 임직원 매칭)
- 6년 재직 (agm_candidate_tenure)
- 출석률 75% (gov_kosdaq_attendance)
- 겸직 개수 (agm_candidate_concurrent)
- 스톡옵션 희석률 (agm_stock_option / 발행주식수)
- 비감사용역 보수 비율 (audit_fee_disclosure)
- 발행예정주식 증가율 (kind_disclosure)
- CB/BW 희석률 (cb_bw_disclosure)
- 자사주 비중 (own_shareholder_relations)
- 배당성향 vs 동종업계 (dividend_history + 산업분류 매핑)
- 기업지배구조보고서 준수율 (gov_compliance_rate)
- 다양성 (agm_board_diversity)

### 5.3 자동 추출 + LLM 보조 Dim (Priority 2)

다음 dim은 자동 추출 + LLM/사람 검토:

- 부정 보도 (search_naver_news + LLM 분류)
- 충실의무 위반 신호 (related_party_transaction + LLM 패턴 인식)
- 합병 시너지 명확성 (merger_disclosure + LLM 분석)
- 분할 목적 (spin_off_disclosure + LLM 의도 분석)
- MoM 시뮬레이션 (own_shareholder_relations + 계산)
- 회사명 변경 코붕이 criteria (articles_amendment + LLM 분류)

### 5.4 사람 검토 필수 Dim (Priority 3)

- adverse_news 중대성 판단
- 임원진-감사인 관계 (auditor_independence_signal)
- 분쟁 컨텍스트 (korea_specific_dispute, ownership_structure_signal)

## 6. 운용사·자문사 비교

### 6.1 5 운용사 매트릭스 부재

5 운용사 모두 매트릭스 형태 정책 없음. 선형 룰 ("X면 against") + 외부 자문 의존.

### 6.2 ISS·Glass Lewis 부분 매트릭스

- ISS Pay-for-Performance: 정량 점수 + Concern Level (Low/Medium/High) — 부분 매트릭스
- Glass Lewis Korea: Anti-takeover Score, Compensation Score — 부분 매트릭스
- 둘 다 빙고 패턴 형태 부재

### 6.3 OPM 차별화

- **12 × 8 매트릭스**: 카테고리당 다각도 평가
- **빙고 패턴**: 정량 + 정성 결합 (자동 결정)
- **충실의무 cross-cutting**: §382의3 모든 매트릭스 적용
- **2026 신법 시점별 자동 트리거**

## 7. 시각화 권고

### 7.1 매트릭스 히트맵

각 회사 × 각 매트릭스 → 8 dim 점수 히트맵:

```
director_election (회사 X)
┌────────────────────────────────────────────────┐
│ outside_director_independence  ████████  2/2  │
│ tenure                         ████      1/2  │
│ concurrent_positions           ██████    1/2  │
│ attendance                     ████████  2/2  │
│ adverse_news                   ████████  2/2  │
│ fiduciary_duty_signal          ▒         0/2  │ ← red
│ governance_compliance_rate     ████      1/2  │
│ diversity                      ████████  2/2  │
├────────────────────────────────────────────────┤
│ TOTAL: 11/16  → review                         │
│ BINGO: independence_red_x_fiduciary_red 미매칭 │
│ DECISION: review (사용자 검토)                 │
└────────────────────────────────────────────────┘
```

### 7.2 점수 막대 차트

회사 간 비교용:

```
Director Election Score (12 회사)
SK하이닉스   ████████████ 14/16  for
삼성전자     ███████████  12/16  for
NAVER       █████████    11/16  review
KB금융       █████████    10/16  review
HD현대       ███████      8/16   review (border)
LG에너지솔루션 ████        6/16   against
[...]
```

### 7.3 빙고 패턴 강조

매칭된 빙고 패턴 별도 highlight:

```
🚨 BINGO: korea_2026_burnout_red
   condition: burnout_commitment=0 AND 안건일 ≥ 2026-03-06
   decision: AGAINST (override)
   rationale: 2026.03 신법 자기주식 1년 내 의무소각 위반
```

## 8. 검증 기준

- 12 카테고리 매트릭스 모두 작성: ✓
- 각 매트릭스 8 dim: ✓
- 각 매트릭스 5+ 빙고 패턴: ✓ (총 67 패턴)
- 모든 dim에 data_source 명시: ✓
- 모든 dim에 law_basis 명시: ✓
- Cross-cutting 4 dim 적용: ✓ (fiduciary, korea_2026, governance, controlling_shareholder_flag)

## 9. 향후 진화

### 9.1 v2 계획

- 각 dim별 가중치 도입 (예: fiduciary_duty_signal 1.5배)
- 회사별 학습 (machine learning) — 과거 결정 정합성 검증
- 사용자 피드백 루프 (사용자가 OPM 결정 override 시 패턴 학습)

### 9.2 데이터 의존성

- 2026 KOSPI 전체 842사 기업지배구조보고서 의무 → 데이터 가용성 확대
- 2026.05 임원보수 TSR 병기 → ceo_pay_ratio + performance_link 자동화
- 2026.03.01 주총 표결결과 공시 → 주주 다수 승인 미시행 자동 감지
