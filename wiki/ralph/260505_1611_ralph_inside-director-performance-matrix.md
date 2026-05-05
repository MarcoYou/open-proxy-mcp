---
type: ralph
title: 사내이사 재직 중 성과 매트릭스 (2x3) — proxy_advise status quo 편향 mitigation
created: 2026-05-05 16:11
completion_promise: INSIDE_DIRECTOR_PERFORMANCE_VERIFIED
max_iterations: 12
---

## Invoke (복붙)

```
/ralph-loop:ralph-loop wiki/ralph/260505_1611_ralph_inside-director-performance-matrix.md 가이드 따라 사내이사 performance 매트릭스 (2x3) 구현 + proxy_advise wire. KOSPI200 표본 50 회사 검증 — 사내이사 평가 결과에 performance classification 노출률 ≥95% + 자본잠식/적자 special rule 정확도 100% + bad/weak 케이스 자동 REVIEW 분기 정확 + 분류 distribution (good/moderate/weak/bad) 합리성 확인 모두 충족 시 promise. --completion-promise INSIDE_DIRECTOR_PERFORMANCE_VERIFIED --max-iterations 12
```

# Ralph: 사내이사 재직 중 성과 매트릭스

## Context

사용자 (코붕이) 비판 (2026-05-05, 고려아연 케이스):
- "open_proxy 결과가 회사·현 경영진을 방어하려는 입장"
- 회사 추천 사내이사 (최윤범) → 자동 FOR
- 주주제안 후보 (MBK·영풍 연합) → 이해충돌 명목 자동 반대
- → status quo 편향 인지

근본 원인:
1. `_decide_director_election` 사내이사 분기 = 결격사유만 검증 → 자동 FOR
2. 사내이사에게 "독립성" 평가 자체 무의미 (CEO/본부장은 당연 회사 사람)
3. 사내이사 평가 axis가 결격 + audit_history만 있어 status quo 무검증

해결: **재직 중 회사 운영 성과**를 사내이사 평가 axis로 추가.

## 가정 (이전 ralph 동일)
- No conversation context / no web search / MCP only / deterministic
- 분당 DART 1000회 한도 hard rule 준수 (rolling window cap 900)

## 매 iteration 작업
1. 현황: git status + 직전 검증 csv
2. 다음 1 step만 진행 (작게 쪼갬)
3. fix 검증: KOSPI200 표본 spot 측정
4. commit (의미 있는 변경마다)
5. 다음 iter 1줄

---

## 성공 기준 (모두 충족 시 promise)

### G1. performance classification 노출률 ≥95%

KOSPI200 표본 50 회사 × 평균 1-3 사내이사 = ~100 사내이사. 각 사내이사에:
- `performance.matrix` (2x3 cell scores)
- `performance.classification` (good/moderate/weak/bad)
- `performance.rationale` (한국어 설명)

3 정보 모두 노출률 ≥95%. 한 정보라도 누락 시 fail.

### G2. 자본잠식/적자 special rule 정확도 100%

- 자본잠식 (full): ROE avg + leverage avg 둘 다 자동 bad (-1)
- 적자 + 환원 활동 (배당+소각 > 0): CSR avg + trend 둘 다 weak (0)
- 적자 + 환원 자제: CSR avg + trend 둘 다 moderate (+1)

표본에서 자본잠식/적자 회사 발견 시 위 룰 정확 적용 ratio = 100%.

### G3. bad/weak 케이스 자동 REVIEW/AGAINST 분기 정확

표본의 사내이사 결정 (`agenda_decisions`):
- performance == "bad" → AGAINST + reason "재직 중 성과 bad"
- performance == "weak" → REVIEW + reason "재직 중 성과 weak"
- performance ∈ {"moderate", "good"} → FOR

분기 정확도 100%.

### G4. 분류 distribution 합리성

KOSPI200 50 회사 사내이사 distribution:
- good: 20-40% 정도
- moderate: 30-50%
- weak: 15-30%
- bad: 5-15% (자본잠식 회사 등)

너무 한쪽 쏠림 (예: 90% good 또는 90% bad) 발견 시 임계값 재조정.

---

## 매트릭스 정의 (2x3 = 6 cells)

| Metric \ View | 평균 (avg) | 트렌드 (trend) |
|---|---|---|
| **ROE** | 평균 점수 | 추세 점수 |
| **부채비율** | 평균 점수 | 추세 점수 |
| **CSR (환원율)** | 평균 점수 | 추세 점수 |

### Cell 점수

| classification | 점수 |
|---|---|
| good | +2 |
| moderate | +1 |
| weak | 0 |
| bad | -1 |

총점 범위: -6 ~ +12.

### 분류 임계값

**ROE**:
| | good (+2) | moderate (+1) | weak (0) | bad (-1) |
|---|---|---|---|---|
| avg | ≥15% | 5-15% | 0-5% | <0% **또는 자본잠식** |
| trend (8년 누적 변화) | ≥+1%p/년 | -1 ~ +1%p/년 | -1 ~ -3%p/년 | < -3%p/년 |

**부채비율**:
| | good (+2) | moderate (+1) | weak (0) | bad (-1) |
|---|---|---|---|---|
| avg | <50% | 50-100% | 100-200% | >200% **또는 자본잠식** |
| trend (재직 누적) | ≤-20%p (대폭 개선) | -1 ~ -20%p (개선) | 0 ~ +10%p (유지/약화) | > +10%p |

**CSR (배당 + 소각 / 누적 지배주주 순이익)**:
| | good (+2) | moderate (+1) | weak (0) | bad (-1) |
|---|---|---|---|---|
| avg | ≥30% | 10-30% | 0-10% | 0% (배당+소각 모두 0) |
| trend | +5%p/년 이상 | 안정 | 감소 | 환원 자체 사라짐 |
| **special** | — | 적자 + 환원 자제 (둘 다 +1) | **적자에서 환원 (둘 다 0)** | — |

### 종합 분류 (총점 -6 ~ +12)

| 총점 | overall |
|---|---|
| ≥ +9 | **good** |
| +3 ~ +8 | **moderate** |
| 0 ~ +2 | **weak** |
| < 0 | **bad** |

---

## `_decide_director_election` 사내이사 분기 변경

```python
if is_inside:
    if disqualification == "red_flag":
        return "AGAINST", "결격사유 발견"
    perf = candidate.get("performance", {}).get("classification")
    if perf == "bad":
        return "AGAINST", "재직 중 회사 운영 성과 bad (자본잠식/적자 또는 누적 악화)"
    if perf == "weak":
        return "REVIEW", "재직 중 회사 운영 성과 weak — 사용자 검토 필요"
    # moderate / good / 미평가 (신임은 평가 X) → FOR
    return "FOR", f"사내이사 결격 없음, 재직 성과 {perf or '미평가 (신임)'}"
```

신임 (`appointment_type == "new"`) 사내이사는 재직 X → performance 미평가 → 기존 logic (결격만 + FOR).

---

## 데이터 chain

사내이사 1명 평가 추가 호출:

| 데이터 | source | 추가 호출 |
|---|---|---|
| 재직 시작 detect | `appointment_type.earliest_start` (이미 있음) | 0 |
| ROE / 부채비율 yearly | `financial_metrics(scope="yearly")` — 재직 기간 N년 | 1 (이미 chain) |
| 배당 history | `dividend(scope="history")` — 재직 기간 N년 | 1 NEW chain |
| 소각 events | `treasury_share(scope="summary")` — 재직 중 events | 1 NEW chain |
| 누적 순이익 | financial_metrics yearly (이미 fetch) | 0 |

총 추가 ~2 호출/회사 (사내이사 N명이라도 회사 단위 1회 fetch). 분당 한도 안전.

---

## 작업 plan (예상 순서)

### Step 1. performance helper 함수
`services/director_performance.py` 신규:
- `_score_roe_avg(avg, capital_impairment_status) -> int`
- `_score_roe_trend(trend) -> int`
- `_score_leverage_avg(avg, capital_impairment_status) -> int`
- `_score_leverage_trend(trend) -> int`
- `_score_csr(avg, trend, avg_net_income, dividend_total, cancelation_total) -> tuple[int, int]`
- `compute_performance(roe_yearly, leverage_yearly, dividend_history, treasury_events, fin_yearly) -> dict`

### Step 2. proxy_advise wire
- `build_proxy_advise_payload`에 dividend + treasury_share + financial_metrics(yearly) chain 추가
- 사내이사 후보 detect → `compute_performance` 호출 → `candidate["performance"]` 부착
- `_decide_director_election` 사내이사 분기에 performance 평가 추가

### Step 3. render 보강
- `tools_v2/proxy_advise_before_meeting.py`: 후보 detail에 performance matrix + classification + rationale 노출

### Step 4. 검증 harness
`scripts/ralph_inside_director_performance_audit.py`:
- KOSPI200 50 회사 audit
- 사내이사별 performance 추출 + classification distribution
- bad/weak 비율 + special rule 적용률
- G1~G4 측정

### Step 5. 임계값 조정
distribution 결과로 임계값 fine-tune (G4 합리성).

### Step 6. 문서화
- `wiki/decisions/inside-director-performance-matrix.md` (정책 결정)
- `wiki/lessons/` 추가 lesson (status quo 편향 mitigation)

---

## 영향 범위

- `open_proxy_mcp/services/director_performance.py` (NEW)
- `open_proxy_mcp/services/proxy_advise.py` (chain 추가 + decision logic 수정)
- `open_proxy_mcp/tools_v2/proxy_advise_before_meeting.py` (render)
- `wiki/decisions/inside-director-performance-matrix.md` (NEW)
- `wiki/architecture/audits/data/260505_inside_director_performance/` (검증 csv)

## 비목표 (이번 ralph X)

- 사외이사 평가 변경 (현재 logic 유지 — 독립성·결격사유 기준)
- 주가 추이 (외부 source 의존 — 별도 ralph)
- 충실의무 위반 history (DART 외 source 필요)
- 외부 영입 신임 사내이사 detect 정확도 (현재 patternX → 별도 ralph)

## 가설 / 위험

- **위험 1**: 재직 시작 detect 부정확 시 performance 기간 잘못 계산. mitigation — `appointment_type.earliest_start` 신뢰성 검증.
- **위험 2**: financial_metrics yearly가 N년 한 번에 못 가져오면 반복 호출 → DART rate hit. mitigation — 1 호출에 5-10년 yearly 들어가는지 확인.
- **위험 3**: distribution 너무 쏠림. mitigation — Step 5 임계값 조정.
- **위험 4**: 사내이사 결격이 아닌 weak/bad가 너무 많으면 mainstream 운용사와 큰 차이 → G2 99.36% (proxy_advise 기존 검증) regression. mitigation — old vs new decision diff audit.

## archive 폴더

`wiki/architecture/audits/data/260505_inside_director_performance/`
