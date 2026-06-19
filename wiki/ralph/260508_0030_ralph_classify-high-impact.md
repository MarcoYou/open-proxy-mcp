---
type: ralph
title: high-impact classifier 정확도 audit + fix (value_up / proxy_contest filer)
created: 2026-05-08 00:30
completion_promise: CLASSIFY_HIGH_IMPACT_VERIFIED
max_iterations: 7
ref:
  - wiki/ralph/260507_2330_ralph_classify-agenda-fix.md
  - wiki/lessons/agenda-classification-260507.md
  - wiki/decisions/260508_0030_decision_classify-agenda-parent-shortcircuit.md
---

## Invoke (복붙)

```
/ralph-loop:ralph-loop wiki/ralph/260508_0030_ralph_classify-high-impact.md 가이드 따라 high-impact 분류기 정확도 측정 후 패턴별로 fix. value_up과 proxy_contest filer 분류 검증. KOSPI 100 KOSDAQ 50 sample. 분류 오류 1퍼센트 미만, 회귀 0 모두 충족 시 promise. 보면서 유용한 데이터나 자주 등장하는 새로운 패턴 있으면 노트하고 보고. --completion-promise CLASSIFY_HIGH_IMPACT_VERIFIED --max-iterations 7
```

# Ralph 2: high-impact classifier 정확도 audit + fix

## Context

Ralph 1 (260507_2330_ralph_classify-agenda-fix) 완료 — `_classify_agenda` 19.3% mismatch → 0%. 패턴 발견 시 단일 root cause 추출이 우월하다는 lesson.

다른 분류기에도 같은 위험. catalog (proxy_advise iter 1 제외) 중 high-impact 2개:

1. **`_classify_value_up_item`** (services/value_up_v2.py:96)
   - 밸류업 공시 카테고리 분류 — `plan`/`progress`/`meta_amendment`
   - 잘못 분류 시 latest_plan 잘못 선택 → 사용자에게 잘못된 commitment 노출
   - report_nm 기반 키워드 매칭

2. **`_is_company_side`** / **`_is_retail_activism_side`** (services/proxy_contest.py)
   - 위임장 filer 3-way 분류: company / shareholder / retail_activism
   - has_contest_signal 결정에 직접 영향
   - filer_name 기반 키워드 매칭

## 가정

- No conversation context / no web search / MCP only / deterministic
- 분당 DART 1000회 hard rule
- v2 production
- 7 iter max / 의미 있는 변경마다 commit

## 성공 기준 (모두 충족 시 promise)

### G1. value_up 분류 정확도 ≥99%
- KOSPI 100 + KOSDAQ 50 (밸류업 공시 있는 회사) sample
- 각 회사 value_up scope=summary/timeline 호출 → kind_items / dart_items 카테고리 결과
- 검증: report_nm 패턴별 ground truth와 분류 결과 일치
- 잘못 분류 비율 < 1%

### G2. proxy_contest filer 3-way 정확도 ≥99%
- 동일 sample에서 proxy_contest scope=fight 호출 → filer 분류 결과
- 회사 측 / 주주 측 / 소액주주 플랫폼 명확 구분 검증
- 잘못 분류 비율 < 1%

### G3. 회귀 0
fix 후 기존 정상 분류 유지.

## 작업 plan (7 iter)

### Phase 0 — Universe 사전 (Ralph 1 자산 재사용)

universe csv 그대로:
- `wiki/architecture/audits/data/260506_universe_kospi_200.csv`
- `wiki/architecture/audits/data/260506_universe_kosdaq_100.csv`

### Phase 1 — value_up audit (iter 1-2)

#### iter 1. value_up audit script + KOSPI 50 batch
- `scripts/spot_classify_value_up.py` 신규
- 50 회사 → kind_items + dart_items 카테고리 분류 수집
- 결과 JSON: `wiki/architecture/audits/data/260508_classify_high_impact/iter01_kospi_0-50.json`

#### iter 2. KOSPI 50-100 + KOSDAQ 0-50 batch chain
- 100 회사 추가 — 누적 150 회사

### Phase 2 — value_up 패턴 분석 + fix (iter 3)

- 잘못 분류 패턴 catalog
- `_classify_value_up_item` 키워드 강화 또는 시그니처 변경
- 회귀 spot

### Phase 3 — proxy_contest filer audit + fix (iter 4-5)

#### iter 4. proxy_contest filer audit (150 회사)
- `scripts/spot_classify_filer.py` 신규
- 위임장 공시 있는 회사 filer 분류 결과 수집

#### iter 5. 패턴 분석 + fix
- false positive / false negative catalog
- `_is_company_side` / `_is_retail_activism_side` 강화
- 회귀

### Phase 4 — 통합 검증 + 문서화 (iter 6-7)

#### iter 6. 양쪽 fix 후 통합 spot
- 50 회사 spot 재 audit
- 두 분류기 모두 G1/G2 충족 확인

#### iter 7. lesson + decision 작성 + promise

---

## 총 DART 호출 추정

| Phase | iter | 호출 수 |
|---|---|---|
| Phase 1 (value_up audit, 150 회사) | 1-2 | ~450 (3/회사) |
| Phase 2 (정적 + 회귀 50 회사) | 3 | ~150 |
| Phase 3 (proxy_contest, 150 회사) | 4-5 | ~600 (4/회사) |
| Phase 4 (통합 spot 50 회사) | 6 | ~150 |
| Phase 5 (문서화) | 7 | 0 |
| **총합** | 7 iter | **~1,350 calls** |

→ 이전 Ralph 1과 비슷한 호출량. iter 분산 + connection pool로 cap 안전.

---

## 영향 범위

- `open_proxy_mcp/services/value_up_v2.py` — `_classify_value_up_item` fix
- `open_proxy_mcp/services/proxy_contest.py` — `_is_company_side` / `_is_retail_activism_side` 강화
- `scripts/spot_classify_value_up.py` — 신규
- `scripts/spot_classify_filer.py` — 신규
- `wiki/architecture/audits/data/260508_classify_high_impact/` — 검증 데이터
- `wiki/lessons/classify-high-impact-260508.md` — lesson
- `wiki/decisions/...` — 변경 결정

## 비목표

- `_classify_filing` (company service) audit — 낮은 impact, 별도 ralph
- `_classify_equity_deal` / `_classify_supply_contract` (related_party_transaction) — medium impact, 별도
- `_is_active_purpose` / `_is_material_block` (ownership_structure) — medium, 별도
- 결정 logic (`_decide_*`) 변경 — 분류만 fix
- agenda tree 구조 변경

## 가설 / 위험

- **위험 1 (ground truth 정의)**: report_nm/filer_name 키워드 매칭 정확도 사람 판단 영역. 보수적 rule (예: KIND 명시 카테고리 vs 우리 자체 분류)
- **위험 2 (밸류업 공시 적은 회사)**: 일부 회사 밸류업 공시 0건 → audit sample 부족 가능. lookback 늘려 cover
- **위험 3 (filer 3-way 모호)**: 회사 자회사가 filer면 company-side? 자회사가 별도 활동 시 shareholder-side? 명확 rule 필요
- **위험 4 (회귀)**: 키워드 추가/제거 시 다른 패턴 깨질 수 있음 — 회귀 spot 필수

## archive 폴더

`wiki/architecture/audits/data/260508_classify_high_impact/`

---

## iteration log

### iter 1 — value_up audit script + KOSPI 0-50 batch
(작성 예정)

### iter 2 — KOSPI 50-100 + KOSDAQ 0-50 batch chain
(작성 예정)

### iter 3 — value_up 패턴 분석 + fix + 회귀
(작성 예정)

### iter 4 — proxy_contest filer audit (150 회사)
(작성 예정)

### iter 5 — filer 패턴 분석 + fix + 회귀
(작성 예정)

### iter 6 — 통합 검증 spot
(작성 예정)

### iter 7 — 문서화 + promise
(작성 예정)
