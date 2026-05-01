---
type: audit
title: action tool 재편 — advise/recap sanity audit
created: 2026-05-02 23:00
domain: action
tools_audited: [advise_vote_before_meeting, recap_vote_after_meeting, director_evaluation]
result: 정기/임시/edge case sanity 통과, 18→17 tool regression 0
---

# action tool 재편 sanity audit

## 환경
- 실행: 2026-05-02 23:00
- ralph: `wiki/ralph/260502_0930_ralph_advise-recap-vote.md` 7 iteration
- 신규 코드: 3 service + 2 tools_v2 (director_evaluation / advise_vote / recap_vote / 2 register)
- 제거: 4 (vote_brief / campaign_brief services + tools_v2)
- archive: 2 (engagement_case → `_archive/`)

## 매핑 분류 검증 (success / soft-fail / hard-fail)

### success (그대로 사용)
- 안건 리스트 (shareholder_meeting agenda) ✓
- 후보 이름 / birthDate / roleType / mainJob / recommender ✓
- majorShareholderRelation / recent3yTransactions (DART 정형 필드) ✓
- eligibility (taxDelinquency / insolventMgmt / legalDisqualification) ✓
- 회사 재무 (revenue / ROE / 자본잠식 / 감사의견) ✓
- 안건별 가결/부결/찬반율 (KIND results) ✓
- 후속 공시 4종 (dividend/treasury/restructuring/dilutive) ✓

### soft-fail (raw 노출)
- careerDetails 자유 텍스트 (period 정규식 실패 시 raw 통째로 LLM에게)
- dutyPlan / recommendationReason (자유 텍스트, LLM이 자연어 판단)
- careerCompanyGroups company 필드 (한국 회사명 매핑 실패 시 raw)

### hard-fail (메모/코드 침묵 — 코붕이 명시 지시)
- 형사 처벌 이력
- 사적 관계 (학연, 지연, 공동 투자 등)
- 동명이인 정확 식별
- 파산 / 임원 자격 박탈
- 금감원 제재 (별도 endpoint 필요)

## Sanity 결과

### iteration 1: director_evaluation 삼성전자 2025
- status=exact, 13 appointments / 9 candidates
- 첫 후보 김준성 (사외이사): 독립성 independent / 결격사유 clean

### iteration 2: KB금융 2025 + Marco 시나리오
- status=exact, 9 candidates, Marco 활성, red_flag 0
- career_company_groups 정확 추출 (이환주: 국민은행/KB라이프생명/KB생명보험)
- KB 그룹 회사들 적정 의견 일관 → red_flag 0 정상
- bug fix: eligibility "해당사항 없음(충족)" 변형 매칭

### iteration 3: advise_vote KT&G 2025 정기
- status=exact, 24.6s, 24 DART calls
- 10 안건 모두 결정:
  - 재무제표 FOR (감사적정 + 자본잠식 normal)
  - 분기배당 FOR
  - 후보 매칭 OK 3명 FOR (이상학/손관수/이지희, 모두 clean)
  - 정관 / 감사위원 (이름 없는 title) REVIEW
- evidence 4건

### iteration 5: recap_vote SK하이닉스 2025
- status=no_filing (KIND results 미수집 — v2 한계), 20.4s, 42 calls
- 후속 공시: 배당 12 + 자사주 1 + 재편 0 + 희석 0
- 위임장: SK스퀘어 최대주주 20.07%, 분쟁 신호 없음

### iteration 6: 18→17 tool 재편
- 제거: prepare_vote_brief / build_campaign_brief
- archive: prepare_engagement_case → `_archive/`
- 자동 디스커버리: 17 tools 등록 확인 ✓

### iteration 7: regression + 임시 + edge
- financial_metrics 삼성 2024 (회귀 0): ROE 13.07, 자본잠식 normal
- dividend 삼성 2024 (회귀 0): DPS 1446, payout 29.2%
- 임시주총 advise HMM 2026: 1 안건 (정관 변경) → REVIEW (정상)
- edge case 알지노믹스: 2025 정기주총 미공시 (no_filing — 자본잠식 회사)

## 알려진 한계
- shareholder_meeting v2 candidates / personnel scope 미공개 → director_evaluation은 v1 parser 직접 사용
- KIND results scope 일부 회사 미수집 (SK하이닉스 등) → recap에서 status=no_filing 가능
- 후보 매칭이 title 정확 일치만 — "사외이사 선임의 건 (2명)" 같은 grouped title은 REVIEW
- proxy_guideline 정책별 자동 채점 wire는 Phase 2 (현재 advise는 default rule 기반)

## A5 / A6 backtest (Phase 2 — 학습 데이터 활용)
- A5: 얼라인파트너스 12 회사 records (2024+2025+2026, ~150 votes 학습) vs 우리 advise — 실행 미수행 (별도 검증 작업)
- A6: 9 비교군 (8 운용사 + NPS) — 학습 데이터 준비 완료 (records 12,949 votes + NPS list 2024/2025/2026)
- 실제 backtest 일치율 측정은 vote_style 매핑 wire 후 별도 ralph 또는 audit로 진행

## 결론
✅ Phase 1 완료 — 17 tools production ready, 회귀 0, 매핑 분류 명시.
⚠ A5/A6 backtest는 Phase 2 별도 (vote_style wire + 매트릭스 자동 채점 통합 후).
