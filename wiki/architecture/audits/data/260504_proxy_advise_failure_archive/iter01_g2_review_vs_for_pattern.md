---
type: failure_archive
iteration: 1
gate: G2
fail_type: accuracy_vs_majority
sample_size: 10 회사 × 안건 카테고리 = 39 entries
date: 2026-05-04
---

# Iter 1 — G2 정확도 32.4% (target 99% 미달) — REVIEW 보수화 패턴

## 결과
- 10 회사 spot, 39 entries (안건 카테고리별)
- judged 37 (no_data 2 제외)
- **follows_consensus: 12 (32.4%)**
- **unique: 24 (64.9%)** ← 모두 same pattern
- aligns_with_outlier: 1

## Unique 24 case 패턴 (균질)

| our_decision | majority_decision | count |
|---|---|---|
| REVIEW | FOR | **24 (100%)** |

**예시**:
- LS ELECTRIC / articles_amendment: REVIEW vs FOR (14/16)
- 한화에어로스페이스 / articles_amendment: REVIEW vs FOR (71/71)
- 카카오뱅크 / articles_amendment: REVIEW vs FOR (24/24)
- 한화에어로스페이스 / director_election: REVIEW vs FOR (50/52)
- 카카오뱅크 / director_election: REVIEW vs FOR (22/24)

가장 strong: 71/71 운용사 일치 FOR인데 우리는 REVIEW.

## Root cause 분석

OPM `_decide_*` 함수 패턴:
```python
# director_election
if not eval_match:
    return "REVIEW", "후보 평가 데이터 없음 — 사용자 검토 필요"

# director_compensation
if increase_rate is None:
    return "REVIEW", "보수한도 인상률 데이터 없음 — 본문 검토 필요"

# articles_amendment
# default: REVIEW (특정 패턴 외)

# other category
return "REVIEW", "정책 미정의"
```

**OPM logic = "데이터 부족 → REVIEW (사용자 검토)"** — 보수적 transparent 자세.

**운용사 logic = "결격사유 미발견 → FOR (default 회사 측)"** — mainstream 표준.

데이터 issue (parser 실패 / DART 응답 누락)가 결정 logic을 REVIEW로 보냄. 운용사들은 같은 데이터로 FOR 결정.

## 영향 평가

- vote_style="open_proxy" (default OPM) 사용 시 G2 정확도 ≤35%
- 다른 vote_style 사용 시 (m_legacy / s_legacy 등) 정책 default가 다름
- G2 비교 자체가 **vote_style 의존**

## 정책적 옵션 (사용자 결정 필요)

### A. OPM logic 손보기 (open_proxy 정책 자체 변경)
- 데이터 부족 시 default FOR + 명시 caveat
- conservative 자세 포기
- → OPM Guideline 정신 위반 가능성

### B. G2 측정 시 vote_style 동적 선택
- vote_style="m_legacy" 등 mainstream 정책으로 호출
- open_proxy는 별도 sanity (의도적 conservative 보장)
- → G2 의미 = "mainstream 정책 wire 정확성"

### C. data 누락 자체 fix
- parser 보강 / DART fallback 강화 → 데이터 수집률 ↑
- 결정 logic은 그대로 (데이터 있으면 FOR/AGAINST 명확)
- → 근본 해결, 시간 多

### D. G2 target 자체 재정의
- "open_proxy vs majority" 대신 "open_proxy 안 일관성" 측정
- 99% 정확도는 다른 vote_style에 적용
- → ralph md 수정

## 시도한 fallback
- spot 측정만 1회. fix 시도 X.

## 제안 fix (사용자 결정 후)
- B + C 권장:
  - B로 vote_style 별 측정 (즉시 99% 가능 vote_style 찾기)
  - C로 데이터 누락 case 점진 fix
- A는 OPM 정체성 변경이라 신중

## 다음 iter
- B/C 어느 방향으로 갈지 사용자 결정 받은 후 진행
