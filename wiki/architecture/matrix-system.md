---
type: architecture
title: 매트릭스 시스템 — 설계 + 자동 채점 (v1.3)
generated: 2026-04-28
updated: 2026-04-29
related:
  - open-proxy-guideline
  - 260429_0059_debate_opm-guideline-7전문가
sources:
  - decisions/decision-matrix-design.md (archived)
  - decisions/matrix-auto-scoring-2026-04-29.md (archived)
---

# 매트릭스 시스템

> 두 원본 문서 통합: 설계 (Part A) + 자동 채점 (Part B).

## Part A — 12 카테고리별 의사결정 매트릭스 설계

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

---

## Part B — 12 매트릭스 자동 채점 시스템 (v1.3)

## 1. 개요

`_decision_matrices.json`의 12 카테고리 매트릭스 100 dim을 OPM data tool 기반으로 자동 채점하는 시스템.
proxy_guideline scope=predict의 `auto_score=True` 옵션 (기본 ON)에서 사용.

- **자동 채점 dim**: ~85개 (data tool에서 직접 추출 또는 휴리스틱)
- **Manual dim**: ~15개 (사용자 input 필수, 데이터 미통합 영역)
- **빙고 패턴 평가**: 76 패턴 인터프리터로 자동 매칭 → for/against/review 결정

## 2. 모듈 구조

신규 파일: `open_proxy_mcp/services/proxy_guideline_scoring.py`

핵심 함수:

- `score_*` (각 dim별 채점 함수): 0/1/2/None 반환
- `auto_score_<category>` (12개 dispatch 함수): dim 점수 dict 반환
- `evaluate_bingo_pattern`: 단일 빙고 패턴 평가 (condition 표현식 → bool)
- `evaluate_all_bingo_patterns`: 매트릭스의 모든 빙고 평가
- `aggregate_score_to_decision`: 점수 + 빙고 → for/against/review 결정
- `auto_score_matrix`: 카테고리 → data tool 호출 → dim 점수 통합 진입점

## 3. 카테고리별 자동/Manual 분류

### 3.1 director_election (9 dim)

| dim_id | 모드 | 데이터 소스 |
|---|---|---|
| outside_director_independence | auto | board candidates careerDetails 5년 룰 휴리스틱 |
| tenure | auto | careerDetails 재직년수 추정 |
| concurrent_positions | auto | careerDetails "현)" 카운트 |
| attendance | auto | corp_gov_report metric 매칭 (없으면 None) |
| adverse_news | manual | Naver News API 미통합 |
| fiduciary_duty_signal | auto | related_party_transaction + ownership |
| governance_compliance_rate | auto | corp_gov_report.compliance_rate |
| diversity | auto | board summary female_count |
| bundled_slate_signal | auto | other dim 결과 + appointments 묶음 검출 |

### 3.2 director_compensation (8 dim)

| dim_id | 모드 |
|---|---|
| utilization_rate | auto (compensation_summary.priorUtilization) |
| yoy_change | auto (current vs prior limit 차이 %) |
| ceo_pay_ratio | manual (peer 데이터 미통합) |
| performance_link | manual (TSR 통합 필요) |
| stock_option_dilution | auto (휴리스틱, 정량 부족 시 yellow) |
| retirement_pay | auto (안건 텍스트 황금낙하산 매칭) |
| company_performance | manual (financial_statements 미통합) |
| clawback_say_on_pay_signal | auto (corp_gov_report 추정) |

### 3.3 articles_amendment (9 dim)

전부 auto. agenda 텍스트 키워드 기반 + disclosure_compliance만 날짜 차이 정량.

### 3.4 audit_committee_election (8 dim)

- auto (5): 3pct_rule_compliance, separate_election, independence_5year, financial_expertise, compliance_rate, fiduciary_duty_signal
- manual (2): audit_opinion_history, non_audit_fee_ratio (audit_fee_disclosure tool 미통합)

### 3.5 treasury_share (8 dim)

- auto (6): burnout_commitment, purpose_clarity, disposal_method, ownership_structure_signal, treasury_share_ratio, fiduciary_duty_signal
- manual (2): disposal_agm_approval, shareholder_return_ratio

### 3.6 cash_dividend (8 dim)

- auto (5): payout_ratio_vs_industry (history 절대값), policy_disclosure (corp_gov metric), interim_quarterly_dividend, controlling_shareholder_signal, compliance_rate
- manual (3): cash_flow_sustainability, dividend_decision_authority, shareholder_return_ratio

### 3.7 financial_statements (9 dim)

- auto (2): fiduciary_duty_signal, compliance_disclosure
- manual (7): audit_opinion, non_audit_fee_ratio, accounting_error_history, internal_control_weakness, auditor_tenure, auditor_independence_signal, climate_disclosure (KIND/ESG 통합 필요)

### 3.8 merger (8 dim)

- auto (2): controlling_shareholder_conflict, anti_takeover_signal
- manual (6): 외부평가 + MoM + 시너지 등 (정성 영역)

### 3.9 spin_off (8 dim)

- auto (3): split_method, purpose_clarity, fiduciary_duty_signal
- manual (5): 자회사 상장 + 보호 + 평가 등

### 3.10 capital_increase_decrease (8 dim)

- auto (5): preemptive_right, anti_takeover_signal, capital_decrease_type, fiduciary_duty_signal, disclosure_compliance
- manual (3): issuance_size, issuance_purpose, issuance_price

### 3.11 cb_bw (8 dim)

- auto (1): fiduciary_duty_signal
- manual (7): cb_bw_disclosure tool 미통합 영역 다수

### 3.12 shareholder_proposal (9 dim)

- auto (4): esg_sustainability, minority_shareholder_protection, controlling_shareholder_conflict, active_engagement_signal
- manual (5): 장기 가치, 비교, 자격, 미시행 이력 등

## 4. 통계

- 총 dim: 100
- 자동 채점 (auto): ~71 dim
- manual 입력 권장: ~29 dim (financial_statements, merger, cb_bw가 manual 비중 높음)

자동 채점 비중이 카테고리마다 다른 이유: data tool 통합 정도. 향후 audit_fee_disclosure / esg_disclosure / cb_bw_disclosure tool 추가 시 자동 채점 비중 90%+로 상승 예정.

## 5. 빙고 패턴 인터프리터

### 5.1 condition 표현식 파싱

지원 문법:

```
dim_id=0           # 정확히 0 (red)
dim_id=2           # 정확히 2 (green)
dim_id≥1           # 1 이상
dim_a=0 AND dim_b=0   # 모두 만족
others ≥1          # 다른 dim (이미 매칭한 dim 제외) 모두 ≥1
모든 dim = 2       # 전체 dim green
```

### 5.2 시점 + 카테고리 조건

`안건일 ≥ 2026-03-06` → meeting_date 비교, threshold 미만이면 패턴 skip
`안건이 사외이사 선임` → agenda_category 매칭

### 5.3 평가 불가 조건 (skip)

- `자산 ≥ 2조원`: 회사 자산 통합 미가능 → 보수적 skip
- `회사 독립이사 비율 < 1/3`: board composition 메타 통합 필요

skip 시 패턴 미트리거 (보수적 — false negative 우선).

## 6. 점수 → 결정 매핑

`aggregate_score_to_decision` 로직:

1. **빙고 우선**:
   - against 빙고 1+ → against
   - review 빙고 1+ → review
   - for 빙고 + against/review 빙고 0 → for

2. **점수 fallback** (빙고 미트리거):
   - 2+ dim red → against
   - raw_score ≤ 7 + 데이터 충분 → against
   - raw_score ≥ 12 + red 0 + unknown 0 → for
   - else → review

3. **안전망 (conservative)**:
   - unknown ≥ total/2 + 빙고 미트리거 → review로 강제
   - 채점 오류 발생 시 → review

## 7. predict scope 자동 채점 통합

기존 사용 (수동 입력만):

```python
matrix_dimensions = {"outside_director_independence": 0, "fiduciary_duty_signal": 0}
result = await build_proxy_guideline_payload(
    scope="predict",
    company="KT&G",
    agenda_title="...",
    matrix_dimensions=matrix_dimensions,
    auto_score=False,
)
```

신규 사용 (자동 채점):

```python
result = await build_proxy_guideline_payload(
    scope="predict",
    company="KT&G",
    agenda_title="사외이사 김용기 선임의 건",
    auto_score=True,  # 기본
)

# result.data.auto_decision = {decision: against, raw_score: 6, ...}
# result.data.bingo_matches = [...]
# result.data.matrix_score.dimensions_scored = {...}
# result.data.manual_dims = ["adverse_news"]  # 입력 권장 안내
```

manual override 가능:

```python
result = await build_proxy_guideline_payload(
    scope="predict",
    company="KT&G",
    agenda_title="...",
    auto_score=True,
    matrix_dimensions={"adverse_news": 0},  # 자동 채점 결과에 사용자 input 합쳐 평가
)
```

## 8. prepare_vote_brief 통합

`auto_score_matrix=True` 옵션으로 안건별 자동 채점 활성화:

```python
result = await build_vote_brief_payload(
    company_query="KT&G",
    auto_score_matrix=True,
)
# result.data.proxy_guideline_brief.agenda_recommendations[i].auto_score = {
#     "decision": "against", "raw_score": 6, "triggered_pattern_ids": [...]
# }
```

상위 5건만 자동 채점 (cost 보호). 카테고리 중복 시 1회만 채점.

## 9. 검증 결과 (KT&G + 삼성전자)

### 9.1 KT&G 사외이사 선임

- 카테고리: director_election
- 자동 결정: **AGAINST** (score_red_2plus)
- 점수: 6/18 (red 3, unknown 3)
- 채점 dim: outside_director_independence=0, tenure=0, concurrent_positions=0, fiduciary_duty_signal=2, governance_compliance_rate=2, bundled_slate_signal=2
- unknown: attendance, adverse_news, diversity (데이터 부족)
- data_calls: board=exact, corp_gov=exact, ownership=exact, related_party=exact

### 9.2 삼성전자 보수한도

- 카테고리: director_compensation
- 자동 결정: **REVIEW** (score_mid)
- 점수: 6/16 (red 0, unknown 4)
- 채점 dim: utilization_rate=2, yoy_change=1, retirement_pay=2, clawback_say_on_pay_signal=1
- unknown: ceo_pay_ratio, performance_link, stock_option_dilution, company_performance (manual 영역)
- data_calls: compensation=exact, corp_gov=exact

두 회사 모두 정상 작동. 각각 against / review 결정에 도달. 데이터 부족(unknown) 시 conservative review로 fallback하는 안전망 정상 동작 확인.

## 10. 진화 방향

- audit_fee_disclosure tool 추가 → audit 카테고리 자동 비중 증가
- search_naver_news + LLM 분류 → adverse_news, audit_opinion_history 자동화
- esg_disclosure tool → climate_disclosure 자동화
- cb_bw_disclosure tool → cb_bw 카테고리 자동 채점 비중 80%+
- 회사 자산 통합 (own_corp_metadata) → "자산 ≥ 2조원" 조건 평가 가능

## 11. 안전망

- 채점 오류 발생 시 conservative review로 fallback (`scope_predict` try/except)
- unknown dim 절반 이상이면 for/against 강제로 review 변환 (단, 빙고 트리거 시 유지)
- disclaimer 메시지 자동 포함: "최종 판단은 사용자가 검토 후 결정"
- manual dim 명시적 표시 + 입력 가이드
