---
type: failure_archive
iteration: 4
date: 2026-05-04
fields_target: ≥95% all 8 / careerDetails empty ≤10%
result: 7/8 fields ≥95%, 1 (career_period) 88.7% (-6.3%p) — parser 한계
---

# Iter 1-4 Status — role 100% 회복, career_period 한계

## 8 필드 baseline (300 회사 / 690 candidates)

| 필드 | baseline | iter4 fix 후 | target |
|---|---|---|---|
| name | 100.0% | 100.0% | ≥95% ✅ |
| birth | 99.1% | 99.1% | ≥95% ✅ |
| **role** | 88.7% | **100.0%** | ≥95% ✅ (+11.3%p) |
| career | 95.1% | 95.1% | ≥95% ✅ |
| career_period | 88.7% | 88.7% | ≥95% ❌ (-6.3%p) |
| careergroup | 95.1% | 95.1% | ≥95% ✅ |
| careerDetails empty | 4.9% | 4.9% | ≤10% ✅ |

→ **7/8 필드 충족, 1 (career_period) 미달**.

## Iter 1 (baseline 측정)

`/tmp/parse_personnel_baseline.py` 작성 + 300 회사 sample.

**fail 회사 79건** (role + period 미달). 30 회사 detail 분석:

### role 분포 (94 candidates from 30 fail)
- 정상 (사외/사내/감사/비상무): 42 (45%)
- **None 33** (35%)
- **노이즈 18** (19%): "해당없음/-/부/여/미해당/해당" — header 값을 cell value로 잘못 추출
- 변형 3 (3%): "사내이사 후보자(재선임)" — normalize 필요

### period 분포 (110)
- RANGE 정상 49 (44.5%)
- EMPTY 21 (19%)
- NO_CAREER 11 (10%)
- YEAR_ONLY 11 (10%) — "1993" 단일 연도
- OTHER 4 (3.6%) — "2020.06" 시작만

## Iter 2-4 fix

### Fix A — role 추출 강화 (`parse_personnel_xml`)
- `_normalize_role_value(v)` helper 신규
  - 노이즈 set: ('-', '_', '해당없음', '미해당', '비해당', '해당안됨', '해당', '부', '무', '여', '유', 'X', 'x', 'N', 'O', 'Y') → None
  - 표기 표준화: "사외이사 후보자(재선임)" → "사외이사" 등
- header 매칭 폭넓게: '이사구분' / '직위' / '구분' / '직책' 추가
- roleType None or 노이즈 시 → **안건 title에서 category fallback** (`_CATEGORY_MAP` 활용)
- 결과: role 88.7% → **100.0%** ✅ +11.3%p

### Fix B — period 단일 연도 normalize (`_clean_career_details`)
- "1993" / "2020.06" (시작만 명시) → "1993 ~ 현재" normalize
- 1950-2030 범위 + range 표시 ("~/-") 없을 때만
- 결과: career_period 88.7% → **88.7%** (효과 없음)
- 이유: measurement는 truthy period 카운트. 단일 연도 케이스도 이미 truthy. 진짜 fail (78건)은 본문에 period 자체 X — **parser 한계가 아닌 데이터 한계**.

## Iter 3 regression bug
- 첫 fix 시도에서 `category` 변수 NameError → fetch_or_parse_error 101 발생
- iter 4에서 `_CATEGORY_MAP` from title으로 수정 — 정상 회복

## 결론

- 6/7 필드 ≥95% target 충족 ✅
- careerDetails empty ≤10% 충족 ✅
- **career_period 88.7% — 본문 정보 X case 존재 (parser 한계 X)**
- promise 정직 X (target strict 95%일 때)

## 다음 fix 후보 (별도 작업)

- content에서 연도 추출 → period 자동 채움 (예: "한화 사장 (2018-2024)" content → period 추출)
- 본문 다른 section (이력 / 약력)에서 시작 연도 보완
- 또는 target 90% relax (정보 X 본문 제외)
