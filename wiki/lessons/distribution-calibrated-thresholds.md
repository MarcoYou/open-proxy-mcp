---
type: lesson
title: distribution-calibrated thresholds — 임계값은 사전(prior)이 아니라 사후(posterior)
context: 사내이사 performance 매트릭스 ralph (260505)
date_learned: 2026-05-05
---

# distribution-calibrated thresholds

## Context

사내이사 performance 매트릭스를 만들면서 종합 점수 (-6 ~ +12) 의 classification 임계값을 정해야 했다.

직관적 출발: "good = 점수 ≥9 (4-5개 cell이 good)" — 이 사람은 정말 잘했다.
- 결과: KOSPI 100에서 good 7.7%만 도달. moderate 69.2%로 쏠림.
- target band (good 20-40 / mod 30-50 / weak 15-30 / bad 5-15) 명백히 미달.

threshold ≥9 → ≥7로 조정 후:
- good 26.4% / mod 50.5% / weak 17.6% / bad 5.5% — 모든 band 충족.

## Did

- 1차: 임계값 직관 (≥9 good). 매트릭스 구성 시 "이상적" 비율 가정.
- 2차: KOSPI 100 100 회사 audit으로 실제 score distribution 확인 → 점수 5-6에 봉우리, ≥9는 long tail.
- 3차: target distribution band과 실제 분포 비교 → cutoff 재조정 (≥7).
- 4차: KOSDAQ 50 audit으로 cross-validation. KOSDAQ 0-30: good 35.7% / mod 39.3% / weak 14.3% / bad 10.7% — 비슷한 분포 패턴, threshold 일관성 확인.

## Improved

- **classification cutoff은 데이터 본 다음 정함**. 매트릭스 디자인 시 임계값 미정 → audit 결과로 교정.
- target distribution band은 **검증 anchor** 로 활용 (성공 기준의 일부). 이상적 가정이 실제 데이터와 안 맞으면 임계값을 옮긴다.
- code 주석에 "왜 ≥7인지" 기록 (후세를 위한 origin):
  ```python
  if total >= 7:  # KOSPI 100 audit 260505: ≥9는 7.7%로 너무 보수적, ≥7로 26.4%·target 20-40% 충족
      classification = "good"
  ```

## Trade-off

- **사후 calibration의 한계**: 표본 (KOSPI 100) 분포에 fit한 임계값이 미래 데이터 (다른 시기, 코스닥 소형주, 외국 사례)에서도 valid한지 보장 X.
- **순환 정합 위험**: target band 자체가 임의 (good 20-40%가 reasonable한가?). target을 임의로 정한 뒤 임계값을 거기에 맞추면 "분포 합리성"이 자기-증명 (self-fulfilling).
- **mitigation**: target band은 ralph plan 단계에서 **misuse 방지 가드**로만 사용 (90% good 또는 90% bad 같은 비상식 결과 차단). band 안 들어오면 임계값 조정 OR 매트릭스 재설계 — 어느 쪽이 더 옳은지 분리해 판단.

이 case는 임계값 조정이 정당했다 (개별 score=7-8 케이스 — 효성중공업·코웨이·LG씨엔에스·우리금융지주 — 가 직관적으로 "good"에 가까움 → cutoff을 데이터에 맞춰 옮기는 게 맞음).

## Takeaway

- **임계값은 prior (이상)가 아니라 posterior (실측 + 검증)에서 정한다**. 매트릭스/scoring 만들 때 임계값은 placeholder로 두고, 실제 표본 100개+ 굴려 distribution 본 후 확정.
- target distribution band은 **상한/하한 가드**로만 (90/0% 같은 극단 차단). 정확한 값 fit은 임계값 자유 변수.
- code 주석에 "왜 이 cutoff인지 + 어느 audit의 결과인지" 명시. 임의 magic number는 미래에 흔들림.
- 다른 universe (KOSDAQ, 미래 시기) 적용 시 distribution 재측정 필요. cutoff은 sample-specific.

## 관련

- [[ralph-threshold-realism]] (서로 다른 ralph target 비교 — threshold가 데이터 성격에 맞아야)
- 사내이사 performance ralph: `wiki/ralph/260505_1611_ralph_inside-director-performance-matrix.md`
- decision: `wiki/decisions/260505_1700_decision_inside-director-performance-matrix.md`
- audit data: `wiki/architecture/audits/data/260505_inside_director_performance/`
