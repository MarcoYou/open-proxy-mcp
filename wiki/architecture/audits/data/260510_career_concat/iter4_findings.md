---
type: audit
title: parser concat split 510 회사 회귀 + Ralph 9 분포 변화 — iter 4
date: 2026-05-10
related:
  - wiki/ralph/260510_1200_ralph_career-parser-concat.md
related_ralph: [260510_1200_ralph_career-parser-concat]
related_lessons: [career-parser-concat-260510]
related_decisions: [260510_1230_decision_career-parser-concat]
---

# Ralph 10 iter 4-5 — 510 회사 회귀

## 단위 검증 (메리츠 + LG화학)

| 후보 | Before | After | 변화 |
|---|---:|---:|---|
| 메리츠 조홍희 | 2 entries | **4 entries** | +2 (2010/2011~현재 회수) |
| 메리츠 김우진 | 1 entry | **5 entries** | +4 (concat 5개 split) |
| 메리츠 김명애 | 1 entry | 1 entry | 변화 X (period 1개라 정상) |
| 메리츠 김연미 | 1 entry | 1 entry | 변화 X (동일) |
| LG화학 천경훈 | 3 entries | 3 entries | regression 0 |

## 510 회사 회귀 (Ralph 9 v3 spot 재실행)

| 항목 | Ralph 9 (before fix) | Ralph 10 (after fix) | 변화 |
|---|---:|---:|---|
| 사외이사 후보 총 | 815 | 815 | 0 |
| concerns 후보 (≥2) | 108 | **112** | +4 |
| strong 후보 (≥3) | 22 | **20** | -2 |
| concerns 회사 | 64 | **66** | +2 |
| strong 회사 | 13 | **11** | -2 |

해석:
- concerns 후보 +4: parser 정확도 ↑로 careerDetails 더 풍부 추출 → catch 증가
- strong → concerns 이동 -2: entries 더 정확 → 일부 후보 분류 변화
- 회사 단위 net 변화: concerns +2 / strong -2

## 메리츠금융지주 결과

| 후보 | total | in_career | 본 회사 표기 | 결과 |
|---|---:|---:|---|---|
| 조홍희 | 1 | 0 | 본 회사만 | single (정상) |
| 김우진 | 1 | 0 | 본 회사만 | single (정상) |
| 김연미 | 1 | 0 | 본 회사만 | single (정상) |
| 김명애 | 1 | 0 | 본 회사만 | single (정상) |

→ careerDetails 정확 추출 후 confirmed: 메리츠 사외이사 모두 다른 회사 사외이사 직책 X (본업이 교수/변호사/고문 등). Ralph 9 결과 그대로 (위반 없음).

## 미발견 케이스 (G5)

메리츠 김명애/김연미: period 1개 (1997~현재 / 1999~현재) + content multi-roles concat.
- 한 시기 동안 여러 직책 거침 (학교 직급 변화 등)
- period split 안 됨 (1개) → split logic 진입 X
- 별도 case — 별도 ralph 또는 raw 노출 + LLM 위임

## archive

- `iter1_concat_audit.json` (510 raw concat 정량화)
- `wiki/architecture/audits/data/260510_director_faithfulness/iter2_concurrent_v3.json` (510 v3 회귀)
