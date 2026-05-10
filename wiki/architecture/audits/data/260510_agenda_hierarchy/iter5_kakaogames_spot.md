---
type: audit
title: 카카오게임즈 D 패턴 X 케이스 spot — sub→amendment 매핑 분석
date: 2026-05-10
related:
  - wiki/ralph/260510_0823_ralph_agenda-hierarchy-body-fallback.md
  - wiki/lessons/agenda-hierarchy-260510.md
related_ralph: [260510_0823_ralph_agenda-hierarchy-body-fallback]
related_lessons: [agenda-hierarchy-260510]
related_decisions: [260510_0900_decision_d-pattern-body-fallback]
---

# 카카오게임즈 spot — Ralph 7 범위 외 케이스 분석

## 안건 트리

```
- 제2호 정관 일부 변경의 건 (children 2)
  - 제2-1호 주주총회 기준일 변경의 건 (children 0, sub title 일반)
  - 제2-2호 개정 상법 반영의 건       (children 0, sub title 일반)
```

→ Ralph 7 D 패턴 정의 (top + children 0)에 맞지 않음. parent (제2호) children > 0 → fallback 진입 X.

## amendments (raw 정확)

| label | reason | catch 가능 룰 |
|---|---|---|
| 제13조의3 | 전자등록 도입 / 명의개서정지기간 / 기준일 | 명확 catch 룰 없음 (B 영역) |
| 제20조 | 상법 제542조의14, 제542조의15 / 전자주주총회 | A1-7 (전자주총 도입) |

## 진입 조건 확장 시뮬레이션

조건: parent에 "정관" + 자기 children 0 + 자기 title에 "정관" 없음

```
sub: 주주총회 기준일 변경의 건 → body 일괄 검사 → A1-7 hit
sub: 개정 상법 반영의 건       → body 일괄 검사 → A1-7 hit (cross-match!)
```

문제:
- 두 sub 모두 같은 amendments 일괄 검사로 A1-7 hit
- 실제로는 제20조 amendment (전자주총) 1건만 → A1-7 1번 hit가 맞음
- sub 단위 진입 추가 시 **double counting** 발생

## 진정한 catch 방안 — sub → amendment 1:1 매핑

### 방안 A: label/clause 키워드 기반 매핑

```python
sub: "주주총회 기준일 변경의 건"
  → amendment[0] (label 제13조의3 + reason "기준일") 매핑 ✓
  → amendment[0] body로만 _law_layer 호출

sub: "개정 상법 반영의 건"
  → amendment[1] (label 제20조 + reason "전자주주총회") 매핑?
  → "개정 상법 반영" 키워드와 amendment 본문 fuzzy 매칭 — 너무 generic, 매칭 보장 어려움
```

### 방안 B: amendment 갯수 == sub 갯수 + 순서 매핑

카카오게임즈는 amendments 2 / sub 2 → 순서대로 매핑 가정.
단 다른 회사에서 깨질 수 있음 (amendments 갯수 != sub 갯수).

### 방안 C: 모든 sub × 모든 amendment 매트릭스 매칭 + label fuzzy score

각 (sub, amendment) 쌍에 대해 label/clause/reason 키워드 overlap score → 최고 score 매핑.
단 generic sub title ("개정 상법 반영")은 score 낮음 → 미매칭.

## Ralph 7 범위 외 결정

- 카카오게임즈는 sub-agenda 있지만 sub title이 너무 일반 표현 ("개정 상법 반영")
- sub→amendment 매핑 logic이 fuzzy 매칭 의존 → false positive 위험
- 본 ralph (D 패턴 한정 안전 fallback)에 맞지 않음
- **별도 architect ralph 후보**

## 별도 ralph 후보 outline

**title**: sub-agenda 일반 표현 회사 — sub→amendment 1:1 매핑 logic

**가설**:
- amendment label/clause 정관 조항 번호 기반 (제N조 또는 제N조의M)
- sub-agenda title의 키워드 (기준일 / 보수 / 임기 / 명칭 / 전자 등) → amendment label 매핑
- generic title ("개정 상법 반영" / "기타") 케이스: amendment 통합 검사 + parent 단위 1번 적용

**위험**:
- fuzzy 매칭 false positive (generic title)
- amendment 갯수 != sub 갯수 케이스 처리

**검증 방안**:
- 카카오게임즈 + 다른 sub 일반 표현 회사 sample 식별 (510 spot data 활용)
- 매핑 정확도 측정
- 회귀 검증

## 510 spot data에서 카카오게임즈 같은 케이스 식별

`iter4_spot_*.json` records 중 정관변경 top + children 2+ + sub title에 "정관" 없음 + body fallback 미진입 회사 list. (별도 ralph iter 1)
