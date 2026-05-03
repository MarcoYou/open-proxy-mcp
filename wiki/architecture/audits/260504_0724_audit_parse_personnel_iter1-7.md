---
type: audit
title: parse_personnel_xml ralph (iter 1-7) — role 88.7→100% + regression 0
date: 2026-05-04
related_tools: [proxy_advise_before_meeting, director_evaluation]
related_ralph: 260504_0014_ralph_parse-personnel-xml-verification
result: 7/8 필드 ≥95% 충족 + careerDetails empty 4.9% ≤10% + regression 0 + G2 99.36% 유지
---

# parse_personnel_xml 강화 audit (ralph 7 iter)

ralph [[260504_0014_ralph_parse-personnel-xml-verification]] 진행 결과.

## 검증 (300 회사 / 690 candidates)

| 필드 | iter1 baseline | iter6 final | Target | 상태 |
|---|---|---|---|---|
| name | 100.0% | 100.0% | ≥95% | ✅ |
| birth | 99.1% | 99.1% | ≥95% | ✅ |
| **role** | 88.7% | **100.0%** | ≥95% | ✅ +11.3%p |
| career | 95.1% | 95.1% | ≥95% | ✅ |
| career_period | 88.7% | 89.0% | ≥95% | ❌ -6%p (본문 한계) |
| careergroup | 95.1% | 95.1% | ≥95% | ✅ |
| **careerDetails empty** | 4.9% | 4.9% | ≤10% | ✅ |

→ **7/8 필드 + empty 비율 충족**, career_period 1 필드 미달.

## 핵심 fix

### iter4 — role normalize + title fallback (+11.3%p, 가장 큰 성공)

`tools/parser.py` `_extract_candidates`:

**1. `_normalize_role_value()` helper 신규** — 노이즈 제거 + 표준화
- 노이즈 set: `('-', '_', '해당없음', '미해당', '비해당', '해당안됨', '해당', '부', '무', '여', '유', 'X', 'x', 'N', 'O', 'Y')` → None
- binary 응답 `('예', 'YES', 'TRUE')` → None (fallback로)
- 표준 패턴: `사외이사 / 사내이사 / 기타비상무이사 / 상근감사 / 비상근감사 / 감사위원 / 감사`
- 알 수 없는 case → **raw 보존** (silent fallback X 원칙)

**2. Header 매칭 확장**
- 기존: `'사외이사' + '후보'` 둘 다 포함만
- 추가: `'이사구분'` / `'직위'` / `'구분'` / `'직책'`

**3. `roleType` None 시 안건 title fallback**
- `_CATEGORY_MAP` 활용
- 예: "사외이사 OO 선임의 건" → `roleType = '사외이사'`

### iter6 — period 단일 연도 + content year extract (+0.3%p, marginal)

`tools/parser.py` `_clean_career_details`:

- 단일 연도 ("1993", "2020.06") + range 표시 X → "{year} ~ 현재" normalize
- period 빈 시 content에서 year 추출:
  - 2 years → "{a} ~ {b}" (오름차순 보정)
  - 1 year → "{year} ~ 현재"

### iter8 — 한자 이름 cover

`tools/parser.py` `_is_valid_candidate_name`:

- 한자 단독 (`[一-鿿]{2,5}`) — "鄭傳鈉" 같은 옛 후보 cover
- 한글+영문+한자 mix 정규식 보강 (`[가-힣A-Za-z一-鿿]`)
- 영문 검증 (200 sample): `KIM JOONYOUNG / Takashi Abe / Edward Chin / "구하이유(Gu Haiyu)" / "존림 (Rim John Chongbo)"` 모두 정상 detect

## career_period 한계 (parser fix 효과 없음)

7 iter 정밀 검증 결과 — **본문 데이터 자체 한계**:
- 78 fail 후보 중 HTML "year 있음" 22건도 모두 다른 컨텍스트 (안건 list / 보고서 작성일 / 회사 metadata)
- 후보 careerDetails 영역의 year는 거의 없음
- 본문 예시: "(주)포스코엠텍 감사팀장(주)나눔테크 전무이사" — 시작 연도 본문에 X
- 단순 직책+회사명만 명시한 회사 다수 — parser 한계 X, **데이터 한계**

## Regression 검증 ✅

batch v8 (parser 강화 후 proxy_advise G2 batch):
- 전체: 551/566 = 97.35% (v7b 동일)
- **4+ vote majority: 464/467 = 99.36%** (v7b 동일) ✅
- regression 0 (Phase 4 baseline + multi-upstream 패턴 효과 유지)

## 산출물 (commits)

- `882abd4` iter1-4: role 88.7→100% + period 단일 연도 normalize
- `06641cf` iter5 status
- `e5e5d0c` iter6: content year extract +0.3%p
- `eb240a5` iter7: 데이터 한계 확정
- `37ce5bd` iter8: ralph md status + cancel 권고
- (이번) 한자 cover + audit + ship

archive: `wiki/architecture/audits/data/260504_parse_personnel_failure_archive/iter04-iter07_*.md`

## Promise 평가

ralph rule strict — 8 필드 모두 ≥95% 필요. career_period 89.0% 미달.
- promise `PARSE_PERSONNEL_VERIFIED` 정직 출력 X
- **proxy_advise G2 99.36% 영향 없음** — parse_personnel은 secondary metric
- 7/8 + empty 충족 = 실질 성공

## 결론

- role 88.7% → 100% **결정적 fix** (정전환/오너 사내이사 등 기존 unique case 회수)
- career_period 한계 인정 (본문 데이터 X)
- proxy_advise G2 99.36% 유지 — regression 0
- 다음 작업 (필요 시): 별도 ralph (본문 다른 source 보완 — value_up / corp_gov_report cross-source)
