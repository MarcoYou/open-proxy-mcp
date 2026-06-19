---
type: failure_archive
iteration: 7 (max 5 초과 — stop hook 무한 loop)
date: 2026-05-04
result: career_period 89.0% 한계 확인 — 본문 정보 X case 회수 불가능
promise: PARSE_PERSONNEL_VERIFIED 출력 X (정직 — 7 iter 누적 검증)
---

# Iter 7 — 데이터 한계 확정 + Promise 정직 X

## 검증 logic

15 fail companies / 78 fail 후보 entries 중 후보 이름 근처 (±1000자) HTML year 검색:
- HTML에 year 있음: 22 (85%) — 그러나 **모두 다른 컨텍스트** (안건 list, 보고서 작성일, 회사 정보)
- HTML에도 year 없음: 4 (15%)

→ 본문에서 *후보 careerDetails 영역*의 year만 의미 있음. 다른 위치 year는 활용 불가.

## Sample (parser miss로 보였지만 실제론 데이터 X)

### 데이타솔루션 박기재
HTML ctx의 year `2024` = 보고서 안건 list "제5호 의안" 등 — 후보 경력 X.
박기재의 careerDetails entries 본문엔 시작 연도 없음.

### 나눔테크 김영근
HTML ctx의 year `2025` = 회사 전화번호/페이지 metadata — 후보 경력 X.
"(주)포스코엠텍 감사팀장(주)나눔테크 전무이사" 같은 단순 직책+회사 — 연도 X.

### 지엔코 최미혜
HTML ctx의 year `2025` = 정관 변경 / 의안 list — 후보 경력 X.

## 7 iter 누적 결과

| 필드 | iter1 | iter4 | iter6 | target |
|---|---|---|---|---|
| name | 100.0% | 100.0% | 100.0% | ≥95% ✅ |
| birth | 99.1% | 99.1% | 99.1% | ≥95% ✅ |
| role | 88.7% | **100.0%** | 100.0% | ≥95% ✅ |
| career | 95.1% | 95.1% | 95.1% | ≥95% ✅ |
| career_period | 88.7% | 88.7% | **89.0%** | ≥95% ❌ -6%p |
| careergroup | 95.1% | 95.1% | 95.1% | ≥95% ✅ |
| careerDetails empty | 4.9% | 4.9% | 4.9% | ≤10% ✅ |

## 결론 (정직 final)

**Promise PARSE_PERSONNEL_VERIFIED 출력 X**:
- 7 iter 누적 fix:
  - iter4 role +11.3%p ✅
  - iter6 content year +0.3%p (marginal)
- career_period 89.0% — **데이터 한계 확정** (본문 정보 X case 다수)
- 추가 iter 진행해도 95% 도달 불가능
- ralph rule strict 적용 시 정직 X

## 사용자 결정 필요 (ralph 종료 위해)

- **Option A**: target relax — career_period 90% 또는 "본문 정보 X 제외 95%+"
- **Option B**: 별도 ralph (본문 다른 section 보완 — 시간 多)
- **Option C**: ralph 자체 cancel (`/ralph-loop:cancel-ralph`) + 작업 종료

iter 7 도달 (max 5 사용자 설정). stop hook 무한 loop 막으려면 사용자 cancel 또는 promise 충족 (불가능).

→ **정직 권고: Option C (cancel ralph + 종료)**.
