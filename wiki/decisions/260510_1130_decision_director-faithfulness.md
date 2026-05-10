---
type: decision
title: 사외이사 충실성 강화 — 겸직 카운트 + 사내이사 독립성 표기 정정
date: 2026-05-10 11:30
status: active
related_ralph: [260510_1100_ralph_director-faithfulness-enhancement, 260510_1200_ralph_career-parser-concat]
related_lessons: [director-faithfulness-260510, career-parser-concat-260510]
related_audits: [architecture/audits/data/260510_director_faithfulness/iter1_findings]
---

# Decision — 사외이사 충실성 강화

## 결정

1. `evaluate_faithfulness` / `evaluate_faithfulness_basic`에 사외이사 겸직 카운트 추가
2. _extract_facts에서 사내이사 독립성 표기 "독립성 평가 비대상 (사내이사)"로 정정
3. faithfulness summary 통합 (concurrent → weak_concerns / concerns)
4. decision logic 변경 X (facts 노출만 — LLM 직접 검토용)

## 겸직 카운트 logic

```
사외이사_총_갯수 = careerDetails 매칭 (현재 + 사외이사) + (본 회사 표기 X면 +1)

≥ 3 → strong_concerns_concurrent (faithfulness.summary = "concerns")
≥ 2 → concerns_concurrent              (faithfulness.summary = "weak_concerns")
== 1 → single_position                  (정상)
== 0 → no_data
```

## 사내이사 독립성 표기

기존: `independence.summary` (independent / weak_concerns / concerns / long_tenure_concerns) 노출
신규: 사내이사일 때 "독립성 평가 비대상 (사내이사)" 강제 표기 (오인 방지)

사외이사/독립이사 (`role_type`에 "사외" 또는 "독립" 포함)일 때만 기존 summary 노출.

## false positive 회피

본 회사명이 careerDetails에 표기되어 있으면 (하나금융지주/우리금융지주 등) 그 entry는 본 회사로 인식 → 후보 본인 +1 안 함. 본 회사 1개만 사외이사 = 정상.

회사명 정규화: 공백/괄호/㈜/주식회사 제거 후 substring 매칭.

## 510 회사 회귀

- decision 변경 0 (audit_history_check만 활용 유지)
- facts 신규 노출:
  - `concurrent_outside_positions`: int (사외이사 한정)
  - `concurrent_summary`: str (사외이사 한정)
- 사외이사 후보 분포: concerns 13.3% / strong 2.7%

## 비목표

- 결정 logic 변경 X (별도 ralph)
- 최대주주 특수관계인 → 독립성에 유지 (충실성 X)
- 결격사유 4축 변경 X

## 영향 범위

- `open_proxy_mcp/services/director_evaluation.py`: count_outside_director_positions / _is_outside_director_role 헬퍼 + faithfulness 통합 + evaluate_candidate* 시그니처 (own_company_name 인자)
- `open_proxy_mcp/services/proxy_advise.py`: _extract_facts director_election 분기

## 데이터 가용성

careerDetails 채움률 98.4% (510 회사). 본 회사 후보 자동 +1 logic으로 빈 데이터도 최소 1개 카운트 보장.
