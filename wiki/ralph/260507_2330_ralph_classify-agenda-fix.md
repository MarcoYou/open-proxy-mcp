---
type: ralph
title: _classify_agenda 분류 정확도 fix (NO_DATA 잘못 발생 제거)
created: 2026-05-07 23:30
completion_promise: AGENDA_CLASSIFICATION_VERIFIED
max_iterations: 7
ref:
  - wiki/decisions/260507_2330_decision_httpx-connection-pool.md
  - feedback_data_action_tool_layers
---

## Invoke (복붙)

```
/ralph-loop:ralph-loop wiki/ralph/260507_2330_ralph_classify-agenda-fix.md 가이드 따라 안건 분류 함수 정확도 측정 후 패턴별로 fix. KOSPI 200, KOSDAQ 100 sample. NO_DATA 잘못 발생 비율 1퍼센트 미만, 정관변경 sub 안건 100퍼센트, 회귀 0 모두 충족 시 promise. --completion-promise AGENDA_CLASSIFICATION_VERIFIED --max-iterations 7
```

# Ralph: _classify_agenda 분류 정확도 fix

## Context

코붕이 review (2026-05-07): 롯데케미칼 proxy_advise 호출 시 정관변경 sub-안건 ("사외이사 명칭 변경", "감사위원 분리선임 확대") 두 건이 NO_DATA로 떴음.

분석:
- `services/proxy_advise.py:_classify_agenda` 우선순위 — "정관" 키워드 first
- 그러나 sub-안건 title에 "정관" 키워드 없으면 다음 분기로
- "사외이사" 또는 "감사위원" + "선임" 매칭 시 director_election / audit_committee_election로 잘못 분류
- 후보 평가 데이터 없음 (정관 변경이라 후보 자체가 없음) → `_decide_director_election(None)` → **NO_DATA**

→ 정관 sub-안건은 부모 안건 (제2호 정관 변경)에 묶여있어 articles_amendment로 분류돼야 맞음.

## 가정

- No conversation context / no web search / MCP only / deterministic
- 분당 DART 1000회 hard rule (rolling cap 900)
- v2 production (`OPEN_PROXY_TOOLSET=v2`) 기준
- 7 iter max / 의미 있는 변경마다 commit
- proxy_advise 무거움 (30s+/회사) — `_classify_agenda` 단독 검증으로 효율화 (DART 호출 ~3/회사)

## 성공 기준 (모두 충족 시 promise)

### G1. 분류 정확도 ≥99%
KOSPI 200 + KOSDAQ 100 (300 회사)에서:
- 모든 안건 title 추출 (shareholder_meeting_notice summary scope)
- `_classify_agenda` 적용 → 분류 결과
- 검증: `expected category` (rule-based ground truth) vs 실제 결과
- 잘못 분류 비율 < 1%

### G2. 정관변경 sub-안건 100%
정관 변경의 sub-안건 (제2-1호, 제2-2호 …) 전수:
- 부모가 "정관" 키워드 있으면 sub-안건도 articles_amendment로 분류
- 100% 정확

### G3. 회귀 0
기존 정상 분류 (이사 선임, 보수한도, 재무제표 승인 등) 모두 유지.

## 작업 plan (7 iter)

### Phase 0 — Universe + ground truth ✓ (사전 준비)

universe 그대로 재사용:
- `wiki/architecture/audits/data/260506_universe_kospi_200.csv`
- `wiki/architecture/audits/data/260506_universe_kosdaq_100.csv`

### Phase 1 — 분류 audit (iter 1-2)

#### iter 1. Audit script + 첫 batch (KOSPI 0-30)
- `scripts/spot_classify_agenda.py` 신규 — shareholder_meeting_notice summary 호출 + 모든 안건 title flatten + `_classify_agenda` 적용 + parent 정보 함께 수집
- 30 회사 batch — ~90 DART calls / ~1분
- 결과: `wiki/architecture/audits/data/260507_classify_agenda/iter01_kospi_0-30.json`

#### iter 2. KOSPI + KOSDAQ 잔여 batch
- KOSPI 30-200 + KOSDAQ 0-100 — 총 270 회사 / ~810 DART calls
- 6-9 batch × 30 회사

### Phase 2 — 패턴 분석 + fix (iter 3-4)

#### iter 3. 통합 분석 (DART X)
- 모든 안건 분류 결과 통합
- 각 카테고리별 sample 검토
- "잘못 분류" 패턴 catalog (예: 정관 sub-안건이 director_election로 간 케이스)

#### iter 4. _classify_agenda fix
가장 가능성 높은 fix 옵션:
- (A) parent agenda 참고 — sub-안건이면 부모 title도 검사
- (B) 키워드 강화 — "선임" 단독 부족, "이사 선임" / "사외이사 선임" 명확 패턴
- (C) "명칭 변경" / "분리선임 확대" 등 정관 sub-안건 키워드 명시 추가

### Phase 3 — 회귀 + 검증 (iter 5)

#### iter 5. 변경 후 재 audit (300 회사 핵심 일부)
- KOSPI 50 + KOSDAQ 30 spot — 패턴 fix가 회귀 안 만드는지
- 문제 case 100% 정상 분류 확인

### Phase 4 — 문서화 + promise (iter 6-7)

- lesson 문서 작성
- decision 작성 (분류 로직 변경 결정)
- 변경 commit + push + deploy

---

## 총 DART 호출 추정

| Phase | iter | DART calls 추정 |
|---|---|---|
| Phase 1 (300 회사 audit) | 1-2 | ~900 (300 × 3) |
| Phase 2 (정적 분석 + fix) | 3-4 | 0 |
| Phase 3 (회귀 80 회사) | 5 | ~240 |
| Phase 4 (문서화) | 6-7 | 0 |
| **총합** | 7 iter | **~1,140 DART calls** |

→ iter 분산 + connection pooling으로 cap 900/min 매우 안전.

---

## 영향 범위

- `open_proxy_mcp/services/proxy_advise.py` — `_classify_agenda` 변경
- `scripts/spot_classify_agenda.py` — 신규 audit script
- `scripts/agg_classify_agenda.py` — 신규 통합 분석
- `wiki/architecture/audits/data/260507_classify_agenda/` — 검증 데이터
- `wiki/lessons/agenda-classification-260507.md` — lesson
- `wiki/decisions/260507_xxxx_decision_classify-agenda-fix.md` — decision

## 비목표

- 다른 분류기 (`_classify_value_up_item`, `_is_company_side` 등) audit — 별도 ralph
- proxy_advise 권고 결정 logic (`_decide_*`) 변경 — 분류만 fix
- shareholder_meeting service 변경

## 가설 / 위험

- **위험 1 (분류 ground truth 정의)**: "이게 진짜 articles_amendment냐"가 사람 판단 영역. 보수적으로: parent에 "정관" 있으면 articles_amendment
- **위험 2 (parent 정보 전달)**: agenda hierarchy의 parent title이 `_classify_agenda` 호출 시점에 전달되는지 확인 필요. 안 되면 dispatcher 수정도 필요
- **위험 3 (regression)**: 키워드 우선순위 조정 시 다른 안건 분류 깨질 수 있음 — 회귀 spot 필수
- **위험 4 (fly suspend latency)**: 첫 호출 cold start 약간 있을 수 있음 — connection pool로 mitigate

## archive 폴더

`wiki/architecture/audits/data/260507_classify_agenda/`

---

## iteration log

### iter 1 — Audit script + KOSPI 0-30 batch
(작성 예정)

### iter 2 — KOSPI 30-200 + KOSDAQ 0-100 batch chain
(작성 예정)

### iter 3 — 통합 분석 (DART X)
(작성 예정)

### iter 4 — _classify_agenda fix
(작성 예정)

### iter 5 — 회귀 spot (80 회사)
(작성 예정)

### iter 6-7 — 문서화 + promise
(작성 예정)
