---
type: ralph
title: 호수 hierarchy 정확 추출 + D 패턴 amendments body fallback (가상 sub X)
created: 2026-05-10 08:23
completion_promise: AGENDA_HIERARCHY_EXTRACTION_VERIFIED
max_iterations: 6
ref:
  - wiki/lessons/law-layer-body-260510.md
  - wiki/architecture/audits/data/260510_agenda_hierarchy/
  - wiki/rules/laws/law_layer_rules.json
related_decisions: [260508_0700_decision_law-layer-precision]
related_lessons: [law-layer-body-260510]
---

## Invoke

특수문자 사용 금지. 한글로 풀어쓰기.

```
/ralph-loop:ralph-loop wiki/ralph/260510_0823_ralph_agenda-hierarchy-body-fallback.md 가이드 따라 iter 2부터 진행. iter 1 완료 — parser fix commit be2e722. --completion-promise AGENDA_HIERARCHY_EXTRACTION_VERIFIED --max-iterations 6
```

# Ralph 7: 호수 hierarchy 정확 추출 + D 패턴 amendments body fallback

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

**문제 + 진행 상태**:
1. parser가 호수 hierarchy를 정확히 추출하나? → ✅ iter 1 검증 완료 (10/10 회사 거의 완벽, LG화학 미세 버그 1건만 fix)
2. D 패턴 회사 (raw에 sub 자체 부재) catch 방법 → **amendments body fallback** (가상 sub 생성 X / hierarchy 변경 X)

## 가정

- iter 1 검증 결과: parser 호수 hierarchy 추출 정확도 매우 높음 (10/10 회사). LG화학 미세 버그 1건만 fix 완료
- 4 미매치 회사 = **D 패턴** — raw에 sub-agenda 호수 자체 부재 + top title 일반 표현 ("정관 일부 변경의 건")
- 정관변경 내용은 amendments[].label/clause/before/after에만 존재
- → 호수 hierarchy 강화로 catch 불가, **amendments body fallback이 유일 해결**

## 핵심 설계 — Ralph 6 회귀 회피

**가상 sub-agenda 생성 X / hierarchy 변경 X**.
title 매칭 fallback만 추가:

```python
title_hit = _law_layer(agenda.title, parent_title, ...)
if title_hit:
    record(title_hit)
elif _is_charter_top(agenda) and not agenda.children and amendments:
    # D 패턴 한정 (children 0 + 정관변경 top + amendments 있음)
    body_hit = _law_layer_body(amendments, parent_title=agenda.title, ...)
    if body_hit:
        record(body_hit, source="amendments_body_fallback")
```

| 항목 | Ralph 6 시도 (회귀) | 이번 |
|---|---|---|
| 진입 조건 | 모든 회사 또는 fuzzy 매칭 | **children 0 + 정관변경 top** (D 패턴만) |
| LG화학 영향 | sub 명확해도 amendments 검사 → false positive 8 hits | children > 0 → fallback **자동 제외** |
| 가상 sub | (안 만듦) | 안 만듦 (hierarchy 변경 0) |
| 결과 노출 | 본 안건 hit 추가 | 본 안건 hit 추가 (source marker) |

## 성공 기준

### G1. 호수 hierarchy 정확 추출 검증 — ✅ 완료 (iter 1)

10/10 회사 검증. parser 거의 완벽. LG화학 제3호 미세 버그 fix (commit be2e722, regression 0).

### G2. D 패턴 amendments body fallback 구현

- `_is_charter_top(agenda)` 헬퍼 (top "정관" + "변경"/"개정")
- `_law_layer_body(amendments, ...)` — amendments label+clause+before+after+reason 텍스트 합쳐 매칭
- 진입 조건 strict (children == 0 + 정관변경 top + amendments 비어있지 않음)
- **호출부에서 fallback 추가** (proxy_advise 안건 loop) — _law_layer 본 함수는 변경 X, 격리 유지

### G3. 4 미매치 회사 catch

- 에코프로비엠: A1-1 (집중투표 적용 X 삭제)
- 카카오게임즈: A1-7 (전자주총)
- 에스엠: A1-5 (독립이사 명칭)
- 메리츠금융지주: A1-7 (전자주총)

### G4. 510 회사 회귀 0%

- LG화학 (sub 명확) → fallback 진입 X 확인
- Ralph 4 + 5 + 6 누적 audit (350 + 160) 기존 hits 유지
- 새 D 패턴 회사 catch가 늘어나는 건 OK (긍정 변화)

### G5. (보너스) 추가 D 패턴 회사 식별

510 회사 중 정관변경 안건이 sub 0개 + amendments 있는 회사 cataloging. 4 회사 외 추가 catch 가능 회사 측정.

## 작업 plan (6 iter)

### iter 1 — ✅ 완료 (호수 hierarchy 진단 + LG화학 미세 버그 fix)

10 회사 raw vs parser 비교. parser 거의 완벽 검증. LG화학 ※ note span lookahead 괄호 옵션 추가 (commit be2e722, regression 0).

### iter 2 — body fallback 코드 구현

- `services/proxy_advise.py` 또는 호출부에서 amendments 접근 흐름 식별
- `_is_charter_top()` 헬퍼 + `_law_layer_body()` 함수 추가
- D 패턴 진입 조건 strict 적용 (children 0 + 정관변경 top + amendments 비어있지 않음)
- 단위 검증 (4 미매치 + LG화학)

### iter 3 — 4 미매치 catch + LG화학 regression 검증

- proxy_advise 호출 후 4 회사 정확 룰 hit 확인
- LG화학 hit 수 5 그대로 (false positive 0)

### iter 4 — 510 회사 회귀 spot

- Ralph 4 + 5 + 6 누적 audit data 활용 (kospi_200 + kosdaq_150 + kosdaq_151-300 + dispute_30)
- before vs after hit 비교 표
- 신규 catch 회사 (D 패턴) cataloging

### iter 5 — D 패턴 회사 추가 catalog + 미사용 룰 활성

- 510 회사 중 D 패턴 회사 list
- 활성된 미사용 룰 정리 (A1-8, B1-9 등)

### iter 6 — 문서화 + promise

- lesson 작성 (D 패턴 fallback design + Ralph 6 회피 logic)
- decision 작성 (정관변경 fallback 정책)
- log update
- promise 발행 (G1-G4 충족 시)

## 영향 범위

- `open_proxy_mcp/tools/parser.py` — ✅ iter 1 fix 완료 (※ note span 정합)
- `open_proxy_mcp/services/proxy_advise.py` — `_law_layer_body()` 추가, 호출부 fallback
- `open_proxy_mcp/services/shareholder_meeting.py` — amendments 전달 path 보강 (필요 시)
- `wiki/lessons/agenda-hierarchy-260510.md` — lesson
- `wiki/decisions/260510_xxxx_decision_d-pattern-body-fallback.md` — decision
- `wiki/architecture/audits/data/260510_agenda_hierarchy/` — audit data

## 비목표

- 가상 sub-agenda 생성 X (hierarchy 변경 X)
- 룰 catalog 패턴 추가 X (기존 패턴 그대로)
- B1/B2 case-by-case 영역 확장 X
- 모든 회사 body 매칭 X (D 패턴 한정 — Ralph 6 회귀 회피)

## archive

`wiki/architecture/audits/data/260510_agenda_hierarchy/`

---

## iteration log

### iter 1 — ✅ 완료 (260510_be2e722)

10 회사 진단 + LG화학 ※ note span 미세 버그 fix. 9 회사 영향 0. parser 호수 hierarchy 정확도 검증.

상세: `wiki/architecture/audits/data/260510_agenda_hierarchy/iter1_findings.md`

### iter 2 — ✅ 완료 (260510_e2292d8)

D 패턴 amendments body fallback logic 구현. `_is_charter_top()` + `_law_layer_body()` + 호출부 fallback (children 0 + charter top + amendments). 단위 검증 5 회사 — 에스엠 A1-5 catch + LG화학 regression 0.

### iter 3 — ✅ 완료 (260510_9d15aed)

body_pattern 별도 필드 추가 (스키마 확장). A1-1/A1-7 raw 표현 보강. 4 미매치 회사 중 D 패턴 3개 모두 catch (에코프로비엠 A1-1 / 에스엠 A1-5 / 메리츠 A1-7). 카카오게임즈는 D 패턴 X (sub-agenda 있음, 별도 ralph 후보).

### iter 4 — 510 회사 회귀 spot (진행 중)

scripts/spot_law_layer_with_body.py — title hits + D 패턴 body hits 측정. 4 universe (kospi200 / kosdaq150 / kosdaq300 151-300 / dispute_30) background 진행.

### iter 5 — D 패턴 추가 catalog
(iter 4 결과 후)

### iter 6 — 문서화 + promise
- lesson 작성: ✅ wiki/lessons/agenda-hierarchy-260510.md
- decision 작성: ✅ wiki/decisions/260510_0900_decision_d-pattern-body-fallback.md
- log + index update: ✅
- 510 결과 반영 후 promise 발행
