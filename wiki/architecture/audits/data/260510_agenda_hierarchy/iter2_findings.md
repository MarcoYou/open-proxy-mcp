---
type: audit
title: D 패턴 amendments body fallback logic 구현 + 단위 검증 — iter 2
date: 2026-05-10
related:
  - wiki/ralph/260510_0823_ralph_agenda-hierarchy-body-fallback.md
---

# iter 2 — D 패턴 fallback 단위 검증 결과

## 구현

### `_is_charter_top()` 헬퍼 (proxy_advise.py)
top-level 정관변경 안건 식별 ("정관" + "변경"/"개정"). 단독으로는 의미 X — 호출부에서 parent=="" + children==0 + amendments 비어있지 않음과 결합.

### `_law_layer_body()` 함수 (proxy_advise.py)
amendments 각각 단위로 _law_layer 호출. **amendment 단위 검사**로 Ralph 6 회귀 (모든 amendments 통합 → 한 안건 키워드가 다른 sub에 잘못 매칭) 회피.

### 호출부 fallback (build_proxy_advise_payload)
title 미매치 + parent_for_title=="" + `_is_charter_top(title)` + `title_to_children_count[title]==0` + aoi_amendments 있음 → `_law_layer_body` 호출.

### `title_to_children_count` map 추가
agenda tree walk 시 children 수 함께 기록 (D 패턴 식별용).

## 단위 검증 (5 회사)

| 회사 | agendas | amends | title_hits | D 진입 | fb_hits | 결과 |
|---|---:|---:|---:|---:|---:|---|
| 에코프로비엠 | 11 | 2 | 0 | 1 | 0 | A1-1 룰 raw 표현 mismatch |
| 카카오게임즈 | 12 | 2 | 0 | 0 | 0 | **D 패턴 X** (sub-agenda 있음, title 일반) |
| 에스엠 | 10 | 1 | 0 | 1 | **1** ✓ | A1-5 catch (사외→독립이사 명칭) |
| 메리츠금융지주 | 7 | 1 | 0 | 1 | 0 | A1-7 룰 raw 표현 mismatch |
| LG화학 | 17 | 8 | 5 | 0 | 0 | ✅ regression 0 |

## 핵심 검증 결과

✅ **logic 작동** — 에스엠 A1-5 (사외이사 → 독립이사 명칭 변경) catch
✅ **LG화학 regression 0** — children > 0이라 D 패턴 진입 자체 X
⚠ **룰 raw 표현 mismatch** — A1-1 / A1-7 룰이 안건 title 표현 기반 (배제, 전자주주총회, 도입 등). raw amendments는 법령 정합 표현 (적용하지 아니, 제542조의14, 신설/반영)

## raw 표현 분석

### 에코프로비엠 — A1-1 (집중투표 배제 조항 삭제)
- before: "집중투표제는 적용하지 아니한다"
- after: "삭제"
- reason: "개정 상법 제542조의7 (집중투표) 반영"
- 룰 요구: any_of=[집중투표] + secondary=[배제] + secondary_then=[삭제, 폐지, ...]
- mismatch: "**배제**" 키워드 없음 (raw는 "적용하지 아니")

### 메리츠금융지주 — A1-7 (전자주총 도입)
- after: "상법 제542조의14 제1항"
- reason: "상법 개정사항 반영 (상법 제542조의14, 제542조의15)"
- 룰 요구: any_of=[전자주주총회, 전자주총, 비대면 주주총회] + all_of=[도입]
- mismatch: "**전자주주총회**" 단어 없음 (법령 인용만)

### 카카오게임즈 — **D 패턴 X**
- agendas: 제2호 정관 일부 변경 (children 2: 제2-1호 주주총회 기준일 변경, 제2-2호 개정 상법 반영)
- sub-agenda title이 일반 표현이라 _law_layer title 매칭 X
- D 패턴 fallback 진입 X (children > 0)
- 별도 architect 필요 (sub title 자체 raw body 매칭)

## 다음 단계 (iter 3)

raw body 표현을 룰 패턴에 보강:
- A1-1 secondary 추가: ["배제", "적용 안", "적용하지 아니", "적용제외"]
- A1-7 any_of 추가 또는 secondary 추가: ["제542조의14"] (법령 직접 인용)

회귀 검증:
- LG화학 regression 0 확인 (D 진입 0 보장)
- 510 회사 spot 회귀 (title 매칭에는 보강 키워드 영향 측정)
