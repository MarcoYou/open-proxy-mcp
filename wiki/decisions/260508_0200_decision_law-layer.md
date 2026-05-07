---
type: decision
title: 법령 layer 도입 — vote_style 위에 강행규정 우선 적용
date: 2026-05-08 02:00
status: adopted
related:
  - wiki/ralph/260508_0130_ralph_law-layer.md
  - wiki/lessons/law-layer-260508.md
  - wiki/rules/laws/상법-2025-2026-종합.md
  - wiki/rules/laws/law_layer_rules.json
  - wiki/decisions/open-proxy-guideline.md
---

# 법령 layer 도입 결정

## 배경

코붕이 review (2026-05-07~08): LG화학 proxy_advise 호출 시 정관 sub 안건 잘못 분류 — 운용사 정책 PDF update 안 되어 stale.

forward-looking proxy advisory 위해 법령 자체를 layer로 정합 필요. 운용사 정책 위에 강행규정 우선 적용.

## 결정

`proxy_advise`에 법령 layer (Layer 1) 도입:

```
[Layer 1 신규] 법령 (강행규정 + 우회 시나리오 — wiki/rules/laws/law_layer_rules.json)
                  ↓ hit 시 거기서 결정 + 아래 skip
[Layer 2] vote_style 운용사 정책 (asset_managers/policies/)
                  ↓
[Layer 3] _decide_*() hardcoded fallback
```

### 36 항목 catalog

| Layer | 권고 | 항목 수 |
|---|---|---|
| A1 법 정합 | FOR | 8 |
| A2 법 위반 | AGAINST | 5 |
| B1 강한 의심 | REVIEW | 10 |
| B2 약한 의심 | REVIEW | 9 |
| C Ownership 신호 | risk_factors | 4 |
| **합계** | — | **36** |

### 핵심 원칙

- **AGAINST**는 명백한 법 위반만 (정당 사유 거의 없음)
- **REVIEW**는 법 테두리 안 모든 의심 케이스 (B1 + B2)
- **의무 정확 충족** (FOR) ≠ **의무 미달** (AGAINST) ≠ **의무 초과 + 우회 의심** (REVIEW)
- A1 룰 `applies_after`는 **공포일** (사전 정관 정비 정합), A2는 **시행일** (시행 전 위법 X)

## 근거

### 1. forward-looking proxy advisory

운용사 정책은 PDF 기반이라 update 늦음. 5+ 운용사가 2026 상법 개정 7개 조항을 정책에 반영하지 않은 상태. hardcoded fallback도 stale. → 법령 layer만이 forward-looking 가능.

### 2. 1·2·3차 상법 개정 통합 (web 검증)

| 차수 | 공포 | 시행 | 핵심 |
|---|---|---|---|
| 1차 | 2025-07-22 | 즉시 / 2026-07-23 / 2027-01-01 | 이사 충실의무, 독립이사, 합산 3% 룰, 전자주총 |
| 2차 | 2025-09-09 | 2026-07-23 / **2026-09-10** | 자산 2조+ 집중투표 의무화 + 분리선출 2명 |
| 3차 | 2026-02-25 통과 | 2026-09-10 (예정) | 자사주 의무소각 + 합병/분할 신주 배정 금지 |

법무법인 자료 (김·장 / 신·김 / 지평 / 태평양 / 율촌 / Deloitte / 삼일회계법인) + FNguide 보고서로 cross-check.

### 3. 정관 우회 시나리오 (FNguide 보고서 + 주총방어 4가지)

수치적 차단 효과 명확:
- 분리선출 증원 (2명 초과) → N 감소 → 진입 허들 1/(N+1) 상승
- 이사 정수 축소 → 직접 진입 허들 상승
- 감사위원회 정원 확대 (3→5) → 분리선출 2명 + 나머지 3명 과반 → 무력화

→ 코붕이 정밀화: 모두 REVIEW (수치 효과 있지만 정당 사유 가능)

## 영향 범위

- `open_proxy_mcp/services/proxy_advise.py` — `_law_layer()` 추가, `_decide_articles_amendment` 정리, `_run` wire
- `wiki/rules/laws/law_layer_rules.json` — 36 머신리더블 룰
- `wiki/rules/laws/상법-2025-2026-종합.md` — 1·2·3차 통합본 + 4 시나리오 + 36 catalog (master)
- `data/asset_managers/policies/open_proxy_v1.json` — 운용사 7→8 표기 통일
- `wiki/decisions/open-proxy-guideline.md` — OPM 4 → 5 기준 (5번째 = 법령 layer)
- `scripts/spot_law_layer.py` — 회귀 spot

## 검증

### G1 36 catalog 정확 분류
- 단위 테스트: A1-1, A2-1, A1-3, A1-4, A1-5, B1-9, B1-10 모두 정확 ✓
- 자산/시행일 필터: 자산 2조 미만 + 시행 전 → 미매치 ✓

### G2 회귀
- LG화학 5/5 핵심 안건 [법령 X-Y] tag 정확 분류
- 자산 2조+ 30 회사 spot: 39 hits / A1-5(11) + A1-1(10) + A1-7(7) + A1-4(5) + A1-2(3) + B1-10(3)
- 새 패턴 발견 X

### G3 운용사 표기 일관성
- `open_proxy_v1.json`: "한국 7 운용사 합의" → "한국 8 운용사 + NPS 합의"
- `open-proxy-guideline.md`: 본문 7→8 + "OPM 4 기준" → "OPM 5 기준"
- `wiki/index.md` / `wiki/tools/proxy_advise_before_meeting.md` 등

## Trade-off

- (+) forward-looking 가능 — 운용사 정책 stale해도 강행규정 자동 반영
- (+) AGAINST는 명백한 법 위반만 → 잘못된 자동 권고 위험 ↓
- (+) REVIEW로 사람 판단 영역 명시 → 정확성 ↑
- (+) 36 항목 catalog 머신리더블 → 시행 일정/패턴 변경 시 JSON만 update
- (-) JSON 룰 작성 + 유지 비용 (법무법인 자료 모니터링)
- (-) keyword 매칭 한계 — 모호 표현은 fallback (LLM/사람 판단)

## 비목표

- 다른 분류기 (`_classify_value_up_item` / `_classify_filing` 등) — 별도 ralph
- 운용사 정책 자동 update — 운용사 책임 영역
- 후보 평가 logic (`_decide_director_election` 등) — 별도 ralph
- 모든 hardcoded `_decide_*` 즉시 정리 — 점진적 (법령 layer 정합 부분만 정리)

## archive 폴더

`wiki/architecture/audits/data/260508_law_layer/` (회귀 spot 결과)
