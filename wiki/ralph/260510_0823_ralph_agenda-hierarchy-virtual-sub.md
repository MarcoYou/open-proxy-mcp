---
type: ralph
title: 안건 hierarchy 구조화 — sub-agenda 없을 때 amendments → 가상 sub
created: 2026-05-10 08:23
completion_promise: AGENDA_HIERARCHY_VIRTUAL_SUB_VERIFIED
max_iterations: 6
ref:
  - wiki/lessons/law-layer-body-260510.md
  - wiki/architecture/audits/data/260510_law_layer_body/
  - wiki/rules/laws/law_layer_rules.json
related_decisions: [260508_0700_decision_law-layer-precision]
related_lessons: [law-layer-body-260510]
---

## Invoke

특수문자 사용 금지. 한글로 풀어쓰기.

```
/ralph-loop:ralph-loop wiki/ralph/260510_0823_ralph_agenda-hierarchy-virtual-sub.md 가이드 따라 안건 hierarchy 구조화 강화. sub-agenda 비어있는 회사는 amendments에서 가상 sub-agenda 자동 생성. _law_layer는 변경 X (title 매칭만). 4 미매치 회사 catch 검증 후 510 회사 회귀. 모두 충족 시 promise. --completion-promise AGENDA_HIERARCHY_VIRTUAL_SUB_VERIFIED --max-iterations 6
```

# Ralph 7: agenda hierarchy 가상 sub-agenda

## Context

Ralph 6 (260510_0747)에서 _law_layer body 매칭 시도 → 회귀 (LG화학 sub-agenda 다수 false positive). 사용자 통찰: matching layer가 아닌 **데이터 구조 (hierarchy)**에서 해결.

기존 parser 구조:
- `parse_agenda_xml`: 안건 트리 추출 (parent + children)
- `parse_agenda_details_xml`: 목적사항별 기재사항
- `parse_aoi_xml(html, sub_agendas=...)`: 정관변경 amendments — **sub_agendas 인자 받음** (이미 매핑 architect 존재)

`services/shareholder_meeting.py`:
- `_agenda_nodes` / `_flatten_agendas` — agenda hierarchy 처리
- line 1095: `parse_aoi_xml(html, sub_agendas=charter_subs)` — sub 있을 때 매핑

**문제**: sub-agenda 비어있는 회사 (4 미매치)는 amendments만 있고 매핑 안 됨.

## 가정

- _law_layer 코드 변경 X (title 매칭만 유지)
- shareholder_meeting/parser 수준에서 hierarchy 보강
- sub-agenda 비어있을 때만 가상 sub 생성 (sub 있는 LG화학 등은 영향 X — regression 0)

## 성공 기준

### G1. 가상 sub-agenda 생성 logic
- `parse_aoi_xml` 또는 `services/shareholder_meeting`에서 sub-agenda 비어있고 amendments 있으면 → amendments[].label/clause/reason 기반 가상 sub-agenda 생성
- agenda hierarchy에 추가
- LG화학 (sub 명확 회사)에는 영향 X

### G2. 4 미매치 회사 catch
- 에코프로비엠: A1-1 (집중투표 적용 X 삭제) catch
- 카카오게임즈: A1-7 (전자주총) catch
- 에스엠: A1-5 (독립이사 명칭) catch
- 메리츠금융지주: A1-7 (전자주총) catch

### G3. 510 회사 회귀 0%
- Ralph 4 + 5 + 6 누적 audit (350 + 160) 기존 hits 유지
- LG화학 5 hits 그대로 (정관 정비 / 권고적 주주제안 / 선임독립이사 false positive 없음)

### G4. 미사용 룰 활성화 시도
- 가상 sub-agenda로 본문 키워드 catch 가능 → A1-8 (자사주 의무소각), B1-9 (감사위 5명) 등 catch?

## 작업 plan (6 iter)

### Phase 1 — hierarchy 강화 logic (iter 1-2)

#### iter 1. 기존 architect 분석 + 4 회사 본문 sub-agenda 부재 확인
- parse_aoi_xml의 sub_agendas 인자 활용 흐름 분석
- shareholder_meeting의 charter_subs 추출 로직 확인
- 4 미매치 회사 raw 직접 확인 (sub-agenda 진짜 없는지)
- 가상 sub-agenda 생성 위치 결정 (parse_aoi_xml vs services/shareholder_meeting)

#### iter 2. 가상 sub-agenda 생성 + agenda hierarchy 추가
- amendments[].label or clause를 가상 sub-agenda title로
- agenda hierarchy의 정관변경 parent 아래에 추가
- 단 sub-agenda 이미 있으면 추가 X (LG화학 영향 0)
- LG화학 + 4 회사 sample 검증

### Phase 2 — 검증 + 회귀 (iter 3-4)

#### iter 3. 4 미매치 회사 catch 검증
- proxy_advise 호출 후 가상 sub-agenda → _law_layer hit 확인
- 각 회사 정확 룰 매칭 (A1-1 / A1-7 / A1-5 / A1-7)

#### iter 4. 510 회사 회귀
- Ralph 4 + 5 + 6 누적 audit data 활용 (회사 list)
- 변경 후 spot 재실행 후 기존 hits 유지 확인

### Phase 3 — 미사용 룰 활성화 시도 (iter 5)

- 가상 sub-agenda + body 키워드 매칭으로 미사용 룰 활성 시도
- A1-8 (자사주 의무소각) / B1-9 (감사위 5명+) 등

### Phase 4 — 문서화 + promise (iter 6)

- lesson 작성 (architect 변경 발견)
- decision 작성 (hierarchy 구조화 변경)
- log update
- promise 발행 (G1-G4 모두 충족 시)

## 영향 범위

- `open_proxy_mcp/tools/parser.py` — `parse_aoi_xml` 내부 가상 sub-agenda 생성 (또는 ...)
- `open_proxy_mcp/services/shareholder_meeting.py` — `_agenda_nodes` 보강 (가상 sub 추가)
- `wiki/lessons/agenda-hierarchy-260510.md` — lesson
- `wiki/decisions/260510_xxxx_decision_virtual-sub-agenda.md` — decision

## 비목표

- _law_layer 변경 X (title 매칭만)
- 룰 catalog 패턴 추가 X (기존 패턴 그대로)
- B1/B2 case-by-case 영역 확장 X

## archive

`wiki/architecture/audits/data/260510_agenda_hierarchy/`

---

## iteration log

### iter 1 — 기존 architect + 4 회사 raw 분석
(작성 예정)

### iter 2 — 가상 sub-agenda 생성 logic
(작성 예정)

### iter 3 — 4 미매치 회사 catch 검증
(작성 예정)

### iter 4 — 510 회사 회귀
(작성 예정)

### iter 5 — 미사용 룰 활성화 시도
(작성 예정)

### iter 6 — 문서화 + promise
(작성 예정)
