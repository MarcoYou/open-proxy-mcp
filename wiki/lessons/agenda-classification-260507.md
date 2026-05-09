---
type: lesson
title: _classify_agenda 정관 sub-안건 분류 fix (260507)
date: 2026-05-07
related:
  - wiki/ralph/260507_2330_ralph_classify-agenda-fix.md
  - wiki/decisions/260507_xxxx_decision_classify-agenda-parent-shortcircuit.md
related_decisions: [260508_0030_decision_classify-agenda-parent-shortcircuit]
---

# _classify_agenda 정관 sub-안건 분류 fix (260507)

## 결과 요약

300 회사 (KOSPI 200 + KOSDAQ 100) 통합 audit 후 fix 적용 — 분류 정확도 0.00% mismatch 달성:

| 지표 | 목표 | Pre-fix | Post-fix |
|---|---|---|---|
| G1 mismatch 비율 | < 1% | 19.30% | **0.00%** ✓ |
| G2 정관 sub 정확도 | 100% | 0.00% | **100.00%** ✓ |
| G3 회귀 | 0 | - | 0 ✓ |

## 핵심 lesson — 단일 패턴 단일 fix

### 발견 (코붕이 review)

롯데케미칼 proxy_advise 호출 → 정관변경 sub-안건 ("사외이사 명칭 변경" / "감사위원 분리선임 확대") 두 건 NO_DATA.

### Root cause

`_classify_agenda` 우선순위:
1. "정관" 키워드 → articles_amendment
2. "사외이사" → director_election
3. "감사위원" + "선임" → audit_committee_election

**문제**: sub-안건 title에 "정관" 키워드 없으면 다음 분기로 → 잘못 분류.

예: parent="제2호 정관 일부 변경의 건" / sub="사외이사 명칭 변경" → director_election 오분류 → 후보 평가 데이터 없음 → NO_DATA.

### 300 회사 audit 결과

전체 mismatch 607건 (19.3%) — **모두 정관변경 sub-안건**. 잘못 분류된 카테고리 분포:

| 카테고리 | 건수 | 패턴 예시 |
|---|---|---|
| `other` | 가장 많음 | 위원회 명칭 / 부칙 / 목적사항 / 개최방식 |
| `director_election` | 6+ | 사외이사 → 독립이사 명칭 변경 |
| `audit_committee_election` | 3+ | 감사위원 분리선임 인원 변경 |
| `treasury_share` | 5+ | 자기주식 보유처분 규정 신설 |
| `retirement_pay` | 4+ | 이사 보수와 퇴직금 변경 |
| `cash_dividend` | 1+ | 배당절차 개선 반영 |
| `director_compensation` | 2+ | 이사 보수한도 규정 신설 |
| `merger_or_restructuring` | 1+ | 주식분할(액면분할) |
| `shareholder_proposal` | 1+ | 권고적 주주제안의 도입 |
| `financial_statements` | 2+ | 재무제표 작성 등 조문 변경 |

→ 패턴이 매우 다양함. 키워드별 fix는 끝없음. **부모 인지 단일 fix가 정답**.

### Fix

`_classify_agenda` 시그니처에 `parent_title=""` 추가 + 우선 short-circuit:

```python
def _classify_agenda(agenda_title: str, parent_title: str = "") -> str:
    parent = (parent_title or "").strip()
    # parent가 정관변경이면 sub 안건도 articles_amendment
    if parent and "정관" in parent:
        return "articles_amendment"
    # 기존 로직 그대로 ...
```

caller (`proxy_advise.py:_run`)에서 agenda tree 순회하며 title→parent map 생성:

```python
title_to_parent: dict[str, str] = {}
def _walk_agenda_tree(items, parent=""):
    for it in items or []:
        t = (it.get("title") or "").strip()
        if t:
            title_to_parent[t] = parent
        _walk_agenda_tree(it.get("children", []), parent=t)
_walk_agenda_tree(agenda_data.get("agendas") or [])
```

호출 시 parent 전달:
```python
category = _classify_agenda(title, parent_title=title_to_parent.get(title, ""))
```

### 검증

300 회사 post-fix 재 audit (KOSPI 200 + KOSDAQ 100):
- 289 OK records / 3,145 agendas / **0 mismatch**
- 정관 sub-안건 607/607 = **100% articles_amendment 분류**
- 롯데케미칼 proxy_advise 회귀: NO_DATA 2건 → **0건**

## Meta-lesson — 단일 패턴 발견 시 단일 fix가 우월

300 회사 audit 결과 mismatch 패턴이 9+ 카테고리로 흩어져 보였지만, **공통 분모는 "정관 sub"라는 단 하나**. 키워드별 case-by-case fix 시도하면 끝없는 whack-a-mole.

→ 패턴 발견 시 **공통 분모 추출**이 우선. 하부 다양성에 휘둘리지 말 것.

## 영향

- `open_proxy_mcp/services/proxy_advise.py`:
  - `_classify_agenda(title, parent_title='')` 시그니처 추가
  - 정관 sub 안건 short-circuit 분기
  - caller (`_run`): title→parent map 추출 + 전달
- `scripts/spot_classify_agenda.py` 신규 (audit script)
- `scripts/agg_classify_agenda.py` 신규 (통합 분석)
- `wiki/architecture/audits/data/260507_classify_agenda/` 검증 데이터

## 비목표

- 다른 분류기 (`_classify_value_up_item` / `_classify_filing` 등) — 별도 ralph
- proxy_advise decision logic (`_decide_*`) 변경
- agenda hierarchy 구조 변경
