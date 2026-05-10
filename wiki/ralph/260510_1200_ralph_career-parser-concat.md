---
type: ralph
title: careerDetails parser concat 분리 강화 (XML only)
created: 2026-05-10 12:00
completion_promise: CAREER_PARSER_CONCAT_VERIFIED
max_iterations: 6
ref:
  - open_proxy_mcp/tools/parser.py
  - open_proxy_mcp/services/director_evaluation.py
related_decisions: [260510_1130_decision_director-faithfulness, 260510_1230_decision_career-parser-concat]
related_lessons: [director-faithfulness-260510, career-parser-concat-260510]
related_ralph: [260510_1100_ralph_director-faithfulness-enhancement]
related_audits: [architecture/audits/data/260510_career_concat/iter4_findings]
---

## Invoke

특수문자 사용 금지. 한글로 풀어쓰기.

```
/ralph-loop:ralph-loop wiki/ralph/260510_1200_ralph_career-parser-concat.md 가이드 따라. careerDetails parser concat 표 layout 분리 강화. XML 본문만 사용 viewer fallback X. 510 회사 정량화 후 logic 강화 후 단위 검증 후 회귀. --completion-promise CAREER_PARSER_CONCAT_VERIFIED --max-iterations 6
```

# Ralph 10: careerDetails parser concat 분리 강화

## Context

Ralph 9 (260510_1100) 사외이사 겸직 카운트 구현 후 메리츠금융지주 사례 진단 시 parser miss 발견:

조홍희 careerDetails raw 본문 (XML pos 141505):
```
조홍희 | 법무법인 태평양 고문 | 2008~2008 2009~2009 2010~2010 2011~현재
서울지방국세청 조사4국장 / 국세청 법인납세국장 / 서울지방국세청장 / 법무법인 태평양 고문
```

→ 4개 직책 (2011~현재 법무법인 태평양 고문 포함)이 한 셀에 concat.
→ 현재 parser 추출: **2개만** (2008~2008, 2009~2009). 2011~현재 (현직 본업) **누락**.

XML 본문에 데이터 풍부 — parser 정확도만 강화하면 회수 가능.

## 가정

- DART XML 본문이 source (viewer HTML fallback X)
- 표 셀 layout이 회사별로 다양 (concat / 줄바꿈 / 분리)
- concat 패턴: 한 셀에 N개 period + N개 직책 concat
- parser 강화로 510 회사 careerDetails 채움률 ↑

## 핵심 design — concat split logic

### 패턴 식별

```
period 셀: "2008~2008 2009~2009 2010~2010 2011~현재"
  → 4개 period entries
content 셀: "서울지방국세청 조사4국장국세청 법인납세국장서울지방국세청장법무법인 태평양 고문"
  → 4개 직책 entries (직책 키워드 -장 / 고문 / 위원 / 이사 / 부회장 / 교수 등 기반 분리)
```

### split logic
1. period 정규식 multi-match (`(\d{4})~(\d{4}|현재)`) — 2개+ 매치 시 split 시도
2. content 직책 키워드 split — 직책 N개 분리
3. period N개 ↔ 직책 N개 zip 매핑 (정확 N 일치 시만)
4. N mismatch 시 원본 entry 유지 (안전 fallback)

### 직책 키워드 catalog
- 끝: 장 / 사장 / 부사장 / 위원 / 이사 / 부회장 / 회장 / 고문 / 교수 / 소장 / 대표 / 본부장 / 사외이사 / 감사 / 감사위원
- 한자/한글 다양 표기 처리 (정규화 후 매칭)

## 성공 기준

### G1. 510 회사 concat 패턴 정량화
- careerDetails entries 중 multi-period 셀 갯수 측정
- concat split 가능 (period count == content split count) 회사 수
- 미분리 (concat 안 된 일반 layout) 회사와 비교

### G2. parser 강화 logic 구현
- `_clean_career_details` (parser.py)에 split helper 추가
- 또는 별도 함수 `_split_concatenated_entries(period, content)`
- 안전 fallback (mismatch 시 원본 유지)

### G3. 단위 검증 (5+ 회사)
- 메리츠금융지주 조홍희: 2 entries → 4 entries
- 그 외 4 사외이사 검증
- 다른 회사 정상 case (이미 분리된 layout) 영향 0

### G4. 510 회사 회귀
- 기존 entries 보존 0 손실
- 신규 entries 추가 (concat 분리 효과)
- Ralph 9 분포 재측정 (concerns 64 / strong 13 → 변화)

### G5. 미발견 케이스 추가 분석
- split 실패 케이스 (직책 키워드 없는 자유 텍스트 등) catalog
- 별도 ralph 후보로 분류

## 작업 plan (6 iter)

### iter 1 — 510 회사 concat 패턴 정량화
- 모든 사외이사 후보 careerDetails 수집
- multi-period 셀 식별 (정규식 매치 N개+)
- split 가능성 측정 (period count vs 직책 키워드 count)
- 분포 catalog
- archive: `architecture/audits/data/260510_career_concat/iter1_findings.json`

### iter 2 — split logic 설계 + 직책 키워드 catalog
- 직책 키워드 list 정의
- split 알고리즘 (period 우선 → 직책 zip)
- 안전 fallback 정책
- 단위 test (메리츠 조홍희 + 5 sample)

### iter 3 — parser 코드 구현
- `_clean_career_details` 또는 별도 helper
- `evaluate_independence` / `evaluate_faithfulness`에 영향 X (data layer만)

### iter 4 — 단위 검증
- 메리츠 사외이사 4명 careerDetails entries 증가 확인
- LG화학 등 정상 회사 영향 0

### iter 5 — 510 회사 회귀
- 기존 spot script 재실행 (Ralph 9 v3 spot)
- concerns/strong 분포 변화 측정
- 신규 catch 회사 list

### iter 6 — 문서화 + promise
- lesson + decision

## 영향 범위

- `open_proxy_mcp/tools/parser.py` — `_clean_career_details` + concat split helper
- `wiki/lessons/career-parser-concat-260510.md`
- `wiki/decisions/260510_xxxx_decision_career-parser-concat.md`
- `wiki/architecture/audits/data/260510_career_concat/`

## 비목표

- HTML viewer fallback 추가 X (XML only)
- 외부 source cross-check X
- careerDetails 외 다른 필드 변경 X
- _decide_director_election 변경 X

## archive

`wiki/architecture/audits/data/260510_career_concat/`

---

## iteration log

### iter 1 — ✅ 510 회사 정량화
815 후보 / multi-period 68 / split 가능 15. 메리츠 같은 진짜 concat은 _extract 단계 누락으로 표면에 안 보임.

### iter 2-3 — ✅ 진단 + split logic 구현
parser pipeline 추적 — _extract_career_from_html (1단계) None / fallback 2단계 contents 분리 부족 발견. _split_content_by_role_endings 직책 boundary split 헬퍼 추가 + fallback 2단계 통합.

### iter 4 — ✅ 단위 검증
- 메리츠 조홍희 2 → 4 entries (2011~현재 catch)
- 메리츠 김우진 1 → 5 entries
- LG화학 천경훈 3 entries 그대로 (regression 0)

### iter 5 — ✅ 510 회사 회귀
- concerns 후보 108 → 112 (+4)
- strong 후보 22 → 20 (-2, strong→concerns 이동)
- concerns 회사 64 → 66 / strong 회사 13 → 11
- 회귀 0 (기존 entries 보존)
- 메리츠 사외이사 4명: 모두 single (본 회사만, 다른 사외이사 X 확인)

### iter 6 — ✅ 문서화 + promise
- lesson: ✅ wiki/lessons/career-parser-concat-260510.md
- decision: ✅ wiki/decisions/260510_1230_decision_career-parser-concat.md
- audit: ✅ iter4_findings.md
- promise: CAREER_PARSER_CONCAT_VERIFIED ✅
