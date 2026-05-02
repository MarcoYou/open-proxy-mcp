---
type: ralph
title: proxy_advise_before_meeting 정확도 + 일관성 + 사실 정확성 검증
created: 2026-05-04 00:02
completion_promise: PROXY_ADVISE_VERIFIED
max_iterations: 30
---

## Invoke (복붙해서 실행)

```
/ralph-loop:ralph-loop wiki/ralph/260503_0002_ralph_proxy-advise-verification.md 가이드 따라 3 gate 검증. archive 우선. soft pattern 우선 hard pattern 다층 fallback. OCR 진단 only parser final. G1 일관성 99이상 G2 정확도 99이상 G3 사실 100 regression 0 모두 충족 시 promise. --completion-promise PROXY_ADVISE_VERIFIED --max-iterations 10
```

> 🔧 모든 gate 99% 이상 통일. iteration 수 등은 위 라인에서 수정.

# Ralph: proxy_advise_before_meeting 검증 — 3축 quality gate

새 통합 action tool `proxy_advise_before_meeting`은 운용사가 **한 회사 보고 의결권 결정**할 때 사용. 결과를 신뢰하려면 3가지 모두 충족해야 함:

1. **일관성** — 같은 회사 / 같은 안건 → 매번 같은 답 (deterministic)
2. **정확도** — 결정이 정책 + 사실 근거에 부합 (7 운용사 baseline 비교)
3. **사실 정확성** — evidence 안에 적힌 숫자/이름/날짜 raw 데이터와 일치 (할루시네이션 0)

기존 advise_vote Phase 4에서 (1) 일관성은 200×3 = 100% 달성. **본 ralph는 (2) + (3) 추가 검증**.

## 가정 (가상환경 명시 — Phase 3 동일)
- No current conversation context
- No web search
- MCP only (proxy_advise + 관련 data tool)
- as if it's the first question
- deterministic (temperature=0)

## 매 iteration 작업

1. **현황 확인**: 이전 검증 csv + 실패 archive
2. **다음 1 step만 진행** (아래 D1-D3 중 하나, 검증 가능 단위로 작게)
3. **fix 검증**: 5-20 회사 spot 재검 후 통과율 측정
4. **commit** (의미 있는 변경마다)
5. 다음 iteration 1줄

---

## 성공 기준 (3 gate 모두 충족 시 promise)

### Gate 1: 일관성 100% (이미 Phase 4 ✅)
- 200×3 batch 동일 결과 (FOR/AGAINST/REVIEW count 변동 0)
- 신규 scope 추가 (agenda/candidates/financial/governance/ownership/policy_basis/proxy_battle/engagement/evidence) 모두 적용 후 재검증
- multi-upstream-pattern 5 요소 적용 검증 (asyncio.Lock + retry + per-call timeout + Semaphore + cache)

### Gate 2: 정확도 ≥99% (vs 7 운용사 majority baseline)

데이터 source: `data/asset_managers/records/*.json` (22 파일 — 7 운용사 × 3 연도 + 1)
- a_activist (2024/2025/2026)
- b_foreign (2024/2025/2026)
- c_activist (2026)
- k_legacy (2024/2025/2026)
- ... (7 운용사 × 3 연도 + 일부)

검증 방법:
1. 각 운용사 행사내역에서 (회사, 안건 카테고리, 결정 FOR/AGAINST) 추출
2. 7 운용사 majority decision 계산 (≥4/7 동조 시 majority, <4 시 분열)
3. proxy_advise.decisions 호출 → 우리 결정 vs majority 비교
4. 정확도 = (우리 결정 == majority) / 전체 검증 case

target: **majority case 정확도 ≥99%**, 분열 case는 정책 일관성만 (open_proxy 정책 기준 따라간다는 logic 검증)

#### Gate 2 sanity check (소규모)
- 알려진 known-good 10 case (자본잠식 + 배당 → AGAINST 보장 등) 100% 통과
- 알려진 known-bad 10 case (할루시네이션 사례) 0건

### Gate 3: 사실 정확성 100% (evidence fact-check)

evidence scope에서 unpack된 결정 근거 안의 facts:
- 재무 숫자 (영업이익, ROE, 배당성향 등)
- 후보 정보 (이름, 직책, 추천인)
- 안건 내용 (의안번호, 제목)
- 정책 인용 (OPM Guideline section)

검증 방법:
1. proxy_advise.evidence 호출 → 모든 fact statements 추출
2. 각 fact을 raw upstream 데이터와 cross-check (financial_metrics raw + shareholder_meeting raw 등)
3. mismatch 1건이라도 있으면 Gate 3 fail

target: **사실 mismatch 0건 (n=200 회사 × 평균 5 facts = 1000 facts)**

검증 자동화 script: `/tmp/test_proxy_advise_facts.py`

---

## 검증 데이터 set

### 전체 universe
- KOSPI 200 — `wiki/architecture/audits/data/260503_universe_200.csv` (199 회사, Phase 4 baseline 동일)

### Gate 2 baseline (7 운용사)
- `data/asset_managers/records/{a_activist|b_foreign|c_activist|k_legacy|...}_2024-04.json` 등
- 22 파일 cross-extract

### Gate 3 fact source
- 같은 회사 raw upstream 호출:
  - `mcp__open-proxy-mcp-v2__financial_metrics` raw
  - `mcp__open-proxy-mcp-v2__shareholder_meeting` raw
  - `mcp__open-proxy-mcp-v2__corp_gov_report` raw
  - 등
- evidence 안 facts와 raw mapping

### Known case sets
- known_good 10: 명백한 결정 (자본잠식 + 배당 = AGAINST 등) — `wiki/architecture/audits/data/260504_known_good_cases.csv`
- known_bad 10: 과거 할루시네이션 사례 (있다면) — `wiki/architecture/audits/data/260504_known_bad_cases.csv`

(파일은 ralph iteration 중 작성)

---

## 종료 조건

### ✅ promise 출력 조건 (모두 충족 필수)

1. **산출물 .py + 신규 scope 9개 모두 commit** (analysis만 X):
   - `services/proxy_advise.py` (rename from advise_vote)
   - `tools_v2/proxy_advise_before_meeting.py` (rename)
   - 9 scope 모두 코드: agenda / candidates / financial / governance / ownership / policy_basis / proxy_battle / engagement / evidence
2. **Gate 1**: 200×3 batch 일관성 100% (신규 scope 모두 적용 후 재검증)
3. **Gate 2**: 7 운용사 majority baseline 정확도 ≥99%
4. **Gate 3**: evidence fact-check mismatch 0건 (n≥1000 facts)
5. Regression 0 — 기존 advise_vote 결과 (Phase 4 baseline) 모두 유지

→ **`<promise>PROXY_ADVISE_VERIFIED</promise>` 출력**

### Regression 검증 의무

- Phase 4 advise_vote 200×3 결과 (`260503_advise_200x3_phase4.csv`)와 cross-match
- 신규 proxy_advise.decisions와 옛 advise_vote.decisions 동일 결과 보장 (rename + scope 추가만, logic 변경 X)
- 1건이라도 깨지면 promise X

### 진단 vs 산출물 분리 (코붕이 명시 — Phase 3 동일)

- 진단 단계: PDF / OCR / LLM fact-check 사용 OK (offline study)
- 최종 산출물: parser only (production runtime OCR 호출 X)

### 실패 사례 incremental archive

매 iteration 실패 case 만날 때 **원문 + 분석 archive**:
- 위치: `wiki/architecture/audits/data/260504_proxy_advise_failure_archive/`
- 파일명: `{ticker}_{rcept_no}_{gate}_{fail_type}.md`
- 내용:
  1. 회사 / rcept_no / scope / 시도한 검증 gate
  2. 실패 유형 (consistency_drift / accuracy_vs_majority / fact_hallucination)
  3. 원문 raw text 발췌 (관련 섹션 500-2000자)
  4. proxy_advise 출력 vs raw fact diff
  5. 시도한 fallback / 어디서 stop
  6. 제안 fix

### Soft pattern 우선 / Hard pattern은 끝까지 (Phase 3 원칙 유지)

evidence fact-check에서 raw 데이터 형식 변형이 많을 것 (숫자 콤마/단위/한자):
- soft 매칭 (정규화 후 비교, 단위 환산, 동의어 처리)
- hard 매칭 안 되면 다층 fallback
- silent fallback X — 명시 status 반환

### ⚠ 막힘 발생 시
- Gate 2 정확도 95% (target 99% 미달) → 실패 case 분석 + 정책 wire fix 또는 vote_style param 조정 필요성 판단 + 사용자 결정 요청
- Gate 3 fact mismatch 발견 → evidence builder 코드 fix (parser raw 직접 인용 보장)

---

## 반복 단위 (작은 step)

좋은 1 iteration 단위 예시:
- "proxy_advise.py rename + 기존 6 scope 통합 후 5 회사 spot 일관성 재확인"
- "agenda scope 신규 코드 + 5 회사 출력 검증"
- "candidates scope wiring + director_evaluation 결과 통합"
- "financial scope + financial_metrics raw 노출"
- "policy_basis scope + 7 운용사 records 비교 logic"
- "evidence scope + fact-check helper"
- "Gate 2 baseline 추출 script 작성 + 20 회사 정확도 측정"
- "Gate 3 fact-check script + 50 facts 검증"

너무 큰 step (예: "9 scope + Gate 2 + Gate 3 한 번에") 금지. 하나씩 검증.

---

## 사전 정리 (Phase 4 finding + 신규 검증 origin)

### Pre-finding 1: 일관성은 이미 100%
Phase 4 advise_vote 200×3 결과 195/195 = 100% 일관 ([[260503_1847_audit_phase4_final]]). multi-upstream-pattern 5 요소 적용 효과.

→ rename + scope 추가 시 같은 패턴 유지 의무. regression 검증 자동화.

### Pre-finding 2: 정책 wire는 이미 검증됨
v1.2/v1.3 OPM Guideline + 8 운용사 정책 wire는 이전 작업 통과 ([[260429_0059_decision_voting-policy-consensus-matrix]]).

→ Gate 2는 우리 결정 vs 7 운용사 majority 비교 자체가 새 검증.

### Pre-finding 3: 할루시네이션 위험은 evidence builder 단계
- raw upstream payload는 정확 (DART API 직접)
- 우리가 결정 사유 작성 시 숫자 paraphrase / context 추가 시 사실 누락/왜곡 위험
- Gate 3는 evidence 안 모든 fact가 raw payload에 직접 인용으로 추적 가능해야 함

→ evidence scope 설계 원칙: **모든 fact statement에 source upstream payload + 라인 reference 필수**.

---

## 참고 — 관련 문서

- [[architecture/multi-upstream-pattern]] — 5 요소 표준 (corpCode lock/retry/per-call timeout/Semaphore/cache)
- [[260503_1847_audit_phase4_final]] — Phase 4 일관성 100% 달성
- [[260503_2304_audit_recap_pattern]] — recap_vote 패턴 일반화
- [[260429_0059_decision_voting-policy-consensus-matrix]] — 7 운용사 + NPS 정책 매트릭스

---

## 명명
- 이 ralph: `wiki/ralph/260503_0002_ralph_proxy-advise-verification.md`
- audit 페이지: `wiki/architecture/audits/260504_HHMM_audit_proxy-advise-{gate}.md`
- 실패 archive: `wiki/architecture/audits/data/260504_proxy_advise_failure_archive/`
- 검증 csv: `wiki/architecture/audits/data/260504_proxy_advise_{gate1|gate2|gate3}.csv`
- 검증 script: `/tmp/run_proxy_advise_gate{1|2|3}.py`
