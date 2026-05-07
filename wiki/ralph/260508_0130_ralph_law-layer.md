---
type: ralph
title: 법령 layer 도입 — 1·2·3차 상법 개정 + 정관 우회 시나리오 catalog 정합
created: 2026-05-08 01:30
completion_promise: LAW_LAYER_VERIFIED
max_iterations: 10
ref:
  - wiki/rules/laws/상법개정-타임라인-2026.md
  - wiki/rules/laws/주총방어-시나리오-4가지.md
  - wiki/rules/laws/주총체크리스트-2026.md
  - wiki/decisions/open-proxy-guideline.md
---

## Invoke (복붙)

```
/ralph-loop:ralph-loop wiki/ralph/260508_0130_ralph_law-layer.md 가이드 따라 1차 2차 3차 상법 개정 + 정관 우회 시나리오 catalog를 머신리더블 JSON으로 정리한 뒤 proxy_advise에 법령 layer 우선 적용. 자산 2조 이상 상장사 30 spot 회귀로 검증. 36 catalog 항목 모두 정확 분류, LG화학 등 회귀 0, 운용사 8 표기 통일 모두 충족 시 promise. 보면서 새로운 패턴 발견하면 노트하고 보고. --completion-promise LAW_LAYER_VERIFIED --max-iterations 10
```

# Ralph: 법령 layer 도입

## Context

### 발견 흐름 (260507~08)
- LG화학 proxy_advise 호출 시 정관 sub 안건 잘못 분류 (NO_DATA → fix → AGAINST 잘못)
- root cause 분석:
  1. _classify_agenda parent 인지 안 함 → fix 완료 (Ralph 1)
  2. _decide_articles_amendment hardcoded 키워드가 stale → 임시 fix
  3. **vote_style 운용사 정책이 운용사 update 안 하면 stale → 법 정합 케이스 잘못 분류** ← 본 ralph
- 코붕이 review: "wiki에 학습시킨 상법 내용이 코드에 반영 안 됨 → forward-looking proxy advisory 위해 법령 layer 도입 필요"

### 1·2·3차 상법 개정 통합 (web 검증)

**1차 (2025-07-22 공포)**:
- 즉시: 이사 충실의무 회사+주주 양방향 (§382의3)
- 2026-07-23: 독립이사 (사외이사 명칭 변경) + 의무선임 1/3 + 합산 3% 룰
- 2027-01-01: 전자주주총회 의무

**2차 (2025-09-09 공포)**:
- 2026-07-23: 합산 3% 룰 모든 감사위원 확대 (사외이사 무관)
- **2026-09-10**: 자산 2조+ 집중투표 의무화 + 감사위원 분리선출 2명 이상

**3차 (2026-02-25 본회의 통과)**:
- 2026-09-10: 자사주 의무소각 (취득 후 1년 내 원칙) + 합병/분할 시 신주 배정 금지

### 정관 우회 시나리오 (주총방어 4가지 + 보고서 검증)

**시나리오 1**: 집중투표/분리선출 무력화 — 이사 정수 축소, 시차임기제, 정원 분리, 분리선출 증원
**시나리오 2**: 보수/임기 우회 — 등기→미등기, 보수 정관 명시, 임기 단축
**시나리오 3**: 합산 3% 회피 — 1·2대 지분 역전, PRS/TRS, 자사주 출연
**시나리오 4**: 자사주 소각 회피 — 정관 예외 사유 폭넓게, 재단·기금 출연

## 가정

- No conversation context / no web search / MCP only / deterministic
- 분당 DART 1000회 hard rule
- v2 production
- 10 iter max / 의미 있는 변경마다 commit
- 회귀 sample: 자산 2조+ 상장사 30 회사 (KOSPI 200 universe 활용)

## 성공 기준 (모두 충족 시 promise)

### G1. 36 catalog 항목 정확 분류
| Layer | 항목 | 권고 | 측정 |
|---|---|---|---|
| A1 | 8 | FOR | 100% |
| A2 | 5 | AGAINST | 100% |
| B1 | 10 | REVIEW | 100% |
| B2 | 9 | REVIEW | 100% |
| C | 4 | risk_factors | 100% |

### G2. 회귀 spot 자산 2조+ 30 회사
- LG화학 + 다른 자산 2조+ 회사 30 spot
- proxy_advise 호출 → agenda_decisions 모두 정합
- 이전 잘못된 AGAINST (LG화학 제2-1호/제2-5호) 회복 확인

### G3. 운용사 표기 일관성
- 코드 + wiki 모든 곳 "7 운용사" → "8 운용사 + NPS"
- `open_proxy_v1.json` policy_meta update

## 작업 plan (10 iter)

### Phase 1 — wiki 보강 (iter 1-2, DART X)

#### iter 1. 상법 개정 wiki 통합본
- `wiki/rules/laws/상법개정-2025-2026-통합본.md` 신규
  - 1·2·3차 통합 타임라인 (web 검증 반영)
  - 시행일별 표 + 적용 대상별 표
  - 법무법인 sources 인용
- 기존 `상법개정-타임라인-2026.md` 업데이트 (혹은 archive + redirect)

#### iter 2. 정관 우회 시나리오 wiki 보강
- `wiki/rules/laws/정관-우회-시나리오-2026.md` 신규 (또는 주총방어-시나리오 보강)
  - 시나리오 1·2·3·4 detail
  - 보고서 (FNguide rptId 1080969) 인용 + KT&G 사례
  - 36 항목 layer 분류 (A1/A2/B1/B2/C)

### Phase 2 — machine-readable JSON 룰 (iter 3, DART X)

#### iter 3. `wiki/rules/laws/law_layer_rules.json`
- 36 항목 모두 JSON 룰화
- 스키마:
  ```json
  {
    "layer": "A1|A2|B1|B2|C",
    "category": "articles_amendment|director_election|...",
    "pattern_keywords": ["..."],
    "pattern_negation": ["..."],
    "applies_to": {"min_asset_won": 2_000_000_000_000, "applies_after": "2026-09-10"},
    "decision": "FOR|AGAINST|REVIEW",
    "reason_template": "...",
    "law_reference": "상법 §...",
    "source_doc": "wiki/rules/laws/..."
  }
  ```

### Phase 3 — proxy_advise law_layer wire (iter 4-5)

#### iter 4. `_law_layer()` 함수 신규
- `services/proxy_advise.py`에 추가
- 입력: agenda_title + parent_title + corp_total_asset_won (재무 metric에서)
- 출력: (decision, reason, law_reference) 또는 None (law layer 미적용 시)
- 36 룰 sequential evaluation (A1 → A2 → B1 → B2)

#### iter 5. caller wire
- `_run` 함수에서 `_law_layer()` 우선 호출
- law layer hit 시 운용사 정책 skip
- law layer miss 시 기존 vote_style 정책 fallback

### Phase 4 — hardcoded `_decide_*` 정리 (iter 6, DART X)

#### iter 6. stale logic 제거
- `_decide_articles_amendment`에서 법령 정합 분기 제거 (law_layer로 이동)
- 임시 fix (260507) 정리
- 키워드 매칭 단순화

### Phase 5 — 회귀 spot (iter 7-8)

#### iter 7. 자산 2조+ 30 회사 spot
- KOSPI 200 universe에서 자산 2조+ 30 회사 추출 (LG화학/삼성전자/SK하이닉스/현대차 등)
- proxy_advise 호출 → agenda_decisions 검증
- 36 catalog 항목 hit 분포 확인

#### iter 8. LG화학 회귀 + edge case
- LG화학 제2-1호/제2-5호 → FOR 정확 확인
- 한진칼 / 고려아연 (분쟁 회사) → 시나리오 1·3 detect
- 회귀 0 확인

### Phase 6 — 부수 cleanup (iter 9, DART X)

#### iter 9. 운용사 8 표기 통일
- `open_proxy_v1.json` policy_meta: "7 운용사" → "8 운용사 + NPS"
- `open-proxy-guideline.md` 본문 update
- `wiki/index.md` / `wiki/log.md` / `wiki/tools/proxy_advise_before_meeting.md` update
- archive는 그대로 (역사 보존)

### Phase 7 — 문서화 + promise (iter 10)

- `wiki/lessons/law-layer-260508.md` 신규
- `wiki/decisions/260508_xxxx_decision_law-layer.md` 신규
- `wiki/log.md` 항목 추가
- `open-proxy-guideline.md`에 5번째 원칙 추가:
  > "5. 의무 정확 충족 = FOR / 의무 미달 = AGAINST / 의무 초과 + 우회 의심 = REVIEW"
- promise 발행

---

## 36 항목 catalog (재명시 — JSON 룰화 대상)

### A1. 법 정합 = FOR (8)

| # | 안건 | 대상 | 시행일 |
|---|---|---|---|
| A1-1 | 집중투표 배제 조항 삭제 | 자산 2조+ | 2026-09-10 |
| A1-2 | 집중투표 도입 | 모든 상장사 | 즉시 |
| A1-3 | 감사위원 분리선출 정확 2명 | 자산 2조+ | 2026-09-10 |
| A1-4 | 감사위원 합산 3% 룰 적용 | 자산 2조+ 또는 1천억-2조 감사위 | 2026-07-23 |
| A1-5 | 사외이사 → 독립이사 명칭 변경 | 모든 상장사 | 2025-07-22 |
| A1-6 | 독립이사 비율 정확 1/3 | 모든 상장사 | 2026-07-23 |
| A1-7 | 전자주주총회 도입 | 일정 규모+ | 2027-01-01 |
| A1-8 | 자사주 의무소각 결의 | 모든 상장사 | 2026-09-10 |

### A2. 법 위반 = AGAINST (5)

| # | 안건 | 대상 | 시행일 후 |
|---|---|---|---|
| A2-1 | 집중투표 배제 조항 신설/유지 | 자산 2조+ | 2026-09-10 후 |
| A2-2 | 감사위원 분리선출 1명 이하 | 자산 2조+ | 2026-09-10 후 |
| A2-3 | 감사위원 합산 3% 룰 미적용 | 자산 2조+ 또는 1천억-2조 감사위 | 2026-07-23 후 |
| A2-4 | 독립이사 비율 1/3 미달 | 모든 상장사 | 2026-07-23 후 |
| A2-5 | 자사주 합병·분할 시 신주 배정 | 모든 상장사 | 2026-09-10 후 |

### B1. 강한 의심 = REVIEW (10)

| # | 안건 | 대상 | 차단 메커니즘 |
|---|---|---|---|
| B1-1 | 시차임기제 (Staggered Terms) | 모든 상장사 | 매년 교체 인원 구조적 최소화 |
| B1-2 | 등기 → 미등기 임원 전환 + 보수 인상 | 고액 보수 임원사 | 주총 보수 승인 회피 |
| B1-3 | 보수 규정 정관 직접 명시 | 모든 상장사 | 매년 주총 결의 불필요화 |
| B1-4 | 임기 단축 정관변경 (예: 3년 → 1년) | 주주제안 후보 직위 | 활동 기간 제한 |
| B1-5 | 자사주 재단·기금·비특수관계인 출연 | 자사주 보유 + 분리선출 압박사 | 일석이조 |
| B1-6 | 상호주 형성 (대기업집단) | 상호출자제한 대기업 | 공정거래법 위반 가능성 |
| B1-7 | 이사 정수 축소 정관변경 | 행동주의 압박사 | 1/(N+1) 진입 허들 상승 |
| B1-8 | 이사 종류별 정원 분리 (KT&G 案) | 자산 2조+ | 집중투표 효과 종류 내 한정 |
| B1-9 | 감사위원회 정원 확대 (3→5) | 자산 2조+ | 분리선출 무력화 |
| B1-10 | 분리선출 감사위원 증원 (2명 초과) | 자산 2조+ | N 감소 → 진입 허들 상승 |

### B2. 약한 의심 = REVIEW (9)

| # | 안건 | 대상 | 모호성 |
|---|---|---|---|
| B2-1 | 임기 "3년" → "3년 이내" 유연화 | 모든 상장사 | 임기 유연성 |
| B2-2 | 자사주 정관 예외 사유 신설 | 자사주 보유사 | 법 인정 예외 정당, 폭넓으면 우회 |
| B2-3 | 임직원 보상 프로그램 연계 보수 안건 | 보수한도 인상 동시사 | ESOP vs 우회 포장 |
| B2-4 | 임기 미만료 이사 사임·재선임 | 분쟁/경영진 교체사 | 정당 사유 vs 시차임기제 |
| B2-5 | 자사주 우리사주(ESOP) 출연 | 자사주 다량 보유사 | 정상 ESOP vs 과도 |
| B2-6 | 상호주 형성 (소형 상장사) | 대기업 X | 가능 범위 |
| B2-7 | 독립이사 비율 1/2 이상 | 자발/행동주의 압박사 | 자발 강화 vs 특수 사정 |
| B2-8 | 분리선출 감사위원 증원 | 자산 2조 미만 | 의무 X, 자발/행동주의 |
| B2-9 | 자사주 합병/분할 외 신주 배정 | 자사주 보유사 | 정상 보상 vs 우회 |

### C. Ownership audit 신호 = risk_factors (4)

| # | 신호 | 대상 | 시나리오 |
|---|---|---|---|
| C-1 | 총회 직전 1·2대 주주 지분 역전 | 자산 2조+ 합산 3% 임박 | 시나리오 3 |
| C-2 | PRS/TRS 비공시 | 분쟁/5% 변동사 | 시나리오 3 |
| C-3 | 5% 보유목적 변경 (단순투자→경영참여) | 5%+ 보유사 | 분쟁 전조 |
| C-4 | 자사주 출연 공시 | 자사주 보유 + 분리선출 임박 | 시나리오 3·4 |

---

## 영향 범위

- `open_proxy_mcp/services/proxy_advise.py` — `_law_layer()` 추가, `_decide_*` 정리
- `open_proxy_mcp/data/asset_managers/policies/open_proxy_v1.json` — policy_meta update
- `wiki/rules/laws/` — 통합본 + 우회 시나리오 보강 + JSON 룰
- `wiki/decisions/open-proxy-guideline.md` — 5번째 원칙 추가
- `wiki/index.md`, `wiki/log.md`, `wiki/tools/proxy_advise_before_meeting.md` — 운용사 8 표기

## 비목표

- 법령 layer 외 다른 분류기 (`_classify_value_up_item` 등) 변경 — 별도 ralph
- `_decide_director_election` 등 후보 평가 logic — 별도 ralph
- 다른 운용사 정책 update (운용사가 update 해야 함)

## 가설 / 위험

- **위험 1**: 자산 2조+ 회사 자산 정보 fetch 비용 — financial_metrics에서 가져올 수 있어야 함
- **위험 2**: B1/B2 분류 사람 판단 영역 — REVIEW 권고 + reason_template 명확
- **위험 3**: hardcoded `_decide_*` 정리 시 회귀 위험 — 회귀 spot 필수
- **위험 4**: JSON 룰 keyword pattern 충돌 (예: "정관"이 articles_amendment 외 다른 안건에도 매칭) — sequential evaluation + most-specific-first

## archive 폴더

`wiki/architecture/audits/data/260508_law_layer/`

---

## iteration log

### iter 1 — 상법 개정 wiki 통합본
(작성 예정)

### iter 2 — 정관 우회 시나리오 wiki 보강
(작성 예정)

### iter 3 — law_layer_rules.json 작성
(작성 예정)

### iter 4 — _law_layer() 함수 신규
(작성 예정)

### iter 5 — proxy_advise caller wire
(작성 예정)

### iter 6 — hardcoded _decide_* 정리
(작성 예정)

### iter 7 — 자산 2조+ 30 회사 spot
(작성 예정)

### iter 8 — LG화학 + edge case 회귀
(작성 예정)

### iter 9 — 운용사 8 표기 통일
(작성 예정)

### iter 10 — 문서화 + promise
(작성 예정)
