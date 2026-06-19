---
type: decision
title: _classify_agenda에 parent_title 옵션 + 정관 sub-안건 short-circuit
date: 2026-05-08 00:30
status: adopted
related:
  - wiki/ralph/260507_2330_ralph_classify-agenda-fix.md
  - wiki/lessons/agenda-classification-260507.md
---

# _classify_agenda parent shortcircuit 결정

## 배경

코붕이 review (2026-05-07): 롯데케미칼 proxy_advise 호출 시 정관변경 sub-안건 두 건 NO_DATA. ralph 7 iter audit 결과 300 회사에서 mismatch 19.3% (607/3145) — 모두 정관 sub-안건이 다양한 카테고리로 잘못 분류.

## 결정

`_classify_agenda` 시그니처 변경 — `parent_title` 옵션 추가 + 정관 sub-안건 short-circuit:

```python
def _classify_agenda(agenda_title: str, parent_title: str = "") -> str:
    parent = (parent_title or "").strip()
    if parent and "정관" in parent:
        return "articles_amendment"
    # ... 기존 로직 그대로
```

caller (`proxy_advise._run`)에서 agenda tree → title:parent map 추출 + 전달.

## 근거

1. 300 회사 audit에서 정관 sub-안건 100%가 잘못 분류됨
2. 잘못된 카테고리가 9+ 종류로 흩어짐 — 키워드별 fix는 whack-a-mole
3. 부모 인지 단일 fix로 해결 가능 (문제는 "정관 sub" 단 하나)
4. parent_title=""로 default — 기존 caller 호환 (회귀 0)

## Trade-off

- (+) 분류 정확도 19.3% mismatch → 0% (300 회사 verified)
- (+) NO_DATA 잘못 발생 제거 (롯데케미칼 case 회귀 검증 — 2건 → 0건)
- (+) 단일 fix로 9+ 카테고리 패턴 모두 해결
- (-) 시그니처 변경 — 모든 caller가 parent 전달해야 효과 있음 (default ""로 fallback 가능)

## 영향 범위

- `open_proxy_mcp/services/proxy_advise.py`:
  - `_classify_agenda` 시그니처 + short-circuit 분기
  - `_run`: agenda tree 순회로 title→parent map 추출 + 전달
- 외부 caller 없음 (proxy_advise 내부 only)

## 검증

| 지표 | Pre-fix | Post-fix |
|---|---|---|
| 전체 mismatch | 19.30% (607/3145) | **0.00%** |
| 정관 sub 정확도 | 0.00% | **100.00%** (607/607) |
| 롯데케미칼 NO_DATA | 2건 | **0건** |
| 회귀 (다른 분류 유지) | - | 100% (변화 없음) |

## 비목표

- 다른 분류기 audit (`_classify_value_up_item`, `_classify_filing`, `_is_company_side` 등) — Ralph 2/3로 분리
- agenda tree 구조 자체 변경
- decision logic (`_decide_*`) 변경

## archive 폴더

`wiki/architecture/audits/data/260507_classify_agenda/` (post-fix audit JSON 8개 + pre-fix 9개 + phase1_aggregate.json)
