---
type: ralph
title: 카카오게임즈 패턴 처리 — sub-agenda + amendment 1:1 매핑 architect
created: 2026-05-10 09:50
completion_promise: SUBAGENDA_AMENDMENT_MAPPING_VERIFIED
max_iterations: 6
ref:
  - wiki/lessons/agenda-hierarchy-260510.md
  - wiki/architecture/audits/data/260510_agenda_hierarchy/iter5_kakaogames_spot.md
  - wiki/architecture/audits/data/260510_agenda_hierarchy/iter5_kakaogames_pattern_510.json
  - wiki/decisions/260510_0900_decision_d-pattern-body-fallback.md
  - wiki/rules/laws/law_layer_rules.json
related_decisions: [260510_0900_decision_d-pattern-body-fallback]
related_lessons: [agenda-hierarchy-260510]
related_ralph: [260510_0823_ralph_agenda-hierarchy-body-fallback]
---

## Invoke

특수문자 사용 금지. 한글로 풀어쓰기.

```
/ralph-loop:ralph-loop wiki/ralph/260510_0950_ralph_subagenda-amendment-mapping.md 가이드 따라. 26 진정 카카오게임즈 패턴 회사 sub-agenda + amendment 1:1 매핑 architect 설계 + 구현 + 510 회사 회귀. cross-match 회피. generic title 처리 정책 명확화. --completion-promise SUBAGENDA_AMENDMENT_MAPPING_VERIFIED --max-iterations 6
```

# Ralph 8: sub-agenda + amendment 1:1 매핑

## Context

Ralph 7에서 D 패턴 (top + children 0 + amendments) fallback 구현 완료. 510 회사 spot 검증 — 회귀 0 + body 70건 신규 catch + A1-8 첫 활성. 단 카카오게임즈 같은 케이스 처리 X.

iter 5 510 회사 식별 — **진정 카카오게임즈 패턴 26개 (5.1%)** 발견:
- 정관변경 top (children > 0, sub-agenda 있음)
- sub-agenda title 모두 generic — "개정 상법 반영" / "조문 정비" / "사업목적 추가" / "제N조 변경"
- D 패턴 fallback 진입 X (children > 0)
- title 매칭 X (sub title에 강행규정 키워드 부재)

26 회사: 유한양행, 강원랜드, 씨에스윈드, 한미사이언스, 솔브레인, 동진쎄미켐, 원익홀딩스, 하나마이크론, 차바이오텍, 인텔리안테크, 솔브레인홀딩스, 쏠리드, 엔켐, 나노신소재, 파이버프로, 넥슨게임즈, 아난티, 에코프로에이치엔, 시노펙스, 한라캐스트, 파인엠텍, 인바디, 이수페타시스, 대덕전자, ISC, 성호전자

## 핵심 design challenge

### 1. 진입 조건 (D 패턴과 다름)
```
parent에 "정관" + "변경"/"개정" (정관변경 top의 sub)
+ 자기 children == 0
+ 자기 title 일반 표현 (정관/변경/개정 단어 없음)
+ amendments 비어있지 않음
```

### 2. sub-agenda → amendment 1:1 매핑 (cross-match 회피)
모든 sub가 모든 amendments 일괄 검사하면 double counting (Ralph 7 카카오게임즈 시뮬레이션에서 두 sub 모두 A1-7 hit). 매핑 logic 필요:
- amendment label (제N조 / 제N조의M) → sub title 키워드 매칭
- 한 amendment 매핑된 후에는 다른 sub에서 skip

### 3. generic title 처리 정책
"개정 상법 반영" / "기타 변경" 같은 generic sub title은 fuzzy 매칭 보장 어려움. 정책 결정 필요:
- (옵션 A) 매칭 안 된 amendment + generic sub: 통합 검사 (1번만, 첫 hit 반환)
- (옵션 B) generic sub는 skip (운용사 정책 fallback)
- (옵션 C) raw 노출 + LLM 판단 위임 (B1/B2 raw 첨부 패턴)

### 4. amendments 갯수 mismatch
- amendments 2 / sub 3: 1 sub 미매핑
- amendments 3 / sub 2: 1 amendment 미매핑

## 가정

- D 패턴 fallback (Ralph 7) 그대로 유지
- _law_layer 본 함수 변경 X
- shareholder_meeting/proxy_advise 호출부 보강
- 26 회사 raw 분석 후 매핑 가능성 정량화

## 성공 기준

### G1. 26 진정 패턴 회사 raw 분석
- 각 회사 sub-agenda 갯수 + amendment 갯수 + label/reason 키워드 catalog
- sub→amendment 매핑 가능성 분류:
  - (a) 명확 매핑 (sub title 키워드 ∈ amendment label/reason)
  - (b) 부분 매핑 (일부만)
  - (c) 매핑 불가 (모두 generic)
- 비율 측정

### G2. 매핑 logic 설계
- label/clause 정관 조항 번호 (제N조 / 제N조의M) 추출
- sub title 키워드 (조항 번호 / 한국어 명사) 매칭
- score 기반 best match (카카오게임즈 검증된 score 정량화)
- generic title 처리 정책 결정 (옵션 A/B/C 중 1)

### G3. 코드 구현 + 단위 검증
- `services/proxy_advise.py` 또는 별도 utility
- 카카오게임즈 + 26 회사 sample (5+) 검증
- 매핑 정확도 측정

### G4. 510 회사 회귀
- Ralph 7 결과 (title 314 + body 70) 그대로 유지
- 카카오게임즈 패턴 신규 catch 측정
- false positive 0 (cross-match 0)

### G5. 문서화 + promise
- lesson + decision + audit
- promise 발행 (G1-G4 충족 시)

## 작업 plan (6 iter)

### iter 1 — 26 회사 raw 매핑 가능성 정량화
- aoi_change scope 호출 → amendments label/clause/reason 수집
- summary scope → sub-agenda title 수집
- sub title vs amendment label 키워드 score 측정
- 분류 (명확 / 부분 / 불가) 비율
- archive: `wiki/architecture/audits/data/260510_subagenda_mapping/iter1_26_companies.json`

### iter 2 — 매핑 logic 설계 + generic 정책 결정
- label 정관 조항 번호 추출 (제N조 / 제N조의M)
- sub title 키워드 (조항 번호 / 한국어 명사) 매칭
- score 기반 best match (threshold 결정)
- generic title 처리 옵션 결정 (A/B/C)

### iter 3 — 코드 구현 + 단위 검증
- helper: `_map_subagenda_to_amendment(sub, amendments)`
- 호출부 fallback (parent 정관변경 + children 0 + sub generic + amendment 있음)
- 카카오게임즈 + 5+ sample 검증
- LG화학 (D 명확) 회귀 0 확인

### iter 4 — 510 회사 회귀
- Ralph 7 spot script 확장 (sub→amendment 매핑 추가)
- title hits 314 + body hits 70 유지
- 카카오게임즈 패턴 신규 catch
- false positive 0 검증

### iter 5 — 추가 sample 검증 + 결과 정리
- 26 진정 패턴 회사 catch 수 측정
- 미catch 회사 사유 분석 (raw 본문 강행규정 키워드 부재 등)

### iter 6 — 문서화 + promise

## 영향 범위

- `open_proxy_mcp/services/proxy_advise.py` — 매핑 helper + 호출부
- `wiki/architecture/audits/data/260510_subagenda_mapping/` — audit data
- `wiki/lessons/subagenda-mapping-260510.md` — lesson
- `wiki/decisions/260510_xxxx_decision_subagenda-mapping.md` — decision

## 비목표

- D 패턴 fallback 변경 X (Ralph 7 그대로)
- 룰 catalog 구조 변경 X (body_pattern 그대로 활용)
- 모든 sub-agenda body 일괄 검사 X (cross-match 위험)
- 정관변경 외 다른 안건 (이사 선임 등) sub 매핑 X (영역 분리)

## archive

`wiki/architecture/audits/data/260510_subagenda_mapping/`

---

## iteration log

### iter 1 — 26 회사 raw 매핑 가능성 정량화
(작성 예정)

### iter 2 — 매핑 logic 설계 + generic 정책
(작성 예정)

### iter 3 — 코드 구현 + 단위 검증
(작성 예정)

### iter 4 — 510 회사 회귀
(작성 예정)

### iter 5 — 추가 sample 검증
(작성 예정)

### iter 6 — 문서화 + promise
(작성 예정)
