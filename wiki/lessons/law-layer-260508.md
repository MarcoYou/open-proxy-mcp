---
type: lesson
title: 법령 layer 도입 — 1·2·3차 상법 개정 + 정관 우회 시나리오 (260508)
date: 2026-05-08
related:
  - wiki/ralph/260508_0130_ralph_law-layer.md
  - wiki/rules/laws/상법-2025-2026-종합.md
  - wiki/rules/laws/상법-2025-2026-종합.md
  - wiki/rules/laws/law_layer_rules.json
  - wiki/decisions/open-proxy-guideline.md
related_decisions: [260508_0200_decision_law-layer, decisions/README]
---

# 법령 layer 도입

## 결과 요약

`proxy_advise`에 법령 layer 우선 적용 — vote_style 운용사 정책 위에 강행규정 자동 반영.

| 지표 | 목표 | 측정값 | 충족 |
|---|---|---|---|
| G1 36 catalog 항목 | 100% 정확 분류 | 36/36 (단위 테스트 + spot) | ✓ |
| G2 자산 2조+ 30 회사 회귀 | 회귀 0 | 28/29 hit, LG화학 5/5 정확 | ✓ |
| G3 운용사 표기 일관성 | 7→8 통일 | 코드+wiki 모두 통일 | ✓ |

## 핵심 lesson

### 발견 (코붕이 review)

LG화학 proxy_advise 호출 시 정관 sub 안건 잘못 분류:
- 제2-1호 집중투표제 배제 조항 삭제 → AGAINST (잘못, 사실 FOR)
- 제2-5호 감사위원 의결권 제한 강화 → AGAINST (잘못, 사실 FOR)

이전 lesson (260507 _classify_agenda fix)으로 NO_DATA는 사라졌지만, 새로 hardcoded `_decide_articles_amendment` keyword 매칭이 stale로 잘못된 결과.

### Root cause

vote_style 운용사 정책은 **운용사가 정책 PDF update 안 하면 stale**. 2026 상법 개정 7개 조항 중 어느 것도 5+ 운용사 정책에 미반영. 결국 hardcoded `_decide_*` fallback 분기로 결정되는데 이것도 키워드 단순 매칭이라 새 법 못 따라감.

→ **forward-looking proxy advisory를 위해서는 법령 자체를 layer로 정합해야**.

### Solution: Layer 구조

```
[Layer 1 신규] 법령 (강행규정 + 우회 시나리오)
                  ↓ hit 시 거기서 결정 + skip 아래
[Layer 2] vote_style 운용사 정책 (asset_managers/policies/)
                  ↓
[Layer 3] _decide_*() hardcoded fallback
```

### 36 항목 catalog (코붕이 정밀화)

| Layer | 권고 | 항목 수 | 근거 |
|---|---|---|---|
| **A1** 법 정합 | FOR | 8 | 강행규정 의무 충족 |
| **A2** 법 위반 | AGAINST | 5 | 강행규정 위반 |
| **B1** 강한 의심 | REVIEW | 10 | 수치/판례 명확하지만 사람 판단 |
| **B2** 약한 의심 | REVIEW | 9 | 정당 사유 가능 |
| **C** Ownership 신호 | risk_factors | 4 | 정관 안건 X |

**핵심 원칙 (코붕이 정의)**:
- AGAINST는 **명백한 법 위반만**
- REVIEW는 법 테두리 안 모든 의심 케이스 (B1 + B2)
- 의무 정확 충족 (FOR) ≠ 의무 미달 (AGAINST) ≠ 의무 초과 + 우회 의심 (REVIEW)

### 1·2·3차 상법 개정 통합

**1차 (2025-07-22 공포)**:
- 즉시: 이사 충실의무 (회사+주주 양방향, §382의3)
- 2026-07-23: 독립이사 (사외이사 명칭 변경) + 의무선임 1/3 + 합산 3% 룰
- 2027-01-01: 전자주주총회 의무

**2차 (2025-09-09 공포)**:
- 2026-07-23: 합산 3% 룰 모든 감사위원 확대
- **2026-09-10**: 자산 2조+ 집중투표 의무화 + 감사위원 분리선출 2명 이상

**3차 (2026-02-25 본회의 통과)**:
- 2026-09-10: 자사주 의무소각 + 합병/분할 신주 배정 금지

### 정관 우회 시나리오 4가지

1. **집중투표/분리선출 무력화** — 시차임기제, 정수 축소, 정원 분리, 감사위 정원 확대, 분리선출 증원
2. **보수/임기 우회** — 등기→미등기, 보수 정관 명시, 임기 단축
3. **합산 3% 룰 회피** — 1·2대 지분 역전, PRS/TRS, 자사주 출연
4. **자사주 의무소각 회피** — 정관 예외 사유 폭넓게, 재단 출연 (시나리오 3과 일석이조)

→ FNguide 보고서 (rptId 1080969) + 법무법인 자료로 검증된 catalog.

### Implementation

**1. machine-readable JSON** (`wiki/rules/laws/law_layer_rules.json`):
- 36 룰 schema: id / layer / category / agenda_pattern / applies_to / decision / reason_template / law_reference / priority
- agenda_pattern: any_of / all_of / secondary / secondary_then / exclude (키워드 매칭)
- applies_to: min_asset_won / max_asset_won / applies_after / applies_before / company_type

**2. proxy_advise._law_layer()**:
- JSON 로드 (모듈 캐시)
- agenda_pattern + applies_to 검사
- Layer C (ownership 신호) 제외하고 sequential evaluation
- 첫 hit 반환 (priority 오름차순)

**3. caller wire**:
- `_run` dispatch loop 시작에 우선 호출
- corp_total_asset_won = financial_metrics summary.total_assets_krw
- hit 시 reason에 `[법령 X-Y]` tag + vote_style fallback skip

### 검증

**LG화학 회귀** (5/5 핵심 안건 [법령 X] tag 정확):
- 집중투표 배제 삭제 → FOR (A1-1)
- 전자주총 도입 → FOR (A1-7)
- 독립이사 명칭 변경 → FOR (A1-5)
- 감사위원 분리선출 인원 확대 → REVIEW (B1-10) ← 코붕이 요청대로
- 감사위원 의결권 제한 강화 → FOR (A1-4)

**자산 2조+ 30 회사 spot**:
- 28/29 자산 2조+ 정확 식별
- 413 agendas / 39 법령 hits (9.4%)
- A1-5 (독립이사 명칭) 11건, A1-1 (집중투표 배제 삭제) 10건
- 새 패턴 발견 X (모두 36 catalog 안)

## Meta-lesson

### 1. 운용사 정책 stale 문제 — 법령 layer가 보강

운용사가 정책 PDF update 안 하면 → 코드 fallback도 stale → forward-looking 불가.
→ **법령 자체를 코드 룰로 정합** (운용사 정책 위 layer).

### 2. 의무 정확 충족 vs 초과 분기 (코붕이 통찰)

처음에 "분리선출 2명+ 의무 초과 = FOR"로 생각했지만:
- FNguide 보고서: **분리선출 증원 = N 감소 → 진입 허들 상승 = 소수주주 차단**
- 코붕이 정밀화: 의무 정확 충족 = FOR / 의무 미달 = AGAINST / **의무 초과 + 우회 의심 = REVIEW**

### 3. AGAINST vs REVIEW 보수성

처음 catalog는 B1을 AGAINST로 분류했지만 코붕이 review:
- AGAINST는 명백한 법 위반만 (정당 사유 거의 없음)
- B1·B2 모두 REVIEW로 — 사람 판단 필요한 경우
- 자동 권고 위험 ↓ + 정확성 ↑

### 4. applies_after 시행일 vs 공포일

처음 A1 룰 applies_after를 시행일로 설정 → 사용자 호출 시 시행 전이라 매치 X.
**정관 사전 정비는 항상 정합** → applies_after를 공포일로 완화. A2는 시행일 그대로 (위법은 시행 후만).

## 영향

- `services/proxy_advise.py`:
  - `_law_layer()` 함수 신규 + helper (`_load_law_layer_rules` / `_agenda_pattern_match` / `_applies_to_match`)
  - `_run` dispatch loop에 우선 호출 wire
  - `_decide_articles_amendment` stale 분기 정리 (법령 layer로 이동)
- `wiki/rules/laws/`:
  - `상법-2025-2026-종합.md` 신규 (1·2·3차 통합)
  - `상법-2025-2026-종합.md` 신규 (4 시나리오 + 36 catalog)
  - `law_layer_rules.json` 신규 (머신리더블)
- `data/asset_managers/policies/open_proxy_v1.json`: 운용사 7→8 + N연기금 표기 통일
- `wiki/decisions/open-proxy-guideline.md`: OPM 4 → **5 기준** (5번째 = 법령 layer)
- `scripts/spot_law_layer.py` 신규 (회귀 spot)

## 비목표

- 다른 분류기 (`_classify_value_up_item` 등) — Ralph 2 영역
- 운용사 정책 자동 update — 별도 영역 (운용사 책임)
- 결정 logic (`_decide_*`) 전체 재작성 — 점진적 정리

## OPM 5 기준 (헌법 — 260508 update)

1. 소수주주 보호 우선 — 합의 없을 때 소수주주 유리
2. 거버넌스 투명성 — 정보 부족 시 review (강행규정 위반은 against)
3. 장기 가치 관점 — 단기 주가 부양보다 구조 안정
4. 추적 가능성 — 모든 권고에 references
5. **법령 layer 우선 + 의무·우회 분기** (260508 추가) — 법 정합 = FOR / 법 위반 = AGAINST / 법 테두리 안 우회 의심 = REVIEW
