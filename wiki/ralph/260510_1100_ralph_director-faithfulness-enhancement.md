---
type: ralph
title: 사외이사 충실성 강화 — 겸직 카운트 + 사내이사 독립성 표기 정정
created: 2026-05-10 11:00
completion_promise: DIRECTOR_FAITHFULNESS_ENHANCED
max_iterations: 6
ref:
  - open_proxy_mcp/services/director_evaluation.py
  - open_proxy_mcp/services/proxy_advise.py
related_decisions: [260510_1130_decision_director-faithfulness]
related_lessons: [director-faithfulness-260510]
related_audits: [architecture/audits/data/260510_director_faithfulness/iter1_findings]
---

## Invoke

특수문자 사용 금지. 한글로 풀어쓰기.

```
/ralph-loop:ralph-loop wiki/ralph/260510_1100_ralph_director-faithfulness-enhancement.md 가이드 따라. 사외이사 겸직 2개 이상 우려 신호 추가. 사내이사 독립성 표기 정정. 단위 검증 + 510 회사 회귀. --completion-promise DIRECTOR_FAITHFULNESS_ENHANCED --max-iterations 6
```

# Ralph 9: 사외이사 충실성 강화 + 사내이사 독립성 표기 정정

## Context

메리츠금융지주 proxy_advise 응답 검토 시 사용자 피드백:

1. **사내이사 독립성 "충족" 표시 부적절** — 김용범 사내이사가 "독립성 충족"으로 노출. 사내이사는 정의상 비독립이라 마치 독립이라고 오인 가능. 다른 용어 필요 (단 필드는 유지).

2. **충실성 항목 강화 필요**:
   - 연임 + 성과 (이미 `performance.classification` 있음, 명시 약함)
   - 사외이사/독립이사 **겸직 2개 이상 우려** (본업 + 1 사외이사 OK, 2+ 사외이사 = concerns)
   - 최대주주 특수관계인은 독립성에 유지 (충실성 X)

## 가정

- DART API `careerDetails`에 현직 사외이사 정보 추출 가능성 검증 필요
- 사외이사 ≥ 2개 = concerns 신호 (본 회사 1 + 다른 회사 1+ = 2+)
- 사내이사 독립성 표기는 필드 유지 + 용어 변경 ("해당 없음"이 아닌 다른 명시적 표현)

## 핵심 design

### 1. 사외이사 겸직 카운트 (충실성 신규 항목)

```python
def count_concurrent_outside_director_positions(candidate):
    """현재 다른 회사 사외이사 직책 갯수 (본 회사 제외).
    careerDetails 중 '현재' / '현직' 마커 + '사외이사' / '독립이사' 직책 검출.
    """
    n = 0
    for cd in candidate.get("careerDetails") or []:
        content = cd.get("content", "") or ""
        if any(k in content for k in ("현재", "현직", "재직")):
            if any(k in content for k in ("사외이사", "독립이사", "사외 이사")):
                n += 1
    return n
```

판단:
- n == 0: 본 회사만 (정상)
- n >= 1: 다른 회사 사외이사 1개 (이미 본 회사 + 1) → **concerns**

### 2. 사내이사 독립성 표기 정정

용어 후보 (사용자 결정 필요):
- (A) "독립성 평가 비대상 (사내이사)" — 명시적 + 톤 중립
- (B) "비독립 (사내이사)" — 짧지만 negative 톤
- (C) "사내이사 (N/A)" — 짧음
- (D) "사내이사" 단독 + 별도 필드 비움

`_extract_facts` (proxy_advise.py 1159~1191)에서 role_type "사내" check → independence summary 대신 결정된 용어 노출.

### 3. 충실성 응답 표기 강화

기존 `audit_history_check` / `performance` 명시적 노출. 메리츠금융지주 응답에는 충실성 표시 약함. facts에 추가:
- `performance` (renewed 후보 한정)
- `audit_history_check` (모든 후보)
- **신규**: `concurrent_outside_positions` (사외이사 한정)

## 성공 기준

### G1. 겸직 카운트 데이터 정확도

5+ 회사 sample (메리츠금융지주 + KOSPI 4) careerDetails parsing → 현직 사외이사 갯수 추출 정확도 측정. raw vs 추출값 비교.

### G2. 사외이사 겸직 ≥ 2 = concerns 신호

- candidate evaluation에 신호 추가
- proxy_advise reason에 노출
- 사외이사 한정 적용 (사내이사 X)

### G3. 사내이사 독립성 용어 정정

- 사용자 결정 용어 적용
- _extract_facts에 role_type 분기

### G4. 510 회사 회귀

- 기존 hits / decisions 변경 0
- 신규 충실성 항목 추가 (응답 풍부도 ↑)

### G5. 메리츠금융지주 단위 검증

- 김용범 사내이사 독립성 표기 정정
- 사외이사 4명 (조홍희/김우진/김연미/김명애) 겸직 카운트 + 충실성 표시

## 작업 plan (6 iter)

### iter 1 — 겸직 카운트 데이터 정확도 spot
- 메리츠금융지주 + KOSPI 4 회사 careerDetails raw 받기
- 현직 사외이사 키워드 패턴 catalog
- 추출 정확도 측정 (raw 수동 검증 vs 추출값)

### iter 2 — 겸직 카운트 logic 구현
- `evaluate_faithfulness` 또는 `evaluate_faithfulness_basic`에 추가
- 사외이사 한정 적용
- ≥ 2 = concerns / ≥ 3 = strong_concerns

### iter 3 — 사내이사 독립성 용어 정정
- 사용자 결정 용어 적용
- `_extract_facts` (proxy_advise.py) role_type 분기
- 단위 검증

### iter 4 — 메리츠금융지주 단위 검증
- proxy_advise 호출 후 5명 후보 응답 확인
- 김용범 사내이사 독립성 정정
- 사외이사 4명 겸직 카운트

### iter 5 — 510 회사 회귀
- 기존 decisions 변경 0
- 신규 응답 풍부도 측정

### iter 6 — 문서화 + promise
- lesson + decision

## 영향 범위

- `open_proxy_mcp/services/director_evaluation.py` — 겸직 카운트 헬퍼 + faithfulness 통합
- `open_proxy_mcp/services/proxy_advise.py` — `_extract_facts` 사내이사 분기 + 신규 facts 노출
- `wiki/lessons/director-faithfulness-260510.md` — lesson
- `wiki/decisions/260510_xxxx_decision_director-faithfulness.md` — decision

## 비목표

- 최대주주 특수관계인 → 독립성에 유지 (충실성으로 재배치 X)
- 결격사유 4축 변경 X
- 후보 평가 framework 전면 개편 X (기존 3축 — 독립성/충실성/결격사유 유지)

## 사용자 결정 필요

1. 사내이사 독립성 용어:
   - (A) "독립성 평가 비대상 (사내이사)"
   - (B) "비독립 (사내이사)"
   - (C) "사내이사 (N/A)"
   - (D) 다른 안

2. 겸직 임계값:
   - n >= 1 다른 사외이사 → concerns (본 + 1 = 총 2개)
   - 또는 n >= 2 다른 사외이사 → concerns (본 + 2 = 총 3개)

## archive

`wiki/architecture/audits/data/260510_director_faithfulness/`

---

## iteration log

### iter 1 — ✅ 완료 510 회사 careerDetails audit
사외이사 후보 798 / careerDetails 98.4% 채워짐 / 겸직 신호 회사 19.6%. 단순 키워드 카운트는 false positive (본 회사 사외이사 표기 케이스).

### iter 2 — ✅ 완료 logic v3 정확 카운트
본 회사명 매칭 + 후보 본인 보장. 510 회사 v3: concerns 64 / strong 13 (false positive 32 회사 제거).

### iter 3 — ✅ 완료 코드 구현 + 사내이사 독립성 정정
- `count_outside_director_positions` 헬퍼 + faithfulness 통합
- `_is_outside_director_role` + 사내이사 "독립성 평가 비대상 (사내이사)" 표기
- _extract_facts에 concurrent_outside_positions / concurrent_summary 노출

### iter 4 — ✅ 완료 단위 검증 (메리츠금융지주 + 삼성바이오 + LG에너지)
- 김용범 (사내) → "독립성 평가 비대상" ✓
- 김정연 (사외, 삼성바이오) → 겸직 3 strong ✓
- 박진규 (사외, LG에너지) → 겸직 2 concerns ✓
- 조홍희 (사외, 메리츠) → 겸직 1 single ✓

### iter 5 — ✅ 완료 510 회사 회귀
iter 2 v3 결과에 통합. decision 변경 0 (audit_history_check만 활용 유지). facts 신규 노출만 추가. concerns 13.3% / strong 2.7% 후보.

### iter 6 — ✅ 완료 문서화 + promise
- lesson: ✅ wiki/lessons/director-faithfulness-260510.md
- decision: ✅ wiki/decisions/260510_1130_decision_director-faithfulness.md
- audit: ✅ iter1_findings (iter1 + iter2 통합)
- promise: DIRECTOR_FAITHFULNESS_ENHANCED ✅
