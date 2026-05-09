---
type: ralph
title: 안건 호수 hierarchy 정확 추출 + title 매칭 성공률 검증
created: 2026-05-10 08:23
completion_promise: AGENDA_HIERARCHY_EXTRACTION_VERIFIED
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
/ralph-loop:ralph-loop wiki/ralph/260510_0823_ralph_agenda-hierarchy-virtual-sub.md 가이드 따라. 안건 호수 hierarchy (1호 2호 2-1호 2-2호 등) 추출 정확도 먼저 검증. parser가 raw에서 sub-agenda 호수 누락하는지 확인 후 보강. title 매칭 성공률 측정. raw에 sub 자체 부재인 D 패턴 회사만 amendments fallback 검토. 모두 충족 시 promise. --completion-promise AGENDA_HIERARCHY_EXTRACTION_VERIFIED --max-iterations 6
```

# Ralph 7: 안건 호수 hierarchy 정확 추출 + title 매칭

## Context

Ralph 6 (260510_0747)에서 _law_layer body 매칭 시도 → 회귀 (LG화학 sub-agenda 다수 false positive). 사용자 통찰: matching layer가 아닌 **데이터 구조 (호수 hierarchy)**에서 해결.

### 핵심 architect

안건은 무조건 호수 (1호 / 2호 / 2-1호 / 2-2호 / 3호) 구조. 회사별 표기 다양:

| 패턴 | 예시 | catch 가능 여부 |
|---|---|---|
| **A** sub 명확 | `2호 정관변경의 건` + `2-1호 집중투표 배제 조항 삭제` | ✅ title 매칭 |
| **B** sub 없음, top에 모든 내용 | `3호 정관변경 - 집중투표 배제 조항 폐기` | ✅ title 매칭 (top title이 명확) |
| **C** "정관변경" 단어 없음 | `4호 집중투표 배제 조항 제거` | ✅ title 매칭 (parent 무관 룰만) |
| **D** sub 없음, top 일반 표현 | `2호 정관 일부 변경의 건` (4 미매치 회사) | ❌ title 부족, amendments 활용 필요 |

기존 parser:
- `parse_agenda_xml`: number / level1-3 / title / children 추출
- `parse_agenda_details_xml`: 목적사항별 기재사항
- `parse_aoi_xml(html, sub_agendas=...)`: amendments — sub_agendas 인자 받음

**문제**:
1. parser가 호수 hierarchy를 정확히 추출하나? → 검증 필요
2. D 패턴 회사는 amendments[].label/clause로 가상 sub 생성 (Ralph 7 원안)

## 가정

- 안건 raw에 호수 (1호 / 2호 / 2-1호 / 2-2호 / 3호) 표기는 회사별 패턴 다름
- parser가 호수 hierarchy를 정확히 추출 못하는 케이스 존재 가능 → 검증 필요
- D 패턴 (raw에 sub 자체 부재)은 amendments fallback 외 catch 불가 (마지막 수단)
- _law_layer 코드 변경 X (title 매칭만 유지)
- shareholder_meeting/parser 수준에서 hierarchy 보강

## 성공 기준

### G1. 호수 hierarchy 추출 정확도 검증 (필수 — 다른 모든 것의 전제)

10+ 회사 sample (LG화학 / 에코프로비엠 / 카카오게임즈 / 에스엠 / 메리츠금융지주 + KOSPI 5 추가) raw aoi_change scope 호출 후:
- raw에 호수가 어떤 형태로 표기 (제2호/2호/제2-1호 등)
- parser parse_agenda_xml 출력의 number / level1-3 / title / children이 raw와 일치하는가
- 누락 / 오인식 케이스 cataloging

### G2. parser 보강 (필요 시)

G1에서 누락 / 오인식 발견 시:
- 정규식 / 분리 로직 보강
- LG화학 sub 명확한 회사 회귀 0
- 보강 전후 510 회사 spot 비교

### G3. title 매칭 성공률 측정 (Before vs After)

Ralph 4 + 5 + 6 누적 audit 350 + 160 = 510 회사 spot 재실행:
- Before (현 parser): hits 수 / catch 회사 수
- After (G2 보강 후): hits 수 / catch 회사 수
- 차이 = G2 효과 측정

### G4. D 패턴 회사 분류 (raw에 sub 진짜 없는 회사 식별)

호수 hierarchy 정확 추출 후에도 catch X 회사 cataloging:
- 4 미매치 회사 중 D 패턴 (raw에 sub 부재 + top title 일반 표현) 식별
- D 패턴은 amendments fallback 검토 (별도 ralph 후보 또는 본 ralph G5)

### G5. (선택) D 패턴 amendments fallback

D 패턴 회사가 G4에서 식별되면:
- amendments[].label / clause / reason → 가상 sub-agenda 생성 (D 패턴 한정)
- LG화학 같은 sub 명확 회사 절대 영향 X (조건: agenda hierarchy에 정관변경 sub 0개일 때만)
- 4 회사 catch 검증

## 작업 plan (6 iter)

### Phase 1 — 호수 hierarchy 진단 (iter 1)

#### iter 1. 10+ 회사 raw vs parser 출력 비교
- LG화학 / 에코프로비엠 / 카카오게임즈 / 에스엠 / 메리츠금융지주 + KOSPI 5 추가 (삼성전자 / SK하이닉스 / 현대차 / NAVER / 셀트리온 등)
- aoi_change scope 호출 → 안건 list raw HTML/XML 보존
- parse_agenda_xml 출력 dump
- raw 호수 표기 vs parser number / hierarchy 표 작성
- 누락 / 오인식 catalog
- archive: `wiki/architecture/audits/data/260510_agenda_hierarchy/raw_vs_parser_10.md`

### Phase 2 — parser 보강 (iter 2-3)

#### iter 2. parser fix (필요 시)
- iter 1 발견 누락 / 오인식 패턴별 정규식 / 분리 로직 보강
- LG화학 + 10 sample 회귀
- sub 명확 회사 영향 0 확인

#### iter 3. 510 회사 spot before vs after
- Ralph 4 + 5 + 6 누적 audit data 활용 (kospi_200.json + kosdaq_150.json + kosdaq_151-300.json + dispute_30)
- 보강 전 hits / 보강 후 hits 비교 표

### Phase 3 — D 패턴 식별 + (선택) fallback (iter 4-5)

#### iter 4. D 패턴 식별
- G3 재spot 후 여전히 catch X 회사 list
- raw aoi_change scope에서 sub-agenda 진짜 없는지 + amendments는 있는지 확인
- D 패턴 회사 catalog (몇 개 / 어떤 미사용 룰 활성 가능한지)

#### iter 5. (선택) D 패턴 amendments fallback
- D 패턴 회사 한정 가상 sub 생성 logic (sub 0개 + amendments 있을 때만)
- 4 미매치 회사 catch 검증
- LG화학 회귀 0 확인

### Phase 4 — 문서화 + promise (iter 6)

- lesson 작성 (호수 hierarchy 발견)
- decision 작성 (parser 변경 + D 패턴 정책)
- log update
- promise 발행 (G1-G4 충족 시 — G5는 선택)

## 영향 범위

- `open_proxy_mcp/tools/parser.py` — `parse_agenda_xml` 정규식 / 호수 분리 보강
- `open_proxy_mcp/services/shareholder_meeting.py` — `_agenda_nodes` D 패턴 fallback (선택)
- `wiki/lessons/agenda-hierarchy-260510.md` — lesson
- `wiki/decisions/260510_xxxx_decision_agenda-hierarchy-extraction.md` — decision
- `wiki/architecture/audits/data/260510_agenda_hierarchy/` — audit data

## 비목표

- _law_layer 변경 X (title 매칭만)
- 룰 catalog 패턴 추가 X (기존 패턴 그대로)
- B1/B2 case-by-case 영역 확장 X
- body 키워드 매칭 X (Ralph 6 회귀로 폐기)

## archive

`wiki/architecture/audits/data/260510_agenda_hierarchy/`

---

## iteration log

### iter 1 — 호수 hierarchy raw vs parser 비교
(작성 예정)

### iter 2 — parser 보강
(작성 예정)

### iter 3 — 510 회사 before vs after
(작성 예정)

### iter 4 — D 패턴 식별
(작성 예정)

### iter 5 — (선택) D 패턴 fallback
(작성 예정)

### iter 6 — 문서화 + promise
(작성 예정)
