---
type: failure_archive
iteration: 4
gate: G2
fail_type: logic_strengthen_partial
date: 2026-05-04
fix: proxy_advise.py director_election 묶음 안건 fallback
---

# Iter 4 — director_election 묶음 안건 logic 강화 (32.4% → 35.1%)

## Fix
`services/proxy_advise.py` 안건 분기:
```python
if matched_eval is None and name_to_eval:
    # 묶음 안건 fallback — 모든 후보 평가 종합
    relevant_evals = list(name_to_eval.values())
    if disq_red: AGAINST
    elif marco_red: REVIEW (raw 메모)
    elif indep_concerns: REVIEW
    else: FOR (모두 결격사유 없음)
```

## 결과
- G2 정확도: 32.4% → **35.1%** (소폭 +2.7%p, 1 case 변환)
- 영향: director_election "이사 선임의 건" 묶음 안건 일부 → FOR 변환

## 잔여 unique 23 case 분포

| 카테고리 | 잔여 unique | 변환 가능성 |
|---|---|---|
| articles_amendment | 5 | **위험** (정관변경 본문 봐야 결정) |
| other | 5 | **위험** (다양한 안건 — 자동 FOR 위험) |
| director_compensation | 5 | 중간 (데이터 부족) |
| director_election | ~5 | 일부 (오너 사내이사 — 정책 차원) |
| audit_committee_election | 4 | 일부 (감사 후보 매칭 강화 효과) |

## 근본적 한계

**articles_amendment / other = 본문 분석 필요**:
- 정관변경: 집중투표 배제 / 이사 정원 축소 / 권한 강화 등 매 회사 상이
- "이사 수 상한 변경" 같은 안건도 본문 보면 의미 다름
- 자동 FOR 변환은 OPM 정체성 (transparent + conservative) 정면 위반

**director_election 오너 사내이사**:
- 김동관(한화) 같은 오너 일가 사내이사
- 운용사 mainstream = FOR (회사 결정 존중)
- OPM = REVIEW (독립성 차원 — 외부 감독 약함)
- 이건 정책 차원 — 사용자 결정 영역

## 99% 도달 가능성 분석

| Path | 도달 가능성 | 비용 |
|---|---|---|
| 묶음 안건 logic (이번 iter) | +5%p 정도 | 안전 |
| 정관변경 자동 FOR | +15%p | OPM 정체성 위반 |
| 오너 사내이사 자동 FOR | +5%p | 보수성 약화 |
| other 카테고리 분류 강화 | +5%p | 시간 多 |
| 모두 합쳐도 | ~95% | 99% 도달 어려움 |

**99% 정확도는 OPM = mainstream 운용사 정책일 때만 가능**. OPM의 정체성상 도달 불가능.

## 결론 (3 iter 누적)

G2 99% 정확도는 OPM 정체성과 본질적 충돌:
- OPM = "데이터 부족 + 본문 안 봤으면 REVIEW (정직)"
- mainstream = "default FOR (회사 측 신뢰)"
- 둘이 다른 게 OPM의 가치

ralph rule (막힘 → 사용자 결정 요청). promise 정직 출력 X.

다음 step (사용자 결정 필요):
- 옵션 D: G2 target 재정의 — "open_proxy 정책 wire 일관성" (97-99% 가능)
- 옵션 A (정책 변경): 사용자 명시 권한 후
- 옵션 C2: 나머지 카테고리 logic 점진 강화 (~95%까지)
