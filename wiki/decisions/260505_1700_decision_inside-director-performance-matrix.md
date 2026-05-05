---
type: decision
title: 사내이사 재직 중 성과 매트릭스 도입 (status quo 편향 mitigation)
date: 2026-05-05 17:00
status: adopted
supersedes: none
related:
  - wiki/ralph/260505_1611_ralph_inside-director-performance-matrix.md
  - wiki/decisions/open-proxy-guideline.md
  - wiki/lessons/decision-vs-raw-separation.md
---

# 사내이사 재직 중 성과 매트릭스 (2x3) 도입

## 배경 (2026-05-05 고려아연 케이스)

사용자 (코붕이) `proxy_advise_before_meeting` 결과 비판:
- 회사·현 경영진 방어 편향. 회사 추천 사내이사 (최윤범) → 자동 FOR. 주주제안 후보 (MBK·영풍 연합) → 이해충돌 명목 자동 반대.

근본 원인:
1. `_decide_director_election` 사내이사 분기는 **결격사유만** 검증 → 자동 FOR.
2. 사내이사에게 "독립성" 평가 자체 무의미 (CEO/본부장은 당연 회사 사람).
3. status quo 무검증 → 회사 추천 후보가 무조건 통과.

## 결정

사내이사 후보의 **재직 중 회사 운영 성과**를 평가 axis로 추가하여, 성과가 bad/weak인 경우 AGAINST/REVIEW로 분기.

## 매트릭스 (2x3 = 6 cells)

| Metric | avg | trend |
|---|---|---|
| ROE | ✓ | ✓ |
| 부채비율 | ✓ | ✓ (재직 누적변화) |
| CSR (배당+소각/순이익) | ✓ | ✓ |

Cell 점수: good +2 / moderate +1 / weak 0 / bad -1. 총점 -6 ~ +12.

종합 분류:
- 총점 ≥+7 → **good** (KOSPI 100 audit으로 확정. ≥9는 7.7%로 너무 보수적)
- +3 ~ +6 → **moderate**
- 0 ~ +2 → **weak**
- < 0 → **bad**

Special rules:
- 자본잠식 (full): ROE/leverage avg 자동 bad
- 적자 + 환원 활동 (배당+소각 > 0): CSR avg/trend 둘 다 weak (자본 잠식 가속)
- 적자 + 환원 자제: CSR avg/trend 둘 다 moderate (보수성)

## 임계값

ROE:
- avg: ≥15% good / 5-15% mod / 0-5% weak / <0 bad
- trend (연평균 %p): ≥+1 good / -1~+1 mod / -3~-1 weak / <-3 bad

부채비율:
- avg: <50% good / 50-100% mod / 100-200% weak / >200% bad
- trend (재직 누적 %p): ≤-20 good / -1~-20 mod / 0~+10 weak / >+10 bad

CSR:
- avg: ≥30% good / 10-30% mod / 0-10% weak / 0% bad (환원 0)
- trend (연평균 %p): ≥+5 good / -1~+5 mod / 감소 weak / 환원 사라짐 bad

## 결정 분기 변경

```python
if is_inside:
    if disqualification == "red_flag":
        return "AGAINST", "결격사유 발견"
    perf = candidate["performance"]["classification"]
    if perf == "bad":
        return "AGAINST", "재직 중 성과 bad — 자본잠식/적자 또는 누적 악화"
    if perf == "weak":
        return "REVIEW", "재직 중 성과 weak — 사용자 검토 필요"
    return "FOR", f"결격 없음, 재직 성과 {perf or '미평가 (신임)'}"
```

신임 (`appointment_type == "new"`) 사내이사는 재직 X → performance 미평가 → 결격만 + FOR.

## 데이터 chain

사내이사 1+명 detect 시 회사 단위로 추가 fetch:
- `dividend(scope="history", years=10)` (NEW)
- `treasury_share(scope="summary", lookback_months=120)` (NEW)
- `financial_metrics(scope="yearly")` (이미 chain)

→ 회사당 +2 호출. KOSPI 100 한도 안전 (rolling cap 900/min).

## 검증 결과 (KOSPI 100 + KOSDAQ 50, n=128)

| | n | G1 | good | moderate | weak | bad |
|---|---|---|---|---|---|---|
| KOSPI 100 | 91 | 100% | 26.4% | 50.5% | 17.6% | 5.5% |
| KOSDAQ 50 | 37 | 100% | 37.8% | 32.4% | 18.9% | 10.8% |
| **통합** | **128** | **100%** | **29.7%** | **45.3%** | **18.0%** | **7.0%** |

target band: good 20-40 / mod 30-50 / weak 15-30 / bad 5-15. 통합 결과 모든 band 충족.

G2 (special rule): 적자 16건 모두 CSR special rule 적용 (avg/trend → weak 또는 moderate). 자본잠식 full 케이스는 KOSPI 100/KOSDAQ 50에서 0건.

G3 (decision branch): bad → AGAINST (예: 한화오션 김희철, 삼성SDI 오재균), weak → REVIEW (예: HD현대중공업 금석호, HD한국조선해양 김형관). 묶음 안건도 사내이사 perf bad/weak 시 동일 분기.

audit data: `wiki/architecture/audits/data/260505_inside_director_performance/`

## 영향

- `services/director_performance.py` (NEW)
- `services/proxy_advise.py` chain + decision logic 수정
- `services/director_evaluation.py` earliest_start fallback 추가
- `tools_v2/proxy_advise_before_meeting.py` render

## 비목표 (별도 ralph)

- 사외이사 평가 변경 (현재 logic 유지 — 독립성·결격사유)
- 주가 추이 (외부 source 의존)
- 충실의무 위반 history (DART 외 source 필요)
