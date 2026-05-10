---
type: decision
title: 카카오게임즈 패턴 — sub→amendment strict 매핑 fallback
date: 2026-05-10 10:15
status: active
related_ralph: [260510_0950_ralph_subagenda-amendment-mapping]
related_lessons: [subagenda-mapping-260510, agenda-hierarchy-260510]
related_audits: [architecture/audits/data/260510_subagenda_mapping/iter1_findings]
---

# Decision — 카카오게임즈 패턴 sub→amendment 매핑 fallback

## 결정

D 패턴 fallback (Ralph 7) 다음에 sub-agenda → amendment 1:1 매핑 fallback 추가. **strict cascade** (label substring + clause 매칭만) — keyword 매칭은 의도적 제외.

## 진입 조건 (4 AND)

```
parent에 "정관" + "변경"/"개정"
+ 자기 children == 0
+ 자기 title generic 아님 (도메인 키워드 1+ 포함)
+ amendments 비어있지 않음
```

## 매핑 cascade

1. amendment label == sub title (또는 substring) — 강원랜드 "관계 법령 ..." 동일 string
2. amendment label/before/after에서 조항 추출 → sub clauses 매칭 — 한미사이언스 "제22조"

keyword 매칭은 의도적 제외 — semantic mismatch false positive 회피 (LG화학 "선임독립이사 선임" → "독립이사 명칭 변경" 매핑 사례).

## generic title 정책

generic sub (도메인 키워드 없음 — "그 외 변경" / "기타 정비")는 fallback 진입 X. cross-match 위험 0. 운용사 정책 fallback.

## cross-match 회피

회사별 `_subagenda_used_amendments: set[int]` track. 매핑된 amendment idx mark → 다른 sub에서 skip.

## 검증

- LG화학 regression 0 (sub_hits 0)
- 한미사이언스 3 / 차바이오텍 2 / 유한양행 1 catch
- 카카오게임즈 미catch (sub keyword 매칭 의도적 제외)

## 비목표

- generic / fuzzy sub title 매핑 — semantic 영역, 별도 LLM ralph
- 모든 sub-agenda body 일괄 검사 — cross-match 위험
- D 패턴 fallback 변경 X (Ralph 7 그대로)

## 영향 범위

- `open_proxy_mcp/services/proxy_advise.py`: 헬퍼 (_extract_clauses / _is_generic_sub / _map_subagenda_to_amendment / _law_layer_subagenda_mapped) + 호출부 0-c
- 룰 catalog 변경 X (body_pattern 그대로 활용)
