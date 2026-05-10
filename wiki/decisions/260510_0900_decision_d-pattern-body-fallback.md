---
type: decision
title: D 패턴 amendments body fallback — 정관변경 sub-agenda 부재 회사 catch
date: 2026-05-10
status: active
related_ralph: [260510_0823_ralph_agenda-hierarchy-body-fallback]
related_lessons: [agenda-hierarchy-260510, law-layer-body-260510]
related_audits: [260510_agenda_hierarchy/iter1_findings, 260510_agenda_hierarchy/iter2_findings, 260510_agenda_hierarchy/iter4_findings]
---

# Decision — D 패턴 amendments body fallback

## 결정

`_law_layer` title 매칭 fallback으로 amendments raw body 매칭을 추가한다. 단 D 패턴 (정관변경 top + children 0 + amendments 있음) 한정.

## 배경

DART 정기주총 소집공고 안건 표기 패턴:

| 패턴 | 예시 | 처리 |
|---|---|---|
| A | "2호 정관변경" + "2-1호 집중투표 배제 삭제" | _law_layer (title) |
| B | "3호 정관변경 - 집중투표 배제 폐기" | _law_layer (title) |
| C | "4호 집중투표 배제 조항 제거" | _law_layer (title) |
| **D** | "2호 정관 일부 변경의 건" + sub 0 | **_law_layer_body (amendments)** |

D 패턴 회사 (에코프로비엠 / 에스엠 / 메리츠금융지주 등)는 변경 내용이 amendments[].label/clause/before/after에만 존재 — title 매칭 catch 불가.

## 안전장치 (Ralph 6 회귀 회피)

1. **strict 진입 조건**: parent=="" + `_is_charter_top(title)` + `children == 0` + `amendments` 비어있지 않음
2. **amendment 단위 검사**: 모든 amendments 통합 X — 각 amendment 자체 body만 검사
3. **body_pattern 별도 필드**: title 매칭은 agenda_pattern 그대로 — title 회귀 위험 0

## 룰 catalog 변경

`law_layer_rules.json` 스키마에 `body_pattern` 옵션 필드 추가. agenda_pattern과 동일 schema. body_pattern 없는 룰은 agenda_pattern fallback.

A1-1 + A1-7에 body_pattern 추가 — raw 본문이 법령 정합 표현 (적용하지 아니, 제542조의14, 신설/반영) 위주라 lenient 패턴 필요.

## 비목표 (제외)

- 카카오게임즈 같은 sub-agenda 있고 sub title 일반 표현 회사 — D 패턴 X, 별도 architect 필요
- 모든 회사 amendments 통합 매칭 (Ralph 6 회귀 사례)
- title 매칭 패턴 보강 (회귀 위험)

## 영향 범위

- `open_proxy_mcp/services/proxy_advise.py`: `_is_charter_top()`, `_law_layer_body()`, 호출부 fallback, `title_to_children_count` map
- `wiki/rules/laws/law_layer_rules.json`: schema body_pattern 필드 + A1-1/A1-7 body_pattern

## 검증

- 4 미매치 회사 중 D 패턴 3개 catch (에코프로비엠 A1-1 / 에스엠 A1-5 / 메리츠 A1-7)
- LG화학 regression 0 (children > 0이라 D 진입 X)
- 510 회사 spot 회귀 (iter 4): TBD

## 후속

- 카카오게임즈 같은 sub-agenda 일반 표현 회사 처리 — 별도 ralph
- 다른 룰 (A1-2 / A1-8 / B1-x) body_pattern 추가
