---
type: failure_archive
iteration: 5 (last, max_iterations=5)
date: 2026-05-04
result: STATUS REPORT — 7/8 필드 충족 + careerDetails empty 충족, 1 필드 (career_period) 미달 88.7% (본문 정보 한계)
promise: PARSE_PERSONNEL_VERIFIED 출력 X (정직)
---

# Iter 5 Final Status — Promise 정직 X

## 결과 요약

| 필드 | 결과 | target | 상태 |
|---|---|---|---|
| name | 100.0% | ≥95% | ✅ |
| birth | 99.1% | ≥95% | ✅ |
| role | **100.0%** | ≥95% | ✅ (iter4 fix +11.3%p) |
| career | 95.1% | ≥95% | ✅ |
| **career_period** | **88.7%** | ≥95% | ❌ -6.3%p |
| careergroup | 95.1% | ≥95% | ✅ |
| careerDetails empty | 4.9% | ≤10% | ✅ |

→ **7/8 필드 충족, 1 필드 (career_period) 미달**.

## career_period 추가 fix 시도 + 결론

**시도**: content에서 year 추출 → period 자동 채움 가능성

**검증 (30 fail companies, 86 period 빈 entries)**:
- content에 year 있음: **4 (4.7%)**
- content에 year 없음: **82 (95.3%)**

→ **fix 효과 미미** (전체 78건 fail 중 ~4건만 회복 가능). 본문 자체에 시작 연도 정보 없는 경력 entry 다수 — **parser 한계 X, 데이터 한계**.

## Sample 정보 X content 예시

- "(주)포스코엠텍 감사팀장(주)나눔테크 전무이사(재무총괄)" — 연도 명시 X
- "오롬컴퓨터 이사" — 연도 X
- 기타 단순 직책+회사명만 명시

DART 본문 파싱 한계가 아닌, 회사가 작성한 본문 자체에 정보 없는 case.

## Promise 평가 (정직)

ralph rule strict 적용:
- 8 필드 모두 ≥95% target 충족 필요
- 1 필드 (career_period 88.7%) 미달 6.3%p
- → **promise 출력 X (정직)**

## 핵심 finding

1. **role 88.7% → 100%** ✅ — iter4 fix 큰 성공
   - 노이즈 ("해당없음/-/부/여/미해당") None 분류 + 표준 표기 + title fallback
2. **career_period 88.7% (parser 한계 X)** — 본문 정보 없는 entry 다수 (95% 회수 불가능)
3. **careerDetails empty 4.9%** ✅ — target ≤10% 큰 마진으로 충족
4. **iter22 birth_date age bug 회귀** ✅ — 99.1% 유지 (정상)

## 다음 작업 (별도 ralph 또는 사용자 결정)

### Option A: target 재정의 (현재 작업 acceptance)
- career_period target 95% → 90% 또는 "본문 정보 X 제외 95%+"
- 재정의 시 모든 필드 충족 → promise OK

### Option B: 추가 parser fix (별도 ralph 1-2시간)
- 본문 다른 section (이력 / 약력) 보완
- content year 추출 + period 자동 채움 (4.7% 회수)
- agenda title 연도 패턴 detect

### Option C: STATUS 그대로 + 다른 작업 (가장 정직)
- 7/8 필드 충족 — proxy_advise G2 99.36%엔 영향 없음
- 잔여 1 필드는 본문 한계 인정
- 별도 ralph 부여하지 않고 종료

권고: **Option C** (정직, 데이터 한계 인정).
