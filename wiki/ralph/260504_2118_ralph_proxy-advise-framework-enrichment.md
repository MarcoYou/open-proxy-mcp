---
type: ralph
title: proxy_advise framework enrichment — 4 dimension 노출률 + 1번안건 FY 본문 raw + 신임/연임 auto detect
created: 2026-05-04 21:18
completion_promise: PROXY_ADVISE_FRAMEWORK_VERIFIED
max_iterations: 20
---

## Invoke (복붙)

```
/ralph-loop:ralph-loop wiki/ralph/260504_2118_ralph_proxy-advise-framework-enrichment.md 가이드 따라 proxy_advise enrichment 보강. KOSPI200 표본 100 회사 검증. framework 4 dimension 노출률 ≥90% + NO_DATA false-positive ≤5% + 신임/연임 auto detect 정확도 ≥85% + 1번안건 FY 본문 raw 추출 ≥80% 모두 충족 시 promise. --completion-promise PROXY_ADVISE_FRAMEWORK_VERIFIED --max-iterations 20
```

> invoke history는 [[invoke-history]] 참조.

# Ralph: proxy_advise framework enrichment

## 배경

직전 batch (commit `549a354` 까지) — proxy_advise에 다음 enrichment 완료:
- agenda_decisions에 facts (정량) / risk_factors / policy_citation / 근거 공고 추가
- scope="all" → "decisions" auto fallback (Claude.ai timeout 60s 위험)
- NO_DATA 분기 추가 (데이터 없을 때 자동 FOR/REVIEW 금지)
- fin_year = target_year - 2 (주총 N년 → FY(N-2) 안정 데이터)
- 후보 평가 framework 4 dimension caveat + per-candidate raw (main_job / 추천사유 / 경력 / audit_history)

본 ralph는 framework dimension의 **자동화 + 측정**을 목표로 한다. 단순 표시는 됐으나:
- 신임/연임이 agenda_action="선임"으로만 표시됨 → 실제 신임/연임 자동 detect 필요 (career_company_groups에서 이 회사 첫 등장 비교)
- 1번 안건 (재무제표 승인) 본문에 FY25 손익/대차 요약이 첨부되지만 미파싱 (현재 사업보고서 FY24만 사용)
- 연임 후보의 "재직 중 회사 운영 성과" 매핑 X (financial_metrics yoy 등 활용 가능)
- 전문성 평가 자동화 X (recommendation_reason_raw keyword 분석 가능)
- NO_DATA가 진짜 데이터 없을 때만 발동하는지 검증 안 됨 (false-positive 위험)

## 가정 (이전 ralph와 동일)
- No current conversation context
- No web search
- MCP only
- as if it's the first question
- deterministic (temperature=0)

## 매 iteration 작업
1. 현황 확인: git status + 직전 검증 csv + audit
2. 다음 1 step만 진행 (작게 쪼갬)
3. fix 검증: KOSPI200 표본 50-100 회사 spot 측정
4. commit (의미 있는 변경마다)
5. 다음 iteration 1줄

---

## 성공 기준 (모두 충족 시 promise)

### G1. Framework 4 dimension 노출률 ≥90%

KOSPI200 표본 100 회사 × 평균 5 후보 (≈500 후보) 측정.

각 후보마다 다음 4 dimension이 render에 노출되는 비율:
- 결격사유 (disqualification.summary) — 이미 100% 가까이 충족 예상
- 독립성 (independence.summary)
- 전문성 (recommendation_reason_raw — non-empty 또는 main_job non-empty)
- 과거 행적 (career_company_groups 1+ entry 또는 audit_history red flag 발견)

target: 4 dimension 모두 noflicker = 정확히 노출된 후보 비율 ≥90%.

### G2. NO_DATA false-positive ≤5%

agenda_decisions 중 decision="NO_DATA"로 반환된 안건이 진짜로 upstream 데이터 없는지 audit.

false-positive: NO_DATA 반환했지만 실제로 facts 추출 가능 / 정상 분류 가능했던 case (예: financial_metrics가 정상 응답인데 요청 year mismatch로 빈 값).

target: NO_DATA 안건 100건 중 false-positive ≤5건.

### G3. 신임/연임 auto detect 정확도 ≥85%

신임 vs 연임 자동 분류 logic 추가:
- 신임 (new): career_company_groups에 이 회사 entry 없음 OR 이 회사 entry가 모두 미래 시작 (예: "2026~현재")
- 연임 (renewed): 이 회사 entry가 과거~현재 또는 과거~과거 형태로 존재

50 회사 × 평균 3-5 후보 표본 manual label과 auto detect 일치율 ≥85%.

facts에 추가: `appointment_type` ("new" / "renewed" / "ambiguous").

### G4. 1번 안건 (재무제표 승인) FY 본문 raw 추출 ≥80%

shareholder_meeting agenda 첫 안건 본문 (보통 첨부 표 형태)에서 다음 추출:
- 당기 (FY25) net income
- 당기 자산총계 / 부채총계
- 전기 비교

표본 100 회사 중 FY 본문 raw 1+ 항목 추출 성공률 ≥80%.

facts (financial_statements 카테고리)에 추가: `fy_current_net_income_krw` / `fy_current_assets_krw` 등 (raw_from_agenda 표시).

### G5. 연임 후보 재직 중 성과 매핑 (옵션, 시도)

연임 (G3) detect된 후보에 대해, 재직 시작 ~ 현재 기간의 financial_metrics yoy 변화 매핑:
- 매출 / 영업이익 변화율
- 부채비율 변화

facts에 `tenure_period_performance` (dict) 추가. 표본 적용률 ≥50% 도달이면 OK.

---

## 영향 범위

- `open_proxy_mcp/services/proxy_advise.py`: _extract_facts 확장 (appointment_type / fy_raw / tenure perf)
- `open_proxy_mcp/services/director_evaluation.py`: appointment_type detect logic
- `open_proxy_mcp/services/shareholder_meeting.py` 또는 신규 `agenda_first_item_parser`: 1번 안건 FY raw 추출
- `open_proxy_mcp/tools_v2/proxy_advise_before_meeting.py`: render 보강
- `wiki/architecture/audits/data/`: 검증 csv 결과 archive

## 비목표 (이번 ralph X)

- proxy_result_after_meeting 보강 (별도)
- vote_style 정책 변경 / G2 (정확도) 재최적화 — 이미 99.36% 충족, 본 ralph는 정확도가 아닌 dimension 노출률 + 데이터 정직성 측정
- 운용사별 정책 비교 보강 — 별도 ralph

## archive 폴더

`wiki/architecture/audits/data/260504_proxy_advise_framework/` 에 매 iter 검증 csv + 실패 sample 보존.
