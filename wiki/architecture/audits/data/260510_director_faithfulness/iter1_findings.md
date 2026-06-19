---
type: audit
title: 사외이사 careerDetails 겸직 데이터 가용성 — iter 1
date: 2026-05-10
related:
  - wiki/ralph/260510_1100_ralph_director-faithfulness-enhancement.md
related_ralph: [260510_1100_ralph_director-faithfulness-enhancement]
related_lessons: [director-faithfulness-260510]
related_decisions: [260510_1130_decision_director-faithfulness]
---

# Ralph 9 iter 1 — 510 회사 careerDetails 겸직 데이터 audit

## 결과

| 항목 | 수치 |
|---|---:|
| 사외이사 후보 총 | 797 |
| careerDetails 있는 후보 | 784 (98.4%) |
| '현재' 마커 있는 후보 | 569 |
| '사외이사' 키워드 있는 후보 | 250 |
| **겸직 신호 (현재 + 사외이사 동시) 후보** | **180 (22.6%)** |
| **겸직 신호 있는 회사** | **96 / 490 (19.6%)** |

→ 데이터 가용성 충분. 겸직 자동 카운트 실현 가능.

## false positive 발견 — 본 회사 사외이사 표기

careerDetails에 후보 본인의 본 회사 사외이사 직책이 들어있는 케이스:
- 하나금융지주 박동문: "하나금융지주 사외이사" (본 회사)
- 우리금융지주 윤인섭: "우리금융지주 사외이사" (본 회사)
- 에이피알 노유리: "에이피알 사외이사" (본 회사)
- HD한국조선해양 김홍기: "HD한국조선해양 사외이사" (본 회사)

→ 진짜 겸직 (다른 회사) 식별 위해 본 회사명 매칭 logic 필요.

## logic v3 — 사용자 결정

```
사외이사_총_갯수 = careerDetails 중 "현재 + 사외이사" 카운트
if 본 회사명 careerDetails 표기 X:
    사외이사_총_갯수 += 1  # 후보 본인 본 회사 보장

if 사외이사_총_갯수 >= 2:  # 본 + 다른 회사 1개+
    concerns
```

## 진짜 겸직 sample

| 후보 | careerDetails 표기 | 총 카운트 | 판단 |
|---|---|---:|---|
| 박진규 (LG에너지솔루션) | "롯데이노베이트 사외이사" | 1 + 1 = 2 | concerns |
| 김정연 (삼성바이오로직스) | "한국타이어 + 한화손해보험 사외이사" | 2 + 1 = 3 | strong |
| 황덕남 (고려아연) | "고려아연 + 롯데웰푸드 + 하나은행 사외이사" | 3 | strong |
| 차경진 (카카오) | "신세계아이앤씨 사외이사" | 1 + 1 = 2 | concerns |
| 박광우 (HD현대중공업) | "매일유업 사외이사" | 1 + 1 = 2 | concerns |

## iter 2 — logic v3 510 회사 정확 카운트 (✅ 완료)

| 항목 | 수치 |
|---|---:|
| 사외이사 후보 총 | 815 |
| concerns (≥2개 사외이사) | 108 (13.3%) |
| strong (≥3개) | 22 (2.7%) |
| **concerns 회사** | **64 / 493 (13.0%)** |
| **strong 회사** | **13 / 493 (2.6%)** |

iter 1 단순 키워드 96 회사 → v3 정확 64 회사. **false positive 32 회사 제거**.

## 다음 단계 (iter 3)

- `director_evaluation.py` faithfulness에 겸직 카운트 추가 (n_outside_director_positions)
- `proxy_advise.py` _extract_facts에 노출
- 사내이사 독립성 표기 정정 ("독립성 평가 비대상 (사내이사)")

## archive

- `iter1_concurrent_audit.json` (510 raw)
- `iter2_concurrent_v3.json` (510 v3)
